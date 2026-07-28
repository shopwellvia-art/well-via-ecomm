"""Tests for the rollup aggregation runner and its first three jobs.

What actually has to be true here
---------------------------------
An aggregation job that computes the wrong number fails loudly the first time
someone reads the dashboard. An aggregation job that computes the *right* number
and then computes it again on top of itself fails silently, forever, and looks
like a good week. So the tests carrying the weight are the ones about running
things twice:

  * ``test_order_daily_is_idempotent`` — the same bucket, twice, must leave
    identical column values. Not "no error", not "no new row": the same numbers.
    An ``ON DUPLICATE KEY UPDATE col = col + VALUES(col)`` passes every other
    test in this file and fails this one.
  * ``test_product_daily_removes_a_group_key_that_disappeared`` — the case a
    plain upsert cannot express. Delete the only order containing a product and
    the product's row must *go*, not linger at yesterday's units.
  * ``test_revenue_bridge_identity_holds_on_the_written_row`` — the five-term
    identity, asserted on the row that was actually written, with all five
    components non-zero.
  * ``test_store_local_day_bucketing`` — two orders on the same **UTC** date that
    belong to two different **store-local** reporting days. A UTC-day bucketer
    puts them together and is wrong by 5.5 hours' worth of trade every day.

Isolation strategy
------------------
Every fixture lives in **June 2009**, years before this store's first order, so
assertions are absolute ("net_revenue is exactly 1180.00") rather than deltas
against whatever the shared throwaway MySQL already holds.
``_assert_sandbox_is_empty`` fails loudly if anything foreign is in the window,
because a stray order would not make these tests noisy — it would make them
wrong.

Teardown deletes every owned row through a fresh session, including the
``agg_*`` rows for the sandbox dates and the ``analytics_sync_runs`` rows written
under this module's worker id. No db fixture exists in ``conftest.py``; each test
owns its ``SessionLocal()`` and closes it in ``finally``.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.analytics_control import (
    AnalyticsRecomputeQueue,
    AnalyticsSyncRun,
    RecomputeReason,
    RecomputeStatus,
    SyncStatus,
    SyncTrigger,
)
from app.models.analytics_rollups import AggOrderDaily, AggOrderHourly, AggProductDaily
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Category, Product
from app.models.return_request import ReturnItem, ReturnRequest, ReturnStatus
from app.models.user import User
from app.services.analytics.aggregation import JOBS, AggregationRunner
from app.services.analytics.contracts import to_minor
from app.services.analytics.margin import MarginService
from app.services.analytics.queue import RecomputeQueue
from app.services.analytics.timebox import (
    active_generation,
    day_bounds_utc,
    local_day,
    store_timezone,
)

# ---------------------------------------------------------------------------
# The June 2009 sandbox
# ---------------------------------------------------------------------------

SANDBOX_FIRST = date(2009, 6, 1)
SANDBOX_LAST = date(2009, 6, 30)

DAY_IDEMPOTENT = date(2009, 6, 3)
DAY_ACCUMULATE = date(2009, 6, 4)
DAY_PRODUCT = date(2009, 6, 5)
DAY_BRIDGE = date(2009, 6, 6)
#: Two consecutive store-local days whose orders share one UTC date.
DAY_TZ_A = date(2009, 6, 7)
DAY_TZ_B = date(2009, 6, 8)
BUDGET_FROM = date(2009, 6, 10)
BUDGET_TO = date(2009, 6, 12)
DAY_REFUNDED = date(2009, 6, 14)
DAY_REFUND_ISSUED = date(2009, 6, 20)
DAY_COST = date(2009, 6, 15)
DAY_SYNC = date(2009, 6, 16)
DAY_RETURN_SALE = date(2009, 6, 17)
DAY_RETURN_REFUND = date(2009, 6, 18)
DAY_QUEUE = date(2009, 6, 19)

#: Distinctive enough that teardown can delete this module's run log without a
#: date filter, and that a stuck row in a shared DB is traceable to these tests.
WORKER_ID = "test-agg-runner"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _at(db: Session, day: date, hour: int, minute: int = 0) -> datetime:
    """The UTC instant that is ``hour:minute`` **store-local** on ``day``.

    Built from ``timebox.day_bounds_utc`` rather than by hand: the point of these
    tests is that the day boundary is the store's, and deriving the fixture from
    the same function the job uses is what makes the assertion about bucketing
    rather than about two independent copies of the same arithmetic.
    """
    start, _ = day_bounds_utc(day, store_timezone(db))
    return start + timedelta(hours=hour, minutes=minute)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


class _Owned:
    """Ids this test created, so teardown removes exactly them."""

    def __init__(self) -> None:
        self.orders: list[int] = []
        self.products: list[int] = []
        self.users: list[int] = []
        self.categories: list[int] = []
        self.returns: list[int] = []


def _create_user(db: Session, owned: _Owned) -> User:
    user = User(
        email=f"aggtest-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.flush()
    owned.users.append(user.id)
    return user


def _create_category(db: Session, owned: _Owned) -> Category:
    uid = _uid()
    category = Category(name=f"AggTestCat-{uid}", slug=f"aggtest-{uid}")
    db.add(category)
    db.flush()
    owned.categories.append(category.id)
    return category


def _create_product(
    db: Session,
    owned: _Owned,
    *,
    price: str,
    cost: str | None = None,
    category: Category | None = None,
) -> Product:
    product = Product(
        sku=f"SKU-AGG-{_uid()}",
        name=f"AggTestProduct {_uid()}",
        price=Decimal(price),
        # None means "never snapshotted" — the case `costed_units` exists to
        # keep visible instead of coalescing to zero.
        cost=None if cost is None else Decimal(cost),
        stock=100,
        category_id=category.id if category else None,
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
) -> Order:
    """An order whose ``total_amount`` satisfies the revenue-bridge identity.

    ``total = gross - discounts + tax + shipping + cod_surcharge``, exactly as
    checkout builds it. The job measures ``net_revenue`` independently from
    ``total_amount`` rather than deriving it from the five components, so
    constructing the fixture this way is what makes the bridge assertion a
    statement about the aggregation instead of a restatement of it.
    """
    order_items: list[OrderItem] = []
    gross = Decimal("0.00")
    for product, quantity in items:
        order_items.append(
            OrderItem(
                product_id=product.id,
                quantity=quantity,
                unit_price=product.price,
                unit_cost=product.cost,
            )
        )
        gross += product.price * quantity

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
        refunded_at=refunded_at,
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
    lines: list[tuple[OrderItem, int]],
    *,
    refunded_at: datetime,
    refund_amount: str,
) -> ReturnRequest:
    """A return that reached the terminal ``refunded`` state."""
    request = ReturnRequest(
        order_id=order.id,
        user_id=order.user_id,
        status=ReturnStatus.REFUNDED,
        reason="defective",
        refund_amount=Decimal(refund_amount),
        requested_at=refunded_at - timedelta(days=1),
        refunded_at=refunded_at,
    )
    request.items = [
        ReturnItem(order_item_id=item.id, quantity=quantity) for item, quantity in lines
    ]
    db.add(request)
    db.flush()
    owned.returns.append(request.id)
    return request


# ---------------------------------------------------------------------------
# Teardown + preconditions
# ---------------------------------------------------------------------------


def _cleanup(owned: _Owned) -> None:
    """Delete everything this test owned, through a fresh session.

    A fresh session so teardown cannot be skipped by a half-rolled-back
    transaction in the test's own session. The rollup and run-log rows go by
    sandbox date / worker id rather than by id, because they are written by
    Core statements the test never sees the ids of.
    """
    with SessionLocal() as session:
        if owned.returns:
            session.execute(
                text("DELETE FROM return_items WHERE return_id IN :ids"),
                {"ids": tuple(owned.returns)},
            )
            session.execute(
                text("DELETE FROM returns WHERE id IN :ids"),
                {"ids": tuple(owned.returns)},
            )
        if owned.orders:
            session.execute(
                text("DELETE FROM order_items WHERE order_id IN :ids"),
                {"ids": tuple(owned.orders)},
            )
            session.execute(
                text("DELETE FROM orders WHERE id IN :ids"),
                {"ids": tuple(owned.orders)},
            )
        if owned.products:
            session.execute(
                text("DELETE FROM products WHERE id IN :ids"),
                {"ids": tuple(owned.products)},
            )
        if owned.users:
            session.execute(
                text("DELETE FROM points_transactions WHERE user_id IN :ids"),
                {"ids": tuple(owned.users)},
            )
            session.execute(
                text("DELETE FROM users WHERE id IN :ids"),
                {"ids": tuple(owned.users)},
            )
        if owned.categories:
            session.execute(
                text("DELETE FROM categories WHERE id IN :ids"),
                {"ids": tuple(owned.categories)},
            )

        window = {"first": SANDBOX_FIRST, "last": SANDBOX_LAST}
        for table in (
            "agg_order_daily",
            "agg_order_hourly",
            "agg_product_daily",
            "analytics_recompute_queue",
        ):
            session.execute(
                text(
                    f"DELETE FROM {table} WHERE bucket_date BETWEEN :first AND :last"
                ),
                window,
            )
        session.execute(
            text("DELETE FROM analytics_sync_runs WHERE worker_id = :worker"),
            {"worker": WORKER_ID},
        )
        session.commit()


def _assert_sandbox_is_empty(db: Session) -> None:
    """Fail loudly if anything foreign already sits in the 2009 window.

    Every expected figure below is absolute, so a stray order would not make
    these tests flaky — it would make them quietly wrong. A clear failure here
    beats a mystifying one thirty lines later.
    """
    tz = store_timezone(db)
    start, _ = day_bounds_utc(SANDBOX_FIRST, tz)
    _, end = day_bounds_utc(SANDBOX_LAST, tz)
    foreign = db.execute(
        select(Order.id).where(Order.created_at >= start, Order.created_at < end)
    ).scalars().all()
    assert not foreign, (
        f"orders {foreign} already exist in the June 2009 sandbox window; these "
        "tests assert absolute figures and cannot share the window"
    )


# ---------------------------------------------------------------------------
# Reading rollup rows back
# ---------------------------------------------------------------------------

#: Bookkeeping, not measurement. Two runs of the same bucket differ in these and
#: must be identical in everything else.
_NON_MEASURE_COLUMNS = frozenset({"id", "computed_at"})


def _measures(row) -> dict:
    """Every measured column of a rollup row, keyed by column name."""
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name not in _NON_MEASURE_COLUMNS
    }


def _daily(db: Session, bucket: date, generation: int) -> AggOrderDaily | None:
    db.expire_all()
    return db.execute(
        select(AggOrderDaily).where(
            AggOrderDaily.bucket_date == bucket,
            AggOrderDaily.tz_generation == generation,
        )
    ).scalars().first()


def _hourly(db: Session, bucket: date, generation: int) -> list[AggOrderHourly]:
    db.expire_all()
    return list(
        db.execute(
            select(AggOrderHourly)
            .where(
                AggOrderHourly.bucket_date == bucket,
                AggOrderHourly.tz_generation == generation,
            )
            .order_by(AggOrderHourly.bucket_hour)
        )
        .scalars()
        .all()
    )


def _products(db: Session, bucket: date, generation: int) -> dict[int, AggProductDaily]:
    db.expire_all()
    rows = (
        db.execute(
            select(AggProductDaily).where(
                AggProductDaily.bucket_date == bucket,
                AggProductDaily.tz_generation == generation,
            )
        )
        .scalars()
        .all()
    )
    return {int(row.product_id): row for row in rows}


def _runner(db: Session, **kwargs) -> AggregationRunner:
    return AggregationRunner(db, worker_id=WORKER_ID, **kwargs)


class _StepClock:
    """A monotonic clock that returns a scripted sequence of seconds.

    Budget exhaustion has real consequences — a ``partial`` status, a watermark,
    a resumable next tick — and pinning that behaviour to wall-clock timing would
    make the test either flaky or slow. The last value repeats forever so the
    script only has to cover the interesting calls.
    """

    def __init__(self, values: list[float]) -> None:
        self._values = values
        self.calls = 0

    def __call__(self) -> float:
        value = self._values[min(self.calls, len(self._values) - 1)]
        self.calls += 1
        return value


# ===========================================================================
# 1. Idempotency — the one that matters most
# ===========================================================================


def test_order_daily_is_idempotent() -> None:
    """Two runs of the same bucket leave identical values, not doubled ones.

    Asserted on the actual column values, not on the row count: a job that
    accumulated (``col = col + VALUES(col)``) would still write exactly one row
    and would still report success. The numbers are the only witness.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_sandbox_is_empty(db)
        generation = active_generation(db).generation

        user = _create_user(db, owned)
        product = _create_product(db, owned, price="500.00", cost="200.00")
        _create_order(
            db,
            owned,
            user,
            [(product, 2)],
            created_at=_at(db, DAY_IDEMPOTENT, 11),
            tax="90.00",
            shipping="60.00",
        )
        db.commit()

        runner = _runner(db)
        first = runner.run_bucket("order_daily", DAY_IDEMPOTENT)
        before = _measures(_daily(db, DAY_IDEMPOTENT, generation))

        second = runner.run_bucket("order_daily", DAY_IDEMPOTENT)
        after = _measures(_daily(db, DAY_IDEMPOTENT, generation))

        assert first.rows_written == 1 and second.rows_written == 1
        assert before == after, (
            "re-running one bucket changed its values; the upsert is "
            f"accumulating rather than overwriting. before={before} after={after}"
        )
        # Pin the absolute figures too, so "identical" cannot be "identically
        # zero because the job silently did nothing".
        assert before["orders_total"] == 1
        assert before["units"] == 2
        assert before["gross_merchandise_sales"] == Decimal("1000.00")
        assert before["net_revenue"] == Decimal("1150.00")
        assert before["cogs_sum"] == Decimal("400.00")

        one_row = db.execute(
            select(AggOrderDaily).where(
                AggOrderDaily.bucket_date == DAY_IDEMPOTENT,
                AggOrderDaily.tz_generation == generation,
            )
        ).scalars().all()
        assert len(one_row) == 1, "the UNIQUE key did not collapse the second run"
    finally:
        _cleanup(owned)
        db.close()


