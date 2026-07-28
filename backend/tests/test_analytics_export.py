"""Tests for CSV export: the row ceiling, explicit truncation, and the safety net.

Why this module exists separately from ``test_analytics_views_api.py``
---------------------------------------------------------------------
That module proves the export *endpoint's* contract — permissions, 404s, the
header block, formula sanitisation — with a **stub resolver**, which is right
for what it asserts and is exactly why it could not catch the defect this module
was written for: the export asked for 50 000 rows, every real resolver clamped
that back to 200, and no test ran a real resolver on the export path.

So the doubles here are one layer lower. `view_service`, the registry and every
registered resolver are the REAL ones; only the **repository** is a double, and
it is a double that always has more rows than the caller asked for. That is the
only way to observe the clamp: the question "did the export get more than 200
rows" is a question about the limit a resolver passes to the repository, and a
stubbed resolver never passes one.

Three properties are asserted, and they pull against each other on purpose:

**The ceiling is a property of the request, not of the resolver.** An on-screen
request stays capped at 200 (a browser rendering 50 000 rows is its own outage);
an export gets `EXPORT_ROW_CAP`. Raising the shared cap would satisfy the first
assertion and hand any dashboard request the whole table, so the test for the
screen cap is not a formality — it is the other half of the fix.

**Nothing a client sends can move the ceiling.** `?export=true` on a view
request is asserted to change nothing. The signal is a context variable that
only `POST /exports` sets; if it ever became a filter field, that test fails.

**Truncation is never silent.** All three bounds — the resolver's row limit, the
50 000-row file cap and the ~45s build budget — end the file with a
`# TRUNCATED` row and set `X-Analytics-Truncated`. A CSV that stops quietly
looks complete, reconciles against nothing, and is acted on.

Conventions
-----------
The router is not mounted on the real app, so each test builds a local
`FastAPI()` with the app-wide exception handlers registered — without them every
`AppError` surfaces as a 500 and every status assertion is meaningless. Users
are throwaway NON-ADMIN accounts created through their own `SessionLocal` and
deleted in a `finally`; non-admin is load-bearing, because `has_permission`
short-circuits True for an admin and would pass every grant untested. Dates live
in a 2007 sandbox that no other suite uses.

Run inside the analytics container:

    docker exec wvana-py python -m pytest tests/test_analytics_export.py -q
"""
from __future__ import annotations

import csv
import inspect
import io
import re
import time
import uuid
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.v1.endpoints import analytics_views
from app.core import config as _config
from app.core.exceptions import register_exception_handlers
from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal
from app.repositories.analytics_repository import HARD_ROW_CAP
from app.services.analytics import export as export_mod
from app.services.analytics import registry
from app.services.analytics.filters import AnalyticsFilters
from app.services.analytics.resolvers import (
    NOT_CONFIGURED,
    RESOLVERS,
    ResolverContext,
    get_resolver,
)
from app.services.analytics.resolvers.special import CUSTOM_FUNCTIONS
from app.services.analytics.view_service import AnalyticsViewService

API_PREFIX = "/api/v1/analytics"

BASE = "analytics.view"
EXPORT_PERM = "analytics.export"
PRODUCTS_PERM = "analytics.products.view"
CUSTOMERS_PERM = "analytics.customers.view"

#: LIVE, exportable, BREAKDOWN — the highest-cardinality export shape in the
#: registry and the one the row cap is actually about.
PRODUCTS_VIEW = ("products", "product-performance")
#: Exportable, and declares NO `TableSpec`: its resolver names the table itself
#: (`cohort_grid`). The export route has to find that block without an id to
#: match on, which it could not do before this change.
UNDECLARED_TABLE_VIEW = ("customers", "cohort-and-retention")

#: An unused reporting window. Every other analytics suite owns a different
#: year, so a stray row written by one cannot be read by another.
SANDBOX_FROM = date(2007, 3, 1)
SANDBOX_TO = date(2007, 3, 31)

