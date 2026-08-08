"""Views 42 (fraud and risk) and 71 (website speed) — binding, honesty, percentiles.

Why this module exists
----------------------
A registry audit found view 42 declared ``LIVE`` with **no ``params`` at all**.
``LIVE`` is not a label — the frontend issues a fetch for it and renders a data
page, so the view was promising risk numbers it had no binding to produce. That
is the defect class this file is built to make unreinventable, and
:func:`test_no_live_view_in_the_registry_is_unbound` is the test that matters
most here: it sweeps the whole registry, not just these two views.

The four things asserted, in order of how badly each failure would hurt
----------------------------------------------------------------------
1. **No LIVE view anywhere is unbound.** Silent and permanent when wrong.
2. **View 42's declared state matches what it can produce.** It is PARTIAL now,
   because it cannot see chargebacks or disputes — ``payment_settlements`` is
   not in this deployment's schema and is fed by the same gateway settlement
   report view 64 is gated on. If somebody promotes it back to LIVE, the
   binding assertion here forces them to have wired it first.
3. **Percentiles are computed over raw rows, not re-aggregated.**
   :func:`test_window_p95_is_not_the_average_of_daily_p95s` pins the exact
   failure the implementation avoids: it computes both answers over the same
   fixture and asserts they differ, so a future refactor that "optimises" the
   window figure into an average of the daily points fails here rather than in
   a board deck.
4. **Route-class separation works.** An eleven-second admin export must not
   land in a storefront latency graph.

Isolation strategy
------------------
House style, matching ``test_analytics_view_bindings.py``: no db fixture in
``conftest.py``; every test owning rows takes its own ``SessionLocal()`` and
tears down in a ``finally`` that runs whether or not the body raised.

**Date sandbox: June 1974.** 1990, 1996-1999, 2001-2016, 2018-2019, 2024 and
2026 are all claimed by other modules in this suite; 1974 is claimed by nothing,
predates every real row, and is far enough from the neighbours that a peer's
window cannot overlap this one mid-run.

Rollup fixtures are written under the database's ACTIVE tz generation, because
``AnalyticsViewService`` reads that from the database and cannot be told
otherwise; isolation comes from the date range instead. ``obs_request_logs``,
``payment_events``, ``orders`` and ``order_addresses`` carry no generation at
all, so they are torn down by primary key — collected as they are inserted, so
a partial failure still cleans up exactly what it created and nothing else.

Run inside the analytics container, three times, to catch inter-run leakage::

    docker exec wvana-py python -m pytest tests/test_analytics_risk_speed_views.py -q
"""
from __future__ import annotations

import statistics
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterator

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import analytics_rollups as rollups
from app.models.observability import RequestLog, SlowQuery
from app.models.order import Order, OrderStatus
from app.models.order_address import OrderAddress, OrderAddressType
from app.models.payment_event import PaymentEvent, PaymentEventType
from app.models.user import User
from app.schemas.analytics_view import AnalyticsViewEnvelope
from app.services.analytics import registry
from app.services.analytics.filters import AnalyticsFilters, Comparison, Granularity, Period
from app.services.analytics.resolvers.base import NOT_CONFIGURED
from app.services.analytics.resolvers.risk import (
    LATENCY_SAMPLE_EXCEEDED,
    MAX_LATENCY_SAMPLE,
    REPEATED_FAILURE_MIN,
    RISK_SIGNALS,
    ROUTE_CLASS_ADMIN,
    ROUTE_CLASS_INTERNAL,
    ROUTE_CLASS_STOREFRONT,
    VELOCITY_ORDERS,
    classify_route,
    percentile,
)
from app.services.analytics.resolvers.special import CUSTOM_FUNCTIONS
from app.services.analytics.timebox import active_generation, store_timezone
from app.services.analytics.types import GATED_STATES, ResolverId, ViewState
from app.services.analytics.view_service import AnalyticsViewService

# ---------------------------------------------------------------------------
# The two views under test
# ---------------------------------------------------------------------------

RISK_VIEW = (42, "payments", "fraud-and-risk-analytics")
SPEED_VIEW = (71, "customer-experience", "website-speed-and-technical-performance")

# June 1974 — see the module docstring. Nothing else in this suite is anywhere
# near it.
SANDBOX_START = date(1974, 6, 3)
SANDBOX_DAYS = 3
SANDBOX_END = date(1974, 6, 30)


def _day(offset: int) -> date:
    return date.fromordinal(SANDBOX_START.toordinal() + offset)


def _filters(
    *, granularity: Granularity = Granularity.DAY, limit: int = 20
) -> AnalyticsFilters:
    return AnalyticsFilters(
        period=Period.CUSTOM,
        date_from=SANDBOX_START,
        date_to=_day(SANDBOX_DAYS),
        comparison=Comparison.NONE,
        granularity=granularity,
        limit=limit,
    )


class _AnalyticsReader:
    """Exactly the permissions these two views need, and nothing else.

    ``AnalyticsViewService`` only ever calls ``has_permission``, so this
    exercises the real authorisation path without creating a user row — and
    without reaching for a real admin account, whose ``is_admin`` short circuit
    would make every check pass for the wrong reason.
    """

    is_admin = False

    def __init__(self, permissions: set[str]) -> None:
        self._permissions = permissions

    def has_permission(self, permission: str) -> bool:
        return permission in self._permissions


def _view(module_slug: str, view_slug: str):
    view = registry.get_view(module_slug, view_slug)
    assert view is not None, f"{module_slug}/{view_slug} is missing from the registry"
    return view


