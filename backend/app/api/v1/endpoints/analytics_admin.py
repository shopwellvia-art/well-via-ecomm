"""Analytics job-control API — enqueue and report, never compute.

Routes
------
=========================== ====== =========================================
POST /admin/recompute       202    mark a window dirty
POST /admin/backfill        202    enqueue one bounded page of history
POST /admin/aggregate       202    enqueue a window
POST /admin/aggregate?inline=true
                            200    bounded inline run (manual/debug only)
GET  /admin/jobs/{run_id}   200    one analytics_sync_runs row (404 if absent)
GET  /admin/health          200    watermarks, queue depth, last error
=========================== ====== =========================================

Why these return 202 and not 200
--------------------------------
A rollup rebuild takes seconds to minutes. 200 means "here is the result"; if
these routes returned 200 an operator would reasonably reload the dashboard on
the strength of it, see the *old* numbers, and conclude the endpoint is broken —
when in fact the work is queued and correct. 202 says exactly what happened:
the request was accepted, rows exist in `analytics_recompute_queue`, and the
worker will get to them. The queued ids come back so the operator can follow it.

Why they enqueue rather than execute
------------------------------------
Uvicorn runs four request workers serving the storefront. Aggregation inside one
of them is a request worker that is not serving requests, for minutes, and under
nginx's 60s `proxy_read_timeout` it eventually becomes a *killed* run that wrote
half a bucket and logged nothing. The dedicated worker process
(`app/services/analytics/worker.py`) owns the heavy path; these routes own the
intent. See `backend/docs/analytics-worker.md`.

The single bounded exception
----------------------------
`POST /admin/aggregate?inline=true` does run work, and is capped twice over:
`budget_ms` <= 55 000 (under the proxy timeout) and `max_days` <= 14. It exists
to prove the pipeline before the worker container is deployed, and to reproduce
one bad bucket in front of a log tail. It is not the normal path and the
docstring on the route says so.

Auth
----
Identical shape to `reconcile_auth` in `endpoints/payments.py`: a machine
presents `X-Analytics-Token`, compared with `secrets.compare_digest` against
`settings.ANALYTICS_CRON_TOKEN`; a human presents a session and needs
`analytics.jobs.run`. A **blank** configured token disables the machine path
outright, so an unset secret can never be satisfied by an empty header.

Job names
---------
`job` is resolved against the `JOBS` registry and a miss is a 400. A
client-supplied job name reaches the queue's `job` column and, later, the
runner's dispatch — accepting an unregistered one would write rows nothing can
ever drain, which is a permanent silent backlog rather than an error.
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, optional_current_user, rate_limit_by_ip
from app.core.config import settings
from app.core.exceptions import AppError, ForbiddenError, NotFoundError
from app.models.analytics_base import DIMENSION_UNKNOWN
from app.models.analytics_control import (
    AnalyticsRecomputeQueue,
    AnalyticsSyncRun,
    RecomputeReason,
    RecomputeStatus,
    SyncStatus,
    SyncTrigger,
)
from app.models.user import User
from app.schemas.analytics_admin import (
    AggregateEnqueuedResponse,
    AggregateInlineResponse,
    AggregateRequest,
    AnalyticsHealthResponse,
    BackfillRequest,
    BackfillResponse,
    JobHealth,
    QueueDepth,
    RecomputeRequest,
    RecomputeResponse,
    SyncRunRead,
)
# Import from the package, not `.base`: `aggregation/__init__` imports the job
# modules for their registration side effect, so `JOBS` is populated. Importing
# `.base` alone would give an empty registry and turn every valid job name into
# a 400.
from app.services.analytics.aggregation import JOBS, AggregationRunner
from app.services.analytics.queue import RecomputeQueue
from app.services.analytics.timebox import active_generation

router = APIRouter()

#: Permission a human needs to drive any of this.
JOBS_RUN_PERMISSION = "analytics.jobs.run"

#: `worker_id` stamped on `analytics_sync_runs` for a bounded inline run, so the
#: run log distinguishes "an operator pressed the button" from a worker tick
#: without having to infer it from `trigger` alone.
_INLINE_WORKER_ID = "api-inline"

#: Enqueueing is cheap but not free — each call can write up to 400 queue rows,
#: and the routes are reachable by a shared secret rather than a session. The
#: limit is generous for an operator paging through a backfill and low enough
#: that a leaked token cannot be used to hammer the queue.
_WRITE_RATE_LIMIT = Depends(
    rate_limit_by_ip(scope="analytics.jobs.write.ip", limit=60, window_sec=300)
)
#: Health/status polling is expected to be frequent (a dashboard, a monitor).
_READ_RATE_LIMIT = Depends(
    rate_limit_by_ip(scope="analytics.jobs.read.ip", limit=300, window_sec=300)
)


class BadRequestError(AppError):
    """400. Defined here rather than in `core/exceptions.py` because this is the
    only surface that needs it: an unknown *job name* is a bad request, not a
    schema violation (422) and not a missing resource (404). Subclasses AppError
    so the app-wide handler renders the standard error envelope."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"


