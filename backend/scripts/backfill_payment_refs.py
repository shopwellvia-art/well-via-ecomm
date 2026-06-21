"""CLI to backfill captured gateway transaction ids from the payment_events log.

Repairs PAID gateway orders whose ``orders.payment_provider_ref`` /
``order_payments.gateway_payment_id`` are missing by recovering the reference
from the append-only ``payment_events`` audit trail. WRITE-ONCE and idempotent —
running it twice (or on an already-healthy order) changes nothing.

Usage (inside the backend container):
    # Backfill specific order(s)
    docker compose exec -T backend python scripts/backfill_payment_refs.py 79

    # Scan all PAID gateway orders missing a ref and backfill them
    docker compose exec -T backend python scripts/backfill_payment_refs.py --all

    # Dry-run: report what would change without writing
    docker compose exec -T backend python scripts/backfill_payment_refs.py --all --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

from app.db.session import SessionLocal  # noqa: E402
from app.services.payment_backfill import (  # noqa: E402
    backfill_order_payment_ref,
    find_orders_missing_ref,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "order_ids",
        nargs="*",
        type=int,
        help="Specific order id(s) to backfill.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Scan all PAID gateway orders missing a ref and backfill them.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.order_ids and not args.all:
        print("Nothing to do: pass order id(s) or --all. See --help.")
        return 1

    with SessionLocal() as db:
        order_ids = list(args.order_ids)
        if args.all:
            found = find_orders_missing_ref(db)
            print(f"--all: {len(found)} PAID gateway order(s) missing a ref: {found}")
            order_ids.extend(i for i in found if i not in order_ids)

        if not order_ids:
            print("No orders to process.")
            return 0

        changed = 0
        for oid in order_ids:
            summary = backfill_order_payment_ref(db, oid, commit=not args.dry_run)
            status = summary.get("status")
            if status == "backfilled":
                changed += 1
            print(
                f"order {oid}: {status} "
                f"(ref={summary.get('found_ref')} "
                f"wrote_order_ref={summary.get('wrote_order_ref')} "
                f"legs={summary.get('updated_leg_ids')})"
            )

        if args.dry_run:
            db.rollback()
            print(f"\nDRY RUN — no changes written. {changed} order(s) would change.")
        else:
            print(f"\nDone. {changed} order(s) backfilled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
