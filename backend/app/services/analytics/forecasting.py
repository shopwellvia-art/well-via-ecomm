"""The forecast engine: a moving level, a linear trend, and a day-of-week factor.

Why this file is mostly about refusing
--------------------------------------
A forecast is the only number on this dashboard that is not a measurement, and it
is also the one that looks the most authoritative. A chart line that continues
past today reads as fact to everybody who sees it, and unlike a wrong revenue
figure there is nothing to reconcile it against until the future arrives. That
asymmetry is the whole design brief:

* **There is a minimum history, and below it this module returns a reason, not a
  number.** ``MIN_HISTORY_DAYS`` is 28 — four complete weeks. That is the
  smallest history in which every weekday has been observed four times, which is
  the smallest sample in which one freak Saturday does not move the Saturday
  factor by a third. Below it, ``fit()`` returns :class:`InsufficientHistory`,
  which carries no estimate of any kind. A straight line through three days of
  noise is not a worse forecast than a good one; it is a different kind of
  object, and printing it in the same place would make the two indistinguishable.

* **Every point carries an interval, and the interval widens honestly.** The
  half-width is the textbook OLS prediction interval,
  ``sigma * sqrt(1 + 1/n + (x-x̄)²/Sxx)``, so it grows with the horizon (the
  ``(x-x̄)²`` term), with the noise in the history (``sigma``), and with a short
  history (``1/n`` and a small ``Sxx``). Sparse history — days missing inside the
  span — inflates it further by ``1/sqrt(coverage)``, which is the same statement
  as "half the days observed is half the evidence". A point estimate on its own
  is a claim nobody can evaluate.

* **There is a stated horizon and nothing is produced past it.**
  ``MAX_HORIZON_DAYS`` is 30, and the model additionally refuses to look further
  forward than it has looked back. Projecting 90 days from 30 days of history is
  asserting a trend three times longer than the evidence for it, and the interval
  arithmetic will not save a reader from that — it will just be very wide and
  still get drawn.

* **Year-over-year needs a prior year.** :func:`year_over_year_readiness` says so
  and says precisely how short the history is. Substituting a shorter comparison
  and labelling it seasonality is the specific failure this function exists to
  prevent: "up 40% on the same period" means nothing if the same period was six
  weeks ago.

Why this method and not something better
----------------------------------------
Moving level + least-squares trend + multiplicative day-of-week factor. It is
chosen because a merchant can be told exactly what it did — "we averaged your
recent weeks, drew the trend through them, and Saturdays run 1.3x your average" —
and can therefore disagree with it. ARIMA or a gradient-boosted regressor would
fit this data no better (there is barely any of it), would add a dependency that
``requirements.txt`` does not have and that this one screen cannot justify, and
would produce a number whose derivation nobody in the building could explain. A
forecast whose method is unstated cannot be argued with, which is a defect and
not a feature — so :attr:`ForecastModel.params` carries every constant that went
into it and the resolvers put it on the response.

Everything here is pure: stdlib only, no SQLAlchemy, no session, no clock. The
maths does not need a database and is tested without one.
"""
from __future__ import annotations

import math
from collections.abc import Mapping as _AbcMapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence

from app.services.analytics.types import MetricQuality

__all__ = [
    "METHOD_ID",
    "METHOD_NAME",
    "MIN_HISTORY_DAYS",
    "MAX_HORIZON_DAYS",
    "DEFAULT_HORIZON_DAYS",
    "DEFAULT_CONFIDENCE",
    "DOW_MIN_OBSERVATIONS",
    "MONTH_MIN_OBSERVATIONS",
    "MIN_MONTHLY_HISTORY_DAYS",
    "YOY_LOOKBACK_DAYS",
    "CONFIDENCE_CAVEAT",
    "InsufficientHistory",
    "ForecastModel",
    "ForecastPoint",
    "SeasonalIndex",
    "YoyReadiness",
    "fit",
    "forecast",
    "seasonal_index",
    "year_over_year_readiness",
    "WEEKDAY_NAMES",
    "MONTH_NAMES",
]


# ---------------------------------------------------------------------------
# The constants, all of which are published in the model's `params`
# ---------------------------------------------------------------------------

