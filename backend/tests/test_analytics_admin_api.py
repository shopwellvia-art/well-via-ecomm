"""Tests for the analytics job-control API and the worker's startup guard.

Strategy
--------
**The router is not mounted on the real app yet** — wiring `api/v1/router.py` is
the lead's change, and this module deliberately does not touch it. So every HTTP
test builds a *local* `FastAPI()` in a fixture, registers the app-wide exception
handlers (so `AppError` subclasses render as their real status codes rather than
500s) and mounts `analytics_admin.router` under the prefix the lead will use.
That exercises the router exactly as it will behave once wired, and the file
needs no edit when it is.

**No shared login account.** Several older test modules authenticate as a
hardcoded `vinay@gmail.com`, which only exists on the production database. These
tests create their own users, role and permission grant, and delete them in a
`finally` via a fresh `SessionLocal` — so a half-rolled-back transaction in the
test session cannot leave orphans behind.

**A throwaway aggregation job.** The `JOBS` registry is owned elsewhere and its
contents will grow. Enqueue paths only need a *registered name*, so these tests
register a uniquely-named no-op job and pop it in teardown. That keeps the
assertions independent of which real jobs happen to exist today.

Run inside the analytics container:

    docker exec wvana-py python -m pytest tests/test_analytics_admin_api.py -q
"""
from __future__ import annotations

import inspect
import os
import secrets as _secrets
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.v1.endpoints import analytics_admin
from app.core import config as _config
from app.core.exceptions import register_exception_handlers
from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal
from app.models.analytics_control import (
    AnalyticsRecomputeQueue,
    AnalyticsSyncRun,
    RecomputeStatus,
    SyncStatus,
    SyncTrigger,
)
from app.services.analytics.aggregation.base import JOBS, JobRunResult

API_PREFIX = "/api/v1/analytics"
TEST_TOKEN = "test-analytics-cron-token-0123456789"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def app() -> FastAPI:
    """A minimal app carrying only this router.

    `register_exception_handlers` matters: without it the 400/403/404 raised as
    `AppError` subclasses would surface as unhandled 500s and every status-code
    assertion below would be meaningless.
    """
    test_app = FastAPI()
    register_exception_handlers(test_app)
    test_app.include_router(analytics_admin.router, prefix=API_PREFIX)
    return test_app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _no_rate_limit():
    """Rate limits are asserted-on nowhere here and would make repeated local
    runs flaky as the IP bucket fills, so they are off for the duration."""
    original = _config.settings.RATE_LIMIT_ENABLED
    _config.settings.RATE_LIMIT_ENABLED = False
    try:
        yield
    finally:
        _config.settings.RATE_LIMIT_ENABLED = original


@pytest.fixture()
def cron_token():
    """Configure a known `ANALYTICS_CRON_TOKEN` for the machine-auth tests."""
    original = _config.settings.ANALYTICS_CRON_TOKEN
    _config.settings.ANALYTICS_CRON_TOKEN = TEST_TOKEN
    try:
        yield TEST_TOKEN
    finally:
        _config.settings.ANALYTICS_CRON_TOKEN = original


