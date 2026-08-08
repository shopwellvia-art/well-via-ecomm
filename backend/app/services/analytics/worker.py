"""The analytics worker process: one image, two roles, one entrypoint.

    python -m app.services.analytics.worker

Why a process rather than an in-process scheduler
-------------------------------------------------
Uvicorn runs four request workers. An APScheduler-style scheduler living inside
the app would exist in all four, so every tick fires **four times** from four
processes against the same buckets. The recompute queue's lease would absorb the
damage, but three of the four would spend each tick colliding on claims instead
of serving requests — and the fourth would still be doing minutes of aggregation
on a process the storefront needs. Aggregation is also continuous rather than
periodic (a refund at 02:00, a settlement batch at 06:00), which is a loop's
shape and not a cron's.

Two roles, one entrypoint
-------------------------
``ANALYTICS_WORKER_ROLE`` selects the loop so a single image and a single
command serve both containers; the two differ only in what one tick does.

``rollup``    reclaim expired leases -> drain the recompute queue -> run the due
              scheduled window -> prune -> sleep.
``delivery``  drain the GA4 outbox -> sleep. Sends nothing until GA4 is
              configured — see :func:`deliver_pending`.

Everything in a tick is wrapped
-------------------------------
One poison bucket must not kill the process. Each phase catches, logs, and
continues; the runner already reports per-bucket failures to
:meth:`RecomputeQueue.fail`, which owns retry accounting and dead-lettering.
A worker that exits on the first bad bucket under ``restart: always`` becomes a
crash loop that drains nothing, which is the same outage as being down but
harder to read.

Shutdown is part of correctness
-------------------------------
Docker sends ``SIGTERM`` on ``docker compose down``. A worker that ignores it is
killed mid-bucket and leaves its rows ``claimed`` by a process that no longer
exists — invisible until the lease expires minutes later. So SIGTERM/SIGINT stop
new claims, let the current call finish its bucket, release this worker's leases
explicitly, and exit 0.
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.analytics_control import RecomputeStatus, SyncTrigger
from app.services.analytics.aggregation import JOBS, AggregationRunner
from app.services.analytics.queue import RecomputeQueue
from app.services.analytics.timebox import local_day, store_timezone

__all__ = [
    "ROLE_ROLLUP",
    "ROLE_DELIVERY",
    "WorkerConfig",
    "AnalyticsWorker",
    "deliver_pending",
    "worker_identity",
    "main",
]

log = logging.getLogger("analytics.worker")

ROLE_ROLLUP = "rollup"
ROLE_DELIVERY = "delivery"
ROLES = (ROLE_ROLLUP, ROLE_DELIVERY)

#: `analytics_recompute_queue.claimed_by` / `analytics_sync_runs.worker_id` are
#: both VARCHAR(64). Truncating here keeps a long pod name from turning into a
#: driver-side error that loses the whole claim.
WORKER_ID_MAX_CHARS = 64

#: Exit codes. 0 for "correctly did nothing" (flag off, or a clean shutdown);
#: non-zero only for a misconfiguration a human must fix, where a restart loop
#: is the *desired* signal rather than noise.
EXIT_OK = 0
EXIT_BAD_CONFIG = 2


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    """Read a positive int from the environment, falling back loudly.

    A typo'd interval must not become 0 and spin the loop at 100% CPU, so an
    unparseable or out-of-range value logs and uses the default rather than
    silently becoming whatever `int()` happened to produce.
    """
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        log.warning("%s=%r is not an integer; using %d", name, raw, default)
        return default
    if value < minimum:
        log.warning("%s=%d is below the minimum %d; using %d", name, value, minimum, default)
        return default
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def worker_identity() -> str:
    """A unique, attributable id for this process: ``<hostname>-<pid>``.

    Lease ownership has to point at something a human can open. "Some rows are
    stuck claimed" is not actionable; "container `analytics-rollup-7f4c9` died at
    04:12" is, and it is the difference between an incident you can investigate
    and one you can only observe. Overridable via `ANALYTICS_WORKER_ID` for
    orchestrators that already have a better name for the process.
    """
    override = os.environ.get("ANALYTICS_WORKER_ID", "").strip()
    if override:
        return override[:WORKER_ID_MAX_CHARS]
    return f"{socket.gethostname()}-{os.getpid()}"[:WORKER_ID_MAX_CHARS]


class WorkerConfig:
    """Everything the loop is allowed to tune, resolved once at startup.

    Read from the process environment rather than `Settings`: these configure a
    *process* (how fast it ticks, how many rows it claims), not the application,
    and the two containers running this image want different values for the same
    deployed app.
    """

    def __init__(self, role: str) -> None:
        self.role = role
        default_interval = 15 if role == ROLE_ROLLUP else 30
        #: Bounded sleep between ticks. Bounded on purpose: an unbounded wait on
        #: a condition would need something to signal it, and nothing does —
        #: work arrives as rows written by other processes.
        self.interval_sec = _env_int("ANALYTICS_WORKER_INTERVAL_SEC", default_interval)
        self.batch_size = _env_int("ANALYTICS_WORKER_BATCH_SIZE", 25)
        #: Must exceed the slowest single bucket or a healthy worker is reclaimed
        #: from underneath itself and two workers rebuild the same bucket.
        self.lease_sec = _env_int("ANALYTICS_WORKER_LEASE_SEC", 300)
        self.max_attempts = _env_int("ANALYTICS_WORKER_MAX_ATTEMPTS", 5)
        self.schedule_interval_sec = _env_int(
            "ANALYTICS_WORKER_SCHEDULE_INTERVAL_SEC", 300
        )
        self.schedule_lookback_days = _env_int(
            "ANALYTICS_WORKER_SCHEDULE_LOOKBACK_DAYS", 3
        )
        self.prune_interval_sec = _env_int("ANALYTICS_WORKER_PRUNE_INTERVAL_SEC", 3600)
        self.retention_days = _env_int("ANALYTICS_WORKER_RETENTION_DAYS", 30)
        #: Cap on rows deleted per prune. A single unbounded DELETE over a year
        #: of `done` rows locks the queue table for as long as it takes, which
        #: stalls every claim behind it.
        self.prune_batch = _env_int("ANALYTICS_WORKER_PRUNE_BATCH", 5000)
        self.once = _env_bool("ANALYTICS_WORKER_ONCE", False)
        #: A drain must fit comfortably inside the lease it was granted.
        self.drain_budget_ms = _env_int(
            "ANALYTICS_WORKER_DRAIN_BUDGET_MS",
            max(1000, (self.lease_sec - 30) * 1000),
            minimum=1000,
        )

    def describe(self) -> str:
        return (
            f"role={self.role} interval={self.interval_sec}s batch={self.batch_size} "
            f"lease={self.lease_sec}s schedule_every={self.schedule_interval_sec}s "
            f"lookback={self.schedule_lookback_days}d retention={self.retention_days}d"
        )


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------
class GracefulShutdown:
    """SIGTERM/SIGINT -> "stop after the current unit of work".

    The handler only sets an Event. Doing real work in a signal handler is how a
    shutdown ends up interleaved with a half-written bucket; setting a flag the
    loop already checks keeps the stop points exactly where they were reasoned
    about.

    A **second** signal escalates: an operator who sends SIGTERM twice is telling
    you they are not waiting, and continuing to finish the bucket at that point
    just delays a SIGKILL. The lease is the backstop either way.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self.escalated = False

    def install(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, self._handle)
            except ValueError:  # pragma: no cover - not the main thread
                log.warning("cannot install handler for %s outside the main thread", sig)

    def _handle(self, signum: int, _frame: Any) -> None:
        if self._event.is_set():
            self.escalated = True
            log.warning(
                "signal %s received again — escalating, leases will expire on their own",
                signum,
            )
            return
        log.info(
            "signal %s received — finishing the current bucket, then shutting down",
            signum,
        )
        self._event.set()

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    def sleep(self, seconds: int) -> None:
        """Interruptible sleep. Returns immediately once a stop is requested,
        so `docker compose down` is not held up by a 15-second nap."""
        self._event.wait(timeout=seconds)

    def trigger(self) -> None:
        """Request a stop programmatically (used by `ANALYTICS_WORKER_ONCE`)."""
        self._event.set()