# ===========================================================================
# 2. A new order restates the day rather than adding to it
# ===========================================================================


def test_rerun_after_a_new_order_reports_the_new_total() -> None:
    """A second order makes the day A+B, never A + (A+B)."""
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_sandbox_is_empty(db)
        generation = active_generation(db).generation

        user = _create_user(db, owned)
        product = _create_product(db, owned, price="250.00", cost="100.00")
        _create_order(
            db, owned, user, [(product, 1)], created_at=_at(db, DAY_ACCUMULATE, 9)
        )
        db.commit()

        runner = _runner(db)
        runner.run_bucket("order_daily", DAY_ACCUMULATE)
        first = _daily(db, DAY_ACCUMULATE, generation)
        assert first.net_revenue == Decimal("250.00")

        _create_order(
            db, owned, user, [(product, 3)], created_at=_at(db, DAY_ACCUMULATE, 14)
        )
        db.commit()

        runner.run_bucket("order_daily", DAY_ACCUMULATE)
        second = _daily(db, DAY_ACCUMULATE, generation)

        assert second.net_revenue == Decimal("1000.00"), (
            "expected the recomputed day total (250 + 750); an accumulating "
            f"upsert would report 1250.00, got {second.net_revenue}"
        )
        assert second.orders_total == 2
        assert second.units == 4
        assert second.distinct_customers == 1, (
            "one customer placed both orders; a distinct count must not be summed"
        )
    finally:
        _cleanup(owned)
        db.close()


