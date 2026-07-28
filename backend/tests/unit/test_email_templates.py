"""Unit + integration tests for the admin email/SMS template system.

Test scope (all in-process, SQLite — no live MySQL required):
  1. Seed idempotency — mirrors test_settings_seed.py
  2. Render engine — render_email / render_sms with sample context
  3. Sanitization — <script> is stripped by bleach before persist and at render
  4. Jinja autoescape — HTML chars in context values are escaped, not interpreted
  5. API — GET list / GET detail / PUT update / POST preview / POST reset +
          unauthorised caller gets 401/403

Run inside the backend container:

    docker compose exec backend pytest tests/unit/test_email_templates.py -v

Strategy mirrors test_settings_seed.py:
  - Builds only the necessary tables in an in-memory SQLite DB.
  - EmailTemplate has a nullable FK to users; we also create a minimal users
    table so FK constraints are satisfied (SQLite enforces them when
    PRAGMA foreign_keys=ON, but SQLAlchemy's default is OFF for SQLite, which
    is fine — we just need the DDL to not fail).
  - API tests use FastAPI TestClient against the real app + live MySQL (same as
    test_payment_methods.py), obtaining a bearer token via the seeded admin.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.email_template import EmailTemplate
from app.services.email_templates.catalog import sample_context
from app.services.email_templates.renderer import render_email, render_sms
from app.services.email_templates.seed import (
    DEFAULT_TEMPLATES,
    get_default,
    seed_email_templates,
)
from tests.conftest import TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD

# ---------------------------------------------------------------------------
# Shared SQLite fixture
# ---------------------------------------------------------------------------


def _make_sqlite_tables():
    """Create an in-memory SQLite engine with ONLY the tables we need.

    We import User here so SQLAlchemy knows about its __tablename__ before
    calling create_all — this prevents the FK reference from failing DDL.
    The users table is created first because email_templates references it.
    """
    from app.models.user import User  # noqa: F401 — registers the ORM metadata

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    # Create only the tables we need (not the full schema).
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            EmailTemplate.__table__,
        ],
    )
    return engine


@pytest.fixture()
def db() -> Session:
    """Throwaway in-memory DB with just email_templates + users tables."""
    engine = _make_sqlite_tables()
    with Session(engine) as session:
        yield session


# ---------------------------------------------------------------------------
# Helper — count rows
# ---------------------------------------------------------------------------


def _count(db: Session) -> int:
    return len(db.execute(select(EmailTemplate)).scalars().all())


def _get(db: Session, key: str) -> EmailTemplate | None:
    return db.execute(
        select(EmailTemplate).where(EmailTemplate.key == key)
    ).scalar_one_or_none()


# ===========================================================================
# 1. Seed idempotency
# ===========================================================================


class TestSeedEmailTemplates:
    def test_seeds_all_defaults_into_empty_table(self, db: Session) -> None:
        inserted = seed_email_templates(db)
        assert inserted == len(DEFAULT_TEMPLATES)
        assert _count(db) == len(DEFAULT_TEMPLATES)

    def test_seeds_all_known_keys(self, db: Session) -> None:
        seed_email_templates(db)
        keys = {t.key for t in db.execute(select(EmailTemplate)).scalars()}
        expected = {
            "_layout",
            "order_paid",
            "order_shipped",
            "order_delivered",
            "order_cancelled",
            "order_refunded",
            "return_refunded",
            "return_rejected",
            "password_reset",
            "sms_order_paid",
            "sms_order_shipped",
        }
        assert expected == keys

    def test_is_idempotent_second_call_inserts_zero(self, db: Session) -> None:
        seed_email_templates(db)
        second = seed_email_templates(db)
        assert second == 0
        assert _count(db) == len(DEFAULT_TEMPLATES)

    def test_preserves_customised_body_html_on_re_seed(self, db: Session) -> None:
        """Admin-edited body_html must survive a re-seed (e.g. on app restart)."""
        seed_email_templates(db)
        row = _get(db, "order_paid")
        assert row is not None
        row.body_html = "<p>Custom body — admin edited</p>"
        db.commit()

        seed_email_templates(db)

        row = _get(db, "order_paid")
        assert row is not None
        assert row.body_html == "<p>Custom body — admin edited</p>"

    def test_preserves_customised_subject_on_re_seed(self, db: Session) -> None:
        seed_email_templates(db)
        row = _get(db, "order_paid")
        assert row is not None
        row.subject = "Custom subject"
        db.commit()

        seed_email_templates(db)

        row = _get(db, "order_paid")
        assert row is not None
        assert row.subject == "Custom subject"

    def test_restores_rows_after_truncate(self, db: Session) -> None:
        seed_email_templates(db)
        db.query(EmailTemplate).delete()
        db.commit()
        assert _count(db) == 0

        inserted = seed_email_templates(db)
        assert inserted == len(DEFAULT_TEMPLATES)
        assert _count(db) == len(DEFAULT_TEMPLATES)

    def test_get_default_returns_dict_for_known_key(self) -> None:
        d = get_default("order_paid")
        assert d is not None
        assert d["key"] == "order_paid"
        assert "body_html" in d

    def test_get_default_returns_none_for_unknown_key(self) -> None:
        assert get_default("no_such_key") is None


# ===========================================================================
# 2. Render engine
# ===========================================================================


class TestRenderEngine:
    """Tests use a seeded in-memory DB so render_email can load templates."""

    @pytest.fixture()
    def seeded_db(self, db: Session) -> Session:
        seed_email_templates(db)
        return db

    def test_render_email_returns_nonempty_tuple(self, seeded_db: Session) -> None:
        ctx = sample_context("order_paid")
        subject, html, text = render_email(seeded_db, "order_paid", ctx)
        assert subject, "subject must be non-empty"
        assert html, "html must be non-empty"
        assert text, "text must be non-empty"

    def test_render_email_subject_contains_order_id(self, seeded_db: Session) -> None:
        ctx = sample_context("order_paid")
        subject, _, _ = render_email(seeded_db, "order_paid", ctx)
        # sample_context returns order_id = "1042"
        assert "1042" in subject

    def test_render_email_html_contains_customer_name(self, seeded_db: Session) -> None:
        ctx = sample_context("order_paid")
        _, html, _ = render_email(seeded_db, "order_paid", ctx)
        # sample_context returns customer_name = "Asha"
        assert "Asha" in html

    def test_render_email_html_is_wrapped_by_layout(self, seeded_db: Session) -> None:
        """The rendered html must include layout markers (DOCTYPE / body style)."""
        ctx = sample_context("order_paid")
        _, html, _ = render_email(seeded_db, "order_paid", ctx)
        assert "<!DOCTYPE html" in html or "<!doctype html" in html.lower()

    def test_render_email_html_contains_store_name(self, seeded_db: Session) -> None:
        """Branding context is injected by the layout; store_name fallback fires."""
        ctx = sample_context("order_paid")
        _, html, _ = render_email(seeded_db, "order_paid", ctx)
        # The layout always renders store_name (default "ShopWellvia") in the
        # header and footer.
        assert "ShopWellvia" in html

    def test_render_sms_returns_plain_text(self, seeded_db: Session) -> None:
        ctx = sample_context("sms_order_paid")
        result = render_sms(seeded_db, "sms_order_paid", ctx)
        assert isinstance(result, str)
        assert result.strip(), "SMS render must not be empty"
        # The sample context has order_id = "1042"
        assert "1042" in result

    def test_render_sms_shipped_contains_tracking(self, seeded_db: Session) -> None:
        ctx = sample_context("sms_order_shipped")
        result = render_sms(seeded_db, "sms_order_shipped", ctx)
        # sample context: tracking_number = "DL1234567890"
        assert "DL1234567890" in result

    def test_render_email_missing_key_falls_back_to_default(self, db: Session) -> None:
        """When the row is absent from DB the renderer falls back to seed defaults."""
        # Do NOT seed — so the table is empty.
        ctx = sample_context("order_paid")
        # Should not raise; renderer calls get_default() as fallback.
        subject, html, text = render_email(db, "order_paid", ctx)
        assert subject
        assert html

    def test_render_email_unknown_key_returns_empty(self, db: Session) -> None:
        """A completely unknown key has no DB row AND no shipped default → the
        renderer logs a warning and returns empty output rather than raising
        (guards against AttributeError on the None fallback)."""
        subject, html, text = render_email(db, "no_such_key_xyz", {})
        assert (subject, html, text) == ("", "", "")

    def test_render_sms_unknown_key_returns_empty(self, db: Session) -> None:
        """Same None-guard for the SMS path — empty string, no raise."""
        assert render_sms(db, "no_such_sms_key", {}) == ""

    def test_render_password_reset_contains_otp(self, seeded_db: Session) -> None:
        ctx = sample_context("password_reset")
        subject, html, text = render_email(seeded_db, "password_reset", ctx)
        # sample_context returns otp_code = "482910"
        assert "482910" in html

    def test_disabled_template_returns_empty_strings(self, db: Session) -> None:
        seed_email_templates(db)
        row = _get(db, "order_paid")
        assert row is not None
        row.is_enabled = False
        db.commit()

        ctx = sample_context("order_paid")
        subject, html, text = render_email(db, "order_paid", ctx)
        assert subject == ""
        assert html == ""
        assert text == ""


# ===========================================================================
# 3. Sanitization — <script> must be stripped
# ===========================================================================


class TestSanitization:
    """bleach strips script tags from body_html at persist AND at render time."""

    @pytest.fixture()
    def seeded_db(self, db: Session) -> Session:
        seed_email_templates(db)
        return db

    def test_render_email_strips_script_tag_from_stored_body(
        self, seeded_db: Session
    ) -> None:
        """If body_html contains a script tag, bleach must strip the tag itself.

        bleach.clean(strip=True) removes the tag delimiters but preserves the
        inner text as inert plain text — 'alert(\"xss\")' becomes plain text
        that cannot execute.  The security guarantee is that the <script> wrapper
        is absent; the text content is harmless in that context.
        """
        row = _get(seeded_db, "order_paid")
        assert row is not None
        # Directly set a malicious body (bypassing the service layer sanitizer).
        row.body_html = '<p>Hello {{ customer_name }}</p><script>alert("xss")</script>'
        seeded_db.commit()

        ctx = sample_context("order_paid")
        _, html, _ = render_email(seeded_db, "order_paid", ctx)
        # The <script> tag itself must be absent (script cannot execute).
        assert "<script>" not in html
        assert "</script>" not in html
        # Legitimate content still present.
        assert "Hello" in html

    def test_service_update_sanitizes_script_before_persist(
        self, seeded_db: Session
    ) -> None:
        """EmailTemplateService.update() must bleach the body before writing to DB."""
        from app.models.user import User
        from app.schemas.email_template import EmailTemplateUpdate
        from app.services.email_templates.service import EmailTemplateService

        # Create a minimal admin user (no customer/roles — just enough for the
        # service's actor reference and audit call).
        actor = User(
            email=f"admin-{uuid.uuid4().hex[:6]}@example.com",
            hashed_password="hashed",
            is_active=True,
            is_admin=True,
        )
        seeded_db.add(actor)
        seeded_db.flush()

        malicious_html = '<p>Hi</p><script>alert("xss")</script>'
        payload = EmailTemplateUpdate(body_html=malicious_html)

        # AuditService is imported locally inside service.update() with:
        #   from app.services.audit_service import AuditService
        # so we patch it at its definition module.
        with patch(
            "app.services.audit_service.AuditService"
        ) as mock_audit_cls:
            mock_audit_cls.return_value = MagicMock()
            svc = EmailTemplateService(seeded_db)
            row = svc.update("order_paid", payload, actor)

        # bleach.clean(strip=True) removes the <script> wrapper tag but
        # preserves inner text as inert plain text — the stored value is
        # '<p>Hi</p>alert("xss")' rather than the original with the script tag.
        assert "<script>" not in row.body_html
        assert "</script>" not in row.body_html


# ===========================================================================
# 4. Jinja autoescape — HTML special chars in context must be escaped
# ===========================================================================


class TestJinjaAutoescape:
    @pytest.fixture()
    def seeded_db(self, db: Session) -> Session:
        seed_email_templates(db)
        return db

    def test_html_chars_in_customer_name_are_escaped(
        self, seeded_db: Session
    ) -> None:
        """A customer_name like '<b>x</b>' must appear escaped, not rendered as bold."""
        # Override the default order_paid body to a simple string so we can
        # clearly see escaping behaviour without the layout complexity.
        row = _get(seeded_db, "order_paid")
        assert row is not None
        row.body_html = "<p>Hello {{ customer_name }}</p>"
        seeded_db.commit()

        ctx = {**sample_context("order_paid"), "customer_name": "<b>x</b>"}
        _, html, _ = render_email(seeded_db, "order_paid", ctx)

        # The dangerous literal must not appear.
        assert "<b>x</b>" not in html
        # The escaped form (&lt;b&gt;) or similar must be present somewhere.
        assert "&lt;" in html or "&#" in html

    def test_html_chars_in_order_id_are_escaped(self, seeded_db: Session) -> None:
        row = _get(seeded_db, "order_paid")
        assert row is not None
        row.body_html = "<p>Order {{ order_id }}</p>"
        seeded_db.commit()

        ctx = {**sample_context("order_paid"), "order_id": "<script>"}
        _, html, _ = render_email(seeded_db, "order_paid", ctx)

        assert "<script>" not in html


# ===========================================================================
# 5. API tests — live MySQL + TestClient (mirrors test_payment_methods.py)
# ===========================================================================


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _get_admin_token(client) -> str:
    """Obtain a bearer token for the seeded admin account."""
    from app.core import config as _config

    orig = _config.settings.RATE_LIMIT_ENABLED
    _config.settings.RATE_LIMIT_ENABLED = False
    try:
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": TEST_ADMIN_EMAIL, "password": TEST_ADMIN_PASSWORD},
        )
        assert resp.status_code == 200, f"Admin login failed: {resp.text}"
        return resp.json()["access_token"]
    finally:
        _config.settings.RATE_LIMIT_ENABLED = orig


class TestEmailTemplatesAPI:
    """HTTP-level tests against the full FastAPI app (live MySQL)."""

    @pytest.fixture()
    def api_client(self):
        from fastapi.testclient import TestClient

        from app.main import app

        return TestClient(app, raise_server_exceptions=False)

    @pytest.fixture()
    def auth_headers(self, api_client) -> dict:
        token = _get_admin_token(api_client)
        return {"Authorization": f"Bearer {token}"}

    # ---- 5a. Unauthorized caller ----

    def test_list_without_token_returns_401(self, api_client) -> None:
        resp = api_client.get("/api/v1/email-templates")
        assert resp.status_code == 401

    def test_list_with_non_admin_token_returns_401_or_403(
        self, api_client
    ) -> None:
        """A regular (non-admin, no permissions) user must be denied."""
        from app.core import config as _config
        from app.core.security import hash_password
        from app.db.session import SessionLocal
        from app.models.user import User

        uid = _uid()
        email = f"nonadmin-{uid}@example.com"
        pw = "TestPass123!"

        # Create a regular user in the live DB.
        with SessionLocal() as db:
            u = User(
                email=email,
                hashed_password=hash_password(pw),
                is_active=True,
                is_admin=False,
            )
            db.add(u)
            db.commit()
            user_id = u.id

        orig = _config.settings.RATE_LIMIT_ENABLED
        _config.settings.RATE_LIMIT_ENABLED = False
        try:
            resp = api_client.post(
                "/api/v1/auth/login", json={"email": email, "password": pw}
            )
            assert resp.status_code == 200
            token = resp.json()["access_token"]
        finally:
            _config.settings.RATE_LIMIT_ENABLED = orig

        resp = api_client.get(
            "/api/v1/email-templates",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code in (401, 403)

        # Cleanup.
        with SessionLocal() as db:
            db.execute(
                __import__("sqlalchemy").text("DELETE FROM users WHERE id = :id"),
                {"id": user_id},
            )
            db.commit()

    # ---- 5b. GET list ----

    def test_list_returns_items(self, api_client, auth_headers) -> None:
        resp = api_client.get("/api/v1/email-templates", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert len(data["items"]) >= 9

    def test_list_items_have_required_fields(
        self, api_client, auth_headers
    ) -> None:
        resp = api_client.get("/api/v1/email-templates", headers=auth_headers)
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert "key" in item
            assert "channel" in item
            assert "name" in item
            assert "is_enabled" in item

    # ---- 5c. GET detail ----

    def test_get_detail_returns_variables_and_default_body(
        self, api_client, auth_headers
    ) -> None:
        resp = api_client.get(
            "/api/v1/email-templates/order_paid", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "variables" in data
        assert isinstance(data["variables"], list)
        assert len(data["variables"]) > 0
        assert "default_body_html" in data
        assert data["default_body_html"]  # must be non-empty

    def test_get_detail_unknown_key_returns_404(
        self, api_client, auth_headers
    ) -> None:
        resp = api_client.get(
            "/api/v1/email-templates/no_such_key_xyz", headers=auth_headers
        )
        assert resp.status_code == 404

    # ---- 5d. PUT update ----

    def test_put_update_persists_subject_and_body(
        self, api_client, auth_headers
    ) -> None:
        new_subject = f"Updated subject {_uid()}"
        new_body = f"<p>Updated body {_uid()}</p>"
        new_design = {"ops": [{"insert": "hi\n"}]}

        resp = api_client.put(
            "/api/v1/email-templates/order_shipped",
            headers=auth_headers,
            json={
                "subject": new_subject,
                "body_html": new_body,
                "body_design": new_design,
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["subject"] == new_subject
        # bleach may normalize whitespace; check the meaningful text survives
        assert "Updated body" in data["body_html"]
        assert data["body_design"] == new_design

    def test_put_update_body_design_as_json_object(
        self, api_client, auth_headers
    ) -> None:
        """body_design must be sent/received as a JSON object, not a string."""
        design = {"ops": [{"insert": "test\n"}], "meta": {"v": 2}}
        resp = api_client.put(
            "/api/v1/email-templates/password_reset",
            headers=auth_headers,
            json={"body_design": design},
        )
        assert resp.status_code == 200, resp.text
        returned_design = resp.json()["body_design"]
        # Must be a dict (JSON object), not a string.
        assert isinstance(returned_design, dict)
        assert returned_design.get("ops") == design["ops"]

    # ---- 5e. POST preview ----

    def test_preview_returns_subject_and_html(
        self, api_client, auth_headers
    ) -> None:
        resp = api_client.post(
            "/api/v1/email-templates/order_paid/preview",
            headers=auth_headers,
            json={},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "subject" in data
        assert "html" in data
        assert data["html"]

    def test_preview_with_draft_uses_draft_body(
        self, api_client, auth_headers
    ) -> None:
        draft_body = "<p>Draft preview content UNIQUE-12345</p>"
        resp = api_client.post(
            "/api/v1/email-templates/order_paid/preview",
            headers=auth_headers,
            json={"body_html": draft_body},
        )
        assert resp.status_code == 200
        # The draft text should appear in the preview HTML (wrapped by layout).
        assert "Draft preview content UNIQUE-12345" in resp.json()["html"]

    def test_preview_sms_returns_none_subject(
        self, api_client, auth_headers
    ) -> None:
        resp = api_client.post(
            "/api/v1/email-templates/sms_order_paid/preview",
            headers=auth_headers,
            json={},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["subject"] is None
        assert data["html"]  # plain text returned in html field for SMS

    # ---- 5f. POST reset ----

    def test_reset_restores_default_body(self, api_client, auth_headers) -> None:
        default = get_default("order_delivered")
        assert default is not None
        default_body = default["body_html"]

        # First, update the template to something non-default.
        api_client.put(
            "/api/v1/email-templates/order_delivered",
            headers=auth_headers,
            json={"body_html": "<p>Temporary override body</p>"},
        )

        # Now reset.
        resp = api_client.post(
            "/api/v1/email-templates/order_delivered/reset",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        # Body should be the default again.
        assert data["body_html"] == default_body

    def test_reset_unknown_key_returns_404(
        self, api_client, auth_headers
    ) -> None:
        resp = api_client.post(
            "/api/v1/email-templates/no_such_key_xyz/reset",
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ===========================================================================
# 6. Regression — notifications service + auth service still wire up correctly
# ===========================================================================


class TestNotificationsRegressionSmoke:
    """Lightweight regression smoke — confirms the send-layer refactor
    (render_email / render_sms / html kwarg) didn't break the call sites.

    These tests do NOT send real emails; they mock send_email / send_sms.
    They do use the seeded in-memory DB via the `seeded_db` fixture.
    """

    @pytest.fixture()
    def seeded_db(self, db: Session) -> Session:
        seed_email_templates(db)
        return db

    def _make_order(self, user_email: str = "customer@example.com"):
        """Return a minimal mock Order object for notification tests."""
        from decimal import Decimal

        order = MagicMock()
        order.id = 999
        order.currency = "INR"
        order.subtotal = Decimal("100.00")
        order.tax_amount = Decimal("10.00")
        order.discount_amount = Decimal("0.00")
        order.total_amount = Decimal("110.00")
        order.shipping_address = "12, MG Road, Bengaluru"
        order.tracking_number = "TRK123"
        order.carrier = "Delhivery"
        order.refund_reason = ""
        order.items = []
        user = MagicMock()
        user.email = user_email
        user.full_name = "Asha Sharma"
        user.phone = ""
        user.customer = MagicMock()
        user.customer.first_name = "Asha"
        user.customer.last_name = "Sharma"
        order.user = user
        return order

    def test_notify_order_paid_calls_send_email(self, seeded_db: Session) -> None:
        from app.services.notifications.service import (
            NotificationEvent,
            NotificationService,
        )

        order = self._make_order()

        with (
            patch("app.services.notifications.service.send_email") as mock_send,
            patch(
                "app.services.notifications.service.SettingsService"
            ) as mock_settings_cls,
        ):
            mock_settings_cls.return_value.get_bool.return_value = True
            svc = NotificationService(seeded_db)
            svc.notify(order, NotificationEvent.ORDER_PAID)

        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args.kwargs
        # New send-layer requires html= kwarg.
        assert "html" in call_kwargs
        assert call_kwargs["html"]  # non-empty HTML

    def test_notify_order_shipped_calls_send_email(
        self, seeded_db: Session
    ) -> None:
        from app.services.notifications.service import (
            NotificationEvent,
            NotificationService,
        )

        order = self._make_order()

        with (
            patch("app.services.notifications.service.send_email") as mock_send,
            patch(
                "app.services.notifications.service.SettingsService"
            ) as mock_settings_cls,
        ):
            mock_settings_cls.return_value.get_bool.return_value = True
            svc = NotificationService(seeded_db)
            svc.notify(order, NotificationEvent.ORDER_SHIPPED)

        mock_send.assert_called_once()

    def test_notify_disabled_event_skips_send(self, seeded_db: Session) -> None:
        """When the settings flag is False, no email must be sent."""
        from app.services.notifications.service import (
            NotificationEvent,
            NotificationService,
        )

        order = self._make_order()

        with (
            patch("app.services.notifications.service.send_email") as mock_send,
            patch(
                "app.services.notifications.service.SettingsService"
            ) as mock_settings_cls,
        ):
            mock_settings_cls.return_value.get_bool.return_value = False
            svc = NotificationService(seeded_db)
            svc.notify(order, NotificationEvent.ORDER_PAID)

        mock_send.assert_not_called()

    def test_notify_no_user_email_skips_send(self, seeded_db: Session) -> None:
        """Order with no customer email should skip silently."""
        from app.services.notifications.service import (
            NotificationEvent,
            NotificationService,
        )

        order = self._make_order()
        order.user.email = None  # no email

        with (
            patch("app.services.notifications.service.send_email") as mock_send,
            patch(
                "app.services.notifications.service.SettingsService"
            ) as mock_settings_cls,
        ):
            mock_settings_cls.return_value.get_bool.return_value = True
            svc = NotificationService(seeded_db)
            svc.notify(order, NotificationEvent.ORDER_PAID)

        mock_send.assert_not_called()


class TestAuthServicePasswordResetRegression:
    """Smoke-tests that auth_service.request_password_reset still resolves
    the render_email import correctly after the send-layer refactor.

    We do NOT actually call request_password_reset (it needs a full DB + Redis
    + OTP write) — instead we confirm the imports are healthy and that the
    password_reset template renders with sample context.
    """

    @pytest.fixture()
    def seeded_db(self, db: Session) -> Session:
        seed_email_templates(db)
        return db

    def test_password_reset_template_renders_otp(
        self, seeded_db: Session
    ) -> None:
        from app.services.email_templates.catalog import password_reset_context

        ctx = password_reset_context("Asha", "482910", 10)
        subject, html, text = render_email(seeded_db, "password_reset", ctx)
        assert "482910" in html
        assert "Asha" in html
        assert "10" in html  # expiry_minutes
        assert subject  # non-empty

    def test_password_reset_subject_contains_store_name(
        self, seeded_db: Session
    ) -> None:
        from app.services.email_templates.catalog import password_reset_context

        ctx = password_reset_context("Asha", "000000", 10)
        subject, _, _ = render_email(seeded_db, "password_reset", ctx)
        assert "ShopWellvia" in subject

    def test_send_email_signature_accepts_html_kwarg(self) -> None:
        """Regression: send_email must accept the html= keyword argument.

        The pre-refactor signature was (to, subject, body, db=None) — the
        refactor added html=None.  This test guards against accidental removal.
        """
        import inspect

        from app.email import send_email

        sig = inspect.signature(send_email)
        params = list(sig.parameters)
        assert "html" in params, (
            "send_email lost the html= parameter — "
            "the notifications refactor requires it"
        )
