"""The GA4 purchase-delivery deliverability guard.

The defect this file locks shut: `analytics.ga4_purchase_delivery` in a server
mode (``server`` / ``both``) while no Measurement Protocol api_secret is saved.
In that state every purchase event is written to the outbox and can never
leave it — `outbox.drain` idles with reason ``no_api_secret`` — so GA4 shows
zero ecommerce revenue while the queue grows silently. The tracking-health
probe WARNS about the state (``ga4_server_delivery_without_secret``); as of
this change the save path also REFUSES to create it, because warning about a
state you could have refused is second best.

The properties under test, in order of importance:

1. A save that would create the state is rejected with a structured 422 that
   names what is missing and both ways out — and persists nothing.
2. The mirror: clearing the secret while delivery is a server mode is rejected
   the same way, and the stored credential survives.
3. Every escape hatch stays open. Secret and mode may arrive in one request
   (no forced ordering), browser-only never needs the secret, and — the
   non-bricking property — a deployment ALREADY in the broken state can save
   its way out in both directions and is not trapped on unrelated saves.
4. No secret material appears in any log record or response body on the
   rejection path.
5. Defence in depth: the tracking-health warning still fires for a deployment
   whose rows are already broken. Its state is seeded with raw row writes on
   purpose — the API now refuses to create it, but production deployments
   configured before the guard existed are in it right now.

Run inside the analytics container:

    docker exec wvana-py python -m pytest tests/test_analytics_delivery_guard.py -q
"""
from __future__ import annotations

import logging
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.api.v1.endpoints import analytics_integrations
from app.core import config as _config
from app.core.crypto import decrypt_secret
from app.core.exceptions import register_exception_handlers
from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal
from app.models.audit import AuditEvent
from app.models.system_setting import SystemSetting
from app.services.analytics import integrations as integrations_service

API_PREFIX = "/api/v1/analytics"
MANAGE = analytics_integrations.INTEGRATIONS_MANAGE_PERMISSION

DELIVERY_KEY = "analytics.ga4_purchase_delivery"
SECRET_KEY = "analytics.ga4_api_secret"
WARNING_CODE = "ga4_server_delivery_without_secret"

#: Distinctive plaintext: if it turns up in a log line or a response body,
#: `assert SECRET not in ...` says exactly what leaked.
SECRET = "wellvia-mp-secret-guard-DO-NOT-LEAK-77c1"

VALID_GA4 = "G-GUARD12345"
VALID_GTM = "GTM-GUARD01"


# ---------------------------------------------------------------------------
# App / fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def app() -> FastAPI:
    """A minimal app carrying the integrations router.

    `register_exception_handlers` is load-bearing: without it the guard's
    `UndeliverableConfiguration` surfaces as an unhandled 500 and every 422
    assertion below is meaningless.
    """
    test_app = FastAPI()
    register_exception_handlers(test_app)
    test_app.include_router(analytics_integrations.router, prefix=API_PREFIX)
    return test_app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _no_rate_limit():
    original = _config.settings.RATE_LIMIT_ENABLED
    _config.settings.RATE_LIMIT_ENABLED = False
    try:
        yield
    finally:
        _config.settings.RATE_LIMIT_ENABLED = original


@pytest.fixture(autouse=True)
def _clean_rows():
    """Purge this module's settings + audit rows before and after each test.

    Before as well as after: a run that died between a write and its teardown
    would otherwise leave a configured secret behind and turn a "secret is
    absent" assertion into a false pass.
    """
    _purge()
    try:
        yield
    finally:
        _purge()


def _purge() -> None:
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM system_settings WHERE `key` LIKE 'analytics.%'"))
        db.execute(
            text("DELETE FROM audit_events WHERE action = 'analytics.integrations.update'")
        )
        db.commit()
    finally:
        db.close()
    try:
        from app.db.redis import get_redis

        redis_client = get_redis()
        for key in integrations_service.FIELD_BY_KEY:
            redis_client.delete(f"settings:{key}")
    except Exception:  # noqa: BLE001 — cache cleanup must never fail a test
        pass


