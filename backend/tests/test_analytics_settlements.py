"""Settlement ingest, matching and reconciliation.

What these tests are actually pinning down
==========================================
The arithmetic is the easy half. What carries the weight here is a set of
behaviours that a settlement importer almost always gets subtly wrong, each of
which is silent in production and each of which has its own test:

* ``test_reupload_of_the_identical_file_changes_nothing`` — finance WILL upload
  the same report twice. The unique key on the gateway's own transaction id has
  to make that a no-op. A doubling here doubles the fee and nothing complains.
* ``test_unmatched_line_and_unsettled_capture_are_both_recorded`` — the two
  gaps are the product, not an error. A line the gateway paid us for that we
  cannot explain, and a payment we captured that the gateway never settled, must
  both survive ingest with their money intact and be retrievable afterwards.
* ``test_refund_line_reduces_the_payout_and_is_not_a_fee`` — refunds arrive as
  negative lines. The classic failure is ``abs()`` somewhere in the parser, which
  turns a refund into revenue and its zero fee into a positive cost.
* ``test_reconciliation_is_exact_to_the_paisa`` — ``gross - fee - tax = net`` as
  integers with no tolerance, asserted against the DATABASE constraint as well as
  the service, because a settlement that reconciles to the rupee has not
  reconciled.
* ``test_fee_buckets_on_the_payment_day_and_cash_on_the_settlement_day`` — a
  payment captured on the 30th settles on the 2nd. Putting the fee on the 2nd or
  the cash on the 30th misstates both figures at once and looks entirely
  plausible.
* ``test_a_malformed_file_ingests_nothing_at_all`` — a partial ingest is worse
  than a rejected one, so the table is asserted byte-for-byte unchanged after
  three different kinds of bad file.

Isolation strategy
------------------
The whole fixture lives in **2018**, a year nothing else in this repo touches
(2001-2015, 2024 and 2026 are all taken by other suites). Every assertion is on
an absolute figure rather than a delta from a baseline.

Each test owns a different **month** of 2018 so a leaked row from one can never
be read by another, and ``_sweep()`` runs both before and after every test
against the whole year. Sweeping by date range rather than by an id list is
deliberate: a run that dies mid-test leaves rows behind and its id list died with
it. The suite has to survive being run three times in a row.

House style, per this repo: no conftest DB fixture, each test owns its
``SessionLocal()``, teardown in ``finally`` through a *fresh* session so a
half-rolled-back transaction cannot skip it.
"""
from __future__ import annotations

import csv
import io
import re
import subprocess
import sys
import time
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.analytics_settlement import (
    AggSettlementDaily,
    PaymentSettlement,
    SettlementMatchKey,
    SettlementMatchStatus,
    SettlementSource,
    SettlementTxnType,
)
from app.models.order import Order, OrderItem, OrderStatus
from app.models.order_payment import OrderPayment, PaymentTxnStatus
from app.models.product import Product
from app.models.user import User
from app.services.analytics.aggregation import AggregationRunner
from app.services.analytics.contracts import from_minor
from app.services.analytics.reconciliation import CheckStatus
from app.services.analytics.settlements import (
    AmountUnit,
    RazorpaySettlementApiClient,
    SettlementApiNotImplemented,
    SettlementCheckKey,
    SettlementIngestError,
    api_availability,
    ingest_settlement_csv,
    observed_gateway_fee,
    parse_settlement_csv,
    settlement_reconciliation,
    unmatched_settlements,
    unsettled_captured_payments,
)
from app.services.analytics.timebox import active_generation
from app.services.analytics.types import MetricQuality

# ---------------------------------------------------------------------------
# The 2018 sandbox
# ---------------------------------------------------------------------------

SANDBOX_FROM = date(2018, 1, 1)
SANDBOX_TO = date(2019, 1, 1)

USER_EMAIL_PATTERN = "setltest-%@example.com"
SKU_PATTERN = "SKU-SETL-%"
GATEWAY = "razorpay"
WORKER_ID = "settlement-tests"

JOB = "settlement_daily"

BACKEND_DIR = Path(__file__).resolve().parents[1]
MIGRATION = (
    BACKEND_DIR / "alembic" / "versions" / "e8b207fd93c1_add_payment_settlements.py"
)
TWIN_SQL = BACKEND_DIR / "scripts" / "sql" / "2026-07-29_payment_settlements.sql"


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _mtid() -> str:
    """A merchant transaction id in the exact shape ``payment_service`` mints.

    ``"ORD"`` plus 24 uppercase hex characters. The shape matters: it is what the
    ingest's extractor scans free-text settlement columns for, so a fixture using
    a convenient short id would test a different code path from production.
    """
    return f"ORD{uuid.uuid4().hex[:24].upper()}"


def _d(value: str) -> Decimal:
    return Decimal(value)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


class _Owned:
    def __init__(self) -> None:
        self.orders: list[int] = []
        self.products: list[int] = []
        self.users: list[int] = []


def _create_user(db: Session, owned: _Owned) -> User:
    user = User(
        email=f"setltest-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.flush()
    owned.users.append(user.id)
    return user


def _create_product(db: Session, owned: _Owned, *, price: str = "1000.00") -> Product:
    product = Product(
        sku=f"SKU-SETL-{_uid()}",
        name=f"SettlementTestProduct {_uid()}",
        price=_d(price),
        cost=_d("400.00"),
        stock=1000,
    )
    db.add(product)
    db.flush()
    owned.products.append(product.id)
    return product


def _create_paid_order(
    db: Session,
    owned: _Owned,
    user: User,
    product: Product,
    *,
    at: datetime,
    total: str,
    mtid: str | None = None,
    provider_ref: str | None = None,
    with_leg: bool = True,
) -> tuple[Order, str]:
    """A prepaid order that reached PAID, plus its gateway payment leg.

    ``mtid`` lands on ``orders.payment_intent_id`` — UNIQUE, and the primary join
    key this module establishes. ``provider_ref`` is the Payment Links id that
    this deployment actually stores in all three "provider ref" columns; the
    fixture writes it to both leg columns exactly as ``order_sync`` does, so the
    matcher is exercised against the real shape rather than a tidied one.
    """
    transaction_id = mtid or _mtid()
    order = Order(
        order_number=f"WV-SETL-{_uid()}",
        user_id=user.id,
        status=OrderStatus.PAID,
        subtotal=_d(total),
        tax_amount=_d("0"),
        discount_amount=_d("0"),
        payment_discount_amount=_d("0"),
        shipping_amount=_d("0"),
        cod_surcharge_amount=_d("0"),
        cod_balance=_d("0.00"),
        total_amount=_d(total),
        currency="INR",
        payment_method="prepaid",
        payment_intent_id=transaction_id,
        gateway_code=GATEWAY,
        payment_provider_ref=provider_ref,
        created_at=at,
        paid_at=at,
    )
    order.items = [
        OrderItem(
            product_id=product.id,
            quantity=1,
            unit_price=_d(total),
            unit_cost=product.cost,
        )
    ]
    db.add(order)
    db.flush()
    owned.orders.append(order.id)

    if with_leg:
        db.add(
            OrderPayment(
                order_id=order.id,
                gateway=GATEWAY,
                gateway_order_id=provider_ref,
                gateway_payment_id=provider_ref,
                payment_method="prepaid",
                payment_status=PaymentTxnStatus.PAID,
                amount=_d(total),
                currency="INR",
                transaction_reference=transaction_id,
                created_at=at,
                paid_at=at,
            )
        )
        db.flush()
    return order, transaction_id


# ---------------------------------------------------------------------------
# CSV builders
# ---------------------------------------------------------------------------

#: The Razorpay combined settlement-report header, in the order the dashboard
#: exports it. Written out in full rather than trimmed to the columns under test
#: so the parser is exercised against a realistic file, including the columns it
#: is supposed to ignore.
_HEADER = (
    "entity_id,type,debit,credit,amount,currency,fee,tax,on_hold,settled,"
    "created_at,settled_at,settlement_id,settlement_utr,payment_id,order_id,"
    "order_receipt,method,card_network"
)


def _line(
    entity_id: str,
    *,
    kind: str = "payment",
    credit: str = "0",
    debit: str = "0",
    fee: str = "0",
    tax: str = "0",
    created_at: str,
    settled_at: str = "",
    settlement_id: str = "",
    utr: str = "",
    receipt: str = "",
    method: str = "upi",
) -> str:
    amount = credit if credit not in ("0", "") else debit
    settled = "Yes" if settlement_id else "No"
    on_hold = "No" if settlement_id else "Yes"
    # Written through `csv.writer` rather than "," .join: a real export quotes
    # any cell containing a comma (a thousands separator, a free-text note), and
    # a fixture that hand-joins would silently produce a file with the wrong
    # number of columns and test the parser against something no gateway sends.
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="").writerow(
        [
            entity_id,
            kind,
            debit,
            credit,
            amount,
            "INR",
            fee,
            tax,
            on_hold,
            settled,
            created_at,
            settled_at,
            settlement_id,
            utr,
            entity_id if kind == "payment" else "",
            "",
            receipt,
            method,
            "",
        ]
    )
    return buffer.getvalue()


