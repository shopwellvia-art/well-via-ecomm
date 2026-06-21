"""Normalized address snapshots for orders.

Part of the order-table normalization (2026-06-21).  Each row is a FROZEN copy
of an address as it was at order time — one SHIPPING row and (when applicable)
one BILLING row per order.  Rows are immutable: never UPDATE an existing
snapshot, because a customer editing or deleting their saved address must not
rewrite order history.  Hence there is no `updated_at` — only `created_at`.

This complements (does not replace) `orders.shipping_address_snapshot` /
`billing_address_snapshot` (JSON), which are kept and still written for
backward compatibility.  This table gives the same data normalized/columnar so
it can be queried and reported on directly.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IDMixin


class OrderAddressType(str, enum.Enum):
    """Which leg of the order this snapshot is for.

    Stored as the member NAME in the DB; serialized as the lowercase `.value`.
    """

    SHIPPING = "shipping"
    BILLING = "billing"


class OrderAddress(Base, IDMixin):
    """Immutable address snapshot captured at order time."""

    __tablename__ = "order_addresses"

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE", name="fk_order_addresses_order_id"),
        nullable=False,
        index=True,
    )

    address_type: Mapped[OrderAddressType] = mapped_column(
        Enum(OrderAddressType, name="orderaddresstype"),
        nullable=False,
    )

    # All nullable: legacy / free-text orders may only carry a partial address.
    full_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(120), nullable=True)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pincode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    landmark: Mapped[str | None] = mapped_column(String(120), nullable=True)
    gst_number: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # No updated_at — snapshots are write-once.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    order: Mapped["Order"] = relationship(back_populates="addresses")  # noqa: F821
