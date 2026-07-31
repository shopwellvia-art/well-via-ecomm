"""Tests for the order-table normalization (order_payments / shipments /
order_addresses + order_number), 2026-06-21.

Two layers:

* ``TestOrderSyncUnit`` — pure, in-memory tests of ``app.services.order_sync``.
  They build transient ORM objects and never touch the database, so they run
  anywhere (and validate the heart of the dual-write: one row per money
  movement, write-once address snapshots, COD-on-delivery settlement, the
  WV-YYYY-NNNNNN order number, and forward-only shipment status).

* ``TestOrderNormalizationDB`` — integration tests that exercise the real
  service + DB (order creation populates the new tables; admin detail returns
  them; legacy orders still load). They auto-SKIP when the database is
  unreachable so a blocked/remote DB doesn't turn into a red suite.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.models.order import Order, OrderItem, OrderStatus
from app.models.order_address import OrderAddressType
from app.models.order_payment import PaymentTxnStatus
from app.models.shipment import ShipmentStatus
from app.services import order_sync


def _order(**kwargs) -> Order:
    """A transient (un-persisted) Order with sensible defaults for unit tests."""
    defaults = dict(
        currency="INR",
        payment_method="prepaid",
        total_amount=Decimal("100.00"),
        cod_balance=Decimal("0.00"),
        payment_intent_id="ORDTESTREF",
        status=OrderStatus.PENDING,
    )
    defaults.update(kwargs)
    return Order(**defaults)


# --------------------------------------------------------------------------- #
# Unit tests — no database                                                     #
# --------------------------------------------------------------------------- #
class TestOrderSyncUnit:
    def test_make_order_number_format(self):
        when = datetime(2026, 6, 21, tzinfo=timezone.utc)
        assert order_sync.make_order_number(123, when) == "WV-2026-000123"
        assert order_sync.make_order_number(7, when) == "WV-2026-000007"

    def test_prepaid_order_gets_one_full_leg(self):
        o = _order(payment_method="prepaid", total_amount=Decimal("250.00"))
        order_sync.init_order_payments(o)
        assert len(o.payments) == 1
        leg = o.payments[0]
        assert leg.payment_method == "prepaid"
        assert leg.amount == Decimal("250.00")
        assert leg.payment_status == PaymentTxnStatus.PENDING
        assert leg.transaction_reference == "ORDTESTREF"

    def test_full_cod_gets_one_cod_leg(self):
        o = _order(
            payment_method="cod",
            total_amount=Decimal("100.00"),
            cod_balance=Decimal("100.00"),
        )
        order_sync.init_order_payments(o)
        assert len(o.payments) == 1
        assert o.payments[0].payment_method == "cod"
        assert o.payments[0].amount == Decimal("100.00")

    def test_split_cod_gets_two_legs_summing_to_total(self):
        o = _order(
            payment_method="split_cod",
            total_amount=Decimal("100.00"),
            cod_balance=Decimal("40.00"),
        )
        order_sync.init_order_payments(o)
        assert len(o.payments) == 2
        methods = {p.payment_method: p.amount for p in o.payments}
        assert methods["split_cod"] == Decimal("60.00")
        assert methods["cod"] == Decimal("40.00")
        assert sum(p.amount for p in o.payments) == o.total_amount

    def test_init_payments_is_idempotent(self):
        o = _order()
        order_sync.init_order_payments(o)
        order_sync.init_order_payments(o)
        assert len(o.payments) == 1

    def test_mark_prepaid_paid_settles_gateway_leg_only(self):
        o = _order(
            payment_method="split_cod",
            total_amount=Decimal("100.00"),
            cod_balance=Decimal("40.00"),
        )
        order_sync.init_order_payments(o)
        order_sync.mark_prepaid_paid(o)
        gateway = next(p for p in o.payments if p.payment_method == "split_cod")
        cod = next(p for p in o.payments if p.payment_method == "cod")
        assert gateway.payment_status == PaymentTxnStatus.PAID
        assert gateway.paid_at is not None
        # COD is collected on delivery, NOT now.
        assert cod.payment_status == PaymentTxnStatus.PENDING

    def test_mark_prepaid_paid_is_noop_for_pure_cod(self):
        o = _order(
            payment_method="cod",
            total_amount=Decimal("100.00"),
            cod_balance=Decimal("100.00"),
        )
        order_sync.init_order_payments(o)
        order_sync.mark_prepaid_paid(o)
        assert o.payments[0].payment_status == PaymentTxnStatus.PENDING

    def test_mark_cod_collected_on_delivery(self):
        o = _order(
            payment_method="cod",
            total_amount=Decimal("100.00"),
            cod_balance=Decimal("100.00"),
        )
        order_sync.init_order_payments(o)
        order_sync.mark_cod_collected(o)
        assert o.payments[0].payment_status == PaymentTxnStatus.PAID
        assert o.payments[0].paid_at is not None

    def test_mark_prepaid_failed(self):
        o = _order(payment_method="prepaid")
        order_sync.init_order_payments(o)
        order_sync.mark_prepaid_failed(o)
        assert o.payments[0].payment_status == PaymentTxnStatus.FAILED
        assert o.payments[0].failed_at is not None

    def test_refund_splits_captured_vs_uncaptured(self):
        o = _order(
            payment_method="split_cod",
            total_amount=Decimal("100.00"),
            cod_balance=Decimal("40.00"),
        )
        order_sync.init_order_payments(o)
        order_sync.mark_prepaid_paid(o)  # gateway leg PAID, cod leg PENDING
        order_sync.mark_payments_refunded(o)
        gateway = next(p for p in o.payments if p.payment_method == "split_cod")
        cod = next(p for p in o.payments if p.payment_method == "cod")
        assert gateway.payment_status == PaymentTxnStatus.REFUNDED
        assert cod.payment_status == PaymentTxnStatus.CANCELLED

    def test_addresses_from_snapshots(self):
        snap = {
            "full_name": "Asha Rao",
            "phone": "9876500000",
            "line1": "42 MG Road",
            "line2": "Flat 3B",
            "landmark": "Near Park",
            "city": "Bengaluru",
            "state": "Karnataka",
            "pincode": "560001",
            "country": "IN",
        }
        o = _order(
            shipping_address_snapshot=snap,
            billing_address_snapshot=dict(snap, full_name="Asha R (billing)"),
            shipping_pincode="560001",
        )
        order_sync.sync_order_addresses(o, email="asha@example.com")
        by_type = {a.address_type: a for a in o.addresses}
        assert set(by_type) == {OrderAddressType.SHIPPING, OrderAddressType.BILLING}
        ship = by_type[OrderAddressType.SHIPPING]
        assert ship.full_name == "Asha Rao"
        assert ship.address_line1 == "42 MG Road"
        assert ship.address_line2 == "Flat 3B"
        assert ship.city == "Bengaluru"
        assert ship.pincode == "560001"
        assert ship.email == "asha@example.com"
        assert by_type[OrderAddressType.BILLING].full_name == "Asha R (billing)"

    def test_addresses_legacy_freetext(self):
        o = _order(
            shipping_address_snapshot=None,
            shipping_address="42 Test Lane, Bengaluru 560001",
            shipping_pincode="560001",
        )
        order_sync.sync_order_addresses(o)
        assert len(o.addresses) == 1
        assert o.addresses[0].address_type == OrderAddressType.SHIPPING
        assert o.addresses[0].address_line1 == "42 Test Lane, Bengaluru 560001"

    def test_addresses_billing_none_means_no_redundant_row(self):
        o = _order(
            shipping_address_snapshot={"full_name": "X", "line1": "Y", "pincode": "1"},
            billing_address_snapshot=None,
        )
        order_sync.sync_order_addresses(o)
        assert [a.address_type for a in o.addresses] == [OrderAddressType.SHIPPING]

    def test_addresses_are_write_once(self):
        o = _order(shipping_address_snapshot={"line1": "Y"})
        order_sync.sync_order_addresses(o)
        order_sync.sync_order_addresses(o)  # second call must not duplicate
        assert len(o.addresses) == 1

    def test_sync_shipment_from_order(self):
        o = _order(
            status=OrderStatus.SHIPPED,
            shipping_provider="delhivery",
            shipping_awb="AWB123",
            tracking_number="AWB123",
            shipped_at=datetime(2026, 6, 21, tzinfo=timezone.utc),
        )
        order_sync.sync_shipment_from_order(o)
        assert len(o.shipments) == 1
        s = o.shipments[0]
        assert s.courier_partner == "delhivery"
        assert s.awb_number == "AWB123"
        assert s.shipment_status == ShipmentStatus.SHIPPED
        assert s.shipped_at is not None

    def test_set_shipment_status_keeps_finer_granularity(self):
        o = _order(status=OrderStatus.SHIPPED, shipping_awb="AWB9")
        order_sync.sync_shipment_from_order(o)
        order_sync.set_shipment_status(o, ShipmentStatus.OUT_FOR_DELIVERY)
        s = o.shipments[0]
        assert s.shipment_status == ShipmentStatus.OUT_FOR_DELIVERY
        assert s.out_for_delivery_at is not None

    def test_set_shipment_status_does_not_unwind_terminal(self):
        o = _order(status=OrderStatus.DELIVERED, shipping_awb="AWB9")
        order_sync.sync_shipment_from_order(o)  # DELIVERED (terminal)
        order_sync.set_shipment_status(o, ShipmentStatus.IN_TRANSIT)
        assert o.shipments[0].shipment_status == ShipmentStatus.DELIVERED


# --------------------------------------------------------------------------- #
# Integration tests — real DB (auto-skip when unreachable)                     #
# --------------------------------------------------------------------------- #
@pytest.fixture
def db():
    """A live DB session, or skip the test if the database is unreachable
    (e.g. the remote host isn't whitelisting this runner's IP)."""
    from sqlalchemy.exc import OperationalError

    from app.db.session import SessionLocal

    try:
        session = SessionLocal()
        session.execute  # noqa: B018 - touch attribute
        from sqlalchemy import text

        session.execute(text("SELECT 1"))
    except OperationalError as exc:  # pragma: no cover - environmental
        pytest.skip(f"database unreachable: {exc}")
    try:
        yield session
    finally:
        session.close()


class TestOrderNormalizationDB:
    def _seed_user_and_product(self, db):
        """Reuse ambient rows when present, otherwise create our own.

        This used to assert on whatever happened to be in the database, which
        made the test pass locally (a dev DB has products) and fail on a clean
        CI database, where `alembic upgrade head` creates the schema and no
        rows. Worse, it passed or failed depending on what earlier tests in the
        run had left behind. Creating the fixture is what the rest of the suite
        does (see `_make_product` in test_cod_and_split_checkout.py et al).
        """
        import uuid

        from app.core.security import hash_password
        from app.models.product import Product
        from app.models.user import User

        user = db.query(User).first()
        if user is None:
            user = User(
                email=f"ordnorm-{uuid.uuid4().hex[:10]}@example.com",
                hashed_password=hash_password("TestPass123!"),
                is_active=True,
            )
            db.add(user)
            db.flush()

        product = db.query(Product).filter(Product.stock > 0).first()
        if product is None:
            uid = uuid.uuid4().hex[:10]
            product = Product(
                sku=f"SKU-ORDNORM-{uid}",
                name=f"OrdNormTestProd-{uid}",
                price=Decimal("100.00"),
                stock=50,
            )
            db.add(product)
            db.flush()

        return user, product

    def test_create_order_populates_payments_and_addresses(self, db):
        from app.schemas.order import OrderCreate, OrderItemCreate
        from app.services.order_service import OrderService

        user, product = self._seed_user_and_product(db)
        order = OrderService(db).create(
            user.id,
            OrderCreate(
                items=[OrderItemCreate(product_id=product.id, quantity=1)],
                shipping_address="42 Test Lane, Bengaluru 560001",
            ),
        )
        try:
            assert order.order_number and order.order_number.startswith("WV-")
            assert len(order.payments) == 1
            assert any(
                a.address_type == OrderAddressType.SHIPPING for a in order.addresses
            )
        finally:
            db.delete(order)  # cascade removes children
            db.commit()

    def test_payment_success_marks_order_payment_paid(self, db):
        from app.schemas.order import OrderCreate, OrderItemCreate
        from app.services.order_service import OrderService

        user, product = self._seed_user_and_product(db)
        order = OrderService(db).create(
            user.id,
            OrderCreate(
                items=[OrderItemCreate(product_id=product.id, quantity=1)],
                shipping_address="42 Test Lane, Bengaluru 560001",
            ),
        )
        try:
            order_sync.mark_prepaid_paid(order)
            db.flush()
            assert order.payments[0].payment_status == PaymentTxnStatus.PAID
            assert order.payments[0].paid_at is not None
        finally:
            db.delete(order)
            db.commit()

    def test_admin_detail_returns_normalized_children(self, db):
        from app.schemas.order import OrderCreate, OrderItemCreate
        from app.services.order_service import OrderService

        user, product = self._seed_user_and_product(db)
        svc = OrderService(db)
        created = svc.create(
            user.id,
            OrderCreate(
                items=[OrderItemCreate(product_id=product.id, quantity=1)],
                shipping_address="42 Test Lane, Bengaluru 560001",
            ),
        )
        try:
            loaded = svc.admin_get(created.id)  # exercises eager-load path
            assert loaded.order_number
            assert len(loaded.payments) >= 1
            assert len(loaded.addresses) >= 1
            # legacy/flat fields still present (backward compatibility)
            assert loaded.total_amount == created.total_amount
        finally:
            db.delete(created)
            db.commit()
