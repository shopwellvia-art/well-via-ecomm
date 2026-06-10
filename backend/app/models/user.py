from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IDMixin, TimestampMixin

if TYPE_CHECKING:
    from datetime import date

    from app.models.customer import Customer
    from app.models.loyalty import PointsTransaction, VipTier
    from app.models.rbac import Role
    from app.models.referral import Referral
    from app.models.user_security import UserSecurity


class User(Base, IDMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    # Login + contact phone. UNIQUE so it can serve as a login identifier
    # alongside email (users sign in with either). Nullable — MySQL allows many
    # NULLs under a unique index, so phone stays optional. When present the
    # notification service also sends SMS for shippable order events.
    phone: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Legacy shortcut. True == bypass all permission checks. Kept so existing
    # code paths still work; new code should use require_permission().
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Profile + denormalized loyalty totals now live on the `customers`
    # satellite (1:1). See the read proxies below for backwards-compatible
    # access to full_name / points_balance / referral_code / vip_tier etc.

    roles: Mapped[list["Role"]] = relationship(
        secondary="user_roles", back_populates="users", lazy="selectin"
    )

    # TOTP / 2FA secrets, split into the user_security satellite (1:1, shared
    # PK). Loaded eagerly so the login path and UserRead see totp state without
    # an N+1. None until the user starts enrollment; removed again on disable.
    security: Mapped["UserSecurity | None"] = relationship(
        back_populates="user",
        uselist=False,
        lazy="selectin",
        cascade="all, delete-orphan",
        single_parent=True,
    )

    # Profile + loyalty satellite (1:1). Created eagerly at registration, so in
    # practice this is never None for a normally-created account. Loaded eagerly
    # so UserRead / the loyalty read path see profile + points without an N+1.
    customer: Mapped["Customer | None"] = relationship(
        back_populates="user",
        uselist=False,
        lazy="selectin",
        cascade="all, delete-orphan",
        single_parent=True,
    )

    points_transactions: Mapped[list["PointsTransaction"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    # Referrals this user *made* (people they brought in).
    referrals_made: Mapped[list["Referral"]] = relationship(
        foreign_keys="Referral.referrer_user_id",
        back_populates="referrer",
        cascade="all, delete-orphan",
    )
    # The single referral row where this user is the *referred* side (or empty
    # list when self-signup). Modeled as a list for SA convenience; the unique
    # constraint on referred_user_id enforces at most one.
    referred_by: Mapped[list["Referral"]] = relationship(
        foreign_keys="Referral.referred_user_id",
        back_populates="referred",
        cascade="all, delete-orphan",
    )

    @property
    def totp_enabled(self) -> bool:
        """Whether 2FA is active. Reads through to the user_security satellite so
        the login check, the disable check, and UserRead keep working unchanged
        after the column move. Read-only — writes go through TotpService."""
        return bool(self.security and self.security.totp_enabled)

    # ---- Customer-profile read proxies -------------------------------------
    # The profile + loyalty fields physically moved to the `customers`
    # satellite. These READ-ONLY properties delegate to it so the many read
    # sites (UserRead serialization, notification templates, admin lists,
    # loyalty endpoints) keep working unchanged — same idiom as `totp_enabled`.
    # WRITES go through the Customer row directly (LoyaltyService /
    # ReferralService / the profile endpoint); never assign to these.

    @property
    def first_name(self) -> str | None:
        return self.customer.first_name if self.customer else None

    @property
    def last_name(self) -> str | None:
        return self.customer.last_name if self.customer else None

    @property
    def full_name(self) -> str | None:
        """Display name rebuilt from the customer's first/last name so callers
        still reading `user.full_name` (templates, admin search) keep working."""
        c = self.customer
        if not c:
            return None
        parts = [p for p in (c.first_name, c.last_name) if p]
        return " ".join(parts) or None

    @property
    def gender(self) -> str | None:
        return self.customer.gender if self.customer else None

    @property
    def date_of_birth(self) -> "date | None":
        return self.customer.date_of_birth if self.customer else None

    @property
    def profile_image(self) -> str | None:
        return self.customer.profile_image if self.customer else None

    @property
    def points_balance(self) -> int:
        return self.customer.points_balance if self.customer else 0

    @property
    def lifetime_points(self) -> int:
        return self.customer.lifetime_points if self.customer else 0

    @property
    def referral_code(self) -> str | None:
        return self.customer.referral_code if self.customer else None

    @property
    def vip_tier(self) -> "VipTier | None":
        return self.customer.vip_tier if self.customer else None

    @property
    def account_status(self) -> str:
        c = self.customer
        return c.account_status.value if c else "active"

    def has_permission(self, permission_name: str) -> bool:
        if self.is_admin:
            return True
        for role in self.roles:
            for perm in role.permissions:
                if perm.name == permission_name:
                    return True
        return False

    @property
    def permissions(self) -> list[str]:
        """Flat sorted list of permission names this user holds.

        Picked up by UserRead via from_attributes so the frontend can mirror
        server-side checks. Admins get the full registry expanded so
        `permissions.includes(x)` on the client matches `has_permission(x)`.
        """
        if self.is_admin:
            # Import lazily — avoid model→service import cycle at module load.
            from app.services.permissions_registry import all_permission_names

            return all_permission_names()
        names: set[str] = set()
        for role in self.roles:
            for perm in role.permissions:
                names.add(perm.name)
        return sorted(names)
