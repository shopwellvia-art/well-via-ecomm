"""add agg_ga4_daily: the GA4 Data API landing table

Creates ONE table and nothing else. Purely additive: no existing table, column,
constraint or index is touched, so this is safe to apply ahead of the code that
writes it, and `ANALYTICS_ROLLUPS_ENABLED` keeps the ingest job inert until it is
deliberately enabled.

WHAT IT IS FOR
--------------
The repository had a GA4 **write** path (`analytics/ga4.py`, Measurement
Protocol) and no read path at all, so roughly a dozen traffic/behaviour views
were `INTEGRATION_REQUIRED` for a reason no amount of credential configuration
could fix. `agg_ga4_daily` is where the new Data API client
(`analytics/ga4_data_api.py`) lands what `runReport` returns, one row per
(store-local day, channel group, source/medium, device category, landing page,
tz generation).

WHY THE TABLE LOOKS LIKE THIS
-----------------------------
Full reasoning lives in `app/models/analytics_ga4.py`; the four decisions that
are visible in the DDL and expensive to change later:

* **No revenue column, and there must never be one.** GA4's revenue is browser-
  tag revenue — lossy to ad blockers, double-counted against the server outbox,
  and not the ledger. Money comes from `agg_order_daily`. `quality` is capped at
  `ACTUAL` and can never be `AUTHORITATIVE`.

* **`sum_engagement_seconds` + `n_engagement`, never an average.** An average of
  averages is wrong the moment a daily bucket is re-bucketed to a week, and it is
  wrong silently. Same rule as `sum_delivery_seconds` / `n_delivery`.

* **Every dimension in the UNIQUE key is NOT NULL with a `'-'` server default.**
  MySQL permits unlimited NULLs under a UNIQUE index, so one nullable dimension
  would turn the idempotency key into a suggestion — and this table is re-pulled
  every night for its trailing provisional window, so the doubling would be
  daily rather than hypothetical. At utf8mb4 the key is 1921 bytes, inside
  InnoDB's 3072-byte limit; `landing_page` is 255 chars for exactly that reason.

* **Provisional, sampling and thresholding markers are columns, not folklore.**
  GA4 revises a day for up to 48 hours (`is_provisional`, `final_after`), it can
  answer from a sample (`is_sampled` + the two sampling counts, never their
  ratio), and it withholds rows below a privacy threshold (`is_thresholded`) —
  in which case the day's totals are a floor and the missing rows are absences,
  not zeros. `tz_mismatch` / `property_timezone` / `ga4_date` record that GA4
  buckets by the PROPERTY's timezone, which need not be `store.timezone`.

INDEXES
-------
Three, all declared on the ORM model so `--autogenerate` will not propose
dropping them:

* `ix_agg_ga4_daily_bucket_date` — every read is a date range.
* `ix_agg_ga4_daily_bucket_date_channel_group` — the most-read breakdown.
* `ix_agg_ga4_daily_bucket_date_tz_generation` — `guard_tz_generation` runs
  `SELECT DISTINCT tz_generation ... WHERE bucket_date >= ? AND < ?` on every
  read of a view backed by this table. Declared from the start rather than
  retrofitted the way `agg_customer_snapshot`'s had to be in revision
  e1c5b7a04d92, which measured that same query at up to 341 ms.

TWIN SQL
--------
`backend/scripts/sql/2026-07-29_agg_ga4_daily.sql` was generated from this
revision with

    alembic upgrade d51e8072ca43:c9f13ab6e207 --sql

because production applies hand-reviewed SQL and never runs alembic (DEPLOY.md
§6). Regenerate it the same way if this revision changes; the two artifacts must
not be able to disagree.

CHAINING
--------
`d51e8072ca43` was the deeper of the two heads present when this was written —
several rollups were landing concurrently. Chaining onto it rather than onto the
other head keeps the branch count where it was instead of adding a third.

Revision ID: c9f13ab6e207
Revises: d51e8072ca43
Create Date: 2026-07-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c9f13ab6e207'
down_revision: Union[str, None] = 'd51e8072ca43'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'agg_ga4_daily',
        sa.Column('channel_group', sa.String(length=64), server_default='-', nullable=False),
        sa.Column('source_medium', sa.String(length=128), server_default='-', nullable=False),
        sa.Column('device_category', sa.String(length=32), server_default='-', nullable=False),
        sa.Column('landing_page', sa.String(length=255), server_default='-', nullable=False),
        sa.Column('sessions', sa.Integer(), server_default='0', nullable=False),
        sa.Column('engaged_sessions', sa.Integer(), server_default='0', nullable=False),
        sa.Column('total_users', sa.Integer(), server_default='0', nullable=False),
        sa.Column('new_users', sa.Integer(), server_default='0', nullable=False),
        sa.Column('screen_page_views', sa.Integer(), server_default='0', nullable=False),
        sa.Column('sum_engagement_seconds', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('n_engagement', sa.Integer(), server_default='0', nullable=False),
        sa.Column('ga4_date', sa.String(length=8), server_default='-', nullable=False),
        sa.Column('property_timezone', sa.String(length=64), server_default='-', nullable=False),
        sa.Column('tz_mismatch', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('is_provisional', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('final_after', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_sampled', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('samples_read_count', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('sampling_space_size', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('is_thresholded', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('data_loss_high_cardinality', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('quality', sa.String(length=16), server_default='ACTUAL', nullable=False),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('bucket_date', sa.Date(), nullable=False),
        sa.Column('tz_generation', sa.SmallInteger(), server_default='1', nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'bucket_date',
            'channel_group',
            'source_medium',
            'device_category',
            'landing_page',
            'tz_generation',
            name='uq_agg_ga4_daily_key',
        ),
    )
    op.create_index(op.f('ix_agg_ga4_daily_bucket_date'), 'agg_ga4_daily', ['bucket_date'], unique=False)
    op.create_index('ix_agg_ga4_daily_bucket_date_channel_group', 'agg_ga4_daily', ['bucket_date', 'channel_group'], unique=False)
    op.create_index('ix_agg_ga4_daily_bucket_date_tz_generation', 'agg_ga4_daily', ['bucket_date', 'tz_generation'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_agg_ga4_daily_bucket_date_tz_generation', table_name='agg_ga4_daily')
    op.drop_index('ix_agg_ga4_daily_bucket_date_channel_group', table_name='agg_ga4_daily')
    op.drop_index(op.f('ix_agg_ga4_daily_bucket_date'), table_name='agg_ga4_daily')
    op.drop_table('agg_ga4_daily')
