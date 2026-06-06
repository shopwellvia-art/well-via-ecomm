"""Password hashing and PASETO v4.local token issuance.

Tokens are PASETO v4.local — symmetric, authenticated encryption. The 32-byte
key is derived deterministically from SECRET_KEY, so existing configuration
keeps working with no new environment variable.

Key derivation — domain separation:
  Each subsystem derives its key from SECRET_KEY with a distinct label so the
  PASETO token key and the Fernet at-rest encryption key are never the same raw
  bytes. Changing SECRET_KEY re-keys both subsystems and invalidates all
  existing tokens AND encrypted TOTP secrets — this is acceptable and desired
  after a deliberate key rotation.
"""
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import pyseto
from passlib.context import CryptContext
from pyseto import Key
from pyseto.exceptions import PysetoError

from app.core.config import settings
from app.core.exceptions import UnauthorizedError

# SECURITY TODO: replace passlib/bcrypt with a modern library (e.g. argon2-cffi)
# and add bcrypt 72-byte pre-hashing — deferred because it would invalidate all
# existing password hashes.

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _derive_key(label: str) -> bytes:
    """Derive a 32-byte key from SECRET_KEY with domain separation.

    Using a distinct label per subsystem ensures that even if one key is
    somehow exposed the other remains uncompromised.
    """
    return hashlib.sha256(
        label.encode("utf-8") + b":" + settings.SECRET_KEY.encode("utf-8")
    ).digest()


# PASETO v4.local requires a 32-byte symmetric key.
# Label "paseto-v4-local" keeps this key distinct from the Fernet key.
_paseto_key = Key.new(
    version=4,
    purpose="local",
    key=_derive_key("paseto-v4-local"),
)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _encode(payload: dict[str, Any]) -> str:
    token = pyseto.encode(_paseto_key, payload, serializer=json)
    return token.decode("utf-8") if isinstance(token, bytes) else token


def create_access_token(subject: str | int, extra: dict[str, Any] | None = None) -> str:
    now = _now()
    expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": "access",
        "iat": now.isoformat(),   # issued-at — used by access-token revocation check
        "exp": expire.isoformat(),
    }
    if extra:
        payload.update(extra)
    return _encode(payload)


def new_session_id() -> str:
    """Generate a session/family id. Same shape as a jti — just a different
    role. URL-safe and impossible to guess."""
    return secrets.token_urlsafe(16)


def new_jti() -> str:
    """Per-token id. Lets the session service tell rotated tokens apart."""
    return secrets.token_urlsafe(16)


def create_refresh_token(
    subject: str | int, *, family_id: str, jti: str
) -> str:
    """Refresh token carries both `family_id` (session) and `jti` (this specific
    token within the family). The server pairs them against Redis on /refresh
    to detect reuse — see SessionService."""
    expire = _now() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": str(subject),
        "type": "refresh",
        "exp": expire.isoformat(),
        "fid": family_id,
        "jti": jti,
    }
    return _encode(payload)


def decode_token(token: str) -> dict[str, Any]:
    try:
        decoded = pyseto.decode(_paseto_key, token, deserializer=json)
    except (PysetoError, ValueError, TypeError) as exc:
        raise UnauthorizedError("Invalid or malformed token") from exc

    payload = decoded.payload
    if not isinstance(payload, dict):
        raise UnauthorizedError("Invalid token payload")

    # Require a valid exp claim — tokens without one are forged or legacy.
    expires_at = payload.get("exp")
    if not expires_at:
        raise UnauthorizedError("Token has expired")
    try:
        exp = datetime.fromisoformat(expires_at)
    except (TypeError, ValueError) as exc:
        raise UnauthorizedError("Invalid token expiry") from exc
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if _now() >= exp:
        raise UnauthorizedError("Token has expired")

    return payload
