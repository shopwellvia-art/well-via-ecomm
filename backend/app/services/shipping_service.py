"""Shipping orchestration on top of the provider abstraction.

Phase 2 surface area:
  - `serviceability(pincode)` — wraps the provider call with a Redis cache
    keyed by (provider name, pincode). TTL is admin-configurable via
    `shipping.serviceability_cache_minutes` so the carrier rate limit (4500
    req / 5 min for Delhivery) is easy to stay under.

The cache also stores *negative* results (non-serviceable pincodes) — the
whole point of caching is to keep us from hammering the carrier for known
answers, and "no, you don't deliver to 999000" is just as expensive a
roundtrip as the positive one.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import redis
from sqlalchemy.orm import Session

from app.core.config import settings as env_settings
from app.db.redis import get_redis
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.integrations.shipping import (
    CartLine,
    PickupRequest,
    PickupResult,
    RateQuote,
    RateQuoteRequest,
    ServiceabilityResult,
    ShipmentAddress,
    ShipmentRequest,
    ShipmentResult,
    ShippingProviderError,
    TrackingStatus,
    TrackingUpdate,
    get_shipping_provider,
)
from app.models.order import Order, OrderStatus
from app.models.product import Product
from app.models.shipment import ShipmentStatus
from app.repositories.order_repository import OrderRepository
from app.repositories.product_repository import ProductRepository
from app.services import order_sync
from app.services.settings_service import SettingsService

# Maps the carrier-agnostic TrackingStatus onto the normalized ShipmentStatus
# so the shipments table keeps the finer granularity the order status collapses
# away (order.status only has SHIPPED for in-transit / out-for-delivery).
_SHIPMENT_STATUS_FOR_TRACKING: dict[TrackingStatus, ShipmentStatus] = {
    TrackingStatus.PICKED_UP: ShipmentStatus.SHIPPED,
    TrackingStatus.IN_TRANSIT: ShipmentStatus.IN_TRANSIT,
    TrackingStatus.OUT_FOR_DELIVERY: ShipmentStatus.OUT_FOR_DELIVERY,
    TrackingStatus.DELIVERED: ShipmentStatus.DELIVERED,
    TrackingStatus.FAILED: ShipmentStatus.DELIVERY_FAILED,
    TrackingStatus.RETURNED: ShipmentStatus.RTO_DELIVERED,
    TrackingStatus.CANCELLED: ShipmentStatus.CANCELLED,
}

logger = logging.getLogger(__name__)

_DEFAULT_TTL_MIN = 60
# Used when a product row doesn't have a weight_grams value. 200g is the
# safe Indian-ecommerce default for "no idea, probably a small parcel".
_FALLBACK_WEIGHT_GRAMS = 200


def _isoformat(value: datetime | None) -> str | None:
    """ISO format with explicit UTC normalization so dedup keys are stable
    regardless of how the carrier formatted its timestamps."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _cache_key(provider: str, pincode: str) -> str:
    return f"shipping:serviceability:{provider}:{pincode}"


