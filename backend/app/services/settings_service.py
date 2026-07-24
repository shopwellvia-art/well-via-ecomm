"""Runtime-editable system settings.

Two read paths:
  1. `get(key)` — single value, Redis-cached for 60s. Used by hot paths
     (every email send reads smtp settings, every login reads auth.totp_mode).
  2. `list_all_for_admin()` — full table, used only by the admin settings page.

Writes invalidate the cache, write the new value, and emit an audit row.
Secret values are redacted in the response but still readable internally
via `get_raw` for the services that need them.
"""
from __future__ import annotations

import logging
from typing import Optional

import redis
from sqlalchemy.orm import Session

from app.core.config import settings as env_settings
from app.db.redis import get_redis
from app.models.system_setting import SystemSetting
from app.models.user import User
from app.repositories.system_setting_repository import SystemSettingRepository

logger = logging.getLogger(__name__)

_CACHE_TTL = 60  # seconds
_REDACTED = "***"


def _cache_key(key: str) -> str:
    return f"settings:{key}"


class SettingsService:
    def __init__(self, db: Session, redis_client: Optional[redis.Redis] = None):
        self.db = db
        self.repo = SystemSettingRepository(db)
        self.redis = redis_client or get_redis()

    # ---- Reads ----

    def get_raw(self, key: str, default: str | None = None) -> str | None:
        """Internal read — returns the real value, including secrets."""
        try:
            cached = self.redis.get(_cache_key(key))
            if cached is not None:
                return cached if cached != "__NONE__" else None
        except redis.RedisError as exc:
            logger.debug("settings cache read failed: %s", exc)

        row = self.repo.get_by_key(key)
        value = row.value if row else None
        if value is None or value == "":
            value = default

        try:
            # Sentinel allows us to cache the "absent" case too, otherwise
            # missing keys would hit the DB every call.
            self.redis.setex(_cache_key(key), _CACHE_TTL, value if value is not None else "__NONE__")
        except redis.RedisError as exc:
            logger.debug("settings cache write failed: %s", exc)
        return value

    def get_bool(self, key: str, default: bool = False) -> bool:
        v = self.get_raw(key)
        if v is None:
            return default
        return v.strip().lower() in ("1", "true", "yes", "on")

    def get_int(self, key: str, default: int = 0) -> int:
        v = self.get_raw(key)
        if v is None or v == "":
            return default
        try:
            return int(v)
        except ValueError:
            return default

    # ---- Admin reads ----

    def list_all(self, *, category: str | None = None) -> list[SystemSetting]:
        if category:
            return self.repo.list_by_category(category)
        return self.repo.list_all()

    def view_value(self, row: SystemSetting) -> str | None:
        """Mask the value if the row is marked secret AND it has one. The
        admin UI relies on this — they should never see the actual password."""
        if row.is_secret and (row.value or "") != "":
            return _REDACTED
        return row.value

    # ---- Writes ----

    def set_many(
        self, updates: dict[str, str | None], *, actor: User | None = None
    ) -> list[tuple[SystemSetting, str | None, str | None]]:
        """Apply a batch of updates. Returns list of (row, before, after) for
        the values that actually changed — caller writes audit rows from this.

        Empty-string values mean "unset" — written as the empty string in the
        DB and surfaced as None to consumers, which lets the env fallback
        kick back in.
        """
        changed: list[tuple[SystemSetting, str | None, str | None]] = []
        for key, new_value in updates.items():
            row = self.repo.get_by_key(key)
            if row is None:
                logger.warning("settings.set: unknown key %s ignored", key)
                continue
            # Don't accept the redaction sentinel as the new value — the admin
            # left the field untouched, keep the existing value.
            if new_value == _REDACTED:
                continue
            before = row.value
            after = new_value if new_value is not None else ""
            if before == after:
                continue
            row.value = after
            changed.append((row, before, after))
        self.db.flush()
        # Invalidate cache. We do this after the flush so a parallel reader
        # observing the cache miss reads the new value from the DB.
        for row, _b, _a in changed:
            try:
                self.redis.delete(_cache_key(row.key))
            except redis.RedisError:
                pass
        return changed
