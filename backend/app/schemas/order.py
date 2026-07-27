from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.order import OrderStatus
from app.models.order_address import OrderAddressType
from app.models.order_payment import PaymentTxnStatus
from app.models.shipment import ShipmentStatus


class OrderItemCreate(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)


class OrderItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    quantity: int
    unit_price: Decimal
    # Display fields resolved from the linked catalog product (the order line
    # itself doesn't snapshot them). Optional so edge rows still validate.
    name: str | None = None
    image_url: str | None = None


# ---- Normalized children (order-table normalization, 2026-06-21) ----
# These mirror the order_payments / shipments / order_addresses tables and are
# returned as nested arrays on the order detail responses. The flat
# payment/shipping/address fields on OrderRead/AdminOrderRead are kept for
# backward compatibility; new clients should prefer these.


class OrderPaymentRead(BaseModel):
    """One payment attempt / money movement against the order."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    gateway: str | None = None
    gateway_order_id: str | None = None
    gateway_payment_id: str | None = None
    payment_method: str | None = None
    payment_status: PaymentTxnStatus
    amount: Decimal
    currency: str
    transaction_reference: str | None = None
    paid_at: datetime | None = None
    failed_at: datetime | None = None
    created_at: datetime


class ShipmentRead(BaseModel):
    """One carrier shipment for the order."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    courier_partner: str | None = None
    courier_service: str | None = None
    awb_number: str | None = None
    tracking_number: str | None = None
    tracking_url: str | None = None
    shipment_status: ShipmentStatus
    shipment_cost: Decimal | None = None
    package_weight_grams: int | None = None
    package_length_cm: Decimal | None = None
    package_width_cm: Decimal | None = None
    package_height_cm: Decimal | None = None
    pickup_scheduled_at: datetime | None = None
    shipped_at: datetime | None = None
    in_transit_at: datetime | None = None
    out_for_delivery_at: datetime | None = None
    delivered_at: datetime | None = None
    failed_delivery_at: datetime | None = None
    returned_at: datetime | None = None
    created_at: datetime


class OrderAddressRead(BaseModel):
    """A frozen SHIPPING or BILLING address snapshot for the order."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    address_type: OrderAddressType
    full_name: str | None = None
    phone: str | None = None
    email: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    pincode: str | None = None
    landmark: str | None = None
    gst_number: str | None = None


class OrderCreate(BaseModel):
    items: list[OrderItemCreate] = Field(min_length=1)
    shipping_address: str | None = None


class OrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_number: str | None = None
    user_id: int
    status: OrderStatus
    subtotal: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    discount_amount: Decimal = Decimal("0")
    shipping_amount: Decimal = Decimal("0")
    total_amount: Decimal
    coupon_code: str | None = None
    currency: str
    shipping_address: str | None
    shipping_pincode: str | None = None
    # Frozen JSON snapshot of the shipping address at order time.
    # snapshot = truth; shipping_address_id is provenance only.
    shipping_address_snapshot: dict | None = None
    shipping_address_id: int | None = None
    # Billing address. None means billing was the same as shipping (legacy
    # or pre-billing-feature orders). billing_address_id is provenance FK;
    # billing_address_snapshot is the frozen truth.
    billing_address_snapshot: dict | None = None
    billing_address_id: int | None = None
    # Payment-method snapshot. 'cod_balance' is the amount the carrier will
    # collect on delivery (zero for prepaid orders).
    payment_method: str = "prepaid"
    payment_instrument: str | None = None
    payment_discount_amount: Decimal = Decimal("0")
    cod_surcharge_amount: Decimal = Decimal("0")
    cod_balance: Decimal = Decimal("0")
    # Customer-visible shipping data: carrier + AWB are useful even without
    # event detail (the customer can paste the AWB into the carrier's site).
    shipping_provider: str | None = None
    shipping_awb: str | None = None
    tracking_events: list[dict] | None = None
    last_tracking_at: datetime | None = None
    # Status timestamps — exposed so the storefront can render a dated
    # "placed → confirmed → shipped → delivered" progress timeline. Each is
    # null until the order reaches that hop.
    paid_at: datetime | None = None
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None
    cancelled_at: datetime | None = None
    refunded_at: datetime | None = None
    items: list[OrderItemRead]
    # Normalized children (preferred over the flat fields above).
    payments: list[OrderPaymentRead] = []
    shipments: list[ShipmentRead] = []
    addresses: list[OrderAddressRead] = []
    created_at: datetime


# ---- Admin schemas ----


class AdminCustomerBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str | None


class AdminOrderRow(BaseModel):
    """Slim list-row payload — what AdminOrdersPage renders per row."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: OrderStatus
    total_amount: Decimal
    currency: str
    customer_email: str
    item_count: int
    created_at: datetime


