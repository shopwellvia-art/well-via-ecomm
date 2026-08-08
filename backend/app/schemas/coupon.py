from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field, field_validator

from app.models.coupon import DiscountType
from app.schemas.base import AppSchema


class CouponCreate(AppSchema):
    code: str = Field(min_length=2, max_length=64)
    description: str | None = None
    discount_type: DiscountType
    discount_value: Decimal = Field(gt=0)
    min_order_amount: Decimal | None = Field(default=None, ge=0)
    max_discount: Decimal | None = Field(default=None, ge=0)
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    usage_limit: int | None = Field(default=None, ge=1)
    per_user_limit: int | None = Field(default=None, ge=1)
    is_active: bool = True

    @field_validator("code")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class CouponUpdate(AppSchema):
    description: str | None = None
    discount_type: DiscountType | None = None
    discount_value: Decimal | None = Field(default=None, gt=0)
    min_order_amount: Decimal | None = Field(default=None, ge=0)
    max_discount: Decimal | None = Field(default=None, ge=0)
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    usage_limit: int | None = Field(default=None, ge=1)
    per_user_limit: int | None = Field(default=None, ge=1)
    is_active: bool | None = None


class CouponRead(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    description: str | None
    discount_type: DiscountType
    discount_value: Decimal
    min_order_amount: Decimal | None
    max_discount: Decimal | None
    starts_at: datetime | None
    expires_at: datetime | None
    usage_limit: int | None
    usage_count: int
    per_user_limit: int | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CouponApply(AppSchema):
    code: str

    @field_validator("code")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class CouponValidationResult(AppSchema):
    code: str
    discount_amount: Decimal
    description: str | None = None
