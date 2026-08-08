"""Regression guards for the four measured performance defects.

Every assertion here corresponds to a number in ``docs/analytics/PERFORMANCE.md``
and to a change made because of it:

===========================================  ==============================  =========
Defect                                       Fixed in                        Guarded by
===========================================  ==============================  =========
§5.1 ``customer_snapshot``: 6 full-history   ``jobs_customer._collect``      query-count
scans/bucket, 21.3 s of which 77% Python     ``jobs_customer._insert``       + value
§5.2 cost rules: 1 SELECT per (type, day)    ``cost_rules``                  query-count
§5.4 ``SELECT DISTINCT tz_generation``       migration ``e1c5b7a04d92``      reflection
§5.5 ``product_daily``: 238 queries/bucket   ``allocation._preload_products``query-count
===========================================  ==============================  =========

Why query counts and not timings
--------------------------------
A timing assertion on a laptop that is also running a browser is a test people
delete. A query count is deterministic: it does not move when the machine is
busy, and it is the thing that actually regressed — every one of these defects
was "the same work, asked for one row at a time".

The ceilings are set from measurements taken on the profiling dataset described
in ``PERFORMANCE.md`` §9 (a 20 000-order / 6 000-customer / 60-product build of
the same generator), then given generous headroom, because they must hold on a
throwaway CI database whose contents this suite does not control. They are still
an order of magnitude below the counts the defects produced, which is the only
property that matters: a reintroduced N+1 here is *per customer* or *per
product*, so it lands in the hundreds and cannot hide under a doubled ceiling.

One is deliberately not a constant. ``customer_snapshot`` inserts in batches of
``SNAPSHOT_INSERT_CHUNK``, so its query count legitimately grows with the
customer population; the ceiling is expressed as "a fixed budget plus one per
insert batch" rather than as a number that silently becomes wrong when the
throwaway database has more customers in it than it did today.

What a counted query is
-----------------------
One ``before_cursor_execute``, i.e. one statement handed to the driver. An
``executemany`` counts once however many rows it carries — which is the point of
the ``_insert`` change and is why the count moved so much less than the wall
clock did.

Conventions follow the neighbouring analytics suites: no shared ``db`` fixture,
each test owns its ``SessionLocal()`` and cleans up in ``finally`` through a
fresh session, and every fixture row is keyed to this module (a 2011 sandbox,
synthetic ``cost_type``s) so nothing it asserts can be decided by another
suite's data.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from math import ceil

import pytest
import redis
from sqlalchemy import event, inspect, select, text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal, engine
from app.models.analytics_control import (
    AnalyticsCostRule,
    CostQuality,
    CostScope,
    CostUnit,
)
from app.models.analytics_rollups import AggCustomerSnapshot
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Product
from app.models.user import User
from app.services.analytics.aggregation import AggregationRunner
from app.services.analytics.aggregation.jobs_customer import (
    SNAPSHOT_INSERT_CHUNK,
    _customer_key,
)
from app.services.analytics.cost_rules import CostRuleResolver
from app.services.analytics.timebox import active_generation, day_bounds_utc, store_timezone

# ---------------------------------------------------------------------------
# The 2011 sandbox
# ---------------------------------------------------------------------------
# A different year from every other analytics suite, so a concurrent run of one
# of them cannot land an order inside a bucket this module measures. May 31st
# because the retention policy only keeps daily snapshot rows for 90 days and
# month-end rows forever — a non-month-end 2011 bucket would correctly write
# nothing at all, and every assertion below would be vacuous.
DAY_SNAPSHOT = date(2011, 5, 31)
DAY_PRODUCT = date(2011, 5, 24)

#: Inside the trailing RFM window as of DAY_SNAPSHOT (365 days).
DAY_RECENT_A = date(2011, 5, 20)
DAY_RECENT_B = date(2011, 5, 25)
#: Outside it, and outside the 90-day active window: the lifetime-only orders.
DAY_OLD = date(2010, 1, 10)

SANDBOX_FIRST = date(2010, 1, 1)
SANDBOX_LAST = date(2011, 12, 31)

WORKER_ID = "test-perf-fixes"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _at(db: Session, day: date, hour: int) -> datetime:
    """The UTC instant that is `hour`:00 STORE-LOCAL on `day`."""
    start, _ = day_bounds_utc(day, store_timezone(db))
    return start + timedelta(hours=hour)


# ---------------------------------------------------------------------------
# Query counting
# ---------------------------------------------------------------------------


class _Counter:
    def __init__(self) -> None:
        self.statements: list[str] = []

    @property
    def count(self) -> int:
        return len(self.statements)

    def summary(self) -> str:
        """The distinct statements and their counts, newest reasoning first.

        Printed into the assertion message rather than just the total: a failure
        that says "47, expected under 30" sends someone hunting, and a failure
        that says "31 x SELECT products ..." names the regression.
        """
        seen: dict[str, int] = {}
        for statement in self.statements:
            fingerprint = " ".join(statement.split())[:110]
            seen[fingerprint] = seen.get(fingerprint, 0) + 1
        return "\n".join(
            f"  {n:4d}x  {fingerprint}"
            for fingerprint, n in sorted(seen.items(), key=lambda kv: -kv[1])
        )


@contextmanager
def _count_queries():
    """Count every statement the shared engine executes inside the block."""
    counter = _Counter()

    def _record(conn, cursor, statement, parameters, context, executemany):
        counter.statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        yield counter
    finally:
        event.remove(engine, "before_cursor_execute", _record)


# ---------------------------------------------------------------------------
# Redis doubles
# ---------------------------------------------------------------------------


class _FakeRedis:
    """In-process stand-in that also speaks the bulk verbs.

    Per-instance state, so one test can never serve a cached answer to another —
    which for a cache of *missing* rules would hide exactly the bug the cost
    module exists to prevent.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.gets = 0
        self.mgets = 0

    def get(self, key: str):
        self.gets += 1
        return self.store.get(key)

    def mget(self, keys):
        self.mgets += 1
        return [self.store.get(key) for key in keys]

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.store[key] = value

    def delete(self, key: str) -> None:
        self.store.pop(key, None)

    def scan_iter(self, match: str = "*", count: int = 100):
        prefix = match.rstrip("*")
        return [k for k in list(self.store) if k.startswith(prefix)]

    def pipeline(self, transaction: bool = True):
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, client: _FakeRedis) -> None:
        self.client = client
        self.queued: list[tuple[str, str]] = []

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.queued.append((key, value))

    def execute(self) -> None:
        for key, value in self.queued:
            self.client.store[key] = value
        self.queued.clear()


