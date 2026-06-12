"""Integration tests for the billing address resolution path in PaymentService.

Mirrors the style of test_checkout_address.py — all tests are hermetic: they
call _resolve_billing_address and _build_order directly, patch
get_payment_provider to avoid needing a live gateway, use unique-suffix
fixtures to avoid cross-test collisions, and clean up after themselves with
raw-SQL deletes.

Test matrix
-----------
1. Saved shipping address_id + no billing → billing copies shipping (snapshot + id).
2. Inline (unsaved) shipping address + no billing → billing copies shipping; both ids None.
3. Explicit billing_address_id (different saved address) → billing matches that
   address; shipping columns unaffected.
4. billing_address_id belonging to another user → NotFoundError.
5. Inline billing_address → snapshot stored; billing_id None; address count unchanged.
6a. Legacy free-text shipping + no billing → both billing columns None.
6b. Legacy free-text shipping + explicit billing_address_id → billing populated.
7. Snapshot frozen: update billing address → order snapshot unchanged; delete
   billing address → billing_address_id NULL at DB level, snapshot intact.
8. Copy independence: mutating order.shipping_address_snapshot must not affect
   order.billing_address_snapshot.

Runs inside the backend container:

    docker compose exec backend pytest tests/test_checkout_billing.py -v
"""
from __future__ import annotations

import json as _json
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.address import Address, AddressLabel
from app.models.order import Order, OrderStatus
from app.models.product import Product
from app.models.user import User
from app.schemas.address import AddressCreate, AddressUpdate
from app.schemas.order import OrderItemCreate
from app.schemas.payment import CheckoutRequest
from app.services.address_service import AddressService, snapshot_of
from app.services.payment_service import PaymentService


# ---------------------------------------------------------------------------
# Provider stub
# ---------------------------------------------------------------------------

