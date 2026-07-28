"""add parent_id to categories (one-level subcategory hierarchy)

The column ALREADY EXISTS on the shared remote MySQL once
scripts/sql/2026-07-28_categories_parent_id.sql has been applied there — this
migration exists so a fresh local database built from this repo's chain
matches the live schema. The add is guarded by a column-existence check,
making the migration safe to run against either database state.
Additive-only: the column is nullable. The shared remote MySQL is applied
manually from reviewed SQL, never via a blind `alembic upgrade head`.

Revision ID: c4a1d0e7b93f
Revises: p1e2r3f4i5x6
Create Date: 2026-07-28 00:00:00.000000

NOTE ON THE REVISION ID: this migration originally claimed `a2v3w4x5y6z7`,
which `a2v3w4x5y6z7_add_site_pages_table.py` already owned — two authors both
took "the letter after z1u2v3w4x5y6" under the old rolling-token scheme. A
duplicate id makes Alembic raise while building the script directory, so
`alembic upgrade head` (and therefore CI's backend-tests job) failed outright.
Renumbered to a random hex id chained off the real head, and new revisions
should use random hex from here on — the letter rotation has no safe headroom
left and collides silently whenever two branches add a migration in parallel.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4a1d0e7b93f"
down_revision: Union[str, None] = "p1e2r3f4i5x6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_parent_id() -> bool:
    inspector = sa.inspect(op.get_bind())
    return "parent_id" in {c["name"] for c in inspector.get_columns("categories")}


def _fk_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {
        fk["name"]
        for fk in inspector.get_foreign_keys("categories")
        if fk.get("name") and "parent_id" in (fk.get("constrained_columns") or [])
    }


def _index_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {
        ix["name"]
        for ix in inspector.get_indexes("categories")
        if ix.get("name") and list(ix.get("column_names") or []) == ["parent_id"]
    }


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
    """Drop order matters: FOREIGN KEY first, then the index, then the column.

    InnoDB requires an index on a foreign-key column, so dropping
    ix_categories_parent_id while fk_categories_parent still exists fails with
    MySQL error 1553 ("Cannot drop index ...: needed in a foreign key
    constraint"). The original order here was index-first and could never have
    run; the rollback comment in scripts/sql/2026-07-28_categories_parent_id.sql
    already had the correct order, so the two artifacts disagreed.

    Names are discovered by reflection rather than hardcoded: the shared remote
    MySQL is patched by hand from that SQL file, and InnoDB silently drops its
    own auto-generated FK index once the named one is created, so what actually
    exists is worth checking instead of assuming.
    """
    if not _has_parent_id():
        return
    for fk_name in _fk_names():
        op.drop_constraint(fk_name, "categories", type_="foreignkey")
    for ix_name in _index_names():
        op.drop_index(ix_name, table_name="categories")
    op.drop_column("categories", "parent_id")
