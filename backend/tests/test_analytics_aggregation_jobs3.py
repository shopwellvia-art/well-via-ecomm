"""Tests for the last four aggregation jobs.

Covers ``payment_daily``, ``shipment_geo_daily``, ``promo_daily`` and
``cohort_monthly`` — the four rollups that complete the twelve.
``tests/test_analytics_aggregation.py`` covers the runner and the first three
jobs and ``tests/test_analytics_aggregation_jobs2.py`` the middle five; this file
deliberately repeats none of what they already prove, only what these four each
do.

What actually has to be true here
---------------------------------
A rollup that computes the wrong number is caught the first time somebody reads
the dashboard. A rollup that computes the *right* number and then computes it
again on top of itself is never caught at all, and neither is one that keeps
serving a group key whose data has gone. So the tests carrying the weight are:

  * ``test_*_is_idempotent_*`` — four of them, one per job. The same bucket,
    twice, identical column values. Not "no error" and not "one row": the
    numbers. An ``ON DUPLICATE KEY UPDATE col = col + VALUES(col)`` passes
    everything else in this file and fails these.
  * ``test_*_removes_a_group_key_that_disappeared`` — four of them. All four of
    these tables are dimensioned, so all four have a key that can vanish: the
    day's only attempt on a gateway, the day's only order from a pincode, a
    coupon whose redemption was backed out, a cohort whose only customer's order
    was cancelled. Delete-then-insert is the only write pattern that can express
    that, and an upsert leaves the vacated row at its old numbers forever.
  * ``test_*_bucketing_is_store_local`` — four of them. Two events on the same
    **UTC** date belonging to two different **store-local** reporting days (and,
    for cohorts, two different reporting *months*). 23:00 IST is 17:30 UTC the
    same day; 00:30 IST is 19:00 UTC the day before. A UTC bucketer puts both
    together and is wrong by 5.5 hours of trade, every day, unrecoverably.

Then the two claims specific to this wave, which are exactly the ones a
plausible-looking implementation gets wrong:

  * a pincode is normalised, never **truncated**
    (``test_geo_daily_normalises_pincodes_and_never_truncates``). Cutting
    "5600012" back to "560001" invents an order from a real Bengaluru pincode
    that nobody placed, and it renders on the map looking like a measurement;
  * ``cohort_size`` is written **once per cohort** and repeated on every period
    row, so summing it multiplies the cohort by the number of periods
    (``test_cohort_monthly_writes_cohort_size_once_per_cohort``).

Isolation strategy
------------------
Every fixture lives in **2014-2015**, a window this store has never traded in.
Three of the four jobs are then scoped by **their own dimension key**: each test
mints a unique gateway, state or coupon code, so its row is deterministic even
when the shared throwaway MySQL is not, and a concurrent suite can neither move
the numbers nor be counted by them. That is stronger than a baseline/delta here,
because these are dimensioned tables where a key this test invented cannot
collide.

``cohort_monthly`` cannot be scoped that way: its group key is an acquisition
*month*, which is shared by construction, and ``cohort_size`` is a global count
over everyone whose first order landed in that month. It also recomputes the
whole trailing 24-cohort window on every run rather than one bucket. So it keeps
a loud precondition instead — ``_assert_cohort_window_is_empty`` — because a
clear failure beats a number that is quietly wrong.

Teardown deletes every owned row through a fresh session, including the ``agg_*``
rows for the sandbox dates, the cohort rows (which are keyed by ``cohort_month``,
not by the bucket the job was handed) and the ``analytics_sync_runs`` rows written
under this module's worker id. No db fixture exists in ``conftest.py``; each test
owns its ``SessionLocal()`` and closes it in ``finally``.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.analytics_base import DIMENSION_UNKNOWN
from app.models.analytics_rollups import (
    AggCustomerCohortMonthly,
    AggGeoDaily,
    AggPaymentDaily,
    AggPromoDaily,
)
from app.models.coupon import Coupon, CouponUsage, DiscountType
from app.models.order import Order, OrderItem, OrderStatus
from app.models.order_payment import OrderPayment, PaymentTxnStatus
from app.models.payment_event import PaymentEvent, PaymentEventType
from app.models.product import Product
from app.models.return_request import ReturnItem, ReturnRequest, ReturnStatus
from app.models.shipment import Shipment, ShipmentStatus
from app.models.user import User
from app.services.analytics.aggregation import AggregationRunner
from app.services.analytics.aggregation.jobs_finance import (
    COHORT_MONTHS_RECOMPUTED,
    PINCODE_DIGITS,
    _pincode,
)
from app.services.analytics.metric_kind import MetricKind, classify
from app.services.analytics.timebox import active_generation, day_bounds_utc, store_timezone

# ---------------------------------------------------------------------------
# The 2014-2015 sandbox
# ---------------------------------------------------------------------------

#: Wide enough to cover the cohort job's period rows, whose `bucket_date` is the
#: first day of the period being measured and therefore reaches back to the
#: oldest cohort in the trailing window, not just to the bucket it was handed.
SANDBOX_FIRST = date(2014, 1, 1)
SANDBOX_LAST = date(2015, 12, 31)

DAY_PAY = date(2015, 3, 4)
DAY_PAY_TZ_A = date(2015, 3, 7)
DAY_PAY_TZ_B = date(2015, 3, 8)
DAY_PAY_GONE = date(2015, 3, 11)

DAY_GEO = date(2015, 4, 3)
DAY_GEO_DELIVERED = date(2015, 4, 6)
DAY_GEO_TZ_A = date(2015, 4, 9)
DAY_GEO_TZ_B = date(2015, 4, 10)
DAY_GEO_GONE = date(2015, 4, 14)
DAY_GEO_PIN = date(2015, 4, 17)

DAY_PROMO = date(2015, 5, 5)
DAY_PROMO_TZ_A = date(2015, 5, 8)
DAY_PROMO_TZ_B = date(2015, 5, 9)
DAY_PROMO_GONE = date(2015, 5, 13)

#: The day the cohort job is asked to run. Only its MONTH matters — the job
#: rebuilds the 24 cohorts ending with it — so 2014-01 .. 2015-12 is the window
#: every cohort assertion below lives in.
COHORT_RUN = date(2015, 12, 15)
COHORT_ACQUIRED = date(2015, 6, 10)
COHORT_SECOND_ORDER = date(2015, 6, 20)
COHORT_TOURIST = date(2015, 6, 25)
COHORT_RETURNED = date(2015, 8, 12)
COHORT_REFUND_ISSUED = date(2015, 8, 25)
#: 23:00 IST here and 00:30 IST on the next day share a UTC date and straddle a
#: store-local MONTH boundary, which is the unit a cohort is keyed by.
COHORT_TZ_LATE = date(2015, 6, 30)
COHORT_TZ_EARLY = date(2015, 7, 1)
COHORT_GONE = date(2015, 2, 9)

COHORT_MONTH_JUNE = "2015-06"
COHORT_MONTH_JULY = "2015-07"
COHORT_MONTH_FEBRUARY = "2015-02"

#: Distinctive enough that teardown can delete this module's run log without a
#: date filter, and that a stuck row in a shared DB is traceable to these tests.
WORKER_ID = "test-agg-jobs3"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _at(db: Session, day: date, hour: int, minute: int = 0) -> datetime:
    """The UTC instant that is ``hour:minute`` **store-local** on ``day``.

    Built from ``timebox.day_bounds_utc`` rather than by hand: the point of the
    bucketing tests is that the day boundary is the store's, and deriving the
    fixture from the same function the jobs use makes the assertion about
    bucketing rather than about two independent copies of the same arithmetic.
    """
    start, _ = day_bounds_utc(day, store_timezone(db))
    return start + timedelta(hours=hour, minutes=minute)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


class _Owned:
    """Ids this test created, so teardown removes exactly them."""

    def __init__(self) -> None:
        self.orders: list[int] = []
        self.products: list[int] = []
        self.users: list[int] = []
        self.returns: list[int] = []
        self.shipments: list[int] = []
        self.payments: list[int] = []
        self.events: list[int] = []
        self.coupons: list[int] = []
        self.usages: list[int] = []


def _create_user(db: Session, owned: _Owned) -> User:
    user = User(
        email=f"jobs3-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.flush()
    owned.users.append(user.id)
    return user


def _create_product(db: Session, owned: _Owned, *, price: str = "500.00") -> Product:
    product = Product(
        sku=f"SKU-J3-{_uid()}",
        name=f"Jobs3Product {_uid()}",
        price=Decimal(price),
        cost=Decimal("200.00"),
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
    created_at: datetime,
    status: OrderStatus = OrderStatus.PAID,
    payment_method: str = "prepaid",
    payment_instrument: str | None = None,
    cod_balance: str = "0",
    coupon_code: str | None = None,
    discount: str = "0",
    pincode: str | None = None,
    state: str | None = None,
) -> Order:
    """An order whose ``total_amount`` is built the way checkout builds it."""
    order_items: list[OrderItem] = []
    gross = Decimal("0.00")
    for product, quantity in items:
        order_items.append(
            OrderItem(
                product_id=product.id,
                quantity=quantity,
                unit_price=product.price,
                unit_cost=product.cost,
            )
        )
        gross += product.price * quantity

    total = gross - Decimal(discount)
    snapshot = None
    if state is not None or pincode is not None:
        # The frozen structured copy taken at checkout — the only place the geo
        # job reads `state` from, because the saved address can be edited later.
        snapshot = {
            "full_name": "Jobs3 Buyer",
            "line1": "1 Test Road",
            "city": "Testville",
            "state": state,
            "pincode": pincode,
            "country": "IN",
        }

    order = Order(
        user_id=user.id,
        status=status,
        subtotal=gross,
        tax_amount=Decimal("0.00"),
        discount_amount=Decimal(discount),
        payment_discount_amount=Decimal("0.00"),
        shipping_amount=Decimal("0.00"),
        cod_surcharge_amount=Decimal("0.00"),
        cod_balance=Decimal(cod_balance),
        total_amount=total,
        currency="INR",
        payment_method=payment_method,
        payment_instrument=payment_instrument,
        coupon_code=coupon_code,
        shipping_pincode=pincode,
        shipping_address_snapshot=snapshot,
        created_at=created_at,
    )
    order.items = order_items
    db.add(order)
    db.flush()
    owned.orders.append(order.id)
    return order


def _create_payment(
    db: Session,
    owned: _Owned,
    order: Order,
    *,
    created_at: datetime,
    gateway: str | None,
    method: str | None,
    status: PaymentTxnStatus,
    amount: str,
    paid_at: datetime | None = None,
) -> OrderPayment:
    payment = OrderPayment(
        order_id=order.id,
        gateway=gateway,
        payment_method=method,
        payment_status=status,
        amount=Decimal(amount),
        currency="INR",
        created_at=created_at,
        paid_at=paid_at,
    )
    db.add(payment)
    db.flush()
    owned.payments.append(payment.id)
    return payment


def _create_event(
    db: Session,
    owned: _Owned,
    order: Order,
    *,
    created_at: datetime,
    event_type: str,
    gateway_code: str | None,
    message: str | None = None,
    payment_status: str | None = None,
) -> PaymentEvent:
    event = PaymentEvent(
        order_id=order.id,
        gateway_code=gateway_code,
        event_type=event_type,
        payment_status=payment_status,
        message=message,
        created_at=created_at,
    )
    db.add(event)
    db.flush()
    owned.events.append(event.id)
    return event


def _create_shipment(
    db: Session,
    owned: _Owned,
    order: Order,
    *,
    created_at: datetime,
    status: ShipmentStatus,
    delivered_at: datetime | None = None,
) -> Shipment:
    shipment = Shipment(
        order_id=order.id,
        courier_partner=f"courier-{_uid()}",
        shipment_status=status,
        delivered_at=delivered_at,
        created_at=created_at,
    )
    db.add(shipment)
    db.flush()
    owned.shipments.append(shipment.id)
    return shipment


def _create_coupon(
    db: Session, owned: _Owned, *, is_loyalty_reward: bool = False
) -> Coupon:
    coupon = Coupon(
        code=f"J3{_uid().upper()}",
        description="jobs3 fixture",
        discount_type=DiscountType.FIXED,
        discount_value=Decimal("100.00"),
        is_active=True,
        is_loyalty_reward=is_loyalty_reward,
    )
    db.add(coupon)
    db.flush()
    owned.coupons.append(coupon.id)
    return coupon


def _create_usage(
    db: Session,
    owned: _Owned,
    coupon: Coupon,
    user: User,
    *,
    created_at: datetime,
    discount: str,
    order: Order | None = None,
) -> CouponUsage:
    usage = CouponUsage(
        coupon_id=coupon.id,
        user_id=user.id,
        order_id=order.id if order is not None else None,
        discount_amount=Decimal(discount),
        created_at=created_at,
    )
    db.add(usage)
    db.flush()
    owned.usages.append(usage.id)
    return usage


def _create_refunded_return(
    db: Session,
    owned: _Owned,
    order: Order,
    *,
    refunded_at: datetime,
    refund_amount: str,
) -> ReturnRequest:
    request = ReturnRequest(
        order_id=order.id,
        user_id=order.user_id,
        status=ReturnStatus.REFUNDED,
        reason="defective",
        refund_amount=Decimal(refund_amount),
        requested_at=refunded_at - timedelta(days=1),
        refunded_at=refunded_at,
    )
    request.items = [ReturnItem(order_item_id=order.items[0].id, quantity=1)]
    db.add(request)
    db.flush()
    owned.returns.append(request.id)
    return request


# ---------------------------------------------------------------------------
# Teardown + preconditions
# ---------------------------------------------------------------------------


def _cleanup(owned: _Owned) -> None:
    """Delete everything this test owned, through a fresh session.

    A fresh session so teardown cannot be skipped by a half-rolled-back
    transaction in the test's own session. The rollup and run-log rows go by
    sandbox date / worker id rather than by id, because they are written by Core
    statements the test never sees the ids of — and the cohort rows go by
    ``cohort_month``, which is the only key that table is scoped by.
    """
    with SessionLocal() as session:
        for table, ids in (
            ("payment_events", owned.events),
            ("order_payments", owned.payments),
            ("shipments", owned.shipments),
            ("coupon_usages", owned.usages),
        ):
            if ids:
                session.execute(
                    text(f"DELETE FROM {table} WHERE id IN :ids"),
                    {"ids": tuple(ids)},
                )
        if owned.coupons:
            session.execute(
                text("DELETE FROM coupons WHERE id IN :ids"),
                {"ids": tuple(owned.coupons)},
            )
        if owned.returns:
            session.execute(
                text("DELETE FROM return_items WHERE return_id IN :ids"),
                {"ids": tuple(owned.returns)},
            )
            session.execute(
                text("DELETE FROM returns WHERE id IN :ids"),
                {"ids": tuple(owned.returns)},
            )
        if owned.orders:
            session.execute(
                text("DELETE FROM order_items WHERE order_id IN :ids"),
                {"ids": tuple(owned.orders)},
            )
            session.execute(
                text("DELETE FROM orders WHERE id IN :ids"),
                {"ids": tuple(owned.orders)},
            )
        if owned.products:
            session.execute(
                text("DELETE FROM products WHERE id IN :ids"),
                {"ids": tuple(owned.products)},
            )
        if owned.users:
            session.execute(
                text("DELETE FROM points_transactions WHERE user_id IN :ids"),
                {"ids": tuple(owned.users)},
            )
            session.execute(
                text("DELETE FROM users WHERE id IN :ids"),
                {"ids": tuple(owned.users)},
            )

        window = {"first": SANDBOX_FIRST, "last": SANDBOX_LAST}
        for table in (
            "agg_payment_daily",
            "agg_geo_daily",
            "agg_promo_daily",
            "analytics_recompute_queue",
        ):
            session.execute(
                text(f"DELETE FROM {table} WHERE bucket_date BETWEEN :first AND :last"),
                window,
            )
        # Cohort rows are keyed by cohort_month, and their bucket_date is the
        # period being measured rather than the bucket the job was handed — so
        # they are removed by the key the job actually scopes itself with.
        session.execute(
            text(
                "DELETE FROM agg_customer_cohort_monthly "
                "WHERE cohort_month BETWEEN :first AND :last"
            ),
            {"first": "2014-01", "last": "2015-12"},
        )
        session.execute(
            text("DELETE FROM analytics_sync_runs WHERE worker_id = :worker"),
            {"worker": WORKER_ID},
        )
        session.commit()


def _assert_cohort_window_is_empty(db: Session) -> None:
    """Fail loudly if a foreign order sits inside the cohort recompute window.

    The only assertion in this file that cannot be scoped to a key this test
    invented: a cohort is keyed by an acquisition *month*, which is shared by
    construction, and ``cohort_size`` counts everyone whose first order landed in
    it. A stray 2015 order would not make these tests noisy — it would make them
    wrong.
    """
    tz = store_timezone(db)
    start, _ = day_bounds_utc(date(2014, 1, 1), tz)
    _, end = day_bounds_utc(date(2015, 12, 31), tz)
    foreign = db.execute(
        select(Order.id)
        .where(Order.created_at >= start, Order.created_at < end)
        .limit(5)
    ).scalars().all()
    assert not foreign, (
        f"orders {foreign} already sit inside the cohort recompute window "
        "2014-01..2015-12; cohort_size is a global count over acquisition month "
        "and these tests assert absolute figures"
    )


# ---------------------------------------------------------------------------
# Reading rollup rows back
# ---------------------------------------------------------------------------

#: Bookkeeping, not measurement. Two runs of the same bucket differ in these and
#: must be identical in everything else.
_NON_MEASURE_COLUMNS = frozenset({"id", "computed_at"})

#: Why every "the rerun deleted its rows" assertion below is a LOWER bound rather
#: than an equality against the bucket's total row count.
#:
#: Pattern B's DELETE is scoped to the whole bucket, not to the keys one test
#: owns, so a second copy of this suite running against the same throwaway MySQL
#: deletes this test's rows out from under its own count and the equality becomes
#: a coin toss. The lower bound still has all the teeth that matter: an upsert
#: implementation deletes NOTHING, so `rows_deleted >= 1` already fails it, and
#: the dedicated `*_removes_a_group_key_that_disappeared` tests prove the part an
#: upsert genuinely cannot express.
_DELETED_AT_LEAST = (
    "Pattern B deletes before it reinserts, so a rerun must delete at least the "
    "rows this test owns; an upsert deletes none of them and leaves a vacated "
    "key behind at its old numbers"
)


def _measures(row) -> dict:
    """Every measured column of a rollup row, keyed by column name."""
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name not in _NON_MEASURE_COLUMNS
    }


def _rows(db: Session, model, bucket: date, generation: int) -> list:
    db.expire_all()
    return db.execute(
        select(model).where(
            model.bucket_date == bucket, model.tz_generation == generation
        )
    ).scalars().all()


def _keyed(db: Session, model, bucket: date, generation: int, key) -> dict:
    """Rollup rows for one bucket, keyed by a callable over the row."""
    return {key(row): row for row in _rows(db, model, bucket, generation)}


def _cohort_grid(db: Session, generation: int) -> dict:
    """The whole cohort window, keyed by ``(cohort_month, period_index)``."""
    db.expire_all()
    rows = db.execute(
        select(AggCustomerCohortMonthly).where(
            AggCustomerCohortMonthly.tz_generation == generation,
            AggCustomerCohortMonthly.cohort_month >= "2014-01",
            AggCustomerCohortMonthly.cohort_month <= "2015-12",
        )
    ).scalars().all()
    return {(row.cohort_month, int(row.period_index)): row for row in rows}


def _runner(db: Session) -> AggregationRunner:
    return AggregationRunner(db, worker_id=WORKER_ID)


# ===========================================================================
# payment_daily
# ===========================================================================


def _payment_fixture(db: Session, owned: _Owned, day: date) -> tuple[str, str, str]:
    """One gateway with every outcome, plus a COD leg under both sentinels.

    The gateway and both payment-method strings are minted per test, so every
    key this fixture produces is unique to it and its figures are absolute even
    when the shared MySQL is not.
    """
    gateway = f"rzp-{_uid()}"[:40]
    method = f"prepaid-{_uid()}"[:32]
    cod_method = f"cod-{_uid()}"[:32]

    product = _create_product(db, owned, price="500.00")
    user = _create_user(db, owned)
    prepaid = _create_order(
        db,
        owned,
        user,
        [(product, 2)],
        created_at=_at(db, day, 9),
        payment_method=method,
        payment_instrument="upi",
    )
    cod = _create_order(
        db,
        owned,
        user,
        [(product, 1)],
        created_at=_at(db, day, 9, 30),
        payment_method=cod_method,
        cod_balance="500.00",
    )

    attempts = (
        # (status, amount, created hour, paid hour/minute)
        (PaymentTxnStatus.PAID, "1000.00", 10, (10, 5)),
        (PaymentTxnStatus.PAID, "500.00", 11, (11, 10)),
        (PaymentTxnStatus.FAILED, "700.00", 12, None),
        (PaymentTxnStatus.CANCELLED, "200.00", 13, None),
        (PaymentTxnStatus.REFUNDED, "300.00", 14, (14, 2)),
        # Paid with no paid_at: counted in `paid`, absent from `n_settle`.
        (PaymentTxnStatus.PAID, "400.00", 15, None),
    )
    for status, amount, hour, paid in attempts:
        _create_payment(
            db,
            owned,
            prepaid,
            created_at=_at(db, day, hour),
            gateway=gateway,
            method=method,
            status=status,
            amount=amount,
            paid_at=None if paid is None else _at(db, day, paid[0], paid[1]),
        )

    # A COD leg never touches a gateway: NULL gateway, NULL instrument, and both
    # must become the '-' sentinel or the UNIQUE key stops holding on MySQL.
    _create_payment(
        db,
        owned,
        cod,
        created_at=_at(db, day, 16),
        gateway=None,
        method=cod_method,
        status=PaymentTxnStatus.PAID,
        amount="500.00",
        paid_at=None,
    )

    for message, count in (("card declined", 2), ("insufficient funds", 1)):
        for index in range(count):
            _create_event(
                db,
                owned,
                prepaid,
                created_at=_at(db, day, 17, index),
                event_type=PaymentEventType.GATEWAY_ERROR,
                gateway_code=gateway,
                message=message,
            )
    # A reconciliation disagreement, not a failure: it must raise
    # `mismatch_events` and must NOT become the top failure reason.
    _create_event(
        db,
        owned,
        prepaid,
        created_at=_at(db, day, 18),
        event_type=PaymentEventType.AMOUNT_MISMATCH,
        gateway_code=gateway,
        message="gateway reported 100.00, expected 1000.00",
        payment_status="success",
    )
    db.commit()
    return gateway, method, cod_method


def test_payment_daily_is_idempotent_with_realistic_values() -> None:
    """Two runs leave identical counters, amounts and settlement sums."""
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        gateway, method, cod_method = _payment_fixture(db, owned, DAY_PAY)
        mine = {(gateway, method, "upi"), (DIMENSION_UNKNOWN, cod_method, DIMENSION_UNKNOWN)}

        def owned_rows() -> dict:
            return {
                key: _measures(row)
                for key, row in _keyed(
                    db,
                    AggPaymentDaily,
                    DAY_PAY,
                    generation,
                    lambda r: (r.gateway, r.payment_method, r.payment_instrument),
                ).items()
                if key in mine
            }

        runner = _runner(db)
        first = runner.run_bucket("payment_daily", DAY_PAY)
        before = owned_rows()

        second = runner.run_bucket("payment_daily", DAY_PAY)
        after = owned_rows()

        assert set(before) == mine, (
            f"both the named gateway and the COD sentinel key must have a row; got "
            f"{sorted(before)}"
        )
        assert second.rows_deleted >= len(mine), _DELETED_AT_LEAST
        assert before == after, (
            "re-running one bucket changed its values; the write is accumulating "
            f"rather than replacing. before={before} after={after}"
        )
        assert first.rows_written == second.rows_written >= 2

        row = before[(gateway, method, "upi")]
        assert row["attempts"] == 6, "attempts are payment attempts, not orders"
        assert row["paid"] == 3
        assert row["failed"] == 1
        assert row["cancelled"] == 1
        assert row["refunded"] == 1
        assert (
            row["paid"] + row["failed"] + row["cancelled"] + row["refunded"]
            <= row["attempts"]
        ), "the outcome counters are current statuses and are mutually exclusive"
        assert row["paid_amount"] == Decimal("1900.00")
        assert row["failed_amount"] == Decimal("700.00"), (
            "the size of the leak, not lost revenue"
        )

        assert row["n_settle"] == 2, (
            "one PAID attempt carries no paid_at and one settled attempt was later "
            "refunded; dividing the settle sum by `paid` instead would understate "
            "settlement time, which is the bug the sum+count pair prevents"
        )
        assert row["n_settle"] < row["paid"]
        assert row["sum_settle_seconds"] == 5 * 60 + 10 * 60

        assert row["mismatch_events"] == 1
        assert row["top_failure_reason"] == "card declined", (
            "the modal failure message, and only from failure events: the amount "
            "mismatch carries a message too and must not be reported as a reason"
        )

        sentinel = before[(DIMENSION_UNKNOWN, cod_method, DIMENSION_UNKNOWN)]
        assert sentinel["attempts"] == 1, (
            "a COD leg has no gateway and no instrument; both must become the '-' "
            "sentinel, because a NULL does not collide under a MySQL UNIQUE key "
            "and the next run would insert a second row instead of replacing it"
        )
        assert sentinel["paid"] == 1
        assert sentinel["n_settle"] == 0, "COD is collected without a paid_at"
        assert sentinel["top_failure_reason"] is None, (
            "NULL means no failure event carried a message — a different statement "
            "from 'nothing failed', which is why `failed` is stored next to it"
        )
    finally:
        _cleanup(owned)
        db.close()


def test_payment_daily_removes_a_group_key_that_disappeared() -> None:
    """A gateway whose only attempt is gone must lose its row, not keep it.

    This is the case an upsert cannot express. It writes nothing for the vacated
    key, so yesterday's row survives at yesterday's numbers in the table an
    operator reads to decide which gateway is failing.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        gateway = f"rzp-{_uid()}"[:40]
        method = f"prepaid-{_uid()}"[:32]

        product = _create_product(db, owned, price="300.00")
        user = _create_user(db, owned)
        order = _create_order(
            db,
            owned,
            user,
            [(product, 1)],
            created_at=_at(db, DAY_PAY_GONE, 9),
            payment_method=method,
            payment_instrument="card",
        )
        payment = _create_payment(
            db,
            owned,
            order,
            created_at=_at(db, DAY_PAY_GONE, 10),
            gateway=gateway,
            method=method,
            status=PaymentTxnStatus.PAID,
            amount="300.00",
            paid_at=_at(db, DAY_PAY_GONE, 10, 1),
        )
        db.commit()

        runner = _runner(db)
        runner.run_bucket("payment_daily", DAY_PAY_GONE)
        keys = set(
            _keyed(
                db,
                AggPaymentDaily,
                DAY_PAY_GONE,
                generation,
                lambda r: (r.gateway, r.payment_method, r.payment_instrument),
            )
        )
        assert (gateway, method, "card") in keys

        db.execute(
            text("DELETE FROM order_payments WHERE id = :id"), {"id": payment.id}
        )
        db.commit()

        result = runner.run_bucket("payment_daily", DAY_PAY_GONE)
        keys_after = set(
            _keyed(
                db,
                AggPaymentDaily,
                DAY_PAY_GONE,
                generation,
                lambda r: (r.gateway, r.payment_method, r.payment_instrument),
            )
        )
        assert (gateway, method, "card") not in keys_after, (
            "the gateway's only attempt is gone, so its row must GO; an upsert "
            "leaves it behind reporting a gateway that processed nothing"
        )
        assert result.rows_deleted >= 1
    finally:
        _cleanup(owned)
        db.close()


