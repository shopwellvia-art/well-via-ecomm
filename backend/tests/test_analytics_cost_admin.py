"""Tests for the cost-rule and marketing-spend admin API.

What is actually under test
---------------------------
Not "does the CRUD work". The properties that matter here are the ones whose
failure is silent:

1. **A rate change must not rewrite history.** After superseding, a report for a
   date before the new `effective_from` must still resolve the OLD rate. This is
   asserted through `CostRuleResolver` — the thing the rest of the system
   actually reads — not by inspecting the rows we just wrote.
2. **Overlapping windows are refused.** Two rules covering the same day make the
   resolved rate depend on row order, which is not reproducible and produces no
   error.
3. **A change enqueues.** A rate edit that leaves the stored rollups alone has
   changed nothing anyone can see; the queue rows are the evidence that it will
   propagate.
4. **Permission isolation is real.** `require_permission` lets `is_admin` bypass
   everything (`User.has_permission` short-circuits), so an admin account would
   pass every check below without exercising a single grant. Every user here is
   NON-ADMIN, and `TestPermissionIsolation` asserts the permission lookup itself
   before asserting a status code — a prior audit found a test that "proved"
   isolation while both users were 403 because neither held any permission at
   all, which would have passed against a completely broken RBAC layer.
5. **A monthly lump allocates exactly.** ₹40 000 over 30 days is ₹1 333.33...;
   naive rounding loses paise off the store's monthly spend permanently.

Strategy
--------
**The router is not mounted on the real app** — `ANALYTICS_V2_ENABLED` is false
by default and the mount is inside that flag block, so every HTTP test builds a
local `FastAPI()`, registers the app-wide exception handlers (without them every
`AppError` surfaces as an unhandled 500 and every status assertion is
meaningless) and mounts the router under the prefix `router.py` uses.

**No conftest DB fixture.** Each test opens its own `SessionLocal()` and cleans
up in a `finally` through a *fresh* session, so teardown cannot fail because of
a half-rolled-back transaction.

**An unused date sandbox.** Every rule, spend row and queue row this module
writes lives in 2018–2019, years before this store has any analytics history.
Teardown deletes by that date range, so a test can never delete a queue row it
did not create.

**A unique scope, not a unique cost type.** The write endpoint validates
`cost_type` against the `CostType` vocabulary — a plain varchar column would
otherwise accept `per_oder` and then never resolve — so these tests cannot
invent one. They use a real cost type with a per-test synthetic **courier**
`scope_value` instead, which is both isolated (the overlap check and the
resolver key on `(cost_type, scope, scope_value)`, and `CostScope.PRECEDENCE`
puts COURIER above GLOBAL) and realistic: a per-courier forward-shipping rate is
exactly what this table exists to express.

Run inside the analytics container:

    docker exec wvana-py python -m pytest tests/test_analytics_cost_admin.py -q
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.api.v1.endpoints import analytics_cost_admin
from app.core import config as _config
from app.core.exceptions import register_exception_handlers
from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal
from app.models.analytics_control import (
    AnalyticsCostRule,
    AnalyticsRecomputeQueue,
    CostQuality,
    CostScope,
    CostType,
    CostUnit,
    RecomputeReason,
)
from app.models.analytics_spend import (
    AnalyticsMarketingSpend,
    SpendGrain,
    SpendQuality,
    allocate_amount_across_days,
    allocate_row_to_days,
    month_bounds,
)
from app.models.audit import AuditEvent
from app.schemas.analytics_cost_admin import MAX_RECOMPUTE_DAYS
from app.services.analytics.cost_rules import CostRuleResolver
from app.services.analytics.types import MetricQuality

API_PREFIX = "/api/v1/analytics"

READ_PERM = analytics_cost_admin.READ_PERMISSION
WRITE_PERM = analytics_cost_admin.WRITE_PERMISSION
#: A scoped analytics permission that is real, seeded, and NOT finance. Held by
#: the user that must be 403 — so the 403 proves tiering, not an empty role.
OTHER_ANALYTICS_PERM = "analytics.orders.view"

# --------------------------------------------------------------------------
# The date sandbox. Far enough back that no real bucket, rule or spend row can
# collide with it, so teardown by date range is safe on a shared database.
# --------------------------------------------------------------------------
SANDBOX_FROM = date(2018, 1, 1)
SANDBOX_TO = date(2019, 12, 31)
D_START = date(2018, 3, 1)
D_MID = date(2018, 3, 11)
D_END = date(2018, 3, 31)


# ===========================================================================
# App / client
# ===========================================================================
@pytest.fixture()
def app() -> FastAPI:
    test_app = FastAPI()
    register_exception_handlers(test_app)
    test_app.include_router(analytics_cost_admin.router, prefix=API_PREFIX)
    return test_app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _no_rate_limit():
    """Off for the duration.

    Nothing here asserts on rate limiting, and the write limit is 30 per five
    minutes per IP — every test in this module shares one IP, so a filling
    bucket would make the suite fail on its second consecutive run and look like
    a flaky endpoint rather than a saturated limiter.
    """
    original = _config.settings.RATE_LIMIT_ENABLED
    _config.settings.RATE_LIMIT_ENABLED = False
    try:
        yield
    finally:
        _config.settings.RATE_LIMIT_ENABLED = original


class _FakeRedis:
    """In-process stand-in with per-instance state.

    A shared Redis is the whole reason this exists: `CostRuleResolver` caches
    resolutions for 60 seconds, so a test that resolved a rate before a
    supersession would serve the pre-edit answer to the test that resolves it
    after — and the assertion "the old rate still resolves for old dates" would
    pass for the wrong reason.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key: str):
        return self.store.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.store[key] = value

    def mget(self, keys):
        return [self.store.get(k) for k in keys]

    def delete(self, key: str) -> None:
        self.store.pop(key, None)

    def pipeline(self):
        return _FakePipeline(self)

    def scan_iter(self, match: str = "*", count: int = 100):
        prefix = match.rstrip("*")
        return [k for k in list(self.store) if k.startswith(prefix)]