class AdminOrderListPage(BaseModel):
    items: list[AdminOrderRow]
    total: int
    page: int
    page_size: int
    counts_by_status: dict[str, int]


class AdminOrderRead(BaseModel):
    """Full order payload for the detail view. Carries fulfillment metadata
    and admin-only notes that we don't surface to the customer."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    order_number: str | None = None
    status: OrderStatus
    subtotal: Decimal
    tax_amount: Decimal
    discount_amount: Decimal
    shipping_amount: Decimal = Decimal("0")
    total_amount: Decimal
    coupon_code: str | None
    currency: str
    shipping_address: str | None
    shipping_pincode: str | None = None
    # Frozen JSON snapshot of the shipping address at order time.
    # snapshot = truth; shipping_address_id is provenance only.
    shipping_address_snapshot: dict | None = None
    shipping_address_id: int | None = None
    # Billing address. None means billing was the same as shipping (legacy
    # or pre-billing-feature orders). billing_address_id is provenance FK;
    # billing_address_snapshot is the frozen truth.
    billing_address_snapshot: dict | None = None
    billing_address_id: int | None = None
    payment_method: str = "prepaid"
    payment_instrument: str | None = None
    payment_discount_amount: Decimal = Decimal("0")
    cod_surcharge_amount: Decimal = Decimal("0")
    cod_balance: Decimal = Decimal("0")
    payment_intent_id: str | None
    items: list[OrderItemRead]
    # Normalized children (preferred over the flat fields below).
    payments: list[OrderPaymentRead] = []
    shipments: list[ShipmentRead] = []
    addresses: list[OrderAddressRead] = []
    customer: AdminCustomerBrief
    tracking_number: str | None
    carrier: str | None
    # Carrier-side shipment identity. Populated by ShippingService once
    # the order has been pushed to the carrier (Phase 4+).
    shipping_provider: str | None = None
    shipping_awb: str | None = None
    shipping_label_url: str | None = None
    shipment_created_at: datetime | None = None
    pickup_id: str | None = None
    pickup_scheduled_for: datetime | None = None
    tracking_events: list[dict] | None = None
    last_tracking_at: datetime | None = None
    paid_at: datetime | None
    shipped_at: datetime | None
    delivered_at: datetime | None
    cancelled_at: datetime | None
    refunded_at: datetime | None
    refund_reason: str | None
    internal_notes: str | None
    created_at: datetime
    updated_at: datetime


class ShipRequest(BaseModel):
    tracking_number: str | None = Field(default=None, max_length=120)
    carrier: str | None = Field(default=None, max_length=60)


class SchedulePickupRequest(BaseModel):
    """Body of POST /orders/admin/{id}/schedule-pickup.

    `pickup_date` is an ISO date (YYYY-MM-DD). Carriers in India typically
    won't accept past dates or pickups more than ~7 days out — the service
    layer enforces "tomorrow or later, within 7 days" so admins get a clear
    error instead of a vague carrier rejection.
    """

    pickup_date: datetime = Field(
        description="When the carrier should collect the package (any time on this date)."
    )
    expected_package_count: int = Field(default=1, ge=1, le=999)


class RefundOrCancelRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=255)


class AdminRefundRequest(RefundOrCancelRequest):
    """Body of POST /orders/admin/{id}/refund.

    `force_manual` skips the gateway call entirely and records the refund for
    offline/manual processing (e.g. the operator already reversed it in the
    gateway dashboard, or wants to settle by bank transfer).
    """

    force_manual: bool = False


class CustomerCancelRequest(BaseModel):
    """Optional body of POST /orders/{id}/cancel (customer self-service).
    Omitted/blank reason falls back to "Cancelled by customer"."""

    reason: str | None = Field(default=None, max_length=255)


class NotesRequest(BaseModel):
    internal_notes: str | None = Field(default=None, max_length=4000)
