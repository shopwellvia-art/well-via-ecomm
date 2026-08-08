"""Tests for the anomaly detector and the alerts engine.

What actually has to be true here
---------------------------------
Almost none of these tests are about whether a deviation was measured
accurately. An alert that is 8% out is still actionable. The tests carrying the
weight are the ones about **not firing**, because the failure mode of an
alerting system is not a missed incident — it is being muted, after which it
detects nothing at all, forever:

  * ``test_a_rule_without_a_baseline_does_not_fire_and_records_why`` — five days
    of history against a 28-day minimum must produce a *reason*, not a quieter
    alert. "Sales dropped 40%" off three days of history is arithmetic, and it
    renders identically to the real thing.
  * ``test_a_volatile_weekend_is_inside_the_band_not_outside_it`` — the specific
    noise this design exists to avoid. A store whose Saturdays run 1.3x average
    must not be told every Saturday that revenue spiked; the day-of-week factor
    belongs in the *band*, which is why the expected range comes from the
    forecast engine and not from a percentage of last week.
  * ``test_no_cost_rules_raises_missing_cost_data_and_never_negative_cm3`` — the
    asymmetry that matters most. NEGATIVE_CM3 asserts a business fact; raising
    it from an absent number is an accusation with no evidence behind it.
  * ``test_re_running_the_same_bucket_does_not_duplicate_the_alert`` — an alert
    list that repeats itself is unreadable within a week, and unreadable has the
    same outcome as muted.
  * ``test_a_rule_whose_source_has_no_rows_is_skipped_not_fired`` — an empty
    rollup is a pipeline state. Reading it as "revenue was zero" would be the
    largest sales drop the store has ever had.

Isolation strategy
------------------
The detection maths is pure — ``{date: value}`` in, an expected range out — so
most of this file needs no database at all. Testing an interval through MySQL
would be slower, flakier, and would prove less.

The database half follows the house pattern:

  a) every fixture lives in **2004**, a sandbox no other suite uses (2005, 2007,
     2008, 2009, 2011, 2012 and 2013 are taken) and decades before this store's
     first order, so assertions are absolute rather than deltas;
  b) each test writes rollups under its **own random ``tz_generation``**
     (21000-21999 — 1000-20999, 22000-30999 and 31000+ belong to other suites);
  c) ``analytics_alerts`` has no ``tz_generation`` column, so isolation there is
     by **bucket date**: every test owns a distinct day in 2004 and the sandbox
     purges that day on the way in and on the way out;
  d) there is no conftest DB fixture. Each test owns its ``SessionLocal()`` and
     tears down in ``finally``, through a fresh session so teardown cannot fail
     because of a half-rolled-back transaction;
  e) ``CostRuleResolver`` caches resolutions in Redis for 60s **including
     misses**, and the cost tests turn on whether a rule is absent. Every test
     passes its own in-process fake, so one test's cached miss can never answer
     another's question.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterator

import pytest
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.analytics_control import (
    AlertRuleKey,
    AlertSeverity,
    AlertStatus,
    AnalyticsAlert,
    AnalyticsCostRule,
    CostQuality,
    CostScope,
    CostType,
    CostUnit,
)
from app.models.analytics_rollups import AggInventoryDaily, AggOrderDaily
from app.services.analytics import anomalies
from app.services.analytics.anomalies import (
    ExpectedRange,
    RangeBasis,
    SkipReason,
    acknowledge,
    days_of_cover,
    detect,
    deviation_ratio,
    forecast_range,
    mute,
    resolve,
    severity_for,
)

# ===========================================================================
# The pure fixtures
# ===========================================================================

#: A Monday, so a whole number of weeks starts and ends cleanly and the weekday
#: a one-step-ahead horizon lands on is predictable by inspection.
PURE_START = date(2004, 3, 1)

#: Mon..Sun. Averages exactly 1.0, so it can be applied to a flat level without
#: also shifting it. Saturday at 1.3x is the volatility this whole design is
#: about: it has to end up in the band, not in the alert list.
WEEKLY: tuple[float, ...] = (0.80, 0.90, 1.00, 1.00, 1.10, 1.30, 0.90)

LEVEL = 10_000.0


def _noise(i: int) -> float:
    """Deterministic pseudo-noise, ~±0.2%. A seeded RNG would be a second thing
    to trust; a coprime modular walk is reproducible by inspection."""
    return ((317 * i) % 41) - 20


def _seasonal_history(
    days: int, *, start: date = PURE_START, level: float = LEVEL
) -> dict[date, float]:
    return {
        start + timedelta(days=i): level * WEEKLY[(start + timedelta(days=i)).weekday()]
        + _noise(i)
        for i in range(days)
    }


def _flat_history(days: int, value: float, *, start: date = PURE_START) -> dict[date, float]:
    return {start + timedelta(days=i): value for i in range(days)}


# ===========================================================================
# 1. A rule with no baseline does not fire, and says why
# ===========================================================================


def test_a_rule_without_a_baseline_does_not_fire_and_records_why():
    """Five days against a 28-day minimum yields a reason, not an estimate.

    The refusal carries no range of any kind, so there is nothing a caller could
    accidentally grade a value against. That is stronger than returning a wide
    band: a wide band still fires eventually, and it fires on a store whose
    entire history is one unusual week.
    """
    history = _seasonal_history(5)
    refusal = forecast_range(history, PURE_START + timedelta(days=5), min_days=28)

    assert not isinstance(refusal, ExpectedRange)
    reason, message, detail = refusal
    assert reason == SkipReason.INSUFFICIENT_BASELINE
    assert detail["observed_days"] == 5
    assert detail["required_days"] == 28
    assert detail["short_by_days"] == 23
    # The message has to be usable by whoever reads the skip, not just parseable.
    assert "28" in message and "5" in message


def test_an_empty_baseline_is_no_history_not_a_history_of_zeroes():
    reason, message, detail = forecast_range({}, PURE_START, min_days=28)
    assert reason == SkipReason.NO_SOURCE_ROWS
    assert detail["observed_days"] == 0
    assert "not measured" in message


def test_a_baseline_of_all_zeroes_is_degenerate_rather_than_certain():
    """A perfectly flat zero history produces no scale, so nothing is graded.

    Without this branch the expected range is [0, 0] and every non-zero value is
    infinitely far outside it — every rule would fire CRITICAL on the store's
    first refund, its first RTO and its first failed payment.
    """
    reason, message, _ = forecast_range(
        _flat_history(40, 0.0), PURE_START + timedelta(days=40), min_days=28
    )
    assert reason == SkipReason.DEGENERATE_BASELINE
    assert "no width" in message


def test_a_stale_baseline_is_refused_rather_than_extrapolated():
    """A baseline that stops three weeks ago is not a baseline for today."""
    history = _seasonal_history(40)
    stale_target = PURE_START + timedelta(days=70)
    reason, _, detail = forecast_range(history, stale_target, min_days=28)
    assert reason == SkipReason.BASELINE_GAP_TOO_WIDE
    assert detail["gap_days"] > anomalies.MAX_BASELINE_GAP_DAYS


def test_the_judged_day_may_never_appear_in_its_own_baseline():
    """A bucket in its own baseline widens the band by its own deviation.

    That is the one direction of error that hides the thing being looked for, so
    it raises rather than returning a wider range.
    """
    history = _seasonal_history(40)
    target = PURE_START + timedelta(days=10)
    assert target in history
    with pytest.raises(ValueError, match="inside its own baseline"):
        forecast_range(history, target, min_days=28)


# ===========================================================================
# 2 & 3. The band: a real break fires, ordinary volatility does not
# ===========================================================================


def test_a_genuine_collapse_lands_outside_the_band_with_the_band_reported():
    history = _seasonal_history(42)
    target = PURE_START + timedelta(days=42)

    expected = forecast_range(history, target, min_days=28)
    assert isinstance(expected, ExpectedRange)
    assert expected.basis == RangeBasis.FORECAST_INTERVAL
    # The interval machinery is the forecast engine's, not a second one.
    assert expected.detail["method_id"] == "ma_trend_dow_v1"
    assert expected.detail["day_of_week_factor_applied"] is True
    assert expected.low < expected.high

    collapsed = Decimal("500")
    ratio = deviation_ratio(collapsed, expected)
    assert ratio > 1
    assert severity_for(ratio) == AlertSeverity.CRITICAL


def test_a_volatile_weekend_is_inside_the_band_not_outside_it():
    """The noise this design exists to avoid.

    Saturday runs 1.3x this store's average day. A rule comparing against "the
    last 7 days" or "30% below last week" pages someone every Saturday and every
    Monday; after three weekends nobody reads the channel, and the detector has
    achieved nothing at all. The weekly shape belongs inside the expected range,
    which is the entire reason the range comes from the forecast engine.
    """
    history = _seasonal_history(42)
    # The next day after 42 whole weeks... land the target on a Saturday.
    target = PURE_START + timedelta(days=42)
    while target.weekday() != 5:
        target += timedelta(days=1)
    history = {d: v for d, v in history.items()}

    expected = forecast_range(history, target, min_days=28)
    assert isinstance(expected, ExpectedRange)

    ordinary_saturday = Decimal(str(LEVEL * WEEKLY[5]))
    assert deviation_ratio(ordinary_saturday, expected) == 0, (
        f"an ordinary Saturday ({ordinary_saturday}) fell outside "
        f"[{expected.low}, {expected.high}] — the day-of-week factor is not in "
        "the band, so this rule would fire every weekend"
    )

    # And an ordinary *Monday* value on that Saturday IS a drop: the band is
    # weekday-aware in both directions, not merely wide.
    ordinary_monday = Decimal(str(LEVEL * WEEKLY[0]))
    assert deviation_ratio(ordinary_monday, expected) > 0


def test_a_typical_day_inside_the_band_produces_no_alert_at_all():
    history = _seasonal_history(42)
    target = PURE_START + timedelta(days=42)
    expected = forecast_range(history, target, min_days=28)
    assert isinstance(expected, ExpectedRange)

    typical = Decimal(str(LEVEL * WEEKLY[target.weekday()]))
    assert deviation_ratio(typical, expected) == 0
    with pytest.raises(ValueError, match="inside its expected range"):
        severity_for(Decimal("0"))


# ===========================================================================
# 6. Severity is graded by distance, not fixed per rule
# ===========================================================================


def test_severity_scales_with_distance_outside_the_range():
    """One ladder, measured in half-widths of the range itself.

    Measuring in half-widths rather than in rupees or percentage points is what
    lets one ladder mean the same thing for a currency total, a conversion rate
    and a count of days — and what makes a volatile store's WARNING as
    meaningful as a steady store's.
    """
    band = ExpectedRange(
        low=Decimal("100"),
        high=Decimal("200"),
        half_width=Decimal("50"),
        basis=RangeBasis.FORECAST_INTERVAL,
    )
    assert deviation_ratio(Decimal("150"), band) == 0
    assert severity_for(deviation_ratio(Decimal("90"), band)) == AlertSeverity.INFO
    assert severity_for(deviation_ratio(Decimal("60"), band)) == AlertSeverity.WARNING
    assert severity_for(deviation_ratio(Decimal("0"), band)) == AlertSeverity.CRITICAL

    # Monotonic: further out is never quieter.
    ladder = [
        severity_for(deviation_ratio(Decimal(str(v)), band))
        for v in (95, 80, 40, 0)
    ]
    ranks = [anomalies.SEVERITY_ORDER.index(s) for s in ladder]
    assert ranks == sorted(ranks)

    # The same distance on the other side grades identically — a spike of a
    # given size is as loud as a drop of that size.
    assert severity_for(deviation_ratio(Decimal("310"), band)) == severity_for(
        deviation_ratio(Decimal("-10"), band)
    )


def test_a_severity_floor_raises_a_grade_and_never_caps_one():
    band = ExpectedRange(
        low=Decimal("0"),
        high=None,
        half_width=Decimal("100"),
        basis=RangeBasis.DEFINITIONAL,
    )
    shallow = deviation_ratio(Decimal("-10"), band)  # 0.1 half-widths -> INFO
    assert severity_for(shallow) == AlertSeverity.INFO
    assert severity_for(shallow, floor=AlertSeverity.CRITICAL) == AlertSeverity.CRITICAL
    deep = deviation_ratio(Decimal("-500"), band)
    assert severity_for(deep, floor=AlertSeverity.INFO) == AlertSeverity.CRITICAL


def test_a_range_with_no_scale_is_refused_at_construction():
    """A zero half-width would make every value infinitely anomalous."""
    with pytest.raises(ValueError, match="half_width must be > 0"):
        ExpectedRange(
            low=Decimal("0"),
            high=Decimal("0"),
            half_width=Decimal("0"),
            basis=RangeBasis.DEFINITIONAL,
        )


def test_stock_cover_is_undefined_rather_than_infinite_when_nothing_sells():
    """None, not 999. A product with no sales has no runway to run out of, and
    a large number sorts and charts as a measurement."""
    assert days_of_cover(50, Decimal("0")) is None
    assert days_of_cover(50, Decimal("10")) == Decimal("5.0000")


# ===========================================================================
# The database half
# ===========================================================================

#: 2004 belongs to this suite. Each test owns one bucket day inside it, because
#: `analytics_alerts` has no tz_generation column and dedup keys on the date.
INSUFFICIENT_BUCKET = date(2004, 2, 7)
DROP_BUCKET = date(2004, 4, 12)
NORMAL_BUCKET = date(2004, 6, 14)
COST_BUCKET = date(2004, 7, 5)
DEDUP_BUCKET = date(2004, 9, 13)
LIFECYCLE_BUCKET = date(2004, 11, 15)
EMPTY_BUCKET = date(2004, 12, 6)
STOCK_BUCKET = date(2004, 12, 20)
MARKETING_BUCKET = date(2004, 5, 17)

SANDBOX_FROM = date(2004, 1, 1)
SANDBOX_TO = date(2004, 12, 31)

#: Stamped on every cost rule this suite writes, so teardown deletes exactly
#: ours and `_assert_no_foreign_cost_rules` can tell a leftover of ours from a
#: rule another suite left covering 2004.
RULE_SOURCE = "test_analytics_anomalies"

NOW = datetime(2026, 7, 28, 6, 0, tzinfo=timezone.utc)


class _FakeRedis:
    """In-process stand-in with per-instance state.

    Per-instance matters more here than anywhere: ``CostRuleResolver`` caches
    *misses*, and the cost tests turn entirely on a rule being absent. A shared
    cache would let one test's "no rule here" answer another test's question.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key: str):
        return self.store.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.store[key] = value

    def delete(self, key: str) -> None:
        self.store.pop(key, None)

    def scan_iter(self, match: str = "*", count: int = 100):
        prefix = match.rstrip("*")
        return [k for k in list(self.store) if k.startswith(prefix)]


