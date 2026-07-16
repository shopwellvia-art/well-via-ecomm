"""Coupon CRUD + validation + redemption.

`validate` is read-only; it returns the discount the cart would receive but
does not persist anything. The actual usage record + usage_count increment
happen in `record_usage`, which the payment service calls on PAID.
"""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.coupon import Coupon, CouponUsage, DiscountType
from app.repositories.coupon_repository import CouponRepository, CouponUsageRepository
from app.schemas.coupon import CouponCreate, CouponUpdate
from app.services.tax_service import quantize_money


class CouponService:
    def __init__(self, db: Session):
        self.db = db
        self.coupons = CouponRepository(db)
        self.usages = CouponUsageRepository(db)

    # ---- admin CRUD ----

    def list_all(self) -> list[Coupon]:
        return self.coupons.list_all()

    def get(self, coupon_id: int) -> Coupon:
        c = self.coupons.get(coupon_id)
        if not c:
            raise NotFoundError("Coupon not found")
        return c

    def create(self, data: CouponCreate) -> Coupon:
        if self.coupons.get_by_code(data.code):
            raise ConflictError(f"Coupon '{data.code}' already exists")
        c = Coupon(**data.model_dump())
        self.coupons.add(c)
        self.db.commit()
        return c

    def update(self, coupon_id: int, data: CouponUpdate) -> Coupon:
        c = self.get(coupon_id)
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(c, key, value)
        self.db.commit()
        return c

    def delete(self, coupon_id: int) -> None:
        c = self.get(coupon_id)
        self.coupons.delete(c)
        self.db.commit()

    # ---- runtime ----

    def validate(self, code: str, user_id: int, subtotal: Decimal) -> tuple[Coupon, Decimal]:
        """Returns (coupon, discount_amount). Raises ValidationError if not usable."""
        coupon = self.coupons.get_by_code(code)
        if not coupon or not coupon.is_active:
            raise ValidationError("Invalid coupon code")

        now = datetime.now(timezone.utc)
        if coupon.starts_at and now < coupon.starts_at:
            raise ValidationError("Coupon is not yet active")
        if coupon.expires_at and now > coupon.expires_at:
            raise ValidationError("Coupon has expired")

        if coupon.min_order_amount and subtotal < coupon.min_order_amount:
            raise ValidationError(
                f"Order subtotal must be at least {coupon.min_order_amount}"
            )

        if coupon.usage_limit is not None and coupon.usage_count >= coupon.usage_limit:
            raise ValidationError("Coupon usage limit reached")

        if coupon.per_user_limit is not None:
            used = self.usages.count_for_user(coupon.id, user_id)
            if used >= coupon.per_user_limit:
                raise ValidationError("You have already used this coupon")

        discount = self._compute_discount(coupon, subtotal)
        return coupon, discount

    def record_usage(
        self, coupon: Coupon, user_id: int, order_id: int, discount_amount: Decimal
    ) -> CouponUsage:
        """Called from payment service on PAID (post-payment). Records the usage
        row and atomically bumps usage_count.

        The increment is an atomic expression UPDATE, not a Python
        read-modify-write, so two concurrent settlements never lose a count.

        It deliberately does NOT re-enforce usage_limit here: this runs AFTER
        the gateway has captured payment, so refusing would strand an already
        paid order in PENDING forever (the reconcile cron would re-hit the same
        error each cycle). The cap is enforced earlier, in validate() at cart
        time, before the customer pays. Any residual over-issue from a checkout
        race is bounded and far preferable to losing a paid order — enforce the
        hard cap with a reservation at checkout if that tradeoff is unacceptable.
        """
        self.db.execute(
            update(Coupon)
            .where(Coupon.id == coupon.id)
            .values(usage_count=Coupon.usage_count + 1)
            .execution_options(synchronize_session="fetch")
        )
        usage = CouponUsage(
            coupon_id=coupon.id,
            user_id=user_id,
            order_id=order_id,
            discount_amount=discount_amount,
        )
        self.usages.add(usage)
        self.db.flush()
        return usage

    # ---- internals ----

    @staticmethod
    def _compute_discount(coupon: Coupon, subtotal: Decimal) -> Decimal:
        if coupon.discount_type == DiscountType.FIXED:
            discount = Decimal(coupon.discount_value)
        else:  # PERCENT
            discount = subtotal * Decimal(coupon.discount_value) / Decimal("100")

        if coupon.max_discount is not None:
            discount = min(discount, Decimal(coupon.max_discount))
        # Never discount more than the cart subtotal.
        discount = min(discount, subtotal)
        return quantize_money(discount)
