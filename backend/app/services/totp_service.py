"""TOTP / 2FA enrollment + verification.

Flow:
  1. `start_enrollment(user)`  — generate secret, store encrypted, return the
     `otpauth://...` URI for the QR code. `totp_enabled` stays False.
  2. `confirm_enrollment(user, code)` — verify the user can produce a valid
     code. On success: set totp_enabled=True, mint 10 single-use backup codes,
     return the cleartext codes ONCE.
  3. `verify(user, code)` — used during login. Accepts either a TOTP code or
     a one-time backup code. Backup codes self-consume on use.
  4. `disable(user)` — clear the secret and flag.

System-level switch lives in `system_settings` under `auth.totp_mode`:
  - "off"      — feature disabled everywhere
  - "optional" — users may enable on their own (v1 default)

Forced enrollment (`admin_required`, `all_required`) is intentionally out of
scope for v1; the column accepts those values for forward-compat.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

import pyotp
from sqlalchemy.orm import Session

from app.core.config import settings as env_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.exceptions import ConflictError, ForbiddenError, ValidationError
from app.models.user import User
from app.models.user_security import UserSecurity
from app.services.settings_service import SettingsService

_BACKUP_CODE_COUNT = 10
_ISSUER_NAME = "Wellvia"


def _hash_backup(code: str) -> str:
    """Backup codes are stored as plain SHA-256 hashes — they're high-entropy
    one-time strings, no need for bcrypt overhead. Salted with a constant
    domain-separation prefix to keep them tied to this app.

    NOTE: the "lumen:" prefix is a legacy value inherited from the template and
    is deliberately NOT rebranded. It is baked into every backup-code hash
    already stored in `user_security.backup_codes`; changing it would silently
    invalidate every user's existing backup codes. It is never displayed
    anywhere — only `_ISSUER_NAME` above is user-visible (in authenticator
    apps). Change it only alongside a migration that re-issues backup codes."""
    return hashlib.sha256(f"lumen:{code}".encode("utf-8")).hexdigest()


def _new_backup_code() -> str:
    # 10 chars from a 32-char alphabet — ~50 bits of entropy, more than enough
    # given each code is single-use and rate-limited at login.
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # ambiguous chars dropped
    return "".join(secrets.choice(alphabet) for _ in range(10))


class TotpService:
    def __init__(self, db: Session):
        self.db = db

    # ---- System gate ----

    def system_mode(self) -> str:
        return (SettingsService(self.db).get_raw("auth.totp_mode") or "off").strip().lower()

    def is_enabled_system_wide(self) -> bool:
        return self.system_mode() != "off"

    def assert_system_enabled(self) -> None:
        if not self.is_enabled_system_wide():
            raise ForbiddenError("Two-factor authentication is disabled by the administrator.")

    # ---- Satellite row access ----

    def _security(self, user: User, *, create: bool = False) -> UserSecurity | None:
        """The user's 2FA row, or None if they've never enrolled. Pass
        create=True on the write path to lazily attach a fresh row — the
        relationship cascade persists it when the session flushes."""
        sec = user.security
        if sec is None and create:
            sec = UserSecurity(user_id=user.id)
            user.security = sec
            self.db.add(sec)
        return sec

    # ---- Enrollment ----

    def start_enrollment(self, user: User) -> dict:
        """Generate a new TOTP secret. Stored encrypted; the cleartext is
        returned ONCE for QR display. Calling this on a user who already
        finished enrollment is a 409 — they must `disable()` first."""
        self.assert_system_enabled()
        if user.totp_enabled:
            raise ConflictError("Two-factor is already enabled. Disable it first to re-enroll.")

        secret_plain = pyotp.random_base32()
        sec = self._security(user, create=True)
        sec.totp_secret = encrypt_secret(secret_plain)
        sec.totp_enabled = False
        sec.totp_confirmed_at = None
        sec.backup_codes = None
        self.db.flush()

        totp = pyotp.TOTP(secret_plain)
        uri = totp.provisioning_uri(name=user.email, issuer_name=_ISSUER_NAME)
        return {"secret": secret_plain, "otpauth_uri": uri}

    def confirm_enrollment(self, user: User, code: str) -> list[str]:
        """Confirm enrollment by verifying a fresh TOTP code. Returns the
        backup codes (cleartext) — caller MUST surface them once and never
        store them on the client.
        """
        self.assert_system_enabled()
        sec = user.security
        if not sec or not sec.totp_secret:
            raise ValidationError("Start enrollment first.")
        if sec.totp_enabled:
            raise ConflictError("Two-factor is already enabled.")

        secret = decrypt_secret(sec.totp_secret)
        totp = pyotp.TOTP(secret)
        # `valid_window=1` accepts the previous and next 30s windows too —
        # forgiving of small client clock skew without weakening security.
        if not totp.verify(code.strip(), valid_window=1):
            raise ValidationError("Code didn't match. Double-check your authenticator.")

        backup_plain = [_new_backup_code() for _ in range(_BACKUP_CODE_COUNT)]
        sec.backup_codes = [_hash_backup(c) for c in backup_plain]
        sec.totp_enabled = True
        sec.totp_confirmed_at = datetime.now(timezone.utc)
        self.db.flush()
        return backup_plain

    # ---- Verification (login path) ----

    def verify(self, user: User, code: str) -> bool:
        """True iff the code is a valid current TOTP OR an unused backup code.
        Backup codes self-consume on a successful match.
        """
        sec = user.security
        if not sec or not sec.totp_enabled or not sec.totp_secret:
            return False
        cleaned = (code or "").strip().replace(" ", "").replace("-", "").upper()
        if not cleaned:
            return False

        # Backup codes are 10 chars and alphabetic; TOTP codes are 6 digits.
        # We try the cheaper check first.
        if cleaned.isdigit():
            try:
                secret = decrypt_secret(sec.totp_secret)
            except ValueError:
                return False
            totp = pyotp.TOTP(secret)
            return totp.verify(cleaned, valid_window=1)

        # Backup-code path
        if not sec.backup_codes:
            return False
        target = _hash_backup(cleaned)
        if target in sec.backup_codes:
            remaining = [c for c in sec.backup_codes if c != target]
            sec.backup_codes = remaining
            self.db.flush()
            return True
        return False

    # ---- Disable ----

    def disable(self, user: User) -> None:
        # Drop the whole satellite row rather than blanking fields — keeps the
        # data clean (no row == never-enrolled). delete-orphan does the DELETE.
        if user.security is not None:
            user.security = None
        self.db.flush()
