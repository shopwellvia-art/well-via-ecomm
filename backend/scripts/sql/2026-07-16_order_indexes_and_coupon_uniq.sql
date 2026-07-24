-- Manual DDL for the shared remote MySQL (applied in a reviewed window, NOT via
-- `alembic upgrade` — the live lineage diverges from the repo). Mirrors alembic
-- revision p1e2r3f4i5x6. Safe/additive, but run during low traffic: adding
-- indexes locks/copies on older MySQL. Verify each object does not already
-- exist first (SHOW INDEX FROM orders; SHOW INDEX FROM coupon_usages;).
--
-- Rationale:
--   * orders.created_at drives admin sort, date filters and the reconcile cron
--     scan but was unindexed (full-table filesort on the default admin view).
--   * coupon_usages had no uniqueness; unique (coupon_id, order_id) backstops
--     double-settlement. NULL order_ids remain distinct under MySQL semantics.

CREATE INDEX ix_orders_created_at          ON orders (created_at);
CREATE INDEX ix_orders_status_created_at   ON orders (status, created_at);
CREATE INDEX ix_orders_user_id_created_at  ON orders (user_id, created_at);

ALTER TABLE coupon_usages
  ADD CONSTRAINT uq_coupon_usages_coupon_order UNIQUE (coupon_id, order_id);

-- Rollback:
--   ALTER TABLE coupon_usages DROP INDEX uq_coupon_usages_coupon_order;
--   DROP INDEX ix_orders_user_id_created_at ON orders;
--   DROP INDEX ix_orders_status_created_at  ON orders;
--   DROP INDEX ix_orders_created_at         ON orders;
