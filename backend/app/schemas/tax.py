from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field
from app.schemas.base import AppSchema


class TaxCreate(AppSchema):
    name: str = Field(min_length=1, max_length=100)
    rate: Decimal = Field(ge=0, le=100)
    is_active: bool = True


class TaxUpdate(AppSchema):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    rate: Decimal | None = Field(default=None, ge=0, le=100)
    is_active: bool | None = None


class TaxRead(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    rate: Decimal
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ProductTaxUpdate(AppSchema):
    tax_ids: list[int]