@pytest.fixture()
def test_job():
    """Register a no-op job under a unique name; remove it afterwards.

    Its `run` is never called by these tests — the routes under test only ever
    enqueue — but it is a valid `AggregationJob` so nothing downstream is
    surprised by it.
    """
    name = f"test_job_{uuid.uuid4().hex[:8]}"

    class _NoopJob:
        def __init__(self, job_name: str) -> None:
            self.name = job_name

        def run(self, db, bucket_date, tz_generation):  # pragma: no cover
            return JobRunResult()

    JOBS[name] = _NoopJob(name)
    try:
        yield name
    finally:
        JOBS.pop(name, None)
        with SessionLocal() as s:
            s.execute(
                text("DELETE FROM analytics_recompute_queue WHERE job = :job"),
                {"job": name},
            )
            s.execute(
                text("DELETE FROM analytics_sync_runs WHERE job = :job"), {"job": name}
            )
            s.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _create_user(*, with_permission: bool) -> tuple[int, str, list[int], list[int]]:
    """Create a NON-admin user, optionally holding `analytics.jobs.run`.

    Non-admin on purpose: `User.has_permission` short-circuits True for
    `is_admin`, so an admin account would pass the auth dependency without ever
    exercising the permission grant this test is about.

    Returns (user_id, bearer_token, role_ids, permission_ids_created).
    """
    from app.models.rbac import Permission, Role
    from app.models.user import User

    role_ids: list[int] = []
    created_permission_ids: list[int] = []

    with SessionLocal() as db:
        user = User(
            email=f"analytics-jobs-{_uid()}@example.com",
            hashed_password=hash_password("TestPass123!"),
            is_active=True,
            is_admin=False,
        )
        db.add(user)
        db.flush()

        if with_permission:
            perm = (
                db.query(Permission)
                .filter(Permission.name == analytics_admin.JOBS_RUN_PERMISSION)
                .first()
            )
            if perm is None:
                # Seeded on a normal deploy; create it if this DB predates the
                # registry entry, and remember to remove only what we created.
                perm = Permission(
                    name=analytics_admin.JOBS_RUN_PERMISSION,
                    description="test-created",
                    group_name="Analytics",
                )
                db.add(perm)
                db.flush()
                created_permission_ids.append(perm.id)

            role = Role(
                name=f"analytics-jobs-test-{_uid()}",
                description="test role",
                is_system=False,
            )
            role.permissions.append(perm)
            db.add(role)
            db.flush()
            role_ids.append(role.id)
            user.roles.append(role)

        db.commit()
        user_id = user.id

    return user_id, create_access_token(user_id), role_ids, created_permission_ids


def _cleanup_user(
    user_id: int, role_ids: list[int], permission_ids: list[int]
) -> None:
    with SessionLocal() as s:
        s.execute(
            text("DELETE FROM user_roles WHERE user_id = :uid"), {"uid": user_id}
        )
        for role_id in role_ids:
            s.execute(
                text("DELETE FROM role_permissions WHERE role_id = :rid"),
                {"rid": role_id},
            )
            s.execute(text("DELETE FROM roles WHERE id = :rid"), {"rid": role_id})
        for perm_id in permission_ids:
            s.execute(text("DELETE FROM permissions WHERE id = :pid"), {"pid": perm_id})
        s.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
        s.commit()


def _machine(token: str) -> dict[str, str]:
    return {"X-Analytics-Token": token}


def _window(days: int = 3) -> dict[str, str]:
    start = date(2026, 3, 1)
    return {
        "date_from": start.isoformat(),
        "date_to": (start + timedelta(days=days)).isoformat(),
    }


#: (method, path, json-body-or-None) for every route this module exposes.
ALL_ROUTES = [
    ("POST", "/admin/recompute", {"job": "order_daily", **_window()}),
    ("POST", "/admin/backfill", {"job": "order_daily", **_window()}),
    ("POST", "/admin/aggregate", {"job": "order_daily", **_window()}),
    ("GET", "/admin/jobs/1", None),
    ("GET", "/admin/health", None),
]


def _call(client: TestClient, method: str, path: str, body, headers=None):
    url = f"{API_PREFIX}{path}"
    if method == "GET":
        return client.get(url, headers=headers or {})
    return client.post(url, json=body, headers=headers or {})