def _resolve(db: Session, spec: tuple[int, str, str], filters: AnalyticsFilters):
    _number, module_slug, view_slug = spec
    view = _view(module_slug, view_slug)
    service = AnalyticsViewService(db, _AnalyticsReader({view.permission}))
    return service.resolve_view(module_slug, view_slug, filters, use_cache=False)


def _utc(day: date, hour: int = 12, minute: int = 0) -> datetime:
    """A UTC instant inside the store-local reporting day ``day``.

    The store reports in Asia/Kolkata (UTC+5:30), so store-local day ``d`` is
    the UTC range ``[d-1 18:30, d 18:30)``. Every caller here keeps ``hour``
    below 18 so the row lands in the reporting day the window asked for; a
    20:00 UTC row would belong to the NEXT store-local day and silently fall
    outside a window that names this one.
    """
    assert 0 <= hour < 18, (
        f"hour={hour} UTC is store-local tomorrow; keep fixtures below 18:00 UTC"
    )
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)


# ===========================================================================
# 1. The guard. The defect that produced this whole task.
# ===========================================================================


def test_no_live_view_in_the_registry_is_unbound():
    """A LIVE view with empty ``params`` promises numbers it cannot produce.

    ``LIVE`` is not documentation. ``GATED_STATES`` deliberately excludes it, so
    the frontend issues a real request for every LIVE view and renders a data
    page with the answer. A LIVE view with no binding therefore ships a screen
    that fetches, gets nothing, and renders as a quiet period rather than as a
    missing wire — which is exactly how view 42 sat in the registry until this
    change.

    ``bespoke`` is NOT accepted as a substitute for ``params`` here. It is a
    real dispatch fallback in ``special.CustomResolver`` (``params['fn'] or
    ctx.view.bespoke``), so a view using it does reach a function — but a view
    that reaches a function which unconditionally returns ``not_configured`` is
    still a LIVE view that can never produce a number, which is the same defect
    wearing a different hat. If such a view is genuinely unwirable its state is
    PARTIAL or FEATURE_REQUIRED, and saying so costs one line.

    This test sweeps the WHOLE registry on purpose. It is expected to fail on
    views other people own; the fix belongs to whoever owns them.
    """
    offenders = sorted(
        (v.number, v.slug, v.resolver.value, v.bespoke)
        for v in registry.all_views()
        if v.state is ViewState.LIVE and not v.params
    )
    assert not offenders, (
        "These views are declared LIVE but carry no `params` binding, so each one "
        "issues a fetch and renders a data page it has no wire to fill:\n"
        + "\n".join(
            f"  - view {number} ({slug}): resolver={resolver!r}, bespoke={bespoke!r}"
            for number, slug, resolver, bespoke in offenders
        )
        + "\nEither bind it, or downgrade the state and populate `requires`. "
        "Downgrading is a correct outcome; leaving it LIVE and unbound is not."
    )


def test_the_guard_above_is_actually_capable_of_failing():
    """A guard on the guard.

    The assertion above passes trivially if ``all_views()`` ever returns nothing
    or if no view is LIVE at all — a registry refactor could do either without
    anybody noticing the guard had stopped guarding.
    """
    views = registry.all_views()
    assert len(views) == 73
    live = [v for v in views if v.state is ViewState.LIVE]
    assert len(live) >= 20, "the LIVE population collapsed; the guard is now vacuous"
    assert ViewState.LIVE not in GATED_STATES, (
        "LIVE became a gated state, which would mean the frontend no longer "
        "fetches it — the premise of the guard above."
    )


# ===========================================================================
# 2. View 42 — the declared state must match what it can produce
# ===========================================================================


def test_view_42_state_matches_what_it_can_actually_produce():
    """PARTIAL, bound, with the missing capability named.

    The three conditions are asserted together because any one of them alone is
    satisfiable dishonestly: bound-but-LIVE hides the dispute gap, PARTIAL-but-
    unbound is the original defect with a smaller label, and a limitation with
    no ``requires`` tells an admin something is wrong but not what to do.
    """
    view = _view(*RISK_VIEW[1:])
    assert view.number == 42

    assert view.params, (
        "View 42 has no params. It is not gated, so it fetches and renders — "
        "this is the defect this module exists to prevent."
    )
    assert view.params.get("fn") in CUSTOM_FUNCTIONS, (
        f"View 42 dispatches to {view.params.get('fn')!r}, which is not a "
        f"registered custom function. Known: {sorted(CUSTOM_FUNCTIONS)}."
    )
    assert view.resolver is ResolverId.CUSTOM

    assert view.state is ViewState.PARTIAL, (
        f"View 42 is {view.state.value}. It cannot observe chargebacks or "
        "disputes — payment_settlements is not in this deployment's schema and "
        "is loaded from the same gateway settlement report view 64 is gated on. "
        "If it is promoted back to LIVE, the dispute gap has to be closed first, "
        "not relabelled."
    )
    assert view.state not in GATED_STATES, (
        "View 42 must still resolve. PARTIAL is honest about the gap AND renders "
        "the signals it can measure; a gated state would show nothing at all."
    )
    assert view.requires, "a non-LIVE view must name what it is waiting on"
    assert view.limitation.strip()
    lowered = view.limitation.lower()
    assert "chargeback" in lowered or "dispute" in lowered, (
        "View 42's limitation must name the gap that produced the downgrade in "
        f"the words an admin reads. Got: {view.limitation!r}"
    )