#: The classic DDE payload. If this reaches a cell unescaped, opening the file
#: in Excel runs it.
FORMULA_PAYLOAD = "=cmd|' /C calc'!A0"

#: nginx's `proxy_read_timeout` in front of this app. The build budget has to
#: stay under it: a response killed mid-stream arrives as a *valid-looking*
#: short CSV with no truncation marker at all.
NGINX_PROXY_READ_TIMEOUT_SEC = 60.0


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
    """Off for the duration. Nothing here asserts on rate limiting, and a
    filling IP bucket would make a repeated local run flaky. The dependency
    itself stays on the route — this only disables the backend."""
    original = _config.settings.RATE_LIMIT_ENABLED
    _config.settings.RATE_LIMIT_ENABLED = False
    try:
        yield
    finally:
        _config.settings.RATE_LIMIT_ENABLED = original


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------
class NullCache:
    """No-op cache with `AnalyticsCache`'s interface.

    Redis is shared across the whole container. A real entry written by one test
    would silently satisfy the next one's request, and every assertion about row
    counts would be measuring the previous test.
    """

    def generation(self) -> int:
        return 1

    def build_key(self, **kwargs) -> str:
        return "test:" + ":".join(str(v) for v in kwargs.values())

    def get(self, _key):
        return None

    def set(self, _key, _payload, _ttl) -> None:
        return None


class WideRepo:
    """A repository double that always has more rows than it was asked for.

    This is the whole point of the module. `AnalyticsRepository` is the layer
    that receives the row limit a resolver decided on, so recording that limit
    is how "the export was clamped back to 200" becomes an observable fact
    rather than a code reading.

    `rows_available` is what the underlying table would hold. Returning
    `min(limit, rows_available)` reproduces the real contract exactly, including
    the `limit + 1` probe every resolver uses to detect a truncated list.
    """

    def __init__(self, window, *, rows_available: int = 1_000, first_key=None):
        self.window = window
        self.rows_available = rows_available
        self.first_key = first_key
        #: Every limit any resolver asked for, in call order.
        self.limits: list[int | None] = []

    # -- provenance --------------------------------------------------------
    def distinct_tz_generations(self, _source, _window) -> set[int]:
        return {1}

    def source_watermark(self, _source, _generation):
        """Fully aggregated through the last day of the window.

        Anything less and a resolver correctly returns nothing, which would make
        every row-count assertion below pass for the wrong reason.
        """
        return self.window.date_to - timedelta(days=1), 5_000

    # -- reads -------------------------------------------------------------
    def fetch_rollup(
        self,
        _source,
        *,
        columns,
        window,
        tz_generation,
        group_by=None,
        filters=None,
        order_by=None,
        limit=None,
        offset=0,
    ) -> list[dict]:
        self.limits.append(limit)
        wanted = self.rows_available if limit is None else int(limit)
        count = max(0, min(wanted, self.rows_available))

        rows: list[dict] = []
        for i in range(count):
            row: dict = {}
            for key in group_by or ():
                row[key] = _group_value(key, i, window)
            for column in columns:
                row.setdefault(column, Decimal("1"))
            rows.append(row)

        if self.first_key is not None and rows and group_by:
            rows[0][group_by[0]] = self.first_key
        return rows

    def fetch_totals(self, _source, *, columns, window, tz_generation, filters=None):
        return {column: Decimal("1") for column in columns}


#: Grouping keys a resolver reads back as something other than a label — a date
#: it buckets by, or an integer it calls `int()` on. Returning a string for
#: these would fail inside the resolver rather than in an assertion, which is a
#: test double lying about the schema rather than a finding.
_NUMERIC_GROUP_KEYS = frozenset(
    {"bucket_hour", "period_index", "product_id", "category_id_snapshot"}
)


