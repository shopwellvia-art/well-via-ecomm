"""Tests for shadow mode — the legacy-vs-new comparison that gates retirement.

What actually has to be true here
---------------------------------
Shadow mode's only job is to tell two things apart: a difference that exists
because a definition was corrected, and a difference that exists because
something is broken. Getting the arithmetic right is table stakes. The tests
that carry the weight are the ones about the *classification*:

  * ``test_corrupted_rollup_is_unexplained_and_alerts`` — the defect-detection
    path. A rollup nudged by ₹500 must come back UNEXPLAINED with an open
    ``analytics_alerts`` row, not absorbed by whichever registered explanation
    happens to apply to that metric.
  * ``test_an_unrecognised_delta_defaults_to_unexplained`` — a bridge term
    naming an explanation that is not in the register contributes **nothing**.
    This is the property the whole module rests on. If it ever inverts, every
    other test here passes while the job silently stops being evidence.
  * ``test_an_explanation_not_permitted_for_the_metric_is_rejected`` — the same
    default, one level subtler: a real explanation used on the wrong metric.
  * ``test_the_parity_anchor_admits_no_explanation`` — the anchor is compared at
    zero tolerance with an empty permitted set, so it cannot be explained away.

Isolation strategy
------------------
Every fixture lives in **February/March 2006**, years before this store's first
order and clear of every other suite's sandbox (2005, 2007, 2008, 2009, 2011,
2012, 2013 are taken). Assertions are absolute, not deltas against whatever the
shared throwaway MySQL already holds, so ``_assert_sandbox_is_empty`` fails
loudly if anything foreign is in the window — a stray order would not make these
tests noisy, it would make them wrong.

No db fixture exists in ``conftest.py``; each test owns its ``SessionLocal()``
and closes it in ``finally``. Teardown runs through a *fresh* session so a
half-rolled-back transaction cannot skip it, and deletes every owned row:
orders, items, products, categories, users, cost rules, the ``agg_*`` rows for
the sandbox buckets, the ``analytics_sync_runs`` written under this module's
worker id and job name, and the ``analytics_alerts`` raised for the sandbox
buckets.

``CostRuleResolver`` caches resolutions in Redis for 60s, **including misses**,
and these tests reuse the same dates with and without rules. Every seed and
teardown calls ``invalidate_all()``.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.analytics_control import (
    AlertStatus,
    AnalyticsAlert,
    AnalyticsCostRule,
    CostQuality,
    CostScope,
    CostType,
    CostUnit,
)
from app.models.analytics_rollups import AggOrderDaily
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Category, Product
from app.models.user import User
from app.services.analytics.aggregation import AggregationRunner
from app.services.analytics.cost_rules import CostRuleResolver
from app.services.analytics.shadow import (
    ANCHOR_METRIC,
    CONSECUTIVE_DAYS_REQUIRED,
    EXPECTED_DIFFERENCE_REGISTER,
    GATE_METRICS,
    BridgeTerm,
    Difference,
    DifferenceKind,
    MetricComparison,
    ShadowReport,
    _legacy_profit,
    classify,
    compare,
    is_ready_to_retire_legacy,
    verify_register_is_documented,
)
from app.services.analytics.timebox import (
    active_generation,
    day_bounds_utc,
    store_timezone,
)

# ---------------------------------------------------------------------------
# The 2006 sandbox
# ---------------------------------------------------------------------------

#: Store-local reporting days, half-open, matching every other date range here.
WINDOW_FROM = date(2006, 2, 6)
WINDOW_TO = date(2006, 2, 9)  # three reporting days
WINDOW_DAYS = [WINDOW_FROM + timedelta(days=n) for n in range((WINDOW_TO - WINDOW_FROM).days)]

#: A separate window for the refund test, so a refunded order in one cannot
#: perturb the other.
REFUND_FROM = date(2006, 3, 6)
REFUND_TO = date(2006, 3, 9)
REFUND_DAYS = [REFUND_FROM + timedelta(days=n) for n in range((REFUND_TO - REFUND_FROM).days)]

ALL_SANDBOX_DAYS = WINDOW_DAYS + REFUND_DAYS

RULE_FROM = date(2006, 1, 1)
#: Deliberately CLOSED. An open-ended rule would reach into the present and
#: perturb every other suite that reads cost rules.
RULE_TO = date(2006, 12, 31)
RULE_SOURCE = "test_analytics_shadow"
WORKER_ID = "shadow-test"

#: (cost_type, unit, value) — every input CM2/CM3 needs, so the full cascade
#: resolves and the CM bridges are exercised rather than short-circuited by
#: `missing_cost_input`.
COST_RULES: tuple[tuple[str, str, Decimal], ...] = (
    (CostType.GATEWAY_FEE, CostUnit.PCT, Decimal("2.0")),
    (CostType.PACKAGING, CostUnit.PER_ORDER, Decimal("15")),
    (CostType.HANDLING, CostUnit.PER_ORDER, Decimal("10")),
    (CostType.FORWARD_SHIPPING, CostUnit.PER_ORDER, Decimal("50")),
    (CostType.RETURN_SHIPPING, CostUnit.PER_ORDER, Decimal("45")),
    (CostType.RTO_LOGISTICS, CostUnit.PER_ORDER, Decimal("90")),
    (CostType.MARKETPLACE_COMMISSION, CostUnit.PCT, Decimal("0")),
    (CostType.MARKETING_SPEND, CostUnit.PER_MONTH, Decimal("280")),
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


class _Owned:
    """Ids this test created, so teardown deletes exactly them and nothing else."""

    def __init__(self) -> None:
        self.orders: list[int] = []
        self.products: list[int] = []
        self.categories: list[int] = []
        self.users: list[int] = []
        self.rules: list[int] = []


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _create_user(db: Session, owned: _Owned) -> User:
    user = User(
        email=f"shadowtest-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.flush()
    owned.users.append(user.id)
    return user


def _create_category(db: Session, owned: _Owned, label: str) -> Category:
    suffix = _uid()
    category = Category(name=f"{label} {suffix}", slug=f"{label.lower()}-{suffix}")
    db.add(category)
    db.flush()
    owned.categories.append(category.id)
    return category


def _create_product(
    db: Session,
    owned: _Owned,
    *,
    price: str,
    cost: str | None,
    category: Category | None = None,
) -> Product:
    product = Product(
        sku=f"SKU-SHADOW-{_uid()}",
        name=f"ShadowTestProduct {_uid()}",
        price=Decimal(price),
        cost=None if cost is None else Decimal(cost),
        stock=100,
        category_id=category.id if category else None,
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
    created_at: datetime,
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

    ``total_amount = gross - discounts + tax + shipping + cod_surcharge``, which
    is what makes the aggregation job's bridge check a real assertion about the
    rows rather than a restatement of how they were built.
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


def _seed_cost_rules(db: Session, owned: _Owned) -> None:
    for cost_type, unit, value in COST_RULES:
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
                note="shadow test fixture",
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
    CostRuleResolver(db).invalidate_all()


def _assert_sandbox_is_empty(db: Session) -> None:
    """Fail loudly if anything foreign already lives in the 2006 sandbox.

    Every expected figure below is absolute, so a stray order would not make
    these tests noisy — it would make them wrong.
    """
    start = datetime(2006, 1, 1, tzinfo=timezone.utc)
    end = datetime(2007, 1, 1, tzinfo=timezone.utc)
    stray = db.execute(
        select(Order.id).where(Order.created_at >= start, Order.created_at < end)
    ).scalars().all()
    if stray:
        pytest.fail(
            "orders this module did not create already live in the 2006 sandbox and "
            f"would change every expected figure: {stray[:10]}"
        )
    foreign_rules = [
        r
        for r in db.execute(
            select(AnalyticsCostRule).where(
                AnalyticsCostRule.effective_from <= RULE_TO,
                or_(
                    AnalyticsCostRule.effective_to.is_(None),
                    AnalyticsCostRule.effective_to >= RULE_FROM,
                ),
            )
        ).scalars()
        if (r.source or "") != RULE_SOURCE
    ]
    if foreign_rules:
        pytest.fail(
            "cost rules not owned by this test cover the 2006 fixture window: "
            + ", ".join(f"#{r.id} {r.cost_type}" for r in foreign_rules)
        )


def _aggregate(db: Session, days: list[date]) -> None:
    """Build the rollups the new pages read, one bucket at a time."""
    runner = AggregationRunner(db, worker_id=WORKER_ID)
    for day in days:
        runner.run_bucket("order_daily", day)
        runner.run_bucket("product_daily", day)
    db.commit()


def _cleanup(owned: _Owned) -> None:
    with SessionLocal() as s:
        if owned.orders:
            s.execute(
                text("DELETE FROM order_items WHERE order_id IN :ids"),
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
        if owned.categories:
            s.execute(
                text("DELETE FROM categories WHERE id IN :ids"),
                {"ids": tuple(owned.categories)},
            )
        if owned.users:
            s.execute(
                text("DELETE FROM users WHERE id IN :ids"),
                {"ids": tuple(owned.users)},
            )
        s.execute(
            text("DELETE FROM analytics_cost_rules WHERE source = :src"),
            {"src": RULE_SOURCE},
        )
        s.execute(
            text("DELETE FROM agg_order_daily WHERE bucket_date IN :days"),
            {"days": tuple(ALL_SANDBOX_DAYS)},
        )
        s.execute(
            text("DELETE FROM agg_product_daily WHERE bucket_date IN :days"),
            {"days": tuple(ALL_SANDBOX_DAYS)},
        )
        s.execute(
            text("DELETE FROM agg_order_hourly WHERE bucket_date IN :days"),
            {"days": tuple(ALL_SANDBOX_DAYS)},
        )
        s.execute(
            text(
                "DELETE FROM analytics_sync_runs WHERE worker_id = :w OR job = :job"
            ),
            {"w": WORKER_ID, "job": "shadow_compare"},
        )
        s.execute(
            text(
                "DELETE FROM analytics_alerts WHERE dimension = 'shadow_metric' "
                "AND bucket_date IN :days"
            ),
            {"days": tuple(ALL_SANDBOX_DAYS)},
        )
        s.commit()
    CostRuleResolver(SessionLocal()).invalidate_all()


def _midday(db: Session, day: date, hours: int = 12) -> datetime:
    """A UTC instant ``hours`` into the store-local reporting day ``day``."""
    start, _ = day_bounds_utc(day, store_timezone(db))
    return start + timedelta(hours=hours)


# ---------------------------------------------------------------------------
# 1. Identical inputs
# ---------------------------------------------------------------------------


def test_identical_inputs_produce_no_unexplained_differences() -> None:
    """The base case, and the one that makes every other result meaningful.

    Plain paid orders, placed mid reporting-day so no window boundary is in
    play, with every cost rule present. Both systems are looking at the same
    rows and there is nothing for them to legitimately disagree about beyond the
    documented definitional differences, so the residual on every metric must be
    zero and no alert may be raised.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_sandbox_is_empty(db)
        _seed_cost_rules(db, owned)
        user = _create_user(db, owned)
        category = _create_category(db, owned, "Wellness")
        product = _create_product(
            db, owned, price="500.00", cost="200.00", category=category
        )
        for day in WINDOW_DAYS:
            _create_order(
                db,
                owned,
                user,
                [(product, 2)],
                created_at=_midday(db, day),
                discount="50.00",
                shipping="40.00",
                tax="0",
            )
        db.commit()
        _aggregate(db, WINDOW_DAYS)

        report = compare(db, WINDOW_FROM, WINDOW_TO, worker_id=WORKER_ID)
        db.commit()

        assert report.unexplained == (), (
            "identical inputs must produce no unexplained difference; got:\n"
            + "\n".join(d.describe() for d in report.unexplained)
        )
        assert report.is_clean
        assert report.missing_buckets == ()

        ok, reasons = report.gate_metrics_match()
        assert ok, reasons

        # The headline figures agree outright, not merely "within explanation".
        anchor = report.comparison(ANCHOR_METRIC)
        assert anchor is not None and anchor.delta == 0
        paid = report.comparison("paid_order_value")
        # 3 orders x (2 x 500 - 50 + 40) = 3 x 990.00 = 2970.00
        assert paid is not None
        assert paid.new == 297000 and paid.legacy == 297000
        orders = report.comparison("orders")
        assert orders is not None and orders.new == 3 and orders.legacy == 3

        # No alert may exist for a clean window.
        assert report.alert_ids == ()
        raised = db.execute(
            select(AnalyticsAlert.id).where(
                AnalyticsAlert.dimension == "shadow_metric",
                AnalyticsAlert.bucket_date == WINDOW_FROM,
            )
        ).scalars().all()
        assert raised == []

        # The run was logged. A comparison that did not run must be
        # distinguishable from one that ran clean.
        assert report.sync_run_id is not None
    finally:
        db.close()
        _cleanup(owned)