def test_view_42_declares_no_composite_risk_score():
    """No weighted score, no coefficients, no ranking model.

    This deployment has no labelled fraud outcomes, so any weighting would be
    invented here and never validated against a single confirmed case. A score
    would look authoritative, would order the table by nothing, and would be
    impossible to argue with. The view emits counts of things that happened.
    """
    view = _view(*RISK_VIEW[1:])
    column_keys = {c.key for spec in view.tables for c in spec.columns}
    series_keys = {s for chart in view.charts for s in chart.series}
    banned = {"score", "risk_score", "fraud_score", "risk_level", "weighted_score"}
    assert not (column_keys | series_keys) & banned, (
        "View 42 has grown a score column. See the module docstring in "
        "resolvers/risk.py: there is nothing here to fit coefficients against."
    )
    assert "observed" in column_keys, (
        "The flagged table must show the count behind each signal — three failed "
        "attempts and thirty are not the same finding, and a bare label hides it."
    )
    # The signal vocabulary is closed and every member is a countable event.
    assert len(RISK_SIGNALS) == len(set(RISK_SIGNALS))
    assert all(not s.endswith("_score") for s in RISK_SIGNALS)


# ===========================================================================
# 3. Percentiles — the arithmetic, before any database is involved
# ===========================================================================


class TestPercentileArithmetic:
    """Pure arithmetic. No fixtures, so a failure here is unambiguous."""

    def test_nearest_rank_matches_a_hand_computed_p95(self):
        """1..100 in order: the 95th nearest-rank value is 95, exactly.

        Nearest rank is ``ceil(q/100 * n)`` over the sorted values, 1-based. For
        n=100 and q=95 that is rank 95, i.e. the value 95. No interpolation, so
        the answer is always a latency that was genuinely observed.
        """
        values = list(range(1, 101))
        assert percentile(values, 95) == 95
        assert percentile(values, 50) == 50
        assert percentile(values, 99) == 99
        assert percentile(values, 100) == 100

    def test_it_does_not_care_about_input_order(self):
        assert percentile([9, 1, 5, 3, 7], 95) == 9
        assert percentile([7, 3, 5, 1, 9], 95) == 9

    def test_a_single_observation_is_its_own_every_percentile(self):
        assert percentile([42], 50) == 42
        assert percentile([42], 99) == 42

    def test_no_observations_is_none_and_never_zero(self):
        """A bucket with no requests has no p95. Zero would read as instant."""
        assert percentile([], 95) is None
        assert percentile((), 50) is None

    def test_a_percentile_of_percentiles_is_not_the_percentile(self):
        """The arithmetic core of the limitation, with no database in the way.

        Three days whose individual p95s are all small, but whose combined
        population has a fat tail on one day only. Averaging the daily figures
        loses the tail entirely.
        """
        day_a = [10] * 100
        day_b = [10] * 100
        # 6 % of this day is slow, so the day's OWN p95 sits in the tail:
        # nearest rank 95 of 100 is index 94, which is already a 5 000.
        day_c = [10] * 94 + [5_000] * 6

        window_p95 = percentile(day_a + day_b + day_c, 95)
        daily = [percentile(day_a, 95), percentile(day_b, 95), percentile(day_c, 95)]
        averaged = statistics.mean(daily)

        # Over 300 rows only 6 are slow — 2 %, well inside the 95th — so the
        # window p95 is 10 ms even though one day's p95 is 5 000 ms.
        assert window_p95 == 10
        assert daily == [10, 10, 5_000]
        # The window figure and the average of the dailies disagree, and the
        # direction is not fixed — which is why no correction factor exists.
        assert averaged != window_p95
        assert averaged > window_p95 * 100
        # The window p99 DOES see the tail, and reports the value it took.
        assert percentile(day_a + day_b + day_c, 99) == 5_000


class TestRouteClassification:
    """Storefront / admin / internal, from the FastAPI route template."""

    @pytest.mark.parametrize(
        "route, expected",
        [
            ("/api/v1/products/{product_id}", ROUTE_CLASS_STOREFRONT),
            ("/api/v1/cart", ROUTE_CLASS_STOREFRONT),
            ("/api/v1/checkout/initiate", ROUTE_CLASS_STOREFRONT),
            ("/api/v1/orders/{order_id}", ROUTE_CLASS_STOREFRONT),
            ("/api/v1/admin/database/tables", ROUTE_CLASS_ADMIN),
            ("/api/v1/admin/payment-methods", ROUTE_CLASS_ADMIN),
            ("/api/v1/analytics/views/{module}/{view}", ROUTE_CLASS_ADMIN),
            ("/api/v1/audit-events", ROUTE_CLASS_ADMIN),
            ("/api/v1/observability/requests", ROUTE_CLASS_ADMIN),
            ("/api/v1/dashboard/summary", ROUTE_CLASS_ADMIN),
            ("/api/v1/users/{user_id}", ROUTE_CLASS_ADMIN),
            ("/api/v1/returns/admin/{return_id}", ROUTE_CLASS_ADMIN),
            ("/health", ROUTE_CLASS_INTERNAL),
            ("/metrics", ROUTE_CLASS_INTERNAL),
            ("/openapi.json", ROUTE_CLASS_INTERNAL),
            ("/", ROUTE_CLASS_INTERNAL),
            ("/static/logo.png", ROUTE_CLASS_INTERNAL),
            (None, ROUTE_CLASS_INTERNAL),
            ("", ROUTE_CLASS_INTERNAL),
        ],
    )
    def test_it_sorts_the_real_route_templates(self, route, expected):
        assert classify_route(route) == expected

    def test_an_admin_prefix_does_not_swallow_a_storefront_sibling(self):
        """``/api/v1/user-facing`` must not be caught by the ``/api/v1/users``
        prefix. Prefix matching is on segment boundaries, not on characters."""
        assert classify_route("/api/v1/users") == ROUTE_CLASS_ADMIN
        assert classify_route("/api/v1/users/{id}") == ROUTE_CLASS_ADMIN
        assert classify_route("/api/v1/usersomething") == ROUTE_CLASS_STOREFRONT

    def test_classification_is_conservative_by_default(self):
        """An unrecognised API route stays storefront.

        Over-claiming which traffic is internal is the worse error: it filters
        real customer-facing requests out of the graph, which hides a
        regression. Leaving a little admin traffic in only dilutes one.
        """
        assert classify_route("/api/v1/something-nobody-classified") == (
            ROUTE_CLASS_STOREFRONT
        )


