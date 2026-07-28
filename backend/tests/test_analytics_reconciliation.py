"""Tests for the reconciliation service.

The arithmetic here is the easy half. What these tests actually pin down is the
one behaviour a reconciliation screen exists to have and almost never does:
**an absence must not render as agreement.**

The tests that carry the weight
-------------------------------
* ``test_ga4_check_is_not_configured_and_never_match`` — GA4 is not connected,
  so the purchase-parity check must say so with NULL values. It asserts
  ``status != "match"`` explicitly and separately, because the whole failure mode
  is a check that quietly grades itself a pass on a comparison it never made.
* ``test_a_day_with_no_data_produces_no_row`` — the sparse-day rule. A ``0``
  variance means "checked and balanced"; a day nobody checked has no row at all.
  Densifying it would put a confident zero exactly where a reader is looking for
  a gap.
* ``test_empty_window_reports_every_check_as_not_configured`` — the composite of
  the two: a window with nothing in it produces six ``not_configured`` rows and
  zero ``match`` rows.
* ``test_legacy_matches_new_exactly`` — the shadow-mode parity anchor. This is
  the check whose green light licenses retiring the legacy admin pages, so it is
  asserted on absolute figures and on an exact zero difference, never a tolerance.

Isolation strategy
------------------
The whole fixture lives in **2010**, a year no other suite in this repo touches
(2005, 2007, 2008, 2009, 2011, 2012 and 2013 are all taken) and years before any
row this store will ever hold. Every assertion is therefore on an absolute
figure rather than on a delta from a baseline.

Each test owns a different **month** of 2010 so that a leaked row from one can
never be read by another, and ``_sweep()`` runs both before and after every test
against the whole year. Sweeping by date range rather than only by id is
deliberate: a run that dies mid-test leaves rows behind, and an id list that died
with it cannot clean them up. The suite has to survive being run three times in a
row, which is the only way "consecutive runs are identical" gets proved.

House style, per this repo: no conftest DB fixture, each test owns its
``SessionLocal()``, teardown in ``finally`` through a *fresh* session so a
half-rolled-back transaction cannot skip it.
"""
from __future__ import annotations

import time
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.analytics_control import (
    AlertRuleKey,
    AlertSeverity,
    AlertStatus,
    AnalyticsAlert,
    AnalyticsEventOutbox,
    ConsentState,
    OutboxEventName,
    OutboxStatus,
)
from app.models.analytics_rollups import AggOrderDaily
from app.models.order import Order, OrderItem, OrderStatus
from app.models.order_payment import OrderPayment, PaymentTxnStatus
from app.models.product import Product
from app.models.shipment import Shipment, ShipmentStatus
from app.models.user import User
from app.services.analytics.reconciliation import (
    CHECK_ORDER,
    CheckKey,
    CheckStatus,
    run_reconciliation,
)
from app.services.analytics.timebox import active_generation, day_bounds_utc, store_timezone

# ---------------------------------------------------------------------------
# The 2010 sandbox
# ---------------------------------------------------------------------------

SANDBOX_FROM = date(2010, 1, 1)
SANDBOX_TO = date(2011, 1, 1)

USER_EMAIL_PATTERN = "recontest-%@example.com"
SKU_PATTERN = "SKU-RECON-%"


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _d(value: str) -> Decimal:
    return Decimal(value)


def _window(month: int, days: int = 5) -> tuple[date, date]:
    """A half-open window of `days` store-local days inside one 2010 month."""
    start = date(2010, month, 1)
    return start, start + timedelta(days=days)


def _midday(db: Session, day: date) -> datetime:
    """A UTC instant safely inside one store-local reporting day.

    Derived from ``timebox`` rather than hardcoded, so the fixture lands in the
    intended bucket whatever ``store.timezone`` is set to. The store runs on
    Asia/Kolkata, whose day boundary is 18:30 UTC the *previous* day — an order
    stamped "2010-03-02 00:00 UTC" belongs to March 1st, and a test that assumed
    otherwise would fail for a reason that has nothing to do with reconciliation.
    """
    start, end = day_bounds_utc(day, store_timezone(db))
    return start + (end - start) / 2


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


class _Owned:
    """Ids this test created. The sweep is the safety net; this is the scalpel."""

    def __init__(self) -> None:
        self.orders: list[int] = []
        self.products: list[int] = []
        self.users: list[int] = []


