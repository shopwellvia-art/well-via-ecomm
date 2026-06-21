"""add return inspection verdict + refund-execution columns

Returns inspect → refund workflow (2026-06-21). Once a returned item is
physically received it is inspected against the customer's claim + return
policy; a passing inspection unlocks a refund issued through the original
payment method. This migration adds the columns that record the inspection
verdict and how/where the refund was routed.

NON-DESTRUCTIVE: only ADDs nullable columns to `returns`; no data backfill
needed (existing returns simply have NULL inspection/refund metadata).

New columns on `returns`
------------------------
  inspection_passed   BOOLEAN  — verdict (NULL=not inspected)
  inspection_notes    TEXT     — inspector's notes
  inspected_at        DATETIME — when inspected
  refund_method       VARCHAR(40)  — original gateway code or "manual"
  refund_reference    VARCHAR(128) — provider refund id / internal ref

Revision ID: q8r9s0t1u2v3
Revises: p7q8r9s0t1u2
Create Date: 2026-06-21 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "q8r9s0t1u2v3"
down_revision: Union[str, None] = "p7q8r9s0t1u2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("returns", sa.Column("inspection_passed", sa.Boolean(), nullable=True))
    op.add_column("returns", sa.Column("inspection_notes", sa.Text(), nullable=True))
    op.add_column(
        "returns", sa.Column("inspected_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("returns", sa.Column("refund_method", sa.String(length=40), nullable=True))
    op.add_column(
        "returns", sa.Column("refund_reference", sa.String(length=128), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("returns", "refund_reference")
    op.drop_column("returns", "refund_method")
    op.drop_column("returns", "inspected_at")
    op.drop_column("returns", "inspection_notes")
    op.drop_column("returns", "inspection_passed")