# ---------------------------------------------------------------------------
# 2. Timezone boundary
# ---------------------------------------------------------------------------


def test_timezone_boundary_order_is_an_expected_difference() -> None:
    """An order across the store-local midnight, matched to its explanation.

    Two orders, 90 minutes apart on the clock, on opposite sides of local
    midnight:

    * **23:00 IST** on the last reporting day — 17:30 UTC the *same* day. This
      one is inside **both** windows and must move nothing. It is the negative
      control: a test that cannot say which orders do *not* move is not testing
      the boundary.
    * **00:30 IST** on the first reporting day — 19:00 UTC the *previous*
      evening. Store-local it is day one of the window; on the legacy UTC
      window, which starts at 00:00 UTC, it is 5 hours too early and falls out
      entirely.

    The second one must produce an EXPECTED difference on the ``tz`` component,
    matched to ``tz_bucketing``, attributed to the paisa, with the order itself
    listed in ``boundary_orders`` as the evidence.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_sandbox_is_empty(db)
        tz = store_timezone(db)
        aligned_start, _ = day_bounds_utc(WINDOW_FROM, tz)
        legacy_start = datetime(
            WINDOW_FROM.year, WINDOW_FROM.month, WINDOW_FROM.day, tzinfo=timezone.utc
        )
        if aligned_start >= legacy_start:
            pytest.skip(
                f"store timezone {tz} has no positive UTC offset, so there is no "
                "boundary to test"
            )

        _seed_cost_rules(db, owned)
        user = _create_user(db, owned)
        product = _create_product(db, owned, price="400.00", cost="100.00")

        # The control: 23:00 store-local on the window's last reporting day.
        control_at = _midday(db, WINDOW_DAYS[-1], hours=23)
        _create_order(db, owned, user, [(product, 1)], created_at=control_at)

        # The boundary: 00:30 store-local on the first reporting day, which is
        # the previous evening in UTC and therefore outside the legacy window.
        boundary_at = aligned_start + timedelta(minutes=30)
        boundary = _create_order(
            db, owned, user, [(product, 3)], created_at=boundary_at
        )
        db.commit()
        _aggregate(db, WINDOW_DAYS)

        report = compare(db, WINDOW_FROM, WINDOW_TO, worker_id=WORKER_ID)
        db.commit()

        # The control sits inside both windows.
        assert report.legacy_window[0] <= control_at < report.legacy_window[1]
        assert report.aligned_window[0] <= control_at < report.aligned_window[1]

        # The boundary order sits in exactly one, and is offered as evidence.
        assert not (report.legacy_window[0] <= boundary_at < report.legacy_window[1])
        assert report.aligned_window[0] <= boundary_at < report.aligned_window[1]
        evidence = [
            row for row in report.boundary_orders if row["order_id"] == boundary.id
        ]
        assert evidence, report.boundary_orders
        assert evidence[0]["only_in"] == "aligned_window"

        tz_differences = [
            d
            for d in report.differences_for("paid_order_value")
            if d.component == "tz"
        ]
        assert len(tz_differences) == 1, report.differences_for("paid_order_value")
        difference = tz_differences[0]
        assert difference.kind is DifferenceKind.EXPECTED
        assert difference.explanations == ("tz_bucketing",)
        assert difference.residual == 0
        # 3 x 400.00, the whole of the boundary order and nothing else.
        assert difference.delta == 120000
        assert difference.explained == 120000

        # The explanation it was matched to is a real, registered entry that
        # names this metric.
        entry = EXPECTED_DIFFERENCE_REGISTER["tz_bucketing"]
        assert "paid_order_value" in entry.applies_to
        assert entry.correct_side == "new"

        assert report.is_clean, [d.describe() for d in report.unexplained]
    finally:
        db.close()
        _cleanup(owned)


# ---------------------------------------------------------------------------
# 3. Revenue recognition
# ---------------------------------------------------------------------------


def test_refunded_order_is_an_expected_recognition_difference() -> None:
    """A sale refunded *after* the window stays in the window it was made in.

    The legacy rule is a status filter, so the moment the order became REFUNDED
    it left February retroactively — a closed month changed because of a March
    event. The corrected rule keeps the sale where it happened and books the
    reversal in the period of its own ``refunded_at``.

    The refund is dated a month later on purpose. Put both events in one window
    and they net to zero and match legacy's zero, which proves nothing; the
    correction is only visible when the two periods are different, which is
    exactly the case that was wrong.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_sandbox_is_empty(db)
        _seed_cost_rules(db, owned)
        user = _create_user(db, owned)
        product = _create_product(db, owned, price="600.00", cost="250.00")

        _create_order(
            db, owned, user, [(product, 1)], created_at=_midday(db, REFUND_DAYS[0])
        )
        refunded = _create_order(
            db,
            owned,
            user,
            [(product, 2)],
            created_at=_midday(db, REFUND_DAYS[1]),
            status=OrderStatus.REFUNDED,
            # Dated a month after the window: the reversal is a April event.
            refunded_at=datetime(2006, 4, 12, 9, 0, tzinfo=timezone.utc),
        )
        db.commit()
        _aggregate(db, REFUND_DAYS)

        report = compare(db, REFUND_FROM, REFUND_TO, worker_id=WORKER_ID)
        db.commit()

        # Legacy drops the refunded order; the corrected definition keeps it.
        net = report.comparison("net_revenue")
        assert net is not None
        assert net.legacy == 60000, "legacy sees only the un-refunded order"
        assert net.new == 60000 + 120000, "the corrected rule keeps the sale"

        differences = [
            d
            for d in report.differences_for("net_revenue")
            if d.component == "definitional"
        ]
        assert len(differences) == 1, report.differences_for("net_revenue")
        difference = differences[0]
        assert difference.kind is DifferenceKind.EXPECTED
        assert "revenue_recognition" in difference.explanations
        assert difference.delta == 120000
        assert difference.residual == 0

        entry = EXPECTED_DIFFERENCE_REGISTER["revenue_recognition"]
        assert entry.correct_side == "new"
        assert "net_revenue" in entry.applies_to

        # The parity anchor must NOT move: it keeps the legacy rule on purpose,
        # so a refunded order leaves it entirely. If recognition reached the
        # anchor, the reconciliation against the legacy pages would be worthless.
        anchor = report.comparison(ANCHOR_METRIC)
        assert anchor is not None and anchor.delta == 0
        assert anchor.new == 60000
        assert refunded.id not in [row["order_id"] for row in report.boundary_orders]
    finally:
        db.close()
        _cleanup(owned)


