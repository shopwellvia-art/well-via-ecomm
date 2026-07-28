"""Tests for the analytics view surface: permission tiering, gating and export.

Strategy
--------
**The router is not mounted on the real app yet.** Wiring `api/v1/router.py` is
the lead's change and this module deliberately does not touch it, so every HTTP
test builds a local `FastAPI()`, registers the app-wide exception handlers and
mounts `analytics_views.router` under the prefix the lead will use. The handlers
matter: without them every `AppError` subclass surfaces as an unhandled 500 and
every status assertion below is meaningless.

**No shared login account.** Older test modules authenticate as a hardcoded
`vinay@gmail.com`, which only exists on the production database. These tests
create their own NON-ADMIN users, roles and permission grants and delete them in
a `finally` through a fresh `SessionLocal`. Non-admin is load-bearing:
`User.has_permission` short-circuits True for `is_admin`, so an admin account
would pass every check here without exercising a single grant.

**The resolver is a double.** `resolvers/` is a peer's module and its registered
resolvers query real rollup tables. These tests monkeypatch
`view_service.get_resolver`, which makes the assertions deterministic, keeps
them independent of which rollups happen to have rows, and — for the gated-view
test — lets a resolver that *raises on call* prove that no resolver ran.

**The cache is a double too.** `analytics_views._service` is the seam: swapping
it for one that injects an in-memory cache lets the tier-isolation test assert
on the actual reads and writes rather than on Redis being empty.

Run inside the analytics container:

    docker exec wvana-py python -m pytest tests/test_analytics_views_api.py -q
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.v1.endpoints import analytics_views
from app.core import config as _config
from app.core.exceptions import register_exception_handlers
from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal
from app.schemas.analytics_view import KpiValue, SourceRef, TableBlock
from app.services.analytics import export as export_mod
from app.services.analytics import registry
from app.services.analytics import view_service
from app.services.analytics.resolvers.base import ResolverResult
from app.services.analytics.types import MetricQuality
from app.services.analytics.view_service import AnalyticsViewService

API_PREFIX = "/api/v1/analytics"

# Registry fixtures the assertions lean on. Slugs, not indices, so a registry
# reorder cannot silently repoint a test at a different view.
BASE = "analytics.view"
ORDERS_PERM = "analytics.orders.view"
FINANCE_PERM = "analytics.finance.view"
SALES_PERM = "analytics.sales.view"
PRODUCTS_PERM = "analytics.products.view"
CUSTOMERS_PERM = "analytics.customers.view"
EXPORT_PERM = "analytics.export"
JOBS_PERM = "analytics.jobs.run"

#: LIVE, exportable, gated on a non-sensitive module permission.
ORDERS_VIEW = ("orders", "order-fulfilment")
#: LIVE, exportable, has a free-text `product` column — the injection target.
PRODUCTS_VIEW = ("products", "product-performance")
#: SENSITIVE tier: lives in the `sales-finance` module but requires
#: `analytics.finance.view`, so the module grant alone must not open it.
FINANCE_VIEW = ("sales-finance", "pricing-and-margin")
#: FEATURE_REQUIRED — must return the gated envelope and run nothing.
GATED_VIEW = ("customers", "subscription-commerce")

#: The classic DDE payload. If this reaches a cell unescaped, opening the file
#: in Excel runs it.
FORMULA_PAYLOAD = "=cmd|' /C calc'!A0"
SANITISE_PREFIX = "'"


# ---------------------------------------------------------------------------
# App / client
# ---------------------------------------------------------------------------
@pytest.fixture()
def app() -> FastAPI:
    test_app = FastAPI()
    register_exception_handlers(test_app)
    test_app.include_router(analytics_views.router, prefix=API_PREFIX)
    return test_app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _no_rate_limit():
    """Off for the duration: nothing here asserts on rate limiting, and a
    filling IP bucket would make repeated local runs flaky."""
    original = _config.settings.RATE_LIMIT_ENABLED
    _config.settings.RATE_LIMIT_ENABLED = False
    try:
        yield
    finally:
        _config.settings.RATE_LIMIT_ENABLED = original


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------
class FakeCache:
    """In-memory stand-in with `AnalyticsCache`'s interface.

    `build_key` keeps `visibility` in the key exactly as the real one does, so
    the tier-isolation assertion is about the real property and not about this
    double being conveniently empty.
    """

    def __init__(self) -> None:
        self.store: dict[str, dict] = {}
        self.reads: list[str] = []
        self.writes: list[tuple[str, int]] = []

    def generation(self) -> int:
        return 1

    def build_key(
        self, *, module: str, view: str, filter_hash: str, tz_generation: int, visibility: str
    ) -> str:
        return f"g1:t{tz_generation}:{visibility}:view:{module}/{view}:{filter_hash}"

    def get(self, key: str):
        self.reads.append(key)
        return self.store.get(key)

    def set(self, key: str, payload: dict, ttl: int) -> None:
        self.writes.append((key, ttl))
        self.store[key] = payload

    def reset(self) -> None:
        """Forget everything. Used where two requests in one test must not
        share an entry — a cold cache is the honest baseline."""
        self.store.clear()
        self.reads.clear()
        self.writes.clear()


class StubResolver:
    """A resolver that returns a fixed, fully-formed `ResolverResult`."""

    id = "stub"

    def __init__(self, result: ResolverResult | None = None) -> None:
        self.result = result if result is not None else _sample_result()
        self.calls = 0

    def run(self, ctx):
        self.calls += 1
        return self.result


class ExplodingResolver:
    """Raises if it is ever run. Proves the gated path issues no query."""

    id = "exploding"

    def run(self, ctx):  # pragma: no cover - the assertion is that this never runs
        raise AssertionError(
            "a resolver ran for a gated view; gated views must cost nothing"
        )


def _sample_result(rows: list[dict] | None = None) -> ResolverResult:
    return ResolverResult(
        kpis={
            "orders_count": KpiValue(
                kpi_id="orders_count", value=Decimal("12"), format="int"
            )
        },
        series={"orders_trend": [{"date": "2026-07-01", "orders_count": 12}]},
        tables={
            "ageing_orders": TableBlock(rows=rows or [], total_rows=len(rows or [])),
            "product_table": TableBlock(rows=rows or [], total_rows=len(rows or [])),
        },
        sources=[
            SourceRef(
                id="order_daily",
                kind="rollup",
                label="Order daily rollup",
                rows=30,
                through=date(2026, 7, 27),
            )
        ],
        warnings=[],
        quality=MetricQuality.AUTHORITATIVE,
        coverage_pct=Decimal("100"),
    )


@pytest.fixture()
def stub_resolver(monkeypatch) -> StubResolver:
    """Point `view_service.get_resolver` at a deterministic double."""
    stub = StubResolver()
    monkeypatch.setattr(view_service, "get_resolver", lambda _rid: stub)
    return stub


@pytest.fixture()
def no_resolver(monkeypatch) -> ExplodingResolver:
    boom = ExplodingResolver()
    monkeypatch.setattr(view_service, "get_resolver", lambda _rid: boom)
    return boom


@pytest.fixture(autouse=True)
def fake_cache(monkeypatch) -> FakeCache:
    """Inject one shared in-memory cache into every service the router builds.

    Autouse, and one instance per test. Redis is shared across the whole
    container, so a test that primed a real entry would silently satisfy the
    next test's request from cache and every assertion about resolver calls,
    availability and freshness would be measuring the previous test.
    """
    cache = FakeCache()
    monkeypatch.setattr(
        analytics_views,
        "_service",
        lambda db, user: AnalyticsViewService(db, user, cache=cache),
    )
    return cache


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
def _uid() -> str:
    return uuid.uuid4().hex[:8]


class PermissionedUser:
    """A throwaway non-admin user holding exactly the named permissions.

    Not named `TestUser*`: pytest would try to collect it as a test class and
    warn about the constructor.
    """

    def __init__(self, *permissions: str) -> None:
        from app.models.rbac import Permission, Role
        from app.models.user import User

        self.role_ids: list[int] = []
        self.created_permission_ids: list[int] = []
        self.permissions = list(permissions)

        with SessionLocal() as db:
            user = User(
                email=f"analytics-view-{_uid()}@example.com",
                hashed_password=hash_password("TestPass123!"),
                is_active=True,
                # Non-admin on purpose: `has_permission` returns True for every
                # permission when `is_admin`, which would make every grant below
                # untested.
                is_admin=False,
            )
            db.add(user)
            db.flush()

            if permissions:
                role = Role(
                    name=f"analytics-view-test-{_uid()}",
                    description="test role",
                    is_system=False,
                )
                # Added and flushed BEFORE the association is built: appending
                # to `role.permissions` while `role` is transient makes
                # SQLAlchemy skip the backref cascade and silently write no
                # `role_permissions` rows.
                db.add(role)
                db.flush()
                for name in permissions:
                    perm = (
                        db.query(Permission).filter(Permission.name == name).first()
                    )
                    if perm is None:
                        # Seeded on a normal deploy; create it if this DB
                        # predates the registry entry, and remember to remove
                        # only what we created.
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
    """Create throwaway users; delete every one of them in teardown."""
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


def _view_url(pair: tuple[str, str], **params: object) -> str:
    module_slug, view_slug = pair
    url = f"{API_PREFIX}/modules/{module_slug}/views/{view_slug}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{query}"
    return url


# ===========================================================================
# 1. Unknown module or view -> 404
# ===========================================================================
class TestUnknownSlugs:
    def test_unknown_module_is_404(self, client, make_user):
        user = make_user(BASE, ORDERS_PERM)
        r = client.get(_view_url(("no-such-module", "anything")), headers=user.headers)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"

    def test_unknown_view_in_a_real_module_is_404(self, client, make_user):
        user = make_user(BASE, ORDERS_PERM)
        r = client.get(_view_url(("orders", "no-such-view")), headers=user.headers)
        assert r.status_code == 404

    def test_unknown_module_detail_is_404(self, client, make_user):
        user = make_user(BASE, ORDERS_PERM)
        r = client.get(f"{API_PREFIX}/modules/nope", headers=user.headers)
        assert r.status_code == 404

    def test_a_slug_never_reaches_a_resolver(self, client, make_user, no_resolver):
        """The registry lookup runs first, so an unknown slug cannot dispatch."""
        user = make_user(BASE, ORDERS_PERM)
        r = client.get(_view_url(("orders", "'; DROP TABLE orders--")), headers=user.headers)
        assert r.status_code == 404


# ===========================================================================
# 2. + 3. Permission tiering
# ===========================================================================
class TestPermissionTiering:
    def test_without_the_views_permission_is_403(self, client, make_user, stub_resolver):
        user = make_user(BASE)  # base access only
        r = client.get(_view_url(ORDERS_VIEW), headers=user.headers)
        assert r.status_code == 403
        assert ORDERS_PERM in r.json()["error"]["message"]
        assert stub_resolver.calls == 0

    def test_with_the_views_permission_is_200(self, client, make_user, stub_resolver):
        user = make_user(BASE, ORDERS_PERM)
        r = client.get(_view_url(ORDERS_VIEW), headers=user.headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["view"]["view"] == "order-fulfilment"
        assert body["view"]["module"] == "orders"
        assert stub_resolver.calls == 1

    def test_base_permission_alone_does_not_open_a_finance_view(
        self, client, make_user, stub_resolver
    ):
        """The SENSITIVE tier bites: `analytics.view` is not enough."""
        user = make_user(BASE)
        r = client.get(_view_url(FINANCE_VIEW), headers=user.headers)
        assert r.status_code == 403
        assert FINANCE_PERM in r.json()["error"]["message"]
        assert stub_resolver.calls == 0

    def test_the_modules_own_permission_does_not_open_a_finance_view(
        self, client, make_user, stub_resolver
    ):
        """`pricing-and-margin` lives in the `sales-finance` module, which is
        gated on `analytics.sales.view`. Holding the *module* grant must not
        open a view that declares the sensitive one."""
        user = make_user(BASE, SALES_PERM)
        r = client.get(_view_url(FINANCE_VIEW), headers=user.headers)
        assert r.status_code == 403
        assert stub_resolver.calls == 0

    def test_the_finance_grant_opens_the_finance_view(
        self, client, make_user, stub_resolver
    ):
        user = make_user(BASE, FINANCE_PERM)
        r = client.get(_view_url(FINANCE_VIEW), headers=user.headers)
        assert r.status_code == 200, r.text

    def test_no_analytics_access_at_all_is_403(self, client, make_user):
        user = make_user()  # no grants
        r = client.get(_view_url(ORDERS_VIEW), headers=user.headers)
        assert r.status_code == 403
        assert BASE in r.json()["error"]["message"]

    def test_anonymous_is_401(self, client):
        r = client.get(_view_url(ORDERS_VIEW))
        assert r.status_code == 401


# ===========================================================================
# 4. A gated view costs nothing
# ===========================================================================
class TestGatedViews:
    def test_gated_view_returns_the_gated_envelope_and_runs_nothing(
        self, client, make_user, no_resolver, fake_cache
    ):
        user = make_user(BASE, CUSTOMERS_PERM)
        r = client.get(_view_url(GATED_VIEW), headers=user.headers)
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["availability"] == "FEATURE_REQUIRED"
        assert body["requires"] == ["subscriptions"]
        assert body["limitation"]
        # The small shape: nothing a client could draw an axis through.
        assert "kpis" not in body
        assert "series" not in body
        assert "tables" not in body
        # And nothing touched the cache on the way.
        assert fake_cache.reads == []
        assert fake_cache.writes == []

    def test_a_gated_view_is_still_403_without_its_permission(
        self, client, make_user, no_resolver
    ):
        """Authorisation runs before the gate, so a gated view does not become
        a free description of what the deployment is missing."""
        user = make_user(BASE)
        r = client.get(_view_url(GATED_VIEW), headers=user.headers)
        assert r.status_code == 403
        assert "requires" not in r.json()


# ===========================================================================
# 5. Navigation hides what it must
# ===========================================================================
class TestNavigation:
    def test_navigation_shows_only_permitted_modules(self, client, make_user):
        user = make_user(BASE, ORDERS_PERM)
        r = client.get(f"{API_PREFIX}/modules", headers=user.headers)
        assert r.status_code == 200, r.text
        body = r.json()

        slugs = [m["slug"] for m in body["modules"]]
        assert slugs == ["orders"]
        assert body["module_count"] == 1

        orders = registry.get_module("orders")
        assert {v["slug"] for v in body["modules"][0]["views"]} == {
            v.slug for v in orders.views
        }
        # `default_view_slug` must point at a view this user can actually open.
        assert body["modules"][0]["default_view_slug"] in {
            v["slug"] for v in body["modules"][0]["views"]
        }

    def test_navigation_leaks_no_hidden_view_slug(self, client, make_user):
        user = make_user(BASE, ORDERS_PERM)
        raw = client.get(f"{API_PREFIX}/modules", headers=user.headers).text

        hidden = [
            v.slug
            for m in registry.MODULES
            if m.slug != "orders"
            for v in m.views
        ]
        leaked = [slug for slug in hidden if slug in raw]
        assert leaked == [], f"navigation leaked hidden view slugs: {leaked}"

    def test_a_user_with_no_module_grants_sees_nothing(self, client, make_user):
        user = make_user(BASE)
        body = client.get(f"{API_PREFIX}/modules", headers=user.headers).json()
        assert body["modules"] == []
        assert body["view_count"] == 0

    def test_module_detail_is_403_when_every_view_is_hidden(self, client, make_user):
        user = make_user(BASE, ORDERS_PERM)
        r = client.get(f"{API_PREFIX}/modules/customers", headers=user.headers)
        assert r.status_code == 403

    def test_module_detail_returns_only_visible_views(self, client, make_user):
        """`sales-finance` mixes two tiers: a sales user sees its sales views
        and must not see the three finance ones."""
        user = make_user(BASE, SALES_PERM)
        r = client.get(f"{API_PREFIX}/modules/sales-finance", headers=user.headers)
        assert r.status_code == 200, r.text
        slugs = {v["slug"] for v in r.json()["views"]}

        module = registry.get_module("sales-finance")
        finance_slugs = {
            v.slug for v in module.views if v.permission == FINANCE_PERM
        }
        assert finance_slugs, "fixture assumption: the module mixes tiers"
        assert slugs.isdisjoint(finance_slugs)

    def test_navigation_service_hides_modules_with_no_visible_views(self, make_user):
        """The service-level assertion, independent of HTTP."""
        user = make_user(BASE, ORDERS_PERM)
        with SessionLocal() as db:
            from app.models.user import User

            row = db.get(User, user.id)
            nav = AnalyticsViewService(db, row, cache=FakeCache()).navigation()
        assert [m["slug"] for m in nav["modules"]] == ["orders"]


# ===========================================================================
# 6. Cache isolation across visibility tiers
# ===========================================================================
class TestCacheIsolation:
    def test_a_primed_finance_entry_is_never_served_to_a_non_finance_user(
        self, client, make_user, stub_resolver, fake_cache
    ):
        finance = make_user(BASE, FINANCE_PERM)
        sales = make_user(BASE, SALES_PERM)

        primed = client.get(_view_url(FINANCE_VIEW), headers=finance.headers)
        assert primed.status_code == 200, primed.text
        assert len(fake_cache.writes) == 1
        reads_after_priming = len(fake_cache.reads)

        blocked = client.get(_view_url(FINANCE_VIEW), headers=sales.headers)
        assert blocked.status_code == 403
        # Not a cached body — and the cache was not even consulted, because
        # authorisation runs first.
        assert blocked.json()["error"]["code"] == "forbidden"
        assert "kpis" not in blocked.json()
        assert len(fake_cache.reads) == reads_after_priming

    def test_a_second_finance_user_does_hit_the_primed_entry(
        self, client, make_user, stub_resolver, fake_cache
    ):
        """The flip side: the tier is a tier, not a user id, or the cache would
        never hit."""
        first = make_user(BASE, FINANCE_PERM)
        second = make_user(BASE, FINANCE_PERM)

        assert client.get(_view_url(FINANCE_VIEW), headers=first.headers).status_code == 200
        r = client.get(_view_url(FINANCE_VIEW), headers=second.headers)
        assert r.status_code == 200
        assert r.json()["cache"]["hit"] is True
        assert stub_resolver.calls == 1  # the second request resolved nothing

    def test_the_tier_token_differs_by_sensitive_grant(self, make_user):
        from app.models.user import User

        finance = make_user(BASE, FINANCE_PERM)
        customers = make_user(BASE, CUSTOMERS_PERM)
        plain = make_user(BASE)

        with SessionLocal() as db:
            tiers = {
                name: AnalyticsViewService(
                    db, db.get(User, u.id), cache=FakeCache()
                ).visibility_tier()
                for name, u in (
                    ("finance", finance),
                    ("customers", customers),
                    ("plain", plain),
                )
            }
        assert tiers["finance"] == "t10"
        assert tiers["customers"] == "t01"
        assert tiers["plain"] == "t00"
        assert len(set(tiers.values())) == 3

    def test_the_tier_is_in_the_cache_key(self, make_user):
        from app.models.user import User

        from app.services.analytics.filters import AnalyticsFilters

        finance = make_user(BASE, FINANCE_PERM)
        plain = make_user(BASE)
        cache = FakeCache()
        filters = AnalyticsFilters()

        with SessionLocal() as db:
            keys = []
            for u in (finance, plain):
                svc = AnalyticsViewService(db, db.get(User, u.id), cache=cache)
                keys.append(
                    cache.build_key(
                        module="sales-finance",
                        view="pricing-and-margin",
                        filter_hash=filters.cache_key_part(),
                        tz_generation=1,
                        visibility=svc.visibility_tier(),
                    )
                )
        assert keys[0] != keys[1]


# ===========================================================================
# 7. refresh=true is privileged
# ===========================================================================
class TestRefresh:
    def test_refresh_without_jobs_run_is_403_not_silently_ignored(
        self, client, make_user, stub_resolver, fake_cache
    ):
        user = make_user(BASE, ORDERS_PERM)
        r = client.get(_view_url(ORDERS_VIEW, refresh="true"), headers=user.headers)
        assert r.status_code == 403
        assert JOBS_PERM in r.json()["error"]["message"]
        assert stub_resolver.calls == 0
        assert fake_cache.reads == []

    def test_refresh_with_jobs_run_bypasses_the_cache_read_but_still_writes(
        self, client, make_user, stub_resolver, fake_cache
    ):
        user = make_user(BASE, ORDERS_PERM, JOBS_PERM)

        first = client.get(_view_url(ORDERS_VIEW), headers=user.headers)
        assert first.status_code == 200
        assert len(fake_cache.writes) == 1
        reads_before = len(fake_cache.reads)

        second = client.get(_view_url(ORDERS_VIEW, refresh="true"), headers=user.headers)
        assert second.status_code == 200
        assert second.json()["cache"]["hit"] is False
        assert len(fake_cache.reads) == reads_before  # read skipped
        assert len(fake_cache.writes) == 2  # write still happened
        assert stub_resolver.calls == 2


# ===========================================================================
# 8. + 9. + 10. Export
# ===========================================================================
def _export(
    client: TestClient,
    user,
    pair: tuple[str, str],
    *,
    table_id: str | None = None,
    **params: object,
):
    """POST an export: target in the body, filters in the query string."""
    module_slug, view_slug = pair
    url = f"{API_PREFIX}/exports"
    if params:
        url = f"{url}?" + "&".join(f"{k}={v}" for k, v in params.items())
    body = {"module_slug": module_slug, "view_slug": view_slug}
    if table_id is not None:
        body["table_id"] = table_id
    return client.post(url, json=body, headers=user.headers)


def _parse_csv(body: str) -> tuple[list[list[str]], list[list[str]]]:
    """Split an export into (header block rows, data rows incl. column row)."""
    rows = list(csv.reader(io.StringIO(body.lstrip("﻿"))))
    header = [r for r in rows if r and r[0].startswith("#")]
    data = [r for r in rows if r and not r[0].startswith("#")]
    return header, data


@pytest.fixture()
def export_resolver(monkeypatch) -> StubResolver:
    """A resolver whose product table carries a formula-injection payload."""
    stub = StubResolver(
        _sample_result(
            rows=[
                {
                    "product": FORMULA_PAYLOAD,
                    "units": 3,
                    "revenue": Decimal("-1250.00"),
                    "return_rate": Decimal("0.5"),
                },
                {
                    "product": "Ashwagandha, 60ct",
                    "units": 11,
                    "revenue": Decimal("4400.00"),
                    "return_rate": Decimal("0"),
                },
            ]
        )
    )
    monkeypatch.setattr(view_service, "get_resolver", lambda _rid: stub)
    return stub


class TestExport:
    def test_export_without_the_export_permission_is_403(
        self, client, make_user, export_resolver
    ):
        user = make_user(BASE, PRODUCTS_PERM)  # can view, cannot export
        r = _export(client, user, PRODUCTS_VIEW)
        assert r.status_code == 403
        assert EXPORT_PERM in r.json()["error"]["message"]
        assert export_resolver.calls == 0

    def test_export_permission_is_not_a_side_door_into_finance(
        self, client, make_user, export_resolver
    ):
        """`analytics.export` is a format, never an escalation."""
        user = make_user(BASE, EXPORT_PERM, PRODUCTS_PERM)
        r = _export(client, user, FINANCE_VIEW)
        assert r.status_code == 403
        assert export_resolver.calls == 0

    def test_export_sanitises_a_formula_in_a_product_name(
        self, client, make_user, export_resolver
    ):
        user = make_user(BASE, PRODUCTS_PERM, EXPORT_PERM)
        r = _export(client, user, PRODUCTS_VIEW)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/csv")
        assert 'attachment; filename="' in r.headers["content-disposition"]

        _header, data = _parse_csv(r.text)
        column_row, first_row = data[0], data[1]
        assert column_row == ["Product", "Units", "Revenue", "Returns"]

        cell = first_row[0]
        assert cell.startswith("'"), f"formula was not neutralised: {cell!r}"
        assert cell == SANITISE_PREFIX + FORMULA_PAYLOAD
        # And the raw bytes quote the field, so no lenient parser can re-split
        # it back into something a spreadsheet would evaluate.
        assert f'"{SANITISE_PREFIX}{FORMULA_PAYLOAD}"' in r.text

    def test_export_does_not_mangle_a_negative_number(
        self, client, make_user, export_resolver
    ):
        """A `-1250.00` refund is a number, not a formula; prefixing it would
        turn every negative figure in the file into unsummable text."""
        user = make_user(BASE, PRODUCTS_PERM, EXPORT_PERM)
        r = _export(client, user, PRODUCTS_VIEW)
        _header, data = _parse_csv(r.text)
        revenue = data[1][2]  # the row carrying the injection payload
        assert revenue == "-1250.00"

    def test_export_header_block_describes_the_window(
        self, client, make_user, export_resolver
    ):
        user = make_user(BASE, PRODUCTS_PERM, EXPORT_PERM)
        r = _export(client, user, PRODUCTS_VIEW, period="7d")
        header, _data = _parse_csv(r.text)
        keys = {row[0].removeprefix("# ").strip(): row[1] for row in header if len(row) > 1}
        assert keys["view_slug"] == "product-performance"
        assert keys["module_slug"] == "products"
        assert keys["period"] == "7d"
        assert keys["exported_by"] == user.email
        assert keys["reporting_timezone"]
        assert keys["date_from"] and keys["date_to_exclusive"]
        assert keys["row_cap"] == str(export_mod.CSV_ROW_CAP)

    def test_export_writes_an_audit_event(self, client, make_user, export_resolver):
        user = make_user(BASE, PRODUCTS_PERM, EXPORT_PERM)
        r = _export(client, user, PRODUCTS_VIEW)
        assert r.status_code == 200
        # StreamingResponse: consume it so the request completes end to end.
        assert r.content

        with SessionLocal() as db:
            row = db.execute(
                text(
                    "SELECT action, target_label, summary, extra, actor_email "
                    "FROM audit_events WHERE actor_user_id = :uid "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"uid": user.id},
            ).mappings().first()

        assert row is not None, "no audit row was written for the export"
        assert row["action"] == analytics_views.EXPORT_AUDIT_ACTION
        assert row["target_label"] == "products/product-performance"
        assert row["actor_email"] == user.email
        assert "2" in row["summary"]  # two rows exported

    def test_export_of_a_gated_view_is_409_not_an_empty_csv(
        self, client, make_user, no_resolver
    ):
        user = make_user(BASE, CUSTOMERS_PERM, EXPORT_PERM)
        r = _export(client, user, GATED_VIEW)
        assert r.status_code == 409
        assert r.json()["error"]["details"]["requires"] == ["subscriptions"]

    def test_export_of_an_unknown_view_is_404(self, client, make_user):
        user = make_user(BASE, PRODUCTS_PERM, EXPORT_PERM)
        r = _export(client, user, ("products", "nope"))
        assert r.status_code == 404

    def test_export_of_an_unknown_table_is_404_not_the_first_table(
        self, client, make_user, export_resolver
    ):
        user = make_user(BASE, PRODUCTS_PERM, EXPORT_PERM)
        r = _export(client, user, PRODUCTS_VIEW, table_id="not_a_table")
        assert r.status_code == 404


# ===========================================================================
# 9b. The exporter itself, without HTTP
# ===========================================================================
class TestCsvSanitisation:
    @pytest.mark.parametrize("payload", ["=1+1", "+1", "-1", "@SUM(A1)", "\tx", "\rx", "\nx"])
    def test_every_trigger_character_is_neutralised(self, payload):
        assert export_mod.sanitise_cell(payload).startswith("'")

    def test_an_embedded_equals_is_left_alone(self):
        assert export_mod.sanitise_cell("size=XL") == "size=XL"

    def test_a_sanitised_field_is_also_quoted(self):
        line = export_mod.csv_line([FORMULA_PAYLOAD])
        assert line.startswith('"\'=cmd')

    def test_a_numeric_negative_is_not_sanitised(self):
        text_value, forced = export_mod.render_cell(Decimal("-1250.00"))
        assert text_value == "-1250.00"
        assert forced is False

    def test_a_string_that_looks_negative_is_sanitised(self):
        text_value, forced = export_mod.render_cell("-1250.00")
        assert text_value == "'-1250.00"
        assert forced is True

    def test_a_quote_in_a_value_is_doubled(self):
        assert export_mod.csv_field('say "hi"') == '"say ""hi"""'


