"""Tests for MarginService — the CM1/CM2/CM3 cascade and the revenue bridge.

The arithmetic tests here matter less than the honesty tests. Getting a margin
number right is table stakes; the reason this service exists is that its
predecessor got two things wrong in the *flattering* direction and said nothing
about it. So the tests that carry the weight are:

  * ``test_missing_unit_cost_is_never_zero_filled`` — a line with no cost
    snapshot must reduce coverage and grade the result INCOMPLETE, and must NOT
    fall out of the sum as free goods (inherited bug 2).
  * ``test_missing_gateway_rule_makes_cm2_none`` — an absent cost rule must
    blank CM2, not quietly report CM1 wearing CM2's label.
  * ``test_shipping_income_is_never_charged_as_a_cost`` — the 2x shipping
    penalty in ``profit_service.py`` (inherited bug 1), pinned by an identity
    that fails if anyone reintroduces it.
  * ``test_revenue_bridge_detects_an_imbalance`` — proof that ``balances()``
    can actually fail, so a passing bridge means something.

Isolation strategy
------------------
Unlike ``test_profit_service.py`` these are **not** baseline/delta tests. The
whole fixture lives in a window in **2007**, years before any row this store
will ever hold, so every assertion is on an absolute figure rather than a
difference — which is what lets the tests say "CM2 is exactly 638.20" instead of
"CM2 moved by roughly the right amount".

Three consequences of the shared throwaway MySQL are handled explicitly:

  a) ``_assert_no_foreign_cost_rules`` fails loudly if any rule not written by
     this module covers the 2007 window. A stray rule would silently change
     every expected figure; a clear failure beats a mystifying one.
  b) ``CostRuleResolver`` caches resolutions in Redis for 60s, **including
     misses**, and these tests deliberately reuse the same dates with and
     without a given rule. Every seed and every teardown calls
     ``invalidate_all()``.
  c) Explicit teardown of every owned row in ``finally``, through a fresh
     session so a half-rolled-back transaction cannot skip it.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.analytics_control import (
    AnalyticsCostRule,
    CostQuality,
    CostScope,
    CostType,
    CostUnit,
)
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Product
from app.models.shipment import Shipment, ShipmentStatus
from app.models.user import User
from app.services.analytics.contracts import to_minor
from app.services.analytics.cost_rules import CostRuleResolver
from app.services.analytics.margin import COGS, SHIPPING_INCOME, MarginService
from app.services.analytics.types import MetricQuality

# ---------------------------------------------------------------------------
# The 2007 sandbox
# ---------------------------------------------------------------------------

WINDOW_FROM = datetime(2007, 3, 1, tzinfo=timezone.utc)
WINDOW_TO = datetime(2007, 3, 11, tzinfo=timezone.utc)  # half-open: 10 days
ORDER_AT = datetime(2007, 3, 5, 10, 30, tzinfo=timezone.utc)

#: A quiet window with nothing in it, for the zero-sales test.
EMPTY_FROM = datetime(2007, 5, 1, tzinfo=timezone.utc)
EMPTY_TO = datetime(2007, 5, 11, tzinfo=timezone.utc)

RULE_FROM = date(2007, 1, 1)
#: Deliberately CLOSED. An open-ended rule would reach into the present and
#: perturb every other suite that reads cost rules.
RULE_TO = date(2007, 12, 31)
RULE_SOURCE = "test_analytics_margin"

#: (cost_type, unit, value). March has 31 days, so the PER_MONTH marketing rule
#: of 310 pro-rates to exactly 10.00/day and 100.00 over the 10-day window — no
#: rounding residue to reason about in the expected figures.
DEFAULT_RULES: tuple[tuple[str, str, Decimal], ...] = (
    (CostType.GATEWAY_FEE, CostUnit.PCT, Decimal("2.0")),
    (CostType.PACKAGING, CostUnit.PER_ORDER, Decimal("15")),
    (CostType.HANDLING, CostUnit.PER_ORDER, Decimal("10")),
    (CostType.FORWARD_SHIPPING, CostUnit.PER_ORDER, Decimal("50")),
    (CostType.RETURN_SHIPPING, CostUnit.PER_ORDER, Decimal("45")),
    (CostType.RTO_LOGISTICS, CostUnit.PER_ORDER, Decimal("90")),
    (CostType.MARKETPLACE_COMMISSION, CostUnit.PCT, Decimal("0")),
    (CostType.MARKETING_SPEND, CostUnit.PER_MONTH, Decimal("310")),
)


def _paise(amount: str | int) -> int:
    return to_minor(Decimal(str(amount)))


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


class _Owned:
    """Ids this test created, so teardown deletes exactly them and nothing else."""

    def __init__(self) -> None:
        self.orders: list[int] = []
        self.products: list[int] = []
        self.users: list[int] = []
        self.rules: list[int] = []
        self.shipments: list[int] = []


def _create_user(db: Session, owned: _Owned) -> User:
    user = User(
        email=f"margintest-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.flush()
    owned.users.append(user.id)
    return user


def _create_product(
    db: Session, owned: _Owned, *, price: str, cost: str | None
) -> Product:
    product = Product(
        sku=f"SKU-MARGIN-{_uid()}",
        name=f"MarginTestProduct {_uid()}",
        price=Decimal(price),
        # None means "never snapshotted" — the case profit_service.py coalesces
        # to 0 and this service must exclude instead.
        cost=None if cost is None else Decimal(cost),
        stock=100,
    )
    db.add(product)
    db.flush()
    owned.products.append(product.id)
    return product


def _create_order(
    db: Session,
    owned: _Owned,
    user: User,
    items: list[tuple[Product, int]],
    *,
    created_at: datetime = ORDER_AT,
    status: OrderStatus = OrderStatus.PAID,
    discount: str = "0",
    payment_discount: str = "0",
    tax: str = "0",
    shipping: str = "0",
    cod_surcharge: str = "0",
    payment_method: str = "prepaid",
    refunded_at: datetime | None = None,
) -> Order:
    """An order whose total_amount satisfies the revenue-bridge identity.

    ``total_amount = gross - discounts + tax + shipping + cod_surcharge``. The
    bridge measures net revenue independently from ``total_amount``, so building
    the fixture this way is what makes ``balances()`` a real assertion about the
    aggregation rather than a restatement of it.
    """
    order_items: list[OrderItem] = []
    gross = Decimal("0.00")
    for product, qty in items:
        order_items.append(
            OrderItem(
                product_id=product.id,
                quantity=qty,
                unit_price=product.price,
                # The snapshot taken at sale. None when the product had no cost.
                unit_cost=product.cost,
            )
        )
        gross += product.price * qty

    discounts = Decimal(discount) + Decimal(payment_discount)
    total = gross - discounts + Decimal(tax) + Decimal(shipping) + Decimal(cod_surcharge)
    is_cod = payment_method != "prepaid"

    order = Order(
        user_id=user.id,
        status=status,
        subtotal=gross,
        tax_amount=Decimal(tax),
        discount_amount=Decimal(discount),
        payment_discount_amount=Decimal(payment_discount),
        shipping_amount=Decimal(shipping),
        cod_surcharge_amount=Decimal(cod_surcharge),
        # cod_balance is what the courier collects in cash; it is what makes the
        # gateway-fee base exact for prepaid, COD and split COD alike.
        cod_balance=total if is_cod else Decimal("0.00"),
        total_amount=total,
        currency="INR",
        payment_method=payment_method,
        created_at=created_at,
        refunded_at=refunded_at,
    )
    order.items = order_items
    db.add(order)
    db.flush()
    owned.orders.append(order.id)
    return order


def _create_shipment(
    db: Session, owned: _Owned, order: Order, *, cost: str | None
) -> Shipment:
    shipment = Shipment(
        order_id=order.id,
        courier_partner="testcourier",
        shipment_status=ShipmentStatus.DELIVERED,
        shipment_cost=None if cost is None else Decimal(cost),
    )
    db.add(shipment)
    db.flush()
    owned.shipments.append(shipment.id)
    return shipment


def _seed_cost_rules(
    db: Session,
    owned: _Owned,
    *,
    skip: frozenset[str] = frozenset(),
    override: dict[str, Decimal] | None = None,
) -> None:
    """Write the eight rules the cascade needs, minus anything in ``skip``.

    A rule whose ``value`` is 0 is a real measurement ("packaging genuinely
    costs us nothing"), which is categorically different from ``skip``ping it —
    that is the absence of any rule at all. Several tests below depend on the
    distinction.
    """
    for cost_type, unit, value in DEFAULT_RULES:
        if cost_type in skip:
            continue
        db.add(
            AnalyticsCostRule(
                cost_type=cost_type,
                scope=CostScope.GLOBAL,
                scope_value="-",
                value=(override or {}).get(cost_type, value),
                unit=unit,
                currency="INR",
                quality=CostQuality.CONTRACTED,
                effective_from=RULE_FROM,
                effective_to=RULE_TO,
                source=RULE_SOURCE,
                note="test fixture",
            )
        )
    db.flush()
    owned.rules.extend(
        r.id
        for r in db.execute(
            select(AnalyticsCostRule).where(AnalyticsCostRule.source == RULE_SOURCE)
        ).scalars()
    )
    db.commit()
    # The resolver caches misses as well as hits for 60s, and these tests reuse
    # the same dates with and without a given rule.
    CostRuleResolver(db).invalidate_all()


def _assert_no_foreign_cost_rules(db: Session) -> None:
    """Fail loudly if a rule this module did not write covers the 2007 window.

    Every expected figure below is absolute, so a stray rule would not make the
    tests noisy — it would make them wrong. This turns that into a clear failure.
    """
    rows = (
        db.execute(
            select(AnalyticsCostRule).where(
                AnalyticsCostRule.effective_from <= RULE_TO,
                or_(
                    AnalyticsCostRule.effective_to.is_(None),
                    AnalyticsCostRule.effective_to >= RULE_FROM,
                ),
            )
        )
        .scalars()
        .all()
    )
    foreign = [r for r in rows if (r.source or "") != RULE_SOURCE]
    if foreign:
        pytest.fail(
            "cost rules not owned by this test cover the 2007 fixture window and "
            "would change every expected figure: "
            + ", ".join(f"#{r.id} {r.cost_type} from {r.effective_from}" for r in foreign)
        )


def _cleanup(owned: _Owned) -> None:
    with SessionLocal() as s:
        if owned.shipments:
            s.execute(
                text("DELETE FROM shipments WHERE id IN :ids"),
                {"ids": tuple(owned.shipments)},
            )
        if owned.orders:
            s.execute(
                text("DELETE FROM order_items WHERE order_id IN :ids"),
                {"ids": tuple(owned.orders)},
            )
            s.execute(
                text("DELETE FROM shipments WHERE order_id IN :ids"),
                {"ids": tuple(owned.orders)},
            )
            s.execute(
                text("DELETE FROM orders WHERE id IN :ids"),
                {"ids": tuple(owned.orders)},
            )
        if owned.products:
            s.execute(
                text("DELETE FROM products WHERE id IN :ids"),
                {"ids": tuple(owned.products)},
            )
        if owned.users:
            s.execute(
                text("DELETE FROM points_transactions WHERE user_id IN :ids"),
                {"ids": tuple(owned.users)},
            )
            s.execute(
                text("DELETE FROM users WHERE id IN :ids"),
                {"ids": tuple(owned.users)},
            )
        # Delete by source, not by id: a rule inserted then rolled back still
        # leaves nothing, but a rule inserted by a previous *failed* run of this
        # module would otherwise poison every later run.
        s.execute(
            text("DELETE FROM analytics_cost_rules WHERE source = :src"),
            {"src": RULE_SOURCE},
        )
        s.commit()
        CostRuleResolver(s).invalidate_all()


def _component(result, cost_type: str):
    return next(c for c in result.components if c.cost_type == cost_type)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestContributionMarginCascade:
    """CM1 > CM2 > CM3 with every input present, and the honesty rules."""

    def test_full_cascade_with_every_cost_configured(self) -> None:
        """All eight rules present + full cost coverage: exact figures, and a
        quality that is graded but not INCOMPLETE."""
        owned = _Owned()
        db = SessionLocal()
        try:
            _assert_no_foreign_cost_rules(db)
            user = _create_user(db, owned)
            a = _create_product(db, owned, price="500.00", cost="200.00")
            b = _create_product(db, owned, price="300.00", cost="100.00")
            order = _create_order(
                db, owned, user, [(a, 2), (b, 1)],
                discount="100.00", payment_discount="30.00",
                tax="90.00", shipping="80.00",
            )
            # A real carrier charge: forward shipping comes from this, not a rule.
            _create_shipment(db, owned, order, cost="60.00")
            db.commit()
            _seed_cost_rules(db, owned)

            result = MarginService(db).compute(WINDOW_FROM, WINDOW_TO)

            # gross 1300 - (100 + 30) discounts
            assert result.net_merchandise_sales_minor == _paise("1170.00")
            assert result.cogs_minor == _paise("500.00")
            assert result.cost_coverage_pct == Decimal("100.00")

            # CM1 = 1170 - 500
            assert result.cm1_minor == _paise("670.00")
            # CM2 = 670 + 80 shipping income - 26.80 gateway (2% of 1340 prepaid)
            #       - 15 packaging - 10 handling - 60 real carrier cost
            #       - 0 returns - 0 RTO - 0 marketplace
            assert result.cm2_minor == _paise("638.20")
            # CM3 = 638.20 - 100 marketing (310/month x 10 of March's 31 days)
            assert result.cm3_minor == _paise("538.20")

            assert result.cm1_minor > result.cm2_minor > result.cm3_minor
            assert result.missing_inputs == ()
            assert result.quality is not MetricQuality.INCOMPLETE
            assert result.quality is MetricQuality.ESTIMATED  # contracted rules

            # Forward shipping is the observed charge, not the 50/order rule.
            forward = _component(result, CostType.FORWARD_SHIPPING)
            assert forward.value_minor == _paise("60.00")
            assert forward.source == "shipments.shipment_cost"
            assert forward.quality is MetricQuality.ACTUAL

            assert result.pct(result.cm1_minor) == Decimal("57.26")
        finally:
            db.close()
            _cleanup(owned)

    def test_missing_unit_cost_is_never_zero_filled(self) -> None:
        """Inherited bug 2. A line with no cost snapshot is EXCLUDED from COGS
        and drops coverage; it is never counted as free goods."""
        owned = _Owned()
        db = SessionLocal()
        try:
            _assert_no_foreign_cost_rules(db)
            user = _create_user(db, owned)
            costed = _create_product(db, owned, price="500.00", cost="200.00")
            uncosted = _create_product(db, owned, price="300.00", cost=None)
            order = _create_order(
                db, owned, user, [(costed, 2), (uncosted, 1)],
                discount="100.00", payment_discount="30.00",
                tax="90.00", shipping="80.00",
            )
            _create_shipment(db, owned, order, cost="60.00")
            db.commit()
            _seed_cost_rules(db, owned)

            result = MarginService(db).compute(WINDOW_FROM, WINDOW_TO)

            # One of two lines has a cost.
            assert result.cost_coverage_pct == Decimal("50.00")
            assert result.cost_coverage_pct < Decimal("100")
            assert result.quality is MetricQuality.INCOMPLETE
            assert _component(result, COGS).quality is MetricQuality.INCOMPLETE

            # COGS is the costed line only — 2 x 200. Not 500 (there is no cost
            # to find for the second line) and emphatically not 0.
            assert result.cogs_minor == _paise("400.00")
            assert result.cogs_minor > 0

            # The load-bearing assertion: CM1 is NOT computed as though the
            # uncosted line were free. profit_service.py's COALESCE would make
            # the uncosted line contribute 0 cost, which does not change CM1's
            # value here but does make it a lie; what must never happen is CM1
            # collapsing onto the revenue line.
            assert result.cm1_minor != result.net_merchandise_sales_minor
            assert result.cm1_minor == result.net_merchandise_sales_minor - _paise("400.00")

            # And the gap is stated in words, not just in a grade.
            assert any("unit_cost" in w for w in result.warnings)
        finally:
            db.close()
            _cleanup(owned)

    def test_missing_gateway_rule_makes_cm2_none(self) -> None:
        """An absent cost rule blanks the whole CM level. CM2 is not CM1 with a
        different label, and it is not CM1 minus the costs that happen to exist."""
        owned = _Owned()
        db = SessionLocal()
        try:
            _assert_no_foreign_cost_rules(db)
            user = _create_user(db, owned)
            product = _create_product(db, owned, price="500.00", cost="200.00")
            order = _create_order(
                db, owned, user, [(product, 2)],
                shipping="80.00", payment_method="prepaid",
            )
            _create_shipment(db, owned, order, cost="60.00")
            db.commit()
            # Every rule EXCEPT the gateway fee. The order is prepaid, so money
            # really did travel through a gateway and a rule really is required.
            _seed_cost_rules(db, owned, skip=frozenset({CostType.GATEWAY_FEE}))

            result = MarginService(db).compute(WINDOW_FROM, WINDOW_TO)

            assert result.cm1_minor == _paise("600.00")  # 1000 - 400, still knowable
            assert result.cm2_minor is None
            assert result.cm2_minor != result.cm1_minor
            assert CostType.GATEWAY_FEE in result.missing_inputs
            assert _component(result, CostType.GATEWAY_FEE).is_missing

            # CM3 cannot outlive CM2.
            assert result.cm3_minor is None
            assert result.quality is MetricQuality.INCOMPLETE
            # ...and the percentage of an unknown is unknown, not zero.
            assert result.pct(result.cm2_minor) is None
        finally:
            db.close()
            _cleanup(owned)

    def test_shipping_income_is_never_charged_as_a_cost(self) -> None:
        """Inherited bug 1, pinned.

        ``profit_service.py:61-72`` measures revenue at the line level, which
        EXCLUDES ``orders.shipping_amount``, and then ``:132`` subtracts that
        same ``orders.shipping_amount`` as a cost. Shipping is therefore missing
        from revenue *and* deducted — a 2x penalty — while the real carrier
        charge on ``shipments.shipment_cost`` is never read.

        Every rule here is seeded at 0 (a real measurement of zero, not an
        absence), so the only moving parts are the shipping terms.
        """
        owned = _Owned()
        db = SessionLocal()
        try:
            _assert_no_foreign_cost_rules(db)
            user = _create_user(db, owned)
            product = _create_product(db, owned, price="1000.00", cost="400.00")
            order = _create_order(
                db, owned, user, [(product, 1)], shipping="100.00",
            )
            _create_shipment(db, owned, order, cost="60.00")
            db.commit()
            _seed_cost_rules(
                db, owned,
                override={cost_type: Decimal("0") for cost_type, _u, _v in DEFAULT_RULES},
            )

            result = MarginService(db).compute(WINDOW_FROM, WINDOW_TO)

            shipping_charged = _paise("100.00")  # what the customer paid us
            carrier_cost = _paise("60.00")  # what the courier charged us
            nms = _paise("1000.00")
            cogs = _paise("400.00")

            # CM1 has no shipping term at all, on either side.
            assert result.cm1_minor == nms - cogs
            assert result.cm1_minor != nms - cogs - shipping_charged

            # Shipping appears once, as income, and the carrier charge appears
            # once, as a cost.
            assert _component(result, SHIPPING_INCOME).value_minor == shipping_charged
            assert _component(result, CostType.FORWARD_SHIPPING).value_minor == carrier_cost
            assert result.cm2_minor == result.cm1_minor + shipping_charged - carrier_cost

            # The regression identity. profit_service.py's C1 with packing and
            # handling at zero is `line_revenue - product_cost - shipping_paid`.
            old_c1 = nms - cogs - shipping_charged
            assert result.cm2_minor - old_c1 == 2 * shipping_charged - carrier_cost
            # Read the other way: had the courier charged us nothing, the old
            # figure would understate contribution by exactly twice shipping.
            assert (result.cm2_minor + carrier_cost) - old_c1 == 2 * shipping_charged
        finally:
            db.close()
            _cleanup(owned)

    def test_zero_sales_period_has_no_margin_percentage(self) -> None:
        """A period with no sales has an undefined margin, not a 0% one. Charting
        0% would draw a confident flat line through a hole in the data."""
        owned = _Owned()
        db = SessionLocal()
        try:
            result = MarginService(db).compute(EMPTY_FROM, EMPTY_TO)

            assert result.net_merchandise_sales_minor == 0
            for value in (result.cm1_minor, result.cm2_minor, result.cm3_minor):
                assert result.pct(value) is None
                assert result.pct(value) != Decimal("0")
        finally:
            db.close()
            _cleanup(owned)

    def test_waterfall_steps_agree_with_the_computed_margins(self) -> None:
        """The rendered waterfall is the cascade, not a parallel calculation."""
        owned = _Owned()
        db = SessionLocal()
        try:
            _assert_no_foreign_cost_rules(db)
            user = _create_user(db, owned)
            a = _create_product(db, owned, price="500.00", cost="200.00")
            b = _create_product(db, owned, price="300.00", cost="100.00")
            order = _create_order(
                db, owned, user, [(a, 2), (b, 1)],
                discount="100.00", payment_discount="30.00",
                tax="90.00", shipping="80.00",
            )
            _create_shipment(db, owned, order, cost="60.00")
            db.commit()
            _seed_cost_rules(db, owned)

            service = MarginService(db)
            result = service.compute(WINDOW_FROM, WINDOW_TO)
            steps = service.waterfall(result)

            assert {s["kind"] for s in steps} <= {
                "start", "cost", "income", "subtotal", "result"
            }
            by_label = {s["label"]: s for s in steps}
            assert by_label["Net merchandise sales"]["amount_minor"] == (
                result.net_merchandise_sales_minor
            )
            assert by_label["CM1"]["amount_minor"] == result.cm1_minor
            assert by_label["CM2"]["amount_minor"] == result.cm2_minor
            assert by_label["CM3"]["amount_minor"] == result.cm3_minor
            assert by_label["CM3"]["kind"] == "result"

            # Every step carries its grade and a missing marker.
            assert all("quality" in s and "missing" in s for s in steps)
            assert not any(s["missing"] for s in steps)

            # The running total reaches each subtotal exactly: sum the signed
            # movement steps between the subtotals and land on the printed value.
            running = 0
            for step in steps:
                if step["kind"] in {"start", "cost", "income"}:
                    running += step["amount_minor"]
                else:
                    assert running == step["amount_minor"], step["label"]

            # And amounts are Decimal rupees, never float.
            assert all(
                isinstance(s["amount"], Decimal)
                for s in steps
                if s["amount"] is not None
            )
        finally:
            db.close()
            _cleanup(owned)

    def test_waterfall_marks_a_missing_step_rather_than_drawing_a_zero(self) -> None:
        """A missing input must render as a gap, not as a zero-height bar."""
        owned = _Owned()
        db = SessionLocal()
        try:
            _assert_no_foreign_cost_rules(db)
            user = _create_user(db, owned)
            product = _create_product(db, owned, price="500.00", cost="200.00")
            order = _create_order(db, owned, user, [(product, 2)], shipping="80.00")
            _create_shipment(db, owned, order, cost="60.00")
            db.commit()
            _seed_cost_rules(db, owned, skip=frozenset({CostType.GATEWAY_FEE}))

            service = MarginService(db)
            steps = service.waterfall(service.compute(WINDOW_FROM, WINDOW_TO))
            by_label = {s["label"]: s for s in steps}

            gateway = by_label["Gateway fees"]
            assert gateway["missing"] is True
            assert gateway["amount"] is None
            assert gateway["amount_minor"] is None
            assert gateway["quality"] == MetricQuality.INCOMPLETE.value
            assert by_label["CM2"]["missing"] is True
            assert by_label["CM1"]["missing"] is False
        finally:
            db.close()
            _cleanup(owned)


class TestRevenueBridge:
    """The five-term identity that reconciles the merchandise and accounting bases."""

    def test_bridge_balances_on_a_realistic_multi_line_order(self) -> None:
        """gross - discounts + tax + shipping + cod_surcharge - refunds == net."""
        owned = _Owned()
        db = SessionLocal()
        try:
            user = _create_user(db, owned)
            x = _create_product(db, owned, price="250.00", cost="90.00")
            y = _create_product(db, owned, price="120.00", cost="40.00")
            _create_order(
                db, owned, user, [(x, 2), (y, 3)],
                discount="60.00", payment_discount="25.00",
                tax="45.00", shipping="70.00", cod_surcharge="30.00",
                payment_method="cod",
            )
            db.commit()

            bridge = MarginService(db).revenue_bridge(WINDOW_FROM, WINDOW_TO)

            assert bridge.gross_merchandise_sales_minor == _paise("860.00")
            assert bridge.discounts_minor == _paise("85.00")
            assert bridge.tax_minor == _paise("45.00")
            assert bridge.shipping_minor == _paise("70.00")
            assert bridge.cod_surcharge_minor == _paise("30.00")
            assert bridge.refunds_minor == 0
            assert bridge.net_revenue_minor == _paise("920.00")

            assert bridge.balances() is True
            assert bridge.imbalance_minor() == 0
            assert ("Net revenue", _paise("920.00")) in bridge.steps
        finally:
            db.close()
            _cleanup(owned)

    def test_bridge_detects_an_imbalance(self) -> None:
        """balances() only means something if it can fail. Perturb one term and
        the reported gap must be the exact perturbation, signed."""
        owned = _Owned()
        db = SessionLocal()
        try:
            user = _create_user(db, owned)
            x = _create_product(db, owned, price="250.00", cost="90.00")
            y = _create_product(db, owned, price="120.00", cost="40.00")
            _create_order(
                db, owned, user, [(x, 2), (y, 3)],
                discount="60.00", payment_discount="25.00",
                tax="45.00", shipping="70.00", cod_surcharge="30.00",
                payment_method="cod",
            )
            db.commit()

            service = MarginService(db)

            over = service.revenue_bridge(WINDOW_FROM, WINDOW_TO)
            over.tax_minor += 137
            assert over.balances() is False
            assert over.imbalance_minor() == 137

            under = service.revenue_bridge(WINDOW_FROM, WINDOW_TO)
            under.net_revenue_minor += 500
            assert under.balances() is False
            assert under.imbalance_minor() == -500
        finally:
            db.close()
            _cleanup(owned)

    def test_fully_refunded_order_is_recognised_then_reversed_once(self) -> None:
        """A refunded order's SALE stays in the period it happened in, its
        reversal is counted once in refunds, and the bridge still closes.

        This test used to assert the opposite — that a refunded order left the
        merchandise figures entirely — which reversed the same refund twice:
        once by dropping the order, once by subtracting it. See the "Own bug"
        section of ``margin.py`` and ``test_analytics_revenue_recognition.py``.
        """
        owned = _Owned()
        db = SessionLocal()
        try:
            _assert_no_foreign_cost_rules(db)
            user = _create_user(db, owned)
            kept = _create_product(db, owned, price="400.00", cost="150.00")
            returned = _create_product(db, owned, price="900.00", cost="350.00")

            _create_order(db, owned, user, [(kept, 1)], tax="20.00", shipping="50.00")
            refunded = _create_order(
                db, owned, user, [(returned, 1)],
                status=OrderStatus.REFUNDED,
                tax="45.00", shipping="60.00",
                refunded_at=datetime(2007, 3, 6, 9, 0, tzinfo=timezone.utc),
            )
            db.commit()
            _seed_cost_rules(db, owned)

            service = MarginService(db)
            result = service.compute(WINDOW_FROM, WINDOW_TO)
            bridge = service.revenue_bridge(WINDOW_FROM, WINDOW_TO)

            # Both sales count in both surfaces: the goods were sold, and the
            # costs of selling them were really incurred.
            assert result.net_merchandise_sales_minor == _paise("1300.00")
            assert result.cogs_minor == _paise("500.00")
            assert result.cost_coverage_pct == Decimal("100.00")
            assert bridge.gross_merchandise_sales_minor == _paise("1300.00")
            assert bridge.shipping_minor == _paise("110.00")

            # The refund is counted once, valued at the order total, dated by
            # refunded_at — and NOT a second time via the status filter.
            assert bridge.refunds_minor == to_minor(refunded.total_amount)
            assert bridge.refunds_minor == _paise("1005.00")
            # (470 + 1005) recognised, less the 1005 reversed in the same window.
            assert bridge.net_revenue_minor == _paise("470.00")
            assert bridge.net_revenue_minor > 0
            assert bridge.balances() is True

            # The cascade has no contra-revenue term, so it says so in words.
            assert any("later refunded" in w for w in result.warnings)
        finally:
            db.close()
            _cleanup(owned)