def _purge(db: Session, generation: int, buckets: tuple[date, ...]) -> None:
    db.execute(
        delete(AggOrderDaily).where(AggOrderDaily.tz_generation == generation)
    )
    db.execute(
        delete(AggInventoryDaily).where(AggInventoryDaily.tz_generation == generation)
    )
    db.execute(delete(AnalyticsAlert).where(AnalyticsAlert.bucket_date.in_(buckets)))
    db.execute(delete(AnalyticsCostRule).where(AnalyticsCostRule.source == RULE_SOURCE))
    db.commit()


def _assert_no_foreign_cost_rules(db: Session) -> None:
    """Fail loudly if a rule written elsewhere covers the 2004 sandbox.

    A stray open-ended rule would silently satisfy the cost cascade and turn the
    MISSING_COST_DATA test green for the wrong reason — which is the one
    outcome worse than it failing.
    """
    intruders = db.execute(
        select(AnalyticsCostRule.id, AnalyticsCostRule.cost_type).where(
            AnalyticsCostRule.effective_from <= SANDBOX_TO,
            or_(
                AnalyticsCostRule.effective_to.is_(None),
                AnalyticsCostRule.effective_to >= SANDBOX_FROM,
            ),
        )
    ).all()
    assert not intruders, (
        f"cost rules from outside this suite cover the 2004 sandbox: {intruders}. "
        "Every expected figure below assumes no rule applies."
    )


