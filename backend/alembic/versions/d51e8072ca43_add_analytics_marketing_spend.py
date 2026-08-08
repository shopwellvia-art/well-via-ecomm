"""add analytics_marketing_spend — hand-entered ad spend, daily or monthly

Creates ONE table and nothing else. Purely additive: no existing table, column,
constraint or index is touched, so it is safe to apply one deploy ahead of the
code that reads it, and rolling it back has no data implications for anything
else.

WHY THIS TABLE EXISTS
---------------------
`analytics_cost_rules` can already express marketing spend as a PER_MONTH rule,
and `margin.py` reads exactly that for CM3 and CAC. This table exists because a
cost rule is a rate card and spend is a ledger of observations: it carries a
channel and a campaign (a rule's `scope_value` has no marketing axis, so a
blended store-level rule can never produce per-channel ROAS), it records two
grains (a store running its own ads knows Meta day by day; a store whose agency
invoices monthly knows "₹40,000 on Meta in June" and nothing finer), and it is
upsertable so an ads API can later replace a human's estimate for a month
without producing two rows that disagree about it.

The full rationale for every column is in `app/models/analytics_spend.py`. The
parts that constrain THIS DDL:

  * `channel` and `campaign` are `NOT NULL DEFAULT '-'` because both sit inside
    the UNIQUE key. MySQL permits unlimited NULLs under a UNIQUE index, so a
    nullable dimension would not enforce one-row-per-(period, channel, campaign)
    and a repeated ads-API sync would insert a second June/Meta row instead of
    replacing the first. `'-'` is also a real value here: in `campaign` it means
    the channel total, in `channel` it means unattributed marketing.
  * `uq_analytics_marketing_spend_key` includes `tz_generation`, so a
    reporting-timezone rebuild can write the same periods under a new generation
    rather than colliding with the live rows.
  * `amount` is `DECIMAL(14,2) NOT NULL` with **no default**. Every other
    analytics money column defaults to 0 because 0 there means "an aggregation
    job summed the source rows and they came to zero". Here the number is typed
    by a person, an omitted amount is not zero, and a defaulted 0 would report
    free advertising — the direction of error nobody investigates. The database
    refuses the row instead.
  * No FOREIGN KEY anywhere, including `entered_by_user_id`. Same reasoning as
    every other analytics table and as `audit.py`: the record of who entered a
    financial figure must outlive the account.
  * Both `period_start` and `period_end` are stored and both are inclusive. A
    monthly row carries the 1st and the last day of its month, so allocation
    never re-derives the month length and a range query never guesses the grain.

TWIN SQL
--------
`backend/scripts/sql/2026-07-29_analytics_marketing_spend.sql` is generated from
this revision with

    alembic upgrade b7e40c9a2d15:d51e8072ca43 --sql

because production applies hand-reviewed SQL and never runs alembic (DEPLOY.md
§6). The generated `alembic_version` UPDATE is stripped from it: the shared
remote DB is on the `conpay001` lineage this repo does not contain, and stamping
it with a revision id from this chain would corrupt its migration state.
Regenerate the same way if this revision changes — the two artifacts must not be
able to disagree.

BRANCH NOTE
-----------
`down_revision` is `b7e40c9a2d15`, the head observed when this was written. The
repo currently has TWO heads (`a3d82f5c1e94` and `b7e40c9a2d15`, both branching
from `e1c5b7a04d92`) because several additive analytics revisions landed in
parallel; this one chains off the deeper of the two rather than adding a third
branch point. Both are pure `CREATE TABLE`s, so the branch is a bookkeeping
issue rather than an ordering hazard, and a later `alembic merge` resolves it.

Revision ID: d51e8072ca43
Revises: b7e40c9a2d15
Create Date: 2026-07-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd51e8072ca43'
down_revision: Union[str, None] = 'b7e40c9a2d15'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = 'analytics_marketing_spend'


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column('grain', sa.String(length=8), server_default='daily', nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('channel', sa.String(length=48), server_default='-', nullable=False),
        sa.Column('campaign', sa.String(length=96), server_default='-', nullable=False),
        sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('currency', sa.String(length=3), server_default='INR', nullable=False),
        sa.Column(
            'quality', sa.String(length=16), server_default='assumed', nullable=False
        ),
        sa.Column(
            'source', sa.String(length=64), server_default='manual', nullable=False
        ),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('entered_by_user_id', sa.Integer(), nullable=True),
        sa.Column('tz_generation', sa.SmallInteger(), server_default='1', nullable=False),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'grain',
            'period_start',
            'channel',
            'campaign',
            'tz_generation',
            name='uq_analytics_marketing_spend_key',
        ),
    )
    op.create_index(
        op.f('ix_analytics_marketing_spend_period_start'),
        TABLE_NAME,
        ['period_start'],
        unique=False,
    )
    op.create_index(
        'ix_analytics_marketing_spend_period',
        TABLE_NAME,
        ['period_start', 'period_end'],
        unique=False,
    )
    op.create_index(
        'ix_analytics_marketing_spend_channel_period',
        TABLE_NAME,
        ['channel', 'period_start'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_analytics_marketing_spend_channel_period', table_name=TABLE_NAME)
    op.drop_index('ix_analytics_marketing_spend_period', table_name=TABLE_NAME)
    op.drop_index(
        op.f('ix_analytics_marketing_spend_period_start'), table_name=TABLE_NAME
    )
    op.drop_table(TABLE_NAME)
