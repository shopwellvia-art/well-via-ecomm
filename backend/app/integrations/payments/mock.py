"""In-process mock provider used when the active payment gateway is "mock".

The user is redirected to a local simulator page on the frontend
(``/payments/mock/{merchantTransactionId}``). That page POSTs the user's
decision back through ``/payments/webhook/mock`` — same code path the real
PhonePe webhook would hit, just without signature verification.

State for "what did the customer choose" lives in Redis under
``payment:mock:{mtid}`` so a webhook arriving before a status poll still wins.
"""
from __future__ import annotations

import json
import logging
from urllib.parse import urlencode

import redis

from app.integrations.payments.base import (
    InitiateRequest,
    InitiateResponse,
    PaymentStatus,
    StatusResponse,
)

logger = logging.getLogger(__name__)

_KEY_PREFIX = "payment:mock:"
_TTL_SECONDS = 60 * 60  # one hour is plenty for a checkout flow


class MockProvider:
    name = "mock"

    def __init__(self, *, frontend_url: str, redis_url: str):
        self.frontend_url = frontend_url.rstrip("/")
        self._redis = redis.Redis.from_url(redis_url, decode_responses=True)

    def initiate(self, req: InitiateRequest) -> InitiateResponse:
        # Stash the expected amount so the simulator/webhook stays consistent.
        self._redis.setex(
            _KEY_PREFIX + req.merchant_transaction_id,
            _TTL_SECONDS,
            json.dumps(
                {
                    "status": PaymentStatus.PENDING.value,
                    "amount_minor": req.amount_minor,
                    "order_id": req.order_id,
                }
            ),
        )
        # The frontend route reads `return` and posts to /payments/webhook/mock,
        # then sends the user back to ?return=...
        params = urlencode({"return": req.return_url, "amount": req.amount_minor})
        redirect = f"{self.frontend_url}/payments/mock/{req.merchant_transaction_id}?{params}"
        return InitiateResponse(redirect_url=redirect, provider_transaction_id=None)

    def fetch_status(
        self, merchant_transaction_id: str, provider_ref: str | None = None
    ) -> StatusResponse:
        raw = self._redis.get(_KEY_PREFIX + merchant_transaction_id)
        if not raw:
            return StatusResponse(
                merchant_transaction_id=merchant_transaction_id,
                status=PaymentStatus.PENDING,
            )
        record = json.loads(raw)
        return StatusResponse(
            merchant_transaction_id=merchant_transaction_id,
            status=PaymentStatus(record["status"]),
            amount_minor=record.get("amount_minor"),
            raw=record,
        )

    def verify_webhook(self, body: bytes, signature: str | None) -> bool:
        # Mock mode trusts in-network callers (the simulator). The real
        # PhonePe provider is what enforces signatures.
        return True

    def parse_webhook(self, body: bytes) -> StatusResponse:
        parsed = json.loads(body)
        mtid = parsed["merchant_transaction_id"]
        action = parsed.get("action", "approve").lower()
        status_ = PaymentStatus.SUCCESS if action == "approve" else PaymentStatus.FAILED

        # Persist the decision so a later fetch_status reflects it.
        key = _KEY_PREFIX + mtid
        existing = self._redis.get(key)
        record: dict = json.loads(existing) if existing else {}
        record["status"] = status_.value
        self._redis.setex(key, _TTL_SECONDS, json.dumps(record))

        return StatusResponse(
            merchant_transaction_id=mtid,
            status=status_,
            amount_minor=record.get("amount_minor"),
            raw=record,
        )
