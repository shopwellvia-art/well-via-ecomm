import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Numeric, String, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IDMixin, TimestampMixin


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class Order(Base, IDMixin, TimestampMixin):
    __tablename__ = "orders"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus), default=OrderStatus.PENDING, nullable=False, index=True
    )
    # subtotal       = sum(unit_price * qty) before tax/discount/shipping
    # tax_amount     = sum of taxes applied at order time (snapshotted from product.taxes)
    # discount_amount = coupon-derived discount (snapshotted)
    # shipping_amount = carrier-quoted dispatch cost, snapshotted at checkout
    # total_amount   = subtotal + tax_amount + shipping_amount - discount_amount
    #
    # Discount does NOT apply to shipping — the same rule every Indian
    # marketplace runs. Tax on shipping is also not applied (carrier issues
    # its own tax invoice).
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    shipping_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    coupon_code: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)

    # Payment method. 'prepaid' goes through the gateway; 'cod' bypasses it
    # and the carrier collects on delivery; 'split_cod' (Phase 9) collects a
    # prepaid slice via the gateway + the balance on delivery.
    payment_method: Mapped[str] = mapped_column(
        String(32), default="prepaid", nullable=False, index=True
    )
    # Extra fee added when COD is selected. Snapshotted from `cod.flat_surcharge`
    # at checkout time; admin changes to the setting don't retro-apply to
    # existing orders.
    cod_surcharge_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0
    )
    # Amount the carrier collects on delivery. Zero for prepaid orders, equals
    # `total_amount` for full COD, equals (total - prepaid) for split COD.
    cod_balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0
    )

    # Instrument the customer picked at checkout ('upi'|'netbanking'|'card'|
    # 'wallet'|null). Captured for reporting + so the gateway can pre-select
    # the instrument on its hosted page. Null for COD orders.
    payment_instrument: Mapped[str | None] = mapped_column(String(32))
    # Instrument-specific discount applied at checkout (separate from coupon
    # discount so reporting can split "method incentive" vs "promo").
    payment_discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0
    )

    shipping_address: Mapped[str | None] = mapped_column(String(512))
    # Destination pin extracted from `shipping_address`. Stored separately so
    # the shipment/tracking phases don't re-parse the free-text address.
    shipping_pincode: Mapped[str | None] = mapped_column(String(20))
    # `shipping_address_snapshot` is the frozen, structured copy of the address
    # at checkout time — it is the source of truth for fulfillment display.
    # `shipping_address_id` is provenance-only (SET NULL on delete); never read
    # for fulfillment so that editing or deleting the saved address never
    # rewrites historical order data.
    shipping_address_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("addresses.id", ondelete="SET NULL", name="fk_orders_shipping_address_id"),
        nullable=True,
        index=True,
    )
    shipping_address_snapshot: Mapped[dict | None] = mapped_column(JSON)
    payment_intent_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    # Which payment gateway routed this order (matches payment_methods.gateway_code).
    # Null for legacy orders and COD orders that bypass the gateway entirely.
    gateway_code: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    # Provider-side transaction / session / order identifier returned at checkout
    # initiation — e.g. a Stripe checkout session id, Razorpay payment-link id,
    # or PayPal order id.  Used by the status-polling / webhook handlers to
    # correlate provider callbacks back to this order row.
    payment_provider_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Fulfillment metadata. Admin writes these when transitioning the status.
    # `tracking_number` + `carrier` are presentational — the storefront can
    # show "Shipped via UPS, tracking 1Z..." on the order page.
    tracking_number: Mapped[str | None] = mapped_column(String(120))
    carrier: Mapped[str | None] = mapped_column(String(60))

    # Carrier-side shipment identity. Populated by ShippingService once the
    # order is pushed to the active provider. `shipping_awb` is the carrier's
    # waybill (Delhivery returns one per shipment); `shipping_label_url` is
    # the carrier-hosted PDF when they expose it; `shipping_provider` records
    # WHICH carrier this AWB lives in so tracking calls hit the right backend
    # even if the admin switches providers later.
    shipping_provider: Mapped[str | None] = mapped_column(String(32))
    shipping_awb: Mapped[str | None] = mapped_column(String(64), index=True)
    shipping_label_url: Mapped[str | None] = mapped_column(String(512))
    shipment_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Pickup scheduling. `pickup_id` is the carrier's pickup reference; the
    # presence of either column means "we've asked the carrier for a pickup".
    pickup_id: Mapped[str | None] = mapped_column(String(64))
    pickup_scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Tracking event log. Append-only list of
    #   {status, occurred_at, location, note}
    # Mirrors what the carrier has told us about the shipment's progress.
    # We dedup on (status, occurred_at) before appending so re-played
    # webhooks don't bloat the row.
    tracking_events: Mapped[list[dict] | None] = mapped_column(JSON)
    last_tracking_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Status timestamps. The current `status` is the latest hop; these record
    # *when* each hop happened. Useful for an audit trail and reporting.
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Admin-only annotations. `refund_reason` is a one-liner explaining the
    # last cancel/refund. `internal_notes` is free-form scratchpad — never
    # shown to the customer.
    refund_reason: Mapped[str | None] = mapped_column(String(255))
    internal_notes: Mapped[str | None] = mapped_column(Text)

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    user: Mapped["User"] = relationship(lazy="joined")  # noqa: F821


class OrderItem(Base, IDMixin, TimestampMixin):
    __tablename__ = "order_items"

    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    quantity: Mapped[int] = mapped_column(nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # Snapshot of Product.cost at the moment of sale — frozen like unit_price so
    # historical profit stays accurate even if the product's cost changes later.
    # Nullable: orders placed before cost tracking existed have no snapshot.
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    order: Mapped[Order] = relationship(back_populates="items")
