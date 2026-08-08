"""Integration tests for payment audit-log (payment_events) and reconciliation.

Covers:
  1. Webhook success writes STATUS_APPLIED event with payment_status="success".
  2. Amount mismatch writes AMOUNT_MISMATCH event and leaves order PENDING.
  3. Invalid signature writes WEBHOOK_SIGNATURE_INVALID event (fresh-session read
     proves the audit row survived the request rollback).
  4. Reconcile settles a stale PENDING order that the gateway has already approved.
  5. Reconcile leaves a genuinely still-pending order in PENDING state.

Run inside the backend container:
    docker compose exec -T backend pytest tests/test_payment_events_and_reconcile.py -v
"""
from __future__ import annotations

import json
import time
import uuid
from decimal import Decimal

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.integrations.payments.base import PaymentStatus
from app.integrations.payments.mock import MockProvider
from app.main import app
from app.models.order import Order, OrderStatus
from app.models.payment_event import PaymentEvent, PaymentEventType
from app.models.product import Product
from app.models.user import User
from app.schemas.address import AddressCreate
from app.schemas.order import OrderItemCreate
from app.schemas.payment import CheckoutRequest
from app.schemas.payment_method import PaymentMethodUpdate
from app.services.payment_method_config_service import PaymentMethodConfigService
from app.services.payment_service import PaymentService


# ---------------------------------------------------------------------------
# Local helpers  (self-contained — do NOT import across test files)
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _make_user(db: Session, *, is_admin: bool = False) -> User:
    u = User(
        email=f"perf-test-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=is_admin,
    )
    db.add(u)
    db.flush()
    return u


def _make_product(
    db: Session, *, price: Decimal = Decimal("100.00"), stock: int = 50
) -> Product:
    uid = _uid()
    p = Product(
        sku=f"SKU-PERF-{uid}",
        name=f"PerfTestProd-{uid}",
        price=price,
        stock=stock,
    )
    db.add(p)
    db.flush()
    return p


def _addr_create() -> AddressCreate:
    return AddressCreate(
        full_name="Perf Test Buyer",
        phone="9876543210",
        line1="5 Audit Lane",
        city="Bangalore",
        state="Karnataka",
        country="IN",
        pincode="560001",
        label="home",
    )


def _enable_mock(db: Session) -> None:
    """Enable the mock gateway so checkout with gateway_code='mock' works."""
    PaymentMethodConfigService(db).update("mock", PaymentMethodUpdate(enabled=True))


def _disable_mock(db: Session) -> None:
    """Restore mock to its seeded (disabled) state."""
    PaymentMethodConfigService(db).update("mock", PaymentMethodUpdate(enabled=False))


def _reset_razorpay(db: Session) -> None:
    """Reset razorpay to its post-migration state: disabled, no credentials."""
    PaymentMethodConfigService(db).update(
        "razorpay",
        PaymentMethodUpdate(
            enabled=False,
            credentials={"key_id": "", "key_secret": "", "webhook_secret": ""},
        ),
    )


def _cleanup(
    user_ids: list[int],
    product_ids: list[int],
    order_ids: list[int],
) -> None:
    """Delete test rows via a fresh session.  Deletion order honours FK constraints.

    payment_events rows must be deleted BEFORE orders because order_id is
    ON DELETE SET NULL — deleting the order would null out order_id, making
    a subsequent 'by order_id' delete a no-op.
    """
    with SessionLocal() as s:
        if order_ids:
            ids_tuple = tuple(order_ids)
            s.execute(
                text("DELETE FROM payment_events WHERE order_id IN :ids"),
                {"ids": ids_tuple},
            )
            s.execute(
                text("DELETE FROM coupon_usages WHERE order_id IN :ids"),
                {"ids": ids_tuple},
            )
            s.execute(
                text("DELETE FROM order_items WHERE order_id IN :ids"),
                {"ids": ids_tuple},
            )
            s.execute(
                text("DELETE FROM orders WHERE id IN :ids"),
                {"ids": ids_tuple},
            )
        if product_ids:
            s.execute(
                text("DELETE FROM products WHERE id IN :ids"),
                {"ids": tuple(product_ids)},
            )
        if user_ids:
            s.execute(
                text("DELETE FROM addresses WHERE user_id IN :ids"),
                {"ids": tuple(user_ids)},
            )
            s.execute(
                text("DELETE FROM points_transactions WHERE user_id IN :ids"),
                {"ids": tuple(user_ids)},
            )
            s.execute(
                text("DELETE FROM customers WHERE user_id IN :ids"),
                {"ids": tuple(user_ids)},
            )
            s.execute(
                text("DELETE FROM users WHERE id IN :ids"),
                {"ids": tuple(user_ids)},
            )
        s.commit()


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


