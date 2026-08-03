"""Production payment-config guard.

Covers the two enforcement points added after the 2026-08-03 strand incident
(a captured Razorpay payment whose payment link carried the dev default
``http://localhost:5173/payments/return`` as its callback):

  * ``payment_config_problems`` / ``payment_return_url_is_dev_shaped`` —
    pure config checks, only ever active when ENVIRONMENT=production.
  * ``PaymentService.checkout`` — refuses gateway (non-COD) checkouts while
    production carries a dev-shaped return URL, BEFORE any order rows exist.

The suite runs with ENVIRONMENT=test/development (the conftest guard enforces
that), so production is simulated by patching the settings singleton.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.core.config import (
    payment_config_problems,
    payment_return_url_is_dev_shaped,
    settings,
)
from app.core.exceptions import ValidationError
from app.db.session import SessionLocal
from app.schemas.payment import CheckoutRequest
from app.services.payment_service import PaymentService
from tests.test_payment_methods import _addr_create, _make_product, _make_user


# ---------------------------------------------------------------------------
# Config checks
# ---------------------------------------------------------------------------

class TestPaymentConfigChecks:

    def test_never_flags_outside_production(self) -> None:
        """Dev/test/ci run on localhost by design — zero problems reported."""
        assert settings.ENVIRONMENT != "production"  # conftest guarantees this
        assert payment_return_url_is_dev_shaped() is False
        assert payment_config_problems() == []

    def test_flags_dev_return_url_and_blank_token_in_production(self) -> None:
        with (
            patch.object(settings, "ENVIRONMENT", "production"),
            patch.object(
                settings, "PAYMENT_RETURN_URL", "http://localhost:5173/payments/return"
            ),
            patch.object(settings, "PAYMENT_RECONCILE_TOKEN", ""),
        ):
            assert payment_return_url_is_dev_shaped() is True
            problems = payment_config_problems()
            assert len(problems) == 2
            assert any("PAYMENT_RETURN_URL" in p for p in problems)
            assert any("PAYMENT_RECONCILE_TOKEN" in p for p in problems)

    def test_clean_production_config_reports_nothing(self) -> None:
        with (
            patch.object(settings, "ENVIRONMENT", "production"),
            patch.object(
                settings,
                "PAYMENT_RETURN_URL",
                "https://shopwellvia.in/payments/return",
            ),
            patch.object(settings, "PAYMENT_RECONCILE_TOKEN", "a-real-token"),
        ):
            assert payment_return_url_is_dev_shaped() is False
            assert payment_config_problems() == []

    @pytest.mark.parametrize("marker", ["localhost", "127.0.0.1", "0.0.0.0"])
    def test_each_dev_host_marker_is_caught(self, marker: str) -> None:
        with (
            patch.object(settings, "ENVIRONMENT", "production"),
            patch.object(
                settings, "PAYMENT_RETURN_URL", f"http://{marker}:5173/payments/return"
            ),
        ):
            assert payment_return_url_is_dev_shaped() is True


# ---------------------------------------------------------------------------
# Checkout enforcement
# ---------------------------------------------------------------------------

class TestCheckoutGuard:

    def test_gateway_checkout_refused_and_no_rows_created(self) -> None:
        """Prepaid checkout in mis-configured production must fail BEFORE any
        order exists — the guard's whole point is refusing while it's cheap."""
        db = SessionLocal()
        try:
            user = _make_user(db)
            product = _make_product(db)
            db.commit()

            from app.models.order import Order

            orders_before = db.query(Order).filter(Order.user_id == user.id).count()

            req = CheckoutRequest(
                items=[{"product_id": product.id, "quantity": 1}],
                payment_method="prepaid",
                address=_addr_create(),
                save_address=False,
            )
            with (
                patch.object(settings, "ENVIRONMENT", "production"),
                patch.object(
                    settings,
                    "PAYMENT_RETURN_URL",
                    "http://localhost:5173/payments/return",
                ),
            ):
                with pytest.raises(ValidationError) as exc:
                    PaymentService(db).checkout(user, req)
            assert "temporarily unavailable" in str(exc.value.message)

            db.rollback()
            orders_after = db.query(Order).filter(Order.user_id == user.id).count()
            assert orders_after == orders_before, (
                "guard must fire before any order row is created"
            )
        finally:
            db.rollback()
            with SessionLocal() as s:
                s.execute(text("DELETE FROM products WHERE id = :pid"), {"pid": product.id})
                s.execute(text("DELETE FROM customers WHERE user_id = :uid"), {"uid": user.id})
                s.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user.id})
                s.commit()
            db.close()

    def test_guard_inactive_outside_production(self) -> None:
        """The same dev-shaped URL outside production must not trip the guard —
        localhost is the CORRECT return origin for dev/test/ci."""
        assert settings.ENVIRONMENT != "production"
        assert payment_return_url_is_dev_shaped() is False
