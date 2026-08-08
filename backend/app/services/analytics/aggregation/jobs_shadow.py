"""``shadow_compare``: the runner that makes shadow mode actually happen.

:mod:`app.services.analytics.shadow` computes the legacy-vs-new comparison
correctly and completely. Until this module existed, nothing called it on a
schedule — which meant the retirement gate had no evidence to read, and
"the comparison was clean for 30 days" and "the comparison never ran" produced
identical silence. That is the exact failure ``shadow.SHADOW_JOB_NAME`` was
reserved against, and it had gone unnoticed because a job that does not run
looks like a job with nothing to report.

This module is **wiring**. It does not restate, re-derive or second-guess any
number in ``shadow.py``: it calls :func:`shadow.compare` for one bucket, writes
down what came back, and reads that history back for the gate.

How it is scheduled
-------------------
By being in ``JOBS``, and by nothing else. ``worker._run_scheduled`` runs
``sorted(JOBS)`` over the trailing window on every tick, and the admin
recompute endpoint and the dirty-bucket queue both resolve names through the
same registry, so registering here is the whole integration. No worker change,
no second scheduler, no cron entry that can rot independently of the code.

Why the run is persisted, and where
-----------------------------------
The gate spans **thirty consecutive reporting days**. A
:class:`~app.services.analytics.shadow.ShadowReport` held in memory answers
nothing about the twenty-nine days before it, so the verdict for each bucket has
to survive the process that produced it.

It is written to ``analytics_sync_runs`` — the table that already exists for
exactly this ("the run log is the only place a *non-event* leaves a trace") —
under the job name ``shadow_compare``, one row per bucket. No new table: a
thirteenth analytics table whose only reader is one gate would be a schema
migration, a retention policy and a backup concern in exchange for what an
existing append-only run log already provides.

Unexplained differences are additionally raised as ``AnalyticsAlert`` rows by
:func:`shadow.compare` itself, which is where the amount, the band and the
triage context live.

The verdict rides in ``analytics_sync_runs.error``
--------------------------------------------------
That table has no JSON column, and this change may not add one. ``error`` is
already the run log's free-text channel for things a job noticed but did not
die of — :meth:`AggregationRunner._close_run` puts job warnings there for the
same reason. So the per-bucket verdict is encoded there as one line, prefixed
:data:`VERDICT_MARKER`, in a form a human can read and :func:`_decode_verdict`
can parse back. What it carries is exactly what the gate needs and nothing more:
the counts, and the four gate metrics with their measured legacy and new values,
so a "not ready" reason can quote real figures rather than the fact that a figure
once existed.

Why the row is always ``success``
---------------------------------
The status of this row describes whether the **comparison ran**, not whether the
two systems agreed. A bucket that diverged is recorded — in ``rows_written``, in
the encoded verdict, and in an open alert — and none of that is a failed run.

It also may not be ``partial``, for a concrete reason:
:meth:`AggregationRunner._resume_from` treats the latest ``partial`` run over a
matching ``(job, window_from, window_to)`` as "resume after its watermark". A
single-bucket row written for bucket *D* has ``window_from == window_to == D``,
which is exactly the shape of a runner row for the one-day window ``[D, D+1)``.
Marking it ``partial`` with a watermark of *D* would therefore tell the next tick
to skip *D* — silently retiring the comparison for precisely the days that
diverged, which is the one set of days that must be re-compared until it is
fixed. Any status other than ``partial`` is inert to that lookup; ``success``
is the honest one.

Cost, and why this must not run inside a request
------------------------------------------------
:func:`shadow.compare` runs the *real* legacy services, which query ``orders``
live, plus the margin cascade and the rollups — roughly two dozen aggregate
queries per call. It is bounded here to **one reporting day** per invocation,
the same contract every other job honours, and that keeps a single bucket well
inside the share of :data:`~app.services.analytics.aggregation.runner.DEFAULT_BUDGET_MS`
it can fairly claim. :data:`BUCKET_BUDGET_MS` is the point past which it is
crowding the other twelve jobs out of the runner's budget; exceeding it does not
fail the bucket, it warns, because a slow comparison is still evidence and a
missing one is not.

Nothing here is reachable from a request handler except through the admin
recompute endpoint, which goes through the runner and is bounded by its budget.
:func:`readiness` is the only function here safe to call from a request, and it
is safe because it reads the run log and the alerts table and computes nothing.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.analytics_control import AnalyticsSyncRun, SyncStatus, SyncTrigger
from app.services.analytics.aggregation.base import JobRunResult, register
from app.services.analytics.shadow import (
    ANCHOR_METRIC,
    CONSECUTIVE_DAYS_REQUIRED,
    GATE_METRICS,
    SHADOW_JOB_NAME,
    MetricComparison,
    ShadowReport,
    compare,
    is_ready_to_retire_legacy,
)

__all__ = [
    "ShadowCompareJob",
    "readiness",
    "BUCKET_BUDGET_MS",
    "VERDICT_MARKER",
    "ERROR_MAX_CHARS",
]


#: Prefix of the encoded per-bucket verdict in ``analytics_sync_runs.error``.
#: Versioned, so a later encoding can be introduced without a reader having to
#: guess which one it is looking at — an unrecognised prefix is skipped rather
#: than misparsed, and a skipped day is reported as *not covered*, which is the
#: safe direction for a gate.
VERDICT_MARKER = "shadow-verdict/1"

#: Width of ``analytics_sync_runs.error``. Same constant as the runner's, kept
#: local because the encoder truncates against it before the driver would.
ERROR_MAX_CHARS = 500

#: Soft per-bucket budget, in milliseconds.
#:
#: The runner's default budget of 45s covers *every* job over *every* bucket in
#: one call. With thirteen registered jobs and the worker's trailing window,
#: one bucket of one job can fairly claim on the order of a second. Two seconds
#: is where this job starts pushing the others past the deadline — at which
#: point the runner stops before them, and a rollup silently fails to rebuild
#: because the comparison that is only *watching* it took too long.
BUCKET_BUDGET_MS = 2_000

#: ``worker_id`` when the run could not be attributed to an enclosing runner
#: run — a direct ``job.run(...)`` from a script or a test. Naming the writer is
#: more honest than inventing a hostname.
_UNATTRIBUTED_WORKER = "shadow-compare-job"


def _utcnow() -> datetime:
    """Naive UTC — what MySQL ``DATETIME`` columns actually store.

    Same convention as ``runner._utcnow`` and ``queue._utcnow``:
    ``DateTime(timezone=True)`` is a no-op on MySQL, so an aware datetime
    written to one of these columns reads back naive and every later comparison
    against it raises.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ===========================================================================
