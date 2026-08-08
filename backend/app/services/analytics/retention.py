"""Data retention and pruning for the analytics subsystem.

Every retention rule implemented here is transcribed from a model docstring, not
invented in this file. ``AggOrderHourly`` says "pruned after 90 days";
``AggCustomerSnapshot`` says "daily rows for 90 days, then month-end rows only";
``AggGeoDaily`` says "pincode-grain rows are pruned to state grain after 180
days". Those docstrings are the policy; this module is the implementation, and
where the two ever disagree the docstring wins. The handful of horizons that no
docstring states are marked :data:`DECIDED_HERE` in the policy table below so a
reader can tell in one glance which numbers have an owner and which do not.

Four rules govern every policy in this file
===========================================

**1. Bounded batches, and a per-run ceiling.**
A single unbounded ``DELETE`` over a year of rows holds a table lock for as long
as it takes to run, and on ``analytics_recompute_queue`` that means every worker
trying to claim a bucket waits behind it — a prune becomes a pipeline outage.
So each policy deletes in capped batches (:data:`DEFAULT_BATCH_SIZE`, matching
the worker's ``ANALYTICS_WORKER_PRUNE_BATCH``), commits between them, and stops
at a per-run ceiling (:data:`DEFAULT_MAX_ROWS_PER_POLICY`). A run that hits the
ceiling reports ``capped: True`` and the next run continues; convergence over a
few ticks is strictly better than one long lock.

**2. ``dry_run`` is real.**
It executes the same scan, in the same batch order, against the same predicates,
and reports the same numbers a real run would — then deletes nothing and rolls
back. An operator has to be able to see the blast radius *before* authorising
it, and a "preview" that estimates rather than measures is exactly the thing
nobody should authorise anything on.

**3. The geo collapse aggregates before it deletes, in one transaction.**
This is the only irreversible policy in the file. Every other rollup row it
removes can be rebuilt from the facts; the pincode rows in ``agg_geo_daily``
cannot, because there is no pincode-grain fact table to rebuild them from. So
the collapse sums a batch up to state grain, adds it into the state-grain row,
and deletes the pincode originals **inside one transaction**. If any part fails,
the whole batch rolls back and the pincode rows are still there. Because the
upsert *adds*, a run interrupted between batches leaves state-grain totals
correct — half-collapsed is still exactly right.

**4. Never prune a bucket the aggregator has not reached.**
Pruning ahead of the aggregator loses a day that nobody will notice is missing:
the rollup for it is simply absent, and an absent rollup looks identical to a
quiet trading day. Every bucket-grained policy therefore clamps its horizon to
``MAX(analytics_sync_runs.watermark_date)`` for the job that writes the table
(see :func:`_aggregated_through`). No watermark means no pruning at all, which
is the correct failure direction — a table that grows is recoverable, a day that
was deleted before it was aggregated is not.

And one more, because a prune that leaves no trace is indistinguishable from a
prune that never ran: **every call writes an** ``analytics_sync_runs`` **row**,
under the job name :data:`PRUNE_JOB_NAME`, so deletion is as auditable as
aggregation. It carries ``watermark_date = NULL`` deliberately — pruning does
not advance any job's data watermark, and writing one would corrupt the
freshness figure the admin health panel reads.

Relationship to what already prunes
===================================
Two prunes already exist and this module deliberately does not replace either:

* ``AnalyticsWorker._prune`` sweeps ``analytics_recompute_queue`` ``done`` rows
  at ``ANALYTICS_WORKER_RETENTION_DAYS`` (default **30**), which is a subset of
  this module's 90-day horizon. Both are safe together; the tighter one simply
  wins in practice.
* ``CustomerSnapshotJob._prune_expired`` enforces the snapshot policy inside the
  aggregation transaction, as an unbounded ``DELETE``. That is a correctness
  guarantee (the job refuses to leave rows the policy forbids) and this module is
  the bounded, previewable, audited version of the same rule. The constant is
  imported from that job rather than restated, so the two can never drift.

Wiring it to the worker
=======================
:func:`prune_all` owns nothing but the session it is handed and commits per
batch, which is the shape ``AnalyticsWorker._rollup_tick`` already expects of a
prune phase::

    outcome["pruned"] = self._guard(
        "prune",
        lambda: prune_all(db, batch_size=self.config.prune_batch),
        default={},
    )

It never raises: a policy that fails is recorded against that policy and the
others still run, because one broken predicate must not stop the table that is
actually growing from being pruned.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.models.analytics_base import DIMENSION_UNKNOWN
from app.models.analytics_control import (
    AnalyticsEventOutbox,
    AnalyticsRecomputeQueue,
    AnalyticsSyncRun,
    OutboxStatus,
    RecomputeStatus,
    SyncStatus,
    SyncTrigger,
)
from app.models.analytics_facts import CartEvent
from app.models.analytics_rollups import (
    AggCustomerSnapshot,
    AggGeoDaily,
    AggOrderHourly,
)
from app.services.analytics.aggregation import JOBS
from app.services.analytics.aggregation.jobs_customer import RETENTION_DAILY_DAYS
from app.services.analytics.timebox import day_bounds_utc, local_day, store_timezone

__all__ = [
    "PRUNE_JOB_NAME",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_MAX_ROWS_PER_POLICY",
    "DECIDED_HERE",
    "FROM_MODEL_DOCSTRING",
    "RetentionPolicy",
    "POLICIES",
    "policy_keys",
    "describe_policies",
    "prune_all",
]

log = logging.getLogger("analytics.retention")

#: ``analytics_sync_runs.job`` for a prune. Deliberately NOT an aggregation job
#: name: writing under ``order_hourly`` would put prune runs into that job's
#: health history and its ``MAX(watermark_date)``, which is the number the
#: dashboard prints as "these figures include everything through …". The admin
#: health panel already tolerates names it does not recognise (it unions
#: ``JOBS`` with every name seen in the run log), so a distinct name shows up as
#: its own row rather than corrupting someone else's.
PRUNE_JOB_NAME = "retention_prune"

#: Rows deleted per statement. Matches the worker's ``ANALYTICS_WORKER_PRUNE_BATCH``
#: default so both prunes hold locks for comparable durations.
DEFAULT_BATCH_SIZE = 5_000

#: Ceiling per policy per run. A year of neglected `cart_events` is tens of
#: millions of rows; deleting them all in one call would run for hours while
#: holding a connection the worker needs for aggregation. Converging over
#: several hourly ticks costs nothing and blocks nothing.
DEFAULT_MAX_ROWS_PER_POLICY = 50_000

#: Provenance labels for :attr:`RetentionPolicy.source`. The distinction is not
#: decoration: a horizon with a docstring behind it has a documented owner and a
#: stated reason, and one without is a number this module chose, which is the
#: kind of thing that should be easy to find and challenge.
FROM_MODEL_DOCSTRING = "model docstring"
DECIDED_HERE = "decided here"

# ---------------------------------------------------------------------------
# Horizons
# ---------------------------------------------------------------------------
#: `AggOrderHourly`: "Pruned after 90 days … the daily table remains the
#: long-term record."
HOURLY_RETENTION_DAYS = 90

#: `AggCustomerSnapshot`: daily rows for 90 days, month-end rows beyond.
#: Imported from `jobs_customer` rather than restated so the job's inline prune
#: and this one can never disagree about where the boundary is.
SNAPSHOT_DAILY_DAYS = RETENTION_DAILY_DAYS

#: `AggGeoDaily`: "Pincode-grain rows are pruned to state grain after 180 days."
GEO_PINCODE_DAYS = 180

#: Control-plane terminal rows. No model docstring states a number — and
#: `AnalyticsSyncRun`'s docstring says the opposite (see `_prune_sync_runs`).
CONTROL_PLANE_DAYS = 90

#: `CartEvent` says only that "a retention/aggregation policy for raw rows
#: should exist before the table gets large". 400 days is chosen so a
#: year-over-year comparison of the funnel still has raw sessions on both sides
#: with a month of slack; beyond it the aggregate in `agg_funnel_daily` is the
#: record.
CART_EVENT_DAYS = 400

#: CSP violation reports. Nothing in this deployment stores one — see
#: `_prune_csp_reports`.
CSP_REPORT_DAYS = 30

#: Measure columns on `agg_geo_daily`, in the order the collapse sums them.
#: Every one is a sum or a count, never an average or a rate, which is exactly
#: what makes re-aggregating pincode -> state arithmetically valid.
_GEO_MEASURES: tuple[str, ...] = (
    "orders",
    "net_revenue",
    "units",
    "cod_orders",
    "prepaid_orders",
    "delivered",
    "rto",
    "sum_delivery_seconds",
    "n_delivery",
)

#: Sync-run statuses a prune may remove. `FAILED` and `PARTIAL` are what
#: alerting reads and what a human investigates months later; `RUNNING` past its
#: expected duration is the only trace an OOM-killed pipeline leaves. Deleting
#: any of the three would silently close the only report of a problem, which is
#: the same reasoning the worker uses for never pruning `failed` queue rows.
_PRUNABLE_SYNC_STATUSES = (SyncStatus.SUCCESS, SyncStatus.SKIPPED_LOCKED)


def _utcnow() -> datetime:
    """Naive UTC, matching the convention in `runner` and `queue`.

    ``DateTime(timezone=True)`` is a no-op on MySQL, so an aware datetime
    written to one of these columns reads back naive and the next comparison
    raises. One convention, everywhere in this subsystem.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ===========================================================================
