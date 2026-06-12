"""Unit-style tests for GeocodeService.reverse and the /shipping/geocode/reverse
endpoint.

Fully hermetic — no real HTTP calls to Nominatim / BigDataCloud and no real
Redis writes.  Stubbing strategy mirrors test_pincode_service.py:
  1. Instantiate GeocodeService() normally (real Redis client in __init__).
  2. Replace self._redis with a _FakeRedis instance after construction.
  3. Patch _fetch_from_nominatim / _fetch_from_bigdatacloud in the
     app.services.geocode_service module namespace so each provider can be
     controlled independently.

Test matrix
-----------
Service-level:
  1.  Happy path — Nominatim road+suburb+postcode → found=True, area/road mapped.
  2.  Positive result cached with positive TTL (new key prefix geocode:rev2:).
  3.  Nominatim valid IN but postcode missing → BDC fallback fills pincode;
      road/area preserved from Nominatim; result cached positive.
  4.  Nominatim transient error → full BDC fallback, road/area None, cached.
  5.  Both fail transiently → found=False, NOT cached.
  6.  Non-Indian country code (Nominatim) → found=False, cached negative.
  7.  Empty postcode from Nominatim AND BDC both definitive → found=False, cached.
  8.  Invalid postcode pattern (starts with 0) → found=False.
  9.  Cache-key rounding: nearby coords share key, provider called once.
  10. Cache-key format uses three decimal places with new prefix.
  11. User-Agent header asserted on the Nominatim HTTP request.

Endpoint-level (via TestClient):
  12. lat=91 → 422.
  13. lng=181 → 422.
  14. Missing query params → 422.
  15. Valid coords + patched service → 200 with {found,pincode,city,state,area,road}.
  16. Service found=False → HTTP 200.
  17. lat=-90 boundary → 200.

Runs inside the backend container:

    docker compose exec -e PYTHONPATH=/app:/home/app/.local/lib/python3.12/site-packages \\
        backend python -m pytest tests/test_geocode_service.py -q
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest
from fastapi.testclient import TestClient

from app.services.geocode_service import (
    GeocodeService,
    _CACHE_KEY_PREFIX,
    _NEGATIVE_TTL,
    _NOMINATIM_USER_AGENT,
    _POSITIVE_TTL,
)


# ---------------------------------------------------------------------------
# Redis stub helpers
# ---------------------------------------------------------------------------

class _FakeRedis:
    """Minimal in-memory Redis stub supporting get / setex / delete."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._ttls: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self._store[key] = value
        self._ttls[key] = ttl

    def delete(self, key: str) -> None:
        self._store.pop(key, None)
        self._ttls.pop(key, None)


def _make_service(fake_redis: _FakeRedis | None = None) -> GeocodeService:
    svc = GeocodeService()
    svc._redis = fake_redis or _FakeRedis()
    return svc


# ---------------------------------------------------------------------------
# Nominatim HTTP response builder
# ---------------------------------------------------------------------------

def _nom_response(
    *,
    country_code: str = "in",
    postcode: str = "560001",
    road: str | None = "MG Road",
    suburb: str | None = "Shivajinagar",
    city: str | None = "Bengaluru",
    state: str | None = "Karnataka",
) -> dict:
    """Build a minimal Nominatim-shaped response dict."""
    addr: dict = {"country_code": country_code}
    if postcode:
        addr["postcode"] = postcode
    if road:
        addr["road"] = road
    if suburb:
        addr["suburb"] = suburb
    if city:
        addr["city"] = city
    if state:
        addr["state"] = state
    return {"place_id": 123, "address": addr}


def _nom_no_postcode(
    *,
    road: str = "Brigade Road",
    suburb: str = "Ashok Nagar",
    city: str = "Bengaluru",
    state: str = "Karnataka",
) -> dict:
    """Nominatim response with no postcode field (common in India on OSM)."""
    return {
        "place_id": 456,
        "address": {
            "country_code": "in",
            "road": road,
            "suburb": suburb,
            "city": city,
            "state": state,
        },
    }


# ---------------------------------------------------------------------------
# BigDataCloud HTTP response builder
# ---------------------------------------------------------------------------

def _bdc_response(
    *,
    postcode: str = "560001",
    country_code: str = "IN",
    city: str = "Bengaluru",
    state: str = "Karnataka",
    locality: str | None = None,
) -> dict:
    """Return a BigDataCloud-shaped response dict."""
    payload: dict = {
        "countryCode": country_code,
        "postcode": postcode,
        "city": city,
        "principalSubdivision": state,
    }
    if locality is not None:
        payload["locality"] = locality
    return payload


