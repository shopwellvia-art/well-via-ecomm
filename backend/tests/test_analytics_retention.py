"""Tests for the analytics retention and pruning jobs.

What actually has to be true here
---------------------------------
A prune that deletes too little is a table that grows. A prune that deletes too
much is data that is gone, and — for everything except one policy — that is
recoverable by recomputing from the facts. The exception is the reason this file
exists:

  * ``test_geo_collapse_preserves_state_grain_totals`` — there is no
    pincode-grain fact table anywhere in this schema, so a pincode row deleted
    without first being summed into its state row is unrecoverable from
    anything. Sum before must equal sum after, per state, per day, on every
    measure. This is the test carrying the weight.
  * ``test_dry_run_reports_what_a_real_run_would_delete`` — an operator
    authorises a deletion on the strength of the preview. If the preview
    estimates rather than measures, the authorisation was uninformed.
  * ``test_pruning_never_runs_ahead_of_the_watermark`` — pruning a bucket the
    aggregator has not reached deletes a day that nobody will notice is missing,
    because an absent rollup looks exactly like a quiet trading day.
  * ``test_snapshot_keeps_month_end_rows_and_drops_mid_month_ones`` — the half
    of the snapshot policy that a plain age predicate silently gets wrong.

Isolation strategy
------------------
Every fixture lives in the **2002-2003 sandbox**, years before this store's
first order, and the run clock is injected (``now=NOW``) so all three horizons —
90, 180 and 400 days — land inside it. That is what makes the assertions
absolute ("exactly these two ids survive") rather than deltas against whatever
the shared throwaway MySQL already holds.

**The sandbox is the oldest in the repository, and that is deliberate.** This is
the one thing to know before moving it. ``prune_all`` prunes a *table*, not a
date range, so anything older than this module's injected clock is inside its
horizons and would be deleted by these tests and counted in their assertions.
Putting the clock at 2003 — below the earliest date any other suite uses (2004)
— makes that impossible by construction: every other sandbox is in this module's
*future* and therefore outside every horizon. A sandbox merely "unused" is not
enough; it has to be *earliest*.

:func:`_assert_window_is_clean` fails loudly if that ever stops holding, rather
than letting a foreign row turn an exact count into a quietly wrong one.

Every test drives one policy at a time through ``only=[...]``, so a failure
names the policy that broke rather than the run that contained it.

Teardown deletes every owned row through a fresh session — the ``agg_*`` rows in
the sandbox date range, the ``cart_events`` / outbox / queue rows carrying this
module's key prefix, and every ``analytics_sync_runs`` row written under this
module's worker id (which includes the run rows ``prune_all`` writes itself). No
db fixture exists in ``conftest.py``; each test owns its ``SessionLocal()`` and
closes it in ``finally``.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.analytics_control import (
    AnalyticsEventOutbox,
    AnalyticsRecomputeQueue,
    AnalyticsSyncRun,
    ConsentState,
    OutboxEventName,
    OutboxStatus,
    RecomputeReason,
    RecomputeStatus,
    SyncStatus,
    SyncTrigger,
)
from app.models.analytics_facts import CartEvent, CartEventType
from app.models.analytics_rollups import (
    AggCustomerSnapshot,
    AggGeoDaily,
    AggOrderHourly,
)
from app.services.analytics.retention import (
    CART_EVENT_DAYS,
    GEO_PINCODE_DAYS,
    HOURLY_RETENTION_DAYS,
    PRUNE_JOB_NAME,
    SNAPSHOT_DAILY_DAYS,
    describe_policies,
    policy_keys,
    prune_all,
)

# ---------------------------------------------------------------------------
# The 2002-2003 sandbox
# ---------------------------------------------------------------------------
#: The instant every run in this module believes it is happening at. Midday UTC
#: is 17:30 in Asia/Kolkata, so the store-local reporting day is unambiguously
#: the same calendar date — a midnight-adjacent choice would make every horizon
#: below depend on the store timezone setting.
NOW = datetime(2003, 9, 15, 12, 0, tzinfo=timezone.utc)
TODAY = date(2003, 9, 15)

#: The three bucket-date horizons, computed the same way the module does.
CUT_90 = TODAY - timedelta(days=HOURLY_RETENTION_DAYS)    # 2003-06-17
CUT_180 = TODAY - timedelta(days=GEO_PINCODE_DAYS)        # 2003-03-19
CUT_400 = TODAY - timedelta(days=CART_EVENT_DAYS)         # 2002-08-11

#: Everything this module writes lives inside here; teardown sweeps the range.
SANDBOX_FIRST = date(2002, 1, 1)
SANDBOX_LAST = date(2003, 12, 31)

#: A watermark far enough forward that no policy is clamped by it, except in the
#: one test that deliberately lowers it.
WATERMARK = date(2003, 9, 14)

WORKER_ID = "pytest-retention"

#: Prefix on every `event_key` / `transaction_id` / `dimension_key` this module
#: writes, so teardown can find them without a date range.
TAG = "pytest-retention"

TZ_GENERATION = 1


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _clean(db: Session) -> None:
    """Remove everything this module owns. Safe to call before and after."""
    db.execute(
        text(
            "DELETE FROM agg_order_hourly WHERE bucket_date BETWEEN :a AND :b"
        ),
        {"a": SANDBOX_FIRST, "b": SANDBOX_LAST},
    )
    db.execute(
        text(
            "DELETE FROM agg_customer_snapshot WHERE bucket_date BETWEEN :a AND :b"
        ),
        {"a": SANDBOX_FIRST, "b": SANDBOX_LAST},
    )
    db.execute(
        text("DELETE FROM agg_geo_daily WHERE bucket_date BETWEEN :a AND :b"),
        {"a": SANDBOX_FIRST, "b": SANDBOX_LAST},
    )
    db.execute(
        text("DELETE FROM cart_events WHERE event_key LIKE :p"), {"p": f"{TAG}%"}
    )
    db.execute(
        text("DELETE FROM analytics_event_outbox WHERE transaction_id LIKE :p"),
        {"p": f"{TAG}%"},
    )
    db.execute(
        text("DELETE FROM analytics_recompute_queue WHERE dimension_key LIKE :p"),
        {"p": f"{TAG}%"},
    )
    # Written by the seeds below AND by every `prune_all` call in this module.
    db.execute(
        text("DELETE FROM analytics_sync_runs WHERE worker_id = :w"), {"w": WORKER_ID}
    )
    db.commit()


#: Every table a policy in this module can delete from, with the predicate that
#: decides eligibility. Used by the contamination guard below.
_PRUNE_TARGETS: tuple[tuple[str, str], ...] = (
    ("agg_order_hourly", "bucket_date < :day"),
    ("agg_customer_snapshot", "bucket_date < :day"),
    ("agg_geo_daily", "bucket_date < :day AND pincode <> '-'"),
    ("cart_events", "occurred_at < :moment"),
    ("analytics_recompute_queue", "processed_at IS NOT NULL AND processed_at < :moment"),
    (
        "analytics_event_outbox",
        "COALESCE(delivered_at, occurred_at) < :moment",
    ),
)


def _assert_window_is_clean(db: Session) -> None:
    """Fail loudly if anything foreign sits inside this module's horizons.

    ``prune_all`` is global by design — it prunes a table, not a sandbox — so a
    stray row left behind by another suite is inside every horizon here and
    would be deleted by these tests. That would not make them noisy, it would
    make them **wrong**: the counts they assert on inflate, and the
    batch-ordering tests would spend their batches on somebody else's rows
    (which sort first, being older) while leaving this module's untouched.

    Same reasoning and same shape as ``_assert_sandbox_is_empty`` in
    ``test_analytics_aggregation``. A contaminated window must produce a message
    that names the table, not a quietly wrong number.
    """
    params = {"day": TODAY, "moment": NOW.replace(tzinfo=None)}
    strays = {}
    for table, predicate in _PRUNE_TARGETS:
        count = int(
            db.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE {predicate}"), params
            ).scalar()
            or 0
        )
        if count:
            strays[table] = count
    assert not strays, (
        f"rows older than this module's clock ({TODAY}) are already present: "
        f"{strays}. `prune_all` is global, so these would be deleted by the "
        "tests below and counted in their assertions. Another suite left them "
        "behind, or a second test session is running against this database."
    )


@pytest.fixture(autouse=True)
def sandbox() -> None:
    """Clean before and after, so a crashed run cannot poison the next one."""
    db = SessionLocal()
    try:
        _clean(db)
        _assert_window_is_clean(db)
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        _clean(db)
    finally:
        db.close()


def _watermark(db: Session, job: str, through: date = WATERMARK) -> AnalyticsSyncRun:
    """Record that `job` has aggregated everything through `through`.

    ``started_at`` is inside the sandbox so :func:`_aggregated_through`'s
    ``started_at <= now`` filter picks this row and ignores whatever real runs
    the shared test database happens to carry from other suites.
    """
    run = AnalyticsSyncRun(
        job=job,
        trigger=SyncTrigger.CRON,
        status=SyncStatus.SUCCESS,
        worker_id=WORKER_ID,
        window_from=through,
        window_to=through,
        tz_generation=TZ_GENERATION,
        days_requested=1,
        days_processed=1,
        rows_written=1,
        rows_deleted=0,
        watermark_date=through,
        started_at=datetime(2003, 9, 15, 0, 0),
        finished_at=datetime(2003, 9, 15, 0, 1),
        duration_ms=60_000,
    )
    db.add(run)
    db.commit()
    return run


def _hourly(db: Session, bucket: date, hour: int = 0) -> AggOrderHourly:
    row = AggOrderHourly(
        bucket_date=bucket,
        bucket_hour=hour,
        tz_generation=TZ_GENERATION,
        orders=1,
        net_revenue=Decimal("100.00"),
        units=1,
    )
    db.add(row)
    return row


def _snapshot(db: Session, bucket: date, customer: str) -> AggCustomerSnapshot:
    row = AggCustomerSnapshot(
        bucket_date=bucket,
        tz_generation=TZ_GENERATION,
        customer_key=customer,
        orders_count=1,
        units=1,
        gross_ltv=Decimal("100.00"),
        net_ltv=Decimal("100.00"),
        margin_ltv=Decimal("40.00"),
        aov=Decimal("100.00"),
        recency_days=1,
        frequency=1,
        monetary=Decimal("100.00"),
        rfm_segment="loyal",
        cohort_month=bucket.strftime("%Y-%m"),
        tenure_days=10,
        churn_risk_band="low",
        preferred_payment_method="prepaid",
        quality="actual",
    )
    db.add(row)
    return row


def _geo(
    db: Session,
    bucket: date,
    state: str,
    pincode: str,
    *,
    orders: int = 1,
    revenue: str = "100.00",
    units: int = 2,
    cod: int = 1,
    prepaid: int = 0,
    delivered: int = 1,
    rto: int = 0,
    seconds: int = 3600,
    n_delivery: int = 1,
) -> AggGeoDaily:
    row = AggGeoDaily(
        bucket_date=bucket,
        tz_generation=TZ_GENERATION,
        state=state,
        pincode=pincode,
        orders=orders,
        net_revenue=Decimal(revenue),
        units=units,
        cod_orders=cod,
        prepaid_orders=prepaid,
        delivered=delivered,
        rto=rto,
        sum_delivery_seconds=seconds,
        n_delivery=n_delivery,
    )
    db.add(row)
    return row


def _cart_event(db: Session, occurred: datetime, suffix: str) -> CartEvent:
    row = CartEvent(
        occurred_at=occurred,
        session_key=f"{TAG}-session",
        event_type=CartEventType.PRODUCT_VIEWED,
        event_key=f"{TAG}-{suffix}",
    )
    db.add(row)
    return row


def _queue_row(db: Session, processed_at: datetime, status: str, suffix: str) -> None:
    db.add(
        AnalyticsRecomputeQueue(
            job="order_daily",
            bucket_date=date(2003, 1, 1),
            dimension_key=f"{TAG}-{suffix}",
            tz_generation=TZ_GENERATION,
            reason=RecomputeReason.MANUAL,
            status=status,
            enqueued_at=processed_at,
            processed_at=processed_at,
        )
    )


def _outbox_row(db: Session, delivered_at: datetime, status: str, suffix: str) -> None:
    db.add(
        AnalyticsEventOutbox(
            event_name=OutboxEventName.PURCHASE,
            transaction_id=f"{TAG}-{suffix}",
            occurred_at=delivered_at,
            payload={"value": 1},
            consent_state=ConsentState.GRANTED,
            status=status,
            delivered_at=delivered_at if status == OutboxStatus.DELIVERED else None,
        )
    )


def _run(db: Session, key: str, **kwargs) -> dict:
    """`prune_all` for one policy, on the sandbox clock."""
    kwargs.setdefault("batch_size", 100)
    return prune_all(db, only=[key], worker_id=WORKER_ID, now=NOW, **kwargs)


def _ids(db: Session, model, **filters) -> set[int]:
    statement = select(model.id)
    for column, value in filters.items():
        statement = statement.where(getattr(model, column) == value)
    return set(db.execute(statement).scalars().all())


# ---------------------------------------------------------------------------
# 1. Each policy deletes exactly the rows past its horizon and none before it
# ---------------------------------------------------------------------------
def test_hourly_prunes_exactly_the_buckets_past_ninety_days() -> None:
    """The boundary is exclusive: the cutoff day itself survives.

    Asserted on the day either side of the horizon rather than on a count,
    because an off-by-one here silently shortens every retention window in the
    file by a day and no total would show it.
    """
    db = SessionLocal()
    try:
        _watermark(db, "order_hourly")
        gone_old = _hourly(db, CUT_90 - timedelta(days=30))
        gone_edge = _hourly(db, CUT_90 - timedelta(days=1))
        kept_edge = _hourly(db, CUT_90)
        kept_new = _hourly(db, CUT_90 + timedelta(days=30))
        db.commit()
        doomed = {gone_old.id, gone_edge.id}
        survivors = {kept_edge.id, kept_new.id}

        result = _run(db, "agg_order_hourly")

        assert result["policies"]["agg_order_hourly"]["rows"] == 2
        assert result["policies"]["agg_order_hourly"]["cutoff"] == CUT_90
        remaining = _ids(db, AggOrderHourly, tz_generation=TZ_GENERATION)
        assert doomed.isdisjoint(remaining)
        assert survivors.issubset(remaining)
    finally:
        db.close()


def test_control_plane_policies_prune_only_terminal_rows() -> None:
    """`done` / `delivered` go; `failed` and `pending` never do.

    Those are what alerting reads and what the outbox still owes GA4. A prune
    that swept them would close the only report of a bucket that will never
    rebuild, and would discharge a conversion debt without paying it — both
    while leaving a queue that looks perfectly healthy.
    """
    db = SessionLocal()
    try:
        old = NOW.replace(tzinfo=None) - timedelta(days=200)
        recent = NOW.replace(tzinfo=None) - timedelta(days=5)

        _queue_row(db, old, RecomputeStatus.DONE, "q-old-done")
        _queue_row(db, old, RecomputeStatus.FAILED, "q-old-failed")
        _queue_row(db, recent, RecomputeStatus.DONE, "q-new-done")
        _outbox_row(db, old, OutboxStatus.DELIVERED, "o-old-delivered")
        _outbox_row(db, old, OutboxStatus.FAILED, "o-old-failed")
        _outbox_row(db, old, OutboxStatus.SUPPRESSED_NO_CONSENT, "o-old-suppressed")
        _outbox_row(db, recent, OutboxStatus.DELIVERED, "o-new-delivered")
        db.commit()

        queue_result = _run(db, "analytics_recompute_queue")
        outbox_result = _run(db, "analytics_event_outbox")

        assert queue_result["policies"]["analytics_recompute_queue"]["rows"] == 1
        assert outbox_result["policies"]["analytics_event_outbox"]["rows"] == 1

        left_queue = set(
            db.execute(
                text(
                    "SELECT dimension_key FROM analytics_recompute_queue "
                    "WHERE dimension_key LIKE :p"
                ),
                {"p": f"{TAG}%"},
            ).scalars()
        )
        assert left_queue == {f"{TAG}-q-old-failed", f"{TAG}-q-new-done"}

        left_outbox = set(
            db.execute(
                text(
                    "SELECT transaction_id FROM analytics_event_outbox "
                    "WHERE transaction_id LIKE :p"
                ),
                {"p": f"{TAG}%"},
            ).scalars()
        )
        assert left_outbox == {
            f"{TAG}-o-old-failed",
            f"{TAG}-o-old-suppressed",
            f"{TAG}-o-new-delivered",
        }
    finally:
        db.close()


def test_cart_events_prune_on_a_store_local_day_boundary() -> None:
    """Whole reporting days go, never part of one.

    A half-deleted day leaves a session-level funnel that cannot be
    reconstructed next to a daily aggregate that can, and the disagreement only
    surfaces when someone tries to explain a number.
    """
    db = SessionLocal()
    try:
        _watermark(db, "funnel_daily")
        # 18:30 UTC on the day before the cutoff is already the *next* store-local
        # day in Asia/Kolkata (00:00 IST), so it must survive.
        _cart_event(db, datetime(2002, 8, 5, 9, 0), "old")
        _cart_event(
            db,
            datetime.combine(CUT_400 - timedelta(days=1), datetime.min.time())
            + timedelta(hours=18, minutes=30),
            "edge-next-day",
        )
        _cart_event(db, datetime(2003, 6, 1, 9, 0), "recent")
        db.commit()

        result = _run(db, "cart_events")

        assert result["policies"]["cart_events"]["rows"] == 1
        left = set(
            db.execute(
                text("SELECT event_key FROM cart_events WHERE event_key LIKE :p"),
                {"p": f"{TAG}%"},
            ).scalars()
        )
        assert left == {f"{TAG}-edge-next-day", f"{TAG}-recent"}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 2. dry_run deletes nothing and reports what a real run would
# ---------------------------------------------------------------------------
def test_dry_run_reports_what_a_real_run_would_delete() -> None:
    """Measured, not estimated — and identical, policy by policy.

    The comparison is the whole point. An operator authorises a deletion on the
    strength of the preview, so a preview that merely approximates the blast
    radius is a preview nobody should act on. Run the dry pass, assert nothing
    moved, then run for real and assert the numbers match exactly.
    """
    db = SessionLocal()
    try:
        _watermark(db, "order_hourly")
        _watermark(db, "customer_snapshot")
        _watermark(db, "shipment_geo_daily")
        for offset in range(7):
            _hourly(db, CUT_90 - timedelta(days=offset + 1), hour=offset)
        _snapshot(db, date(2003, 5, 15), "u:1")
        _snapshot(db, date(2003, 5, 31), "u:1")
        _geo(db, date(2003, 1, 10), "Karnataka", "560001")
        _geo(db, date(2003, 1, 10), "Karnataka", "560002")
        db.commit()

        before = _snapshot_of_tables(db)
        dry = prune_all(
            db,
            batch_size=3,
            dry_run=True,
            only=["agg_order_hourly", "agg_customer_snapshot", "agg_geo_daily_pincode"],
            worker_id=WORKER_ID,
            now=NOW,
        )
        assert dry["dry_run"] is True
        assert _snapshot_of_tables(db) == before, "a dry run must not move a row"

        real = prune_all(
            db,
            batch_size=3,
            only=["agg_order_hourly", "agg_customer_snapshot", "agg_geo_daily_pincode"],
            worker_id=WORKER_ID,
            now=NOW,
        )

        for key in dry["policies"]:
            assert dry["policies"][key] == real["policies"][key], key
        assert dry["rows_deleted"] == real["rows_deleted"] == 10
        assert dry["rows_written"] == real["rows_written"] == 1
        assert _snapshot_of_tables(db) != before, "the real run must delete"
    finally:
        db.close()


def _snapshot_of_tables(db: Session) -> dict[str, int]:
    """Row counts for everything the dry-run test touches."""
    db.expire_all()
    return {
        "hourly": _count(db, "agg_order_hourly"),
        "snapshot": _count(db, "agg_customer_snapshot"),
        "geo": _count(db, "agg_geo_daily"),
    }


def _count(db: Session, table: str) -> int:
    return int(
        db.execute(
            text(
                f"SELECT COUNT(*) FROM {table} WHERE bucket_date BETWEEN :a AND :b"
            ),
            {"a": SANDBOX_FIRST, "b": SANDBOX_LAST},
        ).scalar()
        or 0
    )


# ---------------------------------------------------------------------------
# 3. Batch bounding
# ---------------------------------------------------------------------------
def test_a_large_set_is_deleted_across_bounded_batches() -> None:
    """No single statement may exceed the cap.

    The cap is not a performance tuning knob. An unbounded DELETE over a year of
    rows holds a table lock for its whole duration, and on the queue table every
    worker trying to claim a bucket waits behind it — the prune becomes the
    outage. Twenty-four rows at a batch of five must be five statements, none
    larger than five.
    """
    db = SessionLocal()
    try:
        _watermark(db, "order_hourly")
        old = CUT_90 - timedelta(days=1)
        for hour in range(24):
            _hourly(db, old, hour=hour)
        db.commit()

        result = _run(db, "agg_order_hourly", batch_size=5)
        policy = result["policies"]["agg_order_hourly"]

        assert policy["rows"] == 24
        assert policy["batches"] == 5
        assert policy["max_batch_rows"] <= 5
        assert policy["capped"] is False
        assert _count(db, "agg_order_hourly") == 0
    finally:
        db.close()


def test_the_per_run_ceiling_stops_the_run_and_says_so() -> None:
    """A ceilinged run reports `capped` and leaves the rest for the next tick.

    ``capped`` has to distinguish "stopped because the ceiling was reached" from
    "stopped because there was nothing left" — they produce the same row count
    and mean opposite things to whoever is watching the table size.
    """
    db = SessionLocal()
    try:
        _watermark(db, "order_hourly")
        old = CUT_90 - timedelta(days=1)
        for hour in range(24):
            _hourly(db, old, hour=hour)
        db.commit()

        first = _run(db, "agg_order_hourly", batch_size=4, max_rows_per_policy=10)
        assert first["policies"]["agg_order_hourly"]["rows"] == 10
        assert first["policies"]["agg_order_hourly"]["capped"] is True
        assert _count(db, "agg_order_hourly") == 14

        second = _run(db, "agg_order_hourly", batch_size=100, max_rows_per_policy=100)
        assert second["policies"]["agg_order_hourly"]["rows"] == 14
        assert second["policies"]["agg_order_hourly"]["capped"] is False
        assert _count(db, "agg_order_hourly") == 0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 4. The geo collapse preserves state-grain totals — the important one
# ---------------------------------------------------------------------------
def test_geo_collapse_preserves_state_grain_totals() -> None:
    """Sum before must equal sum after, at state grain, on every measure.

    This is the one policy in the subsystem where a bug cannot be undone. Every
    other rollup row the retention job removes is derived from a fact table and
    can be rebuilt; there is no pincode-grain fact anywhere in this schema, so a
    pincode row deleted before it was summed into its state row is simply gone.

    The fixture is deliberately awkward: two states on one day, an already
    existing state-grain row for one of them (so the collapse must ADD rather
    than overwrite), a second day, and a recent day that must not be touched at
    all. A collapse that overwrote instead of accumulating passes a naive
    row-count assertion and fails this one.
    """
    db = SessionLocal()
    try:
        _watermark(db, "shipment_geo_daily")
        old_a = CUT_180 - timedelta(days=10)
        old_b = CUT_180 - timedelta(days=40)
        recent = CUT_180 + timedelta(days=10)

        _geo(db, old_a, "Karnataka", "560001", orders=3, revenue="300.00", units=6)
        _geo(db, old_a, "Karnataka", "560002", orders=2, revenue="250.50", units=4)
        # Already at state grain: a genuine "pincode unknown" row from the
        # aggregator. The collapse must add into it, not replace it.
        _geo(db, old_a, "Karnataka", "-", orders=1, revenue="99.50", units=1)
        _geo(db, old_a, "Kerala", "682001", orders=5, revenue="500.00", units=10)
        _geo(db, old_b, "Karnataka", "560001", orders=7, revenue="700.25", units=14)
        _geo(db, recent, "Karnataka", "560001", orders=9, revenue="900.00", units=18)
        db.commit()

        before = _geo_totals_by_state(db)
        recent_pincode_ids = _ids(db, AggGeoDaily, bucket_date=recent)

        result = _run(db, "agg_geo_daily_pincode")
        policy = result["policies"]["agg_geo_daily_pincode"]

        assert policy["rows"] == 4, "four pincode rows were past the 180-day horizon"
        assert policy["collapsed_into"] == 3, "three (day, state) destinations"

        after = _geo_totals_by_state(db)
        assert after == before, "state-grain totals must survive the collapse exactly"

        # And the pincode grain is genuinely gone for the aged days only.
        db.expire_all()
        aged_pincodes = db.execute(
            text(
                "SELECT COUNT(*) FROM agg_geo_daily "
                "WHERE bucket_date < :cutoff AND pincode <> '-'"
            ),
            {"cutoff": CUT_180},
        ).scalar()
        assert aged_pincodes == 0
        assert recent_pincode_ids == _ids(db, AggGeoDaily, bucket_date=recent)
    finally:
        db.close()


def _geo_totals_by_state(db: Session) -> dict[tuple[date, str], tuple]:
    """Every measure, summed to (day, state) — the grain the collapse must preserve."""
    db.expire_all()
    rows = db.execute(
        select(
            AggGeoDaily.bucket_date,
            AggGeoDaily.state,
            func.sum(AggGeoDaily.orders),
            func.sum(AggGeoDaily.net_revenue),
            func.sum(AggGeoDaily.units),
            func.sum(AggGeoDaily.cod_orders),
            func.sum(AggGeoDaily.prepaid_orders),
            func.sum(AggGeoDaily.delivered),
            func.sum(AggGeoDaily.rto),
            func.sum(AggGeoDaily.sum_delivery_seconds),
            func.sum(AggGeoDaily.n_delivery),
        )
        .where(AggGeoDaily.bucket_date.between(SANDBOX_FIRST, SANDBOX_LAST))
        .group_by(AggGeoDaily.bucket_date, AggGeoDaily.state)
    ).all()
    return {(row[0], row[1]): tuple(row[2:]) for row in rows}


def test_geo_collapse_writes_nothing_when_it_deletes_nothing_in_dry_run() -> None:
    """The dangerous policy's preview must be inert in *both* directions.

    A dry run that skipped the delete but still performed the state-grain upsert
    would double every collapsed total the moment the real run followed it — the
    worst possible outcome for the one table that cannot be rebuilt.
    """
    db = SessionLocal()
    try:
        _watermark(db, "shipment_geo_daily")
        old = CUT_180 - timedelta(days=5)
        _geo(db, old, "Karnataka", "560001", orders=3, revenue="300.00")
        _geo(db, old, "Karnataka", "560002", orders=2, revenue="200.00")
        db.commit()

        before = _geo_totals_by_state(db)
        rows_before = _count(db, "agg_geo_daily")

        result = _run(db, "agg_geo_daily_pincode", dry_run=True)

        assert result["policies"]["agg_geo_daily_pincode"]["rows"] == 2
        assert result["policies"]["agg_geo_daily_pincode"]["collapsed_into"] == 1
        assert _count(db, "agg_geo_daily") == rows_before
        assert _geo_totals_by_state(db) == before
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 5. Pruning does not run ahead of the watermark
# ---------------------------------------------------------------------------
def test_pruning_never_runs_ahead_of_the_watermark() -> None:
    """A bucket the aggregator has not reached is never pruned.

    Pruning ahead of the aggregator deletes a day that nobody will notice is
    missing: the rollup for it is simply absent, and an absent rollup is
    indistinguishable from a quiet trading day. Here the watermark sits *behind*
    the 90-day horizon, so the horizon must yield to it.
    """
    db = SessionLocal()
    try:
        behind = CUT_90 - timedelta(days=20)
        _watermark(db, "order_hourly", through=behind)
        prunable = _hourly(db, behind - timedelta(days=1))
        protected = _hourly(db, behind + timedelta(days=1))
        also_protected = _hourly(db, CUT_90 - timedelta(days=2))
        db.commit()

        result = _run(db, "agg_order_hourly")
        policy = result["policies"]["agg_order_hourly"]

        assert policy["cutoff"] == behind + timedelta(days=1)
        assert policy["rows"] == 1
        assert any("clamped" in warning for warning in policy["warnings"])

        remaining = _ids(db, AggOrderHourly, tz_generation=TZ_GENERATION)
        assert prunable.id not in remaining
        assert {protected.id, also_protected.id}.issubset(remaining)
    finally:
        db.close()


def test_a_job_with_no_watermark_prunes_nothing_at_all() -> None:
    """No watermark means no proof, and no proof means no deletion.

    A job that has never completed a bucket cannot vouch for *any* day, so the
    correct output is zero rows and a stated reason — not a horizon applied on
    the assumption that something 400 days old must surely have been aggregated
    by now.
    """
    db = SessionLocal()
    try:
        _cart_event(db, datetime(2002, 6, 1, 9, 0), "very-old")
        db.commit()

        result = _run(db, "cart_events")
        policy = result["policies"]["cart_events"]

        assert policy["skipped"] == "awaiting_watermark"
        assert policy["rows"] == 0
        assert policy["cutoff"] is None
        assert _count_cart_events(db) == 1
    finally:
        db.close()


def _count_cart_events(db: Session) -> int:
    return int(
        db.execute(
            text("SELECT COUNT(*) FROM cart_events WHERE event_key LIKE :p"),
            {"p": f"{TAG}%"},
        ).scalar()
        or 0
    )


# ---------------------------------------------------------------------------
# 6. agg_customer_snapshot keeps month-end rows
# ---------------------------------------------------------------------------
def test_snapshot_keeps_month_end_rows_and_drops_mid_month_ones() -> None:
    """Beyond 90 days only month-end rows survive; inside it, everything does.

    The month-end half is what a plain age predicate gets wrong, and it gets it
    wrong invisibly — the table shrinks, nothing errors, and the LTV curve
    simply stops before the last quarter. February and leap years are why this
    is `LAST_DAY()` in SQL rather than a date list built in Python.
    """
    db = SessionLocal()
    try:
        _watermark(db, "customer_snapshot")
        # Past the horizon.
        dropped_mid = _snapshot(db, date(2003, 4, 15), "u:1")
        kept_month_end = _snapshot(db, date(2003, 4, 30), "u:1")
        kept_feb_end = _snapshot(db, date(2003, 2, 28), "u:1")
        dropped_feb_mid = _snapshot(db, date(2003, 2, 27), "u:1")
        # Inside the horizon: a mid-month row here is retained.
        kept_recent_mid = _snapshot(db, date(2003, 8, 15), "u:1")
        db.commit()

        result = _run(db, "agg_customer_snapshot")

        assert result["policies"]["agg_customer_snapshot"]["rows"] == 2
        remaining = _ids(db, AggCustomerSnapshot, customer_key="u:1")
        assert {kept_month_end.id, kept_feb_end.id, kept_recent_mid.id}.issubset(
            remaining
        )
        assert {dropped_mid.id, dropped_feb_mid.id}.isdisjoint(remaining)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 7. Every run is audited
# ---------------------------------------------------------------------------
def test_every_run_writes_an_analytics_sync_run_row() -> None:
    """Pruning is as auditable as aggregating, dry runs included.

    A prune that leaves no trace is indistinguishable from a prune that never
    ran — the same failure the run log exists to make visible for aggregation.
    The dry run's row records ``rows_deleted = 0`` and says what it *would* have
    removed in the message, so the log can never imply a deletion that did not
    happen.
    """
    db = SessionLocal()
    try:
        _watermark(db, "order_hourly")
        for hour in range(3):
            _hourly(db, CUT_90 - timedelta(days=1), hour=hour)
        db.commit()

        dry = _run(db, "agg_order_hourly", dry_run=True)
        real = _run(db, "agg_order_hourly")

        runs = {
            run.id: run
            for run in db.execute(
                select(AnalyticsSyncRun).where(
                    AnalyticsSyncRun.job == PRUNE_JOB_NAME,
                    AnalyticsSyncRun.worker_id == WORKER_ID,
                )
            ).scalars()
        }
        assert set(runs) == {dry["sync_run_id"], real["sync_run_id"]}

        dry_run_row = runs[dry["sync_run_id"]]
        assert dry_run_row.status == SyncStatus.SUCCESS
        assert dry_run_row.rows_deleted == 0
        assert "dry_run" in (dry_run_row.error or "")
        assert dry_run_row.finished_at is not None

        real_run_row = runs[real["sync_run_id"]]
        assert real_run_row.rows_deleted == 3
        assert real_run_row.days_processed == 1
        # A prune advances no job's data watermark; writing one would assert a
        # freshness it did not produce.
        assert real_run_row.watermark_date is None
    finally:
        db.close()


def test_the_run_log_row_is_not_pruned_by_its_own_run() -> None:
    """A prune must never delete the record of itself.

    ``analytics_sync_runs`` is one of the tables this job prunes, so the
    self-reference is real rather than theoretical: the run row is written
    before the policies execute, and the sync-run policy runs while it is still
    ``RUNNING``.
    """
    db = SessionLocal()
    try:
        result = _run(db, "analytics_sync_runs")
        assert (
            db.get(AnalyticsSyncRun, result["sync_run_id"]) is not None
        ), "the prune deleted its own audit row"
    finally:
        db.close()


def test_sync_run_prune_keeps_failures_and_the_highest_watermark() -> None:
    """Only routine successes go; the forensic rows and the watermark stay.

    ``AnalyticsSyncRun``'s docstring says rows are never deleted by the
    application, and this policy is a deliberate, narrow exception to it. The
    scope of that exception is the whole justification, so it is asserted rather
    than described: a failure, a still-running row, and the row carrying a job's
    highest watermark all survive a prune that is otherwise 200 days past the
    horizon.
    """
    db = SessionLocal()
    try:
        old = datetime(2003, 1, 1, 0, 0)
        base = dict(
            trigger=SyncTrigger.CRON,
            worker_id=WORKER_ID,
            tz_generation=TZ_GENERATION,
            days_requested=1,
            days_processed=1,
            rows_written=0,
            rows_deleted=0,
            started_at=old,
        )
        routine = AnalyticsSyncRun(
            job="pytest_retired_job", status=SyncStatus.SUCCESS,
            finished_at=old, watermark_date=date(2002, 12, 30), **base
        )
        watermark_holder = AnalyticsSyncRun(
            job="pytest_retired_job", status=SyncStatus.SUCCESS,
            finished_at=old, watermark_date=date(2002, 12, 31), **base
        )
        failure = AnalyticsSyncRun(
            job="pytest_retired_job", status=SyncStatus.FAILED,
            finished_at=old, watermark_date=None, **base
        )
        stuck = AnalyticsSyncRun(
            job="pytest_retired_job", status=SyncStatus.RUNNING,
            finished_at=None, watermark_date=None, **base
        )
        db.add_all([routine, watermark_holder, failure, stuck])
        db.commit()
        doomed, survivors = routine.id, {watermark_holder.id, failure.id, stuck.id}

        result = _run(db, "analytics_sync_runs")

        assert result["policies"]["analytics_sync_runs"]["rows"] == 1
        remaining = _ids(db, AnalyticsSyncRun, job="pytest_retired_job")
        assert doomed not in remaining
        assert survivors.issubset(remaining)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 8. Idempotence
# ---------------------------------------------------------------------------
def test_re_running_is_idempotent() -> None:
    """A second pass finds nothing to do and does not error.

    The prune runs hourly. If a second pass over an already-clean table were
    anything other than a no-op — an error, or worse, a second collapse adding
    the same pincode totals into the state row again — the damage would compound
    once an hour, every hour, unnoticed.
    """
    db = SessionLocal()
    try:
        _watermark(db, "order_hourly")
        _watermark(db, "customer_snapshot")
        _watermark(db, "shipment_geo_daily")
        _watermark(db, "funnel_daily")
        _hourly(db, CUT_90 - timedelta(days=1))
        _snapshot(db, date(2003, 4, 15), "u:1")
        _geo(db, CUT_180 - timedelta(days=1), "Karnataka", "560001", orders=4)
        _cart_event(db, datetime(2002, 6, 1, 9, 0), "old")
        db.commit()

        first = prune_all(db, batch_size=10, worker_id=WORKER_ID, now=NOW)
        assert first["rows_deleted"] == 4

        totals_after_first = _geo_totals_by_state(db)

        second = prune_all(db, batch_size=10, worker_id=WORKER_ID, now=NOW)
        assert second["rows_deleted"] == 0
        assert second["status"] == SyncStatus.SUCCESS
        assert all(
            policy["error"] is None for policy in second["policies"].values()
        )
        assert (
            _geo_totals_by_state(db) == totals_after_first
        ), "a second collapse must not re-add the same totals"

        third = prune_all(db, batch_size=10, worker_id=WORKER_ID, now=NOW)
        assert third["rows_deleted"] == 0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# The policy table itself
# ---------------------------------------------------------------------------
def test_every_policy_key_has_a_pruner_and_a_stated_provenance() -> None:
    """The table is the documentation, so it has to be complete.

    ``source`` says whether a horizon came from a model docstring or was decided
    in the retention module. That distinction is what lets a reviewer find every
    number that has no owner, and it is worthless if a policy can be added
    without one.
    """
    described = {entry["key"]: entry for entry in describe_policies()}
    assert set(described) == set(policy_keys())
    for key, entry in described.items():
        assert entry["source"] in {"model docstring", "decided here"}, key
        assert entry["horizon_days"] > 0, key
        assert entry["summary"], key

    assert described["agg_order_hourly"]["horizon_days"] == HOURLY_RETENTION_DAYS
    assert described["agg_customer_snapshot"]["horizon_days"] == SNAPSHOT_DAILY_DAYS
    assert described["agg_geo_daily_pincode"]["horizon_days"] == GEO_PINCODE_DAYS
    assert described["cart_events"]["horizon_days"] == CART_EVENT_DAYS


def test_csp_report_retention_is_reported_as_not_implemented() -> None:
    """Nothing stores a CSP report, and the output says so rather than zero.

    "Zero rows pruned" and "this is not a thing yet" look identical in a count
    and mean completely different things to anyone auditing what the store
    retains. The policy is declared so it engages by itself the day a table
    appears.
    """
    db = SessionLocal()
    try:
        result = _run(db, "csp_reports")
        policy = result["policies"]["csp_reports"]
        assert policy["skipped"] == "not_implemented"
        assert policy["rows"] == 0
        assert any("no CSP violation reports" in w for w in policy["warnings"])
    finally:
        db.close()


def test_an_unknown_policy_name_is_refused() -> None:
    """A typo must fail loudly rather than silently prune nothing.

    ``only=`` is how a deployment opts out of a policy it does not accept. A
    misspelled name that quietly matched nothing would look exactly like a
    successful run of the policies the operator meant to keep.
    """
    db = SessionLocal()
    try:
        with pytest.raises(KeyError):
            prune_all(db, batch_size=10, only=["agg_order_hourley"], now=NOW)
    finally:
        db.close()
