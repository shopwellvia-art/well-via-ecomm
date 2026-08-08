"""Analytics ROLLUP (aggregate) tables — the read side of the analytics schema.

Facts are narrow and immutable; these tables are the pre-aggregated answers the
dashboard and reporting APIs actually read. Every table here is *derived* and
therefore disposable: any row can be deleted and recomputed from the facts and
the transactional tables without losing information. That property is the whole
point, and it is what dictates the conventions below.

Shared rules (see `analytics_base` for the full reasoning — this is the short
version, and none of it is negotiable):

* **Sums and counts, never stored averages.** `sum_delivery_seconds` +
  `n_delivery`, never `avg_delivery_days`. The moment a daily bucket is
  re-bucketed to a week or a month, an average of averages is wrong, and it is
  wrong *silently* — nothing raises, the number is just quietly incorrect.
  Divide at query time.

* **Never store a derivable percentage.** No `cod_share_pct`, no
  `delivery_success_rate`, no `cogs_coverage_pct`. Store the numerator and the
  denominator (`cod_orders` + `orders`, `delivered` + `shipments`,
  `costed_units` + `units`) and let the API compute the ratio. A stored
  percentage cannot be re-aggregated and cannot be audited.

* **Every dimension that participates in a UNIQUE key is `dimension_column()`**
  — NOT NULL with a `'-'` sentinel. MySQL permits unlimited NULLs under a
  UNIQUE index, so one nullable `gateway` column turns the idempotency key into
  a suggestion and the next job run double-counts the day. Writers must
  COALESCE at write time.

* **No ForeignKey constraints anywhere.** Rollups must be independently
  TRUNCATE-able and rebuildable. An FK to `products` would turn "recompute
  2026-03" into a referential-integrity problem and would block product
  deletion. Entity ids are plain integers and identity is snapshotted
  (`sku_snapshot`, `category_id_snapshot`) so history survives a delete.

* **Every table carries an explicit `UniqueConstraint` including
  `tz_generation`.** That constraint *is* the idempotency key — the aggregation
  jobs upsert against it. `tz_generation` must be in it because changing the
  store's reporting timezone re-buckets every day; rows from two generations
  must be able to coexist rather than collide and overwrite each other.

Revenue definition: the canonical rule lives in
`app/services/dashboard_service.py` as `_REVENUE_STATUSES = (PAID, SHIPPED,
DELIVERED)`. Pending has not paid; cancelled and refunded gave the money back.
Every `net_revenue` / `revenue` / `order_revenue` column in this module is
computed over that status set unless its own docstring says otherwise. Do not
introduce a second definition here.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.analytics_base import (
    MONEY,
    BigIDMixin,
    RollupMixin,
    count_column,
    dimension_column,
    money_column,
    seconds_column,
)
from app.models.base import Base

__all__ = [
    "AggOrderDaily",
    "AggOrderHourly",
    "AggProductDaily",
    "AggCustomerDaily",
    "AggCustomerSnapshot",
    "AggCustomerCohortMonthly",
    "AggPaymentDaily",
    "AggShipmentDaily",
    "AggGeoDaily",
    "AggPromoDaily",
    "AggFunnelDaily",
    "AggInventoryDaily",
]


class AggOrderDaily(Base, BigIDMixin, RollupMixin):
    """One row per store-local reporting day: the top-line revenue table.

    Answers: how many orders, in what states, worth how much, from how many
    customers, on a given day — and every trend/comparison card on the admin
    dashboard is a range scan over this table rather than a scan over `orders`.

    **The revenue bridge.** These columns are designed so the money reconciles
    exactly, and a reconciliation test asserts it::

        gross_merchandise_sales
          - discount_sum
          + tax_sum
          + shipping_income
          + cod_surcharge_sum
          - refund_sum
          = net_revenue

    Keep that identity true. If a new money component is ever added to orders
    (a fee, a levy, a credit), it must be added to this table *and* to both
    sides of that equation in the same change, or the test fails — which is the
    intended behaviour, not an inconvenience.

    The other money columns sit outside the bridge deliberately:
    `order_value_created` counts every order placed that day regardless of
    status (a demand measure, not a revenue measure), `paid_order_value` counts
    only orders that reached a revenue status, `subtotal_sum` and
    `payment_discount_sum` are inputs kept for drill-down, and `cogs_sum` /
    `costed_units` support margin.

    **Margin is deliberately not stored as a percentage.** `cogs_sum` and
    `costed_units` are stored next to `net_merchandise_sales` and `units` so
    the API can report both the margin *and* its cost coverage
    (`costed_units / units`) — a 60% margin computed over 30% costed units is a
    lie, and the only way to catch that is to keep the denominator.

    Deliberately not here: anything per-product (see `agg_product_daily`), per-
    customer (see `agg_customer_snapshot`), or per-gateway (see
    `agg_payment_daily`). This table is one row a day and must stay that way.
    """

    __tablename__ = "agg_order_daily"

    # ---- Order counts by terminal/interim status -------------------------
    # Counted on the order's reporting day. An order that moves pending -> paid
    # tomorrow decrements nothing today; the day is recomputed instead.
    orders_total: Mapped[int] = count_column()
    orders_pending: Mapped[int] = count_column()
    orders_paid: Mapped[int] = count_column()
    orders_shipped: Mapped[int] = count_column()
    orders_delivered: Mapped[int] = count_column()
    orders_cancelled: Mapped[int] = count_column()
    orders_refunded: Mapped[int] = count_column()

    # ---- Demand (all statuses) -------------------------------------------
    #: Value of every order created today, whatever happened to it afterwards.
    #: A demand signal — never quote this as revenue.
    order_value_created: Mapped[Decimal] = money_column()
    #: Value of today's orders that reached PAID/SHIPPED/DELIVERED.
    paid_order_value: Mapped[Decimal] = money_column()

    # ---- The revenue bridge (see class docstring) ------------------------
    gross_merchandise_sales: Mapped[Decimal] = money_column()
    net_merchandise_sales: Mapped[Decimal] = money_column()
    net_revenue: Mapped[Decimal] = money_column()

    # ---- Bridge components + drill-down inputs ---------------------------
    subtotal_sum: Mapped[Decimal] = money_column()
    tax_sum: Mapped[Decimal] = money_column()
    discount_sum: Mapped[Decimal] = money_column()
    #: Gateway/instrument-level discounts, already included in `discount_sum`.
    #: Split out so promo attribution can separate coupon from payment offers.
    payment_discount_sum: Mapped[Decimal] = money_column()
    shipping_income: Mapped[Decimal] = money_column()
    cod_surcharge_sum: Mapped[Decimal] = money_column()
    refund_sum: Mapped[Decimal] = money_column()
    cogs_sum: Mapped[Decimal] = money_column()

    # ---- Volume + margin coverage ----------------------------------------
    units: Mapped[int] = count_column()
    #: Units whose line had a known unit_cost. Denominator for cost coverage —
    #: `costed_units / units`. Never store that ratio.
    costed_units: Mapped[int] = count_column()

    # ---- Customers --------------------------------------------------------
    #: Distinct customers who ordered today. NOT additive across days — summing
    #: it over a week overcounts anyone who ordered twice. Weekly/monthly
    #: distinct counts must be recomputed from the facts.
    distinct_customers: Mapped[int] = count_column()
    new_customers: Mapped[int] = count_column()
    returning_customers: Mapped[int] = count_column()

    __table_args__ = (
        UniqueConstraint("bucket_date", "tz_generation", name="uq_agg_order_daily_key"),
    )


class AggOrderHourly(Base, BigIDMixin, RollupMixin):
    """Hour-of-day order volume, for "when do people buy" and incident triage.

    One row per (reporting day, hour 0-23). `bucket_hour` is the hour in the
    store's local timezone, which is why `tz_generation` matters even more here
    than on the daily tables — a timezone change shifts every hour bucket.

    **Pruned after 90 days.** This table is 24x the row count of
    `agg_order_daily` and its only questions are "what does a normal Tuesday
    look like" and "when exactly did checkout break", neither of which needs a
    two-year history. The retention job drops buckets older than 90 days; the
    daily table remains the long-term record.

    Deliberately minimal: three measures, no dimensions. Anything that wants an
    hourly breakdown *by gateway* or *by product* is asking for a table 24x
    wider than it should be — use the facts for that.
    """

    __tablename__ = "agg_order_hourly"

    #: 0-23, store-local. Not a dimension_column because it is a NOT NULL
    #: integer with no unknown value — an event always has an hour.
    bucket_hour: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    orders: Mapped[int] = count_column()
    net_revenue: Mapped[Decimal] = money_column()
    units: Mapped[int] = count_column()

    __table_args__ = (
        UniqueConstraint(
            "bucket_date", "bucket_hour", "tz_generation", name="uq_agg_order_hourly_key"
        ),
    )


class AggProductDaily(Base, BigIDMixin, RollupMixin):
    """Per-product, per-day sales performance — the best/worst seller table.

    Answers: what sold, how much of it, at what margin, and what came back.
    Powers product leaderboards, category mix, and the margin-by-product view.

    `product_id` is a plain NOT NULL integer with no FK, so deleting a product
    never erases its sales history. Because identity would otherwise be lost
    with it, the product's identity is *snapshotted* into the row:
    `sku_snapshot` and `category_id_snapshot` record what the product was on
    the bucket date. Re-categorising a product tomorrow therefore does not
    silently rewrite last quarter's category mix — which is the correct
    behaviour for a report, even though it surprises people the first time.

    `category_id_snapshot` is nullable (a product genuinely may have no
    category) and is *not* part of the UNIQUE key, so nullability here does not
    endanger idempotency. `sku_snapshot` uses `dimension_column()` anyway, for
    consistency with how every other snapshotted string is written.

    Margin coverage: `line_cost` + `costed_units` are the numerator/denominator
    pair. `costed_units < units` means some lines had no `unit_cost` and the
    margin shown for this product is partial — the API surfaces that as a
    coverage percentage rather than this table storing one.

    Deliberately not here: stock levels (see `agg_inventory_daily`), views or
    add-to-carts per product (the funnel is store-wide — see
    `agg_funnel_daily`), and any notion of "product revenue" that includes
    shipping or tax, because neither can be honestly attributed to a line.
    """

    __tablename__ = "agg_product_daily"

    product_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The SKU as it was on `bucket_date`. History must not follow a rename.
    sku_snapshot: Mapped[str] = dimension_column(64)
    #: The category as it was on `bucket_date`; NULL means uncategorised.
    category_id_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)

    units: Mapped[int] = count_column()
    #: Distinct orders containing this product. Additive within a day only.
    orders: Mapped[int] = count_column()

    gross_merchandise_sales: Mapped[Decimal] = money_column()
    net_merchandise_sales: Mapped[Decimal] = money_column()

    #: Sum of (unit_cost * qty) over lines that had a known unit cost.
    line_cost: Mapped[Decimal] = money_column()
    #: Denominator for cost coverage. Never store the coverage percentage.
    costed_units: Mapped[int] = count_column()

    returned_units: Mapped[int] = count_column()
    returned_value: Mapped[Decimal] = money_column()

    __table_args__ = (
        UniqueConstraint(
            "bucket_date", "product_id", "tz_generation", name="uq_agg_product_daily_key"
        ),
        Index("ix_agg_product_daily_product_bucket_date", "product_id", "bucket_date"),
        Index(
            "ix_agg_product_daily_bucket_date_category",
            "bucket_date",
            "category_id_snapshot",
        ),
    )


class AggCustomerDaily(Base, BigIDMixin, RollupMixin):
    """Daily new-vs-returning customer trend. One row per reporting day.

    This is the **cheap trend table**: seven counters a day, scanned for the
    "new vs returning" chart and the acquisition/retention split on the
    dashboard. It is small enough to keep forever and to query over any range
    without thinking about cost.

    It deliberately cannot answer anything *per customer*. CLV, RFM segments,
    churn risk, cohort drill-down, "show me the customers in this band", and
    every list view of customers require `agg_customer_snapshot`, which is the
    expensive customers x days table. Do not extend this table toward those
    questions — adding a `customer_id` here would change its grain and destroy
    the reason it exists.

    Also note `active_customers` is a distinct count and therefore **not
    additive across days**: summing seven rows overcounts anyone who ordered
    twice that week. Weekly and monthly distinct counts must be recomputed from
    the facts or read off `agg_customer_snapshot`.
    """

    __tablename__ = "agg_customer_daily"

    #: Customers whose first-ever order landed on this day.
    new_customers: Mapped[int] = count_column()
    #: Customers who ordered today and had ordered before today.
    returning_customers: Mapped[int] = count_column()
    #: Distinct customers who ordered today. NOT additive across days.
    active_customers: Mapped[int] = count_column()

    orders_new: Mapped[int] = count_column()
    orders_returning: Mapped[int] = count_column()

    revenue_new: Mapped[Decimal] = money_column()
    revenue_returning: Mapped[Decimal] = money_column()

    __table_args__ = (
        UniqueConstraint("bucket_date", "tz_generation", name="uq_agg_customer_daily_key"),
    )


class AggCustomerSnapshot(Base, BigIDMixin, RollupMixin):
    """Per-customer lifetime state as of a date — CLV, RFM, cohort, churn risk.

    Grain is (snapshot date, customer). `bucket_date` from `RollupMixin` is
    reused as the **snapshot date**: unlike every other table in this module it
    is not "activity that happened on this day" but "what this customer looked
    like at the close of this day". The mixin is kept rather than a separate
    `snapshot_date` column so retention, `tz_generation` handling, and
    `computed_at` behave identically everywhere; the semantic difference lives
    here in the docstring and must be respected by anything that joins to it.

    Answers: who are our best customers, what is a customer worth, which
    segment is a customer in, who is about to churn, how does a cohort mature.
    This is the table behind every per-customer question — `agg_customer_daily`
    can answer none of them.

    **RETENTION POLICY — this is not optional.** The grain is customers x days,
    so the table grows without bound: 50k customers is 50k rows *per day*, 18M
    rows a year, for a table whose main use is "current state". The retention
    job therefore keeps:

      * daily rows for the last **90 days** (recent trend, churn movement), and
      * **month-end rows only** beyond that (long-term LTV and cohort curves).

    Anything that needs day-level history older than 90 days is asking the
    wrong table — recompute from the facts.

    `customer_key` is the grain key rather than `user_id` because it must be
    stable across account merges and must cover guest checkouts that never got
    a user row. `user_id` is kept alongside as a nullable, un-FK'd convenience
    for joining to `users` when one exists.

    `quality` labels how complete this row is (e.g. cost data missing, orders
    predating the fact backfill). The API surfaces it in the response envelope;
    a metric that is *unknown* is never represented by writing 0.

    Deliberately not here: PII. No email, no phone, no name — this is an
    analytics table that is exported, sampled, and joined casually, and
    identity lives one join away in `users`.
    """

    __tablename__ = "agg_customer_snapshot"

    #: Stable analytics identity: survives account merges and covers guests.
    customer_key: Mapped[str] = dimension_column(64)
    #: Convenience join target. NULL for guest customers. No FK by design.
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    first_order_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_order_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    orders_count: Mapped[int] = count_column()
    units: Mapped[int] = count_column()

    gross_ltv: Mapped[Decimal] = money_column()
    net_ltv: Mapped[Decimal] = money_column()
    margin_ltv: Mapped[Decimal] = money_column()
    #: Per-customer average order value == `gross_ltv / orders_count`.
    #: Materialised only because it is the RFM/segmentation sort key and the
    #: snapshot row is the grain, so it is never re-bucketed. It is a DERIVED
    #: column: never SUM it, never AVG it across rows. A cross-customer AOV is
    #: `SUM(gross_ltv) / SUM(orders_count)`, recomputed from the two stored
    #: components — averaging this column would be an average of averages.
    aov: Mapped[Decimal] = money_column()

    # ---- RFM inputs and scores -------------------------------------------
    #: Days between `last_order_at` and the snapshot date.
    recency_days: Mapped[int] = count_column()
    #: Order count over the RFM window (not lifetime — see the scoring job).
    frequency: Mapped[int] = count_column()
    #: Spend over the RFM window.
    monetary: Mapped[Decimal] = mapped_column(MONEY, nullable=False, server_default="0")

    #: Quintile scores 1-5. Stored because they depend on the whole population
    #: on the snapshot date and cannot be recomputed from this row alone.
    r_score: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    f_score: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    m_score: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")

    #: e.g. "champions", "at_risk", "hibernating". Indexed with bucket_date.
    rfm_segment: Mapped[str] = dimension_column(32)

    # ---- Cohort / tenure / status ----------------------------------------
    #: 'YYYY-MM' of the customer's first order. Joins to
    #: `agg_customer_cohort_monthly.cohort_month`.
    cohort_month: Mapped[str] = dimension_column(7)
    #: Days between `first_order_at` and the snapshot date.
    tenure_days: Mapped[int] = count_column()
    #: Ordered within the activity window defined by the scoring job.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    #: Banded, not a probability: "low" / "medium" / "high". A stored churn
    #: *percentage* would be a model output masquerading as a measurement.
    churn_risk_band: Mapped[str] = dimension_column(16)
    preferred_payment_method: Mapped[str] = dimension_column(20)

    #: Completeness label for this row, surfaced in the API envelope.
    quality: Mapped[str] = mapped_column(String(16), nullable=False, server_default="unknown")

    __table_args__ = (
        UniqueConstraint(
            "bucket_date",
            "customer_key",
            "tz_generation",
            name="uq_agg_customer_snapshot_key",
        ),
        Index("ix_agg_customer_snapshot_bucket_date_segment", "bucket_date", "rfm_segment"),
        Index(
            "ix_agg_customer_snapshot_customer_key_bucket_date",
            "customer_key",
            "bucket_date",
        ),
    )


class AggCustomerCohortMonthly(Base, BigIDMixin, RollupMixin):
    """Monthly acquisition cohorts x months-since-acquisition retention grid.

    Grain is (cohort_month, period_index). `cohort_month` is 'YYYY-MM' of the
    customer's first order; `period_index` is months elapsed since then, so
    `period_index = 0` is the acquisition month itself and `cohort_size` is
    constant across a cohort's row family. This is the retention triangle
    rendered by the cohort heatmap.

    **Fully recomputed for the last 24 cohorts on every run.** Cohort retention
    is exquisitely sensitive to late status changes — a refund processed in
    August rewrites the March cohort's month-5 revenue — so this table is not
    incrementally updated. Each run deletes and reinserts the trailing 24
    cohorts' rows, which makes it immune to late-arriving corrections by
    construction rather than by a reconciliation job. Cohorts older than 24
    months are frozen; if they ever need correcting, widen the recompute
    window rather than patching rows.

    `bucket_date` from `RollupMixin` is the first day of the *period* being
    measured (cohort month + `period_index` months). It is intentionally not
    part of the UNIQUE key because `(cohort_month, period_index)` already
    determines it; including it would let a bug write two rows for the same
    cell.

    Deliberately not here: retention *rate*. `active_customers / cohort_size`
    is the ratio and both sides are stored, so the heatmap divides at render
    time and can re-aggregate cohorts (e.g. quarterly) correctly.
    """

    __tablename__ = "agg_customer_cohort_monthly"

    #: 'YYYY-MM' of the cohort's acquisition month.
    cohort_month: Mapped[str] = dimension_column(7)
    #: Months since acquisition. 0 = the acquisition month itself.
    period_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    #: Customers acquired in `cohort_month`. Constant across the cohort's rows;
    #: repeated per row so a single row is self-sufficient as a denominator.
    cohort_size: Mapped[int] = count_column()
    #: Cohort members who ordered during this period. Numerator for retention.
    active_customers: Mapped[int] = count_column()

    orders: Mapped[int] = count_column()
    revenue: Mapped[Decimal] = money_column()

    __table_args__ = (
        UniqueConstraint(
            "cohort_month",
            "period_index",
            "tz_generation",
            name="uq_agg_customer_cohort_monthly_key",
        ),
        Index("ix_agg_customer_cohort_monthly_cohort_month", "cohort_month"),
    )


class AggPaymentDaily(Base, BigIDMixin, RollupMixin):
    """Payment outcomes per day x gateway x method x instrument.

    Answers: which gateway is failing, which payment method converts, how long
    settlement takes, and where our records disagree with the gateway's.

    All three dimensions are `dimension_column()` — NOT NULL with a `'-'`
    sentinel — because they are in the UNIQUE key. The source columns
    (`order_payments.gateway`, `.payment_method`, `orders.payment_instrument`)
    are all nullable, so writers *must* COALESCE. A single NULL slipping
    through would let the same day+gateway row be inserted repeatedly, and
    MySQL would not complain.

    **Rates are not stored.** Success rate is `paid / attempts`, failure rate
    is `failed / attempts`, and both denominators are here. Storing a rate
    would make "success rate across all gateways this month" an unweighted
    average of averages.

    Settlement time follows the sum+count rule: `sum_settle_seconds` with
    `n_settle`. `n_settle` is usually smaller than `paid` because not every
    paid payment has both timestamps; dividing by `paid` instead would
    understate the average, which is exactly the bug the pair prevents.

    `mismatch_events` counts reconciliation disagreements between our record
    and the gateway's (amount, status, or missing on either side) attributed to
    this bucket — an operational alarm, not a financial figure.

    `top_failure_reason` is a *convenience label only*: the single most common
    failure string in the bucket, nullable because a bucket with no failures
    has none. It is not a dimension, is not in the key, and must never be
    counted — the full distribution lives in the payment event facts.
    """

    __tablename__ = "agg_payment_daily"

    gateway: Mapped[str] = dimension_column(40)
    payment_method: Mapped[str] = dimension_column(32)
    payment_instrument: Mapped[str] = dimension_column(32)

    #: Every payment attempt in the bucket. Denominator for all rates.
    attempts: Mapped[int] = count_column()
    paid: Mapped[int] = count_column()
    failed: Mapped[int] = count_column()
    cancelled: Mapped[int] = count_column()
    refunded: Mapped[int] = count_column()

    paid_amount: Mapped[Decimal] = money_column()
    #: Value of attempts that failed — the size of the leak, not lost revenue.
    failed_amount: Mapped[Decimal] = money_column()

    #: Initiation -> settlement duration. Divide by `n_settle`, never `paid`.
    sum_settle_seconds: Mapped[int] = seconds_column()
    n_settle: Mapped[int] = count_column()

    mismatch_events: Mapped[int] = count_column()

    #: Most frequent failure reason in this bucket. Label only — never a key,
    #: never counted. NULL when nothing failed.
    top_failure_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "bucket_date",
            "gateway",
            "payment_method",
            "payment_instrument",
            "tz_generation",
            name="uq_agg_payment_daily_key",
        ),
        Index("ix_agg_payment_daily_bucket_date_gateway", "bucket_date", "gateway"),
    )


class AggShipmentDaily(Base, BigIDMixin, RollupMixin):
    """Fulfilment outcomes and cycle times per day x courier partner.

    Answers: how many shipments went out, how many were delivered, how many
    came back as RTO, what shipping cost us, and how long each leg took.

    **On-time delivery rate is NOT computable and must not appear here.** There
    is no promised-delivery-date, no SLA, and no courier ETA column anywhere in
    the schema — not on `shipments`, not on `orders`, not on any shipping
    provider payload we persist. Any "on-time %" would therefore be measured
    against a threshold invented at query time and presented as a fact. Do not
    add a column whose name implies we know the promise date (`on_time`,
    `sla_met`, `late_shipments`). If the business needs OTD, the fix is to
    capture the promised date at shipment creation and start a forward-only
    series — not to backfill an assumption. What *is* here instead:
    `sum_ship_to_deliver_seconds` / `n_ship_to_deliver`, an honest transit-time
    distribution input.

    Both cycle times follow the sum+count rule with their own denominators:
    `n_order_to_ship` counts shipments that actually shipped,
    `n_ship_to_deliver` counts those that actually delivered. They differ, and
    dividing either sum by `shipments` would be wrong.

    Delivery success rate is `delivered / shipments`, RTO rate is
    `rto_initiated / shipments` — computed at query time from the stored
    numerators and denominator, never materialised.

    `courier_partner` is `dimension_column()` because `shipments.courier_partner`
    is nullable and the column is in the UNIQUE key.
    """

    __tablename__ = "agg_shipment_daily"

    courier_partner: Mapped[str] = dimension_column(64)

    #: Shipments created in this bucket. Denominator for delivery/RTO rates.
    shipments: Mapped[int] = count_column()
    delivered: Mapped[int] = count_column()
    delivery_failed: Mapped[int] = count_column()
    rto_initiated: Mapped[int] = count_column()
    rto_delivered: Mapped[int] = count_column()
    cancelled: Mapped[int] = count_column()

    shipment_cost: Mapped[Decimal] = money_column()

    #: Order placed -> handed to courier. Divide by `n_order_to_ship`.
    sum_order_to_ship_seconds: Mapped[int] = seconds_column()
    n_order_to_ship: Mapped[int] = count_column()

    #: Handed to courier -> delivered. Divide by `n_ship_to_deliver`.
    sum_ship_to_deliver_seconds: Mapped[int] = seconds_column()
    n_ship_to_deliver: Mapped[int] = count_column()

    __table_args__ = (
        UniqueConstraint(
            "bucket_date", "courier_partner", "tz_generation", name="uq_agg_shipment_daily_key"
        ),
        Index("ix_agg_shipment_daily_bucket_date_courier", "bucket_date", "courier_partner"),
    )


class AggGeoDaily(Base, BigIDMixin, RollupMixin):
    """Where orders come from and how they land: day x state x pincode.

    Answers: which states and pincodes drive revenue, where COD dominates,
    where RTO is bleeding money, and where delivery is slow. Powers the geo map
    and the "problem pincodes" operational list.

    **Pincode-grain rows are pruned to state grain after 180 days.** Two
    reasons, and the second one is the binding one:

      1. Row count. India has ~19k live pincodes; pincode grain is the widest
         table in this module and it is queried almost entirely for *recent*
         operational decisions.
      2. Privacy retention. Fine-grained geographic history is personal-data
         adjacent — a small pincode plus a date plus an order count is close to
         identifying. Keeping it indefinitely is a liability with no analytical
         payoff, so the retention job re-aggregates rows older than 180 days up
         to state grain (pincode becomes the `'-'` sentinel) and deletes the
         pincode-level originals.

    Because of that rollup-in-place, both dimensions are `dimension_column()`:
    `'-'` is a real, meaningful value here ("aggregated to state grain" or
    "pincode unknown"), and a NULL would both break the UNIQUE key and make the
    pruned rows indistinguishable from a bug.

    COD share is `cod_orders / orders` and RTO rate is `rto / delivered + rto`
    — computed at query time. `cod_orders` and `prepaid_orders` are stored
    separately rather than as a share so a state's mix can be re-aggregated to
    a region correctly.
    """

    __tablename__ = "agg_geo_daily"

    state: Mapped[str] = dimension_column(64)
    #: 6-digit PIN, normalised by the writer. Anything non-conforming (or a
    #: row rolled up past the 180-day pincode retention window) is `'-'`.
    pincode: Mapped[str] = dimension_column(10)

    orders: Mapped[int] = count_column()
    net_revenue: Mapped[Decimal] = money_column()
    units: Mapped[int] = count_column()

    cod_orders: Mapped[int] = count_column()
    prepaid_orders: Mapped[int] = count_column()

    delivered: Mapped[int] = count_column()
    rto: Mapped[int] = count_column()

    #: Order -> delivered duration for this geography. Divide by `n_delivery`,
    #: which is smaller than `delivered` when timestamps are incomplete.
    sum_delivery_seconds: Mapped[int] = seconds_column()
    n_delivery: Mapped[int] = count_column()

    __table_args__ = (
        UniqueConstraint(
            "bucket_date", "state", "pincode", "tz_generation", name="uq_agg_geo_daily_key"
        ),
        Index("ix_agg_geo_daily_bucket_date_state", "bucket_date", "state"),
    )


class AggPromoDaily(Base, BigIDMixin, RollupMixin):
    """Coupon and loyalty-reward redemption per day x code.

    Answers: which codes are being used, what they cost in discount, and what
    revenue the discounted orders brought in. The pairing of
    `discount_amount` with `order_revenue` is the point — a code that gives
    away 40k to bring in 45k is a different story from one that gives away 40k
    to bring in 400k, and only both numbers together tell it.

    `redemptions` and `orders` are separate on purpose: a single order can
    apply a code once, but redemption events and orders diverge when an order
    is later cancelled or a code is re-applied during an edit. Keeping both
    lets the discrepancy be seen rather than averaged away.

    `is_loyalty_reward` distinguishes a loyalty-programme reward code from a
    marketing coupon. It is an attribute of the code, not part of the grain:
    the code determines it, so putting it in the UNIQUE key would allow the
    same code to occupy two rows on one day if the flag were ever computed
    inconsistently.

    Deliberately not here: ROI, uplift, or incrementality. None of them are
    measurable from redemption data alone — they need a control group we do not
    have — and a stored `roi_pct` column would be a guess with a decimal point.
    """

    __tablename__ = "agg_promo_daily"

    coupon_code: Mapped[str] = dimension_column(64)

    #: Redemption events in the bucket.
    redemptions: Mapped[int] = count_column()
    #: Total discount given away by this code. The cost side.
    discount_amount: Mapped[Decimal] = money_column()
    #: Distinct orders carrying this code.
    orders: Mapped[int] = count_column()
    #: Revenue of those orders (canonical revenue statuses). The return side.
    order_revenue: Mapped[Decimal] = money_column()

    #: Attribute of the code, not part of the grain. See class docstring.
    is_loyalty_reward: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="0"
    )

    __table_args__ = (
        UniqueConstraint(
            "bucket_date", "coupon_code", "tz_generation", name="uq_agg_promo_daily_key"
        ),
        Index("ix_agg_promo_daily_coupon_code_bucket_date", "coupon_code", "bucket_date"),
    )


class AggFunnelDaily(Base, BigIDMixin, RollupMixin):
    """Daily checkout funnel step counts. One row per reporting day.

    Answers: where do people drop out between having a cart and having an
    order. Each column is a step counter; conversion between any two steps is
    a division done at query time, never stored.

    **FUNNEL_STARTS_AT_CART.** This funnel begins at cart/product-view events
    we emit ourselves — it does **not** start at a session or a visit. We have
    no server-side record of a visitor who never touched a product or a cart,
    so there is no traffic top-of-funnel here and no honest "visitor ->
    purchase" conversion rate. Anything above the cart (sessions, landing
    pages, sources, bounce) requires GA4 or an equivalent client-side
    analytics source, which is a separate integration.

    Every API view and chart built on this table **must carry the
    `FUNNEL_STARTS_AT_CART` warning** in its response envelope. Without it a
    reader will divide `orders_placed` by `distinct_sessions`, call the result
    "site conversion rate", and be wrong by whatever fraction of traffic never
    reached a product page — which is most of it.

    `distinct_sessions` is a distinct count and therefore **not additive**:
    summing seven days overcounts returning sessions. It also counts only
    sessions that reached at least one tracked step, per the warning above.
    """

    __tablename__ = "agg_funnel_daily"

    product_views: Mapped[int] = count_column()
    cart_views: Mapped[int] = count_column()
    items_added: Mapped[int] = count_column()
    checkouts_started: Mapped[int] = count_column()
    shipping_submitted: Mapped[int] = count_column()
    payments_initiated: Mapped[int] = count_column()
    payments_failed: Mapped[int] = count_column()
    orders_placed: Mapped[int] = count_column()

    #: Sessions that reached at least one tracked step. NOT additive across
    #: days, and NOT total site traffic — see FUNNEL_STARTS_AT_CART above.
    distinct_sessions: Mapped[int] = count_column()

    __table_args__ = (
        UniqueConstraint("bucket_date", "tz_generation", name="uq_agg_funnel_daily_key"),
    )


class AggInventoryDaily(Base, BigIDMixin, RollupMixin):
    """End-of-day stock position per product — the inventory ledger.

    Answers: what did we hold and what was it worth at the close of each day,
    how much moved, and how often did we run out. Powers stock-value trend,
    out-of-stock days, and reorder timing.

    **FORWARD-ONLY. Inventory history cannot be backfilled.** Reconstructing a
    past stock level requires the full sequence of restocks and manual stock
    edits, and neither was ever recorded — `products.stock` is a mutable
    current value with no audit trail, so walking backwards from today through
    sales alone would silently produce fiction. This table therefore starts on
    the day the ledger job first ran and has nothing before it.

    The first ledger date is stored in the **`analytics.inventory_history_since`**
    setting. Every history view must read that setting and clamp its x-axis to
    it. Drawing a flat zero line before that date is the failure mode to avoid:
    it looks like "we held no stock" rather than "we have no data", and it is
    indistinguishable from a real stockout at a glance.

    `stock_close` is a *level*, not a flow: it is the closing balance, so it is
    never summed across days. `units_sold` and `units_restocked` are flows and
    are additive. Mixing the two is the classic inventory reporting bug.

    `is_oos` and `days_oos` are stored rather than an OOS *rate*: `days_oos` is
    the running count of consecutive out-of-stock days as of this date (an
    operational signal, reset on restock), and any OOS percentage is
    `SUM(is_oos) / COUNT(*)` over the queried range.

    `reorder_gap` is nullable because it is genuinely unknown for products with
    no reorder point configured — NULL here means "not configured", which is
    different from 0 ("at the reorder point"). It is not in the UNIQUE key, so
    the nullability is safe.
    """

    __tablename__ = "agg_inventory_daily"

    product_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The SKU as it was on `bucket_date`. No FK — see module docstring.
    sku_snapshot: Mapped[str] = dimension_column(64)

    #: Closing stock level. A LEVEL — never sum this across days.
    stock_close: Mapped[int] = count_column()
    #: Closing stock valued at cost. Also a level.
    stock_value_close: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False, server_default="0"
    )

    #: Flows — additive across days.
    units_sold: Mapped[int] = count_column()
    units_restocked: Mapped[int] = count_column()

    #: True if stock was zero at close.
    is_oos: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    #: Consecutive out-of-stock days as of this date; resets on restock.
    days_oos: Mapped[int] = count_column()

    #: stock_close - reorder_point. NULL = no reorder point configured, which
    #: is not the same as 0. Negative means "below the reorder point".
    reorder_gap: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "bucket_date", "product_id", "tz_generation", name="uq_agg_inventory_daily_key"
        ),
        Index("ix_agg_inventory_daily_product_bucket_date", "product_id", "bucket_date"),
    )
