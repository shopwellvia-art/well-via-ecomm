"""Tests for the forecast engine and the forecasting/seasonality resolvers.

What actually has to be true here
---------------------------------
Almost none of these tests are about whether the arithmetic is right. A forecast
that is 8% out fails safely — the interval covers it, and reality arrives to
correct it. The tests carrying real weight are the ones about **refusing**,
because a forecast is the only thing on this dashboard that is not a measurement
and it is drawn with exactly the same ink as everything that is:

  * ``test_below_the_minimum_refuses_and_returns_no_number`` — 20 days of history
    against a 28-day minimum must produce a *reason*, not an estimate. A line
    through three weeks of a new store's orders renders identically to one built
    on two years, and no reader downstream can tell which they are looking at.
  * ``test_a_short_history_produces_no_forecast_series_at_all`` — the resolver
    half of the same rule. Not an empty series, not a flat one, not one tagged
    "low confidence": the key is absent, because a present-but-empty series
    invites a chart to draw an axis through it.
  * ``test_intervals_widen_with_the_horizon`` and its sparse/noisy siblings — an
    interval that does not widen is a point estimate wearing a costume, and the
    whole claim of this module is that the band is the answer.
  * ``test_seasonality_without_a_prior_year_is_partial_and_substitutes_nothing``
    — the tempting failure. "Up 40% on the same period" against a window six
    weeks old looks exactly like a year-on-year comparison on the chart and means
    something entirely different.
  * ``test_every_forecast_point_is_estimated_never_authoritative`` — the grade is
    what stops a projection being reconciled against a bank statement.

Isolation strategy
------------------
Most of this file needs no database at all: the engine is pure functions over
``{date: value}`` and testing it through MySQL would be slower, flakier and would
prove less. Only the four resolver tests touch a session.

Those four use two layers, matching ``test_analytics_resolvers.py``:

  1. Every fixture lives in **early 2013** — a sandbox no other suite uses (June
     2008, June 2009, Jan/Jul 2009 and 2011 are taken) and years before this
     store's first order, so assertions are absolute rather than deltas.
  2. Every test writes under its **own random ``tz_generation``**, which is what
     ``source_watermark`` filters on, so one test's rollup can never move
     another's watermark.

There is no db fixture in ``conftest.py``; each test owns its ``SessionLocal()``
and tears down in ``finally`` via ``sandbox()``.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal
from typing import Callable, Iterator

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.analytics_rollups import AggOrderDaily
from app.repositories.analytics_repository import AnalyticsRepository
from app.services.analytics import forecasting
from app.services.analytics.forecasting import (
    ForecastModel,
    ForecastPoint,
    InsufficientHistory,
)
from app.services.analytics.filters import AnalyticsFilters, Comparison, Period
from app.services.analytics.resolvers import ResolverContext
from app.services.analytics.resolvers.base import NOT_CONFIGURED
from app.services.analytics.resolvers.forecast import (
    FORECAST_HORIZON,
    FORECAST_INSUFFICIENT_HISTORY,
    FORECAST_METHOD,
    SEASONALITY_INDEX_UNAVAILABLE,
    SEASONALITY_PARTIAL,
    demand_forecast,
    sales_forecast,
    seasonality,
)
from app.services.analytics.types import (
    AnalyticsViewDefinition,
    Freshness,
    MetricQuality,
    ResolverId,
    ViewState,
)
from app.services.analytics.view_service import _runtime_availability

# --------------------------------------------------------------------------
# Fixtures for the pure half
# --------------------------------------------------------------------------

#: A Monday, so a history of a whole number of weeks starts and ends cleanly and
#: the weekday a horizon lands on is predictable.
PURE_START = date(2013, 3, 4)


def _history(
    days: int,
    value: Callable[[int], float],
    *,
    start: date = PURE_START,
    skip: Callable[[int], bool] = lambda i: False,
) -> dict[date, float]:
    """`{day: value}` for `days` consecutive days, minus whatever `skip` drops."""
    return {
        start + timedelta(days=i): value(i)
        for i in range(days)
        if not skip(i)
    }


def _flat(days: int, level: float = 1000.0, **kw) -> dict[date, float]:
    return _history(days, lambda i: level, **kw)


def _noisy(days: int, level: float = 1000.0, amplitude: int = 601, **kw):
    """Deterministic pseudo-noise. A seeded RNG would be a second thing to trust;
    a coprime modular walk is reproducible by inspection."""
    return _history(
        days, lambda i: level + ((317 * i) % amplitude) - amplitude // 2, **kw
    )


#: A weekly shape whose seven factors already average exactly 1.0, so the
#: recovered index can be compared with it directly rather than to a rescaling.
WEEKLY_SHAPE: tuple[float, ...] = (0.8, 0.9, 1.0, 1.0, 1.1, 1.3, 0.9)


# ===========================================================================
# 1. Below the minimum: a reason, and no number
# ===========================================================================


def test_below_the_minimum_refuses_and_returns_no_number():
    """Twenty days against a twenty-eight day minimum yields no estimate at all.

    The refusal object deliberately has no level, no slope and no point of any
    kind on it. That is stronger than a model carrying a `low_confidence` flag: a
    flag is something every downstream caller has to remember to read, and the
    one that forgets renders a line through three weeks of noise in the same
    colour as a line built on two years.
    """
    outcome = forecasting.fit(_flat(20))

    assert isinstance(outcome, InsufficientHistory)
    assert outcome.ok is False
    assert outcome.reason == forecasting.REASON_NOT_ENOUGH_HISTORY
    assert outcome.required_days == forecasting.MIN_HISTORY_DAYS == 28
    assert outcome.observed_days == 20
    assert outcome.short_by_days == 8

    # The message states the requirement and the shortfall, not just "no data".
    assert "28" in outcome.message and "20" in outcome.message

    # Nothing on this object can be mistaken for an estimate.
    for numeric in ("point", "slope", "level", "intercept", "dow_factors"):
        assert not hasattr(outcome, numeric), f"{numeric} must not exist on a refusal"

    # And it cannot be coerced into producing one.
    with pytest.raises(AttributeError):
        forecasting.forecast(outcome, 7)  # type: ignore[arg-type]


def test_the_refusal_detail_is_machine_readable():
    """The warning payload must carry the numbers, not only the prose.

    A frontend that has to regex "needs 28 days" out of a sentence will break the
    first time the sentence is reworded, and the fallback is showing nothing at
    all — which is the same screen as a healthy view with no orders.
    """
    detail = forecasting.fit(_flat(9)).as_detail()

    assert detail["reason"] == forecasting.REASON_NOT_ENOUGH_HISTORY
    assert detail["required_days"] == 28
    assert detail["observed_days"] == 9
    assert detail["short_by_days"] == 19
    assert detail["method_id"] == forecasting.METHOD_ID


# ===========================================================================
# 2. At or above the minimum: lower <= point <= upper, always
# ===========================================================================


def test_at_the_minimum_every_point_carries_an_ordered_interval():
    model = forecasting.fit(_noisy(forecasting.MIN_HISTORY_DAYS))
    assert isinstance(model, ForecastModel)
    assert model.ok is True

    points = forecasting.forecast(model, 14)
    assert len(points) == 14
    for point in points:
        assert point.lower <= point.point <= point.upper
        assert point.interval_width == point.upper - point.lower
        assert point.interval_width > 0


def test_a_point_estimate_alone_is_not_representable():
    """`ForecastPoint` has no constructor that omits the bounds.

    Structural rather than a convention: the type is the thing that stops a
    future caller emitting a bare number, because there is no way to build one.
    """
    with pytest.raises(TypeError):
        ForecastPoint(day=PURE_START, horizon=1, point=10.0)  # type: ignore[call-arg]


def test_forecasts_are_never_negative_for_a_non_negative_measure():
    """Revenue cannot be below zero, so neither can its lower bound."""
    collapsing = _history(40, lambda i: max(0.0, 1000.0 - 30.0 * i))
    model = forecasting.fit(collapsing, non_negative=True)
    assert model.ok

    for point in forecasting.forecast(model, model.max_horizon_days):
        assert point.lower >= 0.0
        assert point.point >= 0.0


# ===========================================================================
# 3. Intervals widen with the horizon
# ===========================================================================


def test_intervals_widen_with_the_horizon():
    """Day 30 must be less certain than day 1, and visibly so.

    This is the ``(x - x̄)² / Sxx`` term of the prediction interval doing its job.
    An interval that stayed the same width for a month would be a point estimate
    with decoration: it would tell a reader that a projection three days out and
    one thirty days out deserve the same weight, which is the single most
    expensive thing a forecast can imply.
    """
    model = forecasting.fit(_noisy(35), max_horizon_days=30)
    assert model.ok
    assert model.max_horizon_days == 30

    points = forecasting.forecast(model, 30)
    day_1, day_30 = points[0], points[29]

    assert day_30.interval_width > day_1.interval_width
    # Not a rounding artefact: a month out is materially less certain.
    assert day_30.interval_width > day_1.interval_width * 1.1

    # Monotone the whole way, not merely wider at the ends — compared at a fixed
    # weekday, because the band is scaled by the same day-of-week factor as the
    # point, so a big Saturday legitimately has a wider band in rupees than the
    # Monday after it.
    same_weekday = [p.interval_width for p in points[::7]]
    assert same_weekday == sorted(same_weekday)


def test_the_horizon_is_capped_and_the_cap_is_refused_not_truncated():
    """Beyond the stated horizon this raises rather than quietly serving less.

    Truncating would leave the caller believing it got the 90 days it asked for —
    the same failure the repository refuses for over-long result sets.
    """
    model = forecasting.fit(_flat(60))
    assert model.ok
    assert model.max_horizon_days <= forecasting.MAX_HORIZON_DAYS

    with pytest.raises(ValueError, match="capped at"):
        forecasting.forecast(model, model.max_horizon_days + 1)


def test_the_horizon_never_exceeds_the_history():
    """Thirty days of history buys at most thirty days of projection."""
    model = forecasting.fit(_flat(30), max_horizon_days=forecasting.MAX_HORIZON_DAYS)
    assert model.ok
    assert model.max_horizon_days == 30

    shorter = forecasting.fit(_flat(28), max_horizon_days=forecasting.MAX_HORIZON_DAYS)
    assert shorter.ok
    assert shorter.max_horizon_days == 28
    assert "never runs further forward than the history runs back" in (
        shorter.params["horizon_cap_reason"]
    )


# ===========================================================================
# 4. Sparse and noisy histories are less certain, and say so
# ===========================================================================


def test_noisy_history_gives_wider_intervals_than_stable_history():
    stable = forecasting.forecast(forecasting.fit(_flat(90)), 14)
    noisy = forecasting.forecast(forecasting.fit(_noisy(90)), 14)

    assert noisy[0].interval_width > stable[0].interval_width * 2


def test_sparse_history_gives_wider_intervals_than_dense_history():
    """Same span, same values, a third of the days missing -> wider band.

    Both histories are flat, so the residual scale is identical and the whole
    difference is the coverage term. A gap is an absence of evidence and has to
    cost something; treating the missing days as zeros instead would be the
    inverse error and would move the *line*, not the band.
    """
    dense_model = forecasting.fit(_flat(90))
    sparse_model = forecasting.fit(_flat(90, skip=lambda i: i % 3 == 1))

    assert dense_model.coverage == 1.0
    assert sparse_model.coverage < 0.7
    assert sparse_model.span_days == dense_model.span_days

    dense = forecasting.forecast(dense_model, 14)
    sparse = forecasting.forecast(sparse_model, 14)
    assert sparse[0].interval_width > dense[0].interval_width


# ===========================================================================
# 5. A flat history stays flat; a trend is followed
# ===========================================================================


def test_a_flat_history_forecasts_flat():
    model = forecasting.fit(_flat(56, level=1000.0))
    assert model.ok
    assert model.slope == pytest.approx(0.0, abs=1e-9)

    for point in forecasting.forecast(model, 14):
        assert point.point == pytest.approx(1000.0, abs=1e-6)


def test_a_flat_history_still_carries_a_non_zero_interval():
    """A perfect fixture is a small-sample artefact, not certainty.

    A zero-width band would be a claim of perfect knowledge, which no forecast is
    entitled to make — so the residual scale is floored at a fraction of the
    level and the band survives.
    """
    points = forecasting.forecast(forecasting.fit(_flat(56, level=1000.0)), 14)
    assert points[0].interval_width > 0
    assert points[13].interval_width > points[0].interval_width


def test_a_linear_trend_is_followed():
    """value = 100 + 10i over 56 days -> slope 10, and day 57 is 660."""
    model = forecasting.fit(_history(56, lambda i: 100.0 + 10.0 * i))
    assert model.ok
    assert model.slope == pytest.approx(10.0, rel=1e-9)

    points = forecasting.forecast(model, 3)
    assert points[0].point == pytest.approx(660.0, rel=1e-6)
    assert points[1].point == pytest.approx(670.0, rel=1e-6)
    assert points[2].point == pytest.approx(680.0, rel=1e-6)


def test_a_trend_does_not_masquerade_as_a_weekly_pattern():
    """A rising series must not produce day-of-week factors out of thin air.

    Indexing each day against the *mean* rather than the *trend* is the obvious
    implementation and it invents a weekly shape whenever the business is
    growing: later weekdays sit above the mean purely because of where they fell
    in the week. The invented pattern survives any amount of data and would then
    be applied on top of the trend a second time.
    """
    model = forecasting.fit(_history(56, lambda i: 100.0 + 10.0 * i))
    assert model.ok
    for weekday, factor in model.dow_factors.items():
        assert factor == pytest.approx(1.0, abs=1e-6), f"weekday {weekday}"


# ===========================================================================
# 6. Day-of-week factors are recovered
# ===========================================================================


def test_day_of_week_factors_are_recovered_from_a_weekly_pattern():
    """Eight clean weeks of a known shape come back as that shape.

    Tolerance is 1%, not exact: a within-week shape induces a very small slope in
    the least-squares line used to detrend, so the recovered factors are the
    planted ones plus a fraction of a percent. Asserting exactness would be
    asserting an implementation rather than a behaviour.
    """
    history = _history(56, lambda i: 1000.0 * WEEKLY_SHAPE[i % 7])
    index = forecasting.seasonal_index(history)

    assert index.day_of_week is not None
    for weekday, expected in enumerate(WEEKLY_SHAPE):
        assert index.day_of_week[weekday] == pytest.approx(expected, rel=0.01)

    # And the same shape survives into the projection.
    model = forecasting.fit(history)
    assert model.ok and model.dow_applied
    saturday = next(
        p for p in forecasting.forecast(model, 7) if p.day.weekday() == 5
    )
    monday = next(p for p in forecasting.forecast(model, 7) if p.day.weekday() == 0)
    assert saturday.point > monday.point * 1.5


def test_a_weekday_index_is_not_claimed_from_three_weeks():
    """Three observations of each weekday is one freak Saturday from nonsense."""
    index = forecasting.seasonal_index(_history(21, lambda i: 1000.0 * WEEKLY_SHAPE[i % 7]))

    assert index.day_of_week is None  # not an empty dict — that renders as "flat"
    assert any("Day-of-week index" in m for m in index.missing)
    assert any(str(forecasting.DOW_MIN_OBSERVATIONS) in m for m in index.missing)


def test_a_month_index_is_not_claimed_without_a_full_year():
    """Six months of data ranks six months; it does not describe a year."""
    index = forecasting.seasonal_index(_noisy(180))

    assert index.month_of_year is None
    assert any("Month-of-year index" in m for m in index.missing)
    assert any("full calendar year" in m for m in index.missing)


# ===========================================================================
# 7. Year-over-year needs a prior year and substitutes nothing
# ===========================================================================


def test_year_over_year_without_a_prior_year_is_refused_with_the_shortfall():
    window_from = date(2013, 3, 1)
    readiness = forecasting.year_over_year_readiness(
        earliest_history=date(2012, 11, 21),
        window_from=window_from,
        window_to=date(2013, 3, 31),
    )

    assert readiness.ready is False
    assert readiness.required_from == date(2012, 3, 1)
    # The shortfall is how far AFTER the required date the history begins. Signed
    # the other way it would read as a surplus, and a UI showing "-265 days" next
    # to "not enough history" is a bug report nobody can act on.
    assert readiness.short_by_days == 265
    assert readiness.short_by_days == (date(2012, 11, 21) - date(2012, 3, 1)).days

    text = " ".join(readiness.missing)
    assert "2012-03-01" in text and "2012-11-21" in text
    assert "NOT substituted" in text

    # There is nowhere for a substituted comparison to live.
    assert not hasattr(readiness, "comparison")
    assert not hasattr(readiness, "previous_period")


def test_year_over_year_with_no_history_at_all_names_the_whole_year():
    readiness = forecasting.year_over_year_readiness(
        earliest_history=None,
        window_from=date(2013, 3, 1),
        window_to=date(2013, 3, 31),
    )
    assert readiness.ready is False
    assert readiness.short_by_days == forecasting.YOY_LOOKBACK_DAYS
    assert "no order history at all" in " ".join(readiness.missing)


def test_year_over_year_with_a_full_prior_year_is_ready():
    readiness = forecasting.year_over_year_readiness(
        earliest_history=date(2011, 12, 31),
        window_from=date(2013, 3, 1),
        window_to=date(2013, 3, 31),
    )
    assert readiness.ready is True
    assert readiness.missing == ()
    assert readiness.short_by_days == 0


# ===========================================================================
# 8 + 9. Quality and provenance
# ===========================================================================


def test_every_forecast_point_is_estimated_never_authoritative():
    """A projection is not a measurement and cannot be graded as one.

    `quality` is `init=False` on the dataclass, so there is no argument by which a
    caller could label one of these AUTHORITATIVE — which is what would let a
    forecast be reconciled against a bank statement.
    """
    model = forecasting.fit(_noisy(60))
    points = forecasting.forecast(model, model.max_horizon_days)

    assert points
    assert {p.quality for p in points} == {MetricQuality.ESTIMATED.value}
    assert MetricQuality.AUTHORITATIVE.value not in {p.quality for p in points}

    with pytest.raises(TypeError):
        ForecastPoint(  # type: ignore[call-arg]
            day=PURE_START,
            horizon=1,
            point=1.0,
            lower=0.0,
            upper=2.0,
            quality=MetricQuality.AUTHORITATIVE.value,
        )


def test_the_method_name_and_its_parameters_are_in_the_output():
    """A forecast whose derivation is unstated can only be believed, not argued
    with, so every constant that produced it travels with it."""
    model = forecasting.fit(_noisy(60), confidence=0.95)
    params = model.params

    assert params["method"] == forecasting.METHOD_NAME
    assert params["method_id"] == forecasting.METHOD_ID
    assert params["min_history_days"] == forecasting.MIN_HISTORY_DAYS
    assert params["history_days_used"] == 60
    assert params["history_from"] == PURE_START.isoformat()
    assert params["history_to"] == (PURE_START + timedelta(days=59)).isoformat()
    assert params["confidence"] == 0.95
    assert params["level_window_days"] == forecasting.LEVEL_WINDOW_DAYS
    assert params["day_of_week_factor_applied"] is True
    assert params["day_of_week_factors"]["Saturday"] == round(model.dow_factors[5], 4)
    assert "trend_per_day" in params
    assert "residual_sigma" in params
    assert params["max_horizon_days"] == model.max_horizon_days

    # And in a sentence, because the params table is not what a merchant reads.
    assert forecasting.METHOD_NAME in model.method_summary
    assert "per day" in model.method_summary


def test_a_wider_confidence_level_gives_a_wider_band():
    eighty = forecasting.forecast(forecasting.fit(_noisy(60), confidence=0.80), 7)
    ninety_five = forecasting.forecast(forecasting.fit(_noisy(60), confidence=0.95), 7)

    assert ninety_five[0].interval_width > eighty[0].interval_width


def test_an_untabulated_confidence_level_is_refused():
    """Serving the nearest tabulated level would relabel the band."""
    with pytest.raises(ValueError, match="confidence"):
        forecasting.fit(_flat(60), confidence=0.99)


# ===========================================================================
# 10. Zero history is "not computed", never zero
# ===========================================================================


@pytest.mark.parametrize("empty", [{}, [], ()], ids=["dict", "list", "tuple"])
def test_a_zero_history_window_returns_not_enough_history_not_zeros(empty):
    outcome = forecasting.fit(empty)

    assert isinstance(outcome, InsufficientHistory)
    assert outcome.reason == forecasting.REASON_NO_HISTORY
    assert outcome.observed_days == 0
    assert outcome.earliest is None and outcome.latest is None
    assert "not computed" in outcome.message
    # Specifically NOT a model whose every projected day happens to be 0.0.
    assert not hasattr(outcome, "level")


def test_a_duplicated_day_is_refused_rather_than_averaged():
    """Two rows for one day means the caller did not group by bucket_date, and it
    silently reweights every day-of-week factor."""
    with pytest.raises(ValueError, match="twice"):
        forecasting.fit([(PURE_START, 1.0), (PURE_START, 2.0)])


def test_absent_days_are_skipped_rather_than_zero_filled():
    """A None value is 'not measured' and must not become a measured zero.

    Zero-filling here would drag both the level and the trend downwards, and the
    resulting forecast would be confidently pessimistic with nothing on the
    response to say why.
    """
    with_nones = _flat(60)
    for offset in (10, 11, 12):
        with_nones[PURE_START + timedelta(days=offset)] = None  # type: ignore[assignment]

    model = forecasting.fit(with_nones)
    assert model.ok
    assert model.observed_days == 57
    assert model.span_days == 60
    assert model.level == pytest.approx(1000.0)


# ===========================================================================
# The resolver half — four tests, each owning its session and generation
# ===========================================================================

DB_HISTORY_START = date(2013, 1, 7)  # a Monday
DB_HISTORY_DAYS = 63
DB_LAST_DAY = DB_HISTORY_START + timedelta(days=DB_HISTORY_DAYS - 1)  # 2013-03-10
DB_WINDOW_TO = DB_LAST_DAY + timedelta(days=1)  # half-open
DB_TODAY = DB_LAST_DAY + timedelta(days=2)


@contextmanager
def sandbox() -> Iterator[tuple[Session, int]]:
    """A session plus a private ``tz_generation``, cleaned up unconditionally.

    The generation is the isolation key: ``source_watermark`` filters on it, and
    the watermark is what decides whether a forecast can be fitted at all.
    """
    db = SessionLocal()
    # `tz_generation` is a SMALLINT, so the whole key space is 1..32767 and it is
    # shared with every other suite. 1000-20999 belongs to
    # test_analytics_resolvers.py and 22000-30999 to test_analytics_level_resolver.py;
    # this suite takes the top of the range.
    generation = 31000 + (uuid.uuid4().int % 1700)
    try:
        yield db, generation
    finally:
        try:
            db.rollback()
            db.execute(
                delete(AggOrderDaily).where(AggOrderDaily.tz_generation == generation)
            )
            db.commit()
        finally:
            db.close()


def _seed(db: Session, generation: int, days: int, *, start: date = DB_HISTORY_START):
    for offset in range(days):
        db.add(
            AggOrderDaily(
                bucket_date=start + timedelta(days=offset),
                tz_generation=generation,
                net_revenue=Decimal("1000.00") + Decimal(offset * 5),
                orders_total=4 + (offset % 3),
                orders_paid=4 + (offset % 3),
            )
        )
    db.commit()


def _view(**kw) -> AnalyticsViewDefinition:
    base = dict(
        number=999,
        name="Forecast fixture",
        slug="forecast-fixture",
        summary="fixture",
        permission="analytics.executive.view",
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.DAILY,
        # LIVE on purpose: `_runtime_availability` may only DOWNGRADE, so a view
        # declared PARTIAL would pass the PARTIAL assertion without the resolver
        # having done anything at all.
        state=ViewState.LIVE,
        kpis=("net_revenue", "orders_count"),
    )
    base.update(kw)
    return AnalyticsViewDefinition(**base)  # type: ignore[arg-type]


def _ctx(db: Session, view: AnalyticsViewDefinition, generation: int) -> ResolverContext:
    filters = AnalyticsFilters(
        period=Period.CUSTOM,
        date_from=DB_HISTORY_START,
        date_to=DB_WINDOW_TO,
        comparison=Comparison.NONE,
    )
    return ResolverContext(
        db=db,
        repo=AnalyticsRepository(db),
        view=view,
        filters=filters,
        window=filters.resolve(DB_TODAY),
        tz_generation=generation,
        today=DB_TODAY,
    )


def _warning(result, code: str):
    return next((w for w in result.warnings if w.code == code), None)


def test_sales_forecast_emits_a_band_the_method_and_an_estimated_grade():
    """The happy path, and every honesty obligation it carries.

    Sixty-three days is comfortably past the minimum, so a projection is produced
    — and it arrives with bounds on every point, the method and its parameters in
    a warning, a `derived` provenance entry distinct from the rollup it was built
    from, and an ESTIMATED grade on the view as a whole.
    """
    view = _view(params={"fn": "sales_forecast", "horizon_days": 14})
    with sandbox() as (db, generation):
        _seed(db, generation, DB_HISTORY_DAYS)
        result = sales_forecast(_ctx(db, view, generation))

        points = result.series["actual_vs_forecast"]
        actuals = [p for p in points if not p["is_forecast"]]
        projected = [p for p in points if p["is_forecast"]]

        assert len(actuals) == DB_HISTORY_DAYS
        assert len(projected) == 14

        # Actuals stop exactly at the watermark; the forecast starts the day after.
        assert actuals[-1]["date"] == DB_LAST_DAY.isoformat()
        assert projected[0]["date"] == (DB_LAST_DAY + timedelta(days=1)).isoformat()

        for point in projected:
            assert point["forecast_lower"] <= point["forecast_revenue"]
            assert point["forecast_revenue"] <= point["forecast_upper"]
            assert point["quality"] == MetricQuality.ESTIMATED.value
            # The actual key stays null on a projected day: a forecast must never
            # be readable as a measurement by a chart that only knows one series.
            assert point["net_revenue"] is None

        assert projected[-1]["forecast_upper"] - projected[-1]["forecast_lower"] > (
            projected[0]["forecast_upper"] - projected[0]["forecast_lower"]
        )

        method = _warning(result, FORECAST_METHOD)
        assert method is not None
        assert method.severity == "info"
        assert method.detail["method"] == forecasting.METHOD_NAME
        assert method.detail["method_id"] == forecasting.METHOD_ID
        assert method.detail["min_history_days"] == forecasting.MIN_HISTORY_DAYS
        assert method.detail["history_days_used"] == DB_HISTORY_DAYS
        assert method.detail["history_from"] == DB_HISTORY_START.isoformat()
        assert method.detail["history_to"] == DB_LAST_DAY.isoformat()
        assert "interval" in method.message or "%" in method.message

        horizon = _warning(result, FORECAST_HORIZON)
        assert horizon is not None
        assert horizon.detail["horizon_days"] == 14
        assert horizon.detail["forecast_to"] == (
            DB_LAST_DAY + timedelta(days=14)
        ).isoformat()

        # A view carrying a projection can never be better than ESTIMATED.
        assert result.quality is MetricQuality.ESTIMATED
        assert _runtime_availability(view, result) == ViewState.LIVE.value

        derived = [s for s in result.sources if s.kind == "derived"]
        assert len(derived) == 1
        assert derived[0].id == f"forecast:{forecasting.METHOD_ID}"
        assert [s.id for s in result.sources].count("agg_order_daily") == 1


def test_a_short_history_produces_no_forecast_series_at_all():
    """Ten days of orders: the actuals are drawn and nothing is projected.

    Not an empty forecast series, not a flat one, not a shorter one. The key is
    absent, so there is no array for a chart to draw an axis through, and the
    warning carries the shortfall as numbers.
    """
    view = _view(params={"fn": "sales_forecast"})
    with sandbox() as (db, generation):
        _seed(db, generation, 10)
        ctx = _ctx(db, view, generation)
        result = sales_forecast(ctx)

        points = result.series.get("actual_vs_forecast", [])
        assert points, "the measured half is still shown"
        assert all(p["is_forecast"] is False for p in points)
        assert all("forecast_revenue" not in p for p in points)
        assert all("forecast_lower" not in p for p in points)

        refusal = _warning(result, FORECAST_INSUFFICIENT_HISTORY)
        assert refusal is not None
        assert refusal.severity == "error"
        assert refusal.detail["required_days"] == forecasting.MIN_HISTORY_DAYS
        assert refusal.detail["observed_days"] == 10
        assert refusal.detail["short_by_days"] == 18

        # No method warning either: there is no method, because nothing was done.
        assert _warning(result, FORECAST_METHOD) is None
        assert result.quality is MetricQuality.INCOMPLETE
        assert _runtime_availability(view, result) == ViewState.PARTIAL.value
        assert not any(s.kind == "derived" for s in result.sources)


def test_an_empty_window_returns_the_not_enough_history_state_not_zeros():
    """No rollup rows at all. Nothing on the response may read as a zero."""
    view = _view(params={"fn": "sales_forecast"})
    with sandbox() as (db, generation):
        result = sales_forecast(_ctx(db, view, generation))

        assert result.series == {}
        refusal = _warning(result, FORECAST_INSUFFICIENT_HISTORY)
        assert refusal is not None
        assert refusal.detail["reason"] == forecasting.REASON_NO_HISTORY
        assert refusal.detail["observed_days"] == 0

        # The KPI cards are null, not 0 — `build_kpi` refuses to invent a zero.
        for kpi in result.kpis.values():
            assert kpi.value is None

        assert result.quality is MetricQuality.INCOMPLETE
        assert _runtime_availability(view, result) == ViewState.PARTIAL.value


def test_seasonality_without_a_prior_year_is_partial_and_substitutes_nothing():
    """The whole point of view 56, and the whole risk of it.

    Sixty-three days is enough for a weekday index and nowhere near enough for a
    year-over-year comparison. The resolver must produce the first, refuse the
    second by name, and offer no shorter comparison in its place — because a
    previous-period delta labelled "seasonality" renders identically and means
    something completely different.
    """
    view = _view(params={"fn": "seasonality"})
    with sandbox() as (db, generation):
        _seed(db, generation, DB_HISTORY_DAYS)
        result = seasonality(_ctx(db, view, generation))

        # The weekday index is earned and produced.
        dow = result.series["dow_index"]
        assert len(dow) == 7
        assert {row["label"] for row in dow} == set(forecasting.WEEKDAY_NAMES)
        assert all(row["observations"] >= forecasting.DOW_MIN_OBSERVATIONS for row in dow)
        assert all(row["quality"] == MetricQuality.ESTIMATED.value for row in dow)

        # The month index is not, and says so rather than ranking three months.
        assert "month_index" not in result.series
        month_gap = _warning(result, SEASONALITY_INDEX_UNAVAILABLE)
        assert month_gap is not None
        assert "Month-of-year index" in month_gap.message

        partial = _warning(result, SEASONALITY_PARTIAL)
        assert partial is not None
        assert partial.detail["state"] == ViewState.PARTIAL.value
        assert partial.detail["ready"] is False
        assert partial.detail["needs_history_from"] == (
            DB_HISTORY_START - timedelta(days=forecasting.YOY_LOOKBACK_DAYS)
        ).isoformat()
        assert partial.detail["earliest_history"] == DB_HISTORY_START.isoformat()
        assert partial.detail["short_by_days"] > 0
        assert "NOT substituted" in partial.message

        # Nothing anywhere on the response is a stand-in comparison.
        assert partial.detail["substituted_comparison"] is None
        assert not any(key.startswith("yoy") for key in result.series)
        assert not any("previous" in key for key in result.series)

        assert result.quality is MetricQuality.INCOMPLETE
        assert _runtime_availability(view, result) == ViewState.PARTIAL.value


def test_demand_forecasting_is_gated_and_reports_what_it_would_need():
    """View 31 needs per-product stock-out history, which is forward-only.

    The gate returns the requirement with real numbers in it and — critically —
    an empty `sources` list, which is the machine-readable way a caller tells
    "nothing is configured" from "configured and healthy" without parsing prose.
    """
    view = _view(
        slug="demand-forecasting",
        params={"fn": "demand_forecast"},
        limitation="Forecasts need several months of demand and stock-out history.",
    )
    with sandbox() as (db, generation):
        result = demand_forecast(_ctx(db, view, generation))

        assert result.series == {}
        assert result.kpis == {}
        assert result.tables == {}
        assert result.sources == []
        assert result.quality is MetricQuality.INCOMPLETE

        gate = _warning(result, NOT_CONFIGURED)
        assert gate is not None
        assert gate.severity == "error"
        assert gate.detail["required_days"] == forecasting.MIN_HISTORY_DAYS
        assert gate.detail["days_available"] == 0
        assert gate.detail["short_by_days"] == forecasting.MIN_HISTORY_DAYS
        assert "inventory_ledger" in gate.detail["requires"]
        assert "order_line_fact" in gate.detail["requires"]
        assert "is_oos" in gate.detail["blocking_input"]
        assert "forward-only" in gate.message
