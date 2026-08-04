"""Schemas for the payment-instrument picker.

The list is admin-configurable in settings; this schema is just the wire
shape. Each instrument carries its display config + per-instrument discount
metadata so the checkout picker can render the cards without a second call.
"""
from __future__ import annotations

from decimal import Decimal

from pydantic import Field
from app.schemas.base import AppSchema


class PaymentInstrumentItem(AppSchema):
    # Stable enum key — also what we persist on `orders.payment_instrument`.
    code: str  # 'upi' | 'netbanking' | 'card' | 'wallet'
    label: str
    description: str
    enabled: bool
    discount_percent: Decimal = Decimal("0")
    suggested: bool = False


class PaymentInstrumentsResponse(AppSchema):
    items: list[PaymentInstrumentItem] = Field(default_factory=list)
