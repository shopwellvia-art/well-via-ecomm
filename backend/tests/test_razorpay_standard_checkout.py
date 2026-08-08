"""Razorpay Standard Checkout (Orders API) tests.

Covers the migration off Payment Links:
  signature      – verify_signature true / tampered / empty / None
  initiate       – POSTs /orders with receipt+notes.mtid and returns the
                   embedded checkout.js payload (no redirect URL)
  fetch_status   – branches on the provider-ref generation: pay_ (payment
                   entity), order_ (scan the order's payments), plink_
                   (legacy Payment Links, unchanged)
  parse_webhook  – payment.captured / order.paid (new) + payment_link.paid
                   (legacy) + unknown-event and missing-mtid (no-op ack)
  verify+settle  – PaymentService.verify_and_settle_razorpay: a non-owner
                   sees 404, a bad signature never settles (and is audited),
                   a good signature + captured payment settles PAID with the
                   pay_ id stored, authorized-without-capture stays PENDING,
                   and a second call is idempotent

All provider HTTP methods are patched — no real network. The service-level
tests run against the live test DB (SessionLocal) with explicit cleanup,
mirroring tests/test_payment_webhook_lifecycle.py.

Run inside the backend container:
    docker compose exec -T backend pytest tests/test_razorpay_standard_checkout.py -v
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
import redis
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.integrations.payments.base import InitiateRequest, PaymentStatus
from app.integrations.payments.razorpay import RazorpayError, RazorpayProvider
from app.models.order import Order, OrderStatus
from app.models.payment_event import PaymentEvent, PaymentEventType
from app.models.product import Product
from app.models.user import User
from app.schemas.address import AddressCreate
from app.schemas.order import OrderItemCreate
from app.schemas.payment import CheckoutRequest, RazorpayVerifyRequest
from app.schemas.payment_method import PaymentMethodUpdate
from app.services.payment_method_config_service import PaymentMethodConfigService
from app.services.payment_service import PaymentService


# ---------------------------------------------------------------------------
# Local helpers  (self-contained — do NOT import across test files)
# ---------------------------------------------------------------------------

_KEY_ID = "rzp_test_x"
_SECRET = "sec_x"
_WHSEC = "whsec_test"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _provider() -> RazorpayProvider:
    return RazorpayProvider(
        key_id=_KEY_ID, key_secret=_SECRET, webhook_secret=_WHSEC
    )


def _sign(order_id: str, payment_id: str, secret: str = _SECRET) -> str:
    """Compute the signature checkout.js would hand the browser."""
    return hmac.new(
        secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
    ).hexdigest()


def _make_user(db: Session) -> User:
    u = User(
        email=f"rzpstd-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_product(
    db: Session, *, price: Decimal = Decimal("100.00"), stock: int = 50
) -> Product:
    uid = _uid()
    p = Product(
        sku=f"SKU-RZP-{uid}",
        name=f"RzpStdTestProd-{uid}",
        price=price,
        stock=stock,
    )
    db.add(p)
    db.flush()
    return p


def _addr_create() -> AddressCreate:
    return AddressCreate(
        full_name="Rzp Std Buyer",
        phone="9876543210",
        line1="1 Standard Checkout Lane",
        city="Bangalore",
        state="Karnataka",
        country="IN",
        pincode="560001",
        label="home",
    )


def _cleanup(
    user_ids: list[int],
    product_ids: list[int],
    order_ids: list[int],
) -> None:
    """Delete all test rows via a fresh session so partial rollbacks in the
    test session never leave orphan rows.  Deletion order honours FK constraints."""
    with SessionLocal() as s:
        if order_ids:
            ids_tuple = tuple(order_ids)
            s.execute(
                text("DELETE FROM order_items WHERE order_id IN :ids"),
                {"ids": ids_tuple},
            )
            s.execute(
                text("DELETE FROM orders WHERE id IN :ids"),
                {"ids": ids_tuple},
            )
        if product_ids:
            s.execute(
                text("DELETE FROM products WHERE id IN :ids"),
                {"ids": tuple(product_ids)},
            )
        if user_ids:
            s.execute(
                text("DELETE FROM addresses WHERE user_id IN :ids"),
                {"ids": tuple(user_ids)},
            )
            s.execute(
                text("DELETE FROM points_transactions WHERE user_id IN :ids"),
                {"ids": tuple(user_ids)},
            )
            s.execute(
                text("DELETE FROM customers WHERE user_id IN :ids"),
                {"ids": tuple(user_ids)},
            )
            s.execute(
                text("DELETE FROM users WHERE id IN :ids"),
                {"ids": tuple(user_ids)},
            )
        s.commit()


def _cleanup_events(mtid: str) -> None:
    with SessionLocal() as s:
        s.execute(
            text("DELETE FROM payment_events WHERE merchant_transaction_id = :mtid"),
            {"mtid": mtid},
        )
        s.commit()


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def _reset_razorpay(db: Session) -> None:
    """Reset razorpay to its post-migration state: disabled, no credentials.
    Copied verbatim from test_payment_methods.py."""
    svc = PaymentMethodConfigService(db)
    svc.update(
        "razorpay",
        PaymentMethodUpdate(
            enabled=False,
            credentials={"key_id": "", "key_secret": "", "webhook_secret": ""},
        ),
    )


def _enable_razorpay(db: Session) -> None:
    svc = PaymentMethodConfigService(db)
    svc.update(
        "razorpay",
        PaymentMethodUpdate(
            credentials={
                "key_id": _KEY_ID,
                "key_secret": _SECRET,
                "webhook_secret": _WHSEC,
            }
        ),
    )
    svc.update("razorpay", PaymentMethodUpdate(enabled=True))


def _enable_mock(db: Session) -> None:
    PaymentMethodConfigService(db).update("mock", PaymentMethodUpdate(enabled=True))


def _disable_mock(db: Session) -> None:
    PaymentMethodConfigService(db).update("mock", PaymentMethodUpdate(enabled=False))


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

class TestVerifySignature:
    ORDER_ID = "order_SIGTEST1"
    PAY_ID = "pay_SIGTEST1"

    def test_valid_signature_verifies_true(self) -> None:
        p = _provider()
        sig = _sign(self.ORDER_ID, self.PAY_ID)
        assert p.verify_signature(self.ORDER_ID, self.PAY_ID, sig) is True

    def test_tampered_signature_verifies_false(self) -> None:
        p = _provider()
        sig = _sign(self.ORDER_ID, self.PAY_ID)
        tampered = ("0" if sig[0] != "0" else "1") + sig[1:]
        assert p.verify_signature(self.ORDER_ID, self.PAY_ID, tampered) is False

    def test_signature_from_wrong_secret_verifies_false(self) -> None:
        p = _provider()
        sig = _sign(self.ORDER_ID, self.PAY_ID, secret="some_other_secret")
        assert p.verify_signature(self.ORDER_ID, self.PAY_ID, sig) is False

    def test_empty_and_none_signature_verify_false(self) -> None:
        p = _provider()
        assert p.verify_signature(self.ORDER_ID, self.PAY_ID, "") is False
        assert p.verify_signature(self.ORDER_ID, self.PAY_ID, None) is False


# ---------------------------------------------------------------------------
# initiate: Orders API + embedded checkout payload
# ---------------------------------------------------------------------------

class TestInitiate:
    def test_initiate_posts_orders_and_returns_checkout_payload(self) -> None:
        """initiate must POST /orders with receipt + notes.mtid and return an
        InitiateResponse whose checkout dict carries everything checkout.js
        needs (key_id, order_id, amount, prefill) — with no redirect URL."""
        p = _provider()
        mtid = f"ORDRZPTEST{_uid().upper()}"
        req = InitiateRequest(
            order_id=1,
            user_id=1,
            amount_minor=25000,
            currency="INR",
            merchant_transaction_id=mtid,
            return_url="https://shop.example/payment/return",
            user_email="buyer@example.com",
        )
        gateway_order = {
            "id": "order_INITTEST1",
            "amount": 25000,
            "currency": "INR",
            "receipt": mtid,
            "status": "created",
        }
        with patch.object(RazorpayProvider, "_post", return_value=gateway_order) as post_mock:
            resp = p.initiate(req)

        post_mock.assert_called_once_with(
            "/orders",
            {
                "amount": 25000,
                "currency": "INR",
                "receipt": mtid,
                "notes": {"mtid": mtid},
            },
        )
        assert resp.redirect_url == "", "Standard Checkout has no redirect URL"
        assert resp.provider_transaction_id == "order_INITTEST1"
        assert resp.raw == gateway_order
        assert resp.checkout == {
            "provider": "razorpay",
            "key_id": _KEY_ID,
            "order_id": "order_INITTEST1",
            "amount": 25000,
            "currency": "INR",
            "name": "Wellvia",
            "description": f"Order {mtid}",
            "prefill": {"email": "buyer@example.com"},
            "notes": {"mtid": mtid},
        }

    def test_initiate_without_order_id_raises(self) -> None:
        """A 2xx body with no order id must fail loudly, not hand the SPA a
        checkout payload that can never open."""
        p = _provider()
        req = InitiateRequest(
            order_id=1,
            user_id=1,
            amount_minor=1000,
            currency="INR",
            merchant_transaction_id="ORDNOID",
            return_url="https://shop.example/payment/return",
        )
        with patch.object(RazorpayProvider, "_post", return_value={}):
            with pytest.raises(RazorpayError):
                p.initiate(req)


# ---------------------------------------------------------------------------
# fetch_status: pay_ / order_ / plink_ branches
# ---------------------------------------------------------------------------

class TestFetchStatusBranches:
    MTID = "ORDFETCHTEST1"

    def test_pay_ref_captured_is_success_with_amount(self) -> None:
        p = _provider()
        payment = {"id": "pay_FST1", "status": "captured", "amount": 25000}
        with patch.object(RazorpayProvider, "_get", return_value=payment) as get_mock:
            result = p.fetch_status(self.MTID, "pay_FST1")
        get_mock.assert_called_once_with("/payments/pay_FST1")
        assert result.status == PaymentStatus.SUCCESS
        assert result.provider_transaction_id == "pay_FST1"
        assert result.amount_minor == 25000

    def test_pay_ref_failed_is_failed(self) -> None:
        p = _provider()
        payment = {"id": "pay_FST2", "status": "failed", "amount": 25000}
        with patch.object(RazorpayProvider, "_get", return_value=payment):
            result = p.fetch_status(self.MTID, "pay_FST2")
        assert result.status == PaymentStatus.FAILED
        assert result.amount_minor is None, "amount only reported on SUCCESS"

    def test_pay_ref_created_stays_pending(self) -> None:
        p = _provider()
        payment = {"id": "pay_FST3", "status": "created", "amount": 25000}
        with patch.object(RazorpayProvider, "_get", return_value=payment):
            result = p.fetch_status(self.MTID, "pay_FST3")
        assert result.status == PaymentStatus.PENDING

    def test_order_ref_with_captured_payment_is_success_with_pay_id(self) -> None:
        """order_ refs scan /orders/{id}/payments; the captured pay_ id must
        become the provider_transaction_id so settlement stores it."""
        p = _provider()
        payments = {
            "items": [
                {"id": "pay_FSTFAIL", "status": "failed", "amount": 25000},
                {"id": "pay_FSTOK", "status": "captured", "amount": 25000},
            ]
        }
        with patch.object(RazorpayProvider, "_get", return_value=payments) as get_mock:
            result = p.fetch_status(self.MTID, "order_FST4")
        get_mock.assert_called_once_with("/orders/order_FST4/payments")
        assert result.status == PaymentStatus.SUCCESS
        assert result.provider_transaction_id == "pay_FSTOK"
        assert result.amount_minor == 25000

    def test_order_ref_with_no_captured_payment_stays_pending(self) -> None:
        """A failed/abandoned attempt on the order doesn't preclude a retry
        inside the same checkout — never FAILED, always PENDING."""
        p = _provider()
        payments = {"items": [{"id": "pay_FSTFAIL", "status": "failed"}]}
        with patch.object(RazorpayProvider, "_get", return_value=payments):
            result = p.fetch_status(self.MTID, "order_FST5")
        assert result.status == PaymentStatus.PENDING
        assert result.provider_transaction_id == "order_FST5"

    def test_plink_ref_uses_legacy_payment_links_path(self) -> None:
        p = _provider()
        link = {"id": "plink_FST6", "status": "paid", "amount": 25000, "amount_paid": 25000}
        with patch.object(RazorpayProvider, "_get", return_value=link) as get_mock:
            result = p.fetch_status(self.MTID, "plink_FST6")
        get_mock.assert_called_once_with("/payment_links/plink_FST6")
        assert result.status == PaymentStatus.SUCCESS
        assert result.provider_transaction_id == "plink_FST6"
        assert result.amount_minor == 25000

    def test_no_ref_stays_pending_without_network(self) -> None:
        p = _provider()
        with patch.object(RazorpayProvider, "_get") as get_mock:
            result = p.fetch_status(self.MTID, None)
        get_mock.assert_not_called()
        assert result.status == PaymentStatus.PENDING


# ---------------------------------------------------------------------------
# parse_webhook: new events + legacy event
# ---------------------------------------------------------------------------

class TestParseWebhook:
    MTID = "ORDWEBHOOKTEST1"

    def test_payment_captured_resolves_success_with_pay_id(self) -> None:
        body = json.dumps(
            {
                "event": "payment.captured",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay_WH1",
                            "order_id": "order_WH1",
                            "status": "captured",
                            "amount": 25000,
                            "notes": {"mtid": self.MTID},
                        }
                    }
                },
            }
        ).encode()
        result = _provider().parse_webhook(body)
        assert result.merchant_transaction_id == self.MTID
        assert result.status == PaymentStatus.SUCCESS
        assert result.provider_transaction_id == "pay_WH1"
        assert result.amount_minor == 25000

    def test_order_paid_resolves_success_with_pay_id_from_receipt(self) -> None:
        """order.paid carries no payment notes on the order entity — the mtid
        must come from the receipt we stamped at initiate, and the pay_ id
        from the accompanying payment entity."""
        body = json.dumps(
            {
                "event": "order.paid",
                "payload": {
                    "order": {
                        "entity": {
                            "id": "order_WH2",
                            "receipt": self.MTID,
                            "amount": 25000,
                            "amount_paid": 25000,
                            "status": "paid",
                        }
                    },
                    "payment": {
                        "entity": {
                            "id": "pay_WH2",
                            "order_id": "order_WH2",
                            "status": "captured",
                            "amount": 25000,
                        }
                    },
                },
            }
        ).encode()
        result = _provider().parse_webhook(body)
        assert result.merchant_transaction_id == self.MTID
        assert result.status == PaymentStatus.SUCCESS
        assert result.provider_transaction_id == "pay_WH2"
        assert result.amount_minor == 25000

    def test_legacy_payment_link_paid_unchanged(self) -> None:
        body = json.dumps(
            {
                "event": "payment_link.paid",
                "payload": {
                    "payment_link": {
                        "entity": {
                            "id": "plink_WH3",
                            "reference_id": self.MTID,
                            "status": "paid",
                            "amount": 25000,
                            "amount_paid": 25000,
                        }
                    }
                },
            }
        ).encode()
        result = _provider().parse_webhook(body)
        assert result.merchant_transaction_id == self.MTID
        assert result.status == PaymentStatus.SUCCESS
        assert result.provider_transaction_id == "plink_WH3"
        assert result.amount_minor == 25000

    def test_unknown_event_with_recoverable_mtid_is_pending(self) -> None:
        """payment.failed (unhandled) must not settle anything — PENDING is a
        no-op in handle_webhook — but the mtid must still come back so the
        event is auditable against the order."""
        body = json.dumps(
            {
                "event": "payment.failed",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay_WH4",
                            "status": "failed",
                            "amount": 25000,
                            "notes": {"mtid": self.MTID},
                        }
                    }
                },
            }
        ).encode()
        result = _provider().parse_webhook(body)
        assert result.merchant_transaction_id == self.MTID
        assert result.status == PaymentStatus.PENDING

    def test_webhook_without_any_mtid_is_a_pending_noop(self) -> None:
        """A signed payload with no recoverable mtid (e.g. payment.captured
        for a legacy payment link that carries no notes.mtid) must resolve to
        an uncorrelated PENDING no-op, never raise: raising 502s the webhook
        route, Razorpay redelivers the same event forever, and sustained
        failures disable the whole webhook."""
        body = json.dumps(
            {
                "event": "payment.captured",
                "payload": {
                    "payment": {"entity": {"id": "pay_WH5", "status": "captured"}}
                },
            }
        ).encode()
        result = _provider().parse_webhook(body)
        assert result.merchant_transaction_id == ""
        assert result.status == PaymentStatus.PENDING
        assert result.provider_transaction_id == "pay_WH5"


# ---------------------------------------------------------------------------
# verify_and_settle_razorpay (live DB)
# ---------------------------------------------------------------------------

class TestVerifyAndSettle:
    """The browser-callback settle path. Orders are checked out through the
    mock gateway (no network at initiate), then re-routed to razorpay with a
    known order_ provider ref — the same trick TestGatewayConfirmation uses
    in test_payment_webhook_lifecycle.py."""

    ORDER_REF = "order_VRFTEST1"
    PAY_ID = "pay_VRFTEST1"

    def _pending_razorpay_order(
        self,
        db: Session,
        user_ids: list[int],
        product_ids: list[int],
        order_ids: list[int],
    ) -> tuple[Order, User, str, int]:
        _enable_mock(db)
        _enable_razorpay(db)
        user = _make_user(db)
        user_ids.append(user.id)
        prod = _make_product(db, price=Decimal("100.00"), stock=50)
        product_ids.append(prod.id)
        db.commit()

        req = CheckoutRequest(
            items=[OrderItemCreate(product_id=prod.id, quantity=1)],
            address=_addr_create(),
            gateway_code="mock",
            payment_method="prepaid",
        )
        order, mtid, _redirect, _checkout = PaymentService(db).checkout(user, req)
        order_ids.append(order.id)
        order.gateway_code = "razorpay"
        order.payment_provider_ref = self.ORDER_REF
        db.commit()
        expected_minor = int(
            (Decimal(str(order.total_amount)) * 100).to_integral_value()
        )
        return order, user, mtid, expected_minor

    def _verify_request(self, mtid: str, *, signature: str | None = None) -> RazorpayVerifyRequest:
        return RazorpayVerifyRequest(
            merchant_transaction_id=mtid,
            razorpay_order_id=self.ORDER_REF,
            razorpay_payment_id=self.PAY_ID,
            razorpay_signature=(
                signature
                if signature is not None
                else _sign(self.ORDER_REF, self.PAY_ID)
            ),
        )

    def _payment_entity(self, amount_minor: int, status: str = "captured") -> dict:
        return {
            "id": self.PAY_ID,
            "order_id": self.ORDER_REF,
            "status": status,
            "amount": amount_minor,
            "currency": "INR",
        }

    @staticmethod
    def _events_for(db: Session, order_id: int, event_type: str) -> list[PaymentEvent]:
        return list(
            db.execute(
                select(PaymentEvent).where(
                    PaymentEvent.order_id == order_id,
                    PaymentEvent.event_type == event_type,
                )
            ).scalars()
        )

    def _teardown(
        self,
        user_ids: list[int],
        product_ids: list[int],
        order_ids: list[int],
        mtid: str | None,
    ) -> None:
        db = SessionLocal()
        try:
            _reset_razorpay(db)
            _disable_mock(db)
        finally:
            db.close()
        if mtid:
            _cleanup_events(mtid)
            _redis_client().delete(f"payment:mock:{mtid}")
        _cleanup(user_ids, product_ids, order_ids)
        if user_ids:
            _redis_client().delete(f"cart:{user_ids[0]}")

    def test_wrong_user_sees_not_found(self) -> None:
        """404, not 403 — matches get_status so neither mtid-keyed endpoint
        confirms to a non-owner that the transaction id exists."""
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtid: str | None = None

        db = SessionLocal()
        try:
            order, _user, mtid, _minor = self._pending_razorpay_order(
                db, user_ids, product_ids, order_ids
            )
            intruder = _make_user(db)
            user_ids.append(intruder.id)
            db.commit()

            with pytest.raises(NotFoundError):
                PaymentService(db).verify_and_settle_razorpay(
                    intruder, self._verify_request(mtid)
                )

            db.refresh(order)
            assert order.status == OrderStatus.PENDING
        finally:
            db.rollback()
            db.close()
            self._teardown(user_ids, product_ids, order_ids, mtid)

    def test_bad_signature_never_settles_and_is_audited(self) -> None:
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtid: str | None = None

        db = SessionLocal()
        try:
            order, user, mtid, _minor = self._pending_razorpay_order(
                db, user_ids, product_ids, order_ids
            )

            with patch.object(RazorpayProvider, "fetch_payment") as fetch_mock:
                with pytest.raises(ValidationError):
                    PaymentService(db).verify_and_settle_razorpay(
                        user, self._verify_request(mtid, signature="deadbeef")
                    )
            # The gateway must never even be consulted for a bad signature.
            fetch_mock.assert_not_called()

            # End the session's REPEATABLE READ snapshot: the audit row was
            # committed through record_payment_event's own session, which this
            # session's in-flight transaction (opened by the order lookup)
            # cannot see. A rollback starts a fresh snapshot that can.
            db.rollback()
            db.refresh(order)
            assert order.status == OrderStatus.PENDING, (
                f"Order must stay PENDING on a bad signature, got {order.status}"
            )
            assert order.paid_at is None

            events = self._events_for(
                db, order.id, PaymentEventType.CLIENT_SIGNATURE_INVALID
            )
            assert len(events) == 1, (
                f"Exactly one client_signature_invalid event expected, got {len(events)}"
            )
            assert events[0].signature_valid is False
        finally:
            db.rollback()
            db.close()
            self._teardown(user_ids, product_ids, order_ids, mtid)

    def test_good_signature_captured_settles_paid_with_pay_ref(self) -> None:
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtid: str | None = None

        db = SessionLocal()
        try:
            order, user, mtid, expected_minor = self._pending_razorpay_order(
                db, user_ids, product_ids, order_ids
            )

            with patch.object(
                RazorpayProvider,
                "fetch_payment",
                return_value=self._payment_entity(expected_minor),
            ) as fetch_mock:
                settled = PaymentService(db).verify_and_settle_razorpay(
                    user, self._verify_request(mtid)
                )
            fetch_mock.assert_called_once_with(self.PAY_ID)

            assert settled.status == OrderStatus.PAID, (
                f"Expected PAID after captured verify, got {settled.status}"
            )
            assert settled.paid_at is not None
            # The stored ref must be upgraded from the order_ id to the pay_
            # id — the whole point of the Standard Checkout migration.
            assert settled.payment_provider_ref == self.PAY_ID
            prepaid_leg = next(
                p for p in settled.payments
                if (p.payment_method or "").lower() != "cod"
            )
            assert prepaid_leg.gateway_payment_id == self.PAY_ID
        finally:
            db.rollback()
            db.close()
            self._teardown(user_ids, product_ids, order_ids, mtid)

    def test_authorized_without_capture_stays_pending(self) -> None:
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtid: str | None = None

        db = SessionLocal()
        try:
            order, user, mtid, expected_minor = self._pending_razorpay_order(
                db, user_ids, product_ids, order_ids
            )

            with patch.object(
                RazorpayProvider,
                "fetch_payment",
                return_value=self._payment_entity(expected_minor, status="authorized"),
            ):
                result = PaymentService(db).verify_and_settle_razorpay(
                    user, self._verify_request(mtid)
                )

            assert result.status == OrderStatus.PENDING, (
                "authorized-without-capture must NOT mark the order PAID — "
                f"got {result.status}"
            )
            db.refresh(order)
            assert order.paid_at is None
        finally:
            db.rollback()
            db.close()
            self._teardown(user_ids, product_ids, order_ids, mtid)

    def test_second_call_is_idempotent(self) -> None:
        """After a successful settle, a repeat verify must return the PAID
        order without consulting the gateway or re-running side effects."""
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        mtid: str | None = None

        db = SessionLocal()
        try:
            order, user, mtid, expected_minor = self._pending_razorpay_order(
                db, user_ids, product_ids, order_ids
            )

            with patch.object(
                RazorpayProvider,
                "fetch_payment",
                return_value=self._payment_entity(expected_minor),
            ):
                PaymentService(db).verify_and_settle_razorpay(
                    user, self._verify_request(mtid)
                )

            with patch.object(RazorpayProvider, "fetch_payment") as fetch_mock:
                with patch(
                    "app.services.payment_service.PaymentService._send_notification"
                ) as notify_mock:
                    again = PaymentService(db).verify_and_settle_razorpay(
                        user, self._verify_request(mtid)
                    )
            fetch_mock.assert_not_called()
            notify_mock.assert_not_called()
            assert again.status == OrderStatus.PAID
            assert again.payment_provider_ref == self.PAY_ID
        finally:
            db.rollback()
            db.close()
            self._teardown(user_ids, product_ids, order_ids, mtid)
