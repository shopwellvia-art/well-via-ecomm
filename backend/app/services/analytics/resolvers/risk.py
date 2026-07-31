"""Views 42 (fraud and risk) and 71 (website speed) — the two internal-telemetry views.

Both views sit outside the rollup pipeline, which is why they were the last two
unbound ones. ``AnalyticsRepository`` reflects its allowlist from the fifteen
``agg_*`` models, so ``payment_events``, ``orders``, ``order_addresses``,
``obs_request_logs`` and ``obs_slow_queries`` are unreachable through it by
construction. They are read here through ``ctx.db`` instead, exactly as
``control_centre.anomaly_feed`` reads ``analytics_alerts`` — server-trusted
model classes, never a client-supplied table name.

--------------------------------------------------------------------------
View 42 — what it shows, and the two things it deliberately refuses to do
--------------------------------------------------------------------------

**It does not compute a fraud score.** A weighted composite of "3 x failures +
2 x velocity + 1 x address mismatch" reads as a measurement, ranks orders as if
the ordering meant something, and its coefficients would be invented here and
never validated against a single confirmed fraud case — this deployment has no
labelled outcomes to fit against. What is emitted instead is one row per
(order, signal) with the observed count behind it, so the operator sees *why* an
order surfaced and applies their own judgement. Every number on the screen is a
count of something that actually happened.

**It cannot see chargebacks or disputes, and says so.** The registry entry is
``PARTIAL`` with ``requires=(GATEWAY_SETTLEMENT_API,)`` for that reason and no
other. ``app/models/analytics_settlement.py`` defines ``payment_settlements``,
whose ``SettlementTxnType`` carries the chargeback sign convention — but the
table is not present in this deployment's schema, and even where it is, it is
populated from an uploaded gateway settlement report (view 64, also gated on
``GATEWAY_SETTLEMENT_API``). A dispute count is therefore not "currently zero",
it is unobservable, and a fraud screen rendering LIVE while blind to disputes
is the green-health-check failure mode ``control_centre`` warns about.

The seven signals, all of them direct reads of the transactional record:

===============================  =========================================
signal                           definition
===============================  =========================================
``repeated_payment_failure``     >= :data:`REPEATED_FAILURE_MIN` payment
                                 events with ``payment_status='failed'``
                                 against one order inside the window.
``gateway_amount_mismatch``      >= 1 ``amount_mismatch`` event: the
                                 gateway reported an amount that is not the
                                 amount we expected.
``unsigned_webhook``             >= 1 ``webhook_signature_invalid`` event:
                                 something posted a callback for this order
                                 that did not carry a valid signature.
``settled_after_cancel``         >= 1 ``settled_after_cancel`` event: money
                                 captured against an already-cancelled
                                 order.
``customer_order_velocity``      the ordering customer placed >=
                                 :data:`VELOCITY_ORDERS` orders inside any
                                 :data:`VELOCITY_WINDOW_HOURS` span.
``billing_shipping_mismatch``    the order carries both a SHIPPING and a
                                 BILLING address snapshot and their pincodes
                                 differ.
``cancelled_after_payment``      the order reached CANCELLED/REFUNDED with a
                                 ``paid_at`` already set.
===============================  =========================================

Every threshold above is a module constant, named and documented, rather than a
literal buried in a query — a signal whose trigger point cannot be read off is
a signal nobody can calibrate. They are NOT in the registry's ``params``:
``tests/test_analytics_view_bindings.py`` closes that dict to the keys some
resolver actually reads, and smuggling a threshold through a key that means
something else would be worse than a constant.

Two things are counted but cannot be flagged, and are reported as warnings
rather than dropped:

* payment events whose ``order_id`` is NULL — an unsigned webhook that matched
  no order is the *more* interesting one, and it has no row to attach to;
* orders past :data:`MAX_ORDERS_SCANNED` — the scan is bounded, and a bounded
  scan that does not say it was bounded is a top-N presented as a population.

--------------------------------------------------------------------------
View 71 — percentiles, and why the obvious implementation is wrong
--------------------------------------------------------------------------

**Percentiles are neither summable nor averageable.** A stored daily p95
averaged into a weekly p95 is not the weekly p95 and there is no correction
factor — the weekly figure depends on the whole joint distribution, which the
daily summaries have thrown away. Two implementations are defensible: keep a
histogram / bucket-count structure that re-aggregates, or compute over the raw
rows each time. This adds no models and no migrations, so there is no histogram
to keep:

    **Every percentile here is computed by nearest-rank over the raw
    ``obs_request_logs.total_ms`` values inside the bucket being reported, and
    the window-level figures are computed over the window's raw rows — never
    from the daily points.**

:func:`percentile` is the nearest-rank definition: sort ascending, take the
value at ``ceil(q/100 * n)``. No interpolation, so the answer is always a
latency that was actually observed.

The bound is :data:`MAX_LATENCY_SAMPLE` request rows per window. Past it the
percentiles are **refused** — reported as ``None`` with the bound named in
``inputs_missing`` and a ``LATENCY_SAMPLE_EXCEEDED`` warning — while the
additive figures (requests, errors, throughput, slow queries) are still
reported, because those *are* summable at any size. Sampling instead, or
silently truncating to the first N rows by time, would return a precise-looking
p95 of the first hour of the window.

**This is not what a user experiences.** ``obs_request_logs`` is written by
``TimingMiddleware`` and measures server time between request receipt and
response start. There is no TTFB as the browser saw it, no LCP, no CLS, no INP
— those need real user monitoring in the page, which is what view 71's
``requires=(GA4_DATA_API,)`` names. Nothing here may be read as Core Web Vitals
and the registry's ``limitation`` says so in the words the admin sees.

**Admin traffic is separated, not silently mixed.** An admin export that runs
for eleven seconds is not a storefront latency problem, and averaged in it makes
the storefront look broken. :func:`classify_route` sorts every route template
into ``storefront`` / ``admin`` / ``internal``; the charts show storefront only,
and ``latency_summary`` carries one row per class so the admin numbers stay
visible and separable rather than deleted. The classification is deliberately
conservative — a route that is not clearly admin-only stays ``storefront``,
because over-claiming which traffic is internal is how a real customer-facing
regression gets filtered out of the graph.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Sequence

from sqlalchemy import func, select

from app.models.observability import RequestLog, SlowQuery
from app.models.order import Order, OrderStatus
from app.models.order_address import OrderAddress, OrderAddressType
from app.models.payment_event import PaymentEvent, PaymentEventType
from app.schemas.analytics_view import (
    AnalyticsWarning,
    SourceRef,
    TableBlock,
    WarningCode,
)
from app.services.analytics.export import clamp_row_limit
from app.services.analytics.filters import Granularity
from app.services.analytics.resolvers.base import (
    ResolverContext,
    ResolverResult,
    source_ref,
    warn,
)
from app.services.analytics.resolvers.core import compute_kpis
from app.services.analytics.resolvers.special import custom_function
from app.services.analytics.timebox import range_bounds_utc, store_timezone
from app.services.analytics.types import MetricQuality

__all__ = [
    "REPEATED_FAILURE_MIN",
    "VELOCITY_ORDERS",
    "VELOCITY_WINDOW_HOURS",
    "MAX_ORDERS_SCANNED",
    "MAX_LATENCY_SAMPLE",
    "RISK_SIGNALS",
    "ROUTE_CLASSES",
    "ROUTE_CLASS_ADMIN",
    "ROUTE_CLASS_INTERNAL",
    "ROUTE_CLASS_STOREFRONT",
    "UNATTRIBUTED_PAYMENT_EVENTS",
    "LATENCY_SAMPLE_EXCEEDED",
    "ORDER_SCAN_TRUNCATED",
    "classify_route",
    "percentile",
    "risk_signals",
    "request_performance",
]


# ---------------------------------------------------------------------------
# Warning codes
# ---------------------------------------------------------------------------
# Plain strings, per `WarningCode`'s own docstring: a new code needs no
# migration and no coordinated frontend release, and an unrecognised one still
# renders its message.

#: Payment events that matched no order. They cannot be flagged against a row,
#: so they are counted here rather than silently dropped.
UNATTRIBUTED_PAYMENT_EVENTS = "UNATTRIBUTED_PAYMENT_EVENTS"

#: The window holds more request rows than the percentile scan is allowed to
#: read. Percentiles are withheld; the additive figures are not.
LATENCY_SAMPLE_EXCEEDED = "LATENCY_SAMPLE_EXCEEDED"

#: The order scan hit its cap, so the flagged set is drawn from a prefix of the
#: window's orders rather than all of them.
ORDER_SCAN_TRUNCATED = "ORDER_SCAN_TRUNCATED"


# ---------------------------------------------------------------------------
# View 42 — thresholds
# ---------------------------------------------------------------------------
# Named and documented rather than inlined. A signal whose trigger point cannot
# be read off the module is a signal nobody can calibrate or argue with.

#: Failed payment events against ONE order before it is worth a look. Two is
#: an ordinary mistyped CVV; three is a pattern.
REPEATED_FAILURE_MIN = 3

#: Orders by one customer inside `VELOCITY_WINDOW_HOURS` before the burst is
#: flagged. Not a fraud verdict — a legitimate bulk buyer trips this too, which
#: is exactly why the row shows the count and lets the operator decide.
VELOCITY_ORDERS = 3
VELOCITY_WINDOW_HOURS = 24

#: Upper bound on the order rows one request may scan. Velocity needs every
#: order in the window (not just the flagged ones) to count a burst, so the
#: scan cannot be narrowed by a WHERE clause; it is bounded instead, and the
#: envelope says when the bound bit.
MAX_ORDERS_SCANNED = 50_000

SIGNAL_REPEATED_FAILURE = "repeated_payment_failure"
SIGNAL_AMOUNT_MISMATCH = "gateway_amount_mismatch"
SIGNAL_UNSIGNED_WEBHOOK = "unsigned_webhook"
SIGNAL_SETTLED_AFTER_CANCEL = "settled_after_cancel"
SIGNAL_ORDER_VELOCITY = "customer_order_velocity"
SIGNAL_BILLING_MISMATCH = "billing_shipping_mismatch"
SIGNAL_CANCELLED_AFTER_PAYMENT = "cancelled_after_payment"

#: Every signal this view can raise, in the order the chart lists them when
#: counts tie. Exported so a test can assert the set has not silently grown a
#: composite score.
RISK_SIGNALS: tuple[str, ...] = (
    SIGNAL_REPEATED_FAILURE,
    SIGNAL_AMOUNT_MISMATCH,
    SIGNAL_UNSIGNED_WEBHOOK,
    SIGNAL_SETTLED_AFTER_CANCEL,
    SIGNAL_ORDER_VELOCITY,
    SIGNAL_BILLING_MISMATCH,
    SIGNAL_CANCELLED_AFTER_PAYMENT,
)

#: Statuses that mean the order was undone after money had already been taken.
_UNDONE_STATUSES = (OrderStatus.CANCELLED, OrderStatus.REFUNDED)


# ---------------------------------------------------------------------------
# View 71 — route classification and percentile bound
# ---------------------------------------------------------------------------

ROUTE_CLASS_STOREFRONT = "storefront"
ROUTE_CLASS_ADMIN = "admin"
ROUTE_CLASS_INTERNAL = "internal"

#: Ordered worst-to-best for display; also the row order of `latency_summary`.
ROUTE_CLASSES: tuple[str, ...] = (
    ROUTE_CLASS_STOREFRONT,
    ROUTE_CLASS_ADMIN,
    ROUTE_CLASS_INTERNAL,
)

#: Route templates under these prefixes are admin-only surfaces. Deliberately
#: short and conservative: anything ambiguous (`/coupons`, `/settings`,
#: `/payment-methods`) has a public read path and stays `storefront`. Filtering
#: real customer traffic out of a storefront graph by over-claiming is a worse
#: error than leaving a little admin traffic in it, because the first one hides
#: a regression and the second one only dilutes it.
_ADMIN_PREFIXES: tuple[str, ...] = (
    "/api/v1/analytics",
    "/api/v1/audit-events",
    "/api/v1/observability",
    "/api/v1/dashboard",
    "/api/v1/roles",
    "/api/v1/users",
    "/api/v1/email-templates",
    "/api/v1/returns/admin",
)

#: Anything containing this segment is an admin surface wherever it is mounted
#: (`/api/v1/admin/database`, `/api/v1/admin/payment-methods`, ...).
_ADMIN_SEGMENT = "/admin/"

#: Probes and generated docs. Not traffic anybody experiences.
_INTERNAL_EXACT: frozenset[str] = frozenset(
    {"/", "/health", "/healthz", "/readyz", "/livez", "/metrics",
     "/docs", "/redoc", "/openapi.json"}
)

#: The mount point of the versioned API. A route outside it is not a page or an
#: endpoint a shopper hits.
_API_PREFIX = "/api/"

#: Request rows one percentile scan may read. Past this the percentiles are
#: withheld rather than approximated — see the module docstring.
MAX_LATENCY_SAMPLE = 200_000

#: HTTP status at or above which a response counts as an error. 4xx is included
#: on purpose: a storefront returning 404 for a product page is a defect the
#: shopper experiences, and a graph that only counts 5xx would render it flat.
ERROR_STATUS_FLOOR = 400

_PCT_Q = Decimal("0.0001")
_HUNDRED = Decimal("100")


def classify_route(route: str | None) -> str:
    """Sort one route template into storefront / admin / internal.

    Takes the FastAPI path template (``/api/v1/products/{product_id}``) that
    ``TimingMiddleware`` records, not a concrete URL, so there is nothing
    per-request in the key and no cardinality explosion.
    """
    if not route:
        return ROUTE_CLASS_INTERNAL
    path = route.split("?", 1)[0]
    if path in _INTERNAL_EXACT:
        return ROUTE_CLASS_INTERNAL
    if not path.startswith(_API_PREFIX):
        return ROUTE_CLASS_INTERNAL
    if _ADMIN_SEGMENT in path:
        return ROUTE_CLASS_ADMIN
    for prefix in _ADMIN_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return ROUTE_CLASS_ADMIN
    return ROUTE_CLASS_STOREFRONT


def percentile(values: Sequence[int | float], q: float) -> int | None:
    """Nearest-rank percentile over raw observations. ``None`` for no data.

    Sort ascending and take the value at rank ``ceil(q/100 * n)``, 1-based. No
    interpolation: the answer is always a latency that was genuinely observed,
    which matters because the reader will go looking for the request that
    produced it.

    Callers must pass the RAW values for the bucket being reported. Feeding this
    a list of per-day percentiles produces a percentile-of-percentiles, which is
    not the window's percentile and is wrong in an unbounded direction.
    """
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    rank = math.ceil(q / 100.0 * n)
    index = min(max(rank, 1), n) - 1
    return int(ordered[index])


def _bucket_start(day: date, granularity: Granularity) -> date:
    """The bucket a reporting day belongs to.

    Mirrors ``core._bucket_start``. Restated rather than imported because that
    one is private to the rollup path and this module buckets RAW rows, not
    stored daily aggregates — the two must be free to diverge.
    """
    if granularity is Granularity.WEEK:
        return day - timedelta(days=day.weekday())
    if granularity is Granularity.MONTH:
        return day.replace(day=1)
    return day


def _pct(numerator: int, denominator: int) -> Decimal | None:
    """A percentage, or None when the base is empty.

    Never 0. A bucket with no requests has no error rate; drawing 0% there is a
    claim that everything succeeded.
    """
    if denominator <= 0:
        return None
    return (Decimal(numerator) / Decimal(denominator) * _HUNDRED).quantize(_PCT_Q)


def _window_bounds(ctx: ResolverContext) -> tuple[datetime, datetime]:
    """The request window as a half-open UTC instant range.

    ``obs_request_logs.ts``, ``payment_events.created_at`` and
    ``orders.created_at`` are UTC instants, while the window is in store-local
    reporting days. Comparing the two directly shifts every boundary by the
    store's offset — 5.5 hours here, which moves a fifth of a day's rows into
    the neighbouring bucket.
    """
    tz = store_timezone(ctx.db)
    return range_bounds_utc(ctx.window.date_from, ctx.window.date_to, tz)


def _local_day(moment: datetime, tz: Any) -> date:
    """Store-local reporting day for a UTC instant read out of the database.

    MySQL hands back naive datetimes for ``DateTime(timezone=True)`` columns;
    they are UTC by the writer's contract, so the tzinfo is attached here rather
    than assumed by ``astimezone``, which would otherwise read them as local.
    """
    from datetime import timezone as _tz

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_tz.utc)
    return moment.astimezone(tz).date()


# ===========================================================================
# View 42 — fraud and risk analytics
# ===========================================================================


def _payment_signal_rows(ctx: ResolverContext, lo: datetime, hi: datetime):
    """Per-order payment-event counts for the window, in one grouped query.

    NULL ``order_id`` rows are excluded here and counted separately: an event
    that matched no order has no row to attach to, but it is not nothing.
    """
    failed = func.sum(
        func.if_(PaymentEvent.payment_status == "failed", 1, 0)
    ).label("failed_events")
    mismatch = func.sum(
        func.if_(PaymentEvent.event_type == PaymentEventType.AMOUNT_MISMATCH, 1, 0)
    ).label("mismatch_events")
    unsigned = func.sum(
        func.if_(
            PaymentEvent.event_type == PaymentEventType.WEBHOOK_SIGNATURE_INVALID, 1, 0
        )
    ).label("unsigned_events")
    after_cancel = func.sum(
        func.if_(
            PaymentEvent.event_type == PaymentEventType.SETTLED_AFTER_CANCEL, 1, 0
        )
    ).label("after_cancel_events")

    stmt = (
        select(PaymentEvent.order_id, failed, mismatch, unsigned, after_cancel)
        .where(
            PaymentEvent.created_at >= lo,
            PaymentEvent.created_at < hi,
            PaymentEvent.order_id.is_not(None),
        )
        .group_by(PaymentEvent.order_id)
    )
    return ctx.db.execute(stmt).all()


def _unattributed_events(ctx: ResolverContext, lo: datetime, hi: datetime) -> dict[str, int]:
    """Risk-bearing payment events in the window that matched no order."""
    stmt = (
        select(PaymentEvent.event_type, func.count())
        .where(
            PaymentEvent.created_at >= lo,
            PaymentEvent.created_at < hi,
            PaymentEvent.order_id.is_(None),
            PaymentEvent.event_type.in_(
                (
                    PaymentEventType.WEBHOOK_SIGNATURE_INVALID,
                    PaymentEventType.AMOUNT_MISMATCH,
                    PaymentEventType.SETTLED_AFTER_CANCEL,
                )
            ),
        )
        .group_by(PaymentEvent.event_type)
    )
    return {str(row[0]): int(row[1] or 0) for row in ctx.db.execute(stmt).all()}


def _address_mismatch_ids(ctx: ResolverContext, order_ids: Sequence[int]) -> set[int]:
    """Orders whose BILLING and SHIPPING snapshots disagree on the pincode.

    A NULL billing snapshot means "same as shipping" (``order_addresses`` and
    ``orders.billing_address_snapshot`` both document that), so only orders
    carrying both rows can mismatch. Comparing free-text address lines would
    flag every abbreviation; the pincode is a normalised field and either
    matches or does not.
    """
    if not order_ids:
        return set()
    shipping = OrderAddress.__table__.alias("ship")
    billing = OrderAddress.__table__.alias("bill")
    stmt = (
        select(shipping.c.order_id)
        .join(billing, billing.c.order_id == shipping.c.order_id)
        .where(
            shipping.c.address_type == OrderAddressType.SHIPPING.name,
            billing.c.address_type == OrderAddressType.BILLING.name,
            shipping.c.order_id.in_(order_ids),
            shipping.c.pincode.is_not(None),
            billing.c.pincode.is_not(None),
            func.trim(shipping.c.pincode) != func.trim(billing.c.pincode),
        )
        .distinct()
    )
    return {int(r[0]) for r in ctx.db.execute(stmt).all()}


def _velocity_ids(orders: Sequence[Any]) -> dict[int, int]:
    """Orders belonging to a burst: >= N orders by one customer within H hours.

    A sliding window over each customer's orders sorted by time. Every order
    inside a qualifying burst is flagged, not just the last one, because the
    operator needs the whole cluster to judge it — and the count reported is the
    size of the burst that order sat in.
    """
    by_customer: dict[int, list[Any]] = defaultdict(list)
    for order in orders:
        if order.user_id is not None and order.created_at is not None:
            by_customer[int(order.user_id)].append(order)

    span = timedelta(hours=VELOCITY_WINDOW_HOURS)
    flagged: dict[int, int] = {}
    for placed in by_customer.values():
        placed.sort(key=lambda o: o.created_at)
        left = 0
        for right in range(len(placed)):
            while placed[right].created_at - placed[left].created_at > span:
                left += 1
            size = right - left + 1
            if size >= VELOCITY_ORDERS:
                for order in placed[left : right + 1]:
                    # max(): an order can sit in several overlapping bursts;
                    # report the largest one it belonged to.
                    flagged[int(order.id)] = max(flagged.get(int(order.id), 0), size)
    return flagged


def _order_no(order: Any) -> str:
    return order.order_number or f"#{order.id}"


def _placed_at(order: Any) -> str | None:
    return order.created_at.isoformat() if order.created_at is not None else None


@custom_function("risk_signals")
def risk_signals(ctx: ResolverContext) -> ResolverResult:
    """View 42 — observable risk signals, one row per (order, signal).

    No score, no ranking model, no coefficients. See the module docstring for
    why, and for why the view's registry state is PARTIAL rather than LIVE.
    """
    lo, hi = _window_bounds(ctx)
    warnings: list[AnalyticsWarning] = []

    # -- the population ----------------------------------------------------
    # Bounded, and one row past the bound so the truncation is observable
    # rather than inferred.
    order_rows = list(
        ctx.db.execute(
            select(
                Order.id,
                Order.order_number,
                Order.user_id,
                Order.total_amount,
                Order.created_at,
                Order.status,
                Order.paid_at,
            )
            .where(Order.created_at >= lo, Order.created_at < hi)
            .order_by(Order.created_at, Order.id)
            .limit(MAX_ORDERS_SCANNED + 1)
        ).all()
    )
    scan_truncated = len(order_rows) > MAX_ORDERS_SCANNED
    if scan_truncated:
        order_rows = order_rows[:MAX_ORDERS_SCANNED]
        warnings.append(
            warn(
                ORDER_SCAN_TRUNCATED,
                f"More than {MAX_ORDERS_SCANNED:,} orders fall in this window; the "
                "signals below are computed over the earliest "
                f"{MAX_ORDERS_SCANNED:,} of them. Narrow the date range for a "
                "complete answer — this is a bounded scan, not a full population.",
                severity="warn",
                scanned=MAX_ORDERS_SCANNED,
            )
        )

    by_id = {int(o.id): o for o in order_rows}
    order_ids = list(by_id)

    # -- signals -----------------------------------------------------------
    # order id -> signal -> observed count. A count of 0 never becomes a row.
    hits: dict[int, dict[str, int]] = defaultdict(dict)

    for row in _payment_signal_rows(ctx, lo, hi):
        order_id = int(row.order_id)
        if order_id not in by_id:
            # The event is in the window but its order is not (placed earlier,
            # or past the scan bound). Nothing to attach it to.
            continue
        failed = int(row.failed_events or 0)
        if failed >= REPEATED_FAILURE_MIN:
            hits[order_id][SIGNAL_REPEATED_FAILURE] = failed
        if int(row.mismatch_events or 0):
            hits[order_id][SIGNAL_AMOUNT_MISMATCH] = int(row.mismatch_events)
        if int(row.unsigned_events or 0):
            hits[order_id][SIGNAL_UNSIGNED_WEBHOOK] = int(row.unsigned_events)
        if int(row.after_cancel_events or 0):
            hits[order_id][SIGNAL_SETTLED_AFTER_CANCEL] = int(row.after_cancel_events)

    for order_id, burst in _velocity_ids(order_rows).items():
        hits[order_id][SIGNAL_ORDER_VELOCITY] = burst

    for order_id in _address_mismatch_ids(ctx, order_ids):
        if order_id in by_id:
            hits[order_id][SIGNAL_BILLING_MISMATCH] = 1

    for order_id, order in by_id.items():
        if order.status in _UNDONE_STATUSES and order.paid_at is not None:
            hits[order_id][SIGNAL_CANCELLED_AFTER_PAYMENT] = 1

    # -- events with no order ----------------------------------------------
    orphans = _unattributed_events(ctx, lo, hi)
    if orphans:
        total = sum(orphans.values())
        warnings.append(
            warn(
                UNATTRIBUTED_PAYMENT_EVENTS,
                f"{total} payment event(s) in this window matched no order "
                f"({', '.join(f'{k}={v}' for k, v in sorted(orphans.items()))}). "
                "They cannot be listed against an order and are NOT in the counts "
                "below — an unsigned callback that matched nothing is usually the "
                "more interesting one.",
                severity="warn",
                **{k: v for k, v in sorted(orphans.items())},
            )
        )

    # -- shaping -----------------------------------------------------------
    rows = [
        {
            "order_no": _order_no(by_id[order_id]),
            "signal": signal,
            "observed": observed,
            "value": by_id[order_id].total_amount,
            "placed_at": _placed_at(by_id[order_id]),
        }
        for order_id, signals in hits.items()
        for signal, observed in signals.items()
    ]
    # Highest order value first, matching the table's declared default sort;
    # the signal name breaks ties so the order is stable between requests.
    rows.sort(key=lambda r: (-(r["value"] or Decimal("0")), r["signal"], r["order_no"]))

    limit = clamp_row_limit(ctx.filters.limit)
    truncated = len(rows) > limit
    total_rows = len(rows)
    if truncated:
        warnings.append(
            warn(
                WarningCode.SMALL_SAMPLE,
                f"Showing the {limit} highest-value flagged rows of {total_rows}.",
                severity="info",
                limit=limit,
                total_rows=total_rows,
            )
        )

    # The chart counts DISTINCT ORDERS per signal over the whole flagged set,
    # not the truncated table — a bar chart that shrank when somebody changed
    # the page size would be reporting the page, not the period.
    per_signal: dict[str, int] = defaultdict(int)
    for signals in hits.values():
        for signal in signals:
            per_signal[signal] += 1
    signal_points = [
        {"signal": signal, "orders": per_signal[signal]}
        for signal in RISK_SIGNALS
        if per_signal.get(signal)
    ]
    signal_points.sort(key=lambda p: (-p["orders"], p["signal"]))

    tables = {
        "flagged_orders": TableBlock(
            rows=rows[:limit], total_rows=total_rows, truncated=truncated
        )
    }

    # -- provenance --------------------------------------------------------
    watermark = ctx.db.execute(
        select(func.max(PaymentEvent.created_at)).where(
            PaymentEvent.created_at >= lo, PaymentEvent.created_at < hi
        )
    ).scalar()
    tz = store_timezone(ctx.db)
    sources: list[SourceRef] = [
        source_ref(
            "payment_events",
            label="Payment event log",
            kind="live",
            rows=sum(len(s) for s in hits.values()),
            through=_local_day(watermark, tz) if watermark is not None else None,
        ),
        source_ref(
            "orders",
            label="Orders",
            kind="live",
            rows=len(by_id),
            through=(
                _local_day(max(o.created_at for o in order_rows), tz)
                if order_rows
                else None
            ),
        ),
    ]

    # The three KPI cards keep the catalogue's definitions — `rto_rate`,
    # `payment_success_rate` and `cancellation_rate` are already bound to
    # agg_shipment_daily / agg_payment_daily / agg_order_daily in
    # `core.METRIC_BINDINGS`. Restating them here would be a second definition
    # of a published metric, which is how two screens end up disagreeing.
    bundle = compute_kpis(ctx, ctx.view.kpis)

    return ResolverResult(
        kpis=bundle.kpis,
        series={"risk_signals": signal_points},
        tables=tables,
        sources=[*sources, *bundle.sources],
        warnings=[*warnings, *bundle.warnings],
        # AUTHORITATIVE for the signal counts themselves: every one is a direct
        # read of the append-only payment log or the order record. The view's
        # published quality is still the worst of these and the KPI cards, which
        # `rolled_up()` takes care of.
        quality=MetricQuality.AUTHORITATIVE,
    ).rolled_up()


# ===========================================================================
# View 71 — website speed and technical performance
# ===========================================================================


def _request_rows(ctx: ResolverContext, lo: datetime, hi: datetime):
    """Raw request rows for the window. Bounded by the caller, never sampled."""
    return ctx.db.execute(
        select(RequestLog.ts, RequestLog.route, RequestLog.status, RequestLog.total_ms)
        .where(RequestLog.ts >= lo, RequestLog.ts < hi)
    ).all()


def _slow_query_rows(ctx: ResolverContext, lo: datetime, hi: datetime, limit: int):
    """Slow-query fingerprints for the window, worst total time first."""
    occurrences = func.count().label("occurrences")
    total_ms = func.sum(SlowQuery.duration_ms).label("total_ms")
    max_ms = func.max(SlowQuery.duration_ms).label("max_ms")
    stmt = (
        select(
            SlowQuery.fingerprint_hash,
            SlowQuery.table_name,
            SlowQuery.operation,
            occurrences,
            total_ms,
            max_ms,
        )
        .where(SlowQuery.ts >= lo, SlowQuery.ts < hi)
        .group_by(SlowQuery.fingerprint_hash, SlowQuery.table_name, SlowQuery.operation)
        .order_by(total_ms.desc())
        .limit(limit)
    )
    return ctx.db.execute(stmt).all()


def _latency_block(
    latencies: Sequence[int], requests: int, errors: int, *, percentiles_ok: bool
) -> dict[str, Any]:
    """One row's worth of latency + error figures.

    ``percentiles_ok`` is False when the window blew the scan bound. The counts
    are still exact — they are sums — so they are reported; the percentiles are
    None, because there is no honest value for them.
    """
    return {
        "requests": requests,
        "errors": errors,
        "error_rate": _pct(errors, requests),
        "p50_ms": percentile(latencies, 50) if percentiles_ok else None,
        "p95_ms": percentile(latencies, 95) if percentiles_ok else None,
        "p99_ms": percentile(latencies, 99) if percentiles_ok else None,
    }


@custom_function("request_performance")
def request_performance(ctx: ResolverContext) -> ResolverResult:
    """View 71 — server-side latency, errors and throughput from the request log.

    Every percentile is computed by nearest rank over the raw ``total_ms``
    values of the bucket being reported. The window-level rows in
    ``latency_summary`` are computed over the window's raw rows, NOT from the
    per-bucket points in ``latency_trend`` — see the module docstring.
    """
    lo, hi = _window_bounds(ctx)
    tz = store_timezone(ctx.db)
    granularity = ctx.filters.granularity
    warnings: list[AnalyticsWarning] = []

    total_requests = int(
        ctx.db.execute(
            select(func.count())
            .select_from(RequestLog)
            .where(RequestLog.ts >= lo, RequestLog.ts < hi)
        ).scalar_one()
        or 0
    )

    percentiles_ok = total_requests <= MAX_LATENCY_SAMPLE
    if not percentiles_ok:
        warnings.append(
            warn(
                LATENCY_SAMPLE_EXCEEDED,
                f"This window holds {total_requests:,} requests, above the "
                f"{MAX_LATENCY_SAMPLE:,}-row bound for an exact percentile scan. "
                "Percentiles are withheld rather than estimated: they are not "
                "summable and no stored daily figure can be re-aggregated into "
                "them. Request counts and error rates below are still exact. "
                "Narrow the window to get percentiles back.",
                severity="warn",
                requests=total_requests,
                bound=MAX_LATENCY_SAMPLE,
            )
        )

    rows = _request_rows(ctx, lo, hi) if percentiles_ok else []

    # bucket -> class -> raw latencies; plus the two counters, which stay exact
    # even when the raw scan was skipped.
    per_bucket: dict[date, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    per_bucket_counts: dict[date, dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(lambda: [0, 0])
    )
    per_class: dict[str, list[int]] = defaultdict(list)
    per_class_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    per_route: dict[tuple[str, str], list[int]] = defaultdict(list)
    per_route_counts: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    newest: datetime | None = None

    if percentiles_ok:
        for ts, route, status, total_ms in rows:
            klass = classify_route(route)
            bucket = _bucket_start(_local_day(ts, tz), granularity)
            is_error = 1 if int(status) >= ERROR_STATUS_FLOOR else 0
            ms = int(total_ms or 0)

            per_bucket[bucket][klass].append(ms)
            counts = per_bucket_counts[bucket][klass]
            counts[0] += 1
            counts[1] += is_error

            per_class[klass].append(ms)
            klass_counts = per_class_counts[klass]
            klass_counts[0] += 1
            klass_counts[1] += is_error

            key = (route or "(unmatched)", klass)
            per_route[key].append(ms)
            route_counts = per_route_counts[key]
            route_counts[0] += 1
            route_counts[1] += is_error

            if newest is None or ts > newest:
                newest = ts
    else:
        # Percentiles are off the table, but the additive figures are not:
        # counts and error counts re-aggregate exactly at any size. Grouped in
        # SQL so nothing large crosses into Python.
        grouped = ctx.db.execute(
            select(
                RequestLog.route,
                func.count().label("requests"),
                func.sum(func.if_(RequestLog.status >= ERROR_STATUS_FLOOR, 1, 0)).label(
                    "errors"
                ),
                func.max(RequestLog.ts).label("newest"),
            )
            .where(RequestLog.ts >= lo, RequestLog.ts < hi)
            .group_by(RequestLog.route)
        ).all()
        for route, requests, errors, route_newest in grouped:
            klass = classify_route(route)
            key = (route or "(unmatched)", klass)
            per_route_counts[key][0] += int(requests or 0)
            per_route_counts[key][1] += int(errors or 0)
            per_class_counts[klass][0] += int(requests or 0)
            per_class_counts[klass][1] += int(errors or 0)
            if route_newest is not None and (newest is None or route_newest > newest):
                newest = route_newest

    # -- series: storefront only, one point per bucket ----------------------
    # Admin traffic is excluded from the charts and kept in `latency_summary`,
    # so it is separable rather than deleted.
    buckets = sorted(set(per_bucket) | set(per_bucket_counts))
    latency_points: list[dict[str, Any]] = []
    error_points: list[dict[str, Any]] = []
    throughput_points: list[dict[str, Any]] = []
    for bucket in buckets:
        latencies = per_bucket[bucket].get(ROUTE_CLASS_STOREFRONT, [])
        requests, errors = per_bucket_counts[bucket][ROUTE_CLASS_STOREFRONT]
        if not requests:
            continue
        block = _latency_block(
            latencies, requests, errors, percentiles_ok=percentiles_ok
        )
        stamp = bucket.isoformat()
        latency_points.append(
            {
                "date": stamp,
                "p50_ms": block["p50_ms"],
                "p95_ms": block["p95_ms"],
                "p99_ms": block["p99_ms"],
            }
        )
        error_points.append({"date": stamp, "error_rate": block["error_rate"]})
        throughput_points.append({"date": stamp, "requests": block["requests"]})

    # -- latency_summary: one row per route class, over the WHOLE window ----
    summary_rows: list[dict[str, Any]] = []
    for klass in ROUTE_CLASSES:
        requests, errors = per_class_counts.get(klass, [0, 0])
        if not requests:
            continue
        summary_rows.append(
            {
                "route_class": klass,
                **_latency_block(
                    per_class.get(klass, []),
                    requests,
                    errors,
                    percentiles_ok=percentiles_ok,
                ),
            }
        )

    # -- slow_endpoints: every class, so admin stays visible and separable ---
    limit = clamp_row_limit(ctx.filters.limit)
    endpoint_rows = []
    for (route, klass), counts in per_route_counts.items():
        requests, errors = counts
        block = _latency_block(
            per_route.get((route, klass), []),
            requests,
            errors,
            percentiles_ok=percentiles_ok,
        )
        endpoint_rows.append({"endpoint": route, "route_class": klass, **block})
    # Slowest first when percentiles exist, busiest first when they do not —
    # sorting by a column that is None for every row would be an arbitrary
    # order presented as a ranking.
    endpoint_rows.sort(
        key=lambda r: (-(r["p95_ms"] or 0), -r["requests"], r["endpoint"])
    )
    endpoints_truncated = len(endpoint_rows) > limit

    slow_rows = [
        {
            "fingerprint": str(fingerprint)[:12],
            "table_name": table_name or "-",
            "operation": operation or "-",
            "occurrences": int(occurrences or 0),
            "total_ms": int(total or 0),
            "max_ms": int(worst or 0),
        }
        for fingerprint, table_name, operation, occurrences, total, worst in (
            _slow_query_rows(ctx, lo, hi, limit)
        )
    ]

    tables = {
        "latency_summary": TableBlock(
            rows=summary_rows, total_rows=len(summary_rows), truncated=False
        ),
        "slow_endpoints": TableBlock(
            rows=endpoint_rows[:limit],
            total_rows=len(endpoint_rows),
            truncated=endpoints_truncated,
        ),
        "slow_queries": TableBlock(
            rows=slow_rows, total_rows=len(slow_rows), truncated=len(slow_rows) >= limit
        ),
    }

    if total_requests == 0:
        warnings.append(
            warn(
                WarningCode.NO_ROLLUP_YET,
                "obs_request_logs holds no requests in this window, so there is "
                "nothing to measure. This is 'not recorded', not 'nothing was "
                "slow' — the telemetry buffer writes off the hot path and is "
                "pruned by a retention job.",
                severity="warn",
                source="obs_request_logs",
                requested_through=ctx.window.date_to.isoformat(),
            )
        )

    sources = [
        source_ref(
            "obs_request_logs",
            label="Server request log",
            kind="live",
            rows=total_requests,
            through=_local_day(newest, tz) if newest is not None else None,
        ),
        source_ref(
            "obs_slow_queries",
            label="Slow query log",
            kind="live",
            rows=sum(r["occurrences"] for r in slow_rows),
        ),
    ]

    return ResolverResult(
        series={
            "latency_trend": latency_points,
            "error_rate": error_points,
            "throughput": throughput_points,
        },
        tables=tables,
        sources=sources,
        warnings=warnings,
        # ACTUAL, not AUTHORITATIVE: these are observed measurements of this
        # server's own behaviour rather than entries in the transactional
        # record, and they measure the server half of a latency the user
        # experiences as a whole.
        quality=MetricQuality.ACTUAL,
    ).rolled_up()
