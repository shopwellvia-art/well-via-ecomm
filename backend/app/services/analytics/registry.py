"""The canonical analytics registry — 12 modules, 73 views.

This file *is* the specification. It is Python (not JSON, not a database table)
for three reasons:

  1. It has to be importable by the permission layer, the resolvers and the
     tests without a database, a migration or a running app.
  2. Every value here is type-checked against ``types.py`` at import time, so a
     typo in a permission name, a resolver id or a capability is a hard failure
     on boot rather than a blank panel in production.
  3. ``backend/scripts/dump_analytics_registry.py`` serialises it into
     ``frontend/src/features/analytics/registry.contract.json``. React never
     hand-maintains a second copy of the slugs, so the two cannot drift.

The `state` on each view is the honest ceiling of what this deployment can show
*today*. It is deliberately pessimistic: a runtime probe may downgrade a view
(LIVE -> PARTIAL when a rollup is still empty) but never upgrade one. Anything
that is not LIVE carries a `requires` tuple naming the missing capability and a
one-line `limitation` written for an admin to read: what is missing, and what
would fix it. No marketing copy, no "coming soon".

Context that drives most of the state decisions here — this deployment is a
SINGLE store with NO product variants, NO supplier / warehouse / subscription /
B2B / marketplace models, and NO external integrations connected. The inventory
ledger, cart events and order-line fact tables ship with this project, so they
count as available, but they start empty and cannot be backfilled.
"""
from __future__ import annotations

from .types import (
    AnalyticsModuleDefinition,
    AnalyticsViewDefinition,
    Capability,
    ChartSpec,
    DataSource,
    FilterKey,
    FormatId,
    Freshness,
    ResolverId,
    TableColumn,
    TableSpec,
    ViewState,
)

# --------------------------------------------------------------------------
# Shorthands
# --------------------------------------------------------------------------
# Thin factories only. They exist so 73 view definitions stay readable inside
# ~95 columns; they add no behaviour and the dump script never sees them.

_TIME = (FilterKey.DATE_RANGE, FilterKey.COMPARISON, FilterKey.GRANULARITY)
_RANGE = (FilterKey.DATE_RANGE, FilterKey.COMPARISON)


def _chart(
    cid: str,
    title: str,
    ctype: str,
    x: str,
    series: tuple[str, ...],
    fmt: FormatId = FormatId.INT,
    span: int = 2,
    hint: str = "",
) -> ChartSpec:
    return ChartSpec(
        id=cid, title=title, type=ctype, x=x, series=series, format=fmt, span=span,
        empty_hint=hint,
    )


def _col(key: str, label: str, fmt: FormatId = FormatId.TEXT, align: str = "left") -> TableColumn:
    return TableColumn(key=key, label=label, format=fmt, align=align)


def _table(
    tid: str,
    title: str,
    columns: tuple[TableColumn, ...],
    sort: str = "",
    hint: str = "",
) -> TableSpec:
    return TableSpec(id=tid, title=title, columns=columns, default_sort=sort, empty_hint=hint)


# Permissions, spelled once so a rename is a single edit.
P_EXEC = "analytics.executive.view"
P_SALES = "analytics.sales.view"
P_FINANCE = "analytics.finance.view"
P_PRODUCTS = "analytics.products.view"
P_CUSTOMERS = "analytics.customers.view"
P_MARKETING = "analytics.marketing.view"
P_WEBSITE = "analytics.website.view"
P_INVENTORY = "analytics.inventory.view"
P_ORDERS = "analytics.orders.view"
P_PAYMENTS = "analytics.payments.view"
P_MARKETPLACE = "analytics.marketplace.view"
P_CX = "analytics.cx.view"
P_CONTROL = "analytics.control_centre.view"


# --------------------------------------------------------------------------
# 1. Executive & Business Health
# --------------------------------------------------------------------------

_EXECUTIVE_VIEWS: tuple[AnalyticsViewDefinition, ...] = (
    AnalyticsViewDefinition(
        number=1,
        name="Executive Overview",
        slug="executive-overview",
        summary="One screen: revenue, orders, AOV, refunds and repeat rate against the "
                "previous period.",
        permission=P_EXEC,
        resolver=ResolverId.METRICS,
        freshness=Freshness.HOURLY,
        state=ViewState.LIVE,
        filters=_TIME,
        kpis=("net_revenue", "orders_count", "aov", "refund_rate", "repeat_purchase_rate"),
        charts=(
            _chart("revenue_trend", "Net revenue", "area", "date", ("net_revenue",),
                   FormatId.MONEY),
            _chart("orders_trend", "Orders", "bar", "date", ("orders_count",), FormatId.INT, 1),
            _chart("aov_trend", "Average order value", "line", "date", ("aov",),
                   FormatId.MONEY, 1),
        ),
        # Every card here is a catalogue metric already bound to a column of
        # `agg_order_daily`; naming the source pins the rollup explicitly rather
        # than inheriting `resolvers.core.DEFAULT_SOURCE`. `repeat_purchase_rate`
        # stays unbound on purpose: it counts customers with >= 2 LIFETIME paid
        # orders, and no rollup stores that population.
        params={"source": "agg_order_daily"},
        keywords=("summary", "kpi", "overview", "board"),
    ),
    AnalyticsViewDefinition(
        number=2,
        name="Real-Time Sales",
        slug="real-time-sales",
        summary="Orders and revenue as they land today, with the latest orders and payment "
                "outcomes.",
        permission=P_EXEC,
        resolver=ResolverId.TIMESERIES,
        freshness=Freshness.REALTIME,
        state=ViewState.LIVE,
        filters=(FilterKey.DATE_RANGE, FilterKey.GRANULARITY),
        kpis=("orders_count", "net_revenue", "aov", "payment_success_rate"),
        charts=(
            _chart("orders_by_hour", "Orders per hour", "bar", "hour", ("orders_count",)),
            _chart("revenue_by_hour", "Revenue per hour", "line", "hour", ("net_revenue",),
                   FormatId.MONEY),
        ),
        tables=(
            _table(
                "recent_orders",
                "Latest orders",
                (
                    _col("order_no", "Order"),
                    _col("placed_at", "Placed"),
                    _col("items", "Items", FormatId.INT, "right"),
                    _col("total", "Total", FormatId.MONEY, "right"),
                    _col("status", "Status"),
                ),
                sort="placed_at",
            ),
        ),
        # NOT exportable. `recent_orders` is an order-level list — order number,
        # placed-at, status — and every source the analytics repository may read
        # is a DAILY rollup. No resolver can produce these rows, so an export
        # button here is a button that returns 404 every time. The hourly charts
        # above are what this view actually measures, and they work.
        export=False,
        params={"source": "agg_order_daily", "hourly_source": "agg_order_hourly"},
        keywords=("live", "today", "now"),
    ),
    AnalyticsViewDefinition(
        number=55,
        name="Sales Forecasting",
        slug="sales-forecasting",
        # PARTIAL: the maths is fine, the history is not. A forecast off a few
        # weeks of orders is a trend line, not a seasonal model — say so.
        summary="Projected revenue and orders for the next periods from the order history "
                "accumulated so far.",
        permission=P_EXEC,
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.DAILY,
        state=ViewState.PARTIAL,
        requires=(Capability.ORDER_LINE_FACT,),
        filters=_TIME,
        kpis=("net_revenue", "orders_count"),
        charts=(
            # The band members are declared, not just the point estimate. The
            # resolver emits `forecast_lower`/`forecast_upper` on every projected
            # point, but a series the chart spec does not name is never rendered
            # — so without these two the interval would exist in the payload and
            # be invisible on screen, which is a point estimate wearing a
            # forecast's clothes.
            _chart("actual_vs_forecast", "Actual vs forecast revenue", "line", "date",
                   ("net_revenue", "forecast_revenue", "forecast_lower", "forecast_upper"),
                   FormatId.MONEY),
        ),
        # `forecast_revenue` has no stored column and stays unbound: the actual
        # line is drawn, the forecast line is absent rather than invented.
        params={"fn": "sales_forecast", "source": "agg_order_daily", "forecast_column": "net_revenue", "actual_key": "net_revenue", "forecast_key": "forecast_revenue", "series_id": "actual_vs_forecast", "history_days": 180, "horizon_days": 14, "min_history_days": 28, "confidence": 0.80},
        limitation="Only a few weeks of line-level order history exist, so the projection is a "
                   "trend extrapolation with no seasonal model; accuracy improves as history "
                   "accumulates.",
        keywords=("projection", "forecast", "plan"),
    ),
    AnalyticsViewDefinition(
        number=56,
        name="Seasonal and Festival Sales",
        slug="seasonal-and-festival-sales",
        # PARTIAL: year-on-year is the whole point of this view and there is no
        # prior year in the data yet.
        summary="Demand peaks around festivals and sale windows, by category and day.",
        permission=P_EXEC,
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.DAILY,
        state=ViewState.PARTIAL,
        requires=(Capability.ORDER_LINE_FACT,),
        filters=_TIME + (FilterKey.CATEGORY,),
        kpis=("net_revenue", "orders_count", "aov"),
        charts=(
            _chart("seasonal_revenue", "Revenue by day", "area", "date", ("net_revenue",),
                   FormatId.MONEY),
            _chart("peak_categories", "Category mix during peaks", "stacked-bar", "date",
                   ("net_revenue",), FormatId.MONEY),
        ),
        params={"fn": "seasonality", "source": "agg_order_daily", "seasonality_column": "net_revenue", "history_days": 400, "actuals_series_id": "seasonal_revenue"},
        limitation="Year-on-year comparison needs a full prior year of orders, which does not "
                   "exist yet; until then only peaks within the selected range are shown.",
        keywords=("festival", "diwali", "seasonality", "peak"),
    ),
    AnalyticsViewDefinition(
        number=60,
        name="E-commerce Business Health",
        slug="ecommerce-business-health",
        summary="Scorecard of the handful of ratios that say whether the store is healthy: "
                "conversion, repeat rate, refunds, abandonment.",
        permission=P_EXEC,
        resolver=ResolverId.METRICS,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
        filters=_TIME,
        kpis=(
            "conversion_rate", "repeat_purchase_rate", "refund_rate", "cart_abandonment_rate",
            "aov", "net_revenue",
        ),
        charts=(
            _chart("health_trend", "Health ratios over time", "line", "date",
                   ("conversion_rate", "repeat_purchase_rate", "refund_rate"), FormatId.PCT),
        ),
        # Only `refund_rate`, `aov` and `net_revenue` are bound. `conversion_rate`
        # divides by GA4 sessions, `cart_abandonment_rate` needs cart history and
        # `repeat_purchase_rate` needs a lifetime customer population — none of
        # which exists here, so those three cards report as unavailable.
        params={"source": "agg_order_daily"},
        keywords=("scorecard", "health", "ratios"),
    ),
    AnalyticsViewDefinition(
        number=72,
        name="Budget vs Actual",
        slug="budget-vs-actual",
        # LIVE: analytics_budgets ships with this project. It is empty until an
        # admin enters targets, which is an empty state, not a missing source.
        summary="Revenue and order targets against actuals, by month and category.",
        permission=P_EXEC,
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
        filters=_RANGE + (FilterKey.CATEGORY,),
        kpis=("net_revenue", "orders_count"),
        charts=(
            _chart("budget_vs_actual", "Budget vs actual revenue", "bar", "period",
                   ("budget_revenue", "net_revenue"), FormatId.MONEY,
                   hint="No budgets have been entered yet."),
        ),
        tables=(
            _table(
                "budget_lines",
                "Targets vs actuals",
                (
                    _col("period", "Period"),
                    _col("scope", "Scope"),
                    _col("budget", "Target", FormatId.MONEY, "right"),
                    _col("actual", "Actual", FormatId.MONEY, "right"),
                    _col("variance_pct", "Variance", FormatId.PCT, "right"),
                ),
                sort="period",
                hint="No budgets have been entered yet.",
            ),
        ),
        export=True,
        # The actuals are ordinary rollup columns; the targets are authored rows
        # in `analytics_budgets` that no rollup mirrors, so the two halves have
        # to be joined server-side. `budget_vs_actual` does that and — while the
        # table is empty — returns the actuals plus NOT_CONFIGURED, never a 0%
        # attainment, which would read as a catastrophic miss rather than as an
        # absent target.
        params={"fn": "budget_vs_actual", "source": "agg_order_daily"},
        keywords=("target", "plan", "variance"),
    ),
)


# --------------------------------------------------------------------------
# 2. Sales, Revenue & Finance
# --------------------------------------------------------------------------

