"""Integration tests for payment status polling scenarios.

Covers the following scenarios from the payment test matrix:
  6_return_page_polling — get_status drives PENDING -> PAID transition via
      MockProvider.fetch_status when the gateway has already succeeded but the
      webhook hasn't arrived yet.
  7_gateway_down — get_status swallows a provider exception and returns the
      still-PENDING order without raising a 500.

All tests are service-level (no HTTP), hermetic, and self-cleaning.

Run inside the backend container:
    docker compose exec -T backend pytest tests/test_payment_status_polling.py -v
"""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import redis
from sqlalchemy import text

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.integrations.payments.mock import MockProvider
from app.models.order import OrderStatus
from app.models.product import Product
from app.models.user import User
from app.schemas.address import AddressCreate
from app.schemas.order import OrderItemCreate
from app.schemas.payment import CheckoutRequest
from app.schemas.payment_method import PaymentMethodUpdate
from app.services.payment_method_config_service import PaymentMethodConfigService
from app.services.payment_service import PaymentService


# ---------------------------------------------------------------------------
# Helpers (local copies — not imported from other test files)
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _make_user(db) -> User:
    u = User(
        email=f"poll-test-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(u)
    db.flush()
    return u


def _make_product(db, *, price: Decimal = Decimal("100.00"), stock: int = 50) -> Product:
    p = Product(
        sku=f"SKU-POLL-{_uid()}",
        name=f"PollTestProduct {_uid()}",
        price=price,
        stock=stock,
    )
    db.add(p)
    db.flush()
    return p


def _addr_create() -> AddressCreate:
    return AddressCreate(
        full_name="Poll Test Buyer",
        phone="9876543210",
        line1="12 Poll Lane",
        city="Bangalore",
        state="Karnataka",
        country="IN",
        pincode="560001",
        label="home",
    )


def _enable_mock(db) -> None:
    """Enable the mock gateway so checkout with gateway_code='mock' works."""
    PaymentMethodConfigService(db).update("mock", PaymentMethodUpdate(enabled=True))


def _disable_mock(db) -> None:
    """Restore mock to its seeded (disabled) state."""
    PaymentMethodConfigService(db).update("mock", PaymentMethodUpdate(enabled=False))


def _cleanup(user_ids: list, product_ids: list, order_ids: list) -> None:
    """Delete test rows in FK-safe order using a fresh session."""
    with SessionLocal() as s:
        if order_ids:
            s.execute(
                text("DELETE FROM order_items WHERE order_id IN :ids"),
                {"ids": tuple(order_ids)},
            )
            s.execute(
                text("DELETE FROM orders WHERE id IN :ids"),
                {"ids": tuple(order_ids)},
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


# ---------------------------------------------------------------------------
# Scenario 6: return-page polling drives PENDING -> PAID via fetch_status
# ---------------------------------------------------------------------------

class TestReturnPagePolling:
    """Scenario 6 — 6_return_page_polling.

    Expected result: get_status polls MockProvider.fetch_status, sees SUCCESS,
    calls _apply_status, and returns the order in PAID state with paid_at set.
    The gateway having already succeeded (Redis key = success) but the webhook
    not yet having arrived is the realistic trigger.
    """

    def test_get_status_transitions_pending_to_paid_when_provider_reports_success(self):
        """When the mock Redis key is flipped to success before the webhook
        lands, PaymentService.get_status must drive the order from PENDING to
        PAID via fetch_status (amount guard passes because we preserve the
        original amount_minor from initiate via parse_webhook)."""
        db = SessionLocal()
        user_ids: list = []
        product_ids: list = []
        order_ids: list = []
        mtid: str | None = None
        r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

        try:
            _enable_mock(db)
            # --- setup: user + product ---
            user = _make_user(db)
            prod = _make_product(db, price=Decimal("100.00"), stock=50)
            db.commit()
            user_ids.append(user.id)
            product_ids.append(prod.id)

            # --- checkout: order is PENDING, stock decremented, Redis key seeded ---
            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=2)],
                address=_addr_create(),
                gateway_code="mock",
                payment_method="prepaid",
            )
            svc = PaymentService(db)
            order, mtid, _redirect = svc.checkout(user, req)
            order_ids.append(order.id)

            assert order.status == OrderStatus.PENDING, (
                f"Order should be PENDING after checkout, got {order.status}"
            )

            # --- simulate gateway success without touching _apply_status ---
            # Use MockProvider.parse_webhook so the existing Redis record's
            # amount_minor is preserved (merge approach).  This is the 'gateway
            # already processed but webhook delayed' scenario.
            mp = MockProvider(
                frontend_url=settings.FRONTEND_URL,
                redis_url=settings.REDIS_URL,
            )
            mp.parse_webhook(
                (
                    '{"merchant_transaction_id":"%s","action":"approve"}' % mtid
                ).encode()
            )

            # Verify the Redis key now shows success before we poll.
            raw = r.get(f"payment:mock:{mtid}")
            assert raw is not None, "Redis key must exist after parse_webhook"
            record = json.loads(raw)
            assert record["status"] == "success", (
                f"Redis key should be 'success' after parse_webhook, got {record['status']}"
            )

            # --- poll: get_status should detect SUCCESS and settle the order ---
            order2 = PaymentService(db).get_status(user.id, mtid)

            # --- assertions ---
            db.refresh(order2)
            assert order2.status == OrderStatus.PAID, (
                f"Order must be PAID after get_status poll, got {order2.status}"
            )
            assert order2.paid_at is not None, (
                "paid_at must be set after successful get_status poll"
            )

        finally:
            # Clean up Redis keys.
            db.rollback()
            _disable_mock(db)
            if mtid:
                r.delete(f"payment:mock:{mtid}")
                r.delete(f"cart:{user_ids[0]}" if user_ids else "")
            _cleanup(user_ids, product_ids, order_ids)
            db.close()

    def test_get_status_returns_paid_order_confirmed_in_fresh_session(self):
        """After get_status settles the order to PAID, a fresh DB session
        reading the same row also sees PAID — confirming the commit landed."""
        db = SessionLocal()
        user_ids: list = []
        product_ids: list = []
        order_ids: list = []
        mtid: str | None = None
        r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

        try:
            _enable_mock(db)
            user = _make_user(db)
            prod = _make_product(db, price=Decimal("50.00"), stock=30)
            db.commit()
            user_ids.append(user.id)
            product_ids.append(prod.id)

            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address=_addr_create(),
                gateway_code="mock",
                payment_method="prepaid",
            )
            svc = PaymentService(db)
            order, mtid, _redirect = svc.checkout(user, req)
            order_id = order.id
            order_ids.append(order_id)

            # Flip Redis to success via parse_webhook (preserves amount_minor).
            mp = MockProvider(
                frontend_url=settings.FRONTEND_URL,
                redis_url=settings.REDIS_URL,
            )
            mp.parse_webhook(
                (
                    '{"merchant_transaction_id":"%s","action":"approve"}' % mtid
                ).encode()
            )

            # Poll via a fresh PaymentService on the same session.
            PaymentService(db).get_status(user.id, mtid)

            # Re-query in a brand-new session to verify the commit.
            with SessionLocal() as fresh_db:
                from sqlalchemy import select as _select
                from app.models.order import Order as _Order
                settled = fresh_db.execute(
                    _select(_Order).where(_Order.id == order_id)
                ).scalar_one()
                assert settled.status == OrderStatus.PAID, (
                    f"Fresh-session re-query should see PAID, got {settled.status}"
                )
                assert settled.paid_at is not None, (
                    "Fresh-session re-query should see paid_at set"
                )

        finally:
            db.rollback()
            _disable_mock(db)
            if mtid:
                r.delete(f"payment:mock:{mtid}")
            _cleanup(user_ids, product_ids, order_ids)
            db.close()


