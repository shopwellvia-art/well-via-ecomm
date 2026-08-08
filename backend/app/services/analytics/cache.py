"""Redis cache for resolved analytics views.

Deliberately the same shape as `settings_service`: a `_cache_key()` helper, a
`try/except redis.RedisError` around **both** the read and the write, `setex`
rather than `set` + `expire`, DEBUG-level logging, and graceful degradation. No
new caching framework — a second idiom for the same job is how one of them ends
up subtly wrong.

Three decisions carry weight here.

**Visibility is part of the key.** A resolved analytics payload is not the same
document for every reader: the finance module exposes margin, COGS and gateway
fees behind `analytics.finance.view`. If the key were only
`module/view/filters`, the first requester to open a view would warm an entry
that any later requester could read — a permission check that runs on the
request but not on the cache is not a permission check at all. The tier the
caller derives from the requester's permissions therefore sits in the key, so
two tiers physically cannot share an entry.

**Invalidation is a generation counter, never SCAN.** After a successful
aggregation the worker calls `bump_generation()`, which is a single `INCR` on
`an:v1:gen`. Every key built under the old generation is instantly unreachable
and evaporates on its own TTL. That is O(1) regardless of how many entries
exist; `SCAN`/`KEYS` over a cache holding 73 views x every filter combination
would be an O(keyspace) walk on the hot path, which is exactly why
`settings_service` does not do it either.

**A window that includes today is never stable.** A "daily" view over the last
30 days ending today is not a daily answer — today's bucket is still being
written. `ttl_for()` therefore downgrades any window touching today to the
realtime TTL regardless of the view's declared freshness class, so a number
cannot sit in the cache for an hour while the day it summarises keeps moving.

Redis being down degrades the API to uncached reads. Every method here swallows
`redis.RedisError`, logs at DEBUG and returns `None` / does nothing /
reports generation 0. Nothing in this module raises because of Redis.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional

import redis

from app.db.redis import get_redis

logger = logging.getLogger(__name__)

#: Bump when the *shape* of a cached payload changes incompatibly. Everything
#: written under the old prefix becomes unreachable at deploy time, which is
#: cheaper and safer than a migration or a flush.
KEY_NAMESPACE = "an:v1"

#: The single counter every analytics key is built under.
GENERATION_KEY = f"{KEY_NAMESPACE}:gen"

#: TTL in seconds per declared freshness class.
#:
#: `realtime` (45s) is short enough that a dashboard left open reflects a
#: recompute within a minute; `hourly` (900s) matches the cadence of the hourly
#: aggregation jobs so an entry never outlives more than one run; `daily`
#: (3600s) applies only to windows that have already closed, where the answer
#: genuinely cannot change.
TTL_BY_FRESHNESS: dict[str, int] = {
    "realtime": 45,
    "hourly": 900,
    "daily": 3600,
}

#: Used when a caller passes a freshness class we do not recognise. The
#: shortest TTL is the safe default — serving a stale number for longer than
#: intended is worse than a cache miss.
DEFAULT_FRESHNESS = "realtime"

#: `generation()` is read once per request and then memoised for this long, so a
#: view resolving 8 panels issues one GET rather than 8. The window bounds how
#: long a bumped generation can go unnoticed inside a single process.
GENERATION_MEMO_SECONDS = 1.0

#: Key components are server-controlled (registry ids, a permission tier, a hex
#: filter hash), so this is defence in depth: a component containing `:` would
#: let two distinct requests collide onto one cache entry, which for the
#: visibility component means a permission bypass.
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_.\-]+$")


def ttl_for(freshness: str, window_includes_today: bool) -> int:
    """Seconds to cache a view resolved under `freshness`.

    `window_includes_today` wins. A 30-day window ending today is not a stable
    answer no matter what class the view declares: today's bucket is rewritten
    every time the aggregation runs, so caching it for an hour would pin a
    number that the underlying rollup has already moved past. Callers pass
    `window.date_to > store_local_today` (the window is half-open, so a window
    that ends *tomorrow* is the one containing today).
    """
    if window_includes_today:
        return TTL_BY_FRESHNESS["realtime"]
    return TTL_BY_FRESHNESS.get(freshness, TTL_BY_FRESHNESS[DEFAULT_FRESHNESS])


def _validate_component(kind: str, value: Any) -> str:
    if not isinstance(value, str) or not _SAFE_COMPONENT.match(value):
        raise ValueError(
            f"invalid analytics cache key {kind} {value!r}: components must be "
            "non-empty and contain only [A-Za-z0-9_.-]. A component holding a "
            "':' could collide two different requests onto one entry."
        )
    return value


def _cache_key(
    *,
    generation: int,
    tz_generation: int,
    visibility: str,
    module: str,
    view: str,
    filter_hash: str,
) -> str:
    return (
        f"{KEY_NAMESPACE}:g{generation}:t{tz_generation}:{visibility}"
        f":view:{module}/{view}:{filter_hash}"
    )


class AnalyticsCache:
    """Generation-keyed cache for resolved analytics view payloads."""

    def __init__(self, redis_client: Optional[redis.Redis] = None):
        self.redis = redis_client if redis_client is not None else get_redis()
        self._generation: int | None = None
        self._generation_read_at: float = 0.0

    # ---- Generation ----

    def generation(self) -> int:
        """Current cache generation, or 0 when Redis is unreachable.

        Memoised for `GENERATION_MEMO_SECONDS` so one request that resolves many
        panels performs one GET. Generation 0 on failure is deliberate: with
        Redis down every key is built under the same generation, reads miss
        anyway, and nothing is corrupted — the API just runs uncached.
        """
        now = time.monotonic()
        if (
            self._generation is not None
            and now - self._generation_read_at < GENERATION_MEMO_SECONDS
        ):
            return self._generation

        value = 0
        try:
            raw = self.redis.get(GENERATION_KEY)
            if raw is not None:
                value = int(raw)
        except redis.RedisError as exc:
            logger.debug("analytics cache generation read failed: %s", exc)
        except (TypeError, ValueError) as exc:
            # Somebody SET the counter to a non-integer. Treat it as generation
            # 0 rather than failing every analytics request.
            logger.debug("analytics cache generation is not an integer: %s", exc)

        self._generation = value
        self._generation_read_at = now
        return value

    def bump_generation(self) -> None:
        """Invalidate every cached view. Call after a successful aggregation.

        One `INCR`. Nothing is deleted — every key under the previous generation
        is simply never constructed again and expires on its own TTL.
        """
        self.invalidate_all()

    def invalidate_all(self) -> int:
        """`bump_generation()` that returns the new generation (0 on failure)."""
        try:
            value = int(self.redis.incr(GENERATION_KEY))
        except redis.RedisError as exc:
            logger.debug("analytics cache generation bump failed: %s", exc)
            return 0
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            logger.debug("analytics cache generation bump returned non-integer: %s", exc)
            return 0
        self._generation = value
        self._generation_read_at = time.monotonic()
        return value

    # ---- Keys ----

    def build_key(
        self,
        *,
        module: str,
        view: str,
        filter_hash: str,
        tz_generation: int,
        visibility: str,
    ) -> str:
        """`an:v1:g{generation}:t{tz_generation}:{visibility}:view:{module}/{view}:{filter_hash}`

        `visibility` is a permission tier derived from the requester's
        permissions, not a user id — caching per user would give a hit rate near
        zero. Two requesters share an entry only when they are entitled to
        exactly the same payload.

        `tz_generation` is in the key because a reporting-timezone change
        re-buckets history: the same filters over the same dates are a different
        answer under a new generation, and a stale entry would serve the old
        buckets under the new boundaries.
        """
        return _cache_key(
            generation=self.generation(),
            tz_generation=int(tz_generation),
            visibility=_validate_component("visibility", visibility),
            module=_validate_component("module", module),
            view=_validate_component("view", view),
            filter_hash=_validate_component("filter_hash", filter_hash),
        )

    # ---- Payloads ----

    def get(self, key: str) -> dict | None:
        """Cached payload, or None on a miss, a corrupt entry, or Redis down.

        Corrupt JSON is a miss, not an exception. An entry written by an older
        deploy, truncated, or hand-edited must not take out the view — the
        recomputed answer overwrites it on the way back.
        """
        try:
            raw = self.redis.get(key)
        except redis.RedisError as exc:
            logger.debug("analytics cache read failed: %s", exc)
            return None
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError) as exc:
            logger.debug("analytics cache entry is not valid JSON (%s): %s", key, exc)
            return None
        if not isinstance(payload, dict):
            logger.debug("analytics cache entry is not an object: %s", key)
            return None
        return payload

    def set(self, key: str, payload: dict, ttl: int) -> None:
        """Store a payload under `key` for `ttl` seconds. Never raises.

        `default=str` because resolved payloads carry `Decimal` money and `date`
        buckets. Losing exactness here is safe — the cache holds the *rendered*
        answer, and the authoritative values come from the repository.
        """
        try:
            ttl = max(1, int(ttl))
        except (TypeError, ValueError):
            logger.warning("analytics cache: invalid ttl %r, entry not cached", ttl)
            return
        try:
            blob = json.dumps(payload, default=str)
        except (TypeError, ValueError) as exc:
            # A payload that cannot be serialised is a bug in the resolver, not
            # a Redis problem — log louder, but still do not fail the request.
            logger.warning("analytics cache: payload not serialisable (%s): %s", key, exc)
            return
        try:
            self.redis.setex(key, ttl, blob)
        except redis.RedisError as exc:
            logger.debug("analytics cache write failed: %s", exc)
