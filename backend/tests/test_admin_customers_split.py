"""Tests for the staff/customer split and the guards it hangs on.

Same hermetic pattern as `test_admin_users.py` — in-memory SQLite plus an
in-process fake Redis, with the users, roles and customers routers mounted on a
purpose-built app so `require_permission`, the real services and the real error
envelopes are all exercised.

    ENVIRONMENT=test MYSQL_HOST=localhost pytest tests/test_admin_customers_split.py -v

What matters here, in order of severity:

1. **Privilege escalation.** Before this change `RoleService.assign_to_user`
   resolved role ids and assigned them with no checks at all, so anyone holding
   `users.assign_role` could grant themselves `admin`. Four guards now stand in
   the way and each has a test.
2. **Lockout.** The last enabled superadmin cannot be disabled or stripped.
3. **Tier separation.** `/customers` cannot reach a staff account, and per-person
   money is invisible without the SENSITIVE `analytics.customers.view`.
4. **The split itself.** `scope=staff` and the customer directory partition the
   same table with no overlap and no gaps.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — register every table on Base.metadata
from app.api.deps import get_current_user, get_db
from app.api.v1.endpoints.customers import router as customers_router
from app.api.v1.endpoints.roles import router as roles_router
from app.api.v1.endpoints.users import router as users_router
from app.core.exceptions import register_exception_handlers
from app.core.security import hash_password
from app.models.analytics_control import AnalyticsTzGeneration
from app.models.analytics_rollups import AggCustomerSnapshot
from app.models.audit import AuditEvent
from app.models.base import Base
from app.models.customer import AccountStatus, Customer
from app.models.order import Order
from app.models.rbac import Permission, Role
from app.models.user import User
from tests.test_admin_users import FakeRedis

#: Every permission these tests hand out. Mirrors the registry names exactly —
#: a typo here would silently grant nothing and the tests would still "pass".
PERMS = (
    "users.view",
    "users.manage",
    "users.assign_role",
    "users.invite",
    "customers.view",
    "customers.manage",
    "analytics.customers.view",
    "products.view",
    "roles.view",
)


class Env:
    def __init__(self, client, db_factory, fake_redis, sent_emails, users, roles):
        self.client = client
        self.db = db_factory
        self.redis = fake_redis
        self.sent_emails = sent_emails
        self.users = users
        self.roles = roles
        self._actor = {"id": users["superadmin"]}

    def act_as(self, name: str) -> None:
        self._actor["id"] = self.users[name]


def _seed(db: Session) -> tuple[dict[str, int], dict[str, int]]:
    perms = {name: Permission(name=name, group_name="Test") for name in PERMS}
    db.add_all(list(perms.values()))

    # `customer` is the empty system role every shopper may carry. Holding it
    # must NOT make someone staff — that is the whole subtlety of the split.
    customer_role = Role(name="customer", is_system=True, permissions=[])
    # Full user-admin reach but NOT analytics.customers.view: this is the ops
    # tier that must never see a named person's spend.
    support = Role(
        name="support",
        is_system=False,
        permissions=[
            perms["users.view"],
            perms["users.manage"],
            perms["users.assign_role"],
            perms["users.invite"],
            perms["customers.view"],
            perms["customers.manage"],
            perms["roles.view"],
        ],
    )
    # The escalation target: holds a permission `support` does not.
    privileged = Role(
        name="privileged",
        is_system=False,
        permissions=[perms["analytics.customers.view"], perms["products.view"]],
    )
    # Ops + the sensitive analytics tier, for the money-visibility test.
    analyst = Role(
        name="analyst",
        is_system=False,
        permissions=[perms["customers.view"], perms["analytics.customers.view"]],
    )
    db.add_all([customer_role, support, privileged, analyst])
    db.flush()

    def mk(email, *, is_admin=False, roles=None, first=None, last=None, active=True):
        u = User(
            email=email,
            hashed_password=hash_password("TestPass123!"),
            is_active=active,
            is_admin=is_admin,
        )
        if roles:
            u.roles = roles
        db.add(u)
        db.flush()
        db.add(
            Customer(
                user_id=u.id,
                first_name=first,
                last_name=last,
                account_status=(
                    AccountStatus.ACTIVE if active else AccountStatus.DEACTIVATED
                ),
            )
        )
        return u

    users = {
        "superadmin": mk("root@example.com", is_admin=True, first="Root"),
        "other_admin": mk("root2@example.com", is_admin=True, first="Root", last="Two"),
        "support": mk("support@example.com", roles=[support], first="Sup"),
        "analyst": mk("analyst@example.com", roles=[analyst], first="Ana"),
        "shopper": mk("shopper@example.com", first="Asha", last="Rao"),
        # A shopper who carries the empty `customer` role — still a customer.
        "role_shopper": mk(
            "roled@example.com", roles=[customer_role], first="Rolled", last="Shopper"
        ),
    }
    db.commit()
    return (
        {k: u.id for k, u in users.items()},
        {
            "customer": customer_role.id,
            "support": support.id,
            "privileged": privileged.id,
            "analyst": analyst.id,
        },
    )


@pytest.fixture
def env(monkeypatch) -> Env:
    fake = FakeRedis()
    for module in (
        "app.db.redis",
        "app.services.auth_service",
        "app.services.session_service",
        "app.services.settings_service",
        "app.core.rate_limit",
    ):
        monkeypatch.setattr(f"{module}.get_redis", lambda fake=fake: fake)

    sent: list[dict] = []

    def capture_send_email(*, to, subject, body, db=None, html=None):
        sent.append({"to": to, "subject": subject, "body": body, "html": html})

    monkeypatch.setattr("app.services.auth_service.send_email", capture_send_email)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with db_factory() as db:
        users, roles = _seed(db)

    test_app = FastAPI()
    register_exception_handlers(test_app)
    test_app.include_router(users_router, prefix="/api/v1/users")
    test_app.include_router(roles_router, prefix="/api/v1/roles")
    test_app.include_router(customers_router, prefix="/api/v1/customers")

    def override_get_db():
        db = db_factory()
        try:
            yield db
        finally:
            db.close()

    holder = {"id": users["superadmin"]}

    def override_current_user(db: Session = Depends(get_db)) -> User:
        return db.get(User, holder["id"])

    test_app.dependency_overrides[get_db] = override_get_db
    test_app.dependency_overrides[get_current_user] = override_current_user

    e = Env(TestClient(test_app), db_factory, fake, sent, users, roles)
    e._actor = holder
    return e


def _roles_of(env: Env, user_id: int) -> set[str]:
    with env.db() as db:
        user = db.get(User, user_id)
        return {r.name for r in user.roles}


# ---------------------------------------------------------------------------
# 1. Privilege escalation — the hole this change closes
# ---------------------------------------------------------------------------


class TestRoleAssignmentGuards:
    def test_cannot_grant_permissions_you_do_not_hold(self, env: Env):
        """`support` holds no analytics.customers.view, so it cannot hand out a
        role that carries one — the escalation-by-proxy path."""
        env.act_as("support")
        r = env.client.put(
            f"/api/v1/roles/users/{env.users['shopper']}",
            json={"role_ids": [env.roles["privileged"]]},
        )
        assert r.status_code == 403
        assert "cannot grant permissions you do not hold" in r.json()["error"]["message"]
        assert _roles_of(env, env.users["shopper"]) == set()

    def test_cannot_change_own_roles(self, env: Env):
        """The direct path: grant yourself more, keep the session you have."""
        env.act_as("support")
        r = env.client.put(
            f"/api/v1/roles/users/{env.users['support']}",
            json={"role_ids": [env.roles["support"], env.roles["privileged"]]},
        )
        assert r.status_code == 403
        assert "your own roles" in r.json()["error"]["message"]
        assert _roles_of(env, env.users["support"]) == {"support"}

    def test_non_superadmin_cannot_reassign_a_superadmin(self, env: Env):
        env.act_as("support")
        r = env.client.put(
            f"/api/v1/roles/users/{env.users['other_admin']}",
            json={"role_ids": [env.roles["support"]]},
        )
        assert r.status_code == 403
        assert "superadmin" in r.json()["error"]["message"]

    def test_superadmin_can_still_assign(self, env: Env):
        """The guards must not break the legitimate path."""
        env.act_as("superadmin")
        r = env.client.put(
            f"/api/v1/roles/users/{env.users['shopper']}",
            json={"role_ids": [env.roles["support"]]},
        )
        assert r.status_code == 200
        assert _roles_of(env, env.users["shopper"]) == {"support"}

    def test_granting_a_subset_of_your_own_is_allowed(self, env: Env):
        """`support` may hand out `support` — everything in it, it already holds."""
        env.act_as("support")
        r = env.client.put(
            f"/api/v1/roles/users/{env.users['shopper']}",
            json={"role_ids": [env.roles["support"]]},
        )
        assert r.status_code == 200
        assert _roles_of(env, env.users["shopper"]) == {"support"}


class TestLockoutProtection:
    def test_last_active_superadmin_cannot_be_disabled(self, env: Env):
        env.act_as("superadmin")
        # Disable the spare first — allowed, one superadmin still stands.
        r = env.client.patch(
            f"/api/v1/users/{env.users['other_admin']}", json={"is_active": False}
        )
        assert r.status_code == 200

        # Now the actor is the only one left.
        r = env.client.patch(
            f"/api/v1/users/{env.users['superadmin']}", json={"is_active": False}
        )
        assert r.status_code == 403
        assert "last active superadmin" in r.json()["error"]["message"]
        with env.db() as db:
            assert db.get(User, env.users["superadmin"]).is_active is True


# ---------------------------------------------------------------------------
# 2. The split
# ---------------------------------------------------------------------------


class TestStaffScope:
    def test_scope_staff_lists_only_staff(self, env: Env):
        env.act_as("superadmin")
        r = env.client.get("/api/v1/users?scope=staff&page_size=100")
        assert r.status_code == 200
        emails = {u["email"] for u in r.json()["items"]}
        assert emails == {
            "root@example.com",
            "root2@example.com",
            "support@example.com",
            "analyst@example.com",
        }

    def test_empty_customer_role_is_not_staff(self, env: Env):
        """A shopper carrying the seeded `customer` role must not be promoted
        into the staff directory by that alone."""
        env.act_as("superadmin")
        r = env.client.get("/api/v1/users?scope=staff&page_size=100")
        assert "roled@example.com" not in {u["email"] for u in r.json()["items"]}

    def test_default_scope_is_unchanged(self, env: Env):
        """`/users` with no scope keeps its pre-split behaviour so existing
        callers are unaffected."""
        env.act_as("superadmin")
        r = env.client.get("/api/v1/users?page_size=100")
        assert r.json()["total"] == 6


class TestCustomerDirectory:
    def test_lists_only_non_staff(self, env: Env):
        env.act_as("support")
        r = env.client.get("/api/v1/customers?page_size=100")
        assert r.status_code == 200
        emails = {c["email"] for c in r.json()["items"]}
        assert emails == {"shopper@example.com", "roled@example.com"}

    def test_requires_customers_view(self, env: Env):
        env.act_as("shopper")
        r = env.client.get("/api/v1/customers")
        assert r.status_code == 403
        assert "customers.view" in r.json()["error"]["message"]

    def test_staff_target_is_404_on_detail(self, env: Env):
        """The customer API's universe is shoppers. A staff id must not resolve
        through it — and must 404 rather than 403, so the shape of the staff
        directory does not leak to an operator who cannot see it."""
        env.act_as("support")
        r = env.client.get(f"/api/v1/customers/{env.users['other_admin']}")
        assert r.status_code == 404

    def test_staff_target_is_404_on_patch(self, env: Env):
        env.act_as("support")
        r = env.client.patch(
            f"/api/v1/customers/{env.users['other_admin']}",
            json={"is_active": False},
        )
        assert r.status_code == 404
        with env.db() as db:
            assert db.get(User, env.users["other_admin"]).is_active is True

    def test_patch_customer_updates_and_audits(self, env: Env):
        env.act_as("support")
        r = env.client.patch(
            f"/api/v1/customers/{env.users['shopper']}",
            json={"full_name": "Asha Kumar"},
        )
        assert r.status_code == 200
        assert r.json()["full_name"] == "Asha Kumar"
        with env.db() as db:
            rows = list(
                db.execute(
                    select(AuditEvent).where(AuditEvent.action == "customer.update")
                ).scalars().all()
            )
        assert len(rows) == 1
        assert rows[0].target_label == "shopper@example.com"

    def test_password_reset_emails_and_never_returns_the_code(self, env: Env):
        env.act_as("support")
        r = env.client.post(
            f"/api/v1/customers/{env.users['shopper']}/password-reset"
        )
        assert r.status_code == 202
        assert len(env.sent_emails) == 1
        assert env.sent_emails[0]["to"] == "shopper@example.com"
        # The OTP is six digits; none of it may appear in the response.
        assert not any(ch.isdigit() for ch in r.json()["detail"])


# ---------------------------------------------------------------------------
# 3. The sensitive tier
# ---------------------------------------------------------------------------


def _add_snapshot(env: Env, user_id: int, **kw) -> date:
    """Plant one `agg_customer_snapshot` row, plus the active tz generation it
    hangs off.

    BigInteger primary keys do not auto-increment on SQLite, so ids are explicit
    here. `_next_id` keeps them unique across repeated calls in a test.
    """
    bucket = date.today() - timedelta(days=1)
    with env.db() as db:
        if db.execute(
            select(AnalyticsTzGeneration).where(AnalyticsTzGeneration.generation == 1)
        ).scalar_one_or_none() is None:
            db.add(
                AnalyticsTzGeneration(
                    id=1,
                    generation=1,
                    timezone="Asia/Kolkata",
                    effective_from=datetime.now(timezone.utc),
                    status="active",
                )
            )
            db.flush()
        db.add(
            AggCustomerSnapshot(
                id=user_id * 100 + 1,
                bucket_date=bucket,
                tz_generation=1,
                customer_key=f"u{user_id}",
                user_id=user_id,
                orders_count=kw.get("orders_count", 3),
                units=4,
                gross_ltv=kw.get("gross_ltv", 4500),
                net_ltv=4200,
                margin_ltv=1200,
                aov=1500,
                recency_days=7,
                frequency=3,
                monetary=4500,
                r_score=4,
                f_score=3,
                m_score=5,
                rfm_segment=kw.get("rfm_segment", "champions"),
                cohort_month="2026-05",
                tenure_days=90,
                is_active=True,
                churn_risk_band="low",
                preferred_payment_method="upi",
                quality="complete",
            )
        )
        db.commit()
    return bucket


class TestMoneyVisibility:
    def test_money_is_hidden_without_the_sensitive_permission(self, env: Env):
        _add_snapshot(env, env.users["shopper"])
        env.act_as("support")  # customers.view but NOT analytics.customers.view
        r = env.client.get("/api/v1/customers?page_size=100")
        assert r.status_code == 200
        body = r.json()
        assert body["money_visible"] is False
        row = next(c for c in body["items"] if c["email"] == "shopper@example.com")
        snap = row["snapshot"]
        # The non-money analytics stay — ops still needs them to do the job.
        assert snap["orders_count"] == 3
        assert snap["rfm_segment"] == "champions"
        # The money does not.
        assert snap["gross_ltv"] is None
        assert snap["net_ltv"] is None
        assert snap["margin_ltv"] is None
        assert snap["aov"] is None
        assert snap["monetary"] is None

    def test_money_is_visible_with_the_sensitive_permission(self, env: Env):
        _add_snapshot(env, env.users["shopper"])
        env.act_as("analyst")
        r = env.client.get("/api/v1/customers?page_size=100")
        body = r.json()
        assert body["money_visible"] is True
        row = next(c for c in body["items"] if c["email"] == "shopper@example.com")
        assert float(row["snapshot"]["gross_ltv"]) == 4500.0

    def test_snapshot_date_is_reported(self, env: Env):
        """A rollup figure must arrive labelled with the day it was computed,
        never bare — bare reads as "now", which it is not."""
        bucket = _add_snapshot(env, env.users["shopper"])
        env.act_as("analyst")
        r = env.client.get("/api/v1/customers?page_size=100")
        assert r.json()["snapshot_date"] == bucket.isoformat()

    def test_sorting_by_spend_is_refused_without_the_permission(self, env: Env):
        """Ranking by spend discloses spend. A caller who cannot read the money
        columns must not be able to order by them either — otherwise the row
        ORDER hands over the relative figures the nulled columns withhold."""
        _add_snapshot(env, env.users["shopper"], gross_ltv=9000)
        _add_snapshot(env, env.users["role_shopper"], gross_ltv=100)

        # Ops asks for highest-spend-first; it must NOT get spend order.
        env.act_as("support")
        r = env.client.get("/api/v1/customers?sort=ltv&page_size=100")
        assert r.status_code == 200
        ops_order = [c["email"] for c in r.json()["items"]]

        # The analyst asking the same question does get spend order.
        env.act_as("analyst")
        r = env.client.get("/api/v1/customers?sort=ltv&page_size=100")
        analyst_order = [c["email"] for c in r.json()["items"]]
        assert analyst_order[0] == "shopper@example.com"  # the ₹9000 customer

        # Ops fell back to the default (newest first = descending id).
        env.act_as("support")
        default_order = [
            c["email"]
            for c in env.client.get("/api/v1/customers?page_size=100").json()["items"]
        ]
        assert ops_order == default_order

    def test_customer_without_snapshot_has_none(self, env: Env):
        """Registered but never ordered: no snapshot row, so `snapshot` is null
        rather than a row of zeros. "No data" and "zero" differ."""
        _add_snapshot(env, env.users["shopper"])
        env.act_as("analyst")
        r = env.client.get("/api/v1/customers?page_size=100")
        row = next(c for c in r.json()["items"] if c["email"] == "roled@example.com")
        assert row["snapshot"] is None


class TestSegmentAndOrderFilters:
    def test_segment_filter(self, env: Env):
        _add_snapshot(env, env.users["shopper"], rfm_segment="champions")
        env.act_as("analyst")
        r = env.client.get("/api/v1/customers?segment=champions&page_size=100")
        assert [c["email"] for c in r.json()["items"]] == ["shopper@example.com"]
        assert r.json()["total"] == 1

        r = env.client.get("/api/v1/customers?segment=at_risk&page_size=100")
        assert r.json()["items"] == []
        assert r.json()["total"] == 0

    def test_never_ordered_filter_finds_the_snapshotless(self, env: Env):
        _add_snapshot(env, env.users["shopper"])
        env.act_as("analyst")
        r = env.client.get("/api/v1/customers?has_ordered=false&page_size=100")
        assert [c["email"] for c in r.json()["items"]] == ["roled@example.com"]
        assert r.json()["total"] == 1


class TestSortCompilesOnMySQL:
    """The suite runs on SQLite; production is MySQL. Anything SQLite accepts
    and MySQL rejects passes every test and then 500s live.

    That already happened once: the "sort by spend / orders / last order" keys
    used `.nullslast()`, which SQLite accepts and MySQL rejects with a 1064
    syntax error. This compiles the real statement against the MySQL dialect so
    the next such divergence fails here instead of in production.
    """

    @pytest.mark.parametrize("sort", ["recent", "oldest", "email", "ltv", "orders", "last_order"])
    def test_every_sort_compiles_under_the_mysql_dialect(self, env: Env, sort: str):
        from sqlalchemy.dialects import mysql

        from app.repositories.user_repository import UserRepository

        with env.db() as db:
            repo = UserRepository(db)
            # Reach the statement the same way the endpoint does, then compile
            # it for MySQL rather than executing it.
            captured = {}
            real_execute = db.execute

            def capture(stmt, *a, **kw):
                captured.setdefault("stmts", []).append(stmt)
                return real_execute(stmt, *a, **kw)

            db.execute = capture  # type: ignore[method-assign]
            try:
                repo.search_customers(sort=sort, limit=5)
            finally:
                db.execute = real_execute  # type: ignore[method-assign]

        for stmt in captured.get("stmts", []):
            sql = str(
                stmt.compile(
                    dialect=mysql.dialect(),
                    compile_kwargs={"literal_binds": False},
                )
            )
            assert "NULLS LAST" not in sql.upper(), (
                f"sort={sort!r} compiles to MySQL-invalid SQL: {sql}"
            )


# ---------------------------------------------------------------------------
# 4. Activity timeline
# ---------------------------------------------------------------------------


class TestActivityTimeline:
    def test_orders_and_admin_actions_appear(self, env: Env):
        with env.db() as db:
            db.add(
                Order(
                    order_number="WV-1001",
                    user_id=env.users["shopper"],
                    subtotal=1000,
                    tax_amount=50,
                    discount_amount=0,
                    shipping_amount=0,
                    total_amount=1050,
                    currency="INR",
                )
            )
            db.commit()

        # A staff write on the account should also land on the timeline.
        env.act_as("support")
        env.client.patch(
            f"/api/v1/customers/{env.users['shopper']}", json={"full_name": "Asha K"}
        )

        r = env.client.get(f"/api/v1/customers/{env.users['shopper']}/activity")
        assert r.status_code == 200
        kinds = {e["kind"] for e in r.json()["items"]}
        assert "order" in kinds
        assert "admin_action" in kinds

        order_event = next(e for e in r.json()["items"] if e["kind"] == "order")
        assert "WV-1001" in order_event["title"]

    def test_order_amounts_are_hidden_without_a_money_permission(self, env: Env):
        """Four order totals on a feed sum to roughly the lifetime spend the
        directory withholds, so the amount is gated the same way. `support`
        holds neither analytics.customers.view nor orders.view_all."""
        with env.db() as db:
            db.add(
                Order(
                    order_number="WV-3001",
                    user_id=env.users["shopper"],
                    subtotal=1000,
                    tax_amount=0,
                    discount_amount=0,
                    shipping_amount=0,
                    total_amount=1000,
                    currency="INR",
                )
            )
            db.commit()

        env.act_as("support")
        events = env.client.get(
            f"/api/v1/customers/{env.users['shopper']}/activity?kinds=order"
        ).json()["items"]
        assert events, "the order should still appear — only the amount is gated"
        assert "1000" not in (events[0]["detail"] or "")
        assert "INR" not in (events[0]["detail"] or "")
        # The event itself is intact: staff still see that an order happened.
        assert "WV-3001" in events[0]["title"]

        # The analyst tier does see the amount.
        env.act_as("analyst")
        events = env.client.get(
            f"/api/v1/customers/{env.users['shopper']}/activity?kinds=order"
        ).json()["items"]
        assert "1000" in events[0]["detail"]

    def test_kind_filter(self, env: Env):
        with env.db() as db:
            db.add(
                Order(
                    order_number="WV-1002",
                    user_id=env.users["shopper"],
                    subtotal=500,
                    tax_amount=0,
                    discount_amount=0,
                    shipping_amount=0,
                    total_amount=500,
                    currency="INR",
                )
            )
            db.commit()
        env.act_as("support")
        r = env.client.get(
            f"/api/v1/customers/{env.users['shopper']}/activity?kinds=order"
        )
        assert {e["kind"] for e in r.json()["items"]} == {"order"}

    def test_counters_match_the_timeline(self, env: Env):
        with env.db() as db:
            for n in (1, 2):
                db.add(
                    Order(
                        order_number=f"WV-200{n}",
                        user_id=env.users["shopper"],
                        subtotal=100,
                        tax_amount=0,
                        discount_amount=0,
                        shipping_amount=0,
                        total_amount=100,
                        currency="INR",
                    )
                )
            db.commit()
        env.act_as("support")
        detail = env.client.get(f"/api/v1/customers/{env.users['shopper']}").json()
        assert detail["activity_counts"]["order"] == 2


# ---------------------------------------------------------------------------
# 5. Staff invite
# ---------------------------------------------------------------------------


class TestStaffInvite:
    def test_invite_creates_account_emails_code_and_audits(self, env: Env):
        env.act_as("superadmin")
        r = env.client.post(
            "/api/v1/users/invite",
            json={
                "email": "newstaff@example.com",
                "full_name": "New Staff",
                "role_ids": [env.roles["support"]],
            },
        )
        assert r.status_code == 201
        assert r.json()["email"] == "newstaff@example.com"

        with env.db() as db:
            user = db.execute(
                select(User).where(User.email == "newstaff@example.com")
            ).scalar_one()
            assert user.is_active is True
            assert {role.name for role in user.roles} == {"support"}
            # The profile satellite is created eagerly, like registration does.
            assert user.full_name == "New Staff"
            audits = list(
                db.execute(
                    select(AuditEvent).where(AuditEvent.action == "user.invite")
                ).scalars().all()
            )
        assert len(audits) == 1
        assert [m["to"] for m in env.sent_emails] == ["newstaff@example.com"]

    def test_invited_account_appears_in_the_staff_scope(self, env: Env):
        env.act_as("superadmin")
        env.client.post(
            "/api/v1/users/invite",
            json={"email": "newstaff@example.com", "role_ids": [env.roles["support"]]},
        )
        r = env.client.get("/api/v1/users?scope=staff&page_size=100")
        assert "newstaff@example.com" in {u["email"] for u in r.json()["items"]}

    def test_invite_cannot_escalate(self, env: Env):
        """The same guard as a later role assignment — you cannot conjure an
        account more powerful than yourself."""
        env.act_as("support")
        r = env.client.post(
            "/api/v1/users/invite",
            json={
                "email": "escalated@example.com",
                "role_ids": [env.roles["privileged"]],
            },
        )
        assert r.status_code == 403
        with env.db() as db:
            assert (
                db.execute(
                    select(User).where(User.email == "escalated@example.com")
                ).scalar_one_or_none()
                is None
            )

    def test_invite_requires_the_permission(self, env: Env):
        env.act_as("analyst")  # customers.view + analytics, no users.invite
        r = env.client.post(
            "/api/v1/users/invite", json={"email": "nope@example.com"}
        )
        assert r.status_code == 403
        assert "users.invite" in r.json()["error"]["message"]

    def test_duplicate_email_is_a_conflict(self, env: Env):
        env.act_as("superadmin")
        r = env.client.post(
            "/api/v1/users/invite", json={"email": "shopper@example.com"}
        )
        assert r.status_code == 409
