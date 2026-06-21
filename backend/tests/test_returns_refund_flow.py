"""End-to-end tests for the returns inspect → refund → notify workflow.

Covers the senior-dev workflow:
  1. A received item is inspected against the stated claim.
  2. A PASSING inspection unlocks the refund, issued through the ORIGINAL
     payment method (gateway leg refunded; COD falls back to manual).
  3. The customer is notified with an estimated timeline.
  4. A FAILING inspection rejects the return (notified, no money moves).

Also guards: refund is blocked until a passing inspection exists; partial
returns mark the original leg PARTIALLY_REFUNDED; the refund is audited in
payment_events.

The payments factory is patched to a deterministic refund-capable provider and
the email sender is patched (no real SMTP). Uses the shared dev DB and cleans
up after itself, FK-safe.

Run inside the backend container:
    docker compose exec -T backend pytest tests/test_returns_refund_flow.py -v
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select, text

from app.core.exceptions import ConflictError
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.integrations.payments.base import PaymentStatus, RefundResult
from app.models.order import Order, OrderItem, OrderStatus
from app.models.order_payment import OrderPayment, PaymentTxnStatus
from app.models.payment_event import PaymentEvent, PaymentEventType
from app.models.product import Product
from app.models.return_request import ReturnStatus
from app.models.user import User
from app.services.return_service import ReturnService


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


def _build_delivered_order(
    db,
    *,
    prepaid: bool = True,
    total: Decimal = Decimal("200.00"),
    qty: int = 2,
    unit_price: Decimal = Decimal("100.00"),
):
    """Create a DELIVERED order (with a paid gateway leg, or a COD leg) plus its
    user + product. Returns (user, product, order, item)."""
    user = User(
        email=f"ret-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.flush()
    prod = Product(
        sku=f"SKU-RET-{_uid()}", name=f"RetProd-{_uid()}",
        price=unit_price, cost=Decimal("40.00"), stock=100,
    )
    db.add(prod)
    db.flush()

    order = Order(
        user_id=user.id,
        status=OrderStatus.DELIVERED,
        subtotal=total, tax_amount=Decimal("0.00"), discount_amount=Decimal("0.00"),
        shipping_amount=Decimal("0.00"), total_amount=total, currency="INR",
        payment_method="prepaid" if prepaid else "cod",
        gateway_code="mock" if prepaid else None,
        payment_intent_id=f"ORD{_uid().upper()}",
        payment_provider_ref=("T" + _uid().upper()) if prepaid else None,
        shipping_address="42 Test Lane, Bengaluru",
        shipping_pincode="560001",
        delivered_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    order.items.append(
        OrderItem(product_id=prod.id, quantity=qty, unit_price=unit_price,
                  unit_cost=Decimal("40.00"))
    )
    if prepaid:
        order.payments.append(OrderPayment(
            payment_method="prepaid", payment_status=PaymentTxnStatus.PAID,
            amount=total, currency="INR", gateway="mock",
            gateway_payment_id="TPAY" + _uid().upper(),
            transaction_reference=order.payment_intent_id,
        ))
    else:
        order.payments.append(OrderPayment(
            payment_method="cod", payment_status=PaymentTxnStatus.PAID,
            amount=total, currency="INR",
        ))
    db.add(order)
    db.flush()
    return user, prod, order, order.items[0]


def _cleanup(user_id: int, product_id: int, order_id: int) -> None:
    with SessionLocal() as s:
        s.execute(text("DELETE FROM payment_events WHERE order_id = :o"), {"o": order_id})
        s.execute(text(
            "DELETE FROM return_items WHERE return_id IN "
            "(SELECT id FROM returns WHERE order_id = :o)"), {"o": order_id})
        s.execute(text("DELETE FROM returns WHERE order_id = :o"), {"o": order_id})
        for tbl in ("order_items", "order_payments", "order_addresses", "shipments"):
            s.execute(text(f"DELETE FROM {tbl} WHERE order_id = :o"), {"o": order_id})
        s.execute(text("DELETE FROM orders WHERE id = :o"), {"o": order_id})
        s.execute(text("DELETE FROM products WHERE id = :p"), {"p": product_id})
        s.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
        s.commit()


def _prepare_received_return(db, svc, user, order, item, *, qty_return: int):
    """create → approve → mark_received, returning the ReturnRequest id."""
    req = svc.create_return(
        user_id=user.id, order_id=order.id,
        items=[(item.id, qty_return)], reason="arrived_damaged",
        customer_notes="Screen cracked on arrival",
    )
    svc.approve(req.id)
    svc.mark_received(req.id)
    db.commit()
    return req.id


# ---------------------------------------------------------------------------


def test_inspect_pass_then_refund_to_original_gateway_and_notify() -> None:
    ids = None
    db = SessionLocal()
    try:
        user, prod, order, item = _build_delivered_order(
            db, total=Decimal("200.00"), qty=2, unit_price=Decimal("100.00"))
        ids = (user.id, prod.id, order.id)
        leg_id = order.payments[0].id
        svc = ReturnService(db)
        rid = _prepare_received_return(db, svc, user, order, item, qty_return=2)

        fake = _FakeRefundProvider()
        with patch("app.integrations.payments.factory.get_provider_for_order",
                   return_value=fake), \
             patch("app.services.notifications.service.send_email") as send_email:
            # Inspect — passes; stays RECEIVED, ready to refund.
            svc.inspect(rid, passed=True, inspection_notes="Damage matches claim.")
            db.commit()
            mid = db.get(type(order), order.id)  # noqa: F841
            from app.models.return_request import ReturnRequest
            r = db.get(ReturnRequest, rid)
            assert r.status == ReturnStatus.RECEIVED
            assert r.inspection_passed is True

            # Issue the refund through the original method.
            svc.issue_refund(rid)
            db.commit()

        r = db.get(ReturnRequest, rid)
        assert r.status == ReturnStatus.REFUNDED
        assert r.refunded_at is not None
        assert r.refund_method == "mock"                       # original gateway
        assert r.refund_reference.startswith("GWREFUND-")      # provider's id
        assert r.refund_amount == Decimal("200.00")

        # Provider.refund called with the right money + original txn id.
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call.amount_minor == 20000
        assert call.currency == "INR"
        assert call.original_transaction_id is not None

        # Original leg now fully REFUNDED.
        leg = db.get(OrderPayment, leg_id)
        assert leg.payment_status == PaymentTxnStatus.REFUNDED

        # Refund audited.
        n_events = db.execute(
            select(PaymentEvent).where(
                PaymentEvent.order_id == order.id,
                PaymentEvent.event_type == PaymentEventType.REFUND_ATTEMPT,
            )
        ).scalars().all()
        assert len(n_events) == 1
        assert n_events[0].provider_ref == r.refund_reference

        # Customer notified.
        assert send_email.call_count == 1
        assert send_email.call_args.kwargs["to"] == user.email
    finally:
        db.rollback()
        db.close()
        if ids:
            _cleanup(*ids)


def test_inspect_fail_rejects_return_and_notifies_without_refund() -> None:
    ids = None
    db = SessionLocal()
    try:
        user, prod, order, item = _build_delivered_order(db)
        ids = (user.id, prod.id, order.id)
        leg_id = order.payments[0].id
        svc = ReturnService(db)
        rid = _prepare_received_return(db, svc, user, order, item, qty_return=2)

        with patch("app.services.notifications.service.send_email") as send_email:
            svc.inspect(rid, passed=False,
                        inspection_notes="No damage found; item used.")
            db.commit()

        from app.models.return_request import ReturnRequest
        r = db.get(ReturnRequest, rid)
        assert r.status == ReturnStatus.REJECTED
        assert r.rejected_at is not None
        assert r.inspection_passed is False
        assert r.refund_method is None
        # Original leg untouched (still PAID).
        assert db.get(OrderPayment, leg_id).payment_status == PaymentTxnStatus.PAID
        # Rejection emailed.
        assert send_email.call_count == 1

        # And a refund cannot be forced after rejection.
        with pytest.raises(ConflictError):
            svc.issue_refund(rid)
    finally:
        db.rollback()
        db.close()
        if ids:
            _cleanup(*ids)


def test_refund_blocked_until_inspection_passes() -> None:
    ids = None
    db = SessionLocal()
    try:
        user, prod, order, item = _build_delivered_order(db)
        ids = (user.id, prod.id, order.id)
        svc = ReturnService(db)
        rid = _prepare_received_return(db, svc, user, order, item, qty_return=2)

        # No inspection yet → refund must be refused.
        with pytest.raises(ConflictError):
            svc.issue_refund(rid)
    finally:
        db.rollback()
        db.close()
        if ids:
            _cleanup(*ids)


def test_partial_return_marks_leg_partially_refunded() -> None:
    ids = None
    db = SessionLocal()
    try:
        user, prod, order, item = _build_delivered_order(
            db, total=Decimal("200.00"), qty=2, unit_price=Decimal("100.00"))
        ids = (user.id, prod.id, order.id)
        leg_id = order.payments[0].id
        svc = ReturnService(db)
        rid = _prepare_received_return(db, svc, user, order, item, qty_return=1)

        fake = _FakeRefundProvider()
        with patch("app.integrations.payments.factory.get_provider_for_order",
                   return_value=fake), \
             patch("app.services.notifications.service.send_email"):
            svc.inspect(rid, passed=True)
            svc.issue_refund(rid)
            db.commit()

        from app.models.return_request import ReturnRequest
        r = db.get(ReturnRequest, rid)
        assert r.status == ReturnStatus.REFUNDED
        assert r.refund_amount == Decimal("100.00")       # 1 × 100, partial
        assert fake.calls[0].amount_minor == 10000
        # Leg only partially refunded (100 of 200).
        assert db.get(OrderPayment, leg_id).payment_status == \
            PaymentTxnStatus.PARTIALLY_REFUNDED
    finally:
        db.rollback()
        db.close()
        if ids:
            _cleanup(*ids)


def test_cod_order_refund_falls_back_to_manual() -> None:
    ids = None
    db = SessionLocal()
    try:
        user, prod, order, item = _build_delivered_order(db, prepaid=False)
        ids = (user.id, prod.id, order.id)
        leg_id = order.payments[0].id
        svc = ReturnService(db)
        rid = _prepare_received_return(db, svc, user, order, item, qty_return=2)

        with patch("app.services.notifications.service.send_email") as send_email:
            svc.inspect(rid, passed=True)
            svc.issue_refund(rid)
            db.commit()

        from app.models.return_request import ReturnRequest
        r = db.get(ReturnRequest, rid)
        assert r.status == ReturnStatus.REFUNDED
        assert r.refund_method == "manual"                  # no gateway to reverse
        assert r.refund_reference.startswith("RFND")
        # COD leg is left as-is (cash refund handled offline).
        assert db.get(OrderPayment, leg_id).payment_status == PaymentTxnStatus.PAID
        assert send_email.call_count == 1
    finally:
        db.rollback()
        db.close()
        if ids:
            _cleanup(*ids)
