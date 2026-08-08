from datetime import datetime
from typing import Any

from app.schemas.base import AppSchema


class MetricDelta(AppSchema):
    """Single KPI card: current period value, previous period value, % change.

    `delta_pct` is None when previous was 0 — the UI shows "—" instead of
    infinity. Lets the same response shape serve every card.
    """

    current: float
    previous: float
    delta_pct: float | None


class IntMetricDelta(AppSchema):
    current: int
    previous: int
    delta_pct: float | None


class SummaryBlock(AppSchema):
    revenue: MetricDelta
    orders: IntMetricDelta
    new_customers: IntMetricDelta
    aov: MetricDelta


class RevenuePoint(AppSchema):
    date: str
    revenue: float
    orders: int


class TopProduct(AppSchema):
    product_id: int
    name: str
    sku: str
    units: int
    revenue: float


class RecentOrder(AppSchema):
    id: int
    status: str
    total_amount: float
    currency: str
    created_at: datetime | None
    customer_email: str


class LowStockProduct(AppSchema):
    id: int
    name: str
    sku: str
    stock: int
    price: float


class DashboardOverview(AppSchema):
    period: str
    period_start: datetime
    period_end: datetime
    summary: SummaryBlock
    revenue_series: list[RevenuePoint]
    orders_by_status: dict[str, int]
    top_products: list[TopProduct]
    recent_orders: list[RecentOrder]
    low_stock: list[LowStockProduct]
