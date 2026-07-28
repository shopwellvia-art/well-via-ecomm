"""Cross-system data reconciliation: the evidence that licenses retiring the legacy pages.

Every other module in this package computes a number. This one asks whether the
numbers agree with each other, and — much more importantly — refuses to imply
agreement where no comparison was made.

The failure this module exists to prevent
=========================================
A reconciliation screen is the one dashboard whose *empty* state is dangerous.
Every other view that finds nothing renders a blank chart and the reader
correctly concludes "nothing happened". Here, a variance table with no rows
reads as **"everything balances"** — which is the strongest claim the system can
make, made from the weakest possible evidence: an absence.

So the whole module is built around one distinction, applied without exception::

    status = match             we compared two things and they were equal
    status = variance          we compared two things and they differed
    status = not_configured    we could NOT compare, and the values are NULL
    status = error             the check raised; the values are NULL

A check that cannot run is *listed*, with NULL values, so it occupies a visible
row on the screen. It is never omitted (omission reads as "clean") and never
reported as a 0.00% variance (which reads as "checked"). ``ReconciliationReport``
therefore always carries exactly one row per check in :data:`CHECK_ORDER`,
whatever the data looks like.

The same rule at day grain: **never densify**
---------------------------------------------
``resolvers/core.dense_points`` zero-fills gaps inside a covered range, and that
is right for a revenue chart — a day with no orders really did earn zero. It is
wrong here. A ``0`` in this module means "checked, and it balanced". Filling a
day that was never checked with a 0 variance would assert a check that never
ran, and would do it in the one place where a reader is looking specifically for
gaps. :attr:`CheckResult.days` therefore contains a row only for days that were
actually compared; the rest are accounted for by :attr:`CheckResult.coverage_pct`.

Coverage, per check
-------------------
"What fraction of the window could actually be compared." For the rollup checks
that is the share of days that have a rollup row at all (a missing
``agg_order_daily`` row means the aggregation job never ran for that day — the
table writes one row per bucket it processes, even a zero one, so absence is
unambiguous). For the order-level checks it is the share of the population that
carried the counterpart record. It is NULL for a check that did not run, because
"0% of the window was compared" and "the comparison is unavailable" are the same
statement and only one of them needs a number.

What can and cannot be checked in this deployment
=================================================
Runs today, entirely on internal data:

* ``legacy_vs_new`` — the shadow-mode **parity anchor**.
  ``MarginService.paid_order_value()`` against
  ``DashboardService._revenue_summary()``. Both are deliberately pinned to the
  legacy ``_REVENUE_STATUSES`` rule and are supposed to be byte-identical, so
  *any* difference at all is a real defect, not a tolerance question. This is the
  check that licenses retiring the legacy admin pages: until it matches over a
  real window, the new subsystem has no evidence behind it.
* ``rollup_vs_live`` — ``agg_order_daily`` against the same figures recomputed
  live from ``orders``. Catches a stale, half-written or double-counted bucket.
* ``revenue_bridge`` — the stored rows' own identity, via
  ``contracts.RevenueBridge`` so this module and the aggregation tests assert
  the same one. Self-proving: no external system is needed to know it is wrong.
* ``gateway_vs_paid_orders`` — ``order_payments`` against ``orders``.
* ``courier_vs_shipments`` — courier delivery scans against the shipments we
  count as completed.

Cannot run today:

* ``ga4_purchase_parity`` — backend paid orders against GA4 purchases. GA4 is
  not connected, and more fundamentally there is **no GA4 Data API client in
  this codebase at all**: ``analytics/ga4.py`` speaks the Measurement Protocol,
  which can *send* events and cannot *read* what GA4 recorded. So this check is
  ``not_configured`` with NULL values, permanently, until both the credentials
  and a reader exist.

  It still reports what it *can* see, because the outbox is ours: the paid
  orders with no ``purchase`` event queued (``missing_ids``) and the orders that
  would produce more than one GA4 purchase (``duplicate_ids``). Those are
  tracking losses we can prove locally. They are deliberately **not** promoted
  into ``coverage_pct`` — that field means "fraction compared against the
  counterparty", which here is zero-by-unavailability. The outbox-side coverage
  is reported as ``context["outbox_coverage_pct"]`` instead, so the two can
  never be read as the same measurement.

Alerts
------
A *material* variance writes one :class:`AnalyticsAlert` per check, so the
Control Centre shows it whether or not anyone opens the reconciliation page. A
variance nobody is looking at is indistinguishable from no variance.

``status`` and materiality are two different bars, on purpose. Status is
factual: any non-zero difference is a variance. Materiality decides whether a
human is interrupted, and for the money identities that must hold exactly
(``legacy_vs_new``, ``rollup_vs_live``, ``revenue_bridge``) it is also any
non-zero difference. For the population checks a small residue is routine — a
split-COD order whose cash leg has not been collected yet is a real, expected
gap — so those alert above a percentage tolerance and report the rest quietly.

``rule_key`` is :attr:`AlertRuleKey.TRACKING_FAILURE`, the instrumentation
family: nothing is necessarily wrong with the *store*, the numbers themselves
are not trustworthy, which is exactly what that rule names. A dedicated
``reconciliation_variance`` key would be better and belongs in
``models/analytics_control.py``; adding it is a schema change owned elsewhere,
and inventing a free-text rule key here would create an alert stream that no
mute, route or acknowledgement rule matches.

Windows and timezones
---------------------
``[date_from, date_to)`` is **half-open** and is expressed in **store-local
reporting days**, matching ``agg_order_daily.bucket_date``. Order-level queries
convert it once through ``timebox.range_bounds_utc`` so both sides of every
comparison see the identical instant range — which is what makes "byte-identical"
a checkable claim rather than an aspiration.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping, Sequence

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.analytics_control import (
    AlertRuleKey,
    AlertSeverity,
    AlertStatus,
    AnalyticsAlert,
    AnalyticsEventOutbox,
    AnalyticsSyncRun,
    OutboxEventName,
    SyncStatus,
)
from app.models.analytics_rollups import AggOrderDaily
from app.models.order import Order
from app.models.order_payment import OrderPayment, PaymentTxnStatus
from app.models.shipment import Shipment, ShipmentStatus
from app.services.analytics.contracts import RevenueBridge, from_minor, to_minor
from app.services.analytics.margin import MarginService
from app.services.analytics.timebox import (
    active_generation,
    day_bounds_utc,
    range_bounds_utc,
    store_timezone,
)
from app.services.analytics.tracking_events import transaction_id_for
from app.services.dashboard_service import _REVENUE_STATUSES, DashboardService

__all__ = [
    "CheckKey",
    "CheckStatus",
    "CHECK_ORDER",
    "DayVariance",
    "CheckResult",
    "ReconciliationReport",
    "run_reconciliation",
    "ROLLUP_JOB",
]

log = logging.getLogger("analytics.reconciliation")

_HUNDRED = Decimal("100")
_PCT_Q = Decimal("0.0001")
_COVERAGE_Q = Decimal("0.01")

#: The aggregation job whose watermark explains a missing `agg_order_daily` row.
#: Read-only here — this module never enqueues or runs it.
ROLLUP_JOB = "order_daily"

#: Reported id lists are capped so one badly broken window cannot produce an
#: alert `context` too large to store or a payload too large to render. The
#: untruncated count always travels alongside, so a capped list is never
#: mistaken for a complete one.
MAX_REPORTED_IDS = 200


class CheckStatus:
    """The four outcomes, and the reason they are four and not two.

    ``NOT_CONFIGURED`` and ``ERROR`` both mean "no comparison happened", and
    both carry NULL values — but they are not the same instruction. The first is
    an admin's job (connect the counterparty); the second is an engineer's (the
    query raised). Collapsing them would send every reconciliation gap to the
    wrong person.
    """

    MATCH = "match"
    VARIANCE = "variance"
    NOT_CONFIGURED = "not_configured"
    ERROR = "error"


class CheckKey:
    """Stable identifiers. Constants because they key alerts and UI state."""

    LEGACY_VS_NEW = "legacy_vs_new"
    ROLLUP_VS_LIVE = "rollup_vs_live"
    REVENUE_BRIDGE = "revenue_bridge"
    GA4_PURCHASE_PARITY = "ga4_purchase_parity"
    GATEWAY_VS_PAID_ORDERS = "gateway_vs_paid_orders"
    COURIER_VS_SHIPMENTS = "courier_vs_shipments"


#: Render order. Also the guarantee that the report has one row per check: the
#: runner iterates this, so a check cannot disappear by returning early.
CHECK_ORDER: tuple[str, ...] = (
    CheckKey.LEGACY_VS_NEW,
    CheckKey.ROLLUP_VS_LIVE,
    CheckKey.REVENUE_BRIDGE,
    CheckKey.GA4_PURCHASE_PARITY,
    CheckKey.GATEWAY_VS_PAID_ORDERS,
    CheckKey.COURIER_VS_SHIPMENTS,
)


@dataclass(frozen=True)
class _CheckSpec:
    """Static description of one check: labels, materiality, alert routing.

    ``exact`` marks the identities that must hold to the paisa. For those there
    is no such thing as an acceptable residue: the two sides are the same query
    or the same arithmetic, so a difference is a defect by construction and a
    tolerance would only delay finding it.

    ``expected_is_left`` says which side is the reference. It matters only when
    an alert is written — ``expected_*`` and ``actual_value`` are what make an
    alert triageable months later — and for the denominator of
    ``difference_pct``, which is a relative error and must be relative to the
    side believed to be right.
    """

    key: str
    label: str
    left_label: str
    right_label: str
    unit: str  # "money" | "count"
    exact: bool
    tolerance_pct: Decimal
    severity: str
    expected_is_left: bool = False


_SPECS: dict[str, _CheckSpec] = {
    CheckKey.LEGACY_VS_NEW: _CheckSpec(
        key=CheckKey.LEGACY_VS_NEW,
        label="Legacy revenue vs new subsystem",
        left_label="MarginService.paid_order_value",
        right_label="DashboardService._revenue_summary",
        unit="money",
        exact=True,
        tolerance_pct=Decimal("0"),
        severity=AlertSeverity.CRITICAL,
    ),
    CheckKey.ROLLUP_VS_LIVE: _CheckSpec(
        key=CheckKey.ROLLUP_VS_LIVE,
        label="Order rollup vs live orders",
        left_label="agg_order_daily.paid_order_value",
        right_label="orders (recomputed live)",
        unit="money",
        exact=True,
        tolerance_pct=Decimal("0"),
        severity=AlertSeverity.CRITICAL,
    ),
    CheckKey.REVENUE_BRIDGE: _CheckSpec(
        key=CheckKey.REVENUE_BRIDGE,
        label="Revenue bridge identity",
        left_label="gms - discounts + tax + shipping + cod_surcharge - refunds",
        right_label="agg_order_daily.net_revenue",
        unit="money",
        exact=True,
        tolerance_pct=Decimal("0"),
        severity=AlertSeverity.CRITICAL,
        expected_is_left=True,
    ),
    CheckKey.GA4_PURCHASE_PARITY: _CheckSpec(
        key=CheckKey.GA4_PURCHASE_PARITY,
        label="Backend paid orders vs GA4 purchases",
        left_label="orders reaching a paid state",
        right_label="GA4 purchase events",
        unit="count",
        exact=False,
        tolerance_pct=Decimal("1"),
        severity=AlertSeverity.WARNING,
        expected_is_left=True,
    ),
    CheckKey.GATEWAY_VS_PAID_ORDERS: _CheckSpec(
        key=CheckKey.GATEWAY_VS_PAID_ORDERS,
        label="Gateway transactions vs paid orders",
        left_label="order_payments (captured)",
        right_label="orders.total_amount (paid)",
        unit="money",
        exact=False,
        tolerance_pct=Decimal("1"),
        severity=AlertSeverity.WARNING,
    ),
    CheckKey.COURIER_VS_SHIPMENTS: _CheckSpec(
        key=CheckKey.COURIER_VS_SHIPMENTS,
        label="Courier deliveries vs completed shipments",
        left_label="shipments with a courier delivery scan",
        right_label="shipments marked delivered",
        unit="count",
        exact=False,
        tolerance_pct=Decimal("1"),
        severity=AlertSeverity.WARNING,
        expected_is_left=True,
    ),
}


# ===========================================================================
# Result shapes
# ===========================================================================


@dataclass(frozen=True)
class DayVariance:
    """One reporting day that was actually compared.

    A day with no row here was **not checked** — it is not a day that checked
    out at zero. That distinction is the entire reason this list is sparse, and
    it must survive every layer above: a UI that zero-fills these points to draw
    a continuous line re-introduces exactly the lie the sparseness prevents.
    """

    check_key: str
    bucket_date: date
    left_value: Decimal | None
    right_value: Decimal | None
    difference: Decimal
    difference_pct: Decimal | None

    @property
    def balanced(self) -> bool:
        return self.difference == 0


@dataclass(frozen=True)
class CheckResult:
    """One row of the variance table.

    Invariant enforced by :func:`_finalise`: when ``status`` is
    ``not_configured`` or ``error``, every numeric field is ``None``. There is
    no code path that produces ``status = not_configured`` alongside a
    ``difference`` of ``0.00``, because that pair is precisely the misreading
    this module exists to make impossible.
    """

    check_key: str
    label: str
    status: str
    left_label: str = ""
    left_value: Decimal | None = None
    right_label: str = ""
    right_value: Decimal | None = None
    difference: Decimal | None = None
    difference_pct: Decimal | None = None
    coverage_pct: Decimal | None = None
    #: Rows/days the check was asked to cover, and how many it could compare.
    population: int = 0
    compared: int = 0
    #: Identifiers present on one side and absent on the other, and identifiers
    #: that would be counted twice. Capped at MAX_REPORTED_IDS; the full counts
    #: live in `context`.
    missing_ids: tuple[str, ...] = ()
    duplicate_ids: tuple[str, ...] = ()
    detail: str = ""
    days: tuple[DayVariance, ...] = ()
    context: Mapping[str, Any] = field(default_factory=dict)
    #: Set when this result caused an alert to be written.
    alert_id: int | None = None

    @property
    def ran(self) -> bool:
        return self.status in (CheckStatus.MATCH, CheckStatus.VARIANCE)

    @property
    def is_variance(self) -> bool:
        return self.status == CheckStatus.VARIANCE

    @property
    def is_not_configured(self) -> bool:
        return self.status == CheckStatus.NOT_CONFIGURED

    def to_row(self, period: str) -> dict[str, Any]:
        """The shape ``resolvers/special.ReconciliationResolver`` renders.

        Its ``_Check`` uses ``source_value`` for the reference side and
        ``rollup_value`` for the side under test, and spells a passing check
        ``matched`` rather than ``match``. Both are translated here rather than
        in the resolver: the resolver is owned elsewhere, and a service that
        cannot be dropped into the existing table without editing it is not
        actually the service that resolver was waiting for.
        """
        spec = _SPECS[self.check_key]
        source, rollup = (
            (self.left_value, self.right_value)
            if spec.expected_is_left
            else (self.right_value, self.left_value)
        )
        return {
            "check_name": self.check_key,
            "period": period,
            "status": "matched" if self.status == CheckStatus.MATCH else self.status,
            "source_value": source,
            "rollup_value": rollup,
            "variance_pct": self.difference_pct,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ReconciliationReport:
    """Every check, always — including the ones that could not run."""

    date_from: date
    date_to: date
    tz_generation: int
    timezone_name: str
    generated_at: datetime
    checks: tuple[CheckResult, ...]

    @property
    def period(self) -> str:
        return f"{self.date_from.isoformat()}..{self.date_to.isoformat()}"

    def by_key(self, check_key: str) -> CheckResult:
        for check in self.checks:
            if check.check_key == check_key:
                return check
        raise KeyError(check_key)

    @property
    def variances(self) -> tuple[CheckResult, ...]:
        return tuple(c for c in self.checks if c.is_variance)

    @property
    def not_configured(self) -> tuple[CheckResult, ...]:
        return tuple(c for c in self.checks if c.is_not_configured)

    @property
    def errors(self) -> tuple[CheckResult, ...]:
        return tuple(c for c in self.checks if c.status == CheckStatus.ERROR)

    @property
    def alert_ids(self) -> tuple[int, ...]:
        return tuple(c.alert_id for c in self.checks if c.alert_id is not None)

    @property
    def days(self) -> tuple[DayVariance, ...]:
        """Every compared day across every check. Sparse by construction."""
        return tuple(d for c in self.checks for d in c.days)

    def to_rows(self) -> list[dict[str, Any]]:
        return [c.to_row(self.period) for c in self.checks]


# ===========================================================================
# Public entry point
# ===========================================================================


def run_reconciliation(
    db: Session,
    date_from: date,
    date_to: date,
    *,
    write_alerts: bool = True,
) -> ReconciliationReport:
    """Run every check over the store-local day window ``[date_from, date_to)``.

    Half-open, matching ``MarginService`` and ``DashboardService`` so the three
    surfaces cannot disagree about which orders belong to a period.

    Every check is isolated: one that raises becomes a ``CheckStatus.ERROR`` row
    with NULL values and does not prevent the others from running. A partial
    report is useful; a reconciliation page that 500s because one counterparty
    table is unreadable tells an operator nothing about the five checks that
    would have passed.
    """
    return _Reconciler(db, date_from, date_to).run(write_alerts=write_alerts)


# ===========================================================================
# Implementation
# ===========================================================================


class _Reconciler:
    def __init__(self, db: Session, date_from: date, date_to: date) -> None:
        self.db = db
        self.date_from = _as_date(date_from)
        self.date_to = _as_date(date_to)
        if self.date_to <= self.date_from:
            raise ValueError(
                f"date_to ({self.date_to.isoformat()}) must be after date_from "
                f"({self.date_from.isoformat()}); the window is half-open [from, to)"
            )
        self.tz = store_timezone(db)
        self.generation = int(active_generation(db).generation)
        self.start, self.end = range_bounds_utc(self.date_from, self.date_to, self.tz)
        self.days: list[date] = [
            self.date_from + timedelta(days=n)
            for n in range((self.date_to - self.date_from).days)
        ]

    # -- runner ------------------------------------------------------------

    def run(self, *, write_alerts: bool) -> ReconciliationReport:
        runners = {
            CheckKey.LEGACY_VS_NEW: self._check_legacy_vs_new,
            CheckKey.ROLLUP_VS_LIVE: self._check_rollup_vs_live,
            CheckKey.REVENUE_BRIDGE: self._check_revenue_bridge,
            CheckKey.GA4_PURCHASE_PARITY: self._check_ga4_purchase_parity,
            CheckKey.GATEWAY_VS_PAID_ORDERS: self._check_gateway_vs_paid_orders,
            CheckKey.COURIER_VS_SHIPMENTS: self._check_courier_vs_shipments,
        }
        results: list[CheckResult] = []
        for key in CHECK_ORDER:
            try:
                results.append(runners[key]())
            except Exception as exc:  # noqa: BLE001 - one broken check, not six
                log.exception("reconciliation check %s failed", key)
                results.append(self._errored(key, exc))

        if write_alerts:
            results = [self._maybe_alert(r) for r in results]
            self.db.commit()

        return ReconciliationReport(
            date_from=self.date_from,
            date_to=self.date_to,
            tz_generation=self.generation,
            timezone_name=str(self.tz),
            generated_at=_utcnow(),
            checks=tuple(results),
        )

    # -- 1. legacy vs new (the parity anchor) ------------------------------

    def _check_legacy_vs_new(self) -> CheckResult:
        """``MarginService.paid_order_value`` against the legacy dashboard headline.

        Both sides are handed the **same** UTC instant range rather than each
        deriving its own from a date, because the claim under test is that the
        two queries are identical — and a window computed twice is one more
        place they could differ for a reason that has nothing to do with the
        thing being proved.

        An empty window returns ``not_configured``, not ``match``. Two queries
        that both return zero over a window with no paid orders have agreed
        about nothing, and this is the check whose green light retires the
        legacy pages; it must be earned by real rows.
        """
        spec = _SPECS[CheckKey.LEGACY_VS_NEW]
        legacy = DashboardService(self.db)._revenue_summary(self.start, self.end)
        population = int(legacy["count"])
        if population == 0:
            return self._not_configured(
                spec,
                "No order reached a paid state in this window, so the parity "
                "anchor compared nothing. Two queries that both return zero have "
                "not agreed about anything — this is not a passing check.",
                context={"paid_orders": 0},
            )

        new_minor = to_minor(MarginService(self.db).paid_order_value(self.start, self.end))
        legacy_minor = to_minor(legacy["revenue"])

        return self._finalise(
            spec,
            left_minor=new_minor,
            right_minor=legacy_minor,
            population=population,
            compared=population,
            coverage_pct=_HUNDRED,
            detail=(
                "The shadow-mode parity anchor. Both sides read orders in "
                f"{_REVENUE_STATUSES_TEXT} over the identical instant range and "
                "must be byte-identical; any difference at all is a defect, not a "
                "tolerance question."
            ),
            context={"paid_orders": population},
        )

    # -- 2. rollup vs live -------------------------------------------------

    def _check_rollup_vs_live(self) -> CheckResult:
        """``agg_order_daily`` against the same figures recomputed from ``orders``.

        Only the days that HAVE a rollup row are compared. ``agg_order_daily``
        writes one row per bucket it processes — including a bucket with no
        trade — so a missing row means the job never ran for that day. Charging
        that day's live revenue against a rollup of zero would report an
        aggregation *variance* for what is actually an aggregation *gap*, and the
        two need completely different responses.
        """
        spec = _SPECS[CheckKey.ROLLUP_VS_LIVE]
        rows = self._rollup_rows(
            AggOrderDaily.bucket_date,
            AggOrderDaily.paid_order_value,
            AggOrderDaily.orders_total,
        )
        if not rows:
            return self._not_configured(spec, self._no_rollup_detail())

        days: list[DayVariance] = []
        left_minor = right_minor = 0
        rollup_orders = live_orders = 0

        for row in rows:
            live = self._live_day(row.bucket_date)
            stored_minor = to_minor(row.paid_order_value)
            left_minor += stored_minor
            right_minor += live["paid_value_minor"]
            rollup_orders += int(row.orders_total or 0)
            live_orders += live["orders_total"]
            days.append(
                self._day(
                    spec,
                    row.bucket_date,
                    stored_minor,
                    live["paid_value_minor"],
                )
            )

        count_gap = rollup_orders - live_orders
        covered_dates = {r.bucket_date for r in rows}
        return self._finalise(
            spec,
            left_minor=left_minor,
            right_minor=right_minor,
            population=len(self.days),
            compared=len(rows),
            days=tuple(days),
            force_variance=count_gap != 0,
            detail=(
                f"{len(rows)} of {len(self.days)} day(s) in the window carry a "
                f"{AggOrderDaily.__tablename__} row and were compared against a live "
                "recomputation from orders. Days with no rollup row were not "
                "checked and are absent rather than reported as balanced."
                + (
                    ""
                    if count_gap == 0
                    else f" Order counts also disagree: rollup {rollup_orders}, "
                    f"live {live_orders} (gap {count_gap})."
                )
            ),
            context={
                "rollup_orders_total": rollup_orders,
                "live_orders_total": live_orders,
                "order_count_gap": count_gap,
                "uncovered_days": [
                    d.isoformat() for d in self.days if d not in covered_dates
                ][:MAX_REPORTED_IDS],
            },
        )

    # -- 3. revenue bridge -------------------------------------------------

    def _check_revenue_bridge(self) -> CheckResult:
        """The stored rows' own identity, evaluated per day and in total.

        Uses ``contracts.RevenueBridge`` so this module, the resolver and the
        aggregation tests all assert the same identity from the same code.

        Status is driven by the per-day imbalances, not by the window total. Two
        days wrong by +500 and -500 sum to a total that balances perfectly, and
        a check that read only the total would call that a match while both
        buckets are corrupt.
        """
        spec = _SPECS[CheckKey.REVENUE_BRIDGE]
        rows = self._rollup_rows(
            AggOrderDaily.bucket_date,
            AggOrderDaily.gross_merchandise_sales,
            AggOrderDaily.discount_sum,
            AggOrderDaily.tax_sum,
            AggOrderDaily.shipping_income,
            AggOrderDaily.cod_surcharge_sum,
            AggOrderDaily.refund_sum,
            AggOrderDaily.net_revenue,
        )
        if not rows:
            return self._not_configured(spec, self._no_rollup_detail())

        days: list[DayVariance] = []
        expected_minor = stored_minor = 0
        unbalanced: list[str] = []

        for row in rows:
            bridge = _bridge_from(row)
            imbalance = bridge.imbalance_minor()
            row_expected = bridge.net_revenue_minor + imbalance
            expected_minor += row_expected
            stored_minor += bridge.net_revenue_minor
            if imbalance:
                unbalanced.append(row.bucket_date.isoformat())
            days.append(
                self._day(spec, row.bucket_date, row_expected, bridge.net_revenue_minor)
            )

        return self._finalise(
            spec,
            left_minor=expected_minor,
            right_minor=stored_minor,
            population=len(self.days),
            compared=len(rows),
            days=tuple(days),
            force_variance=bool(unbalanced),
            detail=(
                "gross - discounts + tax + shipping + cod_surcharge - refunds must "
                "equal net_revenue on every stored row. This identity is "
                "self-proving: if it does not hold, an aggregation job is wrong and "
                "no external system is needed to know it. "
                + (
                    f"{len(unbalanced)} of {len(rows)} row(s) do not balance: "
                    + ", ".join(unbalanced[:10])
                    if unbalanced
                    else f"All {len(rows)} compared row(s) balance."
                )
            ),
            context={"unbalanced_days": unbalanced[:MAX_REPORTED_IDS]},
        )

    # -- 4. backend paid orders vs GA4 purchases ---------------------------

    def _check_ga4_purchase_parity(self) -> CheckResult:
        """Paid orders against GA4 purchases. Cannot run, and says so.

        The counterparty is GA4's own record of what it ingested, which is only
        readable through the Data API. This deployment has neither the
        credentials nor a client: ``analytics/ga4.py`` implements the Measurement
        Protocol, whose entire vocabulary is "here is an event" — it answers 204
        to anything and cannot be asked what GA4 kept. So the comparison is
        unavailable, the values are NULL, and the status is ``not_configured``.

        What it does report is the outbox evidence, because the outbox is ours:

        * ``missing_ids`` — paid orders with no ``purchase`` event queued at all.
          Those conversions cannot reach GA4 by any route.
        * ``duplicate_ids`` — transaction ids that would produce more than one
          GA4 purchase for one order, either because two outbox rows point at the
          same order or because a row's transaction id is not the canonical one
          from ``transaction_id_for()`` and so will not dedupe against the
          browser tag. Duplicate revenue in GA4 inflates reported ROAS, which
          changes what the business spends money on.

        Those numbers are a floor on the tracking loss, never a measurement of
        it: an event we sent and GA4 dropped is invisible from here. That is
        exactly why the check stays ``not_configured`` instead of grading itself
        on the half it can see.
        """
        spec = _SPECS[CheckKey.GA4_PURCHASE_PARITY]
        reasons = self._ga4_unavailable_reasons()

        paid = self.db.execute(
            select(Order.id, Order.order_number).where(*self._paid_window())
        ).all()
        expected_txn = {
            int(o.id): transaction_id_for(o.order_number, int(o.id)) for o in paid
        }

        events = (
            self.db.execute(
                select(
                    AnalyticsEventOutbox.order_id,
                    AnalyticsEventOutbox.transaction_id,
                    AnalyticsEventOutbox.status,
                ).where(
                    AnalyticsEventOutbox.event_name == OutboxEventName.PURCHASE,
                    AnalyticsEventOutbox.order_id.in_(sorted(expected_txn)),
                )
            ).all()
            if expected_txn
            else []
        )

        by_order: dict[int, list[str]] = {}
        seen_txn: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for event in events:
            by_order.setdefault(int(event.order_id), []).append(event.transaction_id)
            seen_txn[event.transaction_id] = seen_txn.get(event.transaction_id, 0) + 1
            by_status[event.status] = by_status.get(event.status, 0) + 1

        missing = sorted(
            txn for order_id, txn in expected_txn.items() if order_id not in by_order
        )

        duplicates: set[str] = set()
        for order_id, txns in by_order.items():
            if len(txns) > 1:
                # More than one purchase event for one order: GA4 records the
                # sale twice however well-formed each event is.
                duplicates.update(txns)
            for txn in txns:
                if txn != expected_txn[order_id]:
                    # Not the id the browser tag would send, so GA4 has nothing
                    # to dedupe against and counts both.
                    duplicates.add(txn)
        # Defence in depth: UNIQUE(event_name, transaction_id) makes this
        # unreachable today, and it is checked anyway because the day that
        # constraint is relaxed is the day nobody remembers it was load-bearing.
        duplicates.update(txn for txn, n in seen_txn.items() if n > 1)

        covered = len(by_order)
        outbox_coverage = (
            (Decimal(covered) / Decimal(len(expected_txn)) * _HUNDRED).quantize(
                _COVERAGE_Q
            )
            if expected_txn
            else None
        )

        return self._not_configured(
            spec,
            "Backend paid orders cannot be compared against GA4 purchases: "
            + "; ".join(reasons)
            + ". Values are NULL rather than zero — this check is unavailable, not "
            "clean. The ids below are outbox-side evidence only: conversions we "
            "never queued, and ids that would be counted twice. Events we did send "
            "and GA4 dropped are invisible from here, so treat these as a floor on "
            "the tracking loss.",
            missing_ids=missing,
            duplicate_ids=sorted(duplicates),
            population=len(expected_txn),
            compared=0,
            context={
                "requires": reasons,
                "paid_orders": len(expected_txn),
                "purchase_events": len(events),
                # A queued event is not a delivered one, and a delivered one is
                # not an ingested one — GA4 answers 204 to anything. Reported by
                # status so nobody reads "queued" as "GA4 has it".
                "purchase_events_by_status": by_status,
                "orders_with_purchase_event": covered,
                # Deliberately NOT `coverage_pct`: this measures the outbox, not
                # the comparison against GA4, which did not happen at all.
                "outbox_coverage_pct": outbox_coverage,
                "missing_purchase_events": len(missing),
                "duplicate_transaction_ids": len(duplicates),
            },
        )

    # -- 5. gateway transactions vs paid orders ----------------------------

    def _check_gateway_vs_paid_orders(self) -> CheckResult:
        """``order_payments`` against the ``orders`` they are supposed to back.

        A paid order with no captured payment row is the interesting find: the
        order says the money arrived and nothing records it arriving.

        A residue is normal and is reported rather than smoothed away. A
        split-COD order carries a prepaid leg now and a cash leg only once the
        courier remits, so its payments legitimately sum to less than the order
        total until then. That is why this check alerts on a tolerance while
        still reporting the exact difference.
        """
        spec = _SPECS[CheckKey.GATEWAY_VS_PAID_ORDERS]
        orders = self.db.execute(
            select(Order.id, Order.order_number, Order.total_amount).where(
                *self._paid_window()
            )
        ).all()
        if not orders:
            return self._not_configured(
                spec,
                "No order reached a paid state in this window, so there was no "
                "money movement to reconcile against the gateway records.",
                context={"paid_orders": 0},
            )

        order_ids = [int(o.id) for o in orders]
        payments = self.db.execute(
            select(
                OrderPayment.order_id,
                OrderPayment.amount,
                OrderPayment.gateway_payment_id,
            ).where(
                OrderPayment.order_id.in_(order_ids),
                OrderPayment.payment_status == PaymentTxnStatus.PAID,
            )
        ).all()

        paid_minor: dict[int, int] = {}
        by_gateway_ref: dict[str, int] = {}
        for payment in payments:
            paid_minor[int(payment.order_id)] = paid_minor.get(
                int(payment.order_id), 0
            ) + to_minor(payment.amount)
            ref = (payment.gateway_payment_id or "").strip()
            if ref:
                by_gateway_ref[ref] = by_gateway_ref.get(ref, 0) + 1

        left_minor = sum(paid_minor.values())
        right_minor = sum(to_minor(o.total_amount) for o in orders)
        missing = sorted(
            transaction_id_for(o.order_number, int(o.id))
            for o in orders
            if int(o.id) not in paid_minor
        )
        # One captured payment id recorded against more than one row is a double
        # capture or a replayed webhook; either way the money is counted twice.
        duplicates = sorted(ref for ref, n in by_gateway_ref.items() if n > 1)

        return self._finalise(
            spec,
            left_minor=left_minor,
            right_minor=right_minor,
            population=len(orders),
            compared=len(paid_minor),
            missing_ids=missing,
            duplicate_ids=duplicates,
            force_variance=bool(missing or duplicates),
            detail=(
                f"{len(paid_minor)} of {len(orders)} paid order(s) carry at least one "
                "captured order_payments row. A shortfall is not automatically a "
                "defect — a split-COD order's cash leg is recorded only once the "
                "courier remits — but an order with no payment row at all is money "
                "the order claims to have received and nothing records receiving."
            ),
            context={
                "paid_orders": len(orders),
                "orders_with_captured_payment": len(paid_minor),
                "captured_payment_rows": len(payments),
                "orders_without_payment": len(missing),
                "duplicate_gateway_payment_ids": len(duplicates),
            },
        )

    # -- 6. courier deliveries vs completed shipments ----------------------

    def _check_courier_vs_shipments(self) -> CheckResult:
        """Courier delivery scans against the shipments we count as completed.

        Both sides are properties of the same shipment rows — the courier's
        ``delivered_at`` scan, and the ``shipment_status`` we set from it — so
        the check is an internal-consistency one and is scoped to the orders
        created in the window rather than to a delivery date, which would put the
        two sides on different populations.

        Stated plainly because it bounds what this proves: the courier scan API
        is not connected, so "courier deliveries" here means the timestamps that
        reached us by webhook or poll, **not** the carrier's own manifest. A
        delivery the carrier recorded and never told us about is invisible on
        both sides and cannot be found from inside this database.

        Two mismatches are surfaced separately because they mean opposite
        things: a shipment marked delivered with no scan is a delivery we
        *asserted*, and a scan with no delivered status is a delivery we
        *received and dropped on the floor*.
        """
        spec = _SPECS[CheckKey.COURIER_VS_SHIPMENTS]
        rows = self.db.execute(
            select(
                Shipment.id,
                Shipment.awb_number,
                Shipment.shipment_status,
                Shipment.delivered_at,
            )
            .join(Order, Order.id == Shipment.order_id)
            .where(Order.created_at >= self.start, Order.created_at < self.end)
        ).all()
        if not rows:
            return self._not_configured(
                spec,
                "No shipment belongs to an order created in this window, so no "
                "delivery record existed to reconcile.",
                context={"shipments": 0},
            )

        # Keyed by shipment id, not by AWB: a carrier that reuses or omits a
        # waybill number would otherwise collapse two shipments into one entry
        # and shrink both sides of the comparison by the same amount, which is
        # invisible. The AWB is carried as the human-readable label only.
        label = {
            int(r.id): ((r.awb_number or "").strip() or f"shipment#{int(r.id)}")
            for r in rows
        }
        scanned = {int(r.id) for r in rows if r.delivered_at is not None}
        completed = {
            int(r.id) for r in rows if r.shipment_status == ShipmentStatus.DELIVERED
        }
        if not scanned and not completed:
            return self._not_configured(
                spec,
                f"None of the {len(rows)} shipment(s) in this window has reached a "
                "delivered state on either side, so there was nothing to compare. "
                "Nothing to compare is not the same as agreement.",
                population=len(rows),
                context={"shipments": len(rows)},
            )

        missing = sorted(label[i] for i in completed - scanned)
        dropped = sorted(label[i] for i in scanned - completed)

        return self._finalise(
            spec,
            left_minor=len(scanned),
            right_minor=len(completed),
            population=len(rows),
            compared=len(scanned),
            missing_ids=missing,
            force_variance=bool(missing or dropped),
            detail=(
                f"{len(scanned)} of {len(rows)} shipment(s) carry a courier delivery "
                f"timestamp; {len(completed)} are marked delivered. "
                f"{len(missing)} delivered without any courier scan, {len(dropped)} "
                "scanned as delivered without the status advancing. The courier scan "
                "API is not connected, so this compares the timestamps that reached "
                "us against our own status — not the carrier's manifest."
            ),
            context={
                "shipments": len(rows),
                "courier_scans": len(scanned),
                "marked_delivered": len(completed),
                "delivered_without_scan": missing[:MAX_REPORTED_IDS],
                "scanned_without_delivered_status": dropped[:MAX_REPORTED_IDS],
            },
        )

    # ------------------------------------------------------------------
    # Shared query helpers
    # ------------------------------------------------------------------

    def _paid_window(self) -> list[Any]:
        """The LEGACY revenue rule, unchanged, over this window.

        ``_REVENUE_STATUSES`` rather than ``margin._recognised_sale()`` on
        purpose: every check here reconciles against surfaces the business
        currently operates on, and those all use the legacy set.
        """
        return [
            Order.status.in_(_REVENUE_STATUSES),
            Order.created_at >= self.start,
            Order.created_at < self.end,
        ]

    def _rollup_rows(self, *columns: Any) -> list[Any]:
        return list(
            self.db.execute(
                select(*columns)
                .where(
                    AggOrderDaily.bucket_date >= self.date_from,
                    AggOrderDaily.bucket_date < self.date_to,
                    AggOrderDaily.tz_generation == self.generation,
                )
                .order_by(AggOrderDaily.bucket_date)
            ).all()
        )

    def _live_day(self, bucket: date) -> dict[str, int]:
        """Recompute one store-local day straight from ``orders``.

        One query per covered day rather than a single grouped one. Grouping by
        store-local day in SQL needs either a timezone conversion MySQL may not
        have the tables for, or a fixed UTC offset that is silently wrong across
        a DST boundary. Bucket boundaries are the thing being verified here, so
        deriving them from ``timebox`` — the same helper the aggregation job
        uses — is the point, not an inefficiency to optimise away.
        """
        start, end = day_bounds_utc(bucket, self.tz)
        row = self.db.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (Order.status.in_(_REVENUE_STATUSES), Order.total_amount),
                            else_=None,
                        )
                    ),
                    0,
                ).label("paid_value"),
                func.count(Order.id).label("orders_total"),
            ).where(Order.created_at >= start, Order.created_at < end)
        ).one()
        return {
            "paid_value_minor": to_minor(row.paid_value),
            "orders_total": int(row.orders_total or 0),
        }

    def _no_rollup_detail(self) -> str:
        watermark = self.db.execute(
            select(func.max(AnalyticsSyncRun.watermark_date)).where(
                AnalyticsSyncRun.job == ROLLUP_JOB,
                AnalyticsSyncRun.tz_generation == self.generation,
                AnalyticsSyncRun.status.in_((SyncStatus.SUCCESS, SyncStatus.PARTIAL)),
            )
        ).scalar()
        through = watermark.isoformat() if watermark else "never"
        return (
            f"{AggOrderDaily.__tablename__} holds no row for any day in this window "
            f"under generation {self.generation}, so nothing could be compared. The "
            f"{ROLLUP_JOB} job's watermark is {through}. A rollup cannot report its "
            "own absence, which is why this is a listed check with NULL values "
            "rather than an empty table."
        )

    def _ga4_unavailable_reasons(self) -> list[str]:
        """Everything standing between this check and an actual comparison.

        Deliberately not short-circuited. An admin who pastes in a property id
        should see the *remaining* blocker immediately rather than discovering it
        one field at a time.
        """
        reasons: list[str] = []
        try:
            from app.services.analytics import integrations

            stored = integrations.stored_flags(self.db)
            if not stored.get("analytics.ga4_property_id"):
                reasons.append("analytics.ga4_property_id is not set")
            if not stored.get("analytics.ga4_data_api_credentials"):
                reasons.append(
                    "analytics.ga4_data_api_credentials (service-account JSON) is not set"
                )
        except Exception as exc:  # noqa: BLE001 - config probe must never break the report
            reasons.append(f"GA4 settings could not be read ({exc})")
        reasons.append(
            "no GA4 Data API client exists in this deployment — "
            "app/services/analytics/ga4.py speaks the Measurement Protocol, which "
            "can send events but cannot read back what GA4 recorded"
        )
        return reasons

    # ------------------------------------------------------------------
    # Result construction
    # ------------------------------------------------------------------

    def _day(
        self, spec: _CheckSpec, bucket: date, left_minor: int, right_minor: int
    ) -> DayVariance:
        difference = left_minor - right_minor
        base = left_minor if spec.expected_is_left else right_minor
        return DayVariance(
            check_key=spec.key,
            bucket_date=bucket,
            left_value=_value(spec, left_minor),
            right_value=_value(spec, right_minor),
            difference=_value(spec, difference),
            difference_pct=_pct(difference, base),
        )

    def _finalise(
        self,
        spec: _CheckSpec,
        *,
        left_minor: int,
        right_minor: int,
        population: int,
        compared: int,
        coverage_pct: Decimal | None = None,
        days: tuple[DayVariance, ...] = (),
        missing_ids: Sequence[str] = (),
        duplicate_ids: Sequence[str] = (),
        force_variance: bool = False,
        detail: str = "",
        context: Mapping[str, Any] | None = None,
    ) -> CheckResult:
        difference = left_minor - right_minor
        base = left_minor if spec.expected_is_left else right_minor
        if coverage_pct is None:
            coverage_pct = (
                (Decimal(compared) / Decimal(population) * _HUNDRED).quantize(
                    _COVERAGE_Q
                )
                if population
                else None
            )
        status = (
            CheckStatus.VARIANCE
            if difference != 0 or force_variance
            else CheckStatus.MATCH
        )
        return CheckResult(
            check_key=spec.key,
            label=spec.label,
            status=status,
            left_label=spec.left_label,
            left_value=_value(spec, left_minor),
            right_label=spec.right_label,
            right_value=_value(spec, right_minor),
            difference=_value(spec, difference),
            difference_pct=_pct(difference, base),
            coverage_pct=coverage_pct,
            population=population,
            compared=compared,
            missing_ids=tuple(missing_ids[:MAX_REPORTED_IDS]),
            duplicate_ids=tuple(duplicate_ids[:MAX_REPORTED_IDS]),
            detail=detail,
            days=days,
            context=dict(context or {}),
        )

    def _not_configured(
        self,
        spec: _CheckSpec,
        detail: str,
        *,
        missing_ids: Sequence[str] = (),
        duplicate_ids: Sequence[str] = (),
        population: int = 0,
        compared: int = 0,
        context: Mapping[str, Any] | None = None,
    ) -> CheckResult:
        """A listed, visible, explicitly value-less row.

        No numeric field is populated — not even a 0 for coverage. "0% of the
        window was compared" and "the comparison is unavailable" are the same
        fact, and printing the first one invites a reader to average it,
        threshold it, or chart it alongside checks that actually ran.
        """
        return CheckResult(
            check_key=spec.key,
            label=spec.label,
            status=CheckStatus.NOT_CONFIGURED,
            left_label=spec.left_label,
            right_label=spec.right_label,
            left_value=None,
            right_value=None,
            difference=None,
            difference_pct=None,
            coverage_pct=None,
            population=population,
            compared=compared,
            missing_ids=tuple(missing_ids[:MAX_REPORTED_IDS]),
            duplicate_ids=tuple(duplicate_ids[:MAX_REPORTED_IDS]),
            detail=detail,
            context=dict(context or {}),
        )

    def _errored(self, check_key: str, exc: Exception) -> CheckResult:
        spec = _SPECS[check_key]
        return CheckResult(
            check_key=check_key,
            label=spec.label,
            status=CheckStatus.ERROR,
            left_label=spec.left_label,
            right_label=spec.right_label,
            detail=(
                f"{type(exc).__name__}: {exc}. The check raised, so nothing was "
                "compared and every value is NULL. This is an engineering failure, "
                "not a missing integration."
            ),
            context={"exception": type(exc).__name__},
        )

    # ------------------------------------------------------------------
    # Alerting
    # ------------------------------------------------------------------

    def _maybe_alert(self, check: CheckResult) -> CheckResult:
        if not check.is_variance:
            return check
        spec = _SPECS[check.check_key]
        if not self._is_material(spec, check):
            return check

        expected, actual = (
            (check.left_value, check.right_value)
            if spec.expected_is_left
            else (check.right_value, check.left_value)
        )
        alert = AnalyticsAlert(
            rule_key=AlertRuleKey.TRACKING_FAILURE,
            severity=spec.severity,
            metric=check.check_key[:64],
            dimension="reconciliation",
            dimension_value=spec.key[:64],
            # An exact identity has a band of width zero. Storing the same value
            # twice is not redundant here: `expected_low`/`expected_high` are what
            # let the Control Centre render every alert the same way, and a NULL
            # band would read as "no expectation was set".
            expected_low=expected,
            expected_high=expected,
            actual_value=actual,
            # The window's last reporting day. The variance is a property of the
            # window, and dating it to the first day would file a fresh finding
            # under the oldest bucket on the screen.
            bucket_date=self.date_to - timedelta(days=1),
            detected_at=_utcnow(),
            status=AlertStatus.OPEN,
            context=_jsonable(
                {
                    "check_key": check.check_key,
                    "label": check.label,
                    "window": f"{self.date_from.isoformat()}..{self.date_to.isoformat()}",
                    "tz_generation": self.generation,
                    "left_label": check.left_label,
                    "left_value": check.left_value,
                    "right_label": check.right_label,
                    "right_value": check.right_value,
                    "difference": check.difference,
                    "difference_pct": check.difference_pct,
                    "coverage_pct": check.coverage_pct,
                    "population": check.population,
                    "compared": check.compared,
                    "missing_ids": list(check.missing_ids[:50]),
                    "duplicate_ids": list(check.duplicate_ids[:50]),
                    "unbalanced_days": [
                        d.bucket_date.isoformat() for d in check.days if not d.balanced
                    ][:50],
                    "detail": check.detail,
                }
            ),
        )
        self.db.add(alert)
        self.db.flush()
        return replace(check, alert_id=int(alert.id))

    @staticmethod
    def _is_material(spec: _CheckSpec, check: CheckResult) -> bool:
        """Does this variance justify interrupting a human?

        ``exact`` checks always do: their two sides are the same query or the
        same arithmetic, so a difference is a defect and there is no residue to
        forgive. Everything else is judged on the larger of the value gap and
        the population gap, so a check whose totals happen to net out while half
        its records are unmatched is not quietly excused.
        """
        if spec.exact:
            return True
        gap = abs(check.difference_pct or Decimal("0"))
        if check.difference and not check.difference_pct:
            # A non-zero difference against a zero base: infinitely relative.
            return True
        if check.population:
            unmatched = Decimal(len(check.missing_ids) + len(check.duplicate_ids))
            gap = max(gap, unmatched / Decimal(check.population) * _HUNDRED)
        return gap >= spec.tolerance_pct


# ===========================================================================
# Small pure helpers
# ===========================================================================

_REVENUE_STATUSES_TEXT = "/".join(s.name for s in _REVENUE_STATUSES)


def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _utcnow() -> datetime:
    """Naive UTC, matching ``aggregation/jobs._utcnow``.

    ``DateTime(timezone=True)`` is a no-op on MySQL, so a stored offset is
    silently discarded; writing naive UTC keeps every analytics timestamp in the
    same frame instead of two that look identical and are not.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _value(spec: _CheckSpec, minor: int) -> Decimal:
    """Minor units back to whatever the check reports in.

    Money passes through ``from_minor`` for an exact 2dp Decimal; counts are
    carried as integers in the same field so the report shape stays uniform.
    """
    return from_minor(minor) if spec.unit == "money" else Decimal(minor)


def _pct(difference: int, base: int) -> Decimal | None:
    """Relative error, or None when there is no base to be relative to.

    None rather than 0: a difference measured against nothing has no percentage,
    and 0.0000% is the one value that must only ever mean "checked and equal".
    """
    if base == 0:
        return None
    return (Decimal(difference) / Decimal(base) * _HUNDRED).quantize(_PCT_Q)


def _bridge_from(row: Any) -> RevenueBridge:
    return RevenueBridge(
        gross_merchandise_sales_minor=to_minor(row.gross_merchandise_sales),
        discounts_minor=to_minor(row.discount_sum),
        tax_minor=to_minor(row.tax_sum),
        shipping_minor=to_minor(row.shipping_income),
        cod_surcharge_minor=to_minor(row.cod_surcharge_sum),
        refunds_minor=to_minor(row.refund_sum),
        net_revenue_minor=to_minor(row.net_revenue),
    )


def _jsonable(value: Any) -> Any:
    """Decimals to strings, recursively, for the alert's JSON column.

    ``json.dumps`` cannot encode a Decimal, and floating them would corrupt the
    money figures the alert exists to preserve — an alert that cannot justify
    itself later is not worth writing.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value