def _csv(*lines: str) -> str:
    return "\n".join((_HEADER, *lines)) + "\n"


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


def _ids(db: Session, sql: str, params: dict[str, Any]) -> list[int]:
    return [int(r[0]) for r in db.execute(text(sql), params).all()]


def _sweep_once() -> None:
    """Remove every row this module could have written, anywhere in 2018.

    By date range and by name pattern, not by an id list held in memory: the
    point is to survive a previous run that died before its teardown. Children go
    before parents so this works whether or not the FKs cascade.

    ``payment_settlements`` is swept on BOTH date columns. A row transacted in
    2018 and settled in 2019 would escape a payment-date-only sweep and then be
    read by the next run of the settlement-date bucketing test, which is exactly
    the cross-year case that test exists to create.
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
                "DELETE FROM payment_settlements "
                "WHERE (payment_date >= :a AND payment_date < :b) "
                "   OR (settlement_date >= :a AND settlement_date < :b)"
            ),
            bounds,
        )
        s.execute(
            text(
                "DELETE FROM agg_settlement_daily "
                "WHERE bucket_date >= :a AND bucket_date < :b"
            ),
            bounds,
        )
        s.execute(
            text("DELETE FROM analytics_sync_runs WHERE worker_id = :w"),
            {"w": WORKER_ID},
        )
        if order_ids:
            ids = {"ids": tuple(order_ids)}
            for child in ("order_items", "order_payments", "shipments"):
                s.execute(text(f"DELETE FROM {child} WHERE order_id IN :ids"), ids)
            s.execute(text("DELETE FROM payment_events WHERE order_id IN :ids"), ids)
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
    """``_sweep_once``, retried on a deadlock.

    A teardown that loses a lock race must not fail the suite; the assertion has
    already passed or failed on its own merits. The retry is bounded and the last
    attempt is allowed to raise, because a sandbox that genuinely cannot be
    cleaned has to be loud — every later run reads its leftovers.
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


def _settlement_rows(db: Session, *, source_file: str) -> list[PaymentSettlement]:
    return list(
        db.execute(
            select(PaymentSettlement)
            .where(PaymentSettlement.source_file == source_file)
            .order_by(PaymentSettlement.transaction_id)
        )
        .scalars()
        .all()
    )


def _snapshot(db: Session) -> list[tuple]:
    """Every field of every sandbox settlement row, for byte-level comparison.

    A row COUNT is not enough for either of the things this is used for.
    Idempotency can fail by accumulating a fee in place, which a count misses
    entirely; and "a rejected file wrote nothing" has to mean nothing, including
    a partially-updated existing row. ``id`` and ``created_at`` are included so a
    delete-and-reinsert cannot pass as a no-op.
    """
    rows = db.execute(
        select(PaymentSettlement)
        .where(
            PaymentSettlement.payment_date >= SANDBOX_FROM,
            PaymentSettlement.payment_date < SANDBOX_TO,
        )
        .order_by(PaymentSettlement.id)
    ).scalars().all()
    return [
        (
            r.id,
            r.gateway,
            r.transaction_id,
            r.transaction_type,
            r.settlement_id,
            r.settlement_utr,
            r.gross_minor,
            r.fee_minor,
            r.tax_minor,
            r.net_minor,
            r.transacted_at,
            r.settled_at,
            r.payment_date,
            r.settlement_date,
            r.match_status,
            r.match_key,
            r.order_id,
            r.order_payment_id,
            r.source_row,
            r.created_at,
        )
        for r in rows
    ]


def _rollup(db: Session, day: date, generation: int) -> AggSettlementDaily | None:
    return db.execute(
        select(AggSettlementDaily).where(
            AggSettlementDaily.bucket_date == day,
            AggSettlementDaily.gateway == GATEWAY,
            AggSettlementDaily.tz_generation == generation,
        )
    ).scalars().first()


def _run_job(db: Session, day: date) -> None:
    AggregationRunner(db, worker_id=WORKER_ID).run_bucket(JOB, day)


# ===========================================================================
# 1. A clean file ingests, matches, and produces ACTUAL fees
# ===========================================================================