class TestCsvBounds:
    def _build(self, rows, **kwargs):
        view = registry.get_view(*PRODUCTS_VIEW)
        return export_mod.build_csv_export(
            view=view,
            module_slug="products",
            table_id="product_table",
            table_spec=view.tables[0],
            rows=rows,
            resolved=_resolved_filters_stub(),
            exported_by="tester@example.com",
            timezone_name="Asia/Kolkata",
            **kwargs,
        )

    def test_the_row_cap_truncates_with_an_explicit_final_row(self):
        export = self._build(
            [{"product": f"p{i}", "units": i} for i in range(10)], row_cap=3
        )
        assert export.row_count == 3
        assert export.truncated is True
        last = list(csv.reader(io.StringIO(export.text().lstrip("﻿"))))[-1]
        assert last[0] == export_mod.TRUNCATION_MARKER
        assert "row cap" in last[1]

    def test_the_time_budget_truncates_with_an_explicit_final_row(self):
        def slow_rows():
            for i in range(100):
                yield {"product": f"p{i}", "units": i}

        export = self._build(slow_rows(), time_budget_sec=-1.0)
        assert export.truncated is True
        assert "time budget" in export.truncation_reason
        last = list(csv.reader(io.StringIO(export.text().lstrip("﻿"))))[-1]
        assert last[0] == export_mod.TRUNCATION_MARKER

    def test_an_untruncated_export_has_no_marker(self):
        export = self._build([{"product": "p", "units": 1}])
        assert export.truncated is False
        assert export_mod.TRUNCATION_MARKER not in export.text()

    def test_the_filename_is_header_safe(self):
        export = self._build([])
        assert '"' not in export.filename
        assert "\n" not in export.filename
        assert export.filename.endswith(".csv")

    def test_columns_follow_the_registry_spec_not_the_row_dict(self):
        """Column order is a contract: a CSV whose columns move cannot be
        diffed against last month's."""
        export = self._build([{"units": 1, "product": "p", "revenue": 2}])
        _header, data = _parse_csv(export.text())
        column_row = [r for r in data if r and r[0] == "Product"][0]
        assert column_row == ["Product", "Units", "Revenue", "Returns"]


