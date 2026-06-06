"""Payment + checkout endpoints.

Routes:
  POST /checkout                     create order + initiate payment
  GET  /payments/{mtid}/status       what the return page polls
  POST /payments/webhook/phonepe     S2S callback from PhonePe (signed)
  POST /payments/webhook/mock        local simulator (mock provider only)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.core.exceptions import ForbiddenError, ValidationError
from app.models.user import User
from app.schemas.order import OrderRead
from app.schemas.payment import (
    CheckoutRequest,
    CheckoutResponse,
    MockWebhookRequest,
    PaymentStatusResponse,
)
from app.services.payment_gateway_service import PaymentGatewayService
from app.services.payment_service import PaymentService

checkout_router = APIRouter()
payments_router = APIRouter()


@checkout_router.post(
    "", response_model=CheckoutResponse, status_code=status.HTTP_201_CREATED
)
def start_checkout(
    payload: CheckoutRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = PaymentService(db)
    order, mtid, redirect_url = svc.checkout(user, payload)
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
        provider=svc.provider.name,
        amount_minor=amount_minor,
        currency=order.currency,
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


@payments_router.post("/webhook/phonepe", status_code=status.HTTP_200_OK)
async def phonepe_webhook(
    request: Request,
    x_verify: str | None = Header(default=None, alias="X-VERIFY"),
    db: Session = Depends(get_db),
):
    body = await request.body()
    PaymentService(db).handle_webhook(body, x_verify)
    return {"ok": True}


@payments_router.post("/webhook/mock", status_code=status.HTTP_200_OK)
def mock_webhook(
    payload: MockWebhookRequest,
    db: Session = Depends(get_db),
):
    # Guard so this stays out of production paths. The active provider now
    # lives in the DB (admin-configurable), so read it from there rather than
    # the env var. Additionally, reject in production even if the DB still has
    # the mock provider set — a misconfigured prod deployment must not expose
    # this endpoint.
    if (settings.ENVIRONMENT or "").lower() == "production":
        raise ForbiddenError("Mock webhook is disabled in production.")
    active_provider = (PaymentGatewayService(db).get().provider or "mock").lower()
    if active_provider != "mock":
        raise ForbiddenError("Mock webhook disabled when a real provider is configured.")
    if payload.action not in {"approve", "decline"}:
        raise ValidationError("action must be 'approve' or 'decline'.")
    order = PaymentService(db).mark_mock_decision(
        payload.merchant_transaction_id, payload.action
    )
    return {"ok": True, "order_id": order.id, "order_status": order.status.value}