# ===========================================================================
# 3. Dimensioned bucket: delete-and-reinsert
# ===========================================================================


def test_product_daily_removes_a_group_key_that_disappeared() -> None:
    """Deleting the only order for a product removes its row on the next run.

    This is the case a plain upsert cannot express: it writes the products that
    *are* there and has no statement to make about the one that is not, so the
    stale row survives at yesterday's units — in the table that drives the
    best-seller leaderboard.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_sandbox_is_empty(db)
        generation = active_generation(db).generation

        user = _create_user(db, owned)
        category = _create_category(db, owned)
        kept = _create_product(db, owned, price="300.00", cost="120.00", category=category)
        doomed = _create_product(db, owned, price="150.00", cost="60.00")

        _create_order(db, owned, user, [(kept, 2)], created_at=_at(db, DAY_PRODUCT, 10))
        vanishing = _create_order(
            db, owned, user, [(doomed, 5)], created_at=_at(db, DAY_PRODUCT, 12)
        )
        db.commit()

        runner = _runner(db)
        first = runner.run_bucket("product_daily", DAY_PRODUCT)
        rows = _products(db, DAY_PRODUCT, generation)

        assert first.rows_written == 2 and first.rows_deleted == 0
        assert set(rows) == {kept.id, doomed.id}
        assert rows[doomed.id].units == 5
        assert rows[kept.id].sku_snapshot == kept.sku
        assert rows[kept.id].category_id_snapshot == category.id
        assert rows[doomed.id].category_id_snapshot is None

        with SessionLocal() as session:
            session.execute(
                text("DELETE FROM order_items WHERE order_id = :id"),
                {"id": vanishing.id},
            )
            session.execute(
                text("DELETE FROM orders WHERE id = :id"), {"id": vanishing.id}
            )
            session.commit()
        owned.orders.remove(vanishing.id)

        second = runner.run_bucket("product_daily", DAY_PRODUCT)
        rows = _products(db, DAY_PRODUCT, generation)

        assert doomed.id not in rows, (
            "the stale product row survived a recompute of its bucket; an upsert "
            "cannot delete a group key that disappeared, which is why this table "
            "is DELETE + INSERT"
        )
        assert set(rows) == {kept.id}
        assert second.rows_deleted == 2, (
            f"expected both rows deleted before reinsert, got {second.rows_deleted}"
        )
        assert second.rows_written == 1
    finally:
        _cleanup(owned)
        db.close()


def test_product_daily_reconciles_with_the_daily_table() -> None:
    """Per-product merchandise sales sum back to the day's, to the paisa.

    ``order_items`` has no discount column, so ``net_merchandise_sales`` per
    product is an *allocation* of the order-level discount. Largest-remainder
    integer allocation is what makes these two tables agree exactly rather than
    approximately — a rounded per-line share would leak a paisa per line and the
    two surfaces would disagree by an amount nobody could explain.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_sandbox_is_empty(db)
        generation = active_generation(db).generation

        user = _create_user(db, owned)
        # Three lines and a discount that does not divide evenly by three, so the
        # remainder loop in the allocator is actually exercised.
        first = _create_product(db, owned, price="99.99", cost="40.00")
        second = _create_product(db, owned, price="33.33", cost=None)
        third = _create_product(db, owned, price="10.01", cost="5.00")
        _create_order(
            db,
            owned,
            user,
            [(first, 3), (second, 2), (third, 7)],
            created_at=_at(db, DAY_PRODUCT, 15),
            discount="41.17",
            payment_discount="7.77",
        )
        db.commit()

        runner = _runner(db)
        runner.run_bucket("order_daily", DAY_PRODUCT)
        runner.run_bucket("product_daily", DAY_PRODUCT)

        day = _daily(db, DAY_PRODUCT, generation)
        rows = _products(db, DAY_PRODUCT, generation)

        assert sum(row.gross_merchandise_sales for row in rows.values()) == (
            day.gross_merchandise_sales
        )
        assert sum(row.net_merchandise_sales for row in rows.values()) == (
            day.net_merchandise_sales
        )
        assert sum(row.units for row in rows.values()) == day.units
        assert sum(row.costed_units for row in rows.values()) == day.costed_units
        assert sum(row.line_cost for row in rows.values()) == day.cogs_sum
    finally:
        _cleanup(owned)
        db.close()


