"""Integration tests for COD scenarios 8, 9, and 10 from the payment test matrix.

  8_cod_otp    — COD OTP gate: blocked without verified marker, succeeds + consumes marker with one.
  9_cod_limits — COD blocked below cod.min_order_total or above cod.max_order_total.
  10_split_cod — Split COD charges gateway the prepaid portion only; cod_balance = total - prepaid.

Runs inside the backend container:

    docker compose exec -T backend pytest tests/test_cod_and_split_checkout.py -v
"""
from __future__ import annotations

import time
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, ValidationError
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.integrations.payments import InitiateResponse
from app.models.order import Order, OrderStatus
from app.models.product import Product
from app.models.user import User
from app.schemas.address import AddressCreate
from app.schemas.order import OrderItemCreate
from app.schemas.payment import CheckoutRequest
from app.services.cod_otp_service import (
    CodOtpService,
    _normalize_phone,
    _verified_key,
    _VERIFIED_TTL_SECONDS,
)
from app.services.cod_service import CodService
from app.services.payment_service import PaymentService
from app.services.settings_service import SettingsService


# ---------------------------------------------------------------------------
# Shared helpers (file-local, not imported from other test files)
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _make_user(db: Session, *, is_admin: bool = False) -> User:
    u = User(
        email=f"codtest-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=is_admin,
    )
    db.add(u)
    db.flush()
    return u


def _make_product(
    db: Session,
    *,
    price: Decimal = Decimal("100.00"),
    stock: int = 10,
) -> Product:
    p = Product(
        sku=f"SKU-COD-{_uid()}",
        name=f"CodTestProd-{_uid()}",
        price=price,
        stock=stock,
    )
    db.add(p)
    db.flush()
    return p


def _addr_create(*, pincode: str = "400001") -> AddressCreate:
    return AddressCreate(
        full_name="COD Test Buyer",
        phone="9876543210",
        line1="1 Test Lane",
        city="Mumbai",
        state="Maharashtra",
        country="IN",
        pincode=pincode,
        label="home",
    )


def _cleanup(
    user_ids: list[int],
    product_ids: list[int],
    order_ids: list[int],
) -> None:
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


# ---------------------------------------------------------------------------
# Scenario 8: COD OTP gate
# ---------------------------------------------------------------------------

