"""Flutterwave Hosted Payments integration.

Uses the /v3/payments endpoint which returns a hosted link.
References:
  https://developer.flutterwave.com/docs/collecting-payments/standard
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging

import httpx

from app.core.exceptions import AppError
from app.integrations.payments.base import (
    InitiateRequest,
    InitiateResponse,
    PaymentStatus,
    StatusResponse,
    provider_rejection,
)

logger = logging.getLogger(__name__)

_BASE = "https://api.flutterwave.com/v3"


class FlutterwaveError(AppError):
    code = "payment_provider_error"


class FlutterwaveProvider:
    name = "flutterwave"

    def __init__(
        self,
        *,
        public_key: str,
        secret_key: str,
        webhook_secret_hash: str = "",
    ):
        if not secret_key:
            raise FlutterwaveError(
                "Flutterwave is selected but the Secret Key is not configured. "
                "Fill it in Admin → Settings → Payments."
            )
        self._public_key = public_key
        self._secret_key = secret_key
        self._webhook_secret_hash = webhook_secret_hash

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------

    def initiate(self, req: InitiateRequest) -> InitiateResponse:
        payload = {
            "tx_ref": req.merchant_transaction_id,
            "amount": str(req.amount_minor / 100),
            "currency": req.currency,
            "redirect_url": req.return_url,
            "customer": {
                "email": req.user_email or "customer@example.com",
            },
        }
        resp = self._post("/payments", payload)
        data = resp.get("data", {})
        redirect = data.get("link") or ""
        if not redirect:
            raise FlutterwaveError(
                "Flutterwave did not return a payment link.",
                details={"resp": resp},
            )
        return InitiateResponse(
            redirect_url=redirect,
            provider_transaction_id=None,
            raw=resp,
        )

    def fetch_status(
        self, merchant_transaction_id: str, provider_ref: str | None = None
    ) -> StatusResponse:
        resp = self._get(
            f"/transactions/verify_by_reference?tx_ref={merchant_transaction_id}"
        )
        data = resp.get("data", {})
        fw_status = (data.get("status") or "").lower()
        if fw_status == "successful":
            # Convert float amount to minor units
            try:
                amount_minor = int(round(float(data["amount"]) * 100))
            except (KeyError, TypeError, ValueError):
                amount_minor = None
            return StatusResponse(
                merchant_transaction_id=merchant_transaction_id,
                status=PaymentStatus.SUCCESS,
                amount_minor=amount_minor,
                raw=resp,
            )
        if fw_status == "failed":
            return StatusResponse(
                merchant_transaction_id=merchant_transaction_id,
                status=PaymentStatus.FAILED,
                raw=resp,
            )
        return StatusResponse(
            merchant_transaction_id=merchant_transaction_id,
            status=PaymentStatus.PENDING,
            raw=resp,
        )

    def verify_webhook(self, body: bytes, signature: str | None) -> bool:
        """Flutterwave uses a plain secret-hash header (verif-hash), not HMAC."""
        if not self._webhook_secret_hash or not signature:
            return False
        return hmac.compare_digest(self._webhook_secret_hash, signature)

    def parse_webhook(self, body: bytes) -> StatusResponse:
        payload = json.loads(body)
        data = payload.get("data", {})
        mtid = data.get("tx_ref") or ""
        if not mtid:
            raise FlutterwaveError("Flutterwave webhook missing tx_ref.")
        fw_status = (data.get("status") or "").lower()
        if fw_status == "successful":
            try:
                amount_minor = int(round(float(data["amount"]) * 100))
            except (KeyError, TypeError, ValueError):
                amount_minor = None
            status_ = PaymentStatus.SUCCESS
        elif fw_status == "failed":
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
            logger.warning("flutterwave POST %s -> %s", path, exc.response.text[:500])
            raise provider_rejection(FlutterwaveError, "Flutterwave rejected the request", exc.response)
        except httpx.HTTPError as exc:
            logger.warning("flutterwave POST %s network error: %s", path, exc)
            raise FlutterwaveError("Could not reach Flutterwave.")

    def _get(self, path: str) -> dict:
        url = _BASE + path
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.get(url, headers=self._headers())
                r.raise_for_status()
                return r.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("flutterwave GET %s -> %s", path, exc.response.text[:500])
            raise provider_rejection(FlutterwaveError, "Flutterwave status check failed", exc.response)
        except httpx.HTTPError as exc:
            logger.warning("flutterwave GET %s network error: %s", path, exc)
            raise FlutterwaveError("Could not reach Flutterwave.")
