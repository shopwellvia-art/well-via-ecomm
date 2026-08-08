"""Tests for the analytics integration settings API and the tracking-health probe.

What this file is actually defending
====================================
Most of these assertions exist because the failure they catch is **silent**.

* A leaked GA4 Measurement Protocol API secret is a write credential for the
  store's analytics property, and it leaks by being *rendered* — into an admin
  response, into `/settings/public`, or into a database column an operations
  engineer can read. Four separate tests close those doors, and the strongest
  of them asserts the literal plaintext is absent from the raw response body
  rather than checking a field it expects to be masked: a mask on the field the
  test knows about proves nothing about the field it does not.
* A "Test connection" button that goes green without checking anything is worse
  than no button, because it converts "we did not look" into "we looked and it
  is fine". `TestConnectionHonesty` asserts GTM and Clarity report
  `cannot_verify_server_side` with `verified: False`, and that they make no
  outbound request at all while doing so.
* A production deployment reporting into a staging GA4 property looks perfectly
  healthy from every other signal — the tag loads, events send, charts draw.
  Only the declared property environment can catch it.

Strategy
--------
**The router is not mounted on the real app yet** — wiring `api/v1/router.py`
is the lead's change and this module deliberately does not touch it. Every HTTP
test builds a *local* `FastAPI()`, registers the app-wide exception handlers (so
`AppError` subclasses render as their real status codes rather than 500s) and
mounts the router under the prefix the lead will use. `endpoints/settings.py` is
mounted alongside it so the `/settings/public` leak test exercises the real
anonymous endpoint rather than a re-implementation of it.

**No shared login account.** These tests create their own non-admin users,
roles and permission grants. Non-admin is load-bearing: `User.has_permission`
short-circuits `True` for `is_admin`, so an admin account would sail through
the dependency without ever exercising the grant the test is about.

**Nothing reaches the network.** The one provider with a real server-side check
(GA4) has its HTTP client patched; a test that depended on Google being up
would fail for reasons that have nothing to do with this code.

Run inside the analytics container:

    docker exec wvana-py python -m pytest tests/test_analytics_integrations.py -q
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.api.v1.endpoints import analytics_integrations, settings as settings_endpoint
from app.core import config as _config
from app.core.crypto import decrypt_secret
from app.core.exceptions import register_exception_handlers
from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal
from app.models.analytics_control import (
    AnalyticsEventOutbox,
    AnalyticsSyncRun,
    ConsentState,
    OutboxEventName,
    OutboxStatus,
    SyncStatus,
    SyncTrigger,
)
from app.models.audit import AuditEvent
from app.models.system_setting import SystemSetting
from app.services.analytics import integrations as integrations_service

API_PREFIX = "/api/v1/analytics"
SETTINGS_PREFIX = "/api/v1/settings"

MANAGE = analytics_integrations.INTEGRATIONS_MANAGE_PERMISSION
VIEW = analytics_integrations.CONTROL_CENTRE_VIEW_PERMISSION

#: A distinctive, unmistakable plaintext. If this string turns up anywhere it
#: should not, `assert SECRET not in resp.text` says so without ambiguity.
SECRET = "wellvia-mp-api-secret-DO-NOT-LEAK-9f3a"

VALID_GTM = "GTM-ABCD123"
VALID_GA4 = "G-ABCDE12345"
VALID_CLARITY = "abcd1234ef"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def app() -> FastAPI:
    """A minimal app carrying this router plus the real settings router.

    `register_exception_handlers` matters: without it the 400/403/422 raised as
    `AppError` subclasses surface as unhandled 500s and every status-code
    assertion below becomes meaningless.

    `settings_endpoint.router` is mounted because `TestPublicSettingsLeak` must
    hit the *real* `/settings/public` handler — the whole point of that test is
    that the curated anonymous allowlist does not grow a hole.
    """
    test_app = FastAPI()
    register_exception_handlers(test_app)
    test_app.include_router(analytics_integrations.router, prefix=API_PREFIX)
    test_app.include_router(settings_endpoint.router, prefix=SETTINGS_PREFIX)
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


@pytest.fixture(autouse=True)
def _clean_integration_rows():
    """Remove this module's settings + audit rows before and after each test.

    Before as well as after: a previous run that died between the write and the
    teardown would otherwise leave a configured secret behind and turn a
    "secret is absent" assertion into a false pass. `ensure_rows` recreates the
    schema on the next call, so deleting them is safe.
    """
    _purge()
    try:
        yield
    finally:
        _purge()


def _purge() -> None:
    with SessionLocal() as db:
        db.execute(
            text("DELETE FROM system_settings WHERE `key` LIKE 'analytics.%'")
        )
        db.execute(
            text("DELETE FROM audit_events WHERE action = 'analytics.integrations.update'")
        )
        db.commit()
    # The settings cache is keyed per settings key and lives 60s; a stale entry
    # would let a later read see a value whose row has been deleted.
    try:
        from app.db.redis import get_redis

        redis_client = get_redis()
        for key in integrations_service.FIELD_BY_KEY:
            redis_client.delete(f"settings:{key}")
    except Exception:  # noqa: BLE001 — cache cleanup must never fail a test
        pass


# ---------------------------------------------------------------------------
# Users / auth helpers
# ---------------------------------------------------------------------------
def _uid() -> str:
    return uuid.uuid4().hex[:8]


class _Actor:
    """A disposable non-admin user holding exactly the named permissions."""

    def __init__(self, *permissions: str) -> None:
        from app.models.rbac import Permission, Role
        from app.models.user import User

        self.role_ids: list[int] = []
        self.permission_ids: list[int] = []

        with SessionLocal() as db:
            user = User(
                email=f"analytics-integrations-{_uid()}@example.com",
                hashed_password=hash_password("TestPass123!"),
                is_active=True,
                # Non-admin on purpose: `User.has_permission` returns True for
                # any admin, so an admin account would never exercise the grant.
                is_admin=False,
            )
            db.add(user)
            db.flush()

            if permissions:
                role = Role(
                    name=f"analytics-integrations-{_uid()}",
                    description="test role",
                    is_system=False,
                )
                for name in permissions:
                    perm = (
                        db.query(Permission).filter(Permission.name == name).first()
                    )
                    if perm is None:
                        # Seeded on a normal deploy once the lead wires the RBAC
                        # registry; create it if this DB predates that, and
                        # remember to remove only what we created.
                        perm = Permission(
                            name=name,
                            description="test-created",
                            group_name="Analytics",
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

        self.token = create_access_token(self.id)

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def cleanup(self) -> None:
        with SessionLocal() as db:
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


@pytest.fixture()
def manager():
    actor = _Actor(MANAGE)
    try:
        yield actor
    finally:
        actor.cleanup()


@pytest.fixture()
def viewer():
    actor = _Actor(VIEW)
    try:
        yield actor
    finally:
        actor.cleanup()


@pytest.fixture()
def nobody():
    actor = _Actor()
    try:
        yield actor
    finally:
        actor.cleanup()


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------
def _get(client: TestClient, actor: _Actor):
    return client.get(f"{API_PREFIX}/integrations", headers=actor.headers)


def _put(client: TestClient, actor: _Actor, updates: dict):
    return client.put(
        f"{API_PREFIX}/integrations", json={"updates": updates}, headers=actor.headers
    )


def _test_provider(client: TestClient, actor: _Actor, provider: str):
    return client.post(
        f"{API_PREFIX}/integrations/{provider}/test", headers=actor.headers
    )


def _health(client: TestClient, actor: _Actor):
    return client.get(f"{API_PREFIX}/integrations/health", headers=actor.headers)


def _field(body: dict, key: str) -> dict:
    for group in body["groups"]:
        for field in group["fields"]:
            if field["key"] == key:
                return field
    raise AssertionError(f"{key} missing from the integrations schema")


def _row(key: str) -> SystemSetting | None:
    with SessionLocal() as db:
        return db.execute(
            select(SystemSetting).where(SystemSetting.key == key)
        ).scalar_one_or_none()


def _snapshot_setting(key: str) -> dict | None:
    """The row as it stands, or None when absent — for `_restore_setting`."""
    row = _row(key)
    if row is None:
        return None
    return {
        "value": row.value,
        "category": row.category,
        "description": row.description,
        "is_secret": bool(row.is_secret),
    }


def _restore_setting(key: str, prior: dict | None) -> None:
    """Put a shared settings row back exactly as it was before a test wrote it.

    The autouse `_clean_integration_rows` purge deletes this module's rows
    after each test, but deletion-later is the wrong tool for a poisoned
    CREDENTIAL: anything reading the shared row between the write and the purge
    — another suite in a concurrent wave, or the live app — sees the fake value
    and honestly reports auth_failed against it, and a run killed before the
    purge leaves the poison in place indefinitely. Restoring the prior value
    (or absence) in the test's own `finally` closes that window to the width of
    the assertions. The settings cache is cleared too, or a reader could keep
    seeing the poison for up to 60 seconds after the row itself is fixed.
    """
    with SessionLocal() as db:
        row = db.execute(
            select(SystemSetting).where(SystemSetting.key == key)
        ).scalar_one_or_none()
        if prior is None:
            if row is not None:
                db.delete(row)
        elif row is None:
            db.add(SystemSetting(key=key, **prior))
        else:
            for field, value in prior.items():
                setattr(row, field, value)
        db.commit()
    try:
        from app.db.redis import get_redis

        get_redis().delete(f"settings:{key}")
    except Exception:  # noqa: BLE001 — cache cleanup must never fail a test
        pass


def _configure_all(client: TestClient, manager: _Actor) -> None:
    resp = _put(
        client,
        manager,
        {
            "analytics.gtm_enabled": "true",
            "analytics.gtm_container_id": VALID_GTM,
            "analytics.ga4_enabled": "true",
            "analytics.ga4_measurement_id": VALID_GA4,
            "analytics.ga4_api_secret": SECRET,
            "analytics.clarity_enabled": "true",
            "analytics.clarity_project_id": VALID_CLARITY,
        },
    )
    assert resp.status_code == 200, resp.text


def _fake_ga4_client(payload: dict, status_code: int = 200) -> MagicMock:
    """A stand-in for `httpx.Client` used as a context manager."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    response.text = json.dumps(payload)

    instance = MagicMock()
    instance.post.return_value = response

    factory = MagicMock()
    factory.return_value.__enter__.return_value = instance
    factory.return_value.__exit__.return_value = False
    return factory


