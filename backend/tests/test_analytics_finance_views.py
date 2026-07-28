"""Tests for the three finance resolvers and the five views they answer.

Views 47 (Pricing and Margin), 67 (Contribution Margin), 68 (Unit Economics),
70 (Tax and GST) and 72 (Budget vs Actual).

What these tests are actually defending
---------------------------------------
The arithmetic here is the easy half. The reason this suite exists is that this
deployment has **no cost rules at all** — every ``costs.*`` setting holds ``"0"``
and ``CostRuleResolver.seed_from_legacy_settings`` correctly refuses to migrate a
zero, because a zero rate and a never-configured rate are indistinguishable. So
the interesting behaviour is what the views do with an unknown, and there are
exactly two failure modes worth writing tests for:

  * ``test_cm1_is_not_net_merchandise_sales_when_coverage_is_partial`` — a
    zero-filled COGS makes CM1 equal net merchandise sales exactly, and reports
    100% margin. That is what ``profit_service.py``'s
    ``coalesce(unit_cost, 0)`` does today, it flatters the number, and nobody
    ever investigates a margin that came in high. The equality is asserted
    against explicitly.
  * ``test_no_cost_rules_leaves_cm2_and_cm3_unknown`` — CM2 must be None and
    must NAME the rules it is short of. A CM2 quietly equal to CM1 (which is
    what dropping a missing cost gives) is CM1 wearing CM2's label, and the
    screen it appears on is the one the business prices from.

Two more say the same thing in the other two views:
``test_budget_vs_actual_with_no_budgets_...`` (0% attainment reads as
catastrophic failure; "no targets set" reads as work to do) and
``test_cac_is_unknown_...`` (a zero-spend CAC reports free acquisition).

Isolation strategy
------------------
House style, following ``test_analytics_margin.py`` and
``test_analytics_view_bindings.py``: no db fixture in ``conftest.py``; every
test owns its ``SessionLocal()`` and tears down in a ``finally``.

The sandbox is **April / September 2012** — a year no other suite touches
(2007 is ``test_analytics_margin``, June 2008 is ``test_analytics_cache`` and
``test_analytics_view_bindings``, 2009 is the resolver and aggregation suites,
2011 is ``test_analytics_revenue_recognition``, 2026 is
``test_analytics_admin_api``). Every assertion is therefore on an absolute
figure rather than on a delta.

Three consequences of the shared throwaway MySQL are handled explicitly:

  a) ``_assert_no_foreign_cost_rules`` fails loudly if a rule this module did
     not write covers 2012. A stray rule would not make these tests noisy, it
     would make them wrong.
  b) ``CostRuleResolver`` caches resolutions in Redis for 60s **including
     misses**, and these tests reuse the same dates with and without rules.
     Setup and teardown both call ``invalidate_all()``.
  c) Rollup fixtures are written under the database's **active** generation,
     because ``AnalyticsViewService`` reads it from the database and cannot be
     told otherwise; isolation comes from the 2012 dates.

Run inside the analytics container:

    docker exec wvana-py python -m pytest tests/test_analytics_finance_views.py -q
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterator

import pytest
from sqlalchemy import delete, or_, select, text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models import analytics_rollups as rollups
from app.models.analytics_control import (
    AnalyticsBudget,
    AnalyticsCostRule,
    BudgetGranularity,
    CostQuality,
    CostScope,
    CostType,
    CostUnit,
)
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Product
from app.models.user import User
from app.repositories.analytics_repository import AnalyticsRepository
from app.schemas.analytics_view import AnalyticsViewEnvelope
from app.services.analytics import registry
from app.services.analytics.contracts import to_minor
from app.services.analytics.cost_rules import CostRuleResolver
from app.services.analytics.filters import AnalyticsFilters, Comparison, Period
from app.services.analytics.resolvers.base import NOT_CONFIGURED, ResolverContext
from app.services.analytics.resolvers.finance import (
    budget_vs_actual,
    margin_cascade,
    unit_economics,
)
from app.services.analytics.timebox import active_generation
from app.services.analytics.types import MetricQuality, ResolverId, ViewState
from app.services.analytics.view_service import AnalyticsViewService
from app.schemas.analytics_view import WarningCode

# ---------------------------------------------------------------------------
# The 2012 sandbox
# ---------------------------------------------------------------------------

WINDOW_FROM = date(2012, 4, 1)
WINDOW_TO = date(2012, 4, 11)  # half-open: 10 reporting days
ORDER_AT = datetime(2012, 4, 5, 10, 30, tzinfo=timezone.utc)

#: A quiet window with nothing in it, for the zero-sales assertions.
EMPTY_FROM = date(2012, 9, 1)
EMPTY_TO = date(2012, 9, 11)

#: Far enough after the window that no resolved window can include "today", so
#: `includes_today` never adds a PARTIAL_TODAY warning to an assertion.
SANDBOX_TODAY = date(2012, 6, 1)

#: Bounds of everything this module may delete on the way out.
SANDBOX_FIRST = date(2012, 1, 1)
SANDBOX_LAST = date(2012, 12, 31)

RULE_FROM = date(2012, 1, 1)
#: Deliberately CLOSED. An open-ended rule would reach into the present and
#: perturb every other suite that reads cost rules.
RULE_TO = date(2012, 12, 31)
RULE_SOURCE = "test_analytics_finance_views"
BUDGET_NOTE = "test_analytics_finance_views fixture"

#: (cost_type, unit, value). April has 30 days, so a PER_MONTH marketing rule of
#: 300 pro-rates to exactly 10.00/day and 100.00 over the 10-day window.
FULL_RULES: tuple[tuple[str, str, Decimal], ...] = (
    (CostType.GATEWAY_FEE, CostUnit.PCT, Decimal("2.0")),
    (CostType.PACKAGING, CostUnit.PER_ORDER, Decimal("15")),
    (CostType.HANDLING, CostUnit.PER_ORDER, Decimal("10")),
    (CostType.FORWARD_SHIPPING, CostUnit.PER_ORDER, Decimal("50")),
    (CostType.RETURN_SHIPPING, CostUnit.PER_ORDER, Decimal("45")),
    (CostType.RTO_LOGISTICS, CostUnit.PER_ORDER, Decimal("90")),
    (CostType.MARKETPLACE_COMMISSION, CostUnit.PCT, Decimal("0")),
    (CostType.MARKETING_SPEND, CostUnit.PER_MONTH, Decimal("300")),
)

#: What the cascade is short of on this database, in `CM2_COST_TYPES +
#: CM3_COST_TYPES` order. `return_shipping`, `rto_logistics` and
#: `marketplace_commission` are absent from this list on purpose: their DRIVERS
#: are zero in the window (no reverse pickup, no RTO leg, no marketplace
#: channel), and a zero quantity is a measurement rather than a missing rate.
EXPECTED_MISSING: tuple[str, ...] = (
    CostType.GATEWAY_FEE,
    CostType.PACKAGING,
    CostType.HANDLING,
    CostType.FORWARD_SHIPPING,
    CostType.MARKETING_SPEND,
)

#: (view number, module slug, view slug) for the five views this module wires.
FINANCE_VIEWS: tuple[tuple[int, str, str], ...] = (
    (47, "sales-finance", "pricing-and-margin"),
    (67, "sales-finance", "contribution-margin"),
    (68, "sales-finance", "unit-economics"),
    (70, "sales-finance", "tax-and-gst"),
    (72, "executive", "budget-vs-actual"),
)

_OWNED_ROLLUPS = (
    rollups.AggOrderDaily,
    rollups.AggProductDaily,
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


def _create_user(db: Session, owned: _Owned) -> User:
    user = User(
        email=f"financeviews-{_uid()}@example.com",
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
        sku=f"SKU-FINVIEW-{_uid()}",
        name=f"FinanceViewProduct {_uid()}",
        price=Decimal(price),
        # None means "never snapshotted" — the case a zero-fill would report as
        # free goods and a 100% margin.
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
    tax: str = "0",
    shipping: str = "0",
) -> Order:
    """An order whose total satisfies the revenue-bridge identity.

    Built the same way as ``test_analytics_margin._create_order`` so the two
    suites cannot disagree about what a fixture order is worth.
    """
    order_items: list[OrderItem] = []
    gross = Decimal("0.00")
    for product, qty in items:
        order_items.append(
            OrderItem(
                product_id=product.id,
                quantity=qty,
                unit_price=product.price,
                unit_cost=product.cost,
            )
        )
        gross += product.price * qty

    total = gross - Decimal(discount) + Decimal(tax) + Decimal(shipping)
    order = Order(
        user_id=user.id,
        status=status,
        subtotal=gross,
        tax_amount=Decimal(tax),
        discount_amount=Decimal(discount),
        payment_discount_amount=Decimal("0.00"),
        shipping_amount=Decimal(shipping),
        cod_surcharge_amount=Decimal("0.00"),
        # Prepaid: the whole total travelled through a gateway, so the day has a
        # non-zero gateway driver and therefore genuinely needs a fee rule.
        cod_balance=Decimal("0.00"),
        total_amount=total,
        currency="INR",
        payment_method="prepaid",
        created_at=created_at,
    )
    order.items = order_items
    db.add(order)
    db.flush()
    owned.orders.append(order.id)
    return order


def _seed_cost_rules(db: Session) -> None:
    """Write the eight rules the cascade needs.

    A rule whose ``value`` is 0 (marketplace commission) is a real measurement,
    which is categorically different from having no rule at all.
    """
    for cost_type, unit, value in FULL_RULES:
        db.add(
            AnalyticsCostRule(
                cost_type=cost_type,
                scope=CostScope.GLOBAL,
                scope_value="-",
                value=value,
                unit=unit,
                currency="INR",
                quality=CostQuality.CONTRACTED,
                effective_from=RULE_FROM,
                effective_to=RULE_TO,
                source=RULE_SOURCE,
                note="test fixture",
            )
        )
    db.commit()
    # The resolver caches misses as well as hits for 60s, and these tests reuse
    # the same dates with and without a given rule.
    CostRuleResolver(db).invalidate_all()


def _seed_rollups(db: Session, generation: int) -> None:
    """One complete reporting day, written across the 10-day window.

    The revenue bridge balances: 900 - 50 + 50 + 30 + 0 - 0 = 930.
    """
    for offset in range(10):
        day = WINDOW_FROM + timedelta(days=offset)
        db.add(
            rollups.AggOrderDaily(
                bucket_date=day,
                tz_generation=generation,
                orders_total=10,
                orders_paid=5,
                orders_shipped=3,
                orders_delivered=2,
                order_value_created=Decimal("1000.00"),
                paid_order_value=Decimal("800.00"),
                gross_merchandise_sales=Decimal("900.00"),
                net_merchandise_sales=Decimal("850.00"),
                net_revenue=Decimal("930.00"),
                subtotal_sum=Decimal("900.00"),
                tax_sum=Decimal("50.00"),
                discount_sum=Decimal("50.00"),
                shipping_income=Decimal("30.00"),
                refund_sum=Decimal("0.00"),
                cogs_sum=Decimal("400.00"),
                units=20,
                costed_units=20,
                distinct_customers=8,
                new_customers=5,
                returning_customers=3,
            )
        )
        db.add(
            rollups.AggProductDaily(
                bucket_date=day,
                tz_generation=generation,
                product_id=1,
                sku_snapshot="SKU-FINVIEW-ROLLUP",
                category_id_snapshot=1,
                units=20,
                orders=10,
                gross_merchandise_sales=Decimal("900.00"),
                net_merchandise_sales=Decimal("850.00"),
                line_cost=Decimal("400.00"),
                costed_units=20,
            )
        )
    db.commit()


def _assert_no_foreign_cost_rules(db: Session) -> None:
    """Fail loudly if a rule this module did not write covers 2012.

    Every expected figure below is absolute, so a stray rule would not make
    these tests noisy — it would make them wrong, and in the flattering
    direction.
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
            "cost rules not owned by this test cover the 2012 fixture window and "
            "would change every expected figure: "
            + ", ".join(
                f"#{r.id} {r.cost_type} from {r.effective_from}" for r in foreign
            )
        )


