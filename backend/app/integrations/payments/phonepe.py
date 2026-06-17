"""PhonePe Standard Checkout v1.

Auth model: every request carries an ``X-VERIFY`` header that proves the
request was built by someone who knows the merchant's salt key. For payment
initiation that signature covers the base64-encoded payload AND the endpoint
path; for status checks it only covers the endpoint path. Webhook callbacks
arrive with the same header shape — the body is ``{response: <base64 json>}``
and the signature is over the raw base64 string.

References:
  https://developer.phonepe.com/v1/reference/pay-api
  https://developer.phonepe.com/v1/reference/check-status-api
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from typing import Any

import httpx

from app.integrations.payments.base import (
    InitiateRequest,
    InitiateResponse,
    PaymentGatewayError,
    PaymentStatus,
    StatusResponse,
    provider_rejection,
)

logger = logging.getLogger(__name__)

# Per docs — the codes you see in real `code`/`state` fields from PhonePe.
_SUCCESS_CODES = {"PAYMENT_SUCCESS"}
_FAILED_CODES = {
    "PAYMENT_ERROR",
    "PAYMENT_DECLINED",
    "PAYMENT_CANCELLED",
    "TIMED_OUT",
    "TRANSACTION_NOT_FOUND",
}


class PhonePeError(PaymentGatewayError):
    code = "payment_provider_error"


class PhonePeProvider:
    name = "phonepe"

    def __init__(
        self,
        *,
        merchant_id: str,
        salt_key: str,
        salt_index: int,
        base_url: str,
        callback_url: str,
    ):
        if not merchant_id or not salt_key:
            raise PhonePeError(
                "PhonePe is selected but its Merchant ID / Salt key are not set. "
                "Fill them in Admin → Settings → Payments."
            )
        self.merchant_id = merchant_id
        self.salt_key = salt_key
        self.salt_index = salt_index
        self.base_url = base_url.rstrip("/")
        self.callback_url = callback_url

    # ---- public API ----

    def initiate(self, req: InitiateRequest) -> InitiateResponse:
        endpoint = "/pg/v1/pay"
        payload = {
            "merchantId": self.merchant_id,
            "merchantTransactionId": req.merchant_transaction_id,
            "merchantUserId": f"USER{req.user_id}",
            "amount": req.amount_minor,
            "redirectUrl": req.return_url,
            "redirectMode": "REDIRECT",
            "callbackUrl": self.callback_url,
            "paymentInstrument": {"type": "PAY_PAGE"},
        }
        if req.user_phone:
            payload["mobileNumber"] = req.user_phone

        body_b64 = base64.b64encode(json.dumps(payload).encode()).decode()
        signature = self._sign(body_b64 + endpoint)

        resp = self._post(
            endpoint,
            json_body={"request": body_b64},
            extra_headers={"X-VERIFY": signature},
        )

        data = resp.get("data") or {}
        instrument = data.get("instrumentResponse") or {}
        redirect = (instrument.get("redirectInfo") or {}).get("url")
        if not redirect:
            raise PhonePeError("PhonePe did not return a redirect URL.", details={"resp": resp})

        return InitiateResponse(
            redirect_url=redirect,
            provider_transaction_id=data.get("transactionId"),
            raw=resp,
        )

    def fetch_status(
        self, merchant_transaction_id: str, provider_ref: str | None = None
    ) -> StatusResponse:
        endpoint = f"/pg/v1/status/{self.merchant_id}/{merchant_transaction_id}"
        signature = self._sign(endpoint)
        resp = self._get(
            endpoint,
            extra_headers={
                "X-VERIFY": signature,
                "X-MERCHANT-ID": self.merchant_id,
            },
        )
        return self._to_status(merchant_transaction_id, resp)

    def verify_webhook(self, body: bytes, signature: str | None) -> bool:
        # PhonePe S2S callback body is {"response": "<base64>"}; X-VERIFY covers
        # the base64 string. We re-compute and compare in constant time.
        if not signature:
            return False
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return False
        encoded = parsed.get("response")
        if not isinstance(encoded, str):
            return False
        expected = self._sign(encoded)
        return hmac.compare_digest(expected, signature)

    def parse_webhook(self, body: bytes) -> StatusResponse:
        parsed = json.loads(body)
        encoded = parsed["response"]
        decoded = json.loads(base64.b64decode(encoded).decode())
        mtid = decoded.get("data", {}).get("merchantTransactionId") or decoded.get(
            "merchantTransactionId"
        )
        if not mtid:
            raise PhonePeError("Webhook payload missing merchantTransactionId.")
        return self._to_status(mtid, decoded)

    # ---- internals ----

    def _sign(self, message: str) -> str:
        digest = hashlib.sha256((message + self.salt_key).encode()).hexdigest()
        return f"{digest}###{self.salt_index}"

    def _post(self, endpoint: str, *, json_body: dict, extra_headers: dict) -> dict:
        url = self.base_url + endpoint
        headers = {"Content-Type": "application/json", "accept": "application/json", **extra_headers}
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.post(url, json=json_body, headers=headers)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPStatusError as e:  # surface PhonePe's error envelope
            logger.warning("phonepe POST %s -> %s", endpoint, e.response.text[:500])
            raise provider_rejection(PhonePeError, "PhonePe rejected the request", e.response)
        except httpx.HTTPError as e:
            logger.warning("phonepe POST %s network error: %s", endpoint, e)
            raise PhonePeError("Could not reach PhonePe.")

    def _get(self, endpoint: str, *, extra_headers: dict) -> dict:
        url = self.base_url + endpoint
        headers = {"Content-Type": "application/json", "accept": "application/json", **extra_headers}
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.get(url, headers=headers)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPStatusError as e:
            logger.warning("phonepe GET %s -> %s", endpoint, e.response.text[:500])
            raise provider_rejection(PhonePeError, "PhonePe status check failed", e.response)
        except httpx.HTTPError as e:
            logger.warning("phonepe GET %s network error: %s", endpoint, e)
            raise PhonePeError("Could not reach PhonePe.")

    @staticmethod
    def _to_status(mtid: str, resp: dict[str, Any]) -> StatusResponse:
        data = resp.get("data") or {}
        # `code` is set on the top-level envelope; `state` on the data block.
        code = (resp.get("code") or data.get("state") or "").upper()
        if code in _SUCCESS_CODES or data.get("state", "").upper() == "COMPLETED":
            status_ = PaymentStatus.SUCCESS
        elif code in _FAILED_CODES:
            status_ = PaymentStatus.FAILED
        else:
            status_ = PaymentStatus.PENDING
        return StatusResponse(
            merchant_transaction_id=mtid,
            status=status_,
            provider_transaction_id=data.get("transactionId"),
            amount_minor=data.get("amount"),
            raw=resp,
        )