# ===========================================================================
# 4. The revenue bridge
# ===========================================================================


def test_revenue_bridge_identity_holds_on_the_written_row() -> None:
    """gross - discount + tax + shipping + cod - refund == net_revenue.

    All five components non-zero, on a realistic multi-line COD order. The job
    measures ``net_revenue`` independently (``SUM(total_amount)`` less refunds)
    instead of deriving it from the other five, so this is a real check on the
    order rows rather than a restatement of the job's own arithmetic.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_sandbox_is_empty(db)
        generation = active_generation(db).generation

        user = _create_user(db, owned)
        premium = _create_product(db, owned, price="1200.00", cost="700.00")
        accessory = _create_product(db, owned, price="349.50", cost="150.00")
        _create_order(
            db,
            owned,
            user,
            [(premium, 2), (accessory, 3)],
            created_at=_at(db, DAY_BRIDGE, 13, 45),
            discount="250.00",
            payment_discount="48.50",
            tax="311.40",
            shipping="99.00",
            cod_surcharge="45.00",
            payment_method="cod",
        )
        db.commit()

        result = _runner(db).run_bucket("order_daily", DAY_BRIDGE)
        row = _daily(db, DAY_BRIDGE, generation)

        assert not [w for w in result.warnings if "revenue_bridge_imbalance" in w], (
            f"the job reported the bridge as unbalanced: {result.warnings}"
        )
        for column in (
            "gross_merchandise_sales",
            "discount_sum",
            "tax_sum",
            "shipping_income",
            "cod_surcharge_sum",
        ):
            assert getattr(row, column) > 0, f"{column} must be non-zero to test the bridge"

        computed = (
            row.gross_merchandise_sales
            - row.discount_sum
            + row.tax_sum
            + row.shipping_income
            + row.cod_surcharge_sum
            - row.refund_sum
        )
        assert computed == row.net_revenue, (
            f"the revenue bridge does not balance on the written row: "
            f"{computed} != {row.net_revenue}"
        )

        assert row.gross_merchandise_sales == Decimal("3448.50")
        assert row.discount_sum == Decimal("298.50"), (
            "discount_sum must include the payment discount, matching "
            "MarginService — payment_discount_sum is a split-out of it, not an "
            "additional term"
        )
        assert row.payment_discount_sum == Decimal("48.50")
        assert row.net_merchandise_sales == Decimal("3150.00")
        assert row.net_revenue == Decimal("3605.40")

        # And the same figure the existing engine reports for the same window,
        # so the rollup cannot become a second, quietly different definition.
        tz = store_timezone(db)
        start, end = day_bounds_utc(DAY_BRIDGE, tz)
        bridge = MarginService(db).revenue_bridge(start, end)
        assert bridge.balances()
        assert to_minor(row.net_revenue) == bridge.net_revenue_minor
        assert to_minor(row.gross_merchandise_sales) == (
            bridge.gross_merchandise_sales_minor
        )
        assert to_minor(row.discount_sum) == bridge.discounts_minor
    finally:
        _cleanup(owned)
        db.close()


# ===========================================================================
# 5 + 6. Store-local day and hour bucketing
# ===========================================================================


def test_store_local_day_bucketing() -> None:
    """Two orders on one UTC date belong to two different reporting days.

    ``17:30 UTC`` is 23:00 IST the same day; ``23:30 UTC`` is 05:00 IST the day
    *after*. Both share a UTC date, so a job that bucketed on UTC would put them
    in one row — and roughly a fifth of every day's orders with them.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_sandbox_is_empty(db)
        tz = store_timezone(db)
        generation = active_generation(db).generation

        late_evening = _at(db, DAY_TZ_A, 23)  # 17:30 UTC on DAY_TZ_A
        early_morning = _at(db, DAY_TZ_B, 5)  # 23:30 UTC on DAY_TZ_A

        assert late_evening.date() == early_morning.date() == DAY_TZ_A, (
            "the fixture is only meaningful if both instants share a UTC date"
        )
        assert local_day(late_evening, tz) == DAY_TZ_A
        assert local_day(early_morning, tz) == DAY_TZ_B

        user = _create_user(db, owned)
        product = _create_product(db, owned, price="400.00", cost="150.00")
        _create_order(db, owned, user, [(product, 1)], created_at=late_evening)
        _create_order(db, owned, user, [(product, 2)], created_at=early_morning)
        db.commit()

        runner = _runner(db)
        runner.run_bucket("order_daily", DAY_TZ_A)
        runner.run_bucket("order_daily", DAY_TZ_B)

        first = _daily(db, DAY_TZ_A, generation)
        second = _daily(db, DAY_TZ_B, generation)

        assert first.orders_total == 1 and first.units == 1
        assert first.net_revenue == Decimal("400.00")
        assert second.orders_total == 1 and second.units == 2, (
            "the 05:00 IST order was bucketed on its UTC date instead of its "
            "store-local reporting day"
        )
        assert second.net_revenue == Decimal("800.00")
    finally:
        _cleanup(owned)
        db.close()


