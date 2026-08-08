from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field, model_validator
from app.schemas.base import AppSchema


class ReviewBase(AppSchema):
    rating: int = Field(ge=1, le=5)
    title: str | None = Field(default=None, max_length=160)
    body: str | None = None


class ReviewCreate(ReviewBase):
    """Body used by signed-in users to submit a review."""


class ReviewUpdate(AppSchema):
    rating: int | None = Field(default=None, ge=1, le=5)
    title: str | None = Field(default=None, max_length=160)
    body: str | None = None


class AdminReviewCreate(ReviewBase):
    """Admin-only create. Author may be a real user (user_id) or a free-text
    name (author_name). At least one must be present; UI typically uses
    `author_name` for seeded reviews."""

    product_id: int
    user_id: int | None = None
    author_name: str | None = Field(default=None, max_length=120)
    is_verified_purchase: bool = False
    is_approved: bool = True

    @model_validator(mode="after")
    def _author_required(self) -> "AdminReviewCreate":
        if self.user_id is None and not (self.author_name and self.author_name.strip()):
            raise ValueError("Either user_id or author_name is required")
        return self


class AdminReviewUpdate(AppSchema):
    rating: int | None = Field(default=None, ge=1, le=5)
    author_name: str | None = Field(default=None, max_length=120)
    title: str | None = Field(default=None, max_length=160)
    body: str | None = None
    is_verified_purchase: bool | None = None
    is_approved: bool | None = None


class ReviewRead(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    user_id: int | None
    rating: int
    title: str | None
    body: str | None
    is_verified_purchase: bool
    is_approved: bool
    helpful_count: int
    # Resolved display name — prefers user.full_name, falls back to author_name,
    # then to a generic placeholder so the UI never has to guess.
    author_display: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_review(cls, review) -> "ReviewRead":
        # Done in code (not as a computed_field) so we can choose the display
        # without exposing the underlying user object on the wire.
        if review.user_id and review.user is not None:
            author = review.user.full_name or review.user.email or "Customer"
        elif review.author_name:
            author = review.author_name
        else:
            author = "Anonymous"
        return cls(
            id=review.id,
            product_id=review.product_id,
            user_id=review.user_id,
            rating=review.rating,
            title=review.title,
            body=review.body,
            is_verified_purchase=review.is_verified_purchase,
            is_approved=review.is_approved,
            helpful_count=review.helpful_count,
            author_display=author,
            created_at=review.created_at,
            updated_at=review.updated_at,
        )


class ReviewListPage(AppSchema):
    items: list[ReviewRead]
    total: int
    page: int
    page_size: int


class ProductRatingSummary(AppSchema):
    """Compact rating block — used by storefront for the histogram + stars."""

    rating_avg: Decimal
    rating_count: int
    distribution: dict[str, int]