def test_rollup_net_revenue_agrees_with_the_service_on_a_same_window_refund() -> None:
    """The rollup and the service must agree. This test found the bug that broke it.

    An order placed and fully refunded inside one window nets to **zero**:
    ``MarginService.recognised_revenue`` recognises the sale (₹1,000) and
    subtracts the reversal (₹1,000). ``agg_order_daily.net_revenue`` reports
    **-₹1,000**, because ``OrderDailyJob._money`` builds it from the LEGACY
    ``paid_order_value`` — which already excluded the refunded order — and then
    subtracts the refund again. That is the double reversal ``margin.py``'s
    docstring says was fixed, still live in the table the dashboard reads.

    This is exactly why ``net_revenue`` is compared against **both** sources: a
    correction that lands in a service and not in the rollup that feeds the
    screen has not landed, and comparing only one of them would miss it.

    ``jobs.py`` is not this change's to edit. **When it is fixed**, the rollup
    figure becomes 0, the unexplained difference disappears, and the assertion
    below flips to ``report.is_clean`` — which is the point at which this test
    stops describing a defect and starts describing agreement.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_sandbox_is_empty(db)
        user = _create_user(db, owned)
        product = _create_product(db, owned, price="1000.00", cost="400.00")
        placed = _midday(db, REFUND_DAYS[0], hours=10)
        _create_order(
            db,
            owned,
            user,
            [(product, 1)],
            created_at=placed,
            status=OrderStatus.REFUNDED,
            # Reversed the next day — same window, so the two events net to zero.
            refunded_at=placed + timedelta(hours=24),
        )
        db.commit()
        _aggregate(db, REFUND_DAYS)

        report = compare(db, REFUND_FROM, REFUND_TO, worker_id=WORKER_ID)
        db.commit()

        service = report.comparison("net_revenue")
        assert service is not None
        assert service.new == 0, "the sale and its reversal net to zero"
        assert report.differences_for("net_revenue") == (), (
            "the corrected definition agrees with the legacy page here: legacy "
            "drops the order, the new rule books it and reverses it"
        )

        rollup = report.comparison("net_revenue_rollup")
        assert rollup is not None
        assert rollup.new == 0, (
            "agg_order_daily.net_revenue must net a same-window refund to zero, "
            "exactly as MarginService does. It reported -100000 until "
            "OrderDailyJob._money was changed to build net_revenue from the "
            "RECOGNISED order value instead of the legacy paid_order_value — the "
            "legacy figure has already dropped the refunded order, so subtracting "
            "the refund from it reversed the same sale twice."
        )
        assert rollup.new == service.new, (
            "rollup and service must not hold two different revenue truths"
        )
        caught = [d for d in report.unexplained if d.metric == "net_revenue_rollup"]
        assert len(caught) == 0, (
            "a corrected rollup raises no unexplained variance. While the bug was "
            "live this list held one entry with residual -100000, and that is how "
            "shadow mode surfaced it: the defect was in the table the dashboard "
            "reads, not in the service, which is why net_revenue is compared "
            f"against BOTH sources. Still unexplained: {caught}"
        )
    finally:
        db.close()
        _cleanup(owned)


# ---------------------------------------------------------------------------
# 4. The defect-detection path
# ---------------------------------------------------------------------------


def test_corrupted_rollup_is_unexplained_and_alerts() -> None:
    """The whole point of the job: a wrong number is caught and named.

    ``agg_order_daily.paid_order_value`` is nudged by ₹500 — a plausible
    magnitude, in the flattering direction, on one bucket out of three. There is
    no registered explanation that could account for it, so it must come back
    UNEXPLAINED with the residual stated, raise an OPEN alert, and mark the run
    ``partial`` rather than ``success``.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_sandbox_is_empty(db)
        _seed_cost_rules(db, owned)
        user = _create_user(db, owned)
        product = _create_product(db, owned, price="500.00", cost="200.00")
        for day in WINDOW_DAYS:
            _create_order(
                db, owned, user, [(product, 2)], created_at=_midday(db, day)
            )
        db.commit()
        _aggregate(db, WINDOW_DAYS)

        clean = compare(db, WINDOW_FROM, WINDOW_TO, worker_id=WORKER_ID)
        assert clean.is_clean, [d.describe() for d in clean.unexplained]
        db.commit()

        generation = int(active_generation(db).generation)
        row = db.execute(
            select(AggOrderDaily).where(
                AggOrderDaily.bucket_date == WINDOW_DAYS[1],
                AggOrderDaily.tz_generation == generation,
            )
        ).scalar_one()
        row.paid_order_value = row.paid_order_value + Decimal("500.00")
        db.commit()

        report = compare(db, WINDOW_FROM, WINDOW_TO, worker_id=WORKER_ID)
        db.commit()

        unexplained = [
            d for d in report.unexplained if d.metric == "paid_order_value"
        ]
        assert len(unexplained) == 1, [d.describe() for d in report.unexplained]
        difference = unexplained[0]
        assert difference.kind is DifferenceKind.UNEXPLAINED
        assert difference.explanations == ()
        assert difference.delta == 50000
        assert difference.residual == 50000, (
            "the residual must be the whole delta — no registered explanation "
            "may absorb any part of a corrupted rollup"
        )

        # The gate metrics no longer match, and say so specifically.
        ok, reasons = report.gate_metrics_match()
        assert not ok
        assert any("paid_order_value differs by 500.00" in r for r in reasons), reasons

        # An alert was raised, is OPEN, and carries enough to triage without
        # reproducing the query.
        assert report.alert_ids
        alert = db.execute(
            select(AnalyticsAlert).where(AnalyticsAlert.id == report.alert_ids[0])
        ).scalar_one()
        assert alert.status == AlertStatus.OPEN
        assert alert.dimension == "shadow_metric"
        assert alert.bucket_date == WINDOW_FROM
        # 3 orders x 2 x 500.00 = 3000.00 on the legacy side, 3500.00 in the
        # nudged rollup, and the band is the legacy figure +/- one paisa.
        assert alert.actual_value == Decimal("3500.00")
        assert alert.expected_low == Decimal("2999.99")
        assert alert.expected_high == Decimal("3000.01")
        assert alert.context["residual"] == 50000
        assert alert.context["new_source"] == "agg_order_daily.paid_order_value"

        # Re-running must not create a second copy of a divergence someone is
        # already looking at.
        again = compare(db, WINDOW_FROM, WINDOW_TO, worker_id=WORKER_ID)
        db.commit()
        assert set(again.alert_ids) == set(report.alert_ids)
    finally:
        db.close()
        _cleanup(owned)


