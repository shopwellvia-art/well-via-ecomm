"""Shipping-related response schemas.

Phase 2 ships just the serviceability shape. Later phases extend this file
with quote / shipment / tracking schemas — keeping all shipping wire
contracts in one place makes them easy to audit.
"""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class ServiceabilityResponse(BaseModel):
    """Result of `GET /shipping/serviceability/{pincode}`.

    Public endpoint — never returns provider-specific raw payloads so a
    leaked staging token can't be diffed against the response shape."""

    pincode: str
    serviceable: bool
    cod_available: bool = False
    prepaid_available: bool = True
    eta_days_min: int | None = Field(default=None, ge=0)
    eta_days_max: int | None = Field(default=None, ge=0)
    # Free-text reason for non-serviceability; safe to render verbatim to the
    # customer (we control the strings on both mock and `none` providers, and
    # Delhivery's `remark` field is sanitized before it gets here).
    remark: str | None = None
    # Which provider answered. Surfaced so the frontend can hide the widget
    # when shipping is disabled entirely.
    provider: str


class RateQuoteItem(BaseModel):
    """Single cart line as the rate-quote endpoint sees it."""

    product_id: int
    quantity: int = Field(gt=0)


class RateQuoteRequest(BaseModel):
    """Body of POST /shipping/rate-quote.

    We only take the destination pin + the cart; weights + prices are read
    from the products table server-side. This means the customer can't lie
    about their cart's weight to game shipping cost."""

    destination_pincode: str = Field(min_length=3, max_length=10)
    items: list[RateQuoteItem] = Field(min_length=1)


class RateQuoteResponse(BaseModel):
    amount: Decimal
    currency: str = "INR"
    chargeable_weight_grams: int
    provider: str
    # Optional ETA pulled from the same pincode lookup we already cache —
    # convenient for the cart summary so it doesn't need a second call.
    eta_days_min: int | None = None
    eta_days_max: int | None = None


class PincodeLookupResponse(BaseModel):
    """Result of GET /shipping/pincode/{pincode}.

    Always returns HTTP 200 — `found=False` is the degraded-but-safe response
    when the pin is invalid, the external API is down, or the pin simply isn't
    in the postal database.  Autofill is cosmetic; checkout is never blocked by
    a lookup failure.
    """

    pincode: str
    found: bool
    city: str | None = None
    state: str | None = None
