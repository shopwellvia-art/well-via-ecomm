"""Cross-table consistency audit for a single order.

Prints the order row alongside its order_items, order_payments, order_addresses
and the payment_events audit trail, then runs a set of consistency checks so an
operator can eyeball that a PhonePe (or any gateway) order is internally
consistent: the captured gateway transaction id on the order matches the
prepaid payment leg, the leg amounts sum to the order total, a SHIPPING address
snapshot exists, every item carries a cost basis, and the audit log records the
settlement.

Usage (inside the backend container):
    docker compose exec -T backend python scripts/audit_phonepe_order.py 79

Read-only: this script never writes. Use scripts/backfill_payment_refs.py to
repair a missing gateway transaction id.
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal

# Running a script file puts the script's own dir on sys.path, not the backend
# root, so `import app` fails unless we add it explicitly (mirrors the other
# scripts in this directory).
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from sqlalchemy import select, text

from app.db.session import SessionLocal
from app.models.order import Order
from app.models.order_address import OrderAddressType
from app.models.order_payment import PaymentTxnStatus

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


def _D(v) -> Decimal:
    return Decimal(str(v or 0))


def audit(order_id: int) -> int:
    with SessionLocal() as s:
        order = s.execute(
            select(Order).where(Order.id == order_id)
        ).scalar_one_or_none()
        if order is None:
            print(f"ORDER {order_id}: NOT FOUND")
            return 1

        print("=" * 70)
        print(f"ORDER #{order.id}")
        print("=" * 70)
        print(f"  order_number         : {order.order_number}")
        print(f"  status               : {order.status}")
        print(f"  payment_method       : {order.payment_method}")
        print(f"  gateway_code         : {order.gateway_code}")
        print(f"  payment_intent_id    : {order.payment_intent_id}  (merchant txn id)")
        print(f"  payment_provider_ref : {order.payment_provider_ref}  (gateway txn id)")
        print(f"  currency             : {order.currency}")
        print(f"  subtotal             : {order.subtotal}")
        print(f"  tax_amount           : {order.tax_amount}")
        print(f"  discount_amount      : {order.discount_amount}")
        print(f"  shipping_amount      : {order.shipping_amount}")
        print(f"  cod_surcharge_amount : {order.cod_surcharge_amount}")
        print(f"  cod_balance          : {order.cod_balance}")
        print(f"  total_amount         : {order.total_amount}")
        print(f"  paid_at              : {order.paid_at}")

        print("\n  --- order_items ---")
        for it in order.items:
            print(
                f"    item id={it.id} product_id={it.product_id} qty={it.quantity} "
                f"unit_price={it.unit_price} unit_cost={it.unit_cost}"
            )

        print("\n  --- order_payments ---")
        for p in order.payments:
            print(
                f"    leg id={p.id} method={p.payment_method} status={p.payment_status} "
                f"gateway={p.gateway}\n"
                f"        gateway_order_id={p.gateway_order_id} "
                f"gateway_payment_id={p.gateway_payment_id}\n"
                f"        amount={p.amount} txn_ref={p.transaction_reference} "
                f"paid_at={p.paid_at} raw_response={'yes' if p.raw_gateway_response else 'no'}"
            )

        print("\n  --- order_addresses ---")
        for a in order.addresses:
            print(
                f"    addr id={a.id} type={a.address_type} name={a.full_name} "
                f"city={a.city} pincode={a.pincode}"
            )

        print("\n  --- payment_events (by order_id or merchant txn id) ---")
        rows = s.execute(
            text(
                """
                SELECT id, event_type, payment_status, provider_ref,
                       merchant_transaction_id, gateway_code, signature_valid,
                       amount_reported_minor, created_at
                FROM payment_events
                WHERE order_id = :oid OR merchant_transaction_id = :mtid
                ORDER BY id
                """
            ),
            {"oid": order.id, "mtid": order.payment_intent_id},
        ).fetchall()
        if not rows:
            print("    (none)")
        for r in rows:
            print(
                f"    ev id={r[0]} type={r[1]} status={r[2]} provider_ref={r[3]} "
                f"mtid={r[4]} gw={r[5]} sig_valid={r[6]} amt_minor={r[7]} at={r[8]}"
            )

        # ---- consistency checks ----
        print("\n" + "-" * 70)
        print("CONSISTENCY CHECKS")
        print("-" * 70)
        checks: list[tuple[str, bool, str]] = []

        prepaid_legs = [
            p for p in order.payments
            if (p.payment_method or "").lower() != "cod"
        ]
        cod_legs = [p for p in order.payments if (p.payment_method or "").lower() == "cod"]

        leg_sum = sum((_D(p.amount) for p in order.payments), Decimal("0"))
        checks.append(
            (
                "payment legs sum to order total",
                leg_sum == _D(order.total_amount),
                f"legs={leg_sum} total={order.total_amount}",
            )
        )

        items_have_cost = all(it.unit_cost is not None for it in order.items)
        checks.append(
            (
                "every order_item has a unit_cost basis",
                items_have_cost,
                f"{sum(1 for it in order.items if it.unit_cost is not None)}/{len(order.items)} items",
            )
        )

        has_shipping_addr = any(
            a.address_type == OrderAddressType.SHIPPING for a in order.addresses
        )
        checks.append(
            (
                "a SHIPPING order_addresses snapshot exists",
                has_shipping_addr,
                f"{len(order.addresses)} address rows",
            )
        )

        # The captured gateway txn id should appear on the order AND the
        # prepaid leg once the order is paid.
        event_refs = {r[3] for r in rows if r[3]}
        if order.status.value == "paid" and order.gateway_code not in (None, "mock"):
            paid_prepaid = [
                p for p in prepaid_legs if p.payment_status == PaymentTxnStatus.PAID
            ]
            checks.append(
                (
                    "order.payment_provider_ref is set on a paid gateway order",
                    bool(order.payment_provider_ref),
                    f"ref={order.payment_provider_ref}",
                )
            )
            checks.append(
                (
                    "prepaid leg captured (PAID) with a gateway_payment_id",
                    bool(paid_prepaid) and all(p.gateway_payment_id for p in paid_prepaid),
                    f"{len(paid_prepaid)} paid prepaid leg(s)",
                )
            )
            if event_refs:
                checks.append(
                    (
                        "order/leg gateway txn id matches a payment_events provider_ref",
                        order.payment_provider_ref in event_refs
                        or any(p.gateway_payment_id in event_refs for p in paid_prepaid),
                        f"event_refs={sorted(event_refs)}",
                    )
                )

        ok = True
        for label, passed, detail in checks:
            flag = "PASS" if passed else "FAIL"
            if not passed:
                ok = False
            print(f"  [{flag}] {label}  ({detail})")

        print("-" * 70)
        print("RESULT:", "OK — order is internally consistent" if ok else "INCONSISTENCIES FOUND")
        return 0 if ok else 2


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/audit_phonepe_order.py <order_id>")
        return 1
    try:
        order_id = int(sys.argv[1])
    except ValueError:
        print(f"order_id must be an integer, got {sys.argv[1]!r}")
        return 1
    return audit(order_id)


if __name__ == "__main__":
    raise SystemExit(main())
