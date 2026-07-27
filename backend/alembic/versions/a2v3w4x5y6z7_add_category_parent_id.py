"""add parent_id to categories (one-level subcategory hierarchy)

The column ALREADY EXISTS on the shared remote MySQL once
scripts/sql/2026-07-28_categories_parent_id.sql has been applied there — this
migration exists so a fresh local database built from this repo's chain
matches the live schema. The add is guarded by a column-existence check,
making the migration safe to run against either database state.
Additive-only: the column is nullable. The shared remote MySQL is applied
manually from reviewed SQL, never via a blind `alembic upgrade head`.

Revision ID: a2v3w4x5y6z7
Revises: z1u2v3w4x5y6
Create Date: 2026-07-28 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a2v3w4x5y6z7"
down_revision: Union[str, None] = "z1u2v3w4x5y6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_parent_id() -> bool:
    inspector = sa.inspect(op.get_bind())
    return "parent_id" in {c["name"] for c in inspector.get_columns("categories")}


def upgrade() -> None:
    if _has_parent_id():
        return
    op.add_column("categories", sa.Column("parent_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_categories_parent",
        "categories",
        "categories",
        ["parent_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_categories_parent_id", "categories", ["parent_id"])


def downgrade() -> None:
    if not _has_parent_id():
        return
    op.drop_index("ix_categories_parent_id", table_name="categories")
    op.drop_constraint("fk_categories_parent", "categories", type_="foreignkey")
    op.drop_column("categories", "parent_id")