_SALES_VIEWS: tuple[AnalyticsViewDefinition, ...] = (
    AnalyticsViewDefinition(
        number=3,
        name="Revenue and Profitability",
        slug="revenue-and-profitability",
        # PARTIAL: the waterfall down to net revenue is authoritative, but every
        # step below it depends on how many products have a cost rule.
        summary="Waterfall from gross sales through discounts, returns and cost to "
                "contribution.",
        permission=P_SALES,
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.DAILY,
        state=ViewState.PARTIAL,
        requires=(Capability.COST_RULES, Capability.ORDER_LINE_FACT),
        filters=_TIME + (FilterKey.CATEGORY,),
        kpis=("gross_merchandise_sales", "net_merchandise_sales", "net_revenue", "cm1",
              "cm1_pct", "gross_margin_pct"),
        charts=(
            _chart("revenue_waterfall", "Gross to contribution", "waterfall", "step",
                   ("amount",), FormatId.MONEY),
            _chart("margin_trend", "Contribution margin over time", "line", "date",
                   ("cm1", "cm1_pct"), FormatId.MONEY),
        ),
        bespoke="revenue_waterfall",
        # `bespoke` is the FRONTEND component key; `fn` is what dispatches on the
        # server. They happen to share a name here, and naming `fn` explicitly is
        # what keeps that a coincidence rather than a contract.
        params={"fn": "revenue_waterfall", "source": "agg_order_daily"},
        limitation="Margin steps only cover orders whose products have a cost rule; uncovered "
                   "lines are reported separately and never zero-filled.",
        keywords=("waterfall", "profit", "margin", "gross"),
    ),
    AnalyticsViewDefinition(
        number=4,
        name="Orders and Average Order Value",
        slug="orders-and-average-order-value",
        summary="Order volume, basket size and AOV, including cancellations.",
        permission=P_SALES,
        resolver=ResolverId.TIMESERIES,
        freshness=Freshness.HOURLY,
        state=ViewState.LIVE,
        filters=_TIME + (FilterKey.ORDER_STATUS, FilterKey.PAYMENT_METHOD),
        kpis=("orders_count", "aov", "units_sold", "cancellation_rate"),
        charts=(
            _chart("orders_over_time", "Orders", "bar", "date", ("orders_count",)),
            _chart("aov_over_time", "Average order value", "line", "date", ("aov",),
                   FormatId.MONEY),
            _chart("basket_size", "Orders by basket value band", "hbar", "band",
                   ("orders_count",), FormatId.INT, 1),
        ),
        params={"source": "agg_order_daily", "hourly_source": "agg_order_hourly"},
        keywords=("aov", "basket", "volume"),
    ),
    AnalyticsViewDefinition(
        number=25,
        name="Coupon and Discount Performance",
        slug="coupon-and-discount-performance",
        summary="What each coupon cost, what it sold, and whether discounted baskets are "
                "bigger.",
        permission=P_SALES,
        resolver=ResolverId.BREAKDOWN,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
        filters=_TIME + (FilterKey.COUPON, FilterKey.CATEGORY),
        kpis=("discounts", "orders_count", "net_revenue", "aov"),
        charts=(
            _chart("discount_trend", "Discount given", "area", "date", ("discount_amount",),
                   FormatId.MONEY),
            _chart("coupon_revenue", "Revenue by coupon", "hbar", "coupon", ("net_revenue",),
                   FormatId.MONEY),
        ),
        tables=(
            _table(
                "coupon_table",
                "Coupons",
                (
                    _col("code", "Code"),
                    _col("uses", "Uses", FormatId.INT, "right"),
                    _col("discount", "Discount", FormatId.MONEY, "right"),
                    _col("revenue", "Revenue", FormatId.MONEY, "right"),
                    _col("aov", "AOV", FormatId.MONEY, "right"),
                ),
                sort="revenue",
            ),
        ),
        export=True,
        # `discounts` (the catalogue KPI) is NOT bound to `discount_amount`: the
        # catalogue figure is every discount on paid orders, while this rollup
        # holds only what coupon codes gave away. The table's own `discount` key
        # carries the coupon number under its own name instead.
        params={
            "source": "agg_promo_daily",
            "dimension": "coupon",
            "metrics": {
                "revenue": {"add": ["order_revenue"]},
                "discount": {"add": ["discount_amount"]},
                "uses": {"add": ["redemptions"]},
                "orders": {"add": ["orders"]},
            },
        },
        keywords=("coupon", "promo", "discount"),
    ),
    AnalyticsViewDefinition(
        number=47,
        name="Pricing and Margin",
        slug="pricing-and-margin",
        summary="Selling price, cost and margin per product, with discount depth.",
        permission=P_FINANCE,
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.DAILY,
        state=ViewState.PARTIAL,
        requires=(Capability.COST_RULES,),
        filters=_RANGE + (FilterKey.CATEGORY, FilterKey.PRODUCT),
        kpis=("gross_margin_pct", "cm1_pct", "cogs", "average_selling_price", "net_revenue"),
        charts=(
            _chart("margin_by_category", "Margin by category", "hbar", "category",
                   ("gross_margin_pct",), FormatId.PCT),
        ),
        tables=(
            _table(
                "pricing_table",
                "Products",
                (
                    _col("product", "Product"),
                    _col("sku", "SKU"),
                    _col("price", "Price", FormatId.MONEY, "right"),
                    _col("cost", "Cost", FormatId.MONEY, "right"),
                    _col("margin_pct", "Margin", FormatId.PCT, "right"),
                    _col("units", "Units", FormatId.INT, "right"),
                ),
                sort="margin_pct",
            ),
        ),
        export=True,
        # Only the identity and volume columns exist at product grain. `price`,
        # `cost` and `margin_pct` are left unmapped rather than filled from
        # `line_cost` (a window total, not a unit cost) — an unfilled column
        # reads as "not available", a wrong one reads as a unit economics fact.
        #
        # `margin_cascade` runs the `table` shape below for the product rows and
        # then overwrites the margin CARDS from the cost engine, because
        # `gross_margin_pct` / `cm1_pct` / `cogs` off `agg_product_daily` stop at
        # product cost and cannot see a cost rule at all. `average_selling_price`
        # stays deliberately unbound: no rollup stores a per-unit price.
        params={
            "fn": "margin_cascade",
            "source": "agg_product_daily",
            "table": "pricing_table",
            "group_by": ["product_id", "sku_snapshot"],
            "columns": {
                "product": "product_id",
                "sku": "sku_snapshot",
                "units": "units",
            },
        },
        limitation="Products without a cost rule are listed as uncovered and excluded from "
                   "margin; enter costs under analytics cost rules to complete the picture.",
        keywords=("price", "cogs", "markdown"),
    ),
    AnalyticsViewDefinition(
        number=67,
        name="Contribution Margin",
        slug="contribution-margin",
        summary="CM1 and CM2 per order and per category after product cost, shipping and "
                "payment fees.",
        permission=P_FINANCE,
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.DAILY,
        state=ViewState.PARTIAL,
        requires=(Capability.COST_RULES, Capability.ORDER_LINE_FACT),
        filters=_TIME + (FilterKey.CATEGORY, FilterKey.PAYMENT_METHOD),
        kpis=("cm1", "cm1_pct", "cm2", "cm2_pct", "gateway_fees", "net_merchandise_sales"),
        charts=(
            _chart("cm_trend", "Contribution margin", "line", "date", ("cm1",), FormatId.MONEY),
            _chart("cm_by_category", "CM1 by category", "hbar", "category", ("cm1",),
                   FormatId.MONEY),
        ),
        # CM2 and CM3 are the whole point of this view and no rollup column can
        # hold either: they are the output of effective-dated cost rules, which
        # are resolved per reporting day. `margin_cascade` runs the timeseries
        # shape for the CM1 trend off `agg_order_daily`, then computes the cards
        # live through `MarginService`. With no cost rule configured, CM2/CM3
        # come back null naming every absent rule — never CM1 under CM2's label.
        params={"fn": "margin_cascade", "source": "agg_order_daily"},
        limitation="CM covers only orders whose products have a cost rule, and shipping and "
                   "gateway fees are allocated by rule rather than observed per order.",
        keywords=("cm1", "cm2", "contribution"),
    ),
    AnalyticsViewDefinition(
        number=68,
        name="Unit Economics",
        slug="unit-economics",
        summary="What one order is worth after variable cost, and how that compares with "
                "lifetime value.",
        permission=P_FINANCE,
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.DAILY,
        state=ViewState.PARTIAL,
        requires=(Capability.COST_RULES, Capability.AD_PLATFORM),
        filters=_TIME + (FilterKey.CATEGORY,),
        kpis=("aov", "cm1", "cm1_pct", "ltv"),
        charts=(
            _chart("cm_per_order", "Contribution per order", "line", "date", ("cm1_per_order",),
                   FormatId.MONEY),
        ),
        # `cm1_per_order` divides two stored columns and so cannot be a metric
        # binding; `cac` needs marketing spend, which only a MARKETING_SPEND cost
        # rule can supply here. `unit_economics` computes both, reports CAC as
        # null with its inputs named while no rule exists, and never treats
        # unconnected ad spend as zero-cost acquisition.
        params={"fn": "unit_economics", "source": "agg_order_daily"},
        limitation="Acquisition cost is excluded until an ad platform is connected, so this is "
                   "contribution economics only; it also depends on product cost coverage.",
        keywords=("ltv", "cac", "payback", "unit"),
    ),
    AnalyticsViewDefinition(
        number=69,
        name="Cash Flow and Working Capital",
        slug="cash-flow-and-working-capital",
        # Approved decision: settlement-derived cash is not cash. A captured
        # payment is a promise; only a bank feed says money arrived.
        summary="Money actually in and out of the bank, and the working capital tied up in "
                "stock and receivables.",
        permission=P_FINANCE,
        resolver=ResolverId.TIMESERIES,
        freshness=Freshness.DAILY,
        state=ViewState.INTEGRATION_REQUIRED,
        sources=(DataSource.INTERNAL_DB,),
        requires=(Capability.BANK_CASH_FEED,),
        filters=_RANGE,
        limitation="Needs a bank or cash feed. Captured payments and gateway settlements are "
                   "not cash and will not be used as a substitute.",
        keywords=("cash", "runway", "working capital"),
    ),
    AnalyticsViewDefinition(
        number=70,
        name="Tax and GST",
        slug="tax-and-gst",
        summary="Tax collected by rate slab and place of supply, for operational review.",
        permission=P_FINANCE,
        resolver=ResolverId.TABLE,
        freshness=Freshness.DAILY,
        state=ViewState.PARTIAL,
        sources=(DataSource.INTERNAL_DB,),
        requires=(Capability.HSN_TAX_DETAIL,),
        filters=_RANGE + (FilterKey.STATE, FilterKey.CATEGORY),
        kpis=("tax_collected", "net_revenue", "orders_count"),
        charts=(
            _chart("tax_by_slab", "Tax collected by rate", "hbar", "rate", ("tax_amount",),
                   FormatId.MONEY),
        ),
        tables=(
            _table(
                "tax_table",
                "Tax by slab and state",
                (
                    _col("rate", "Rate"),
                    _col("state", "Place of supply"),
                    _col("taxable_value", "Taxable value", FormatId.MONEY, "right"),
                    _col("tax_amount", "Tax", FormatId.MONEY, "right"),
                ),
                sort="tax_amount",
            ),
        ),
        export=True,
        # Bound to the only tax figure this schema actually holds: the
        # order-level `orders.tax_amount`, rolled up as `tax_sum`, against the
        # pre-discount merchandise value it was computed on (`orders.subtotal`
        # -> `subtotal_sum`; cart_service applies tax per item on the list
        # price, before any coupon).
        #
        # `rate` and `state` are left UNMAPPED on purpose and stay blank. There
        # is no HSN code, no rate slab, no place of supply and no reverse-charge
        # flag anywhere in the schema, so a rate column filled from anything
        # available would be an invented slab, and a place of supply taken from
        # the delivery address would silently assert an intra/inter-state call
        # this system never made. A blank column reads as "not available"; a
        # filled one would read as a filing-ready fact. The view therefore stays
        # PARTIAL and keeps its NOT-compliance-grade limitation.
        params={
            "source": "agg_order_daily",
            "table": "tax_table",
            "columns": {
                "taxable_value": "subtotal_sum",
                "tax_amount": "tax_sum",
            },
        },
        limitation="Operational reporting only — HSN-level detail is not stored, so these "
                   "figures are NOT compliance-grade and must not be used for GST filing.",
        keywords=("gst", "tax", "hsn", "slab"),
    ),
)


# --------------------------------------------------------------------------
# 3. Products & Merchandising
# --------------------------------------------------------------------------

_PRODUCT_VIEWS: tuple[AnalyticsViewDefinition, ...] = (
    AnalyticsViewDefinition(
        number=5,
        name="Product Performance",
        slug="product-performance",
        summary="Units, revenue and returns per product, with best and worst sellers.",
        permission=P_PRODUCTS,
        resolver=ResolverId.BREAKDOWN,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
        filters=_TIME + (FilterKey.CATEGORY, FilterKey.PRODUCT),
        kpis=("units_sold", "net_revenue", "aov", "return_rate"),
        charts=(
            _chart("top_products", "Top products by revenue", "hbar", "product",
                   ("net_revenue",), FormatId.MONEY),
            _chart("product_revenue_trend", "Revenue", "line", "date", ("net_revenue",),
                   FormatId.MONEY),
        ),
        tables=(
            _table(
                "product_table",
                "Products",
                (
                    _col("product", "Product"),
                    _col("units", "Units", FormatId.INT, "right"),
                    _col("revenue", "Revenue", FormatId.MONEY, "right"),
                    _col("return_rate", "Returns", FormatId.PCT, "right"),
                ),
                sort="revenue",
            ),
        ),
        export=True,
        # `net_revenue` is deliberately NOT bound at this grain. Its catalogue
        # definition is order-basis (tax and shipping included, refunds
        # deducted) and its `dimensions` tuple does not list `product`, because
        # neither tax nor shipping can be attributed to a line. `revenue` here is
        # net merchandise sales, which genuinely is per-product, and it is
        # carried under the table's own key rather than under a KPI id that
        # means something else.
        params={
            "source": "agg_product_daily",
            "dimension": "product",
            "metrics": {
                "revenue": {"add": ["net_merchandise_sales"]},
                "units": {"add": ["units"]},
                "return_rate": {
                    "add": ["returned_units"],
                    "over": ["units"],
                    "scale": 100,
                },
            },
        },
        keywords=("bestseller", "product", "sku"),
    ),
    AnalyticsViewDefinition(
        number=6,
        name="Category Performance",
        slug="category-performance",
        summary="Revenue, units and mix by category and sub-category.",
        permission=P_PRODUCTS,
        resolver=ResolverId.BREAKDOWN,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
        filters=_TIME + (FilterKey.CATEGORY,),
        kpis=("net_revenue", "units_sold", "aov"),
        charts=(
            _chart("category_revenue", "Revenue by category", "hbar", "category",
                   ("net_revenue",), FormatId.MONEY),
            _chart("category_mix", "Share of revenue", "donut", "category", ("net_revenue",),
                   FormatId.MONEY, 1),
        ),
        tables=(
            _table(
                "category_table",
                "Categories",
                (
                    _col("category", "Category"),
                    _col("units", "Units", FormatId.INT, "right"),
                    _col("revenue", "Revenue", FormatId.MONEY, "right"),
                    _col("share_pct", "Share", FormatId.PCT, "right"),
                ),
                sort="revenue",
            ),
        ),
        export=True,
        # `share_pct` needs a total across every category in the window, which
        # this resolver does not compute; it is left out rather than filled with
        # a share of the top-N only.
        params={
            "source": "agg_product_daily",
            "dimension": "category",
            "metrics": {
                "revenue": {"add": ["net_merchandise_sales"]},
                "units": {"add": ["units"]},
            },
        },
        keywords=("category", "mix", "department"),
    ),
    AnalyticsViewDefinition(
        number=7,
        name="SKU-Level Analytics",
        slug="sku-level-analytics",
        # PARTIAL: there is no variant model, so "SKU" and "product" are the same
        # row. The view still works, it just cannot split by size or colour.
        summary="Per-SKU sales velocity, stock position and revenue contribution.",
        permission=P_PRODUCTS,
        resolver=ResolverId.TABLE,
        freshness=Freshness.DAILY,
        state=ViewState.PARTIAL,
        requires=(Capability.PRODUCT_VARIANTS,),
        filters=_TIME + (FilterKey.SKU, FilterKey.CATEGORY),
        kpis=("units_sold", "net_revenue", "stockout_rate"),
        charts=(
            _chart("sku_velocity", "Units per day by SKU", "line", "date", ("units_sold",)),
        ),
        tables=(
            _table(
                "sku_table",
                "SKUs",
                (
                    _col("sku", "SKU"),
                    _col("product", "Product"),
                    _col("units", "Units", FormatId.INT, "right"),
                    _col("revenue", "Revenue", FormatId.MONEY, "right"),
                    _col("stock", "Stock", FormatId.INT, "right"),
                ),
                sort="units",
            ),
        ),
        export=True,
        # `stock` lives in `agg_inventory_daily`, a different rollup at a
        # different grain, so it stays unmapped here rather than being joined in
        # at read time.
        params={
            "source": "agg_product_daily",
            "table": "sku_table",
            "group_by": ["product_id", "sku_snapshot"],
            "columns": {
                "sku": "sku_snapshot",
                "product": "product_id",
                "units": "units",
                "revenue": "net_merchandise_sales",
            },
        },
        limitation="There is no variant model, so every product has exactly one SKU; size, "
                   "colour and pack-size splits are not possible until variants exist.",
        keywords=("sku", "variant", "velocity"),
    ),
    AnalyticsViewDefinition(
        number=46,
        name="Product Recommendation Performance",
        slug="product-recommendation-performance",
        summary="Impressions, clicks and revenue attributed to recommendation slots.",
        permission=P_PRODUCTS,
        resolver=ResolverId.BREAKDOWN,
        freshness=Freshness.DAILY,
        # Stays FEATURE_REQUIRED, and `agg_basket_pair_daily` landing does not
        # change that. The basket rollup answers "which two products appear in
        # the same order more often than chance" — an observation about what
        # customers already did unprompted. This view asks what a recommender
        # *caused*: impressions of a slot, clicks on it, and revenue attributed
        # to it. Those need a recommender that served something and logging of
        # what it served. Neither exists — there is no recommendation slot in
        # the storefront and no impression or click event for one.
        #
        # So the pair rollup is not the substrate for this view, and binding it
        # here would be wrong twice over: it would report co-occurrence under
        # three column headings that promise causation, and it would duplicate
        # view 57 (Product Bundling and Cross-Sell), which is already LIVE on
        # that exact rollup and is where "frequently bought together" belongs.
        # If a recommender is ever built, the pair rollup is a reasonable thing
        # to seed it *from*; it is not a measurement of it.
        state=ViewState.FEATURE_REQUIRED,
        requires=(Capability.RECOMMENDATION_ENGINE,),
        filters=_RANGE,
        limitation="No recommendation engine is running, so there are no recommendation "
                   "impressions, clicks or attributed orders to measure.",
        keywords=("recommendation", "you may also like"),
    ),
    AnalyticsViewDefinition(
        number=57,
        name="Product Bundling and Cross-Sell",
        slug="product-bundling-and-cross-sell",
        summary="Which products are bought together more often than chance, from actual "
                "baskets.",
        permission=P_PRODUCTS,
        # CUSTOM, not TABLE, and that is forced by the rollup rather than chosen:
        # `agg_basket_pair_daily` stores COUNTS (pair_orders, orders_with_a,
        # orders_with_b, total_orders_in_bucket) and this view shows RATIOs.
        # There is no `lift` column for a table binding to project, deliberately
        # — a stored daily lift cannot be re-bucketed into a week, because the
        # average of daily lifts is not the lift of their union. `resolvers/
        # basket.py` recomputes support, confidence and lift from the summed
        # counts at the moment the window is known.
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
        # DATE_RANGE only, and the two that were here before are gone on purpose.
        # A CATEGORY filter has nothing to bind to: the pair rollup carries no
        # category, and a pair spans two products that may sit in different ones,
        # so "revenue in category X" has no analogue here. A PRODUCT filter is
        # worse than useless on an unordered pair — the product sits in
        # product_a_id or product_b_id depending on which id is lower, so
        # filtering one column silently returns half the pairs that mention it.
        # A filter a view declares and does not honour is a control that appears
        # to work.
        filters=(FilterKey.DATE_RANGE,),
        kpis=("basket_attach_rate", "basket_pairs_observed"),
        charts=(
            _chart("top_pairs", "Most frequent pairs", "hbar", "pair", ("orders_count",),
                   hint="No two products have been bought together yet."),
        ),
        tables=(
            _table(
                "basket_pairs",
                "Frequently bought together",
                (
                    _col("product_a", "Product A"),
                    _col("product_b", "Product B"),
                    _col("orders", "Orders together", FormatId.INT, "right"),
                    # How many DAYS the pair co-occurred. On the table because
                    # every ratio beside it is computed over exactly those days,
                    # so "20 baskets across 30 days" and "20 baskets on one
                    # festival day" are otherwise indistinguishable rows.
                    _col("days_observed", "Days seen", FormatId.INT, "right"),
                    _col("support", "Support", FormatId.PCT, "right"),
                    _col("confidence", "Confidence", FormatId.PCT, "right"),
                    _col("lift", "Lift", FormatId.RATIO, "right"),
                ),
                # Lift, not co-occurrence count: sorting by count just reprints
                # the bestseller list, because the two most popular products
                # co-occur most whether or not they have anything to do with
                # each other. The resolver breaks lift ties on support and ranks
                # every small-sample pair below every pair that clears the floor.
                sort="lift",
                hint="No two products have been bought together yet.",
            ),
        ),
        export=True,
        params={"fn": "basket_cross_sell", "source": "agg_basket_pair_daily"},
        keywords=("bundle", "cross-sell", "market basket", "affinity", "lift"),
    ),
    AnalyticsViewDefinition(
        number=58,
        name="Upsell Performance",
        slug="upsell-performance",
        summary="Whether upsell offers work: offers shown, offers taken, and the order "
                "value they add.",
        permission=P_PRODUCTS,
        resolver=ResolverId.BREAKDOWN,
        freshness=Freshness.DAILY,
        # FEATURE_REQUIRED, not PARTIAL — reassessed after `agg_basket_pair_daily`
        # landed, and the honest answer is that no subset of "upsell performance"
        # exists in this deployment's data. Three candidates were checked against
        # the real schema before binding nothing:
        #
        #   * Offer conversion / acceptance / attributed revenue: needs an upsell
        #     placement that records impressions and clicks. The storefront has
        #     none, so those metrics have neither a numerator nor a denominator.
        #     This was the original gap and it is still the whole gap.
        #   * "Traded up to a higher-value item": the pair rollup deliberately
        #     carries NO money column ("a basket composition table, not a revenue
        #     table" — models/analytics_basket.py), and its name snapshots must
        #     not be resolved through the live catalogue for a price. A price
        #     step between paired products is therefore not computable from any
        #     source the repository can read.
        #   * "Baskets with an add-on are worth more" (AOV multi- vs single-item):
        #     needs order value split by basket size. `agg_order_daily` is one
        #     row a day with aggregate money; no rollup stores that split, and
        #     building one is an aggregation change, not a wiring change, so it
        #     is deliberately not done here.
        #
        # What IS computable — the share of orders holding a second distinct
        # product, and its trend — is `basket_attach_rate`: view 57's headline
        # KPI, served from the same rows. Binding it here would republish the
        # cross-sell number under an upsell heading, and without prices "traded
        # up" cannot even be told apart from "bought together". PARTIAL means
        # reduced data; the reduced subset that is honestly *upsell* is empty,
        # so the state is FEATURE_REQUIRED and `params` stays empty on purpose.
        # tests/test_analytics_upsell.py pins the schema facts above — if a
        # price or a basket-size split ever lands, it fails and forces this
        # state to be reargued.
        state=ViewState.FEATURE_REQUIRED,
        requires=(Capability.RECOMMENDATION_ENGINE,),
        filters=_RANGE,
        # No kpis and no charts. The previous declarations ("AOV with vs without
        # add-on") described exactly the basket inference ruled out above, and a
        # gated view's declarations are a promise about what will render when
        # the capability lands — impressions, acceptance rate and attributed
        # revenue, whose shapes cannot be named until a placement exists.
        limitation="No upsell placement exists in the storefront, so no offer impression, "
                   "click or acceptance is ever recorded — offer conversion has neither a "
                   "numerator nor a denominator. The basket data that does exist cannot "
                   "stand in: the pair rollup stores counts and name snapshots with no "
                   "prices, and no rollup splits order value by basket size, so a "
                   "trade-up cannot be told apart from a co-purchase, which Product "
                   "Bundling and Cross-Sell already reports.",
        keywords=("upsell", "add-on", "uplift"),
    ),
)


