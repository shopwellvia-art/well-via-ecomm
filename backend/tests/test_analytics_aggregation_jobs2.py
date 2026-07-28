"""Tests for the second wave of aggregation jobs.

Covers ``customer_daily``, ``customer_snapshot``, ``funnel_daily``,
``inventory_daily`` and ``shipment_daily`` — the five rollups behind the first
eight production dashboards. ``tests/test_analytics_aggregation.py`` covers the
first three jobs and the runner; this file deliberately does not repeat what it
already proves about the runner, only what these five jobs each do.

What actually has to be true here
---------------------------------
A rollup that computes the wrong number is caught the first time somebody reads
the dashboard. A rollup that computes the *right* number and then computes it
again on top of itself is never caught at all, so the tests carrying the weight
are the ones that run a bucket twice and compare column values:

  * ``test_*_is_idempotent`` — five of them, one per job. Same bucket, twice,
    identical values. Not "no error" and not "one row": the numbers. An
    ``ON DUPLICATE KEY UPDATE col = col + VALUES(col)`` passes everything else in
    this file and fails these.
  * ``test_*_bucketing_is_store_local`` — five of them. Two events on the same
    **UTC** date that belong to two different **store-local** reporting days.
    23:00 IST is 17:30 UTC the same day; 00:30 IST is 19:00 UTC the day before.
    A UTC-day bucketer puts both in one bucket and is wrong by 5.5 hours of
    trade, every day, unrecoverably.

Then the five claims that are specific to these jobs and are exactly the ones a
plausible-looking implementation gets wrong:

  * new vs returning is decided by a **prior order**, never by ``users.created_at``
    (``test_customer_daily_new_is_by_prior_order_not_signup``);
  * the snapshot table's retention policy actually runs, in both directions
    (``test_customer_snapshot_retention_*``);
  * an empty ``cart_events`` produces honest zeros and the funnel does **not**
    invent its bottom step from the orders table
    (``test_funnel_daily_writes_zeros_and_does_not_invent_steps_from_orders``);
  * inventory is forward-only: the first run records where history starts and
    an earlier bucket is refused, not fabricated
    (``test_inventory_daily_sets_history_since_and_refuses_to_backfill``);
  * ``agg_shipment_daily`` has no on-time / SLA / late column and cannot grow one
    (``test_shipment_daily_has_no_on_time_column``).

Isolation strategy
------------------
Every fixture lives in **2009**, years before this store's first order. But
``customer_daily`` and ``customer_snapshot`` are *lifetime-state* metrics — "new
vs returning" is a global "has this customer ever ordered before" query, and a
snapshot's LTV, recency and RFM are lifetime aggregates over the whole customer
population — so a fixture date alone cannot isolate them. Any row another test
leaves behind, or creates concurrently, moves the numbers.

So the assertions are built to be indifferent to that, following the house
pattern documented in ``tests/test_sales_analytics_service.py``:

  * **single-row-per-bucket tables** (``agg_customer_daily``,
    ``agg_funnel_daily``) — ``_baseline`` builds the bucket *before* the fixtures
    exist and every figure is asserted as a ``_delta`` against it;
  * **dimensioned tables** (``agg_customer_snapshot``, ``agg_inventory_daily``,
    ``agg_shipment_daily``) — assertions key on the customer / product / courier
    this test created, which is deterministic even when the table is not;
  * **RFM quintile scores** are defined *relative to the population*, so only
    what holds for any population is asserted — see the comment at that
    assertion, which spells out what is being claimed and what is not.

Two things genuinely cannot be scoped: the ``'-'`` courier sentinel (a shared
key) and the funnel's all-zeros day (which needs a globally empty
``cart_events``). Those keep a loud precondition rather than a weakened
assertion — a clear failure beats a number that is quietly wrong.

Teardown deletes every owned row through a fresh session, including the ``agg_*``
rows for the sandbox dates, the ``analytics_sync_runs`` rows written under this
module's worker id, and the ``analytics.inventory_history_since`` setting the
ledger job creates. No db fixture exists in ``conftest.py``; each test owns its
``SessionLocal()`` and closes it in ``finally``.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.analytics_facts import CartEvent, CartEventType, InventoryMovement, MovementType
from app.models.analytics_rollups import (
    AggCustomerDaily,
    AggCustomerSnapshot,
    AggFunnelDaily,
    AggInventoryDaily,
    AggShipmentDaily,
)
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Product
from app.models.return_request import ReturnItem, ReturnRequest, ReturnStatus
from app.models.shipment import Shipment, ShipmentStatus
from app.models.system_setting import SystemSetting
from app.models.user import User
from app.services.analytics.aggregation import AggregationRunner
from app.services.analytics.aggregation.jobs_customer import (
    ACTIVE_WINDOW_DAYS,
    RETENTION_DAILY_DAYS,
    _customer_key,
    _segment,
)
from app.services.analytics.aggregation.jobs_ops import INVENTORY_HISTORY_SINCE_KEY
from app.services.analytics.timebox import active_generation, day_bounds_utc, store_timezone
from app.services.analytics.types import MetricQuality

# ---------------------------------------------------------------------------
# The 2009 sandbox
# ---------------------------------------------------------------------------

SANDBOX_FIRST = date(2009, 1, 1)
SANDBOX_LAST = date(2009, 8, 2)

DAY_CUSTOMER = date(2009, 7, 3)
DAY_CUSTOMER_TZ_A = date(2009, 7, 7)
DAY_CUSTOMER_TZ_B = date(2009, 7, 8)
DAY_SIGNUP = date(2009, 7, 9)
DAY_SIGNUP_EARLIER = date(2009, 7, 1)

#: Month-end, therefore retained as a daily row forever. Every snapshot test
#: that wants rows to exist has to use one — see the retention policy.
DAY_SNAPSHOT = date(2009, 7, 31)
#: Not a month-end and long past the 90-day window: the retention case.
DAY_SNAPSHOT_PRUNED = date(2009, 7, 15)
DAY_SNAPSHOT_FIRST_ORDER = date(2009, 7, 10)
DAY_SNAPSHOT_OLD_ORDER = date(2009, 1, 5)

DAY_FUNNEL = date(2009, 7, 5)
DAY_FUNNEL_TZ_A = date(2009, 7, 11)
DAY_FUNNEL_TZ_B = date(2009, 7, 12)
DAY_FUNNEL_ZEROS = date(2009, 7, 20)

DAY_INV_SEED = date(2009, 7, 1)
DAY_INV = date(2009, 7, 6)
DAY_INV_NEXT = date(2009, 7, 7)
DAY_INV_TZ_A = date(2009, 7, 13)
DAY_INV_TZ_B = date(2009, 7, 14)
DAY_INV_SINCE = date(2009, 7, 25)
DAY_INV_BEFORE_SINCE = date(2009, 7, 24)

DAY_SHIP = date(2009, 7, 4)
DAY_SHIP_TZ_A = date(2009, 7, 17)
DAY_SHIP_TZ_B = date(2009, 7, 18)
DAY_SHIP_DELIVERED = date(2009, 7, 6)

#: Distinctive enough that teardown can delete this module's run log without a
#: date filter, and that a stuck row in a shared DB is traceable to these tests.
WORKER_ID = "test-agg-jobs2"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _at(db: Session, day: date, hour: int, minute: int = 0) -> datetime:
    """The UTC instant that is ``hour:minute`` **store-local** on ``day``.

    Built from ``timebox.day_bounds_utc`` rather than by hand: the point of the
    bucketing tests is that the day boundary is the store's, and deriving the
    fixture from the same function the jobs use makes the assertion about
    bucketing rather than about two independent copies of the same arithmetic.
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
        self.returns: list[int] = []
        self.shipments: list[int] = []
        self.cart_events: list[int] = []
        self.movements: list[int] = []


