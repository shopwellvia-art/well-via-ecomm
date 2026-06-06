"""add product cost and order_item unit_cost

Revision ID: c4x5y6z7a8b9
Revises: b3w4x5y6z7a8
Create Date: 2026-06-06 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4x5y6z7a8b9"
down_revision: Union[str, None] = "b3w4x5y6z7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable on both columns — existing rows get NULL, which is the correct
    # "no cost recorded" sentinel value for historical products and orders.
    op.add_column("products", sa.Column("cost", sa.Numeric(12, 2), nullable=True))
    op.add_column("order_items", sa.Column("unit_cost", sa.Numeric(12, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("order_items", "unit_cost")
    op.drop_column("products", "cost")
