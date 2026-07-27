import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.order import Order, OrderItem, OrderStatus
from app.models.order_payment import OrderPayment, PaymentTxnStatus
from app.models.payment_event import PaymentEvent, PaymentEventType
from app.models.shipment import ShipmentStatus
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
        order = self._lock_order(order_id)
        if order is None:
            raise NotFoundError("Order not found")
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
        order = self._lock_order(order_id)
        if order is None:
            raise NotFoundError("Order not found")
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
        """Cancel an unshipped order (admin). Restores stock + reverses loyalty
        points. Use `refund` for shipped/delivered orders so the reporting
        distinction is preserved."""
        order = self._lock_order(order_id)
        if order is None:
            raise NotFoundError("Order not found")
        self._assert_transition(order, OrderStatus.CANCELLED)
        if not reason.strip():
            raise ValidationError("Provide a reason — cancellations are audit-logged.")
        self._cancel_core(order, reason=reason.strip())
        return order

    def refund(
        self, order_id: int, *, reason: str, force_manual: bool = False
    ) -> Order:
        """Refund a paid/shipped/delivered order. Same downstream effects as
        cancel but lands in REFUNDED so analytics can separate "never went
        out" from "came back to us".

        When the order has a captured gateway leg, the money is first sent
        back through the original gateway (``provider.refund``, mirroring
        ReturnService). If the provider lacks refund support, the call fails,
        or ``force_manual`` is set, the refund is recorded as ``manual`` — the
        operator completes the reversal offline — and the bookkeeping below
        (status flip, payment legs, restock, loyalty) still runs unchanged.

        Only the money still with us moves: amounts already reversed through
        return-refunds are deducted, and the gateway call is skipped entirely
        once the leg is fully reversed.
        """
        order = self._lock_order(order_id)
        if order is None:
            raise NotFoundError("Order not found")
        self._assert_transition(order, OrderStatus.REFUNDED)
        if not reason.strip():
            raise ValidationError("Provide a reason — refunds are audit-logged.")

        leg = self._captured_gateway_leg(order)
        if leg is not None:
            # Refund only what is still with us: the leg amount minus anything
            # already sent back through return-refunds (a PARTIALLY_REFUNDED
            # leg keeps its ORIGINAL amount — nothing on OrderPayment tracks
            # the cumulative reversed total). Without this, a full refund
            # after a Rs 500 return-refund would re-present the full leg
            # amount — rejected by the gateway, then recorded as a manual
            # note telling the operator to over-pay.
            amount = Decimal(leg.amount) - self._already_refunded_total(order)
            if amount <= 0:
                logger.info(
                    "order #%s: leg %s already fully reversed via return-"
                    "refunds — skipping gateway refund", order.id, leg.id,
                )
                leg = None
        if leg is not None:
            refund_method, refund_reference, raw = "manual", self._refund_reference(order, leg), None
            if force_manual:
                logger.info(
                    "order #%s: force_manual set — skipping gateway refund (ref=%s)",
                    order.id, refund_reference,
                )
            else:
                refund_method, refund_reference, raw = self._attempt_gateway_refund(
                    order,
                    leg,
                    amount=amount,
                    refund_reference=refund_reference,
                    reason=f"Admin refund: {reason.strip()}",
                )
            self._record_refund_outcome(
                order,
                amount=amount,
                refund_method=refund_method,
                refund_reference=refund_reference,
                raw=raw,
                context="admin refund",
            )

        order.status = OrderStatus.REFUNDED
        order.refunded_at = datetime.now(timezone.utc)
        order.refund_reason = reason.strip()[:255]
        order_sync.mark_payments_refunded(order)
        self._restore_stock(order)
        self._reverse_loyalty(order)
        self.db.flush()
        self._notify(order, "order_refunded")
        return order

    # ---- Customer state transitions ----

    def cancel_for_customer(
        self, user_id: int, order_id: int, *, reason: str | None = None
    ) -> Order:
        """Customer self-service cancellation.

        Allowed only while we can still stop fulfillment: PENDING orders, or
        PAID orders that have not been pushed to a carrier yet (no AWB /
        tracking / pickup — the shipment fields order_sync maintains). 404 for
        orders the caller doesn't own (never reveal other users' order ids).

        For PAID prepaid orders the captured amount is sent back through the
        original gateway first (same path as the admin refund); if the gateway
        can't do it, the order still cancels but the refund is recorded as
        ``manual`` so admins see it needs action.
        """
        order = self._lock_order(order_id)
        if not order or order.user_id != user_id:
            raise NotFoundError("Order not found")
        if order.status not in (OrderStatus.PENDING, OrderStatus.PAID):
            raise ConflictError(
                "This order can no longer be cancelled "
                f"(current status: {order.status.value})."
            )
        if order.status == OrderStatus.PAID and self._carrier_engaged(order):
            raise ConflictError(
                "This order is already with the courier and can't be "
                "cancelled — you can request a return once it arrives."
            )

        leg = self._captured_gateway_leg(order)
        if leg is not None:
            amount = Decimal(leg.amount)
            refund_method, refund_reference, raw = self._attempt_gateway_refund(
                order,
                leg,
                amount=amount,
                refund_reference=self._refund_reference(order, leg),
                reason=f"Customer cancellation of order #{order.id}",
            )
            self._record_refund_outcome(
                order,
                amount=amount,
                refund_method=refund_method,
                refund_reference=refund_reference,
                raw=raw,
                context="customer cancellation",
            )

        self._cancel_core(
            order, reason=(reason or "").strip() or "Cancelled by customer"
        )
        return order

    def update_notes(self, order_id: int, notes: str | None) -> Order:
        order = self.admin_get(order_id)
        order.internal_notes = (notes or None)
        self.db.flush()
        return order

    # ---- Internals ----

    def _cancel_core(self, order: Order, *, reason: str) -> None:
        """Shared cancellation side effects (admin cancel + customer cancel):
        status flip, payment-leg + shipment sync, stock restore, loyalty
        reversal, customer notification. Callers hold the row lock and have
        already validated the transition/ownership."""
        order.status = OrderStatus.CANCELLED
        order.cancelled_at = datetime.now(timezone.utc)
        order.refund_reason = reason[:255]
        order_sync.mark_payments_cancelled(order)
        order_sync.cancel_shipments(order)
        self._restore_stock(order)
        self._reverse_loyalty(order)
        self.db.flush()
        self._notify(order, "order_cancelled")

    def _carrier_engaged(self, order: Order) -> bool:
        """Has this order been pushed to a carrier? True once any carrier-side
        identity exists — the flat AWB/tracking/pickup columns or a shipment
        row that order_sync has advanced past the pre-carrier states."""
        if (
            order.shipping_awb
            or order.tracking_number
            or order.pickup_id
            or order.shipment_created_at
        ):
            return True
        for s in order.shipments:
            if s.awb_number or s.tracking_number:
                return True
            if s.shipment_status not in (
                ShipmentStatus.PENDING,
                ShipmentStatus.READY_TO_SHIP,
                ShipmentStatus.CANCELLED,
            ):
                return True
        return False

    def _captured_gateway_leg(self, order: Order) -> OrderPayment | None:
        """The captured prepaid/gateway payment leg to reverse, or None when
        there is nothing to send back (COD legs are skipped — no electronic
        source to refund to; uncaptured legs never moved money). Mirrors
        ReturnService._gateway_leg plus the captured-status guard."""
        for p in order.payments:
            if (p.payment_method or "").lower() == "cod":
                continue
            if p.payment_status in (
                PaymentTxnStatus.PAID,
                PaymentTxnStatus.PARTIALLY_REFUNDED,
            ):
                return p
            return None
        return None

    def _already_refunded_total(self, order: Order) -> Decimal:
        """Money already sent back to the customer for this order through
        return-refunds. ReturnService only flips the leg's status
        (PARTIALLY_REFUNDED) — the leg keeps its original amount and nothing
        on OrderPayment accumulates the reversed total — so the sum lives on
        the REFUNDED return rows."""
        # Lazy import: keeps the service layers decoupled (mirrors the other
        # cross-service imports in this module).
        from app.models.return_request import ReturnRequest, ReturnStatus

        total = self.db.execute(
            select(func.coalesce(func.sum(ReturnRequest.refund_amount), 0)).where(
                ReturnRequest.order_id == order.id,
                ReturnRequest.status == ReturnStatus.REFUNDED,
            )
        ).scalar_one()
        return Decimal(str(total or 0))

    @staticmethod
    def _refund_reference(order: Order, leg: OrderPayment) -> str:
        """The merchant-side reference for the refund of THIS payment leg.

        Deterministic (order id + leg id, no random suffix) so a retry after
        a failed commit re-presents the SAME reference: the provider can then
        find the refund it already issued under it (see
        RazorpayProvider._find_existing_refund) instead of paying out twice.
        Replaced by the provider's own id when returned. Same shape as
        ReturnService's RFND refs, ORD-scoped."""
        return f"RFNDORD{order.id}-{leg.id}"

    def _attempt_gateway_refund(
        self,
        order: Order,
        leg: OrderPayment,
        *,
        amount: Decimal,
        refund_reference: str,
        reason: str,
    ) -> tuple[str, str, dict | None]:
        """Try to reverse ``amount`` on the order's original gateway.

        Returns ``(refund_method, refund_reference, raw)``:
          * gateway accepted → ``(order.gateway_code, provider refund id, raw)``
          * provider missing/unsupported/failed → ``("manual", our ref, raw)``
            so the caller records an operator-actionable manual refund.

        Never raises — a gateway hiccup must not abort the order transition.
        Mirrors ReturnService._issue_refund's provider resolution.
        """
        # Lazy imports: the payments factory has a known import cycle with the
        # service layer, so it is resolved at call time.
        from app.integrations.payments.base import PaymentStatus, RefundRequest
        from app.integrations.payments.factory import get_provider_for_order

        if not order.gateway_code:
            return "manual", refund_reference, None

        provider = None
        try:
            provider = get_provider_for_order(self.db, order)
        except Exception as exc:  # noqa: BLE001 — gateway no longer configured
            logger.warning(
                "order #%s: could not build provider: %s — recording a manual "
                "refund instead",
                order.id, exc,
            )
        if provider is None or not hasattr(provider, "refund"):
            logger.warning(
                "order #%s: gateway %s has no automated refund API; recorded "
                "for manual gateway processing (ref=%s)",
                order.id, order.gateway_code, refund_reference,
            )
            return "manual", refund_reference, None

        try:
            result = provider.refund(
                RefundRequest(
                    order_id=order.id,
                    amount_minor=int((amount * 100).to_integral_value()),
                    currency=order.currency or "INR",
                    merchant_transaction_id=order.payment_intent_id or "",
                    refund_reference=refund_reference,
                    original_transaction_id=(
                        leg.gateway_payment_id or order.payment_provider_ref
                    ),
                    reason=reason[:255],
                )
            )
        except Exception as exc:  # noqa: BLE001 — provider raised; fall back
            logger.warning(
                "order #%s: gateway %s refund call failed: %s — recording a "
                "manual refund instead (ref=%s)",
                order.id, order.gateway_code, exc, refund_reference,
            )
            return "manual", refund_reference, None
        if result.status == PaymentStatus.FAILED:
            logger.warning(
                "order #%s: gateway %s reported the refund FAILED — recording "
                "a manual refund instead (ref=%s)",
                order.id, order.gateway_code, refund_reference,
            )
            return "manual", refund_reference, result.raw
        return order.gateway_code, (result.refund_id or refund_reference), result.raw

    def _record_refund_outcome(
        self,
        order: Order,
        *,
        amount: Decimal,
        refund_method: str,
        refund_reference: str,
        raw: dict | None,
        context: str,
    ) -> None:
        """Persist how the money went back: an append-only payment_events row
        plus an internal-notes line so a manual refund is visible on the admin
        order page without digging through audit logs.

        Unlike ReturnService, the event row is written through THIS session,
        not payment_audit's independent one. The callers here hold a
        FOR UPDATE lock on the orders row (`_lock_order`), so an
        independent-session INSERT would block on the payment_events→orders
        FK check against our own uncommitted lock until the client timeout —
        stalling the request ~30s and silently losing the audit row. Writing
        in-transaction commits the audit row atomically with the refund
        transition it describes. ReturnService keeps the own-session write
        because it never locks the orders row."""
        message = f"{context}: refund of {amount} via {refund_method}"
        self.db.add(
            PaymentEvent(
                event_type=PaymentEventType.REFUND_ATTEMPT,
                order_id=order.id,
                merchant_transaction_id=order.payment_intent_id,
                gateway_code=order.gateway_code,
                provider_ref=refund_reference,
                amount_reported_minor=int((amount * 100).to_integral_value()),
                # Same hygiene rules as payment_audit.record_payment_event:
                # cap the message at the column limit; raw is a bounded
                # provider-protocol payload (never secrets/signatures).
                message=message[:500],
                raw_payload=raw,
            )
        )
        if refund_method == "manual":
            note = (
                f"Refund of {amount} {order.currency or 'INR'} NEEDS MANUAL "
                f"PROCESSING — gateway refund not completed (ref {refund_reference})."
            )
        else:
            note = (
                f"Refund of {amount} {order.currency or 'INR'} issued via "
                f"{refund_method} (ref {refund_reference})."
            )
        self._append_internal_note(order, note)
        logger.info(
            "order #%s %s: refund amount=%s method=%s ref=%s",
            order.id, context, amount, refund_method, refund_reference,
        )

    @staticmethod
    def _append_internal_note(order: Order, line: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        entry = f"[{stamp}] {line}"
        order.internal_notes = (
            f"{order.internal_notes}\n{entry}" if order.internal_notes else entry
        )

    def _lock_order(self, order_id: int) -> Order | None:
        """Take a row lock on the order (and its payment legs) before a state
        transition, returning the freshly-locked Order (None when absent).

        The transitions below are check-then-act on order.status, so two
        concurrent admin actions (or an admin action racing payment
        settlement) could both read the same status and both apply their side
        effects — double stock restore, double gateway refund. Acquiring the
        row lock first serializes them; the lock releases on the request commit.

        The status/leg checks MUST run on the entity returned here, not on a
        subsequent plain SELECT. Under MySQL's REPEATABLE READ the session's
        read view is usually established before the lock wait (the auth
        dependency already queried this session), so a non-locking re-read
        after the lock returns the PRE-lock snapshot — the second waiter would
        still see PAID and double-apply. The locking read is an InnoDB
        *current* read (latest committed row), and populate_existing pushes
        those fresh values over any stale identity-map instance — mirroring
        payment_service._apply_status. The payment legs are locked+refreshed
        the same way because _captured_gateway_leg keys off leg status.
        """
        order = self.db.execute(
            select(Order)
            .where(Order.id == order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if order is not None:
            self.db.execute(
                select(OrderPayment)
                .where(OrderPayment.order_id == order_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalars().all()
        return order

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