# ---------------------------------------------------------------------------
# Auth — machine or human, exactly as payments.reconcile_auth does it
# ---------------------------------------------------------------------------
def analytics_auth(
    x_analytics_token: str | None = Header(default=None, alias="X-Analytics-Token"),
    user: User | None = Depends(optional_current_user),
) -> User | None:
    """Authenticate an analytics job trigger for a machine OR a human.

    A cron sidecar or the analytics worker cannot hold a 30-minute human access
    token, so it presents a shared secret compared in **constant time** —
    `secrets.compare_digest` rather than `==`, because a byte-at-a-time early
    return leaks the token's prefix to anyone who can measure response latency,
    and this token queues arbitrary work.

    `configured and x_analytics_token and ...` short-circuits before the compare:
    with `ANALYTICS_CRON_TOKEN` unset, machine auth is *off*, and an empty header
    must not match an empty secret.

    Returns the acting user (None for the machine path) so a route can attribute
    the request; raises 403 for everything else.
    """
    configured = settings.ANALYTICS_CRON_TOKEN
    if (
        configured
        and x_analytics_token
        and secrets.compare_digest(x_analytics_token, configured)
    ):
        return None
    if user is not None and user.has_permission(JOBS_RUN_PERMISSION):
        return user
    raise ForbiddenError(
        "Analytics jobs require a valid X-Analytics-Token or the "
        f"{JOBS_RUN_PERMISSION} permission."
    )


_AUTH = Depends(analytics_auth)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _require_known_job(name: str) -> str:
    """Reject a job name the registry does not know, with 400.

    Never let a client-supplied identifier through to the queue. `job` is
    written verbatim into `analytics_recompute_queue.job` and later dispatched
    on; an unregistered name produces rows no worker will ever claim — an
    invisible, permanent backlog that looks like a successful 202.
    """
    if name not in JOBS:
        raise BadRequestError(
            f"Unknown analytics job {name!r}.",
            details={"registered_jobs": sorted(JOBS)},
        )
    return name


def _days(date_from: date, date_to: date) -> list[date]:
    """Every store-local reporting day in the half-open window."""
    return [
        date_from + timedelta(days=offset)
        for offset in range((date_to - date_from).days)
    ]


def _enqueue_window(
    db: Session,
    *,
    job: str,
    date_from: date,
    date_to: date,
    dimension_key: str,
    priority: int,
    reason: str,
) -> tuple[int, list[int]]:
    """Enqueue every bucket in `[date_from, date_to)`; return (count, row ids).

    `RecomputeQueue.enqueue*` flush but deliberately do not commit — they are
    built to run inside the caller's transaction so an enqueue for a change that
    rolled back rolls back too. This route *is* the transaction, so it commits.

    Ids are read back after the commit rather than returned by the enqueue,
    because enqueue is an upsert: a bucket already queued is reopened in place,
    so MySQL's affected-row count cannot be turned into a set of ids. The
    read-back gives the operator the actual rows to follow.
    """
    queue = RecomputeQueue(db)
    generation = active_generation(db).generation
    buckets = _days(date_from, date_to)

    if dimension_key == DIMENSION_UNKNOWN:
        # One statement for the whole-bucket case, which is nearly all of them.
        queue.enqueue_many(
            ((job, bucket) for bucket in buckets), reason=reason, priority=priority
        )
    else:
        for bucket in buckets:
            queue.enqueue(
                job,
                bucket,
                reason=reason,
                dimension_key=dimension_key,
                priority=priority,
                tz_generation=generation,
            )
    db.commit()

    ids = list(
        db.execute(
            select(AnalyticsRecomputeQueue.id)
            .where(
                AnalyticsRecomputeQueue.job == job,
                AnalyticsRecomputeQueue.dimension_key == dimension_key,
                AnalyticsRecomputeQueue.tz_generation == generation,
                AnalyticsRecomputeQueue.bucket_date >= date_from,
                AnalyticsRecomputeQueue.bucket_date < date_to,
            )
            .order_by(AnalyticsRecomputeQueue.bucket_date.asc())
        )
        .scalars()
        .all()
    )
    return len(buckets), ids


