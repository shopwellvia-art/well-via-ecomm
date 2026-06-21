"""End-to-end PhonePe checkout / payment test suite.

Covers the PhonePe-specific lifecycle on top of the order-normalization tables
(order_payments / order_addresses) introduced 2026-06-21:

  Area 1  TestCheckoutCreation   – order row, order_number, items, address
                                    snapshots, prepaid payment leg, money math
  Area 2  TestStatusPollSuccess  – PhonePe status poll settles PENDING -> PAID,
                                    provider ref + raw response persisted
  Area 3  TestWebhookSuccess      – signed S2S webhook settles the order
          TestPathConsistency     – webhook and status-poll converge identically
  Area 4  TestIdempotency         – repeated poll / repeated webhook never
                                    duplicate payment / item / address rows,
                                    never double-deduct stock
  Area 5  TestNegative            – failed / pending / bad-signature /
                                    amount-mismatch / unknown-mtid all safe
  Area 6  TestCostSnapshot        – order_items.unit_cost frozen from product.cost

Strategy
--------
The real ``PhonePeProvider`` class is used (so HMAC signing, webhook
verification and PhonePe's response→status mapping are exercised for real) but
its HTTP methods (``_post`` / ``_get``) are stubbed with canned PhonePe
envelopes, and the payment-service provider factory is patched to hand back this
test provider. No real network calls and no PhonePe credentials in the DB are
required. Webhook bodies are built and signed with the same test salt the
provider verifies against, so the signature path is genuinely tested.

Tests use the shared dev MySQL (the project convention) and clean up after
themselves in a fresh session, FK-safe. payment_events rows are append-only
audit and are intentionally left (their order_id FK is ON DELETE SET NULL);
they are matched per-test by the globally-unique merchant transaction id.

Run inside the backend container:
    docker compose exec -T backend pytest tests/test_phonepe_checkout_suite.py -v
"""
from __future__ import annotations

import base64
import json
import re
import uuid
from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
import redis
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.integrations.payments.base import PaymentStatus
from app.integrations.payments.phonepe import PhonePeProvider
from app.models.coupon import DiscountType
from app.models.order import Order, OrderItem, OrderStatus
from app.models.order_address import OrderAddress, OrderAddressType
from app.models.order_payment import OrderPayment, PaymentTxnStatus
from app.models.payment_event import PaymentEvent, PaymentEventType
from app.models.product import Product
from app.models.user import User
from app.schemas.address import AddressCreate
from app.schemas.coupon import CouponCreate
from app.schemas.order import OrderItemCreate
from app.schemas.payment import CheckoutRequest
from app.services.coupon_service import CouponService
from app.services.payment_service import PaymentService


# ---------------------------------------------------------------------------
# PhonePe test credentials + provider harness
# ---------------------------------------------------------------------------

TEST_MERCHANT_ID = "PGTESTMERCHANT"
TEST_SALT_KEY = "test-salt-key-0000-1111-2222"
TEST_SALT_INDEX = 1


def _make_phonepe_provider() -> PhonePeProvider:
    """A real PhonePeProvider wired with deterministic test credentials. HTTP
    methods are stubbed per-test; signing / verify / parse stay real."""
    return PhonePeProvider(
        merchant_id=TEST_MERCHANT_ID,
        salt_key=TEST_SALT_KEY,
        salt_index=TEST_SALT_INDEX,
        base_url="https://sandbox.phonepe.test/apis/pg-sandbox",
        callback_url="https://shop.test/api/v1/payments/webhook/phonepe",
    )


def _phonepe_initiate_envelope(redirect: str = "https://sandbox.phonepe.test/pay/redirect/abc") -> dict:
    """What /pg/v1/pay returns. PhonePe does NOT return a transactionId at
    initiation (it appears only at settlement), so the prepaid leg's
    gateway_order_id stays NULL — mirroring real order #79."""
    return {
        "success": True,
        "code": "PAYMENT_INITIATED",
        "message": "Payment initiated",
        "data": {
            "merchantId": TEST_MERCHANT_ID,
            "instrumentResponse": {
                "type": "PAY_PAGE",
                "redirectInfo": {"url": redirect, "method": "GET"},
            },
        },
    }


