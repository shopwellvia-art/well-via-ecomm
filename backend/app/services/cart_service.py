"""Redis-backed cart. Stateless across web workers.

Items hash:  cart:{user_id}        -> { product_id: quantity }
Coupon key:  cart:{user_id}:coupon -> coupon code (string)

The coupon is held alongside the items so a page refresh still shows the
applied discount. We *validate* on every read in case the coupon expired,
was deactivated, or no longer meets the min-order threshold — silently
dropping it in that case so the cart stays consistent.
"""
from __future__ import annotations

import logging
from decimal import Decimal

import redis
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.redis import get_redis
from app.core.exceptions import NotFoundError, ValidationError
from app.repositories.product_repository import ProductRepository
from app.schemas.cart import CartItemIn, CartItemRead, CartRead
from app.services.coupon_service import CouponService
from app.services.tax_service import compute_line_tax, quantize_money

logger = logging.getLogger(__name__)


def _items_key(user_id: int) -> str:
    return f"cart:{user_id}"


def _coupon_key(user_id: int) -> str:
    return f"cart:{user_id}:coupon"


class CartService:
    def __init__(self, db: Session, redis_client: redis.Redis | None = None):
        self.db = db
        self.products = ProductRepository(db)
        self.redis = redis_client or get_redis()

    # ---- mutations ----

    def add_item(self, user_id: int, item: CartItemIn) -> None:
        product = self.products.get(item.product_id)
        if not product:
            raise NotFoundError("Product not found")
        self.redis.hincrby(_items_key(user_id), str(item.product_id), item.quantity)

    def remove_item(self, user_id: int, product_id: int) -> None:
        self.redis.hdel(_items_key(user_id), str(product_id))

    def set_quantity(self, user_id: int, product_id: int, quantity: int) -> None:
        """Absolute set of a cart line's quantity. Quantity 0 removes the
        line — saves the UI from having to switch between PUT/DELETE on a
        stepper. Validates the product exists so a stale UI can't push
        ghost ids into the cart hash."""
        if quantity < 0:
            raise ValidationError("Quantity cannot be negative.")
        if quantity == 0:
            self.redis.hdel(_items_key(user_id), str(product_id))
            return
        product = self.products.get(product_id)
        if not product:
            raise NotFoundError("Product not found")
        self.redis.hset(_items_key(user_id), str(product_id), quantity)

    def clear(self, user_id: int) -> None:
        self.redis.delete(_items_key(user_id))
        self.redis.delete(_coupon_key(user_id))

    def apply_coupon(self, user_id: int, code: str) -> CartRead:
        # Build subtotal first so we can validate the coupon against it.
        items, subtotal, tax_amount = self._collect_items(user_id)
        coupon, _discount = CouponService(self.db).validate(code, user_id, subtotal)
        self.redis.set(_coupon_key(user_id), coupon.code)
        return self.get(user_id)

    def remove_coupon(self, user_id: int) -> CartRead:
        self.redis.delete(_coupon_key(user_id))
        return self.get(user_id)

    # ---- reads ----

    def get(self, user_id: int) -> CartRead:
        items, subtotal, tax_amount = self._collect_items(user_id)

        discount_amount = Decimal("0.00")
        coupon_code: str | None = self.redis.get(_coupon_key(user_id))  # type: ignore[assignment]
        if coupon_code:
            try:
                _coupon, discount_amount = CouponService(self.db).validate(
                    coupon_code, user_id, subtotal
                )
            except ValidationError:
                # Coupon went stale (expired / deactivated / no longer meets min).
                # Drop it silently so the cart total stays sane.
                logger.info("dropping stale coupon %s for user %s", coupon_code, user_id)
                self.redis.delete(_coupon_key(user_id))
                coupon_code = None
                discount_amount = Decimal("0.00")

        total = quantize_money(subtotal + tax_amount - discount_amount)
        return CartRead(
            items=items,
            subtotal=subtotal,
            tax_amount=tax_amount,
            discount_amount=discount_amount,
            total=total,
            coupon_code=coupon_code,
        )

    # ---- internals ----

    def _collect_items(self, user_id: int) -> tuple[list[CartItemRead], Decimal, Decimal]:
        raw: dict[str, str] = self.redis.hgetall(_items_key(user_id))  # type: ignore[assignment]
        items: list[CartItemRead] = []
        subtotal = Decimal("0.00")
        tax_total = Decimal("0.00")
        for pid_str, qty_str in raw.items():
            product = self.products.get(int(pid_str))
            if not product:
                continue
            qty = int(qty_str)
            line_subtotal = quantize_money(product.price * qty)
            line_tax = compute_line_tax(product.price, qty, list(product.taxes))
            subtotal += line_subtotal
            tax_total += line_tax
            items.append(
                CartItemRead(
                    product_id=product.id,
                    name=product.name,
                    quantity=qty,
                    unit_price=product.price,
                    compare_at_price=product.compare_at_price,
                    line_subtotal=line_subtotal,
                    line_tax=line_tax,
                    line_total=quantize_money(line_subtotal + line_tax),
                )
            )
        return items, subtotal, tax_total