@contextmanager
def sandbox(*buckets: date) -> Iterator[tuple[Session, int, _FakeRedis]]:
    """A session, a private tz_generation, a private cache, and clean 2004 days."""
    db = SessionLocal()
    # SMALLINT keyspace shared with every suite: 1000-20999 is
    # test_analytics_resolvers.py, 22000-30999 test_analytics_level_resolver.py,
    # 31000+ test_analytics_forecasting.py. This suite takes 21000-21999.
    generation = 21000 + (uuid.uuid4().int % 1000)
    try:
        _purge(db, generation, buckets)
        _assert_no_foreign_cost_rules(db)
        yield db, generation, _FakeRedis()
    finally:
        try:
            db.rollback()
        finally:
            db.close()
        cleanup = SessionLocal()
        try:
            _purge(cleanup, generation, buckets)
        finally:
            cleanup.close()


def _seed_orders(
    db: Session,
    generation: int,
    *,
    bucket: date,
    days: int,
    level: float = LEVEL,
    bucket_value: float | None = None,
    include_bucket: bool = True,
) -> None:
    """``days`` of seasonal order history ending the day before ``bucket``.

    The bucket row is written separately so a test can make the judged day a
    collapse, an ordinary day, or absent entirely — the three cases the detector
    has to tell apart.
    """
    start = bucket - timedelta(days=days)
    for i in range(days):
        day = start + timedelta(days=i)
        revenue = Decimal(str(round(level * WEEKLY[day.weekday()] + _noise(i), 2)))
        db.add(
            AggOrderDaily(
                bucket_date=day,
                tz_generation=generation,
                net_revenue=revenue,
                gross_merchandise_sales=revenue,
                orders_total=20,
                orders_paid=20,
            )
        )
    if include_bucket:
        value = (
            bucket_value
            if bucket_value is not None
            else level * WEEKLY[bucket.weekday()]
        )
        db.add(
            AggOrderDaily(
                bucket_date=bucket,
                tz_generation=generation,
                net_revenue=Decimal(str(round(value, 2))),
                gross_merchandise_sales=Decimal(str(round(value, 2))),
                orders_total=20,
                orders_paid=20,
            )
        )
    db.commit()