class TestCleanIngest:
    """January 2018."""

    def test_clean_csv_matches_and_the_fee_becomes_actual(self) -> None:
        """Two captures, matched by the two keys that actually work here.

        Row one carries the merchant transaction id in ``order_receipt`` — the
        primary join key in this deployment, because the ``ORD...`` string is
        what we hand Razorpay as the payment link's ``reference_id`` and it is
        UNIQUE on ``orders.payment_intent_id``.

        Row two carries only a ``plink_...`` id, the Payment Links reference this
        codebase actually stores in ``order_payments``. Both are asserted
        separately: a matcher that only handled one of them would still pass a
        single-key test and would silently stop matching half the file.

        The fee is then asserted to be ``ACTUAL`` — the whole point of the
        feature. It is only allowed to be ACTUAL because every line matched AND
        nothing captured that day is outstanding; the coverage property is
        asserted alongside so a future change that loosens the quality rule fails
        here rather than upgrading an estimate by accident.
        """
        _sweep()
        owned = _Owned()
        db = SessionLocal()
        source = f"clean-{_uid()}.csv"
        try:
            generation = _generation(db)
            user = _create_user(db, owned)
            product = _create_product(db, owned)
            when = datetime(2018, 1, 10, 8, 0, 0)

            _, mtid = _create_paid_order(
                db, owned, user, product, at=when, total="1180.00"
            )
            plink = f"plink_{_uid()}"
            _create_paid_order(
                db,
                owned,
                user,
                product,
                at=when,
                total="590.00",
                provider_ref=plink,
            )
            db.commit()

            result = ingest_settlement_csv(
                db,
                _csv(
                    _line(
                        f"pay_{_uid()}",
                        credit="1180.00",
                        fee="23.60",
                        tax="4.25",
                        created_at="2018-01-10 13:30:00",
                        settled_at="2018-01-12 11:00:00",
                        settlement_id="setl_jan01",
                        utr="UTRJAN01",
                        receipt=mtid,
                    ),
                    _line(
                        f"pay_{_uid()}",
                        credit="590.00",
                        fee="11.80",
                        tax="2.12",
                        created_at="2018-01-10 14:00:00",
                        settled_at="2018-01-12 11:00:00",
                        settlement_id="setl_jan01",
                        utr="UTRJAN01",
                        receipt=f"link {plink} captured",
                    ),
                ),
                amount_unit=AmountUnit.MAJOR,
                source_file=source,
            )

            assert result.rows_parsed == 2
            assert result.rows_new == 2
            assert result.matched == 2, (
                f"both lines must match; unmatched={result.unmatched} "
                f"ambiguous={result.ambiguous}"
            )
            assert result.unmatched == 0 and result.ambiguous == 0
            assert result.payment_dates == (date(2018, 1, 10),)
            assert result.settlement_dates == (date(2018, 1, 12),)

            rows = _settlement_rows(db, source_file=source)
            assert len(rows) == 2
            keys = {r.match_key for r in rows}
            assert keys == {
                SettlementMatchKey.MERCHANT_TXN_ID,
                SettlementMatchKey.PAYMENT_LINK_ID,
            }, (
                "one line must match on the merchant transaction id and the other "
                f"on the payment-link id; got {keys}"
            )
            assert all(r.order_id is not None for r in rows)
            assert all(r.source == SettlementSource.CSV_UPLOAD for r in rows)
            assert all(
                r.transaction_type == SettlementTxnType.PAYMENT for r in rows
            )

            # Money, to the paisa, on the way in.
            assert result.gross_minor == 177000
            assert result.fee_minor == 3540
            assert result.tax_minor == 637
            assert result.net_minor == 177000 - 3540 - 637

            observed = observed_gateway_fee(
                db, date(2018, 1, 10), gateway=GATEWAY, tz_generation=generation
            )
            assert observed is not None
            assert observed.fee_minor == 3540
            assert observed.tax_minor == 637
            assert observed.total_minor == 4177
            assert observed.matched_txns == 2
            assert observed.unmatched_txns == 0
            assert observed.unsettled_payments == 0
            assert observed.coverage_pct == Decimal("100.00")
            assert observed.quality is MetricQuality.ACTUAL, (
                "a fully matched, fully settled day is the ONE case where the "
                "gateway fee stops being an estimate and becomes an observation"
            )
            assert observed.source == "razorpay_settlement"
        finally:
            db.close()
            _sweep()

    def test_a_day_with_no_settlement_report_has_no_observed_fee(self) -> None:
        """``None``, not a zero-fee observation.

        A day nobody has uploaded a report for has an UNKNOWN gateway fee and the
        cost rule remains the only answer. Returning a zero-valued observation
        would let a caller book an ACTUAL zero against a day whose report simply
        has not arrived, which is the exact substitution this whole feature
        exists to stop.
        """
        _sweep()
        db = SessionLocal()
        try:
            assert (
                observed_gateway_fee(db, date(2018, 1, 20), gateway=GATEWAY) is None
            )
        finally:
            db.close()
            _sweep()


# ===========================================================================
# 2. Idempotent re-upload
# ===========================================================================


class TestIdempotentReupload:
    """February 2018."""

    def test_reupload_of_the_identical_file_changes_nothing(self) -> None:
        """Finance uploads the same report twice. Nothing may move.

        Asserted on a full field-by-field snapshot rather than on a row count,
        because the failure this guards against has two shapes: a second row per
        transaction (which a count catches) and an accumulating UPDATE that
        doubles the fee in place (which it does not). ``id`` and ``created_at``
        are in the snapshot too — a re-upload that deleted and reinserted would
        satisfy every value assertion and still lose the record of when the line
        was first seen.
        """
        _sweep()
        owned = _Owned()
        db = SessionLocal()
        source = f"dup-{_uid()}.csv"
        try:
            user = _create_user(db, owned)
            product = _create_product(db, owned)
            when = datetime(2018, 2, 8, 8, 0, 0)
            _, mtid = _create_paid_order(
                db, owned, user, product, at=when, total="2360.00"
            )
            db.commit()

            payload = _csv(
                _line(
                    "pay_FEB0001",
                    credit="2360.00",
                    fee="47.20",
                    tax="8.50",
                    created_at="2018-02-08 13:30:00",
                    settled_at="2018-02-10 11:00:00",
                    settlement_id="setl_feb01",
                    utr="UTRFEB01",
                    receipt=mtid,
                ),
                _line(
                    "pay_FEB0002",
                    credit="1000.00",
                    fee="20.00",
                    tax="3.60",
                    created_at="2018-02-08 15:00:00",
                    settled_at="2018-02-10 11:00:00",
                    settlement_id="setl_feb01",
                    utr="UTRFEB01",
                    receipt="no reference we know",
                ),
            )

            first = ingest_settlement_csv(
                db, payload, amount_unit=AmountUnit.MAJOR, source_file=source
            )
            assert first.rows_new == 2
            before = _snapshot(db)
            assert len(before) == 2

            second = ingest_settlement_csv(
                db, payload, amount_unit=AmountUnit.MAJOR, source_file=source
            )
            db.expire_all()
            after = _snapshot(db)

            assert second.rows_parsed == 2
            assert second.rows_new == 0, (
                "the second upload must recognise every transaction id it "
                "already holds"
            )
            assert after == before, (
                "re-uploading an identical settlement file must be a no-op. "
                f"before={before} after={after}"
            )

            # And a third time, because "idempotent" is not a property of the
            # second call in particular.
            ingest_settlement_csv(
                db, payload, amount_unit=AmountUnit.MAJOR, source_file=source
            )
            db.expire_all()
            assert _snapshot(db) == before
        finally:
            db.close()
            _sweep()


# ===========================================================================
# 3. Both gaps are recorded, and neither is dropped
# ===========================================================================