# The policy table
# ===========================================================================
@dataclass(frozen=True)
class RetentionPolicy:
    """One retention rule: what it prunes, how far back, and who decided.

    ``watermark_job`` is the aggregation job that *writes* the table. When it is
    set, the horizon is clamped to that job's data watermark so a prune can
    never overtake the aggregator. ``None`` means there is no aggregator to
    overtake — the control-plane tables are keyed off their own completion
    timestamps, not off a reporting bucket.
    """

    key: str
    table: str
    horizon_days: int
    source: str
    summary: str
    watermark_job: str | None = None
    #: Set for policies that do not delete rows outright.
    rewrites: bool = False


POLICIES: tuple[RetentionPolicy, ...] = (
    RetentionPolicy(
        key="agg_order_hourly",
        table=AggOrderHourly.__tablename__,
        horizon_days=HOURLY_RETENTION_DAYS,
        source=FROM_MODEL_DOCSTRING,
        watermark_job="order_hourly",
        summary=(
            "Drop hourly order buckets older than 90 store-local days. The table "
            "is 24x the daily one and answers only 'what does a normal Tuesday "
            "look like' and 'when did checkout break'; agg_order_daily is the "
            "long-term record."
        ),
    ),
    RetentionPolicy(
        key="agg_customer_snapshot",
        table=AggCustomerSnapshot.__tablename__,
        horizon_days=SNAPSHOT_DAILY_DAYS,
        source=FROM_MODEL_DOCSTRING,
        watermark_job="customer_snapshot",
        summary=(
            "Beyond 90 days keep month-end snapshot rows only. The grain is "
            "customers x days — 50k customers is 18M rows a year — and long-term "
            "LTV and cohort curves need a monthly sample, not a daily one."
        ),
    ),
    RetentionPolicy(
        key="agg_geo_daily_pincode",
        table=AggGeoDaily.__tablename__,
        horizon_days=GEO_PINCODE_DAYS,
        source=FROM_MODEL_DOCSTRING,
        # `shipment_geo_daily` (jobs_finance) is what writes this table — the
        # name does not match the table's, which is exactly why it is named
        # here rather than derived from it.
        watermark_job="shipment_geo_daily",
        rewrites=True,
        summary=(
            "Re-aggregate pincode-grain geo rows older than 180 days up to state "
            "grain, then delete the originals. Row count is the lesser reason; "
            "the binding one is that a small pincode plus a date plus an order "
            "count is personal-data adjacent."
        ),
    ),
    RetentionPolicy(
        key="analytics_recompute_queue",
        table=AnalyticsRecomputeQueue.__tablename__,
        horizon_days=CONTROL_PLANE_DAYS,
        source=DECIDED_HERE,
        summary=(
            "Delete `done` queue rows processed more than 90 days ago. `failed` "
            "rows are never pruned — they are what alerting reads, and deleting "
            "one closes the only report of a bucket that will never rebuild."
        ),
    ),
    RetentionPolicy(
        key="analytics_sync_runs",
        table=AnalyticsSyncRun.__tablename__,
        horizon_days=CONTROL_PLANE_DAYS,
        source=DECIDED_HERE,
        summary=(
            "Delete `success` / `skipped_locked` runs finished more than 90 days "
            "ago. Failures, partials, still-RUNNING rows and the row carrying "
            "each job's highest watermark are kept indefinitely."
        ),
    ),
    RetentionPolicy(
        key="analytics_event_outbox",
        table=AnalyticsEventOutbox.__tablename__,
        horizon_days=CONTROL_PLANE_DAYS,
        source=DECIDED_HERE,
        summary=(
            "Delete GA4 outbox rows delivered more than 90 days ago. Pending, "
            "failed and consent-suppressed rows are kept: the first two are debts "
            "still owed, and the third is the record of a decision not to send."
        ),
    ),
    RetentionPolicy(
        key="cart_events",
        table=CartEvent.__tablename__,
        horizon_days=CART_EVENT_DAYS,
        source=DECIDED_HERE,
        watermark_job="funnel_daily",
        summary=(
            "Delete raw funnel events older than 400 store-local days; beyond "
            "that agg_funnel_daily is the record. Clamped to funnel_daily's "
            "watermark — cart_events cannot be backfilled from anywhere, so a "
            "day deleted before it was aggregated is gone for good."
        ),
    ),
    RetentionPolicy(
        key="csp_reports",
        table="csp_reports",
        horizon_days=CSP_REPORT_DAYS,
        source=DECIDED_HERE,
        summary=(
            "NOT IMPLEMENTED. No CSP violation report is persisted anywhere in "
            "this deployment — the middleware sets a Content-Security-Policy "
            "header with no report-uri and there is no receiving endpoint or "
            "table. The policy is declared so it engages the day one exists."
        ),
    ),
)

