"""Unit-style tests for GeocodeService.reverse and the /shipping/geocode/reverse
endpoint.

Fully hermetic — no real HTTP calls to BigDataCloud and no real Redis writes.
Stubbing strategy mirrors test_pincode_service.py:
  1. Instantiate GeocodeService() normally (real Redis client in __init__).
  2. Replace self._redis with a _FakeRedis instance after construction.
  3. Patch httpx.get in the app.services.geocode_service module namespace.

Test matrix
-----------
1. Happy path: IN postcode → found=True with mapped fields; positive TTL cached.
2. Cache-key rounding: (12.97193, 77.59461) and (12.9722, 77.5949) share key
   geocode:rev:12.972:77.595 — second lookup hits cache, provider called once.
3. Non-Indian response (countryCode="GB") → found=False, negative TTL cached.
4. Missing postcode (empty string) → found=False, negative TTL cached.
5. Network error (httpx raises) → found=False, NOT cached.
6. Endpoint validation: lat=91 → 422; valid coordinates with service patched → 200.

Runs inside the backend container:

    docker compose exec backend pytest tests/test_geocode_service.py -v
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.services.geocode_service import (
    GeocodeService,
    _NEGATIVE_TTL,
    _POSITIVE_TTL,
    _CACHE_KEY_PREFIX,
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
# BigDataCloud HTTP response stubs
# ---------------------------------------------------------------------------

def _bdc_response(
    *,
    postcode: str = "560001",
    country_code: str = "IN",
    city: str = "Bengaluru",
    state: str = "Karnataka",
    locality: str | None = None,
) -> Any:
    """Return a mock httpx.Response for a successful BigDataCloud call."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    payload: dict = {
        "countryCode": country_code,
        "postcode": postcode,
        "city": city,
        "principalSubdivision": state,
    }
    if locality is not None:
        payload["locality"] = locality
    mock_resp.json.return_value = payload
    return mock_resp


def _bdc_no_postcode(country_code: str = "IN") -> Any:
    """Simulate a valid-country response that has no postcode."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "countryCode": country_code,
        "postcode": "",
        "city": "Some City",
        "principalSubdivision": "Some State",
    }
    return mock_resp


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------

class TestGeocodeServiceHappyPath:

    def test_indian_postcode_returns_found_true_with_fields(self) -> None:
        """Provider returns valid IN postcode → found=True; pincode, city, state
        are mapped correctly."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        with patch(
            "app.services.geocode_service.httpx.get",
            return_value=_bdc_response(
                postcode="560001",
                country_code="IN",
                city="Bengaluru",
                state="Karnataka",
            ),
        ) as mock_http:
            result = svc.reverse(12.9716, 77.5946)

        assert result["found"] is True
        assert result["pincode"] == "560001"
        assert result["city"] == "Bengaluru"
        assert result["state"] == "Karnataka"
        mock_http.assert_called_once()

    def test_positive_result_is_cached_with_positive_ttl(self) -> None:
        """After a successful IN lookup the result must be written to the fake
        Redis store with the positive TTL."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        with patch(
            "app.services.geocode_service.httpx.get",
            return_value=_bdc_response(postcode="560001", country_code="IN"),
        ):
            svc.reverse(12.9716, 77.5946)

        # Cache key uses 3-dp rounding.
        expected_key = f"{_CACHE_KEY_PREFIX}12.972:77.595"
        assert expected_key in fake._store, "Positive result must be cached in Redis"
        stored = json.loads(fake._store[expected_key])
        assert stored["found"] is True
        assert fake._ttls[expected_key] == _POSITIVE_TTL

    def test_city_falls_back_to_locality_when_city_missing(self) -> None:
        """When the provider omits 'city' but provides 'locality', locality is
        used as the city value."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "countryCode": "IN",
            "postcode": "560001",
            "city": "",
            "locality": "Koramangala",
            "principalSubdivision": "Karnataka",
        }

        with patch("app.services.geocode_service.httpx.get", return_value=mock_resp):
            result = svc.reverse(12.9345, 77.6127)

        assert result["found"] is True
        assert result["city"] == "Koramangala"


