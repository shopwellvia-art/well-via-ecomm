"""Regression tests for the dirty-bucket recompute queue.

What is actually under test is not "does a row get inserted" — it is the set of
properties that make a queue safe to point a fleet of workers at:

- dedup, so fifty refunds against one bucket cost one recompute;
- reopen, so a late refund can dirty a bucket that was already `done`;
- disjoint concurrent claims, so scaling out adds throughput rather than
  contention;
- leases, so a worker that dies does not park a bucket forever;
- ownership, so a worker whose lease lapsed cannot report on work another
  worker has already redone;
- dead-lettering with an alert, because a bucket that will never rebuild is
  otherwise completely invisible — the rollup still has its old row and the
  chart still draws.

Strategy mirrors test_sales_analytics_service.py / test_analytics_cost_rules.py:
- no shared db fixture; each test opens its own SessionLocal();
- every test-owned row is deleted in a finally block, through a fresh session
  so teardown never fails because of a half-rolled-back test transaction;
- every test uses a UNIQUE synthetic `job` name and filters `claim(jobs=[...])`
  on it, so one test can never claim another test's rows (or real queued work
  that happens to be sitting in the DB), and queue-wide counters are asserted
  as deltas against a baseline.
"""
from __future__ import annotations

import threading
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.analytics_control import (
    AlertRuleKey,
    AlertSeverity,
    AlertStatus,
    AnalyticsAlert,
    AnalyticsRecomputeQueue,
    RecomputeReason,
    RecomputeStatus,
)
from app.services.analytics.queue import ALERT_METRIC, FailOutcome, RecomputeQueue
from app.services.analytics.timebox import active_generation


# ---------------------------------------------------------------------------
# Helpers (local, not shared with other test modules)
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _job() -> str:
    """A job name no other test or real workload can collide with."""
    return f"qtest_{_uid()}"


def _day(offset: int = 0) -> date:
    return date(2026, 1, 1) + timedelta(days=offset)


def _cleanup(jobs: list[str]) -> None:
    """Delete every row these tests own, via a fresh session."""
    jobs = [j for j in jobs if j]
    if not jobs:
        return
    with SessionLocal() as s:
        s.execute(
            text("DELETE FROM analytics_recompute_queue WHERE job IN :jobs"),
            {"jobs": tuple(jobs)},
        )
        s.execute(
            text(
                "DELETE FROM analytics_alerts "
                "WHERE metric = :metric AND dimension_value IN :jobs"
            ),
            {"metric": ALERT_METRIC, "jobs": tuple(jobs)},
        )
        s.commit()


def _rows(db: Session, job: str) -> list[AnalyticsRecomputeQueue]:
    db.expire_all()
    return list(
        db.execute(
            select(AnalyticsRecomputeQueue)
            .where(AnalyticsRecomputeQueue.job == job)
            .order_by(AnalyticsRecomputeQueue.id)
        ).scalars().all()
    )


def _row(db: Session, id_: int) -> AnalyticsRecomputeQueue:
    """Re-read one row. Bulk UPDATEs bypass the identity map, and the session is
    configured with expire_on_commit=False, so a stale ORM object would happily
    assert the pre-update values."""
    db.expire_all()
    row = db.get(AnalyticsRecomputeQueue, id_)
    assert row is not None
    return row


def _alert_count(db: Session, job: str) -> int:
    db.expire_all()
    return int(
        db.execute(
            select(func.count(AnalyticsAlert.id)).where(
                AnalyticsAlert.metric == ALERT_METRIC,
                AnalyticsAlert.dimension_value == job,
            )
        ).scalar()
        or 0
    )


