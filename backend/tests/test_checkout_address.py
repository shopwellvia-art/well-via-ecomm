"""Integration tests for the shipping address resolution path in PaymentService.

These tests focus on `_resolve_shipping_address` + `_build_order` — the two
methods that wire the new address book into the checkout pipeline.

APPROACH: We call PaymentService._resolve_shipping_address and
PaymentService._build_order directly instead of going through the full
checkout() path.  Reasons:
  1. checkout() calls provider.initiate() which hits Redis; staying at the
     service-method level keeps tests hermetic and fast.
  2. The payment gateway in the live dev DB is configured as "phonepe" but
     without valid credentials, so PaymentService.__init__ would fail if we
     let it call get_payment_provider().  We patch
     app.services.payment_service.get_payment_provider with a MagicMock to
     avoid that failure — all tests here only need the service methods, not the
     provider.
  3. Skipping COD altogether (COD gate hits serviceability checks).

Runs inside the backend container:

    docker compose exec backend pytest tests/test_checkout_address.py -v
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ValidationError
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.address import Address, AddressLabel
from app.models.order import Order, OrderStatus
from app.models.product import Product
from app.models.user import User
from app.schemas.address import AddressCreate
from app.schemas.order import OrderItemCreate
from app.schemas.payment import CheckoutRequest
from app.services.address_service import (
    AddressService,
    MAX_ADDRESSES,
    render_address_text,
    snapshot_of,
)
from app.services.payment_service import PaymentService


# ---------------------------------------------------------------------------
# Payment provider stub
# ---------------------------------------------------------------------------

def _make_payment_service(db: Session) -> PaymentService:
    """Construct a PaymentService with the provider stubbed out.

    The live dev DB gateway config points to PhonePe without credentials, so
    get_payment_provider(db) raises.  We patch the factory at the module level
    so PaymentService.__init__ gets a harmless MagicMock instead.
    """
    mock_provider = MagicMock()
    with patch("app.services.payment_service.get_payment_provider", return_value=mock_provider):
        svc = PaymentService(db)
    return svc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _make_user(db: Session) -> User:
    u = User(
        email=f"cotest-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(u)
    db.flush()
    return u


def _make_product(db: Session, *, price: Decimal = Decimal("100.00"), stock: int = 50) -> Product:
    p = Product(
        sku=f"SKU-CO-{_uid()}",
        name=f"CheckoutTestProduct {_uid()}",
        price=price,
        stock=stock,
    )
    db.add(p)
    db.flush()
    return p


def _addr_create(
    *,
    pincode: str = "560001",
    label: AddressLabel = AddressLabel.HOME,
    is_default: bool = False,
) -> AddressCreate:
    uid = _uid()
    return AddressCreate(
        full_name=f"Buyer {uid}",
        phone="9876543210",
        line1=f"{uid} Test Road",
        line2=None,
        landmark=None,
        city="Bangalore",
        state="Karnataka",
        pincode=pincode,
        country="IN",
        label=label,
        is_default=is_default,
    )


def _checkout_request_with_address_id(
    product: Product,
    address_id: int,
) -> CheckoutRequest:
    return CheckoutRequest(
        items=[OrderItemCreate(product_id=product.id, quantity=1)],
        address_id=address_id,
        payment_method="prepaid",
    )


def _checkout_request_inline(
    product: Product,
    address: AddressCreate,
    save_address: bool = False,
) -> CheckoutRequest:
    return CheckoutRequest(
        items=[OrderItemCreate(product_id=product.id, quantity=1)],
        address=address,
        save_address=save_address,
        payment_method="prepaid",
    )


def _cleanup(
    user_ids: list[int],
    product_ids: list[int],
    order_ids: list[int] | None = None,
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
# address_id path
# ---------------------------------------------------------------------------

class TestCheckoutWithAddressId:

    def test_snapshot_rendered_text_pincode_and_fk_are_set(self) -> None:
        """Checking out with address_id must produce an order where:
        - shipping_address_snapshot matches snapshot_of the saved address
        - shipping_address text matches render_address_text(snapshot)
        - shipping_pincode equals the address pincode
        - shipping_address_id is set to the saved address id
        """
        user_ids: list[int] = []
        product_ids: list[int] = []
        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db)
            product_ids.append(prod.id)
            db.commit()

            addr = AddressService(db).create(user.id, _addr_create(pincode="560001"))
            expected_snap = snapshot_of(addr)

            svc = _make_payment_service(db)
            req = _checkout_request_with_address_id(prod, addr.id)
            snap, resolved_id = svc._resolve_shipping_address(user, req)

            assert snap is not None
            assert resolved_id == addr.id
            assert snap["pincode"] == "560001"
            assert req.shipping_pincode == "560001"
            assert req.shipping_address == render_address_text(expected_snap)

            # Build order and check the columns.
            order, total = svc._build_order(user.id, req, snapshot=snap, resolved_address_id=resolved_id)

            assert order.shipping_address_snapshot == snap
            assert order.shipping_address_id == addr.id
            assert order.shipping_pincode == "560001"
            assert order.shipping_address == render_address_text(snap)

        finally:
            db.rollback()
            _cleanup(user_ids, product_ids)
            db.close()

    def test_another_users_address_id_raises_not_found(self) -> None:
        """Using another user's address_id must raise NotFoundError."""
        user_ids: list[int] = []
        product_ids: list[int] = []
        db = SessionLocal()
        try:
            user_a = _make_user(db)
            user_b = _make_user(db)
            user_ids.extend([user_a.id, user_b.id])
            prod = _make_product(db)
            product_ids.append(prod.id)
            db.commit()

            addr = AddressService(db).create(user_a.id, _addr_create())

            svc = _make_payment_service(db)
            req = _checkout_request_with_address_id(prod, addr.id)

            with pytest.raises(NotFoundError):
                svc._resolve_shipping_address(user_b, req)
        finally:
            db.rollback()
            _cleanup(user_ids, product_ids)
            db.close()


