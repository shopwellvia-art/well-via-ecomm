"""Dual-write helpers for the order normalization (2026-06-21).

These keep the new normalized tables — ``order_payments``, ``shipments``,
``order_addresses`` — in step with the legacy flat columns on ``orders`` that
the service layer already writes.  The legacy columns stay authoritative for
the existing storefront/admin reads; these helpers populate the new tables
alongside them so new clients (and reporting) can use the normalized shape.

Design rules
------------
* Defensive: helpers never raise on the happy path and are safe to call
  repeatedly, so wiring them into checkout/webhook/admin flows can't break a
  sale or make a carrier webhook 500.
* Children are attached via the ORM relationships (``order.payments`` etc.),
  so the cascade persists them when the order flushes — no manual order_id.
* Address snapshots are WRITE-ONCE (immutable history): ``sync_order_addresses``
  is a no-op once rows exist.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from app.models.order import Order, OrderStatus
from app.models.order_address import OrderAddress, OrderAddressType
from app.models.order_payment import OrderPayment, PaymentTxnStatus
from app.models.shipment import Shipment, ShipmentStatus

logger = logging.getLogger(__name__)


def _D(value) -> Decimal:
    return Decimal(str(value or 0))


def _is_cod_leg(p: OrderPayment) -> bool:
    return (p.payment_method or "").lower() == "cod"


def make_order_number(order_id: int, when: datetime | None = None) -> str:
    """Human-friendly reference, e.g. "WV-2026-000123". Embeds the numeric id
    so it's globally unique without a separate counter. Matches the migration
    backfill format (CONCAT('WV-', YEAR(created_at), '-', LPAD(id,6,'0')))."""
    year = (when or datetime.now(timezone.utc)).year
    return f"WV-{year}-{order_id:06d}"


# --------------------------------------------------------------------------- #
# Address snapshots                                                            #
# --------------------------------------------------------------------------- #
def _row_from_snapshot(
    address_type: OrderAddressType,
    snap: dict | None,
    *,
    email: str | None = None,
    pincode_fallback: str | None = None,
) -> OrderAddress:
    snap = snap or {}
    return OrderAddress(
        address_type=address_type,
        full_name=snap.get("full_name"),
        phone=snap.get("phone"),
        email=email,
        address_line1=snap.get("line1"),
        address_line2=snap.get("line2"),
        city=snap.get("city"),
        state=snap.get("state"),
        country=snap.get("country"),
        pincode=snap.get("pincode") or pincode_fallback,
        landmark=snap.get("landmark"),
    )


def sync_order_addresses(order: Order, *, email: str | None = None) -> None:
    """Attach SHIPPING / BILLING snapshot rows to a freshly built order.

    Write-once: does nothing if the order already has address rows, so a
    customer later editing their saved address never rewrites order history.
    A NULL billing snapshot means "same as shipping" — no redundant BILLING
    row is created (mirrors the response + migration doctrine).
    """
    if order.addresses:
        return
    if order.shipping_address_snapshot:
        order.addresses.append(
            _row_from_snapshot(
                OrderAddressType.SHIPPING,
                order.shipping_address_snapshot,
                email=email,
                pincode_fallback=order.shipping_pincode,
            )
        )
    elif order.shipping_address:
        # Legacy free-text shipping with no structured snapshot.
        order.addresses.append(
            OrderAddress(
                address_type=OrderAddressType.SHIPPING,
                address_line1=order.shipping_address,
                pincode=order.shipping_pincode,
                email=email,
            )
        )
    if order.billing_address_snapshot:
        order.addresses.append(
            _row_from_snapshot(
                OrderAddressType.BILLING,
                order.billing_address_snapshot,
                email=email,
            )
        )


# --------------------------------------------------------------------------- #
# Payments (one row per money movement)                                        #
# --------------------------------------------------------------------------- #
def init_order_payments(order: Order) -> None:
    """Create the money-movement rows for a freshly built order:

      * a gateway / prepaid leg of (total - cod_balance) when that is > 0
      * a COD leg of cod_balance when that is > 0

    For a pure prepaid order this is one leg == total; for full COD one COD
    leg == total; for split-COD both. The legs sum to the order total, so the
    "payment amount matches order total" invariant holds by construction.
    No-op if rows already exist.
    """
    if order.payments:
        return
    total = _D(order.total_amount)
    cod_balance = _D(order.cod_balance)
    prepaid = total - cod_balance
    currency = order.currency or "INR"
    if prepaid > 0:
        order.payments.append(
            OrderPayment(
                payment_method=order.payment_method,
                payment_status=PaymentTxnStatus.PENDING,
                amount=prepaid,
                currency=currency,
                transaction_reference=order.payment_intent_id,
            )
        )
    if cod_balance > 0:
        order.payments.append(
            OrderPayment(
                payment_method="cod",
                payment_status=PaymentTxnStatus.PENDING,
                amount=cod_balance,
                currency=currency,
                transaction_reference=order.payment_intent_id,
            )
        )


def attach_gateway_to_prepaid_leg(
    order: Order,
    *,
    gateway: str | None,
    gateway_order_id: str | None = None,
) -> None:
    """After provider.initiate(): stamp gateway identity onto the prepaid leg
    and move it PENDING -> INITIATED."""
    for p in order.payments:
        if _is_cod_leg(p):
            continue
        p.gateway = gateway
        if gateway_order_id is not None:
            p.gateway_order_id = gateway_order_id
        if p.payment_status == PaymentTxnStatus.PENDING:
            p.payment_status = PaymentTxnStatus.INITIATED


def mark_prepaid_paid(
    order: Order,
    *,
    when: datetime | None = None,
    gateway_payment_id: str | None = None,
    raw: dict | None = None,
) -> None:
    """Flip the gateway (non-COD) legs to PAID. No-op for pure-COD orders,
    so calling it on the shared COD/gateway success path is safe."""
    when = when or datetime.now(timezone.utc)
    for p in order.payments:
        if _is_cod_leg(p):
            continue
        if p.payment_status in (PaymentTxnStatus.PAID, PaymentTxnStatus.REFUNDED):
            continue
        p.payment_status = PaymentTxnStatus.PAID
        p.paid_at = when
        if gateway_payment_id and not p.gateway_payment_id:
            p.gateway_payment_id = gateway_payment_id
        if raw is not None and p.raw_gateway_response is None:
            p.raw_gateway_response = raw


def mark_prepaid_failed(order: Order, *, when: datetime | None = None) -> None:
    when = when or datetime.now(timezone.utc)
    for p in order.payments:
        if _is_cod_leg(p):
            continue
        if p.payment_status in (PaymentTxnStatus.PAID, PaymentTxnStatus.REFUNDED):
            continue
        p.payment_status = PaymentTxnStatus.FAILED
        p.failed_at = when


def mark_cod_collected(order: Order, *, when: datetime | None = None) -> None:
    """Flip COD legs to PAID — the courier collected the cash on delivery."""
    when = when or datetime.now(timezone.utc)
    for p in order.payments:
        if not _is_cod_leg(p):
            continue
        if p.payment_status in (PaymentTxnStatus.PAID, PaymentTxnStatus.REFUNDED):
            continue
        p.payment_status = PaymentTxnStatus.PAID
        p.paid_at = when


def mark_payments_refunded(order: Order) -> None:
    """Captured legs become REFUNDED; not-yet-captured legs CANCELLED."""
    for p in order.payments:
        if p.payment_status == PaymentTxnStatus.PAID:
            p.payment_status = PaymentTxnStatus.REFUNDED
        elif p.payment_status not in (PaymentTxnStatus.REFUNDED, PaymentTxnStatus.CANCELLED):
            p.payment_status = PaymentTxnStatus.CANCELLED


def mark_payments_cancelled(order: Order) -> None:
    """Cancel uncaptured legs; a captured leg being cancelled is really a
    refund, so mark it REFUNDED for honest reporting."""
    for p in order.payments:
        if p.payment_status == PaymentTxnStatus.PAID:
            p.payment_status = PaymentTxnStatus.REFUNDED
        elif p.payment_status != PaymentTxnStatus.CANCELLED:
            p.payment_status = PaymentTxnStatus.CANCELLED


# --------------------------------------------------------------------------- #
# Shipments                                                                    #
# --------------------------------------------------------------------------- #
_SHIPMENT_STATUS_FOR_ORDER = {
    OrderStatus.PAID: ShipmentStatus.READY_TO_SHIP,
    OrderStatus.SHIPPED: ShipmentStatus.SHIPPED,
    OrderStatus.DELIVERED: ShipmentStatus.DELIVERED,
}

_SHIPMENT_TS_FIELD = {
    ShipmentStatus.PICKUP_SCHEDULED: "pickup_scheduled_at",
    ShipmentStatus.SHIPPED: "shipped_at",
    ShipmentStatus.IN_TRANSIT: "in_transit_at",
    ShipmentStatus.OUT_FOR_DELIVERY: "out_for_delivery_at",
    ShipmentStatus.DELIVERED: "delivered_at",
    ShipmentStatus.DELIVERY_FAILED: "failed_delivery_at",
    ShipmentStatus.RTO_DELIVERED: "returned_at",
}

# A shipment in one of these states is finished — don't unwind it on a late
# or out-of-order tracking event.
_TERMINAL_SHIPMENT = {
    ShipmentStatus.DELIVERED,
    ShipmentStatus.RTO_DELIVERED,
    ShipmentStatus.CANCELLED,
}


def _ensure_shipment(order: Order) -> Shipment:
    """Return the order's single shipment, creating it from the flat carrier
    columns if absent. (One shipment per order for now.)"""
    if order.shipments:
        return order.shipments[0]
    shipment = Shipment(shipment_status=ShipmentStatus.PENDING)
    order.shipments.append(shipment)
    return shipment


def sync_shipment_from_order(order: Order, *, raw: dict | None = None) -> Shipment:
    """Create/update the order's shipment row from the flat carrier/tracking
    columns and the order status. Used after create_shipment / mark_shipped /
    mark_delivered."""
    shipment = _ensure_shipment(order)
    if order.shipping_provider:
        shipment.courier_partner = order.shipping_provider
    elif order.carrier and not shipment.courier_partner:
        shipment.courier_partner = order.carrier
    if order.shipping_awb:
        shipment.awb_number = order.shipping_awb
    if order.tracking_number or order.shipping_awb:
        shipment.tracking_number = order.tracking_number or order.shipping_awb
    if order.shipping_label_url and not shipment.tracking_url:
        shipment.tracking_url = order.shipping_label_url
    if order.pickup_scheduled_for and not shipment.pickup_scheduled_at:
        shipment.pickup_scheduled_at = order.pickup_scheduled_for
    if order.shipped_at and not shipment.shipped_at:
        shipment.shipped_at = order.shipped_at
    if order.delivered_at and not shipment.delivered_at:
        shipment.delivered_at = order.delivered_at
    if raw is not None:
        shipment.raw_courier_response = raw

    mapped = _SHIPMENT_STATUS_FOR_ORDER.get(order.status)
    if mapped is not None and shipment.shipment_status not in _TERMINAL_SHIPMENT:
        shipment.shipment_status = mapped
    elif shipment.awb_number and shipment.shipment_status == ShipmentStatus.PENDING:
        shipment.shipment_status = ShipmentStatus.READY_TO_SHIP
    return shipment


def set_shipment_status(
    order: Order,
    status: ShipmentStatus,
    *,
    when: datetime | None = None,
    raw: dict | None = None,
) -> Shipment:
    """Set a precise shipment status + its matching per-leg timestamp. Won't
    unwind a terminal shipment. Used by tracking updates / pickup scheduling
    where the carrier gives finer detail than the order status."""
    shipment = _ensure_shipment(order)
    when = when or datetime.now(timezone.utc)
    if not (shipment.shipment_status in _TERMINAL_SHIPMENT and status not in _TERMINAL_SHIPMENT):
        shipment.shipment_status = status
    ts_field = _SHIPMENT_TS_FIELD.get(status)
    if ts_field and getattr(shipment, ts_field) is None:
        setattr(shipment, ts_field, when)
    if raw is not None:
        shipment.raw_courier_response = raw
    return shipment


def cancel_shipments(order: Order) -> None:
    for s in order.shipments:
        if s.shipment_status not in _TERMINAL_SHIPMENT:
            s.shipment_status = ShipmentStatus.CANCELLED
