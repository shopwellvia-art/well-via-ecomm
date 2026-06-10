"""split profile + loyalty columns off `users` into a 1:1 `customers` table

Moves the profile field (full_name, split into first_name/last_name) and the
four loyalty columns (points_balance, lifetime_points, referral_code,
vip_tier_id) off `users` into a dedicated `customers` satellite joined 1:1 via
`user_id` (UNIQUE, FK -> users.id ON DELETE CASCADE). `users` is left
authentication-only.

Unlike the user_security split (which only backfilled enrolled users), EVERY
user becomes a customer — the backfill is unfiltered, so each user gets exactly
one row. full_name is split on the first space into first_name / last_name. The
new profile columns (gender, date_of_birth, profile_image) and lifecycle columns
(account_status, deactivated_at, deleted_at) start empty/active.

Also makes `users.phone` UNIQUE so it can serve as a login identifier alongside
email. The referral_code unique index and the vip_tier_id FK (SET NULL) move
verbatim onto `customers`.

The downgrade copies everything back onto `users` and restores is_active from
account_status, so the move round-trips both ways.

Revision ID: f7a8b9c0d1e2
Revises: e6z7a8b9c0d1
Create Date: 2026-06-10 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, None] = "e6z7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- new 1:1 profile + loyalty satellite ----
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("first_name", sa.String(length=120), nullable=True),
        sa.Column("last_name", sa.String(length=120), nullable=True),
        sa.Column("gender", sa.String(length=16), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("profile_image", sa.String(length=512), nullable=True),
        sa.Column(
            "points_balance", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "lifetime_points", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("referral_code", sa.String(length=32), nullable=True),
        sa.Column("vip_tier_id", sa.Integer(), nullable=True),
        sa.Column(
            "account_status",
            sa.Enum("ACTIVE", "DEACTIVATED", "DELETED", name="accountstatus"),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["vip_tier_id"], ["vip_tiers.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # user_id: single UNIQUE index (matches mapped_column(unique=True, index=True)).
    op.create_index(
        op.f("ix_customers_user_id"), "customers", ["user_id"], unique=True
    )
    op.create_index(
        op.f("ix_customers_referral_code"), "customers", ["referral_code"], unique=True
    )
    op.create_index(
        op.f("ix_customers_vip_tier_id"), "customers", ["vip_tier_id"], unique=False
    )
    op.create_index(
        op.f("ix_customers_account_status"),
        "customers",
        ["account_status"],
        unique=False,
    )

    # ---- backfill: one customer row per user (unfiltered — every user is a
    # customer). full_name is split on the first space into first/last. ----
    op.execute(
        """
        INSERT INTO customers
            (user_id, first_name, last_name, points_balance, lifetime_points,
             referral_code, vip_tier_id, account_status, created_at, updated_at)
        SELECT
            u.id,
            NULLIF(SUBSTRING_INDEX(TRIM(u.full_name), ' ', 1), ''),
            NULLIF(TRIM(SUBSTRING(TRIM(u.full_name),
                   LENGTH(SUBSTRING_INDEX(TRIM(u.full_name), ' ', 1)) + 1)), ''),
            COALESCE(u.points_balance, 0),
            COALESCE(u.lifetime_points, 0),
            u.referral_code,
            u.vip_tier_id,
            CASE WHEN u.is_active = 1 THEN 'ACTIVE' ELSE 'DEACTIVATED' END,
            u.created_at,
            u.updated_at
        FROM users u
        """
    )

    # ---- drop the moved columns from users (FK + indexes first, MySQL rule) ----
    op.drop_constraint("fk_users_vip_tier", "users", type_="foreignkey")
    op.drop_index(op.f("ix_users_vip_tier_id"), table_name="users")
    op.drop_index(op.f("ix_users_referral_code"), table_name="users")
    op.drop_column("users", "vip_tier_id")
    op.drop_column("users", "referral_code")
    op.drop_column("users", "lifetime_points")
    op.drop_column("users", "points_balance")
    op.drop_column("users", "full_name")

    # ---- phone becomes a unique login identifier (was unindexed) ----
    # A phone can serve as a login identifier only if it's unique, so collapse
    # any pre-existing duplicates first: keep the earliest user's number and
    # NULL the rest (duplicate-phone login is impossible by definition). NULLs
    # are allowed many-times under a MySQL unique index, so this is sufficient.
    op.execute(
        """
        UPDATE users u
        JOIN (
            SELECT phone, MIN(id) AS keep_id
            FROM users
            WHERE phone IS NOT NULL AND phone <> ''
            GROUP BY phone
            HAVING COUNT(*) > 1
        ) d ON u.phone = d.phone AND u.id <> d.keep_id
        SET u.phone = NULL
        """
    )
    op.create_index(op.f("ix_users_phone"), "users", ["phone"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_users_phone"), table_name="users")

    # ---- re-add the moved columns on users ----
    op.add_column("users", sa.Column("full_name", sa.String(length=255), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "points_balance", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "lifetime_points", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "users", sa.Column("referral_code", sa.String(length=32), nullable=True)
    )
    op.add_column("users", sa.Column("vip_tier_id", sa.Integer(), nullable=True))

    # ---- copy the data back, rebuild full_name + is_active ----
    op.execute(
        """
        UPDATE users u JOIN customers c ON c.user_id = u.id SET
            u.full_name = NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.last_name)), ''),
            u.points_balance = c.points_balance,
            u.lifetime_points = c.lifetime_points,
            u.referral_code = c.referral_code,
            u.vip_tier_id = c.vip_tier_id,
            u.is_active = CASE WHEN c.account_status = 'ACTIVE' THEN 1 ELSE 0 END
        """
    )

    op.create_index(
        op.f("ix_users_referral_code"), "users", ["referral_code"], unique=True
    )
    op.create_index(
        op.f("ix_users_vip_tier_id"), "users", ["vip_tier_id"], unique=False
    )
    op.create_foreign_key(
        "fk_users_vip_tier", "users", "vip_tiers", ["vip_tier_id"], ["id"],
        ondelete="SET NULL",
    )

    op.drop_table("customers")
