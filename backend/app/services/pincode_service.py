"""Pincode → city/state lookup via api.postalpincode.in with Redis caching.

Design decisions (per plan):
- Redis cache: positives for 30 days, negatives for 1 day.
- Redis errors are swallowed — cache is a perf optimization, not correctness.
- Any network/parse/timeout exception → found=False (no caching for transient
  failures).  Only a definitive API "Error"/no-records response caches negative.
- Autofill is always decoration — checkout is never blocked by a lookup failure.
"""
from __future__ import annotations

import json
import logging
import re

import httpx
import redis

from app.core.config import settings
from app.db.redis import get_redis

logger = logging.getLogger(__name__)

_PINCODE_RE = re.compile(r"^[1-9][0-9]{5}$")

_REDIS_KEY_PREFIX = "pincode:lookup:"
_POSITIVE_TTL = 30 * 24 * 60 * 60   # 30 days in seconds
_NEGATIVE_TTL = 1 * 24 * 60 * 60    # 1 day in seconds

_POSTALPINCODE_URL = "https://api.postalpincode.in/pincode/{pin}"


class PincodeService:
    def __init__(self) -> None:
        self._redis = get_redis()

    def lookup(self, pincode: str) -> dict:
        """Return ``{pincode, found, city, state}``.

        Always returns a dict — never raises.  ``found=False`` signals that
        city/state could not be determined; callers should fall back to manual
        entry.
        """
        pin = (pincode or "").strip()

        # Validate format first — invalid pins are immediately negative.
        if not _PINCODE_RE.match(pin):
            return {"pincode": pin, "found": False, "city": None, "state": None}

        # Redis cache hit?
        cached = self._cache_read(pin)
        if cached is not None:
            return cached

        # External API call.
        return self._fetch_and_cache(pin)

    # ---- internals ----

    def _cache_key(self, pin: str) -> str:
        return f"{_REDIS_KEY_PREFIX}{pin}"

    def _cache_read(self, pin: str) -> dict | None:
        """Return the cached result dict, or None on miss / Redis error."""
        try:
            raw = self._redis.get(self._cache_key(pin))
        except redis.RedisError as exc:
            logger.debug("pincode cache read failed for %s: %s", pin, exc)
            return None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("bad pincode cache value for %s: %s", pin, exc)
            return None

    def _cache_write(self, pin: str, result: dict, ttl: int) -> None:
        try:
            self._redis.setex(self._cache_key(pin), ttl, json.dumps(result))
        except redis.RedisError as exc:
            # Cache write failure must never affect the caller.
            logger.debug("pincode cache write failed for %s: %s", pin, exc)

    def _fetch_and_cache(self, pin: str) -> dict:
        """Call api.postalpincode.in, parse the response, cache appropriately."""
        not_found = {"pincode": pin, "found": False, "city": None, "state": None}

        try:
            resp = httpx.get(
                _POSTALPINCODE_URL.format(pin=pin),
                timeout=5.0,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            # Network/timeout/parse errors are transient — do NOT cache negative.
            logger.debug("pincode API error for %s: %s", pin, exc)
            return not_found

        # API response structure: a list with one element.
        # Success shape: [{"Status": "Success", "PostOffice": [{...}]}]
        # Error shape:   [{"Status": "Error", "PostOffice": null}]
        try:
            entry = data[0]
            if entry.get("Status") != "Success":
                # Definitive "not found" from the API — cache negative.
                post_offices = entry.get("PostOffice") or []
                if not post_offices:
                    self._cache_write(pin, not_found, _NEGATIVE_TTL)
                return not_found

            post_offices = entry.get("PostOffice") or []
            if not post_offices:
                self._cache_write(pin, not_found, _NEGATIVE_TTL)
                return not_found

            po = post_offices[0]
            result = {
                "pincode": pin,
                "found": True,
                "city": po.get("District") or None,
                "state": po.get("State") or None,
            }
            self._cache_write(pin, result, _POSITIVE_TTL)
            return result

        except (IndexError, KeyError, TypeError) as exc:
            # Unexpected response shape — treat as transient, don't cache.
            logger.warning("unexpected pincode API response for %s: %s", pin, exc)
            return not_found