#: Bumped when the method changes meaning, so a screenshot of a forecast can be
#: traced back to the arithmetic that produced it. Same discipline as
#: `kpis.KpiDef.version`.
METHOD_ID = "ma_trend_dow_v1"
METHOD_NAME = "Moving level + least-squares trend + day-of-week factor"

#: Four complete weeks. Chosen so every weekday has four observations before a
#: day-of-week factor is claimed — at three weeks one unusual Saturday moves the
#: Saturday factor by a third, and at two weeks by half. It is also the point
#: below which a least-squares slope over daily retail data is mostly fitting the
#: weekly cycle rather than any trend.
MIN_HISTORY_DAYS = 28

#: Observations of a given weekday required before a day-of-week factor is used
#: at all. With fewer, all seven factors are held at 1.0 and the model says so
#: via `dow_applied=False` rather than applying a factor built on two Tuesdays.
DOW_MIN_OBSERVATIONS = 4

#: The level is averaged over whole weeks so it does not inherit the weekday mix
#: of whichever days happened to land at the end of the history.
LEVEL_WINDOW_DAYS = 7

#: The furthest this module will ever project, whatever a caller asks for. Past a
#: month, a trend fitted on daily order data is describing the fitter's optimism
#: rather than the business.
MAX_HORIZON_DAYS = 30
DEFAULT_HORIZON_DAYS = 14

#: Two-sided interval. 80% rather than 95% because the honest reading of these
#: bands is "most days land in here", and a 95% band on this much data is so wide
#: that readers learn to ignore it — which loses the uncertainty entirely.
DEFAULT_CONFIDENCE = 0.80
SUPPORTED_CONFIDENCE: tuple[float, ...] = (0.80, 0.95)

#: A perfectly repeating history is a small-sample artefact, not evidence of
#: perfect knowledge, so the residual scale is floored at this fraction of the
#: mean level. Without it a flat fixture produces a zero-width interval, which is
#: a claim of certainty no forecast is entitled to make.
MIN_SIGMA_FRACTION = 0.05

#: Day-of-week factors are floored here so deseasonalising cannot divide by ~0
#: (a store closed every Sunday would otherwise produce an infinite Sunday).
MIN_DOW_FACTOR = 0.05

#: A month-of-year index needs a full calendar year, so that every month has been
#: observed at all, plus most of the days in each month.
MIN_MONTHLY_HISTORY_DAYS = 365
MONTH_MIN_OBSERVATIONS = 20

#: Year-over-year needs the history to reach a full year before the window under
#: comparison. Nothing shorter is a year-over-year comparison.
YOY_LOOKBACK_DAYS = 365

#: The sentence that goes on every response carrying a forecast. Stated once here
#: so both resolvers say exactly the same thing.
CONFIDENCE_CAVEAT = (
    "These are estimates, not measurements. The band is an {conf:.0%} interval: "
    "on this method and this much history, roughly {conf:.0%} of days should land "
    "inside it, and it widens the further out the projection goes. Treat the band "
    "as the answer and the line as its midpoint — a single number here would be "
    "more precise than the data supports."
)

WEEKDAY_NAMES: tuple[str, ...] = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)
MONTH_NAMES: tuple[str, ...] = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

#: Machine-readable refusal reasons. Strings rather than an enum for the same
#: reason `WarningCode` is: a new one needs no migration and no coordinated
#: frontend release.
REASON_NOT_ENOUGH_HISTORY = "NOT_ENOUGH_HISTORY"
REASON_NO_HISTORY = "NO_HISTORY"
REASON_DEGENERATE = "HISTORY_HAS_NO_VARIATION_AND_NO_LEVEL"


# ---------------------------------------------------------------------------
# Student-t quantiles without a dependency
# ---------------------------------------------------------------------------
# scipy is not in requirements.txt and would be an absurd thing to add for two
# columns of a table. These are the standard two-sided critical values; a df that
# is not tabulated takes the largest tabulated df BELOW it, which errs wide
# rather than narrow. Past 120 df the t distribution is the normal to three
# decimal places, so the z value is used.

