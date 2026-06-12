"""Business logic for the customer address book.

Architecture:
  - All DB writes go through AddressRepository (flush-only).
  - This service owns the commit after each mutation.
  - Pure helpers (snapshot_of, render_address_text) are module-level so they
    can be imported directly by payment_service without constructing an
    AddressService instance.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Union

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ValidationError
from app.models.address import Address
from app.repositories.address_repository import AddressRepository

if TYPE_CHECKING:
    from app.schemas.address import AddressCreate, AddressUpdate

logger = logging.getLogger(__name__)

MAX_ADDRESSES = 10


# ---------------------------------------------------------------------------
# Pure helpers — no DB access, usable everywhere including payment_service
# ---------------------------------------------------------------------------


def snapshot_of(addr: Union[Address, "AddressCreate"]) -> dict:
    """Return a frozen dict snapshot of an address suitable for storing as
    ``order.shipping_address_snapshot``.

    Accepts either an ORM ``Address`` instance or an ``AddressCreate`` Pydantic
    model (used for inline one-off addresses at checkout that haven't been
    persisted yet).

    The ``label`` value is stored as its string value (e.g. "home") consistent
    with how AddressLabel is defined as a str-enum.
    """
    label_val = getattr(addr, "label", None)
    if label_val is not None:
        # AddressLabel is a str-enum whose .value is "home"/"work"/"other"
        label_str = label_val.value if hasattr(label_val, "value") else str(label_val)
    else:
        label_str = "home"

    # Coordinates: cast to plain float() when present — ORM rows may carry a
    # Decimal-like type from MySQL DOUBLE; json.dumps chokes on Decimal but is
    # safe with float.  getattr with None default handles AddressCreate payloads
    # that don't have coordinates set.
    raw_lat = getattr(addr, "latitude", None)
    raw_lng = getattr(addr, "longitude", None)

    return {
        "full_name": addr.full_name,
        "phone": addr.phone,
        "line1": addr.line1,
        "line2": addr.line2 or None,
        "landmark": addr.landmark or None,
        "city": addr.city,
        "state": addr.state,
        "pincode": addr.pincode,
        "country": getattr(addr, "country", "IN") or "IN",
        "label": label_str,
        "latitude": float(raw_lat) if raw_lat is not None else None,
        "longitude": float(raw_lng) if raw_lng is not None else None,
    }


def render_address_text(snapshot: dict) -> str:
    """Render a snapshot dict into the legacy ``order.shipping_address`` string.

    Format (skipping empty parts):
        <full_name>, <line1>[, <line2>][, <landmark>], <city>, <state> - <pincode>, Phone: <phone>

    Truncation strategy (max 512 chars):
      1. Try with all parts.
      2. If > 512, re-render dropping landmark.
      3. If still > 512, re-render dropping landmark + line2.
      4. Hard-truncate to 512 as the final safety net.
    """

    def _build(include_line2: bool = True, include_landmark: bool = True) -> str:
        parts: list[str] = []
        full_name = (snapshot.get("full_name") or "").strip()
        if full_name:
            parts.append(full_name)

        line1 = (snapshot.get("line1") or "").strip()
        if line1:
            parts.append(line1)

        if include_line2:
            line2 = (snapshot.get("line2") or "").strip()
            if line2:
                parts.append(line2)

        if include_landmark:
            landmark = (snapshot.get("landmark") or "").strip()
            if landmark:
                parts.append(landmark)

        city = (snapshot.get("city") or "").strip()
        state = (snapshot.get("state") or "").strip()
        pincode = (snapshot.get("pincode") or "").strip()

        locality = ""
        if city and state and pincode:
            locality = f"{city}, {state} - {pincode}"
        elif city and state:
            locality = f"{city}, {state}"
        elif city:
            locality = city
        if locality:
            parts.append(locality)

        phone = (snapshot.get("phone") or "").strip()
        if phone:
            parts.append(f"Phone: {phone}")

        return ", ".join(parts)

    text = _build(include_line2=True, include_landmark=True)
    if len(text) <= 512:
        return text

    text = _build(include_line2=True, include_landmark=False)
    if len(text) <= 512:
        return text

    text = _build(include_line2=False, include_landmark=False)
    return text[:512]


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------


class AddressService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = AddressRepository(db)

    # ---- reads ----

    def list_for_user(self, user_id: int) -> list[Address]:
        return self.repo.list_for_user(user_id)

    def get_owned(self, user_id: int, address_id: int) -> Address:
        """Fetch a specific address, raising NotFoundError for missing OR
        not-owned rows.  Never 403 — don't leak existence of another user's
        address."""
        addr = self.repo.get(address_id)
        if not addr or addr.user_id != user_id:
            raise NotFoundError("Address not found")
        return addr

    # ---- mutations ----

    def create(self, user_id: int, data: "AddressCreate") -> Address:
        """Create a new address for ``user_id``.

        Rules:
        - Rejects if the user already has MAX_ADDRESSES.
        - First address for a user is auto-promoted to default.
        - If data.is_default is True, clears the existing default first.
        """
        count = self.repo.count_for_user(user_id)
        if count >= MAX_ADDRESSES:
            raise ValidationError(
                f"You can save at most {MAX_ADDRESSES} addresses. "
                "Please remove one before adding a new one."
            )

        # First address → automatically the default regardless of the flag.
        force_default = count == 0

        if force_default or data.is_default:
            self.repo.clear_default(user_id)

        addr = Address(
            user_id=user_id,
            full_name=data.full_name,
            phone=data.phone,
            line1=data.line1,
            line2=data.line2,
            landmark=data.landmark,
            city=data.city,
            state=data.state,
            pincode=data.pincode,
            country=data.country,
            label=data.label,
            is_default=force_default or data.is_default,
            latitude=data.latitude,
            longitude=data.longitude,
        )
        self.repo.add(addr)
        self.db.commit()
        self.db.refresh(addr)
        return addr

    def update(self, user_id: int, address_id: int, data: "AddressUpdate") -> Address:
        addr = self.get_owned(user_id, address_id)

        # Apply non-None fields from the update payload.
        if data.full_name is not None:
            addr.full_name = data.full_name
        if data.phone is not None:
            addr.phone = data.phone
        if data.line1 is not None:
            addr.line1 = data.line1
        if data.line2 is not None:
            addr.line2 = data.line2
        if data.landmark is not None:
            addr.landmark = data.landmark
        if data.city is not None:
            addr.city = data.city
        if data.state is not None:
            addr.state = data.state
        if data.pincode is not None:
            addr.pincode = data.pincode
        if data.country is not None:
            addr.country = data.country
        if data.label is not None:
            addr.label = data.label

        if data.latitude is not None:
            addr.latitude = data.latitude
        if data.longitude is not None:
            addr.longitude = data.longitude

        if data.is_default is True and not addr.is_default:
            # Becoming the default — clear others first.
            self.repo.clear_default(user_id)
            addr.is_default = True
        elif data.is_default is False:
            addr.is_default = False

        self.db.flush()
        self.db.commit()
        self.db.refresh(addr)
        return addr

    def delete(self, user_id: int, address_id: int) -> None:
        addr = self.get_owned(user_id, address_id)
        was_default = addr.is_default
        self.repo.delete(addr)

        if was_default:
            # Promote the most-recently-updated remaining address to default.
            remaining = self.repo.list_for_user(user_id)
            if remaining:
                # list_for_user orders by updated_at desc when is_default is
                # equal (all False now); first entry is most-recently-updated.
                remaining[0].is_default = True
                self.db.flush()

        self.db.commit()

    def set_default(self, user_id: int, address_id: int) -> Address:
        addr = self.get_owned(user_id, address_id)
        self.repo.clear_default(user_id)
        addr.is_default = True
        self.db.flush()
        self.db.commit()
        self.db.refresh(addr)
        return addr