def test_payment_daily_bucketing_is_store_local() -> None:
    """23:00 IST and 00:30 IST are two reporting days and one UTC date."""
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        gateway = f"rzp-{_uid()}"[:40]
        method = f"prepaid-{_uid()}"[:32]

        product = _create_product(db, owned, price="250.00")
        user = _create_user(db, owned)
        order = _create_order(
            db,
            owned,
            user,
            [(product, 1)],
            created_at=_at(db, DAY_PAY_TZ_A, 8),
            payment_method=method,
            payment_instrument="upi",
        )
        late = _create_payment(
            db,
            owned,
            order,
            created_at=_at(db, DAY_PAY_TZ_A, 23),
            gateway=gateway,
            method=method,
            status=PaymentTxnStatus.PAID,
            amount="250.00",
            paid_at=_at(db, DAY_PAY_TZ_A, 23, 1),
        )
        early = _create_payment(
            db,
            owned,
            order,
            created_at=_at(db, DAY_PAY_TZ_B, 0, 30),
            gateway=gateway,
            method=method,
            status=PaymentTxnStatus.FAILED,
            amount="250.00",
        )
        db.commit()

        assert late.created_at.date() == early.created_at.date(), (
            "fixture: both attempts must share a UTC date for this test to mean "
            f"anything ({late.created_at} vs {early.created_at})"
        )

        runner = _runner(db)
        runner.run_bucket("payment_daily", DAY_PAY_TZ_A)
        runner.run_bucket("payment_daily", DAY_PAY_TZ_B)

        key = (gateway, method, "upi")
        day_a = _keyed(
            db,
            AggPaymentDaily,
            DAY_PAY_TZ_A,
            generation,
            lambda r: (r.gateway, r.payment_method, r.payment_instrument),
        )[key]
        day_b = _keyed(
            db,
            AggPaymentDaily,
            DAY_PAY_TZ_B,
            generation,
            lambda r: (r.gateway, r.payment_method, r.payment_instrument),
        )[key]

        assert (day_a.attempts, day_a.paid, day_a.failed) == (1, 1, 0), (
            "a UTC-day bucketer puts both attempts in the earlier bucket and "
            "reports 2 attempts here and none the next day"
        )
        assert (day_b.attempts, day_b.paid, day_b.failed) == (1, 0, 1)
    finally:
        _cleanup(owned)
        db.close()