# --------------------------------------------------------------------------
# 4. Customers & Retention
# --------------------------------------------------------------------------
# Every view in this module can drill down to a named customer, which is why the
# whole module sits behind the sensitive analytics.customers.view permission.

_CUSTOMER_VIEWS: tuple[AnalyticsViewDefinition, ...] = (
    AnalyticsViewDefinition(
        number=8,
        name="Customer Analytics",
        slug="customer-analytics",
        summary="Active, new and returning customers, orders per customer and spend "
                "distribution.",
        permission=P_CUSTOMERS,
        resolver=ResolverId.METRICS,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
        filters=_TIME + (FilterKey.CUSTOMER_SEGMENT, FilterKey.NEW_OR_RETURNING),
        kpis=("new_customers", "returning_customers", "repeat_purchase_rate",
              "customer_retention_rate", "aov"),
        charts=(
            _chart("customers_trend", "Customers who ordered", "line", "date",
                   ("customers", "new_customers")),
            _chart("orders_per_customer", "Orders per customer", "hbar", "band",
                   ("customers",), FormatId.INT, 1),
        ),
        # `new_customers`, `returning_customers` and `aov` are bound in the
        # catalogue against this rollup. `repeat_purchase_rate` (>= 2 lifetime
        # paid orders) and `customer_retention_rate` (customers active in BOTH
        # of two windows) are set operations over customer history, not sums of
        # daily counters, so no binding here can produce them honestly.
        params={"source": "agg_order_daily"},
        keywords=("customers", "buyers", "accounts"),
    ),
    AnalyticsViewDefinition(
        number=9,
        name="New vs Returning Customers",
        slug="new-vs-returning-customers",
        summary="Split of orders, revenue and AOV between first-time and repeat buyers.",
        permission=P_CUSTOMERS,
        resolver=ResolverId.BREAKDOWN,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
        filters=_TIME + (FilterKey.NEW_OR_RETURNING, FilterKey.CATEGORY),
        kpis=("new_customers", "returning_customers", "repeat_purchase_rate", "aov"),
        charts=(
            _chart("revenue_split", "Revenue by customer type", "stacked-bar", "date",
                   ("new_revenue", "returning_revenue"), FormatId.MONEY),
            _chart("aov_split", "AOV by customer type", "bar", "group", ("aov",),
                   FormatId.MONEY, 1),
        ),
        # `agg_customer_daily` is the only rollup that splits revenue by
        # first-time vs repeat buyer. The two customer-count cards keep their
        # catalogue bindings on `agg_order_daily`, which stores the identical
        # definition, so this view reads two rollups and says so.
        params={
            "source": "agg_customer_daily",
            "dimension": "date",
            "metrics": {
                "new_revenue": {"add": ["revenue_new"]},
                "returning_revenue": {"add": ["revenue_returning"]},
            },
        },
        keywords=("repeat", "first order", "acquisition"),
    ),
    AnalyticsViewDefinition(
        number=10,
        name="Customer Lifetime Value",
        slug="customer-lifetime-value",
        summary="Realised revenue per customer over their life so far, by acquisition month.",
        permission=P_CUSTOMERS,
        resolver=ResolverId.BREAKDOWN,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
        filters=_TIME + (FilterKey.CUSTOMER_SEGMENT,),
        kpis=("ltv", "aov", "repeat_purchase_rate", "orders_count"),
        charts=(
            _chart("ltv_by_cohort", "LTV by acquisition month", "bar", "cohort", ("ltv",),
                   FormatId.MONEY),
        ),
        tables=(
            _table(
                "top_customers",
                "Highest lifetime value",
                (
                    _col("customer", "Customer"),
                    _col("first_order", "First order"),
                    _col("orders", "Orders", FormatId.INT, "right"),
                    _col("lifetime_revenue", "Lifetime revenue", FormatId.MONEY, "right"),
                ),
                sort="lifetime_revenue",
            ),
        ),
        export=True,
        # `ltv` itself is NOT bound by cohort: `cohort_size` is repeated on every
        # period row of a cohort, so summing it over a window multiplies the
        # denominator by the number of periods in range. What is bound is the
        # numerator side — revenue, orders and active customers per cohort —
        # each of which is additive across period rows.
        params={
            "source": "agg_customer_cohort_monthly",
            "dimension": "cohort",
            "metrics": {
                "revenue": {"add": ["revenue"]},
                "orders": {"add": ["orders"]},
                "active_customers": {"add": ["active_customers"]},
            },
        },
        keywords=("ltv", "clv", "lifetime"),
    ),
    AnalyticsViewDefinition(
        number=11,
        name="Customer Segmentation",
        slug="customer-segmentation",
        summary="Customers grouped by value, frequency and recency, with each group's "
                "contribution.",
        permission=P_CUSTOMERS,
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
        filters=_TIME + (FilterKey.CUSTOMER_SEGMENT, FilterKey.CATEGORY),
        kpis=("ltv", "aov", "orders_count", "repeat_purchase_rate"),
        charts=(
            _chart("segment_size", "Customers per segment", "hbar", "segment", ("customers",)),
            _chart("segment_revenue", "Revenue per segment", "hbar", "segment",
                   ("net_revenue",), FormatId.MONEY),
        ),
        tables=(
            _table(
                "segment_table",
                "Segments",
                (
                    _col("segment", "Segment"),
                    _col("customers", "Customers", FormatId.INT, "right"),
                    _col("revenue", "Revenue", FormatId.MONEY, "right"),
                    _col("aov", "AOV", FormatId.MONEY, "right"),
                ),
                sort="revenue",
            ),
        ),
        export=True,
        # `agg_customer_snapshot` holds one row per customer per day of LIFETIME
        # state, so this view is answered at the LATEST snapshot inside the
        # window and never by summing the window's days. The measure the chart
        # asks for — customers per segment — is a COUNT of that population, not
        # a sum of anything: `resolvers/levels.SNAPSHOT_BINDINGS["customers"]`
        # projects COUNT(DISTINCT customer_key). No `metrics` block, deliberately
        # — every column named there is summed by the generic resolvers, and on
        # this table that is wrong by the number of snapshot days in range.
        #
        # `net_revenue` per segment stays unbound: no rollup carries revenue at
        # customer-segment grain, and `gross_ltv` under that label would publish
        # lifetime value as a period figure. The chart is left empty and the
        # warning names it.
        params={
            "fn": "snapshot",
            "source": "agg_customer_snapshot",
            "dimension": "segment",
        },
        keywords=("segment", "vip", "group"),
    ),
    AnalyticsViewDefinition(
        number=12,
        name="Cohort and Retention",
        slug="cohort-and-retention",
        summary="Retention heatmap by acquisition month: how many customers come back in "
                "month 1, 2, 3.",
        permission=P_CUSTOMERS,
        resolver=ResolverId.COHORT,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
        filters=_RANGE + (FilterKey.CUSTOMER_SEGMENT,),
        kpis=("customer_retention_rate", "repeat_purchase_rate", "ltv"),
        charts=(
            _chart("retention_curve", "Retention by month since first order", "line", "month",
                   ("retention_pct",), FormatId.PCT),
        ),
        bespoke="cohort_heatmap",
        export=True,
        params={"source": "agg_customer_cohort_monthly"},
        keywords=("cohort", "retention", "heatmap"),
    ),
    AnalyticsViewDefinition(
        number=13,
        name="Customer Churn",
        slug="customer-churn",
        # LIVE with a stated definition: with no subscriptions there is no
        # cancellation event, so churn means "has not ordered within the window".
        summary="Lapsed customers — those past their expected repurchase window — and what "
                "they used to be worth.",
        permission=P_CUSTOMERS,
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
        filters=_TIME + (FilterKey.CUSTOMER_SEGMENT, FilterKey.CATEGORY),
        kpis=("customer_churn_rate", "customer_retention_rate", "repeat_purchase_rate", "ltv"),
        charts=(
            _chart("lapsed_trend", "Lapsed customers", "line", "date", ("lapsed_customers",)),
            _chart("lapse_by_segment", "Lapse rate by segment", "hbar", "segment",
                   ("lapse_rate",), FormatId.PCT),
        ),
        tables=(
            _table(
                "at_risk",
                "At risk of lapsing",
                (
                    _col("customer", "Customer"),
                    _col("last_order", "Last order"),
                    _col("days_since", "Days since", FormatId.DAYS, "right"),
                    _col("lifetime_revenue", "Lifetime revenue", FormatId.MONEY, "right"),
                ),
                sort="lifetime_revenue",
            ),
        ),
        # NOT exportable. `at_risk` is one row per customer; the only source
        # holding a customer-level position is `agg_customer_snapshot`, and that
        # is a LEVEL — projecting it across a window would list the same
        # customer once per day in range. The resolver that reads it correctly
        # (`snapshot`) can only return the population GROUPED by a dimension,
        # which is not this table. Rebinding this view would also replace its
        # trend charts with a breakdown, so the export is switched off instead.
        export=False,
        # Churn here is lapse, and the snapshot stores it: `is_active` is
        # "ordered within the activity window defined by the scoring job", which
        # is word for word the definition this view states above. Lapsed is
        # therefore the population MINUS the active part of it, at one instant —
        # a COUNT, which is why this could not be bound before
        # `AnalyticsRepository` grew count projections. Both charts are drawn:
        # `lapsed_trend` as a level series (each bucket's latest day, gaps left
        # empty) and `lapse_by_segment` as a partition of the population at the
        # pinned instant.
        #
        # The four KPI cards stay null and name what they need: churn and
        # retention are set operations over two windows of customer history, and
        # `at_risk` is one row per customer, which no groupable dimension
        # produces. See `export=False` above for the same reason stated for the
        # export path.
        params={
            "fn": "snapshot",
            "source": "agg_customer_snapshot",
            "dimension": "segment",
        },
        keywords=("churn", "lapsed", "win-back"),
    ),
    AnalyticsViewDefinition(
        number=52,
        name="Loyalty and Rewards",
        slug="loyalty-and-rewards",
        # Reworded from "referral activity and its order value": the completion
        # is counted here, the money is not. `referrals.completed_order_id` makes
        # a referral-attributed revenue figure joinable in principle, but it is
        # order-level money under the revenue bridge's definitions, and a second
        # revenue number computed in the loyalty rollup is how two screens end up
        # disagreeing under one word.
        summary="Points issued, redeemed, expired and outstanding, plus completed "
                "referrals.",
        permission=P_CUSTOMERS,
        resolver=ResolverId.TIMESERIES,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
        filters=_TIME + (FilterKey.CUSTOMER_SEGMENT,),
        kpis=("returning_customers", "orders_count", "aov", "repeat_purchase_rate"),
        charts=(
            _chart("points_flow", "Points issued vs redeemed", "bar", "date",
                   ("points_issued", "points_redeemed", "points_expired")),
            # Was "Orders from referrals" bound to `orders_count`. That series
            # lives in `agg_order_daily` and counts EVERY order, so on this view
            # it drew the whole store's order line under a referral heading —
            # and `orders_count` cannot be rebound, because a catalogue metric
            # must mean one thing everywhere. What the data actually supports is
            # the completion event: `referrals.completed_at` is stamped when a
            # referred friend's first order is paid, which is one order, and the
            # rollup counts it. The title says completions because that is what
            # the number is.
            _chart("referral_completions", "Completed referrals", "line", "date",
                   ("referral_completions",), FormatId.INT, 1),
        ),
        tables=(
            _table(
                "loyalty_summary",
                "Loyalty by month",
                (
                    _col("period", "Month"),
                    _col("issued", "Issued", FormatId.INT, "right"),
                    _col("redeemed", "Redeemed", FormatId.INT, "right"),
                    _col("outstanding", "Outstanding", FormatId.INT, "right"),
                ),
                sort="period",
            ),
        ),
        # NOT exportable, and the TableSpec above is still declared but still
        # unfilled — for a narrower reason than before. `issued` and `redeemed`
        # are now stored (`points_earned`, `points_redeemed`) and the timeseries
        # resolver already draws them at whatever granularity is asked for,
        # month included. `outstanding` is the blocker: `points_outstanding_close`
        # is a LEVEL, so a month's value is its LAST bucket, and every resolver
        # that emits a table SUMs — thirty daily balances added together would
        # report thirty times the liability under a column labelled
        # "Outstanding". A table with two right columns and one silently wrong
        # one is worse than no table, so this one stays empty until a
        # level-aware table resolver exists. The spec is kept because the intent
        # is part of the frontend contract (see tests/test_analytics_export.py).
        export=False,
        # LIVE and now actually reading something. `points_issued`/`points_redeemed`
        # /`points_expired` are display keys, not catalogue KPIs — kpis.py has no
        # loyalty entry and inventing one here would put a metric on screen that
        # nothing else in the catalogue can define — so they are bound directly
        # to their stored columns. All three are FLOWS and are non-negative
        # halves of the signed ledger `delta`, which is why the chart can add
        # them across a bucket at all.
        #
        # Deliberately NOT bound, and each for its own reason:
        #   * `points_outstanding_close` — a LEVEL. See the export note above.
        #   * `distinct_customers` — a DISTINCT count. Summing a week
        #     double-counts anyone who transacted twice and taking the latest day
        #     is just as wrong; it has to be recomputed from the ledger.
        #   * anything per redemption TIER — `points_transactions` and `coupons`
        #     carry no `redemption_tier_id`, so the only trace of which tier was
        #     traded is a free-text description. Parsing it would re-partition
        #     history on a rename while looking like a measurement.
        params={
            "source": "agg_loyalty_daily",
            "metrics": {
                "points_issued": "points_earned",
                "points_redeemed": "points_redeemed",
                "points_expired": "points_expired",
                "referral_completions": "referral_completions",
            },
        },
        keywords=("points", "rewards", "referral"),
    ),
    AnalyticsViewDefinition(
        number=53,
        name="Subscription Commerce",
        slug="subscription-commerce",
        summary="Active subscriptions, MRR, renewal and cancellation behaviour.",
        permission=P_CUSTOMERS,
        resolver=ResolverId.TIMESERIES,
        freshness=Freshness.DAILY,
        state=ViewState.FEATURE_REQUIRED,
        requires=(Capability.SUBSCRIPTIONS,),
        filters=_RANGE,
        limitation="The store sells one-off orders only — there is no subscription, plan or "
                   "renewal model, so there is nothing to measure.",
        keywords=("subscription", "mrr", "renewal"),
    ),
    AnalyticsViewDefinition(
        number=59,
        name="RFM Customer Analysis",
        slug="rfm-customer-analysis",
        summary="Recency, frequency and monetary scores placing every customer in a 5x5 grid.",
        permission=P_CUSTOMERS,
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
        filters=_RANGE + (FilterKey.CUSTOMER_SEGMENT,),
        kpis=("rfm_score", "ltv", "repeat_purchase_rate", "aov"),
        charts=(
            _chart("rfm_revenue", "Revenue by RFM segment", "hbar", "segment",
                   ("net_revenue",), FormatId.MONEY),
        ),
        tables=(
            _table(
                "rfm_table",
                "Customers by RFM cell",
                (
                    _col("customer", "Customer"),
                    _col("recency_days", "Recency", FormatId.DAYS, "right"),
                    _col("frequency", "Frequency", FormatId.INT, "right"),
                    _col("monetary", "Monetary", FormatId.MONEY, "right"),
                    _col("cell", "Cell"),
                ),
                sort="monetary",
            ),
        ),
        bespoke="rfm_matrix",
        export=True,
        # Read at the latest snapshot in the window, like every other view on
        # this table, so `aov` and the provenance are real and the gaps are
        # named rather than generic. What this view still cannot DISPLAY is
        # stated plainly: `rfm_revenue` asks for revenue at customer-segment
        # grain, which no rollup stores (`gross_ltv` is lifetime value, a
        # different figure under the same word), and `rfm_table` is one row per
        # customer, which no groupable dimension produces — the population count
        # per segment that COUNT projections unlocked is displayed by view 11,
        # which declares a slot for it.
        params={
            "fn": "snapshot",
            "source": "agg_customer_snapshot",
            "dimension": "segment",
        },
        keywords=("rfm", "recency", "frequency", "monetary"),
    ),
)