# ---------------------------------------------------------------------------
# 2. Cache-key rounding collapses nearby coordinates
# ---------------------------------------------------------------------------

class TestGeocodeServiceCacheKeyRounding:

    def test_nearby_coords_share_cache_key_provider_called_once(self) -> None:
        """Coordinates that round to the same 3-dp key must share a single cache
        entry.  The provider must be called exactly once across both lookups."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        with patch(
            "app.services.geocode_service.httpx.get",
            return_value=_bdc_response(postcode="560001", country_code="IN"),
        ) as mock_http:
            # 12.97193 → 12.972, 77.59461 → 77.595
            result1 = svc.reverse(12.97193, 77.59461)
            # 12.9722  → 12.972, 77.5949  → 77.595
            result2 = svc.reverse(12.9722, 77.5949)

        assert mock_http.call_count == 1, (
            "Both nearby coords round to the same key; provider must be called once"
        )
        assert result1 == result2

        expected_key = f"{_CACHE_KEY_PREFIX}12.972:77.595"
        assert expected_key in fake._store

    def test_cache_key_format_uses_three_decimal_places(self) -> None:
        """The cache key must be formatted as geocode:rev:{lat:.3f}:{lng:.3f}."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        with patch(
            "app.services.geocode_service.httpx.get",
            return_value=_bdc_response(postcode="400001", country_code="IN"),
        ):
            svc.reverse(19.07283, 72.88261)

        # 19.07283 rounds to 19.073, 72.88261 rounds to 72.883
        expected_key = f"{_CACHE_KEY_PREFIX}19.073:72.883"
        assert expected_key in fake._store


# ---------------------------------------------------------------------------
# 3. Non-Indian postcode → found=False, negative TTL
# ---------------------------------------------------------------------------

class TestGeocodeServiceNonIndian:

    def test_non_indian_country_code_returns_found_false(self) -> None:
        """Provider returns countryCode='GB' (or any non-IN country) → found=False."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        with patch(
            "app.services.geocode_service.httpx.get",
            return_value=_bdc_response(
                postcode="SW1A 1AA",
                country_code="GB",
                city="London",
                state="England",
            ),
        ):
            result = svc.reverse(51.501, -0.1415)

        assert result["found"] is False

    def test_non_indian_result_cached_with_negative_ttl(self) -> None:
        """A non-Indian result is a definitive negative; it must be cached with
        the negative TTL so the next call hits the cache."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        with patch(
            "app.services.geocode_service.httpx.get",
            return_value=_bdc_response(postcode="SW1A 1AA", country_code="GB"),
        ):
            svc.reverse(51.501, -0.142)

        expected_key = f"{_CACHE_KEY_PREFIX}51.501:-0.142"
        assert expected_key in fake._store
        stored = json.loads(fake._store[expected_key])
        assert stored["found"] is False
        assert fake._ttls[expected_key] == _NEGATIVE_TTL


# ---------------------------------------------------------------------------
# 4. Missing postcode → found=False, negative TTL
# ---------------------------------------------------------------------------