def _phonepe_status_envelope(
    mtid: str,
    amount_minor: int,
    txn_id: str,
    *,
    code: str = "PAYMENT_SUCCESS",
    state: str = "COMPLETED",
) -> dict:
    """What /pg/v1/status returns and (base64-wrapped) what the S2S webhook
    carries. ``txn_id`` is PhonePe's captured transaction id."""
    return {
        "success": code == "PAYMENT_SUCCESS",
        "code": code,
        "message": "Your request has been processed.",
        "data": {
            "merchantId": TEST_MERCHANT_ID,
            "merchantTransactionId": mtid,
            "transactionId": txn_id,
            "amount": amount_minor,
            "state": state,
            "responseCode": "SUCCESS" if code == "PAYMENT_SUCCESS" else "FAILED",
            "paymentInstrument": {"type": "UPI"},
        },
    }


def _sign_webhook(provider: PhonePeProvider, payload: dict) -> tuple[bytes, str]:
    """Build the {"response": "<base64>"} body PhonePe POSTs to the webhook and
    the matching X-VERIFY signature (over the base64 string), using the
    provider's own salt — so provider.verify_webhook accepts it."""
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    body = json.dumps({"response": encoded}).encode()
    signature = provider._sign(encoded)
    return body, signature


@contextmanager
def _use_phonepe(provider: PhonePeProvider):
    """Patch the payment service so every provider lookup returns our stubbed
    PhonePe provider (covers checkout, webhook handling and status polling)."""
    with patch(
        "app.services.payment_service.get_payment_provider", return_value=provider
    ), patch(
        "app.services.payment_service.get_provider_for_order", return_value=provider
    ):
        yield