# The persisted verdict
# ===========================================================================


@dataclass(frozen=True)
class _GateFigure:
    """One gate metric as it was measured, as persisted and read back."""

    metric: str
    unit: str
    legacy: int | None
    new: int | None


@dataclass(frozen=True)
class _Verdict:
    """One bucket's comparison, recovered from ``analytics_sync_runs``.

    This is a **reconstruction**, and it is deliberately narrower than the
    :class:`ShadowReport` it came from. Only what the retirement gate reads was
    persisted: the counts, and the gate metrics with their measured values. The
    bridge terms, the boundary orders and the per-category comparisons are not
    here and are not claimed to be — they were evidence for a decision that was
    already taken and recorded when the comparison ran.
    """

    bucket: date
    tz_generation: int
    differences: int
    expected: int
    unexplained: int
    alerts: int
    gate: tuple[_GateFigure, ...]
    unexplained_metrics: tuple[str, ...]
    recorded_at: datetime

    def as_report(self) -> ShadowReport:
        """The narrowest ShadowReport that answers the gate's questions honestly.

        ``legacy_window`` is exact: :func:`shadow.compare` derives it from the
        reporting dates as UTC instants and nothing else, so it can be
        recomputed rather than stored. The aligned window cannot be — it depends
        on the store timezone as it was at comparison time — so it is not
        claimed, and the fields that were never persisted say so rather than
        being filled with plausible-looking values.
        """
        legacy_start = datetime(
            self.bucket.year, self.bucket.month, self.bucket.day, tzinfo=timezone.utc
        )
        legacy_end = legacy_start + timedelta(days=1)
        return ShadowReport(
            date_from=self.bucket,
            date_to=self.bucket + timedelta(days=1),
            generated_at=self.recorded_at.replace(tzinfo=timezone.utc),
            timezone_name="unrecorded",
            tz_generation=self.tz_generation,
            legacy_window=(legacy_start, legacy_end),
            aligned_window=(legacy_start, legacy_end),
            comparisons=tuple(
                MetricComparison(
                    metric=figure.metric,
                    unit=figure.unit,
                    legacy=figure.legacy,
                    # Not persisted: the tz split is a comparison-time
                    # observation and the gate does not read it.
                    legacy_aligned=None,
                    new=figure.new,
                    permitted=frozenset(),
                    tolerance_override=0 if figure.metric == ANCHOR_METRIC else None,
                    missing_reason=(
                        ""
                        if figure.new is not None
                        else "the new subsystem reported no value for this metric when "
                        "the comparison ran"
                    ),
                    new_source="analytics_sync_runs (recorded by shadow_compare)",
                )
                for figure in self.gate
            ),
            # The unexplained differences of this bucket are carried as reasons
            # by `readiness`, from the counts and metric names recorded here.
            # They are not rebuilt as `Difference` objects, because a Difference
            # states a delta, an explained amount and a residual to the paisa,
            # and inventing those three numbers to fill the shape would be a
            # fabricated figure in a report about fabricated figures.
            differences=(),
            warnings=(
                "reconstructed from analytics_sync_runs; only the difference "
                "counts and the gate metrics were persisted",
            ),
        )

    def unexplained_reason(self) -> str:
        named = ", ".join(self.unexplained_metrics) if self.unexplained_metrics else "-"
        return (
            f"{self.bucket}: the shadow_compare run recorded "
            f"{self.unexplained} unexplained difference(s) on {named}; a bucket "
            "that diverged is not evidence of equivalence. Fix the divergence and "
            "re-run the bucket, or resolve the alert it raised with a note saying "
            "why the number is right after all"
        )


