"""Normalized shipment records for orders.

Part of the order-table normalization (2026-06-21): courier/AWB/tracking data
that used to live as flat columns on `orders` (`shipping_provider`,
`shipping_awb`, `tracking_number`, `tracking_events`, the per-leg timestamps)
now lives here as discrete shipment rows.  Initially one shipment per order,
but the schema allows several (split parcels) without further migration.

The legacy flat columns on `orders` are kept and still written for backward
compatibility — the storefront/admin already read them — see the DEPRECATED
comments on the Order model.  Customer-initiated returns/RTO continue to live
in the `returns` / `return_items` tables; the RTO_* statuses here only describe
the carrier leg of *this* shipment.
"""
from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IDMixin, TimestampMixin


class ShipmentStatus(str, enum.Enum):
    """Lifecycle of a carrier shipment.

    Stored as the member NAME in the DB; serialized as the lowercase `.value`.
    """

    PENDING = "pending"
    READY_TO_SHIP = "ready_to_ship"
    PICKUP_SCHEDULED = "pickup_scheduled"
    SHIPPED = "shipped"
    IN_TRANSIT = "in_transit"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    DELIVERY_FAILED = "delivery_failed"
    RTO_INITIATED = "rto_initiated"
    RTO_DELIVERED = "rto_delivered"
    CANCELLED = "cancelled"


class Shipment(Base, IDMixin, TimestampMixin):
    """One carrier shipment for an order."""

    __tablename__ = "shipments"

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE", name="fk_shipments_order_id"),
        nullable=False,
        index=True,
    )

    # Carrier identity. courier_partner = provider code/name (e.g. "delhivery");
    # courier_service = the speed/tier within that carrier ("Surface"/"Express").
    courier_partner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    courier_service: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # awb_number is the carrier waybill; tracking_number is what we surface to
    # the customer (usually mirrors the AWB). Both indexed for inbound webhook
    # / customer-paste lookups.
    awb_number: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    tracking_number: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    tracking_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    shipment_status: Mapped[ShipmentStatus] = mapped_column(
        Enum(ShipmentStatus, name="shipmentstatus"),
        default=ShipmentStatus.PENDING,
        nullable=False,
        index=True,
    )

    shipment_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    # Package dimensions/weight. Weight in grams (integer); dims in cm (allow
    # one decimal place for carriers that quote fractional cm).
    package_weight_grams: Mapped[int | None] = mapped_column(Integer, nullable=True)
    package_length_cm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    package_width_cm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    package_height_cm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)

    # Per-leg timestamps mirroring the ShipmentStatus progression.
    pickup_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    in_transit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    out_for_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Full carrier payload (create-shipment / tracking webhook) for forensics.
    raw_courier_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    order: Mapped["Order"] = relationship(back_populates="shipments")  # noqa: F821
