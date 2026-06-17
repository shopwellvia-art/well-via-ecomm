"""add payment_events audit-log table

Creates the ``payment_events`` append-only audit log for the payment
subsystem.  Every significant payment lifecycle event — webhook receipt,
signature validation, status polls, amount-mismatch detections, gateway
errors, reconciliation runs, and refund attempts — writes one row here.

Table design
------------
* ``id`` is BIGINT so the table survives billions of rows without wrapping.
* ``order_id`` is nullable FK -> orders.id ON DELETE SET NULL so the audit
  row survives order deletion and can also record pre-order events.
* ``event_type`` is VARCHAR (not a DB enum) so adding new event types never
  requires a schema migration.
* ``raw_payload`` / ``message`` are audit-only fields; no secrets or auth
  headers should ever be stored there.
* Indexes:
    - ``order_id``                         (FK, queried by order)
    - ``merchant_transaction_id``          (correlate webhook mtid to order)
    - ``gateway_code``                     (filter by gateway)
    - ``event_type``                       (filter by event kind)
    - ``created_at``                       (time-range scans)
    - composite (event_type, created_at)   (common pattern: recent events of type X)

Downgrade: drops FK constraint, then all indexes, then the table (MySQL-safe
order — FK constraint before index before table).

Revision ID: k2g3h4i5j6k7
Revises: j1f2a3b4c5d6
Create Date: 2026-06-17 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "k2g3h4i5j6k7"
down_revision: Union[str, None] = "j1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payment_events",
        # --- Primary key ---------------------------------------------------
        sa.Column(
            "id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        # --- Foreign key to orders -----------------------------------------
        # Nullable: some events arrive before an order exists, or after the
        # order has been deleted.  SET NULL preserves the audit row.
        sa.Column("order_id", sa.Integer(), nullable=True),
        # --- Gateway correlation --------------------------------------------
        sa.Column("merchant_transaction_id", sa.String(length=64), nullable=True),
        sa.Column("gateway_code", sa.String(length=40), nullable=True),
        # --- Event classification ------------------------------------------
        sa.Column("event_type", sa.String(length=40), nullable=False),
        # --- Payment state snapshot ----------------------------------------
        sa.Column("payment_status", sa.String(length=16), nullable=True),
        sa.Column("signature_valid", sa.Boolean(), nullable=True),
        # --- Amount fields (minor units, e.g. paise for INR) ---------------
        sa.Column("amount_reported_minor", sa.Integer(), nullable=True),
        sa.Column("amount_expected_minor", sa.Integer(), nullable=True),
        # --- Provider reference --------------------------------------------
        sa.Column("provider_ref", sa.String(length=128), nullable=True),
        # --- Human note / error text ---------------------------------------
        # NEVER store signatures or secrets here.
        sa.Column("message", sa.String(length=500), nullable=True),
        # --- Raw webhook body or status-response dict ----------------------
        # NEVER store auth headers or sensitive keys here.
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        # --- Timestamp ------------------------------------------------------
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # --- Constraints ----------------------------------------------------
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_payment_events_order_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- Single-column indexes ---------------------------------------------
    # order_id — FK, queried when viewing payment history for an order.
    op.create_index(
        op.f("ix_payment_events_order_id"),
        "payment_events",
        ["order_id"],
        unique=False,
    )
    # merchant_transaction_id — correlate a webhook mtid back to an order.
    op.create_index(
        op.f("ix_payment_events_merchant_transaction_id"),
        "payment_events",
        ["merchant_transaction_id"],
        unique=False,
    )
    # gateway_code — filter all events for one gateway.
    op.create_index(
        op.f("ix_payment_events_gateway_code"),
        "payment_events",
        ["gateway_code"],
        unique=False,
    )
    # event_type — filter by event kind (also covered by composite below, but
    # a single-column index is cheaper for equality-only predicates).
    op.create_index(
        op.f("ix_payment_events_event_type"),
        "payment_events",
        ["event_type"],
        unique=False,
    )
    # created_at — time-range scans ("last N events").
    op.create_index(
        op.f("ix_payment_events_created_at"),
        "payment_events",
        ["created_at"],
        unique=False,
    )

    # --- Composite index ---------------------------------------------------
    # (event_type, created_at) — covers the common pattern:
    # "show me all AMOUNT_MISMATCH events in the last 7 days".
    op.create_index(
        "ix_payment_events_event_type_created_at",
        "payment_events",
        ["event_type", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    # MySQL requires dropping FK constraints before indexes, and indexes
    # before the table.  Follow that order strictly.

    # 1. Drop FK constraint.
    op.drop_constraint(
        "fk_payment_events_order_id", "payment_events", type_="foreignkey"
    )

    # 2. Drop indexes (composite first, then singles).
    op.drop_index(
        "ix_payment_events_event_type_created_at",
        table_name="payment_events",
    )
    op.drop_index(
        op.f("ix_payment_events_created_at"),
        table_name="payment_events",
    )
    op.drop_index(
        op.f("ix_payment_events_event_type"),
        table_name="payment_events",
    )
    op.drop_index(
        op.f("ix_payment_events_gateway_code"),
        table_name="payment_events",
    )
    op.drop_index(
        op.f("ix_payment_events_merchant_transaction_id"),
        table_name="payment_events",
    )
    op.drop_index(
        op.f("ix_payment_events_order_id"),
        table_name="payment_events",
    )

    # 3. Drop the table.
    op.drop_table("payment_events")
