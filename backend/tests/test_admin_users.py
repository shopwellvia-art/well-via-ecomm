"""Admin user-management endpoint tests (PATCH /users/{id} and
POST /users/{id}/password-reset).

Hermetic — in-memory SQLite + an in-process fake Redis, so no live MySQL or
Redis is needed. The users router is mounted on a purpose-built FastAPI app
with `get_db` / `get_current_user` overridden, which exercises the real
`require_permission` checker, the real AuthService admin functions, the real
SessionService revocation bookkeeping, and the real error envelopes.

Run anywhere:

    ENVIRONMENT=test MYSQL_HOST=localhost pytest tests/test_admin_users.py -v

(or inside the backend container, where the conftest DB guard is satisfied
the same way.)

Covers:
- 403 without users.manage on both endpoints (permission enforcement)
- PATCH full_name (customer satellite split) without touching sessions
- PATCH is_active=false revokes every session + stamps the revoked_after
  marker; is_active=true restores every login gate
- POST password-reset sends via the email backend and never leaks the code
- superadmin (is_admin) targets are refused for non-superadmin staff
- audit rows land for both writes
"""
from __future__ import annotations

import re

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — register every table on Base.metadata
from app.api.deps import get_current_user, get_db
from app.api.v1.endpoints.users import router as users_router
from app.core.exceptions import register_exception_handlers
from app.core.security import hash_password
from app.models.audit import AuditEvent
from app.models.base import Base
from app.models.customer import AccountStatus, Customer
from app.models.rbac import Permission, Role
from app.models.user import User


# ---------------------------------------------------------------------------
# Fake Redis — just enough surface for SessionService, RateLimiter and the
# OTP store. Mirrors decode_responses=True (everything is str).
# ---------------------------------------------------------------------------


class FakePipeline:
    def __init__(self, parent: "FakeRedis"):
        self.parent = parent
        self.ops: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name):
        def queue(*args, **kwargs):
            self.ops.append((name, args, kwargs))
            return self

        return queue

    def execute(self):
        return [getattr(self.parent, op)(*args, **kwargs) for op, args, kwargs in self.ops]


class FakeRedis:
    def __init__(self):
        self.kv: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.sets: dict[str, set[str]] = {}

    # strings
    def get(self, key):
        return self.kv.get(key)

    def setex(self, key, ttl, value):
        self.kv[key] = str(value)
        return True

    def incr(self, key):
        value = int(self.kv.get(key, "0")) + 1
        self.kv[key] = str(value)
        return value

    def expire(self, key, ttl):
        return True

    def ttl(self, key):
        return 60

    def exists(self, key):
        return int(key in self.kv or key in self.hashes or key in self.sets)

    def delete(self, *keys):
        n = 0
        for key in keys:
            n += int(key in self.kv or key in self.hashes or key in self.sets)
            self.kv.pop(key, None)
            self.hashes.pop(key, None)
            self.sets.pop(key, None)
        return n

    # hashes
    def hset(self, key, field=None, value=None, mapping=None):
        h = self.hashes.setdefault(key, {})
        if mapping:
            h.update({str(k): str(v) for k, v in mapping.items()})
        if field is not None:
            h[str(field)] = str(value)
        return 1

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    # sets
    def sadd(self, key, *members):
        s = self.sets.setdefault(key, set())
        s.update(str(m) for m in members)
        return len(members)

    def srem(self, key, *members):
        s = self.sets.get(key, set())
        n = 0
        for m in members:
            if str(m) in s:
                s.discard(str(m))
                n += 1
        return n

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def scard(self, key):
        return len(self.sets.get(key, set()))

    def pipeline(self):
        return FakePipeline(self)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class Env:
    """Everything a test needs: the client, the DB factory, the fake redis,
    the seeded user ids, the captured outbound emails, and an actor switch."""

    def __init__(self, client, db_factory, fake_redis, sent_emails, users):
        self.client = client
        self.db = db_factory
        self.redis = fake_redis
        self.sent_emails = sent_emails
        self.users = users  # name -> id
        self._actor = {"id": users["superadmin"]}

    def act_as(self, name: str) -> None:
        self._actor["id"] = self.users[name]

    @property
    def actor_holder(self):
        return self._actor

    def seed_session(self, user_id: int, family_id: str = "fam1") -> None:
        """Plant a live refresh-token family for a user, the same shape
        SessionService.start_family writes."""
        self.redis.sadd(f"auth:user:{user_id}:families", family_id)
        self.redis.hset(
            f"auth:family:{family_id}",
            mapping={"user_id": str(user_id), "jti": "jti-1"},
        )


