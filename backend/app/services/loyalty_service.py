"""Loyalty engine — the points ledger.

Single entry points:
  - award(...)           — idempotent earn from a domain event
  - redeem(...)          — turn points into a one-time coupon
  - reverse_for_order(...)  — refund-driven reversal
  - admin_adjust(...)    — manual correction with paper trail

Every mutation writes one row to `points_transactions` and updates the cached
totals on `users` in the same transaction. The unique index on
`(reason, ref_type, ref_id)` makes retried webhooks safe — the second call is
a no-op.
"""
from __future__ import annotations

import logging
import secrets
import string
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

import redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.redis import get_redis
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.models.coupon import Coupon, DiscountType
from app.models.customer import Customer
from app.models.loyalty import EarnRule, PointsReason, PointsTransaction, RedemptionTier, VipTier
from app.models.order import Order
from app.models.review import Review
from app.models.user import User
from app.repositories.earn_rule_repository import EarnRuleRepository
from app.repositories.loyalty_repository import (
    PointsTransactionRepository,
    RedemptionTierRepository,
)
from app.repositories.user_repository import UserRepository
from app.repositories.vip_tier_repository import VipTierRepository
from app.services import loyalty_config

logger = logging.getLogger(__name__)


def _coupon_code() -> str:
    """Random REW-XXXXXX — distinct prefix so customers can spot a loyalty
    reward in their inbox, and admin lists can filter by it visually."""
    alnum = string.ascii_uppercase + string.digits
    return "REW-" + "".join(secrets.choice(alnum) for _ in range(6))