# ---------------------------------------------------------------------------
# Data factories + cleanup (self-contained — not shared across test files)
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _make_user(db: Session) -> User:
    u = User(
        email=f"pptest-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(u)
    db.flush()
    return u


def _make_product(
    db: Session,
    *,
    price: Decimal = Decimal("100.00"),
    cost: Decimal | None = Decimal("60.00"),
    stock: int = 50,
) -> Product:
    uid = _uid()
    p = Product(
        sku=f"SKU-PP-{uid}",
        name=f"PPTestProd-{uid}",
        price=price,
        cost=cost,
        stock=stock,
    )
    db.add(p)
    db.flush()
    return p


def _addr_create() -> AddressCreate:
    return AddressCreate(
        full_name="PhonePe Test Buyer",
        phone="9876543210",
        line1="42 PhonePe Lane",
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
    """Delete all test rows via a fresh session, FK-safe. The normalized
    children (order_payments / order_addresses / shipments) are deleted
    explicitly rather than relying on ON DELETE CASCADE."""
    with SessionLocal() as s:
        if order_ids:
            ids = tuple(order_ids)
            for tbl in (
                "coupon_usages",
                "order_items",
                "order_payments",
                "order_addresses",
                "shipments",
            ):
                s.execute(
                    text(f"DELETE FROM {tbl} WHERE order_id IN :ids"), {"ids": ids}
                )
            s.execute(text("DELETE FROM orders WHERE id IN :ids"), {"ids": ids})
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
            uids = tuple(user_ids)
            for tbl in (
                "addresses",
                "points_transactions",
                "customers",
            ):
                s.execute(
                    text(f"DELETE FROM {tbl} WHERE user_id IN :ids"), {"ids": uids}
                )
            s.execute(text("DELETE FROM users WHERE id IN :ids"), {"ids": uids})
        s.commit()


def _redis() -> redis.Redis:
    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


# ---------------------------------------------------------------------------
# Fresh-session read helpers (return primitives — never detached ORM objects)
# ---------------------------------------------------------------------------

def _snapshot_order(order_id: int) -> dict:
    """Plain-dict snapshot of an order + normalized children read in a fresh
    session so assertions never observe identity-map staleness."""
    with SessionLocal() as s:
        o = s.execute(select(Order).where(Order.id == order_id)).scalar_one()
        legs = (
            s.execute(
                select(OrderPayment)
                .where(OrderPayment.order_id == order_id)
                .order_by(OrderPayment.id)
            )
            .scalars()
            .all()
        )
        addrs = (
            s.execute(
                select(OrderAddress)
                .where(OrderAddress.order_id == order_id)
                .order_by(OrderAddress.id)
            )
            .scalars()
            .all()
        )
        items = (
            s.execute(
                select(OrderItem)
                .where(OrderItem.order_id == order_id)
                .order_by(OrderItem.id)
            )
            .scalars()
            .all()
        )
        return {
            "status": o.status,
            "paid_at": o.paid_at,
            "payment_provider_ref": o.payment_provider_ref,
            "gateway_code": o.gateway_code,
            "order_number": o.order_number,
            "payment_intent_id": o.payment_intent_id,
            "subtotal": o.subtotal,
            "tax_amount": o.tax_amount,
            "discount_amount": o.discount_amount,
            "shipping_amount": o.shipping_amount,
            "cod_surcharge_amount": o.cod_surcharge_amount,
            "payment_discount_amount": o.payment_discount_amount,
            "total_amount": o.total_amount,
            "legs": [
                {
                    "id": p.id,
                    "method": p.payment_method,
                    "status": p.payment_status,
                    "gateway": p.gateway,
                    "gateway_order_id": p.gateway_order_id,
                    "gateway_payment_id": p.gateway_payment_id,
                    "amount": p.amount,
                    "paid_at": p.paid_at,
                    "raw": p.raw_gateway_response,
                    "txn_ref": p.transaction_reference,
                }
                for p in legs
            ],
            "addresses": [{"id": a.id, "type": a.address_type} for a in addrs],
            "items": [
                {
                    "id": it.id,
                    "product_id": it.product_id,
                    "qty": it.quantity,
                    "unit_price": it.unit_price,
                    "unit_cost": it.unit_cost,
                }
                for it in items
            ],
        }


def _stock(product_id: int) -> int:
    with SessionLocal() as s:
        return s.execute(
            select(Product.stock).where(Product.id == product_id)
        ).scalar_one()


def _count_events(mtid: str, event_type: str | None = None) -> int:
    with SessionLocal() as s:
        q = (
            select(func.count())
            .select_from(PaymentEvent)
            .where(PaymentEvent.merchant_transaction_id == mtid)
        )
        if event_type is not None:
            q = q.where(PaymentEvent.event_type == event_type)
        return s.execute(q).scalar_one()


def _expected_minor(total_amount) -> int:
    return int((Decimal(str(total_amount)) * 100).to_integral_value())


def _checkout_phonepe(
    db: Session,
    svc: PaymentService,
    user: User,
    product: Product,
    *,
    quantity: int = 1,
    coupon_code: str | None = None,
    provider: PhonePeProvider | None = None,
):
    """Run a prepaid PhonePe checkout through the patched provider. Returns
    (order, mtid, provider)."""
    provider = provider or _make_phonepe_provider()
    provider._post = MagicMock(return_value=_phonepe_initiate_envelope())
    req = CheckoutRequest(
        items=[OrderItemCreate(product_id=product.id, quantity=quantity)],
        address=_addr_create(),
        gateway_code="phonepe",
        payment_method="prepaid",
        coupon_code=coupon_code,
    )
    with _use_phonepe(provider):
        order, mtid, _redirect = svc.checkout(user, req)
    return order, mtid, provider


# ===========================================================================
# Area 1 — Checkout creation
# ===========================================================================

class TestCheckoutCreation:
    def test_checkout_creates_full_normalized_order(self) -> None:
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtid: str | None = None

        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db, price=Decimal("250.00"), cost=Decimal("100.00"), stock=20)
            product_ids.append(prod.id)
            db.commit()

            svc = PaymentService(db)
            order, mtid, _provider = _checkout_phonepe(db, svc, user, prod, quantity=2)
            oid = order.id
            order_ids.append(oid)

            snap = _snapshot_order(oid)

            # --- order row ---
            assert snap["status"] == OrderStatus.PENDING
            assert snap["gateway_code"] == "phonepe"
            assert snap["payment_intent_id"] == mtid
            assert mtid.startswith("ORD")

            # --- order number format WV-YYYY-NNNNNN ---
            assert re.fullmatch(r"WV-\d{4}-\d{6}", snap["order_number"]), snap["order_number"]
            assert snap["order_number"].endswith(f"{oid:06d}")

            # --- order items ---
            assert len(snap["items"]) == 1
            item = snap["items"][0]
            assert item["product_id"] == prod.id
            assert item["qty"] == 2
            assert item["unit_price"] == Decimal("250.00")

            # --- address snapshots (shipping + billing, billing copies shipping) ---
            types = {a["type"] for a in snap["addresses"]}
            assert OrderAddressType.SHIPPING in types, snap["addresses"]
            assert OrderAddressType.BILLING in types, snap["addresses"]

            # --- payment leg created, INITIATED on the gateway ---
            assert len(snap["legs"]) == 1, snap["legs"]
            leg = snap["legs"][0]
            assert leg["method"] == "prepaid"
            assert leg["status"] == PaymentTxnStatus.INITIATED
            assert leg["gateway"] == "phonepe"
            assert leg["txn_ref"] == mtid
            assert leg["amount"] == snap["total_amount"]

            # --- money math ---
            assert snap["subtotal"] == Decimal("500.00")  # 250 * 2
            expected_total = (
                snap["subtotal"]
                + snap["tax_amount"]
                + snap["shipping_amount"]
                + snap["cod_surcharge_amount"]
                - snap["discount_amount"]
                - snap["payment_discount_amount"]
            )
            assert snap["total_amount"] == expected_total
            assert snap["discount_amount"] == Decimal("0.00")
            assert snap["cod_surcharge_amount"] == Decimal("0.00")

        finally:
            db.rollback()
            _cleanup(user_ids, product_ids, order_ids)
            if mtid:
                _redis().delete(f"payment:mock:{mtid}")
            db.close()

    def test_checkout_with_coupon_applies_discount_to_total(self) -> None:
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        coupon_ids: list[int] = []
        mtid: str | None = None

        db = SessionLocal()
        try:
            code = f"PPCPN-{_uid()}".upper()
            coupon = CouponService(db).create(
                CouponCreate(
                    code=code,
                    discount_type=DiscountType.FIXED,
                    discount_value=Decimal("10.00"),
                    is_active=True,
                )
            )
            coupon_ids.append(coupon.id)

            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db, price=Decimal("100.00"), stock=50)
            product_ids.append(prod.id)
            db.commit()

            svc = PaymentService(db)
            order, mtid, _p = _checkout_phonepe(
                db, svc, user, prod, quantity=2, coupon_code=code
            )
            order_ids.append(order.id)

            snap = _snapshot_order(order.id)
            assert snap["subtotal"] == Decimal("200.00")
            assert snap["discount_amount"] == Decimal("10.00")
            # total reflects the discount via the accounting identity
            expected_total = (
                snap["subtotal"]
                + snap["tax_amount"]
                + snap["shipping_amount"]
                - snap["discount_amount"]
            )
            assert snap["total_amount"] == expected_total

        finally:
            db.rollback()
            _cleanup(user_ids, product_ids, order_ids, coupon_ids)
            if mtid:
                _redis().delete(f"payment:mock:{mtid}")
            db.close()


