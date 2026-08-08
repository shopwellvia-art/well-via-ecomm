"""add (bucket_date, tz_generation) index to agg_customer_snapshot

One index, on the one read-path query that measurement showed paying for it.

WHY
---
`AnalyticsRepository.distinct_tz_generations` (analytics_repository.py:398) runs

    SELECT DISTINCT tz_generation FROM agg_customer_snapshot
     WHERE bucket_date >= ? AND bucket_date < ?

on **every** read of a view backed by this table, through
`probe_source` -> `guard_tz_generation`. The guard is right to exist — mixing
rows computed under two different day boundaries is the one error nothing
downstream can detect — but on the largest rollup it was costing more than the
query it protects.

`docs/analytics/PERFORMANCE.md` §5.4 measured it at up to **341 ms** and
EXPLAINed it against the 327 400-row table:

    type: range   key: uq_agg_customer_snapshot_key   key_len: 3   rows: 167373

167 373 index entries scanned to return one distinct value. The UNIQUE key is
`(bucket_date, customer_key, tz_generation)`, so `customer_key` sits between the
only two columns this query touches and MySQL cannot do a loose index scan. It
is why `customers/customer-segmentation` (547 ms, 425 ms of it SQL, 4 queries)
and `customers/rfm-customer-analysis` (353 ms, 346 ms SQL, 4 queries) were the
two slowest non-margin views measured.

`(bucket_date, tz_generation)` is a covering prefix for that query, so it becomes
a loose index scan over one entry per (day, generation) instead of one per
customer per day.

WHAT IT COSTS
-------------
Write amplification on the one table that writes most: `customer_snapshot`
inserts ~11 000 rows per bucket at the measured volume, and each now maintains a
fourth secondary index. The index is two small columns (DATE + SMALLINT) and the
job's INSERT was measured at 274 ms per bucket, so this is a few per cent of a
write path that runs once a day against a read path that runs on every dashboard
request.

Additive and reversible: no column, constraint or existing index is touched, so
this can be applied ahead of any code and rolled back with no data implications.

NOT DECLARED ON THE ORM MODEL
-----------------------------
`app/models/analytics_rollups.py` is not changed here, so the model does not
declare this index. That is deliberate under the current split of ownership, and
it is safe in both directions the schema test checks: `test_indexes_present_in_
database` asserts ORM-declared indexes exist in the database, not the converse,
and `tests/test_analytics_perf_fixes.py` asserts this one exists by reflection so
it cannot be silently dropped.

It does mean `alembic --autogenerate` will propose dropping it. That is already
true of a great deal in this repo — see revision d7f3a9c2e814, which had to
strip 23 destructive operations autogenerate proposed against existing tables —
and the standing rule is that generated migrations are reviewed line by line.
The tidy-up is to add

    Index("ix_agg_customer_snapshot_bucket_date_tz_generation",
          "bucket_date", "tz_generation")

to `AggCustomerSnapshot.__table_args__` in the same change that next touches
that model.

TWIN SQL
--------
`backend/scripts/sql/2026-07-28_agg_customer_snapshot_tz_index.sql` was generated
from this revision with

    alembic upgrade d7f3a9c2e814:e1c5b7a04d92 --sql

because production applies hand-reviewed SQL and never runs alembic (DEPLOY.md
§6). Regenerate it the same way if this revision changes; the two artifacts must
not be able to disagree.

Revision ID: e1c5b7a04d92
Revises: d7f3a9c2e814
Create Date: 2026-07-28
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'e1c5b7a04d92'
down_revision: Union[str, None] = 'd7f3a9c2e814'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = 'ix_agg_customer_snapshot_bucket_date_tz_generation'
TABLE_NAME = 'agg_customer_snapshot'


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        TABLE_NAME,
        ['bucket_date', 'tz_generation'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name=TABLE_NAME)
