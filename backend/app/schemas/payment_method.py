"""Pydantic schemas for the payment-method admin + public APIs."""
from __future__ import annotations

from pydantic import ConfigDict, Field
from app.schemas.base import AppSchema


class PaymentMethodFieldRead(AppSchema):
    """One credential field definition with its current value/state."""

    key: str
    label: str
    secret: bool
    required: bool
    placeholder: str
    help: str
    # For secret fields: value is ALWAYS null; set = True if a value is stored.
    # For non-secret fields: value = stored plaintext (or null); set accordingly.
    value: str | None
    set: bool


class PaymentMethodRead(AppSchema):
    """Full representation returned by GET/PUT /admin/payment-methods."""

    code: str
    name: str
    description: str
    enabled: bool
    environment: str
    supports_environment: bool
    implemented: bool
    # ready: every *required* credential field has a stored value.
    ready: bool
    sort_order: int
    fields: list[PaymentMethodFieldRead]


class PaymentMethodUpdate(AppSchema):
    """Body for PUT /admin/payment-methods/{code}."""

    enabled: bool | None = None
    environment: str | None = Field(default=None, pattern="^(sandbox|live)$")
    # Partial merge — key="" deletes the key; unknown keys → 422.
    credentials: dict[str, str] | None = None


class PaymentMethodActiveRead(AppSchema):
    """Minimal public representation for GET /payment-methods/active."""

    code: str
    name: str
    description: str


class PaymentMethodListResponse(AppSchema):
    """Wrapper returned by both list endpoints."""

    items: list[PaymentMethodRead]


class PaymentMethodActiveListResponse(AppSchema):
    items: list[PaymentMethodActiveRead]