def _create_user(
    db: Session, owned: _Owned, *, created_at: datetime | None = None
) -> User:
    user = User(
        email=f"jobs2-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    if created_at is not None:
        user.created_at = created_at
    db.add(user)
    db.flush()
    owned.users.append(user.id)
    return user


def _create_product(
    db: Session, owned: _Owned, *, price: str = "500.00", cost: str | None = None
) -> Product:
    product = Product(
        sku=f"SKU-J2-{_uid()}",
        name=f"Jobs2Product {_uid()}",
        price=Decimal(price),
        # None means "never snapshotted" — the case `costed_units` and `quality`
        # exist to keep visible instead of coalescing to zero.
        cost=None if cost is None else Decimal(cost),
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
    tax: str = "0",
    shipping: str = "0",
    payment_method: str = "prepaid",
    refunded_at: datetime | None = None,
) -> Order:
    """An order whose ``total_amount`` is built the way checkout builds it."""
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

    total = gross - Decimal(discount) + Decimal(tax) + Decimal(shipping)
    order = Order(
        user_id=user.id,
        status=status,
        subtotal=gross,
        tax_amount=Decimal(tax),
        discount_amount=Decimal(discount),
        payment_discount_amount=Decimal("0.00"),
        shipping_amount=Decimal(shipping),
        cod_surcharge_amount=Decimal("0.00"),
        cod_balance=Decimal("0.00"),
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
    *,
    refunded_at: datetime,
    refund_amount: str,
) -> ReturnRequest:
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
        ReturnItem(order_item_id=order.items[0].id, quantity=1),
    ]
    db.add(request)
    db.flush()
    owned.returns.append(request.id)
    return request


def _create_shipment(
    db: Session,
    owned: _Owned,
    order: Order,
    *,
    created_at: datetime,
    courier: str | None,
    status: ShipmentStatus,
    cost: str | None = None,
    shipped_at: datetime | None = None,
    delivered_at: datetime | None = None,
) -> Shipment:
    shipment = Shipment(
        order_id=order.id,
        courier_partner=courier,
        shipment_status=status,
        shipment_cost=None if cost is None else Decimal(cost),
        shipped_at=shipped_at,
        delivered_at=delivered_at,
        created_at=created_at,
    )
    db.add(shipment)
    db.flush()
    owned.shipments.append(shipment.id)
    return shipment


def _create_cart_event(
    db: Session,
    owned: _Owned,
    *,
    occurred_at: datetime,
    event_type: str,
    session_key: str,
    user: User | None = None,
) -> CartEvent:
    event = CartEvent(
        occurred_at=occurred_at,
        session_key=session_key,
        user_id=user.id if user else None,
        event_type=event_type,
        currency="INR",
        # NOT NULL and UNIQUE: the ingestion path's idempotency key, so a
        # double-clicked add-to-cart cannot become three funnel events.
        event_key=f"jobs2:{_uid()}",
    )
    db.add(event)
    db.flush()
    owned.cart_events.append(event.id)
    return event


def _create_movement(
    db: Session,
    owned: _Owned,
    product: Product,
    *,
    occurred_at: datetime,
    movement_type: str,
    delta: int,
) -> InventoryMovement:
    movement = InventoryMovement(
        product_id=product.id,
        occurred_at=occurred_at,
        movement_type=movement_type,
        delta=delta,
        event_key=f"jobs2:{_uid()}:{product.id}",
    )
    db.add(movement)
    db.flush()
    owned.movements.append(movement.id)
    return movement


# ---------------------------------------------------------------------------
# Teardown + preconditions
# ---------------------------------------------------------------------------


def _cleanup(owned: _Owned) -> None:
    """Delete everything this test owned, through a fresh session.

    A fresh session so teardown cannot be skipped by a half-rolled-back
    transaction in the test's own session. The rollup and run-log rows go by
    sandbox date / worker id rather than by id, because they are written by Core
    statements the test never sees the ids of.
    """
    with SessionLocal() as session:
        for table, ids in (
            ("cart_events", owned.cart_events),
            ("inventory_movements", owned.movements),
            ("shipments", owned.shipments),
        ):
            if ids:
                session.execute(
                    text(f"DELETE FROM {table} WHERE id IN :ids"),
                    {"ids": tuple(ids)},
                )
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

        window = {"first": SANDBOX_FIRST, "last": SANDBOX_LAST}
        for table in (
            "agg_customer_daily",
            "agg_customer_snapshot",
            "agg_funnel_daily",
            "agg_inventory_daily",
            "agg_shipment_daily",
            "analytics_recompute_queue",
        ):
            session.execute(
                text(f"DELETE FROM {table} WHERE bucket_date BETWEEN :first AND :last"),
                window,
            )
        session.execute(
            text("DELETE FROM analytics_sync_runs WHERE worker_id = :worker"),
            {"worker": WORKER_ID},
        )
        # The ledger job creates this row on its first ever run. Removing it is
        # what makes "first run" reproducible from one test to the next.
        session.execute(
            text("DELETE FROM system_settings WHERE `key` = :key"),
            {"key": INVENTORY_HISTORY_SINCE_KEY},
        )
        session.commit()


def _assert_days_are_empty(db: Session, *days: date) -> None:
    """Fail loudly if a foreign row already sits in one of these buckets.

    Used only where an assertion genuinely cannot be scoped or deltaed: the
    ``'-'`` courier sentinel (a shared key no test can uniquify) and the funnel's
    all-zeros day. Everywhere else the tests take a baseline and assert the
    delta, or key on a customer / product / courier they created, so pre-existing
    and concurrently-created rows cancel out instead of being counted.
    """
    tz = store_timezone(db)
    for day in days:
        start, end = day_bounds_utc(day, tz)
        for label, column, model in (
            ("orders", Order.created_at, Order),
            ("shipments", Shipment.created_at, Shipment),
            ("cart_events", CartEvent.occurred_at, CartEvent),
        ):
            foreign = db.execute(
                select(model.id).where(column >= start, column < end).limit(5)
            ).scalars().all()
            assert not foreign, (
                f"{label} {foreign} already sit in the store-local day {day}; "
                "these tests assert absolute figures and cannot share a bucket"
            )


def _assert_no_cart_events(db: Session) -> None:
    """The funnel-zeros test asserts on a globally empty source table."""
    stray = db.execute(select(CartEvent.id).limit(5)).scalars().all()
    assert not stray, (
        f"cart_events already holds rows {stray}; the zeros test asserts that an "
        "entirely empty source produces zeros plus the not-instrumented warning"
    )