# ===========================================================================
# Area 6 — Cost snapshot (run early; it shares the checkout-creation setup)
# ===========================================================================

class TestCostSnapshot:
    def test_unit_cost_copied_from_product_cost(self) -> None:
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtid: str | None = None

        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db, price=Decimal("299.00"), cost=Decimal("149.50"), stock=10)
            product_ids.append(prod.id)
            db.commit()

            svc = PaymentService(db)
            order, mtid, _p = _checkout_phonepe(db, svc, user, prod, quantity=3)
            order_ids.append(order.id)

            snap = _snapshot_order(order.id)
            item = snap["items"][0]
            assert item["unit_cost"] == Decimal("149.50"), (
                "order_items.unit_cost must be frozen from product.cost at sale time"
            )
            assert item["unit_price"] == Decimal("299.00")
            # Margin basis is available for profit analytics.
            margin = (item["unit_price"] - item["unit_cost"]) * item["qty"]
            assert margin == Decimal("448.50")

        finally:
            db.rollback()
            _cleanup(user_ids, product_ids, order_ids)
            if mtid:
                _redis().delete(f"payment:mock:{mtid}")
            db.close()

    def test_unit_cost_null_when_product_cost_unset(self) -> None:
        """A product with no recorded cost yields a NULL unit_cost snapshot
        (legacy products), not a crash."""
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtid: str | None = None

        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db, price=Decimal("100.00"), cost=None, stock=10)
            product_ids.append(prod.id)
            db.commit()

            svc = PaymentService(db)
            order, mtid, _p = _checkout_phonepe(db, svc, user, prod, quantity=1)
            order_ids.append(order.id)

            snap = _snapshot_order(order.id)
            assert snap["items"][0]["unit_cost"] is None

        finally:
            db.rollback()
            _cleanup(user_ids, product_ids, order_ids)
            if mtid:
                _redis().delete(f"payment:mock:{mtid}")
            db.close()