# ===========================================================================
# shipment_geo_daily
# ===========================================================================


def _geo_fixture(db: Session, owned: _Owned, day: date) -> str:
    """One state with a good pincode, a COD order, an RTO and a bad pincode."""
    state = f"Testland-{_uid()}"[:64]
    product = _create_product(db, owned, price="500.00")
    user = _create_user(db, owned)

    delivered_order = _create_order(
        db,
        owned,
        user,
        [(product, 2)],
        created_at=_at(db, day, 10),
        pincode="560001",
        state=state,
    )
    _create_shipment(
        db,
        owned,
        delivered_order,
        created_at=_at(db, day, 12),
        status=ShipmentStatus.DELIVERED,
        delivered_at=_at(db, DAY_GEO_DELIVERED, 14),
    )

    cod_order = _create_order(
        db,
        owned,
        user,
        [(product, 1)],
        created_at=_at(db, day, 11),
        payment_method="cod",
        cod_balance="500.00",
        pincode="560001",
        state=state,
    )
    _create_shipment(
        db,
        owned,
        cod_order,
        created_at=_at(db, day, 13),
        status=ShipmentStatus.RTO_DELIVERED,
    )

    # Seven digits: unusable, and specifically NOT truncated to 560001.
    _create_order(
        db,
        owned,
        user,
        [(product, 1)],
        created_at=_at(db, day, 15),
        pincode="5600012",
        state=state,
    )
    db.commit()
    return state


