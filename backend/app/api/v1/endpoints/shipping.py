"""Customer-facing shipping endpoints.

Phase 2: pincode serviceability. Intentionally public (no auth) so that
anonymous shoppers can check delivery before they commit to signing up —
this is the standard pattern on every Indian e-commerce site.

Rate-limit defense in depth:
  1. The endpoint hits our Redis-cached result first, so most requests
     never touch the carrier.
  2. The IP-based rate limiter caps abuse — 60 req/min/IP is generous for
     a real user typing a pincode and tight enough to make scraping the
     carrier's database via our endpoint impractical.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.core.rate_limit import RateLimiter, get_client_ip
from app.integrations.shipping import (
    ProviderNotConfiguredError,
    ShippingProviderError,
    TrackingStatus,
    TrackingUpdate,
    get_shipping_provider,
)
from app.models.user import User
from app.schemas.shipping import (
    PincodeLookupResponse,
    RateQuoteRequest as RateQuoteRequestSchema,
    RateQuoteResponse,
    ReverseGeocodeResponse,
    ServiceabilityResponse,
)
from app.services.geocode_service import GeocodeService
from app.services.pincode_service import PincodeService
from app.services.settings_service import SettingsService
from app.services.shipping_service import ShippingService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/serviceability/{pincode}",
    response_model=ServiceabilityResponse,
)
def check_serviceability(
    pincode: str,
    request: Request,
    db: Session = Depends(get_db),
):
    # 60 req/min/IP — comfortably above human typing speed, well below any
    # carrier rate limit. Keys off IP because the endpoint is unauthenticated.
    RateLimiter().enforce(
        scope="shipping.serviceability",
        identifier=get_client_ip(request),
        limit=60,
        window_sec=60,
    )

    svc = ShippingService(db)
    try:
        result = svc.serviceability(pincode)
    except ShippingProviderError as exc:
        # Convert provider-layer errors into a 400 — the message is admin-
        # authored or carrier-derived; safe to surface verbatim.
        raise ValidationError(str(exc)) from exc

    return ServiceabilityResponse(
        pincode=result.pincode,
        serviceable=result.serviceable,
        cod_available=result.cod_available,
        prepaid_available=result.prepaid_available,
        eta_days_min=result.eta_days_min,
        eta_days_max=result.eta_days_max,
        remark=result.remark,
        provider=svc.provider.name,
    )


@router.post(
    "/rate-quote",
    response_model=RateQuoteResponse,
)
def rate_quote(
    payload: RateQuoteRequestSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    """Quote shipping for a cart.

    Public + rate-limited. Weights/prices are read from the products table
    server-side, so the body only carries `(product_id, quantity)` — a hostile
    client can't forge a lighter cart to pay less shipping.
    """
    RateQuoteLimiter = RateLimiter()  # noqa: N806 — local alias for readability
    RateQuoteLimiter.enforce(
        scope="shipping.rate_quote",
        identifier=get_client_ip(request),
        limit=60,
        window_sec=60,
    )

    svc = ShippingService(db)
    try:
        quote = svc.rate_quote(
            destination_pincode=payload.destination_pincode,
            cart_items=[(i.product_id, i.quantity) for i in payload.items],
        )
        # Pull the ETA from the serviceability cache opportunistically — same
        # call the customer just made on the cart page, so it's almost certainly
        # cached. If it isn't (admin tweaked the TTL, etc.), skip the field.
        try:
            check = svc.serviceability(payload.destination_pincode)
            eta_min, eta_max = check.eta_days_min, check.eta_days_max
        except Exception:  # noqa: BLE001 — ETA is decoration, never block the quote
            eta_min, eta_max = None, None
    except ProviderNotConfiguredError as exc:
        raise ValidationError(str(exc)) from exc
    except ShippingProviderError as exc:
        raise ValidationError(str(exc)) from exc

    return RateQuoteResponse(
        amount=quote.amount,
        chargeable_weight_grams=quote.chargeable_weight_grams,
        provider=svc.provider.name,
        eta_days_min=eta_min,
        eta_days_max=eta_max,
    )


# ---- Tracking webhook ----------------------------------------------------
#
# Carriers push tracking updates here. We accept any provider name and let
# the active provider's `verify_webhook` decide whether to trust it — that
# way switching carriers doesn't require code changes here.


@router.post("/webhook/{provider_name}", status_code=200)
async def tracking_webhook(
    provider_name: str,
    request: Request,
    token: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Inbound tracking webhook.

    Defense layers, outermost first:
      1. Rate limit on the IP — abuse caps.
      2. Shared-secret check on `?token=…` against `shipping.webhook_secret`
         (carrier registers `https://yoursite.com/.../webhook/delhivery?token=…`).
      3. Provider-specific signature check (`verify_webhook`) — Delhivery
         doesn't sign bodies today, but a future carrier might.
      4. Provider must currently match `shipping.provider` setting — a
         leaked Delhivery token can't be used to spoof events for a store
         currently routing through Shiprocket.

    All carrier retries are idempotent because `apply_tracking_update` dedups
    on (status, occurred_at).
    """
    RateLimiter().enforce(
        scope="shipping.webhook",
        identifier=get_client_ip(request),
        limit=600,  # carrier may legitimately fire many events per minute
        window_sec=60,
    )

    expected_token = (SettingsService(db).get_raw("shipping.webhook_secret") or "").strip()
    if not expected_token:
        # Fail closed when the admin hasn't set a secret — accepting unsigned
        # carrier traffic would let anyone POST fake events.
        logger.warning("rejecting webhook for %s: no webhook_secret configured", provider_name)
        raise ForbiddenError("Webhook secret not configured.")
    if not token or token != expected_token:
        raise ForbiddenError("Invalid webhook token.")

    active = (SettingsService(db).get_raw("shipping.provider") or "none").lower()
    if active != provider_name.lower():
        raise ForbiddenError(
            f"Provider {provider_name!r} is not currently active "
            f"(store is routing through {active!r})."
        )

    body = await request.body()
    provider = get_shipping_provider(db)
    if not provider.verify_webhook(body, signature=None):
        raise ForbiddenError("Webhook signature check failed.")

    try:
        update: TrackingUpdate = provider.parse_webhook(body)
    except Exception as exc:  # noqa: BLE001 — carrier sends whatever it wants
        logger.warning("could not parse %s webhook: %s", provider_name, exc)
        raise ValidationError(f"Could not parse webhook body: {exc}") from exc

    svc = ShippingService(db)
    order = svc.apply_tracking_update(update)
    db.commit()
    if order is None:
        # Acknowledge unknown AWBs — carriers retry until 200, and there's
        # no benefit to making them retry an event for a shipment we don't
        # own.
        return {"ok": True, "matched": False}
    return {"ok": True, "matched": True, "order_id": order.id, "order_status": order.status.value}


