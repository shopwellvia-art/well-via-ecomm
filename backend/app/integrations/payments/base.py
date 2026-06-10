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