def _seed_users(db: Session) -> dict[str, int]:
    manage = Permission(
        name="users.manage",
        description="Edit user accounts",
        group_name="Users",
    )
    support = Role(
        name="support", description="Support staff", is_system=False, permissions=[manage]
    )
    db.add_all([manage, support])

    def mk(email: str, *, is_admin: bool = False, roles: list[Role] | None = None,
           first: str | None = None, last: str | None = None) -> User:
        u = User(
            email=email,
            hashed_password=hash_password("TestPass123!"),
            is_active=True,
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
                account_status=AccountStatus.ACTIVE,
            )
        )
        return u

    superadmin = mk("root@example.com", is_admin=True, first="Root")
    other_admin = mk("root2@example.com", is_admin=True, first="Root", last="Two")
    staff = mk("staff@example.com", roles=[support], first="Staff")
    nobody = mk("nobody@example.com", first="No", last="Perms")
    shopper = mk("shopper@example.com", first="Old", last="Name")
    db.commit()
    return {
        "superadmin": superadmin.id,
        "other_admin": other_admin.id,
        "staff": staff.id,
        "nobody": nobody.id,
        "shopper": shopper.id,
    }


@pytest.fixture
def env(monkeypatch) -> Env:
    fake = FakeRedis()

    # Every module that bound `get_redis` at import time gets the fake, so no
    # code path can reach a real Redis.
    for module in (
        "app.db.redis",
        "app.services.auth_service",
        "app.services.session_service",
        "app.services.settings_service",
        "app.core.rate_limit",
    ):
        monkeypatch.setattr(f"{module}.get_redis", lambda fake=fake: fake)

    # Capture outbound email at the same seam AuthService uses.
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
        users = _seed_users(db)

    test_app = FastAPI()
    register_exception_handlers(test_app)
    test_app.include_router(users_router, prefix="/api/v1/users")

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

    e = Env(TestClient(test_app), db_factory, fake, sent, users)
    e._actor = holder
    return e


def _audit_rows(env: Env, action: str) -> list[AuditEvent]:
    with env.db() as db:
        return list(
            db.execute(select(AuditEvent).where(AuditEvent.action == action))
            .scalars()
            .all()
        )


# ---------------------------------------------------------------------------
# Permission enforcement
# ---------------------------------------------------------------------------


class TestPermissionEnforcement:
    def test_patch_requires_users_manage(self, env: Env):
        env.act_as("nobody")
        r = env.client.patch(
            f"/api/v1/users/{env.users['shopper']}", json={"full_name": "X"}
        )
        assert r.status_code == 403
        body = r.json()
        assert body["error"]["code"] == "forbidden"
        assert "users.manage" in body["error"]["message"]
        assert _audit_rows(env, "user.update") == []

    def test_password_reset_requires_users_manage(self, env: Env):
        env.act_as("nobody")
        r = env.client.post(f"/api/v1/users/{env.users['shopper']}/password-reset")
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "forbidden"
        assert env.sent_emails == []

    def test_unknown_target_is_404(self, env: Env):
        env.act_as("staff")
        r = env.client.patch("/api/v1/users/999999", json={"full_name": "X"})
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"


# ---------------------------------------------------------------------------
# PATCH /users/{id}
# ---------------------------------------------------------------------------