def _assert_no_foreign_budgets(db: Session) -> None:
    """The no-budget assertions are only meaningful if there really are none."""
    rows = (
        db.execute(
            select(AnalyticsBudget).where(
                AnalyticsBudget.period_start <= SANDBOX_LAST,
                AnalyticsBudget.period_end >= SANDBOX_FIRST,
            )
        )
        .scalars()
        .all()
    )
    foreign = [r for r in rows if (r.note or "") != BUDGET_NOTE]
    if foreign:
        pytest.fail(
            "budget rows not owned by this test cover the 2012 window: "
            + ", ".join(f"#{r.id} {r.metric} {r.period_start}" for r in foreign)
        )


def _cleanup(owned: _Owned, generation: int) -> None:
    """Delete everything this module could have written, through a fresh session."""
    with SessionLocal() as s:
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
        # By source / note, not by id: a row inserted by a previous *failed* run
        # would otherwise poison every later run.
        s.execute(
            text("DELETE FROM analytics_cost_rules WHERE source = :src"),
            {"src": RULE_SOURCE},
        )
        s.execute(
            text("DELETE FROM analytics_budgets WHERE note = :note"),
            {"note": BUDGET_NOTE},
        )
        for model in _OWNED_ROLLUPS:
            s.execute(
                delete(model).where(
                    model.tz_generation == generation,
                    model.bucket_date >= SANDBOX_FIRST,
                    model.bucket_date <= SANDBOX_LAST,
                )
            )
        s.commit()
        CostRuleResolver(s).invalidate_all()


