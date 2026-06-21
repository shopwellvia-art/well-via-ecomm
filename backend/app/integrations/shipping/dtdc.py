"""DTDC courier integration.

Two-host architecture:
  Shipsy/softdata host  — order upload (softdata), label stream, cancellation.
  DTDC tracking host    — REST authentication + consignment tracking.

Staging hosts:
  Shipsy:  https://alphademodashboardapi.shipsy.io
  Tracking: http://dtdcstagingapi.dtdc.com/dtdc-tracking-api/dtdc-api

Production hosts:
  Shipsy:  https://dtdcapi.shipsy.io
  Tracking: https://blktracksvc.dtdc.com/dtdc-api

Auth:
  Shipsy endpoints  — request header ``api-key: <api_key>``
  Tracking step 1   — GET authenticate?username=&password= → plain-text / JSON token
  Tracking step 2   — request header ``X-Access-Token: <token>``

Optional Redis caching for the tracking bearer token (TTL 1800 s). All Redis
ops are best-effort — a cache miss / Redis outage never fails the caller.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Final

import httpx

from app.integrations.shipping.base import (
    PickupRequest,
    PickupResult,
    ProviderNotConfiguredError,
    RateQuote,
    RateQuoteRequest,
    ReverseShipmentRequest,
    ServiceabilityResult,
    ShipmentRequest,
    ShipmentResult,
    ShippingProviderError,
    TrackingEvent,
    TrackingStatus,
    TrackingUpdate,
)

logger = logging.getLogger(__name__)

# ── base-URL tables ────────────────────────────────────────────────────────────

_SHIPSY_BASE: Final[dict[str, str]] = {
    "staging":    "https://alphademodashboardapi.shipsy.io",
    "production": "https://dtdcapi.shipsy.io",
}

_TRACK_BASE: Final[dict[str, str]] = {
    "staging":    "http://dtdcstagingapi.dtdc.com/dtdc-tracking-api/dtdc-api",
    "production": "https://blktracksvc.dtdc.com/dtdc-api",
}

_REQUEST_TIMEOUT = httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=5.0)

# IST offset used when building aware datetimes from DTDC's local timestamps.
_IST = timezone(timedelta(hours=5, minutes=30))


# ── status map ─────────────────────────────────────────────────────────────────

# Each entry: (tuple-of-needles, TrackingStatus).
# Matching is done on the lower-cased combined string "<code> <action>" —
# the first tuple whose ANY needle is contained in that string wins.
_DTDC_STATUS_MAP: list[tuple[tuple[str, ...], TrackingStatus]] = [
    # More-specific strings MUST come before their substrings.
    # "not delivered" contains "delivered", so FAILED must be tested first.
    (("not delivered", "attempted", "undelivered"),        TrackingStatus.FAILED),
    (("out for delivery", "outdlv", "ofd"),                TrackingStatus.OUT_FOR_DELIVERY),
    (("delivered",),                                       TrackingStatus.DELIVERED),
    (("rto", "returned"),                                  TrackingStatus.RETURNED),
    (("booked", "bkd"),                                    TrackingStatus.CREATED),
    (("picked", "pickup"),                                 TrackingStatus.PICKED_UP),
    (("cancelled", "canceled"),                            TrackingStatus.CANCELLED),
]
# Fallback: IN_TRANSIT (covers "in transit", "heldup", "received", "obmd",
# "cdout", "inscan", and anything else DTDC adds later).


def _map_dtdc_status(code: str, action: str) -> TrackingStatus:
    """Map a DTDC event code + action label to a normalized TrackingStatus.

    Both args are lower-cased and concatenated so a single scan covers either
    field, e.g. ``"bkd"`` in the code column matches the ``("booked","bkd")``
    needle even if *action* says something verbose.
    """
    combined = f"{(code or '').lower()} {(action or '').lower()}".strip()
    for needles, status in _DTDC_STATUS_MAP:
        if any(n in combined for n in needles):
            return status
    return TrackingStatus.IN_TRANSIT


def _dtdc_dt(date_str: str, time_str: str) -> datetime:
    """Parse DTDC's ``DDMMYYYY`` date and ``HHMM`` (or ``HH:MM``/``HH:MM:SS``)
    time into a UTC-aware datetime.

    Falls back to ``datetime.now(timezone.utc)`` on any parse failure so a
    single malformed timestamp never explodes the whole tracking update.
    """
    try:
        d = date_str.strip() if date_str else ""
        t = (time_str or "").strip().replace(":", "")[:4]   # "HH:MM" → "HHMM"
        if len(d) != 8 or len(t) != 4:
            raise ValueError("unexpected length")
        dt_local = datetime(
            year=int(d[4:8]),
            month=int(d[2:4]),
            day=int(d[0:2]),
            hour=int(t[0:2]),
            minute=int(t[2:4]),
            tzinfo=_IST,
        )
        return dt_local.astimezone(timezone.utc)
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc)


def _parse_track_response(awb: str, payload: dict) -> TrackingUpdate:
    """Convert the raw DTDC tracking JSON into a ``TrackingUpdate``.

    Expected shape::

        {
          "statusCode": 200,
          "statusFlag": true,
          "status": "SUCCESS",
          "trackHeader": {
            "strShipmentNo": "...",
            "strStatus": "Delivered",
            "strStatusTransOn": "21062017",
            "strStatusTransTime": "1614",
            ...
          },
          "trackDetails": [
            {"strCode": "BKD", "strAction": "Booked",
             "strActionDate": "21062017", "strActionTime": "1530",
             "strOrigin": "BLR", "sTrRemarks": ""},
            ...
          ]
        }
    """
    flag = payload.get("statusFlag", True)
    status_word = (payload.get("status") or "").upper()

    if not flag or status_word not in ("SUCCESS", ""):
        # Surface DTDC's own error message when available.
        err = (
            payload.get("errorDetails")
            or payload.get("strError")
            or payload.get("message")
            or f"DTDC tracking API returned status={status_word!r}"
        )
        raise ShippingProviderError(f"DTDC tracking error: {err}")

    header = payload.get("trackHeader")
    if not header:
        raise ShippingProviderError(
            f"DTDC returned no trackHeader for AWB {awb}."
        )

    overall_status = _map_dtdc_status(
        header.get("strStatus", ""),
        header.get("strStatus", ""),
    )
    overall_dt = _dtdc_dt(
        header.get("strStatusTransOn", ""),
        header.get("strStatusTransTime", ""),
    )

    events: list[TrackingEvent] = []
    for row in payload.get("trackDetails") or []:
        events.append(
            TrackingEvent(
                status=_map_dtdc_status(
                    row.get("strCode", ""),
                    row.get("strAction", ""),
                ),
                occurred_at=_dtdc_dt(
                    row.get("strActionDate", ""),
                    row.get("strActionTime", ""),
                ),
                location=row.get("strOrigin") or None,
                note=row.get("sTrRemarks") or row.get("strAction") or None,
            )
        )

    return TrackingUpdate(
        awb_number=str(awb),
        status=overall_status,
        occurred_at=overall_dt,
        events=events,
    )


# ── provider ───────────────────────────────────────────────────────────────────


class DtdcProvider:
    """DTDC B2C shipping carrier.

    Implements the ``ShippingProvider`` Protocol plus an extra
    ``cancel_shipment`` method (DTDC exposes cancellation; the base Protocol
    does not define it — that is intentional per the spec).
    """

    name = "dtdc"

    def __init__(
        self,
        *,
        api_key: str,
        customer_code: str,
        tracking_username: str,
        tracking_password: str,
        environment: str = "staging",
        service_type_id: str = "B2C PRIORITY",
        load_type: str = "NON-DOCUMENT",
        commodity_id: str = "99",
        label_code: str = "SHIP_LABEL_4X6",
        redis_url: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.customer_code = customer_code
        self.tracking_username = tracking_username
        self.tracking_password = tracking_password
        self.environment = environment.lower()
        self.service_type_id = service_type_id
        self.load_type = load_type
        self.commodity_id = commodity_id
        self.label_code = label_code
        self.redis_url = redis_url

        self._shipsy_base = _SHIPSY_BASE.get(self.environment, _SHIPSY_BASE["staging"])
        self._track_base = _TRACK_BASE.get(self.environment, _TRACK_BASE["staging"])

        # Lazily created Redis client — None until first use.
        self._redis = None

    # ── internal helpers ───────────────────────────────────────────────────────

    def _api_headers(self) -> dict[str, str]:
        """Headers for Shipsy/softdata endpoints."""
        return {
            "Content-Type": "application/json",
            "api-key": self.api_key,
        }

    def _redis_client(self):
        """Return a cached redis.Redis instance, or None if unavailable."""
        if self._redis is not None:
            return self._redis
        if not self.redis_url:
            return None
        try:
            import redis as _redis_mod
            self._redis = _redis_mod.Redis.from_url(
                self.redis_url, decode_responses=True
            )
            return self._redis
        except Exception as exc:  # noqa: BLE001
            logger.warning("DTDC: could not create Redis client: %s", exc)
            return None

    def _get_tracking_token(self) -> str:
        """Obtain a DTDC tracking bearer token.

        Checks Redis cache first (key ``shipping:dtdc:token:<username>``).
        On miss, calls the authenticate endpoint and caches the result for
        1 800 s.  All Redis ops are best-effort — errors are swallowed.
        """
        if not self.tracking_username or not self.tracking_password:
            raise ProviderNotConfiguredError(
                "DTDC tracking credentials are empty. Set "
                "shipping.dtdc.tracking_username and "
                "shipping.dtdc.tracking_password in Admin → Settings → Shipping."
            )

        cache_key = f"shipping:dtdc:token:{self.tracking_username}"
        r = self._redis_client()

        # ── try cache ──────────────────────────────────────────────────────────
        if r is not None:
            try:
                cached = r.get(cache_key)
                if cached:
                    return cached
            except Exception as exc:  # noqa: BLE001
                logger.warning("DTDC: Redis get failed (ignored): %s", exc)

        # ── fetch fresh token ──────────────────────────────────────────────────
        url = f"{self._track_base}/api/dtdc/authenticate"
        try:
            resp = httpx.get(
                url,
                params={
                    "username": self.tracking_username,
                    "password": self.tracking_password,
                },
                timeout=_REQUEST_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            logger.warning("DTDC: tracking auth network error: %s", exc)
            raise ShippingProviderError(
                "Could not reach DTDC tracking API for authentication."
            ) from exc

        if resp.status_code == 401:
            raise ProviderNotConfiguredError(
                "DTDC tracking auth rejected (401). Check "
                "shipping.dtdc.tracking_username / tracking_password."
            )
        if resp.status_code >= 400:
            raise ShippingProviderError(
                f"DTDC tracking auth failed ({resp.status_code}): "
                f"{resp.text[:200]}"
            )

        # Response can be either plain-text token or {"token": "..."} JSON.
        raw = resp.text.strip()
        try:
            parsed = json.loads(raw)
            token = str(parsed.get("token") or parsed.get("access_token") or raw)
        except (json.JSONDecodeError, AttributeError):
            token = raw

        if not token:
            raise ShippingProviderError(
                "DTDC tracking auth returned an empty token."
            )

        # ── store in cache ─────────────────────────────────────────────────────
        if r is not None:
            try:
                r.setex(cache_key, 1800, token)
            except Exception as exc:  # noqa: BLE001
                logger.warning("DTDC: Redis setex failed (ignored): %s", exc)

        return token

    def _clear_token_cache(self) -> None:
        """Evict the cached tracking token (called before a re-auth retry)."""
        r = self._redis_client()
        if r is None:
            return
        try:
            r.delete(f"shipping:dtdc:token:{self.tracking_username}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("DTDC: Redis delete failed (ignored): %s", exc)

    # ── Protocol methods ───────────────────────────────────────────────────────

    def create_shipment(self, req: ShipmentRequest) -> ShipmentResult:
        """Upload a consignment to DTDC via the Shipsy softdata endpoint.

        POST {shipsy_base}/api/customer/integration/consignment/softdata
        """
        # ── credential guard ───────────────────────────────────────────────────
        if not self.api_key or not self.customer_code:
            raise ProviderNotConfiguredError(
                "DTDC api_key or customer_code is empty. "
                "Set shipping.dtdc.* in Admin → Settings → Shipping."
            )

        # ── DTDC mandates destination city + state ─────────────────────────────
        if not req.consignee.city or not req.consignee.state:
            raise ShippingProviderError(
                "DTDC requires destination city and state — "
                "please fill in both fields on the delivery address."
            )

        # ── weight (kg, string) ────────────────────────────────────────────────
        total_kg = (
            sum(
                (line.weight_grams or 200) * line.quantity
                for line in req.items
            )
            / 1000.0
        )
        weight_str = f"{total_kg:.2f}"

        # ── description (label copy) ───────────────────────────────────────────
        products_desc = ", ".join(
            f"#{i.product_id} x{i.quantity}" for i in req.items
        )[:255]

        body = {
            "consignments": [
                {
                    "customer_code":         self.customer_code,
                    "service_type_id":       self.service_type_id,
                    "load_type":             self.load_type,
                    "description":           products_desc,
                    "dimension_unit":        "cm",
                    "length":                "10",
                    "width":                 "10",
                    "height":                "10",
                    "weight_unit":           "kg",
                    "weight":                weight_str,
                    "declared_value":        str(req.declared_value),
                    "num_pieces":            "1",
                    "origin_details": {
                        "name":          req.pickup.name,
                        "phone":         req.pickup.phone,
                        "address_line_1": req.pickup.address,
                        "address_line_2": "",
                        "pincode":       req.pickup.pincode,
                        "city":          req.pickup.city or "",
                        "state":         req.pickup.state or "",
                    },
                    "destination_details": {
                        "name":          req.consignee.name,
                        "phone":         req.consignee.phone,
                        "address_line_1": req.consignee.address,
                        "address_line_2": "",
                        "pincode":       req.consignee.pincode,
                        "city":          req.consignee.city or "",
                        "state":         req.consignee.state or "",
                    },
                    "customer_reference_number":  req.order_reference,
                    "cod_collection_mode":        "CASH" if req.cod_amount else "",
                    "cod_amount":                 str(req.cod_amount) if req.cod_amount else "",
                    "commodity_id":               self.commodity_id,
                    "is_risk_surcharge_applicable": "false",
                    "reference_number":           "",
                }
            ]
        }

        url = f"{self._shipsy_base}/api/customer/integration/consignment/softdata"
        try:
            resp = httpx.post(
                url,
                json=body,
                headers=self._api_headers(),
                timeout=_REQUEST_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            logger.warning("DTDC create_shipment network error: %s", exc)
            raise ShippingProviderError(
                "Could not reach DTDC to create the shipment."
            ) from exc

        if resp.status_code == 401:
            raise ProviderNotConfiguredError(
                "DTDC rejected the api-key (401). Check "
                "shipping.dtdc.api_key in Admin → Settings → Shipping."
            )
        if resp.status_code >= 400:
            raise ShippingProviderError(
                f"DTDC rejected the shipment ({resp.status_code}): "
                f"{resp.text[:300]}"
            )

        data = resp.json()
        # Expected: {"status": "OK", "data": [{"success": true, "reference_number": "<AWB>", ...}]}
        consignments = data.get("data") or []
        if not consignments:
            raise ShippingProviderError(
                "DTDC accepted the call but returned no consignment data."
            )

        first = consignments[0]
        if not first.get("success", False):
            reason = (
                first.get("error")
                or first.get("message")
                or first.get("reason")
                or str(first)
            )
            raise ShippingProviderError(f"DTDC rejected the consignment: {reason}")

        awb = first.get("reference_number")
        if not awb:
            raise ShippingProviderError(
                "DTDC returned success but no reference_number (AWB)."
            )

        return ShipmentResult(
            awb_number=str(awb),
            provider=self.name,
            label_url=None,
            raw=data,
        )

    def fetch_tracking(self, awb_number: str) -> TrackingUpdate:
        """Poll DTDC for the current tracking state of an AWB.

        Two-step:
          1. Obtain / refresh a bearer token via ``_get_tracking_token()``.
          2. POST the tracking query; retry once on 401 (stale token).
        """
        token = self._get_tracking_token()
        update = self._do_track_request(awb_number, token)
        return update

    def _do_track_request(
        self, awb_number: str, token: str, *, _retry: bool = True
    ) -> TrackingUpdate:
        """Perform the DTDC tracking POST with the supplied token.

        On HTTP-401 or JSON statusCode==401 the token is evicted and one
        re-auth retry is attempted (``_retry=False`` on the second pass).
        """
        url = f"{self._track_base}/rest/JSONCnTrk/getTrackDetails"
        try:
            resp = httpx.post(
                url,
                params={
                    "trkType":   "cnno",
                    "strcnno":   awb_number,
                    "addtnlDtl": "Y",
                },
                headers={"X-Access-Token": token},
                timeout=_REQUEST_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            logger.warning("DTDC fetch_tracking network error: %s", exc)
            raise ShippingProviderError(
                "Could not reach DTDC tracking API."
            ) from exc

        # Auth-failure check at HTTP level.
        if resp.status_code == 401:
            if _retry:
                self._clear_token_cache()
                fresh_token = self._get_tracking_token()
                return self._do_track_request(
                    awb_number, fresh_token, _retry=False
                )
            raise ShippingProviderError(
                "DTDC tracking auth failed after token refresh."
            )

        if resp.status_code >= 400:
            raise ShippingProviderError(
                f"DTDC tracking request failed ({resp.status_code}): "
                f"{resp.text[:200]}"
            )

        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise ShippingProviderError(
                f"DTDC tracking returned non-JSON: {resp.text[:200]}"
            ) from exc

        # Auth-failure check at JSON level (DTDC sometimes returns 200 + 401 body).
        if isinstance(payload, dict) and payload.get("statusCode") == 401:
            if _retry:
                self._clear_token_cache()
                fresh_token = self._get_tracking_token()
                return self._do_track_request(
                    awb_number, fresh_token, _retry=False
                )
            raise ShippingProviderError(
                "DTDC tracking auth failed after token refresh (JSON 401)."
            )

        return _parse_track_response(awb_number, payload)

    def label_pdf(self, awb_number: str) -> bytes:
        """Fetch the shipping label PDF for an AWB from the Shipsy host.

        GET {shipsy_base}/api/customer/integration/consignment/shippinglabel/stream
        """
        url = (
            f"{self._shipsy_base}"
            "/api/customer/integration/consignment/shippinglabel/stream"
        )
        try:
            resp = httpx.get(
                url,
                params={
                    "reference_number": awb_number,
                    "label_code":       self.label_code,
                    "label_format":     "pdf",
                },
                headers={"api-key": self.api_key},
                timeout=_REQUEST_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            logger.warning("DTDC label_pdf network error: %s", exc)
            raise ShippingProviderError(
                "Could not reach DTDC to fetch the label."
            ) from exc

        if resp.status_code == 401:
            raise ProviderNotConfiguredError(
                "DTDC rejected the api-key while fetching the label (401)."
            )
        if resp.status_code >= 400:
            raise ShippingProviderError(
                f"DTDC rejected the label request ({resp.status_code}): "
                f"{resp.text[:200]}"
            )

        body = resp.content
        if not body.startswith(b"%PDF-"):
            raise ShippingProviderError(
                "DTDC returned a non-PDF label response."
            )
        return body

    def cancel_shipment(self, awb_number: str) -> bool:
        """Cancel a DTDC consignment.

        POST {shipsy_base}/api/customer/integration/consignment/cancel

        This method extends the base Protocol (which has no cancel_shipment).
        Returns ``True`` on success; raises ``ShippingProviderError`` on
        carrier-side rejection.
        """
        if not self.api_key or not self.customer_code:
            raise ProviderNotConfiguredError(
                "DTDC api_key or customer_code is empty. "
                "Set shipping.dtdc.* in Admin → Settings → Shipping."
            )

        url = f"{self._shipsy_base}/api/customer/integration/consignment/cancel"
        body = {
            "AWBNo":        [awb_number],
            "customerCode": self.customer_code,
        }
        try:
            resp = httpx.post(
                url,
                json=body,
                headers=self._api_headers(),
                timeout=_REQUEST_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            logger.warning("DTDC cancel_shipment network error: %s", exc)
            raise ShippingProviderError(
                "Could not reach DTDC to cancel the shipment."
            ) from exc

        if resp.status_code == 401:
            raise ProviderNotConfiguredError(
                "DTDC rejected the api-key during cancellation (401)."
            )
        if resp.status_code >= 400:
            raise ShippingProviderError(
                f"DTDC rejected the cancel request ({resp.status_code}): "
                f"{resp.text[:300]}"
            )

        data = resp.json()
        # Response: {"status":"OK","success":true,"successConsignments":[...]}
        if data.get("success", False):
            return True

        # Check if our specific AWB succeeded even if the top-level flag is off.
        for entry in data.get("successConsignments") or []:
            if entry.get("success") and str(entry.get("reference_number")) == str(awb_number):
                return True

        detail = (
            data.get("error")
            or data.get("message")
            or data.get("reason")
            or str(data)[:200]
        )
        raise ShippingProviderError(f"DTDC cancellation failed: {detail}")

    # ── degraded / unsupported methods ─────────────────────────────────────────

    def serviceability(self, pincode: str) -> ServiceabilityResult:
        """DTDC has no pincode serviceability API — always return serviceable
        so the checkout flow is never blocked.  Admins should verify coverage
        manually via the DTDC portal."""
        return ServiceabilityResult(
            pincode=pincode,
            serviceable=True,
            remark=(
                "DTDC has no pincode serviceability API — "
                "manual check required via the DTDC portal."
            ),
        )

    def rate_quote(self, req: RateQuoteRequest) -> RateQuote:
        raise ShippingProviderError(
            "DTDC does not expose a rate API — configure flat/free shipping "
            "(shipping.free_threshold)."
        )

    def create_reverse_shipment(self, req: ReverseShipmentRequest) -> ShipmentResult:
        raise ShippingProviderError(
            "DTDC reverse pickup is not supported via the configured API."
        )

    def schedule_pickup(self, req: PickupRequest) -> PickupResult:
        raise ShippingProviderError(
            "DTDC pickup scheduling is not supported via the configured API "
            "(softdata enables self-pickup)."
        )

    def verify_webhook(self, body: bytes, signature: str | None) -> bool:
        """DTDC tracking is poll-only — there is no inbound webhook to verify."""
        return False

    def parse_webhook(self, body: bytes) -> TrackingUpdate:
        raise ShippingProviderError(
            "DTDC tracking is poll-only — use fetch_tracking (Sync tracking)."
        )