def _group_value(key: str, index: int, window):
    if key == "bucket_date":
        return window.date_from + timedelta(days=index % 28)
    if key in _NUMERIC_GROUP_KEYS:
        return index
    return f"P{index:05d}"


@pytest.fixture()
def wide_repo(monkeypatch):
    """Inject one `WideRepo` into every service the router builds.

    `analytics_views._service` is the seam. Swapping it keeps the real registry,
    the real `AnalyticsViewService` and the real resolvers on the path — only the
    SQL is replaced — which is what makes an assertion about the row limit an
    assertion about production behaviour.
    """
    window = _sandbox_filters().resolve(date(2007, 4, 1))

    def _install(**kwargs) -> WideRepo:
        repo = WideRepo(window, **kwargs)
        monkeypatch.setattr(
            analytics_views,
            "_service",
            lambda db, user: AnalyticsViewService(
                db, user, cache=NullCache(), repo=repo
            ),
        )
        return repo

    return _install


def _sandbox_filters() -> AnalyticsFilters:
    return AnalyticsFilters(
        period="custom", date_from=SANDBOX_FROM, date_to=SANDBOX_TO
    )


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
class PermissionedUser:
    """A throwaway NON-ADMIN user holding exactly the named permissions.

    Not named `TestUser*`: pytest would try to collect it as a test class.
    Non-admin is load-bearing — `User.has_permission` short-circuits True for an
    admin, so an admin account would pass every check below without exercising a
    single grant.
    """

    def __init__(self, *permissions: str) -> None:
        from app.models.rbac import Permission, Role
        from app.models.user import User

        self.role_ids: list[int] = []
        self.created_permission_ids: list[int] = []

        db = SessionLocal()
        try:
            user = User(
                email=f"analytics-export-{uuid.uuid4().hex[:8]}@example.com",
                hashed_password=hash_password("TestPass123!"),
                is_active=True,
                is_admin=False,
            )
            db.add(user)
            db.flush()

            if permissions:
                role = Role(
                    name=f"analytics-export-test-{uuid.uuid4().hex[:8]}",
                    description="test role",
                    is_system=False,
                )
                # Added and flushed BEFORE the association is built: appending to
                # `role.permissions` while `role` is transient makes SQLAlchemy
                # skip the backref cascade and write no `role_permissions` rows.
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
        finally:
            db.close()

        self.token = create_access_token(self.id)

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def cleanup(self) -> None:
        db = SessionLocal()
        try:
            db.execute(
                text("DELETE FROM audit_events WHERE actor_user_id = :uid"),
                {"uid": self.id},
            )
            db.execute(
                text("DELETE FROM user_roles WHERE user_id = :uid"), {"uid": self.id}
            )
            for role_id in self.role_ids:
                db.execute(
                    text("DELETE FROM role_permissions WHERE role_id = :rid"),
                    {"rid": role_id},
                )
                db.execute(text("DELETE FROM roles WHERE id = :rid"), {"rid": role_id})
            for perm_id in self.created_permission_ids:
                db.execute(
                    text("DELETE FROM permissions WHERE id = :pid"), {"pid": perm_id}
                )
            db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": self.id})
            db.commit()
        finally:
            db.close()


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


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------
def _window_query(**extra: object) -> str:
    params = {
        "period": "custom",
        "date_from": SANDBOX_FROM.isoformat(),
        "date_to": SANDBOX_TO.isoformat(),
        **extra,
    }
    return "&".join(f"{k}={v}" for k, v in params.items())


def _export(client: TestClient, user, pair, *, table_id=None, **extra):
    """POST an export: target in the body, filters in the query string."""
    module_slug, view_slug = pair
    body = {"module_slug": module_slug, "view_slug": view_slug}
    if table_id is not None:
        body["table_id"] = table_id
    return client.post(
        f"{API_PREFIX}/exports?{_window_query(**extra)}",
        json=body,
        headers=user.headers,
    )


