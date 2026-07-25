"""Reverse-geocode lat/lng → pincode/city/state/area/road via Nominatim
(primary) with BigDataCloud as a postcode/city/state fallback, both backed by
Redis caching.

Design decisions (mirrors pincode_service.py):
- Redis cache: positives for 30 days, negatives for 1 day.
- Redis errors are swallowed — cache is a perf optimization, not correctness.
- Any network/parse/timeout exception → {found: False} with no caching
  (transient failures must not poison the cache).
- Only definitive "outside India" or "no valid postcode from either provider"
  responses are cached as negative.
- Lat/lng are rounded to 3 decimal places (~110 m precision) BEFORE both the
  cache key lookup and the upstream calls — this collapses nearby requests onto
  a single cache entry and avoids storing precise user coordinates.
- found=True ONLY when country_code == "in" (Nominatim) or "IN" (BDC) and
  postcode matches ^[1-9][0-9]{5}$ (valid Indian 6-digit pincode).
- Autofill is always decoration — checkout is never blocked by a lookup failure.

Provider waterfall:
  1. Nominatim (OpenStreetMap) — primary; provides road + area + city + state +
     postcode.  User-Agent required by their usage policy.
  2. BigDataCloud — postcode/city/state fallback when Nominatim succeeds but
     returns no valid Indian postcode, OR when Nominatim errors transiently.
     road/area from Nominatim are preserved when Nominatim partially succeeded.

Cache key prefix bumped to geocode:rev2: — old keys used the old shape and the
old (BDC-only) provider; stale entries must not be served.
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

_CACHE_KEY_PREFIX = "geocode:rev2:"
_POSITIVE_TTL = 30 * 24 * 60 * 60   # 30 days in seconds
_NEGATIVE_TTL = 1 * 24 * 60 * 60    # 1 day in seconds

_NOMINATIM_URL = (
    "https://nominatim.openstreetmap.org/reverse"
    "?format=jsonv2&lat={lat}&lon={lng}&zoom=18&addressdetails=1&accept-language=en"
)
_NOMINATIM_USER_AGENT = "shopwellvia-store/1.0 (marketing@shopwellvia.in)"

_BIGDATACLOUD_URL = (
    "https://api.bigdatacloud.net/data/reverse-geocode-client"
    "?latitude={lat}&longitude={lng}&localityLanguage=en"
)


class GeocodeService:
    def __init__(self) -> None:
        self._redis = get_redis()

    def reverse(self, lat: float, lng: float) -> dict:
        """Return ``{found, pincode, city, state, area, road}`` for the given coordinates.

        Always returns a dict — never raises.  ``found=False`` signals that no
        valid Indian pincode could be determined; callers should fall back to
        manual entry.  ``area`` and ``road`` may be None even when found=True.
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
        """Orchestrate Nominatim → BigDataCloud waterfall, then cache.

        Fallback rules
        --------------
        - Nominatim success + valid IN postcode  → use Nominatim exclusively.
        - Nominatim success + no valid postcode  → keep road/area from Nominatim,
          call BigDataCloud for pincode/city/state.
        - Nominatim transient error              → full BigDataCloud fallback,
          road/area stay None.
        - Both fail transiently                  → found=False, not cached.
        - Definitive non-IN or no-postcode-from-either → found=False, cached negative.
        """
        not_found: dict = {
            "found": False,
            "pincode": None,
            "city": None,
            "state": None,
            "area": None,
            "road": None,
        }

        # ---- 1. Try Nominatim ----
        nominatim_data = self._fetch_from_nominatim(lat, lng)

        if nominatim_data is not None:
            # Parse Nominatim fields.
            nom = self._parse_nominatim(nominatim_data)

            if nom is None:
                # Unexpected shape — treat as transient, don't cache.
                return not_found

            nom_country = nom["country_code"]   # already lowercased
            nom_postcode = nom["postcode"]
            road = nom["road"]
            area = nom["area"]
            city = nom["city"]
            state = nom["state"]

            if nom_country != "in":
                # Definitively outside India — cache negative immediately.
                self._cache_write(lat, lng, not_found, _NEGATIVE_TTL)
                return not_found

            if _PINCODE_RE.match(nom_postcode):
                # Nominatim gave us everything — no fallback needed.
                result: dict = {
                    "found": True,
                    "pincode": nom_postcode,
                    "city": city,
                    "state": state,
                    "area": area,
                    "road": road,
                }
                self._cache_write(lat, lng, result, _POSITIVE_TTL)
                return result

            # Nominatim is IN but postcode missing/invalid — try BDC for pincode.
            bdc_data = self._fetch_from_bigdatacloud(lat, lng)
            if bdc_data is None:
                # BDC transient error — neither provider gave us a definitive
                # answer; don't cache.
                return not_found

            bdc = self._parse_bigdatacloud(bdc_data)
            if bdc is None:
                return not_found

            bdc_country = bdc["country_code"]   # already uppercased
            bdc_postcode = bdc["postcode"]

            if bdc_country != "IN" or not _PINCODE_RE.match(bdc_postcode):
                # Both providers agree no valid Indian postcode exists for these
                # coordinates — cache as definitive negative.
                self._cache_write(lat, lng, not_found, _NEGATIVE_TTL)
                return not_found

            # Merge: Nominatim road/area + BDC pincode; prefer Nominatim city/state
            # when present, otherwise fall back to BDC.
            result = {
                "found": True,
                "pincode": bdc_postcode,
                "city": city or bdc["city"],
                "state": state or bdc["state"],
                "area": area,
                "road": road,
            }
            self._cache_write(lat, lng, result, _POSITIVE_TTL)
            return result

        # ---- 2. Nominatim transient error → full BigDataCloud fallback ----
        bdc_data = self._fetch_from_bigdatacloud(lat, lng)
        if bdc_data is None:
            # Both providers failed transiently — do NOT cache.
            return not_found

        bdc = self._parse_bigdatacloud(bdc_data)
        if bdc is None:
            return not_found

        bdc_country = bdc["country_code"]
        bdc_postcode = bdc["postcode"]

        if bdc_country != "IN" or not _PINCODE_RE.match(bdc_postcode):
            self._cache_write(lat, lng, not_found, _NEGATIVE_TTL)
            return not_found

        result = {
            "found": True,
            "pincode": bdc_postcode,
            "city": bdc["city"],
            "state": bdc["state"],
            "area": None,    # Nominatim failed; no street-level data
            "road": None,
        }
        self._cache_write(lat, lng, result, _POSITIVE_TTL)
        return result

    # ---- Provider fetch functions (isolated for easy testing/swapping) ----

    def _fetch_from_nominatim(self, lat: float, lng: float) -> dict | None:
        """GET Nominatim reverse endpoint; return raw JSON dict or None on error.

        Sends the required User-Agent per Nominatim usage policy.
        Returns None on any network/HTTP/parse error (treat as transient).
        """
        try:
            resp = httpx.get(
                _NOMINATIM_URL.format(lat=lat, lng=lng),
                headers={"User-Agent": _NOMINATIM_USER_AGENT},
                timeout=5.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.debug("Nominatim API error for %s,%s: %s", lat, lng, exc)
            return None

    def _fetch_from_bigdatacloud(self, lat: float, lng: float) -> dict | None:
        """GET BigDataCloud reverse endpoint; return raw JSON dict or None on error."""
        try:
            resp = httpx.get(
                _BIGDATACLOUD_URL.format(lat=lat, lng=lng),
                timeout=5.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.debug("BigDataCloud API error for %s,%s: %s", lat, lng, exc)
            return None

    def _parse_nominatim(self, data: dict) -> dict | None:
        """Extract fields from a Nominatim response dict.

        Returns a normalised dict with keys:
            country_code (str, lowercased), postcode (str), road (str|None),
            area (str|None), city (str|None), state (str|None).
        Returns None if the response shape is unexpected (treat as transient).
        """
        try:
            addr = data.get("address") or {}
            country_code = (addr.get("country_code") or "").strip().lower()
            postcode = (addr.get("postcode") or "").strip()

            road = (addr.get("road") or "").strip() or None

            # area: first non-empty of suburb / neighbourhood / quarter / locality
            area = None
            for key in ("suburb", "neighbourhood", "quarter", "locality"):
                val = (addr.get(key) or "").strip()
                if val:
                    area = val
                    break

            # city: first non-empty of city / town / village / city_district
            city = None
            for key in ("city", "town", "village", "city_district"):
                val = (addr.get(key) or "").strip()
                if val:
                    city = val
                    break

            state = (addr.get("state") or "").strip() or None

            return {
                "country_code": country_code,
                "postcode": postcode,
                "road": road,
                "area": area,
                "city": city,
                "state": state,
            }
        except (AttributeError, TypeError) as exc:
            logger.warning(
                "unexpected Nominatim response shape for data=%r: %s", data, exc
            )
            return None

    def _parse_bigdatacloud(self, data: dict) -> dict | None:
        """Extract fields from a BigDataCloud response dict.

        Returns a normalised dict with keys:
            country_code (str, uppercased), postcode (str),
            city (str|None), state (str|None).
        Returns None if the response shape is unexpected (treat as transient).
        """
        try:
            country_code = (data.get("countryCode") or "").strip().upper()
            postcode = (data.get("postcode") or "").strip()
            city = (data.get("city") or data.get("locality") or "").strip() or None
            state = (data.get("principalSubdivision") or "").strip() or None
            return {
                "country_code": country_code,
                "postcode": postcode,
                "city": city,
                "state": state,
            }
        except (AttributeError, TypeError) as exc:
            logger.warning(
                "unexpected BigDataCloud response shape for data=%r: %s", data, exc
            )
            return None
