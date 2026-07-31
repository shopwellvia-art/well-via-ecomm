-- Manual DDL for the shared remote MySQL (applied in a reviewed window, NOT via
-- `alembic upgrade` — the live lineage diverges from the repo; never run alembic
-- against this DB, see DEPLOY.md §6). Run ONCE, BEFORE deploying the code that
-- reads these columns: the currently deployed backend tolerates extra columns,
-- the new code requires them. Re-running fails harmlessly (duplicate-column
-- error) without touching data. Verify first if unsure:
--   SHOW COLUMNS FROM products LIKE 'reorder_point';
--
-- Generated from alembic revision f4c28d19ab60 with
--   alembic upgrade f4c7a1b90d23:f4c28d19ab60 --sql
-- and the `alembic_version` stamp stripped (the remote's version table is on a
-- different lineage and must not be moved by this file). The DDL below is the
-- generated output verbatim so the two artifacts cannot silently diverge —
-- tests/test_product_analytics_fields.py asserts they still agree.
--
-- Four separate ALTERs rather than one combined statement, because that is what
-- the migration emits. On MySQL 8 each is an ALGORITHM=INSTANT append (nullable,
-- no default, added at the end of the table), so this is metadata-only and does
-- not rebuild `products`.
--
-- Rationale:
--   * reorder_point — `agg_inventory_daily.reorder_gap` is `stock_close -
--     reorder_point` and has been NULL on every row ever written, because no
--     product carries a reorder point. NULL stays "not configured"; 0 means
--     "reorder only at empty". Those are DIFFERENT, the rollup already depends
--     on the distinction, and that is why this column has NO DEFAULT and is NOT
--     backfilled — defaulting to 0 would declare the whole catalog to be
--     sitting exactly at its reorder point.
--   * hsn_code — view 70 (Tax and GST) is PARTIAL because HSN-level detail is
--     not stored, so its figures are explicitly not compliance-grade. HSN is a
--     per-product attribute and mandatory on Indian GST invoices. VARCHAR, not
--     INT: leading zeros are significant in the tariff. VARCHAR(8) because 8
--     digits is the longest legal code, so an over-long value errors instead of
--     being truncated into a different, real, wrong heading.
--   * brand — `analytics_order_line.brand_snapshot` VARCHAR(120) already exists
--     and was reserved for exactly this field. The widths match on purpose so
--     the snapshot can never truncate.
--   * shelf_life_days — the `days_of_inventory` KPI's own caveat is that shelf
--     life is not modelled, so a supplement can have plenty of cover and still
--     expire before it sells. NULL = not tracked (most of the catalog); the API
--     rejects 0, so a stored 0 can only mean a direct SQL write.
--
-- All four are NULL with no default: a product that predates them has genuinely
-- unknown values, and a default would assert a fact nobody entered. Readers must
-- render NULL as unknown, never as 0 and never as "none".

ALTER TABLE products ADD COLUMN reorder_point INTEGER;

ALTER TABLE products ADD COLUMN hsn_code VARCHAR(8);

ALTER TABLE products ADD COLUMN brand VARCHAR(120);

ALTER TABLE products ADD COLUMN shelf_life_days INTEGER;

-- Rollback:
--   ALTER TABLE products DROP COLUMN shelf_life_days;
--   ALTER TABLE products DROP COLUMN brand;
--   ALTER TABLE products DROP COLUMN hsn_code;
--   ALTER TABLE products DROP COLUMN reorder_point;
