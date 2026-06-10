"""split user TOTP/2FA columns into a 1:1 user_security table

Moves the four 2FA columns off `users` (totp_secret, totp_enabled,
totp_confirmed_at, backup_codes) into a dedicated `user_security` satellite
joined 1:1 on a shared primary key (user_id = PK + FK -> users.id). This is the
inverse of h3c8d9e0f1a2 plus a data-preserving move.

Existing data is backfilled: only users who actually have 2FA data get a
security row, so non-enrolled accounts stay clean (no row). The downgrade
copies the data back onto `users`, so the move round-trips both ways.

Revision ID: e6z7a8b9c0d1
Revises: d5y6z7a8b9c0
Create Date: 2026-06-09 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e6z7a8b9c0d1"
down_revision: Union[str, None] = "d5y6z7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- new satellite table ----
    op.create_table(
        "user_security",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("totp_secret", sa.String(length=255), nullable=True),
        sa.Column(
            "totp_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("totp_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("backup_codes", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )

    # ---- backfill existing 2FA data (only enrolled/pending users) ----
    op.execute(
        "INSERT INTO user_security "
        "(user_id, totp_secret, totp_enabled, totp_confirmed_at, backup_codes) "
        "SELECT id, totp_secret, totp_enabled, totp_confirmed_at, backup_codes "
        "FROM users WHERE totp_secret IS NOT NULL OR totp_enabled = 1"
    )

    # ---- drop the moved columns from users ----
    op.drop_column("users", "backup_codes")
    op.drop_column("users", "totp_confirmed_at")
    op.drop_column("users", "totp_enabled")
    op.drop_column("users", "totp_secret")


def downgrade() -> None:
    # ---- re-add the columns on users ----
    op.add_column(
        "users", sa.Column("totp_secret", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "users",
        sa.Column(
            "totp_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "users",
        sa.Column("totp_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("users", sa.Column("backup_codes", sa.JSON(), nullable=True))

    # ---- copy the data back, then drop the satellite ----
    op.execute(
        "UPDATE users u JOIN user_security s ON s.user_id = u.id SET "
        "u.totp_secret = s.totp_secret, "
        "u.totp_enabled = s.totp_enabled, "
        "u.totp_confirmed_at = s.totp_confirmed_at, "
        "u.backup_codes = s.backup_codes"
    )
    op.drop_table("user_security")