class LoyaltyService:
    def __init__(self, db: Session, redis_client: redis.Redis | None = None):
        self.db = db
        self.tx_repo = PointsTransactionRepository(db)
        self.tier_repo = RedemptionTierRepository(db)
        self.rule_repo = EarnRuleRepository(db)
        self.vip_repo = VipTierRepository(db)
        self.users = UserRepository(db)
        self.redis = redis_client or get_redis()
        # Per-instance rule cache — avoids repeat DB hits when one request
        # triggers multiple awards (rare, but free).
        self._rule_cache: dict[str, int] = {}

    # ---- Rule lookups ----

    def _rule_points(self, key: str, fallback: int) -> int | None:
        """Returns the configured points value for an earn rule, or None when
        the rule is inactive. Falls back to the v1 hardcoded constant if the
        rule row is missing (e.g. fresh DB before the v3 seed)."""
        if key in self._rule_cache:
            cached = self._rule_cache[key]
            return cached if cached >= 0 else None
        rule = self.rule_repo.get_by_key(key)
        if rule is None:
            self._rule_cache[key] = fallback
            return fallback
        if not rule.is_active:
            self._rule_cache[key] = -1
            return None
        self._rule_cache[key] = rule.points_value
        return rule.points_value

    # ---- Reads ----

    def get_balance(self, user_id: int) -> int:
        customer = self.users.get_customer(user_id)
        return customer.points_balance if customer else 0

    def list_transactions(
        self, user_id: int, *, offset: int = 0, limit: int = 50
    ) -> tuple[list[PointsTransaction], int]:
        return self.tx_repo.list_for_user(user_id, offset=offset, limit=limit)

    def list_tiers(self, *, active_only: bool = False) -> list[RedemptionTier]:
        return self.tier_repo.list_all(active_only=active_only)

    def recompute_balance(self, user_id: int) -> int:
        """Rebuild the cached balance from the ledger. Diagnostic — should not
        be needed in normal flow; useful after manual SQL surgery."""
        user = self.users.get(user_id)
        if not user:
            raise NotFoundError("User not found")
        customer = self.users.get_or_create_customer(user_id)
        customer.points_balance = self.tx_repo.sum_for_user(user_id)
        self.db.flush()
        return customer.points_balance

    # ---- Earn (idempotent) ----

    def award_signup_bonus(self, user: User) -> PointsTransaction | None:
        amount = self._rule_points("signup_bonus", loyalty_config.SIGNUP_BONUS)
        if not amount:
            return None
        return self._award(
            user_id=user.id,
            delta=amount,
            reason=PointsReason.SIGNUP_BONUS,
            # Use the user id as the ref so the unique index makes this
            # genuinely once-per-user.
            ref_type="user",
            ref_id=user.id,
            description="Welcome bonus",
        )

    def award_for_order(self, order: Order) -> PointsTransaction | None:
        # Earn on the post-discount slice — points shouldn't fund a discount.
        earn_base = Decimal(order.subtotal or 0) - Decimal(order.discount_amount or 0)
        if earn_base <= 0:
            return None
        rate = self._rule_points("place_order", int(loyalty_config.POINTS_PER_CURRENCY_UNIT))
        if not rate:
            return None
        points = int(earn_base * Decimal(rate))
        if points <= 0:
            return None
        return self._award(
            user_id=order.user_id,
            delta=points,
            reason=PointsReason.PLACE_ORDER,
            ref_type="order",
            ref_id=order.id,
            description=f"Order #{order.id}",
        )

    def award_for_review(self, review: Review) -> PointsTransaction | None:
        # Admin-entered reviews (user_id null) don't earn anything.
        if review.user_id is None:
            return None
        amount = self._rule_points("write_review", loyalty_config.REVIEW_POINTS)
        if not amount:
            return None
        return self._award(
            user_id=review.user_id,
            delta=amount,
            reason=PointsReason.WRITE_REVIEW,
            ref_type="review",
            ref_id=review.id,
            description=f"Review on product #{review.product_id}",
        )

    # ---- Reverse ----

    def reverse_for_order(self, order: Order) -> PointsTransaction | None:
        """Cancel the points awarded for this order (used on refund/cancel).

        Looks up the original PLACE_ORDER row and writes the opposite delta.
        Safe to call repeatedly — the (reason, ref_type, ref_id) unique index
        on REFUND_REVERSAL keeps it idempotent.
        """
        original = self.tx_repo.find_by_ref(
            PointsReason.PLACE_ORDER, "order", order.id
        )
        if not original or original.delta <= 0:
            return None
        return self._award(
            user_id=order.user_id,
            delta=-original.delta,
            reason=PointsReason.REFUND_REVERSAL,
            ref_type="order",
            ref_id=order.id,
            description=f"Refund of order #{order.id}",
            # lifetime_points should not move on reversal; lifetime tracks gross
            # earning so VIP eligibility doesn't oscillate.
            affects_lifetime=False,
        )

    # ---- Redeem ----

    def redeem(self, user: User, tier_id: int) -> Coupon:
        """Trade points for a coupon. Atomic: per-user Redis lock around the
        balance check + ledger write so two concurrent clicks can't both spend
        the same points.
        """
        tier = self.tier_repo.get(tier_id)
        if not tier or not tier.is_active:
            raise NotFoundError("Reward not available")

        lock_key = f"loyalty:redeem:{user.id}"
        # 5s is plenty for a single redeem round-trip; auto-expires so we don't
        # strand a key if the worker dies mid-flight.
        if not self.redis.set(lock_key, "1", nx=True, ex=5):
            raise ConflictError("Another redemption is already in progress")

        try:
            fresh = self.users.get(user.id)
            if not fresh:
                raise NotFoundError("User not found")
            customer = self.users.get_or_create_customer(user.id)
            if customer.points_balance < tier.cost_points:
                raise ValidationError(
                    f"Not enough points — need {tier.cost_points}, have {customer.points_balance}"
                )

            coupon = self._mint_loyalty_coupon(fresh, tier)
            self.db.flush()  # we need coupon.id for the ledger ref

            tx = self._award(
                user_id=fresh.id,
                delta=-tier.cost_points,
                reason=PointsReason.REDEEM,
                ref_type="coupon",
                ref_id=coupon.id,
                description=f"Redeemed: {tier.name}",
                affects_lifetime=False,  # spending shouldn't reduce lifetime
            )
            if tx is None:
                # Shouldn't happen — a brand-new coupon id can't clash — but
                # if it does, refuse rather than silently lose the coupon row.
                self.db.rollback()
                raise ConflictError("Redemption could not be recorded")

            self.db.commit()
            self.db.refresh(coupon)
            return coupon
        finally:
            try:
                self.redis.delete(lock_key)
            except Exception:  # best-effort lock cleanup
                logger.warning("could not release redeem lock %s", lock_key)

    # ---- Admin ----

    def admin_adjust(
        self,
        admin: User,
        target_user_id: int,
        delta: int,
        description: str,
    ) -> PointsTransaction:
        if delta == 0:
            raise ValidationError("Delta cannot be zero")
        if not description.strip():
            raise ValidationError("Provide a reason — adjustments require a paper trail")
        target = self.users.get(target_user_id)
        if not target:
            raise NotFoundError("User not found")

        tx = self._award(
            user_id=target_user_id,
            delta=delta,
            reason=PointsReason.ADMIN_ADJUST,
            ref_type=None,
            ref_id=None,
            description=f"[admin {admin.email}] {description.strip()}",
            # Manual adjustments deliberately don't move lifetime so we can't
            # silently shove a customer into a higher VIP tier.
            affects_lifetime=False,
        )
        # admin_adjust leaves ref null on purpose so multiple rows allowed —
        # the unique index won't reject it. _award returns the new row.
        if tx is None:  # defensive
            raise ConflictError("Adjustment could not be recorded")
        self.db.commit()
        return tx

    # ---- Internals ----

    def _award(
        self,
        *,
        user_id: int,
        delta: int,
        reason: PointsReason,
        ref_type: str | None,
        ref_id: int | None,
        description: str | None,
        affects_lifetime: bool = True,
    ) -> PointsTransaction | None:
        """Insert one ledger row + update cached totals. Idempotent for rows
        carrying a (ref_type, ref_id). Returns None when the row already exists
        (so callers can treat the duplicate as a no-op)."""
        # Pre-check is a courtesy — saves a round-trip in the common dedupe
        # case. The unique index is the real guarantee.
        if ref_type is not None and ref_id is not None:
            existing = self.tx_repo.find_by_ref(reason, ref_type, ref_id)
            if existing is not None:
                return None

        customer = self.users.get_or_create_customer(user_id)

        # VIP multiplier — apply to positive earns only, skip expiry / reversal.
        # Refund reversals and expiry should remove EXACTLY what was previously
        # awarded, not the multiplied value (we already credited the multiplied
        # amount when it was earned).
        multiplied = delta
        if (
            delta > 0
            and reason
            not in (PointsReason.REFUND_REVERSAL, PointsReason.EXPIRY)
            and customer.vip_tier is not None
        ):
            mult = Decimal(customer.vip_tier.earn_multiplier or 1)
            if mult != Decimal("1.00"):
                multiplied = int(
                    (Decimal(delta) * mult).quantize(
                        Decimal("1"), rounding=ROUND_HALF_UP
                    )
                )

        # Set expiry on positive earn rows. Negative deltas (spend, reverse,
        # expiry itself, admin debit) don't expire — they ARE the consumption.
        expires_at: datetime | None = None
        if multiplied > 0 and reason not in (PointsReason.REFUND_REVERSAL, PointsReason.EXPIRY):
            expires_at = datetime.now(timezone.utc) + timedelta(
                days=loyalty_config.POINTS_EXPIRY_DAYS
            )

        tx = PointsTransaction(
            user_id=user_id,
            delta=multiplied,
            reason=reason,
            ref_type=ref_type,
            ref_id=ref_id,
            description=description,
            expires_at=expires_at,
        )
        try:
            # SAVEPOINT, not a full rollback: _award runs on the caller's shared
            # session (e.g. inside PaymentService._mark_paid, after order.status
            # was set to PAID but before commit). A plain self.db.rollback() on
            # an idempotency-race IntegrityError would discard those pending
            # writes — the customer pays, gets a confirmation, and the order
            # stays PENDING. begin_nested() confines the rollback to this insert.
            with self.db.begin_nested():
                self.tx_repo.add(tx)
                customer.points_balance = (customer.points_balance or 0) + multiplied
                if affects_lifetime and multiplied > 0:
                    customer.lifetime_points = (customer.lifetime_points or 0) + multiplied
                    # Lifetime moved → may have crossed a tier threshold.
                    self._maybe_promote_tier(customer)
            return tx
        except IntegrityError:
            # Race: another worker beat us to the same idempotency key. The
            # SAVEPOINT already rolled back just this insert; the caller's other
            # pending writes are intact. Treat the duplicate as already-done.
            return None

    def _maybe_promote_tier(self, customer: Customer) -> None:
        """Pick the highest tier whose threshold ≤ customer.lifetime_points and
        cache it on the customer row. Idempotent — re-assigning the same id is a
        no-op."""
        tier = self.vip_repo.find_for_points(customer.lifetime_points or 0)
        new_id = tier.id if tier else None
        if new_id != customer.vip_tier_id:
            customer.vip_tier_id = new_id

    # ---- Expiry ----

    def expire_points(self) -> dict[str, int]:
        """Walk the ledger and write EXPIRY rows for unconsumed portions of
        earn entries past `expires_at`.

        FIFO accounting: spending always consumes the oldest earn first. We
        recompute consumption from scratch rather than tracking per-row
        consumption — simpler, no extra state to corrupt. O(N) per user.

        Idempotent over a (reason, ref_type, ref_id) where ref_type='ledger'
        and ref_id is the *earn* row id — so expiring the same row twice is a
        no-op even if the job re-runs on the same day.
        """
        from sqlalchemy import select

        now = datetime.now(timezone.utc)

        # Pull rows that *could* affect expiry: every row for every user that
        # has at least one expired earn. For a small store this is fine; v3
        # batches per-user.
        users_with_expirable = self.db.execute(
            select(PointsTransaction.user_id)
            .where(
                PointsTransaction.delta > 0,
                PointsTransaction.expires_at != None,  # noqa: E711
                PointsTransaction.expires_at <= now,
            )
            .distinct()
        ).scalars().all()

        summary = {"users_processed": 0, "rows_expired": 0, "points_expired": 0}
        for uid in users_with_expirable:
            n_rows, n_points = self._expire_for_user(uid, now)
            if n_rows > 0:
                summary["users_processed"] += 1
                summary["rows_expired"] += n_rows
                summary["points_expired"] += n_points

        self.db.commit()
        return summary

    def _expire_for_user(self, user_id: int, now: datetime) -> tuple[int, int]:
        """FIFO expiry for one user. Returns (rows_expired, points_expired)."""
        from sqlalchemy import asc, select

        all_rows = list(
            self.db.execute(
                select(PointsTransaction)
                .where(PointsTransaction.user_id == user_id)
                .order_by(asc(PointsTransaction.created_at), asc(PointsTransaction.id))
            )
            .scalars()
            .all()
        )

        # Track unconsumed remaining for each positive (earn) row.
        remaining: dict[int, int] = {}
        earn_order: list[PointsTransaction] = []
        for row in all_rows:
            if row.delta > 0 and row.reason not in (
                PointsReason.EXPIRY,
                PointsReason.REFUND_REVERSAL,
            ):
                remaining[row.id] = row.delta
                earn_order.append(row)

        # Apply every negative row, consuming oldest earns first.
        for row in all_rows:
            if row.delta < 0:
                to_consume = -row.delta
                for earn in earn_order:
                    if to_consume == 0:
                        break
                    take = min(remaining.get(earn.id, 0), to_consume)
                    if take > 0:
                        remaining[earn.id] -= take
                        to_consume -= take
                # to_consume > 0 here is allowed — the user has a negative
                # balance, e.g. from a refund of an expired-row order.

        # Anything still remaining and past expiry needs an EXPIRY row.
        rows_expired = 0
        points_expired = 0
        for earn in earn_order:
            if earn.expires_at is None:
                continue
            # Normalize to UTC-aware for the comparison.
            earn_exp = earn.expires_at
            if earn_exp.tzinfo is None:
                earn_exp = earn_exp.replace(tzinfo=timezone.utc)
            if earn_exp > now:
                continue
            unspent = remaining.get(earn.id, 0)
            if unspent <= 0:
                continue
            # Idempotent: use the earn row's id as the ref so the same earn
            # cannot be expired twice (the unique index enforces it).
            tx = self._award(
                user_id=user_id,
                delta=-unspent,
                reason=PointsReason.EXPIRY,
                ref_type="ledger",
                ref_id=earn.id,
                description=f"Expired earnings from row #{earn.id}",
                affects_lifetime=False,
            )
            if tx is not None:
                rows_expired += 1
                points_expired += unspent
        return rows_expired, points_expired

    def _mint_loyalty_coupon(self, user: User, tier: RedemptionTier) -> Coupon:
        """Issue the one-time coupon tied to this redemption."""
        # 5 attempts to land a unique code. Random 6-char suffix collides at
        # ~1/2B, so this is paranoia. Still, retry rather than crash.
        for _ in range(5):
            code = _coupon_code()
            coupon = Coupon(
                code=code,
                description=f"Loyalty reward — {tier.name}",
                discount_type=(
                    DiscountType.PERCENT
                    if tier.discount_type == "percent"
                    else DiscountType.FIXED
                ),
                discount_value=Decimal(tier.discount_value),
                max_discount=Decimal(tier.max_discount) if tier.max_discount else None,
                expires_at=datetime.now(timezone.utc)
                + timedelta(days=tier.expires_after_days),
                usage_limit=1,
                per_user_limit=1,
                is_active=True,
                is_loyalty_reward=True,
            )
            try:
                self.db.add(coupon)
                self.db.flush()
                return coupon
            except IntegrityError:
                self.db.rollback()
                continue
        raise ConflictError("Could not generate a unique reward code; try again")
