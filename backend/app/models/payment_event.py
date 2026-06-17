"""Append-only audit log for the payment subsystem.

Every significant event in the payment lifecycle — webhook receipt, signature
validation, status polls, amount-mismatch detections, gateway errors,
reconciliation runs, and refund attempts — writes one row here.  The table is
never UPDATEd or DELETEd; rows accumulate indefinitely.

Design notes
------------
* `id` is BigInteger so the table can grow to billions of rows without
  wrapping.
* `order_id` is nullable and uses ON DELETE SET NULL so audit rows survive
  order deletion.  Some events (e.g. a webhook whose mtid doesn't match any
  order) arrive with no order at all.
* `event_type` is a plain varchar, NOT a DB enum, so adding new event types
  never requires a schema migration.  Use the ``PaymentEventType`` constants
  below instead of bare strings.
* `raw_payload` stores the webhook body or status-response dict for
  forensics — NEVER store authentication headers, Fernet secrets, or payment
  card data here.
* `message` is a short human note or gateway error string — NEVER store
  signatures or sensitive values.
* `amount_*_minor` fields use integer minor units (e.g. paise for INR) to
  avoid floating-point issues and match what gateways send.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import Base


# ---------------------------------------------------------------------------
# Event-type constants
# Use these instead of bare strings everywhere in the service layer so that
# typos fail at import time rather than silently writing unknown event types.
# ---------------------------------------------------------------------------
class PaymentEventType:
    WEBHOOK_RECEIVED = "webhook_received"
    WEBHOOK_SIGNATURE_INVALID = "webhook_signature_invalid"
    STATUS_POLL = "status_poll"
    STATUS_APPLIED = "status_applied"
    AMOUNT_MISMATCH = "amount_mismatch"
    GATEWAY_ERROR = "gateway_error"
    RECONCILE = "reconcile"
    REFUND_ATTEMPT = "refund_attempt"


class PaymentEvent(Base):
    """Single audit-log entry for the payment subsystem."""

    __tablename__ = "payment_events"

    # BigInteger PK — this table can grow large; avoid INT wrap-around.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Provenance: which order this event belongs to.
    # Nullable: some events (unmatched webhook, pre-order initiation) have no
    # matched order yet.  SET NULL means the audit row survives order deletion.
    order_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("orders.id", ondelete="SET NULL", name="fk_payment_events_order_id"),
        nullable=True,
        index=True,
    )

    # The merchant-transaction-id extracted from the gateway payload.  Present
    # even when no order row was found (useful for correlation across logs).
    merchant_transaction_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )

    # Which gateway produced this event.  Matches payment_methods.gateway_code.
    gateway_code: Mapped[str | None] = mapped_column(
        String(40), nullable=True, index=True
    )

    # The kind of event — use PaymentEventType constants.  NOT NULL because
    # every row must be classifiable; use GATEWAY_ERROR as a catch-all.
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)

    # Resolved payment status at the time of the event, when applicable.
    # Mirrors the PaymentStatus enum values: "pending" / "success" / "failed".
    # Null for purely informational events (e.g. webhook_received before
    # the payload has been interpreted).
    payment_status: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # For webhook events: did the HMAC / signature verification pass?
    # Null for non-webhook events (polls, internal reconcile, etc.).
    signature_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Gateway-reported amount in minor units (e.g. paise).  Captured verbatim
    # from the payload before any conversion.
    amount_reported_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Our expected amount in minor units, populated when a mismatch is detected
    # so the mismatch is self-documented in the event row.
    amount_expected_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Provider-side transaction / session / order reference (e.g. Razorpay
    # payment_id, Stripe charge id).
    provider_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Short human note or gateway error string.
    # NEVER store signatures, tokens, or other secrets here.
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # The full webhook body or status-response dict, stored for forensics.
    # NEVER store authentication headers, Fernet keys, or card data here.
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Timestamp of the event.  Indexed so recent-events queries are fast.
    # Server default means the DB fills it in even if the ORM layer omits it,
    # making it safe to use in raw-SQL inserts during high-volume ingestion.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default="now()",
        nullable=False,
        index=True,
    )

    # Composite index: (event_type, created_at) covers the common access
    # pattern "show me all AMOUNT_MISMATCH events in the last 7 days".
    __table_args__ = (
        Index(
            "ix_payment_events_event_type_created_at",
            "event_type",
            "created_at",
        ),
    )
