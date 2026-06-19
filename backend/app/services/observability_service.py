"""Read-side aggregations for the Observability / APM dashboard.

Mirrors the aggregation style of ``analytics_service.py`` (SQL ``GROUP BY`` +
``func.*`` over a time window). All reads hit the two append-only telemetry
tables; nothing here writes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, desc, func, select
from sqlalchemy.orm import Session

from app.models.observability import RequestLog, SlowQuery

# period -> (window timedelta, MySQL DATE_FORMAT pattern for the latency buckets)
_PERIODS: dict[str, tuple[timedelta, str]] = {
    "1h": (timedelta(hours=1), "%Y-%m-%d %H:%i:00"),    # minute buckets
    "24h": (timedelta(hours=24), "%Y-%m-%d %H:00:00"),  # hour buckets
    "7d": (timedelta(days=7), "%Y-%m-%d"),              # day buckets
}


def _bounds(period: str) -> tuple[datetime, datetime, str]:
    window, fmt = _PERIODS.get(period, _PERIODS["24h"])
    end = datetime.now(timezone.utc)
    return end - window, end, fmt


class ObservabilityService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ---- Overview: KPIs + charts ----

    def overview(self, *, period: str = "24h") -> dict[str, Any]:
        start, end, fmt = _bounds(period)

        row = self.db.execute(
            select(
                func.count(RequestLog.id),
                func.coalesce(func.avg(RequestLog.total_ms), 0),
                func.coalesce(func.max(RequestLog.total_ms), 0),
                func.coalesce(func.avg(RequestLog.db_ms), 0),
                func.coalesce(
                    func.sum(case((RequestLog.status >= 500, 1), else_=0)), 0
                ),
            ).where(RequestLog.ts >= start, RequestLog.ts < end)
        ).one()

        total = int(row[0] or 0)
        errors = int(row[4] or 0)

        return {
            "period": period,
            "requests": total,
            "error_rate": round((errors / total) * 100, 2) if total else 0.0,
            "avg_latency_ms": round(float(row[1] or 0), 1),
            "max_latency_ms": int(row[2] or 0),
            "avg_db_ms": round(float(row[3] or 0), 1),
            "latency_series": self._latency_series(start, end, fmt),
            "slowest_routes": self._slowest_routes(start, end),
        }

    def _latency_series(
        self, start: datetime, end: datetime, fmt: str
    ) -> list[dict[str, Any]]:
        bucket = func.date_format(RequestLog.ts, fmt).label("bucket")
        rows = self.db.execute(
            select(
                bucket,
                func.avg(RequestLog.total_ms),
                func.max(RequestLog.total_ms),
                func.count(RequestLog.id),
            )
            .where(RequestLog.ts >= start, RequestLog.ts < end)
            .group_by(bucket)
            .order_by(bucket)
        ).all()
        return [
            {
                "bucket": r[0],
                "avg_ms": round(float(r[1] or 0), 1),
                "max_ms": int(r[2] or 0),
                "count": int(r[3] or 0),
            }
            for r in rows
        ]

    def _slowest_routes(
        self, start: datetime, end: datetime, limit: int = 12
    ) -> list[dict[str, Any]]:
        rows = self.db.execute(
            select(
                RequestLog.route,
                func.avg(RequestLog.total_ms).label("avg_ms"),
                func.count(RequestLog.id).label("cnt"),
            )
            .where(RequestLog.ts >= start, RequestLog.ts < end)
            .group_by(RequestLog.route)
            .order_by(desc("avg_ms"))
            .limit(limit)
        ).all()
        return [
            {"route": r[0], "avg_ms": round(float(r[1] or 0), 1), "count": int(r[2] or 0)}
            for r in rows
        ]

    # ---- Request Logs tab ----

    def requests(
        self,
        *,
        period: str = "24h",
        method: str | None = None,
        status: int | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        start, end, _ = _bounds(period)
        filters = [RequestLog.ts >= start, RequestLog.ts < end]
        if method:
            filters.append(RequestLog.method == method.upper())
        if status is not None:
            filters.append(RequestLog.status == status)
        if q:
            filters.append(RequestLog.route.like(f"%{q}%"))

        total = int(
            self.db.execute(
                select(func.count(RequestLog.id)).where(*filters)
            ).scalar()
            or 0
        )
        items = (
            self.db.execute(
                select(RequestLog)
                .where(*filters)
                .order_by(desc(RequestLog.ts))
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            .scalars()
            .all()
        )
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    # ---- Aggregated Logs tab ----

    def routes(self, *, period: str = "24h", sort: str = "avg") -> list[dict[str, Any]]:
        start, end, _ = _bounds(period)
        avg_ms = func.avg(RequestLog.total_ms).label("avg_ms")
        max_ms = func.max(RequestLog.total_ms).label("max_ms")
        cnt = func.count(RequestLog.id).label("cnt")
        order = {"avg": desc("avg_ms"), "count": desc("cnt"), "max": desc("max_ms")}.get(
            sort, desc("avg_ms")
        )
        rows = self.db.execute(
            select(
                RequestLog.route,
                cnt,
                avg_ms,
                func.min(RequestLog.total_ms),
                max_ms,
                func.avg(RequestLog.db_ms),
            )
            .where(RequestLog.ts >= start, RequestLog.ts < end)
            .group_by(RequestLog.route)
            .order_by(order)
        ).all()
        return [
            {
                "route": r[0],
                "count": int(r[1] or 0),
                "avg_ms": round(float(r[2] or 0), 1),
                "min_ms": int(r[3] or 0),
                "max_ms": int(r[4] or 0),
                "avg_db_ms": round(float(r[5] or 0), 1),
            }
            for r in rows
        ]

    # ---- Slow Queries tab ----

    def slow_queries(
        self,
        *,
        period: str = "24h",
        view: str = "queries",
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        start, end, _ = _bounds(period)
        window = [SlowQuery.ts >= start, SlowQuery.ts < end]

        if view == "table":
            rows = self.db.execute(
                select(
                    SlowQuery.table_name,
                    func.count(SlowQuery.id),
                    func.avg(SlowQuery.duration_ms),
                    func.max(SlowQuery.duration_ms),
                )
                .where(*window)
                .group_by(SlowQuery.table_name)
                .order_by(desc(func.count(SlowQuery.id)))
            ).all()
            return {
                "view": view,
                "tables": [
                    {
                        "table_name": r[0],
                        "count": int(r[1] or 0),
                        "avg_ms": round(float(r[2] or 0), 1),
                        "max_ms": int(r[3] or 0),
                    }
                    for r in rows
                ],
            }

        if view == "recent":
            total = int(
                self.db.execute(
                    select(func.count(SlowQuery.id)).where(*window)
                ).scalar()
                or 0
            )
            items = (
                self.db.execute(
                    select(SlowQuery)
                    .where(*window)
                    .order_by(desc(SlowQuery.ts))
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
                .scalars()
                .all()
            )
            return {
                "view": view,
                "recent": items,
                "total": total,
                "page": page,
                "page_size": page_size,
            }

        # default: group by normalized query fingerprint
        rows = self.db.execute(
            select(
                SlowQuery.fingerprint_hash,
                func.min(SlowQuery.sql_normalized),
                func.min(SlowQuery.table_name),
                func.min(SlowQuery.operation),
                func.count(SlowQuery.id),
                func.avg(SlowQuery.duration_ms),
                func.max(SlowQuery.duration_ms),
            )
            .where(*window)
            .group_by(SlowQuery.fingerprint_hash)
            .order_by(desc(func.avg(SlowQuery.duration_ms)))
            .limit(200)
        ).all()
        return {
            "view": "queries",
            "fingerprints": [
                {
                    "fingerprint_hash": r[0],
                    "sql_normalized": r[1],
                    "table_name": r[2],
                    "operation": r[3],
                    "count": int(r[4] or 0),
                    "avg_ms": round(float(r[5] or 0), 1),
                    "max_ms": int(r[6] or 0),
                }
                for r in rows
            ],
        }
