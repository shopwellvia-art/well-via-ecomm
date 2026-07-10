"""add storefront merchandising + rich PDP content fields to products

Two groups of columns:

1. Net-new merchandising fields: flavour, is_combo, offer_text, coupon_code,
   coupon_hint.
2. Rich PDP content fields (short_description, badge, sold_count, ingredients,
   care_instructions, highlights, benefits, specifications, box_contents,
   usage_steps, faqs). These ALREADY EXIST on the shared remote MySQL — they
   were added there by a newer migration lineage (alembic_version 'conpay001')
   that is not part of this repo. They are included here conditionally so a
   fresh local database built from this repo's chain matches the live schema.

Every add is guarded by a column-existence check, making the migration safe
to run against either database state. Additive-only: all columns nullable or
server-defaulted. The shared remote MySQL is applied manually from reviewed
SQL, never via a blind `alembic upgrade head`.

Revision ID: s0t1u2v3w4x5
Revises: r9s0t1u2v3w4
Create Date: 2026-07-09 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "s0t1u2v3w4x5"
down_revision: Union[str, None] = "r9s0t1u2v3w4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (name, column, needs_index)
_COLUMNS: list[tuple[str, sa.Column, bool]] = [
    ("flavour", sa.Column("flavour", sa.String(length=80), nullable=True), True),
    (
        "is_combo",
        sa.Column(
            "is_combo", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
        False,
    ),
    ("offer_text", sa.Column("offer_text", sa.String(length=160), nullable=True), False),
    ("coupon_code", sa.Column("coupon_code", sa.String(length=40), nullable=True), False),
    ("coupon_hint", sa.Column("coupon_hint", sa.String(length=160), nullable=True), False),
    ("short_description", sa.Column("short_description", sa.Text(), nullable=True), False),
    ("badge", sa.Column("badge", sa.String(length=60), nullable=True), False),
    ("sold_count", sa.Column("sold_count", sa.Integer(), nullable=True), False),
    ("ingredients", sa.Column("ingredients", sa.Text(), nullable=True), False),
    (
        "care_instructions",
        sa.Column("care_instructions", sa.Text(), nullable=True),
        False,
    ),
    ("highlights", sa.Column("highlights", sa.JSON(), nullable=True), False),
    ("benefits", sa.Column("benefits", sa.JSON(), nullable=True), False),
    ("specifications", sa.Column("specifications", sa.JSON(), nullable=True), False),
    ("box_contents", sa.Column("box_contents", sa.JSON(), nullable=True), False),
    ("usage_steps", sa.Column("usage_steps", sa.JSON(), nullable=True), False),
    ("faqs", sa.Column("faqs", sa.JSON(), nullable=True), False),
]


def _existing_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {c["name"] for c in inspector.get_columns("products")}


def upgrade() -> None:
    existing = _existing_columns()
    for name, column, needs_index in _COLUMNS:
        if name in existing:
            continue
        op.add_column("products", column)
        if needs_index:
            op.create_index(f"ix_products_{name}", "products", [name])


def downgrade() -> None:
    # Only drop what this migration could have added and still exists.
    existing = _existing_columns()
    for name, _column, needs_index in reversed(_COLUMNS):
        if name not in existing:
            continue
        if needs_index:
            op.drop_index(f"ix_products_{name}", table_name="products")
        op.drop_column("products", name)
