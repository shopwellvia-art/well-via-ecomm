"""The writer for `analytics_order_line` — forward capture and backfill.

`analytics_order_line` has existed, been read by `jobs_basket.BasketPairDailyJob`
and been documented as the base grain of the reporting stack since the analytics
v2 schema landed. Nothing ever wrote to it. Production carries 0 rows, which
means view 57 (Product Bundling and Cross-Sell) is permanently empty and every
per-product report still resolves display identity through a live join to
`products` — the exact failure the table was created to close. This module is
the missing half.

Two entry points, one row builder
---------------------------------
``capture_order_lines``  runs at checkout, in the order's own transaction, and
                         stamps ``IdentitySource.CAPTURED_AT_SALE``.
``build_fact_rows``      turns one order + its lines into insertable dicts. Both
                         the capture and ``scripts/backfill_order_lines.py`` go
                         through it, so a backfilled row and a captured row
                         differ in exactly one field — their provenance — rather
                         than in whatever the second implementation happened to
                         do differently.

The same-transaction / never-fail-checkout trade-off
----------------------------------------------------
These two requirements genuinely conflict, and the resolution is a SAVEPOINT
rather than a preference for one over the other:

* The fact write runs inside the caller's transaction, so if the order rolls
  back the facts roll back with it. There is no window in which a fact row
  describes an order that does not exist, and none in which the facts are
  committed by a second connection that cannot see the uncommitted order.
* The fact write is wrapped in ``db.begin_nested()``. If it raises — a bad
  migration, a column that does not exist yet, a deadlock on the analytics
  table — only the savepoint is rolled back. The order, its items, its payment
  legs and its stock decrement are untouched, the caller's ``commit()`` still
  succeeds, and the customer's checkout completes.

So the invariant is asymmetric on purpose, and the asymmetry is the correct one:
**an order can exist without its facts, but facts can never exist without their
order.** The first is recoverable (the backfill finds the gap by definition —
its whole predicate is "order lines with no fact row"), the second is not, since
a fact row pointing at a rolled-back `order_items.id` would be attributed to
whatever row later reuses that id.

The failure is never silent. It is logged at ERROR with the order id and the
exception, and it leaves behind precisely the shape the backfill sweeps for. A
best-effort write that swallowed its own failure with no log and no detectable
trace would be worse than not writing at all, because the empty table would look
like a quiet day rather than a broken writer.

Cost is copied, never reconstructed
-----------------------------------
`unit_cost` is copied verbatim from `order_items.unit_cost` and nothing else.
Present -> ``AUTHORITATIVE`` (it was snapshotted at the moment of sale). Absent
-> the column stays NULL and `cost_quality` is ``INCOMPLETE``. It is never 0:
`ProfitService`'s ``coalesce(unit_cost, 0)`` is exactly why every legacy line
currently reports 100% margin, and this table exists partly to stop repeating it.

Note what is deliberately NOT implemented: the model docstring allows a
``ESTIMATED`` grade for a cost taken from the product's *current* `cost`. Doing
that would import today's number into a historical fact — the same class of
retroactive rewrite the identity snapshots exist to prevent, applied to money
instead of names. A missing cost is reported as missing.

Quality labels come from the allocator
--------------------------------------
`alloc_quality` is whatever ``allocation.allocate_order`` graded the line
(``ALLOCATED``), copied rather than re-derived here. The allocator owns the
definition of how good an allocated number is; a second opinion in this module
would drift from it. `alloc_gateway_fee` is 0 with no fee passed in, which is
the allocator's documented "no fee known" behaviour — no settlement feed exists
in this schema, and a settled figure arriving later is booked as a
``GATEWAY_FEE_CORRECTION`` adjustment, never by mutating this row.

Brand
-----
`brand_snapshot` is read with ``getattr(product, "brand", None)``. `products`
has no `brand` column today; one is being added concurrently. The defensive read
means this module is correct before, during and after that migration lands, and
needs no change when it does — a missing attribute and a NULL column both mean
"no brand", which is what the column already stores.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.models.analytics_control import AnalyticsTzGeneration
from app.models.analytics_facts import (
    UNRESOLVED_IDENTITY,
    AnalyticsOrderLine,
    IdentitySource,
)
from app.services.analytics.allocation import allocate_order
from app.services.analytics.contracts import from_minor
from app.services.analytics.timebox import local_day, store_timezone
from app.services.analytics.types import MetricQuality

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.order import Order, OrderItem

__all__ = [
    "DIMENSION_UNKNOWN",
    "build_fact_rows",
    "capture_order_lines",
    "existing_order_item_ids",
    "write_fact_rows",
]

logger = logging.getLogger(__name__)

#: `'-'` sentinel for the NOT NULL low-cardinality dimensions. Re-exported from
#: the model layer's convention rather than re-spelled, so "unknown" reads
#: identically across the analytics schema.
DIMENSION_UNKNOWN = "-"

#: Column widths from `AnalyticsOrderLine`. Truncating here rather than letting
#: MySQL do it means a wide value is shortened by a rule we chose, not silently
#: by strict-mode being off on one deployment and on in another.
_SKU_MAX = 64
_NAME_MAX = 255
_CATEGORY_NAME_MAX = 120
_BRAND_MAX = 120
_STATUS_MAX = 20
_METHOD_MAX = 20

#: How many `order_item_id` values one existence probe carries. Bounded so a
#: very large backfill page cannot build a single enormous `IN` list.
_EXISTS_CHUNK = 500


# ---------------------------------------------------------------------------
# Small readers. None of these write, and specifically none of them commit.
# ---------------------------------------------------------------------------


def _tz_generation(db: Session) -> int:
    """The active `tz_generation`, read without ever writing one.

    ``timebox.active_generation`` seeds generation 1 when none exists **and
    commits** to do it. That is right for a job runner and catastrophic here:
    called from inside a checkout it would commit the half-built order — before
    the payment provider has been asked for a redirect URL, and past the
    ``db.rollback()`` the checkout paths rely on when the provider fails.

    So this reads only. A missing generation row falls back to 1, which is both
    the value ``active_generation`` would have seeded and the column's own
    ``server_default``, so the first real generation row cannot disagree with
    rows written just before it.
    """
    generation = db.execute(
        select(AnalyticsTzGeneration.generation)
        .where(AnalyticsTzGeneration.status == "active")
        .order_by(AnalyticsTzGeneration.generation.desc())
    ).scalars().first()
    if generation is None:
        logger.warning(
            "analytics_order_line: no active tz_generation row; writing "
            "generation 1 (the value active_generation() would seed)"
        )
        return 1
    return int(generation)


def existing_order_item_ids(db: Session, order_item_ids: Sequence[int]) -> set[int]:
    """Which of these order lines already have a fact row.

    The idempotency check. UNIQUE on `order_item_id` is what actually enforces
    it, but probing first is what lets both writers report an honest "wrote
    nothing" instead of inferring it from MySQL's affected-rows, which returns
    the same 0 for "duplicate ignored" and "row unchanged".
    """
    found: set[int] = set()
    ids = [int(i) for i in order_item_ids]
    for start in range(0, len(ids), _EXISTS_CHUNK):
        chunk = ids[start : start + _EXISTS_CHUNK]
        found.update(
            int(row)
            for row in db.execute(
                select(AnalyticsOrderLine.order_item_id).where(
                    AnalyticsOrderLine.order_item_id.in_(chunk)
                )
            ).scalars()
        )
    return found


# ---------------------------------------------------------------------------
# Identity snapshot
# ---------------------------------------------------------------------------


def _clip(value: Any, limit: int) -> str | None:
    """Stringify and truncate, mapping blank to None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def _identity_snapshot(product: Any) -> dict[str, Any]:
    """Copy the product's display identity out as plain strings.

    Deliberately values, never references: no FK, no join at read time, no
    `product_id`-driven lookup on the reporting path. That is the whole point of
    the table — the copy is what a later rename or re-SKU cannot reach.

    `brand` is read with ``getattr`` because `products` has no brand column
    today and one is landing concurrently; see the module docstring.

    Returns ``resolved: False`` when there is no product row at all, which the
    caller turns into ``MISSING_LEGACY_IDENTITY`` rather than inventing values.
    """
    if product is None:
        return {
            "resolved": False,
            "sku_snapshot": UNRESOLVED_IDENTITY,
            "product_name_snapshot": UNRESOLVED_IDENTITY,
            "category_id_snapshot": None,
            "category_name_snapshot": None,
            "brand_snapshot": None,
        }

    category = getattr(product, "category", None)
    category_id = getattr(product, "category_id", None)
    return {
        "resolved": True,
        # NOT NULL columns: a product with a blank name or SKU is a data defect,
        # but it must not become a NULL that escapes grouping — bucket it with
        # the other unknowns instead.
        "sku_snapshot": _clip(getattr(product, "sku", None), _SKU_MAX)
        or UNRESOLVED_IDENTITY,
        "product_name_snapshot": _clip(getattr(product, "name", None), _NAME_MAX)
        or UNRESOLVED_IDENTITY,
        # Nullable for a real reason: `products.category_id` is nullable with
        # ON DELETE SET NULL, so an uncategorised sale is a representable state.
        "category_id_snapshot": int(category_id) if category_id is not None else None,
        "category_name_snapshot": _clip(
            getattr(category, "name", None), _CATEGORY_NAME_MAX
        ),
        # Works whether or not `products.brand` has landed. Never derived from
        # the product name — no brand is NULL, not a guess.
        "brand_snapshot": _clip(getattr(product, "brand", None), _BRAND_MAX),
    }


