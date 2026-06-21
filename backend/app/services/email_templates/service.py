"""Business logic for admin-editable email/SMS templates."""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.email_template import EmailTemplate
from app.models.user import User
from app.repositories.email_template_repository import EmailTemplateRepository
from app.schemas.email_template import EmailTemplateUpdate

logger = logging.getLogger(__name__)

# Keys the service recognises — any other key is a 404.
_KNOWN_KEYS: frozenset[str] = frozenset({
    "_layout",
    "order_paid",
    "order_shipped",
    "order_delivered",
    "order_cancelled",
    "order_refunded",
    "password_reset",
    "sms_order_paid",
    "sms_order_shipped",
})


class EmailTemplateService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = EmailTemplateRepository(db)

    # ---- Reads ----

    def list(self) -> list[EmailTemplate]:
        return self.repo.list_all()

    def get_detail(self, key: str) -> EmailTemplate:
        """Return the template row.  404 if key is unknown."""
        if key not in _KNOWN_KEYS:
            raise NotFoundError(f"Unknown email template key: {key!r}")
        row = self.repo.get_by_key(key)
        if row is None:
            # Row hasn't been seeded into this DB yet.
            raise NotFoundError(f"Email template {key!r} not found in database — run the seeder.")
        return row

    # ---- Writes ----

    def update(
        self,
        key: str,
        payload: EmailTemplateUpdate,
        actor: User,
        actor_ip: str | None = None,
    ) -> EmailTemplate:
        """Apply a partial update to a template.

        Sanitizes body_html via bleach before persisting so the DB never holds
        unsafe markup. Records an audit event (without logging the full body).
        """
        from app.services.audit_service import AuditService
        from app.services.email_templates.renderer import _sanitize_html  # type: ignore[attr-defined]

        row = self.get_detail(key)

        changed_fields: list[str] = []

        if payload.subject is not None:
            row.subject = payload.subject
            changed_fields.append("subject")

        if payload.body_html is not None:
            row.body_html = _sanitize_html(payload.body_html)
            changed_fields.append("body_html")

        if payload.body_design is not None:
            row.body_design = payload.body_design
            changed_fields.append("body_design")

        if payload.is_enabled is not None:
            row.is_enabled = payload.is_enabled
            changed_fields.append("is_enabled")

        if changed_fields:
            row.updated_by_id = actor.id
            self.db.flush()

            audit = AuditService(self.db)
            audit.record(
                actor=actor,
                actor_ip=actor_ip,
                action="email_template.update",
                target_type="email_template",
                target_id=row.id,
                target_label=row.key,
                summary=f"Updated template {row.key!r}: {', '.join(changed_fields)}",
                extra={
                    "key": row.key,
                    "changed_fields": changed_fields,
                    # Body deliberately not logged — may be large / contain markup.
                    "subject_changed": "subject" in changed_fields,
                    "body_changed": "body_html" in changed_fields or "body_design" in changed_fields,
                },
            )

        self.db.commit()
        self.db.refresh(row)
        return row

    def reset(self, key: str, actor: User, actor_ip: str | None = None) -> EmailTemplate:
        """Restore a template to its seeded default (subject, body_html, body_design)."""
        from app.services.email_templates.seed import get_default
        from app.services.audit_service import AuditService

        if key not in _KNOWN_KEYS:
            raise NotFoundError(f"Unknown email template key: {key!r}")

        default = get_default(key)
        if default is None:
            raise NotFoundError(f"No default found for template key {key!r}")

        row = self.repo.get_by_key(key)
        if row is None:
            raise NotFoundError(f"Email template {key!r} not found in database — run the seeder.")

        row.subject = default.get("subject")
        row.body_html = default["body_html"]
        row.body_design = default.get("body_design")
        row.is_enabled = default.get("is_enabled", True)
        row.updated_by_id = actor.id

        self.db.flush()
        audit = AuditService(self.db)
        audit.record(
            actor=actor,
            actor_ip=actor_ip,
            action="email_template.reset",
            target_type="email_template",
            target_id=row.id,
            target_label=row.key,
            summary=f"Reset template {row.key!r} to factory default",
            extra={"key": row.key},
        )
        self.db.commit()
        self.db.refresh(row)
        return row

    # ---- Preview ----

    def preview(self, key: str, draft_subject: str | None, draft_body_html: str | None):
        """Render a preview with sample data. Returns (subject, html)."""
        if key not in _KNOWN_KEYS:
            raise NotFoundError(f"Unknown email template key: {key!r}")
        from app.services.email_templates.renderer import preview as _preview
        return _preview(self.db, key, draft_subject, draft_body_html)

    # ---- Test send ----

    def send_test(self, key: str, to: str, actor: User) -> None:
        """Render the template with sample context and send it to ``to``."""
        from app.email import send_email
        from app.services.email_templates.catalog import sample_context
        from app.services.email_templates.renderer import render_email, render_sms

        if key not in _KNOWN_KEYS:
            raise NotFoundError(f"Unknown email template key: {key!r}")

        # Determine channel — check the row first, fall back to seed defaults.
        row = self.repo.get_by_key(key)
        channel = "email"
        if row is not None:
            channel = row.channel
        else:
            from app.services.email_templates.seed import get_default
            default = get_default(key)
            if default:
                channel = default.get("channel", "email")

        ctx = sample_context(key)

        if channel == "sms":
            # For SMS test sends we log via the email channel to show a sample.
            body_text = render_sms(self.db, key, ctx)
            send_email(
                to=to,
                subject=f"[SMS Preview] {key}",
                body=body_text,
                db=self.db,
            )
        else:
            subject, html, text = render_email(self.db, key, ctx)
            if not subject:
                subject = f"[Test] {key}"
            send_email(
                to=to,
                subject=subject,
                body=text,
                html=html,
                db=self.db,
            )