def test_geo_daily_is_idempotent_with_realistic_values() -> None:
    """Two runs leave identical demand and fulfilment figures for a geography."""
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        state = _geo_fixture(db, owned, DAY_GEO)
        mine = {(state, "560001"), (state, DIMENSION_UNKNOWN)}

        def owned_rows() -> dict:
            return {
                key: _measures(row)
                for key, row in _keyed(
                    db, AggGeoDaily, DAY_GEO, generation, lambda r: (r.state, r.pincode)
                ).items()
                if key in mine
            }

        runner = _runner(db)
        first = runner.run_bucket("shipment_geo_daily", DAY_GEO)
        before = owned_rows()

        second = runner.run_bucket("shipment_geo_daily", DAY_GEO)
        after = owned_rows()

        assert set(before) == mine, (
            f"the pincode row and the '-' row must both exist; got {sorted(before)}"
        )
        assert second.rows_deleted >= len(mine), _DELETED_AT_LEAST
        assert before == after, (
            f"re-running the day changed its values. before={before} after={after}"
        )

        row = before[(state, "560001")]
        assert row["orders"] == 2
        assert row["net_revenue"] == Decimal("1500.00")
        assert row["units"] == 3
        assert row["cod_orders"] == 1
        assert row["prepaid_orders"] == 1
        assert row["cod_orders"] + row["prepaid_orders"] == row["orders"], (
            "the two must partition orders; a split_cod order has a prepaid leg "
            "AND a cash balance, so a payment_method label leaves it in neither"
        )
        assert row["delivered"] == 1
        assert row["rto"] == 1, (
            "an RTO_DELIVERED parcel was necessarily initiated; counting only the "
            "RTO_INITIATED status makes the RTO rate fall as returns complete"
        )
        assert row["n_delivery"] == 1
        # Ordered 10:00 IST on the 3rd, delivered 14:00 IST on the 6th.
        assert row["sum_delivery_seconds"] == 3 * 24 * 3600 + 4 * 3600

        unusable = before[(state, DIMENSION_UNKNOWN)]
        assert unusable["orders"] == 1
        assert unusable["net_revenue"] == Decimal("500.00")
        assert unusable["delivered"] == 0 and unusable["rto"] == 0
        assert any("geo_pincode_unusable" in w for w in first.warnings), (
            f"an unusable pincode must be reported; warnings were {first.warnings}"
        )
    finally:
        _cleanup(owned)
        db.close()