def _bdc_no_postcode(country_code: str = "IN") -> dict:
    return {
        "countryCode": country_code,
        "postcode": "",
        "city": "Some City",
        "principalSubdivision": "Some State",
    }


# ---------------------------------------------------------------------------
# Patch helpers — patch the two provider methods directly on the instance
# so tests don't need to know about httpx internals.
# ---------------------------------------------------------------------------

def _patch_nom(svc: GeocodeService, return_value: Any):
    """Monkey-patch _fetch_from_nominatim on svc to return a fixed value."""
    svc._fetch_from_nominatim = MagicMock(return_value=return_value)
    return svc._fetch_from_nominatim


def _patch_bdc(svc: GeocodeService, return_value: Any):
    """Monkey-patch _fetch_from_bigdatacloud on svc to return a fixed value."""
    svc._fetch_from_bigdatacloud = MagicMock(return_value=return_value)
    return svc._fetch_from_bigdatacloud


# ---------------------------------------------------------------------------
# 1. Happy path — Nominatim provides everything
# ---------------------------------------------------------------------------

class TestNominatimHappyPath:

    def test_road_suburb_postcode_returns_found_true_with_area_road(self) -> None:
        """Nominatim returns road + suburb + valid IN postcode → found=True with
        area and road populated."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        _patch_nom(svc, _nom_response(
            postcode="560001",
            road="MG Road",
            suburb="Shivajinagar",
            city="Bengaluru",
            state="Karnataka",
        ))
        _patch_bdc(svc, None)  # must NOT be called

        result = svc.reverse(12.9716, 77.5946)

        assert result["found"] is True
        assert result["pincode"] == "560001"
        assert result["city"] == "Bengaluru"
        assert result["state"] == "Karnataka"
        assert result["area"] == "Shivajinagar"
        assert result["road"] == "MG Road"
        svc._fetch_from_bigdatacloud.assert_not_called()

    def test_positive_result_cached_with_positive_ttl(self) -> None:
        """Successful Nominatim result must be written to Redis with positive TTL."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        _patch_nom(svc, _nom_response(postcode="560001"))
        _patch_bdc(svc, None)

        svc.reverse(12.9716, 77.5946)

        expected_key = f"{_CACHE_KEY_PREFIX}12.972:77.595"
        assert expected_key in fake._store, "Positive result must be cached"
        stored = json.loads(fake._store[expected_key])
        assert stored["found"] is True
        assert stored["area"] == "Shivajinagar"
        assert stored["road"] == "MG Road"
        assert fake._ttls[expected_key] == _POSITIVE_TTL

    def test_area_fallback_order_suburb_first(self) -> None:
        """area picks the first non-empty of suburb/neighbourhood/quarter/locality."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        _patch_nom(svc, {
            "address": {
                "country_code": "in",
                "postcode": "400001",
                "road": "Marine Drive",
                "neighbourhood": "Churchgate",
                "suburb": "",   # empty — skip
                "city": "Mumbai",
                "state": "Maharashtra",
            }
        })
        _patch_bdc(svc, None)

        result = svc.reverse(18.9322, 72.8264)

        assert result["found"] is True
        assert result["area"] == "Churchgate"

    def test_city_fallback_order_town_village(self) -> None:
        """city picks first non-empty of city/town/village/city_district."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        _patch_nom(svc, {
            "address": {
                "country_code": "in",
                "postcode": "500001",
                "road": "Tank Bund Road",
                "suburb": "Abids",
                "town": "Hyderabad",   # city key absent → use town
                "state": "Telangana",
            }
        })
        _patch_bdc(svc, None)

        result = svc.reverse(17.3850, 78.4867)

        assert result["found"] is True
        assert result["city"] == "Hyderabad"


# ---------------------------------------------------------------------------
# 2. Nominatim valid IN but postcode missing → BDC fills pincode
# ---------------------------------------------------------------------------