def test_order_hourly_buckets_to_the_store_local_hour() -> None:
    """The hour is the store's, and all 24 are written even when empty."""
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_sandbox_is_empty(db)
        generation = active_generation(db).generation

        user = _create_user(db, owned)
        product = _create_product(db, owned, price="400.00", cost="150.00")
        _create_order(db, owned, user, [(product, 1)], created_at=_at(db, DAY_TZ_A, 23))
        _create_order(db, owned, user, [(product, 2)], created_at=_at(db, DAY_TZ_B, 5))
        db.commit()

        runner = _runner(db)
        runner.run_bucket("order_hourly", DAY_TZ_A)
        runner.run_bucket("order_hourly", DAY_TZ_B)

        first_day = _hourly(db, DAY_TZ_A, generation)
        second_day = _hourly(db, DAY_TZ_B, generation)

        assert [row.bucket_hour for row in first_day] == list(range(24)), (
            "all 24 hours must be written; a shrinking key set would need the "
            "delete-and-reinsert pattern instead of an upsert"
        )
        assert first_day[23].orders == 1
        assert first_day[23].net_revenue == Decimal("400.00")
        assert first_day[23].units == 1
        assert sum(row.orders for row in first_day) == 1, (
            "the 23:00 IST order landed in more than one hour bucket"
        )

        assert second_day[5].orders == 1
        assert second_day[5].units == 2
        assert second_day[5].net_revenue == Decimal("800.00")
        assert sum(row.orders for row in second_day) == 1

        # Re-running must not double the hour either.
        runner.run_bucket("order_hourly", DAY_TZ_A)
        again = _hourly(db, DAY_TZ_A, generation)
        assert again[23].orders == 1 and again[23].net_revenue == Decimal("400.00")
    finally:
        _cleanup(owned)
        db.close()


# ===========================================================================
# 7. Budget exhaustion, watermark, resumption
# ===========================================================================