def _encode_verdict(report: ShadowReport) -> str:
    """One line of ``analytics_sync_runs.error`` describing this bucket.

    Space-separated ``key=value``; no value contains a space, because metric
    names, units and integers never do. Truncated against
    :data:`ERROR_MAX_CHARS` by dropping *named metrics* from the tail — the
    counts and the gate figures are what the gate reads and are never dropped.
    """
    gate = []
    for metric in GATE_METRICS:
        comparison = report.comparison(metric)
        if comparison is None:
            # Absent on purpose: `gate_metrics_match` reports "was not compared
            # in this report", which is the truth and is actionable.
            continue
        gate.append(
            ":".join(
                (
                    comparison.metric,
                    comparison.unit,
                    "-" if comparison.legacy is None else str(comparison.legacy),
                    "-" if comparison.new is None else str(comparison.new),
                )
            )
        )

    unexplained_metrics: list[str] = []
    for difference in report.unexplained:
        if difference.metric not in unexplained_metrics:
            unexplained_metrics.append(difference.metric)

    head = (
        f"{VERDICT_MARKER}"
        f" diff={len(report.differences)}"
        f" expected={len(report.expected)}"
        f" unexplained={len(report.unexplained)}"
        f" alerts={len(report.alert_ids)}"
        f" gate={','.join(gate) if gate else '-'}"
    )
    while True:
        tail = (
            f" unexplained_metrics={','.join(unexplained_metrics)}"
            if unexplained_metrics
            else ""
        )
        if len(head) + len(tail) <= ERROR_MAX_CHARS or not unexplained_metrics:
            return (head + tail)[:ERROR_MAX_CHARS]
        unexplained_metrics.pop()


def _decode_verdict(run: AnalyticsSyncRun) -> _Verdict | None:
    """Recover a verdict from a run row, or ``None`` if it is not one.

    Anything unparseable returns ``None`` and the day is then reported as *not
    covered*. That is the safe direction: a gate that reads a corrupted record
    as "clean" is worse than one that reads it as "no evidence".
    """
    text = (run.error or "").strip()
    if not text.startswith(VERDICT_MARKER) or run.window_from is None:
        return None
    fields: dict[str, str] = {}
    for token in text.split(" ")[1:]:
        key, _, value = token.partition("=")
        if key:
            fields[key] = value

    def _count(key: str) -> int:
        try:
            return int(fields.get(key, "0"))
        except ValueError:
            return 0

    gate: list[_GateFigure] = []
    for entry in fields.get("gate", "-").split(","):
        parts = entry.split(":")
        if len(parts) != 4:
            continue
        metric, unit, legacy, new = parts

        def _value(raw: str) -> int | None:
            try:
                return None if raw == "-" else int(raw)
            except ValueError:
                return None

        gate.append(_GateFigure(metric, unit, _value(legacy), _value(new)))

    named = fields.get("unexplained_metrics", "")
    return _Verdict(
        bucket=run.window_from,
        tz_generation=int(run.tz_generation or 1),
        differences=_count("diff"),
        expected=_count("expected"),
        unexplained=_count("unexplained"),
        alerts=_count("alerts"),
        gate=tuple(gate),
        unexplained_metrics=tuple(m for m in named.split(",") if m),
        recorded_at=run.finished_at or run.started_at,
    )


