"""Admin-editable email / SMS templates.

Each row represents one transactional message template that admin users can
customise through the UI without redeploying.  The special key "_layout" in
the 'email' channel holds the outer HTML wrapper that wraps every other email
body — it is rendered first and the per-email body is interpolated into it.

Column notes:
  key          — machine identifier, e.g. "order_paid", "_layout", "otp_sms".
                 Unique across (key, channel) isn't enforced; instead key alone
                 is globally unique, so "order_paid" unambiguously picks the
                 right template regardless of channel.
  channel      — 'email' or 'sms'.  Drives which editor fields are shown in UI.
  group_name   — UI grouping bucket.  Named group_name to avoid the SQL reserved
                 word GROUP.
  body_html    — Quill HTML for email; plain Jinja2 text for SMS; the full
                 layout HTML for the _layout template.
  body_design  — Quill Delta JSON (only for email templates); null for SMS.
  is_enabled   — Soft-disable without deleting the template.
  updated_at   — Overrides the TimestampMixin's updated_at so it is controlled
                 entirely here (server_default + onupdate); NOT inheriting the
                 mixin's version — this model does NOT mix in TimestampMixin.
  updated_by_id — FK to users.id; SET NULL on user deletion so the template
                 row is never orphaned.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.models.base import Base, IDMixin

if TYPE_CHECKING:
    from app.models.user import User


class EmailTemplate(Base, IDMixin):
    __tablename__ = "email_templates"

    # Machine identifier — unique, indexed.
    key: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )

    # 'email' | 'sms'
    channel: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="email"
    )

    # Human-readable display name shown in the admin template list.
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    # Optional longer explanation surfaced as a tooltip in the admin UI.
    description: Mapped[str | None] = mapped_column(String(255))

    # UI grouping bucket: 'orders' | 'account' | 'branding' | 'sms'.
    # Named group_name to avoid the SQL reserved word GROUP.
    group_name: Mapped[str] = mapped_column(String(32), nullable=False)

    # Email subject line; null for SMS or layout templates.
    subject: Mapped[str | None] = mapped_column(String(255))

    # Quill HTML (email) / plain Jinja2 text (sms) / layout HTML (_layout).
    body_html: Mapped[str] = mapped_column(Text, nullable=False)

    # Quill Delta JSON for lossless re-editing; null for SMS templates.
    body_design: Mapped[dict | None] = mapped_column(JSON)

    # Soft-disable flag: False = template is skipped at send time.
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="1"
    )

    # Timestamps — managed explicitly here (no TimestampMixin) so that
    # updated_at carries both server_default and onupdate, matching the pattern
    # in app/models/base.py's TimestampMixin but without created_at overhead.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Which admin last saved this template.  Nullable so templates survive
    # admin account deletion; SET NULL keeps the row intact.
    updated_by_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    # Relationship — lazy load is fine; only read when rendering audit info.
    updated_by: Mapped["User | None"] = relationship(
        "User", foreign_keys=[updated_by_id], lazy="select"
    )
