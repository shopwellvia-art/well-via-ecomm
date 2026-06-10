"""Referral program — two-sided rewards.

Flow:
  1. Each user has a persistent `referral_code` (lazily assigned).
  2. New customer signs up with `referral_code=...` on the register payload.
     We create a `Referral(status=PENDING)` and mint the friend-welcome coupon
     so they have something to use immediately.
  3. When that referred user's first order reaches PAID, we move the referral
     to COMPLETED and mint the referrer's reward coupon.

Hooks:
  - AuthService.register   -> register_referred_user(...)
  - PaymentService.PAID    -> complete_referral_for_user(user_id, order)
"""
from __future__ import annotations

import logging
import secrets
import string
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError
from app.models.coupon import Coupon, DiscountType
from app.models.customer import Customer
from app.models.referral import Referral, ReferralStatus
from app.models.user import User
from app.repositories.referral_repository import ReferralRepository
from app.repositories.user_repository import UserRepository
from app.services import loyalty_config

logger = logging.getLogger(__name__)


def _random_suffix(n: int = 6) -> str:
    alnum = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alnum) for _ in range(n))


class ReferralService:
    def __init__(self, db: Session):
        self.db = db
        self.referrals = ReferralRepository(db)
        self.users = UserRepository(db)

    # ---- Code management ----

    def get_or_create_code(self, user: User) -> str:
        # The referral code now lives on the customer satellite.
        customer = self.users.get_or_create_customer(user.id)
        if customer.referral_code:
            return customer.referral_code
        # Up to 5 attempts to dodge a unique-collision.
        for _ in range(5):
            candidate = f"REF-{_random_suffix(6)}"
            customer.referral_code = candidate
            try:
                self.db.flush()
                return candidate
            except IntegrityError:
                self.db.rollback()
                # Re-fetch — rollback expires session state.
                customer = self.users.get_or_create_customer(user.id)
                continue
        raise ConflictError("Could not assign a referral code; try again")

    def find_referrer(self, code: str) -> User | None:
        cleaned = (code or "").strip().upper()
        if not cleaned:
            return None
        from sqlalchemy import select

        # Referral codes live on customers; join back to the owning user.
        stmt = (
            select(User)
            .join(Customer, Customer.user_id == User.id)
            .where(Customer.referral_code == cleaned)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    # ---- Lifecycle ----

    def register_referred_user(
        self, referred_user: User, referrer_code: str
    ) -> Referral | None:
        """Called from AuthService.register when the new user provided a
        referral code. Creates the pending referral and mints the friend's
        welcome coupon. No-op (returns None) on any failure mode — referral
        should never block account creation."""
        referrer = self.find_referrer(referrer_code)
        if referrer is None:
            logger.info("referral code not found: %s", referrer_code)
            return None
        if referrer.id == referred_user.id:
            # Self-referral attempt — silently ignore.
            return None
        # The friend can only be referred once (DB also enforces it).
        existing = self.referrals.find_by_referred(referred_user.id)
        if existing is not None:
            return existing

        referral = Referral(
            referrer_user_id=referrer.id,
            referred_user_id=referred_user.id,
            code=referrer.referral_code or referrer_code.strip().upper(),
            status=ReferralStatus.PENDING,
        )
        try:
            self.referrals.add(referral)
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            return self.referrals.find_by_referred(referred_user.id)

        # Mint the friend's welcome coupon — fire and remember; if it fails the
        # referral still stands and an admin can manually issue one later.
        try:
            self._mint_coupon(
                user=referred_user,
                amount=loyalty_config.FRIEND_WELCOME_DISCOUNT_AMOUNT,
                min_order=loyalty_config.FRIEND_WELCOME_MIN_ORDER,
                prefix=loyalty_config.FRIEND_WELCOME_COUPON_PREFIX,
                valid_days=loyalty_config.FRIEND_WELCOME_VALID_DAYS,
                description="Friend welcome — referred by a friend",
            )
        except Exception as exc:
            logger.warning(
                "friend-welcome coupon mint failed for user %s: %s",
                referred_user.id,
                exc,
            )

        return referral

    def complete_referral_for_user(self, user_id: int, order_id: int) -> Referral | None:
        """Called from PaymentService on PAID. If the user was referred and the
        referral is still pending, mark it complete and mint the referrer's
        reward coupon. Idempotent — repeated calls return the existing row."""
        referral = self.referrals.find_by_referred(user_id)
        if referral is None or referral.status != ReferralStatus.PENDING:
            return referral

        referrer = self.users.get(referral.referrer_user_id)
        if referrer is None:
            # Referrer was deleted — leave the row pending so a human can spot it.
            return referral

        referral.status = ReferralStatus.COMPLETED
        referral.completed_at = datetime.now(timezone.utc)
        referral.completed_order_id = order_id

        try:
            self._mint_coupon(
                user=referrer,
                amount=loyalty_config.REFERRER_REWARD_DISCOUNT_AMOUNT,
                min_order=loyalty_config.REFERRER_REWARD_MIN_ORDER,
                prefix=loyalty_config.REFERRER_REWARD_COUPON_PREFIX,
                valid_days=loyalty_config.REFERRER_REWARD_VALID_DAYS,
                description=f"Referrer reward — friend completed order #{order_id}",
            )
        except Exception as exc:
            logger.warning(
                "referrer reward coupon mint failed for user %s: %s",
                referrer.id,
                exc,
            )

        self.db.flush()
        return referral

    # ---- Reads ----

    def list_for_referrer(
        self, referrer_id: int, *, offset: int = 0, limit: int = 50
    ) -> tuple[list[Referral], int]:
        return self.referrals.list_by_referrer(
            referrer_id, offset=offset, limit=limit
        )

    def stats_for_referrer(self, referrer_id: int) -> tuple[int, int]:
        return self.referrals.count_for_referrer(referrer_id)

    def list_admin(
        self,
        *,
        status: ReferralStatus | None,
        offset: int,
        limit: int,
    ) -> tuple[list[Referral], int]:
        return self.referrals.list_admin(status=status, offset=offset, limit=limit)

    # ---- Internals ----

    def _mint_coupon(
        self,
        *,
        user: User,
        amount: Decimal,
        min_order: Decimal | None,
        prefix: str,
        valid_days: int,
        description: str,
    ) -> Coupon:
        """Mint a one-time, per-user, fixed-discount coupon and add it to the
        session. Caller wraps in the outer transaction.
        """
        for _ in range(5):
            code = f"{prefix}-{_random_suffix(6)}"
            coupon = Coupon(
                code=code,
                description=description,
                discount_type=DiscountType.FIXED,
                discount_value=Decimal(amount),
                min_order_amount=Decimal(min_order) if min_order is not None else None,
                expires_at=datetime.now(timezone.utc)
                + timedelta(days=valid_days),
                usage_limit=1,
                per_user_limit=1,
                is_active=True,
                # Reuse the v1 "loyalty reward" flag — these coupons are also
                # system-issued and shouldn't clutter the admin promo list.
                is_loyalty_reward=True,
            )
            try:
                self.db.add(coupon)
                self.db.flush()
                return coupon
            except IntegrityError:
                self.db.rollback()
                continue
        raise ConflictError("Could not mint referral coupon — try again")
