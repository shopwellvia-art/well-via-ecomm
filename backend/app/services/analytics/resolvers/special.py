"""The five shaped resolvers, plus `custom` dispatch.

`funnel`, `cohort` and `geo` read real rollups. `reconciliation` and
`tracking_health` mostly do not, and that is the interesting part of this file.

The rule everything here follows
--------------------------------
**Where the underlying data does not exist, return the reason and nothing
else.** Not an empty series, not a zeroed KPI, not a table with a "0" in every
cell. `base.not_configured()` produces a result with an explicitly EMPTY
`sources` list, which is the machine-readable difference between *nothing is
connected* and *everything is connected and the answer is zero*. A caller that
sees `sources: []` plus a `NOT_CONFIGURED` warning knows an admin has work to
do; a caller that sees `sources: [agg_order_daily through 2026-07-27]` and no
warnings knows the zero is real. Those two states must never render the same,
and the only way to guarantee that is to never manufacture the second one.

The funnel's permanent caveat
-----------------------------
`agg_funnel_daily` begins at cart and product-view events we emit ourselves. We
have no server-side record of a visitor who never touched a product, so there is
no honest "visitor -> purchase" conversion rate here. Every response from the
funnel resolver carries `FUNNEL_STARTS_AT_CART` — including empty ones, because
an empty funnel is exactly when someone is most likely to go looking for the
missing top of it. `distinct_sessions` is additionally NOT additive across days,
so a multi-day session-based conversion rate is reported as *not computable*
rather than as a number that is quietly too small.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Callable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.schemas.analytics_view import (
    AnalyticsWarning,
    KpiValue,
    SourceRef,
    TableBlock,
    WarningCode,
)
from app.services.analytics.contracts import RevenueBridge, to_minor
from app.services.analytics.export import clamp_row_limit
from app.services.analytics.filters import Granularity
from app.services.analytics.resolvers.base import (
    NOT_CONFIGURED,
    ResolverContext,
    ResolverError,
    ResolverResult,
    SourceState,
    build_kpi,
    missing_kpi,
    not_configured,
    probe_source,
    register,
    source_ref,
    warn,
)
from app.services.analytics.resolvers.core import (
    METRIC_NOT_BOUND,
    MetricBinding,
    compute_kpis,
    dense_points,
    evaluate,
)
from app.services.analytics.types import (
    AnalyticsViewDefinition,
    Capability,
    MetricQuality,
    ResolverId,
)

__all__ = [
    "FunnelResolver",
    "CohortResolver",
    "GeoResolver",
    "ReconciliationResolver",
    "TrackingHealthResolver",
    "CustomResolver",
    "CUSTOM_FUNCTIONS",
    "custom_function",
    "EXTERNAL_CAPABILITIES",
    "external_capability_satisfied",
    "unsatisfied_external_requirements",
]

_HUNDRED = Decimal("100")
_PCT_Q = Decimal("0.0001")

#: Capabilities that name a system OUTSIDE this deployment. A view requiring one
#: can only answer with that system's data — so where it is missing, the view
#: says "not connected" rather than rendering an empty chart the reader will
#: misread as a flat line at zero.
#:
#: Membership here means "external", NOT "unsatisfiable forever". Whether an
#: entry is satisfied in THIS deployment is a runtime question answered by
#: `external_capability_satisfied`, because two entries stopped being
#: hard-coded absences:
#:
#:   * `GA4_DATA_API` — a real Data API client exists (`ga4_data_api.py`). With
#:     credentials configured in system settings the capability is satisfiable;
#:     with none (every deployment today) it behaves exactly as before.
#:   * `AD_PLATFORM` — manually entered spend (`analytics_marketing_spend`)
#:     satisfies the SPEND half of what an ad platform provides, and only that:
#:     blended ROAS/MER and spend-by-channel. Clicks, impressions and
#:     per-channel revenue attribution cannot be typed in, so any view whose
#:     promise includes those (views 19 and 20) must stay gated regardless of
#:     what this probe returns.
EXTERNAL_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.GA4_MEASUREMENT,
        Capability.GA4_DATA_API,
        Capability.GTM_CONTAINER,
        Capability.CLARITY_PROJECT,
        Capability.SEARCH_CONSOLE,
        Capability.AD_PLATFORM,
        Capability.EMAIL_SMS_PLATFORM,
        Capability.AFFILIATE_PLATFORM,
        Capability.GATEWAY_SETTLEMENT_API,
        Capability.COURIER_SCAN_API,
        Capability.SOCIAL_COMMERCE_API,
        Capability.BANK_CASH_FEED,
    }
)


def _ga4_data_api_configured(db: Session) -> bool:
    """Whether usable GA4 Data API credentials are configured RIGHT NOW.

    Delegates to `ga4_data_api.load_data_api_config`, which never raises and
    respects `ANALYTICS_ROLLUPS_ENABLED` — with the subsystem's kill switch off,
    credentials that exist are credentials the pipeline is forbidden to use, so
    the capability is honestly unsatisfied. Imported locally to keep the heavy
    GA4 module off this package's import path.
    """
    from app.services.analytics.ga4_data_api import load_data_api_config

    return load_data_api_config(db).configured


def _marketing_spend_recorded(db: Session) -> bool:
    """Whether ANY marketing spend has ever been entered.

    Existence, not coverage: per-window coverage (and the honest refusal when a
    window has no rows) belongs to the resolver reading the ledger, exactly as
    a rollup's watermark does. This probe only answers "has the admin started
    recording spend at all".
    """
    from app.models.analytics_spend import AnalyticsMarketingSpend

    return (
        db.execute(select(AnalyticsMarketingSpend.id).limit(1)).first() is not None
    )


def external_capability_satisfied(db: Session, capability: Capability) -> bool:
    """Whether an EXTERNAL capability is satisfied in this deployment, today.

    False for anything not named below: nothing inside this deployment can
    stand in for Clarity, Search Console, a settlement API, a courier scan feed
    or the rest, so their honest answer is still an unconditional "not
    connected".

    `AD_PLATFORM` deliberately reports the narrowest true thing: entered spend
    satisfies the questions SPEND alone can answer (blended ROAS/MER, spend by
    channel). It does not conjure clicks, impressions or attribution, and no
    caller may ungate a view that promises those on the strength of this
    returning True — the registry keeps such views (19, 20) statically gated.
    """
    if capability is Capability.GA4_DATA_API:
        return _ga4_data_api_configured(db)
    if capability is Capability.AD_PLATFORM:
        return _marketing_spend_recorded(db)
    return False


def unsatisfied_external_requirements(
    db: Session, view: AnalyticsViewDefinition
) -> list[str]:
    """The view's external requirements that are NOT satisfied right now.

    This is what a runtime gate consumes: with nothing configured anywhere (the
    state of every current deployment) it returns exactly the static filter it
    replaced — every external requirement — byte for byte.
    """
    return sorted(
        c.value
        for c in view.requires
        if c in EXTERNAL_CAPABILITIES and not external_capability_satisfied(db, c)
    )

#: Geo rollups only cover orders whose delivery address resolved to a state, so
#: their totals are a subset of the store's. Stated rather than reconciled away.
GEO_SCOPE_PARTIAL = "GEO_SCOPE_PARTIAL"

#: A check that could not be run because its counterparty is not connected.
#: Distinct from a check that ran and matched.
CHECK_NOT_RUN = "not_configured"
CHECK_MATCHED = "matched"
CHECK_VARIANCE = "variance"

#: A rollup disagreed with an identity it is supposed to satisfy. Severity
#: error: this is an aggregation bug, not a data-quality nuance.
RECONCILIATION_VARIANCE = "RECONCILIATION_VARIANCE"


def _external_requirements(ctx: ResolverContext) -> list[str]:
    return unsatisfied_external_requirements(ctx.db, ctx.view)


def _ratio(
    numerator: Any, denominator: Any, *, scale: Decimal = _HUNDRED
) -> Decimal | None:
    """A rate, or None when the base is empty.

    None rather than 0: a step nobody reached has no conversion rate, and 0%
    would be a claim that everybody who reached it dropped out.
    """
    if numerator is None or denominator in (None, 0):
        return None
    den = Decimal(str(denominator))
    if den == 0:
        return None
    return (Decimal(str(numerator)) / den * scale).quantize(_PCT_Q)


# ===========================================================================
# funnel
# ===========================================================================

FUNNEL_SOURCE = "agg_funnel_daily"

#: Step order as the model defines it. `payments_failed` is deliberately absent:
#: it is a leak counter, not a stage, and putting it in the ladder would make
#: the funnel appear to widen.
FUNNEL_STEPS: tuple[tuple[str, str], ...] = (
    ("product_views", "Product viewed"),
    ("cart_views", "Cart viewed"),
    ("items_added", "Added to cart"),
    ("checkouts_started", "Checkout started"),
    ("shipping_submitted", "Shipping submitted"),
    ("payments_initiated", "Payment initiated"),
    ("orders_placed", "Order placed"),
)

#: Rates computable from the funnel's own additive counters. Every one of these
#: divides two step counters, both of which are additive across days.
FUNNEL_BINDINGS: Mapping[str, MetricBinding] = {
    "add_to_cart_rate": MetricBinding(
        FUNNEL_SOURCE, add=("items_added",), over_add=("product_views",), scale=_HUNDRED
    ),
    "cart_abandonment_rate": MetricBinding(
        FUNNEL_SOURCE,
        add=("checkouts_started",),
        over_add=("items_added",),
        scale=_HUNDRED,
        complement=True,
    ),
    "checkout_abandonment_rate": MetricBinding(
        FUNNEL_SOURCE,
        add=("orders_placed",),
        over_add=("checkouts_started",),
        scale=_HUNDRED,
        complement=True,
    ),
    "checkout_completion_pct": MetricBinding(
        FUNNEL_SOURCE,
        add=("orders_placed",),
        over_add=("checkouts_started",),
        scale=_HUNDRED,
    ),
    "users": MetricBinding(FUNNEL_SOURCE, add=("orders_placed",)),
}

#: conversion_rate divides by `distinct_sessions`, which is NOT additive across
#: days — summing a week's rows overcounts anyone who came back. It is therefore
#: only bound for a single-day window; anything wider reports it unavailable.
_SESSION_CONVERSION = MetricBinding(
    FUNNEL_SOURCE,
    add=("orders_placed",),
    over_add=("distinct_sessions",),
    scale=_HUNDRED,
    quality=MetricQuality.INCOMPLETE,
)


class FunnelResolver:
    """Checkout funnel from `agg_funnel_daily`, always caveated.

    Emits `FUNNEL_STARTS_AT_CART` unconditionally — before touching the
    database, so no code path can return a funnel without it. Without that
    warning a reader divides orders by sessions, calls it "site conversion
    rate", and is wrong by whatever fraction of traffic never reached a product
    page, which is most of it.
    """

    id = ResolverId.FUNNEL.value

    def run(self, ctx: ResolverContext) -> ResolverResult:
        warnings: list[AnalyticsWarning] = [
            warn(
                WarningCode.FUNNEL_STARTS_AT_CART,
                "This funnel starts at add-to-cart. Sessions, visits and product "
                "impressions are not recorded server-side, so there is no "
                "visitor-level top of funnel and no site-wide conversion rate "
                "here — those need GA4.",
                severity="info",
                source=FUNNEL_SOURCE,
            )
        ]

        state = probe_source(ctx, FUNNEL_SOURCE, label="Checkout funnel (daily)")
        warnings.extend(state.warnings)

        columns = [c for c, _ in FUNNEL_STEPS] + ["distinct_sessions"]
        totals = ctx.repo.fetch_totals(
            FUNNEL_SOURCE,
            columns=columns,
            window=ctx.window,
            tz_generation=ctx.tz_generation,
        )

        kpis = self._kpis(ctx, totals, warnings)

        series: dict[str, list[dict]] = {}
        if state.has_rows and any(totals.get(c) for c, _ in FUNNEL_STEPS):
            steps = self._steps(totals)
            for chart in ctx.view.charts:
                if chart.x == "step":
                    series[chart.id] = steps
                elif chart.x == "date":
                    points = self._trend(ctx, state, chart)
                    if points is not None:
                        series[chart.id] = points
        # No `else`: with no rows there is no steps list at all. A ladder of
        # zeros is a picture of a store nobody visited, which is a different
        # claim from "the funnel has not been aggregated".

        return ResolverResult(
            kpis=kpis,
            series=series,
            sources=[state.ref],
            warnings=warnings,
        ).rolled_up()

    # -- internals ---------------------------------------------------------

    def _kpis(
        self,
        ctx: ResolverContext,
        totals: Mapping[str, Any],
        warnings: list[AnalyticsWarning],
    ) -> dict[str, KpiValue]:
        kpis: dict[str, KpiValue] = {}
        other: list[str] = []
        single_day = ctx.window.days <= 1

        for kpi_id in ctx.view.kpis:
            if kpi_id == "conversion_rate":
                if not single_day:
                    kpis[kpi_id] = missing_kpi(
                        kpi_id,
                        "distinct_sessions is not additive across days",
                    )
                    warnings.append(
                        warn(
                            METRIC_NOT_BOUND,
                            "conversion_rate is not reported over a multi-day window: "
                            "distinct_sessions cannot be summed across days without "
                            "double-counting returning visitors, and a quietly "
                            "inflated denominator understates conversion.",
                            severity="warn",
                            metric=kpi_id,
                        )
                    )
                    continue
                outcome = evaluate(_SESSION_CONVERSION, totals)
                kpis[kpi_id] = build_kpi(
                    kpi_id,
                    outcome.value,
                    inputs_missing=outcome.inputs_missing,
                    quality=MetricQuality.INCOMPLETE,
                )
                continue
            binding = FUNNEL_BINDINGS.get(kpi_id)
            if binding is None:
                other.append(kpi_id)
                continue
            outcome = evaluate(binding, totals)
            kpis[kpi_id] = build_kpi(
                kpi_id,
                outcome.value,
                inputs_missing=outcome.inputs_missing,
                quality=outcome.quality,
            )

        if other:
            bundle = compute_kpis(ctx, other)
            kpis.update(bundle.kpis)
            warnings.extend(bundle.warnings)
        return kpis

    @staticmethod
    def _steps(totals: Mapping[str, Any]) -> list[dict]:
        first = totals.get(FUNNEL_STEPS[0][0])
        rows: list[dict] = []
        previous: Any = None
        for column, label in FUNNEL_STEPS:
            users = totals.get(column)
            rows.append(
                {
                    "step": label,
                    "step_key": column,
                    "users": int(users) if users is not None else None,
                    "conversion_rate": _ratio(users, previous),
                    "conversion_from_first": _ratio(users, first),
                }
            )
            previous = users
        return rows

    @staticmethod
    def _trend(
        ctx: ResolverContext, state: SourceState, chart: Any
    ) -> list[dict] | None:
        covered = state.covered_through(ctx.window)
        if covered is None:
            return None
        bindings = {
            s: FUNNEL_BINDINGS[s] for s in chart.series if s in FUNNEL_BINDINGS
        }
        if not bindings:
            return None
        columns = sorted({c for b in bindings.values() for c in b.columns})
        rows = ctx.repo.fetch_rollup(
            FUNNEL_SOURCE,
            columns=columns,
            window=ctx.window,
            tz_generation=ctx.tz_generation,
            group_by=["bucket_date"],
        )
        points = dense_points(
            rows, ctx.window, covered, Granularity.DAY, bindings
        )
        return [{chart.x: p["_x"], **{k: p.get(k) for k in bindings}} for p in points]


# ===========================================================================
# cohort
# ===========================================================================

COHORT_SOURCE = "agg_customer_cohort_monthly"


class CohortResolver:
    """The retention triangle from `agg_customer_cohort_monthly`.

    Retention is `active_customers / cohort_size`, divided here at read time
    because the rollup deliberately stores both sides — a stored percentage
    cannot be re-aggregated across cohorts and cannot be audited.

    A cohort with `cohort_size = 0` gets `retention_pct = None`, not 0%. Zero
    customers acquired has no retention rate; printing 0% would put a cohort
    that does not exist on the heatmap as the worst-performing one.
    """

    id = ResolverId.COHORT.value

    def run(self, ctx: ResolverContext) -> ResolverResult:
        state = probe_source(ctx, COHORT_SOURCE, label="Monthly cohorts")
        warnings = list(state.warnings)

        bundle = compute_kpis(ctx, ctx.view.kpis)
        warnings.extend(bundle.warnings)

        tables: dict[str, TableBlock] = {}
        series: dict[str, list[dict]] = {}

        if state.has_rows:
            rows = ctx.repo.fetch_rollup(
                COHORT_SOURCE,
                columns=["cohort_size", "active_customers", "orders", "revenue"],
                window=ctx.window,
                tz_generation=ctx.tz_generation,
                group_by=["cohort_month", "period_index"],
                order_by="cohort_month",
            )
            grid = [
                {
                    "cohort_month": row["cohort_month"],
                    "period_index": int(row["period_index"]),
                    "cohort_size": int(row["cohort_size"] or 0),
                    "active_customers": int(row["active_customers"] or 0),
                    "orders": int(row["orders"] or 0),
                    "revenue": row["revenue"],
                    "retention_pct": _ratio(
                        row["active_customers"], row["cohort_size"]
                    ),
                }
                for row in rows
            ]
            if grid:
                tables[self._table_id(ctx)] = TableBlock(
                    rows=grid, total_rows=len(grid), truncated=False
                )
                series.update(self._curve(ctx, grid))
                if len({g["cohort_month"] for g in grid}) < 3:
                    warnings.append(
                        warn(
                            WarningCode.SMALL_SAMPLE,
                            "Fewer than three cohorts are in range; a retention curve "
                            "over one or two months is a description, not a trend.",
                            severity="info",
                            cohorts=len({g["cohort_month"] for g in grid}),
                        )
                    )

        return ResolverResult(
            kpis=bundle.kpis,
            series=series,
            tables=tables,
            sources=[state.ref, *bundle.sources],
            warnings=warnings,
        ).rolled_up()

    @staticmethod
    def _table_id(ctx: ResolverContext) -> str:
        return ctx.view.tables[0].id if ctx.view.tables else "cohort_grid"

    @staticmethod
    def _curve(ctx: ResolverContext, grid: list[dict]) -> dict[str, list[dict]]:
        """Retention by months-since-acquisition, pooled across cohorts.

        Pooled by summing the two stored components and dividing once, never by
        averaging each cohort's percentage: an unweighted mean of cohort rates
        lets a 4-customer cohort count as much as a 4,000-customer one.
        """
        pooled: dict[int, list[int]] = {}
        for row in grid:
            acc = pooled.setdefault(row["period_index"], [0, 0])
            acc[0] += row["active_customers"]
            acc[1] += row["cohort_size"]
        points = [
            {
                "month": index,
                "retention_pct": _ratio(active, size),
                "cohort_size": size,
            }
            for index, (active, size) in sorted(pooled.items())
        ]
        charts = [c for c in ctx.view.charts if c.x in ("month", "period_index")]
        if charts:
            return {charts[0].id: points}
        return {"retention_curve": points}


# ===========================================================================
# geo
# ===========================================================================

GEO_SOURCE = "agg_geo_daily"


class GeoResolver:
    """Orders, revenue and RTO by state (or pincode) from `agg_geo_daily`.

    The KPI cards deliberately come from the store-wide rollups via the normal
    bindings, while the table and map come from the geo rollup. Those two do not
    have to agree, and the difference is meaningful: `agg_geo_daily` only holds
    orders whose delivery address resolved to a state. Rather than reconcile
    that away, the difference is named in a warning — a geo table that silently
    matched the store total would be hiding unaddressed orders.
    """

    id = ResolverId.GEO.value

    def run(self, ctx: ResolverContext) -> ResolverResult:
        state = probe_source(ctx, GEO_SOURCE, label="Geography (daily)")
        warnings = list(state.warnings)

        bundle = compute_kpis(ctx, ctx.view.kpis)
        warnings.extend(bundle.warnings)

        by_pincode = (ctx.filters.dimension or ctx.param("dimension")) == "pincode"
        group_by = ["state", "pincode"] if by_pincode else ["state"]

        tables: dict[str, TableBlock] = {}
        series: dict[str, list[dict]] = {}

        if state.has_rows:
            # 200 on a screen, the export ceiling inside `export_scope()`. The
            # bound was hardcoded here; a literal that has to agree with
            # `filters.MAX_LIMIT` and with the export path is a literal that
            # will not.
            limit = clamp_row_limit(ctx.filters.limit)
            rows = ctx.repo.fetch_rollup(
                GEO_SOURCE,
                columns=[
                    "orders",
                    "net_revenue",
                    "units",
                    "cod_orders",
                    "prepaid_orders",
                    "delivered",
                    "rto",
                ],
                window=ctx.window,
                tz_generation=ctx.tz_generation,
                group_by=group_by,
                order_by="-net_revenue",
                limit=limit + 1,
            )
            truncated = len(rows) > limit
            shaped = [
                {
                    "state": row["state"],
                    **({"pincode": row["pincode"]} if by_pincode else {}),
                    "orders": int(row["orders"] or 0),
                    "revenue": row["net_revenue"],
                    "net_revenue": row["net_revenue"],
                    "units": int(row["units"] or 0),
                    # Both rates follow the model's own documented definition:
                    # cod_orders / orders and rto / (delivered + rto).
                    "cod_share": _ratio(row["cod_orders"], row["orders"]),
                    "rto_rate": _ratio(
                        row["rto"], (row["delivered"] or 0) + (row["rto"] or 0)
                    ),
                }
                for row in rows[:limit]
            ]
            if shaped:
                tables[self._table_id(ctx)] = TableBlock(
                    rows=shaped, total_rows=len(shaped), truncated=truncated
                )
                for chart in ctx.view.charts:
                    series[chart.id] = shaped
                warnings.append(
                    warn(
                        GEO_SCOPE_PARTIAL,
                        "Geographic figures cover only orders whose delivery address "
                        "resolved to a state, so they need not sum to the store-wide "
                        "cards above. Pincode rows older than 180 days are rolled up "
                        "to state grain and appear as '-'.",
                        severity="info",
                        source=GEO_SOURCE,
                    )
                )

        return ResolverResult(
            kpis=bundle.kpis,
            series=series,
            tables=tables,
            sources=[state.ref, *bundle.sources],
            warnings=warnings,
        ).rolled_up()

    @staticmethod
    def _table_id(ctx: ResolverContext) -> str:
        return ctx.view.tables[0].id if ctx.view.tables else "geo_table"


# ===========================================================================
# reconciliation
# ===========================================================================

RECON_SOURCE = "agg_order_daily"


@dataclass(frozen=True)
class _Check:
    """One reconciliation check. `status` distinguishes the three outcomes.

    A check that could not run is `not_configured` with NULL values, never a
    0.00% variance. "We checked and it matched" and "we could not check" are the
    two answers a reconciliation screen exists to tell apart.
    """

    name: str
    status: str
    source_value: Decimal | None = None
    rollup_value: Decimal | None = None
    variance_pct: Decimal | None = None
    detail: str = ""


class ReconciliationResolver:
    """Checks the rollups against what can actually be checked.

    What IS checkable here is the **revenue bridge identity** inside
    `agg_order_daily`::

        gross - discounts + tax + shipping + cod_surcharge - refunds == net_revenue

    That is self-proving: if it does not balance, an aggregation job is wrong,
    and no external system is needed to know it. `contracts.RevenueBridge` owns
    the identity so this resolver and the aggregation tests assert the same one.

    What is NOT checkable HERE is everything with a gateway on the other side —
    settlement batches, fees deducted, payout dates. Those appear in the
    variance table with NULL values and status `not_configured`, so an admin can
    see the check exists and is not running, rather than seeing a table that
    looks clean because it is empty.

    NOTE: view 64 (Settlements & Payouts) no longer uses this resolver — it
    moved to `resolvers/settlements_view.py` (`settlements_payouts`), fed by
    uploaded settlement report CSVs (`payment_settlements` /
    `agg_settlement_daily`). This resolver remains correct for view 63's bridge
    identity, and its gateway-side rows could now defer to
    `settlements.settlement_reconciliation()` where report data exists — a
    deliberate future improvement, not an oversight.
    """

    id = ResolverId.RECONCILIATION.value

    def run(self, ctx: ResolverContext) -> ResolverResult:
        external = _external_requirements(ctx)
        if external:
            # View 64 (Settlements & Payouts): every figure on it comes from the
            # gateway. There is no partial answer to give.
            return not_configured(
                ctx.view.limitation
                or "This view needs an external source that is not connected.",
                requires=external,
                detail={"view": ctx.view.slug},
            )

        state = probe_source(ctx, RECON_SOURCE, label="Order rollup (daily)")
        warnings = list(state.warnings)

        checks: list[_Check] = []
        series: dict[str, list[dict]] = {}

        if state.has_rows:
            rows = ctx.repo.fetch_rollup(
                RECON_SOURCE,
                columns=[
                    "gross_merchandise_sales",
                    "discount_sum",
                    "tax_sum",
                    "shipping_income",
                    "cod_surcharge_sum",
                    "refund_sum",
                    "net_revenue",
                ],
                window=ctx.window,
                tz_generation=ctx.tz_generation,
                group_by=["bucket_date"],
            )
            checks.append(self._bridge_check(rows))
            series.update(self._variance_series(ctx, rows))
        else:
            checks.append(
                _Check(
                    name="revenue_bridge",
                    status=CHECK_NOT_RUN,
                    detail=(
                        f"{RECON_SOURCE} holds no rows for this window, so the bridge "
                        "identity could not be evaluated. Not a zero variance."
                    ),
                )
            )

        checks.extend(self._unavailable_checks())

        variance = [c for c in checks if c.status == CHECK_VARIANCE]
        not_run = [c for c in checks if c.status == CHECK_NOT_RUN]
        if variance:
            warnings.append(
                warn(
                    RECONCILIATION_VARIANCE,
                    f"{len(variance)} check(s) did not balance: "
                    + ", ".join(c.name for c in variance)
                    + ". A rollup that disagrees with its own identity is a bug in "
                    "an aggregation job, not a rounding artefact.",
                    severity="error",
                    checks=[c.name for c in variance],
                )
            )
        if not_run:
            warnings.append(
                warn(
                    NOT_CONFIGURED,
                    f"{len(not_run)} check(s) could not be run: "
                    + ", ".join(c.name for c in not_run)
                    + ". They are listed with no values rather than omitted, so an "
                    "empty variance table is never mistaken for a clean one.",
                    severity="warn",
                    checks=[c.name for c in not_run],
                )
            )

        table = TableBlock(
            rows=[
                {
                    "check_name": c.name,
                    "period": f"{ctx.window.date_from.isoformat()}..{ctx.window.date_to.isoformat()}",
                    "status": c.status,
                    "source_value": c.source_value,
                    "rollup_value": c.rollup_value,
                    "variance_pct": c.variance_pct,
                    "detail": c.detail,
                }
                for c in checks
            ],
            total_rows=len(checks),
            truncated=False,
        )

        return ResolverResult(
            tables={self._table_id(ctx): table},
            series=series,
            sources=[state.ref],
            warnings=warnings,
            # The screen can never be better than its weakest check, and at
            # least one check is always unrunnable today.
            quality=MetricQuality.INCOMPLETE if not_run else MetricQuality.AUTHORITATIVE,
        ).rolled_up()

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _table_id(ctx: ResolverContext) -> str:
        return ctx.view.tables[0].id if ctx.view.tables else "variances"

    @staticmethod
    def _bridge(row: Mapping[str, Any]) -> RevenueBridge:
        return RevenueBridge(
            gross_merchandise_sales_minor=to_minor(row.get("gross_merchandise_sales") or 0),
            discounts_minor=to_minor(row.get("discount_sum") or 0),
            tax_minor=to_minor(row.get("tax_sum") or 0),
            shipping_minor=to_minor(row.get("shipping_income") or 0),
            cod_surcharge_minor=to_minor(row.get("cod_surcharge_sum") or 0),
            refunds_minor=to_minor(row.get("refund_sum") or 0),
            net_revenue_minor=to_minor(row.get("net_revenue") or 0),
        )

    def _bridge_check(self, rows: list[dict]) -> _Check:
        totals: dict[str, Decimal] = {}
        for row in rows:
            for key, value in row.items():
                if key == "bucket_date" or value is None:
                    continue
                totals[key] = Decimal(str(value)) + totals.get(key, Decimal("0"))
        bridge = self._bridge(totals)
        expected = Decimal(
            bridge.net_revenue_minor + bridge.imbalance_minor()
        ) / _HUNDRED
        actual = Decimal(bridge.net_revenue_minor) / _HUNDRED
        return _Check(
            name="revenue_bridge",
            status=CHECK_MATCHED if bridge.balances() else CHECK_VARIANCE,
            source_value=expected,
            rollup_value=actual,
            variance_pct=_ratio(
                Decimal(bridge.imbalance_minor()), bridge.net_revenue_minor
            ),
            detail=(
                "gross - discounts + tax + shipping + cod_surcharge - refunds "
                "must equal net_revenue"
            ),
        )

    def _variance_series(
        self, ctx: ResolverContext, rows: list[dict]
    ) -> dict[str, list[dict]]:
        """Daily bridge variance — points ONLY for days that were checked.

        Deliberately not densified. Elsewhere a missing day inside the covered
        range is a real zero; here a zero means "checked and balanced", so
        filling a gap with 0.00% would assert a check that never ran.
        """
        points = []
        for row in sorted(rows, key=lambda r: r["bucket_date"]):
            bridge = self._bridge(row)
            points.append(
                {
                    "date": row["bucket_date"].isoformat(),
                    "variance_pct": _ratio(
                        Decimal(bridge.imbalance_minor()), bridge.net_revenue_minor
                    ),
                    "imbalance": Decimal(bridge.imbalance_minor()) / _HUNDRED,
                }
            )
        charts = [c for c in ctx.view.charts if c.x == "date"]
        if charts and points:
            return {charts[0].id: points}
        return {}

    @staticmethod
    def _unavailable_checks() -> list[_Check]:
        return [
            _Check(
                name="gateway_settlement",
                status=CHECK_NOT_RUN,
                detail=(
                    "Captured payments cannot be compared against settled payouts "
                    "without the gateway settlement API. No settlement batch, fee or "
                    "payout date is available to check against."
                ),
            ),
            _Check(
                name="ga4_purchase_parity",
                status=CHECK_NOT_RUN,
                detail=(
                    "Internal orders cannot be compared against GA4 purchase events: "
                    "GA4 is not connected. GA4 revenue is never the accounting value "
                    "in any case — this check only ever measures tracking loss."
                ),
            ),
        ]


# ===========================================================================
# tracking health
# ===========================================================================

#: Every rollup whose freshness is worth watching. Probing all of them is the
#: one genuinely complete answer this module can give, because "did the pipeline
#: run" is a question the rollups CAN answer about themselves via their
#: watermarks.
HEALTH_SOURCES: tuple[tuple[str, str], ...] = (
    ("agg_order_daily", "Orders (daily)"),
    ("agg_order_hourly", "Orders (hourly)"),
    ("agg_product_daily", "Products (daily)"),
    ("agg_customer_daily", "Customers (daily)"),
    ("agg_customer_snapshot", "Customer snapshots"),
    ("agg_customer_cohort_monthly", "Cohorts (monthly)"),
    ("agg_payment_daily", "Payments (daily)"),
    ("agg_shipment_daily", "Shipments (daily)"),
    ("agg_geo_daily", "Geography (daily)"),
    ("agg_promo_daily", "Promotions (daily)"),
    ("agg_funnel_daily", "Funnel (daily)"),
    ("agg_inventory_daily", "Inventory (daily)"),
)


class TrackingHealthResolver:
    """Is data arriving, and how stale is each rollup.

    This is the one view that must never look healthy by default. A rollup that
    has never been built reports `never_built` with a NULL watermark — not "0
    days behind", which is what an empty table would imply and which is exactly
    backwards: never built is the *worst* state, not the freshest.

    The event-volume chart the registry declares is deliberately NOT emitted.
    Per-hour event counts need a client-side event stream (GA4 or equivalent)
    that is not connected; returning `{"event_volume": []}` would let the chart
    render an axis and read as "zero events received", which is a much stronger
    claim than "we cannot see events at all".
    """

    id = ResolverId.TRACKING_HEALTH.value

    def run(self, ctx: ResolverContext) -> ResolverResult:
        last_requested = date.fromordinal(ctx.window.date_to.toordinal() - 1)
        rows: list[dict] = []
        refs: list[SourceRef] = []
        never_built: list[str] = []
        stale: list[str] = []

        for source, label in HEALTH_SOURCES:
            watermark, count = ctx.repo.source_watermark(source, ctx.tz_generation)
            if watermark is None or count == 0:
                status, lag = "never_built", None
                never_built.append(source)
            else:
                lag = (last_requested - watermark).days
                if lag > 0:
                    status = "stale"
                    stale.append(source)
                else:
                    status = "current"
            rows.append(
                {
                    "source": source,
                    "label": label,
                    "status": status,
                    "through": watermark.isoformat() if watermark else None,
                    "rows": count,
                    # None, not 0: a rollup that was never built is not "0 days
                    # behind", and sorting a table by lag must not put it first.
                    "lag_days": lag,
                }
            )
            refs.append(
                source_ref(source, label=label, rows=count, through=watermark)
            )

        warnings: list[AnalyticsWarning] = []
        if never_built:
            warnings.append(
                warn(
                    WarningCode.NO_ROLLUP_YET,
                    f"{len(never_built)} rollup(s) have never been built for "
                    f"generation {ctx.tz_generation}: {', '.join(never_built)}. "
                    "Views built on them have nothing to show — which is not the "
                    "same as having nothing to report.",
                    severity="warn",
                    sources=never_built,
                )
            )
        if stale:
            warnings.append(
                warn(
                    WarningCode.ROLLUP_STALE,
                    f"{len(stale)} rollup(s) stop before {last_requested.isoformat()}: "
                    + ", ".join(stale),
                    severity="warn",
                    sources=stale,
                )
            )
        warnings.append(
            warn(
                NOT_CONFIGURED,
                "Event-level tracking health (events received per hour, tag "
                "coverage, consent state) needs a client-side analytics stream. "
                "None is connected, so no event series is returned — an empty "
                "chart would read as 'zero events', which is a stronger claim "
                "than 'we cannot see events'.",
                severity="warn",
                requires=[Capability.GA4_MEASUREMENT.value, Capability.GTM_CONTAINER.value],
            )
        )

        return ResolverResult(
            tables={
                self._table_id(ctx): TableBlock(
                    rows=rows, total_rows=len(rows), truncated=False
                )
            },
            sources=refs,
            warnings=warnings,
            # The rollup half is measured directly and is authoritative; the
            # event half cannot be seen at all, so the view as a whole is not.
            quality=MetricQuality.INCOMPLETE,
        ).rolled_up()

    @staticmethod
    def _table_id(ctx: ResolverContext) -> str:
        return ctx.view.tables[0].id if ctx.view.tables else "rollup_freshness"


# ===========================================================================
# custom
# ===========================================================================

CustomFn = Callable[[ResolverContext], ResolverResult]

#: Named functions `custom` may dispatch to. A closed registry, not a lookup
#: into module globals: dispatching on an attribute name would let a registry
#: typo call something that was never meant to be a resolver.
CUSTOM_FUNCTIONS: dict[str, CustomFn] = {}


def custom_function(name: str) -> Callable[[CustomFn], CustomFn]:
    def decorate(fn: CustomFn) -> CustomFn:
        if name in CUSTOM_FUNCTIONS:
            raise ResolverError(f"custom function {name!r} is already registered")
        CUSTOM_FUNCTIONS[name] = fn
        return fn

    return decorate


class CustomResolver:
    """Dispatches to a named function from the SERVER-TRUSTED registry entry.

    The name comes from `params["fn"]`, falling back to the view's `bespoke`
    key — both are registry values, never request values. An unknown or missing
    name **raises**. It does not return an empty result: an empty result renders
    as a view that loaded and found nothing, which would hide a registry typo
    behind a plausible-looking blank screen for as long as nobody checked.
    """

    id = ResolverId.CUSTOM.value

    def run(self, ctx: ResolverContext) -> ResolverResult:
        name = ctx.param("fn") or ctx.view.bespoke
        if not name:
            raise ResolverError(
                f"view {ctx.view.slug!r} uses the custom resolver but names no "
                f"function (params['fn'] or bespoke). Known: {sorted(CUSTOM_FUNCTIONS)}"
            )
        fn = CUSTOM_FUNCTIONS.get(str(name))
        if fn is None:
            raise ResolverError(
                f"unknown custom analytics function {name!r} for view "
                f"{ctx.view.slug!r}. Known: {sorted(CUSTOM_FUNCTIONS)}"
            )
        return fn(ctx)


@custom_function("revenue_waterfall")
def revenue_waterfall(ctx: ResolverContext) -> ResolverResult:
    """Gross merchandise sales down to contribution, as a waterfall.

    The steps are the revenue bridge, which balances by construction, so the
    waterfall cannot show a step that does not add up. The margin steps below it
    are a different matter: they exist only for lines that carried a `unit_cost`
    snapshot. Cost coverage is therefore reported alongside, and at zero
    coverage the margin KPIs come back null rather than showing the full
    merchandise value as profit.
    """
    state = probe_source(ctx, "agg_order_daily", label="Order rollup (daily)")
    warnings = list(state.warnings)

    totals = ctx.repo.fetch_totals(
        "agg_order_daily",
        columns=[
            "gross_merchandise_sales",
            "discount_sum",
            "tax_sum",
            "shipping_income",
            "cod_surcharge_sum",
            "refund_sum",
            "net_revenue",
            "net_merchandise_sales",
            "cogs_sum",
            "units",
            "costed_units",
        ],
        window=ctx.window,
        tz_generation=ctx.tz_generation,
    )

    bundle = compute_kpis(ctx, ctx.view.kpis)
    warnings.extend(bundle.warnings)

    series: dict[str, list[dict]] = {}
    coverage: Decimal | None = None

    if state.has_rows and totals.get("net_revenue") is not None:
        steps = [
            ("Gross merchandise sales", totals["gross_merchandise_sales"], 1),
            ("Discounts", totals["discount_sum"], -1),
            ("Tax collected", totals["tax_sum"], 1),
            ("Shipping income", totals["shipping_income"], 1),
            ("COD surcharge", totals["cod_surcharge_sum"], 1),
            ("Refunds", totals["refund_sum"], -1),
            ("Net revenue", totals["net_revenue"], 0),
        ]
        series["revenue_waterfall"] = [
            {
                "step": label,
                "amount": (Decimal(str(value or 0)) * sign) if sign else Decimal(str(value or 0)),
                "is_total": sign == 0,
            }
            for label, value, sign in steps
        ]
        units = Decimal(str(totals.get("units") or 0))
        costed = Decimal(str(totals.get("costed_units") or 0))
        if units > 0:
            coverage = (costed / units * _HUNDRED).quantize(Decimal("0.01"))
            if coverage < _HUNDRED:
                warnings.append(
                    warn(
                        WarningCode.COST_COVERAGE_LOW,
                        f"Only {coverage}% of units in this window carried a unit "
                        "cost, so every margin step below net revenue covers that "
                        "share of the business and no more. Uncovered lines are "
                        "excluded, never costed at zero.",
                        severity="warn",
                        coverage_pct=str(coverage),
                        units=int(units),
                        costed_units=int(costed),
                    )
                )
        charts = [c for c in ctx.view.charts if c.x == "date"]
        if charts:
            covered = state.covered_through(ctx.window)
            if covered is not None:
                from app.services.analytics.resolvers.core import METRIC_BINDINGS

                bindings = {
                    s: METRIC_BINDINGS[s]
                    for s in charts[0].series
                    if s in METRIC_BINDINGS
                }
                if bindings:
                    rows = ctx.repo.fetch_rollup(
                        "agg_order_daily",
                        columns=sorted({c for b in bindings.values() for c in b.columns}),
                        window=ctx.window,
                        tz_generation=ctx.tz_generation,
                        group_by=["bucket_date"],
                    )
                    points = dense_points(
                        rows, ctx.window, covered, Granularity.DAY, bindings
                    )
                    series[charts[0].id] = [
                        {charts[0].x: p["_x"], **{k: p.get(k) for k in bindings}}
                        for p in points
                    ]

    return ResolverResult(
        kpis=bundle.kpis,
        series=series,
        sources=[state.ref, *bundle.sources],
        warnings=warnings,
        coverage_pct=coverage,
    ).rolled_up()


@custom_function("journey_paths")
def journey_paths(ctx: ResolverContext) -> ResolverResult:
    """Cross-session touchpoint paths. Needs GA4; nothing internal substitutes.

    Internally only cart and order events exist, so the earliest touchpoint a
    journey could start from is add-to-cart — which is not a journey, it is the
    last step of one. Rather than present a two-node "path" as attribution, this
    returns the reason and no data at all.
    """
    return not_configured(
        ctx.view.limitation
        or "Cross-session journeys and attribution paths need GA4; internal "
        "events start at add-to-cart, which is the end of a journey, not one.",
        requires=_external_requirements(ctx) or [Capability.GA4_DATA_API.value],
        detail={"view": ctx.view.slug},
    )


@custom_function("experiment_results")
def experiment_results(ctx: ResolverContext) -> ResolverResult:
    """Per-variant experiment results. No experiment store exists yet.

    There is no assignment table, so there is no way to know which visitor saw
    which variant. Reporting the store's overall conversion rate under a single
    "control" variant would look like a running experiment with no effect, which
    is a far more misleading answer than none.
    """
    return not_configured(
        "No experiment definitions or variant assignments are recorded, so there "
        "is nothing to split by variant. A single-variant result would read as an "
        "experiment that ran and found no difference.",
        requires=[Capability.EXPERIMENTS.value],
        detail={"view": ctx.view.slug},
    )


register(FunnelResolver())
register(CohortResolver())
register(GeoResolver())
register(ReconciliationResolver())
register(TrackingHealthResolver())
register(CustomResolver())
