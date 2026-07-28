"""Integration tests for the one-level category hierarchy (categories.parent_id).

Covers:
  - POST creates a child under a top-level parent; GET list stays FLAT and
    exposes parent_id on every row
  - POST rejects a grandchild (parent that is itself a subcategory) → 422
  - PATCH rejects making a category its own parent → 422
  - PATCH rejects giving a parent to a category that has children → 422
  - PATCH with explicit parent_id null detaches back to top level
  - PATCH omitting parent_id leaves the parent unchanged
  - DELETE of a parent that still has children → 409

Runs inside the backend container:

    docker compose exec backend pytest tests/test_categories_hierarchy.py -v

Strategy mirrors existing test modules:
  - Live MySQL via SessionLocal (no DB mocking).
  - Every category is created through the API with a unique per-call suffix
    (never clashing with real data) and torn down afterwards via SessionLocal —
    children first, then parents, so the self-referential FK never blocks — so
    a run leaves the categories table exactly as it found it.
  - HTTP tests use FastAPI TestClient for endpoint-level coverage.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.db.session import SessionLocal
from app.models.product import Category
from tests.conftest import TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


@pytest.fixture
def admin_headers(client: TestClient) -> dict[str, str]:
    return {"Authorization": f"Bearer {_get_admin_token(client)}"}


@pytest.fixture
def make_category(client: TestClient, admin_headers: dict[str, str]):
    """POST a uniquely-named category, tracking every created id for teardown."""
    created_ids: list[int] = []

    def _make(parent_id: int | None = None) -> Response:
        payload: dict = {"name": f"hier-test-{uuid.uuid4().hex[:10]}"}
        if parent_id is not None:
            payload["parent_id"] = parent_id
        resp = client.post("/api/v1/categories", json=payload, headers=admin_headers)
        if resp.status_code == 201:
            created_ids.append(resp.json()["id"])
        return resp

    yield _make

    # Restore the table to its prior state: delete everything this test made,
    # children before parents so the self-referential FK never blocks.
    with SessionLocal() as db:
        rows = [
            row
            for cid in created_ids
            if (row := db.get(Category, cid)) is not None
        ]
        for cat in (c for c in rows if c.parent_id is not None):
            db.delete(cat)
        db.flush()
        for cat in (c for c in rows if c.parent_id is None):
            db.delete(cat)
        db.commit()


# ---------------------------------------------------------------------------
# Create + flat list
# ---------------------------------------------------------------------------

class TestCreateHierarchy:

    def test_create_child_and_flat_list_exposes_parent_id(
        self, client: TestClient, make_category
    ) -> None:
        parent_resp = make_category()
        assert parent_resp.status_code == 201, parent_resp.text
        parent = parent_resp.json()
        assert parent["parent_id"] is None

        child_resp = make_category(parent_id=parent["id"])
        assert child_resp.status_code == 201, child_resp.text
        child = child_resp.json()
        assert child["parent_id"] == parent["id"]

        # GET stays a FLAT list — both rows are top-level array entries, and
        # frontends rebuild the tree by grouping on parent_id.
        r = client.get("/api/v1/categories")
        assert r.status_code == 200, r.text
        by_id = {row["id"]: row for row in r.json()}
        assert by_id[parent["id"]]["parent_id"] is None
        assert by_id[child["id"]]["parent_id"] == parent["id"]

    def test_grandchild_rejected(self, make_category) -> None:
        parent = make_category().json()
        child = make_category(parent_id=parent["id"]).json()

        r = make_category(parent_id=child["id"])
        assert r.status_code == 422, r.text
        assert "Only one level of subcategories is supported" in r.text


# ---------------------------------------------------------------------------
# PATCH — reparent, detach, self-parent
# ---------------------------------------------------------------------------

class TestPatchHierarchy:

    def test_self_parent_rejected(
        self, client: TestClient, make_category, admin_headers: dict[str, str]
    ) -> None:
        cat = make_category().json()
        r = client.patch(
            f"/api/v1/categories/{cat['id']}",
            json={"parent_id": cat["id"]},
            headers=admin_headers,
        )
        assert r.status_code == 422, r.text
        assert "cannot be its own parent" in r.text

    def test_category_with_children_cannot_get_parent(
        self, client: TestClient, make_category, admin_headers: dict[str, str]
    ) -> None:
        parent = make_category().json()
        child_resp = make_category(parent_id=parent["id"])
        assert child_resp.status_code == 201, child_resp.text
        other = make_category().json()

        r = client.patch(
            f"/api/v1/categories/{parent['id']}",
            json={"parent_id": other["id"]},
            headers=admin_headers,
        )
        assert r.status_code == 422, r.text
        assert "Only one level of subcategories is supported" in r.text

    def test_explicit_null_detaches_to_top_level(
        self, client: TestClient, make_category, admin_headers: dict[str, str]
    ) -> None:
        parent = make_category().json()
        child = make_category(parent_id=parent["id"]).json()
        assert child["parent_id"] == parent["id"]

        r = client.patch(
            f"/api/v1/categories/{child['id']}",
            json={"parent_id": None},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["parent_id"] is None

    def test_omitted_parent_id_left_unchanged(
        self, client: TestClient, make_category, admin_headers: dict[str, str]
    ) -> None:
        parent = make_category().json()
        child = make_category(parent_id=parent["id"]).json()

        r = client.patch(
            f"/api/v1/categories/{child['id']}",
            json={"name": f"hier-test-{uuid.uuid4().hex[:10]}"},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["parent_id"] == parent["id"]


# ---------------------------------------------------------------------------
# DELETE — parent with children is blocked
# ---------------------------------------------------------------------------

class TestDeleteHierarchy:

    def test_delete_parent_with_children_conflict(
        self, client: TestClient, make_category, admin_headers: dict[str, str]
    ) -> None:
        parent = make_category().json()
        child_resp = make_category(parent_id=parent["id"])
        assert child_resp.status_code == 201, child_resp.text

        r = client.delete(
            f"/api/v1/categories/{parent['id']}", headers=admin_headers
        )
        assert r.status_code == 409, r.text
        assert "Delete or move its subcategories first" in r.text

        # Once the child is gone the parent deletes cleanly.
        r2 = client.delete(
            f"/api/v1/categories/{child_resp.json()['id']}", headers=admin_headers
        )
        assert r2.status_code == 204, r2.text
        r3 = client.delete(
            f"/api/v1/categories/{parent['id']}", headers=admin_headers
        )
        assert r3.status_code == 204, r3.text
