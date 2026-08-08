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
        """Internal read — returns the real value, including secrets.

        What gets cached is what the DATABASE holds, never what this particular
        caller would fall back to. `default` is applied on the way out, on the
        hit path and the miss path alike, so the answer does not depend on
        whether the key happens to be cached or on who asked first.

        It used to cache the resolved value, which broke that in both
        directions for any key with no stored row:

          * `get_raw("storage.s3_region", "ap-south-1")` cached "ap-south-1",
            so a later bare `get_raw("storage.s3_region")` reported a region
            nobody had configured — a fallback promoted to a setting.
          * a bare `get_raw(k)` cached `__NONE__`, and the hit path returned
            early on the sentinel, so the NEXT caller's default was dropped and
            it got None — the same call returning two different things a minute
            apart depending on cache timing.

        A cache is allowed to make a read faster. It is not allowed to change
        the answer.
        """
        try:
            cached = self.redis.get(_cache_key(key))
            if cached is not None:
                return default if cached == "__NONE__" else cached
        except redis.RedisError as exc:
            logger.debug("settings cache read failed: %s", exc)

        row = self.repo.get_by_key(key)
        stored = row.value if row else None
        if stored == "":
            # Empty means "unset" — the same contract `set_many` documents, so
            # the env/argument fallback kicks back in.
            stored = None

        try:
            # Sentinel allows us to cache the "absent" case too, otherwise
            # missing keys would hit the DB every call.
            self.redis.setex(
                _cache_key(key),
                _CACHE_TTL,
                stored if stored is not None else "__NONE__",
            )
        except redis.RedisError as exc:
            logger.debug("settings cache write failed: %s", exc)
        return stored if stored is not None else default

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
        # Invalidate EVERY key in the batch, not only the ones whose value
        # moved. We do this after the flush so a parallel reader observing the
        # cache miss reads the new value from the DB.
        #
        # Invalidating only `changed` assumed the cache always agrees with the
        # DB when a write is a no-op, and it does not:
        #
        #   * Writers that bypass this service exist and do not all bust the
        #     cache. `analytics/integrations.py::ensure_rows` INSERTs settings
        #     rows directly and commits with no invalidation at all;
        #     `aggregation/jobs_ops.py` writes `analytics.inventory_history_since`
        #     directly and only busts the cache best-effort, BEFORE the runner
        #     commits, so a reader racing that window re-caches the stale answer
        #     for another full TTL. Several test suites write the table in raw
        #     SQL and bust nothing.
        #   * A reader caches the ABSENT case as `__NONE__` for the full TTL, so
        #     a key whose row was created by one of those writers inside that
        #     window keeps reading as "not configured".
        #
        # In both cases the operator sees a saved value behaving as if it were
        # not saved, and the one thing they will try — pressing Save again — was
        # precisely the path that skipped the delete, because the second save
        # changes nothing. A redundant delete costs one DB read on the next
        # `get_raw`; the alternative costs a support ticket.
        for key in updates:
            try:
                self.redis.delete(_cache_key(key))
            except redis.RedisError:
                pass
        return changed
