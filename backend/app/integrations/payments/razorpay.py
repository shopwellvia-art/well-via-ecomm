"""Razorpay integration — Standard Checkout (Orders API) with Payment Links
legacy support.

New checkouts create an Order (`order_...`) and hand the SPA an embedded
checkout payload for checkout.js; settlement arrives via the browser's
verify callback (server-side signature + gateway re-check) or the S2S
webhook. Orders placed before this switch carry `plink_...` provider refs,
so fetch_status / refund / parse_webhook keep their Payment Links branches
alive until those rows age out.

References:
  https://razorpay.com/docs/api/orders/
  https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/
  https://razorpay.com/docs/api/payment-links/   (legacy rows only)
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

# Razorpay payment-link status strings (legacy plink_ rows).
_STATUS_MAP = {
    "paid": PaymentStatus.SUCCESS,
    "cancelled": PaymentStatus.FAILED,
    "expired": PaymentStatus.FAILED,
}

# Razorpay payment-entity status strings (pay_ refs). Deliberately narrow:
# "authorized" and "created" stay PENDING — money isn't ours until capture —
# and "refunded" stays out because a refunded payment on a still-PENDING
# order is an operator problem, not a settlement signal.
_PAYMENT_STATUS_MAP = {
    "captured": PaymentStatus.SUCCESS,
    "failed": PaymentStatus.FAILED,
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
        # Kept individually as well: key_id is embedded in the checkout.js
        # payload the SPA renders, and key_secret signs/verifies the
        # order|payment signature Standard Checkout hands back.
        self._key_id = key_id
        self._key_secret = key_secret
        self._webhook_secret = webhook_secret

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------

    def initiate(self, req: InitiateRequest) -> InitiateResponse:
        """Create a Razorpay Order for Standard Checkout.

        Replaces the old Payment Links initiation: instead of a hosted page we
        get an ``order_...`` id and hand the SPA a ``checkout`` payload for
        checkout.js, which renders the payment sheet inside our own page.
        ``redirect_url`` is "" — there is nowhere to redirect to.

        Both ``receipt`` and ``notes.mtid`` carry our merchant transaction id
        so every downstream artefact (payment entity, order entity, webhook
        payload) can be traced back to the order without a DB join on the
        provider ref. Settlement lands via /payments/razorpay/verify
        (signature + gateway re-check) or the payment.captured / order.paid
        webhook — never from the browser's word alone.
        """
        payload = {
            "amount": req.amount_minor,
            "currency": req.currency,
            "receipt": req.merchant_transaction_id,
            "notes": {"mtid": req.merchant_transaction_id},
        }
        resp = self._post("/orders", payload)
        order_id = resp.get("id")
        if not order_id:
            raise RazorpayError(
                "Razorpay did not return an order id.",
                details={"resp": resp},
            )
        return InitiateResponse(
            redirect_url="",
            provider_transaction_id=order_id,
            raw=resp,
            checkout={
                "provider": "razorpay",
                "key_id": self._key_id,
                "order_id": order_id,
                "amount": req.amount_minor,
                "currency": req.currency,
                "name": "Wellvia",
                "description": f"Order {req.merchant_transaction_id}",
                "prefill": {"email": req.user_email or ""},
                "notes": {"mtid": req.merchant_transaction_id},
            },
        )

    def verify_signature(
        self, order_id: str, payment_id: str, signature: str | None
    ) -> bool:
        """True iff ``signature`` is Razorpay's HMAC for this order/payment pair.

        Standard Checkout's success handler gives the browser
        ``razorpay_signature`` = HMAC-SHA256(key_secret, "order_id|payment_id").
        Verifying it server-side proves Razorpay minted the pair under our key
        — it does NOT prove money moved or that the pair belongs to any
        particular order of ours; callers must still re-fetch the payment from
        the gateway before settling. Missing/empty inputs are a hard False.
        """
        if not order_id or not payment_id or not signature:
            return False
        digest = hmac.new(
            self._key_secret.encode(),
            f"{order_id}|{payment_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(digest, signature)

    def fetch_payment(self, payment_id: str) -> dict:
        """GET the raw payment entity (``pay_...``) from the gateway.

        The verify path uses it as the source of truth for status + amount
        after the signature check passes. May raise RazorpayError on HTTP
        failure — callers decide whether that blocks settlement (it should).
        """
        return self._get(f"/payments/{payment_id}")

    def fetch_status(
        self, merchant_transaction_id: str, provider_ref: str | None = None
    ) -> StatusResponse:
        """Resolve the payment status behind whatever ref we recorded.

        Three ref generations coexist in the orders table, so branch on the
        prefix:

          ``pay_``   — a captured-payment id (stamped at settlement by the
                       verify path / new webhooks). GET the payment entity.
          ``order_`` — a Standard Checkout order id (stamped at initiate,
                       before any payment exists). GET the order's payments
                       and scan for a captured one; the pay_ id becomes the
                       provider_transaction_id so _apply_status can upgrade
                       the stored ref. No captured payment → PENDING, NOT
                       FAILED: an abandoned or failed attempt on an order
                       doesn't preclude a retry inside the same checkout.
          ``plink_`` — legacy Payment Links rows; unchanged behavior.
        """
        if not provider_ref:
            # Without the provider ref we cannot look anything up.
            return StatusResponse(
                merchant_transaction_id=merchant_transaction_id,
                status=PaymentStatus.PENDING,
            )
        ref = provider_ref.strip()

        if ref.startswith("pay_"):
            resp = self._get(f"/payments/{ref}")
            rzp_status = (resp.get("status") or "").lower()
            status_ = _PAYMENT_STATUS_MAP.get(rzp_status, PaymentStatus.PENDING)
            return StatusResponse(
                merchant_transaction_id=merchant_transaction_id,
                status=status_,
                provider_transaction_id=ref,
                amount_minor=(
                    resp.get("amount") if status_ == PaymentStatus.SUCCESS else None
                ),
                raw=resp,
            )

        if ref.startswith("order_"):
            resp = self._get(f"/orders/{ref}/payments")
            for p in resp.get("items") or []:
                if (p.get("status") or "").lower() == "captured":
                    return StatusResponse(
                        merchant_transaction_id=merchant_transaction_id,
                        status=PaymentStatus.SUCCESS,
                        provider_transaction_id=p.get("id"),
                        amount_minor=p.get("amount"),
                        raw=resp,
                    )
            return StatusResponse(
                merchant_transaction_id=merchant_transaction_id,
                status=PaymentStatus.PENDING,
                provider_transaction_id=ref,
                raw=resp,
            )

        # Legacy Payment Links ref (plink_ and anything unrecognized).
        resp = self._get(f"/payment_links/{ref}")
        rzp_status = (resp.get("status") or "").lower()
        status_ = _STATUS_MAP.get(rzp_status, PaymentStatus.PENDING)
        amount_minor: int | None = None
        if status_ == PaymentStatus.SUCCESS:
            amount_minor = resp.get("amount_paid") or resp.get("amount")
        return StatusResponse(
            merchant_transaction_id=merchant_transaction_id,
            status=status_,
            provider_transaction_id=ref,
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
        """Extract the resolved status from a (signature-verified) webhook.

        Handles both checkout generations:
          ``payment_link.paid``               — legacy Payment Links rows.
          ``payment.captured`` / ``order.paid`` — Standard Checkout. The mtid
            travels in ``payment.entity.notes.mtid`` (stamped at initiate)
            with ``order.entity.receipt`` / ``order.entity.notes.mtid`` as
            fallbacks, and the ``pay_...`` id becomes the
            provider_transaction_id so settlement finally records the
            captured-payment id (the plink_-era rows never carried it).

        Unknown events resolve to PENDING with whatever mtid is recoverable —
        handle_webhook treats PENDING as a no-op. A payload with NO recoverable
        mtid also resolves to PENDING (with an empty mtid handle_webhook acks
        without correlating): raising here would 502 the webhook route, Razorpay
        would redeliver the same uncorrelatable event forever, and sustained
        failures get the whole webhook disabled — one stray event must never
        cost us the delivery channel.
        """
        payload = json.loads(body)
        event = payload.get("event", "")
        entities = payload.get("payload", {}) or {}
        link = (entities.get("payment_link", {}) or {}).get("entity", {}) or {}
        payment = (entities.get("payment", {}) or {}).get("entity", {}) or {}
        order = (entities.get("order", {}) or {}).get("entity", {}) or {}

        mtid = (
            link.get("reference_id")
            or (payment.get("notes") or {}).get("mtid")
            or order.get("receipt")
            or (order.get("notes") or {}).get("mtid")
            or ""
        )
        if not mtid:
            logger.warning(
                "razorpay webhook event %r carries no merchant transaction id "
                "(payment=%s) — acking as a no-op",
                event, payment.get("id") or link.get("id") or order.get("id"),
            )
            return StatusResponse(
                merchant_transaction_id="",
                status=PaymentStatus.PENDING,
                provider_transaction_id=(
                    payment.get("id") or link.get("id") or order.get("id")
                ),
                raw=payload,
            )

        if event == "payment_link.paid":
            return StatusResponse(
                merchant_transaction_id=mtid,
                status=PaymentStatus.SUCCESS,
                provider_transaction_id=link.get("id"),
                amount_minor=link.get("amount_paid") or link.get("amount"),
                raw=payload,
            )

        if event in ("payment.captured", "order.paid"):
            # Both events carry the payment entity — its pay_ id and amount
            # are what settlement stores and amount-guards against.
            return StatusResponse(
                merchant_transaction_id=mtid,
                status=PaymentStatus.SUCCESS,
                provider_transaction_id=payment.get("id") or order.get("id"),
                amount_minor=payment.get("amount") or order.get("amount_paid"),
                raw=payload,
            )

        return StatusResponse(
            merchant_transaction_id=mtid,
            status=PaymentStatus.PENDING,
            provider_transaction_id=(
                payment.get("id") or link.get("id") or order.get("id")
            ),
            raw=payload,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_captured_payment_id(self, req: RefundRequest) -> str | None:
        """Resolve the captured ``pay_...`` id for a refund request.

        ``req.original_transaction_id`` is whatever we recorded at settlement:
        ideally the captured payment id itself, but legacy rows carry the
        ``plink_...`` id and Standard Checkout rows settled before the verify
        callback landed may still carry the ``order_...`` id. Resolution order:

          1. Already a ``pay_...`` id → use it directly.
          2. ``order_...`` (Standard Checkout) → GET the order's payments
             (``/orders/{id}/payments``) and pick the captured one.
          3. ``plink_...`` → GET the payment link; scan its ``payments`` array
             for a captured entry; fall back to the link's underlying order's
             payments.

        Returns None when no captured payment exists (unpaid/expired link,
        authorized-but-never-captured, refunded already under a different id)
        so the caller can return the failure shape. May raise RazorpayError on
        HTTP failure — ``refund()`` catches it.
        """
        ref = (req.original_transaction_id or "").strip()
        if ref.startswith("pay_"):
            return ref
        if ref.startswith("order_"):
            resp = self._get(f"/orders/{ref}/payments")
            for p in resp.get("items") or []:
                if (p.get("status") or "").lower() == "captured":
                    return p.get("id")
            return None
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
