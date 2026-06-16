"""Unit tests for the shared payment-provider error-surfacing helpers.

`extract_provider_error` / `provider_rejection` live in
`app.integrations.payments.base` and exist so a gateway's *own* error code and
message reach the API response / admin UI instead of a generic "rejected"
wrapper. These tests lock in the documented error-envelope shape of every
integrated gateway, plus the graceful fallbacks for non-JSON bodies.

Pure functions — no DB, no network. Runs inside the backend container:

    docker compose exec backend pytest tests/unit/test_payment_provider_errors.py -v
"""
from __future__ import annotations

import httpx
import pytest

from app.core.exceptions import AppError
from app.integrations.payments.base import (
    _MAX_DETAIL,
    extract_provider_error,
    provider_rejection,
)
from app.integrations.payments.flutterwave import FlutterwaveError
from app.integrations.payments.paypal import PayPalError
from app.integrations.payments.paystack import PaystackError
from app.integrations.payments.phonepe import PhonePeError
from app.integrations.payments.razorpay import RazorpayError
from app.integrations.payments.stripe import StripeError


# ---------------------------------------------------------------------------
# extract_provider_error — one case per documented gateway envelope shape
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "label, status, body, expected",
    [
        (
            "razorpay",
            400,
            {"error": {"code": "BAD_REQUEST_ERROR", "description": "amount must be at least 100"}},
            ("BAD_REQUEST_ERROR", "amount must be at least 100"),
        ),
        (
            "stripe-code",
            400,
            {"error": {"type": "invalid_request_error", "code": "parameter_missing", "message": "Missing required param: line_items."}},
            ("parameter_missing", "Missing required param: line_items."),
        ),
        (
            "stripe-type-only",  # no `code` → falls back to `type`
            401,
            {"error": {"type": "authentication_error", "message": "Invalid API Key provided."}},
            ("authentication_error", "Invalid API Key provided."),
        ),
        (
            "paystack",  # flat envelope, no machine code
            401,
            {"status": False, "message": "Invalid key"},
            (None, "Invalid key"),
        ),
        (
            "flutterwave",
            401,
            {"status": "error", "message": "Authorization required"},
            (None, "Authorization required"),
        ),
        (
            "paypal-orders",  # `name` is the code; details[0] enriches the message
            422,
            {
                "name": "UNPROCESSABLE_ENTITY",
                "message": "The requested action could not be performed.",
                "details": [{"issue": "CURRENCY_NOT_SUPPORTED", "description": "Currency code not supported."}],
            },
            ("UNPROCESSABLE_ENTITY", "The requested action could not be performed. (Currency code not supported.)"),
        ),
        (
            "paypal-token",  # OAuth-style flat error string
            401,
            {"error": "invalid_client", "error_description": "Client Authentication failed"},
            ("invalid_client", "Client Authentication failed"),
        ),
        (
            "phonepe",
            401,
            {"success": False, "code": "KEY_NOT_CONFIGURED", "message": "Key not found for the merchant"},
            ("KEY_NOT_CONFIGURED", "Key not found for the merchant"),
        ),
    ],
)
def test_extract_known_envelopes(label, status, body, expected):
    resp = httpx.Response(status_code=status, json=body)
    assert extract_provider_error(resp) == expected


def test_paypal_details_issue_used_as_code_when_no_name():
    # No top-level `name`/`code` → the actionable `issue` from details[0]
    # becomes the code so callers still get a machine-readable reason.
    body = {
        "message": "Invalid request.",
        "details": [{"issue": "MISSING_REQUIRED_PARAMETER", "description": "amount is required."}],
    }
    resp = httpx.Response(status_code=400, json=body)
    code, message = extract_provider_error(resp)
    assert code == "MISSING_REQUIRED_PARAMETER"
    assert message == "Invalid request. (amount is required.)"


# ---------------------------------------------------------------------------
# extract_provider_error — graceful fallbacks
# ---------------------------------------------------------------------------

def test_non_json_body_returns_none():
    # e.g. an HTML 502 page from an upstream proxy.
    resp = httpx.Response(status_code=502, text="<html>Bad Gateway</html>")
    assert extract_provider_error(resp) == (None, None)


def test_json_non_object_body_returns_none():
    # A JSON array (or scalar) is valid JSON but has no fields to read.
    resp = httpx.Response(status_code=400, json=["nope"])
    assert extract_provider_error(resp) == (None, None)


def test_blank_fields_cleaned_to_none():
    # Whitespace-only / empty values must not surface as "" — they read as None.
    resp = httpx.Response(status_code=400, json={"code": "   ", "message": ""})
    assert extract_provider_error(resp) == (None, None)


def test_non_string_fields_are_stringified():
    # Some gateways send numeric codes; they should be coerced to str.
    resp = httpx.Response(status_code=400, json={"code": 4001, "message": "bad"})
    assert extract_provider_error(resp) == ("4001", "bad")


def test_long_message_is_capped():
    long_msg = "x" * (_MAX_DETAIL + 50)
    resp = httpx.Response(status_code=400, json={"code": "E", "message": long_msg})
    _, message = extract_provider_error(resp)
    assert message is not None
    assert len(message) == _MAX_DETAIL


# ---------------------------------------------------------------------------
# provider_rejection — message + details assembly, error class preserved
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "error_cls",
    [PhonePeError, RazorpayError, StripeError, PaystackError, FlutterwaveError, PayPalError],
)
def test_provider_rejection_preserves_error_class(error_cls):
    resp = httpx.Response(status_code=401, json={"code": "X", "message": "y"})
    err = provider_rejection(error_cls, "Prefix", resp)
    assert isinstance(err, error_cls)
    assert isinstance(err, AppError)
    # All provider errors share the same machine code for the API envelope.
    assert err.code == "payment_provider_error"


def test_provider_rejection_message_and_details_when_parsed():
    body = {"success": False, "code": "KEY_NOT_CONFIGURED", "message": "Key not found for the merchant"}
    resp = httpx.Response(status_code=401, json=body)
    err = provider_rejection(PhonePeError, "PhonePe rejected the request", resp)
    assert err.message == (
        "PhonePe rejected the request: KEY_NOT_CONFIGURED — Key not found for the merchant"
    )
    assert err.details == {
        "status": 401,
        "provider_code": "KEY_NOT_CONFIGURED",
        "provider_message": "Key not found for the merchant",
    }


def test_provider_rejection_code_only():
    resp = httpx.Response(status_code=400, json={"code": "BAD_REQUEST_ERROR"})
    err = provider_rejection(RazorpayError, "Razorpay rejected the request", resp)
    assert err.message == "Razorpay rejected the request: BAD_REQUEST_ERROR"
    assert err.details["provider_code"] == "BAD_REQUEST_ERROR"
    assert err.details["provider_message"] is None


def test_provider_rejection_message_only():
    resp = httpx.Response(status_code=401, json={"status": False, "message": "Invalid key"})
    err = provider_rejection(PaystackError, "Paystack rejected the request", resp)
    assert err.message == "Paystack rejected the request: Invalid key"
    assert err.details["provider_code"] is None
    assert err.details["provider_message"] == "Invalid key"


def test_provider_rejection_falls_back_to_generic_on_non_json():
    # Non-JSON body → no detail to append; message ends with a plain period.
    resp = httpx.Response(status_code=502, text="<html>502</html>")
    err = provider_rejection(StripeError, "Stripe rejected the request", resp)
    assert err.message == "Stripe rejected the request."
    assert err.details == {
        "status": 502,
        "provider_code": None,
        "provider_message": None,
    }
