"""add product analytics & compliance fields

WHAT THIS DOES
--------------
Adds four nullable columns to `products` and nothing else:

    reorder_point    INTEGER      NULL
    hsn_code         VARCHAR(8)   NULL
    brand            VARCHAR(120) NULL
    shelf_life_days  INTEGER      NULL

No index, no default, no backfill, no data movement.

WHY
---
Each of these is the missing input to a reporting column that already exists
and is blank forever without it. This revision is the capture half only — the
rollup/view side is wired separately.

  * `reorder_point` — `agg_inventory_daily.reorder_gap` is defined as
    `stock_close - reorder_point` and is NULL on every row ever written,
    because no product carries a reorder point. The rollup column, its
    metric-kind classification and the low-stock view are all already built.
  * `hsn_code` — view 70 (Tax and GST) is PARTIAL with the limitation
    "HSN-level detail is not stored, so these figures are NOT compliance-grade
    and must not be used for GST filing". HSN is a per-product attribute and is
    mandatory on Indian GST invoices.
  * `brand` — `analytics_order_line.brand_snapshot` (VARCHAR(120)) exists, is
    always NULL, and was reserved with the note that it is there "so a future
    brand field lands without a schema change". This is that field; the widths
    match on purpose so the snapshot can never truncate.
  * `shelf_life_days` — the `days_of_inventory` KPI carries the caveat that
    "shelf life is not modelled; a supplement can have plenty of cover and
    still expire before it sells". This store sells supplements.

WHY ALL FOUR ARE NULLABLE WITH NO SERVER DEFAULT
------------------------------------------------
A product that predates these columns has genuinely unknown values. A default
would assert a fact nobody entered — the same reasoning already applied to
`analytics_marketing_spend.amount`. It matters most for `reorder_point`, where
NULL ("no reorder point configured") and 0 ("reorder only at empty") are
different statements and `reorder_gap` already depends on telling them apart.
Defaulting to 0 would silently declare every SKU in the catalog to be sitting
exactly at its reorder point.

`hsn_code` is VARCHAR, not an integer: leading zeros are significant in the HSN
tariff and an INT column would eat them. Width 8 is the longest legal code, so
an over-long value fails at the column rather than being truncated into a
different, real, wrong tariff heading.

CHAIN NOTE
----------
This was specified to chain off `e8b207fd93c1`, which was the single head when
the work was scoped. `f4c7a1b90d23` (add users.last_login_at) landed on top of
it first, so chaining off `e8b207fd93c1` would create a second head and break
`alembic upgrade head`. It therefore chains off `f4c7a1b90d23` to keep the
lineage linear. If `f4c7a1b90d23` is ever dropped, change `down_revision` back
to `e8b207fd93c1` — there is no other coupling between the two revisions.

NOTE FOR THE SHARED REMOTE DB
-----------------------------
The live database's migration lineage diverges from this repo and `alembic
upgrade` must never be run against it (DEPLOY.md §6). Apply
`scripts/sql/2026-07-31_product_analytics_fields.sql` by hand there, one deploy
BEFORE the code that reads these columns ships. This revision covers local, CI
and fresh installs.

Revision ID: f4c28d19ab60
Revises: f4c7a1b90d23
"""
from alembic import op
import sqlalchemy as sa

revision = "f4c28d19ab60"
down_revision = "f4c7a1b90d23"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("reorder_point", sa.Integer(), nullable=True))
    op.add_column("products", sa.Column("hsn_code", sa.String(length=8), nullable=True))
    op.add_column("products", sa.Column("brand", sa.String(length=120), nullable=True))
    op.add_column(
        "products", sa.Column("shelf_life_days", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("products", "shelf_life_days")
    op.drop_column("products", "brand")
    op.drop_column("products", "hsn_code")
    op.drop_column("products", "reorder_point")
