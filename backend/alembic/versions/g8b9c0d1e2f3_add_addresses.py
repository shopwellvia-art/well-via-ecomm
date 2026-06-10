"""add addresses table and snapshot columns to orders

Creates the `addresses` table (per-user saved shipping address book) and adds
two new columns to `orders`:

  - `shipping_address_id` (INT NULL, FK -> addresses.id ON DELETE SET NULL,
    named `fk_orders_shipping_address_id`) — provenance link only; never read
    for fulfillment so editing / deleting a saved address never rewrites
    historical order data.
  - `shipping_address_snapshot` (JSON NULL) — frozen, structured copy of the
    address taken at checkout time; source of truth for fulfillment display.

Old orders keep their free-text `shipping_address` and NULL snapshot / FK.
No data backfill is performed.

`AddressLabel` enum stores the member NAME (HOME/WORK/OTHER), matching the
AccountStatus / OrderStatus convention throughout this schema.

Downgrade is MySQL-safe: FK constraint on orders is dropped before the column,
then the orders columns are removed, then the addresses table is dropped.

Revision ID: g8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-06-10 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "g8b9c0d1e2f3"
down_revision: Union[str, None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- addresses table ----
    op.create_table(
        "addresses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("line1", sa.String(length=255), nullable=False),
        sa.Column("line2", sa.String(length=255), nullable=True),
        sa.Column("landmark", sa.String(length=120), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=False),
        sa.Column("state", sa.String(length=120), nullable=False),
        sa.Column("pincode", sa.String(length=6), nullable=False),
        sa.Column(
            "country",
            sa.String(length=2),
            nullable=False,
            server_default=sa.text("'IN'"),
        ),
        sa.Column(
            "label",
            sa.Enum("HOME", "WORK", "OTHER", name="addresslabel"),
            nullable=False,
            server_default="HOME",
        ),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_addresses_user_id"), "addresses", ["user_id"], unique=False
    )

    # ---- new columns on orders ----
    # `shipping_address_id`: provenance FK (SET NULL on address delete).
    # Named explicitly so the downgrade can drop it by name (MySQL requires
    # dropping FK constraint before dropping the column).
    op.add_column(
        "orders",
        sa.Column("shipping_address_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        op.f("ix_orders_shipping_address_id"),
        "orders",
        ["shipping_address_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_orders_shipping_address_id",
        "orders",
        "addresses",
        ["shipping_address_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # `shipping_address_snapshot`: frozen JSON copy, mirrors tracking_events.
    op.add_column(
        "orders",
        sa.Column("shipping_address_snapshot", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    # ---- orders: drop FK constraint first (MySQL rule), then index, then columns ----
    op.drop_constraint(
        "fk_orders_shipping_address_id", "orders", type_="foreignkey"
    )
    op.drop_index(op.f("ix_orders_shipping_address_id"), table_name="orders")
    op.drop_column("orders", "shipping_address_snapshot")
    op.drop_column("orders", "shipping_address_id")

    # ---- drop addresses table ----
    # No separate drop_index for ix_addresses_user_id: MySQL refuses to drop
    # an index still backing the user_id FK (errno 1553); drop_table removes
    # the table's indexes and constraints together.
    op.drop_table("addresses")