# ===========================================================================
# The job
# ===========================================================================


class ShadowCompareJob:
    """Compares the legacy pages and the new subsystem for one reporting day.

    It writes no rollup. ``rows_written`` is the number of **differences
    recorded** for the bucket — expected and unexplained together — because that
    is the count this job actually produced and the one whose sudden change is
    worth seeing in the run log. A day whose difference count jumps from four to
    forty has had something happen to it whether or not any single difference
    was unexplained.

    Idempotent, like every job here. Re-running a bucket re-measures it and
    appends a fresh verdict row (the run log is append-only by contract, so a
    second run is a second row and :func:`readiness` reads the latest per
    bucket) and raises **no duplicate alert** — ``shadow._raise_alerts``
    de-duplicates on ``(rule, metric, dimension value, bucket, OPEN)`` and
    returns the existing id, which ``test_analytics_shadow_job`` verifies rather
    than assumes.
    """

    name = SHADOW_JOB_NAME

    def run(
        self, db: Session, bucket_date: date, tz_generation: int
    ) -> JobRunResult:
        started = time.monotonic()
        report = compare(
            db,
            bucket_date,
            bucket_date + timedelta(days=1),
            alert=True,
            # The runner owns the run row it opened; this job owns the
            # per-bucket verdict row it writes below. Letting `compare` log as
            # well would put a third row in the log for one bucket, one of them
            # `partial` with a watermark — see the module docstring.
            log_run=False,
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)

        warnings: list[str] = []
        if report.tz_generation != tz_generation:
            warnings.append(
                f"asked to compare bucket {bucket_date} under generation "
                f"{tz_generation} but the active generation is "
                f"{report.tz_generation}; shadow.compare always measures the "
                "active one, so this comparison is about a generation nobody "
                "asked for"
            )
        if report.differences or report.warnings:
            warnings.append(
                f"{len(report.differences)} difference(s) recorded for "
                f"{bucket_date}: {len(report.expected)} expected, "
                f"{len(report.unexplained)} unexplained, "
                f"{len(report.alert_ids)} alert(s)"
            )
        for difference in report.unexplained:
            warnings.append(f"unexplained: {difference.describe()}")
        warnings.extend(report.warnings)
        if elapsed_ms > BUCKET_BUDGET_MS:
            warnings.append(
                f"this bucket took {elapsed_ms}ms, over the {BUCKET_BUDGET_MS}ms "
                "per-bucket budget; shadow_compare runs the legacy services live "
                "against `orders` and is now taking budget the rollup jobs need"
            )

        self._record(db, report, bucket_date, elapsed_ms)
        return JobRunResult(
            rows_written=len(report.differences),
            rows_deleted=0,
            warnings=tuple(warnings),
        )

    # ------------------------------------------------------------------
    def _record(
        self,
        db: Session,
        report: ShadowReport,
        bucket_date: date,
        elapsed_ms: int,
    ) -> None:
        """Append this bucket's verdict to ``analytics_sync_runs``.

        Flushed, never committed: the runner owns the transaction, so the
        verdict, the alerts it raised and the rollup state it measured land
        together or not at all.
        """
        enclosing = self._enclosing_run(db)
        now = _utcnow()
        db.add(
            AnalyticsSyncRun(
                job=SHADOW_JOB_NAME,
                # Inherited from the run the runner opened for this job, so the
                # verdict row says truthfully what caused it and which process
                # wrote it. A direct call has neither, and says so.
                trigger=(enclosing.trigger if enclosing else SyncTrigger.MANUAL),
                status=SyncStatus.SUCCESS,
                worker_id=(
                    (enclosing.worker_id or _UNATTRIBUTED_WORKER)
                    if enclosing
                    else _UNATTRIBUTED_WORKER
                )[:64],
                # Inclusive, per this table's contract: one bucket, so both
                # bounds are it.
                window_from=bucket_date,
                window_to=bucket_date,
                tz_generation=report.tz_generation,
                days_requested=1,
                days_processed=1,
                rows_written=len(report.differences),
                rows_deleted=0,
                watermark_date=bucket_date,
                duration_ms=elapsed_ms,
                started_at=now,
                finished_at=now,
                error=_encode_verdict(report),
            )
        )
        db.flush()

    @staticmethod
    def _enclosing_run(db: Session) -> AnalyticsSyncRun | None:
        """The runner's own ``RUNNING`` row for this job, if there is one.

        ``AggregationRunner._open_run`` commits before any bucket executes, so
        it is visible here. Uses ``ix_analytics_sync_runs_job_started``. A stale
        row left by a killed worker would be picked up; that mis-attributes a
        trigger, which is a cosmetic loss next to fabricating one.
        """
        return (
            db.execute(
                select(AnalyticsSyncRun)
                .where(
                    AnalyticsSyncRun.job == SHADOW_JOB_NAME,
                    AnalyticsSyncRun.status == SyncStatus.RUNNING,
                )
                .order_by(
                    AnalyticsSyncRun.started_at.desc(), AnalyticsSyncRun.id.desc()
                )
                .limit(1)
            )
            .scalars()
            .first()
        )


