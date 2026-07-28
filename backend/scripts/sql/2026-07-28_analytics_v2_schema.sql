-- Manual DDL for the shared remote MySQL (applied in a reviewed window, NOT via
-- `alembic upgrade` — the live lineage diverges from the repo; see DEPLOY.md §6).
-- Mirrors alembic revision d7f3a9c2e814 EXACTLY: this file was generated from it
-- with `alembic upgrade c4a1d0e7b93f:d7f3a9c2e814 --sql`, so the two artifacts
-- cannot silently disagree. Regenerate the same way if the revision changes.
--
-- WHAT THIS DOES
--   Creates 23 new analytics tables: 4 immutable facts (analytics_order_line,
--   analytics_order_adjustment, inventory_movements, cart_events), 12 rollups
--   (agg_*), and 7 control tables (tz generations, cost rules, recompute queue,
--   sync runs, GA4 outbox, budgets, alerts).
--
-- WHAT THIS DELIBERATELY DOES NOT DO
--   It touches NO existing table. No column is added, altered or dropped; no
--   existing index or constraint is modified. Verified by grepping the generated
--   DDL for any non-analytics table name.
--
--   In particular it does NOT include the 23 operations `alembic --autogenerate`
--   originally proposed against existing tables. Those were stripped by hand:
--   they would have dropped the orders hot-path indexes (ix_orders_created_at,
--   ix_orders_status_created_at, ix_orders_user_id_created_at), dropped
--   uq_coupon_usages_coupon_order, recreated fk_categories_parent unnamed and
--   without ON DELETE RESTRICT, and altered returns.status from ENUM to VARCHAR.
--   They are pre-existing ORM/schema drift, not part of this feature.
--
-- SAFETY
--   Purely additive, so it is safe to apply one deploy BEFORE the code that
--   reads these tables, which is the required order (additive-first). The three
--   ANALYTICS_* feature flags default false, so the application ignores these
--   tables entirely until they are switched on.
--
--   Re-running fails harmlessly on duplicate-table errors without touching data.
--   Verify first if unsure:
--     SELECT table_name FROM information_schema.tables
--      WHERE table_schema = DATABASE()
--        AND (table_name LIKE 'analytics%' OR table_name LIKE 'agg\_%'
--             OR table_name IN ('cart_events','inventory_movements'));
--
-- BEFORE RUNNING
--   Take a backup: backend/scripts/backup_db.sh
--
-- NOTE ON alembic_version
--   The generated statement that stamps alembic_version has been REMOVED on
--   purpose. The shared remote DB is on the `conpay001` lineage which this repo
--   does not contain; stamping it with a revision id from this repo's chain
--   would corrupt its migration state. Nothing here needs alembic to know.
--
-- ROLLBACK
--   These tables are additive and unread by existing code, so leaving them in
--   place is safe. To remove them (reverse dependency order is irrelevant —
--   there are no foreign keys by design):
--     DROP TABLE IF EXISTS agg_customer_cohort_monthly, agg_customer_daily,
--       agg_customer_snapshot, agg_funnel_daily, agg_geo_daily,
--       agg_inventory_daily, agg_order_daily, agg_order_hourly,
--       agg_payment_daily, agg_product_daily, agg_promo_daily,
--       agg_shipment_daily, analytics_alerts, analytics_budgets,
--       analytics_cost_rules, analytics_event_outbox,
--       analytics_order_adjustment, analytics_order_line,
--       analytics_recompute_queue, analytics_sync_runs,
--       analytics_tz_generations, cart_events, inventory_movements;