class TestCodOtpGate:
    """Scenario 8: COD OTP gate blocks without verified marker, succeeds + consumes with one."""

    _PHONE = "+919876543210"

    def test_cod_otp_blocked_without_verified_marker(self) -> None:
        """Expected: checkout raises ConflictError containing 'OTP' when
        cod.require_otp=true and no verified marker exists for the phone."""
        db = SessionLocal()
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        otp_svc: CodOtpService | None = None
        try:
            user = _make_user(db)
            prod = _make_product(db, price=Decimal("300.00"), stock=10)
            user_ids.append(user.id)
            product_ids.append(prod.id)
            db.commit()

            # Settings: COD on, OTP required, no bounds limit, mock shipping.
            SettingsService(db).set_many({
                "cod.enabled": "true",
                "cod.require_otp": "true",
                "cod.min_order_total": "0",
                "cod.max_order_total": "0",
                "shipping.provider": "mock",
                "shipping.warehouse.pincode": "560001",
                "shipping.warehouse.name": "HQ",
                "shipping.warehouse.address": "X",
            })
            db.commit()

            otp_svc = CodOtpService(db)
            # Ensure no stale verified marker from a previous run.
            otp_svc.consume(user_id=user.id, phone=self._PHONE)

            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address=_addr_create(pincode="400001"),
                payment_method="cod",
                customer_phone=self._PHONE,
            )
            svc = PaymentService(db)

            with pytest.raises(ConflictError) as exc_info:
                svc.checkout(user, req)

            assert "OTP" in exc_info.value.message

        finally:
            # Rollback any pending-flushed order so the settings commit
            # below does NOT accidentally persist a partial checkout row.
            db.rollback()
            SettingsService(db).set_many({
                "cod.require_otp": "true",
                "cod.min_order_total": "199",
                "cod.max_order_total": "5000",
                "shipping.provider": "none",
                "shipping.warehouse.pincode": "560001",
                "shipping.warehouse.name": "HQ",
                "shipping.warehouse.address": "X",
            })
            db.commit()
            if otp_svc is not None and user_ids:
                otp_svc.consume(user_id=user_ids[0], phone=self._PHONE)
            db.close()
            _cleanup(user_ids, product_ids, order_ids)

    def test_cod_otp_blocked_when_phone_omitted(self) -> None:
        """Expected: checkout raises ConflictError (not OTP-specific message) when
        cod.require_otp=true and customer_phone is not provided at all."""
        db = SessionLocal()
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        try:
            user = _make_user(db)
            prod = _make_product(db, price=Decimal("300.00"), stock=10)
            user_ids.append(user.id)
            product_ids.append(prod.id)
            db.commit()

            SettingsService(db).set_many({
                "cod.enabled": "true",
                "cod.require_otp": "true",
                "cod.min_order_total": "0",
                "cod.max_order_total": "0",
                "shipping.provider": "mock",
                "shipping.warehouse.pincode": "560001",
                "shipping.warehouse.name": "HQ",
                "shipping.warehouse.address": "X",
            })
            db.commit()

            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address=_addr_create(pincode="400001"),
                payment_method="cod",
                # customer_phone intentionally omitted
            )
            svc = PaymentService(db)

            with pytest.raises(ConflictError) as exc_info:
                svc.checkout(user, req)

            # When phone is missing the message mentions "phone number" but not "OTP"
            assert "phone" in exc_info.value.message.lower()

        finally:
            # Rollback any pending-flushed order before committing settings.
            db.rollback()
            SettingsService(db).set_many({
                "cod.require_otp": "true",
                "cod.min_order_total": "199",
                "cod.max_order_total": "5000",
                "shipping.provider": "none",
            })
            db.commit()
            db.close()
            _cleanup(user_ids, product_ids, order_ids)

    def test_cod_otp_succeeds_with_verified_marker_and_consumes_it(self) -> None:
        """Expected: checkout succeeds when a verified marker is planted, order
        is PAID, marker is consumed so a second checkout raises ConflictError."""
        db = SessionLocal()
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        otp_svc: CodOtpService | None = None
        try:
            user = _make_user(db)
            prod = _make_product(db, price=Decimal("300.00"), stock=10)
            user_ids.append(user.id)
            product_ids.append(prod.id)
            db.commit()

            SettingsService(db).set_many({
                "cod.enabled": "true",
                "cod.require_otp": "true",
                "cod.min_order_total": "0",
                "cod.max_order_total": "0",
                "shipping.provider": "mock",
                "shipping.warehouse.pincode": "560001",
                "shipping.warehouse.name": "HQ",
                "shipping.warehouse.address": "X",
            })
            db.commit()

            otp_svc = CodOtpService(db)

            # Plant a verified marker directly via the service's Redis client
            # (avoids Twilio; uses the same key-builder + normalizer the gate reads).
            clean_phone = _normalize_phone(self._PHONE)
            otp_svc.redis.setex(
                _verified_key(user.id, clean_phone),
                _VERIFIED_TTL_SECONDS,
                str(int(time.time())),
            )

            # Confirm the marker is visible before checkout.
            assert otp_svc.is_verified(user_id=user.id, phone=self._PHONE) is True

            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address=_addr_create(pincode="400001"),
                payment_method="cod",
                customer_phone=self._PHONE,
            )
            svc = PaymentService(db)
            order, mtid, redirect = svc.checkout(user, req)
            order_ids.append(order.id)

            # Order must be PAID immediately for full COD.
            assert order.payment_method == "cod"
            assert order.status.value == "paid"
            assert redirect.endswith(f"?mtid={mtid}")

            # Marker must have been consumed by the service.
            assert otp_svc.is_verified(user_id=user.id, phone=self._PHONE) is False

            # A second checkout with the same phone must fail (marker gone).
            req2 = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address=_addr_create(pincode="400001"),
                payment_method="cod",
                customer_phone=self._PHONE,
            )
            with pytest.raises(ConflictError) as exc_info2:
                svc.checkout(user, req2)

            assert "OTP" in exc_info2.value.message

        finally:
            # Rollback any second checkout's pending-flushed order.
            db.rollback()
            SettingsService(db).set_many({
                "cod.require_otp": "true",
                "cod.min_order_total": "199",
                "cod.max_order_total": "5000",
                "shipping.provider": "none",
                "shipping.warehouse.pincode": "560001",
                "shipping.warehouse.name": "HQ",
                "shipping.warehouse.address": "X",
            })
            db.commit()
            if otp_svc is not None and user_ids:
                otp_svc.consume(user_id=user_ids[0], phone=self._PHONE)
            db.close()
            _cleanup(user_ids, product_ids, order_ids)