# --------------------------------------------------------------------------
# 5. Marketing & Attribution
# --------------------------------------------------------------------------
# The store records orders, not sessions. Nothing in this module can be computed
# from internal tables alone, so every view here is honestly gated rather than
# shown with fabricated channel splits.

_MARKETING_VIEWS: tuple[AnalyticsViewDefinition, ...] = (
    AnalyticsViewDefinition(
        number=19,
        name="Marketing Channel Performance",
        slug="marketing-channel-performance",
        summary="Sessions, orders and revenue by channel and source/medium.",
        permission=P_MARKETING,
        resolver=ResolverId.BREAKDOWN,
        freshness=Freshness.DAILY,
        state=ViewState.INTEGRATION_REQUIRED,
        sources=(DataSource.INTERNAL_DB, DataSource.GA4),
        requires=(Capability.GA4_DATA_API,),
        filters=_RANGE + (FilterKey.SOURCE, FilterKey.MEDIUM, FilterKey.CHANNEL),
        limitation="The store does not persist session-level traffic sources; channel "
                   "attribution needs the GA4 Data API to be connected.",
        keywords=("channel", "source", "medium", "utm"),
    ),
    AnalyticsViewDefinition(
        number=20,
        name="Campaign Performance",
        slug="campaign-performance",
        summary="Spend, clicks, orders and revenue per campaign and ad set.",
        permission=P_MARKETING,
        resolver=ResolverId.TABLE,
        freshness=Freshness.DAILY,
        state=ViewState.INTEGRATION_REQUIRED,
        sources=(DataSource.INTERNAL_DB, DataSource.GA4, DataSource.ADS),
        requires=(Capability.GA4_DATA_API, Capability.AD_PLATFORM),
        filters=_RANGE + (FilterKey.CAMPAIGN, FilterKey.SOURCE),
        limitation="Campaign results need GA4 for click-through data and an ad platform for "
                   "spend; neither is connected.",
        keywords=("campaign", "ads", "adset"),
    ),
    AnalyticsViewDefinition(
        number=21,
        name="ROAS and Marketing Profitability",
        slug="roas-and-marketing-profitability",
        summary="Blended return on recorded marketing spend (MER), with the spend "
                "split by channel.",
        permission=P_MARKETING,
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.DAILY,
        # PARTIAL, unlocked by manually entered spend (`analytics_marketing_spend`)
        # — but ONLY for what spend alone can honestly answer: blended ROAS/MER
        # (internal net_revenue over total recorded spend) and the spend split
        # by channel. The resolver downgrades to the gated shape at runtime
        # whenever the window holds zero spend rows. Per-channel ROAS is NOT
        # computable — revenue cannot be attributed per channel without a
        # session->order key (GA4 attribution, click ids), which does not exist
        # — so the CHANNEL/CAMPAIGN filters were deliberately REMOVED: a channel
        # filter on a blended ratio is a fabricated per-channel ROAS by another
        # name. Views 19 and 20 stay INTEGRATION_REQUIRED for the same reason.
        state=ViewState.PARTIAL,
        sources=(DataSource.INTERNAL_DB,),
        requires=(Capability.AD_PLATFORM,),
        filters=_TIME,
        kpis=("blended_roas", "total_spend", "net_revenue"),
        charts=(
            _chart("roas_trend", "Blended ROAS (MER)", "line", "date",
                   ("blended_roas",), FormatId.RATIO),
            _chart("spend_by_channel", "Spend by channel", "hbar", "channel",
                   ("spend",), FormatId.MONEY),
        ),
        tables=(
            # Spend ONLY. No revenue and no ROAS column may ever be added here
            # without per-channel attribution actually existing — a per-channel
            # ratio filled from blended revenue would be fabrication, and
            # tests/test_analytics_roas.py pins the column set.
            _table(
                "channel_spend",
                "Recorded spend by channel",
                (
                    _col("channel", "Channel"),
                    _col("spend", "Spend", FormatId.MONEY, "right"),
                    _col("spend_share_pct", "Share of spend", FormatId.PCT, "right"),
                    _col("quality", "Quality"),
                ),
                sort="spend",
            ),
        ),
        params={"fn": "roas_blended"},
        limitation="Spend is manually entered in the admin, so ROAS here is blended "
                   "(MER): total store revenue over total recorded spend. Revenue "
                   "cannot be attributed to a channel without ad-platform "
                   "attribution, so per-channel ROAS is not shown — only "
                   "per-channel spend.",
        keywords=("roas", "mer", "spend", "profit", "blended"),
    ),
    AnalyticsViewDefinition(
        number=22,
        name="SEO Performance",
        slug="seo-performance",
        summary="Impressions, clicks, position and landing pages from organic search.",
        permission=P_MARKETING,
        resolver=ResolverId.TABLE,
        freshness=Freshness.DAILY,
        state=ViewState.INTEGRATION_REQUIRED,
        sources=(DataSource.GOOGLE_SEARCH_CONSOLE,),
        requires=(Capability.SEARCH_CONSOLE,),
        filters=_RANGE,
        limitation="Query, impression and position data only exists in Google Search Console, "
                   "which is not connected.",
        keywords=("seo", "organic", "search console", "keywords"),
    ),
    AnalyticsViewDefinition(
        number=23,
        name="Social Media Commerce",
        slug="social-media-commerce",
        summary="Traffic and orders originating from social shops, posts and links.",
        permission=P_MARKETING,
        resolver=ResolverId.BREAKDOWN,
        freshness=Freshness.DAILY,
        state=ViewState.INTEGRATION_REQUIRED,
        sources=(DataSource.INTERNAL_DB, DataSource.GA4),
        requires=(Capability.SOCIAL_COMMERCE_API, Capability.GA4_DATA_API),
        filters=_RANGE + (FilterKey.SOURCE,),
        limitation="Post-level and social-shop data comes from the social commerce APIs, and "
                   "the referring traffic from GA4; none of them is connected.",
        keywords=("instagram", "facebook", "social", "shop"),
    ),
    AnalyticsViewDefinition(
        number=24,
        name="Email and SMS Marketing",
        slug="email-and-sms-marketing",
        summary="Sends, deliveries, opens, clicks and attributed orders per campaign.",
        permission=P_MARKETING,
        resolver=ResolverId.TABLE,
        freshness=Freshness.DAILY,
        state=ViewState.INTEGRATION_REQUIRED,
        requires=(Capability.EMAIL_SMS_PLATFORM,),
        filters=_RANGE + (FilterKey.CAMPAIGN,),
        limitation="Transactional mail is sent by the app, but campaign sends, opens and "
                   "clicks live in an email/SMS platform that is not connected.",
        keywords=("email", "sms", "newsletter", "broadcast"),
    ),
    AnalyticsViewDefinition(
        number=26,
        name="Affiliate and Influencer",
        slug="affiliate-and-influencer",
        summary="Orders, revenue and commission per affiliate or influencer partner.",
        permission=P_MARKETING,
        resolver=ResolverId.TABLE,
        freshness=Freshness.DAILY,
        state=ViewState.INTEGRATION_REQUIRED,
        requires=(Capability.AFFILIATE_PLATFORM,),
        filters=_RANGE + (FilterKey.SOURCE,),
        limitation="Partner links and commissions live in an affiliate platform that is not "
                   "connected; the internal referrals table is customer-to-customer only.",
        keywords=("affiliate", "influencer", "commission", "partner"),
    ),
    AnalyticsViewDefinition(
        number=65,
        name="Customer Journey and Attribution",
        slug="customer-journey-and-attribution",
        summary="The touchpoint path to purchase, with first- and last-click attribution.",
        permission=P_MARKETING,
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.DAILY,
        state=ViewState.INTEGRATION_REQUIRED,
        sources=(DataSource.INTERNAL_DB, DataSource.GA4),
        requires=(Capability.GA4_DATA_API,),
        filters=_RANGE + (FilterKey.CHANNEL, FilterKey.DEVICE),
        bespoke="journey_paths",
        limitation="Cross-session paths need GA4; internally only cart and order events exist, "
                   "so a journey could not start earlier than add-to-cart.",
        keywords=("journey", "attribution", "path", "touchpoint"),
    ),
)


# --------------------------------------------------------------------------
# 6. Website & Conversion
# --------------------------------------------------------------------------

_WEBSITE_VIEWS: tuple[AnalyticsViewDefinition, ...] = (
    AnalyticsViewDefinition(
        number=14,
        name="Conversion Funnel",
        slug="conversion-funnel",
        # PARTIAL by design: cart_events give us add-to-cart onwards. Sessions
        # and product views are not recorded server-side, so the top of the
        # funnel is missing and the conversion base is carts, not visits.
        summary="Drop-off from add-to-cart through checkout to a paid order.",
        permission=P_WEBSITE,
        resolver=ResolverId.FUNNEL,
        freshness=Freshness.DAILY,
        state=ViewState.PARTIAL,
        sources=(DataSource.INTERNAL_DB,),
        requires=(Capability.GA4_DATA_API,),
        filters=_TIME + (FilterKey.DEVICE, FilterKey.CATEGORY),
        kpis=("conversion_rate", "add_to_cart_rate", "cart_abandonment_rate",
              "checkout_abandonment_rate"),
        charts=(
            _chart("funnel_steps", "Funnel steps", "bar", "step", ("users",)),
            _chart("step_conversion", "Step-to-step conversion", "line", "step",
                   ("conversion_rate",), FormatId.PCT),
        ),
        bespoke="conversion_funnel",
        # The funnel resolver reads `agg_funnel_daily` and computes every step
        # rate from two of its own additive counters; naming the source here
        # keeps the registry entry self-describing. `conversion_rate` is NOT
        # bound: its denominator is GA4 sessions, and the internal
        # `distinct_sessions` is not additive across days.
        params={"source": "agg_funnel_daily"},
        limitation="The funnel starts at add-to-cart because sessions and product views are "
                   "not recorded internally; the session-level steps need GA4.",
        keywords=("funnel", "drop-off", "conversion"),
    ),
    AnalyticsViewDefinition(
        number=15,
        name="Cart Abandonment",
        slug="cart-abandonment",
        summary="Carts created versus carts ordered, with the value and contents left behind.",
        permission=P_WEBSITE,
        resolver=ResolverId.TIMESERIES,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
        filters=_TIME + (FilterKey.DEVICE, FilterKey.CATEGORY),
        kpis=("cart_abandonment_rate", "add_to_cart_rate", "aov", "conversion_rate"),
        charts=(
            _chart("abandonment_trend", "Abandonment rate", "line", "date",
                   ("cart_abandonment_rate",), FormatId.PCT),
            _chart("abandoned_value", "Value left in carts", "area", "date",
                   ("abandoned_value",), FormatId.MONEY),
        ),
        tables=(
            _table(
                "abandoned_products",
                "Most abandoned products",
                (
                    _col("product", "Product"),
                    _col("added", "Added to cart", FormatId.INT, "right"),
                    _col("ordered", "Ordered", FormatId.INT, "right"),
                    _col("abandon_rate", "Abandoned", FormatId.PCT, "right"),
                ),
                sort="added",
            ),
        ),
        # NOT exportable. `abandoned_products` needs cart adds PER PRODUCT.
        # `agg_funnel_daily` counts `items_added` store-wide with no product
        # dimension, and `agg_product_daily` has no cart column at all — the
        # cart is a Redis hash with no history. Grouping the funnel rollup by
        # product is not a binding that exists to be wired; the data is not kept.
        export=False,
        # The same two ratios `resolvers.special.FUNNEL_BINDINGS` uses, so the
        # funnel view and this one cannot disagree. `abandoned_value` has no
        # stored column — the cart is a Redis hash with no history — and stays
        # unbound, so that chart draws nothing rather than a zero line.
        params={
            "source": "agg_funnel_daily",
            "metrics": {
                "cart_abandonment_rate": {
                    "add": ["checkouts_started"],
                    "over": ["items_added"],
                    "scale": 100,
                    "complement": True,
                },
                "add_to_cart_rate": {
                    "add": ["items_added"],
                    "over": ["product_views"],
                    "scale": 100,
                },
            },
        },
        keywords=("abandoned", "cart", "recovery"),
    ),
    AnalyticsViewDefinition(
        number=16,
        name="Checkout Performance",
        slug="checkout-performance",
        summary="How many started checkout, how many paid, and where the rest stopped.",
        permission=P_WEBSITE,
        resolver=ResolverId.FUNNEL,
        freshness=Freshness.HOURLY,
        state=ViewState.LIVE,
        filters=_TIME + (FilterKey.DEVICE, FilterKey.PAYMENT_METHOD),
        kpis=("checkout_abandonment_rate", "payment_success_rate", "conversion_rate",
              "orders_count"),
        charts=(
            _chart("checkout_steps", "Checkout step completion", "bar", "step", ("users",)),
            _chart("checkout_trend", "Completion rate", "line", "date",
                   ("checkout_completion_pct",), FormatId.PCT),
        ),
        params={"source": "agg_funnel_daily"},
        keywords=("checkout", "payment step", "address"),
    ),
    AnalyticsViewDefinition(
        number=17,
        name="Website Traffic",
        slug="website-traffic",
        summary="Sessions, users, pageviews and engagement over time.",
        permission=P_WEBSITE,
        resolver=ResolverId.TIMESERIES,
        freshness=Freshness.DAILY,
        state=ViewState.INTEGRATION_REQUIRED,
        sources=(DataSource.GA4,),
        requires=(Capability.GA4_DATA_API, Capability.GA4_MEASUREMENT),
        filters=_RANGE + (FilterKey.DEVICE, FilterKey.COUNTRY),
        limitation="Sessions and pageviews are not recorded server-side; this view needs GA4 "
                   "measurement plus the GA4 Data API.",
        keywords=("traffic", "sessions", "visitors", "pageviews"),
    ),
    AnalyticsViewDefinition(
        number=18,
        name="Landing Page Performance",
        slug="landing-page-performance",
        summary="Entry pages ranked by sessions, bounce and assisted revenue.",
        permission=P_WEBSITE,
        resolver=ResolverId.TABLE,
        freshness=Freshness.DAILY,
        state=ViewState.INTEGRATION_REQUIRED,
        sources=(DataSource.GA4,),
        requires=(Capability.GA4_DATA_API,),
        filters=_RANGE + (FilterKey.DEVICE, FilterKey.SOURCE),
        limitation="Landing pages are identified from session entry data, which only GA4 has; "
                   "the store logs API requests, not page entries.",
        keywords=("landing", "entry page", "bounce"),
    ),
    AnalyticsViewDefinition(
        number=43,
        name="Geographic Sales",
        slug="geographic-sales",
        summary="Orders, revenue and RTO by state and city, taken from delivery addresses.",
        permission=P_WEBSITE,
        resolver=ResolverId.GEO,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
        filters=_TIME + (FilterKey.COUNTRY, FilterKey.STATE, FilterKey.CITY,
                         FilterKey.CATEGORY),
        kpis=("net_revenue", "orders_count", "aov", "rto_rate"),
        charts=(
            _chart("geo_revenue", "Revenue by state", "hbar", "state", ("net_revenue",),
                   FormatId.MONEY),
        ),
        tables=(
            _table(
                "geo_table",
                "States and cities",
                (
                    _col("state", "State"),
                    _col("city", "City"),
                    _col("orders", "Orders", FormatId.INT, "right"),
                    _col("revenue", "Revenue", FormatId.MONEY, "right"),
                    _col("rto_rate", "RTO", FormatId.PCT, "right"),
                ),
                sort="revenue",
            ),
        ),
        bespoke="geo_map",
        export=True,
        # `city` is not a dimension of `agg_geo_daily` — the rollup is state x
        # pincode — so that column stays empty rather than being filled from the
        # nearest-looking field.
        params={"source": "agg_geo_daily", "dimension": "state"},
        keywords=("geo", "map", "state", "city", "pincode"),
    ),
    AnalyticsViewDefinition(
        number=44,
        name="Device and Browser Analytics",
        slug="device-and-browser-analytics",
        summary="Sessions, conversion and revenue split by device, browser and OS.",
        permission=P_WEBSITE,
        resolver=ResolverId.BREAKDOWN,
        freshness=Freshness.DAILY,
        state=ViewState.INTEGRATION_REQUIRED,
        sources=(DataSource.GA4,),
        requires=(Capability.GA4_DATA_API,),
        filters=_RANGE + (FilterKey.DEVICE,),
        limitation="Client device, browser and OS are not stored with orders; these splits "
                   "require GA4.",
        keywords=("device", "mobile", "browser", "os"),
    ),
    AnalyticsViewDefinition(
        number=45,
        name="Search Analytics",
        slug="search-analytics",
        summary="What visitors search for on the site, and which searches end in no result "
                "or no order.",
        permission=P_WEBSITE,
        resolver=ResolverId.TABLE,
        freshness=Freshness.DAILY,
        state=ViewState.INTEGRATION_REQUIRED,
        sources=(DataSource.GA4,),
        requires=(Capability.GA4_DATA_API,),
        filters=_RANGE,
        limitation="On-site search terms are not persisted by the storefront; reporting them "
                   "needs GA4 with site search configured.",
        keywords=("site search", "query", "no results"),
    ),
)


