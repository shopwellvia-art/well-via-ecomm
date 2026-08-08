"""Customer-initiated returns.

Service responsibilities:
  - Create a return request from a delivered order (within the configured
    window, with quantity + ownership guards).
  - Drive the state machine: requested → approved → picked_up → received →
    refunded, with rejected / cancelled as terminal short-circuits.
  - Wrap the reverse-pickup carrier call on Approve, recording the
    reverse_awb. Failures fall back to "approved without a reverse AWB"
    so the admin can still process manually.
  - Compute the default refund amount as the sum of (returned qty × unit
    price) from the original order. Admin can override.

We deliberately do NOT mutate the original order's status here. A return
operates on a slice of the order; the order itself stays DELIVERED. The
only exception: when `refund_amount == order.total_amount` AND all line
items are fully returned, the admin may separately mark the order REFUNDED
through the existing order-refund flow.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.integrations.shipping import (
    CartLine,
    ReverseShipmentRequest,
    ShipmentAddress,
    ShippingProviderError,
)
from app.models.order import Order, OrderItem, OrderStatus
from app.models.order_payment import OrderPayment, PaymentTxnStatus
from app.models.payment_event import PaymentEventType
from app.models.return_request import (
    ReturnItem,
    ReturnReason,
    ReturnRequest,
    ReturnStatus,
)
from app.services.notifications import NotificationEvent, NotificationService
from app.services.payment_audit import record_payment_event
from app.services.settings_service import SettingsService

logger = logging.getLogger(__name__)

_DEFAULT_WINDOW_DAYS = 7
_VALID_REASONS = {r.value for r in ReturnReason}


class ReturnService:
    def __init__(self, db: Session):
        self.db = db
        self.settings = SettingsService(db)

    # ---- customer create ---------------------------------------------------

    def create_return(
        self,
        *,
        user_id: int,
        order_id: int,
        items: list[tuple[int, int]],   # [(order_item_id, qty), ...]
        reason: str,
        customer_notes: str | None,
    ) -> ReturnRequest:
        order = self.db.get(Order, order_id)
        if not order:
            raise NotFoundError("Order not found")
        if order.user_id != user_id:
            # 404 (not 403) to avoid telling random callers what orders exist.
            raise NotFoundError("Order not found")
        if order.status != OrderStatus.DELIVERED:
            raise ConflictError(
                "Returns are only available on delivered orders."
            )
        # Window check. delivered_at + N days must be in the future.
        window = self.settings.get_int(
            "returns.window_days", default=_DEFAULT_WINDOW_DAYS
        )
        if order.delivered_at:
            cutoff = order.delivered_at + timedelta(days=window)
            if datetime.now(timezone.utc) > cutoff.astimezone(timezone.utc):
                raise ConflictError(
                    f"The return window ({window} days) closed on "
                    f"{cutoff:%Y-%m-%d}."
                )

        if reason not in _VALID_REASONS:
            raise ValidationError(
                f"reason must be one of: {sorted(_VALID_REASONS)}"
            )

        # Validate items: every order_item belongs to this order, quantity
        # is positive, and (prior returns + this return) <= original qty.
        order_items_by_id: dict[int, OrderItem] = {it.id: it for it in order.items}
        if not items:
            raise ValidationError("Pick at least one item to return.")

        already_returned = self._returned_quantities_for_order(order_id)
        for oi_id, qty in items:
            oi = order_items_by_id.get(oi_id)
            if not oi:
                raise ValidationError(
                    f"Order item {oi_id} doesn't belong to order #{order_id}."
                )
            if qty <= 0 or qty > oi.quantity:
                raise ValidationError(
                    f"Quantity for item #{oi_id} must be 1–{oi.quantity}."
                )
            prior = already_returned.get(oi_id, 0)
            if prior + qty > oi.quantity:
                remaining = oi.quantity - prior
                raise ConflictError(
                    f"Only {remaining} unit(s) of item #{oi_id} remain "
                    "returnable (the rest are already in another return)."
                )

        req = ReturnRequest(
            order_id=order_id,
            user_id=user_id,
            status=ReturnStatus.REQUESTED,
            reason=reason,
            customer_notes=(customer_notes or None),
            requested_at=datetime.now(timezone.utc),
        )
        for oi_id, qty in items:
            req.items.append(ReturnItem(order_item_id=oi_id, quantity=qty))
        self.db.add(req)
        self.db.flush()
        logger.info(
            "return requested id=%s order=%s user=%s reason=%s items=%s",
            req.id, order_id, user_id, reason, items,
        )
        return req

    def cancel_by_customer(self, *, user_id: int, return_id: int) -> ReturnRequest:
        req = self._owned(user_id, return_id)
        if req.status != ReturnStatus.REQUESTED:
            raise ConflictError(
                "Only pending return requests can be cancelled. Contact "
                "support if you need to cancel an approved return."
            )
        req.status = ReturnStatus.CANCELLED
        req.cancelled_at = datetime.now(timezone.utc)
        self.db.flush()
        return req

    # ---- customer reads ----------------------------------------------------

    def list_for_user(self, user_id: int) -> list[ReturnRequest]:
        stmt = (
            select(ReturnRequest)
            .where(ReturnRequest.user_id == user_id)
            .order_by(ReturnRequest.requested_at.desc())
        )
        return list(self.db.execute(stmt).scalars().all())

    def get_for_user(self, user_id: int, return_id: int) -> ReturnRequest:
        return self._owned(user_id, return_id)

    # ---- admin reads -------------------------------------------------------

    def list_all(self, *, status: str | None = None) -> list[ReturnRequest]:
        stmt = select(ReturnRequest).order_by(ReturnRequest.requested_at.desc())
        if status:
            stmt = stmt.where(ReturnRequest.status == status)
        return list(self.db.execute(stmt).scalars().all())

    def admin_get(self, return_id: int) -> ReturnRequest:
        req = self.db.get(ReturnRequest, return_id)
        if not req:
            raise NotFoundError("Return not found")
        return req

    # ---- admin actions -----------------------------------------------------

    def approve(
        self,
        return_id: int,
        *,
        admin_notes: str | None = None,
        refund_amount: Decimal | None = None,
    ) -> ReturnRequest:
        req = self.admin_get(return_id)
        if req.status != ReturnStatus.REQUESTED:
            raise ConflictError(
                f"Only requested returns can be approved (current state: "
                f"{req.status.value})."
            )

        # Compute the refund amount if the admin didn't override.
        if refund_amount is None:
            refund_amount = self._default_refund_amount(req)
        req.refund_amount = refund_amount
        if admin_notes is not None:
            req.admin_notes = admin_notes

        # Best-effort: schedule reverse pickup. Failures don't block the
        # approval — admin can retry via mark-received-then-refund manually.
        self._try_schedule_reverse_pickup(req)

        req.status = ReturnStatus.APPROVED
        req.approved_at = datetime.now(timezone.utc)
        self.db.flush()
        return req

    def reject(self, return_id: int, *, admin_notes: str | None) -> ReturnRequest:
        req = self.admin_get(return_id)
        if req.status != ReturnStatus.REQUESTED:
            raise ConflictError(
                f"Only requested returns can be rejected (current state: "
                f"{req.status.value})."
            )
        req.status = ReturnStatus.REJECTED
        req.admin_notes = admin_notes
        req.rejected_at = datetime.now(timezone.utc)
        self.db.flush()
        return req

    def mark_picked_up(self, return_id: int) -> ReturnRequest:
        req = self.admin_get(return_id)
        if req.status != ReturnStatus.APPROVED:
            raise ConflictError(
                f"Only approved returns can be marked picked up (current: "
                f"{req.status.value})."
            )
        req.status = ReturnStatus.PICKED_UP
        req.picked_up_at = datetime.now(timezone.utc)
        self.db.flush()
        return req

    def mark_received(self, return_id: int) -> ReturnRequest:
        req = self.admin_get(return_id)
        if req.status not in {ReturnStatus.APPROVED, ReturnStatus.PICKED_UP}:
            raise ConflictError(
                f"Only approved or picked-up returns can be marked received "
                f"(current: {req.status.value})."
            )
        req.status = ReturnStatus.RECEIVED
        req.received_at = datetime.now(timezone.utc)
        self.db.flush()
        return req

    def inspect(
        self,
        return_id: int,
        *,
        passed: bool,
        inspection_notes: str | None = None,
        refund_amount: Decimal | None = None,
    ) -> ReturnRequest:
        """Record the post-receipt inspection verdict.

        The physically-received item is inspected against the customer's stated
        claim and the return policy. The verdict gates the refund:

          * ``passed=False`` → the return is REJECTED (terminal); the customer
            is notified and no money moves.
          * ``passed=True``  → the verdict is recorded, the return stays
            RECEIVED and becomes eligible for :meth:`issue_refund`. An optional
            ``refund_amount`` overrides the amount computed at approval.
        """
        req = self.admin_get(return_id)
        if req.status != ReturnStatus.RECEIVED:
            raise ConflictError(
                "Inspect a return only after it has been marked received "
                f"(current state: {req.status.value})."
            )
        req.inspection_passed = passed
        req.inspection_notes = (inspection_notes or None)
        req.inspected_at = datetime.now(timezone.utc)

        if not passed:
            req.status = ReturnStatus.REJECTED
            req.rejected_at = datetime.now(timezone.utc)
            if inspection_notes:
                req.admin_notes = inspection_notes
            self.db.flush()
            self._notify_return(req, NotificationEvent.RETURN_REJECTED)
            logger.info("return %s rejected after failed inspection", req.id)
            return req

        if refund_amount is not None:
            req.refund_amount = refund_amount
        self.db.flush()
        logger.info("return %s passed inspection; eligible for refund", req.id)
        return req

    def issue_refund(
        self,
        return_id: int,
        *,
        refund_amount: Decimal | None = None,
    ) -> ReturnRequest:
        """Issue the refund for an inspected, passing return back through the
        customer's original payment method, then notify them with an expected
        timeline.

        Guard: the item must have been received AND have a passing inspection —
        we never refund an item we haven't confirmed matches the claim.
        """
        req = self.admin_get(return_id)
        if req.status != ReturnStatus.RECEIVED:
            raise ConflictError(
                "A refund can only be issued on a received return "
                f"(current state: {req.status.value})."
            )
        if req.inspection_passed is not True:
            raise ConflictError(
                "Inspect the item and record a passing result before issuing "
                "a refund."
            )
        if refund_amount is not None:
            req.refund_amount = refund_amount
        amount = Decimal(req.refund_amount or 0)
        if amount <= 0:
            raise ValidationError("Refund amount must be greater than zero.")

        self._issue_refund(req, amount)
        return req

    # ---- internals ---------------------------------------------------------

    def _gateway_leg(self, order: Order) -> OrderPayment | None:
        """The original prepaid/gateway payment leg to reverse. COD legs are
        skipped — there is no electronic source to refund to (the cash refund
        is handled manually)."""
        for p in order.payments:
            if (p.payment_method or "").lower() == "cod":
                continue
            return p
        return None

    def _issue_refund(self, req: ReturnRequest, amount: Decimal) -> None:
        """Route ``amount`` back to the customer's original payment method,
        record the money movement on the original leg + the payment audit log,
        flip the return to REFUNDED, and notify the customer with a timeline.

        Prepaid orders refund to the original gateway (via ``provider.refund``
        when the gateway supports it, else recorded for manual gateway action).
        COD / sourceless orders fall back to ``refund_method='manual'`` (an
        offline bank transfer the operator completes)."""
        # Lazy imports: the payments factory has a known import cycle with the
        # service layer, so it is resolved at call time.
        from app.integrations.payments.base import RefundRequest
        from app.integrations.payments.factory import get_provider_for_order

        order = req.order
        currency = (order.currency if order else None) or "INR"
        amount_minor = int((amount * 100).to_integral_value())
        # A fresh, unique merchant-side reference for THIS refund (idempotency
        # key on the gateway). Replaced by the provider's own id when returned.
        refund_ref = f"RFND{req.id}-{uuid.uuid4().hex[:10]}".upper()

        gateway_leg = self._gateway_leg(order) if order else None
        refund_method = "manual"
        refund_reference = refund_ref
        raw: dict | None = None

        if gateway_leg is not None and order and order.gateway_code:
            provider = None
            try:
                provider = get_provider_for_order(self.db, order)
            except Exception as exc:  # noqa: BLE001 — gateway no longer configured
                logger.warning(
                    "return %s: could not build provider for order %s: %s — "
                    "recording a manual refund instead",
                    req.id, order.id, exc,
                )
            if provider is not None and hasattr(provider, "refund"):
                try:
                    result = provider.refund(
                        RefundRequest(
                            order_id=order.id,
                            amount_minor=amount_minor,
                            currency=currency,
                            merchant_transaction_id=order.payment_intent_id or "",
                            refund_reference=refund_ref,
                            original_transaction_id=(
                                gateway_leg.gateway_payment_id
                                or order.payment_provider_ref
                            ),
                            reason=f"Return #{req.id}: {req.reason}",
                        )
                    )
                except Exception as exc:  # noqa: BLE001 — provider raised
                    # Providers promise never to raise, but a misbehaving one
                    # must not abort the return transition — map it to the
                    # same manual-refund fallback as a missing provider
                    # (refund_method stays "manual", ref stays ours).
                    result = None
                    logger.warning(
                        "return %s: gateway %s refund call raised %s: %s — "
                        "recording a manual refund instead (ref=%s)",
                        req.id, order.gateway_code, type(exc).__name__, exc,
                        refund_ref,
                    )
                if result is not None:
                    refund_method = order.gateway_code
                    refund_reference = result.refund_id or refund_ref
                    raw = result.raw
            else:
                # Gateway integrated for charging but not yet for refunds — route
                # to the original method on the books; an operator completes the
                # gateway-side reversal. Honest about the current capability.
                refund_method = order.gateway_code
                logger.warning(
                    "return %s: gateway %s has no automated refund API; recorded "
                    "for manual gateway processing (ref=%s)",
                    req.id, order.gateway_code, refund_ref,
                )

            # Reflect the reversal on the original payment leg so the order's
            # money state stays truthful (full vs partial return).
            if Decimal(gateway_leg.amount) <= amount:
                gateway_leg.payment_status = PaymentTxnStatus.REFUNDED
            else:
                gateway_leg.payment_status = PaymentTxnStatus.PARTIALLY_REFUNDED

        # Audit the refund — own-session write, durable regardless of caller txn.
        record_payment_event(
            event_type=PaymentEventType.REFUND_ATTEMPT,
            order_id=order.id if order else None,
            merchant_transaction_id=order.payment_intent_id if order else None,
            gateway_code=(order.gateway_code if order else None),
            provider_ref=refund_reference,
            amount_reported_minor=amount_minor,
            message=f"return #{req.id} refund of {amount} via {refund_method}",
            raw_payload=raw,
        )

        req.status = ReturnStatus.REFUNDED
        req.refunded_at = datetime.now(timezone.utc)
        req.refund_method = refund_method
        req.refund_reference = refund_reference[:128]
        self.db.flush()

        timeline = self.settings.get_int("returns.refund_timeline_days", default=7)
        self._notify_return(
            req, NotificationEvent.RETURN_REFUNDED, timeline_days=timeline
        )
        logger.info(
            "return %s refunded amount=%s method=%s ref=%s",
            req.id, amount, refund_method, refund_reference,
        )

    def _notify_return(
        self,
        req: ReturnRequest,
        event: NotificationEvent,
        *,
        timeline_days: int = 7,
    ) -> None:
        """Best-effort customer notification — a failure never breaks the
        admin's return action (mirrors OrderService notifications)."""
        try:
            NotificationService(self.db).notify_return(
                req, event, timeline_days=timeline_days
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("return notification failed for %s: %s", req.id, exc)

    def _owned(self, user_id: int, return_id: int) -> ReturnRequest:
        req = self.db.get(ReturnRequest, return_id)
        if not req or req.user_id != user_id:
            raise NotFoundError("Return not found")
        return req

    def _returned_quantities_for_order(self, order_id: int) -> dict[int, int]:
        """Sum of `quantity` per order_item_id across all *active* returns
        for the order. Cancelled / rejected returns don't count — those
        units are available to return again."""
        from sqlalchemy import func

        active_states = (
            ReturnStatus.REQUESTED,
            ReturnStatus.APPROVED,
            ReturnStatus.PICKED_UP,
            ReturnStatus.RECEIVED,
            ReturnStatus.REFUNDED,
        )
        stmt = (
            select(ReturnItem.order_item_id, func.sum(ReturnItem.quantity))
            .join(ReturnRequest, ReturnRequest.id == ReturnItem.return_id)
            .where(
                ReturnRequest.order_id == order_id,
                ReturnRequest.status.in_(active_states),
            )
            .group_by(ReturnItem.order_item_id)
        )
        rows = self.db.execute(stmt).all()
        return {oi_id: int(qty or 0) for oi_id, qty in rows}

    def _default_refund_amount(self, req: ReturnRequest) -> Decimal:
        """Sum of (return_item.quantity × order_item.unit_price). Shipping
        is NOT refunded by default — that mirrors common Indian-marketplace
        policy; admin can override per return."""
        total = Decimal("0.00")
        for ri in req.items:
            oi = ri.order_item
            if oi:
                total += Decimal(oi.unit_price) * ri.quantity
        return total.quantize(Decimal("0.01"))

    def _try_schedule_reverse_pickup(self, req: ReturnRequest) -> None:
        """Mint a reverse waybill with the active carrier. Wrapped so a
        provider hiccup doesn't block the approval — admin can retry via
        a future "Re-schedule reverse pickup" action (out of scope for v1)
        or process manually."""
        # Lazy import to avoid a service-cycle through ShippingService.
        from app.integrations.shipping import get_shipping_provider

        order = req.order
        if not order or not order.shipping_address or not order.shipping_pincode:
            logger.info(
                "skip reverse pickup for return=%s: order missing address",
                req.id,
            )
            return
        provider = get_shipping_provider(self.db)
        if provider.name == "none":
            logger.info(
                "skip reverse pickup for return=%s: no shipping provider", req.id
            )
            return

        warehouse_name = (self.settings.get_raw("shipping.warehouse.name") or "").strip()
        warehouse_pin = (self.settings.get_raw("shipping.warehouse.pincode") or "").strip()
        warehouse_addr = (self.settings.get_raw("shipping.warehouse.address") or "").strip()
        if not (warehouse_name and warehouse_pin and warehouse_addr):
            logger.info(
                "skip reverse pickup for return=%s: warehouse not configured",
                req.id,
            )
            return

        user = req.user
        customer_addr = ShipmentAddress(
            name=(user.full_name if user and user.full_name else "Customer"),
            phone=((user.phone if user and user.phone else "") or "0000000000"),
            pincode=order.shipping_pincode,
            address=order.shipping_address,
            email=(user.email if user else None),
        )
        warehouse_addr_obj = ShipmentAddress(
            name=warehouse_name, phone="0000000000",
            pincode=warehouse_pin, address=warehouse_addr,
        )

        items: list[CartLine] = []
        for ri in req.items:
            oi = ri.order_item
            if not oi:
                continue
            product = oi.product if hasattr(oi, "product") else None
            items.append(CartLine(
                product_id=oi.product_id,
                quantity=ri.quantity,
                unit_price=Decimal(oi.unit_price),
                weight_grams=(product.weight_grams if product else None) or 200,
            ))
        if not items:
            return

        try:
            result = provider.create_reverse_shipment(
                ReverseShipmentRequest(
                    return_id=req.id,
                    return_reference=f"RET{req.id}",
                    customer=customer_addr,
                    warehouse=warehouse_addr_obj,
                    items=items,
                    declared_value=Decimal(req.refund_amount or 0),
                    original_awb=order.shipping_awb,
                )
            )
        except ShippingProviderError as exc:
            logger.warning(
                "reverse pickup creation failed for return=%s: %s — "
                "approving without an AWB; admin can retry manually.",
                req.id, exc,
            )
            return
        req.reverse_awb = result.awb_number
        logger.info(
            "reverse pickup minted return=%s provider=%s awb=%s",
            req.id, result.provider, result.awb_number,
        )