def _create_user(db: Session, owned: _Owned) -> User:
    user = User(
        email=f"recontest-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.flush()
    owned.users.append(user.id)
    return user


def _create_product(db: Session, owned: _Owned, *, price: str) -> Product:
    product = Product(
        sku=f"SKU-RECON-{_uid()}",
        name=f"ReconTestProduct {_uid()}",
        price=_d(price),
        cost=_d("100.00"),
        stock=1000,
    )
    db.add(product)
    db.flush()
    owned.products.append(product.id)
    return product


def _create_order(
    db: Session,
    owned: _Owned,
    user: User,
    product: Product,
    *,
    at: datetime,
    qty: int = 1,
    status: OrderStatus = OrderStatus.PAID,
    discount: str = "0",
    tax: str = "0",
    shipping: str = "0",
    cod_surcharge: str = "0",
    payment_method: str = "prepaid",
) -> Order:
    """An order whose ``total_amount`` satisfies the revenue-bridge identity.

    Built the same way ``test_analytics_margin`` builds them, so a fixture that
    balances here balances there: ``total = gross - discounts + tax + shipping +
    cod_surcharge``.
    """
    gross = product.price * qty
    total = gross - _d(discount) + _d(tax) + _d(shipping) + _d(cod_surcharge)
    is_cod = payment_method != "prepaid"
    order = Order(
        order_number=f"WV-RECON-{_uid()}",
        user_id=user.id,
        status=status,
        subtotal=gross,
        tax_amount=_d(tax),
        discount_amount=_d(discount),
        payment_discount_amount=_d("0"),
        shipping_amount=_d(shipping),
        cod_surcharge_amount=_d(cod_surcharge),
        cod_balance=total if is_cod else _d("0.00"),
        total_amount=total,
        currency="INR",
        payment_method=payment_method,
        created_at=at,
        paid_at=at if status != OrderStatus.PENDING else None,
    )
    order.items = [
        OrderItem(
            product_id=product.id,
            quantity=qty,
            unit_price=product.price,
            unit_cost=product.cost,
        )
    ]
    db.add(order)
    db.flush()
    owned.orders.append(order.id)
    return order


def _create_rollup(
    db: Session,
    day: date,
    *,
    generation: int,
    orders_total: int = 0,
    paid_order_value: str = "0.00",
    gms: str = "0.00",
    discount: str = "0.00",
    tax: str = "0.00",
    shipping: str = "0.00",
    cod_surcharge: str = "0.00",
    refunds: str = "0.00",
    net_revenue: str | None = None,
) -> AggOrderDaily:
    """One ``agg_order_daily`` bucket.

    ``net_revenue`` defaults to the value the bridge identity demands, so a row
    is balanced unless a test deliberately corrupts it. Passing it explicitly is
    how the imbalance tests introduce a defect that no aggregation job would.
    """
    balanced = (
        _d(gms) - _d(discount) + _d(tax) + _d(shipping) + _d(cod_surcharge) - _d(refunds)
    )
    row = AggOrderDaily(
        bucket_date=day,
        tz_generation=generation,
        computed_at=datetime(2010, 12, 31, 0, 0, 0),
        orders_total=orders_total,
        orders_paid=orders_total,
        order_value_created=_d(paid_order_value),
        paid_order_value=_d(paid_order_value),
        gross_merchandise_sales=_d(gms),
        net_merchandise_sales=_d(gms) - _d(discount),
        net_revenue=balanced if net_revenue is None else _d(net_revenue),
        subtotal_sum=_d(gms),
        tax_sum=_d(tax),
        discount_sum=_d(discount),
        payment_discount_sum=_d("0.00"),
        shipping_income=_d(shipping),
        cod_surcharge_sum=_d(cod_surcharge),
        refund_sum=_d(refunds),
        cogs_sum=_d("0.00"),
        units=orders_total,
        costed_units=orders_total,
        distinct_customers=orders_total,
        new_customers=orders_total,
        returning_customers=0,
    )
    db.add(row)
    db.flush()
    return row


def _create_outbox(
    db: Session,
    order: Order,
    *,
    transaction_id: str | None = None,
    event_name: str = OutboxEventName.PURCHASE,
    status: str = OutboxStatus.PENDING,
) -> AnalyticsEventOutbox:
    row = AnalyticsEventOutbox(
        event_name=event_name,
        transaction_id=transaction_id or (order.order_number or f"ORD{order.id}"),
        order_id=int(order.id),
        occurred_at=order.created_at,
        payload={"value": str(order.total_amount), "currency": "INR", "items": []},
        consent_state=ConsentState.GRANTED,
        status=status,
        attempts=0,
    )
    db.add(row)
    db.flush()
    return row


def _create_payment(
    db: Session,
    order: Order,
    *,
    amount: str | None = None,
    gateway_payment_id: str | None = None,
    status: PaymentTxnStatus = PaymentTxnStatus.PAID,
) -> OrderPayment:
    row = OrderPayment(
        order_id=int(order.id),
        gateway="razorpay",
        gateway_payment_id=gateway_payment_id or f"pay_{_uid()}",
        payment_method="prepaid",
        payment_status=status,
        amount=_d(amount) if amount is not None else order.total_amount,
        currency="INR",
        paid_at=order.created_at,
    )
    db.add(row)
    db.flush()
    return row


def _create_shipment(
    db: Session,
    order: Order,
    *,
    awb: str,
    status: ShipmentStatus,
    delivered_at: datetime | None,
) -> Shipment:
    row = Shipment(
        order_id=int(order.id),
        courier_partner="testcourier",
        awb_number=awb,
        shipment_status=status,
        delivered_at=delivered_at,
    )
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------------------
# Sandbox hygiene
# ---------------------------------------------------------------------------


def _ids(s: Session, sql: str, params: dict[str, Any]) -> list[int]:
    return [int(i) for i in s.execute(text(sql), params).scalars().all()]


def _sweep_once() -> None:
    """Remove every row this module could have written, anywhere in 2010.

    By date range and by name pattern, not by an id list held in memory: the
    point is to survive a previous run that died before its teardown, and that
    run's id list died with it. Children go before parents so this works whether
    or not the FKs were created with ON DELETE CASCADE.

    The parent ids are resolved with a plain SELECT and the deletes then target
    an explicit id list, rather than the more obvious
    ``DELETE ... WHERE order_id IN (SELECT id FROM orders WHERE created_at ...)``.
    That form makes InnoDB take next-key locks over a *range* of a second table
    inside a multi-statement transaction, which on a shared throwaway MySQL
    deadlocks against anything else touching `orders` — observed, not
    hypothetical. A consistent read takes no locks at all.
    """
    bounds: dict[str, Any] = {"a": SANDBOX_FROM, "b": SANDBOX_TO}
    with SessionLocal() as s:
        order_ids = _ids(
            s,
            "SELECT id FROM orders WHERE created_at >= :a AND created_at < :b",
            bounds,
        )
        user_ids = _ids(
            s, "SELECT id FROM users WHERE email LIKE :pat", {"pat": USER_EMAIL_PATTERN}
        )
        product_ids = _ids(
            s, "SELECT id FROM products WHERE sku LIKE :pat", {"pat": SKU_PATTERN}
        )

        s.execute(
            text(
                "DELETE FROM analytics_alerts "
                "WHERE bucket_date >= :a AND bucket_date < :b"
            ),
            bounds,
        )
        s.execute(
            text(
                "DELETE FROM analytics_event_outbox "
                "WHERE occurred_at >= :a AND occurred_at < :b"
            ),
            bounds,
        )
        s.execute(
            text(
                "DELETE FROM agg_order_daily "
                "WHERE bucket_date >= :a AND bucket_date < :b"
            ),
            bounds,
        )
        if order_ids:
            ids = {"ids": tuple(order_ids)}
            for child in ("order_items", "order_payments", "shipments"):
                s.execute(text(f"DELETE FROM {child} WHERE order_id IN :ids"), ids)
            s.execute(text("DELETE FROM orders WHERE id IN :ids"), ids)
        if user_ids:
            ids = {"ids": tuple(user_ids)}
            s.execute(
                text("DELETE FROM points_transactions WHERE user_id IN :ids"), ids
            )
            s.execute(text("DELETE FROM users WHERE id IN :ids"), ids)
        if product_ids:
            s.execute(
                text("DELETE FROM products WHERE id IN :ids"),
                {"ids": tuple(product_ids)},
            )
        s.commit()


def _sweep(attempts: int = 4) -> None:
    """`_sweep_once`, retried on a deadlock.

    A teardown that loses a lock race must not fail the suite: the assertion has
    already passed or failed on its own merits by then, and a flaky clean-up
    would make an honest regression indistinguishable from noise. The retry is
    bounded, and the last attempt is allowed to raise — a sandbox that genuinely
    cannot be cleaned has to be loud, because every later run reads its leftovers.
    """
    for attempt in range(attempts):
        try:
            _sweep_once()
            return
        except OperationalError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.25 * (attempt + 1))