def _expire_lease(db: Session, id_: int) -> None:
    """Simulate a worker dying: push its lease into the past."""
    db.execute(
        update(AnalyticsRecomputeQueue)
        .where(AnalyticsRecomputeQueue.id == id_)
        .values(
            claim_expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(seconds=60)
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()


# ---------------------------------------------------------------------------
# Enqueue: dedup, reopen, priority
# ---------------------------------------------------------------------------

class TestEnqueue:

    def test_enqueue_twice_creates_one_row(self) -> None:
        """The UNIQUE key is the dedup. Fifty refunds against one March bucket
        must cost one recompute, not fifty."""
        job = _job()
        db = SessionLocal()
        try:
            q = RecomputeQueue(db)
            inserted = q.enqueue(job, _day(), reason=RecomputeReason.REFUND)
            q.enqueue(job, _day(), reason=RecomputeReason.REFUND)
            q.enqueue(job, _day(), reason=RecomputeReason.RETURN)
            db.commit()

            rows = _rows(db, job)
            assert inserted is True, "first enqueue should report a change"
            assert len(rows) == 1, f"expected 1 deduped row, got {len(rows)}"
            assert rows[0].status == RecomputeStatus.PENDING
            assert rows[0].dimension_key == "-", "sentinel, never NULL"
            assert rows[0].tz_generation == active_generation(db).generation
        finally:
            _cleanup([job])
            db.close()

    def test_distinct_dimension_keys_are_distinct_rows(self) -> None:
        """dimension_key is inside the UNIQUE key, so a per-product slice does
        not collide with the whole-day bucket."""
        job = _job()
        db = SessionLocal()
        try:
            q = RecomputeQueue(db)
            q.enqueue(job, _day(), reason=RecomputeReason.MANUAL)
            q.enqueue(job, _day(), reason=RecomputeReason.MANUAL, dimension_key="sku-1")
            q.enqueue(job, _day(), reason=RecomputeReason.MANUAL, dimension_key="sku-2")
            db.commit()

            keys = sorted(r.dimension_key for r in _rows(db, job))
            assert keys == ["-", "sku-1", "sku-2"], keys
        finally:
            _cleanup([job])
            db.close()

    def test_reenqueue_reopens_a_done_row(self) -> None:
        """A late refund must be able to dirty a bucket that already completed.
        INSERT IGNORE would discard the new dirt and the bucket would stay
        stale forever."""
        job = _job()
        db = SessionLocal()
        try:
            q = RecomputeQueue(db)
            q.enqueue(job, _day(), reason=RecomputeReason.ORDER_STATUS_CHANGE)
            db.commit()

            claimed = q.claim(worker_id="w1", jobs=[job])
            assert len(claimed) == 1
            id_ = claimed[0].id
            assert q.complete([id_], worker_id="w1") == 1
            assert _row(db, id_).status == RecomputeStatus.DONE

            # Two months later, a refund lands against the same bucket.
            reopened = q.enqueue(job, _day(), reason=RecomputeReason.REFUND)
            db.commit()

            row = _row(db, id_)
            assert reopened is True
            assert len(_rows(db, job)) == 1, "reopen must not create a second row"
            assert row.status == RecomputeStatus.PENDING
            assert row.attempts == 0
            assert row.reason == RecomputeReason.REFUND
            assert row.processed_at is None, "a reopened row is not processed work"
            assert row.claimed_by is None and row.claim_expires_at is None
        finally:
            _cleanup([job])
            db.close()

    def test_reenqueue_upgrades_priority_but_never_downgrades(self) -> None:
        """LEAST(): an urgent enqueue can promote a queued row, a routine one
        can never demote an urgent row waiting behind it."""
        job = _job()
        db = SessionLocal()
        try:
            q = RecomputeQueue(db)
            # Routine first, then urgent -> promoted.
            q.enqueue(job, _day(0), reason=RecomputeReason.BACKFILL, priority=5)
            q.enqueue(job, _day(0), reason=RecomputeReason.MANUAL, priority=1)
            # Urgent first, then routine -> stays urgent.
            q.enqueue(job, _day(1), reason=RecomputeReason.MANUAL, priority=1)
            q.enqueue(job, _day(1), reason=RecomputeReason.BACKFILL, priority=5)
            db.commit()

            by_date = {r.bucket_date: r.priority for r in _rows(db, job)}
            assert by_date[_day(0)] == 1, "5 then 1 must upgrade to 1"
            assert by_date[_day(1)] == 1, "1 then 5 must stay 1"
        finally:
            _cleanup([job])
            db.close()

    def test_enqueue_many_dedupes_and_counts_buckets(self) -> None:
        job_a, job_b = _job(), _job()
        db = SessionLocal()
        try:
            q = RecomputeQueue(db)
            n = q.enqueue_many(
                [
                    (job_a, _day(0)),
                    (job_a, _day(1)),
                    (job_a, _day(0)),  # duplicate within the batch
                    (job_b, _day(0)),
                ],
                reason=RecomputeReason.COST_RULE_CHANGE,
                priority=7,
            )
            db.commit()

            assert n == 3, f"expected 3 distinct buckets, got {n}"
            assert len(_rows(db, job_a)) == 2
            assert len(_rows(db, job_b)) == 1
            assert all(r.priority == 7 for r in _rows(db, job_a))
            assert q.enqueue_many([], reason=RecomputeReason.MANUAL) == 0
        finally:
            _cleanup([job_a, job_b])
            db.close()


# ---------------------------------------------------------------------------
# Claim: ordering, concurrency, leases
# ---------------------------------------------------------------------------

class TestClaim:

    def test_claim_orders_by_priority_then_bucket_date(self) -> None:
        """Lower priority first; within a priority, the oldest bucket first, so
        a backfill can never starve a live bucket."""
        job = _job()
        db = SessionLocal()
        try:
            q = RecomputeQueue(db)
            q.enqueue(job, _day(10), reason=RecomputeReason.REFUND, priority=5)
            q.enqueue(job, _day(20), reason=RecomputeReason.MANUAL, priority=1)
            q.enqueue(job, _day(5), reason=RecomputeReason.REFUND, priority=5)
            q.enqueue(job, _day(0), reason=RecomputeReason.BACKFILL, priority=9)
            db.commit()

            claimed = q.claim(worker_id="w1", limit=10, jobs=[job])
            order = [(r.priority, r.bucket_date) for r in claimed]
            assert order == [
                (1, _day(20)),
                (5, _day(5)),
                (5, _day(10)),
                (9, _day(0)),
            ], order
            assert all(r.status == RecomputeStatus.CLAIMED for r in claimed)
            assert all(r.claimed_by == "w1" for r in claimed)
            assert all(r.claim_expires_at is not None for r in claimed)
        finally:
            _cleanup([job])
            db.close()

    def test_two_concurrent_claimers_get_disjoint_batches(self) -> None:
        """SKIP LOCKED is what makes a worker fleet scale.

        Without it the second worker blocks on the first worker's row locks and
        the fleet serialises; with it, it steps over them and takes the next
        rows. Two workers racing must therefore split the queue, never overlap
        — an overlap means the same bucket is recomputed twice concurrently and
        the losing writer's DELETE+INSERT can land on top of the winner's.
        """
        job = _job()
        setup = SessionLocal()
        total = 20
        try:
            q = RecomputeQueue(setup)
            for i in range(total):
                q.enqueue(job, _day(i), reason=RecomputeReason.SHIPMENT_UPDATE)
            setup.commit()

            results: dict[str, list[int]] = {}
            errors: list[BaseException] = []
            barrier = threading.Barrier(2, timeout=30)

            def run(name: str) -> None:
                s = SessionLocal()
                try:
                    barrier.wait()
                    rows = RecomputeQueue(s).claim(
                        worker_id=name, limit=total // 2, jobs=[job]
                    )
                    results[name] = [r.id for r in rows]
                except BaseException as exc:  # noqa: BLE001 - surfaced below
                    errors.append(exc)
                finally:
                    s.close()

            threads = [
                threading.Thread(target=run, args=(f"worker-{i}",)) for i in (1, 2)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)

            assert not errors, f"claim raised concurrently: {errors!r}"
            a = results.get("worker-1", [])
            b = results.get("worker-2", [])
            assert set(a) & set(b) == set(), (
                f"workers claimed overlapping ids: {sorted(set(a) & set(b))}"
            )
            assert len(set(a) | set(b)) == total, (
                f"expected all {total} buckets claimed once, got {len(set(a) | set(b))}"
            )

            owners = {r.claimed_by for r in _rows(setup, job)}
            assert owners == {"worker-1", "worker-2"}, owners
        finally:
            _cleanup([job])
            setup.close()

    def test_claim_locks_only_the_rows_it_takes(self) -> None:
        """Deterministic guard for the lock footprint of the claim query.

        The threaded test above only catches a bad plan when the two claims
        genuinely overlap in time, which is a race. This one holds the locks
        open by hand, so it fails every time.

        It reaches for `_lock_batch` deliberately: the thing under test is the
        exact SELECT `claim` issues, because the *plan* is what decides how
        many rows get locked. If that query ever needs a filesort again, MySQL
        reads and locks every candidate before applying LIMIT, one worker ends
        up holding the whole pending queue, and every other worker's
        SKIP LOCKED claim comes back empty — a fleet that has silently
        serialised while still looking healthy.
        """
        job = _job()
        holder = SessionLocal()
        other = SessionLocal()
        try:
            q = RecomputeQueue(holder)
            for i in range(4):
                q.enqueue(job, _day(i), reason=RecomputeReason.REFUND)
            holder.commit()

            # Lock 2 of the 4 and keep the transaction open, standing in for a
            # concurrent worker mid-claim. READ COMMITTED first, exactly as
            # `claim` does: under REPEATABLE READ this holder would also take
            # gap locks and the other session's UPDATE would block on them
            # rather than on anything it actually selected.
            q._use_read_committed()
            held = q._lock_batch(
                AnalyticsRecomputeQueue.status == RecomputeStatus.PENDING,
                limit=2,
                jobs=[job],
            )
            assert len(held) == 2
            held_ids = {r.id for r in held}

            # If gap locking ever comes back, the other session's UPDATE waits
            # on a lock it can never get. Cap the wait so that regression fails
            # in two seconds with a lock-wait error instead of looking like a
            # hung suite for the connection's read timeout.
            other.execute(text("SET SESSION innodb_lock_wait_timeout = 2"))
            other.commit()

            # Must return promptly with the OTHER two: SKIP LOCKED steps over
            # the held rows instead of blocking on them, and the held query
            # locked only the two rows it returned.
            taken = RecomputeQueue(other).claim(worker_id="w2", limit=10, jobs=[job])
            taken_ids = {r.id for r in taken}

            assert taken_ids & held_ids == set(), "claimed a row another tx holds"
            assert len(taken_ids) == 2, (
                f"expected the 2 unheld rows, got {len(taken_ids)} — the claim "
                "query locked more rows than it returned"
            )
        finally:
            holder.rollback()
            _cleanup([job])
            holder.close()
            other.close()

    def test_claim_skips_rows_under_a_live_lease(self) -> None:
        job = _job()
        db = SessionLocal()
        try:
            q = RecomputeQueue(db)
            q.enqueue(job, _day(), reason=RecomputeReason.PAYMENT_SETTLEMENT)
            db.commit()

            first = q.claim(worker_id="w1", lease_seconds=600, jobs=[job])
            assert len(first) == 1

            second = q.claim(worker_id="w2", lease_seconds=600, jobs=[job])
            assert second == [], "a live lease must not be claimable by another worker"
            assert _row(db, first[0].id).claimed_by == "w1"
        finally:
            _cleanup([job])
            db.close()

    def test_expired_lease_is_reclaimed_and_claimable_again(self) -> None:
        """A killed worker must not park a bucket forever."""
        job = _job()
        db = SessionLocal()
        try:
            q = RecomputeQueue(db)
            q.enqueue(job, _day(), reason=RecomputeReason.RETURN)
            db.commit()

            claimed = q.claim(worker_id="dead-worker", lease_seconds=600, jobs=[job])
            id_ = claimed[0].id
            _expire_lease(db, id_)

            assert q.reclaim_expired() >= 1
            row = _row(db, id_)
            assert row.status == RecomputeStatus.PENDING
            assert row.claim_expires_at is None
            assert row.attempts == 0, "dying is not the row's fault"
            assert row.claimed_by == "dead-worker", (
                "the last holder is kept as forensics; status is what gates writes"
            )

            picked_up = q.claim(worker_id="live-worker", jobs=[job])
            assert [r.id for r in picked_up] == [id_]
            assert _row(db, id_).claimed_by == "live-worker"
        finally:
            _cleanup([job])
            db.close()

    def test_claim_takes_lease_expired_rows_without_a_sweeper(self) -> None:
        """reclaim_expired() is a convenience, not a dependency: if the sweeper
        never runs, the next claim must still recover the bucket."""
        job = _job()
        db = SessionLocal()
        try:
            q = RecomputeQueue(db)
            q.enqueue(job, _day(), reason=RecomputeReason.INVENTORY_CHANGE)
            db.commit()

            id_ = q.claim(worker_id="dead-worker", lease_seconds=600, jobs=[job])[0].id
            _expire_lease(db, id_)

            recovered = q.claim(worker_id="live-worker", jobs=[job])
            assert [r.id for r in recovered] == [id_]
            assert _row(db, id_).claimed_by == "live-worker"
        finally:
            _cleanup([job])
            db.close()


# ---------------------------------------------------------------------------
# Ownership: heartbeat / complete
# ---------------------------------------------------------------------------

class TestOwnership:

    def test_heartbeat_extends_only_for_the_owning_worker(self) -> None:
        job = _job()
        db = SessionLocal()
        try:
            q = RecomputeQueue(db)
            q.enqueue(job, _day(), reason=RecomputeReason.MANUAL)
            db.commit()

            id_ = q.claim(worker_id="w1", lease_seconds=60, jobs=[job])[0].id
            before = _row(db, id_).claim_expires_at

            assert q.heartbeat([id_], worker_id="w1", lease_seconds=900) == 1
            after = _row(db, id_).claim_expires_at
            assert after > before, f"lease did not extend: {before} -> {after}"

            # A stale heartbeat from a worker that no longer owns the row must
            # not resurrect its claim.
            assert q.heartbeat([id_], worker_id="intruder", lease_seconds=5) == 0
            assert _row(db, id_).claim_expires_at == after
            assert _row(db, id_).claimed_by == "w1"

            assert q.heartbeat([], worker_id="w1") == 0
        finally:
            _cleanup([job])
            db.close()

    def test_complete_from_the_wrong_worker_is_a_noop(self) -> None:
        """A worker whose lease lapsed mid-run must not be able to mark done
        work that another worker has already picked up and redone."""
        job = _job()
        db = SessionLocal()
        try:
            q = RecomputeQueue(db)
            q.enqueue(job, _day(), reason=RecomputeReason.REFUND)
            db.commit()

            id_ = q.claim(worker_id="w1", jobs=[job])[0].id

            assert q.complete([id_], worker_id="w2") == 0
            assert _row(db, id_).status == RecomputeStatus.CLAIMED

            assert q.complete([id_], worker_id="w1") == 1
            row = _row(db, id_)
            assert row.status == RecomputeStatus.DONE
            assert row.processed_at is not None
            assert row.claim_expires_at is None
        finally:
            _cleanup([job])
            db.close()

    def test_fail_from_the_wrong_worker_is_ignored(self) -> None:
        job = _job()
        db = SessionLocal()
        try:
            q = RecomputeQueue(db)
            q.enqueue(job, _day(), reason=RecomputeReason.REFUND)
            db.commit()

            id_ = q.claim(worker_id="w1", jobs=[job])[0].id
            outcome = q.fail(id_, worker_id="w2", error="not mine", max_attempts=5)

            row = _row(db, id_)
            assert outcome == FailOutcome.IGNORED
            assert row.status == RecomputeStatus.CLAIMED
            assert row.attempts == 0
            assert row.last_error is None
        finally:
            _cleanup([job])
            db.close()


# ---------------------------------------------------------------------------
# Retry and dead-lettering
# ---------------------------------------------------------------------------

class TestFail:

    def test_fail_below_threshold_returns_to_pending(self) -> None:
        job = _job()
        db = SessionLocal()
        try:
            q = RecomputeQueue(db)
            q.enqueue(job, _day(), reason=RecomputeReason.REFUND)
            db.commit()

            id_ = q.claim(worker_id="w1", jobs=[job])[0].id
            outcome = q.fail(id_, worker_id="w1", error="lock wait timeout", max_attempts=3)

            row = _row(db, id_)
            assert outcome == FailOutcome.RETRY
            assert row.status == RecomputeStatus.PENDING
            assert row.attempts == 1
            assert row.last_error == "lock wait timeout"
            assert row.claim_expires_at is None
            assert _alert_count(db, job) == 0, "a retryable failure must not alert"

            # And it really is retryable.
            again = q.claim(worker_id="w2", jobs=[job])
            assert [r.id for r in again] == [id_]
        finally:
            _cleanup([job])
            db.close()

    def test_fail_at_threshold_dead_letters_and_raises_one_alert(self) -> None:
        """A bucket that will never rebuild is invisible everywhere else — the
        rollup still holds its old row and the chart still draws — so the alert
        is the only thing that turns it into something an operator sees."""
        job = _job()
        db = SessionLocal()
        try:
            q = RecomputeQueue(db)
            q.enqueue(job, _day(), reason=RecomputeReason.COST_RULE_CHANGE)
            db.commit()

            id_ = q.claim(worker_id="w1", jobs=[job])[0].id
            assert q.fail(id_, worker_id="w1", error="boom 1", max_attempts=2) == (
                FailOutcome.RETRY
            )

            q.claim(worker_id="w1", jobs=[job])
            long_error = "x" * 900
            outcome = q.fail(id_, worker_id="w1", error=long_error, max_attempts=2)

            row = _row(db, id_)
            assert outcome == FailOutcome.DEAD_LETTER
            assert row.status == RecomputeStatus.FAILED
            assert row.attempts == 2
            assert row.processed_at is not None
            assert len(row.last_error) == 500, "last_error must fit the column"

            assert _alert_count(db, job) == 1, "exactly one alert per dead letter"
            alert = db.execute(
                select(AnalyticsAlert).where(
                    AnalyticsAlert.metric == ALERT_METRIC,
                    AnalyticsAlert.dimension_value == job,
                )
            ).scalars().one()
            assert alert.rule_key == AlertRuleKey.TRACKING_FAILURE
            assert alert.severity == AlertSeverity.CRITICAL
            assert alert.status == AlertStatus.OPEN
            assert alert.bucket_date == _day()
            assert alert.context["queue_id"] == id_
            assert alert.context["worker_id"] == "w1"
            assert alert.context["attempts"] == 2

            # A dead-lettered bucket is not claimable; only a fresh enqueue
            # (real new dirt, or an operator retry) brings it back.
            assert q.claim(worker_id="w2", jobs=[job]) == []
            assert q.enqueue(job, _day(), reason=RecomputeReason.MANUAL, priority=1)
            db.commit()
            revived = _row(db, id_)
            assert revived.status == RecomputeStatus.PENDING
            assert revived.attempts == 0, "new dirt gets a fresh retry budget"
            assert revived.last_error is None
        finally:
            _cleanup([job])
            db.close()


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------

class TestDepth:

    def test_depth_reports_counts_by_status(self) -> None:
        """Asserted as deltas: the queue is shared, so absolute counts depend on
        whatever else is in flight."""
        job = _job()
        db = SessionLocal()
        try:
            q = RecomputeQueue(db)
            base = q.depth()

            for i in range(4):
                q.enqueue(job, _day(i), reason=RecomputeReason.REFUND)
            db.commit()

            after_enqueue = q.depth()
            assert after_enqueue[RecomputeStatus.PENDING] - base[
                RecomputeStatus.PENDING
            ] == 4
            assert after_enqueue["total"] - base["total"] == 4
            assert after_enqueue["oldest_pending_age_seconds"] >= 0

            claimed = q.claim(worker_id="w1", limit=2, jobs=[job])
            assert len(claimed) == 2
            q.complete([claimed[0].id], worker_id="w1")
            q.fail(claimed[1].id, worker_id="w1", error="dead", max_attempts=1)

            final = q.depth()
            delta = {
                k: final[k] - base.get(k, 0)
                for k in (
                    RecomputeStatus.PENDING,
                    RecomputeStatus.CLAIMED,
                    RecomputeStatus.DONE,
                    RecomputeStatus.FAILED,
                    "total",
                )
            }
            assert delta == {
                RecomputeStatus.PENDING: 2,
                RecomputeStatus.CLAIMED: 0,
                RecomputeStatus.DONE: 1,
                RecomputeStatus.FAILED: 1,
                "total": 4,
            }, delta
        finally:
            _cleanup([job])
            db.close()
