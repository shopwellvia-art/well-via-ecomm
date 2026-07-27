"""Tests for gateway-backed order refunds + customer self-service cancellation.

Covers:
  1. A customer cancels their own PENDING order — stock restored, payment leg
     cancelled, no refund attempted (nothing was captured).
  2. A SHIPPED order cannot be cancelled by the customer.
  3. A customer cannot cancel (or even see) someone else's order — 404 shape.
  4. Admin refund attempts the gateway first and falls back to MANUAL when the
     provider reports the refund failed (bookkeeping still runs).
  5. Admin refund via a refund-capable provider records a GATEWAY refund with
     the provider's refund id.
  6. `force_manual` skips the gateway call entirely.
  7. Customer cancellation of a PAID-but-unshipped prepaid order attempts the
     refund; on failure the order still cancels, marked for manual refund.

The payments factory is patched to deterministic providers and the email
sender is patched (no real SMTP). Uses the shared dev DB and cleans up after
itself, FK-safe — same conventions as test_returns_refund_flow.py.

Run inside the backend container:
    docker compose exec -T backend pytest tests/test_refund_cancel.py -v
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import select, text

from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.integrations.payments.base import PaymentStatus, RefundResult
from app.models.order import Order, OrderItem, OrderStatus
from app.models.order_payment import OrderPayment, PaymentTxnStatus
from app.models.payment_event import PaymentEvent, PaymentEventType
from app.models.product import Product
from app.models.user import User
from app.services.order_service import OrderService

_SEND_EMAIL = "app.services.notifications.service.send_email"
_GET_PROVIDER = "app.integrations.payments.factory.get_provider_for_order"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


class _FakeRefundProvider:
    """Refund-capable stand-in for a real gateway (matches SupportsRefund)."""

    name = "mock"

    def __init__(self) -> None:
        self.calls: list = []

    def refund(self, req):  # noqa: ANN001
        self.calls.append(req)
        return RefundResult(
            refund_id=f"GWREFUND-{req.refund_reference}",
            status=PaymentStatus.SUCCESS,
            raw={"provider": "mock", "amount_minor": req.amount_minor},
        )


class _FailingRefundProvider:
    """Provider whose refund call is accepted but reported FAILED by the
    gateway — callers must fall back to a manual refund."""

    name = "mock"

    def __init__(self) -> None:
        self.calls: list = []

    def refund(self, req):  # noqa: ANN001
        self.calls.append(req)
        return RefundResult(
            refund_id=req.refund_reference,
            status=PaymentStatus.FAILED,
            raw={"provider": "mock", "error": "REFUND_DECLINED"},
        )


def _build_order(
    db,
    *,
    status: OrderStatus,
    total: Decimal = Decimal("200.00"),
    qty: int = 2,
    unit_price: Decimal = Decimal("100.00"),
    stock: int = 98,
    leg_status: PaymentTxnStatus = PaymentTxnStatus.PENDING,
    tracking_number: str | None = None,
):
    """Create an order in `status` with one prepaid gateway leg, plus its user
    + product. Stock defaults to 98 = "100 minus the 2 units this order holds"
    so a restock is observable. Returns (user, product, order)."""
    user = User(
        email=f"cancel-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.flush()
    prod = Product(
        sku=f"SKU-CXL-{_uid()}", name=f"CxlProd-{_uid()}",
        price=unit_price, cost=Decimal("40.00"), stock=stock,
    )
    db.add(prod)
    db.flush()

    order = Order(
        user_id=user.id,
        status=status,
        subtotal=total, tax_amount=Decimal("0.00"), discount_amount=Decimal("0.00"),
        shipping_amount=Decimal("0.00"), total_amount=total, currency="INR",
        payment_method="prepaid",
        gateway_code="mock",
        payment_intent_id=f"ORD{_uid().upper()}",
        payment_provider_ref="T" + _uid().upper(),
        shipping_address="42 Test Lane, Bengaluru",
        shipping_pincode="560001",
        tracking_number=tracking_number,
    )
    order.items.append(
        OrderItem(product_id=prod.id, quantity=qty, unit_price=unit_price,
                  unit_cost=Decimal("40.00"))
    )
    order.payments.append(OrderPayment(
        payment_method="prepaid", payment_status=leg_status,
        amount=total, currency="INR", gateway="mock",
        gateway_payment_id=(
            "TPAY" + _uid().upper()
            if leg_status == PaymentTxnStatus.PAID else None
        ),
        transaction_reference=order.payment_intent_id,
    ))
    db.add(order)
    db.flush()
    return user, prod, order


def _cleanup(user_ids, product_id: int, order_id: int) -> None:
    if isinstance(user_ids, int):
        user_ids = [user_ids]
    with SessionLocal() as s:
        s.execute(text("DELETE FROM payment_events WHERE order_id = :o"), {"o": order_id})
        for tbl in ("order_items", "order_payments", "order_addresses", "shipments"):
            s.execute(text(f"DELETE FROM {tbl} WHERE order_id = :o"), {"o": order_id})
        s.execute(text("DELETE FROM orders WHERE id = :o"), {"o": order_id})
        s.execute(text("DELETE FROM products WHERE id = :p"), {"p": product_id})
        for uid in user_ids:
            s.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})
        s.commit()


def _refund_events(db, order_id: int) -> list[PaymentEvent]:
    return list(db.execute(
        select(PaymentEvent).where(
            PaymentEvent.order_id == order_id,
            PaymentEvent.event_type == PaymentEventType.REFUND_ATTEMPT,
        )
    ).scalars().all())


# ---------------------------------------------------------------------------
# Customer cancellation
# ---------------------------------------------------------------------------


def test_customer_cancels_pending_order_restocks() -> None:
    ids = None
    db = SessionLocal()
    try:
        user, prod, order = _build_order(db, status=OrderStatus.PENDING)
        ids = (user.id, prod.id, order.id)
        leg_id = order.payments[0].id

        with patch(_SEND_EMAIL):
            OrderService(db).cancel_for_customer(user.id, order.id)
            db.commit()

        order = db.get(Order, order.id)
        assert order.status == OrderStatus.CANCELLED
        assert order.cancelled_at is not None
        assert order.refund_reason == "Cancelled by customer"
        # Units back on the shelf: 98 + the 2 this order held.
        assert db.get(Product, prod.id).stock == 100
        # Uncaptured leg is voided, not refunded.
        assert db.get(OrderPayment, leg_id).payment_status == PaymentTxnStatus.CANCELLED
        # Nothing was captured, so no refund attempt is recorded.
        assert _refund_events(db, order.id) == []
    finally:
        db.rollback()
        db.close()
        if ids:
            _cleanup(*ids)


def test_customer_cannot_cancel_shipped_order() -> None:
    ids = None
    db = SessionLocal()
    try:
        user, prod, order = _build_order(
            db, status=OrderStatus.SHIPPED,
            leg_status=PaymentTxnStatus.PAID, tracking_number="AWB123456",
        )
        ids = (user.id, prod.id, order.id)
        # Commit the fixture: the rollback below (after the expected refusal)
        # must clear only the service's partial work, not the order itself.
        db.commit()

        with pytest.raises(ConflictError):
            OrderService(db).cancel_for_customer(user.id, order.id)
        db.rollback()

        order = db.get(Order, order.id)
        assert order.status == OrderStatus.SHIPPED
        assert db.get(Product, prod.id).stock == 98  # untouched
    finally:
        db.rollback()
        db.close()
        if ids:
            _cleanup(*ids)


def test_customer_cannot_cancel_someone_elses_order() -> None:
    ids = None
    db = SessionLocal()
    try:
        user, prod, order = _build_order(db, status=OrderStatus.PENDING)
        stranger = User(
            email=f"stranger-{_uid()}@example.com",
            hashed_password=hash_password("TestPass123!"),
            is_active=True,
            is_admin=False,
        )
        db.add(stranger)
        db.flush()
        ids = ([user.id, stranger.id], prod.id, order.id)
        # Commit the fixture: the rollback below (after the expected 404)
        # must clear only the service's partial work, not the order itself.
        db.commit()

        # 404 shape — never reveal that the order exists.
        with pytest.raises(NotFoundError):
            OrderService(db).cancel_for_customer(stranger.id, order.id)
        db.rollback()

        assert db.get(Order, order.id).status == OrderStatus.PENDING
    finally:
        db.rollback()
        db.close()
        if ids:
            _cleanup(*ids)


def test_customer_cancel_paid_unshipped_refunds_or_marks_manual() -> None:
    """PAID + not yet with a carrier → cancellable; a failed gateway refund
    still cancels the order but flags the refund for manual action."""
    ids = None
    db = SessionLocal()
    try:
        user, prod, order = _build_order(
            db, status=OrderStatus.PAID, leg_status=PaymentTxnStatus.PAID,
        )
        ids = (user.id, prod.id, order.id)
        leg_id = order.payments[0].id

        failing = _FailingRefundProvider()
        with patch(_GET_PROVIDER, return_value=failing), patch(_SEND_EMAIL):
            OrderService(db).cancel_for_customer(
                user.id, order.id, reason="Ordered by mistake"
            )
            db.commit()

        order = db.get(Order, order.id)
        assert order.status == OrderStatus.CANCELLED
        assert order.refund_reason == "Ordered by mistake"
        assert len(failing.calls) == 1
        # Captured leg reversed on the books even though the gateway declined.
        assert db.get(OrderPayment, leg_id).payment_status == PaymentTxnStatus.REFUNDED
        assert db.get(Product, prod.id).stock == 100
        # Manual refund flagged where admins look.
        events = _refund_events(db, order.id)
        assert len(events) == 1
        assert "via manual" in events[0].message
        assert "MANUAL" in (order.internal_notes or "")
    finally:
        db.rollback()
        db.close()
        if ids:
            _cleanup(*ids)


# ---------------------------------------------------------------------------
# Admin refunds
# ---------------------------------------------------------------------------


def test_admin_refund_falls_back_to_manual_when_gateway_fails() -> None:
    ids = None
    db = SessionLocal()
    try:
        user, prod, order = _build_order(
            db, status=OrderStatus.PAID, leg_status=PaymentTxnStatus.PAID,
        )
        ids = (user.id, prod.id, order.id)
        leg_id = order.payments[0].id

        failing = _FailingRefundProvider()
        with patch(_GET_PROVIDER, return_value=failing), patch(_SEND_EMAIL):
            OrderService(db).refund(order.id, reason="Damaged in warehouse")
            db.commit()

        order = db.get(Order, order.id)
        assert order.status == OrderStatus.REFUNDED
        assert order.refunded_at is not None
        assert len(failing.calls) == 1
        # Bookkeeping fallback still ran in full.
        assert db.get(OrderPayment, leg_id).payment_status == PaymentTxnStatus.REFUNDED
        assert db.get(Product, prod.id).stock == 100
        # Recorded as MANUAL so the operator knows money still has to move.
        events = _refund_events(db, order.id)
        assert len(events) == 1
        assert "via manual" in events[0].message
        assert events[0].amount_reported_minor == 20000
        assert "NEEDS MANUAL PROCESSING" in (order.internal_notes or "")
    finally:
        db.rollback()
        db.close()
        if ids:
            _cleanup(*ids)


def test_admin_refund_via_gateway_succeeds() -> None:
    ids = None
    db = SessionLocal()
    try:
        user, prod, order = _build_order(
            db, status=OrderStatus.PAID, leg_status=PaymentTxnStatus.PAID,
        )
        ids = (user.id, prod.id, order.id)
        leg_id = order.payments[0].id

        fake = _FakeRefundProvider()
        with patch(_GET_PROVIDER, return_value=fake), patch(_SEND_EMAIL):
            OrderService(db).refund(order.id, reason="Customer complaint")
            db.commit()

        order = db.get(Order, order.id)
        assert order.status == OrderStatus.REFUNDED
        assert db.get(OrderPayment, leg_id).payment_status == PaymentTxnStatus.REFUNDED
        assert db.get(Product, prod.id).stock == 100

        # Provider called with the right money + original txn id.
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call.amount_minor == 20000
        assert call.currency == "INR"
        assert call.original_transaction_id is not None

        # Recorded as a GATEWAY refund under the provider's refund id.
        events = _refund_events(db, order.id)
        assert len(events) == 1
        assert "via mock" in events[0].message
        assert events[0].provider_ref.startswith("GWREFUND-")
        assert "issued via mock" in (order.internal_notes or "")
    finally:
        db.rollback()
        db.close()
        if ids:
            _cleanup(*ids)


def test_admin_refund_force_manual_skips_gateway() -> None:
    ids = None
    db = SessionLocal()
    try:
        user, prod, order = _build_order(
            db, status=OrderStatus.PAID, leg_status=PaymentTxnStatus.PAID,
        )
        ids = (user.id, prod.id, order.id)

        fake = _FakeRefundProvider()
        with patch(_GET_PROVIDER, return_value=fake), patch(_SEND_EMAIL):
            OrderService(db).refund(
                order.id, reason="Settled by bank transfer", force_manual=True
            )
            db.commit()

        order = db.get(Order, order.id)
        assert order.status == OrderStatus.REFUNDED
        assert fake.calls == []  # gateway never touched
        events = _refund_events(db, order.id)
        assert len(events) == 1
        assert "via manual" in events[0].message
    finally:
        db.rollback()
        db.close()
        if ids:
            _cleanup(*ids)