# ---------------------------------------------------------------------------
# 1. Unauthenticated
# ---------------------------------------------------------------------------
class TestAuthRequired:
    def test_every_route_refuses_anonymous_callers(
        self, client: TestClient, cron_token: str
    ) -> None:
        """No token, no session → 401/403 on all five routes.

        Asserted route-by-route rather than on one sample, because an auth
        dependency that is merely *forgotten* on one route is the realistic
        failure and a single-route test would not see it.
        """
        for method, path, body in ALL_ROUTES:
            resp = _call(client, method, path, body)
            assert resp.status_code in (401, 403), (
                f"{method} {path} allowed an anonymous caller: "
                f"{resp.status_code} {resp.text}"
            )

    def test_blank_configured_token_disables_machine_auth(
        self, client: TestClient
    ) -> None:
        """With `ANALYTICS_CRON_TOKEN` unset, an empty header must not match.

        The `configured and header and compare_digest(...)` short-circuit is the
        guard; without the first term an unset secret would be satisfied by an
        empty (or any) header on a machine that has no secret at all.
        """
        original = _config.settings.ANALYTICS_CRON_TOKEN
        _config.settings.ANALYTICS_CRON_TOKEN = ""
        try:
            resp = _call(
                client, "GET", "/admin/health", None, headers={"X-Analytics-Token": ""}
            )
            assert resp.status_code == 403, (
                f"Blank token must not authenticate, got {resp.status_code}"
            )
            resp = _call(
                client,
                "GET",
                "/admin/health",
                None,
                headers={"X-Analytics-Token": "anything"},
            )
            assert resp.status_code == 403
        finally:
            _config.settings.ANALYTICS_CRON_TOKEN = original


# ---------------------------------------------------------------------------
# 2 & 3. Machine token
# ---------------------------------------------------------------------------
class TestMachineToken:
    def test_valid_token_works_without_a_user_session(
        self, client: TestClient, cron_token: str
    ) -> None:
        """The cron path: correct header, no Authorization at all → 200."""
        resp = _call(
            client, "GET", "/admin/health", None, headers=_machine(cron_token)
        )
        assert resp.status_code == 200, resp.text
        assert "queue_depth" in resp.json()

    def test_wrong_token_is_rejected(
        self, client: TestClient, cron_token: str
    ) -> None:
        resp = _call(
            client,
            "GET",
            "/admin/health",
            None,
            headers=_machine("wrong-token-entirely"),
        )
        assert resp.status_code == 403, resp.text

    def test_same_length_wrong_token_is_rejected(
        self, client: TestClient, cron_token: str
    ) -> None:
        """A near-miss of identical length — the case a naive prefix comparison
        would still reject but a timing attack would exploit."""
        near_miss = cron_token[:-1] + ("X" if cron_token[-1] != "X" else "Y")
        assert len(near_miss) == len(cron_token)
        resp = _call(client, "GET", "/admin/health", None, headers=_machine(near_miss))
        assert resp.status_code == 403, resp.text

    def test_token_comparison_is_constant_time(
        self, client: TestClient, cron_token: str
    ) -> None:
        """`secrets.compare_digest` must do the comparison, not `==`.

        Verified two ways: the call is actually made (spy), and the source
        contains no direct equality comparison against the configured token. A
        byte-at-a-time early return leaks the secret's prefix to anyone who can
        measure latency, and this secret queues arbitrary work.
        """
        with patch.object(
            analytics_admin.secrets,
            "compare_digest",
            wraps=_secrets.compare_digest,
        ) as spy:
            resp = _call(
                client, "GET", "/admin/health", None, headers=_machine(cron_token)
            )
            assert resp.status_code == 200, resp.text
            assert spy.called, (
                "secrets.compare_digest was never called — the token is being "
                "compared some other way"
            )
            args = spy.call_args[0]
            assert cron_token in args, f"compare_digest called with {args!r}"

        source = inspect.getsource(analytics_admin.analytics_auth)
        assert "compare_digest" in source
        assert "== configured" not in source
        assert "configured ==" not in source


