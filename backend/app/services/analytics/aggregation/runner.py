"""Executes aggregation jobs: bounded, resumable, and always logged.

A job knows how to rebuild one bucket. The runner decides *which* buckets, in
what order, inside what transaction, within what time budget — and records that
it happened. Those four concerns are here rather than in the jobs because they
are identical for all twelve rollups and getting any of them wrong is invisible
in the output: a rollup table cannot report its own absence.

Bounded and resumable
---------------------
``run_window`` checks the clock **before** each bucket and stops when the budget
is spent. That is what makes the manual "Recompute" button in the admin safe
behind nginx's 60s ``proxy_read_timeout``: the default 45s budget returns a real
answer inside the proxy's patience instead of the browser receiving a 504 while
the recompute carries on invisibly and a second impatient click starts another
one alongside it.

Stopping early is not failing. The run is recorded ``partial`` with
``watermark_date`` set to the last bucket that was **fully** processed, and the
next call over the same window resumes from the bucket after it. Progress is
therefore durable per bucket rather than per run — a recompute of a year does not
have to fit in one HTTP request, and does not lose 300 days of work when the 301st
fails.

One bucket, one transaction
---------------------------
Every ``(job, bucket)`` is committed on its own. A run that is cut short by the
budget, a deploy, or an OOM kill therefore leaves the rollup tables *consistent
but incomplete* — some buckets rebuilt, none half-rebuilt — which is a state the
next tick can finish. The alternative, one transaction per window, means an
interrupted recompute silently discards everything it did and the pipeline never
converges on a window bigger than its own reliability.

``watermark_date`` is per run, deliberately
-------------------------------------------
It is the highest bucket **this run** completed, not the job's all-time high
water mark. A queue drain triggered by a July refund against a March order
legitimately reports March: that is what the run did. A reader wanting a job's
freshness takes ``MAX(watermark_date)`` over its runs; a reader wanting to know
whether the last tick made progress compares consecutive runs. Folding the two
into one column would make both questions unanswerable.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analytics_control import AnalyticsSyncRun, SyncStatus, SyncTrigger
from app.services.analytics.aggregation.base import JobRunResult, get_job
from app.services.analytics.queue import RecomputeQueue
from app.services.analytics.timebox import active_generation

__all__ = ["AggregationRunner", "DEFAULT_BUDGET_MS", "ERROR_MAX_CHARS"]

#: Default wall-clock budget for one call. Sized against nginx's 60s
#: `proxy_read_timeout` with room for request overhead and the response, so the
#: interactive trigger always answers rather than timing out mid-recompute.
DEFAULT_BUDGET_MS = 45_000

#: Width of `analytics_sync_runs.error`. Truncated here rather than letting the
#: driver reject the INSERT and lose the whole run record along with the error.
ERROR_MAX_CHARS = 500

#: Extra lease beyond the drain budget. Buckets claimed but not reached before
#: the budget expires come back to `pending` this long after the call returns,
#: instead of sitting claimed by a worker that has already gone home.
QUEUE_LEASE_SLACK_SECONDS = 30


def _utcnow() -> datetime:
    """Naive UTC — what MySQL DATETIME columns actually store.

    Same reasoning as `queue._utcnow`: `DateTime(timezone=True)` is a no-op on
    MySQL, so an aware datetime written to one of these columns reads back naive
    and any later aware-vs-naive comparison raises. One convention, everywhere in
    this subsystem.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _days(date_from: date, date_to: date) -> list[date]:
    """Bucket dates in the **half-open** window ``[date_from, date_to)``.

    Half-open to match ``timebox.range_bounds_utc`` and every other date range in
    this stack. ``date_from == date_to`` is therefore an empty window, and a
    reversed one is empty rather than an error at this level — `run_window`
    rejects that case with a message, which is more useful than a silent no-op.
    """
    return [date_from + timedelta(days=n) for n in range((date_to - date_from).days)]