_BY_KEY: dict[str, RetentionPolicy] = {p.key: p for p in POLICIES}


def policy_keys() -> tuple[str, ...]:
    """Every policy key, in run order."""
    return tuple(p.key for p in POLICIES)


def describe_policies() -> list[dict[str, Any]]:
    """The policy table as plain data — for docs, admin UI and tests.

    Exposed so the retention story has exactly one source. A policy documented
    in a Markdown file and implemented in Python is a policy that will disagree
    with itself within two quarters.
    """
    return [
        {
            "key": p.key,
            "table": p.table,
            "horizon_days": p.horizon_days,
            "source": p.source,
            "watermark_job": p.watermark_job,
            "rewrites": p.rewrites,
            "summary": p.summary,
        }
        for p in POLICIES
    ]


# ===========================================================================
# Run context and per-policy outcome
# ===========================================================================
@dataclass
class _Context:
    """Everything a policy needs that is the same for all of them."""

    now: datetime          # naive UTC
    tz: ZoneInfo
    today: date            # store-local reporting day of `now`
    batch_size: int
    max_rows: int
    dry_run: bool


@dataclass
class _Outcome:
    """What one policy did, or would have done."""

    rows: int = 0
    batches: int = 0
    max_batch_rows: int = 0
    capped: bool = False
    cutoff: Any = None
    skipped: str | None = None
    collapsed_into: int | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def as_dict(self, policy: RetentionPolicy) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "table": policy.table,
            "horizon_days": policy.horizon_days,
            "source": policy.source,
            "cutoff": self.cutoff,
            "rows": self.rows,
            "batches": self.batches,
            "max_batch_rows": self.max_batch_rows,
            "capped": self.capped,
            "skipped": self.skipped,
            "warnings": list(self.warnings),
            "error": self.error,
        }
        if self.collapsed_into is not None:
            payload["collapsed_into"] = self.collapsed_into
        return payload


# ===========================================================================
# Primitives
# ===========================================================================
def _scan(
    db: Session,
    sql: str,
    params: Mapping[str, Any],
    *,
    limit: int,
    offset: int,
) -> list[Any]:
    """One bounded page of candidate rows.

    Select-then-delete-by-primary-key rather than ``DELETE ... LIMIT``: the
    predicates here are not all simple (``LAST_DAY()``, a NOT IN over row
    constructors), the delete then touches a known set of PKs and holds the
    narrowest possible lock, and — the reason it matters most — the same scan
    is exactly what a dry run executes, so a preview and a real run can be
    proven to see the same rows.

    ``offset`` is 0 for a real run, because each committed batch removes the rows
    the previous page returned. A dry run deletes nothing, so it pages forward
    instead; that is the only difference between the two code paths.
    """
    return list(
        db.execute(text(sql), {**params, "limit": int(limit), "offset": int(offset)}).all()
    )


def _delete_ids(db: Session, table: str, ids: Sequence[int]) -> int:
    """Delete an explicit set of primary keys.

    ``table`` is only ever a ``__tablename__`` read off a model in
    :data:`POLICIES` — never a caller-supplied string — which is what makes the
    f-string safe. The ids are bound parameters.
    """
    if not ids:
        return 0
    placeholders = ", ".join(f":id{i}" for i in range(len(ids)))
    params = {f"id{i}": int(value) for i, value in enumerate(ids)}
    result = db.execute(
        text(f"DELETE FROM {table} WHERE id IN ({placeholders})"), params
    )
    return int(result.rowcount or 0)


