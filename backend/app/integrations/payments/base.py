"""Payment provider contract.

A provider knows how to:
  1. `initiate` a payment — given an order, return a URL the customer should be
     redirected to.
  2. `fetch_status` for a given merchant transaction id — used as the source of
     truth after the customer returns to our return URL.
  3. `parse_webhook` — given an inbound, signature-verified callback body,
     return the resolved status.

The merchant transaction id is *our* identifier (stored on
`orders.payment_intent_id`) — distinct from any id the provider assigns.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Protocol

import httpx
from fastapi import status

from app.core.exceptions import AppError


class PaymentGatewayError(AppError):
    """Upstream payment gateway rejected or failed a request.

    Surfaces as 502 Bad Gateway — the failure is upstream, not a bug in our
    app — and carries the gateway's own code/message via ``message`` /
    ``details`` (see ``provider_rejection``). Every concrete provider error
    class subclasses this so a gateway failure during checkout never reads as
    a generic 500 Internal Server Error.
    """

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "payment_provider_error"


_LIVE_ALIASES = {"live", "production", "prod"}


def normalize_environment(value: str | None) -> str:
    """Canonicalize a gateway environment to exactly ``"sandbox"`` or ``"live"``.

    Accepts common live aliases (``"production"``, ``"prod"``) so a
    misconfigured row can't silently fall through to the sandbox base URL in
    the factory. Anything not recognized as live (``None``, ``"sandbox"``,
    ``"test"``, ``"uat"``, unknown) resolves to ``"sandbox"`` — the safe
    default that never makes real charges.
    """
    if value and value.strip().lower() in _LIVE_ALIASES:
        return "live"
    return "sandbox"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class InitiateRequest:
    order_id: int
    user_id: int
    amount_minor: int  # smallest unit (paise for INR, cents for USD)
    currency: str
    merchant_transaction_id: str
    return_url: str
    user_email: str | None = None
    user_phone: str | None = None


@dataclass
class InitiateResponse:
    redirect_url: str
    provider_transaction_id: str | None = None
    raw: dict | None = None


@dataclass
class StatusResponse:
    merchant_transaction_id: str
    status: PaymentStatus
    provider_transaction_id: str | None = None
    amount_minor: int | None = None
    raw: dict | None = None


class PaymentProvider(Protocol):
    """Minimal surface area; concrete classes may add provider-specific helpers."""

    name: str

    def initiate(self, req: InitiateRequest) -> InitiateResponse: ...

    def fetch_status(
        self, merchant_transaction_id: str, provider_ref: str | None = None
    ) -> StatusResponse: ...

    def verify_webhook(self, body: bytes, signature: str | None) -> bool:
        """Return True iff the webhook body is authentic for this provider."""

    def parse_webhook(self, body: bytes) -> StatusResponse:
        """Extract the resolved status from a (verified) webhook body."""


# ----------------------------------------------------------------------
# Shared error-surfacing helpers
#
# Every provider wraps an upstream HTTP error in its own AppError subclass.
# Historically that wrapper threw away the gateway's own error code/message,
# leaving operators to grep logs to find out *why* a charge was rejected.
# These helpers pull the real reason out of the response body so it flows
# into the API response and admin UI.
# ----------------------------------------------------------------------

_MAX_DETAIL = 300


def _clean(value: object) -> str | None:
    """Stringify a provider-supplied field, trimming whitespace and capping
    length. Returns None for missing/blank values so callers can skip them."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:_MAX_DETAIL]


def extract_provider_error(
    response: httpx.Response,
) -> tuple[str | None, str | None]:
    """Best-effort pull of a gateway's own ``(code, human_message)`` out of an
    error response body, so the real reason surfaces instead of a generic
    'rejected' wrapper.

    Covers the documented error envelopes of every integrated gateway::

        Razorpay      {"error": {"code", "description"}}
        Stripe        {"error": {"code"|"type", "message"}}
        PayPal token  {"error", "error_description"}
        PayPal Orders {"name", "message", "details": [{"issue", "description"}]}
        Paystack      {"status": false, "message", "code"?}
        Flutterwave   {"status": "error", "message"}
        PhonePe       {"code", "message"}

    Returns ``(None, None)`` when the body is not a JSON object (e.g. an HTML
    502 page from an upstream proxy).
    """
    try:
        body = response.json()
    except ValueError:  # json.JSONDecodeError is a ValueError subclass
        return None, None
    if not isinstance(body, dict):
        return None, None

    err = body.get("error")
    # Nested envelope: Razorpay / Stripe.
    if isinstance(err, dict):
        code = err.get("code") or err.get("type")
        message = err.get("description") or err.get("message")
        return _clean(code), _clean(message)
    # OAuth-style flat error string: PayPal token endpoint.
    if isinstance(err, str):
        return _clean(err), _clean(body.get("error_description"))

    # Flat envelopes: PhonePe / Paystack / Flutterwave / PayPal Orders.
    code = body.get("code") or body.get("name")
    message = body.get("message")
    # PayPal puts the actionable reason in details[0].
    details = body.get("details")
    if isinstance(details, list) and details and isinstance(details[0], dict):
        code = code or details[0].get("issue")
        desc = details[0].get("description")
        if desc:
            message = f"{message} ({desc})" if message else desc
    return _clean(code), _clean(message)


def provider_rejection(
    error_cls: type[AppError], prefix: str, response: httpx.Response
) -> AppError:
    """Build a provider error that carries the gateway's own code + message.

    Use inside the ``httpx.HTTPStatusError`` branch of a provider's HTTP
    helpers. The returned exception's ``message`` reads e.g.
    "Razorpay rejected the request: BAD_REQUEST_ERROR — amount is invalid",
    and its ``details`` carries machine-readable ``provider_code`` /
    ``provider_message`` / ``status`` for the admin UI and logs.
    """
    code, message = extract_provider_error(response)
    detail = " — ".join(part for part in (code, message) if part)
    full_message = f"{prefix}: {detail}" if detail else f"{prefix}."
    return error_cls(
        full_message,
        details={
            "status": response.status_code,
            "provider_code": code,
            "provider_message": message,
        },
    )
