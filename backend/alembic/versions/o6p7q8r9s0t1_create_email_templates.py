"""create email_templates table

Admin-editable email/SMS template system.  Each row holds one transactional
message template (subject + Quill HTML body + optional Quill Delta JSON for
lossless re-editing).  The special key "_layout" in the email channel stores
the outer wrapper HTML that every email body is interpolated into.

Columns:
  id             INT PK autoincrement
  key            VARCHAR(64)  UNIQUE NOT NULL  — machine identifier
  channel        VARCHAR(16)  NOT NULL  default 'email'  — 'email' | 'sms'
  name           VARCHAR(128) NOT NULL  — human display name
  description    VARCHAR(255) NULL
  group_name     VARCHAR(32)  NOT NULL  — UI grouping: orders/account/branding/sms
  subject        VARCHAR(255) NULL  — email subject; null for sms/layout
  body_html      TEXT         NOT NULL
  body_design    JSON         NULL  — Quill Delta; null for sms
  is_enabled     BOOLEAN      NOT NULL  default TRUE
  updated_at     DATETIME(tz) NOT NULL  server_default + onupdate
  updated_by_id  INT          NULL FK→users.id SET NULL

Indexes:
  UQ + IX on key (unique=True already provides the unique index)
  IX on updated_by_id (FK column)

Downgrade: drop_table.

Revision ID: o6p7q8r9s0t1
Revises: n5o6p7q8r9s0
Create Date: 2026-06-21 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "o6p7q8r9s0t1"
down_revision: Union[str, None] = "n5o6p7q8r9s0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_templates",
        # Primary key
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),

        # Machine identifier — globally unique across all channels.
        sa.Column("key", sa.String(length=64), nullable=False),

        # 'email' | 'sms'
        sa.Column(
            "channel",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'email'"),
        ),

        # Human-readable display name shown in the admin template list.
        sa.Column("name", sa.String(length=128), nullable=False),

        # Optional tooltip-level explanation.
        sa.Column("description", sa.String(length=255), nullable=True),

        # UI grouping bucket — named group_name to avoid the SQL reserved word GROUP.
        sa.Column("group_name", sa.String(length=32), nullable=False),

        # Email subject line; null for SMS or layout templates.
        sa.Column("subject", sa.String(length=255), nullable=True),

        # Full template body: Quill HTML / plain Jinja2 text / layout HTML.
        sa.Column("body_html", sa.Text(), nullable=False),

        # Quill Delta JSON for lossless re-editing; null for SMS.
        sa.Column("body_design", sa.JSON(), nullable=True),

        # Soft-disable: False means the template is skipped at send time.
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),

        # Last-saved timestamp — server_default + onupdate mirrors TimestampMixin.
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),

        # Which admin last saved this template; SET NULL on user deletion.
        sa.Column("updated_by_id", sa.Integer(), nullable=True),

        # Constraints
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_email_templates_key"),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name="fk_email_templates_updated_by_id",
            ondelete="SET NULL",
        ),
    )

    # Explicit index on `key` for fast lookups by template key.
    op.create_index(
        "ix_email_templates_key",
        "email_templates",
        ["key"],
        unique=True,
    )

    # Index on FK column — FK indexes are not auto-created in MySQL.
    op.create_index(
        "ix_email_templates_updated_by_id",
        "email_templates",
        ["updated_by_id"],
        unique=False,
    )


def downgrade() -> None:
    # Drop indexes before table (MySQL requires it for named indexes).
    op.drop_index("ix_email_templates_updated_by_id", table_name="email_templates")
    op.drop_index("ix_email_templates_key", table_name="email_templates")
    op.drop_table("email_templates")