_T_TABLE: Mapping[float, Mapping[int, float]] = {
    0.80: {
        1: 3.078, 2: 1.886, 3: 1.638, 4: 1.533, 5: 1.476, 6: 1.440, 7: 1.415,
        8: 1.397, 9: 1.383, 10: 1.372, 11: 1.363, 12: 1.356, 13: 1.350,
        14: 1.345, 15: 1.341, 16: 1.337, 17: 1.333, 18: 1.330, 19: 1.328,
        20: 1.325, 22: 1.321, 24: 1.318, 26: 1.315, 28: 1.313, 30: 1.310,
        40: 1.303, 50: 1.299, 60: 1.296, 80: 1.292, 100: 1.290, 120: 1.289,
    },
    0.95: {
        1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
        8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160,
        14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093,
        20: 2.086, 22: 2.074, 24: 2.064, 26: 2.056, 28: 2.048, 30: 2.042,
        40: 2.021, 50: 2.009, 60: 2.000, 80: 1.990, 100: 1.984, 120: 1.980,
    },
}
_Z_TABLE: Mapping[float, float] = {0.80: 1.2816, 0.95: 1.9600}


def _critical_value(confidence: float, df: int) -> float:
    """Two-sided critical value for `confidence` at `df` degrees of freedom."""
    table = _T_TABLE.get(confidence)
    if table is None:  # pragma: no cover - guarded by _check_confidence
        raise ValueError(f"unsupported confidence {confidence!r}")
    if df <= 0:
        # No degrees of freedom means no estimate of spread at all. Use the
        # widest tabulated value rather than pretending to a narrow one.
        return table[1]
    if df > 120:
        return _Z_TABLE[confidence]
    keys = [k for k in table if k <= df]
    return table[max(keys)]


def _check_confidence(confidence: float) -> float:
    value = round(float(confidence), 2)
    if value not in SUPPORTED_CONFIDENCE:
        raise ValueError(
            f"confidence {confidence!r} is not one of {list(SUPPORTED_CONFIDENCE)}. "
            "Critical values are tabulated rather than computed (no scipy in "
            "requirements.txt), so an arbitrary level cannot be honoured — and "
            "silently serving the nearest one would relabel the band."
        )
    return value


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InsufficientHistory:
    """`fit()` could not build a model, and says exactly why.

    Deliberately carries no level, no slope and no number of any kind. A caller
    cannot accidentally render this as a forecast, because there is nothing in it
    to render — which is the point. The alternative (a model flagged
    `low_confidence=True`) puts a number on the screen and relies on every
    downstream reader noticing a boolean.
    """

    reason: str
    message: str
    required_days: int
    observed_days: int
    span_days: int
    earliest: date | None
    latest: date | None

    #: Class attribute, not a field: `ok` is what callers branch on.
    ok = False

    @property
    def short_by_days(self) -> int:
        return max(0, self.required_days - self.observed_days)

    def as_detail(self) -> dict[str, Any]:
        """Machine-readable half of the refusal, for a warning's `detail`."""
        return {
            "reason": self.reason,
            "required_days": self.required_days,
            "observed_days": self.observed_days,
            "short_by_days": self.short_by_days,
            "span_days": self.span_days,
            "earliest": self.earliest.isoformat() if self.earliest else None,
            "latest": self.latest.isoformat() if self.latest else None,
            "method_id": METHOD_ID,
            "method": METHOD_NAME,
        }


@dataclass(frozen=True)
class ForecastModel:
    """A fitted model plus everything needed to state how it was produced.

    `params` is not decoration. It is copied verbatim onto the response so a
    reader can see the history window, the horizon cap, the confidence level and
    whether a day-of-week factor was applied at all. A forecast whose method is
    unstated cannot be challenged.
    """

    method_id: str
    method_name: str
    params: dict[str, Any]

    #: Trend on the DESEASONALISED series: value = intercept + slope * day_index.
    intercept: float
    slope: float
    #: Mean of the last whole week(s), reported so a reader can sanity-check the
    #: starting point of the projection against something they recognise.
    level: float

    #: weekday (0=Monday) -> multiplicative factor, normalised to average 1.0.
    dow_factors: dict[int, float]
    dow_observations: dict[int, int]
    #: False when the history was too thin to claim a weekly shape; the factors
    #: are then all 1.0 and the projection carries no weekly pattern.
    dow_applied: bool

    #: Residual scale on the deseasonalised series, floored — see
    #: MIN_SIGMA_FRACTION.
    sigma: float
    n: int
    x_mean: float
    sxx: float
    #: observed days / span days. Below 1.0 the intervals are inflated.
    coverage: float

    first_day: date
    last_day: date
    observed_days: int
    span_days: int

    confidence: float
    max_horizon_days: int
    non_negative: bool

    ok = True

    @property
    def method_summary(self) -> str:
        """One sentence a merchant can argue with."""
        direction = "rising" if self.slope > 0 else ("falling" if self.slope < 0 else "flat")
        weekly = (
            "with a day-of-week factor from "
            f"{min(self.dow_observations.values())}+ observations per weekday"
            if self.dow_applied
            else "with no day-of-week factor (too few observations per weekday)"
        )
        return (
            f"{METHOD_NAME}: level {self.level:,.2f} over the last "
            f"{LEVEL_WINDOW_DAYS} days, {direction} trend of {self.slope:,.2f} per "
            f"day fitted over {self.observed_days} days, {weekly}."
        )


