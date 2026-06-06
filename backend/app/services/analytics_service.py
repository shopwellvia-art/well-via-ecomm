"""Analytics aggregates for the admin Analytics sidebar.

Page 1: Sales & Revenue. Built on the same revenue rule as the dashboard
(an order counts iff status in PAID / SHIPPED / DELIVERED) by reusing the
dashboard's bounds/delta/status helpers — the two surfaces must never
disagree on "what is revenue".
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models.order import Order, OrderItem
from app.models.product import Category, Product
from app.services.dashboard_service import (
    _REVENUE_STATUSES,
    _pct_delta,
    _period_bounds,
)

# Mon..Sun, indexed by Python's date.weekday() (Mon=0).
_DOW_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class AnalyticsService:
    def __init__(self, db: Session):
        self.db = db

    def sales(self, *, period: str = "30d", granularity: str = "day") -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        period_start, prev_start, prev_end = _period_bounds(period, now)

        cur = self._summary(period_start, now)
        prev = self._summary(prev_start, prev_end)

        # One daily fetch feeds both the (re-bucketed) trend line and the
        # day-of-week breakdown — no need to hit the DB twice for the same rows.
        daily = self._daily_series(period_start, now)

        return {
            "period": period,
            "granularity": granularity,
            "period_start": period_start.isoformat(),
            "period_end": now.isoformat(),
            "summary": {
                "revenue": self._delta(cur["revenue"], prev["revenue"]),
                "orders": self._delta_int(cur["count"], prev["count"]),
                "aov": self._delta(cur["aov"], prev["aov"]),
                "discounts": self._delta(cur["discounts"], prev["discounts"]),
            },
            "series": self._bucket(daily, granularity),
            "by_category": self._by_category(period_start, now),
            "by_day_of_week": self._by_day_of_week(daily),
        }

    # ---- KPI cards ----

    def _summary(self, start: datetime, end: datetime) -> dict[str, Any]:
        row = self.db.execute(
            select(
                func.coalesce(func.sum(Order.total_amount), 0),
                func.count(Order.id),
                func.coalesce(func.sum(Order.discount_amount), 0),
            ).where(
                Order.status.in_(_REVENUE_STATUSES),
                Order.created_at >= start,
                Order.created_at < end,
            )
        ).one()
        revenue = Decimal(row[0] or 0)
        count = int(row[1] or 0)
        discounts = Decimal(row[2] or 0)
        aov = revenue / count if count > 0 else Decimal("0")
        return {
            "revenue": float(revenue),
            "count": count,
            "discounts": float(discounts),
            "aov": float(aov),
        }

    @staticmethod
    def _delta(cur: float, prev: float) -> dict[str, Any]:
        return {"current": cur, "previous": prev, "delta_pct": _pct_delta(cur, prev)}

    @staticmethod
    def _delta_int(cur: int, prev: int) -> dict[str, Any]:
        return {"current": cur, "previous": prev, "delta_pct": _pct_delta(cur, prev)}

    # ---- Time series ----

    def _daily_series(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Zero-filled daily revenue + order count. Densified so re-bucketing
        and the day-of-week roll-up never have to reason about gaps."""
        rows = self.db.execute(
            select(
                func.date(Order.created_at).label("day"),
                func.coalesce(func.sum(Order.total_amount), 0).label("revenue"),
                func.count(Order.id).label("orders"),
            )
            .where(
                Order.status.in_(_REVENUE_STATUSES),
                Order.created_at >= start,
                Order.created_at < end,
            )
            .group_by("day")
            .order_by("day")
        ).all()
        by_day: dict[str, dict[str, Any]] = {}
        for r in rows:
            day = r[0]
            key = day.isoformat() if hasattr(day, "isoformat") else str(day)
            by_day[key] = {"revenue": float(r[1] or 0), "orders": int(r[2] or 0)}

        out: list[dict[str, Any]] = []
        cursor = start.date()
        end_date = end.date()
        while cursor <= end_date:
            key = cursor.isoformat()
            hit = by_day.get(key)
            out.append(
                {
                    "date": key,
                    "revenue": hit["revenue"] if hit else 0.0,
                    "orders": hit["orders"] if hit else 0,
                }
            )
            cursor = cursor + timedelta(days=1)
        return out

    @staticmethod
    def _bucket(daily: list[dict[str, Any]], granularity: str) -> list[dict[str, Any]]:
        """Roll the daily series up to week (Mon-start) or month buckets.
        'day' passes through unchanged."""
        if granularity == "day":
            return daily

        buckets: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for point in daily:
            d = date.fromisoformat(point["date"])
            if granularity == "week":
                key = (d - timedelta(days=d.weekday())).isoformat()
            else:  # month
                key = d.replace(day=1).isoformat()
            if key not in buckets:
                buckets[key] = {"date": key, "revenue": 0.0, "orders": 0}
                order.append(key)
            buckets[key]["revenue"] += point["revenue"]
            buckets[key]["orders"] += point["orders"]
        return [buckets[k] for k in order]

    @staticmethod
    def _by_day_of_week(daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Sum the daily series into Mon..Sun buckets so admins can see which
        weekday sells best within the selected window."""
        agg = [{"revenue": 0.0, "orders": 0} for _ in range(7)]
        for point in daily:
            idx = date.fromisoformat(point["date"]).weekday()
            agg[idx]["revenue"] += point["revenue"]
            agg[idx]["orders"] += point["orders"]
        return [
            {"dow": _DOW_LABELS[i], "revenue": agg[i]["revenue"], "orders": agg[i]["orders"]}
            for i in range(7)
        ]

    # ---- Category breakdown ----

    def _by_category(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Line-item revenue per category. Products with no category fold into
        'Uncategorized'. Percentages are shares of the period's line revenue."""
        rows = self.db.execute(
            select(
                Category.name,
                func.sum(OrderItem.quantity * OrderItem.unit_price).label("revenue"),
            )
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .join(Product, Product.id == OrderItem.product_id)
            .outerjoin(Category, Category.id == Product.category_id)
            .where(
                Order.status.in_(_REVENUE_STATUSES),
                Order.created_at >= start,
                Order.created_at < end,
            )
            .group_by(Category.name)
            .order_by(desc("revenue"))
        ).all()

        total = sum(float(r[1] or 0) for r in rows)
        out: list[dict[str, Any]] = []
        for r in rows:
            rev = float(r[1] or 0)
            out.append(
                {
                    "category": r[0] or "Uncategorized",
                    "revenue": rev,
                    "pct": round((rev / total) * 100, 1) if total > 0 else 0.0,
                }
            )
        return out