def _get_view(client: TestClient, user, pair, **extra):
    module_slug, view_slug = pair
    return client.get(
        f"{API_PREFIX}/modules/{module_slug}/views/{view_slug}?{_window_query(**extra)}",
        headers=user.headers,
    )


def _csv_rows(body: str) -> list[list[str]]:
    """Data rows only — the `#` header block and the blank separator dropped."""
    rows = list(csv.reader(io.StringIO(body.lstrip("﻿"))))
    return [r for r in rows if r and not r[0].startswith("#")]


def _all_rows(body: str) -> list[list[str]]:
    return [r for r in csv.reader(io.StringIO(body.lstrip("﻿"))) if r]


# ===========================================================================
# 1. The defect: an export is not a top-200
# ===========================================================================
class TestExportRowCeiling:
    def test_an_export_returns_more_than_the_on_screen_cap(
        self, client, make_user, wide_repo
    ):
        """The bug, stated as a test.

        `POST /exports` substituted a 50 000-row limit and every resolver
        clamped it straight back to `MAX_LIMIT`, so a feature documented as bulk
        export delivered a top-200 and said nothing about it.
        """
        repo = wide_repo(rows_available=1_000)
        user = make_user(BASE, PRODUCTS_PERM, EXPORT_PERM)

        response = _export(client, user, PRODUCTS_VIEW)
        assert response.status_code == 200, response.text

        rows = _csv_rows(response.text)
        data = rows[1:]  # row 0 is the column header
        assert len(data) > export_mod.SCREEN_ROW_CAP, (
            "the export is still capped at the on-screen limit: "
            f"{len(data)} row(s)"
        )
        assert len(data) == 1_000
        assert response.headers["X-Analytics-Row-Count"] == "1000"

        # And it asked the repository for the export ceiling, not for 200. The
        # `+ 1` is the resolver's truncation probe.
        assert export_mod.EXPORT_ROW_CAP + 1 in repo.limits

    def test_an_on_screen_request_is_still_capped(self, client, make_user, wide_repo):
        """The other half of the fix.

        If the cap had simply been raised, this passes only by accident of the
        default `limit`. Asking for the maximum a client may request and getting
        200 is what proves a dashboard cannot pull the whole table.
        """
        repo = wide_repo(rows_available=1_000)
        user = make_user(BASE, PRODUCTS_PERM)

        response = _get_view(client, user, PRODUCTS_VIEW, limit=200)
        assert response.status_code == 200, response.text

        block = response.json()["tables"]["product_table"]
        assert len(block["rows"]) == export_mod.SCREEN_ROW_CAP
        assert block["truncated"] is True
        assert repo.limits == [export_mod.SCREEN_ROW_CAP + 1]

    def test_the_export_ceiling_cannot_be_asked_for_from_the_query_string(
        self, client, make_user, wide_repo
    ):
        """`?export=true` must be inert.

        The signal is deliberately not a field on `AnalyticsFilters`: that model
        is bound straight from the query string, so a field on it would be a
        client-settable lever that lifts the row cap on any request — the exact
        denial of service the cap exists to prevent.
        """
        repo = wide_repo(rows_available=1_000)
        user = make_user(BASE, PRODUCTS_PERM)

        response = _get_view(client, user, PRODUCTS_VIEW, limit=200, export="true")
        assert response.status_code == 200, response.text
        assert repo.limits == [export_mod.SCREEN_ROW_CAP + 1]

    def test_the_export_scope_does_not_outlive_the_request(
        self, client, make_user, wide_repo
    ):
        """A leaked flag would raise the cap for every later request in the
        worker. `export_scope` resets through its token in a `finally`."""
        wide_repo(rows_available=10)
        user = make_user(BASE, PRODUCTS_PERM, EXPORT_PERM)

        assert export_mod.is_exporting() is False
        assert _export(client, user, PRODUCTS_VIEW).status_code == 200
        assert export_mod.is_exporting() is False

    def test_the_resolver_ceiling_fits_inside_the_repository_cap(self):
        """`_execute_capped` RAISES above its cap rather than truncating, and
        every resolver asks for `limit + 1`. A ceiling equal to the repository's
        would therefore 500 on precisely the largest exports — the ones nobody
        runs by hand before a release."""
        assert export_mod.EXPORT_ROW_CAP + 1 <= HARD_ROW_CAP
        assert export_mod.EXPORT_ROW_CAP > export_mod.SCREEN_ROW_CAP


