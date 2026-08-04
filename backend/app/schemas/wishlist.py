from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict
from app.schemas.base import AppSchema


class WishlistItemRead(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    name: str
    price: Decimal
    image_url: str | None
    created_at: datetime


class WishlistAdd(AppSchema):
    product_id: int