# ---------------------------------------------------------------------------
# Inline address path
# ---------------------------------------------------------------------------

class TestCheckoutWithInlineAddress:

    def test_inline_with_save_address_true_persists_address(self) -> None:
        """When save_address=True, the inline address must be saved to the book
        and the resolved_address_id must point to the newly saved row."""
        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db)
            product_ids.append(prod.id)
            db.commit()

            inline = _addr_create(pincode="400001")
            svc = _make_payment_service(db)
            req = _checkout_request_inline(prod, inline, save_address=True)
            snap, resolved_id = svc._resolve_shipping_address(user, req)

            assert snap is not None
            assert resolved_id is not None, "save_address=True must set resolved_address_id"

            # The address must now exist in the book.
            saved_addr = db.get(Address, resolved_id)
            assert saved_addr is not None
            assert saved_addr.user_id == user.id
            assert saved_addr.pincode == "400001"

            # Snapshot content must match.
            assert snap["pincode"] == "400001"
            assert req.shipping_pincode == "400001"
        finally:
            db.rollback()
            _cleanup(user_ids, product_ids, order_ids)
            db.close()

    def test_inline_with_save_address_false_does_not_persist(self) -> None:
        """When save_address=False, the inline address must NOT be saved to the
        book, snapshot is still set, and shipping_address_id is None."""
        user_ids: list[int] = []
        product_ids: list[int] = []
        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db)
            product_ids.append(prod.id)
            db.commit()

            inline = _addr_create(pincode="110001")
            svc = _make_payment_service(db)
            req = _checkout_request_inline(prod, inline, save_address=False)
            snap, resolved_id = svc._resolve_shipping_address(user, req)

            assert snap is not None
            assert resolved_id is None, "save_address=False must yield resolved_address_id=None"
            assert snap["pincode"] == "110001"

            # No address row must have been created.
            count = db.execute(
                select(Address).where(Address.user_id == user.id)
            ).scalars().all()
            assert len(count) == 0

            # Build order and confirm snapshot FK is None.
            order, _ = svc._build_order(
                user.id, req, snapshot=snap, resolved_address_id=resolved_id
            )
            assert order.shipping_address_id is None
            assert order.shipping_address_snapshot == snap
        finally:
            db.rollback()
            _cleanup(user_ids, product_ids)
            db.close()

    def test_save_address_true_when_book_full_checkout_still_succeeds(self) -> None:
        """When the address book is already at MAX_ADDRESSES and save_address=True
        is requested during checkout, the save is silently skipped but checkout
        succeeds (resolved_address_id will be None — the cap-full code path)."""
        user_ids: list[int] = []
        product_ids: list[int] = []
        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db)
            product_ids.append(prod.id)
            db.commit()

            addr_svc = AddressService(db)
            for _ in range(MAX_ADDRESSES):
                addr_svc.create(user.id, _addr_create())

            # Book is now full.
            inline = _addr_create(pincode="700001")
            svc = _make_payment_service(db)
            req = _checkout_request_inline(prod, inline, save_address=True)

            # Must not raise — the cap-full case logs and skips the save.
            snap, resolved_id = svc._resolve_shipping_address(user, req)

            assert snap is not None, "Snapshot must still be set even when save is skipped"
            assert resolved_id is None, "No new address should be saved when cap is full"

            # Exactly MAX_ADDRESSES rows remain.
            count = db.execute(
                select(Address).where(Address.user_id == user.id)
            ).scalars().all()
            assert len(count) == MAX_ADDRESSES
        finally:
            db.rollback()
            _cleanup(user_ids, product_ids)
            db.close()