# ===========================================================================
# 1. Secrets never leave the building
# ===========================================================================
class TestSecretsNeverReachTheBrowser:
    def test_get_integrations_never_returns_the_secret(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """Assert on the raw body, not on the field the test expects to be masked.

        A field-level check only proves the one field the test knows about is
        masked. Searching the whole response for the literal plaintext also
        catches it turning up in an echo of the request, a validation error, a
        debug key, or a field added later by someone who did not read this file.
        """
        assert _put(client, manager, {"analytics.ga4_api_secret": SECRET}).status_code == 200

        resp = _get(client, manager)
        assert resp.status_code == 200, resp.text
        assert SECRET not in resp.text, "the API secret was returned to the browser"

        field = _field(resp.json(), "analytics.ga4_api_secret")
        assert field["is_secret"] is True
        assert field["value"] == integrations_service.REDACTED
        assert field["has_value"] is True, (
            "a masked field that cannot say whether it is set is unusable — the "
            "operator cannot tell 'configured, hidden' from 'never entered'"
        )

    def test_the_update_response_does_not_echo_the_secret_back(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """The PUT response embeds the full integrations payload; it must be
        redacted there too. Returning it 'just this once, they typed it anyway'
        puts the credential in the browser's memory, its network log and any
        error-reporting SDK on the page."""
        resp = _put(client, manager, {"analytics.ga4_api_secret": SECRET})
        assert resp.status_code == 200, resp.text
        assert SECRET not in resp.text

    def test_service_account_json_is_redacted_too(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """The fake key below is well-formed enough to save and USELESS to
        authenticate with, so it is restored in the `finally` rather than left
        for the fixture purge: the GA4 Data API reads this same shared row, and
        a concurrent (or crashed) run that saw the poison would honestly —
        and mystifyingly — report auth_failed."""
        key = "analytics.ga4_data_api_credentials"
        prior = _snapshot_setting(key)
        creds = json.dumps(
            {
                "type": "service_account",
                "client_email": "svc@example.iam.gserviceaccount.com",
                "private_key": "-----BEGIN PRIVATE KEY-----\nLEAKME-abc123\n",
            }
        )
        try:
            assert _put(client, manager, {key: creds}).status_code == 200

            resp = _get(client, manager)
            assert "LEAKME-abc123" not in resp.text
            assert _field(resp.json(), key)["value"] == "***"
        finally:
            _restore_setting(key, prior)

    def test_health_never_reports_a_credential_value(
        self, client: TestClient, manager: _Actor, viewer: _Actor
    ) -> None:
        """Health reports credential *state*, never the credential."""
        assert _put(client, manager, {"analytics.ga4_api_secret": SECRET}).status_code == 200

        resp = _health(client, viewer)
        assert resp.status_code == 200, resp.text
        assert SECRET not in resp.text
        assert resp.json()["providers"]["ga4"]["api_secret_state"] == "set"


class TestPublicSettingsLeak:
    def test_secrets_are_absent_from_the_anonymous_settings_endpoint(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """`/settings/public` serves a hard-coded allowlist to anonymous
        customers. The secret keys must not be in it — now or after the lead
        adds the *public* analytics keys, which is why this asserts on the whole
        secret set rather than on today's two names."""
        assert _put(client, manager, {"analytics.ga4_api_secret": SECRET}).status_code == 200

        resp = client.get(f"{SETTINGS_PREFIX}/public")  # no auth header at all
        assert resp.status_code == 200, resp.text
        assert SECRET not in resp.text, "the API secret is being served anonymously"

        body = resp.json()
        for key in integrations_service.SECRET_KEYS:
            assert key not in body, f"{key} is exposed on /settings/public"

    def test_the_derived_public_allowlist_contains_no_secret(self) -> None:
        """`PUBLIC_KEYS` is what the lead copies into `_PUBLIC_KEYS`. It is
        derived from the field visibilities rather than hand-maintained, and
        this asserts the derivation, because a hand-edited list is exactly how a
        secret ends up on an anonymous endpoint."""
        described = integrations_service.describe_settings()
        assert set(described["public_keys"]).isdisjoint(
            integrations_service.SECRET_KEYS
        )
        for key in described["public_keys"]:
            field = integrations_service.FIELD_BY_KEY[key]
            assert field.visibility == integrations_service.Visibility.PUBLIC
            assert field.is_secret is False


class TestEncryptionAtRest:
    def test_the_secret_is_stored_encrypted_not_in_cleartext(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """Read the row straight out of `system_settings`.

        `is_secret=True` only masks the value in API responses — on its own it
        leaves the credential readable by anyone with a database session, a
        backup, or a replica. The stored bytes must be Fernet ciphertext, and
        must still round-trip.
        """
        assert _put(client, manager, {"analytics.ga4_api_secret": SECRET}).status_code == 200

        row = _row("analytics.ga4_api_secret")
        assert row is not None
        assert row.is_secret is True
        assert row.value != SECRET, "the API secret is stored in cleartext"
        assert SECRET not in (row.value or ""), "the cleartext is embedded in the stored value"
        assert (row.value or "").startswith("gAAAAA"), (
            f"expected a Fernet token, got {row.value!r}"
        )
        assert decrypt_secret(row.value) == SECRET

        with SessionLocal() as db:
            assert integrations_service.read_secret(db, "analytics.ga4_api_secret") == SECRET

    def test_public_values_are_stored_in_cleartext(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """The mirror of the test above. A GTM container id is in the page
        source of every request; encrypting it would imply a confidentiality
        this value does not have and cannot get."""
        assert _put(
            client, manager, {"analytics.gtm_container_id": VALID_GTM}
        ).status_code == 200
        row = _row("analytics.gtm_container_id")
        assert row is not None
        assert row.value == VALID_GTM
        assert row.is_secret is False

    def test_resubmitting_the_mask_does_not_clobber_the_stored_secret(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """The form round-trips `***`. Writing that through would replace a
        working credential with three asterisks the moment anyone saved an
        unrelated field in the same group."""
        assert _put(client, manager, {"analytics.ga4_api_secret": SECRET}).status_code == 200

        resp = _put(
            client,
            manager,
            {
                "analytics.ga4_api_secret": integrations_service.REDACTED,
                "analytics.ga4_measurement_id": VALID_GA4,
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["changed"] == ["analytics.ga4_measurement_id"]
        assert decrypt_secret(_row("analytics.ga4_api_secret").value) == SECRET

    def test_an_empty_string_clears_the_secret(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """Distinct from the mask: an explicitly emptied field must actually
        remove the credential, or a compromised key could never be revoked from
        the UI.

        Delivery moves to browser-only in the same request: purchase delivery
        defaults to server mode, and the deliverability guard (rightly) refuses
        to drop the secret while the server is the one that needs it. Revoking
        a compromised key therefore goes hand in hand with saying who sends
        purchases from now on — see `test_analytics_delivery_guard.py`.
        """
        assert _put(client, manager, {"analytics.ga4_api_secret": SECRET}).status_code == 200
        assert _put(
            client,
            manager,
            {
                "analytics.ga4_api_secret": "",
                "analytics.ga4_purchase_delivery": "browser",
            },
        ).status_code == 200

        assert (_row("analytics.ga4_api_secret").value or "") == ""
        with SessionLocal() as db:
            assert integrations_service.secret_state(db, "analytics.ga4_api_secret") == "unset"


# ===========================================================================
# 2. Audit
# ===========================================================================
class TestAudit:
    def _events(self) -> list[AuditEvent]:
        with SessionLocal() as db:
            return list(
                db.execute(
                    select(AuditEvent)
                    .where(AuditEvent.action == "analytics.integrations.update")
                    .order_by(AuditEvent.id)
                )
                .scalars()
                .all()
            )

    def test_updating_a_setting_writes_an_audit_event(
        self, client: TestClient, manager: _Actor
    ) -> None:
        assert _put(
            client, manager, {"analytics.gtm_container_id": VALID_GTM}
        ).status_code == 200

        events = self._events()
        assert len(events) == 1, f"expected exactly one audit row, got {len(events)}"
        event = events[0]
        assert event.actor_user_id == manager.id
        assert event.target_type == "setting"
        assert event.target_label == "analytics.gtm_container_id"
        assert event.extra["after"] == VALID_GTM
        assert event.extra["before"] in ("", None)

    def test_the_audit_trail_records_that_a_secret_changed_not_what_it_became(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """`audit_events` is long-lived, widely readable and frequently
        exported. A credential written into it survives every rotation of the
        credential itself."""
        assert _put(client, manager, {"analytics.ga4_api_secret": SECRET}).status_code == 200

        events = self._events()
        assert len(events) == 1
        event = events[0]
        assert event.target_label == "analytics.ga4_api_secret"
        assert event.extra["after"] == integrations_service.REDACTED
        assert event.extra["before"] == integrations_service.REDACTED
        assert SECRET not in json.dumps(event.extra)
        assert SECRET not in (event.summary or "")

    def test_a_no_op_save_writes_nothing(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """Saving an unchanged form must not manufacture an audit row. A trail
        full of no-ops is a trail nobody reads."""
        assert _put(
            client, manager, {"analytics.gtm_container_id": VALID_GTM}
        ).status_code == 200
        resp = _put(client, manager, {"analytics.gtm_container_id": VALID_GTM})
        assert resp.status_code == 200
        assert resp.json()["changed"] == []
        assert len(self._events()) == 1

    def test_resaving_an_unchanged_secret_is_not_recorded_as_a_change(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """Fernet is randomised, so naive re-encryption of an identical secret
        produces different bytes and would look like a rotation every save."""
        assert _put(client, manager, {"analytics.ga4_api_secret": SECRET}).status_code == 200
        first = _row("analytics.ga4_api_secret").value

        resp = _put(client, manager, {"analytics.ga4_api_secret": SECRET})
        assert resp.status_code == 200
        assert resp.json()["changed"] == []
        assert _row("analytics.ga4_api_secret").value == first
        assert len(self._events()) == 1


# ===========================================================================
# 3. Permissions
# ===========================================================================
class TestPermissions:
    def test_anonymous_callers_are_refused_everywhere(
        self, client: TestClient
    ) -> None:
        for method, path in (
            ("GET", "/integrations"),
            ("PUT", "/integrations"),
            ("POST", "/integrations/gtm/test"),
            ("GET", "/integrations/health"),
        ):
            resp = client.request(method, f"{API_PREFIX}{path}", json={"updates": {}})
            assert resp.status_code in (401, 403), (
                f"{method} {path} allowed an anonymous caller: {resp.status_code}"
            )

    def test_manage_permission_is_required_to_read_or_write(
        self, client: TestClient, nobody: _Actor, manager: _Actor
    ) -> None:
        """403 without the grant, 200 with it — on the same routes, with users
        that differ only in the grant."""
        assert _get(client, nobody).status_code == 403
        assert _put(client, nobody, {"analytics.gtm_container_id": VALID_GTM}).status_code == 403
        assert _test_provider(client, nobody, "gtm").status_code == 403

        assert _get(client, manager).status_code == 200
        assert _put(client, manager, {"analytics.gtm_container_id": VALID_GTM}).status_code == 200
        assert _test_provider(client, manager, "gtm").status_code == 200

    def test_a_refused_write_changes_nothing(
        self, client: TestClient, nobody: _Actor
    ) -> None:
        """A 403 that still wrote the row would be the worst possible outcome.

        Asserted as "the refused VALUE did not land", not as "the row is
        absent": `system_settings` is shared, and any concurrent reader of the
        integrations screen (another suite, the live app) legitimately
        re-creates the row with its shipped default via `ensure_rows`. Same
        reasoning as `test_malformed_values_are_rejected_not_normalised`.
        """
        assert _put(
            client, nobody, {"analytics.gtm_container_id": VALID_GTM}
        ).status_code == 403
        row = _row("analytics.gtm_container_id")
        assert row is None or (row.value or "") != VALID_GTM, (
            "the refused write landed in the shared settings row"
        )

    def test_health_requires_control_centre_view_not_manage(
        self, client: TestClient, viewer: _Actor, manager: _Actor, nobody: _Actor
    ) -> None:
        """The two permissions are genuinely separate.

        Someone who watches dashboards must be able to see that tracking has
        stopped without also holding the credential that can write into the GA4
        property — and the manage grant does not implicitly confer the view.
        """
        assert _health(client, nobody).status_code == 403
        assert _health(client, viewer).status_code == 200
        assert _health(client, manager).status_code == 403, (
            "manage must not silently satisfy the control-centre view grant"
        )

    def test_the_viewer_cannot_read_or_edit_credentials(
        self, client: TestClient, viewer: _Actor
    ) -> None:
        assert _get(client, viewer).status_code == 403
        assert _put(client, viewer, {"analytics.ga4_api_secret": SECRET}).status_code == 403


# ===========================================================================
# 4. Validation
# ===========================================================================
class TestValidation:
    def test_an_unknown_key_is_rejected(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """A key with no field spec has no visibility, so nothing knows whether
        it is a secret. Writing it would create a row this module cannot
        classify — and `SettingsService.set_many` would silently drop it,
        returning 200 for a save that did nothing."""
        resp = _put(client, manager, {"analytics.definitely_not_a_key": "x"})
        assert resp.status_code == 400, resp.text
        assert "known_keys" in resp.json()["error"]["details"]

    @pytest.mark.parametrize(
        "key,value",
        [
            ("analytics.gtm_container_id", "UA-12345-1"),
            ("analytics.ga4_measurement_id", "GTM-ABCD123"),
            ("analytics.clarity_project_id", "NOT-LOWERCASE"),
            ("analytics.consent_mode", "maybe"),
            ("analytics.ga4_property_id", "not-numeric"),
        ],
    )
    def test_malformed_values_are_rejected_not_normalised(
        self, client: TestClient, manager: _Actor, key: str, value: str
    ) -> None:
        """Rejected loudly, because the alternative is a tag that loads,
        reports nothing, and gives nobody a reason to look at it again."""
        resp = _put(client, manager, {key: value})
        assert resp.status_code == 422, f"{key}={value!r} was accepted: {resp.text}"
        # A 422 that still wrote the row would be the worst of both worlds. The
        # row may legitimately exist holding its shipped default (`ensure_rows`
        # creates it), so the assertion is that the *rejected* value is not what
        # landed in it.
        row = _row(key)
        assert row is None or (row.value or "") != value

    def test_service_account_json_must_be_a_service_account_key(
        self, client: TestClient, manager: _Actor
    ) -> None:
        assert _put(
            client, manager, {"analytics.ga4_data_api_credentials": "not json at all"}
        ).status_code == 422
        assert _put(
            client,
            manager,
            {"analytics.ga4_data_api_credentials": json.dumps({"type": "service_account"})},
        ).status_code == 422, "JSON without a private_key is not a usable key file"

    def test_an_unknown_provider_is_rejected(
        self, client: TestClient, manager: _Actor
    ) -> None:
        resp = _test_provider(client, manager, "facebook")
        assert resp.status_code == 422, resp.text


# ===========================================================================
# 5. Connection tests — honesty
# ===========================================================================
class TestConnectionHonesty:
    @pytest.mark.parametrize("provider", ["gtm", "clarity"])
    def test_gtm_and_clarity_cannot_be_verified_server_side(
        self, client: TestClient, manager: _Actor, provider: str
    ) -> None:
        """Neither provider publishes an endpoint that distinguishes a real
        container/project from a well-formed invented one, so the only honest
        answer is that we could not check. A green tick here would mean "we did
        not look", and the operator would stop looking too.
        """
        _configure_all(client, manager)

        resp = _test_provider(client, manager, provider)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "cannot_verify_server_side", body
        assert body["verified"] is False, "a check that did not happen is not a pass"
        assert body["not_checked"], "the result must say what it cannot prove"
        assert body["checked"], "the result must say what it did do"

    @pytest.mark.parametrize("provider", ["gtm", "clarity"])
    def test_they_make_no_outbound_request_at_all(
        self, client: TestClient, manager: _Actor, provider: str
    ) -> None:
        """Not merely 'the fetch is not treated as proof' — there is no fetch.

        Hitting `gtm.js` and ignoring the result would still burn a request per
        click and leave a future reader assuming the 200 meant something.
        """
        _configure_all(client, manager)
        factory = _fake_ga4_client({"validationMessages": []})
        with patch.object(integrations_service.httpx, "Client", factory):
            resp = _test_provider(client, manager, provider)
        assert resp.status_code == 200
        assert not factory.called, f"{provider} made an HTTP request"

    def test_an_unconfigured_provider_says_so_rather_than_failing(
        self, client: TestClient, manager: _Actor
    ) -> None:
        resp = _test_provider(client, manager, "clarity")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "not_configured"
        assert body["verified"] is False

    def test_a_malformed_id_is_reported_as_such(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """The one thing that *is* locally decidable must not be hidden inside
        `cannot_verify_server_side` — a malformed id is a definite failure.

        The malformed id is written straight into the shared row (the API would
        rightly refuse it), so like the poisoned credential above it is restored
        in the `finally` rather than left for the fixture purge.
        """
        key = "analytics.gtm_container_id"
        prior = _snapshot_setting(key)
        try:
            with SessionLocal() as db:
                integrations_service.ensure_rows(db)
                db.execute(
                    text(
                        "UPDATE system_settings SET value = 'gtm-lowercase' "
                        "WHERE `key` = 'analytics.gtm_container_id'"
                    )
                )
                db.commit()

            body = _test_provider(client, manager, "gtm").json()
            assert body["status"] == "invalid_format", body
            assert body["verified"] is False
        finally:
            _restore_setting(key, prior)


class TestGa4ConnectionTest:
    def test_ga4_without_credentials_makes_no_request(
        self, client: TestClient, manager: _Actor
    ) -> None:
        factory = _fake_ga4_client({"validationMessages": []})
        with patch.object(integrations_service.httpx, "Client", factory):
            body = _test_provider(client, manager, "ga4").json()
        assert body["status"] == "not_configured", body
        assert body["verified"] is False
        assert not factory.called

    def test_ga4_is_verified_when_google_raises_no_validation_messages(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """GA4 is the one provider with a real server-side check — the MP
        *debug* endpoint validates the payload and the id/secret pair and says
        what is wrong, unlike production `/mp/collect` which answers 204 to
        everything."""
        _configure_all(client, manager)
        factory = _fake_ga4_client({"validationMessages": []})
        with patch.object(integrations_service.httpx, "Client", factory):
            body = _test_provider(client, manager, "ga4").json()

        assert body["status"] == "verified", body
        assert body["verified"] is True
        assert body["not_checked"], (
            "even a real pass must state its limits — it does not prove the "
            "browser tag fires"
        )

        call = factory.return_value.__enter__.return_value.post.call_args
        assert call.args[0] == integrations_service.GA4_DEBUG_URL, (
            "the test must hit /debug/mp/collect; the production endpoint would "
            "write a junk event into the customer's property and prove nothing"
        )
        assert call.kwargs["params"]["api_secret"] == SECRET
        assert call.kwargs["params"]["measurement_id"] == VALID_GA4

    def test_ga4_reports_googles_rejection_rather_than_a_generic_failure(
        self, client: TestClient, manager: _Actor
    ) -> None:
        _configure_all(client, manager)
        factory = _fake_ga4_client(
            {
                "validationMessages": [
                    {
                        "description": "Measurement id and api_secret are invalid.",
                        "validationCode": "VALUE_INVALID",
                    }
                ]
            }
        )
        with patch.object(integrations_service.httpx, "Client", factory):
            body = _test_provider(client, manager, "ga4").json()

        assert body["status"] == "rejected", body
        assert body["verified"] is False
        assert "api_secret are invalid" in body["message"]

    def test_a_network_failure_is_not_reported_as_a_credential_failure(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """'Google was unreachable' and 'your API secret is wrong' demand
        completely different responses from the operator."""
        _configure_all(client, manager)
        factory = MagicMock()
        factory.return_value.__enter__.return_value.post.side_effect = (
            integrations_service.httpx.ConnectTimeout("timed out")
        )
        with patch.object(integrations_service.httpx, "Client", factory):
            body = _test_provider(client, manager, "ga4").json()

        assert body["status"] == "unreachable", body
        assert body["verified"] is False


# ===========================================================================
# 6. Tracking health
# ===========================================================================
def _seed_outbox(statuses: dict[str, int]) -> list[int]:
    """Insert outbox rows with the given per-status counts. Returns their ids."""
    ids: list[int] = []
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        for row_status, count in statuses.items():
            for n in range(count):
                row = AnalyticsEventOutbox(
                    event_name=OutboxEventName.PURCHASE,
                    transaction_id=f"PYTEST-{uuid.uuid4().hex[:16]}",
                    order_id=None,
                    occurred_at=now - timedelta(minutes=n + 1),
                    payload={"value": 100, "currency": "INR", "items": []},
                    consent_state=(
                        ConsentState.DENIED
                        if row_status == OutboxStatus.SUPPRESSED_NO_CONSENT
                        else ConsentState.GRANTED
                    ),
                    status=row_status,
                    delivered_at=(
                        now if row_status == OutboxStatus.DELIVERED else None
                    ),
                    last_error=(
                        "MP returned 400" if row_status == OutboxStatus.FAILED else None
                    ),
                )
                db.add(row)
                db.flush()
                ids.append(row.id)
        db.commit()
    return ids


def _delete_outbox(ids: list[int]) -> None:
    if not ids:
        return
    with SessionLocal() as db:
        for row_id in ids:
            db.execute(
                text("DELETE FROM analytics_event_outbox WHERE id = :id"), {"id": row_id}
            )
        db.commit()


class TestTrackingHealth:
    def test_outbox_counts_include_suppressed_no_consent(
        self, client: TestClient, viewer: _Actor
    ) -> None:
        """`SUPPRESSED_NO_CONSENT` is a first-class count, not "not delivered".

        A suppressed row is a *correct* outcome — the purchase was recorded
        internally and deliberately not transmitted. Folding it in with FAILED
        would either raise a false alarm for every consent-declining customer or
        hide a real outage behind a plausible-looking number.
        """
        before = _health(client, viewer).json()["outbox"]["counts"]

        ids = _seed_outbox(
            {
                OutboxStatus.PENDING: 2,
                OutboxStatus.DELIVERED: 3,
                OutboxStatus.FAILED: 1,
                OutboxStatus.SUPPRESSED_NO_CONSENT: 4,
            }
        )
        try:
            body = _health(client, viewer).json()
            counts = body["outbox"]["counts"]

            assert OutboxStatus.SUPPRESSED_NO_CONSENT in counts, (
                f"suppressed events are not reported at all: {counts}"
            )
            for row_status, added in (
                (OutboxStatus.PENDING, 2),
                (OutboxStatus.DELIVERED, 3),
                (OutboxStatus.FAILED, 1),
                (OutboxStatus.SUPPRESSED_NO_CONSENT, 4),
            ):
                assert counts[row_status] - before.get(row_status, 0) == added, (
                    f"{row_status}: {counts[row_status]} - "
                    f"{before.get(row_status, 0)} != {added}"
                )
            assert counts["total"] - before.get("total", 0) == 10

            assert body["outbox"]["last_delivered_at"] is not None
            assert body["outbox"]["last_error"] == "MP returned 400"

            codes = {w["code"] for w in body["warnings"]}
            assert "outbox_failed_rows" in codes, (
                "a purchase that exhausted its retries is permanently lost "
                "attribution and must be surfaced, not merely counted"
            )
        finally:
            _delete_outbox(ids)

    def test_environment_mismatch_is_detected(
        self, client: TestClient, manager: _Actor, viewer: _Actor
    ) -> None:
        """A production deployment configured with a staging property.

        Every other signal is green: the tag loads, events send, the staging
        property fills up. The live reports are simply missing the revenue, and
        only the declared property environment can catch it.
        """
        _configure_all(client, manager)
        assert _put(
            client, manager, {"analytics.property_environment": "staging"}
        ).status_code == 200

        original = _config.settings.ENVIRONMENT
        _config.settings.ENVIRONMENT = "production"
        try:
            body = _health(client, viewer).json()
        finally:
            _config.settings.ENVIRONMENT = original

        env = body["environment"]
        assert env["mismatch"] is True, env
        assert env["mismatch_code"] == "production_host_non_production_property"
        assert env["app_environment"] == "production"
        assert env["declared_property_environment"] == "staging"
        assert "environment_mismatch" in {w["code"] for w in body["warnings"]}

    def test_the_reverse_mismatch_is_detected_too(
        self, client: TestClient, manager: _Actor, viewer: _Actor
    ) -> None:
        """Staging traffic into the production property inflates conversions
        the business then spends money against — the more expensive direction
        of the same mistake."""
        _configure_all(client, manager)
        assert _put(
            client, manager, {"analytics.property_environment": "production"}
        ).status_code == 200

        original = _config.settings.ENVIRONMENT
        _config.settings.ENVIRONMENT = "staging"
        try:
            env = _health(client, viewer).json()["environment"]
        finally:
            _config.settings.ENVIRONMENT = original

        assert env["mismatch"] is True, env
        assert env["mismatch_code"] == "non_production_host_production_property"

    def test_a_matching_environment_raises_no_mismatch(
        self, client: TestClient, manager: _Actor, viewer: _Actor
    ) -> None:
        _configure_all(client, manager)
        assert _put(
            client, manager, {"analytics.property_environment": "production"}
        ).status_code == 200

        original = _config.settings.ENVIRONMENT
        _config.settings.ENVIRONMENT = "production"
        try:
            env = _health(client, viewer).json()["environment"]
        finally:
            _config.settings.ENVIRONMENT = original

        assert env["mismatch"] is False, env
        assert env["mismatch_code"] is None

    def test_provider_and_consent_state_are_reported(
        self, client: TestClient, manager: _Actor, viewer: _Actor
    ) -> None:
        _configure_all(client, manager)
        body = _health(client, viewer).json()

        for name, ident in (
            ("gtm", VALID_GTM),
            ("ga4", VALID_GA4),
            ("clarity", VALID_CLARITY),
        ):
            state = body["providers"][name]
            assert state["enabled"] is True
            assert state["configured"] is True
            assert state["id"] == ident
            assert state["id_format_ok"] is True

        assert body["providers"]["ga4"]["api_secret_state"] == "set"
        assert body["providers"]["ga4"]["server_delivery_ready"] is True
        assert body["consent"]["mode"] == "basic"

    def test_a_tag_switched_on_with_no_id_is_a_warning(
        self, client: TestClient, manager: _Actor, viewer: _Actor
    ) -> None:
        """The storefront looks instrumented and is collecting nothing. Nothing
        errors; the reports are simply empty."""
        assert _put(client, manager, {"analytics.clarity_enabled": "true"}).status_code == 200

        body = _health(client, viewer).json()
        assert body["providers"]["clarity"]["enabled"] is True
        assert body["providers"]["clarity"]["configured"] is False
        assert "clarity_enabled_without_id" in {w["code"] for w in body["warnings"]}

    def test_server_side_delivery_without_an_api_secret_is_a_warning(
        self, client: TestClient, manager: _Actor, viewer: _Actor
    ) -> None:
        """Purchases would queue in the outbox forever with nothing to send
        them with — and the outbox would look busy rather than broken.

        The state is seeded with raw row writes, not through the PUT endpoint:
        the save path now *refuses* to create it (`UndeliverableConfiguration`),
        but deployments configured before the guard existed are already in it,
        and the warning is the defence-in-depth that must keep firing for them.
        """
        with SessionLocal() as db:
            integrations_service.ensure_rows(db)
            db.execute(
                text(
                    "UPDATE system_settings SET value = :v WHERE `key` = :k"
                ),
                [
                    {"k": "analytics.ga4_enabled", "v": "true"},
                    {"k": "analytics.ga4_measurement_id", "v": VALID_GA4},
                    {"k": "analytics.ga4_purchase_delivery", "v": "server"},
                    {"k": "analytics.ga4_api_secret", "v": ""},
                ],
            )
            db.commit()

        body = _health(client, viewer).json()
        assert body["providers"]["ga4"]["server_delivery_ready"] is False
        assert "ga4_server_delivery_without_secret" in {
            w["code"] for w in body["warnings"]
        }

    def test_rollup_watermarks_are_reported(
        self, client: TestClient, viewer: _Actor
    ) -> None:
        """The watermark, not the run timestamp: a pipeline that ticks green and
        advances no watermark serves last week's numbers as today's."""
        job = f"test_integrations_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            db.add(
                AnalyticsSyncRun(
                    job=job,
                    trigger=SyncTrigger.CRON,
                    status=SyncStatus.SUCCESS,
                    tz_generation=1,
                    watermark_date=(now - timedelta(days=1)).date(),
                    started_at=now - timedelta(minutes=5),
                    finished_at=now,
                )
            )
            db.commit()
        try:
            rollups = _health(client, viewer).json()["rollups"]
            entry = next((j for j in rollups["jobs"] if j["job"] == job), None)
            assert entry is not None, f"{job} missing from {rollups['jobs']}"
            assert entry["watermark_date"] == (now - timedelta(days=1)).date().isoformat()
            assert entry["last_success_at"] is not None
            assert rollups["newest_watermark_date"] is not None
        finally:
            with SessionLocal() as db:
                db.execute(
                    text("DELETE FROM analytics_sync_runs WHERE job = :job"), {"job": job}
                )
                db.commit()


# ===========================================================================
# 7. Schema round-trip
# ===========================================================================
class TestSchema:
    def test_every_field_is_rendered_in_exactly_one_group(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """A field the UI never renders is a setting nobody can change — and it
        fails silently, because the API still accepts writes to it."""
        body = _get(client, manager).json()
        rendered = [f["key"] for g in body["groups"] for f in g["fields"]]
        assert sorted(rendered) == sorted(integrations_service.FIELD_BY_KEY)
        assert len(rendered) == len(set(rendered))

    def test_groups_declare_their_testable_provider(
        self, client: TestClient, manager: _Actor
    ) -> None:
        body = _get(client, manager).json()
        providers = {g["id"]: g["provider"] for g in body["groups"]}
        assert providers == {
            "gtm": "gtm",
            "ga4": "ga4",
            "clarity": "clarity",
            # Consent has no third party to test against. Claiming otherwise
            # would put a button on the screen that could only ever lie.
            "consent": None,
        }

    def test_the_schema_survives_a_missing_row(
        self, client: TestClient, manager: _Actor
    ) -> None:
        """`settings_seed.py` is owned elsewhere and may not carry these keys.

        `ensure_rows` creates whatever is missing, so the screen works before
        the seeder catches up — and `SettingsService.set_many`, which silently
        ignores keys with no row, cannot turn a save into a 200 that wrote
        nothing.
        """
        with SessionLocal() as db:
            db.execute(text("DELETE FROM system_settings WHERE `key` LIKE 'analytics.%'"))
            db.commit()

        assert _get(client, manager).status_code == 200
        with SessionLocal() as db:
            present = set(
                db.execute(
                    select(SystemSetting.key).where(
                        SystemSetting.key.in_(list(integrations_service.FIELD_BY_KEY))
                    )
                )
                .scalars()
                .all()
            )
        assert present == set(integrations_service.FIELD_BY_KEY)