@contextmanager
def sandbox() -> Iterator[tuple[Session, _Owned, int]]:
    """A session, an ownership ledger and the database's ACTIVE generation.

    The generation is the active one rather than a private random value because
    ``AnalyticsViewService`` reads it from the database and cannot be told
    otherwise. Isolation therefore comes from the 2012 dates.
    """
    owned = _Owned()
    db = SessionLocal()
    generation = int(active_generation(db).generation)
    try:
        _assert_no_foreign_cost_rules(db)
        _assert_no_foreign_budgets(db)
        CostRuleResolver(db).invalidate_all()
        yield db, owned, generation
    finally:
        try:
            db.rollback()
        finally:
            db.close()
            _cleanup(owned, generation)


# ---------------------------------------------------------------------------
# Context / envelope helpers
# ---------------------------------------------------------------------------


def _view(number: int):
    for _n, module_slug, view_slug in FINANCE_VIEWS:
        if _n == number:
            view = registry.get_view(module_slug, view_slug)
            assert view is not None, f"view {number} missing from the registry"
            return view
    raise AssertionError(f"view {number} is not one of this module's views")


def _ctx(
    db: Session,
    number: int,
    generation: int,
    *,
    date_from: date = WINDOW_FROM,
    date_to: date = WINDOW_TO,
    comparison: Comparison = Comparison.NONE,
    **filter_kw,
) -> ResolverContext:
    filters = AnalyticsFilters(
        period=Period.CUSTOM,
        date_from=date_from,
        date_to=date_to,
        comparison=comparison,
        **filter_kw,
    )
    view = _view(number)
    return ResolverContext(
        db=db,
        repo=AnalyticsRepository(db),
        view=view,
        filters=filters,
        window=filters.resolve(SANDBOX_TODAY),
        tz_generation=generation,
        today=SANDBOX_TODAY,
    )


def _codes(result) -> list[str]:
    return [w.code for w in result.warnings]


def _warning(result, code: str):
    for w in result.warnings:
        if w.code == code:
            return w
    raise AssertionError(
        f"expected a {code} warning; got {sorted(set(_codes(result)))}"
    )


class _FinanceReader:
    """Holds exactly the permissions the five views need, and nothing else.

    `AnalyticsViewService` only ever calls `has_permission`, so this exercises
    the real authorisation path without creating a user row — and without
    reaching for an admin account whose `is_admin` short circuit would make
    every check pass for the wrong reason.
    """

    is_admin = False

    def __init__(self) -> None:
        self._permissions = {
            "analytics.view",
            "analytics.finance.view",
            "analytics.executive.view",
        }

    def has_permission(self, permission: str) -> bool:
        return permission in self._permissions