# --------------------------------------------------------------------------
# 7. Inventory & Supply Chain
# --------------------------------------------------------------------------
# Current stock is exact (it is a column on the product). Anything that needs a
# stock position *in the past* is PARTIAL: the ledger starts the day it ships and
# there is no historical movement data to backfill from.

_INVENTORY_VIEWS: tuple[AnalyticsViewDefinition, ...] = (
    AnalyticsViewDefinition(
        number=27,
        name="Inventory Analytics",
        slug="inventory-analytics",
        summary="Stock on hand, movement in and out, and stock value by category.",
        permission=P_INVENTORY,
        resolver=ResolverId.TIMESERIES,
        freshness=Freshness.DAILY,
        state=ViewState.PARTIAL,
        requires=(Capability.INVENTORY_LEDGER,),
        filters=_TIME + (FilterKey.CATEGORY, FilterKey.PRODUCT),
        kpis=("stockout_rate", "units_sold", "stock_value"),
        charts=(
            _chart("stock_on_hand", "Units on hand", "area", "date", ("units_on_hand",)),
            _chart("movements", "Stock in vs out", "bar", "date", ("stock_in", "stock_out")),
        ),
        # Only the FLOWS are bound. `units_on_hand` (`stock_close`) and
        # `stock_value` (`stock_value_close`) are LEVELS, and both the KPI path
        # (one SUM over the whole window) and the series path (one SUM per
        # bucket) would add closing balances together — thirty days of stock
        # reported as thirty times the stock. `analytics_rollups` states the
        # rule outright: "never sum this across days". Drawing the on-hand line
        # correctly needs a level-aware resolver, not a different binding.
        params={
            "source": "agg_inventory_daily",
            "metrics": {
                "stock_in": {"add": ["units_restocked"]},
                "stock_out": {"add": ["units_sold"]},
            },
        },
        limitation="Stock movement history begins when the inventory ledger was switched on; "
                   "earlier periods cannot be reconstructed and are shown as no data.",
        keywords=("stock", "inventory", "movement"),
    ),
    AnalyticsViewDefinition(
        number=28,
        name="Stock Availability",
        slug="stock-availability",
        summary="Share of the catalogue that is currently buyable, by category.",
        permission=P_INVENTORY,
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
        filters=(FilterKey.CATEGORY, FilterKey.PRODUCT),
        kpis=("stockout_rate", "units_sold"),
        charts=(
            _chart("availability_by_category", "In-stock share by category", "hbar",
                   "category", ("in_stock_pct",), FormatId.PCT),
        ),
        tables=(
            _table(
                "availability_table",
                "Availability",
                (
                    _col("category", "Category"),
                    _col("products", "Products", FormatId.INT, "right"),
                    _col("in_stock", "In stock", FormatId.INT, "right"),
                    _col("in_stock_pct", "In-stock share", FormatId.PCT, "right"),
                ),
                sort="in_stock_pct",
            ),
        ),
        export=True,
        # `stockout_rate` is exactly what `AggInventoryDaily` says an OOS share
        # is — SUM(is_oos) / COUNT(*) — read at the LATEST ledger day inside the
        # window, which is what the KPI catalogue defines it as ("POINT IN TIME,
        # not a period rate"). Over the window instead, the same expression
        # returns the time-weighted share of product-days out of stock: a
        # different figure, entirely plausible, and one the catalogue says
        # outright does not exist yet. The binding lives in
        # `resolvers/levels.INVENTORY_BINDINGS` rather than here because a
        # `metrics` block is summed across the window by the generic resolvers.
        #
        # No `dimension`: the by-category chart and table CANNOT be filled. The
        # ledger is keyed by product and carries no category column, and no
        # rollup joins stock to a category — so the split is reported as
        # unavailable (DIMENSION_NOT_STORED) instead of being regrouped by SKU
        # under a heading that says category.
        params={"fn": "snapshot", "source": "agg_inventory_daily"},
        keywords=("availability", "in stock", "buyable"),
    ),
    AnalyticsViewDefinition(
        number=29,
        name="Low-Stock and Out-of-Stock",
        slug="low-stock-and-out-of-stock",
        summary="Products at or below their threshold, ranked by the sales they put at risk.",
        permission=P_INVENTORY,
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
        filters=(FilterKey.CATEGORY, FilterKey.PRODUCT),
        kpis=("stockout_rate", "units_sold", "net_revenue"),
        charts=(
            _chart("stockouts_trend", "Products out of stock", "line", "date",
                   ("out_of_stock",)),
        ),
        tables=(
            _table(
                "low_stock_table",
                "Needs attention",
                (
                    _col("product", "Product"),
                    _col("stock", "Stock", FormatId.INT, "right"),
                    _col("units_30d", "Units (30d)", FormatId.INT, "right"),
                    _col("days_cover", "Days cover", FormatId.DAYS, "right"),
                ),
                sort="days_cover",
                hint="Nothing is below its low-stock threshold.",
            ),
        ),
        export=True,
        # Latest-per-product, not one row per product per day. `TableResolver`
        # would project the ledger straight through and list every product once
        # for every day in the window, each at that day's stock — a table whose
        # length is the window length and whose top row is the oldest reading.
        # The `snapshot` resolver pins the newest ledger day inside the window
        # and groups there, so the table is one row per product at a stated
        # `as_at`, and `stockouts_trend` is a level series (each bucket's last
        # day) rather than a sum of daily out-of-stock counts.
        #
        # `breakdown_sort` puts the emptiest shelf first: the default top-N is
        # by the primary measure DESCENDING, which under a heading that says
        # "Needs attention" would show the fullest shelves.
        #
        # `units_30d` and `days_cover` stay unbound — both are flows (or a flow
        # divided by a level) and cannot be read at an instant; the threshold
        # itself is not expressible either, since the repository filters on
        # equality and `reorder_gap` needs an inequality. So this lists every
        # product, lowest stock first, rather than only those below their
        # reorder point.
        params={
            "fn": "snapshot",
            "source": "agg_inventory_daily",
            "dimension": "product",
            "breakdown_sort": "stock",
        },
        keywords=("low stock", "oos", "reorder"),
    ),
    AnalyticsViewDefinition(
        number=30,
        name="Inventory Turnover",
        slug="inventory-turnover",
        summary="How many times stock turns over in a period, and days of cover per "
                "product with the slow movers first.",
        permission=P_INVENTORY,
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.DAILY,
        state=ViewState.PARTIAL,
        requires=(Capability.INVENTORY_LEDGER, Capability.COST_RULES),
        filters=_RANGE + (FilterKey.CATEGORY,),
        kpis=("inventory_turnover", "days_of_inventory", "stock_value", "units_sold"),
        charts=(
            _chart("turnover_by_category", "Turns by category", "hbar", "category",
                   ("inventory_turnover",), FormatId.RATIO),
        ),
        tables=(
            _table(
                "turnover_by_product",
                "Turnover by product",
                (
                    _col("product", "Product"),
                    _col("sku", "SKU"),
                    _col("units_sold", "Units sold", FormatId.INT, "right"),
                    _col("cogs", "COGS", FormatId.MONEY, "right"),
                    _col("avg_stock_value", "Avg stock value", FormatId.MONEY, "right"),
                    _col("inventory_turnover", "Turns", FormatId.RATIO, "right"),
                    _col("days_of_inventory", "Days of cover", FormatId.DAYS, "right"),
                    _col("cost_coverage_pct", "Cost coverage", FormatId.PCT, "right"),
                    _col("days_observed", "Ledger days", FormatId.INT, "right"),
                ),
                sort="inventory_turnover",
                hint="No inventory ledger days inside this window yet.",
            ),
        ),
        export=True,
        # Turnover = COGS in window / AVERAGE inventory value over the window —
        # a two-rollup ratio whose denominator is the mean of a LEVEL, which no
        # `metrics` binding can express (a MetricBinding names one source, and a
        # SUM of `stock_value_close` across days is the non-additive operation
        # `metric_kind` refuses). `resolvers/turnover.py` computes it through
        # the repository's `avg:stock_value_close` projection (LEVEL-only by
        # construction), never as units-sold over latest-stock: a level pinned
        # at the window end understates turnover after a restock and overstates
        # it after a sellout.
        #
        # The by-category chart and the category filter CANNOT be honoured: the
        # ledger is keyed by product and carries no category column, and no
        # rollup joins a stock level to a category — both are reported as
        # DIMENSION_NOT_STORED (the same refusal views 28/29 make on this
        # table) rather than regrouped by something adjacent. The per-product
        # table, slow movers first, is the split the ledger can answer.
        params={"fn": "inventory_turnover"},
        limitation="The inventory ledger is forward-only (history begins when it was "
                   "switched on), so the average stock in older windows cannot be "
                   "reconstructed, and stock is valued at CURRENT cost — turnover is an "
                   "estimate at best, incomplete where unit_cost coverage is below 100%. "
                   "The ledger stores no category, so turns cannot be split by category.",
        keywords=("turnover", "turns", "days cover"),
    ),
    AnalyticsViewDefinition(
        number=31,
        name="Demand Forecasting",
        slug="demand-forecasting",
        summary="Expected demand per product for the next period, with suggested reorder "
                "quantities.",
        permission=P_INVENTORY,
        resolver=ResolverId.CUSTOM,
        params={"fn": "demand_forecast", "inventory_source": "agg_inventory_daily", "min_history_days": 28},
        freshness=Freshness.DAILY,
        state=ViewState.PARTIAL,
        requires=(Capability.INVENTORY_LEDGER, Capability.ORDER_LINE_FACT),
        filters=_RANGE + (FilterKey.CATEGORY, FilterKey.PRODUCT),
        kpis=("units_sold", "stockout_rate"),
        charts=(
            _chart("demand_forecast", "Units sold vs forecast", "line", "date",
                   ("units_sold", "forecast_units")),
        ),
        limitation="Forecasts need several months of demand and stock-out history; with the "
                   "history available now the suggestions are indicative only.",
        keywords=("forecast", "demand", "reorder"),
    ),
    AnalyticsViewDefinition(
        number=32,
        name="Supplier Performance",
        slug="supplier-performance",
        summary="Lead time, fill rate and price movement per supplier.",
        permission=P_INVENTORY,
        resolver=ResolverId.TABLE,
        freshness=Freshness.DAILY,
        state=ViewState.FEATURE_REQUIRED,
        requires=(Capability.SUPPLIERS,),
        filters=_RANGE,
        limitation="There is no supplier or purchase-order model in the system, so lead times "
                   "and fill rates do not exist to be measured.",
        keywords=("supplier", "vendor", "purchase order", "lead time"),
    ),
    AnalyticsViewDefinition(
        number=33,
        name="Warehouse Performance",
        slug="warehouse-performance",
        summary="Pick, pack and dispatch throughput and accuracy per warehouse.",
        permission=P_INVENTORY,
        resolver=ResolverId.BREAKDOWN,
        freshness=Freshness.DAILY,
        state=ViewState.FEATURE_REQUIRED,
        requires=(Capability.WAREHOUSES,),
        filters=_RANGE + (FilterKey.WAREHOUSE,),
        limitation="Stock is not tracked per warehouse, bin or location, so pick and pack "
                   "throughput cannot be attributed anywhere.",
        keywords=("warehouse", "pick", "pack", "bin"),
    ),
)


# --------------------------------------------------------------------------
# 8. Orders & Logistics
# --------------------------------------------------------------------------

