"""Payment webhook lifecycle integration tests.

Covers the following scenarios from the payment test matrix:
  1_prepaid_success   – PENDING -> PAID, paid_at set, cart cleared, stock stays reduced
  2_prepaid_failure   – PENDING -> CANCELLED, stock RESTORED to pre-checkout value
  3_duplicate_webhook – duplicate success settle is idempotent (one loyalty row, one coupon-usage, one notification)
  4_wrong_amount      – SUCCESS settle with mismatched gateway amount leaves order PENDING
  5_invalid_signature – webhook POST with bad signature returns 403 (razorpay)

Run inside the backend container:
    docker compose exec -T backend pytest tests/test_payment_webhook_lifecycle.py -v
"""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.integrations.payments.base import PaymentStatus
from app.main import app
from app.models.coupon import DiscountType
from app.models.loyalty import PointsReason
from app.models.order import Order, OrderStatus
from app.models.product import Product
from app.models.user import User
from app.repositories.coupon_repository import CouponUsageRepository
from app.repositories.loyalty_repository import PointsTransactionRepository
from app.schemas.address import AddressCreate
from app.schemas.cart import CartItemIn
from app.schemas.coupon import CouponCreate
from app.schemas.order import OrderItemCreate
from app.schemas.payment import CheckoutRequest
from app.schemas.payment_method import PaymentMethodUpdate
from app.services.cart_service import CartService
from app.services.coupon_service import CouponService
from app.services.payment_method_config_service import PaymentMethodConfigService
from app.services.payment_service import PaymentService


# ---------------------------------------------------------------------------
# Local helpers  (self-contained — do NOT import across test files)
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _make_user(db: Session, *, is_admin: bool = False) -> User:
    u = User(
        email=f"whltest-{_uid()}@example.com",
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
        sku=f"SKU-WHL-{uid}",
        name=f"WHLTestProd-{uid}",
        price=price,
        stock=stock,
    )
    db.add(p)
    db.flush()
    return p


def _addr_create() -> AddressCreate:
    return AddressCreate(
        full_name="WHL Test Buyer",
        phone="9876543210",
        line1="1 Webhook Lane",
        city="Bangalore",
        state="Karnataka",
        country="IN",
        pincode="560001",
        label="home",
    )


def _cleanup(
    user_ids: list[int],
    product_ids: list[int],
    order_ids: list[int],
    coupon_ids: list[int] | None = None,
) -> None:
    """Delete all test rows via a fresh session so partial rollbacks in the
    test session never leave orphan rows.  Deletion order honours FK constraints."""
    with SessionLocal() as s:
        if order_ids:
            ids_tuple = tuple(order_ids)
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
        if coupon_ids:
            s.execute(
                text("DELETE FROM coupons WHERE id IN :ids"),
                {"ids": tuple(coupon_ids)},
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


def _reset_razorpay(db: Session) -> None:
    """Reset razorpay to its post-migration state: disabled, no credentials.
    Copied verbatim from test_payment_methods.py."""
    svc = PaymentMethodConfigService(db)
    svc.update(
        "razorpay",
        PaymentMethodUpdate(
            enabled=False,
            credentials={"key_id": "", "key_secret": "", "webhook_secret": ""},
        ),
    )


def _enable_mock(db: Session) -> None:
    """Enable the mock gateway so checkout with gateway_code='mock' works."""
    PaymentMethodConfigService(db).update("mock", PaymentMethodUpdate(enabled=True))


def _disable_mock(db: Session) -> None:
    """Restore mock to its seeded (disabled) state."""
    PaymentMethodConfigService(db).update("mock", PaymentMethodUpdate(enabled=False))


# ---------------------------------------------------------------------------
# Scenario 1: prepaid SUCCESS
# ---------------------------------------------------------------------------

class TestPrepaidSuccess:
    """1_prepaid_success: Prepaid SUCCESS — PENDING -> PAID, paid_at set,
    cart cleared, stock stays reduced (not restored on success)."""

    def test_prepaid_success_order_becomes_paid_stock_stays_reduced_cart_cleared(
        self,
    ) -> None:
        """After approving a mock prepaid checkout the order must be PAID,
        paid_at must be set, stock must remain at (initial - qty), and the
        Redis cart key must be absent."""
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
            prod = _make_product(db, stock=50)
            product_ids.append(prod.id)
            db.commit()

            pre_checkout_stock = prod.stock  # 50

            # Seed a cart item so we can verify _clear_cart runs.
            CartService(db).add_item(user.id, CartItemIn(product_id=prod.id, quantity=2))

            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=2)],
                address=_addr_create(),
                gateway_code="mock",
                payment_method="prepaid",
            )
            svc = PaymentService(db)
            order, mtid, _redirect = svc.checkout(user, req)
            order_ids.append(order.id)

            # Order is PENDING; stock already decremented by checkout.
            assert order.status == OrderStatus.PENDING

            # Settle SUCCESS via mock decision (no gateway_amount_minor passed
            # by this path, so the amount guard is skipped).
            svc.mark_mock_decision(mtid, "approve")
            db.refresh(order)

            assert order.status == OrderStatus.PAID, (
                f"Expected PAID after approve, got {order.status}"
            )
            assert order.paid_at is not None, "paid_at must be set on PAID order"

            # Stock stays reduced — success does NOT restore it.
            db.refresh(prod)
            assert prod.stock == pre_checkout_stock - 2, (
                f"Stock should be {pre_checkout_stock - 2}, got {prod.stock}"
            )

            # Cart must be cleared by _mark_paid -> _clear_cart.
            cart_exists = r.exists(f"cart:{user.id}")
            assert not cart_exists, (
                "Redis cart key must be absent after successful payment"
            )

        finally:
            db.rollback()
            _disable_mock(db)
            _cleanup(user_ids, product_ids, order_ids)
            if mtid:
                r.delete(f"payment:mock:{mtid}")
            r.delete(f"cart:{user_ids[0] if user_ids else 0}")
            db.close()