def _marketing_rule(
    db: Session, *, value: str, effective_from: date, effective_to: date
) -> None:
    """A blended monthly ad budget, effective-dated the way a real one is.

    PER_MONTH, because that is the only shape the store can express without an
    ad-platform connection — and the reason the marketing rule is the one cost
    that accrues on a day with no orders at all.
    """
    db.add(
        AnalyticsCostRule(
            cost_type=CostType.MARKETING_SPEND,
            scope=CostScope.GLOBAL,
            scope_value="-",
            value=Decimal(value),
            unit=CostUnit.PER_MONTH,
            currency="INR",
            quality=CostQuality.ASSUMED,
            effective_from=effective_from,
            effective_to=effective_to,
            source=RULE_SOURCE,
        )
    )


def _alert_rows(db: Session, bucket: date, rule_key: str) -> list[AnalyticsAlert]:
    return list(
        db.execute(
            select(AnalyticsAlert).where(
                AnalyticsAlert.bucket_date == bucket,
                AnalyticsAlert.rule_key == rule_key,
            )
        ).scalars()
    )


# ---------------------------------------------------------------------------
# 1. Insufficient baseline, through the real detector
# ---------------------------------------------------------------------------


def test_a_thin_history_skips_the_rule_and_the_skip_explains_itself():
    """Five days of rollup, a real bucket, and no alert — with the reason.

    The skip is the product here. Without it, an operator looking at an empty
    alert list on a store that just lost half its revenue has no way to tell
    "checked, normal" from "never ran".
    """
    with sandbox(INSUFFICIENT_BUCKET) as (db, generation, cache):
        _seed_orders(
            db,
            generation,
            bucket=INSUFFICIENT_BUCKET,
            days=5,
            bucket_value=100.0,  # a catastrophic collapse, on purpose
        )

        run = anomalies.run(
            db,
            INSUFFICIENT_BUCKET,
            tz_generation=generation,
            now=NOW,
            redis_client=cache,
        )

        assert not run.fired(AlertRuleKey.SALES_DROP)
        skip = run.skip_for(AlertRuleKey.SALES_DROP)
        assert skip is not None
        assert skip.reason == SkipReason.INSUFFICIENT_BASELINE
        assert skip.detail["observed_days"] == 5
        assert skip.detail["required_days"] == anomalies.RULES[
            AlertRuleKey.SALES_DROP
        ].min_baseline_days
        assert "28" in skip.message

        # Nothing was written for it, either. A skipped rule leaves no row.
        assert _alert_rows(db, INSUFFICIENT_BUCKET, AlertRuleKey.SALES_DROP) == []


