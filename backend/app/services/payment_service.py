"""Orchestrates the order + payment lifecycle.

`checkout` builds a PENDING order (reserving stock) and asks the active
provider for a redirect URL. The order's `payment_intent_id` becomes the
merchant transaction id we share with the provider; later webhook / status
calls look the order back up by that column.

State transitions are intentionally narrow:
  PENDING --(payment succeeds)--> PAID
  PENDING --(payment fails or user cancels)--> CANCELLED
Re-running a webhook on an order that's already terminal is a no-op.
"""
from __future__ import annotations

import logging
import secrets
from decimal import Decimal
from typing import Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.integrations.payments import (
    InitiateRequest,
    PaymentStatus,
    get_payment_provider,
    get_provider_for_order,
)
from app.models.coupon import Coupon
from app.models.order import Order, OrderItem, OrderStatus
from app.models.payment_event import PaymentEventType
from app.services.payment_audit import record_payment_event
from app.models.user import User
from app.repositories.coupon_repository import CouponRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.product_repository import ProductRepository
from app.schemas.payment import CheckoutRequest
from app.services.address_service import AddressService, render_address_text, snapshot_of
from app.services.cart_service import CartService
from app.services.cod_service import CodService
from app.services.coupon_service import CouponService
from app.services.payment_methods_service import PaymentMethodsService
from app.services.settings_service import SettingsService
from app.services.shipping_service import ShippingService
from app.services.tax_service import compute_line_tax, quantize_money

logger = logging.getLogger(__name__)


def _new_mtid() -> str:
    # PhonePe limits merchantTransactionId to <=35 alphanumerics — pad ours
    # with a short prefix so it's recognizable in logs.
    return f"ORD{secrets.token_hex(12).upper()}"  # 27 chars