# ---------------------------------------------------------------------------
# 5-6, 9. The retirement gate
# ---------------------------------------------------------------------------


def _synthetic_report(
    day: date, *, unexplained: tuple[Difference, ...] = (), delta: int = 0
) -> ShadowReport:
    """A one-day report with the gate metrics matching (unless `delta` says not)."""
    comparisons = tuple(
        MetricComparison(
            metric=metric,
            unit="count" if metric == "orders" else "money",
            legacy=100000,
            legacy_aligned=100000,
            new=100000 + delta,
            permitted=frozenset(),
            tolerance_override=0 if metric == ANCHOR_METRIC else None,
        )
        for metric in GATE_METRICS
    )
    return ShadowReport(
        date_from=day,
        date_to=day + timedelta(days=1),
        generated_at=datetime(2006, 5, 1, tzinfo=timezone.utc),
        timezone_name="Asia/Kolkata",
        tz_generation=1,
        legacy_window=(
            datetime(2006, 5, 1, tzinfo=timezone.utc),
            datetime(2006, 5, 2, tzinfo=timezone.utc),
        ),
        aligned_window=(
            datetime(2006, 5, 1, tzinfo=timezone.utc),
            datetime(2006, 5, 2, tzinfo=timezone.utc),
        ),
        comparisons=comparisons,
        differences=unexplained,
    )