# ===========================================================================
# 2. Truncation is never silent
# ===========================================================================
def _build(rows, **kwargs):
    view = registry.get_view(*PRODUCTS_VIEW)
    return export_mod.build_csv_export(
        view=view,
        module_slug=PRODUCTS_VIEW[0],
        table_id="product_table",
        table_spec=view.tables[0],
        rows=rows,
        resolved=_sandbox_filters(),
        exported_by="tester@example.com",
        timezone_name="Asia/Kolkata",
        **kwargs,
    )


class TestTruncationIsExplicit:
    def test_a_fifty_thousand_row_export_truncates_with_the_row_and_the_header(self):
        """The file cap, exercised at its real value.

        A generator, not a list: `build_csv_export` consumes rows lazily so the
        cap applies while building, and materialising 50 001 rows to test the
        50 000-row bound would be testing the test.
        """
        def rows():
            for i in range(export_mod.CSV_ROW_CAP + 1):
                yield {"product": f"p{i}", "units": i, "revenue": Decimal("1")}

        export = _build(rows())

        assert export.row_count == export_mod.CSV_ROW_CAP
        assert export.truncated is True
        assert "row cap" in export.truncation_reason

        last = _all_rows(export.text())[-1]
        assert last[0] == export_mod.TRUNCATION_MARKER
        assert str(export_mod.CSV_ROW_CAP) in last[1]

        response = export_mod.csv_streaming_response(export)
        assert response.headers["X-Analytics-Truncated"] == "true"
        assert response.headers["X-Analytics-Row-Count"] == str(export_mod.CSV_ROW_CAP)
        assert response.headers["X-Analytics-Truncation-Reason"]

    def test_the_time_budget_truncates_rather_than_overrunning_it(self):
        """The budget exists because nginx kills the response at 60s and a
        killed stream arrives as a valid-looking short CSV. So the build must
        stop itself, and say that it did."""
        def slow_rows():
            for i in range(50_000):
                time.sleep(0.001)
                yield {"product": f"p{i}", "units": i}

        started = time.monotonic()
        export = _build(slow_rows(), time_budget_sec=0.05)
        elapsed = time.monotonic() - started

        assert export.truncated is True
        assert "time budget" in export.truncation_reason
        assert export.row_count < 50_000
        # Stopped near its own budget rather than running to the row cap.
        assert elapsed < 5.0, f"the budget did not bound the build: {elapsed:.1f}s"

        last = _all_rows(export.text())[-1]
        assert last[0] == export_mod.TRUNCATION_MARKER
        assert export_mod.csv_streaming_response(export).headers[
            "X-Analytics-Truncated"
        ] == "true"

    def test_the_build_budget_stays_under_the_nginx_read_timeout(self):
        """Stated as an assertion because the two numbers live in different
        repositories: raise this one past nginx's and every large export starts
        arriving truncated with no marker."""
        assert export_mod.CSV_TIME_BUDGET_SEC < NGINX_PROXY_READ_TIMEOUT_SEC

    def test_a_table_the_resolver_truncated_is_marked_in_the_file(
        self, client, make_user, wide_repo
    ):
        """The bound that actually bites in production.

        The resolver's ceiling is lower than the file's, so a table longer than
        it stops there. Before this change the CSV said nothing at all about it —
        the file simply ended, which is the silent truncation this module refuses.
        """
        wide_repo(rows_available=export_mod.EXPORT_ROW_CAP + 500)
        user = make_user(BASE, PRODUCTS_PERM, EXPORT_PERM)

        response = _export(client, user, PRODUCTS_VIEW)
        assert response.status_code == 200, response.text
        assert response.headers["X-Analytics-Truncated"] == "true"

        last = _all_rows(response.text)[-1]
        assert last[0] == export_mod.TRUNCATION_MARKER
        assert str(export_mod.EXPORT_ROW_CAP) in last[1]

    def test_an_export_that_fits_carries_no_marker(
        self, client, make_user, wide_repo
    ):
        wide_repo(rows_available=250)
        user = make_user(BASE, PRODUCTS_PERM, EXPORT_PERM)

        response = _export(client, user, PRODUCTS_VIEW)
        assert response.status_code == 200
        assert response.headers["X-Analytics-Truncated"] == "false"
        assert export_mod.TRUNCATION_MARKER not in response.text


