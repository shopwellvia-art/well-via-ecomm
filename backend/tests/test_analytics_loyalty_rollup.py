"""Tests for ``loyalty_daily`` -> ``agg_loyalty_daily``, and for view 52 on it.

The thirteenth rollup. ``test_analytics_aggregation{,_jobs2,_jobs3}.py`` already
prove the runner, the two write patterns and the other twelve jobs; this file
repeats none of that and tests only what this table does differently.

What actually has to be true here
---------------------------------
Three of these would be caught the first time somebody opened the view. The ones
carrying the weight are the ones whose failures are silent:

  * ``test_loyalty_daily_is_idempotent`` — the same bucket, twice, identical
    column values. Not "no error" and not "one row": the numbers. An
    ``ON DUPLICATE KEY UPDATE col = col + VALUES(col)`` passes everything else in
    this file and fails this.
  * ``test_loyalty_daily_removes_a_reason_that_disappeared`` — the table is
    dimensioned by ``reason``, so a reason group can vanish when its only
    transaction is backed out. Delete-then-insert is the only write pattern that
    expresses that; an upsert leaves the vacated row reporting points that are no
    longer in the ledger.
  * ``test_loyalty_daily_bucketing_is_store_local`` — 23:00 IST is 17:30 UTC on
    the same date; 00:30 IST is 19:00 UTC on the date *before*. A UTC bucketer
    puts both in one day and is wrong by 5.5 hours of ledger, every day.
  * ``test_earned_and_redeemed_are_separate_and_never_negative`` — the claim this
    table exists to make. ``points_transactions.delta`` is SIGNED, and a rollup
    that stored one net column could not answer "how many points did we issue":
    1 000 issued against 1 000 redeemed and a dormant month are the same net
    zero. Both counters must be non-negative and must move independently.
  * ``test_points_outstanding_close_is_a_level_and_is_never_summed`` — the
    balance is a LEVEL. Summing thirty daily balances reports thirty times the
    liability, the query succeeds, and the number looks plausible. Asserted
    three ways: the classifier says LEVEL, ``assert_summable`` refuses it, and
    the naive SUM is shown to differ from the correct latest-bucket value so the
    guard is not vacuous.

Then the wiring, because a rollup nobody reads is not worth having:

  * ``test_view_52_resolves_end_to_end_with_real_values`` — through
    ``AnalyticsViewService``, so permissions, gating, the registry binding and
    envelope construction are all in the path, and the numbers coming back are
    the seeded ones rather than zeros.
  * ``TestSchemaAndTwinDdl`` — the live table matches the ORM, obeys the four
    conventions ``analytics_base`` sets, and agrees with the hand-applied SQL
    production gets. The migration and the twin cannot be allowed to drift: CI
    runs one and production runs the other.

Isolation strategy
------------------
House style, and no db fixture in ``conftest.py``: every test owns its
``SessionLocal()`` and tears down in a ``finally``.

The sandbox is **March 1999**, a month no other analytics test touches (2001,
2003, 2005-2010 and 2012-2015 are all spoken for) and one this store has
obviously never traded in. That date choice is doing real work here rather than
being decoration: ``points_outstanding_close`` is a GLOBAL ``SUM(delta)`` below
the bucket's close, so every real ledger row would land inside it if the sandbox
were in the present. In 1999 the cutoff excludes the entire live ledger by
construction, and the balance assertions can be absolute.

Unlike ``jobs3``, the group key here cannot be minted per test — ``reason`` is a
closed enum and ``'-'`` is the store-level row — so two concurrent copies of this
suite would fight over the same ``(day, reason)`` keys. Two things guard that,
and they are not the same guard:

  * Teardown is **swept, not owner-scoped**. ``_reset_sandbox`` empties the
    window by date and by an email marker rather than by the ids a test
    remembers, and it runs BEFORE seeding as well as in every ``finally``. A run
    that dies between the two therefore cannot poison the next one — which is
    the failure this file actually hit: leftover fixtures from an interrupted
    run tripped the precondition of every run after it.
  * ``_assert_sandbox_ledger_is_empty`` then runs *after* the reset, so the only
    thing it can still catch is another process writing into the window right
    now. That case cannot be survived quietly (every figure here is absolute),
    so it fails loudly and says what it found — the same call
    ``cohort_monthly`` makes for the same reason.

Run inside the analytics container:

    docker exec wvana-py python -m pytest tests/test_analytics_loyalty_rollup.py -q
"""
from __future__ import annotations

import os
import re
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import UniqueConstraint, inspect, select, text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.base import Base
from app.db.session import SessionLocal
from app.models.analytics_base import DIMENSION_UNKNOWN
from app.models.analytics_loyalty import AggLoyaltyDaily
from app.models.loyalty import PointsReason, PointsTransaction
from app.models.referral import Referral, ReferralStatus
from app.models.user import User
from app.repositories.analytics_repository import (
    columns_for,
    known_sources,
    measures_for,
)
from app.schemas.analytics_view import AnalyticsViewEnvelope
from app.services.analytics import registry
from app.services.analytics.aggregation import AggregationRunner
from app.services.analytics.aggregation.jobs_loyalty import (
    DEBIT_SUBSET_COLUMNS,
    STORE_LEVEL_REASON,
)
from app.services.analytics.filters import AnalyticsFilters, Comparison, Period
from app.services.analytics.metric_kind import MetricKind, NonAdditive, assert_summable, classify
from app.services.analytics.resolvers.base import NOT_CONFIGURED
from app.services.analytics.resolvers.core import METRIC_NOT_BOUND
from app.services.analytics.timebox import active_generation, day_bounds_utc, store_timezone
from app.services.analytics.types import ViewState
from app.services.analytics.view_service import AnalyticsViewService

# ---------------------------------------------------------------------------
# The March 1999 sandbox
# ---------------------------------------------------------------------------

SOURCE = "agg_loyalty_daily"
JOB = "loyalty_daily"

SANDBOX_FIRST = date(1999, 3, 1)
SANDBOX_LAST = date(1999, 3, 31)

