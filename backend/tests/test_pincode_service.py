"""Unit-style tests for PincodeService.lookup.

These tests are fully hermetic — no real HTTP calls to api.postalpincode.in
and no real Redis writes.  We stub at the seams PincodeService uses:
  - self._redis  — the redis.Redis instance stored on the service after __init__
  - httpx.get    — the function used in _fetch_and_cache

Stubbing approach:
  1. Instantiate PincodeService() normally (it builds a real Redis client in
     __init__; that is fine — we replace self._redis afterwards).
  2. Monkeypatch `httpx.get` in the `app.services.pincode_service` module
     namespace so calls inside the service hit our stub instead of the real
     network.

Runs inside the backend container:

    docker compose exec backend pytest tests/test_pincode_service.py -v
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.pincode_service import PincodeService, _NEGATIVE_TTL, _POSITIVE_TTL


# ---------------------------------------------------------------------------
# Redis stub helpers
# ---------------------------------------------------------------------------

class _FakeRedis:
    """Minimal in-memory Redis stub that supports get / setex used by the
    service.  Thread safety is irrelevant for single-threaded tests."""

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


def _make_service(fake_redis: _FakeRedis | None = None) -> PincodeService:
    svc = PincodeService()
    svc._redis = fake_redis or _FakeRedis()
    return svc


# ---------------------------------------------------------------------------
# HTTP response stub helpers
# ---------------------------------------------------------------------------

def _http_success(pincode: str, district: str = "Bangalore Urban", state: str = "Karnataka") -> Any:
    """Return a mock httpx.Response for a successful postal-pincode API call."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = [
        {
            "Status": "Success",
            "PostOffice": [
                {"District": district, "State": state, "Name": "Test PO"},
            ],
        }
    ]
    return mock_resp


