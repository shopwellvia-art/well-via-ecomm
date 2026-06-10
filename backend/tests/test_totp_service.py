"""Tests for TotpService after the 2FA columns moved to user_security.

These prove the full enrollment/verify/disable lifecycle still works once the
TOTP secrets live in the 1:1 `user_security` satellite instead of on `users`:

- start_enrollment lazily creates the satellite row (pending: secret present,
  totp_enabled still False).
- confirm_enrollment flips it on, mints 10 backup codes, stamps confirmed_at.
- verify accepts a live TOTP code AND a backup code, and backup codes
  self-consume (usable exactly once).
- disable DELETES the satellite row entirely (clean data: no row == never
  enrolled), and the User.totp_enabled property tracks all of this.

Like the other service tests, this runs against the live MySQL instance and
must be executed inside the backend container:

    docker compose exec backend pytest tests/test_totp_service.py -v
"""
from __future__ import annotations

import uuid

import pyotp
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.user import User
from app.models.user_security import UserSecurity
from app.services.settings_service import SettingsService, _cache_key
from app.services.totp_service import TotpService


def _make_user(db: Session) -> User:
    u = User(
        email=f"totptest-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(u)
    db.flush()
    return u


def _enable_totp_system_wide(db: Session) -> None:
    """Force auth.totp_mode='optional' and bust the 60s settings cache so the
    test never races a stale 'off' value from another run."""
    svc = SettingsService(db)
    svc.set_many({"auth.totp_mode": "optional"})
    db.flush()
    try:
        svc.redis.delete(_cache_key("auth.totp_mode"))
    except Exception:
        pass


def _cleanup(user_id: int | None) -> None:
    if user_id is None:
        return
    with SessionLocal() as s:
        s.execute(
            text("DELETE FROM user_security WHERE user_id = :id"), {"id": user_id}
        )
        s.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        s.commit()


class TestTotpService:
    def test_full_lifecycle(self) -> None:
        user_id = None
        db = SessionLocal()
        try:
            _enable_totp_system_wide(db)
            user = _make_user(db)
            user_id = user.id
            svc = TotpService(db)

            # --- start: lazily creates a pending satellite row ---
            assert user.security is None
            data = svc.start_enrollment(user)
            assert set(data) >= {"secret", "otpauth_uri"}
            assert isinstance(user.security, UserSecurity)
            assert user.security.totp_secret  # stored (encrypted)
            assert user.security.totp_enabled is False
            assert user.totp_enabled is False  # property reads through

            # --- confirm: flips on + returns 10 backup codes ---
            code = pyotp.TOTP(data["secret"]).now()
            backup_codes = svc.confirm_enrollment(user, code)
            assert len(backup_codes) == 10
            assert user.security.totp_enabled is True
            assert user.security.totp_confirmed_at is not None
            assert user.totp_enabled is True
            assert len(user.security.backup_codes) == 10

            # --- verify: live TOTP code accepted ---
            assert svc.verify(user, pyotp.TOTP(data["secret"]).now()) is True

            # --- verify: backup code accepted once, then consumed ---
            one = backup_codes[0]
            assert svc.verify(user, one) is True
            assert len(user.security.backup_codes) == 9
            assert svc.verify(user, one) is False  # already used

            # --- disable: satellite row is deleted outright ---
            svc.disable(user)
            db.flush()
            assert user.security is None
            assert user.totp_enabled is False

            db.commit()

            # Confirm at the DB level that no row lingers.
            remaining = db.execute(
                text("SELECT COUNT(*) FROM user_security WHERE user_id = :id"),
                {"id": user_id},
            ).scalar()
            assert remaining == 0
        finally:
            db.rollback()
            db.close()
            _cleanup(user_id)

    def test_verify_false_when_not_enrolled(self) -> None:
        """A user with no security row never verifies (no row == no 2FA)."""
        user_id = None
        db = SessionLocal()
        try:
            user = _make_user(db)
            user_id = user.id
            db.commit()
            assert user.security is None
            assert TotpService(db).verify(user, "123456") is False
            assert user.totp_enabled is False
        finally:
            db.rollback()
            db.close()
            _cleanup(user_id)