class ShippingService:
    def __init__(self, db: Session):
        self.db = db
        self.provider = get_shipping_provider(db)
        self.settings = SettingsService(db)
        self.products = ProductRepository(db)
        self.orders = OrderRepository(db)
        self._redis = get_redis()

    # ---- serviceability ----

    def serviceability(self, pincode: str) -> ServiceabilityResult:
        clean = (pincode or "").strip()
        if not clean.isdigit() or not (3 <= len(clean) <= 10):
            # Don't pretend to call the carrier for obviously-malformed input.
            raise ShippingProviderError("Pincode must be 3–10 digits.")

        cached = self._read_cache(clean)
        if cached is not None:
            return cached

        result = self.provider.serviceability(clean)
        self._write_cache(clean, result)
        return result

    # ---- rate quote ----

    def rate_quote(
        self,
        *,
        destination_pincode: str,
        cart_items: list[tuple[int, int]],  # [(product_id, quantity), ...]
    ) -> RateQuote:
        """Build a `RateQuoteRequest` from the cart and delegate to the provider.

        Weights and prices come from the products table — we never trust
        client-supplied weight/price for shipping math.
        """
        dest = (destination_pincode or "").strip()
        if not dest.isdigit() or not (3 <= len(dest) <= 10):
            raise ValidationError("Pincode must be 3–10 digits.")
        if not cart_items:
            raise ValidationError("Cart is empty.")

        origin = (self.settings.get_raw("shipping.warehouse.pincode") or "").strip()
        if not origin:
            # When no warehouse pin is configured we can't rate-quote — mock
            # synthesizes one anyway, but Delhivery would reject the call.
            # Pick the destination as origin so mock still works; real
            # providers will raise their own clearer error.
            origin = dest

        lines: list[CartLine] = []
        for product_id, qty in cart_items:
            product = self.products.get(product_id)
            if not product:
                raise NotFoundError(f"Product {product_id} not found")
            lines.append(
                CartLine(
                    product_id=product.id,
                    quantity=qty,
                    unit_price=Decimal(product.price),
                    weight_grams=product.weight_grams or _FALLBACK_WEIGHT_GRAMS,
                )
            )

        return self.provider.rate_quote(
            RateQuoteRequest(
                origin_pincode=origin,
                destination_pincode=dest,
                items=lines,
            )
        )

    # ---- shipment creation ----

    def create_shipment_for_order(self, order_id: int) -> Order:
        """Push an order to the active carrier and persist the AWB.

        Refuses if:
          - Order isn't found
          - Order is not in PAID (we don't ship unpaid orders, and shipping
            a CANCELLED order would be a bug)
          - Order already has an AWB (idempotency — re-clicking the button
            shouldn't double-charge the merchant by creating two shipments)
          - Pickup warehouse settings aren't configured
        """
        order = self.orders.get(order_id)
        if not order:
            raise NotFoundError("Order not found")
        if order.shipping_awb:
            raise ConflictError(
                f"Order #{order.id} already has AWB {order.shipping_awb}."
            )
        if order.status != OrderStatus.PAID:
            raise ConflictError(
                f"Order #{order.id} is {order.status.value}; only PAID orders "
                "can be pushed to the carrier."
            )
        if not order.shipping_address or not order.shipping_pincode:
            raise ConflictError(
                f"Order #{order.id} is missing a destination pin — can't "
                "create a shipment without one."
            )

        pickup = self._pickup_address()
        consignee = self._consignee_address(order)
        items = [
            CartLine(
                product_id=item.product_id,
                quantity=item.quantity,
                unit_price=Decimal(item.unit_price),
                weight_grams=(
                    item.product.weight_grams
                    if getattr(item, "product", None)
                    else None
                ) or 200,
            )
            for item in order.items
        ]

        # When the order is COD (or has a Split COD balance > 0), the
        # carrier collects on delivery. payment_mode + cod_amount drive the
        # carrier's COD vs Prepaid manifest path.
        cod_balance = Decimal(order.cod_balance or 0)
        is_cod_shipment = cod_balance > 0
        req = ShipmentRequest(
            order_id=order.id,
            order_reference=f"ORD{order.id}",
            consignee=consignee,
            pickup=pickup,
            items=items,
            declared_value=Decimal(order.total_amount),
            cod_amount=(cod_balance if is_cod_shipment else None),
            payment_mode=("COD" if is_cod_shipment else "Prepaid"),
        )

        result: ShipmentResult = self.provider.create_shipment(req)
        order.shipping_provider = result.provider
        order.shipping_awb = result.awb_number
        order.shipping_label_url = result.label_url
        order.shipment_created_at = datetime.now(timezone.utc)
        # Mirror into the normalized shipments table.
        order_sync.sync_shipment_from_order(
            order,
            raw={
                "provider": result.provider,
                "awb_number": result.awb_number,
                "label_url": result.label_url,
            },
        )
        self.db.flush()
        logger.info(
            "shipment created order=%s provider=%s awb=%s",
            order.id, result.provider, result.awb_number,
        )
        return order

    def _pickup_address(self) -> ShipmentAddress:
        name = (self.settings.get_raw("shipping.warehouse.name") or "").strip()
        pin = (self.settings.get_raw("shipping.warehouse.pincode") or "").strip()
        addr = (self.settings.get_raw("shipping.warehouse.address") or "").strip()
        if not (name and pin and addr):
            raise ConflictError(
                "Pickup warehouse isn't fully configured. Set the name, "
                "pincode, and address in Admin → Settings → Shipping."
            )
        return ShipmentAddress(
            name=name,
            phone=(self.settings.get_raw("shipping.warehouse.phone") or "0000000000").strip() or "0000000000",
            pincode=pin,
            address=addr,
            city=(self.settings.get_raw("shipping.warehouse.city") or None),
            state=(self.settings.get_raw("shipping.warehouse.state") or None),
        )

    # ---- pickup ----

    def schedule_pickup_for_order(
        self,
        order_id: int,
        *,
        pickup_date: datetime,
        expected_package_count: int = 1,
    ) -> Order:
        """Ask the carrier to collect this order's package.

        Refuses missing orders, orders without a shipment yet, and orders
        that already have a pickup scheduled. Date is normalized to UTC so
        comparisons against `now()` are deterministic regardless of how the
        admin's browser sent it.
        """
        order = self.orders.get(order_id)
        if not order:
            raise NotFoundError("Order not found")
        if not order.shipping_awb:
            raise ConflictError(
                f"Order #{order.id} doesn't have a shipment yet — push it to "
                "the carrier first."
            )
        if order.pickup_id:
            raise ConflictError(
                f"Order #{order.id} already has pickup {order.pickup_id} "
                "scheduled."
            )

        # Normalize the requested date. Carriers reject past/far-future dates;
        # we enforce "today through 7 days out" so admins get a clear error.
        if pickup_date.tzinfo is None:
            pickup_date = pickup_date.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if pickup_date.date() < now.date():
            raise ValidationError("Pickup date can't be in the past.")
        if pickup_date.date() > (now + timedelta(days=7)).date():
            raise ValidationError("Pickup date must be within the next 7 days.")

        result: PickupResult = self.provider.schedule_pickup(
            PickupRequest(
                pickup_location_name=(
                    self.settings.get_raw("shipping.warehouse.name") or ""
                ).strip(),
                pickup_date=pickup_date,
                expected_package_count=expected_package_count,
            )
        )
        order.pickup_id = result.pickup_id
        order.pickup_scheduled_for = result.scheduled_for
        order_sync.set_shipment_status(
            order, ShipmentStatus.PICKUP_SCHEDULED, when=result.scheduled_for
        )
        self.db.flush()
        logger.info(
            "pickup scheduled order=%s pickup_id=%s for=%s",
            order.id, result.pickup_id, result.scheduled_for,
        )
        return order

    # ---- tracking ----

    # Maps the (normalized, carrier-agnostic) TrackingStatus to the order's
    # high-level status. Several carrier states collapse into SHIPPED for the
    # customer's mental model. We deliberately do NOT map CANCELLED here —
    # carrier-cancellation always needs admin review.
    _ORDER_STATUS_FOR_TRACKING: dict[TrackingStatus, OrderStatus] = {
        TrackingStatus.PICKED_UP: OrderStatus.SHIPPED,
        TrackingStatus.IN_TRANSIT: OrderStatus.SHIPPED,
        TrackingStatus.OUT_FOR_DELIVERY: OrderStatus.SHIPPED,
        TrackingStatus.DELIVERED: OrderStatus.DELIVERED,
    }

    def apply_tracking_update(self, update: TrackingUpdate) -> Order | None:
        """Persist a tracking update to whichever order owns the AWB.

        Returns the affected order, or None if no order matches (e.g. a
        webhook for a shipment that belongs to a different store sharing
        the carrier account). Idempotent: re-applying the same event is a
        no-op via (status, occurred_at) dedup.
        """
        order = self._find_order_by_awb(update.awb_number)
        if not order:
            logger.info(
                "tracking update for unknown awb=%s status=%s — ignored",
                update.awb_number, update.status,
            )
            return None

        events = list(order.tracking_events or [])
        seen: set[tuple[str, str]] = {
            (e.get("status"), e.get("occurred_at")) for e in events
        }
        # The provider's `events` may contain history; the top-level
        # `status` + `occurred_at` is always the latest. Merge both.
        candidates: list[dict] = []
        for evt in (update.events or []):
            candidates.append({
                "status": evt.status.value,
                "occurred_at": _isoformat(evt.occurred_at),
                "location": evt.location,
                "note": evt.note,
            })
        candidates.append({
            "status": update.status.value,
            "occurred_at": _isoformat(update.occurred_at),
            "location": None,
            "note": None,
        })

        appended = 0
        for c in candidates:
            key = (c["status"], c["occurred_at"])
            if key in seen:
                continue
            seen.add(key)
            events.append(c)
            appended += 1

        if not appended:
            return order  # all events already on file

        # Keep events sorted by time so the UI can render top-down.
        events.sort(key=lambda e: e.get("occurred_at") or "")
        order.tracking_events = events
        # last_tracking_at = newest event timestamp across the merged log.
        # Without this guard, a late webhook with an older occurred_at would
        # roll the "last updated" stamp backwards.
        newest_iso = events[-1].get("occurred_at") if events else None
        if newest_iso:
            from datetime import datetime as _dt
            try:
                order.last_tracking_at = _dt.fromisoformat(newest_iso)
            except ValueError:
                order.last_tracking_at = update.occurred_at
        else:
            order.last_tracking_at = update.occurred_at
        # Mirror the AWB into the customer-visible tracking_number field so
        # the storefront's "track your package" widget keeps working without
        # extra wiring.
        if not order.tracking_number and order.shipping_awb:
            order.tracking_number = order.shipping_awb
        if not order.carrier and order.shipping_provider:
            order.carrier = order.shipping_provider.capitalize()

        # High-level transition. Only ever moves forward (PAID → SHIPPED →
        # DELIVERED) — once an order is DELIVERED we don't unwind it from
        # a late "in transit" event.
        new_status = self._ORDER_STATUS_FOR_TRACKING.get(update.status)
        notify_event: str | None = None
        if new_status and self._is_forward_transition(order.status, new_status):
            order.status = new_status
            if new_status == OrderStatus.SHIPPED:
                if not order.shipped_at:
                    order.shipped_at = update.occurred_at
                notify_event = "order_shipped"
            elif new_status == OrderStatus.DELIVERED:
                if not order.delivered_at:
                    order.delivered_at = update.occurred_at
                notify_event = "order_delivered"

        # Mirror into the normalized shipments table with the finer carrier
        # status, and settle the COD leg once the parcel is delivered.
        shipment_status = _SHIPMENT_STATUS_FOR_TRACKING.get(update.status)
        if shipment_status is not None:
            order_sync.sync_shipment_from_order(order)
            order_sync.set_shipment_status(
                order, shipment_status, when=update.occurred_at
            )
            if shipment_status == ShipmentStatus.DELIVERED:
                order_sync.mark_cod_collected(order, when=update.occurred_at)

        self.db.flush()
        logger.info(
            "tracking applied order=%s awb=%s status=%s appended=%d",
            order.id, update.awb_number, update.status, appended,
        )
        # Notifications are best-effort and must never bubble up — a failing
        # email shouldn't make the webhook 500 (carriers retry indefinitely).
        if notify_event:
            try:
                from app.services.notifications import (
                    NotificationEvent,
                    NotificationService,
                )

                NotificationService(self.db).notify(
                    order, NotificationEvent(notify_event)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "tracking notification %s failed for order %s: %s",
                    notify_event, order.id, exc,
                )
        return order

    def sync_tracking_for_order(self, order_id: int) -> Order:
        """Admin button — polls the carrier for the current state and
        applies whatever's new. Useful when webhooks are misconfigured or
        we missed one."""
        order = self.orders.get(order_id)
        if not order:
            raise NotFoundError("Order not found")
        if not order.shipping_awb:
            raise ConflictError(
                f"Order #{order.id} hasn't been pushed to the carrier yet."
            )
        update = self.provider.fetch_tracking(order.shipping_awb)
        applied = self.apply_tracking_update(update)
        return applied or order

    def cancel_shipment_for_order(self, order_id: int) -> Order:
        """Cancel the carrier consignment for an order (carrier-side only; does
        NOT change order.status — that's a separate admin refund/cancel flow)."""
        order = self.orders.get(order_id)
        if not order:
            raise NotFoundError("Order not found")
        if not order.shipping_awb:
            raise ConflictError(f"Order #{order.id} has no shipment to cancel.")
        cancel = getattr(self.provider, "cancel_shipment", None)
        if not callable(cancel):
            raise ConflictError(
                f"The active carrier ({self.provider.name}) doesn't support shipment cancellation."
            )
        cancel(order.shipping_awb)
        order_sync.set_shipment_status(order, ShipmentStatus.CANCELLED, when=datetime.now(timezone.utc))
        self.db.flush()
        logger.info("shipment cancelled order=%s awb=%s provider=%s", order.id, order.shipping_awb, self.provider.name)
        return order

    @staticmethod
    def _is_forward_transition(current: OrderStatus, target: OrderStatus) -> bool:
        """Only allow PAID→SHIPPED, PAID→DELIVERED (rare but possible if the
        webhook arrives out of order), and SHIPPED→DELIVERED. Anything else
        is a no-op — late events never roll the order backwards or out of
        terminal states like CANCELLED/REFUNDED."""
        if current == target:
            return False
        order_rank = {
            OrderStatus.PENDING: 0,
            OrderStatus.PAID: 1,
            OrderStatus.SHIPPED: 2,
            OrderStatus.DELIVERED: 3,
        }
        # CANCELLED / REFUNDED aren't in the rank → late tracking can't move
        # them. That's intentional.
        if current not in order_rank or target not in order_rank:
            return False
        return order_rank[target] > order_rank[current]

    def _find_order_by_awb(self, awb_number: str) -> Order | None:
        from sqlalchemy import select

        if not awb_number:
            return None
        stmt = select(Order).where(Order.shipping_awb == awb_number).limit(1)
        return self.db.execute(stmt).scalar_one_or_none()

    # ---- label ----

    def label_pdf_for_order(self, order_id: int) -> tuple[bytes, str]:
        """Return (pdf_bytes, filename). Raises if the order has no shipment."""
        order = self.orders.get(order_id)
        if not order:
            raise NotFoundError("Order not found")
        if not order.shipping_awb:
            raise ConflictError(
                f"Order #{order.id} doesn't have a shipment yet."
            )
        pdf = self.provider.label_pdf(order.shipping_awb)
        filename = f"label-order-{order.id}-{order.shipping_awb}.pdf"
        return pdf, filename

    def local_label_pdf_for_order(self, order_id: int) -> tuple[bytes, str]:
        """Render an in-house 4x6 shipping label from our own order data.

        Unlike `label_pdf_for_order` (which fetches the carrier's official
        label and needs a live AWB), this works for ANY order — useful as a
        preview/fallback and with the mock provider. The barcode encodes the
        AWB when present, otherwise the order number.
        """
        from app.services.shipping_label_pdf import LabelData, render_label_pdf

        order = self.orders.get_with_items(order_id)
        if not order:
            raise NotFoundError("Order not found")

        consignee = self._consignee_address(order)
        store_name = (
            self.settings.get_raw("store.name")
            or self.settings.get_raw("branding.store_name")
            or "Shipping Label"
        )
        from_address = ", ".join(
            b
            for b in (
                self.settings.get_raw("shipping.warehouse.address"),
                self.settings.get_raw("shipping.warehouse.city"),
                self.settings.get_raw("shipping.warehouse.state"),
            )
            if b
        ) or "-"

        cod = Decimal(order.cod_balance or 0) > 0
        currency = order.currency or "INR"
        symbol = "Rs." if currency == "INR" else f"{currency} "
        payment_label = (
            f"COD {symbol}{Decimal(order.cod_balance or 0):,.2f}" if cod else "PREPAID"
        )

        pieces = 0
        weight_grams = 0
        for item in order.items:
            pieces += item.quantity
            product = getattr(item, "product", None)
            grams = product.weight_grams if product and product.weight_grams else 200
            weight_grams += grams * item.quantity

        data = LabelData(
            store_name=store_name,
            order_number=order.order_number or f"ORD{order.id}",
            awb=order.shipping_awb,
            created_at=order.created_at,
            payment_label=payment_label,
            cod=cod,
            pieces=pieces or 1,
            weight_grams=weight_grams,
            to_name=consignee.name,
            to_address=consignee.address,
            to_city=consignee.city,
            to_state=consignee.state,
            to_pincode=consignee.pincode,
            to_phone=consignee.phone,
            from_name=self.settings.get_raw("shipping.warehouse.name") or store_name,
            from_address=from_address,
            from_pincode=self.settings.get_raw("shipping.warehouse.pincode"),
            from_phone=self.settings.get_raw("shipping.warehouse.phone"),
        )
        pdf = render_label_pdf(data)
        return pdf, f"label-order-{order.id}.pdf"

    # ---- internals (continued) ----

    def _consignee_address(self, order: Order) -> ShipmentAddress:
        snap = order.shipping_address_snapshot or {}
        user = order.user
        name = (snap.get("full_name") or (user.full_name if user and user.full_name else None) or "Customer").strip()
        phone = (snap.get("phone") or (user.phone if user and user.phone else None) or "0000000000").strip() or "0000000000"
        addr = ", ".join(p for p in [snap.get("line1"), snap.get("line2"), snap.get("landmark")] if p) or (order.shipping_address or "")
        return ShipmentAddress(
            name=name,
            phone=phone,
            pincode=(snap.get("pincode") or order.shipping_pincode or ""),
            address=addr,
            city=(snap.get("city") or None),
            state=(snap.get("state") or None),
            email=(snap.get("email") or (user.email if user else None)),
        )

    # ---- cache helpers ----

    def _ttl_seconds(self) -> int:
        minutes = self.settings.get_int(
            "shipping.serviceability_cache_minutes", default=_DEFAULT_TTL_MIN
        )
        # Clamp so an over-eager admin can't disable the cache entirely or
        # cache forever (a Delhivery embargo can flip back to serviceable).
        minutes = max(1, min(minutes, 24 * 60))
        return minutes * 60

    def _read_cache(self, pincode: str) -> ServiceabilityResult | None:
        try:
            raw = self._redis.get(_cache_key(self.provider.name, pincode))
        except redis.RedisError as exc:
            logger.debug("serviceability cache read failed: %s", exc)
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return ServiceabilityResult(**data)
        except (json.JSONDecodeError, TypeError) as exc:
            # Bad cache entry — drop it on the floor; the next call refills.
            logger.warning("bad serviceability cache value for %s: %s", pincode, exc)
            return None

    def _write_cache(self, pincode: str, result: ServiceabilityResult) -> None:
        try:
            self._redis.setex(
                _cache_key(self.provider.name, pincode),
                self._ttl_seconds(),
                json.dumps({
                    "pincode": result.pincode,
                    "serviceable": result.serviceable,
                    "cod_available": result.cod_available,
                    "prepaid_available": result.prepaid_available,
                    "eta_days_min": result.eta_days_min,
                    "eta_days_max": result.eta_days_max,
                    "remark": result.remark,
                }),
            )
        except redis.RedisError as exc:
            # Caching is a perf optimization, not correctness — never fail
            # the call because Redis is unreachable.
            logger.debug("serviceability cache write failed: %s", exc)