def _generation(db: Session) -> int:
    return int(active_generation(db).generation)


def _alerts_in_sandbox(db: Session) -> list[AnalyticsAlert]:
    return list(
        db.execute(
            select(AnalyticsAlert).where(
                AnalyticsAlert.bucket_date >= SANDBOX_FROM,
                AnalyticsAlert.bucket_date < SANDBOX_TO,
            )
        )
        .scalars()
        .all()
    )


def _day(check: Any, when: date):
    return next((d for d in check.days if d.bucket_date == when), None)


# ---------------------------------------------------------------------------
# 1. The parity anchor
# ---------------------------------------------------------------------------


class TestLegacyParityAnchor:
    """``MarginService.paid_order_value`` vs ``DashboardService._revenue_summary``."""

    def test_legacy_matches_new_exactly(self) -> None:
        """The shadow-mode anchor on a realistic mixed-status fixture.

        Statuses that are NOT revenue (pending, cancelled, refunded) are seeded
        alongside the ones that are, because a parity test over paid orders only
        would pass even if one side had forgotten the status filter entirely.
        """
        _sweep()
        owned = _Owned()
        db = SessionLocal()
        try:
            date_from, date_to = _window(1)
            user = _create_user(db, owned)
            product = _create_product(db, owned, price="500.00")
            day_one = _midday(db, date(2010, 1, 2))
            day_two = _midday(db, date(2010, 1, 3))

            _create_order(db, owned, user, product, at=day_one, status=OrderStatus.PAID,
                          tax="90.00", shipping="80.00")
            _create_order(db, owned, user, product, at=day_one, qty=2,
                          status=OrderStatus.SHIPPED, discount="100.00")
            _create_order(db, owned, user, product, at=day_two,
                          status=OrderStatus.DELIVERED, cod_surcharge="30.00",
                          payment_method="cod")
            # Not revenue under the legacy rule; both sides must drop all three.
            _create_order(db, owned, user, product, at=day_two, status=OrderStatus.PENDING)
            _create_order(db, owned, user, product, at=day_two, status=OrderStatus.CANCELLED)
            _create_order(db, owned, user, product, at=day_two, status=OrderStatus.REFUNDED)
            db.commit()

            report = run_reconciliation(db, date_from, date_to, write_alerts=False)
            check = report.by_key(CheckKey.LEGACY_VS_NEW)

            # 670 (500 + 90 + 80) + 900 (1000 - 100) + 530 (500 + 30)
            assert check.left_value == _d("2100.00")
            assert check.right_value == _d("2100.00")
            assert check.difference == _d("0.00")
            assert check.difference_pct == _d("0.0000")
            assert check.status == CheckStatus.MATCH
            assert check.coverage_pct == _d("100.00")
            assert check.population == 3
        finally:
            db.close()
            _sweep()

    def test_empty_window_reports_every_check_as_not_configured(self) -> None:
        """A window with nothing in it is never a clean bill of health.

        Six listed rows, six NULL values, zero matches. This is the composite
        assertion the whole module exists for: the state that is easiest to
        render as "all clear" renders as "nothing was checked".
        """
        _sweep()
        db = SessionLocal()
        try:
            date_from, date_to = _window(12)
            report = run_reconciliation(db, date_from, date_to, write_alerts=False)

            assert len(report.checks) == len(CHECK_ORDER)
            assert len(report.not_configured) == len(CHECK_ORDER)
            assert report.variances == ()
            assert report.errors == ()
            assert not [c for c in report.checks if c.status == CheckStatus.MATCH]
            for check in report.checks:
                assert check.left_value is None
                assert check.right_value is None
                assert check.difference is None
                assert check.difference_pct is None
                assert check.coverage_pct is None
                assert check.days == ()
                assert check.detail, f"{check.check_key} must explain itself"
        finally:
            db.close()
            _sweep()

    def test_reversed_window_is_refused(self) -> None:
        """``[from, to)`` is half-open; an empty or reversed window is a bug."""
        db = SessionLocal()
        try:
            with pytest.raises(ValueError, match="half-open"):
                run_reconciliation(db, date(2010, 4, 10), date(2010, 4, 10))
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 2 + 3. Rollup and bridge
# ---------------------------------------------------------------------------