# ===========================================================================
# 1. With no cost rules: CM1 is real, CM2/CM3 are unknown and say why
# ===========================================================================


def test_no_cost_rules_leaves_cm2_and_cm3_unknown_and_names_every_absent_rule():
    """The state this database is actually in, asserted end to end.

    CM1 computes from real ``order_items.unit_cost``. CM2 and CM3 are None —
    NOT CM1 under another label, and NOT a cascade with the missing terms
    silently dropped — and every absent rule is named on the card and in the
    warning, so an admin can see which rows to write.
    """
    with sandbox() as (db, owned, generation):
        user = _create_user(db, owned)
        a = _create_product(db, owned, price="500.00", cost="200.00")
        b = _create_product(db, owned, price="300.00", cost="100.00")
        # No shipment at all, so the order needs a FORWARD_SHIPPING rule and the
        # absence of one is a genuine gap rather than "the carrier billed us 0".
        _create_order(db, owned, user, [(a, 2), (b, 1)], discount="100.00")
        db.commit()
        _seed_rollups(db, generation)

        result = margin_cascade(_ctx(db, 67, generation))
        kpis = result.kpis

        # gross 1300 - 100 discount = 1200; COGS = 2x200 + 100 = 500.
        assert kpis["net_merchandise_sales"].value == Decimal("1200.00")
        assert kpis["cogs"].value == Decimal("500.00")
        assert kpis["cm1"].value == Decimal("700.00")
        assert kpis["cm1_pct"].value == Decimal("58.33")

        # The whole point: unknown, not zero, and not CM1.
        assert kpis["cm2"].value is None
        assert kpis["cm3"].value is None
        assert kpis["cm2_pct"].value is None
        assert kpis["cm3_pct"].value is None
        assert kpis["cm2"].value != kpis["cm1"].value

        assert set(kpis["cm2"].inputs_missing) == {
            CostType.GATEWAY_FEE,
            CostType.PACKAGING,
            CostType.HANDLING,
            CostType.FORWARD_SHIPPING,
        }
        assert set(kpis["cm3"].inputs_missing) == set(EXPECTED_MISSING)
        assert kpis["cm2"].quality == MetricQuality.INCOMPLETE.value
        assert kpis["cm3"].quality == MetricQuality.INCOMPLETE.value

        # CM1 itself is fully covered, so it is NOT incomplete — the grade is
        # per level, and condemning CM1 for CM2's gap would be as misleading as
        # excusing CM2 for CM1's health.
        assert kpis["cm1"].quality == MetricQuality.AUTHORITATIVE.value
        assert result.quality is MetricQuality.INCOMPLETE

        # A missing rule is a missing COMPONENT, not a missing card.
        assert kpis["gateway_fees"].value is None
        assert kpis["gateway_fees"].inputs_missing == [CostType.GATEWAY_FEE]


def test_the_missing_cost_rule_warning_says_which_rules_and_where_to_put_them():
    """An admin who reads only the warning must know what to do next.

    "Contribution Margin: —" is not a finding, it is a shrug. The message has to
    name the rules and the table, or the view has told nobody anything.
    """
    with sandbox() as (db, owned, generation):
        user = _create_user(db, owned)
        product = _create_product(db, owned, price="500.00", cost="200.00")
        _create_order(db, owned, user, [(product, 1)])
        db.commit()
        _seed_rollups(db, generation)

        result = margin_cascade(_ctx(db, 67, generation))
        warning = _warning(result, WarningCode.COST_RULE_MISSING)

        assert warning.detail["missing"] == list(EXPECTED_MISSING)
        assert warning.detail["table"] == "analytics_cost_rules"
        for label in (
            "gateway fees",
            "packaging",
            "fulfilment / handling",
            "forward shipping",
            "marketing spend",
        ):
            assert label in warning.message.lower(), warning.message
        assert "analytics_cost_rules" in warning.message
        # The zero-substitution rule, stated where a reader will see it.
        assert "zero" in warning.message.lower()


# ===========================================================================
# 2. The equality a zero-fill produces
# ===========================================================================


def test_cm1_is_not_net_merchandise_sales_when_cost_coverage_is_partial():
    """The inherited bug, pinned by the identity it would create.

    ``profit_service.py`` does ``coalesce(unit_cost, 0)``. Under that rule an
    uncosted line contributes zero COGS, and in the limit CM1 == net merchandise
    sales and margin reads 100%. Here the uncosted line is EXCLUDED from COGS
    and counted in the coverage denominator instead, so CM1 stays strictly below
    net merchandise sales and the result is graded INCOMPLETE.
    """
    with sandbox() as (db, owned, generation):
        user = _create_user(db, owned)
        costed = _create_product(db, owned, price="400.00", cost="150.00")
        uncosted = _create_product(db, owned, price="600.00", cost=None)
        _create_order(db, owned, user, [(costed, 1), (uncosted, 1)])
        db.commit()
        _seed_rollups(db, generation)

        result = margin_cascade(_ctx(db, 67, generation))
        kpis = result.kpis

        nms = kpis["net_merchandise_sales"].value
        cm1 = kpis["cm1"].value
        assert nms == Decimal("1000.00")
        assert kpis["cogs"].value == Decimal("150.00")

        assert cm1 != nms, (
            "CM1 equals net merchandise sales, which is exactly what a "
            "coalesce(unit_cost, 0) produces: the uncosted line contributed no "
            "COGS and is being reported as free goods at 100% margin."
        )
        assert cm1 == Decimal("850.00")
        assert cm1 < nms

        # One of two lines carried a cost.
        assert result.coverage_pct == Decimal("50.00")
        assert kpis["cm1"].coverage_pct == Decimal("50.00")
        assert kpis["cm1"].quality == MetricQuality.INCOMPLETE.value
        assert kpis["cm1_pct"].value == Decimal("85.00")

        coverage = _warning(result, WarningCode.COST_COVERAGE_LOW)
        assert "50.00" in coverage.message
        assert "zero" in coverage.message.lower()