DAY_MIX = date(1999, 3, 4)
DAY_GONE = date(1999, 3, 8)
DAY_TZ_A = date(1999, 3, 11)
DAY_TZ_B = date(1999, 3, 12)
DAY_LEVEL = (date(1999, 3, 15), date(1999, 3, 16), date(1999, 3, 17))
DAY_VIEW = (date(1999, 3, 22), date(1999, 3, 23), date(1999, 3, 24))
#: Half-open, so the last reporting day the view window asks for is the 24th.
VIEW_WINDOW_END = date(1999, 3, 25)

#: Everything dated before this is sandbox, by construction: the live ledger
#: starts in this decade and `points_outstanding_close` sums below the bucket's
#: close, so a 1999 cutoff excludes the whole real programme.
SANDBOX_CUTOFF = date(2000, 1, 1)

#: Distinctive enough that teardown can delete this module's throwaway accounts
#: without an id list, and that a stuck row in a shared DB is traceable here.
USER_MARKER = "loyalty-rollup-"

#: Per-PROCESS, not per-module. ``analytics_sync_runs`` rows are opened by the
#: runner and UPDATEd when the bucket finishes, so a teardown that deleted every
#: row under a shared worker id would pull an in-flight run out from under a
#: concurrent copy of this suite — which surfaces as
#: ``StaleDataError: UPDATE ... expected to update 1 row(s); 0 were matched``,
#: a crash that says nothing about what actually went wrong. Scoping the id to
#: the process means teardown can only ever delete its own run log.
WORKER_ID = f"test-agg-loyalty-{os.getpid()}"

#: Bookkeeping, not measurement. Two runs of one bucket differ in these and must
#: be identical in everything else.
_NON_MEASURE_COLUMNS = frozenset({"id", "computed_at"})

TWIN_SQL = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "sql"
    / "2026-07-29_agg_loyalty_daily.sql"
)
REVISION = "a3d82f5c1e94"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _at(db: Session, day: date, hour: int, minute: int = 0) -> datetime:
    """The UTC instant that is ``hour:minute`` **store-local** on ``day``.

    Built from ``timebox.day_bounds_utc`` rather than by hand: the point of the
    bucketing test is that the day boundary is the store's, and deriving the
    fixture from the same function the job uses makes the assertion about
    bucketing rather than about two independent copies of the same arithmetic.
    """
    start, _ = day_bounds_utc(day, store_timezone(db))
    return start + timedelta(hours=hour, minutes=minute)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _create_user(db: Session) -> User:
    """A throwaway account, carrying this module's email marker.

    The marker is what teardown sweeps by. Ids are deliberately NOT tracked: a
    run that dies between seeding and its ``finally`` would leave rows nothing
    remembers, and the next run would then fail its precondition on debris its
    own predecessor dropped. Sweeping by marker and by sandbox date makes the
    sandbox self-healing instead.
    """
    user = User(
        email=f"{USER_MARKER}{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.flush()
    return user


def _award(
    db: Session,
    user: User,
    *,
    delta: int,
    reason: PointsReason,
    created_at: datetime,
) -> PointsTransaction:
    """One ledger row, written directly.

    Not through ``LoyaltyService``: that would mint coupons, move
    ``customers.points_balance`` and apply a VIP multiplier, none of which this
    rollup reads, and all of which would make the fixture's numbers depend on
    seeded configuration. ``ref_type``/``ref_id`` are left NULL — the ledger's
    idempotency index is ``(reason, ref_type, ref_id)`` and MySQL does not
    collide NULLs, so several fixture rows may share a reason on one day.
    """
    row = PointsTransaction(
        user_id=user.id,
        delta=delta,
        reason=reason,
        ref_type=None,
        ref_id=None,
        description="loyalty rollup fixture",
        created_at=created_at,
    )
    db.add(row)
    db.flush()
    return row


def _complete_referral(db: Session, *, completed_at: datetime) -> Referral:
    """A referral that reached COMPLETED at a given instant.

    ``completed_order_id`` is left NULL on purpose: the rollup counts the
    completion event and deliberately stores no referral revenue (see the model
    docstring), so an order here would be fixture nobody reads.
    """
    referral = Referral(
        referrer_user_id=_create_user(db).id,
        referred_user_id=_create_user(db).id,
        code=f"REF-{_uid().upper()}",
        status=ReferralStatus.COMPLETED,
        completed_at=completed_at,
    )
    db.add(referral)
    db.flush()
    return referral


# ---------------------------------------------------------------------------
# Teardown + preconditions
# ---------------------------------------------------------------------------


def _reset_sandbox() -> None:
    """Empty the March 1999 sandbox, through a fresh session.

    Called BEFORE seeding as well as in every ``finally``, following
    ``test_analytics_remaining_views.py``: a run that died between seeding and
    its teardown must not be able to poison the next one. Owner-scoped deletion
    cannot give that — the ids live only in the dead process — so everything is
    swept by a marker this module owns instead:

      * the throwaway accounts, by the ``loyalty-rollup-`` email prefix. Their
        ledger rows and referrals go with them: ``points_transactions.user_id``
        and both of ``referrals``' user columns are ``ON DELETE CASCADE``, so a
        single indexed DELETE removes every fixture row this file can create.
      * rollup and queue rows by ``bucket_date``, run-log rows by this process's
        own ``worker_id`` — these are written by the runner and by Core
        statements whose ids the test never sees.

    Sweeping the ledger by DATE instead is the obvious move and is the wrong
    one: ``points_transactions.created_at`` carries no index of its own, so
    ``WHERE created_at < :cutoff`` scans the table and takes next-key locks
    across the entire live ledger. That deadlocks against any concurrent writer,
    which on a shared MySQL is any other suite that awards a point — observed,
    not theorised. ``email LIKE 'prefix%'`` is a range scan on a unique index
    and the cascade then follows ``ix_points_transactions_user_id``, so this
    touches only rows this module owns.

    A fresh session so teardown cannot be skipped by a half-rolled-back
    transaction in the test's own session.
    """
    with SessionLocal() as session:
        session.execute(
            text("DELETE FROM users WHERE email LIKE :marker"),
            {"marker": f"{USER_MARKER}%"},
        )
        window = {"first": SANDBOX_FIRST, "last": SANDBOX_LAST}
        for table in ("agg_loyalty_daily", "analytics_recompute_queue"):
            session.execute(
                text(f"DELETE FROM {table} WHERE bucket_date BETWEEN :first AND :last"),
                window,
            )
        session.execute(
            text("DELETE FROM analytics_sync_runs WHERE worker_id = :worker"),
            {"worker": WORKER_ID},
        )
        session.commit()


@contextmanager
def _sandbox() -> Iterator[Session]:
    """A session over an emptied sandbox, emptied again unconditionally.

    Reset, assert, yield, reset. The assertion sits between the two resets on
    purpose: after a reset the only thing that can still be in the window is
    another process writing to it right now, which is the case the precondition
    exists for and the one case this suite cannot survive quietly.
    """
    _reset_sandbox()
    db = SessionLocal()
    try:
        _assert_sandbox_ledger_is_empty(db)
        yield db
    finally:
        try:
            db.rollback()
        finally:
            db.close()
            _reset_sandbox()


def _assert_sandbox_ledger_is_empty(db: Session) -> None:
    """Fail loudly if a foreign ledger row or referral sits in March 1999.

    Runs AFTER ``_reset_sandbox``, so debris from an interrupted run is already
    gone and the only thing left to catch is another process writing into the
    window right now. ``reason`` is a closed enum and cannot be minted per test
    the way ``jobs3`` mints a gateway code, so two concurrent copies of this
    suite would share the ``(day, reason)`` keys. A stray row would not make
    these tests noisy — it would make them wrong — and
    ``points_outstanding_close`` is a global sum below the bucket's close, so
    anything dated earlier moves it too.

    Both probes are plain SELECTs: consistent, non-locking reads, unlike the
    date-scoped DELETE ``_reset_sandbox`` explicitly does not do.
    """
    stray_points = db.execute(
        select(PointsTransaction.id)
        .where(PointsTransaction.created_at < SANDBOX_CUTOFF)
        .limit(5)
    ).scalars().all()
    assert not stray_points, (
        f"points_transactions {stray_points} are dated before {SANDBOX_CUTOFF} and "
        "would land inside this suite's buckets and its closing balances; the "
        "March 1999 sandbox exists so the whole live ledger falls outside it"
    )
    stray_referrals = db.execute(
        select(Referral.id)
        .where(
            Referral.completed_at.isnot(None),
            Referral.completed_at < SANDBOX_CUTOFF,
        )
        .limit(5)
    ).scalars().all()
    assert not stray_referrals, (
        f"referrals {stray_referrals} completed before {SANDBOX_CUTOFF} and would "
        "be counted by this suite's store-level rows"
    )


def _runner(db: Session) -> AggregationRunner:
    return AggregationRunner(db, worker_id=WORKER_ID)


# ---------------------------------------------------------------------------
# Reading rollup rows back
# ---------------------------------------------------------------------------


def _measures(row: AggLoyaltyDaily) -> dict:
    """Every measured column of a rollup row, keyed by column name."""
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name not in _NON_MEASURE_COLUMNS
    }