class TestTheTwoGaps:
    """March 2018."""

    def test_unmatched_line_and_unsettled_capture_are_both_recorded(self) -> None:
        """The two findings, in both directions, stored and retrievable.

        Direction one: the gateway paid us for something our records cannot
        explain. The line must survive ingest with its full gross, fee and dates —
        NOT be dropped as unparseable and NOT be zeroed as unknown — and must come
        back from ``unmatched_settlements``.

        Direction two: we captured a payment the gateway has never settled. There
        is no settlement row for it by definition, so it can only be found by
        looking for the absence; ``unsettled_captured_payments`` does, and the
        rollup stores the count so the finding survives on the dashboard.

        Both are then asserted on the rollup row, because a finding that lives
        only in a service call nobody makes is not surfaced.
        """
        _sweep()
        owned = _Owned()
        db = SessionLocal()
        source = f"gaps-{_uid()}.csv"
        try:
            generation = _generation(db)
            user = _create_user(db, owned)
            product = _create_product(db, owned)
            when = datetime(2018, 3, 6, 7, 30, 0)

            # A capture we recorded and the gateway has never settled.
            _create_paid_order(db, owned, user, product, at=when, total="777.00")
            db.commit()

            result = ingest_settlement_csv(
                db,
                _csv(
                    _line(
                        "pay_MAR_ORPHAN",
                        credit="4321.00",
                        fee="86.42",
                        tax="15.56",
                        created_at="2018-03-06 13:00:00",
                        settled_at="2018-03-08 11:00:00",
                        settlement_id="setl_mar01",
                        utr="UTRMAR01",
                        receipt="a reference from another system entirely",
                    )
                ),
                amount_unit=AmountUnit.MAJOR,
                source_file=source,
            )
            assert result.matched == 0 and result.unmatched == 1
            assert result.warnings, "an unmatched line must be reported, not hidden"

            stored = _settlement_rows(db, source_file=source)
            assert len(stored) == 1, "an unmatched line must still be STORED"
            orphan = stored[0]
            assert orphan.match_status == SettlementMatchStatus.UNMATCHED
            assert orphan.match_key == SettlementMatchKey.NONE
            assert orphan.order_id is None
            assert orphan.gross_minor == 432100, "its money must not be zeroed"
            assert orphan.fee_minor == 8642
            assert orphan.payment_date == date(2018, 3, 6)
            assert orphan.settlement_date == date(2018, 3, 8)

            listed = unmatched_settlements(
                db,
                date(2018, 3, 1),
                date(2018, 4, 1),
                gateway=GATEWAY,
                tz_generation=generation,
            )
            assert [r.transaction_id for r in listed] == ["pay_MAR_ORPHAN"]

            outstanding = unsettled_captured_payments(
                db, date(2018, 3, 1), date(2018, 4, 1), gateway=GATEWAY
            )
            assert len(outstanding) == 1, (
                "a captured payment with no settlement line after the grace "
                "period is a finding and must be retrievable"
            )
            assert outstanding[0].amount_minor == 77700
            assert outstanding[0].days_outstanding > 0

            _run_job(db, date(2018, 3, 6))
            db.commit()
            row = _rollup(db, date(2018, 3, 6), generation)
            assert row is not None
            assert row.unmatched_txns == 1
            assert row.unmatched_gross == from_minor(432100)
            assert row.matched_txns == 0
            assert row.unsettled_payments == 1
            assert row.unsettled_amount == from_minor(77700)
            assert row.txns_settled == 0, (
                "the cash for this line moved on the 8th, not the 6th"
            )
        finally:
            db.close()
            _sweep()


# ===========================================================================
# 4. Refunds are negative and are not fees
# ===========================================================================


class TestRefundSign:
    """April 2018."""

    def test_refund_line_reduces_the_payout_and_is_not_a_fee(self) -> None:
        """A refund arrives as a debit. It must stay negative all the way through.

        The failure mode this guards is an ``abs()`` in the parser: it turns the
        refund into extra revenue AND, in the variants that also normalise the
        fee columns, turns a zero refund fee into a positive cost. Both halves are
        asserted — the sign of the gross and the exact zero of the fee — plus the
        payout, which is the number a finance person compares against the bank.
        """
        _sweep()
        owned = _Owned()
        db = SessionLocal()
        source = f"refund-{_uid()}.csv"
        try:
            generation = _generation(db)
            user = _create_user(db, owned)
            product = _create_product(db, owned)
            when = datetime(2018, 4, 4, 7, 0, 0)
            _, mtid = _create_paid_order(
                db, owned, user, product, at=when, total="1180.00"
            )
            db.commit()

            ingest_settlement_csv(
                db,
                _csv(
                    _line(
                        "pay_APR0001",
                        credit="1180.00",
                        fee="23.60",
                        tax="4.25",
                        created_at="2018-04-04 12:30:00",
                        settled_at="2018-04-06 11:00:00",
                        settlement_id="setl_apr01",
                        utr="UTRAPR01",
                        receipt=mtid,
                    ),
                    _line(
                        "rfnd_APR0001",
                        kind="refund",
                        debit="500.00",
                        fee="0",
                        tax="0",
                        created_at="2018-04-04 16:00:00",
                        settled_at="2018-04-06 11:00:00",
                        settlement_id="setl_apr01",
                        utr="UTRAPR01",
                        receipt=f"refund against {mtid}",
                    ),
                ),
                amount_unit=AmountUnit.MAJOR,
                source_file=source,
            )

            rows = {r.transaction_id: r for r in _settlement_rows(db, source_file=source)}
            refund = rows["rfnd_APR0001"]
            assert refund.gross_minor == -50000, (
                "a refund is a NEGATIVE line; a positive gross here means the "
                "parser took its magnitude and booked a reversal as revenue"
            )
            assert refund.fee_minor == 0, (
                "the refund carried no fee; a positive fee here means the sign "
                "handling leaked into the fee columns"
            )
            assert refund.tax_minor == 0
            assert refund.net_minor == -50000
            assert refund.transaction_type == SettlementTxnType.REFUND

            payment = rows["pay_APR0001"]
            assert payment.gross_minor == 118000 and payment.fee_minor == 2360

            _run_job(db, date(2018, 4, 4))
            _run_job(db, date(2018, 4, 6))
            db.commit()

            transacted = _rollup(db, date(2018, 4, 4), generation)
            assert transacted is not None
            assert transacted.payments_transacted == 1
            assert transacted.reversals_transacted == 1
            assert transacted.gross_transacted == from_minor(118000 - 50000)
            assert transacted.fee_transacted == from_minor(2360), (
                "the refund must not add to the day's fee"
            )

            settled = _rollup(db, date(2018, 4, 6), generation)
            assert settled is not None
            assert settled.txns_settled == 2
            assert settled.settlement_batches == 1
            assert settled.payout_amount == from_minor(118000 - 2360 - 425 - 50000)
            assert settled.payout_amount < from_minor(118000), (
                "the refund must REDUCE the payout"
            )
        finally:
            db.close()
            _sweep()


# ===========================================================================
# 5. Paisa-exact reconciliation
# ===========================================================================


