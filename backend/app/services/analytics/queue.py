"""Dirty-bucket recompute queue — the set of stale buckets, made explicit.

Why this exists instead of a lookback window
--------------------------------------------
The obvious way to keep rollups fresh is a fixed window: "every night,
recompute the last seven days". One cron line, no bookkeeping, and wrong in a
way that never raises an error.

Everything that mutates an already-closed bucket in this business arrives on
its own schedule, and none of those schedules fit inside N days. A refund
issued in July restates March. A return completes weeks after delivery. A
chargeback lands months later. A courier scan flips a shipment to RTO long
after dispatch. A gateway settlement correction restates net receipts for a
batch of old orders. A cost-rule edit deliberately restates an arbitrarily
deep range. Pick N = 7 and every one of those silently vanishes: the fact row
updates, the rollup is never rebuilt, and the dashboard serves a stale
aggregate forever — no failed run, no alert, just a number that quietly stopped
being true. Pick N large enough to be safe and the nightly job recomputes years
of untouched buckets every night, so the window grows until someone shrinks it
again for cost reasons and reintroduces the bug.

A queue inverts the default. The writer that caused a change enqueues the
bucket it invalidated, whenever that is, and the queue is the explicit,
inspectable, countable answer to "what do we currently believe is stale?".
Work is bounded by what actually changed rather than by a guess about how late
change arrives.

The residual risk, stated plainly
---------------------------------
This trades a *systematic* error for a *distributed* one. A lookback window is
uniformly wrong; a queue is exactly right for every mutation path that
remembers to call `enqueue`, and silently, permanently wrong for any path that
forgets. A new refund code path, a bulk SQL correction run by hand, an
importer, a migration that back-dates orders — each is one missing `enqueue`
away from a rollup that is stale forever, with a green pipeline, no failed run,
and no alert, because nothing in this module can observe a bucket that was
never enqueued. The queue's own instrumentation (`depth`, dead-letter alerts)
covers work it was *told* about and nothing else.

Two things reduce that exposure and neither eliminates it: enqueue from the
lowest-level write path rather than the caller (so one call site covers every
route into it), and keep a slow, cheap reconciliation sweep that re-derives a
handful of old buckets from facts and enqueues any that disagree — a smoke
detector for enqueues that were never made. Treat "which writers enqueue?" as a
standing review question, not a solved problem.

Transaction ownership
---------------------
`enqueue` / `enqueue_many` **flush but never commit**. They are meant to be
called from inside the transaction that made the bucket dirty, so that if the
refund rolls back the enqueue rolls back with it — an enqueue for a change that
never happened is a wasted recompute, and worse, a commit forced from inside
this module would durably commit half of the caller's unfinished work. (The one
exception is outside this module's control: on a virgin database the very first
enqueue resolves the tz generation, and `timebox.active_generation` commits the
generation row it seeds.)

Every other method (`claim`, `heartbeat`, `complete`, `fail`,
`reclaim_expired`) **does commit**, because a lease is worthless unless it is
visible to other workers immediately. Those methods therefore assume they own
their `Session` — give a worker loop its own.

Ownership invariant
-------------------
`status` is the authority on who may act on a row; `claimed_by` is a record of
who last held it and is deliberately *not* cleared when a lease lapses, so a
dead worker is still identifiable after the fact. Every mutation a worker makes
to a row it believes it holds is filtered on
``status == 'claimed' AND claimed_by == <me>``, which is what stops a worker
whose lease expired mid-run from completing, failing or extending work that
another worker has already redone.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, func, select, text, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.models.analytics_base import DIMENSION_UNKNOWN
from app.models.analytics_control import (
    AlertRuleKey,
    AlertSeverity,
    AlertStatus,
    AnalyticsAlert,
    AnalyticsRecomputeQueue,
    RecomputeStatus,
)
from app.services.analytics.timebox import active_generation

__all__ = [
    "FailOutcome",
    "RecomputeQueue",
    "DEFAULT_PRIORITY",
    "DEFAULT_LEASE_SECONDS",
    "ALERT_METRIC",
]

#: Routine dirt. Interactive/manual requests go lower, deep backfills higher so
#: they can never starve live buckets.
DEFAULT_PRIORITY = 5

#: How long a claim is believed without a heartbeat. Long enough for a slow
#: bucket, short enough that a killed worker's rows come back the same shift.
DEFAULT_LEASE_SECONDS = 300

#: Width of `analytics_recompute_queue.last_error`. A driver-side truncation
#: error would lose the error *and* the retry accounting, so truncate here.
ERROR_MAX_CHARS = 500

#: `analytics_alerts.metric` for a dead-lettered bucket. Constant so the
#: Control Centre can filter the stream without matching free text.
ALERT_METRIC = "recompute_queue"

#: `analytics_alerts.dimension` for the same. The dimension *value* is the job.
ALERT_DIMENSION = "job"

#: The index `claim` must be planned against, discovered from the model rather
#: than hardcoded so renaming it in a migration cannot silently break the plan
#: (and dropping it degrades to the optimiser's choice rather than erroring).
#: See `_lock_batch` for why the plan is a correctness concern, not a tuning one.
CLAIM_INDEX: str | None = next(
    (
        ix.name
        for ix in AnalyticsRecomputeQueue.__table__.indexes
        if [c.name for c in ix.columns] == ["status", "priority", "bucket_date"]
    ),
    None,
)


class FailOutcome:
    """What `RecomputeQueue.fail` actually did, so the caller can log it."""

    #: Attempts remain; the row went back to `pending` and will be retried.
    RETRY = "retry"
    #: Attempts exhausted; row is `failed` and an alert was raised.
    DEAD_LETTER = "dead_letter"
    #: The caller no longer holds the row (lease lapsed, someone else redid
    #: it). Nothing was written — reporting a failure against work another
    #: worker has since completed would resurrect a finished bucket.
    IGNORED = "ignored"


def _utcnow() -> datetime:
    """Naive UTC — what MySQL DATETIME columns actually store.

    `DateTime(timezone=True)` is a no-op on MySQL: the column is a plain
    DATETIME carrying no offset, so an aware datetime written to it reads back
    naive and a later aware-vs-naive comparison in Python raises TypeError.
    Every lease timestamp this module writes and compares is therefore naive
    UTC, and the one column left on the DB clock (`enqueued_at`, defaulted by
    `NOW()`) is only ever compared against `NOW()` — never against Python.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class RecomputeQueue:
    """Read/write access to `analytics_recompute_queue`.

    Producers call `enqueue` / `enqueue_many` inside their own transaction.
    Workers call `claim` -> (`heartbeat`)* -> `complete` | `fail`. A sweeper
    calls `reclaim_expired`; the admin health panel calls `depth`.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # -- producers ---------------------------------------------------------
    def enqueue(
        self,
        job: str,
        bucket_date: date,
        *,
        reason: str,
        dimension_key: str = DIMENSION_UNKNOWN,
        priority: int = DEFAULT_PRIORITY,
        tz_generation: int | None = None,
    ) -> bool:
        """Mark one bucket dirty. Idempotent, and reopens completed work.

        Writers must not check-then-insert: two refunds against the same bucket
        race and either duplicate the row or lose one. This is a single
        `INSERT ... ON DUPLICATE KEY UPDATE` against the UNIQUE
        `(job, bucket_date, dimension_key, tz_generation)` key, so fifty
        refunds collapse into one queued recompute.

        A row already `done` is **reopened** — which is exactly what a late
        refund needs, and what `INSERT IGNORE` would get wrong by discarding
        the new dirt. Lease and outcome columns are cleared with it: a reopened
        row is new work, not a half-finished old attempt.

        `priority = LEAST(priority, VALUES(priority))` means an urgent enqueue
        can promote an already-queued row, and a routine one can never demote
        an urgent row that is waiting behind it.

        `tz_generation` defaults to the active generation. Never pass a literal
        — the only caller that legitimately names one is a timezone rebuild,
        which enqueues the same dates under the *new* generation and relies on
        that column to avoid colliding with the live queue.

        Returns True when the call changed the queue (a new row, or an existing
        row reopened / promoted). False means MySQL reported zero affected
        rows, i.e. an identical `pending` row already existed — the bucket is
        queued either way, so False is not a failure. Does not commit.
        """
        generation = (
            tz_generation
            if tz_generation is not None
            else active_generation(self.db).generation
        )
        result = self.db.execute(
            self._upsert(
                [
                    self._row_values(
                        job=job,
                        bucket_date=bucket_date,
                        reason=reason,
                        dimension_key=dimension_key,
                        priority=priority,
                        tz_generation=generation,
                    )
                ]
            )
        )
        self.db.flush()
        return bool(result.rowcount)

    def enqueue_many(
        self,
        items: Iterable[tuple[str, date]],
        *,
        reason: str,
        priority: int = DEFAULT_PRIORITY,
    ) -> int:
        """Enqueue `(job, bucket_date)` pairs in one statement.

        For the fan-out cases — a cost-rule edit covering a quarter, a
        backfill, a timezone rebuild — where one row per bucket per job would
        otherwise be thousands of round trips.

        Returns the number of distinct buckets covered. Duplicates within
        `items` are collapsed in Python first, both to keep the statement
        small and because MySQL's affected-rows for a multi-row upsert counts
        1 per insert and 2 per update, so it cannot be read as a row count.
        Does not commit.
        """
        deduped: dict[tuple[str, date], None] = {}
        for job, bucket_date in items:
            deduped[(job, bucket_date)] = None
        if not deduped:
            return 0

        generation = active_generation(self.db).generation
        self.db.execute(
            self._upsert(
                [
                    self._row_values(
                        job=job,
                        bucket_date=bucket_date,
                        reason=reason,
                        dimension_key=DIMENSION_UNKNOWN,
                        priority=priority,
                        tz_generation=generation,
                    )
                    for job, bucket_date in deduped
                ]
            )
        )
        self.db.flush()
        return len(deduped)

    # -- workers -----------------------------------------------------------
    def claim(
        self,
        *,
        worker_id: str,
        limit: int = 20,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        jobs: list[str] | None = None,
    ) -> list[AnalyticsRecomputeQueue]:
        """Atomically take up to `limit` buckets, in queue order.

        One transaction: lock a batch `FOR UPDATE SKIP LOCKED`, then stamp the
        winners `claimed`. `SKIP LOCKED` is what makes horizontal scaling work
        — without it a second worker running the same query blocks on the first
        worker's row locks and the fleet serialises; with it, it steps over
        them and takes the next rows instead. Two workers get disjoint batches
        rather than one worker and one queue.

        Fresh `pending` work is taken first, and only if the batch is not full
        does it fall back to rows whose lease has expired. That is two queries
        rather than one `OR`, for a reason that is not stylistic — see
        `_lock_batch`. It also means recovering a dead worker's buckets can
        never delay live dirt; the sweeper (`reclaim_expired`) folds expired
        rows back into `pending`, where they compete on priority normally.

        Lease-expired rows are still claimable here directly, so a dead
        worker's buckets recover on the next claim even if the sweeper never
        runs at all.

        Returned rows are in the order they should be processed. Commits.
        """
        self._use_read_committed()
        now = _utcnow()
        rows = self._lock_batch(
            AnalyticsRecomputeQueue.status == RecomputeStatus.PENDING,
            limit=limit,
            jobs=jobs,
        )
        if len(rows) < limit:
            # Phase A has not written anything yet, so a row it locked is still
            # `pending` and cannot match this predicate — the two batches are
            # disjoint by construction.
            rows.extend(
                self._lock_batch(
                    and_(
                        AnalyticsRecomputeQueue.status == RecomputeStatus.CLAIMED,
                        AnalyticsRecomputeQueue.claim_expires_at.is_not(None),
                        AnalyticsRecomputeQueue.claim_expires_at < now,
                    ),
                    limit=limit - len(rows),
                    jobs=jobs,
                )
            )
        if not rows:
            # Nothing matched, but the SELECT opened a transaction; end it so
            # the connection is not left idle-in-transaction holding a snapshot.
            self.db.commit()
            return []

        rows.sort(key=lambda r: (r.priority, r.bucket_date, r.id))
        expires_at = now + timedelta(seconds=lease_seconds)
        for row in rows:
            row.status = RecomputeStatus.CLAIMED
            row.claimed_by = worker_id
            row.claimed_at = now
            row.claim_expires_at = expires_at
            row.heartbeat_at = now
        self.db.commit()
        return rows

    def heartbeat(
        self,
        ids: list[int],
        *,
        worker_id: str,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> int:
        """Push the lease out on rows this worker still holds.

        A long but healthy recompute must be able to outlive its lease without
        being reclaimed from underneath itself. The `claimed_by` + `claimed`
        filter is the safety: once a row has been reclaimed and handed to
        someone else, a late heartbeat from the previous holder must not
        resurrect its claim, or two workers would believe they own the same
        bucket.

        Returns the number of rows whose lease actually moved. Commits.
        """
        if not ids:
            return 0
        now = _utcnow()
        result = self.db.execute(
            update(AnalyticsRecomputeQueue)
            .where(
                AnalyticsRecomputeQueue.id.in_(ids),
                AnalyticsRecomputeQueue.status == RecomputeStatus.CLAIMED,
                AnalyticsRecomputeQueue.claimed_by == worker_id,
            )
            .values(
                claim_expires_at=now + timedelta(seconds=lease_seconds),
                heartbeat_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        self.db.commit()
        return int(result.rowcount or 0)

    def complete(self, ids: list[int], *, worker_id: str) -> int:
        """Mark rows done — but only the ones this worker still owns.

        The ownership filter matters more than it looks. A worker that stalled
        past its lease may finish and report success long after another worker
        reclaimed the row and started redoing it; without the filter it would
        mark the bucket `done` while a recompute is still in flight, and the
        row would go quiet in whatever state that second run left it.

        Returns the number of rows completed. Commits.
        """
        if not ids:
            return 0
        result = self.db.execute(
            update(AnalyticsRecomputeQueue)
            .where(
                AnalyticsRecomputeQueue.id.in_(ids),
                AnalyticsRecomputeQueue.status == RecomputeStatus.CLAIMED,
                AnalyticsRecomputeQueue.claimed_by == worker_id,
            )
            .values(
                status=RecomputeStatus.DONE,
                processed_at=_utcnow(),
                claim_expires_at=None,
                last_error=None,
            )
            .execution_options(synchronize_session=False)
        )
        self.db.commit()
        return int(result.rowcount or 0)

    def fail(
        self,
        id_: int,
        *,
        worker_id: str,
        error: str,
        max_attempts: int = 5,
    ) -> str:
        """Record a failed attempt: retry, or dead-letter with an alert.

        Below `max_attempts` the row goes back to `pending` and is retried —
        most recompute failures are a lock timeout, a deploy, or a transient
        connection drop.

        At `max_attempts` retrying is no longer information: the bucket goes
        `failed` so it stops burning worker time, **and an `AnalyticsAlert`
        row is written**. That second half is the point. A permanently
        unbuildable bucket is invisible in every other surface — the rollup
        table still has the old row, the chart still draws, the pipeline still
        reports success — so without an alert the failure mode is a number that
        is confidently, permanently wrong. The alert is what turns it into
        something an operator sees in the Control Centre.

        Returns a `FailOutcome`. `IGNORED` means the caller no longer holds the
        row and nothing was written. Commits.
        """
        row = self.db.execute(
            select(AnalyticsRecomputeQueue)
            .where(AnalyticsRecomputeQueue.id == id_)
            .with_for_update()
        ).scalars().first()

        if (
            row is None
            or row.status != RecomputeStatus.CLAIMED
            or row.claimed_by != worker_id
        ):
            self.db.commit()
            return FailOutcome.IGNORED

        now = _utcnow()
        row.attempts = int(row.attempts or 0) + 1
        row.last_error = (error or "")[:ERROR_MAX_CHARS]
        row.heartbeat_at = now
        row.claim_expires_at = None

        if row.attempts >= max_attempts:
            row.status = RecomputeStatus.FAILED
            row.processed_at = now
            self.db.add(self._dead_letter_alert(row, worker_id=worker_id, at=now))
            outcome = FailOutcome.DEAD_LETTER
        else:
            # Back to pending. `claimed_by` is left as the forensic record of
            # who last attempted it; `status` is what gates further writes.
            row.status = RecomputeStatus.PENDING
            outcome = FailOutcome.RETRY

        self.db.commit()
        return outcome

    def reclaim_expired(self) -> int:
        """Return rows whose lease lapsed to `pending`.

        A worker that is OOM-killed mid-batch leaves its rows `claimed` by a
        process that no longer exists. Without a lease those buckets are stuck
        forever with no error and no retry — they simply stop updating. With
        one, the claim expires and the work comes back.

        `attempts` is deliberately **not** incremented: the row did not fail,
        the worker died, and charging a retry against the bucket would
        dead-letter a perfectly good bucket after five unrelated deploys.
        Recompute is idempotent (DELETE + INSERT of the bucket), so a duplicate
        execution after a falsely-expired lease costs time and nothing else —
        which is why an expiring lease is safe here and would not be for, say,
        moving money.

        Returns how many rows were released. Commits.
        """
        self._use_read_committed()
        result = self.db.execute(
            update(AnalyticsRecomputeQueue)
            .where(
                AnalyticsRecomputeQueue.status == RecomputeStatus.CLAIMED,
                AnalyticsRecomputeQueue.claim_expires_at.is_not(None),
                AnalyticsRecomputeQueue.claim_expires_at < _utcnow(),
            )
            .values(status=RecomputeStatus.PENDING, claim_expires_at=None)
            .execution_options(synchronize_session=False)
        )
        self.db.commit()
        return int(result.rowcount or 0)

    # -- observability -----------------------------------------------------
    def depth(self) -> dict[str, int]:
        """Queue depth by status, plus how long the oldest dirt has waited.

        Counts alone cannot distinguish a healthy busy queue from a stalled
        one — 400 pending rows are fine if they drain in a minute and an
        outage if the oldest has been waiting since Tuesday. So the age of the
        oldest pending row ships alongside the counts, and it is computed
        entirely on the DB clock (`TIMESTAMPDIFF(SECOND, MIN(enqueued_at),
        NOW())`) rather than against Python's, because `enqueued_at` is written
        by `NOW()` and comparing the two clocks would report drift as backlog.

        Keys: one per `RecomputeStatus` (always present, zero when empty),
        `total`, and `oldest_pending_age_seconds` (0 when nothing is pending).
        """
        counts: dict[str, int] = {
            RecomputeStatus.PENDING: 0,
            RecomputeStatus.CLAIMED: 0,
            RecomputeStatus.DONE: 0,
            RecomputeStatus.FAILED: 0,
        }
        total = 0
        rows = self.db.execute(
            select(AnalyticsRecomputeQueue.status, func.count(AnalyticsRecomputeQueue.id))
            .group_by(AnalyticsRecomputeQueue.status)
        ).all()
        for status, n in rows:
            counts[status] = int(n)
            total += int(n)
        counts["total"] = total

        oldest = self.db.execute(
            select(
                func.coalesce(
                    func.timestampdiff(
                        text("SECOND"),
                        func.min(AnalyticsRecomputeQueue.enqueued_at),
                        func.now(),
                    ),
                    0,
                )
            ).where(AnalyticsRecomputeQueue.status == RecomputeStatus.PENDING)
        ).scalar()
        counts["oldest_pending_age_seconds"] = max(int(oldest or 0), 0)
        return counts

    # -- internals ---------------------------------------------------------
    def _use_read_committed(self) -> None:
        """Run this transaction without gap locks.

        `SKIP LOCKED` fixes the SELECT half of a claim; it does nothing for the
        UPDATE half. Under MySQL's default REPEATABLE READ, a locking read over
        an index range also takes *next-key* locks — the records **plus the
        gaps around them** — and moving a row from `pending` to `claimed`
        rewrites its entry in that same index. So a second worker cleanly skips
        the first worker's rows, selects its own disjoint batch, and then
        blocks anyway on an insert-intention conflict with gaps it never
        touched. Measured on this table: with two overlapping claims the second
        worker's UPDATE waits out `innodb_lock_wait_timeout` and dies; under
        READ COMMITTED, which takes no gap locks, it returns immediately.

        The weaker isolation costs nothing here. A claim reads each row exactly
        once and needs no repeatable read; the row-level `status` + `claimed_by`
        filters, not the snapshot, are what make the handoff safe.

        Set per transaction, and SQLAlchemy restores the level when the
        connection goes back to the pool, so this cannot leak into unrelated
        work. Skipped if the caller handed us a session with a transaction
        already open — ending it here could commit or discard their work, and a
        contended claim is much better than that.
        """
        if self.db.in_transaction():
            return
        self.db.connection(execution_options={"isolation_level": "READ COMMITTED"})

    def _lock_batch(
        self, predicate, *, limit: int, jobs: list[str] | None
    ) -> list[AnalyticsRecomputeQueue]:
        """Lock up to `limit` rows matching `predicate`, skipping locked ones.

        The query plan here is a correctness concern, not a tuning one, and it
        is the reason `claim` runs two narrow queries instead of one `OR`.

        `SKIP LOCKED` skips rows *as the scan reads them*, so a worker's
        effective batch is "everything the scan touched that nobody else held".
        If the plan needs a filesort, MySQL must read — and lock — every
        matching row before `ORDER BY ... LIMIT` can pick ten of them. One
        worker then holds locks on the entire pending queue for the duration of
        its claim, the next worker skips all of it and comes back empty, and
        the fleet silently collapses to one effective worker. It looks like a
        healthy claim returning zero rows.

        Two things keep the plan on `(status, priority, bucket_date)`, where
        the index supplies the ordering and the scan can stop at `LIMIT`:

        * one equality on `status` per query, never an `OR` across two values
          (`OR` defeats the ordered range and reintroduces the filesort);
        * `FORCE INDEX`, because adding `job IN (...)` tips the optimiser into
          an index-merge with the unique key plus a filesort — measurably, on
          this table — and that puts the lock footprint back where it started.
        """
        if limit <= 0:
            return []
        query = select(AnalyticsRecomputeQueue).where(predicate)
        if jobs:
            query = query.where(AnalyticsRecomputeQueue.job.in_(jobs))
        if CLAIM_INDEX:
            query = query.with_hint(
                AnalyticsRecomputeQueue, f"FORCE INDEX ({CLAIM_INDEX})", "mysql"
            )
        query = (
            query.order_by(
                AnalyticsRecomputeQueue.priority.asc(),
                AnalyticsRecomputeQueue.bucket_date.asc(),
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(self.db.execute(query).scalars().all())

    @staticmethod
    def _row_values(
        *,
        job: str,
        bucket_date: date,
        reason: str,
        dimension_key: str,
        priority: int,
        tz_generation: int,
    ) -> dict:
        return {
            "job": job,
            "bucket_date": bucket_date,
            # '-' rather than NULL: this column is inside the UNIQUE key, and
            # MySQL allows unlimited NULLs under a UNIQUE index, so a NULL here
            # would quietly switch the dedup off.
            "dimension_key": dimension_key or DIMENSION_UNKNOWN,
            "tz_generation": tz_generation,
            "reason": reason,
            "priority": priority,
            "status": RecomputeStatus.PENDING,
            "attempts": 0,
            "enqueued_at": func.now(),
        }

    @staticmethod
    def _upsert(rows: list[dict]):
        """`INSERT ... ON DUPLICATE KEY UPDATE` over the dedup key."""
        stmt = mysql_insert(AnalyticsRecomputeQueue).values(rows)
        return stmt.on_duplicate_key_update(
            status=RecomputeStatus.PENDING,
            # Fresh dirt deserves a fresh budget of retries, even on a bucket
            # that previously exhausted them for an unrelated reason.
            attempts=0,
            priority=func.least(
                AnalyticsRecomputeQueue.__table__.c.priority, stmt.inserted.priority
            ),
            reason=stmt.inserted.reason,
            enqueued_at=func.now(),
            # A reopened row is new work; leaving the previous attempt's lease
            # or outcome on it would make a pending row look claimed or done.
            last_error=None,
            claimed_at=None,
            claimed_by=None,
            claim_expires_at=None,
            heartbeat_at=None,
            processed_at=None,
        )

    @staticmethod
    def _dead_letter_alert(
        row: AnalyticsRecomputeQueue, *, worker_id: str, at: datetime
    ) -> AnalyticsAlert:
        """The Control Centre row for a bucket that will never rebuild itself.

        `TRACKING_FAILURE` rather than a business rule: nothing is wrong with
        the store, the instrumentation is wrong. The dashboard is serving a
        stale rollup for this bucket as though it were current, which is the
        precise condition that rule exists to name. CRITICAL because a wrong
        number that looks right is acted on.
        """
        return AnalyticsAlert(
            rule_key=AlertRuleKey.TRACKING_FAILURE,
            severity=AlertSeverity.CRITICAL,
            metric=ALERT_METRIC,
            dimension=ALERT_DIMENSION,
            dimension_value=row.job[:64],
            bucket_date=row.bucket_date,
            detected_at=at,
            status=AlertStatus.OPEN,
            context={
                "queue_id": row.id,
                "job": row.job,
                "dimension_key": row.dimension_key,
                "tz_generation": row.tz_generation,
                "reason": row.reason,
                "attempts": row.attempts,
                "worker_id": worker_id,
                "last_error": row.last_error,
            },
        )