def _keyed(db: Session, bucket: date, generation: int) -> dict[str, AggLoyaltyDaily]:
    """The bucket's rows, keyed by ``reason``."""
    db.expire_all()
    rows = db.execute(
        select(AggLoyaltyDaily).where(
            AggLoyaltyDaily.bucket_date == bucket,
            AggLoyaltyDaily.tz_generation == generation,
        )
    ).scalars().all()
    return {row.reason: row for row in rows}


# ===========================================================================
# 1. Idempotency
# ===========================================================================


def _mixed_day(db: Session, day: date) -> None:
    """One day with both signs, four reasons and a referral completion.

    Deliberately includes ``admin_adjust`` in BOTH directions on the same day:
    it is the one reason whose group carries a positive and a negative total at
    once, which is what makes ``points_earned`` and ``points_debited`` provably
    two different measurements rather than one number under two names.
    """
    alice = _create_user(db)
    bob = _create_user(db)

    _award(db, alice, delta=1000, reason=PointsReason.PLACE_ORDER, created_at=_at(db, day, 9))
    _award(db, bob, delta=200, reason=PointsReason.PLACE_ORDER, created_at=_at(db, day, 10))
    _award(db, alice, delta=50, reason=PointsReason.WRITE_REVIEW, created_at=_at(db, day, 11))

    _award(db, alice, delta=-300, reason=PointsReason.REDEEM, created_at=_at(db, day, 12))
    _award(db, bob, delta=-100, reason=PointsReason.REDEEM, created_at=_at(db, day, 13))
    _award(db, bob, delta=-25, reason=PointsReason.EXPIRY, created_at=_at(db, day, 14))
    _award(
        db, alice, delta=-200, reason=PointsReason.REFUND_REVERSAL,
        created_at=_at(db, day, 15),
    )

    _award(db, alice, delta=40, reason=PointsReason.ADMIN_ADJUST, created_at=_at(db, day, 16))
    _award(db, bob, delta=-15, reason=PointsReason.ADMIN_ADJUST, created_at=_at(db, day, 17))

    _complete_referral(db, completed_at=_at(db, day, 18))
    _complete_referral(db, completed_at=_at(db, day, 19))
    db.commit()


