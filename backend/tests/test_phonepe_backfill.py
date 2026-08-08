"""Regression test for backfilling a PhonePe gateway transaction id.

Mirrors the real-world repair of order #79: an order that is PAID via PhonePe
but whose captured gateway transaction id was never copied from the
``payment_events`` audit log onto the canonical rows
(``orders.payment_provider_ref`` / ``order_payments.gateway_payment_id``).

``app.services.payment_backfill.backfill_order_payment_ref`` must recover the
reference from the audit trail and write it onto both rows, idempotently.

Run inside the backend container:
    docker compose exec -T backend pytest tests/test_phonepe_backfill.py -v
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import MagicMock, patch

import redis
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.integrations.payments.phonepe import PhonePeProvider
from app.models.order import Order, OrderStatus
from app.models.order_payment import OrderPayment, PaymentTxnStatus
from app.models.product import Product
from app.models.user import User
from app.schemas.address import AddressCreate
from app.schemas.order import OrderItemCreate
from app.schemas.payment import CheckoutRequest
from app.services.payment_backfill import backfill_order_payment_ref
from app.services.payment_service import PaymentService


TEST_MERCHANT_ID = "PGTESTMERCHANT"
TEST_SALT_KEY = "test-salt-key-0000-1111-2222"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _provider() -> PhonePeProvider:
    return PhonePeProvider(
        merchant_id=TEST_MERCHANT_ID,
        salt_key=TEST_SALT_KEY,
        salt_index=1,
        base_url="https://sandbox.phonepe.test/apis/pg-sandbox",
        callback_url="https://shop.test/api/v1/payments/webhook/phonepe",
    )


def _initiate_envelope() -> dict:
    return {
        "success": True,
        "code": "PAYMENT_INITIATED",
        "data": {
            "merchantId": TEST_MERCHANT_ID,
            "instrumentResponse": {
                "redirectInfo": {"url": "https://sandbox.phonepe.test/pay/x"}
            },
        },
    }


def _status_envelope(mtid: str, amount_minor: int, txn_id: str) -> dict:
    return {
        "success": True,
        "code": "PAYMENT_SUCCESS",
        "data": {
            "merchantId": TEST_MERCHANT_ID,
            "merchantTransactionId": mtid,
            "transactionId": txn_id,
            "amount": amount_minor,
            "state": "COMPLETED",
            "responseCode": "SUCCESS",
        },
    }


@contextmanager
def _use(provider: PhonePeProvider):
    with patch(
        "app.services.payment_service.get_payment_provider", return_value=provider
    ), patch(
        "app.services.payment_service.get_provider_for_order", return_value=provider
    ):
        yield


def _make_user(db: Session) -> User:
    u = User(
        email=f"bftest-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(u)
    db.flush()
    return u


def _make_product(db: Session) -> Product:
    uid = _uid()
    p = Product(
        sku=f"SKU-BF-{uid}",
        name=f"BFTestProd-{uid}",
        price=Decimal("100.00"),
        cost=Decimal("55.00"),
        stock=50,
    )
    db.add(p)
    db.flush()
    return p


def _addr() -> AddressCreate:
    return AddressCreate(
        full_name="Backfill Buyer",
        phone="9876543210",
        line1="7 Backfill Road",
        city="Bangalore",
        state="Karnataka",
        country="IN",
        pincode="560001",
        label="home",
    )


def _cleanup(user_ids: list[int], product_ids: list[int], order_ids: list[int]) -> None:
    with SessionLocal() as s:
        if order_ids:
            ids = tuple(order_ids)
            for tbl in ("order_items", "order_payments", "order_addresses", "shipments"):
                s.execute(text(f"DELETE FROM {tbl} WHERE order_id IN :ids"), {"ids": ids})
            s.execute(text("DELETE FROM orders WHERE id IN :ids"), {"ids": ids})
        if product_ids:
            s.execute(
                text("DELETE FROM products WHERE id IN :ids"),
                {"ids": tuple(product_ids)},
            )
        if user_ids:
            uids = tuple(user_ids)
            for tbl in ("addresses", "points_transactions", "customers"):
                s.execute(text(f"DELETE FROM {tbl} WHERE user_id IN :ids"), {"ids": uids})
            s.execute(text("DELETE FROM users WHERE id IN :ids"), {"ids": uids})
        s.commit()


def _settle_via_poll(db: Session, user: User, prod: Product) -> tuple[int, str, str]:
    """Checkout + settle a PhonePe order via status poll. Returns
    (order_id, mtid, txn_id). After this the order is healthy: refs populated
    and a STATUS_POLL payment_events row carries the provider_ref."""
    provider = _provider()
    provider._post = MagicMock(return_value=_initiate_envelope())
    req = CheckoutRequest(
        items=[OrderItemCreate(product_id=prod.id, quantity=1)],
        address=_addr(),
        gateway_code="phonepe",
        payment_method="prepaid",
    )
    with _use(provider):
        order, mtid, _redirect, _checkout = PaymentService(db).checkout(user, req)
    oid = order.id
    amount_minor = int((Decimal(str(order.total_amount)) * 100).to_integral_value())
    txn_id = "T_BF_" + _uid().upper()
    provider._get = MagicMock(return_value=_status_envelope(mtid, amount_minor, txn_id))
    with _use(provider):
        PaymentService(db).get_status(user.id, mtid)
    return oid, mtid, txn_id


class TestBackfillFromPaymentEvents:
    def test_backfill_recovers_ref_then_is_idempotent(self) -> None:
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtid: str | None = None

        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db)
            product_ids.append(prod.id)
            db.commit()

            oid, mtid, txn_id = _settle_via_poll(db, user, prod)
            order_ids.append(oid)

            # Sanity: the healthy order has the ref on both rows.
            with SessionLocal() as s:
                o = s.get(Order, oid)
                assert o.payment_provider_ref == txn_id
                assert o.status == OrderStatus.PAID

            # --- simulate the historical bug: strip the captured ref ---
            with SessionLocal() as s:
                o = s.get(Order, oid)
                o.payment_provider_ref = None
                for leg in o.payments:
                    if (leg.payment_method or "").lower() != "cod":
                        leg.gateway_payment_id = None
                        leg.raw_gateway_response = None
                s.commit()

            # Confirm it's now "broken".
            with SessionLocal() as s:
                o = s.get(Order, oid)
                assert o.payment_provider_ref is None
                prepaid = [p for p in o.payments if (p.payment_method or "") != "cod"]
                assert all(p.gateway_payment_id is None for p in prepaid)

            # --- run the backfill (fresh session) ---
            with SessionLocal() as s:
                summary = backfill_order_payment_ref(s, oid)
            assert summary["status"] == "backfilled", summary
            assert summary["found_ref"] == txn_id
            assert summary["wrote_order_ref"] is True
            assert len(summary["updated_leg_ids"]) == 1

            # --- verify the PhonePe txn id is written to order + payment tables ---
            with SessionLocal() as s:
                o = s.get(Order, oid)
                assert o.payment_provider_ref == txn_id
                prepaid = [p for p in o.payments if (p.payment_method or "") != "cod"]
                assert len(prepaid) == 1
                assert prepaid[0].gateway_payment_id == txn_id
                assert prepaid[0].payment_status == PaymentTxnStatus.PAID

            # --- idempotent: a second run changes nothing ---
            with SessionLocal() as s:
                again = backfill_order_payment_ref(s, oid)
            assert again["status"] == "already_complete", again
            assert again["wrote_order_ref"] is False
            assert again["updated_leg_ids"] == []

        finally:
            db.rollback()
            _cleanup(user_ids, product_ids, order_ids)
            if mtid:
                redis.Redis.from_url(settings.REDIS_URL, decode_responses=True).delete(
                    f"payment:mock:{mtid}"
                )
            db.close()

    def test_backfill_healthy_order_is_noop(self) -> None:
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtid: str | None = None

        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db)
            product_ids.append(prod.id)
            db.commit()

            oid, mtid, txn_id = _settle_via_poll(db, user, prod)
            order_ids.append(oid)

            with SessionLocal() as s:
                summary = backfill_order_payment_ref(s, oid)
            assert summary["status"] == "already_complete"
            assert summary["found_ref"] == txn_id

        finally:
            db.rollback()
            _cleanup(user_ids, product_ids, order_ids)
            if mtid:
                redis.Redis.from_url(settings.REDIS_URL, decode_responses=True).delete(
                    f"payment:mock:{mtid}"
                )
            db.close()

    def test_backfill_pending_order_without_events_reports_no_ref(self) -> None:
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtid: str | None = None

        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db)
            product_ids.append(prod.id)
            db.commit()

            # Checkout only — PENDING, no settlement, so no provider_ref anywhere
            # and no payment_events carrying one.
            provider = _provider()
            provider._post = MagicMock(return_value=_initiate_envelope())
            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address=_addr(),
                gateway_code="phonepe",
                payment_method="prepaid",
            )
            with _use(provider):
                order, mtid, _r, _checkout = PaymentService(db).checkout(user, req)
            order_ids.append(order.id)

            with SessionLocal() as s:
                summary = backfill_order_payment_ref(s, order.id)
            assert summary["status"] == "no_ref_in_events", summary
            assert summary["found_ref"] is None

        finally:
            db.rollback()
            _cleanup(user_ids, product_ids, order_ids)
            if mtid:
                redis.Redis.from_url(settings.REDIS_URL, decode_responses=True).delete(
                    f"payment:mock:{mtid}"
                )
            db.close()

    def test_backfill_missing_order_reports_not_found(self) -> None:
        with SessionLocal() as s:
            summary = backfill_order_payment_ref(s, 99_000_001)
        assert summary["status"] == "not_found"