def _batched(
    db: Session,
    ctx: _Context,
    *,
    table: str,
    sql: str,
    params: Mapping[str, Any],
    on_batch: Callable[[Session, Sequence[Any], bool], int] | None = None,
) -> tuple[int, int, int, bool, int]:
    """The batch loop every policy shares.

    Returns ``(rows, batches, max_batch_rows, capped, extra)`` where ``extra`` is
    whatever ``on_batch`` accumulated (rows written by the geo collapse; 0 for
    every plain delete).

    ``on_batch`` runs **before** the delete and inside the same transaction, so
    a policy that has to preserve information before removing it either does
    both or does neither.
    """
    rows = batches = max_batch = extra = 0
    offset = 0

    while rows < ctx.max_rows:
        limit = min(ctx.batch_size, ctx.max_rows - rows)
        batch = _scan(db, sql, params, limit=limit, offset=offset)
        if not batch:
            break
        try:
            if on_batch is not None:
                extra += on_batch(db, batch, ctx.dry_run)
            if ctx.dry_run:
                # Belt and braces. `on_batch` already refuses to write in dry
                # mode; rolling back guarantees it even if a future policy
                # forgets, which is the only guarantee worth giving an operator
                # who is about to authorise a deletion.
                db.rollback()
                offset += len(batch)
            else:
                _delete_ids(db, table, [row.id for row in batch])
                db.commit()
        except Exception:
            db.rollback()
            raise
        rows += len(batch)
        batches += 1
        max_batch = max(max_batch, len(batch))

    # Was the ceiling the reason we stopped, or did we run out of rows? The
    # difference decides whether the next tick has work to do, and guessing
    # would make `capped` unusable for exactly that.
    capped = False
    if rows >= ctx.max_rows:
        capped = bool(_scan(db, sql, params, limit=1, offset=offset if ctx.dry_run else 0))

    return rows, batches, max_batch, capped, extra


def _aggregated_through(db: Session, job_name: str, *, as_of: datetime) -> date | None:
    """The highest bucket ``job_name`` has fully computed, as it stood at ``as_of``.

    ``MAX(watermark_date)`` over the job's runs, because
    ``analytics_sync_runs.watermark_date`` is per-run by design — a queue drain
    triggered by a July refund against a March order legitimately reports March,
    so the job's freshness is the maximum over runs and never the latest one.

    Filtered on ``started_at <= as_of`` so the answer is "what had been
    aggregated at that moment", not "what has been aggregated since". A prune
    reasoning about a point in time must not be handed a watermark from the
    future.
    """
    return db.execute(
        select(func.max(AnalyticsSyncRun.watermark_date)).where(
            AnalyticsSyncRun.job == job_name,
            AnalyticsSyncRun.started_at <= as_of,
        )
    ).scalar()


def _bucket_cutoff(
    db: Session, policy: RetentionPolicy, ctx: _Context, outcome: _Outcome
) -> date | None:
    """The exclusive bucket-date boundary this run may prune below.

    ``None`` means "prune nothing", and the caller must honour it. Three cases:

    * **No aggregator declared** — the horizon is the only bound.
    * **Declared but not registered** in ``JOBS``: the table has no aggregator in
      this deployment, so there is nothing to overtake. Warned about rather than
      silently skipped, because a job that exists everywhere except here is a
      deployment gap worth seeing in the run log.
    * **Registered with no watermark** — the job has never completed a bucket, so
      *every* bucket is potentially un-aggregated and nothing may be pruned.
    """
    horizon = ctx.today - timedelta(days=policy.horizon_days)
    if policy.watermark_job is None:
        return horizon

    if policy.watermark_job not in JOBS:
        outcome.warnings.append(
            f"no {policy.watermark_job!r} aggregation job is registered in this "
            "deployment, so there is no watermark to outrun; the horizon is the "
            "only bound on this policy"
        )
        return horizon

    watermark = _aggregated_through(db, policy.watermark_job, as_of=ctx.now)
    if watermark is None:
        outcome.skipped = "awaiting_watermark"
        outcome.warnings.append(
            f"{policy.watermark_job!r} has never recorded a watermark, so no "
            "bucket can be proven aggregated; pruning nothing"
        )
        return None

    # Buckets up to and including the watermark are aggregated, so the exclusive
    # bound is the day after it.
    bound = watermark + timedelta(days=1)
    if bound < horizon:
        outcome.warnings.append(
            f"clamped to {policy.watermark_job!r}'s watermark {watermark} "
            f"(horizon alone would have pruned below {horizon})"
        )
        return bound
    return horizon


# ===========================================================================
# The policies
# ===========================================================================
def _prune_order_hourly(
    db: Session, policy: RetentionPolicy, ctx: _Context
) -> _Outcome:
    """`agg_order_hourly`: drop whole reporting days older than the horizon.

    Rows are removed by bucket date, not by hour, so a day is either entirely
    present or entirely gone. A partially pruned day would render as a real
    trading day with a hole in the middle of it.
    """
    outcome = _Outcome()
    cutoff = _bucket_cutoff(db, policy, ctx, outcome)
    outcome.cutoff = cutoff
    if cutoff is None:
        return outcome

    sql = (
        f"SELECT id FROM {policy.table} WHERE bucket_date < :cutoff "
        "ORDER BY bucket_date, bucket_hour, id LIMIT :limit OFFSET :offset"
    )
    (
        outcome.rows,
        outcome.batches,
        outcome.max_batch_rows,
        outcome.capped,
        _,
    ) = _batched(db, ctx, table=policy.table, sql=sql, params={"cutoff": cutoff})
    return outcome