# ---------------------------------------------------------------------------
# 2. A genuine drop fires, with everything a reader needs
# ---------------------------------------------------------------------------


def test_a_genuine_sales_drop_fires_with_the_band_and_the_actual_recorded():
    with sandbox(DROP_BUCKET) as (db, generation, cache):
        _seed_orders(
            db, generation, bucket=DROP_BUCKET, days=42, bucket_value=500.0
        )

        run = anomalies.run(
            db, DROP_BUCKET, tz_generation=generation, now=NOW, redis_client=cache
        )
        db.commit()

        assert run.fired(AlertRuleKey.SALES_DROP)
        alert = run.alerts_for(AlertRuleKey.SALES_DROP)[0]

        # An alert that records only "sales dropped" is not actionable.
        assert alert.actual_value == Decimal("500.0000")
        assert alert.expected_low is not None
        assert alert.expected_high is not None
        assert alert.expected_low > alert.actual_value
        assert alert.expected_low < alert.expected_high
        assert alert.bucket_date == DROP_BUCKET
        assert alert.metric == "net_revenue"
        assert alert.status == AlertStatus.OPEN
        assert alert.severity == AlertSeverity.CRITICAL

        evidence = alert.context["evidence"]
        assert evidence["range_basis"] == RangeBasis.FORECAST_INTERVAL
        assert evidence["range"]["method_id"] == "ma_trend_dow_v1"
        assert evidence["range"]["baseline_observed_days"] == 42
        assert Decimal(evidence["deviation_ratio"]) > anomalies.SEVERITY_WARNING_MAX
        # The alert carries the reason its own minimum exists, so "why did this
        # need 28 days?" never requires reading the source.
        assert "four complete weeks" in evidence["baseline_rationale"]

        # The same band, read from the other side, did NOT also fire.
        assert not run.fired(AlertRuleKey.REVENUE_SPIKE)


# ---------------------------------------------------------------------------
# 3. Ordinary volatility does not fire
# ---------------------------------------------------------------------------


def test_an_ordinary_day_inside_the_band_fires_nothing_and_says_it_was_checked():
    with sandbox(NORMAL_BUCKET) as (db, generation, cache):
        _seed_orders(db, generation, bucket=NORMAL_BUCKET, days=42)

        run = anomalies.run(
            db, NORMAL_BUCKET, tz_generation=generation, now=NOW, redis_client=cache
        )
        db.commit()

        assert not run.fired(AlertRuleKey.SALES_DROP)
        assert not run.fired(AlertRuleKey.REVENUE_SPIKE)
        assert _alert_rows(db, NORMAL_BUCKET, AlertRuleKey.SALES_DROP) == []

        # "Checked and clean" is a different state from "could not check", and
        # both render as an absent alert. The run says which.
        clear = run.clear_for(AlertRuleKey.SALES_DROP)
        assert clear is not None
        assert clear.expected.low <= clear.actual <= clear.expected.high
        assert run.skip_for(AlertRuleKey.SALES_DROP) is None

        # A history of zero refunds is not evidence that today's refund is
        # normal — it is no scale at all, so the rule is skipped, not cleared.
        refunds = run.skip_for(AlertRuleKey.REFUND_SPIKE)
        assert refunds is not None
        assert refunds.reason == SkipReason.DEGENERATE_BASELINE


# ---------------------------------------------------------------------------
# 4. A missing input is a configuration alert, never a business claim
# ---------------------------------------------------------------------------