class _FakePipeline:
    def __init__(self, parent: _FakeRedis) -> None:
        self.parent = parent
        self.ops: list[tuple[str, int, str]] = []

    def setex(self, key: str, ttl: int, value: str):
        self.ops.append((key, ttl, value))
        return self

    def execute(self):
        for key, ttl, value in self.ops:
            self.parent.setex(key, ttl, value)
        self.ops.clear()


def _resolver(db) -> CostRuleResolver:
    """A resolver with a private cache, so one test can never answer another."""
    return CostRuleResolver(db, redis_client=_FakeRedis())


# ===========================================================================
# Users — non-admin, with explicitly seeded grants
# ===========================================================================
def _uid() -> str:
    return uuid.uuid4().hex[:8]


class PermissionedUser:
    """A throwaway NON-ADMIN user holding exactly the named permissions.

    Not named `TestUser*`: pytest collects classes matching that prefix and
    warns about the constructor.

    Non-admin is load-bearing, not incidental. `User.has_permission` returns True
    for every permission when `is_admin` is set, so an admin account would
    satisfy `require_permission` without any of the grants below being written,
    read, or correct.
    """

    def __init__(self, *permissions: str) -> None:
        from app.models.rbac import Permission, Role
        from app.models.user import User

        self.role_ids: list[int] = []
        self.created_permission_ids: list[int] = []
        self.permissions = list(permissions)

        with SessionLocal() as db:
            user = User(
                email=f"cost-admin-{_uid()}@example.com",
                hashed_password=hash_password("TestPass123!"),
                is_active=True,
                is_admin=False,
            )
            db.add(user)
            db.flush()

            if permissions:
                role = Role(
                    name=f"cost-admin-test-{_uid()}",
                    description="test role",
                    is_system=False,
                )
                # Added and flushed BEFORE the association is built: appending to
                # `role.permissions` while `role` is still transient makes
                # SQLAlchemy skip the backref cascade and silently write no
                # `role_permissions` rows — which is exactly the failure that
                # makes a permission test pass for the wrong reason.
                db.add(role)
                db.flush()
                for name in permissions:
                    perm = db.query(Permission).filter(Permission.name == name).first()
                    if perm is None:
                        perm = Permission(
                            name=name,
                            description="test-created",
                            group_name="Analytics",
                        )
                        db.add(perm)
                        db.flush()
                        self.created_permission_ids.append(perm.id)
                    role.permissions.append(perm)
                db.flush()
                self.role_ids.append(role.id)
                user.roles.append(role)

            db.commit()
            self.id = user.id
            self.email = user.email

        self.token = create_access_token(self.id)

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def reload_permissions(self) -> dict[str, bool]:
        """What the RBAC layer ACTUALLY believes about this user, freshly read.

        The point of the whole module: assert the lookup, not only the HTTP
        status. A 403 is equally consistent with "correctly denied" and "the
        grants were never written", and only this distinguishes them.
        """
        from app.models.user import User

        with SessionLocal() as db:
            user = db.get(User, self.id)
            assert user is not None
            return {
                name: user.has_permission(name)
                for name in (READ_PERM, WRITE_PERM, OTHER_ANALYTICS_PERM)
            }

    def cleanup(self) -> None:
        with SessionLocal() as s:
            s.execute(
                text("DELETE FROM audit_events WHERE actor_user_id = :uid"),
                {"uid": self.id},
            )
            s.execute(
                text("DELETE FROM user_roles WHERE user_id = :uid"), {"uid": self.id}
            )
            for role_id in self.role_ids:
                s.execute(
                    text("DELETE FROM role_permissions WHERE role_id = :rid"),
                    {"rid": role_id},
                )
                s.execute(text("DELETE FROM roles WHERE id = :rid"), {"rid": role_id})
            for perm_id in self.created_permission_ids:
                s.execute(
                    text("DELETE FROM permissions WHERE id = :pid"), {"pid": perm_id}
                )
            s.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": self.id})
            s.commit()


@pytest.fixture()
def make_user():
    created: list[PermissionedUser] = []

    def _make(*permissions: str) -> PermissionedUser:
        user = PermissionedUser(*permissions)
        created.append(user)
        return user

    try:
        yield _make
    finally:
        for user in created:
            user.cleanup()


@pytest.fixture()
def finance_user(make_user) -> PermissionedUser:
    """Can read AND write. Both grants, because the write routes do not imply
    the read one and a fixture that only worked for half the suite would push
    every test into re-deriving which permissions it needs."""
    return make_user(READ_PERM, WRITE_PERM)


# ===========================================================================
# Sandbox teardown
# ===========================================================================
#: A real cost type, because the endpoint validates the vocabulary. Courier
#: scoped, so each test gets its own isolated rate line under it.
SANDBOX_COST_TYPE = CostType.FORWARD_SHIPPING