class TestRollupChecks:
    def test_corrupted_rollup_row_is_detected_with_the_exact_difference(self) -> None:
        """A stored bucket that disagrees with the live orders behind it.

        The corruption is +250.00 on one day only, so the assertion is on the
        exact gap and on which day carries it — a check that merely noticed
        "something is off" would not tell an operator which bucket to recompute.
        """
        _sweep()
        owned = _Owned()
        db = SessionLocal()
        try:
            date_from, date_to = _window(2)
            generation = _generation(db)
            user = _create_user(db, owned)
            product = _create_product(db, owned, price="400.00")
            good_day, bad_day = date(2010, 2, 2), date(2010, 2, 3)

            _create_order(db, owned, user, product, at=_midday(db, good_day))
            _create_order(db, owned, user, product, at=_midday(db, bad_day))
            db.commit()

            _create_rollup(db, good_day, generation=generation,
                           orders_total=1, paid_order_value="400.00", gms="400.00")
            # The defect: this bucket claims 250.00 more than the orders hold.
            _create_rollup(db, bad_day, generation=generation,
                           orders_total=1, paid_order_value="650.00", gms="650.00")
            db.commit()

            report = run_reconciliation(db, date_from, date_to, write_alerts=False)
            check = report.by_key(CheckKey.ROLLUP_VS_LIVE)

            assert check.status == CheckStatus.VARIANCE
            assert check.left_value == _d("1050.00")   # rollup
            assert check.right_value == _d("800.00")   # live
            assert check.difference == _d("250.00")
            assert check.difference_pct == _d("31.2500")

            assert _day(check, good_day).difference == _d("0.00")
            assert _day(check, bad_day).difference == _d("250.00")
            assert _day(check, bad_day).difference_pct == _d("62.5000")

            # The rollup rows are internally consistent, so the bridge is clean.
            # A single defect must not smear across unrelated checks.
            assert report.by_key(CheckKey.REVENUE_BRIDGE).status == CheckStatus.MATCH
        finally:
            db.close()
            _sweep()

    def test_bridge_imbalance_is_detected_and_the_gap_reported_exactly(self) -> None:
        """``gms - discounts + tax + shipping + cod - refunds == net_revenue``.

        Only ``net_revenue`` is corrupted, by exactly 40.00, so the reported gap
        must be exactly 40.00 — the identity's whole value is that it names the
        size of the inconsistency, not merely its existence.
        """
        _sweep()
        db = SessionLocal()
        try:
            date_from, date_to = _window(3)
            generation = _generation(db)
            good_day, bad_day = date(2010, 3, 2), date(2010, 3, 3)

            terms = dict(gms="1000.00", discount="100.00", tax="90.00",
                         shipping="80.00", cod_surcharge="0.00", refunds="0.00")
            _create_rollup(db, good_day, generation=generation, **terms)
            # Balanced value is 1070.00; store 1030.00 instead.
            _create_rollup(db, bad_day, generation=generation,
                           net_revenue="1030.00", **terms)
            db.commit()

            report = run_reconciliation(db, date_from, date_to, write_alerts=False)
            check = report.by_key(CheckKey.REVENUE_BRIDGE)

            assert check.status == CheckStatus.VARIANCE
            assert check.left_value == _d("2140.00")   # what the terms require
            assert check.right_value == _d("2100.00")  # what is stored
            assert check.difference == _d("40.00")

            assert _day(check, good_day).difference == _d("0.00")
            assert _day(check, bad_day).difference == _d("40.00")
            assert check.context["unbalanced_days"] == [bad_day.isoformat()]
        finally:
            db.close()
            _sweep()

    def test_offsetting_daily_imbalances_do_not_cancel_into_a_match(self) -> None:
        """+500 on one day and -500 on the next is two broken buckets, not zero.

        A check that read only the window total would call this clean. Status is
        therefore driven by the per-day imbalances, which is the only reading
        that survives a rollup whose errors happen to be symmetric.
        """
        _sweep()
        db = SessionLocal()
        try:
            date_from, date_to = _window(11)
            generation = _generation(db)
            terms = dict(gms="1000.00", discount="0.00", tax="0.00",
                         shipping="0.00", cod_surcharge="0.00", refunds="0.00")
            _create_rollup(db, date(2010, 11, 2), generation=generation,
                           net_revenue="500.00", **terms)
            _create_rollup(db, date(2010, 11, 3), generation=generation,
                           net_revenue="1500.00", **terms)
            db.commit()

            check = run_reconciliation(
                db, date_from, date_to, write_alerts=False
            ).by_key(CheckKey.REVENUE_BRIDGE)

            assert check.difference == _d("0.00")
            assert check.status == CheckStatus.VARIANCE
            assert len(check.context["unbalanced_days"]) == 2
        finally:
            db.close()
            _sweep()