def test_loyalty_daily_is_idempotent() -> None:
    """Two runs of one bucket leave identical values, not doubled ones."""
    with _sandbox() as db:
        generation = active_generation(db).generation
        _mixed_day(db, DAY_MIX)

        runner = _runner(db)
        first = runner.run_bucket(JOB, DAY_MIX)
        before = {reason: _measures(row) for reason, row in _keyed(db, DAY_MIX, generation).items()}

        second = runner.run_bucket(JOB, DAY_MIX)
        after = {reason: _measures(row) for reason, row in _keyed(db, DAY_MIX, generation).items()}

        assert set(before) == {
            "place_order",
            "write_review",
            "redeem",
            "expiry",
            "refund_reversal",
            "admin_adjust",
            STORE_LEVEL_REASON,
        }, f"one row per reason that traded, plus the store-level row; got {sorted(before)}"
        assert before == after, (
            "re-running one bucket changed its values; the write is accumulating "
            f"rather than replacing. before={before} after={after}"
        )
        assert first.rows_written == second.rows_written == len(before)
        assert second.rows_deleted >= len(before), (
            "Pattern B deletes before it reinserts; an upsert deletes nothing and "
            "cannot express a reason that has left the bucket"
        )


# ===========================================================================
# 2. Delete-and-reinsert
# ===========================================================================


def test_loyalty_daily_removes_a_reason_that_disappeared() -> None:
    """A reason whose only transaction is deleted must lose its row.

    The store-level ``'-'`` row survives, and that is not an inconsistency: it
    carries the day's closing balance and its referral count, which are still
    real measurements on a day whose ledger activity was backed out.
    """
    with _sandbox() as db:
        generation = active_generation(db).generation
        user = _create_user(db)
        kept = _award(
            db, user, delta=500, reason=PointsReason.PLACE_ORDER,
            created_at=_at(db, DAY_GONE, 9),
        )
        doomed = _award(
            db, user, delta=-120, reason=PointsReason.REDEEM,
            created_at=_at(db, DAY_GONE, 10),
        )
        db.commit()

        runner = _runner(db)
        runner.run_bucket(JOB, DAY_GONE)
        rows = _keyed(db, DAY_GONE, generation)
        assert "redeem" in rows and rows["redeem"].points_redeemed == 120

        db.execute(
            text("DELETE FROM points_transactions WHERE id = :id"), {"id": doomed.id}
        )
        db.commit()

        result = runner.run_bucket(JOB, DAY_GONE)
        rows = _keyed(db, DAY_GONE, generation)

        assert "redeem" not in rows, (
            "the only redemption on this day is gone from the ledger, so the "
            "reason's row must GO rather than keep reporting 120 points nobody "
            "spent — which is exactly what an upsert would leave behind"
        )
        assert rows["place_order"].points_earned == 500, "the untouched reason stays"
        assert STORE_LEVEL_REASON in rows, (
            "the store-level row carries the day's closing balance and referral "
            "count, which are still measurements when a reason group empties"
        )
        assert rows[STORE_LEVEL_REASON].points_outstanding_close == 500, (
            "the deleted debit is no longer in the ledger, so the reconstructed "
            "closing balance moves with it"
        )
        assert result.rows_deleted >= 1
        assert kept.id  # the fixture row is still there; nothing else deleted it


# ===========================================================================
# 3. Store-local bucketing
# ===========================================================================


def test_loyalty_daily_bucketing_is_store_local() -> None:
    """A transaction at 23:00 IST lands in THAT IST day, not the UTC one."""
    with _sandbox() as db:
        generation = active_generation(db).generation
        user = _create_user(db)

        late = _award(
            db, user, delta=700, reason=PointsReason.PLACE_ORDER,
            created_at=_at(db, DAY_TZ_A, 23),
        )
        early = _award(
            db, user, delta=-90, reason=PointsReason.REDEEM,
            created_at=_at(db, DAY_TZ_B, 0, 30),
        )
        referral_late = _complete_referral(db, completed_at=_at(db, DAY_TZ_A, 23, 30))
        db.commit()

        assert late.created_at.date() == early.created_at.date(), (
            "fixture: both ledger rows must share a UTC date for this test to mean "
            f"anything ({late.created_at} vs {early.created_at})"
        )
        assert referral_late.completed_at.date() == early.created_at.date(), (
            "fixture: the referral completion must share that UTC date too"
        )

        runner = _runner(db)
        runner.run_bucket(JOB, DAY_TZ_A)
        runner.run_bucket(JOB, DAY_TZ_B)

        day_a = _keyed(db, DAY_TZ_A, generation)
        day_b = _keyed(db, DAY_TZ_B, generation)

        assert "place_order" in day_a and day_a["place_order"].points_earned == 700, (
            "a UTC-day bucketer puts the 23:00 IST earn in the earlier bucket"
        )
        assert "redeem" not in day_a, (
            "the 00:30 IST redemption belongs to the NEXT store-local day even "
            "though it shares a UTC date with the 23:00 IST earn"
        )
        assert "redeem" in day_b and day_b["redeem"].points_redeemed == 90
        assert "place_order" not in day_b

        assert day_a[STORE_LEVEL_REASON].referral_completions == 1, (
            "the 23:30 IST completion is dated to the IST day it happened on"
        )
        assert day_b[STORE_LEVEL_REASON].referral_completions == 0

        assert day_a[STORE_LEVEL_REASON].points_outstanding_close == 700
        assert day_b[STORE_LEVEL_REASON].points_outstanding_close == 610, (
            "the closing balance cutoff is the store-local end of the day, so the "
            "00:30 IST debit is inside day B's close and outside day A's"
        )


# ===========================================================================
# 4. Earned and redeemed are separate, and both non-negative
# ===========================================================================