def _make_payment_service(db: Session) -> PaymentService:
    """Construct a PaymentService with the gateway provider stubbed out."""
    mock_provider = MagicMock()
    with patch("app.services.payment_service.get_payment_provider", return_value=mock_provider):
        svc = PaymentService(db)
    return svc


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _make_user(db: Session) -> User:
    u = User(
        email=f"billing-test-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(u)
    db.flush()
    return u


def _make_product(db: Session, *, price: Decimal = Decimal("100.00"), stock: int = 50) -> Product:
    p = Product(
        sku=f"SKU-BT-{_uid()}",
        name=f"BillingTestProduct {_uid()}",
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


def _checkout_with_address_id(product: Product, address_id: int) -> CheckoutRequest:
    return CheckoutRequest(
        items=[OrderItemCreate(product_id=product.id, quantity=1)],
        address_id=address_id,
        payment_method="prepaid",
    )


def _checkout_inline(product: Product, address: AddressCreate) -> CheckoutRequest:
    return CheckoutRequest(
        items=[OrderItemCreate(product_id=product.id, quantity=1)],
        address=address,
        save_address=False,
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


def _insert_order_raw(db: Session, *, user_id: int, order: Order,
                      shipping_address_id: int | None,
                      shipping_snapshot: dict,
                      billing_address_id: int | None,
                      billing_snapshot: dict | None) -> int:
    """Insert an order row using only columns that exist in the DB.

    Uses raw SQL so we bypass any ORM mismatch with columns added by later
    migrations that haven't run in the test DB.  Returns the new order id.
    """
    db.execute(
        text(
            "INSERT INTO orders "
            "(user_id, status, subtotal, tax_amount, discount_amount, "
            " shipping_amount, total_amount, currency, payment_method, "
            " cod_surcharge_amount, cod_balance, payment_discount_amount, "
            " shipping_address, shipping_pincode, "
            " shipping_address_id, shipping_address_snapshot, "
            " billing_address_id, billing_address_snapshot) "
            "VALUES "
            "(:user_id, :status, :subtotal, :tax_amount, :discount_amount, "
            " :shipping_amount, :total_amount, :currency, :payment_method, "
            " :cod_surcharge_amount, :cod_balance, :payment_discount_amount, "
            " :shipping_address, :shipping_pincode, "
            " :shipping_address_id, :shipping_address_snapshot, "
            " :billing_address_id, :billing_address_snapshot)"
        ),
        {
            "user_id": user_id,
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
            "shipping_address_id": shipping_address_id,
            "shipping_address_snapshot": _json.dumps(shipping_snapshot),
            "billing_address_id": billing_address_id,
            "billing_address_snapshot": (
                _json.dumps(billing_snapshot) if billing_snapshot is not None else None
            ),
        },
    )
    db.commit()
    return db.execute(
        text("SELECT id FROM orders WHERE user_id = :uid ORDER BY id DESC LIMIT 1"),
        {"uid": user_id},
    ).scalar_one()


# ---------------------------------------------------------------------------
# 1. Saved shipping + no billing → billing copies shipping
# ---------------------------------------------------------------------------

class TestBillingDefaultsToShipping:

    def test_saved_shipping_no_billing_billing_equals_shipping(self) -> None:
        """When checkout uses a saved address_id and no billing fields are sent,
        billing_address_snapshot must equal shipping_address_snapshot and
        billing_address_id must equal shipping_address_id."""
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

            svc = _make_payment_service(db)
            req = _checkout_with_address_id(prod, addr.id)

            ship_snap, ship_id = svc._resolve_shipping_address(user, req)
            bill_snap, bill_id = svc._resolve_billing_address(
                user, req, ship_snap, ship_id
            )

            assert bill_snap == ship_snap
            assert bill_id == ship_id
            assert bill_id == addr.id

            order, _ = svc._build_order(
                user.id, req,
                snapshot=ship_snap,
                resolved_address_id=ship_id,
                billing_snapshot=bill_snap,
                billing_address_id=bill_id,
            )
            assert order.billing_address_snapshot == order.shipping_address_snapshot
            assert order.billing_address_id == order.shipping_address_id
        finally:
            db.rollback()
            _cleanup(user_ids, product_ids)
            db.close()


# ---------------------------------------------------------------------------
# 2. Inline shipping + no billing → billing copies inline snapshot, ids None
# ---------------------------------------------------------------------------

class TestBillingCopiesInlineShipping:

    def test_inline_shipping_no_billing_billing_snapshot_equals_shipping(self) -> None:
        """When checkout uses an inline (unsaved) address and no billing fields
        are sent, billing_snapshot must equal shipping_snapshot and both ids
        must be None."""
        user_ids: list[int] = []
        product_ids: list[int] = []
        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db)
            product_ids.append(prod.id)
            db.commit()

            inline = _addr_create(pincode="400001")
            svc = _make_payment_service(db)
            req = _checkout_inline(prod, inline)

            ship_snap, ship_id = svc._resolve_shipping_address(user, req)
            bill_snap, bill_id = svc._resolve_billing_address(
                user, req, ship_snap, ship_id
            )

            assert ship_id is None
            assert bill_id is None
            assert bill_snap == ship_snap

            order, _ = svc._build_order(
                user.id, req,
                snapshot=ship_snap,
                resolved_address_id=ship_id,
                billing_snapshot=bill_snap,
                billing_address_id=bill_id,
            )
            assert order.billing_address_id is None
            assert order.shipping_address_id is None
            assert order.billing_address_snapshot == order.shipping_address_snapshot
        finally:
            db.rollback()
            _cleanup(user_ids, product_ids)
            db.close()


# ---------------------------------------------------------------------------
# 3. Explicit billing_address_id → billing uses that address; shipping unchanged
# ---------------------------------------------------------------------------

class TestExplicitBillingAddressId:

    def test_explicit_billing_id_overrides_default_copy(self) -> None:
        """When billing_address_id is supplied (and differs from the shipping
        address), billing_snapshot/id must reflect that saved address.
        Shipping columns must be unchanged."""
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
            ship_addr = addr_svc.create(user.id, _addr_create(pincode="560001"))
            bill_addr = addr_svc.create(
                user.id,
                _addr_create(pincode="110001", label=AddressLabel.WORK),
            )

            expected_bill_snap = snapshot_of(bill_addr)

            svc = _make_payment_service(db)
            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address_id=ship_addr.id,
                billing_address_id=bill_addr.id,
                payment_method="prepaid",
            )

            ship_snap, ship_id = svc._resolve_shipping_address(user, req)
            bill_snap, bill_id = svc._resolve_billing_address(
                user, req, ship_snap, ship_id
            )

            assert bill_id == bill_addr.id
            assert bill_snap == expected_bill_snap
            assert bill_snap["pincode"] == "110001"

            # Shipping must not have been modified.
            assert ship_id == ship_addr.id
            assert ship_snap["pincode"] == "560001"

            order, _ = svc._build_order(
                user.id, req,
                snapshot=ship_snap,
                resolved_address_id=ship_id,
                billing_snapshot=bill_snap,
                billing_address_id=bill_id,
            )
            assert order.billing_address_id == bill_addr.id
            assert order.billing_address_snapshot["pincode"] == "110001"
            assert order.shipping_address_id == ship_addr.id
            assert order.shipping_address_snapshot["pincode"] == "560001"
        finally:
            db.rollback()
            _cleanup(user_ids, product_ids)
            db.close()


# ---------------------------------------------------------------------------
# 4. billing_address_id belonging to another user → NotFoundError
# ---------------------------------------------------------------------------

class TestBillingAddressOwnership:

    def test_other_users_billing_address_id_raises_not_found(self) -> None:
        """Passing a billing_address_id that belongs to another user must raise
        NotFoundError — no existence leak."""
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

            addr_svc = AddressService(db)
            ship_addr = addr_svc.create(user_a.id, _addr_create(pincode="560001"))
            other_addr = addr_svc.create(user_b.id, _addr_create(pincode="400001"))

            svc = _make_payment_service(db)
            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address_id=ship_addr.id,
                billing_address_id=other_addr.id,
                payment_method="prepaid",
            )

            ship_snap, ship_id = svc._resolve_shipping_address(user_a, req)

            with pytest.raises(NotFoundError):
                svc._resolve_billing_address(user_a, req, ship_snap, ship_id)
        finally:
            db.rollback()
            _cleanup(user_ids, product_ids)
            db.close()


