from __future__ import annotations

from typing import TYPE_CHECKING

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class UserSecurity(Base):
    """Per-user 2FA secrets, split out of `users` so the hot account row never
    carries them and a plain user query can't surface them. 1:1 with users via
    a shared primary key — `user_id` is both PK and FK, so a user has at most
    one row. The row is created lazily on first TOTP enrollment and deleted on
    disable (see TotpService), so users who never touch 2FA have no row at all.

    Column semantics are unchanged from when these lived on `users`:
    `totp_secret` is the Fernet-encrypted base32 secret; `totp_enabled` only
    flips True after the user confirms a code; `backup_codes` holds 10
    single-use salted hashes, shown in cleartext exactly once at enrollment.
    """

    __tablename__ = "user_security"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )

    totp_secret: Mapped[str | None] = mapped_column(String(255))
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    totp_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    backup_codes: Mapped[list[str] | None] = mapped_column(JSON)

    user: Mapped["User"] = relationship(back_populates="security")