# ===========================================================================
# 3. With rules configured, the cascade is a cascade again
# ===========================================================================


def test_with_every_cost_rule_seeded_cm1_beats_cm2_beats_cm3():
    """The ordering only holds when every input exists, so assert both halves.

    Also asserts the grade is no longer INCOMPLETE: the rules are CONTRACTED, so
    the honest ceiling is ESTIMATED — a rate card is a promise about the future,
    not an observation of the past.
    """
    with sandbox() as (db, owned, generation):
        user = _create_user(db, owned)
        a = _create_product(db, owned, price="500.00", cost="200.00")
        b = _create_product(db, owned, price="300.00", cost="100.00")
        _create_order(db, owned, user, [(a, 2), (b, 1)], discount="100.00")
        db.commit()
        _seed_rollups(db, generation)
        _seed_cost_rules(db)

        result = margin_cascade(_ctx(db, 67, generation))
        kpis = result.kpis

        assert kpis["cm1"].value == Decimal("700.00")
        # CM2 = 700 + 0 shipping income - 24.00 gateway (2% of 1200 prepaid)
        #       - 15 packaging - 10 handling - 50 forward shipping
        #       - 0 returns - 0 RTO - 0 marketplace
        assert kpis["cm2"].value == Decimal("601.00")
        # CM3 = 601.00 - 100.00 marketing (300/month x 10 of April's 30 days)
        assert kpis["cm3"].value == Decimal("501.00")

        assert kpis["cm1"].value > kpis["cm2"].value > kpis["cm3"].value
        assert kpis["cm1_pct"].value > kpis["cm2_pct"].value > kpis["cm3_pct"].value

        assert kpis["cm2"].inputs_missing == []
        assert kpis["cm3"].inputs_missing == []
        assert kpis["cm2"].quality != MetricQuality.INCOMPLETE.value
        assert kpis["cm3"].quality != MetricQuality.INCOMPLETE.value
        assert result.quality is not MetricQuality.INCOMPLETE
        assert result.quality is MetricQuality.ESTIMATED

        assert kpis["gateway_fees"].value == Decimal("24.00")
        assert WarningCode.COST_RULE_MISSING not in _codes(result)

        # The waterfall is a function of the same result, so it cannot disagree.
        steps = {s["label"]: s for s in result.series["margin_waterfall"]}
        assert steps["CM1"]["amount"] == Decimal("700.00")
        assert steps["CM3"]["amount"] == Decimal("501.00")
        assert not any(s["missing"] for s in result.series["margin_waterfall"])


def test_the_waterfall_draws_a_gap_not_a_zero_for_an_unconfigured_cost():
    """A zero-height bar claims the cost was measured and was free."""
    with sandbox() as (db, owned, generation):
        user = _create_user(db, owned)
        product = _create_product(db, owned, price="500.00", cost="200.00")
        _create_order(db, owned, user, [(product, 1)])
        db.commit()
        _seed_rollups(db, generation)

        result = margin_cascade(_ctx(db, 67, generation))
        steps = {s["cost_type"]: s for s in result.series["margin_waterfall"]}

        for cost_type in EXPECTED_MISSING:
            step = steps[cost_type]
            assert step["missing"] is True, cost_type
            assert step["amount"] is None, cost_type
            assert step["quality"] == MetricQuality.INCOMPLETE.value, cost_type

        # A driver measured at zero is a real measurement and keeps its zero.
        assert steps[CostType.RETURN_SHIPPING]["missing"] is False
        assert steps[CostType.RETURN_SHIPPING]["amount"] == Decimal("0.00")


# ===========================================================================
# 4. A percentage over a zero base
# ===========================================================================


def test_margin_percentage_over_a_zero_sales_window_is_none_not_zero():
    """A period with no sales has an undefined margin, not a 0% one.

    Charting 0% would draw a confident flat line through a gap in the data, and
    a reader cannot tell that apart from a month where everything was sold at
    cost.
    """
    with sandbox() as (db, owned, generation):
        result = margin_cascade(
            _ctx(db, 67, generation, date_from=EMPTY_FROM, date_to=EMPTY_TO)
        )
        kpis = result.kpis

        assert kpis["net_merchandise_sales"].value == Decimal("0")
        # A real, measured zero on the amount...
        assert kpis["cm1"].value == Decimal("0")
        # ...and an explicit "undefined" on the percentage.
        assert kpis["cm1_pct"].value is None
        assert kpis["cm1_pct"].value != Decimal("0")
        assert kpis["gross_margin_pct"].value is None
        assert kpis["cm2_pct"].value is None
        assert kpis["cm3_pct"].value is None


# ===========================================================================
# 5. Budgets: absent is not zero
# ===========================================================================


