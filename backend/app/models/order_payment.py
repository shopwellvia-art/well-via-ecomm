"""Normalized payment-attempt records for orders.

Part of the order-table normalization (2026-06-21): payment data that used to
live as flat columns on `orders` (gateway ids, provider refs, paid/failed
timestamps) now lives here as discrete *payment-attempt* rows.  One order can
have many rows — multiple gateway retries, or the prepaid + COD legs of a
split-COD order (one row per real money movement).

Relationship to `payment_events`
--------------------------------
`payment_events` is an append-only *forensic audit log* (every webhook, poll,
mismatch).  This table is the *canonical transactional record* — the current
state of each payment attempt.  They are complementary; neither replaces the
other.  The old flat columns on `orders` (`payment_intent_id`, `gateway_code`,
`payment_provider_ref`, `paid_at`, ...) are kept and still written for
backward compatibility — see the DEPRECATED comments on the Order model.
"""
from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IDMixin, TimestampMixin


class PaymentTxnStatus(str, enum.Enum):
    """Lifecycle of a single payment attempt.

    Stored in the DB as the member NAME (e.g. "PAID"), matching the project's
    existing enum convention (see OrderStatus); serialized over the API as the
    lowercase `.value` (e.g. "paid").
    """

    PENDING = "pending"          # created, nothing sent to gateway yet
    INITIATED = "initiated"      # handed off to the gateway / hosted page
    AUTHORIZED = "authorized"    # funds held, not yet captured
    PAID = "paid"                # captured / settled (or COD collected)
    FAILED = "failed"            # gateway declined / errored
    REFUNDED = "refunded"        # fully reversed
    PARTIALLY_REFUNDED = "partially_refunded"
    CANCELLED = "cancelled"      # abandoned / voided before capture


class OrderPayment(Base, IDMixin, TimestampMixin):
    """One payment attempt against an order."""

    __tablename__ = "order_payments"

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE", name="fk_order_payments_order_id"),
        nullable=False,
        index=True,
    )

    # Which gateway processed this attempt (matches payment_methods.gateway_code).
    # Null for COD legs that never touch a gateway.
    gateway: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Provider-side identifiers. gateway_order_id is the order/session/intent
    # reference returned at initiation; gateway_payment_id is the captured
    # payment/charge id; gateway_signature is the HMAC the provider returned
    # (verified, never a secret of ours). All indexed where we look them up.
    gateway_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    gateway_payment_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    gateway_signature: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # 'prepaid' | 'cod' | 'split_cod' | instrument ('upi'|'card'|...). Free-text
    # to stay in step with orders.payment_method / payment_instrument.
    payment_method: Mapped[str | None] = mapped_column(String(32), nullable=True)

    payment_status: Mapped[PaymentTxnStatus] = mapped_column(
        Enum(PaymentTxnStatus, name="paymenttxnstatus"),
        default=PaymentTxnStatus.PENDING,
        nullable=False,
        index=True,
    )

    # Money for THIS attempt (a split-COD prepaid leg is < order total). The
    # CHECK keeps it non-negative at the DB layer.
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")

    # Our internal correlation key (e.g. the merchant transaction id / mtid).
    transaction_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Idempotency key for inbound webhooks. Unique WHEN PRESENT — MySQL allows
    # many NULLs under a UNIQUE index, so duplicate webhook deliveries that
    # carry the same event id collapse to one row.
    webhook_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)

    # Full provider payload for forensics. Never store our own secrets here.
    raw_gateway_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    order: Mapped["Order"] = relationship(back_populates="payments")  # noqa: F821

    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_order_payments_amount_nonneg"),
    )