# ---------------------------------------------------------------------------
# 4 + 5. GA4
# ---------------------------------------------------------------------------


class TestGa4PurchaseParity:
    def test_ga4_check_is_not_configured_and_never_match(self) -> None:
        """GA4 is not connected, so this check has no counterparty to read.

        Asserted three ways on purpose. NULL values prove nothing was measured;
        the explicit ``!= match`` proves the check did not grade itself a pass on
        a comparison it never made; and the reason string proves an admin is told
        what to connect rather than left with a blank cell.
        """
        _sweep()
        owned = _Owned()
        db = SessionLocal()
        try:
            date_from, date_to = _window(4)
            user = _create_user(db, owned)
            product = _create_product(db, owned, price="750.00")
            order = _create_order(db, owned, user, product, at=_midday(db, date(2010, 4, 2)))
            db.commit()
            # Even with a perfectly delivered purchase event on every paid order,
            # the check must NOT claim parity: what GA4 kept is unknowable here.
            _create_outbox(db, order, status=OutboxStatus.DELIVERED)
            db.commit()

            check = run_reconciliation(
                db, date_from, date_to, write_alerts=False
            ).by_key(CheckKey.GA4_PURCHASE_PARITY)

            assert check.status == CheckStatus.NOT_CONFIGURED
            assert check.status != CheckStatus.MATCH
            assert check.left_value is None
            assert check.right_value is None
            assert check.difference is None
            assert check.difference_pct is None
            assert check.coverage_pct is None
            assert check.missing_ids == ()

            reasons = " ".join(check.context["requires"])
            assert "Data API" in reasons
            # The outbox coverage is real and is reported — but under its own
            # name, never as the coverage of a comparison that did not happen.
            assert check.context["outbox_coverage_pct"] == _d("100.00")
            assert check.context["purchase_events_by_status"] == {
                OutboxStatus.DELIVERED: 1
            }
        finally:
            db.close()
            _sweep()

    def test_missing_and_duplicate_transaction_ids_are_listed_by_id(self) -> None:
        """Names, not counts.

        "3 conversions are missing" is not actionable; the order numbers are.
        The duplicate case is the expensive one: two purchase events for one
        order put the same revenue into GA4 twice, which inflates reported ROAS
        and therefore changes what the business spends money on.
        """
        _sweep()
        owned = _Owned()
        db = SessionLocal()
        try:
            date_from, date_to = _window(5)
            user = _create_user(db, owned)
            product = _create_product(db, owned, price="600.00")
            at = _midday(db, date(2010, 5, 2))

            clean = _create_order(db, owned, user, product, at=at)
            doubled = _create_order(db, owned, user, product, at=at)
            never_sent = _create_order(db, owned, user, product, at=at)
            db.commit()

            _create_outbox(db, clean)
            _create_outbox(db, doubled)
            alt_id = f"ALT-{_uid()}"
            _create_outbox(db, doubled, transaction_id=alt_id)
            db.commit()

            check = run_reconciliation(
                db, date_from, date_to, write_alerts=False
            ).by_key(CheckKey.GA4_PURCHASE_PARITY)

            assert check.missing_ids == (never_sent.order_number,)
            assert set(check.duplicate_ids) == {doubled.order_number, alt_id}
            assert clean.order_number not in check.missing_ids
            assert clean.order_number not in check.duplicate_ids

            assert check.context["missing_purchase_events"] == 1
            assert check.context["outbox_coverage_pct"] == _d("66.67")
            # Evidence, not a verdict: the status stays not_configured.
            assert check.status == CheckStatus.NOT_CONFIGURED
        finally:
            db.close()
            _sweep()