# ===========================================================================
# 4. Fixtures against the real database
# ===========================================================================


@contextmanager
def _speed_sandbox() -> Iterator[tuple[Session, dict[str, list[int]]]]:
    """Three June-1974 days of request telemetry, deleted unconditionally.

    The shape is chosen to make two separate points provable at once:

    * **storefront vs admin.** Day 3 carries one 30-second admin export. If the
      charts included admin traffic, the storefront p99 would jump to 30 000 ms.
    * **window p95 != average of daily p95s.** Days 1 and 2 are flat at 10 ms;
      day 3 carries a 6 % tail at 5 000 ms, which is enough to move that day's
      own p95 into the tail but not enough to move the window's. The daily p95s
      are 10, 10, 5 000; the window p95 over all 300 raw rows is 10.
    """
    db = SessionLocal()
    written: list[int] = []
    slow_written: list[int] = []
    try:
        storefront: dict[str, list[int]] = {}
        for offset in range(SANDBOX_DAYS):
            day = _day(offset)
            if offset < 2:
                latencies = [10] * 100
            else:
                latencies = [10] * 94 + [5_000] * 6
            storefront[day.isoformat()] = latencies

            for i, ms in enumerate(latencies):
                # One request in twenty is a 500, so the error rate is a clean
                # 5.00% and an off-by-one in the status floor is visible.
                status = 500 if i % 20 == 0 else 200
                db.add(
                    RequestLog(
                        ts=_utc(day, 12, i % 60),
                        method="GET",
                        route="/api/v1/products/{product_id}",
                        status=status,
                        total_ms=ms,
                        db_ms=1,
                        query_count=1,
                    )
                )
            if offset == 2:
                # The admin outlier. Thirty seconds, and it must not reach the
                # storefront charts.
                db.add(
                    RequestLog(
                        ts=_utc(day, 13, 0),
                        method="GET",
                        route="/api/v1/admin/database/tables",
                        status=200,
                        total_ms=30_000,
                        db_ms=29_000,
                        query_count=400,
                    )
                )
                db.add(
                    RequestLog(
                        ts=_utc(day, 13, 5),
                        method="GET",
                        route="/health",
                        status=200,
                        total_ms=1,
                        db_ms=0,
                        query_count=0,
                    )
                )
                db.add(
                    SlowQuery(
                        ts=_utc(day, 13, 0),
                        request_id="rq-1974",
                        route="/api/v1/admin/database/tables",
                        fingerprint_hash="a" * 32,
                        sql_normalized="SELECT * FROM orders WHERE id = ?",
                        table_name="orders",
                        operation="SELECT",
                        duration_ms=28_000,
                    )
                )
        db.commit()
        written = [
            int(r)
            for r in db.execute(
                select(RequestLog.id).where(
                    RequestLog.ts >= _utc(SANDBOX_START, 0),
                    RequestLog.ts < _utc(SANDBOX_END, 0),
                )
            )
            .scalars()
            .all()
        ]
        slow_written = [
            int(r)
            for r in db.execute(
                select(SlowQuery.id).where(
                    SlowQuery.ts >= _utc(SANDBOX_START, 0),
                    SlowQuery.ts < _utc(SANDBOX_END, 0),
                )
            )
            .scalars()
            .all()
        ]
        yield db, storefront
    finally:
        try:
            db.rollback()
            if written:
                db.execute(delete(RequestLog).where(RequestLog.id.in_(written)))
            if slow_written:
                db.execute(delete(SlowQuery).where(SlowQuery.id.in_(slow_written)))
            db.commit()
        finally:
            db.close()