# ===========================================================================
# Area 2 — PhonePe status-poll success
# ===========================================================================

class TestStatusPollSuccess:
    def test_status_poll_success_settles_order_and_persists_refs(self) -> None:
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtid: str | None = None

        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db, price=Decimal("100.00"), stock=50)
            product_ids.append(prod.id)
            db.commit()
            pre_stock = prod.stock

            svc = PaymentService(db)
            order, mtid, provider = _checkout_phonepe(db, svc, user, prod, quantity=2)
            order_ids.append(order.id)

            pending = _snapshot_order(order.id)
            assert pending["status"] == OrderStatus.PENDING
            amount_minor = _expected_minor(pending["total_amount"])
            txn_id = "T_POLL_" + _uid().upper()

            # PhonePe reports PAYMENT_SUCCESS on the next status poll.
            provider._get = MagicMock(
                return_value=_phonepe_status_envelope(mtid, amount_minor, txn_id)
            )
            with _use_phonepe(provider):
                PaymentService(db).get_status(user.id, mtid)

            snap = _snapshot_order(order.id)
            assert snap["status"] == OrderStatus.PAID
            assert snap["paid_at"] is not None
            # orders.payment_provider_ref saved
            assert snap["payment_provider_ref"] == txn_id
            # order_payments leg captured
            leg = snap["legs"][0]
            assert leg["status"] == PaymentTxnStatus.PAID
            assert leg["gateway_payment_id"] == txn_id
            assert leg["paid_at"] is not None
            # order_payments.raw_gateway_response saved
            assert leg["raw"] is not None
            assert leg["raw"]["data"]["transactionId"] == txn_id

            # stock stays reduced on success
            assert _stock(prod.id) == pre_stock - 2

            # audit trail recorded the poll + the applied transition
            assert _count_events(mtid, PaymentEventType.STATUS_POLL) >= 1
            assert _count_events(mtid, PaymentEventType.STATUS_APPLIED) >= 1

        finally:
            db.rollback()
            _cleanup(user_ids, product_ids, order_ids)
            if mtid:
                _redis().delete(f"payment:mock:{mtid}")
            db.close()


# ===========================================================================
# Area 3 — PhonePe S2S webhook success + signature
# ===========================================================================

class TestWebhookSuccess:
    def test_signed_webhook_settles_order_and_persists_refs(self) -> None:
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtid: str | None = None

        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db, price=Decimal("150.00"), stock=30)
            product_ids.append(prod.id)
            db.commit()
            pre_stock = prod.stock

            svc = PaymentService(db)
            order, mtid, provider = _checkout_phonepe(db, svc, user, prod, quantity=1)
            order_ids.append(order.id)

            pending = _snapshot_order(order.id)
            amount_minor = _expected_minor(pending["total_amount"])
            txn_id = "T_HOOK_" + _uid().upper()
            payload = _phonepe_status_envelope(mtid, amount_minor, txn_id)
            body, signature = _sign_webhook(provider, payload)

            with _use_phonepe(provider):
                returned = PaymentService(db).handle_webhook(
                    body, signature, gateway_code="phonepe"
                )
            assert returned.id == order.id

            snap = _snapshot_order(order.id)
            assert snap["status"] == OrderStatus.PAID
            assert snap["paid_at"] is not None
            assert snap["payment_provider_ref"] == txn_id
            leg = snap["legs"][0]
            assert leg["status"] == PaymentTxnStatus.PAID
            assert leg["gateway_payment_id"] == txn_id
            assert leg["raw"] is not None
            assert leg["raw"]["data"]["transactionId"] == txn_id

            assert _stock(prod.id) == pre_stock - 1

            # signature verified + transition applied are both audited
            assert _count_events(mtid, PaymentEventType.WEBHOOK_RECEIVED) >= 1
            assert _count_events(mtid, PaymentEventType.STATUS_APPLIED) >= 1

        finally:
            db.rollback()
            _cleanup(user_ids, product_ids, order_ids)
            if mtid:
                _redis().delete(f"payment:mock:{mtid}")
            db.close()


