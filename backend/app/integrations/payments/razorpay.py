"""Razorpay Payment Links integration.

Uses the Payment Links API so we don't need a custom checkout page.
References:
  https://razorpay.com/docs/api/payment-links/
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
    RefundRequest,
    RefundResult,
    StatusResponse,
    provider_rejection,
)

logger = logging.getLogger(__name__)

_BASE = "https://api.razorpay.com/v1"

# Razorpay payment-link status strings.
_STATUS_MAP = {
    "paid": PaymentStatus.SUCCESS,
    "cancelled": PaymentStatus.FAILED,
    "expired": PaymentStatus.FAILED,
}

# Razorpay refund-entity status strings. Refunds commonly settle
# asynchronously — "pending"/"created" means accepted, funds in flight.
_REFUND_STATUS_MAP = {
    "processed": PaymentStatus.SUCCESS,
    "failed": PaymentStatus.FAILED,
}


class RazorpayError(PaymentGatewayError):
    code = "payment_provider_error"


class RazorpayProvider:
    name = "razorpay"

    def __init__(self, *, key_id: str, key_secret: str, webhook_secret: str = ""):
        if not key_id or not key_secret:
            raise RazorpayError(
                "Razorpay is selected but Key ID / Key Secret are not configured. "
                "Fill them in Admin → Settings → Payments."
            )
        self._auth = (key_id, key_secret)
        self._webhook_secret = webhook_secret

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------

    def initiate(self, req: InitiateRequest) -> InitiateResponse:
        payload = {
            "amount": req.amount_minor,
            "currency": req.currency,
            "reference_id": req.merchant_transaction_id,
            "callback_url": req.return_url,
            "callback_method": "get",
        }
        resp = self._post("/payment_links", payload)
        redirect = resp.get("short_url") or resp.get("url") or ""
        if not redirect:
            raise RazorpayError(
                "Razorpay did not return a redirect URL.",
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
            # Without the provider ref we cannot look up the link.
            return StatusResponse(
                merchant_transaction_id=merchant_transaction_id,
                status=PaymentStatus.PENDING,
            )
        resp = self._get(f"/payment_links/{provider_ref}")
        rzp_status = (resp.get("status") or "").lower()
        status_ = _STATUS_MAP.get(rzp_status, PaymentStatus.PENDING)
        amount_minor: int | None = None
        if status_ == PaymentStatus.SUCCESS:
            amount_minor = resp.get("amount_paid") or resp.get("amount")
        return StatusResponse(
            merchant_transaction_id=merchant_transaction_id,
            status=status_,
            provider_transaction_id=provider_ref,
            amount_minor=amount_minor,
            raw=resp,
        )

    def refund(self, req: RefundRequest) -> RefundResult:
        """Reverse (part of) a captured Payment-Links payment back to the
        customer's original instrument.

        The Payment Links flow never hands us the captured ``pay_...`` id at
        checkout — we store the link id (``plink_...``) as the provider ref —
        so the refund first resolves the captured payment id via the REST API
        (fetch the payment link, falling back to the underlying order's
        payments), then POSTs ``/payments/{payment_id}/refund`` with the
        amount in paise.

        NEVER raises: any resolution/HTTP failure (link not paid, partial
        capture, missing payment, gateway rejection, network error) is
        returned as a FAILED RefundResult so callers fall back to a manual
        refund instead of aborting their own state transition.

        Idempotent per ``req.refund_reference``: before POSTing, the payment's
        existing refunds are checked for one already issued under this
        reference (receipt / notes.reference) and returned as-is when found.
        Callers mint DETERMINISTIC references (e.g. RFNDORD{order}-{leg}), so
        a retry after "gateway refund succeeded but our DB commit failed"
        re-presents the same reference and short-circuits here instead of
        paying the customer twice.
        """
        try:
            payment_id = self._resolve_captured_payment_id(req)
            if not payment_id:
                logger.warning(
                    "razorpay refund order=%s ref=%s: no captured payment "
                    "found for %r — falling back to manual",
                    req.order_id, req.refund_reference, req.original_transaction_id,
                )
                return RefundResult(
                    refund_id=req.refund_reference,
                    status=PaymentStatus.FAILED,
                    raw={
                        "error": "no captured payment found",
                        "original_transaction_id": req.original_transaction_id,
                    },
                )
            existing = self._find_existing_refund(payment_id, req.refund_reference)
            if existing is not None:
                logger.info(
                    "razorpay refund order=%s ref=%s: refund %s already issued "
                    "under this reference — returning it instead of re-refunding",
                    req.order_id, req.refund_reference, existing.get("id"),
                )
                rzp_status = (existing.get("status") or "").lower()
                return RefundResult(
                    refund_id=existing.get("id") or req.refund_reference,
                    status=_REFUND_STATUS_MAP.get(rzp_status, PaymentStatus.PENDING),
                    raw=existing,
                )
            payload: dict = {
                "amount": req.amount_minor,  # paise
                "receipt": req.refund_reference,
                "notes": {"reference": req.refund_reference},
            }
            if req.reason:
                payload["notes"]["reason"] = req.reason[:255]
            resp = self._post(f"/payments/{payment_id}/refund", payload)
            rzp_status = (resp.get("status") or "").lower()
            status_ = _REFUND_STATUS_MAP.get(rzp_status, PaymentStatus.PENDING)
            return RefundResult(
                refund_id=resp.get("id") or req.refund_reference,
                status=status_,
                raw=resp,
            )
        except RazorpayError as exc:
            logger.warning(
                "razorpay refund order=%s ref=%s failed: %s — falling back to manual",
                req.order_id, req.refund_reference, exc.message,
            )
            return RefundResult(
                refund_id=req.refund_reference,
                status=PaymentStatus.FAILED,
                raw={"error": exc.message, "details": exc.details},
            )
        except Exception as exc:  # noqa: BLE001 — never-raise contract
            # _post/_get only wrap httpx errors as RazorpayError; anything
            # else (a non-JSON 2xx body from r.json(), an unexpected payload
            # shape, ...) must still come back as a FAILED result so callers
            # fall back to a manual refund instead of aborting their own
            # state transition.
            logger.warning(
                "razorpay refund order=%s ref=%s raised %s: %s — falling back "
                "to manual",
                req.order_id, req.refund_reference, type(exc).__name__, exc,
            )
            return RefundResult(
                refund_id=req.refund_reference,
                status=PaymentStatus.FAILED,
                raw={"error": str(exc)},
            )

    def verify_webhook(self, body: bytes, signature: str | None) -> bool:
        if not self._webhook_secret or not signature:
            return False
        digest = hmac.new(
            self._webhook_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(digest, signature)

    def parse_webhook(self, body: bytes) -> StatusResponse:
        payload = json.loads(body)
        event = payload.get("event", "")
        entity = (payload.get("payload", {}).get("payment_link", {}) or {}).get(
            "entity", {}
        )
        mtid = entity.get("reference_id") or ""
        if not mtid:
            raise RazorpayError("Webhook payload missing reference_id.")
        if event == "payment_link.paid":
            status_ = PaymentStatus.SUCCESS
            amount_minor = entity.get("amount_paid") or entity.get("amount")
        else:
            status_ = PaymentStatus.PENDING
            amount_minor = None
        return StatusResponse(
            merchant_transaction_id=mtid,
            status=status_,
            provider_transaction_id=entity.get("id"),
            amount_minor=amount_minor,
            raw=payload,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_captured_payment_id(self, req: RefundRequest) -> str | None:
        """Resolve the captured ``pay_...`` id for a refund request.

        ``req.original_transaction_id`` is whatever we recorded at settlement:
        ideally the captured payment id itself, but for the Payment Links flow
        it is usually the ``plink_...`` id. Resolution order:

          1. Already a ``pay_...`` id → use it directly.
          2. ``plink_...`` → GET the payment link; scan its ``payments`` array
             for a captured entry.
          3. Still nothing → GET the link's underlying order's payments
             (``/orders/{order_id}/payments``) and pick the captured one.

        Returns None when no captured payment exists (unpaid/expired link,
        authorized-but-never-captured, refunded already under a different id)
        so the caller can return the failure shape. May raise RazorpayError on
        HTTP failure — ``refund()`` catches it.
        """
        ref = (req.original_transaction_id or "").strip()
        if ref.startswith("pay_"):
            return ref
        if not ref.startswith("plink_"):
            return None

        link = self._get(f"/payment_links/{ref}")
        for p in link.get("payments") or []:
            if (p.get("status") or "").lower() == "captured":
                return p.get("payment_id") or p.get("id")

        order_id = link.get("order_id")
        if order_id:
            resp = self._get(f"/orders/{order_id}/payments")
            for p in resp.get("items") or []:
                if (p.get("status") or "").lower() == "captured":
                    return p.get("id")
        return None

    def _find_existing_refund(self, payment_id: str, reference: str) -> dict | None:
        """The refund already issued against ``payment_id`` under our merchant
        ``reference``, or None.

        GET /payments/{id}/refunds and match on ``receipt`` or
        ``notes.reference`` (both are stamped with the reference at POST time).
        Razorpay's receipt/notes are plain metadata — NOT a gateway-side
        idempotency mechanism — so this pre-check is what makes a refund retry
        safe. May raise RazorpayError on HTTP failure; ``refund()`` catches it
        and falls back to manual (never blind-POST when we can't rule out an
        existing refund).
        """
        resp = self._get(f"/payments/{payment_id}/refunds")
        for r in resp.get("items") or []:
            notes = r.get("notes") or {}
            if reference and (
                r.get("receipt") == reference or notes.get("reference") == reference
            ):
                return r
        return None

    def _post(self, path: str, body: dict) -> dict:
        url = _BASE + path
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.post(url, json=body, auth=self._auth)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("razorpay POST %s -> %s", path, exc.response.text[:500])
            raise provider_rejection(RazorpayError, "Razorpay rejected the request", exc.response)
        except httpx.HTTPError as exc:
            logger.warning("razorpay POST %s network error: %s", path, exc)
            raise RazorpayError("Could not reach Razorpay.")

    def _get(self, path: str) -> dict:
        url = _BASE + path
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.get(url, auth=self._auth)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("razorpay GET %s -> %s", path, exc.response.text[:500])
            raise provider_rejection(RazorpayError, "Razorpay status check failed", exc.response)
        except httpx.HTTPError as exc:
            logger.warning("razorpay GET %s network error: %s", path, exc)
            raise RazorpayError("Could not reach Razorpay.")