class TestPaisaExactness:
    """May 2018."""

    def test_reconciliation_is_exact_to_the_paisa(self) -> None:
        """``gross - fee - tax == net``, as integers, with no tolerance.

        Deliberately awkward amounts: values that divide cleanly by 100 would
        pass even if the whole pipeline worked in rupees and rounded.
        """
        _sweep()
        owned = _Owned()
        db = SessionLocal()
        source = f"exact-{_uid()}.csv"
        try:
            user = _create_user(db, owned)
            product = _create_product(db, owned)
            when = datetime(2018, 5, 9, 7, 0, 0)
            _, mtid = _create_paid_order(
                db, owned, user, product, at=when, total="1234.56"
            )
            db.commit()

            result = ingest_settlement_csv(
                db,
                _csv(
                    _line(
                        "pay_MAY0001",
                        credit="1234.56",
                        fee="29.13",
                        tax="5.24",
                        created_at="2018-05-09 12:30:00",
                        settled_at="2018-05-11 11:00:00",
                        settlement_id="setl_may01",
                        utr="UTRMAY01",
                        receipt=mtid,
                    ),
                    _line(
                        "pay_MAY0002",
                        credit="99.99",
                        fee="2.36",
                        tax="0.43",
                        created_at="2018-05-09 12:45:00",
                        settled_at="2018-05-11 11:00:00",
                        settlement_id="setl_may01",
                        utr="UTRMAY01",
                        receipt="unattributable",
                    ),
                ),
                amount_unit=AmountUnit.MAJOR,
                source_file=source,
            )

            assert result.gross_minor == 123456 + 9999
            assert result.fee_minor == 2913 + 236
            assert result.tax_minor == 524 + 43
            assert (
                result.net_minor
                == result.gross_minor - result.fee_minor - result.tax_minor
            )

            for row in _settlement_rows(db, source_file=source):
                assert isinstance(row.gross_minor, int)
                assert (
                    row.net_minor
                    == row.gross_minor - row.fee_minor - row.tax_minor
                ), f"{row.transaction_id} does not close to the paisa"

            report = settlement_reconciliation(
                db, date(2018, 5, 1), date(2018, 6, 1), gateway=GATEWAY
            )
            identity = report.by_key(SettlementCheckKey.PAYOUT_IDENTITY)
            assert identity.status == CheckStatus.MATCH
            assert identity.difference == Decimal("0.00")

            captured = report.by_key(SettlementCheckKey.SETTLED_VS_CAPTURED)
            assert captured.status == CheckStatus.MATCH, (
                "the one matched line is worth exactly the one captured payment"
            )
            assert captured.left_value == _d("1234.56")

            unmatched = report.by_key(SettlementCheckKey.UNMATCHED_LINES)
            assert unmatched.status == CheckStatus.VARIANCE
            assert unmatched.missing_ids == ("pay_MAY0002",)
        finally:
            db.close()
            _sweep()

    def test_the_database_itself_refuses_a_line_that_does_not_close(self) -> None:
        """``ck_payment_settlements_net`` is not decoration.

        The identity is enforced by the DATABASE, so no code path — including a
        hand-run INSERT during an incident, or a future service that forgets to
        compute ``net`` — can store a settlement line whose arithmetic does not
        close. If this test starts failing, check the server version: MySQL 5.7
        parses CHECK constraints and silently ignores them.
        """
        _sweep()
        db = SessionLocal()
        try:
            # MySQL raises 3819 for a CHECK violation, which pymysql surfaces as
            # OperationalError rather than IntegrityError. Both are caught and the
            # constraint is then asserted BY NAME, so this stays true across driver
            # versions and cannot pass on some unrelated failure.
            with pytest.raises((IntegrityError, OperationalError)) as exc:
                db.execute(
                    text(
                        "INSERT INTO payment_settlements "
                        "(gateway, transaction_id, transaction_type, gross_minor, "
                        " fee_minor, tax_minor, net_minor, transacted_at, "
                        " payment_date, tz_generation) "
                        "VALUES ('razorpay', 'pay_BROKEN_MAY', 'payment', 100000, "
                        " 2000, 360, 99999, '2018-05-20 06:30:00', "
                        " '2018-05-20', 1)"
                    )
                )
                db.flush()
            assert "ck_payment_settlements_net" in str(exc.value), str(exc.value)
            db.rollback()

            # ...and the same INSERT with the identity satisfied is accepted, so
            # the rejection above is the constraint doing its job and not some
            # unrelated problem with the statement.
            db.execute(
                text(
                    "INSERT INTO payment_settlements "
                    "(gateway, transaction_id, transaction_type, gross_minor, "
                    " fee_minor, tax_minor, net_minor, transacted_at, "
                    " payment_date, tz_generation) "
                    "VALUES ('razorpay', 'pay_OK_MAY', 'payment', 100000, "
                    " 2000, 360, 97640, '2018-05-20 06:30:00', "
                    " '2018-05-20', 1)"
                )
            )
            db.commit()
        finally:
            db.close()
            _sweep()

    def test_an_empty_window_reports_not_configured_not_a_clean_bill(self) -> None:
        """No report uploaded is NOT everything balanced.

        This is the same rule ``reconciliation.py`` is built around, applied to
        the settlement checks: a window with no settlement lines produces four
        listed rows with NULL values, and zero rows claiming a match. An empty
        variance table reads as "reconciled", which is the strongest claim the
        system can make from the weakest possible evidence.
        """
        _sweep()
        db = SessionLocal()
        try:
            report = settlement_reconciliation(
                db, date(2018, 5, 25), date(2018, 5, 28), gateway=GATEWAY
            )
            assert len(report.checks) == 4
            assert all(
                c.status == CheckStatus.NOT_CONFIGURED for c in report.checks
            )
            assert not any(c.status == CheckStatus.MATCH for c in report.checks)
            for check in report.checks:
                assert check.left_value is None
                assert check.right_value is None
                assert check.difference is None
                assert check.coverage_pct is None
            assert all(
                r["status"] == CheckStatus.NOT_CONFIGURED for r in report.to_rows()
            )
        finally:
            db.close()
            _sweep()


# ===========================================================================
# 6. Payment-date vs settlement-date bucketing
# ===========================================================================