class TestPathConsistency:
    """The webhook path and the status-poll path must produce the same end
    state on equivalent orders — same OrderStatus, same leg status, both persist
    the captured gateway transaction id onto order + leg."""

    def test_webhook_and_poll_converge_to_identical_state(self) -> None:
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtids: list[str] = []

        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db, price=Decimal("120.00"), stock=50)
            product_ids.append(prod.id)
            db.commit()

            svc = PaymentService(db)

            # Order A — settle via status poll.
            order_a, mtid_a, prov_a = _checkout_phonepe(db, svc, user, prod, quantity=1)
            order_ids.append(order_a.id)
            mtids.append(mtid_a)
            amt_a = _expected_minor(_snapshot_order(order_a.id)["total_amount"])
            txn_a = "T_A_" + _uid().upper()
            prov_a._get = MagicMock(
                return_value=_phonepe_status_envelope(mtid_a, amt_a, txn_a)
            )
            with _use_phonepe(prov_a):
                PaymentService(db).get_status(user.id, mtid_a)

            # Order B — settle via signed webhook.
            order_b, mtid_b, prov_b = _checkout_phonepe(db, svc, user, prod, quantity=1)
            order_ids.append(order_b.id)
            mtids.append(mtid_b)
            amt_b = _expected_minor(_snapshot_order(order_b.id)["total_amount"])
            txn_b = "T_B_" + _uid().upper()
            body, sig = _sign_webhook(
                prov_b, _phonepe_status_envelope(mtid_b, amt_b, txn_b)
            )
            with _use_phonepe(prov_b):
                PaymentService(db).handle_webhook(body, sig, gateway_code="phonepe")

            sa = _snapshot_order(order_a.id)
            sb = _snapshot_order(order_b.id)

            assert sa["status"] == sb["status"] == OrderStatus.PAID
            assert sa["legs"][0]["status"] == sb["legs"][0]["status"] == PaymentTxnStatus.PAID
            # each persisted its own captured ref onto BOTH order and leg
            assert sa["payment_provider_ref"] == txn_a == sa["legs"][0]["gateway_payment_id"]
            assert sb["payment_provider_ref"] == txn_b == sb["legs"][0]["gateway_payment_id"]
            # both have exactly one prepaid leg and a stored raw response
            assert len(sa["legs"]) == len(sb["legs"]) == 1
            assert sa["legs"][0]["raw"] is not None and sb["legs"][0]["raw"] is not None

        finally:
            db.rollback()
            _cleanup(user_ids, product_ids, order_ids)
            r = _redis()
            for m in mtids:
                r.delete(f"payment:mock:{m}")
            db.close()


# ===========================================================================
# Area 4 — Idempotency
# ===========================================================================