def test_budget_exhaustion_is_partial_and_resumable() -> None:
    """Out of budget: stop, report `partial`, resume from the watermark.

    The watermark must sit on the last **fully** processed bucket — that is the
    contract the next tick relies on, and the reason a recompute of a year does
    not have to fit inside one HTTP request. The final rows must equal what a
    single unbounded run would have written, or "resumable" would mean
    "eventually different".
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_sandbox_is_empty(db)
        generation = active_generation(db).generation

        user = _create_user(db, owned)
        product = _create_product(db, owned, price="100.00", cost="40.00")
        days = [BUDGET_FROM + timedelta(days=n) for n in range(3)]
        #: Half-open: three buckets are days[0..2], so the exclusive bound is the
        #: day after the last one.
        window_end = days[2] + timedelta(days=1)
        for index, day in enumerate(days, start=1):
            _create_order(
                db, owned, user, [(product, index)], created_at=_at(db, day, 10)
            )
        db.commit()

        # start=0, bucket 1 at 10s, bucket 2 at 20s, bucket 3 at 100s > the 45s
        # budget. The final call is the duration measurement.
        clock = _StepClock([0, 10, 20, 100])
        first = _runner(db, clock=clock).run_window(
            ["order_daily"], days[0], window_end, budget_ms=45_000
        )

        assert first["budget_exhausted"] is True
        assert first["status"] == SyncStatus.PARTIAL
        job = first["jobs"]["order_daily"]
        assert job["days_requested"] == 3
        assert job["days_processed"] == 2
        assert job["days_processed"] < job["days_requested"]
        assert job["watermark_date"] == days[1], (
            "the watermark must name the last COMPLETE bucket, not the one the "
            "run gave up on"
        )
        assert _daily(db, days[2], generation) is None, (
            "the third bucket must not have been written at all"
        )

        run_row = db.execute(
            select(AnalyticsSyncRun).where(AnalyticsSyncRun.id == job["sync_run_id"])
        ).scalars().one()
        assert run_row.status == SyncStatus.PARTIAL
        assert run_row.watermark_date == days[1]
        assert run_row.days_processed == 2 and run_row.days_requested == 3
        assert run_row.window_to == days[2], (
            "analytics_sync_runs.window_to is documented as INCLUSIVE, so the "
            "half-open bound must be converted to the last bucket in range"
        )

        # The next tick resumes because the watermark moved.
        second = _runner(db).run_window(["order_daily"], days[0], window_end)
        resumed = second["jobs"]["order_daily"]
        assert second["status"] == SyncStatus.SUCCESS
        assert resumed["resumed_from"] == days[2], (
            "the second call restarted from the beginning instead of resuming"
        )
        assert resumed["days_requested"] == 1 and resumed["days_processed"] == 1
        assert resumed["watermark_date"] == days[2]

        after_resume = {day: _measures(_daily(db, day, generation)) for day in days}
        assert after_resume[days[2]]["net_revenue"] == Decimal("300.00")

        # Now the control: throw the rows away and do the whole window in one go.
        with SessionLocal() as session:
            session.execute(
                text(
                    "DELETE FROM agg_order_daily WHERE bucket_date BETWEEN "
                    ":first AND :last"
                ),
                {"first": days[0], "last": days[2]},
            )
            session.commit()

        unbounded = _runner(db).run_window(["order_daily"], days[0], window_end)
        assert unbounded["jobs"]["order_daily"]["days_processed"] == 3, (
            "a window whose last run succeeded must be recomputed in full, not "
            "skipped as already done"
        )
        in_one_go = {day: _measures(_daily(db, day, generation)) for day in days}

        assert after_resume == in_one_go, (
            "a budget-interrupted run that resumed produced different rows than "
            "a single unbounded run"
        )
    finally:
        _cleanup(owned)
        db.close()


def test_run_window_is_half_open() -> None:
    """`date_to` is the first bucket NOT rebuilt.

    Every date range in this stack is half-open — ``timebox.day_bounds_utc``,
    ``timebox.range_bounds_utc``, the admin API. One inclusive window among them
    is an off-by-one waiting for the next person who does not read the docstring:
    a backfill that quietly rebuilds one extra day, or misses the last one, and a
    chart with a gap nobody can trace back to it.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_sandbox_is_empty(db)
        generation = active_generation(db).generation

        first_day = BUDGET_FROM + timedelta(days=15)
        excluded_day = first_day + timedelta(days=1)

        user = _create_user(db, owned)
        product = _create_product(db, owned, price="100.00", cost="40.00")
        _create_order(db, owned, user, [(product, 1)], created_at=_at(db, first_day, 10))
        _create_order(
            db, owned, user, [(product, 9)], created_at=_at(db, excluded_day, 10)
        )
        db.commit()

        result = _runner(db).run_window(["order_daily"], first_day, excluded_day)
        job = result["jobs"]["order_daily"]

        assert job["days_requested"] == 1 and job["days_processed"] == 1
        assert job["watermark_date"] == first_day
        assert _daily(db, first_day, generation).net_revenue == Decimal("100.00")
        assert _daily(db, excluded_day, generation) is None, (
            "date_to is exclusive; the order on the boundary day must not have "
            "been aggregated by a window ending there"
        )
    finally:
        _cleanup(owned)
        db.close()


def test_empty_window_is_a_clean_no_op() -> None:
    """`date_from == date_to` rebuilds nothing and is not an error.

    A caller holding an empty date range asking for "rebuild nothing" is
    sensible; raising would push that check out to every call site, where one of
    them would eventually forget it.
    """
    db = SessionLocal()
    try:
        day = BUDGET_FROM + timedelta(days=20)
        result = _runner(db).run_window(["order_daily", "product_daily"], day, day)

        assert result["status"] == SyncStatus.SUCCESS
        assert result["days_requested"] == 0 and result["days_processed"] == 0
        assert result["rows_written"] == 0 and result["rows_deleted"] == 0
        for job in result["jobs"].values():
            assert job["status"] == SyncStatus.SUCCESS
            assert job["days_requested"] == 0
            assert job["watermark_date"] is None

        with pytest.raises(ValueError):
            _runner(db).run_window(["order_daily"], day, day - timedelta(days=1))
    finally:
        _cleanup(_Owned())
        db.close()


# ===========================================================================
# 8. Refunded orders: demand yes, revenue no
# ===========================================================================


