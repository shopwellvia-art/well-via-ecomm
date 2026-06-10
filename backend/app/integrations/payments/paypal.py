"""PayPal Orders v2 integration.

Uses the Orders API with a redirect-to-PayPal hosted checkout flow.
Webhook verification requires a stored webhook_id + PayPal's verify-webhook-signature
API, which adds extra complexity and a network round-trip. Instead, we rely on
status polling on the return URL (consistent with other providers), so
verify_webhook returns False and parse_webhook is a minimal stub.

References:
  https://developer.paypal.com/docs/api/orders/v2/
"""
from __future__ import annotations

import json
import logging

import httpx

from app.core.exceptions import AppError
from app.integrations.payments.base import (
    InitiateRequest,
    InitiateResponse,
    PaymentStatus,
    StatusResponse,
)

logger = logging.getLogger(__name__)

_BASES = {
    "sandbox": "https://api-m.sandbox.paypal.com",
    "live": "https://api-m.paypal.com",
}


class PayPalError(AppError):
    code = "payment_provider_error"


class PayPalProvider:
    name = "paypal"

    def __init__(
        self, *, client_id: str, client_secret: str, environment: str = "sandbox"
    ):
        if not client_id or not client_secret:
            raise PayPalError(
                "PayPal is selected but Client ID / Client Secret are not configured. "
                "Fill them in Admin → Settings → Payments."
            )
        self._client_id = client_id
        self._client_secret = client_secret
        self._base = _BASES.get(environment, _BASES["sandbox"])

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------

    def initiate(self, req: InitiateRequest) -> InitiateResponse:
        token = self._get_token()
        amount_value = f"{req.amount_minor / 100:.2f}"
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "custom_id": req.merchant_transaction_id,
                    "amount": {
                        "currency_code": req.currency,
                        "value": amount_value,
                    },
                }
            ],
            "application_context": {
                "return_url": req.return_url,
                "cancel_url": req.return_url + "&cancelled=1",
                "user_action": "PAY_NOW",
            },
        }
        resp = self._post("/v2/checkout/orders", payload, token)
        redirect = ""
        for link in resp.get("links", []):
            if link.get("rel") == "approve":
                redirect = link.get("href", "")
                break
        if not redirect:
            raise PayPalError(
                "PayPal did not return an approval URL.",
                details={"resp": resp},
            )
        return InitiateResponse(
            redirect_url=redirect,
            provider_transaction_id=resp.get("id"),
            raw=resp,
        )

    def fetch_status(
        self, merchant_transaction_id: str, provider_ref: str | None = None
    ) -> StatusResponse:
        if not provider_ref:
            return StatusResponse(
                merchant_transaction_id=merchant_transaction_id,
                status=PaymentStatus.PENDING,
            )
        token = self._get_token()
        resp = self._get(f"/v2/checkout/orders/{provider_ref}", token)
        pp_status = (resp.get("status") or "").upper()

        if pp_status == "APPROVED":
            # Capture it now.
            capture_resp = self._post(
                f"/v2/checkout/orders/{provider_ref}/capture", {}, token
            )
            pp_status = (capture_resp.get("status") or "").upper()
            resp = capture_resp

        if pp_status == "COMPLETED":
            # Amount from purchase_units[0].amount.value * 100
            try:
                value = resp["purchase_units"][0]["amount"]["value"]
                amount_minor = round(float(value) * 100)
            except (KeyError, IndexError, ValueError, TypeError):
                amount_minor = None
            return StatusResponse(
                merchant_transaction_id=merchant_transaction_id,
                status=PaymentStatus.SUCCESS,
                provider_transaction_id=provider_ref,
                amount_minor=amount_minor,
                raw=resp,
            )
        if pp_status == "VOIDED":
            return StatusResponse(
                merchant_transaction_id=merchant_transaction_id,
                status=PaymentStatus.FAILED,
                provider_transaction_id=provider_ref,
                raw=resp,
            )
        return StatusResponse(
            merchant_transaction_id=merchant_transaction_id,
            status=PaymentStatus.PENDING,
            provider_transaction_id=provider_ref,
            raw=resp,
        )

    def verify_webhook(self, body: bytes, signature: str | None) -> bool:
        # PayPal webhook verification requires storing a webhook_id and calling
        # /v1/notifications/verify-webhook-signature, which adds extra latency
        # and a stored secret not covered by the standard credential model.
        # Status-polling on the return URL is used for confirmation instead.
        # See: https://developer.paypal.com/api/webhooks/v1/
        return False

    def parse_webhook(self, body: bytes) -> StatusResponse:
        # verify_webhook always returns False so this is never reached in the
        # normal webhook flow. Implemented as a minimal stub for completeness.
        payload = json.loads(body)
        resource = payload.get("resource", {})
        mtid = (
            resource.get("custom_id")
            or (resource.get("purchase_units") or [{}])[0].get("custom_id")
            or ""
        )
        if not mtid:
            raise PayPalError("PayPal webhook payload missing custom_id.")
        event_type = payload.get("event_type", "")
        if event_type in ("CHECKOUT.ORDER.COMPLETED", "PAYMENT.CAPTURE.COMPLETED"):
            status_ = PaymentStatus.SUCCESS
        elif event_type in ("CHECKOUT.ORDER.VOIDED",):
            status_ = PaymentStatus.FAILED
        else:
            status_ = PaymentStatus.PENDING
        return StatusResponse(
            merchant_transaction_id=mtid,
            status=status_,
            raw=payload,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_token(self) -> str:
        url = self._base + "/v1/oauth2/token"
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.post(
                    url,
                    data={"grant_type": "client_credentials"},
                    auth=(self._client_id, self._client_secret),
                )
                r.raise_for_status()
                return r.json()["access_token"]
        except httpx.HTTPStatusError as exc:
            logger.warning("paypal token %s", exc.response.text[:500])
            raise PayPalError(
                "PayPal authentication failed.",
                details={"status": exc.response.status_code},
            )
        except httpx.HTTPError as exc:
            logger.warning("paypal token network error: %s", exc)
            raise PayPalError("Could not reach PayPal.")

    def _post(self, path: str, body: dict, token: str) -> dict:
        url = self._base + path
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.post(url, json=body, headers=headers)
                r.raise_for_status()
                # Capture endpoint returns 201 with no body on some responses
                if r.content:
                    return r.json()
                return {}
        except httpx.HTTPStatusError as exc:
            logger.warning("paypal POST %s -> %s", path, exc.response.text[:500])
            raise PayPalError(
                "PayPal rejected the request.",
                details={"status": exc.response.status_code},
            )
        except httpx.HTTPError as exc:
            logger.warning("paypal POST %s network error: %s", path, exc)
            raise PayPalError("Could not reach PayPal.")

    def _get(self, path: str, token: str) -> dict:
        url = self._base + path
        headers = {"Authorization": f"Bearer {token}"}
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.get(url, headers=headers)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("paypal GET %s -> %s", path, exc.response.text[:500])
            raise PayPalError(
                "PayPal status check failed.",
                details={"status": exc.response.status_code},
            )
        except httpx.HTTPError as exc:
            logger.warning("paypal GET %s network error: %s", path, exc)
            raise PayPalError("Could not reach PayPal.")