def _clean_history(days: int, end: date = date(2006, 6, 30)) -> list[ShadowReport]:
    return [
        _synthetic_report(end - timedelta(days=n)) for n in range(days - 1, -1, -1)
    ]


def test_gate_passes_on_thirty_clean_consecutive_days() -> None:
    """The gate can actually pass, so its refusals mean something.

    A gate that never opens is indistinguishable from a gate that is broken, and
    every "not ready" assertion below would be vacuous without this.
    """
    ready, reasons = is_ready_to_retire_legacy(_clean_history(CONSECUTIVE_DAYS_REQUIRED))
    assert ready, reasons
    assert reasons == []


def test_gate_refuses_with_an_open_unexplained_delta_and_says_which() -> None:
    """Thirty days of evidence, one unexplained delta, still not ready."""
    history = _clean_history(CONSECUTIVE_DAYS_REQUIRED)
    dirty_day = history[-3].date_from
    history[-3] = _synthetic_report(
        dirty_day,
        unexplained=(
            Difference(
                metric="paid_order_value",
                kind=DifferenceKind.UNEXPLAINED,
                component="definitional",
                delta=50000,
                explained=0,
                bounded=0,
                residual=50000,
                unit="money",
                note="the rollup does not match the legacy page",
            ),
        ),
    )

    ready, reasons = is_ready_to_retire_legacy(history)
    assert not ready
    assert reasons, "'not ready' without a reason is unactionable"
    assert any(
        "unexplained" in r and "paid_order_value" in r and str(dirty_day) in r
        for r in reasons
    ), reasons
    # And the reason names the size, so it can be triaged without re-running.
    assert any("500.00" in r for r in reasons), reasons


