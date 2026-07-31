"""add agg_basket_pair_daily — the market-basket co-occurrence rollup

Creates one table backing view 57 (Product Bundling and Cross-Sell): unordered
product pairs that appeared in the same order, per store-local reporting day.

WHY A TABLE AND NOT A QUERY
---------------------------
Co-occurrence is a self-join of the line facts on `order_id`, which is O(lines²)
per order and unindexable in any useful way. Answering it live would mean
re-deriving every basket in the window on every dashboard request. Pre-computing
it per day also makes the answer *combinable*: a week is the sum of its days,
which is only true because the counts are counts (see below).

WHAT IS DELIBERATELY ABSENT
---------------------------
There is no `support`, `confidence` or `lift` column, and there must never be
one. All three are RATIOs, and a stored daily ratio cannot be re-bucketed into a
week — the average of seven daily lifts is not the weekly lift, and it is wrong
by an amount that grows with the variance in daily volume. The four counts
(`pair_orders`, `orders_with_a`, `orders_with_b`, `total_orders_in_bucket`) are
what is stored; the ratios are recomputed at read time from their sums. This is
the same rule `analytics_base.py` states for averages, applied to ratios.

Likewise absent: any money column. This is a basket-composition table.

CANONICAL PAIR ORDERING
-----------------------
`product_a_id < product_b_id` is an invariant of the writing job
(`aggregation/jobs_basket.py`), not of the schema. MySQL 8 does support CHECK
constraints, but expressing it here would put an invariant in two places that
can disagree, and the job derives the ordering from `combinations(sorted(ids))`
so there is no code path that could produce {B,A}. `tests/
test_analytics_basket_rollup.py` asserts it directly.

Without it {A,B} and {B,A} become two rows under the UNIQUE key and every
support figure halves. Nothing raises; the numbers are simply half.

NO FOREIGN KEYS
---------------
`product_a_id` / `product_b_id` are plain integers, matching every other
analytics table: rollups must stay independently truncatable and rebuildable,
and a deleted product must not erase the affinity it participated in. Identity
is snapshotted into `product_a_name` / `product_b_name` instead.

INDEXES
-------
Three, each for a query the view actually makes:

  * `(bucket_date, pair_orders)` — "the strongest pairs in this window", which
    is the whole view. Without it a window scan reads every pair of every day
    before sorting.
  * `(product_a_id, bucket_date)` and `(product_b_id, bucket_date)` — "what is
    bought with product X". A pair mentioning X stores it in whichever column
    holds the lower id, so both halves need covering; one index cannot serve an
    unordered pair.

TWIN SQL
--------
`backend/scripts/sql/2026-07-29_agg_basket_pair_daily.sql` is generated from
this revision with

    alembic upgrade f2a91c4e7b30:b7e40c9a2d15 --sql

with the `alembic_version` stamp stripped, because production applies
hand-reviewed SQL and never runs alembic (DEPLOY.md §6). Regenerate it the same
way if this revision changes; the two artifacts must not be able to disagree.

BRANCH NOTE
-----------
This chains off `f2a91c4e7b30` (agg_cx_daily). At the time it was written the
lineage had TWO heads — `f2a91c4e7b30` and `a3d82f5c1e94` (agg_loyalty_daily),
both children of `e1c5b7a04d92` — because two rollups landed concurrently.
Chaining off one of them leaves two heads rather than three; linearising the
pair is a rebase of `down_revision`, and nothing in this revision depends on
anything either sibling creates.

Revision ID: b7e40c9a2d15
Revises: f2a91c4e7b30
Create Date: 2026-07-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b7e40c9a2d15'
down_revision: Union[str, None] = 'f2a91c4e7b30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = 'agg_basket_pair_daily'


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('bucket_date', sa.Date(), nullable=False),
        sa.Column('tz_generation', sa.SmallInteger(), server_default='1', nullable=False),
        sa.Column(
            'computed_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('product_a_id', sa.Integer(), nullable=False),
        sa.Column('product_b_id', sa.Integer(), nullable=False),
        sa.Column('product_a_name', sa.String(length=255), server_default='-', nullable=False),
        sa.Column('product_b_name', sa.String(length=255), server_default='-', nullable=False),
        sa.Column('pair_orders', sa.Integer(), server_default='0', nullable=False),
        sa.Column('orders_with_a', sa.Integer(), server_default='0', nullable=False),
        sa.Column('orders_with_b', sa.Integer(), server_default='0', nullable=False),
        sa.Column('total_orders_in_bucket', sa.Integer(), server_default='0', nullable=False),
        sa.Column('orders_with_any_pair', sa.Integer(), server_default='0', nullable=False),
        sa.Column('orders_skipped_over_cap', sa.Integer(), server_default='0', nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'bucket_date',
            'product_a_id',
            'product_b_id',
            'tz_generation',
            name='uq_agg_basket_pair_daily_key',
        ),
    )
    op.create_index(
        op.f('ix_agg_basket_pair_daily_bucket_date'), TABLE_NAME, ['bucket_date'], unique=False
    )
    op.create_index(
        'ix_agg_basket_pair_daily_bucket_pair_orders',
        TABLE_NAME,
        ['bucket_date', 'pair_orders'],
        unique=False,
    )
    op.create_index(
        'ix_agg_basket_pair_daily_product_a',
        TABLE_NAME,
        ['product_a_id', 'bucket_date'],
        unique=False,
    )
    op.create_index(
        'ix_agg_basket_pair_daily_product_b',
        TABLE_NAME,
        ['product_b_id', 'bucket_date'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_agg_basket_pair_daily_product_b', table_name=TABLE_NAME)
    op.drop_index('ix_agg_basket_pair_daily_product_a', table_name=TABLE_NAME)
    op.drop_index('ix_agg_basket_pair_daily_bucket_pair_orders', table_name=TABLE_NAME)
    op.drop_index(op.f('ix_agg_basket_pair_daily_bucket_date'), table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
