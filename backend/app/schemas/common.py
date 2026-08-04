from typing import Generic, TypeVar

from pydantic import BaseModel, Field
from app.schemas.base import AppSchema

T = TypeVar("T")


class PaginationParams(AppSchema):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class Token(AppSchema):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