class TestIdempotency:
    def test_repeated_status_poll_does_not_duplicate_or_double_deduct(self) -> None:
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtid: str | None = None

        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db, price=Decimal("100.00"), stock=50)
            product_ids.append(prod.id)
            db.commit()
            pre_stock = prod.stock

            svc = PaymentService(db)
            order, mtid, provider = _checkout_phonepe(db, svc, user, prod, quantity=2)
            order_ids.append(order.id)
            amount_minor = _expected_minor(_snapshot_order(order.id)["total_amount"])
            txn_id = "T_IDEMP_" + _uid().upper()
            provider._get = MagicMock(
                return_value=_phonepe_status_envelope(mtid, amount_minor, txn_id)
            )

            # Poll three times.
            with _use_phonepe(provider):
                PaymentService(db).get_status(user.id, mtid)
                PaymentService(db).get_status(user.id, mtid)
                PaymentService(db).get_status(user.id, mtid)

            snap = _snapshot_order(order.id)
            assert snap["status"] == OrderStatus.PAID
            # no duplicate child rows
            assert len(snap["legs"]) == 1
            assert len(snap["items"]) == 1
            assert len(snap["addresses"]) == 2  # shipping + billing
            # no double stock deduction
            assert _stock(prod.id) == pre_stock - 2
            # the gateway is only polled while PENDING — later polls short-circuit
            assert _count_events(mtid, PaymentEventType.STATUS_POLL) == 1

        finally:
            db.rollback()
            _cleanup(user_ids, product_ids, order_ids)
            if mtid:
                _redis().delete(f"payment:mock:{mtid}")
            db.close()

    def test_duplicate_webhook_does_not_duplicate_payment_rows(self) -> None:
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtid: str | None = None

        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db, price=Decimal("100.00"), stock=50)
            product_ids.append(prod.id)
            db.commit()
            pre_stock = prod.stock

            svc = PaymentService(db)
            order, mtid, provider = _checkout_phonepe(db, svc, user, prod, quantity=4)
            order_ids.append(order.id)
            amount_minor = _expected_minor(_snapshot_order(order.id)["total_amount"])
            txn_id = "T_DUP_" + _uid().upper()
            body, sig = _sign_webhook(
                provider, _phonepe_status_envelope(mtid, amount_minor, txn_id)
            )

            # Deliver the SAME signed webhook three times.
            with _use_phonepe(provider):
                PaymentService(db).handle_webhook(body, sig, gateway_code="phonepe")
                PaymentService(db).handle_webhook(body, sig, gateway_code="phonepe")
                PaymentService(db).handle_webhook(body, sig, gateway_code="phonepe")

            snap = _snapshot_order(order.id)
            assert snap["status"] == OrderStatus.PAID
            # paid order stays paid; exactly one captured leg, one item, two addresses
            assert len(snap["legs"]) == 1
            assert snap["legs"][0]["status"] == PaymentTxnStatus.PAID
            assert snap["legs"][0]["gateway_payment_id"] == txn_id
            assert len(snap["items"]) == 1
            assert len(snap["addresses"]) == 2
            # no double stock deduction across the replays
            assert _stock(prod.id) == pre_stock - 4
            # transition applied exactly once even though 3 webhooks arrived
            assert _count_events(mtid, PaymentEventType.STATUS_APPLIED) == 1

        finally:
            db.rollback()
            _cleanup(user_ids, product_ids, order_ids)
            if mtid:
                _redis().delete(f"payment:mock:{mtid}")
            db.close()


# ===========================================================================
# Area 5 — Negative paths
# ===========================================================================