def test_earned_and_redeemed_are_separate_and_never_negative() -> None:
    """The claim this table exists to make, from a fixture with both signs.

    ``delta`` is signed. A rollup storing only the net could not answer "how many
    points did we issue this month" — issuance and redemption would be one
    number, and a busy month that nets to zero would be indistinguishable from a
    dormant one. So both counters are asserted non-negative, asserted to move
    independently, and the day's issuance is asserted to be unrecoverable from
    its net.
    """
    with _sandbox() as db:
        generation = active_generation(db).generation
        _mixed_day(db, DAY_MIX)
        _runner(db).run_bucket(JOB, DAY_MIX)
        rows = _keyed(db, DAY_MIX, generation)

        for reason, row in rows.items():
            assert row.points_earned >= 0, f"{reason}: points_earned went negative"
            assert row.points_debited >= 0, f"{reason}: points_debited went negative"
            assert row.points_redeemed >= 0
            assert row.points_expired >= 0
            assert row.points_reversed >= 0
            assert row.points_earned - row.points_debited == row.net_points, (
                f"{reason}: the measured net must equal earned - debited. The job "
                "measures net independently precisely so this is a check rather "
                "than a restatement"
            )

        earns = rows["place_order"]
        assert (earns.points_earned, earns.points_debited) == (1200, 0)
        assert earns.net_points == 1200
        assert earns.transactions == 2
        assert earns.distinct_customers == 2

        spends = rows["redeem"]
        assert (spends.points_earned, spends.points_debited) == (0, 400)
        assert spends.points_redeemed == 400, (
            "the named subset carries the same non-negative figure as the general "
            "debit counter, so a window total needs no row filter"
        )
        assert spends.net_points == -400, (
            "net is SIGNED and is allowed to be negative; the two counters "
            "feeding it are not"
        )

        assert rows["expiry"].points_expired == 25
        assert rows["refund_reversal"].points_reversed == 200

        # The reason with BOTH signs. One net number could not describe it.
        adjust = rows["admin_adjust"]
        assert (adjust.points_earned, adjust.points_debited) == (40, 15)
        assert adjust.net_points == 25
        assert adjust.points_redeemed == adjust.points_expired == adjust.points_reversed == 0, (
            "the negative half of admin_adjust is inside points_debited and has no "
            "named subset of its own — it is a correction, not a programme mechanic"
        )

        # The whole day, as a window total would read it.
        issued = sum(row.points_earned for row in rows.values())
        debited = sum(row.points_debited for row in rows.values())
        net = sum(row.net_points for row in rows.values())
        assert (issued, debited, net) == (1290, 640, 650)
        assert issued != abs(net) and debited != abs(net), (
            "if issuance or redemption were recoverable from the net on this "
            "fixture the test would prove nothing; they are not, which is why "
            "both counters are stored"
        )
        assert sum(row.points_redeemed for row in rows.values()) == 400
        assert sum(row.points_expired for row in rows.values()) == 25

        assert rows[STORE_LEVEL_REASON].referral_completions == 2
        assert rows[STORE_LEVEL_REASON].points_earned == 0, (
            "store-level measures live on their own row and the reason counters "
            "are zero there, so a window SUM counts every figure exactly once"
        )


# ===========================================================================
# 5. The balance is a LEVEL
# ===========================================================================


def test_points_outstanding_close_is_a_level_and_is_never_summed() -> None:
    """A window's outstanding balance is its LATEST bucket, never the sum.

    Three consecutive days: earn, spend, silence. The correct answer for the
    whole window is the third day's balance. The sum of the three is the failure
    this test exists to make impossible — it succeeds, it is plausible, and it is
    wrong by roughly the number of days in range.
    """
    with _sandbox() as db:
        generation = active_generation(db).generation
        day_one, day_two, day_three = DAY_LEVEL
        user = _create_user(db)
        _award(
            db, user, delta=500, reason=PointsReason.PLACE_ORDER,
            created_at=_at(db, day_one, 10),
        )
        _award(
            db, user, delta=-200, reason=PointsReason.REDEEM,
            created_at=_at(db, day_two, 10),
        )
        db.commit()

        runner = _runner(db)
        for day in DAY_LEVEL:
            runner.run_bucket(JOB, day)

        closes = [
            _keyed(db, day, generation)[STORE_LEVEL_REASON].points_outstanding_close
            for day in DAY_LEVEL
        ]
        assert closes == [500, 300, 300], (
            "the balance is carried forward on a silent day because it is a "
            f"LEVEL, not a flow that resets; got {closes}"
        )

        latest = closes[-1]
        naive_sum = sum(closes)
        assert naive_sum == 1100 and latest == 300, (
            "the fixture must make the two answers differ, or this guard is "
            f"vacuous (sum={naive_sum}, latest={latest})"
        )

        # The classifier is what stops a resolver reaching for the sum.
        assert classify("points_outstanding_close", source=SOURCE) is MetricKind.LEVEL, (
            "the `_close` suffix is load-bearing: metric_kind reads it, and a "
            "column named `points_balance` or `outstanding_points` would classify "
            "as a FLOW and be summed"
        )
        with pytest.raises(NonAdditive):
            assert_summable("points_outstanding_close", source=SOURCE)

        # And the view must not have quietly bound it anyway.
        view = registry.get_view("customers", "loyalty-and-rewards")
        assert view is not None
        bound = {
            column
            for spec in (view.params.get("metrics") or {}).values()
            for column in ([spec] if isinstance(spec, str) else spec.get("add") or ())
        }
        assert "points_outstanding_close" not in bound, (
            "every params column is SUMmed — over the window for a card and over "
            "the bucket for a series — so binding the level here would report one "
            f"balance per day added together instead of {latest}"
        )
        assert "distinct_customers" not in bound, (
            "a distinct count cannot be combined in EITHER direction; a weekly "
            "figure has to be recomputed from the ledger"
        )
        assert classify("distinct_customers", source=SOURCE) is MetricKind.DISTINCT


def test_every_flow_column_classifies_as_a_flow() -> None:
    """The columns the view DOES sum must all be additive, and be known to be.

    The mirror of the test above: `metric_kind` is name-based, so a flow that
    accidentally acquired a level-ish suffix would be silently refused at query
    time and its chart would go blank with no explanation.
    """
    flows = {
        "points_earned",
        "points_debited",
        "points_redeemed",
        "points_expired",
        "points_reversed",
        "net_points",
        "transactions",
        "referral_completions",
    }
    for column in flows:
        assert classify(column, source=SOURCE) is MetricKind.FLOW, column
        assert_summable(column, source=SOURCE)
    assert flows | {"distinct_customers", "points_outstanding_close"} == set(
        measures_for(SOURCE)
    ), (
        "every measure of this table is classified above. A new column added "
        "without deciding whether it is a flow, a level or a distinct count is "
        "exactly how a level ends up summed."
    )


