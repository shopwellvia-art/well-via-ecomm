"""The customer rollups: the daily trend, and the per-customer snapshot.

Registered here: ``customer_daily`` (``agg_customer_daily``) and
``customer_snapshot`` (``agg_customer_snapshot``). Both follow the two write
patterns documented at length in
:mod:`app.services.analytics.aggregation.jobs`, and this module imports that
module's helpers rather than restating them — one copy of "COALESCE the
dimension, overwrite never accumulate, convert money once at the write" is the
only way those rules stay true across nine more jobs.

New vs returning is decided by ORDERS, not by signups
=====================================================
``agg_customer_daily.new_customers`` counts customers whose **first
revenue-status order** landed in this bucket: no earlier order exists before the
bucket's store-local start. It is *not* the number of accounts created that day.

``DashboardService._new_customers`` (dashboard_service.py:128) counts
``users.created_at`` in the window — signups. The two numbers are different and
the signup one is **always larger or equal**, because every customer signs up
(often days or weeks) before ordering and most accounts never order at all. Both
are legitimate metrics; they are not interchangeable, and a dashboard that puts
"new customers" from one next to revenue from the other is quietly comparing an
acquisition funnel step with a purchase. This table means purchasers, and the
definition matches ``AggOrderDaily.new_customers`` exactly so the two tables
agree row for row.

The consequence worth stating: this definition is *retroactively stable*.
Whether a customer was new on 3 March depends only on orders placed before 3
March, so recomputing that bucket in July gives the same answer — unless an
older order was cancelled or refunded out of the revenue statuses, which is
precisely the kind of late change the recompute queue exists to propagate.

The snapshot table is customers x days, and that is dangerous
=============================================================
``agg_customer_snapshot`` has one row per customer per snapshot date. At 50k
customers that is 50k rows a day and ~18M rows a year for a table whose main use
is "current state", so the retention policy in the model docstring is not
advisory — see :data:`RETENTION_DAILY_DAYS` and ``_RetentionPolicy`` below. This
job implements it in both directions: it refuses to write daily rows for a
bucket that has already aged out, and it prunes rows that have aged out since
they were written.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import case, delete, func, insert, select
from sqlalchemy.orm import Session

from app.models.analytics_rollups import AggCustomerDaily, AggCustomerSnapshot
from app.models.order import Order, OrderItem
from app.models.return_request import ReturnRequest
from app.services.analytics.aggregation.base import JobRunResult, register

# The write patterns and their guard rails live in `jobs`, imported rather than
# re-implemented. `_upsert` is what makes "overwrite, never accumulate" a
# property of one function instead of a rule twelve jobs have to remember.
from app.services.analytics.aggregation.jobs import (
    _bucket_window,
    _cap,
    _dim,
    _revenue_window,
    _upsert,
    _utcnow,
)
from app.services.analytics.contracts import from_minor, to_minor
from app.services.analytics.timebox import local_day
from app.services.analytics.types import MetricQuality
from app.services.dashboard_service import _REVENUE_STATUSES

__all__ = [
    "CustomerDailyJob",
    "CustomerSnapshotJob",
    "RETENTION_DAILY_DAYS",
    "RFM_WINDOW_DAYS",
    "ACTIVE_WINDOW_DAYS",
    "CHURN_MEDIUM_DAYS",
    "SNAPSHOT_INSERT_CHUNK",
    "RFM_QUINTILES",
]

#: Daily snapshot rows are kept this long; beyond it only month-end rows
#: survive. Straight from the `AggCustomerSnapshot` docstring, which is the
#: authority — this constant exists so the number appears once in code.
RETENTION_DAILY_DAYS = 90

#: Window for the F and M of RFM. Lifetime frequency would score a customer who
#: bought weekly in 2019 and never returned as our most frequent, which is the
#: opposite of what the segment is for. One year is the convention here and it
#: is stated on every column that depends on it.
RFM_WINDOW_DAYS = 365

#: A customer who has ordered within this many days of the snapshot date is
#: `is_active`. Also the low/medium boundary of `churn_risk_band`.
ACTIVE_WINDOW_DAYS = 90

#: Upper boundary of the "medium" churn band. Beyond it, "high".
CHURN_MEDIUM_DAYS = 180

#: R, F and M are scored into this many bands. Five is what "quintile" means;
#: named so the scoring code reads as a rule rather than a magic 5.
RFM_QUINTILES = 5

#: Snapshot rows are inserted in batches of this size. One INSERT carrying 50k
#: rows exceeds `max_allowed_packet` on a default MySQL 8 and fails the whole
#: bucket; batching makes the statement size a function of this constant rather
#: than of the customer base.
SNAPSHOT_INSERT_CHUNK = 500

#: RFM segment matrix, evaluated top to bottom, first match wins. This is a
#: documented CONVENTION, not a derived truth: the measurements are `r_score`,
#: `f_score` and `m_score`, which are stored next to it so anyone who disagrees
#: with the naming can re-segment without recomputing anything. Order matters —
#: `cant_lose` must be tested before `at_risk` or a lapsed big spender would be
#: filed under the milder label.
_SEGMENT_RULES: tuple[tuple[str, Any], ...] = (
    ("champions", lambda r, f, m: r >= 4 and f >= 4),
    ("cant_lose", lambda r, f, m: r <= 2 and f >= 4 and m >= 4),
    ("loyal", lambda r, f, m: r >= 3 and f >= 3),
    ("at_risk", lambda r, f, m: r == 2 and f >= 3),
    ("new_customers", lambda r, f, m: r >= 4 and f <= 2),
    ("promising", lambda r, f, m: r == 3 and f <= 2),
    ("hibernating", lambda r, f, m: r == 2 and f <= 2),
    ("lost", lambda r, f, m: r <= 1),
)

#: Only reachable if `_SEGMENT_RULES` stops covering the 5x5 grid.
_SEGMENT_FALLBACK = "needs_attention"


def _customer_key(user_id: int) -> str:
    """The stable analytics identity for a customer.

    ``orders.user_id`` is NOT NULL with an FK to ``users``, so every order in
    this deployment belongs to an account and the key is always derivable from
    the user id. The column is a general 64-char dimension rather than an
    integer precisely so that stays an implementation detail: a guest-checkout
    key (``g:<email hash>``) or a post-merge alias can be introduced later
    without a migration and without changing this table's grain. The ``u:``
    prefix is what makes those two namespaces impossible to collide.
    """
    return _dim(f"u:{int(user_id)}")


def _local_today(tz: ZoneInfo) -> date:
    """The store-local calendar day this run is happening on."""
    return local_day(datetime.now(timezone.utc), tz)


def _is_month_end(day: date) -> bool:
    """True if `day` is the last day of its month."""
    return (day + timedelta(days=1)).month != day.month


def _quintile(sorted_values: Sequence[int], value: int) -> int:
    """Score `value` 1..5 against the population it came from.

    Cumulative-share quintiles: a value's score is driven by how much of the
    population sits at or below it, so the largest value always scores 5 and
    **equal values always score equally** — without that, two customers with
    identical spend could land in different segments purely by row order, and
    the segmentation would drift every run for no reason anyone could see.

    An empty population scores 0, which is the server default and reads as "not
    scored" rather than as the bottom quintile.
    """
    population = len(sorted_values)
    if population == 0:  # pragma: no cover - defensive
        return 0
    # bisect_right without importing bisect for one call site would be cheaper,
    # but the population lists here are already materialised and sorted.
    from bisect import bisect_right

    at_or_below = bisect_right(sorted_values, value)
    score = -(-at_or_below * RFM_QUINTILES // population)  # ceil division
    return max(1, min(RFM_QUINTILES, score))


def _segment(r_score: int, f_score: int, m_score: int) -> str:
    for label, predicate in _SEGMENT_RULES:
        if predicate(r_score, f_score, m_score):
            return label
    return _SEGMENT_FALLBACK  # pragma: no cover - the matrix is exhaustive


def _churn_band(recency_days: int) -> str:
    """Band a customer's lapse, never a probability.

    Thresholds are :data:`ACTIVE_WINDOW_DAYS` and :data:`CHURN_MEDIUM_DAYS` —
    two stated constants, so the band is a rule anyone can re-derive. A stored
    churn *percentage* would be a model output wearing a measurement's clothes,
    which is why the column is banded (see the model docstring).
    """
    if recency_days <= ACTIVE_WINDOW_DAYS:
        return "low"
    if recency_days <= CHURN_MEDIUM_DAYS:
        return "medium"
    return "high"


# ===========================================================================
# customer_daily -> agg_customer_daily     (Pattern A: one row per bucket)
# ===========================================================================
class CustomerDailyJob:
    """New vs returning purchasers per store-local day, with their revenue.

    One row per ``(bucket_date, tz_generation)``: the key set is a single known
    key, so Pattern A's upsert-overwrite is complete.

    Every counter here is taken over **revenue-status orders only** (PAID /
    SHIPPED / DELIVERED, imported from ``dashboard_service``). Counting someone
    whose only order was cancelled as an acquired customer would inflate
    acquisition and put a customer next to revenue that never arrived.

    ``new_customers + returning_customers == active_customers`` holds by
    construction and is asserted below: "new" means no *earlier* revenue-status
    order exists, which is a property of the customer and the bucket start, so
    the two counts partition the day's distinct purchasers exactly. The same is
    true of ``orders_new + orders_returning == the day's revenue orders`` and of
    the two revenue columns.

    **This table does not net off refunds.** ``revenue_new`` and
    ``revenue_returning`` sum ``total_amount`` over the day's revenue-status
    orders, so together they equal ``agg_order_daily.paid_order_value`` and not
    its ``net_revenue``. A refund is dated by when it was *issued*; splitting a
    July refund back across the new/returning status its customer held in March
    would be an attribution nobody could reproduce. The daily order table
    remains the reconciled revenue record.

    ``active_customers`` is a distinct count and is **not additive across
    days** — summing a week overcounts anyone who ordered twice. That is stated
    on the model column too, and it is why the weekly figure has to come from
    ``agg_customer_snapshot`` or a recompute rather than a SUM.
    """

    name = "customer_daily"

    def run(self, db: Session, bucket_date: date, tz_generation: int) -> JobRunResult:
        start, end, _tz = _bucket_window(db, bucket_date)
        warnings: list[str] = []

        # Per-order, not pre-aggregated: the split is by customer status, so the
        # orders have to be attributed to a customer before they can be counted.
        placed = db.execute(
            select(Order.user_id, Order.total_amount).where(*_revenue_window(start, end))
        ).all()

        customers = {int(row.user_id) for row in placed}
        prior = self._customers_with_earlier_orders(db, customers, start)

        new_customers = customers - prior
        orders = {"new": 0, "returning": 0}
        revenue_minor = {"new": 0, "returning": 0}
        for row in placed:
            side = "new" if int(row.user_id) in new_customers else "returning"
            orders[side] += 1
            revenue_minor[side] += to_minor(row.total_amount)

        if len(new_customers) > len(customers):  # pragma: no cover - defensive
            warnings.append(
                f"customer_partition: new_customers ({len(new_customers)}) exceeds "
                f"active_customers ({len(customers)}); the two counts no longer "
                "partition the day"
            )

        row = {
            "bucket_date": bucket_date,
            "tz_generation": tz_generation,
            "computed_at": _utcnow(),
            "new_customers": len(new_customers),
            "returning_customers": len(customers) - len(new_customers),
            "active_customers": len(customers),
            "orders_new": orders["new"],
            "orders_returning": orders["returning"],
            "revenue_new": from_minor(revenue_minor["new"]),
            "revenue_returning": from_minor(revenue_minor["returning"]),
        }
        written = _upsert(
            db,
            AggCustomerDaily,
            [row],
            key_columns=("bucket_date", "tz_generation"),
        )
        return JobRunResult(rows_written=written, warnings=_cap(warnings))

    @staticmethod
    def _customers_with_earlier_orders(
        db: Session, customers: set[int], start: datetime
    ) -> set[int]:
        """Of `customers`, those who had a revenue-status order before `start`.

        Restricted to the day's customers rather than scanning every purchaser
        ever: the answer is only needed for people who ordered today, and the
        ``IN`` list is bounded by a single day's distinct buyers.
        """
        if not customers:
            return set()
        rows = db.execute(
            select(func.distinct(Order.user_id)).where(
                Order.user_id.in_(customers),
                Order.status.in_(_REVENUE_STATUSES),
                Order.created_at < start,
            )
        ).scalars().all()
        return {int(user_id) for user_id in rows}


# ===========================================================================
# customer_snapshot -> agg_customer_snapshot   (Pattern B: delete + reinsert)
# ===========================================================================
@dataclass
class _CustomerState:
    """Everything measured about one customer as of one snapshot date."""

    user_id: int
    first_order_at: datetime | None = None
    last_order_at: datetime | None = None
    orders_count: int = 0
    units: int = 0
    costed_units: int = 0
    gross_ltv_minor: int = 0
    discounts_minor: int = 0
    gms_minor: int = 0
    cogs_minor: int = 0
    refunds_minor: int = 0
    frequency: int = 0
    monetary_minor: int = 0
    preferred_payment_method: str = "-"


class CustomerSnapshotJob:
    """Per-customer lifetime state at the close of one day: LTV, RFM, churn.

    Pattern B. The group key is the customer population *as of the snapshot
    date*, which is discovered rather than declared: a customer whose only order
    is refunded out of the revenue statuses must **leave** the snapshot, and an
    upsert has no statement to make about a key that disappeared — it would
    leave that customer in the "champions" segment forever. DELETE by
    ``(bucket_date, tz_generation)`` then INSERT, inside the runner's single
    transaction.

    Grain and dating
    ----------------
    ``bucket_date`` is the **snapshot date**, not "activity on this day" — the
    one place in the rollup module where the mixin column means something else,
    as ``AggCustomerSnapshot`` documents. Everything is measured as of the
    store-local close of that day: an order at 23:00 IST on the bucket date is
    inside the snapshot, one at 00:30 IST the next morning is not, and both of
    those are the same UTC date. All boundaries come from ``timebox``.

    What each money column means
    ----------------------------
    * ``gross_ltv`` — SUM(``total_amount``) over the customer's revenue-status
      orders. Gross of refunds, and the numerator of ``aov``.
    * ``net_ltv`` — ``gross_ltv`` minus refunds *issued* on or before the
      snapshot date, de-duplicated across the two refund sources exactly as
      ``jobs._refunds_minor`` does. This is the same net-revenue definition
      ``agg_order_daily`` uses, restricted to one customer.
    * ``margin_ltv`` — **CM1**: net merchandise sales (line value minus both
      order-level discounts) minus COGS. It is deliberately NOT
      ``net_ltv - cogs``: ``net_ltv`` carries tax, shipping and the COD
      surcharge, none of which are margin. It also stops at CM1 — gateway fees
      and shipping cost need the cost-rule engine, which resolves per day and
      per order rather than per customer.
    * ``aov`` — ``gross_ltv / orders_count``, materialised only because it is
      the segmentation sort key. It is DERIVED: never SUM it and never AVG it
      across rows. A cross-customer AOV is ``SUM(gross_ltv) / SUM(orders_count)``.

    ``cogs`` sums only lines that carry a non-null ``unit_cost``; ``units`` and
    ``costed_units`` are both stored so the coverage is visible, and ``quality``
    is INCOMPLETE for any customer with an uncosted line. It is emphatically not
    ``COALESCE(unit_cost, 0)``, which reports 100% margin in the flattering
    direction, silently.

    RFM
    ---
    ``recency_days`` is store-local days between the customer's last order and
    the snapshot date. ``frequency`` and ``monetary`` are over the trailing
    :data:`RFM_WINDOW_DAYS`, **not lifetime** — a customer who bought weekly
    five years ago and never came back is not our most frequent buyer, and
    lifetime counts would say he is.

    The three scores are quintiles **over the population on this snapshot
    date**, which is why they are stored: they cannot be recomputed from a
    single row. All three are oriented the same way — 5 is the best band — which
    for R means fewest days since the last order.

    Retention
    ---------
    See :data:`RETENTION_DAILY_DAYS`. Two halves, because a policy that only
    covered one of them would still let the table grow without bound:

      1. A bucket that has already aged past the daily window and is not a
         month-end is **not written at all** — the job deletes anything already
         there for that bucket and returns having written nothing. A backfill of
         two years therefore produces month-end rows for the old part and daily
         rows for the recent part, which is exactly the policy.
      2. Rows that were daily-fresh when written and have since aged out are
         pruned on every run.

    The prune is the one place this job touches buckets other than its own, and
    that is deliberate: no separate retention job exists, the rows it removes
    are ones the policy says must not exist, and the table is derived — any of
    them can be recomputed from the transactional tables. It is reported in
    ``rows_deleted`` and, when it removes anything, as a warning, so it can
    never quietly delete a table nobody meant to prune.

    Why this is still a full recompute, and not incremental
    ------------------------------------------------------
    Every bucket is rebuilt from the transactional tables, so day D never reads
    day D-1. That is the expensive shape and it is kept on purpose.

    ``docs/analytics/PERFORMANCE.md`` §4.2 measured this job at 21.3 s per
    bucket with **only 23% of it in SQL**. Chaining buckets off each other
    attacks the 23% and leaves the 77%; the two changes below attack the 77%
    and leave the semantics alone. Re-measured on a 20 000-order / 6 000-customer
    build of the generator in that document's §9, they took one bucket from
    **5.06 s to 1.87 s** — and 1.13 s of what remains is SQL, so an incremental
    path had at most ~0.7 s of Python left to win.

    It would also have to be *wrong* somewhere, and the wrongness is not
    detectable from the output:

      * A March order cancelled or refunded out of the revenue statuses in July
        changes that customer's lifetime state for **every** bucket from March
        on. A full recompute of any one of those buckets gets it right; a chain
        anchored on a snapshot written before the change silently carries the
        old total until the whole chain is rebuilt.
      * ``frequency`` and ``monetary`` are over a trailing window, so advancing
        one day means subtracting the orders that fell *out* of it as well as
        adding the day's own — two bounded queries, but only correct if the
        chain has never skipped a day. The retention policy guarantees it does:
        beyond 90 days only month-ends survive, so the predecessor a backfill
        would chain from is usually not there.
      * ``preferred_payment_method`` is the modal method over lifetime orders.
        The snapshot stores the winner, not the per-method counts, so yesterday's
        row does not contain enough information to score today's — the input the
        increment would need was thrown away when the row was written.

    The honest summary: incremental is a correctness project (a chain that knows
    when it is invalid), not a performance patch, and the performance it would
    buy on top of what is below is a fraction of what it risks.
    """

    name = "customer_snapshot"

    def run(self, db: Session, bucket_date: date, tz_generation: int) -> JobRunResult:
        start, end, tz = _bucket_window(db, bucket_date)
        warnings: list[str] = []

        deleted = self._delete_bucket(db, bucket_date, tz_generation)
        pruned = self._prune_expired(db, bucket_date, tz_generation, tz, warnings)

        if not self._is_retained(bucket_date, tz):
            # Aged-out and not a month-end: the policy says this bucket holds no
            # daily rows, so the DELETE above is the entire correct output. The
            # warning keeps "retention declined to write" distinguishable from
            # "there were no customers", which look identical in the table.
            warnings.append(
                f"snapshot_retention_skipped: {bucket_date} is older than "
                f"{RETENTION_DAILY_DAYS} days and is not a month-end, so no daily "
                "snapshot rows were written (see AggCustomerSnapshot's retention "
                "policy); month-end buckets in the same period are still built"
            )
            return JobRunResult(
                rows_written=0,
                rows_deleted=deleted + pruned,
                warnings=_cap(warnings),
            )

        states = self._collect(db, end)
        rows = self._rows(states, bucket_date, tz_generation, tz, warnings)
        self._insert(db, rows)

        return JobRunResult(
            rows_written=len(rows),
            rows_deleted=deleted + pruned,
            warnings=_cap(warnings),
        )

    # -- retention --------------------------------------------------------
    @staticmethod
    def _is_retained(bucket_date: date, tz: ZoneInfo) -> bool:
        """Does the policy keep DAILY rows for this bucket?

        Age is measured against the store-local today, not against the bucket
        being computed, so the answer does not depend on which order the buckets
        of a backfill happen to run in.
        """
        if _is_month_end(bucket_date):
            return True
        return (_local_today(tz) - bucket_date).days <= RETENTION_DAILY_DAYS

    @staticmethod
    def _delete_bucket(db: Session, bucket_date: date, tz_generation: int) -> int:
        """Pattern B's DELETE. Unconditional, including when nothing follows it."""
        return int(
            db.execute(
                delete(AggCustomerSnapshot).where(
                    AggCustomerSnapshot.bucket_date == bucket_date,
                    AggCustomerSnapshot.tz_generation == tz_generation,
                )
            ).rowcount
            or 0
        )

    @staticmethod
    def _insert(db: Session, rows: Sequence[dict[str, Any]]) -> None:
        """Pattern B's INSERT: parameter sets, not one literal VALUES list.

        The rows go in as ``executemany`` — ``db.execute(insert(Model), rows)``
        — rather than as ``insert(Model).values(rows)``. The two produce the
        same INSERTs; they cost wildly different amounts of Python to produce.

        ``.values([...])`` builds one statement carrying every row as inline
        bind parameters, so the compiler walks 500 rows x 28 columns = 14 000
        parameters *per chunk* and cannot reuse the compiled form for the next
        chunk, because the next chunk is a different statement.

        Profiled, that was **the single largest cost in the whole job**:
        ``.values()`` plus compilation accounted for 7.0 s of a 12.1 s cProfile
        run of one bucket, against 3.0 s spent in the driver for all 23 of the
        bucket's statements put together. Nothing there is database work, which
        is why ``docs/analytics/PERFORMANCE.md`` §4.2 could measure 21.3 s per
        bucket with only 23% of it in SQL. Passing the rows as parameter sets
        compiles once and hands the batching to the driver.

        :data:`SNAPSHOT_INSERT_CHUNK` still bounds the statement size for the
        same reason it always did — one INSERT carrying 50k customers exceeds
        ``max_allowed_packet`` — but it is now a bound on the driver's batch
        rather than on a compiler walk, so the constant is cheap either way.
        """
        if not rows:
            return
        statement = insert(AggCustomerSnapshot)
        for index in range(0, len(rows), SNAPSHOT_INSERT_CHUNK):
            db.execute(statement, list(rows[index : index + SNAPSHOT_INSERT_CHUNK]))

    @staticmethod
    def _prune_expired(
        db: Session,
        bucket_date: date,
        tz_generation: int,
        tz: ZoneInfo,
        warnings: list[str],
    ) -> int:
        """Delete daily rows that have aged past the retention window.

        ``LAST_DAY()`` is MySQL's month-end function and expresses "keep only
        month-end rows beyond the window" as one predicate the database can
        evaluate, rather than as a date list assembled in Python. The bucket
        currently being rebuilt is excluded so this can never race with the
        INSERT that follows it.
        """
        cutoff = _local_today(tz) - timedelta(days=RETENTION_DAILY_DAYS)
        removed = int(
            db.execute(
                delete(AggCustomerSnapshot).where(
                    AggCustomerSnapshot.tz_generation == tz_generation,
                    AggCustomerSnapshot.bucket_date < cutoff,
                    AggCustomerSnapshot.bucket_date != bucket_date,
                    AggCustomerSnapshot.bucket_date
                    != func.last_day(AggCustomerSnapshot.bucket_date),
                )
            ).rowcount
            or 0
        )
        if removed:
            warnings.append(
                f"snapshot_retention_pruned: removed {removed} daily snapshot "
                f"row(s) older than {cutoff} that are not month-end rows, per "
                "AggCustomerSnapshot's retention policy"
            )
        return removed

    # -- measurement ------------------------------------------------------
    def _collect(self, db: Session, end: datetime) -> dict[int, _CustomerState]:
        """Every customer with at least one revenue-status order before `end`.

        Separate aggregate queries rather than one join: joining lifetime
        orders, their lines and their refunds in a single statement multiplies
        rows against each other and silently doubles the sums. Separate GROUP
        BYs merged in Python cannot do that.

        What *can* safely share a statement is anything reading the same rows at
        the same grain, and three of the six queries this used to issue did
        exactly that: lifetime totals, the trailing-:data:`RFM_WINDOW_DAYS`
        totals, and the modal payment method were three full-history scans of
        ``orders`` under one identical predicate. They are now one scan grouped
        by ``(user_id, payment_method)`` — the finest grain any of the three
        needs — with the RFM window expressed as a conditional aggregate and the
        per-user totals folded up in Python. MIN, MAX, COUNT and SUM all
        decompose over a partition of the rows, so folding the sub-groups is not
        an approximation of the old answer, it is the same arithmetic in a
        different order. ``docs/analytics/PERFORMANCE.md`` §4.6 measured those
        three scans at 43.3 s of the six-scan set's 110.6 s across 33 runs.

        Four queries remain, and two of them are still full-history: that is the
        job's semantics ("lifetime state as of the close of day D") and not an
        oversight — see the module notes on why this was not made incremental.
        """
        states: dict[int, _CustomerState] = {}

        # The RFM window boundary, needed inside the scan below rather than
        # after it: F and M are over the trailing year, never lifetime.
        window_start = end - timedelta(days=RFM_WINDOW_DAYS)

        lifetime = db.execute(
            select(
                Order.user_id.label("user_id"),
                Order.payment_method.label("method"),
                func.min(Order.created_at).label("first_at"),
                func.max(Order.created_at).label("last_at"),
                func.count(Order.id).label("orders"),
                func.coalesce(func.sum(Order.total_amount), 0).label("gross"),
                func.coalesce(
                    func.sum(Order.discount_amount + Order.payment_discount_amount), 0
                ).label("discounts"),
                # The trailing-window halves of the same rows. CASE rather than
                # a second scan: the window is a subset of this predicate, so
                # the row is already in hand when the question is asked.
                func.coalesce(
                    func.sum(
                        case((Order.created_at >= window_start, 1), else_=0)
                    ),
                    0,
                ).label("recent_orders"),
                func.coalesce(
                    func.sum(
                        case(
                            (Order.created_at >= window_start, Order.total_amount),
                            else_=0,
                        )
                    ),
                    0,
                ).label("recent_spend"),
            )
            .where(Order.status.in_(_REVENUE_STATUSES), Order.created_at < end)
            .group_by(Order.user_id, Order.payment_method)
        ).all()

        # Money is folded as Decimal and converted once at the end, so the
        # result is `to_minor(SUM(everything))` exactly as before rather than a
        # sum of independently-converted sub-totals.
        gross: dict[int, Decimal] = defaultdict(Decimal)
        discounts: dict[int, Decimal] = defaultdict(Decimal)
        spend: dict[int, Decimal] = defaultdict(Decimal)
        #: user -> (orders on this method, that method's last order, method).
        #: Ties break on the most recent order and then on the method name, so
        #: the answer is deterministic — an arbitrary tie-break would make the
        #: column flap between runs and take `rfm_segment`-adjacent dashboards
        #: with it.
        preferred: dict[int, tuple[int, datetime, str]] = {}

        for row in lifetime:
            user_id = int(row.user_id)
            state = states.get(user_id)
            if state is None:
                state = states[user_id] = _CustomerState(user_id=user_id)

            orders = int(row.orders or 0)
            state.orders_count += orders
            state.frequency += int(row.recent_orders or 0)
            gross[user_id] += Decimal(row.gross or 0)
            discounts[user_id] += Decimal(row.discounts or 0)
            spend[user_id] += Decimal(row.recent_spend or 0)

            if state.first_order_at is None or (
                row.first_at is not None and row.first_at < state.first_order_at
            ):
                state.first_order_at = row.first_at
            if state.last_order_at is None or (
                row.last_at is not None and row.last_at > state.last_order_at
            ):
                state.last_order_at = row.last_at

            candidate = (orders, row.last_at or datetime.min, _dim(row.method)[:20])
            if user_id not in preferred or candidate > preferred[user_id]:
                preferred[user_id] = candidate

        for user_id, state in states.items():
            state.gross_ltv_minor = to_minor(gross[user_id])
            state.discounts_minor = to_minor(discounts[user_id])
            state.monetary_minor = to_minor(spend[user_id])
            state.preferred_payment_method = preferred[user_id][2]

        if not states:
            return states

        lines = db.execute(
            select(
                Order.user_id.label("user_id"),
                func.coalesce(func.sum(OrderItem.quantity), 0).label("units"),
                func.coalesce(
                    func.sum(OrderItem.quantity * OrderItem.unit_price), 0
                ).label("gms"),
                func.coalesce(
                    func.sum(
                        case(
                            (OrderItem.unit_cost.isnot(None), OrderItem.quantity),
                            else_=0,
                        )
                    ),
                    0,
                ).label("costed_units"),
                # SQL drops NULLs from SUM, so an uncosted line contributes
                # nothing here while still counting in `units`.
                func.coalesce(
                    func.sum(OrderItem.quantity * OrderItem.unit_cost), 0
                ).label("cogs"),
            )
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .where(Order.status.in_(_REVENUE_STATUSES), Order.created_at < end)
            .group_by(Order.user_id)
        ).all()
        for row in lines:
            state = states.get(int(row.user_id))
            if state is None:  # pragma: no cover - same predicate as `lifetime`
                continue
            state.units = int(row.units or 0)
            state.costed_units = int(row.costed_units or 0)
            state.gms_minor = to_minor(row.gms)
            state.cogs_minor = to_minor(row.cogs)

        for user_id, refund_minor in self._refunds_by_customer(db, end).items():
            state = states.get(user_id)
            if state is not None:
                state.refunds_minor = refund_minor

        return states

    @staticmethod
    def _refunds_by_customer(db: Session, end: datetime) -> dict[int, int]:
        """Refunds *issued* before `end`, per customer, in paise.

        Mirrors ``jobs._refunds_minor`` term for term with a per-customer GROUP
        BY: a returns-driven refund carries an explicit ``returns.refund_amount``
        while a whole-order refund has no amount column and is valued at
        ``orders.total_amount`` — but only when the order has no refunded return
        row, or the same money is subtracted twice.
        """
        totals: dict[int, int] = defaultdict(int)

        from_returns = db.execute(
            select(
                ReturnRequest.user_id.label("user_id"),
                func.coalesce(func.sum(ReturnRequest.refund_amount), 0).label("amount"),
            )
            .where(
                ReturnRequest.refunded_at.isnot(None),
                ReturnRequest.refunded_at < end,
            )
            .group_by(ReturnRequest.user_id)
        ).all()
        for row in from_returns:
            totals[int(row.user_id)] += to_minor(row.amount)

        has_refunded_return = (
            select(ReturnRequest.id)
            .where(
                ReturnRequest.order_id == Order.id,
                ReturnRequest.refunded_at.isnot(None),
            )
            .correlate(Order)
            .exists()
        )
        whole_order = db.execute(
            select(
                Order.user_id.label("user_id"),
                func.coalesce(func.sum(Order.total_amount), 0).label("amount"),
            )
            .where(
                Order.refunded_at.isnot(None),
                Order.refunded_at < end,
                ~has_refunded_return,
            )
            .group_by(Order.user_id)
        ).all()
        for row in whole_order:
            totals[int(row.user_id)] += to_minor(row.amount)

        return dict(totals)

    # -- row building -----------------------------------------------------
    def _rows(
        self,
        states: dict[int, _CustomerState],
        bucket_date: date,
        tz_generation: int,
        tz: ZoneInfo,
        warnings: list[str],
    ) -> list[dict[str, Any]]:
        if not states:
            return []

        ordered = [states[user_id] for user_id in sorted(states)]
        recency = {
            state.user_id: self._recency_days(state, bucket_date, tz, warnings)
            for state in ordered
        }

        # Quintile bases: sorted once, shared by every customer. Recency is
        # NEGATED before scoring rather than scored and then subtracted from 6,
        # so that all three scores mean the same thing — the best value in the
        # population takes the top band. Inverting after the fact instead gives
        # the only customer in a one-customer population r_score 1, i.e. "lost"
        # for somebody who ordered this morning.
        recency_sorted = sorted(-days for days in recency.values())
        frequency_sorted = sorted(state.frequency for state in ordered)
        monetary_sorted = sorted(state.monetary_minor for state in ordered)

        computed_at = _utcnow()
        rows: list[dict[str, Any]] = []
        uncosted = 0
        for state in ordered:
            recency_days = recency[state.user_id]
            r_score = _quintile(recency_sorted, -recency_days)
            f_score = _quintile(frequency_sorted, state.frequency)
            m_score = _quintile(monetary_sorted, state.monetary_minor)

            complete = state.units > 0 and state.costed_units == state.units
            if not complete:
                uncosted += 1

            net_ltv_minor = state.gross_ltv_minor - state.refunds_minor
            margin_ltv_minor = (
                state.gms_minor - state.discounts_minor - state.cogs_minor
            )
            # Integer paise throughout, so the division truncates at the paisa
            # rather than introducing a float. A customer with no orders cannot
            # be in this population, but 0 is the honest answer if one ever is.
            aov_minor = (
                state.gross_ltv_minor // state.orders_count
                if state.orders_count
                else 0
            )

            rows.append(
                {
                    "bucket_date": bucket_date,
                    "customer_key": _customer_key(state.user_id),
                    "tz_generation": tz_generation,
                    "computed_at": computed_at,
                    "user_id": state.user_id,
                    "first_order_at": state.first_order_at,
                    "last_order_at": state.last_order_at,
                    "orders_count": state.orders_count,
                    "units": state.units,
                    "gross_ltv": from_minor(state.gross_ltv_minor),
                    "net_ltv": from_minor(net_ltv_minor),
                    "margin_ltv": from_minor(margin_ltv_minor),
                    "aov": from_minor(aov_minor),
                    "recency_days": recency_days,
                    "frequency": state.frequency,
                    "monetary": from_minor(state.monetary_minor),
                    "r_score": r_score,
                    "f_score": f_score,
                    "m_score": m_score,
                    "rfm_segment": _dim(_segment(r_score, f_score, m_score))[:32],
                    "cohort_month": self._cohort_month(state, tz),
                    "tenure_days": self._tenure_days(state, bucket_date, tz),
                    "is_active": recency_days <= ACTIVE_WINDOW_DAYS,
                    "churn_risk_band": _churn_band(recency_days),
                    "preferred_payment_method": state.preferred_payment_method,
                    "quality": (
                        MetricQuality.AUTHORITATIVE.value
                        if complete
                        else MetricQuality.INCOMPLETE.value
                    ),
                }
            )

        if uncosted:
            warnings.append(
                f"snapshot_cost_coverage: {uncosted} of {len(ordered)} customer(s) "
                "have order lines with no unit_cost snapshot; their margin_ltv is "
                f"measured over the costed lines only and is labelled "
                f"{MetricQuality.INCOMPLETE.value}"
            )
        return rows

    @staticmethod
    def _recency_days(
        state: _CustomerState, bucket_date: date, tz: ZoneInfo, warnings: list[str]
    ) -> int:
        """Store-local days from the customer's last order to the snapshot date."""
        if state.last_order_at is None:  # pragma: no cover - population has orders
            return 0
        days = (bucket_date - local_day(state.last_order_at, tz)).days
        if days < 0:  # pragma: no cover - the window excludes later orders
            warnings.append(
                f"snapshot_future_order: customer {state.user_id} has a last order "
                f"after snapshot date {bucket_date}; recency clamped to 0"
            )
            return 0
        return days

    @staticmethod
    def _cohort_month(state: _CustomerState, tz: ZoneInfo) -> str:
        """'YYYY-MM' of the first order, in store-local time."""
        if state.first_order_at is None:  # pragma: no cover - defensive
            return _dim(None)
        return local_day(state.first_order_at, tz).strftime("%Y-%m")

    @staticmethod
    def _tenure_days(state: _CustomerState, bucket_date: date, tz: ZoneInfo) -> int:
        if state.first_order_at is None:  # pragma: no cover - defensive
            return 0
        return max(0, (bucket_date - local_day(state.first_order_at, tz)).days)


register(CustomerDailyJob())
register(CustomerSnapshotJob())
