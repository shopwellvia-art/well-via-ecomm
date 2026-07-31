"""add agg_cx_daily: the reviews + inbound-contact rollup

Creates ONE table, ``agg_cx_daily``, and its three indexes. Purely additive: no
existing table, column, constraint or index is touched, so this is safe to apply
ahead of the code that reads it and safe to roll back with no data implications
(the table is a derived rollup — every row can be rebuilt from ``reviews`` and
``contact_messages`` by re-running the ``cx_daily`` job).

WHAT IT HOLDS
-------------
One row per (store-local reporting day, product) for reviews, plus one
store-wide row per day at ``product_id = 0`` carrying inbound contact volume —
``contact_messages`` has no product, and 0 is a sentinel MySQL AUTO_INCREMENT
never issues. The two column families are disjoint per row, so summing either
one over any window is correct without filtering. See
``app/models/analytics_cx.py`` for the full rationale, including why the rating
distribution is five counts plus ``rating_sum`` / ``rated_reviews`` rather than a
stored ``avg_rating``: an average cannot be re-bucketed, and averaging daily
averages into a week is wrong silently.

MANUALLY WRITTEN — DO NOT REGENERATE WITH --autogenerate.
Revision d7f3a9c2e814 documents why: autogenerate on this repo proposes ~23
destructive operations against existing tables because the models and the
migration history have drifted. This revision was written by hand against
``AggCxDaily`` and contains exactly the one CREATE TABLE and three CREATE INDEXes
that model declares.

TWIN SQL
--------
``backend/scripts/sql/2026-07-29_agg_cx_daily.sql`` was generated from this
revision with

    alembic upgrade e1c5b7a04d92:f2a91c4e7b30 --sql

with the ``alembic_version`` stamp stripped, because production applies
hand-reviewed SQL and never runs alembic (DEPLOY.md §6). Regenerate it the same
way if this revision changes; ``tests/test_analytics_cx_rollup.py`` applies the
twin to a shadow table and asserts it reflects identically to the table this
revision built, so the two artifacts cannot silently disagree.

Revision ID: f2a91c4e7b30
Revises: e1c5b7a04d92
Create Date: 2026-07-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f2a91c4e7b30'
down_revision: Union[str, None] = 'e1c5b7a04d92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = 'agg_cx_daily'


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('sku_snapshot', sa.String(length=64), server_default='-', nullable=False),
        sa.Column('category_id_snapshot', sa.Integer(), nullable=True),
        sa.Column('reviews_submitted', sa.Integer(), server_default='0', nullable=False),
        sa.Column('reviews_approved', sa.Integer(), server_default='0', nullable=False),
        sa.Column('reviews_unapproved', sa.Integer(), server_default='0', nullable=False),
        sa.Column('rating_1', sa.Integer(), server_default='0', nullable=False),
        sa.Column('rating_2', sa.Integer(), server_default='0', nullable=False),
        sa.Column('rating_3', sa.Integer(), server_default='0', nullable=False),
        sa.Column('rating_4', sa.Integer(), server_default='0', nullable=False),
        sa.Column('rating_5', sa.Integer(), server_default='0', nullable=False),
        sa.Column('rating_sum', sa.Integer(), server_default='0', nullable=False),
        sa.Column('rated_reviews', sa.Integer(), server_default='0', nullable=False),
        sa.Column('verified_purchase_reviews', sa.Integer(), server_default='0', nullable=False),
        sa.Column('helpful_votes', sa.Integer(), server_default='0', nullable=False),
        sa.Column('messages_received', sa.Integer(), server_default='0', nullable=False),
        sa.Column('messages_new', sa.Integer(), server_default='0', nullable=False),
        sa.Column('messages_replied', sa.Integer(), server_default='0', nullable=False),
        sa.Column('messages_closed', sa.Integer(), server_default='0', nullable=False),
        sa.Column('messages_other', sa.Integer(), server_default='0', nullable=False),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('bucket_date', sa.Date(), nullable=False),
        sa.Column('tz_generation', sa.SmallInteger(), server_default='1', nullable=False),
        sa.Column(
            'computed_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'bucket_date', 'product_id', 'tz_generation', name='uq_agg_cx_daily_key'
        ),
    )
    op.create_index(
        op.f('ix_agg_cx_daily_bucket_date'), TABLE_NAME, ['bucket_date'], unique=False
    )
    op.create_index(
        'ix_agg_cx_daily_bucket_date_category',
        TABLE_NAME,
        ['bucket_date', 'category_id_snapshot'],
        unique=False,
    )
    op.create_index(
        'ix_agg_cx_daily_product_bucket_date',
        TABLE_NAME,
        ['product_id', 'bucket_date'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_agg_cx_daily_product_bucket_date', table_name=TABLE_NAME)
    op.drop_index('ix_agg_cx_daily_bucket_date_category', table_name=TABLE_NAME)
    op.drop_index(op.f('ix_agg_cx_daily_bucket_date'), table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