# ---------------------------------------------------------------------------
# 5 + 6. Gateway and courier
# ---------------------------------------------------------------------------


class TestGatewayAndCourier:
    def test_paid_order_without_a_gateway_transaction_is_named(self) -> None:
        """An order that says the money arrived, with nothing recording it."""
        _sweep()
        owned = _Owned()
        db = SessionLocal()
        try:
            date_from, date_to = _window(9)
            user = _create_user(db, owned)
            product = _create_product(db, owned, price="1000.00")
            at = _midday(db, date(2010, 9, 2))

            backed = _create_order(db, owned, user, product, at=at)
            unbacked = _create_order(db, owned, user, product, at=at)
            db.commit()
            _create_payment(db, backed)
            db.commit()

            check = run_reconciliation(
                db, date_from, date_to, write_alerts=False
            ).by_key(CheckKey.GATEWAY_VS_PAID_ORDERS)

            assert check.status == CheckStatus.VARIANCE
            assert check.left_value == _d("1000.00")   # order_payments
            assert check.right_value == _d("2000.00")  # orders
            assert check.difference == _d("-1000.00")
            assert check.coverage_pct == _d("50.00")
            assert check.missing_ids == (unbacked.order_number,)
        finally:
            db.close()
            _sweep()

    def test_one_captured_payment_id_recorded_twice_is_flagged(self) -> None:
        """A replayed webhook counts the same capture as two payments."""
        _sweep()
        owned = _Owned()
        db = SessionLocal()
        try:
            date_from, date_to = _window(10)
            user = _create_user(db, owned)
            product = _create_product(db, owned, price="500.00")
            order = _create_order(db, owned, user, product, at=_midday(db, date(2010, 10, 2)))
            db.commit()
            replayed = f"pay_{_uid()}"
            _create_payment(db, order, amount="500.00", gateway_payment_id=replayed)
            _create_payment(db, order, amount="500.00", gateway_payment_id=replayed)
            db.commit()

            check = run_reconciliation(
                db, date_from, date_to, write_alerts=False
            ).by_key(CheckKey.GATEWAY_VS_PAID_ORDERS)

            assert check.duplicate_ids == (replayed,)
            assert check.left_value == _d("1000.00")
            assert check.right_value == _d("500.00")
            assert check.status == CheckStatus.VARIANCE
        finally:
            db.close()
            _sweep()

    def test_delivery_asserted_without_a_courier_scan_is_a_variance(self) -> None:
        """Both directions of the mismatch, including the one counts hide.

        Two shipments carry a courier scan and two are marked delivered, so the
        counts are equal — but they are not the *same* two. A check comparing
        only totals would report a match over a set mismatch.
        """
        _sweep()
        owned = _Owned()
        db = SessionLocal()
        try:
            date_from, date_to = _window(6)
            user = _create_user(db, owned)
            product = _create_product(db, owned, price="300.00")
            at = _midday(db, date(2010, 6, 2))
            order = _create_order(db, owned, user, product, at=at)
            db.commit()

            _create_shipment(db, order, awb="AWB-AGREE", status=ShipmentStatus.DELIVERED,
                             delivered_at=at)
            asserted = _create_shipment(db, order, awb="AWB-NOSCAN",
                                        status=ShipmentStatus.DELIVERED, delivered_at=None)
            _create_shipment(db, order, awb="AWB-DROPPED",
                             status=ShipmentStatus.IN_TRANSIT, delivered_at=at)
            db.commit()

            check = run_reconciliation(
                db, date_from, date_to, write_alerts=False
            ).by_key(CheckKey.COURIER_VS_SHIPMENTS)

            assert check.left_value == _d("2")   # courier scans
            assert check.right_value == _d("2")  # marked delivered
            assert check.difference == _d("0")
            assert check.status == CheckStatus.VARIANCE
            assert check.missing_ids == (asserted.awb_number,)
            assert check.context["scanned_without_delivered_status"] == ["AWB-DROPPED"]
            assert check.coverage_pct == _d("66.67")
        finally:
            db.close()
            _sweep()

    def test_no_delivery_on_either_side_is_not_configured_not_a_match(self) -> None:
        """Shipments exist but none has been delivered by anyone's account.

        Nothing to compare is not agreement. Reporting ``match`` here would put a
        green tick on a window in which no delivery was ever confirmed.
        """
        _sweep()
        owned = _Owned()
        db = SessionLocal()
        try:
            date_from, date_to = _window(7)
            user = _create_user(db, owned)
            product = _create_product(db, owned, price="300.00")
            at = _midday(db, date(2010, 7, 2))
            order = _create_order(db, owned, user, product, at=at)
            db.commit()
            _create_shipment(db, order, awb="AWB-INTRANSIT",
                             status=ShipmentStatus.IN_TRANSIT, delivered_at=None)
            db.commit()

            check = run_reconciliation(
                db, date_from, date_to, write_alerts=False
            ).by_key(CheckKey.COURIER_VS_SHIPMENTS)

            assert check.status == CheckStatus.NOT_CONFIGURED
            assert check.status != CheckStatus.MATCH
            assert check.left_value is None
            assert check.right_value is None
            assert check.coverage_pct is None
        finally:
            db.close()
            _sweep()