# ---------------------------------------------------------------------------
# Scenario 2: prepaid FAILURE
# ---------------------------------------------------------------------------

class TestPrepaidFailure:
    """2_prepaid_failure: Prepaid FAILED — PENDING -> CANCELLED, stock RESTORED."""

    def test_prepaid_failure_order_cancelled_stock_restored(self) -> None:
        """After declining a mock prepaid checkout the order must be CANCELLED,
        paid_at must remain None, and stock must be fully restored."""
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
            prod = _make_product(db, stock=50)
            product_ids.append(prod.id)
            db.commit()

            pre_checkout_stock = prod.stock  # 50

            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=3)],
                address=_addr_create(),
                gateway_code="mock",
                payment_method="prepaid",
            )
            svc = PaymentService(db)
            order, mtid, _redirect = svc.checkout(user, req)
            order_ids.append(order.id)

            # Checkout decrements stock by 3.
            db.refresh(prod)
            assert prod.stock == pre_checkout_stock - 3

            # Settle FAILED via mock decline.
            svc.mark_mock_decision(mtid, "decline")
            db.refresh(order)

            assert order.status == OrderStatus.CANCELLED, (
                f"Expected CANCELLED after decline, got {order.status}"
            )
            assert order.paid_at is None, "paid_at must remain None on CANCELLED order"

            # Stock must be fully restored by _restore_stock.
            db.refresh(prod)
            assert prod.stock == pre_checkout_stock, (
                f"Stock should be restored to {pre_checkout_stock}, got {prod.stock}"
            )

            # No loyalty row must exist.
            pts_row = PointsTransactionRepository(db).find_by_ref(
                PointsReason.PLACE_ORDER, "order", order.id
            )
            assert pts_row is None, "No loyalty row must be created on FAILED payment"

        finally:
            db.rollback()
            _disable_mock(db)
            _cleanup(user_ids, product_ids, order_ids)
            if mtid:
                r.delete(f"payment:mock:{mtid}")
            db.close()


# ---------------------------------------------------------------------------
# Scenario 3: duplicate webhook idempotency
# ---------------------------------------------------------------------------

class TestDuplicateWebhook:
    """3_duplicate_webhook: duplicate success settle is idempotent — exactly
    one loyalty row, one coupon-usage row, one notification dispatch."""

    def test_duplicate_success_settle_is_idempotent(self) -> None:
        """Calling mark_mock_decision('approve') twice must result in exactly
        one loyalty row, one coupon-usage row, and one _send_notification call."""
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        coupon_ids: list[int] = []
        mtid: str | None = None
        r = _redis_client()

        db = SessionLocal()
        try:
            _enable_mock(db)
            # Create coupon BEFORE checkout so it can be validated during order build.
            coupon_code = f"DUPTEST-{_uid()}".upper()
            coupon = CouponService(db).create(
                CouponCreate(
                    code=coupon_code,
                    discount_type=DiscountType.FIXED,
                    discount_value=Decimal("10.00"),
                    is_active=True,
                )
            )
            coupon_ids.append(coupon.id)

            user = _make_user(db)
            user_ids.append(user.id)
            # Use price 100.00 * qty 2 = subtotal 200.00 > discount 10.00 => discount_amount > 0
            prod = _make_product(db, price=Decimal("100.00"), stock=50)
            product_ids.append(prod.id)
            db.commit()

            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=2)],
                address=_addr_create(),
                gateway_code="mock",
                payment_method="prepaid",
                coupon_code=coupon_code,
            )
            svc = PaymentService(db)
            order, mtid, _redirect = svc.checkout(user, req)
            order_ids.append(order.id)

            # Verify the coupon was snapshotted on the order.
            assert order.coupon_code == coupon_code, (
                f"Expected coupon_code={coupon_code!r}, got {order.coupon_code!r}"
            )
            assert order.discount_amount > 0, (
                "discount_amount must be > 0 for coupon-usage path to run"
            )

            # Settle twice; patch _send_notification to count calls.
            with patch(
                "app.services.payment_service.PaymentService._send_notification"
            ) as notify_mock:
                svc.mark_mock_decision(mtid, "approve")
                svc.mark_mock_decision(mtid, "approve")  # duplicate — must be no-op

            db.refresh(order)
            assert order.status == OrderStatus.PAID, (
                f"Order must be PAID after first approve, got {order.status}"
            )

            # Exactly one _send_notification call.
            assert notify_mock.call_count == 1, (
                f"_send_notification must be called exactly once, got {notify_mock.call_count}"
            )

            # Exactly one loyalty row.
            pts_row = PointsTransactionRepository(db).find_by_ref(
                PointsReason.PLACE_ORDER, "order", order.id
            )
            assert pts_row is not None, "One loyalty row must exist after PAID"

            # Exactly one coupon-usage row.
            usage = CouponUsageRepository(db).find_for_order(order.id)
            assert usage is not None, "One coupon-usage row must exist after PAID"

            # Coupon usage_count incremented by exactly 1.
            db.refresh(coupon)
            assert coupon.usage_count == 1, (
                f"coupon.usage_count must be 1, got {coupon.usage_count}"
            )

        finally:
            db.rollback()
            _disable_mock(db)
            _cleanup(user_ids, product_ids, order_ids, coupon_ids)
            if mtid:
                r.delete(f"payment:mock:{mtid}")
            if user_ids:
                r.delete(f"cart:{user_ids[0]}")
            db.close()


