"""add billing address columns to orders

Adds two new columns to `orders` mirroring the existing shipping snapshot
pattern:

  - `billing_address_id` (INT NULL, FK -> addresses.id ON DELETE SET NULL,
    named `fk_orders_billing_address_id`) — provenance link only; never read
    for fulfillment so that editing or deleting a saved address never rewrites
    historical order data.
  - `billing_address_snapshot` (JSON NULL) — frozen, structured copy of the
    billing address taken at checkout time; source of truth for invoice
    display.

NULL billing means "same as shipping" — callers copy the shipping snapshot
for display; the absence is intentional (no redundant data storage).

No data backfill is performed.

Downgrade is MySQL-safe: FK constraint on orders is dropped before the
index, then the index is dropped, then both columns are removed.

Revision ID: i0e1f2a3b4c5
Revises: h9d0e1f2a3b4
Create Date: 2026-06-12 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "i0e1f2a3b4c5"
down_revision: Union[str, None] = "h9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `billing_address_id`: provenance FK (SET NULL on address delete).
    # Named explicitly so the downgrade can drop it by name (MySQL requires
    # dropping FK constraint before dropping the column).
    op.add_column(
        "orders",
        sa.Column("billing_address_id", sa.Integer(), nullable=True),
    )
    # Create index BEFORE the FK so MySQL reuses it for the constraint rather
    # than creating an implicit duplicate index (same rule as shipping pair).
    op.create_index(
        op.f("ix_orders_billing_address_id"),
        "orders",
        ["billing_address_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_orders_billing_address_id",
        "orders",
        "addresses",
        ["billing_address_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # `billing_address_snapshot`: frozen JSON copy, mirrors shipping_address_snapshot.
    op.add_column(
        "orders",
        sa.Column("billing_address_snapshot", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    # ---- orders: drop FK constraint first (MySQL rule), then index, then columns ----
    op.drop_constraint(
        "fk_orders_billing_address_id", "orders", type_="foreignkey"
    )
    op.drop_index(op.f("ix_orders_billing_address_id"), table_name="orders")
    op.drop_column("orders", "billing_address_snapshot")
    op.drop_column("orders", "billing_address_id")