# ---------------------------------------------------------------------------
# Test 1: Webhook success writes STATUS_APPLIED event
# ---------------------------------------------------------------------------

class TestWebhookSuccessWritesStatusAppliedEvent:
    """1. mark_mock_decision('approve') drives _apply_status(SUCCESS) which
    must write a STATUS_APPLIED row in payment_events with
    payment_status='success' for the order."""

    def test_webhook_success_writes_status_applied_event(self) -> None:
        """Enable mock, checkout, approve — then assert a STATUS_APPLIED row
        with payment_status='success' exists in a FRESH session (confirms the
        audit writer's own commit landed independently)."""
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtid: str | None = None
        r = _redis_client()

        db = SessionLocal()
        try:
            _enable_mock(db)
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db, price=Decimal("150.00"), stock=20)
            product_ids.append(prod.id)
            db.commit()

            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address=_addr_create(),
                gateway_code="mock",
                payment_method="prepaid",
            )
            svc = PaymentService(db)
            order, mtid, _redirect, _checkout = svc.checkout(user, req)
            order_ids.append(order.id)
            order_id = order.id

            assert order.status == OrderStatus.PENDING

            # Approve via mock — triggers _apply_status(SUCCESS) which writes
            # STATUS_APPLIED via record_payment_event (independent session).
            svc.mark_mock_decision(mtid, "approve")

            # Verify the order itself settled.
            db.refresh(order)
            assert order.status == OrderStatus.PAID, (
                f"Expected PAID after approve, got {order.status}"
            )

            # Query payment_events in a FRESH session — record_payment_event
            # uses its own SessionLocal so its commit is independent of ours.
            with SessionLocal() as fresh_db:
                row = fresh_db.execute(
                    select(PaymentEvent).where(
                        PaymentEvent.order_id == order_id,
                        PaymentEvent.event_type == PaymentEventType.STATUS_APPLIED,
                    )
                ).scalar_one_or_none()

            assert row is not None, (
                "A STATUS_APPLIED payment_event row must exist after mock approve"
            )
            assert row.payment_status == PaymentStatus.SUCCESS.value, (
                f"Expected payment_status='success', got {row.payment_status!r}"
            )

        finally:
            db.rollback()
            _disable_mock(db)
            _cleanup(user_ids, product_ids, order_ids)
            if mtid:
                r.delete(f"payment:mock:{mtid}")
            if user_ids:
                r.delete(f"cart:{user_ids[0]}")
            db.close()


# ---------------------------------------------------------------------------
# Test 2: Amount mismatch writes AMOUNT_MISMATCH event and leaves PENDING
# ---------------------------------------------------------------------------