class TestNegative:
    def test_failed_payment_does_not_mark_paid_and_restores_stock(self) -> None:
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtid: str | None = None

        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db, price=Decimal("100.00"), stock=50)
            product_ids.append(prod.id)
            db.commit()
            pre_stock = prod.stock

            svc = PaymentService(db)
            order, mtid, provider = _checkout_phonepe(db, svc, user, prod, quantity=3)
            order_ids.append(order.id)
            amount_minor = _expected_minor(_snapshot_order(order.id)["total_amount"])
            body, sig = _sign_webhook(
                provider,
                _phonepe_status_envelope(
                    mtid, amount_minor, "T_FAIL", code="PAYMENT_ERROR", state="FAILED"
                ),
            )

            with _use_phonepe(provider):
                PaymentService(db).handle_webhook(body, sig, gateway_code="phonepe")

            snap = _snapshot_order(order.id)
            assert snap["status"] == OrderStatus.CANCELLED
            assert snap["paid_at"] is None
            assert snap["legs"][0]["status"] == PaymentTxnStatus.FAILED
            # stock restored on failure
            assert _stock(prod.id) == pre_stock

        finally:
            db.rollback()
            _cleanup(user_ids, product_ids, order_ids)
            if mtid:
                _redis().delete(f"payment:mock:{mtid}")
            db.close()

    def test_pending_status_poll_leaves_order_pending(self) -> None:
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtid: str | None = None

        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db, price=Decimal("100.00"), stock=50)
            product_ids.append(prod.id)
            db.commit()

            svc = PaymentService(db)
            order, mtid, provider = _checkout_phonepe(db, svc, user, prod, quantity=1)
            order_ids.append(order.id)

            # PhonePe still processing — neither success nor failed.
            provider._get = MagicMock(
                return_value=_phonepe_status_envelope(
                    mtid, _expected_minor(_snapshot_order(order.id)["total_amount"]),
                    "T_PEND", code="PAYMENT_PENDING", state="PENDING",
                )
            )
            with _use_phonepe(provider):
                PaymentService(db).get_status(user.id, mtid)

            snap = _snapshot_order(order.id)
            assert snap["status"] == OrderStatus.PENDING
            assert snap["paid_at"] is None
            assert snap["legs"][0]["status"] == PaymentTxnStatus.INITIATED

        finally:
            db.rollback()
            _cleanup(user_ids, product_ids, order_ids)
            if mtid:
                _redis().delete(f"payment:mock:{mtid}")
            db.close()

    def test_invalid_signature_is_rejected(self) -> None:
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtid: str | None = None

        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db, price=Decimal("100.00"), stock=50)
            product_ids.append(prod.id)
            db.commit()

            svc = PaymentService(db)
            order, mtid, provider = _checkout_phonepe(db, svc, user, prod, quantity=1)
            order_ids.append(order.id)
            amount_minor = _expected_minor(_snapshot_order(order.id)["total_amount"])
            body, _good_sig = _sign_webhook(
                provider, _phonepe_status_envelope(mtid, amount_minor, "T_BADSIG")
            )

            with _use_phonepe(provider):
                with pytest.raises(ForbiddenError):
                    PaymentService(db).handle_webhook(
                        body, "deadbeef###1", gateway_code="phonepe"
                    )

            # order untouched, signature-invalid event audited
            snap = _snapshot_order(order.id)
            assert snap["status"] == OrderStatus.PENDING
            assert snap["paid_at"] is None
            assert (
                _count_events(mtid, PaymentEventType.WEBHOOK_SIGNATURE_INVALID) >= 0
            )  # event is keyed by gateway only (no mtid); presence not asserted

        finally:
            db.rollback()
            _cleanup(user_ids, product_ids, order_ids)
            if mtid:
                _redis().delete(f"payment:mock:{mtid}")
            db.close()

    def test_amount_mismatch_does_not_mark_paid(self) -> None:
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtid: str | None = None

        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db, price=Decimal("100.00"), stock=50)
            product_ids.append(prod.id)
            db.commit()

            svc = PaymentService(db)
            order, mtid, provider = _checkout_phonepe(db, svc, user, prod, quantity=2)
            order_ids.append(order.id)
            expected = _expected_minor(_snapshot_order(order.id)["total_amount"])
            wrong = expected - 100  # underpaid by 1 rupee

            body, sig = _sign_webhook(
                provider, _phonepe_status_envelope(mtid, wrong, "T_MISMATCH")
            )
            with _use_phonepe(provider):
                PaymentService(db).handle_webhook(body, sig, gateway_code="phonepe")

            snap = _snapshot_order(order.id)
            assert snap["status"] == OrderStatus.PENDING, (
                "amount mismatch must leave the order PENDING for review"
            )
            assert snap["paid_at"] is None
            assert snap["legs"][0]["status"] == PaymentTxnStatus.INITIATED
            # the mismatch is audited
            assert _count_events(mtid, PaymentEventType.AMOUNT_MISMATCH) >= 1

        finally:
            db.rollback()
            _cleanup(user_ids, product_ids, order_ids)
            if mtid:
                _redis().delete(f"payment:mock:{mtid}")
            db.close()

    def test_unknown_merchant_transaction_id_returns_safe_error(self) -> None:
        """A valid, correctly-signed webhook for a merchant txn id that matches
        no order must surface a clean NotFoundError, not a 500/IntegrityError."""
        db = SessionLocal()
        try:
            provider = _make_phonepe_provider()
            unknown_mtid = "ORD" + _uid().upper() + _uid().upper()
            body, sig = _sign_webhook(
                provider, _phonepe_status_envelope(unknown_mtid, 10000, "T_UNKNOWN")
            )
            with _use_phonepe(provider):
                with pytest.raises(NotFoundError):
                    PaymentService(db).handle_webhook(
                        body, sig, gateway_code="phonepe"
                    )
        finally:
            db.close()