def _http_error(status: str = "Error") -> Any:
    """Return a mock httpx.Response for a definitive API failure."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = [{"Status": status, "PostOffice": None}]
    return mock_resp


# ---------------------------------------------------------------------------
# Tests: invalid pincode (no HTTP, no Redis)
# ---------------------------------------------------------------------------

class TestPincodeServiceInvalidFormat:

    def test_leading_zero_returns_not_found_without_http(self) -> None:
        """A pin starting with 0 must return found=False immediately without
        hitting Redis or HTTP."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        with patch("app.services.pincode_service.httpx.get") as mock_http:
            result = svc.lookup("012345")

        assert result["found"] is False
        mock_http.assert_not_called()
        assert len(fake._store) == 0, "No cache write expected for invalid format"

    def test_five_digit_pin_returns_not_found_without_http(self) -> None:
        """A 5-digit pin must return found=False without any HTTP call."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        with patch("app.services.pincode_service.httpx.get") as mock_http:
            result = svc.lookup("12345")

        assert result["found"] is False
        mock_http.assert_not_called()

    def test_empty_string_returns_not_found_without_http(self) -> None:
        fake = _FakeRedis()
        svc = _make_service(fake)

        with patch("app.services.pincode_service.httpx.get") as mock_http:
            result = svc.lookup("")

        assert result["found"] is False
        mock_http.assert_not_called()

    def test_non_numeric_returns_not_found_without_http(self) -> None:
        fake = _FakeRedis()
        svc = _make_service(fake)

        with patch("app.services.pincode_service.httpx.get") as mock_http:
            result = svc.lookup("56000X")

        assert result["found"] is False
        mock_http.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: Redis cache hit
# ---------------------------------------------------------------------------

class TestPincodeServiceCacheHit:

    def test_cache_hit_returns_cached_value_without_http(self) -> None:
        """When a positive result is already in Redis, lookup must return it
        without making any HTTP call."""
        fake = _FakeRedis()
        cached_result = {
            "pincode": "560001",
            "found": True,
            "city": "Bangalore Urban",
            "state": "Karnataka",
        }
        # Pre-populate the cache with the key the service expects.
        cache_key = f"pincode:lookup:560001"
        fake.setex(cache_key, _POSITIVE_TTL, json.dumps(cached_result))

        svc = _make_service(fake)

        with patch("app.services.pincode_service.httpx.get") as mock_http:
            result = svc.lookup("560001")

        assert result == cached_result
        mock_http.assert_not_called()

    def test_negative_cache_hit_returns_not_found_without_http(self) -> None:
        """A cached negative result must be returned without HTTP."""
        fake = _FakeRedis()
        cached_result = {
            "pincode": "999999",
            "found": False,
            "city": None,
            "state": None,
        }
        fake.setex("pincode:lookup:999999", _NEGATIVE_TTL, json.dumps(cached_result))
        svc = _make_service(fake)

        with patch("app.services.pincode_service.httpx.get") as mock_http:
            result = svc.lookup("999999")

        assert result["found"] is False
        mock_http.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: cache miss → HTTP success → positive cached
# ---------------------------------------------------------------------------

class TestPincodeServiceCacheMissSuccess:

    def test_cache_miss_calls_http_and_returns_city_state(self) -> None:
        """On a cache miss, the service must call the external API and return
        found=True with city and state populated."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        with patch(
            "app.services.pincode_service.httpx.get",
            return_value=_http_success("560001", "Bangalore Urban", "Karnataka"),
        ) as mock_http:
            result = svc.lookup("560001")

        assert result["found"] is True
        assert result["city"] == "Bangalore Urban"
        assert result["state"] == "Karnataka"
        assert result["pincode"] == "560001"
        mock_http.assert_called_once()

    def test_successful_result_is_cached_positively(self) -> None:
        """After a successful HTTP response the result must be written to the
        Redis store with the positive TTL."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        with patch(
            "app.services.pincode_service.httpx.get",
            return_value=_http_success("560001", "Bangalore Urban", "Karnataka"),
        ):
            svc.lookup("560001")

        key = "pincode:lookup:560001"
        assert key in fake._store, "Positive result must be cached in Redis"
        stored = json.loads(fake._store[key])
        assert stored["found"] is True
        assert fake._ttls[key] == _POSITIVE_TTL


# ---------------------------------------------------------------------------
# Tests: API returns Error / no records → found=False, negative cached
# ---------------------------------------------------------------------------

class TestPincodeServiceApiError:

    def test_api_returns_error_status_yields_not_found_and_negative_cached(self) -> None:
        """When the API returns Status='Error', the result must be found=False
        and a negative TTL entry must be written to the cache."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        with patch(
            "app.services.pincode_service.httpx.get",
            return_value=_http_error("Error"),
        ):
            result = svc.lookup("999999")

        assert result["found"] is False

        key = "pincode:lookup:999999"
        assert key in fake._store, "Definitive API failure must be cached (negative)"
        stored = json.loads(fake._store[key])
        assert stored["found"] is False
        assert fake._ttls[key] == _NEGATIVE_TTL

    def test_api_returns_success_with_empty_post_offices_negative_cached(self) -> None:
        """Status='Success' with an empty PostOffice list is also a definitive
        not-found — must cache negative."""
        fake = _FakeRedis()
        svc = _make_service(fake)

        empty_resp = MagicMock()
        empty_resp.raise_for_status = MagicMock()
        empty_resp.json.return_value = [{"Status": "Success", "PostOffice": []}]

        with patch("app.services.pincode_service.httpx.get", return_value=empty_resp):
            result = svc.lookup("560100")

        assert result["found"] is False
        key = "pincode:lookup:560100"
        assert key in fake._store


# ---------------------------------------------------------------------------
# Tests: HTTP raises (timeout / connection error) → found=False, NOT cached
# ---------------------------------------------------------------------------

class TestPincodeServiceHttpFailure:

    def test_http_timeout_returns_not_found_and_is_not_cached(self) -> None:
        """A transient HTTP error (timeout, connection error) must return
        found=False WITHOUT writing anything to the cache so the next request
        retries the real API."""
        import httpx

        fake = _FakeRedis()
        svc = _make_service(fake)

        with patch(
            "app.services.pincode_service.httpx.get",
            side_effect=httpx.TimeoutException("timed out"),
        ):
            result = svc.lookup("560001")

        assert result["found"] is False
        key = "pincode:lookup:560001"
        assert key not in fake._store, (
            "Transient HTTP errors must NOT be cached — next call should retry"
        )

    def test_http_connection_error_returns_not_found_and_is_not_cached(self) -> None:
        """Connection-level failures must also be swallowed as found=False with
        no cache write."""
        import httpx

        fake = _FakeRedis()
        svc = _make_service(fake)

        with patch(
            "app.services.pincode_service.httpx.get",
            side_effect=httpx.ConnectError("connection refused"),
        ):
            result = svc.lookup("400001")

        assert result["found"] is False
        assert "pincode:lookup:400001" not in fake._store