@contextmanager
def _risk_sandbox() -> Iterator[tuple[Session, dict[str, int]]]:
    """One June-1974 window of orders, payment events and rollups.

    Every signal the view can raise is represented exactly once, so a resolver
    that silently stopped emitting one would drop a row rather than change a
    number — which a count assertion catches and a spot check would not.
    """
    db = SessionLocal()
    generation = int(active_generation(db).generation)
    order_ids: list[int] = []
    event_ids: list[int] = []
    address_ids: list[int] = []
    user_id: int | None = None
    try:
        # Any existing user satisfies the NOT NULL FK; the risk resolver only
        # ever groups by user_id and never reads the user row, so borrowing one
        # is safe and avoids authoring an account this test would have to own.
        user_id = db.execute(select(User.id).order_by(User.id).limit(1)).scalar()
        assert user_id is not None, "the database has no users to attach orders to"

        day = _day(0)

        def _order(
            suffix: str,
            *,
            on: date,
            hour: int,
            minute: int = 0,
            status=OrderStatus.PAID,
            paid: bool = True,
        ) -> Order:
            placed = _utc(on, hour, minute)
            order = Order(
                order_number=f"WV-1974-{suffix}",
                user_id=user_id,
                status=status,
                subtotal=Decimal("1000.00"),
                total_amount=Decimal("1000.00"),
                currency="INR",
                payment_method="prepaid",
                created_at=placed,
                paid_at=placed if paid else None,
            )
            db.add(order)
            return order

        # All six orders belong to ONE borrowed customer, so the velocity signal
        # is decided purely by the timing below. The three clusters are >24h
        # apart on purpose: only the middle one may qualify, which is what makes
        # `observed == VELOCITY_ORDERS` a real assertion rather than a count of
        # everything the fixture wrote.
        #
        #   day 0, 01:00        one order          -> no burst
        #   day 1, 08:00-08:02  three orders       -> BURST of exactly 3
        #   day 2, 16:00-16:05  two orders         -> below the threshold
        #
        # Signal 1 + 2 + 3 + 4: all four payment-event signals on one order, so
        # the (order, signal) fan-out is exercised rather than assumed.
        events_order = _order("EVENTS", on=day, hour=1)
        # Signal 5: three orders inside 24h by one customer -> velocity.
        velocity = [
            _order(f"VEL{i}", on=_day(1), hour=8, minute=i)
            for i in range(VELOCITY_ORDERS)
        ]
        # Signal 6: billing and shipping pincodes disagree.
        mismatch_order = _order("ADDR", on=_day(2), hour=16)
        # Signal 7: cancelled with paid_at already set.
        undone_order = _order(
            "UNDONE", on=_day(2), hour=16, minute=5, status=OrderStatus.CANCELLED
        )
        db.flush()

        order_ids = [
            int(o.id)
            for o in [events_order, *velocity, mismatch_order, undone_order]
        ]

        for _ in range(REPEATED_FAILURE_MIN):
            db.add(
                PaymentEvent(
                    order_id=events_order.id,
                    event_type=PaymentEventType.STATUS_APPLIED,
                    payment_status="failed",
                    gateway_code="razorpay",
                    created_at=_utc(day, 7),
                )
            )
        db.add(
            PaymentEvent(
                order_id=events_order.id,
                event_type=PaymentEventType.AMOUNT_MISMATCH,
                payment_status="success",
                amount_reported_minor=1,
                amount_expected_minor=100_000,
                created_at=_utc(day, 7, 1),
            )
        )
        db.add(
            PaymentEvent(
                order_id=events_order.id,
                event_type=PaymentEventType.WEBHOOK_SIGNATURE_INVALID,
                signature_valid=False,
                created_at=_utc(day, 7, 2),
            )
        )
        db.add(
            PaymentEvent(
                order_id=events_order.id,
                event_type=PaymentEventType.SETTLED_AFTER_CANCEL,
                payment_status="success",
                created_at=_utc(day, 7, 3),
            )
        )
        # An unsigned callback that matched NO order. It cannot be flagged
        # against a row and must be reported as a warning, not dropped.
        db.add(
            PaymentEvent(
                order_id=None,
                merchant_transaction_id="mtid-1974-orphan",
                event_type=PaymentEventType.WEBHOOK_SIGNATURE_INVALID,
                signature_valid=False,
                created_at=_utc(day, 7, 4),
            )
        )

        for kind, pincode in (
            (OrderAddressType.SHIPPING, "560001"),
            (OrderAddressType.BILLING, "110001"),
        ):
            db.add(
                OrderAddress(
                    order_id=mismatch_order.id,
                    address_type=kind,
                    full_name="Sandbox",
                    city="Sandbox",
                    state="KA",
                    country="IN",
                    pincode=pincode,
                )
            )

        # Rollups behind the three catalogue KPI cards. Written under the ACTIVE
        # generation because the service reads that from the database.
        for offset in range(SANDBOX_DAYS):
            bucket = _day(offset)
            db.add(
                rollups.AggOrderDaily(
                    bucket_date=bucket,
                    tz_generation=generation,
                    orders_total=10,
                    orders_cancelled=2,
                    orders_paid=8,
                    order_value_created=Decimal("1000.00"),
                    paid_order_value=Decimal("800.00"),
                )
            )
            db.add(
                rollups.AggPaymentDaily(
                    bucket_date=bucket,
                    tz_generation=generation,
                    gateway="razorpay",
                    payment_method="prepaid",
                    payment_instrument="upi",
                    attempts=20,
                    paid=15,
                    failed=5,
                )
            )
            db.add(
                rollups.AggShipmentDaily(
                    bucket_date=bucket,
                    tz_generation=generation,
                    courier_partner="bluedart",
                    shipments=10,
                    delivered=8,
                    rto_initiated=2,
                )
            )
        db.commit()

        event_ids = [
            int(r)
            for r in db.execute(
                select(PaymentEvent.id).where(
                    PaymentEvent.created_at >= _utc(SANDBOX_START, 0),
                    PaymentEvent.created_at < _utc(SANDBOX_END, 0),
                )
            )
            .scalars()
            .all()
        ]
        address_ids = [
            int(r)
            for r in db.execute(
                select(OrderAddress.id).where(OrderAddress.order_id.in_(order_ids))
            )
            .scalars()
            .all()
        ]
        yield db, {"generation": generation, "orders": len(order_ids)}
    finally:
        try:
            db.rollback()
            if event_ids:
                db.execute(delete(PaymentEvent).where(PaymentEvent.id.in_(event_ids)))
            if address_ids:
                db.execute(delete(OrderAddress).where(OrderAddress.id.in_(address_ids)))
            if order_ids:
                db.execute(delete(Order).where(Order.id.in_(order_ids)))
            for model in (
                rollups.AggOrderDaily,
                rollups.AggPaymentDaily,
                rollups.AggShipmentDaily,
            ):
                db.execute(
                    delete(model).where(
                        model.tz_generation == generation,
                        model.bucket_date >= SANDBOX_START,
                        model.bucket_date <= SANDBOX_END,
                    )
                )
            db.commit()
        finally:
            db.close()