@dataclass(frozen=True)
class ForecastPoint:
    """One projected day. Never a bare point estimate.

    `quality` is fixed at ESTIMATED at construction and is not a parameter: a
    forecast is not a measurement, and there is no code path by which one of
    these can be labelled AUTHORITATIVE.
    """

    day: date
    horizon: int
    point: float
    lower: float
    upper: float

    quality: str = field(default=MetricQuality.ESTIMATED.value, init=False)

    @property
    def interval_width(self) -> float:
        return self.upper - self.lower


@dataclass(frozen=True)
class SeasonalIndex:
    """Day-of-week and month-of-year shape, or a statement of what is missing.

    `day_of_week` / `month_of_year` are None when the history does not support
    them. None is not an empty dict: an empty dict renders as a flat chart, which
    says "no seasonality" — a much stronger and quite different claim from "we
    cannot see whether there is seasonality".
    """

    day_of_week: dict[int, float] | None
    day_of_week_observations: dict[int, int]
    month_of_year: dict[int, float] | None
    month_of_year_observations: dict[int, int]

    observed_days: int
    span_days: int
    earliest: date | None
    latest: date | None
    #: Human-readable statements of what could not be computed and why.
    missing: tuple[str, ...]

    method_id: str = METHOD_ID
    method_name: str = METHOD_NAME

    @property
    def has_any(self) -> bool:
        return self.day_of_week is not None or self.month_of_year is not None


@dataclass(frozen=True)
class YoyReadiness:
    """Whether a year-over-year comparison is possible, and how short it falls."""

    ready: bool
    window_from: date
    window_to: date
    required_from: date
    earliest_history: date | None
    short_by_days: int
    missing: tuple[str, ...]

    def as_detail(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "window_from": self.window_from.isoformat(),
            "window_to": self.window_to.isoformat(),
            "needs_history_from": self.required_from.isoformat(),
            "earliest_history": (
                self.earliest_history.isoformat() if self.earliest_history else None
            ),
            "short_by_days": self.short_by_days,
            "lookback_days": YOY_LOOKBACK_DAYS,
        }


# ---------------------------------------------------------------------------
# History normalisation
# ---------------------------------------------------------------------------

HistoryLike = Iterable[tuple[date, Any]] | Mapping[date, Any]


def _normalise(history: HistoryLike) -> tuple[list[date], list[float]]:
    """Sorted (days, values) with Nones dropped and duplicates refused.

    A duplicate date means the caller did not group by `bucket_date`, which would
    make the weekday factors depend on how many rows a day happened to have.
    Refusing is cheaper than the alternative of quietly averaging two rows that
    should have been one.
    """
    pairs = history.items() if isinstance(history, _AbcMapping) else history
    seen: dict[date, float] = {}
    for day, value in pairs:
        if not isinstance(day, date):
            raise ValueError(f"history key {day!r} is not a date")
        if day in seen:
            raise ValueError(
                f"history contains {day.isoformat()} twice; group by bucket_date "
                "before fitting — a duplicated day silently reweights the "
                "day-of-week factors"
            )
        if value is None:
            # A None is "not measured", which is exactly what an absent day is.
            # Skipping it (rather than zero-filling) keeps the coverage term
            # honest, and coverage is what widens the interval.
            continue
        # Decimal, int and float all arrive here; float() is exact enough for a
        # projection and money is re-quantised by the caller before display.
        seen[day] = float(value)
    days = sorted(seen)
    return days, [seen[d] for d in days]