class _BrokenRedis:
    """Every call raises, the way a real client does when Redis is unreachable."""

    def get(self, key: str):
        raise redis.RedisError("connection refused")

    def mget(self, keys):
        raise redis.RedisError("connection refused")

    def setex(self, key: str, ttl: int, value: str):
        raise redis.RedisError("connection refused")

    def delete(self, key: str):
        raise redis.RedisError("connection refused")

    def scan_iter(self, match: str = "*", count: int = 100):
        raise redis.RedisError("connection refused")

    def pipeline(self, transaction: bool = True):
        raise redis.RedisError("connection refused")


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


class _Owned:
    def __init__(self) -> None:
        self.orders: list[int] = []
        self.products: list[int] = []
        self.users: list[int] = []
        self.rules: list[int] = []


def _create_user(db: Session, owned: _Owned) -> User:
    user = User(
        email=f"perffix-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.flush()
    owned.users.append(user.id)
    return user


def _create_product(
    db: Session, owned: _Owned, *, price: str = "500.00", cost: str | None = "200.00"
) -> Product:
    product = Product(
        sku=f"SKU-PF-{_uid()}",
        name=f"PerfFixProduct {_uid()}",
        price=Decimal(price),
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
    payment_method: str = "prepaid",
    discount: str = "0",
    tax: str = "0",
    status: OrderStatus = OrderStatus.PAID,
) -> Order:
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

    order = Order(
        user_id=user.id,
        status=status,
        subtotal=gross,
        tax_amount=Decimal(tax),
        discount_amount=Decimal(discount),
        payment_discount_amount=Decimal("0.00"),
        shipping_amount=Decimal("0.00"),
        cod_surcharge_amount=Decimal("0.00"),
        cod_balance=Decimal("0.00"),
        total_amount=gross - Decimal(discount) + Decimal(tax),
        currency="INR",
        payment_method=payment_method,
        created_at=created_at,
    )
    order.items = order_items
    db.add(order)
    db.flush()
    owned.orders.append(order.id)
    return order


def _cost_type() -> str:
    """A cost_type nothing else in the database can be using."""
    return f"perffix_{uuid.uuid4().hex[:12]}"


def _rule(
    db: Session,
    owned: _Owned,
    cost_type: str,
    *,
    value: str,
    effective_from: date,
    effective_to: date | None = None,
    scope: str = CostScope.GLOBAL,
    scope_value: str = "-",
    unit: str = CostUnit.PER_ORDER,
) -> AnalyticsCostRule:
    row = AnalyticsCostRule(
        cost_type=cost_type,
        scope=scope,
        scope_value=scope_value,
        value=Decimal(value),
        unit=unit,
        currency="INR",
        quality=CostQuality.CONTRACTED,
        effective_from=effective_from,
        effective_to=effective_to,
        source="test-perf-fixes",
    )
    db.add(row)
    db.flush()
    owned.rules.append(row.id)
    return row


def _cleanup(owned: _Owned) -> None:
    with SessionLocal() as session:
        if owned.rules:
            session.execute(
                text("DELETE FROM analytics_cost_rules WHERE id IN :ids"),
                {"ids": tuple(owned.rules)},
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
        for table in ("agg_customer_snapshot", "agg_product_daily", "analytics_recompute_queue"):
            session.execute(
                text(f"DELETE FROM {table} WHERE bucket_date BETWEEN :first AND :last"),
                window,
            )
        session.execute(
            text("DELETE FROM analytics_sync_runs WHERE worker_id = :worker"),
            {"worker": WORKER_ID},
        )
        session.commit()


def _runner(db: Session) -> AggregationRunner:
    return AggregationRunner(db, worker_id=WORKER_ID)


def _snapshot_rows(db: Session, generation: int) -> dict[str, AggCustomerSnapshot]:
    db.expire_all()
    rows = db.execute(
        select(AggCustomerSnapshot).where(
            AggCustomerSnapshot.bucket_date == DAY_SNAPSHOT,
            AggCustomerSnapshot.tz_generation == generation,
        )
    ).scalars().all()
    return {row.customer_key: row for row in rows}


_NON_MEASURE_COLUMNS = frozenset({"id", "computed_at"})


def _measures(row) -> dict:
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name not in _NON_MEASURE_COLUMNS
    }


# ===========================================================================
# §5.1 — customer_snapshot
# ===========================================================================

#: Everything one `customer_snapshot` bucket does that is NOT an insert batch:
#: the runner's generation read and its two run-log writes, the job's DELETE,
#: the retention prune, and the four collect queries. Measured at 10 on the
#: profiling dataset (21 statements, 11 of them insert batches) and 11 here.
#: Doubled, so a throwaway CI database with an unexpected shape cannot fail it
#: while a statement issued per customer — which is what every regression in
#: this job has looked like — still cannot fit underneath it.
SNAPSHOT_FIXED_QUERY_BUDGET = 24


def test_customer_snapshot_one_bucket_stays_inside_its_query_budget() -> None:
    """One bucket is a fixed number of statements plus one per insert batch.

    The guard is on the shape, not the size: nothing in this job may issue a
    statement *per customer*. PERFORMANCE.md §5.1 measured six full-history
    scans per bucket where three of them were the same scan asked three ways;
    §4.2 measured 35 statements for a bucket that wrote 11 024 rows, 23 of them
    INSERT round trips.

    The insert allowance is derived from `SNAPSHOT_INSERT_CHUNK` rather than
    hard-coded, so this stays true on a database with a different customer
    population — including one where another suite has left purchasers behind.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        product = _create_product(db, owned)
        user = _create_user(db, owned)
        _create_order(db, owned, user, [(product, 2)], created_at=_at(db, DAY_RECENT_A, 11))
        db.commit()

        with _count_queries() as counter:
            result = _runner(db).run_bucket("customer_snapshot", DAY_SNAPSHOT)

        batches = ceil(result.rows_written / SNAPSHOT_INSERT_CHUNK)
        ceiling = SNAPSHOT_FIXED_QUERY_BUDGET + batches
        assert counter.count <= ceiling, (
            f"customer_snapshot issued {counter.count} statements for one bucket "
            f"writing {result.rows_written} rows; the budget is "
            f"{SNAPSHOT_FIXED_QUERY_BUDGET} + {batches} insert batch(es) = "
            f"{ceiling}. A count that scales with the customer population is the "
            f"defect this guards.\n{counter.summary()}"
        )
        assert result.rows_written >= 1, (
            "fixture: this test's customer must be in the snapshot, or the count "
            "above is measuring an empty job"
        )
    finally:
        _cleanup(owned)
        db.close()


def test_customer_snapshot_merged_scan_measures_exactly_what_six_did() -> None:
    """The three merged `orders` scans still produce their own answers.

    `_collect` used to read `orders` three times over one predicate: lifetime
    totals, the trailing-RFM-window totals, and the modal payment method. They
    are now one scan grouped by ``(user_id, payment_method)`` and folded in
    Python, which is only safe if every one of those answers survives the fold.
    The fixture is built so that a fold that is wrong in *any* of the three ways
    it could be wrong produces a different number here:

      * **the group fold** — one customer with orders under two payment methods,
        so lifetime COUNT/SUM/MIN/MAX are sums and extremes across sub-groups
        rather than a single group's value;
      * **the window conditional** — one of that customer's orders is outside
        the trailing RFM window, so `frequency`/`monetary` must exclude it while
        `orders_count`/`gross_ltv` include it. A CASE that leaked would make the
        two pairs equal;
      * **the modal tie-break** — a second customer with exactly one order under
        each method, where the winner is decided by the most recent order and by
        nothing else.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        product = _create_product(db, owned, price="500.00", cost="200.00")

        mixed = _create_user(db, owned)
        # In the RFM window. 2 x 500 - 100 discount + 90 tax = 990.
        _create_order(
            db, owned, mixed, [(product, 2)],
            created_at=_at(db, DAY_RECENT_A, 11),
            payment_method="prepaid", discount="100.00", tax="90.00",
        )
        # In the window, and the second `cod` order, so `cod` is the mode.
        _create_order(
            db, owned, mixed, [(product, 1)],
            created_at=_at(db, DAY_RECENT_B, 9), payment_method="cod",
        )
        # Outside the window: lifetime only.
        _create_order(
            db, owned, mixed, [(product, 1)],
            created_at=_at(db, DAY_OLD, 15), payment_method="cod",
        )

        tied = _create_user(db, owned)
        _create_order(
            db, owned, tied, [(product, 1)],
            created_at=_at(db, DAY_RECENT_A, 8), payment_method="prepaid",
        )
        _create_order(
            db, owned, tied, [(product, 1)],
            created_at=_at(db, DAY_RECENT_B, 8), payment_method="cod",
        )
        db.commit()

        _runner(db).run_bucket("customer_snapshot", DAY_SNAPSHOT)
        rows = _snapshot_rows(db, generation)

        row = rows[_customer_key(mixed.id)]
        assert row.orders_count == 3, "lifetime COUNT must sum the method sub-groups"
        assert row.gross_ltv == Decimal("1990.00"), (
            "lifetime SUM must sum the method sub-groups: 990 + 500 + 500"
        )
        assert row.units == 4 and row.margin_ltv == Decimal("1100.00"), (
            "line value 2000 - discount 100 - COGS 800; tax is not margin"
        )
        assert row.frequency == 2, (
            "the 2010 order is outside the trailing RFM window and must not be "
            "counted, even though orders_count includes it"
        )
        assert row.monetary == Decimal("1490.00"), "990 + 500, excluding the old order"
        assert row.frequency < row.orders_count and row.monetary < row.gross_ltv, (
            "the windowed pair must be strictly smaller than the lifetime pair "
            "here; equal values would mean the CASE leaked the whole history"
        )
        assert row.preferred_payment_method == "cod", (
            "two cod orders against one prepaid: the mode is over lifetime orders"
        )
        assert row.cohort_month == "2010-01", "MIN(created_at) across sub-groups"
        assert row.recency_days == (DAY_SNAPSHOT - DAY_RECENT_B).days, (
            "MAX(created_at) across sub-groups"
        )

        tie = rows[_customer_key(tied.id)]
        assert tie.preferred_payment_method == "cod", (
            "one order per method is a tie on count; it breaks on the most recent "
            f"order, which is the {DAY_RECENT_B} cod one"
        )

        # ...and running it again changes nothing. Pattern B deletes and
        # reinserts, so a fold that accumulated instead of overwriting would
        # double every figure above on the second pass.
        before = {key: _measures(row) for key, row in rows.items()}
        _runner(db).run_bucket("customer_snapshot", DAY_SNAPSHOT)
        after = {key: _measures(row) for key, row in _snapshot_rows(db, generation).items()}
        for key in (_customer_key(mixed.id), _customer_key(tied.id)):
            assert before[key] == after[key], (
                f"recomputing the bucket changed {key}: {before[key]} -> {after[key]}"
            )
    finally:
        _cleanup(owned)
        db.close()


# ===========================================================================
# §5.5 — product_daily / the allocator's lazy load
# ===========================================================================

#: `product_daily` for one bucket: the runner's three, the job's own reads and
#: writes, and TWO for the whole catalogue it touches (products + their taxes).
#: Measured at 14 on the profiling dataset — against 132 there before the fix,
#: and 238 in PERFORMANCE.md §4.2 at full volume — and 15 here. 30 leaves room
#: for a differently-shaped test database while staying far below "two per
#: distinct product sold".
PRODUCT_DAILY_QUERY_CEILING = 30


def test_product_daily_one_bucket_stays_inside_its_query_budget() -> None:
    """No statement per product, however many products the day sold.

    PERFORMANCE.md §5.5: `allocate_order` reaches through `OrderItem.product`
    (`lazy="select"`) and `Product.taxes` (`lazy="selectin"`), which cost two
    queries per distinct product per Session — 238 for a 113-product catalogue,
    growing with the catalogue rather than with the work.

    Three products across three orders is enough to catch the regression: the
    pre-load either covers the bucket in one statement or it does not, and if it
    does not the count rises with every product added here.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        products = [
            _create_product(db, owned, price=f"{100 + n}.00", cost="40.00")
            for n in range(3)
        ]
        user = _create_user(db, owned)
        for index, product in enumerate(products):
            _create_order(
                db, owned, user, [(product, index + 1)],
                created_at=_at(db, DAY_PRODUCT, 10 + index), tax="18.00",
            )
        db.commit()

        with _count_queries() as counter:
            result = _runner(db).run_bucket("product_daily", DAY_PRODUCT)

        assert result.rows_written >= len(products), (
            "fixture: every product must appear in the bucket, or the count "
            "below is measuring a job that had nothing to allocate"
        )
        assert counter.count <= PRODUCT_DAILY_QUERY_CEILING, (
            f"product_daily issued {counter.count} statements for a bucket with "
            f"{len(products)} products; the ceiling is "
            f"{PRODUCT_DAILY_QUERY_CEILING}. A count that grows with the "
            f"catalogue is the defect this guards.\n{counter.summary()}"
        )
    finally:
        _cleanup(owned)
        db.close()


def test_the_allocator_still_reads_the_product_it_preloaded() -> None:
    """The pre-load is an access-pattern change, not a change of answer.

    Tax splits on each line's own rate when every line resolves one, and falls
    back to extended price when any line does not. Both the rate and the
    fallback come from `item.product`, so this is where a pre-load that attached
    the wrong row would show — and it would show as a *different split*, not as
    an exception.

    Two products with deliberately different rates and a price ratio chosen so
    the two bases give different answers: an 18% ₹100 line against a 5% ₹600
    line. By extended price the split is 1:6; by tax base it is 1800:3000.
    Getting the products crossed over, or losing them, lands on neither.
    """
    from app.models.tax import Tax
    from app.services.analytics.allocation import allocate_order

    owned = _Owned()
    db = SessionLocal()
    tax_ids: list[int] = []
    try:
        standard = Tax(name=f"PF-GST18-{_uid()}", rate=Decimal("18.000"), is_active=True)
        reduced = Tax(name=f"PF-GST5-{_uid()}", rate=Decimal("5.000"), is_active=True)
        db.add_all([standard, reduced])
        db.flush()
        tax_ids = [standard.id, reduced.id]

        first = _create_product(db, owned, price="100.00", cost="40.00")
        second = _create_product(db, owned, price="300.00", cost="90.00")
        first.taxes = [standard]
        second.taxes = [reduced]
        db.flush()

        user = _create_user(db, owned)
        order = _create_order(
            db, owned, user, [(first, 1), (second, 2)],
            created_at=_at(db, DAY_PRODUCT, 12), tax="48.00",
        )
        db.commit()
        line_ids = {int(item.product_id): int(item.id) for item in order.items}

        with SessionLocal() as fresh:
            loaded = fresh.get(Order, order.id)
            allocation = allocate_order(loaded, loaded.items)

        assert not [w for w in allocation.warnings if "tax_basis_fallback" in w], (
            "every line's product resolves a tax rate, so tax must be allocated "
            f"on the rate basis; warnings were {allocation.warnings}"
        )
        shares = {line.order_item_id: line.tax for line in allocation.lines}
        # weights 100_00 x 18000 = 1.8e9 and 600_00 x 5000 = 3.0e9, i.e. 3:5 of
        # 4800 paise. An extended-price split would be 686 / 4114.
        assert shares[line_ids[first.id]] == 1800
        assert shares[line_ids[second.id]] == 3000
        assert allocation.total("tax") == 4800, (
            "the allocator's postcondition: shares sum to the order amount, in "
            "paise, whatever basis was used"
        )
    finally:
        _cleanup(owned)
        if tax_ids:
            with SessionLocal() as session:
                session.execute(
                    text("DELETE FROM product_taxes WHERE tax_id IN :ids"),
                    {"ids": tuple(tax_ids)},
                )
                session.execute(
                    text("DELETE FROM taxes WHERE id IN :ids"), {"ids": tuple(tax_ids)}
                )
                session.commit()
        db.close()


# ===========================================================================
# §5.2 — the cost-rule N+1
# ===========================================================================


def _window_days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def test_bulk_resolve_agrees_with_the_per_date_api_for_every_day() -> None:
    """`resolve_range` returns, day for day, what `resolve` returns.

    This is the whole safety case for the change: the window API is only allowed
    to exist because it cannot disagree with the per-date one. The fixture makes
    every way it *could* disagree reachable inside one window —

      * a day before any rule exists (MISSING, never zero);
      * a day covered by one global rule;
      * the changeover day, where an old rule ends and a new one begins and the
        answer must be the new one;
      * days covered by a scoped rule that beats the global one;
      * a day after the scoped rule has expired, where the global one takes over
        again.

    — and then asserts equality of every field of the component, not just the
    value, so a window resolve that picked the right rate off the wrong rule
    still fails.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        cost_type = _cost_type()
        first, last = date(2011, 3, 1), date(2011, 4, 10)

        _rule(db, owned, cost_type, value="10.00",
              effective_from=date(2011, 3, 10), effective_to=date(2011, 3, 19))
        _rule(db, owned, cost_type, value="12.50", effective_from=date(2011, 3, 20))
        _rule(db, owned, cost_type, value="9.00",
              effective_from=date(2011, 3, 25), effective_to=date(2011, 4, 1),
              scope=CostScope.GATEWAY, scope_value="razorpay")
        db.commit()

        scope = {"gateway": "razorpay"}
        days = _window_days(first, last)

        # Separate resolvers, separate caches: the two answers must come from
        # the same rules, not from one having warmed the other.
        per_date = CostRuleResolver(db, redis_client=_FakeRedis())
        expected = {
            day: per_date.resolve(cost_type, day, scope_candidates=scope)
            for day in days
        }

        bulk_client = _FakeRedis()
        bulk = CostRuleResolver(db, redis_client=bulk_client)
        actual = bulk.resolve_range([cost_type], first, last, scope_candidates=scope)

        assert set(actual) == {(cost_type, day) for day in days}, (
            "the window API must answer for every day of the inclusive range"
        )
        for day in days:
            assert actual[(cost_type, day)] == expected[day], (
                f"{day}: window resolve says {actual[(cost_type, day)]}, per-date "
                f"resolve says {expected[day]}"
            )

        # The fixture is only meaningful if the window really does contain
        # several different answers; assert that rather than trust it.
        distinct = {
            (c.rule_id, c.value_minor, c.scope) for c in expected.values()
        }
        assert len(distinct) >= 4, (
            f"fixture: the window must span at least four distinct outcomes "
            f"(missing, global v1, scoped, global v2); it spans {distinct}"
        )
        assert expected[date(2011, 3, 1)].is_missing, (
            "no rule covers the start of the window, and a missing cost must "
            "stay missing rather than become zero"
        )

        # ...and the window API leaves the same cache behind, so a later
        # per-date read hits the entries it wrote.
        warmed = CostRuleResolver(db, redis_client=bulk_client)
        for day in days:
            assert warmed.resolve(cost_type, day, scope_candidates=scope) == expected[day]
    finally:
        _cleanup(owned)
        db.close()


def test_a_cold_window_costs_one_query_per_cost_type_not_one_per_day() -> None:
    """The N+1 itself. Two windows of different lengths, same query count.

    PERFORMANCE.md §5.2 measured 99 / 328 / 568 SQL queries for a 7 / 30 / 90-day
    margin view on a cold cost-rule cache, against 28 warm, because the Redis key
    includes the date and a miss meant one SELECT per (cost type, day).

    Length-independence is the assertion, not a threshold: a resolver that is
    right for 7 days and quadratic at 90 passes any fixed ceiling chosen at 7.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        types = [_cost_type() for _ in range(3)]
        for cost_type in types:
            _rule(db, owned, cost_type, value="5.00", effective_from=date(2011, 1, 1))
        db.commit()

        counts = {}
        for span in (7, 30, 90):
            resolver = CostRuleResolver(db, redis_client=_FakeRedis())
            first = date(2011, 4, 1)
            with _count_queries() as counter:
                resolver.resolve_range([*types], first, first + timedelta(days=span - 1))
            counts[span] = counter.count

        assert counts[7] == counts[30] == counts[90] == len(types), (
            "a window must cost one read per cost type whatever its length; got "
            f"{counts} for {len(types)} cost types"
        )

        # The per-date API is the one the margin cascade actually calls, and it
        # has to get the same benefit — margin.py loops day by day and is not
        # ours to change. One resolver, 90 days, still one read per type.
        resolver = CostRuleResolver(db, redis_client=_FakeRedis())
        first = date(2011, 4, 1)
        with _count_queries() as counter:
            for cost_type in types:
                for day in _window_days(first, first + timedelta(days=89)):
                    resolver.resolve(cost_type, day)
        assert counter.count == len(types), (
            f"270 per-date resolves through one resolver issued {counter.count} "
            f"statements; the memo should make it {len(types)}.\n{counter.summary()}"
        )
    finally:
        _cleanup(owned)
        db.close()


def test_cost_rules_still_degrade_to_uncached_when_redis_is_down() -> None:
    """A dead Redis costs queries, never correctness — on both APIs.

    `cache.py` degrades to uncached reads by design, and PERFORMANCE.md §5.2
    called the old shape a "Redis-outage amplifier": with Redis down, every
    margin request paid the full per-day count. The amplifier is what changed.
    The degradation is not: a resolver whose cache raises on every call must
    still return exactly what a working one returns.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        cost_type = _cost_type()
        _rule(db, owned, cost_type, value="7.25",
              effective_from=date(2011, 2, 1), effective_to=date(2011, 4, 30))
        db.commit()

        first, last = date(2011, 1, 20), date(2011, 5, 10)
        days = _window_days(first, last)

        healthy = CostRuleResolver(db, redis_client=_FakeRedis())
        expected = {day: healthy.resolve(cost_type, day) for day in days}

        broken = CostRuleResolver(db, redis_client=_BrokenRedis())
        for day in days:
            assert broken.resolve(cost_type, day) == expected[day], (
                f"{day}: a resolver with a dead Redis returned a different answer"
            )

        broken_bulk = CostRuleResolver(db, redis_client=_BrokenRedis())
        with _count_queries() as counter:
            windowed = broken_bulk.resolve_range([cost_type], first, last)
        for day in days:
            assert windowed[(cost_type, day)] == expected[day]
        assert counter.count == 1, (
            f"with Redis down, a {len(days)}-day window must still be one read; "
            f"it was {counter.count}.\n{counter.summary()}"
        )
    finally:
        _cleanup(owned)
        db.close()


def test_invalidate_all_drops_the_in_memory_rule_memo_too() -> None:
    """The memo has no TTL, so `invalidate_all` has to reach it.

    A rule write enqueues buckets for recompute and calls `invalidate_all`. If
    that only cleared Redis, a resolver that had already read the old rules
    would keep answering from them for as long as it lived — the same staleness
    the Redis invalidation exists to close, one layer further in.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        cost_type = _cost_type()
        day = date(2011, 6, 15)
        rule = _rule(db, owned, cost_type, value="4.00", effective_from=date(2011, 1, 1))
        db.commit()

        resolver = CostRuleResolver(db, redis_client=_FakeRedis())
        assert resolver.resolve(cost_type, day).value_minor == 400

        rule.value = Decimal("6.00")
        db.commit()
        resolver.invalidate_all()

        assert resolver.resolve(cost_type, day).value_minor == 600, (
            "after invalidate_all the resolver must re-read the rule, not answer "
            "from the candidate list it loaded before the edit"
        )
    finally:
        _cleanup(owned)
        db.close()


# ===========================================================================
# §5.4 — the index
# ===========================================================================

INDEX_NAME = "ix_agg_customer_snapshot_bucket_date_tz_generation"


def test_the_tz_generation_index_exists_on_the_snapshot_table() -> None:
    """Migration e1c5b7a04d92 is applied, and the index has the right columns.

    Asserted by reflection and by *columns*, not by name: a rename is cosmetic,
    a different column order is a different index. `(bucket_date,
    tz_generation)` is a covering prefix for
    `AnalyticsRepository.distinct_tz_generations`, which runs on every read of a
    view backed by this table; the UNIQUE key is not, because `customer_key`
    sits between the two columns the query needs (PERFORMANCE.md §5.4).

    The ORM model does not declare this index — see the migration's docstring —
    so `test_analytics_schema.py::test_indexes_present_in_database` does not
    cover it. This is the test that does.
    """
    with SessionLocal() as db:
        indexes = inspect(db.get_bind()).get_indexes("agg_customer_snapshot")

    by_columns = {tuple(ix["column_names"]): ix["name"] for ix in indexes}
    assert ("bucket_date", "tz_generation") in by_columns, (
        "agg_customer_snapshot has no (bucket_date, tz_generation) index. Run "
        "`alembic upgrade head`, or apply "
        "backend/scripts/sql/2026-07-28_agg_customer_snapshot_tz_index.sql on "
        f"the shared DB. Present indexes: {sorted(by_columns)}"
    )
    assert by_columns[("bucket_date", "tz_generation")] == INDEX_NAME, (
        f"the index exists but is named "
        f"{by_columns[('bucket_date', 'tz_generation')]!r}; the migration and its "
        f"twin SQL both create {INDEX_NAME!r}, so a different name means the two "
        "artifacts have drifted"
    )


def test_the_migration_and_its_twin_sql_create_the_same_index() -> None:
    """The twin SQL is generated from the revision and must stay that way.

    The analytics schema ships as two artifacts — an Alembic revision for CI and
    local databases, and hand-applied SQL for the shared production MySQL, which
    is on a lineage this repo does not contain (DEPLOY.md §6). If they drift,
    production and CI disagree about what the schema is. `test_analytics_schema`
    makes that argument for the tables; this makes it for the one index.
    """
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    revision = (
        backend / "alembic" / "versions"
        / "e1c5b7a04d92_add_agg_customer_snapshot_bucket_tz_index.py"
    ).read_text()
    twin = (
        backend / "scripts" / "sql"
        / "2026-07-28_agg_customer_snapshot_tz_index.sql"
    ).read_text()

    assert INDEX_NAME in revision, f"{INDEX_NAME} missing from the alembic revision"
    statement = (
        f"CREATE INDEX {INDEX_NAME} ON agg_customer_snapshot "
        "(bucket_date, tz_generation);"
    )
    assert statement in twin, (
        "the twin SQL no longer contains the statement the revision generates. "
        "Regenerate it with `alembic upgrade d7f3a9c2e814:e1c5b7a04d92 --sql` "
        f"and strip the alembic_version stamp.\nExpected:\n  {statement}"
    )
    assert "alembic_version" in twin and "UPDATE alembic_version" not in twin, (
        "the twin must explain why the alembic_version stamp was removed, and "
        "must not carry it: the shared DB is on a different migration lineage"
    )