# ---------------------------------------------------------------------------
# 5. Inline billing_address → snapshot stored; billing_id None; no new address
# ---------------------------------------------------------------------------

class TestInlineBillingAddress:

    def test_inline_billing_snapshot_stored_id_none_count_unchanged(self) -> None:
        """When an inline billing_address is supplied, billing_address_snapshot
        must be set, billing_address_id must be None, and the user's address
        count must not have increased."""
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
            ship_addr = addr_svc.create(user.id, _addr_create(pincode="560001"))
            count_before = len(addr_svc.list_for_user(user.id))

            inline_billing = _addr_create(pincode="110001", label=AddressLabel.WORK)
            expected_bill_snap = snapshot_of(inline_billing)

            svc = _make_payment_service(db)
            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address_id=ship_addr.id,
                billing_address=inline_billing,
                payment_method="prepaid",
            )

            ship_snap, ship_id = svc._resolve_shipping_address(user, req)
            bill_snap, bill_id = svc._resolve_billing_address(
                user, req, ship_snap, ship_id
            )

            assert bill_id is None
            assert bill_snap == expected_bill_snap
            assert bill_snap["pincode"] == "110001"

            count_after = len(addr_svc.list_for_user(user.id))
            assert count_after == count_before, (
                "Inline billing must not persist to the address book"
            )

            order, _ = svc._build_order(
                user.id, req,
                snapshot=ship_snap,
                resolved_address_id=ship_id,
                billing_snapshot=bill_snap,
                billing_address_id=bill_id,
            )
            assert order.billing_address_id is None
            assert order.billing_address_snapshot == expected_bill_snap
        finally:
            db.rollback()
            _cleanup(user_ids, product_ids)
            db.close()