# ---------------------------------------------------------------------------
# 7 + 8. Coverage and the sparse-day rule
# ---------------------------------------------------------------------------


class TestCoverageAndSparseDays:
    def test_coverage_is_below_100_when_part_of_the_window_has_no_data(self) -> None:
        """Two of five days were aggregated, so 40% of the window was checked.

        Coverage is what stops the other 60% being read as balanced. Without it,
        a rollup that stopped running on the 3rd looks exactly like a rollup that
        ran all week and found nothing wrong.
        """
        _sweep()
        db = SessionLocal()
        try:
            date_from, date_to = _window(8, days=5)
            generation = _generation(db)
            _create_rollup(db, date(2010, 8, 1), generation=generation, gms="100.00")
            _create_rollup(db, date(2010, 8, 2), generation=generation, gms="200.00")
            db.commit()

            report = run_reconciliation(db, date_from, date_to, write_alerts=False)

            for key in (CheckKey.ROLLUP_VS_LIVE, CheckKey.REVENUE_BRIDGE):
                check = report.by_key(key)
                assert check.population == 5
                assert check.compared == 2
                assert check.coverage_pct == _d("40.00")
                assert check.coverage_pct < _d("100.00")

            uncovered = report.by_key(CheckKey.ROLLUP_VS_LIVE).context["uncovered_days"]
            assert uncovered == ["2010-08-03", "2010-08-04", "2010-08-05"]
        finally:
            db.close()
            _sweep()

    def test_a_day_with_no_data_produces_no_row(self) -> None:
        """Never densify.

        Three of the five days were never aggregated. They must be absent from
        ``days``, not present with a 0.00 variance — a zero here is a positive
        claim ("checked, and it balanced") and manufacturing one for a day nobody
        checked is the exact misstatement this report exists to prevent.
        """
        _sweep()
        db = SessionLocal()
        try:
            date_from, date_to = _window(8, days=5)
            generation = _generation(db)
            seeded = (date(2010, 8, 1), date(2010, 8, 4))
            for day in seeded:
                _create_rollup(db, day, generation=generation, gms="100.00")
            db.commit()

            report = run_reconciliation(db, date_from, date_to, write_alerts=False)
            check = report.by_key(CheckKey.REVENUE_BRIDGE)

            assert tuple(d.bucket_date for d in check.days) == seeded
            for absent in (date(2010, 8, 2), date(2010, 8, 3), date(2010, 8, 5)):
                assert _day(check, absent) is None, (
                    f"{absent} was never checked and must have no row; a 0.00 "
                    "variance would assert a check that never ran"
                )
            # The days that WERE checked report a real, earned zero.
            assert all(d.difference == _d("0.00") for d in check.days)
            assert check.status == CheckStatus.MATCH
        finally:
            db.close()
            _sweep()


