"""Integration tests for the multi-gateway Payment Methods feature.

Covers:
  - PaymentMethodConfigService.list_items  (26 rows, sort order)
  - Admin HTTP endpoints permission gate (GET without token → 401)
  - update: enable-without-keys → ValidationError (422 via HTTP)
  - update: unknown credential key → ValidationError
  - update: enable unimplemented gateway (paytm) → ValidationError
  - Credential merge + secret masking in PaymentMethodRead
  - Public active-gateway list filtering
  - Checkout with gateway_code="mock" sets order.gateway_code
  - Checkout with disabled/unknown gateway_code → ValidationError

Runs inside the backend container:

    docker compose exec backend pytest tests/test_payment_methods.py -v

Strategy mirrors existing test modules:
  - Live MySQL via SessionLocal (no DB mocking).
  - Explicit teardown in `finally` blocks via a fresh session so partial
    rollbacks in the test session never leave orphan rows.
  - HTTP tests use FastAPI TestClient for endpoint-level coverage.
  - Checkout tests patch `get_payment_provider` where needed so they don't
    depend on Redis being pre-seeded.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret
from app.core.exceptions import ValidationError
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.models.payment_method import PaymentMethod
from app.models.product import Product
from app.models.user import User
from app.schemas.address import AddressCreate
from app.schemas.order import OrderItemCreate
from app.schemas.payment import CheckoutRequest
from app.schemas.payment_method import PaymentMethodUpdate
from app.services.payment_method_config_service import PaymentMethodConfigService
from app.services.payment_service import PaymentService
from tests.conftest import TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _make_user(db: Session, *, is_admin: bool = False) -> User:
    u = User(
        email=f"pmtest-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=is_admin,
    )
    db.add(u)
    db.flush()
    return u


def _make_product(db: Session, *, price: Decimal = Decimal("100.00"), stock: int = 50) -> Product:
    p = Product(
        sku=f"SKU-PMT-{_uid()}",
        name=f"PMTestProd-{_uid()}",
        price=price,
        stock=stock,
    )
    db.add(p)
    db.flush()
    return p


def _addr_create() -> AddressCreate:
    return AddressCreate(
        full_name="PM Test Buyer",
        phone="9876543210",
        line1="1 Test Lane",
        city="Mumbai",
        state="Maharashtra",
        country="IN",
        pincode="400001",
        label="home",
    )


def _cleanup(user_ids: list[int], product_ids: list[int], order_ids: list[int]) -> None:
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


def _reset_razorpay(db: Session) -> None:
    """Reset razorpay to its post-migration state: disabled, no credentials."""
    svc = PaymentMethodConfigService(db)
    # Clear credentials first (empty-string delete)
    svc.update(
        "razorpay",
        PaymentMethodUpdate(
            enabled=False,
            credentials={"key_id": "", "key_secret": "", "webhook_secret": ""},
        ),
    )


def _enable_mock(db: Session) -> None:
    """Ensure the mock gateway is enabled — tests must not assume it. The seed
    migration disables mock once a real gateway is configured, and other test
    modules toggle it, so any test relying on mock enables it explicitly."""
    PaymentMethodConfigService(db).update("mock", PaymentMethodUpdate(enabled=True))


def _get_admin_token(client: TestClient) -> str:
    """Obtain a bearer token for the seeded admin account.

    Patches the rate-limit flag off for the login call so repeated test runs
    don't exhaust the Redis bucket — this is the same strategy used by the
    other HTTP-facing tests that call auth endpoints.
    """
    from app.core import config as _config
    orig = _config.settings.RATE_LIMIT_ENABLED
    _config.settings.RATE_LIMIT_ENABLED = False
    try:
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": TEST_ADMIN_EMAIL, "password": TEST_ADMIN_PASSWORD},
        )
        assert resp.status_code == 200, f"Login failed: {resp.text}"
        return resp.json()["access_token"]
    finally:
        _config.settings.RATE_LIMIT_ENABLED = orig


# ---------------------------------------------------------------------------
# PaymentMethodConfigService — unit-style (service layer, real DB)
# ---------------------------------------------------------------------------

class TestPaymentMethodConfigServiceListItems:

    def test_list_returns_26_items(self) -> None:
        """list_items must return exactly 26 rows — one per seeded gateway."""
        db = SessionLocal()
        try:
            items = PaymentMethodConfigService(db).list_items()
            assert len(items) == 26, f"Expected 26 items, got {len(items)}"
        finally:
            db.close()

    def test_list_sorted_by_sort_order(self) -> None:
        """Items must come back ordered ascending by sort_order."""
        db = SessionLocal()
        try:
            items = PaymentMethodConfigService(db).list_items()
            orders = [i.sort_order for i in items]
            assert orders == sorted(orders), "Items are not sorted by sort_order"
        finally:
            db.close()

    def test_list_first_item_is_paypal(self) -> None:
        """PayPal has sort_order=1 and should be the first item."""
        db = SessionLocal()
        try:
            items = PaymentMethodConfigService(db).list_items()
            assert items[0].code == "paypal", f"First item should be paypal, got {items[0].code}"
        finally:
            db.close()

    def test_list_last_item_is_mock(self) -> None:
        """Mock gateway has sort_order=99 and should be last."""
        db = SessionLocal()
        try:
            items = PaymentMethodConfigService(db).list_items()
            assert items[-1].code == "mock", f"Last item should be mock, got {items[-1].code}"
        finally:
            db.close()


class TestPaymentMethodConfigServiceUpdate:

    def test_enable_without_credentials_raises_validation_error(self) -> None:
        """Attempting to enable razorpay before any credentials are stored
        must raise ValidationError with a message about missing fields."""
        db = SessionLocal()
        try:
            _reset_razorpay(db)

            svc = PaymentMethodConfigService(db)
            with pytest.raises(ValidationError) as exc_info:
                svc.update("razorpay", PaymentMethodUpdate(enabled=True))
            assert "missing required" in exc_info.value.message.lower(), (
                f"Unexpected error message: {exc_info.value.message}"
            )
        finally:
            _reset_razorpay(db)
            db.close()

    def test_unknown_credential_key_raises_validation_error(self) -> None:
        """Supplying a credential key not in the gateway's field list must
        raise ValidationError — unknown keys are rejected outright."""
        db = SessionLocal()
        try:
            svc = PaymentMethodConfigService(db)
            with pytest.raises(ValidationError) as exc_info:
                svc.update(
                    "razorpay",
                    PaymentMethodUpdate(credentials={"nonexistent_key": "value"}),
                )
            assert "nonexistent_key" in exc_info.value.message.lower() or \
                   "unknown" in exc_info.value.message.lower(), (
                f"Expected unknown-key mention in: {exc_info.value.message}"
            )
        finally:
            _reset_razorpay(db)
            db.close()

    def test_enable_unimplemented_gateway_raises_validation_error(self) -> None:
        """Enabling paytm (implemented=False in registry) must be refused even
        when all its required credentials are saved — no code integration exists."""
        db = SessionLocal()
        try:
            svc = PaymentMethodConfigService(db)
            # Save all required fields for paytm first.
            svc.update(
                "paytm",
                PaymentMethodUpdate(
                    credentials={
                        "merchant_id": "TEST_MID",
                        "merchant_key": "TEST_MKEY",
                        "website": "DEFAULT",
                        "industry_type": "Retail",
                    }
                ),
            )
            with pytest.raises(ValidationError) as exc_info:
                svc.update("paytm", PaymentMethodUpdate(enabled=True))
            msg = exc_info.value.message.lower()
            assert "not yet available" in msg or "integration" in msg, (
                f"Unexpected error message: {exc_info.value.message}"
            )
        finally:
            # Clear paytm credentials and ensure disabled.
            svc2 = PaymentMethodConfigService(db)
            svc2.update(
                "paytm",
                PaymentMethodUpdate(
                    enabled=False,
                    credentials={
                        "merchant_id": "",
                        "merchant_key": "",
                        "website": "",
                        "industry_type": "",
                    },
                ),
            )
            db.close()

    def test_credential_merge_and_secret_masking(self) -> None:
        """Stored credentials must be:
        - encrypted at rest (credentials_encrypted starts with 'gAAAA')
        - non-secret fields round-trip as plaintext in PaymentMethodRead.value
        - secret fields always return value=None in PaymentMethodRead
        - set=True when a value is stored
        """
        db = SessionLocal()
        try:
            _reset_razorpay(db)
            svc = PaymentMethodConfigService(db)

            result = svc.update(
                "razorpay",
                PaymentMethodUpdate(
                    credentials={"key_id": "rzp_test_testkey", "key_secret": "test_secret"}
                ),
            )

            # Check the PaymentMethodRead representation.
            key_id_field = next(f for f in result.fields if f.key == "key_id")
            key_secret_field = next(f for f in result.fields if f.key == "key_secret")

            assert key_id_field.set is True, "key_id should be set=True"
            assert key_id_field.value == "rzp_test_testkey", (
                f"Non-secret key_id should round-trip; got {key_id_field.value}"
            )
            assert key_secret_field.set is True, "key_secret should be set=True"
            assert key_secret_field.value is None, (
                "Secret key_secret must always return value=None (masked)"
            )

            # Verify encryption at rest.
            row = db.execute(
                select(PaymentMethod).where(PaymentMethod.gateway_code == "razorpay")
            ).scalar_one()
            enc = row.credentials_encrypted
            assert enc.startswith("gAAAA"), (
                f"credentials_encrypted should be Fernet ciphertext; starts with: {enc[:10]}"
            )
            assert "test_secret" not in enc, (
                "Plaintext secret must NOT appear in credentials_encrypted"
            )
            assert "rzp_test_testkey" not in enc, (
                "Plaintext key must NOT appear in credentials_encrypted"
            )

            # Verify decryption round-trip.
            creds = svc.credentials_for("razorpay")
            assert creds["key_id"] == "rzp_test_testkey"
            assert creds["key_secret"] == "test_secret"

        finally:
            _reset_razorpay(db)
            db.close()

    def test_credential_empty_string_deletes_key(self) -> None:
        """Sending "" for an existing key in the credentials dict must remove
        that key from the stored blob (not store an empty string)."""
        db = SessionLocal()
        try:
            _reset_razorpay(db)
            svc = PaymentMethodConfigService(db)

            # Set key_id first.
            svc.update("razorpay", PaymentMethodUpdate(credentials={"key_id": "rzp_test_initial"}))
            creds_before = svc.credentials_for("razorpay")
            assert "key_id" in creds_before

            # Now delete key_id by sending "".
            svc.update("razorpay", PaymentMethodUpdate(credentials={"key_id": ""}))
            creds_after = svc.credentials_for("razorpay")
            assert "key_id" not in creds_after, (
                "key_id should be removed when credential value is empty string"
            )
        finally:
            _reset_razorpay(db)
            db.close()

    def test_enable_after_credentials_set_succeeds(self) -> None:
        """Setting all required credentials then enabling must succeed and
        return ready=True, enabled=True."""
        db = SessionLocal()
        try:
            _reset_razorpay(db)
            svc = PaymentMethodConfigService(db)

            # Set credentials first.
            svc.update(
                "razorpay",
                PaymentMethodUpdate(credentials={"key_id": "rzp_test_abc", "key_secret": "sec123"}),
            )
            # Now enable.
            result = svc.update("razorpay", PaymentMethodUpdate(enabled=True))
            assert result.enabled is True
            assert result.ready is True
        finally:
            _reset_razorpay(db)
            db.close()


# ---------------------------------------------------------------------------
# Public active-gateway list filtering
# ---------------------------------------------------------------------------

class TestPublicActiveGatewayList:

    def test_active_list_excludes_disabled_gateways(self) -> None:
        """The public active list must only include gateways where
        enabled=True AND implemented=True AND ready=True (all required
        credentials present)."""
        db = SessionLocal()
        try:
            _reset_razorpay(db)  # ensure razorpay is disabled and has no creds
            _enable_mock(db)     # mock is the gateway this test expects active
            svc = PaymentMethodConfigService(db)
            all_items = svc.list_items()
            active = [i for i in all_items if i.enabled and i.implemented and i.ready]

            # After reset, only mock should be in the active list.
            active_codes = {i.code for i in active}
            assert "mock" in active_codes, "mock gateway must be active by default"
            assert "razorpay" not in active_codes, (
                "razorpay must not be active after resetting credentials"
            )

            # All items in the active list must be fully ready.
            for item in active:
                assert item.enabled, f"{item.code} is active but enabled=False"
                assert item.implemented, f"{item.code} is active but implemented=False"
                assert item.ready, f"{item.code} is active but ready=False"
        finally:
            db.close()

    def test_active_list_includes_razorpay_when_enabled_and_ready(self) -> None:
        """After enabling razorpay with valid credentials, it must appear in
        the active list."""
        db = SessionLocal()
        try:
            _reset_razorpay(db)
            svc = PaymentMethodConfigService(db)
            svc.update(
                "razorpay",
                PaymentMethodUpdate(
                    credentials={"key_id": "rzp_test_live", "key_secret": "live_sec"}
                ),
            )
            svc.update("razorpay", PaymentMethodUpdate(enabled=True))

            all_items = svc.list_items()
            active_codes = {i.code for i in all_items if i.enabled and i.implemented and i.ready}
            assert "razorpay" in active_codes, (
                "razorpay should appear in active list when enabled + ready"
            )
        finally:
            _reset_razorpay(db)
            db.close()


# ---------------------------------------------------------------------------
# HTTP endpoint tests (TestClient — exercises auth + routing)
# ---------------------------------------------------------------------------

class TestAdminPaymentMethodsEndpoints:

    def test_list_without_token_returns_401(self) -> None:
        """GET /admin/payment-methods without Authorization header must
        return 401 Unauthorized."""
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/admin/payment-methods")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"

    def test_list_with_admin_token_returns_26_items(self) -> None:
        """Admin GET must return all 26 items sorted by sort_order."""
        client = TestClient(app, raise_server_exceptions=False)
        token = _get_admin_token(client)
        resp = client.get(
            "/api/v1/admin/payment-methods",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, f"Unexpected status: {resp.status_code}"
        data = resp.json()
        assert "items" in data
        assert len(data["items"]) == 26, f"Expected 26, got {len(data['items'])}"
        sort_orders = [i["sort_order"] for i in data["items"]]
        assert sort_orders == sorted(sort_orders), "Items must be sorted by sort_order"

    def test_update_enable_without_keys_returns_422(self) -> None:
        """PUT razorpay with only enabled=true (no credentials stored) must
        return 422 Unprocessable Entity."""
        db = SessionLocal()
        client = TestClient(app, raise_server_exceptions=False)
        try:
            _reset_razorpay(db)
            db.close()

            token = _get_admin_token(client)
            resp = client.put(
                "/api/v1/admin/payment-methods/razorpay",
                json={"enabled": True},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 422, (
                f"Expected 422 when enabling without credentials, got {resp.status_code}: {resp.text}"
            )
        finally:
            db2 = SessionLocal()
            _reset_razorpay(db2)
            db2.close()

    def test_update_unknown_credential_key_returns_422(self) -> None:
        """PUT with an unknown credential key must return 422."""
        client = TestClient(app, raise_server_exceptions=False)
        token = _get_admin_token(client)
        resp = client.put(
            "/api/v1/admin/payment-methods/razorpay",
            json={"credentials": {"totally_fake_key": "value"}},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, (
            f"Expected 422 for unknown credential key, got {resp.status_code}: {resp.text}"
        )

    def test_secret_fields_have_value_null_in_response(self) -> None:
        """After storing credentials, secret fields must always return
        value=null in the admin list response — never expose secrets."""
        db = SessionLocal()
        client = TestClient(app, raise_server_exceptions=False)
        try:
            _reset_razorpay(db)
            svc = PaymentMethodConfigService(db)
            svc.update(
                "razorpay",
                PaymentMethodUpdate(credentials={"key_id": "rzp_chk", "key_secret": "sup3rs3cr3t"}),
            )
            db.close()

            token = _get_admin_token(client)
            resp = client.get(
                "/api/v1/admin/payment-methods",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            items = resp.json()["items"]
            rzp = next(i for i in items if i["code"] == "razorpay")
            key_secret_field = next(f for f in rzp["fields"] if f["key"] == "key_secret")

            assert key_secret_field["value"] is None, (
                "key_secret must be null in API response (secret field masking)"
            )
            assert key_secret_field["set"] is True, (
                "key_secret set flag must be True when a value is stored"
            )
        finally:
            db2 = SessionLocal()
            _reset_razorpay(db2)
            db2.close()


# ---------------------------------------------------------------------------
# Checkout with gateway_code integration
# ---------------------------------------------------------------------------

class TestCheckoutWithGatewayCode:

    def test_checkout_with_gateway_code_mock_sets_order_gateway_code(self) -> None:
        """Checkout with gateway_code='mock' must produce a PENDING order
        with order.gateway_code='mock' and a redirect_url containing 'mock'."""
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        db = SessionLocal()
        try:
            _enable_mock(db)
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db)
            product_ids.append(prod.id)
            db.commit()

            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address=_addr_create(),
                gateway_code="mock",
                payment_method="prepaid",
            )
            svc = PaymentService(db)
            order, mtid, redirect_url = svc.checkout(user, req)
            order_ids.append(order.id)

            assert order.gateway_code == "mock", (
                f"order.gateway_code expected 'mock', got '{order.gateway_code}'"
            )
            assert order.status.value == "pending"
            assert "mock" in redirect_url, (
                f"redirect_url should reference mock, got: {redirect_url}"
            )
        finally:
            db.rollback()
            _cleanup(user_ids, product_ids, order_ids)
            db.close()

    def test_checkout_without_gateway_code_auto_resolves_to_mock(self) -> None:
        """With only the mock gateway enabled, checkout without an explicit
        gateway_code must auto-resolve to mock."""
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        re_enable: list[str] = []
        db = SessionLocal()
        try:
            _reset_razorpay(db)  # ensure no real gateway is enabled
            # Make mock the ONLY enabled implemented gateway so auto-resolve is
            # deterministic regardless of ambient dev config (e.g. a PhonePe UAT
            # row left enabled — it has a lower sort_order than mock and would
            # otherwise win). Snapshot the others, disable them, restore later.
            cfg = PaymentMethodConfigService(db)
            re_enable = [
                i.code for i in cfg.list_items()
                if i.implemented and i.enabled and i.code != "mock"
            ]
            for code in re_enable:
                cfg.update(code, PaymentMethodUpdate(enabled=False))
            cfg.update("mock", PaymentMethodUpdate(enabled=True))

            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db)
            product_ids.append(prod.id)
            db.commit()

            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address=_addr_create(),
                payment_method="prepaid",
                # gateway_code omitted — auto-resolve path
            )
            svc = PaymentService(db)
            order, _, _ = svc.checkout(user, req)
            order_ids.append(order.id)

            assert order.gateway_code == "mock", (
                f"Auto-resolve should land on 'mock', got '{order.gateway_code}'"
            )
            assert order.status.value == "pending"
        finally:
            # Restore the gateways we disabled (creds unchanged → still ready).
            restore = PaymentMethodConfigService(db)
            for code in re_enable:
                try:
                    restore.update(code, PaymentMethodUpdate(enabled=True))
                except Exception:
                    pass
            db.rollback()
            _cleanup(user_ids, product_ids, order_ids)
            db.close()

    def test_checkout_with_disabled_gateway_raises_validation_error(self) -> None:
        """Requesting a disabled gateway explicitly at checkout must raise
        ValidationError — the customer cannot force a disabled provider."""
        user_ids: list[int] = []
        product_ids: list[int] = []
        db = SessionLocal()
        try:
            _reset_razorpay(db)  # ensures razorpay is disabled

            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db)
            product_ids.append(prod.id)
            db.commit()

            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address=_addr_create(),
                gateway_code="razorpay",  # disabled
                payment_method="prepaid",
            )
            svc = PaymentService(db)
            with pytest.raises(ValidationError) as exc_info:
                svc.checkout(user, req)
            msg = exc_info.value.message.lower()
            assert "not currently enabled" in msg or "not available" in msg, (
                f"Unexpected error message: {exc_info.value.message}"
            )
        finally:
            db.rollback()
            _cleanup(user_ids, product_ids, [])
            db.close()

    def test_checkout_with_unknown_gateway_code_raises_validation_error(self) -> None:
        """An unrecognised gateway_code at checkout must raise ValidationError
        immediately — the factory must not fall back silently."""
        user_ids: list[int] = []
        product_ids: list[int] = []
        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db)
            product_ids.append(prod.id)
            db.commit()

            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address=_addr_create(),
                gateway_code="completely_fake_gateway",
                payment_method="prepaid",
            )
            svc = PaymentService(db)
            with pytest.raises(ValidationError):
                svc.checkout(user, req)
        finally:
            db.rollback()
            _cleanup(user_ids, product_ids, [])
            db.close()