class AggregationRunner:
    """Runs aggregation jobs over buckets, with a budget and a run log.

    ``worker_id`` lands in ``analytics_sync_runs.worker_id`` and is the identity
    the recompute queue leases rows to, so a stuck claim can be traced back to a
    container log. Give each worker process its own; the default is for a local
    shell or a single-node cron.

    ``clock`` exists so the budget is testable. Budget exhaustion is a behaviour
    with real consequences — a `partial` status, a watermark, a resumable next
    tick — and pinning it to wall-clock timing would make its test either flaky
    or slow. Production never passes it.
    """

    def __init__(
        self,
        db: Session,
        *,
        worker_id: str = "local",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.db = db
        self.worker_id = worker_id
        self._clock = clock

    # ------------------------------------------------------------------
    # One bucket
    # ------------------------------------------------------------------
    def run_bucket(
        self,
        job_name: str,
        bucket_date: date,
        *,
        tz_generation: int | None = None,
    ) -> JobRunResult:
        """Rebuild a single bucket, logged as its own run.

        The direct entry point — an operator recomputing one day, or a test.
        ``run_window`` and ``drain_queue`` do not call this: they own a run row
        that spans many buckets and would otherwise write one log row per day.
        """
        # Before `_open_run`, and for the same reason `run_window` validates its
        # names up front: the run log is append-only and never cleaned up, so an
        # unknown job name must not leave a row stuck at RUNNING behind it. That
        # state is the signature of a process that died mid-run, and the alert
        # that watches for it cannot afford to be taught to ignore typos.
        get_job(job_name)
        generation = self._generation(tz_generation)
        run = self._open_run(
            job_name,
            SyncTrigger.MANUAL,
            window_from=bucket_date,
            window_to=bucket_date,
            tz_generation=generation,
            days_requested=1,
        )
        try:
            result = self._execute_bucket(job_name, bucket_date, generation)
        except Exception as exc:
            self._close_run(
                run,
                status=SyncStatus.FAILED,
                days_processed=0,
                result=JobRunResult(),
                watermark=None,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        self._close_run(
            run,
            status=SyncStatus.SUCCESS,
            days_processed=1,
            result=result,
            watermark=bucket_date,
        )
        return result

    # ------------------------------------------------------------------
    # A window of buckets
    # ------------------------------------------------------------------
    def run_window(
        self,
        job_names: Sequence[str],
        date_from: date,
        date_to: date,
        *,
        budget_ms: int = DEFAULT_BUDGET_MS,
        trigger: str = SyncTrigger.MANUAL,
    ) -> dict[str, Any]:
        """Rebuild the **half-open** window ``[date_from, date_to)``, oldest first.

        Half-open, matching ``timebox.range_bounds_utc`` and every other date
        range in this stack. ``date_to`` is the first bucket **not** rebuilt; the
        last one processed is ``date_to - 1 day``. ``date_from == date_to`` is an
        empty window and is a clean no-op returning zero counts, not an error —
        "rebuild nothing" is a perfectly sensible thing for a caller with an
        empty date range to ask for, and raising would push the check out to
        every caller.

        One convention, everywhere, is the whole point: a stack that is half-open
        in five places and inclusive in the sixth produces a backfill that
        rebuilds one extra day or misses the last one, and the resulting gap in a
        chart is untraceable months later.

        Buckets are the outer loop and jobs the inner one, so every job advances
        through the window together. Job-outer would let the first job consume
        the whole budget on every call and starve the last one indefinitely —
        it would never once be reached, and nothing in the run log would say so.

        **Resumption.** A previous run of *exactly this window and generation*
        that ended ``partial`` resumes from the bucket after its watermark. The
        match is on the window bounds, so a deliberate recompute of a different
        range always starts at its own beginning, and a window whose last run
        succeeded is recomputed in full rather than skipped.

        Returns a summary of the whole call, with a per-job breakdown. Dates are
        `date` objects; the API layer serialises them. ``window_from`` /
        ``window_to`` in the result echo the half-open window as it was asked
        for; the ``analytics_sync_runs`` columns of the same name are *inclusive*
        per that table's contract, so ``window_to`` there is the last bucket in
        range and the two differ by a day on purpose.
        """
        if date_to < date_from:
            raise ValueError(
                f"date_to ({date_to}) precedes date_from ({date_from}); the "
                "window is half-open [from, to) and must be ordered oldest-first"
            )
        names = list(dict.fromkeys(job_names))
        if not names:
            raise ValueError("run_window needs at least one job name")
        for name in names:
            get_job(name)  # Fail before writing any run rows.

        generation = self._generation(None)
        started = self._clock()
        deadline = started + budget_ms / 1000

        # `analytics_sync_runs.window_to` is documented as inclusive, so the
        # half-open bound is converted once, here, and every read of that column
        # (including `_resume_from`) uses the same converted value.
        last_bucket = date_to - timedelta(days=1)

        resume_from = {
            name: self._resume_from(name, date_from, last_bucket, generation)
            for name in names
        }
        requested = {name: len(_days(resume_from[name], date_to)) for name in names}
        runs = {
            name: self._open_run(
                name,
                trigger,
                window_from=date_from,
                window_to=last_bucket,
                tz_generation=generation,
                days_requested=requested[name],
            )
            for name in names
        }

        processed: dict[str, int] = {name: 0 for name in names}
        totals: dict[str, JobRunResult] = {name: JobRunResult() for name in names}
        watermarks: dict[str, date | None] = {name: None for name in names}
        errors: dict[str, str | None] = {name: None for name in names}
        exhausted = False

        for bucket in _days(min(resume_from.values()), date_to):
            if exhausted:
                break
            for name in names:
                if bucket < resume_from[name]:
                    continue
                # BEFORE the bucket, never after: a check that runs afterwards
                # has already spent the time it was meant to protect.
                if self._clock() >= deadline:
                    exhausted = True
                    break
                try:
                    result = self._execute_bucket(name, bucket, generation)
                except Exception as exc:
                    errors[name] = f"{bucket}: {type(exc).__name__}: {exc}"
                    continue
                totals[name] = totals[name].merged(result)
                processed[name] += 1
                watermarks[name] = bucket

        summary: dict[str, Any] = {}
        for name in names:
            status = self._window_status(
                requested[name], processed[name], errors[name]
            )
            self._close_run(
                runs[name],
                status=status,
                days_processed=processed[name],
                result=totals[name],
                watermark=watermarks[name],
                error=errors[name],
            )
            summary[name] = {
                "status": status,
                "sync_run_id": runs[name].id,
                "days_requested": requested[name],
                "days_processed": processed[name],
                "rows_written": totals[name].rows_written,
                "rows_deleted": totals[name].rows_deleted,
                "watermark_date": watermarks[name],
                "resumed_from": resume_from[name] if resume_from[name] != date_from else None,
                "warnings": list(totals[name].warnings),
                "error": errors[name],
            }

        statuses = {entry["status"] for entry in summary.values()}
        return {
            "status": self._overall_status(statuses),
            "trigger": trigger,
            "worker_id": self.worker_id,
            "tz_generation": generation,
            "window_from": date_from,
            "window_to": date_to,
            "budget_ms": budget_ms,
            "budget_exhausted": exhausted,
            "duration_ms": int((self._clock() - started) * 1000),
            "days_requested": sum(requested.values()),
            "days_processed": sum(processed.values()),
            "rows_written": sum(t.rows_written for t in totals.values()),
            "rows_deleted": sum(t.rows_deleted for t in totals.values()),
            "jobs": summary,
        }

    # ------------------------------------------------------------------
    # The dirty-bucket queue
    # ------------------------------------------------------------------
    def drain_queue(
        self, *, limit: int = 50, budget_ms: int = DEFAULT_BUDGET_MS
    ) -> dict[str, Any]:
        """Claim stale buckets, rebuild them, report each one back to the queue.

        The queue owns claiming, leasing, retry accounting and dead-lettering
        (:class:`~app.services.analytics.queue.RecomputeQueue`); this method owns
        only the "run" in claim -> run -> complete/fail.

        The lease is sized to the budget plus a little slack, so buckets claimed
        but not reached before the budget expires return to ``pending`` shortly
        after this call instead of sitting claimed by a worker that has already
        returned. For the same reason no heartbeat is needed: the lease cannot
        lapse while the call is still inside its own budget.

        A failed bucket is reported with ``fail`` (one attempt spent, retried or
        dead-lettered by the queue) and does **not** abort the drain — one poison
        bucket must not block every other stale bucket behind it.
        """
        queue = RecomputeQueue(self.db)
        started = self._clock()
        deadline = started + budget_ms / 1000
        lease = int(budget_ms / 1000) + QUEUE_LEASE_SLACK_SECONDS

        claimed = queue.claim(
            worker_id=self.worker_id, limit=limit, lease_seconds=lease
        )
        if not claimed:
            return {
                "status": SyncStatus.SUCCESS,
                "worker_id": self.worker_id,
                "claimed": 0,
                "completed": 0,
                "failed": 0,
                "deferred": 0,
                "rows_written": 0,
                "rows_deleted": 0,
                "duration_ms": int((self._clock() - started) * 1000),
                "jobs": {},
            }

        # One `analytics_sync_runs` row per job, because that table is keyed by
        # job — a single row covering a mixed batch could not answer "when did
        # product_daily last run?", which is the only question it is asked.
        by_job: dict[str, list[Any]] = {}
        for row in claimed:
            by_job.setdefault(row.job, []).append(row)
        runs = {
            job: self._open_run(
                job,
                SyncTrigger.QUEUE,
                # NULL window: a queue run's scope is whatever it claimed, not a
                # range someone asked for. Recording the min/max as a "window"
                # would imply the buckets between them were processed too.
                window_from=None,
                window_to=None,
                tz_generation=rows[0].tz_generation,
                days_requested=len(rows),
            )
            for job, rows in by_job.items()
        }

        completed_ids: dict[str, list[int]] = {job: [] for job in by_job}
        totals = {job: JobRunResult() for job in by_job}
        watermarks: dict[str, date | None] = {job: None for job in by_job}
        errors: dict[str, str | None] = {job: None for job in by_job}
        failed = 0
        deferred: list[Any] = []

        for row in claimed:
            if self._clock() >= deadline:
                deferred.append(row)
                continue
            try:
                result = self._execute_bucket(
                    row.job, row.bucket_date, int(row.tz_generation)
                )
            except Exception as exc:
                failed += 1
                errors[row.job] = f"{row.bucket_date}: {type(exc).__name__}: {exc}"
                queue.fail(
                    row.id,
                    worker_id=self.worker_id,
                    error=f"{type(exc).__name__}: {exc}",
                )
                continue
            totals[row.job] = totals[row.job].merged(result)
            completed_ids[row.job].append(int(row.id))
            current = watermarks[row.job]
            if current is None or row.bucket_date > current:
                watermarks[row.job] = row.bucket_date

        completed = 0
        summary: dict[str, Any] = {}
        for job, rows in by_job.items():
            completed += queue.complete(completed_ids[job], worker_id=self.worker_id)
            done = len(completed_ids[job])
            status = self._window_status(len(rows), done, errors[job])
            self._close_run(
                runs[job],
                status=status,
                days_processed=done,
                result=totals[job],
                watermark=watermarks[job],
                error=errors[job],
            )
            summary[job] = {
                "status": status,
                "sync_run_id": runs[job].id,
                "claimed": len(rows),
                "completed": done,
                "rows_written": totals[job].rows_written,
                "rows_deleted": totals[job].rows_deleted,
                "watermark_date": watermarks[job],
                "warnings": list(totals[job].warnings),
                "error": errors[job],
            }

        return {
            "status": self._overall_status({e["status"] for e in summary.values()}),
            "worker_id": self.worker_id,
            "claimed": len(claimed),
            "completed": completed,
            "failed": failed,
            # Left claimed on purpose; their lease returns them to `pending`.
            "deferred": len(deferred),
            "budget_exhausted": bool(deferred),
            "rows_written": sum(t.rows_written for t in totals.values()),
            "rows_deleted": sum(t.rows_deleted for t in totals.values()),
            "duration_ms": int((self._clock() - started) * 1000),
            "jobs": summary,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _generation(self, tz_generation: int | None) -> int:
        """Resolve the generation to compute under. Seeds one if none exists."""
        if tz_generation is not None:
            return int(tz_generation)
        return int(active_generation(self.db).generation)

    def _execute_bucket(
        self, job_name: str, bucket_date: date, tz_generation: int
    ) -> JobRunResult:
        """One bucket, one transaction. The runner commits; the job never does."""
        job = get_job(job_name)
        try:
            result = job.run(self.db, bucket_date, tz_generation)
            self.db.commit()
            return result
        except Exception:
            self.db.rollback()
            raise

    def _resume_from(
        self, job_name: str, date_from: date, window_to: date, tz_generation: int
    ) -> date:
        """Where this job should start, given its last run over this window.

        ``window_to`` is the **inclusive** last bucket, i.e. the value stored in
        ``analytics_sync_runs.window_to``, so the lookup matches what was written
        rather than the half-open bound the caller passed.
        """
        last = self.db.execute(
            select(AnalyticsSyncRun)
            .where(
                AnalyticsSyncRun.job == job_name,
                AnalyticsSyncRun.window_from == date_from,
                AnalyticsSyncRun.window_to == window_to,
                AnalyticsSyncRun.tz_generation == tz_generation,
                AnalyticsSyncRun.status != SyncStatus.RUNNING,
            )
            .order_by(AnalyticsSyncRun.started_at.desc(), AnalyticsSyncRun.id.desc())
            .limit(1)
        ).scalars().first()

        if last is None or last.status != SyncStatus.PARTIAL:
            return date_from
        if last.watermark_date is None or last.watermark_date < date_from:
            return date_from
        return last.watermark_date + timedelta(days=1)

    @staticmethod
    def _window_status(requested: int, processed: int, error: str | None) -> str:
        """SUCCESS / PARTIAL / FAILED for one job's slice of a run.

        `PARTIAL` rather than `FAILED` whenever anything was written: the
        watermark may still have advanced, and treating a mostly-successful run
        as a total failure would hide the progress the next tick depends on.
        """
        if processed >= requested and error is None:
            return SyncStatus.SUCCESS
        if processed == 0 and error is not None:
            return SyncStatus.FAILED
        return SyncStatus.PARTIAL

    @staticmethod
    def _overall_status(statuses: set[str]) -> str:
        if not statuses or statuses == {SyncStatus.SUCCESS}:
            return SyncStatus.SUCCESS
        if statuses == {SyncStatus.FAILED}:
            return SyncStatus.FAILED
        return SyncStatus.PARTIAL

    def _open_run(
        self,
        job_name: str,
        trigger: str,
        *,
        window_from: date | None,
        window_to: date | None,
        tz_generation: int,
        days_requested: int,
    ) -> AnalyticsSyncRun:
        """Write the `RUNNING` row before any work starts.

        Committed immediately and on purpose: a row that only appeared once the
        run finished could never record a run that *didn't*. A row stuck at
        `RUNNING` is the sole trace an OOM-killed pipeline leaves behind.
        """
        run = AnalyticsSyncRun(
            job=job_name,
            trigger=trigger,
            status=SyncStatus.RUNNING,
            worker_id=self.worker_id,
            window_from=window_from,
            window_to=window_to,
            tz_generation=tz_generation,
            days_requested=days_requested,
            days_processed=0,
            rows_written=0,
            rows_deleted=0,
            started_at=_utcnow(),
        )
        self.db.add(run)
        self.db.commit()
        return run

    def _close_run(
        self,
        run: AnalyticsSyncRun,
        *,
        status: str,
        days_processed: int,
        result: JobRunResult,
        watermark: date | None,
        error: str | None = None,
    ) -> None:
        """Complete the run row. Written once; never touched again after this."""
        finished = _utcnow()
        run.status = status
        run.days_processed = days_processed
        run.rows_written = result.rows_written
        run.rows_deleted = result.rows_deleted
        run.watermark_date = watermark
        run.finished_at = finished
        run.duration_ms = max(
            int((finished - run.started_at).total_seconds() * 1000), 0
        )
        # Job warnings ride in `error` when nothing worse happened: a bucket whose
        # revenue bridge did not balance is exactly what someone reading the run
        # log needs to see, and there is no other column for it.
        message = error or ("; ".join(result.warnings) if result.warnings else None)
        run.error = message[:ERROR_MAX_CHARS] if message else None
        self.db.commit()