def _prune_customer_snapshot(
    db: Session, policy: RetentionPolicy, ctx: _Context
) -> _Outcome:
    """`agg_customer_snapshot`: keep month-end rows, drop the rest past 90 days.

    ``LAST_DAY()`` is MySQL's month-end function, and expressing "keep only
    month-end rows beyond the window" as one predicate the database evaluates is
    what stops this from becoming a list of dates assembled in Python — which
    would be wrong for February, and wrong again in a leap year.

    The same predicate as ``CustomerSnapshotJob._prune_expired``, deliberately:
    that one runs inside the aggregation transaction for one bucket and is
    unbounded, this one is bounded, previewable and audited. They must agree, and
    they share :data:`SNAPSHOT_DAILY_DAYS` so they cannot drift apart.
    """
    outcome = _Outcome()
    cutoff = _bucket_cutoff(db, policy, ctx, outcome)
    outcome.cutoff = cutoff
    if cutoff is None:
        return outcome

    sql = (
        f"SELECT id FROM {policy.table} "
        "WHERE bucket_date < :cutoff AND bucket_date <> LAST_DAY(bucket_date) "
        "ORDER BY bucket_date, id LIMIT :limit OFFSET :offset"
    )
    (
        outcome.rows,
        outcome.batches,
        outcome.max_batch_rows,
        outcome.capped,
        _,
    ) = _batched(db, ctx, table=policy.table, sql=sql, params={"cutoff": cutoff})
    return outcome


def _collapse_geo_batch(db: Session, rows: Sequence[Any], dry_run: bool) -> int:
    """Sum one batch of pincode rows into their state-grain rows.

    Adds rather than overwrites (``col = col + VALUES(col)``), which is the exact
    opposite of the rule ``jobs._upsert`` enforces — and correct here for the
    opposite reason. An aggregation job recomputes a bucket from source and must
    overwrite, or a retry doubles the day. This is not a recomputation: the
    pincode rows being folded in are *deleted in the same transaction*, so their
    contribution is added exactly once and cannot be re-added. That atomicity is
    what makes the accumulate safe, and it is why the delete may never be split
    away from the upsert.

    The state-grain destination may already exist — as a genuine "pincode
    unknown" row from the aggregator, or as the product of an earlier batch of
    this same collapse — so the add is the only correct merge in both cases.

    Returns the number of state-grain rows presented, for ``rows_written``.
    """
    grouped: dict[tuple[date, str, int], dict[str, Any]] = {}
    for row in rows:
        mapping = row._mapping
        key = (mapping["bucket_date"], mapping["state"], int(mapping["tz_generation"]))
        bucket = grouped.get(key)
        if bucket is None:
            bucket = grouped[key] = {measure: 0 for measure in _GEO_MEASURES}
        for measure in _GEO_MEASURES:
            bucket[measure] += mapping[measure]

    if dry_run or not grouped:
        return len(grouped)

    computed_at = _utcnow()
    values = [
        {
            "bucket_date": bucket_date,
            "state": state,
            # '-' is a real, documented value on this column: "aggregated to
            # state grain, or pincode unknown". It is not a NULL stand-in.
            "pincode": DIMENSION_UNKNOWN,
            "tz_generation": tz_generation,
            "computed_at": computed_at,
            **measures,
        }
        for (bucket_date, state, tz_generation), measures in grouped.items()
    ]
    statement = mysql_insert(AggGeoDaily).values(values)
    db.execute(
        statement.on_duplicate_key_update(
            **{
                measure: getattr(AggGeoDaily, measure) + statement.inserted[measure]
                for measure in _GEO_MEASURES
            },
            # The row genuinely was rewritten now. `computed_at` surfaces as the
            # view's `last_updated_at`, so it must reflect the write that
            # actually happened rather than the aggregation that preceded it.
            computed_at=statement.inserted.computed_at,
        )
    )
    return len(values)


def _prune_geo_pincode(db: Session, policy: RetentionPolicy, ctx: _Context) -> _Outcome:
    """`agg_geo_daily`: collapse pincode grain to state grain past 180 days.

    **The one policy in this file where a bug is irreversible.** Every other row
    it removes is derived from a fact table and can be rebuilt; there is no
    pincode-grain fact anywhere in this schema, so a pincode row deleted without
    first being summed into its state is data that cannot be recovered from
    anything. Hence: aggregate, then delete, in one transaction, per batch.

    Known interaction with the aggregator, stated because it is not visible from
    here. ``ShipmentGeoDailyJob`` (``jobs_finance``) rebuilds this table with
    Pattern B — DELETE the whole ``(bucket_date, tz_generation)`` then INSERT —
    and it does **not** refuse to rewrite buckets older than
    :data:`GEO_PINCODE_DAYS` the way ``CustomerSnapshotJob`` refuses aged-out
    daily rows. Two consequences, and only the first is benign:

    * **Totals stay correct.** A full replace wipes the collapsed ``'-'`` row
      along with everything else, so re-collapsing the fresh pincode rows cannot
      double-count. Had the job been an upsert instead, it would.
    * **Pincode history reappears** for an aged day until the next prune, which
      weakens the privacy half of the 180-day horizon to "at most one prune
      interval" rather than "never". Recomputing a year-old bucket is rare (it
      takes a refund against a year-old order, or a manual backfill), but it is
      not impossible, and the honest statement of this control is therefore
      *eventually* enforced rather than *continuously*.
    """
    outcome = _Outcome(collapsed_into=0)
    cutoff = _bucket_cutoff(db, policy, ctx, outcome)
    outcome.cutoff = cutoff
    if cutoff is None:
        return outcome

    columns = ", ".join(_GEO_MEASURES)
    sql = (
        f"SELECT id, bucket_date, state, tz_generation, {columns} "
        f"FROM {policy.table} "
        "WHERE pincode <> :sentinel AND bucket_date < :cutoff "
        "ORDER BY bucket_date, state, id LIMIT :limit OFFSET :offset"
    )
    (
        outcome.rows,
        outcome.batches,
        outcome.max_batch_rows,
        outcome.capped,
        outcome.collapsed_into,
    ) = _batched(
        db,
        ctx,
        table=policy.table,
        sql=sql,
        params={"cutoff": cutoff, "sentinel": DIMENSION_UNKNOWN},
        on_batch=_collapse_geo_batch,
    )
    return outcome