# ===========================================================================
# 3. The safety properties the larger export must not have cost
# ===========================================================================
class TestExportSafetyProperties:
    def test_a_formula_in_a_product_name_is_still_neutralised(
        self, client, make_user, wide_repo
    ):
        """A CSV is a program. `=cmd|' /C calc'!A0` in a product name is remote
        command execution on the machine of whoever opens the file, and a
        product name is written by somebody outside the finance team."""
        wide_repo(rows_available=300, first_key=FORMULA_PAYLOAD)
        user = make_user(BASE, PRODUCTS_PERM, EXPORT_PERM)

        response = _export(client, user, PRODUCTS_VIEW)
        assert response.status_code == 200, response.text

        cell = _csv_rows(response.text)[1][0]
        assert cell == "'" + FORMULA_PAYLOAD, f"formula not neutralised: {cell!r}"
        # And the raw bytes quote the field, so no lenient parser can re-split it
        # back into something a spreadsheet would evaluate.
        assert f"\"'{FORMULA_PAYLOAD}\"" in response.text

    def test_export_without_the_export_permission_is_403(
        self, client, make_user, wide_repo
    ):
        """`analytics.export` is a separate grant from viewing: a CSV leaves the
        building. Read access to the view is not read access to a file."""
        wide_repo(rows_available=10)
        user = make_user(BASE, PRODUCTS_PERM)  # may view, may not export

        response = _export(client, user, PRODUCTS_VIEW)
        assert response.status_code == 403
        assert EXPORT_PERM in response.json()["error"]["message"]

    def test_an_export_writes_an_audit_event(self, client, make_user, wide_repo):
        """The audit row is the only record that a file left the building, and
        its row count has to be the real one — which is why the file is built
        before the response is handed back rather than around a lazy generator."""
        wide_repo(rows_available=300)
        user = make_user(BASE, PRODUCTS_PERM, EXPORT_PERM)

        response = _export(client, user, PRODUCTS_VIEW)
        assert response.status_code == 200
        assert response.content  # consume the stream; the request completes

        db = SessionLocal()
        try:
            row = (
                db.execute(
                    text(
                        "SELECT action, target_label, summary, extra, actor_email "
                        "FROM audit_events WHERE actor_user_id = :uid "
                        "ORDER BY id DESC LIMIT 1"
                    ),
                    {"uid": user.id},
                )
                .mappings()
                .first()
            )
        finally:
            db.close()

        assert row is not None, "no audit row was written for the export"
        assert row["action"] == analytics_views.EXPORT_AUDIT_ACTION
        assert row["target_label"] == "products/product-performance"
        assert row["actor_email"] == user.email
        assert "300" in row["summary"]

    def test_the_header_block_still_describes_the_file(
        self, client, make_user, wide_repo
    ):
        """A CSV outlives the URL that produced it by years. Without the
        preamble the first question anyone asks of it — "what period is this?" —
        is unanswerable."""
        wide_repo(rows_available=250)
        user = make_user(BASE, PRODUCTS_PERM, EXPORT_PERM)

        response = _export(client, user, PRODUCTS_VIEW)
        header = [
            r for r in _all_rows(response.text) if r and r[0].startswith("#")
        ]
        keys = {r[0].removeprefix("# ").strip(): r[1] for r in header if len(r) > 1}

        assert keys["view_slug"] == "product-performance"
        assert keys["exported_by"] == user.email
        assert keys["date_from"] == SANDBOX_FROM.isoformat()
        assert keys["date_to_exclusive"] == SANDBOX_TO.isoformat()
        assert keys["row_cap"] == str(export_mod.CSV_ROW_CAP)
        # The limit the resolver was actually run under, not the file cap.
        assert keys["row_limit"] == str(analytics_views.EXPORT_ROW_LIMIT)

    def test_a_view_whose_resolver_names_its_own_table_still_exports(
        self, client, make_user, wide_repo
    ):
        """View 12 declares no `TableSpec`; its resolver produces `cohort_grid`.
        With no id to match on, the route used to 404 on a table that was
        sitting in the envelope."""
        wide_repo(rows_available=40)
        user = make_user(BASE, CUSTOMERS_PERM, EXPORT_PERM)

        response = _export(client, user, UNDECLARED_TABLE_VIEW)
        assert response.status_code == 200, response.text
        assert len(_csv_rows(response.text)) > 1


