"""Reverse-geocode lat/lng → pincode/city/state via BigDataCloud with Redis caching.

Design decisions (mirrors pincode_service.py):
- Redis cache: positives for 30 days, negatives for 1 day.
- Redis errors are swallowed — cache is a perf optimization, not correctness.
- Any network/parse/timeout exception → {found: False} with no caching
  (transient failures must not poison the cache).
- Only definitive "outside India" or "no valid postcode" responses are cached
  as negative.
- Lat/lng are rounded to 3 decimal places (~110 m precision) BEFORE both the
  cache key lookup and the upstream call — this collapses nearby requests onto
  a single cache entry and avoids storing precise user coordinates.
- found=True ONLY when countryCode == "IN" and postcode matches
  ^[1-9][0-9]{5}$ (valid Indian 6-digit pincode, first digit non-zero).
- Autofill is always decoration — checkout is never blocked by a lookup failure.

Provider: BigDataCloud reverse-geocode-client (free, no API key):
  GET https://api.bigdatacloud.net/data/reverse-geocode-client
       ?latitude={lat}&longitude={lng}&localityLanguage=en
  Response fields used: postcode, city (fallback: locality),
  principalSubdivision, countryCode.

Swap-in fallback (if BigDataCloud ToS becomes an issue):
  Nominatim — GET https://nominatim.openstreetmap.org/reverse?format=jsonv2
               &lat={lat}&lon={lng}
  Requires a User-Agent header and must not exceed 1 req/s.  With 30-day
  caching + 60/min/IP rate limit the effective upstream rate is negligible.
  The provider fetch+parse is isolated in _fetch_from_provider() so the swap
  is a one-function change.
"""
from __future__ import annotations

import json
import logging
import re

import httpx
import redis

from app.core.config import settings

logger = logging.getLogger(__name__)

_PINCODE_RE = re.compile(r"^[1-9][0-9]{5}$")

_CACHE_KEY_PREFIX = "geocode:rev:"
_POSITIVE_TTL = 30 * 24 * 60 * 60   # 30 days in seconds
_NEGATIVE_TTL = 1 * 24 * 60 * 60    # 1 day in seconds

_BIGDATACLOUD_URL = (
    "https://api.bigdatacloud.net/data/reverse-geocode-client"
    "?latitude={lat}&longitude={lng}&localityLanguage=en"
)


class GeocodeService:
    def __init__(self) -> None:
        self._redis = redis.Redis.from_url(
            settings.REDIS_URL, decode_responses=True
        )

    def reverse(self, lat: float, lng: float) -> dict:
        """Return ``{found, pincode, city, state}`` for the given coordinates.

        Always returns a dict — never raises.  ``found=False`` signals that no
        valid Indian pincode could be determined; callers should fall back to
        manual entry.
        """
        # Round to 3 decimal places before any cache or network operation.
        # This ~110 m bucketing collapses nearby requests and avoids storing
        # precise location data in Redis.
        lat_r = round(lat, 3)
        lng_r = round(lng, 3)

        cached = self._cache_read(lat_r, lng_r)
        if cached is not None:
            return cached

        return self._fetch_and_cache(lat_r, lng_r)

    # ---- internals ----

    def _cache_key(self, lat: float, lng: float) -> str:
        return f"{_CACHE_KEY_PREFIX}{lat:.3f}:{lng:.3f}"

    def _cache_read(self, lat: float, lng: float) -> dict | None:
        """Return the cached result dict, or None on miss / Redis error."""
        try:
            raw = self._redis.get(self._cache_key(lat, lng))
        except redis.RedisError as exc:
            logger.debug("geocode cache read failed for %s,%s: %s", lat, lng, exc)
            return None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("bad geocode cache value for %s,%s: %s", lat, lng, exc)
            return None

    def _cache_write(self, lat: float, lng: float, result: dict, ttl: int) -> None:
        try:
            self._redis.setex(self._cache_key(lat, lng), ttl, json.dumps(result))
        except redis.RedisError as exc:
            logger.debug("geocode cache write failed for %s,%s: %s", lat, lng, exc)

    def _fetch_and_cache(self, lat: float, lng: float) -> dict:
        """Call the upstream provider, parse the response, cache appropriately.

        Provider fetch+parse is isolated here so swapping to Nominatim or any
        other provider only requires changing this one function.
        """
        not_found: dict = {"found": False, "pincode": None, "city": None, "state": None}

        data = self._fetch_from_provider(lat, lng)
        if data is None:
            # Transient error — do NOT cache negative.
            return not_found

        try:
            country_code = (data.get("countryCode") or "").strip().upper()
            postcode = (data.get("postcode") or "").strip()
            city = (data.get("city") or data.get("locality") or "").strip() or None
            state = (data.get("principalSubdivision") or "").strip() or None
        except (AttributeError, TypeError) as exc:
            # Unexpected response shape — treat as transient, don't cache.
            logger.warning("unexpected geocode API response for %s,%s: %s", lat, lng, exc)
            return not_found

        if country_code != "IN" or not _PINCODE_RE.match(postcode):
            # Definitive negative (outside India or no valid postcode) — cache.
            self._cache_write(lat, lng, not_found, _NEGATIVE_TTL)
            return not_found

        result: dict = {
            "found": True,
            "pincode": postcode,
            "city": city,
            "state": state,
        }
        self._cache_write(lat, lng, result, _POSITIVE_TTL)
        return result

    def _fetch_from_provider(self, lat: float, lng: float) -> dict | None:
        """Call BigDataCloud and return the raw JSON dict, or None on error.

        Keeping the HTTP call isolated here makes swapping to Nominatim trivial:
          url = f"https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat={lat}&lon={lng}"
          headers = {"User-Agent": "shopwellvia/1.0 (marketing@shopwellvia.in)"}
          resp = httpx.get(url, headers=headers, timeout=5.0)
          # Nominatim postcode: data["address"]["postcode"]
          # city: data["address"].get("city") or data["address"].get("town")
          # state: data["address"]["state"]
          # country_code: data["address"]["country_code"].upper()
        """
        try:
            resp = httpx.get(
                _BIGDATACLOUD_URL.format(lat=lat, lng=lng),
                timeout=5.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            # Network/timeout/parse errors are transient — caller must NOT cache.
            logger.debug("geocode API error for %s,%s: %s", lat, lng, exc)
            return None