# ---------------------------------------------------------------------------
# POST /admin/recompute
# ---------------------------------------------------------------------------
@router.post(
    "/admin/recompute",
    response_model=RecomputeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[_AUTH, _WRITE_RATE_LIMIT],
)
def recompute(payload: RecomputeRequest, db: Session = Depends(get_db)):
    """Mark a window's buckets dirty. 202 — queued, not computed.

    The "these numbers are wrong, rebuild them" button. Enqueue is an upsert on
    `(job, bucket_date, dimension_key, tz_generation)`, so calling this twice
    for the same window costs one set of rows, not two, and a bucket already
    marked `done` is reopened — which is exactly what a late correction needs.

    Window is half-open: `[date_from, date_to)`.
    """
    job = _require_known_job(payload.job)
    queued, ids = _enqueue_window(
        db,
        job=job,
        date_from=payload.date_from,
        date_to=payload.date_to,
        dimension_key=payload.dimension_key,
        priority=payload.priority,
        reason=RecomputeReason.MANUAL,
    )
    return RecomputeResponse(
        job=job,
        queued=queued,
        job_ids=ids,
        date_from=payload.date_from,
        date_to=payload.date_to,
    )


# ---------------------------------------------------------------------------
# POST /admin/backfill
# ---------------------------------------------------------------------------
@router.post(
    "/admin/backfill",
    response_model=BackfillResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[_AUTH, _WRITE_RATE_LIMIT],
)
def backfill(payload: BackfillRequest, db: Session = Depends(get_db)):
    """Enqueue one bounded page of history. 202, with a cursor for the next.

    Backfill's natural size is "everything", which is the one size a single
    request must not accept. This enqueues at most `max_days` (default 30, hard
    cap 60 enforced by the schema — asking for more is a 422, never a silent
    reduction) and returns `next_date_from`.

    **Paging, not truncation.** A truncated backfill leaves a hole in the chart
    that nobody can trace back to the request that caused it. Here the caller
    is handed the cursor and `done: false`, so the remaining window is visible
    in the response rather than lost: feed `next_date_from` back as `date_from`
    and repeat until `done`.

    Default `priority` is numerically *higher* (= less urgent) than recompute's,
    so a year of history can never starve today's live buckets.
    """
    job = _require_known_job(payload.job)
    page_end = min(
        payload.date_to, payload.date_from + timedelta(days=payload.max_days)
    )
    queued, ids = _enqueue_window(
        db,
        job=job,
        date_from=payload.date_from,
        date_to=page_end,
        dimension_key=payload.dimension_key,
        priority=payload.priority,
        reason=RecomputeReason.BACKFILL,
    )
    done = page_end >= payload.date_to
    return BackfillResponse(
        job=job,
        queued=queued,
        job_ids=ids,
        date_from=payload.date_from,
        date_to=page_end,
        next_date_from=None if done else page_end,
        done=done,
        requested_date_to=payload.date_to,
    )


