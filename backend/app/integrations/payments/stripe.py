"""Stripe Checkout Sessions integration.

Uses Stripe Checkout hosted pages so we don't maintain a custom card form.
References:
  https://stripe.com/docs/api/checkout/sessions
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time

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

_BASE = "https://api.stripe.com/v1"


class StripeError(PaymentGatewayError):
    code = "payment_provider_error"


class StripeProvider:
    name = "stripe"

    def __init__(
        self,
        *,
        publishable_key: str,
        secret_key: str,
        webhook_secret: str = "",
    ):
        if not secret_key:
            raise StripeError(
                "Stripe is selected but the Secret Key is not configured. "
                "Fill it in Admin → Settings → Payments."
            )
        self._secret_key = secret_key
        self._publishable_key = publishable_key
        self._webhook_secret = webhook_secret

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------

    def initiate(self, req: InitiateRequest) -> InitiateResponse:
        # Stripe form-encoded API
        data = {
            "mode": "payment",
            "client_reference_id": req.merchant_transaction_id,
            "success_url": req.return_url + "&stripe=1",
            "cancel_url": req.return_url + "&cancelled=1",
            "line_items[0][price_data][currency]": req.currency.lower(),
            "line_items[0][price_data][unit_amount]": str(req.amount_minor),
            "line_items[0][price_data][product_data][name]": f"Order {req.merchant_transaction_id}",
            "line_items[0][quantity]": "1",
        }
        resp = self._post("/checkout/sessions", data)
        redirect = resp.get("url") or ""
        if not redirect:
            raise StripeError(
                "Stripe did not return a checkout URL.",
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
        resp = self._get(f"/checkout/sessions/{provider_ref}")
        payment_status = resp.get("payment_status", "")
        session_status = resp.get("status", "")
        if payment_status == "paid":
            status_ = PaymentStatus.SUCCESS
            amount_minor = resp.get("amount_total")
        elif session_status == "expired":
            status_ = PaymentStatus.FAILED
            amount_minor = None
        else:
            status_ = PaymentStatus.PENDING
            amount_minor = None
        return StatusResponse(
            merchant_transaction_id=merchant_transaction_id,
            status=status_,
            provider_transaction_id=provider_ref,
            amount_minor=amount_minor,
            raw=resp,
        )

    def verify_webhook(self, body: bytes, signature: str | None) -> bool:
        """Verify Stripe-Signature header (t=...,v1=... format)."""
        if not self._webhook_secret or not signature:
            return False
        # Parse t= and v1= from header
        parts: dict[str, str] = {}
        for part in signature.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                parts[k.strip()] = v.strip()
        timestamp = parts.get("t")
        v1 = parts.get("v1")
        if not timestamp or not v1:
            return False
        signed_payload = f"{timestamp}.{body.decode('utf-8', errors='replace')}"
        expected = hmac.new(
            self._webhook_secret.encode(),
            signed_payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, v1)

    def parse_webhook(self, body: bytes) -> StatusResponse:
        payload = json.loads(body)
        event_type = payload.get("type", "")
        session = payload.get("data", {}).get("object", {})
        mtid = session.get("client_reference_id") or ""
        if not mtid:
            raise StripeError("Webhook payload missing client_reference_id.")
        if event_type == "checkout.session.completed":
            status_ = PaymentStatus.SUCCESS
            amount_minor = session.get("amount_total")
        elif event_type == "checkout.session.expired":
            status_ = PaymentStatus.FAILED
            amount_minor = None
        else:
            status_ = PaymentStatus.PENDING
            amount_minor = None
        return StatusResponse(
            merchant_transaction_id=mtid,
            status=status_,
            amount_minor=amount_minor,
            raw=payload,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _post(self, path: str, data: dict) -> dict:
        url = _BASE + path
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.post(
                    url,
                    data=data,
                    headers={"Authorization": f"Bearer {self._secret_key}"},
                )
                r.raise_for_status()
                return r.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("stripe POST %s -> %s", path, exc.response.text[:500])
            raise provider_rejection(StripeError, "Stripe rejected the request", exc.response)
        except httpx.HTTPError as exc:
            logger.warning("stripe POST %s network error: %s", path, exc)
            raise StripeError("Could not reach Stripe.")

    def _get(self, path: str) -> dict:
        url = _BASE + path
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.get(
                    url,
                    headers={"Authorization": f"Bearer {self._secret_key}"},
                )
                r.raise_for_status()
                return r.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("stripe GET %s -> %s", path, exc.response.text[:500])
            raise provider_rejection(StripeError, "Stripe status check failed", exc.response)
        except httpx.HTTPError as exc:
            logger.warning("stripe GET %s network error: %s", path, exc)
            raise StripeError("Could not reach Stripe.")