class TestDateBucketing:
    """June and July 2018 — the one case that spans two months by design."""

    def test_fee_buckets_on_the_payment_day_and_cash_on_the_settlement_day(
        self,
    ) -> None:
        """Captured on the 30th, settled on the 2nd. Two buckets, two figures.

        The fee is a cost of the sale and belongs on the 30th, in the same bucket
        as the revenue it was charged against. The cash moved on the 2nd. Putting
        either on the other day is the classic error in settlement reporting: it
        misstates both numbers simultaneously and every individual figure still
        looks plausible.

        Both buckets are asserted for BOTH families of columns, including the
        zeros — asserting only the non-zero half would pass on an implementation
        that wrote every figure into both buckets.
        """
        _sweep()
        owned = _Owned()
        db = SessionLocal()
        source = f"timing-{_uid()}.csv"
        try:
            generation = _generation(db)
            user = _create_user(db, owned)
            product = _create_product(db, owned)
            when = datetime(2018, 6, 30, 7, 0, 0)
            _, mtid = _create_paid_order(
                db, owned, user, product, at=when, total="5900.00"
            )
            db.commit()

            result = ingest_settlement_csv(
                db,
                _csv(
                    _line(
                        "pay_JUN0030",
                        credit="5900.00",
                        fee="118.00",
                        tax="21.24",
                        created_at="2018-06-30 12:30:00",
                        settled_at="2018-07-02 10:00:00",
                        settlement_id="setl_jul02",
                        utr="UTRJUL02",
                        receipt=mtid,
                    )
                ),
                amount_unit=AmountUnit.MAJOR,
                source_file=source,
            )
            assert result.payment_dates == (date(2018, 6, 30),)
            assert result.settlement_dates == (date(2018, 7, 2),)
            assert result.dirty_dates == (date(2018, 6, 30), date(2018, 7, 2)), (
                "a single upload dirties two different buckets, and the caller "
                "has to be told which so it can enqueue both"
            )

            row = _settlement_rows(db, source_file=source)[0]
            assert row.payment_date == date(2018, 6, 30)
            assert row.settlement_date == date(2018, 7, 2)

            _run_job(db, date(2018, 6, 30))
            _run_job(db, date(2018, 7, 2))
            db.commit()

            fee_day = _rollup(db, date(2018, 6, 30), generation)
            assert fee_day is not None
            assert fee_day.txns_transacted == 1
            assert fee_day.fee_transacted == from_minor(11800)
            assert fee_day.tax_transacted == from_minor(2124)
            assert fee_day.gross_transacted == from_minor(590000)
            assert fee_day.txns_settled == 0, "no cash moved on the 30th"
            assert fee_day.payout_amount == from_minor(0)
            assert fee_day.fee_settled == from_minor(0)

            cash_day = _rollup(db, date(2018, 7, 2), generation)
            assert cash_day is not None
            assert cash_day.txns_settled == 1
            assert cash_day.settlement_batches == 1
            assert cash_day.payout_amount == from_minor(590000 - 11800 - 2124)
            assert cash_day.txns_transacted == 0, "no sale was processed on the 2nd"
            assert cash_day.fee_transacted == from_minor(0), (
                "the fee belongs to the 30th; a non-zero fee here means the two "
                "date bases have been conflated"
            )
            assert cash_day.gross_transacted == from_minor(0)

            # And the observed fee follows the payment date, not the cash date.
            assert (
                observed_gateway_fee(
                    db, date(2018, 6, 30), gateway=GATEWAY, tz_generation=generation
                ).fee_minor
                == 11800
            )
            assert (
                observed_gateway_fee(
                    db, date(2018, 7, 2), gateway=GATEWAY, tz_generation=generation
                )
                is None
            )
        finally:
            db.close()
            _sweep()

    def test_rerunning_the_job_leaves_the_buckets_identical(self) -> None:
        """The job contract: same bucket twice, same table.

        The recompute queue's lease can expire and hand a bucket to a second
        worker, and the runner retries, so this is not an optimisation.
        """
        _sweep()
        owned = _Owned()
        db = SessionLocal()
        source = f"rerun-{_uid()}.csv"
        try:
            generation = _generation(db)
            user = _create_user(db, owned)
            product = _create_product(db, owned)
            when = datetime(2018, 7, 18, 7, 0, 0)
            _, mtid = _create_paid_order(
                db, owned, user, product, at=when, total="250.00"
            )
            db.commit()
            ingest_settlement_csv(
                db,
                _csv(
                    _line(
                        "pay_JUL0018",
                        credit="250.00",
                        fee="5.00",
                        tax="0.90",
                        created_at="2018-07-18 12:30:00",
                        settled_at="2018-07-20 10:00:00",
                        settlement_id="setl_jul18",
                        receipt=mtid,
                    )
                ),
                amount_unit=AmountUnit.MAJOR,
                source_file=source,
            )
            _run_job(db, date(2018, 7, 18))
            db.commit()
            first = _rollup(db, date(2018, 7, 18), generation)
            snapshot = (
                first.txns_transacted,
                first.fee_transacted,
                first.tax_transacted,
                first.gross_transacted,
                first.matched_txns,
            )

            _run_job(db, date(2018, 7, 18))
            db.commit()
            db.expire_all()
            again = _rollup(db, date(2018, 7, 18), generation)
            assert (
                again.txns_transacted,
                again.fee_transacted,
                again.tax_transacted,
                again.gross_transacted,
                again.matched_txns,
            ) == snapshot
            assert (
                db.execute(
                    select(AggSettlementDaily).where(
                        AggSettlementDaily.bucket_date == date(2018, 7, 18),
                        AggSettlementDaily.tz_generation == generation,
                    )
                )
                .scalars()
                .all()
                .__len__()
                == 1
            ), "a second run must replace the bucket, never add to it"
        finally:
            db.close()
            _sweep()


# ===========================================================================
# 7. A malformed file ingests nothing
# ===========================================================================


