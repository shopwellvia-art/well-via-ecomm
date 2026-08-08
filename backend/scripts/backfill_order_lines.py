"""Backfill `analytics_order_line` for orders placed before the live capture.

`app/services/analytics/order_line_facts.capture_order_lines` snapshots every
order from now on. Everything already in `orders` predates it, and so does every
order whose capture failed and was logged. This script writes their fact rows.

What a backfilled row honestly is
---------------------------------
Product name, SKU and category are read from the catalog **as it stands today**,
because no earlier copy exists anywhere — that absence is the defect the fact
table closes, and it cannot be closed retroactively. Every row this script
writes therefore carries ``IdentitySource.BACKFILLED_CURRENT_CATALOG``, which is
correct for every SKU that was never renamed or recategorised and quietly wrong
for every SKU that was. The flag is the point: a report can exclude these rows,
caveat them, or accept them, but it cannot mistake them for a real historical
record. Rows written by the live capture carry ``CAPTURED_AT_SALE`` and this
script never overwrites one.

The money is not degraded in the same way. `alloc_*` comes from the same
largest-remainder allocator the live capture uses, reading the order's own
snapshotted `subtotal` / `tax_amount` / `discount_amount` / `shipping_amount` /
`cod_surcharge_amount` / `payment_discount_amount`, so ``SUM(alloc_x) ==
orders.x`` exactly for a backfilled order just as for a captured one. Only
`alloc_tax`'s *distribution across lines* can shift, and only when an admin has
edited a product's tax rows since the sale — the allocator says so in its
warnings rather than hiding it.

Idempotency
-----------
`analytics_order_line.order_item_id` is UNIQUE. Each page probes which of its
order lines already have a fact row and builds insert dicts only for the rest,
so a second run over the same orders writes nothing and reports 0. A partially
captured order (some lines written, capture failed midway) is completed rather
than skipped — the allocation still runs over ALL of the order's lines, so the
lines this script adds sum together with the ones already there back to the
order totals.

Safety
------
The database guard is copied from ``scripts/analytics_perf_report.py`` so there
is one rule in this codebase for "is this database disposable". Unlike that
script, this one has a legitimate reason to run against production — the
production dataset is the only place the historical orders live — so the guard
is overridable by ``--yes-production``. The override is a flag you have to type,
and it prints AND logs exactly which host, schema and environment it is about to
write to before a single row moves. Pair it with ``--dry-run`` first; this
script only ever INSERTs, and never UPDATEs or DELETEs anything.

Usage::

    docker exec wvana-py python scripts/backfill_order_lines.py --dry-run
    docker exec wvana-py python scripts/backfill_order_lines.py
    docker exec wvana-py python scripts/backfill_order_lines.py --order-id 8812
    docker exec wvana-py python scripts/backfill_order_lines.py \\
        --yes-production --page-size 100

Exit codes: 0 success, 3 refused by the database guard.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

# Make the `app` package importable when run as a plain script, exactly as
# scripts/analytics_perf_report.py does. Paths derive from __file__ so the
# working directory is irrelevant.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session, selectinload  # noqa: E402

from app.core.config import settings  # noqa: E402

logger = logging.getLogger("backfill_order_lines")

# ---------------------------------------------------------------------------
# The guard. Nothing below this line may run before it.
# ---------------------------------------------------------------------------
# Copied from scripts/analytics_perf_report.py, which copied it from
# backend/tests/conftest.py. The two allowlists are identical on purpose: one
# rule for "is this database disposable", enforced everywhere that can write.
_SAFE_ENVIRONMENTS = {"test", "development", "ci"}
_SAFE_DB_HOSTS = {"localhost", "127.0.0.1", "mysql", "db"}


class UnsafeDatabaseError(RuntimeError):
    """The configured database is not a local throwaway. Refuse to proceed."""


def require_throwaway_database() -> None:
    """Hard-fail unless we are pointed at a disposable local database.

    Raises rather than exits so the pytest suite can assert on it. The CLI turns
    it into exit code 3, matching ``conftest``'s ``pytest.exit(returncode=3)``.
    """
    if settings.ENVIRONMENT not in _SAFE_ENVIRONMENTS:
        raise UnsafeDatabaseError(
            f"Refusing to run: ENVIRONMENT={settings.ENVIRONMENT!r} is not one of "
            f"{sorted(_SAFE_ENVIRONMENTS)}. This script writes analytics fact "
            "rows; point it at a test/dev/ci environment, or pass "
            "--yes-production if you really mean the live database."
        )
    if settings.MYSQL_HOST not in _SAFE_DB_HOSTS:
        raise UnsafeDatabaseError(
            f"Refusing to run: MYSQL_HOST={settings.MYSQL_HOST!r} is not a "
            f"known-local host {sorted(_SAFE_DB_HOSTS)} — this looks like the "
            "shared remote production MySQL. Pass --yes-production if writing "
            "analytics_order_line there is genuinely what you intend."
        )


def guard_database(*, yes_production: bool, quiet: bool = False) -> bool:
    """Run the guard, honouring an explicit and loudly-recorded override.

    Returns True when the target is a throwaway, False when the override was
    used. Raises ``UnsafeDatabaseError`` when it was not.

    The override deliberately costs something: it names the host, the schema and
    the environment it is about to write to, on stdout AND through the logger,
    so "I ran the backfill" and "I ran the backfill against production" are
    never the same line in a terminal history or a log aggregator.
    """
    try:
        require_throwaway_database()
        return True
    except UnsafeDatabaseError as exc:
        if not yes_production:
            raise
        banner = (
            "\n"
            "=========================================================\n"
            " --yes-production: DATABASE GUARD DELIBERATELY OVERRIDDEN\n"
            f"   environment : {settings.ENVIRONMENT}\n"
            f"   mysql host  : {settings.MYSQL_HOST}\n"
            f"   schema      : {getattr(settings, 'MYSQL_DB', '?')}\n"
            "   writing     : analytics_order_line (INSERT only, never UPDATE)\n"
            f"   guard said  : {exc}\n"
            "=========================================================\n"
        )
        if not quiet:
            print(banner, flush=True)
        logger.warning(
            "backfill_order_lines: database guard overridden with "
            "--yes-production; writing analytics_order_line to "
            "environment=%s host=%s schema=%s",
            settings.ENVIRONMENT,
            settings.MYSQL_HOST,
            getattr(settings, "MYSQL_DB", "?"),
        )
        return False


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------
#: Orders per page. Keyset-paged on `orders.id`, not OFFSET: a concurrent insert
#: shifts every OFFSET page and would silently skip an order.
DEFAULT_PAGE_SIZE = 200


def backfill(
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    limit: int | None = None,
    order_id: int | None = None,
    dry_run: bool = False,
    quiet: bool = False,
) -> dict[str, Any]:
    """Walk historical orders and write the fact rows they are missing.

    Correct and cheap on an empty or tiny dataset: with no orders the first page
    comes back empty and nothing else runs; with production's 8 orders it is a
    single page and two statements.
    """
    from app.db.session import SessionLocal
    from app.models.analytics_facts import IdentitySource
    from app.models.order import Order
    from app.services.analytics.order_line_facts import (
        build_fact_rows,
        existing_order_item_ids,
        write_fact_rows,
    )
    from app.services.analytics.timebox import active_generation

    def log(message: str) -> None:
        if not quiet:
            print(message, flush=True)

    stats = {
        "orders_scanned": 0,
        "orders_with_gaps": 0,
        "orders_already_complete": 0,
        "orders_without_lines": 0,
        "lines_seen": 0,
        "lines_already_present": 0,
        "rows_written": 0,
        "dry_run": dry_run,
        "warnings": [],
    }

    db: Session = SessionLocal()
    try:
        # Safe here (unlike inside a checkout) — this is a CLI and owns its
        # transaction, so letting timebox seed generation 1 is fine. Resolved
        # once so every row in the run carries the same generation even if an
        # admin rolls the reporting timezone mid-run.
        generation = int(active_generation(db).generation)
        total_orders = db.execute(select(func.count(Order.id))).scalar_one()
        log(
            f"backfill: {total_orders} order(s) in scope, tz_generation="
            f"{generation}, page_size={page_size}"
            + (", DRY RUN (nothing will be written)" if dry_run else "")
        )

        last_id = 0
        while True:
            statement = (
                select(Order)
                .options(selectinload(Order.items))
                .where(Order.id > last_id)
                .order_by(Order.id)
                .limit(page_size)
            )
            if order_id is not None:
                statement = statement.where(Order.id == order_id)
            page = list(db.execute(statement).scalars())
            if not page:
                break

            page_rows: list[dict[str, Any]] = []
            for order in page:
                last_id = int(order.id)
                stats["orders_scanned"] += 1

                items = list(order.items)
                if not items:
                    # A real state for a cancelled/abandoned shell row. Nothing
                    # to snapshot; counted rather than silently passed over.
                    stats["orders_without_lines"] += 1
                    continue

                item_ids = [int(i.id) for i in items]
                stats["lines_seen"] += len(item_ids)
                already = existing_order_item_ids(db, item_ids)
                stats["lines_already_present"] += len(already)
                pending = set(item_ids) - already
                if not pending:
                    stats["orders_already_complete"] += 1
                    continue

                stats["orders_with_gaps"] += 1
                # `only_order_item_ids` filters the OUTPUT; the allocation still
                # runs over every line of the order, so a partially-captured
                # order's new rows sum together with its existing ones back to
                # the order totals.
                page_rows.extend(
                    build_fact_rows(
                        db,
                        order,
                        items,
                        identity_source=IdentitySource.BACKFILLED_CURRENT_CATALOG,
                        tz_generation=generation,
                        only_order_item_ids=pending,
                    )
                )

            if page_rows and not dry_run:
                write_fact_rows(db, page_rows)
                db.commit()
            elif page_rows:
                db.rollback()
            stats["rows_written"] += len(page_rows)

            log(
                f"backfill: through order #{last_id} — "
                f"{stats['orders_scanned']} scanned, "
                f"{stats['rows_written']} row(s) "
                + ("would be written" if dry_run else "written")
            )

            if order_id is not None:
                break
            if limit is not None and stats["orders_scanned"] >= limit:
                stats["warnings"].append(
                    f"stopped at --limit {limit}; re-run to continue from "
                    f"order #{last_id}"
                )
                break
            if len(page) < page_size:
                break

        if not dry_run:
            db.commit()
    finally:
        db.close()

    log(
        "backfill: done — "
        f"{stats['orders_scanned']} order(s) scanned, "
        f"{stats['orders_with_gaps']} with missing facts, "
        f"{stats['orders_already_complete']} already complete, "
        f"{stats['lines_already_present']} line(s) already present, "
        f"{stats['rows_written']} row(s) "
        + ("that WOULD be written" if dry_run else "written")
    )
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill analytics_order_line from historical orders. Idempotent: "
            "rows whose order_item_id already has a fact are never rewritten."
        )
    )
    parser.add_argument(
        "--page-size", type=int, default=DEFAULT_PAGE_SIZE,
        help=f"orders per page (default {DEFAULT_PAGE_SIZE})",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="stop after this many orders (for a cautious first pass)",
    )
    parser.add_argument(
        "--order-id", type=int, default=None,
        help="backfill exactly one order",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="report what would be written without writing anything",
    )
    parser.add_argument(
        "--yes-production", action="store_true",
        help=(
            "override the throwaway-database guard. Required to write to the "
            "shared remote MySQL; prints and logs the target first."
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="suppress progress output")
    parser.add_argument(
        "--json", dest="json_path", default=None,
        help="also write the run summary to this path as JSON",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    if args.page_size < 1:
        parser.error("--page-size must be at least 1")

    try:
        guard_database(yes_production=args.yes_production, quiet=args.quiet)
    except UnsafeDatabaseError as exc:
        print(f"\n{exc}\n", file=sys.stderr, flush=True)
        return 3

    stats = backfill(
        page_size=args.page_size,
        limit=args.limit,
        order_id=args.order_id,
        dry_run=args.dry_run,
        quiet=args.quiet,
    )
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(stats, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
