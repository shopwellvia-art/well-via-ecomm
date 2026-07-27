"""SMS OTP verification for COD orders.

Why this exists (Phase 12): full COD is the highest-RTO payment method on
Indian D2C — a meaningful slice of "failed to deliver" is fake phone
numbers. Requiring the customer to receive an SMS code and enter it
before the order is placed filters the bots + half-hearted attempts.

Storage:
  - The code itself lives in Redis at `cod:otp:{user_id}:{phone}` with a
    10-minute TTL. We store the *hash* of the code, not the cleartext —
    cheap, and prevents anyone with Redis read access from harvesting
    fresh OTPs.
  - The "verified" marker lives at `cod:otp_ok:{user_id}:{phone}` with
    a 15-minute TTL. Long enough for the customer to finish typing the
    address; short enough that a stolen marker isn't useful tomorrow.

Rate limits (defense in depth on top of the endpoint's IP cap):
  - 5 sends per phone per 15 minutes (SMS-bomb defense)
  - 5 verify attempts per OTP — after that, the OTP is invalidated and
    the customer has to request a new one.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import time
from typing import Optional

import redis
from sqlalchemy.orm import Session

from app.core.config import settings as env_settings
from app.db.redis import get_redis
from app.core.exceptions import ConflictError, ValidationError
from app.sms import send_sms

logger = logging.getLogger(__name__)

_OTP_TTL_SECONDS = 10 * 60
_VERIFIED_TTL_SECONDS = 15 * 60
_MAX_ATTEMPTS = 5
_SEND_WINDOW_SECONDS = 15 * 60
_SEND_MAX_PER_WINDOW = 5


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _normalize_phone(phone: str) -> str:
    """Strip spaces / dashes / parens. We accept anything the customer's
    SMS provider will, but normalize for stable cache keys."""
    return "".join(c for c in (phone or "") if c.isdigit() or c == "+")


def _otp_key(user_id: int, phone: str) -> str:
    return f"cod:otp:{user_id}:{phone}"


def _verified_key(user_id: int, phone: str) -> str:
    return f"cod:otp_ok:{user_id}:{phone}"


def _send_window_key(user_id: int, phone: str) -> str:
    return f"cod:otp_send:{user_id}:{phone}"


class CodOtpService:
    def __init__(self, db: Session, redis_client: Optional[redis.Redis] = None):
        self.db = db
        self.redis = redis_client or get_redis()

    # ---- public ------------------------------------------------------------

    def send(self, *, user_id: int, phone: str) -> dict:
        """Generates a 6-digit code, SMS-es it, and stores its hash.

        Returns a small dict the endpoint forwards to the client:
        masked phone + expires_in_seconds. Never returns the code itself.
        """
        clean = _normalize_phone(phone)
        if not clean or len(clean) < 8:
            raise ValidationError("Phone number doesn't look right.")

        # Rolling-window SMS rate limit — we deliberately do this in Redis
        # rather than the global RateLimiter so it's keyed off (user, phone)
        # together, not just the IP.
        sent_count = self._incr_send_count(user_id, clean)
        if sent_count > _SEND_MAX_PER_WINDOW:
            raise ConflictError(
                "Too many OTP requests for this number. Try again in a few minutes."
            )

        code = f"{secrets.randbelow(1_000_000):06d}"
        self.redis.setex(
            _otp_key(user_id, clean),
            _OTP_TTL_SECONDS,
            f"{_hash_code(code)}:0",   # `<hash>:<attempts>`
        )
        # Body kept short — most carriers cap at 160 chars + the URL changes
        # nothing about COD verification.
        send_sms(
            to=clean,
            body=f"Your Wellvia verification code is {code}. Valid 10 minutes.",
            db=self.db,
        )
        logger.info("cod otp sent user=%s phone=%s", user_id, clean[-4:])
        return {
            "phone_masked": _mask_phone(clean),
            "expires_in_seconds": _OTP_TTL_SECONDS,
        }

    def verify(self, *, user_id: int, phone: str, code: str) -> None:
        """Compares the code against the stored hash. On success, plants
        the verified marker. On 5th wrong attempt, invalidates the OTP."""
        clean = _normalize_phone(phone)
        raw = self.redis.get(_otp_key(user_id, clean))
        if not raw:
            raise ValidationError(
                "OTP expired. Tap 'Resend code' to get a new one."
            )
        try:
            stored_hash, attempts_str = raw.rsplit(":", 1)
            attempts = int(attempts_str)
        except (ValueError, AttributeError):
            self.redis.delete(_otp_key(user_id, clean))
            raise ValidationError("OTP record is corrupt — please resend.") from None

        if attempts >= _MAX_ATTEMPTS:
            # Already past the limit (race condition / re-attempt). Burn it.
            self.redis.delete(_otp_key(user_id, clean))
            raise ConflictError(
                "Too many wrong attempts. Tap 'Resend code' to start over."
            )

        if _hash_code((code or "").strip()) != stored_hash:
            new_attempts = attempts + 1
            if new_attempts >= _MAX_ATTEMPTS:
                self.redis.delete(_otp_key(user_id, clean))
                raise ConflictError(
                    "Too many wrong attempts. Tap 'Resend code' to start over."
                )
            # Preserve remaining TTL so the verify window doesn't expand on bad guesses.
            ttl = self.redis.ttl(_otp_key(user_id, clean))
            self.redis.setex(
                _otp_key(user_id, clean),
                max(ttl, 1),
                f"{stored_hash}:{new_attempts}",
            )
            raise ValidationError(
                f"That code didn't match. {_MAX_ATTEMPTS - new_attempts} attempts left."
            )

        # Win — drop the OTP and plant the verified marker.
        self.redis.delete(_otp_key(user_id, clean))
        self.redis.setex(
            _verified_key(user_id, clean), _VERIFIED_TTL_SECONDS, str(int(time.time()))
        )
        logger.info("cod otp verified user=%s phone=%s", user_id, clean[-4:])

    def is_verified(self, *, user_id: int, phone: str) -> bool:
        clean = _normalize_phone(phone)
        return bool(self.redis.get(_verified_key(user_id, clean)))

    def consume(self, *, user_id: int, phone: str) -> None:
        """Drop the verified marker after a successful COD checkout so a
        subsequent COD order requires its own OTP."""
        clean = _normalize_phone(phone)
        self.redis.delete(_verified_key(user_id, clean))

    # ---- internals ---------------------------------------------------------

    def _incr_send_count(self, user_id: int, phone: str) -> int:
        key = _send_window_key(user_id, phone)
        try:
            n = self.redis.incr(key)
            if n == 1:
                self.redis.expire(key, _SEND_WINDOW_SECONDS)
            return int(n)
        except redis.RedisError as exc:
            logger.warning("otp send-count incr failed: %s", exc)
            # Fail open — better to send an SMS than to lock out a customer
            # because Redis hiccupped. The endpoint's IP rate limit is the
            # backstop.
            return 1


def _mask_phone(phone: str) -> str:
    """+91 ******1234 — keeps the last 4 visible, hides the rest."""
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) <= 4:
        return phone
    return f"{phone[:len(phone) - len(digits) + 2]}{'*' * (len(digits) - 4)}{digits[-4:]}"