class TestGeocodeServiceMissingPostcode:

    def test_empty_postcode_returns_found_false(self) -> None:
        """Provider returns IN with an empty postcode → found=False."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        with patch(
            "app.services.geocode_service.httpx.get",
            return_value=_bdc_no_postcode(country_code="IN"),
        ):
            result = svc.reverse(28.6139, 77.2090)

        assert result["found"] is False

    def test_empty_postcode_cached_with_negative_ttl(self) -> None:
        """Empty postcode is a definitive negative (no valid pin) → cached negative."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        with patch(
            "app.services.geocode_service.httpx.get",
            return_value=_bdc_no_postcode(country_code="IN"),
        ):
            svc.reverse(28.6139, 77.2090)

        # 28.6139 → 28.614, 77.209 → 77.209
        expected_key = f"{_CACHE_KEY_PREFIX}28.614:77.209"
        assert expected_key in fake._store
        assert fake._ttls[expected_key] == _NEGATIVE_TTL

    def test_invalid_postcode_pattern_not_starting_with_nonzero_returns_found_false(self) -> None:
        """Provider returns a non-Indian-looking postcode (starts with 0) for
        countryCode=IN → found=False."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "countryCode": "IN",
            "postcode": "012345",
            "city": "Test",
            "principalSubdivision": "TestState",
        }

        with patch("app.services.geocode_service.httpx.get", return_value=mock_resp):
            result = svc.reverse(20.0, 78.0)

        assert result["found"] is False


# ---------------------------------------------------------------------------
# 5. Network error → found=False, NOT cached
# ---------------------------------------------------------------------------

class TestGeocodeServiceNetworkError:

    def test_httpx_raises_returns_found_false_and_not_cached(self) -> None:
        """Any httpx exception (timeout, connection error, etc.) must return
        found=False WITHOUT writing anything to the cache so the next request
        retries the real provider."""
        import httpx

        fake = _FakeRedis()
        svc = _make_service(fake)

        with patch(
            "app.services.geocode_service.httpx.get",
            side_effect=httpx.TimeoutException("timed out"),
        ):
            result = svc.reverse(12.9716, 77.5946)

        assert result["found"] is False
        # Nothing must have been written to the cache.
        assert len(fake._store) == 0, (
            "Transient HTTP errors must NOT be cached — next call should retry"
        )

    def test_connection_error_not_cached(self) -> None:
        """Connection errors (not just timeouts) must also be swallowed as
        found=False with no cache write."""
        import httpx

        fake = _FakeRedis()
        svc = _make_service(fake)

        with patch(
            "app.services.geocode_service.httpx.get",
            side_effect=httpx.ConnectError("connection refused"),
        ):
            result = svc.reverse(19.0760, 72.8777)

        assert result["found"] is False
        assert len(fake._store) == 0

    def test_http_status_error_not_cached(self) -> None:
        """An HTTP 5xx from the provider (raise_for_status raises) must not be
        cached — it is transient."""
        import httpx

        fake = _FakeRedis()
        svc = _make_service(fake)

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "503 Service Unavailable",
            request=MagicMock(),
            response=MagicMock(),
        )

        with patch("app.services.geocode_service.httpx.get", return_value=mock_resp):
            result = svc.reverse(13.0827, 80.2707)

        assert result["found"] is False
        assert len(fake._store) == 0


# ---------------------------------------------------------------------------
# 6. Endpoint-level tests via TestClient
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
        """Valid in-range coordinates with the GeocodeService patched to return
        a known result → HTTP 200 with {found, pincode, city, state} shape."""
        mocked_result = {
            "found": True,
            "pincode": "560001",
            "city": "Bengaluru",
            "state": "Karnataka",
        }

        with patch(
            "app.api.v1.endpoints.shipping.GeocodeService.reverse",
            return_value=mocked_result,
        ):
            resp = client.get("/api/v1/shipping/geocode/reverse?lat=12.972&lng=77.595")

        assert resp.status_code == 200
        data = resp.json()
        assert "found" in data
        assert "pincode" in data
        assert "city" in data
        assert "state" in data
        assert data["found"] is True
        assert data["pincode"] == "560001"
        assert data["city"] == "Bengaluru"
        assert data["state"] == "Karnataka"

    def test_valid_coordinates_found_false_returns_200(
        self, client: TestClient
    ) -> None:
        """Service returning found=False is still HTTP 200 (degraded-but-safe path)."""
        mocked_result = {
            "found": False,
            "pincode": None,
            "city": None,
            "state": None,
        }

        with patch(
            "app.api.v1.endpoints.shipping.GeocodeService.reverse",
            return_value=mocked_result,
        ):
            resp = client.get("/api/v1/shipping/geocode/reverse?lat=51.5&lng=-0.1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] is False

    def test_lat_at_boundary_minus_90_returns_200(self, client: TestClient) -> None:
        """lat=-90 is exactly on the boundary and must be accepted (not 422)."""
        mocked_result = {
            "found": False,
            "pincode": None,
            "city": None,
            "state": None,
        }

        with patch(
            "app.api.v1.endpoints.shipping.GeocodeService.reverse",
            return_value=mocked_result,
        ):
            resp = client.get("/api/v1/shipping/geocode/reverse?lat=-90&lng=0")

        assert resp.status_code == 200