# ===========================================================================
# 6. View 52, end to end
# ===========================================================================


class _AnalyticsReader:
    """Holds exactly the permission view 52 needs, and nothing else.

    ``AnalyticsViewService`` only ever calls ``has_permission``, so this
    exercises the real authorisation path without creating a user row — and
    without reaching for the shared admin account, whose ``is_admin`` short
    circuit would make the check pass for the wrong reason.
    """

    is_admin = False

    def __init__(self, permissions: set[str]) -> None:
        self._permissions = permissions

    def has_permission(self, permission: str) -> bool:
        return permission in self._permissions


@contextmanager
def _view_sandbox() -> Iterator[tuple[Session, int]]:
    """A session plus three seeded March 1999 buckets, deleted unconditionally.

    The generation is the database's ACTIVE one rather than a private value,
    because ``AnalyticsViewService`` reads it from the database and cannot be
    told otherwise. Isolation comes from the date range instead.

    Rows are written directly rather than through the job: this test is about
    the registry binding and the resolver, and seeding the table makes the
    expected numbers visible in one place.
    """
    _reset_sandbox()
    db = SessionLocal()
    generation = int(active_generation(db).generation)
    try:
        for index, day in enumerate(DAY_VIEW):
            db.add(
                AggLoyaltyDaily(
                    bucket_date=day,
                    reason="place_order",
                    tz_generation=generation,
                    points_earned=100 * (index + 1),
                    net_points=100 * (index + 1),
                    transactions=index + 1,
                    distinct_customers=index + 1,
                )
            )
            db.add(
                AggLoyaltyDaily(
                    bucket_date=day,
                    reason="redeem",
                    tz_generation=generation,
                    points_debited=30,
                    points_redeemed=30,
                    net_points=-30,
                    transactions=1,
                    distinct_customers=1,
                )
            )
            db.add(
                AggLoyaltyDaily(
                    bucket_date=day,
                    reason="expiry",
                    tz_generation=generation,
                    points_debited=5,
                    points_expired=5,
                    net_points=-5,
                    transactions=1,
                    distinct_customers=1,
                )
            )
            db.add(
                AggLoyaltyDaily(
                    bucket_date=day,
                    reason=DIMENSION_UNKNOWN,
                    tz_generation=generation,
                    referral_completions=2,
                    points_outstanding_close=1000 + index,
                )
            )
        db.commit()
        yield db, generation
    finally:
        try:
            db.rollback()
        finally:
            db.close()
            _reset_sandbox()


def _view_filters() -> AnalyticsFilters:
    return AnalyticsFilters(
        period=Period.CUSTOM,
        date_from=DAY_VIEW[0],
        date_to=VIEW_WINDOW_END,
        comparison=Comparison.NONE,
    )


def test_view_52_resolves_end_to_end_with_real_values() -> None:
    """View 52 answers with the seeded numbers, through the real service.

    Asserted through ``AnalyticsViewService`` rather than the resolver directly,
    so permissions, gating, the registry binding and envelope construction are
    all in the path. ``sources`` is the machine-readable half of the answer:
    ``not_configured`` returns an explicitly EMPTY list, so a non-empty one is
    the difference between "nothing is wired up" and "here is the number".
    """
    with _view_sandbox() as (db, _generation):
        view = registry.get_view("customers", "loyalty-and-rewards")
        assert view is not None and view.number == 52
        assert view.state is ViewState.LIVE

        service = AnalyticsViewService(db, _AnalyticsReader({view.permission}))
        envelope = service.resolve_view(
            "customers", "loyalty-and-rewards", _view_filters(), use_cache=False
        )

        assert isinstance(envelope, AnalyticsViewEnvelope), (
            "view 52 is not gated and must resolve rather than return the gated "
            "envelope"
        )
        assert SOURCE in {ref.id for ref in envelope.sources}, (
            f"the view must report which rollup it read; got {envelope.sources}"
        )
        assert NOT_CONFIGURED not in {w.code for w in envelope.warnings}, "; ".join(
            w.message for w in envelope.warnings if w.code == NOT_CONFIGURED
        )

        points = envelope.series["points_flow"]
        assert [p["date"] for p in points] == [d.isoformat() for d in DAY_VIEW], (
            "three seeded days, densified only inside the watermark"
        )
        assert [p["points_issued"] for p in points] == [
            Decimal("100"),
            Decimal("200"),
            Decimal("300"),
        ]
        assert [p["points_redeemed"] for p in points] == [Decimal("30")] * 3
        assert [p["points_expired"] for p in points] == [Decimal("5")] * 3

        referrals = envelope.series["referral_completions"]
        assert [p["referral_completions"] for p in referrals] == [Decimal("2")] * 3, (
            "the store-level row's count reaches the chart once per day — it is "
            "on one row per bucket, so summing the bucket cannot multiply it"
        )

        # The chart series must not be among anything reported unbound. The KPI
        # cards are a separate conversation: `repeat_purchase_rate` has no
        # binding anywhere and is correctly reported as unavailable, not zero.
        unbound = {
            metric
            for w in envelope.warnings
            if w.code == METRIC_NOT_BOUND
            for metric in (w.detail.get("metrics") or ())
        }
        assert not unbound & {
            "points_issued",
            "points_redeemed",
            "points_expired",
            "referral_completions",
        }, f"chart series reported unbound: {sorted(unbound)}"

        repeat = envelope.kpis["repeat_purchase_rate"]
        assert repeat.value is None and repeat.inputs_missing, (
            "a lifetime repeat rate needs a customer population no rollup stores; "
            "it must stay null and NAME what is missing rather than read as 0%"
        )


