"""Tests for AddressService — CRUD, default-flag invariants, ownership, and
pure helpers (snapshot_of, render_address_text).

Runs against the live MySQL instance inside the backend container:

    docker compose exec backend pytest tests/test_address_service.py -v

Strategy (follows test_dashboard_service.py / test_totp_service.py):
- Every test opens its own SessionLocal, inserts known rows, asserts, then
  deletes test-owned rows in a finally block via a fresh session so teardown
  never fails on a half-rolled-back transaction.
- Users are created without a customer satellite because AddressService only
  needs `users.id` and no loyalty/profile code runs in address operations.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ValidationError
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.address import Address, AddressLabel
from app.models.user import User
from app.schemas.address import AddressCreate, AddressUpdate
from app.services.address_service import (
    MAX_ADDRESSES,
    AddressService,
    render_address_text,
    snapshot_of,
)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _make_user(db: Session) -> User:
    u = User(
        email=f"addrtest-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(u)
    db.flush()
    return u


def _addr_create(
    *,
    is_default: bool = False,
    label: AddressLabel = AddressLabel.HOME,
    pincode: str = "560001",
    line2: str | None = None,
    landmark: str | None = None,
) -> AddressCreate:
    uid = _uid()
    return AddressCreate(
        full_name=f"Test User {uid}",
        phone="9876543210",
        line1=f"{uid} Main Street",
        line2=line2,
        landmark=landmark,
        city="Bangalore",
        state="Karnataka",
        pincode=pincode,
        country="IN",
        label=label,
        is_default=is_default,
    )


def _cleanup(user_ids: list[int]) -> None:
    """Hard-delete all test-created rows via a fresh session."""
    if not user_ids:
        return
    with SessionLocal() as s:
        # addresses ON DELETE CASCADE from users; order_items and orders have
        # no test rows here, so we only need to clear users (and the customer
        # satellite which also cascades).
        s.execute(
            text("DELETE FROM addresses WHERE user_id IN :ids"),
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


def _count_defaults(db: Session, user_id: int) -> int:
    return db.execute(
        select(Address).where(Address.user_id == user_id, Address.is_default.is_(True))
    ).scalars().all().__len__()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAddressServiceCreate:

    def test_first_address_is_default_even_when_flag_false(self) -> None:
        """The very first address for a user is always promoted to default,
        regardless of the is_default field in AddressCreate."""
        user_ids: list[int] = []
        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            db.commit()

            svc = AddressService(db)
            addr = svc.create(user.id, _addr_create(is_default=False))

            assert addr.is_default is True, (
                "First address must be auto-promoted to default even when is_default=False"
            )
            # Only one default must exist.
            assert _count_defaults(db, user.id) == 1
        finally:
            _cleanup(user_ids)
            db.close()

    def test_second_address_with_is_default_flips_exactly_one_default(self) -> None:
        """Creating a second address with is_default=True must clear the first
        default so that exactly one address per user carries is_default=True."""
        user_ids: list[int] = []
        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            db.commit()

            svc = AddressService(db)
            addr1 = svc.create(user.id, _addr_create(is_default=False))
            addr2 = svc.create(user.id, _addr_create(is_default=True))

            # Reload from DB to get the current state.
            db.expire_all()
            fresh1 = db.get(Address, addr1.id)
            fresh2 = db.get(Address, addr2.id)

            assert fresh1 is not None and fresh2 is not None
            assert fresh2.is_default is True, "Second address (is_default=True) should be default"
            assert fresh1.is_default is False, "First address should no longer be default"
            assert _count_defaults(db, user.id) == 1, "Exactly one default must exist"
        finally:
            _cleanup(user_ids)
            db.close()

    def test_eleventh_address_raises_validation_error(self) -> None:
        """Creating an 11th address must raise ValidationError (MAX_ADDRESSES=10)."""
        user_ids: list[int] = []
        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            db.commit()

            svc = AddressService(db)
            for _ in range(MAX_ADDRESSES):
                svc.create(user.id, _addr_create())

            with pytest.raises(ValidationError):
                svc.create(user.id, _addr_create())
        finally:
            _cleanup(user_ids)
            db.close()


class TestAddressServiceUpdate:

    def test_update_sets_is_default_flips_correctly(self) -> None:
        """Updating an address to is_default=True must clear the previous default
        and leave exactly one default for the user."""
        user_ids: list[int] = []
        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            db.commit()

            svc = AddressService(db)
            addr1 = svc.create(user.id, _addr_create())  # becomes default (first)
            addr2 = svc.create(user.id, _addr_create(is_default=False))

            # addr1 is default; promote addr2 via update.
            svc.update(user.id, addr2.id, AddressUpdate(is_default=True))

            db.expire_all()
            fresh1 = db.get(Address, addr1.id)
            fresh2 = db.get(Address, addr2.id)

            assert fresh1 is not None and fresh2 is not None
            assert fresh2.is_default is True
            assert fresh1.is_default is False
            assert _count_defaults(db, user.id) == 1
        finally:
            _cleanup(user_ids)
            db.close()


class TestAddressServiceOwnership:

    def test_user_b_cannot_get_user_a_address(self) -> None:
        """get_owned must raise NotFoundError when user B asks for user A's
        address — existence must not be leaked (not a 403)."""
        user_ids: list[int] = []
        db = SessionLocal()
        try:
            user_a = _make_user(db)
            user_b = _make_user(db)
            user_ids.extend([user_a.id, user_b.id])
            db.commit()

            svc = AddressService(db)
            addr = svc.create(user_a.id, _addr_create())

            with pytest.raises(NotFoundError):
                svc.get_owned(user_b.id, addr.id)
        finally:
            _cleanup(user_ids)
            db.close()

    def test_user_b_cannot_update_user_a_address(self) -> None:
        """update must raise NotFoundError when another user owns the address."""
        user_ids: list[int] = []
        db = SessionLocal()
        try:
            user_a = _make_user(db)
            user_b = _make_user(db)
            user_ids.extend([user_a.id, user_b.id])
            db.commit()

            svc = AddressService(db)
            addr = svc.create(user_a.id, _addr_create())

            with pytest.raises(NotFoundError):
                svc.update(user_b.id, addr.id, AddressUpdate(city="Mumbai"))
        finally:
            _cleanup(user_ids)
            db.close()

    def test_user_b_cannot_delete_user_a_address(self) -> None:
        """delete must raise NotFoundError when another user owns the address."""
        user_ids: list[int] = []
        db = SessionLocal()
        try:
            user_a = _make_user(db)
            user_b = _make_user(db)
            user_ids.extend([user_a.id, user_b.id])
            db.commit()

            svc = AddressService(db)
            addr = svc.create(user_a.id, _addr_create())

            with pytest.raises(NotFoundError):
                svc.delete(user_b.id, addr.id)
        finally:
            _cleanup(user_ids)
            db.close()


class TestAddressServiceDelete:

    def test_delete_default_promotes_most_recently_updated_survivor(self) -> None:
        """Deleting the current default must promote the most-recently-updated
        surviving address to default.

        MySQL DATETIME has second resolution, so creating all rows and then
        immediately updating addr2 can leave addr2.updated_at == addr3.updated_at
        (same second); on a tie the repository falls back to id desc, which
        would pick addr3.  Sleep past the second boundary before touching addr2
        so its updated_at is STRICTLY newer and the promotion target is
        unambiguous.  After the default (addr1) is deleted, addr2 must be the
        promoted default.
        """
        user_ids: list[int] = []
        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            db.commit()

            svc = AddressService(db)
            addr1 = svc.create(user.id, _addr_create())   # default (first)
            addr2 = svc.create(user.id, _addr_create())   # not default
            addr3 = svc.create(user.id, _addr_create())   # not default

            time.sleep(1.1)  # cross the DATETIME second boundary
            svc.update(user.id, addr2.id, AddressUpdate(city="Mumbai"))

            db.expire_all()
            fresh2_pre = db.get(Address, addr2.id)
            fresh3_pre = db.get(Address, addr3.id)
            assert fresh2_pre is not None and fresh3_pre is not None
            assert fresh2_pre.updated_at > fresh3_pre.updated_at, (
                "Setup: addr2 must have strictly newer updated_at than addr3"
            )

            # addr1 is the default — delete it.
            svc.delete(user.id, addr1.id)

            db.expire_all()
            fresh2 = db.get(Address, addr2.id)
            fresh3 = db.get(Address, addr3.id)

            assert fresh2 is not None and fresh3 is not None
            # addr2 was updated most recently, so it should be promoted.
            assert fresh2.is_default is True, (
                "Most-recently-updated surviving address (addr2) must become default"
            )
            assert fresh3.is_default is False
            assert _count_defaults(db, user.id) == 1
        finally:
            _cleanup(user_ids)
            db.close()

    def test_delete_last_address_leaves_no_default(self) -> None:
        """Deleting the only address must leave no default row."""
        user_ids: list[int] = []
        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            db.commit()

            svc = AddressService(db)
            addr = svc.create(user.id, _addr_create())
            assert addr.is_default is True

            svc.delete(user.id, addr.id)

            db.expire_all()
            assert _count_defaults(db, user.id) == 0, (
                "No default must remain after the last address is deleted"
            )
        finally:
            _cleanup(user_ids)
            db.close()

    def test_delete_non_default_does_not_change_default(self) -> None:
        """Deleting a non-default address must not change which address is default."""
        user_ids: list[int] = []
        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            db.commit()

            svc = AddressService(db)
            addr1 = svc.create(user.id, _addr_create())       # auto-default (first)
            addr2 = svc.create(user.id, _addr_create())       # not default

            svc.delete(user.id, addr2.id)

            db.expire_all()
            fresh1 = db.get(Address, addr1.id)
            assert fresh1 is not None
            assert fresh1.is_default is True, "Existing default must stay after non-default deletion"
            assert _count_defaults(db, user.id) == 1
        finally:
            _cleanup(user_ids)
            db.close()


# ---------------------------------------------------------------------------
# Pure helper tests — no DB, no session
# ---------------------------------------------------------------------------

class TestRenderAddressText:

    def test_all_parts_present_are_included(self) -> None:
        snap = {
            "full_name": "John Doe",
            "phone": "9876543210",
            "line1": "123 Main St",
            "line2": "Apt 4",
            "landmark": "Near Park",
            "city": "Bangalore",
            "state": "Karnataka",
            "pincode": "560001",
            "country": "IN",
        }
        text = render_address_text(snap)
        assert "John Doe" in text
        assert "123 Main St" in text
        assert "Apt 4" in text
        assert "Near Park" in text
        assert "Bangalore" in text
        assert "Karnataka" in text
        assert "560001" in text
        assert "9876543210" in text

    def test_empty_optional_parts_are_skipped(self) -> None:
        snap = {
            "full_name": "Jane",
            "phone": "9000000000",
            "line1": "456 Side Street",
            "line2": None,
            "landmark": "",
            "city": "Mumbai",
            "state": "Maharashtra",
            "pincode": "400001",
            "country": "IN",
        }
        text = render_address_text(snap)
        # The rendered text must not contain empty-label markers or double commas
        assert "None" not in text
        assert ", ," not in text
        assert "Jane" in text
        assert "456 Side Street" in text
        assert "Mumbai" in text

    def test_result_is_at_most_512_chars_even_with_max_length_fields(self) -> None:
        """Even with all fields at their maximum defined lengths the output must
        not exceed 512 characters (truncation strategy)."""
        snap = {
            "full_name": "N" * 120,
            "phone": "9" * 20,
            "line1": "L" * 255,
            "line2": "M" * 255,
            "landmark": "K" * 120,
            "city": "C" * 120,
            "state": "S" * 120,
            "pincode": "560001",
            "country": "IN",
        }
        text = render_address_text(snap)
        assert len(text) <= 512, f"render_address_text exceeded 512 chars: {len(text)}"


class TestSnapshotOf:

    def test_snapshot_contains_all_expected_keys(self) -> None:
        data = _addr_create(line2="Floor 2", landmark="Near Bus Stop")
        snap = snapshot_of(data)
        expected_keys = {
            "full_name", "phone", "line1", "line2", "landmark",
            "city", "state", "pincode", "country", "label",
            "latitude", "longitude",
        }
        assert expected_keys == set(snap.keys()), (
            f"Snapshot missing keys: {expected_keys - set(snap.keys())}"
        )

    def test_snapshot_label_is_serialized_as_string(self) -> None:
        data = _addr_create(label=AddressLabel.WORK)
        snap = snapshot_of(data)
        assert isinstance(snap["label"], str), "label must be a plain string in the snapshot"
        assert snap["label"] == "work"

    def test_snapshot_of_orm_address(self) -> None:
        """snapshot_of must work on an ORM Address instance (not just AddressCreate)."""
        user_ids: list[int] = []
        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            db.commit()

            svc = AddressService(db)
            addr = svc.create(user.id, _addr_create(label=AddressLabel.OTHER))
            snap = snapshot_of(addr)

            assert snap["full_name"] == addr.full_name
            assert snap["pincode"] == addr.pincode
            assert snap["label"] == "other"
            assert isinstance(snap["label"], str)
        finally:
            _cleanup(user_ids)
            db.close()

    def test_snapshot_line2_and_landmark_none_when_absent(self) -> None:
        data = _addr_create(line2=None, landmark=None)
        snap = snapshot_of(data)
        assert snap["line2"] is None
        assert snap["landmark"] is None


# ---------------------------------------------------------------------------
# Latitude / longitude — new tests (migration j1f2a3b4c5d6)
# ---------------------------------------------------------------------------

class TestCoordinateStorage:

    def test_create_address_with_coords_stores_and_reads_them(self) -> None:
        """AddressService.create with lat/lng → row stores them; AddressRead returns them."""
        user_ids: list[int] = []
        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            db.commit()

            payload = AddressCreate(
                full_name="Map User",
                phone="9876543210",
                line1="1 Coord Lane",
                city="Bangalore",
                state="Karnataka",
                pincode="560001",
                country="IN",
                label=AddressLabel.HOME,
                latitude=12.9716,
                longitude=77.5946,
            )
            svc = AddressService(db)
            addr = svc.create(user.id, payload)

            assert addr.latitude == pytest.approx(12.9716, rel=1e-6)
            assert addr.longitude == pytest.approx(77.5946, rel=1e-6)

            from app.schemas.address import AddressRead
            read = AddressRead.model_validate(addr)
            assert read.latitude == pytest.approx(12.9716, rel=1e-6)
            assert read.longitude == pytest.approx(77.5946, rel=1e-6)
        finally:
            _cleanup(user_ids)
            db.close()

    def test_snapshot_of_address_with_coords_includes_plain_float_coords(self) -> None:
        """snapshot_of(orm_address_with_coords) must include latitude/longitude as
        plain Python floats (not Decimal — json.dumps must not raise)."""
        import json as _json

        user_ids: list[int] = []
        db = SessionLocal()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            db.commit()

            payload = AddressCreate(
                full_name="Snap User",
                phone="9876543210",
                line1="2 Snapshot Rd",
                city="Bangalore",
                state="Karnataka",
                pincode="560001",
                country="IN",
                label=AddressLabel.HOME,
                latitude=12.9716,
                longitude=77.5946,
            )
            addr = AddressService(db).create(user.id, payload)
            snap = snapshot_of(addr)

            assert snap["latitude"] == pytest.approx(12.9716, rel=1e-6)
            assert snap["longitude"] == pytest.approx(77.5946, rel=1e-6)
            assert isinstance(snap["latitude"], float), "latitude must be a plain float"
            assert isinstance(snap["longitude"], float), "longitude must be a plain float"

            # Must be JSON-serializable without raising (catches Decimal pitfall).
            serialized = _json.dumps(snap)
            roundtripped = _json.loads(serialized)
            assert roundtripped["latitude"] == pytest.approx(12.9716, rel=1e-6)
            assert roundtripped["longitude"] == pytest.approx(77.5946, rel=1e-6)
        finally:
            _cleanup(user_ids)
            db.close()

    def test_snapshot_of_address_without_coords_has_none_values(self) -> None:
        """Address created without coordinates → snapshot has latitude=None, longitude=None."""
        data = _addr_create()  # no lat/lng
        snap = snapshot_of(data)
        assert snap["latitude"] is None
        assert snap["longitude"] is None

    def test_address_create_rejects_latitude_out_of_range(self) -> None:
        """AddressCreate with latitude=91 (> 90) must raise a Pydantic ValidationError."""
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError):
            AddressCreate(
                full_name="Bad Lat",
                phone="9876543210",
                line1="1 Bad Lane",
                city="Bangalore",
                state="Karnataka",
                pincode="560001",
                country="IN",
                latitude=91.0,  # out of range
            )

    def test_address_create_rejects_longitude_out_of_range(self) -> None:
        """AddressCreate with longitude=181 (> 180) must raise a Pydantic ValidationError."""
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError):
            AddressCreate(
                full_name="Bad Lng",
                phone="9876543210",
                line1="1 Bad Lane",
                city="Bangalore",
                state="Karnataka",
                pincode="560001",
                country="IN",
                longitude=181.0,  # out of range
            )


class TestCoordinateSnapshotInOrder:

    def test_checkout_order_snapshot_carries_coords_and_is_json_serializable(
        self,
    ) -> None:
        """Full checkout path: address with lat/lng → order.shipping_address_snapshot
        carries latitude/longitude AND survives a json.dumps round-trip (catches the
        Decimal pitfall at the point where the order JSON column is serialized).

        The order row is committed to the DB via raw SQL (same pattern as
        TestSnapshotImmutability) so the MySQL JSON column serialization is exercised.
        """
        import json as _json
        from decimal import Decimal as _Decimal
        from unittest.mock import MagicMock, patch
        from sqlalchemy import text as _text

        from app.db.session import SessionLocal as _SL
        from app.models.product import Product
        from app.schemas.order import OrderItemCreate
        from app.schemas.payment import CheckoutRequest
        from app.services.payment_service import PaymentService

        user_ids: list[int] = []
        product_ids: list[int] = []
        order_ids: list[int] = []

        db = _SL()
        try:
            user = _make_user(db)
            user_ids.append(user.id)
            prod = Product(
                sku=f"SKU-COORD-{_uid()}",
                name=f"CoordProd {_uid()}",
                price=_Decimal("100.00"),
                stock=10,
            )
            db.add(prod)
            db.commit()
            product_ids.append(prod.id)

            payload = AddressCreate(
                full_name="Coord Buyer",
                phone="9876543210",
                line1="3 GPS Road",
                city="Bangalore",
                state="Karnataka",
                pincode="560001",
                country="IN",
                label=AddressLabel.HOME,
                latitude=12.9716,
                longitude=77.5946,
            )
            addr = AddressService(db).create(user.id, payload)

            mock_provider = MagicMock()
            with patch(
                "app.services.payment_service.get_payment_provider",
                return_value=mock_provider,
            ):
                svc = PaymentService(db)

            req = CheckoutRequest(
                items=[OrderItemCreate(product_id=prod.id, quantity=1)],
                address_id=addr.id,
                payment_method="prepaid",
            )
            snap, resolved_id = svc._resolve_shipping_address(user, req)

            assert snap["latitude"] == pytest.approx(12.9716, rel=1e-6)
            assert snap["longitude"] == pytest.approx(77.5946, rel=1e-6)
            assert isinstance(snap["latitude"], float)
            assert isinstance(snap["longitude"], float)

            order, _ = svc._build_order(
                user.id, req, snapshot=snap, resolved_address_id=resolved_id
            )

            # Insert via raw SQL and commit — exercises MySQL JSON column serialization.
            db.execute(
                _text(
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

            order_id = db.execute(
                _text(
                    "SELECT id FROM orders WHERE user_id = :uid "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"uid": user.id},
            ).scalar_one()
            order_ids.append(order_id)

            # Re-read the snapshot from the DB and check it has coords.
            row = db.execute(
                _text(
                    "SELECT shipping_address_snapshot FROM orders WHERE id = :id"
                ),
                {"id": order_id},
            ).mappings().one()

            stored_snap = _json.loads(row["shipping_address_snapshot"])
            assert stored_snap["latitude"] == pytest.approx(12.9716, rel=1e-6)
            assert stored_snap["longitude"] == pytest.approx(77.5946, rel=1e-6)
        finally:
            # Close the test session before cleanup to release any locks.
            db.close()
            with _SL() as s:
                if order_ids:
                    s.execute(
                        _text("DELETE FROM order_items WHERE order_id IN :ids"),
                        {"ids": tuple(order_ids)},
                    )
                    s.execute(
                        _text("DELETE FROM orders WHERE id IN :ids"),
                        {"ids": tuple(order_ids)},
                    )
                if product_ids:
                    s.execute(
                        _text("DELETE FROM products WHERE id IN :ids"),
                        {"ids": tuple(product_ids)},
                    )
                s.commit()
            _cleanup(user_ids)
