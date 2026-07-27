"""Review CRUD + product aggregate recompute.

The product's denormalized rating_avg/rating_count/rating_distribution columns
are recomputed inside the same transaction as every review mutation, so the
storefront's listing/card queries can read them in one row without joining
back to reviews.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Product
from app.models.review import Review
from app.models.user import User
from app.repositories.product_repository import ProductRepository
from app.repositories.review_repository import ReviewRepository
from app.services.settings_service import SettingsService

# Order states that mean the purchase "really happened" — the same
# committed-states semantics CODService._is_first_time_customer uses. Every
# order (prepaid AND COD) must pass through PAID before it can be shipped
# (see order_service transition map), so PAID is the earliest point at which
# the purchase is committed. PENDING/CANCELLED are abandoned or reversed
# checkouts; REFUNDED means the money went back, so it doesn't verify.
_COMMITTED_ORDER_STATES = (
    OrderStatus.PAID,
    OrderStatus.SHIPPED,
    OrderStatus.DELIVERED,
)

# Moderation switch. "true" (the default when the setting row is absent)
# keeps today's behavior: user reviews publish immediately. "false" routes
# new user reviews into the admin moderation queue (is_approved=False).
_AUTO_APPROVE_KEY = "reviews.auto_approve"


class ReviewService:
    def __init__(self, db: Session):
        self.db = db
        self.reviews = ReviewRepository(db)
        self.products = ProductRepository(db)

    # ---- public / user-facing reads ----

    def list_for_product(
        self,
        product_id: int,
        *,
        offset: int,
        limit: int,
        sort: str,
        approved_only: bool = True,
    ) -> tuple[list[Review], int]:
        return self.reviews.list_for_product(
            product_id,
            offset=offset,
            limit=limit,
            sort=sort,
            approved_only=approved_only,
        )

    # ---- user actions ----

    def create_for_user(
        self,
        user: User,
        product_id: int,
        rating: int,
        title: str | None,
        body: str | None,
    ) -> Review:
        product = self.products.get(product_id)
        if not product:
            raise NotFoundError("Product not found")
        if self.reviews.find_user_review(product_id, user.id):
            raise ConflictError("You have already reviewed this product")
        review = Review(
            product_id=product_id,
            user_id=user.id,
            rating=rating,
            title=title,
            body=body,
            # Derived from the author's order history: a committed order
            # (PAID/SHIPPED/DELIVERED) containing this product marks the
            # review as a verified purchase. Admins can still flip it.
            is_verified_purchase=self._has_purchased(user.id, product_id),
            # Auto-approve unless moderation is switched on. Unapproved
            # reviews sit in the existing admin queue (GET /reviews/admin
            # ?approved=false) and are excluded from the public listing and
            # the product's rating aggregates until an admin approves them.
            is_approved=SettingsService(self.db).get_bool(
                _AUTO_APPROVE_KEY, default=True
            ),
        )
        self.reviews.add(review)
        self.db.flush()  # so review.id is set before loyalty references it
        self._recompute(product)
        self._award_loyalty_points(review)
        self.db.commit()
        return self.reviews.get(review.id)  # type: ignore[return-value]

    def _award_loyalty_points(self, review) -> None:
        """Hand the review to the loyalty engine. Lazy import keeps the
        module dependency direction clean."""
        from app.services.loyalty_service import LoyaltyService
        import logging

        try:
            LoyaltyService(self.db).award_for_review(review)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "loyalty award failed for review %s: %s", review.id, exc
            )

    def update_own(
        self,
        user: User,
        review_id: int,
        *,
        rating: int | None,
        title: str | None,
        body: str | None,
    ) -> Review:
        review = self._must_get(review_id)
        if review.user_id != user.id and not user.is_admin:
            raise ForbiddenError("You can only edit your own review")
        if rating is not None:
            review.rating = rating
        if title is not None:
            review.title = title
        if body is not None:
            review.body = body
        self._recompute(self.products.get(review.product_id))
        self.db.commit()
        return self.reviews.get(review_id)  # type: ignore[return-value]

    def delete_own(self, user: User, review_id: int) -> None:
        review = self._must_get(review_id)
        if review.user_id != user.id and not user.is_admin:
            raise ForbiddenError("You can only delete your own review")
        product_id = review.product_id
        self.reviews.delete(review)
        self._recompute(self.products.get(product_id))
        self.db.commit()

    # ---- admin actions ----

    def list_admin(
        self,
        *,
        q: str | None,
        product_id: int | None,
        rating: int | None,
        approved: bool | None,
        offset: int,
        limit: int,
    ) -> tuple[list[Review], int]:
        return self.reviews.list_admin(
            q=q,
            product_id=product_id,
            rating=rating,
            approved=approved,
            offset=offset,
            limit=limit,
        )

    def admin_create(
        self,
        *,
        product_id: int,
        rating: int,
        author_name: str | None,
        title: str | None,
        body: str | None,
        is_verified_purchase: bool,
        is_approved: bool,
        user_id: int | None = None,
    ) -> Review:
        product = self.products.get(product_id)
        if not product:
            raise NotFoundError("Product not found")
        if user_id is not None and self.reviews.find_user_review(product_id, user_id):
            raise ConflictError("That user has already reviewed this product")
        review = Review(
            product_id=product_id,
            user_id=user_id,
            author_name=author_name or None,
            rating=rating,
            title=title,
            body=body,
            is_verified_purchase=is_verified_purchase,
            is_approved=is_approved,
        )
        self.reviews.add(review)
        self._recompute(product)
        self.db.commit()
        return self.reviews.get(review.id)  # type: ignore[return-value]

    def admin_update(
        self,
        review_id: int,
        *,
        rating: int | None = None,
        author_name: str | None = None,
        title: str | None = None,
        body: str | None = None,
        is_verified_purchase: bool | None = None,
        is_approved: bool | None = None,
    ) -> Review:
        review = self._must_get(review_id)
        if rating is not None:
            review.rating = rating
        if author_name is not None:
            review.author_name = author_name or None
        if title is not None:
            review.title = title
        if body is not None:
            review.body = body
        if is_verified_purchase is not None:
            review.is_verified_purchase = is_verified_purchase
        if is_approved is not None:
            review.is_approved = is_approved
        self._recompute(self.products.get(review.product_id))
        self.db.commit()
        return self.reviews.get(review_id)  # type: ignore[return-value]

    def admin_delete(self, review_id: int) -> None:
        review = self._must_get(review_id)
        product_id = review.product_id
        self.reviews.delete(review)
        self._recompute(self.products.get(product_id))
        self.db.commit()

    # ---- internals ----

    def _has_purchased(self, user_id: int, product_id: int) -> bool:
        """One EXISTS query: does this user have a committed order
        (PAID/SHIPPED/DELIVERED) containing this product?"""
        stmt = select(
            exists().where(
                Order.user_id == user_id,
                Order.status.in_(_COMMITTED_ORDER_STATES),
                OrderItem.order_id == Order.id,
                OrderItem.product_id == product_id,
            )
        )
        return bool(self.db.execute(stmt).scalar())

    def _must_get(self, review_id: int) -> Review:
        review = self.reviews.get(review_id)
        if not review:
            raise NotFoundError("Review not found")
        return review

    def _recompute(self, product: Product | None) -> None:
        if product is None:
            return
        count, avg, distribution = self.reviews.aggregate_for_product(product.id)
        product.rating_count = count
        product.rating_avg = (
            Decimal(str(avg or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )
        product.rating_distribution = distribution if count else None
        self.db.flush()