class _Actor:
    """A disposable NON-admin user holding exactly `analytics.integrations.manage`.

    Non-admin on purpose: `User.has_permission` short-circuits True for admins,
    so an admin account would never exercise the permission dependency.
    """

    def __init__(self) -> None:
        from app.models.rbac import Permission, Role
        from app.models.user import User

        self.role_ids: list[int] = []
        self.permission_ids: list[int] = []
        uid = uuid.uuid4().hex[:8]

        db = SessionLocal()
        try:
            user = User(
                email=f"delivery-guard-{uid}@example.com",
                hashed_password=hash_password("TestPass123!"),
                is_active=True,
                is_admin=False,
            )
            db.add(user)
            db.flush()

            role = Role(
                name=f"delivery-guard-{uid}", description="test role", is_system=False
            )
            perm = db.query(Permission).filter(Permission.name == MANAGE).first()
            if perm is None:
                perm = Permission(
                    name=MANAGE, description="test-created", group_name="Analytics"
                )
                db.add(perm)
                db.flush()
                self.permission_ids.append(perm.id)
            role.permissions.append(perm)
            db.add(role)
            db.flush()
            self.role_ids.append(role.id)
            user.roles.append(role)
            db.commit()
            self.id = user.id
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
                text("DELETE FROM user_roles WHERE user_id = :uid"), {"uid": self.id}
            )
            for role_id in self.role_ids:
                db.execute(
                    text("DELETE FROM role_permissions WHERE role_id = :rid"),
                    {"rid": role_id},
                )
                db.execute(text("DELETE FROM roles WHERE id = :rid"), {"rid": role_id})
            for perm_id in self.permission_ids:
                db.execute(
                    text("DELETE FROM permissions WHERE id = :pid"), {"pid": perm_id}
                )
            db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": self.id})
            db.commit()
        finally:
            db.close()


@pytest.fixture()
def manager():
    actor = _Actor()
    try:
        yield actor
    finally:
        actor.cleanup()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _put(client: TestClient, actor: _Actor, updates: dict):
    return client.put(
        f"{API_PREFIX}/integrations", json={"updates": updates}, headers=actor.headers
    )


def _row_value(key: str) -> str:
    db = SessionLocal()
    try:
        row = db.execute(
            select(SystemSetting).where(SystemSetting.key == key)
        ).scalar_one_or_none()
        return (row.value if row else "") or ""
    finally:
        db.close()


def _snapshot() -> dict[str, str]:
    """Every stored analytics.* value, for whole-state "nothing changed" asserts."""
    db = SessionLocal()
    try:
        rows = (
            db.execute(
                select(SystemSetting).where(SystemSetting.key.like("analytics.%"))
            )
            .scalars()
            .all()
        )
        return {r.key: (r.value or "") for r in rows}
    finally:
        db.close()


def _audit_count() -> int:
    db = SessionLocal()
    try:
        return (
            db.query(AuditEvent)
            .filter(AuditEvent.action == "analytics.integrations.update")
            .count()
        )
    finally:
        db.close()


def _ensure_rows() -> None:
    db = SessionLocal()
    try:
        integrations_service.ensure_rows(db)
    finally:
        db.close()


def _seed_broken_state(delivery: str = "server") -> None:
    """Mirror the production defect with raw row writes: delivery in a server
    mode, GA4 on, and no stored secret. Deliberately NOT via the API — the API
    now refuses to create this state, and these rows stand in for a deployment
    configured before the guard existed.
    """
    db = SessionLocal()
    try:
        integrations_service.ensure_rows(db)
        db.execute(
            text("UPDATE system_settings SET value = :v WHERE `key` = :k"),
            [
                {"k": "analytics.ga4_enabled", "v": "true"},
                {"k": "analytics.ga4_measurement_id", "v": VALID_GA4},
                {"k": DELIVERY_KEY, "v": delivery},
                {"k": SECRET_KEY, "v": ""},
            ],
        )
        db.commit()
    finally:
        db.close()