def _prune_recompute_queue(
    db: Session, policy: RetentionPolicy, ctx: _Context
) -> _Outcome:
    """`analytics_recompute_queue`: delete `done` rows past the horizon.

    Only ``done``. ``failed`` rows are the dead-letter queue that alerting reads;
    ``pending`` and ``claimed`` are live work. Deleting a ``failed`` row would
    close the only report of a bucket that will never rebuild itself, and the
    dashboard would go on serving that bucket's stale aggregate forever.
    """
    outcome = _Outcome()
    cutoff = ctx.now - timedelta(days=policy.horizon_days)
    outcome.cutoff = cutoff

    sql = (
        f"SELECT id FROM {policy.table} "
        "WHERE status = :status AND processed_at IS NOT NULL AND processed_at < :cutoff "
        "ORDER BY id LIMIT :limit OFFSET :offset"
    )
    (
        outcome.rows,
        outcome.batches,
        outcome.max_batch_rows,
        outcome.capped,
        _,
    ) = _batched(
        db,
        ctx,
        table=policy.table,
        sql=sql,
        params={"cutoff": cutoff, "status": RecomputeStatus.DONE},
    )
    return outcome


def _protected_watermark_rows(db: Session, *, as_of: datetime) -> list[tuple[str, date]]:
    """``(job, watermark_date)`` pairs that must survive any prune.

    Each job's highest watermark is the answer to "these figures include
    everything through …", and — more pointedly — it is the value
    :func:`_aggregated_through` reads to decide what this very module is allowed
    to delete. Pruning it away would erase the guard on the next run.

    In practice this only bites for retired jobs: a job still running has its
    highest watermark on a recent row, which is nowhere near the 90-day horizon.
    """
    return [
        (job, watermark)
        for job, watermark in db.execute(
            select(AnalyticsSyncRun.job, func.max(AnalyticsSyncRun.watermark_date))
            .where(
                AnalyticsSyncRun.watermark_date.isnot(None),
                AnalyticsSyncRun.started_at <= as_of,
            )
            .group_by(AnalyticsSyncRun.job)
        ).all()
        if watermark is not None
    ]


def _prune_sync_runs(db: Session, policy: RetentionPolicy, ctx: _Context) -> _Outcome:
    """`analytics_sync_runs`: delete successful runs past the horizon.

    **This policy contradicts its own model docstring, and the contradiction is
    deliberate and narrow.** ``AnalyticsSyncRun`` says rows are "never DELETEd by
    the application", and the reason it says so is sound: a run log that can be
    edited after the fact cannot answer "did the pipeline actually run on the
    3rd?", which is the only reason the table exists. So this prune is scoped so
    that everything the docstring is protecting survives:

    * ``failed`` and ``partial`` runs are kept **forever** — those are the
      incidents, and they are what an investigation months later needs.
    * Rows stuck at ``running`` are kept forever — a row stuck there is the sole
      trace of a process that died mid-run.
    * Each job's highest ``watermark_date`` is kept forever
      (:func:`_protected_watermark_rows`).
    * Only ``success`` and ``skipped_locked`` runs older than 90 days go, and
      what they record — "a routine tick did routine work" — is the one thing in
      this table nobody has ever needed at that age.

    If that trade is not acceptable for a given deployment, this is the policy to
    turn off first: pass ``only=`` without ``analytics_sync_runs``.
    """
    outcome = _Outcome()
    cutoff = ctx.now - timedelta(days=policy.horizon_days)
    outcome.cutoff = cutoff

    statuses = {f"st{i}": value for i, value in enumerate(_PRUNABLE_SYNC_STATUSES)}
    status_in = ", ".join(f":{name}" for name in statuses)

    protected = _protected_watermark_rows(db, as_of=ctx.now)
    params: dict[str, Any] = {"cutoff": cutoff, **statuses}
    keep_clause = ""
    if protected:
        pairs = []
        for i, (job, watermark) in enumerate(protected):
            params[f"pj{i}"] = job
            params[f"pw{i}"] = watermark
            pairs.append(f"(:pj{i}, :pw{i})")
        # `watermark_date IS NULL` has to be spelled out: a row constructor
        # containing NULL evaluates to NULL, so `NOT IN` would be NULL too and
        # the row would never match — silently exempting every watermark-less
        # run from a policy that is supposed to cover them.
        keep_clause = (
            " AND (watermark_date IS NULL OR (job, watermark_date) NOT IN ("
            + ", ".join(pairs)
            + "))"
        )

    sql = (
        f"SELECT id FROM {policy.table} "
        f"WHERE status IN ({status_in}) "
        "AND finished_at IS NOT NULL AND finished_at < :cutoff"
        f"{keep_clause} "
        "ORDER BY id LIMIT :limit OFFSET :offset"
    )
    (
        outcome.rows,
        outcome.batches,
        outcome.max_batch_rows,
        outcome.capped,
        _,
    ) = _batched(db, ctx, table=policy.table, sql=sql, params=params)
    return outcome


