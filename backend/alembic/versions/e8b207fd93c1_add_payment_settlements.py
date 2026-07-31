"""add payment_settlements and agg_settlement_daily

WHAT THIS DOES
--------------
Creates the two tables that record what the payment gateway actually PAID OUT,
as opposed to what a customer was charged:

  * `payment_settlements`   — one immutable row per settlement line exactly as
                              the gateway reported it (a captured payment, a
                              refund, a chargeback, an adjustment).
  * `agg_settlement_daily`  — the daily rollup the reconciliation view reads.

Nothing else is touched. No existing table gains, loses or alters a column,
index or constraint.

WHY
---
`app/models/analytics_facts.py` states it plainly: no gateway fee is stored
anywhere in this schema — not on `orders`, not on `order_payments`, not on
`payment_events`. Every gateway fee the system reports today is therefore
ESTIMATED, produced by applying a `gateway_fee` cost rule to prepaid order
value. A cost rule is a forecast; a settlement report is a measurement. These
tables are where the measurement lands, so that a fee tied to a real settlement
line can be labelled ACTUAL and one that could not be tied stays ESTIMATED and
says so.

THE TWO DESIGN DECISIONS WORTH REVIEWING
----------------------------------------
1. **Money is signed integer paise (BIGINT), not DECIMAL(14,2).**
   Every other analytics table uses DECIMAL(14,2) and this one deliberately does
   not. It is the only table in the schema whose purpose is an equality against
   a third party's arithmetic: `gross - fee - tax = net` must hold to the paisa,
   and a settlement that reconciles to the rupee but not the paisa has not
   reconciled. `ck_payment_settlements_net` enforces the identity IN THE
   DATABASE, so no code path — including a hand-run INSERT during an incident —
   can store a line whose arithmetic does not close.

   The rollup goes back to DECIMAL(14,2) because it is read by the same
   resolvers as every other agg_* table.

   NOTE FOR THE HAND-APPLIED TWIN: MySQL 8 enforces CHECK constraints (5.7
   parsed and ignored them). If the shared host is 5.7 the constraint is inert
   and the identity is only enforced by the ingest service — `settlement_daily`
   emits a `settlement_net_identity_broken` warning per offending row, so the
   loss is visible rather than silent.

2. **UNIQUE (gateway, transaction_id), deliberately WITHOUT settlement_id.**
   This is the key that makes re-uploading the same settlement file a no-op
   instead of a doubling, and finance will upload the same file twice. It
   excludes `settlement_id` because Razorpay reports a captured payment that is
   still on hold with an EMPTY settlement id, then reports the same transaction
   again once it settles. Keying on both would store that payment twice and
   double-count its fee. Keying on the transaction alone lets the second report
   update the first in place, which is what actually happened.

   The cost is stated rather than hidden: a transaction genuinely split across
   two settlement batches would collapse to the later batch. Razorpay does not
   split a payment across batches.

TWO DATES, AND THEY ARE NOT INTERCHANGEABLE
-------------------------------------------
`transacted_at` / `payment_date` is when the gateway created the transaction —
the fee belongs to this day, in the same bucket as the revenue it was charged
against. `settled_at` / `settlement_date` is when the payout reached the bank —
the cash belongs to this day, typically two or three days later, and is NULL
while the line is on hold. `agg_settlement_daily` therefore carries two families
of columns, suffixed `_transacted` and `_settled`, over two different
populations. They are never summed together.

NO FOREIGN KEYS
---------------
`order_id` / `order_payment_id` are plain integers, per the convention in
`app/models/analytics_base.py`: these tables must stay independently truncatable
and re-ingestable from the source file, and a settlement line records something
that happened at a third party and must survive an order being deleted.

SAFETY
------
Purely additive — two CREATE TABLEs and their indexes, nothing else — so it is
safe to apply one deploy BEFORE the code that reads them, which is the required
order. Re-running fails harmlessly on duplicate-table errors without touching
data.

CHAINING
--------
Six agents were adding migrations concurrently when this was written, so the
lineage had multiple heads. This revision chains off `a3d82f5c1e94`
(`add_agg_loyalty_daily`), which was BOTH a head and already applied to the CI
database at the time, so `alembic upgrade e8b207fd93c1` runs cleanly from that
state. The set is re-linearised centrally afterwards; re-pointing
`down_revision` does not invalidate any DDL here, because this revision creates
new tables and depends on nothing any other revision does.

TWIN SQL
--------
`backend/scripts/sql/2026-07-29_payment_settlements.sql` was generated from this
revision with

    alembic upgrade a3d82f5c1e94:e8b207fd93c1 --sql

because production applies hand-reviewed SQL and never runs alembic (DEPLOY.md
§6). Regenerate it the same way if this revision changes; the two artifacts must
not be able to disagree.

Revision ID: e8b207fd93c1
Revises: a3d82f5c1e94
Create Date: 2026-07-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e8b207fd93c1'
down_revision: Union[str, None] = 'a3d82f5c1e94'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'payment_settlements',
        sa.Column('gateway', sa.String(length=40), server_default='-', nullable=False),
        sa.Column('transaction_id', sa.String(length=128), nullable=False),
        sa.Column('transaction_type', sa.String(length=24), server_default='other', nullable=False),
        sa.Column('settlement_id', sa.String(length=64), server_default='-', nullable=False),
        sa.Column('settlement_utr', sa.String(length=64), server_default='-', nullable=False),
        sa.Column('currency', sa.String(length=3), server_default='INR', nullable=False),
        sa.Column('method', sa.String(length=32), server_default='-', nullable=False),
        sa.Column('gross_minor', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('fee_minor', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('tax_minor', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('net_minor', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('transacted_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('settled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('tz_generation', sa.SmallInteger(), server_default='1', nullable=False),
        sa.Column('payment_date', sa.Date(), nullable=False),
        sa.Column('settlement_date', sa.Date(), nullable=True),
        sa.Column('gateway_payment_id', sa.String(length=128), nullable=True),
        sa.Column('gateway_order_id', sa.String(length=128), nullable=True),
        sa.Column('gateway_reference', sa.String(length=255), nullable=True),
        sa.Column('merchant_transaction_id', sa.String(length=64), nullable=True),
        sa.Column('match_status', sa.String(length=16), server_default='unmatched', nullable=False),
        sa.Column('match_key', sa.String(length=32), server_default='-', nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=True),
        sa.Column('order_payment_id', sa.Integer(), nullable=True),
        sa.Column('source', sa.String(length=16), server_default='csv_upload', nullable=False),
        sa.Column('source_file', sa.String(length=255), server_default='-', nullable=False),
        sa.Column('source_row', sa.Integer(), server_default='0', nullable=False),
        sa.Column('raw', sa.JSON(), nullable=True),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('net_minor = gross_minor - fee_minor - tax_minor', name='ck_payment_settlements_net'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('gateway', 'transaction_id', name='uq_payment_settlements_txn'),
    )
    op.create_index(op.f('ix_payment_settlements_created_at'), 'payment_settlements', ['created_at'], unique=False)
    op.create_index('ix_payment_settlements_match_status', 'payment_settlements', ['match_status', 'payment_date'], unique=False)
    op.create_index('ix_payment_settlements_order_id', 'payment_settlements', ['order_id'], unique=False)
    op.create_index('ix_payment_settlements_payment_date', 'payment_settlements', ['payment_date', 'gateway'], unique=False)
    op.create_index('ix_payment_settlements_settlement_date', 'payment_settlements', ['settlement_date', 'gateway'], unique=False)
    op.create_index('ix_payment_settlements_settlement_id', 'payment_settlements', ['settlement_id'], unique=False)

    op.create_table(
        'agg_settlement_daily',
        sa.Column('gateway', sa.String(length=40), server_default='-', nullable=False),
        sa.Column('txns_transacted', sa.Integer(), server_default='0', nullable=False),
        sa.Column('payments_transacted', sa.Integer(), server_default='0', nullable=False),
        sa.Column('reversals_transacted', sa.Integer(), server_default='0', nullable=False),
        sa.Column('gross_transacted', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
        sa.Column('fee_transacted', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
        sa.Column('tax_transacted', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
        sa.Column('net_transacted', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
        sa.Column('matched_txns', sa.Integer(), server_default='0', nullable=False),
        sa.Column('matched_gross', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
        sa.Column('unmatched_txns', sa.Integer(), server_default='0', nullable=False),
        sa.Column('unmatched_gross', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
        sa.Column('unsettled_payments', sa.Integer(), server_default='0', nullable=False),
        sa.Column('unsettled_amount', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
        sa.Column('txns_settled', sa.Integer(), server_default='0', nullable=False),
        sa.Column('settlement_batches', sa.Integer(), server_default='0', nullable=False),
        sa.Column('gross_settled', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
        sa.Column('fee_settled', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
        sa.Column('tax_settled', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
        sa.Column('payout_amount', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('bucket_date', sa.Date(), nullable=False),
        sa.Column('tz_generation', sa.SmallInteger(), server_default='1', nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('bucket_date', 'gateway', 'tz_generation', name='uq_agg_settlement_daily_key'),
    )
    op.create_index(op.f('ix_agg_settlement_daily_bucket_date'), 'agg_settlement_daily', ['bucket_date'], unique=False)
    op.create_index('ix_agg_settlement_daily_bucket_date_gateway', 'agg_settlement_daily', ['bucket_date', 'gateway'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_agg_settlement_daily_bucket_date_gateway', table_name='agg_settlement_daily')
    op.drop_index(op.f('ix_agg_settlement_daily_bucket_date'), table_name='agg_settlement_daily')
    op.drop_table('agg_settlement_daily')
    op.drop_index('ix_payment_settlements_settlement_id', table_name='payment_settlements')
    op.drop_index('ix_payment_settlements_settlement_date', table_name='payment_settlements')
    op.drop_index('ix_payment_settlements_payment_date', table_name='payment_settlements')
    op.drop_index('ix_payment_settlements_order_id', table_name='payment_settlements')
    op.drop_index('ix_payment_settlements_match_status', table_name='payment_settlements')
    op.drop_index(op.f('ix_payment_settlements_created_at'), table_name='payment_settlements')
    op.drop_table('payment_settlements')
