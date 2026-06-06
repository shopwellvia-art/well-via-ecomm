"""Response shapes for the admin Analytics pages.

Reuses the dashboard's MetricDelta / IntMetricDelta / RevenuePoint so the
KPI-card and time-series contracts stay identical across both surfaces.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.dashboard import IntMetricDelta, MetricDelta, RevenuePoint


class SalesSummary(BaseModel):
    """The four KPI cards at the top of the Sales & Revenue page."""

    revenue: MetricDelta
    orders: IntMetricDelta
    aov: MetricDelta
    discounts: MetricDelta


class CategoryRevenue(BaseModel):
    """One slice of the 'Revenue by category' breakdown.

    `revenue` is line-item revenue (quantity x unit_price) so it can be
    attributed per category; `pct` is its share of the period's line revenue.
    """

    category: str
    revenue: float
    pct: float


class DayOfWeekPoint(BaseModel):
    """One bar of the 'Sales by day of week' chart. `dow` is Mon..Sun."""

    dow: str
    revenue: float
    orders: int


class SalesAnalytics(BaseModel):
    period: str
    granularity: str
    period_start: datetime
    period_end: datetime
    summary: SalesSummary
    series: list[RevenuePoint]
    by_category: list[CategoryRevenue]
    by_day_of_week: list[DayOfWeekPoint]


# ---------------------------------------------------------------------------
# Profit / contribution-margin analytics
# ---------------------------------------------------------------------------


class CostConfig(BaseModel):
    """The five operator-configured cost parameters actually used for this
    report period — echoed so the UI can show what assumptions drove the
    numbers."""

    packing_per_order: float
    handling_per_order: float
    gateway_fee_pct: float
    monthly_overheads: float
    monthly_ad_spend: float


class WaterfallStep(BaseModel):
    """One bar of the contribution-margin waterfall chart.

    `kind` controls colour / treatment in the chart:
      start    — the opening Revenue bar (always positive)
      cost     — a cost deduction (amount is stored as negative)
      subtotal — a C-level subtotal (C1 / C2 / C3)
      result   — the final Net Profit bar
    """

    label: str
    amount: float
    kind: Literal["start", "cost", "subtotal", "result"]


class ProductMarginRow(BaseModel):
    """Per-product gross-margin summary for the period."""

    product_id: int
    name: str
    sku: str
    units: int
    revenue: float
    cost: float
    gross_profit: float
    margin_pct: float | None  # null when revenue is 0


class CategoryMarginRow(BaseModel):
    """Per-category gross-margin summary for the period."""

    category: str
    revenue: float
    cost: float
    gross_profit: float
    margin_pct: float | None  # null when revenue is 0


class ProfitAnalytics(BaseModel):
    """Full contribution-margin report for a period window."""

    period: str
    period_start: datetime
    period_end: datetime

    # Revenue (line-item, not order total_amount)
    revenue: float
    # Direct cost of goods
    product_cost: float
    # Fraction of line-items that had a unit_cost snapshot (data quality signal)
    cost_coverage_pct: float | None

    # Per-order operational costs
    shipping_cost: float
    order_count: int
    packing_cost: float
    handling_cost: float

    # C-level cascade
    c1: float
    gateway_fees: float
    c2: float
    marketing_discounts: float
    ad_spend: float
    c3: float
    overheads: float
    net_profit: float

    # Margin percentages (null when revenue <= 0)
    c1_margin_pct: float | None
    net_margin_pct: float | None

    # Config values used
    config: CostConfig

    # Waterfall chart data
    waterfall: list[WaterfallStep]

    # Drill-down tables
    margin_by_product: list[ProductMarginRow]
    margin_by_category: list[CategoryMarginRow]
