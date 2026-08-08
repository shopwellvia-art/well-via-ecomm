-- Manual DDL for the shared remote MySQL (applied in a reviewed window, NOT via
-- `alembic upgrade` — the live lineage diverges from the repo; see DEPLOY.md §6).
-- Mirrors alembic revision c9f13ab6e207 EXACTLY: this file was generated from it
-- with `alembic upgrade d51e8072ca43:c9f13ab6e207 --sql`, so the two artifacts
-- cannot silently disagree. Regenerate the same way if the revision changes.
--
-- WHAT THIS DOES
--   Creates ONE new table, agg_ga4_daily, plus its three indexes. Nothing
--   existing is touched: no column, no constraint, no index, no data.
--
-- WHY
--   The codebase had a GA4 *write* path only — `analytics/ga4.py` speaks the
--   Measurement Protocol, which sends events and cannot read what GA4 recorded.
--   Roughly a dozen traffic and behaviour views were therefore
--   INTEGRATION_REQUIRED for a reason no amount of credential configuration
--   could fix. This is the landing table for the new Data API client
--   (`analytics/ga4_data_api.py`), written by the `ga4_daily` aggregation job.
--
--   Grain: one row per (store-local reporting day, channel group, source/medium,
--   device category, landing page, tz generation).
--
-- WHAT IS DELIBERATELY ABSENT
--   There is no revenue column and there must never be one. GA4's revenue is
--   browser-tag revenue: lossy to ad blockers, double-counted against the
--   server-side outbox, and not the ledger. Money comes from agg_order_daily.
--   The `quality` column is capped at 'ACTUAL' by the writer and can never be
--   'AUTHORITATIVE'.
--
--   There is no stored average and no stored rate. `sum_engagement_seconds` is
--   paired with `n_engagement` so the average is recomputable at any
--   granularity; bounce rate is (sessions - engaged_sessions) / sessions,
--   computed at query time.
--
-- THE COLUMNS THAT ARE NOT MEASUREMENTS
--   Six columns record how much the measurements are worth, and they are the
--   reason this table can be trusted at all:
--
--     is_provisional / final_after
--       GA4 revises a day for up to 48 hours after it ends. A first read is
--       useful and not final. The ingest job re-pulls the trailing provisional
--       window and REPLACES the day (delete-and-reinsert), so a re-pull restates
--       rather than doubles.
--
--     is_sampled / samples_read_count / sampling_space_size
--       GA4 may answer from a subset of sessions and scale up. The two counts
--       are stored and their ratio is not — a stored percentage cannot be
--       re-aggregated.
--
--     is_thresholded
--       GA4 WITHHOLDS rows below a privacy threshold. Those rows are absent, not
--       zero, so a thresholded day's totals are a FLOOR. Nothing in the ingest
--       path fabricates a zero row for a dimension GA4 declined to return.
--
--     data_loss_high_cardinality
--       The dimension combination overflowed GA4's cardinality limit and the
--       tail was folded into '(other)', so the breakdown does not add to the
--       property total.
--
--     tz_mismatch / property_timezone / ga4_date
--       GA4 buckets by the timezone configured on the PROPERTY, which is set in
--       a different console and need not equal store.timezone. When they differ,
--       a row filed under a store-local bucket_date is really a property-local
--       day. It is recorded rather than silently reconciled, because treating a
--       New York day as an IST day shifts a fraction of every day's traffic into
--       the neighbouring bucket — the exact defect tz_generation exists for.
--
-- THE UNIQUE KEY IS THE IDEMPOTENCY KEY
--   uq_agg_ga4_daily_key covers (bucket_date, channel_group, source_medium,
--   device_category, landing_page, tz_generation). Every one of those dimension
--   columns is NOT NULL with a '-' default, because MySQL permits unlimited
--   NULLs under a UNIQUE index: one nullable dimension would let the nightly
--   provisional re-pull INSERT a second row instead of replacing the first, and
--   the doubling would be daily rather than hypothetical.
--
--   At utf8mb4 the key is 1921 bytes (3 + 256 + 512 + 128 + 1020 + 2), inside
--   InnoDB's 3072-byte limit with DYNAMIC row format. landing_page is capped at
--   255 characters for precisely that reason; the writer truncates and reports
--   how many rows it truncated rather than doing it silently.
--
-- INDEXES
--   ix_agg_ga4_daily_bucket_date                 every read is a date range
--   ix_agg_ga4_daily_bucket_date_channel_group   the most-read breakdown
--   ix_agg_ga4_daily_bucket_date_tz_generation   AnalyticsRepository
--     .distinct_tz_generations runs
--       SELECT DISTINCT tz_generation FROM agg_ga4_daily
--        WHERE bucket_date >= ? AND bucket_date < ?
--     on EVERY read of a view backed by this table (probe_source ->
--     guard_tz_generation). Declared from the start rather than retrofitted the
--     way agg_customer_snapshot's had to be (revision e1c5b7a04d92 /
--     2026-07-28_agg_customer_snapshot_tz_index.sql), where the same query was
--     measured at up to 341 ms.
--
-- SAFETY
--   Purely additive — a CREATE TABLE for a table that does not exist. Safe to
--   apply BEFORE the code deploy (additive-first): nothing reads it until the
--   `ga4_daily` job runs, and that job is gated by ANALYTICS_ROLLUPS_ENABLED
--   AND no-ops when the Data API credentials are absent, which they are until an
--   admin pastes a service-account key.
--
--   Re-running fails harmlessly with "Table 'agg_ga4_daily' already exists" and
--   changes nothing. Verify first if unsure:
--     SHOW TABLES LIKE 'agg_ga4_daily';
--
--   No lock concern: the table does not exist yet, so there is nothing to lock
--   and nothing to rebuild. This is milliseconds regardless of database size.
--
-- BEFORE RUNNING
--   Take a backup: backend/scripts/backup_db.sh
--
-- PREREQUISITE
--   None. This table has no foreign keys (by design — see
--   app/models/analytics_base.py) and does not reference any other table, so it
--   can be applied independently of the rest of the analytics schema.
--
-- NOTE ON alembic_version
--   The generated statement that stamps alembic_version has been REMOVED on
--   purpose. The shared remote DB is on the `conpay001` lineage which this repo
--   does not contain; stamping it with a revision id from this repo's chain
--   would corrupt its migration state. Nothing here needs alembic to know.
--
-- ROLLBACK
--   The table is written only by the `ga4_daily` job and read only by views that
--   are not bound to it yet, so leaving it in place is safe and empty. To remove
--   it (this DESTROYS any ingested GA4 history, which is re-pullable from GA4
--   for the standard 14-month retention window and not beyond):
--     DROP TABLE agg_ga4_daily;

