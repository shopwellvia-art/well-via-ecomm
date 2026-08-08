"""Payment + checkout endpoints.

Routes:
  POST /checkout                          create order + initiate payment
  GET  /payments/{mtid}/status            what the return page polls
  POST /payments/razorpay/verify          Razorpay Standard Checkout browser callback
  POST /payments/webhook/phonepe          S2S callback from PhonePe (signed)
  POST /payments/webhook/mock             local simulator (mock provider only)
  POST /payments/webhook/{gateway_code}   generic signed webhook for any gateway
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, optional_current_user
from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.integrations.payments.registry import get_gateway
from app.models.payment_method import PaymentMethod
from app.models.user import User
from app.schemas.order import OrderRead
from app.schemas.payment import (
    CheckoutRequest,
    CheckoutResponse,
    MockWebhookRequest,
    PaymentStatusResponse,
    RazorpayVerifyRequest,
    ReconcilePendingRequest,
    ReconcilePendingResponse,
)
from app.services.payment_service import PaymentService

checkout_router = APIRouter()
payments_router = APIRouter()


async def get_raw_body(request: Request) -> bytes:
    """Read the raw request body in the async context so the webhook handlers
    themselves can be plain `def` — FastAPI then runs their blocking DB / SMTP /
    SMS / carrier work in the threadpool instead of on the event loop."""
    return await request.body()


def reconcile_auth(
    x_reconcile_token: str | None = Header(default=None, alias="X-Reconcile-Token"),
    user: User | None = Depends(optional_current_user),
) -> None:
    """Authenticate the reconcile endpoint for a machine OR a human.

    A scheduler can't hold a 30-minute human access token, so it presents a
    shared secret (X-Reconcile-Token == settings.PAYMENT_RECONCILE_TOKEN),
    compared in constant time. A signed-in user with payments.manage may also
    trigger it manually. Everything else is refused.
    """
    configured = settings.PAYMENT_RECONCILE_TOKEN
    if (
        configured
        and x_reconcile_token
        and secrets.compare_digest(x_reconcile_token, configured)
    ):
        return
    if user is not None and user.has_permission("payments.manage"):
        return
    raise ForbiddenError(
        "Reconcile requires a valid service token or the payments.manage permission."
    )


@checkout_router.post(
    "", response_model=CheckoutResponse, status_code=status.HTTP_201_CREATED
)
def start_checkout(
    payload: CheckoutRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = PaymentService(db)
    order, mtid, redirect_url, checkout_payload = svc.checkout(user, payload)
    # `amount_minor` is what the gateway was asked to charge — for COD the
    # gateway wasn't called at all (0) and for Split COD it's only the
    # prepaid portion (total − balance). Prepaid orders carry the full total.
    from decimal import Decimal as _Decimal
    if order.payment_method == "cod":
        gateway_amount = _Decimal("0")
    elif order.payment_method == "split_cod":
        gateway_amount = _Decimal(order.total_amount) - _Decimal(order.cod_balance)
    else:
        gateway_amount = _Decimal(order.total_amount)
    amount_minor = int((gateway_amount * 100).to_integral_value())
    return CheckoutResponse(
        order_id=order.id,
        merchant_transaction_id=mtid,
        redirect_url=redirect_url,
        provider=order.gateway_code or "mock",
        amount_minor=amount_minor,
        currency=order.currency,
        checkout=checkout_payload,
    )


@payments_router.get("/{mtid}/status", response_model=PaymentStatusResponse)
def payment_status(
    mtid: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    order = PaymentService(db).get_status(user.id, mtid)
    return PaymentStatusResponse(
        order_id=order.id,
        merchant_transaction_id=mtid,
        order_status=order.status,
        total_amount=order.total_amount,
        currency=order.currency,
        updated_at=order.updated_at,
    )


@payments_router.get("/{mtid}/order", response_model=OrderRead)
def order_for_payment(
    mtid: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Detailed order view keyed off the merchant transaction id — handy for
    the return page (it has the txn id from PhonePe's redirect, not the order
    id)."""
    order = PaymentService(db).get_status(user.id, mtid)
    return order