def test_refunded_order_is_demand_but_not_revenue() -> None:
    """A refunded order is demand, and its SALE is recognised in its own period.

    ``order_value_created`` spans every status because it is a *demand* measure —
    the order was really placed. ``_REVENUE_STATUSES`` is PAID/SHIPPED/DELIVERED,
    so the same order contributes nothing to ``net_revenue``, ``units`` or the
    customer counts.

    The refund is dated **outside** this bucket on purpose. A whole-order refund
    removes the order from the revenue statuses *and* is counted again in
    ``refund_sum`` on the day it was issued — an inherited quirk of the codebase's
    single refund definition (see ``MarginService._refunds_minor``). Keeping the
    two days apart tests each half without asserting that quirk is correct.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_sandbox_is_empty(db)
        generation = active_generation(db).generation

        user = _create_user(db, owned)
        buyer = _create_user(db, owned)
        product = _create_product(db, owned, price="600.00", cost="250.00")

        _create_order(
            db,
            owned,
            user,
            [(product, 1)],
            created_at=_at(db, DAY_REFUNDED, 9),
            status=OrderStatus.REFUNDED,
            refunded_at=_at(db, DAY_REFUND_ISSUED, 11),
        )
        _create_order(
            db, owned, buyer, [(product, 2)], created_at=_at(db, DAY_REFUNDED, 16)
        )
        db.commit()

        _runner(db).run_bucket("order_daily", DAY_REFUNDED)
        row = _daily(db, DAY_REFUNDED, generation)

        assert row.orders_total == 2
        assert row.orders_refunded == 1
        assert row.orders_paid == 1
        assert row.order_value_created == Decimal("1800.00"), (
            "order_value_created spans every status — it is what was demanded"
        )
        # The two figures deliberately DISAGREE, and that is the point of this
        # test now. They answer different questions:
        #
        #   paid_order_value  1200 — the LEGACY rule (_REVENUE_STATUSES), which
        #       drops the refunded order entirely. Frozen as the shadow-mode
        #       parity anchor: it must stay byte-identical to
        #       DashboardService._revenue_summary or the comparison against the
        #       legacy pages stops meaning anything.
        #
        #   net_revenue       1800 — the CORRECTED recognition rule. Both sales
        #       really happened on this day; the refund was issued on
        #       DAY_REFUND_ISSUED and is reversed in THAT bucket, which is why
        #       refund_sum is 0.00 here.
        #
        # Deriving net_revenue from paid_order_value is what produced the
        # negative-revenue bug: the refunded order is already absent from the
        # legacy total, so subtracting its refund reverses the same sale twice.
        assert row.paid_order_value == Decimal("1200.00"), (
            "the legacy parity anchor must not move with the recognition rule"
        )
        assert row.net_revenue == Decimal("1800.00"), (
            "both sales are recognised on the day they happened; the reversal "
            "belongs to the bucket where the refund was issued"
        )
        assert row.refund_sum == Decimal("0.00"), (
            "the refund was issued on a different day and belongs to that bucket"
        )
        # 3, not 2. Under the corrected recognition rule the refunded order's
        # SALE is recognised in the period it happened — the customer really did
        # buy 1 unit that day — and the reversal is booked in the period the
        # refund was issued (a different bucket here, which is why `refund_sum`
        # above is 0.00).
        #
        # The previous expectation of 2 came from the old rule, where a refunded
        # order was erased from its own period retroactively. That is what made
        # an order placed and refunded in one window report NEGATIVE revenue:
        # excluded by status, then subtracted again as a refund.
        assert row.units == 3, (
            "a refunded order's units were still sold on the day it was placed; "
            "the reversal belongs to the refund's own bucket"
        )
        # 2, not 1 — for the same reason `units` is 3. Both customers really
        # bought that day; recognising one sale and erasing the other's would be
        # the retroactive rewrite the recognition fix removed.
        assert row.distinct_customers == 2, (
            "both customers transacted on this day; the refund is a later event "
            "in its own bucket, not a reason to un-count a customer"
        )
        assert row.new_customers == 2

        # The refund lands on the day it was issued, in a bucket with no orders.
        _runner(db).run_bucket("order_daily", DAY_REFUND_ISSUED)
        refund_day = _daily(db, DAY_REFUND_ISSUED, generation)
        assert refund_day.orders_total == 0
        assert refund_day.refund_sum == Decimal("600.00")
    finally:
        _cleanup(owned)
        db.close()


# ===========================================================================
# 9. Cost coverage
# ===========================================================================


def test_costed_units_counts_only_lines_with_a_unit_cost() -> None:
    """A line with no `unit_cost` is excluded from COGS and kept in `units`.

    ``profit_service.py`` does ``COALESCE(unit_cost, 0)``, so an uncosted line
    reports 100% margin on itself — an error in the flattering direction that
    nobody investigates. Here the line falls out of ``cogs_sum`` and stays in
    ``units``, so ``costed_units / units`` states how much of the margin was
    actually measured.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_sandbox_is_empty(db)
        generation = active_generation(db).generation

        user = _create_user(db, owned)
        costed = _create_product(db, owned, price="500.00", cost="180.00")
        uncosted = _create_product(db, owned, price="250.00", cost=None)
        _create_order(
            db,
            owned,
            user,
            [(costed, 3), (uncosted, 4)],
            created_at=_at(db, DAY_COST, 12),
        )
        db.commit()

        runner = _runner(db)
        runner.run_bucket("order_daily", DAY_COST)
        runner.run_bucket("product_daily", DAY_COST)

        row = _daily(db, DAY_COST, generation)
        assert row.units == 7
        assert row.costed_units == 3, (
            "only the three units on the line carrying a unit_cost are costed"
        )
        assert row.cogs_sum == Decimal("540.00"), (
            "the uncosted line must contribute nothing, not zero-filled cost"
        )

        products = _products(db, DAY_COST, generation)
        assert products[costed.id].costed_units == 3
        assert products[costed.id].line_cost == Decimal("540.00")
        assert products[uncosted.id].units == 4
        assert products[uncosted.id].costed_units == 0
        assert products[uncosted.id].line_cost == Decimal("0.00")
    finally:
        _cleanup(owned)
        db.close()