def _span_days(days: Sequence[date]) -> int:
    if not days:
        return 0
    return (days[-1] - days[0]).days + 1


def _ols(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float, float, float]:
    """Least squares fit. Returns (intercept, slope, x_mean, Sxx)."""
    x_mean = fmean(xs)
    y_mean = fmean(ys)
    sxx = sum((x - x_mean) ** 2 for x in xs)
    if sxx <= 0:
        return y_mean, 0.0, x_mean, 0.0
    sxy = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    slope = sxy / sxx
    return y_mean - slope * x_mean, slope, x_mean, sxx


def _ratio_to_trend(
    days: Sequence[date], values: Sequence[float]
) -> tuple[list[date], list[float]]:
    """Each day as a multiple of the trend line through the whole history.

    **Ratio to trend, not ratio to the mean.** Dividing by the overall mean is
    the obvious thing and it is wrong whenever the business is growing: over
    eight whole weeks of a steadily rising series, later weekdays sit above the
    mean and earlier ones below it purely because of *when they fell in the
    week*, and a naive index reports that as "Sundays are 8% better than
    Mondays". The pattern is entirely manufactured by the trend, it survives any
    amount of data, and it would then be applied on top of the trend a second
    time in the forecast.

    Days where the fitted trend is <= 0 are dropped: a ratio to a non-positive
    base is not a seasonal factor, it is a sign error waiting to be plotted.
    """
    if not days:
        return [], []
    origin = days[0].toordinal()
    xs = [float(d.toordinal() - origin) for d in days]
    intercept, slope, _, _ = _ols(xs, values)
    kept_days: list[date] = []
    ratios: list[float] = []
    for day, x, value in zip(days, xs, values):
        trend = intercept + slope * x
        if trend <= 0:
            continue
        kept_days.append(day)
        ratios.append(value / trend)
    return kept_days, ratios


def _dow_factors(
    days: Sequence[date], values: Sequence[float], *, min_observations: int
) -> tuple[dict[int, float], dict[int, int], bool]:
    """Multiplicative weekday factors, normalised to average exactly 1.0.

    Normalising matters: seven factors that average 1.12 would silently lift
    every projected day by 12% on top of the trend, and the trend is fitted on
    the deseasonalised series afterwards — so the inflation would be applied
    twice and nothing in the output would show it.
    """
    observations = {d: 0 for d in range(7)}
    for day in days:
        observations[day.weekday()] += 1

    flat = {d: 1.0 for d in range(7)}
    if any(c < min_observations for c in observations.values()):
        return flat, observations, False

    kept_days, ratios = _ratio_to_trend(days, values)
    buckets: dict[int, list[float]] = {d: [] for d in range(7)}
    for day, ratio in zip(kept_days, ratios):
        buckets[day.weekday()].append(ratio)
    if any(len(b) < min_observations for b in buckets.values()):
        # Enough calendar days, but not enough of them sit on a positive trend.
        return flat, observations, False

    raw = {d: fmean(buckets[d]) for d in range(7)}
    mean_factor = fmean(raw.values())
    if mean_factor <= 0:  # pragma: no cover - ratios to a positive trend
        return flat, observations, False
    # Floored so deseasonalising a genuinely-closed weekday cannot divide by ~0.
    factors = {d: max(raw[d] / mean_factor, MIN_DOW_FACTOR) for d in range(7)}
    return factors, observations, True


# ---------------------------------------------------------------------------
# fit
# ---------------------------------------------------------------------------