# ===========================================================================
# 5. End to end through the real view service
# ===========================================================================


def test_view_42_resolves_end_to_end_with_real_values():
    """A data envelope with provenance, real rows, and every signal represented.

    Asserted through ``AnalyticsViewService`` rather than the resolver directly,
    so registry lookup, permission check, gating and envelope construction are
    all in the path — the same path a browser takes.
    """
    with _risk_sandbox() as (db, _meta):
        envelope = _resolve(db, RISK_VIEW, _filters())

        assert isinstance(envelope, AnalyticsViewEnvelope), (
            "View 42 must resolve. PARTIAL is not gated: it renders the signals "
            "it can measure and states the gap it cannot."
        )
        codes = {w.code for w in envelope.warnings}
        assert NOT_CONFIGURED not in codes, (
            "View 42 still reports NOT_CONFIGURED: "
            + "; ".join(w.message for w in envelope.warnings if w.code == NOT_CONFIGURED)
        )
        assert envelope.sources, (
            "an empty `sources` list is how this subsystem says 'nothing is "
            "wired up' — view 42 is wired now and must report provenance"
        )
        assert {s.id for s in envelope.sources} >= {"payment_events", "orders"}

        rows = envelope.tables["flagged_orders"].rows
        assert rows, "the fixture raises every signal; the table must not be empty"

        raised = {row["signal"] for row in rows}
        assert raised == set(RISK_SIGNALS), (
            "Every signal the resolver can raise is represented in the fixture, "
            f"so all of them must appear. Missing: {sorted(set(RISK_SIGNALS) - raised)}"
        )
        # The count behind each signal is reported, not just its name.
        by_signal = {row["signal"]: row for row in rows}
        assert by_signal["repeated_payment_failure"]["observed"] == REPEATED_FAILURE_MIN
        assert by_signal["customer_order_velocity"]["observed"] == VELOCITY_ORDERS
        assert all(row["value"] == Decimal("1000.00") for row in rows)

        chart = envelope.series["risk_signals"]
        assert chart, "the signal chart must carry the distinct-order counts"
        assert {p["signal"] for p in chart} == set(RISK_SIGNALS)
        velocity_point = next(
            p for p in chart if p["signal"] == "customer_order_velocity"
        )
        assert velocity_point["orders"] == VELOCITY_ORDERS, (
            "every order in a qualifying burst is flagged, not only the last one"
        )


def test_view_42_reports_the_orphan_events_it_cannot_flag():
    """An unsigned callback that matched no order is the interesting one.

    It has no row to attach to, so it would vanish from a naive implementation.
    Counted in a warning instead — the number of things this screen cannot show
    is part of what the screen has to say.
    """
    with _risk_sandbox() as (db, _meta):
        envelope = _resolve(db, RISK_VIEW, _filters())
        orphan = [w for w in envelope.warnings if w.code == "UNATTRIBUTED_PAYMENT_EVENTS"]
        assert orphan, (
            "the fixture writes one signature-invalid webhook with no order_id; "
            "it must be reported rather than silently dropped"
        )
        assert orphan[0].detail.get("webhook_signature_invalid") == 1


def test_view_42_kpi_cards_come_from_the_catalogue_bindings():
    """The three cards keep ``core.METRIC_BINDINGS``, not a second definition.

    ``rto_rate`` is already bound to agg_shipment_daily, ``payment_success_rate``
    to agg_payment_daily and ``cancellation_rate`` to agg_order_daily. Restating
    any of them inside this view would be a second definition of a published
    metric, which is how two screens end up disagreeing under one label.
    """
    with _risk_sandbox() as (db, _meta):
        envelope = _resolve(db, RISK_VIEW, _filters())
        # 3 days x (paid 15 / attempts 20) = 45/60 = 75%
        assert envelope.kpis["payment_success_rate"].value == Decimal("75.0000")
        # 3 days x (cancelled 2 / total 10) = 6/30 = 20%
        assert envelope.kpis["cancellation_rate"].value == Decimal("20.0000")
        # 3 days x rto 2 / (delivered 8 + rto 2) = 6/30 = 20%
        assert envelope.kpis["rto_rate"].value == Decimal("20.0000")