class TestMalformedFiles:
    """August 2018."""

    def _assert_rejected_and_unchanged(
        self, db: Session, payload: str, *, expect: str
    ) -> SettlementIngestError:
        before = _snapshot(db)
        with pytest.raises(SettlementIngestError) as exc:
            ingest_settlement_csv(
                db,
                payload,
                amount_unit=AmountUnit.MAJOR,
                source_file=f"bad-{_uid()}.csv",
            )
        db.rollback()
        db.expire_all()
        assert _snapshot(db) == before, (
            "a rejected file must write NOTHING. A partial ingest looks like a "
            "successful upload, reconciles against nothing, and the missing rows "
            "are invisible until somebody chases a number months later"
        )
        assert expect.lower() in str(exc.value).lower(), str(exc.value)
        return exc.value

    def test_a_malformed_file_ingests_nothing_at_all(self) -> None:
        """Four kinds of bad file, and the table is unchanged after every one.

        A good row is seeded first, so "unchanged" is a real claim about a
        non-empty table rather than the trivial one about an empty one, and so a
        rejected file cannot be shown to have left the previously ingested data
        alone only because there was none.
        """
        _sweep()
        owned = _Owned()
        db = SessionLocal()
        try:
            user = _create_user(db, owned)
            product = _create_product(db, owned)
            when = datetime(2018, 8, 3, 7, 0, 0)
            _, mtid = _create_paid_order(
                db, owned, user, product, at=when, total="100.00"
            )
            db.commit()
            ingest_settlement_csv(
                db,
                _csv(
                    _line(
                        "pay_AUG_GOOD",
                        credit="100.00",
                        fee="2.00",
                        tax="0.36",
                        created_at="2018-08-03 12:30:00",
                        settled_at="2018-08-05 10:00:00",
                        settlement_id="setl_aug01",
                        receipt=mtid,
                    )
                ),
                amount_unit=AmountUnit.MAJOR,
                source_file=f"good-{_uid()}.csv",
            )
            assert len(_snapshot(db)) == 1

            # (a) a required column is missing. `fee` in particular: ingesting a
            # report with no fee column as "this gateway charged us nothing" would
            # replace an ESTIMATED fee with a confident, wrong, ACTUAL zero.
            self._assert_rejected_and_unchanged(
                db,
                "entity_id,type,credit,debit,amount,tax,created_at\n"
                "pay_X,payment,10.00,0,10.00,0.18,2018-08-04 12:00:00\n",
                expect="fee",
            )

            # (b) a garbage amount. Never coerced to zero: a fee that failed to
            # parse is UNKNOWN, and zero is a measurement.
            self._assert_rejected_and_unchanged(
                db,
                _csv(
                    _line(
                        "pay_AUG_BAD1",
                        credit="100.00",
                        fee="twelve rupees",
                        tax="0.36",
                        created_at="2018-08-04 12:00:00",
                    )
                ),
                expect="not a number",
            )

            # (c) one bad row among good ones. The good rows must NOT land.
            error = self._assert_rejected_and_unchanged(
                db,
                _csv(
                    _line(
                        "pay_AUG_OK1",
                        credit="10.00",
                        fee="0.20",
                        tax="0.04",
                        created_at="2018-08-04 12:00:00",
                    ),
                    _line(
                        "pay_AUG_BAD2",
                        credit="10.00",
                        fee="0.20",
                        tax="0.04",
                        created_at="04/08/2018 12:00:00",
                    ),
                    _line(
                        "pay_AUG_OK2",
                        credit="10.00",
                        fee="0.20",
                        tax="0.04",
                        created_at="2018-08-04 13:00:00",
                    ),
                ),
                expect="not a recognised timestamp",
            )
            assert error.problems, "every problem must be reported, not just a count"

            # (d) the same transaction id twice in one file. Which figures survive
            # would otherwise be arbitrary under the idempotency key.
            self._assert_rejected_and_unchanged(
                db,
                _csv(
                    _line(
                        "pay_AUG_TWICE",
                        credit="10.00",
                        fee="0.20",
                        tax="0.04",
                        created_at="2018-08-04 12:00:00",
                    ),
                    _line(
                        "pay_AUG_TWICE",
                        credit="99.00",
                        fee="1.98",
                        tax="0.36",
                        created_at="2018-08-04 12:05:00",
                    ),
                ),
                expect="already appeared",
            )
        finally:
            db.close()
            _sweep()

    def test_the_amount_unit_is_never_inferred(self) -> None:
        """Reading paise as rupees is a 100x error that reconciles with itself.

        The caller must declare which the file is in, and the same bytes parse to
        two different — both internally consistent — sets of figures depending on
        the declaration. That is precisely why guessing is refused.
        """
        payload = _csv(
            _line(
                "pay_UNIT",
                credit="118000",
                fee="2360",
                tax="425",
                created_at="2018-08-20 12:00:00",
            )
        )
        as_paise = parse_settlement_csv(payload, amount_unit=AmountUnit.MINOR)
        as_rupees = parse_settlement_csv(payload, amount_unit=AmountUnit.MAJOR)
        assert as_paise[0].gross_minor == 118000
        assert as_rupees[0].gross_minor == 11800000
        with pytest.raises(SettlementIngestError, match="amount_unit"):
            parse_settlement_csv(payload, amount_unit="guess")

    def test_sub_paisa_precision_is_refused_rather_than_rounded(self) -> None:
        """A third decimal place means the column is not what we think it is."""
        with pytest.raises(SettlementIngestError, match="two decimal places"):
            parse_settlement_csv(
                _csv(
                    _line(
                        "pay_SUBPAISA",
                        credit="100.005",
                        fee="2.00",
                        tax="0.36",
                        created_at="2018-08-21 12:00:00",
                    )
                ),
                amount_unit=AmountUnit.MAJOR,
            )

    def test_currency_symbols_and_epoch_timestamps_parse(self) -> None:
        """What real exports actually contain, parsed without a float anywhere.

        A rupee symbol, a thousands separator inside a quoted cell, an ``Rs.``
        prefix, a Unix epoch, an ISO timestamp with an offset, and the blank
        spellings of "nothing here" — all in one file, because they arrive that
        way.
        """
        rows = parse_settlement_csv(
            _csv(
                _line(
                    "pay_MESSY",
                    credit="₹1,234.56",
                    fee="Rs. 29.13",
                    tax="5.24",
                    created_at="1534766400",
                    settled_at="2018-08-22T10:00:00+05:30",
                    settlement_id="setl_messy",
                ),
                _line(
                    "rfnd_MESSY",
                    kind="refund",
                    debit="500.00",
                    fee="",
                    tax="-",
                    created_at="2018-08-20 12:00:00",
                ),
            ),
            amount_unit=AmountUnit.MAJOR,
        )
        assert rows[0].gross_minor == 123456
        assert rows[0].fee_minor == 2913
        assert rows[0].tax_minor == 524
        # 1534766400 == 2018-08-20T12:00:00Z, unambiguously UTC.
        assert rows[0].transacted_at == datetime(2018, 8, 20, 12, 0, 0)
        # +05:30 converted, not discarded.
        assert rows[0].settled_at == datetime(2018, 8, 22, 4, 30, 0)
        assert rows[1].gross_minor == -50000, "a debit is a negative gross"
        assert rows[1].fee_minor == 0 and rows[1].tax_minor == 0

    def test_bracketed_negatives_parse_in_an_amount_only_report(self) -> None:
        """``(500.00)`` is accounting notation for -500, and appears in the
        single-``amount``-column exports rather than in credit/debit pairs.

        A negative value in a ``debit`` column is a different matter and is
        REFUSED: a debit is a magnitude, and a negative one is a credit that
        would cancel the wrong way. Both behaviours are asserted here so neither
        can be relaxed by accident.
        """
        rows = parse_settlement_csv(
            "entity_id,type,amount,fee,tax,created_at\n"
            "rfnd_BRACKET,refund,(500.00),0,0,2018-08-23 12:00:00\n"
            "pay_PLAIN,payment,100.00,2.00,0.36,2018-08-23 12:00:00\n",
            amount_unit=AmountUnit.MAJOR,
        )
        assert rows[0].gross_minor == -50000
        assert rows[1].gross_minor == 10000

        with pytest.raises(SettlementIngestError, match="magnitudes"):
            parse_settlement_csv(
                _csv(
                    _line(
                        "rfnd_NEGDEBIT",
                        kind="refund",
                        debit="(500.00)",
                        fee="0",
                        tax="0",
                        created_at="2018-08-23 12:00:00",
                    )
                ),
                amount_unit=AmountUnit.MAJOR,
            )

    def test_an_amount_only_report_refuses_to_guess_the_sign(self) -> None:
        """An unrecognised type with no credit/debit pair cannot be signed.

        Guessing "positive unless it says minus" is exactly how an adjustment
        that took money out gets booked as money coming in.
        """
        with pytest.raises(SettlementIngestError, match="direction of"):
            parse_settlement_csv(
                "entity_id,type,amount,fee,tax,created_at\n"
                "adj_1,adjustment,250.00,0,0,2018-08-24 12:00:00\n",
                amount_unit=AmountUnit.MAJOR,
            )


# ===========================================================================
# 8. Schema, migration and twin SQL
# ===========================================================================


def _sql_statements(text_: str) -> list[str]:
    """SQL statements with comments, blank lines and whitespace normalised away.

    Comments are stripped because the twin file is a hand-annotated copy of the
    generated DDL and its header is the whole reason it is readable. What must
    match byte-for-byte after normalisation is the DDL itself.
    """
    without_comments = "\n".join(
        line for line in text_.splitlines() if not line.strip().startswith("--")
    )
    return [
        re.sub(r"\s+", " ", part).strip()
        for part in without_comments.split(";")
        if part.strip()
    ]