# ---------------------------------------------------------------------------
# 6. Legacy free-text shipping combos
# ---------------------------------------------------------------------------

class TestLegacyShippingWithBilling:

    def test_legacy_shipping_no_billing_both_billing_columns_none(self) -> None:
        """Legacy free-text shipping + no billing fields → billing_address_snapshot
        and billing_address_id must both be None (nothing structured to copy)."""
        user_ids: list[int] = []
        product_ids: list[int] = []
        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db)
            product_ids.append(prod.id)
            db.commit()

            svc = _make_payment_service(db)
            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                shipping_address="123 Legacy Lane, Mumbai, Maharashtra",
                shipping_pincode="400001",
                payment_method="prepaid",
            )

            ship_snap, ship_id = svc._resolve_shipping_address(user, req)
            # Legacy path returns (None, None).
            assert ship_snap is None
            assert ship_id is None

            bill_snap, bill_id = svc._resolve_billing_address(
                user, req, ship_snap, ship_id
            )
            assert bill_snap is None
            assert bill_id is None

            order, _ = svc._build_order(
                user.id, req,
                snapshot=ship_snap,
                resolved_address_id=ship_id,
                billing_snapshot=bill_snap,
                billing_address_id=bill_id,
            )
            assert order.billing_address_snapshot is None
            assert order.billing_address_id is None
        finally:
            db.rollback()
            _cleanup(user_ids, product_ids)
            db.close()

    def test_legacy_shipping_with_explicit_billing_id_billing_populated(self) -> None:
        """Legacy free-text shipping + explicit billing_address_id → billing
        columns must be populated even though shipping snapshot is None."""
        user_ids: list[int] = []
        product_ids: list[int] = []
        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = _make_product(db)
            product_ids.append(prod.id)
            db.commit()

            bill_addr = AddressService(db).create(user.id, _addr_create(pincode="110001"))
            expected_bill_snap = snapshot_of(bill_addr)

            svc = _make_payment_service(db)
            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                shipping_address="123 Legacy Lane, Mumbai, Maharashtra",
                shipping_pincode="400001",
                billing_address_id=bill_addr.id,
                payment_method="prepaid",
            )

            ship_snap, ship_id = svc._resolve_shipping_address(user, req)
            bill_snap, bill_id = svc._resolve_billing_address(
                user, req, ship_snap, ship_id
            )

            assert bill_id == bill_addr.id
            assert bill_snap == expected_bill_snap

            order, _ = svc._build_order(
                user.id, req,
                snapshot=ship_snap,
                resolved_address_id=ship_id,
                billing_snapshot=bill_snap,
                billing_address_id=bill_id,
            )
            assert order.billing_address_id == bill_addr.id
            assert order.billing_address_snapshot == expected_bill_snap
            # Shipping columns stay None (legacy path).
            assert order.shipping_address_snapshot is None
        finally:
            db.rollback()
            _cleanup(user_ids, product_ids)
            db.close()


# ---------------------------------------------------------------------------
# 7. Snapshot frozen: update/delete billing address → snapshot unchanged
# ---------------------------------------------------------------------------