_ORDER_VIEWS: tuple[AnalyticsViewDefinition, ...] = (
    AnalyticsViewDefinition(
        number=34,
        name="Shipping and Delivery",
        slug="shipping-and-delivery",
        # PARTIAL: our shipment rows record the status changes we are told about.
        # Transit detail between them needs the courier's scan feed.
        summary="Dispatch to delivery times, in-transit ageing and delivery outcomes.",
        permission=P_ORDERS,
        resolver=ResolverId.TIMESERIES,
        freshness=Freshness.HOURLY,
        state=ViewState.PARTIAL,
        sources=(DataSource.INTERNAL_DB,),
        requires=(Capability.COURIER_SCAN_API,),
        filters=_TIME + (FilterKey.COURIER, FilterKey.STATE, FilterKey.FULFILMENT_STATUS),
        kpis=("avg_delivery_days", "on_time_delivery_rate", "rto_rate"),
        charts=(
            _chart("delivery_days", "Dispatch to delivery (days)", "line", "date",
                   ("avg_delivery_days",), FormatId.DAYS),
            _chart("in_transit_ageing", "Shipments in transit by age", "hbar", "age_band",
                   ("shipments",)),
        ),
        # `avg_delivery_days` is a sum-of-seconds over a count and cannot be
        # expressed as a sum of columns without a seconds-to-days conversion the
        # binding language does not carry; `on_time_delivery_rate` has no
        # promise date anywhere in the schema. Both stay unbound.
        params={"source": "agg_shipment_daily"},
        limitation="Only the status changes recorded on our own shipments are available; "
                   "leg-by-leg transit timings need the courier scan API.",
        keywords=("shipping", "delivery", "transit", "sla"),
    ),
    AnalyticsViewDefinition(
        number=35,
        name="Courier Performance",
        slug="courier-performance",
        summary="Couriers compared on delivery time, on-time share and RTO.",
        permission=P_ORDERS,
        resolver=ResolverId.BREAKDOWN,
        freshness=Freshness.HOURLY,
        state=ViewState.PARTIAL,
        sources=(DataSource.INTERNAL_DB,),
        requires=(Capability.COURIER_SCAN_API,),
        filters=_TIME + (FilterKey.COURIER, FilterKey.STATE),
        kpis=("on_time_delivery_rate", "rto_rate", "orders_count"),
        charts=(
            _chart("courier_ontime", "On-time delivery by courier", "hbar", "courier",
                   ("on_time_delivery_rate",), FormatId.PCT),
        ),
        tables=(
            _table(
                "courier_table",
                "Couriers",
                (
                    _col("courier", "Courier"),
                    _col("shipments", "Shipments", FormatId.INT, "right"),
                    _col("avg_days", "Avg days", FormatId.DAYS, "right"),
                    _col("on_time_pct", "On time", FormatId.PCT, "right"),
                    _col("rto_pct", "RTO", FormatId.PCT, "right"),
                ),
                sort="shipments",
            ),
        ),
        export=True,
        # `on_time_delivery_rate` is the chart's declared series and is left
        # UNBOUND on purpose: there is no promised-delivery date on shipments,
        # orders or any courier payload, so every "on time %" would be measured
        # against a threshold invented at query time.
        params={
            "source": "agg_shipment_daily",
            "dimension": "courier",
            "metrics": {
                "shipments": {"add": ["shipments"]},
                "delivered": {"add": ["delivered"]},
                "rto_initiated": {"add": ["rto_initiated"]},
                "rto_rate": {
                    "add": ["rto_initiated"],
                    "over": ["delivered", "rto_initiated"],
                    "scale": 100,
                },
            },
        },
        limitation="Comparison uses our own dispatch and delivery timestamps; failed-attempt "
                   "and exception reasons need the courier scan API.",
        keywords=("courier", "carrier", "delhivery", "rto"),
    ),
    AnalyticsViewDefinition(
        number=36,
        name="Order Fulfilment",
        slug="order-fulfilment",
        summary="Orders by fulfilment stage and how long each stage is taking right now.",
        permission=P_ORDERS,
        resolver=ResolverId.BREAKDOWN,
        freshness=Freshness.HOURLY,
        state=ViewState.LIVE,
        filters=_TIME + (FilterKey.ORDER_STATUS, FilterKey.FULFILMENT_STATUS),
        kpis=("orders_count", "on_time_delivery_rate", "cancellation_rate"),
        charts=(
            _chart("orders_by_stage", "Orders by stage", "hbar", "stage", ("orders_count",)),
            _chart("time_to_dispatch", "Hours to dispatch", "line", "date",
                   ("hours_to_dispatch",), FormatId.HOURS),
        ),
        tables=(
            _table(
                "ageing_orders",
                "Oldest unfulfilled orders",
                (
                    _col("order_no", "Order"),
                    _col("placed_at", "Placed"),
                    _col("stage", "Stage"),
                    _col("age_hours", "Age", FormatId.HOURS, "right"),
                ),
                sort="age_hours",
                hint="Nothing is waiting to be fulfilled.",
            ),
        ),
        export=True,
        # `agg_order_daily` stores each fulfilment stage as its own counter
        # COLUMN, not as a groupable dimension, so the breakdown groups by date
        # and carries one stage counter per key. A `stage` dimension would need
        # a pivot no rollup provides. `on_time_delivery_rate` stays unbound —
        # there is no promise date in the schema — and the per-order ageing
        # table needs order-level rows, which no rollup holds.
        params={
            "source": "agg_order_daily",
            "dimension": "date",
            "metrics": {
                "orders_count": {"add": ["orders_total"]},
                "orders_pending": {"add": ["orders_pending"]},
                "orders_paid": {"add": ["orders_paid"]},
                "orders_shipped": {"add": ["orders_shipped"]},
                "orders_delivered": {"add": ["orders_delivered"]},
                "orders_cancelled": {"add": ["orders_cancelled"]},
            },
        },
        keywords=("fulfilment", "pending", "dispatch", "backlog"),
    ),
    AnalyticsViewDefinition(
        number=37,
        name="Returns and Refunds",
        slug="returns-and-refunds",
        summary="Return volume and value by reason, product and outcome, with refunds paid.",
        permission=P_ORDERS,
        resolver=ResolverId.BREAKDOWN,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
        filters=_TIME + (FilterKey.RETURN_REASON, FilterKey.CATEGORY, FilterKey.PRODUCT),
        kpis=("return_rate", "refund_rate", "refunds", "returns"),
        charts=(
            _chart("returns_trend", "Returns and refunds", "bar", "date",
                   ("returns_count", "refund_amount"), FormatId.INT),
            _chart("return_reasons", "Reasons", "hbar", "reason", ("returns_count",)),
        ),
        tables=(
            _table(
                "returns_table",
                "Returns",
                (
                    _col("product", "Product"),
                    _col("reason", "Reason"),
                    _col("returns", "Returns", FormatId.INT, "right"),
                    _col("refund_amount", "Refunded", FormatId.MONEY, "right"),
                ),
                sort="returns",
            ),
        ),
        export=True,
        # `return_reason` is not a dimension of any rollup, so the reason chart
        # and column stay empty. The catalogue `returns` KPI counts return
        # REQUESTS; `returned_units` counts units, so the two are carried under
        # different keys rather than one standing in for the other.
        params={
            "source": "agg_product_daily",
            "dimension": "product",
            "metrics": {
                "returned_value": {"add": ["returned_value"]},
                "returned_units": {"add": ["returned_units"]},
                "return_rate": {
                    "add": ["returned_units"],
                    "over": ["units"],
                    "scale": 100,
                },
            },
        },
        keywords=("returns", "refund", "rma"),
    ),
    AnalyticsViewDefinition(
        number=38,
        name="Cancellation Analytics",
        slug="cancellation-analytics",
        summary="Cancellations by stage, reason and who cancelled, with the revenue lost.",
        permission=P_ORDERS,
        resolver=ResolverId.BREAKDOWN,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
        filters=_TIME + (FilterKey.ORDER_STATUS, FilterKey.PAYMENT_METHOD),
        kpis=("cancellation_rate", "cancelled_orders", "orders_count", "net_revenue"),
        charts=(
            _chart("cancellations_trend", "Cancellations", "line", "date",
                   ("cancellations",)),
            _chart("cancel_reasons", "Reasons", "hbar", "reason", ("cancellations",)),
        ),
        tables=(
            _table(
                "cancellation_table",
                "Cancellations",
                (
                    _col("reason", "Reason"),
                    _col("stage", "Cancelled at"),
                    _col("cancelled_by", "Cancelled by"),
                    _col("orders", "Orders", FormatId.INT, "right"),
                    _col("value_lost", "Value lost", FormatId.MONEY, "right"),
                ),
                sort="orders",
                hint="No orders were cancelled in this period.",
            ),
        ),
        export=True,
        # Cancellation reason, stage and actor are order-level attributes with
        # no rollup column, so that table stays empty. The counts and the rate
        # are exact.
        params={
            "source": "agg_order_daily",
            "dimension": "date",
            "metrics": {
                "cancellations": {"add": ["orders_cancelled"]},
                "cancelled_orders": {"add": ["orders_cancelled"]},
                "cancellation_rate": {
                    "add": ["orders_cancelled"],
                    "over": ["orders_total"],
                    "scale": 100,
                },
                "orders_count": {"add": ["orders_total"]},
            },
        },
        keywords=("cancel", "cancellation", "lost"),
    ),
)


# --------------------------------------------------------------------------
# 9. Payments & Risk
# --------------------------------------------------------------------------

_PAYMENT_VIEWS: tuple[AnalyticsViewDefinition, ...] = (
    AnalyticsViewDefinition(
        number=39,
        name="Payment Analytics",
        slug="payment-analytics",
        summary="Payment volume, method mix and success rate by gateway.",
        permission=P_PAYMENTS,
        resolver=ResolverId.BREAKDOWN,
        freshness=Freshness.HOURLY,
        state=ViewState.LIVE,
        filters=_TIME + (FilterKey.PAYMENT_METHOD, FilterKey.PAYMENT_GATEWAY),
        kpis=("payment_success_rate", "payment_failure_rate", "orders_count", "net_revenue"),
        charts=(
            _chart("method_mix", "Orders by payment method", "stacked-bar", "date",
                   ("orders_count",)),
            _chart("success_trend", "Success rate", "line", "date", ("payment_success_rate",),
                   FormatId.PCT),
        ),
        # `orders_count` is not bound here: `agg_payment_daily` counts payment
        # ATTEMPTS, and one order can make several, so an attempt count under an
        # order label would overstate volume. The order card keeps its
        # `agg_order_daily` binding and this rollup supplies attempts/paid/failed
        # under their own names.
        params={
            "source": "agg_payment_daily",
            "dimension": "payment_method",
            "metrics": {
                "attempts": {"add": ["attempts"]},
                "paid": {"add": ["paid"]},
                "failed": {"add": ["failed"]},
                "payment_success_rate": {
                    "add": ["paid"],
                    "over": ["attempts"],
                    "scale": 100,
                },
            },
        },
        keywords=("payments", "upi", "card", "gateway"),
    ),
    AnalyticsViewDefinition(
        number=40,
        name="Payment Failure",
        slug="payment-failure",
        summary="Failed and dropped payment attempts by reason, method and gateway.",
        permission=P_PAYMENTS,
        resolver=ResolverId.TABLE,
        freshness=Freshness.HOURLY,
        state=ViewState.LIVE,
        filters=_TIME + (FilterKey.PAYMENT_METHOD, FilterKey.PAYMENT_GATEWAY),
        kpis=("payment_failure_rate", "payment_success_rate", "orders_count"),
        charts=(
            _chart("failure_trend", "Failed attempts", "line", "date", ("failed_attempts",)),
        ),
        tables=(
            _table(
                "failure_reasons",
                "Failure reasons",
                (
                    _col("reason", "Reason"),
                    _col("method", "Method"),
                    _col("attempts", "Attempts", FormatId.INT, "right"),
                    _col("value", "Value at risk", FormatId.MONEY, "right"),
                ),
                sort="attempts",
                hint="No failed payment attempts in this period.",
            ),
        ),
        export=True,
        # No `group_by`: `top_failure_reason` is documented as a label that must
        # never be counted, so the rows are the stored buckets themselves rather
        # than a distribution grouped by a field that cannot carry one.
        params={
            "source": "agg_payment_daily",
            "table": "failure_reasons",
            "columns": {
                "reason": "top_failure_reason",
                "method": "payment_method",
                "attempts": "failed",
                "value": "failed_amount",
            },
        },
        keywords=("failure", "declined", "error", "retry"),
    ),
    AnalyticsViewDefinition(
        number=41,
        name="COD Performance",
        slug="cod-performance",
        summary="Share of orders paid on delivery, and how often those orders come back.",
        permission=P_PAYMENTS,
        resolver=ResolverId.TIMESERIES,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
        filters=_TIME + (FilterKey.STATE, FilterKey.CITY, FilterKey.CATEGORY),
        kpis=("cod_delivery_rate", "rto_rate", "cod_surcharge_collected", "aov"),
        charts=(
            _chart("cod_share_trend", "COD share of orders", "line", "date", ("cod_share",),
                   FormatId.PCT),
            _chart("cod_rto_by_state", "COD RTO by state", "hbar", "state", ("rto_rate",),
                   FormatId.PCT),
        ),
        tables=(
            _table(
                "cod_by_state",
                "COD by state",
                (
                    _col("state", "State"),
                    _col("cod_orders", "COD orders", FormatId.INT, "right"),
                    _col("cod_share", "COD share", FormatId.PCT, "right"),
                    _col("delivered_pct", "Delivered", FormatId.PCT, "right"),
                    _col("rto_rate", "RTO", FormatId.PCT, "right"),
                ),
                sort="cod_orders",
            ),
        ),
        # NOT exportable, and this one was the closest call of the six.
        # `agg_geo_daily` genuinely holds every column `cod_by_state` wants, and
        # `GeoResolver` genuinely produces a state table from it — but that
        # resolver feeds the SAME state-grouped rows to every chart the view
        # declares, which would put state names on `cod_share_trend`'s date
        # axis. A wrong chart is worse than an absent table. The state-level COD
        # numbers are already exportable from view 43 (`geographic-sales`),
        # which is bound to `geo` and whose charts are breakdowns.
        export=False,
        # `cod_share` is `cod_orders / orders`, exactly as `AggGeoDaily`
        # documents it. `cod_delivery_rate` is NOT bound: its denominator is
        # dispatched COD orders, and `delivered` in this rollup covers every
        # payment method, so the ratio would silently include prepaid parcels.
        params={
            "source": "agg_geo_daily",
            "metrics": {
                "cod_share": {
                    "add": ["cod_orders"],
                    "over": ["orders"],
                    "scale": 100,
                },
            },
        },
        keywords=("cod", "cash on delivery", "rto", "prepaid"),
    ),
    AnalyticsViewDefinition(
        number=42,
        name="Fraud and Risk Analytics",
        slug="fraud-and-risk-analytics",
        # PARTIAL, downgraded from LIVE, and the downgrade is the point.
        #
        # It was LIVE with NO `params` at all: a LIVE view issues a fetch and
        # renders a data page, so this one promised risk numbers it had no
        # binding to produce. Two things were wrong and both are fixed here.
        #
        # 1. It is bound now. `resolvers/risk.py` reads `payment_events`,
        #    `orders` and `order_addresses` directly — none of them is one of
        #    the fifteen rollups `AnalyticsRepository` reflects its allowlist
        #    from, so no shared resolver can reach them and `table` could never
        #    have filled `flagged_orders` however the params were written. A
        #    custom function is the documented escape hatch for exactly that,
        #    and the function name is a server-trusted registry value.
        #
        # 2. It is PARTIAL because it cannot see disputes. `payment_settlements`
        #    is not present in this deployment's schema, and where it exists it
        #    is loaded from an uploaded gateway settlement report — the same
        #    capability view 64 is gated on. A chargeback count is therefore
        #    unobservable, not zero, and `test_gated_view_is_not_marked_live`
        #    is right that a real caveat means PARTIAL: a fraud screen rendering
        #    LIVE while blind to disputes is a green health check that is a
        #    claim rather than a measurement.
        #
        # What it does NOT do is score. There is no weighted composite and no
        # coefficients: this deployment has no labelled fraud outcomes to fit
        # against, so any weighting would be invented, would look authoritative,
        # and would rank orders by nothing. `risk.py` emits one row per
        # (order, signal) with the observed count behind it and lets the
        # operator judge. `keywords` still lists "chargeback" deliberately —
        # somebody searching for it should land here and read the limitation.
        summary="Risk signals from our own data: repeated payment failures, gateway amount "
                "mismatches, unsigned callbacks, order velocity and address mismatches.",
        permission=P_PAYMENTS,
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.DAILY,
        state=ViewState.PARTIAL,
        sources=(DataSource.INTERNAL_DB,),
        requires=(Capability.GATEWAY_SETTLEMENT_API,),
        filters=_TIME + (FilterKey.PAYMENT_METHOD, FilterKey.STATE),
        kpis=("rto_rate", "payment_success_rate", "cancellation_rate"),
        charts=(
            _chart("risk_signals", "Flagged orders by signal", "hbar", "signal", ("orders",),
                   hint="No order matched a risk signal in this period."),
        ),
        tables=(
            _table(
                "flagged_orders",
                "Flagged orders",
                (
                    _col("order_no", "Order"),
                    _col("signal", "Signal"),
                    # The count behind the signal — three failed attempts reads
                    # very differently from thirty, and a bare label hides that.
                    _col("observed", "Observed", FormatId.INT, "right"),
                    _col("value", "Order value", FormatId.MONEY, "right"),
                    _col("placed_at", "Placed"),
                ),
                sort="value",
                hint="No orders matched a risk signal in this period.",
            ),
        ),
        export=True,
        params={"fn": "risk_signals"},
        limitation="These are internal behavioural signals — repeated payment failures, "
                   "gateway amount mismatches, unsigned callbacks, order velocity and "
                   "billing/shipping address mismatches — not a fraud score. Chargebacks and "
                   "disputes need the gateway settlement feed, which is not connected, so no "
                   "dispute appears here at all.",
        keywords=("fraud", "risk", "abuse", "chargeback"),
    ),
    AnalyticsViewDefinition(
        number=64,
        name="Settlements and Payouts",
        slug="settlements-and-payouts",
        # PARTIAL, re-keyed from GATEWAY_SETTLEMENT_API to GATEWAY_SETTLEMENT_REPORT,
        # and the re-key is the product decision, so it is recorded here:
        #
        #   * The view was INTEGRATION_REQUIRED on the settlements API. The API
        #     is still not built (`settlements.RazorpaySettlementApiClient`
        #     raises by design) and that meaning has NOT silently changed — the
        #     API integration remains future work, and `limitation` says so.
        #   * What unblocked the view is the CSV path: `payment_settlements` and
        #     `agg_settlement_daily` are populated from an uploaded gateway
        #     settlement report (POST /analytics/admin/settlements/upload), which
        #     needs no credentials. GATEWAY_SETTLEMENT_REPORT is satisfied when
        #     settlement rows exist, however they arrived — so the CSV is the
        #     operative source today and an API client would satisfy the same
        #     capability later without another registry change.
        #   * PARTIAL is the ceiling, not LIVE: fees are ACTUAL only on days a
        #     report fully covers, coverage is whatever finance has uploaded,
        #     and the runtime probe in `resolvers/settlements_view.py` downgrades
        #     to a `not_configured` refusal (the gated answer) when
        #     `payment_settlements` holds no rows at all — mirroring how the
        #     other probed views answer before their first data arrives.
        #
        # Two populations, never summed: `*_transacted` columns are payment-date
        # bucketed (the fee side — a cost of the sale's day) and `*_settled`
        # columns are settlement-date bucketed (the cash side — the day the
        # payout cleared). Each chart and column below reads exactly one family.
        summary="Gateway settlement lines from the uploaded settlement report: fees deducted "
                "(payment-dated), payouts credited (settlement-dated), and the lines that "
                "do not reconcile.",
        permission=P_FINANCE,
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.DAILY,
        state=ViewState.PARTIAL,
        sources=(DataSource.INTERNAL_DB, DataSource.PAYMENT_GATEWAY),
        requires=(Capability.GATEWAY_SETTLEMENT_REPORT,),
        filters=_RANGE + (FilterKey.PAYMENT_GATEWAY,),
        charts=(
            _chart("fees_trend", "Gateway fees charged (payment-dated)", "line", "date",
                   ("fee_transacted", "tax_transacted"), FormatId.MONEY,
                   hint="No settlement report covers this period."),
            _chart("payout_trend", "Payouts credited (settlement-dated)", "bar", "date",
                   ("payout_amount",), FormatId.MONEY,
                   hint="No payout was credited in this period."),
        ),
        tables=(
            _table(
                "settlement_days",
                "Daily settlement activity",
                (
                    _col("date", "Day"),
                    _col("gateway", "Gateway"),
                    _col("gross_transacted", "Gross (transacted)", FormatId.MONEY, "right"),
                    _col("fee_transacted", "Fee (transacted)", FormatId.MONEY, "right"),
                    _col("tax_transacted", "Tax on fee", FormatId.MONEY, "right"),
                    _col("matched_txns", "Matched", FormatId.INT, "right"),
                    _col("unmatched_txns", "Unmatched", FormatId.INT, "right"),
                    _col("unsettled_payments", "Unsettled captures", FormatId.INT, "right"),
                    _col("payout_amount", "Payout (settled)", FormatId.MONEY, "right"),
                    _col("settlement_batches", "Batches", FormatId.INT, "right"),
                ),
                sort="date",
                hint="No settlement report covers this period.",
            ),
            _table(
                "variances",
                "Settlement reconciliation",
                (
                    _col("check_name", "Check"),
                    _col("period", "Period"),
                    _col("status", "Status"),
                    _col("source_value", "Source", FormatId.MONEY, "right"),
                    _col("rollup_value", "Expected", FormatId.MONEY, "right"),
                    _col("variance_pct", "Variance", FormatId.PCT, "right"),
                ),
                sort="check_name",
                hint="No settlement report covers this period, so nothing was compared.",
            ),
            _table(
                "unmatched_lines",
                "Settlement lines with no matching payment",
                (
                    _col("transaction_id", "Gateway txn"),
                    _col("transaction_type", "Type"),
                    _col("payment_date", "Transacted"),
                    _col("gross", "Gross", FormatId.MONEY, "right"),
                    _col("fee", "Fee", FormatId.MONEY, "right"),
                    _col("match_status", "Match status"),
                    _col("reference", "Reference"),
                ),
                sort="payment_date",
                hint="Every settlement line in this period was tied to a payment.",
            ),
            _table(
                "unsettled_payments",
                "Captured payments with no settlement",
                (
                    _col("order_id", "Order", FormatId.INT, "right"),
                    _col("order_payment_id", "Payment leg", FormatId.INT, "right"),
                    _col("gateway", "Gateway"),
                    _col("amount", "Amount", FormatId.MONEY, "right"),
                    _col("paid_at", "Captured"),
                    _col("days_outstanding", "Days outstanding", FormatId.INT, "right"),
                ),
                sort="days_outstanding",
                hint="Every capture in this period is explained by a settlement line "
                     "(or is still inside the gateway's settlement cycle).",
            ),
        ),
        export=True,
        params={"fn": "settlements_payouts", "source": "agg_settlement_daily"},
        limitation="Fed by uploaded gateway settlement report CSVs, not a live gateway feed: "
                   "coverage is whatever finance has uploaded, and fees are ACTUAL only on "
                   "days a report fully covers. The Razorpay settlements API integration "
                   "remains future work.",
        keywords=("settlement", "payout", "mdr", "fees", "utr"),
    ),
)