# ---------------------------------------------------------------------------
# Scenario 9: COD subtotal bounds
# ---------------------------------------------------------------------------

class TestCodSubtotalBounds:
    """Scenario 9: COD blocked when subtotal is below cod.min_order_total or
    above cod.max_order_total."""

    def test_cod_limits_below_minimum(self) -> None:
        """Expected: checkout raises ConflictError containing 'or more' and '500'
        when subtotal (100) is below cod.min_order_total (500)."""
        db = SessionLocal()
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        try:
            user = _make_user(db)
            # price=100, qty=1 -> subtotal=100 < min=500
            prod = _make_product(db, price=Decimal("100.00"), stock=10)
            user_ids.append(user.id)
            product_ids.append(prod.id)
            db.commit()

            SettingsService(db).set_many({
                "cod.enabled": "true",
                "cod.require_otp": "false",
                "cod.min_order_total": "500",
                "cod.max_order_total": "0",
                "shipping.provider": "mock",
                "shipping.warehouse.pincode": "560001",
                "shipping.warehouse.name": "HQ",
                "shipping.warehouse.address": "X",
            })
            db.commit()

            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address=_addr_create(pincode="400001"),
                payment_method="cod",
            )
            svc = PaymentService(db)

            with pytest.raises(ConflictError) as exc_info:
                svc.checkout(user, req)

            msg = exc_info.value.message
            assert "COD only available" in msg
            assert "or more" in msg
            assert "500" in msg

        finally:
            # Rollback the pending-flushed order before committing settings.
            db.rollback()
            SettingsService(db).set_many({
                "cod.require_otp": "true",
                "cod.min_order_total": "199",
                "cod.max_order_total": "5000",
                "shipping.provider": "none",
            })
            db.commit()
            db.close()
            _cleanup(user_ids, product_ids, order_ids)

    def test_cod_limits_above_maximum(self) -> None:
        """Expected: checkout raises ConflictError containing 'up to' and '1000'
        when subtotal (1200) exceeds cod.max_order_total (1000)."""
        db = SessionLocal()
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        try:
            user = _make_user(db)
            # price=600, qty=2 -> subtotal=1200 > max=1000
            prod = _make_product(db, price=Decimal("600.00"), stock=10)
            user_ids.append(user.id)
            product_ids.append(prod.id)
            db.commit()

            SettingsService(db).set_many({
                "cod.enabled": "true",
                "cod.require_otp": "false",
                "cod.min_order_total": "0",
                "cod.max_order_total": "1000",
                "shipping.provider": "mock",
                "shipping.warehouse.pincode": "560001",
                "shipping.warehouse.name": "HQ",
                "shipping.warehouse.address": "X",
            })
            db.commit()

            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=2)],
                address=_addr_create(pincode="400001"),
                payment_method="cod",
            )
            svc = PaymentService(db)

            with pytest.raises(ConflictError) as exc_info:
                svc.checkout(user, req)

            msg = exc_info.value.message
            assert "COD only available" in msg
            assert "up to" in msg
            assert "1000" in msg

        finally:
            # Rollback the pending-flushed order before committing settings.
            db.rollback()
            SettingsService(db).set_many({
                "cod.require_otp": "true",
                "cod.min_order_total": "199",
                "cod.max_order_total": "5000",
                "shipping.provider": "none",
            })
            db.commit()
            db.close()
            _cleanup(user_ids, product_ids, order_ids)

    def test_cod_limits_direct_check_availability_below_min(self) -> None:
        """Expected: CodService.check_availability returns available=False with
        an 'or more' reason when subtotal is below the minimum bound.
        This exercises the gate directly without building a full order."""
        db = SessionLocal()
        user_ids: list[int] = []
        product_ids: list[int] = []
        try:
            user = _make_user(db)
            prod = _make_product(db, price=Decimal("100.00"), stock=10)
            user_ids.append(user.id)
            product_ids.append(prod.id)
            db.commit()

            SettingsService(db).set_many({
                "cod.enabled": "true",
                "cod.require_otp": "false",
                "cod.min_order_total": "500",
                "cod.max_order_total": "0",
                "shipping.provider": "mock",
                "shipping.warehouse.pincode": "560001",
                "shipping.warehouse.name": "HQ",
                "shipping.warehouse.address": "X",
            })
            db.commit()

            result = CodService(db).check_availability(
                user=user,
                cart_items=[(prod.id, 1)],
                destination_pincode="400001",
            )

            assert result.available is False
            assert any("or more" in r for r in result.reasons)
            assert any("500" in r for r in result.reasons)

        finally:
            SettingsService(db).set_many({
                "cod.require_otp": "true",
                "cod.min_order_total": "199",
                "cod.max_order_total": "5000",
                "shipping.provider": "none",
            })
            db.commit()
            db.close()
            _cleanup(user_ids, product_ids, [])

    def test_cod_limits_direct_check_availability_above_max(self) -> None:
        """Expected: CodService.check_availability returns available=False with
        an 'up to' reason when subtotal exceeds the maximum bound."""
        db = SessionLocal()
        user_ids: list[int] = []
        product_ids: list[int] = []
        try:
            user = _make_user(db)
            prod = _make_product(db, price=Decimal("600.00"), stock=10)
            user_ids.append(user.id)
            product_ids.append(prod.id)
            db.commit()

            SettingsService(db).set_many({
                "cod.enabled": "true",
                "cod.require_otp": "false",
                "cod.min_order_total": "0",
                "cod.max_order_total": "1000",
                "shipping.provider": "mock",
                "shipping.warehouse.pincode": "560001",
                "shipping.warehouse.name": "HQ",
                "shipping.warehouse.address": "X",
            })
            db.commit()

            result = CodService(db).check_availability(
                user=user,
                cart_items=[(prod.id, 2)],
                destination_pincode="400001",
            )

            assert result.available is False
            assert any("up to" in r for r in result.reasons)
            assert any("1000" in r for r in result.reasons)

        finally:
            SettingsService(db).set_many({
                "cod.require_otp": "true",
                "cod.min_order_total": "199",
                "cod.max_order_total": "5000",
                "shipping.provider": "none",
            })
            db.commit()
            db.close()
            _cleanup(user_ids, product_ids, [])


