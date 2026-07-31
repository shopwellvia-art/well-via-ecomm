-- Manual DDL for the shared remote MySQL (applied in a reviewed window, NOT via
-- `alembic upgrade` — the live lineage diverges from the repo; see DEPLOY.md §6).
-- Mirrors alembic revision f2a91c4e7b30 EXACTLY: this file was generated from it
-- with `alembic upgrade e1c5b7a04d92:f2a91c4e7b30 --sql`, so the two artifacts
-- cannot silently disagree. Regenerate the same way if the revision changes.
--
-- WHAT THIS DOES
--   Creates ONE new table, agg_cx_daily, and its three indexes. No existing
--   table, column, constraint or index is touched.
--
-- WHAT THE TABLE HOLDS
--   The customer-experience rollup, at two grains kept apart by product_id:
--
--     product_id > 0  one row per (store-local reporting day, product) holding
--                     that day's reviews for that product — volume, moderation
--                     state, the 1..5 star distribution, verified-purchase
--                     count and helpful votes. Every messages_* column is 0.
--     product_id = 0  the store-wide row, written only on days with inbound
--                     contact, holding contact_messages volume split by status.
--                     Every review column is 0. A message has no product, and 0
--                     is a sentinel MySQL AUTO_INCREMENT never issues — a NULL
--                     would not collide under the UNIQUE key and the next job
--                     run would insert a second row instead of replacing it.
--
--   Because the two column families are disjoint per row, SUM(messages_received)
--   and SUM(reviews_submitted) are both correct over any window with no
--   filtering by the reader.
--
-- WHY FIVE STAR COUNTS AND NOT avg_rating
--   An average cannot be re-bucketed: averaging seven daily averages into a week
--   weights a three-review day the same as a three-hundred-review day, and the
--   result is wrong SILENTLY. So the table stores rating_1..rating_5 plus the
--   additive pair rating_sum / rated_reviews, and the average is a division at
--   query time — which also yields the distribution the reviews view actually
--   wants. rating_sum = 1*rating_1 + ... + 5*rating_5 by construction and a test
--   asserts it on every written row.
--
--   rated_reviews is a SEPARATE denominator from reviews_submitted on purpose:
--   only a rating inside 1..5 contributes to the sum and the buckets, so the
--   average never describes a population the distribution does not.
--
-- WHAT IT DELIBERATELY CANNOT SAY
--   contact_messages is a contact form, not a ticketing system: no assignee, no
--   reply timestamp, no closure timestamp. There is therefore NO first-response
--   time, NO resolution time, NO SLA column here and there must never be one —
--   the same rule, for the same reason, that agg_shipment_daily forbids an
--   on-time column. Likewise reviews.is_approved is one boolean with no audit
--   trail, so the column is reviews_unapproved (provable) and not
--   reviews_rejected (a moderation decision this schema never recorded).
--
-- SAFETY
--   Purely additive and safe to apply BEFORE the code deploy (additive-first):
--   nothing reads the table until the cx_daily aggregation job is deployed and
--   run, and until then every analytics view that names it reports NO_ROLLUP_YET
--   rather than zero.
--
--   CREATE TABLE on MySQL 8 is instantaneous — it writes no rows and locks
--   nothing that exists. Re-running fails harmlessly with "Table 'agg_cx_daily'
--   already exists" and changes nothing. Verify first if unsure:
--     SHOW TABLES LIKE 'agg_cx_daily';
--
-- BEFORE RUNNING
--   Take a backup: backend/scripts/backup_db.sh
--
-- PREREQUISITE
--   None beyond a MySQL 8 database. The table has NO foreign keys by design
--   (rollups must stay independently truncatable and rebuildable), so it does
--   not depend on products, reviews or contact_messages existing.
--
-- AFTER RUNNING
--   The table is empty and must be backfilled by the aggregation job, which
--   rebuilds it from reviews and contact_messages:
--     POST /api/v1/admin/analytics/aggregation/run  {"job": "cx_daily", ...}
--   Every row is derived, so a bad backfill is fixed by re-running the bucket.
--
-- NOTE ON alembic_version
--   The generated statement that stamps alembic_version has been REMOVED on
--   purpose. The shared remote DB is on the `conpay001` lineage which this repo
--   does not contain; stamping it with a revision id from this repo's chain
--   would corrupt its migration state. Nothing here needs alembic to know.
--
-- ROLLBACK
--   The table is derived and nothing else references it, so dropping it loses no
--   information that cannot be recomputed:
--     DROP TABLE agg_cx_daily;

-- Running upgrade e1c5b7a04d92 -> f2a91c4e7b30

CREATE TABLE agg_cx_daily (
    product_id INTEGER NOT NULL,
    sku_snapshot VARCHAR(64) NOT NULL DEFAULT '-',
    category_id_snapshot INTEGER,
    reviews_submitted INTEGER NOT NULL DEFAULT '0',
    reviews_approved INTEGER NOT NULL DEFAULT '0',
    reviews_unapproved INTEGER NOT NULL DEFAULT '0',
    rating_1 INTEGER NOT NULL DEFAULT '0',
    rating_2 INTEGER NOT NULL DEFAULT '0',
    rating_3 INTEGER NOT NULL DEFAULT '0',
    rating_4 INTEGER NOT NULL DEFAULT '0',
    rating_5 INTEGER NOT NULL DEFAULT '0',
    rating_sum INTEGER NOT NULL DEFAULT '0',
    rated_reviews INTEGER NOT NULL DEFAULT '0',
    verified_purchase_reviews INTEGER NOT NULL DEFAULT '0',
    helpful_votes INTEGER NOT NULL DEFAULT '0',
    messages_received INTEGER NOT NULL DEFAULT '0',
    messages_new INTEGER NOT NULL DEFAULT '0',
    messages_replied INTEGER NOT NULL DEFAULT '0',
    messages_closed INTEGER NOT NULL DEFAULT '0',
    messages_other INTEGER NOT NULL DEFAULT '0',
    id BIGINT NOT NULL AUTO_INCREMENT,
    bucket_date DATE NOT NULL,
    tz_generation SMALLINT NOT NULL DEFAULT '1',
    computed_at DATETIME NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    CONSTRAINT uq_agg_cx_daily_key UNIQUE (bucket_date, product_id, tz_generation)
);

CREATE INDEX ix_agg_cx_daily_bucket_date ON agg_cx_daily (bucket_date);

CREATE INDEX ix_agg_cx_daily_bucket_date_category ON agg_cx_daily (bucket_date, category_id_snapshot);

CREATE INDEX ix_agg_cx_daily_product_bucket_date ON agg_cx_daily (product_id, bucket_date);