def fit(
    history: HistoryLike,
    *,
    min_days: int = MIN_HISTORY_DAYS,
    confidence: float = DEFAULT_CONFIDENCE,
    non_negative: bool = True,
    dow_min_observations: int = DOW_MIN_OBSERVATIONS,
    max_horizon_days: int = MAX_HORIZON_DAYS,
) -> ForecastModel | InsufficientHistory:
    """Fit the model, or return the reason it cannot be fitted.

    `min_days` counts **observed** days, not the span. Twenty-eight days of
    calendar containing nine rows is nine days of evidence; treating it as
    twenty-eight would be the same mistake as densifying a chart past its
    watermark.

    The return type is a union rather than a model-with-a-flag on purpose. A flag
    is something a caller can forget to read; a union is something the type
    checker and the ``.ok`` branch both make them handle.
    """
    confidence = _check_confidence(confidence)
    min_days = max(1, int(min_days))
    days, values = _normalise(history)
    observed = len(days)
    span = _span_days(days)

    if observed == 0:
        return InsufficientHistory(
            reason=REASON_NO_HISTORY,
            message=(
                f"No order history at all in the window, so there is nothing to "
                f"project from. A forecast needs at least {min_days} days of "
                "measured history. This is 'not computed', not 'zero'."
            ),
            required_days=min_days,
            observed_days=0,
            span_days=0,
            earliest=None,
            latest=None,
        )

    if observed < min_days:
        return InsufficientHistory(
            reason=REASON_NOT_ENOUGH_HISTORY,
            message=(
                f"A forecast needs at least {min_days} days of history and this "
                f"store has {observed} ({days[0].isoformat()} to "
                f"{days[-1].isoformat()}). {min_days} days is four complete weeks, "
                "which is the shortest history in which every weekday has been "
                "seen four times. Below that, a projection is a line drawn through "
                "noise, and it would look exactly as confident as a good one — so "
                "no number is produced."
            ),
            required_days=min_days,
            observed_days=observed,
            span_days=span,
            earliest=days[0],
            latest=days[-1],
        )

    factors, counts, dow_applied = _dow_factors(
        days, values, min_observations=dow_min_observations
    )

    origin = days[0].toordinal()
    xs = [float(d.toordinal() - origin) for d in days]
    deseasonalised = [v / factors[d.weekday()] for d, v in zip(days, values)]

    intercept, slope, x_mean, sxx = _ols(xs, deseasonalised)

    fitted = [intercept + slope * x for x in xs]
    df = observed - 2
    if df > 0:
        residual_sigma = math.sqrt(
            sum((y - f) ** 2 for y, f in zip(deseasonalised, fitted)) / df
        )
    else:  # pragma: no cover - min_days >= 3 in every real configuration
        residual_sigma = 0.0

    mean_level = fmean(deseasonalised)
    # A perfectly repeating fixture is a small-sample artefact, not certainty.
    sigma = max(residual_sigma, MIN_SIGMA_FRACTION * abs(mean_level))

    coverage = (observed / span) if span > 0 else 1.0

    tail = values[-LEVEL_WINDOW_DAYS:]
    level = fmean(tail) if tail else 0.0

    # Never look further forward than backward: a 30-day projection off 28 days
    # of history is already asserting a trend as long as the evidence for it.
    requested_horizon = max(1, int(max_horizon_days))
    horizon_cap = min(requested_horizon, MAX_HORIZON_DAYS, observed)

    params: dict[str, Any] = {
        "method_id": METHOD_ID,
        "method": METHOD_NAME,
        "min_history_days": min_days,
        "history_days_used": observed,
        "history_span_days": span,
        "history_from": days[0].isoformat(),
        "history_to": days[-1].isoformat(),
        "history_coverage_pct": round(coverage * 100, 2),
        "level_window_days": LEVEL_WINDOW_DAYS,
        "trend_per_day": round(slope, 6),
        "day_of_week_factor_applied": dow_applied,
        "day_of_week_min_observations": dow_min_observations,
        "day_of_week_factors": (
            {WEEKDAY_NAMES[d]: round(factors[d], 4) for d in range(7)}
            if dow_applied
            else None
        ),
        "residual_sigma": round(sigma, 6),
        "sigma_floor_fraction": MIN_SIGMA_FRACTION,
        "confidence": confidence,
        "interval": "OLS prediction interval, widened by 1/sqrt(coverage)",
        "max_horizon_days": horizon_cap,
        "horizon_requested_days": requested_horizon,
        "horizon_cap_reason": (
            f"capped at min(requested {requested_horizon} days, hard limit "
            f"{MAX_HORIZON_DAYS} days, {observed} days of history) — the "
            "projection never runs further forward than the history runs back"
        ),
        "non_negative": non_negative,
    }

    return ForecastModel(
        method_id=METHOD_ID,
        method_name=METHOD_NAME,
        params=params,
        intercept=intercept,
        slope=slope,
        level=level,
        dow_factors=factors,
        dow_observations=counts,
        dow_applied=dow_applied,
        sigma=sigma,
        n=observed,
        x_mean=x_mean,
        sxx=sxx,
        coverage=coverage,
        first_day=days[0],
        last_day=days[-1],
        observed_days=observed,
        span_days=span,
        confidence=confidence,
        max_horizon_days=horizon_cap,
        non_negative=non_negative,
    )


