"""The three finance resolvers: the cascade, unit economics, budget vs actual.

These are the views that spend money's reputation. Every other resolver in this
package reads a rollup and reports what it finds; these three read the
**contribution-margin engine**, which is the one part of the subsystem that can
answer "did this order make money" — and the one part that is currently
answering "we cannot say" for most of its terms.

No formula is restated here
---------------------------
``margin.MarginService`` owns the cascade, ``cost_rules.CostRuleResolver`` owns
rule resolution, ``contracts.MarginResult.pct()`` owns the percentage (and its
``None`` on a zero base), and ``kpis.py`` owns what each id means. This module
only turns a ``MarginResult`` into KPI cards, a waterfall and a set of warnings.
If a number here disagrees with the catalogue, this module is wrong.

What these views actually show today, and why that is the point
---------------------------------------------------------------
Every ``costs.*`` row in ``system_settings`` holds ``"0"``, and
``CostRuleResolver.seed_from_legacy_settings`` deliberately refuses to migrate a
zero — a zero rate and a never-configured rate are indistinguishable, and
writing the zero would convert an honest MISSING into an authoritative-looking
"this costs nothing" for all of history. There is consequently **no cost rule in
this deployment at all**, so:

    CM1  computes from real ``order_items.unit_cost``
    CM2  is None, because gateway fee / packaging / handling / forward shipping
         have no covering rule
    CM3  is None, because CM2 is, and because marketing spend has no rule either

That is the correct answer. A resolver's job is to make it *useful*. An admin
who reads "Contribution Margin: —" learns nothing; one who reads "no rule covers
gateway fees, packaging, fulfilment/handling, forward shipping or marketing
spend — add them to analytics_cost_rules" can act this afternoon. So every
missing component is named: in the KPI's ``inputs_missing``, in a
``COST_RULE_MISSING`` warning that spells out the fix, and in the waterfall,
where a missing step is a gap rather than a zero-height bar.

The one thing this module must never do is substitute a number. A coalesced-to-
zero COGS reports a 100% margin, which is the single direction of error nobody
investigates, and it is exactly what ``profit_service.py`` does today.

Live cards, rollup trends
-------------------------
The cascade cards are computed **live** from ``orders`` / ``order_items``
through the cost engine, because cost rules are effective-dated per day and
``agg_order_daily`` stores no cost-rule output at all. The trend line beneath
them is still the rollup, which is additive and cheap. Both provenances are
listed in ``sources`` and a ``MARGIN_LIVE_BASIS`` note says so out loud, because
two figures on one screen from two pipelines is precisely the thing this
subsystem refuses to leave unlabelled.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import select

from app.models.analytics_control import AnalyticsBudget, CostType
from app.schemas.analytics_view import (
    AnalyticsWarning,
    KpiValue,
    SourceRef,
    TableBlock,
    WarningCode,
)
from app.services.analytics.contracts import MarginResult, from_minor
from app.services.analytics.filters import ResolvedWindow
from app.services.analytics.margin import (
    CM2_COST_TYPES,
    CM3_COST_TYPES,
    COGS,
    MarginService,
)

# Private in `margin`, imported rather than restated: two label maps for the
# same cost types would drift, and the admin-facing wording is the entire value
# of the "which rule is missing" message.
from app.services.analytics.margin import _LABELS as COST_LABELS
from app.services.analytics.resolvers.base import (
    NOT_CONFIGURED,
    ResolverContext,
    ResolverResult,
    SourceState,
    build_kpi,
    missing_kpi,
    probe_source,
    source_ref,
    warn,
)
from app.services.analytics.resolvers.core import (
    DEFAULT_SOURCE,
    METRIC_NOT_BOUND,
    MetricBinding,
    MetricsResolver,
    TableResolver,
    TimeseriesResolver,
    compute_kpis,
    dense_points,
    evaluate,
)
from app.services.analytics.resolvers.special import custom_function
from app.services.analytics.types import FilterKey, MetricQuality, worst_quality

__all__ = [
    "margin_cascade",
    "unit_economics",
    "budget_vs_actual",
    "Cascade",
    "cascade_for",
    "cascade_kpis",
    "cascade_warnings",
    "MARGIN_LIVE_BASIS",
    "MARGIN_ENGINE_NOTE",
    "MARGIN_SOURCE",
    "BUDGET_SOURCE",
    "COST_RULE_FIX",
    "CM1_PER_ORDER",
]

_HUNDRED = Decimal("100")
_MONEY_Q = Decimal("0.01")
_PCT_Q = Decimal("0.01")

#: The cascade cards come from `orders`, not from `agg_order_daily`. Emitted on
#: every cascade response so a reader never has to guess why a card and the
#: trend line beneath it can differ while a rollup is catching up.
MARGIN_LIVE_BASIS = "MARGIN_LIVE_BASIS"

#: A note the margin engine itself raised. Passed through verbatim: the engine
#: knows things about the window (a sale later refunded, a carrier charge with
#: no covering rule) that this module has no business paraphrasing.
MARGIN_ENGINE_NOTE = "MARGIN_ENGINE_NOTE"

#: Provenance id for the live cascade. Deliberately not a rollup name — the
#: envelope's `sources` is how a reader tells which pipeline a figure came from.
MARGIN_SOURCE = "orders+order_items (live, via cost rules)"

#: Provenance id for the budget table. `analytics_budgets` is authored, not
#: aggregated, so it carries no watermark and its `kind` is "live".
BUDGET_SOURCE = "analytics_budgets"

#: The sentence an admin can act on. Spelled once and quoted by both the cascade
#: and unit economics, because two differently-worded versions of one fix on two
#: screens read as two different problems.
COST_RULE_FIX = (
    "Add one effective-dated row per cost type to analytics_cost_rules "
    "(cost_type, scope, value, unit, effective_from). The costs.* fields under "
    "Admin -> Settings are NOT this engine's input and every one of them still "
    'holds "0"; a zero rate is indistinguishable from an unconfigured one, '
    "which is why the legacy migration refuses to copy them."
)

#: Every cost type the cascade can be short of, in the order the waterfall
#: renders them. Composed from `margin`'s own tuples rather than restated, so
#: adding a cost line to the cascade cannot leave this list behind.
_CASCADE_COST_TYPES: tuple[str, ...] = CM2_COST_TYPES + CM3_COST_TYPES

#: Filter keys the margin engine can genuinely narrow orders by. `category` and
#: `product` are absent on purpose: `MarginService` refuses them (they are
#: line-grained, and the order-level income terms cannot be attributed to a line
#: without running the allocator per order), so honouring them here would mean
#: silently applying half a filter.
_SCOPE_FILTERS: tuple[tuple[FilterKey, str, str], ...] = (
    (FilterKey.PAYMENT_METHOD, "payment_method", "payment_method"),
    (FilterKey.PAYMENT_GATEWAY, "payment_gateway", "gateway"),
    (FilterKey.COURIER, "courier", "courier"),
)

#: Filter keys a finance view may declare that the cascade cannot honour. The
#: envelope reports them as `applied`, so saying nothing here would let a reader
#: believe a category-filtered margin is a category margin.
_UNSCOPEABLE_FILTERS: tuple[tuple[FilterKey, str], ...] = (
    (FilterKey.CATEGORY, "category_id"),
    (FilterKey.PRODUCT, "product_id"),
    (FilterKey.SKU, "sku"),
)

#: CM1 per paid order, bucketed. Same denominator as the `aov` binding in
#: `core.METRIC_BINDINGS`, so the two cards on view 68 divide by one population;
#: the coverage pair makes a bucket with no costed unit come back missing rather
#: than as 100% margin.
CM1_PER_ORDER = MetricBinding(
    source=DEFAULT_SOURCE,
    add=("net_merchandise_sales",),
    sub=("cogs_sum",),
    over_add=("orders_paid", "orders_shipped", "orders_delivered"),
    coverage=("costed_units", "units"),
    coverage_input="unit_cost",
)

#: New customers acquired in the window — CAC's denominator. A customer's first
#: paid order falls on exactly one day, so this column is additive.
_NEW_CUSTOMERS = MetricBinding(source=DEFAULT_SOURCE, add=("new_customers",))

#: Budget metric id -> how its actual is measured. Mirrors the vocabulary
#: `AnalyticsBudget.metric` documents. Anything outside this map is reported as
#: an unmeasurable target rather than compared against a near-enough figure.
_BUDGET_ROLLUP_METRICS: Mapping[str, MetricBinding] = {
    "net_revenue": MetricBinding(source=DEFAULT_SOURCE, add=("net_revenue",)),
    "orders": MetricBinding(source=DEFAULT_SOURCE, add=("orders_total",)),
    "aov": MetricBinding(
        source=DEFAULT_SOURCE,
        add=("paid_order_value",),
        over_add=("orders_paid", "orders_shipped", "orders_delivered"),
    ),
}

#: Budget metrics that need the cost engine rather than a rollup column.
_BUDGET_ENGINE_METRICS: frozenset[str] = frozenset(
    {"contribution_margin_3", "marketing_spend"}
)


# ---------------------------------------------------------------------------
# The cascade, computed once per request
# ---------------------------------------------------------------------------


@dataclass
class Cascade:
    """One window's `MarginResult`, its comparison window's, and the scope used.

    Held and passed around rather than recomputed: `MarginService.compute()`
    issues roughly a dozen queries, and a view showing CM1, CM1%, CM2, CM2%, CM3
    and CM3% must not issue them six times. The service comes along for
    `waterfall()`, which is a pure function of the result.
    """

    service: MarginService
    now: MarginResult
    previous: MarginResult | None = None
    scope: Mapping[str, str] = field(default_factory=dict)
    #: Filter keys the view declared, the request supplied, and the engine
    #: cannot apply. Reported, never silently dropped.
    unscopeable: tuple[str, ...] = ()

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(self.now.missing_inputs)

    def component(self, cost_type: str):
        for c in self.now.components:
            if c.cost_type == cost_type:
                return c
        return None

    def component_quality(self, cost_type: str) -> MetricQuality:
        component = self.component(cost_type)
        return component.quality if component is not None else MetricQuality.INCOMPLETE

    def waterfall(self) -> list[dict[str, Any]]:
        return self.service.waterfall(self.now)


def _scope_for(ctx: ResolverContext) -> tuple[dict[str, str], tuple[str, ...]]:
    """Split the request's dimension filters into what the engine can apply.

    A view honours only the filter keys it declares, so a `courier` sent to a
    view with no courier filter is ignored here exactly as `view_service`
    reports it ignored. Of the keys a finance view *does* declare, only the
    order-level ones can narrow the cascade.
    """
    declared = set(ctx.view.filters)
    scope: dict[str, str] = {}
    for key, field_name, scope_name in _SCOPE_FILTERS:
        if key not in declared:
            continue
        value = getattr(ctx.filters, field_name, None)
        if value in (None, ""):
            continue
        scope[scope_name] = str(value)

    unscopeable = tuple(
        key.value
        for key, field_name in _UNSCOPEABLE_FILTERS
        if key in declared and getattr(ctx.filters, field_name, None) not in (None, "")
    )
    return scope, unscopeable


def cascade_for(ctx: ResolverContext) -> Cascade:
    """Run the margin engine over the window and over its comparison window.

    Both windows are half-open, matching `filters.ResolvedWindow` and
    `MarginService._window`, so a card and its delta cover equal spans.
    """
    scope, unscopeable = _scope_for(ctx)
    service = MarginService(ctx.db)
    now = service.compute(ctx.window.date_from, ctx.window.date_to, scope=scope or None)
    previous: MarginResult | None = None
    if ctx.window.has_comparison:
        previous = service.compute(
            ctx.window.compare_from,  # type: ignore[arg-type]
            ctx.window.compare_to,  # type: ignore[arg-type]
            scope=scope or None,
        )
    return Cascade(
        service=service,
        now=now,
        previous=previous,
        scope=scope,
        unscopeable=unscopeable,
    )


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------


def _money(minor: int | None) -> Decimal | None:
    return None if minor is None else from_minor(minor)


def _labelled(cost_types: Iterable[str]) -> list[str]:
    return [COST_LABELS.get(c, c) for c in cost_types]


def _missing_for(cascade: Cascade, levels: Sequence[Sequence[str]]) -> tuple[str, ...]:
    """The absent cost types that block the given cascade levels.

    Cost-type ids, not labels: `KpiValue.inputs_missing` is read by machines as
    well as by people, and the human wording lives in the warning beside it.
    """
    wanted = {name for level in levels for name in level}
    return tuple(name for name in cascade.missing if name in wanted)


def _level_quality(cascade: Cascade, cost_types: Sequence[str]) -> MetricQuality:
    """Worst grade across a level's inputs, COGS included.

    A cascade level is worth exactly what its weakest input is worth: a CM2
    built on one ESTIMATED rate is ESTIMATED however solid the other six are.
    """
    grades = [cascade.component_quality(COGS)]
    grades.extend(cascade.component_quality(name) for name in cost_types)
    return worst_quality(grades)


def _pct_card(
    kpi_id: str,
    cascade: Cascade,
    minor: int | None,
    previous_minor: int | None,
    *,
    missing: tuple[str, ...],
    quality: MetricQuality,
    coverage: Decimal | None,
) -> KpiValue:
    """A margin percentage card.

    `MarginResult.pct()` returns None over a zero base — a period with no sales
    has an undefined margin, not a 0% one — so a None here is passed through
    with NO quality override. `build_kpi` then grades it INCOMPLETE, which is
    what an undefined figure is; claiming ESTIMATED for a number that does not
    exist would be confidence about nothing.
    """
    value = cascade.now.pct(minor)
    previous = (
        cascade.previous.pct(previous_minor) if cascade.previous is not None else None
    )
    return build_kpi(
        kpi_id,
        value,
        previous=previous,
        coverage_pct=coverage,
        inputs_missing=missing,
        quality=quality if value is not None else None,
    )


def _component_kpi(cascade: Cascade, kpi_id: str, cost_type: str) -> KpiValue:
    """One resolved cost component as a card, or the named absence of one."""
    component = cascade.component(cost_type)
    if component is None or component.is_missing:
        return missing_kpi(kpi_id, cost_type)
    previous: Decimal | None = None
    if cascade.previous is not None:
        for c in cascade.previous.components:
            if c.cost_type == cost_type and not c.is_missing:
                previous = _money(c.value_minor)
                break
    return build_kpi(
        kpi_id,
        _money(component.value_minor),
        previous=previous,
        quality=component.quality,
    )


def cascade_kpis(cascade: Cascade) -> dict[str, KpiValue]:
    """Every card the cascade can fill, including the ones it cannot.

    A level whose inputs are absent is emitted with ``value=None`` and the
    absent cost types named. `build_kpi` enforces that pairing — it refuses to
    carry a value alongside a non-empty `inputs_missing` — so no code path here
    can publish a confident number with a footnote nobody reads.
    """
    now, previous = cascade.now, cascade.previous
    coverage = now.cost_coverage_pct
    cogs_quality = cascade.component_quality(COGS)

    cm2_missing = _missing_for(cascade, [CM2_COST_TYPES])
    cm3_missing = _missing_for(cascade, [CM2_COST_TYPES, CM3_COST_TYPES])
    cm2_quality = _level_quality(cascade, CM2_COST_TYPES)
    cm3_quality = _level_quality(cascade, _CASCADE_COST_TYPES)

    def prev(attr: str) -> int | None:
        return None if previous is None else getattr(previous, attr)

    kpis: dict[str, KpiValue] = {
        "net_merchandise_sales": build_kpi(
            "net_merchandise_sales",
            _money(now.net_merchandise_sales_minor),
            previous=_money(prev("net_merchandise_sales_minor")),
        ),
        "cogs": build_kpi(
            "cogs",
            _money(now.cogs_minor),
            previous=_money(prev("cogs_minor")),
            coverage_pct=coverage,
            quality=cogs_quality,
        ),
        "cm1": build_kpi(
            "cm1",
            _money(now.cm1_minor),
            previous=_money(prev("cm1_minor")),
            coverage_pct=coverage,
            quality=cogs_quality,
        ),
        # Numerically identical to cm1 by the catalogue's own definition, and
        # built from the same call so the two can never disagree on one screen.
        "gross_profit": build_kpi(
            "gross_profit",
            _money(now.cm1_minor),
            previous=_money(prev("cm1_minor")),
            coverage_pct=coverage,
            quality=cogs_quality,
        ),
        "cm2": build_kpi(
            "cm2",
            _money(now.cm2_minor),
            previous=_money(prev("cm2_minor")),
            coverage_pct=coverage,
            inputs_missing=cm2_missing,
            quality=cm2_quality,
        ),
        "cm3": build_kpi(
            "cm3",
            _money(now.cm3_minor),
            previous=_money(prev("cm3_minor")),
            coverage_pct=coverage,
            inputs_missing=cm3_missing,
            quality=cm3_quality,
        ),
    }

    kpis["cm1_pct"] = _pct_card(
        "cm1_pct", cascade, now.cm1_minor, prev("cm1_minor"),
        missing=(), quality=cogs_quality, coverage=coverage,
    )
    kpis["gross_margin_pct"] = _pct_card(
        "gross_margin_pct", cascade, now.cm1_minor, prev("cm1_minor"),
        missing=(), quality=cogs_quality, coverage=coverage,
    )
    kpis["cm2_pct"] = _pct_card(
        "cm2_pct", cascade, now.cm2_minor, prev("cm2_minor"),
        missing=cm2_missing, quality=cm2_quality, coverage=coverage,
    )
    kpis["cm3_pct"] = _pct_card(
        "cm3_pct", cascade, now.cm3_minor, prev("cm3_minor"),
        missing=cm3_missing, quality=cm3_quality, coverage=coverage,
    )

    kpis["gateway_fees"] = _component_kpi(cascade, "gateway_fees", CostType.GATEWAY_FEE)
    kpis["marketing_spend"] = _component_kpi(
        cascade, "marketing_spend", CostType.MARKETING_SPEND
    )
    return kpis


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


def cascade_warnings(cascade: Cascade) -> list[AnalyticsWarning]:
    """Everything a reader needs in order to act on, or discount, the cascade."""
    now = cascade.now
    warnings: list[AnalyticsWarning] = [
        warn(
            MARGIN_LIVE_BASIS,
            "CM1/CM2/CM3 and their percentages are computed live from orders and "
            "order_items through the effective-dated cost rules, not from "
            "agg_order_daily — cost rules are dated per day and no rollup stores "
            "their output. Any trend line on this view comes from the rollup and "
            "stops at its watermark; the cards do not.",
            severity="info",
            basis=MARGIN_SOURCE,
        )
    ]

    if now.missing_inputs:
        blocked = "CM2 and CM3 are" if now.cm2_minor is None else "CM3 is"
        warnings.append(
            warn(
                WarningCode.COST_RULE_MISSING,
                "Contribution margin stops at CM1: no effective-dated cost rule "
                f"covers {', '.join(_labelled(now.missing_inputs)).lower()}. "
                f"{blocked} reported as unknown rather than as CM1 wearing CM2's "
                "label, and no missing cost is substituted with zero — a zero "
                "cost reports a better margin than reality, which is the one "
                f"direction of error nobody investigates. {COST_RULE_FIX}",
                severity="warn",
                missing=list(now.missing_inputs),
                missing_labels=_labelled(now.missing_inputs),
                table="analytics_cost_rules",
            )
        )

    if now.cost_coverage_pct < _HUNDRED:
        warnings.append(
            warn(
                WarningCode.COST_COVERAGE_LOW,
                f"Only {now.cost_coverage_pct}% of order lines in this window "
                "carry a unit_cost snapshot. The uncosted lines are EXCLUDED from "
                "COGS rather than costed at zero, so CM1 and every level under it "
                "covers that share of the business and no more.",
                severity="warn",
                coverage_pct=str(now.cost_coverage_pct),
            )
        )

    if cascade.unscopeable:
        warnings.append(
            warn(
                METRIC_NOT_BOUND,
                "The margin cascade cannot be narrowed by "
                f"{', '.join(cascade.unscopeable)}: those are line-grained, and "
                "the order-level terms (shipping income, COD surcharge, gateway "
                "fee) cannot be attributed to a line without running the "
                "allocator per order. The cascade cards below are UNFILTERED; "
                "only the rollup-backed panels honour that filter.",
                severity="warn",
                ignored_by_cascade=list(cascade.unscopeable),
            )
        )

    # The engine's own notes, verbatim and individually keyed so `_dedupe`
    # cannot collapse two different facts into one line.
    for index, note in enumerate(now.warnings):
        warnings.append(
            warn(
                MARGIN_ENGINE_NOTE,
                note,
                severity="info",
                note_index=index,
                basis=MARGIN_SOURCE,
            )
        )
    return warnings


def _prune_unbound_warnings(
    warnings: Sequence[AnalyticsWarning], supplied: Iterable[str]
) -> list[AnalyticsWarning]:
    """Drop the metrics this module now supplies from METRIC_NOT_BOUND notices.

    `compute_kpis` correctly reports cm2 / cm2_pct / gateway_fees as unbound —
    no rollup stores a cost-rule output. Once the cascade has filled them, the
    notice would be describing a card that is right there on the screen, which
    trains a reader to ignore the one warning code on this view that matters.
    """
    filled = set(supplied)
    out: list[AnalyticsWarning] = []
    for item in warnings:
        if item.code != METRIC_NOT_BOUND:
            out.append(item)
            continue
        named = item.detail.get("metrics")
        if not isinstance(named, list):
            out.append(item)
            continue
        remaining = sorted(m for m in named if m not in filled)
        if not remaining:
            continue
        out.append(
            warn(
                METRIC_NOT_BOUND,
                "No stored column binds "
                + ", ".join(remaining)
                + "; reported as unavailable rather than as zero.",
                severity=item.severity,
                metrics=remaining,
            )
        )
    return out


def _cascade_source() -> SourceRef:
    return source_ref(
        MARGIN_SOURCE,
        kind="live",
        label="Orders + order items, costed through analytics_cost_rules",
    )


def _merge_refs(*groups: Sequence[SourceRef]) -> list[SourceRef]:
    """One entry per source id. A repeated rollup reads as two pipelines."""
    out: list[SourceRef] = []
    seen: set[str] = set()
    for group in groups:
        for ref in group:
            if ref.id in seen:
                continue
            seen.add(ref.id)
            out.append(ref)
    return out


# ===========================================================================
# margin_cascade — views 67 (Contribution Margin) and 47 (Pricing and Margin)
# ===========================================================================


def _base_result(ctx: ResolverContext) -> ResolverResult:
    """Run the reusable resolver this view would otherwise have used.

    The cascade ADDS to a view; it does not replace it. View 47 keeps its
    product table and view 67 keeps its trend line, both from the rollups they
    were already bound to, and the cascade overwrites only the cards it can
    compute better. The shape is chosen from the view's own declarations rather
    than from a parameter, so the registry entry keeps describing the *view*
    instead of the plumbing.
    """
    if ctx.view.tables and ctx.param("columns"):
        return TableResolver().run(ctx)
    if any(c.x in ("date", "hour", "month", "week") for c in ctx.view.charts):
        return TimeseriesResolver().run(ctx)
    return MetricsResolver().run(ctx)


@custom_function("margin_cascade")
def margin_cascade(ctx: ResolverContext) -> ResolverResult:
    """CM1 / CM2 / CM3 with amounts, percentages, deltas and the waterfall.

    Percentages come from `MarginResult.pct()`, which returns None over a zero
    base. Quality rolls up to the worst component, so a cascade missing one rule
    is INCOMPLETE however solid CM1 is — and `view_service` then downgrades the
    view's availability, which is the entire point of grading it.
    """
    base = _base_result(ctx)
    cascade = cascade_for(ctx)

    kpis = cascade_kpis(cascade)
    merged = {**base.kpis, **kpis}

    warnings = [
        *_prune_unbound_warnings(base.warnings, kpis),
        *cascade_warnings(cascade),
    ]

    series = dict(base.series)
    # A missing step comes through with amount=None so a chart draws a gap.
    # Never 0 — a zero-height bar claims the cost was measured and was free.
    series["margin_waterfall"] = cascade.waterfall()

    return ResolverResult(
        kpis=merged,
        series=series,
        tables=dict(base.tables),
        sources=_merge_refs(base.sources, [_cascade_source()]),
        warnings=warnings,
        quality=cascade.now.quality,
        coverage_pct=cascade.now.cost_coverage_pct,
    ).rolled_up()


# ===========================================================================
# unit_economics — view 68
# ===========================================================================


def _per_order(minor: int | None, orders: int) -> Decimal | None:
    """Money per order, or None when either half is unknown.

    Zero orders gives None rather than 0: a window with no orders has no
    per-order economics, and 0.00 would read as an order that contributed
    nothing.
    """
    if minor is None or orders <= 0:
        return None
    return (Decimal(minor) / Decimal(orders) / _HUNDRED).quantize(_MONEY_Q)


def _paid_orders(totals: Mapping[str, Any]) -> int:
    """Orders in a revenue status — the same population `aov` divides by."""
    total = 0
    for column in ("orders_paid", "orders_shipped", "orders_delivered"):
        value = totals.get(column)
        if value is not None:
            total += int(value)
    return total


def _unit_rows(cascade: Cascade, orders: int) -> list[dict[str, Any]]:
    """The cascade's waterfall restated per order.

    Every step keeps its own `missing` flag, so a reader sees "Packaging — not
    configured" against a line rather than an unexplained blank in a total.
    """
    return [
        {
            "component": step["label"],
            "cost_type": step["cost_type"],
            "kind": step["kind"],
            "amount": step["amount"],
            "per_order": _per_order(step["amount_minor"], orders),
            "quality": step["quality"],
            "missing": step["missing"],
        }
        for step in cascade.waterfall()
    ]


def _per_order_points(ctx: ResolverContext, state: SourceState) -> list[dict] | None:
    """CM1 per paid order, bucketed, densified ONLY inside the watermark."""
    if not state.has_rows:
        return None
    covered = state.covered_through(ctx.window)
    if covered is None:
        return None
    rows = ctx.repo.fetch_rollup(
        DEFAULT_SOURCE,
        columns=sorted(CM1_PER_ORDER.columns),
        window=ctx.window,
        tz_generation=ctx.tz_generation,
        group_by=["bucket_date"],
    )
    points = dense_points(
        rows, ctx.window, covered, ctx.filters.granularity, {"cm1_per_order": CM1_PER_ORDER}
    )
    return [{"date": p["_x"], "cm1_per_order": p.get("cm1_per_order")} for p in points]


@custom_function("unit_economics")
def unit_economics(ctx: ResolverContext) -> ResolverResult:
    """What one order is worth after variable cost — and what is not knowable.

    CAC needs marketing spend, and the only source of marketing spend in this
    deployment is a MARKETING_SPEND cost rule: there is no ad-platform
    connection and no internal table of ad spend. With no rule, CAC is None and
    names what is absent. It is never computed as free acquisition, and never
    quietly replaced by a figure that happens to be available.
    """
    base = MetricsResolver().run(ctx)
    cascade = cascade_for(ctx)
    state = probe_source(ctx, DEFAULT_SOURCE, label="Order rollup (daily)")

    totals = ctx.repo.fetch_totals(
        DEFAULT_SOURCE,
        columns=sorted({*CM1_PER_ORDER.columns, *_NEW_CUSTOMERS.columns}),
        window=ctx.window,
        tz_generation=ctx.tz_generation,
    )
    paid_orders = _paid_orders(totals)
    new_customers = evaluate(_NEW_CUSTOMERS, totals).value

    kpis = cascade_kpis(cascade)

    # -- CAC, and everything downstream of it -------------------------------
    spend = cascade.component(CostType.MARKETING_SPEND)
    spend_missing = spend is None or spend.is_missing
    cac_missing: list[str] = []
    if spend_missing:
        cac_missing.append(CostType.MARKETING_SPEND)
    if not new_customers:
        # The catalogue is explicit: None, not 0, when there were no new
        # customers. Dividing by zero acquisitions has no answer.
        cac_missing.append("new_customers")

    cac_value: Decimal | None = None
    if not cac_missing and spend is not None:
        cac_value = (
            Decimal(int(spend.value_minor or 0)) / _HUNDRED / Decimal(new_customers)
        ).quantize(_MONEY_Q)
    kpis["cac"] = build_kpi(
        "cac",
        cac_value,
        inputs_missing=cac_missing,
        quality=MetricQuality.ESTIMATED if cac_value is not None else None,
    )

    # LTV is a LIFETIME figure per customer. `agg_customer_snapshot` holds it,
    # but every column there is a level: summing it over a window multiplies one
    # customer's lifetime value by the number of snapshot days in range. There
    # is no additive source, so it is reported unavailable rather than
    # approximated from the window's revenue — which would be AOV wearing LTV's
    # label and would flatter every LTV:CAC comparison built on it.
    kpis["ltv"] = missing_kpi(
        "ltv",
        "lifetime customer revenue (agg_customer_snapshot is a level, not a flow)",
    )
    kpis["ltv_to_cac_ratio"] = missing_kpi("ltv_to_cac_ratio", "ltv", *cac_missing)

    payback_missing = list(cac_missing)
    if cascade.now.cm2_minor is None:
        payback_missing.extend(_missing_for(cascade, [CM2_COST_TYPES]))
    kpis["cac_payback_period"] = build_kpi(
        "cac_payback_period",
        None,
        inputs_missing=payback_missing or ["cm2 per new customer per day"],
    )

    warnings = [
        *_prune_unbound_warnings(base.warnings, kpis),
        *cascade_warnings(cascade),
        *state.warnings,
    ]

    if spend_missing:
        warnings.append(
            warn(
                NOT_CONFIGURED,
                "Acquisition cost is not reported: there is no ad-platform "
                "connection and no MARKETING_SPEND cost rule, so marketing spend "
                "is unknown rather than zero. CAC, LTV:CAC and CAC payback are "
                "null with their inputs named — a zero-spend CAC would report "
                "free acquisition. " + COST_RULE_FIX,
                severity="warn",
                requires=["ad_platform", CostType.MARKETING_SPEND],
            )
        )
    if paid_orders <= 0:
        warnings.append(
            warn(
                WarningCode.SMALL_SAMPLE,
                "No paid orders in this window, so every per-order figure is "
                "undefined rather than zero — there is no order to divide by.",
                severity="info",
                orders=paid_orders,
            )
        )

    series = dict(base.series)
    points = _per_order_points(ctx, state)
    if points is not None:
        for chart in ctx.view.charts:
            if chart.x in ("date", "week", "month"):
                series[chart.id] = points

    rows = _unit_rows(cascade, paid_orders)
    return ResolverResult(
        kpis={**base.kpis, **kpis},
        series=series,
        tables={
            "unit_economics": TableBlock(
                rows=rows, total_rows=len(rows), truncated=False
            )
        },
        sources=_merge_refs(base.sources, [state.ref], [_cascade_source()]),
        warnings=warnings,
        quality=cascade.now.quality,
        coverage_pct=cascade.now.cost_coverage_pct,
    ).rolled_up()


# ===========================================================================
# budget_vs_actual — view 72
# ===========================================================================


def _budget_rows(ctx: ResolverContext) -> list[AnalyticsBudget]:
    """Budgets whose period overlaps the requested window, oldest first.

    Overlap rather than containment: a monthly target is worth showing on a
    two-week window, with its period stated, so a reader can see they are
    looking at a partial period rather than at a missed one.
    """
    return list(
        ctx.db.execute(
            select(AnalyticsBudget)
            .where(
                AnalyticsBudget.period_start < ctx.window.date_to,
                AnalyticsBudget.period_end >= ctx.window.date_from,
            )
            .order_by(
                AnalyticsBudget.period_start,
                AnalyticsBudget.metric,
                AnalyticsBudget.dimension,
                AnalyticsBudget.dimension_value,
            )
        )
        .scalars()
        .all()
    )


def _budget_window(budget: AnalyticsBudget) -> ResolvedWindow:
    """The budget's own period as a half-open window.

    `period_end` is stored INCLUSIVE, so the half-open upper bound is the day
    after it. Off by one here would silently drop the last day of every target.
    """
    return ResolvedWindow(
        date_from=budget.period_start,
        date_to=budget.period_end + timedelta(days=1),
    )


def _budget_actual(
    ctx: ResolverContext, budget: AnalyticsBudget
) -> tuple[Decimal | None, list[str]]:
    """The measured figure for one budget row, or why it cannot be measured."""
    dimension = (budget.dimension or "-").strip()
    if dimension not in ("", "-"):
        # A scoped target needs its actual at the same scope. No rollup carries
        # net revenue or orders at category grain — tax and shipping cannot be
        # attributed to a line — so the honest answer is that the comparison
        # cannot be made, not the store-wide number under a category label.
        return None, [f"actual at scope {dimension}={budget.dimension_value}"]

    metric = (budget.metric or "").strip()
    window = _budget_window(budget)

    binding = _BUDGET_ROLLUP_METRICS.get(metric)
    if binding is not None:
        totals = ctx.repo.fetch_totals(
            binding.source,
            columns=sorted(binding.columns),
            window=window,
            tz_generation=ctx.tz_generation,
        )
        outcome = evaluate(binding, totals)
        if outcome.value is None:
            return None, list(outcome.inputs_missing) or [binding.source]
        return Decimal(outcome.value).quantize(_MONEY_Q), []

    if metric in _BUDGET_ENGINE_METRICS:
        result = MarginService(ctx.db).compute(window.date_from, window.date_to)
        if metric == "contribution_margin_3":
            if result.cm3_minor is None:
                return None, list(result.missing_inputs)
            return from_minor(result.cm3_minor), []
        for component in result.components:
            if component.cost_type == CostType.MARKETING_SPEND:
                if component.is_missing:
                    return None, [CostType.MARKETING_SPEND]
                return from_minor(int(component.value_minor or 0)), []
        return None, [CostType.MARKETING_SPEND]

    return None, [f"unknown budget metric {metric!r}"]


def _variance(
    actual: Decimal | None, target: Decimal | None
) -> tuple[Decimal | None, Decimal | None]:
    """(variance %, attainment %) against a target, or (None, None).

    A zero or negative target has no attainment percentage: dividing by it would
    report infinite overachievement against a target nobody set, which is
    exactly why `AnalyticsBudget.budget_amount` refuses a server default.
    """
    if actual is None or target is None or target <= 0:
        return None, None
    attainment = (Decimal(actual) / Decimal(target) * _HUNDRED).quantize(_PCT_Q)
    return (attainment - _HUNDRED).quantize(_PCT_Q), attainment


@custom_function("budget_vs_actual")
def budget_vs_actual(ctx: ResolverContext) -> ResolverResult:
    """Targets against actuals — or, today, the honest absence of targets.

    `analytics_budgets` ships empty. That is an EMPTY STATE, not a failure: a
    store with no targets is not a store that missed them. So the no-budget path
    returns the real actuals, an explicit NOT_CONFIGURED warning naming exactly
    what to add, and **no attainment figure at all**. Reporting 0% attainment
    would read as catastrophe on a screen whose real message is "nobody has set
    a target yet".

    It deliberately does NOT use `base.not_configured()`, which returns an empty
    `sources` list. The actual figures here are real, measured and worth showing
    — the missing half is the target, and the warning says which half that is.
    """
    bundle = compute_kpis(ctx, ctx.view.kpis)
    warnings = list(bundle.warnings)
    budgets = _budget_rows(ctx)

    if not budgets:
        warnings.append(
            warn(
                NOT_CONFIGURED,
                "No budget targets cover this period, so there is nothing to "
                "compare the actuals against. This is 'not configured', NOT 0% "
                "attainment — a store with no targets has not missed them. Add "
                "rows to analytics_budgets: each needs period_start, period_end, "
                "granularity (month/quarter/year), a metric (net_revenue, "
                "orders, aov, marketing_spend or contribution_margin_3) and a "
                "budget_amount. The actual figures on this view are real and "
                "unaffected.",
                severity="warn",
                requires=["budgets"],
                table=BUDGET_SOURCE,
            )
        )
        return ResolverResult(
            kpis=bundle.kpis,
            # Deliberately NO `tables` and NO attainment KPI. An empty table
            # block reads as "we looked and found no variance"; the absence of
            # the block plus the warning above reads as "nobody set a target",
            # which is the true statement.
            sources=_merge_refs(
                bundle.sources,
                [source_ref(BUDGET_SOURCE, kind="live", label="Budget targets", rows=0)],
            ),
            warnings=warnings,
            # The view cannot do the job it is named for. INCOMPLETE downgrades
            # its availability to PARTIAL, which is what it is.
            quality=MetricQuality.INCOMPLETE,
        ).rolled_up()

    rows: list[dict[str, Any]] = []
    chart_points: list[dict[str, Any]] = []
    unmeasurable = 0
    open_periods = 0

    for budget in budgets:
        target = Decimal(budget.budget_amount)
        actual, missing = _budget_actual(ctx, budget)
        variance_pct, attainment_pct = _variance(actual, target)
        if actual is None:
            unmeasurable += 1
        if budget.period_end >= ctx.today:
            open_periods += 1
        scope = (
            "store-wide"
            if (budget.dimension or "-").strip() in ("", "-")
            else f"{budget.dimension}={budget.dimension_value}"
        )
        period = f"{budget.period_start.isoformat()}..{budget.period_end.isoformat()}"
        rows.append(
            {
                "period": period,
                "granularity": budget.granularity,
                "metric": budget.metric,
                "scope": scope,
                "budget": target,
                "actual": actual,
                "variance_pct": variance_pct,
                "attainment_pct": attainment_pct,
                "period_complete": budget.period_end < ctx.today,
                "inputs_missing": missing,
            }
        )
        if budget.metric == "net_revenue" and scope == "store-wide":
            chart_points.append(
                {"period": period, "budget_revenue": target, "net_revenue": actual}
            )

    if unmeasurable:
        warnings.append(
            warn(
                METRIC_NOT_BOUND,
                f"{unmeasurable} target(s) have no measurable actual and are "
                "listed with a null rather than a zero — a target compared "
                "against a fabricated zero reads as a total miss.",
                severity="warn",
                unmeasurable=unmeasurable,
            )
        )
    if open_periods:
        warnings.append(
            warn(
                WarningCode.PARTIAL_TODAY,
                f"{open_periods} target period(s) have not closed yet. Their "
                "actuals are period-to-date, so attainment below 100% is pacing "
                "rather than a miss.",
                severity="info",
                open_periods=open_periods,
            )
        )

    series: dict[str, list[dict]] = {}
    for chart in ctx.view.charts:
        if chart.x == "period" and chart_points:
            series[chart.id] = chart_points

    table_id = ctx.view.tables[0].id if ctx.view.tables else "budget_lines"
    return ResolverResult(
        kpis=bundle.kpis,
        series=series,
        tables={table_id: TableBlock(rows=rows, total_rows=len(rows), truncated=False)},
        sources=_merge_refs(
            bundle.sources,
            [
                source_ref(
                    BUDGET_SOURCE,
                    kind="live",
                    label="Budget targets",
                    rows=len(budgets),
                )
            ],
        ),
        warnings=warnings,
        quality=(
            MetricQuality.INCOMPLETE if unmeasurable else MetricQuality.AUTHORITATIVE
        ),
    ).rolled_up()