# ---------------------------------------------------------------------------
# POST /admin/aggregate
# ---------------------------------------------------------------------------
@router.post(
    "/admin/aggregate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AggregateEnqueuedResponse | AggregateInlineResponse,
    dependencies=[_AUTH, _WRITE_RATE_LIMIT],
)
def aggregate(
    payload: AggregateRequest,
    response: Response,
    inline: bool = Query(
        default=False,
        description=(
            "Run now instead of enqueuing. Bounded, and for manual/debug use "
            "only — the worker is the normal path."
        ),
    ),
    db: Session = Depends(get_db),
):
    """Enqueue a window (202), or run it inline within a hard budget (200).

    **Default — enqueue.** Identical to `/admin/recompute` in effect; it exists
    separately so "aggregate this window" reads as an operation in its own right
    rather than as an admission that something was wrong.

    **`?inline=true` — the one route that computes.** For proving the pipeline
    before the worker container is deployed, and for reproducing one bad bucket
    in front of a log tail. Two hard bounds, both enforced by the schema:
    `budget_ms` <= 55 000 (nginx's `proxy_read_timeout` is 60s — a run that
    overruns it is killed mid-write and reports nothing, which is strictly worse
    than a short run that says where it stopped) and `max_days` <= 14.

    Both bounds are *resumable*, not lossy: the runner commits one bucket per
    transaction and reports the last one it finished, so `next_date_from` is
    always a safe place to continue. Routine aggregation must go through the
    worker; using this route for it puts minutes of work back inside a request
    worker, which is the whole thing this design avoids.

    The response is **200** for an inline run and **202** for an enqueue,
    because conflating "done" with "accepted" would leave an operator unable to
    tell which one they got.
    """
    job = _require_known_job(payload.job)

    if not inline:
        queued, ids = _enqueue_window(
            db,
            job=job,
            date_from=payload.date_from,
            date_to=payload.date_to,
            dimension_key=payload.dimension_key,
            priority=payload.priority,
            reason=RecomputeReason.MANUAL,
        )
        return AggregateEnqueuedResponse(
            job=job,
            queued=queued,
            job_ids=ids,
            date_from=payload.date_from,
            date_to=payload.date_to,
        )

    # `max_days` caps the window before the clock does, so a caller asking for
    # 14 days gets a predictable amount of work rather than "whatever fits".
    window_end = min(
        payload.date_to, payload.date_from + timedelta(days=payload.max_days)
    )
    summary = AggregationRunner(db, worker_id=_INLINE_WORKER_ID).run_window(
        [job],
        payload.date_from,
        # Both are half-open [from, to) — same convention as timebox.py and the
        # request schema, so no conversion is needed here.
        window_end,
        budget_ms=payload.budget_ms,
        trigger=SyncTrigger.MANUAL,
    )
    per_job = summary["jobs"][job]

    # Where to resume: the bucket after the last one fully completed. Falls back
    # to where the run started when nothing completed at all (budget spent
    # immediately, or the first bucket failed) — never to the window end, which
    # would silently declare unprocessed days done.
    watermark = per_job["watermark_date"]
    if watermark is not None:
        resume = watermark + timedelta(days=1)
    else:
        resume = per_job["resumed_from"] or payload.date_from
    next_date_from = resume if resume < payload.date_to else None

    # 200: this ran. FastAPI's route-level 202 is the enqueue case above.
    response.status_code = status.HTTP_200_OK
    return AggregateInlineResponse(
        job=job,
        run_id=per_job["sync_run_id"],
        status=per_job["status"],
        days_requested=per_job["days_requested"],
        days_processed=per_job["days_processed"],
        rows_written=per_job["rows_written"],
        rows_deleted=per_job["rows_deleted"],
        watermark_date=watermark,
        duration_ms=summary["duration_ms"],
        budget_exhausted=bool(summary["budget_exhausted"]),
        next_date_from=next_date_from,
        error=per_job["error"],
    )


# ---------------------------------------------------------------------------
# GET /admin/jobs/{run_id}
# ---------------------------------------------------------------------------
@router.get(
    "/admin/jobs/{run_id}",
    response_model=SyncRunRead,
    dependencies=[_AUTH, _READ_RATE_LIMIT],
)
def get_job_run(run_id: int, db: Session = Depends(get_db)):
    """One `analytics_sync_runs` row.

    A row stuck at `running` well past its expected duration is the signature of
    a worker that died without writing a terminal status — the run log is the
    only place that non-event leaves a trace.
    """
    run = db.get(AnalyticsSyncRun, run_id)
    if run is None:
        raise NotFoundError(f"No analytics sync run with id {run_id}.")
    return run


