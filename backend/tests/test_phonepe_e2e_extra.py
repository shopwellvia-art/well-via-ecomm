"""Supplementary PhonePe E2E scenarios — gaps not covered by
``test_phonepe_checkout_suite.py``.

A thorough tester's matrix for the states a real PhonePe payment passes through:
  * EVERY failure code PhonePe can return (not just PAYMENT_ERROR) must cancel
    the order and restore stock.
  * The realistic "user is waiting" flow: a first poll comes back PENDING, then
    a later poll resolves to SUCCESS (paid) or FAILED (cancelled).
  * A late SUCCESS arriving AFTER the order was already cancelled by a failure
    must NOT revive it to PAID (terminal-state guard / out-of-order callbacks).

Reuses the proven harness from test_phonepe_checkout_suite (real PhonePeProvider,
mocked HTTP boundary, shared dev DB, FK-safe self-cleanup).

Run inside the backend container:
    docker compose exec -T backend pytest tests/test_phonepe_e2e_extra.py -v
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

# Make the sibling suite importable regardless of pytest's import mode. This
# used to hardcode "/app/tests", the mount point of one particular container;
# anywhere else it silently inserted a path that does not exist and the import
# only worked because pytest happens to prepend the rootdir itself. Derive it
# from this file so the suite is not tied to one deployment's layout.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_phonepe_checkout_suite as S  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.order import OrderStatus  # noqa: E402
from app.models.order_payment import PaymentTxnStatus  # noqa: E402
from app.models.payment_event import PaymentEventType  # noqa: E402
from app.services.payment_service import PaymentService  # noqa: E402


# All codes PhonePe maps to a failed payment (phonepe.py:_FAILED_CODES).
FAILURE_CODES = [
    "PAYMENT_ERROR",
    "PAYMENT_DECLINED",
    "PAYMENT_CANCELLED",
    "TIMED_OUT",
    "TRANSACTION_NOT_FOUND",
]


@pytest.mark.parametrize("fail_code", FAILURE_CODES)
def test_every_failure_code_cancels_and_restores_stock(fail_code: str) -> None:
    """Each distinct PhonePe failure code → order CANCELLED, leg FAILED, stock
    fully restored, never paid. Proves decline / cancel / timeout / not-found
    are all handled, not just the generic PAYMENT_ERROR."""
    user_ids: list[int] = []
    product_ids: list[int] = []
    order_ids: list[int] = []
    mtid: str | None = None
    db = SessionLocal()
    try:
        user = S._make_user(db)
        user_ids.append(user.id)
        prod = S._make_product(db, price=Decimal("100.00"), stock=50)
        product_ids.append(prod.id)
        db.commit()
        pre_stock = prod.stock

        svc = PaymentService(db)
        order, mtid, provider = S._checkout_phonepe(db, svc, user, prod, quantity=3)
        order_ids.append(order.id)
        amount_minor = S._expected_minor(S._snapshot_order(order.id)["total_amount"])

        envelope = S._phonepe_status_envelope(
            mtid, amount_minor, f"T_{fail_code}", code=fail_code, state="FAILED"
        )
        body, sig = S._sign_webhook(provider, envelope)
        # handle_webhook confirms the claimed failure with the gateway before
        # acting on it, so the status endpoint has to report it too.
        S._gateway_confirms(provider, envelope)
        with S._use_phonepe(provider):
            PaymentService(db).handle_webhook(body, sig, gateway_code="phonepe")

        snap = S._snapshot_order(order.id)
        assert snap["status"] == OrderStatus.CANCELLED, fail_code
        assert snap["paid_at"] is None, fail_code
        assert snap["legs"][0]["status"] == PaymentTxnStatus.FAILED, fail_code
        assert S._stock(prod.id) == pre_stock, f"{fail_code}: stock not restored"
    finally:
        db.rollback()
        S._cleanup(user_ids, product_ids, order_ids)
        if mtid:
            S._redis().delete(f"payment:mock:{mtid}")
        db.close()


def test_pending_then_success_transition() -> None:
    """The 'waiting' flow: first status poll is PENDING (order untouched), a
    later poll returns SUCCESS and settles the order to PAID exactly once."""
    user_ids: list[int] = []
    product_ids: list[int] = []
    order_ids: list[int] = []
    mtid: str | None = None
    db = SessionLocal()
    try:
        user = S._make_user(db)
        user_ids.append(user.id)
        prod = S._make_product(db, price=Decimal("100.00"), stock=50)
        product_ids.append(prod.id)
        db.commit()
        pre_stock = prod.stock

        svc = PaymentService(db)
        order, mtid, provider = S._checkout_phonepe(db, svc, user, prod, quantity=2)
        order_ids.append(order.id)
        amount_minor = S._expected_minor(S._snapshot_order(order.id)["total_amount"])

        # Poll 1 — still processing.
        provider._get = MagicMock(
            return_value=S._phonepe_status_envelope(
                mtid, amount_minor, "T_WAIT", code="PAYMENT_PENDING", state="PENDING"
            )
        )
        with S._use_phonepe(provider):
            PaymentService(db).get_status(user.id, mtid)
        mid = S._snapshot_order(order.id)
        assert mid["status"] == OrderStatus.PENDING
        assert mid["legs"][0]["status"] == PaymentTxnStatus.INITIATED
        assert S._stock(prod.id) == pre_stock - 2  # stock stays reserved while waiting

        # Poll 2 — PhonePe now reports success.
        txn_id = "T_DONE_" + S._uid().upper()
        provider._get = MagicMock(
            return_value=S._phonepe_status_envelope(mtid, amount_minor, txn_id)
        )
        with S._use_phonepe(provider):
            PaymentService(db).get_status(user.id, mtid)

        snap = S._snapshot_order(order.id)
        assert snap["status"] == OrderStatus.PAID
        assert snap["paid_at"] is not None
        assert snap["payment_provider_ref"] == txn_id
        assert snap["legs"][0]["status"] == PaymentTxnStatus.PAID
        assert snap["legs"][0]["gateway_payment_id"] == txn_id
        assert snap["legs"][0]["raw"] is not None
        assert S._stock(prod.id) == pre_stock - 2  # no double deduction
        # Two polls happened, but the settle transition applied exactly once.
        assert S._count_events(mtid, PaymentEventType.STATUS_POLL) == 2
        assert S._count_events(mtid, PaymentEventType.STATUS_APPLIED) == 1
    finally:
        db.rollback()
        S._cleanup(user_ids, product_ids, order_ids)
        if mtid:
            S._redis().delete(f"payment:mock:{mtid}")
        db.close()


def test_pending_then_failure_transition_restores_stock() -> None:
    """'Waiting then it failed': first poll PENDING, later poll FAILED → the
    order cancels and the reserved stock is returned."""
    user_ids: list[int] = []
    product_ids: list[int] = []
    order_ids: list[int] = []
    mtid: str | None = None
    db = SessionLocal()
    try:
        user = S._make_user(db)
        user_ids.append(user.id)
        prod = S._make_product(db, price=Decimal("100.00"), stock=50)
        product_ids.append(prod.id)
        db.commit()
        pre_stock = prod.stock

        svc = PaymentService(db)
        order, mtid, provider = S._checkout_phonepe(db, svc, user, prod, quantity=3)
        order_ids.append(order.id)
        amount_minor = S._expected_minor(S._snapshot_order(order.id)["total_amount"])

        provider._get = MagicMock(
            return_value=S._phonepe_status_envelope(
                mtid, amount_minor, "T_W2", code="PAYMENT_PENDING", state="PENDING"
            )
        )
        with S._use_phonepe(provider):
            PaymentService(db).get_status(user.id, mtid)
        assert S._snapshot_order(order.id)["status"] == OrderStatus.PENDING
        assert S._stock(prod.id) == pre_stock - 3

        provider._get = MagicMock(
            return_value=S._phonepe_status_envelope(
                mtid, amount_minor, "T_W2F", code="PAYMENT_DECLINED", state="FAILED"
            )
        )
        with S._use_phonepe(provider):
            PaymentService(db).get_status(user.id, mtid)

        snap = S._snapshot_order(order.id)
        assert snap["status"] == OrderStatus.CANCELLED
        assert snap["paid_at"] is None
        assert snap["legs"][0]["status"] == PaymentTxnStatus.FAILED
        assert S._stock(prod.id) == pre_stock  # reserved stock returned
    finally:
        db.rollback()
        S._cleanup(user_ids, product_ids, order_ids)
        if mtid:
            S._redis().delete(f"payment:mock:{mtid}")
        db.close()


def test_late_success_after_failure_does_not_revive_order() -> None:
    """Out-of-order callbacks: an order already CANCELLED by a failure must NOT
    be flipped back to PAID by a late SUCCESS webhook, and stock must not be
    re-deducted. Guards against a replayed/delayed success.

    Holding the order CANCELLED is only half the obligation. The gateway is
    telling us it took the customer's money, and refusing to mark the order paid
    does not give it back — so `_apply_status` routes a post-cancellation
    settlement to `_refund_settled_after_cancel`: a SETTLED_AFTER_CANCEL audit
    row, a refund attempt, and the captured leg flipped to REFUNDED on the books
    whether or not the gateway could be reached. Asserting REFUNDED rather than
    FAILED is the point: FAILED would mean we had quietly kept the money.
    """
    user_ids: list[int] = []
    product_ids: list[int] = []
    order_ids: list[int] = []
    mtid: str | None = None
    db = SessionLocal()
    try:
        user = S._make_user(db)
        user_ids.append(user.id)
        prod = S._make_product(db, price=Decimal("100.00"), stock=50)
        product_ids.append(prod.id)
        db.commit()
        pre_stock = prod.stock

        svc = PaymentService(db)
        order, mtid, provider = S._checkout_phonepe(db, svc, user, prod, quantity=2)
        order_ids.append(order.id)
        amount_minor = S._expected_minor(S._snapshot_order(order.id)["total_amount"])

        # First: a failure cancels the order + restores stock.
        envelope_f = S._phonepe_status_envelope(
            mtid, amount_minor, "T_LF", code="PAYMENT_ERROR", state="FAILED"
        )
        body_f, sig_f = S._sign_webhook(provider, envelope_f)
        S._gateway_confirms(provider, envelope_f)
        with S._use_phonepe(provider):
            PaymentService(db).handle_webhook(body_f, sig_f, gateway_code="phonepe")
        assert S._snapshot_order(order.id)["status"] == OrderStatus.CANCELLED
        assert S._stock(prod.id) == pre_stock

        # Then: a late SUCCESS for the same order arrives — and PhonePe itself
        # would corroborate it if asked. Arming the status endpoint with the
        # success envelope is deliberate: it proves the order is held CANCELLED
        # by the terminal-state guard in `_apply_status`, and not merely by
        # `handle_webhook` skipping confirmation for a non-PENDING order.
        envelope_s = S._phonepe_status_envelope(mtid, amount_minor, "T_LS_SUCCESS")
        body_s, sig_s = S._sign_webhook(provider, envelope_s)
        S._gateway_confirms(provider, envelope_s)
        with S._use_phonepe(provider):
            PaymentService(db).handle_webhook(body_s, sig_s, gateway_code="phonepe")

        snap = S._snapshot_order(order.id)
        assert snap["status"] == OrderStatus.CANCELLED, "late success must not revive"
        assert snap["paid_at"] is None
        assert snap["legs"][0]["status"] == PaymentTxnStatus.REFUNDED, (
            "money captured after cancellation is owed back — the leg must be "
            "REFUNDED on the books, not left FAILED as though nothing was taken"
        )
        assert S._stock(prod.id) == pre_stock, "stock must not be re-deducted"
        # The reversal is audited, and audited exactly once.
        assert S._count_events(mtid, PaymentEventType.SETTLED_AFTER_CANCEL) == 1
        assert S._count_events(mtid, PaymentEventType.REFUND_ATTEMPT) >= 1

        # A redelivery of the same late success must be a no-op: the leg is
        # already REFUNDED, so no second refund is attempted.
        with S._use_phonepe(provider):
            PaymentService(db).handle_webhook(body_s, sig_s, gateway_code="phonepe")
        replayed = S._snapshot_order(order.id)
        assert replayed["status"] == OrderStatus.CANCELLED
        assert replayed["legs"][0]["status"] == PaymentTxnStatus.REFUNDED
        assert S._count_events(mtid, PaymentEventType.SETTLED_AFTER_CANCEL) == 1, (
            "a redelivered settlement must not refund twice"
        )
    finally:
        db.rollback()
        S._cleanup(user_ids, product_ids, order_ids)
        if mtid:
            S._redis().delete(f"payment:mock:{mtid}")
        db.close()
