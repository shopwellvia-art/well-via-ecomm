"""Refresh-token session state, kept in Redis.

Architecture: each login starts a "family" (a refresh-token chain). Every
refresh rotates the family's current `jti` to a new value. We never trust a
token whose `jti` doesn't match the family's current value — that's evidence
the previous token leaked.

Redis layout:
  auth:family:{family_id}             HASH { user_id, jti }
  auth:user:{user_id}:families        SET  of family_ids belonging to a user

Family hashes get the same TTL as a refresh token. The user→families set's TTL
is refreshed on every login/rotate so it always outlives its members.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import redis

from app.core.config import settings
from app.db.redis import get_redis
from app.core.exceptions import UnauthorizedError
from app.core.security import new_jti, new_session_id

logger = logging.getLogger(__name__)


def _family_key(family_id: str) -> str:
    return f"auth:family:{family_id}"


def _user_families_key(user_id: int) -> str:
    return f"auth:user:{user_id}:families"


def _revoked_after_key(user_id: int) -> str:
    """Marker timestamp: every access token issued at/before this instant is
    considered revoked for the user. Used by get_current_user."""
    return f"auth:revoked_after:{user_id}"


def _refresh_ttl_seconds() -> int:
    return max(60, int(settings.REFRESH_TOKEN_EXPIRE_DAYS) * 86400)


def _access_ttl_seconds() -> int:
    # The marker only needs to outlive any access token already in the wild.
    return max(60, int(settings.ACCESS_TOKEN_EXPIRE_MINUTES) * 60)


class SessionService:
    """All session bookkeeping lives here so AuthService only deals with one
    object regardless of which entry point (login / refresh / logout / admin)
    triggered the change."""

    def __init__(self, client: Optional[redis.Redis] = None):
        self.redis = client or get_redis()

    # ---- Create / rotate ----

    def start_family(self, user_id: int) -> tuple[str, str]:
        """Open a new session. Returns (family_id, jti) — the caller embeds
        both into the refresh token."""
        family_id = new_session_id()
        jti = new_jti()
        ttl = _refresh_ttl_seconds()
        try:
            pipe = self.redis.pipeline()
            pipe.hset(
                _family_key(family_id),
                mapping={"user_id": str(user_id), "jti": jti},
            )
            pipe.expire(_family_key(family_id), ttl)
            pipe.sadd(_user_families_key(user_id), family_id)
            pipe.expire(_user_families_key(user_id), ttl)
            pipe.execute()
        except redis.RedisError as exc:
            # Refresh stops working but login itself still succeeds. The user
            # can still call /auth/login again to recover.
            logger.warning("session start failed: %s", exc)
        return family_id, jti

    def rotate(self, family_id: str, presented_jti: str, user_id: int) -> str:
        """Validate the presented refresh and issue a new jti for the family.

        Three failure modes:
          1. Family unknown → caller's refresh token doesn't correspond to any
             active session (probably logged out / expired).
          2. user mismatch → token belongs to a different user (very unlikely
             unless secrets leaked; we still refuse).
          3. jti stale → REUSE DETECTED. We wipe the family so even the actor
             holding the *new* token gets locked out, then 401.
        """
        try:
            data = self.redis.hgetall(_family_key(family_id))
        except redis.RedisError as exc:
            logger.warning("session lookup failed: %s", exc)
            raise UnauthorizedError("Could not validate session")

        if not data:
            raise UnauthorizedError("Session not found — please sign in again")

        if int(data.get("user_id", "0") or 0) != int(user_id):
            self._wipe_family(family_id, int(data.get("user_id") or 0))
            raise UnauthorizedError("Session user mismatch")

        if data.get("jti") != presented_jti:
            # Token reuse detected — the previous token in the chain is being
            # replayed. Burn the whole family on principle: the legitimate
            # owner can re-authenticate, and the attacker loses everything.
            self._wipe_family(family_id, user_id)
            logger.warning(
                "auth: refresh token reuse detected for family=%s user=%s",
                family_id,
                user_id,
            )
            raise UnauthorizedError(
                "Session reuse detected. For your safety we ended this session."
            )

        new = new_jti()
        try:
            pipe = self.redis.pipeline()
            pipe.hset(_family_key(family_id), "jti", new)
            pipe.expire(_family_key(family_id), _refresh_ttl_seconds())
            pipe.expire(_user_families_key(user_id), _refresh_ttl_seconds())
            pipe.execute()
        except redis.RedisError as exc:
            logger.warning("session rotate failed: %s", exc)
        return new

    # ---- Revoke ----

    def revoke_family(self, family_id: str, user_id: int) -> bool:
        """End a single session (called from /auth/logout)."""
        return self._wipe_family(family_id, user_id) > 0

    def revoke_all_for_user(self, user_id: int) -> int:
        """End every session for a user. Returns how many were killed.

        Also stamps a `revoked_after` marker so outstanding *access* tokens
        (which are stateless and otherwise valid for their full TTL) are
        rejected on their next request — closing the gap where an admin
        force-logout or self "revoke all" left live access tokens working.
        """
        try:
            family_ids = list(self.redis.smembers(_user_families_key(user_id)))
        except redis.RedisError as exc:
            logger.warning("revoke_all_for_user lookup failed: %s", exc)
            family_ids = []
        for fid in family_ids:
            self._wipe_family(fid, user_id)
        try:
            self.redis.delete(_user_families_key(user_id))
            self.redis.setex(
                _revoked_after_key(user_id),
                _access_ttl_seconds(),
                datetime.now(timezone.utc).isoformat(),
            )
        except redis.RedisError as exc:
            logger.warning("revoke_all_for_user cleanup failed: %s", exc)
        return len(family_ids)

    def access_revoked_after(self, user_id: int) -> Optional[str]:
        """ISO timestamp before which the user's access tokens are revoked, or
        None. Fails open (returns None) if Redis is unavailable so an outage
        can't lock everyone out — matches the rest of the auth layer."""
        try:
            return self.redis.get(_revoked_after_key(user_id))
        except redis.RedisError as exc:
            logger.warning("revoked_after lookup failed: %s", exc)
            return None

    def session_count(self, user_id: int) -> int:
        try:
            return int(self.redis.scard(_user_families_key(user_id)) or 0)
        except redis.RedisError:
            return 0

    # ---- Internals ----

    def _wipe_family(self, family_id: str, user_id: int) -> int:
        try:
            pipe = self.redis.pipeline()
            pipe.delete(_family_key(family_id))
            pipe.srem(_user_families_key(user_id), family_id)
            results = pipe.execute()
            return int(results[0] or 0)
        except redis.RedisError as exc:
            logger.warning("family wipe failed: %s", exc)
            return 0
