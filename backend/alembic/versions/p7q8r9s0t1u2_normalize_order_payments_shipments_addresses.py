"""normalize orders: order_payments, shipments, order_addresses + order_number

Order-table normalization (2026-06-21).  The `orders` table had grown to carry
order, payment, shipping, tracking, refund and address-snapshot data all at
once.  This migration extracts payment / shipment / address-snapshot data into
three new normalized tables and adds a human-friendly `order_number`.

NON-DESTRUCTIVE: no existing column is dropped.  The old flat columns on
`orders` stay and are still dual-written by the service layer for backward
compatibility (they are marked DEPRECATED in the model).  This migration only
ADDS tables + one column, then BACKFILLS history into the new tables.

New tables
----------
  order_payments    one row per payment attempt / money movement
  shipments         one carrier shipment per order (multiple allowed later)
  order_addresses   frozen SHIPPING / BILLING address snapshot per order

Backfill
--------
  orders.order_number  <- CONCAT('WV-', YEAR(created_at), '-', LPAD(id,6,'0'))
  order_payments       <- one online/prepaid leg per order where
                          (total_amount - cod_balance) > 0, plus one COD leg
                          per order where cod_balance > 0 (honours the
                          "one row per money movement" model).
  shipments            <- one row per order that already has carrier/AWB/
                          tracking data.
  order_addresses      <- SHIPPING from shipping_address_snapshot (or legacy
                          free-text), BILLING from billing_address_snapshot
                          when present (NULL billing == same as shipping).

Revision ID: p7q8r9s0t1u2
Revises: o6p7q8r9s0t1
Create Date: 2026-06-21 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "p7q8r9s0t1u2"
down_revision: Union[str, None] = "o6p7q8r9s0t1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PAYMENT_STATUSES = (
    "PENDING",
    "INITIATED",
    "AUTHORIZED",
    "PAID",
    "FAILED",
    "REFUNDED",
    "PARTIALLY_REFUNDED",
    "CANCELLED",
)
_SHIPMENT_STATUSES = (
    "PENDING",
    "READY_TO_SHIP",
    "PICKUP_SCHEDULED",
    "SHIPPED",
    "IN_TRANSIT",
    "OUT_FOR_DELIVERY",
    "DELIVERED",
    "DELIVERY_FAILED",
    "RTO_INITIATED",
    "RTO_DELIVERED",
    "CANCELLED",
)


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. orders.order_number — nullable + UNIQUE.                          #
    #    Nullable so the INSERT that allocates the id doesn't trip NOT     #
    #    NULL (the value embeds the id and is set right after flush), and  #
    #    so legacy rows tolerate NULL.  Callers fall back to f"ORD{id}".   #
    # ------------------------------------------------------------------ #
    op.add_column("orders", sa.Column("order_number", sa.String(length=32), nullable=True))
    op.create_index("ix_orders_order_number", "orders", ["order_number"], unique=True)

    # ------------------------------------------------------------------ #
    # 2. order_payments                                                   #
    # ------------------------------------------------------------------ #
    op.create_table(
        "order_payments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("gateway", sa.String(length=40), nullable=True),
        sa.Column("gateway_order_id", sa.String(length=128), nullable=True),
        sa.Column("gateway_payment_id", sa.String(length=128), nullable=True),
        sa.Column("gateway_signature", sa.String(length=255), nullable=True),
        sa.Column("payment_method", sa.String(length=32), nullable=True),
        sa.Column(
            "payment_status",
            sa.Enum(*_PAYMENT_STATUSES, name="paymenttxnstatus"),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False, server_default=sa.text("0")),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default=sa.text("'INR'")),
        sa.Column("transaction_reference", sa.String(length=128), nullable=True),
        sa.Column("webhook_event_id", sa.String(length=128), nullable=True),
        sa.Column("raw_gateway_response", sa.JSON(), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name="fk_order_payments_order_id", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("webhook_event_id", name="uq_order_payments_webhook_event_id"),
        sa.CheckConstraint("amount >= 0", name="ck_order_payments_amount_nonneg"),
    )
    op.create_index("ix_order_payments_order_id", "order_payments", ["order_id"])
    op.create_index("ix_order_payments_gateway_order_id", "order_payments", ["gateway_order_id"])
    op.create_index("ix_order_payments_gateway_payment_id", "order_payments", ["gateway_payment_id"])
    op.create_index("ix_order_payments_payment_status", "order_payments", ["payment_status"])

    # ------------------------------------------------------------------ #
    # 3. shipments                                                        #
    # ------------------------------------------------------------------ #
    op.create_table(
        "shipments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("courier_partner", sa.String(length=64), nullable=True),
        sa.Column("courier_service", sa.String(length=64), nullable=True),
        sa.Column("awb_number", sa.String(length=64), nullable=True),
        sa.Column("tracking_number", sa.String(length=120), nullable=True),
        sa.Column("tracking_url", sa.String(length=512), nullable=True),
        sa.Column(
            "shipment_status",
            sa.Enum(*_SHIPMENT_STATUSES, name="shipmentstatus"),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column("shipment_cost", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("package_weight_grams", sa.Integer(), nullable=True),
        sa.Column("package_length_cm", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("package_width_cm", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("package_height_cm", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("pickup_scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("in_transit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("out_for_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("returned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_courier_response", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name="fk_shipments_order_id", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_shipments_order_id", "shipments", ["order_id"])
    op.create_index("ix_shipments_awb_number", "shipments", ["awb_number"])
    op.create_index("ix_shipments_tracking_number", "shipments", ["tracking_number"])
    op.create_index("ix_shipments_shipment_status", "shipments", ["shipment_status"])

    # ------------------------------------------------------------------ #
    # 4. order_addresses                                                  #
    # ------------------------------------------------------------------ #
    op.create_table(
        "order_addresses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column(
            "address_type",
            sa.Enum("SHIPPING", "BILLING", name="orderaddresstype"),
            nullable=False,
        ),
        sa.Column("full_name", sa.String(length=120), nullable=True),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("address_line1", sa.String(length=255), nullable=True),
        sa.Column("address_line2", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("state", sa.String(length=120), nullable=True),
        sa.Column("country", sa.String(length=64), nullable=True),
        sa.Column("pincode", sa.String(length=20), nullable=True),
        sa.Column("landmark", sa.String(length=120), nullable=True),
        sa.Column("gst_number", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name="fk_order_addresses_order_id", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_order_addresses_order_id", "order_addresses", ["order_id"])

    # ================================================================== #
    # BACKFILL                                                            #
    # ================================================================== #

    # -- order_number ---------------------------------------------------
    op.execute(
        "UPDATE orders "
        "SET order_number = CONCAT('WV-', YEAR(created_at), '-', LPAD(id, 6, '0')) "
        "WHERE order_number IS NULL"
    )

    # -- order_payments: online / prepaid leg ---------------------------
    # amount = total_amount - cod_balance (the gateway-paid portion).
    # Skipped for full-COD orders where that is 0.
    op.execute(
        """
        INSERT INTO order_payments
            (order_id, gateway, gateway_order_id, gateway_payment_id,
             payment_method, payment_status, amount, currency,
             transaction_reference, paid_at, created_at, updated_at)
        SELECT
            o.id,
            o.gateway_code,
            o.payment_provider_ref,
            NULL,
            o.payment_method,
            CASE
                WHEN o.status IN ('PAID', 'SHIPPED', 'DELIVERED') THEN 'PAID'
                WHEN o.status = 'REFUNDED' THEN 'REFUNDED'
                WHEN o.status = 'CANCELLED' THEN 'CANCELLED'
                ELSE 'PENDING'
            END,
            (o.total_amount - o.cod_balance),
            o.currency,
            o.payment_intent_id,
            CASE WHEN o.status IN ('PAID', 'SHIPPED', 'DELIVERED') THEN o.paid_at ELSE NULL END,
            o.created_at,
            o.created_at
        FROM orders o
        WHERE (o.total_amount - o.cod_balance) > 0
        """
    )

    # -- order_payments: COD leg ---------------------------------------
    # amount = cod_balance (collected by the courier on delivery). PAID only
    # once the order is delivered; otherwise still PENDING collection.
    op.execute(
        """
        INSERT INTO order_payments
            (order_id, gateway, payment_method, payment_status, amount, currency,
             transaction_reference, paid_at, created_at, updated_at)
        SELECT
            o.id,
            NULL,
            'cod',
            CASE
                WHEN o.status = 'DELIVERED' THEN 'PAID'
                WHEN o.status = 'REFUNDED' THEN 'REFUNDED'
                WHEN o.status = 'CANCELLED' THEN 'CANCELLED'
                ELSE 'PENDING'
            END,
            o.cod_balance,
            o.currency,
            o.payment_intent_id,
            CASE WHEN o.status = 'DELIVERED' THEN o.delivered_at ELSE NULL END,
            o.created_at,
            o.created_at
        FROM orders o
        WHERE o.cod_balance > 0
        """
    )

    # -- shipments ------------------------------------------------------
    op.execute(
        """
        INSERT INTO shipments
            (order_id, courier_partner, awb_number, tracking_number,
             shipment_status, pickup_scheduled_at, shipped_at, delivered_at,
             created_at, updated_at)
        SELECT
            o.id,
            COALESCE(o.shipping_provider, o.carrier),
            o.shipping_awb,
            COALESCE(o.tracking_number, o.shipping_awb),
            CASE
                WHEN o.status = 'DELIVERED' THEN 'DELIVERED'
                WHEN o.status = 'SHIPPED' THEN 'SHIPPED'
                WHEN o.shipping_awb IS NOT NULL THEN 'READY_TO_SHIP'
                ELSE 'PENDING'
            END,
            o.pickup_scheduled_for,
            o.shipped_at,
            o.delivered_at,
            COALESCE(o.shipment_created_at, o.created_at),
            COALESCE(o.last_tracking_at, o.shipment_created_at, o.created_at)
        FROM orders o
        WHERE o.shipping_awb IS NOT NULL
           OR o.tracking_number IS NOT NULL
           OR o.shipment_created_at IS NOT NULL
           OR o.shipping_provider IS NOT NULL
        """
    )

    # -- order_addresses: SHIPPING from JSON snapshot -------------------
    op.execute(
        """
        INSERT INTO order_addresses
            (order_id, address_type, full_name, phone, address_line1,
             address_line2, city, state, country, pincode, landmark, created_at)
        SELECT
            o.id, 'SHIPPING',
            o.shipping_address_snapshot->>'$.full_name',
            o.shipping_address_snapshot->>'$.phone',
            o.shipping_address_snapshot->>'$.line1',
            o.shipping_address_snapshot->>'$.line2',
            o.shipping_address_snapshot->>'$.city',
            o.shipping_address_snapshot->>'$.state',
            o.shipping_address_snapshot->>'$.country',
            COALESCE(o.shipping_address_snapshot->>'$.pincode', o.shipping_pincode),
            o.shipping_address_snapshot->>'$.landmark',
            o.created_at
        FROM orders o
        WHERE o.shipping_address_snapshot IS NOT NULL
        """
    )

    # -- order_addresses: SHIPPING legacy free-text (no snapshot) -------
    op.execute(
        """
        INSERT INTO order_addresses
            (order_id, address_type, address_line1, pincode, created_at)
        SELECT o.id, 'SHIPPING', o.shipping_address, o.shipping_pincode, o.created_at
        FROM orders o
        WHERE o.shipping_address_snapshot IS NULL
          AND o.shipping_address IS NOT NULL
        """
    )

    # -- order_addresses: BILLING from JSON snapshot --------------------
    # NULL billing snapshot means "same as shipping" — no redundant row.
    op.execute(
        """
        INSERT INTO order_addresses
            (order_id, address_type, full_name, phone, address_line1,
             address_line2, city, state, country, pincode, landmark, created_at)
        SELECT
            o.id, 'BILLING',
            o.billing_address_snapshot->>'$.full_name',
            o.billing_address_snapshot->>'$.phone',
            o.billing_address_snapshot->>'$.line1',
            o.billing_address_snapshot->>'$.line2',
            o.billing_address_snapshot->>'$.city',
            o.billing_address_snapshot->>'$.state',
            o.billing_address_snapshot->>'$.country',
            o.billing_address_snapshot->>'$.pincode',
            o.billing_address_snapshot->>'$.landmark',
            o.created_at
        FROM orders o
        WHERE o.billing_address_snapshot IS NOT NULL
        """
    )


def downgrade() -> None:
    # Drop child tables first (FKs reference orders). Indexes drop with the
    # tables in MySQL; named indexes on the kept `orders` column are dropped
    # explicitly before the column.
    op.drop_table("order_addresses")
    op.drop_table("shipments")
    op.drop_table("order_payments")

    op.drop_index("ix_orders_order_number", table_name="orders")
    op.drop_column("orders", "order_number")
