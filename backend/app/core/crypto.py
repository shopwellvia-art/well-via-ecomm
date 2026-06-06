"""Symmetric encryption for at-rest secrets (TOTP secrets today, maybe more
later). Key is derived from SECRET_KEY so deployments don't need a separate
secret — same trade-off as the PASETO key derivation in core/security.

Domain separation: the label "fernet-at-rest" keeps this key distinct from the
PASETO token key even though both are derived from SECRET_KEY. Changing
SECRET_KEY re-keys this subsystem and invalidates all encrypted TOTP secrets —
acceptable and desired after a deliberate key rotation.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _derive_fernet_key() -> bytes:
    """Return a 32-byte key derived with the 'fernet-at-rest' domain label."""
    return hashlib.sha256(
        b"fernet-at-rest:" + settings.SECRET_KEY.encode("utf-8")
    ).digest()


def _fernet() -> Fernet:
    # Fernet requires a 32-byte url-safe base64-encoded key.
    return Fernet(base64.urlsafe_b64encode(_derive_fernet_key()))


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        # Likely cause: SECRET_KEY rotated under us. Let caller decide whether
        # to re-enroll the user or surface an error.
        raise ValueError("Could not decrypt — key may have changed") from exc
