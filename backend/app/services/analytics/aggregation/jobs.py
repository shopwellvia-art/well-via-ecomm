"""The aggregation jobs. Three of twelve, and the pattern the other nine follow.

Registered here: ``order_daily`` (``agg_order_daily``), ``order_hourly``
(``agg_order_hourly``) and ``product_daily`` (``agg_product_daily``). The
remaining nine rollups — customer daily/snapshot/cohort, payment, shipment, geo,
promo, funnel, inventory — are a later phase and are deliberately **not stubbed**
here: an empty job registered under a real name would report SUCCESS, advance a
watermark, and make an unbuilt table indistinguishable from a quiet one. Absent
is honest; a stub is not.

The two write patterns, and how to choose
=========================================
Every job here is idempotent, because the recompute queue's lease can expire and
re-hand the same bucket to a second worker, and because the runner retries. There
are exactly two ways to get that, and picking the wrong one is the classic
double-count bug.

**Pattern A — single row per bucket (upsert-overwrite).**
Used by ``agg_order_daily`` and ``agg_order_hourly``. The set of keys a bucket
produces is *fixed and known in advance*: one row per day, and 24 rows per day
respectively. Nothing can disappear between runs, so::

    INSERT ... ON DUPLICATE KEY UPDATE col = VALUES(col)

is complete. Note ``= VALUES(col)`` — **overwrite, never ``col = col +
VALUES(col)``**. Accumulating turns a second run into a doubled day, and MySQL
reports nothing wrong because the statement succeeded exactly as written.

``agg_order_hourly`` qualifies for Pattern A *only because this job writes all 24
hours every run*, zero-filling the quiet ones. A job that wrote only the hours
with trade would have a key set that shrinks when an order is deleted, which is
Pattern B's problem — and an upsert would leave the vacated hour behind at its
old value forever. A zero-filled hour is a real measurement ("nothing sold
between 03:00 and 04:00"), not a fabricated one.

**Pattern B — dimensioned bucket (delete-and-reinsert).**
Used by ``agg_product_daily``, and by every future dimensioned rollup (payment,
shipment, geo, promo, ...). The group key is data-dependent: which products sold
on a day is discovered, not declared. Upsert alone cannot express a key that
*disappears* — cancel the only order containing SKU-A and re-run, and the upsert
writes nothing for SKU-A, so yesterday's row survives untouched and the
leaderboard keeps showing a product that sold nothing. So::

    DELETE FROM agg_product_daily WHERE bucket_date = :d AND tz_generation = :g
    INSERT ...   -- the freshly computed rows

both inside **one transaction**, which the runner owns. Delete-then-insert is
also what makes the rollups "derived and therefore disposable" true in practice.

Every dimension value that participates in a UNIQUE key is COALESCEd to ``'-'``
before it is written. MySQL permits unlimited NULLs under a UNIQUE index, so one
NULL ``sku_snapshot`` would silently escape the idempotency key and the next run
would insert a second row for the same product instead of replacing it.

Definitions these jobs do not get to invent
===========================================
* **Revenue statuses** come from ``dashboard_service._REVENUE_STATUSES``
  (PAID / SHIPPED / DELIVERED), imported and never restated. Pending has not
  paid; cancelled and refunded gave the money back.
* **Day and hour boundaries** come from :mod:`app.services.analytics.timebox`.
  No date arithmetic on UTC timestamps happens in this module. An order at 23:00
  IST is 17:30 UTC the same day; one at 05:00 IST is 23:30 UTC the day *before*.
* **The revenue bridge** matches ``MarginService.revenue_bridge`` term for term —
  discounts are ``discount_amount + payment_discount_amount``, refunds are dated
  by when they were *issued* and de-duplicated across the two refund sources, and
  ``net_revenue`` is measured independently as ``SUM(total_amount) - refunds``
  rather than derived from the other five terms. Deriving it would make the
  identity a restatement instead of a check; measuring it separately is what
  lets a mismatch be detected at all. The check runs on every bucket and a
  failure is reported as a warning on the row that was written, not swallowed.
* **Money is integer paise** end to end (``contracts.to_minor`` /
  ``from_minor``), converted to ``Decimal`` once at the moment of writing. No
  float touches a money value.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import case, delete, func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session, aliased, selectinload

from app.models.analytics_base import DIMENSION_UNKNOWN
from app.models.analytics_rollups import AggOrderDaily, AggOrderHourly, AggProductDaily
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Product
from app.models.return_request import ReturnItem, ReturnRequest
from app.services.analytics.aggregation.base import JobRunResult, register
from app.services.analytics.allocation import allocate_order
from app.services.analytics.contracts import RevenueBridge, from_minor, to_minor
from app.services.analytics.timebox import day_bounds_utc, local_day, store_timezone
from app.services.dashboard_service import _REVENUE_STATUSES

__all__ = [
    "OrderDailyJob",
    "OrderHourlyJob",
    "ProductDailyJob",
    "HOURS_PER_DAY",
    "MAX_JOB_WARNINGS",
]

#: Hours written per day, always, including the quiet ones. See "Pattern A".
HOURS_PER_DAY = 24

#: A job that emitted one warning per order would produce an unreadable run log
#: and a 500-char `analytics_sync_runs.error` column that truncates mid-sentence.
#: Distinct warnings are kept up to this many, then summarised.
MAX_JOB_WARNINGS = 8

#: OrderStatus -> the `agg_order_daily` column counting it. Exhaustive on
#: purpose: a new OrderStatus with no column here fails the completeness check in
#: `_status_counts` rather than quietly vanishing from the day's totals.
_STATUS_COLUMNS: dict[OrderStatus, str] = {
    OrderStatus.PENDING: "orders_pending",
    OrderStatus.PAID: "orders_paid",
    OrderStatus.SHIPPED: "orders_shipped",
    OrderStatus.DELIVERED: "orders_delivered",
    OrderStatus.CANCELLED: "orders_cancelled",
    OrderStatus.REFUNDED: "orders_refunded",
}


# ===========================================================================
# Shared helpers
# ===========================================================================
def _as_utc(moment: datetime) -> datetime:
    """Attach UTC to a naive timestamp, matching `timebox`'s convention.

    MySQL DATETIME carries no zone, so everything SQLAlchemy hands back is naive
    even though the column is declared ``DateTime(timezone=True)``. Containers
    run UTC and the application writes UTC, so naive means UTC — but it has to be
    said once, here, rather than assumed at four different call sites.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def _utcnow() -> datetime:
    """Naive UTC, for writing into a MySQL DATETIME (`computed_at`).

    The mirror image of `_as_utc`: aware on the way in for arithmetic, naive on
    the way out for storage, because `DateTime(timezone=True)` is a no-op on
    MySQL and a stored offset would be silently discarded.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _bucket_window(db: Session, bucket_date: date) -> tuple[datetime, datetime, ZoneInfo]:
    """The UTC half-open range covering one store-local reporting day."""
    tz = store_timezone(db)
    start, end = day_bounds_utc(bucket_date, tz)
    return start, end, tz


def _revenue_window(start: datetime, end: datetime) -> list[Any]:
    """Orders whose SALE belongs to this bucket.

    Uses ``margin._recognised_sale()`` rather than
    ``Order.status.in_(_REVENUE_STATUSES)``, so the rollup and ``MarginService``
    answer the same question. They diverged briefly and the consequence was
    concrete: a refunded order was excluded from revenue by its status *and*
    subtracted again as a refund, so an order placed and refunded in one window
    reported NEGATIVE revenue. The bridge identity did not catch it, because
    both sides of the identity shared the wrong input.

    This matters more here than in the service. Dashboards read the ROLLUPS, so
    a fix that lands only in ``margin.py`` leaves the wrong number on screen.
    """
    from app.services.analytics.margin import _recognised_sale

    return [
        _recognised_sale(),
        Order.created_at >= start,
        Order.created_at < end,
    ]


def _all_status_window(start: datetime, end: datetime) -> list[Any]:
    """Every order placed in this bucket, whatever became of it."""
    return [Order.created_at >= start, Order.created_at < end]


def _dim(value: str | None) -> str:
    """COALESCE a dimension to the `'-'` sentinel. Never let a NULL through.

    A NULL in a UNIQUE key does not collide on MySQL, so it would silently defeat
    the idempotency key this table's whole recompute story rests on.
    """
    text = (value or "").strip()
    return text[:64] if text else DIMENSION_UNKNOWN


def _cap(warnings: Sequence[str]) -> tuple[str, ...]:
    """De-duplicate warnings and cap the list, keeping the count honest."""
    seen: list[str] = []
    for warning in warnings:
        if warning not in seen:
            seen.append(warning)
    if len(seen) <= MAX_JOB_WARNINGS:
        return tuple(seen)
    hidden = len(seen) - MAX_JOB_WARNINGS
    return tuple(seen[:MAX_JOB_WARNINGS]) + (f"... and {hidden} further warning(s)",)


def _upsert(
    db: Session,
    model: type,
    rows: Sequence[dict[str, Any]],
    *,
    key_columns: Sequence[str],
) -> int:
    """Pattern A: `INSERT ... ON DUPLICATE KEY UPDATE col = VALUES(col)`.

    Every non-key column is **overwritten** with the freshly computed value.
    Adding (``col = col + VALUES(col)``) is what turns a retry into a doubled
    bucket, so the update map is built mechanically from the row keys rather than
    hand-listed, and there is nowhere for an accumulating column to hide.

    Returns the number of rows presented, not MySQL's `rowcount` — see
    `JobRunResult.rows_written`.
    """
    if not rows:
        return 0
    statement = mysql_insert(model).values(list(rows))
    updates = {
        column: statement.inserted[column]
        for column in rows[0]
        if column not in set(key_columns)
    }
    db.execute(statement.on_duplicate_key_update(**updates))
    return len(rows)


def _refunds_minor(db: Session, start: datetime, end: datetime) -> int:
    """Refunds *issued* in the window, de-duplicated across the two sources.

    Mirrors ``MarginService._refunds_minor`` exactly, and must keep mirroring it:
    a returns-driven refund carries an explicit ``returns.refund_amount``, while
    a whole-order refund has no amount column at all and is valued at
    ``orders.total_amount`` — but only when the order has no refunded return row,
    or the same money is counted twice.

    Note the dating. A refund belongs to the day it was issued, not to the day
    the order was placed, which is why ``refund_sum`` on a bucket can be non-zero
    on a day with no orders at all. That is the correct behaviour and it is the
    reason the recompute queue exists: a July refund dirties nothing in March.
    """
    from_returns = db.execute(
        select(func.coalesce(func.sum(ReturnRequest.refund_amount), 0)).where(
            ReturnRequest.refunded_at.isnot(None),
            ReturnRequest.refunded_at >= start,
            ReturnRequest.refunded_at < end,
        )
    ).scalar_one()

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
        select(func.coalesce(func.sum(Order.total_amount), 0)).where(
            Order.refunded_at.isnot(None),
            Order.refunded_at >= start,
            Order.refunded_at < end,
            ~has_refunded_return,
        )
    ).scalar_one()

    return to_minor(from_returns) + to_minor(whole_order)


# ===========================================================================
# order_daily -> agg_order_daily          (Pattern A: one row per bucket)
# ===========================================================================
class OrderDailyJob:
    """The top-line revenue day: counts, the revenue bridge, and customers.

    One row per ``(bucket_date, tz_generation)``, so the key set is a single
    known key and Pattern A's upsert-overwrite is complete.

    Three status sets are in play and they are not interchangeable:

    * **All statuses** — ``orders_total``, the seven per-status counters and
      ``order_value_created``. This is *demand*: every order placed on the day,
      whatever happened to it afterwards. Never quote it as revenue.
    * **Revenue statuses** — every money column in the bridge, plus ``units``,
      ``costed_units``, ``cogs_sum`` and the customer counts.
    * **Refunds** — dated by when the refund was issued, so they belong to this
      bucket even when the order they reverse was placed months earlier.

    Customer counts are taken over revenue-status orders, not over all orders.
    Counting someone whose only order was cancelled as an acquired customer
    would inflate acquisition and make ``new_customers`` disagree with the
    revenue sitting next to it. ``new_customers + returning_customers ==
    distinct_customers`` holds by construction and is asserted below: "new" means
    no *earlier* revenue-status order exists, which is a property of the customer
    and the bucket start, so the two counts partition the distinct set exactly.

    ``costed_units`` counts units on lines that carry a non-null ``unit_cost``,
    and ``cogs_sum`` sums only those lines. A line with no cost snapshot is
    excluded from the numerator and still counted in ``units``, so
    ``costed_units / units`` is the coverage the API needs to say how much of the
    margin is actually measured. This is deliberately *not*
    ``COALESCE(unit_cost, 0)``, which is the defect ``profit_service.py`` has:
    zero cost reads as 100% margin, in the flattering direction, silently.
    """

    name = "order_daily"

    def run(self, db: Session, bucket_date: date, tz_generation: int) -> JobRunResult:
        start, end, _tz = _bucket_window(db, bucket_date)
        warnings: list[str] = []

        counts = self._status_counts(db, start, end, warnings)
        money = self._money(db, start, end)
        lines = self._lines(db, start, end)
        customers = self._customers(db, start, end, warnings)
        refunds_minor = _refunds_minor(db, start, end)

        bridge = RevenueBridge(
            gross_merchandise_sales_minor=lines["gms_minor"],
            discounts_minor=money["discount_minor"],
            tax_minor=money["tax_minor"],
            shipping_minor=money["shipping_minor"],
            cod_surcharge_minor=money["cod_minor"],
            refunds_minor=refunds_minor,
            # Measured independently, not derived — see the module docstring.
            # Built from the RECOGNISED order value, never the legacy one: the
            # legacy figure has already dropped refunded orders, so subtracting
            # the refund from it reverses the same sale twice.
            net_revenue_minor=money["recognised_order_value_minor"] - refunds_minor,
        )
        if not bridge.balances():
            # Written anyway, with the imbalance named. Refusing to write would
            # leave the day blank, which reads as "no trade" rather than "the
            # order rows do not add up" — the wrong one of the two to imply.
            warnings.append(
                f"revenue_bridge_imbalance: gross - discounts + tax + shipping + "
                f"cod - refunds differs from net revenue by "
                f"{from_minor(bridge.imbalance_minor())} on {bucket_date}; the "
                "order rows for this day are not internally consistent "
                "(usually orders.subtotal disagreeing with its own line items)"
            )

        row: dict[str, Any] = {
            "bucket_date": bucket_date,
            "tz_generation": tz_generation,
            "computed_at": _utcnow(),
            **counts,
            "order_value_created": from_minor(money["order_value_created_minor"]),
            "paid_order_value": from_minor(money["paid_order_value_minor"]),
            "gross_merchandise_sales": from_minor(lines["gms_minor"]),
            "net_merchandise_sales": from_minor(
                lines["gms_minor"] - money["discount_minor"]
            ),
            "net_revenue": from_minor(bridge.net_revenue_minor),
            "subtotal_sum": from_minor(money["subtotal_minor"]),
            "tax_sum": from_minor(money["tax_minor"]),
            "discount_sum": from_minor(money["discount_minor"]),
            "payment_discount_sum": from_minor(money["payment_discount_minor"]),
            "shipping_income": from_minor(money["shipping_minor"]),
            "cod_surcharge_sum": from_minor(money["cod_minor"]),
            "refund_sum": from_minor(refunds_minor),
            "cogs_sum": from_minor(lines["cogs_minor"]),
            "units": lines["units"],
            "costed_units": lines["costed_units"],
            **customers,
        }
        written = _upsert(
            db,
            AggOrderDaily,
            [row],
            key_columns=("bucket_date", "tz_generation"),
        )
        return JobRunResult(rows_written=written, warnings=_cap(warnings))

    # -- pieces -----------------------------------------------------------
    @staticmethod
    def _status_counts(
        db: Session, start: datetime, end: datetime, warnings: list[str]
    ) -> dict[str, int]:
        """Per-status counters over every order placed in the bucket."""
        columns = [func.count(Order.id).label("orders_total")]
        for status, column in _STATUS_COLUMNS.items():
            columns.append(
                func.coalesce(
                    func.sum(case((Order.status == status, 1), else_=0)), 0
                ).label(column)
            )
        row = db.execute(
            select(*columns).where(*_all_status_window(start, end))
        ).one()._mapping

        counts = {key: int(row[key] or 0) for key in row.keys()}
        counted = sum(counts[column] for column in _STATUS_COLUMNS.values())
        if counted != counts["orders_total"]:
            # Only reachable if `orders.status` holds a value OrderStatus does
            # not, which would otherwise silently drop those orders from every
            # per-status counter while leaving orders_total right.
            warnings.append(
                f"unmapped_order_status: {counts['orders_total'] - counted} order(s) "
                "carry a status with no counter column; the per-status counters "
                "do not add up to orders_total"
            )
        return counts

    @staticmethod
    def _legacy_paid_order_value(db: Session, start: datetime, end: datetime) -> int:
        """SUM(total_amount) under the LEGACY revenue rule, in minor units.

        Restates ``dashboard_service._REVENUE_STATUSES`` deliberately rather than
        reusing ``_revenue_window``: that window now carries the corrected
        recognition rule, and this column exists precisely to NOT move with it.
        """
        total = db.execute(
            select(func.coalesce(func.sum(Order.total_amount), 0)).where(
                Order.status.in_(_REVENUE_STATUSES),
                Order.created_at >= start,
                Order.created_at < end,
            )
        ).scalar_one()
        return to_minor(total)

    @staticmethod
    def _money(db: Session, start: datetime, end: datetime) -> dict[str, int]:
        """Order-level money, in paise. Demand over all statuses, rest revenue-only."""
        created = db.execute(
            select(func.coalesce(func.sum(Order.total_amount), 0)).where(
                *_all_status_window(start, end)
            )
        ).scalar_one()

        row = db.execute(
            select(
                func.coalesce(func.sum(Order.total_amount), 0).label("total"),
                func.coalesce(func.sum(Order.subtotal), 0).label("subtotal"),
                func.coalesce(func.sum(Order.tax_amount), 0).label("tax"),
                func.coalesce(
                    func.sum(Order.discount_amount + Order.payment_discount_amount), 0
                ).label("discount"),
                func.coalesce(func.sum(Order.payment_discount_amount), 0).label(
                    "payment_discount"
                ),
                func.coalesce(func.sum(Order.shipping_amount), 0).label("shipping"),
                func.coalesce(func.sum(Order.cod_surcharge_amount), 0).label("cod"),
            ).where(*_revenue_window(start, end))
        ).one()

        # paid_order_value is the SHADOW-MODE PARITY ANCHOR and must keep the
        # LEGACY rule (`_REVENUE_STATUSES`, which excludes refunded orders) —
        # not the corrected recognition rule the other money columns use.
        #
        # This is not a redundant second query. The two rules genuinely differ:
        # recognition admits a refunded order (the sale happened; the reversal is
        # booked in the refund's own period), the legacy rule drops it entirely.
        # If this column moves, the comparison against the legacy Sales/Profit
        # pages stops meaning anything and we lose the only evidence that the new
        # numbers are right.
        legacy_paid = OrderDailyJob._legacy_paid_order_value(db, start, end)

        return {
            "order_value_created_minor": to_minor(created),
            # TWO order-value figures, deliberately, because they answer
            # different questions and must never be swapped:
            #
            #   paid_order_value_minor  — the LEGACY rule (_REVENUE_STATUSES),
            #       which drops a refunded order entirely. This is the shadow-mode
            #       parity anchor and must stay byte-identical to
            #       DashboardService._revenue_summary.
            #
            #   recognised_order_value_minor — the CORRECTED recognition rule,
            #       which keeps a refunded order's sale in the period it happened.
            #       This is what net_revenue must be built from.
            #
            # Deriving net_revenue from the legacy figure re-creates the exact
            # double-reversal this recognition work removed: the refunded order is
            # already absent from the legacy total, and then the refund is
            # subtracted again, so an order placed and refunded in one window
            # reports NEGATIVE revenue. The revenue bridge does not catch it,
            # because both sides of the identity share the wrong input.
            "paid_order_value_minor": legacy_paid,
            "recognised_order_value_minor": to_minor(row.total),
            "subtotal_minor": to_minor(row.subtotal),
            "tax_minor": to_minor(row.tax),
            "discount_minor": to_minor(row.discount),
            "payment_discount_minor": to_minor(row.payment_discount),
            "shipping_minor": to_minor(row.shipping),
            "cod_minor": to_minor(row.cod),
        }

    @staticmethod
    def _lines(db: Session, start: datetime, end: datetime) -> dict[str, int]:
        """Line-level volume and cost over revenue-status orders."""
        row = db.execute(
            select(
                func.coalesce(
                    func.sum(OrderItem.quantity * OrderItem.unit_price), 0
                ).label("gms"),
                func.coalesce(func.sum(OrderItem.quantity), 0).label("units"),
                func.coalesce(
                    func.sum(
                        case(
                            (OrderItem.unit_cost.isnot(None), OrderItem.quantity),
                            else_=0,
                        )
                    ),
                    0,
                ).label("costed_units"),
                # SQL drops NULL rows from SUM, so a line with no cost snapshot
                # contributes nothing here while still counting in `units`.
                func.coalesce(
                    func.sum(OrderItem.quantity * OrderItem.unit_cost), 0
                ).label("cogs"),
            )
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .where(*_revenue_window(start, end))
        ).one()
        return {
            "gms_minor": to_minor(row.gms),
            "units": int(row.units or 0),
            "costed_units": int(row.costed_units or 0),
            "cogs_minor": to_minor(row.cogs),
        }

    @staticmethod
    def _customers(
        db: Session, start: datetime, end: datetime, warnings: list[str]
    ) -> dict[str, int]:
        """Distinct / new / returning customers over revenue-status orders."""
        distinct = int(
            db.execute(
                select(func.count(func.distinct(Order.user_id))).where(
                    *_revenue_window(start, end)
                )
            ).scalar_one()
            or 0
        )

        earlier = aliased(Order)
        had_ordered_before = (
            select(earlier.id)
            .where(
                earlier.user_id == Order.user_id,
                earlier.status.in_(_REVENUE_STATUSES),
                earlier.created_at < start,
            )
            .correlate(Order)
            .exists()
        )
        new = int(
            db.execute(
                select(func.count(func.distinct(Order.user_id))).where(
                    *_revenue_window(start, end), ~had_ordered_before
                )
            ).scalar_one()
            or 0
        )

        if new > distinct:  # pragma: no cover - defensive
            warnings.append(
                f"customer_partition: new_customers ({new}) exceeds "
                f"distinct_customers ({distinct}); the two counts no longer "
                "partition the day"
            )
            new = distinct
        return {
            "distinct_customers": distinct,
            "new_customers": new,
            "returning_customers": distinct - new,
        }


# ===========================================================================
# order_hourly -> agg_order_hourly        (Pattern A: 24 fixed rows per bucket)
# ===========================================================================
class OrderHourlyJob:
    """Intraday shape: 24 store-local hours per reporting day, always all 24.

    ``bucket_hour`` is derived as **whole hours since the store-local start of
    the reporting day**, where that start comes from ``timebox.day_bounds_utc``.
    Deriving it that way rather than reading ``.hour`` off a converted timestamp
    keeps every boundary in this subsystem coming from one place, and makes hour
    buckets automatically consistent with the day bucket that contains them: an
    order can never land in hour 23 of a day it does not belong to.

    Writing all 24 hours — zero-filling the quiet ones — is what qualifies this
    table for Pattern A. See the module docstring.

    **This table does not net off refunds, and therefore does not reconcile to
    ``agg_order_daily.net_revenue`` on a day with refunds.** A refund's only
    honest timestamp is when it was *issued*; subtracting it from the hour the
    order was *placed* is impossible, and putting it in the hour it was issued
    would produce hours with negative revenue and no orders in a table whose one
    job is showing what a normal Tuesday looks like. So hourly ``net_revenue`` is
    revenue-status orders placed in that hour, valued at ``total_amount``, gross
    of later refunds. The daily table remains the reconciled record.
    """

    name = "order_hourly"

    def run(self, db: Session, bucket_date: date, tz_generation: int) -> JobRunResult:
        start, end, tz = _bucket_window(db, bucket_date)
        warnings: list[str] = []

        placed = db.execute(
            select(
                Order.id,
                Order.created_at,
                Order.total_amount,
                func.coalesce(func.sum(OrderItem.quantity), 0).label("units"),
            )
            .select_from(Order)
            .outerjoin(OrderItem, OrderItem.order_id == Order.id)
            .where(*_revenue_window(start, end))
            .group_by(Order.id, Order.created_at, Order.total_amount)
        ).all()

        orders = [0] * HOURS_PER_DAY
        revenue_minor = [0] * HOURS_PER_DAY
        units = [0] * HOURS_PER_DAY

        for row in placed:
            hour = self._local_hour(row.created_at, start, bucket_date, tz, warnings)
            orders[hour] += 1
            revenue_minor[hour] += to_minor(row.total_amount)
            units[hour] += int(row.units or 0)

        computed_at = _utcnow()
        rows = [
            {
                "bucket_date": bucket_date,
                "bucket_hour": hour,
                "tz_generation": tz_generation,
                "computed_at": computed_at,
                "orders": orders[hour],
                "net_revenue": from_minor(revenue_minor[hour]),
                "units": units[hour],
            }
            for hour in range(HOURS_PER_DAY)
        ]
        written = _upsert(
            db,
            AggOrderHourly,
            rows,
            key_columns=("bucket_date", "bucket_hour", "tz_generation"),
        )
        return JobRunResult(rows_written=written, warnings=_cap(warnings))

    @staticmethod
    def _local_hour(
        moment: datetime,
        day_start_utc: datetime,
        bucket_date: date,
        tz: ZoneInfo,
        warnings: list[str],
    ) -> int:
        """Whole store-local hours between the day's start and this instant."""
        placed_at = _as_utc(moment)
        hour = int((placed_at - day_start_utc).total_seconds() // 3600)
        if 0 <= hour < HOURS_PER_DAY:
            return hour
        # A 25-hour DST day has a 24th hour and `bucket_hour` has nowhere to put
        # it. Folding it into 23 keeps the day's totals whole — losing the order
        # entirely would silently under-report the day — and says so out loud.
        # Not reachable in Asia/Kolkata, which has no DST.
        warnings.append(
            f"hour_out_of_range: an order at {placed_at.isoformat()} is "
            f"{hour}h into store-local {bucket_date} (a DST-lengthened day); it "
            f"was folded into hour {HOURS_PER_DAY - 1}. local_day says "
            f"{local_day(placed_at, tz)}"
        )
        return min(max(hour, 0), HOURS_PER_DAY - 1)


# ===========================================================================
# product_daily -> agg_product_daily   (Pattern B: delete and reinsert)
# ===========================================================================
class ProductDailyJob:
    """Per-product, per-day sales, cost coverage and returns.

    Pattern B, and this table is why the pattern exists: which products sold on a
    day is discovered from the data, so a product can *leave* the bucket when its
    only order is cancelled or deleted. An upsert would leave that product's row
    behind at yesterday's numbers, in the table that drives the best-seller
    leaderboard. DELETE by ``(bucket_date, tz_generation)`` then INSERT, in the
    runner's single transaction.

    **Identity is snapshotted at aggregation time.** ``sku_snapshot`` and
    ``category_id_snapshot`` record what the product was when the bucket was
    computed, so re-categorising a product tomorrow does not rewrite last
    quarter's category mix. It surprises people once and is the correct
    behaviour for a report.

    **Money per line comes from the allocator, not from a per-line column.**
    ``order_items`` has no discount column — there is only an order-level
    ``discount_amount`` — so ``net_merchandise_sales`` needs the order total
    pushed down onto its lines. ``allocation.allocate_order`` does that with
    largest-remainder integer arithmetic, so the per-product shares sum back to
    the order-level figure exactly and this table reconciles with
    ``agg_order_daily`` to the paisa rather than approximately. Tax, shipping and
    COD surcharge are deliberately *not* folded in: the model docstring rules out
    a "product revenue" that includes them, because neither can be honestly
    attributed to a line.

    **Returns are dated by ``returns.refunded_at``**, matching ``refund_sum`` on
    the daily table. A return that was requested, approved or even received but
    not yet refunded is not counted: those states can still end in a rejection,
    and counting them would report goods as returned that we may yet keep the
    money for. A consequence worth knowing: a product can appear in a bucket with
    ``units = 0`` and ``returned_units > 0``, on a day it sold nothing and a
    months-old order came back.
    """

    name = "product_daily"

    def run(self, db: Session, bucket_date: date, tz_generation: int) -> JobRunResult:
        start, end, _tz = _bucket_window(db, bucket_date)
        warnings: list[str] = []

        sold = self._sold(db, start, end, warnings)
        returned = self._returned(db, start, end)

        product_ids = sorted(set(sold) | set(returned))
        snapshots = self._snapshots(db, product_ids, warnings)

        computed_at = _utcnow()
        rows: list[dict[str, Any]] = []
        for product_id in product_ids:
            sale = sold.get(product_id)
            back = returned.get(product_id, (0, 0))
            sku, category_id = snapshots[product_id]
            rows.append(
                {
                    "bucket_date": bucket_date,
                    "product_id": product_id,
                    "tz_generation": tz_generation,
                    "computed_at": computed_at,
                    "sku_snapshot": sku,
                    "category_id_snapshot": category_id,
                    "units": sale["units"] if sale else 0,
                    "orders": len(sale["orders"]) if sale else 0,
                    "gross_merchandise_sales": from_minor(
                        sale["gms_minor"] if sale else 0
                    ),
                    "net_merchandise_sales": from_minor(
                        sale["net_minor"] if sale else 0
                    ),
                    "line_cost": from_minor(sale["line_cost_minor"] if sale else 0),
                    "costed_units": sale["costed_units"] if sale else 0,
                    "returned_units": back[0],
                    "returned_value": from_minor(back[1]),
                }
            )

        # DELETE first, unconditionally — including when `rows` is empty, which is
        # exactly the case an upsert cannot express: a bucket that used to have
        # products and now has none.
        deleted = db.execute(
            delete(AggProductDaily).where(
                AggProductDaily.bucket_date == bucket_date,
                AggProductDaily.tz_generation == tz_generation,
            )
        ).rowcount
        if rows:
            db.execute(mysql_insert(AggProductDaily).values(rows))

        return JobRunResult(
            rows_written=len(rows),
            rows_deleted=int(deleted or 0),
            warnings=_cap(warnings),
        )

    # -- pieces -----------------------------------------------------------
    @staticmethod
    def _sold(
        db: Session, start: datetime, end: datetime, warnings: list[str]
    ) -> dict[int, dict[str, Any]]:
        """Per-product sales for the bucket, with discounts allocated per line."""
        orders = (
            db.execute(
                select(Order)
                .where(*_revenue_window(start, end))
                .options(selectinload(Order.items))
                .order_by(Order.id)
            )
            .scalars()
            .unique()
            .all()
        )

        by_product: dict[int, dict[str, Any]] = defaultdict(
            lambda: {
                "units": 0,
                "orders": set(),
                "gms_minor": 0,
                "net_minor": 0,
                "line_cost_minor": 0,
                "costed_units": 0,
            }
        )
        for order in orders:
            if not order.items:
                continue
            allocation = allocate_order(order, order.items)
            warnings.extend(allocation.warnings)
            shares = {line.order_item_id: line for line in allocation.lines}

            for item in order.items:
                share = shares.get(int(item.id))
                if share is None:  # pragma: no cover - allocator covers every line
                    continue
                quantity = int(item.quantity)
                bucket = by_product[int(item.product_id)]
                bucket["units"] += quantity
                bucket["orders"].add(int(order.id))
                bucket["gms_minor"] += share.extended_price
                # Net of BOTH discounts, matching `agg_order_daily.discount_sum`
                # so the two tables reconcile. Tax/shipping/COD stay out — see
                # the class docstring.
                bucket["net_minor"] += (
                    share.extended_price - share.discount - share.payment_discount
                )
                if item.unit_cost is not None:
                    bucket["line_cost_minor"] += to_minor(
                        Decimal(item.unit_cost) * quantity
                    )
                    bucket["costed_units"] += quantity
        return dict(by_product)

    @staticmethod
    def _returned(
        db: Session, start: datetime, end: datetime
    ) -> dict[int, tuple[int, int]]:
        """Per-product returned units and value for refunds issued in the bucket.

        Valued at the line's own ``unit_price``, which is the merchandise value
        that came back. Deliberately not ``returns.refund_amount``: that is an
        order-level figure an admin can override, and splitting it across lines
        would be an allocation of an already-approximate number.
        """
        rows = db.execute(
            select(
                OrderItem.product_id.label("product_id"),
                func.coalesce(func.sum(ReturnItem.quantity), 0).label("units"),
                func.coalesce(
                    func.sum(ReturnItem.quantity * OrderItem.unit_price), 0
                ).label("value"),
            )
            .select_from(ReturnItem)
            .join(ReturnRequest, ReturnRequest.id == ReturnItem.return_id)
            .join(OrderItem, OrderItem.id == ReturnItem.order_item_id)
            .where(
                ReturnRequest.refunded_at.isnot(None),
                ReturnRequest.refunded_at >= start,
                ReturnRequest.refunded_at < end,
            )
            .group_by(OrderItem.product_id)
        ).all()
        return {
            int(row.product_id): (int(row.units or 0), to_minor(row.value))
            for row in rows
        }

    @staticmethod
    def _snapshots(
        db: Session, product_ids: Iterable[int], warnings: list[str]
    ) -> dict[int, tuple[str, int | None]]:
        """`(sku_snapshot, category_id_snapshot)` per product, as of right now.

        A product that no longer exists snapshots as the `'-'` sentinel rather
        than being dropped: the sale happened, and this table has no FK precisely
        so a deleted product cannot erase its own history.
        """
        ids = list(product_ids)
        if not ids:
            return {}
        found = {
            int(row.id): (_dim(row.sku), row.category_id)
            for row in db.execute(
                select(Product.id, Product.sku, Product.category_id).where(
                    Product.id.in_(ids)
                )
            ).all()
        }
        missing = [product_id for product_id in ids if product_id not in found]
        if missing:
            warnings.append(
                f"product_missing: {len(missing)} product(s) sold in this bucket no "
                f"longer exist ({sorted(missing)[:5]}); their sku_snapshot is "
                f"'{DIMENSION_UNKNOWN}' and their category is unknown"
            )
        for product_id in missing:
            found[product_id] = (DIMENSION_UNKNOWN, None)
        return found


register(OrderDailyJob())
register(OrderHourlyJob())
register(ProductDailyJob())