def test_view_52_binding_only_names_real_summable_columns() -> None:
    """The registry binding is validated against the repository's allowlist.

    The same guard ``test_analytics_view_bindings.py`` applies to every bound
    view, restated here so a change to view 52 alone fails in this file too.
    """
    view = registry.get_view("customers", "loyalty-and-rewards")
    assert view is not None
    assert view.params.get("source") == SOURCE
    assert SOURCE in known_sources(), (
        "the model must be registered in `analytics_repository._build_registry`, "
        "or the view's source is not a table the repository can read"
    )

    available = set(columns_for(SOURCE))
    summable = set(measures_for(SOURCE))
    for metric_id, spec in view.params["metrics"].items():
        columns = [spec] if isinstance(spec, str) else list(spec.get("add") or ())
        assert columns, metric_id
        for column in columns:
            assert column in available, f"{metric_id} -> {column}"
            assert column in summable, f"{metric_id} -> {column} is not summable"

    # Every chart series the view declares has a binding, or the chart is blank.
    declared = set(view.params["metrics"])
    for chart in view.charts:
        assert set(chart.series) <= declared, (
            f"chart {chart.id!r} declares series {sorted(set(chart.series) - declared)} "
            "with no binding, so it would render an axis and no line"
        )


# ===========================================================================
# 7. Schema reflection, and the migration/twin agreement
# ===========================================================================

#: One `col TYPE ...` line out of the twin's CREATE TABLE body.
_SQL_COLUMN = re.compile(r"^\s{4}([a-z_]+)\s+([A-Z]+(?:\([^)]*\))?)(.*?),?$")


def _twin_sql() -> str:
    return TWIN_SQL.read_text(encoding="utf-8")


def _twin_columns() -> dict[str, str]:
    """`column -> the rest of its DDL line`, parsed out of the twin SQL."""
    body = _twin_sql().split("CREATE TABLE agg_loyalty_daily (", 1)[1].split("\n);", 1)[0]
    out: dict[str, str] = {}
    for line in body.splitlines():
        if line.strip().startswith(("PRIMARY KEY", "CONSTRAINT")):
            continue
        match = _SQL_COLUMN.match(line)
        if match:
            out[match.group(1)] = f"{match.group(2)}{match.group(3)}".strip()
    return out


class TestSchemaAndTwinDdl:
    """The live table matches the ORM, the conventions, and the twin SQL.

    Why the twin is checked against the DATABASE rather than against the
    revision's Python: this database was built by `alembic upgrade head`, and
    nothing else creates `agg_loyalty_daily`. So "the twin agrees with the live
    schema" and "the live schema is what the revision produced" together give
    "the twin agrees with the revision" — and it checks the property that
    actually matters, since CI runs the revision and production runs the twin.
    Re-executing alembic's offline renderer inside a test would assert the same
    thing more fragilely.
    """

    @pytest.fixture(scope="class")
    def inspector(self):
        with SessionLocal() as session:
            yield inspect(session.get_bind())

    def test_table_exists(self, inspector):
        assert inspector.has_table("agg_loyalty_daily"), (
            "run `alembic upgrade head` locally, or apply "
            "backend/scripts/sql/2026-07-29_agg_loyalty_daily.sql on the shared DB"
        )

    def test_columns_match_the_orm(self, inspector):
        table = AggLoyaltyDaily.__table__
        actual = {c["name"]: c for c in inspector.get_columns("agg_loyalty_daily")}
        assert set(table.columns.keys()) == set(actual), (
            f"ORM-only: {sorted(set(table.columns) - set(actual))}; "
            f"DB-only: {sorted(set(actual) - set(table.columns))}"
        )
        for column in table.columns:
            db_type = str(actual[column.name]["type"]).upper()
            orm_type = str(column.type).upper()
            assert _family(db_type) == _family(orm_type), (
                f"agg_loyalty_daily.{column.name}: ORM says {orm_type}, database "
                f"has {db_type}"
            )

    def test_no_float_columns(self):
        """Nothing here is money today, and nothing here may become a float.

        Binary floating point cannot represent 0.10, and a points balance that
        drifts is a balance nobody can reconcile against the ledger.
        """
        floats = [
            c.name
            for c in AggLoyaltyDaily.__table__.columns
            if any(k in str(c.type).upper() for k in ("FLOAT", "DOUBLE", "REAL"))
        ]
        assert not floats, floats

    def test_no_foreign_keys(self, inspector):
        """Convention 1: rollups stay independently truncatable and rebuildable.

        An FK to `users` would make "recompute March" a referential-integrity
        problem and would let deleting an account erase its points history.
        """
        assert not AggLoyaltyDaily.__table__.foreign_keys
        assert not inspector.get_foreign_keys("agg_loyalty_daily")

    def test_unique_key_includes_tz_generation(self):
        """Convention 2. Without it, two reporting timezones collide in one key."""
        uniques = [
            c
            for c in AggLoyaltyDaily.__table__.constraints
            if isinstance(c, UniqueConstraint)
        ]
        assert len(uniques) == 1, uniques
        assert [c.name for c in uniques[0].columns] == [
            "bucket_date",
            "reason",
            "tz_generation",
        ]

    def test_no_nullable_column_inside_the_unique_key(self):
        """Convention 3, and the subtlest.

        MySQL permits MANY rows with NULL in a UNIQUE-indexed column, so a
        nullable `reason` would not enforce one-row-per-(day, reason) at all.
        """
        offenders = [
            c.name
            for u in AggLoyaltyDaily.__table__.constraints
            if isinstance(u, UniqueConstraint)
            for c in u.columns
            if c.nullable
        ]
        assert not offenders, offenders

    def test_no_stored_averages(self):
        """Convention 4. Points per member is `net_points / distinct_customers`,
        recomputed at query time — and only ever within one bucket, because the
        denominator is a distinct count."""
        offenders = [
            c.name
            for c in AggLoyaltyDaily.__table__.columns
            if c.name.startswith("avg_") or c.name in {"aov", "average"}
        ]
        assert not offenders, offenders

    def test_indexes_present_in_the_database(self, inspector):
        declared = {ix.name for ix in AggLoyaltyDaily.__table__.indexes}
        actual = {ix["name"] for ix in inspector.get_indexes("agg_loyalty_daily")}
        assert declared <= actual, sorted(declared - actual)
        assert "ix_agg_loyalty_daily_bucket_date_reason" in actual

    def test_registered_for_alembic_autogenerate(self):
        """`app/db/base.py` must import the model, or autogenerate proposes a DROP."""
        assert "agg_loyalty_daily" in Base.metadata.tables

    def test_twin_sql_matches_the_live_schema(self, inspector):
        twin = _twin_columns()
        actual = {c["name"]: c for c in inspector.get_columns("agg_loyalty_daily")}
        assert set(twin) == set(actual), (
            "the hand-applied SQL and the migrated schema disagree on which "
            f"columns exist. twin-only: {sorted(set(twin) - set(actual))}; "
            f"db-only: {sorted(set(actual) - set(twin))}. Regenerate the twin: "
            f"alembic upgrade e1c5b7a04d92:{REVISION} --sql"
        )
        for name, ddl in twin.items():
            assert _family(ddl) == _family(str(actual[name]["type"]).upper()), (
                f"{name}: twin SQL says {ddl!r}, database has "
                f"{actual[name]['type']}"
            )
            assert "NOT NULL" in ddl, (
                f"{name} is nullable in the twin SQL; every column of this rollup "
                "is NOT NULL, and a nullable dimension defeats the UNIQUE key"
            )

    def test_twin_sql_declares_the_same_key_and_indexes(self, inspector):
        sql = _twin_sql()
        assert (
            "CONSTRAINT uq_agg_loyalty_daily_key UNIQUE "
            "(bucket_date, reason, tz_generation)" in sql
        ), "the twin must create the idempotency key, tz_generation included"
        actual = {ix["name"] for ix in inspector.get_indexes("agg_loyalty_daily")}
        for index in actual:
            assert index in sql, (
                f"{index} exists in the migrated database but the twin SQL does "
                "not create it, so production would run without it"
            )

    def test_twin_sql_does_not_stamp_alembic_version(self):
        """The stamp is stripped on purpose.

        The shared remote DB is on the `conpay001` lineage this repo does not
        contain (DEPLOY.md §6); stamping it with a revision id from this chain
        would corrupt its migration state.
        """
        sql = _twin_sql()
        assert "alembic_version" not in sql.split("-- Running upgrade", 1)[1], (
            "the generated UPDATE alembic_version statement must be removed"
        )
        assert REVISION in sql, (
            "the twin must name the revision it was generated from, or nobody can "
            "tell which one it mirrors"
        )


