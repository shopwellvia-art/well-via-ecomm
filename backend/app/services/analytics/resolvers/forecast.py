"""Forecasting and seasonality resolvers — views 55, 56 and 31.

These three views share one problem and it is not arithmetic. A forecast is the
only thing this dashboard prints that is not a measurement, and it is drawn with
the same ink as everything that is. This database currently holds a few weeks of
orders. A projection off that is a straight line through noise, and rendered on
the same axis as the actuals it is indistinguishable from a projection off two
years of trade. Nothing downstream — not the chart, not a screenshot of it, not
the person acting on it — can tell those two apart afterwards.

So the shape of every resolver here is the same:

1. Ask the engine for a model. If the history is too short, the engine returns a
   **reason and no number**, and the resolver emits no forecast series at all —
   not an empty one, not a flat one, not one flagged "low confidence". The
   warning carries how many days are needed and how many exist.
2. If a model is produced, every projected day carries ``lower``/``point``/
   ``upper`` and ``quality=ESTIMATED``. There is no code path that emits a bare
   point estimate and none that labels a projection AUTHORITATIVE.
3. The method, its parameters and the exact history window are attached to the
   response as an INFO warning and as a ``derived`` source ref, so the number can
   be argued with rather than merely believed.

Year-over-year is treated as a separate question with a separate answer, because
the tempting failure is to substitute a shorter comparison when a prior year is
missing. "Up 40% on the same period" against a window six weeks old is not a
weaker version of year-over-year; it is a different claim, and it looks identical
on the chart. :func:`forecasting.year_over_year_readiness` returns the shortfall
in days and this module reports it instead.

Every query goes through ``AnalyticsRepository``. Nothing here builds SQL.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.schemas.analytics_view import AnalyticsWarning, SourceRef, WarningCode
from app.services.analytics import forecasting
from app.services.analytics.filters import ResolvedWindow
from app.services.analytics.forecasting import ForecastModel, InsufficientHistory
from app.services.analytics.resolvers.base import (
    ResolverContext,
    ResolverResult,
    SourceState,
    not_configured,
    probe_source,
    source_ref,
    warn,
)
from app.services.analytics.resolvers.core import DEFAULT_SOURCE, compute_kpis
from app.services.analytics.resolvers.special import custom_function
from app.services.analytics.types import Capability, MetricQuality, ViewState

__all__ = [
    "FORECAST_METHOD",
    "FORECAST_INSUFFICIENT_HISTORY",
    "FORECAST_HORIZON",
    "SEASONALITY_PARTIAL",
    "SEASONALITY_INDEX_UNAVAILABLE",
    "sales_forecast",
    "seasonality",
    "demand_forecast",
    "DEFAULT_HISTORY_DAYS",
    "MAX_HISTORY_DAYS",
    "EARLIEST_PROBE_DAYS",
]


# ---------------------------------------------------------------------------
# Warning codes
# ---------------------------------------------------------------------------
# Plain strings for the same reason `WarningCode` is: a new code needs no
# migration and no coordinated frontend release, and an unrecognised code still
# renders its message.

#: INFO. Carries the method name and every parameter that produced the numbers.
#: Always present when a forecast is present — a forecast whose derivation is
#: unstated cannot be challenged, only believed.
FORECAST_METHOD = "FORECAST_METHOD"

#: ERROR. The history is below the minimum and NO forecast was produced. Carries
#: `required_days` / `observed_days` / `short_by_days`.
FORECAST_INSUFFICIENT_HISTORY = "FORECAST_INSUFFICIENT_HISTORY"

#: INFO. The horizon actually served and why it is capped there.
FORECAST_HORIZON = "FORECAST_HORIZON"

#: WARN. A seasonality view that cannot answer the question it exists to answer.
#: Detail carries `state: PARTIAL` and names exactly what is missing.
SEASONALITY_PARTIAL = "SEASONALITY_PARTIAL"

#: WARN. One of the two indices could not be earned from this history.
SEASONALITY_INDEX_UNAVAILABLE = "SEASONALITY_INDEX_UNAVAILABLE"


# ---------------------------------------------------------------------------
# History window sizing
# ---------------------------------------------------------------------------

#: How far back a fit looks by default, independent of the window on screen. A
#: forecast fitted only on the 30 days the user happens to be looking at would
#: change shape every time they changed the date filter, which is a property of
#: the filter and not of the business.
DEFAULT_HISTORY_DAYS = 180

#: Matches `filters.MAX_RANGE_DAYS`. The repository caps rows, not days, so the
#: ceiling has to be applied here.
MAX_HISTORY_DAYS = 400

#: How far back the "when does history start" probe looks. Beyond this a store
#: has more than two years of trade, at which point every year-over-year question
#: a 400-day window can ask is answerable anyway.
EARLIEST_PROBE_DAYS = 800

_MONEY_Q = Decimal("0.01")
_INDEX_Q = Decimal("0.0001")


def _q(value: float, quant: Decimal) -> Decimal:
    """Quantise for display. `str()` first — Decimal(0.1) is not 0.1."""
    return Decimal(str(value)).quantize(quant)


def _guard_history_generations(ctx: ResolverContext, source: str, window: ResolvedWindow) -> None:
    """Refuse a history window that straddles a reporting-timezone rebuild.

    Same rule as `base.guard_tz_generation`, applied to the fit window rather
    than the display window: the repository pins the active generation, so a
    straddling window silently drops the days that were bucketed under the old
    day boundary. Those days would then read as a gap in the history and quietly
    widen every interval, which is a plausible-looking wrong answer rather than a
    visible one.
    """
    from app.services.analytics.timebox import assert_single_generation

    seen = ctx.repo.distinct_tz_generations(source, window)
    if len(seen) > 1:
        assert_single_generation(seen)


def _history_window(anchor: date, days: int, earliest: date | None) -> ResolvedWindow:
    """Half-open window of at most `days` days, ending on `anchor` inclusive.

    Clamped at `earliest`, the first day the rollup actually holds. Without that
    clamp a 180-day look-back against a store with 60 days of history would
    zero-fill 120 days that were never aggregated — the same fabrication as
    densifying past the watermark, only pointing backwards, and worse in effect:
    it drags the fitted trend upwards out of an invented slow start and makes a
    new store's forecast look like a rocket.

    `OrderDailyJob` writes a row for every bucket it runs, including days with no
    orders, so an absent row before `earliest` really does mean "never
    aggregated" and never means "sold nothing".
    """
    start = anchor - timedelta(days=days - 1)
    if earliest is not None and earliest > start:
        start = earliest
    return ResolvedWindow(date_from=start, date_to=anchor + timedelta(days=1))


def _anchor(ctx: ResolverContext, state: SourceState) -> date | None:
    """The last day of measured history a fit may use.

    `min(watermark, last requested day)`: the freshest fully-aggregated day that
    the user's own window admits. Returning None means the rollup cannot answer
    any day of this window, which is the empty state and not a zero.
    """
    return state.covered_through(ctx.window)


def _daily_values(
    ctx: ResolverContext,
    source: str,
    column: str,
    window: ResolvedWindow,
    *,
    covered_through: date,
) -> dict[date, Decimal]:
    """One value per reporting day, zero-filled ONLY inside the covered range.

    The same rule as `core.dense_points`, restated because it is the difference
    between an honest fit and a lying one: inside the aggregated range a day with
    no row genuinely sold nothing, so it is a measured zero and belongs in the
    fit. Past the watermark there is no measurement, so the day is absent — and
    the engine's coverage term turns that absence into a wider interval rather
    than into a confident zero.
    """
    rows = ctx.repo.fetch_rollup(
        source,
        columns=[column],
        window=window,
        tz_generation=ctx.tz_generation,
        group_by=["bucket_date"],
    )
    measured = {
        row["bucket_date"]: Decimal(str(row[column] or 0))
        for row in rows
        if row.get("bucket_date") is not None
    }

    out: dict[date, Decimal] = {}
    cursor = window.date_from
    last = min(covered_through, window.date_to - timedelta(days=1))
    while cursor <= last:
        out[cursor] = measured.get(cursor, Decimal("0"))
        cursor += timedelta(days=1)
    return out


def _earliest_history_day(
    ctx: ResolverContext, source: str, anchor: date
) -> date | None:
    """The oldest reporting day this source holds, within the probe horizon.

    Deliberately NOT generation-guarded: this reads a date to answer "is there a
    prior year", not a number to be summed, and refusing the whole view because a
    timezone rebuild happened eighteen months ago would be a worse answer than
    the date itself.
    """
    window = ResolvedWindow(
        date_from=anchor - timedelta(days=EARLIEST_PROBE_DAYS),
        date_to=anchor + timedelta(days=1),
    )
    rows = ctx.repo.fetch_rollup(
        source,
        columns=["bucket_date"],
        window=window,
        tz_generation=ctx.tz_generation,
        order_by="bucket_date",
        limit=1,
    )
    return rows[0]["bucket_date"] if rows else None


def _method_warnings(model: ForecastModel, horizon: int) -> list[AnalyticsWarning]:
    """The method, its parameters, the horizon and the confidence caveat."""
    return [
        warn(
            FORECAST_METHOD,
            model.method_summary
            + " "
            + forecasting.CONFIDENCE_CAVEAT.format(conf=model.confidence),
            severity="info",
            **model.params,
        ),
        warn(
            FORECAST_HORIZON,
            f"Projected {horizon} day(s) ahead, to "
            f"{(model.last_day + timedelta(days=horizon)).isoformat()}. "
            f"{model.params['horizon_cap_reason']}. Nothing beyond that date is "
            "produced — a longer line would be an assertion, not an extrapolation.",
            severity="info",
            horizon_days=horizon,
            max_horizon_days=model.max_horizon_days,
            forecast_from=(model.last_day + timedelta(days=1)).isoformat(),
            forecast_to=(model.last_day + timedelta(days=horizon)).isoformat(),
        ),
    ]


def _insufficient_warning(
    failure: InsufficientHistory, *, what: str
) -> AnalyticsWarning:
    return warn(
        FORECAST_INSUFFICIENT_HISTORY,
        f"No {what} is shown. " + failure.message,
        severity="error",
        **failure.as_detail(),
    )


def _dedupe_sources(*refs: SourceRef) -> list[SourceRef]:
    """One provenance entry per source id.

    `compute_kpis` probes the same rollup this resolver already probed, so the
    footnote would otherwise name `agg_order_daily` twice — which reads as two
    inputs that happen to agree rather than one input read once.
    """
    out: list[SourceRef] = []
    seen: set[str] = set()
    for ref in refs:
        if ref.id in seen:
            continue
        seen.add(ref.id)
        out.append(ref)
    return out


def _forecast_source_ref(model: ForecastModel) -> SourceRef:
    """Provenance for the projected half of the chart.

    `kind="derived"` rather than `"rollup"`: these rows were computed, not read,
    and the distinction is the machine-readable half of "this is an estimate".
    """
    return source_ref(
        f"forecast:{model.method_id}",
        label=f"Forecast — {model.method_name}",
        kind="derived",
        rows=model.observed_days,
        through=model.last_day,
    )


# ===========================================================================
# 55. Sales forecasting
# ===========================================================================


@custom_function("sales_forecast")
def sales_forecast(ctx: ResolverContext) -> ResolverResult:
    """Actual revenue to date, then a bounded projection — or a stated refusal.

    The chart carries three things per projected day (``forecast_lower``,
    ``forecast_revenue``, ``forecast_upper``) and never just the middle one. A
    lone point estimate is a claim the reader has no way to evaluate; the band is
    what makes it a forecast rather than a prediction.

    Below `min_history_days` this returns the actuals and **no projection at
    all**. That is the single behaviour worth protecting here: a line through
    three weeks of a new store's orders would be drawn in the same colour, on the
    same axis, with the same apparent authority as one built on two years, and no
    reader could tell which they were looking at.
    """
    source = str(ctx.param("source") or DEFAULT_SOURCE)
    column = str(ctx.param("forecast_column") or "net_revenue")
    series_id = str(ctx.param("series_id") or "actual_vs_forecast")
    actual_key = str(ctx.param("actual_key") or column)
    forecast_key = str(ctx.param("forecast_key") or "forecast_revenue")

    min_days = int(ctx.param("min_history_days") or forecasting.MIN_HISTORY_DAYS)
    history_days = min(
        MAX_HISTORY_DAYS, max(min_days, int(ctx.param("history_days") or DEFAULT_HISTORY_DAYS))
    )
    horizon_days = int(ctx.param("horizon_days") or forecasting.DEFAULT_HORIZON_DAYS)
    confidence = float(ctx.param("confidence") or forecasting.DEFAULT_CONFIDENCE)

    state = probe_source(ctx, source, label="Order rollup (daily)")
    warnings: list[AnalyticsWarning] = list(state.warnings)
    bundle = compute_kpis(ctx, ctx.view.kpis)
    warnings.extend(bundle.warnings)

    anchor = _anchor(ctx, state)
    if anchor is None:
        # Nothing aggregated inside this window. `probe_source` already said so
        # with NO_ROLLUP_YET / ROLLUP_STALE; adding a zero-history refusal on top
        # states the forecast-specific consequence.
        failure = forecasting.fit({}, min_days=min_days)
        return ResolverResult(
            kpis=bundle.kpis,
            sources=_dedupe_sources(state.ref, *bundle.sources),
            warnings=[*warnings, _insufficient_warning(failure, what="forecast")],
            quality=MetricQuality.INCOMPLETE,
        ).rolled_up()

    earliest = _earliest_history_day(ctx, source, anchor)
    history_window = _history_window(anchor, history_days, earliest)
    _guard_history_generations(ctx, source, history_window)
    history = _daily_values(ctx, source, column, history_window, covered_through=anchor)

    # Today is still being written, so its partial total would drag the trend
    # down for no reason other than the hour of day the report was run.
    excluded_today = ctx.today in history
    if excluded_today:
        history.pop(ctx.today)

    model = forecasting.fit(
        history,
        min_days=min_days,
        confidence=confidence,
        non_negative=True,
        max_horizon_days=horizon_days,
    )

    actual_points = _actual_points(ctx, source, column, anchor, actual_key, earliest)

    if not model.ok:
        # No forecast key, no empty list, no zeros. The actuals stand alone and
        # the warning says what is missing and by how much.
        series = {series_id: actual_points} if actual_points else {}
        return ResolverResult(
            kpis=bundle.kpis,
            series=series,
            sources=_dedupe_sources(state.ref, *bundle.sources),
            warnings=[*warnings, _insufficient_warning(model, what="forecast")],
            quality=MetricQuality.INCOMPLETE,
        ).rolled_up()

    horizon = min(horizon_days, model.max_horizon_days)
    points = forecasting.forecast(model, horizon)

    forecast_points = [
        {
            "date": p.day.isoformat(),
            actual_key: None,
            forecast_key: _q(p.point, _MONEY_Q),
            "forecast_lower": _q(p.lower, _MONEY_Q),
            "forecast_upper": _q(p.upper, _MONEY_Q),
            "is_forecast": True,
            "horizon_days": p.horizon,
            "quality": p.quality,
        }
        for p in points
    ]

    warnings.extend(_method_warnings(model, horizon))
    if excluded_today:
        warnings.append(
            warn(
                WarningCode.PARTIAL_TODAY,
                f"{ctx.today.isoformat()} is still being written and was excluded "
                "from the fit; a part-day total would bias the trend downwards by "
                "however many hours are left in the day.",
                severity="info",
                excluded_day=ctx.today.isoformat(),
            )
        )
    if model.coverage < 1.0:
        warnings.append(
            warn(
                WarningCode.SMALL_SAMPLE,
                f"The fit used {model.observed_days} measured days across a "
                f"{model.span_days}-day span ({model.coverage:.0%} coverage). The "
                "intervals are widened by 1/sqrt(coverage) to account for the "
                "missing days rather than treating them as zeros.",
                severity="warn",
                observed_days=model.observed_days,
                span_days=model.span_days,
                coverage_pct=round(model.coverage * 100, 2),
            )
        )

    return ResolverResult(
        kpis=bundle.kpis,
        series={series_id: [*actual_points, *forecast_points]},
        sources=_dedupe_sources(state.ref, *bundle.sources, _forecast_source_ref(model)),
        warnings=warnings,
        # A view carrying a projection is at best ESTIMATED, whatever the
        # measured half of it is worth.
        quality=MetricQuality.ESTIMATED,
    ).rolled_up()


def _actual_points(
    ctx: ResolverContext,
    source: str,
    column: str,
    anchor: date,
    actual_key: str,
    earliest: date | None,
) -> list[dict[str, Any]]:
    """Measured days inside the requested window, stopping at the watermark."""
    start = ctx.window.date_from
    if earliest is not None and earliest > start:
        # Same clamp as the fit window: days before the rollup's first bucket
        # were never aggregated, and drawing them at zero reads as a stretch of
        # no trade rather than as an absence of data.
        start = earliest
    if start > anchor:
        return []
    window = ResolvedWindow(date_from=start, date_to=anchor + timedelta(days=1))
    measured = _daily_values(ctx, source, column, window, covered_through=anchor)
    return [
        {
            "date": day.isoformat(),
            actual_key: value,
            "is_forecast": False,
            "quality": MetricQuality.AUTHORITATIVE.value,
        }
        for day, value in sorted(measured.items())
    ]


# ===========================================================================
# 56. Seasonality
# ===========================================================================


@custom_function("seasonality")
def seasonality(ctx: ResolverContext) -> ResolverResult:
    """Day-of-week and month-of-year shape, plus an honest year-over-year answer.

    Three separate questions with three separate bars, because they need very
    different amounts of history and collapsing them would let the easiest one
    vouch for the hardest:

    * **Day of week** needs four observations of every weekday (28 days).
    * **Month of year** needs a full calendar year, so that every month has been
      observed at all. Without one, "December is the biggest month" is a fact
      about when the store launched.
    * **Year over year** needs a full prior year before the window on screen.
      Where it is missing this returns PARTIAL and names the shortfall in days.
      It does **not** fall back to the previous quarter, month or period: that
      comparison renders identically and means something else entirely.
    """
    source = str(ctx.param("source") or DEFAULT_SOURCE)
    column = str(ctx.param("seasonality_column") or "net_revenue")
    history_days = min(
        MAX_HISTORY_DAYS, int(ctx.param("history_days") or MAX_HISTORY_DAYS)
    )

    state = probe_source(ctx, source, label="Order rollup (daily)")
    warnings: list[AnalyticsWarning] = list(state.warnings)
    bundle = compute_kpis(ctx, ctx.view.kpis)
    warnings.extend(bundle.warnings)

    anchor = _anchor(ctx, state)
    if anchor is None:
        yoy = forecasting.year_over_year_readiness(
            earliest_history=None,
            window_from=ctx.window.date_from,
            window_to=ctx.window.date_to,
        )
        return ResolverResult(
            kpis=bundle.kpis,
            sources=_dedupe_sources(state.ref, *bundle.sources),
            warnings=[*warnings, _yoy_warning(yoy)],
            quality=MetricQuality.INCOMPLETE,
        ).rolled_up()

    earliest = _earliest_history_day(ctx, source, anchor)
    history_window = _history_window(anchor, history_days, earliest)
    _guard_history_generations(ctx, source, history_window)
    history = _daily_values(ctx, source, column, history_window, covered_through=anchor)

    index = forecasting.seasonal_index(history)
    yoy = forecasting.year_over_year_readiness(
        earliest_history=earliest,
        window_from=ctx.window.date_from,
        window_to=ctx.window.date_to,
    )

    series: dict[str, list[dict]] = {}
    actual_points = _actual_points(ctx, source, column, anchor, column, earliest)
    if actual_points:
        series[str(ctx.param("actuals_series_id") or "seasonal_revenue")] = actual_points

    if index.day_of_week is not None:
        series["dow_index"] = [
            {
                "weekday": d,
                "label": forecasting.WEEKDAY_NAMES[d],
                "index": _q(index.day_of_week[d], _INDEX_Q),
                "observations": index.day_of_week_observations[d],
                "quality": MetricQuality.ESTIMATED.value,
            }
            for d in range(7)
        ]
    if index.month_of_year is not None:
        series["month_index"] = [
            {
                "month": m,
                "label": forecasting.MONTH_NAMES[m - 1],
                "index": _q(index.month_of_year[m], _INDEX_Q),
                "observations": index.month_of_year_observations[m],
                "quality": MetricQuality.ESTIMATED.value,
            }
            for m in range(1, 13)
        ]

    for missing in index.missing:
        # One warning per index, each naming its own shortfall. A single "some
        # seasonality is unavailable" would leave the reader unable to tell which
        # of the two charts on screen is the one they cannot trust.
        warnings.append(
            warn(
                SEASONALITY_INDEX_UNAVAILABLE,
                missing,
                severity="warn",
                observed_days=index.observed_days,
                span_days=index.span_days,
                earliest=index.earliest.isoformat() if index.earliest else None,
                latest=index.latest.isoformat() if index.latest else None,
            )
        )

    if not yoy.ready:
        warnings.append(_yoy_warning(yoy))

    # PARTIAL whenever any of the three questions is unanswered: the view exists
    # to answer them, and a screen showing only the one that worked reads as a
    # complete answer.
    incomplete = bool(index.missing) or not yoy.ready
    quality = MetricQuality.INCOMPLETE if incomplete else MetricQuality.ESTIMATED

    sources = _dedupe_sources(state.ref, *bundle.sources)
    if index.has_any:
        sources.append(
            source_ref(
                f"seasonality:{index.method_id}",
                label="Seasonal index — normalised multiplicative factors",
                kind="derived",
                rows=index.observed_days,
                through=index.latest,
            )
        )

    return ResolverResult(
        kpis=bundle.kpis,
        series=series,
        sources=sources,
        warnings=warnings,
        quality=quality,
    ).rolled_up()


def _yoy_warning(yoy: forecasting.YoyReadiness) -> AnalyticsWarning:
    """The PARTIAL statement for a missing prior year.

    `state` is carried in the detail so the frontend can render the view's
    availability from the same fact the message describes, rather than inferring
    it from the absence of a series.
    """
    return warn(
        SEASONALITY_PARTIAL,
        " ".join(yoy.missing),
        severity="warn",
        state=ViewState.PARTIAL.value,
        missing=list(yoy.missing),
        substituted_comparison=None,
        **yoy.as_detail(),
    )


# ===========================================================================
# 31. Demand forecasting (per product) — gated, and honest about why
# ===========================================================================


@custom_function("demand_forecast")
def demand_forecast(ctx: ResolverContext) -> ResolverResult:
    """Per-product demand. Gated: the inventory history it needs does not exist.

    The maths would be the same engine, run per product. What is missing is the
    input. ``agg_inventory_daily`` is **forward-only** by construction — stock
    levels before the ledger job first ran cannot be reconstructed, because
    restocks and manual stock edits were never recorded — so the history starts
    on the day the job started and grows a day at a time from there.

    That matters more for demand than for revenue. A demand forecast that ignores
    stock-outs learns that a product which was unavailable for three weeks has no
    demand, and then recommends not reordering it. The correction requires knowing
    which days it was out of stock, which is exactly the history that does not
    exist yet.

    Rather than project anyway, this returns the requirement with the real
    numbers in it: what the ledger holds today, and what it would have to hold.
    """
    from app.services.analytics.aggregation.jobs_ops import (
        INVENTORY_HISTORY_SINCE_KEY,
    )
    from app.services.settings_service import SettingsService

    source = str(ctx.param("inventory_source") or "agg_inventory_daily")
    min_days = int(ctx.param("min_history_days") or forecasting.MIN_HISTORY_DAYS)

    watermark, rows = ctx.repo.source_watermark(source, ctx.tz_generation)

    raw_since = SettingsService(ctx.db).get_raw(INVENTORY_HISTORY_SINCE_KEY)
    since: date | None = None
    if raw_since:
        try:
            since = date.fromisoformat(raw_since.strip())
        except ValueError:
            # A corrupted marker must not be interpreted; the gate stands either
            # way and the raw value is reported so somebody can fix it.
            since = None

    days_available = 0
    if since is not None and watermark is not None:
        days_available = max(0, (watermark - since).days + 1)

    # The registry limitation is a prefix, never a replacement. It says what the
    # admin sees on the card; the sentences below say why the number cannot exist,
    # and dropping them whenever a limitation happens to be set would lose the
    # only part of this message that answers "so what would fix it".
    preamble = f"{ctx.view.limitation.rstrip()} " if ctx.view.limitation else ""

    return not_configured(
        preamble
        + (
            "Per-product demand forecasting needs a demand and stock-out history "
            "per SKU, and the inventory ledger is forward-only: stock levels "
            "before it started cannot be reconstructed, because restocks and "
            "manual edits were never recorded."
        )
        + f" The engine needs {min_days} days of per-product history (four "
        f"complete weeks, so every weekday is seen four times) and the ledger "
        f"currently holds {days_available}. Until then no per-product projection "
        "is produced — a forecast that cannot see stock-outs learns that a "
        "product which was unavailable has no demand, and recommends not "
        "reordering it.",
        requires=[
            Capability.INVENTORY_LEDGER.value,
            Capability.ORDER_LINE_FACT.value,
        ],
        detail={
            "view": ctx.view.slug,
            "method_id": forecasting.METHOD_ID,
            "method": forecasting.METHOD_NAME,
            "required_days": min_days,
            "days_available": days_available,
            "short_by_days": max(0, min_days - days_available),
            "inventory_history_since": since.isoformat() if since else None,
            "inventory_history_since_raw": raw_since,
            "inventory_watermark": watermark.isoformat() if watermark else None,
            "inventory_rows": rows,
            "blocking_input": "per-product stock-out days (agg_inventory_daily.is_oos)",
        },
    )
