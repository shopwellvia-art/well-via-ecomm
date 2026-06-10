"""Customer address book.

Each user can have up to 10 saved shipping addresses. Addresses are 1:N with
`users` via `user_id` (FK -> users.id ON DELETE CASCADE) — orders, wishlists,
and all other domain objects FK users directly, so we follow the same pattern
rather than fanning off `customers`.

The `is_default` flag is enforced at the *service* layer (not a DB partial
unique index — MySQL does not support them). The service flips all other rows
to False transactionally before setting the target True, so at most one default
exists per user at steady state.

`AddressLabel` stores its *name* (HOME/WORK/OTHER) in the ENUM column, matching
the same convention used by `AccountStatus` and `OrderStatus` throughout this
schema.
"""
from __future__ import annotations

import enum
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IDMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class AddressLabel(str, enum.Enum):
    """Physical context of an address. Stored as the member NAME
    (HOME/WORK/OTHER) by SQLAlchemy's Enum, matching OrderStatus and
    AccountStatus conventions."""

    HOME = "home"
    WORK = "work"
    OTHER = "other"


class Address(Base, IDMixin, TimestampMixin):
    __tablename__ = "addresses"

    # FK to users.id — cascade deletes the address when the account is hard-deleted.
    user_id: Mapped[int] = mapped_column(
        sa.Integer,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ---- Consignee details ----
    full_name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    phone: Mapped[str] = mapped_column(sa.String(20), nullable=False)

    # ---- Street address ----
    line1: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    line2: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    landmark: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)

    # ---- Locality ----
    city: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    state: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    pincode: Mapped[str] = mapped_column(sa.String(6), nullable=False)
    # ISO 3166-1 alpha-2. Default "IN" — nearly all addresses in scope are India.
    country: Mapped[str] = mapped_column(
        sa.String(2), nullable=False, default="IN", server_default=sa.text("'IN'")
    )

    # ---- Classification ----
    label: Mapped[AddressLabel] = mapped_column(
        sa.Enum(AddressLabel),
        nullable=False,
        default=AddressLabel.HOME,
        server_default="HOME",
    )

    # ---- Default flag ----
    # Only one row per user should have this True; enforced in the service layer.
    is_default: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.text("0")
    )

    # ---- Relationships ----
    user: Mapped["User"] = relationship(lazy="joined")