def test_no_cost_rules_raises_missing_cost_data_and_never_negative_cm3():
    """The asymmetry this engine most has to get right.

    With no cost rules configured, contribution margin is *unknown*. Reporting
    it as negative would be asserting that every order lost money, from a number
    nobody has. MarginService already refuses to produce CM3 when an input is
    unresolved, so this is enforced by construction and not by an ``if`` that
    could be edited away.
    """
    with sandbox(COST_BUCKET) as (db, generation, cache):
        db.add(
            AggOrderDaily(
                bucket_date=COST_BUCKET,
                tz_generation=generation,
                net_revenue=Decimal("12000.00"),
                gross_merchandise_sales=Decimal("12000.00"),
                orders_total=25,
                orders_paid=25,
            )
        )
        db.commit()

        run = anomalies.run(
            db, COST_BUCKET, tz_generation=generation, now=NOW, redis_client=cache
        )
        db.commit()

        assert run.fired(AlertRuleKey.MISSING_COST_DATA)
        alert = run.alerts_for(AlertRuleKey.MISSING_COST_DATA)[0]
        assert "marketing_spend" in alert.context["evidence"]["missing_inputs"]
        assert alert.actual_value == Decimal("1.0000")
        assert alert.expected_high == Decimal("0.0000")

        # The whole point, asserted explicitly and from both directions.
        assert not run.fired(AlertRuleKey.NEGATIVE_CM3)
        assert _alert_rows(db, COST_BUCKET, AlertRuleKey.NEGATIVE_CM3) == []
        cm3_skip = run.skip_for(AlertRuleKey.NEGATIVE_CM3)
        assert cm3_skip is not None
        assert cm3_skip.reason == SkipReason.INPUT_NOT_CONFIGURED
        assert "marketing_spend" in cm3_skip.detail["missing_inputs"]

        # And the marketing-ratio rule declines too: an unconfigured cost is a
        # configuration problem, not a marketing one.
        marketing = run.skip_for(AlertRuleKey.MARKETING_COST_SPIKE)
        assert marketing is not None
        assert marketing.reason == SkipReason.INPUT_NOT_CONFIGURED


def test_a_day_the_store_did_not_trade_owes_no_cost_configuration_alert():
    """Marketing accrues on a dead day, but alerting on it every day would mute
    the channel before the store ever launched."""
    with sandbox(EMPTY_BUCKET) as (db, generation, cache):
        db.add(
            AggOrderDaily(
                bucket_date=EMPTY_BUCKET,
                tz_generation=generation,
                net_revenue=Decimal("0.00"),
                orders_total=0,
                orders_paid=0,
            )
        )
        db.commit()

        run = anomalies.run(
            db, EMPTY_BUCKET, tz_generation=generation, now=NOW, redis_client=cache
        )
        assert not run.fired(AlertRuleKey.MISSING_COST_DATA)
        skip = run.skip_for(AlertRuleKey.MISSING_COST_DATA)
        assert skip is not None
        assert skip.reason == SkipReason.NO_TRADING_ACTIVITY


# ---------------------------------------------------------------------------
# 5. Dedup
# ---------------------------------------------------------------------------


def test_re_running_the_same_bucket_does_not_duplicate_the_alert():
    """A bucket gets recomputed whenever a refund or a cost edit dirties it.

    Four recomputes must not leave four identical rows: the existing row is
    refreshed in place and its evaluation count goes up, so the feed stays
    readable and the alert keeps a single id anyone can refer to.
    """
    with sandbox(DEDUP_BUCKET) as (db, generation, cache):
        _seed_orders(
            db, generation, bucket=DEDUP_BUCKET, days=42, bucket_value=400.0
        )

        first = anomalies.run(
            db, DEDUP_BUCKET, tz_generation=generation, now=NOW, redis_client=cache
        )
        db.commit()
        assert first.fired(AlertRuleKey.SALES_DROP)
        first_id = first.alerts_for(AlertRuleKey.SALES_DROP)[0].id

        later = NOW + timedelta(hours=6)
        second = anomalies.run(
            db,
            DEDUP_BUCKET,
            tz_generation=generation,
            now=later,
            redis_client=cache,
        )
        db.commit()

        rows = _alert_rows(db, DEDUP_BUCKET, AlertRuleKey.SALES_DROP)
        assert len(rows) == 1, "a re-run created a second row for the same bucket"
        assert rows[0].id == first_id
        assert second.alerts_for(AlertRuleKey.SALES_DROP)[0].id == first_id

        lifecycle = rows[0].context["lifecycle"]
        assert lifecycle["evaluations"] == 2
        # The first sighting is preserved even though detected_at moved on, so
        # "how long has this been going?" is still answerable.
        assert lifecycle["first_detected_at"] == NOW.isoformat()
        assert rows[0].detected_at.replace(tzinfo=timezone.utc) == later


# ---------------------------------------------------------------------------
# 7. Lifecycle
# ---------------------------------------------------------------------------


