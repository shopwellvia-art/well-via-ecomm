"""Admin Observability / APM read endpoints.

Gated behind the dedicated ``observability.view`` permission. All reads are
served from the two append-only telemetry tables populated off the request hot
path; these endpoints themselves are excluded from capture by the middleware.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission
from app.schemas.observability import (
    ObservabilityOverview,
    RequestLogPage,
    RouteAggregate,
    SlowQueryResponse,
)
from app.services.observability_service import ObservabilityService

router = APIRouter()

_PERIOD = "^(1h|24h|7d)$"
_VIEW = Depends(require_permission("observability.view"))


@router.get("/overview", response_model=ObservabilityOverview, dependencies=[_VIEW])
def observability_overview(
    period: str = Query(default="24h", pattern=_PERIOD),
    db: Session = Depends(get_db),
):
    return ObservabilityService(db).overview(period=period)


@router.get("/requests", response_model=RequestLogPage, dependencies=[_VIEW])
def observability_requests(
    period: str = Query(default="24h", pattern=_PERIOD),
    method: str | None = Query(default=None),
    status: int | None = Query(default=None, ge=100, le=599),
    q: str | None = Query(default=None, max_length=255),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return ObservabilityService(db).requests(
        period=period, method=method, status=status, q=q, page=page, page_size=page_size
    )


@router.get("/routes", response_model=list[RouteAggregate], dependencies=[_VIEW])
def observability_routes(
    period: str = Query(default="24h", pattern=_PERIOD),
    sort: str = Query(default="avg", pattern="^(avg|count|max)$"),
    db: Session = Depends(get_db),
):
    return ObservabilityService(db).routes(period=period, sort=sort)


@router.get("/slow-queries", response_model=SlowQueryResponse, dependencies=[_VIEW])
def observability_slow_queries(
    period: str = Query(default="24h", pattern=_PERIOD),
    view: str = Query(default="queries", pattern="^(queries|table|recent)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return ObservabilityService(db).slow_queries(
        period=period, view=view, page=page, page_size=page_size
    )