# ---------------------------------------------------------------------------
# 9. Alerting
# ---------------------------------------------------------------------------


class TestAlerting:
    def test_a_material_variance_writes_exactly_one_alert(self) -> None:
        """The variance has to be visible without anyone opening this page.

        The fixture isolates a single material finding — a rollup claiming
        revenue no order supports — so "exactly one" is a real assertion about
        one alert per material check rather than an accident of the data.
        """
        _sweep()
        db = SessionLocal()
        try:
            date_from, date_to = _window(2, days=3)
            generation = _generation(db)
            # No orders at all: the rollup asserts 500.00 the orders table has
            # no record of. Bridge terms stay internally consistent, so this is
            # the only material check.
            _create_rollup(db, date(2010, 2, 2), generation=generation,
                           paid_order_value="500.00", gms="500.00")
            db.commit()

            report = run_reconciliation(db, date_from, date_to, write_alerts=True)

            assert report.by_key(CheckKey.ROLLUP_VS_LIVE).status == CheckStatus.VARIANCE
            assert report.by_key(CheckKey.REVENUE_BRIDGE).status == CheckStatus.MATCH
            assert len(report.alert_ids) == 1

            alerts = _alerts_in_sandbox(db)
            assert len(alerts) == 1
            alert = alerts[0]
            assert alert.id == report.alert_ids[0]
            assert alert.rule_key == AlertRuleKey.TRACKING_FAILURE
            assert alert.severity == AlertSeverity.CRITICAL
            assert alert.metric == CheckKey.ROLLUP_VS_LIVE
            assert alert.status == AlertStatus.OPEN
            # Expected is the live orders side; actual is what the rollup claims.
            assert alert.expected_low == _d("0.0000")
            assert alert.actual_value == _d("500.0000")
            assert alert.bucket_date == date_to - timedelta(days=1)
            # The evidence has to survive the condition clearing.
            assert alert.context["difference"] == "500.00"
            assert alert.context["window"] == f"{date_from}..{date_to}"
        finally:
            db.close()
            _sweep()

    def test_a_clean_window_writes_no_alert(self) -> None:
        """Alerts are for findings. A balanced window is silent."""
        _sweep()
        owned = _Owned()
        db = SessionLocal()
        try:
            date_from, date_to = _window(3, days=2)
            generation = _generation(db)
            user = _create_user(db, owned)
            product = _create_product(db, owned, price="400.00")
            day = date(2010, 3, 1)
            order = _create_order(db, owned, user, product, at=_midday(db, day))
            db.commit()
            # Every runnable check has to be clean, not just the rollup ones:
            # a paid order with no captured payment is itself a real finding, so
            # leaving it out would make this test pass for the wrong reason.
            _create_payment(db, order)
            _create_rollup(db, day, generation=generation, orders_total=1,
                           paid_order_value="400.00", gms="400.00")
            _create_rollup(db, date(2010, 3, 2), generation=generation)
            db.commit()

            report = run_reconciliation(db, date_from, date_to, write_alerts=True)

            assert report.by_key(CheckKey.ROLLUP_VS_LIVE).status == CheckStatus.MATCH
            assert report.by_key(CheckKey.REVENUE_BRIDGE).status == CheckStatus.MATCH
            assert report.by_key(CheckKey.LEGACY_VS_NEW).status == CheckStatus.MATCH
            assert report.by_key(CheckKey.GATEWAY_VS_PAID_ORDERS).status == (
                CheckStatus.MATCH
            )
            assert report.alert_ids == ()
            assert _alerts_in_sandbox(db) == []
        finally:
            db.close()
            _sweep()


# ---------------------------------------------------------------------------
# 10. Report shape
# ---------------------------------------------------------------------------


class TestReportShape:
    def test_every_check_is_listed_once_and_renders_for_the_resolver(self) -> None:
        """One row per check, always, in a shape the existing resolver renders.

        The row count is asserted against ``CHECK_ORDER`` rather than a literal:
        a check that silently stops being emitted is the failure mode a
        reconciliation table cannot detect about itself.
        """
        _sweep()
        db = SessionLocal()
        try:
            date_from, date_to = _window(12, days=2)
            report = run_reconciliation(db, date_from, date_to, write_alerts=False)

            assert tuple(c.check_key for c in report.checks) == CHECK_ORDER
            rows = report.to_rows()
            assert len(rows) == len(CHECK_ORDER)
            assert {r["period"] for r in rows} == {f"{date_from}..{date_to}"}
            # The resolver's vocabulary spells a passing check `matched`.
            assert all(r["status"] != CheckStatus.MATCH for r in rows)
            assert all(set(r) == {
                "check_name", "period", "status", "source_value",
                "rollup_value", "variance_pct", "detail",
            } for r in rows)
        finally:
            db.close()
            _sweep()