class TestAmountMismatchWritesEventAndLeavesPending:
    """2. _apply_status called with wrong gateway_amount_minor must write an
    AMOUNT_MISMATCH event with the correct reported/expected values and must
    NOT transition the order out of PENDING."""

    def test_amount_mismatch_writes_event_and_leaves_pending(self) -> None:
        """Checkout via mock; call _apply_status with expected+100 paise; assert
        the order stays PENDING and an AMOUNT_MISMATCH row exists with
        amount_reported_minor and amount_expected_minor populated."""
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtid: str | None = None
        r = _redis_client()

        db = SessionLocal()
        try:
            _enable_mock(db)
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db, price=Decimal("200.00"), stock=30)
            product_ids.append(prod.id)
            db.commit()

            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address=_addr_create(),
                gateway_code="mock",
                payment_method="prepaid",
            )
            svc = PaymentService(db)
            order, mtid, _redirect, _checkout = svc.checkout(user, req)
            order_ids.append(order.id)
            order_id = order.id

            assert order.status == OrderStatus.PENDING

            # Compute what the guard expects and supply a deliberately wrong value.
            expected_minor = int(
                (Decimal(str(order.total_amount)) * 100).to_integral_value()
            )
            wrong_minor = expected_minor + 100  # 100 paise too many

            # Call _apply_status directly — the amount guard must fire and
            # write the AMOUNT_MISMATCH event then return early.
            svc._apply_status(
                order,
                PaymentStatus.SUCCESS,
                gateway_amount_minor=wrong_minor,
            )
            db.refresh(order)

            assert order.status == OrderStatus.PENDING, (
                f"Order must remain PENDING on amount mismatch, got {order.status}"
            )
            assert order.paid_at is None, (
                "paid_at must not be set when amount guard fires"
            )

            # Verify the AMOUNT_MISMATCH event in a fresh session.
            with SessionLocal() as fresh_db:
                row = fresh_db.execute(
                    select(PaymentEvent).where(
                        PaymentEvent.order_id == order_id,
                        PaymentEvent.event_type == PaymentEventType.AMOUNT_MISMATCH,
                    )
                ).scalar_one_or_none()

            assert row is not None, (
                "An AMOUNT_MISMATCH payment_event row must exist after the guard fires"
            )
            assert row.amount_reported_minor == wrong_minor, (
                f"amount_reported_minor should be {wrong_minor}, got {row.amount_reported_minor}"
            )
            assert row.amount_expected_minor == expected_minor, (
                f"amount_expected_minor should be {expected_minor}, "
                f"got {row.amount_expected_minor}"
            )

        finally:
            db.rollback()
            _disable_mock(db)
            _cleanup(user_ids, product_ids, order_ids)
            if mtid:
                r.delete(f"payment:mock:{mtid}")
            db.close()


# ---------------------------------------------------------------------------
# Test 3: Invalid razorpay signature records WEBHOOK_SIGNATURE_INVALID event
# ---------------------------------------------------------------------------