# ---- Pincode autofill (public) ------------------------------------------


@router.get(
    "/pincode/{pincode}",
    response_model=PincodeLookupResponse,
)
def lookup_pincode(
    pincode: str,
    request: Request,
):
    """Resolve a 6-digit Indian pincode to city + state for checkout autofill.

    Public endpoint — no auth required.  Returns HTTP 200 in all cases;
    ``found=False`` signals that the city/state could not be determined.
    Never blocks checkout: autofill is purely cosmetic.

    Rate-limited 60 req/min/IP — same policy as the serviceability check.
    """
    RateLimiter().enforce(
        scope="shipping.pincode_lookup",
        identifier=get_client_ip(request),
        limit=60,
        window_sec=60,
    )
    result = PincodeService().lookup(pincode)
    return PincodeLookupResponse(**result)


@router.get(
    "/geocode/reverse",
    response_model=ReverseGeocodeResponse,
)
def reverse_geocode(
    lat: float = Query(ge=-90, le=90, description="Latitude in decimal degrees"),
    lng: float = Query(ge=-180, le=180, description="Longitude in decimal degrees"),
    request: Request = None,
):
    """Resolve a browser geolocation coordinate pair to pincode/city/state.

    Public endpoint — no auth required.  Returns HTTP 200 in all cases;
    ``found=False`` signals that no valid Indian pincode could be determined
    (outside India, postcode unavailable, upstream error, etc.).  Autofill is
    purely cosmetic; checkout is never blocked by a lookup failure.

    Out-of-range coordinates (lat outside [-90,90] or lng outside [-180,180])
    → FastAPI 422 before the handler runs.

    Rate-limited 60 req/min/IP — same policy as the pincode lookup.
    """
    RateLimiter().enforce(
        scope="shipping.reverse_geocode",
        identifier=get_client_ip(request),
        limit=60,
        window_sec=60,
    )
    result = GeocodeService().reverse(lat, lng)
    return ReverseGeocodeResponse(**result)


# ---- Mock simulator (dev only) ------------------------------------------


@router.post("/mock/simulate")
def mock_simulate_status(
    awb: str = Query(..., description="AWB to advance"),
    status: str = Query(..., description="Target TrackingStatus value"),
    note: str | None = Query(default=None),
    _actor: User = Depends(require_permission("orders.update_status")),
    db: Session = Depends(get_db),
):
    """Dev affordance: flip a mock shipment to a new status.

    Hidden when the active provider isn't `mock` — we don't want admins to
    accidentally spoof a Delhivery event for a real shipment.
    """
    active = (SettingsService(db).get_raw("shipping.provider") or "none").lower()
    if active != "mock":
        raise ForbiddenError(
            "The mock simulator only works when shipping.provider=mock."
        )
    try:
        ts = TrackingStatus(status)
    except ValueError as exc:
        raise ValidationError(
            f"status must be one of: {[s.value for s in TrackingStatus]}"
        ) from exc

    # MockShippingProvider has a private helper we lean on for the simulation.
    from app.integrations.shipping.mock import MockShippingProvider

    provider = get_shipping_provider(db)
    if not isinstance(provider, MockShippingProvider):  # safety belt
        raise ForbiddenError("Active provider isn't the mock.")
    provider.push_status(awb, ts, note=note)

    # Replay the update through the normal apply path so the order moves.
    update = provider.fetch_tracking(awb)
    order = ShippingService(db).apply_tracking_update(update)
    db.commit()
    if order is None:
        raise NotFoundError(f"No order references AWB {awb}.")
    return {
        "ok": True,
        "order_id": order.id,
        "order_status": order.status.value,
        "tracking_status": ts.value,
    }