def _prune_event_outbox(db: Session, policy: RetentionPolicy, ctx: _Context) -> _Outcome:
    """`analytics_event_outbox`: delete delivered rows past the horizon.

    Only ``delivered``. A ``pending`` row is a conversion GA4 is still owed and
    deleting it discharges the debt without paying it; a ``failed`` row is what
    the GA4_SYNC_FAILURE alert reads; a ``suppressed_no_consent`` row is the
    record of a deliberate decision not to transmit, which is precisely the row
    you want to still have when someone asks whether consent was honoured.

    Dated on ``delivered_at``, falling back to ``occurred_at`` so a delivered row
    that somehow lost its delivery stamp is still eventually reachable rather
    than immortal.

    This policy also carries a privacy dividend: ``client_id`` and ``session_id``
    are GA cookie identifiers, and 90 days after delivery they have served their
    only purpose (joining the server event to the browser session that produced
    it).
    """
    outcome = _Outcome()
    cutoff = ctx.now - timedelta(days=policy.horizon_days)
    outcome.cutoff = cutoff

    sql = (
        f"SELECT id FROM {policy.table} "
        "WHERE status = :status AND COALESCE(delivered_at, occurred_at) < :cutoff "
        "ORDER BY id LIMIT :limit OFFSET :offset"
    )
    (
        outcome.rows,
        outcome.batches,
        outcome.max_batch_rows,
        outcome.capped,
        _,
    ) = _batched(
        db,
        ctx,
        table=policy.table,
        sql=sql,
        params={"cutoff": cutoff, "status": OutboxStatus.DELIVERED},
    )
    return outcome


def _prune_cart_events(db: Session, policy: RetentionPolicy, ctx: _Context) -> _Outcome:
    """`cart_events`: delete raw funnel events past 400 store-local days.

    The horizon is a **store-local day boundary**, not "now minus 400 days", so
    whole reporting days are removed together. A half-deleted day would leave a
    session-level funnel that cannot be reconstructed and a daily aggregate that
    can, which is the sort of disagreement that gets discovered by someone
    trying to explain a number.

    This is the only policy here that deletes a *fact*. `cart_events` cannot be
    backfilled from anywhere — the cart lives in Redis and leaves no other
    trace — so the watermark clamp on ``funnel_daily`` is not a nicety: pruning a
    day the funnel job has not aggregated destroys it outright, and the missing
    day looks exactly like a day with no traffic.
    """
    outcome = _Outcome()
    cutoff_day = _bucket_cutoff(db, policy, ctx, outcome)
    if cutoff_day is None:
        outcome.cutoff = None
        return outcome

    # Store-local midnight of the cutoff day, expressed in UTC, because
    # `occurred_at` is a UTC instant and the horizon is a reporting day.
    cutoff_at, _ = day_bounds_utc(cutoff_day, ctx.tz)
    cutoff_naive = cutoff_at.astimezone(timezone.utc).replace(tzinfo=None)
    outcome.cutoff = cutoff_naive

    sql = (
        f"SELECT id FROM {policy.table} WHERE occurred_at < :cutoff "
        "ORDER BY occurred_at, id LIMIT :limit OFFSET :offset"
    )
    (
        outcome.rows,
        outcome.batches,
        outcome.max_batch_rows,
        outcome.capped,
        _,
    ) = _batched(db, ctx, table=policy.table, sql=sql, params={"cutoff": cutoff_naive})
    return outcome


def _prune_csp_reports(db: Session, policy: RetentionPolicy, ctx: _Context) -> _Outcome:
    """CSP violation reports: **not implemented, because nothing stores them.**

    ``SecurityHeadersMiddleware`` sets a ``Content-Security-Policy`` header with
    no ``report-uri`` / ``report-to`` directive, there is no endpoint that
    accepts a violation report, and no table holds one. A 30-day retention on
    CSP reports is therefore a policy with no data behind it.

    Reported as ``skipped: not_implemented`` rather than as a silent zero, so a
    reader of the prune output can tell "nothing to delete" from "this is not a
    thing yet". The existence check means the policy engages by itself the day a
    ``csp_reports`` table appears, without anyone having to remember this file.
    """
    outcome = _Outcome()
    exists = db.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = :name"
        ),
        {"name": policy.table},
    ).scalar()
    if not exists:
        outcome.skipped = "not_implemented"
        outcome.warnings.append(
            "no CSP violation reports are stored in this deployment: the CSP "
            "header carries no report-uri, there is no receiving endpoint, and "
            f"no {policy.table!r} table exists. Nothing to prune."
        )
        return outcome

    cutoff = ctx.now - timedelta(days=policy.horizon_days)
    outcome.cutoff = cutoff
    sql = (
        f"SELECT id FROM {policy.table} WHERE created_at < :cutoff "
        "ORDER BY id LIMIT :limit OFFSET :offset"
    )
    (
        outcome.rows,
        outcome.batches,
        outcome.max_batch_rows,
        outcome.capped,
        _,
    ) = _batched(db, ctx, table=policy.table, sql=sql, params={"cutoff": cutoff})
    return outcome


_PRUNERS: dict[str, Callable[[Session, RetentionPolicy, _Context], _Outcome]] = {
    "agg_order_hourly": _prune_order_hourly,
    "agg_customer_snapshot": _prune_customer_snapshot,
    "agg_geo_daily_pincode": _prune_geo_pincode,
    "analytics_recompute_queue": _prune_recompute_queue,
    "analytics_sync_runs": _prune_sync_runs,
    "analytics_event_outbox": _prune_event_outbox,
    "cart_events": _prune_cart_events,
    "csp_reports": _prune_csp_reports,
}