def test_acknowledge_and_resolve_transitions_work_and_are_recorded():
    with sandbox(LIFECYCLE_BUCKET) as (db, generation, cache):
        _seed_orders(
            db, generation, bucket=LIFECYCLE_BUCKET, days=42, bucket_value=300.0
        )
        alerts = detect(
            db,
            LIFECYCLE_BUCKET,
            tz_generation=generation,
            now=NOW,
            redis_client=cache,
        )
        db.commit()
        alert = next(a for a in alerts if a.rule_key == AlertRuleKey.SALES_DROP)
        detected_severity = alert.severity
        detected_actual = alert.actual_value

        acked_at = NOW + timedelta(hours=1)
        acknowledge(db, alert, user_id=4242, now=acked_at)
        db.commit()
        assert alert.status == AlertStatus.ACKNOWLEDGED
        assert alert.acknowledged_by_user_id == 4242
        assert alert.acknowledged_at.replace(tzinfo=timezone.utc) == acked_at
        trail = alert.context["lifecycle"]["transitions"]
        assert [t["to"] for t in trail] == [AlertStatus.ACKNOWLEDGED]
        assert trail[0]["by_user_id"] == 4242

        # A detector re-run must not rewrite an alert a human now owns: those
        # numbers are what they were told.
        anomalies.run(
            db,
            LIFECYCLE_BUCKET,
            tz_generation=generation,
            now=NOW + timedelta(hours=2),
            redis_client=cache,
        )
        db.commit()
        rows = _alert_rows(db, LIFECYCLE_BUCKET, AlertRuleKey.SALES_DROP)
        assert len(rows) == 1
        assert rows[0].status == AlertStatus.ACKNOWLEDGED
        assert rows[0].severity == detected_severity
        assert rows[0].actual_value == detected_actual

        # Resolving without saying what was done is refused: "resolved" with no
        # reason is indistinguishable from "dismissed".
        with pytest.raises(anomalies.AlertTransitionError, match="requires a note"):
            resolve(db, alert, note="   ")

        resolved_at = NOW + timedelta(hours=3)
        resolve(
            db,
            alert,
            note="Gateway outage at the PSP; revenue recovered next day.",
            user_id=4242,
            now=resolved_at,
        )
        db.commit()
        assert alert.status == AlertStatus.RESOLVED
        assert "Gateway outage" in alert.resolution_note
        trail = alert.context["lifecycle"]["transitions"]
        assert [t["to"] for t in trail] == [
            AlertStatus.ACKNOWLEDGED,
            AlertStatus.RESOLVED,
        ]
        # The detector's evidence survived the lifecycle untouched.
        assert alert.context["evidence"]["metric"] == "net_revenue"

        # Terminal means terminal. Re-opening would erase the record that it was
        # handled; the honest way to say "it happened again" is a new bucket.
        with pytest.raises(anomalies.AlertTransitionError, match="cannot move"):
            acknowledge(db, alert, user_id=4242)
        with pytest.raises(anomalies.AlertTransitionError, match="cannot move"):
            mute(db, alert, note="noisy rule")


# ---------------------------------------------------------------------------
# 8. Zero rows is a pipeline state, not a business one
# ---------------------------------------------------------------------------


def test_a_rule_whose_source_has_no_rows_is_skipped_not_fired():
    """No rollups at all, and not one alert.

    Reading an empty rollup as zero would make the store's quietest possible
    day — no data — its worst trading day on record, on every rule at once.
    """
    with sandbox(EMPTY_BUCKET) as (db, generation, cache):
        run = anomalies.run(
            db, EMPTY_BUCKET, tz_generation=generation, now=NOW, redis_client=cache
        )
        db.commit()

        assert run.alerts == []

        expectations = {
            AlertRuleKey.SALES_DROP: SkipReason.BUCKET_NOT_MEASURED,
            AlertRuleKey.REVENUE_SPIKE: SkipReason.BUCKET_NOT_MEASURED,
            AlertRuleKey.REFUND_SPIKE: SkipReason.BUCKET_NOT_MEASURED,
            AlertRuleKey.CONVERSION_DROP: SkipReason.NO_SOURCE_ROWS,
            AlertRuleKey.PAYMENT_FAILURE_SPIKE: SkipReason.NO_SOURCE_ROWS,
            AlertRuleKey.RETURN_SPIKE: SkipReason.NO_SOURCE_ROWS,
            AlertRuleKey.RTO_SPIKE: SkipReason.NO_SOURCE_ROWS,
            AlertRuleKey.OUT_OF_STOCK_RISK: SkipReason.BUCKET_NOT_MEASURED,
            AlertRuleKey.MISSING_COST_DATA: SkipReason.NO_TRADING_ACTIVITY,
            AlertRuleKey.NEGATIVE_CM3: SkipReason.NO_TRADING_ACTIVITY,
            AlertRuleKey.GA4_SYNC_FAILURE: SkipReason.NO_SOURCE_ROWS,
        }
        for rule_key, reason in expectations.items():
            skip = run.skip_for(rule_key)
            assert skip is not None, f"{rule_key} produced neither alert nor skip"
            assert skip.reason == reason, f"{rule_key}: {skip.reason} != {reason}"
            assert skip.message, f"{rule_key} skipped without saying why"

        # Every one of the thirteen rules accounted for, one way or another.
        accounted = (
            {s.rule_key for s in run.skips}
            | {c.rule_key for c in run.clear}
            | {a.rule_key for a in run.alerts}
        )
        assert accounted == set(anomalies.RULES)


# ---------------------------------------------------------------------------
# The other side of rule 4: a COMPLETE cascade is allowed to say CM3 is negative
# ---------------------------------------------------------------------------