def test_budget_vs_actual_with_no_budgets_reports_not_configured_not_zero_attainment():
    """0% attainment reads as catastrophe. "No target set" reads as work to do.

    The actuals are real and are still shown; what is missing is the other half
    of the comparison, and the warning says so by name.
    """
    with sandbox() as (db, owned, generation):
        _seed_rollups(db, generation)

        result = budget_vs_actual(_ctx(db, 72, generation))

        # No table at all — an empty variance table reads as a clean one.
        assert "budget_lines" not in result.tables
        assert result.tables == {}

        warning = _warning(result, NOT_CONFIGURED)
        assert "not configured" in warning.message.lower()
        assert "attainment" in warning.message.lower()
        assert "analytics_budgets" in warning.message
        assert warning.detail["table"] == "analytics_budgets"

        # Nothing anywhere in the payload claims 0% attainment.
        assert "budget_attainment_pct" not in result.kpis
        for kpi in result.kpis.values():
            assert "attainment" not in kpi.kpi_id

        # The actuals are genuinely measured and are still reported.
        assert result.kpis["net_revenue"].value == Decimal("9300.00")
        assert result.kpis["orders_count"].value == Decimal("100")

        # The view cannot do the job it is named for.
        assert result.quality is MetricQuality.INCOMPLETE


def test_budget_vs_actual_with_a_target_reports_a_real_attainment():
    """The other half of the previous test: with a target, the maths happens.

    Without this, "never reports 0%" would also be satisfied by a resolver that
    never reports anything.
    """
    with sandbox() as (db, owned, generation):
        _seed_rollups(db, generation)
        db.add(
            AnalyticsBudget(
                period_start=WINDOW_FROM,
                period_end=WINDOW_TO - timedelta(days=1),
                granularity=BudgetGranularity.MONTH,
                metric="net_revenue",
                dimension="-",
                dimension_value="-",
                budget_amount=Decimal("10000.00"),
                currency="INR",
                note=BUDGET_NOTE,
            )
        )
        db.commit()

        result = budget_vs_actual(_ctx(db, 72, generation))

        rows = result.tables["budget_lines"].rows
        assert len(rows) == 1
        row = rows[0]
        assert row["metric"] == "net_revenue"
        assert row["scope"] == "store-wide"
        assert row["budget"] == Decimal("10000.00")
        # 10 days x 930.00 of net revenue.
        assert row["actual"] == Decimal("9300.00")
        assert row["attainment_pct"] == Decimal("93.00")
        assert row["variance_pct"] == Decimal("-7.00")
        assert row["period_complete"] is True
        assert row["inputs_missing"] == []

        assert NOT_CONFIGURED not in _codes(result)
        assert result.series["budget_vs_actual"] == [
            {
                "period": "2012-04-01..2012-04-10",
                "budget_revenue": Decimal("10000.00"),
                "net_revenue": Decimal("9300.00"),
            }
        ]


def test_a_scoped_budget_reports_a_null_actual_rather_than_the_store_total():
    """A category target compared against the store number is not a comparison.

    `net_revenue` is order-basis and includes tax and shipping, neither of which
    can be attributed to a line, so there is no category actual to compare
    against. Null names the gap; the store-wide figure under a category label
    would be a much worse answer.
    """
    with sandbox() as (db, owned, generation):
        _seed_rollups(db, generation)
        db.add(
            AnalyticsBudget(
                period_start=WINDOW_FROM,
                period_end=WINDOW_TO - timedelta(days=1),
                granularity=BudgetGranularity.MONTH,
                metric="net_revenue",
                dimension="category",
                dimension_value="7",
                budget_amount=Decimal("2500.00"),
                currency="INR",
                note=BUDGET_NOTE,
            )
        )
        db.commit()

        result = budget_vs_actual(_ctx(db, 72, generation))
        row = result.tables["budget_lines"].rows[0]

        assert row["scope"] == "category=7"
        assert row["actual"] is None
        assert row["attainment_pct"] is None
        assert row["variance_pct"] is None
        assert row["inputs_missing"] == ["actual at scope category=7"]
        assert result.quality is MetricQuality.INCOMPLETE


# ===========================================================================
# 6. Unit economics: CAC without spend is unknown, never free
# ===========================================================================


def test_cac_is_unknown_and_names_marketing_spend_when_no_rule_is_configured():
    """A CAC of zero says acquisition is free. It is not; it is unmeasured.

    The only source of marketing spend in this deployment is a MARKETING_SPEND
    cost rule — there is no ad-platform connection and no internal table of ad
    spend — so with no rule, CAC and everything downstream of it are null with
    their inputs named.
    """
    with sandbox() as (db, owned, generation):
        user = _create_user(db, owned)
        product = _create_product(db, owned, price="500.00", cost="200.00")
        _create_order(db, owned, user, [(product, 1)])
        db.commit()
        _seed_rollups(db, generation)

        result = unit_economics(_ctx(db, 68, generation))
        kpis = result.kpis

        assert kpis["cac"].value is None
        assert kpis["cac"].value != Decimal("0")
        assert CostType.MARKETING_SPEND in kpis["cac"].inputs_missing
        assert kpis["cac"].quality == MetricQuality.INCOMPLETE.value

        # Everything built on CAC inherits the gap rather than inventing a base.
        assert kpis["ltv_to_cac_ratio"].value is None
        assert CostType.MARKETING_SPEND in kpis["ltv_to_cac_ratio"].inputs_missing
        assert kpis["cac_payback_period"].value is None
        assert CostType.MARKETING_SPEND in kpis["cac_payback_period"].inputs_missing

        # LTV needs a lifetime population no additive rollup holds.
        assert kpis["ltv"].value is None
        assert kpis["ltv"].inputs_missing

        warning = _warning(result, NOT_CONFIGURED)
        assert "ad-platform" in warning.message.lower()
        assert "marketing_spend" in warning.message.lower()
        assert "free acquisition" in warning.message.lower()
        assert CostType.MARKETING_SPEND in warning.detail["requires"]


