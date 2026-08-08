"""Immutable analytics FACT tables — the append-only base of the reporting stack.

Everything in this module is written once and never UPDATEd. Rollups
(`analytics_rollups.py`) are derived, truncatable and rebuildable; the rows
here are the raw grain those rollups are rebuilt *from*, so if a fact row is
wrong or missing there is nothing left to recompute from. That asymmetry is
the whole reason facts and rollups live in separate modules.

Read `analytics_base.py` first — BigInteger PKs, no foreign keys,
`DECIMAL(14,2)` money, `'-'` dimension sentinels and `tz_generation` are all
decided there and are not re-litigated per table.

What "fact" means here
----------------------
A fact row is a *statement about a moment that has already happened*. It
snapshots the dimensions it needs (SKU, product name, category) rather than
joining to them, because a live join makes today's catalog edits rewrite last
quarter's report. This is the single largest correctness gap in the current
schema: `order_items` freezes `unit_price` and `unit_cost` but nothing else,
so a product rename or re-categorisation silently and retroactively changes
every historical product/category report. `analytics_order_line` closes it.

Quality labels are strings, deliberately
----------------------------------------
`cost_quality`, `alloc_quality` and `AnalyticsOrderAdjustment.quality` store
the *values* of ``app.services.analytics.types.MetricQuality`` —
``AUTHORITATIVE`` / ``ACTUAL`` / ``ALLOCATED`` / ``ESTIMATED`` /
``INCOMPLETE``. They are plain varchars, and this module does NOT import that
enum: models must not depend on services, and a DB enum would need a migration
every time the vocabulary grows. The rule the enum encodes still binds —
a number that is unknown is labelled ``INCOMPLETE`` and reported with a
coverage percentage; it is never zero-filled into a fact row.

Event-type / adjustment-type / movement-type vocabularies follow the same
rule and the same house pattern as `payment_event.py`: constant classes, not
DB enums, so adding a value is a code change rather than a schema change.

Idempotency
-----------
Every table here is written by a backfill or an ingestion path that WILL be
re-run — after a crash, after a timezone change, after a bug fix. Each table
therefore carries exactly one natural key enforced by a UNIQUE index:

    analytics_order_line        -> order_item_id
    analytics_order_adjustment  -> event_key
    inventory_movements         -> event_key
    cart_events                 -> event_key

Writers use INSERT ... ON DUPLICATE KEY UPDATE / IGNORE against that key. A
fact table without one double-counts revenue on the second run, and does so
silently.

Capability mapping
------------------
Three of these tables are the physical backing for capabilities the analytics
registry already declares (`app.services.analytics.types.Capability`):
``ORDER_LINE_FACT``, ``INVENTORY_LEDGER`` and ``CART_EVENTS``. Views that
declare those capabilities stay gated until the corresponding table is both
created and populated — presence of the table alone is not data.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.analytics_base import (
    MONEY,
    DIMENSION_UNKNOWN,
    BigIDMixin,
    CreatedAtMixin,
    dimension_column,
    money_column,
)
from app.models.base import Base

__all__ = [
    "UNRESOLVED_IDENTITY",
    "IdentitySource",
    "AdjustmentType",
    "MovementType",
    "CartEventType",
    "AnalyticsOrderLine",
    "AnalyticsOrderAdjustment",
    "InventoryMovement",
    "CartEvent",
]


#: What a writer puts into a NOT NULL snapshot column (`sku_snapshot`,
#: `product_name_snapshot`) when the source identity is genuinely
#: unrecoverable — i.e. `identity_source = MISSING_LEGACY_IDENTITY`. Reuses the
#: shared dimension sentinel so "unknown" reads identically across the whole
#: analytics schema, and so grouping by SKU buckets the unknowns together
#: instead of scattering them under invented placeholder values.
UNRESOLVED_IDENTITY = DIMENSION_UNKNOWN


# ---------------------------------------------------------------------------
# Vocabularies. Plain string constants, never DB enums — see module docstring.
# Use these instead of bare strings in the service layer so a typo fails at
# import time rather than writing an unknown value nobody ever queries for.
# ---------------------------------------------------------------------------
class IdentitySource:
    """How the product identity on an order-line fact was obtained.

    This is provenance, not decoration. A report that mixes sale-time identity
    with today's-catalog identity is not wrong in a way anyone can see, which
    is exactly why the row has to say which one it is.
    """

    #: Snapshotted at the moment of sale by the live ingestion path. The only
    #: value that makes the row a true historical record.
    CAPTURED_AT_SALE = "CAPTURED_AT_SALE"
    #: Filled in by the one-time backfill from the product row as it exists
    #: TODAY. Correct for every SKU that was never renamed or recategorised,
    #: and quietly wrong for every SKU that was. Rows older than the ingestion
    #: cutover all carry this.
    BACKFILLED_CURRENT_CATALOG = "BACKFILLED_CURRENT_CATALOG"
    #: Neither available: no snapshot was taken and the catalog row can no
    #: longer be resolved. Snapshot columns hold `UNRESOLVED_IDENTITY`; the row
    #: still counts toward revenue totals but must be excluded from — or shown
    #: as an explicit "unattributed" bucket in — any per-product breakdown.
    MISSING_LEGACY_IDENTITY = "MISSING_LEGACY_IDENTITY"


class AdjustmentType:
    """Kinds of post-sale financial movement recorded against an order."""

    #: Part of an order's value returned to the customer.
    PARTIAL_REFUND = "PARTIAL_REFUND"
    #: The whole order value returned to the customer.
    FULL_REFUND = "FULL_REFUND"
    #: Money movement driven by a `returns` / `return_items` workflow, booked
    #: when the refund is actually issued rather than when it is requested.
    RETURN_ADJUSTMENT = "RETURN_ADJUSTMENT"
    #: Bank/gateway-initiated reversal. Distinct from a refund because the
    #: merchant did not choose it and usually eats a penalty fee too.
    CHARGEBACK = "CHARGEBACK"
    #: Correction once a real settlement figure replaces an estimated MDR.
    GATEWAY_FEE_CORRECTION = "GATEWAY_FEE_CORRECTION"
    #: Correction once the carrier's actual billed weight/cost replaces the
    #: quoted shipping figure snapshotted at checkout.
    SHIPPING_COST_CORRECTION = "SHIPPING_COST_CORRECTION"
    #: Reserved. No marketplace channel exists in this deployment today
    #: (`Capability.MARKETPLACE_CHANNEL`); defined so adding one later needs no
    #: migration.
    MARKETPLACE_COMMISSION_CORRECTION = "MARKETPLACE_COMMISSION_CORRECTION"


class MovementType:
    """Kinds of stock movement recorded in the inventory ledger."""

    #: Stock held for an in-flight checkout. RESERVED FOR A FUTURE
    #: RESERVATION MODEL — this codebase decrements stock outright at order
    #: creation and never emits this today.
    RESERVE = "RESERVE"
    #: Stock permanently consumed by a confirmed sale. What
    #: `ProductRepository.decrement_stock` emits.
    COMMIT_SALE = "COMMIT_SALE"
    #: A reservation expiring or being abandoned. RESERVED FOR A FUTURE
    #: RESERVATION MODEL — never emitted today, for the same reason as RESERVE.
    RELEASE_RESERVATION = "RELEASE_RESERVATION"
    #: Stock returned to sellable inventory — cancellation, payment failure, or
    #: an accepted return. What `ProductRepository.increment_stock` emits.
    RETURN_RESTOCK = "RETURN_RESTOCK"
    #: Returned goods that failed inspection and were NOT restocked. Recorded
    #: with `delta = 0` against sellable stock so shrinkage stays visible
    #: instead of vanishing into the gap between units sold and units on hand.
    DAMAGED_RETURN = "DAMAGED_RETURN"
    #: Manual correction via the admin product form (`ProductUpdate.stock`).
    #: Always carries `actor_user_id` — an unattributed manual adjustment is
    #: indistinguishable from a bug.
    ADMIN_ADJUSTMENT = "ADMIN_ADJUSTMENT"
    #: The opening balance written when a product first enters the ledger, so
    #: replaying the ledger reproduces current stock exactly.
    INITIAL_SEED = "INITIAL_SEED"


class CartEventType:
    """Funnel steps, ordered from top of funnel to conversion."""

    PRODUCT_VIEWED = "PRODUCT_VIEWED"
    CART_VIEWED = "CART_VIEWED"
    ITEM_ADDED = "ITEM_ADDED"
    ITEM_REMOVED = "ITEM_REMOVED"
    CHECKOUT_STARTED = "CHECKOUT_STARTED"
    SHIPPING_SUBMITTED = "SHIPPING_SUBMITTED"
    PAYMENT_INITIATED = "PAYMENT_INITIATED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    ORDER_PLACED = "ORDER_PLACED"


class AnalyticsOrderLine(Base, BigIDMixin, CreatedAtMixin):
    """Immutable per-order-line fact — one row per `order_items` row, forever.

    Why this table exists
    ---------------------
    `order_items` snapshots exactly two things: `unit_price` and `unit_cost`.
    Product name, SKU and category are resolved through a live join to
    `products` / `categories` on every report. That means:

    * renaming a product rewrites its entire sales history;
    * moving a product between categories moves every past sale with it, so a
      category revenue chart silently disagrees with itself month to month;
    * `Product.sold_count` and every "top SKU" list inherit the same defect.

    Nothing errors. The numbers just change, and the change is invisible
    because there is no prior copy to compare against. This table takes that
    copy at write time.

    It is also the grain every product, category and margin rollup is rebuilt
    from, which is why it is per-line rather than per-order: an order-level
    fact cannot answer "which SKU carried the margin" without re-joining, and
    re-joining is the problem.

    Allocation
    ----------
    Discount, tax, shipping, the COD surcharge, the instrument-specific
    payment discount and the gateway fee are all recorded on `orders` at the
    ORDER level; `order_items` has no share of them at all. The `alloc_*`
    columns hold this line's apportioned share, computed by a documented rule
    (value-weighted, with the rounding residue placed on the largest line so
    the parts always sum back to the order total). Because they are derived,
    they are labelled: `alloc_quality` is normally ``ALLOCATED``, never
    ``AUTHORITATIVE``.

    `alloc_gateway_fee` deserves its own warning. **No gateway fee is stored
    anywhere in this schema** — not on `orders`, not on `order_payments`, not
    on `payment_events`. Until a settlement feed exists
    (`Capability.GATEWAY_SETTLEMENT_API`), this column can only ever be an
    ``ESTIMATED`` figure from a configured MDR rate, and a real settled figure
    arriving later must be booked as a `GATEWAY_FEE_CORRECTION` adjustment
    rather than by mutating this row.

    `cost_quality` is separate from `alloc_quality` because it fails
    separately: `Product.cost` is nullable and `OrderItem.unit_cost` is
    nullable for every order placed before cost tracking existed. A line with
    no cost snapshot is ``INCOMPLETE`` for margin and must be excluded from
    margin numerators and denominators alike — never treated as zero cost,
    which would report 100% margin on the oldest orders.

    `brand_snapshot` is nullable because `products` has NO brand column today.
    It is reserved so a future brand field lands without a schema change; it is
    NOT an invitation to derive a brand from the product name. Until a brand
    column exists, brand-dimension views must report ``NOT_APPLICABLE``
    rather than invent one.

    `category_id_snapshot` / `category_name_snapshot` are nullable for a
    different and honest reason: `Product.category_id` is itself nullable with
    ``ON DELETE SET NULL``, so an uncategorised sale is a real state, not a
    gap. Note that categories are one level deep (`Category.parent_id`); this
    snapshot records the category the product was IN, not its parent, so
    parent-level rollups resolve the hierarchy at query time.

    Immutability
    ------------
    Never UPDATEd. Refunds, returns, chargebacks and fee corrections are
    appended to `analytics_order_adjustment` instead, so gross and net revenue
    stay separately answerable and "what did we think on the day" survives.
    """

    __tablename__ = "analytics_order_line"

    # Provenance back to the transactional row. Plain INTs, no FK — a rollup
    # rebuild must never be blocked by referential integrity, and a deleted
    # order must not erase the history of what it earned.
    order_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # The idempotency key. Exactly one fact row per order line, so the
    # backfill and the live ingestion path can both run, repeatedly, without
    # double-counting revenue. UNIQUE is doing real work here, not documenting
    # an intention.
    order_item_id: Mapped[int] = mapped_column(
        Integer, nullable=False, unique=True
    )

    # When the order was placed (`orders.created_at`), carried onto the line so
    # no report needs to join back to `orders` to bucket by time.
    ordered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Store-local reporting day derived from `ordered_at` — NOT a UTC day. An
    # order placed 23:40 IST belongs to that IST day, and getting this wrong
    # moves revenue between days at every month boundary.
    bucket_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # Which timezone generation produced `bucket_date`. Queries spanning two
    # generations must refuse rather than mix them. See analytics_base.
    tz_generation: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="1"
    )

    # ---- Snapshotted product identity -----------------------------------
    # `product_id` stays queryable for joins that legitimately want the CURRENT
    # catalog row (e.g. "is this still in stock"), but no report may resolve
    # display identity through it — that is what the *_snapshot columns are for.
    product_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # Matches `products.sku` (String(64), UNIQUE in the catalog). NOT NULL:
    # `UNRESOLVED_IDENTITY` when the identity could not be recovered.
    sku_snapshot: Mapped[str] = mapped_column(String(64), nullable=False)
    # Matches `products.name` (String(255)). The column a rename would
    # otherwise rewrite retroactively.
    product_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    # Nullable because `products.category_id` is nullable with ON DELETE SET
    # NULL — "no category" is a real, representable sale.
    category_id_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Matches `categories.name` (String(120)).
    category_name_snapshot: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    # RESERVED, always NULL today: `products` has no brand column. Views that
    # want a brand dimension must report NOT_APPLICABLE, never invent one by
    # parsing the product name.
    brand_snapshot: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # ---- Measures --------------------------------------------------------
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    # Verbatim from `order_items.unit_price`, widened to MONEY so every money
    # column on this row shares one precision and no arithmetic silently
    # truncates.
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    # Verbatim from `order_items.unit_cost`, kept at the transactional
    # Numeric(12,2) because it is a copy of a single value and never
    # accumulates here. NULL means no cost was ever snapshotted — see
    # `cost_quality`. NULL is not zero.
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    # quantity * unit_price, stored rather than derived so a line's gross value
    # cannot drift with a rounding-rule change made years later.
    extended_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)

    # ---- Order-level amounts apportioned to this line --------------------
    # All derived. 0 here means "the allocation ran and this line's share was
    # zero", never "we don't know" — unknown is expressed by `alloc_quality`.
    #: Share of `orders.discount_amount` (coupon-derived).
    alloc_discount: Mapped[Decimal] = money_column()
    #: Share of `orders.tax_amount`. The order snapshots only the total; per-line
    #: tax is not stored transactionally even though it was computed per line.
    alloc_tax: Mapped[Decimal] = money_column()
    #: Share of `orders.shipping_amount` — what the CUSTOMER was charged. Our
    #: actual carrier cost lives on `shipments.shipment_cost` and is a different
    #: number; reconciling the two is a SHIPPING_COST_CORRECTION adjustment.
    alloc_shipping: Mapped[Decimal] = money_column()
    #: Share of `orders.cod_surcharge_amount`. Zero for prepaid orders.
    alloc_cod_surcharge: Mapped[Decimal] = money_column()
    #: Share of `orders.payment_discount_amount` — the instrument incentive,
    #: kept separate from coupon discount so "promo" and "method subsidy" never
    #: get conflated in margin reporting.
    alloc_payment_discount: Mapped[Decimal] = money_column()
    #: Share of the gateway fee. ESTIMATED from an MDR rate today; no settled
    #: fee exists anywhere in this schema. See the class docstring.
    alloc_gateway_fee: Mapped[Decimal] = money_column()

    # ---- Honesty labels ---------------------------------------------------
    # MetricQuality values as plain strings. Longest is "AUTHORITATIVE" (13).
    #: Grade of `unit_cost`: AUTHORITATIVE when snapshotted at sale, ESTIMATED
    #: when taken from the product's current cost, INCOMPLETE when absent.
    cost_quality: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Grade of the `alloc_*` block as a whole — normally ALLOCATED, degrading
    #: to ESTIMATED or INCOMPLETE when an input (notably the gateway fee) is
    #: itself estimated or missing.
    alloc_quality: Mapped[str] = mapped_column(String(16), nullable=False)
    #: How the *_snapshot identity was obtained — an `IdentitySource` value.
    identity_source: Mapped[str] = mapped_column(String(32), nullable=False)

    # ---- Low-cardinality dimensions --------------------------------------
    # NOT NULL with the '-' sentinel: a NULL dimension escapes UNIQUE-key
    # semantics on MySQL and quietly splits its own bucket. Writers COALESCE.
    #: `orders.status` as of ingestion — pending/paid/shipped/delivered/
    #: cancelled/refunded. A snapshot, deliberately: order status keeps moving
    #: after the fact is written, so lifecycle questions belong to the rollup
    #: refresh, not to this row.
    order_status: Mapped[str] = dimension_column(20)
    #: `orders.payment_method` — prepaid / cod / split_cod.
    payment_method: Mapped[str] = dimension_column(20)

    # (order_id) as a standalone index is provided by `index=True` on the
    # column above; repeating it here would create a second identical index.
    __table_args__ = (
        # "revenue and units for product X over a date range" — the single
        # hottest analytics query shape.
        Index("ix_aol_bucket_product", "bucket_date", "product_id"),
        # Same shape, category grain. Kept separate rather than relying on the
        # product index because category rollups never filter by product.
        Index("ix_aol_bucket_category", "bucket_date", "category_id_snapshot"),
    )


class AnalyticsOrderAdjustment(Base, BigIDMixin, CreatedAtMixin):
    """Append-only ledger of post-sale money movements against an order.

    Why this table exists
    ---------------------
    Today the only trace of a refund is `orders.status = 'refunded'` plus
    `orders.refunded_at` and a free-text `refund_reason`. Reporting therefore
    has to *infer* net revenue from a status flag, which fails in every case
    that matters:

    * a partial refund has no representation at all — the order is either
      wholly refunded or not, so refunding one line of a three-line order
      either overstates the refund threefold or hides it entirely;
    * a second refund on the same order overwrites the first, because status is
      a single value and not a history;
    * a chargeback, a gateway fee correction and a customer refund are three
      economically different events that all collapse into "refunded";
    * the refund's own timestamp replaces the previous one, so "refunds issued
      in March" cannot be answered for an order refunded twice.

    Recording adjustments as events fixes all four at once. Gross revenue is
    the sum over `analytics_order_line`; net revenue is gross plus the sum of
    these signed amounts. Both stay answerable, independently, forever.

    Signed amounts
    --------------
    `amount` is SIGNED. Negative reduces revenue (refunds, chargebacks, fees);
    positive increases it (a fee reversal, an over-refund clawed back). There
    is no separate direction column, because two representations of "which way
    did the money go" is how a ledger ends up double-negating.

    Grain
    -----
    `order_line_id` is nullable on purpose. A return maps to a specific line
    and should carry one; a shipping cost correction or a chargeback is
    order-level and genuinely has no line. Forcing a line would mean inventing
    one, and a rollup that filters `order_line_id IS NOT NULL` gets exactly the
    line-attributable subset without guesswork.

    Idempotency
    -----------
    `event_key` is the natural key — a deterministic string built from the
    source (e.g. ``return:412:refund``, ``payment_event:99182:chargeback``) so
    replaying the same source row produces the same key and the second insert
    is a no-op rather than a duplicate refund.
    """

    __tablename__ = "analytics_order_adjustment"

    order_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # BigInteger to match `analytics_order_line.id`. Logical reference only —
    # no FK, so the order-line fact table stays independently rebuildable.
    # NULL = order-level adjustment with no line attribution (see docstring).
    order_line_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # When the money actually moved, NOT when we recorded it. A refund
    # discovered a week late still belongs to the day it was issued, otherwise
    # a closed reporting period silently reopens.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    bucket_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    tz_generation: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="1"
    )

    #: An `AdjustmentType` value. Plain varchar so new kinds of adjustment need
    #: no migration; indexed because "all chargebacks this quarter" is a first
    #: class question.
    adjustment_type: Mapped[str] = mapped_column(
        String(40), nullable=False, index=True
    )

    #: Signed. Negative reduces revenue. See the class docstring.
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    #: Matches `orders.currency`. Single-currency today, carried so a
    #: multi-currency future does not require restating history.
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="INR", server_default="INR"
    )
    #: MetricQuality value. AUTHORITATIVE for a refund read from `returns`,
    #: ESTIMATED for a modelled fee, INCOMPLETE when only a partial figure is
    #: known — which is reported as such, never rounded to a convenient number.
    quality: Mapped[str] = mapped_column(String(16), nullable=False)

    #: Where this came from — e.g. "return", "payment_event", "order",
    #: "manual". Paired with `source_ref_id`; both NULL for adjustments with no
    #: single originating row.
    source_ref_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: Idempotency key. Deterministic per source event so re-ingestion cannot
    #: book the same refund twice. NOT NULL — see analytics_base on why a
    #: nullable column can never be a reliable UNIQUE key on MySQL.
    event_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)

    #: Short human note (e.g. the admin's refund reason). NEVER store gateway
    #: signatures, tokens or card data here — same rule as `payment_events`.
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Originating payload kept for forensics and for re-deriving the row if
    #: the allocation rule changes. Same prohibition as `note` applies.
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class InventoryMovement(Base, BigIDMixin, CreatedAtMixin):
    """Append-only stock ledger — every change to `products.stock`, in order.

    Why this table exists
    ---------------------
    `products.stock` is a single mutable integer. It answers "how many do we
    have right now" and nothing else. It cannot answer:

    * what was stock on 3 March (needed for days-of-cover, sell-through, and
      any stockout analysis worth the name);
    * why stock dropped 40 units overnight — sales, a return that never got
      restocked, or an admin typo;
    * how long a SKU was actually at zero, which is the number that turns
      "we sold 12" into "we would have sold 30".

    An integer that is overwritten has no history to reconstruct. This ledger
    is the history, and current stock becomes a checkable consequence of it:
    ``sum(delta)`` for a product must equal `products.stock`. A drift between
    the two is a real bug in a write path, and it is only detectable because
    both numbers exist.

    Where the writes go — this is the whole reason it is feasible
    -------------------------------------------------------------
    Stock mutation in this codebase is already centralised at exactly three
    choke points, and there are no others:

      1. ``ProductRepository.decrement_stock``  (product_repository.py:100)
      2. ``ProductRepository.increment_stock``  (product_repository.py:115)
      3. Admin edits through ``ProductUpdate.stock``

    The ledger write goes INSIDE those three, in the SAME transaction as the
    stock UPDATE. That is non-negotiable: a ledger written in a separate
    transaction, or by a listener, or after the fact, will disagree with
    `products.stock` the first time anything rolls back — and a stock ledger
    that disagrees with stock is worse than no ledger, because it is trusted.

    Both repository methods use atomic expression-based UPDATEs
    (``stock = stock ± qty``) specifically to avoid read-modify-write races
    under concurrency. The ledger must not undo that: it records the `delta`,
    which is always known, and leaves `stock_after` NULL unless the resulting
    value was genuinely observed (e.g. via a RETURNING clause or an explicit
    admin set). Re-reading stock to populate `stock_after` would reintroduce
    exactly the race those methods were written to eliminate.

    Reservations
    ------------
    ``RESERVE`` and ``RELEASE_RESERVATION`` are defined but NEVER emitted
    today. This codebase decrements stock outright at order creation and has no
    reservation model, so there is nothing to reserve or release. They exist so
    that adding reservations later is a code change, not a migration plus a
    backfill of a vocabulary column.

    Backfill limits
    ---------------
    History before this table starts is unrecoverable — the overwritten values
    are simply gone. The honest bootstrap is one ``INITIAL_SEED`` row per
    product carrying current stock, with everything before it reported as
    absent rather than reconstructed from order history (which would omit every
    admin correction and every unrestocked return, and look plausible while
    doing so).
    """

    __tablename__ = "inventory_movements"

    product_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # When the stock actually moved. Indexed because nearly every question here
    # is "as of date D" or "between D1 and D2".
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    #: A `MovementType` value. Indexed: separating sales from admin corrections
    #: is the first thing anyone does with this table.
    movement_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    #: Signed change in sellable units. Negative for a sale, positive for a
    #: restock, and 0 for a movement that is real but does not change sellable
    #: stock (a DAMAGED_RETURN that was written off rather than restocked).
    delta: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Resulting stock level, ONLY when it was genuinely observed. NULL means
    #: "not observable at write time" — never a guess, and never a re-read (see
    #: the class docstring on why re-reading reintroduces the oversell race).
    #: A point-in-time level is reconstructed by summing `delta`, which is
    #: always correct; a fabricated `stock_after` would look authoritative and
    #: be wrong.
    stock_after: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: What caused the movement — "order", "return", "admin", "seed" — paired
    #: with `ref_id`. This is what turns "stock went down 40" into an answer.
    ref_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: Who did it, for manual movements. NULL for system-driven ones. An
    #: ADMIN_ADJUSTMENT without an actor is indistinguishable from a bug, which
    #: is the entire reason this column exists.
    actor_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: Idempotency key, deterministic per source event (e.g.
    #: ``order:8812:item:3:commit``). Retried checkouts and replayed webhooks
    #: must not be able to write the same movement twice — a duplicated
    #: `delta` corrupts every stock figure derived from this table.
    event_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)

    #: Short human note, e.g. the admin's reason for a manual correction.
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        # "the full movement history for product X, in order" and "stock as of
        # date D" are the same index. Also the natural scan for the
        # sum(delta) == products.stock reconciliation check.
        Index("ix_invmov_product_occurred", "product_id", "occurred_at"),
    )


class CartEvent(Base, BigIDMixin, CreatedAtMixin):
    """Append-only funnel events — the pre-purchase half of the business.

    Why this table exists
    ---------------------
    The cart lives ONLY in Redis (`app/services/cart_service.py`: a
    ``cart:{user_id}`` hash of product_id -> quantity, plus a coupon key).
    Nothing about it is ever written to the database. The consequences are
    absolute, not merely inconvenient:

    * an abandoned cart leaves NO database trace whatsoever — the key is
      overwritten or expires and the intent is gone;
    * cart abandonment rate, checkout drop-off, add-to-cart rate and
      view-to-cart conversion are therefore not "hard to compute", they are
      not computable at all;
    * and none of it can be BACKFILLED. Unlike the identity snapshots in
      `analytics_order_line` — where a degraded, honestly-labelled backfill
      from the current catalog is possible — there is no historical source to
      backfill from. History starts the day instrumentation is switched on,
      and every funnel view must say so rather than showing a partial series
      as if it were complete.

    Orders record only successes. This table is where the failures live, and
    the failures are where the money is.

    Sessions
    --------
    `session_key` is NOT NULL and is the primary funnel identity, because the
    top of the funnel is anonymous: a logged-out visitor browsing products has
    no user id. Note that the Redis cart is keyed by `user_id` alone, so it
    cannot supply this — the writer must mint and carry a session key (cookie
    or client-generated id) of its own. `user_id` is nullable and is populated
    once the visitor authenticates, which is what lets a single session be
    stitched across the login boundary instead of being counted as two.

    `value` is nullable for a genuine reason: a PRODUCT_VIEWED has no monetary
    value, and writing 0 would drag every average-cart-value calculation
    toward zero. NULL means "not applicable", not "zero".

    `order_id` is populated on ORDER_PLACED (and on PAYMENT_FAILED where a
    pending order exists), which is what closes the loop from funnel event to
    `analytics_order_line` without a join through Redis.

    Volume
    ------
    This is the highest-volume table in the analytics schema by a wide margin —
    PRODUCT_VIEWED alone outnumbers orders by orders of magnitude. BigInteger
    PK is not optional here, and a retention/aggregation policy for raw rows
    should exist before the table gets large, not after.
    """

    __tablename__ = "cart_events"

    # When the visitor did it — client-supplied or server-stamped at receipt,
    # but always the event time, never the ingestion time.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    #: Anonymous-safe funnel identity. NOT NULL — an event that cannot be
    #: attributed to a session cannot participate in a funnel at all, since
    #: every funnel metric is a ratio of sessions that reached step N.
    session_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: Set once the visitor is authenticated; NULL for anonymous traffic, which
    #: is most of the top of the funnel.
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    #: A `CartEventType` value. Plain varchar so a new funnel step needs no
    #: migration.
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    #: The product involved. NULL for events with no single product
    #: (CART_VIEWED, CHECKOUT_STARTED, SHIPPING_SUBMITTED, PAYMENT_*).
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Units involved in an ITEM_ADDED / ITEM_REMOVED. NULL elsewhere.
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Monetary value at the moment of the event — line value for an item
    #: event, cart total for a cart/checkout event. NULL where value does not
    #: apply. NULL is not zero: see the class docstring.
    value: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="INR", server_default="INR"
    )

    #: Present on ORDER_PLACED (and PAYMENT_FAILED against a pending order).
    #: Links the funnel to `analytics_order_line` without a join through Redis.
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    #: Idempotency key. Browser retries, double-clicks and at-least-once
    #: delivery all replay events; without this, one add-to-cart becomes three
    #: and the funnel reports a conversion rate below reality.
    event_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)

    #: Free-form context — referrer, device class, experiment arm, coupon shown.
    #: Named `meta`, NOT `metadata`: `metadata` is reserved on SQLAlchemy
    #: declarative classes (it is the `MetaData` registry) and defining it here
    #: raises InvalidRequestError at class-definition time. The DB column is
    #: `meta` too, so raw SQL and the ORM agree.
    meta: Mapped[dict | None] = mapped_column("meta", JSON, nullable=True)

    __table_args__ = (
        # Sessionised funnel reconstruction: every step one visitor took, in
        # order. This is THE access pattern for drop-off analysis.
        Index("ix_cart_events_session_occurred", "session_key", "occurred_at"),
        # Step-level counts over a date range, without touching sessions.
        Index("ix_cart_events_type_occurred", "event_type", "occurred_at"),
    )