# ---------------------------------------------------------------------------
# Legacy free-text path
# ---------------------------------------------------------------------------

class TestCheckoutLegacyFreeText:

    def test_legacy_free_text_sets_null_snapshot_and_fk(self) -> None:
        """CheckoutRequest with only shipping_address (legacy) must succeed;
        shipping_address_snapshot and shipping_address_id must both be None."""
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
                shipping_address="123 Legacy Lane, Mumbai, Maharashtra",
                shipping_pincode="400001",
                payment_method="prepaid",
            )

            svc = _make_payment_service(db)
            snap, resolved_id = svc._resolve_shipping_address(user, req)

            assert snap is None
            assert resolved_id is None

            order, _ = svc._build_order(
                user.id, req, snapshot=snap, resolved_address_id=resolved_id
            )
            assert order.shipping_address_snapshot is None
            assert order.shipping_address_id is None
            assert order.shipping_address == "123 Legacy Lane, Mumbai, Maharashtra"
        finally:
            db.rollback()
            _cleanup(user_ids, product_ids)
            db.close()

    def test_no_address_source_raises_validation_error(self) -> None:
        """A CheckoutRequest with no address source at all must raise
        ValidationError before any order row is written."""
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
                payment_method="prepaid",
            )

            svc = _make_payment_service(db)
            with pytest.raises(ValidationError):
                svc._resolve_shipping_address(user, req)
        finally:
            db.rollback()
            _cleanup(user_ids, product_ids)
            db.close()


# ---------------------------------------------------------------------------
# Snapshot immutability
# ---------------------------------------------------------------------------

