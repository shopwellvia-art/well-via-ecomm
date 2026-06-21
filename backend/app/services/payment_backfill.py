"""Recover a captured gateway transaction id from the payment_events audit log.

Background
----------
Early PhonePe orders could end up PAID with the captured gateway transaction id
recorded only in the append-only ``payment_events`` audit log but never copied
onto the canonical rows (``orders.payment_provider_ref`` and the prepaid
``order_payments`` leg's ``gateway_payment_id``). That left profit/reconciliation
reporting unable to tie the money movement back to PhonePe's own reference.

``backfill_order_payment_ref`` repairs such an order by reading the provider
reference back out of the audit trail and writing it onto the canonical rows.

Guarantees
----------
* WRITE-ONCE: an existing non-null ``payment_provider_ref`` / ``gateway_payment_id``
  is never overwritten, so re-running the backfill (or running it on an already
  healthy order such as #79) is a safe no-op.
* Read-only on the audit log: ``payment_events`` rows are never modified.
* Returns a structured summary so a caller (CLI / test) can assert exactly what
  changed.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.order import Order
from app.models.order_payment import OrderPayment
from app.models.payment_event import PaymentEvent

logger = logging.getLogger(__name__)


def _is_cod_leg(p: OrderPayment) -> bool:
    return (p.payment_method or "").lower() == "cod"


def find_provider_ref_in_events(db: Session, order: Order) -> PaymentEvent | None:
    """Return the most authoritative payment_events row carrying a gateway
    reference for this order, or None.

    Matches on either ``order_id`` or the order's merchant transaction id
    (``payment_intent_id``) because some events (e.g. a webhook that arrives
    before the order is looked up) are only keyed by the merchant txn id.
    Successful events win over others, then most-recent wins.
    """
    if order.payment_intent_id:
        mtid_match = PaymentEvent.merchant_transaction_id == order.payment_intent_id
        where_match = or_(PaymentEvent.order_id == order.id, mtid_match)
    else:
        where_match = PaymentEvent.order_id == order.id

    stmt = (
        select(PaymentEvent)
        .where(where_match)
        .where(PaymentEvent.provider_ref.isnot(None))
        .order_by(
            (PaymentEvent.payment_status == "success").desc(),
            PaymentEvent.id.desc(),
        )
    )
    return db.execute(stmt).scalars().first()


def backfill_order_payment_ref(
    db: Session, order_id: int, *, commit: bool = True
) -> dict[str, Any]:
    """Backfill a single order's captured gateway transaction id from the
    payment_events audit log.

    Returns a summary dict::

        {
          "order_id": 79,
          "order_number": "WV-2026-000079",
          "status": "already_complete" | "backfilled" | "no_ref_in_events"
                    | "not_found" | "not_applicable",
          "found_ref": "T2606210954047550407786" | None,
          "wrote_order_ref": bool,
          "updated_leg_ids": [52],
        }
    """
    order = db.execute(
        select(Order).where(Order.id == order_id)
    ).scalar_one_or_none()
    if order is None:
        return {"order_id": order_id, "status": "not_found"}

    summary: dict[str, Any] = {
        "order_id": order.id,
        "order_number": order.order_number,
        "found_ref": None,
        "wrote_order_ref": False,
        "updated_leg_ids": [],
    }

    prepaid_legs = [p for p in order.payments if not _is_cod_leg(p)]

    # Nothing to do when the order has no gateway leg (pure-COD) — there is no
    # gateway transaction id to recover.
    if not prepaid_legs and not order.payment_intent_id:
        summary["status"] = "not_applicable"
        return summary

    order_ref_missing = not order.payment_provider_ref
    legs_missing = [p for p in prepaid_legs if not p.gateway_payment_id]

    if not order_ref_missing and not legs_missing:
        summary["status"] = "already_complete"
        summary["found_ref"] = order.payment_provider_ref
        return summary

    event = find_provider_ref_in_events(db, order)
    if event is None or not event.provider_ref:
        summary["status"] = "no_ref_in_events"
        return summary

    ref = event.provider_ref
    summary["found_ref"] = ref

    if order_ref_missing:
        order.payment_provider_ref = ref
        summary["wrote_order_ref"] = True

    for leg in legs_missing:
        leg.gateway_payment_id = ref
        # Recover the raw provider payload too when the audit row captured one
        # and the leg has none — never clobber an existing payload.
        if event.raw_payload is not None and leg.raw_gateway_response is None:
            leg.raw_gateway_response = event.raw_payload
        summary["updated_leg_ids"].append(leg.id)

    summary["status"] = "backfilled"

    if commit:
        db.commit()

    logger.info(
        "backfill order=%s ref=%s wrote_order_ref=%s legs=%s",
        order.id,
        ref,
        summary["wrote_order_ref"],
        summary["updated_leg_ids"],
    )
    return summary


def find_orders_missing_ref(db: Session, *, limit: int = 500) -> list[int]:
    """Return ids of PAID, gateway-routed orders whose captured gateway
    transaction id is missing from the canonical rows — candidates for backfill.

    A pure-COD order (no gateway) is never a candidate.
    """
    from app.models.order import OrderStatus

    stmt = (
        select(Order.id)
        .where(Order.status == OrderStatus.PAID)
        .where(Order.gateway_code.isnot(None))
        .where(Order.gateway_code != "mock")
        .where(Order.payment_provider_ref.is_(None))
        .order_by(Order.id.asc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())