# ---------------------------------------------------------------------------
# forecast
# ---------------------------------------------------------------------------


def forecast(model: ForecastModel, horizon_days: int) -> list[ForecastPoint]:
    """Project `horizon_days` days past the end of the history.

    Raises rather than truncating when the horizon exceeds
    ``model.max_horizon_days``. Truncation would leave the caller believing it
    got what it asked for — the same class of bug as a silently shortened query
    window, which the repository also refuses.
    """
    horizon = int(horizon_days)
    if horizon < 1:
        raise ValueError("horizon_days must be >= 1")
    if horizon > model.max_horizon_days:
        raise ValueError(
            f"refusing a {horizon}-day horizon: this model is capped at "
            f"{model.max_horizon_days} days ({model.params['horizon_cap_reason']}). "
            "It is not truncated silently — a shortened horizon would look "
            "identical to the one that was asked for."
        )

    origin = model.first_day.toordinal()
    crit = _critical_value(model.confidence, model.n - 2)
    # Sparse history is less evidence per day of span; 1/sqrt(coverage) is the
    # effective-sample-size correction that says so.
    inflation = 1.0 / math.sqrt(model.coverage) if model.coverage > 0 else 1.0

    points: list[ForecastPoint] = []
    for step in range(1, horizon + 1):
        day = model.last_day + timedelta(days=step)
        x = float(day.toordinal() - origin)
        base = model.intercept + model.slope * x

        if model.sxx > 0:
            leverage = 1.0 + (1.0 / model.n) + ((x - model.x_mean) ** 2) / model.sxx
        else:  # pragma: no cover - a single-x history cannot reach here
            leverage = 1.0 + (1.0 / model.n)
        half = crit * model.sigma * math.sqrt(leverage) * inflation

        factor = model.dow_factors[day.weekday()]
        point = base * factor
        lower = (base - half) * factor
        upper = (base + half) * factor

        if model.non_negative:
            # Revenue, orders and units cannot be negative. The upper bound is
            # left alone so the band still widens after the lower bound clamps.
            point = max(point, 0.0)
            lower = max(lower, 0.0)
            upper = max(upper, 0.0)

        points.append(
            ForecastPoint(day=day, horizon=step, point=point, lower=lower, upper=upper)
        )
    return points


# ---------------------------------------------------------------------------
# seasonality
# ---------------------------------------------------------------------------


