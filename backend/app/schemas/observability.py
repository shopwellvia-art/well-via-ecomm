"""Response schemas for the Observability / APM dashboard."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


# ---- Overview (KPIs + charts) ----


class LatencyPoint(BaseModel):
    bucket: str  # time bucket label, e.g. "2026-06-19 03:51:00"
    avg_ms: float
    max_ms: int
    count: int


class SlowestRoute(BaseModel):
    route: str
    avg_ms: float
    count: int


class ObservabilityOverview(BaseModel):
    period: str
    requests: int
    error_rate: float  # % of requests with status >= 500
    avg_latency_ms: float
    max_latency_ms: int
    avg_db_ms: float
    latency_series: list[LatencyPoint]
    slowest_routes: list[SlowestRoute]


# ---- Request Logs tab ----


class RequestLogItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: datetime
    method: str
    route: str
    status: int
    total_ms: int
    db_ms: int
    query_count: int
    user_id: int | None
    request_id: str | None
    ip: str | None


class RequestLogPage(BaseModel):
    items: list[RequestLogItem]
    total: int
    page: int
    page_size: int


# ---- Aggregated Logs tab ----


class RouteAggregate(BaseModel):
    route: str
    count: int
    avg_ms: float
    min_ms: int
    max_ms: int
    avg_db_ms: float


# ---- Slow Queries tab ----


class SlowQueryFingerprint(BaseModel):
    fingerprint_hash: str
    sql_normalized: str
    table_name: str | None
    operation: str | None
    count: int
    avg_ms: float
    max_ms: int


class SlowQueryByTable(BaseModel):
    table_name: str | None
    count: int
    avg_ms: float
    max_ms: int


class SlowQueryRecentItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: datetime
    route: str | None
    table_name: str | None
    operation: str | None
    duration_ms: int
    sql_normalized: str
    request_id: str | None


class SlowQueryResponse(BaseModel):
    view: str  # "queries" | "table" | "recent"
    fingerprints: list[SlowQueryFingerprint] = []
    tables: list[SlowQueryByTable] = []
    recent: list[SlowQueryRecentItem] = []
    total: int = 0
    page: int = 1
    page_size: int = 50