def test_geo_daily_normalises_pincodes_and_never_truncates() -> None:
    """Separators come out; anything not six digits becomes ``'-'``, whole.

    Truncating "5600012" to "560001" would invent an order from a real Bengaluru
    pincode that nobody placed — and it would render on the geo map exactly like
    a measurement, with nothing downstream able to tell the difference.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        state = f"Pinland-{_uid()}"[:64]
        product = _create_product(db, owned, price="100.00")
        user = _create_user(db, owned)

        # Two spellings of one real pincode...
        conforming = ["560 001", "560-001"]
        # ...and five values that are not a pincode at all.
        unusable = ["5600012", "56001", "056001", "BLR-560001", None]
        for index, raw in enumerate(conforming + unusable):
            _create_order(
                db,
                owned,
                user,
                [(product, 1)],
                created_at=_at(db, DAY_GEO_PIN, 9 + index),
                pincode=raw,
                state=state,
            )
        db.commit()

        _runner(db).run_bucket("shipment_geo_daily", DAY_GEO_PIN)
        rows = {
            key: row
            for key, row in _keyed(
                db, AggGeoDaily, DAY_GEO_PIN, generation, lambda r: (r.state, r.pincode)
            ).items()
            if key[0] == state
        }

        assert set(rows) == {(state, "560001"), (state, DIMENSION_UNKNOWN)}, (
            f"exactly two keys are expected; got {sorted(rows)}"
        )
        assert rows[(state, "560001")].orders == len(conforming), (
            "a pincode written with a space or a hyphen is the same pincode; if "
            "this reads 3 the seven-digit value was truncated into it"
        )
        assert rows[(state, DIMENSION_UNKNOWN)].orders == len(unusable), (
            f"every non-conforming value must land under '{DIMENSION_UNKNOWN}', "
            "which is a real value in this table (it is also what the 180-day "
            "pincode retention rollup writes), not a truncated near-miss"
        )

        # The normaliser itself, stated once so the rule is readable without
        # reconstructing it from row counts.
        assert _pincode("560 001") == "560001"
        assert _pincode("5600012") == DIMENSION_UNKNOWN
        assert _pincode("056001") == DIMENSION_UNKNOWN, (
            "India Post allocates no postal region 0; a leading zero is a "
            "data-entry artefact, not a pincode"
        )
        assert _pincode(None) == DIMENSION_UNKNOWN
        assert len(_pincode("560001")) == PINCODE_DIGITS
    finally:
        _cleanup(owned)
        db.close()


def test_geo_daily_removes_a_group_key_that_disappeared() -> None:
    """A pincode whose only order left the revenue statuses must lose its row."""
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        state = f"Goneland-{_uid()}"[:64]
        product = _create_product(db, owned, price="400.00")
        user = _create_user(db, owned)
        order = _create_order(
            db,
            owned,
            user,
            [(product, 1)],
            created_at=_at(db, DAY_GEO_GONE, 10),
            pincode="110001",
            state=state,
        )
        db.commit()

        runner = _runner(db)
        runner.run_bucket("shipment_geo_daily", DAY_GEO_GONE)
        assert (state, "110001") in _keyed(
            db, AggGeoDaily, DAY_GEO_GONE, generation, lambda r: (r.state, r.pincode)
        )

        # Cancelled, not deleted: the realistic late change, and the one an
        # upsert cannot see because the row simply stops being produced.
        order.status = OrderStatus.CANCELLED
        db.commit()

        result = runner.run_bucket("shipment_geo_daily", DAY_GEO_GONE)
        assert (state, "110001") not in _keyed(
            db, AggGeoDaily, DAY_GEO_GONE, generation, lambda r: (r.state, r.pincode)
        ), (
            "the pincode's only order is no longer a recognised sale, so its row "
            "must GO; an upsert leaves a problem pincode on the operational list "
            "with no order behind it"
        )
        assert result.rows_deleted >= 1
    finally:
        _cleanup(owned)
        db.close()


def test_geo_daily_bucketing_is_store_local() -> None:
    """An order at 23:00 IST and one at 00:30 IST are two reporting days."""
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        state = f"Tzland-{_uid()}"[:64]
        product = _create_product(db, owned, price="700.00")
        user = _create_user(db, owned)

        late = _create_order(
            db,
            owned,
            user,
            [(product, 2)],
            created_at=_at(db, DAY_GEO_TZ_A, 23),
            pincode="400001",
            state=state,
        )
        early = _create_order(
            db,
            owned,
            user,
            [(product, 1)],
            created_at=_at(db, DAY_GEO_TZ_B, 0, 30),
            pincode="400001",
            state=state,
        )
        db.commit()

        assert late.created_at.date() == early.created_at.date(), (
            "fixture: both orders must share a UTC date for this test to mean "
            f"anything ({late.created_at} vs {early.created_at})"
        )

        runner = _runner(db)
        runner.run_bucket("shipment_geo_daily", DAY_GEO_TZ_A)
        runner.run_bucket("shipment_geo_daily", DAY_GEO_TZ_B)

        key = (state, "400001")
        day_a = _keyed(
            db, AggGeoDaily, DAY_GEO_TZ_A, generation, lambda r: (r.state, r.pincode)
        )[key]
        day_b = _keyed(
            db, AggGeoDaily, DAY_GEO_TZ_B, generation, lambda r: (r.state, r.pincode)
        )[key]

        assert (day_a.orders, day_a.units) == (1, 2), (
            "a UTC-day bucketer puts both orders in the earlier bucket"
        )
        assert day_a.net_revenue == Decimal("1400.00")
        assert (day_b.orders, day_b.units) == (1, 1)
        assert day_b.net_revenue == Decimal("700.00")
    finally:
        _cleanup(owned)
        db.close()


# ===========================================================================
# promo_daily
# ===========================================================================


def _promo_fixture(db: Session, owned: _Owned, day: date) -> tuple[Coupon, Coupon]:
    """A marketing coupon whose redemptions and orders disagree, and a reward."""
    marketing = _create_coupon(db, owned, is_loyalty_reward=False)
    reward = _create_coupon(db, owned, is_loyalty_reward=True)

    product = _create_product(db, owned, price="500.00")
    buyer = _create_user(db, owned)
    quitter = _create_user(db, owned)

    paid_a = _create_order(
        db,
        owned,
        buyer,
        [(product, 2)],
        created_at=_at(db, day, 10),
        coupon_code=marketing.code,
        discount="100.00",
    )
    paid_b = _create_order(
        db,
        owned,
        buyer,
        [(product, 3)],
        created_at=_at(db, day, 11),
        coupon_code=marketing.code,
        discount="100.00",
    )
    # Redeemed, then the order never made it: the divergence the two counters
    # exist to keep visible instead of reconciling away.
    abandoned = _create_order(
        db,
        owned,
        quitter,
        [(product, 1)],
        created_at=_at(db, day, 12),
        status=OrderStatus.CANCELLED,
        coupon_code=marketing.code,
        discount="70.00",
    )
    _create_usage(
        db, owned, marketing, buyer, created_at=_at(db, day, 10), discount="100.00",
        order=paid_a,
    )
    _create_usage(
        db, owned, marketing, buyer, created_at=_at(db, day, 11), discount="50.00",
        order=paid_b,
    )
    _create_usage(
        db, owned, marketing, quitter, created_at=_at(db, day, 12), discount="70.00",
        order=abandoned,
    )

    reward_order = _create_order(
        db,
        owned,
        buyer,
        [(product, 1)],
        created_at=_at(db, day, 13),
        coupon_code=reward.code,
        discount="100.00",
    )
    _create_usage(
        db, owned, reward, buyer, created_at=_at(db, day, 13), discount="25.00",
        order=reward_order,
    )
    db.commit()
    return marketing, reward


def test_promo_daily_is_idempotent_with_realistic_values() -> None:
    """Two runs leave identical redemptions, discount and revenue per code."""
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        marketing, reward = _promo_fixture(db, owned, DAY_PROMO)
        mine = {marketing.code, reward.code}

        def owned_rows() -> dict:
            return {
                code: _measures(row)
                for code, row in _keyed(
                    db, AggPromoDaily, DAY_PROMO, generation, lambda r: r.coupon_code
                ).items()
                if code in mine
            }

        runner = _runner(db)
        runner.run_bucket("promo_daily", DAY_PROMO)
        before = owned_rows()

        second = runner.run_bucket("promo_daily", DAY_PROMO)
        after = owned_rows()

        assert set(before) == mine, (
            f"both codes must have a row; got {sorted(before)}"
        )
        assert second.rows_deleted >= len(mine), _DELETED_AT_LEAST
        assert before == after, (
            f"re-running the day changed its values. before={before} after={after}"
        )

        row = before[marketing.code]
        assert row["redemptions"] == 3
        assert row["discount_amount"] == Decimal("220.00")
        assert row["orders"] == 2, (
            "one redemption's order was cancelled, so it is not a recognised sale. "
            "redemptions and orders are stored separately precisely so this gap is "
            "visible rather than averaged away"
        )
        assert row["orders"] < row["redemptions"]
        assert row["order_revenue"] == Decimal("2300.00")
        assert row["is_loyalty_reward"] is False

        loyalty = before[reward.code]
        assert loyalty["redemptions"] == 1
        assert loyalty["discount_amount"] == Decimal("25.00")
        assert loyalty["orders"] == 1
        assert loyalty["order_revenue"] == Decimal("400.00")
        assert loyalty["is_loyalty_reward"] is True, (
            "an attribute of the code, read from the coupon row — not part of the "
            "grain, or one code could occupy two rows on one day"
        )
    finally:
        _cleanup(owned)
        db.close()


def test_promo_daily_removes_a_group_key_that_disappeared() -> None:
    """A code whose redemption is backed out must lose its row."""
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        coupon = _create_coupon(db, owned)
        product = _create_product(db, owned, price="600.00")
        user = _create_user(db, owned)
        order = _create_order(
            db,
            owned,
            user,
            [(product, 1)],
            created_at=_at(db, DAY_PROMO_GONE, 10),
            coupon_code=coupon.code,
            discount="60.00",
        )
        usage = _create_usage(
            db,
            owned,
            coupon,
            user,
            created_at=_at(db, DAY_PROMO_GONE, 10),
            discount="60.00",
            order=order,
        )
        db.commit()

        runner = _runner(db)
        runner.run_bucket("promo_daily", DAY_PROMO_GONE)
        assert coupon.code in _keyed(
            db, AggPromoDaily, DAY_PROMO_GONE, generation, lambda r: r.coupon_code
        )

        db.execute(text("DELETE FROM coupon_usages WHERE id = :id"), {"id": usage.id})
        order.status = OrderStatus.CANCELLED
        db.commit()

        result = runner.run_bucket("promo_daily", DAY_PROMO_GONE)
        assert coupon.code not in _keyed(
            db, AggPromoDaily, DAY_PROMO_GONE, generation, lambda r: r.coupon_code
        ), (
            "the redemption is backed out and the order is cancelled, so the code "
            "is on neither side of this table any more; its row must GO rather "
            "than keep reporting a discount nobody took"
        )
        assert result.rows_deleted >= 1
    finally:
        _cleanup(owned)
        db.close()


def test_promo_daily_bucketing_is_store_local() -> None:
    """A redemption at 23:00 IST and one at 00:30 IST are two reporting days."""
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        coupon = _create_coupon(db, owned)
        user = _create_user(db, owned)

        late = _create_usage(
            db,
            owned,
            coupon,
            user,
            created_at=_at(db, DAY_PROMO_TZ_A, 23),
            discount="40.00",
        )
        early = _create_usage(
            db,
            owned,
            coupon,
            user,
            created_at=_at(db, DAY_PROMO_TZ_B, 0, 30),
            discount="90.00",
        )
        db.commit()

        assert late.created_at.date() == early.created_at.date(), (
            "fixture: both redemptions must share a UTC date for this test to mean "
            f"anything ({late.created_at} vs {early.created_at})"
        )

        runner = _runner(db)
        runner.run_bucket("promo_daily", DAY_PROMO_TZ_A)
        runner.run_bucket("promo_daily", DAY_PROMO_TZ_B)

        day_a = _keyed(
            db, AggPromoDaily, DAY_PROMO_TZ_A, generation, lambda r: r.coupon_code
        )[coupon.code]
        day_b = _keyed(
            db, AggPromoDaily, DAY_PROMO_TZ_B, generation, lambda r: r.coupon_code
        )[coupon.code]

        assert day_a.redemptions == 1, (
            "a UTC-day bucketer puts both redemptions in the earlier bucket"
        )
        assert day_a.discount_amount == Decimal("40.00")
        assert day_b.redemptions == 1
        assert day_b.discount_amount == Decimal("90.00")
    finally:
        _cleanup(owned)
        db.close()


# ===========================================================================
# cohort_monthly
# ===========================================================================


def _cohort_fixture(db: Session, owned: _Owned) -> tuple[User, User]:
    """A June 2015 cohort of two: one who came back in August, one who did not."""
    product = _create_product(db, owned, price="500.00")
    founder = _create_user(db, owned)
    tourist = _create_user(db, owned)

    acquisition = _create_order(
        db, owned, founder, [(product, 2)], created_at=_at(db, COHORT_ACQUIRED, 11)
    )
    _create_order(
        db, owned, founder, [(product, 1)], created_at=_at(db, COHORT_SECOND_ORDER, 15)
    )
    _create_order(
        db, owned, tourist, [(product, 3)], created_at=_at(db, COHORT_TOURIST, 9)
    )
    _create_order(
        db, owned, founder, [(product, 4)], created_at=_at(db, COHORT_RETURNED, 14)
    )
    # A reversal of the JUNE order, issued in AUGUST: the sale stays in its own
    # period and the refund lands in the period it was issued.
    _create_refunded_return(
        db,
        owned,
        acquisition,
        refunded_at=_at(db, COHORT_REFUND_ISSUED, 12),
        refund_amount="300.00",
    )
    db.commit()
    return founder, tourist


def test_cohort_monthly_is_idempotent_with_realistic_values() -> None:
    """Two full recomputes leave an identical grid, and the grid is real.

    ``cohort_monthly`` is the one job here whose assertions cannot be scoped to a
    key it invented — a cohort is an acquisition month and ``cohort_size`` counts
    everyone in it — so the window is asserted empty first.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_cohort_window_is_empty(db)
        generation = active_generation(db).generation
        _cohort_fixture(db, owned)

        runner = _runner(db)
        first = runner.run_bucket("cohort_monthly", COHORT_RUN)
        before = {key: _measures(row) for key, row in _cohort_grid(db, generation).items()}

        second = runner.run_bucket("cohort_monthly", COHORT_RUN)
        after = {key: _measures(row) for key, row in _cohort_grid(db, generation).items()}

        assert second.rows_deleted >= len(before), _DELETED_AT_LEAST
        assert before == after, (
            f"re-running the cohort grid changed its values. before={before} "
            f"after={after}"
        )
        assert first.rows_written == second.rows_written

        # June 2015 is 6 months before the run month, so the cohort has periods
        # 0..6 and nothing beyond: a period that has not happened yet is not a
        # zero, it is absent.
        periods = sorted(
            index for month, index in before if month == COHORT_MONTH_JUNE
        )
        assert periods == list(range(0, 7)), (
            f"the June cohort must have exactly periods 0..6; got {periods}"
        )
        assert set(month for month, _ in before) == {COHORT_MONTH_JUNE}, (
            "no other month acquired a customer, and a cohort with no acquisitions "
            "gets no rows at all rather than a row family of zeros"
        )

        acquisition = before[(COHORT_MONTH_JUNE, 0)]
        assert acquisition["cohort_size"] == 2
        assert acquisition["active_customers"] == 2, (
            "every member's first order is by definition in the acquisition "
            "month, so period 0 activity always equals the cohort size"
        )
        assert acquisition["orders"] == 3
        assert acquisition["revenue"] == Decimal("3000.00"), (
            "the August refund reverses a June sale but is recognised in AUGUST; "
            "June keeps its sale"
        )
        assert acquisition["bucket_date"] == date(2015, 6, 1), (
            "bucket_date is the first day of the period being measured, not the "
            "bucket the job was handed"
        )

        quiet = before[(COHORT_MONTH_JUNE, 1)]
        assert quiet["active_customers"] == 0 and quiet["orders"] == 0
        assert quiet["revenue"] == Decimal("0.00")
        assert quiet["cohort_size"] == 2, "the denominator is on every row"
        assert quiet["bucket_date"] == date(2015, 7, 1)

        returned = before[(COHORT_MONTH_JUNE, 2)]
        assert returned["active_customers"] == 1, "only one of the two came back"
        assert returned["orders"] == 1
        assert returned["revenue"] == Decimal("1700.00"), (
            "2000.00 of August sales less the 300.00 refund ISSUED in August — the "
            "sale in its period, the reversal in its own"
        )
        assert returned["bucket_date"] == date(2015, 8, 1)
    finally:
        _cleanup(owned)
        db.close()