-- Running upgrade d51e8072ca43 -> c9f13ab6e207

CREATE TABLE agg_ga4_daily (
    channel_group VARCHAR(64) NOT NULL DEFAULT '-',
    source_medium VARCHAR(128) NOT NULL DEFAULT '-',
    device_category VARCHAR(32) NOT NULL DEFAULT '-',
    landing_page VARCHAR(255) NOT NULL DEFAULT '-',
    sessions INTEGER NOT NULL DEFAULT '0',
    engaged_sessions INTEGER NOT NULL DEFAULT '0',
    total_users INTEGER NOT NULL DEFAULT '0',
    new_users INTEGER NOT NULL DEFAULT '0',
    screen_page_views INTEGER NOT NULL DEFAULT '0',
    sum_engagement_seconds BIGINT NOT NULL DEFAULT '0',
    n_engagement INTEGER NOT NULL DEFAULT '0',
    ga4_date VARCHAR(8) NOT NULL DEFAULT '-',
    property_timezone VARCHAR(64) NOT NULL DEFAULT '-',
    tz_mismatch BOOL NOT NULL DEFAULT '0',
    is_provisional BOOL NOT NULL DEFAULT '1',
    final_after DATETIME,
    is_sampled BOOL NOT NULL DEFAULT '0',
    samples_read_count BIGINT NOT NULL DEFAULT '0',
    sampling_space_size BIGINT NOT NULL DEFAULT '0',
    is_thresholded BOOL NOT NULL DEFAULT '0',
    data_loss_high_cardinality BOOL NOT NULL DEFAULT '0',
    quality VARCHAR(16) NOT NULL DEFAULT 'ACTUAL',
    id BIGINT NOT NULL AUTO_INCREMENT,
    bucket_date DATE NOT NULL,
    tz_generation SMALLINT NOT NULL DEFAULT '1',
    computed_at DATETIME NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    CONSTRAINT uq_agg_ga4_daily_key UNIQUE (bucket_date, channel_group, source_medium, device_category, landing_page, tz_generation)
);

CREATE INDEX ix_agg_ga4_daily_bucket_date ON agg_ga4_daily (bucket_date);

CREATE INDEX ix_agg_ga4_daily_bucket_date_channel_group ON agg_ga4_daily (bucket_date, channel_group);

CREATE INDEX ix_agg_ga4_daily_bucket_date_tz_generation ON agg_ga4_daily (bucket_date, tz_generation);