def test_a_complete_cascade_reports_negative_cm3_and_a_marketing_cost_spike():
    """The counterpart to the missing-cost test, and the proof it is not vacuous.

    ``test_no_cost_rules_raises_missing_cost_data_and_never_negative_cm3`` would
    pass just as happily against a NEGATIVE_CM3 rule that never fires at all.
    This is the same store with the ad budget actually configured: the cascade
    resolves, CM3 is a real number, it is below zero, and the rule says so.

    What this fixture does and does not model
    -----------------------------------------
    The order *rollup* records a trading day while the ``orders`` table is empty
    — 2004 predates every order this store will ever hold. That is deliberate:
    it isolates the two rules under test to the one cost that accrues on time
    rather than on volume, so the expected CM3 is exactly minus the day's
    pro-rated ad spend and can be asserted absolutely. It is not a claim about
    how a real trading day's cascade decomposes; ``test_analytics_margin.py``
    owns that.
    """
    history_days = 42
    with sandbox(MARKETING_BUCKET) as (db, generation, cache):
        _seed_orders(db, generation, bucket=MARKETING_BUCKET, days=history_days)
        # 31,000/month over a 31-day May is 1,000/day...
        _marketing_rule(
            db,
            value="31000",
            effective_from=SANDBOX_FROM,
            effective_to=MARKETING_BUCKET - timedelta(days=1),
        )
        # ...and someone raises it tenfold on the bucket itself. Effective dating
        # means the history keeps the old rate rather than being restated.
        _marketing_rule(
            db,
            value="310000",
            effective_from=MARKETING_BUCKET,
            effective_to=MARKETING_BUCKET,
        )
        db.commit()

        run = anomalies.run(
            db,
            MARKETING_BUCKET,
            tz_generation=generation,
            now=NOW,
            redis_client=cache,
        )
        db.commit()

        # Every cost input resolved, so there is nothing to report as missing.
        assert not run.fired(AlertRuleKey.MISSING_COST_DATA)

        cm3 = run.alerts_for(AlertRuleKey.NEGATIVE_CM3)
        assert len(cm3) == 1
        # 310,000 pro-rated across a 31-day May = 10,000 on the day, against no
        # merchandise sales in the orders table.
        assert cm3[0].actual_value == Decimal("-10000.0000")
        assert cm3[0].expected_low == Decimal("0.0000")
        assert cm3[0].expected_high is None
        assert cm3[0].severity == AlertSeverity.CRITICAL
        assert cm3[0].context["evidence"]["range_basis"] == RangeBasis.DEFINITIONAL

        # And the ratio rule sees the budget step against unchanged revenue.
        spike = run.alerts_for(AlertRuleKey.MARKETING_COST_SPIKE)
        assert len(spike) == 1
        evidence = spike[0].context["evidence"]
        assert evidence["marketing_spend"] == "10000.0000"
        assert spike[0].actual_value > spike[0].expected_high
        assert spike[0].severity == AlertSeverity.CRITICAL
        # The caveat rides on the alert, so nobody reads this as attributed spend.
        assert "campaign-attributed" in evidence["caveat"]

        # Revenue itself did not move, and the revenue rules stayed quiet — the
        # marketing alert is about the numerator, not a restatement of a sales
        # move under a second rule key.
        assert not run.fired(AlertRuleKey.SALES_DROP)
        assert not run.fired(AlertRuleKey.REVENUE_SPIKE)


# ---------------------------------------------------------------------------
# Out-of-stock risk: a policy threshold, not a forecast band
# ---------------------------------------------------------------------------


def test_stock_risk_alerts_only_on_products_that_are_actually_selling():
    """A slow mover reaching zero is a catalogue decision, not an incident."""
    with sandbox(STOCK_BUCKET) as (db, generation, cache):
        fast, slow, stocked = 904_001, 904_002, 904_003
        for offset in range(anomalies.VELOCITY_WINDOW_DAYS):
            day = STOCK_BUCKET - timedelta(
                days=anomalies.VELOCITY_WINDOW_DAYS - 1 - offset
            )
            db.add_all(
                [
                    AggInventoryDaily(
                        bucket_date=day,
                        tz_generation=generation,
                        product_id=fast,
                        sku_snapshot="FAST-904001",
                        stock_close=20,
                        units_sold=10,
                    ),
                    AggInventoryDaily(
                        bucket_date=day,
                        tz_generation=generation,
                        product_id=slow,
                        sku_snapshot="SLOW-904002",
                        stock_close=1,
                        units_sold=0,
                    ),
                    AggInventoryDaily(
                        bucket_date=day,
                        tz_generation=generation,
                        product_id=stocked,
                        sku_snapshot="DEEP-904003",
                        stock_close=900,
                        units_sold=10,
                    ),
                ]
            )
        db.commit()

        run = anomalies.run(
            db, STOCK_BUCKET, tz_generation=generation, now=NOW, redis_client=cache
        )
        db.commit()

        fired = run.alerts_for(AlertRuleKey.OUT_OF_STOCK_RISK)
        assert [a.dimension_value for a in fired] == ["FAST-904001"]
        alert = fired[0]
        assert alert.dimension == "product"
        # 20 units at 10/day is two days of cover against a seven-day lead time.
        assert alert.actual_value == Decimal("2.0000")
        assert alert.expected_low == Decimal("7.0000")
        assert alert.expected_high is None
        assert alert.context["evidence"]["range_basis"] == RangeBasis.POLICY_THRESHOLD
        assert alert.severity == AlertSeverity.WARNING
