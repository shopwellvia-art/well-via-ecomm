"""add agg_loyalty_daily — the loyalty points rollup

Creates ONE table and nothing else. Purely additive: no existing table, column,
constraint or index is touched, so this is safe to apply ahead of the code that
reads it and rolls back with no data implications (the table is derived and
disposable, like every other `agg_*`).

WHAT IT IS
----------
`agg_loyalty_daily` is the read side of `points_transactions`, at one row per
(store-local reporting day, ledger `reason`, tz generation). It is written by
`analytics.aggregation.jobs_loyalty.LoyaltyDailyJob` under Pattern B
(delete-and-reinsert) and read by view 52, Loyalty and Rewards. The full
rationale for every column lives in `app/models/analytics_loyalty.py`; the parts
that constrain THIS DDL are:

  * `reason` is `VARCHAR(32) NOT NULL DEFAULT '-'` because it sits inside the
    UNIQUE key. MySQL permits unlimited NULLs under a UNIQUE index, so a
    nullable dimension would not enforce one-row-per-(day, reason) and the job's
    next run would insert a second row instead of replacing the first. The `'-'`
    sentinel is also a real value here: it is the store-level row carrying
    `referral_completions` and `points_outstanding_close`, the two measures that
    belong to the day rather than to a reason.
  * `uq_agg_loyalty_daily_key` includes `tz_generation`. That constraint IS the
    job's idempotency key, and rows computed under two different reporting
    timezones must be able to coexist rather than collide.
  * No FOREIGN KEY anywhere. Rollups stay independently TRUNCATE-able and
    rebuildable; deleting a user must not be a referential-integrity problem for
    a report, and must not erase the points history either.
  * Every counter is `INT NOT NULL DEFAULT 0`. `points_earned` and
    `points_debited` are non-negative halves of the SIGNED ledger `delta`;
    `net_points` and `points_outstanding_close` are signed and are stored as
    measured rather than clamped.

WHAT IS NOT HERE, DELIBERATELY
------------------------------
No tier column. There is no `redemption_tier_id` on `points_transactions` or on
`coupons` — a redemption's ledger row references the minted coupon, and the only
trace of the tier is a free-text description. A dimension recovered by parsing
that label would re-partition history on a tier rename while looking like a
measurement, so redemptions-by-tier is left unanswered rather than approximated.
Adding it later is an additive migration on the transactional table plus a
forward-only backfill, not a change to this one.

No money column. A point has no stored monetary value; what a redemption is
finally worth is real money on an order and is already measured by
`agg_promo_daily.discount_amount`.

TWIN SQL
--------
`backend/scripts/sql/2026-07-29_agg_loyalty_daily.sql` is generated from this
revision with

    alembic upgrade c9f13ab6e207:a3d82f5c1e94 --sql

because production applies hand-reviewed SQL and never runs alembic (DEPLOY.md
§6). The generated `alembic_version` UPDATE is stripped from it: the shared
remote DB is on the `conpay001` lineage this repo does not contain, and stamping
it with a revision id from this chain would corrupt its migration state.
Regenerate the same way if this revision changes — the two artifacts must not be
able to disagree.

NOTE the range above starts at `c9f13ab6e207`, not at the `e1c5b7a04d92` this
revision was originally authored against. `down_revision` was re-chained to
linearise six parallel analytics migrations (see the comment on it below). Using
the old range would now walk the whole sibling branch and emit five other
tables' DDL into this file's twin. The generated SQL is byte-identical either
way for THIS table — only the range changes.

Revision ID: a3d82f5c1e94
Revises: c9f13ab6e207
Create Date: 2026-07-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a3d82f5c1e94'
# Re-chained by the lead from 'e1c5b7a04d92' to 'c9f13ab6e207'.
#
# Six analytics rollups were authored in parallel and every one of them chained
# off the then-current head, `e1c5b7a04d92`. That produced TWO heads. Alembic
# does not reject that: `alembic upgrade head` fails outright with
# `CommandError: Multiple head revisions are present`, which breaks CI's
# migration step and therefore every backend test. This repo has been bitten by
# a forked chain before (two files declaring revision `a2v3w4x5y6z7`).
#
# Repointing THIS revision at the tip of the other branch linearises all six
# with a single edit. It is safe because the branches are disjoint and purely
# additive: each creates its own new table and touches nothing the others do, so
# ordering between them carries no data dependency. The DDL below is unchanged.
down_revision: Union[str, None] = 'c9f13ab6e207'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = 'agg_loyalty_daily'


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column('reason', sa.String(length=32), server_default='-', nullable=False),
        sa.Column('points_earned', sa.Integer(), server_default='0', nullable=False),
        sa.Column('points_debited', sa.Integer(), server_default='0', nullable=False),
        sa.Column('points_redeemed', sa.Integer(), server_default='0', nullable=False),
        sa.Column('points_expired', sa.Integer(), server_default='0', nullable=False),
        sa.Column('points_reversed', sa.Integer(), server_default='0', nullable=False),
        sa.Column('net_points', sa.Integer(), server_default='0', nullable=False),
        sa.Column('transactions', sa.Integer(), server_default='0', nullable=False),
        sa.Column('distinct_customers', sa.Integer(), server_default='0', nullable=False),
        sa.Column('referral_completions', sa.Integer(), server_default='0', nullable=False),
        sa.Column(
            'points_outstanding_close', sa.Integer(), server_default='0', nullable=False
        ),
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
            'bucket_date', 'reason', 'tz_generation', name='uq_agg_loyalty_daily_key'
        ),
    )
    op.create_index(
        op.f('ix_agg_loyalty_daily_bucket_date'), TABLE_NAME, ['bucket_date'], unique=False
    )
    op.create_index(
        'ix_agg_loyalty_daily_bucket_date_reason',
        TABLE_NAME,
        ['bucket_date', 'reason'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_agg_loyalty_daily_bucket_date_reason', table_name=TABLE_NAME)
    op.drop_index(op.f('ix_agg_loyalty_daily_bucket_date'), table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