class TestNominatimMissingPostcodeBDCFallback:

    def test_missing_postcode_bdc_fills_pincode_road_area_preserved(self) -> None:
        """Nominatim: IN, no postcode.  BDC: valid pincode.
        road + area come from Nominatim; pincode from BDC; result cached positive.
        """
        fake = _FakeRedis()
        svc = _make_service(fake)

        _patch_nom(svc, _nom_no_postcode(
            road="Brigade Road",
            suburb="Ashok Nagar",
            city="Bengaluru",
            state="Karnataka",
        ))
        _patch_bdc(svc, _bdc_response(
            postcode="560025",
            city="Bengaluru",
            state="Karnataka",
        ))

        result = svc.reverse(12.9716, 77.5946)

        assert result["found"] is True
        assert result["pincode"] == "560025"
        assert result["road"] == "Brigade Road"
        assert result["area"] == "Ashok Nagar"
        assert result["city"] == "Bengaluru"   # Nominatim city preferred
        assert result["state"] == "Karnataka"
        # Must be cached positive.
        key = f"{_CACHE_KEY_PREFIX}12.972:77.595"
        assert key in fake._store
        assert fake._ttls[key] == _POSITIVE_TTL

    def test_nominatim_city_preferred_over_bdc_when_both_present(self) -> None:
        """When Nominatim returns a city and BDC also has one, Nominatim wins."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        _patch_nom(svc, _nom_no_postcode(city="Bengaluru", state="Karnataka"))
        _patch_bdc(svc, _bdc_response(postcode="560001", city="BDC City", state="BDC State"))

        result = svc.reverse(12.9716, 77.5946)

        assert result["city"] == "Bengaluru"
        assert result["state"] == "Karnataka"

    def test_bdc_city_used_when_nominatim_city_missing(self) -> None:
        """When Nominatim lacks a city value, BDC city fills it."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        _patch_nom(svc, {
            "address": {
                "country_code": "in",
                "suburb": "Some Area",
                "road": "Some Road",
                "state": "Karnataka",
                # No city/town/village/city_district key at all.
            }
        })
        _patch_bdc(svc, _bdc_response(postcode="560001", city="Bengaluru", state="Karnataka"))

        result = svc.reverse(12.9716, 77.5946)

        assert result["found"] is True
        assert result["city"] == "Bengaluru"

    def test_both_no_valid_postcode_cached_negative(self) -> None:
        """Nominatim IN + no postcode, BDC also no valid postcode → cached negative."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        _patch_nom(svc, _nom_no_postcode())
        _patch_bdc(svc, _bdc_no_postcode(country_code="IN"))

        result = svc.reverse(28.6139, 77.2090)

        assert result["found"] is False
        key = f"{_CACHE_KEY_PREFIX}28.614:77.209"
        assert key in fake._store
        assert fake._ttls[key] == _NEGATIVE_TTL

    def test_nominatim_missing_postcode_bdc_transient_not_cached(self) -> None:
        """Nominatim IN + no postcode; BDC transient → neither answer is definitive;
        result must NOT be cached."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        _patch_nom(svc, _nom_no_postcode())
        _patch_bdc(svc, None)   # transient

        result = svc.reverse(12.9716, 77.5946)

        assert result["found"] is False
        assert len(fake._store) == 0, "Transient BDC after partial Nominatim must not be cached"


# ---------------------------------------------------------------------------
# 3. Nominatim transient error → full BDC fallback
# ---------------------------------------------------------------------------

class TestNominatimTransientFullBDCFallback:

    def test_nominatim_error_bdc_success_road_area_none(self) -> None:
        """Nominatim transient → BDC provides IN postcode; road/area stay None."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        _patch_nom(svc, None)   # transient
        _patch_bdc(svc, _bdc_response(postcode="400001", city="Mumbai", state="Maharashtra"))

        result = svc.reverse(19.0760, 72.8777)

        assert result["found"] is True
        assert result["pincode"] == "400001"
        assert result["city"] == "Mumbai"
        assert result["state"] == "Maharashtra"
        assert result["road"] is None
        assert result["area"] is None

    def test_nominatim_error_bdc_success_cached_positive(self) -> None:
        """Full BDC fallback (Nominatim error) → result must still be cached positive."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        _patch_nom(svc, None)
        _patch_bdc(svc, _bdc_response(postcode="400001"))

        svc.reverse(19.0760, 72.8777)

        key = f"{_CACHE_KEY_PREFIX}19.076:72.878"
        assert key in fake._store
        assert fake._ttls[key] == _POSITIVE_TTL

    def test_nominatim_error_bdc_non_indian_cached_negative(self) -> None:
        """Nominatim error + BDC returns non-IN → definitive negative, cached."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        _patch_nom(svc, None)
        _patch_bdc(svc, _bdc_response(postcode="SW1A 1AA", country_code="GB"))

        result = svc.reverse(51.501, -0.142)

        assert result["found"] is False
        key = f"{_CACHE_KEY_PREFIX}51.501:-0.142"
        assert key in fake._store
        assert fake._ttls[key] == _NEGATIVE_TTL


# ---------------------------------------------------------------------------
# 4. Both providers fail transiently → found=False, NOT cached
# ---------------------------------------------------------------------------

class TestBothProvidersFail:

    def test_both_fail_returns_found_false_not_cached(self) -> None:
        """Both Nominatim and BDC transient errors → found=False; nothing cached."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        _patch_nom(svc, None)
        _patch_bdc(svc, None)

        result = svc.reverse(12.9716, 77.5946)

        assert result["found"] is False
        assert len(fake._store) == 0, "Both-transient result must NOT be cached"