# --------------------------------------------------------------------------
# 10. Marketplace, Stores & B2B
# --------------------------------------------------------------------------
# Kept as a module rather than deleted: the views are specified, gated and
# visible so it is obvious what turning each capability on would unlock.

_MARKETPLACE_VIEWS: tuple[AnalyticsViewDefinition, ...] = (
    AnalyticsViewDefinition(
        number=48,
        name="Marketplace Performance",
        slug="marketplace-performance",
        summary="Orders, revenue, fees and returns per marketplace channel.",
        permission=P_MARKETPLACE,
        resolver=ResolverId.BREAKDOWN,
        freshness=Freshness.DAILY,
        state=ViewState.FEATURE_REQUIRED,
        requires=(Capability.MARKETPLACE_CHANNEL,),
        filters=_RANGE + (FilterKey.MARKETPLACE,),
        limitation="No marketplace channel is connected, so there are no marketplace orders, "
                   "listings or commission fees to report.",
        keywords=("amazon", "flipkart", "marketplace", "channel"),
    ),
    AnalyticsViewDefinition(
        number=49,
        name="Store or Branch Performance",
        slug="store-or-branch-performance",
        # Approved decision: NOT_APPLICABLE, not FEATURE_REQUIRED. This will not
        # become available by connecting anything — it needs a second store.
        summary="Comparison of sales and fulfilment across physical stores or branches.",
        permission=P_MARKETPLACE,
        resolver=ResolverId.BREAKDOWN,
        freshness=Freshness.DAILY,
        state=ViewState.NOT_APPLICABLE,
        requires=(Capability.MULTI_STORE,),
        filters=(),
        limitation="This is a single-store deployment — there are no branches or stores to "
                   "compare, and this view will stay unavailable until that changes.",
        keywords=("store", "branch", "outlet"),
    ),
    AnalyticsViewDefinition(
        number=54,
        name="B2B Customer Analytics",
        slug="b2b-customer-analytics",
        summary="Business accounts by spend, order cadence, price list and credit usage.",
        permission=P_MARKETPLACE,
        resolver=ResolverId.TABLE,
        freshness=Freshness.DAILY,
        state=ViewState.FEATURE_REQUIRED,
        requires=(Capability.B2B_ACCOUNTS,),
        filters=_RANGE,
        limitation="There is no B2B account, price list or credit model, so business buyers "
                   "cannot be separated from retail ones.",
        keywords=("b2b", "wholesale", "trade", "account"),
    ),
)


# --------------------------------------------------------------------------
# 11. Customer Experience & UX
# --------------------------------------------------------------------------

_CX_VIEWS: tuple[AnalyticsViewDefinition, ...] = (
    AnalyticsViewDefinition(
        number=50,
        name="Customer Support and Complaint",
        slug="customer-support-and-complaint",
        # PARTIAL: contact_messages give volume and topic. There is no ticket, so
        # no assignment, no SLA clock and no resolution time.
        summary="Inbound contact volume and topics, and which orders they relate to.",
        permission=P_CX,
        # TIMESERIES, not BREAKDOWN. The only thing this view can honestly draw
        # is the volume trend, and the timeseries resolver draws exactly that and
        # nothing else. A breakdown would additionally fill `message_table`
        # ("Recent messages": received_at, subject, topic, status) with one row
        # per DAY, because a daily rollup has no message-level grain — four empty
        # columns under a heading that promises individual messages, and a CSV
        # export of the same. An absent table reads as "nothing here"; a table of
        # blank rows reads as data.
        resolver=ResolverId.TIMESERIES,
        freshness=Freshness.DAILY,
        state=ViewState.PARTIAL,
        requires=(Capability.SUPPORT_TICKETING,),
        filters=_TIME,
        charts=(
            _chart("messages_trend", "Messages received", "line", "date", ("messages",)),
            _chart("message_topics", "By topic", "hbar", "topic", ("messages",)),
        ),
        tables=(
            _table(
                "message_table",
                "Recent messages",
                (
                    _col("received_at", "Received"),
                    _col("subject", "Subject"),
                    _col("topic", "Topic"),
                    _col("status", "Status"),
                ),
                sort="received_at",
            ),
        ),
        # NOT exportable. `message_table` is a MESSAGE-level list — received_at,
        # subject, topic, status — and the only source the analytics repository
        # may read is a daily rollup, so no resolver in this subsystem can
        # produce those rows. The same call view 2 makes about `recent_orders`.
        # Leaving `export=True` would leave a button that 404s every time it is
        # pressed; filling the table with day rows to make the button work would
        # ship a CSV of four empty columns under a heading that promises
        # individual messages, which is worse. The trend above is what this view
        # measures, and it works.
        export=False,
        limitation="Only inbound contact messages are stored — without ticketing there is no "
                   "assignment, SLA clock, first-response or resolution time to report.",
        # Volume, and only volume. `agg_cx_daily` also stores the status mix
        # (messages_new / messages_replied / messages_closed / messages_other,
        # which partition messages_received exactly), and it is deliberately NOT
        # bound here: this view's only status-bearing element is a per-message
        # table, and binding a day-grain row into it would publish daily
        # aggregates under a "Recent messages" heading.
        #
        # Everything else this view names stays unbound because nothing measures
        # it, and the state stays PARTIAL with SUPPORT_TICKETING in `requires`:
        #   * `message_topics` (by topic) — contact_messages has a free-text
        #     `subject` and no topic taxonomy. Bucketing subjects by keyword
        #     would render invented categories as a measured distribution.
        #   * `message_table` — message grain. No rollup stores it, and the
        #     repository reads only rollups.
        #   * first response, resolution time, SLA, assignment — there is no
        #     assignee, no reply timestamp and no closure timestamp anywhere in
        #     the schema, so every one of them would be measured against a clock
        #     this system never started. This is the same refusal
        #     `agg_shipment_daily` makes about on-time delivery.
        params={
            "source": "agg_cx_daily",
            "metrics": {"messages": {"add": ["messages_received"]}},
        },
        keywords=("support", "complaint", "contact", "tickets"),
    ),
    AnalyticsViewDefinition(
        number=51,
        name="Reviews and Ratings",
        slug="reviews-and-ratings",
        summary="Rating distribution and trend, review volume, and the products pulling the "
                "average down.",
        permission=P_CX,
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
        filters=_TIME + (FilterKey.CATEGORY, FilterKey.PRODUCT),
        charts=(
            _chart("rating_distribution", "Ratings given", "bar", "rating", ("reviews",),
                   FormatId.INT, 1),
            _chart("rating_trend", "Average rating", "line", "date", ("avg_rating",),
                   FormatId.RATIO),
        ),
        tables=(
            _table(
                "rated_products",
                "Products by rating",
                (
                    _col("product", "Product"),
                    _col("reviews", "Reviews", FormatId.INT, "right"),
                    _col("avg_rating", "Average", FormatId.RATIO, "right"),
                    _col("one_star", "1-star", FormatId.INT, "right"),
                ),
                sort="avg_rating",
            ),
        ),
        export=True,
        # Bound to `agg_cx_daily`, which stores the rating distribution as five
        # counts (`rating_1`..`rating_5`) plus the additive pair
        # `rating_sum` / `rated_reviews`. `avg_rating` is therefore computed as
        # SUM(rating_sum) / SUM(rated_reviews) over whatever window is asked for,
        # which re-buckets correctly to a week or a month — a stored daily
        # average would not, and averaging averages is wrong silently.
        #
        # The metric ids are the TABLE's column keys, not catalogue KPI ids:
        # `reviews`, `avg_rating` and `one_star` are display keys this view owns
        # (the KPI catalogue has no review metrics), so the breakdown's rows drop
        # straight into `rated_products`. `reviews` is declared first because the
        # resolver ranks by the first metric's first column — most-reviewed
        # products first, so the top-N is the population worth reading.
        #
        # On the CUSTOM resolver (`resolvers/cx.py::cx_reviews`) rather than
        # BREAKDOWN, because the distribution is five COLUMNS, not a groupable
        # dimension — no rollup groups by rating, so a group-by resolver cannot
        # pivot it, and a view runs exactly one resolver. `cx_reviews` produces
        # the same `rated_products` table through `BreakdownResolver`'s own
        # helpers (same bindings, ordering and row cap — pinned by a regression
        # test), plus the two shapes the breakdown could not express:
        #   * `rating_distribution` (x = "rating") — the five counts pivoted
        #     into five rows, ALWAYS all five: within a measured window a star
        #     nobody gave is a real zero, and a histogram with a missing bar
        #     reads as a four-point scale.
        #   * `rating_trend` (x = "date") — SUM(rating_sum)/SUM(rated_reviews)
        #     per bucket, summed first and divided once, so a weekly re-bucket
        #     equals the true weekly average. A day with no rated reviews is a
        #     GAP in the line, never a 0 — an average of nothing is not 0 stars.
        #
        # `agg_cx_daily` also carries a store-wide row at product_id = 0 holding
        # the day's contact-message volume (a message belongs to no product). It
        # appears here with zero reviews and an em-dash average, sorted last.
        params={
            "fn": "cx_reviews",
            "source": "agg_cx_daily",
            "dimension": "product",
            "metrics": {
                "reviews": {"add": ["reviews_submitted"]},
                "avg_rating": {"add": ["rating_sum"], "over": ["rated_reviews"]},
                "one_star": {"add": ["rating_1"]},
            },
        },
        keywords=("reviews", "ratings", "stars", "feedback"),
    ),
    AnalyticsViewDefinition(
        number=61,
        name="Behaviour and UX Insights",
        slug="behaviour-and-ux-insights",
        summary="Scroll depth, rage clicks, dead clicks and session replay themes.",
        permission=P_CX,
        resolver=ResolverId.BREAKDOWN,
        freshness=Freshness.DAILY,
        state=ViewState.INTEGRATION_REQUIRED,
        sources=(DataSource.CLARITY, DataSource.GA4),
        requires=(Capability.CLARITY_PROJECT, Capability.GA4_DATA_API),
        filters=_RANGE + (FilterKey.DEVICE,),
        limitation="Scroll, click and replay behaviour comes from Microsoft Clarity and GA4; "
                   "neither is connected, and the store records no page-level interaction.",
        keywords=("ux", "clarity", "heatmap", "rage click"),
    ),
    AnalyticsViewDefinition(
        number=71,
        name="Website Speed and Technical Performance",
        slug="website-speed-and-technical-performance",
        # PARTIAL: obs_request_logs is a real, internal APM source — server side
        # is genuinely measured. What is missing is the browser half.
        #
        # Bound now, via `resolvers/risk.py`. The previous note here was right
        # that `obs_request_logs` is outside `AnalyticsRepository`'s reflected
        # allowlist and that no shared resolver can reach it — but the fix is a
        # custom function reading the model directly (the same route
        # `control_centre` takes to `analytics_alerts`), not a new source spec
        # in the repository. `timeseries` was also the wrong shape regardless:
        # it sums a bucket's stored rows, and a percentile is not a sum.
        #
        # PERCENTILES ARE COMPUTED OVER RAW ROWS, NEVER RE-AGGREGATED. There is
        # no stored daily p95 here, because a stored daily p95 cannot be turned
        # into a weekly one — no average, no weighting and no correction factor
        # recovers it, and the wrong answer looks exactly as precise as the
        # right one. `risk.py` sorts the raw `total_ms` values inside whatever
        # bucket is being reported and takes the nearest rank; `latency_summary`
        # does the same over the whole window rather than over the daily points.
        # The scan is bounded at `risk.MAX_LATENCY_SAMPLE` rows, past which the
        # percentiles are WITHHELD (null, with a LATENCY_SAMPLE_EXCEEDED
        # warning) while the additive counts, which do re-aggregate, are kept.
        summary="Server-side latency percentiles, error rate, throughput and the slowest "
                "endpoints and queries, from the internal request log.",
        permission=P_CX,
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.HOURLY,
        state=ViewState.PARTIAL,
        sources=(DataSource.INTERNAL_DB,),
        requires=(Capability.GA4_DATA_API,),
        filters=_TIME,
        charts=(
            # Three percentiles rather than p95 alone: p50 next to p99 is what
            # separates "everything got slower" from "a tail appeared", and a
            # single line cannot show the difference.
            _chart("latency_trend", "Response time (storefront routes)", "line", "date",
                   ("p50_ms", "p95_ms", "p99_ms"), FormatId.INT,
                   hint="No storefront requests were recorded in this period."),
            _chart("error_rate", "Error rate (storefront routes)", "line", "date",
                   ("error_rate",), FormatId.PCT),
            _chart("throughput", "Requests (storefront routes)", "bar", "date",
                   ("requests",), FormatId.INT),
        ),
        tables=(
            # Window-level figures per route class. Admin traffic is kept OUT of
            # the three charts above — an eleven-second admin export is not a
            # storefront latency problem — and kept visible here, so it is
            # separable rather than deleted.
            _table(
                "latency_summary",
                "By route class (whole window)",
                (
                    _col("route_class", "Route class"),
                    _col("requests", "Requests", FormatId.INT, "right"),
                    _col("p50_ms", "p50 (ms)", FormatId.INT, "right"),
                    _col("p95_ms", "p95 (ms)", FormatId.INT, "right"),
                    _col("p99_ms", "p99 (ms)", FormatId.INT, "right"),
                    _col("error_rate", "Errors", FormatId.PCT, "right"),
                ),
                sort="requests",
                hint="No requests were recorded in this period.",
            ),
            _table(
                "slow_endpoints",
                "Slowest endpoints",
                (
                    _col("endpoint", "Endpoint"),
                    _col("route_class", "Class"),
                    _col("requests", "Requests", FormatId.INT, "right"),
                    _col("p95_ms", "p95 (ms)", FormatId.INT, "right"),
                    _col("error_rate", "Errors", FormatId.PCT, "right"),
                ),
                sort="p95_ms",
            ),
            _table(
                "slow_queries",
                "Slowest queries",
                (
                    _col("table_name", "Table"),
                    _col("operation", "Operation"),
                    _col("occurrences", "Occurrences", FormatId.INT, "right"),
                    _col("total_ms", "Total (ms)", FormatId.INT, "right"),
                    _col("max_ms", "Worst (ms)", FormatId.INT, "right"),
                ),
                sort="total_ms",
                hint="No query crossed the slow-query threshold in this period.",
            ),
        ),
        # Still NOT exportable, and now for a different reason than the comment
        # this replaces. The tables fill. What they must not become is a
        # downloadable file of raw latency that reads as a Core Web Vitals
        # report once it is out of the UI and away from the limitation line
        # below. `tests/test_analytics_export.py` names this view explicitly, so
        # re-enabling it stays a deliberate act.
        export=False,
        params={"fn": "request_performance"},
        limitation="Server-side latency measured between request receipt and response start, "
                   "as percentiles over raw request rows — never re-aggregated from stored "
                   "daily figures, and withheld entirely above the scan bound rather than "
                   "estimated. This is NOT what a visitor experiences: TTFB, LCP, CLS and INP "
                   "need browser real-user monitoring such as GA4, which is not connected, so "
                   "nothing here is a Core Web Vitals figure. Charts cover storefront routes "
                   "only; admin and internal traffic is separated into the route-class table.",
        keywords=("speed", "latency", "performance", "core web vitals", "p95", "apm"),
    ),
)


