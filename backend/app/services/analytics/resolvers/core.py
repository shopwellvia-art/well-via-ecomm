"""The four reusable resolvers: metrics, timeseries, breakdown, table.

Between them these answer most of the 73 views. Adding a fifth shape here is
cheap and reusable; adding a bespoke React component is neither, which is why
the registry names a resolver rather than a component.

Metric bindings, and why they are not formulas
----------------------------------------------
`kpis.py` owns what a number *means* — the formula, the status set, the tax and
refund treatment, the honest quality grade. This module owns only the far
smaller question of *which stored columns* that formula corresponds to, because
`kpis.py` is deliberately import-free of SQLAlchemy and cannot name a rollup
column. `METRIC_BINDINGS` is therefore a lookup from a catalogue id to a
numerator/denominator pair of columns that already exist in
`analytics_rollups.py`. It never restates a formula and it never invents one: a
KPI with no binding is reported as **not computable**, with the missing input
named, rather than approximated.

That falling-through matters more than the bindings themselves. The failure mode
this file is built against is a KPI card that quietly shows 0 because nothing
was wired up — which looks exactly like a bad week and is indistinguishable from
one at a glance.

The densification rule
----------------------
`timeseries` fills gaps **only inside the source's watermark range**. Past the
watermark there is no data, and filling zeros there draws a confident flat line
through a hole. Inside the range a missing day genuinely is a zero — the
aggregation covered that day and found nothing — so it is filled. Those two
sentences are the entire difference between an honest chart and a lying one, and
`dashboard_service._revenue_series` densifies unconditionally, which is the bug
being avoided here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from app.schemas.analytics_view import (
    AnalyticsWarning,
    KpiValue,
    SourceRef,
    TableBlock,
    WarningCode,
)
from app.services.analytics.export import clamp_row_limit
from app.services.analytics.filters import Granularity, ResolvedWindow
from app.services.analytics.resolvers.base import (
    ResolverContext,
    ResolverError,
    ResolverResult,
    SourceState,
    build_kpi,
    missing_kpi,
    not_configured,
    probe_source,
    register,
    warn,
)
from app.services.analytics.types import MetricQuality, ResolverId, TableSpec

__all__ = [
    "MetricBinding",
    "METRIC_BINDINGS",
    "DIMENSION_BINDINGS",
    "DEFAULT_SOURCE",
    "MetricsResolver",
    "TimeseriesResolver",
    "BreakdownResolver",
    "TableResolver",
    "compute_kpis",
    "dense_points",
    "evaluate",
    "MetricOutcome",
    "METRIC_NOT_BOUND",
]

#: The rollup a view reads unless its registry entry names another. Every
#: top-line money and order figure lives here (see `AggOrderDaily`).
DEFAULT_SOURCE = "agg_order_daily"

#: Emitted when the requested granularity has no rollup at that grain. Serving a
#: coarser bucket without saying so would silently change what the chart means.
GRANULARITY_DOWNGRADED = "GRANULARITY_DOWNGRADED"

#: Emitted when a KPI or series the view asked for has no binding to a stored
#: column. Named rather than dropped: a card that silently disappears is a bug
#: report nobody files.
METRIC_NOT_BOUND = "METRIC_NOT_BOUND"

_HUNDRED = Decimal("100")
_PCT_Q = Decimal("0.0001")
_MONEY_Q = Decimal("0.01")


# ---------------------------------------------------------------------------
# Bindings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricBinding:
    """Which stored columns a catalogue KPI is computed from.

    Everything is expressed as *sums of stored columns*, never as a stored
    average or a stored percentage, because the rollups deliberately store
    numerators and denominators separately: re-bucketing a daily figure to a
    week and then averaging the averages is wrong, and wrong silently. Summing
    the components first and dividing once at the end is the only re-aggregable
    order of operations.

    `coverage` names the (measured, total) pair for metrics that can be computed
    over only part of their population — cost coverage on margin, above all.
    Zero coverage means the metric is *missing*, not zero; partial coverage
    means it is reported with an INCOMPLETE grade and the percentage stated.
    """

    source: str
    #: Columns summed into the numerator.
    add: tuple[str, ...]
    #: Columns subtracted from the numerator.
    sub: tuple[str, ...] = ()
    #: Columns summed into the denominator. Empty means an absolute figure.
    over_add: tuple[str, ...] = ()
    over_sub: tuple[str, ...] = ()
    #: 100 turns a ratio into a percentage.
    scale: Decimal = Decimal("1")
    #: True for "1 - x" style rates (abandonment). Value becomes scale - value.
    complement: bool = False
    #: (measured_column, population_column) for partial-coverage metrics.
    coverage: tuple[str, str] | None = None
    #: Named in `inputs_missing` when coverage is zero.
    coverage_input: str = ""
    #: Overrides the catalogue's default grade when the binding is materially
    #: weaker than the definition assumes (a non-additive denominator).
    quality: MetricQuality | None = None

    @property
    def columns(self) -> tuple[str, ...]:
        cols = [*self.add, *self.sub, *self.over_add, *self.over_sub]
        if self.coverage:
            cols.extend(self.coverage)
        seen: list[str] = []
        for c in cols:
            if c not in seen:
                seen.append(c)
        return tuple(seen)

    @property
    def is_ratio(self) -> bool:
        return bool(self.over_add or self.over_sub)


def _money(source: str, column: str, **kw: Any) -> MetricBinding:
    return MetricBinding(source=source, add=(column,), **kw)


#: Catalogue id -> stored columns. See the module docstring: this is a binding,
#: not a definition. A KPI absent from here is reported as not computable.
METRIC_BINDINGS: Mapping[str, MetricBinding] = {
    # -- agg_order_daily: the revenue bridge and order volume ---------------
    "order_value_created": _money(DEFAULT_SOURCE, "order_value_created"),
    "paid_order_value": _money(DEFAULT_SOURCE, "paid_order_value"),
    "gross_merchandise_sales": _money(DEFAULT_SOURCE, "gross_merchandise_sales"),
    "net_merchandise_sales": _money(DEFAULT_SOURCE, "net_merchandise_sales"),
    "net_revenue": _money(DEFAULT_SOURCE, "net_revenue"),
    "refunds_value": _money(DEFAULT_SOURCE, "refund_sum"),
    "discount_value": _money(DEFAULT_SOURCE, "discount_sum"),
    "tax_collected": _money(DEFAULT_SOURCE, "tax_sum"),
    "shipping_income": _money(DEFAULT_SOURCE, "shipping_income"),
    "cod_surcharge_collected": _money(DEFAULT_SOURCE, "cod_surcharge_sum"),
    "orders_count": MetricBinding(DEFAULT_SOURCE, add=("orders_total",)),
    "units_sold": MetricBinding(DEFAULT_SOURCE, add=("units",)),
    "new_customers": MetricBinding(DEFAULT_SOURCE, add=("new_customers",)),
    "returning_customers": MetricBinding(DEFAULT_SOURCE, add=("returning_customers",)),
    # aov = paid_order_value / paid orders. "Paid" is the canonical
    # PAID/SHIPPED/DELIVERED status set, which the rollup stores as three
    # separate counters, so the denominator is their sum.
    "aov": MetricBinding(
        DEFAULT_SOURCE,
        add=("paid_order_value",),
        over_add=("orders_paid", "orders_shipped", "orders_delivered"),
    ),
    "refund_rate": MetricBinding(
        DEFAULT_SOURCE, add=("refund_sum",), over_add=("paid_order_value",),
        scale=_HUNDRED,
    ),
    "cancellation_rate": MetricBinding(
        DEFAULT_SOURCE, add=("orders_cancelled",), over_add=("orders_total",),
        scale=_HUNDRED,
    ),
    "discount_rate": MetricBinding(
        DEFAULT_SOURCE, add=("discount_sum",), over_add=("gross_merchandise_sales",),
        scale=_HUNDRED,
    ),
    # Margin: computable only over lines that carried a unit_cost snapshot, so
    # both carry the coverage pair. Zero coverage is MISSING, never zero cost —
    # a zero COGS reports 100% margin, the one direction of error nobody
    # investigates.
    "cm1": MetricBinding(
        DEFAULT_SOURCE,
        add=("net_merchandise_sales",),
        sub=("cogs_sum",),
        coverage=("costed_units", "units"),
        coverage_input="unit_cost",
    ),
    "cm1_pct": MetricBinding(
        DEFAULT_SOURCE,
        add=("net_merchandise_sales",),
        sub=("cogs_sum",),
        over_add=("net_merchandise_sales",),
        scale=_HUNDRED,
        coverage=("costed_units", "units"),
        coverage_input="unit_cost",
    ),
    "gross_margin_pct": MetricBinding(
        DEFAULT_SOURCE,
        add=("net_merchandise_sales",),
        sub=("cogs_sum",),
        over_add=("net_merchandise_sales",),
        scale=_HUNDRED,
        coverage=("costed_units", "units"),
        coverage_input="unit_cost",
    ),
    # -- agg_shipment_daily ------------------------------------------------
    # The denominator is the shipment population that reached a terminal
    # outcome, exactly as `AggShipmentDaily` documents the rate.
    "rto_rate": MetricBinding(
        "agg_shipment_daily",
        add=("rto_initiated",),
        over_add=("delivered", "rto_initiated"),
        scale=_HUNDRED,
    ),
    "delivery_success_rate": MetricBinding(
        "agg_shipment_daily",
        add=("delivered",),
        over_add=("shipments",),
        scale=_HUNDRED,
    ),
    "shipping_cost": _money("agg_shipment_daily", "shipment_cost"),
    # -- agg_payment_daily -------------------------------------------------
    "payment_success_rate": MetricBinding(
        "agg_payment_daily", add=("paid",), over_add=("attempts",), scale=_HUNDRED,
    ),
    "payment_failure_rate": MetricBinding(
        "agg_payment_daily", add=("failed",), over_add=("attempts",), scale=_HUNDRED,
    ),
    # -- agg_promo_daily ---------------------------------------------------
    "coupon_discount_value": _money("agg_promo_daily", "discount_amount"),
    "coupon_orders": MetricBinding("agg_promo_daily", add=("orders",)),
    "coupon_revenue": _money("agg_promo_daily", "order_revenue"),
}


@dataclass(frozen=True)
class DimensionBinding:
    """A breakdown label -> the rollup and column that groups by it.

    The client may send `dimension=courier`. It can never send a column name:
    this dict is the only path from a label to an identifier, and the repository
    validates the result against its own reflected allowlist a second time.
    """

    source: str
    column: str
    label: str = ""


DIMENSION_BINDINGS: Mapping[str, DimensionBinding] = {
    "date": DimensionBinding(DEFAULT_SOURCE, "bucket_date", "Date"),
    "hour": DimensionBinding("agg_order_hourly", "bucket_hour", "Hour of day"),
    "product": DimensionBinding("agg_product_daily", "product_id", "Product"),
    "sku": DimensionBinding("agg_product_daily", "sku_snapshot", "SKU"),
    "category": DimensionBinding(
        "agg_product_daily", "category_id_snapshot", "Category"
    ),
    "coupon": DimensionBinding("agg_promo_daily", "coupon_code", "Coupon"),
    "courier": DimensionBinding("agg_shipment_daily", "courier_partner", "Courier"),
    "payment_gateway": DimensionBinding("agg_payment_daily", "gateway", "Gateway"),
    "payment_method": DimensionBinding(
        "agg_payment_daily", "payment_method", "Payment method"
    ),
    "payment_instrument": DimensionBinding(
        "agg_payment_daily", "payment_instrument", "Instrument"
    ),
    "state": DimensionBinding("agg_geo_daily", "state", "State"),
    "pincode": DimensionBinding("agg_geo_daily", "pincode", "Pincode"),
    "cohort_month": DimensionBinding(
        "agg_customer_cohort_monthly", "cohort_month", "Cohort"
    ),
    "customer_segment": DimensionBinding(
        "agg_customer_snapshot", "rfm_segment", "Segment"
    ),
    # Aliases for the labels registry charts use on their x axis. Each one is an
    # exact synonym of the entry above it — never a near-enough substitute, which
    # would silently regroup a chart by something adjacent to what it claims.
    "segment": DimensionBinding("agg_customer_snapshot", "rfm_segment", "Segment"),
    "cohort": DimensionBinding(
        "agg_customer_cohort_monthly", "cohort_month", "Cohort"
    ),
    "rfm_segment": DimensionBinding("agg_customer_snapshot", "rfm_segment", "Segment"),
    "churn_risk_band": DimensionBinding(
        "agg_customer_snapshot", "churn_risk_band", "Churn risk"
    ),
}


# ---------------------------------------------------------------------------
# Binding resolution + arithmetic
# ---------------------------------------------------------------------------


def _binding_from_param(spec: Any, default_source: str) -> MetricBinding:
    """Build a binding from a SERVER-TRUSTED registry `params` entry.

    Accepts either a bare column name or a dict naming numerator/denominator
    columns. These come from `registry.py`; the repository re-validates every
    name against its allowlist, so a typo in a registry entry fails loudly at
    query time instead of reaching SQL.
    """
    if isinstance(spec, str):
        return MetricBinding(source=default_source, add=(spec,))
    if not isinstance(spec, Mapping):
        raise ResolverError(f"metric binding must be a column name or a mapping: {spec!r}")
    return MetricBinding(
        source=str(spec.get("source") or default_source),
        add=tuple(spec.get("add") or ()),
        sub=tuple(spec.get("sub") or ()),
        over_add=tuple(spec.get("over") or spec.get("over_add") or ()),
        over_sub=tuple(spec.get("over_sub") or ()),
        scale=Decimal(str(spec.get("scale", 1))),
        complement=bool(spec.get("complement", False)),
    )


def binding_for(
    ctx: ResolverContext, metric_id: str, *, default_source: str | None = None
) -> MetricBinding | None:
    """The binding for one metric: registry params first, catalogue second."""
    declared = ctx.params.get("metrics")
    fallback = default_source or str(ctx.param("source") or DEFAULT_SOURCE)
    if isinstance(declared, Mapping) and metric_id in declared:
        return _binding_from_param(declared[metric_id], fallback)
    return METRIC_BINDINGS.get(metric_id)


def _sum(values: Mapping[str, Any], names: Sequence[str]) -> Decimal | None:
    """Sum stored columns, preserving "nothing was measured" as None.

    Every column in one `fetch_totals` call is summed over the same rows, so
    they are all NULL together. All-NULL means the window held no rows — which
    this function reports as None and NEVER as 0, because a fabricated zero and
    a real zero render identically and only one of them is true.
    """
    if not names:
        return Decimal("0")
    present = [values.get(n) for n in names]
    if all(v is None for v in present):
        return None
    return sum((Decimal(str(v)) for v in present if v is not None), Decimal("0"))


@dataclass(frozen=True)
class MetricOutcome:
    """The computed value plus everything needed to grade it honestly."""

    value: Decimal | None = None
    coverage_pct: Decimal | None = None
    inputs_missing: tuple[str, ...] = ()
    quality: MetricQuality | None = None


def evaluate(binding: MetricBinding, totals: Mapping[str, Any]) -> MetricOutcome:
    """Apply one binding to a row (or window total) of stored columns."""
    numerator = _sum(totals, binding.add)
    subtrahend = _sum(totals, binding.sub) if binding.sub else Decimal("0")

    if numerator is None or subtrahend is None:
        # Nothing was measured. Name the source rather than showing a zero.
        return MetricOutcome(inputs_missing=(binding.source,))

    coverage_pct: Decimal | None = None
    quality = binding.quality
    if binding.coverage:
        measured = _sum(totals, (binding.coverage[0],))
        population = _sum(totals, (binding.coverage[1],))
        if population is None or population == 0:
            coverage_pct = None
        else:
            coverage_pct = (
                (measured or Decimal("0")) / population * _HUNDRED
            ).quantize(_MONEY_Q)
            if coverage_pct == 0:
                # No line in the window carried the input at all. A margin
                # computed against zero cost reports 100% and reads better than
                # reality; report it missing instead.
                return MetricOutcome(
                    coverage_pct=coverage_pct,
                    inputs_missing=(binding.coverage_input or binding.coverage[0],),
                )
            if coverage_pct < _HUNDRED:
                quality = MetricQuality.INCOMPLETE

    value = numerator - subtrahend

    if binding.is_ratio:
        denominator = (_sum(totals, binding.over_add) or Decimal("0")) - (
            _sum(totals, binding.over_sub) or Decimal("0")
        )
        if denominator == 0:
            # An undefined ratio over an empty base. Not zero — a period with no
            # sales has no margin percentage, and drawing 0% would be a claim.
            return MetricOutcome(
                coverage_pct=coverage_pct, quality=MetricQuality.INCOMPLETE
            )
        value = (value / denominator * binding.scale).quantize(_PCT_Q)
    elif binding.scale != 1:
        value = (value * binding.scale).quantize(_PCT_Q)
    else:
        value = value.quantize(_MONEY_Q) if value % 1 else value

    if binding.complement:
        value = binding.scale - value

    return MetricOutcome(value=value, coverage_pct=coverage_pct, quality=quality)


# ---------------------------------------------------------------------------
# Shared KPI computation
# ---------------------------------------------------------------------------


@dataclass
class _Bundle:
    """Accumulates KPIs, provenance and warnings across several sources."""

    kpis: dict[str, KpiValue] = field(default_factory=dict)
    sources: list[SourceRef] = field(default_factory=list)
    warnings: list[AnalyticsWarning] = field(default_factory=list)


def _compare_window(window: ResolvedWindow) -> ResolvedWindow | None:
    if not window.has_comparison:
        return None
    return ResolvedWindow(
        date_from=window.compare_from,  # type: ignore[arg-type]
        date_to=window.compare_to,  # type: ignore[arg-type]
    )


def compute_kpis(
    ctx: ResolverContext, kpi_ids: Iterable[str], *, default_source: str | None = None
) -> _Bundle:
    """KPI cards for `kpi_ids` over the window and its comparison window.

    One `fetch_totals` per distinct source rather than per KPI, so a five-card
    view built entirely on `agg_order_daily` is two queries (window + comparison)
    and not ten.
    """
    bundle = _Bundle()
    wanted = [k for k in kpi_ids if k]
    if not wanted:
        return bundle

    by_source: dict[str, list[tuple[str, MetricBinding]]] = {}
    unbound: list[str] = []
    for kpi_id in wanted:
        binding = binding_for(ctx, kpi_id, default_source=default_source)
        if binding is None:
            # Named, not dropped, and never zeroed: the reader must be able to
            # see that this number is unavailable rather than nil.
            bundle.kpis[kpi_id] = missing_kpi(kpi_id, "no rollup binding")
            unbound.append(kpi_id)
            continue
        by_source.setdefault(binding.source, []).append((kpi_id, binding))

    unbound.sort()
    if unbound:
        bundle.warnings.append(
            warn(
                METRIC_NOT_BOUND,
                "These metrics have no binding to a stored column and are reported "
                "as unavailable rather than zero: " + ", ".join(unbound),
                severity="warn",
                metrics=unbound,
            )
        )

    compare = _compare_window(ctx.window)

    for source, entries in by_source.items():
        state = probe_source(ctx, source)
        bundle.sources.append(state.ref)
        bundle.warnings.extend(state.warnings)

        columns = sorted({c for _, b in entries for c in b.columns})
        now_totals = ctx.repo.fetch_totals(
            source,
            columns=columns,
            window=ctx.window,
            tz_generation=ctx.tz_generation,
        )
        prev_totals: Mapping[str, Any] = {}
        if compare is not None:
            prev_totals = ctx.repo.fetch_totals(
                source,
                columns=columns,
                window=compare,
                tz_generation=ctx.tz_generation,
            )

        for kpi_id, binding in entries:
            now = evaluate(binding, now_totals)
            previous = evaluate(binding, prev_totals) if prev_totals else MetricOutcome()
            bundle.kpis[kpi_id] = build_kpi(
                kpi_id,
                now.value,
                previous=previous.value,
                coverage_pct=now.coverage_pct,
                inputs_missing=now.inputs_missing,
                quality=now.quality,
            )
    return bundle


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


class MetricsResolver:
    """KPI cards for the window plus its comparison window, with deltas.

    The delta comes from `dashboard_service._pct_delta` unchanged, which returns
    None when the previous value is <= 0. That is the correct answer, not a
    limitation: growth from zero is undefined. Rendering it as +100%, or as
    infinity, or as 0, all invent a fact.
    """

    id = ResolverId.METRICS.value

    def run(self, ctx: ResolverContext) -> ResolverResult:
        kpi_ids = ctx.param("kpis") or ctx.view.kpis
        bundle = compute_kpis(ctx, kpi_ids)
        return ResolverResult(
            kpis=bundle.kpis,
            sources=bundle.sources,
            warnings=bundle.warnings,
        ).rolled_up()


# ---------------------------------------------------------------------------
# timeseries
# ---------------------------------------------------------------------------


def _bucket_start(day: date, granularity: Granularity) -> date:
    if granularity is Granularity.WEEK:
        return day - timedelta(days=day.weekday())
    if granularity is Granularity.MONTH:
        return day.replace(day=1)
    return day


def _next_bucket(start: date, granularity: Granularity) -> date:
    if granularity is Granularity.WEEK:
        return start + timedelta(days=7)
    if granularity is Granularity.MONTH:
        return (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start + timedelta(days=1)


def dense_points(
    rows: list[dict],
    window: ResolvedWindow,
    covered_through: date,
    granularity: Granularity,
    bindings: Mapping[str, MetricBinding],
) -> list[dict]:
    """Bucket rows and fill gaps — but ONLY up to `covered_through`.

    `covered_through` is `min(source watermark, last requested day)`. The loop
    below stops there, so a window that runs past the watermark simply produces
    a shorter series. That single bound is what separates "we have not computed
    those days" from "you sold nothing on those days"; the two are visually
    identical once a zero is written, and no downstream layer can undo it.

    Inside the bound, a bucket with no row IS a zero for an absolute measure —
    the aggregation covered it and found nothing. A *rate* over an empty bucket
    stays None, because a ratio with a zero denominator is undefined rather
    than 0%.

    Points carry the bucket under `_x`; the caller renames it to the chart's
    declared x axis.
    """
    totals: dict[date, dict[str, Any]] = {}
    for row in rows:
        bucket = _bucket_start(row["bucket_date"], granularity)
        acc = totals.setdefault(bucket, {})
        for key, value in row.items():
            if key == "bucket_date" or value is None:
                continue
            acc[key] = Decimal(str(value)) + Decimal(str(acc.get(key, 0)))

    points: list[dict] = []
    cursor = _bucket_start(window.date_from, granularity)
    while cursor <= covered_through:
        measured = totals.get(cursor)
        point: dict[str, Any] = {"_x": cursor.isoformat()}
        for metric_id, binding in bindings.items():
            if measured is None:
                point[metric_id] = None if binding.is_ratio else Decimal("0")
            else:
                point[metric_id] = evaluate(binding, measured).value
        points.append(point)
        cursor = _next_bucket(cursor, granularity)
    return points


class TimeseriesResolver:
    """One bucketed series per chart, densified ONLY inside the watermark.

    The densification boundary is the whole point of this resolver, so it is
    spelled out here rather than left to a reader of the code:

    * **No watermark at all** -> the series is `[]` and the result carries
      `NO_ROLLUP_YET`. Not `[{date: d, value: 0}, ...]`. Nothing has been
      aggregated, so there is no zero to report.
    * **Watermark before the end of the window** -> buckets are emitted up to
      the watermark and stop. The chart ends early, which is true, instead of
      running flat along zero, which is not. `ROLLUP_STALE` says how far it got.
    * **A gap inside the covered range** -> filled with zero. The aggregation
      covered that day and found nothing, so zero is a measurement.

    Ratios are recomputed per bucket from their stored numerator and denominator
    rather than averaged across buckets — an average of daily AOVs is not the
    period AOV, and the error is invisible.
    """

    id = ResolverId.TIMESERIES.value

    def run(self, ctx: ResolverContext) -> ResolverResult:
        source = str(ctx.param("source") or DEFAULT_SOURCE)
        granularity = ctx.filters.granularity
        warnings: list[AnalyticsWarning] = []

        # Hour grain needs a rollup that has an hour column. Downgrading is
        # announced: silently serving days for an hourly request changes what
        # the chart means without changing how it looks.
        if granularity is Granularity.HOUR:
            hourly = ctx.param("hourly_source") or (
                "agg_order_hourly" if source == DEFAULT_SOURCE else None
            )
            if hourly:
                source = str(hourly)
            else:
                granularity = Granularity.DAY
                warnings.append(
                    warn(
                        GRANULARITY_DOWNGRADED,
                        f"{source} has no hourly grain; this series is bucketed by "
                        "day. Hourly detail would need an hourly rollup.",
                        severity="info",
                        source=source,
                        requested="hour",
                        served="day",
                    )
                )

        state = probe_source(ctx, source)
        warnings.extend(state.warnings)

        charts = ctx.view.charts or ()
        time_charts = [c for c in charts if c.x in ("date", "hour", "month", "week")]
        metric_ids: list[str] = []
        for chart in time_charts:
            metric_ids.extend(chart.series)
        if not metric_ids:
            metric_ids = list(ctx.param("series") or ctx.view.kpis)

        bindings: dict[str, MetricBinding] = {}
        unbound: list[str] = []
        for metric_id in dict.fromkeys(metric_ids):
            binding = binding_for(ctx, metric_id, default_source=source)
            if binding is None or binding.source != source:
                unbound.append(metric_id)
                continue
            bindings[metric_id] = binding
        if unbound:
            warnings.append(
                warn(
                    METRIC_NOT_BOUND,
                    f"No series drawn for {', '.join(sorted(unbound))}: not stored "
                    f"in {source}. An absent line is honest; a flat zero is not.",
                    severity="warn",
                    metrics=sorted(unbound),
                    source=source,
                )
            )

        kpi_bundle = compute_kpis(ctx, ctx.view.kpis)

        points = self._points(ctx, source, state, granularity, bindings, warnings)

        series: dict[str, list[dict]] = {}
        if points is not None:
            for chart in time_charts:
                keys = [m for m in chart.series if m in bindings]
                series[chart.id] = [
                    {chart.x: p["_x"], **{k: p.get(k) for k in keys}} for p in points
                ]
            if not time_charts:
                series[str(ctx.param("series_id") or "series")] = [
                    {"date": p["_x"], **{k: p.get(k) for k in bindings}} for p in points
                ]

        return ResolverResult(
            kpis=kpi_bundle.kpis,
            series=series,
            sources=_merge_sources([state.ref], kpi_bundle.sources),
            warnings=[*warnings, *kpi_bundle.warnings],
        ).rolled_up()

    # -- internals ---------------------------------------------------------

    def _points(
        self,
        ctx: ResolverContext,
        source: str,
        state: SourceState,
        granularity: Granularity,
        bindings: Mapping[str, MetricBinding],
        warnings: list[AnalyticsWarning],
    ) -> list[dict] | None:
        """Bucketed points, or None when there is nothing honest to draw."""
        if not bindings:
            return None
        if not state.has_rows:
            # NO_ROLLUP_YET is already on the result from `probe_source`. An
            # empty list here would still let a chart render an axis, so the
            # series key is omitted entirely by the caller.
            return None

        covered_through = state.covered_through(ctx.window)
        if covered_through is None:
            # The rollup exists but stops before this window even begins.
            return None

        hourly = granularity is Granularity.HOUR
        columns = sorted({c for b in bindings.values() for c in b.columns})
        group_by = ["bucket_date", "bucket_hour"] if hourly else ["bucket_date"]

        rows = ctx.repo.fetch_rollup(
            source,
            columns=columns,
            window=ctx.window,
            tz_generation=ctx.tz_generation,
            group_by=group_by,
            filters=ctx.param("row_filters") or None,
        )

        if hourly:
            return self._hourly_points(rows, ctx.window, covered_through, bindings)
        return dense_points(rows, ctx.window, covered_through, granularity, bindings)

    def _hourly_points(
        self,
        rows: list[dict],
        window: ResolvedWindow,
        covered_through: date,
        bindings: Mapping[str, MetricBinding],
    ) -> list[dict]:
        totals = {
            (row["bucket_date"], int(row["bucket_hour"])): row for row in rows
        }
        points: list[dict] = []
        day = window.date_from
        while day <= covered_through:
            for hour in range(24):
                measured = totals.get((day, hour))
                point: dict[str, Any] = {"_x": f"{day.isoformat()}T{hour:02d}"}
                for metric_id, binding in bindings.items():
                    if measured is None:
                        point[metric_id] = None if binding.is_ratio else Decimal("0")
                    else:
                        point[metric_id] = evaluate(binding, measured).value
                points.append(point)
            day += timedelta(days=1)
        return points


def _merge_sources(*groups: Sequence[SourceRef]) -> list[SourceRef]:
    out: list[SourceRef] = []
    seen: set[str] = set()
    for group in groups:
        for ref in group:
            if ref.id in seen:
                continue
            seen.add(ref.id)
            out.append(ref)
    return out


# ---------------------------------------------------------------------------
# breakdown
# ---------------------------------------------------------------------------


class BreakdownResolver:
    """Top-N by a metric, grouped by one dimension.

    `truncated` is set by fetching one row past the cap, so the reader is told
    the table is a top-N rather than the whole population. A silently capped
    list is how "our five best products" becomes "the only five products we
    bothered to fetch".
    """

    id = ResolverId.BREAKDOWN.value

    def run(self, ctx: ResolverContext) -> ResolverResult:
        dimension = self._dimension(ctx)
        binding_dim = DIMENSION_BINDINGS.get(dimension)
        if binding_dim is None:
            # No rollup groups by this label. Reported as a wiring gap rather
            # than approximated with an adjacent dimension — regrouping a chart
            # by something *near* what it claims is worse than not drawing it.
            return not_configured(
                f"This view breaks down by {dimension!r}, which no rollup stores as "
                f"a dimension. Groupable dimensions: {', '.join(sorted(DIMENSION_BINDINGS))}.",
                requires=[f"dimension:{dimension}"],
                detail={"view": ctx.view.slug, "dimension": dimension},
            )
        source = str(ctx.param("source") or binding_dim.source)
        state = probe_source(ctx, source)
        warnings = list(state.warnings)

        metric_ids = self._metrics(ctx, source)
        bindings: dict[str, MetricBinding] = {}
        unbound: list[str] = []
        for metric_id in metric_ids:
            binding = binding_for(ctx, metric_id, default_source=source)
            if binding is None or binding.source != source:
                unbound.append(metric_id)
                continue
            bindings[metric_id] = binding
        if unbound:
            warnings.append(
                warn(
                    METRIC_NOT_BOUND,
                    f"{', '.join(sorted(unbound))} is not stored in {source}, so it "
                    "is omitted from this breakdown rather than shown as zero.",
                    severity="warn",
                    metrics=sorted(unbound),
                    source=source,
                )
            )
        if not bindings:
            # Every requested measure lives in a different rollup (or in none).
            # Substituting a same-sounding column at this grain would publish a
            # different number under the label the view advertises, so the view
            # reports itself unwired instead. Fixing it means adding `params` to
            # the registry entry, not loosening the binding rules here.
            return not_configured(
                f"None of {', '.join(sorted(metric_ids)) or 'this view'}'s measures are "
                f"stored in {source}, which is the rollup that groups by "
                f"{dimension!r}. The view needs an explicit metric binding.",
                requires=[f"binding:{source}"],
                detail={
                    "view": ctx.view.slug,
                    "dimension": dimension,
                    "source": source,
                    "metrics": sorted(metric_ids),
                },
            )

        kpi_bundle = compute_kpis(ctx, ctx.view.kpis)
        tables: dict[str, TableBlock] = {}
        series: dict[str, list[dict]] = {}

        if state.has_rows:
            # Clamped to the ceiling for THIS kind of request: 200 for a screen,
            # the export ceiling when `export_scope()` is open. A single constant
            # here would either cap the export at a top-200 or hand a dashboard
            # the whole table.
            limit = clamp_row_limit(ctx.filters.limit)
            primary = next(iter(bindings.values()))
            columns = sorted({c for b in bindings.values() for c in b.columns})
            rows = ctx.repo.fetch_rollup(
                source,
                columns=columns,
                window=ctx.window,
                tz_generation=ctx.tz_generation,
                group_by=[binding_dim.column],
                order_by=f"-{primary.add[0]}",
                # One past the cap: enough to know the cap bit, not enough to
                # be a second page.
                limit=limit + 1,
            )
            truncated = len(rows) > limit
            rows = rows[:limit]

            shaped = [
                {
                    dimension: row.get(binding_dim.column),
                    **{
                        metric_id: evaluate(binding, row).value
                        for metric_id, binding in bindings.items()
                    },
                }
                for row in rows
            ]
            block = TableBlock(
                rows=shaped, total_rows=len(shaped), truncated=truncated
            )
            tables[self._table_id(ctx)] = block
            if ctx.view.charts:
                series[ctx.view.charts[0].id] = shaped
            if truncated:
                warnings.append(
                    warn(
                        WarningCode.SMALL_SAMPLE,
                        f"Showing the top {limit} of a longer list; the remainder is "
                        "not included in these rows.",
                        severity="info",
                        dimension=dimension,
                        limit=limit,
                    )
                )

        return ResolverResult(
            kpis=kpi_bundle.kpis,
            series=series,
            tables=tables,
            sources=_merge_sources([state.ref], kpi_bundle.sources),
            warnings=[*warnings, *kpi_bundle.warnings],
        ).rolled_up()

    @staticmethod
    def _dimension(ctx: ResolverContext) -> str:
        """The grouping label: request first (if the view honours it), then the
        registry, then the view's own chart axis.

        A view honours only the filter keys it declares, so a `dimension` the
        registry never listed is ignored rather than rejected — a link copied
        between two views still opens, and an ignored key cannot change a number.
        """
        requested = ctx.filters.dimension
        honoured = {f.value for f in ctx.view.filters}
        if requested and requested in honoured and requested in DIMENSION_BINDINGS:
            return requested
        declared = ctx.param("dimension")
        if declared:
            return str(declared)
        if ctx.view.charts:
            return ctx.view.charts[0].x
        if ctx.view.tables and ctx.view.tables[0].columns:
            return ctx.view.tables[0].columns[0].key
        return "date"

    @staticmethod
    def _metrics(ctx: ResolverContext, source: str) -> list[str]:
        declared = ctx.param("metrics")
        if isinstance(declared, Mapping):
            return list(declared)
        if ctx.view.charts:
            return list(ctx.view.charts[0].series)
        return list(ctx.view.kpis)

    @staticmethod
    def _table_id(ctx: ResolverContext) -> str:
        if ctx.view.tables:
            return ctx.view.tables[0].id
        return str(ctx.param("table_id") or "breakdown")


# ---------------------------------------------------------------------------
# table
# ---------------------------------------------------------------------------


class TableResolver:
    """Paginated rows for one `TableSpec`.

    Sorting is validated **against the spec's declared columns**, not against
    the source's. The spec is what the client can see, so a sort key it does not
    declare is a client asking for something that is not on the page — refused
    loudly rather than quietly ignored, because a silently-ignored sort returns
    a different page 1 than the user asked for and nothing says so.
    """

    id = ResolverId.TABLE.value

    def run(self, ctx: ResolverContext) -> ResolverResult:
        spec = self._spec(ctx)
        if spec is None:
            return not_configured(
                f"View {ctx.view.slug!r} uses the table resolver but declares no "
                "TableSpec, so there is no table to fill.",
                requires=["registry:tables"],
                detail={"view": ctx.view.slug},
            )
        source = str(ctx.param("source") or DEFAULT_SOURCE)
        state = probe_source(ctx, source)
        warnings = list(state.warnings)

        column_map = self._column_map(ctx, spec, source)
        if not column_map:
            # Nothing on this spec maps to a stored column. An empty TableBlock
            # here would read as "we looked and there was nothing", so the table
            # is omitted entirely and the reason is returned in its place.
            return not_configured(
                f"Table {spec.id!r} declares no column that exists in {source}, so "
                "no rows can be read. This is 'not wired up', not 'no data'.",
                requires=[f"binding:{source}"],
                detail={"view": ctx.view.slug, "table": spec.id, "source": source},
            )

        sort_key, sort_dir = self._sort(ctx, spec, column_map, warnings)
        order_by = None
        if sort_key:
            order_by = f"{'-' if sort_dir == 'desc' else ''}{column_map[sort_key]}"

        limit = clamp_row_limit(ctx.filters.limit, default=spec.page_size or 25)
        offset = int(ctx.filters.offset or 0)
        group_by = list(ctx.param("group_by") or ())

        rows: list[dict] = []
        truncated = False
        if state.has_rows:
            fetched = ctx.repo.fetch_rollup(
                source,
                columns=sorted(set(column_map.values())),
                window=ctx.window,
                tz_generation=ctx.tz_generation,
                group_by=group_by or None,
                filters=ctx.param("row_filters") or None,
                order_by=order_by,
                limit=limit + 1,
                offset=offset,
            )
            truncated = len(fetched) > limit
            rows = [
                {key: row.get(column) for key, column in column_map.items()}
                for row in fetched[:limit]
            ]

        # `total_rows` is the count IN THIS BLOCK. The repository exposes no
        # COUNT(*), and guessing a population total is exactly the kind of
        # invented number this subsystem refuses to print; `truncated` says
        # whether more exist without claiming how many.
        block = TableBlock(rows=rows, total_rows=len(rows), truncated=truncated)

        kpi_bundle = compute_kpis(ctx, ctx.view.kpis)
        return ResolverResult(
            kpis=kpi_bundle.kpis,
            tables={spec.id: block},
            sources=_merge_sources([state.ref], kpi_bundle.sources),
            warnings=[*warnings, *kpi_bundle.warnings],
        ).rolled_up()

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _spec(ctx: ResolverContext) -> TableSpec | None:
        wanted = ctx.param("table")
        for spec in ctx.view.tables:
            if wanted is None or spec.id == wanted:
                return spec
        return None

    @staticmethod
    def _column_map(
        ctx: ResolverContext, spec: TableSpec, source: str
    ) -> dict[str, str]:
        """Display key -> stored column.

        `params["columns"]` is authoritative (server-trusted, from the
        registry). Without it, only spec keys that are literally columns of the
        source are used — a display key is never guessed into a column name.
        """
        declared = ctx.param("columns")
        if isinstance(declared, Mapping):
            return {str(k): str(v) for k, v in declared.items()}

        from app.repositories.analytics_repository import columns_for

        available = set(columns_for(source))
        return {c.key: c.key for c in spec.columns if c.key in available}

    @staticmethod
    def _sort(
        ctx: ResolverContext,
        spec: TableSpec,
        column_map: Mapping[str, str],
        warnings: list[AnalyticsWarning],
    ) -> tuple[str, str]:
        """Validate the sort key against the spec, then against what is stored.

        Two separate failures with two separate messages, because they need two
        different fixes: a key the table does not declare is a bad request, and
        a key the table declares but nothing stores is a bad registry entry.
        Both are refused rather than ignored — a silently dropped sort returns a
        different page than was asked for and says nothing about it.
        """
        declared = {c.key for c in spec.columns if c.sortable}
        requested = ctx.filters.sort
        if requested:
            if requested not in declared:
                raise ResolverError(
                    f"cannot sort table {spec.id!r} by {requested!r}: the table "
                    f"declares {sorted(declared)}. A sort key that is not a column "
                    "of the table is refused rather than ignored — silently "
                    "ignoring it returns a different page than was asked for."
                )
            if requested not in column_map:
                raise ResolverError(
                    f"table {spec.id!r} declares column {requested!r} but nothing "
                    "stores it, so it cannot be sorted on. Bind it in the view's "
                    "registry params or drop it from the spec."
                )
            return requested, ctx.filters.sort_dir

        default = spec.default_sort
        if default and default in declared and default in column_map:
            return default, spec.default_sort_dir
        if default:
            # A default sort naming an unstored column is a registry gap. Fall
            # back to the repository's deterministic ordering and say so, rather
            # than serving an arbitrary page order silently.
            warnings.append(
                warn(
                    METRIC_NOT_BOUND,
                    f"Table {spec.id!r} defaults to sorting by {default!r}, which is "
                    "not stored; rows are returned in bucket order instead.",
                    severity="info",
                    table=spec.id,
                    default_sort=default,
                )
            )
        return "", spec.default_sort_dir


register(MetricsResolver())
register(TimeseriesResolver())
register(BreakdownResolver())
register(TableResolver())