def _assert_history_since_absent(db: Session) -> None:
    existing = db.execute(
        select(SystemSetting.value).where(SystemSetting.key == INVENTORY_HISTORY_SINCE_KEY)
    ).scalars().first()
    assert existing is None, (
        f"{INVENTORY_HISTORY_SINCE_KEY} already exists ({existing!r}); the "
        "forward-only test asserts what the FIRST ever ledger run does"
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


def _one(db: Session, model, bucket: date, generation: int):
    db.expire_all()
    return db.execute(
        select(model).where(
            model.bucket_date == bucket, model.tz_generation == generation
        )
    ).scalars().first()


def _many(db: Session, model, bucket: date, generation: int, key: str) -> dict:
    db.expire_all()
    rows = db.execute(
        select(model).where(
            model.bucket_date == bucket, model.tz_generation == generation
        )
    ).scalars().all()
    return {getattr(row, key): row for row in rows}


def _runner(db: Session) -> AggregationRunner:
    return AggregationRunner(db, worker_id=WORKER_ID)


def _baseline(
    db: Session, runner: AggregationRunner, job: str, model, bucket: date, generation: int
) -> dict:
    """Build the bucket BEFORE any fixture exists and return what it measured.

    The house pattern from ``tests/test_sales_analytics_service.py``: capture a
    baseline, then assert on the **delta**, so whatever the shared throwaway
    MySQL already holds — or whatever a concurrently running suite inserts —
    cancels out instead of being counted as this test's own.

    It is used only for the single-row-per-bucket tables, where every column is
    a count or a sum over a population this test does not control. The
    per-customer and per-product tables do not need it: a row keyed by *this
    test's* customer or product is deterministic even when the table is not.
    """
    runner.run_bucket(job, bucket)
    row = _one(db, model, bucket, generation)
    return _measures(row) if row is not None else {}


def _delta(before: dict, after: dict, column: str):
    """What this test's fixtures added to a column, net of the baseline."""
    return after[column] - before.get(column, 0)


#: Snapshot columns whose value depends on the WHOLE population on the snapshot
#: date, not on the customer's own row: quintile scores and the segment derived
#: from them. If another suite creates a 2009 customer between two runs of the
#: same bucket, these legitimately move and the rest must not.
_POPULATION_RELATIVE = frozenset({"r_score", "f_score", "m_score", "rfm_segment"})


def _without_population_relative(rows: dict) -> dict:
    return {
        key: {
            column: value
            for column, value in measures.items()
            if column not in _POPULATION_RELATIVE
        }
        for key, measures in rows.items()
    }


def _bucket_row_count(db: Session, model, bucket: date, generation: int) -> int:
    """How many rows the bucket holds right now — the Pattern B delete target."""
    db.expire_all()
    return len(
        db.execute(
            select(model.id).where(
                model.bucket_date == bucket, model.tz_generation == generation
            )
        ).scalars().all()
    )


# ===========================================================================
# customer_daily
# ===========================================================================


def test_customer_daily_is_idempotent_and_splits_new_from_returning() -> None:
    """Two runs leave identical values, and the split is measured, not zero.

    The realistic fixture: one first-time buyer and one customer who bought on an
    earlier day and came back, so every column on the row is non-zero and
    "identical" cannot be "identically zero because the job did nothing".

    Every counter here is a count over a population this test does not own —
    ``new_customers`` in particular is decided by a global "has this customer
    ever ordered before" query — so the figures are asserted as deltas against a
    baseline taken before the fixtures exist.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        runner = _runner(db)
        baseline = _baseline(
            db, runner, "customer_daily", AggCustomerDaily, DAY_CUSTOMER, generation
        )

        product = _create_product(db, owned, price="500.00", cost="200.00")
        first_timer = _create_user(db, owned)
        repeat = _create_user(db, owned)

        # The returning customer's earlier order, on a different bucket.
        _create_order(
            db, owned, repeat, [(product, 1)], created_at=_at(db, DAY_SIGNUP_EARLIER, 12)
        )
        _create_order(
            db, owned, first_timer, [(product, 2)], created_at=_at(db, DAY_CUSTOMER, 10)
        )
        _create_order(
            db, owned, repeat, [(product, 1)], created_at=_at(db, DAY_CUSTOMER, 14)
        )
        _create_order(
            db, owned, repeat, [(product, 3)], created_at=_at(db, DAY_CUSTOMER, 20)
        )
        # Cancelled: never an acquired customer, whatever the intent was.
        _create_order(
            db,
            owned,
            _create_user(db, owned),
            [(product, 4)],
            created_at=_at(db, DAY_CUSTOMER, 16),
            status=OrderStatus.CANCELLED,
        )
        db.commit()

        first = runner.run_bucket("customer_daily", DAY_CUSTOMER)
        before = _measures(_one(db, AggCustomerDaily, DAY_CUSTOMER, generation))

        second = runner.run_bucket("customer_daily", DAY_CUSTOMER)
        after = _measures(_one(db, AggCustomerDaily, DAY_CUSTOMER, generation))

        assert first.rows_written == 1 and second.rows_written == 1
        assert before == after, (
            "re-running one bucket changed its values; the upsert is accumulating "
            f"rather than overwriting. before={before} after={after}"
        )

        assert _delta(baseline, before, "new_customers") == 1
        assert _delta(baseline, before, "returning_customers") == 1
        assert _delta(baseline, before, "active_customers") == 2
        assert _delta(baseline, before, "orders_new") == 1
        assert _delta(baseline, before, "orders_returning") == 2
        assert _delta(baseline, before, "revenue_new") == Decimal("1000.00")
        assert _delta(baseline, before, "revenue_returning") == Decimal("2000.00")
        assert (
            before["new_customers"] + before["returning_customers"]
            == before["active_customers"]
        ), "new and returning must partition the day's distinct purchasers"

        rows = db.execute(
            select(AggCustomerDaily).where(
                AggCustomerDaily.bucket_date == DAY_CUSTOMER,
                AggCustomerDaily.tz_generation == generation,
            )
        ).scalars().all()
        assert len(rows) == 1, "the UNIQUE key did not collapse the second run"
    finally:
        _cleanup(owned)
        db.close()


def test_customer_daily_new_is_by_prior_order_not_signup() -> None:
    """"New" means no earlier order — it is not the number of accounts created.

    ``DashboardService._new_customers`` counts ``users.created_at`` in the
    window, which is signups: a different and always-larger number, because most
    accounts never order and the ones that do usually order later. This fixture
    makes the two disagree on purpose — three signups, one of whom buys — and
    pins the rollup to the purchase definition.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        runner = _runner(db)
        baseline = _baseline(
            db, runner, "customer_daily", AggCustomerDaily, DAY_SIGNUP, generation
        )

        product = _create_product(db, owned, price="400.00", cost="150.00")
        signup_at = _at(db, DAY_SIGNUP, 9)

        buyer = _create_user(db, owned, created_at=signup_at)
        _create_user(db, owned, created_at=signup_at)  # signed up, never ordered
        _create_user(db, owned, created_at=signup_at)  # signed up, never ordered

        # Signed up long before the bucket, and had already bought before it.
        veteran = _create_user(db, owned, created_at=_at(db, DAY_SIGNUP_EARLIER, 8))
        _create_order(
            db, owned, veteran, [(product, 1)], created_at=_at(db, DAY_SIGNUP_EARLIER, 11)
        )

        _create_order(db, owned, buyer, [(product, 1)], created_at=_at(db, DAY_SIGNUP, 15))
        _create_order(
            db, owned, veteran, [(product, 2)], created_at=_at(db, DAY_SIGNUP, 18)
        )
        db.commit()

        runner.run_bucket("customer_daily", DAY_SIGNUP)
        measured = _measures(_one(db, AggCustomerDaily, DAY_SIGNUP, generation))

        # Scoped to the accounts this test created: the signup count is what
        # `DashboardService._new_customers` would have reported for them.
        start, end = day_bounds_utc(DAY_SIGNUP, store_timezone(db))
        signups = db.execute(
            select(User.id).where(
                User.id.in_(owned.users),
                User.created_at >= start,
                User.created_at < end,
            )
        ).scalars().all()

        assert len(signups) == 3, "fixture: three accounts were created in the bucket"
        assert _delta(baseline, measured, "new_customers") == 1, (
            "new_customers must count customers whose FIRST revenue order landed "
            f"in the bucket, not the {len(signups)} accounts created that day"
        )
        assert _delta(baseline, measured, "returning_customers") == 1, (
            "the veteran had an earlier order, so a signup-based definition would "
            "have missed him entirely — his account is older than the window"
        )
        assert _delta(baseline, measured, "active_customers") == 2
        assert _delta(baseline, measured, "new_customers") < len(signups), (
            "the signup count is the always-larger number these two definitions "
            "are routinely confused for"
        )
    finally:
        _cleanup(owned)
        db.close()


def test_customer_daily_bucketing_is_store_local() -> None:
    """23:00 IST and 00:30 IST are two reporting days and one UTC date."""
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        runner = _runner(db)
        base_a = _baseline(
            db, runner, "customer_daily", AggCustomerDaily, DAY_CUSTOMER_TZ_A, generation
        )
        base_b = _baseline(
            db, runner, "customer_daily", AggCustomerDaily, DAY_CUSTOMER_TZ_B, generation
        )

        product = _create_product(db, owned, price="250.00", cost="100.00")
        late = _create_user(db, owned)
        early = _create_user(db, owned)

        late_order = _create_order(
            db, owned, late, [(product, 2)], created_at=_at(db, DAY_CUSTOMER_TZ_A, 23)
        )
        early_order = _create_order(
            db,
            owned,
            early,
            [(product, 1)],
            created_at=_at(db, DAY_CUSTOMER_TZ_B, 0, 30),
        )
        db.commit()

        assert late_order.created_at.date() == early_order.created_at.date(), (
            "fixture: both orders must share a UTC date for this test to mean "
            f"anything ({late_order.created_at} vs {early_order.created_at})"
        )

        runner.run_bucket("customer_daily", DAY_CUSTOMER_TZ_A)
        runner.run_bucket("customer_daily", DAY_CUSTOMER_TZ_B)

        day_a = _measures(_one(db, AggCustomerDaily, DAY_CUSTOMER_TZ_A, generation))
        day_b = _measures(_one(db, AggCustomerDaily, DAY_CUSTOMER_TZ_B, generation))

        assert (
            _delta(base_a, day_a, "active_customers") == 1
            and _delta(base_b, day_b, "active_customers") == 1
        ), "a UTC-day bucketer puts both orders in the earlier bucket: 2 and 0"
        assert _delta(base_a, day_a, "revenue_new") == Decimal("500.00")
        assert _delta(base_b, day_b, "revenue_new") == Decimal("250.00")
    finally:
        _cleanup(owned)
        db.close()


# ===========================================================================
# customer_snapshot
# ===========================================================================


def _snapshot_fixture(db: Session, owned: _Owned) -> tuple[User, User]:
    """Two customers whose snapshots differ in every interesting dimension.

    ``loyal`` bought twice in July with cost snapshots on both lines. ``lapsed``
    bought once in January, with no cost snapshot, and was partly refunded.
    """
    costed = _create_product(db, owned, price="500.00", cost="200.00")
    uncosted = _create_product(db, owned, price="300.00", cost=None)

    loyal = _create_user(db, owned)
    lapsed = _create_user(db, owned)

    _create_order(
        db,
        owned,
        loyal,
        [(costed, 2)],
        created_at=_at(db, DAY_SNAPSHOT_FIRST_ORDER, 11),
        discount="100.00",
        tax="90.00",
    )
    _create_order(
        db,
        owned,
        loyal,
        [(costed, 1)],
        created_at=_at(db, DAY_SNAPSHOT, 23),
    )
    old = _create_order(
        db,
        owned,
        lapsed,
        [(uncosted, 2)],
        created_at=_at(db, DAY_SNAPSHOT_OLD_ORDER, 10),
        payment_method="cod",
    )
    _create_refunded_return(
        db,
        owned,
        old,
        refunded_at=_at(db, DAY_SNAPSHOT_OLD_ORDER + timedelta(days=3), 12),
        refund_amount="300.00",
    )
    db.commit()
    return loyal, lapsed


def test_customer_snapshot_is_idempotent_with_realistic_values() -> None:
    """Two runs leave identical per-customer values, and the values are real.

    Assertions are scoped to **this test's own** ``customer_key``s. A snapshot
    row is per-customer and every money, tenure and cohort column on it is
    deterministic no matter who else is in the table; only the table-wide counts
    and the RFM quintiles depend on the rest of the population, and those are
    handled below without pretending otherwise.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        loyal, lapsed = _snapshot_fixture(db, owned)
        mine = {_customer_key(loyal.id), _customer_key(lapsed.id)}

        def owned_rows() -> dict:
            return {
                key: _measures(row)
                for key, row in _many(
                    db, AggCustomerSnapshot, DAY_SNAPSHOT, generation, "customer_key"
                ).items()
                if key in mine
            }

        def population() -> set:
            return set(
                _many(
                    db, AggCustomerSnapshot, DAY_SNAPSHOT, generation, "customer_key"
                )
            )

        runner = _runner(db)
        runner.run_bucket("customer_snapshot", DAY_SNAPSHOT)
        before = owned_rows()
        held = _bucket_row_count(db, AggCustomerSnapshot, DAY_SNAPSHOT, generation)
        population_before = population()

        second = runner.run_bucket("customer_snapshot", DAY_SNAPSHOT)
        after = owned_rows()

        assert set(before) == mine, (
            f"both customers must have a snapshot row; got {sorted(before)}"
        )
        assert second.rows_deleted == held, (
            "Pattern B deletes the bucket before reinserting it, so a rerun must "
            f"delete every row the bucket held; it held {held} and deleted "
            f"{second.rows_deleted}"
        )
        if population() == population_before:
            assert before == after, (
                f"re-running the snapshot changed its values. before={before} "
                f"after={after}"
            )
        else:
            # Another suite created a 2009 customer between the two runs. RFM
            # scores are quintiles over the population and legitimately move;
            # nothing else may, and that is still what an accumulating write
            # would break.
            assert _without_population_relative(
                before
            ) == _without_population_relative(after), (
                f"re-running the snapshot changed values that do not depend on the "
                f"population. before={before} after={after}"
            )

        good = before[_customer_key(loyal.id)]
        assert good["orders_count"] == 2
        assert good["units"] == 3
        # 2 x 500 - 100 discount + 90 tax = 990, plus 500 = 1490.
        assert good["gross_ltv"] == Decimal("1490.00")
        assert good["net_ltv"] == Decimal("1490.00"), "no refunds for this customer"
        # CM1: line value 1500 - discount 100 - COGS 600. Tax is not margin.
        assert good["margin_ltv"] == Decimal("800.00")
        assert good["aov"] == Decimal("745.00")
        assert good["recency_days"] == 0
        assert good["tenure_days"] == 21
        assert good["cohort_month"] == "2009-07"
        assert good["is_active"] is True
        assert good["churn_risk_band"] == "low"
        assert good["preferred_payment_method"] == "prepaid"
        assert good["quality"] == MetricQuality.AUTHORITATIVE.value
        assert good["user_id"] == loyal.id

        bad = before[_customer_key(lapsed.id)]
        assert bad["orders_count"] == 1
        assert bad["gross_ltv"] == Decimal("600.00")
        assert bad["net_ltv"] == Decimal("300.00"), "the refund is netted off"
        assert bad["quality"] == MetricQuality.INCOMPLETE.value, (
            "the order line carries no unit_cost, so margin_ltv is measured over "
            "the costed lines only and must say so"
        )
        assert bad["cohort_month"] == "2009-01"
        assert bad["churn_risk_band"] == "high"
        assert bad["is_active"] is False
        assert bad["preferred_payment_method"] == "cod"
        assert bad["recency_days"] > ACTIVE_WINDOW_DAYS

        # RFM scores are quintiles over the whole population on the snapshot
        # date, so their exact values are only defined relative to that
        # population. What IS defined for any population is asserted here, and
        # nothing weaker is smuggled in as if it were the real check:
        #
        #  * the best value in a population always takes the top band, and
        #    `loyal` ordered on the snapshot date itself — recency 0 is the
        #    minimum possible, so r_score 5 is deterministic. A scorer that
        #    inverted an ascending quintile instead of scoring negated recency
        #    hands the only customer in a small population r_score 1 ("lost"
        #    for somebody who bought this morning), and this catches it.
        #  * `_quintile` is monotone, so the more recent / more frequent
        #    customer can never score lower. True whoever else is in the table.
        assert good["r_score"] == 5, (
            "the most recent customer in the population must take the top R band"
        )
        assert good["r_score"] >= bad["r_score"]
        assert good["f_score"] >= bad["f_score"]
        for row in (good, bad):
            assert 1 <= row["r_score"] <= 5
            assert 1 <= row["f_score"] <= 5
            assert 1 <= row["m_score"] <= 5
            assert row["rfm_segment"] == _segment(
                row["r_score"], row["f_score"], row["m_score"]
            ), (
                "the stored segment must be the one the stored scores imply; a "
                "segment that drifts from its own scores is unauditable"
            )
    finally:
        _cleanup(owned)
        db.close()


def test_customer_snapshot_bucketing_is_store_local() -> None:
    """An order at 23:00 IST is inside that day's snapshot; 00:30 IST is not."""
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation

        product = _create_product(db, owned, price="200.00", cost="80.00")
        user = _create_user(db, owned)
        inside = _create_order(
            db, owned, user, [(product, 1)], created_at=_at(db, DAY_SNAPSHOT, 23)
        )
        outside = _create_order(
            db,
            owned,
            user,
            [(product, 5)],
            created_at=_at(db, DAY_SNAPSHOT + timedelta(days=1), 0, 30),
        )
        db.commit()

        assert inside.created_at.date() == outside.created_at.date(), (
            "fixture: both orders must share a UTC date for this test to mean "
            f"anything ({inside.created_at} vs {outside.created_at})"
        )

        _runner(db).run_bucket("customer_snapshot", DAY_SNAPSHOT)
        row = _many(db, AggCustomerSnapshot, DAY_SNAPSHOT, generation, "customer_key")[
            _customer_key(user.id)
        ]

        assert row.orders_count == 1, (
            "the 00:30 IST order belongs to the next reporting day; a UTC-day "
            "cutoff counts it here and reports 2"
        )
        assert row.gross_ltv == Decimal("200.00")
        assert row.units == 1
    finally:
        _cleanup(owned)
        db.close()


def test_customer_snapshot_retention_skips_aged_out_daily_buckets() -> None:
    """A bucket past the 90-day window that is not a month-end gets no rows.

    Without this the table is customers x days forever: 50k customers is 50k
    rows a day and 18M a year, for a table whose main use is current state.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        loyal, lapsed = _snapshot_fixture(db, owned)
        mine = {_customer_key(loyal.id), _customer_key(lapsed.id)}

        assert (date.today() - DAY_SNAPSHOT_PRUNED).days > RETENTION_DAILY_DAYS, (
            "fixture: the pruned day must genuinely be outside the daily window"
        )

        result = _runner(db).run_bucket("customer_snapshot", DAY_SNAPSHOT_PRUNED)
        rows = _many(
            db, AggCustomerSnapshot, DAY_SNAPSHOT_PRUNED, generation, "customer_key"
        )

        assert result.rows_written == 0 and not rows, (
            "an aged-out non-month-end bucket must hold no daily rows"
        )
        assert any("snapshot_retention_skipped" in w for w in result.warnings), (
            "retention declining to write must be distinguishable from a day with "
            f"no customers; warnings were {result.warnings}"
        )

        # ...and the month-end bucket in the same period is still built.
        _runner(db).run_bucket("customer_snapshot", DAY_SNAPSHOT)
        kept = _many(
            db, AggCustomerSnapshot, DAY_SNAPSHOT, generation, "customer_key"
        )
        assert mine <= set(kept), (
            "month-end rows are what the policy keeps beyond the daily window; "
            f"{sorted(mine - set(kept))} are missing"
        )
    finally:
        _cleanup(owned)
        db.close()


def test_customer_snapshot_retention_prunes_rows_written_when_they_were_fresh() -> None:
    """Rows that have since aged out are removed, not left to accumulate.

    The skip above only covers buckets computed after they aged. A row written
    on the day it described is inside the window then and outside it 91 days
    later, and nothing else in this system would ever revisit it.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        loyal, _lapsed = _snapshot_fixture(db, owned)

        stale = AggCustomerSnapshot(
            bucket_date=DAY_SNAPSHOT_PRUNED,
            customer_key=_customer_key(loyal.id),
            tz_generation=generation,
            computed_at=datetime.now(timezone.utc).replace(tzinfo=None),
            user_id=loyal.id,
            orders_count=1,
            rfm_segment="champions",
            cohort_month="2009-07",
            churn_risk_band="low",
            preferred_payment_method="prepaid",
            quality=MetricQuality.AUTHORITATIVE.value,
        )
        db.add(stale)
        db.commit()

        result = _runner(db).run_bucket("customer_snapshot", DAY_SNAPSHOT)

        assert not _many(
            db, AggCustomerSnapshot, DAY_SNAPSHOT_PRUNED, generation, "customer_key"
        ), (
            f"the daily row for {DAY_SNAPSHOT_PRUNED} is past the "
            f"{RETENTION_DAILY_DAYS}-day window and is not a month-end; it must "
            "have been pruned"
        )
        assert result.rows_deleted >= 1
        assert any("snapshot_retention_pruned" in w for w in result.warnings), (
            f"a prune must be reported, not silent; warnings were {result.warnings}"
        )
        assert _many(
            db, AggCustomerSnapshot, DAY_SNAPSHOT, generation, "customer_key"
        ), "the month-end bucket must survive the prune"
    finally:
        _cleanup(owned)
        db.close()


# ===========================================================================
# funnel_daily
# ===========================================================================


def _funnel_fixture(db: Session, owned: _Owned, day: date) -> None:
    """Two sessions with a realistic drop-off through the checkout."""
    session_a = f"sess-{_uid()}"
    session_b = f"sess-{_uid()}"
    steps_a = [
        (CartEventType.PRODUCT_VIEWED, 9),
        (CartEventType.PRODUCT_VIEWED, 10),
        (CartEventType.CART_VIEWED, 11),
        (CartEventType.ITEM_ADDED, 12),
        (CartEventType.CHECKOUT_STARTED, 13),
        (CartEventType.SHIPPING_SUBMITTED, 14),
        (CartEventType.PAYMENT_INITIATED, 15),
        (CartEventType.ORDER_PLACED, 16),
    ]
    steps_b = [
        (CartEventType.PRODUCT_VIEWED, 17),
        (CartEventType.ITEM_ADDED, 18),
        (CartEventType.ITEM_REMOVED, 18),
        (CartEventType.CHECKOUT_STARTED, 19),
        (CartEventType.PAYMENT_INITIATED, 20),
        (CartEventType.PAYMENT_FAILED, 21),
    ]
    for event_type, hour in steps_a:
        _create_cart_event(
            db,
            owned,
            occurred_at=_at(db, day, hour),
            event_type=event_type,
            session_key=session_a,
        )
    for event_type, hour in steps_b:
        _create_cart_event(
            db,
            owned,
            occurred_at=_at(db, day, hour),
            event_type=event_type,
            session_key=session_b,
        )
    db.commit()


def test_funnel_daily_is_idempotent_with_realistic_values() -> None:
    """Two runs leave identical counters, and every step counter is non-zero."""
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        runner = _runner(db)
        baseline = _baseline(
            db, runner, "funnel_daily", AggFunnelDaily, DAY_FUNNEL, generation
        )
        _funnel_fixture(db, owned, DAY_FUNNEL)

        first = runner.run_bucket("funnel_daily", DAY_FUNNEL)
        before = _measures(_one(db, AggFunnelDaily, DAY_FUNNEL, generation))
        second = runner.run_bucket("funnel_daily", DAY_FUNNEL)
        after = _measures(_one(db, AggFunnelDaily, DAY_FUNNEL, generation))

        assert first.rows_written == 1 and second.rows_written == 1
        assert before == after, (
            f"re-running the funnel changed its counters. before={before} "
            f"after={after}"
        )

        assert _delta(baseline, before, "product_views") == 3, (
            "events, not sessions: session A viewed two products"
        )
        assert _delta(baseline, before, "cart_views") == 1
        assert _delta(baseline, before, "items_added") == 2
        assert _delta(baseline, before, "checkouts_started") == 2
        assert _delta(baseline, before, "shipping_submitted") == 1
        assert _delta(baseline, before, "payments_initiated") == 2
        assert _delta(baseline, before, "payments_failed") == 1
        assert _delta(baseline, before, "orders_placed") == 1
        assert _delta(baseline, before, "distinct_sessions") == 2, (
            "two sessions reached a tracked step. This one is a DISTINCT count and "
            "is only additive here because the baseline sessions are disjoint from "
            "this test's freshly minted session keys"
        )

        rows = db.execute(
            select(AggFunnelDaily).where(
                AggFunnelDaily.bucket_date == DAY_FUNNEL,
                AggFunnelDaily.tz_generation == generation,
            )
        ).scalars().all()
        assert len(rows) == 1, "the UNIQUE key did not collapse the second run"
    finally:
        _cleanup(owned)
        db.close()


def test_funnel_daily_bucketing_is_store_local() -> None:
    """A 23:00 IST event and a 00:30 IST one are two days and one UTC date."""
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        runner = _runner(db)
        base_a = _baseline(
            db, runner, "funnel_daily", AggFunnelDaily, DAY_FUNNEL_TZ_A, generation
        )
        base_b = _baseline(
            db, runner, "funnel_daily", AggFunnelDaily, DAY_FUNNEL_TZ_B, generation
        )

        session_key = f"sess-{_uid()}"
        late = _create_cart_event(
            db,
            owned,
            occurred_at=_at(db, DAY_FUNNEL_TZ_A, 23),
            event_type=CartEventType.ITEM_ADDED,
            session_key=session_key,
        )
        early = _create_cart_event(
            db,
            owned,
            occurred_at=_at(db, DAY_FUNNEL_TZ_B, 0, 30),
            event_type=CartEventType.CHECKOUT_STARTED,
            session_key=session_key,
        )
        db.commit()

        assert late.occurred_at.date() == early.occurred_at.date(), (
            "fixture: both events must share a UTC date for this test to mean "
            f"anything ({late.occurred_at} vs {early.occurred_at})"
        )

        runner.run_bucket("funnel_daily", DAY_FUNNEL_TZ_A)
        runner.run_bucket("funnel_daily", DAY_FUNNEL_TZ_B)

        day_a = _measures(_one(db, AggFunnelDaily, DAY_FUNNEL_TZ_A, generation))
        day_b = _measures(_one(db, AggFunnelDaily, DAY_FUNNEL_TZ_B, generation))

        assert (
            _delta(base_a, day_a, "items_added"),
            _delta(base_a, day_a, "checkouts_started"),
        ) == (1, 0), "a UTC-day bucketer collapses both events into the earlier bucket"
        assert (
            _delta(base_b, day_b, "items_added"),
            _delta(base_b, day_b, "checkouts_started"),
        ) == (0, 1)
        assert _delta(base_a, day_a, "distinct_sessions") == 1
        assert _delta(base_b, day_b, "distinct_sessions") == 1
    finally:
        _cleanup(owned)
        db.close()


def test_funnel_daily_writes_zeros_and_does_not_invent_steps_from_orders() -> None:
    """An empty source produces zeros — including ``orders_placed``.

    ``cart_events`` is empty today because nothing emits funnel events yet. The
    tempting fix is to populate ``orders_placed`` from the orders table, which
    renders a funnel claiming every cart converts: wrong, flattering, and
    invisible to anyone who does not read the aggregation job. The day here has a
    real revenue order specifically so the job has something to be tempted by.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_days_are_empty(db, DAY_FUNNEL_ZEROS)
        _assert_no_cart_events(db)
        generation = active_generation(db).generation

        product = _create_product(db, owned, price="600.00", cost="250.00")
        user = _create_user(db, owned)
        _create_order(
            db, owned, user, [(product, 2)], created_at=_at(db, DAY_FUNNEL_ZEROS, 13)
        )
        db.commit()

        result = _runner(db).run_bucket("funnel_daily", DAY_FUNNEL_ZEROS)
        row = _one(db, AggFunnelDaily, DAY_FUNNEL_ZEROS, generation)

        assert result.rows_written == 1, "a measured zero is still a measurement"
        assert row is not None
        measures = _measures(row)
        for column in (
            "product_views",
            "cart_views",
            "items_added",
            "checkouts_started",
            "shipping_submitted",
            "payments_initiated",
            "payments_failed",
            "orders_placed",
            "distinct_sessions",
        ):
            assert measures[column] == 0, (
                f"{column} is {measures[column]} on a day with no cart_events; the "
                "funnel must not be reconstructed from the orders table"
            )
        assert any("funnel_source_empty" in w for w in result.warnings), (
            "zeros from an unwired source must be distinguishable from a quiet "
            f"day; warnings were {result.warnings}"
        )
    finally:
        _cleanup(owned)
        db.close()


# ===========================================================================
# inventory_daily
# ===========================================================================


def _inventory_fixture(db: Session, owned: _Owned) -> tuple[Product, Product]:
    """A costed product that sells and restocks, and an uncosted one that OOSes."""
    stocked = _create_product(db, owned, price="900.00", cost="40.00")
    oos = _create_product(db, owned, price="150.00", cost=None)

    _create_movement(
        db,
        owned,
        stocked,
        occurred_at=_at(db, DAY_INV_SEED, 9),
        movement_type=MovementType.INITIAL_SEED,
        delta=100,
    )
    _create_movement(
        db,
        owned,
        stocked,
        occurred_at=_at(db, DAY_INV, 11),
        movement_type=MovementType.COMMIT_SALE,
        delta=-12,
    )
    _create_movement(
        db,
        owned,
        stocked,
        occurred_at=_at(db, DAY_INV, 15),
        movement_type=MovementType.RETURN_RESTOCK,
        delta=2,
    )
    _create_movement(
        db,
        owned,
        oos,
        occurred_at=_at(db, DAY_INV_SEED, 9),
        movement_type=MovementType.INITIAL_SEED,
        delta=5,
    )
    _create_movement(
        db,
        owned,
        oos,
        occurred_at=_at(db, DAY_INV, 12),
        movement_type=MovementType.COMMIT_SALE,
        delta=-5,
    )
    db.commit()
    return stocked, oos


def test_inventory_daily_is_idempotent_with_realistic_values() -> None:
    """Two runs leave identical levels and flows, reconstructed from the ledger."""
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_history_since_absent(db)
        generation = active_generation(db).generation
        stocked, oos = _inventory_fixture(db, owned)

        mine = {stocked.id, oos.id}

        def owned_rows(bucket: date) -> dict:
            return {
                pid: _measures(row)
                for pid, row in _many(
                    db, AggInventoryDaily, bucket, generation, "product_id"
                ).items()
                if pid in mine
            }

        runner = _runner(db)
        first = runner.run_bucket("inventory_daily", DAY_INV)
        before = owned_rows(DAY_INV)
        held = _bucket_row_count(db, AggInventoryDaily, DAY_INV, generation)

        second = runner.run_bucket("inventory_daily", DAY_INV)
        after = owned_rows(DAY_INV)

        assert set(before) == mine, (
            f"both ledger-covered products must have a row; got {sorted(before)}"
        )
        assert second.rows_deleted == held, (
            "Pattern B deletes the bucket before reinserting it, so a rerun must "
            f"delete every row the bucket held; it held {held} and deleted "
            f"{second.rows_deleted}"
        )
        assert before == after, (
            f"re-running the ledger changed its values. before={before} after={after}"
        )

        good = before[stocked.id]
        assert good["stock_close"] == 90, "100 seeded - 12 sold + 2 restocked"
        assert good["units_sold"] == 12
        assert good["units_restocked"] == 2
        assert good["stock_value_close"] == Decimal("3600.00"), "90 units at 40.00"
        assert good["is_oos"] is False
        assert good["days_oos"] == 0
        assert good["sku_snapshot"] == stocked.sku
        assert good["reorder_gap"] is None, (
            "no product carries a reorder point in this schema; NULL means 'not "
            "configured', which is not the same statement as 0"
        )

        empty = before[oos.id]
        assert empty["stock_close"] == 0
        assert empty["units_sold"] == 5
        assert empty["is_oos"] is True
        assert empty["days_oos"] == 1
        assert empty["stock_value_close"] == Decimal("0.00")
        assert any("inventory_cost_coverage" in w for w in first.warnings), (
            f"an unvalued product must be reported; warnings were {first.warnings}"
        )

        # The next day: the level carries, the flows do not, and the OOS run grows.
        runner.run_bucket("inventory_daily", DAY_INV_NEXT)
        next_day = owned_rows(DAY_INV_NEXT)
        assert next_day[stocked.id]["stock_close"] == 90, "a level, not a flow"
        assert next_day[stocked.id]["units_sold"] == 0, "a flow, not a level"
        assert next_day[oos.id]["days_oos"] == 2, "consecutive out-of-stock days"
    finally:
        _cleanup(owned)
        db.close()


def test_inventory_daily_bucketing_is_store_local() -> None:
    """A sale at 23:00 IST is that day's flow, not the next day's."""
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_history_since_absent(db)
        generation = active_generation(db).generation
        product = _create_product(db, owned, price="120.00", cost="50.00")

        _create_movement(
            db,
            owned,
            product,
            occurred_at=_at(db, DAY_INV_SEED, 9),
            movement_type=MovementType.INITIAL_SEED,
            delta=40,
        )
        late = _create_movement(
            db,
            owned,
            product,
            occurred_at=_at(db, DAY_INV_TZ_A, 23),
            movement_type=MovementType.COMMIT_SALE,
            delta=-4,
        )
        early = _create_movement(
            db,
            owned,
            product,
            occurred_at=_at(db, DAY_INV_TZ_B, 0, 30),
            movement_type=MovementType.COMMIT_SALE,
            delta=-6,
        )
        db.commit()

        assert late.occurred_at.date() == early.occurred_at.date(), (
            "fixture: both movements must share a UTC date for this test to mean "
            f"anything ({late.occurred_at} vs {early.occurred_at})"
        )

        runner = _runner(db)
        runner.run_bucket("inventory_daily", DAY_INV_TZ_A)
        runner.run_bucket("inventory_daily", DAY_INV_TZ_B)

        day_a = _many(db, AggInventoryDaily, DAY_INV_TZ_A, generation, "product_id")[
            product.id
        ]
        day_b = _many(db, AggInventoryDaily, DAY_INV_TZ_B, generation, "product_id")[
            product.id
        ]

        assert day_a.units_sold == 4, (
            "a UTC-day bucketer folds the 00:30 IST sale into the earlier day and "
            "reports 10"
        )
        assert day_a.stock_close == 36
        assert day_b.units_sold == 6
        assert day_b.stock_close == 30
    finally:
        _cleanup(owned)
        db.close()


def test_inventory_daily_sets_history_since_and_refuses_to_backfill() -> None:
    """The first run records where history starts; earlier buckets are refused.

    Reconstructing a past stock level needs the restocks and manual edits that
    were never recorded, so an earlier bucket is left absent rather than
    fabricated — and the setting exists so a history view can clamp its axis
    instead of drawing a flat zero line that reads as "we held no stock".
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_history_since_absent(db)
        generation = active_generation(db).generation
        stocked, oos = _inventory_fixture(db, owned)

        runner = _runner(db)
        first = runner.run_bucket("inventory_daily", DAY_INV_SINCE)
        written = _many(
            db, AggInventoryDaily, DAY_INV_SINCE, generation, "product_id"
        )

        marker = db.execute(
            select(SystemSetting.value).where(
                SystemSetting.key == INVENTORY_HISTORY_SINCE_KEY
            )
        ).scalars().first()
        assert {product.id for product in (stocked, oos)} <= set(written)
        assert marker == DAY_INV_SINCE.isoformat(), (
            f"{INVENTORY_HISTORY_SINCE_KEY} must record the first bucket ever "
            f"built; it holds {marker!r}"
        )
        assert any("inventory_history_started" in w for w in first.warnings)

        earlier = runner.run_bucket("inventory_daily", DAY_INV_BEFORE_SINCE)
        rows = _many(
            db, AggInventoryDaily, DAY_INV_BEFORE_SINCE, generation, "product_id"
        )

        assert earlier.rows_written == 0 and not rows, (
            "a bucket before the ledger's first date must be left absent; a row of "
            "reconstructed stock there would be fiction"
        )
        assert any("inventory_backfill_refused" in w for w in earlier.warnings), (
            f"the refusal must be recorded; warnings were {earlier.warnings}"
        )

        after = db.execute(
            select(SystemSetting.value).where(
                SystemSetting.key == INVENTORY_HISTORY_SINCE_KEY
            )
        ).scalars().first()
        assert after == DAY_INV_SINCE.isoformat(), (
            "the refused bucket must not move the start of history backwards"
        )
    finally:
        _cleanup(owned)
        db.close()


# ===========================================================================
# shipment_daily
# ===========================================================================


def _shipment_fixture(db: Session, owned: _Owned) -> tuple[str, Order]:
    """One courier with three outcomes, plus an unattributed shipment."""
    courier = f"delhivery-{_uid()}"[:64]
    product = _create_product(db, owned, price="700.00", cost="300.00")
    user = _create_user(db, owned)
    order = _create_order(
        db, owned, user, [(product, 1)], created_at=_at(db, DAY_SHIP, 10)
    )

    _create_shipment(
        db,
        owned,
        order,
        created_at=_at(db, DAY_SHIP, 11),
        courier=courier,
        status=ShipmentStatus.DELIVERED,
        cost="80.00",
        shipped_at=_at(db, DAY_SHIP, 14),
        delivered_at=_at(db, DAY_SHIP_DELIVERED, 14),
    )
    _create_shipment(
        db,
        owned,
        order,
        created_at=_at(db, DAY_SHIP, 12),
        courier=courier,
        status=ShipmentStatus.RTO_DELIVERED,
        cost="60.00",
        shipped_at=_at(db, DAY_SHIP, 16),
    )
    _create_shipment(
        db,
        owned,
        order,
        created_at=_at(db, DAY_SHIP, 13),
        courier=courier,
        status=ShipmentStatus.CANCELLED,
        cost="10.00",
    )
    # No courier assigned yet, and no cost: the '-' sentinel and the coverage
    # warning in one row.
    _create_shipment(
        db,
        owned,
        order,
        created_at=_at(db, DAY_SHIP, 18),
        courier=None,
        status=ShipmentStatus.PENDING,
    )
    db.commit()
    return courier, order


def test_shipment_daily_is_idempotent_with_realistic_values() -> None:
    """Two runs leave identical counters, costs and duration sums."""
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_days_are_empty(db, DAY_SHIP)
        generation = active_generation(db).generation
        courier, _order = _shipment_fixture(db, owned)

        mine = {courier, "-"}

        def owned_rows() -> dict:
            return {
                key: _measures(row)
                for key, row in _many(
                    db, AggShipmentDaily, DAY_SHIP, generation, "courier_partner"
                ).items()
                if key in mine
            }

        runner = _runner(db)
        runner.run_bucket("shipment_daily", DAY_SHIP)
        before = owned_rows()
        held = _bucket_row_count(db, AggShipmentDaily, DAY_SHIP, generation)

        second = runner.run_bucket("shipment_daily", DAY_SHIP)
        after = owned_rows()

        assert set(before) == mine, (
            f"both the named courier and the '-' sentinel must have a row; got "
            f"{sorted(before)}"
        )
        assert second.rows_deleted == held, (
            "Pattern B deletes the bucket before reinserting it, so a rerun must "
            f"delete every row the bucket held; it held {held} and deleted "
            f"{second.rows_deleted}"
        )
        assert before == after, (
            f"re-running the day changed its values. before={before} after={after}"
        )

        row = before[courier]
        assert row["shipments"] == 3
        assert row["delivered"] == 1
        assert row["delivery_failed"] == 0
        assert row["cancelled"] == 1
        assert row["rto_initiated"] == 1, (
            "an RTO_DELIVERED parcel was necessarily initiated; counting only the "
            "RTO_INITIATED status makes the RTO rate fall as returns complete"
        )
        assert row["rto_delivered"] == 1
        assert row["shipment_cost"] == Decimal("150.00")

        # Order placed 10:00 IST; shipped 14:00 and 16:00 IST.
        assert row["n_order_to_ship"] == 2
        assert row["sum_order_to_ship_seconds"] == 4 * 3600 + 6 * 3600
        # Only the delivered parcel has both legs: 14:00 on the 4th -> 14:00 on
        # the 6th.
        assert row["n_ship_to_deliver"] == 1
        assert row["sum_ship_to_deliver_seconds"] == 2 * 24 * 3600

        sentinel = before["-"]
        assert sentinel["shipments"] == 1, (
            "a shipment with no courier must land under the '-' sentinel; a NULL "
            "does not collide under a MySQL UNIQUE key and would double-count"
        )
        assert sentinel["shipment_cost"] == Decimal("0.00")
        assert any("shipment_cost_coverage" in w for w in second.warnings), (
            "a missing shipment cost must be reported; warnings were "
            f"{second.warnings}"
        )
    finally:
        _cleanup(owned)
        db.close()


def test_shipment_daily_bucketing_is_store_local() -> None:
    """A shipment created at 23:00 IST and one at 00:30 IST are two days."""
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_days_are_empty(db, DAY_SHIP_TZ_A, DAY_SHIP_TZ_B)
        generation = active_generation(db).generation

        product = _create_product(db, owned, price="300.00", cost="120.00")
        user = _create_user(db, owned)
        order = _create_order(
            db, owned, user, [(product, 1)], created_at=_at(db, DAY_SHIP_TZ_A, 9)
        )
        courier = f"bluedart-{_uid()}"[:64]

        late = _create_shipment(
            db,
            owned,
            order,
            created_at=_at(db, DAY_SHIP_TZ_A, 23),
            courier=courier,
            status=ShipmentStatus.SHIPPED,
            cost="45.00",
        )
        early = _create_shipment(
            db,
            owned,
            order,
            created_at=_at(db, DAY_SHIP_TZ_B, 0, 30),
            courier=courier,
            status=ShipmentStatus.SHIPPED,
            cost="55.00",
        )
        db.commit()

        assert late.created_at.date() == early.created_at.date(), (
            "fixture: both shipments must share a UTC date for this test to mean "
            f"anything ({late.created_at} vs {early.created_at})"
        )

        runner = _runner(db)
        runner.run_bucket("shipment_daily", DAY_SHIP_TZ_A)
        runner.run_bucket("shipment_daily", DAY_SHIP_TZ_B)

        day_a = _many(db, AggShipmentDaily, DAY_SHIP_TZ_A, generation, "courier_partner")
        day_b = _many(db, AggShipmentDaily, DAY_SHIP_TZ_B, generation, "courier_partner")

        assert day_a[courier].shipments == 1, (
            "a UTC-day bucketer puts both shipments in the earlier bucket"
        )
        assert day_a[courier].shipment_cost == Decimal("45.00")
        assert day_b[courier].shipments == 1
        assert day_b[courier].shipment_cost == Decimal("55.00")
    finally:
        _cleanup(owned)
        db.close()


def test_shipment_daily_has_no_on_time_column() -> None:
    """There is no promised-delivery date in this schema, so there is no OTD.

    Any "on-time %" would be measured against a threshold invented at query time
    and presented as a fact. The model docstring names ``on_time``, ``sla_met``
    and ``late_shipments`` as forbidden; this pins that so a future column cannot
    be added without someone reading the reasoning first. What must exist instead
    is the honest input: two sum+count pairs with their own denominators.
    """
    columns = {column.name for column in AggShipmentDaily.__table__.columns}

    forbidden = {
        name
        for name in columns
        if "on_time" in name
        or "sla" in name
        or "late" in name
        or "promised" in name
        or "eta" in name
    }
    assert not forbidden, (
        f"{sorted(forbidden)} imply a promised delivery date that does not exist "
        "anywhere in this schema — not on shipments, not on orders, not in any "
        "persisted courier payload. Capture the promise at shipment creation and "
        "start a forward-only series instead of backfilling an assumption"
    )

    for pair in (
        ("sum_order_to_ship_seconds", "n_order_to_ship"),
        ("sum_ship_to_deliver_seconds", "n_ship_to_deliver"),
    ):
        assert set(pair) <= columns, (
            f"{pair} is the honest substitute for an on-time rate and must stay: "
            "a sum with its own count, divided at query time"
        )
    assert not any(name.startswith("avg_") for name in columns), (
        "sums and counts, never stored averages: an average of averages is wrong "
        "the moment a daily bucket is re-bucketed to a week, and it is wrong "
        "silently"
    )
