from pydantic import ConfigDict, Field
from app.schemas.base import AppSchema


class CategoryCreate(AppSchema):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, max_length=140)
    parent_id: int | None = None


class CategoryUpdate(AppSchema):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, max_length=140)
    parent_id: int | None = None


class CategoryRead(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    image_url: str | None = None
    parent_id: int | None = None