# ---------------------------------------------------------------------------
# GA4 delivery
# ---------------------------------------------------------------------------
def deliver_pending(
    db: Session, *, limit: int = 100, budget_ms: int = 20_000
) -> dict[str, Any]:
    """Drain `analytics_event_outbox` through the Measurement Protocol.

    A thin adapter: the outbox owns claiming, retry, backoff, dead-lettering and
    the guarantee that a row is marked `delivered` only after a send actually
    succeeded. This function exists to hold the worker's shape — bounded batch,
    bounded wall clock, a dict the tick can log — and nothing else.

    **If GA4 is not configured, nothing is marked delivered.** The result says
    `configured: False` with a machine-readable `reason` (`tracking_disabled`,
    `no_measurement_id`, `no_api_secret`, ...) and every row stays `pending`.
    That is the important half of this function: a `pending` row is a debt GA4
    is still owed, and discharging it without paying it would lose the
    conversions permanently while leaving a queue that looks healthy.

    `implemented` is kept in the result for the log lines and dashboards that
    read the older shape.
    """
    # Imported here rather than at module scope so this file's import graph —
    # which the entrypoint pays on every container start — does not grow an
    # httpx-and-settings dependency for the rollup role, which never sends
    # anything.
    from app.services.analytics.outbox import drain

    result = drain(db, limit=limit, budget_ms=budget_ms)
    return {
        "attempted": result["attempted"],
        "delivered": result["sent"],
        "failed": result["failed"],
        "suppressed": result["suppressed"],
        "skipped": result["skipped"],
        "remaining": result["remaining"],
        "configured": result["configured"],
        "reason": result["reason"],
        "implemented": True,
    }


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------
class AnalyticsWorker:
    """One process, one role, one loop.

    Owns its own `Session` (the queue's lease methods commit, so they assume
    they own the session) and its own `worker_id`.
    """

    def __init__(
        self,
        config: WorkerConfig,
        *,
        worker_id: str | None = None,
        shutdown: GracefulShutdown | None = None,
    ) -> None:
        self.config = config
        self.worker_id = worker_id or worker_identity()
        self.shutdown = shutdown or GracefulShutdown()
        self._last_schedule_at: float | None = None
        self._last_prune_at: float | None = None

    # -- lifecycle ---------------------------------------------------------
    def run_forever(self) -> int:
        """Tick until a stop is requested. Always returns 0 on a clean exit."""
        log.info(
            "analytics-worker starting: worker_id=%s %s",
            self.worker_id,
            self.config.describe(),
        )
        try:
            while not self.shutdown.requested:
                self.tick()
                if self.config.once:
                    log.info("ANALYTICS_WORKER_ONCE set — one tick done, stopping")
                    break
                self.shutdown.sleep(self.config.interval_sec)
        finally:
            # Runs on the normal path, on SIGTERM, and on an unexpected raise.
            # Releasing here rather than only on the happy path is the point:
            # the rows most worth releasing are the ones held when something
            # went wrong.
            self._release_leases()
        log.info("analytics-worker stopped cleanly: worker_id=%s", self.worker_id)
        return EXIT_OK

    def tick(self) -> dict[str, Any]:
        """One iteration. Never raises — a bad bucket must not end the process."""
        if self.config.role == ROLE_DELIVERY:
            return self._delivery_tick()
        return self._rollup_tick()

    # -- rollup ------------------------------------------------------------
    def _rollup_tick(self) -> dict[str, Any]:
        outcome: dict[str, Any] = {}
        with SessionLocal() as db:
            runner = AggregationRunner(db, worker_id=self.worker_id)
            queue = RecomputeQueue(db)

            # 1. Reclaim first, so this tick can pick up whatever a dead worker
            #    left behind instead of waiting a full cycle for the next one.
            outcome["reclaimed"] = self._guard(
                "reclaim", lambda: queue.reclaim_expired(), default=0
            )

            # 2. Drain. Per-bucket failures are reported to `queue.fail()` by the
            #    runner, which owns retry accounting and dead-lettering.
            if not self.shutdown.requested:
                outcome["drain"] = self._guard(
                    "drain",
                    lambda: runner.drain_queue(
                        limit=self.config.batch_size,
                        budget_ms=self.config.drain_budget_ms,
                    ),
                    default={},
                )
            else:
                # Stop claiming new work the moment a shutdown is requested;
                # anything already claimed above is released in `run_forever`.
                outcome["drain"] = {"skipped": "shutdown_requested"}

            # 3. The trailing scheduled window. Today's bucket is dirty by
            #    definition (it is still accruing) and yesterday's may have
            #    settled late, so neither is reliably in the queue.
            if not self.shutdown.requested and self._due(
                self._last_schedule_at, self.config.schedule_interval_sec
            ):
                outcome["scheduled"] = self._guard(
                    "scheduled", lambda: self._run_scheduled(db, runner), default={}
                )
                self._last_schedule_at = self._now()

            # 4. Prune. `failed` rows are never pruned — they are what alerting
            #    reads, and deleting them would silently close the only report
            #    of a bucket that will never rebuild itself.
            if self._due(self._last_prune_at, self.config.prune_interval_sec):
                outcome["pruned"] = self._guard(
                    "prune", lambda: self._prune(db), default=0
                )
                self._last_prune_at = self._now()

        log.info("rollup tick: %s", outcome)
        return outcome

    def _run_scheduled(self, db: Session, runner: AggregationRunner) -> dict[str, Any]:
        """Recompute the trailing window for every registered job.

        Run directly rather than enqueued: these buckets are due on a clock, not
        dirty because something changed, and pushing them through the dirty-bucket
        queue would make "what do we believe is stale?" — the one question that
        table answers — unreadable under a constant drip of routine work.

        The window is in **store-local** reporting days from `timebox`, never
        `date.today()`: containers run UTC, and with `Asia/Kolkata` a UTC-derived
        "today" is the wrong bucket for five and a half hours of every day.
        """
        if not JOBS:
            return {"skipped": "no jobs registered"}
        today = local_day(datetime.now(timezone.utc), store_timezone(db))
        window_from = today - timedelta(days=max(self.config.schedule_lookback_days - 1, 0))
        summary = runner.run_window(
            sorted(JOBS),
            window_from,
            # Half-open [from, to), so the exclusive end must be TOMORROW for
            # today's bucket to be aggregated at all. Passing `today` here would
            # silently stop rebuilding the current day and quietly shorten the
            # lookback by one — with no error, no failed run, and a dashboard
            # that just never catches up to the present.
            today + timedelta(days=1),
            budget_ms=self.config.drain_budget_ms,
            trigger=SyncTrigger.CRON,
        )
        return {
            "status": summary["status"],
            "window": f"{window_from}..{today}",
            "days_processed": summary["days_processed"],
            "rows_written": summary["rows_written"],
            "budget_exhausted": summary["budget_exhausted"],
        }

    def _prune(self, db: Session) -> int:
        """Delete `done` queue rows past the retention window, in one bounded batch.

        `LIMIT` rather than a single sweeping DELETE: an unbounded delete over a
        year of completed rows holds a table lock for its whole duration, and
        every worker trying to claim work waits behind it. A capped batch per
        tick converges just as well and never blocks a claim for long.
        """
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            days=self.config.retention_days
        )
        result = db.execute(
            text(
                "DELETE FROM analytics_recompute_queue "
                "WHERE status = :status AND processed_at IS NOT NULL "
                "AND processed_at < :cutoff LIMIT :limit"
            ),
            {
                "status": RecomputeStatus.DONE,
                "cutoff": cutoff,
                "limit": self.config.prune_batch,
            },
        )
        db.commit()
        return int(result.rowcount or 0)

    # -- delivery ----------------------------------------------------------
    def _delivery_tick(self) -> dict[str, Any]:
        with SessionLocal() as db:
            outcome = self._guard(
                "deliver",
                lambda: deliver_pending(db, limit=self.config.batch_size),
                default={},
            )
        log.info("delivery tick: %s", outcome)
        return {"delivery": outcome}

    # -- internals ---------------------------------------------------------
    def _guard(self, phase: str, fn, *, default):
        """Run one phase; log and swallow anything it raises.

        The loop's survival is the contract. A phase that fails this tick is
        retried next tick, and the queue's own retry/dead-letter accounting
        makes a genuinely broken bucket visible without the process needing to
        die to report it.
        """
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - the loop must outlive any phase
            log.exception("analytics-worker phase %s failed: %s", phase, exc)
            return default

    def _release_leases(self) -> int:
        """Hand back everything this worker still holds, on the way out.

        The lease already guarantees these rows come back — this just makes it
        immediate. Without it a clean `docker compose down` leaves the next
        worker staring at rows claimed by a container that no longer exists
        until `ANALYTICS_WORKER_LEASE_SEC` elapses, which looks exactly like a
        stall.

        Filtered on `claimed_by = <me>`: releasing another worker's live claim
        would hand a bucket to two processes at once.
        """
        try:
            with SessionLocal() as db:
                result = db.execute(
                    text(
                        "UPDATE analytics_recompute_queue "
                        "SET status = :pending, claim_expires_at = NULL "
                        "WHERE status = :claimed AND claimed_by = :worker_id"
                    ),
                    {
                        "pending": RecomputeStatus.PENDING,
                        "claimed": RecomputeStatus.CLAIMED,
                        "worker_id": self.worker_id,
                    },
                )
                db.commit()
                released = int(result.rowcount or 0)
            if released:
                log.info("released %d claimed bucket(s) on shutdown", released)
            return released
        except Exception as exc:  # noqa: BLE001 - shutdown must still exit 0
            log.exception("could not release leases on shutdown: %s", exc)
            return 0

    @staticmethod
    def _now() -> float:
        """Monotonic, not wall clock: an NTP step must not make the schedule
        interval fire twice or skip an hour."""
        return time.monotonic()

    @classmethod
    def _due(cls, last_at: float | None, interval_sec: int) -> bool:
        return last_at is None or (cls._now() - last_at) >= interval_sec


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def _configure_logging() -> None:
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=os.environ.get("ANALYTICS_WORKER_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )


def main() -> int:
    """Process entrypoint. Returns the exit code; never raises on a normal path.

    **Refuses to start with `ANALYTICS_ROLLUPS_ENABLED` false, and exits 0.**
    Not `sys.exit(1)`, not an exception: under `restart: always` a non-zero exit
    produces a crash loop that fills the logs and the restart-count metric with a
    condition that is not an error — the flag is off deliberately, and turning it
    off is the documented rollback. Exit 0 stops cleanly and stays stopped.
    """
    _configure_logging()

    if not settings.ANALYTICS_ROLLUPS_ENABLED:
        log.warning(
            "analytics-worker: ANALYTICS_ROLLUPS_ENABLED is false — refusing to "
            "start. Set ANALYTICS_ROLLUPS_ENABLED=true to enable the analytics "
            "rollup pipeline. Exiting 0 so `restart: always` does not crash-loop."
        )
        return EXIT_OK

    role = os.environ.get("ANALYTICS_WORKER_ROLE", ROLE_ROLLUP).strip().lower()
    if role not in ROLES:
        # A non-zero exit here IS the right signal: a typo'd role means this
        # container is doing nothing anyone asked for, and unlike the flag above
        # that is a mistake, not a decision. Let it be loud.
        log.error(
            "analytics-worker: ANALYTICS_WORKER_ROLE=%r is not one of %s",
            role,
            list(ROLES),
        )
        return EXIT_BAD_CONFIG

    shutdown = GracefulShutdown()
    shutdown.install()
    worker = AnalyticsWorker(WorkerConfig(role), shutdown=shutdown)
    return worker.run_forever()


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    sys.exit(main())
