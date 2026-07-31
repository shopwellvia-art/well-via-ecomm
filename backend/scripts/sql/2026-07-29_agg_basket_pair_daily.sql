-- Manual DDL for the shared remote MySQL (applied in a reviewed window, NOT via
-- `alembic upgrade` — the live lineage diverges from the repo; see DEPLOY.md §6).
-- Mirrors alembic revision b7e40c9a2d15 EXACTLY: this file was generated from it
-- with `alembic upgrade f2a91c4e7b30:b7e40c9a2d15 --sql`, so the two artifacts
-- cannot silently disagree. Regenerate the same way if the revision changes.
--
-- WHAT THIS DOES
--   Creates ONE new table, agg_basket_pair_daily, plus its four indexes.
--   Nothing existing is touched: no column is added to another table, no
--   constraint is altered, no data is read or written. It is additive and
--   reversible with a single DROP TABLE.
--
-- WHAT THE TABLE IS
--   The market-basket co-occurrence rollup behind view 57 (Product Bundling and
--   Cross-Sell). One row per (store-local reporting day, unordered product
--   pair) that appeared together in the same order.
--
--   Written by the `basket_pair_daily` aggregation job
--   (backend/app/services/analytics/aggregation/jobs_basket.py) from
--   analytics_order_line — the immutable line fact — and NEVER from
--   order_items joined to products. A historical basket's identity must not
--   resolve through the live catalogue: a rename would rewrite every past pair
--   and a delete would erase an affinity that really happened. product_a_name
--   and product_b_name are the sale-time snapshots carried onto the row, so the
--   view renders with no catalogue join at all.
--
-- WHY THERE IS NO support / confidence / lift COLUMN
--   All three are RATIOs, and a stored daily ratio cannot be re-bucketed. The
--   average of seven daily lifts is not the weekly lift; the gap grows with the
--   variance in daily volume, nothing raises, and the number stays plausible.
--   So the four COUNTS are stored -- pair_orders, orders_with_a, orders_with_b,
--   total_orders_in_bucket -- and the ratios are recomputed at read time from
--   their sums:
--
--     support    = SUM(pair_orders) / SUM(total_orders_in_bucket)
--     confidence = SUM(pair_orders) / SUM(orders_with_a)
--     lift       = SUM(pair_orders) * SUM(total_orders_in_bucket)
--                  / (SUM(orders_with_a) * SUM(orders_with_b))
--
--   summed over the rows of ONE pair. This is the same rule
--   app/models/analytics_base.py states for averages, applied to ratios.
--
--   There is likewise no money column. This is a basket-composition table.
--
-- CANONICAL PAIR ORDERING
--   product_a_id < product_b_id, ALWAYS. It is an invariant of the writing job
--   (which pairs from `combinations(sorted(ids), 2)`, so {B,A} is unreachable)
--   rather than a CHECK constraint, to keep one statement of it rather than two
--   that can disagree. Without it {A,B} and {B,A} are two distinct rows under
--   the UNIQUE key and every support figure is exactly halved -- silently.
--
-- NO FOREIGN KEYS
--   product_a_id / product_b_id are plain integers, matching every other
--   analytics table. Rollups must stay independently truncatable and
--   rebuildable, and deleting a product must not erase the affinity it took
--   part in. This is deliberate, not an omission.
--
-- IDEMPOTENCY
--   uq_agg_basket_pair_daily_key (bucket_date, product_a_id, product_b_id,
--   tz_generation) is the natural key. The job deletes and reinserts the whole
--   bucket inside one transaction, so a re-run replaces rows rather than
--   doubling them, and a pair whose only order was cancelled leaves the table
--   instead of lingering at its last count.
--
--   tz_generation is in the key because reporting days are computed in the
--   store's timezone; changing it re-buckets everything, and rows from two
--   generations must be able to coexist rather than collide.
--
-- INDEXES, AND THE QUERY EACH ONE SERVES
--   ix_..._bucket_date            range scan by reporting day (declared on the
--                                 column, as on every other rollup)
--   ix_..._bucket_pair_orders     "the strongest pairs in this window" — the
--                                 whole view; without it a window scan reads
--                                 every pair of every day before sorting
--   ix_..._product_a  /  _b       "what is bought with product X". An unordered
--                                 pair stores X in whichever column holds the
--                                 lower id, so both halves need covering; one
--                                 index cannot serve both.
--
-- SIZE
--   Row growth is quadratic in basket size: an order with N distinct products
--   contributes N*(N-1)/2 pairs. The job caps that at
--   MAX_DISTINCT_PRODUCTS_PER_ORDER = 20 distinct products (190 pairs) and
--   skips larger orders entirely — from the pairs, the marginals AND the
--   denominator, so every ratio stays over one population — recording the count
--   in orders_skipped_over_cap so the view can say so.
--
-- PREREQUISITE
--   analytics_order_line must already exist and be populated, i.e.
--   2026-07-28_analytics_v2_schema.sql has been applied. The table can be
--   created before that, but the job has nothing to read until it is.
--
-- NOTE ON alembic_version
--   The generated statement that stamps alembic_version has been REMOVED on
--   purpose. The shared remote DB is on the `conpay001` lineage which this repo
--   does not contain; stamping it with a revision id from this repo's chain
--   would corrupt its migration state. Nothing here needs alembic to know.
--
-- ROLLBACK
--   The table is derived and disposable — every row can be recomputed from
--   analytics_order_line — and nothing reads it except view 57.
--     DROP TABLE agg_basket_pair_daily;

-- Running upgrade f2a91c4e7b30 -> b7e40c9a2d15

CREATE TABLE agg_basket_pair_daily (
    id BIGINT NOT NULL AUTO_INCREMENT,
    bucket_date DATE NOT NULL,
    tz_generation SMALLINT NOT NULL DEFAULT '1',
    computed_at DATETIME NOT NULL DEFAULT now(),
    product_a_id INTEGER NOT NULL,
    product_b_id INTEGER NOT NULL,
    product_a_name VARCHAR(255) NOT NULL DEFAULT '-',
    product_b_name VARCHAR(255) NOT NULL DEFAULT '-',
    pair_orders INTEGER NOT NULL DEFAULT '0',
    orders_with_a INTEGER NOT NULL DEFAULT '0',
    orders_with_b INTEGER NOT NULL DEFAULT '0',
    total_orders_in_bucket INTEGER NOT NULL DEFAULT '0',
    orders_with_any_pair INTEGER NOT NULL DEFAULT '0',
    orders_skipped_over_cap INTEGER NOT NULL DEFAULT '0',
    PRIMARY KEY (id),
    CONSTRAINT uq_agg_basket_pair_daily_key UNIQUE (bucket_date, product_a_id, product_b_id, tz_generation)
);

CREATE INDEX ix_agg_basket_pair_daily_bucket_date ON agg_basket_pair_daily (bucket_date);

CREATE INDEX ix_agg_basket_pair_daily_bucket_pair_orders ON agg_basket_pair_daily (bucket_date, pair_orders);

CREATE INDEX ix_agg_basket_pair_daily_product_a ON agg_basket_pair_daily (product_a_id, bucket_date);

CREATE INDEX ix_agg_basket_pair_daily_product_b ON agg_basket_pair_daily (product_b_id, bucket_date);