class TestInvalidSignatureRecordsEvent:
    """3. POST /api/v1/payments/webhook/razorpay with bad X-Razorpay-Signature
    must return 403 AND write a WEBHOOK_SIGNATURE_INVALID audit row that
    survives the request-level rollback (audit writer uses its own session)."""

    def test_invalid_signature_records_event_after_403(self) -> None:
        """Enable razorpay with test credentials; POST with bad signature -> 403;
        then query payment_events in a fresh session for a WEBHOOK_SIGNATURE_INVALID
        row with gateway_code='razorpay' and signature_valid=False."""
        # Identify the rows this test creates by id, NOT by created_at:
        # payment_events.created_at is a second-precision DATETIME (micro-
        # seconds truncated) and the DB clock can be skewed from the host
        # clock (container-UTC vs host-IST), so a `created_at >= now()` filter
        # nondeterministically misses same-second writes. Ids are monotonic —
        # everything this test writes has id > the pre-test max.
        from sqlalchemy import func

        with SessionLocal() as s0:
            before_max_id = s0.execute(
                select(func.coalesce(func.max(PaymentEvent.id), 0))
            ).scalar_one()

        db = SessionLocal()
        try:
            svc = PaymentMethodConfigService(db)
            svc.update(
                "razorpay",
                PaymentMethodUpdate(
                    credentials={
                        "key_id": "rzp_test_audit",
                        "key_secret": "sec_audit",
                        "webhook_secret": "whsec_audit_test",
                    }
                ),
            )
            svc.update("razorpay", PaymentMethodUpdate(enabled=True))
        finally:
            # Close this session BEFORE HTTP calls (mirrors test_payment_webhook_lifecycle.py)
            # to avoid a concurrent-session conflict under TestClient.
            db.close()

        client = TestClient(app, raise_server_exceptions=False)
        try:
            resp = client.post(
                "/api/v1/payments/webhook/razorpay",
                content=b"{}",
                headers={"X-Razorpay-Signature": "deadbeef_audit"},
            )
            assert resp.status_code == 403, (
                f"Expected 403 for bad razorpay signature, got {resp.status_code}: {resp.text}"
            )

            # Query in a fresh session — the audit row must have been committed
            # by record_payment_event's own session even though the request
            # transaction rolled back.
            with SessionLocal() as fresh_db:
                row = fresh_db.execute(
                    select(PaymentEvent).where(
                        PaymentEvent.gateway_code == "razorpay",
                        PaymentEvent.event_type == PaymentEventType.WEBHOOK_SIGNATURE_INVALID,
                        PaymentEvent.id > before_max_id,
                    )
                ).scalars().first()

            assert row is not None, (
                "A WEBHOOK_SIGNATURE_INVALID payment_event row must exist after the "
                "403 — proves the audit write survived the request rollback"
            )
            assert row.signature_valid is False, (
                f"signature_valid must be False, got {row.signature_valid!r}"
            )
            assert row.gateway_code == "razorpay", (
                f"gateway_code must be 'razorpay', got {row.gateway_code!r}"
            )

        finally:
            db2 = SessionLocal()
            try:
                _reset_razorpay(db2)
                # Delete payment_events rows created by this test (no order_id;
                # identify by gateway_code + id > pre-test max — created_at is
                # second-precision and clock-skewed, so it can miss rows).
                db2.execute(
                    text(
                        "DELETE FROM payment_events "
                        "WHERE gateway_code = 'razorpay' "
                        "AND event_type = :et "
                        "AND id > :mid"
                    ),
                    {
                        "et": PaymentEventType.WEBHOOK_SIGNATURE_INVALID,
                        "mid": before_max_id,
                    },
                )
                db2.commit()
            finally:
                db2.close()


# ---------------------------------------------------------------------------
# Test 4: Reconcile settles a stale PENDING order
# ---------------------------------------------------------------------------

class TestReconcileSettlesStalePendingOrder:
    """4. reconcile_pending settles a PENDING order whose mock Redis key has
    been flipped to 'success' (without going through _apply_status first)."""

    def test_reconcile_settles_stale_pending_order(self) -> None:
        """Enable mock; checkout (PENDING); flip Redis to success via
        MockProvider.parse_webhook (no _apply_status); call reconcile_pending
        (older_than_minutes=0); assert order is now PAID and the returned dict
        has settled_paid>=1 and checked>=1."""
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtid: str | None = None
        r = _redis_client()

        db = SessionLocal()
        try:
            _enable_mock(db)
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db, price=Decimal("100.00"), stock=50)
            product_ids.append(prod.id)
            db.commit()

            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=2)],
                address=_addr_create(),
                gateway_code="mock",
                payment_method="prepaid",
            )
            svc = PaymentService(db)
            order, mtid, _redirect, _checkout = svc.checkout(user, req)
            order_ids.append(order.id)
            order_id = order.id

            assert order.status == OrderStatus.PENDING, (
                f"Order must be PENDING after checkout, got {order.status}"
            )

            # Sleep 1 s so the order's created_at is strictly older than the
            # reconcile cutoff (older_than_minutes=0 means cutoff=now(); on a
            # fast machine the order and the cutoff can share the same second).
            time.sleep(1)

            # Flip Redis to success via MockProvider.parse_webhook — this is
            # exactly how test_payment_status_polling.py seeds the 'gateway
            # already processed but webhook delayed' scenario.  The existing
            # Redis record's amount_minor is preserved (merge approach).
            mp = MockProvider(
                frontend_url=settings.FRONTEND_URL,
                redis_url=settings.REDIS_URL,
            )
            mp.parse_webhook(
                (
                    '{"merchant_transaction_id":"%s","action":"approve"}' % mtid
                ).encode()
            )

            # Verify Redis key shows success before reconcile.
            raw = r.get(f"payment:mock:{mtid}")
            assert raw is not None, "Redis key must exist after parse_webhook"
            assert json.loads(raw)["status"] == "success", (
                "Redis key should show 'success' before reconcile"
            )

            # Run reconcile — older_than_minutes=0 so our brand-new order qualifies.
            result = PaymentService(db).reconcile_pending(older_than_minutes=0, limit=50)

            # The order must now be PAID.
            db.refresh(order)
            assert order.status == OrderStatus.PAID, (
                f"Order must be PAID after reconcile, got {order.status}"
            )

            assert result["checked"] >= 1, (
                f"reconcile must have checked at least 1 order, got {result['checked']}"
            )
            assert result["settled_paid"] >= 1, (
                f"reconcile must have settled at least 1 order, got {result['settled_paid']}"
            )

        finally:
            db.rollback()
            _disable_mock(db)
            _cleanup(user_ids, product_ids, order_ids)
            if mtid:
                r.delete(f"payment:mock:{mtid}")
            if user_ids:
                r.delete(f"cart:{user_ids[0]}")
            db.close()