def _guard_error(resp) -> dict:
    assert resp.status_code == 422, resp.text
    body = resp.json()["error"]
    assert body["code"] == WARNING_CODE, body
    return body


# ===========================================================================
# 1. Rejects creating the broken state
# ===========================================================================
class TestRejectsCreatingTheBrokenState:
    @pytest.mark.parametrize("mode", ["server", "both"])
    def test_server_modes_without_a_secret_are_rejected_and_nothing_persists(
        self, client: TestClient, manager: _Actor, mode: str
    ) -> None:
        """Both server-involving modes need the secret — `outbox.drain` is the
        only sender in `server` mode and one of the two in `both` — and the
        rejection must name what is missing and both ways out, while the
        stored settings stay byte-for-byte what they were."""
        _ensure_rows()
        before = _snapshot()

        error = _guard_error(_put(client, manager, {DELIVERY_KEY: mode}))

        assert error["details"]["missing"] == SECRET_KEY
        assert error["details"]["purchase_delivery"] == mode
        assert error["details"]["api_secret_state"] == "unset"
        fixes = error["details"]["fixes"]
        assert len(fixes) == 2
        assert any("Measurement Protocol API secrets" in fix for fix in fixes)
        assert any("browser" in fix for fix in fixes)

        assert _snapshot() == before, "a rejected save must persist nothing"
        assert _audit_count() == 0, "a rejected save must audit nothing"

    def test_clearing_the_delivery_mode_falls_back_to_server_and_is_rejected(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """An empty value resolves to the field default — which is `server` —
        so "unset it" is not a loophole around the guard."""
        _ensure_rows()
        error = _guard_error(_put(client, manager, {DELIVERY_KEY: ""}))
        assert error["details"]["purchase_delivery"] == "server"

    def test_clearing_the_secret_while_server_mode_is_rejected_and_the_secret_survives(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """The mirror image. Revoking the credential while the server still
        owns purchase delivery would break delivery exactly as surely as the
        forward direction — and the stored credential must survive the
        rejected attempt."""
        assert _put(
            client, manager, {SECRET_KEY: SECRET, DELIVERY_KEY: "server"}
        ).status_code == 200

        error = _guard_error(_put(client, manager, {SECRET_KEY: ""}))
        assert "remove" in error["message"].lower()
        assert error["details"]["missing"] == SECRET_KEY

        assert decrypt_secret(_row_value(SECRET_KEY)) == SECRET
        assert _row_value(DELIVERY_KEY) == "server"


# ===========================================================================
# 2. Every way out stays open
# ===========================================================================
class TestEveryWayOutStaysOpen:
    def test_secret_and_server_mode_may_arrive_in_one_request(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """No forced ordering: the operator pastes the secret and picks the
        mode on one screen, and one save must be enough."""
        _ensure_rows()
        resp = _put(
            client,
            manager,
            {
                SECRET_KEY: SECRET,
                DELIVERY_KEY: "both",
                "analytics.ga4_measurement_id": VALID_GA4,
            },
        )
        assert resp.status_code == 200, resp.text
        assert set(resp.json()["changed"]) >= {SECRET_KEY, DELIVERY_KEY}
        assert _row_value(DELIVERY_KEY) == "both"

    def test_browser_only_never_needs_the_secret(
        self, client: TestClient, manager: _Actor
    ) -> None:
        _ensure_rows()
        resp = _put(client, manager, {DELIVERY_KEY: "browser"})
        assert resp.status_code == 200, resp.text
        assert _row_value(DELIVERY_KEY) == "browser"

    def test_an_already_broken_deployment_can_switch_to_browser(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """THE non-bricking property, direction one. The stored state is
        already inconsistent; switching delivery to the browser is one of the
        two documented ways out and must not be caught by the guard."""
        _seed_broken_state()
        resp = _put(client, manager, {DELIVERY_KEY: "browser"})
        assert resp.status_code == 200, resp.text
        assert _row_value(DELIVERY_KEY) == "browser"

    def test_an_already_broken_deployment_can_add_the_secret(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """Direction two: pasting the secret while delivery stays `server`."""
        _seed_broken_state()
        resp = _put(client, manager, {SECRET_KEY: SECRET})
        assert resp.status_code == 200, resp.text
        db = SessionLocal()
        try:
            assert integrations_service.secret_state(db, SECRET_KEY) == "set"
        finally:
            db.close()
        assert _row_value(DELIVERY_KEY) == "server"

    def test_unrelated_saves_are_not_trapped_by_a_broken_state(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """A naive "reject any save while inconsistent" would lock the whole
        settings screen behind the GA4 defect. Only saves that touch the
        delivery/secret pair are the guard's business."""
        _seed_broken_state()
        resp = _put(client, manager, {"analytics.gtm_container_id": VALID_GTM})
        assert resp.status_code == 200, resp.text
        assert _row_value("analytics.gtm_container_id") == VALID_GTM

    def test_the_mask_keeps_a_stored_secret_usable_for_server_mode(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """The form round-trips `***` for an untouched secret; that must read
        as "still set", not "arriving empty"."""
        assert _put(
            client, manager, {SECRET_KEY: SECRET, DELIVERY_KEY: "server"}
        ).status_code == 200
        resp = _put(
            client,
            manager,
            {DELIVERY_KEY: "both", SECRET_KEY: integrations_service.REDACTED},
        )
        assert resp.status_code == 200, resp.text
        assert decrypt_secret(_row_value(SECRET_KEY)) == SECRET


# ===========================================================================
# 3. The rejection path leaks nothing
# ===========================================================================
class TestRejectionLeaksNoSecret:
    def test_no_secret_material_in_logs_or_body_when_clearing_is_rejected(
        self, client: TestClient, manager: _Actor, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The one rejection that happens while a real credential is stored.
        Neither the plaintext nor the stored Fernet ciphertext may appear in
        the response body or in any log record the rejection produced."""
        assert _put(
            client, manager, {SECRET_KEY: SECRET, DELIVERY_KEY: "server"}
        ).status_code == 200
        ciphertext = _row_value(SECRET_KEY)
        assert ciphertext and ciphertext != SECRET

        with caplog.at_level(logging.DEBUG):
            resp = _put(client, manager, {SECRET_KEY: ""})

        assert resp.status_code == 422
        assert SECRET not in resp.text
        assert ciphertext not in resp.text
        for record in caplog.records:
            line = record.getMessage()
            assert SECRET not in line
            assert ciphertext not in line
        assert SECRET not in caplog.text
        assert ciphertext not in caplog.text

    def test_no_secret_material_when_server_mode_is_rejected_with_the_mask(
        self, client: TestClient, manager: _Actor, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The forward rejection, with the UI's mask riding along. The response
        must carry key names and state words only — no value of any secret
        field, masked or otherwise, beyond the fact that it is unset."""
        _ensure_rows()
        with caplog.at_level(logging.DEBUG):
            resp = _put(
                client,
                manager,
                {DELIVERY_KEY: "server", SECRET_KEY: integrations_service.REDACTED},
            )

        error = _guard_error(resp)
        assert error["details"]["api_secret_state"] == "unset"
        assert SECRET not in resp.text
        assert SECRET not in caplog.text


# ===========================================================================
# 4. Defence in depth: the health warning still fires
# ===========================================================================
class TestWarningStillFires:
    def test_tracking_health_still_warns_for_a_deployment_already_in_the_state(
        self,
    ) -> None:
        """The guard prevents new instances; the warning covers the deployments
        that already exist. The broken rows are seeded raw and deliberately NOT
        repaired in this setup — the warning firing over genuinely broken rows
        is the entire point."""
        _seed_broken_state()
        db = SessionLocal()
        try:
            health = integrations_service.tracking_health(db)
        finally:
            db.close()

        codes = {w["code"] for w in health["warnings"]}
        assert WARNING_CODE in codes
        assert health["providers"]["ga4"]["server_delivery_ready"] is False
        assert health["providers"]["ga4"]["api_secret_state"] == "unset"
