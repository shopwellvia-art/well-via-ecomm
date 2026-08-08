-- Manual DDL for the shared remote MySQL (applied in a reviewed window, NOT via
-- `alembic upgrade` — the live lineage diverges from the repo; see DEPLOY.md §6).
-- Mirrors alembic revision e8b207fd93c1 EXACTLY: this file was generated from it
-- with `alembic upgrade a3d82f5c1e94:e8b207fd93c1 --sql`, so the two artifacts
-- cannot silently disagree. Regenerate the same way if the revision changes.
--
-- WHAT THIS DOES
--   Creates TWO new tables and nothing else:
--     payment_settlements   one immutable row per settlement line exactly as the
--                           gateway reported it (captured payment, refund,
--                           chargeback, adjustment).
--     agg_settlement_daily  the daily rollup the reconciliation view reads.
--
-- WHAT THIS DELIBERATELY DOES NOT DO
--   It touches NO existing table. No column is added, altered or dropped; no
--   existing index or constraint is modified. Grep the DDL below for any table
--   name other than the two above and you will find none.
--
-- WHY
--   No gateway fee is stored anywhere in the current schema — not on `orders`,
--   not on `order_payments`, not on `payment_events`. So every gateway fee this
--   system reports today is ESTIMATED: a `gateway_fee` cost rule applied to
--   prepaid order value. A cost rule is a forecast; a settlement report is a
--   measurement. These tables are where the measurement lands, so a fee tied to
--   a real settlement line becomes ACTUAL and one that could not be tied stays
--   ESTIMATED and says so.
--
-- TWO THINGS IN THIS DDL THAT ARE NOT ACCIDENTS
--   1. MONEY IN payment_settlements IS SIGNED INTEGER PAISE (BIGINT), NOT
--      DECIMAL(14,2) LIKE EVERY OTHER ANALYTICS TABLE.
--      This is the one table whose purpose is an equality against a third
--      party's arithmetic: gross - fee - tax = net must hold TO THE PAISA, and a
--      settlement that reconciles to the rupee but not the paisa has not
--      reconciled. `ck_payment_settlements_net` enforces that identity in the
--      DATABASE, so no code path — including a hand-run INSERT during an
--      incident — can store a line whose arithmetic does not close.
--
--      >>> CHECK THIS BEFORE APPLYING <<<
--      MySQL 8.0.16+ ENFORCES CHECK constraints. MySQL 5.7 PARSES AND IGNORES
--      them, silently. Confirm the server version first:
--        SELECT VERSION();
--      On 5.7 the clause is accepted and inert, the table is otherwise correct,
--      and the identity is then enforced only by the ingest service — the
--      settlement_daily job emits a `settlement_net_identity_broken` warning per
--      offending row, so the loss of the guarantee is visible rather than
--      silent. Do not delete the clause to "clean up" the DDL: on a later 8.x
--      upgrade it starts working again for free.
--
--      Signs are real and are never normalised away: a refund is a NEGATIVE
--      gross, and a chargeback is a negative gross with a POSITIVE fee (the
--      gateway charges for the dispute), so its net is more negative than its
--      gross.
--
--   2. UNIQUE (gateway, transaction_id) — DELIBERATELY WITHOUT settlement_id.
--      This is what makes re-uploading the same settlement file a no-op instead
--      of a doubling, and finance will upload the same file twice. It excludes
--      settlement_id because Razorpay reports a captured payment that is still
--      on hold with an EMPTY settlement id, then reports the SAME transaction
--      again once it settles; keying on both would store that payment twice and
--      double-count its fee. Keying on the transaction alone lets the second
--      report update the first in place, which is what actually happened.
--      The cost, stated rather than hidden: a transaction genuinely split across
--      two settlement batches collapses to the later batch. Razorpay does not
--      split a payment across batches.
--
-- TWO DATES, AND THEY ARE NOT INTERCHANGEABLE
--   transacted_at / payment_date is when the gateway created the transaction.
--   The FEE belongs to that day, in the same bucket as the revenue it was
--   charged against. settled_at / settlement_date is when the payout reached the
--   bank — the CASH belongs to that day, typically two or three days later, and
--   is NULL while the line is on hold. That is why agg_settlement_daily carries
--   two families of columns, `_transacted` and `_settled`, over two DIFFERENT
--   populations. Never sum one against the other.
--
-- NO FOREIGN KEYS
--   order_id / order_payment_id are plain integers, per the analytics-schema
--   convention: these tables must stay independently truncatable and
--   re-ingestable from the source file, and a settlement line records something
--   that happened at a third party and must survive an order being deleted.
--
-- SAFETY
--   Purely additive — two CREATE TABLEs and their indexes — so it is safe to
--   apply one deploy BEFORE the code that reads them, which is the required
--   order (additive-first). Nothing reads these tables until a settlement report
--   is uploaded.
--
--   Re-running fails harmlessly with "Table already exists" and changes no data.
--   Verify first if unsure:
--     SELECT table_name FROM information_schema.tables
--      WHERE table_schema = DATABASE()
--        AND table_name IN ('payment_settlements','agg_settlement_daily');
--
-- BEFORE RUNNING
--   Take a backup: backend/scripts/backup_db.sh
--
-- PREREQUISITE
--   None. Neither table references any other, so this file can be applied
--   independently of the other analytics DDL in this directory.
--
-- NOTE ON alembic_version
--   The generated statement that stamps alembic_version has been REMOVED on
--   purpose. The shared remote DB is on the `conpay001` lineage which this repo
--   does not contain; stamping it with a revision id from this repo's chain
--   would corrupt its migration state. Nothing here needs alembic to know.
--
-- ROLLBACK
--   Both tables are new and nothing else references them, so dropping them is
--   safe and loses only ingested settlement data (re-uploadable from the
--   gateway's own report):
--     DROP TABLE agg_settlement_daily;
--     DROP TABLE payment_settlements;
-- Running upgrade a3d82f5c1e94 -> e8b207fd93c1

CREATE TABLE payment_settlements (
    gateway VARCHAR(40) NOT NULL DEFAULT '-', 
    transaction_id VARCHAR(128) NOT NULL, 
    transaction_type VARCHAR(24) NOT NULL DEFAULT 'other', 
    settlement_id VARCHAR(64) NOT NULL DEFAULT '-', 
    settlement_utr VARCHAR(64) NOT NULL DEFAULT '-', 
    currency VARCHAR(3) NOT NULL DEFAULT 'INR', 
    method VARCHAR(32) NOT NULL DEFAULT '-', 
    gross_minor BIGINT NOT NULL DEFAULT '0', 
    fee_minor BIGINT NOT NULL DEFAULT '0', 
    tax_minor BIGINT NOT NULL DEFAULT '0', 
    net_minor BIGINT NOT NULL DEFAULT '0', 
    transacted_at DATETIME NOT NULL, 
    settled_at DATETIME, 
    tz_generation SMALLINT NOT NULL DEFAULT '1', 
    payment_date DATE NOT NULL, 
    settlement_date DATE, 
    gateway_payment_id VARCHAR(128), 
    gateway_order_id VARCHAR(128), 
    gateway_reference VARCHAR(255), 
    merchant_transaction_id VARCHAR(64), 
    match_status VARCHAR(16) NOT NULL DEFAULT 'unmatched', 
    match_key VARCHAR(32) NOT NULL DEFAULT '-', 
    order_id INTEGER, 
    order_payment_id INTEGER, 
    source VARCHAR(16) NOT NULL DEFAULT 'csv_upload', 
    source_file VARCHAR(255) NOT NULL DEFAULT '-', 
    source_row INTEGER NOT NULL DEFAULT '0', 
    raw JSON, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT ck_payment_settlements_net CHECK (net_minor = gross_minor - fee_minor - tax_minor), 
    CONSTRAINT uq_payment_settlements_txn UNIQUE (gateway, transaction_id)
);

CREATE INDEX ix_payment_settlements_created_at ON payment_settlements (created_at);

CREATE INDEX ix_payment_settlements_match_status ON payment_settlements (match_status, payment_date);

CREATE INDEX ix_payment_settlements_order_id ON payment_settlements (order_id);

CREATE INDEX ix_payment_settlements_payment_date ON payment_settlements (payment_date, gateway);

CREATE INDEX ix_payment_settlements_settlement_date ON payment_settlements (settlement_date, gateway);

CREATE INDEX ix_payment_settlements_settlement_id ON payment_settlements (settlement_id);

CREATE TABLE agg_settlement_daily (
    gateway VARCHAR(40) NOT NULL DEFAULT '-', 
    txns_transacted INTEGER NOT NULL DEFAULT '0', 
    payments_transacted INTEGER NOT NULL DEFAULT '0', 
    reversals_transacted INTEGER NOT NULL DEFAULT '0', 
    gross_transacted NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    fee_transacted NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    tax_transacted NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    net_transacted NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    matched_txns INTEGER NOT NULL DEFAULT '0', 
    matched_gross NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    unmatched_txns INTEGER NOT NULL DEFAULT '0', 
    unmatched_gross NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    unsettled_payments INTEGER NOT NULL DEFAULT '0', 
    unsettled_amount NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    txns_settled INTEGER NOT NULL DEFAULT '0', 
    settlement_batches INTEGER NOT NULL DEFAULT '0', 
    gross_settled NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    fee_settled NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    tax_settled NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    payout_amount NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    bucket_date DATE NOT NULL, 
    tz_generation SMALLINT NOT NULL DEFAULT '1', 
    computed_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_agg_settlement_daily_key UNIQUE (bucket_date, gateway, tz_generation)
);

CREATE INDEX ix_agg_settlement_daily_bucket_date ON agg_settlement_daily (bucket_date);

CREATE INDEX ix_agg_settlement_daily_bucket_date_gateway ON agg_settlement_daily (bucket_date, gateway);
