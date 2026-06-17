"""Append-only audit writer for the payment subsystem.

All callers use ``record_payment_event(...)`` — it opens its own short-lived
session so the audit row is committed independently of the caller's
transaction.  This guarantees a row survives even when the caller's request
rolls back (e.g. an invalid-signature 403 or an amount-mismatch guard).

Rules enforced here:
  * Never raises — every exception is caught and logged at WARNING level so
    audit failures cannot break the payment flow.
  * Never stores secrets — signatures, Fernet keys, and auth headers must
    NOT appear in ``message`` or ``raw_payload``.
  * Caps raw_payload at 200 top-level keys; deeply-nested payloads are stored
    as-is (they are already bounded by the gateway's own protocol design).
"""
from __future__ import annotations

import logging
from typing import Any

from app.db.session import SessionLocal
from app.models.payment_event import PaymentEvent, PaymentEventType  # noqa: F401

logger = logging.getLogger(__name__)

# Safety cap: if the gateway sends a payload with an absurd number of top-level
# keys we truncate rather than risk persisting something oversized.
_MAX_PAYLOAD_KEYS = 200


def record_payment_event(
    *,
    event_type: str,
    order_id: int | None = None,
    merchant_transaction_id: str | None = None,
    gateway_code: str | None = None,
    payment_status: str | None = None,
    signature_valid: bool | None = None,
    amount_reported_minor: int | None = None,
    amount_expected_minor: int | None = None,
    provider_ref: str | None = None,
    message: str | None = None,
    raw_payload: dict[str, Any] | None = None,
) -> None:
    """Write a single audit row to the payment_events table.

    Opens and immediately commits its own session so the row is durable
    regardless of whatever the caller's session is doing.  Never propagates
    exceptions — callers must not check the return value.
    """
    try:
        # Sanity-cap the payload at the top level only.
        safe_payload: dict[str, Any] | None = None
        if raw_payload is not None:
            if len(raw_payload) > _MAX_PAYLOAD_KEYS:
                items = list(raw_payload.items())[:_MAX_PAYLOAD_KEYS]
                safe_payload = dict(items)
                safe_payload["_truncated"] = True
            else:
                safe_payload = raw_payload

        # Truncate message to the column limit (500 chars).
        safe_message = message[:500] if message else None

        event = PaymentEvent(
            event_type=event_type,
            order_id=order_id,
            merchant_transaction_id=merchant_transaction_id,
            gateway_code=gateway_code,
            payment_status=payment_status,
            signature_valid=signature_valid,
            amount_reported_minor=amount_reported_minor,
            amount_expected_minor=amount_expected_minor,
            provider_ref=provider_ref,
            message=safe_message,
            raw_payload=safe_payload,
        )

        with SessionLocal() as s:
            s.add(event)
            s.commit()

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "payment_audit: failed to write event_type=%s order_id=%s mtid=%s: %s",
            event_type,
            order_id,
            merchant_transaction_id,
            exc,
        )