@pytest.fixture()
def sandbox():
    """Delete everything this module could have written.

    Yields a unique courier `scope_value` per test, so a resolution can never
    see another test's rule (or a real global one — `CostScope.PRECEDENCE` puts
    COURIER first). Teardown runs through a fresh session and only ever touches
    that scope value and the 2018–2019 date sandbox, so it cannot remove a queue
    row, rule or spend figure that belongs to the store.
    """
    scope_value = f"testcour{_uid()}"
    channel = f"test_ch_{_uid()}"[:48]
    try:
        yield {
            "cost_type": SANDBOX_COST_TYPE,
            "scope": CostScope.COURIER,
            "scope_value": scope_value,
            "candidates": {"courier": scope_value},
            "channel": channel,
        }
    finally:
        with SessionLocal() as s:
            s.execute(
                text("DELETE FROM analytics_cost_rules WHERE scope_value = :sv"),
                {"sv": scope_value},
            )
            s.execute(
                text(
                    "DELETE FROM analytics_marketing_spend "
                    "WHERE period_start BETWEEN :a AND :b"
                ),
                {"a": SANDBOX_FROM, "b": SANDBOX_TO},
            )
            # The date filter alone does NOT clean this table, and assuming it did
            # left ~1,600 rows behind on every run of this suite.
            #
            # A cost-rule change deliberately enqueues a recompute of every
            # affected bucket, and that window is capped at 400 days ending
            # TODAY — it is not the rule's own effective range. So a rule seeded
            # in this suite's 2018/2019 sandbox enqueues buckets across roughly
            # 2025-07..2026-07, every one of them outside SANDBOX_FROM..SANDBOX_TO.
            # Those rows survived, accumulated across runs, and made
            # `drain_queue(limit=50)` in test_analytics_aggregation.py claim them
            # instead of its own bucket — failing in a different file with a
            # watermark from a year this suite never mentions. Two agents
            # independently misdiagnosed it as their own concurrency problem.
            #
            # Date sandboxing cannot isolate this table: `drain_queue` claims by
            # (priority, bucket_date) across ALL rows. Deleting by the reason this
            # suite is the only writer of is what actually scopes it.
            s.execute(
                text(
                    "DELETE FROM analytics_recompute_queue "
                    "WHERE bucket_date BETWEEN :a AND :b "
                    "   OR reason = 'cost_rule_change'"
                ),
                {"a": SANDBOX_FROM, "b": SANDBOX_TO},
            )
            s.execute(
                text(
                    "DELETE FROM audit_events WHERE target_type IN "
                    "('analytics_cost_rule', 'analytics_marketing_spend')"
                    " AND created_at > NOW() - INTERVAL 1 HOUR"
                )
            )
            s.commit()


def _rule_payload(sandbox: dict, **overrides) -> dict:
    body = {
        "cost_type": sandbox["cost_type"],
        "scope": sandbox["scope"],
        "scope_value": sandbox["scope_value"],
        "value": "8.0000",
        "unit": CostUnit.PER_ORDER,
        "quality": CostQuality.ASSUMED,
        "currency": "INR",
        "effective_from": D_START.isoformat(),
        "effective_to": D_END.isoformat(),
        "source": "test",
        "note": "seeded by test",
    }
    body.update(overrides)
    return body