def test_gate_refuses_with_less_than_thirty_days_and_names_how_many_remain() -> None:
    """Short history: the reason has to be a number, not 'not yet'."""
    history = _clean_history(12)
    ready, reasons = is_ready_to_retire_legacy(history)
    assert not ready
    assert len(reasons) == 1, reasons
    reason = reasons[0]
    assert f"only 12 of {CONSECUTIVE_DAYS_REQUIRED}" in reason, reason
    assert "18 more day(s)" in reason, reason

    # No history at all is a different, equally specific message.
    ready, reasons = is_ready_to_retire_legacy([])
    assert not ready
    assert reasons and f"{CONSECUTIVE_DAYS_REQUIRED} still required" in reasons[0]


def test_gate_refuses_when_a_registered_explanation_is_not_written_down(
    tmp_path,
) -> None:
    """'Documented' means prose in RECONCILIATION.md, not a Python string.

    Pointed at an empty register file, thirty otherwise-clean days must still
    fail — and name every explanation with no section.
    """
    empty = tmp_path / "RECONCILIATION.md"
    empty.write_text("# nothing here\n", encoding="utf-8")
    ready, reasons = is_ready_to_retire_legacy(
        _clean_history(CONSECUTIVE_DAYS_REQUIRED), doc_path=empty
    )
    assert not ready
    for key in EXPECTED_DIFFERENCE_REGISTER:
        assert any(f"'{key}'" in r for r in reasons), (key, reasons)


def test_every_registered_explanation_is_documented() -> None:
    """The real register file backs every explanation the code can produce."""
    assert verify_register_is_documented() == []