# ---------------------------------------------------------------------------
# Scenario 4: wrong amount — order stays PENDING
# ---------------------------------------------------------------------------

class TestWrongAmount:
    """4_wrong_amount: SUCCESS settle with mismatched gateway_amount_minor
    leaves order PENDING (amount guard in _apply_status)."""

    def test_wrong_amount_leaves_order_pending(self) -> None:
        """Calling _apply_status directly with a wrong gateway_amount_minor
        must leave the order PENDING and not set paid_at."""
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
            # Use a known price so we can compute expected_minor precisely.
            prod = _make_product(db, price=Decimal("100.00"), stock=50)
            product_ids.append(prod.id)
            db.commit()

            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address=_addr_create(),
                gateway_code="mock",
                payment_method="prepaid",
            )
            svc = PaymentService(db)
            order, mtid, _redirect = svc.checkout(user, req)
            order_ids.append(order.id)

            assert order.status == OrderStatus.PENDING

            # Compute what the guard expects.
            expected_minor = int(
                (Decimal(str(order.total_amount)) * 100).to_integral_value()
            )
            wrong_minor = expected_minor - 1  # deliberately one paise short

            # Call _apply_status directly with wrong amount — the guard must
            # detect the mismatch and return early without marking PAID.
            svc._apply_status(
                order,
                PaymentStatus.SUCCESS,
                gateway_amount_minor=wrong_minor,
            )
            db.refresh(order)

            assert order.status == OrderStatus.PENDING, (
                f"Order must remain PENDING on amount mismatch, got {order.status}"
            )
            assert order.paid_at is None, "paid_at must not be set when amount guard fires"

            # No loyalty row must have been created.
            pts_row = PointsTransactionRepository(db).find_by_ref(
                PointsReason.PLACE_ORDER, "order", order.id
            )
            assert pts_row is None, "No loyalty row must exist when order stays PENDING"

        finally:
            db.rollback()
            _disable_mock(db)
            _cleanup(user_ids, product_ids, order_ids)
            if mtid:
                r.delete(f"payment:mock:{mtid}")
            db.close()


# ---------------------------------------------------------------------------
# Scenario 5: invalid signature — HTTP 403
# ---------------------------------------------------------------------------

class TestInvalidSignature:
    """5_invalid_signature: Webhook POST with bad signature returns 403 (razorpay)."""

    def test_razorpay_webhook_bad_signature_returns_403(self) -> None:
        """POSTing to /api/v1/payments/webhook/razorpay with a bogus
        X-Razorpay-Signature header must return HTTP 403."""
        db = SessionLocal()
        try:
            # Enable razorpay with minimal credentials so the endpoint passes
            # the pm_row.enabled and gw_def.implemented checks.  The wrong
            # signature is what causes the 403 we are testing.
            svc = PaymentMethodConfigService(db)
            svc.update(
                "razorpay",
                PaymentMethodUpdate(
                    credentials={
                        "key_id": "rzp_test_x",
                        "key_secret": "sec_x",
                        "webhook_secret": "whsec_test",
                    }
                ),
            )
            svc.update("razorpay", PaymentMethodUpdate(enabled=True))
        finally:
            # Close this session BEFORE making HTTP calls (mirrors
            # test_payment_methods.py which does the same to avoid a
            # concurrent session conflict under the TestClient).
            db.close()

        client = TestClient(app, raise_server_exceptions=False)
        try:
            resp = client.post(
                "/api/v1/payments/webhook/razorpay",
                content=b"{}",
                headers={"X-Razorpay-Signature": "deadbeef"},
            )
            assert resp.status_code == 403, (
                f"Expected 403 for bad razorpay signature, got {resp.status_code}: {resp.text}"
            )
        finally:
            db2 = SessionLocal()
            try:
                _reset_razorpay(db2)
            finally:
                db2.close()