@payments_router.post("/razorpay/verify", response_model=PaymentStatusResponse)
def verify_razorpay_payment(
    payload: RazorpayVerifyRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Settle a Razorpay Standard Checkout payment from the browser callback.

    checkout.js calls this with the (order_id, payment_id, signature) triplet
    from its success handler. The service re-verifies the signature AND
    re-fetches the payment from Razorpay before any state moves — the browser
    is never trusted to settle an order. The response is the same shape the
    return page polls at GET /payments/{mtid}/status, so the SPA can treat
    both interchangeably.
    """
    order = PaymentService(db).verify_and_settle_razorpay(user, payload)
    return PaymentStatusResponse(
        order_id=order.id,
        merchant_transaction_id=payload.merchant_transaction_id,
        order_status=order.status,
        total_amount=order.total_amount,
        currency=order.currency,
        updated_at=order.updated_at,
    )


@payments_router.post("/webhook/phonepe", status_code=status.HTTP_200_OK)
def phonepe_webhook(
    body: bytes = Depends(get_raw_body),
    x_verify: str | None = Header(default=None, alias="X-VERIFY"),
    db: Session = Depends(get_db),
):
    # Sync def: runs in the threadpool so the blocking settlement chain (DB +
    # SMTP + SMS + carrier push, each up to 15s) never stalls the event loop.
    PaymentService(db).handle_webhook(body, x_verify, gateway_code="phonepe")
    return {"ok": True}


@payments_router.post("/webhook/mock", status_code=status.HTTP_200_OK)
def mock_webhook(
    payload: MockWebhookRequest,
    db: Session = Depends(get_db),
):
    # Hard-disable in production regardless of any DB state — a misconfigured
    # prod deployment must never expose this unsigned settle path.
    if (settings.ENVIRONMENT or "").lower() == "production":
        raise ForbiddenError("Mock webhook is disabled in production.")
    if payload.action not in {"approve", "decline"}:
        raise ValidationError("action must be 'approve' or 'decline'.")
    # Whether this settle is allowed is decided per-order from the order's own
    # gateway_code inside mark_mock_decision — NOT from the deprecated
    # PaymentGatewayConfig "active provider" row, which the checkout factory no
    # longer consults. Reading that stale row caused a split-brain: a real
    # order could be mock-settled, or a genuine mock order blocked.
    order = PaymentService(db).mark_mock_decision(
        payload.merchant_transaction_id, payload.action
    )
    return {"ok": True, "order_id": order.id, "order_status": order.status.value}


@payments_router.post("/webhook/{gateway_code}", status_code=status.HTTP_200_OK)
def generic_webhook(
    gateway_code: str,
    request: Request,
    body: bytes = Depends(get_raw_body),
    db: Session = Depends(get_db),
):
    """Generic signed webhook endpoint for any implemented gateway.

    Signature header selection per provider:
    - phonepe: X-VERIFY (use the dedicated /webhook/phonepe route instead)
    - razorpay: X-Razorpay-Signature
    - stripe: Stripe-Signature
    - paystack: x-paystack-signature
    - flutterwave: verif-hash
    - paypal: not used (verify always returns False; status-polling is used)
    - All others: no standard header — pass None and let verify decide.
    """
    # Validate that the gateway code is known and enabled+implemented.
    from sqlalchemy import select as _select
    pm_row = db.execute(
        _select(PaymentMethod).where(PaymentMethod.gateway_code == gateway_code)
    ).scalar_one_or_none()
    if pm_row is None:
        raise NotFoundError(f"Payment gateway '{gateway_code}' not found.")
    gw_def = get_gateway(gateway_code)
    if gw_def is None or not gw_def.implemented:
        raise ForbiddenError(
            f"Webhook not available for gateway '{gateway_code}'."
        )
    if not pm_row.enabled:
        raise ForbiddenError(
            f"Gateway '{gateway_code}' is not enabled."
        )

    # Pick the most relevant signature header for each gateway.
    signature: str | None = None
    if gateway_code == "razorpay":
        signature = request.headers.get("X-Razorpay-Signature")
    elif gateway_code == "stripe":
        signature = request.headers.get("Stripe-Signature")
    elif gateway_code == "paystack":
        signature = request.headers.get("x-paystack-signature")
    elif gateway_code == "flutterwave":
        signature = request.headers.get("verif-hash")
    elif gateway_code == "phonepe":
        signature = request.headers.get("X-VERIFY")

    PaymentService(db).handle_webhook(body, signature, gateway_code=gateway_code)
    return {"ok": True}


@payments_router.post(
    "/admin/reconcile-pending",
    response_model=ReconcilePendingResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(reconcile_auth)],
    summary="Reconcile stale PENDING gateway orders against the provider",
)
def reconcile_pending(
    payload: ReconcilePendingRequest = ReconcilePendingRequest(),
    db: Session = Depends(get_db),
) -> ReconcilePendingResponse:
    """Poll the gateway for every PENDING order older than `older_than_minutes`
    and settle any whose status has moved.  Capped at `limit` orders per call.

    Requires the ``payments.manage`` permission.
    """
    result = PaymentService(db).reconcile_pending(
        older_than_minutes=payload.older_than_minutes,
        limit=payload.limit,
    )
    return ReconcilePendingResponse(**result)
