import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.order import Order, OrderItem, OrderStatus
from app.repositories.order_repository import OrderRepository
from app.repositories.product_repository import ProductRepository
from app.schemas.order import OrderCreate
from app.services import order_sync

logger = logging.getLogger(__name__)


# Legal forward state transitions. Backward / arbitrary jumps are refused.
_ALLOWED_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.PAID, OrderStatus.CANCELLED},
    OrderStatus.PAID: {OrderStatus.SHIPPED, OrderStatus.CANCELLED, OrderStatus.REFUNDED},
    OrderStatus.SHIPPED: {OrderStatus.DELIVERED, OrderStatus.REFUNDED},
    OrderStatus.DELIVERED: {OrderStatus.REFUNDED},
    OrderStatus.CANCELLED: set(),
    OrderStatus.REFUNDED: set(),
}


class OrderService:
    def __init__(self, db: Session):
        self.db = db
        self.orders = OrderRepository(db)
        self.products = ProductRepository(db)

    def create(self, user_id: int, data: OrderCreate) -> Order:
        order = Order(user_id=user_id, shipping_address=data.shipping_address)
        total = Decimal("0.00")

        for line in data.items:
            product = self.products.get(line.product_id)
            if not product:
                raise NotFoundError(f"Product {line.product_id} not found")
            if product.stock < line.quantity:
                raise ConflictError(f"Insufficient stock for {product.sku}")

            self.products.decrement_stock(product, line.quantity)
            item = OrderItem(
                product_id=product.id,
                quantity=line.quantity,
                unit_price=product.price,
                unit_cost=product.cost,  # snapshot cost like unit_price; may be None
            )
            order.items.append(item)
            total += product.price * line.quantity

        order.total_amount = total
        order.status = OrderStatus.PENDING
        # Normalized dual-write: a prepaid payment leg + the (legacy free-text)
        # shipping address snapshot. Children persist via cascade on flush;
        # order_number embeds the allocated id.
        order_sync.init_order_payments(order)
        order_sync.sync_order_addresses(order)
        self.orders.add(order)
        self.db.flush()
        if not order.order_number:
            order.order_number = order_sync.make_order_number(order.id)
        self.db.commit()
        return self.orders.get_with_items(order.id)  # type: ignore[return-value]

    def get_for_user(self, user_id: int, order_id: int) -> Order:
        order = self.orders.get_with_items(order_id)
        if not order or order.user_id != user_id:
            raise NotFoundError("Order not found")
        return order

    def list_for_user(self, user_id: int, *, offset: int, limit: int) -> list[Order]:
        return self.orders.list_for_user(user_id, offset=offset, limit=limit)

    # ---- Admin reads ----

    def admin_search(
        self,
        *,
        q: str | None,
        status: OrderStatus | None,
        date_from: datetime | None,
        date_to: datetime | None,
        offset: int,
        limit: int,
    ) -> tuple[list[Order], int]:
        return self.orders.admin_search(
            q=q,
            status=status,
            date_from=date_from,
            date_to=date_to,
            offset=offset,
            limit=limit,
        )

    def admin_get(self, order_id: int) -> Order:
        order = self.orders.get_with_items(order_id)
        if not order:
            raise NotFoundError("Order not found")
        return order

    def counts_by_status(self) -> dict[str, int]:
        return self.orders.counts_by_status()

    # ---- Admin state transitions ----

    def mark_shipped(
        self,
        order_id: int,
        *,
        tracking_number: str | None,
        carrier: str | None,
    ) -> Order:
        self._lock_order(order_id)
        order = self.admin_get(order_id)
        self._assert_transition(order, OrderStatus.SHIPPED)
        order.status = OrderStatus.SHIPPED
        order.tracking_number = (tracking_number or "").strip() or None
        order.carrier = (carrier or "").strip() or None
        order.shipped_at = datetime.now(timezone.utc)
        order_sync.sync_shipment_from_order(order)
        self.db.flush()
        self._notify(order, "order_shipped")
        return order

    def mark_delivered(self, order_id: int) -> Order:
        self._lock_order(order_id)
        order = self.admin_get(order_id)
        self._assert_transition(order, OrderStatus.DELIVERED)
        order.status = OrderStatus.DELIVERED
        order.delivered_at = datetime.now(timezone.utc)
        order_sync.sync_shipment_from_order(order)
        # COD balance is collected by the courier on delivery — settle the COD
        # payment leg now (no-op for prepaid orders).
        order_sync.mark_cod_collected(order, when=order.delivered_at)
        self.db.flush()
        self._notify(order, "order_delivered")
        return order

    def cancel(self, order_id: int, *, reason: str) -> Order:
        """Cancel an unshipped order. Restores stock + reverses loyalty points.
        Use `refund` for shipped/delivered orders so the reporting distinction
        is preserved."""
        self._lock_order(order_id)
        order = self.admin_get(order_id)
        self._assert_transition(order, OrderStatus.CANCELLED)
        if not reason.strip():
            raise ValidationError("Provide a reason — cancellations are audit-logged.")
        order.status = OrderStatus.CANCELLED
        order.cancelled_at = datetime.now(timezone.utc)
        order.refund_reason = reason.strip()[:255]
        order_sync.mark_payments_cancelled(order)
        order_sync.cancel_shipments(order)
        self._restore_stock(order)
        self._reverse_loyalty(order)
        self.db.flush()
        self._notify(order, "order_cancelled")
        return order

    def refund(self, order_id: int, *, reason: str) -> Order:
        """Refund a paid/shipped/delivered order. Same downstream effects as
        cancel but lands in REFUNDED so analytics can separate "never went
        out" from "came back to us"."""
        self._lock_order(order_id)
        order = self.admin_get(order_id)
        self._assert_transition(order, OrderStatus.REFUNDED)
        if not reason.strip():
            raise ValidationError("Provide a reason — refunds are audit-logged.")
        order.status = OrderStatus.REFUNDED
        order.refunded_at = datetime.now(timezone.utc)
        order.refund_reason = reason.strip()[:255]
        order_sync.mark_payments_refunded(order)
        self._restore_stock(order)
        self._reverse_loyalty(order)
        self.db.flush()
        self._notify(order, "order_refunded")
        return order

    def update_notes(self, order_id: int, notes: str | None) -> Order:
        order = self.admin_get(order_id)
        order.internal_notes = (notes or None)
        self.db.flush()
        return order

    # ---- Internals ----

    def _lock_order(self, order_id: int) -> None:
        """Take a row lock on the order before a state transition.

        The transitions below are check-then-act on order.status with no lock,
        so two concurrent admin actions (or an admin action racing payment
        settlement) could both read the same status and both apply their side
        effects — double stock restore, double loyalty reversal. Acquiring the
        row lock first serializes them; the lock releases on the request commit.
        """
        self.db.execute(
            select(Order.id).where(Order.id == order_id).with_for_update()
        ).first()

    def _assert_transition(self, order: Order, target: OrderStatus) -> None:
        allowed = _ALLOWED_TRANSITIONS.get(order.status, set())
        if target not in allowed:
            raise ConflictError(
                f"Cannot move order from {order.status.value} to {target.value}."
            )

    def _restore_stock(self, order: Order) -> None:
        """Put units back. Mirrors the decrement we did at checkout."""
        for item in order.items:
            product = self.products.get(item.product_id)
            if product is None:
                logger.warning(
                    "restock: product %s gone, skipping order #%s line",
                    item.product_id,
                    order.id,
                )
                continue
            # Atomic increment, not a Python read-modify-write: a concurrent
            # sale's decrement must not be lost between our read and flush.
            self.products.increment_stock(product, item.quantity)

    def _reverse_loyalty(self, order: Order) -> None:
        """Reverse the points awarded for this order. Idempotent — the
        loyalty engine's (reason, ref_type, ref_id) unique index makes a
        repeat call a no-op."""
        from app.services.loyalty_service import LoyaltyService

        try:
            LoyaltyService(self.db).reverse_for_order(order)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "loyalty reverse failed for order #%s: %s", order.id, exc
            )

    def _notify(self, order: Order, event_name: str) -> None:
        """Fire-and-forget customer notification. A flaky email/SMS provider
        never breaks the underlying order action."""
        from app.services.notifications import NotificationEvent, NotificationService

        try:
            NotificationService(self.db).notify(order, NotificationEvent(event_name))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "notification dispatch failed for order #%s event %s: %s",
                order.id,
                event_name,
                exc,
            )