class TestSchemaAndMigration:
    def test_reflected_schema_matches_the_orm(self) -> None:
        """The live tables are what the models say they are.

        The analytics schema ships as TWO artifacts that can silently diverge —
        an Alembic revision applied to CI, and a hand-applied SQL file for the
        shared production MySQL, which is on a lineage this repo does not
        contain. This reflects what the database ACTUALLY has.
        """
        db = SessionLocal()
        try:
            insp = inspect(db.get_bind())
            for model in (PaymentSettlement, AggSettlementDaily):
                table = model.__table__
                assert insp.has_table(table.name), (
                    f"{table.name} is declared in the ORM but missing from the "
                    "database. Run `alembic upgrade e8b207fd93c1`, or apply "
                    f"{TWIN_SQL.name} on the shared DB."
                )
                actual = {c["name"]: c for c in insp.get_columns(table.name)}
                missing = sorted(set(table.columns.keys()) - set(actual))
                assert not missing, f"{table.name}: in ORM but not in DB: {missing}"

                for col in table.columns:
                    db_type = str(actual[col.name]["type"]).upper()
                    orm_type = str(col.type).upper()
                    assert _family(db_type) == _family(orm_type), (
                        f"{table.name}.{col.name}: ORM says {orm_type}, "
                        f"database has {db_type}"
                    )
                    assert actual[col.name]["nullable"] == col.nullable, (
                        f"{table.name}.{col.name}: nullability disagrees"
                    )

                floats = [
                    c.name
                    for c in table.columns
                    if any(
                        k in str(c.type).upper() for k in ("FLOAT", "DOUBLE", "REAL")
                    )
                ]
                assert not floats, (
                    f"{table.name}: floating-point columns forbidden — binary "
                    f"floating point cannot represent 0.10: {floats}"
                )

                # Convention: no foreign keys, so these stay independently
                # truncatable and re-ingestable from the gateway's own file.
                assert not table.foreign_keys, f"{table.name}: ORM declares an FK"
                assert not insp.get_foreign_keys(table.name), (
                    f"{table.name}: database has an FK"
                )

                declared = {ix.name for ix in table.indexes}
                present = {ix["name"] for ix in insp.get_indexes(table.name)}
                assert not declared - present, (
                    f"{table.name}: indexes in ORM but absent from DB: "
                    f"{sorted(declared - present)}"
                )

                uniques = {
                    u["name"]: u["column_names"]
                    for u in insp.get_unique_constraints(table.name)
                }
                for constraint in table.constraints:
                    if constraint.__class__.__name__ != "UniqueConstraint":
                        continue
                    cols = [c.name for c in constraint.columns]
                    assert uniques.get(constraint.name) == cols, (
                        f"{table.name}: unique key {constraint.name} is "
                        f"{uniques.get(constraint.name)} in the DB, {cols} in the ORM"
                    )
                    # MySQL permits MANY NULLs under a UNIQUE index, so a nullable
                    # column inside one does not enforce uniqueness at all and the
                    # idempotency key silently stops working.
                    nullable = [c.name for c in constraint.columns if c.nullable]
                    assert not nullable, (
                        f"{table.name}: nullable column(s) {nullable} inside "
                        f"UNIQUE {constraint.name}"
                    )
        finally:
            db.close()

    def test_the_rollup_unique_key_includes_tz_generation(self) -> None:
        """Otherwise a reporting-timezone change lets rows bucketed under two
        different day boundaries collide in one key, corrupting history in a way
        that cannot be untangled afterwards."""
        cols = [
            [c.name for c in u.columns]
            for u in AggSettlementDaily.__table__.constraints
            if u.__class__.__name__ == "UniqueConstraint"
        ]
        assert len(cols) == 1
        assert "tz_generation" in cols[0], cols[0]

    def test_migration_and_twin_sql_agree(self) -> None:
        """The two artifacts cannot be allowed to disagree.

        Production applies the hand-reviewed SQL and never runs alembic
        (DEPLOY.md §6), so if the twin drifts from the revision, CI stays green
        while production 500s on every settlement call.

        The revision range is read from the migration module rather than
        hardcoded, because several agents were landing migrations concurrently
        and the lineage is re-chained centrally afterwards. Re-pointing
        ``down_revision`` changes only the ``-- Running upgrade`` comment, which
        normalisation strips — the CREATE TABLEs depend on nothing else.
        """
        source = MIGRATION.read_text()
        down = re.search(
            r"^down_revision:[^=]*=\s*'([^']+)'", source, re.MULTILINE
        )
        revision = re.search(r"^revision:[^=]*=\s*'([^']+)'", source, re.MULTILINE)
        assert down and revision, "could not read the revision ids from the migration"
        assert revision.group(1) == "e8b207fd93c1"

        generated = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "upgrade",
                f"{down.group(1)}:{revision.group(1)}",
                "--sql",
            ],
            cwd=str(BACKEND_DIR),
            capture_output=True,
            text=True,
        )
        assert generated.returncode == 0, generated.stderr[-2000:]

        expected = [
            s
            for s in _sql_statements(generated.stdout)
            # The twin deliberately omits the alembic_version stamp: the shared
            # remote DB is on a lineage this repo does not contain, and stamping
            # it with a revision id from this chain would corrupt its state.
            if not s.upper().startswith("UPDATE ALEMBIC_VERSION")
        ]
        actual = _sql_statements(TWIN_SQL.read_text())
        assert actual == expected, (
            "the twin SQL no longer matches the migration. Regenerate it:\n"
            f"  alembic upgrade {down.group(1)}:{revision.group(1)} --sql\n"
            "and re-apply the header block."
        )
        assert not any(
            "ALEMBIC_VERSION" in s.upper() for s in actual
        ), "the twin must not stamp alembic_version"
        # It must also touch nothing that already exists.
        assert all(
            "payment_settlements" in s or "agg_settlement_daily" in s for s in actual
        ), "the twin must touch only the two new tables"


def _family(t: str) -> str:
    """Compare the type FAMILY, not its rendering.

    MySQL reports DECIMAL where SQLAlchemy says NUMERIC, and integer widths
    render differently across versions. The mismatch that actually matters is a
    family one — NUMERIC modelled but DOUBLE in the database — because binary
    floating point silently corrupts money.
    """
    if "BOOL" in t or "TINYINT" in t:
        return "BOOL"
    for key in ("DECIMAL", "NUMERIC"):
        if key in t:
            return "NUMERIC"
    for key in ("BIGINT", "SMALLINT", "INTEGER", "INT"):
        if key in t:
            return "INT"
    for key in ("VARCHAR", "CHAR", "TEXT"):
        if key in t:
            return "STRING"
    for key in ("DATETIME", "TIMESTAMP"):
        if key in t:
            return "DATETIME"
    if "DATE" in t:
        return "DATE"
    if "JSON" in t:
        return "JSON"
    if "FLOAT" in t or "DOUBLE" in t or "REAL" in t:
        return "FLOAT"
    return t


# ===========================================================================
# 9. The API path is explicitly unbuilt
# ===========================================================================


class TestApiPathIsNotBuilt:
    def test_the_settlements_api_client_raises_rather_than_returning_nothing(
        self,
    ) -> None:
        """A stub returning ``[]`` would make "we never built this" look exactly
        like "the gateway settled nothing", and the second is a legitimate
        business fact somebody would act on."""
        with pytest.raises(SettlementApiNotImplemented) as exc:
            RazorpaySettlementApiClient().fetch(date(2018, 9, 1))
        assert "csv" in str(exc.value).lower(), (
            "the error must name the path that DOES work today"
        )

    def test_api_availability_lists_every_blocker_not_just_the_first(self) -> None:
        """An admin who fixes one blocker should see the next one immediately."""
        db = SessionLocal()
        try:
            report = api_availability(db)
            assert report["available"] is False
            assert report["csv_upload_available"] is True
            assert len(report["reasons"]) >= 2
            assert any("test keys" in r.lower() for r in report["reasons"]), (
                "the report must name the test-keys blocker, not only the "
                "missing client"
            )
            assert any("client" in r.lower() for r in report["reasons"])
        finally:
            db.close()