# ---------------------------------------------------------------------------
# Scenario 10: Split COD
# ---------------------------------------------------------------------------

class TestSplitCod:
    """Scenario 10: Split COD charges the prepaid portion only via the gateway;
    cod_balance = total - prepaid."""

    def test_split_cod_gateway_receives_prepaid_only(self) -> None:
        """Expected: provider.initiate is called with amount_minor equal to
        (total_amount - cod_balance) * 100; order.status is pending; order
        has correct cod_balance and cod_surcharge_amount."""
        db = SessionLocal()
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        try:
            user = _make_user(db)
            prod = _make_product(db, price=Decimal("300.00"), stock=10)
            user_ids.append(user.id)
            product_ids.append(prod.id)
            db.commit()

            SettingsService(db).set_many({
                "cod.enabled": "true",
                "cod.split_enabled": "true",
                "cod.split_prepaid_amount": "100",
                "cod.flat_surcharge": "40",
                "cod.min_order_total": "0",
                "cod.max_order_total": "0",
                "cod.require_otp": "false",
                "shipping.provider": "mock",
                "shipping.warehouse.pincode": "560001",
                "shipping.warehouse.name": "HQ",
                "shipping.warehouse.address": "X",
            })
            db.commit()

            mock_provider = MagicMock()
            mock_provider.name = "mock"
            mock_provider.initiate.return_value = InitiateResponse(
                redirect_url="http://x/mock",
                provider_transaction_id="PVR123",
            )

            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address=_addr_create(pincode="400001"),
                payment_method="split_cod",
                gateway_code="mock",
            )

            with patch(
                "app.services.payment_service.get_payment_provider",
                return_value=mock_provider,
            ):
                svc = PaymentService(db)
                order, mtid, redirect = svc.checkout(user, req)

            order_ids.append(order.id)

            # Order stays PENDING for split_cod (gateway webhook moves it to PAID).
            assert order.payment_method == "split_cod"
            assert order.status.value == "pending"

            # COD surcharge must be applied.
            assert Decimal(order.cod_surcharge_amount) == Decimal("40.00")

            # Gateway must be invoked.
            assert mock_provider.initiate.called

            # The initiate call must pass the PREPAID portion only, not the full total.
            call_arg = mock_provider.initiate.call_args.args[0]
            total = Decimal(order.total_amount)
            balance = Decimal(order.cod_balance)
            expected_prepaid_minor = int((total - balance) * 100)

            assert call_arg.amount_minor == expected_prepaid_minor

            # Prepaid is 100 (from settings) so cod_balance = total - 100.
            assert balance == total - Decimal("100.00")

            # Sanity: provider transaction id was stored.
            assert order.payment_provider_ref == "PVR123"

        finally:
            SettingsService(db).set_many({
                "cod.split_enabled": "false",
                "cod.split_prepaid_amount": "100",
                "cod.min_order_total": "199",
                "cod.max_order_total": "5000",
                "cod.require_otp": "true",
                "shipping.provider": "none",
                "shipping.warehouse.pincode": "560001",
                "shipping.warehouse.name": "HQ",
                "shipping.warehouse.address": "X",
            })
            db.commit()
            db.close()
            _cleanup(user_ids, product_ids, order_ids)

    def test_split_cod_degenerate_raises_validation_error(self) -> None:
        """Expected: checkout raises ValidationError when cod.split_prepaid_amount=0
        (degenerate case — prepaid_minor <= 0 triggers the guard in payment_service)."""
        db = SessionLocal()
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        try:
            user = _make_user(db)
            prod = _make_product(db, price=Decimal("300.00"), stock=10)
            user_ids.append(user.id)
            product_ids.append(prod.id)
            db.commit()

            SettingsService(db).set_many({
                "cod.enabled": "true",
                "cod.split_enabled": "true",
                # 0 means split_prepaid_for returns 0 -> degenerate in _build_order
                # -> cod_balance = total -> prepaid_minor = 0 -> ValidationError
                "cod.split_prepaid_amount": "0",
                "cod.flat_surcharge": "40",
                "cod.min_order_total": "0",
                "cod.max_order_total": "0",
                "cod.require_otp": "false",
                "shipping.provider": "mock",
                "shipping.warehouse.pincode": "560001",
                "shipping.warehouse.name": "HQ",
                "shipping.warehouse.address": "X",
            })
            db.commit()

            mock_provider = MagicMock()
            mock_provider.name = "mock"
            mock_provider.initiate.return_value = InitiateResponse(
                redirect_url="http://x/mock",
                provider_transaction_id="PVR456",
            )

            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address=_addr_create(pincode="400001"),
                payment_method="split_cod",
                gateway_code="mock",
            )

            with patch(
                "app.services.payment_service.get_payment_provider",
                return_value=mock_provider,
            ):
                svc = PaymentService(db)
                with pytest.raises(ValidationError) as exc_info:
                    svc.checkout(user, req)

            assert "Split COD prepaid portion is no longer valid" in exc_info.value.message

        finally:
            SettingsService(db).set_many({
                "cod.split_enabled": "false",
                "cod.split_prepaid_amount": "100",
                "cod.min_order_total": "199",
                "cod.max_order_total": "5000",
                "cod.require_otp": "true",
                "shipping.provider": "none",
            })
            db.commit()
            db.close()
            _cleanup(user_ids, product_ids, order_ids)