def _family(rendered: str) -> str:
    """Type family, so MySQL's DECIMAL/NUMERIC and INT widths do not false-alarm."""
    text_ = rendered.upper()
    if "BOOL" in text_ or "TINYINT" in text_:
        return "BOOL"
    for key in ("DECIMAL", "NUMERIC"):
        if key in text_:
            return "NUMERIC"
    for key in ("BIGINT", "SMALLINT", "INTEGER", "INT"):
        if key in text_:
            return "INT"
    for key in ("VARCHAR", "CHAR", "TEXT"):
        if key in text_:
            return "STRING"
    for key in ("DATETIME", "TIMESTAMP"):
        if key in text_:
            return "DATETIME"
    if "DATE" in text_:
        return "DATE"
    if "FLOAT" in text_ or "DOUBLE" in text_ or "REAL" in text_:
        return "FLOAT"
    return text_


# ===========================================================================
# 8. What this rollup deliberately cannot answer
# ===========================================================================


def test_no_tier_dimension_exists_anywhere() -> None:
    """Redemptions by tier is unanswered, not approximated. Asserted, not assumed.

    ``LoyaltyService.redeem`` writes the ledger row with ``ref_type='coupon'``
    and the minted coupon's id; neither ``points_transactions`` nor ``coupons``
    carries a ``redemption_tier_id``. The only surviving trace of the tier is a
    free-text description, and a dimension recovered by parsing it would
    re-partition every past month the first time a tier is renamed — while
    looking exactly like a measurement.

    This test fails the moment somebody adds a tier column here. That is the
    point: adding one is only honest once the transactional side captures the
    tier, and at that moment this test should be deleted along with the caveat
    in the model docstring.
    """
    columns = set(columns_for(SOURCE))
    assert not {c for c in columns if "tier" in c}, (
        f"agg_loyalty_daily grew a tier column ({sorted(c for c in columns if 'tier' in c)}). "
        "There is no redemption_tier_id on points_transactions or coupons to fill "
        "it from; capture one at redemption time first, forward-only."
    )
    from app.models.coupon import Coupon

    assert not {c.name for c in Coupon.__table__.columns if "tier" in c.name}, (
        "coupons now references a tier — the rollup can honestly gain a tier "
        "dimension, and this test plus the model's caveat should go"
    )
    assert not {
        c.name for c in PointsTransaction.__table__.columns if "tier" in c.name
    }, "points_transactions now references a tier — same conversation as above"


def test_the_debit_subsets_cover_exactly_the_named_reasons() -> None:
    """The three named subsets are the three the model documents, and no more.

    ``admin_adjust`` is deliberately absent: its negative half is inside
    ``points_debited`` and has no column of its own, because it is a correction
    rather than a programme mechanic and a column would invite it onto a chart
    as though it were one.
    """
    assert DEBIT_SUBSET_COLUMNS == {
        PointsReason.REDEEM: "points_redeemed",
        PointsReason.EXPIRY: "points_expired",
        PointsReason.REFUND_REVERSAL: "points_reversed",
    }
    for column in DEBIT_SUBSET_COLUMNS.values():
        assert column in columns_for(SOURCE)
    assert STORE_LEVEL_REASON == DIMENSION_UNKNOWN, (
        "the store-level row uses the '-' sentinel, which is unambiguous only "
        "because points_transactions.reason is NOT NULL"
    )