# ===========================================================================
# Entry point
# ===========================================================================
def prune_all(
    db: Session,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
    max_rows_per_policy: int = DEFAULT_MAX_ROWS_PER_POLICY,
    only: Sequence[str] | None = None,
    worker_id: str = "local",
    trigger: str = SyncTrigger.CRON,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply every retention policy once, in bounded batches, and log the run.

    Returns rows affected per policy::

        {
          "dry_run": False,
          "rows_deleted": 1284,          # total across policies
          "rows_written": 37,            # state-grain rows the geo collapse wrote
          "sync_run_id": 9182,
          "policies": {
            "agg_order_hourly": {"rows": 720, "batches": 1, "capped": False, ...},
            ...
          },
        }

    ``dry_run`` runs every scan and reports every count, writes nothing and
    deletes nothing. The numbers are measured, not estimated: it walks the same
    pages in the same order and simply pages forward instead of deleting.

    ``now`` overrides the clock. Production never passes it; it exists for the
    same reason ``AggregationRunner``'s ``clock`` does — a horizon is a behaviour
    with real consequences, and a test that cannot pin the current instant can
    only assert on it vaguely.

    ``only`` restricts the run to named policies, which is also how a deployment
    that does not accept the ``analytics_sync_runs`` trade-off opts out of it.

    Never raises. A policy that fails is recorded with its error and the rest
    still run: the table that is actually growing must not go unpruned because a
    different table's predicate broke.
    """
    unknown = sorted(set(only or ()) - set(_BY_KEY))
    if unknown:
        raise KeyError(
            f"unknown retention policy {unknown}; known policies are "
            f"{list(policy_keys())}"
        )

    selected = [p for p in POLICIES if only is None or p.key in set(only)]

    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    tz = store_timezone(db)
    ctx = _Context(
        now=moment.astimezone(timezone.utc).replace(tzinfo=None),
        tz=tz,
        today=local_day(moment, tz),
        batch_size=max(int(batch_size), 1),
        max_rows=max(int(max_rows_per_policy), 0),
        dry_run=bool(dry_run),
    )

    run = _open_run(db, worker_id=worker_id, trigger=trigger, ctx=ctx, count=len(selected))
    started = time.monotonic()

    results: dict[str, dict[str, Any]] = {}
    total_rows = total_written = 0
    completed = 0
    failures: list[str] = []

    for policy in selected:
        try:
            outcome = _PRUNERS[policy.key](db, policy, ctx)
            completed += 1
        except Exception as exc:  # noqa: BLE001 - one policy must not stop the rest
            db.rollback()
            log.exception("retention policy %s failed: %s", policy.key, exc)
            outcome = _Outcome(error=f"{type(exc).__name__}: {exc}")
            failures.append(f"{policy.key}: {type(exc).__name__}: {exc}")
        total_rows += outcome.rows
        total_written += outcome.collapsed_into or 0
        results[policy.key] = outcome.as_dict(policy)

    status = SyncStatus.SUCCESS
    if failures and completed == 0:
        status = SyncStatus.FAILED
    elif failures:
        status = SyncStatus.PARTIAL

    _close_run(
        db,
        run,
        status=status,
        ctx=ctx,
        days_processed=completed,
        rows_deleted=0 if ctx.dry_run else total_rows,
        rows_written=0 if ctx.dry_run else total_written,
        duration_ms=max(int((time.monotonic() - started) * 1000), 0),
        note=_run_note(ctx, total_rows, total_written, failures),
    )

    return {
        "dry_run": ctx.dry_run,
        "status": status,
        "now": ctx.now,
        "reporting_day": ctx.today,
        "worker_id": worker_id,
        "sync_run_id": run.id,
        "batch_size": ctx.batch_size,
        "max_rows_per_policy": ctx.max_rows,
        "rows_deleted": total_rows,
        "rows_written": total_written,
        "policies": results,
    }


def _run_note(
    ctx: _Context, rows: int, written: int, failures: Sequence[str]
) -> str | None:
    """The line that lands in ``analytics_sync_runs.error``.

    That column doubles as the run log's warning channel (``AggregationRunner``
    puts job warnings there for the same reason): there is no other place a run
    can say something a reader needs to see. A dry run says so explicitly and
    reports what it *would* have removed, because the row itself records
    ``rows_deleted = 0`` — the log must never imply a deletion that did not
    happen.
    """
    parts: list[str] = []
    if ctx.dry_run:
        parts.append(
            f"dry_run: would remove {rows} row(s) and write {written} "
            "state-grain row(s); nothing was deleted"
        )
    if failures:
        parts.append("; ".join(failures))
    if not parts:
        return None
    return "; ".join(parts)[:500]


def _open_run(
    db: Session,
    *,
    worker_id: str,
    trigger: str,
    ctx: _Context,
    count: int,
) -> AnalyticsSyncRun:
    """Write the ``RUNNING`` row before anything is deleted.

    Committed immediately, exactly as ``AggregationRunner._open_run`` does: a row
    that only appeared once the prune finished could never record a prune that
    died halfway through — which is the one prune anybody would want to read
    about afterwards.

    ``days_requested`` / ``days_processed`` count *policies*, not days. A prune
    has no bucket window, and leaving both at zero would make a run that did work
    indistinguishable in the health panel from one that did nothing.
    """
    run = AnalyticsSyncRun(
        job=PRUNE_JOB_NAME,
        trigger=trigger,
        status=SyncStatus.RUNNING,
        worker_id=worker_id[:64],
        # NULL window: retention has no bucket range someone asked for. Recording
        # the horizons as a window would imply every bucket between them was
        # touched, and most were not.
        window_from=None,
        window_to=None,
        tz_generation=1,
        days_requested=count,
        days_processed=0,
        rows_written=0,
        rows_deleted=0,
        started_at=ctx.now,
    )
    db.add(run)
    db.commit()
    return run


def _close_run(
    db: Session,
    run: AnalyticsSyncRun,
    *,
    status: str,
    ctx: _Context,
    days_processed: int,
    rows_deleted: int,
    rows_written: int,
    duration_ms: int,
    note: str | None,
) -> None:
    """Complete the run row. Written once, never touched again.

    ``watermark_date`` stays NULL on purpose. A prune advances no job's data
    watermark, and the admin health panel reads ``MAX(watermark_date)`` per job
    as "these numbers include everything through …" — a prune writing one would
    be asserting freshness it did not produce.

    ``finished_at`` is derived from ``started_at`` plus the measured duration
    rather than from a second wall-clock read. The two must stay on the same
    clock: with an injected ``now`` they would otherwise be years apart, and
    ``duration_ms`` is an ``Integer`` that a gap like that overflows.
    """
    run.status = status
    run.days_processed = days_processed
    run.rows_written = rows_written
    run.rows_deleted = rows_deleted
    run.watermark_date = None
    run.duration_ms = duration_ms
    run.finished_at = ctx.now + timedelta(milliseconds=duration_ms)
    run.error = note
    db.commit()