# ---------------------------------------------------------------------------
# 7-8. Classification defaults — the properties everything else rests on
# ---------------------------------------------------------------------------


def _comparison(**kwargs) -> MetricComparison:
    base = {
        "metric": "paid_order_value",
        "unit": "money",
        "legacy": 100000,
        "legacy_aligned": 100000,
        "new": 100000,
        "permitted": frozenset({"tz_bucketing"}),
    }
    base.update(kwargs)
    return MetricComparison(**base)


def test_delta_inside_tolerance_is_not_a_difference() -> None:
    """One paisa is the currency's resolution, not a licence to be wrong.

    A one-paisa gap is the width of the legacy ``Decimal -> float`` boundary and
    is not reported. Two paise is not, and is reported in full.
    """
    assert classify(_comparison(new=100001)) == []
    assert classify(_comparison(new=99999)) == []

    differences = classify(_comparison(new=100002))
    assert len(differences) == 1
    assert differences[0].kind is DifferenceKind.UNEXPLAINED
    assert differences[0].delta == 2

    # Counts have no tolerance at all: one order is one order.
    counted = classify(
        _comparison(metric="orders", unit="count", legacy=10, legacy_aligned=10, new=11)
    )
    assert len(counted) == 1 and counted[0].delta == 1


def test_an_unrecognised_delta_defaults_to_unexplained() -> None:
    """The property the entire module rests on, asserted directly.

    A bridge term naming an explanation that is not in the register contributes
    **nothing**: it cannot reduce the residual, so the delta it claimed stays
    unexplained. If this ever inverts, an unknown delta becomes silently
    EXPECTED and the job stops being evidence while every other test still
    passes.
    """
    differences = classify(
        _comparison(
            new=150000,
            terms=(
                BridgeTerm(
                    explanation="the_moon_was_full",
                    amount=50000,
                    note="not in the register",
                ),
            ),
        )
    )
    assert len(differences) == 1
    difference = differences[0]
    assert difference.kind is DifferenceKind.UNEXPLAINED
    assert difference.explained == 0
    assert difference.residual == 50000, "the bogus term must absorb nothing"
    assert difference.explanations == ()
    assert "the_moon_was_full" in difference.note
    assert "the_moon_was_full" not in EXPECTED_DIFFERENCE_REGISTER


def test_an_explanation_not_permitted_for_the_metric_is_rejected() -> None:
    """A real explanation used on the wrong metric is still no explanation.

    ``category_snapshot`` is registered and correct — for ``revenue_by_category``.
    Reaching for it to explain a headline revenue delta must fail exactly as an
    invented key does, which is what stops the register from becoming a list of
    excuses that apply to everything.
    """
    differences = classify(
        _comparison(
            new=150000,
            terms=(BridgeTerm(explanation="category_snapshot", amount=50000),),
        )
    )
    assert len(differences) == 1
    assert differences[0].kind is DifferenceKind.UNEXPLAINED
    assert differences[0].residual == 50000
    assert "category_snapshot" in EXPECTED_DIFFERENCE_REGISTER
    assert "category_snapshot" in differences[0].note

    # Permitted and registered: the same term now accounts for the whole delta.
    allowed = classify(
        _comparison(
            new=150000,
            permitted=frozenset({"category_snapshot"}),
            terms=(BridgeTerm(explanation="category_snapshot", amount=50000),),
        )
    )
    assert len(allowed) == 1
    assert allowed[0].kind is DifferenceKind.EXPECTED
    assert allowed[0].residual == 0


def test_a_partially_explained_delta_alerts_on_the_remainder() -> None:
    """Explaining ₹900 of a ₹1,000 delta leaves ₹100 unexplained.

    An explanation that covers *most* of a difference must not launder the rest;
    that is how a real defect hides behind a real definitional change.
    """
    differences = classify(
        _comparison(
            new=200000,
            permitted=frozenset({"revenue_recognition"}),
            terms=(BridgeTerm(explanation="revenue_recognition", amount=90000),),
        )
    )
    assert len(differences) == 1
    assert differences[0].kind is DifferenceKind.UNEXPLAINED
    assert differences[0].explained == 90000
    assert differences[0].residual == 10000


def test_a_bound_absorbs_only_up_to_its_own_size() -> None:
    """The one explanation that is not an exact amount is still not a blank cheque."""
    inside = classify(
        _comparison(
            metric="cm1",
            new=100003,
            permitted=frozenset({"paisa_rounding"}),
            terms=(BridgeTerm(explanation="paisa_rounding", bound=5),),
        )
    )
    assert len(inside) == 1
    assert inside[0].kind is DifferenceKind.EXPECTED
    assert inside[0].residual == 0
    assert inside[0].explanations == ("paisa_rounding",)

    outside = classify(
        _comparison(
            metric="cm1",
            new=100050,
            permitted=frozenset({"paisa_rounding"}),
            terms=(BridgeTerm(explanation="paisa_rounding", bound=5),),
        )
    )
    assert len(outside) == 1
    assert outside[0].kind is DifferenceKind.UNEXPLAINED
    assert outside[0].residual == 50