class TestBillingSnapshotFrozen:

    def test_snapshot_unchanged_after_billing_address_update_and_delete(self) -> None:
        """After placing an order with a billing_address_id:
        - Updating the billing address fields must NOT change the snapshot.
        - Deleting the billing address must set billing_address_id to NULL via
          the FK SET NULL constraint, but must NOT touch the snapshot.

        The order row is inserted via raw SQL to match the existing pattern in
        test_checkout_address.py and avoid ORM issues with missing columns.
        """
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
            ship_addr = addr_svc.create(user.id, _addr_create(pincode="560001"))
            bill_addr = addr_svc.create(
                user.id,
                _addr_create(pincode="110001", label=AddressLabel.WORK),
            )
            original_bill_snap = snapshot_of(bill_addr)

            svc = _make_payment_service(db)
            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address_id=ship_addr.id,
                billing_address_id=bill_addr.id,
                payment_method="prepaid",
            )

            ship_snap, ship_id = svc._resolve_shipping_address(user, req)
            bill_snap, bill_id = svc._resolve_billing_address(
                user, req, ship_snap, ship_id
            )
            order, _ = svc._build_order(
                user.id, req,
                snapshot=ship_snap,
                resolved_address_id=ship_id,
                billing_snapshot=bill_snap,
                billing_address_id=bill_id,
            )

            order_id = _insert_order_raw(
                db,
                user_id=user.id,
                order=order,
                shipping_address_id=ship_id,
                shipping_snapshot=ship_snap,
                billing_address_id=bill_addr.id,
                billing_snapshot=bill_snap,
            )
            order_ids.append(order_id)

            # --- Update the billing address ---
            addr_svc.update(
                user.id, bill_addr.id,
                AddressUpdate(
                    line1="9999 New Street",
                    city="Chennai",
                    state="Tamil Nadu",
                    pincode="600001",
                ),
            )

            row = db.execute(
                text(
                    "SELECT billing_address_snapshot, billing_address_id "
                    "FROM orders WHERE id = :id"
                ),
                {"id": order_id},
            ).mappings().one()

            stored_snap = _json.loads(row["billing_address_snapshot"])
            assert stored_snap == original_bill_snap, (
                "Updating the billing address must not change the order snapshot"
            )
            assert row["billing_address_id"] == bill_addr.id, (
                "FK must still point to the billing address after an update"
            )

            # --- Delete the billing address (FK SET NULL fires) ---
            addr_svc.delete(user.id, bill_addr.id)

            row2 = db.execute(
                text(
                    "SELECT billing_address_snapshot, billing_address_id "
                    "FROM orders WHERE id = :id"
                ),
                {"id": order_id},
            ).mappings().one()

            assert row2["billing_address_id"] is None, (
                "billing_address_id must become NULL after address deletion (FK SET NULL)"
            )
            stored_snap2 = _json.loads(row2["billing_address_snapshot"])
            assert stored_snap2 == original_bill_snap, (
                "Billing snapshot must survive address deletion"
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


# ---------------------------------------------------------------------------
# 8. Copy independence: shipping and billing dicts are separate objects
# ---------------------------------------------------------------------------

class TestBillingShippingCopyIndependence:

    def test_mutating_shipping_snapshot_does_not_affect_billing_snapshot(self) -> None:
        """When billing is copied from shipping (default path), the two dicts
        must be independent objects — mutating one must not change the other."""
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

            svc = _make_payment_service(db)
            req = _checkout_with_address_id(prod, addr.id)

            ship_snap, ship_id = svc._resolve_shipping_address(user, req)
            bill_snap, bill_id = svc._resolve_billing_address(
                user, req, ship_snap, ship_id
            )

            order, _ = svc._build_order(
                user.id, req,
                snapshot=ship_snap,
                resolved_address_id=ship_id,
                billing_snapshot=bill_snap,
                billing_address_id=bill_id,
            )

            # Capture the original billing pincode before mutating shipping.
            original_billing_pincode = order.billing_address_snapshot["pincode"]

            # Mutate the shipping snapshot dict in-place.
            order.shipping_address_snapshot["pincode"] = "999999"
            order.shipping_address_snapshot["city"] = "Mutated City"

            # Billing snapshot must be unaffected.
            assert order.billing_address_snapshot["pincode"] == original_billing_pincode, (
                "Mutating shipping_address_snapshot must not change billing_address_snapshot"
            )
            assert order.billing_address_snapshot.get("city") != "Mutated City"
        finally:
            db.rollback()
            _cleanup(user_ids, product_ids)
            db.close()