# ---------------------------------------------------------------------------
# 5. Non-Indian country code from Nominatim
# ---------------------------------------------------------------------------

class TestNonIndianNominatim:

    def test_non_indian_country_code_returns_found_false(self) -> None:
        """Nominatim countryCode != 'in' → found=False, no BDC call."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        _patch_nom(svc, _nom_response(
            country_code="gb",
            postcode="SW1A 1AA",
            city="London",
            state="England",
        ))
        bdc_mock = _patch_bdc(svc, None)

        result = svc.reverse(51.501, -0.1415)

        assert result["found"] is False
        bdc_mock.assert_not_called()

    def test_non_indian_result_cached_negative(self) -> None:
        """Non-Indian result is definitive — must be cached with negative TTL."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        _patch_nom(svc, _nom_response(country_code="gb", postcode="SW1A 1AA"))
        _patch_bdc(svc, None)

        svc.reverse(51.501, -0.142)

        key = f"{_CACHE_KEY_PREFIX}51.501:-0.142"
        assert key in fake._store
        stored = json.loads(fake._store[key])
        assert stored["found"] is False
        assert fake._ttls[key] == _NEGATIVE_TTL


# ---------------------------------------------------------------------------
# 6. Invalid postcode pattern
# ---------------------------------------------------------------------------