def test_product_daily_counts_returns_on_the_day_the_refund_was_issued() -> None:
    """Returned units belong to the refund's bucket, not the sale's."""
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_sandbox_is_empty(db)
        generation = active_generation(db).generation

        user = _create_user(db, owned)
        product = _create_product(db, owned, price="200.00", cost="80.00")
        order = _create_order(
            db, owned, user, [(product, 4)], created_at=_at(db, DAY_RETURN_SALE, 10)
        )
        _create_refunded_return(
            db,
            owned,
            order,
            [(order.items[0], 3)],
            refunded_at=_at(db, DAY_RETURN_REFUND, 15),
            refund_amount="600.00",
        )
        db.commit()

        runner = _runner(db)
        runner.run_bucket("product_daily", DAY_RETURN_SALE)
        runner.run_bucket("product_daily", DAY_RETURN_REFUND)

        sale_day = _products(db, DAY_RETURN_SALE, generation)[product.id]
        assert sale_day.units == 4
        assert sale_day.returned_units == 0, (
            "a return is dated by when it was refunded, not by the sale"
        )

        refund_day = _products(db, DAY_RETURN_REFUND, generation)[product.id]
        assert refund_day.units == 0, (
            "nothing sold on the refund day; the product is here only because "
            "goods came back"
        )
        assert refund_day.returned_units == 3
        assert refund_day.returned_value == Decimal("600.00")
    finally:
        _cleanup(owned)
        db.close()


# ===========================================================================
# 10. The run log
# ===========================================================================


def test_sync_run_row_is_written_with_a_correct_watermark() -> None:
    """Every run leaves a row; a rollup table cannot report its own absence."""
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_sandbox_is_empty(db)
        generation = active_generation(db).generation

        user = _create_user(db, owned)
        product = _create_product(db, owned, price="150.00", cost="60.00")
        _create_order(db, owned, user, [(product, 2)], created_at=_at(db, DAY_SYNC, 8))
        db.commit()

        result = _runner(db).run_bucket("order_daily", DAY_SYNC)

        run = db.execute(
            select(AnalyticsSyncRun)
            .where(
                AnalyticsSyncRun.worker_id == WORKER_ID,
                AnalyticsSyncRun.job == "order_daily",
            )
            .order_by(AnalyticsSyncRun.id.desc())
            .limit(1)
        ).scalars().one()

        assert run.status == SyncStatus.SUCCESS
        assert run.trigger == SyncTrigger.MANUAL
        assert run.window_from == DAY_SYNC and run.window_to == DAY_SYNC
        assert run.tz_generation == generation
        assert run.days_requested == 1 and run.days_processed == 1
        assert run.rows_written == result.rows_written == 1
        assert run.watermark_date == DAY_SYNC, (
            "the watermark is a DATA watermark — the highest bucket fully "
            "computed, not the clock time the job ran"
        )
        assert run.started_at is not None and run.finished_at is not None
        assert run.duration_ms is not None and run.duration_ms >= 0
        assert run.error is None
    finally:
        _cleanup(owned)
        db.close()


def test_unknown_job_fails_before_anything_is_written() -> None:
    """A queue row naming a job that does not exist must fail loudly."""
    db = SessionLocal()
    try:
        assert sorted(JOBS) == [
            "cohort_monthly",
            "customer_daily",
            "customer_snapshot",
            "funnel_daily",
            "inventory_daily",
            "order_daily",
            "order_hourly",
            "payment_daily",
            "product_daily",
            "promo_daily",
            "shipment_daily",
            "shipment_geo_daily",
        ], (
            "all twelve rollups are implemented and registered. This list is "
            "exhaustive on purpose: a job registered under a real name but not "
            "actually built would report SUCCESS, advance a watermark, and make "
            "an unbuilt table indistinguishable from a quiet one. Absent is "
            "honest; a stub is not. They live in aggregation/jobs.py, "
            "jobs_customer.py, jobs_ops.py and jobs_finance.py, covered by "
            "test_analytics_aggregation{,_jobs2,_jobs3}.py"
        )
        with pytest.raises(KeyError):
            # A name that is not, and will never be, a real job. This example has
            # now been invalidated twice by a job actually shipping under the
            # placeholder name — first `customer_daily`, then `payment_daily`.
            # A deliberately impossible name cannot be overtaken by real work.
            _runner(db).run_bucket("__not_a_real_job__", DAY_SYNC)
        before = db.execute(
            select(AnalyticsSyncRun).where(AnalyticsSyncRun.worker_id == WORKER_ID)
        ).scalars().all()
        assert not before, "a failed lookup must not leave a run row behind"
    finally:
        _cleanup(_Owned())
        db.close()


# ===========================================================================
# The queue integration
# ===========================================================================


def test_drain_queue_claims_runs_and_completes() -> None:
    """claim -> run -> complete, using the queue rather than reimplementing it."""
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_sandbox_is_empty(db)
        generation = active_generation(db).generation

        user = _create_user(db, owned)
        product = _create_product(db, owned, price="220.00", cost="90.00")
        _create_order(db, owned, user, [(product, 2)], created_at=_at(db, DAY_QUEUE, 19))
        db.commit()

        queue = RecomputeQueue(db)
        queue.enqueue("order_daily", DAY_QUEUE, reason=RecomputeReason.BACKFILL)
        db.commit()

        result = _runner(db).drain_queue(limit=50)

        assert result["completed"] >= 1
        assert result["failed"] == 0
        assert "order_daily" in result["jobs"]
        assert result["jobs"]["order_daily"]["watermark_date"] == DAY_QUEUE

        row = _daily(db, DAY_QUEUE, generation)
        assert row is not None and row.net_revenue == Decimal("440.00")

        queued = db.execute(
            select(AnalyticsRecomputeQueue).where(
                AnalyticsRecomputeQueue.job == "order_daily",
                AnalyticsRecomputeQueue.bucket_date == DAY_QUEUE,
            )
        ).scalars().one()
        assert queued.status == RecomputeStatus.DONE
        assert queued.processed_at is not None
    finally:
        _cleanup(owned)
        db.close()
