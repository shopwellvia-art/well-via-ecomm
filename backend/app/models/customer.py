"""Customer profile + loyalty totals, split 1:1 off `users`.

`users` stays authentication-only (email, phone, password, flags). Everything a
shopper *is* — their name, gender, date of birth, profile image — plus the
denormalized loyalty engine state (points, referral code, VIP tier) lives here.

1:1 with users via a dedicated `user_id` (UNIQUE, FK -> users.id, ON DELETE
CASCADE). Unlike `user_security` (shared PK, created lazily), a customer row is
created *eagerly* for every user at registration and backfilled for all existing
users — every account is a customer, so there is always exactly one row.

Account lifecycle lives here too: `account_status` is the source of truth for
deactivate / soft-delete, with `deactivated_at` / `deleted_at` recording when.
Both lifecycle actions also flip `users.is_active=False`, so the auth layer's
existing `is_active` gate keeps blocking these accounts with no extra wiring —
`account_status` carries the *reason* and powers admin filtering.
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IDMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.loyalty import VipTier
    from app.models.user import User


class AccountStatus(str, enum.Enum):
    """Lifecycle state of a customer account. Stored as the member NAME
    (ACTIVE/DEACTIVATED/DELETED) by SQLAlchemy's Enum, matching the rest of the
    schema; the str value is the lowercase form the API surfaces."""

    ACTIVE = "active"
    DEACTIVATED = "deactivated"
    DELETED = "deleted"


class Customer(Base, IDMixin, TimestampMixin):
    __tablename__ = "customers"

    # 1:1 link. UNIQUE so a user can have at most one customer; CASCADE so the
    # profile dies with the auth row (we soft-delete via account_status, so a
    # hard user delete is the only path that reaches this cascade).
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )

    # ---- Profile ----
    first_name: Mapped[str | None] = mapped_column(String(120))
    last_name: Mapped[str | None] = mapped_column(String(120))
    gender: Mapped[str | None] = mapped_column(String(16))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    profile_image: Mapped[str | None] = mapped_column(String(512))

    # ---- Loyalty (denormalized totals, moved off `users`) ----
    # `points_balance` = SUM(delta), can go negative on refund reversals.
    # `lifetime_points` only grows from positive deltas — drives VIP tier.
    points_balance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lifetime_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Per-customer shareable referral code (e.g. "REF-VINAY42"). Lazily assigned
    # on first /loyalty/me/referral fetch. UNIQUE so find_referrer can match it.
    referral_code: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    # Cached VIP tier — recomputed after every positive earn so the read path
    # never has to. SET NULL so deleting a tier doesn't delete customers.
    vip_tier_id: Mapped[int | None] = mapped_column(
        ForeignKey("vip_tiers.id", ondelete="SET NULL"), index=True
    )

    # ---- Lifecycle ----
    account_status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus),
        default=AccountStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="customer")
    # The VIP tier relationship moves here from User. lazy="selectin" mirrors the
    # old eager load so _award() reads customer.vip_tier.earn_multiplier with no
    # N+1 (the customer itself is selectin-loaded from the user).
    vip_tier: Mapped["VipTier | None"] = relationship(lazy="selectin")