# ===========================================================================
# 4. The regression guard for defect 2
# ===========================================================================
def _exportable_views() -> list[tuple[str, object]]:
    return [
        (module.slug, view)
        for module in registry.MODULES
        for view in module.views
        if view.export
    ]


EXPORTABLE = _exportable_views()

#: An assignment to a local named `tables`, which is how every resolver that can
#: return one builds it. The lookbehind drops `ctx.view.tables[0]` and
#: `base.tables` — reading the registry's *declared* tables is what the six
#: broken views did, and it is precisely not evidence that any get filled.
_TABLE_ASSIGNMENT = re.compile(r"(?<![.\w])tables\s*[=\[]")


def _resolver_can_ever_emit_a_table(view) -> bool:
    """Whether the shape this view names has a table output AT ALL.

    Read off the implementation rather than from a hand-maintained list, because
    a hand-maintained list is a second copy of the fact and would go stale in
    exactly the case this guards. For `custom` the class that matters is the
    named function's, not `CustomResolver`, which only dispatches.

    This is the structural question — *can* it — and is deliberately separate
    from whether it did on any given run. A resolver that declined because a
    binding is missing is a deployment gap; one with no table output is a view
    whose export button can never work however much data arrives.
    """
    resolver_id = view.resolver.value
    if resolver_id == "custom":
        name = (view.params or {}).get("fn") or view.bespoke
        fn = CUSTOM_FUNCTIONS.get(str(name))
        if fn is None:
            return False
        owner = getattr(fn, "__self__", None)
        target = type(owner) if owner is not None else fn
    else:
        target = type(RESOLVERS[resolver_id])
    return bool(_TABLE_ASSIGNMENT.search(inspect.getsource(target)))


