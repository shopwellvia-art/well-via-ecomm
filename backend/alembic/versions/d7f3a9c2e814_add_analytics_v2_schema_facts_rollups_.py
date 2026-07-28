"""add analytics v2 schema: facts, rollups, control tables

Creates the 23 analytics tables — 4 immutable facts, 12 rollups, 7 control —
and nothing else. Purely additive: no existing table is touched, so this is safe
to apply ahead of the code that reads it, and the analytics feature flags keep
the whole subsystem inert until deliberately enabled.

MANUALLY CURATED — DO NOT REGENERATE THIS FILE WITH --autogenerate.

Autogenerate produced 23 extra operations against EXISTING tables, because the
ORM models and the migration history have drifted apart. Left in, this migration
would have:

  * dropped ix_orders_created_at, ix_orders_status_created_at and
    ix_orders_user_id_created_at — the order hot-path indexes that migration
    p1e2r3f4i5x6 exists to create,
  * dropped uq_coupon_usages_coupon_order, a data-integrity constraint,
  * dropped fk_categories_parent and recreated it unnamed and without
    ON DELETE RESTRICT,
  * altered returns.status from ENUM to VARCHAR,
  * churned unique indexes on coupons, earn_rules, email_templates,
    payment_methods, permissions and system_settings.

Those are all pre-existing model/schema drift and none of them belong in a
feature migration. They were stripped by hand. The drift itself still needs
fixing separately — until it is, every future --autogenerate on this repo will
propose the same destructive changes, so review generated migrations line by
line rather than trusting them.

Revision ID: d7f3a9c2e814
Revises: c4a1d0e7b93f
Create Date: 2026-07-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = 'd7f3a9c2e814'
down_revision: Union[str, None] = 'c4a1d0e7b93f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('agg_customer_cohort_monthly',
    sa.Column('cohort_month', sa.String(length=7), server_default='-', nullable=False),
    sa.Column('period_index', sa.SmallInteger(), nullable=False),
    sa.Column('cohort_size', sa.Integer(), server_default='0', nullable=False),
    sa.Column('active_customers', sa.Integer(), server_default='0', nullable=False),
    sa.Column('orders', sa.Integer(), server_default='0', nullable=False),
    sa.Column('revenue', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('bucket_date', sa.Date(), nullable=False),
    sa.Column('tz_generation', sa.SmallInteger(), server_default='1', nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('cohort_month', 'period_index', 'tz_generation', name='uq_agg_customer_cohort_monthly_key')
    )
    op.create_index(op.f('ix_agg_customer_cohort_monthly_bucket_date'), 'agg_customer_cohort_monthly', ['bucket_date'], unique=False)
    op.create_index('ix_agg_customer_cohort_monthly_cohort_month', 'agg_customer_cohort_monthly', ['cohort_month'], unique=False)
    op.create_table('agg_customer_daily',
    sa.Column('new_customers', sa.Integer(), server_default='0', nullable=False),
    sa.Column('returning_customers', sa.Integer(), server_default='0', nullable=False),
    sa.Column('active_customers', sa.Integer(), server_default='0', nullable=False),
    sa.Column('orders_new', sa.Integer(), server_default='0', nullable=False),
    sa.Column('orders_returning', sa.Integer(), server_default='0', nullable=False),
    sa.Column('revenue_new', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('revenue_returning', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('bucket_date', sa.Date(), nullable=False),
    sa.Column('tz_generation', sa.SmallInteger(), server_default='1', nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('bucket_date', 'tz_generation', name='uq_agg_customer_daily_key')
    )
    op.create_index(op.f('ix_agg_customer_daily_bucket_date'), 'agg_customer_daily', ['bucket_date'], unique=False)
    op.create_table('agg_customer_snapshot',
    sa.Column('customer_key', sa.String(length=64), server_default='-', nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('first_order_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_order_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('orders_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('units', sa.Integer(), server_default='0', nullable=False),
    sa.Column('gross_ltv', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('net_ltv', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('margin_ltv', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('aov', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('recency_days', sa.Integer(), server_default='0', nullable=False),
    sa.Column('frequency', sa.Integer(), server_default='0', nullable=False),
    sa.Column('monetary', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('r_score', sa.SmallInteger(), server_default='0', nullable=False),
    sa.Column('f_score', sa.SmallInteger(), server_default='0', nullable=False),
    sa.Column('m_score', sa.SmallInteger(), server_default='0', nullable=False),
    sa.Column('rfm_segment', sa.String(length=32), server_default='-', nullable=False),
    sa.Column('cohort_month', sa.String(length=7), server_default='-', nullable=False),
    sa.Column('tenure_days', sa.Integer(), server_default='0', nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('churn_risk_band', sa.String(length=16), server_default='-', nullable=False),
    sa.Column('preferred_payment_method', sa.String(length=20), server_default='-', nullable=False),
    sa.Column('quality', sa.String(length=16), server_default='unknown', nullable=False),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('bucket_date', sa.Date(), nullable=False),
    sa.Column('tz_generation', sa.SmallInteger(), server_default='1', nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('bucket_date', 'customer_key', 'tz_generation', name='uq_agg_customer_snapshot_key')
    )
    op.create_index(op.f('ix_agg_customer_snapshot_bucket_date'), 'agg_customer_snapshot', ['bucket_date'], unique=False)
    op.create_index('ix_agg_customer_snapshot_bucket_date_segment', 'agg_customer_snapshot', ['bucket_date', 'rfm_segment'], unique=False)
    op.create_index('ix_agg_customer_snapshot_customer_key_bucket_date', 'agg_customer_snapshot', ['customer_key', 'bucket_date'], unique=False)
    op.create_table('agg_funnel_daily',
    sa.Column('product_views', sa.Integer(), server_default='0', nullable=False),
    sa.Column('cart_views', sa.Integer(), server_default='0', nullable=False),
    sa.Column('items_added', sa.Integer(), server_default='0', nullable=False),
    sa.Column('checkouts_started', sa.Integer(), server_default='0', nullable=False),
    sa.Column('shipping_submitted', sa.Integer(), server_default='0', nullable=False),
    sa.Column('payments_initiated', sa.Integer(), server_default='0', nullable=False),
    sa.Column('payments_failed', sa.Integer(), server_default='0', nullable=False),
    sa.Column('orders_placed', sa.Integer(), server_default='0', nullable=False),
    sa.Column('distinct_sessions', sa.Integer(), server_default='0', nullable=False),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('bucket_date', sa.Date(), nullable=False),
    sa.Column('tz_generation', sa.SmallInteger(), server_default='1', nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('bucket_date', 'tz_generation', name='uq_agg_funnel_daily_key')
    )
    op.create_index(op.f('ix_agg_funnel_daily_bucket_date'), 'agg_funnel_daily', ['bucket_date'], unique=False)
    op.create_table('agg_geo_daily',
    sa.Column('state', sa.String(length=64), server_default='-', nullable=False),
    sa.Column('pincode', sa.String(length=10), server_default='-', nullable=False),
    sa.Column('orders', sa.Integer(), server_default='0', nullable=False),
    sa.Column('net_revenue', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('units', sa.Integer(), server_default='0', nullable=False),
    sa.Column('cod_orders', sa.Integer(), server_default='0', nullable=False),
    sa.Column('prepaid_orders', sa.Integer(), server_default='0', nullable=False),
    sa.Column('delivered', sa.Integer(), server_default='0', nullable=False),
    sa.Column('rto', sa.Integer(), server_default='0', nullable=False),
    sa.Column('sum_delivery_seconds', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('n_delivery', sa.Integer(), server_default='0', nullable=False),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('bucket_date', sa.Date(), nullable=False),
    sa.Column('tz_generation', sa.SmallInteger(), server_default='1', nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('bucket_date', 'state', 'pincode', 'tz_generation', name='uq_agg_geo_daily_key')
    )
    op.create_index(op.f('ix_agg_geo_daily_bucket_date'), 'agg_geo_daily', ['bucket_date'], unique=False)
    op.create_index('ix_agg_geo_daily_bucket_date_state', 'agg_geo_daily', ['bucket_date', 'state'], unique=False)
    op.create_table('agg_inventory_daily',
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('sku_snapshot', sa.String(length=64), server_default='-', nullable=False),
    sa.Column('stock_close', sa.Integer(), server_default='0', nullable=False),
    sa.Column('stock_value_close', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('units_sold', sa.Integer(), server_default='0', nullable=False),
    sa.Column('units_restocked', sa.Integer(), server_default='0', nullable=False),
    sa.Column('is_oos', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('days_oos', sa.Integer(), server_default='0', nullable=False),
    sa.Column('reorder_gap', sa.Integer(), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('bucket_date', sa.Date(), nullable=False),
    sa.Column('tz_generation', sa.SmallInteger(), server_default='1', nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('bucket_date', 'product_id', 'tz_generation', name='uq_agg_inventory_daily_key')
    )
    op.create_index(op.f('ix_agg_inventory_daily_bucket_date'), 'agg_inventory_daily', ['bucket_date'], unique=False)
    op.create_index('ix_agg_inventory_daily_product_bucket_date', 'agg_inventory_daily', ['product_id', 'bucket_date'], unique=False)
    op.create_table('agg_order_daily',
    sa.Column('orders_total', sa.Integer(), server_default='0', nullable=False),
    sa.Column('orders_pending', sa.Integer(), server_default='0', nullable=False),
    sa.Column('orders_paid', sa.Integer(), server_default='0', nullable=False),
    sa.Column('orders_shipped', sa.Integer(), server_default='0', nullable=False),
    sa.Column('orders_delivered', sa.Integer(), server_default='0', nullable=False),
    sa.Column('orders_cancelled', sa.Integer(), server_default='0', nullable=False),
    sa.Column('orders_refunded', sa.Integer(), server_default='0', nullable=False),
    sa.Column('order_value_created', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('paid_order_value', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('gross_merchandise_sales', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('net_merchandise_sales', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('net_revenue', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('subtotal_sum', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('tax_sum', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('discount_sum', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('payment_discount_sum', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('shipping_income', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('cod_surcharge_sum', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('refund_sum', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('cogs_sum', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('units', sa.Integer(), server_default='0', nullable=False),
    sa.Column('costed_units', sa.Integer(), server_default='0', nullable=False),
    sa.Column('distinct_customers', sa.Integer(), server_default='0', nullable=False),
    sa.Column('new_customers', sa.Integer(), server_default='0', nullable=False),
    sa.Column('returning_customers', sa.Integer(), server_default='0', nullable=False),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('bucket_date', sa.Date(), nullable=False),
    sa.Column('tz_generation', sa.SmallInteger(), server_default='1', nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('bucket_date', 'tz_generation', name='uq_agg_order_daily_key')
    )
    op.create_index(op.f('ix_agg_order_daily_bucket_date'), 'agg_order_daily', ['bucket_date'], unique=False)
    op.create_table('agg_order_hourly',
    sa.Column('bucket_hour', sa.SmallInteger(), nullable=False),
    sa.Column('orders', sa.Integer(), server_default='0', nullable=False),
    sa.Column('net_revenue', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('units', sa.Integer(), server_default='0', nullable=False),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('bucket_date', sa.Date(), nullable=False),
    sa.Column('tz_generation', sa.SmallInteger(), server_default='1', nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('bucket_date', 'bucket_hour', 'tz_generation', name='uq_agg_order_hourly_key')
    )
    op.create_index(op.f('ix_agg_order_hourly_bucket_date'), 'agg_order_hourly', ['bucket_date'], unique=False)
    op.create_table('agg_payment_daily',
    sa.Column('gateway', sa.String(length=40), server_default='-', nullable=False),
    sa.Column('payment_method', sa.String(length=32), server_default='-', nullable=False),
    sa.Column('payment_instrument', sa.String(length=32), server_default='-', nullable=False),
    sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('paid', sa.Integer(), server_default='0', nullable=False),
    sa.Column('failed', sa.Integer(), server_default='0', nullable=False),
    sa.Column('cancelled', sa.Integer(), server_default='0', nullable=False),
    sa.Column('refunded', sa.Integer(), server_default='0', nullable=False),
    sa.Column('paid_amount', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('failed_amount', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('sum_settle_seconds', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('n_settle', sa.Integer(), server_default='0', nullable=False),
    sa.Column('mismatch_events', sa.Integer(), server_default='0', nullable=False),
    sa.Column('top_failure_reason', sa.String(length=120), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('bucket_date', sa.Date(), nullable=False),
    sa.Column('tz_generation', sa.SmallInteger(), server_default='1', nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('bucket_date', 'gateway', 'payment_method', 'payment_instrument', 'tz_generation', name='uq_agg_payment_daily_key')
    )
    op.create_index(op.f('ix_agg_payment_daily_bucket_date'), 'agg_payment_daily', ['bucket_date'], unique=False)
    op.create_index('ix_agg_payment_daily_bucket_date_gateway', 'agg_payment_daily', ['bucket_date', 'gateway'], unique=False)
    op.create_table('agg_product_daily',
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('sku_snapshot', sa.String(length=64), server_default='-', nullable=False),
    sa.Column('category_id_snapshot', sa.Integer(), nullable=True),
    sa.Column('units', sa.Integer(), server_default='0', nullable=False),
    sa.Column('orders', sa.Integer(), server_default='0', nullable=False),
    sa.Column('gross_merchandise_sales', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('net_merchandise_sales', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('line_cost', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('costed_units', sa.Integer(), server_default='0', nullable=False),
    sa.Column('returned_units', sa.Integer(), server_default='0', nullable=False),
    sa.Column('returned_value', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('bucket_date', sa.Date(), nullable=False),
    sa.Column('tz_generation', sa.SmallInteger(), server_default='1', nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('bucket_date', 'product_id', 'tz_generation', name='uq_agg_product_daily_key')
    )
    op.create_index(op.f('ix_agg_product_daily_bucket_date'), 'agg_product_daily', ['bucket_date'], unique=False)
    op.create_index('ix_agg_product_daily_bucket_date_category', 'agg_product_daily', ['bucket_date', 'category_id_snapshot'], unique=False)
    op.create_index('ix_agg_product_daily_product_bucket_date', 'agg_product_daily', ['product_id', 'bucket_date'], unique=False)
    op.create_table('agg_promo_daily',
    sa.Column('coupon_code', sa.String(length=64), server_default='-', nullable=False),
    sa.Column('redemptions', sa.Integer(), server_default='0', nullable=False),
    sa.Column('discount_amount', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('orders', sa.Integer(), server_default='0', nullable=False),
    sa.Column('order_revenue', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('is_loyalty_reward', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('bucket_date', sa.Date(), nullable=False),
    sa.Column('tz_generation', sa.SmallInteger(), server_default='1', nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('bucket_date', 'coupon_code', 'tz_generation', name='uq_agg_promo_daily_key')
    )
    op.create_index(op.f('ix_agg_promo_daily_bucket_date'), 'agg_promo_daily', ['bucket_date'], unique=False)
    op.create_index('ix_agg_promo_daily_coupon_code_bucket_date', 'agg_promo_daily', ['coupon_code', 'bucket_date'], unique=False)
    op.create_table('agg_shipment_daily',
    sa.Column('courier_partner', sa.String(length=64), server_default='-', nullable=False),
    sa.Column('shipments', sa.Integer(), server_default='0', nullable=False),
    sa.Column('delivered', sa.Integer(), server_default='0', nullable=False),
    sa.Column('delivery_failed', sa.Integer(), server_default='0', nullable=False),
    sa.Column('rto_initiated', sa.Integer(), server_default='0', nullable=False),
    sa.Column('rto_delivered', sa.Integer(), server_default='0', nullable=False),
    sa.Column('cancelled', sa.Integer(), server_default='0', nullable=False),
    sa.Column('shipment_cost', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('sum_order_to_ship_seconds', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('n_order_to_ship', sa.Integer(), server_default='0', nullable=False),
    sa.Column('sum_ship_to_deliver_seconds', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('n_ship_to_deliver', sa.Integer(), server_default='0', nullable=False),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('bucket_date', sa.Date(), nullable=False),
    sa.Column('tz_generation', sa.SmallInteger(), server_default='1', nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('bucket_date', 'courier_partner', 'tz_generation', name='uq_agg_shipment_daily_key')
    )
    op.create_index(op.f('ix_agg_shipment_daily_bucket_date'), 'agg_shipment_daily', ['bucket_date'], unique=False)
    op.create_index('ix_agg_shipment_daily_bucket_date_courier', 'agg_shipment_daily', ['bucket_date', 'courier_partner'], unique=False)
    op.create_table('analytics_alerts',
    sa.Column('rule_key', sa.String(length=64), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('metric', sa.String(length=64), nullable=False),
    sa.Column('dimension', sa.String(length=32), server_default='-', nullable=False),
    sa.Column('dimension_value', sa.String(length=64), server_default='-', nullable=False),
    sa.Column('expected_low', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('expected_high', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('actual_value', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('bucket_date', sa.Date(), nullable=True),
    sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='open', nullable=False),
    sa.Column('acknowledged_by_user_id', sa.Integer(), nullable=True),
    sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolution_note', sa.String(length=500), nullable=True),
    sa.Column('context', sa.JSON(), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_analytics_alerts_created_at'), 'analytics_alerts', ['created_at'], unique=False)
    op.create_index('ix_analytics_alerts_rule_detected', 'analytics_alerts', ['rule_key', 'detected_at'], unique=False)
    op.create_index('ix_analytics_alerts_status_detected', 'analytics_alerts', ['status', 'detected_at'], unique=False)
    op.create_table('analytics_budgets',
    sa.Column('period_start', sa.Date(), nullable=False),
    sa.Column('period_end', sa.Date(), nullable=False),
    sa.Column('granularity', sa.String(length=16), nullable=False),
    sa.Column('metric', sa.String(length=64), nullable=False),
    sa.Column('dimension', sa.String(length=32), server_default='-', nullable=False),
    sa.Column('dimension_value', sa.String(length=64), server_default='-', nullable=False),
    sa.Column('budget_amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('currency', sa.String(length=3), server_default='INR', nullable=False),
    sa.Column('note', sa.String(length=255), nullable=True),
    sa.Column('created_by_user_id', sa.Integer(), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('period_start', 'granularity', 'metric', 'dimension', 'dimension_value', name='uq_analytics_budgets_period_gran_metric_dim')
    )
    op.create_index('ix_analytics_budgets_metric_period', 'analytics_budgets', ['metric', 'period_start', 'period_end'], unique=False)
    op.create_table('analytics_cost_rules',
    sa.Column('cost_type', sa.String(length=48), nullable=False),
    sa.Column('scope', sa.String(length=24), server_default='global', nullable=False),
    sa.Column('scope_value', sa.String(length=64), server_default='-', nullable=False),
    sa.Column('value', sa.Numeric(precision=14, scale=4), nullable=False),
    sa.Column('unit', sa.String(length=24), nullable=False),
    sa.Column('currency', sa.String(length=3), server_default='INR', nullable=False),
    sa.Column('quality', sa.String(length=16), nullable=False),
    sa.Column('effective_from', sa.Date(), nullable=False),
    sa.Column('effective_to', sa.Date(), nullable=True),
    sa.Column('source', sa.String(length=64), nullable=True),
    sa.Column('note', sa.String(length=255), nullable=True),
    sa.Column('created_by_user_id', sa.Integer(), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('cost_type', 'scope', 'scope_value', 'effective_from', name='uq_analytics_cost_rules_type_scope_value_from')
    )
    op.create_index(op.f('ix_analytics_cost_rules_created_at'), 'analytics_cost_rules', ['created_at'], unique=False)
    op.create_index('ix_analytics_cost_rules_type_from_to', 'analytics_cost_rules', ['cost_type', 'effective_from', 'effective_to'], unique=False)
    op.create_table('analytics_event_outbox',
    sa.Column('event_name', sa.String(length=40), nullable=False),
    sa.Column('transaction_id', sa.String(length=64), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('client_id', sa.String(length=64), nullable=True),
    sa.Column('session_id', sa.String(length=64), nullable=True),
    sa.Column('consent_state', sa.String(length=24), nullable=False),
    sa.Column('status', sa.String(length=24), server_default='pending', nullable=False),
    sa.Column('attempts', sa.SmallInteger(), server_default='0', nullable=False),
    sa.Column('last_error', sa.String(length=500), nullable=True),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('event_name', 'transaction_id', name='uq_analytics_event_outbox_event_txn')
    )
    op.create_index(op.f('ix_analytics_event_outbox_created_at'), 'analytics_event_outbox', ['created_at'], unique=False)
    op.create_index(op.f('ix_analytics_event_outbox_order_id'), 'analytics_event_outbox', ['order_id'], unique=False)
    op.create_index('ix_analytics_event_outbox_status_occurred', 'analytics_event_outbox', ['status', 'occurred_at'], unique=False)
    op.create_table('analytics_order_adjustment',
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('order_line_id', sa.BigInteger(), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('bucket_date', sa.Date(), nullable=False),
    sa.Column('tz_generation', sa.SmallInteger(), server_default='1', nullable=False),
    sa.Column('adjustment_type', sa.String(length=40), nullable=False),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('currency', sa.String(length=3), server_default='INR', nullable=False),
    sa.Column('quality', sa.String(length=16), nullable=False),
    sa.Column('source_ref_type', sa.String(length=32), nullable=True),
    sa.Column('source_ref_id', sa.Integer(), nullable=True),
    sa.Column('event_key', sa.String(length=120), nullable=False),
    sa.Column('note', sa.String(length=255), nullable=True),
    sa.Column('raw', sa.JSON(), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('event_key')
    )
    op.create_index(op.f('ix_analytics_order_adjustment_adjustment_type'), 'analytics_order_adjustment', ['adjustment_type'], unique=False)
    op.create_index(op.f('ix_analytics_order_adjustment_bucket_date'), 'analytics_order_adjustment', ['bucket_date'], unique=False)
    op.create_index(op.f('ix_analytics_order_adjustment_created_at'), 'analytics_order_adjustment', ['created_at'], unique=False)
    op.create_index(op.f('ix_analytics_order_adjustment_occurred_at'), 'analytics_order_adjustment', ['occurred_at'], unique=False)
    op.create_index(op.f('ix_analytics_order_adjustment_order_id'), 'analytics_order_adjustment', ['order_id'], unique=False)
    op.create_table('analytics_order_line',
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('order_item_id', sa.Integer(), nullable=False),
    sa.Column('ordered_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('bucket_date', sa.Date(), nullable=False),
    sa.Column('tz_generation', sa.SmallInteger(), server_default='1', nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('sku_snapshot', sa.String(length=64), nullable=False),
    sa.Column('product_name_snapshot', sa.String(length=255), nullable=False),
    sa.Column('category_id_snapshot', sa.Integer(), nullable=True),
    sa.Column('category_name_snapshot', sa.String(length=120), nullable=True),
    sa.Column('brand_snapshot', sa.String(length=120), nullable=True),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('unit_price', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('unit_cost', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('extended_price', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('alloc_discount', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('alloc_tax', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('alloc_shipping', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('alloc_cod_surcharge', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('alloc_payment_discount', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('alloc_gateway_fee', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('cost_quality', sa.String(length=16), nullable=False),
    sa.Column('alloc_quality', sa.String(length=16), nullable=False),
    sa.Column('identity_source', sa.String(length=32), nullable=False),
    sa.Column('order_status', sa.String(length=20), server_default='-', nullable=False),
    sa.Column('payment_method', sa.String(length=20), server_default='-', nullable=False),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('order_item_id')
    )
    op.create_index(op.f('ix_analytics_order_line_bucket_date'), 'analytics_order_line', ['bucket_date'], unique=False)
    op.create_index(op.f('ix_analytics_order_line_created_at'), 'analytics_order_line', ['created_at'], unique=False)
    op.create_index(op.f('ix_analytics_order_line_order_id'), 'analytics_order_line', ['order_id'], unique=False)
    op.create_index(op.f('ix_analytics_order_line_product_id'), 'analytics_order_line', ['product_id'], unique=False)
    op.create_index('ix_aol_bucket_category', 'analytics_order_line', ['bucket_date', 'category_id_snapshot'], unique=False)
    op.create_index('ix_aol_bucket_product', 'analytics_order_line', ['bucket_date', 'product_id'], unique=False)
    op.create_table('analytics_recompute_queue',
    sa.Column('job', sa.String(length=64), nullable=False),
    sa.Column('bucket_date', sa.Date(), nullable=False),
    sa.Column('dimension_key', sa.String(length=96), server_default='-', nullable=False),
    sa.Column('tz_generation', sa.SmallInteger(), server_default='1', nullable=False),
    sa.Column('reason', sa.String(length=48), nullable=False),
    sa.Column('priority', sa.SmallInteger(), server_default='5', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='pending', nullable=False),
    sa.Column('attempts', sa.SmallInteger(), server_default='0', nullable=False),
    sa.Column('last_error', sa.String(length=500), nullable=True),
    sa.Column('enqueued_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('claimed_by', sa.String(length=64), nullable=True),
    sa.Column('claim_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('job', 'bucket_date', 'dimension_key', 'tz_generation', name='uq_analytics_recompute_queue_job_date_dim_tz')
    )
    op.create_index('ix_analytics_recompute_queue_status_priority_date', 'analytics_recompute_queue', ['status', 'priority', 'bucket_date'], unique=False)
    op.create_table('analytics_sync_runs',
    sa.Column('job', sa.String(length=64), nullable=False),
    sa.Column('trigger', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('worker_id', sa.String(length=64), nullable=True),
    sa.Column('window_from', sa.Date(), nullable=True),
    sa.Column('window_to', sa.Date(), nullable=True),
    sa.Column('tz_generation', sa.SmallInteger(), server_default='1', nullable=False),
    sa.Column('days_requested', sa.SmallInteger(), server_default='0', nullable=False),
    sa.Column('days_processed', sa.SmallInteger(), server_default='0', nullable=False),
    sa.Column('rows_written', sa.Integer(), server_default='0', nullable=False),
    sa.Column('rows_deleted', sa.Integer(), server_default='0', nullable=False),
    sa.Column('watermark_date', sa.Date(), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('error', sa.String(length=500), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_analytics_sync_runs_job_started', 'analytics_sync_runs', ['job', 'started_at'], unique=False)
    op.create_index('ix_analytics_sync_runs_status_started', 'analytics_sync_runs', ['status', 'started_at'], unique=False)
    op.create_table('analytics_tz_generations',
    sa.Column('generation', sa.SmallInteger(), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('effective_from', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('rebuild_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('rebuild_finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by_user_id', sa.Integer(), nullable=True),
    sa.Column('note', sa.String(length=255), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('generation', name='uq_analytics_tz_generations_generation')
    )
    op.create_index(op.f('ix_analytics_tz_generations_created_at'), 'analytics_tz_generations', ['created_at'], unique=False)
    op.create_index(op.f('ix_analytics_tz_generations_status'), 'analytics_tz_generations', ['status'], unique=False)
    op.create_table('cart_events',
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('session_key', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('event_type', sa.String(length=32), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=True),
    sa.Column('quantity', sa.Integer(), nullable=True),
    sa.Column('value', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('currency', sa.String(length=3), server_default='INR', nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=True),
    sa.Column('event_key', sa.String(length=120), nullable=False),
    sa.Column('meta', sa.JSON(), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('event_key')
    )
    op.create_index(op.f('ix_cart_events_created_at'), 'cart_events', ['created_at'], unique=False)
    op.create_index(op.f('ix_cart_events_event_type'), 'cart_events', ['event_type'], unique=False)
    op.create_index(op.f('ix_cart_events_occurred_at'), 'cart_events', ['occurred_at'], unique=False)
    op.create_index(op.f('ix_cart_events_order_id'), 'cart_events', ['order_id'], unique=False)
    op.create_index(op.f('ix_cart_events_session_key'), 'cart_events', ['session_key'], unique=False)
    op.create_index('ix_cart_events_session_occurred', 'cart_events', ['session_key', 'occurred_at'], unique=False)
    op.create_index('ix_cart_events_type_occurred', 'cart_events', ['event_type', 'occurred_at'], unique=False)
    op.create_index(op.f('ix_cart_events_user_id'), 'cart_events', ['user_id'], unique=False)
    op.create_table('inventory_movements',
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('movement_type', sa.String(length=32), nullable=False),
    sa.Column('delta', sa.Integer(), nullable=False),
    sa.Column('stock_after', sa.Integer(), nullable=True),
    sa.Column('ref_type', sa.String(length=32), nullable=True),
    sa.Column('ref_id', sa.Integer(), nullable=True),
    sa.Column('actor_user_id', sa.Integer(), nullable=True),
    sa.Column('event_key', sa.String(length=120), nullable=False),
    sa.Column('note', sa.String(length=255), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('event_key')
    )
    op.create_index(op.f('ix_inventory_movements_created_at'), 'inventory_movements', ['created_at'], unique=False)
    op.create_index(op.f('ix_inventory_movements_movement_type'), 'inventory_movements', ['movement_type'], unique=False)
    op.create_index(op.f('ix_inventory_movements_occurred_at'), 'inventory_movements', ['occurred_at'], unique=False)
    op.create_index(op.f('ix_inventory_movements_product_id'), 'inventory_movements', ['product_id'], unique=False)
    op.create_index('ix_invmov_product_occurred', 'inventory_movements', ['product_id', 'occurred_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_inventory_movements_created_at'), table_name='inventory_movements')
    op.drop_index(op.f('ix_inventory_movements_movement_type'), table_name='inventory_movements')
    op.drop_index(op.f('ix_inventory_movements_occurred_at'), table_name='inventory_movements')
    op.drop_index(op.f('ix_inventory_movements_product_id'), table_name='inventory_movements')
    op.drop_index('ix_invmov_product_occurred', table_name='inventory_movements')
    op.drop_table('inventory_movements')
    op.drop_index(op.f('ix_cart_events_created_at'), table_name='cart_events')
    op.drop_index(op.f('ix_cart_events_event_type'), table_name='cart_events')
    op.drop_index(op.f('ix_cart_events_occurred_at'), table_name='cart_events')
    op.drop_index(op.f('ix_cart_events_order_id'), table_name='cart_events')
    op.drop_index(op.f('ix_cart_events_session_key'), table_name='cart_events')
    op.drop_index('ix_cart_events_session_occurred', table_name='cart_events')
    op.drop_index('ix_cart_events_type_occurred', table_name='cart_events')
    op.drop_index(op.f('ix_cart_events_user_id'), table_name='cart_events')
    op.drop_table('cart_events')
    op.drop_index(op.f('ix_analytics_tz_generations_created_at'), table_name='analytics_tz_generations')
    op.drop_index(op.f('ix_analytics_tz_generations_status'), table_name='analytics_tz_generations')
    op.drop_table('analytics_tz_generations')
    op.drop_index('ix_analytics_sync_runs_job_started', table_name='analytics_sync_runs')
    op.drop_index('ix_analytics_sync_runs_status_started', table_name='analytics_sync_runs')
    op.drop_table('analytics_sync_runs')
    op.drop_index('ix_analytics_recompute_queue_status_priority_date', table_name='analytics_recompute_queue')
    op.drop_table('analytics_recompute_queue')
    op.drop_index(op.f('ix_analytics_order_line_bucket_date'), table_name='analytics_order_line')
    op.drop_index(op.f('ix_analytics_order_line_created_at'), table_name='analytics_order_line')
    op.drop_index(op.f('ix_analytics_order_line_order_id'), table_name='analytics_order_line')
    op.drop_index(op.f('ix_analytics_order_line_product_id'), table_name='analytics_order_line')
    op.drop_index('ix_aol_bucket_category', table_name='analytics_order_line')
    op.drop_index('ix_aol_bucket_product', table_name='analytics_order_line')
    op.drop_table('analytics_order_line')
    op.drop_index(op.f('ix_analytics_order_adjustment_adjustment_type'), table_name='analytics_order_adjustment')
    op.drop_index(op.f('ix_analytics_order_adjustment_bucket_date'), table_name='analytics_order_adjustment')
    op.drop_index(op.f('ix_analytics_order_adjustment_created_at'), table_name='analytics_order_adjustment')
    op.drop_index(op.f('ix_analytics_order_adjustment_occurred_at'), table_name='analytics_order_adjustment')
    op.drop_index(op.f('ix_analytics_order_adjustment_order_id'), table_name='analytics_order_adjustment')
    op.drop_table('analytics_order_adjustment')
    op.drop_index(op.f('ix_analytics_event_outbox_created_at'), table_name='analytics_event_outbox')
    op.drop_index(op.f('ix_analytics_event_outbox_order_id'), table_name='analytics_event_outbox')
    op.drop_index('ix_analytics_event_outbox_status_occurred', table_name='analytics_event_outbox')
    op.drop_table('analytics_event_outbox')
    op.drop_index(op.f('ix_analytics_cost_rules_created_at'), table_name='analytics_cost_rules')
    op.drop_index('ix_analytics_cost_rules_type_from_to', table_name='analytics_cost_rules')
    op.drop_table('analytics_cost_rules')
    op.drop_index('ix_analytics_budgets_metric_period', table_name='analytics_budgets')
    op.drop_table('analytics_budgets')
    op.drop_index(op.f('ix_analytics_alerts_created_at'), table_name='analytics_alerts')
    op.drop_index('ix_analytics_alerts_rule_detected', table_name='analytics_alerts')
    op.drop_index('ix_analytics_alerts_status_detected', table_name='analytics_alerts')
    op.drop_table('analytics_alerts')
    op.drop_index(op.f('ix_agg_shipment_daily_bucket_date'), table_name='agg_shipment_daily')
    op.drop_index('ix_agg_shipment_daily_bucket_date_courier', table_name='agg_shipment_daily')
    op.drop_table('agg_shipment_daily')
    op.drop_index(op.f('ix_agg_promo_daily_bucket_date'), table_name='agg_promo_daily')
    op.drop_index('ix_agg_promo_daily_coupon_code_bucket_date', table_name='agg_promo_daily')
    op.drop_table('agg_promo_daily')
    op.drop_index(op.f('ix_agg_product_daily_bucket_date'), table_name='agg_product_daily')
    op.drop_index('ix_agg_product_daily_bucket_date_category', table_name='agg_product_daily')
    op.drop_index('ix_agg_product_daily_product_bucket_date', table_name='agg_product_daily')
    op.drop_table('agg_product_daily')
    op.drop_index(op.f('ix_agg_payment_daily_bucket_date'), table_name='agg_payment_daily')
    op.drop_index('ix_agg_payment_daily_bucket_date_gateway', table_name='agg_payment_daily')
    op.drop_table('agg_payment_daily')
    op.drop_index(op.f('ix_agg_order_hourly_bucket_date'), table_name='agg_order_hourly')
    op.drop_table('agg_order_hourly')
    op.drop_index(op.f('ix_agg_order_daily_bucket_date'), table_name='agg_order_daily')
    op.drop_table('agg_order_daily')
    op.drop_index(op.f('ix_agg_inventory_daily_bucket_date'), table_name='agg_inventory_daily')
    op.drop_index('ix_agg_inventory_daily_product_bucket_date', table_name='agg_inventory_daily')
    op.drop_table('agg_inventory_daily')
    op.drop_index(op.f('ix_agg_geo_daily_bucket_date'), table_name='agg_geo_daily')
    op.drop_index('ix_agg_geo_daily_bucket_date_state', table_name='agg_geo_daily')
    op.drop_table('agg_geo_daily')
    op.drop_index(op.f('ix_agg_funnel_daily_bucket_date'), table_name='agg_funnel_daily')
    op.drop_table('agg_funnel_daily')
    op.drop_index(op.f('ix_agg_customer_snapshot_bucket_date'), table_name='agg_customer_snapshot')
    op.drop_index('ix_agg_customer_snapshot_bucket_date_segment', table_name='agg_customer_snapshot')
    op.drop_index('ix_agg_customer_snapshot_customer_key_bucket_date', table_name='agg_customer_snapshot')
    op.drop_table('agg_customer_snapshot')
    op.drop_index(op.f('ix_agg_customer_daily_bucket_date'), table_name='agg_customer_daily')
    op.drop_table('agg_customer_daily')
    op.drop_index(op.f('ix_agg_customer_cohort_monthly_bucket_date'), table_name='agg_customer_cohort_monthly')
    op.drop_index('ix_agg_customer_cohort_monthly_cohort_month', table_name='agg_customer_cohort_monthly')
    op.drop_table('agg_customer_cohort_monthly')