def test_unit_economics_reports_per_order_contribution_and_names_what_it_cannot():
    """Per-order economics, with the unconfigured lines marked rather than zeroed."""
    with sandbox() as (db, owned, generation):
        user = _create_user(db, owned)
        product = _create_product(db, owned, price="500.00", cost="200.00")
        _create_order(db, owned, user, [(product, 1)])
        db.commit()
        _seed_rollups(db, generation)

        result = unit_economics(_ctx(db, 68, generation))
        rows = {r["component"]: r for r in result.tables["unit_economics"].rows}

        # 100 paid orders across the seeded rollup window.
        cm1_row = rows["CM1"]
        assert cm1_row["amount"] == Decimal("300.00")
        assert cm1_row["per_order"] == Decimal("3.00")

        # Every unconfigured cost line is flagged, not silently 0.00 per order.
        for cost_type in EXPECTED_MISSING:
            row = next(r for r in rows.values() if r["cost_type"] == cost_type)
            assert row["missing"] is True, cost_type
            assert row["per_order"] is None, cost_type

        # AOV still comes from the rollup and is a real measurement.
        assert result.kpis["aov"].value is not None


def test_unit_economics_over_an_empty_window_has_no_per_order_figures():
    """No orders means no per-order economics — not 0.00 contribution each."""
    with sandbox() as (db, owned, generation):
        result = unit_economics(
            _ctx(db, 68, generation, date_from=EMPTY_FROM, date_to=EMPTY_TO)
        )
        rows = {r["component"]: r for r in result.tables["unit_economics"].rows}
        assert rows["CM1"]["per_order"] is None
        assert WarningCode.SMALL_SAMPLE in _codes(result)


# ===========================================================================
# 7. View 70 — bound to what exists, and still not compliance-grade
# ===========================================================================


def test_view_70_stays_partial_and_says_it_is_not_compliance_grade():
    """Wiring a view must never upgrade what it claims it can show.

    There is no HSN code, no rate slab, no place of supply and no reverse-charge
    flag anywhere in the schema, so a GST-filing-ready view is impossible here.
    Binding the one honest figure (order-level tax) must not soften that.
    """
    view = _view(70)
    assert view.state is ViewState.PARTIAL
    assert "NOT compliance-grade" in view.limitation
    assert "GST filing" in view.limitation
    assert view.resolver is ResolverId.TABLE

    columns = view.params["columns"]
    assert columns["tax_amount"] == "tax_sum"
    assert columns["taxable_value"] == "subtotal_sum"
    # The two columns that would turn an operational report into a filing claim.
    assert "rate" not in columns, (
        "view 70 maps a `rate` column, but no rate slab is stored anywhere. "
        "Whatever it points at, it is not a GST rate, and a filled slab column "
        "reads as filing-ready."
    )
    assert "state" not in columns, (
        "view 70 maps a `state` column. Place of supply is not recorded; a "
        "delivery-address state would assert an intra/inter-state call this "
        "system never made."
    )


def test_view_70_returns_the_tax_it_does_have():
    """Bound, not empty: the operational figure is real and is reported."""
    with sandbox() as (db, owned, generation):
        _seed_rollups(db, generation)

        from app.services.analytics.resolvers.core import TableResolver

        result = TableResolver().run(_ctx(db, 70, generation))
        rows = result.tables["tax_table"].rows
        assert len(rows) == 10, "one row per seeded reporting day"
        assert all(r["tax_amount"] == Decimal("50.00") for r in rows)
        assert all(r["taxable_value"] == Decimal("900.00") for r in rows)
        assert result.kpis["tax_collected"].value == Decimal("500.00")


# ===========================================================================
# 8. End to end through AnalyticsViewService
# ===========================================================================


def test_all_five_finance_views_resolve_through_the_view_service():
    """Every one returns a data envelope, not a gate and not a 503.

    This is the test that would have caught a resolver registered but never
    imported: `CustomResolver` raises for an unknown function name, which
    `view_service` turns into a 503 rather than into a blank screen.
    """
    with sandbox() as (db, owned, generation):
        user = _create_user(db, owned)
        a = _create_product(db, owned, price="500.00", cost="200.00")
        b = _create_product(db, owned, price="300.00", cost=None)
        _create_order(db, owned, user, [(a, 2), (b, 1)], discount="100.00", tax="90.00")
        db.commit()
        _seed_rollups(db, generation)

        service = AnalyticsViewService(db, _FinanceReader())  # type: ignore[arg-type]
        filters = AnalyticsFilters(
            period=Period.CUSTOM,
            date_from=WINDOW_FROM,
            date_to=WINDOW_TO,
            comparison=Comparison.PREVIOUS_PERIOD,
        )

        for number, module_slug, view_slug in FINANCE_VIEWS:
            envelope = service.resolve_view(
                module_slug, view_slug, filters, use_cache=False
            )
            assert isinstance(envelope, AnalyticsViewEnvelope), (
                f"view {number} ({view_slug}) returned "
                f"{type(envelope).__name__}; it is not gated and must carry data"
            )
            assert envelope.view.number == number
            # The data envelope's three blocks are always present, even when a
            # block is legitimately empty.
            assert isinstance(envelope.kpis, dict)
            assert isinstance(envelope.series, dict)
            assert isinstance(envelope.tables, dict)
            assert envelope.sources, f"view {number} reported no provenance"
            assert envelope.currency == "INR"


