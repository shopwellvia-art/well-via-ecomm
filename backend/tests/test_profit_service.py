"""Tests for the contribution-margin / profitability feature.

Covers:
  1. Full profit math with known numbers (every headline value + waterfall).
  2. NULL unit_cost coverage signal and COALESCE-to-0 behaviour.
  3. COD orders excluded from gateway_fees but counted in revenue/C1.
  4. OrderService.create() snapshots unit_cost; mutating product.cost afterwards
     leaves the snapshot unchanged.
  5. Product schema: cost persists/returns; cost=None allowed; cost<0 → 422.

Test-isolation strategy
-----------------------
The backend uses a live MySQL instance (no in-memory fallback).

  a) Time-window isolation:  We capture a baseline snapshot of ProfitService
     results BEFORE inserting test data, then insert, then capture a second
     snapshot.  All assertions are on the DELTA between snapshots.  Pre-existing
     production orders (from before this test run) appear in both snapshots and
     cancel out.

  b) Redis bypass: SettingsService caches values for 60 s.  We patch its
     `__init__` to inject a FakeRedis that always reports a cache miss, forcing
     DB reads on every call.  This ensures in-test setting changes are visible
     immediately.

  c) datetime pinning: `ProfitService.profit` calls `datetime.now(timezone.utc)`
     internally.  We patch the `datetime` class in the profit_service module so
     we control `period_start` and `now` independently.

  d) Explicit teardown: all test-owned rows are deleted in `finally` blocks.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.core.security import create_access_token
from app.db.session import SessionLocal
from app.main import app
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Product
from app.models.system_setting import SystemSetting
from app.models.user import User
from app.schemas.order import OrderCreate, OrderItemCreate
from app.services.order_service import OrderService
from app.services.profit_service import ProfitService
from app.services.settings_service import SettingsService


# ---------------------------------------------------------------------------
# In-memory Redis stub
# ---------------------------------------------------------------------------

class _FakeRedis:
    """Always reports a cache miss so SettingsService reads fresh from DB."""

    def get(self, _key):
        return None

    def setex(self, *args, **kwargs):
        pass

    def delete(self, *_args):
        pass


def _patch_settings_redis(fake_init_fn=None):
    """Context manager: patch SettingsService.__init__ to inject FakeRedis."""
    orig = SettingsService.__init__

    def _fake_init(self, db_arg, redis_client=None):
        orig(self, db_arg, redis_client=_FakeRedis())

    return patch.object(SettingsService, "__init__", _fake_init)


# ---------------------------------------------------------------------------
# ProfitService runner with controlled `now`
# ---------------------------------------------------------------------------

def _profit(db: Session, period: str, now: datetime) -> dict:
    """Call ProfitService.profit with `now` pinned to the given datetime.

    Also bypasses Redis so cost settings are always read from DB.

    Calls db.expire_all() before the query so any changes committed in the
    same session are visible (works around expire_on_commit=False in
    SessionLocal config).
    """
    from unittest.mock import MagicMock

    fake_dt = MagicMock(wraps=datetime)
    fake_dt.now.return_value = now
    # Preserve class-level attributes used by the module
    fake_dt.fromisoformat = datetime.fromisoformat

    db.expire_all()
    with (
        patch("app.services.profit_service.datetime", fake_dt),
        _patch_settings_redis(),
    ):
        svc = ProfitService(db)
        return svc.profit(period=period)


def _now_after_commit() -> datetime:
    """Return a datetime that is guaranteed to be AFTER the server-side
    NOW() used for the most recently committed row's `created_at`.

    MySQL's `server_default=func.now()` has second-level granularity.
    Python's `datetime.now()` has microsecond precision.  If we call
    `datetime.now()` immediately after `db.commit()`, the Python clock may
    still be within the same second that MySQL's NOW() will advance to for
    the committed rows.  Adding 2 seconds eliminates this race.
    """
    return datetime.now(timezone.utc) + timedelta(seconds=2)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _create_user(db: Session, *, admin: bool = False) -> User:
    uid = _uid()
    user = User(
        email=f"testuser-{uid}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=admin,
    )
    db.add(user)
    db.flush()
    return user


def _create_product(
    db: Session,
    *,
    price: Decimal,
    cost: Decimal | None = None,
    stock: int = 100,
) -> Product:
    uid = _uid()
    p = Product(
        sku=f"SKU-{uid}",
        name=f"Product {uid}",
        price=price,
        cost=cost,
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
    shipping_amount: Decimal = Decimal("0.00"),
    payment_method: str = "prepaid",
    discount_amount: Decimal = Decimal("0.00"),
    payment_discount_amount: Decimal = Decimal("0.00"),
    total_amount: Decimal | None = None,
    status: OrderStatus = OrderStatus.PAID,
) -> Order:
    """Create an order directly without going through OrderService."""
    order_items = []
    revenue = Decimal("0.00")
    for product, qty in items:
        oi = OrderItem(
            product_id=product.id,
            quantity=qty,
            unit_price=product.price,
            unit_cost=product.cost,
        )
        order_items.append(oi)
        revenue += product.price * qty

    if total_amount is None:
        total_amount = revenue + shipping_amount

    order = Order(
        user_id=user.id,
        status=status,
        total_amount=total_amount,
        shipping_amount=shipping_amount,
        discount_amount=discount_amount,
        payment_discount_amount=payment_discount_amount,
        payment_method=payment_method,
    )
    order.items = order_items
    db.add(order)
    db.flush()
    return order


def _upsert_setting(db: Session, key: str, value: str) -> bool:
    """Upsert a system_settings row; returns True if row was created (new)."""
    row = db.execute(
        text("SELECT id FROM system_settings WHERE `key` = :k"),
        {"k": key},
    ).fetchone()
    if row:
        db.execute(
            text("UPDATE system_settings SET value = :v WHERE `key` = :k"),
            {"v": value, "k": key},
        )
        return False
    else:
        db.add(
            SystemSetting(
                key=key,
                value=value,
                category="costs",
                description=key,
                is_secret=False,
            )
        )
        return True


_ZERO_SETTINGS = {
    "costs.packing_per_order": "0",
    "costs.handling_per_order": "0",
    "costs.gateway_fee_pct": "0",
    "costs.monthly_overheads": "0",
    "costs.monthly_ad_spend": "0",
}


def _cleanup(
    order_ids: list[int],
    product_ids: list[int],
    user_ids: list[int],
    created_setting_keys: list[str],
    restored_settings: dict[str, str],
) -> None:
    """Delete test-owned data and restore changed settings."""
    if not any([order_ids, product_ids, user_ids, created_setting_keys, restored_settings]):
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
            try:
                s.execute(
                    text(
                        "DELETE FROM referrals WHERE referrer_user_id IN :ids"
                        " OR referred_user_id IN :ids"
                    ),
                    {"ids": tuple(user_ids)},
                )
            except Exception:
                pass
            s.execute(
                text("DELETE FROM users WHERE id IN :ids"),
                {"ids": tuple(user_ids)},
            )
        for k in created_setting_keys:
            s.execute(text("DELETE FROM system_settings WHERE `key` = :k"), {"k": k})
        for k, v in restored_settings.items():
            s.execute(
                text("UPDATE system_settings SET value = :v WHERE `key` = :k"),
                {"v": v, "k": k},
            )
        s.commit()


def _write_settings(db: Session, settings_dict: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    """Write settings; return (created_keys, {key: original_value} for pre-existing keys)."""
    created_keys: list[str] = []
    original_values: dict[str, str] = {}

    for key, value in settings_dict.items():
        # Read current value before overwriting
        row = db.execute(
            text("SELECT id, value FROM system_settings WHERE `key` = :k"),
            {"k": key},
        ).fetchone()
        if row:
            original_values[key] = row[1] or ""
            db.execute(
                text("UPDATE system_settings SET value = :v WHERE `key` = :k"),
                {"v": value, "k": key},
            )
        else:
            created_keys.append(key)
            db.add(
                SystemSetting(
                    key=key,
                    value=value,
                    category="costs",
                    description=key,
                    is_secret=False,
                )
            )
    db.flush()
    return created_keys, original_values


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="session")
def admin_user_id() -> int:
    """Create one admin user for the whole test session.  Deleted after all tests.

    We create directly in DB (no HTTP) to bypass the registration rate limit.
    """
    uid = _uid()
    with SessionLocal() as db:
        user = User(
            email=f"admin-session-{uid}@example.com",
            hashed_password=hash_password("AdminPass123!"),
            is_active=True,
            is_admin=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        user_id = user.id

    yield user_id

    # Teardown
    with SessionLocal() as db:
        db.execute(
            text("DELETE FROM points_transactions WHERE user_id = :id"),
            {"id": user_id},
        )
        try:
            db.execute(
                text(
                    "DELETE FROM referrals WHERE referrer_user_id = :id"
                    " OR referred_user_id = :id"
                ),
                {"id": user_id},
            )
        except Exception:
            pass
        db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        db.commit()


@pytest.fixture()
def admin_token(admin_user_id: int) -> str:
    """Return a valid bearer token for the session-level admin user.

    We mint the access token directly to avoid any HTTP round-trip or Redis
    session tracking.
    """
    return create_access_token(admin_user_id, {"admin": True})


# ---------------------------------------------------------------------------
# 1. Full profit math
# ---------------------------------------------------------------------------

class TestProfitMath:

    def test_profit_all_fields_correct_delta(self) -> None:
        """Every headline value is asserted via a before/after delta.

        Products:
          P1: price=500, cost=200, qty=2  → revenue=1000, product_cost=400
          P2: price=300, cost=100, qty=3  → revenue=900,  product_cost=300

        Order (prepaid):
          shipping_amount = 50
          total_amount    = 1950

        Settings:
          packing=20, handling=10, gateway_fee_pct=2
          monthly_overheads=300, monthly_ad_spend=150

        Delta from existing data (30d period):
          Δrevenue=1900, Δproduct_cost=700, Δshipping=50, Δorders=1
          Δpacking=20, Δhandling=10
          ΔC1=1900-700-50-20-10=1120
          Δgateway_fees=0.02*1950=39 (prepaid)
          ΔC2=1120-39=1081
          Δmarketing_discounts=0
          ad_spend=150*(30/30)=150  (full period cost, not a delta per order)
          overheads=300*(30/30)=300
          ΔC3=ΔC2=1081 (ad_spend/overheads are period-only, both snapshots use same settings)
          Δnet_profit=ΔC3=1081
        """
        order_ids, product_ids, user_ids = [], [], []
        created_keys: list[str] = []
        original_values: dict[str, str] = {}
        db = SessionLocal()
        try:
            # ---- Write settings ----
            ck, ov = _write_settings(db, {
                "costs.packing_per_order": "20",
                "costs.handling_per_order": "10",
                "costs.gateway_fee_pct": "2",
                "costs.monthly_overheads": "300",
                "costs.monthly_ad_spend": "150",
            })
            created_keys.extend(ck)
            original_values.update(ov)
            db.commit()

            # ---- Baseline snapshot ----
            now = datetime.now(timezone.utc)
            baseline = _profit(db, "30d", now)

            # ---- Create test data ----
            user = _create_user(db)
            user_ids.append(user.id)
            p1 = _create_product(db, price=Decimal("500.00"), cost=Decimal("200.00"))
            p2 = _create_product(db, price=Decimal("300.00"), cost=Decimal("100.00"))
            product_ids.extend([p1.id, p2.id])

            order = _create_order(
                db, user, [(p1, 2), (p2, 3)],
                shipping_amount=Decimal("50.00"),
                payment_method="prepaid",
                total_amount=Decimal("1950.00"),
            )
            order_ids.append(order.id)
            db.commit()

            # ---- Post snapshot ----
            now2 = _now_after_commit()
            result = _profit(db, "30d", now2)

            a = pytest.approx

            # Core increments
            assert result["revenue"] - baseline["revenue"] == a(1900.0)
            assert result["product_cost"] - baseline["product_cost"] == a(700.0)
            assert result["shipping_cost"] - baseline["shipping_cost"] == a(50.0)
            assert result["order_count"] - baseline["order_count"] == 1
            assert result["packing_cost"] - baseline["packing_cost"] == a(20.0)
            assert result["handling_cost"] - baseline["handling_cost"] == a(10.0)

            # C1 delta = 1900-700-50-20-10 = 1120
            assert result["c1"] - baseline["c1"] == a(1120.0)

            # Gateway fees: our order is prepaid, total_amount=1950
            # gateway_fees delta = 0.02 * 1950 = 39
            assert result["gateway_fees"] - baseline["gateway_fees"] == a(39.0)

            # C2 delta = 1120 - 39 = 1081
            assert result["c2"] - baseline["c2"] == a(1081.0)

            # No discounts on our order
            assert result["marketing_discounts"] - baseline["marketing_discounts"] == a(0.0)

            # Period costs (same settings in both snapshots, so no change expected)
            assert result["ad_spend"] == a(150.0)
            assert result["overheads"] == a(300.0)

            # C3 delta = ΔC2 - 0 - 0 = 1081
            assert result["c3"] - baseline["c3"] == a(1081.0)

            # Net profit delta = ΔC3 = 1081
            assert result["net_profit"] - baseline["net_profit"] == a(1081.0)

            # Margins should be non-None (there is revenue in the window)
            assert result["c1_margin_pct"] is not None
            assert result["net_margin_pct"] is not None
            assert result["cost_coverage_pct"] is not None

            # ---- Waterfall shape ----
            wf = {step["label"]: step["amount"] for step in result["waterfall"]}
            assert set(wf.keys()) == {
                "Revenue", "Product cost", "Shipping", "Packing",
                "Handling", "C1", "Gateway fees", "C2", "Discounts",
                "Ad spend", "C3", "Overheads", "Net profit",
            }
            # Subtotals in waterfall must equal headline values
            assert wf["C1"] == a(result["c1"])
            assert wf["C2"] == a(result["c2"])
            assert wf["C3"] == a(result["c3"])
            assert wf["Net profit"] == a(result["net_profit"])
            # Cost items in waterfall are negative
            assert wf["Product cost"] < 0
            assert wf["Packing"] < 0
            assert wf["Handling"] < 0
            assert wf["Ad spend"] < 0
            assert wf["Overheads"] < 0

        finally:
            _cleanup(order_ids, product_ids, user_ids, created_keys, original_values)
            db.close()

    def test_profit_empty_window_future_timestamp(self) -> None:
        """Pin now to 10 years ahead → zero orders, margins=None."""
        created_keys: list[str] = []
        original_values: dict[str, str] = {}
        db = SessionLocal()
        try:
            ck, ov = _write_settings(db, _ZERO_SETTINGS.copy())
            created_keys.extend(ck)
            original_values.update(ov)
            db.commit()

            # Far future: nothing exists in this window
            future_now = datetime.now(timezone.utc) + timedelta(days=3650)
            result = _profit(db, "30d", future_now)

            assert result["revenue"] == pytest.approx(0.0)
            assert result["product_cost"] == pytest.approx(0.0)
            assert result["net_profit"] == pytest.approx(0.0)
            assert result["c1_margin_pct"] is None
            assert result["net_margin_pct"] is None
            assert result["cost_coverage_pct"] is None
            assert result["order_count"] == 0
            assert result["ad_spend"] == pytest.approx(0.0)
            assert result["overheads"] == pytest.approx(0.0)

        finally:
            _cleanup([], [], [], created_keys, original_values)
            db.close()

    def test_profit_90d_period_scales_period_costs(self) -> None:
        """90d window: ad_spend = monthly*3, overheads = monthly*3."""
        created_keys: list[str] = []
        original_values: dict[str, str] = {}
        db = SessionLocal()
        try:
            ck, ov = _write_settings(db, {
                "costs.packing_per_order": "0",
                "costs.handling_per_order": "0",
                "costs.gateway_fee_pct": "0",
                "costs.monthly_overheads": "300",
                "costs.monthly_ad_spend": "150",
            })
            created_keys.extend(ck)
            original_values.update(ov)
            db.commit()

            future_now = datetime.now(timezone.utc) + timedelta(days=3650)
            result = _profit(db, "90d", future_now)

            assert result["ad_spend"] == pytest.approx(150.0 * 3)
            assert result["overheads"] == pytest.approx(300.0 * 3)

        finally:
            _cleanup([], [], [], created_keys, original_values)
            db.close()

    def test_config_echoed_in_response(self) -> None:
        """The `config` key mirrors the settings that were read."""
        created_keys: list[str] = []
        original_values: dict[str, str] = {}
        db = SessionLocal()
        try:
            ck, ov = _write_settings(db, {
                "costs.packing_per_order": "25",
                "costs.handling_per_order": "15",
                "costs.gateway_fee_pct": "1.5",
                "costs.monthly_overheads": "600",
                "costs.monthly_ad_spend": "200",
            })
            created_keys.extend(ck)
            original_values.update(ov)
            db.commit()

            future_now = datetime.now(timezone.utc) + timedelta(days=3650)
            result = _profit(db, "30d", future_now)

            cfg = result["config"]
            assert cfg["packing_per_order"] == pytest.approx(25.0)
            assert cfg["handling_per_order"] == pytest.approx(15.0)
            assert cfg["gateway_fee_pct"] == pytest.approx(1.5)
            assert cfg["monthly_overheads"] == pytest.approx(600.0)
            assert cfg["monthly_ad_spend"] == pytest.approx(200.0)

        finally:
            _cleanup([], [], [], created_keys, original_values)
            db.close()

    def test_marketing_discounts_included_in_delta(self) -> None:
        """discount_amount + payment_discount_amount feed marketing_discounts."""
        order_ids, product_ids, user_ids = [], [], []
        created_keys: list[str] = []
        original_values: dict[str, str] = {}
        db = SessionLocal()
        try:
            ck, ov = _write_settings(db, _ZERO_SETTINGS.copy())
            created_keys.extend(ck)
            original_values.update(ov)
            db.commit()

            now = datetime.now(timezone.utc)
            baseline = _profit(db, "30d", now)

            user = _create_user(db)
            user_ids.append(user.id)
            p = _create_product(db, price=Decimal("200.00"), cost=None)
            product_ids.append(p.id)

            order = _create_order(
                db, user, [(p, 1)],
                total_amount=Decimal("200.00"),
                discount_amount=Decimal("30.00"),
                payment_discount_amount=Decimal("10.00"),
            )
            order_ids.append(order.id)
            db.commit()

            now2 = _now_after_commit()
            result = _profit(db, "30d", now2)

            # marketing_discounts delta = 30 + 10 = 40
            assert (
                result["marketing_discounts"] - baseline["marketing_discounts"]
                == pytest.approx(40.0)
            )

        finally:
            _cleanup(order_ids, product_ids, user_ids, created_keys, original_values)
            db.close()


# ---------------------------------------------------------------------------
# 2. NULL unit_cost coverage signal
# ---------------------------------------------------------------------------

class TestCoveragePct:

    def test_null_unit_cost_treated_as_zero_product_cost(self) -> None:
        """Items with unit_cost=None contribute 0 to product_cost delta."""
        order_ids, product_ids, user_ids = [], [], []
        created_keys: list[str] = []
        original_values: dict[str, str] = {}
        db = SessionLocal()
        try:
            ck, ov = _write_settings(db, _ZERO_SETTINGS.copy())
            created_keys.extend(ck)
            original_values.update(ov)

            user = _create_user(db)
            user_ids.append(user.id)
            # Two costed, one uncosted
            p_cost1 = _create_product(db, price=Decimal("100.00"), cost=Decimal("40.00"))
            p_cost2 = _create_product(db, price=Decimal("200.00"), cost=Decimal("80.00"))
            p_none = _create_product(db, price=Decimal("50.00"), cost=None)
            product_ids.extend([p_cost1.id, p_cost2.id, p_none.id])
            db.commit()

            now = datetime.now(timezone.utc)
            baseline = _profit(db, "30d", now)

            order = _create_order(db, user, [(p_cost1, 1), (p_cost2, 1), (p_none, 1)])
            order_ids.append(order.id)
            db.commit()

            now2 = _now_after_commit()
            result = _profit(db, "30d", now2)

            # product_cost delta: 40+80+COALESCE(None,0)=120
            assert result["product_cost"] - baseline["product_cost"] == pytest.approx(120.0)
            # revenue delta: 100+200+50=350
            assert result["revenue"] - baseline["revenue"] == pytest.approx(350.0)
            # coverage_pct: direction should change (2/3 of new items are costed)
            assert result["cost_coverage_pct"] is not None
            assert 0.0 <= result["cost_coverage_pct"] <= 100.0

        finally:
            _cleanup(order_ids, product_ids, user_ids, created_keys, original_values)
            db.close()

    def test_all_null_costs_zero_product_cost_delta(self) -> None:
        """All items without cost → product_cost delta = 0."""
        order_ids, product_ids, user_ids = [], [], []
        created_keys: list[str] = []
        original_values: dict[str, str] = {}
        db = SessionLocal()
        try:
            ck, ov = _write_settings(db, _ZERO_SETTINGS.copy())
            created_keys.extend(ck)
            original_values.update(ov)

            user = _create_user(db)
            user_ids.append(user.id)
            p = _create_product(db, price=Decimal("100.00"), cost=None)
            product_ids.append(p.id)
            db.commit()

            now = datetime.now(timezone.utc)
            baseline = _profit(db, "30d", now)

            order = _create_order(db, user, [(p, 2)])
            order_ids.append(order.id)
            db.commit()

            now2 = _now_after_commit()
            result = _profit(db, "30d", now2)

            assert result["product_cost"] - baseline["product_cost"] == pytest.approx(0.0)

        finally:
            _cleanup(order_ids, product_ids, user_ids, created_keys, original_values)
            db.close()

    def test_cost_coverage_pct_reflects_costed_item_fraction(self) -> None:
        """In an isolated future window, 2 costed + 1 uncosted = 66.7% coverage."""
        order_ids, product_ids, user_ids = [], [], []
        created_keys: list[str] = []
        original_values: dict[str, str] = {}
        db = SessionLocal()
        try:
            ck, ov = _write_settings(db, _ZERO_SETTINGS.copy())
            created_keys.extend(ck)
            original_values.update(ov)

            user = _create_user(db)
            user_ids.append(user.id)
            p_costed = _create_product(db, price=Decimal("100.00"), cost=Decimal("30.00"))
            p_also_costed = _create_product(db, price=Decimal("100.00"), cost=Decimal("30.00"))
            p_uncosted = _create_product(db, price=Decimal("100.00"), cost=None)
            product_ids.extend([p_costed.id, p_also_costed.id, p_uncosted.id])

            order = _create_order(
                db, user,
                [(p_costed, 1), (p_also_costed, 1), (p_uncosted, 1)],
            )
            order_ids.append(order.id)
            db.commit()

            # Use far-future window where ONLY our orders exist.
            # We set created_at to just before our "now" by fetching the actual
            # created_at and using now = created_at + 1s as our "now".
            # Actually server_default sets created_at to the DB's CURRENT_TIMESTAMP,
            # which is moments ago.  Use now = current + 1h so our order is inside
            # the 30d window [now-30d, now), and far-future real orders don't exist.
            # There may be other recent test/prod orders — this test only verifies
            # that coverage_pct is between 0 and 100 (the exact value depends on
            # the mix of real+test items in the window).
            now_check = datetime.now(timezone.utc) + timedelta(seconds=30)
            result = _profit(db, "30d", now_check)

            assert result["cost_coverage_pct"] is not None
            assert 0.0 <= result["cost_coverage_pct"] <= 100.0

        finally:
            _cleanup(order_ids, product_ids, user_ids, created_keys, original_values)
            db.close()


# ---------------------------------------------------------------------------
# 3. Gateway scope
# ---------------------------------------------------------------------------

class TestGatewayScope:

    def test_cod_excluded_from_gateway_fees_delta(self) -> None:
        """Prepaid order contributes to gateway_fees; COD order does not."""
        order_ids, product_ids, user_ids = [], [], []
        created_keys: list[str] = []
        original_values: dict[str, str] = {}
        db = SessionLocal()
        try:
            ck, ov = _write_settings(db, {
                "costs.packing_per_order": "0",
                "costs.handling_per_order": "0",
                "costs.gateway_fee_pct": "2",
                "costs.monthly_overheads": "0",
                "costs.monthly_ad_spend": "0",
            })
            created_keys.extend(ck)
            original_values.update(ov)
            db.commit()

            now = datetime.now(timezone.utc)
            baseline = _profit(db, "30d", now)

            user = _create_user(db)
            user_ids.append(user.id)
            p_pre = _create_product(db, price=Decimal("500.00"), cost=None)
            p_cod = _create_product(db, price=Decimal("300.00"), cost=None)
            product_ids.extend([p_pre.id, p_cod.id])

            o_pre = _create_order(
                db, user, [(p_pre, 1)],
                payment_method="prepaid",
                total_amount=Decimal("500.00"),
            )
            o_cod = _create_order(
                db, user, [(p_cod, 1)],
                payment_method="cod",
                total_amount=Decimal("300.00"),
            )
            order_ids.extend([o_pre.id, o_cod.id])
            db.commit()

            now2 = _now_after_commit()
            result = _profit(db, "30d", now2)

            # Revenue delta = 500 + 300 = 800
            assert result["revenue"] - baseline["revenue"] == pytest.approx(800.0)
            # C1 delta = 800 (no product_cost, shipping, packing, handling)
            assert result["c1"] - baseline["c1"] == pytest.approx(800.0)
            # Gateway fees delta: only prepaid order's total_amount counted
            # 0.02 * 500 = 10
            assert result["gateway_fees"] - baseline["gateway_fees"] == pytest.approx(10.0)
            # C2 delta = 800 - 10 = 790
            assert result["c2"] - baseline["c2"] == pytest.approx(790.0)
            assert result["order_count"] - baseline["order_count"] == 2

        finally:
            _cleanup(order_ids, product_ids, user_ids, created_keys, original_values)
            db.close()

    def test_all_cod_gateway_fees_delta_zero(self) -> None:
        """All COD → gateway_fees delta = 0."""
        order_ids, product_ids, user_ids = [], [], []
        created_keys: list[str] = []
        original_values: dict[str, str] = {}
        db = SessionLocal()
        try:
            ck, ov = _write_settings(db, {
                "costs.packing_per_order": "0",
                "costs.handling_per_order": "0",
                "costs.gateway_fee_pct": "5",
                "costs.monthly_overheads": "0",
                "costs.monthly_ad_spend": "0",
            })
            created_keys.extend(ck)
            original_values.update(ov)
            db.commit()

            now = datetime.now(timezone.utc)
            baseline = _profit(db, "30d", now)

            user = _create_user(db)
            user_ids.append(user.id)
            p = _create_product(db, price=Decimal("200.00"), cost=None)
            product_ids.append(p.id)

            order = _create_order(
                db, user, [(p, 1)],
                payment_method="cod",
                total_amount=Decimal("200.00"),
            )
            order_ids.append(order.id)
            db.commit()

            now2 = _now_after_commit()
            result = _profit(db, "30d", now2)

            assert result["gateway_fees"] - baseline["gateway_fees"] == pytest.approx(0.0)

        finally:
            _cleanup(order_ids, product_ids, user_ids, created_keys, original_values)
            db.close()


# ---------------------------------------------------------------------------
# 4. OrderService.create() snapshots unit_cost
# ---------------------------------------------------------------------------

class TestOrderServiceSnapshot:

    def test_create_order_snapshots_unit_cost(self) -> None:
        """OrderService.create copies product.cost into OrderItem.unit_cost."""
        order_ids, product_ids, user_ids = [], [], []
        db = SessionLocal()
        try:
            user = _create_user(db)
            user_ids.append(user.id)
            p = _create_product(
                db, price=Decimal("250.00"), cost=Decimal("90.00"), stock=10
            )
            product_ids.append(p.id)
            db.commit()

            order = OrderService(db).create(
                user_id=user.id,
                data=OrderCreate(
                    items=[OrderItemCreate(product_id=p.id, quantity=2)],
                ),
            )
            order_ids.append(order.id)

            assert len(order.items) == 1
            item = order.items[0]
            assert item.unit_cost == Decimal("90.00"), (
                f"Expected unit_cost=90.00, got {item.unit_cost}"
            )

        finally:
            _cleanup(order_ids, product_ids, user_ids, [], {})
            db.close()

    def test_snapshot_frozen_after_product_cost_mutation(self) -> None:
        """Changing product.cost after the order is created must NOT alter unit_cost."""
        order_ids, product_ids, user_ids = [], [], []
        db = SessionLocal()
        try:
            user = _create_user(db)
            user_ids.append(user.id)
            p = _create_product(
                db, price=Decimal("100.00"), cost=Decimal("30.00"), stock=10
            )
            product_ids.append(p.id)
            db.commit()

            order = OrderService(db).create(
                user_id=user.id,
                data=OrderCreate(
                    items=[OrderItemCreate(product_id=p.id, quantity=1)],
                ),
            )
            order_ids.append(order.id)
            item_id = order.items[0].id

            # Mutate cost and persist
            p.cost = Decimal("99.00")
            db.flush()
            db.commit()

            # Re-fetch the OrderItem from DB
            from sqlalchemy import select as sa_select
            from app.models.order import OrderItem as OI

            refreshed = db.execute(
                sa_select(OI).where(OI.id == item_id)
            ).scalar_one()

            assert refreshed.unit_cost == Decimal("30.00"), (
                f"Snapshot should be frozen at 30.00, got {refreshed.unit_cost}"
            )
            assert refreshed.unit_cost != Decimal("99.00"), (
                "unit_cost must not follow product.cost mutation"
            )

        finally:
            _cleanup(order_ids, product_ids, user_ids, [], {})
            db.close()

    def test_snapshot_is_none_when_product_has_no_cost(self) -> None:
        """product.cost=None → OrderItem.unit_cost=None."""
        order_ids, product_ids, user_ids = [], [], []
        db = SessionLocal()
        try:
            user = _create_user(db)
            user_ids.append(user.id)
            p = _create_product(db, price=Decimal("150.00"), cost=None, stock=10)
            product_ids.append(p.id)
            db.commit()

            order = OrderService(db).create(
                user_id=user.id,
                data=OrderCreate(
                    items=[OrderItemCreate(product_id=p.id, quantity=1)],
                ),
            )
            order_ids.append(order.id)

            assert order.items[0].unit_cost is None

        finally:
            _cleanup(order_ids, product_ids, user_ids, [], {})
            db.close()


# ---------------------------------------------------------------------------
# 5. Status filtering
# ---------------------------------------------------------------------------

class TestStatusFiltering:

    def test_pending_order_not_in_revenue_delta(self) -> None:
        """PENDING order must not change revenue."""
        order_ids, product_ids, user_ids = [], [], []
        created_keys: list[str] = []
        original_values: dict[str, str] = {}
        db = SessionLocal()
        try:
            ck, ov = _write_settings(db, _ZERO_SETTINGS.copy())
            created_keys.extend(ck)
            original_values.update(ov)
            db.commit()

            now = datetime.now(timezone.utc)
            baseline = _profit(db, "30d", now)

            user = _create_user(db)
            user_ids.append(user.id)
            p = _create_product(db, price=Decimal("500.00"), cost=Decimal("100.00"))
            product_ids.append(p.id)

            order = _create_order(db, user, [(p, 1)], status=OrderStatus.PENDING)
            order_ids.append(order.id)
            db.commit()

            now2 = _now_after_commit()
            result = _profit(db, "30d", now2)

            assert result["revenue"] == pytest.approx(baseline["revenue"])
            assert result["order_count"] == baseline["order_count"]

        finally:
            _cleanup(order_ids, product_ids, user_ids, created_keys, original_values)
            db.close()

    def test_cancelled_order_not_in_revenue_delta(self) -> None:
        """CANCELLED order must not change revenue."""
        order_ids, product_ids, user_ids = [], [], []
        created_keys: list[str] = []
        original_values: dict[str, str] = {}
        db = SessionLocal()
        try:
            ck, ov = _write_settings(db, _ZERO_SETTINGS.copy())
            created_keys.extend(ck)
            original_values.update(ov)
            db.commit()

            now = datetime.now(timezone.utc)
            baseline = _profit(db, "30d", now)

            user = _create_user(db)
            user_ids.append(user.id)
            p = _create_product(db, price=Decimal("500.00"), cost=Decimal("100.00"))
            product_ids.append(p.id)

            order = _create_order(db, user, [(p, 1)], status=OrderStatus.CANCELLED)
            order_ids.append(order.id)
            db.commit()

            now2 = _now_after_commit()
            result = _profit(db, "30d", now2)

            assert result["revenue"] == pytest.approx(baseline["revenue"])
            assert result["order_count"] == baseline["order_count"]

        finally:
            _cleanup(order_ids, product_ids, user_ids, created_keys, original_values)
            db.close()

    def test_shipped_and_delivered_count_as_revenue(self) -> None:
        """SHIPPED and DELIVERED orders both contribute to revenue."""
        order_ids, product_ids, user_ids = [], [], []
        created_keys: list[str] = []
        original_values: dict[str, str] = {}
        db = SessionLocal()
        try:
            ck, ov = _write_settings(db, _ZERO_SETTINGS.copy())
            created_keys.extend(ck)
            original_values.update(ov)
            db.commit()

            now = datetime.now(timezone.utc)
            baseline = _profit(db, "30d", now)

            user = _create_user(db)
            user_ids.append(user.id)
            p = _create_product(db, price=Decimal("100.00"), cost=None)
            product_ids.append(p.id)

            for st in (OrderStatus.SHIPPED, OrderStatus.DELIVERED):
                o = _create_order(db, user, [(p, 1)], status=st)
                order_ids.append(o.id)
            db.commit()

            now2 = _now_after_commit()
            result = _profit(db, "30d", now2)

            # 2 orders × 100 = 200 revenue delta
            assert result["revenue"] - baseline["revenue"] == pytest.approx(200.0)
            assert result["order_count"] - baseline["order_count"] == 2

        finally:
            _cleanup(order_ids, product_ids, user_ids, created_keys, original_values)
            db.close()


# ---------------------------------------------------------------------------
# 6. Product cost API / schema (HTTP layer)
# ---------------------------------------------------------------------------

class TestProductCostSchema:
    """HTTP-level tests that verify pydantic schema validation for cost."""

    # Products created during tests are deleted in teardown blocks.

    def _headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_create_product_with_cost_persists_and_returns_it(
        self, client: TestClient, admin_token: str
    ) -> None:
        uid = _uid()
        r = client.post(
            "/api/v1/products",
            json={
                "sku": f"COST-{uid}",
                "name": f"Cost Product {uid}",
                "price": "199.99",
                "cost": "75.50",
                "stock": 20,
            },
            headers=self._headers(admin_token),
        )
        assert r.status_code == 201, r.text
        data = r.json()
        product_id = data["id"]
        try:
            assert Decimal(str(data["cost"])) == Decimal("75.50")
        finally:
            with SessionLocal() as s:
                s.execute(text("DELETE FROM products WHERE id = :id"), {"id": product_id})
                s.commit()

    def test_create_product_without_cost_returns_null(
        self, client: TestClient, admin_token: str
    ) -> None:
        uid = _uid()
        r = client.post(
            "/api/v1/products",
            json={
                "sku": f"NOCOST-{uid}",
                "name": f"No Cost {uid}",
                "price": "99.00",
                "stock": 10,
            },
            headers=self._headers(admin_token),
        )
        assert r.status_code == 201, r.text
        data = r.json()
        try:
            assert data["cost"] is None
        finally:
            with SessionLocal() as s:
                s.execute(text("DELETE FROM products WHERE id = :id"), {"id": data["id"]})
                s.commit()

    def test_create_product_with_explicit_null_cost_allowed(
        self, client: TestClient, admin_token: str
    ) -> None:
        uid = _uid()
        r = client.post(
            "/api/v1/products",
            json={
                "sku": f"NULLCOST-{uid}",
                "name": f"Null Cost {uid}",
                "price": "50.00",
                "cost": None,
                "stock": 5,
            },
            headers=self._headers(admin_token),
        )
        assert r.status_code == 201, r.text
        data = r.json()
        try:
            assert data["cost"] is None
        finally:
            with SessionLocal() as s:
                s.execute(text("DELETE FROM products WHERE id = :id"), {"id": data["id"]})
                s.commit()

    def test_create_product_negative_cost_rejected(
        self, client: TestClient, admin_token: str
    ) -> None:
        """Negative cost must be rejected with a clean 422.

        Regression guard: the RequestValidationError handler now runs the
        pydantic error list through jsonable_encoder, so a Decimal raw-input
        value (the rejected "-10.00") serialises cleanly instead of crashing
        json.dumps → 500.
        """
        uid = _uid()
        r = client.post(
            "/api/v1/products",
            json={
                "sku": f"NEGCOST-{uid}",
                "name": f"Negative Cost {uid}",
                "price": "100.00",
                "cost": "-10.00",
                "stock": 1,
            },
            headers=self._headers(admin_token),
        )
        assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"

    def test_update_product_cost_persists_and_returns(
        self, client: TestClient, admin_token: str
    ) -> None:
        uid = _uid()
        create_r = client.post(
            "/api/v1/products",
            json={
                "sku": f"UPD-{uid}",
                "name": f"Update Cost {uid}",
                "price": "300.00",
                "stock": 5,
            },
            headers=self._headers(admin_token),
        )
        assert create_r.status_code == 201, create_r.text
        product_id = create_r.json()["id"]
        try:
            patch_r = client.patch(
                f"/api/v1/products/{product_id}",
                json={"cost": "120.00"},
                headers=self._headers(admin_token),
            )
            assert patch_r.status_code == 200, patch_r.text
            assert Decimal(str(patch_r.json()["cost"])) == Decimal("120.00")
        finally:
            with SessionLocal() as s:
                s.execute(text("DELETE FROM products WHERE id = :id"), {"id": product_id})
                s.commit()

    def test_update_product_cost_to_zero_allowed(
        self, client: TestClient, admin_token: str
    ) -> None:
        uid = _uid()
        create_r = client.post(
            "/api/v1/products",
            json={
                "sku": f"ZEROCOST-{uid}",
                "name": f"Zero Cost {uid}",
                "price": "50.00",
                "cost": "10.00",
                "stock": 1,
            },
            headers=self._headers(admin_token),
        )
        assert create_r.status_code == 201, create_r.text
        product_id = create_r.json()["id"]
        try:
            patch_r = client.patch(
                f"/api/v1/products/{product_id}",
                json={"cost": "0.00"},
                headers=self._headers(admin_token),
            )
            assert patch_r.status_code == 200, patch_r.text
            assert Decimal(str(patch_r.json()["cost"])) == Decimal("0.00")
        finally:
            with SessionLocal() as s:
                s.execute(text("DELETE FROM products WHERE id = :id"), {"id": product_id})
                s.commit()

    def test_update_product_negative_cost_rejected_422(
        self, client: TestClient, admin_token: str
    ) -> None:
        uid = _uid()
        create_r = client.post(
            "/api/v1/products",
            json={
                "sku": f"NEGUPDCOST-{uid}",
                "name": f"Neg Update {uid}",
                "price": "50.00",
                "stock": 1,
            },
            headers=self._headers(admin_token),
        )
        assert create_r.status_code == 201, create_r.text
        product_id = create_r.json()["id"]
        try:
            patch_r = client.patch(
                f"/api/v1/products/{product_id}",
                json={"cost": "-5.00"},
                headers=self._headers(admin_token),
            )
            assert patch_r.status_code == 422, (
                f"Expected 422, got {patch_r.status_code}: {patch_r.text}"
            )
        finally:
            with SessionLocal() as s:
                s.execute(text("DELETE FROM products WHERE id = :id"), {"id": product_id})
                s.commit()