def test_cohort_monthly_writes_cohort_size_once_per_cohort() -> None:
    """``cohort_size`` is one measurement repeated, and must never be summed.

    It is written per cohort and copied onto every period row so a single row is
    self-sufficient as a retention denominator. Summing it over the cohort's rows
    multiplies the cohort by the number of periods in range, which is a plausible
    number that is wrong by whatever the window happens to be.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_cohort_window_is_empty(db)
        generation = active_generation(db).generation
        _cohort_fixture(db, owned)

        _runner(db).run_bucket("cohort_monthly", COHORT_RUN)
        grid = _cohort_grid(db, generation)
        june = {
            index: row for (month, index), row in grid.items()
            if month == COHORT_MONTH_JUNE
        }

        assert june, "the June cohort must exist"
        sizes = {int(row.cohort_size) for row in june.values()}
        assert sizes == {2}, (
            f"cohort_size must be the same measurement on every period row; got "
            f"{sorted(sizes)}"
        )

        summed = sum(int(row.cohort_size) for row in june.values())
        assert summed == 2 * len(june) and summed != 2, (
            f"summing cohort_size over {len(june)} period rows reports {summed} "
            "customers acquired in a month that acquired 2 — this is the failure "
            "the DISTINCT metric kind exists to refuse"
        )

        # And the taxonomy already refuses it, so a resolver cannot do by accident
        # what this test does on purpose.
        assert classify("cohort_size") is MetricKind.DISTINCT

        # Retention is the ratio of two stored columns, divided at read time.
        assert int(june[0].active_customers) == 2
        assert int(june[2].active_customers) == 1
    finally:
        _cleanup(owned)
        db.close()


def test_cohort_monthly_bucketing_is_store_local() -> None:
    """23:00 IST on 30 June and 00:30 IST on 1 July are two cohorts, one UTC date.

    The cohort key is a store-local *month*, so a UTC bucketer does not merely
    move a customer between days — it acquires them into the wrong cohort and the
    whole retention row family moves with them.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_cohort_window_is_empty(db)
        generation = active_generation(db).generation

        product = _create_product(db, owned, price="800.00")
        june_customer = _create_user(db, owned)
        july_customer = _create_user(db, owned)

        late = _create_order(
            db,
            owned,
            june_customer,
            [(product, 1)],
            created_at=_at(db, COHORT_TZ_LATE, 23),
        )
        early = _create_order(
            db,
            owned,
            july_customer,
            [(product, 2)],
            created_at=_at(db, COHORT_TZ_EARLY, 0, 30),
        )
        db.commit()

        assert late.created_at.date() == early.created_at.date(), (
            "fixture: both orders must share a UTC date for this test to mean "
            f"anything ({late.created_at} vs {early.created_at})"
        )

        _runner(db).run_bucket("cohort_monthly", COHORT_RUN)
        grid = _cohort_grid(db, generation)

        assert (COHORT_MONTH_JUNE, 0) in grid and (COHORT_MONTH_JULY, 0) in grid, (
            "a UTC bucketer puts both first orders in June and leaves July with "
            f"no cohort at all; got {sorted(set(m for m, _ in grid))}"
        )
        assert int(grid[(COHORT_MONTH_JUNE, 0)].cohort_size) == 1
        assert grid[(COHORT_MONTH_JUNE, 0)].revenue == Decimal("800.00")
        assert int(grid[(COHORT_MONTH_JULY, 0)].cohort_size) == 1
        assert grid[(COHORT_MONTH_JULY, 0)].revenue == Decimal("1600.00")
    finally:
        _cleanup(owned)
        db.close()


