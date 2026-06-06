"""Response shapes for the admin Analytics pages.

Reuses the dashboard's MetricDelta / IntMetricDelta / RevenuePoint so the
KPI-card and time-series contracts stay identical across both surfaces.
"""
from datetime import datetime

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