def seasonal_index(
    history: HistoryLike,
    *,
    dow_min_observations: int = DOW_MIN_OBSERVATIONS,
    month_min_observations: int = MONTH_MIN_OBSERVATIONS,
    min_monthly_history_days: int = MIN_MONTHLY_HISTORY_DAYS,
) -> SeasonalIndex:
    """Day-of-week and month-of-year indices, each produced only if earned.

    Both are multiplicative and normalised to average 1.0, so 1.30 reads as "30%
    above an average day" without the reader needing to know the base.

    The month index has a much higher bar than the weekday index and that is not
    conservatism for its own sake: with less than a calendar year, "December is
    the biggest month" is a statement about which month the store happened to
    launch near, not about the store's year.
    """
    days, values = _normalise(history)
    observed = len(days)
    span = _span_days(days)
    missing: list[str] = []

    factors, counts, dow_applied = _dow_factors(
        days, values, min_observations=dow_min_observations
    )
    dow: dict[int, float] | None = factors if dow_applied else None
    if not dow_applied:
        short = sorted(
            WEEKDAY_NAMES[d] for d, c in counts.items() if c < dow_min_observations
        )
        if observed == 0:
            missing.append(
                "Day-of-week index: no history at all in this window."
            )
        else:
            missing.append(
                "Day-of-week index: needs at least "
                f"{dow_min_observations} observations of every weekday; short on "
                + ", ".join(short)
                + ". A factor built on one or two of a weekday is that weekday's "
                "noise, not its shape."
            )

    month_counts: dict[int, int] = {m: 0 for m in range(1, 13)}
    for day in days:
        month_counts[day.month] += 1

    month: dict[int, float] | None = None
    if span < min_monthly_history_days:
        missing.append(
            f"Month-of-year index: needs a full calendar year "
            f"({min_monthly_history_days} days) so every month has been observed; "
            f"this history spans {span} days. A ranking of the months you happen "
            "to have is not a seasonal index."
        )
    else:
        thin = sorted(m for m, c in month_counts.items() if c < month_min_observations)
        if thin:
            missing.append(
                "Month-of-year index: needs at least "
                f"{month_min_observations} observed days in every month; short on "
                + ", ".join(MONTH_NAMES[m - 1] for m in thin)
                + "."
            )
        else:
            # Ratio to trend for the same reason as the weekday index: a growing
            # store would otherwise report December as strong purely because
            # December is late in the sample.
            kept_days, ratios = _ratio_to_trend(days, values)
            month_ratios: dict[int, list[float]] = {m: [] for m in range(1, 13)}
            for day, ratio in zip(kept_days, ratios):
                month_ratios[day.month].append(ratio)
            if any(len(v) < month_min_observations for v in month_ratios.values()):
                missing.append(
                    "Month-of-year index: too few days sit on a positive trend "
                    "line to index every month against it."
                )
            else:
                raw = {m: fmean(month_ratios[m]) for m in range(1, 13)}
                mean_factor = fmean(raw.values())
                if mean_factor <= 0:  # pragma: no cover - ratios to a positive trend
                    missing.append(
                        "Month-of-year index: the trend base is not positive, so a "
                        "multiplicative index has nothing to be relative to."
                    )
                else:
                    month = {m: raw[m] / mean_factor for m in range(1, 13)}

    return SeasonalIndex(
        day_of_week=dow,
        day_of_week_observations=counts,
        month_of_year=month,
        month_of_year_observations=month_counts,
        observed_days=observed,
        span_days=span,
        earliest=days[0] if days else None,
        latest=days[-1] if days else None,
        missing=tuple(missing),
    )


def year_over_year_readiness(
    *,
    earliest_history: date | None,
    window_from: date,
    window_to: date,
    lookback_days: int = YOY_LOOKBACK_DAYS,
) -> YoyReadiness:
    """Can this window be compared with the same window a year earlier?

    The only acceptable answers are "yes" and "no, and here is exactly what is
    missing". There is deliberately no third branch that substitutes the previous
    quarter, the previous month or the previous period and labels the result
    seasonality — "up 40% year on year" against a comparison window six weeks old
    is not a smaller version of the truth, it is a different claim entirely, and
    nothing in the rendered chart would reveal the substitution.
    """
    required_from = window_from - timedelta(days=lookback_days)
    if earliest_history is not None and earliest_history <= required_from:
        return YoyReadiness(
            ready=True,
            window_from=window_from,
            window_to=window_to,
            required_from=required_from,
            earliest_history=earliest_history,
            short_by_days=0,
            missing=(),
        )

    if earliest_history is None:
        return YoyReadiness(
            ready=False,
            window_from=window_from,
            window_to=window_to,
            required_from=required_from,
            earliest_history=None,
            short_by_days=lookback_days,
            missing=(
                "Year-over-year comparison: there is no order history at all, so "
                f"the prior-year window starting {required_from.isoformat()} is "
                "entirely absent. No shorter comparison is substituted.",
            ),
        )

    # The history starts LATER than required, so the shortfall is how far past
    # the required date it begins — not the other way round, which would report a
    # negative gap and read as a surplus.
    short_by = (earliest_history - required_from).days
    return YoyReadiness(
        ready=False,
        window_from=window_from,
        window_to=window_to,
        required_from=required_from,
        earliest_history=earliest_history,
        short_by_days=short_by,
        missing=(
            "Year-over-year comparison: needs history from "
            f"{required_from.isoformat()} (a full year before this window) and the "
            f"earliest day on record is {earliest_history.isoformat()} — short by "
            f"{short_by} days. A shorter comparison window is NOT substituted: "
            "comparing against six weeks ago and calling it seasonality would be "
            "indistinguishable on the chart from the real thing.",
        ),
    )
