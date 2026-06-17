"""Tests for the two payment robustness fixes.

1. Gateway ``environment`` normalization — "production" / "prod" map to "live",
   and anything else (incl. unknown / None / "sandbox") resolves to "sandbox".
   The factory must use the normalized value to pick the base URL (this is the
   silent sandbox-fallback that contributed to the earlier PhonePe 404).
2. A payment provider failure surfaces as a clean 502 Bad Gateway carrying the
   gateway's own error message — NOT a 500 Internal Server Error.

Runs inside the backend container:

    docker compose exec -T backend python -m pytest tests/test_payment_robustness.py -v
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.exceptions import AppError
from app.db.session import SessionLocal
from app.integrations.payments.base import PaymentGatewayError, normalize_environment
from app.integrations.payments.factory import _PHONEPE_BASE, _build_provider
from app.integrations.payments.flutterwave import FlutterwaveError
from app.integrations.payments.paypal import PayPalError
from app.integrations.payments.paystack import PaystackError
from app.integrations.payments.phonepe import PhonePeError
from app.integrations.payments.razorpay import RazorpayError
from app.integrations.payments.stripe import StripeError
from app.main import app
from app.models.payment_method import PaymentMethod
from app.models.product import Product


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Fix 1 — environment normalization
# ---------------------------------------------------------------------------

class TestNormalizeEnvironment:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("production", "live"),
            ("prod", "live"),
            ("live", "live"),
            ("PRODUCTION", "live"),
            ("  Live  ", "live"),
            ("sandbox", "sandbox"),
            ("SANDBOX", "sandbox"),
            ("test", "sandbox"),
            ("uat", "sandbox"),
            ("", "sandbox"),
            ("   ", "sandbox"),
            (None, "sandbox"),
            ("staging", "sandbox"),
        ],
    )
    def test_normalize(self, value, expected):
        assert normalize_environment(value) == expected


class TestFactoryUsesNormalizedEnvironment:
    """The factory must select PhonePe's LIVE base URL for a live alias like
    "production" and SANDBOX otherwise — proving normalization is wired into
    provider construction (no silent sandbox fallback)."""

    def _phonepe_base_for(self, env: str) -> str:
        row = PaymentMethod(
            gateway_code="phonepe", display_name="PhonePe", environment=env
        )
        creds = {"merchant_id": "M", "salt_key": "S", "salt_index": "1"}
        return _build_provider(row, creds).base_url

    def test_production_alias_uses_live_url(self):
        assert self._phonepe_base_for("production") == _PHONEPE_BASE["live"].rstrip("/")

    def test_prod_alias_uses_live_url(self):
        assert self._phonepe_base_for("prod") == _PHONEPE_BASE["live"].rstrip("/")

    def test_live_uses_live_url(self):
        assert self._phonepe_base_for("live") == _PHONEPE_BASE["live"].rstrip("/")

    def test_sandbox_uses_sandbox_url(self):
        assert self._phonepe_base_for("sandbox") == _PHONEPE_BASE["sandbox"].rstrip("/")

    def test_unknown_falls_back_to_sandbox(self):
        assert self._phonepe_base_for("staging") == _PHONEPE_BASE["sandbox"].rstrip("/")


# ---------------------------------------------------------------------------
# Fix 2 — provider failures are 502, not 500
# ---------------------------------------------------------------------------

class TestProviderErrorsAre502:
    @pytest.mark.parametrize(
        "error_cls",
        [PhonePeError, RazorpayError, StripeError, PayPalError, PaystackError, FlutterwaveError],
    )
    def test_provider_error_is_bad_gateway(self, error_cls):
        err = error_cls("upstream blew up")
        assert isinstance(err, PaymentGatewayError)
        assert isinstance(err, AppError)
        assert err.status_code == 502
        assert err.code == "payment_provider_error"


def _admin_token(client: TestClient) -> str:
    """Bearer token for the seeded admin; rate-limit disabled around login so
    repeated runs don't exhaust the Redis bucket (mirrors test_payment_methods)."""
    from app.core import config as _config

    orig = _config.settings.RATE_LIMIT_ENABLED
    _config.settings.RATE_LIMIT_ENABLED = False
    try:
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "vinay@gmail.com", "password": "vinay@123"},
        )
        assert resp.status_code == 200, f"Login failed: {resp.text}"
        return resp.json()["access_token"]
    finally:
        _config.settings.RATE_LIMIT_ENABLED = orig


class TestCheckoutProviderFailureReturns502:
    def test_checkout_returns_502_when_provider_initiate_fails(self):
        """A gateway rejection during checkout must surface as HTTP 502 with the
        gateway's own message — not a 500 Internal Server Error."""
        product_ids: list[int] = []
        db = SessionLocal()
        try:
            p = Product(
                sku=f"SKU-RB-{_uid()}", name=f"RobustTest {_uid()}",
                price=Decimal("100.00"), stock=5,
            )
            db.add(p); db.flush(); product_ids.append(p.id); db.commit()
            prod_id = p.id
        finally:
            db.close()

        client = TestClient(app, raise_server_exceptions=False)
        token = _admin_token(client)

        fake = MagicMock()
        fake.name = "phonepe"
        fake.initiate.side_effect = PhonePeError(
            "PhonePe rejected the request: KEY_NOT_CONFIGURED — Key not found for the merchant",
            details={
                "status": 400,
                "provider_code": "KEY_NOT_CONFIGURED",
                "provider_message": "Key not found for the merchant",
            },
        )
        body = {
            "items": [{"product_id": prod_id, "quantity": 1}],
            "address": {
                "full_name": "RB Buyer", "phone": "9876543210", "line1": "1 Test Rd",
                "city": "Mumbai", "state": "Maharashtra", "country": "IN",
                "pincode": "400001", "label": "home",
            },
            "payment_method": "prepaid",
            "gateway_code": "phonepe",
        }
        try:
            with patch(
                "app.services.payment_service.get_payment_provider", return_value=fake
            ):
                resp = client.post(
                    "/api/v1/checkout", json=body,
                    headers={"Authorization": f"Bearer {token}"},
                )
            assert resp.status_code == 502, (
                f"Expected 502 Bad Gateway, got {resp.status_code}: {resp.text}"
            )
            envelope = resp.json()["error"]
            assert envelope["code"] == "payment_provider_error"
            assert "PhonePe rejected" in envelope["message"], (
                f"Provider message not surfaced: {envelope}"
            )
        finally:
            # Checkout rolls back on the 502 so no order persists; clean the
            # product (and any stray order_items, defensively) anyway.
            with SessionLocal() as s:
                s.execute(
                    text("DELETE FROM order_items WHERE product_id IN :ids"),
                    {"ids": tuple(product_ids)},
                )
                s.execute(
                    text("DELETE FROM products WHERE id IN :ids"),
                    {"ids": tuple(product_ids)},
                )
                s.commit()
