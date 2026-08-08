-- Manual DDL for the shared remote MySQL (applied in a reviewed window, NOT via
-- `alembic upgrade` — the live lineage diverges from the repo; see DEPLOY.md §6).
-- Mirrors alembic revision a3d82f5c1e94 EXACTLY: this file was generated from it
-- with `alembic upgrade e1c5b7a04d92:a3d82f5c1e94 --sql`, so the two artifacts
-- cannot silently disagree. Regenerate the same way if the revision changes.
--
-- WHAT THIS DOES
--   Creates ONE table, agg_loyalty_daily. No existing table, column, constraint
--   or index is touched.
--
-- WHY
--   It is the read side of points_transactions: one row per (store-local
--   reporting day, ledger `reason`, tz generation), written by the
--   `loyalty_daily` aggregation job and read by view 52 (Loyalty and Rewards).
--   Before it, points and referrals were recorded and nothing aggregated them,
--   so the view had no source and showed nothing.
--
-- THE PARTS OF THIS DDL THAT ARE LOAD-BEARING
--   * `reason VARCHAR(32) NOT NULL DEFAULT '-'` — it is inside the UNIQUE key.
--     MySQL permits UNLIMITED NULLs under a UNIQUE index, so a nullable
--     dimension would not enforce one-row-per-(day, reason): the job's
--     delete-and-reinsert would still be correct, but any upsert path added
--     later would silently double-count. The '-' sentinel is also a REAL value
--     here — it is the store-level row carrying referral_completions and
--     points_outstanding_close, the two measures that belong to the day rather
--     than to a reason. points_transactions.reason is NOT NULL, so no ledger row
--     can ever collide with it.
--   * `uq_agg_loyalty_daily_key (bucket_date, reason, tz_generation)` — this
--     constraint IS the job's idempotency key. tz_generation is in it because
--     changing the store's reporting timezone re-buckets every day, and rows
--     from two generations must coexist rather than overwrite each other.
--   * NO FOREIGN KEY, anywhere. Rollups must stay independently TRUNCATE-able
--     and rebuildable; an FK to users would make "recompute March" a
--     referential-integrity problem and would let a deleted account erase its
--     own points history.
--   * Every counter is INT NOT NULL DEFAULT 0. points_earned and points_debited
--     are the two NON-NEGATIVE halves of the SIGNED ledger delta; net_points and
--     points_outstanding_close are signed and stored as measured, never clamped.
--
-- WHAT IS DELIBERATELY ABSENT
--   No tier column. Neither points_transactions nor coupons carries a
--   redemption_tier_id — a REDEEM row references the minted coupon and the only
--   trace of the tier is a free-text description ("Redeemed: {tier.name}").
--   Recovering a dimension by parsing that would re-partition history the first
--   time a tier is renamed while looking exactly like a measurement, so
--   redemptions-by-tier is left unanswered rather than approximated.
--
--   No money column. A point has no stored monetary value; what a redemption is
--   finally worth is real money on an order and is already measured by
--   agg_promo_daily.discount_amount.
--
-- COST
--   Negligible. The grain is at most 8 rows a day (7 ledger reasons + the
--   store-level row), so this table grows by ~3 000 rows a year. The two indexes
--   are narrow (DATE, and DATE+VARCHAR(32)).
--
-- SAFETY
--   Purely additive and safe to apply BEFORE the code deploy (additive-first):
--   nothing writes or reads it until the aggregation job runs.
--
--   CREATE TABLE takes no lock on anything that exists. Re-running fails
--   harmlessly with "Table 'agg_loyalty_daily' already exists" and changes
--   nothing. Verify first if unsure:
--     SHOW TABLES LIKE 'agg_loyalty_daily';
--
-- BEFORE RUNNING
--   Take a backup: backend/scripts/backup_db.sh
--
-- PREREQUISITE
--   None beyond the database itself. This table has no foreign keys and does not
--   depend on 2026-07-28_analytics_v2_schema.sql having been applied — though in
--   practice it is useless without it, since nothing else would be reading
--   rollups.
--
-- AFTER RUNNING
--   The table is EMPTY and the view that reads it reports NO_ROLLUP_YET rather
--   than zeros — which is correct, and is the difference between "not computed"
--   and "nobody earned a point". Backfill it by enqueuing `loyalty_daily`
--   buckets through the analytics admin API; the ledger is append-only, so every
--   past day is reconstructible exactly, including points_outstanding_close.
--
-- NOTE ON alembic_version
--   The generated statement that stamps alembic_version has been REMOVED on
--   purpose. The shared remote DB is on the `conpay001` lineage which this repo
--   does not contain; stamping it with a revision id from this repo's chain
--   would corrupt its migration state. Nothing here needs alembic to know.
--
-- ROLLBACK
--   The table is derived and disposable — no other table references it, so
--   dropping it loses no information that cannot be recomputed from
--   points_transactions and referrals:
--     DROP TABLE agg_loyalty_daily;

-- Running upgrade e1c5b7a04d92 -> a3d82f5c1e94

CREATE TABLE agg_loyalty_daily (
    reason VARCHAR(32) NOT NULL DEFAULT '-',
    points_earned INTEGER NOT NULL DEFAULT '0',
    points_debited INTEGER NOT NULL DEFAULT '0',
    points_redeemed INTEGER NOT NULL DEFAULT '0',
    points_expired INTEGER NOT NULL DEFAULT '0',
    points_reversed INTEGER NOT NULL DEFAULT '0',
    net_points INTEGER NOT NULL DEFAULT '0',
    transactions INTEGER NOT NULL DEFAULT '0',
    distinct_customers INTEGER NOT NULL DEFAULT '0',
    referral_completions INTEGER NOT NULL DEFAULT '0',
    points_outstanding_close INTEGER NOT NULL DEFAULT '0',
    id BIGINT NOT NULL AUTO_INCREMENT,
    bucket_date DATE NOT NULL,
    tz_generation SMALLINT NOT NULL DEFAULT '1',
    computed_at DATETIME NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    CONSTRAINT uq_agg_loyalty_daily_key UNIQUE (bucket_date, reason, tz_generation)
);

CREATE INDEX ix_agg_loyalty_daily_bucket_date ON agg_loyalty_daily (bucket_date);

CREATE INDEX ix_agg_loyalty_daily_bucket_date_reason ON agg_loyalty_daily (bucket_date, reason);