class TestInvalidPostcodePattern:

    def test_postcode_starting_with_zero_returns_found_false(self) -> None:
        """Postcode starting with 0 fails the ^[1-9][0-9]{5}$ regex → found=False."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        _patch_nom(svc, _nom_response(postcode="012345"))
        # Nominatim gave invalid postcode → BDC fallback; make it also invalid.
        _patch_bdc(svc, _bdc_no_postcode(country_code="IN"))

        result = svc.reverse(20.0, 78.0)

        assert result["found"] is False


# ---------------------------------------------------------------------------
# 7. Cache-key rounding
# ---------------------------------------------------------------------------

class TestCacheKeyRounding:

    def test_nearby_coords_share_cache_key_nominatim_called_once(self) -> None:
        """Two coordinates that round to the same 3-dp key must share a cache entry;
        Nominatim must be called exactly once."""
        fake = _FakeRedis()
        svc = _make_service(fake)
        nom_mock = _patch_nom(svc, _nom_response(postcode="560001"))
        _patch_bdc(svc, None)

        result1 = svc.reverse(12.97193, 77.59461)
        result2 = svc.reverse(12.9722, 77.5949)

        assert nom_mock.call_count == 1, "Cache must be hit on second lookup"
        assert result1 == result2
        assert f"{_CACHE_KEY_PREFIX}12.972:77.595" in fake._store

    def test_cache_key_format_uses_three_decimal_places_new_prefix(self) -> None:
        """Cache key must be geocode:rev2:{lat:.3f}:{lng:.3f}."""
        fake = _FakeRedis()
        svc = _make_service(fake)
        _patch_nom(svc, _nom_response(postcode="400001"))
        _patch_bdc(svc, None)

        svc.reverse(19.07283, 72.88261)

        expected_key = f"{_CACHE_KEY_PREFIX}19.073:72.883"
        assert expected_key in fake._store
        assert expected_key.startswith("geocode:rev2:")


# ---------------------------------------------------------------------------
# 8. User-Agent header asserted on Nominatim HTTP request
# ---------------------------------------------------------------------------

class TestNominatimUserAgent:

    def test_nominatim_request_sends_correct_user_agent(self) -> None:
        """Nominatim's usage policy requires a descriptive User-Agent header.
        Verify _fetch_from_nominatim passes it via httpx.get."""
        import httpx

        fake = _FakeRedis()
        svc = _make_service(fake)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _nom_response(postcode="560001")

        with patch("app.services.geocode_service.httpx.get", return_value=mock_resp) as mock_get:
            # Call the real _fetch_from_nominatim (NOT monkey-patched) so we test
            # the actual httpx.get call.
            svc._fetch_from_nominatim(12.972, 77.595)

        assert mock_get.called
        _, kwargs = mock_get.call_args
        headers = kwargs.get("headers", {})
        assert "User-Agent" in headers, "Nominatim call must include User-Agent header"
        assert headers["User-Agent"] == _NOMINATIM_USER_AGENT

    def test_nominatim_user_agent_value_matches_constant(self) -> None:
        """The exported _NOMINATIM_USER_AGENT constant must match the expected value."""
        assert _NOMINATIM_USER_AGENT == "shopwellvia-store/1.0 (marketing@shopwellvia.in)"


# ---------------------------------------------------------------------------
# 9. Endpoint-level tests via TestClient
# ---------------------------------------------------------------------------

class TestReverseGeocodeEndpoint:

    @pytest.fixture
    def client(self):
        from app.main import app
        return TestClient(app)

    def test_lat_out_of_range_returns_422(self, client: TestClient) -> None:
        """lat=91 is outside the [-90, 90] allowed range → FastAPI returns 422."""
        resp = client.get("/api/v1/shipping/geocode/reverse?lat=91&lng=77.5")
        assert resp.status_code == 422

    def test_lng_out_of_range_returns_422(self, client: TestClient) -> None:
        """lng=181 is outside the [-180, 180] allowed range → FastAPI returns 422."""
        resp = client.get("/api/v1/shipping/geocode/reverse?lat=12.9&lng=181")
        assert resp.status_code == 422

    def test_missing_query_params_returns_422(self, client: TestClient) -> None:
        """Both lat and lng are required; omitting them → 422."""
        resp = client.get("/api/v1/shipping/geocode/reverse")
        assert resp.status_code == 422

    def test_valid_coordinates_with_patched_service_returns_200_and_shape(
        self, client: TestClient
    ) -> None:
        """Valid in-range coordinates with the GeocodeService patched → HTTP 200
        with {found, pincode, city, state, area, road} shape."""
        mocked_result = {
            "found": True,
            "pincode": "560001",
            "city": "Bengaluru",
            "state": "Karnataka",
            "area": "Shivajinagar",
            "road": "MG Road",
        }

        with patch(
            "app.api.v1.endpoints.shipping.GeocodeService.reverse",
            return_value=mocked_result,
        ):
            resp = client.get("/api/v1/shipping/geocode/reverse?lat=12.972&lng=77.595")

        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] is True
        assert data["pincode"] == "560001"
        assert data["city"] == "Bengaluru"
        assert data["state"] == "Karnataka"
        assert data["area"] == "Shivajinagar"
        assert data["road"] == "MG Road"

    def test_valid_coordinates_found_false_returns_200(
        self, client: TestClient
    ) -> None:
        """Service returning found=False is still HTTP 200 (degraded-but-safe path)."""
        mocked_result = {
            "found": False,
            "pincode": None,
            "city": None,
            "state": None,
            "area": None,
            "road": None,
        }

        with patch(
            "app.api.v1.endpoints.shipping.GeocodeService.reverse",
            return_value=mocked_result,
        ):
            resp = client.get("/api/v1/shipping/geocode/reverse?lat=51.5&lng=-0.1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] is False
        # New fields must be present in response shape even when found=False.
        assert "area" in data
        assert "road" in data

    def test_response_shape_includes_area_and_road_fields(
        self, client: TestClient
    ) -> None:
        """The response schema must always include area and road keys (possibly None)."""
        mocked_result = {
            "found": True,
            "pincode": "400001",
            "city": "Mumbai",
            "state": "Maharashtra",
            "area": None,   # May be None even when found=True
            "road": None,
        }

        with patch(
            "app.api.v1.endpoints.shipping.GeocodeService.reverse",
            return_value=mocked_result,
        ):
            resp = client.get("/api/v1/shipping/geocode/reverse?lat=18.932&lng=72.826")

        assert resp.status_code == 200
        data = resp.json()
        assert "area" in data
        assert "road" in data
        assert data["area"] is None
        assert data["road"] is None

    def test_lat_at_boundary_minus_90_returns_200(self, client: TestClient) -> None:
        """lat=-90 is exactly on the boundary and must be accepted (not 422)."""
        mocked_result = {
            "found": False,
            "pincode": None,
            "city": None,
            "state": None,
            "area": None,
            "road": None,
        }

        with patch(
            "app.api.v1.endpoints.shipping.GeocodeService.reverse",
            return_value=mocked_result,
        ):
            resp = client.get("/api/v1/shipping/geocode/reverse?lat=-90&lng=0")

        assert resp.status_code == 200