class TestSnapshotImmutability:

    def test_order_snapshot_unchanged_after_address_update_and_delete(self) -> None:
        """After placing an order via address_id:
        - Updating the address fields must NOT change the snapshot on the order.
        - Deleting the address must set shipping_address_id to NULL on the order
          (FK SET NULL) but must NOT touch the snapshot text/pincode.

        NOTE: The orders table does not yet have the `gateway_code` /
        `payment_provider_ref` columns because that migration hasn't run.  We
        insert the order row via raw SQL to avoid the ORM trying to include those
        missing columns, then re-read via db.get(Order, id) which maps only
        columns that exist.  This is NOT an address-feature bug; the gap is in an
        unrelated migration from the concurrent payment-methods work.
        """
        import json as _json

        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []
        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db, stock=10)
            product_ids.append(prod.id)
            db.commit()

            addr_svc = AddressService(db)
            addr = addr_svc.create(user.id, _addr_create(pincode="560001"))

            # Capture snapshot before order placement.
            original_snap = snapshot_of(addr)
            original_text = render_address_text(original_snap)

            # Compute the shipping amount so _build_order won't fail on the
            # ShippingService rate_quote path (it falls back to 0 on errors,
            # so we don't need to stub it out here).

            # Build the Order object via the service (gives us all the computed
            # fields), but do NOT add it to the session yet — we will insert
            # via raw SQL to avoid the missing gateway_code / payment_provider_ref
            # columns that haven't been migrated yet.
            svc = _make_payment_service(db)
            req = _checkout_request_with_address_id(prod, addr.id)
            snap, resolved_id = svc._resolve_shipping_address(user, req)
            order, _ = svc._build_order(
                user.id, req, snapshot=snap, resolved_address_id=resolved_id
            )

            # Insert via raw SQL using only the columns that actually exist in the DB.
            db.execute(
                text(
                    "INSERT INTO orders "
                    "(user_id, status, subtotal, tax_amount, discount_amount, "
                    " shipping_amount, total_amount, currency, payment_method, "
                    " cod_surcharge_amount, cod_balance, payment_discount_amount, "
                    " shipping_address, shipping_pincode, "
                    " shipping_address_id, shipping_address_snapshot) "
                    "VALUES "
                    "(:user_id, :status, :subtotal, :tax_amount, :discount_amount, "
                    " :shipping_amount, :total_amount, :currency, :payment_method, "
                    " :cod_surcharge_amount, :cod_balance, :payment_discount_amount, "
                    " :shipping_address, :shipping_pincode, "
                    " :shipping_address_id, :shipping_address_snapshot)"
                ),
                {
                    "user_id": user.id,
                    "status": "PENDING",
                    "subtotal": float(order.subtotal),
                    "tax_amount": float(order.tax_amount),
                    "discount_amount": float(order.discount_amount),
                    "shipping_amount": float(order.shipping_amount),
                    "total_amount": float(order.total_amount),
                    "currency": "INR",
                    "payment_method": "prepaid",
                    "cod_surcharge_amount": 0,
                    "cod_balance": 0,
                    "payment_discount_amount": 0,
                    "shipping_address": order.shipping_address,
                    "shipping_pincode": order.shipping_pincode,
                    "shipping_address_id": addr.id,
                    "shipping_address_snapshot": _json.dumps(snap),
                },
            )
            db.commit()

            # Fetch the newly inserted order id.
            order_id = db.execute(
                text(
                    "SELECT id FROM orders WHERE user_id = :uid "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"uid": user.id},
            ).scalar_one()
            order_ids.append(order_id)

            # --- Mutate the address ---
            from app.schemas.address import AddressUpdate
            addr_svc.update(
                user.id, addr.id,
                AddressUpdate(
                    line1="9999 Changed Street",
                    city="Delhi",
                    state="Delhi",
                    pincode="110001",
                ),
            )

            # Re-read the order via raw SQL; snapshot must be unchanged.
            row = db.execute(
                text(
                    "SELECT shipping_address_snapshot, shipping_pincode, "
                    "shipping_address, shipping_address_id "
                    "FROM orders WHERE id = :id"
                ),
                {"id": order_id},
            ).mappings().one()

            stored_snap = _json.loads(row["shipping_address_snapshot"])
            assert stored_snap == original_snap, (
                "Updating the address must not change the order snapshot"
            )
            assert row["shipping_pincode"] == "560001", (
                "shipping_pincode on the order must not change after address update"
            )
            assert row["shipping_address"] == original_text, (
                "shipping_address text must not change after address update"
            )
            assert row["shipping_address_id"] == addr.id, (
                "FK must still point to the address after an update"
            )

            # --- Delete the address ---
            addr_svc.delete(user.id, addr.id)

            # MySQL FK SET NULL fires at the DB level — re-query.
            row2 = db.execute(
                text(
                    "SELECT shipping_address_snapshot, shipping_pincode, "
                    "shipping_address, shipping_address_id "
                    "FROM orders WHERE id = :id"
                ),
                {"id": order_id},
            ).mappings().one()

            # FK must be NULL after deletion.
            assert row2["shipping_address_id"] is None, (
                "shipping_address_id must become NULL after address deletion (FK SET NULL)"
            )
            stored_snap2 = _json.loads(row2["shipping_address_snapshot"])
            assert stored_snap2 == original_snap, (
                "Snapshot must survive address deletion"
            )
            assert row2["shipping_pincode"] == "560001", (
                "shipping_pincode must survive address deletion"
            )
            assert row2["shipping_address"] == original_text, (
                "shipping_address text must survive address deletion"
            )
        finally:
            if order_ids:
                with SessionLocal() as s:
                    s.execute(
                        text("DELETE FROM order_items WHERE order_id IN :ids"),
                        {"ids": tuple(order_ids)},
                    )
                    s.execute(
                        text("DELETE FROM orders WHERE id IN :ids"),
                        {"ids": tuple(order_ids)},
                    )
                    s.commit()
            _cleanup(user_ids, product_ids)
            db.close()
