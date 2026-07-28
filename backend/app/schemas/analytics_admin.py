"""Request/response models for the analytics job-control API.

These types encode the two rules the job-control surface exists to enforce, so
that neither can be forgotten at a call site:

**Windows are half-open, `[date_from, date_to)`.** Same convention as
`analytics/timebox.py:day_bounds_utc` — a closed upper bound double-counts the
boundary day the moment two windows are chained, and chaining is exactly what
backfill paging does. `date_from == date_to` is therefore an empty window and is
rejected rather than silently enqueuing nothing.

**Nothing here is a budget for work; it is a budget for a *request*.** The
routes enqueue, they do not compute (see `endpoints/analytics_admin.py`), so the
bounds below (`max_days`, `budget_ms`) exist to keep a request cheap and
predictable, not to size the pipeline. The one exception — the bounded inline
aggregate — carries its own tighter caps for the same reason.

Bounds are `Field(le=...)` constraints rather than clamps applied in the
handler. A clamp turns "backfill 90 days" into a silent 60-day run whose caller
believes it covered 90; the operator then reads a chart with a 30-day hole and
has nothing to tell them why. Rejecting is louder and cheaper.
"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

#: Widest window any single request may name, in days. Not a performance limit —
#: a mistyped year ("2016-01-01") is the realistic way a 3,000-day window gets
#: requested, and enqueueing 3,000 queue rows from a typo is worse than a 422.
MAX_WINDOW_DAYS = 400

#: Hard ceiling on one backfill page. The default (30) is what an operator gets
#: without thinking about it; 60 is the most they may ask for in one call.
BACKFILL_MAX_DAYS_CAP = 60
BACKFILL_MAX_DAYS_DEFAULT = 30

#: Ceilings for the bounded inline aggregate. 55s sits under nginx's 60s
#: `proxy_read_timeout`: a run that overruns the proxy is killed mid-write and
#: reports nothing back, which is strictly worse than a short run that says it
#: stopped early. 14 days keeps the worst case within that budget.
INLINE_BUDGET_MS_CAP = 55_000
INLINE_BUDGET_MS_DEFAULT = 20_000
INLINE_MAX_DAYS_CAP = 14
INLINE_MAX_DAYS_DEFAULT = 7


class _WindowMixin(BaseModel):
    """Shared half-open `[date_from, date_to)` window with its validation.

    Subclassed rather than repeated so a new route cannot ship with the window
    check missing — the failure mode of a missing check here is a request that
    enqueues a decade of buckets, and it looks like a successful 202.
    """

    date_from: date = Field(description="First store-local reporting day, inclusive.")
    date_to: date = Field(
        description="Day *after* the last one covered — the window is half-open."
    )

    @model_validator(mode="after")
    def _validate_window(self) -> "_WindowMixin":
        if self.date_from >= self.date_to:
            raise ValueError(
                "date_from must be strictly before date_to; the window is "
                "half-open [date_from, date_to) so an equal pair covers no days."
            )
        span = (self.date_to - self.date_from).days
        if span > MAX_WINDOW_DAYS:
            raise ValueError(
                f"window spans {span} days; the maximum is {MAX_WINDOW_DAYS}. "
                "Split the request, or check for a mistyped year."
            )
        return self


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
class RecomputeRequest(_WindowMixin):
    """Mark a window's buckets dirty so the worker rebuilds them.

    This is the "I know these numbers are wrong" button. It does not compute
    anything; it writes queue rows the rollup worker drains on its next tick.
    """

    job: str = Field(
        max_length=64,
        description="Aggregation job name. Must exist in the JOBS registry.",
    )
    dimension_key: str = Field(
        default="-",
        max_length=96,
        description=(
            "Slice to rebuild within each bucket. '-' (the default) means the "
            "whole bucket; NOT NULL sentinel — the queue's dedup UNIQUE key "
            "spans this column."
        ),
    )
    priority: int = Field(
        default=5,
        ge=0,
        le=100,
        description=(
            "Lower runs first. 5 is routine. Enqueue is an upsert using "
            "LEAST(priority, new) so this can raise a row's urgency, never lower it."
        ),
    )


class BackfillRequest(_WindowMixin):
    """Fill history that was never computed, one bounded page at a time.

    Backfill is the one operation whose natural size is "everything", which is
    also the one size a single HTTP request must never accept. `max_days` bounds
    the page; the response's `next_date_from` is how the operator (or a script)
    walks the rest.
    """

    job: str = Field(max_length=64, description="Aggregation job name.")
    max_days: int = Field(
        default=BACKFILL_MAX_DAYS_DEFAULT,
        ge=1,
        le=BACKFILL_MAX_DAYS_CAP,
        description=(
            f"Days to enqueue in this page (1–{BACKFILL_MAX_DAYS_CAP}). Asking "
            "for more is rejected, not quietly reduced."
        ),
    )
    dimension_key: str = Field(default="-", max_length=96)
    priority: int = Field(
        default=20,
        ge=0,
        le=100,
        description=(
            "Defaults *higher* (= less urgent) than recompute so a deep "
            "historical fill can never starve today's live buckets."
        ),
    )


class AggregateRequest(_WindowMixin):
    """Run a job over a window: enqueued by default, inline only on request.

    `inline=true` is the sole route in this module that does work in a request
    worker, and it exists for debugging and for a first-run smoke test before
    the worker container is deployed. It is bounded twice over — by `budget_ms`
    and by `max_days` — because an unbounded inline run holds a request worker
    hostage and then dies at the proxy timeout with nothing written to show for
    it.
    """

    job: str = Field(max_length=64, description="Aggregation job name.")
    max_days: int = Field(
        default=INLINE_MAX_DAYS_DEFAULT,
        ge=1,
        le=INLINE_MAX_DAYS_CAP,
        description=(
            f"Days processed per inline call (1–{INLINE_MAX_DAYS_CAP}). Ignored "
            "when enqueuing, which is bounded by the window itself."
        ),
    )
    budget_ms: int = Field(
        default=INLINE_BUDGET_MS_DEFAULT,
        ge=100,
        le=INLINE_BUDGET_MS_CAP,
        description=(
            f"Wall-clock budget for an inline run (100–{INLINE_BUDGET_MS_CAP} ms). "
            "The run stops at the next bucket boundary once spent and reports "
            "`next_date_from`; it never abandons a half-written bucket."
        ),
    )
    dimension_key: str = Field(default="-", max_length=96)
    priority: int = Field(default=5, ge=0, le=100)


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------
class RecomputeResponse(BaseModel):
    """202 body. `job_ids` are `analytics_recompute_queue` row ids.

    They are returned rather than just a count because enqueue is an *upsert*:
    re-enqueuing a bucket reopens the existing row instead of adding one, so
    `queued` alone cannot tell an operator which buckets they actually touched.
    """

    job: str
    queued: int = Field(description="Queue rows created or reopened.")
    job_ids: list[int] = Field(
        default_factory=list, description="Queue row ids, ascending by bucket date."
    )
    date_from: date
    date_to: date


class BackfillResponse(BaseModel):
    """202 body for one backfill page.

    `date_from`/`date_to` describe the page that was *enqueued*, which is not
    necessarily the window that was requested. `next_date_from` is the cursor:
    feed it back as `date_from` to fetch the next page, and stop when `done`.
    """

    job: str
    queued: int
    job_ids: list[int] = Field(default_factory=list)
    date_from: date = Field(description="First day of the page actually enqueued.")
    date_to: date = Field(description="Exclusive end of the page actually enqueued.")
    next_date_from: date | None = Field(
        default=None,
        description="Cursor for the next page; None when the window is exhausted.",
    )
    done: bool = Field(
        description="True when this page reached the requested date_to."
    )
    requested_date_to: date = Field(
        description="The window the caller asked for, echoed so paging is auditable."
    )


class AggregateEnqueuedResponse(BaseModel):
    """202 body for the default (enqueue) path of /admin/aggregate."""

    job: str
    mode: str = Field(default="enqueued", description="Always 'enqueued' here.")
    queued: int
    job_ids: list[int] = Field(default_factory=list)
    date_from: date
    date_to: date


class AggregateInlineResponse(BaseModel):
    """200 body for `?inline=true` — work that already happened.

    200 rather than 202 on purpose: 202 means "accepted, not yet done", and
    conflating the two would leave an operator unable to tell a completed
    debug run from a queued one. See the module docstring in
    `endpoints/analytics_admin.py`.
    """

    job: str
    mode: str = Field(default="inline", description="Always 'inline' here.")
    run_id: int | None = Field(
        default=None, description="analytics_sync_runs row id for this run."
    )
    status: str = Field(description="SyncStatus value the run terminated with.")
    days_requested: int
    days_processed: int
    rows_written: int = 0
    rows_deleted: int = 0
    watermark_date: date | None = None
    duration_ms: int | None = None
    budget_exhausted: bool = Field(
        default=False,
        description="True when the run stopped on `budget_ms` rather than finishing.",
    )
    next_date_from: date | None = Field(
        default=None,
        description="Where to resume when the budget or max_days cut the run short.",
    )
    error: str | None = None


class SyncRunRead(BaseModel):
    """One `analytics_sync_runs` row — what GET /admin/jobs/{run_id} returns.

    `watermark_date` is the field that matters operationally: it is a *data*
    watermark ("everything through this day is computed"), not a clock
    timestamp, and it is what the dashboard prints beside a revenue figure.
    """

    model_config = {"from_attributes": True}

    id: int
    job: str
    trigger: str
    status: str
    worker_id: str | None = None
    window_from: date | None = None
    window_to: date | None = None
    tz_generation: int
    days_requested: int
    days_processed: int
    rows_written: int
    rows_deleted: int
    watermark_date: date | None = None
    duration_ms: int | None = None
    error: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class JobHealth(BaseModel):
    """Per-job health line for GET /admin/health."""

    job: str
    watermark_date: date | None = Field(
        default=None, description="Highest fully-computed bucket for this job."
    )
    last_run_id: int | None = None
    last_run_status: str | None = None
    last_run_started_at: datetime | None = None
    last_run_finished_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = Field(
        default=None, description="Error from the most recent non-successful run."
    )
    consecutive_failures: int = Field(
        default=0,
        description=(
            "Failed runs since the last success. SKIPPED_LOCKED runs are "
            "excluded — a busy lock is not an outage."
        ),
    )
    pending_buckets: int = 0
    failed_buckets: int = 0
    oldest_pending_bucket: date | None = None


class QueueDepth(BaseModel):
    """Queue rows by `RecomputeStatus`, across all jobs."""

    pending: int = 0
    claimed: int = 0
    done: int = 0
    failed: int = 0
    total: int = 0


class AnalyticsHealthResponse(BaseModel):
    """GET /admin/health — the one page an on-call person reads.

    Deliberately answers "are the numbers current?" and not "did the process
    exit 0?". A pipeline that runs cleanly every tick and advances no watermark
    is the failure mode this endpoint exists to make visible, because it is
    indistinguishable from health in every other signal.
    """

    generated_at: datetime
    rollups_enabled: bool = Field(
        description=(
            "settings.ANALYTICS_ROLLUPS_ENABLED — false means the worker "
            "refuses to start, so nothing drains the queue."
        )
    )
    tz_generation: int | None = Field(
        default=None, description="Active reporting-timezone generation."
    )
    queue_depth: QueueDepth
    oldest_pending_bucket: date | None = Field(
        default=None,
        description=(
            "Oldest dirty bucket anywhere in the queue. If this stops moving, "
            "the worker is not draining."
        ),
    )
    stale_claims: int = Field(
        default=0,
        description=(
            "Rows still 'claimed' past their lease. Non-zero for long means the "
            "reclaim sweep is not running (or a worker died mid-bucket)."
        ),
    )
    jobs: list[JobHealth] = Field(default_factory=list)
    last_error: str | None = Field(
        default=None, description="Most recent error across all jobs, if any."
    )