def test_the_cascade_views_carry_the_missing_cost_rules_through_the_envelope():
    """The actionable message has to survive the envelope, not just the resolver."""
    with sandbox() as (db, owned, generation):
        user = _create_user(db, owned)
        product = _create_product(db, owned, price="500.00", cost="200.00")
        _create_order(db, owned, user, [(product, 1)])
        db.commit()
        _seed_rollups(db, generation)

        service = AnalyticsViewService(db, _FinanceReader())  # type: ignore[arg-type]
        filters = AnalyticsFilters(
            period=Period.CUSTOM,
            date_from=WINDOW_FROM,
            date_to=WINDOW_TO,
            comparison=Comparison.NONE,
        )
        envelope = service.resolve_view(
            "sales-finance", "contribution-margin", filters, use_cache=False
        )

        assert envelope.kpis["cm2"].value is None
        assert set(envelope.kpis["cm2"].inputs_missing) == {
            CostType.GATEWAY_FEE,
            CostType.PACKAGING,
            CostType.HANDLING,
            CostType.FORWARD_SHIPPING,
        }
        assert envelope.quality == MetricQuality.INCOMPLETE.value
        assert envelope.is_partial is True
        assert envelope.availability == ViewState.PARTIAL.value

        codes = [w.code for w in envelope.warnings]
        assert WarningCode.COST_RULE_MISSING in codes
        assert "MARGIN_LIVE_BASIS" in codes

        # And the pruning: the cards the cascade filled must not also be
        # reported as unbound metrics.
        for w in envelope.warnings:
            if w.code == "METRIC_NOT_BOUND":
                assert "cm2" not in w.detail.get("metrics", [])
                assert "gateway_fees" not in w.detail.get("metrics", [])


def test_view_47_keeps_its_product_table_while_the_cascade_fills_the_cards():
    """The cascade ADDS to a view; it must not replace what already worked."""
    with sandbox() as (db, owned, generation):
        user = _create_user(db, owned)
        product = _create_product(db, owned, price="500.00", cost="200.00")
        _create_order(db, owned, user, [(product, 1)])
        db.commit()
        _seed_rollups(db, generation)

        result = margin_cascade(_ctx(db, 47, generation))

        assert "pricing_table" in result.tables
        rows = result.tables["pricing_table"].rows
        assert rows and rows[0]["sku"] == "SKU-FINVIEW-ROLLUP"
        # Unmapped columns stay unmapped: an absent key reads as "not
        # available", a zero would read as a free product.
        assert "cost" not in rows[0]
        assert "price" not in rows[0]

        # And the cards now come from the engine.
        assert result.kpis["cogs"].value == Decimal("200.00")
        assert result.kpis["gross_margin_pct"].value == Decimal("60.00")
        assert result.kpis["cm1_pct"].value == result.kpis["gross_margin_pct"].value
        # `average_selling_price` has no stored per-unit price and stays named
        # as unavailable rather than being approximated.
        assert result.kpis["average_selling_price"].value is None


def test_the_previous_period_comparison_runs_the_engine_over_both_windows():
    """A delta is only honest when both sides are measured the same way."""
    with sandbox() as (db, owned, generation):
        user = _create_user(db, owned)
        product = _create_product(db, owned, price="500.00", cost="200.00")
        # One order in the window, one in the ten days before it.
        _create_order(db, owned, user, [(product, 2)])
        _create_order(
            db,
            owned,
            user,
            [(product, 1)],
            created_at=datetime(2012, 3, 25, 9, 0, tzinfo=timezone.utc),
        )
        db.commit()
        _seed_rollups(db, generation)

        result = margin_cascade(
            _ctx(db, 67, generation, comparison=Comparison.PREVIOUS_PERIOD)
        )
        cm1 = result.kpis["cm1"]

        assert cm1.value == Decimal("600.00")
        assert cm1.previous == Decimal("300.00")
        assert cm1.delta_pct == Decimal("100.0")


def test_a_category_filter_is_reported_as_unhonoured_rather_than_half_applied():
    """The cascade cannot narrow to a category, and must not pretend otherwise.

    `MarginService` refuses line-grained scopes because the order-level income
    terms cannot be attributed to a line. Silently ignoring the filter would let
    a reader take a store-wide margin for a category margin.
    """
    with sandbox() as (db, owned, generation):
        user = _create_user(db, owned)
        product = _create_product(db, owned, price="500.00", cost="200.00")
        _create_order(db, owned, user, [(product, 1)])
        db.commit()
        _seed_rollups(db, generation)

        result = margin_cascade(_ctx(db, 67, generation, category_id=7))

        notices = [
            w
            for w in result.warnings
            if w.code == "METRIC_NOT_BOUND"
            and "category" in w.detail.get("ignored_by_cascade", [])
        ]
        assert notices, (
            "a category filter reached a cascade that cannot apply it and "
            "nothing said so"
        )
        assert "UNFILTERED" in notices[0].message