def test_view_71_resolves_end_to_end_with_real_values():
    """Series, tables and provenance from ``obs_request_logs``/``obs_slow_queries``."""
    with _speed_sandbox() as (db, storefront):
        envelope = _resolve(db, SPEED_VIEW, _filters())

        assert isinstance(envelope, AnalyticsViewEnvelope)
        codes = {w.code for w in envelope.warnings}
        assert NOT_CONFIGURED not in codes
        assert {s.id for s in envelope.sources} >= {
            "obs_request_logs",
            "obs_slow_queries",
        }

        latency = envelope.series["latency_trend"]
        assert len(latency) == SANDBOX_DAYS, "one point per seeded reporting day"
        # Days 1 and 2 are flat at 10 ms; day 3 carries a 5% tail at 5000 ms.
        assert [p["p95_ms"] for p in latency] == [10, 10, 5_000]
        assert [p["p50_ms"] for p in latency] == [10, 10, 10]

        errors = envelope.series["error_rate"]
        assert all(p["error_rate"] == Decimal("5.0000") for p in errors), (
            "one request in twenty is a 500 on every seeded day"
        )
        throughput = envelope.series["throughput"]
        assert [p["requests"] for p in throughput] == [
            len(storefront[_day(i).isoformat()]) for i in range(SANDBOX_DAYS)
        ]

        endpoints = envelope.tables["slow_endpoints"].rows
        assert endpoints, "the slowest-endpoints table must fill"
        assert {r["endpoint"] for r in endpoints} >= {
            "/api/v1/products/{product_id}",
            "/api/v1/admin/database/tables",
        }
        queries = envelope.tables["slow_queries"].rows
        assert queries and queries[0]["table_name"] == "orders"
        assert queries[0]["max_ms"] == 28_000


def test_view_71_excludes_admin_traffic_from_the_storefront_charts():
    """The 30-second admin export must not touch the storefront lines.

    It stays visible in ``latency_summary`` under its own route class, so the
    figure is separated rather than deleted — an admin endpoint taking thirty
    seconds is a real problem, just not a storefront one.
    """
    with _speed_sandbox() as (db, _storefront):
        envelope = _resolve(db, SPEED_VIEW, _filters())

        latency = envelope.series["latency_trend"]
        worst = max(p["p99_ms"] for p in latency)
        assert worst == 5_000, (
            "the storefront p99 picked up the 30 000 ms admin request; route "
            "classification is not being applied to the charts"
        )
        throughput = envelope.series["throughput"]
        assert all(p["requests"] in (100,) for p in throughput), (
            "the admin and health requests leaked into storefront throughput"
        )

        summary = {r["route_class"]: r for r in envelope.tables["latency_summary"].rows}
        assert set(summary) == {
            ROUTE_CLASS_STOREFRONT,
            ROUTE_CLASS_ADMIN,
            ROUTE_CLASS_INTERNAL,
        }
        assert summary[ROUTE_CLASS_ADMIN]["p95_ms"] == 30_000, (
            "the admin outlier must stay visible in its own class, not vanish"
        )
        assert summary[ROUTE_CLASS_ADMIN]["requests"] == 1
        assert summary[ROUTE_CLASS_INTERNAL]["requests"] == 1
        assert summary[ROUTE_CLASS_STOREFRONT]["requests"] == 100 * SANDBOX_DAYS

        endpoints = {r["endpoint"]: r for r in envelope.tables["slow_endpoints"].rows}
        assert endpoints["/api/v1/admin/database/tables"]["route_class"] == (
            ROUTE_CLASS_ADMIN
        ), "the endpoint table must dimension by class so admin is separable"


def test_window_p95_is_not_the_average_of_daily_p95s():
    """The limitation, pinned against the real resolver output.

    ``latency_summary`` is computed over the window's RAW rows. The chart points
    are per-bucket percentiles. Averaging the latter is the tempting refactor
    and it produces a different number — this test computes both from the same
    envelope and asserts they disagree, so the wrong implementation cannot pass.

    Fixture: 300 storefront rows, 294 at 10 ms and 6 at 5 000 ms, with the tail
    entirely on the third day. Nearest rank at 95% over 300 sorted values is
    rank 285, which is still 10 ms. The daily p95s are 10, 10 and 5 000, whose
    mean is 1 673.33 — 167x the true figure, and precise-looking.
    """
    with _speed_sandbox() as (db, storefront):
        envelope = _resolve(db, SPEED_VIEW, _filters())

        summary = {r["route_class"]: r for r in envelope.tables["latency_summary"].rows}
        window_p95 = summary[ROUTE_CLASS_STOREFRONT]["p95_ms"]

        # The truth, computed here from the fixture rather than read back.
        every_latency = [ms for day in storefront.values() for ms in day]
        assert len(every_latency) == 300
        assert window_p95 == percentile(every_latency, 95) == 10

        daily_p95s = [p["p95_ms"] for p in envelope.series["latency_trend"]]
        assert daily_p95s == [10, 10, 5_000]
        averaged = statistics.mean(daily_p95s)

        assert averaged != window_p95, (
            "Averaging the daily p95s happened to match the window p95 for this "
            "fixture, which means the fixture no longer demonstrates the "
            "limitation. Restore a distribution with a one-day tail."
        )
        assert averaged > window_p95 * 100, (
            f"the wrong method gives {averaged}, the right one {window_p95}"
        )
        # And the p99, which DOES see the tail over the window, is the value the
        # tail actually took — not something interpolated between the two.
        assert summary[ROUTE_CLASS_STOREFRONT]["p99_ms"] == 5_000


def test_a_weekly_bucket_recomputes_from_raw_rather_than_folding_daily_points():
    """Re-bucketing to a week must re-read the raw rows, not combine the days.

    The three seeded days fall in one ISO week. The weekly p95 is therefore the
    window p95 (10 ms), NOT the average or the max of the three daily figures —
    both of which a re-aggregating implementation would produce.
    """
    with _speed_sandbox() as (db, storefront):
        weekly = _resolve(db, SPEED_VIEW, _filters(granularity=Granularity.WEEK))
        points = weekly.series["latency_trend"]
        assert len(points) == 1, "1974-06-03 is a Monday; three days, one week"

        every_latency = [ms for day in storefront.values() for ms in day]
        assert points[0]["p95_ms"] == percentile(every_latency, 95) == 10
        assert points[0]["p99_ms"] == percentile(every_latency, 99) == 5_000
        assert points[0]["p95_ms"] != max(10, 10, 5_000)
        assert points[0]["p95_ms"] != statistics.mean([10, 10, 5_000])


