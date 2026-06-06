from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProductImageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    position: int
    is_primary: bool


class ProductTaxBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    rate: Decimal


class ProductBase(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    price: Decimal = Field(ge=0, decimal_places=2)
    # When present, must be strictly greater than `price` — otherwise there's
    # no "discount" to show and the data is misleading.
    compare_at_price: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    # Unit procurement / production cost. Used for contribution-margin analytics.
    # Nullable so existing products without a recorded cost stay valid.
    cost: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    stock: int = Field(ge=0, default=0)
    # Optional shipping weight in grams. Drives the rate calculator; null
    # falls back to a 200g default at quote time.
    weight_grams: int | None = Field(default=None, ge=0, le=200_000)
    # When true, any cart containing this product disables COD at checkout
    # (high-value or fragile items where RTO would be unacceptable).
    cod_blocked: bool = False
    image_url: str | None = None
    category_id: int | None = None

    @model_validator(mode="after")
    def _compare_at_must_exceed_price(self) -> "ProductBase":
        if self.compare_at_price is not None and self.compare_at_price <= self.price:
            raise ValueError("compare_at_price must be greater than price")
        return self


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    price: Decimal | None = None
    compare_at_price: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    # Cost update mirrors compare_at_price: optional, non-negative, 2dp.
    cost: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    stock: int | None = None
    weight_grams: int | None = Field(default=None, ge=0, le=200_000)
    cod_blocked: bool | None = None
    image_url: str | None = None
    category_id: int | None = None


class ProductRead(ProductBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    images: list[ProductImageRead] = []
    taxes: list[ProductTaxBrief] = []
    # Denormalized rating aggregates — set by ReviewService. Defaults make
    # responses for new products consistent (avg=0, count=0, no histogram).
    rating_avg: Decimal = Decimal("0.00")
    rating_count: int = 0
    rating_distribution: dict[str, int] | None = None
    created_at: datetime
    updated_at: datetime
