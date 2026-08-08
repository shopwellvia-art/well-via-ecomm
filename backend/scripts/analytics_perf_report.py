"""Measure what the analytics subsystem actually costs, on real data.

The product brief asserts that cached dashboard responses return within 2s and
uncached aggregates within 5s. Nothing had ever measured either number. This
script exists to replace those assertions with evidence, and it is deliberately
built so that an unflattering result is the *easy* outcome: it reports every
operation it ran, including the ones that miss, and it never tunes anything to
make a target pass.

What it does
------------
``seed``      Builds a realistic transactional dataset — by default ~50 000
              orders spread over 400 store-local days, with line items,
              payments, shipments, returns, cart events and an inventory
              ledger — using a fixed RNG seed so two runs on two machines
              produce the same shape.
``measure``   Runs six measurements against that dataset and writes a JSON
              report: every LIVE/PARTIAL view cold and warm through
              ``AnalyticsViewService``, every aggregation job for one bucket, a
              30-day backfill, a cache hit-rate simulation, the CSV export path,
              and the slow queries all of the above generated.
``clean``     Removes everything ``seed`` created.
``all``       ``seed`` then ``measure``.

Why it refuses to run against anything but a throwaway database
---------------------------------------------------------------
``seed`` writes ~500 000 rows into ``orders``, ``order_items``,
``order_payments``, ``shipments`` and ``returns``. Pointed at the shared remote
production MySQL (13.204.184.41) that is not a slow test — it is an
unrecoverable data-integrity incident, and the rows carry no marker the business
would recognise. The guard below is copied from ``backend/tests/conftest.py``
deliberately, character for character in its two allowlists, so there is exactly
one rule in this codebase for "is this database disposable" and both copies fail
the same way. It runs before any command that opens a session, including
``measure`` and ``clean``.

Everything is namespaced and idempotent
---------------------------------------
Every row this script writes is identifiable without a join: orders by
``order_number LIKE 'PERF-%'``, products by ``sku LIKE 'PERF-SKU-%'``, users and
categories by their own prefixes, slow-query telemetry by ``route LIKE 'perf:%'``.
``seed`` is a no-op when the dataset is already present at the requested size
(``--force`` rebuilds), and ``clean`` removes exactly what those predicates
match and nothing else. Re-running the whole thing is therefore safe, and a
half-finished run leaves nothing that a later ``clean`` cannot find.

What "cold" and "warm" mean here
--------------------------------
Cold is ``refresh=true``, which bypasses the cache *read* and runs the resolver
end to end; warm is the immediately following ordinary read, which is
guaranteed to hit the entry the cold pass just wrote. Both go through the real
``AnalyticsViewService`` — the same permission checks, the same cache key, the
same envelope construction — so the numbers include everything the HTTP layer
does except FastAPI's routing and JSON serialisation. Those two are measured
separately by nothing at all, which is stated in the report rather than
silently folded in.

Usage::

    docker exec wvana-py python scripts/analytics_perf_report.py --help
    docker exec wvana-py python scripts/analytics_perf_report.py seed
    docker exec wvana-py python scripts/analytics_perf_report.py measure \\
        --json /repo/backend/logs/analytics-perf.json
    docker exec wvana-py python scripts/analytics_perf_report.py clean --rollups
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import random
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

# Make the `app` package importable when run as a plain script, exactly as
# scripts/dump_analytics_registry.py does. Paths derive from __file__ so the
# working directory is irrelevant.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import delete, func, insert, select, text  # noqa: E402

from app.core.config import settings  # noqa: E402

# ---------------------------------------------------------------------------
# The guard. Nothing below this line may run before it.
# ---------------------------------------------------------------------------
# Copied from backend/tests/conftest.py. The two allowlists are identical on
# purpose: one rule for "is this database disposable", enforced in both places
# that can destroy data.
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
            f"{sorted(_SAFE_ENVIRONMENTS)}. This script writes hundreds of "
            "thousands of orders; point it at a test/dev/ci environment."
        )
    if settings.MYSQL_HOST not in _SAFE_DB_HOSTS:
        raise UnsafeDatabaseError(
            f"Refusing to run: MYSQL_HOST={settings.MYSQL_HOST!r} is not a "
            f"known-local host {sorted(_SAFE_DB_HOSTS)} — this looks like the "
            "shared remote production MySQL. Seeding 50 000 orders into it "
            "would be unrecoverable. Aborting before any DB access."
        )


# ---------------------------------------------------------------------------
# Namespace. Every row this script writes matches one of these.
# ---------------------------------------------------------------------------
ORDER_NUMBER_PREFIX = "PERF-"
SKU_PREFIX = "PERF-SKU-"
EMAIL_DOMAIN = "@analytics-perf.invalid"
CATEGORY_SLUG_PREFIX = "perf-cat-"
SESSION_PREFIX = "perf-sess-"
EVENT_KEY_PREFIX = "perf-evt-"
MOVEMENT_KEY_PREFIX = "perf-mv-"
#: `analytics_sync_runs.worker_id` for runs this script opens.
WORKER_ID = "perf-report"
#: `obs_slow_queries.route` prefix, so the telemetry this script produces is
#: distinguishable from a real request's and is removable by `clean`.
SLOW_ROUTE_PREFIX = "perf:"
#: The disposable superadmin the view measurements authenticate as.
ADMIN_EMAIL = f"perf-admin{EMAIL_DOMAIN}"

#: `--only` value -> key in the report. One mapping, so a new section cannot be
#: added to the CLI and silently never written to the JSON.
_SECTION_KEYS: dict[str, str] = {
    "jobs": "aggregation_jobs",
    "backfill": "backfill",
    "views": "views",
    "cache": "cache_mix",
    "exports": "exports",
}

#: Chunk size for bulk inserts. Large enough that 500 000 rows is a few hundred
#: round trips, small enough that one packet stays well under max_allowed_packet.
INSERT_CHUNK = 2_000

#: Targets from the brief. Stated here so the report can mark each measurement
#: against them rather than leaving the reader to remember what was promised.
TARGET_WARM_SEC = 2.0
TARGET_COLD_SEC = 5.0
#: nginx `proxy_read_timeout` in frontend/nginx.conf. Anything slower than this
#: is a 504 in production no matter what the backend eventually returns.
NGINX_READ_TIMEOUT_SEC = 60.0


# ---------------------------------------------------------------------------
# Seed profiles
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SeedProfile:
    """How much data to build. `full` is the size the report is written at."""

    name: str
    orders: int
    days: int
    customers: int
    products: int
    categories: int
    #: Cart events emitted per order. The funnel rollup reads nothing else.
    events_per_order: int
    seed: int = 20260728


PROFILES: dict[str, SeedProfile] = {
    # The headline profile. ~50k orders / 400 days is the brief's target volume.
    "full": SeedProfile("full", 50_000, 400, 12_000, 120, 6, 5),
    # For a developer who wants the shape of the answer in a minute.
    "small": SeedProfile("small", 3_000, 90, 900, 40, 4, 4),
    # For the pytest suite: big enough to exercise every code path, small enough
    # that seeding is a few seconds.
    "tiny": SeedProfile("tiny", 400, 21, 150, 12, 3, 3),
}


# ---------------------------------------------------------------------------
# Timing primitives
# ---------------------------------------------------------------------------
@dataclass
class Sample:
    """One timed execution of one operation."""

    op: str
    wall_ms: float
    db_ms: float = 0.0
    queries: int = 0
    note: str = ""
    error: str = ""


@dataclass
class Stats:
    """p50 / p95 / max over a set of samples, with the inputs stated."""

    op: str
    n: int
    p50_ms: float
    p95_ms: float
    max_ms: float
    mean_ms: float
    db_ms_p50: float
    queries_p50: float
    note: str = ""
    error: str = ""

    @property
    def p95_sec(self) -> float:
        return self.p95_ms / 1000.0


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile.

    Nearest-rank rather than an interpolating estimator because these sample
    counts are small (3-10) and an interpolated p95 over 3 samples is a
    fabricated number dressed as a measurement.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def summarise(op: str, samples: list[Sample], *, note: str = "") -> Stats:
    ok = [s for s in samples if not s.error]
    errored = [s for s in samples if s.error]
    if not ok:
        return Stats(
            op=op,
            n=0,
            p50_ms=0.0,
            p95_ms=0.0,
            max_ms=0.0,
            mean_ms=0.0,
            db_ms_p50=0.0,
            queries_p50=0.0,
            note=note,
            error=errored[0].error if errored else "no samples",
        )
    walls = [s.wall_ms for s in ok]
    return Stats(
        op=op,
        n=len(ok),
        p50_ms=round(_percentile(walls, 0.50), 2),
        p95_ms=round(_percentile(walls, 0.95), 2),
        max_ms=round(max(walls), 2),
        mean_ms=round(sum(walls) / len(walls), 2),
        db_ms_p50=round(_percentile([s.db_ms for s in ok], 0.50), 2),
        queries_p50=round(_percentile([float(s.queries) for s in ok], 0.50), 1),
        note=note or (ok[0].note if ok else ""),
        error=errored[0].error if errored else "",
    )


# ---------------------------------------------------------------------------
# Slow-query capture
# ---------------------------------------------------------------------------
#: Every slow query captured during this run: (op, SlowQueryRecord).
_SLOW: list[tuple[str, Any]] = []


@contextmanager
def measured(op: str) -> Iterator[Sample]:
    """Time one operation with the PRODUCTION observability instrumentation.

    Installs a real ``RequestCollector`` in the contextvar the SQLAlchemy
    listeners read, so ``db_ms``, ``query_count`` and the slow-query captures
    are produced by the same code and the same ``OBS_SLOW_QUERY_MS`` threshold
    that a live request uses. Nothing here is a second implementation of the
    measurement — a parallel timer would be one more thing that can disagree
    with what the dashboard shows.
    """
    from app.core.observability.collector import (
        RequestCollector,
        reset_collector,
        set_collector,
    )

    collector = RequestCollector()
    token = set_collector(collector)
    sample = Sample(op=op, wall_ms=0.0)
    started = time.perf_counter()
    try:
        yield sample
    finally:
        sample.wall_ms = (time.perf_counter() - started) * 1000.0
        sample.db_ms = collector.db_ms
        sample.queries = collector.query_count
        for record in collector.slow:
            _SLOW.append((op, record))
        reset_collector(token)


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------
def _chunks(rows: list[dict], size: int = INSERT_CHUNK) -> Iterator[list[dict]]:
    for index in range(0, len(rows), size):
        yield rows[index : index + size]


def _bulk(db, table, rows: list[dict]) -> int:
    """executemany over `rows`, committing per chunk.

    Committing per chunk rather than once at the end keeps the undo log bounded
    and means an interrupted seed leaves a partial dataset that `clean` can
    still find — every row is namespaced before it is written, not after.
    """
    written = 0
    for chunk in _chunks(rows):
        db.execute(insert(table), chunk)
        db.commit()
        written += len(chunk)
    return written


def _split_exactly(total: int, weights: list[float]) -> list[int]:
    """Split `total` across `weights` so the parts sum to `total` EXACTLY.

    Rounding each day's share independently leaves a residue, and the residue
    depends on the weights — which here depend on which weekdays the seeded
    window happens to contain, because weekends carry a 1.35 multiplier. The
    window ends yesterday, so its weekday mix changes every day: the seeder
    wrote exactly 400 orders on some calendar days and 398 on others. That makes
    `--orders 400` a label rather than a fact, and every latency figure in the
    report is quoted against that label.

    The rule is the one `kpis._ALLOCATION_RULE` already states for pushing an
    order-level amount down to lines: floor every share, then hand the remaining
    units to the largest fractional parts. Every day still gets at least one
    order — that floor is allocated first, so it cannot be paid for by making
    the total wrong.
    """
    days = len(weights)
    if days == 0:
        return []
    if total <= days:
        # Degenerate, and not silently: one order per day for as many days as
        # there are orders, rather than a window padded out with empty days.
        return [1] * total + [0] * (days - total)

    remaining = total - days  # the guaranteed one-per-day is taken out first
    weight_sum = sum(weights) or 1.0
    exact = [remaining * w / weight_sum for w in weights]
    counts = [1 + int(share) for share in exact]

    short = total - sum(counts)
    # Hand out what rounding down left over, largest fractional part first.
    for index in sorted(
        range(days), key=lambda i: (exact[i] - int(exact[i]), i), reverse=True
    )[:short]:
        counts[index] += 1
    assert sum(counts) == total, "largest-remainder split must be exact"
    return counts


def _existing_counts(db) -> dict[str, int]:
    """How much of the namespace is already present."""
    from app.models.order import Order
    from app.models.product import Product
    from app.models.user import User

    return {
        "orders": int(
            db.execute(
                select(func.count(Order.id)).where(
                    Order.order_number.like(f"{ORDER_NUMBER_PREFIX}%")
                )
            ).scalar_one()
        ),
        "products": int(
            db.execute(
                select(func.count(Product.id)).where(
                    Product.sku.like(f"{SKU_PREFIX}%")
                )
            ).scalar_one()
        ),
        "users": int(
            db.execute(
                select(func.count(User.id)).where(
                    User.email.like(f"%{EMAIL_DOMAIN}")
                )
            ).scalar_one()
        ),
    }


def seed(profile: SeedProfile, *, force: bool = False, quiet: bool = False) -> dict:
    """Build the dataset. Idempotent: a matching dataset is left alone.

    Returns a summary of what exists afterwards, whether or not it was written
    by this call.
    """
    require_throwaway_database()

    from app.db.session import SessionLocal
    from app.models.analytics_facts import (
        CartEvent,
        CartEventType,
        InventoryMovement,
        MovementType,
    )
    from app.models.order import Order, OrderItem, OrderStatus
    from app.models.order_payment import OrderPayment, PaymentTxnStatus
    from app.models.product import Category, Product
    from app.models.return_request import ReturnRequest
    from app.models.shipment import Shipment, ShipmentStatus
    from app.models.user import User

    def log(message: str) -> None:
        if not quiet:
            print(message, flush=True)

    with SessionLocal() as db:
        present = _existing_counts(db)
        if present["orders"] >= profile.orders and not force:
            log(
                f"seed: {present['orders']} PERF orders already present "
                f"(target {profile.orders}); nothing to do. Use --force to rebuild."
            )
            return {"seeded": False, **present}

    if force or present["orders"]:
        log("seed: clearing the existing PERF namespace first")
        clean(rollups=False, quiet=quiet)

    # Boot seed data. On a database created straight from `alembic upgrade head`
    # none of it exists, because those seeders run in the FastAPI lifespan and
    # this script never builds the app. Without `store.timezone` every bucket
    # boundary falls back to a default rather than to the configured reporting
    # timezone, which is the one input that silently re-buckets a fifth of each
    # day's trade. All three are idempotent.
    with SessionLocal() as db:
        try:
            from app.services.rbac_seed import seed_rbac
            from app.services.settings_seed import seed_settings

            seed_rbac(db)
            seed_settings(db)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - seeding must not abort the run
            db.rollback()
            log(f"seed: warning, boot seeders failed: {exc}")

    rng = random.Random(profile.seed)
    now = datetime.now(timezone.utc)
    # The window ends yesterday: today's bucket is still being written, and a
    # dataset whose last day is a partial one makes every "is the rollup stale"
    # measurement ambiguous.
    last_day = (now - timedelta(days=1)).date()
    first_day = last_day - timedelta(days=profile.days - 1)

    with SessionLocal() as db:
        # -- catalogue --------------------------------------------------
        category_rows = [
            {
                "name": f"Perf Category {n}",
                "slug": f"{CATEGORY_SLUG_PREFIX}{n}",
                "created_at": now,
                "updated_at": now,
            }
            for n in range(1, profile.categories + 1)
        ]
        _bulk(db, Category.__table__, category_rows)
        category_ids = [
            int(cid)
            for cid in db.execute(
                select(Category.id).where(
                    Category.slug.like(f"{CATEGORY_SLUG_PREFIX}%")
                )
            ).scalars()
        ]
        log(f"seed: {len(category_ids)} categories")

        product_rows = []
        for n in range(1, profile.products + 1):
            price = Decimal(rng.choice([199, 349, 499, 799, 1299, 1999, 2999, 4999]))
            # 1 product in 10 carries no cost. That is the real state of this
            # catalogue and it is what makes `costed_units / units` less than 1,
            # which is the coverage every margin view reports on.
            cost = None if n % 10 == 0 else (price * Decimal("0.62")).quantize(Decimal("0.01"))
            # 1 in 12 is out of stock, so inventory_daily's per-product
            # `_days_oos` lookup is actually exercised.
            stock = 0 if n % 12 == 0 else rng.randint(5, 900)
            product_rows.append(
                {
                    "sku": f"{SKU_PREFIX}{n:05d}",
                    "name": f"Perf Product {n}",
                    "price": price,
                    "cost": cost,
                    "stock": stock,
                    "category_id": category_ids[n % len(category_ids)],
                    "weight_grams": rng.randint(80, 1200),
                    "created_at": now,
                    "updated_at": now,
                }
            )
        _bulk(db, Product.__table__, product_rows)
        products = [
            (int(pid), price, cost)
            for pid, price, cost in db.execute(
                select(Product.id, Product.price, Product.cost)
                .where(Product.sku.like(f"{SKU_PREFIX}%"))
                .order_by(Product.id)
            ).all()
        ]
        log(f"seed: {len(products)} products")

        # -- customers --------------------------------------------------
        # A single shared hash: this is a throwaway database and nothing ever
        # authenticates as these accounts. Hashing 12 000 distinct passwords
        # with bcrypt would dominate the seed time and measure nothing.
        placeholder_hash = "$2b$12$perfperfperfperfperfpeMDGL9SEbHOeQyFwUdW3nQ7rL0PIeUi"
        user_rows = [
            {
                "email": f"perf-c{n:06d}{EMAIL_DOMAIN}",
                "hashed_password": placeholder_hash,
                "is_active": True,
                "is_admin": False,
                "created_at": now - timedelta(days=profile.days + 30),
                "updated_at": now,
            }
            for n in range(1, profile.customers + 1)
        ]
        user_rows.append(
            {
                "email": ADMIN_EMAIL,
                "hashed_password": placeholder_hash,
                "is_active": True,
                "is_admin": True,
                "created_at": now,
                "updated_at": now,
            }
        )
        _bulk(db, User.__table__, user_rows)
        customer_ids = [
            int(uid)
            for uid in db.execute(
                select(User.id)
                .where(User.email.like(f"perf-c%{EMAIL_DOMAIN}"))
                .order_by(User.id)
            ).scalars()
        ]
        log(f"seed: {len(customer_ids)} customers + 1 admin")

        # -- orders -----------------------------------------------------
        # Explicit ids so children can reference them without a round trip per
        # order. Taken from the current max so this never collides with rows
        # somebody else's fixture left behind.
        base_order_id = int(db.execute(select(func.coalesce(func.max(Order.id), 0))).scalar_one())

        # Repeat-purchase skew: a third of orders come from the first 15% of
        # customers. A uniform customer distribution would make every cohort,
        # RFM and LTV view degenerate and would understate customer_snapshot's
        # per-customer row count.
        loyal_cut = max(1, int(len(customer_ids) * 0.15))

        statuses = (
            [OrderStatus.DELIVERED] * 35
            + [OrderStatus.PAID] * 30
            + [OrderStatus.SHIPPED] * 20
            + [OrderStatus.PENDING] * 6
            + [OrderStatus.CANCELLED] * 6
            + [OrderStatus.REFUNDED] * 3
        )
        revenue_statuses = {OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.DELIVERED}
        couriers = ["Delhivery", "DTDC", "BlueDart", "Ekart", "XpressBees"]
        gateways = ["razorpay", "phonepe"]
        instruments = ["upi", "card", "netbanking", "wallet"]
        states = ["Karnataka", "Maharashtra", "Tamil Nadu", "Delhi", "Telangana", "Kerala"]
        cities = ["Bengaluru", "Mumbai", "Chennai", "New Delhi", "Hyderabad", "Kochi"]

        order_rows: list[dict] = []
        item_rows: list[dict] = []
        payment_rows: list[dict] = []
        shipment_rows: list[dict] = []
        return_rows: list[dict] = []
        event_rows: list[dict] = []
        movement_rows: list[dict] = []
        totals: dict[str, int] = {
            "orders": 0,
            "order_items": 0,
            "order_payments": 0,
            "shipments": 0,
            "returns": 0,
            "cart_events": 0,
            "inventory_movements": 0,
        }

        def flush_pending() -> None:
            """Write and clear the pending buffers.

            Called periodically rather than once at the end so peak memory stays
            bounded: the full profile produces roughly 900 000 rows, and holding
            all of them as Python dicts before the first INSERT is how a seeder
            gets OOM-killed on a laptop. Orders go first — `order_items`,
            `order_payments` and `shipments` all carry an FK to them.
            """
            for key, table, rows in (
                ("orders", Order.__table__, order_rows),
                ("order_items", OrderItem.__table__, item_rows),
                ("order_payments", OrderPayment.__table__, payment_rows),
                ("shipments", Shipment.__table__, shipment_rows),
                ("returns", ReturnRequest.__table__, return_rows),
                ("cart_events", CartEvent.__table__, event_rows),
                ("inventory_movements", InventoryMovement.__table__, movement_rows),
            ):
                if rows:
                    totals[key] += _bulk(db, table, rows)
                    rows.clear()

        # Weekly seasonality plus a mild growth trend, so daily buckets are not
        # flat. A flat series makes the forecasting and anomaly views trivially
        # cheap and hides the cost of the ones that fit a trend.
        weights = []
        for offset in range(profile.days):
            day = first_day + timedelta(days=offset)
            weekend = 1.35 if day.weekday() >= 5 else 1.0
            growth = 0.7 + 0.6 * (offset / max(1, profile.days - 1))
            weights.append(weekend * growth)
        per_day = _split_exactly(profile.orders, weights)

        order_id = base_order_id
        sequence = 0
        for offset, count in enumerate(per_day):
            day = first_day + timedelta(days=offset)
            for _ in range(count):
                if sequence >= profile.orders:
                    break
                sequence += 1
                order_id += 1
                # Spread across the whole UTC day so the hourly rollup has all
                # 24 buckets populated and the store-local day boundary
                # (Asia/Kolkata, UTC+5:30) actually splits orders between two
                # UTC dates — which is the condition timebox exists for.
                created = datetime.combine(day, dtime.min, tzinfo=timezone.utc) + timedelta(
                    seconds=rng.randint(0, 86_399)
                )
                if rng.random() < 0.33:
                    user_id = customer_ids[rng.randrange(loyal_cut)]
                else:
                    user_id = customer_ids[rng.randrange(len(customer_ids))]

                status = statuses[rng.randrange(len(statuses))]
                line_count = rng.choice([1, 1, 1, 2, 2, 3, 4])
                lines = []
                subtotal = Decimal("0.00")
                for _line in range(line_count):
                    pid, price, cost = products[rng.randrange(len(products))]
                    qty = rng.choice([1, 1, 1, 2, 3])
                    subtotal += price * qty
                    lines.append((pid, qty, price, cost))

                discount = (
                    (subtotal * Decimal("0.10")).quantize(Decimal("0.01"))
                    if rng.random() < 0.28
                    else Decimal("0.00")
                )
                tax = ((subtotal - discount) * Decimal("0.18")).quantize(Decimal("0.01"))
                shipping = Decimal("0.00") if subtotal > 999 else Decimal("49.00")
                is_cod = rng.random() < 0.25
                cod_surcharge = Decimal("39.00") if is_cod else Decimal("0.00")
                payment_discount = (
                    Decimal("25.00") if (not is_cod and rng.random() < 0.15) else Decimal("0.00")
                )
                total = subtotal - discount + tax + shipping + cod_surcharge - payment_discount

                paid_at = created + timedelta(minutes=rng.randint(1, 30)) if status in revenue_statuses else None
                shipped_at = (
                    created + timedelta(hours=rng.randint(6, 72))
                    if status in (OrderStatus.SHIPPED, OrderStatus.DELIVERED)
                    else None
                )
                delivered_at = (
                    (shipped_at or created) + timedelta(hours=rng.randint(18, 120))
                    if status is OrderStatus.DELIVERED
                    else None
                )
                cancelled_at = created + timedelta(hours=rng.randint(1, 48)) if status is OrderStatus.CANCELLED else None
                refunded_at = created + timedelta(days=rng.randint(2, 21)) if status is OrderStatus.REFUNDED else None

                courier = couriers[rng.randrange(len(couriers))]
                region = rng.randrange(len(states))
                order_rows.append(
                    {
                        "id": order_id,
                        "order_number": f"{ORDER_NUMBER_PREFIX}{sequence:07d}",
                        "user_id": user_id,
                        "status": status,
                        "subtotal": subtotal,
                        "tax_amount": tax,
                        "discount_amount": discount,
                        "shipping_amount": shipping,
                        "total_amount": total,
                        "coupon_code": "PERF10" if discount else None,
                        "currency": "INR",
                        "payment_method": "cod" if is_cod else "prepaid",
                        "cod_surcharge_amount": cod_surcharge,
                        "cod_balance": total if is_cod else Decimal("0.00"),
                        "payment_instrument": None if is_cod else instruments[rng.randrange(4)],
                        "payment_discount_amount": payment_discount,
                        "shipping_city": cities[region],
                        "shipping_state": states[region],
                        "shipping_pincode": f"{560000 + rng.randint(1, 99):06d}",
                        "gateway_code": None if is_cod else gateways[rng.randrange(2)],
                        "carrier": courier,
                        "shipping_provider": courier.lower(),
                        "paid_at": paid_at,
                        "shipped_at": shipped_at,
                        "delivered_at": delivered_at,
                        "cancelled_at": cancelled_at,
                        "refunded_at": refunded_at,
                        "created_at": created,
                        "updated_at": created,
                    }
                )
                for pid, qty, price, cost in lines:
                    item_rows.append(
                        {
                            "order_id": order_id,
                            "product_id": pid,
                            "quantity": qty,
                            "unit_price": price,
                            "unit_cost": cost,
                            "created_at": created,
                            "updated_at": created,
                        }
                    )
                    if status in revenue_statuses:
                        movement_rows.append(
                            {
                                "product_id": pid,
                                "occurred_at": created,
                                "movement_type": MovementType.COMMIT_SALE,
                                "delta": -qty,
                                "ref_type": "order",
                                "ref_id": order_id,
                                "event_key": f"{MOVEMENT_KEY_PREFIX}s{order_id}-{pid}-{len(movement_rows)}",
                                "created_at": created,
                            }
                        )

                if not is_cod:
                    payment_rows.append(
                        {
                            "order_id": order_id,
                            "gateway": gateways[rng.randrange(2)],
                            "gateway_order_id": f"perf_ord_{order_id}",
                            "gateway_payment_id": f"perf_pay_{order_id}",
                            "payment_method": "prepaid",
                            "payment_status": (
                                PaymentTxnStatus.PAID
                                if status in revenue_statuses
                                else PaymentTxnStatus.FAILED
                            ),
                            "amount": total,
                            "currency": "INR",
                            "paid_at": paid_at,
                            "failed_at": None if status in revenue_statuses else created,
                            "created_at": created,
                            "updated_at": created,
                        }
                    )
                    # A failed first attempt on ~8% of prepaid orders, so the
                    # payment-failure views have something real to count.
                    if rng.random() < 0.08:
                        payment_rows.append(
                            {
                                "order_id": order_id,
                                "gateway": gateways[rng.randrange(2)],
                                "gateway_order_id": f"perf_ord_{order_id}_r",
                                "gateway_payment_id": f"perf_pay_{order_id}_r",
                                "payment_method": "prepaid",
                                "payment_status": PaymentTxnStatus.FAILED,
                                "amount": total,
                                "currency": "INR",
                                # Every row in this list must carry the same key
                                # set: executemany binds one compiled statement
                                # for the whole chunk and a missing key is a
                                # StatementError, not a NULL.
                                "paid_at": None,
                                "failed_at": created,
                                "created_at": created,
                                "updated_at": created,
                            }
                        )

                if shipped_at is not None:
                    shipment_status = (
                        ShipmentStatus.DELIVERED if delivered_at else ShipmentStatus.IN_TRANSIT
                    )
                    if rng.random() < 0.03:
                        shipment_status = ShipmentStatus.RTO_INITIATED
                    shipment_rows.append(
                        {
                            "order_id": order_id,
                            "courier_partner": courier,
                            "courier_service": "surface",
                            "awb_number": f"PERFAWB{order_id}",
                            "shipment_status": shipment_status,
                            # 1 shipment in 20 has no cost, which is what makes
                            # the rollup's "cost is a floor, not a total"
                            # warning fire.
                            "shipment_cost": None if rng.random() < 0.05 else Decimal(rng.randint(45, 180)),
                            "package_weight_grams": rng.randint(150, 2500),
                            "shipped_at": shipped_at,
                            "delivered_at": delivered_at,
                            "created_at": shipped_at,
                            "updated_at": shipped_at,
                        }
                    )

                if refunded_at is not None and rng.random() < 0.5:
                    return_rows.append(
                        {
                            "order_id": order_id,
                            "user_id": user_id,
                            "reason": rng.choice(["damaged", "wrong_item", "not_as_described"]),
                            "status": "refunded",
                            "refund_amount": total,
                            "requested_at": refunded_at - timedelta(days=2),
                            "refunded_at": refunded_at,
                            "created_at": refunded_at - timedelta(days=2),
                            "updated_at": refunded_at,
                        }
                    )

                # Funnel events. A real session emits several steps and only
                # some sessions convert, so the funnel narrows rather than being
                # a straight line of identical counts.
                session_key = f"{SESSION_PREFIX}{order_id}"
                steps = [
                    CartEventType.PRODUCT_VIEWED,
                    CartEventType.ITEM_ADDED,
                    CartEventType.CART_VIEWED,
                    CartEventType.CHECKOUT_STARTED,
                    CartEventType.SHIPPING_SUBMITTED,
                    CartEventType.PAYMENT_INITIATED,
                    CartEventType.ORDER_PLACED,
                ][: profile.events_per_order + 2]
                for step_no, step in enumerate(steps):
                    event_rows.append(
                        {
                            "occurred_at": created - timedelta(minutes=len(steps) - step_no),
                            "session_key": session_key,
                            "user_id": user_id,
                            "event_type": step,
                            "order_id": order_id if step == CartEventType.ORDER_PLACED else None,
                            "event_key": f"{EVENT_KEY_PREFIX}{order_id}-{step_no}",
                            "created_at": created,
                        }
                    )
                # Abandoned sessions: top-of-funnel only, no order behind them.
                if rng.random() < 0.6:
                    abandoned = f"{SESSION_PREFIX}a{order_id}"
                    for step_no, step in enumerate(
                        [CartEventType.PRODUCT_VIEWED, CartEventType.ITEM_ADDED, CartEventType.CART_VIEWED]
                    ):
                        event_rows.append(
                            {
                                "occurred_at": created - timedelta(minutes=10 - step_no),
                                "session_key": abandoned,
                                "user_id": None,
                                "event_type": step,
                                "order_id": None,
                                "event_key": f"{EVENT_KEY_PREFIX}a{order_id}-{step_no}",
                                "created_at": created,
                            }
                        )

            if len(order_rows) >= 5_000:
                flush_pending()
                log(f"seed: {totals['orders']}/{profile.orders} orders written")

            if sequence >= profile.orders:
                break

        flush_pending()

        # Opening stock for every product, dated before the window. Without a
        # ledger row on or before a bucket, inventory_daily excludes the product
        # from that bucket entirely and reports it as unknowable.
        opening = datetime.combine(first_day - timedelta(days=1), dtime.min, tzinfo=timezone.utc)
        for pid, _price, _cost in products:
            movement_rows.append(
                {
                    "product_id": pid,
                    "occurred_at": opening,
                    "movement_type": MovementType.INITIAL_SEED,
                    "delta": rng.randint(400, 3_000),
                    "ref_type": None,
                    "ref_id": None,
                    "event_key": f"{MOVEMENT_KEY_PREFIX}open-{pid}",
                    "created_at": opening,
                }
            )
            # A restock roughly every 20 days keeps levels from going negative
            # and gives `units_restocked` something to count.
            for week in range(0, profile.days, 20):
                when = datetime.combine(
                    first_day + timedelta(days=week), dtime.min, tzinfo=timezone.utc
                ) + timedelta(hours=4)
                movement_rows.append(
                    {
                        "product_id": pid,
                        "occurred_at": when,
                        "movement_type": MovementType.RETURN_RESTOCK,
                        "delta": rng.randint(20, 200),
                        "ref_type": None,
                        "ref_id": None,
                        "event_key": f"{MOVEMENT_KEY_PREFIX}r-{pid}-{week}",
                        "created_at": when,
                    }
                )

        flush_pending()

        summary = {
            "seeded": True,
            "profile": profile.name,
            "window_from": first_day.isoformat(),
            "window_to": last_day.isoformat(),
            **totals,
            "products": len(product_rows),
            "customers": len(customer_ids),
        }
        log("seed: done")
        return summary


def clean(*, rollups: bool = False, quiet: bool = False) -> dict[str, int]:
    """Remove everything `seed` created. Safe to run when nothing is present."""
    require_throwaway_database()

    from app.db.session import SessionLocal
    from app.models.analytics_control import AnalyticsSyncRun
    from app.models.analytics_facts import CartEvent, InventoryMovement
    from app.models.observability import SlowQuery
    from app.models.order import Order
    from app.models.product import Category, Product
    from app.models.return_request import ReturnRequest
    from app.models.user import User

    def log(message: str) -> None:
        if not quiet:
            print(message, flush=True)

    removed: dict[str, int] = {}
    with SessionLocal() as db:
        # Captured BEFORE the orders are deleted: the rollup cleanup below is
        # scoped to the date window the PERF dataset actually occupies, and the
        # orders' created_at range is the only durable record of that window.
        perf_window = db.execute(
            select(
                func.min(func.date(Order.created_at)),
                func.max(func.date(Order.created_at)),
            ).where(Order.order_number.like(f"{ORDER_NUMBER_PREFIX}%"))
        ).one()

        perf_orders = select(Order.id).where(
            Order.order_number.like(f"{ORDER_NUMBER_PREFIX}%")
        )
        # returns -> orders is RESTRICT, so returns go first. order_items,
        # order_payments, shipments and order_addresses are all CASCADE and go
        # with the order rows.
        removed["returns"] = int(
            db.execute(
                delete(ReturnRequest).where(ReturnRequest.order_id.in_(perf_orders))
            ).rowcount or 0
        )
        db.commit()
        removed["cart_events"] = int(
            db.execute(
                delete(CartEvent).where(CartEvent.event_key.like(f"{EVENT_KEY_PREFIX}%"))
            ).rowcount or 0
        )
        removed["inventory_movements"] = int(
            db.execute(
                delete(InventoryMovement).where(
                    InventoryMovement.event_key.like(f"{MOVEMENT_KEY_PREFIX}%")
                )
            ).rowcount or 0
        )
        db.commit()
        removed["orders"] = int(
            db.execute(
                delete(Order).where(Order.order_number.like(f"{ORDER_NUMBER_PREFIX}%"))
            ).rowcount or 0
        )
        db.commit()
        removed["products"] = int(
            db.execute(
                delete(Product).where(Product.sku.like(f"{SKU_PREFIX}%"))
            ).rowcount or 0
        )
        removed["users"] = int(
            db.execute(
                delete(User).where(User.email.like(f"%{EMAIL_DOMAIN}"))
            ).rowcount or 0
        )
        removed["categories"] = int(
            db.execute(
                delete(Category).where(Category.slug.like(f"{CATEGORY_SLUG_PREFIX}%"))
            ).rowcount or 0
        )
        removed["sync_runs"] = int(
            db.execute(
                delete(AnalyticsSyncRun).where(AnalyticsSyncRun.worker_id == WORKER_ID)
            ).rowcount or 0
        )
        removed["slow_queries"] = int(
            db.execute(
                delete(SlowQuery).where(SlowQuery.route.like(f"{SLOW_ROUTE_PREFIX}%"))
            ).rowcount or 0
        )
        db.commit()

        if rollups:
            # Derived tables. Cleaned only on request, because a developer
            # cleaning the transactional namespace usually still wants to look
            # at what the rollups produced.
            #
            # SCOPED to the PERF dataset's own date window, never `DELETE FROM
            # <table>` bare. The unscoped version emptied every rollup row on
            # the shared database — including rows other test suites had seeded
            # for their own assertions and a demo dataset's 90-day backfill —
            # every time `tests/perf` ran inside a full-suite invocation. A
            # cleanup that destroys OTHER owners' state is the exact defect the
            # analytics test conventions exist to prevent; rollup rows carry no
            # namespace column, so the window derived from the PERF orders
            # (captured above, before those orders were deleted) is the scope.
            # No PERF orders ⇒ seed produced nothing ⇒ nothing to clean.
            window_from, window_to = perf_window
            if window_from is not None:
                for table in (
                    "agg_order_daily",
                    "agg_order_hourly",
                    "agg_product_daily",
                    "agg_customer_daily",
                    "agg_customer_snapshot",
                    "agg_funnel_daily",
                    "agg_inventory_daily",
                    "agg_shipment_daily",
                ):
                    db.execute(
                        text(
                            f"DELETE FROM {table} "
                            "WHERE bucket_date BETWEEN :a AND :b"
                        ),
                        {"a": window_from, "b": window_to},
                    )
                db.commit()
                removed["rollup_tables_cleaned"] = 8
            else:
                removed["rollup_tables_cleaned"] = 0

    log(f"clean: {removed}")
    return removed


# ---------------------------------------------------------------------------
# Environment + dataset description
# ---------------------------------------------------------------------------
def _cgroup_memory_limit() -> str:
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            raw = Path(path).read_text().strip()
        except OSError:
            continue
        if raw in ("max", ""):
            return "unlimited"
        try:
            return f"{int(raw) / (1024 ** 3):.1f} GiB"
        except ValueError:
            return raw
    return "unknown"


def environment() -> dict[str, Any]:
    """Everything a reader needs to know before trusting a p95."""
    from app.db.session import SessionLocal

    info: dict[str, Any] = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "container_memory_limit": _cgroup_memory_limit(),
        "environment": settings.ENVIRONMENT,
        "mysql_host": settings.MYSQL_HOST,
        "obs_slow_query_ms": settings.OBS_SLOW_QUERY_MS,
        "analytics_v2_enabled": bool(settings.ANALYTICS_V2_ENABLED),
        "analytics_rollups_enabled": bool(settings.ANALYTICS_ROLLUPS_ENABLED),
        "analytics_tracking_enabled": bool(settings.ANALYTICS_TRACKING_ENABLED),
    }
    with SessionLocal() as db:
        info["mysql_version"] = db.execute(text("SELECT VERSION()")).scalar_one()
        for variable, key in (
            ("innodb_buffer_pool_size", "innodb_buffer_pool_size"),
            ("innodb_flush_log_at_trx_commit", "innodb_flush_log_at_trx_commit"),
        ):
            row = db.execute(text(f"SHOW VARIABLES LIKE '{variable}'")).first()
            if row is not None:
                info[key] = row[1]
    try:
        from app.db.redis import get_redis

        info["redis_version"] = get_redis().info("server").get("redis_version")
    except Exception as exc:  # noqa: BLE001 - a missing Redis is a finding, not a crash
        info["redis_version"] = f"unavailable: {exc}"
    return info


def dataset(db) -> dict[str, Any]:
    """Row counts and the real date span, measured rather than assumed."""
    from app.models.analytics_facts import CartEvent, InventoryMovement
    from app.models.order import Order, OrderItem
    from app.models.order_payment import OrderPayment
    from app.models.product import Product
    from app.models.return_request import ReturnRequest
    from app.models.shipment import Shipment
    from app.models.user import User

    counts = {
        "orders": int(db.execute(select(func.count(Order.id))).scalar_one()),
        "order_items": int(db.execute(select(func.count(OrderItem.id))).scalar_one()),
        "order_payments": int(db.execute(select(func.count(OrderPayment.id))).scalar_one()),
        "shipments": int(db.execute(select(func.count(Shipment.id))).scalar_one()),
        "returns": int(db.execute(select(func.count(ReturnRequest.id))).scalar_one()),
        "cart_events": int(db.execute(select(func.count(CartEvent.id))).scalar_one()),
        "inventory_movements": int(
            db.execute(select(func.count(InventoryMovement.id))).scalar_one()
        ),
        "products": int(db.execute(select(func.count(Product.id))).scalar_one()),
        "users": int(db.execute(select(func.count(User.id))).scalar_one()),
    }
    span = db.execute(
        select(func.min(Order.created_at), func.max(Order.created_at))
    ).one()
    counts["orders_from"] = span[0].isoformat() if span[0] else None
    counts["orders_to"] = span[1].isoformat() if span[1] else None
    if span[0] and span[1]:
        counts["order_days"] = (span[1].date() - span[0].date()).days + 1
    return counts


# ---------------------------------------------------------------------------
# Measurement: views
# ---------------------------------------------------------------------------
def _admin_user(db):
    """The disposable superadmin every view measurement runs as.

    `is_admin` short-circuits `User.has_permission`, so this holds every
    analytics grant including `analytics.jobs.run` (needed for `refresh=true`,
    which is how the cold path is measured) and `analytics.export`.
    """
    from app.models.user import User

    user = db.execute(select(User).where(User.email == ADMIN_EMAIL)).scalars().first()
    if user is None:
        raise RuntimeError(
            f"{ADMIN_EMAIL} does not exist — run `seed` before `measure`."
        )
    return user


def live_views() -> list[tuple[str, Any]]:
    """(module_slug, view) for every view that is not gated.

    Gated views are excluded because `resolve_view` returns their envelope
    without touching the database or the cache — timing them measures a dict
    literal. They are counted in the report so their absence is stated rather
    than implied.
    """
    from app.services.analytics import registry
    from app.services.analytics.types import GATED_STATES

    out = []
    for module in registry.MODULES:
        for view in module.views:
            if view.state not in GATED_STATES:
                out.append((module.slug, view))
    return out


def measure_views(repeat: int, period: str) -> dict[str, Any]:
    """Every LIVE/PARTIAL view, cold (cache miss) and warm (cache hit).

    Cold is `refresh=true`: it bypasses the cache read, runs the resolver, and
    writes the entry. Warm is the read immediately after, which is guaranteed to
    hit that entry. Both go through the real service, so the numbers include the
    permission check, the tz-generation read, envelope construction and the
    JSON round trip into and out of Redis.
    """
    from app.db.session import SessionLocal
    from app.services.analytics.filters import AnalyticsFilters, Period
    from app.services.analytics.view_service import AnalyticsViewService

    results: dict[str, Any] = {"period": period, "repeat": repeat, "views": []}
    targets = live_views()
    print(f"measure: {len(targets)} LIVE/PARTIAL views x {repeat} runs, period={period}", flush=True)

    for index, (module_slug, view) in enumerate(targets, start=1):
        cold: list[Sample] = []
        warm: list[Sample] = []
        error = ""
        hit_flags: list[bool] = []
        for _run in range(repeat):
            with SessionLocal() as db:
                user = _admin_user(db)
                service = AnalyticsViewService(db, user)
                op = f"view.cold:{module_slug}/{view.slug}"
                try:
                    with measured(op) as sample:
                        service.resolve_view(
                            module_slug,
                            view.slug,
                            AnalyticsFilters(period=Period(period), refresh=True),
                        )
                except Exception as exc:  # noqa: BLE001 - an unmeasurable view is a finding
                    error = f"{type(exc).__name__}: {exc}"
                    sample.error = error
                cold.append(sample)
                if error:
                    break

            with SessionLocal() as db:
                user = _admin_user(db)
                service = AnalyticsViewService(db, user)
                op = f"view.warm:{module_slug}/{view.slug}"
                try:
                    with measured(op) as sample:
                        envelope = service.resolve_view(
                            module_slug,
                            view.slug,
                            AnalyticsFilters(period=Period(period)),
                        )
                    cache_meta = getattr(envelope, "cache", None)
                    hit_flags.append(bool(cache_meta and cache_meta.hit))
                except Exception as exc:  # noqa: BLE001
                    sample.error = f"{type(exc).__name__}: {exc}"
                warm.append(sample)

        entry = {
            "module": module_slug,
            "view": view.slug,
            "number": view.number,
            "state": view.state.value,
            "resolver": view.resolver.value,
            "freshness": view.freshness.value,
            "exportable": bool(view.export),
            "cold": asdict(summarise(f"{module_slug}/{view.slug} cold", cold)),
            "warm": asdict(summarise(f"{module_slug}/{view.slug} warm", warm)),
            "warm_actually_hit": all(hit_flags) if hit_flags else False,
            "error": error,
        }
        results["views"].append(entry)
        flag = "!" if entry["cold"]["p95_ms"] > TARGET_COLD_SEC * 1000 else " "
        status = error or f'{entry["cold"]["p95_ms"]:>9.1f}ms cold / {entry["warm"]["p95_ms"]:>7.1f}ms warm'
        print(f"  [{index:>2}/{len(targets)}]{flag} {module_slug}/{view.slug}: {status}", flush=True)

    return results


# ---------------------------------------------------------------------------
# Measurement: aggregation
# ---------------------------------------------------------------------------
def measure_jobs(repeat: int) -> dict[str, Any]:
    """Each registered aggregation job, one bucket, at full volume.

    The bucket is a real day near the end of the seeded window, so the jobs that
    scan lifetime history (`customer_snapshot`, `inventory_daily`) are measured
    against the whole dataset rather than against its first week.
    """
    from app.db.session import SessionLocal
    from app.services.analytics.aggregation import JOBS, AggregationRunner
    from app.models.order import Order

    with SessionLocal() as db:
        newest = db.execute(select(func.max(Order.created_at))).scalar_one()
    if newest is None:
        return {"error": "no orders in the database — run `seed` first", "jobs": []}
    # One day back from the newest order: a fully-closed store-local day.
    bucket = (newest - timedelta(days=1)).date()

    out: dict[str, Any] = {"bucket_date": bucket.isoformat(), "repeat": repeat, "jobs": []}
    print(f"measure: {len(JOBS)} aggregation jobs x {repeat} runs on bucket {bucket}", flush=True)

    for name in sorted(JOBS):
        samples: list[Sample] = []
        rows_written = 0
        warnings: list[str] = []
        error = ""
        for _run in range(repeat):
            with SessionLocal() as db:
                runner = AggregationRunner(db, worker_id=WORKER_ID)
                try:
                    with measured(f"job:{name}") as sample:
                        result = runner.run_bucket(name, bucket)
                    rows_written = result.rows_written
                    warnings = list(result.warnings)
                except Exception as exc:  # noqa: BLE001
                    error = f"{type(exc).__name__}: {exc}"
                    sample.error = error
                samples.append(sample)
                if error:
                    break
        stats = asdict(summarise(f"job:{name}", samples))
        out["jobs"].append(
            {
                "job": name,
                "rows_written": rows_written,
                "warnings": warnings[:3],
                "error": error,
                **stats,
            }
        )
        print(f"  {name:<20} {stats['p95_ms']:>10.1f}ms p95  ({rows_written} rows) {error}", flush=True)
    return out


def measure_backfill(days: int) -> dict[str, Any]:
    """A full backfill of `days` buckets across every job.

    Run with a budget far larger than the production default so the TRUE cost is
    measured rather than the point at which the runner gives up. The report then
    states whether the production 45s budget would have covered it — which is
    the number that decides whether the admin "Recompute" button can finish
    behind nginx's 60s timeout.
    """
    from app.db.session import SessionLocal
    from app.services.analytics.aggregation import DEFAULT_BUDGET_MS, JOBS, AggregationRunner
    from app.models.order import Order

    with SessionLocal() as db:
        newest = db.execute(select(func.max(Order.created_at))).scalar_one()
    if newest is None:
        return {"error": "no orders in the database — run `seed` first"}

    date_to = newest.date()
    date_from = date_to - timedelta(days=days)
    names = sorted(JOBS)
    print(f"measure: backfill of {days} days x {len(names)} jobs [{date_from}, {date_to})", flush=True)

    with SessionLocal() as db:
        runner = AggregationRunner(db, worker_id=WORKER_ID)
        with measured("backfill") as sample:
            summary = runner.run_window(
                names,
                date_from,
                date_to,
                # 20 minutes: enough that the budget never bites, so the number
                # below is the real cost of the work rather than the budget.
                budget_ms=20 * 60 * 1000,
            )

    per_job = {
        name: {
            "status": entry["status"],
            "days_processed": entry["days_processed"],
            "rows_written": entry["rows_written"],
            "error": entry["error"],
        }
        for name, entry in summary["jobs"].items()
    }
    total_ms = sample.wall_ms
    return {
        "days": days,
        "window_from": date_from.isoformat(),
        "window_to": date_to.isoformat(),
        "jobs": names,
        "wall_ms": round(total_ms, 1),
        "db_ms": round(sample.db_ms, 1),
        "queries": sample.queries,
        "days_processed": summary["days_processed"],
        "rows_written": summary["rows_written"],
        "status": summary["status"],
        "per_job": per_job,
        "production_budget_ms": DEFAULT_BUDGET_MS,
        "fits_in_production_budget": total_ms <= DEFAULT_BUDGET_MS,
        "buckets_per_production_budget": (
            round(DEFAULT_BUDGET_MS / (total_ms / max(1, summary["days_processed"])), 1)
            if total_ms > 0
            else None
        ),
    }


# ---------------------------------------------------------------------------
# Measurement: cache hit rate
# ---------------------------------------------------------------------------
def measure_cache_mix(requests: int, seed_value: int = 4242) -> dict[str, Any]:
    """Hit rate under a realistic request mix.

    The mix is weighted the way a dashboard actually behaves: a small number of
    views are opened constantly (executive overview, real-time sales), the rest
    are visited occasionally, and the period selector is dominated by the two
    default presets. Nothing is pre-warmed and the real TTLs apply, so a
    `realtime` view whose 45s entry expires mid-run misses again — which is the
    behaviour a production hit rate has and a synthetic loop does not.
    """
    from app.db.session import SessionLocal
    from app.services.analytics.filters import AnalyticsFilters, Period
    from app.services.analytics.view_service import AnalyticsViewService

    rng = random.Random(seed_value)
    targets = live_views()
    if not targets:
        return {"error": "no LIVE/PARTIAL views"}

    # 20% of views take 70% of traffic. Ordered by registry number, which puts
    # the executive module first — the same views a real user lands on.
    hot_cut = max(1, len(targets) // 5)
    periods = [Period.LAST_30D] * 6 + [Period.LAST_7D] * 3 + [Period.LAST_90D] * 1

    hits = 0
    misses = 0
    errors = 0
    samples: list[Sample] = []
    started = time.perf_counter()
    for _n in range(requests):
        module_slug, view = (
            targets[rng.randrange(hot_cut)] if rng.random() < 0.7 else targets[rng.randrange(len(targets))]
        )
        period = periods[rng.randrange(len(periods))]
        with SessionLocal() as db:
            user = _admin_user(db)
            service = AnalyticsViewService(db, user)
            with measured(f"mix:{module_slug}/{view.slug}") as sample:
                try:
                    envelope = service.resolve_view(
                        module_slug, view.slug, AnalyticsFilters(period=period)
                    )
                    meta = getattr(envelope, "cache", None)
                    if meta is not None and meta.hit:
                        hits += 1
                    else:
                        misses += 1
                except Exception as exc:  # noqa: BLE001
                    errors += 1
                    sample.error = f"{type(exc).__name__}: {exc}"
        samples.append(sample)

    elapsed = time.perf_counter() - started
    stats = summarise("cache-mix", samples)
    return {
        "requests": requests,
        "distinct_views": len(targets),
        "hot_views": hot_cut,
        "hits": hits,
        "misses": misses,
        "errors": errors,
        "hit_rate_pct": round(100.0 * hits / max(1, hits + misses), 1),
        "elapsed_sec": round(elapsed, 1),
        "latency": asdict(stats),
    }


# ---------------------------------------------------------------------------
# Measurement: CSV export
# ---------------------------------------------------------------------------
def measure_exports() -> dict[str, Any]:
    """The export path at the server-side 50 000-row limit.

    Runs exactly what `POST /analytics/exports` runs: resolve the view uncached
    with `limit=CSV_ROW_CAP`, then `build_csv_export`. The audit write and the
    streaming response are excluded — they are bounded and tiny — and that
    exclusion is stated rather than hidden.
    """
    from app.db.session import SessionLocal
    from app.services.analytics.export import (
        CSV_ROW_CAP,
        CSV_TIME_BUDGET_SEC,
        build_csv_export,
    )
    from app.services.analytics.filters import AnalyticsFilters, Period
    from app.services.analytics.view_service import AnalyticsViewService

    out: dict[str, Any] = {
        "row_cap": CSV_ROW_CAP,
        "time_budget_sec": CSV_TIME_BUDGET_SEC,
        "nginx_read_timeout_sec": NGINX_READ_TIMEOUT_SEC,
        "exports": [],
    }
    targets = [(m, v) for m, v in live_views() if v.export and v.tables]
    print(f"measure: {len(targets)} exportable views at limit={CSV_ROW_CAP}", flush=True)

    for module_slug, view in targets:
        spec = view.tables[0]
        with SessionLocal() as db:
            user = _admin_user(db)
            service = AnalyticsViewService(db, user)
            entry: dict[str, Any] = {
                "module": module_slug,
                "view": view.slug,
                "table": spec.id,
            }
            try:
                with measured(f"export:{module_slug}/{view.slug}") as sample:
                    envelope = service.resolve_view(
                        module_slug,
                        view.slug,
                        AnalyticsFilters(
                            period=Period.LAST_90D
                        ).model_copy(update={"limit": CSV_ROW_CAP}),
                        use_cache=False,
                    )
                    block = envelope.tables.get(spec.id)
                    if block is None:
                        # Not a failure of the export path — the resolver
                        # returned no such table. Recorded by name so the
                        # report can say which views are unexportable in
                        # practice despite declaring `export=True`.
                        raise LookupError(
                            f"resolver produced no table {spec.id!r}; it returned "
                            f"{sorted(envelope.tables) or 'no tables at all'}"
                        )
                    export = build_csv_export(
                        view=view,
                        module_slug=module_slug,
                        table_id=spec.id,
                        table_spec=spec,
                        rows=block.rows,
                        resolved=envelope.filters,
                        exported_by=user.email,
                        timezone_name=envelope.timezone,
                        currency=envelope.currency,
                        availability=envelope.availability,
                        quality=envelope.quality,
                    )
                entry.update(
                    {
                        "rows": export.row_count,
                        "bytes": export.byte_size,
                        "truncated": export.truncated,
                        "truncation_reason": export.truncation_reason,
                        "wall_ms": round(sample.wall_ms, 1),
                        "db_ms": round(sample.db_ms, 1),
                        "queries": sample.queries,
                        "hit_row_cap": export.row_count >= CSV_ROW_CAP,
                        "over_time_budget": sample.wall_ms / 1000.0 > CSV_TIME_BUDGET_SEC,
                        "over_nginx_timeout": sample.wall_ms / 1000.0 > NGINX_READ_TIMEOUT_SEC,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                entry["error"] = f"{type(exc).__name__}: {exc}"
                entry["wall_ms"] = round(sample.wall_ms, 1)
        out["exports"].append(entry)
        print(
            f"  {module_slug}/{view.slug}: {entry.get('rows', '-')} rows in "
            f"{entry.get('wall_ms', 0):.1f}ms {entry.get('error', '')}",
            flush=True,
        )

    rows = [e.get("rows", 0) or 0 for e in out["exports"] if "rows" in e]
    out["max_rows_produced"] = max(rows) if rows else 0
    out["any_hit_row_cap"] = any(e.get("hit_row_cap") for e in out["exports"])
    out["any_over_time_budget"] = any(e.get("over_time_budget") for e in out["exports"])
    out["any_over_nginx_timeout"] = any(e.get("over_nginx_timeout") for e in out["exports"])
    return out


# ---------------------------------------------------------------------------
# Slow queries
# ---------------------------------------------------------------------------
def persist_slow_queries() -> int:
    """Write everything captured into `obs_slow_queries`, tagged `perf:<op>`.

    The real table, written by the real model, so the existing
    `/observability/slow-queries` dashboard can read this run's output with no
    special case. `clean` removes them on the `route` prefix.
    """
    if not _SLOW:
        return 0
    from app.db.session import SessionLocal
    from app.models.observability import SlowQuery

    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.add_all(
            [
                SlowQuery(
                    ts=now,
                    request_id=None,
                    route=f"{SLOW_ROUTE_PREFIX}{op}"[:255],
                    fingerprint_hash=record.fingerprint_hash,
                    sql_normalized=record.sql_normalized,
                    table_name=record.table_name,
                    operation=record.operation,
                    duration_ms=record.duration_ms,
                )
                for op, record in _SLOW
            ]
        )
        db.commit()
    return len(_SLOW)


def slow_query_report(top: int = 15) -> dict[str, Any]:
    """The heaviest queries, by single worst execution and by total time."""
    by_fingerprint: dict[str, dict[str, Any]] = {}
    for op, record in _SLOW:
        entry = by_fingerprint.setdefault(
            record.fingerprint_hash,
            {
                "fingerprint": record.fingerprint_hash,
                "table": record.table_name,
                "operation": record.operation,
                "sql": record.sql_normalized[:400],
                "count": 0,
                "total_ms": 0,
                "max_ms": 0,
                "ops": set(),
            },
        )
        entry["count"] += 1
        entry["total_ms"] += record.duration_ms
        entry["max_ms"] = max(entry["max_ms"], record.duration_ms)
        entry["ops"].add(op.split(":")[0])

    rows = []
    for entry in by_fingerprint.values():
        rows.append({**entry, "ops": sorted(entry["ops"])})
    return {
        "threshold_ms": settings.OBS_SLOW_QUERY_MS,
        "captured": len(_SLOW),
        "distinct_fingerprints": len(by_fingerprint),
        "by_total_time": sorted(rows, key=lambda r: -r["total_ms"])[:top],
        "by_worst_single": sorted(rows, key=lambda r: -r["max_ms"])[:top],
    }


# ---------------------------------------------------------------------------
# The measure command
# ---------------------------------------------------------------------------
def measure(args) -> dict[str, Any]:
    require_throwaway_database()

    from app.core.observability.instrumentation import register_engine_instrumentation
    from app.db.session import SessionLocal, engine
    from app.services.analytics import registry
    from app.services.analytics.types import GATED_STATES

    # The listeners are attached by create_app(); this script never builds the
    # FastAPI app, so attach them here. Idempotent by design.
    register_engine_instrumentation(engine)

    _SLOW.clear()
    report: dict[str, Any] = {"environment": environment()}
    with SessionLocal() as db:
        report["dataset"] = dataset(db)

    gated = [
        {"module": m.slug, "view": v.slug, "state": v.state.value}
        for m in registry.MODULES
        for v in m.views
        if v.state in GATED_STATES
    ]
    report["gated_views_not_measured"] = {
        "count": len(gated),
        "reason": (
            "resolve_view returns a GatedViewEnvelope for these without a query, "
            "a cache read or a cache write. Timing them measures dict "
            "construction, not the analytics stack."
        ),
        "views": gated,
    }

    # Warm the InnoDB buffer pool before timing anything. Without it the first
    # measured view pays for every page fault of the whole dataset and reports
    # a p95 nobody can reproduce.
    if not args.no_warmup:
        print("measure: warming the buffer pool", flush=True)
        with SessionLocal() as db:
            for table in ("orders", "order_items", "order_payments", "shipments"):
                db.execute(text(f"SELECT COUNT(*) FROM {table}"))

    def section(key: str, fn, *fn_args) -> None:
        """Run one measurement, recording a failure instead of losing the report.

        A section that blows up must not discard the four that already
        succeeded — and it must be visible in the output rather than absent,
        because a missing section reads as "not applicable" instead of "this
        broke".
        """
        if key not in args.only and "all" not in args.only:
            return
        try:
            report[_SECTION_KEYS[key]] = fn(*fn_args)
        except Exception as exc:  # noqa: BLE001
            print(f"measure: section {key!r} FAILED: {type(exc).__name__}: {exc}", flush=True)
            report[_SECTION_KEYS[key]] = {
                "error": f"{type(exc).__name__}: {exc}",
                "note": "this section did not complete; the numbers below exclude it",
            }

    section("jobs", measure_jobs, args.repeat)
    section("backfill", measure_backfill, args.backfill_days)
    section("views", measure_views, args.repeat, args.period)
    section("cache", measure_cache_mix, args.mix_requests)
    section("exports", measure_exports)

    report["slow_queries"] = slow_query_report()
    if args.persist_slow_queries:
        report["slow_queries"]["persisted_rows"] = persist_slow_queries()

    report["targets"] = {
        "warm_sec": TARGET_WARM_SEC,
        "cold_sec": TARGET_COLD_SEC,
        "nginx_read_timeout_sec": NGINX_READ_TIMEOUT_SEC,
    }
    report["verdict"] = verdict(report)
    return report


def verdict(report: dict[str, Any]) -> dict[str, Any]:
    """Targets met and missed, computed from the measurements, not asserted."""
    views = report.get("views", {}).get("views", [])
    warm_misses = [
        v for v in views if not v["error"] and v["warm"]["p95_ms"] > TARGET_WARM_SEC * 1000
    ]
    cold_misses = [
        v for v in views if not v["error"] and v["cold"]["p95_ms"] > TARGET_COLD_SEC * 1000
    ]
    nginx_risk = [
        v for v in views if not v["error"] and v["cold"]["max_ms"] > NGINX_READ_TIMEOUT_SEC * 1000
    ]
    unmeasurable = [v for v in views if v["error"]]
    slowest = sorted(
        (v for v in views if not v["error"]), key=lambda v: -v["cold"]["p95_ms"]
    )[:5]
    return {
        "views_measured": len([v for v in views if not v["error"]]),
        "views_unmeasurable": [
            {"module": v["module"], "view": v["view"], "error": v["error"]}
            for v in unmeasurable
        ],
        "warm_target_met": not warm_misses,
        "warm_misses": [
            {"view": f'{v["module"]}/{v["view"]}', "p95_ms": v["warm"]["p95_ms"]}
            for v in warm_misses
        ],
        "cold_target_met": not cold_misses,
        "cold_misses": [
            {"view": f'{v["module"]}/{v["view"]}', "p95_ms": v["cold"]["p95_ms"]}
            for v in cold_misses
        ],
        "would_504_behind_nginx": [
            {"view": f'{v["module"]}/{v["view"]}', "max_ms": v["cold"]["max_ms"]}
            for v in nginx_risk
        ],
        "slowest_cold_views": [
            {
                "view": f'{v["module"]}/{v["view"]}',
                "resolver": v["resolver"],
                "p95_ms": v["cold"]["p95_ms"],
                "db_ms_p50": v["cold"]["db_ms_p50"],
                "queries_p50": v["cold"]["queries_p50"],
            }
            for v in slowest
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_summary(report: dict[str, Any]) -> None:
    env = report["environment"]
    ds = report["dataset"]
    print("\n" + "=" * 78)
    print("ANALYTICS PERFORMANCE — SUMMARY")
    print("=" * 78)
    print(f"host        {env['platform']} / {env['cpu_count']} CPU / {env['container_memory_limit']}")
    print(f"mysql       {env.get('mysql_version')} buffer_pool={env.get('innodb_buffer_pool_size')}")
    print(
        f"dataset     {ds['orders']} orders, {ds['order_items']} lines, "
        f"{ds['shipments']} shipments over {ds.get('order_days')} days"
    )
    v = report.get("verdict", {})
    print(
        f"\ntargets     warm<{TARGET_WARM_SEC}s: "
        f"{'MET' if v.get('warm_target_met') else 'MISSED (' + str(len(v.get('warm_misses', []))) + ' views)'}"
        f"   cold<{TARGET_COLD_SEC}s: "
        f"{'MET' if v.get('cold_target_met') else 'MISSED (' + str(len(v.get('cold_misses', []))) + ' views)'}"
    )
    if v.get("would_504_behind_nginx"):
        print(f"NGINX 504   {len(v['would_504_behind_nginx'])} view(s) exceed {NGINX_READ_TIMEOUT_SEC}s")
    print("\nslowest cold views:")
    for row in v.get("slowest_cold_views", []):
        print(
            f"  {row['view']:<52} {row['p95_ms']:>9.1f}ms p95  "
            f"db={row['db_ms_p50']:.0f}ms q={row['queries_p50']:.0f} ({row['resolver']})"
        )
    if report.get("cache_mix"):
        cm = report["cache_mix"]
        print(f"\ncache mix   {cm.get('hit_rate_pct')}% hit over {cm.get('requests')} requests")
    if report.get("backfill"):
        bf = report["backfill"]
        print(
            f"backfill    {bf.get('days')}d x {len(bf.get('jobs', []))} jobs = "
            f"{bf.get('wall_ms', 0) / 1000:.1f}s "
            f"(production budget {bf.get('production_budget_ms', 0) / 1000:.0f}s: "
            f"{'fits' if bf.get('fits_in_production_budget') else 'DOES NOT FIT'})"
        )
    if report.get("exports"):
        ex = report["exports"]
        print(
            f"exports     max {ex.get('max_rows_produced')} rows produced "
            f"(cap {ex.get('row_cap')}); over 45s budget: {ex.get('any_over_time_budget')}"
        )
    print("=" * 78 + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="analytics_perf_report.py",
        description=(
            "Seed a realistic dataset and measure what the analytics subsystem "
            "actually costs. Refuses to run against anything but a local "
            "throwaway database."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  analytics_perf_report.py seed --profile full\n"
            "  analytics_perf_report.py measure --json logs/analytics-perf.json\n"
            "  analytics_perf_report.py all --profile small\n"
            "  analytics_perf_report.py clean --rollups\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    seed_p = sub.add_parser("seed", help="build the dataset (idempotent)")
    seed_p.add_argument("--profile", choices=sorted(PROFILES), default="full")
    seed_p.add_argument("--orders", type=int, help="override the profile's order count")
    seed_p.add_argument("--days", type=int, help="override the profile's day span")
    seed_p.add_argument("--force", action="store_true", help="rebuild even if present")
    seed_p.add_argument("--quiet", action="store_true")

    measure_p = sub.add_parser("measure", help="run the measurements")
    measure_p.add_argument("--repeat", type=int, default=3, help="runs per operation (default 3)")
    measure_p.add_argument("--period", default="30d", choices=["7d", "30d", "90d"])
    measure_p.add_argument("--backfill-days", type=int, default=30)
    measure_p.add_argument("--mix-requests", type=int, default=200)
    measure_p.add_argument(
        "--only",
        nargs="+",
        default=["all"],
        choices=["all", "views", "jobs", "backfill", "cache", "exports"],
    )
    measure_p.add_argument("--json", dest="json_path", help="write the full report here")
    measure_p.add_argument("--no-warmup", action="store_true")
    measure_p.add_argument(
        "--no-persist-slow-queries",
        dest="persist_slow_queries",
        action="store_false",
        help="do not write captured slow queries into obs_slow_queries",
    )
    measure_p.set_defaults(persist_slow_queries=True)

    clean_p = sub.add_parser("clean", help="remove everything seed created")
    clean_p.add_argument("--rollups", action="store_true", help="also empty the agg_* tables")
    clean_p.add_argument("--quiet", action="store_true")

    all_p = sub.add_parser("all", help="seed then measure")
    all_p.add_argument("--profile", choices=sorted(PROFILES), default="full")
    all_p.add_argument("--repeat", type=int, default=3)
    all_p.add_argument("--period", default="30d", choices=["7d", "30d", "90d"])
    all_p.add_argument("--backfill-days", type=int, default=30)
    all_p.add_argument("--mix-requests", type=int, default=200)
    all_p.add_argument("--only", nargs="+", default=["all"],
                       choices=["all", "views", "jobs", "backfill", "cache", "exports"])
    all_p.add_argument("--json", dest="json_path")
    all_p.add_argument("--force", action="store_true")
    all_p.add_argument("--no-warmup", action="store_true")
    all_p.add_argument("--quiet", action="store_true")
    all_p.set_defaults(persist_slow_queries=True)

    return parser


def _profile_from(args) -> SeedProfile:
    profile = PROFILES[args.profile]
    overrides: dict[str, Any] = {}
    if getattr(args, "orders", None):
        overrides["orders"] = args.orders
    if getattr(args, "days", None):
        overrides["days"] = args.days
    if overrides:
        profile = SeedProfile(**{**asdict(profile), **overrides})
    return profile


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        require_throwaway_database()
    except UnsafeDatabaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    if args.command == "seed":
        seed(_profile_from(args), force=args.force, quiet=args.quiet)
        return 0
    if args.command == "clean":
        clean(rollups=args.rollups, quiet=args.quiet)
        return 0
    if args.command == "all":
        seed(_profile_from(args), force=args.force, quiet=args.quiet)
    report = measure(args)
    if getattr(args, "json_path", None):
        path = Path(args.json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, default=str))
        print(f"measure: wrote {path}")
    _print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