def _resolved_filters_stub():
    from app.schemas.analytics_view import ResolvedFilters

    return ResolvedFilters(
        period="30d",
        date_from=date(2026, 6, 28),
        date_to=date(2026, 7, 28),
        granularity="day",
        comparison="previous_period",
        status_basis="revenue",
        limit=20,
    )


# ===========================================================================
# 11. Filter validation -> 422
# ===========================================================================
class TestFilterValidation:
    def test_from_after_to_is_422(self, client, make_user, stub_resolver):
        user = make_user(BASE, ORDERS_PERM)
        r = client.get(
            _view_url(
                ORDERS_VIEW,
                period="custom",
                date_from="2026-07-28",
                date_to="2026-07-01",
            ),
            headers=user.headers,
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_error"
        assert "date_from" in r.text
        assert stub_resolver.calls == 0

    def test_a_range_over_the_cap_is_422_not_a_silent_truncation(
        self, client, make_user, stub_resolver
    ):
        user = make_user(BASE, ORDERS_PERM)
        start = date(2024, 1, 1)
        r = client.get(
            _view_url(
                ORDERS_VIEW,
                period="custom",
                date_from=start.isoformat(),
                date_to=(start + timedelta(days=401)).isoformat(),
            ),
            headers=user.headers,
        )
        assert r.status_code == 422
        assert "400" in r.text
        assert stub_resolver.calls == 0

    def test_custom_without_dates_is_422(self, client, make_user, stub_resolver):
        user = make_user(BASE, ORDERS_PERM)
        r = client.get(_view_url(ORDERS_VIEW, period="custom"), headers=user.headers)
        assert r.status_code == 422

    def test_hourly_over_its_own_cap_is_422(self, client, make_user, stub_resolver):
        user = make_user(BASE, ORDERS_PERM)
        start = date(2026, 1, 1)
        r = client.get(
            _view_url(
                ORDERS_VIEW,
                period="custom",
                granularity="hour",
                date_from=start.isoformat(),
                date_to=(start + timedelta(days=30)).isoformat(),
            ),
            headers=user.headers,
        )
        assert r.status_code == 422

    def test_an_over_large_limit_is_422(self, client, make_user, stub_resolver):
        user = make_user(BASE, ORDERS_PERM)
        r = client.get(_view_url(ORDERS_VIEW, limit=5000), headers=user.headers)
        assert r.status_code == 422

    def test_a_filter_this_view_ignores_is_reported_as_ignored(
        self, client, make_user, stub_resolver
    ):
        """`AnalyticsFilters` accepts the union of every dimension so a copied
        URL still opens. That is only honest if the response says what it threw
        away."""
        user = make_user(BASE, ORDERS_PERM)
        r = client.get(_view_url(ORDERS_VIEW, courier="DTDC"), headers=user.headers)
        assert r.status_code == 200, r.text
        assert "courier" in r.json()["filters"]["ignored"]


# ===========================================================================
# 12. `today` comes from store-local time, not UTC
# ===========================================================================
class TestStoreLocalToday:
    def _date_to(self, client, user, tz_name: str, monkeypatch, cache) -> date:
        # A cold cache each time. The reporting timezone is NOT in the cache key
        # — `tz_generation` is, and changing the store timezone in production
        # bumps that generation (`analytics_tz_generations` records the zone it
        # was built under). Monkeypatching the zone without bumping the
        # generation is a situation only a test can create, so the test clears
        # the cache rather than the key growing a component to accommodate it.
        cache.reset()
        monkeypatch.setattr(
            view_service, "store_timezone", lambda _db: ZoneInfo(tz_name)
        )
        r = client.get(_view_url(ORDERS_VIEW, period="7d"), headers=user.headers)
        assert r.status_code == 200, r.text
        return date.fromisoformat(r.json()["filters"]["date_to"])

    def test_the_window_follows_the_store_timezone(
        self, client, make_user, stub_resolver, fake_cache, monkeypatch
    ):
        """Two timezones 25 hours apart can never share a local date, so if the
        window were built from `date.today()` these would be identical."""
        user = make_user(BASE, ORDERS_PERM)

        far_east = self._date_to(
            client, user, "Pacific/Kiritimati", monkeypatch, fake_cache
        )  # UTC+14
        far_west = self._date_to(
            client, user, "Pacific/Midway", monkeypatch, fake_cache
        )  # UTC-11

        assert far_east != far_west
        assert (far_east - far_west).days >= 1

        utc_today = datetime.now(timezone.utc).date()
        assert far_east >= utc_today
        assert far_west <= utc_today

    def test_the_reported_timezone_matches_the_store(
        self, client, make_user, stub_resolver, fake_cache, monkeypatch
    ):
        user = make_user(BASE, ORDERS_PERM)
        monkeypatch.setattr(
            view_service, "store_timezone", lambda _db: ZoneInfo("Asia/Kolkata")
        )
        body = client.get(_view_url(ORDERS_VIEW), headers=user.headers).json()
        assert body["timezone"] == "Asia/Kolkata"


# ===========================================================================
# Envelope honesty
# ===========================================================================
class TestEnvelope:
    def test_the_envelope_carries_provenance_and_freshness(
        self, client, make_user, stub_resolver
    ):
        user = make_user(BASE, ORDERS_PERM)
        body = client.get(_view_url(ORDERS_VIEW), headers=user.headers).json()

        assert body["sources"][0]["id"] == "order_daily"
        assert body["sources"][0]["through"] == "2026-07-27"
        assert body["freshness"] == registry.get_view(*ORDERS_VIEW).freshness.value
        assert body["last_updated_at"] is not None
        assert body["currency"] == "INR"
        assert body["tz_generation"] >= 1

    def test_last_updated_at_is_null_when_nothing_has_aggregated(
        self, client, make_user, monkeypatch
    ):
        """Null, never "now" — a timestamp of now over an empty rollup reads as
        a confident zero taken a second ago."""
        stub = StubResolver(ResolverResult(sources=[]))
        monkeypatch.setattr(view_service, "get_resolver", lambda _rid: stub)
        user = make_user(BASE, ORDERS_PERM)
        body = client.get(_view_url(ORDERS_VIEW), headers=user.headers).json()
        assert body["last_updated_at"] is None

    def test_a_live_view_with_incomplete_numbers_is_downgraded_to_partial(
        self, client, make_user, monkeypatch
    ):
        """The registry declares a ceiling; a run may only ever lower it."""
        stub = StubResolver(
            ResolverResult(
                kpis={
                    "orders_count": KpiValue(
                        kpi_id="orders_count",
                        value=None,
                        quality=MetricQuality.INCOMPLETE.value,
                        inputs_missing=["cost_rule"],
                    )
                }
            )
        )
        monkeypatch.setattr(view_service, "get_resolver", lambda _rid: stub)
        user = make_user(BASE, ORDERS_PERM)
        body = client.get(_view_url(ORDERS_VIEW), headers=user.headers).json()

        assert registry.get_view(*ORDERS_VIEW).state.value == "LIVE"
        assert body["availability"] == "PARTIAL"
        assert body["quality"] == "INCOMPLETE"
        assert body["is_partial"] is True

    def test_a_window_including_today_warns_that_it_is_partial(
        self, client, make_user, stub_resolver
    ):
        user = make_user(BASE, ORDERS_PERM)
        body = client.get(
            _view_url(ORDERS_VIEW, period="mtd"), headers=user.headers
        ).json()
        assert "PARTIAL_TODAY" in {w["code"] for w in body["warnings"]}
        assert body["is_partial"] is True


# ===========================================================================
# Against the REAL resolvers
# ===========================================================================
class TestRealResolverDispatch:
    """No stub. These prove the service composes with the peer's resolvers,
    repository and registry as actually shipped — the stub proves the ordering,
    this proves the wiring.

    They assert on shape and honesty metadata, never on values: the rollup
    tables may be empty on a fresh test database, and "no rows yet" is a
    legitimate answer this envelope is built to carry.
    """

    def test_a_live_view_resolves_through_the_registered_resolver(
        self, client, make_user
    ):
        user = make_user(BASE, ORDERS_PERM)
        r = client.get(_view_url(ORDERS_VIEW), headers=user.headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["view"]["number"] == registry.get_view(*ORDERS_VIEW).number
        assert body["availability"] in {"LIVE", "PARTIAL"}
        assert body["quality"] in {
            "AUTHORITATIVE", "ACTUAL", "ALLOCATED", "ESTIMATED", "INCOMPLETE"
        }
        assert body["timezone"]
        assert body["tz_generation"] >= 1

    def test_every_resolver_the_registry_names_is_registered(self):
        """A registry view naming a resolver nobody implemented would 503 at
        request time. Catch it here instead of in production."""
        from app.services.analytics.resolvers.base import RESOLVERS

        from app.services.analytics.types import GATED_STATES as _GATED

        missing = sorted(
            {
                v.resolver.value
                for v in registry.all_views()
                if v.state not in _GATED and v.resolver.value not in RESOLVERS
            }
        )
        assert missing == [], f"registry names unregistered resolvers: {missing}"

    def test_every_non_gated_view_resolves_without_raising(self, make_user):
        """All 51 of them, through the real stack. Not asserting on numbers —
        asserting that no view 500s, and that none silently reports a gated
        state as data."""
        from app.models.user import User

        from app.services.analytics.filters import AnalyticsFilters
        from app.services.analytics.types import GATED_STATES as _GATED

        admin_ish = make_user(
            BASE,
            *sorted({m.permission for m in registry.MODULES}
                    | {v.permission for v in registry.all_views()}),
        )
        failures: list[str] = []
        with SessionLocal() as db:
            svc = AnalyticsViewService(
                db, db.get(User, admin_ish.id), cache=FakeCache()
            )
            for module in registry.MODULES:
                for view in module.views:
                    if view.state in _GATED:
                        continue
                    try:
                        env = svc.resolve_view(
                            module.slug, view.slug, AnalyticsFilters()
                        )
                    except Exception as exc:  # noqa: BLE001
                        failures.append(f"{module.slug}/{view.slug}: {exc!r}")
                        continue
                    if env.availability in {s.value for s in _GATED}:
                        failures.append(
                            f"{module.slug}/{view.slug}: non-gated view reported "
                            f"{env.availability}"
                        )
        assert failures == [], "\n".join(failures)


# ===========================================================================
# KPI catalogue
# ===========================================================================
class TestKpiCatalogue:
    def test_the_catalogue_is_served_with_tooltips(self, client, make_user):
        user = make_user(BASE)
        r = client.get(f"{API_PREFIX}/kpis", headers=user.headers)
        assert r.status_code == 200
        body = r.json()
        assert body["count"] > 0
        assert all(k["tooltip"] for k in body["kpis"])

    def test_one_kpi_can_be_fetched_by_id(self, client, make_user):
        user = make_user(BASE)
        r = client.get(f"{API_PREFIX}/kpis?kpi_id=aov", headers=user.headers)
        assert r.status_code == 200
        assert r.json()["kpis"][0]["id"] == "aov"

    def test_an_unknown_kpi_is_404(self, client, make_user):
        user = make_user(BASE)
        r = client.get(f"{API_PREFIX}/kpis?kpi_id=nope", headers=user.headers)
        assert r.status_code == 404

    def test_the_catalogue_needs_base_analytics_access(self, client, make_user):
        user = make_user()
        r = client.get(f"{API_PREFIX}/kpis", headers=user.headers)
        assert r.status_code == 403