# ---------------------------------------------------------------------------
# GET /admin/health
# ---------------------------------------------------------------------------
@router.get(
    "/admin/health",
    response_model=AnalyticsHealthResponse,
    dependencies=[_AUTH, _READ_RATE_LIMIT],
)
def health(db: Session = Depends(get_db)):
    """Watermarks per job, queue depth by status, last error, oldest pending.

    Answers "are the numbers current?", not "did the process exit 0?". A
    pipeline that ticks cleanly and advances no watermark is indistinguishable
    from health in every other signal — green logs, no restarts, a dashboard
    quietly serving last week — so the watermark and the oldest pending bucket
    are the two fields that actually carry the signal here.
    """
    depth = RecomputeQueue(db).depth()
    queue_depth = QueueDepth(
        pending=depth.get(RecomputeStatus.PENDING, 0),
        claimed=depth.get(RecomputeStatus.CLAIMED, 0),
        done=depth.get(RecomputeStatus.DONE, 0),
        failed=depth.get(RecomputeStatus.FAILED, 0),
        total=depth.get("total", 0),
    )

    oldest_pending = db.execute(
        select(func.min(AnalyticsRecomputeQueue.bucket_date)).where(
            AnalyticsRecomputeQueue.status == RecomputeStatus.PENDING
        )
    ).scalar()

    # Claimed past the lease. Briefly non-zero after a restart is normal;
    # persistently non-zero means the reclaim sweep is not running and those
    # buckets are stuck with nothing reporting it.
    stale_claims = int(
        db.execute(
            select(func.count(AnalyticsRecomputeQueue.id)).where(
                AnalyticsRecomputeQueue.status == RecomputeStatus.CLAIMED,
                AnalyticsRecomputeQueue.claim_expires_at.is_not(None),
                AnalyticsRecomputeQueue.claim_expires_at
                < datetime.now(timezone.utc).replace(tzinfo=None),
            )
        ).scalar()
        or 0
    )

    # Per-job queue counts, in two grouped queries rather than N per job.
    pending_by_job: dict[str, int] = {}
    failed_by_job: dict[str, int] = {}
    oldest_by_job: dict[str, date] = {}
    for job_name, row_status, count, oldest in db.execute(
        select(
            AnalyticsRecomputeQueue.job,
            AnalyticsRecomputeQueue.status,
            func.count(AnalyticsRecomputeQueue.id),
            func.min(AnalyticsRecomputeQueue.bucket_date),
        )
        .where(
            AnalyticsRecomputeQueue.status.in_(
                [RecomputeStatus.PENDING, RecomputeStatus.FAILED]
            )
        )
        .group_by(AnalyticsRecomputeQueue.job, AnalyticsRecomputeQueue.status)
    ).all():
        if row_status == RecomputeStatus.PENDING:
            pending_by_job[job_name] = int(count)
            if oldest is not None:
                oldest_by_job[job_name] = oldest
        else:
            failed_by_job[job_name] = int(count)

    # Every job the system knows about: registered ones (so a job that has never
    # run still shows up as "no watermark") plus any name seen in the queue or
    # the run log (so a retired job's backlog does not vanish from the panel).
    known: set[str] = set(JOBS)
    known.update(pending_by_job)
    known.update(failed_by_job)
    known.update(
        db.execute(select(AnalyticsSyncRun.job).distinct()).scalars().all()
    )

    jobs: list[JobHealth] = []
    last_error: str | None = None
    for job_name in sorted(known):
        recent = list(
            db.execute(
                select(AnalyticsSyncRun)
                .where(AnalyticsSyncRun.job == job_name)
                .order_by(AnalyticsSyncRun.started_at.desc(), AnalyticsSyncRun.id.desc())
                .limit(20)
            )
            .scalars()
            .all()
        )
        latest = recent[0] if recent else None
        last_success = next(
            (r for r in recent if r.status == SyncStatus.SUCCESS), None
        )
        job_error = next((r.error for r in recent if r.error), None)
        if last_error is None and job_error:
            last_error = job_error

        # Consecutive failures since the last success. SKIPPED_LOCKED is skipped
        # over, not counted: another worker held the lock and this tick did
        # nothing, which is normal operation. Counting it turns a busy queue
        # into a false page, and a few of those teach everyone to ignore the
        # real one.
        streak = 0
        for run in recent:
            if run.status == SyncStatus.SKIPPED_LOCKED:
                continue
            if run.status in (SyncStatus.FAILED, SyncStatus.PARTIAL):
                streak += 1
                continue
            break

        watermark = db.execute(
            select(func.max(AnalyticsSyncRun.watermark_date)).where(
                AnalyticsSyncRun.job == job_name
            )
        ).scalar()

        jobs.append(
            JobHealth(
                job=job_name,
                watermark_date=watermark,
                last_run_id=latest.id if latest else None,
                last_run_status=latest.status if latest else None,
                last_run_started_at=latest.started_at if latest else None,
                last_run_finished_at=latest.finished_at if latest else None,
                last_success_at=last_success.finished_at if last_success else None,
                last_error=job_error,
                consecutive_failures=streak,
                pending_buckets=pending_by_job.get(job_name, 0),
                failed_buckets=failed_by_job.get(job_name, 0),
                oldest_pending_bucket=oldest_by_job.get(job_name),
            )
        )

    return AnalyticsHealthResponse(
        generated_at=datetime.now(timezone.utc),
        rollups_enabled=bool(settings.ANALYTICS_ROLLUPS_ENABLED),
        tz_generation=active_generation(db).generation,
        queue_depth=queue_depth,
        oldest_pending_bucket=oldest_pending,
        stale_claims=stale_claims,
        jobs=jobs,
        last_error=last_error,
    )
