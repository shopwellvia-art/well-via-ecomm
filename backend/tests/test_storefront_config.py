"""Integration tests for the admin-controlled storefront configuration.

Covers:
  - GET /storefront-config is public and returns the factory defaults
  - PUT roundtrip: stored values persist and the public GET reflects them
  - PUT without a token → 401
  - PUT rejects a javascript: URL in logo_url (stored-XSS guard)
  - PUT rejects a homepage_sections list missing a known section key
  - POST /storefront-config/image without a token → 401

Runs inside the backend container:

    docker compose exec backend pytest tests/test_storefront_config.py -v

Strategy mirrors existing test modules:
  - Live MySQL via SessionLocal (no DB mocking).
  - The seven storefront.* keys are snapshotted before each test, reset to ""
    (factory default) for the test body, and restored afterwards via
    SettingsService.set_many — which also invalidates the Redis cache — so
    runs never erase an admin-configured document.
  - HTTP tests use FastAPI TestClient for endpoint-level coverage.
"""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.schemas.storefront import DEFAULT_STOREFRONT
from app.services.settings_seed import seed_settings
from app.services.settings_service import SettingsService
from tests.conftest import TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD

_STOREFRONT_KEYS = [
    "storefront.site_title",
    "storefront.brand_name",
    "storefront.tagline",
    "storefront.logo_url",
    "storefront.favicon_url",
    "storefront.nav_items",
    "storefront.homepage_sections",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snapshot_storefront_keys() -> dict[str, str]:
    """Capture the current stored values of the seven storefront.* rows."""
    with SessionLocal() as db:
        # Ensure the rows exist on a fresh DB that never booted the app.
        seed_settings(db)
        svc = SettingsService(db)
        return {key: svc.get_raw(key, default="") or "" for key in _STOREFRONT_KEYS}


def _write_storefront_keys(values: dict[str, str]) -> None:
    with SessionLocal() as db:
        SettingsService(db).set_many(values)
        db.commit()


def _get_admin_token(client: TestClient) -> str:
    """Obtain a bearer token for the seeded admin account.

    Patches the rate-limit flag off for the login call so repeated test runs
    don't exhaust the Redis bucket — this is the same strategy used by the
    other HTTP-facing tests that call auth endpoints.
    """
    from app.core import config as _config
    orig = _config.settings.RATE_LIMIT_ENABLED
    _config.settings.RATE_LIMIT_ENABLED = False
    try:
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": TEST_ADMIN_EMAIL, "password": TEST_ADMIN_PASSWORD},
        )
        assert resp.status_code == 200, f"Login failed: {resp.text}"
        return resp.json()["access_token"]
    finally:
        _config.settings.RATE_LIMIT_ENABLED = orig


@pytest.fixture(autouse=True)
def _clean_storefront_settings():
    # Snapshot whatever is stored (an admin may have configured the live
    # document), run each test from factory defaults, then restore the
    # original values so a test run never erases real configuration.
    saved = _snapshot_storefront_keys()
    _write_storefront_keys({key: "" for key in _STOREFRONT_KEYS})
    yield
    _write_storefront_keys(saved)


# ---------------------------------------------------------------------------
# GET — public read
# ---------------------------------------------------------------------------

class TestGetStorefrontConfig:

    def test_get_is_public_and_returns_defaults(self, client: TestClient) -> None:
        r = client.get("/api/v1/storefront-config")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["site_title"] == DEFAULT_STOREFRONT["site_title"]
        assert body["brand_name"] == DEFAULT_STOREFRONT["brand_name"]
        assert body["tagline"] == DEFAULT_STOREFRONT["tagline"]
        assert body["logo_url"] == ""
        assert body["favicon_url"] == ""
        assert [item["label"] for item in body["nav_items"]] == [
            item["label"] for item in DEFAULT_STOREFRONT["nav_items"]
        ]
        assert [item["to"] for item in body["nav_items"]] == [
            item["to"] for item in DEFAULT_STOREFRONT["nav_items"]
        ]
        assert [s["key"] for s in body["homepage_sections"]] == [
            s["key"] for s in DEFAULT_STOREFRONT["homepage_sections"]
        ]
        assert all(s["visible"] for s in body["homepage_sections"])


# ---------------------------------------------------------------------------
# PUT — full replace + validation
# ---------------------------------------------------------------------------

class TestUpdateStorefrontConfig:

    def test_put_roundtrip_persists_and_get_reflects(self, client: TestClient) -> None:
        token = _get_admin_token(client)
        payload = copy.deepcopy(DEFAULT_STOREFRONT)
        payload["brand_name"] = "WELLVIA TEST"
        payload["tagline"] = "Custom tagline"
        payload["nav_items"] = payload["nav_items"][:3]
        payload["nav_items"][0]["label"] = "Start"
        payload["homepage_sections"][0]["visible"] = False
        payload["homepage_sections"][1]["title"] = "Custom heading"

        r = client.put(
            "/api/v1/storefront-config",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["brand_name"] == "WELLVIA TEST"
        assert [i["label"] for i in body["nav_items"]] == ["Start", "Shop", "Categories"]

        # The public GET must reflect the stored document.
        r2 = client.get("/api/v1/storefront-config")
        assert r2.status_code == 200, r2.text
        body2 = r2.json()
        assert body2["brand_name"] == "WELLVIA TEST"
        assert body2["tagline"] == "Custom tagline"
        # Unset scalars still fall back to the defaults.
        assert body2["site_title"] == DEFAULT_STOREFRONT["site_title"]
        assert len(body2["nav_items"]) == 3
        assert body2["homepage_sections"][0]["visible"] is False
        assert body2["homepage_sections"][1]["title"] == "Custom heading"

    def test_put_requires_auth(self, client: TestClient) -> None:
        r = client.put(
            "/api/v1/storefront-config", json=copy.deepcopy(DEFAULT_STOREFRONT)
        )
        assert r.status_code == 401

    def test_put_rejects_javascript_logo_url(self, client: TestClient) -> None:
        token = _get_admin_token(client)
        payload = copy.deepcopy(DEFAULT_STOREFRONT)
        payload["logo_url"] = "javascript:alert(1)"
        r = client.put(
            "/api/v1/storefront-config",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 422
        assert "Unsafe URL scheme" in r.text

    def test_put_rejects_missing_homepage_section(self, client: TestClient) -> None:
        token = _get_admin_token(client)
        payload = copy.deepcopy(DEFAULT_STOREFRONT)
        payload["homepage_sections"] = [
            s for s in payload["homepage_sections"] if s["key"] != "hero"
        ]
        r = client.put(
            "/api/v1/storefront-config",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 422
        assert "missing keys" in r.text


# ---------------------------------------------------------------------------
# POST /image — upload guard
# ---------------------------------------------------------------------------

class TestUploadStorefrontImage:

    def test_upload_rejects_unauthenticated(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/storefront-config/image",
            files={"file": ("logo.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        )
        assert r.status_code == 401