def test_view_71_states_the_scan_bound_it_operates_under():
    """The bound is a real number the code enforces, not prose in a docstring."""
    assert MAX_LATENCY_SAMPLE > 0
    view = _view(*SPEED_VIEW[1:])
    lowered = view.limitation.lower()
    for phrase in ("percentile", "core web vitals", "storefront"):
        assert phrase in lowered, (
            f"View 71's limitation must mention {phrase!r} — it is what stops a "
            f"reader taking these for browser metrics. Got: {view.limitation!r}"
        )
    assert LATENCY_SAMPLE_EXCEEDED, "the over-bound warning code must exist"


def test_an_empty_window_reports_nothing_measured_rather_than_zero():
    """No requests is 'not recorded', never 'nothing was slow'.

    A percentile of an empty bucket is None and an error rate over no requests
    is None. A zero would render as an instant, flawless service — the one
    direction of error nobody investigates.
    """
    db = SessionLocal()
    try:
        empty = AnalyticsFilters(
            period=Period.CUSTOM,
            # A week of 1974 with nothing seeded in it.
            date_from=date(1974, 6, 17),
            date_to=date(1974, 6, 24),
            comparison=Comparison.NONE,
        )
        envelope = _resolve(db, SPEED_VIEW, empty)
        assert isinstance(envelope, AnalyticsViewEnvelope)
        assert envelope.series["latency_trend"] == []
        assert envelope.tables["latency_summary"].rows == []
        assert "NO_ROLLUP_YET" in {w.code for w in envelope.warnings}
    finally:
        db.close()


# ===========================================================================
# 6. The existing gates, restated for these two views only
# ===========================================================================


@pytest.mark.parametrize("spec", [RISK_VIEW, SPEED_VIEW], ids=["view42", "view71"])
def test_bindings_name_only_params_keys_a_resolver_reads(spec):
    """``params`` is configuration an operator reads. A key nothing consults
    still looks like configuration to the next person."""
    from tests.test_analytics_view_bindings import READABLE_PARAM_KEYS

    view = _view(*spec[1:])
    unknown = set(view.params) - READABLE_PARAM_KEYS
    assert not unknown, f"view {spec[0]} declares unread params {sorted(unknown)}"


@pytest.mark.parametrize("spec", [RISK_VIEW, SPEED_VIEW], ids=["view42", "view71"])
def test_neither_view_claims_a_rollup_it_cannot_read(spec):
    """Neither is bound to a rollup, and neither may pretend to be.

    Both read tables outside ``AnalyticsRepository``'s reflected allowlist, so a
    ``source`` here would be validated against the fifteen ``agg_*`` models and
    raise at query time. The custom function is the whole binding.
    """
    view = _view(*spec[1:])
    assert "source" not in view.params
    assert not (view.params.keys() & {"metrics", "columns", "group_by", "row_filters"})
    assert view.params.get("fn") in CUSTOM_FUNCTIONS


def test_view_71_still_declines_to_export():
    """``tests/test_analytics_export.py`` names this view. Keep it in step.

    The tables fill now, so the old reason ("no rollup can fill them") no longer
    holds — but a downloaded CSV of raw latency, read away from the limitation
    line, is exactly the thing that gets pasted into a deck as Core Web Vitals.
    """
    assert _view(*SPEED_VIEW[1:]).export is False


def test_the_frontend_contract_carries_both_new_bindings():
    """The committed JSON must match a fresh dump, and carry these two views.

    Regenerating is the fix, never editing the artifact:
    ``python backend/scripts/dump_analytics_registry.py``.
    """
    import json

    import scripts.dump_analytics_registry as dump

    payload = json.loads(dump.render_contract())
    by_number = {v["number"]: v for m in payload["modules"] for v in m["views"]}
    assert by_number[42]["params"] == {"fn": "risk_signals"}
    assert by_number[42]["state"] == ViewState.PARTIAL.value
    assert by_number[42]["requires"] == ["gateway_settlement_api"]
    assert by_number[71]["params"] == {"fn": "request_performance"}
    assert dump.CONTRACT_PATH.read_text(encoding="utf-8") == dump.render_contract(), (
        "registry.contract.json is stale — regenerate it, do not edit it."
    )


def test_the_sandbox_leaves_nothing_behind():
    """Teardown is asserted, not assumed.

    Peers run against this same database concurrently. A fixture that leaks
    request rows or orders into June 1974 would make the next run of this module
    fail for a reason that has nothing to do with the code under test.
    """
    with _speed_sandbox() as (db, _s):
        pass
    with _risk_sandbox() as (db2, _m):
        pass

    db = SessionLocal()
    try:
        tz = store_timezone(db)
        assert tz is not None
        leftover_requests = db.execute(
            select(RequestLog.id).where(
                RequestLog.ts >= _utc(SANDBOX_START, 0),
                RequestLog.ts < _utc(SANDBOX_END + timedelta(days=1), 0),
            )
        ).all()
        assert leftover_requests == []
        leftover_orders = db.execute(
            select(Order.id).where(Order.order_number.like("WV-1974-%"))
        ).all()
        assert leftover_orders == []
    finally:
        db.close()
