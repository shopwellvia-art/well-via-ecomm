"""Regression tests for AnalyticsService.sales().

Guards against anyone hardcoding values or breaking the live-DB aggregation.

Strategy (mirrors test_profit_service.py):
- Baseline/delta isolation: capture a snapshot BEFORE inserting test data,
  then compare the delta.  Pre-existing prod orders cancel out.
- Explicit teardown: all test-owned rows are deleted in finally blocks.
- Controlled known amounts let us assert exact expected deltas.
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
from app.models.product import Category, Product
from app.models.user import User
from app.services.analytics_service import AnalyticsService


# ---------------------------------------------------------------------------
# DB helpers  (local, not shared with other test modules)
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _create_user(db: Session) -> User:
    uid = _uid()
    u = User(
        email=f"salestest-{uid}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(u)
    db.flush()
    return u


def _create_product(db: Session, *, price: Decimal, category: Category | None = None) -> Product:
    uid = _uid()
    p = Product(
        sku=f"SKU-SALES-{uid}",
        name=f"SalesTestProd {uid}",
        price=price,
        stock=100,
        category_id=category.id if category else None,
    )
    db.add(p)
    db.flush()
    return p


def _create_category(db: Session) -> Category:
    uid = _uid()
    c = Category(name=f"SalesTestCat-{uid}", slug=f"salestest-{uid}")
    db.add(c)
    db.flush()
    return c


def _create_order(
    db: Session,
    user: User,
    items: list[tuple[Product, int]],
    *,
    status: OrderStatus = OrderStatus.PAID,
    total_amount: Decimal,
    created_at: datetime | None = None,
) -> Order:
    order_items = []
    for product, qty in items:
        oi = OrderItem(
            product_id=product.id,
            quantity=qty,
            unit_price=product.price,
        )
        order_items.append(oi)

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
    category_ids: list[int],
) -> None:
    """Delete all test-owned rows via a fresh session so teardown never fails
    because of a half-rolled-back transaction in the test session."""
    if not any([order_ids, product_ids, user_ids, category_ids]):
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
        if category_ids:
            s.execute(
                text("DELETE FROM categories WHERE id IN :ids"),
                {"ids": tuple(category_ids)},
            )
        s.commit()


def _sales(db: Session, period: str = "30d") -> dict:
    """Call AnalyticsService.sales and expire session so fresh DB data is used."""
    db.expire_all()
    return AnalyticsService(db).sales(period=period)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSalesAnalyticsService:

    def test_revenue_and_order_count_reflect_live_db(self) -> None:
        """Inserting a PAID order with a known total_amount increases revenue and
        order_count by exactly the expected values.  Verifies live-DB aggregation."""
        order_ids, product_ids, user_ids, category_ids = [], [], [], []
        db = SessionLocal()
        try:
            # Baseline
            baseline = _sales(db, "30d")
            rev_base = baseline["summary"]["revenue"]["current"]
            cnt_base = baseline["summary"]["orders"]["current"]

            # Create controlled test data inside the 30d window
            user = _create_user(db)
            user_ids.append(user.id)
            prod = _create_product(db, price=Decimal("500.00"))
            product_ids.append(prod.id)

            order = _create_order(
                db,
                user,
                [(prod, 2)],
                status=OrderStatus.PAID,
                total_amount=Decimal("1000.00"),
                created_at=datetime.now(timezone.utc) - timedelta(days=3),
            )
            order_ids.append(order.id)
            db.commit()

            # Post-insert result
            result = _sales(db, "30d")
            rev_after = result["summary"]["revenue"]["current"]
            cnt_after = result["summary"]["orders"]["current"]

            # Independent raw SQL aggregation for the same window
            from sqlalchemy import text as sa_text, func, select
            from app.models.order import Order as OrderModel
            now = datetime.now(timezone.utc)
            start = now - timedelta(days=30)
            raw = db.execute(
                select(
                    func.coalesce(func.sum(OrderModel.total_amount), 0),
                    func.count(OrderModel.id),
                ).where(
                    OrderModel.status.in_(
                        (OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.DELIVERED)
                    ),
                    OrderModel.created_at >= start,
                    OrderModel.created_at < now,
                )
            ).one()
            raw_revenue = float(raw[0])
            raw_count   = int(raw[1])

            # Assert delta vs baseline
            assert abs((rev_after - rev_base) - 1000.00) < 0.01, (
                f"Revenue delta expected 1000.00, got {rev_after - rev_base:.2f}"
            )
            assert cnt_after - cnt_base == 1, (
                f"Order count delta expected 1, got {cnt_after - cnt_base}"
            )

            # Assert AnalyticsService matches independent SQL exactly
            assert abs(rev_after - raw_revenue) < 0.01, (
                f"AnalyticsService revenue {rev_after:.2f} != raw SQL {raw_revenue:.2f}"
            )
            assert cnt_after == raw_count, (
                f"AnalyticsService order count {cnt_after} != raw SQL {raw_count}"
            )

        finally:
            _cleanup(db, order_ids, product_ids, user_ids, category_ids)
            db.close()

    def test_pending_order_excluded_from_revenue(self) -> None:
        """A PENDING order must not appear in revenue or order count."""
        order_ids, product_ids, user_ids, category_ids = [], [], [], []
        db = SessionLocal()
        try:
            baseline = _sales(db, "30d")
            rev_base = baseline["summary"]["revenue"]["current"]
            cnt_base = baseline["summary"]["orders"]["current"]

            user = _create_user(db)
            user_ids.append(user.id)
            prod = _create_product(db, price=Decimal("999.00"))
            product_ids.append(prod.id)

            order = _create_order(
                db,
                user,
                [(prod, 1)],
                status=OrderStatus.PENDING,
                total_amount=Decimal("999.00"),
                created_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
            order_ids.append(order.id)
            db.commit()

            result = _sales(db, "30d")
            assert abs(result["summary"]["revenue"]["current"] - rev_base) < 0.01, (
                "PENDING order must not change revenue"
            )
            assert result["summary"]["orders"]["current"] == cnt_base, (
                "PENDING order must not change order count"
            )

        finally:
            _cleanup(db, order_ids, product_ids, user_ids, category_ids)
            db.close()

    def test_cancelled_order_excluded_from_revenue(self) -> None:
        """A CANCELLED order must not appear in revenue or order count.

        Measured inside ONE database snapshot rather than by differencing two
        reads taken a few hundred milliseconds apart.

        The baseline-delta form this used to have is order-dependent by
        construction. `revenue.current` is a global aggregate over
        `[now - 30d, now)`; `now` advances between the baseline read and the
        second read; and every suite in the session shares this database. Any
        row that enters or leaves that window in between — a leaked order from a
        suite that failed before its teardown, a row committed by something a
        previous test started and never stopped — lands on THIS assertion
        instead of on the code that caused it. The sibling dashboard KPI test
        failed exactly that way: `assert (13 - 11) == 1`, two rows appearing
        where the test had created one.

        So instead: create the order, ask the service what window it used, and
        aggregate that window twice — once whole, once with this order's id
        excluded — through the same session with no commit in between, so all
        three reads share one REPEATABLE READ snapshot. The difference between
        the two aggregates is this order's contribution and nobody else's, and
        no amount of foreign traffic can move it.
        """
        order_ids, product_ids, user_ids, category_ids = [], [], [], []
        db = SessionLocal()
        try:
            user = _create_user(db)
            user_ids.append(user.id)
            prod = _create_product(db, price=Decimal("500.00"))
            product_ids.append(prod.id)

            order = _create_order(
                db,
                user,
                [(prod, 1)],
                status=OrderStatus.CANCELLED,
                total_amount=Decimal("500.00"),
                created_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
            order_ids.append(order.id)
            db.commit()

            # From here to the last assert: no commit, so one snapshot.
            result = _sales(db, "30d")
            start = datetime.fromisoformat(result["period_start"])
            end = datetime.fromisoformat(result["period_end"])

            # The order really is inside the window by TIME — otherwise this
            # test would pass for the wrong reason (excluded by date, not by
            # status) and would keep passing if the status rule broke.
            placed_at = db.execute(
                select(Order.created_at).where(Order.id == order.id)
            ).scalar_one()
            if placed_at.tzinfo is None:
                placed_at = placed_at.replace(tzinfo=timezone.utc)
            assert start <= placed_at < end, (
                f"fixture: the cancelled order sits at {placed_at}, outside the "
                f"window [{start}, {end}) the service reported"
            )

            def _window_totals(exclude: list[int]) -> tuple[float, int]:
                stmt = select(
                    func.coalesce(func.sum(Order.total_amount), 0),
                    func.count(Order.id),
                ).where(
                    Order.status.in_(
                        (OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.DELIVERED)
                    ),
                    Order.created_at >= start,
                    Order.created_at < end,
                )
                if exclude:
                    stmt = stmt.where(Order.id.not_in(exclude))
                row = db.execute(stmt).one()
                return float(row[0] or 0), int(row[1] or 0)

            whole_revenue, whole_count = _window_totals([])
            without_revenue, without_count = _window_totals([order.id])

            assert abs(whole_revenue - without_revenue) < 0.01, (
                "CANCELLED order must not change revenue"
            )
            assert whole_count == without_count, (
                "CANCELLED order must not change order count"
            )
            # ...and the service agrees with that SQL over its own window, so
            # this still proves live aggregation and not a hardcoded number.
            assert abs(result["summary"]["revenue"]["current"] - whole_revenue) < 0.01, (
                f"service revenue {result['summary']['revenue']['current']:.2f} != "
                f"raw SQL over its own window {whole_revenue:.2f}"
            )
            assert result["summary"]["orders"]["current"] == whole_count

        finally:
            _cleanup(db, order_ids, product_ids, user_ids, category_ids)
            db.close()

    def test_shipped_and_delivered_count_as_revenue(self) -> None:
        """SHIPPED and DELIVERED orders both contribute to revenue."""
        order_ids, product_ids, user_ids, category_ids = [], [], [], []
        db = SessionLocal()
        try:
            baseline = _sales(db, "30d")
            rev_base = baseline["summary"]["revenue"]["current"]
            cnt_base = baseline["summary"]["orders"]["current"]

            user = _create_user(db)
            user_ids.append(user.id)
            prod = _create_product(db, price=Decimal("300.00"))
            product_ids.append(prod.id)

            for status in (OrderStatus.SHIPPED, OrderStatus.DELIVERED):
                o = _create_order(
                    db,
                    user,
                    [(prod, 1)],
                    status=status,
                    total_amount=Decimal("300.00"),
                    created_at=datetime.now(timezone.utc) - timedelta(days=2),
                )
                order_ids.append(o.id)
            db.commit()

            result = _sales(db, "30d")
            rev_delta = result["summary"]["revenue"]["current"] - rev_base
            cnt_delta = result["summary"]["orders"]["current"] - cnt_base

            assert abs(rev_delta - 600.00) < 0.01, (
                f"Expected revenue delta 600.00 (2 x 300), got {rev_delta:.2f}"
            )
            assert cnt_delta == 2, (
                f"Expected order count delta 2, got {cnt_delta}"
            )

        finally:
            _cleanup(db, order_ids, product_ids, user_ids, category_ids)
            db.close()

    def test_by_category_reflects_order_items(self) -> None:
        """by_category sums OrderItem revenue (qty*unit_price) per category."""
        order_ids, product_ids, user_ids, category_ids = [], [], [], []
        db = SessionLocal()
        try:
            baseline = _sales(db, "30d")

            user = _create_user(db)
            user_ids.append(user.id)
            cat = _create_category(db)
            category_ids.append(cat.id)
            prod = _create_product(db, price=Decimal("250.00"), category=cat)
            product_ids.append(prod.id)

            # 4 units at 250 = 1000 line revenue
            order = _create_order(
                db,
                user,
                [(prod, 4)],
                status=OrderStatus.PAID,
                total_amount=Decimal("1000.00"),
                created_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
            order_ids.append(order.id)
            db.commit()

            result = _sales(db, "30d")
            baseline_cats = {c["category"]: c["revenue"] for c in baseline["by_category"]}
            result_cats   = {c["category"]: c["revenue"] for c in result["by_category"]}

            # Our category should appear with at least the 1000.00 we added
            assert cat.name in result_cats, (
                f"Category '{cat.name}' not found in by_category result"
            )
            new_cat_delta = result_cats[cat.name] - baseline_cats.get(cat.name, 0.0)
            assert abs(new_cat_delta - 1000.00) < 0.01, (
                f"Expected category revenue delta 1000.00, got {new_cat_delta:.2f}"
            )

        finally:
            _cleanup(db, order_ids, product_ids, user_ids, category_ids)
            db.close()

    def test_aov_computed_from_db_not_hardcoded(self) -> None:
        """AOV = revenue / order_count. Verifies it is computed, not static."""
        order_ids, product_ids, user_ids, category_ids = [], [], [], []
        db = SessionLocal()
        try:
            baseline = _sales(db, "30d")
            rev_base = baseline["summary"]["revenue"]["current"]
            cnt_base = baseline["summary"]["orders"]["current"]

            # Only proceed if there is at least some existing data so the
            # test is meaningful (cnt_base > 0 means AOV is computed live).
            if cnt_base == 0:
                pytest.skip("No existing orders in the 30d window; AOV test skipped.")

            user = _create_user(db)
            user_ids.append(user.id)
            prod = _create_product(db, price=Decimal("800.00"))
            product_ids.append(prod.id)

            order = _create_order(
                db,
                user,
                [(prod, 1)],
                status=OrderStatus.PAID,
                total_amount=Decimal("800.00"),
                created_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
            order_ids.append(order.id)
            db.commit()

            result = _sales(db, "30d")
            rev_after = result["summary"]["revenue"]["current"]
            cnt_after = result["summary"]["orders"]["current"]
            aov_after = result["summary"]["aov"]["current"]

            expected_aov = rev_after / cnt_after
            assert abs(aov_after - expected_aov) < 0.01, (
                f"AOV={aov_after:.2f} does not match computed rev/count={expected_aov:.2f}"
            )

        finally:
            _cleanup(db, order_ids, product_ids, user_ids, category_ids)
            db.close()