CREATE TABLE agg_customer_cohort_monthly (
    cohort_month VARCHAR(7) NOT NULL DEFAULT '-', 
    period_index SMALLINT NOT NULL, 
    cohort_size INTEGER NOT NULL DEFAULT '0', 
    active_customers INTEGER NOT NULL DEFAULT '0', 
    orders INTEGER NOT NULL DEFAULT '0', 
    revenue NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    bucket_date DATE NOT NULL, 
    tz_generation SMALLINT NOT NULL DEFAULT '1', 
    computed_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_agg_customer_cohort_monthly_key UNIQUE (cohort_month, period_index, tz_generation)
);
CREATE INDEX ix_agg_customer_cohort_monthly_bucket_date ON agg_customer_cohort_monthly (bucket_date);
CREATE INDEX ix_agg_customer_cohort_monthly_cohort_month ON agg_customer_cohort_monthly (cohort_month);
CREATE TABLE agg_customer_daily (
    new_customers INTEGER NOT NULL DEFAULT '0', 
    returning_customers INTEGER NOT NULL DEFAULT '0', 
    active_customers INTEGER NOT NULL DEFAULT '0', 
    orders_new INTEGER NOT NULL DEFAULT '0', 
    orders_returning INTEGER NOT NULL DEFAULT '0', 
    revenue_new NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    revenue_returning NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    bucket_date DATE NOT NULL, 
    tz_generation SMALLINT NOT NULL DEFAULT '1', 
    computed_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_agg_customer_daily_key UNIQUE (bucket_date, tz_generation)
);
CREATE INDEX ix_agg_customer_daily_bucket_date ON agg_customer_daily (bucket_date);
CREATE TABLE agg_customer_snapshot (
    customer_key VARCHAR(64) NOT NULL DEFAULT '-', 
    user_id INTEGER, 
    first_order_at DATETIME, 
    last_order_at DATETIME, 
    orders_count INTEGER NOT NULL DEFAULT '0', 
    units INTEGER NOT NULL DEFAULT '0', 
    gross_ltv NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    net_ltv NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    margin_ltv NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    aov NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    recency_days INTEGER NOT NULL DEFAULT '0', 
    frequency INTEGER NOT NULL DEFAULT '0', 
    monetary NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    r_score SMALLINT NOT NULL DEFAULT '0', 
    f_score SMALLINT NOT NULL DEFAULT '0', 
    m_score SMALLINT NOT NULL DEFAULT '0', 
    rfm_segment VARCHAR(32) NOT NULL DEFAULT '-', 
    cohort_month VARCHAR(7) NOT NULL DEFAULT '-', 
    tenure_days INTEGER NOT NULL DEFAULT '0', 
    is_active BOOL NOT NULL DEFAULT '0', 
    churn_risk_band VARCHAR(16) NOT NULL DEFAULT '-', 
    preferred_payment_method VARCHAR(20) NOT NULL DEFAULT '-', 
    quality VARCHAR(16) NOT NULL DEFAULT 'unknown', 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    bucket_date DATE NOT NULL, 
    tz_generation SMALLINT NOT NULL DEFAULT '1', 
    computed_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_agg_customer_snapshot_key UNIQUE (bucket_date, customer_key, tz_generation)
);
CREATE INDEX ix_agg_customer_snapshot_bucket_date ON agg_customer_snapshot (bucket_date);
CREATE INDEX ix_agg_customer_snapshot_bucket_date_segment ON agg_customer_snapshot (bucket_date, rfm_segment);
CREATE INDEX ix_agg_customer_snapshot_customer_key_bucket_date ON agg_customer_snapshot (customer_key, bucket_date);
CREATE TABLE agg_funnel_daily (
    product_views INTEGER NOT NULL DEFAULT '0', 
    cart_views INTEGER NOT NULL DEFAULT '0', 
    items_added INTEGER NOT NULL DEFAULT '0', 
    checkouts_started INTEGER NOT NULL DEFAULT '0', 
    shipping_submitted INTEGER NOT NULL DEFAULT '0', 
    payments_initiated INTEGER NOT NULL DEFAULT '0', 
    payments_failed INTEGER NOT NULL DEFAULT '0', 
    orders_placed INTEGER NOT NULL DEFAULT '0', 
    distinct_sessions INTEGER NOT NULL DEFAULT '0', 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    bucket_date DATE NOT NULL, 
    tz_generation SMALLINT NOT NULL DEFAULT '1', 
    computed_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_agg_funnel_daily_key UNIQUE (bucket_date, tz_generation)
);
CREATE INDEX ix_agg_funnel_daily_bucket_date ON agg_funnel_daily (bucket_date);
CREATE TABLE agg_geo_daily (
    state VARCHAR(64) NOT NULL DEFAULT '-', 
    pincode VARCHAR(10) NOT NULL DEFAULT '-', 
    orders INTEGER NOT NULL DEFAULT '0', 
    net_revenue NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    units INTEGER NOT NULL DEFAULT '0', 
    cod_orders INTEGER NOT NULL DEFAULT '0', 
    prepaid_orders INTEGER NOT NULL DEFAULT '0', 
    delivered INTEGER NOT NULL DEFAULT '0', 
    rto INTEGER NOT NULL DEFAULT '0', 
    sum_delivery_seconds BIGINT NOT NULL DEFAULT '0', 
    n_delivery INTEGER NOT NULL DEFAULT '0', 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    bucket_date DATE NOT NULL, 
    tz_generation SMALLINT NOT NULL DEFAULT '1', 
    computed_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_agg_geo_daily_key UNIQUE (bucket_date, state, pincode, tz_generation)
);
CREATE INDEX ix_agg_geo_daily_bucket_date ON agg_geo_daily (bucket_date);
CREATE INDEX ix_agg_geo_daily_bucket_date_state ON agg_geo_daily (bucket_date, state);
CREATE TABLE agg_inventory_daily (
    product_id INTEGER NOT NULL, 
    sku_snapshot VARCHAR(64) NOT NULL DEFAULT '-', 
    stock_close INTEGER NOT NULL DEFAULT '0', 
    stock_value_close NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    units_sold INTEGER NOT NULL DEFAULT '0', 
    units_restocked INTEGER NOT NULL DEFAULT '0', 
    is_oos BOOL NOT NULL DEFAULT '0', 
    days_oos INTEGER NOT NULL DEFAULT '0', 
    reorder_gap INTEGER, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    bucket_date DATE NOT NULL, 
    tz_generation SMALLINT NOT NULL DEFAULT '1', 
    computed_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_agg_inventory_daily_key UNIQUE (bucket_date, product_id, tz_generation)
);
CREATE INDEX ix_agg_inventory_daily_bucket_date ON agg_inventory_daily (bucket_date);
CREATE INDEX ix_agg_inventory_daily_product_bucket_date ON agg_inventory_daily (product_id, bucket_date);
CREATE TABLE agg_order_daily (
    orders_total INTEGER NOT NULL DEFAULT '0', 
    orders_pending INTEGER NOT NULL DEFAULT '0', 
    orders_paid INTEGER NOT NULL DEFAULT '0', 
    orders_shipped INTEGER NOT NULL DEFAULT '0', 
    orders_delivered INTEGER NOT NULL DEFAULT '0', 
    orders_cancelled INTEGER NOT NULL DEFAULT '0', 
    orders_refunded INTEGER NOT NULL DEFAULT '0', 
    order_value_created NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    paid_order_value NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    gross_merchandise_sales NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    net_merchandise_sales NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    net_revenue NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    subtotal_sum NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    tax_sum NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    discount_sum NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    payment_discount_sum NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    shipping_income NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    cod_surcharge_sum NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    refund_sum NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    cogs_sum NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    units INTEGER NOT NULL DEFAULT '0', 
    costed_units INTEGER NOT NULL DEFAULT '0', 
    distinct_customers INTEGER NOT NULL DEFAULT '0', 
    new_customers INTEGER NOT NULL DEFAULT '0', 
    returning_customers INTEGER NOT NULL DEFAULT '0', 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    bucket_date DATE NOT NULL, 
    tz_generation SMALLINT NOT NULL DEFAULT '1', 
    computed_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_agg_order_daily_key UNIQUE (bucket_date, tz_generation)
);
CREATE INDEX ix_agg_order_daily_bucket_date ON agg_order_daily (bucket_date);
CREATE TABLE agg_order_hourly (
    bucket_hour SMALLINT NOT NULL, 
    orders INTEGER NOT NULL DEFAULT '0', 
    net_revenue NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    units INTEGER NOT NULL DEFAULT '0', 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    bucket_date DATE NOT NULL, 
    tz_generation SMALLINT NOT NULL DEFAULT '1', 
    computed_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_agg_order_hourly_key UNIQUE (bucket_date, bucket_hour, tz_generation)
);
CREATE INDEX ix_agg_order_hourly_bucket_date ON agg_order_hourly (bucket_date);
CREATE TABLE agg_payment_daily (
    gateway VARCHAR(40) NOT NULL DEFAULT '-', 
    payment_method VARCHAR(32) NOT NULL DEFAULT '-', 
    payment_instrument VARCHAR(32) NOT NULL DEFAULT '-', 
    attempts INTEGER NOT NULL DEFAULT '0', 
    paid INTEGER NOT NULL DEFAULT '0', 
    failed INTEGER NOT NULL DEFAULT '0', 
    cancelled INTEGER NOT NULL DEFAULT '0', 
    refunded INTEGER NOT NULL DEFAULT '0', 
    paid_amount NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    failed_amount NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    sum_settle_seconds BIGINT NOT NULL DEFAULT '0', 
    n_settle INTEGER NOT NULL DEFAULT '0', 
    mismatch_events INTEGER NOT NULL DEFAULT '0', 
    top_failure_reason VARCHAR(120), 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    bucket_date DATE NOT NULL, 
    tz_generation SMALLINT NOT NULL DEFAULT '1', 
    computed_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_agg_payment_daily_key UNIQUE (bucket_date, gateway, payment_method, payment_instrument, tz_generation)
);
CREATE INDEX ix_agg_payment_daily_bucket_date ON agg_payment_daily (bucket_date);
CREATE INDEX ix_agg_payment_daily_bucket_date_gateway ON agg_payment_daily (bucket_date, gateway);
CREATE TABLE agg_product_daily (
    product_id INTEGER NOT NULL, 
    sku_snapshot VARCHAR(64) NOT NULL DEFAULT '-', 
    category_id_snapshot INTEGER, 
    units INTEGER NOT NULL DEFAULT '0', 
    orders INTEGER NOT NULL DEFAULT '0', 
    gross_merchandise_sales NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    net_merchandise_sales NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    line_cost NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    costed_units INTEGER NOT NULL DEFAULT '0', 
    returned_units INTEGER NOT NULL DEFAULT '0', 
    returned_value NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    bucket_date DATE NOT NULL, 
    tz_generation SMALLINT NOT NULL DEFAULT '1', 
    computed_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_agg_product_daily_key UNIQUE (bucket_date, product_id, tz_generation)
);
CREATE INDEX ix_agg_product_daily_bucket_date ON agg_product_daily (bucket_date);
CREATE INDEX ix_agg_product_daily_bucket_date_category ON agg_product_daily (bucket_date, category_id_snapshot);
CREATE INDEX ix_agg_product_daily_product_bucket_date ON agg_product_daily (product_id, bucket_date);
CREATE TABLE agg_promo_daily (
    coupon_code VARCHAR(64) NOT NULL DEFAULT '-', 
    redemptions INTEGER NOT NULL DEFAULT '0', 
    discount_amount NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    orders INTEGER NOT NULL DEFAULT '0', 
    order_revenue NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    is_loyalty_reward BOOL NOT NULL DEFAULT '0', 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    bucket_date DATE NOT NULL, 
    tz_generation SMALLINT NOT NULL DEFAULT '1', 
    computed_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_agg_promo_daily_key UNIQUE (bucket_date, coupon_code, tz_generation)
);
CREATE INDEX ix_agg_promo_daily_bucket_date ON agg_promo_daily (bucket_date);
CREATE INDEX ix_agg_promo_daily_coupon_code_bucket_date ON agg_promo_daily (coupon_code, bucket_date);
CREATE TABLE agg_shipment_daily (
    courier_partner VARCHAR(64) NOT NULL DEFAULT '-', 
    shipments INTEGER NOT NULL DEFAULT '0', 
    delivered INTEGER NOT NULL DEFAULT '0', 
    delivery_failed INTEGER NOT NULL DEFAULT '0', 
    rto_initiated INTEGER NOT NULL DEFAULT '0', 
    rto_delivered INTEGER NOT NULL DEFAULT '0', 
    cancelled INTEGER NOT NULL DEFAULT '0', 
    shipment_cost NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    sum_order_to_ship_seconds BIGINT NOT NULL DEFAULT '0', 
    n_order_to_ship INTEGER NOT NULL DEFAULT '0', 
    sum_ship_to_deliver_seconds BIGINT NOT NULL DEFAULT '0', 
    n_ship_to_deliver INTEGER NOT NULL DEFAULT '0', 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    bucket_date DATE NOT NULL, 
    tz_generation SMALLINT NOT NULL DEFAULT '1', 
    computed_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_agg_shipment_daily_key UNIQUE (bucket_date, courier_partner, tz_generation)
);
CREATE INDEX ix_agg_shipment_daily_bucket_date ON agg_shipment_daily (bucket_date);
CREATE INDEX ix_agg_shipment_daily_bucket_date_courier ON agg_shipment_daily (bucket_date, courier_partner);
CREATE TABLE analytics_alerts (
    rule_key VARCHAR(64) NOT NULL, 
    severity VARCHAR(16) NOT NULL, 
    metric VARCHAR(64) NOT NULL, 
    dimension VARCHAR(32) NOT NULL DEFAULT '-', 
    dimension_value VARCHAR(64) NOT NULL DEFAULT '-', 
    expected_low NUMERIC(18, 4), 
    expected_high NUMERIC(18, 4), 
    actual_value NUMERIC(18, 4), 
    bucket_date DATE, 
    detected_at DATETIME NOT NULL, 
    status VARCHAR(16) NOT NULL DEFAULT 'open', 
    acknowledged_by_user_id INTEGER, 
    acknowledged_at DATETIME, 
    resolution_note VARCHAR(500), 
    context JSON, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id)
);
CREATE INDEX ix_analytics_alerts_created_at ON analytics_alerts (created_at);
CREATE INDEX ix_analytics_alerts_rule_detected ON analytics_alerts (rule_key, detected_at);
CREATE INDEX ix_analytics_alerts_status_detected ON analytics_alerts (status, detected_at);
CREATE TABLE analytics_budgets (
    period_start DATE NOT NULL, 
    period_end DATE NOT NULL, 
    granularity VARCHAR(16) NOT NULL, 
    metric VARCHAR(64) NOT NULL, 
    dimension VARCHAR(32) NOT NULL DEFAULT '-', 
    dimension_value VARCHAR(64) NOT NULL DEFAULT '-', 
    budget_amount NUMERIC(14, 2) NOT NULL, 
    currency VARCHAR(3) NOT NULL DEFAULT 'INR', 
    note VARCHAR(255), 
    created_by_user_id INTEGER, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME NOT NULL DEFAULT now(), 
    updated_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_analytics_budgets_period_gran_metric_dim UNIQUE (period_start, granularity, metric, dimension, dimension_value)
);
CREATE INDEX ix_analytics_budgets_metric_period ON analytics_budgets (metric, period_start, period_end);
CREATE TABLE analytics_cost_rules (
    cost_type VARCHAR(48) NOT NULL, 
    scope VARCHAR(24) NOT NULL DEFAULT 'global', 
    scope_value VARCHAR(64) NOT NULL DEFAULT '-', 
    value NUMERIC(14, 4) NOT NULL, 
    unit VARCHAR(24) NOT NULL, 
    currency VARCHAR(3) NOT NULL DEFAULT 'INR', 
    quality VARCHAR(16) NOT NULL, 
    effective_from DATE NOT NULL, 
    effective_to DATE, 
    source VARCHAR(64), 
    note VARCHAR(255), 
    created_by_user_id INTEGER, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_analytics_cost_rules_type_scope_value_from UNIQUE (cost_type, scope, scope_value, effective_from)
);
CREATE INDEX ix_analytics_cost_rules_created_at ON analytics_cost_rules (created_at);
CREATE INDEX ix_analytics_cost_rules_type_from_to ON analytics_cost_rules (cost_type, effective_from, effective_to);
CREATE TABLE analytics_event_outbox (
    event_name VARCHAR(40) NOT NULL, 
    transaction_id VARCHAR(64) NOT NULL, 
    order_id INTEGER, 
    occurred_at DATETIME NOT NULL, 
    payload JSON NOT NULL, 
    client_id VARCHAR(64), 
    session_id VARCHAR(64), 
    consent_state VARCHAR(24) NOT NULL, 
    status VARCHAR(24) NOT NULL DEFAULT 'pending', 
    attempts SMALLINT NOT NULL DEFAULT '0', 
    last_error VARCHAR(500), 
    delivered_at DATETIME, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_analytics_event_outbox_event_txn UNIQUE (event_name, transaction_id)
);
CREATE INDEX ix_analytics_event_outbox_created_at ON analytics_event_outbox (created_at);
CREATE INDEX ix_analytics_event_outbox_order_id ON analytics_event_outbox (order_id);
CREATE INDEX ix_analytics_event_outbox_status_occurred ON analytics_event_outbox (status, occurred_at);
CREATE TABLE analytics_order_adjustment (
    order_id INTEGER NOT NULL, 
    order_line_id BIGINT, 
    occurred_at DATETIME NOT NULL, 
    bucket_date DATE NOT NULL, 
    tz_generation SMALLINT NOT NULL DEFAULT '1', 
    adjustment_type VARCHAR(40) NOT NULL, 
    amount NUMERIC(14, 2) NOT NULL, 
    currency VARCHAR(3) NOT NULL DEFAULT 'INR', 
    quality VARCHAR(16) NOT NULL, 
    source_ref_type VARCHAR(32), 
    source_ref_id INTEGER, 
    event_key VARCHAR(120) NOT NULL, 
    note VARCHAR(255), 
    raw JSON, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    UNIQUE (event_key)
);
CREATE INDEX ix_analytics_order_adjustment_adjustment_type ON analytics_order_adjustment (adjustment_type);
CREATE INDEX ix_analytics_order_adjustment_bucket_date ON analytics_order_adjustment (bucket_date);
CREATE INDEX ix_analytics_order_adjustment_created_at ON analytics_order_adjustment (created_at);
CREATE INDEX ix_analytics_order_adjustment_occurred_at ON analytics_order_adjustment (occurred_at);
CREATE INDEX ix_analytics_order_adjustment_order_id ON analytics_order_adjustment (order_id);
CREATE TABLE analytics_order_line (
    order_id INTEGER NOT NULL, 
    order_item_id INTEGER NOT NULL, 
    ordered_at DATETIME NOT NULL, 
    bucket_date DATE NOT NULL, 
    tz_generation SMALLINT NOT NULL DEFAULT '1', 
    product_id INTEGER NOT NULL, 
    sku_snapshot VARCHAR(64) NOT NULL, 
    product_name_snapshot VARCHAR(255) NOT NULL, 
    category_id_snapshot INTEGER, 
    category_name_snapshot VARCHAR(120), 
    brand_snapshot VARCHAR(120), 
    quantity INTEGER NOT NULL, 
    unit_price NUMERIC(14, 2) NOT NULL, 
    unit_cost NUMERIC(12, 2), 
    extended_price NUMERIC(14, 2) NOT NULL, 
    alloc_discount NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    alloc_tax NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    alloc_shipping NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    alloc_cod_surcharge NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    alloc_payment_discount NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    alloc_gateway_fee NUMERIC(14, 2) NOT NULL DEFAULT '0', 
    cost_quality VARCHAR(16) NOT NULL, 
    alloc_quality VARCHAR(16) NOT NULL, 
    identity_source VARCHAR(32) NOT NULL, 
    order_status VARCHAR(20) NOT NULL DEFAULT '-', 
    payment_method VARCHAR(20) NOT NULL DEFAULT '-', 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    UNIQUE (order_item_id)
);
CREATE INDEX ix_analytics_order_line_bucket_date ON analytics_order_line (bucket_date);
CREATE INDEX ix_analytics_order_line_created_at ON analytics_order_line (created_at);
CREATE INDEX ix_analytics_order_line_order_id ON analytics_order_line (order_id);
CREATE INDEX ix_analytics_order_line_product_id ON analytics_order_line (product_id);
CREATE INDEX ix_aol_bucket_category ON analytics_order_line (bucket_date, category_id_snapshot);
CREATE INDEX ix_aol_bucket_product ON analytics_order_line (bucket_date, product_id);
CREATE TABLE analytics_recompute_queue (
    job VARCHAR(64) NOT NULL, 
    bucket_date DATE NOT NULL, 
    dimension_key VARCHAR(96) NOT NULL DEFAULT '-', 
    tz_generation SMALLINT NOT NULL DEFAULT '1', 
    reason VARCHAR(48) NOT NULL, 
    priority SMALLINT NOT NULL DEFAULT '5', 
    status VARCHAR(16) NOT NULL DEFAULT 'pending', 
    attempts SMALLINT NOT NULL DEFAULT '0', 
    last_error VARCHAR(500), 
    enqueued_at DATETIME NOT NULL DEFAULT now(), 
    claimed_at DATETIME, 
    claimed_by VARCHAR(64), 
    claim_expires_at DATETIME, 
    heartbeat_at DATETIME, 
    processed_at DATETIME, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_analytics_recompute_queue_job_date_dim_tz UNIQUE (job, bucket_date, dimension_key, tz_generation)
);
CREATE INDEX ix_analytics_recompute_queue_status_priority_date ON analytics_recompute_queue (status, priority, bucket_date);
CREATE TABLE analytics_sync_runs (
    job VARCHAR(64) NOT NULL, 
    `trigger` VARCHAR(16) NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    worker_id VARCHAR(64), 
    window_from DATE, 
    window_to DATE, 
    tz_generation SMALLINT NOT NULL DEFAULT '1', 
    days_requested SMALLINT NOT NULL DEFAULT '0', 
    days_processed SMALLINT NOT NULL DEFAULT '0', 
    rows_written INTEGER NOT NULL DEFAULT '0', 
    rows_deleted INTEGER NOT NULL DEFAULT '0', 
    watermark_date DATE, 
    duration_ms INTEGER, 
    error VARCHAR(500), 
    started_at DATETIME NOT NULL, 
    finished_at DATETIME, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    PRIMARY KEY (id)
);
CREATE INDEX ix_analytics_sync_runs_job_started ON analytics_sync_runs (job, started_at);
CREATE INDEX ix_analytics_sync_runs_status_started ON analytics_sync_runs (status, started_at);
CREATE TABLE analytics_tz_generations (
    generation SMALLINT NOT NULL, 
    timezone VARCHAR(64) NOT NULL, 
    effective_from DATETIME NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    rebuild_started_at DATETIME, 
    rebuild_finished_at DATETIME, 
    created_by_user_id INTEGER, 
    note VARCHAR(255), 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_analytics_tz_generations_generation UNIQUE (generation)
);
CREATE INDEX ix_analytics_tz_generations_created_at ON analytics_tz_generations (created_at);
CREATE INDEX ix_analytics_tz_generations_status ON analytics_tz_generations (status);
CREATE TABLE cart_events (
    occurred_at DATETIME NOT NULL, 
    session_key VARCHAR(64) NOT NULL, 
    user_id INTEGER, 
    event_type VARCHAR(32) NOT NULL, 
    product_id INTEGER, 
    quantity INTEGER, 
    value NUMERIC(14, 2), 
    currency VARCHAR(3) NOT NULL DEFAULT 'INR', 
    order_id INTEGER, 
    event_key VARCHAR(120) NOT NULL, 
    meta JSON, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    UNIQUE (event_key)
);
CREATE INDEX ix_cart_events_created_at ON cart_events (created_at);
CREATE INDEX ix_cart_events_event_type ON cart_events (event_type);
CREATE INDEX ix_cart_events_occurred_at ON cart_events (occurred_at);
CREATE INDEX ix_cart_events_order_id ON cart_events (order_id);
CREATE INDEX ix_cart_events_session_key ON cart_events (session_key);
CREATE INDEX ix_cart_events_session_occurred ON cart_events (session_key, occurred_at);
CREATE INDEX ix_cart_events_type_occurred ON cart_events (event_type, occurred_at);
CREATE INDEX ix_cart_events_user_id ON cart_events (user_id);
CREATE TABLE inventory_movements (
    product_id INTEGER NOT NULL, 
    occurred_at DATETIME NOT NULL, 
    movement_type VARCHAR(32) NOT NULL, 
    delta INTEGER NOT NULL, 
    stock_after INTEGER, 
    ref_type VARCHAR(32), 
    ref_id INTEGER, 
    actor_user_id INTEGER, 
    event_key VARCHAR(120) NOT NULL, 
    note VARCHAR(255), 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    UNIQUE (event_key)
);
CREATE INDEX ix_inventory_movements_created_at ON inventory_movements (created_at);
CREATE INDEX ix_inventory_movements_movement_type ON inventory_movements (movement_type);
CREATE INDEX ix_inventory_movements_occurred_at ON inventory_movements (occurred_at);
CREATE INDEX ix_inventory_movements_product_id ON inventory_movements (product_id);
CREATE INDEX ix_invmov_product_occurred ON inventory_movements (product_id, occurred_at);