class TestUpdateUser:
    def test_staff_with_perm_updates_full_name(self, env: Env):
        env.act_as("staff")
        env.seed_session(env.users["shopper"])

        r = env.client.patch(
            f"/api/v1/users/{env.users['shopper']}",
            json={"full_name": "New Name"},
        )
        assert r.status_code == 200
        assert r.json()["full_name"] == "New Name"
        assert r.json()["is_active"] is True

        # Name is split onto the customer satellite like every other writer.
        with env.db() as db:
            customer = db.execute(
                select(Customer).where(Customer.user_id == env.users["shopper"])
            ).scalar_one()
            assert (customer.first_name, customer.last_name) == ("New", "Name")

        # A pure profile edit must NOT log the user out.
        assert env.redis.scard(f"auth:user:{env.users['shopper']}:families") == 1
        assert env.redis.get(f"auth:revoked_after:{env.users['shopper']}") is None

        rows = _audit_rows(env, "user.update")
        assert len(rows) == 1
        assert rows[0].target_id == env.users["shopper"]
        assert "full_name" in rows[0].extra["changes"]

    def test_deactivate_revokes_all_sessions(self, env: Env):
        env.act_as("staff")
        uid = env.users["shopper"]
        env.seed_session(uid, "fam1")
        env.seed_session(uid, "fam2")

        r = env.client.patch(f"/api/v1/users/{uid}", json={"is_active": False})
        assert r.status_code == 200
        assert r.json()["is_active"] is False

        # Sessions are gone: family hashes wiped, user set emptied, and the
        # revoked_after marker set so outstanding access tokens die too.
        assert env.redis.hgetall("auth:family:fam1") == {}
        assert env.redis.hgetall("auth:family:fam2") == {}
        assert env.redis.smembers(f"auth:user:{uid}:families") == set()
        assert env.redis.get(f"auth:revoked_after:{uid}") is not None

        with env.db() as db:
            user = db.get(User, uid)
            assert user.is_active is False
            customer = db.execute(
                select(Customer).where(Customer.user_id == uid)
            ).scalar_one()
            assert customer.account_status == AccountStatus.DEACTIVATED
            assert customer.deactivated_at is not None

        rows = _audit_rows(env, "user.update")
        assert len(rows) == 1
        assert rows[0].extra["changes"]["is_active"] == {"before": True, "after": False}
        assert rows[0].extra["changes"]["sessions_revoked"] == 2

    def test_reactivate_restores_login_gates(self, env: Env):
        env.act_as("staff")
        uid = env.users["shopper"]
        env.client.patch(f"/api/v1/users/{uid}", json={"is_active": False})

        r = env.client.patch(f"/api/v1/users/{uid}", json={"is_active": True})
        assert r.status_code == 200
        assert r.json()["is_active"] is True
        with env.db() as db:
            user = db.get(User, uid)
            assert user.is_active is True
            customer = db.execute(
                select(Customer).where(Customer.user_id == uid)
            ).scalar_one()
            assert customer.account_status == AccountStatus.ACTIVE
            assert customer.deactivated_at is None


# ---------------------------------------------------------------------------
# POST /users/{id}/password-reset
# ---------------------------------------------------------------------------


class TestAdminPasswordReset:
    def test_sends_email_and_never_returns_the_code(self, env: Env):
        env.act_as("staff")
        uid = env.users["shopper"]

        r = env.client.post(f"/api/v1/users/{uid}/password-reset")
        assert r.status_code == 202
        # The ack carries a detail message and nothing else.
        assert set(r.json()) == {"detail"}

        # Exactly one email, to the target, through the email backend seam.
        assert len(env.sent_emails) == 1
        mail = env.sent_emails[0]
        assert mail["to"] == "shopper@example.com"

        # The 6-digit code is in the email...
        code = re.search(r"\b(\d{6})\b", mail["body"]).group(1)
        # ...but never in the HTTP response...
        assert code not in r.text
        # ...and only its hash is stored (existing forgot-password machinery).
        stored = env.redis.get("otp:reset:shopper@example.com")
        assert stored is not None
        assert code not in stored

        rows = _audit_rows(env, "user.password_reset")
        assert len(rows) == 1
        assert rows[0].target_id == uid

    def test_audited_with_actor(self, env: Env):
        env.act_as("staff")
        env.client.post(f"/api/v1/users/{env.users['shopper']}/password-reset")
        row = _audit_rows(env, "user.password_reset")[0]
        assert row.actor_email == "staff@example.com"


# ---------------------------------------------------------------------------
# Superadmin protection
# ---------------------------------------------------------------------------


class TestSuperadminProtection:
    def test_non_superadmin_cannot_edit_superadmin(self, env: Env):
        env.act_as("staff")  # has users.manage but is_admin=False
        r = env.client.patch(
            f"/api/v1/users/{env.users['other_admin']}",
            json={"is_active": False},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "forbidden"
        with env.db() as db:
            assert db.get(User, env.users["other_admin"]).is_active is True

    def test_non_superadmin_cannot_reset_superadmin_password(self, env: Env):
        env.act_as("staff")
        r = env.client.post(
            f"/api/v1/users/{env.users['other_admin']}/password-reset"
        )
        assert r.status_code == 403
        assert env.sent_emails == []

    def test_superadmin_can_edit_superadmin(self, env: Env):
        env.act_as("superadmin")
        r = env.client.patch(
            f"/api/v1/users/{env.users['other_admin']}",
            json={"full_name": "Second Root"},
        )
        assert r.status_code == 200
        assert r.json()["full_name"] == "Second Root"
