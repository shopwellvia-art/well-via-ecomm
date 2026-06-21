"""Customer-initiated return requests + their line items.

Named `return_request.py` rather than `return.py` because `return` is a Python
keyword and importing `from app.models.return import ...` would never compile.

State machine:

    requested  ──approve──▶ approved  ──pickup──▶ picked_up
       │                       │                       │
       │                       │                       ▼
       │                       │                    received
       │                       │                       │
       │                       │              inspect ─┤
       │                       │             (passed)  ▼
       │                       │                    refunded   (terminal)
       │                       │                       ▲
       │                       │              refund through original method
       ├──reject────────▶ rejected   (terminal) ◀── inspect (failed)
       │
       └──cancel────────▶ cancelled  (terminal — customer-initiated only)

Inspection: once `received`, the item is inspected against the customer's
claim/return policy. A *passing* inspection unlocks the refund (which is then
issued through the original payment method); a *failing* inspection rejects the
return. The verdict + notes are recorded on the row (`inspection_passed`,
`inspection_notes`, `inspected_at`) for audit.

Each non-terminal transition stamps a *_at column so we have an audit trail
without a separate state-log table.
"""
from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IDMixin, TimestampMixin


class ReturnStatus(str, enum.Enum):
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    PICKED_UP = "picked_up"
    RECEIVED = "received"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"


# Controlled reasons — keeps reporting clean and the dropdown short. Anything
# more nuanced goes into customer_notes.
class ReturnReason(str, enum.Enum):
    DEFECTIVE = "defective"
    WRONG_ITEM = "wrong_item"
    NOT_AS_DESCRIBED = "not_as_described"
    ARRIVED_DAMAGED = "arrived_damaged"
    NO_LONGER_NEEDED = "no_longer_needed"
    OTHER = "other"


class ReturnRequest(Base, IDMixin, TimestampMixin):
    __tablename__ = "returns"

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    status: Mapped[ReturnStatus] = mapped_column(
        Enum(ReturnStatus),
        default=ReturnStatus.REQUESTED,
        nullable=False, index=True,
    )
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    customer_notes: Mapped[str | None] = mapped_column(Text)
    admin_notes: Mapped[str | None] = mapped_column(Text)

    # Carrier-side return shipment. `reverse_awb` is the RTO waybill we get
    # back from create_reverse_shipment; `reverse_pickup_id` is the pickup
    # booking reference (if scheduled).
    reverse_awb: Mapped[str | None] = mapped_column(String(64), index=True)
    reverse_pickup_id: Mapped[str | None] = mapped_column(String(64))

    # The amount we'll refund. Defaults to "all returned-item subtotals"
    # at approve time; admin can override.
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    # ---- Inspection (on receipt) -------------------------------------------
    # The verdict from inspecting the physically-received item against the
    # customer's stated claim + return policy. NULL until inspected; True =
    # matches claim / passes policy (unlocks the refund); False = rejected.
    inspection_passed: Mapped[bool | None] = mapped_column()
    inspection_notes: Mapped[str | None] = mapped_column(Text)
    inspected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ---- Refund execution --------------------------------------------------
    # How the refund was routed back to the customer and its provider/internal
    # reference, stamped when the refund is issued. `refund_method` is the
    # original gateway code (e.g. "phonepe") for prepaid orders, or "manual"
    # when there is no electronic source to reverse (e.g. COD). Both NULL until
    # the refund is issued.
    refund_method: Mapped[str | None] = mapped_column(String(40))
    refund_reference: Mapped[str | None] = mapped_column(String(128))

    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    picked_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list["ReturnItem"]] = relationship(
        back_populates="return_request",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    order: Mapped["Order"] = relationship(lazy="joined")  # noqa: F821
    user: Mapped["User"] = relationship(lazy="joined")  # noqa: F821


class ReturnItem(Base, IDMixin, TimestampMixin):
    """A slice of one OrderItem being returned. Multiple ReturnItems map to
    one ReturnRequest; one OrderItem can appear across multiple returns
    over time (partial returns) — the service layer enforces that the sum
    of returned quantities never exceeds the original order quantity."""

    __tablename__ = "return_items"

    return_id: Mapped[int] = mapped_column(
        ForeignKey("returns.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    order_item_id: Mapped[int] = mapped_column(
        ForeignKey("order_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    return_request: Mapped[ReturnRequest] = relationship(back_populates="items")
    order_item: Mapped["OrderItem"] = relationship(lazy="joined")  # noqa: F821
