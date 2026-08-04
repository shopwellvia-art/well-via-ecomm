"""COD availability check schemas.

The check endpoint (POST /cod/check) answers "is COD available for this
cart + this pincode?" and returns the surcharge. Used by the checkout
payment-method picker to render the COD card enabled or greyed out with
human-readable reasons.
"""
from __future__ import annotations

from decimal import Decimal

from pydantic import Field
from app.schemas.base import AppSchema


class CodCheckItem(AppSchema):
    product_id: int
    quantity: int = Field(gt=0)


class CodCheckRequest(AppSchema):
    items: list[CodCheckItem] = Field(min_length=1)
    # Optional. Skipping the pincode means we can't run the carrier
    # serviceability gate — we treat it as "unknown" rather than refuse.
    destination_pincode: str | None = Field(default=None, min_length=3, max_length=10)


class CodCheckResponse(AppSchema):
    available: bool
    # All failed gates, not just the first one — UI shows them as a list so
    # the customer sees every blocker at once (e.g. "Not available below
    # ₹199 AND not delivered to this pincode").
    reasons: list[str] = Field(default_factory=list)
    surcharge_amount: Decimal = Decimal("0")
    # The amount the customer would actually pay on delivery (cart subtotal +
    # tax + shipping − discount + surcharge). The checkout UI uses this to
    # show the COD total on the picker card without an extra round-trip.
    cod_total: Decimal = Decimal("0")

    # ---- Split COD (Phase 9) ---------------------------------------------
    # `split_available` inherits the full-COD gate chain AND requires
    # `cod.split_enabled` AND a non-degenerate prepaid amount (0 < portion <
    # total). When false but `available` is true, the customer can still
    # pick full COD — the picker just won't show the Split card.
    split_available: bool = False
    # Flat ₹ collected via the gateway upfront for Split COD.
    split_prepaid_amount: Decimal = Decimal("0")
    # Balance the carrier collects on delivery (= cod_total − split_prepaid_amount).
    split_cod_amount: Decimal = Decimal("0")