def test_cohort_monthly_removes_a_cohort_that_disappeared() -> None:
    """A cohort whose only customer's order was cancelled must lose its rows.

    This is the case the full recompute exists for: cohort retention is
    exquisitely sensitive to late status changes, and an incrementally updated
    grid would keep serving a cohort whose acquisition never happened.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_cohort_window_is_empty(db)
        generation = active_generation(db).generation

        product = _create_product(db, owned, price="450.00")
        user = _create_user(db, owned)
        order = _create_order(
            db, owned, user, [(product, 1)], created_at=_at(db, COHORT_GONE, 10)
        )
        db.commit()

        runner = _runner(db)
        runner.run_bucket("cohort_monthly", COHORT_RUN)
        grid = _cohort_grid(db, generation)
        february = [
            row for (month, _index), row in grid.items()
            if month == COHORT_MONTH_FEBRUARY
        ]

        assert len(february) == 11, (
            "February 2015 is 10 months before the run month, so the cohort has "
            f"periods 0..10; got {len(february)}"
        )
        assert all(row.bucket_date != COHORT_RUN for row in february), (
            "fixture: no row of this cohort carries the bucket_date the job was "
            "handed, which is what makes the next assertion able to tell a "
            "cohort_month-scoped delete from a bucket_date-scoped one"
        )

        order.status = OrderStatus.CANCELLED
        db.commit()

        result = runner.run_bucket("cohort_monthly", COHORT_RUN)
        after = _cohort_grid(db, generation)

        assert not any(month == COHORT_MONTH_FEBRUARY for month, _ in after), (
            "the cohort's only acquisition is no longer a recognised sale, so its "
            "whole row family must GO; an upsert keyed on (cohort_month, "
            "period_index) writes nothing for it and the retention heatmap keeps "
            "a cohort that does not exist"
        )
        assert result.rows_deleted >= len(february), (
            "the delete must be scoped by cohort_month, not by bucket_date: "
            "bucket_date is not in the UNIQUE key and not one of these "
            f"{len(february)} rows carries the bucket the job was handed, so a "
            "bucket_date-scoped delete would have removed none of them and left "
            "the whole grid behind"
        )
    finally:
        _cleanup(owned)
        db.close()


def test_cohort_monthly_recompute_window_is_the_documented_width() -> None:
    """The trailing window is 24 cohorts, counted back from the bucket's month.

    Cohorts older than that are frozen. The constant is asserted rather than the
    number spelled twice, because the model docstring is the authority and the
    constant exists so the number appears exactly once in code.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _assert_cohort_window_is_empty(db)
        generation = active_generation(db).generation

        product = _create_product(db, owned, price="200.00")
        inside = _create_user(db, owned)
        outside = _create_user(db, owned)

        # The oldest cohort the run still rebuilds, and the newest it does not.
        first_kept = date(2014, 1, 20)
        last_frozen = date(2013, 12, 20)
        assert (
            COHORT_RUN.year * 12
            + COHORT_RUN.month
            - (first_kept.year * 12 + first_kept.month)
            == COHORT_MONTHS_RECOMPUTED - 1
        ), "fixture: first_kept must be the oldest month in the trailing window"

        _create_order(db, owned, inside, [(product, 1)], created_at=_at(db, first_kept, 10))
        _create_order(
            db, owned, outside, [(product, 1)], created_at=_at(db, last_frozen, 10)
        )
        db.commit()

        _runner(db).run_bucket("cohort_monthly", COHORT_RUN)
        months = {month for month, _ in _cohort_grid(db, generation)}

        assert "2014-01" in months, (
            f"the trailing {COHORT_MONTHS_RECOMPUTED} cohorts include the run "
            "month, so the oldest rebuilt cohort is 23 months before it"
        )
        assert "2013-12" not in months, (
            "a cohort older than the window is frozen, not rebuilt; widening the "
            "window is the supported fix, not patching rows"
        )
    finally:
        # `last_frozen` is outside SANDBOX_FIRST, so its rows (if a future change
        # ever writes them) are removed by the same cohort_month sweep below.
        _cleanup(owned)
        db.close()
