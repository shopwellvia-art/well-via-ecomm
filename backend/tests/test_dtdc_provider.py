"""Unit tests for the DTDC shipping provider.

Coverage map
============
Pure-logic (no network):
  1. _map_dtdc_status  — all documented codes + NOT-DELIVERED ordering guard
  2. _dtdc_dt          — IST→UTC conversion
  3. _parse_track_response — full doc-sample JSON ("B32242001" Delivered)

HTTP-mocked (monkeypatching httpx):
  4. create_shipment success path
  5. create_shipment failure path (success=false in data)
  6. create_shipment guards (missing credentials, missing city/state)
  7. label_pdf — PDF bytes returned, non-PDF raises
  8. cancel_shipment — success response, failure response
  9. fetch_tracking — token GET + track POST happy path
 10. Degraded/stub methods — raise or return sentinel values

Runs inside the backend container (no network, no DB):
    docker compose exec backend pytest tests/test_dtdc_provider.py -v
"""
from __future__ import annotations

import json
from datetime import timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.integrations.shipping.base import (
    CartLine,
    PickupRequest,
    ProviderNotConfiguredError,
    RateQuoteRequest,
    ReverseShipmentRequest,
    ShipmentAddress,
    ShipmentRequest,
    ShippingProviderError,
    TrackingStatus,
)
from app.integrations.shipping.dtdc import (
    DtdcProvider,
    _dtdc_dt,
    _map_dtdc_status,
    _parse_track_response,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def provider() -> DtdcProvider:
    """A DtdcProvider in staging mode with all credentials filled in.
    redis_url is None so the token path never attempts a Redis connection."""
    return DtdcProvider(
        api_key="TEST-API-KEY",
        customer_code="TESTCUST",
        tracking_username="trackuser",
        tracking_password="trackpass",
        environment="staging",
        redis_url=None,
    )


@pytest.fixture
def shipment_request() -> ShipmentRequest:
    """A minimal but valid ShipmentRequest (consignee has city + state)."""
    return ShipmentRequest(
        order_id=42,
        order_reference="WV-2026-000042",
        consignee=ShipmentAddress(
            name="Test Receiver",
            phone="9000000001",
            pincode="636010",
            address="123 Main Street",
            city="SALEM",
            state="Tamil Nadu",
        ),
        pickup=ShipmentAddress(
            name="TEST ENTERPRISES",
            phone="9000000000",
            pincode="110046",
            address="dummy sender",
            city="New Delhi",
            state="Delhi",
        ),
        items=[
            CartLine(
                product_id=7,
                quantity=2,
                unit_price=Decimal("199.00"),
                weight_grams=300,
            )
        ],
        declared_value=Decimal("398.00"),
        cod_amount=None,
    )


# Doc-sample JSON response for AWB "B32242001" — used in multiple tests.
_TRACK_SAMPLE: dict = {
    "statusCode": 200,
    "statusFlag": True,
    "status": "SUCCESS",
    "errorDetails": None,
    "trackHeader": {
        "strShipmentNo": "B32242001",
        "strRefNo": "",
        "strCNType": "CP",
        "strCNTypeCode": "BF014",
        "strCNTypeName": "AVENUE ROAD",
        "strCNProduct": "LITE",
        "strModeCode": "",
        "strMode": "",
        "strCNProdCODFOD": "",
        "strOrigin": "BANGALORE",
        "strOriginRemarks": "Booked By",
        "strBookedDate": "21062017",
        "strBookedTime": "15:30:25",
        "strPieces": "1",
        "strWeightUnit": "KG",
        "strWeight": "0.1000",
        "strDestination": "MUMBAI",
        "strStatus": "Delivered",
        "strStatusTransOn": "21062017",
        "strStatusTransTime": "1614",
        "strStatusRelCode": "",
        "strStatusRelName": "",
        "strRemarks": "SIGN",
        "strNoOfAttempts": "1",
        "strRtoNumber": "",
    },
    "trackDetails": [
        {
            "strCode": "BKD",
            "strAction": "Booked",
            "strManifestNo": "",
            "strOrigin": "BANGALORE SURFACE APEX",
            "strDestination": "",
            "strActionDate": "21062017",
            "strActionTime": "1530",
            "sTrRemarks": "",
        },
        {
            "strCode": "OBMD",
            "strAction": "In Transit",
            "strManifestNo": "B7701202",
            "strOrigin": "BANGALORE SURFACE APEX",
            "strDestination": "MUMBAI APEX",
            "strActionDate": "21062017",
            "strActionTime": "1533",
            "sTrRemarks": "",
        },
        {
            "strCode": "OPMF",
            "strAction": "In Transit",
            "strManifestNo": "B7701203",
            "strOrigin": "BANGALORE SURFACE APEX",
            "strDestination": "MUMBAI APEX",
            "strActionDate": "21062017",
            "strActionTime": "1533",
            "sTrRemarks": "",
        },
        {
            "strCode": "IBMD",
            "strAction": "In Transit",
            "strManifestNo": "B7701202",
            "strOrigin": "BANGALORE SURFACE APEX",
            "strDestination": "MUMBAI APEX",
            "strActionDate": "21062017",
            "strActionTime": "1533",
            "sTrRemarks": "",
        },
        {
            "strCode": "CDOUT",
            "strAction": "In Transit",
            "strManifestNo": "",
            "strOrigin": "BANGALORE SURFACE APEX",
            "strDestination": "MUMBAI APEX",
            "strActionDate": "21062017",
            "strActionTime": "1546",
            "sTrRemarks": "",
        },
        {
            "strCode": "CDIN",
            "strAction": "In Transit",
            "strManifestNo": "",
            "strOrigin": "BANGALORE SURFACE APEX",
            "strDestination": "MUMBAI APEX",
            "strActionDate": "21062017",
            "strActionTime": "1555",
            "sTrRemarks": "",
        },
        {
            "strCode": "IPMF",
            "strAction": "In Transit",
            "strManifestNo": "B7701203",
            "strOrigin": "BANGALORE SURFACE APEX",
            "strDestination": "MUMBAI APEX",
            "strActionDate": "21062017",
            "strActionTime": "1603",
            "sTrRemarks": "0.00",
        },
        {
            "strCode": "IBMD",
            "strAction": "In Transit",
            "strManifestNo": "B7701202",
            "strOrigin": "BANGALORE SURFACE APEX",
            "strDestination": "MUMBAI APEX",
            "strActionDate": "21062017",
            "strActionTime": "1603",
            "sTrRemarks": "",
        },
        {
            "strCode": "OBMD",
            "strAction": "In Transit",
            "strManifestNo": "B7701202",
            "strOrigin": "BANGALORE SURFACE APEX",
            "strDestination": "MUMBAI APEX",
            "strActionDate": "21062017",
            "strActionTime": "1603",
            "sTrRemarks": "",
        },
        {
            "strCode": "OUTDLV",
            "strAction": "Out For Delivery",
            "strManifestNo": "",
            "strOrigin": "MUMBAI APEX",
            "strDestination": "",
            "strActionDate": "21062017",
            "strActionTime": "1611",
            "sTrRemarks": "",
        },
        {
            "strCode": "DLV",
            "strAction": "Delivered",
            "strManifestNo": "",
            "strOrigin": "MUMBAI APEX",
            "strDestination": "",
            "strActionDate": "21062017",
            "strActionTime": "1614",
            "sTrRemarks": "SIGN",
        },
    ],
}


# ===========================================================================
# 1. Pure-logic: _map_dtdc_status
# ===========================================================================

class TestMapDtdcStatus:
    """Status mapping — each DTDC code / action maps to a TrackingStatus."""

    def test_dlv_delivered_maps_to_delivered(self):
        assert _map_dtdc_status("DLV", "Delivered") == TrackingStatus.DELIVERED

    def test_outdlv_out_for_delivery_maps_to_out_for_delivery(self):
        assert _map_dtdc_status("OUTDLV", "Out For Delivery") == TrackingStatus.OUT_FOR_DELIVERY

    def test_bkd_booked_maps_to_created(self):
        assert _map_dtdc_status("BKD", "Booked") == TrackingStatus.CREATED

    def test_rto_maps_to_returned(self):
        assert _map_dtdc_status("RTO", "") == TrackingStatus.RETURNED

    def test_returned_action_maps_to_returned(self):
        assert _map_dtdc_status("", "Consignment Has Returned") == TrackingStatus.RETURNED

    def test_not_delivered_maps_to_failed_not_delivered(self):
        # CRITICAL: "not delivered" contains "delivered" — must map to FAILED, not DELIVERED.
        result = _map_dtdc_status("", "Not Delivered")
        assert result == TrackingStatus.FAILED, (
            f"'Not Delivered' must map to FAILED but got {result!r}. "
            "Check that FAILED entry precedes DELIVERED in _DTDC_STATUS_MAP."
        )

    def test_not_delivered_is_not_delivered(self):
        # Extra guard: the returned value must explicitly NOT be DELIVERED.
        assert _map_dtdc_status("", "Not Delivered") != TrackingStatus.DELIVERED

    def test_attempted_maps_to_failed(self):
        assert _map_dtdc_status("", "Attempted") == TrackingStatus.FAILED

    def test_undelivered_maps_to_failed(self):
        assert _map_dtdc_status("", "Undelivered") == TrackingStatus.FAILED

    def test_obmd_in_transit_maps_to_in_transit(self):
        # "OBMD" / "In Transit" — unknown code, no needles match → IN_TRANSIT
        assert _map_dtdc_status("OBMD", "In Transit") == TrackingStatus.IN_TRANSIT

    def test_code_only_bkd_maps_to_created(self):
        # Code alone (empty action) — BKD matches "bkd" needle
        assert _map_dtdc_status("bkd", "") == TrackingStatus.CREATED

    def test_ofd_code_maps_to_out_for_delivery(self):
        assert _map_dtdc_status("OFD", "") == TrackingStatus.OUT_FOR_DELIVERY

    def test_unknown_code_maps_to_in_transit(self):
        assert _map_dtdc_status("XYZUNKNOWN", "Random Scan") == TrackingStatus.IN_TRANSIT

    def test_case_insensitive_matching(self):
        # Matching is done on lower-cased combined string.
        assert _map_dtdc_status("DLV", "DELIVERED") == TrackingStatus.DELIVERED
        assert _map_dtdc_status("dlv", "delivered") == TrackingStatus.DELIVERED

    def test_both_empty_strings_fallback_to_in_transit(self):
        assert _map_dtdc_status("", "") == TrackingStatus.IN_TRANSIT


# ===========================================================================
# 2. Pure-logic: _dtdc_dt date parsing
# ===========================================================================

class TestDtdcDt:
    """Date/time parsing — DTDC uses DDMMYYYY + HHMM in IST."""

    def test_21062017_1614_converts_to_utc(self):
        """21 Jun 2017 16:14 IST == 10:44 UTC (IST = UTC+5:30)."""
        dt = _dtdc_dt("21062017", "1614")
        assert dt.tzinfo is not None
        assert dt.tzinfo == timezone.utc
        assert dt.year == 2017
        assert dt.month == 6
        assert dt.day == 21
        assert dt.hour == 10
        assert dt.minute == 44

    def test_hhmm_colon_format_is_handled(self):
        """_dtdc_dt strips colons from '16:14' → same result as '1614'."""
        dt_colon = _dtdc_dt("21062017", "16:14")
        dt_plain = _dtdc_dt("21062017", "1614")
        assert dt_colon == dt_plain

    def test_hhmmss_format_truncated_to_hhmm(self):
        """'15:30:25' should be treated as '15:30' (seconds ignored)."""
        dt = _dtdc_dt("21062017", "15:30:25")
        assert dt.hour == 10   # 15:30 IST → 10:00 UTC
        assert dt.minute == 0

    def test_malformed_date_returns_now_utc(self):
        """A malformed input must not raise — returns a UTC datetime instead."""
        dt = _dtdc_dt("BADDATE", "9999")
        # Just verify it's tz-aware (UTC) and doesn't explode.
        assert dt.tzinfo == timezone.utc

    def test_empty_date_returns_now_utc(self):
        dt = _dtdc_dt("", "")
        assert dt.tzinfo == timezone.utc


# ===========================================================================
# 3. Pure-logic: _parse_track_response
# ===========================================================================

class TestParseTrackResponse:
    """_parse_track_response against the doc's B32242001 Delivered sample."""

    def test_overall_status_is_delivered(self):
        result = _parse_track_response("B32242001", _TRACK_SAMPLE)
        assert result.status == TrackingStatus.DELIVERED

    def test_awb_number_matches(self):
        result = _parse_track_response("B32242001", _TRACK_SAMPLE)
        assert result.awb_number == "B32242001"

    def test_events_count(self):
        # The sample has 11 trackDetail entries.
        result = _parse_track_response("B32242001", _TRACK_SAMPLE)
        assert len(result.events) == 11

    def test_first_event_is_created_booked(self):
        result = _parse_track_response("B32242001", _TRACK_SAMPLE)
        first = result.events[0]
        assert first.status == TrackingStatus.CREATED  # BKD / Booked

    def test_last_event_is_delivered(self):
        result = _parse_track_response("B32242001", _TRACK_SAMPLE)
        last = result.events[-1]
        assert last.status == TrackingStatus.DELIVERED  # DLV / Delivered

    def test_second_to_last_event_is_out_for_delivery(self):
        result = _parse_track_response("B32242001", _TRACK_SAMPLE)
        ofd = result.events[-2]
        assert ofd.status == TrackingStatus.OUT_FOR_DELIVERY  # OUTDLV

    def test_occurred_at_is_utc_aware(self):
        result = _parse_track_response("B32242001", _TRACK_SAMPLE)
        assert result.occurred_at.tzinfo == timezone.utc

    def test_overall_occurred_at_is_1614_ist(self):
        # strStatusTransOn=21062017, strStatusTransTime=1614 → 10:44 UTC
        result = _parse_track_response("B32242001", _TRACK_SAMPLE)
        assert result.occurred_at.hour == 10
        assert result.occurred_at.minute == 44

    def test_error_response_raises(self):
        bad_payload = {
            "statusCode": 200,
            "statusFlag": False,
            "status": "FAILED",
            "errorDetails": "NO DATA FOUND FOR THIS CNNO NUMBER",
        }
        with pytest.raises(ShippingProviderError, match="DTDC tracking error"):
            _parse_track_response("XXXXXXXXX", bad_payload)

    def test_missing_trackheader_raises(self):
        payload = {
            "statusCode": 200,
            "statusFlag": True,
            "status": "SUCCESS",
            "trackHeader": None,
            "trackDetails": [],
        }
        with pytest.raises(ShippingProviderError, match="no trackHeader"):
            _parse_track_response("B32242001", payload)

    def test_events_location_filled_from_strOrigin(self):
        result = _parse_track_response("B32242001", _TRACK_SAMPLE)
        # First event has strOrigin="BANGALORE SURFACE APEX"
        assert result.events[0].location == "BANGALORE SURFACE APEX"

    def test_in_transit_events_have_correct_status(self):
        result = _parse_track_response("B32242001", _TRACK_SAMPLE)
        # events[1] is OBMD / In Transit
        assert result.events[1].status == TrackingStatus.IN_TRANSIT


# ===========================================================================
# HTTP-mocked helpers
# ===========================================================================

def _make_httpx_response(status_code: int, body, *, content_type: str = "application/json") -> httpx.Response:
    """Build a minimal httpx.Response for monkeypatching."""
    if isinstance(body, (dict, list)):
        encoded = json.dumps(body).encode()
        headers = {"content-type": "application/json"}
    elif isinstance(body, bytes):
        encoded = body
        headers = {"content-type": content_type}
    else:
        encoded = body.encode() if isinstance(body, str) else body
        headers = {"content-type": content_type}
    return httpx.Response(status_code=status_code, content=encoded, headers=headers)


# ===========================================================================
# 4 & 5. create_shipment — success and failure paths
# ===========================================================================

class TestCreateShipment:
    """create_shipment happy path and carrier-side rejection."""

    _SUCCESS_RESPONSE = {
        "status": "OK",
        "data": [
            {
                "success": True,
                "reference_number": "100008518801",
                "courier_partner": None,
                "courier_account": "",
                "courier_partner_reference_number": None,
                "chargeable_weight": 0.025,
                "self_pickup_enabled": True,
                "customer_reference_number": "#100001",
                "pieces": [{"reference_number": "100008518801001", "product_code": ""}],
                "barCodeData": "",
            }
        ],
    }

    def test_create_shipment_success_awb_and_provider(self, provider, shipment_request, monkeypatch):
        mock_resp = _make_httpx_response(200, self._SUCCESS_RESPONSE)
        monkeypatch.setattr("httpx.post", lambda *a, **kw: mock_resp)

        result = provider.create_shipment(shipment_request)

        assert result.awb_number == "100008518801"
        assert result.provider == "dtdc"

    def test_create_shipment_request_body_contains_required_fields(self, provider, shipment_request, monkeypatch):
        captured = {}

        def fake_post(url, *, json=None, headers=None, timeout=None):
            captured["body"] = json
            return _make_httpx_response(200, self._SUCCESS_RESPONSE)

        monkeypatch.setattr("httpx.post", fake_post)
        provider.create_shipment(shipment_request)

        cons = captured["body"]["consignments"][0]
        assert cons["customer_code"] == "TESTCUST"
        assert cons["service_type_id"] == "B2C PRIORITY"
        assert cons["destination_details"]["city"] == "SALEM"
        assert cons["destination_details"]["state"] == "Tamil Nadu"

    def test_create_shipment_weight_calculated_correctly(self, provider, shipment_request, monkeypatch):
        captured = {}

        def fake_post(url, *, json=None, headers=None, timeout=None):
            captured["body"] = json
            return _make_httpx_response(200, self._SUCCESS_RESPONSE)

        monkeypatch.setattr("httpx.post", fake_post)
        provider.create_shipment(shipment_request)

        # 2 items × 300 g = 600 g = 0.60 kg → "0.60"
        assert captured["body"]["consignments"][0]["weight"] == "0.60"

    def test_create_shipment_api_key_sent_in_header(self, provider, shipment_request, monkeypatch):
        captured = {}

        def fake_post(url, *, json=None, headers=None, timeout=None):
            captured["headers"] = headers
            return _make_httpx_response(200, self._SUCCESS_RESPONSE)

        monkeypatch.setattr("httpx.post", fake_post)
        provider.create_shipment(shipment_request)

        assert captured["headers"]["api-key"] == "TEST-API-KEY"

    def test_create_shipment_success_false_raises_shipping_provider_error(self, provider, shipment_request, monkeypatch):
        failure_resp = {
            "status": "OK",
            "data": [
                {
                    "success": False,
                    "reference_number": None,
                    "error": "Invalid pincode",
                }
            ],
        }
        monkeypatch.setattr("httpx.post", lambda *a, **kw: _make_httpx_response(200, failure_resp))

        with pytest.raises(ShippingProviderError, match="Invalid pincode"):
            provider.create_shipment(shipment_request)

    def test_create_shipment_empty_data_raises(self, provider, shipment_request, monkeypatch):
        monkeypatch.setattr(
            "httpx.post",
            lambda *a, **kw: _make_httpx_response(200, {"status": "OK", "data": []}),
        )
        with pytest.raises(ShippingProviderError, match="no consignment data"):
            provider.create_shipment(shipment_request)

    def test_create_shipment_success_but_no_awb_raises(self, provider, shipment_request, monkeypatch):
        resp_body = {
            "status": "OK",
            "data": [{"success": True, "reference_number": None}],
        }
        monkeypatch.setattr("httpx.post", lambda *a, **kw: _make_httpx_response(200, resp_body))

        with pytest.raises(ShippingProviderError, match="no reference_number"):
            provider.create_shipment(shipment_request)

    def test_create_shipment_http_401_raises_provider_not_configured(self, provider, shipment_request, monkeypatch):
        monkeypatch.setattr("httpx.post", lambda *a, **kw: _make_httpx_response(401, {}))
        with pytest.raises(ProviderNotConfiguredError):
            provider.create_shipment(shipment_request)

    def test_create_shipment_http_500_raises_shipping_provider_error(self, provider, shipment_request, monkeypatch):
        monkeypatch.setattr("httpx.post", lambda *a, **kw: _make_httpx_response(500, {"error": "server"}))
        with pytest.raises(ShippingProviderError):
            provider.create_shipment(shipment_request)

    def test_create_shipment_network_error_raises(self, provider, shipment_request, monkeypatch):
        def raise_network(*a, **kw):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr("httpx.post", raise_network)
        with pytest.raises(ShippingProviderError, match="Could not reach DTDC"):
            provider.create_shipment(shipment_request)

    def test_create_shipment_label_url_is_none(self, provider, shipment_request, monkeypatch):
        monkeypatch.setattr("httpx.post", lambda *a, **kw: _make_httpx_response(200, self._SUCCESS_RESPONSE))
        result = provider.create_shipment(shipment_request)
        assert result.label_url is None


# ===========================================================================
# 6. create_shipment guards
# ===========================================================================

class TestCreateShipmentGuards:
    """Guards: missing credentials and missing consignee address fields."""

    def test_empty_api_key_raises_provider_not_configured(self, shipment_request):
        p = DtdcProvider(
            api_key="",
            customer_code="TESTCUST",
            tracking_username="u",
            tracking_password="p",
        )
        with pytest.raises(ProviderNotConfiguredError, match="api_key"):
            p.create_shipment(shipment_request)

    def test_empty_customer_code_raises_provider_not_configured(self, shipment_request):
        p = DtdcProvider(
            api_key="KEY",
            customer_code="",
            tracking_username="u",
            tracking_password="p",
        )
        with pytest.raises(ProviderNotConfiguredError, match="customer_code"):
            p.create_shipment(shipment_request)

    def test_missing_consignee_city_raises_shipping_provider_error(self, provider):
        req = ShipmentRequest(
            order_id=1,
            order_reference="REF001",
            consignee=ShipmentAddress(
                name="Alice",
                phone="9000000001",
                pincode="636010",
                address="123 Main",
                city=None,       # missing
                state="Tamil Nadu",
            ),
            pickup=ShipmentAddress(
                name="Sender",
                phone="9000000000",
                pincode="110046",
                address="Warehouse",
            ),
            items=[CartLine(product_id=1, quantity=1, unit_price=Decimal("100"))],
            declared_value=Decimal("100"),
        )
        with pytest.raises(ShippingProviderError, match="city"):
            provider.create_shipment(req)

    def test_missing_consignee_state_raises_shipping_provider_error(self, provider):
        req = ShipmentRequest(
            order_id=1,
            order_reference="REF001",
            consignee=ShipmentAddress(
                name="Alice",
                phone="9000000001",
                pincode="636010",
                address="123 Main",
                city="SALEM",
                state=None,      # missing
            ),
            pickup=ShipmentAddress(
                name="Sender",
                phone="9000000000",
                pincode="110046",
                address="Warehouse",
            ),
            items=[CartLine(product_id=1, quantity=1, unit_price=Decimal("100"))],
            declared_value=Decimal("100"),
        )
        with pytest.raises(ShippingProviderError, match="state"):
            provider.create_shipment(req)


# ===========================================================================
# 7. label_pdf
# ===========================================================================

class TestLabelPdf:
    """label_pdf: PDF bytes → returned; non-PDF → ShippingProviderError."""

    _PDF_BYTES = b"%PDF-1.4 fake pdf content here"
    _HTML_BYTES = b"<html><body>Error page</body></html>"

    def test_label_pdf_returns_pdf_bytes(self, provider, monkeypatch):
        resp = httpx.Response(
            status_code=200,
            content=self._PDF_BYTES,
            headers={"content-type": "application/pdf"},
        )
        monkeypatch.setattr("httpx.get", lambda *a, **kw: resp)

        result = provider.label_pdf("100008518801")
        assert result == self._PDF_BYTES

    def test_label_pdf_non_pdf_raises_shipping_provider_error(self, provider, monkeypatch):
        resp = httpx.Response(
            status_code=200,
            content=self._HTML_BYTES,
            headers={"content-type": "text/html"},
        )
        monkeypatch.setattr("httpx.get", lambda *a, **kw: resp)

        with pytest.raises(ShippingProviderError, match="non-PDF"):
            provider.label_pdf("100008518801")

    def test_label_pdf_http_401_raises_provider_not_configured(self, provider, monkeypatch):
        monkeypatch.setattr("httpx.get", lambda *a, **kw: httpx.Response(401, content=b""))
        with pytest.raises(ProviderNotConfiguredError):
            provider.label_pdf("100008518801")

    def test_label_pdf_http_400_raises_shipping_provider_error(self, provider, monkeypatch):
        monkeypatch.setattr("httpx.get", lambda *a, **kw: httpx.Response(400, content=b"bad"))
        with pytest.raises(ShippingProviderError):
            provider.label_pdf("100008518801")

    def test_label_pdf_network_error_raises(self, provider, monkeypatch):
        def raise_net(*a, **kw):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr("httpx.get", raise_net)
        with pytest.raises(ShippingProviderError, match="Could not reach DTDC"):
            provider.label_pdf("100008518801")

    def test_label_pdf_sends_api_key_header(self, provider, monkeypatch):
        captured = {}

        def fake_get(url, *, params=None, headers=None, timeout=None):
            captured["headers"] = headers
            return httpx.Response(200, content=self._PDF_BYTES, headers={"content-type": "application/pdf"})

        monkeypatch.setattr("httpx.get", fake_get)
        provider.label_pdf("AWB001")
        assert captured["headers"]["api-key"] == "TEST-API-KEY"


# ===========================================================================
# 8. cancel_shipment
# ===========================================================================

class TestCancelShipment:
    """cancel_shipment: success returns True; failure raises ShippingProviderError."""

    _SUCCESS_RESPONSE = {
        "status": "OK",
        "success": True,
        "successConsignments": [
            {"success": True, "reference_number": "7V000008715"}
        ],
    }

    def test_cancel_shipment_success_returns_true(self, provider, monkeypatch):
        monkeypatch.setattr("httpx.post", lambda *a, **kw: _make_httpx_response(200, self._SUCCESS_RESPONSE))
        assert provider.cancel_shipment("7V000008715") is True

    def test_cancel_shipment_top_level_success_false_but_item_succeeds(self, provider, monkeypatch):
        # Top-level success=false but the specific AWB appears in successConsignments
        resp_body = {
            "status": "OK",
            "success": False,
            "successConsignments": [
                {"success": True, "reference_number": "7V000008715"}
            ],
        }
        monkeypatch.setattr("httpx.post", lambda *a, **kw: _make_httpx_response(200, resp_body))
        assert provider.cancel_shipment("7V000008715") is True

    def test_cancel_shipment_failure_raises_shipping_provider_error(self, provider, monkeypatch):
        failure_resp = {
            "status": "OK",
            "success": False,
            "successConsignments": [],
            "error": "AWB already cancelled",
        }
        monkeypatch.setattr("httpx.post", lambda *a, **kw: _make_httpx_response(200, failure_resp))

        with pytest.raises(ShippingProviderError, match="AWB already cancelled"):
            provider.cancel_shipment("7V000008715")

    def test_cancel_shipment_http_401_raises_provider_not_configured(self, provider, monkeypatch):
        monkeypatch.setattr("httpx.post", lambda *a, **kw: _make_httpx_response(401, {}))
        with pytest.raises(ProviderNotConfiguredError):
            provider.cancel_shipment("7V000008715")

    def test_cancel_shipment_network_error_raises(self, provider, monkeypatch):
        def raise_net(*a, **kw):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr("httpx.post", raise_net)
        with pytest.raises(ShippingProviderError, match="Could not reach DTDC"):
            provider.cancel_shipment("7V000008715")

    def test_cancel_shipment_empty_credentials_raises_provider_not_configured(self):
        p = DtdcProvider(
            api_key="",
            customer_code="",
            tracking_username="u",
            tracking_password="p",
        )
        with pytest.raises(ProviderNotConfiguredError):
            p.cancel_shipment("7V000008715")


# ===========================================================================
# 9. fetch_tracking (token GET + track POST)
# ===========================================================================

class TestFetchTracking:
    """fetch_tracking: auth GET + tracking POST → TrackingUpdate."""

    def _make_provider_no_redis(self):
        return DtdcProvider(
            api_key="TEST-API-KEY",
            customer_code="TESTCUST",
            tracking_username="trackuser",
            tracking_password="trackpass",
            environment="staging",
            redis_url=None,  # no Redis → no caching
        )

    def test_fetch_tracking_happy_path_returns_delivered(self, monkeypatch):
        """Monkeypatch _get_tracking_token to skip the auth HTTP call,
        then monkeypatch httpx.post for the track request."""
        p = self._make_provider_no_redis()

        # Skip token fetch entirely.
        monkeypatch.setattr(p, "_get_tracking_token", lambda: "FAKE-TOKEN")

        def fake_post(url, *, params=None, headers=None, timeout=None):
            return _make_httpx_response(200, _TRACK_SAMPLE)

        monkeypatch.setattr("httpx.post", fake_post)

        update = p.fetch_tracking("B32242001")

        assert update.status == TrackingStatus.DELIVERED
        assert update.awb_number == "B32242001"
        assert len(update.events) == 11

    def test_fetch_tracking_with_auth_http_call(self, monkeypatch):
        """Exercise the full two-step flow: GET token then POST tracking."""
        p = self._make_provider_no_redis()

        token_resp = httpx.Response(200, content=b"MY-BEARER-TOKEN")
        track_resp = _make_httpx_response(200, _TRACK_SAMPLE)

        call_order = []

        def fake_get(url, *, params=None, headers=None, timeout=None):
            call_order.append("GET")
            return token_resp

        def fake_post(url, *, params=None, json=None, headers=None, timeout=None):
            call_order.append("POST")
            assert headers.get("X-Access-Token") == "MY-BEARER-TOKEN"
            return track_resp

        monkeypatch.setattr("httpx.get", fake_get)
        monkeypatch.setattr("httpx.post", fake_post)

        update = p.fetch_tracking("B32242001")
        assert call_order == ["GET", "POST"]
        assert update.status == TrackingStatus.DELIVERED

    def test_fetch_tracking_401_http_response_raises_after_retry(self, monkeypatch):
        """A persistent HTTP 401 on the track endpoint raises ShippingProviderError."""
        p = self._make_provider_no_redis()
        monkeypatch.setattr(p, "_get_tracking_token", lambda: "TOKEN")

        monkeypatch.setattr(
            "httpx.post",
            lambda *a, **kw: _make_httpx_response(401, {"error": "unauthorized"}),
        )
        with pytest.raises(ShippingProviderError, match="auth failed"):
            p.fetch_tracking("B32242001")

    def test_fetch_tracking_token_empty_raises_provider_not_configured(self):
        p = DtdcProvider(
            api_key="KEY",
            customer_code="CUST",
            tracking_username="",   # empty
            tracking_password="",   # empty
            redis_url=None,
        )
        with pytest.raises(ProviderNotConfiguredError, match="tracking credentials"):
            p.fetch_tracking("B32242001")


# ===========================================================================
# 10. Degraded / stub methods
# ===========================================================================

class TestDegradedMethods:
    """Degraded methods raise ShippingProviderError or return sentinel values."""

    def test_rate_quote_raises(self, provider):
        req = RateQuoteRequest(
            origin_pincode="110046",
            destination_pincode="636010",
            items=[],
        )
        with pytest.raises(ShippingProviderError, match="rate API"):
            provider.rate_quote(req)

    def test_create_reverse_shipment_raises(self, provider):
        req = ReverseShipmentRequest(
            return_id=1,
            return_reference="RET001",
            customer=ShipmentAddress(
                name="Customer",
                phone="9000000001",
                pincode="636010",
                address="123 St",
            ),
            warehouse=ShipmentAddress(
                name="WH",
                phone="9000000000",
                pincode="110046",
                address="WH St",
            ),
            items=[],
            declared_value=Decimal("100"),
        )
        with pytest.raises(ShippingProviderError, match="reverse pickup"):
            provider.create_reverse_shipment(req)

    def test_schedule_pickup_raises(self, provider):
        from datetime import datetime, timezone

        req = PickupRequest(
            pickup_location_name="WH",
            pickup_date=datetime(2026, 6, 25, tzinfo=timezone.utc),
        )
        with pytest.raises(ShippingProviderError, match="pickup scheduling"):
            provider.schedule_pickup(req)

    def test_parse_webhook_raises(self, provider):
        with pytest.raises(ShippingProviderError, match="poll-only"):
            provider.parse_webhook(b"{}")

    def test_serviceability_always_returns_serviceable(self, provider):
        result = provider.serviceability("636010")
        assert result.serviceable is True
        assert result.pincode == "636010"

    def test_serviceability_different_pincodes(self, provider):
        for pin in ("110001", "400001", "000000"):
            assert provider.serviceability(pin).serviceable is True

    def test_verify_webhook_returns_false(self, provider):
        assert provider.verify_webhook(b'{"event":"test"}', "sig") is False

    def test_verify_webhook_none_signature_returns_false(self, provider):
        assert provider.verify_webhook(b"body", None) is False

    def test_provider_name_is_dtdc(self, provider):
        assert provider.name == "dtdc"


# ===========================================================================
# 11. Provider constructor / environment selection
# ===========================================================================

class TestProviderConstructor:
    def test_staging_environment_uses_staging_urls(self):
        p = DtdcProvider(
            api_key="K", customer_code="C", tracking_username="u", tracking_password="p",
            environment="staging",
        )
        assert "alphademodashboardapi.shipsy.io" in p._shipsy_base
        assert "dtdcstagingapi.dtdc.com" in p._track_base

    def test_production_environment_uses_production_urls(self):
        p = DtdcProvider(
            api_key="K", customer_code="C", tracking_username="u", tracking_password="p",
            environment="production",
        )
        assert "dtdcapi.shipsy.io" in p._shipsy_base
        assert "blktracksvc.dtdc.com" in p._track_base

    def test_unknown_environment_falls_back_to_staging(self):
        p = DtdcProvider(
            api_key="K", customer_code="C", tracking_username="u", tracking_password="p",
            environment="unknown_env",
        )
        assert "alphademodashboardapi.shipsy.io" in p._shipsy_base

    def test_redis_client_none_when_no_redis_url(self, provider):
        assert provider._redis_client() is None

    def test_default_service_type_id(self, provider):
        assert provider.service_type_id == "B2C PRIORITY"

    def test_default_load_type(self, provider):
        assert provider.load_type == "NON-DOCUMENT"