class TestEveryExportableViewCanProduceATable:
    """The structural defect, guarded at the registry.

    Six views declared `export=True` and a table id on a resolver that emits
    only `series` and never `tables` — an export button that returned 404 every
    time it was pressed, on a view that looked healthy in every other respect.

    The check runs each view's REAL resolver against a repository double where
    every source is fully populated, then accepts one of three answers:

    * a table — the view can be exported, or
    * a `NOT_CONFIGURED` warning — the view is honestly unwired in this
      deployment (no cost rule, no experiment store, no supplier model). Ten
      exportable views answer this way today and that is correct: they say so in
      the envelope, they invent nothing, and connecting the source turns them on
      without a code change, or
    * no table but a resolver shape that HAS a table output — the binding is
      incomplete, which is a data question and somebody else's.

    What fails is the fourth case: no table, no reason, and a shape with no
    table output at all. That is a view whose export button can never work
    however much data arrives, which is what the six timeseries views were.
    """

    @pytest.mark.parametrize(
        "module_slug, view",
        EXPORTABLE,
        ids=[f"{slug}/{view.slug}" for slug, view in EXPORTABLE],
    )
    def test_it_emits_a_table_or_says_why_not(self, module_slug, view):
        filters = _sandbox_filters()
        window = filters.resolve(date(2007, 4, 1))

        db = SessionLocal()
        try:
            ctx = ResolverContext(
                db=db,
                repo=WideRepo(window, rows_available=25),
                view=view,
                filters=filters,
                window=window,
                tz_generation=1,
                today=date(2007, 4, 1),
            )
            result = get_resolver(view.resolver.value).run(ctx)
        finally:
            db.close()

        if result.tables:
            return
        if NOT_CONFIGURED in {w.code for w in result.warnings}:
            return
        assert _resolver_can_ever_emit_a_table(view), (
            f"{module_slug}/{view.slug} is marked export=True, but its "
            f"{view.resolver.value!r} resolver has no table output at all — it "
            "emits series only, so the export button 404s every time it is "
            "pressed. Bind the view to a resolver that emits a table, or set "
            "export=False. Do not leave a button that cannot work."
        )

    def test_the_registry_still_exports_something(self):
        """A guard on the guard: the parametrisation above is satisfied
        trivially if every export flag is switched off."""
        assert len(EXPORTABLE) >= 20

    @pytest.mark.parametrize(
        "resolver_id, params, bespoke, expected",
        [
            ("timeseries", {}, "", False),
            ("metrics", {}, "", False),
            ("breakdown", {}, "", True),
            ("table", {}, "", True),
            ("geo", {}, "", True),
            ("custom", {"fn": "snapshot"}, "", True),
            ("custom", {}, "experiment_results", False),
            ("custom", {}, "no_such_function", False),
        ],
    )
    def test_the_capability_check_tells_the_two_shapes_apart(
        self, resolver_id, params, bespoke, expected
    ):
        """The other guard on the guard.

        If `_resolver_can_ever_emit_a_table` ever answered True for everything —
        a regex that stopped matching, a refactor that renamed the local — the
        parametrisation above would keep passing while guarding nothing. These
        are shims rather than registry views on purpose: a peer rebinding a view
        must not turn this into a false alarm.
        """
        shim = SimpleNamespace(
            resolver=SimpleNamespace(value=resolver_id),
            params=params,
            bespoke=bespoke,
            slug="shim",
        )
        assert _resolver_can_ever_emit_a_table(shim) is expected

    @pytest.mark.parametrize(
        "module_slug, view_slug",
        [
            ("executive", "real-time-sales"),
            ("customers", "customer-churn"),
            ("customers", "loyalty-and-rewards"),
            ("website", "cart-abandonment"),
            ("payments", "cod-performance"),
            ("customer-experience", "website-speed-and-technical-performance"),
        ],
    )
    def test_the_six_chart_views_no_longer_advertise_an_export(
        self, module_slug, view_slug
    ):
        """Named individually so re-enabling one is a deliberate act.

        Each still declares a `TableSpec` — the intent is documented and the
        specs are part of the frontend contract — but none of them can be filled
        from a rollup this deployment holds, so none offers a download.
        """
        view = registry.get_view(module_slug, view_slug)
        assert view is not None
        assert view.export is False

    def test_an_export_of_a_view_that_declines_to_export_is_409(
        self, client, make_user, wide_repo
    ):
        """Refused with a reason, not with an empty CSV. An empty file reads as
        "there were no rows", which is a different and false statement."""
        wide_repo(rows_available=10)
        user = make_user(BASE, CUSTOMERS_PERM, EXPORT_PERM)

        response = _export(client, user, ("customers", "customer-churn"))
        assert response.status_code == 409
        assert "not exportable" in response.json()["error"]["message"]
