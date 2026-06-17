"""Paystack payment integration.

Uses the Transaction Initialize API for redirect-based checkout.
References:
  https://paystack.com/docs/api/transaction/
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging

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

_BASE = "https://api.paystack.co"

_FAILED_STATUSES = {"failed", "abandoned", "reversed"}


class PaystackError(PaymentGatewayError):
    code = "payment_provider_error"


class PaystackProvider:
    name = "paystack"

    def __init__(self, *, public_key: str, secret_key: str):
        if not secret_key:
            raise PaystackError(
                "Paystack is selected but the Secret Key is not configured. "
                "Fill it in Admin → Settings → Payments."
            )
        self._public_key = public_key
        self._secret_key = secret_key

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------

    def initiate(self, req: InitiateRequest) -> InitiateResponse:
        payload: dict = {
            "amount": req.amount_minor,
            "currency": req.currency,
            "reference": req.merchant_transaction_id,
            "callback_url": req.return_url,
            "email": req.user_email or "customer@example.com",
        }
        resp = self._post("/transaction/initialize", payload)
        data = resp.get("data", {})
        redirect = data.get("authorization_url") or ""
        if not redirect:
            raise PaystackError(
                "Paystack did not return an authorization URL.",
                details={"resp": resp},
            )
        return InitiateResponse(
            redirect_url=redirect,
            provider_transaction_id=data.get("access_code"),
            raw=resp,
        )

    def fetch_status(
        self, merchant_transaction_id: str, provider_ref: str | None = None
    ) -> StatusResponse:
        # Paystack verifies by reference (our mtid).
        resp = self._get(f"/transaction/verify/{merchant_transaction_id}")
        data = resp.get("data", {})
        ps_status = (data.get("status") or "").lower()
        if ps_status == "success":
            status_ = PaymentStatus.SUCCESS
            amount_minor = data.get("amount")
        elif ps_status in _FAILED_STATUSES:
            status_ = PaymentStatus.FAILED
            amount_minor = None
        else:
            status_ = PaymentStatus.PENDING
            amount_minor = None
        return StatusResponse(
            merchant_transaction_id=merchant_transaction_id,
            status=status_,
            amount_minor=amount_minor,
            raw=resp,
        )

    def verify_webhook(self, body: bytes, signature: str | None) -> bool:
        if not signature:
            return False
        expected = hmac.new(
            self._secret_key.encode(),
            body,
            hashlib.sha512,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse_webhook(self, body: bytes) -> StatusResponse:
        payload = json.loads(body)
        event = payload.get("event", "")
        data = payload.get("data", {})
        reference = data.get("reference") or ""
        if not reference:
            raise PaystackError("Paystack webhook missing reference.")
        if event == "charge.success":
            status_ = PaymentStatus.SUCCESS
            amount_minor = data.get("amount")
        else:
            status_ = PaymentStatus.PENDING
            amount_minor = None
        return StatusResponse(
            merchant_transaction_id=reference,
            status=status_,
            amount_minor=amount_minor,
            raw=payload,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._secret_key}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, body: dict) -> dict:
        url = _BASE + path
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.post(url, json=body, headers=self._headers())
                r.raise_for_status()
                return r.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("paystack POST %s -> %s", path, exc.response.text[:500])
            raise provider_rejection(PaystackError, "Paystack rejected the request", exc.response)
        except httpx.HTTPError as exc:
            logger.warning("paystack POST %s network error: %s", path, exc)
            raise PaystackError("Could not reach Paystack.")

    def _get(self, path: str) -> dict:
        url = _BASE + path
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.get(url, headers=self._headers())
                r.raise_for_status()
                return r.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("paystack GET %s -> %s", path, exc.response.text[:500])
            raise provider_rejection(PaystackError, "Paystack status check failed", exc.response)
        except httpx.HTTPError as exc:
            logger.warning("paystack GET %s network error: %s", path, exc)
            raise PaystackError("Could not reach Paystack.")