def _cost_of(item: Any) -> tuple[Decimal | None, str]:
    """`unit_cost` verbatim plus its honest grade. NULL is never 0."""
    unit_cost = getattr(item, "unit_cost", None)
    if unit_cost is None:
        # MISSING. `MetricQuality` has no MISSING member — INCOMPLETE is its
        # name for "the input is absent", and the column stays NULL so no
        # downstream SUM can mistake an unknown cost for a free product.
        return None, MetricQuality.INCOMPLETE.value
    return Decimal(unit_cost), MetricQuality.AUTHORITATIVE.value


def _ordered_at(order: Any) -> datetime:
    """`orders.created_at`, the one clock every reader already buckets on.

    Falls back to now only when the row has not been flushed — never to a
    locally-invented timestamp for a persisted order, because the backfill reads
    `orders.created_at` and the two writers must agree to the microsecond or the
    same order lands in two buckets depending on who wrote it.
    """
    created = getattr(order, "created_at", None)
    if created is None:
        return datetime.now(timezone.utc)
    if created.tzinfo is None:
        # MySQL DATETIME carries no zone; the whole stack stores UTC and
        # `timebox` makes the same assumption on read.
        return created.replace(tzinfo=timezone.utc)
    return created


# ---------------------------------------------------------------------------
# Row builder — shared by the live capture and the backfill
# ---------------------------------------------------------------------------