# ---------------------------------------------------------------------------
# 4. Permission-based (human) auth
# ---------------------------------------------------------------------------
class TestPermissionAuth:
    def test_user_with_permission_is_allowed(self, client: TestClient) -> None:
        user_id, token, role_ids, perm_ids = _create_user(with_permission=True)
        try:
            resp = _call(
                client,
                "GET",
                "/admin/health",
                None,
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200, (
                f"User holding {analytics_admin.JOBS_RUN_PERMISSION} was refused: "
                f"{resp.status_code} {resp.text}"
            )
        finally:
            _cleanup_user(user_id, role_ids, perm_ids)

    def test_user_without_permission_is_refused(self, client: TestClient) -> None:
        user_id, token, role_ids, perm_ids = _create_user(with_permission=False)
        try:
            resp = _call(
                client,
                "GET",
                "/admin/health",
                None,
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 403, (
                f"User without the permission should get 403, got {resp.status_code}"
            )
        finally:
            _cleanup_user(user_id, role_ids, perm_ids)


# ---------------------------------------------------------------------------
# 5, 6, 7. Recompute
# ---------------------------------------------------------------------------
class TestRecompute:
    def test_returns_202_and_creates_queue_rows(
        self, client: TestClient, cron_token: str, test_job: str
    ) -> None:
        """202 (queued, not computed) and the rows must actually exist.

        Asserting the row count as well as the status code is the point: a
        handler that validated and returned 202 without writing anything would
        pass a status-only test and silently drop every rebuild request.
        """
        body = {"job": test_job, **_window(days=5)}
        resp = _call(client, "POST", "/admin/recompute", body, _machine(cron_token))
        assert resp.status_code == 202, resp.text

        data = resp.json()
        assert data["queued"] == 5, data
        assert len(data["job_ids"]) == 5, data

        with SessionLocal() as db:
            rows = (
                db.query(AnalyticsRecomputeQueue)
                .filter(AnalyticsRecomputeQueue.job == test_job)
                .all()
            )
            assert len(rows) == 5, f"expected 5 queue rows, found {len(rows)}"
            assert {r.bucket_date for r in rows} == {
                date(2026, 3, 1) + timedelta(days=n) for n in range(5)
            }
            assert all(r.status == RecomputeStatus.PENDING for r in rows)
            assert {r.id for r in rows} == set(data["job_ids"])

    def test_re_enqueue_is_idempotent(
        self, client: TestClient, cron_token: str, test_job: str
    ) -> None:
        """Enqueue is an upsert on the dedup key: twice must not double rows."""
        body = {"job": test_job, **_window(days=3)}
        first = _call(client, "POST", "/admin/recompute", body, _machine(cron_token))
        second = _call(client, "POST", "/admin/recompute", body, _machine(cron_token))
        assert first.status_code == second.status_code == 202
        assert first.json()["job_ids"] == second.json()["job_ids"]

        with SessionLocal() as db:
            count = (
                db.query(AnalyticsRecomputeQueue)
                .filter(AnalyticsRecomputeQueue.job == test_job)
                .count()
            )
            assert count == 3, f"upsert duplicated rows: {count} != 3"

    def test_unknown_job_is_400(self, client: TestClient, cron_token: str) -> None:
        """A client-supplied job name must never reach the queue."""
        body = {"job": "definitely_not_a_registered_job", **_window()}
        resp = _call(client, "POST", "/admin/recompute", body, _machine(cron_token))
        assert resp.status_code == 400, resp.text
        assert "registered_jobs" in resp.json()["error"]["details"]

        with SessionLocal() as db:
            leaked = (
                db.query(AnalyticsRecomputeQueue)
                .filter(
                    AnalyticsRecomputeQueue.job == "definitely_not_a_registered_job"
                )
                .count()
            )
            assert leaked == 0, "a rejected job name still wrote queue rows"

    def test_inverted_window_is_rejected(
        self, client: TestClient, cron_token: str, test_job: str
    ) -> None:
        """`date_from >= date_to` is an empty half-open window, not a no-op."""
        for date_from, date_to in (
            ("2026-03-10", "2026-03-01"),  # inverted
            ("2026-03-01", "2026-03-01"),  # equal → covers zero days
        ):
            body = {"job": test_job, "date_from": date_from, "date_to": date_to}
            resp = _call(
                client, "POST", "/admin/recompute", body, _machine(cron_token)
            )
            assert resp.status_code in (400, 422), (
                f"{date_from}..{date_to} should be rejected, got {resp.status_code}"
            )

    def test_absurd_window_is_rejected(
        self, client: TestClient, cron_token: str, test_job: str
    ) -> None:
        """A mistyped year is the realistic way a 3,000-day window arrives."""
        body = {"job": test_job, "date_from": "2016-01-01", "date_to": "2026-03-01"}
        resp = _call(client, "POST", "/admin/recompute", body, _machine(cron_token))
        assert resp.status_code in (400, 422), resp.text


# ---------------------------------------------------------------------------
# 8. Backfill paging
# ---------------------------------------------------------------------------
class TestBackfill:
    def test_window_longer_than_max_days_pages_rather_than_truncating(
        self, client: TestClient, cron_token: str, test_job: str
    ) -> None:
        """100-day window, max_days=30 → first page of 30 and a usable cursor.

        The failure this guards against is a handler that clamps the window to
        `max_days` and returns `done: true`: the caller believes 100 days were
        covered, 70 silently were not, and the hole in the chart is
        untraceable.
        """
        start = date(2026, 1, 1)
        body = {
            "job": test_job,
            "date_from": start.isoformat(),
            "date_to": (start + timedelta(days=100)).isoformat(),
            "max_days": 30,
        }
        resp = _call(client, "POST", "/admin/backfill", body, _machine(cron_token))
        assert resp.status_code == 202, resp.text

        data = resp.json()
        assert data["queued"] == 30, data
        assert data["date_to"] == (start + timedelta(days=30)).isoformat()
        assert data["done"] is False, "a partially-enqueued window is not done"
        assert data["next_date_from"] == (start + timedelta(days=30)).isoformat(), data
        assert data["requested_date_to"] == (start + timedelta(days=100)).isoformat()

        with SessionLocal() as db:
            count = (
                db.query(AnalyticsRecomputeQueue)
                .filter(AnalyticsRecomputeQueue.job == test_job)
                .count()
            )
            assert count == 30, f"expected exactly one page enqueued, got {count}"

    def test_paging_cursor_walks_the_window_without_gaps_or_overlap(
        self, client: TestClient, cron_token: str, test_job: str
    ) -> None:
        """Chain the cursor to exhaustion — the half-open window is what makes
        consecutive pages meet exactly once at the seam."""
        start = date(2026, 1, 1)
        end = start + timedelta(days=25)
        cursor = start
        total = 0
        pages = 0
        while True:
            body = {
                "job": test_job,
                "date_from": cursor.isoformat(),
                "date_to": end.isoformat(),
                "max_days": 10,
            }
            resp = _call(client, "POST", "/admin/backfill", body, _machine(cron_token))
            assert resp.status_code == 202, resp.text
            data = resp.json()
            total += data["queued"]
            pages += 1
            if data["done"]:
                assert data["next_date_from"] is None
                break
            cursor = date.fromisoformat(data["next_date_from"])
            assert pages < 10, "cursor did not advance — infinite paging"

        assert pages == 3, f"25 days at 10/page should be 3 pages, got {pages}"
        assert total == 25, f"pages must sum to the window, got {total}"

        with SessionLocal() as db:
            rows = (
                db.query(AnalyticsRecomputeQueue)
                .filter(AnalyticsRecomputeQueue.job == test_job)
                .all()
            )
            assert len(rows) == 25, "seam double-counted or a day was skipped"
            assert {r.bucket_date for r in rows} == {
                start + timedelta(days=n) for n in range(25)
            }

    def test_max_days_above_the_cap_is_rejected_not_clamped(
        self, client: TestClient, cron_token: str, test_job: str
    ) -> None:
        body = {"job": test_job, **_window(days=10), "max_days": 90}
        resp = _call(client, "POST", "/admin/backfill", body, _machine(cron_token))
        assert resp.status_code == 422, (
            f"max_days above the 60 cap must be rejected, got {resp.status_code}"
        )

    def test_short_window_completes_in_one_page(
        self, client: TestClient, cron_token: str, test_job: str
    ) -> None:
        body = {"job": test_job, **_window(days=3), "max_days": 30}
        resp = _call(client, "POST", "/admin/backfill", body, _machine(cron_token))
        assert resp.status_code == 202, resp.text
        data = resp.json()
        assert data["done"] is True
        assert data["next_date_from"] is None
        assert data["queued"] == 3


# ---------------------------------------------------------------------------
# 9. Health
# ---------------------------------------------------------------------------
class TestHealth:
    def test_reports_queue_depth_and_watermarks(
        self, client: TestClient, cron_token: str, test_job: str
    ) -> None:
        """Seed 4 pending buckets and a successful run, then read them back."""
        watermark = date(2026, 2, 20)
        run_id: int | None = None
        with SessionLocal() as db:
            run = AnalyticsSyncRun(
                job=test_job,
                trigger=SyncTrigger.CRON,
                status=SyncStatus.SUCCESS,
                worker_id="pytest-worker",
                tz_generation=1,
                days_requested=1,
                days_processed=1,
                rows_written=7,
                rows_deleted=0,
                watermark_date=watermark,
                started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
                finished_at=datetime.now(timezone.utc),
            )
            db.add(run)
            db.commit()
            run_id = run.id

        enqueue = _call(
            client,
            "POST",
            "/admin/recompute",
            {"job": test_job, **_window(days=4)},
            _machine(cron_token),
        )
        assert enqueue.status_code == 202, enqueue.text

        resp = _call(client, "GET", "/admin/health", None, _machine(cron_token))
        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert data["queue_depth"]["pending"] >= 4
        assert data["queue_depth"]["total"] >= 4
        assert data["oldest_pending_bucket"] is not None
        assert isinstance(data["rollups_enabled"], bool)
        assert data["tz_generation"] is not None

        mine = [j for j in data["jobs"] if j["job"] == test_job]
        assert mine, f"{test_job} missing from health jobs: {[j['job'] for j in data['jobs']]}"
        entry = mine[0]
        assert entry["watermark_date"] == watermark.isoformat(), entry
        assert entry["last_run_id"] == run_id
        assert entry["last_run_status"] == SyncStatus.SUCCESS
        assert entry["consecutive_failures"] == 0
        assert entry["pending_buckets"] == 4, entry
        assert entry["oldest_pending_bucket"] == date(2026, 3, 1).isoformat()

    def test_consecutive_failures_ignore_skipped_locked_runs(
        self, client: TestClient, cron_token: str, test_job: str
    ) -> None:
        """A busy lock is not an outage and must not feed the failure streak."""
        base = datetime.now(timezone.utc) - timedelta(hours=1)
        with SessionLocal() as db:
            for offset, run_status in enumerate(
                [SyncStatus.FAILED, SyncStatus.SKIPPED_LOCKED, SyncStatus.FAILED]
            ):
                db.add(
                    AnalyticsSyncRun(
                        job=test_job,
                        trigger=SyncTrigger.CRON,
                        status=run_status,
                        tz_generation=1,
                        error="boom" if run_status == SyncStatus.FAILED else None,
                        started_at=base + timedelta(minutes=offset),
                        finished_at=base + timedelta(minutes=offset, seconds=30),
                    )
                )
            db.commit()

        resp = _call(client, "GET", "/admin/health", None, _machine(cron_token))
        assert resp.status_code == 200, resp.text
        entry = next(j for j in resp.json()["jobs"] if j["job"] == test_job)
        assert entry["consecutive_failures"] == 2, (
            "the skipped_locked run between two failures must be stepped over, "
            f"not counted or treated as a success: {entry}"
        )
        assert entry["last_error"] == "boom"


# ---------------------------------------------------------------------------
# 10. Job-run lookup
# ---------------------------------------------------------------------------
class TestJobRunLookup:
    def test_unknown_run_id_is_404(self, client: TestClient, cron_token: str) -> None:
        resp = _call(
            client, "GET", "/admin/jobs/999999999", None, _machine(cron_token)
        )
        assert resp.status_code == 404, resp.text

    def test_known_run_id_returns_the_row(
        self, client: TestClient, cron_token: str, test_job: str
    ) -> None:
        with SessionLocal() as db:
            run = AnalyticsSyncRun(
                job=test_job,
                trigger=SyncTrigger.MANUAL,
                status=SyncStatus.SUCCESS,
                worker_id="pytest-worker",
                tz_generation=1,
                days_requested=2,
                days_processed=2,
                rows_written=11,
                rows_deleted=3,
                watermark_date=date(2026, 2, 21),
                started_at=datetime.now(timezone.utc) - timedelta(minutes=2),
                finished_at=datetime.now(timezone.utc),
            )
            db.add(run)
            db.commit()
            run_id = run.id

        resp = _call(
            client, "GET", f"/admin/jobs/{run_id}", None, _machine(cron_token)
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["id"] == run_id
        assert data["job"] == test_job
        assert data["watermark_date"] == "2026-02-21"
        assert data["rows_written"] == 11
        assert data["worker_id"] == "pytest-worker"


# ---------------------------------------------------------------------------
# Aggregate: enqueue (202) vs bounded inline (200)
# ---------------------------------------------------------------------------
class TestAggregate:
    def test_default_enqueues_and_returns_202(
        self, client: TestClient, cron_token: str, test_job: str
    ) -> None:
        body = {"job": test_job, **_window(days=4)}
        resp = _call(client, "POST", "/admin/aggregate", body, _machine(cron_token))
        assert resp.status_code == 202, resp.text
        data = resp.json()
        assert data["mode"] == "enqueued"
        assert data["queued"] == 4

        with SessionLocal() as db:
            assert (
                db.query(AnalyticsRecomputeQueue)
                .filter(AnalyticsRecomputeQueue.job == test_job)
                .count()
                == 4
            )

    def test_inline_runs_now_and_returns_200(
        self, client: TestClient, cron_token: str, test_job: str
    ) -> None:
        """`?inline=true` is the only route that does work — and it says so with
        200, not 202. Nothing may be left in the queue: it ran."""
        body = {"job": test_job, **_window(days=3), "budget_ms": 20000}
        resp = client.post(
            f"{API_PREFIX}/admin/aggregate?inline=true",
            json=body,
            headers=_machine(cron_token),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["mode"] == "inline"
        assert data["days_processed"] == 3, data
        assert data["run_id"] is not None
        assert data["status"] == SyncStatus.SUCCESS, data
        assert data["watermark_date"] == "2026-03-03", data
        assert data["next_date_from"] is None, "a completed window has no cursor"
        assert data["budget_exhausted"] is False

        with SessionLocal() as db:
            assert (
                db.query(AnalyticsRecomputeQueue)
                .filter(AnalyticsRecomputeQueue.job == test_job)
                .count()
                == 0
            ), "an inline run must not also enqueue the window"
            run = db.get(AnalyticsSyncRun, data["run_id"])
            assert run is not None and run.job == test_job

    def test_inline_max_days_truncates_the_window_and_returns_a_cursor(
        self, client: TestClient, cron_token: str, test_job: str
    ) -> None:
        """A 10-day window with max_days=2 processes 2 and hands back where to
        resume — bounded, not lossy."""
        body = {"job": test_job, **_window(days=10), "max_days": 2}
        resp = client.post(
            f"{API_PREFIX}/admin/aggregate?inline=true",
            json=body,
            headers=_machine(cron_token),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["days_processed"] == 2, data
        assert data["next_date_from"] == "2026-03-03", data

    def test_inline_budget_above_the_proxy_timeout_is_rejected(
        self, client: TestClient, cron_token: str, test_job: str
    ) -> None:
        """55s cap: a run that outlives nginx's 60s proxy_read_timeout is killed
        mid-write and reports nothing."""
        body = {"job": test_job, **_window(days=2), "budget_ms": 90000}
        resp = client.post(
            f"{API_PREFIX}/admin/aggregate?inline=true",
            json=body,
            headers=_machine(cron_token),
        )
        assert resp.status_code == 422, resp.text

    def test_inline_max_days_above_the_cap_is_rejected(
        self, client: TestClient, cron_token: str, test_job: str
    ) -> None:
        body = {"job": test_job, **_window(days=30), "max_days": 30}
        resp = client.post(
            f"{API_PREFIX}/admin/aggregate?inline=true",
            json=body,
            headers=_machine(cron_token),
        )
        assert resp.status_code == 422, resp.text

    def test_unknown_job_is_400_on_the_inline_path_too(
        self, client: TestClient, cron_token: str
    ) -> None:
        body = {"job": "no_such_job", **_window(days=2)}
        resp = client.post(
            f"{API_PREFIX}/admin/aggregate?inline=true",
            json=body,
            headers=_machine(cron_token),
        )
        assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# 11. Worker startup guard
# ---------------------------------------------------------------------------
class TestWorkerStartupGuard:
    def test_refuses_to_start_when_rollups_are_disabled(self) -> None:
        """Flag off → exit 0 and never enter the loop.

        Exit **0**, not 1: under `restart: always` a non-zero exit turns a
        deliberate "this subsystem is off" into a crash loop that fills the logs
        and the restart-count metric with a non-event. And the loop must not be
        reached at all — a worker that starts and then idles still claims rows.
        """
        from app.services.analytics import worker as worker_module

        original = _config.settings.ANALYTICS_ROLLUPS_ENABLED
        _config.settings.ANALYTICS_ROLLUPS_ENABLED = False
        try:
            with patch.object(
                worker_module.AnalyticsWorker, "run_forever"
            ) as never_called:
                assert worker_module.main() == 0
                assert not never_called.called, (
                    "the worker loop started despite ANALYTICS_ROLLUPS_ENABLED "
                    "being false"
                )
        finally:
            _config.settings.ANALYTICS_ROLLUPS_ENABLED = original

    def test_unknown_role_exits_non_zero(self, monkeypatch) -> None:
        """A typo'd role IS an error — unlike the flag, nobody chose it, so a
        restart loop is the correct, visible signal."""
        from app.services.analytics import worker as worker_module

        original = _config.settings.ANALYTICS_ROLLUPS_ENABLED
        _config.settings.ANALYTICS_ROLLUPS_ENABLED = True
        monkeypatch.setenv("ANALYTICS_WORKER_ROLE", "rolup")
        try:
            with patch.object(
                worker_module.AnalyticsWorker, "run_forever"
            ) as never_called:
                assert worker_module.main() == worker_module.EXIT_BAD_CONFIG
                assert not never_called.called
        finally:
            _config.settings.ANALYTICS_ROLLUPS_ENABLED = original

    def test_worker_id_is_unique_per_process_and_fits_the_column(self) -> None:
        """`<hostname>-<pid>`: lease ownership has to point at a container log."""
        from app.services.analytics.worker import WORKER_ID_MAX_CHARS, worker_identity

        wid = worker_identity()
        assert str(os.getpid()) in wid, wid
        assert 0 < len(wid) <= WORKER_ID_MAX_CHARS

    def test_shutdown_signal_stops_the_loop_and_escalates_on_a_second(self) -> None:
        """SIGTERM sets a flag the loop already checks; a second one escalates."""
        from app.services.analytics.worker import GracefulShutdown

        shutdown = GracefulShutdown()
        assert shutdown.requested is False
        shutdown._handle(15, None)
        assert shutdown.requested is True
        assert shutdown.escalated is False
        shutdown._handle(15, None)
        assert shutdown.escalated is True
        # Interruptible: an already-requested stop must not wait out the nap.
        started = time.monotonic()
        shutdown.sleep(5)
        assert time.monotonic() - started < 1.0

    def test_deliver_pending_delivers_nothing_when_ga4_is_unconfigured(self) -> None:
        """The GA4 drain is implemented now, and still delivers nothing here.

        This test used to assert `implemented is False` — it was the stub's
        specification. The stub existed so an unimplemented drain could not
        discharge a debt it never paid: marking rows `delivered` would lose the
        events, leave the outbox looking healthy, and quietly cost GA4
        conversions with nothing recording it.

        The real drain must keep that property for a *different* reason. GA4 is
        not configured on this test database, so there are no credentials to
        send with. The correct behaviour is to report why and touch nothing —
        NOT to mark rows delivered, and NOT to raise, because "the store has not
        connected GA4 yet" is a normal operating state rather than a fault.
        """
        from app.services.analytics.worker import deliver_pending

        with SessionLocal() as db:
            result = deliver_pending(db)

        assert result["implemented"] is True, "the drain is no longer a stub"
        assert result["configured"] is False, "no GA4 credentials on a test DB"
        assert result.get("reason"), "an unconfigured drain must say why"
        # The property that actually matters, and the one the stub protected:
        # nothing is ever marked delivered without a successful send.
        assert result["delivered"] == 0
        assert result["attempted"] == 0