def test_the_parity_anchor_admits_no_explanation() -> None:
    """The anchor is pinned: zero tolerance, empty permitted set.

    ``margin.paid_order_value`` restates ``DashboardService._revenue_summary``
    byte for byte. If it can be explained away, the reconciliation between the
    legacy pages and this subsystem stops meaning anything — so not even a
    registered explanation may be applied to it.
    """
    differences = classify(
        MetricComparison(
            metric=ANCHOR_METRIC,
            unit="money",
            legacy=100000,
            legacy_aligned=100000,
            new=100001,
            permitted=frozenset(),
            tolerance_override=0,
            terms=(BridgeTerm(explanation="tz_bucketing", amount=1),),
        )
    )
    assert len(differences) == 1
    assert differences[0].kind is DifferenceKind.UNEXPLAINED
    assert differences[0].residual == 1
    assert ANCHOR_METRIC in GATE_METRICS


# ---------------------------------------------------------------------------
# The legacy transcription
# ---------------------------------------------------------------------------


def test_legacy_profit_transcription_reproduces_the_cascade() -> None:
    """Pin ``_legacy_profit`` against hand-computed figures.

    ``ProfitService.profit()`` inlines its cascade against ``period_start..now``
    and has no windowed helper, so shadow mode transcribes it. A transcription
    can drift, and a drifted legacy side would report the drift as a defect in
    the new system — so the arithmetic is asserted here, including the two
    inherited defects it is supposed to reproduce.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_sandbox_is_empty(db)
        user = _create_user(db, owned)
        costed = _create_product(db, owned, price="500.00", cost="200.00")
        # No cost snapshot: profit_service COALESCEs this to zero COGS and
        # reports 100% margin on the line. The transcription must do the same.
        uncosted = _create_product(db, owned, price="300.00", cost=None)

        _create_order(
            db,
            owned,
            user,
            [(costed, 2), (uncosted, 1)],
            created_at=_midday(db, WINDOW_DAYS[0]),
            discount="100.00",
            payment_discount="20.00",
            shipping="60.00",
        )
        db.commit()

        tz = store_timezone(db)
        start, _ = day_bounds_utc(WINDOW_FROM, tz)
        _, end = day_bounds_utc(WINDOW_DAYS[-1], tz)
        legacy = _legacy_profit(db, start, end)

        # Line revenue is 2x500 + 1x300 = 1300.00, shipping excluded (the defect).
        assert legacy.line_revenue_minor == 130000
        # COGS is 2x200 only; the uncosted line contributes zero (inherited bug 2).
        assert legacy.product_cost_minor == 40000
        assert legacy.lines == 2 and legacy.costed_lines == 1
        assert legacy.coverage_bp == 5000  # 50.00pp
        # Shipping income counted as a cost (inherited bug 1).
        assert legacy.shipping_minor == 6000
        assert legacy.orders == 1
        assert legacy.marketing_discounts_minor == 12000

        # C1 = revenue - cogs - shipping - packing - handling, all from settings.
        expected_c1 = (
            legacy.line_revenue_minor
            - legacy.product_cost_minor
            - legacy.shipping_minor
            - legacy.packing_minor
            - legacy.handling_minor
        )
        assert abs(legacy.c1_minor - expected_c1) <= 1
        assert abs(legacy.c2_minor - (legacy.c1_minor - legacy.gateway_minor)) <= 1
        expected_c3 = (
            legacy.c2_minor
            - legacy.marketing_discounts_minor
            - legacy.ad_spend_minor
        )
        assert abs(legacy.c3_minor - expected_c3) <= 1
        assert (
            abs(legacy.net_profit_minor - (legacy.c3_minor - legacy.overheads_minor))
            <= 1
        )
    finally:
        db.close()
        _cleanup(owned)


# ---------------------------------------------------------------------------
# Absence is not zero
# ---------------------------------------------------------------------------


def test_a_missing_rollup_bucket_is_reported_rather_than_read_as_zero() -> None:
    """A day with no ``agg_order_daily`` row is not a day that sold nothing.

    A rollup cannot report its own absence: the row is simply not there, the
    chart ends a day early, and the number next to it is quietly short. Shadow
    mode names the missing days and refuses to call the window clean.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_sandbox_is_empty(db)
        _seed_cost_rules(db, owned)
        user = _create_user(db, owned)
        product = _create_product(db, owned, price="500.00", cost="200.00")
        for day in WINDOW_DAYS:
            _create_order(
                db, owned, user, [(product, 1)], created_at=_midday(db, day)
            )
        db.commit()
        # Deliberately aggregate only the first two of the three days.
        _aggregate(db, WINDOW_DAYS[:2])

        report = compare(db, WINDOW_FROM, WINDOW_TO, worker_id=WORKER_ID)
        db.commit()

        assert report.missing_buckets == (WINDOW_DAYS[2],)
        assert not report.is_clean
        coverage = [d for d in report.unexplained if d.metric == "rollup_coverage"]
        assert len(coverage) == 1
        assert "no agg_order_daily row" in coverage[0].note
        assert any(
            "have no agg_order_daily row" in warning for warning in report.warnings
        )
    finally:
        db.close()
        _cleanup(owned)