def build_fact_rows(
    db: Session,
    order: "Order",
    items: "Iterable[OrderItem] | None" = None,
    *,
    identity_source: str = IdentitySource.CAPTURED_AT_SALE,
    tz_generation: int | None = None,
    only_order_item_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """One insertable dict per order line.

    ``only_order_item_ids`` filters what is *returned*, never what is
    *allocated*. The allocation always runs over the whole order, because a
    line's share of the order's tax is a function of every other line; computing
    it over a subset would make a partially-backfilled order's shares fail to
    sum back to the order totals. So the allocator sees all lines and the caller
    keeps the ones it still needs to write.
    """
    lines = list(items if items is not None else order.items)
    if not lines:
        return []

    generation = tz_generation if tz_generation is not None else _tz_generation(db)
    ordered_at = _ordered_at(order)
    bucket_date = local_day(ordered_at, store_timezone(db))

    # The allocator is the single source of every per-line money share. Never
    # ad-hoc arithmetic here: `sum(alloc_x) == orders.x` exactly is invariant I1,
    # and it is a property of the largest-remainder integer allocator, not of
    # anything this module could reimplement.
    result = allocate_order(order, lines)
    if result.warnings:
        logger.info(
            "analytics_order_line: order #%s allocation warnings: %s",
            getattr(order, "id", None),
            "; ".join(result.warnings),
        )
    by_item = {line.order_item_id: line for line in result.lines}

    status = getattr(order, "status", None)
    status_value = getattr(status, "value", status)
    order_status = _clip(status_value, _STATUS_MAX) or DIMENSION_UNKNOWN
    payment_method = (
        _clip(getattr(order, "payment_method", None), _METHOD_MAX) or DIMENSION_UNKNOWN
    )
    order_id = int(order.id)

    rows: list[dict[str, Any]] = []
    for item in lines:
        item_id = int(item.id)
        if only_order_item_ids is not None and item_id not in only_order_item_ids:
            continue
        alloc = by_item.get(item_id)
        if alloc is None:  # pragma: no cover - allocate_order covers every line
            continue

        identity = _identity_snapshot(getattr(item, "product", None))
        unit_cost, cost_quality = _cost_of(item)
        rows.append(
            {
                "order_id": order_id,
                "order_item_id": item_id,
                "ordered_at": ordered_at,
                "bucket_date": bucket_date,
                "tz_generation": generation,
                "product_id": int(item.product_id),
                "sku_snapshot": identity["sku_snapshot"],
                "product_name_snapshot": identity["product_name_snapshot"],
                "category_id_snapshot": identity["category_id_snapshot"],
                "category_name_snapshot": identity["category_name_snapshot"],
                "brand_snapshot": identity["brand_snapshot"],
                "quantity": int(item.quantity),
                "unit_price": Decimal(item.unit_price),
                "unit_cost": unit_cost,
                "extended_price": from_minor(alloc.extended_price),
                "alloc_discount": from_minor(alloc.discount),
                "alloc_tax": from_minor(alloc.tax),
                "alloc_shipping": from_minor(alloc.shipping),
                "alloc_cod_surcharge": from_minor(alloc.cod_surcharge),
                "alloc_payment_discount": from_minor(alloc.payment_discount),
                "alloc_gateway_fee": from_minor(alloc.gateway_fee),
                "cost_quality": cost_quality,
                # The allocator's own grade, carried not re-derived.
                "alloc_quality": alloc.quality.value,
                # An unresolvable product downgrades provenance for that line
                # only; the row still counts toward revenue, which is why the
                # snapshots hold the shared '-' sentinel rather than NULL.
                "identity_source": (
                    identity_source
                    if identity["resolved"]
                    else IdentitySource.MISSING_LEGACY_IDENTITY
                ),
                "order_status": order_status,
                "payment_method": payment_method,
            }
        )
    return rows


def write_fact_rows(db: Session, rows: Sequence[dict[str, Any]]) -> int:
    """Insert fact rows, ignoring any whose `order_item_id` is already present.

    Core INSERT, not ORM objects, on purpose: nothing is added to the Session's
    identity map, so a savepoint rollback around this call has no ORM state to
    restore and cannot disturb the order the caller is still holding.

    ``ON DUPLICATE KEY UPDATE order_item_id = order_item_id`` is a deliberate
    no-op — it makes a duplicate a silent skip rather than an error, without the
    blanket error-swallowing of ``INSERT IGNORE`` (which would also hide a
    truncation or a bad value). Callers that need an accurate count pre-filter
    with ``existing_order_item_ids``; this clause is the race guard behind that,
    for two writers hitting the same order at once.
    """
    if not rows:
        return 0
    statement = mysql_insert(AnalyticsOrderLine).values(list(rows))
    db.execute(
        statement.on_duplicate_key_update(
            order_item_id=statement.inserted.order_item_id
        )
    )
    return len(rows)


# ---------------------------------------------------------------------------
# Forward capture — the checkout path
# ---------------------------------------------------------------------------


def capture_order_lines(db: Session, order: "Order") -> int:
    """Snapshot one order's lines into `analytics_order_line`. Never raises.

    Call this after the order and its items have ids and before the caller's
    ``commit()``. On success the facts commit atomically with the order. On
    failure the savepoint is discarded, an ERROR is logged, and the checkout
    proceeds — see the module docstring for why that asymmetry is the right one
    and how the gap is recovered.

    Returns the number of fact rows written (0 on failure, and 0 when the
    order's facts already exist).
    """
    order_id = getattr(order, "id", None)
    try:
        # Settle the caller's own pending work BEFORE the savepoint opens.
        # `begin_nested()` flushes as it takes its snapshot, so an unflushed
        # `order.order_number` would otherwise be written inside the savepoint
        # and undone by a rollback to it — an analytics failure would then
        # commit an order with a NULL order number.
        db.flush()

        with db.begin_nested():
            lines = list(order.items)
            if not lines:
                return 0
            already = existing_order_item_ids(db, [int(i.id) for i in lines])
            pending = {int(i.id) for i in lines} - already
            if not pending:
                return 0
            rows = build_fact_rows(
                db,
                order,
                lines,
                identity_source=IdentitySource.CAPTURED_AT_SALE,
                only_order_item_ids=pending,
            )
            written = write_fact_rows(db, rows)
        logger.debug(
            "analytics_order_line: captured %s line fact(s) for order #%s",
            written, order_id,
        )
        return written
    except Exception as exc:  # noqa: BLE001 - analytics must never fail a sale
        # Visible, not silent. The order id is here so the gap is findable by
        # hand, and `scripts/backfill_order_lines.py` finds it without being
        # told — its predicate is exactly "order lines with no fact row".
        logger.error(
            "analytics_order_line: capture FAILED for order #%s: %s — the order "
            "is unaffected and will be picked up by "
            "scripts/backfill_order_lines.py (which will flag those rows "
            "BACKFILLED_CURRENT_CATALOG, not CAPTURED_AT_SALE)",
            order_id, exc, exc_info=True,
        )
        return 0