# --------------------------------------------------------------------------
# 12. Analytics Control Centre
# --------------------------------------------------------------------------
# The module that keeps the other eleven honest: is data arriving, does it agree
# with the transactional tables, what changed, and what should someone look at.

_CONTROL_VIEWS: tuple[AnalyticsViewDefinition, ...] = (
    AnalyticsViewDefinition(
        number=62,
        name="Analytics Tracking Health",
        slug="analytics-tracking-health",
        summary="Whether events, rollups and jobs are arriving on time, and which are stale.",
        permission=P_CONTROL,
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.REALTIME,
        state=ViewState.LIVE,
        filters=(FilterKey.DATE_RANGE,),
        charts=(
            _chart("event_volume", "Events received per hour", "bar", "hour", ("events",)),
        ),
        # `special.TrackingHealthResolver` answers only the rollup-watermark
        # quarter of this screen. Provider configured/enabled state, consent,
        # the server-side outbox (SUPPRESSED_NO_CONSENT included) and the
        # environment mismatch all live in `analytics/integrations.py` and were
        # never wired to anything; `control_centre.tracking_health` joins the
        # two. `event_volume` is deliberately left unfilled — an empty bar chart
        # reads as "zero events received", which is a stronger claim than "no
        # client-side stream is connected".
        params={"fn": "tracking_health"},
        bespoke="tracking_health",
        keywords=("tracking", "health", "pipeline", "stale"),
    ),
    AnalyticsViewDefinition(
        number=63,
        name="Data Reconciliation",
        slug="data-reconciliation",
        summary="Rollups checked against the transactional tables, with every variance listed.",
        permission=P_CONTROL,
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
        filters=_RANGE,
        charts=(
            _chart("variance_trend", "Variance vs source of truth", "line", "date",
                   ("variance_pct",), FormatId.PCT),
        ),
        tables=(
            _table(
                "variances",
                "Variances",
                (
                    _col("check_name", "Check"),
                    _col("period", "Period"),
                    _col("source_value", "Source", FormatId.MONEY, "right"),
                    _col("rollup_value", "Rollup", FormatId.MONEY, "right"),
                    _col("variance_pct", "Variance", FormatId.PCT, "right"),
                ),
                sort="variance_pct",
                hint="Every check matched its source of truth.",
            ),
        ),
        # `special.ReconciliationResolver` covers one of the six checks (the
        # revenue-bridge identity) and hardcodes two more as stubs; it stays the
        # right resolver for view 64, which is gated and has no partial answer.
        # This view binds to `analytics/reconciliation.py`, which runs all six
        # and whose `CheckResult.to_row()` already emits exactly the five
        # columns above — including that a check which could not run is
        # `not_configured` with NULL values and never a 0.00% variance.
        params={"fn": "reconciliation_grid"},
        bespoke="reconciliation_grid",
        export=True,
        keywords=("reconciliation", "variance", "audit", "trust"),
    ),
    AnalyticsViewDefinition(
        number=66,
        name="Experiment and A/B Testing",
        slug="experiment-and-ab-testing",
        summary="Running and finished experiments with per-variant conversion, AOV and "
                "revenue.",
        permission=P_CONTROL,
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.DAILY,
        # FEATURE_REQUIRED, not LIVE. LIVE makes the frontend issue a fetch and
        # render a data page; this one has nothing to fetch. Nothing in this
        # deployment assigns a visitor to a variant, so no row anywhere carries
        # an arm: there is no experiment or variant table in the schema, no
        # column matching experiment/variant on any of the 73 tables, and
        # `cart_events.meta` — the one free-form field that could hold an arm —
        # is never written with one. `experiment_id` appears twice in the repo
        # and neither is a data source: it is allowlisted in the Clarity tag
        # allowlist (`frontend/src/features/tracking/clarity.js`) but
        # `setClarityTag` is only ever called with `page_type` and
        # `device_category`, and Clarity tags are write-only to Microsoft
        # regardless — they never come back to this database. A declared
        # parameter nothing populates is not instrumentation.
        #
        # EXPERIMENTS is internal instrumentation this project would have to
        # build, not an external system to connect, so FEATURE_REQUIRED rather
        # than INTEGRATION_REQUIRED. `requires` can only name Capability
        # members, so the four missing pieces are enumerated in `limitation`,
        # which is the string the gated UI actually shows the admin.
        state=ViewState.FEATURE_REQUIRED,
        requires=(Capability.EXPERIMENTS,),
        filters=_RANGE,
        kpis=("conversion_rate", "aov", "orders_count", "net_revenue"),
        charts=(
            _chart("variant_conversion", "Conversion by variant", "bar", "variant",
                   ("conversion_rate",), FormatId.PCT,
                   hint="No experiments have been defined yet."),
        ),
        # Kept, exactly as view 65 keeps `journey_paths`: the view is gated, so
        # the frontend never fetches and the component never renders. It stays
        # so that the day an experiment store lands, the read side is already
        # written and the change is a state flip rather than a new component.
        bespoke="experiment_results",
        # Also kept: a button that 404s is worse than an absent one, and a gated
        # view has nothing to export either way.
        export=False,
        limitation="No experiment framework exists. Four separate pieces are missing and "
                   "each is required before a single number here would mean anything: an "
                   "assignment service that puts a visitor in an arm, exposure logging that "
                   "records which arm they actually saw, variant storage that survives the "
                   "session so an order can be attributed back, and a significance test with "
                   "a minimum sample size. Without the first three there is nothing to split "
                   "by variant, and a single-variant result would read as an experiment that "
                   "ran and found no difference. Without the fourth, the winner is a coin "
                   "flip presented as a decision.",
        keywords=("experiment", "a/b", "test", "variant"),
    ),
    AnalyticsViewDefinition(
        number=73,
        name="Alerts and Anomaly",
        slug="alerts-and-anomaly",
        summary="Metrics that moved outside their expected band, and the alert rules that "
                "fired.",
        permission=P_CONTROL,
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.HOURLY,
        state=ViewState.LIVE,
        filters=_RANGE,
        charts=(
            _chart("alerts_trend", "Alerts fired", "bar", "date", ("alerts",)),
        ),
        tables=(
            _table(
                "alert_feed",
                "Alerts",
                (
                    _col("fired_at", "Fired"),
                    _col("rule", "Rule"),
                    _col("metric", "Metric"),
                    _col("observed", "Observed", FormatId.RATIO, "right"),
                    _col("expected", "Expected", FormatId.RATIO, "right"),
                ),
                sort="fired_at",
                hint="Nothing has breached an alert rule.",
            ),
        ),
        # `analytics/anomalies.py` already returns alerts, skips AND clears, and
        # the last two are the point: a skipped rule and a clear one both render
        # as an absent alert, and only one of them means the store was checked.
        # A `table` binding over `analytics_alerts` could never express that,
        # because the difference is not in the alert table at all.
        params={"fn": "anomaly_feed"},
        bespoke="anomaly_feed",
        export=True,
        keywords=("alerts", "anomaly", "spike", "threshold"),
    ),
)


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------

MODULES: tuple[AnalyticsModuleDefinition, ...] = (
    AnalyticsModuleDefinition(
        number=1,
        name="Executive & Business Health",
        slug="executive",
        summary="The numbers a founder checks first: today, this month, and against plan.",
        permission=P_EXEC,
        icon="gauge",
        views=_EXECUTIVE_VIEWS,
    ),
    AnalyticsModuleDefinition(
        number=2,
        name="Sales, Revenue & Finance",
        slug="sales-finance",
        summary="Revenue, orders, discounts and the margin underneath them.",
        permission=P_SALES,
        icon="banknote",
        views=_SALES_VIEWS,
    ),
    AnalyticsModuleDefinition(
        number=3,
        name="Products & Merchandising",
        slug="products",
        summary="What sells, what is bought together, and what comes back.",
        permission=P_PRODUCTS,
        icon="package",
        views=_PRODUCT_VIEWS,
    ),
    AnalyticsModuleDefinition(
        number=4,
        name="Customers & Retention",
        slug="customers",
        summary="Who buys, who comes back, and what they are worth over time.",
        permission=P_CUSTOMERS,
        icon="users",
        views=_CUSTOMER_VIEWS,
    ),
    AnalyticsModuleDefinition(
        number=5,
        name="Marketing & Attribution",
        slug="marketing",
        summary="Channels, campaigns and the path to purchase. Needs external sources.",
        permission=P_MARKETING,
        icon="megaphone",
        views=_MARKETING_VIEWS,
    ),
    AnalyticsModuleDefinition(
        number=6,
        name="Website & Conversion",
        slug="website",
        summary="Cart, checkout and geography — where visitors convert and where they stop.",
        permission=P_WEBSITE,
        icon="globe",
        views=_WEBSITE_VIEWS,
    ),
    AnalyticsModuleDefinition(
        number=7,
        name="Inventory & Supply Chain",
        slug="inventory",
        summary="Stock on hand, availability and what to reorder.",
        permission=P_INVENTORY,
        icon="warehouse",
        views=_INVENTORY_VIEWS,
    ),
    AnalyticsModuleDefinition(
        number=8,
        name="Orders & Logistics",
        slug="orders",
        summary="Fulfilment, shipping, returns and cancellations.",
        permission=P_ORDERS,
        icon="truck",
        views=_ORDER_VIEWS,
    ),
    AnalyticsModuleDefinition(
        number=9,
        name="Payments & Risk",
        slug="payments",
        summary="Payment success, failures, COD behaviour and risk signals.",
        permission=P_PAYMENTS,
        icon="credit-card",
        views=_PAYMENT_VIEWS,
    ),
    AnalyticsModuleDefinition(
        number=10,
        name="Marketplace, Stores & B2B",
        slug="marketplace",
        summary="Channels beyond the own-store website. None are active today.",
        permission=P_MARKETPLACE,
        icon="store",
        views=_MARKETPLACE_VIEWS,
    ),
    AnalyticsModuleDefinition(
        number=11,
        name="Customer Experience & UX",
        slug="customer-experience",
        summary="Support contact, reviews, on-site behaviour and site speed.",
        permission=P_CX,
        icon="message-circle",
        views=_CX_VIEWS,
    ),
    AnalyticsModuleDefinition(
        number=12,
        name="Analytics Control Centre",
        slug="control-centre",
        summary="Is the data arriving, does it reconcile, and what needs attention.",
        permission=P_CONTROL,
        icon="settings-2",
        views=_CONTROL_VIEWS,
    ),
)


# --------------------------------------------------------------------------
# Lookups
# --------------------------------------------------------------------------
# Built once at import. The registry is immutable, so a dict is safe and every
# view lookup on a request path is O(1).

_MODULE_BY_SLUG: dict[str, AnalyticsModuleDefinition] = {m.slug: m for m in MODULES}
_VIEW_BY_KEY: dict[tuple[str, str], AnalyticsViewDefinition] = {
    (m.slug, v.slug): v for m in MODULES for v in m.views
}


def all_views() -> tuple[AnalyticsViewDefinition, ...]:
    """Every view across every module, in registry order."""
    return tuple(v for m in MODULES for v in m.views)


def get_module(slug: str) -> AnalyticsModuleDefinition | None:
    """Look a module up by slug. Returns None so callers can raise their own 404."""
    return _MODULE_BY_SLUG.get(slug)


def get_view(module_slug: str, view_slug: str) -> AnalyticsViewDefinition | None:
    """Look a view up by its module + view slug pair."""
    return _VIEW_BY_KEY.get((module_slug, view_slug))


def view_count() -> int:
    return len(_VIEW_BY_KEY)


def module_count() -> int:
    return len(MODULES)
