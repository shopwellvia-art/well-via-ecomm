from decimal import Decimal

from pydantic import Field, field_validator
from app.schemas.base import AppSchema


class CartItemIn(AppSchema):
    product_id: int
    quantity: int = Field(gt=0)


class CartItemQuantityUpdate(AppSchema):
    """Body for PUT /cart/items/{product_id}. Quantity of 0 removes the line —
    the endpoint normalizes the call so the customer-side UI doesn't need
    to switch between PUT and DELETE based on the stepper value."""

    quantity: int = Field(ge=0, le=999)


class CartItemRead(AppSchema):
    product_id: int
    name: str
    quantity: int
    unit_price: Decimal
    # MRP shown as a strikethrough when present. Sourced from
    # `products.compare_at_price`; absent when the product has no MRP set.
    compare_at_price: Decimal | None = None
    line_subtotal: Decimal
    line_tax: Decimal
    line_total: Decimal


class CartRead(AppSchema):
    items: list[CartItemRead]
    subtotal: Decimal
    tax_amount: Decimal
    discount_amount: Decimal
    total: Decimal
    coupon_code: str | None = None
    currency: str = "INR"


class CouponApplyRequest(AppSchema):
    code: str

    @field_validator("code")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()
