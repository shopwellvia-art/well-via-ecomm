"""Regression tests for DashboardService.overview().

This is the data behind the admin dashboard at /admin. The whole point of
these tests is to PROVE the dashboard is dynamic (computed live from the DB)
and not static/hardcoded: every section is checked by inserting known rows
and asserting the snapshot moves by exactly the expected delta.

Strategy (mirrors test_sales_analytics_service.py / test_profit_service.py):
- Baseline/delta isolation: capture a snapshot BEFORE inserting test data,
  then compare the delta. Pre-existing prod rows appear in both snapshots and
  cancel out.
- Explicit teardown: all test-owned rows are deleted in finally blocks via a
  fresh session, so teardown never fails on a half-rolled-back transaction.
- Controlled known amounts let us assert exact expected deltas.

The backend runs against a live MySQL instance (no in-memory fallback), so
these tests must be run inside the backend container:

    docker compose exec backend pytest tests/test_dashboard_service.py -v
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Product
from app.models.user import User
from app.services.dashboard_service import DashboardService


# ---------------------------------------------------------------------------
# DB helpers  (local, not shared with other test modules)
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _create_user(db: Session, *, created_at: datetime | None = None) -> User:
    uid = _uid()
    kwargs: dict = dict(
        email=f"dashtest-{uid}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    # created_at defaults to the DB server clock (func.now()). For window-edge
    # assertions we pin it to an explicit UTC time inside the window instead,
    # so the test doesn't race the `< now` boundary or a server tz offset.
    if created_at is not None:
        kwargs["created_at"] = created_at
    u = User(**kwargs)
    db.add(u)
    db.flush()
    return u


def _create_product(db: Session, *, price: Decimal, stock: int = 100) -> Product:
    uid = _uid()
    p = Product(
        sku=f"SKU-DASH-{uid}",
        name=f"DashTestProd {uid}",
        price=price,
        stock=stock,
    )
    db.add(p)
    db.flush()
    return p


def _create_order(
    db: Session,
    user: User,
    items: list[tuple[Product, int]],
    *,
    status: OrderStatus = OrderStatus.PAID,
    total_amount: Decimal,
    created_at: datetime | None = None,
) -> Order:
    order_items = [
        OrderItem(product_id=product.id, quantity=qty, unit_price=product.price)
        for product, qty in items
    ]
    kwargs: dict = dict(
        user_id=user.id,
        status=status,
        subtotal=total_amount,
        total_amount=total_amount,
        currency="INR",
        payment_method="prepaid",
    )
    if created_at is not None:
        kwargs["created_at"] = created_at
    order = Order(**kwargs)
    order.items = order_items
    db.add(order)
    db.flush()
    return order


def _cleanup(
    db: Session | None,
    order_ids: list[int],
    product_ids: list[int],
    user_ids: list[int],
) -> None:
    """Delete all test-owned rows via a fresh session so teardown never fails
    because of a half-rolled-back transaction in the test session."""
    if not any([order_ids, product_ids, user_ids]):
        return
    with SessionLocal() as s:
        if order_ids:
            s.execute(
                text("DELETE FROM order_items WHERE order_id IN :ids"),
                {"ids": tuple(order_ids)},
            )
            s.execute(
                text("DELETE FROM orders WHERE id IN :ids"),
                {"ids": tuple(order_ids)},
            )
        if product_ids:
            s.execute(
                text("DELETE FROM products WHERE id IN :ids"),
                {"ids": tuple(product_ids)},
            )
        if user_ids:
            s.execute(
                text("DELETE FROM points_transactions WHERE user_id IN :ids"),
                {"ids": tuple(user_ids)},
            )
            s.execute(
                text("DELETE FROM users WHERE id IN :ids"),
                {"ids": tuple(user_ids)},
            )
        s.commit()


def _overview(db: Session, period: str = "30d") -> dict:
    """Call DashboardService.overview and expire the session so fresh DB data
    is used (SessionLocal is configured with expire_on_commit=False)."""
    db.expire_all()
    return DashboardService(db).overview(period=period)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDashboardService:

    def test_revenue_and_orders_reflect_live_db(self) -> None:
        """A PAID order with a known total moves the Revenue + Orders KPI cards
        by exactly that amount, and matches an independent raw-SQL aggregation.
        This is the headline 'data is dynamic, not hardcoded' proof."""
        order_ids, product_ids, user_ids = [], [], []
        db = SessionLocal()
        try:
            baseline = _overview(db, "30d")
            rev_base = baseline["summary"]["revenue"]["current"]
            cnt_base = baseline["summary"]["orders"]["current"]

            user = _create_user(db)
            user_ids.append(user.id)
            prod = _create_product(db, price=Decimal("500.00"))
            product_ids.append(prod.id)
            order = _create_order(
                db, user, [(prod, 2)],
                status=OrderStatus.PAID,
                total_amount=Decimal("1000.00"),
                created_at=datetime.now(timezone.utc) - timedelta(days=3),
            )
            order_ids.append(order.id)
            db.commit()

            result = _overview(db, "30d")
            rev_after = result["summary"]["revenue"]["current"]
            cnt_after = result["summary"]["orders"]["current"]

            # Independent raw SQL for the same window, same revenue rule.
            now = datetime.now(timezone.utc)
            start = now - timedelta(days=30)
            raw = db.execute(
                select(
                    func.coalesce(func.sum(Order.total_amount), 0),
                    func.count(Order.id),
                ).where(
                    Order.status.in_(
                        (OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.DELIVERED)
                    ),
                    Order.created_at >= start,
                    Order.created_at < now,
                )
            ).one()

            assert abs((rev_after - rev_base) - 1000.00) < 0.01, (
                f"Revenue delta expected 1000.00, got {rev_after - rev_base:.2f}"
            )
            assert cnt_after - cnt_base == 1, (
                f"Order count delta expected 1, got {cnt_after - cnt_base}"
            )
            assert abs(rev_after - float(raw[0])) < 0.01, (
                f"Dashboard revenue {rev_after:.2f} != raw SQL {float(raw[0]):.2f}"
            )
            assert cnt_after == int(raw[1]), (
                f"Dashboard order count {cnt_after} != raw SQL {int(raw[1])}"
            )
        finally:
            _cleanup(db, order_ids, product_ids, user_ids)
            db.close()

    def test_pending_cancelled_refunded_excluded_from_revenue(self) -> None:
        """PENDING / CANCELLED / REFUNDED orders must NOT count toward revenue
        or the order KPI — this is the _REVENUE_STATUSES rule the page footer
        promises ('Refunded and cancelled orders are excluded')."""
        order_ids, product_ids, user_ids = [], [], []
        db = SessionLocal()
        try:
            baseline = _overview(db, "30d")
            rev_base = baseline["summary"]["revenue"]["current"]
            cnt_base = baseline["summary"]["orders"]["current"]

            user = _create_user(db)
            user_ids.append(user.id)
            prod = _create_product(db, price=Decimal("777.00"))
            product_ids.append(prod.id)
            for status in (
                OrderStatus.PENDING,
                OrderStatus.CANCELLED,
                OrderStatus.REFUNDED,
            ):
                o = _create_order(
                    db, user, [(prod, 1)],
                    status=status,
                    total_amount=Decimal("777.00"),
                    created_at=datetime.now(timezone.utc) - timedelta(days=1),
                )
                order_ids.append(o.id)
            db.commit()

            result = _overview(db, "30d")
            assert abs(result["summary"]["revenue"]["current"] - rev_base) < 0.01, (
                "Non-revenue statuses must not change revenue"
            )
            assert result["summary"]["orders"]["current"] == cnt_base, (
                "Non-revenue statuses must not change the order count"
            )
        finally:
            _cleanup(db, order_ids, product_ids, user_ids)
            db.close()

    def test_shipped_and_delivered_count_as_revenue(self) -> None:
        """SHIPPED and DELIVERED both contribute to revenue (alongside PAID)."""
        order_ids, product_ids, user_ids = [], [], []
        db = SessionLocal()
        try:
            baseline = _overview(db, "30d")
            rev_base = baseline["summary"]["revenue"]["current"]
            cnt_base = baseline["summary"]["orders"]["current"]

            user = _create_user(db)
            user_ids.append(user.id)
            prod = _create_product(db, price=Decimal("300.00"))
            product_ids.append(prod.id)
            for status in (OrderStatus.SHIPPED, OrderStatus.DELIVERED):
                o = _create_order(
                    db, user, [(prod, 1)],
                    status=status,
                    total_amount=Decimal("300.00"),
                    created_at=datetime.now(timezone.utc) - timedelta(days=2),
                )
                order_ids.append(o.id)
            db.commit()

            result = _overview(db, "30d")
            rev_delta = result["summary"]["revenue"]["current"] - rev_base
            cnt_delta = result["summary"]["orders"]["current"] - cnt_base
            assert abs(rev_delta - 600.00) < 0.01, (
                f"Expected revenue delta 600.00 (2 x 300), got {rev_delta:.2f}"
            )
            assert cnt_delta == 2, f"Expected order count delta 2, got {cnt_delta}"
        finally:
            _cleanup(db, order_ids, product_ids, user_ids)
            db.close()

    def test_new_customers_reflects_live_db(self) -> None:
        """The 'New customers' KPI counts users created in the window."""
        order_ids, product_ids, user_ids = [], [], []
        db = SessionLocal()
        try:
            base = _overview(db, "30d")["summary"]["new_customers"]["current"]

            user = _create_user(
                db, created_at=datetime.now(timezone.utc) - timedelta(days=1)
            )
            user_ids.append(user.id)
            db.commit()

            after = _overview(db, "30d")["summary"]["new_customers"]["current"]
            assert after - base == 1, (
                f"New-customers delta expected 1, got {after - base}"
            )
        finally:
            _cleanup(db, order_ids, product_ids, user_ids)
            db.close()

    def test_aov_is_computed_not_hardcoded(self) -> None:
        """Average order value must equal revenue / order_count, proving it is
        derived live rather than being a static number."""
        order_ids, product_ids, user_ids = [], [], []
        db = SessionLocal()
        try:
            user = _create_user(db)
            user_ids.append(user.id)
            prod = _create_product(db, price=Decimal("800.00"))
            product_ids.append(prod.id)
            order = _create_order(
                db, user, [(prod, 1)],
                status=OrderStatus.PAID,
                total_amount=Decimal("800.00"),
                created_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
            order_ids.append(order.id)
            db.commit()

            summary = _overview(db, "30d")["summary"]
            rev = summary["revenue"]["current"]
            cnt = summary["orders"]["current"]
            aov = summary["aov"]["current"]
            assert cnt > 0
            assert abs(aov - rev / cnt) < 0.01, (
                f"AOV={aov:.2f} != revenue/count={rev / cnt:.2f}"
            )
        finally:
            _cleanup(db, order_ids, product_ids, user_ids)
            db.close()

    def test_revenue_series_is_dense_and_reflects_order(self) -> None:
        """The revenue-over-time chart series is zero-filled (one entry per day)
        and the revenue lands on the correct day."""
        order_ids, product_ids, user_ids = [], [], []
        db = SessionLocal()
        try:
            baseline = _overview(db, "30d")
            base_series = {p["date"]: p["revenue"] for p in baseline["revenue_series"]}

            # Densified series: consecutive dates differ by exactly one day, and
            # every point carries the expected keys.
            dates = [p["date"] for p in baseline["revenue_series"]]
            assert len(dates) >= 28, f"30d series too short: {len(dates)} points"
            for prev, nxt in zip(dates, dates[1:]):
                d0 = datetime.fromisoformat(prev).date()
                d1 = datetime.fromisoformat(nxt).date()
                assert (d1 - d0).days == 1, f"Series not dense between {prev} and {nxt}"
            for p in baseline["revenue_series"]:
                assert {"date", "revenue", "orders"} <= set(p)

            order_day = datetime.now(timezone.utc) - timedelta(days=4)
            day_key = order_day.date().isoformat()

            user = _create_user(db)
            user_ids.append(user.id)
            prod = _create_product(db, price=Decimal("450.00"))
            product_ids.append(prod.id)
            order = _create_order(
                db, user, [(prod, 1)],
                status=OrderStatus.PAID,
                total_amount=Decimal("450.00"),
                created_at=order_day,
            )
            order_ids.append(order.id)
            db.commit()

            after = _overview(db, "30d")
            after_series = {p["date"]: p["revenue"] for p in after["revenue_series"]}
            assert day_key in after_series, f"{day_key} missing from densified series"
            delta = after_series[day_key] - base_series.get(day_key, 0.0)
            assert abs(delta - 450.00) < 0.01, (
                f"Day {day_key} revenue delta expected 450.00, got {delta:.2f}"
            )
        finally:
            _cleanup(db, order_ids, product_ids, user_ids)
            db.close()

    def test_orders_by_status_reflects_live_db(self) -> None:
        """The 'Orders by status' donut counts orders per status (all-time)."""
        order_ids, product_ids, user_ids = [], [], []
        db = SessionLocal()
        try:
            base = _overview(db, "30d")["orders_by_status"].get("shipped", 0)

            user = _create_user(db)
            user_ids.append(user.id)
            prod = _create_product(db, price=Decimal("120.00"))
            product_ids.append(prod.id)
            order = _create_order(
                db, user, [(prod, 1)],
                status=OrderStatus.SHIPPED,
                total_amount=Decimal("120.00"),
                created_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
            order_ids.append(order.id)
            db.commit()

            after = _overview(db, "30d")["orders_by_status"].get("shipped", 0)
            assert after - base == 1, (
                f"'shipped' status count delta expected 1, got {after - base}"
            )
        finally:
            _cleanup(db, order_ids, product_ids, user_ids)
            db.close()

    def test_top_products_reflects_order_items(self) -> None:
        """'Top selling products' aggregates units + revenue from order items.
        A product with a deliberately huge unit count ranks at the top with the
        exact units/revenue we inserted."""
        order_ids, product_ids, user_ids = [], [], []
        db = SessionLocal()
        try:
            user = _create_user(db)
            user_ids.append(user.id)
            prod = _create_product(db, price=Decimal("10.00"))
            product_ids.append(prod.id)
            qty = 1_000_000  # large enough to be the #1 seller in a test DB
            order = _create_order(
                db, user, [(prod, qty)],
                status=OrderStatus.PAID,
                total_amount=Decimal("10000000.00"),
                created_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
            order_ids.append(order.id)
            db.commit()

            top = _overview(db, "30d")["top_products"]
            mine = next((r for r in top if r["product_id"] == prod.id), None)
            assert mine is not None, "High-volume product missing from top_products"
            assert mine["units"] == qty, f"units expected {qty}, got {mine['units']}"
            assert abs(mine["revenue"] - qty * 10.0) < 0.01, (
                f"revenue expected {qty * 10.0}, got {mine['revenue']}"
            )
            assert mine["sku"] == prod.sku and mine["name"] == prod.name
        finally:
            _cleanup(db, order_ids, product_ids, user_ids)
            db.close()

    def test_recent_orders_lists_newest_first(self) -> None:
        """'Recent orders' shows the newest order first with live row fields."""
        order_ids, product_ids, user_ids = [], [], []
        db = SessionLocal()
        try:
            user = _create_user(db)
            user_ids.append(user.id)
            prod = _create_product(db, price=Decimal("250.00"))
            product_ids.append(prod.id)
            # Default created_at == server now -> newest row, highest id, so it
            # sorts first (created_at desc, id desc).
            order = _create_order(
                db, user, [(prod, 1)],
                status=OrderStatus.PAID,
                total_amount=Decimal("250.00"),
            )
            order_ids.append(order.id)
            db.commit()

            recent = _overview(db, "30d")["recent_orders"]
            assert recent, "recent_orders unexpectedly empty"
            assert recent[0]["id"] == order.id, (
                f"Newest order {order.id} not first; got {recent[0]['id']}"
            )
            assert recent[0]["customer_email"] == user.email
            assert abs(recent[0]["total_amount"] - 250.00) < 0.01
            assert recent[0]["status"] == "paid"
            assert recent[0]["currency"] == "INR"
        finally:
            _cleanup(db, order_ids, product_ids, user_ids)
            db.close()

    def test_low_stock_includes_low_excludes_well_stocked(self) -> None:
        """'Low stock' lists products at/under the threshold (5) and never
        lists well-stocked ones."""
        order_ids, product_ids, user_ids = [], [], []
        db = SessionLocal()
        try:
            low = _create_product(db, price=Decimal("99.00"), stock=3)
            high = _create_product(db, price=Decimal("99.00"), stock=999)
            product_ids.extend([low.id, high.id])
            db.commit()

            rows = _overview(db, "30d")["low_stock"]
            ids = {r["id"] for r in rows}

            # Hard invariants: threshold is enforced both ways.
            assert high.id not in ids, "Well-stocked product must not be in low_stock"
            assert all(r["stock"] <= 5 for r in rows), (
                "low_stock returned a product with stock > 5"
            )
            # The list is capped at 8 (stock asc). Our stock=3 product appears
            # unless the DB already has 8+ products with even lower stock.
            if len(rows) < 8:
                assert low.id in ids, "Low-stock product missing from low_stock"
        finally:
            _cleanup(db, order_ids, product_ids, user_ids)
            db.close()

    @pytest.mark.parametrize("period,min_days", [("7d", 7), ("30d", 28), ("90d", 88)])
    def test_period_is_echoed_and_window_scales(self, period: str, min_days: int) -> None:
        """The requested period is echoed back and the revenue series window
        scales with it — proof the period selector actually drives the query."""
        db = SessionLocal()
        try:
            result = _overview(db, period)
            assert result["period"] == period
            assert len(result["revenue_series"]) >= min_days
        finally:
            db.close()
