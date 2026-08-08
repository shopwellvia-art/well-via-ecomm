-- Manual DDL for the shared remote MySQL (applied in a reviewed window, NOT via
-- `alembic upgrade` — the live lineage diverges from the repo; see DEPLOY.md §6).
-- Mirrors alembic revision d51e8072ca43 EXACTLY: this file was generated from it
-- with `alembic upgrade b7e40c9a2d15:d51e8072ca43 --sql`, so the two artifacts
-- cannot silently disagree. Regenerate the same way if the revision changes.
--
-- WHAT THIS DOES
--   Creates ONE table, analytics_marketing_spend. No existing table, column,
--   constraint or index is touched.
--
-- WHY
--   Nothing in this system could record what the store spent on advertising.
--   `analytics_cost_rules` can express a blended PER_MONTH marketing rate, and
--   margin.py reads exactly that for CM3 and CAC — but a cost rule is a rate
--   card, and spend is a ledger of observations. A rule has no channel axis, so
--   it can never produce per-channel ROAS; it has one grain, so a store whose
--   agency invoices monthly cannot record "₹40 000 on Meta in June" without
--   fabricating a daily rate; and it is effective-dated, so an ads API
--   backfilling June later would have to write a *new version* of a fact that
--   never changed. This table holds the observation; the rule stays the rate
--   card. Both feed the same margin cascade.
--
-- THE PARTS OF THIS DDL THAT ARE LOAD-BEARING
--   * `channel VARCHAR(48) NOT NULL DEFAULT '-'` and
--     `campaign VARCHAR(96) NOT NULL DEFAULT '-'` — both sit inside the UNIQUE
--     key. MySQL permits UNLIMITED NULLs under a UNIQUE index, so a nullable
--     dimension would not enforce one-row-per-(period, channel, campaign) and a
--     repeated ads-API sync would insert a second June/Meta row instead of
--     replacing the first — the store's reported spend would double with no
--     error anywhere. The '-' sentinel is also a REAL value: in `campaign` it
--     means the channel total, in `channel` it means unattributed marketing,
--     which is exactly what a store with one agency invoice has.
--   * `uq_analytics_marketing_spend_key (grain, period_start, channel,
--     campaign, tz_generation)` — this constraint IS the idempotency key that
--     makes the write endpoint an upsert. `grain` is in it so a daily Meta feed
--     and a monthly Google invoice can coexist for the same month;
--     `tz_generation` is in it because changing the store's reporting timezone
--     re-buckets every day and rows from two generations must coexist rather
--     than overwrite each other.
--   * `amount DECIMAL(14,2) NOT NULL` with **NO DEFAULT**. Every other analytics
--     money column defaults to 0, because there 0 means "an aggregation job
--     summed the source rows and they came to zero". This number is typed by a
--     person: an omitted amount is not zero, and a column that quietly defaulted
--     it would report free advertising — a margin that reads BETTER than
--     reality, which is the one direction of error nobody investigates. The
--     database refuses the row instead.
--   * NO FOREIGN KEY, anywhere, including `entered_by_user_id`. Same reasoning
--     as every other analytics table and as audit_events.target_id: the record
--     of who entered a financial figure must outlive the account, and deleting a
--     staff user must not take the store's June ad spend with it.
--   * Both `period_start` and `period_end` are stored, both inclusive. A monthly
--     row carries the 1st and the last day of its month, so the daily allocation
--     never re-derives the month length and a range query never has to guess the
--     grain. A DAILY row has period_start = period_end.
--
-- WHAT IS DELIBERATELY ABSENT
--   No clicks, impressions or conversions. Those exist only inside the ad
--   platform and cannot be typed in usefully; a hand-entered impression count
--   would look exactly like a measurement and be fiction. This table carries the
--   one number a human can supply from an invoice — money — and leaves the rest
--   to the ad-platform integration.
--
--   No effective_from/effective_to. Spend is not effective-dated: it measures a
--   period that already happened. Correcting a wrong figure is an UPDATE,
--   because June's total was always whatever it was and the earlier row was
--   simply wrong — unlike a rate, where the old value was correct AT THE TIME
--   and must survive so old reports still reproduce. That asymmetry is the
--   reason analytics_cost_rules is append-only and this table is not.
--
-- COST
--   Negligible. The grain is one row per channel (and optionally campaign) per
--   period, entered by hand: tens of rows a year for most stores, a few thousand
--   if an ads API later syncs daily campaign spend. All three indexes are narrow
--   (DATE, DATE+DATE, VARCHAR(48)+DATE).
--
-- SAFETY
--   Purely additive and safe to apply BEFORE the code deploy (additive-first):
--   nothing writes or reads it until the admin screen ships, and the whole
--   analytics read surface is behind ANALYTICS_V2_ENABLED regardless.
--
--   CREATE TABLE takes no lock on anything that exists. Re-running fails
--   harmlessly with "Table 'analytics_marketing_spend' already exists" and
--   changes nothing. Verify first if unsure:
--     SHOW TABLES LIKE 'analytics_marketing_spend';
--
-- BEFORE RUNNING
--   Take a backup: backend/scripts/backup_db.sh
--
-- PREREQUISITE
--   None beyond the database itself. No foreign keys, and it references nothing.
--
-- NOTE ON alembic_version
--   The generated statement that stamps alembic_version has been REMOVED on
--   purpose. The shared remote DB is on the `conpay001` lineage which this repo
--   does not contain; stamping it with a revision id from this chain would
--   corrupt its migration state. Nothing here needs alembic to know.
--
-- ROLLBACK
--   Additive and unread by existing code, so leaving it in place is safe. To
--   remove it — note this DOES lose data, because the rows are hand-entered and
--   cannot be recomputed from anything:
--     DROP TABLE analytics_marketing_spend;

-- Running upgrade b7e40c9a2d15 -> d51e8072ca43

CREATE TABLE analytics_marketing_spend (
    grain VARCHAR(8) NOT NULL DEFAULT 'daily',
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    channel VARCHAR(48) NOT NULL DEFAULT '-',
    campaign VARCHAR(96) NOT NULL DEFAULT '-',
    amount NUMERIC(14, 2) NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'INR',
    quality VARCHAR(16) NOT NULL DEFAULT 'assumed',
    source VARCHAR(64) NOT NULL DEFAULT 'manual',
    note VARCHAR(255),
    entered_by_user_id INTEGER,
    tz_generation SMALLINT NOT NULL DEFAULT '1',
    id BIGINT NOT NULL AUTO_INCREMENT,
    created_at DATETIME NOT NULL DEFAULT now(),
    updated_at DATETIME NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    CONSTRAINT uq_analytics_marketing_spend_key UNIQUE (grain, period_start, channel, campaign, tz_generation)
);

CREATE INDEX ix_analytics_marketing_spend_period_start ON analytics_marketing_spend (period_start);

CREATE INDEX ix_analytics_marketing_spend_period ON analytics_marketing_spend (period_start, period_end);

CREATE INDEX ix_analytics_marketing_spend_channel_period ON analytics_marketing_spend (channel, period_start);