# ---------------------------------------------------------------------------
# Test 5: Reconcile leaves genuinely pending order in PENDING state
# ---------------------------------------------------------------------------

class TestReconcileLeavesGenuinelyPendingOrderPending:
    """5. reconcile_pending must not change a PENDING order whose gateway
    still reports pending (Redis key stays 'pending')."""

    def test_reconcile_leaves_genuinely_pending_order_pending(self) -> None:
        """Enable mock; checkout (PENDING); do NOT flip Redis; call
        reconcile_pending(older_than_minutes=0); assert the order is still
        PENDING and still_pending counts it, settled_paid does not."""
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtid: str | None = None
        r = _redis_client()

        db = SessionLocal()
        try:
            _enable_mock(db)
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db, price=Decimal("75.00"), stock=40)
            product_ids.append(prod.id)
            db.commit()

            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address=_addr_create(),
                gateway_code="mock",
                payment_method="prepaid",
            )
            svc = PaymentService(db)
            order, mtid, _redirect, _checkout = svc.checkout(user, req)
            order_ids.append(order.id)
            order_id = order.id

            assert order.status == OrderStatus.PENDING, (
                f"Order must be PENDING after checkout, got {order.status}"
            )

            # Sleep 1 s so the order's created_at is strictly older than the
            # reconcile cutoff (older_than_minutes=0 means cutoff=now()).
            time.sleep(1)

            # Verify Redis key shows pending — we deliberately do NOT call
            # parse_webhook, so the gateway still reports 'pending'.
            raw = r.get(f"payment:mock:{mtid}")
            assert raw is not None, "Redis key must exist after checkout"
            assert json.loads(raw)["status"] == "pending", (
                "Redis key must be 'pending' — we have not approved it"
            )

            # Run reconcile — older_than_minutes=0 so the new order qualifies.
            result = PaymentService(db).reconcile_pending(older_than_minutes=0, limit=50)

            # Order must still be PENDING.
            db.refresh(order)
            assert order.status == OrderStatus.PENDING, (
                f"Order must remain PENDING when gateway still reports pending, "
                f"got {order.status}"
            )

            assert result["checked"] >= 1, (
                f"reconcile must have checked at least 1 order, got {result['checked']}"
            )
            assert result["still_pending"] >= 1, (
                f"still_pending must be >=1 for our genuinely-pending order, "
                f"got {result['still_pending']}"
            )
            # settled_paid must not count this order.
            # (Other concurrent tests might have settled orders but not our
            # specific one — we can only assert our order is not PAID.)
            assert order.status == OrderStatus.PENDING, (
                "Order must not have been settled by reconcile"
            )

        finally:
            db.rollback()
            _disable_mock(db)
            _cleanup(user_ids, product_ids, order_ids)
            if mtid:
                r.delete(f"payment:mock:{mtid}")
            db.close()