# ---------------------------------------------------------------------------
# Scenario 7: provider fetch_status raises — order stays PENDING, no exception
# ---------------------------------------------------------------------------

class TestGatewayDown:
    """Scenario 7 — 7_gateway_down.

    Expected result: when the provider's fetch_status raises during get_status,
    the exception is swallowed, no exception propagates to the caller, and the
    order remains in PENDING state with paid_at unset.
    """

    def test_get_status_swallows_provider_exception_and_returns_pending_order(self):
        """If the provider raises RuntimeError during fetch_status, get_status
        must NOT propagate the exception and must return the original PENDING
        order unchanged."""
        db = SessionLocal()
        user_ids: list = []
        product_ids: list = []
        order_ids: list = []
        mtid: str | None = None
        r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

        try:
            _enable_mock(db)
            # --- setup ---
            user = _make_user(db)
            prod = _make_product(db, price=Decimal("200.00"), stock=20)
            db.commit()
            user_ids.append(user.id)
            product_ids.append(prod.id)

            # --- checkout ---
            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address=_addr_create(),
                gateway_code="mock",
                payment_method="prepaid",
            )
            svc = PaymentService(db)
            order, mtid, _redirect = svc.checkout(user, req)
            order_ids.append(order.id)

            assert order.status == OrderStatus.PENDING, (
                "Order must be PENDING before poll"
            )

            # --- patch the provider factory so fetch_status raises ---
            mock_provider = MagicMock()
            mock_provider.fetch_status.side_effect = RuntimeError("gateway down")

            with patch(
                "app.services.payment_service.get_provider_for_order",
                return_value=mock_provider,
            ):
                # Must NOT raise.
                order2 = PaymentService(db).get_status(user.id, mtid)

            # --- assertions ---
            assert mock_provider.fetch_status.called, (
                "fetch_status must have been called to confirm we hit the provider"
            )
            assert order2.status == OrderStatus.PENDING, (
                f"Order must remain PENDING after provider failure, got {order2.status}"
            )
            assert order2.paid_at is None, (
                "paid_at must be None when provider fetch_status failed"
            )

        finally:
            db.rollback()
            _disable_mock(db)
            if mtid:
                r.delete(f"payment:mock:{mtid}")
            _cleanup(user_ids, product_ids, order_ids)
            db.close()

    def test_get_status_swallows_arbitrary_exception_type(self):
        """get_status must swallow any Exception subclass from fetch_status,
        not just RuntimeError — the broad except clause covers all provider
        failure modes (network timeouts, assertion errors, etc.)."""
        db = SessionLocal()
        user_ids: list = []
        product_ids: list = []
        order_ids: list = []
        mtid: str | None = None
        r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

        try:
            _enable_mock(db)
            user = _make_user(db)
            prod = _make_product(db, price=Decimal("75.00"), stock=10)
            db.commit()
            user_ids.append(user.id)
            product_ids.append(prod.id)

            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address=_addr_create(),
                gateway_code="mock",
                payment_method="prepaid",
            )
            svc = PaymentService(db)
            order, mtid, _redirect = svc.checkout(user, req)
            order_ids.append(order.id)

            # Use a ValueError to confirm the except clause is broad.
            mock_provider = MagicMock()
            mock_provider.fetch_status.side_effect = ValueError("unexpected provider response")

            with patch(
                "app.services.payment_service.get_provider_for_order",
                return_value=mock_provider,
            ):
                order2 = PaymentService(db).get_status(user.id, mtid)

            assert order2.status == OrderStatus.PENDING, (
                f"Order must stay PENDING after ValueError from provider, got {order2.status}"
            )
            assert order2.paid_at is None, "paid_at must remain None"

        finally:
            db.rollback()
            _disable_mock(db)
            if mtid:
                r.delete(f"payment:mock:{mtid}")
            _cleanup(user_ids, product_ids, order_ids)
            db.close()

    def test_get_status_gateway_down_confirmed_pending_in_fresh_session(self):
        """After a failed poll (provider down), a fresh session re-query confirms
        the order row was never updated — no partial write leaked through."""
        db = SessionLocal()
        user_ids: list = []
        product_ids: list = []
        order_ids: list = []
        mtid: str | None = None
        r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

        try:
            _enable_mock(db)
            user = _make_user(db)
            prod = _make_product(db, price=Decimal("300.00"), stock=15)
            db.commit()
            user_ids.append(user.id)
            product_ids.append(prod.id)

            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=2)],
                address=_addr_create(),
                gateway_code="mock",
                payment_method="prepaid",
            )
            svc = PaymentService(db)
            order, mtid, _redirect = svc.checkout(user, req)
            order_id = order.id
            order_ids.append(order_id)

            mock_provider = MagicMock()
            mock_provider.fetch_status.side_effect = RuntimeError("provider offline")

            with patch(
                "app.services.payment_service.get_provider_for_order",
                return_value=mock_provider,
            ):
                PaymentService(db).get_status(user.id, mtid)

            # Confirm in a fresh session — no stale-cache effect.
            with SessionLocal() as fresh_db:
                from sqlalchemy import select as _select
                from app.models.order import Order as _Order
                row = fresh_db.execute(
                    _select(_Order).where(_Order.id == order_id)
                ).scalar_one()
                assert row.status == OrderStatus.PENDING, (
                    f"Fresh session must see PENDING, got {row.status}"
                )
                assert row.paid_at is None, (
                    "Fresh session must see paid_at is None after failed poll"
                )

        finally:
            db.rollback()
            _disable_mock(db)
            if mtid:
                r.delete(f"payment:mock:{mtid}")
            _cleanup(user_ids, product_ids, order_ids)
            db.close()