# ===========================================================================
# Reading the history back
# ===========================================================================


def _verdict_rows(db: Session, floor: date, ceiling: date) -> list[AnalyticsSyncRun]:
    return list(
        db.execute(
            select(AnalyticsSyncRun)
            .where(
                AnalyticsSyncRun.job == SHADOW_JOB_NAME,
                AnalyticsSyncRun.window_from.is_not(None),
                AnalyticsSyncRun.window_from == AnalyticsSyncRun.window_to,
                AnalyticsSyncRun.window_from >= floor,
                AnalyticsSyncRun.window_from <= ceiling,
                AnalyticsSyncRun.error.like(f"{VERDICT_MARKER}%"),
            )
            .order_by(
                AnalyticsSyncRun.started_at.asc(), AnalyticsSyncRun.id.asc()
            )
        )
        .scalars()
        .all()
    )


def recorded_verdicts(
    db: Session,
    *,
    days: int = CONSECUTIVE_DAYS_REQUIRED,
    as_of: date | None = None,
) -> list[_Verdict]:
    """The latest verdict for each of the ``days`` reporting days ending ``as_of``.

    ``as_of`` defaults to the most recent bucket any shadow run has recorded,
    which is what the gate means by "the last thirty days": thirty days ending
    at the last day actually compared, not thirty days ending today. A pipeline
    that stopped a week ago must not look ready because the week it missed is
    outside the window it is being judged on — and it does not, because those
    days come back missing.

    Latest-per-bucket, because the run log is append-only: re-comparing a day
    after fixing a divergence appends a second row, and the fixed one is the
    current truth about that day.
    """
    ceiling = as_of
    if ceiling is None:
        ceiling = db.execute(
            select(func.max(AnalyticsSyncRun.window_from)).where(
                AnalyticsSyncRun.job == SHADOW_JOB_NAME,
                AnalyticsSyncRun.window_from == AnalyticsSyncRun.window_to,
                AnalyticsSyncRun.error.like(f"{VERDICT_MARKER}%"),
            )
        ).scalar()
    if ceiling is None:
        return []

    floor = ceiling - timedelta(days=max(days, 1) - 1)
    latest: dict[date, _Verdict] = {}
    for run in _verdict_rows(db, floor, ceiling):
        verdict = _decode_verdict(run)
        if verdict is not None:
            latest[verdict.bucket] = verdict
    return [latest[bucket] for bucket in sorted(latest)]


def readiness(
    db: Session,
    *,
    days: int = CONSECUTIVE_DAYS_REQUIRED,
    as_of: date | None = None,
) -> tuple[bool, list[str]]:
    """May the legacy Sales and Profit pages be retired yet, and if not, why not?

    Reads the persisted history — it re-runs nothing and is cheap enough to
    serve from a request — and hands it to
    :func:`shadow.is_ready_to_retire_legacy`, which owns the three conditions.
    ``db`` is passed through so an open shadow alert blocks the gate whenever it
    was raised, including from outside the window.

    Returns ``(ready, reasons)`` where ``reasons`` is why it is **not** ready,
    one specific line at a time: how many days of evidence are still missing and
    which are the first and last of them, which metric differs by how much on
    which day, and which alert is still open. ``ready`` is exactly "there are no
    reasons", so "not ready" without a reason cannot be returned.
    """
    verdicts = recorded_verdicts(db, days=days, as_of=as_of)
    ready, reasons = is_ready_to_retire_legacy(
        [verdict.as_report() for verdict in verdicts], db=db
    )
    # The gate reads a report's `unexplained` tuple, which a reconstruction
    # cannot honestly rebuild (see `_Verdict.as_report`). The count and the
    # metric names were recorded, so the reason is stated here instead, and it
    # blocks exactly as an in-memory report would have.
    for verdict in verdicts:
        if verdict.unexplained:
            reason = verdict.unexplained_reason()
            if reason not in reasons:
                reasons.append(reason)
    return (not reasons), reasons


register(ShadowCompareJob())
