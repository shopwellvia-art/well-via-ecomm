"""Admin Analytics read endpoints. One page = one call.

Gated behind `dashboard.view` — analytics is the same audience as the
dashboard, so it reuses that permission rather than minting a new one.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission
from app.schemas.analytics import ProfitAnalytics, SalesAnalytics
from app.services.analytics_service import AnalyticsService
from app.services.profit_service import ProfitService

router = APIRouter()


@router.get(
    "/sales",
    response_model=SalesAnalytics,
    dependencies=[Depends(require_permission("dashboard.view"))],
)
def admin_analytics_sales(
    period: str = Query(default="30d", pattern="^(7d|30d|90d)$"),
    granularity: str = Query(default="day", pattern="^(day|week|month)$"),
    db: Session = Depends(get_db),
):
    return AnalyticsService(db).sales(period=period, granularity=granularity)


@router.get(
    "/profit",
    response_model=ProfitAnalytics,
    dependencies=[Depends(require_permission("dashboard.view"))],
)
def admin_analytics_profit(
    period: str = Query(default="30d", pattern="^(7d|30d|90d)$"),
    db: Session = Depends(get_db),
):
    return ProfitService(db).profit(period=period)
