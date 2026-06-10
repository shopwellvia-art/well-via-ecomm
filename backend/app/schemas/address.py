"""Pydantic schemas for the customer address book.

AddressCreate / AddressUpdate are used for CRUD input validation.
AddressRead is the serialization shape returned to the client.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.address import AddressLabel
from app.schemas._validators import normalize_phone


class AddressCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    phone: str
    line1: str = Field(min_length=1, max_length=255)
    line2: str | None = None
    landmark: str | None = None
    city: str = Field(min_length=1, max_length=120)
    state: str = Field(min_length=1, max_length=120)
    # Indian 6-digit pincodes — first digit non-zero.
    pincode: str = Field(pattern=r"^[1-9][0-9]{5}$")
    country: str = Field(default="IN", max_length=2)
    label: AddressLabel = AddressLabel.HOME
    is_default: bool = False

    @field_validator("full_name", mode="before")
    @classmethod
    def strip_full_name(cls, v: str) -> str:
        return v.strip() if isinstance(v, str) else v

    @field_validator("phone", mode="before")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        result = normalize_phone(v)
        if result is None:
            raise ValueError("Phone number is required")
        return result


class AddressUpdate(BaseModel):
    """Same fields as AddressCreate but all optional; None means 'unchanged'."""

    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = None
    line1: str | None = Field(default=None, min_length=1, max_length=255)
    line2: str | None = None
    landmark: str | None = None
    city: str | None = Field(default=None, min_length=1, max_length=120)
    state: str | None = Field(default=None, min_length=1, max_length=120)
    pincode: str | None = Field(default=None, pattern=r"^[1-9][0-9]{5}$")
    country: str | None = Field(default=None, max_length=2)
    label: AddressLabel | None = None
    is_default: bool | None = None

    @field_validator("full_name", mode="before")
    @classmethod
    def strip_full_name(cls, v: str | None) -> str | None:
        return v.strip() if isinstance(v, str) else v

    @field_validator("phone", mode="before")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        return normalize_phone(v)


class AddressRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    full_name: str
    phone: str
    line1: str
    line2: str | None
    landmark: str | None
    city: str
    state: str
    pincode: str
    country: str
    label: AddressLabel
    is_default: bool
    created_at: datetime
    updated_at: datetime