class PaymentService:
    def __init__(self, db: Session):
        self.db = db
        self.orders = OrderRepository(db)
        self.products = ProductRepository(db)
        self.cart = CartService(db)
        self.coupons = CouponRepository(db)

    # ---- public ----

    def checkout(self, user: User, data: CheckoutRequest) -> Tuple[Order, str, str]:
        """Create an order + initiate payment. Returns (order, mtid, redirect_url).

        Two paths depending on `data.payment_method`:
          - 'prepaid' (default): build order, ask provider for a redirect URL,
            order stays PENDING until the webhook lands.
          - 'cod': build order with a COD surcharge added, skip the gateway,
            order transitions straight to PAID on placement (same side-effects
            as the webhook SUCCESS path), redirect URL points to the return
            page so the SPA can read /payments/{mtid}/order and confirm.
        """
        # Resolve the shipping address BEFORE _enforce_cod_availability and
        # _build_order so that data.shipping_pincode and data.shipping_address
        # are populated from the structured source when downstream code reads them.
        # NOTE: _resolve_shipping_address may commit an AddressService.create()
        # call when save_address=True, but the session is clean at this point
        # (no order rows have been added yet), so the early commit is safe.
        snapshot, resolved_address_id = self._resolve_shipping_address(user, data)
        billing_snapshot, billing_addr_id = self._resolve_billing_address(
            user, data, snapshot, resolved_address_id
        )

        method = (data.payment_method or "prepaid").lower()
        order, total = self._build_order(
            user.id,
            data,
            snapshot=snapshot,
            resolved_address_id=resolved_address_id,
            billing_snapshot=billing_snapshot,
            billing_address_id=billing_addr_id,
        )
        mtid = _new_mtid()
        order.payment_intent_id = mtid
        self.orders.add(order)
        # flush so the order has an id we can pass to the provider, but don't
        # commit until we know we'll keep this row.
        self.db.flush()

        currency = (data.currency or order.currency or "INR").upper()
        order.currency = currency
        amount_minor = int((total * 100).to_integral_value())

        if method == "cod":
            # COD: enforce availability server-side (clients can't bypass),
            # require OTP if the admin has the gate on, transition straight
            # to PAID, return the return page so the SPA renders a
            # confirmation immediately.
            self._enforce_cod_availability(user, data)
            self._enforce_cod_otp_if_required(user, data)
            try:
                self._mark_paid(order)
            except Exception:
                self.db.rollback()
                raise
            self.db.commit()
            self.db.refresh(order)
            logger.info(
                "cod checkout user=%s order=%s mtid=%s total=%s balance=%s",
                user.id, order.id, mtid, order.total_amount, order.cod_balance,
            )
            # Fire the same post-paid side effects the gateway webhook would.
            self._send_notification(order, "order_paid")
            self._maybe_auto_push_shipment(order)
            return order, mtid, f"{settings.PAYMENT_RETURN_URL}?mtid={mtid}"

        if method == "split_cod":
            # Split COD: charge only the prepaid portion via the gateway.
            # `cod_balance` was already set in _build_order; the gateway
            # webhook (success path) moves the order to PAID — same code
            # path as a regular prepaid order. The balance is collected
            # by the carrier on delivery via the ShipmentRequest cod_amount
            # hook in ShippingService.
            self._enforce_cod_availability(user, data)
            prepaid_minor = int(
                (Decimal(order.total_amount) - Decimal(order.cod_balance))
                .quantize(Decimal("0.01")) * 100
            )
            if prepaid_minor <= 0:
                # Degenerate case — admin lowered `split_prepaid_amount` between
                # /cod/check and submit. Refuse rather than silently turn it
                # into a regular COD order; the customer should re-pick.
                self.db.rollback()
                raise ValidationError(
                    "Split COD prepaid portion is no longer valid for this "
                    "cart. Please re-select a payment method."
                )
            try:
                provider = get_payment_provider(self.db, data.gateway_code)
                order.gateway_code = provider.name
                initiate = provider.initiate(
                    InitiateRequest(
                        order_id=order.id,
                        user_id=user.id,
                        amount_minor=prepaid_minor,
                        currency=currency,
                        merchant_transaction_id=mtid,
                        return_url=f"{settings.PAYMENT_RETURN_URL}?mtid={mtid}",
                        user_email=user.email,
                    )
                )
                order.payment_provider_ref = initiate.provider_transaction_id
            except Exception:
                self.db.rollback()
                raise
            self.db.commit()
            self.db.refresh(order)
            logger.info(
                "split_cod checkout user=%s order=%s mtid=%s provider=%s prepaid=%s balance=%s",
                user.id, order.id, mtid, provider.name,
                Decimal(prepaid_minor) / 100, order.cod_balance,
            )
            return order, mtid, initiate.redirect_url

        # Prepaid — ask the gateway for a redirect URL and stay PENDING.
        try:
            provider = get_payment_provider(self.db, data.gateway_code)
            order.gateway_code = provider.name
            initiate = provider.initiate(
                InitiateRequest(
                    order_id=order.id,
                    user_id=user.id,
                    amount_minor=amount_minor,
                    currency=currency,
                    merchant_transaction_id=mtid,
                    return_url=f"{settings.PAYMENT_RETURN_URL}?mtid={mtid}",
                    user_email=user.email,
                )
            )
            order.payment_provider_ref = initiate.provider_transaction_id
        except Exception:
            # The provider failed *before* we committed. Roll back the
            # reserved stock so the customer can retry without losing units.
            self.db.rollback()
            raise

        self.db.commit()
        self.db.refresh(order)
        logger.info(
            "checkout user=%s order=%s mtid=%s provider=%s gateway=%s",
            user.id,
            order.id,
            mtid,
            provider.name,
            order.gateway_code,
        )
        return order, mtid, initiate.redirect_url

    def _enforce_cod_otp_if_required(self, user: User, data: CheckoutRequest) -> None:
        """When `cod.require_otp` is on, the customer must have a verified
        OTP marker for the phone they're checking out with. Off by default
        is the *safe* fallback — if Twilio isn't configured we don't want
        to silently block COD."""
        from app.services.cod_otp_service import CodOtpService

        if not SettingsService(self.db).get_bool("cod.require_otp", default=True):
            return
        phone = (data.customer_phone or "").strip()
        if not phone:
            raise ConflictError(
                "Please verify your phone number before placing a COD order."
            )
        ok = CodOtpService(self.db).is_verified(user_id=user.id, phone=phone)
        if not ok:
            raise ConflictError(
                "Please verify your phone number with the OTP before placing "
                "a COD order."
            )
        # Burn the verified marker so a second COD order on the same
        # session requires its own OTP.
        CodOtpService(self.db).consume(user_id=user.id, phone=phone)

    def _enforce_cod_availability(self, user: User, data: CheckoutRequest) -> None:
        """Re-runs the COD gate chain on the server using the trusted cart
        + pincode. Refuses checkout with the *first* reason — the client
        already had a chance to see the full list via /cod/check."""
        result = CodService(self.db).check_availability(
            user=user,
            cart_items=[(line.product_id, line.quantity) for line in data.items],
            destination_pincode=data.shipping_pincode,
        )
        if not result.available:
            first = result.reasons[0] if result.reasons else (
                "Cash on Delivery is not available for this order."
            )
            raise ConflictError(first)

    def handle_webhook(
        self, body: bytes, signature: str | None, gateway_code: str = "phonepe"
    ) -> Order:
        provider = get_payment_provider(self.db, gateway_code)
        if not provider.verify_webhook(body, signature):
            record_payment_event(
                event_type=PaymentEventType.WEBHOOK_SIGNATURE_INVALID,
                gateway_code=gateway_code,
                signature_valid=False,
            )
            raise ForbiddenError("Invalid payment signature.")
        try:
            result = provider.parse_webhook(body)
        except Exception as exc:
            record_payment_event(
                event_type=PaymentEventType.GATEWAY_ERROR,
                gateway_code=gateway_code,
                message=str(exc),
            )
            raise
        record_payment_event(
            event_type=PaymentEventType.WEBHOOK_RECEIVED,
            gateway_code=gateway_code,
            signature_valid=True,
            merchant_transaction_id=result.merchant_transaction_id,
            payment_status=result.status.value if result.status else None,
        )
        order = self._order_for_mtid(result.merchant_transaction_id)
        self._apply_status(order, result.status, gateway_amount_minor=result.amount_minor)
        return order

    def get_status(self, user_id: int, merchant_transaction_id: str) -> Order:
        order = self._order_for_mtid(merchant_transaction_id)
        if order.user_id != user_id:
            raise NotFoundError("Order not found")
        # If the order is still PENDING, poll the provider — covers cases
        # where the webhook hasn't landed yet (the user beat it back to us).
        if order.status == OrderStatus.PENDING:
            try:
                provider = get_provider_for_order(self.db, order)
                result = provider.fetch_status(
                    merchant_transaction_id, order.payment_provider_ref
                )
                record_payment_event(
                    event_type=PaymentEventType.STATUS_POLL,
                    order_id=order.id,
                    merchant_transaction_id=merchant_transaction_id,
                    gateway_code=order.gateway_code,
                    payment_status=result.status.value if result.status else None,
                    provider_ref=result.provider_transaction_id,
                )
                if result.status != PaymentStatus.PENDING:
                    self._apply_status(
                        order, result.status,
                        gateway_amount_minor=result.amount_minor,
                    )
            except Exception as exc:  # don't fail the poll on a flaky provider
                logger.warning("status check failed for %s: %s", merchant_transaction_id, exc)
                record_payment_event(
                    event_type=PaymentEventType.GATEWAY_ERROR,
                    order_id=order.id,
                    merchant_transaction_id=merchant_transaction_id,
                    gateway_code=order.gateway_code,
                    message=str(exc),
                )
        return order

    def mark_mock_decision(self, mtid: str, action: str) -> Order:
        """Dev-only — drives the mock simulator without going through the
        signed-webhook path. Reuses the same `_apply_status` so behavior is
        identical to the real path.

        Authorization is per-order: only an order that was actually routed
        through the mock provider at checkout (``gateway_code == "mock"``) may
        be settled here. We deliberately do NOT consult the deprecated
        PaymentGatewayConfig "active provider" row — the checkout factory no
        longer reads it, so trusting it created a split-brain where a real
        order could be mock-settled or a genuine mock order blocked.
        """
        order = self._order_for_mtid(mtid)
        if (order.gateway_code or "mock").lower() != "mock":
            raise ForbiddenError(
                "This order was not placed through the mock gateway."
            )
        provider = get_payment_provider(self.db, "mock")
        if provider.name != "mock":
            raise ForbiddenError("Mock decisions are disabled.")
        status_ = PaymentStatus.SUCCESS if action == "approve" else PaymentStatus.FAILED
        # Also persist the decision in Redis so a status poll matches.
        body = f'{{"merchant_transaction_id":"{mtid}","action":"{action}"}}'.encode()
        provider.parse_webhook(body)
        self._apply_status(order, status_)
        return order

    def reconcile_pending(
        self, older_than_minutes: int = 30, limit: int = 100
    ) -> dict:
        """Poll the gateway for every stale PENDING order and settle it.

        Selects up to `limit` gateway-routed PENDING orders whose `created_at`
        is older than `older_than_minutes`, oldest first.  For each one it
        calls the provider's fetch_status and, when the status has moved,
        applies the transition via _apply_status.

        Returns a summary dict with keys:
            checked, settled_paid, cancelled, still_pending, errors
        """
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import and_

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)

        stmt = (
            select(Order)
            .where(
                and_(
                    Order.status == OrderStatus.PENDING,
                    Order.created_at < cutoff,
                    Order.gateway_code.isnot(None),
                    Order.payment_method.in_(["prepaid", "split_cod"]),
                )
            )
            .order_by(Order.created_at.asc())
            .limit(limit)
        )
        orders = self.db.execute(stmt).scalars().all()

        checked = 0
        settled_paid = 0
        cancelled = 0
        still_pending = 0
        errors = 0

        for order in orders:
            checked += 1
            try:
                provider = get_provider_for_order(self.db, order)
                result = provider.fetch_status(
                    order.payment_intent_id, order.payment_provider_ref
                )
                if result.status != PaymentStatus.PENDING:
                    self._apply_status(
                        order, result.status,
                        gateway_amount_minor=result.amount_minor,
                    )
                    if result.status == PaymentStatus.SUCCESS:
                        settled_paid += 1
                    else:
                        cancelled += 1
                    outcome = result.status.value
                else:
                    still_pending += 1
                    outcome = PaymentStatus.PENDING.value

                record_payment_event(
                    event_type=PaymentEventType.RECONCILE,
                    order_id=order.id,
                    merchant_transaction_id=order.payment_intent_id,
                    gateway_code=order.gateway_code,
                    payment_status=outcome,
                    provider_ref=result.provider_transaction_id,
                )
            except Exception as exc:
                errors += 1
                logger.warning(
                    "reconcile_pending: error on order=%s mtid=%s: %s",
                    order.id,
                    order.payment_intent_id,
                    exc,
                )
                record_payment_event(
                    event_type=PaymentEventType.GATEWAY_ERROR,
                    order_id=order.id,
                    merchant_transaction_id=order.payment_intent_id,
                    gateway_code=order.gateway_code,
                    message=str(exc),
                )

        logger.info(
            "reconcile_pending: checked=%s paid=%s cancelled=%s pending=%s errors=%s",
            checked, settled_paid, cancelled, still_pending, errors,
        )
        return {
            "checked": checked,
            "settled_paid": settled_paid,
            "cancelled": cancelled,
            "still_pending": still_pending,
            "errors": errors,
        }

    # ---- internals ----

    def _resolve_shipping_address(
        self,
        user: User,
        data: CheckoutRequest,
    ) -> tuple[dict | None, int | None]:
        """Determine the authoritative shipping address for this checkout.

        Precedence (first match wins):
          1. data.address_id  — a saved address the user owns
          2. data.address     — inline AddressCreate (optionally save)
          3. data.shipping_address (legacy free-text) — accepted this release
             for stale SPA bundles; logs a deprecation warning
          4. Nothing provided → ValidationError

        Side-effects when a structured address is resolved:
          - Overwrites data.shipping_pincode with the address pincode so COD
            gate + rate-quote logic always reads the authoritative pin.
          - Overwrites data.shipping_address with render_address_text(snapshot)
            so _build_order's existing ``order.shipping_address = data.shipping_address``
            assignment (line ~269) automatically writes the correct rendered text.
          - Does NOT auto-fill data.customer_phone from the address phone even
            when the field is empty — OTP semantics: the COD OTP was sent to
            whatever phone the user explicitly provided; silently substituting
            the address phone would allow bypassing the OTP gate.

        Returns (snapshot_dict | None, address_id | None).
        Legacy path returns (None, None) — _build_order skips snapshot columns.
        """
        if data.address_id is not None:
            addr = AddressService(self.db).get_owned(user.id, data.address_id)
            snap = snapshot_of(addr)
            data.shipping_pincode = snap["pincode"]
            data.shipping_address = render_address_text(snap)
            return snap, addr.id

        if data.address is not None:
            snap = snapshot_of(data.address)
            saved_id: int | None = None

            if data.save_address:
                try:
                    saved = AddressService(self.db).create(user.id, data.address)
                    saved_id = saved.id
                except ValidationError as exc:
                    # Address cap full — log and continue without saving.
                    # Never fail a sale over address-book housekeeping.
                    logger.warning(
                        "save_address skipped for user=%s: %s", user.id, exc.message
                    )

            data.shipping_pincode = snap["pincode"]
            data.shipping_address = render_address_text(snap)
            return snap, saved_id

        if data.shipping_address:
            # Legacy free-text path — accepted this release for stale SPA
            # bundles. Remove next release.
            logger.warning(
                "checkout user=%s used deprecated free-text shipping_address; "
                "migrate to address_id or address",
                user.id,
            )
            return None, None

        raise ValidationError("A shipping address is required")

    def _resolve_billing_address(
        self,
        user: User,
        data: CheckoutRequest,
        shipping_snapshot: dict | None,
        shipping_address_id: int | None,
    ) -> tuple[dict | None, int | None]:
        """Determine the billing address for this checkout.

        Precedence (first match wins):
          1. data.billing_address_id — a saved address the user owns.
             Calls AddressService.get_owned; NotFoundError propagates (404).
             No existence leak: the error message is the same for missing
             and other-user addresses.
          2. data.billing_address — inline AddressCreate. Snapshot is built
             in memory; never written to the address book.
          3. Neither supplied — billing copies the resolved shipping snapshot.
             A fresh dict() copy is returned so the two JSON columns never
             share a mutable object reference.

        Legacy free-text shipping path (shipping_snapshot is None) with no
        explicit billing → returns (None, None); billing stays NULL on the
        order (nothing structured to copy).

        No session writes, no commits — callers assume the session is clean
        before order rows are appended.
        """
        if data.billing_address_id is not None:
            addr = AddressService(self.db).get_owned(user.id, data.billing_address_id)
            snap = snapshot_of(addr)
            return snap, addr.id

        if data.billing_address is not None:
            snap = snapshot_of(data.billing_address)
            return snap, None

        # Default: copy shipping.
        if shipping_snapshot is not None:
            return dict(shipping_snapshot), shipping_address_id

        # Legacy free-text shipping with no explicit billing — nothing to copy.
        return None, None

    def _build_order(
        self,
        user_id: int,
        data: CheckoutRequest,
        *,
        snapshot: dict | None = None,
        resolved_address_id: int | None = None,
        billing_snapshot: dict | None = None,
        billing_address_id: int | None = None,
    ) -> tuple[Order, Decimal]:
        order = Order(user_id=user_id, shipping_address=data.shipping_address)
        subtotal = Decimal("0.00")
        tax_amount = Decimal("0.00")
        for line in data.items:
            product = self.products.get(line.product_id)
            if not product:
                raise NotFoundError(f"Product {line.product_id} not found")
            if product.stock < line.quantity:
                raise ConflictError(f"Insufficient stock for {product.sku}")
            self.products.decrement_stock(product, line.quantity)
            order.items.append(
                OrderItem(
                    product_id=product.id,
                    quantity=line.quantity,
                    unit_price=product.price,
                )
            )
            subtotal += quantize_money(product.price * line.quantity)
            tax_amount += compute_line_tax(product.price, line.quantity, list(product.taxes))

        discount_amount = Decimal("0.00")
        coupon_code: str | None = None
        if data.coupon_code:
            # Validate raises ValidationError if expired / exhausted / below min.
            coupon, discount_amount = CouponService(self.db).validate(
                data.coupon_code.strip().upper(), user_id, subtotal
            )
            coupon_code = coupon.code

        # Shipping cost. Computed authoritatively here (not trusted from the
        # client) so the customer can't tamper with the cart summary's quote.
        # Empty pincode + non-`none` provider falls through to 0 — the admin
        # picks up the slack manually.
        shipping_amount = Decimal("0.00")
        shipping_pincode = (data.shipping_pincode or "").strip() or None
        if shipping_pincode:
            try:
                quote = ShippingService(self.db).rate_quote(
                    destination_pincode=shipping_pincode,
                    cart_items=[(line.product_id, line.quantity) for line in data.items],
                )
                shipping_amount = quantize_money(Decimal(quote.amount))
            except Exception as exc:  # noqa: BLE001
                # Don't let a flaky carrier kill the checkout. Log and ship
                # the order with zero shipping; admin will reconcile.
                logger.warning(
                    "rate_quote failed at checkout for pin=%s: %s",
                    shipping_pincode, exc,
                )

        # Free-shipping threshold override (Phase 11 polish).
        # When the threshold setting is non-zero AND the cart subtotal clears
        # it AND the order is gateway-routed (prepaid or split_cod), we wipe
        # the shipping fee to zero. Full-COD orders never qualify — the
        # incentive is to nudge customers off COD.
        method_pre_check = (data.payment_method or "prepaid").lower()
        free_threshold_raw = SettingsService(self.db).get_raw(
            "shipping.free_threshold"
        ) or "0"
        try:
            free_threshold = Decimal(free_threshold_raw)
        except Exception:  # noqa: BLE001
            free_threshold = Decimal("0")
        if (
            free_threshold > 0
            and subtotal >= free_threshold
            and method_pre_check in ("prepaid", "split_cod")
        ):
            shipping_amount = Decimal("0.00")

        # COD surcharge — flat fee snapshotted from settings. Applies to
        # both full COD and Split COD (the carrier still has a COD leg);
        # prepaid orders carry 0.
        method = (data.payment_method or "prepaid").lower()
        cod_surcharge = Decimal("0.00")
        cod_balance = Decimal("0.00")
        if method in ("cod", "split_cod"):
            cod_surcharge = CodService(self.db).surcharge_amount()

        # Per-instrument discount (e.g. "Extra 5% off on UPI"). Only applies
        # to methods that touch the gateway (prepaid + split_cod). The
        # discount is computed on `subtotal + tax` — shipping and the COD
        # surcharge are excluded so the customer doesn't get a "discount
        # on a shipping fee" optical illusion.
        payment_instrument: str | None = None
        payment_discount = Decimal("0.00")
        if method in ("prepaid", "split_cod") and data.payment_instrument:
            methods = PaymentMethodsService(self.db)
            if not methods.is_enabled(data.payment_instrument):
                raise ValidationError(
                    f"Payment instrument {data.payment_instrument!r} isn't "
                    "available right now. Please pick another."
                )
            payment_instrument = data.payment_instrument
            payment_discount = methods.discount_for(
                payment_instrument, subtotal + tax_amount
            )

        total = quantize_money(
            subtotal + tax_amount + shipping_amount + cod_surcharge
            - discount_amount - payment_discount
        )
        if method == "cod":
            cod_balance = total
        elif method == "split_cod":
            # Snapshot the prepaid portion from settings at order-build
            # time so the customer can't be over-charged later if an admin
            # bumps it. cod_balance = total − prepaid.
            prepaid_portion = CodService(self.db).split_prepaid_for(total)
            if prepaid_portion <= 0 or prepaid_portion >= total:
                # Degenerate. The picker shouldn't have offered Split COD,
                # but in case the cart shape changed between picker and
                # submit, fall back to full COD math (carrier collects
                # everything).
                cod_balance = total
            else:
                cod_balance = (total - prepaid_portion).quantize(Decimal("0.01"))

        order.subtotal = subtotal
        order.tax_amount = tax_amount
        order.discount_amount = discount_amount
        order.shipping_amount = shipping_amount
        order.shipping_pincode = shipping_pincode
        order.coupon_code = coupon_code
        order.payment_method = method
        order.payment_instrument = payment_instrument
        order.payment_discount_amount = payment_discount
        order.cod_surcharge_amount = cod_surcharge
        order.cod_balance = cod_balance
        order.total_amount = total
        order.status = OrderStatus.PENDING
        # Structured address path: store frozen snapshot + provenance FK.
        # shipping_address (text) and shipping_pincode already carry the
        # correct values because _resolve_shipping_address overwrote
        # data.shipping_address / data.shipping_pincode before _build_order
        # was called, so the existing assignments above are authoritative.
        if snapshot is not None:
            order.shipping_address_snapshot = snapshot
            order.shipping_address_id = resolved_address_id
        # Billing address — stored independently of the shipping block so that
        # explicit billing works even on the legacy free-text shipping path.
        if billing_snapshot is not None:
            order.billing_address_snapshot = billing_snapshot
            order.billing_address_id = billing_address_id
        return order, total

    def _order_for_mtid(self, mtid: str) -> Order:
        stmt = select(Order).where(Order.payment_intent_id == mtid)
        order = self.db.execute(stmt).scalar_one_or_none()
        if not order:
            raise NotFoundError("Order not found for transaction.")
        return order

    def _apply_status(
        self,
        order: Order,
        payment_status: PaymentStatus,
        *,
        gateway_amount_minor: int | None = None,
    ) -> None:
        # Idempotent: only PENDING orders move. Webhooks can fire twice.
        if order.status != OrderStatus.PENDING:
            return
        notify_paid = False
        if payment_status == PaymentStatus.SUCCESS:
            # Verify the gateway-reported amount matches what we expected to
            # charge. A mismatch (e.g. amount tampering or replay from a
            # different order) must not result in marking the order PAID.
            if gateway_amount_minor is not None:
                expected_minor = int(
                    (Decimal(str(order.total_amount)) * 100).to_integral_value()
                )
                if gateway_amount_minor != expected_minor:
                    logger.error(
                        "amount mismatch on order %s: expected %s paise, "
                        "gateway reported %s paise — leaving PENDING for review",
                        order.id,
                        expected_minor,
                        gateway_amount_minor,
                    )
                    record_payment_event(
                        event_type=PaymentEventType.AMOUNT_MISMATCH,
                        order_id=order.id,
                        merchant_transaction_id=order.payment_intent_id,
                        gateway_code=order.gateway_code,
                        payment_status=PaymentStatus.SUCCESS.value,
                        amount_reported_minor=gateway_amount_minor,
                        amount_expected_minor=expected_minor,
                    )
                    self.db.commit()
                    return
            self._mark_paid(order)
            notify_paid = True
        elif payment_status == PaymentStatus.FAILED:
            order.status = OrderStatus.CANCELLED
            self._restore_stock(order)
        # PENDING -> no change.
        self.db.commit()

        # Record the real transition (SUCCESS→PAID or FAILED→CANCELLED).
        # Skip PENDING since no state change happened.
        if payment_status in (PaymentStatus.SUCCESS, PaymentStatus.FAILED):
            record_payment_event(
                event_type=PaymentEventType.STATUS_APPLIED,
                order_id=order.id,
                merchant_transaction_id=order.payment_intent_id,
                gateway_code=order.gateway_code,
                payment_status=payment_status.value,
            )

        # Notifications go AFTER commit so the customer never gets a
        # "your order is paid" email for a row that didn't actually save.
        if notify_paid:
            self._send_notification(order, "order_paid")
            self._maybe_auto_push_shipment(order)

    def _mark_paid(self, order: Order) -> None:
        """Common 'order is now committed' side effects — runs for both the
        gateway SUCCESS branch and the COD-checkout path. Sets paid_at,
        records coupon usage, awards loyalty, completes referrals, clears
        the cart. Caller is responsible for committing + dispatching the
        notification afterwards."""
        from datetime import datetime, timezone

        order.status = OrderStatus.PAID
        order.paid_at = datetime.now(timezone.utc)
        self._record_coupon_usage(order)
        self._award_loyalty_points(order)
        self._complete_referral(order)
        self._clear_cart(order.user_id)

    def _send_notification(self, order: Order, event_name: str) -> None:
        """Fan out to email + SMS. Wrapped so a flaky downstream never breaks
        the payment commit."""
        from app.services.notifications import NotificationEvent, NotificationService

        try:
            NotificationService(self.db).notify(order, NotificationEvent(event_name))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "notification dispatch failed for order %s event %s: %s",
                order.id,
                event_name,
                exc,
            )

    def _maybe_auto_push_shipment(self, order: Order) -> None:
        """If the admin has flipped `shipping.auto_create_on_paid=true`, push
        this freshly-paid order to the carrier. Errors are logged and dropped
        — never fail the payment commit because shipping is offline."""
        from app.services.settings_service import SettingsService
        from app.services.shipping_service import ShippingService

        if not SettingsService(self.db).get_bool(
            "shipping.auto_create_on_paid", default=False
        ):
            return
        try:
            ShippingService(self.db).create_shipment_for_order(order.id)
            self.db.commit()
        except Exception as exc:  # noqa: BLE001
            self.db.rollback()
            logger.warning(
                "auto-push to carrier failed for order %s: %s", order.id, exc
            )

    def _award_loyalty_points(self, order: Order) -> None:
        """Award purchase points after payment succeeds. Idempotent via the
        unique (reason, ref_type, ref_id) index — webhook re-runs are safe."""
        # Lazy import to keep loyalty optional / avoid a cycle.
        from app.services.loyalty_service import LoyaltyService

        try:
            LoyaltyService(self.db).award_for_order(order)
        except Exception as exc:
            # Never fail the payment commit on a points hiccup. The reconcile
            # script can backfill if anything was missed.
            logger.warning(
                "loyalty award failed for order %s: %s", order.id, exc
            )

    def _complete_referral(self, order: Order) -> None:
        """If this order's user was referred, complete the referral and mint
        the referrer's reward coupon. Idempotent — only PENDING referrals move,
        so re-running the webhook is safe."""
        from app.services.referral_service import ReferralService

        try:
            ReferralService(self.db).complete_referral_for_user(order.user_id, order.id)
        except Exception as exc:
            logger.warning(
                "referral completion failed for order %s: %s", order.id, exc
            )

    def _record_coupon_usage(self, order: Order) -> None:
        if not order.coupon_code or order.discount_amount <= 0:
            return
        coupon = self.coupons.get_by_code(order.coupon_code)
        if not coupon:
            logger.warning(
                "order %s snapshotted coupon %s no longer exists; skipping usage log",
                order.id,
                order.coupon_code,
            )
            return
        CouponService(self.db).record_usage(
            coupon, order.user_id, order.id, Decimal(order.discount_amount)
        )

    def _clear_cart(self, user_id: int) -> None:
        # The previous create-order flow left the cart populated. After a
        # successful payment we always clear it — that's the user's mental
        # model when checkout succeeds.
        try:
            self.cart.clear(user_id)
        except Exception as exc:  # cart wipe must not roll back the payment
            logger.warning("post-payment cart clear failed for user=%s: %s", user_id, exc)

    def _restore_stock(self, order: Order) -> None:
        # We decremented stock at checkout to *reserve* it. On failure, give
        # it back so the next shopper can buy the unit.
        for item in order.items:
            product = self.products.get(item.product_id)
            if product:
                product.stock += item.quantity
