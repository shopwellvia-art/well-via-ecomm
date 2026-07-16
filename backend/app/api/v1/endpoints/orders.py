from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_permission
from app.core.exceptions import NotFoundError
from app.core.rate_limit import get_client_ip
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.schemas.common import PaginationParams
from app.schemas.order import (
    AdminCustomerBrief,
    AdminOrderListPage,
    AdminOrderRead,
    AdminOrderRow,
    NotesRequest,
    OrderAddressRead,
    OrderItemRead,
    OrderPaymentRead,
    OrderRead,
    RefundOrCancelRequest,
    SchedulePickupRequest,
    ShipmentRead,
    ShipRequest,
)
from app.services.audit_service import AuditService
from app.services.order_service import OrderService

router = APIRouter()


def _row(order: Order) -> AdminOrderRow:
    return AdminOrderRow(
        id=order.id,
        status=order.status,
        total_amount=order.total_amount,
        currency=order.currency,
        customer_email=order.user.email if order.user else "—",
        item_count=len(order.items) if order.items is not None else 0,
        created_at=order.created_at,
    )


def _detail(order: Order) -> AdminOrderRead:
    return AdminOrderRead(
        id=order.id,
        order_number=order.order_number,
        status=order.status,
        subtotal=order.subtotal,
        tax_amount=order.tax_amount,
        discount_amount=order.discount_amount,
        shipping_amount=order.shipping_amount,
        total_amount=order.total_amount,
        coupon_code=order.coupon_code,
        currency=order.currency,
        shipping_address=order.shipping_address,
        shipping_pincode=order.shipping_pincode,
        shipping_address_snapshot=order.shipping_address_snapshot,
        shipping_address_id=order.shipping_address_id,
        billing_address_snapshot=order.billing_address_snapshot,
        billing_address_id=order.billing_address_id,
        payment_method=order.payment_method,
        payment_instrument=order.payment_instrument,
        payment_discount_amount=order.payment_discount_amount,
        cod_surcharge_amount=order.cod_surcharge_amount,
        cod_balance=order.cod_balance,
        payment_intent_id=order.payment_intent_id,
        items=[OrderItemRead.model_validate(i) for i in order.items],
        payments=[OrderPaymentRead.model_validate(p) for p in order.payments],
        shipments=[ShipmentRead.model_validate(s) for s in order.shipments],
        addresses=[OrderAddressRead.model_validate(a) for a in order.addresses],
        customer=AdminCustomerBrief.model_validate(order.user),
        tracking_number=order.tracking_number,
        carrier=order.carrier,
        shipping_provider=order.shipping_provider,
        shipping_awb=order.shipping_awb,
        shipping_label_url=order.shipping_label_url,
        shipment_created_at=order.shipment_created_at,
        pickup_id=order.pickup_id,
        pickup_scheduled_for=order.pickup_scheduled_for,
        tracking_events=order.tracking_events,
        last_tracking_at=order.last_tracking_at,
        paid_at=order.paid_at,
        shipped_at=order.shipped_at,
        delivered_at=order.delivered_at,
        cancelled_at=order.cancelled_at,
        refunded_at=order.refunded_at,
        refund_reason=order.refund_reason,
        internal_notes=order.internal_notes,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


def _audit_order(
    db: Session,
    actor: User,
    request: Request,
    action: str,
    order: Order,
    *,
    summary: str,
    extra: dict[str, Any] | None = None,
) -> None:
    AuditService(db).record(
        actor=actor,
        actor_ip=get_client_ip(request),
        action=action,
        target_type="order",
        target_id=order.id,
        target_label=f"order#{order.id}",
        summary=summary,
        extra=extra,
    )


# ---- ADMIN ROUTES (must appear before /{order_id} so the dynamic route
#      doesn't shadow them) ----


@router.get(
    "/admin",
    response_model=AdminOrderListPage,
)
def admin_list_orders(
    q: str | None = Query(default=None, description="Match order id or customer email"),
    status_filter: OrderStatus | None = Query(default=None, alias="status"),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    _actor: User = Depends(require_permission("orders.view_all")),
    db: Session = Depends(get_db),
):
    svc = OrderService(db)
    offset = (page - 1) * page_size
    items, total = svc.admin_search(
        q=q,
        status=status_filter,
        date_from=date_from,
        date_to=date_to,
        offset=offset,
        limit=page_size,
    )
    return AdminOrderListPage(
        items=[_row(o) for o in items],
        total=total,
        page=page,
        page_size=page_size,
        counts_by_status=svc.counts_by_status(),
    )


@router.get(
    "/admin/{order_id}",
    response_model=AdminOrderRead,
)
def admin_get_order(
    order_id: int,
    _actor: User = Depends(require_permission("orders.view_all")),
    db: Session = Depends(get_db),
):
    return _detail(OrderService(db).admin_get(order_id))


@router.post("/admin/{order_id}/ship", response_model=AdminOrderRead)
def admin_ship_order(
    order_id: int,
    payload: ShipRequest,
    request: Request,
    actor: User = Depends(require_permission("orders.update_status")),
    db: Session = Depends(get_db),
):
    svc = OrderService(db)
    order = svc.mark_shipped(
        order_id,
        tracking_number=payload.tracking_number,
        carrier=payload.carrier,
    )
    _audit_order(
        db,
        actor,
        request,
        "order.ship",
        order,
        summary=(
            f"Shipped order #{order.id}"
            + (f" via {order.carrier}" if order.carrier else "")
            + (f" ({order.tracking_number})" if order.tracking_number else "")
        ),
        extra={
            "tracking_number": order.tracking_number,
            "carrier": order.carrier,
        },
    )
    db.commit()
    return _detail(order)


@router.post("/admin/{order_id}/deliver", response_model=AdminOrderRead)
def admin_deliver_order(
    order_id: int,
    request: Request,
    actor: User = Depends(require_permission("orders.update_status")),
    db: Session = Depends(get_db),
):
    order = OrderService(db).mark_delivered(order_id)
    _audit_order(
        db,
        actor,
        request,
        "order.deliver",
        order,
        summary=f"Marked order #{order.id} delivered",
    )
    db.commit()
    return _detail(order)


@router.post("/admin/{order_id}/cancel", response_model=AdminOrderRead)
def admin_cancel_order(
    order_id: int,
    payload: RefundOrCancelRequest,
    request: Request,
    actor: User = Depends(require_permission("orders.refund")),
    db: Session = Depends(get_db),
):
    order = OrderService(db).cancel(order_id, reason=payload.reason)
    _audit_order(
        db,
        actor,
        request,
        "order.cancel",
        order,
        summary=f"Cancelled order #{order.id}: {payload.reason}",
        extra={"reason": payload.reason},
    )
    db.commit()
    return _detail(order)


@router.post("/admin/{order_id}/refund", response_model=AdminOrderRead)
def admin_refund_order(
    order_id: int,
    payload: RefundOrCancelRequest,
    request: Request,
    actor: User = Depends(require_permission("orders.refund")),
    db: Session = Depends(get_db),
):
    order = OrderService(db).refund(order_id, reason=payload.reason)
    _audit_order(
        db,
        actor,
        request,
        "order.refund",
        order,
        summary=f"Refunded order #{order.id}: {payload.reason}",
        extra={"reason": payload.reason, "total": float(order.total_amount)},
    )
    db.commit()
    return _detail(order)


@router.post("/admin/{order_id}/push-to-carrier", response_model=AdminOrderRead)
def admin_push_to_carrier(
    order_id: int,
    request: Request,
    actor: User = Depends(require_permission("orders.update_status")),
    db: Session = Depends(get_db),
):
    """Push a PAID order to the active shipping provider and persist the AWB.

    The actual transition to SHIPPED still happens via `mark_shipped` (or via
    the tracking webhook in Phase 6) — this just creates the carrier-side
    shipment and records the AWB. We keep the two actions separate so admins
    can review the AWB / print the label before promising the customer.
    """
    from app.services.shipping_service import ShippingService

    svc = ShippingService(db)
    order = svc.create_shipment_for_order(order_id)
    _audit_order(
        db,
        actor,
        request,
        "order.shipment_create",
        order,
        summary=(
            f"Pushed order #{order.id} to {order.shipping_provider} "
            f"(AWB {order.shipping_awb})"
        ),
        extra={
            "provider": order.shipping_provider,
            "awb": order.shipping_awb,
        },
    )
    db.commit()
    return _detail(order)


@router.post("/admin/{order_id}/schedule-pickup", response_model=AdminOrderRead)
def admin_schedule_pickup(
    order_id: int,
    payload: SchedulePickupRequest,
    request: Request,
    actor: User = Depends(require_permission("orders.update_status")),
    db: Session = Depends(get_db),
):
    """Schedule the carrier pickup for an order that already has an AWB."""
    from app.services.shipping_service import ShippingService

    order = ShippingService(db).schedule_pickup_for_order(
        order_id,
        pickup_date=payload.pickup_date,
        expected_package_count=payload.expected_package_count,
    )
    _audit_order(
        db,
        actor,
        request,
        "order.pickup_schedule",
        order,
        summary=(
            f"Scheduled pickup {order.pickup_id} for order #{order.id} "
            f"on {order.pickup_scheduled_for:%Y-%m-%d}"
        ),
        extra={
            "pickup_id": order.pickup_id,
            "pickup_scheduled_for": order.pickup_scheduled_for.isoformat()
            if order.pickup_scheduled_for
            else None,
        },
    )
    db.commit()
    return _detail(order)


@router.post("/admin/{order_id}/sync-tracking", response_model=AdminOrderRead)
def admin_sync_tracking(
    order_id: int,
    request: Request,
    actor: User = Depends(require_permission("orders.update_status")),
    db: Session = Depends(get_db),
):
    """Pull the carrier's current tracking state for this order and apply
    any events we don't already have. Manual fallback when the webhook
    didn't fire (or hasn't been configured)."""
    from app.services.shipping_service import ShippingService

    order = ShippingService(db).sync_tracking_for_order(order_id)
    _audit_order(
        db,
        actor,
        request,
        "order.tracking_sync",
        order,
        summary=f"Pulled tracking for order #{order.id} ({order.shipping_awb})",
    )
    db.commit()
    return _detail(order)


@router.post("/admin/{order_id}/cancel-shipment", response_model=AdminOrderRead)
def admin_cancel_shipment(
    order_id: int,
    request: Request,
    actor: User = Depends(require_permission("orders.update_status")),
    db: Session = Depends(get_db),
):
    """Cancel the carrier-side shipment for this order (DTDC / any provider
    that exposes cancel_shipment). Does NOT change order.status."""
    from app.services.shipping_service import ShippingService

    order = ShippingService(db).cancel_shipment_for_order(order_id)
    _audit_order(
        db,
        actor,
        request,
        "order.shipment_cancel",
        order,
        summary=f"Cancelled carrier shipment for order #{order.id}",
    )
    db.commit()
    return _detail(order)


@router.get("/admin/{order_id}/shipping-label")
def admin_shipping_label(
    order_id: int,
    _actor: User = Depends(require_permission("orders.view_all")),
    db: Session = Depends(get_db),
):
    """Streams the carrier-issued shipping label as a PDF.

    Returns the raw PDF body so the admin's browser can render it inline or
    download it via `Content-Disposition: attachment`. Auth flows through
    the same permission as the order detail view — anyone who can see the
    order can print its label.
    """
    from app.services.shipping_service import ShippingService

    pdf, filename = ShippingService(db).label_pdf_for_order(order_id)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/admin/{order_id}/label-local")
def admin_local_label(
    order_id: int,
    _actor: User = Depends(require_permission("orders.view_all")),
    db: Session = Depends(get_db),
):
    """Streams an in-house generated 4x6 shipping-label PDF (DTDC-style) built
    from our own order data. Works for any order (no live carrier/AWB needed)
    — a preview/fallback alongside the carrier's official label."""
    from app.services.shipping_service import ShippingService

    pdf, filename = ShippingService(db).local_label_pdf_for_order(order_id)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.patch("/admin/{order_id}/notes", response_model=AdminOrderRead)
def admin_update_notes(
    order_id: int,
    payload: NotesRequest,
    request: Request,
    actor: User = Depends(require_permission("orders.update_status")),
    db: Session = Depends(get_db),
):
    order = OrderService(db).update_notes(order_id, payload.internal_notes)
    _audit_order(
        db,
        actor,
        request,
        "order.notes_update",
        order,
        summary=f"Updated internal notes on order #{order.id}",
    )
    db.commit()
    return _detail(order)


# ---- USER ROUTES ----

# NOTE: The legacy `POST /orders` (create_order) route was removed. It called
# OrderService.create — a pre-checkout path that decremented real stock and left
# the order PENDING with no payment attached and gateway_code NULL, so
# reconcile_pending never settled or expired it. Any authenticated user could
# loop it to reserve (drain) inventory for every product without ever paying.
# The single supported order-creation path is now POST /checkout
# (payments.checkout_router). OrderService.create is retained as an internal /
# test-only order builder and is no longer reachable over HTTP.


@router.get("", response_model=list[OrderRead])
def list_orders(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    p = PaginationParams(page=page, page_size=page_size)
    return OrderService(db).list_for_user(user.id, offset=p.offset, limit=p.page_size)


@router.get("/{order_id}", response_model=OrderRead)
def get_order(
    order_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return OrderService(db).get_for_user(user.id, order_id)
