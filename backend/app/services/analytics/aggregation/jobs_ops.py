"""The operational rollups: funnel, inventory ledger, shipments.

Registered here: ``funnel_daily`` (``agg_funnel_daily``), ``inventory_daily``
(``agg_inventory_daily``) and ``shipment_daily`` (``agg_shipment_daily``). The
two write patterns and their guard rails are documented in
:mod:`app.services.analytics.aggregation.jobs` and its helpers are imported
rather than restated.

The three jobs here share one theme: **each of them has a number it refuses to
produce**, and the refusal is the feature.

``funnel_daily`` writes zeros, and means them
---------------------------------------------
``cart_events`` is real and empty: nothing in this codebase emits a funnel event
yet. This job therefore writes a row of honest zeros for every day it is asked
about, and it does **not** synthesise steps from orders. Deriving
``orders_placed`` from the orders table while every step above it reads zero
would render a funnel that says 100% of carts convert — a chart that is not
merely wrong but flattering, and unfalsifiable without reading this module.
A zero here means "the source recorded nothing", and when the source has never
recorded anything at all the run says so as a warning. The API side is already
covered: ``FunnelResolver`` emits ``FUNNEL_STARTS_AT_CART`` on every response.

``inventory_daily`` is FORWARD-ONLY
-----------------------------------
``products.stock`` is a mutable integer with no audit trail. Walking it
backwards through order history would omit every restock, every admin
correction and every unrestocked return, and would look completely plausible
while doing so. So this job reconstructs stock from ``inventory_movements``
where the ledger covers a product, falls back to a point-in-time read of
``products`` only for a day that has not yet closed, and **refuses any bucket
before the first one it ever built**. That first date is written to the
``analytics.inventory_history_since`` setting so every history view can clamp
its x-axis to it: a flat zero line before the ledger starts reads as "we held no
stock" rather than "we have no data", and at a glance it is indistinguishable
from a real stockout.

``shipment_daily`` has no on-time column, and cannot
----------------------------------------------------
There is no promised-delivery date, no SLA and no courier ETA anywhere in this
schema. Any "on-time %" would be measured against a threshold invented at query
time and presented as a fact, so ``on_time``, ``sla_met`` and ``late_shipments``
are named as forbidden by the model and are absent here. What exists instead is
the honest input: two sum+count duration pairs with their own denominators.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import case, delete, func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.models.analytics_facts import CartEvent, CartEventType, InventoryMovement, MovementType
from app.models.analytics_rollups import (
    AggFunnelDaily,
    AggInventoryDaily,
    AggShipmentDaily,
)
from app.models.order import Order
from app.models.product import Product
from app.models.shipment import Shipment, ShipmentStatus
from app.models.system_setting import SystemSetting
from app.services.analytics.aggregation.base import JobRunResult, register
from app.services.analytics.aggregation.jobs import (
    _as_utc,
    _bucket_window,
    _cap,
    _dim,
    _upsert,
    _utcnow,
)
from app.services.analytics.contracts import from_minor, to_minor
from app.services.analytics.timebox import local_day

__all__ = [
    "FunnelDailyJob",
    "InventoryDailyJob",
    "ShipmentDailyJob",
    "INVENTORY_HISTORY_SINCE_KEY",
    "INVENTORY_INSERT_CHUNK",
]

#: Setting holding the first store-local day the inventory ledger ever produced.
#: Named in the `AggInventoryDaily` docstring; every history view must read it
#: and clamp its axis to it.
INVENTORY_HISTORY_SINCE_KEY = "analytics.inventory_history_since"

#: `system_settings.category` for the row above. The admin settings page groups
#: by this column, so an unrecognised category would strand the row in the UI.
INVENTORY_HISTORY_SINCE_CATEGORY = "store"

#: Same reasoning as the snapshot job's chunk: one INSERT carrying every product
#: would grow with the catalogue and eventually exceed `max_allowed_packet`.
INVENTORY_INSERT_CHUNK = 500

#: CartEventType -> the `agg_funnel_daily` column counting it. Exhaustive over
#: the vocabulary on purpose: an event type with no column here is reported as a
#: warning rather than silently dropped from the funnel.
_FUNNEL_COLUMNS: dict[str, str] = {
    CartEventType.PRODUCT_VIEWED: "product_views",
    CartEventType.CART_VIEWED: "cart_views",
    CartEventType.ITEM_ADDED: "items_added",
    CartEventType.CHECKOUT_STARTED: "checkouts_started",
    CartEventType.SHIPPING_SUBMITTED: "shipping_submitted",
    CartEventType.PAYMENT_INITIATED: "payments_initiated",
    CartEventType.PAYMENT_FAILED: "payments_failed",
    CartEventType.ORDER_PLACED: "orders_placed",
}

#: ITEM_REMOVED is a real event with no funnel column: removing an item is not a
#: step and counting it would make the funnel widen where it should narrow. It
#: is listed so `_FUNNEL_COLUMNS` staying exhaustive can be checked without
#: treating this one as an unknown type on every run.
_FUNNEL_IGNORED: frozenset[str] = frozenset({CartEventType.ITEM_REMOVED})


# ===========================================================================
# funnel_daily -> agg_funnel_daily        (Pattern A: one row per bucket)
# ===========================================================================
class FunnelDailyJob:
    """Checkout funnel step counters for one store-local day.

    One row per ``(bucket_date, tz_generation)``, so Pattern A's
    upsert-overwrite is complete: the key set is a single known key and nothing
    can disappear between runs.

    **Every counter is an EVENT count.** A session that viewed six products
    contributes six to ``product_views``. That is what makes the columns
    additive across days — the property the whole rollup design rests on — and
    it means the step-to-step ratios the resolver computes are event ratios, not
    session conversion. ``distinct_sessions`` is the single exception: it is a
    distinct count, is **not additive**, and counts only sessions that reached at
    least one tracked step.

    **The source is ``cart_events`` and nothing else.** That table is currently
    empty because no code path emits funnel events yet, so this job writes
    zeros — which is a measurement ("the source recorded nothing on this day"),
    not a placeholder. It deliberately does not fall back to the orders table for
    ``orders_placed``: a funnel whose bottom step is populated from orders while
    every step above it is zero renders as a 100%-converting checkout, and the
    reader has no way to see why. When the whole table is empty the run carries a
    warning saying instrumentation is not emitting, which is how a quiet day
    stays distinguishable from an unwired feature.

    ``FUNNEL_STARTS_AT_CART`` — there is no session/visit top of funnel here and
    therefore no site-wide conversion rate. ``FunnelResolver`` emits that
    warning on every response; this job's job is only to keep the numbers below
    it honest.
    """

    name = "funnel_daily"

    def run(self, db: Session, bucket_date: date, tz_generation: int) -> JobRunResult:
        start, end, _tz = _bucket_window(db, bucket_date)
        warnings: list[str] = []

        counts = {column: 0 for column in _FUNNEL_COLUMNS.values()}
        by_type = db.execute(
            select(
                CartEvent.event_type.label("event_type"),
                func.count(CartEvent.id).label("events"),
            )
            .where(CartEvent.occurred_at >= start, CartEvent.occurred_at < end)
            .group_by(CartEvent.event_type)
        ).all()

        unknown: list[str] = []
        for row in by_type:
            column = _FUNNEL_COLUMNS.get(str(row.event_type))
            if column is None:
                if str(row.event_type) not in _FUNNEL_IGNORED:
                    unknown.append(str(row.event_type))
                continue
            counts[column] = int(row.events or 0)
        if unknown:
            warnings.append(
                f"funnel_unknown_event_type: {sorted(set(unknown))} occurred on "
                f"{bucket_date} and no funnel column counts them; the step "
                "counters do not describe those events"
            )

        distinct_sessions = int(
            db.execute(
                select(func.count(func.distinct(CartEvent.session_key))).where(
                    CartEvent.occurred_at >= start, CartEvent.occurred_at < end
                )
            ).scalar_one()
            or 0
        )

        if not any(counts.values()) and not distinct_sessions:
            warnings.extend(self._empty_source_warning(db, bucket_date))

        row: dict[str, Any] = {
            "bucket_date": bucket_date,
            "tz_generation": tz_generation,
            "computed_at": _utcnow(),
            **counts,
            "distinct_sessions": distinct_sessions,
        }
        written = _upsert(
            db,
            AggFunnelDaily,
            [row],
            key_columns=("bucket_date", "tz_generation"),
        )
        return JobRunResult(rows_written=written, warnings=_cap(warnings))

    @staticmethod
    def _empty_source_warning(db: Session, bucket_date: date) -> list[str]:
        """Separate "no events ever" from "no events that day".

        Both write the same row of zeros, and only one of them is a quiet
        Tuesday. One ``LIMIT 1`` probe of the whole table is what makes the run
        log able to tell them apart.
        """
        any_event = db.execute(select(CartEvent.id).limit(1)).scalars().first()
        if any_event is not None:
            return []
        return [
            "funnel_source_empty: cart_events holds no rows at all, so the funnel "
            f"for {bucket_date} is zeros because nothing emits funnel events yet, "
            "not because nobody shopped. Steps are NOT derived from orders — see "
            "AggFunnelDaily.FUNNEL_STARTS_AT_CART"
        ]


# ===========================================================================
# inventory_daily -> agg_inventory_daily  (Pattern B: delete and reinsert)
# ===========================================================================
class InventoryDailyJob:
    """End-of-day stock position per product. Forward-only, never backfilled.

    Pattern B: which products the ledger covers is discovered, and a product
    deleted from the catalogue must leave the bucket rather than linger at its
    last known level.

    Where the level comes from
    --------------------------
    Two sources, in this order, per product:

      1. **``inventory_movements``** — ``SUM(delta)`` over every movement before
         the store-local close of the bucket. This is exact: the ledger is
         append-only and ``sum(delta)`` reconciling with ``products.stock`` is
         the invariant it exists to make checkable.
      2. **A point-in-time read of ``products.stock``** — used only for a bucket
         whose day has not yet ended in store-local time, and only for products
         the ledger does not cover. For a past day it is refused, because
         today's mutable integer is not yesterday's closing balance and writing
         it as one is exactly the fiction this table's docstring forbids. Those
         products are skipped and named in a warning.

    ``units_sold`` and ``units_restocked`` are always ledger-derived, from
    ``COMMIT_SALE`` and ``RETURN_RESTOCK`` movements inside the bucket. An
    ``ADMIN_ADJUSTMENT`` moves ``stock_close`` without moving either flow — that
    is not a bug, it is the difference between a level and a flow, and it is the
    reason ``movement_type`` exists. A product with no ledger rows therefore
    shows zero flows, which is a true statement about the ledger and is why the
    history-since setting and the run warnings matter.

    ``stock_close`` is a LEVEL — never sum it across days. ``units_sold`` and
    ``units_restocked`` are flows and are additive. Mixing the two is the classic
    inventory reporting bug.

    The forward-only guarantee
    --------------------------
    The first bucket this job ever writes is recorded in the
    ``analytics.inventory_history_since`` setting, read and written directly
    against ``system_settings`` rather than through the Redis-cached
    ``SettingsService`` — a cached miss would let the job rewrite the marker and
    silently move the start of history. Any bucket earlier than that date is
    refused with a warning and writes nothing.

    ``reorder_gap``: measured where it is configured, NULL where it is not
    ----------------------------------------------------------------------
    ``products.reorder_point`` exists now, so this column is
    ``stock_close - reorder_point`` — a difference of two levels, and therefore
    itself a LEVEL (``metric_kind`` classifies it so; never sum it).

    Three values, three different statements, and the job keeps them apart:

    * **NULL** — no reorder point is configured on the product. This is not
      zero and must never be coalesced to zero: 0 would assert "reorder at an
      empty shelf, and we are exactly there", which is a claim nobody entered.
      Most of the catalogue is NULL and that is the honest reading of it. No
      warning is raised for it either — unlike ``stock_value_close``, which
      writes a real 0 for an unknown cost and therefore needs the run log to
      say so, this column *can* represent "unknown" in the value itself, so a
      per-run coverage warning would be noise restating what the NULL says.
    * **0** — configured, and stock is sitting exactly on the threshold.
    * **negative** — configured, and stock is BELOW the threshold. Not clamped:
      the depth below the reorder point is the whole operational signal, and a
      clamp at zero would flatten "one unit short" and "two hundred short" into
      the same row.

    Columns this job cannot fill honestly
    -------------------------------------
    * ``stock_value_close`` values stock at ``products.cost``, which is nullable.
      A product with no cost contributes 0 to the value and is counted in a
      coverage warning; there is no quality column on this table to carry that,
      so the run log is where it has to live.
    * ``reorder_point`` is read as it stands **right now**, not as it stood on
      ``bucket_date`` — the catalogue keeps no history of the threshold, so a
      merchant raising it today restates every past ``reorder_gap`` on the next
      recompute. That is the same live-read limitation ``sku_snapshot`` and
      ``cost`` already carry here, and the fix is a threshold history table,
      not arithmetic in this job.
    """

    name = "inventory_daily"

    def run(self, db: Session, bucket_date: date, tz_generation: int) -> JobRunResult:
        start, end, tz = _bucket_window(db, bucket_date)
        warnings: list[str] = []

        since = _history_since(db)
        if since is not None and bucket_date < since:
            # Refused, not written empty: a bucket of zeros before the ledger
            # started is indistinguishable from a real day on which we held no
            # stock, and it is the one thing the history-since setting exists to
            # prevent anybody drawing.
            return JobRunResult(
                rows_written=0,
                warnings=_cap(
                    [
                        f"inventory_backfill_refused: {bucket_date} is before "
                        f"{INVENTORY_HISTORY_SINCE_KEY}={since}. Stock history "
                        "cannot be reconstructed — restocks and manual edits "
                        "before the ledger were never recorded — so this bucket "
                        "is left absent rather than fabricated"
                    ]
                ),
            )

        levels = self._levels(db, start, end, bucket_date, tz, warnings)
        flows = self._flows(db, start, end)
        catalogue = self._catalogue(db, set(levels) | set(flows))

        computed_at = _utcnow()
        rows: list[dict[str, Any]] = []
        uncosted = 0
        for product_id in sorted(levels):
            stock_close = levels[product_id]
            sold, restocked = flows.get(product_id, (0, 0))
            sku, cost, reorder_point = catalogue.get(
                product_id, (_dim(None), None, None)
            )
            if cost is None:
                uncosted += 1
            value_minor = 0 if cost is None else to_minor(cost) * stock_close
            is_oos = stock_close <= 0
            rows.append(
                {
                    "bucket_date": bucket_date,
                    "product_id": product_id,
                    "tz_generation": tz_generation,
                    "computed_at": computed_at,
                    "sku_snapshot": sku,
                    "stock_close": stock_close,
                    "stock_value_close": from_minor(value_minor),
                    "units_sold": sold,
                    "units_restocked": restocked,
                    "is_oos": is_oos,
                    "days_oos": self._days_oos(
                        db, product_id, bucket_date, tz_generation, is_oos
                    ),
                    # NULL when no reorder point is configured, and NULL only
                    # then. `or 0` / `coalesce(..., 0)` here would turn "nobody
                    # set a threshold" into "the threshold is zero and we are
                    # on it" for the whole catalogue — a plausible number on a
                    # LEVEL column, published under a heading that says
                    # "needs attention". Negative is kept as measured: it is
                    # how far below the threshold the shelf has fallen.
                    "reorder_gap": (
                        None if reorder_point is None else stock_close - int(reorder_point)
                    ),
                }
            )

        if uncosted:
            warnings.append(
                f"inventory_cost_coverage: {uncosted} product(s) have no cost, so "
                "their stock_value_close is 0 rather than measured; the stock "
                "value for this day is a floor, not a total"
            )

        deleted = int(
            db.execute(
                delete(AggInventoryDaily).where(
                    AggInventoryDaily.bucket_date == bucket_date,
                    AggInventoryDaily.tz_generation == tz_generation,
                )
            ).rowcount
            or 0
        )
        for index in range(0, len(rows), INVENTORY_INSERT_CHUNK):
            db.execute(
                mysql_insert(AggInventoryDaily).values(
                    rows[index : index + INVENTORY_INSERT_CHUNK]
                )
            )

        if rows and since is None:
            _set_history_since(db, bucket_date)
            warnings.append(
                f"inventory_history_started: {INVENTORY_HISTORY_SINCE_KEY} set to "
                f"{bucket_date}; this table has nothing before that date and every "
                "history view must clamp its axis to it"
            )

        return JobRunResult(
            rows_written=len(rows), rows_deleted=deleted, warnings=_cap(warnings)
        )

    # -- pieces -----------------------------------------------------------
    @staticmethod
    def _levels(
        db: Session,
        start: datetime,
        end: datetime,
        bucket_date: date,
        tz: ZoneInfo,
        warnings: list[str],
    ) -> dict[int, int]:
        """Closing stock per product: ledger where it exists, else today's read."""
        ledger = {
            int(row.product_id): int(row.stock or 0)
            for row in db.execute(
                select(
                    InventoryMovement.product_id.label("product_id"),
                    func.coalesce(func.sum(InventoryMovement.delta), 0).label("stock"),
                )
                .where(InventoryMovement.occurred_at < end)
                .group_by(InventoryMovement.product_id)
            ).all()
        }

        catalogue = db.execute(select(Product.id, Product.stock)).all()
        uncovered = [int(row.id) for row in catalogue if int(row.id) not in ledger]

        day_has_closed = local_day(datetime.now(timezone.utc), tz) > bucket_date
        if uncovered and day_has_closed:
            warnings.append(
                f"inventory_no_ledger_history: {len(uncovered)} product(s) have no "
                f"inventory_movements row on or before {bucket_date} and that day "
                "has already closed, so their closing stock is unknowable; they "
                "are absent from this bucket rather than filled with today's "
                "products.stock"
            )
        elif uncovered:
            # The day is still open (or is today): products.stock IS the level
            # right now, so this is a point-in-time read, not a reconstruction.
            # Re-running the bucket later restates it, which is correct.
            for row in catalogue:
                if int(row.id) not in ledger:
                    ledger[int(row.id)] = int(row.stock or 0)

        negative = [pid for pid, stock in ledger.items() if stock < 0]
        if negative:
            warnings.append(
                f"inventory_negative_stock: {len(negative)} product(s) reconstruct "
                f"to a negative closing level ({sorted(negative)[:5]}); the ledger "
                "and the stock write paths disagree, and the level is reported as "
                "measured rather than clamped"
            )
        return ledger

    @staticmethod
    def _flows(db: Session, start: datetime, end: datetime) -> dict[int, tuple[int, int]]:
        """Units sold and restocked inside the bucket, from the ledger only.

        ``COMMIT_SALE`` carries a negative delta, so ``units_sold`` is its
        negation. Movement types that change the level without being either a
        sale or a restock (``ADMIN_ADJUSTMENT``, ``INITIAL_SEED``) are
        deliberately absent from both flows — see the class docstring.
        """
        rows = db.execute(
            select(
                InventoryMovement.product_id.label("product_id"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                InventoryMovement.movement_type
                                == MovementType.COMMIT_SALE,
                                -InventoryMovement.delta,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("sold"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                InventoryMovement.movement_type
                                == MovementType.RETURN_RESTOCK,
                                InventoryMovement.delta,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("restocked"),
            )
            .where(
                InventoryMovement.occurred_at >= start,
                InventoryMovement.occurred_at < end,
            )
            .group_by(InventoryMovement.product_id)
        ).all()
        return {
            int(row.product_id): (int(row.sold or 0), int(row.restocked or 0))
            for row in rows
        }

    @staticmethod
    def _catalogue(
        db: Session, product_ids: set[int]
    ) -> dict[int, tuple[str, Decimal | None, int | None]]:
        """`(sku_snapshot, cost, reorder_point)` per product, as of right now.

        A product that no longer exists snapshots as the `'-'` sentinel rather
        than being dropped: the stock was really held, and this table has no FK
        precisely so a deleted product cannot erase its own history. It also
        gets `reorder_point = None`, which is right for the same reason: a row
        the catalogue no longer holds has no configured threshold to compare
        against, and inventing 0 would file it as "at its reorder point".

        `reorder_point` is nullable and is carried through as `None`, never
        coalesced — see the class docstring. The `int | None` is deliberate:
        `stock_close - reorder_point` is only computed on the branch where it
        is not None.
        """
        if not product_ids:
            return {}
        return {
            int(row.id): (
                _dim(row.sku),
                row.cost,
                None if row.reorder_point is None else int(row.reorder_point),
            )
            for row in db.execute(
                select(
                    Product.id, Product.sku, Product.cost, Product.reorder_point
                ).where(Product.id.in_(product_ids))
            ).all()
        }

    @staticmethod
    def _days_oos(
        db: Session,
        product_id: int,
        bucket_date: date,
        tz_generation: int,
        is_oos: bool,
    ) -> int:
        """Consecutive out-of-stock days as of this date; resets on restock.

        Read from the previous bucket's row, which is a read of another bucket
        and not a write — the job still touches only its own. A gap in the series
        (the day before was never built) restarts the counter at 1, because the
        run length before a gap is genuinely unknown and continuing the count
        across it would invent stockout days.
        """
        if not is_oos:
            return 0
        previous = db.execute(
            select(AggInventoryDaily.days_oos).where(
                AggInventoryDaily.product_id == product_id,
                AggInventoryDaily.bucket_date == bucket_date - timedelta(days=1),
                AggInventoryDaily.tz_generation == tz_generation,
            )
        ).scalars().first()
        return int(previous or 0) + 1


def _history_since(db: Session) -> date | None:
    """Read ``analytics.inventory_history_since`` straight from the table.

    Not through ``SettingsService``: its 60-second Redis cache can serve the
    "absent" sentinel just after another worker wrote the marker, and the job
    would then treat itself as the first run and move the start of history. The
    marker is written once, forever, so the read has to be authoritative.
    """
    raw = db.execute(
        select(SystemSetting.value).where(SystemSetting.key == INVENTORY_HISTORY_SINCE_KEY)
    ).scalars().first()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        # A hand-edited or corrupted marker must not silently disable the
        # forward-only guard, and must not crash the job either: treating it as
        # absent would rewrite it, so refuse to interpret it and let the caller
        # see the unparsable value on the next write attempt.
        raise ValueError(
            f"{INVENTORY_HISTORY_SINCE_KEY} holds {raw!r}, which is not an ISO "
            "date; inventory history cannot be clamped until it is corrected"
        )


def _set_history_since(db: Session, bucket_date: date) -> None:
    """Write the marker, creating the settings row if it does not exist.

    ``SettingsService.set_many`` ignores keys with no existing row (it is built
    for the admin form, where every key is seeded), so the row is created here.
    The write joins the job's transaction — the runner commits it with the
    bucket, so a failed bucket cannot leave a marker claiming history that was
    never written.
    """
    row = db.execute(
        select(SystemSetting).where(SystemSetting.key == INVENTORY_HISTORY_SINCE_KEY)
    ).scalars().first()
    if row is None:
        row = SystemSetting(
            key=INVENTORY_HISTORY_SINCE_KEY,
            value=bucket_date.isoformat(),
            category=INVENTORY_HISTORY_SINCE_CATEGORY,
            description=(
                "First store-local day with inventory ledger rows. Stock history "
                "is forward-only; views must clamp their axis to this date."
            ),
            is_secret=False,
        )
        db.add(row)
    else:  # pragma: no cover - only reachable if the row exists but is empty
        row.value = bucket_date.isoformat()
    db.flush()

    # Best-effort cache bust so a reader that already cached the absent sentinel
    # picks the marker up. Best-effort because the runner has not committed yet:
    # a reader racing this bust re-caches "absent" for another TTL, which delays
    # the clamp by up to a minute and never produces a wrong date.
    try:  # pragma: no cover - Redis behaviour, not job behaviour
        from app.db.redis import get_redis
        from app.services.settings_service import _cache_key

        get_redis().delete(_cache_key(INVENTORY_HISTORY_SINCE_KEY))
    except Exception:  # pragma: no cover - a cache miss is not a job failure
        pass


# ===========================================================================
# shipment_daily -> agg_shipment_daily    (Pattern B: delete and reinsert)
# ===========================================================================
class ShipmentDailyJob:
    """Fulfilment outcomes and cycle times per day x courier partner.

    Pattern B: which couriers carried anything on a day is discovered, and a
    courier must be able to leave the bucket when its only shipment is deleted.
    ``courier_partner`` is COALESCEd to the ``'-'`` sentinel — the source column
    is nullable and it sits in the UNIQUE key, where a NULL would not collide on
    MySQL and the next run would insert a second row for the same courier
    instead of replacing it.

    **This is a CREATION COHORT table.** ``shipments`` counts shipments created
    in the bucket, and every other counter is the state *those* shipments are in
    as of the moment the bucket was computed. That is what makes
    ``delivered / shipments`` a real delivery success rate: numerator and
    denominator describe the same population. The consequence is that a bucket
    keeps changing after its day ends — a parcel created Monday and delivered
    Thursday increments Monday's ``delivered`` when Monday is recomputed — which
    is precisely what the recompute queue is for. Dating deliveries by the day
    they happened instead would produce a rate whose two halves are different
    populations, and it would read as a plausible number.

    Outcome counters are **current statuses**, so they are mutually exclusive and
    ``delivered + delivery_failed + rto_initiated + cancelled <= shipments``; the
    remainder is still in flight. One deliberate exception: ``rto_initiated``
    counts ``RTO_INITIATED`` *and* ``RTO_DELIVERED``, because a returned parcel
    was necessarily initiated — counting only the first status would make the RTO
    rate fall as returns complete, which is the wrong direction and the kind of
    error that gets celebrated. ``rto_delivered`` is a subset of it.

    **No on-time / SLA / late column, ever.** There is no promised-delivery date
    anywhere in this schema; any such column would be measured against a
    threshold invented at query time. See the model docstring, which names
    ``on_time``, ``sla_met`` and ``late_shipments`` as forbidden. The honest
    substitute is here: ``sum_order_to_ship_seconds`` / ``n_order_to_ship`` and
    ``sum_ship_to_deliver_seconds`` / ``n_ship_to_deliver`` — sums and counts,
    never a stored average, each with its own denominator because the two
    populations differ (shipments that shipped vs shipments that delivered).
    """

    name = "shipment_daily"

    def run(self, db: Session, bucket_date: date, tz_generation: int) -> JobRunResult:
        start, end, _tz = _bucket_window(db, bucket_date)
        warnings: list[str] = []

        created = db.execute(
            select(
                Shipment.id,
                Shipment.courier_partner,
                Shipment.shipment_status,
                Shipment.shipment_cost,
                Shipment.shipped_at,
                Shipment.delivered_at,
                Order.created_at.label("ordered_at"),
            )
            .select_from(Shipment)
            .join(Order, Order.id == Shipment.order_id)
            .where(Shipment.created_at >= start, Shipment.created_at < end)
        ).all()

        buckets: dict[str, dict[str, Any]] = defaultdict(_empty_courier_bucket)
        missing_cost = 0
        for row in created:
            courier = _dim(row.courier_partner)
            bucket = buckets[courier]
            bucket["shipments"] += 1

            status = row.shipment_status
            if status == ShipmentStatus.DELIVERED:
                bucket["delivered"] += 1
            elif status == ShipmentStatus.DELIVERY_FAILED:
                bucket["delivery_failed"] += 1
            elif status == ShipmentStatus.CANCELLED:
                bucket["cancelled"] += 1
            if status in (ShipmentStatus.RTO_INITIATED, ShipmentStatus.RTO_DELIVERED):
                bucket["rto_initiated"] += 1
            if status == ShipmentStatus.RTO_DELIVERED:
                bucket["rto_delivered"] += 1

            if row.shipment_cost is None:
                missing_cost += 1
            else:
                bucket["cost_minor"] += to_minor(row.shipment_cost)

            self._add_duration(
                bucket,
                "order_to_ship",
                row.ordered_at,
                row.shipped_at,
                row.id,
                warnings,
            )
            self._add_duration(
                bucket,
                "ship_to_deliver",
                row.shipped_at,
                row.delivered_at,
                row.id,
                warnings,
            )

        if missing_cost:
            warnings.append(
                f"shipment_cost_coverage: {missing_cost} of {len(created)} shipment(s) "
                f"created on {bucket_date} carry no shipment_cost; the day's "
                "shipment_cost is a floor, not a total"
            )

        computed_at = _utcnow()
        rows = [
            {
                "bucket_date": bucket_date,
                "courier_partner": courier,
                "tz_generation": tz_generation,
                "computed_at": computed_at,
                "shipments": bucket["shipments"],
                "delivered": bucket["delivered"],
                "delivery_failed": bucket["delivery_failed"],
                "rto_initiated": bucket["rto_initiated"],
                "rto_delivered": bucket["rto_delivered"],
                "cancelled": bucket["cancelled"],
                "shipment_cost": from_minor(bucket["cost_minor"]),
                "sum_order_to_ship_seconds": bucket["sum_order_to_ship"],
                "n_order_to_ship": bucket["n_order_to_ship"],
                "sum_ship_to_deliver_seconds": bucket["sum_ship_to_deliver"],
                "n_ship_to_deliver": bucket["n_ship_to_deliver"],
            }
            for courier, bucket in sorted(buckets.items())
        ]

        # DELETE first, unconditionally — including when `rows` is empty, which
        # is the case an upsert cannot express: a bucket that used to have a
        # courier and now has none.
        deleted = int(
            db.execute(
                delete(AggShipmentDaily).where(
                    AggShipmentDaily.bucket_date == bucket_date,
                    AggShipmentDaily.tz_generation == tz_generation,
                )
            ).rowcount
            or 0
        )
        if rows:
            db.execute(mysql_insert(AggShipmentDaily).values(rows))

        return JobRunResult(
            rows_written=len(rows), rows_deleted=deleted, warnings=_cap(warnings)
        )

    @staticmethod
    def _add_duration(
        bucket: dict[str, Any],
        leg: str,
        started_at: datetime | None,
        ended_at: datetime | None,
        shipment_id: int,
        warnings: list[str],
    ) -> None:
        """Accumulate one leg's duration, or decline to.

        A leg only counts when BOTH timestamps exist, which is why each pair has
        its own denominator: ``n_ship_to_deliver`` is smaller than ``delivered``
        whenever a delivery was recorded without a ship time, and dividing the
        sum by ``shipments`` instead would quietly understate transit time.

        A negative duration means the timestamps contradict each other. It is
        dropped rather than accumulated — a negative summand silently pulls the
        average down and there is no way to notice — and named in a warning.
        """
        if started_at is None or ended_at is None:
            return
        seconds = int((_as_utc(ended_at) - _as_utc(started_at)).total_seconds())
        if seconds < 0:
            warnings.append(
                f"shipment_negative_duration: shipment {shipment_id} has a "
                f"{leg} leg of {seconds}s; its timestamps contradict each other "
                "and the leg was excluded from the duration sum"
            )
            return
        bucket[f"sum_{leg}"] += seconds
        bucket[f"n_{leg}"] += 1


def _empty_courier_bucket() -> dict[str, Any]:
    """A zeroed accumulator for one courier within one day."""
    return {
        "shipments": 0,
        "delivered": 0,
        "delivery_failed": 0,
        "rto_initiated": 0,
        "rto_delivered": 0,
        "cancelled": 0,
        "cost_minor": 0,
        "sum_order_to_ship": 0,
        "n_order_to_ship": 0,
        "sum_ship_to_deliver": 0,
        "n_ship_to_deliver": 0,
    }


register(FunnelDailyJob())
register(InventoryDailyJob())
register(ShipmentDailyJob())
