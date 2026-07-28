"""Control-plane tables for the analytics subsystem.

The analytics schema splits three ways:

* **Facts** (`analytics_facts.py`) — immutable, row-per-thing snapshots.
* **Rollups** (`analytics_rollups.py`) — derived aggregates, always rebuildable
  from the facts.
* **Control** (this module) — the operational state that decides *how* and
  *when* the rollups get built, and what the numbers are allowed to claim.

Everything here answers a question that the facts and rollups cannot answer
about themselves:

===========================  =================================================
`analytics_tz_generations`   which calendar the reporting days were cut on
`analytics_cost_rules`       what a cost was *on the day it was incurred*
`analytics_recompute_queue`  which buckets are known-stale and must be redone
`analytics_sync_runs`        whether the pipeline actually ran, and how far
`analytics_event_outbox`     which purchases still owe GA4 a server-side event
`analytics_budgets`          what the business said it intended to do
`analytics_alerts`           which deviations a human has already been told of
===========================  =================================================

Two conventions from `analytics_base.py` matter especially here.

**No foreign keys, anywhere.** Same reasoning as the rollups: these tables must
survive truncation, backfill, product deletion and order purging. A queue row
for a bucket whose orders were later deleted is still a valid instruction to
recompute that bucket — an FK would turn it into an integrity error. User-id
columns (`created_by_user_id`, `acknowledged_by_user_id`) are plain nullable
integers for exactly the reason `audit.py` keeps `target_id` loose: the audit
trail must outlive the account.

**Dimension columns are NOT NULL with the `'-'` sentinel.** MySQL allows an
unlimited number of NULLs under a UNIQUE index. `analytics_recompute_queue` and
`analytics_cost_rules` both lean on a UNIQUE key for correctness — dedup on one,
non-overlap on the other — so a nullable dimension there would not merely be
untidy, it would silently disable the guarantee the table exists to provide.

Status/type columns are plain `String`, never DB enums, so adding a new reason
or alert rule is a code change rather than a migration. Use the constant classes
in this module instead of bare strings; a typo then fails at import rather than
writing an unroutable row.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.analytics_base import (
    MONEY,
    BigIDMixin,
    CreatedAtMixin,
    count_column,
    dimension_column,
)
from app.models.base import Base, TimestampMixin

__all__ = [
    # Timezone generations
    "TzGenerationStatus",
    "AnalyticsTzGeneration",
    # Cost rules
    "CostType",
    "CostScope",
    "CostUnit",
    "CostQuality",
    "AnalyticsCostRule",
    # Recompute queue
    "RecomputeReason",
    "RecomputeStatus",
    "AnalyticsRecomputeQueue",
    # Sync runs
    "SyncTrigger",
    "SyncStatus",
    "AnalyticsSyncRun",
    # GA4 outbox
    "OutboxEventName",
    "OutboxStatus",
    "ConsentState",
    "AnalyticsEventOutbox",
    # Budgets
    "BudgetGranularity",
    "AnalyticsBudget",
    # Alerts
    "AlertSeverity",
    "AlertStatus",
    "AlertRuleKey",
    "AnalyticsAlert",
]


# ===========================================================================
# 1. Reporting-timezone generations
# ===========================================================================
class TzGenerationStatus:
    """Lifecycle of a reporting-timezone generation."""

    #: Serving reads. Exactly one generation should hold this at a time.
    ACTIVE = "active"
    #: Buckets are being recomputed under this generation; not yet readable.
    REBUILDING = "rebuilding"
    #: Superseded. Rows survive for the hold period, then get swept.
    RETIRED = "retired"


class AnalyticsTzGeneration(Base, BigIDMixin, CreatedAtMixin):
    """The authority for reporting-timezone changes.

    Reporting days are store-local, not UTC. `store.timezone` in
    `system_settings` says what that zone currently is, but a *setting* is a
    single mutable cell — it cannot describe the fact that the rows written last
    March were cut on a different calendar. This table can, and every
    date-bucketed row carries the matching `tz_generation`.

    Why it has to work this way
    ---------------------------
    Changing the store timezone re-buckets **every** reporting day. An order
    placed at 02:00 IST belongs to one calendar day under `Asia/Kolkata` and the
    previous one under UTC. Flip the setting and yesterday's revenue moves,
    last quarter's daily series shifts, and the month boundaries land in
    different places. Nothing errors. Nothing looks wrong. The chart just draws
    a different history than it drew the day before, and a mixed query — half
    its buckets cut one way, half the other — double-counts the seam and drops
    the gap, with no symptom beyond numbers that no longer reconcile.

    So generations are never mixed. A query whose range spans two generations
    must **refuse** and tell the caller a rebuild is in flight; it must not
    quietly union them.

    The change procedure
    --------------------
    Never edit a live generation in place. To move the store to a new timezone:

    a. **Write a new generation row as `rebuilding`.** It gets the next
       `generation` number, the new IANA `timezone`, and the `effective_from`
       instant. It is not readable yet.
    b. **Enqueue a full recompute under it** — every bucket, every job, from the
       earliest fact forward, via `analytics_recompute_queue` with reason
       `TZ_REBUILD` and the new `tz_generation`.
    c. **Keep serving the old generation the whole time.** Reads continue
       against `active`; the dashboard never goes blank and never shows a
       half-rebuilt series.
    d. **Flip to `active` only when the rebuild completes** — every enqueued
       bucket `DONE`, no `FAILED` rows outstanding. The flip is the single
       moment reads move, and it is atomic. Set `rebuild_finished_at` with it.
    e. **Retire the old generation after a hold period**, not immediately. The
       hold exists so a bad rebuild can be rolled back by re-activating the
       previous generation instead of rebuilding a second time under pressure.

    Fields
    ------
    `created_by_user_id` and `note` exist because "why did the numbers change on
    the 14th?" is a question someone will ask months later, and the honest
    answer lives nowhere else.
    """

    __tablename__ = "analytics_tz_generations"

    # Monotonic generation counter. Copied onto every date-bucketed row so a
    # row always knows which calendar produced it. SmallInteger: this changes
    # a handful of times in a store's life, never routinely.
    generation: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    # IANA zone name, e.g. "Asia/Kolkata". Not an offset — offsets change with
    # DST and a stored offset would be wrong half the year in zones that
    # observe it.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)

    # The instant this generation's calendar takes effect. UTC.
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # TzGenerationStatus. The read path filters on this; a `rebuilding` row is
    # invisible to queries.
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    # Rebuild window. `rebuild_finished_at` being NULL while status is
    # `rebuilding` is the signal that a rebuild is still in flight, which is
    # what a spanning query reports back to the caller.
    rebuild_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rebuild_finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Admin who initiated the change. Plain int, no FK — see module docstring.
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Free-text reason: "moved ops to Dubai", "was wrong at launch".
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        # One row per generation number. Two rows claiming generation 3 would
        # make every `tz_generation` reference ambiguous.
        UniqueConstraint("generation", name="uq_analytics_tz_generations_generation"),
    )


# ===========================================================================
# 2. Effective-dated cost rules
# ===========================================================================
class CostType:
    """What kind of cost a rule describes.

    These replace the five mutable `costs.*` rows in `system_settings`
    (`costs.packing_per_order`, `costs.handling_per_order`,
    `costs.gateway_fee_pct`, `costs.monthly_overheads`, `costs.monthly_ad_spend`)
    and extend them to the cost lines those five could not express at all —
    per-courier shipping, RTO, returns, marketplace commission.
    """

    #: Payment-gateway fee. Usually PCT of prepaid order value; COD exempt.
    GATEWAY_FEE = "gateway_fee"
    #: Packing material per dispatched order.
    PACKAGING = "packaging"
    #: Warehouse handling / pick-pack labour per order.
    HANDLING = "handling"
    #: Outbound shipping paid to the courier.
    FORWARD_SHIPPING = "forward_shipping"
    #: Reverse pickup on a customer return.
    RETURN_SHIPPING = "return_shipping"
    #: Return-to-origin: the round trip eaten on an undelivered order.
    RTO_LOGISTICS = "rto_logistics"
    #: Variable warehouse cost that scales with volume (not the fixed rent).
    WAREHOUSE_VARIABLE = "warehouse_variable"
    #: Commission taken by a marketplace channel.
    MARKETPLACE_COMMISSION = "marketplace_commission"
    #: Advertising / marketing spend. PER_MONTH, pro-rated into buckets.
    MARKETING_SPEND = "marketing_spend"
    #: Fixed monthly overhead — rent, salaries, SaaS. PER_MONTH, pro-rated.
    MONTHLY_OVERHEAD = "monthly_overhead"


class CostScope:
    """How narrowly a rule applies. Resolution prefers the most specific match."""

    #: Applies to everything not covered by a narrower rule.
    GLOBAL = "global"
    #: `scope_value` is a payment gateway code (matches payment_methods).
    GATEWAY = "gateway"
    #: `scope_value` is a courier code.
    COURIER = "courier"
    #: `scope_value` is a category id or slug.
    CATEGORY = "category"
    #: `scope_value` is a product id or SKU.
    PRODUCT = "product"
    #: `scope_value` is "cod" / "prepaid" / a payment-method code.
    PAYMENT_METHOD = "payment_method"

    #: Most-specific first. Resolution walks this list and takes the first
    #: scope with a rule covering the bucket_date.
    PRECEDENCE = (PRODUCT, CATEGORY, COURIER, GATEWAY, PAYMENT_METHOD, GLOBAL)


class CostUnit:
    """How `value` is to be interpreted. Getting this wrong is a 100x error."""

    #: Percentage of a money base, e.g. 2.5 means 2.5% (NOT 0.025).
    PCT = "pct"
    #: Flat amount per order.
    PER_ORDER = "per_order"
    #: Flat amount per unit / line-item quantity.
    PER_UNIT = "per_unit"
    #: Amount per kilogram of billed shipping weight.
    PER_KG = "per_kg"
    #: Fixed monthly amount, pro-rated across the days in the bucket.
    PER_MONTH = "per_month"


class CostQuality:
    """How much the number is actually worth trusting.

    This rides through to the metric-quality label in the API envelope, so a
    margin computed entirely from `ESTIMATED` rules is never presented with the
    same confidence as one built from settled invoices.

    Note there is deliberately no `MISSING` member: a missing cost is the
    *absence* of a covering rule, computed at resolution time, not a row someone
    remembered to write. See `AnalyticsCostRule` for what happens then.
    """

    #: Reconciled against a settlement report or supplier invoice.
    ACTUAL = "actual"
    #: Taken from a signed rate card / contracted slab.
    CONTRACTED = "contracted"
    #: Derived from historical averages by the system.
    ESTIMATED = "estimated"
    #: A human typed a plausible number. Better than nothing, worse than a bill.
    ASSUMED = "assumed"


class AnalyticsCostRule(Base, BigIDMixin, CreatedAtMixin):
    """Effective-dated cost inputs for contribution-margin reporting.

    What this replaces, and why
    ---------------------------
    Costs currently live as five mutable rows in `system_settings`
    (`costs.gateway_fee_pct`, `costs.packing_per_order`, ...). A single mutable
    cell has no time axis, so it answers "what does packing cost?" but never
    "what did packing cost in March?" — and the margin report needs the second
    question. The consequence is not a missing feature, it is silent
    falsification: an admin who edits `costs.packing_per_order` from ₹8 to ₹12
    rewrites *every historical margin the store has ever reported*. March's
    contribution margin changes retroactively, last quarter's board deck no
    longer reproduces, and nothing anywhere records that an edit happened.

    Effective dating fixes exactly that. A rule is valid over
    `[effective_from, effective_to]` (NULL `effective_to` = open-ended, still in
    force). Changing a cost writes a **new row** with a new `effective_from` and
    closes the old one; the old row stays, so March still resolves March's ₹8.

    Resolution
    ----------
    For a given `bucket_date` and `cost_type`, resolution:

    1. Filters to rules where
       ``effective_from <= bucket_date AND (effective_to IS NULL OR
       effective_to >= bucket_date)`` — **by the bucket's own date, never by
       "now"**. This is the whole point: recomputing March uses March's rules.
    2. Walks `CostScope.PRECEDENCE` most-specific first and takes the first
       scope with a matching rule — a per-courier forward-shipping rule beats
       the global one for that courier, and only for that courier.
    3. Applies `value` according to `unit`, and carries `quality` into the
       metric's quality label.

    Editing enqueues, it does not mutate
    ------------------------------------
    Writing a rule that covers already-computed buckets must enqueue those
    buckets into `analytics_recompute_queue` with reason `COST_RULE_CHANGE`.
    The stored rollup is then rebuilt from facts + the new rule. The past is
    recomputed, deliberately and traceably — not overwritten as a side effect of
    someone saving a settings form.

    Missing is not zero
    -------------------
    If no rule covers a bucket, that cost component resolves to **MISSING** and
    every metric derived from it is labelled **INCOMPLETE**. It must never
    resolve to 0. A zero packaging cost is indistinguishable from free
    packaging, and the resulting margin reads *better* than reality — the exact
    direction of error nobody investigates. An incomplete margin that says so is
    useful; a confident wrong one is worse than no number at all.
    """

    __tablename__ = "analytics_cost_rules"

    # CostType constant. Plain varchar so new cost lines need no migration.
    # No standalone index: it is the leading column of
    # ix_analytics_cost_rules_type_from_to below, which already serves
    # cost_type-only lookups.
    cost_type: Mapped[str] = mapped_column(String(48), nullable=False)

    # CostScope constant. Defaults to global — the common case is one rule for
    # the whole store.
    scope: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=CostScope.GLOBAL
    )

    # The scoped entity: gateway code, courier code, category, SKU, "cod".
    # NOT NULL with the '-' sentinel (global rules carry '-') because this
    # column sits inside the UNIQUE key below — see module docstring.
    scope_value: Mapped[str] = dimension_column(64)

    # The rate or amount. Numeric(14,4), wider on scale than MONEY, because a
    # percentage needs the fractional digits (2.3625% is a real gateway rate)
    # and a per-unit cost can be fractions of a rupee.
    value: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)

    # CostUnit constant. Meaningless without it — 2.5 is either 2.5% or ₹2.50.
    unit: Mapped[str] = mapped_column(String(24), nullable=False)

    # ISO-4217. Ignored when unit is PCT.
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default="INR"
    )

    # CostQuality constant. Propagates into the metric quality label.
    quality: Mapped[str] = mapped_column(String(16), nullable=False)

    # Validity window, in store-local reporting days (same calendar as
    # bucket_date). `effective_to` NULL means still in force.
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Where the number came from: "razorpay_settlement_2026_03",
    # "bluedart_rate_card_v4", "migrated:costs.packing_per_order".
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Admin who wrote the rule. Plain int, no FK.
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        # One rule per (type, scope, scoped entity, start date). Prevents two
        # contradictory rules starting the same day — which would otherwise
        # make resolution order-dependent and therefore non-reproducible.
        # Overlapping windows for the *same* key still need an application-level
        # check on write (close the previous row); MySQL cannot express a range
        # exclusion constraint.
        UniqueConstraint(
            "cost_type",
            "scope",
            "scope_value",
            "effective_from",
            name="uq_analytics_cost_rules_type_scope_value_from",
        ),
        # The resolution query's access path: type, then window containment.
        Index(
            "ix_analytics_cost_rules_type_from_to",
            "cost_type",
            "effective_from",
            "effective_to",
        ),
    )


# ===========================================================================
# 3. Dirty-bucket recompute queue
# ===========================================================================
class RecomputeReason:
    """Why a bucket was marked dirty. Kept for forensics and for prioritising."""

    #: Order moved between states (delivered, cancelled, RTO...).
    ORDER_STATUS_CHANGE = "order_status_change"
    #: Refund issued — frequently weeks after the order's bucket.
    REFUND = "refund"
    #: Return received/approved.
    RETURN = "return"
    #: Courier scan changed a shipment's terminal state.
    SHIPMENT_UPDATE = "shipment_update"
    #: Gateway settlement or chargeback landed and changed net receipts.
    PAYMENT_SETTLEMENT = "payment_settlement"
    #: An `analytics_cost_rules` row covering this bucket was written.
    COST_RULE_CHANGE = "cost_rule_change"
    #: Stock movement affecting inventory-valuation rollups.
    INVENTORY_CHANGE = "inventory_change"
    #: Full rebuild under a new `analytics_tz_generations` row.
    TZ_REBUILD = "tz_rebuild"
    #: An operator asked for it from the admin UI.
    MANUAL = "manual"
    #: Historical fill of a bucket that was never computed.
    BACKFILL = "backfill"


class RecomputeStatus:
    """Queue row lifecycle."""

    #: Waiting to be claimed.
    PENDING = "pending"
    #: Claimed by a worker; `claim_expires_at` bounds how long that is believed.
    CLAIMED = "claimed"
    #: Recomputed successfully.
    DONE = "done"
    #: Exhausted retries. Needs a human; alerting reads this.
    FAILED = "failed"


class AnalyticsRecomputeQueue(Base, BigIDMixin):
    """Buckets known to be stale, waiting to be recomputed.

    Why a queue instead of a lookback window
    ----------------------------------------
    The obvious design is "every night, recompute the last N days". It is also
    wrong, and wrong in a way that never surfaces. Everything that mutates an
    already-closed bucket arrives on its own schedule, and none of those
    schedules fit inside N:

    * a **refund** issued in July against a March order changes March's net
      revenue, not July's;
    * a **return** completes weeks after delivery;
    * a **chargeback** can land months later;
    * a **shipment** reaches its terminal RTO state long after dispatch;
    * a **settlement correction** from the gateway restates net receipts for a
      batch of old orders;
    * a **cost-rule edit** can restate an arbitrarily deep range on purpose.

    Pick N = 7 and every one of those silently vanishes from history: the fact
    row updates, the rollup never gets rebuilt, and the dashboard keeps serving
    a stale aggregate forever. Pick N large enough to be safe and the nightly
    job recomputes years of untouched buckets every night. A dirty-bucket queue
    is the only version that is both correct and affordable — the writer that
    caused the change enqueues the bucket it invalidated, whenever that is.

    Enqueue is an upsert
    --------------------
    Writers do not check-then-insert (that races). They issue::

        INSERT INTO analytics_recompute_queue
            (job, bucket_date, dimension_key, tz_generation, reason,
             priority, status, attempts, enqueued_at)
        VALUES (...)
        ON DUPLICATE KEY UPDATE
            status   = 'pending',
            attempts = 0,
            priority = LEAST(priority, VALUES(priority)),
            reason   = VALUES(reason),
            enqueued_at = NOW()

    The UNIQUE key does the work. Fifty refunds against one bucket collapse into
    one queue row instead of fifty duplicate recomputes. A bucket already
    marked `DONE` is **reopened** by the same statement — which is precisely
    what a late refund needs, and what a plain `INSERT IGNORE` would get wrong
    by discarding the new dirt. `LEAST` means an urgent enqueue can raise a
    row's priority but a routine one can never lower an urgent row's.

    Effectively-once processing
    ---------------------------
    A worker claims a batch by setting `status='claimed'`, `claimed_by`,
    `claimed_at` and `claim_expires_at = NOW() + lease`, and refreshes
    `heartbeat_at` while it works. A sweeper returns rows whose
    `claim_expires_at` has passed to `pending`.

    That lease is the whole reason a worker can die without consequence. Without
    it, a process killed mid-recompute leaves its rows `claimed` by a worker
    that no longer exists, and those buckets are stuck **forever** — no error,
    no retry, just a bucket that quietly stops updating. With it, the claim
    simply expires and another worker picks the row up. Recompute is idempotent
    (DELETE + INSERT of the bucket), so a duplicate execution after a false
    expiry costs time and nothing else; that is why an expiring lease is safe
    here where it would not be for, say, sending money.

    `attempts` and `last_error` bound the retries: after the cap the row goes
    `FAILED`, stops burning worker time, and becomes visible to alerting rather
    than looping invisibly.
    """

    __tablename__ = "analytics_recompute_queue"

    # Which aggregation job owns this bucket, e.g. "sales_daily",
    # "product_daily", "cohort_monthly".
    job: Mapped[str] = mapped_column(String(64), nullable=False)

    # Store-local reporting day to rebuild — same calendar as the rollup's
    # bucket_date, interpreted under `tz_generation` below.
    bucket_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Optional dimension slice, when only part of a day is dirty (a single
    # product/gateway/courier). '-' means the whole bucket. NOT NULL because it
    # is inside the UNIQUE key: a NULL here would let unlimited duplicate rows
    # through on MySQL and defeat the dedup this table depends on.
    dimension_key: Mapped[str] = dimension_column(96)

    # Which timezone generation this bucket belongs to. A rebuild enqueues the
    # same dates under a *new* generation, so this must be part of the key or
    # the rebuild would collide with the live queue.
    tz_generation: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="1"
    )

    # RecomputeReason constant — kept because "why is this bucket dirty?" is the
    # first question when a recompute storm shows up.
    reason: Mapped[str] = mapped_column(String(48), nullable=False)

    # Lower runs first. 5 is routine; interactive/manual requests go lower;
    # deep backfills go higher so they never starve live buckets.
    priority: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="5"
    )

    # RecomputeStatus constant.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=RecomputeStatus.PENDING
    )

    # Retry accounting. Reset to 0 by the enqueue upsert so a genuinely new
    # change gets a fresh set of attempts even on a previously failed bucket.
    attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Server default so raw-SQL upserts (the normal enqueue path) need not
    # supply it.
    enqueued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Lease state. `claim_expires_at` in the past + status 'claimed' is what the
    # sweeper looks for; `heartbeat_at` lets a long-running legitimate job
    # extend its lease instead of being reclaimed underneath itself.
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claimed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # The dedup / reopen key. This constraint IS the queue semantics — the
        # ON DUPLICATE KEY UPDATE enqueue above is meaningless without it.
        UniqueConstraint(
            "job",
            "bucket_date",
            "dimension_key",
            "tz_generation",
            name="uq_analytics_recompute_queue_job_date_dim_tz",
        ),
        # The claim query's access path: pending rows, best priority, oldest
        # bucket first.
        Index(
            "ix_analytics_recompute_queue_status_priority_date",
            "status",
            "priority",
            "bucket_date",
        ),
    )


# ===========================================================================
# 4. Pipeline run log
# ===========================================================================
class SyncTrigger:
    """What caused a run to start."""

    #: Scheduled tick.
    CRON = "cron"
    #: An operator pressed the button.
    MANUAL = "manual"
    #: Historical fill over an explicit window.
    BACKFILL = "backfill"
    #: Drain of `analytics_recompute_queue`.
    QUEUE = "queue"


class SyncStatus:
    """Outcome of a run."""

    #: Started, not yet finished. A row stuck here past its expected duration
    #: means the process died without writing a terminal status.
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    #: Some buckets written, some failed. Distinct from FAILED on purpose: the
    #: watermark may still have advanced, and the failures are the actionable
    #: part.
    PARTIAL = "partial"
    #: Another worker held the lock; this tick did nothing. Not an error, and
    #: must not count toward the consecutive-failure alert.
    SKIPPED_LOCKED = "skipped_locked"


class AnalyticsSyncRun(Base, BigIDMixin):
    """Append-only log of every analytics pipeline run.

    Written once when a run starts (`status = RUNNING`) and completed once when
    it terminates. Like `payment_events`, rows are **never UPDATEd after
    completion** and never DELETEd by the application — a run log that can be
    edited after the fact cannot be used to answer "did the pipeline actually
    run on the 3rd?", which is the only reason it exists.

    Why the pipeline needs its own history
    --------------------------------------
    A rollup table cannot report its own absence. If last night's job never
    started, the rollups look exactly like a quiet trading day: the rows for
    yesterday are simply not there, the chart ends a day early, and nobody
    notices until someone asks why the week looks short. The run log is the only
    place a *non-event* leaves a trace.

    `watermark_date`
    ----------------
    The highest bucket_date this job has fully computed. This is what the API
    surfaces as a view's `last_updated_at`, and it is deliberately a **data**
    watermark rather than a clock timestamp: "this job last ran at 04:00" is not
    the same claim as "these numbers include everything through the 27th", and
    only the second one is safe to print next to a revenue figure.

    Two alerts hang off this table
    ------------------------------
    * **Watermark not advancing** (`AlertRuleKey.TRACKING_FAILURE`) — the
      job keeps reporting `SUCCESS` but `watermark_date` is unchanged across
      runs. This catches the failure mode where the pipeline is healthy and
      doing nothing: an empty source window, a stuck lock, a queue that never
      hands out work. A green run log with a frozen watermark is worse than a
      red one, because the dashboard keeps serving yesterday's numbers as if
      they were today's.
    * **Two consecutive failures** (`AlertRuleKey.GA4_SYNC_FAILURE` /
      pipeline health) — one failure is a blip worth retrying; two in a row is
      a broken pipeline and pages a human. `SKIPPED_LOCKED` runs are excluded
      from the streak, otherwise a busy lock would look like an outage.
    """

    __tablename__ = "analytics_sync_runs"

    # Aggregation job name — matches `analytics_recompute_queue.job`.
    job: Mapped[str] = mapped_column(String(64), nullable=False)

    # SyncTrigger constant. NOTE: `trigger` is a MySQL reserved word;
    # SQLAlchemy quotes it automatically, but hand-written SQL must backtick it.
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)

    # SyncStatus constant.
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    # Host/pod/process identifier, so a run can be tied back to a container log.
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Inclusive bucket-date window the run was asked to cover. NULL for
    # queue-driven runs, whose scope is "whatever was claimed".
    window_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    window_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Generation the run computed under. A run under a retired generation is a
    # bug worth being able to see after the fact.
    tz_generation: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="1"
    )

    # Requested vs actually processed. A persistent gap between the two is the
    # signature of a partially failing job that still reports success.
    days_requested: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )
    days_processed: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )

    # Rollup rows written and deleted. Recompute is DELETE + INSERT, so both
    # matter; deletes far exceeding writes means a bucket lost its source rows.
    rows_written: Mapped[int] = count_column()
    rows_deleted: Mapped[int] = count_column()

    # See class docstring — the data watermark surfaced as `last_updated_at`.
    watermark_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Wall-clock duration. Set at completion; NULL while RUNNING.
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Terminal error message. Never store credentials or connection strings.
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Explicit event times — not server defaults, because these describe the
    # run, not the INSERT. `finished_at` NULL while RUNNING.
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # "Last N runs of this job" — the admin health panel.
        Index("ix_analytics_sync_runs_job_started", "job", "started_at"),
        # "Recent failures across all jobs" — the alert evaluator.
        Index("ix_analytics_sync_runs_status_started", "status", "started_at"),
    )


# ===========================================================================
# 5. Server-side GA4 event outbox
# ===========================================================================
class OutboxEventName:
    """GA4 event names delivered from the server. Keep verbatim GA4 spelling."""

    PURCHASE = "purchase"
    REFUND = "refund"


class OutboxStatus:
    """Delivery lifecycle."""

    #: Written, not yet sent.
    PENDING = "pending"
    #: Accepted by GA4's Measurement Protocol.
    DELIVERED = "delivered"
    #: Retries exhausted. Visible to alerting; never silently dropped.
    FAILED = "failed"
    #: Analytics consent was not granted. Recorded, deliberately never sent.
    SUPPRESSED_NO_CONSENT = "suppressed_no_consent"


class ConsentState:
    """Analytics-consent state captured at checkout, at the moment of purchase.

    Captured at write time rather than read at delivery time, because consent is
    a property of the interaction that produced the event, not of whatever the
    cookie banner happens to say fifteen seconds later when the worker runs.
    """

    #: Analytics storage granted.
    GRANTED = "granted"
    #: Analytics granted, advertising storage denied.
    ANALYTICS_ONLY = "analytics_only"
    #: Explicitly declined.
    DENIED = "denied"
    #: No signal available (banner never answered, cookie unreadable).
    UNKNOWN = "unknown"


class AnalyticsEventOutbox(Base, BigIDMixin, CreatedAtMixin):
    """Transactional outbox for server-side GA4 (Measurement Protocol) events.

    Why an outbox rather than a direct call
    ---------------------------------------
    Browser-side purchase tracking loses somewhere between 10% and 40% of
    conversions to ad blockers, closed tabs, and failed redirects back from the
    payment gateway. The fix is to send `purchase` from the server. But a server
    that calls GA4 inline during checkout has coupled taking money to an
    outbound HTTPS call: GA4 slow means checkout slow, GA4 down means either a
    failed order or a lost conversion.

    So the row is written **inside the order-paid transaction**, committing
    atomically with the order-status change. If the transaction rolls back, no
    event exists to send — an event can never describe a purchase that did not
    happen. If it commits, the event is durably owed. A delivery worker then
    drains `PENDING` rows on a ~15-30s loop, entirely outside the request path.

    Exactly-once
    ------------
    The UNIQUE key on `(event_name, transaction_id)` is what makes delivery
    exactly-once rather than at-least-once. Retries, a double webhook, a
    reconciliation job re-processing an order, two workers racing — all collapse
    to one row, so GA4 receives one `purchase` per order. Duplicate revenue in
    GA4 is not a cosmetic bug: it inflates reported ROAS, which changes what the
    business spends money on.

    `transaction_id` is the internal `orders.order_number`, not the gateway's
    payment id. It must match the `transaction_id` the browser would have sent
    so GA4 dedupes browser and server events against each other instead of
    counting the purchase twice.

    Session joining and consent
    ---------------------------
    `client_id` and `session_id` are read from the GA cookies (`_ga`,
    `_ga_<container>`) at checkout and stored here **only when analytics consent
    was granted**. They are what lets the server event join the browser session
    that produced it. Without them GA4 attributes the purchase to
    `(direct)/(none)` with a fresh client id — the revenue still lands, but
    detached from the campaign, source and landing page that earned it, which is
    the one thing the purchase event was sent to establish.

    When consent is **denied**, the purchase is still recorded internally — it
    is a real order and the store's own reporting must be complete regardless of
    a third-party analytics preference — but the row is marked
    `SUPPRESSED_NO_CONSENT` and is never transmitted. Internal analytics and
    third-party transmission are different decisions, and only the second one
    needs consent.

    Order state is the source of truth
    ----------------------------------
    Rows are written from the confirmed-payment path, never from a thank-you
    page view. Reaching `/order-success` proves only that a browser loaded a
    URL: customers reload it, bookmark it, share it, and reach it after an
    abandoned or failed payment. **A success-page URL is never proof of
    purchase; the backend order status is.** Firing on page load is the single
    most common way store analytics ends up reporting revenue that was never
    collected.
    """

    __tablename__ = "analytics_event_outbox"

    # OutboxEventName constant — the GA4 event name, sent verbatim.
    event_name: Mapped[str] = mapped_column(String(40), nullable=False)

    # The internal `orders.order_number` (NOT the gateway payment id). Must
    # match what the browser tag would send so GA4 can dedupe across both.
    transaction_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # Convenience join back to the order. Plain int, no FK — the outbox must
    # survive order deletion, and an FK would block it.
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    # When the purchase happened, not when the row was inserted or delivered.
    # GA4 rejects events older than its backdating limit, so a delivery worker
    # that has been down a long time needs this to decide what is still
    # sendable rather than silently posting events GA4 will discard.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Fully-formed GA4 payload: items[], value, currency, tax, shipping,
    # coupon. Built at write time from the order as it was then, so a later
    # edit to the order cannot retroactively change what was reported.
    # NEVER put customer email/phone/address in here.
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    # GA cookie identifiers, captured at checkout ONLY with analytics consent.
    # NULL means the server event cannot join the browser session.
    client_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ConsentState at the moment of purchase.
    consent_state: Mapped[str] = mapped_column(String(24), nullable=False)

    # OutboxStatus constant.
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=OutboxStatus.PENDING
    )

    attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # The exactly-once key. See class docstring — this is not an
        # optimisation, it is the delivery guarantee.
        UniqueConstraint(
            "event_name",
            "transaction_id",
            name="uq_analytics_event_outbox_event_txn",
        ),
        # The drain query: oldest pending first.
        Index("ix_analytics_event_outbox_status_occurred", "status", "occurred_at"),
    )


# ===========================================================================
# 6. Budgets / targets
# ===========================================================================
class BudgetGranularity:
    """Period length a budget row covers."""

    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class AnalyticsBudget(Base, BigIDMixin, TimestampMixin):
    """Planned targets, so actuals can be reported against intent.

    A revenue number alone says nothing about whether the month is going well.
    This table holds the other half — what the business said it intended — so
    the dashboard can show variance instead of a bare figure, and so
    `AlertRuleKey.SALES_DROP` has something to compare against beyond the
    previous period.

    Unlike every other table in this module, budgets are **authored, not
    derived**, so this is the one place carrying `TimestampMixin` rather than
    `CreatedAtMixin`: a target genuinely does get revised mid-quarter, and
    `updated_at` records when. (The append-only tables here are never UPDATEd,
    so an `updated_at` on them would be a permanently misleading column.)

    A budget can be global (`dimension = '-'`) or scoped to one dimension value
    — a category's revenue target, a channel's spend cap.
    """

    __tablename__ = "analytics_budgets"

    # Inclusive period bounds, in store-local reporting days. `period_end` is
    # stored rather than derived from granularity so a partial or custom period
    # (a 5-week launch quarter) is expressible without special-casing.
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    # BudgetGranularity constant — how the period should be labelled and
    # pro-rated for partial-period pacing.
    granularity: Mapped[str] = mapped_column(String(16), nullable=False)

    # Which metric the target is for: "net_revenue", "orders", "aov",
    # "marketing_spend", "contribution_margin_3".
    metric: Mapped[str] = mapped_column(String(64), nullable=False)

    # Optional scoping. '-' / '-' means store-wide. NOT NULL sentinels because
    # both sit inside the UNIQUE key below.
    dimension: Mapped[str] = dimension_column(32)
    dimension_value: Mapped[str] = dimension_column(64)

    # The target amount. Deliberately NOT `money_column()`: that helper carries
    # a 0 server_default meaning "measured, and it was zero", which is right for
    # an aggregation job and wrong here. A budget is typed by a human, and a
    # row that silently defaulted to 0 would report every actual as infinite
    # overachievement against a target nobody set. Insert must supply a value.
    budget_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)

    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default="INR"
    )
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Admin who set the target. Plain int, no FK.
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        # One target per metric per scope per period start. Two rows would make
        # "am I ahead of budget?" answerable two different ways.
        UniqueConstraint(
            "period_start",
            "granularity",
            "metric",
            "dimension",
            "dimension_value",
            name="uq_analytics_budgets_period_gran_metric_dim",
        ),
        # Period lookup for the current-period pacing panel.
        Index(
            "ix_analytics_budgets_metric_period",
            "metric",
            "period_start",
            "period_end",
        ),
    )


# ===========================================================================
# 7. Alerts
# ===========================================================================
class AlertSeverity:
    """How loudly to complain."""

    #: Worth knowing, no action implied.
    INFO = "info"
    #: Someone should look today.
    WARNING = "warning"
    #: Money or data integrity is at risk right now.
    CRITICAL = "critical"


class AlertStatus:
    """Alert lifecycle. `open` -> `acknowledged` -> `resolved`, or `muted`."""

    #: Detected, nobody has responded.
    OPEN = "open"
    #: A human has seen it and owns it.
    ACKNOWLEDGED = "acknowledged"
    #: The underlying condition cleared or was fixed.
    RESOLVED = "resolved"
    #: Known and deliberately silenced. Distinct from RESOLVED so a muted
    #: alert is never mistaken for a fixed one.
    MUTED = "muted"


class AlertRuleKey:
    """Stable identifiers for the detection rules.

    Constants rather than free text because `rule_key` is what dedup, muting and
    routing all match on — a typo would create a brand-new alert stream that
    nobody has muted, acknowledged, or routed anywhere.

    They split into two families, and the second is the important one.
    """

    # -- Business-signal rules: something in the store changed -------------
    #: Revenue/orders materially below the comparison baseline.
    SALES_DROP = "sales_drop"
    #: Revenue far above baseline — often good, sometimes a pricing/coupon bug.
    REVENUE_SPIKE = "revenue_spike"
    #: Conversion rate fell — frequently a broken checkout, not lost demand.
    CONVERSION_DROP = "conversion_drop"
    #: Payment failure rate spiked — gateway trouble, and it costs money hourly.
    PAYMENT_FAILURE_SPIKE = "payment_failure_spike"
    #: Refunds above normal.
    REFUND_SPIKE = "refund_spike"
    #: Returns above normal — usually a product or listing-accuracy problem.
    RETURN_SPIKE = "return_spike"
    #: RTO rate spiked — address quality, COD abuse, or a courier going bad.
    RTO_SPIKE = "rto_spike"
    #: A seller is about to stock out of something that is actually selling.
    OUT_OF_STOCK_RISK = "out_of_stock_risk"
    #: Marketing spend rising faster than the revenue it buys.
    MARKETING_COST_SPIKE = "marketing_cost_spike"
    #: Contribution margin after marketing went negative. Every additional
    #: order at this point loses money, so this is the one business rule that
    #: is CRITICAL by default.
    NEGATIVE_CM3 = "negative_cm3"

    # -- Instrumentation rules: the numbers themselves are not trustworthy --
    # These exist because a broken pipeline looks exactly like a calm day. Every
    # rule above is silent when the data stops arriving, so without these three
    # the alerting system's failure mode is total silence.
    #: Pipeline ran but the watermark did not advance, or events stopped
    #: arriving. The dashboard is serving stale numbers as if they were current.
    TRACKING_FAILURE = "tracking_failure"
    #: `analytics_event_outbox` is accumulating FAILED rows — server-side
    #: conversions are not reaching GA4 and attribution is degrading now.
    GA4_SYNC_FAILURE = "ga4_sync_failure"
    #: A bucket resolved a cost component to MISSING because no
    #: `analytics_cost_rules` row covered it. Margin figures for that period are
    #: INCOMPLETE and must not be read as final.
    MISSING_COST_DATA = "missing_cost_data"


class AnalyticsAlert(Base, BigIDMixin, CreatedAtMixin):
    """One detected deviation, with its lifecycle.

    Append-and-acknowledge rather than append-only: rows are created by the
    detector and then updated exactly along the `AlertStatus` path by humans.
    The measured values (`expected_*`, `actual_value`, `bucket_date`, `context`)
    are never rewritten after detection — an alert has to be able to justify
    itself later, including when the condition has since cleared and the numbers
    on the dashboard no longer show what triggered it.

    `expected_low` / `expected_high` / `actual_value` are stored as
    `Numeric(18,4)`, wide enough for a currency total and precise enough for a
    conversion rate, because the same three columns carry both. An alert that
    records only "sales dropped" is not actionable; one that records "expected
    ₹180,000–₹240,000, got ₹41,300 on 2026-03-14" is.

    `context` holds the detector's supporting evidence — comparison window,
    sample size, the top contributing dimension values. It is the difference
    between an alert a human can triage in ten seconds and one that requires
    reproducing the query by hand.
    """

    __tablename__ = "analytics_alerts"

    # AlertRuleKey constant.
    rule_key: Mapped[str] = mapped_column(String(64), nullable=False)

    # AlertSeverity constant. Drives routing (digest vs immediate).
    severity: Mapped[str] = mapped_column(String(16), nullable=False)

    # Metric that tripped: "net_revenue", "conversion_rate", "rto_rate".
    metric: Mapped[str] = mapped_column(String(64), nullable=False)

    # Optional scoping — '-' / '-' for a store-wide alert.
    dimension: Mapped[str] = dimension_column(32)
    dimension_value: Mapped[str] = dimension_column(64)

    # The band the detector expected and what it actually saw. NULL for rules
    # with no numeric band (TRACKING_FAILURE, GA4_SYNC_FAILURE).
    expected_low: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 4), nullable=True
    )
    expected_high: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 4), nullable=True
    )
    actual_value: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 4), nullable=True
    )

    # Reporting day the condition applies to. NULL for rules that are about the
    # system rather than a bucket.
    bucket_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # When the detector fired. Explicit event time, not an insert default.
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # AlertStatus constant.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=AlertStatus.OPEN
    )

    # Who acknowledged, and when. Plain int, no FK — the alert history must
    # outlive the staff account that handled it.
    acknowledged_by_user_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # What was actually done about it. The institutional memory that stops the
    # same alert being re-diagnosed from scratch next quarter.
    resolution_note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Detector evidence: comparison window, sample size, top contributors.
    context: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        # The alerts panel: open alerts, newest first.
        Index("ix_analytics_alerts_status_detected", "status", "detected_at"),
        # Per-rule history — powers "has this fired before?" dedup and
        # suppression of a rule that is flapping.
        Index("ix_analytics_alerts_rule_detected", "rule_key", "detected_at"),
    )
