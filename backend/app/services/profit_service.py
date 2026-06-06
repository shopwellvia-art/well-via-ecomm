"""Contribution-margin analytics service (C1 / C2 / C3 / Net Profit).

Revenue rule: orders count iff status in PAID / SHIPPED / DELIVERED — the same
rule used by DashboardService and AnalyticsService.  Both `_REVENUE_STATUSES`
and `_period_bounds` are reused directly so the three surfaces can never
disagree on "what is revenue".

C-level cascade
---------------
  C1 = revenue - product_cost - shipping - packing - handling
  C2 = C1 - gateway_fees           (gateway applies only to prepaid orders)
  C3 = C2 - marketing_discounts - ad_spend
  Net Profit = C3 - overheads
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.order import Order, OrderItem
from app.models.product import Category, Product
from app.services.dashboard_service import _REVENUE_STATUSES, _period_bounds
from app.services.settings_service import SettingsService

_PERIOD_DAYS: dict[str, int] = {"7d": 7, "30d": 30, "90d": 90}


def _get_cost_float(svc: SettingsService, key: str) -> float:
    """Read a settings key as a float; return 0.0 on missing / invalid."""
    raw = svc.get_raw(key, default="0")
    if not raw:
        return 0.0
    try:
        return float(raw)
    except (ValueError, TypeError):
        return 0.0


class ProfitService:
    def __init__(self, db: Session):
        self.db = db

    def profit(self, period: str = "30d") -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        period_start, _prev_start, _prev_end = _period_bounds(period, now)
        period_days = _PERIOD_DAYS.get(period, 30)

        # ---- Read cost config ----
        svc = SettingsService(self.db)
        packing_per_order = _get_cost_float(svc, "costs.packing_per_order")
        handling_per_order = _get_cost_float(svc, "costs.handling_per_order")
        gateway_fee_pct = _get_cost_float(svc, "costs.gateway_fee_pct")
        monthly_overheads = _get_cost_float(svc, "costs.monthly_overheads")
        monthly_ad_spend = _get_cost_float(svc, "costs.monthly_ad_spend")

        # ---- Core aggregates from qualifying orders ----
        # Single pass over order-items joined to orders for main metrics.
        item_agg = self.db.execute(
            select(
                func.coalesce(
                    func.sum(OrderItem.quantity * OrderItem.unit_price), 0
                ).label("revenue"),
                func.coalesce(
                    func.sum(
                        OrderItem.quantity
                        * func.coalesce(OrderItem.unit_cost, 0)
                    ),
                    0,
                ).label("product_cost"),
                func.count(OrderItem.id).label("total_items"),
                func.sum(
                    case((OrderItem.unit_cost.isnot(None), 1), else_=0)
                ).label("costed_items"),
            )
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .where(
                Order.status.in_(_REVENUE_STATUSES),
                Order.created_at >= period_start,
                Order.created_at < now,
            )
        ).one()

        revenue = float(item_agg.revenue or 0)
        product_cost = float(item_agg.product_cost or 0)
        total_items = int(item_agg.total_items or 0)
        costed_items = int(item_agg.costed_items or 0)

        cost_coverage_pct: float | None = (
            round(100.0 * costed_items / total_items, 1) if total_items > 0 else None
        )

        # ---- Order-level aggregates ----
        order_agg = self.db.execute(
            select(
                func.count(Order.id).label("order_count"),
                func.coalesce(func.sum(Order.shipping_amount), 0).label("shipping_cost"),
                func.coalesce(
                    func.sum(
                        case(
                            (Order.payment_method == "prepaid", Order.total_amount),
                            else_=0,
                        )
                    ),
                    0,
                ).label("prepaid_total"),
                func.coalesce(
                    func.sum(
                        Order.discount_amount + Order.payment_discount_amount
                    ),
                    0,
                ).label("marketing_discounts"),
            ).where(
                Order.status.in_(_REVENUE_STATUSES),
                Order.created_at >= period_start,
                Order.created_at < now,
            )
        ).one()

        order_count = int(order_agg.order_count or 0)
        shipping_cost = float(order_agg.shipping_cost or 0)
        prepaid_total = float(order_agg.prepaid_total or 0)
        marketing_discounts = float(order_agg.marketing_discounts or 0)

        # ---- C-level cascade ----
        packing_cost = packing_per_order * order_count
        handling_cost = handling_per_order * order_count

        c1 = revenue - product_cost - shipping_cost - packing_cost - handling_cost

        gateway_fees = (gateway_fee_pct / 100.0) * prepaid_total
        c2 = c1 - gateway_fees

        ad_spend = monthly_ad_spend * (period_days / 30.0)
        c3 = c2 - marketing_discounts - ad_spend

        overheads = monthly_overheads * (period_days / 30.0)
        net_profit = c3 - overheads

        c1_margin_pct: float | None = (
            round(100.0 * c1 / revenue, 1) if revenue > 0 else None
        )
        net_margin_pct: float | None = (
            round(100.0 * net_profit / revenue, 1) if revenue > 0 else None
        )

        # ---- Waterfall ----
        waterfall: list[dict[str, Any]] = [
            {"label": "Revenue",       "amount": revenue,             "kind": "start"},
            {"label": "Product cost",  "amount": -product_cost,       "kind": "cost"},
            {"label": "Shipping",      "amount": -shipping_cost,      "kind": "cost"},
            {"label": "Packing",       "amount": -packing_cost,       "kind": "cost"},
            {"label": "Handling",      "amount": -handling_cost,      "kind": "cost"},
            {"label": "C1",            "amount": c1,                  "kind": "subtotal"},
            {"label": "Gateway fees",  "amount": -gateway_fees,       "kind": "cost"},
            {"label": "C2",            "amount": c2,                  "kind": "subtotal"},
            {"label": "Discounts",     "amount": -marketing_discounts,"kind": "cost"},
            {"label": "Ad spend",      "amount": -ad_spend,           "kind": "cost"},
            {"label": "C3",            "amount": c3,                  "kind": "subtotal"},
            {"label": "Overheads",     "amount": -overheads,          "kind": "cost"},
            {"label": "Net profit",    "amount": net_profit,          "kind": "result"},
        ]

        # ---- Margin by product (top 20 by revenue) ----
        prod_rows = self.db.execute(
            select(
                Product.id,
                Product.name,
                Product.sku,
                func.sum(OrderItem.quantity).label("units"),
                func.coalesce(
                    func.sum(OrderItem.quantity * OrderItem.unit_price), 0
                ).label("revenue"),
                func.coalesce(
                    func.sum(
                        OrderItem.quantity * func.coalesce(OrderItem.unit_cost, 0)
                    ),
                    0,
                ).label("cost"),
            )
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .join(Product, Product.id == OrderItem.product_id)
            .where(
                Order.status.in_(_REVENUE_STATUSES),
                Order.created_at >= period_start,
                Order.created_at < now,
            )
            .group_by(Product.id, Product.name, Product.sku)
            .order_by(func.sum(OrderItem.quantity * OrderItem.unit_price).desc())
            .limit(20)
        ).all()

        margin_by_product: list[dict[str, Any]] = []
        for r in prod_rows:
            rev = float(r.revenue or 0)
            cst = float(r.cost or 0)
            gp = rev - cst
            margin_by_product.append(
                {
                    "product_id": int(r.id),
                    "name": r.name,
                    "sku": r.sku,
                    "units": int(r.units or 0),
                    "revenue": rev,
                    "cost": cst,
                    "gross_profit": gp,
                    "margin_pct": round(100.0 * gp / rev, 1) if rev > 0 else None,
                }
            )

        # ---- Margin by category ----
        cat_rows = self.db.execute(
            select(
                Category.name,
                func.coalesce(
                    func.sum(OrderItem.quantity * OrderItem.unit_price), 0
                ).label("revenue"),
                func.coalesce(
                    func.sum(
                        OrderItem.quantity * func.coalesce(OrderItem.unit_cost, 0)
                    ),
                    0,
                ).label("cost"),
            )
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .join(Product, Product.id == OrderItem.product_id)
            .outerjoin(Category, Category.id == Product.category_id)
            .where(
                Order.status.in_(_REVENUE_STATUSES),
                Order.created_at >= period_start,
                Order.created_at < now,
            )
            .group_by(Category.name)
            .order_by(func.sum(OrderItem.quantity * OrderItem.unit_price).desc())
        ).all()

        margin_by_category: list[dict[str, Any]] = []
        for r in cat_rows:
            rev = float(r.revenue or 0)
            cst = float(r.cost or 0)
            gp = rev - cst
            margin_by_category.append(
                {
                    "category": r[0] or "Uncategorized",
                    "revenue": rev,
                    "cost": cst,
                    "gross_profit": gp,
                    "margin_pct": round(100.0 * gp / rev, 1) if rev > 0 else None,
                }
            )

        return {
            "period": period,
            "period_start": period_start.isoformat(),
            "period_end": now.isoformat(),
            "revenue": revenue,
            "product_cost": product_cost,
            "cost_coverage_pct": cost_coverage_pct,
            "shipping_cost": shipping_cost,
            "order_count": order_count,
            "packing_cost": packing_cost,
            "handling_cost": handling_cost,
            "c1": c1,
            "gateway_fees": gateway_fees,
            "c2": c2,
            "marketing_discounts": marketing_discounts,
            "ad_spend": ad_spend,
            "c3": c3,
            "overheads": overheads,
            "net_profit": net_profit,
            "c1_margin_pct": c1_margin_pct,
            "net_margin_pct": net_margin_pct,
            "config": {
                "packing_per_order": packing_per_order,
                "handling_per_order": handling_per_order,
                "gateway_fee_pct": gateway_fee_pct,
                "monthly_overheads": monthly_overheads,
                "monthly_ad_spend": monthly_ad_spend,
            },
            "waterfall": waterfall,
            "margin_by_product": margin_by_product,
            "margin_by_category": margin_by_category,
        }
