"""Revenue recognition: the sale in its own period, the reversal in its own.

The bug these tests exist for
-----------------------------
``MarginService`` used to reverse a refund **twice**. Once by excluding the
order from the revenue set (a refunded order's status is ``REFUNDED``, which is
not in ``_REVENUE_STATUSES``), and once again by subtracting ``refunds_minor``.
An order placed and fully refunded inside one window therefore reported::

    gross_merchandise_sales :     0.00   (order excluded — status is REFUNDED)
    refunds                 :  1000.00
    net_revenue             : -1000.00   <- should be 0.00
    bridge balances         :    True    <- the identity does NOT catch it

The bridge balanced through all of it, because both sides of the identity were
built from the same wrong inputs. A self-proving identity proves nothing when
the error is in the input it shares. That is why these tests assert **absolute
figures** and never merely ``balances() is True``.

The second half of the bug is quieter and worse: ``orders.status`` is MUTABLE.
Once refunded, an order is ``REFUNDED`` forever — so a January sale refunded in
March used to vanish from January retroactively, and a closed month moved.

What the fixed rule is
----------------------
* A sale is recognised in the period of ``orders.created_at`` if the order
  reached a paid state — **including** orders that have since been refunded.
* The reversal is a separate event, recognised in the period of its own
  ``refunded_at`` (``returns.refunded_at`` for returns-driven refunds).
* ``paid_order_value`` deliberately keeps the legacy definition (refunded orders
  leave the set entirely) because it is the shadow-mode parity anchor against
  ``DashboardService._revenue_summary``. Two definitions, both documented.

Isolation strategy
------------------
Every fixture lives in **2011**, years before this store's first order, so every
assertion is on an absolute figure rather than a delta. One calendar month per
scenario so a failed teardown in one test cannot perturb another. No conftest DB
fixture: each test owns a ``SessionLocal()`` and deletes exactly the rows it
created, in ``finally``, through a fresh session so a half-rolled-back
transaction cannot skip the cleanup.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Product
from app.models.return_request import ReturnItem, ReturnRequest, ReturnStatus
from app.models.user import User
from app.services.analytics.contracts import to_minor
from app.services.analytics.margin import MarginService
from app.services.analytics.types import MetricQuality
from app.services.dashboard_service import DashboardService

# ---------------------------------------------------------------------------
# The 2011 sandbox — one month per scenario
# ---------------------------------------------------------------------------

_YEAR = 2011

M_REPRO = 1  # placed and refunded inside one window
M_SALE = 2  # period attribution: the sale...
M_REVERSAL = 4  # ...and its reversal, two months later
M_PARTIAL = 5  # partial refund through a return
M_PARITY = 6  # paid_order_value parity anchor
M_BRIDGE = 7  # the identity under the new recognition
M_CANCELLED = 8  # never paid: in neither revenue nor refunds
M_TWICE = 9  # refunded at order level AND return level
M_UNVALUED = 10  # a refund with no determinable amount
M_RESIDUAL = 11  # the documented under-count, pinned
M_LATE = 12  # somewhere to put a refund that must fall outside a window


def _window(month: int) -> tuple[datetime, datetime]:
    """The half-open ``[first of month, first of next month)`` window."""
    start = datetime(_YEAR, month, 1, tzinfo=timezone.utc)
    end = (
        datetime(_YEAR + 1, 1, 1, tzinfo=timezone.utc)
        if month == 12
        else datetime(_YEAR, month + 1, 1, tzinfo=timezone.utc)
    )
    return start, end


def _at(month: int, day: int, hour: int = 10) -> datetime:
    return datetime(_YEAR, month, day, hour, tzinfo=timezone.utc)


def _paise(amount: str | int) -> int:
    return to_minor(Decimal(str(amount)))


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


class _Owned:
    """Ids this test created, so teardown deletes exactly them and nothing else."""

    def __init__(self) -> None:
        self.orders: list[int] = []
        self.products: list[int] = []
        self.users: list[int] = []
        self.returns: list[int] = []


def _create_user(db: Session, owned: _Owned) -> User:
    user = User(
        email=f"revrec-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.flush()
    owned.users.append(user.id)
    return user


def _create_product(db: Session, owned: _Owned, *, price: str) -> Product:
    product = Product(
        sku=f"SKU-REVREC-{_uid()}",
        name=f"RevRecTestProduct {_uid()}",
        price=Decimal(price),
        cost=Decimal(price) / 2,
        stock=100,
    )
    db.add(product)
    db.flush()
    owned.products.append(product.id)
    return product


def _create_order(
    db: Session,
    owned: _Owned,
    user: User,
    items: list[tuple[Product, int]],
    *,
    created_at: datetime,
    status: OrderStatus = OrderStatus.PAID,
    discount: str = "0",
    payment_discount: str = "0",
    tax: str = "0",
    shipping: str = "0",
    cod_surcharge: str = "0",
    payment_method: str = "prepaid",
    refunded_at: datetime | None = None,
    cancelled_at: datetime | None = None,
) -> Order:
    """An order whose ``total_amount`` satisfies the revenue-bridge identity.

    ``total = gross - discounts + tax + shipping + cod_surcharge``. The bridge
    measures net revenue from ``total_amount`` independently of the merchandise
    terms, so building the fixture this way keeps ``balances()`` an assertion
    about the aggregation rather than a restatement of it.
    """
    order_items: list[OrderItem] = []
    gross = Decimal("0.00")
    for product, qty in items:
        order_items.append(
            OrderItem(
                product_id=product.id,
                quantity=qty,
                unit_price=product.price,
                unit_cost=product.cost,
            )
        )
        gross += product.price * qty

    discounts = Decimal(discount) + Decimal(payment_discount)
    total = gross - discounts + Decimal(tax) + Decimal(shipping) + Decimal(cod_surcharge)
    is_cod = payment_method != "prepaid"

    order = Order(
        user_id=user.id,
        status=status,
        subtotal=gross,
        tax_amount=Decimal(tax),
        discount_amount=Decimal(discount),
        payment_discount_amount=Decimal(payment_discount),
        shipping_amount=Decimal(shipping),
        cod_surcharge_amount=Decimal(cod_surcharge),
        cod_balance=total if is_cod else Decimal("0.00"),
        total_amount=total,
        currency="INR",
        payment_method=payment_method,
        created_at=created_at,
        paid_at=None if status is OrderStatus.PENDING else created_at,
        refunded_at=refunded_at,
        cancelled_at=cancelled_at,
    )
    order.items = order_items
    db.add(order)
    db.flush()
    owned.orders.append(order.id)
    return order


def _create_refunded_return(
    db: Session,
    owned: _Owned,
    order: Order,
    *,
    refunded_at: datetime,
    refund_amount: str | None,
) -> ReturnRequest:
    """A return that reached the terminal ``refunded`` state.

    ``refund_amount=None`` is the real data gap this module tests: the column is
    nullable and is only stamped at approval, so a refund can be dated without
    ever being valued.
    """
    request = ReturnRequest(
        order_id=order.id,
        user_id=order.user_id,
        status=ReturnStatus.REFUNDED,
        reason="defective",
        refund_amount=None if refund_amount is None else Decimal(refund_amount),
        requested_at=refunded_at - timedelta(days=2),
        refunded_at=refunded_at,
    )
    request.items = [
        ReturnItem(order_item_id=item.id, quantity=item.quantity)
        for item in order.items
    ]
    db.add(request)
    db.flush()
    owned.returns.append(request.id)
    return request


def _cleanup(owned: _Owned) -> None:
    """Delete exactly what this test created, through a fresh session."""
    with SessionLocal() as s:
        if owned.returns:
            s.execute(
                text("DELETE FROM return_items WHERE return_id IN :ids"),
                {"ids": tuple(owned.returns)},
            )
            s.execute(
                text("DELETE FROM returns WHERE id IN :ids"),
                {"ids": tuple(owned.returns)},
            )
        if owned.orders:
            s.execute(
                text("DELETE FROM return_items WHERE return_id IN "
                     "(SELECT id FROM returns WHERE order_id IN :ids)"),
                {"ids": tuple(owned.orders)},
            )
            s.execute(
                text("DELETE FROM returns WHERE order_id IN :ids"),
                {"ids": tuple(owned.orders)},
            )
            s.execute(
                text("DELETE FROM order_items WHERE order_id IN :ids"),
                {"ids": tuple(owned.orders)},
            )
            s.execute(
                text("DELETE FROM shipments WHERE order_id IN :ids"),
                {"ids": tuple(owned.orders)},
            )
            s.execute(
                text("DELETE FROM orders WHERE id IN :ids"),
                {"ids": tuple(owned.orders)},
            )
        if owned.products:
            s.execute(
                text("DELETE FROM products WHERE id IN :ids"),
                {"ids": tuple(owned.products)},
            )
        if owned.users:
            s.execute(
                text("DELETE FROM points_transactions WHERE user_id IN :ids"),
                {"ids": tuple(owned.users)},
            )
            s.execute(
                text("DELETE FROM users WHERE id IN :ids"),
                {"ids": tuple(owned.users)},
            )
        s.commit()


# ---------------------------------------------------------------------------
# 1. The reproduction
# ---------------------------------------------------------------------------


class TestTheDoubleCount:
    """The confirmed defect, pinned as an exact figure."""

    def test_order_placed_and_fully_refunded_in_one_window_nets_to_zero(self) -> None:
        """The reproduction. Net revenue is 0.00, and emphatically not -1000.00.

        Before the fix the same fixture produced gms=0, refunds=1000,
        net_revenue=-1000 — and ``balances()`` still returned True, which is why
        every assertion here is on an absolute figure.
        """
        owned = _Owned()
        db = SessionLocal()
        try:
            start, end = _window(M_REPRO)
            user = _create_user(db, owned)
            product = _create_product(db, owned, price="1000.00")
            _create_order(
                db, owned, user, [(product, 1)],
                created_at=_at(M_REPRO, 5),
                status=OrderStatus.REFUNDED,
                refunded_at=_at(M_REPRO, 20),
            )
            db.commit()

            bridge = MarginService(db).revenue_bridge(start, end)

            # The sale happened. It belongs to the period it happened in.
            assert bridge.gross_merchandise_sales_minor == _paise("1000.00")
            # The reversal happened too, in the same period.
            assert bridge.refunds_minor == _paise("1000.00")
            # Recognised once each, so they cancel. THIS is the bug's epitaph.
            assert bridge.net_revenue_minor == 0
            assert bridge.net_revenue_minor != -_paise("1000.00")

            # The identity still closes — necessary, never sufficient.
            assert bridge.balances() is True
            assert bridge.imbalance_minor() == 0
        finally:
            db.close()
            _cleanup(owned)


# ---------------------------------------------------------------------------
# 2. Period attribution
# ---------------------------------------------------------------------------


class TestPeriodAttribution:
    """``orders.status`` is mutable; a closed month must not move when it changes."""

    def test_sale_stays_in_its_month_and_the_reversal_lands_in_its_own(self) -> None:
        """February keeps the whole sale; April takes the whole reversal.

        The load-bearing half is the re-read: February is measured BEFORE the
        refund exists and again AFTER, and the two must be identical. Under the
        old rule the second read returned zero — a closed month rewritten by an
        event that happened two months later.
        """
        owned = _Owned()
        db = SessionLocal()
        try:
            feb_start, feb_end = _window(M_SALE)
            apr_start, apr_end = _window(M_REVERSAL)
            user = _create_user(db, owned)
            product = _create_product(db, owned, price="1000.00")
            order = _create_order(
                db, owned, user, [(product, 1)],
                created_at=_at(M_SALE, 10),
                status=OrderStatus.PAID,
            )
            db.commit()

            service = MarginService(db)
            feb_before = service.revenue_bridge(feb_start, feb_end)
            assert feb_before.gross_merchandise_sales_minor == _paise("1000.00")
            assert feb_before.refunds_minor == 0
            assert feb_before.net_revenue_minor == _paise("1000.00")

            apr_before = service.revenue_bridge(apr_start, apr_end)
            assert apr_before.net_revenue_minor == 0

            # April: the refund is issued. Nothing about February changed in the
            # real world, so nothing about February may change in the numbers.
            order.status = OrderStatus.REFUNDED
            order.refunded_at = _at(M_REVERSAL, 15)
            db.commit()

            feb_after = service.revenue_bridge(feb_start, feb_end)
            assert feb_after.gross_merchandise_sales_minor == _paise("1000.00")
            assert feb_after.refunds_minor == 0
            assert feb_after.net_revenue_minor == _paise("1000.00")
            assert feb_after.net_revenue_minor == feb_before.net_revenue_minor
            assert feb_after.steps == feb_before.steps

            # April carries the reversal, and only the reversal: the order was
            # not created in April, so there is no sale here to net it against.
            apr_after = service.revenue_bridge(apr_start, apr_end)
            assert apr_after.gross_merchandise_sales_minor == 0
            assert apr_after.refunds_minor == _paise("1000.00")
            assert apr_after.net_revenue_minor == -_paise("1000.00")
            assert apr_after.balances() is True
        finally:
            db.close()
            _cleanup(owned)


# ---------------------------------------------------------------------------
# 3. Partial refunds
# ---------------------------------------------------------------------------


class TestPartialRefund:
    """A return refunds an amount, not an order."""

    def test_only_the_returned_amount_is_reversed(self) -> None:
        """A 300.00 return against a 1000.00 sale leaves 700.00 of net revenue.

        A partially refunded order keeps its paid status, so the sale side is
        untouched by the old rule as well — what this pins is that the reversal
        is valued at ``returns.refund_amount`` and not at the order total.
        """
        owned = _Owned()
        db = SessionLocal()
        try:
            start, end = _window(M_PARTIAL)
            user = _create_user(db, owned)
            product = _create_product(db, owned, price="1000.00")
            order = _create_order(
                db, owned, user, [(product, 1)],
                created_at=_at(M_PARTIAL, 3),
                status=OrderStatus.DELIVERED,
            )
            _create_refunded_return(
                db, owned, order,
                refunded_at=_at(M_PARTIAL, 21),
                refund_amount="300.00",
            )
            db.commit()

            bridge = MarginService(db).revenue_bridge(start, end)

            assert bridge.gross_merchandise_sales_minor == _paise("1000.00")
            assert bridge.refunds_minor == _paise("300.00")
            assert bridge.refunds_minor != _paise("1000.00")
            assert bridge.net_revenue_minor == _paise("700.00")
            assert bridge.balances() is True
        finally:
            db.close()
            _cleanup(owned)


# ---------------------------------------------------------------------------
# 4. The parity anchor
# ---------------------------------------------------------------------------


class TestPaidOrderValueParity:
    """The one figure that must NOT move. Shadow-mode reconciliation depends on it."""

    def test_paid_order_value_is_byte_identical_to_the_legacy_dashboard(self) -> None:
        """Computed alongside ``DashboardService._revenue_summary`` over the same
        window, on a fixture built to make every other definition disagree.

        If this ever fails, the shadow-mode comparison between the legacy pages
        and v2 stops meaning anything and there is no way left to prove the new
        numbers are right.
        """
        owned = _Owned()
        db = SessionLocal()
        try:
            start, end = _window(M_PARITY)
            user = _create_user(db, owned)
            kept = _create_product(db, owned, price="1000.00")
            reversed_later = _create_product(db, owned, price="500.00")
            never_paid = _create_product(db, owned, price="700.00")
            abandoned = _create_product(db, owned, price="300.00")

            _create_order(
                db, owned, user, [(kept, 1)],
                created_at=_at(M_PARITY, 4),
                status=OrderStatus.PAID, tax="50.00", shipping="40.00",
            )
            # Sold in June, refunded in December — the case the two definitions
            # disagree about, and the whole reason they are two definitions.
            _create_order(
                db, owned, user, [(reversed_later, 1)],
                created_at=_at(M_PARITY, 8),
                status=OrderStatus.REFUNDED, tax="25.00", shipping="20.00",
                refunded_at=_at(M_LATE, 5),
            )
            _create_order(
                db, owned, user, [(never_paid, 1)],
                created_at=_at(M_PARITY, 12),
                status=OrderStatus.CANCELLED, cancelled_at=_at(M_PARITY, 13),
            )
            _create_order(
                db, owned, user, [(abandoned, 1)],
                created_at=_at(M_PARITY, 16),
                status=OrderStatus.PENDING,
            )
            db.commit()

            service = MarginService(db)
            legacy = DashboardService(db)._revenue_summary(start, end)

            assert service.paid_order_value(start, end) == legacy["revenue"]
            assert service.paid_order_value(start, end) == Decimal("1090.00")

            recognition = service.recognised_revenue(start, end)
            assert recognition.paid_order_value_minor == _paise("1090.00")
            assert recognition.paid_order_value_minor == to_minor(legacy["revenue"])

            # ...and the two figures deliberately differ, by exactly the sale
            # that the legacy definition drops and this one keeps.
            assert recognition.net_revenue_minor == _paise("1635.00")
            assert recognition.net_revenue_minor != recognition.paid_order_value_minor
            assert (
                recognition.net_revenue_minor - recognition.paid_order_value_minor
                == _paise("545.00")
            )
        finally:
            db.close()
            _cleanup(owned)


# ---------------------------------------------------------------------------
# 5. The bridge, under the new recognition
# ---------------------------------------------------------------------------


class TestBridgeStillBalances:
    """The five-term identity must survive the change of recognition rule."""

    def test_identity_holds_with_a_sale_and_a_reversal_in_the_same_window(self) -> None:
        owned = _Owned()
        db = SessionLocal()
        try:
            start, end = _window(M_BRIDGE)
            user = _create_user(db, owned)
            a = _create_product(db, owned, price="250.00")
            b = _create_product(db, owned, price="120.00")
            c = _create_product(db, owned, price="900.00")

            _create_order(
                db, owned, user, [(a, 2), (b, 3)],
                created_at=_at(M_BRIDGE, 2),
                discount="60.00", payment_discount="25.00",
                tax="45.00", shipping="70.00", cod_surcharge="30.00",
                payment_method="cod",
            )
            _create_order(
                db, owned, user, [(c, 1)],
                created_at=_at(M_BRIDGE, 9),
                status=OrderStatus.REFUNDED,
                tax="45.00", shipping="60.00",
                refunded_at=_at(M_BRIDGE, 25),
            )
            db.commit()

            bridge = MarginService(db).revenue_bridge(start, end)

            # Both orders are recognised sales: one still paid, one paid-then-
            # reversed. Every merchandise term includes both.
            assert bridge.gross_merchandise_sales_minor == _paise("1760.00")
            assert bridge.discounts_minor == _paise("85.00")
            assert bridge.tax_minor == _paise("90.00")
            assert bridge.shipping_minor == _paise("130.00")
            assert bridge.cod_surcharge_minor == _paise("30.00")
            assert bridge.refunds_minor == _paise("1005.00")
            # (1760 - 85 + 90 + 130 + 30) - 1005
            assert bridge.net_revenue_minor == _paise("920.00")

            assert bridge.balances() is True
            assert bridge.imbalance_minor() == 0
            assert ("Net revenue", _paise("920.00")) in bridge.steps

            # And the identity is still capable of failing, so a True means
            # something: perturb one term and the gap is exactly the change.
            bridge.tax_minor += 137
            assert bridge.balances() is False
            assert bridge.imbalance_minor() == 137
        finally:
            db.close()
            _cleanup(owned)


# ---------------------------------------------------------------------------
# 6. Never paid
# ---------------------------------------------------------------------------


class TestNeverPaid:
    """Recognition is tied to the reversal being recognisable, not to a timestamp."""

    def test_cancelled_order_is_in_neither_revenue_nor_refunds(self) -> None:
        """A cancelled order never reached a paid state that survived, and no
        refund event is dated for it — so it must appear on NEITHER side.

        This is the trap in "include orders that were once paid": a PAID order
        cancelled by the customer has ``paid_at`` set and money really did go
        back, but ``cancelled_at`` is stamped and ``refunded_at`` is not. Keying
        recognition off ``paid_at`` would book the sale forever and the reversal
        never. Keying it off a recognisable reversal keeps the two symmetric.
        """
        owned = _Owned()
        db = SessionLocal()
        try:
            start, end = _window(M_CANCELLED)
            user = _create_user(db, owned)
            product = _create_product(db, owned, price="750.00")
            _create_order(
                db, owned, user, [(product, 1)],
                created_at=_at(M_CANCELLED, 6),
                status=OrderStatus.CANCELLED,
                tax="30.00", shipping="20.00",
                cancelled_at=_at(M_CANCELLED, 7),
            )
            db.commit()

            recognition = MarginService(db).recognised_revenue(start, end)

            assert recognition.gross_merchandise_sales_minor == 0
            assert recognition.refunds_minor == 0
            assert recognition.net_revenue_minor == 0
            assert recognition.quality is not MetricQuality.INCOMPLETE
            assert MarginService(db).paid_order_value(start, end) == Decimal("0")
        finally:
            db.close()
            _cleanup(owned)


# ---------------------------------------------------------------------------
# 7. De-duplication
# ---------------------------------------------------------------------------


class TestRefundedTwice:
    """The same money reversed through two tables must be counted once."""

    def test_order_level_and_return_level_refunds_do_not_double_reverse(self) -> None:
        """A whole-order refund on an order that also carries a refunded return.

        ``orders`` has no refund-amount column, so a whole-order refund is
        valued at ``orders.total_amount``; the return carries an explicit
        ``refund_amount``. Adding both would reverse the sale twice — the exact
        shape of the original bug, one table over.
        """
        owned = _Owned()
        db = SessionLocal()
        try:
            start, end = _window(M_TWICE)
            user = _create_user(db, owned)
            product = _create_product(db, owned, price="1000.00")
            order = _create_order(
                db, owned, user, [(product, 1)],
                created_at=_at(M_TWICE, 3),
                status=OrderStatus.REFUNDED,
                refunded_at=_at(M_TWICE, 18),
            )
            _create_refunded_return(
                db, owned, order,
                refunded_at=_at(M_TWICE, 18),
                refund_amount="1000.00",
            )
            db.commit()

            bridge = MarginService(db).revenue_bridge(start, end)

            # 1000.00 once, not 2000.00.
            assert bridge.refunds_minor == _paise("1000.00")
            assert bridge.refunds_minor != _paise("2000.00")
            # And the sale is recognised once, not once per refund row.
            assert bridge.gross_merchandise_sales_minor == _paise("1000.00")
            assert bridge.net_revenue_minor == 0
            assert bridge.balances() is True
        finally:
            db.close()
            _cleanup(owned)


# ---------------------------------------------------------------------------
# 8. Unknown is never zero
# ---------------------------------------------------------------------------


class TestUnvaluedRefund:
    """``returns.refund_amount`` is nullable. A dated, unvalued refund is a gap."""

    def test_refund_with_no_amount_is_incomplete_not_silently_zero(self) -> None:
        """``COALESCE(SUM(refund_amount), 0)`` would report this refund as 0.00
        and hand back a confident, wrong net revenue. It must report INCOMPLETE,
        name the row, and refuse to put a number on net revenue at all.
        """
        owned = _Owned()
        db = SessionLocal()
        try:
            start, end = _window(M_UNVALUED)
            user = _create_user(db, owned)
            product = _create_product(db, owned, price="800.00")
            order = _create_order(
                db, owned, user, [(product, 1)],
                created_at=_at(M_UNVALUED, 4),
                status=OrderStatus.DELIVERED,
            )
            unvalued = _create_refunded_return(
                db, owned, order,
                refunded_at=_at(M_UNVALUED, 19),
                refund_amount=None,
            )
            db.commit()

            recognition = MarginService(db).recognised_revenue(start, end)

            # The sale side is knowable and is still reported.
            assert recognition.gross_merchandise_sales_minor == _paise("800.00")
            # The reversal is not. Unknown is never a number.
            assert recognition.refunds_minor is None
            assert recognition.net_revenue_minor is None
            assert recognition.quality is MetricQuality.INCOMPLETE
            assert recognition.unvalued_refunds == 1
            assert any("refund_amount" in w for w in recognition.warnings)
            assert any(str(unvalued.id) in w for w in recognition.warnings)

            # ...and it is emphatically not the zero a COALESCE would produce.
            assert recognition.net_revenue_minor != _paise("800.00")
        finally:
            db.close()
            _cleanup(owned)


# ---------------------------------------------------------------------------
# 9. The residue, pinned rather than left to be discovered
# ---------------------------------------------------------------------------


class TestKnownUnderCount:
    """One case where the recognition rule gives a defensible-but-debatable answer."""

    def test_partial_return_then_whole_order_refund_under_reverses(self) -> None:
        """A 300.00 return followed by a full refund of a 1000.00 order.

        ``orders`` records no refund amount, so the whole-order refund can only
        be valued at ``total_amount``; the de-duplication rule therefore drops
        it entirely whenever a refunded return exists, and the window reverses
        300.00 rather than the 1000.00 that actually went back. Net revenue
        reads 700.00 for money the store no longer has.

        Fixing it means valuing the whole-order refund at ``total_amount`` minus
        the returns already reversed — which is precisely what
        ``OrderService.refund`` sends to the gateway — but that changes the
        ``refunds`` KPI definition, which the aggregation job mirrors term for
        term. Pinned here so the number is known rather than discovered, and so
        the day it is fixed this test fails loudly instead of quietly.
        """
        owned = _Owned()
        db = SessionLocal()
        try:
            start, end = _window(M_RESIDUAL)
            user = _create_user(db, owned)
            product = _create_product(db, owned, price="1000.00")
            order = _create_order(
                db, owned, user, [(product, 1)],
                created_at=_at(M_RESIDUAL, 2),
                status=OrderStatus.REFUNDED,
                refunded_at=_at(M_RESIDUAL, 20),
            )
            _create_refunded_return(
                db, owned, order,
                refunded_at=_at(M_RESIDUAL, 14),
                refund_amount="300.00",
            )
            db.commit()

            recognition = MarginService(db).recognised_revenue(start, end)

            assert recognition.gross_merchandise_sales_minor == _paise("1000.00")
            # Known under-count: 300.00 reversed, 1000.00 actually returned.
            assert recognition.refunds_minor == _paise("300.00")
            assert recognition.net_revenue_minor == _paise("700.00")
            # It is at least stated in words rather than left to be found.
            assert any("partially" in w for w in recognition.warnings)
        finally:
            db.close()
            _cleanup(owned)