# ===========================================================================
# 1. Create, supersede, list — and the old rate survives
# ===========================================================================
class TestSupersessionKeepsHistory:
    def test_create_then_list(self, client, finance_user, sandbox):
        r = client.post(
            f"{API_PREFIX}/admin/cost-rules",
            json=_rule_payload(sandbox),
            headers=finance_user.headers,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["rule"]["cost_type"] == sandbox["cost_type"]
        assert body["rule"]["scope_value"] == sandbox["scope_value"]
        assert Decimal(body["rule"]["value"]) == Decimal("8")
        assert body["superseded_rule"] is None

        listed = client.get(
            f"{API_PREFIX}/admin/cost-rules?cost_type={sandbox['cost_type']}"
            f"&scope={sandbox['scope']}",
            headers=finance_user.headers,
        )
        assert listed.status_code == 200, listed.text
        payload = listed.json()
        assert payload["total"] == 1
        # The form vocabularies ride along so the UI never hardcodes them —
        # `unit` in particular is a 100x error (2.5% vs ₹2.50) if guessed.
        assert CostUnit.PER_ORDER in payload["units"]
        assert CostQuality.ACTUAL in payload["qualities"]

    def test_superseded_rule_keeps_its_value_and_closes_its_window(
        self, client, finance_user, sandbox
    ):
        created = client.post(
            f"{API_PREFIX}/admin/cost-rules",
            json=_rule_payload(sandbox),
            headers=finance_user.headers,
        ).json()
        rule_id = created["rule"]["id"]

        r = client.post(
            f"{API_PREFIX}/admin/cost-rules/{rule_id}/supersede",
            json={
                "value": "12.0000",
                "unit": CostUnit.PER_ORDER,
                "quality": CostQuality.CONTRACTED,
                "effective_from": D_MID.isoformat(),
            },
            headers=finance_user.headers,
        )
        assert r.status_code == 201, r.text
        body = r.json()

        old = body["superseded_rule"]
        assert old["id"] == rule_id
        # Closed, NOT edited. The old row's value is the property that makes
        # last quarter's margin reproducible.
        assert Decimal(old["value"]) == Decimal("8")
        assert old["effective_to"] == (D_MID - timedelta(days=1)).isoformat()
        assert Decimal(body["rule"]["value"]) == Decimal("12")
        assert body["rule"]["effective_from"] == D_MID.isoformat()
        # The new rule inherits the closed window rather than silently reopening
        # a bounded rate forever.
        assert body["rule"]["effective_to"] == D_END.isoformat()

    def test_a_report_before_the_change_still_resolves_the_old_rate(
        self, client, finance_user, sandbox
    ):
        """The whole point of the module, asserted through the resolver the rest
        of the system actually reads — not by re-reading the rows we wrote."""
        created = client.post(
            f"{API_PREFIX}/admin/cost-rules",
            json=_rule_payload(sandbox),
            headers=finance_user.headers,
        ).json()
        client.post(
            f"{API_PREFIX}/admin/cost-rules/{created['rule']['id']}/supersede",
            json={
                "value": "12.0000",
                "unit": CostUnit.PER_ORDER,
                "quality": CostQuality.CONTRACTED,
                "effective_from": D_MID.isoformat(),
            },
            headers=finance_user.headers,
        )

        with SessionLocal() as db:
            resolver = _resolver(db)
            # `scope_candidates` is what the bucket IS. The courier scope is
            # unique per test, and CostScope.PRECEDENCE puts COURIER above
            # GLOBAL, so this can only ever see this test's own rules.
            candidates = sandbox["candidates"]
            before = resolver.resolve(
                sandbox["cost_type"], D_MID - timedelta(days=1),
                scope_candidates=candidates,
            )
            on_change = resolver.resolve(
                sandbox["cost_type"], D_MID, scope_candidates=candidates
            )
            after = resolver.resolve(
                sandbox["cost_type"], D_END, scope_candidates=candidates
            )
            outside = resolver.resolve(
                sandbox["cost_type"], D_END + timedelta(days=1),
                scope_candidates=candidates,
            )

        # ₹8.00 -> 800 paise; ₹12.00 -> 1200.
        assert before.value_minor == 800, "the day before the change must be the OLD rate"
        assert on_change.value_minor == 1200
        assert after.value_minor == 1200
        # Past the window there is no rule, and MISSING is never 0.
        assert outside.is_missing
        assert outside.quality == MetricQuality.INCOMPLETE

    def test_superseding_on_the_original_start_date_is_rejected(
        self, client, finance_user, sandbox
    ):
        """Same-day supersession would leave the old rule covering no days —
        an in-place edit of history wearing a version's clothes."""
        created = client.post(
            f"{API_PREFIX}/admin/cost-rules",
            json=_rule_payload(sandbox),
            headers=finance_user.headers,
        ).json()
        r = client.post(
            f"{API_PREFIX}/admin/cost-rules/{created['rule']['id']}/supersede",
            json={
                "value": "12.0000",
                "unit": CostUnit.PER_ORDER,
                "quality": CostQuality.ASSUMED,
                "effective_from": D_START.isoformat(),
            },
            headers=finance_user.headers,
        )
        assert r.status_code == 422, r.text
        assert "must be after" in r.json()["error"]["message"]

    def test_superseding_an_unknown_rule_is_404(self, client, finance_user, sandbox):
        r = client.post(
            f"{API_PREFIX}/admin/cost-rules/999999999/supersede",
            json={
                "value": "1.0000",
                "unit": CostUnit.PER_ORDER,
                "quality": CostQuality.ASSUMED,
                "effective_from": D_MID.isoformat(),
            },
            headers=finance_user.headers,
        )
        assert r.status_code == 404


# ===========================================================================
# 2. Overlapping ranges are refused, not silently resolved
# ===========================================================================
class TestOverlapRejection:
    def test_overlapping_window_is_409_and_names_the_conflict(
        self, client, finance_user, sandbox
    ):
        first = client.post(
            f"{API_PREFIX}/admin/cost-rules",
            json=_rule_payload(sandbox),
            headers=finance_user.headers,
        ).json()

        r = client.post(
            f"{API_PREFIX}/admin/cost-rules",
            json=_rule_payload(
                sandbox,
                value="9.0000",
                effective_from=D_MID.isoformat(),
                effective_to=(D_END + timedelta(days=10)).isoformat(),
            ),
            headers=finance_user.headers,
        )
        assert r.status_code == 409, r.text
        error = r.json()["error"]
        assert error["code"] == "conflict"
        # Coordinates, not just "overlapping cost rule": the rule to supersede
        # may be one of dozens on the screen.
        assert error["details"]["conflicting_rule_id"] == first["rule"]["id"]

        with SessionLocal() as db:
            rows = (
                db.query(AnalyticsCostRule)
                .filter(AnalyticsCostRule.scope_value == sandbox["scope_value"])
                .count()
            )
        assert rows == 1, "the rejected insert must not have landed"

    def test_open_ended_rule_blocks_any_later_window(
        self, client, finance_user, sandbox
    ):
        """A NULL `effective_to` is +infinity. An insert starting after it still
        overlaps, and reading NULL as 'no upper bound to compare' is the exact
        way this check fails open."""
        client.post(
            f"{API_PREFIX}/admin/cost-rules",
            json=_rule_payload(sandbox, effective_to=None),
            headers=finance_user.headers,
        )
        r = client.post(
            f"{API_PREFIX}/admin/cost-rules",
            json=_rule_payload(
                sandbox,
                value="9.0000",
                effective_from=(D_END + timedelta(days=100)).isoformat(),
                effective_to=None,
            ),
            headers=finance_user.headers,
        )
        assert r.status_code == 409, r.text

    def test_adjacent_non_overlapping_windows_are_allowed(
        self, client, finance_user, sandbox
    ):
        """Back-to-back windows that touch but do not intersect are the normal
        shape of a rate history and must not be rejected."""
        client.post(
            f"{API_PREFIX}/admin/cost-rules",
            json=_rule_payload(sandbox),
            headers=finance_user.headers,
        )
        r = client.post(
            f"{API_PREFIX}/admin/cost-rules",
            json=_rule_payload(
                sandbox,
                value="9.0000",
                effective_from=(D_END + timedelta(days=1)).isoformat(),
                effective_to=(D_END + timedelta(days=30)).isoformat(),
            ),
            headers=finance_user.headers,
        )
        assert r.status_code == 201, r.text

    def test_inverted_window_is_422(self, client, finance_user, sandbox):
        r = client.post(
            f"{API_PREFIX}/admin/cost-rules",
            json=_rule_payload(
                sandbox,
                effective_from=D_END.isoformat(),
                effective_to=D_START.isoformat(),
            ),
            headers=finance_user.headers,
        )
        assert r.status_code == 422

    def test_unknown_unit_is_422_with_the_valid_set(
        self, client, finance_user, sandbox
    ):
        """`unit` is a plain varchar so a new cost line needs no migration; the
        cost of that is that `per_oder` inserts happily and then never resolves,
        which presents as 'the margin is still INCOMPLETE' with no error."""
        r = client.post(
            f"{API_PREFIX}/admin/cost-rules",
            json=_rule_payload(sandbox, unit="per_oder"),
            headers=finance_user.headers,
        )
        assert r.status_code == 422, r.text
        assert CostUnit.PER_ORDER in r.json()["error"]["details"]["allowed"]


# ===========================================================================
# 3. A rate change enqueues the affected buckets
# ===========================================================================
def _queue_rows(date_from: date, date_to: date) -> list[AnalyticsRecomputeQueue]:
    with SessionLocal() as db:
        return list(
            db.query(AnalyticsRecomputeQueue)
            .filter(
                AnalyticsRecomputeQueue.bucket_date >= date_from,
                AnalyticsRecomputeQueue.bucket_date <= date_to,
            )
            .all()
        )


class TestRecomputeIsEnqueued:
    def test_create_enqueues_every_covered_bucket(
        self, client, finance_user, sandbox
    ):
        r = client.post(
            f"{API_PREFIX}/admin/cost-rules",
            json=_rule_payload(sandbox),
            headers=finance_user.headers,
        )
        assert r.status_code == 201, r.text
        body = r.json()

        jobs = body["recompute_jobs"]
        assert jobs, "no cost-consuming job was registered — nothing would rebuild"
        days = (D_END - D_START).days + 1
        assert body["recompute_queued"] == len(jobs) * days
        assert body["recompute_from"] == D_START.isoformat()
        assert body["recompute_to"] == D_END.isoformat()

        rows = _queue_rows(D_START, D_END)
        assert len(rows) == len(jobs) * days
        assert {row.reason for row in rows} == {RecomputeReason.COST_RULE_CHANGE}
        assert {row.job for row in rows} == set(jobs)
        assert {row.bucket_date for row in rows} == {
            D_START + timedelta(days=i) for i in range(days)
        }

    def test_supersede_enqueues_only_from_the_change_date(
        self, client, finance_user, sandbox
    ):
        created = client.post(
            f"{API_PREFIX}/admin/cost-rules",
            json=_rule_payload(sandbox),
            headers=finance_user.headers,
        ).json()

        # Clear the create's rows so the supersede's enqueue is measured alone.
        #
        # This must match on `reason`, not on the sandbox dates. The enqueued
        # buckets are a 400-day window ending TODAY, not the rule's effective
        # range, so a date-scoped delete here removed NOTHING and this test was
        # silently measuring create + supersede together while claiming to
        # measure supersede alone. See the teardown for the full explanation.
        with SessionLocal() as s:
            s.execute(
                text(
                    "DELETE FROM analytics_recompute_queue "
                    "WHERE bucket_date BETWEEN :a AND :b "
                    "   OR reason = 'cost_rule_change'"
                ),
                {"a": SANDBOX_FROM, "b": SANDBOX_TO},
            )
            s.commit()

        r = client.post(
            f"{API_PREFIX}/admin/cost-rules/{created['rule']['id']}/supersede",
            json={
                "value": "12.0000",
                "unit": CostUnit.PER_ORDER,
                "quality": CostQuality.ACTUAL,
                "effective_from": D_MID.isoformat(),
            },
            headers=finance_user.headers,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["recompute_from"] == D_MID.isoformat()

        rows = _queue_rows(SANDBOX_FROM, SANDBOX_TO)
        assert rows, "a rate change that enqueues nothing has changed nothing visible"
        # Buckets before the change date resolve the OLD rule and are still
        # correct — rebuilding them would be pure waste.
        assert min(row.bucket_date for row in rows) == D_MID
        assert max(row.bucket_date for row in rows) == D_END

    def test_a_window_wider_than_the_cap_warns_instead_of_silently_truncating(
        self, client, finance_user, sandbox
    ):
        """Partial-and-loud beats partial-and-silent: a hole in a chart nobody
        can trace back to the request that made it is the worse outcome."""
        wide_to = date(2019, 6, 30)
        wide_from = wide_to - timedelta(days=MAX_RECOMPUTE_DAYS + 50)
        r = client.post(
            f"{API_PREFIX}/admin/cost-rules",
            json=_rule_payload(
                sandbox,
                effective_from=wide_from.isoformat(),
                effective_to=wide_to.isoformat(),
            ),
            headers=finance_user.headers,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["warnings"], "the untouched buckets must be reported"
        assert "backfill" in body["warnings"][0]
        assert body["recompute_from"] == (
            wide_to - timedelta(days=MAX_RECOMPUTE_DAYS - 1)
        ).isoformat()
        assert body["recompute_to"] == wide_to.isoformat()


# ===========================================================================
# 4. Permission isolation — the lookup, then the status code
# ===========================================================================
class TestPermissionIsolation:
    def test_rbac_seeding_actually_granted_what_it_claims(self, make_user):
        """Asserted FIRST, and separately.

        A prior audit found a permission test that "proved" isolation while both
        users were 403 because neither held any grant at all — it would have
        passed against an RBAC layer that never wrote a row. These assertions
        fail loudly in that world.
        """
        scoped = make_user(OTHER_ANALYTICS_PERM)
        finance = make_user(READ_PERM)

        scoped_perms = scoped.reload_permissions()
        assert scoped_perms[OTHER_ANALYTICS_PERM] is True, (
            "the grant was never written — every 403 below would be vacuous"
        )
        assert scoped_perms[READ_PERM] is False
        assert scoped_perms[WRITE_PERM] is False

        finance_perms = finance.reload_permissions()
        assert finance_perms[READ_PERM] is True
        assert finance_perms[WRITE_PERM] is False

    def test_scoped_non_finance_role_is_403_on_read(
        self, client, make_user, sandbox
    ):
        scoped = make_user(OTHER_ANALYTICS_PERM)
        assert scoped.reload_permissions()[OTHER_ANALYTICS_PERM] is True

        for url in ("/admin/cost-rules", "/admin/marketing-spend"):
            r = client.get(f"{API_PREFIX}{url}", headers=scoped.headers)
            assert r.status_code == 403, (url, r.text)
            assert READ_PERM in r.json()["error"]["message"]

    def test_scoped_non_finance_role_is_403_on_write(
        self, client, make_user, sandbox
    ):
        scoped = make_user(OTHER_ANALYTICS_PERM)
        assert scoped.reload_permissions()[OTHER_ANALYTICS_PERM] is True

        r = client.post(
            f"{API_PREFIX}/admin/cost-rules",
            json=_rule_payload(sandbox),
            headers=scoped.headers,
        )
        assert r.status_code == 403, r.text
        assert WRITE_PERM in r.json()["error"]["message"]

        r = client.post(
            f"{API_PREFIX}/admin/marketing-spend",
            json={
                "grain": SpendGrain.MONTHLY,
                "period": D_START.isoformat(),
                "channel": sandbox["channel"],
                "amount": "1000.00",
            },
            headers=scoped.headers,
        )
        assert r.status_code == 403, r.text

        with SessionLocal() as db:
            assert (
                db.query(AnalyticsCostRule)
                .filter(AnalyticsCostRule.scope_value == sandbox["scope_value"])
                .count()
                == 0
            )

    def test_read_permission_alone_cannot_write(self, client, make_user, sandbox):
        """Reading the store's margin structure and changing it are different
        privileges; `analytics.finance.view` must not imply the manage grant."""
        reader = make_user(READ_PERM)
        assert reader.reload_permissions()[READ_PERM] is True
        assert reader.reload_permissions()[WRITE_PERM] is False

        assert (
            client.get(
                f"{API_PREFIX}/admin/cost-rules", headers=reader.headers
            ).status_code
            == 200
        )
        r = client.post(
            f"{API_PREFIX}/admin/cost-rules",
            json=_rule_payload(sandbox),
            headers=reader.headers,
        )
        assert r.status_code == 403, r.text

    def test_anonymous_is_401(self, client, sandbox):
        r = client.get(f"{API_PREFIX}/admin/cost-rules")
        assert r.status_code == 401


# ===========================================================================
# 5. Audit events carry old and new
# ===========================================================================
def _audit(action: str, target_id: int) -> AuditEvent | None:
    with SessionLocal() as db:
        return (
            db.query(AuditEvent)
            .filter(AuditEvent.action == action, AuditEvent.target_id == target_id)
            .order_by(AuditEvent.id.desc())
            .first()
        )


class TestAuditTrail:
    def test_create_writes_an_audit_event(self, client, finance_user, sandbox):
        body = client.post(
            f"{API_PREFIX}/admin/cost-rules",
            json=_rule_payload(sandbox),
            headers=finance_user.headers,
        ).json()

        event = _audit("analytics.cost_rule.create", body["rule"]["id"])
        assert event is not None
        assert event.actor_user_id == finance_user.id
        assert event.actor_email == finance_user.email
        assert event.extra["old"] is None
        assert event.extra["new"]["value"] == "8.0000"
        assert event.extra["recompute"]["queued"] > 0

    def test_supersede_records_both_values(self, client, finance_user, sandbox):
        created = client.post(
            f"{API_PREFIX}/admin/cost-rules",
            json=_rule_payload(sandbox),
            headers=finance_user.headers,
        ).json()
        superseded = client.post(
            f"{API_PREFIX}/admin/cost-rules/{created['rule']['id']}/supersede",
            json={
                "value": "12.0000",
                "unit": CostUnit.PER_ORDER,
                "quality": CostQuality.ACTUAL,
                "effective_from": D_MID.isoformat(),
            },
            headers=finance_user.headers,
        ).json()

        event = _audit("analytics.cost_rule.supersede", superseded["rule"]["id"])
        assert event is not None
        # Both sides, as strings. `float(Decimal("2.3625"))` is the start of the
        # exact class of error this subsystem exists to avoid.
        assert event.extra["old"]["value"] == "8.0000"
        assert event.extra["new"]["value"] == "12.0000"
        assert event.extra["superseded_rule_id"] == created["rule"]["id"]
        assert event.extra["superseded_effective_to"] == (
            D_MID - timedelta(days=1)
        ).isoformat()

    def test_spend_revision_records_the_previous_amount(
        self, client, finance_user, sandbox
    ):
        payload = {
            "grain": SpendGrain.MONTHLY,
            "period": D_START.isoformat(),
            "channel": sandbox["channel"],
            "amount": "38000.00",
            "quality": SpendQuality.ASSUMED,
        }
        first = client.post(
            f"{API_PREFIX}/admin/marketing-spend",
            json=payload,
            headers=finance_user.headers,
        )
        assert first.status_code == 201, first.text

        second = client.post(
            f"{API_PREFIX}/admin/marketing-spend",
            json={**payload, "amount": "40000.00", "quality": SpendQuality.ACTUAL},
            headers=finance_user.headers,
        )
        # 200, not 201: an existing period was replaced, and conflating the two
        # would leave the caller unable to tell which happened.
        assert second.status_code == 200, second.text
        body = second.json()
        assert body["created"] is False
        assert Decimal(body["previous_amount"]) == Decimal("38000")

        event = _audit("analytics.marketing_spend.update", body["spend"]["id"])
        assert event is not None
        assert event.extra["old"]["amount"] == "38000.00"
        assert event.extra["new"]["amount"] == "40000.00"


# ===========================================================================
# 6. Monthly spend allocates exactly, and says it was allocated
# ===========================================================================
class TestMonthlyAllocation:
    @pytest.mark.parametrize(
        "amount,days",
        [
            ("40000.00", 30),
            ("40000.00", 31),
            ("100.00", 28),
            ("0.01", 31),
            ("12345.67", 30),
            ("1.00", 1),
        ],
    )
    def test_allocation_sums_back_to_the_total_to_the_paisa(self, amount, days):
        """₹40 000 over 30 days is ₹1 333.333...; rounding each day to ₹1 333.33
        and summing loses 10 paise off the month, permanently and invisibly."""
        start = date(2018, 6, 1)
        shares = allocate_amount_across_days(
            Decimal(amount), start, start + timedelta(days=days - 1)
        )
        assert len(shares) == days
        assert sum(s.amount for s in shares) == Decimal(amount)
        # Every share is a real money amount, not a fraction of a paisa.
        assert all(s.amount == s.amount.quantize(Decimal("0.01")) for s in shares)
        # Deterministic: the remainder always goes to the earliest days, so two
        # recomputes of the same month can never produce different daily charts.
        assert shares == allocate_amount_across_days(
            Decimal(amount), start, start + timedelta(days=days - 1)
        )

    def test_daily_row_is_not_marked_allocated(self):
        row = AnalyticsMarketingSpend(
            grain=SpendGrain.DAILY,
            period_start=D_START,
            period_end=D_START,
            channel="meta",
            campaign="-",
            amount=Decimal("500.00"),
        )
        shares = allocate_row_to_days(row)
        assert shares == [
            shares[0].__class__(day=D_START, amount=Decimal("500.00"), allocated=False)
        ]

    def test_month_bounds_normalises_any_day_to_the_whole_month(self):
        """An admin who types 'June' and picks the 12th means June. Storing
        12 June -> 12 June as a MONTHLY row would put a month's spend on one day
        for the rest of the system's life."""
        assert month_bounds(date(2018, 6, 12)) == (date(2018, 6, 1), date(2018, 6, 30))
        assert month_bounds(date(2016, 2, 5)) == (date(2016, 2, 1), date(2016, 2, 29))

    def test_api_spreads_a_monthly_lump_and_labels_it_allocated(
        self, client, finance_user, sandbox
    ):
        channel = sandbox["channel"]
        r = client.post(
            f"{API_PREFIX}/admin/marketing-spend",
            json={
                "grain": SpendGrain.MONTHLY,
                # Mid-month on purpose: the server must normalise to the month.
                "period": date(2018, 6, 12).isoformat(),
                "channel": channel,
                "amount": "40000.00",
                "quality": SpendQuality.ACTUAL,
            },
            headers=finance_user.headers,
        )
        assert r.status_code == 201, r.text
        assert r.json()["spend"]["period_start"] == "2018-06-01"
        assert r.json()["spend"]["period_end"] == "2018-06-30"

        listed = client.get(
            f"{API_PREFIX}/admin/marketing-spend"
            f"?date_from=2018-06-01&date_to=2018-06-30&include_daily=true"
            f"&channel={channel}",
            headers=finance_user.headers,
        ).json()

        assert len(listed["daily"]) == 30
        assert Decimal(listed["daily_total"]) == Decimal("40000.00")
        # ALLOCATED, never ACTUAL — even though the row itself is a settled
        # invoice. A June invoice is evidence about June and an inference about
        # the 12th, and the label is the only thing that says which.
        assert {d["quality"] for d in listed["daily"]} == {
            MetricQuality.ALLOCATED.value
        }

    def test_a_daily_entry_keeps_its_own_quality(self, client, finance_user, sandbox):
        channel = sandbox["channel"]
        client.post(
            f"{API_PREFIX}/admin/marketing-spend",
            json={
                "grain": SpendGrain.DAILY,
                "period": D_START.isoformat(),
                "channel": channel,
                "amount": "500.00",
                "quality": SpendQuality.ACTUAL,
            },
            headers=finance_user.headers,
        )
        listed = client.get(
            f"{API_PREFIX}/admin/marketing-spend"
            f"?date_from={D_START}&date_to={D_START}&include_daily=true"
            f"&channel={channel}",
            headers=finance_user.headers,
        ).json()
        assert len(listed["daily"]) == 1
        assert listed["daily"][0]["quality"] == MetricQuality.ACTUAL.value

        # An asserted-but-unverified figure is ESTIMATED, never ACTUAL.
        client.post(
            f"{API_PREFIX}/admin/marketing-spend",
            json={
                "grain": SpendGrain.DAILY,
                "period": D_START.isoformat(),
                "channel": channel,
                "amount": "500.00",
                "quality": SpendQuality.ASSUMED,
            },
            headers=finance_user.headers,
        )
        listed = client.get(
            f"{API_PREFIX}/admin/marketing-spend"
            f"?date_from={D_START}&date_to={D_START}&include_daily=true"
            f"&channel={channel}",
            headers=finance_user.headers,
        ).json()
        assert listed["daily"][0]["quality"] == MetricQuality.ESTIMATED.value

    def test_spend_upsert_replaces_rather_than_accumulates(
        self, client, finance_user, sandbox
    ):
        """A double-clicked form must not double the store's reported spend."""
        payload = {
            "grain": SpendGrain.MONTHLY,
            "period": date(2018, 6, 1).isoformat(),
            "channel": sandbox["channel"],
            "amount": "40000.00",
        }
        for _ in range(3):
            client.post(
                f"{API_PREFIX}/admin/marketing-spend",
                json=payload,
                headers=finance_user.headers,
            )
        with SessionLocal() as db:
            rows = (
                db.query(AnalyticsMarketingSpend)
                .filter(AnalyticsMarketingSpend.channel == sandbox["channel"])
                .all()
            )
        assert len(rows) == 1
        assert rows[0].amount == Decimal("40000.00")

    def test_spend_write_enqueues_its_period(self, client, finance_user, sandbox):
        r = client.post(
            f"{API_PREFIX}/admin/marketing-spend",
            json={
                "grain": SpendGrain.MONTHLY,
                "period": date(2018, 6, 1).isoformat(),
                "channel": sandbox["channel"],
                "amount": "40000.00",
            },
            headers=finance_user.headers,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["recompute_from"] == "2018-06-01"
        assert body["recompute_to"] == "2018-06-30"
        assert body["recompute_queued"] == 30 * len(body["recompute_jobs"])


# ===========================================================================
# 7. Schema reflection, and the migration/twin-SQL twins agree
# ===========================================================================
BACKEND_ROOT = Path(__file__).resolve().parents[1]
REVISION_ID = "d51e8072ca43"
REVISION_PATH = (
    BACKEND_ROOT / "alembic" / "versions" / f"{REVISION_ID}_add_analytics_marketing_spend.py"
)
TWIN_SQL_PATH = (
    BACKEND_ROOT / "scripts" / "sql" / "2026-07-29_analytics_marketing_spend.sql"
)


def _sql_statements(text_blob: str) -> list[str]:
    """Executable statements only: comments, blank lines and INFO chatter gone,
    whitespace collapsed, so a reformat cannot fail the comparison but a changed
    column can."""
    body = "\n".join(
        line
        for line in text_blob.splitlines()
        if line.strip()
        and not line.startswith("INFO  [")
        and not line.lstrip().startswith("--")
    )
    return [
        re.sub(r"\s+", " ", stmt).strip().rstrip(";")
        for stmt in body.split(";")
        if stmt.strip()
    ]


class TestSchemaAndMigration:
    def test_orm_table_matches_the_database(self):
        """The live schema must match the ORM. The analytics schema ships as two
        artifacts that can silently diverge — this revision and the hand-applied
        twin SQL — and drift means production 500s while CI stays green."""
        table = AnalyticsMarketingSpend.__table__
        with SessionLocal() as db:
            inspector = inspect(db.get_bind())
            assert inspector.has_table(table.name), (
                f"{table.name} is declared in the ORM but missing from the "
                "database. Run `alembic upgrade d51e8072ca43`, or apply "
                f"{TWIN_SQL_PATH.name} on the shared DB."
            )
            actual = {c["name"]: c for c in inspector.get_columns(table.name)}
            indexes = {ix["name"] for ix in inspector.get_indexes(table.name)}
            uniques = {
                uc["name"]: uc
                for uc in inspector.get_unique_constraints(table.name)
            }

        assert set(actual) == {c.name for c in table.columns}
        for column in table.columns:
            assert actual[column.name]["nullable"] == column.nullable, column.name

        # No foreign keys, ever: analytics tables stay independently truncatable
        # and a deleted staff account must not take the store's spend with it.
        assert not table.foreign_keys

        declared_indexes = {ix.name for ix in table.indexes}
        assert not declared_indexes - indexes, declared_indexes - indexes
        assert "uq_analytics_marketing_spend_key" in uniques

        # Every column in the unique key must be NOT NULL. MySQL permits many
        # NULLs under a UNIQUE index, so a nullable member silently disables the
        # idempotency guarantee the key exists to provide.
        for name in uniques["uq_analytics_marketing_spend_key"]["column_names"]:
            assert actual[name]["nullable"] is False, name

    def test_model_is_registered_for_autogenerate(self):
        """`app/db/base.py` must import the model, or Alembic autogenerate
        cannot see the table and will propose dropping it."""
        from app.db.base import Base

        assert "analytics_marketing_spend" in Base.metadata.tables

    def test_migration_and_twin_sql_agree(self):
        """The revision and the hand-applied SQL are generated from one source.

        Production never runs alembic (DEPLOY.md §6), so the twin SQL is what the
        shared database actually gets. If it drifts from the revision, CI passes
        against a schema production does not have.
        """
        spec = importlib.util.spec_from_file_location("_rev", REVISION_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        previous = module.down_revision
        assert module.revision == REVISION_ID

        generated = subprocess.run(
            ["alembic", "upgrade", f"{previous}:{REVISION_ID}", "--sql"],
            cwd=BACKEND_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        expected = [
            stmt
            for stmt in _sql_statements(generated.stdout)
            # Stripped from the twin on purpose: the shared remote DB is on a
            # migration lineage this repo does not contain, and stamping it with
            # a revision id from this chain would corrupt its migration state.
            if not stmt.startswith("UPDATE alembic_version")
        ]
        actual = _sql_statements(TWIN_SQL_PATH.read_text())

        assert expected, "the revision generated no DDL"
        assert actual == expected, (
            "backend/scripts/sql/2026-07-29_analytics_marketing_spend.sql has "
            f"drifted from revision {REVISION_ID}. Regenerate it with "
            f"`alembic upgrade {previous}:{REVISION_ID} --sql` and strip the "
            "alembic_version stamp."
        )
        assert "UPDATE alembic_version" not in TWIN_SQL_PATH.read_text()
