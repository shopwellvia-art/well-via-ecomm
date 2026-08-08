"""Shadow mode: run the legacy admin pages and the new analytics subsystem over
the same window and account for every rupee of difference between them.

Why this exists
---------------
The legacy Sales and Profit pages stay primary until the new system is *proven*
equivalent. This job is that proof. Retiring ``dashboard_service.py``,
``analytics_service.py`` and ``profit_service.py`` because the new numbers "look
right" is a leap of faith; the feature-flag rollout only buys anything if
something is measuring both sides while both are live.

The single idea the whole module is built around
------------------------------------------------
There are two kinds of difference and confusing them is the only way this job
can fail:

* **EXPECTED** — a difference that exists because a definition was deliberately
  changed. Store-local reporting days instead of the legacy UTC rolling window.
  A refunded order's sale staying in the period it was made. Discounts moving
  from C3 to CM1. Costs resolved from effective-dated rules instead of five
  settings keys. Each of these is *named*, *written down* in
  ``docs/analytics/RECONCILIATION.md``, and — the part that matters — each is
  attributed a **measured amount**, not a hand-wave.

* **UNEXPLAINED** — everything else. A defect in one of the two systems, and it
  alerts.

A difference with no registered explanation is UNEXPLAINED **by default**. There
is no path through :func:`classify` that reaches ``EXPECTED`` without a
registered explanation naming the exact number it accounts for. If that ever
inverts — if an unknown delta can be waved through as "probably the timezone
thing" — the job stops being evidence and becomes a rubber stamp, which is worse
than not running it at all, because it would be *cited*.

How a difference is attributed
------------------------------
Not by modelling. By measuring the same thing twice.

Every metric is computed three times:

``legacy``
    The legacy service over the legacy window — ``[date_from, date_to)`` as UTC
    instants, which is what ``dashboard_service._period_bounds`` produces and
    therefore what the legacy pages actually show.
``legacy_aligned``
    The **same legacy code**, over the **store-local** window. The gap between
    this and ``legacy`` is therefore the timezone re-bucketing, *measured on the
    legacy implementation itself* rather than asserted.
``new``
    The new subsystem: the rollup tables where the new pages will read one, the
    services where they will not.

That splits the total delta into two components that are independently
explainable:

* ``legacy_aligned - legacy`` — timezone bucketing, and nothing else can live
  here, because both sides are the same code over two windows.
* ``new - legacy_aligned`` — definitional, over one identical window. Each
  registered explanation contributes a :class:`BridgeTerm` carrying the exact
  paise it accounts for, queried from the source rows. Whatever is left after
  every term is the **residual**, and a residual outside tolerance is a defect.

A partially-explained difference is not an explained difference. Explaining
₹900 of a ₹1,000 delta leaves ₹100 unexplained and alerts on ₹100 — it does not
launder the remainder.

Service figures and rollup figures are both compared
----------------------------------------------------
``net_revenue`` is compared twice, once against :class:`MarginService` and once
against ``agg_order_daily``. They are two different claims: what the definition
says, and what the table the dashboard reads actually holds. A correction that
lands in a service and not in the rollup that feeds the screen has not landed,
and comparing only one of them would miss it in whichever direction the miss
happened to be.

Tolerance
---------
Money is compared at **1 paisa**. That is not a fudge factor; it is the smallest
unit of money that exists, and it is there because the legacy services cross a
``Decimal -> float`` boundary (``float(summary["revenue"])``) and
``profit_service`` does its cost arithmetic in binary floating point. The new
side is integer paise end to end. One paisa is the width of that representation
gap and nothing wider. Counts are compared at **zero** — an order count has no
rounding error and any difference in one is real. Percentages are compared at
**0.05pp**, half the 0.1pp that ``profit_service``'s ``round(x, 1)`` throws away.
The parity anchor is compared at **zero**, because it is the same query on both
sides and "close" would defeat the purpose of pinning it.

Per-day paise rounding is handled separately and explicitly: cost rules resolve
and round **per day**, so an N-day window can differ from an unrounded float by
up to N paise per rule-resolved component. That is registered as
``paisa_rounding``, and it is the one explanation that contributes an upper
**bound** rather than an exact amount. A bound absorbs a residual only up to its
stated size; it can never absorb an arbitrary delta.

What this module may not do
---------------------------
It does not modify the legacy services, and it calls them rather than restating
them wherever they expose a windowed helper — ``DashboardService._revenue_summary``
and ``AnalyticsService._summary`` / ``_by_category`` all take explicit bounds, so
the comparison runs the real code and cannot drift from it.

``ProfitService.profit()`` is the exception. It has no windowed helper: the whole
cascade is inlined against ``period_start..now``, so it cannot be asked about an
arbitrary window without editing it, and not editing it is exactly what shadow
mode is for. :func:`_legacy_profit` is a transcription of that method, marked as
such, and pinned by ``test_analytics_shadow.py`` against hand-computed figures.
Any drift between them is itself a defect this job should be extended to catch.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, desc, func, or_, select
from sqlalchemy.orm import Session

from app.models.analytics_control import (
    AlertRuleKey,
    AlertSeverity,
    AlertStatus,
    AnalyticsAlert,
    AnalyticsSyncRun,
    CostType,
    SyncStatus,
    SyncTrigger,
)
from app.models.analytics_rollups import AggOrderDaily, AggProductDaily
from app.models.order import Order, OrderItem
from app.models.product import Category, Product
from app.services.analytics.contracts import from_minor, to_minor
from app.services.analytics.margin import (
    CM2_COST_TYPES,
    CM3_COST_TYPES,
    SHIPPING_INCOME,
    MarginService,
    _recognised_sale,
)
from app.services.analytics.timebox import (
    active_generation,
    range_bounds_utc,
    store_timezone,
)
from app.services.analytics_service import AnalyticsService
from app.services.dashboard_service import _REVENUE_STATUSES, DashboardService
from app.services.settings_service import SettingsService

__all__ = [
    "DifferenceKind",
    "Explanation",
    "EXPECTED_DIFFERENCE_REGISTER",
    "explanation",
    "BridgeTerm",
    "Difference",
    "MetricComparison",
    "ShadowReport",
    "compare",
    "classify",
    "is_ready_to_retire_legacy",
    "verify_register_is_documented",
    "RECONCILIATION_DOC",
    "CONSECUTIVE_DAYS_REQUIRED",
    "GATE_METRICS",
    "ANCHOR_METRIC",
    "MONEY_TOLERANCE_MINOR",
    "COUNT_TOLERANCE",
    "PCT_TOLERANCE_BP",
    "SHADOW_JOB_NAME",
]


# ===========================================================================
# Constants
# ===========================================================================

#: The written register. Every explanation below must have a section here whose
#: heading is exactly ``### <key>``; :func:`verify_register_is_documented`
#: enforces it and the retirement gate refuses to pass without it. "Documented"
#: has to mean a human wrote prose another human can read, not that a Python
#: string exists.
RECONCILIATION_DOC = (
    Path(__file__).resolve().parents[4] / "docs" / "analytics" / "RECONCILIATION.md"
)

#: ``analytics_sync_runs.job`` for this comparison. Shadow mode is a pipeline job
#: like any other and a run that did not happen has to leave a trace, for the
#: same reason the rollup jobs log: "the comparison was clean for 30 days" and
#: "the comparison has not run for 30 days" produce identical silence otherwise.
SHADOW_JOB_NAME = "shadow_compare"

#: Consecutive clean reporting days required before the legacy pages may go.
CONSECUTIVE_DAYS_REQUIRED = 30

#: The metrics the gate is actually about. Everything else is compared and
#: reported, but revenue / orders / AOV are the three numbers the business reads
#: off the legacy pages, and they are what "equivalent" has to mean.
GATE_METRICS: tuple[str, ...] = (
    "paid_order_value_anchor",
    "paid_order_value",
    "orders",
    "aov",
)

#: One paisa. See the module docstring — this is the currency's resolution and
#: the width of the legacy float boundary, not a tolerance for being wrong.
MONEY_TOLERANCE_MINOR = 1

#: Zero. A count cannot round.
COUNT_TOLERANCE = 0

#: Percentages are carried as basis points (1bp = 0.01pp) so every comparison is
#: integer arithmetic. 5bp = 0.05pp, half of profit_service's ``round(x, 1)``.
PCT_TOLERANCE_BP = 5

#: The parity anchor. ``margin.paid_order_value`` is pinned byte-identical to
#: ``DashboardService._revenue_summary``: same statuses, same window, same
#: ``Decimal(x or 0)`` boundary. Compared over one identical window with **zero**
#: tolerance and **no** explanation permitted. If this metric ever differs, the
#: reconciliation between the legacy pages and this subsystem has lost its
#: meaning and nothing else in the report can be trusted either.
ANCHOR_METRIC = "paid_order_value_anchor"

_UNIT_MONEY = "money"
_UNIT_COUNT = "count"
_UNIT_PCT = "pct"

_TOLERANCE_BY_UNIT: dict[str, int] = {
    _UNIT_MONEY: MONEY_TOLERANCE_MINOR,
    _UNIT_COUNT: COUNT_TOLERANCE,
    _UNIT_PCT: PCT_TOLERANCE_BP,
}


class DifferenceKind(str, Enum):
    """What a difference means. There are exactly two and no third."""

    #: Definitional, registered, documented, and attributed to the paisa.
    EXPECTED = "EXPECTED"
    #: Everything else. A defect in one of the two systems.
    UNEXPLAINED = "UNEXPLAINED"


# ===========================================================================
# The register of expected differences
# ===========================================================================


@dataclass(frozen=True)
class Explanation:
    """One registered, documented reason two systems may legitimately disagree.

    ``applies_to`` is a whitelist, not decoration. An explanation may only be
    used for the metrics it names, so "the timezone thing" cannot be reached for
    when a CM3 figure moves. The register is what makes EXPECTED a claim rather
    than a mood.
    """

    #: Stable key. Also the heading in RECONCILIATION.md — ``### <key>``.
    key: str
    title: str
    #: What the difference is, in one sentence.
    summary: str
    #: "new" | "legacy" | "neither" — which side is right about this.
    correct_side: str
    #: Why that side is the correct one. This is the sentence someone reads when
    #: a number changed and they want to know whether to worry.
    why_correct: str
    #: How to prove it on real data, concretely enough to actually run.
    how_to_verify: str
    #: The metrics this explanation may be applied to.
    applies_to: frozenset[str]


def _keys(*names: str) -> frozenset[str]:
    return frozenset(names)


_REVENUE_METRICS = _keys(
    "paid_order_value",
    "net_revenue",
    "net_revenue_rollup",
    "orders",
    "aov",
    "discounts",
    "cogs",
    "cost_coverage_pct",
    "revenue_by_category",
    "cm1",
    "cm2",
    "cm3",
)

EXPECTED_DIFFERENCE_REGISTER: dict[str, Explanation] = {
    e.key: e
    for e in (
        Explanation(
            key="tz_bucketing",
            title="Store-local reporting days vs the legacy UTC rolling window",
            summary=(
                "The legacy pages filter orders.created_at on a UTC instant range "
                "(dashboard_service._period_bounds is `now - N days`, so the boundary "
                "is a wall-clock time of day in UTC). The new subsystem buckets on "
                "store-local reporting days via timebox.day_bounds_utc."
            ),
            correct_side="new",
            why_correct=(
                "With store.timezone = Asia/Kolkata an order at 23:00 IST is 17:30 UTC "
                "the same day and one at 05:00 IST is 23:30 UTC the day before, so a "
                "UTC boundary moves roughly 5.5 hours of trade into the neighbouring "
                "day, every day. 'Yesterday's sales' on the legacy page is not a day of "
                "this store's trading."
            ),
            how_to_verify=(
                "ShadowReport.boundary_orders lists the orders in the symmetric "
                "difference of the two windows with created_at in both zones. The tz "
                "component of every metric is measured by running the same legacy query "
                "over both windows, so it is an observation, not a model."
            ),
            applies_to=_REVENUE_METRICS,
        ),
        Explanation(
            key="revenue_recognition",
            title="A refunded order's sale stays in the period it was made",
            summary=(
                "The legacy rule is a status filter (PAID/SHIPPED/DELIVERED), so an "
                "order refunded later leaves its original period retroactively. The new "
                "rule recognises the sale in the period of orders.created_at when the "
                "order reached a paid state, including one refunded since, provided a "
                "reversal can actually be dated for it."
            ),
            correct_side="new",
            why_correct=(
                "orders.status is mutable and REFUNDED is terminal, so under the legacy "
                "rule a January sale refunded in March silently removes itself from "
                "January: a closed month changes because of an event two months later. "
                "The sale and its reversal are two events in two periods and each "
                "belongs in its own."
            ),
            how_to_verify=(
                "SELECT id, created_at, refunded_at, total_amount FROM orders WHERE "
                "status='refunded' AND created_at >= :start AND created_at < :end. "
                "Their total_amount is exactly what this explanation attributes, and it "
                "is reported as the `revenue_recognition` bridge term."
            ),
            applies_to=_REVENUE_METRICS,
        ),
        Explanation(
            key="refund_timing",
            title="A reversal is recognised in the period of its own refunded_at",
            summary=(
                "net_revenue subtracts refunds dated in the window by "
                "orders.refunded_at / returns.refunded_at, whatever period the order "
                "they reverse was created in. The legacy pages have no refund term at "
                "all — they reverse by dropping the order."
            ),
            correct_side="new",
            why_correct=(
                "Dropping the order AND subtracting the refund reverses the same money "
                "twice and reports a negative figure for a window whose answer is zero. "
                "The legacy pages do the first and the corrected definition does the "
                "second; doing both was a real defect fixed in margin.py, and the "
                "revenue bridge balanced all the way through it because both of its "
                "sides shared the wrong input."
            ),
            how_to_verify=(
                "SELECT SUM(refund_amount) FROM returns WHERE refunded_at >= :start AND "
                "refunded_at < :end, plus whole-order refunds with no refunded return "
                "row valued at orders.total_amount. Equals "
                "MarginService.recognised_revenue().refunds_valued_minor."
            ),
            applies_to=_keys("net_revenue", "net_revenue_rollup"),
        ),
        Explanation(
            key="payment_discount_included",
            title="Discounts include the payment/gateway offer",
            summary=(
                "AnalyticsService._summary reports SUM(orders.discount_amount). The new "
                "`discounts` figure is SUM(discount_amount + payment_discount_amount)."
            ),
            correct_side="new",
            why_correct=(
                "A 10% card offer is money the store gave away exactly as a coupon is. "
                "Leaving it out understates discounting and overstates net merchandise "
                "sales by the same amount, and it is invisible on the legacy page "
                "because nothing there shows the column exists."
            ),
            how_to_verify=(
                "SELECT SUM(payment_discount_amount) FROM orders over the window and "
                "the revenue statuses; that is the whole of this difference."
            ),
            applies_to=_keys("discounts"),
        ),
        Explanation(
            key="category_snapshot",
            title="Category mix uses the category as it was on the sale date",
            summary=(
                "The legacy breakdown joins products.category_id live, so "
                "re-categorising a product rewrites every past period. "
                "agg_product_daily stores category_id_snapshot as it was when the "
                "bucket was computed."
            ),
            correct_side="new",
            why_correct=(
                "A report about March must not change in July because someone tidied "
                "the catalogue. Snapshotting surprises people once; a silently restated "
                "history surprises them repeatedly and without warning."
            ),
            how_to_verify=(
                "SELECT p.id, p.category_id, a.category_id_snapshot FROM "
                "agg_product_daily a JOIN products p ON p.id = a.product_id WHERE "
                "a.bucket_date >= :from AND a.bucket_date < :to AND "
                "COALESCE(a.category_id_snapshot,0) <> COALESCE(p.category_id,0)."
            ),
            applies_to=_keys("revenue_by_category"),
        ),
        Explanation(
            key="shipping_income_not_a_cost",
            title="orders.shipping_amount is income, not an expense",
            summary=(
                "profit_service excludes shipping from revenue (revenue is line-level "
                "SUM(qty*unit_price)) and then subtracts SUM(orders.shipping_amount) as "
                "a cost — a 2x penalty on money the customer paid us. The new cascade "
                "adds it inside CM2 and takes the real carrier charge from "
                "shipments.shipment_cost instead."
            ),
            correct_side="new",
            why_correct=(
                "orders.shipping_amount is what the customer paid; the carrier charge is "
                "shipments.shipment_cost, which profit_service never reads at all. The "
                "legacy C1 is understated by twice the shipping income and the error "
                "grows with delivery volume."
            ),
            how_to_verify=(
                "SELECT SUM(shipping_amount) FROM orders over the window; that figure "
                "appears twice in the legacy cascade with the wrong sign and once in the "
                "new one with the right sign."
            ),
            applies_to=_keys("cm1", "cm2", "cm3"),
        ),
        Explanation(
            key="discount_in_nms",
            title="Discounts are deducted at CM1, not at C3",
            summary=(
                "The new cascade starts from net_merchandise_sales (line revenue less "
                "order-level discounts). profit_service starts from gross line revenue "
                "and subtracts marketing_discounts three levels later, at C3."
            ),
            correct_side="new",
            why_correct=(
                "A discount is a reduction of the sale price, not a marketing expense "
                "incurred after gross profit. Placing it at C3 inflates C1 and C2 by the "
                "full discount and makes gross margin look better than it is."
            ),
            how_to_verify=(
                "SELECT SUM(discount_amount + payment_discount_amount) over the window's "
                "revenue-status orders. CM1 and C1 differ by exactly this amount plus "
                "the shipping and packing/handling terms."
            ),
            applies_to=_keys("cm1", "cm2", "cm3"),
        ),
        Explanation(
            key="cost_rules_vs_settings",
            title="Costs come from effective-dated rules, not five settings keys",
            summary=(
                "profit_service reads costs.packing_per_order, "
                "costs.handling_per_order, costs.monthly_overheads and "
                "costs.monthly_ad_spend as flat current values and applies today's "
                "number to the whole window. The new cascade resolves "
                "analytics_cost_rules per day against that day's drivers, and charges "
                "packing/handling at CM2 rather than C1."
            ),
            correct_side="new",
            why_correct=(
                "A settings key has no history, so raising packing cost today "
                "retroactively restates every past period on the legacy page. Rules are "
                "effective-dated, so a rate change applies to the days on each side of "
                "it instead of being averaged across the window."
            ),
            how_to_verify=(
                "SELECT * FROM analytics_cost_rules WHERE cost_type IN "
                "('packaging','handling','marketing_spend') AND effective_from <= :day "
                "AND (effective_to IS NULL OR effective_to >= :day), against the "
                "matching costs.* rows in system_settings."
            ),
            applies_to=_keys("cm1", "cm2", "cm3"),
        ),
        Explanation(
            key="gateway_fee_basis",
            title="Gateway fees are charged on money that went through a gateway",
            summary=(
                "profit_service applies costs.gateway_fee_pct to SUM(total_amount) WHERE "
                "payment_method='prepaid'. The new cascade applies a GATEWAY_FEE rule to "
                "(total_amount - cod_balance)."
            ),
            correct_side="new",
            why_correct=(
                "total_amount - cod_balance is the money that actually travelled through "
                "a gateway. It is exact for prepaid (cod_balance 0), for COD "
                "(cod_balance == total) and for split COD, with no special case — the "
                "legacy string test on payment_method charges a full fee on the cash leg "
                "of a split order and nothing at all on a non-'prepaid' online method."
            ),
            how_to_verify=(
                "SELECT SUM(total_amount - cod_balance), SUM(CASE WHEN "
                "payment_method='prepaid' THEN total_amount ELSE 0 END) FROM orders over "
                "the window. Any gap is split COD or a mislabelled payment method."
            ),
            applies_to=_keys("cm2", "cm3"),
        ),
        Explanation(
            key="carrier_and_logistics_costs",
            title="Forward shipping, return shipping, RTO and commission exist",
            summary=(
                "CM2 subtracts FORWARD_SHIPPING, RETURN_SHIPPING, RTO_LOGISTICS and "
                "MARKETPLACE_COMMISSION. profit_service has no term for any of them."
            ),
            correct_side="new",
            why_correct=(
                "These are real cash costs. A return that was picked up cost a reverse "
                "shipment; an RTO cost the forward leg and the return leg and produced "
                "no revenue. Omitting them makes contribution margin flattering in "
                "exactly the direction nobody investigates."
            ),
            how_to_verify=(
                "ShadowReport reports each component with its resolved value; "
                "cross-check against shipments.shipment_cost for the orders in the "
                "window and the FORWARD_SHIPPING rule for the orders with no shipment "
                "cost."
            ),
            applies_to=_keys("cm2", "cm3"),
        ),
        Explanation(
            key="missing_cost_input",
            title="A missing cost input blanks the level instead of assuming zero",
            summary=(
                "When no analytics_cost_rules row covers a component, the new cascade "
                "reports that CM level as None and names the missing input. "
                "profit_service reads a missing settings key as 0.0."
            ),
            correct_side="new",
            why_correct=(
                "A zero cost silently inflates margin, and an inflated margin is the one "
                "error nobody goes looking for. Reporting nothing forces the gap to be "
                "closed; reporting zero hides it behind a plausible number."
            ),
            how_to_verify=(
                "MarginResult.missing_inputs names the components; each maps to a "
                "cost_type with no covering analytics_cost_rules row for some day in the "
                "window."
            ),
            applies_to=_keys("cm1", "cm2", "cm3"),
        ),
        Explanation(
            key="cogs_null_not_zeroed",
            title="A line with no unit_cost is excluded from COGS, not costed at zero",
            summary=(
                "profit_service does COALESCE(unit_cost, 0), so an uncosted line "
                "contributes zero COGS and reports 100% margin on itself. The new COGS "
                "sum drops those rows and still counts them in the coverage denominator, "
                "which grades the whole result INCOMPLETE."
            ),
            correct_side="new",
            why_correct=(
                "The two produce the same COGS total — SQL drops NULLs from SUM either "
                "way — so this difference is not in the money, it is in what the number "
                "claims about itself. The legacy page prints a confident margin over "
                "partial cost data with nothing on screen to say so."
            ),
            how_to_verify=(
                "SELECT COUNT(*) FROM order_items oi JOIN orders o ON o.id = oi.order_id "
                "WHERE oi.unit_cost IS NULL over the window; compare cost_coverage_pct "
                "on both sides and read MarginResult.quality."
            ),
            applies_to=_keys("cogs", "cost_coverage_pct", "cm1", "cm2", "cm3"),
        ),
        Explanation(
            key="overheads_excluded_from_cm3",
            title="CM3 is a contribution figure and stops before overheads",
            summary=(
                "profit_service's net_profit subtracts costs.monthly_overheads. CM3 does "
                "not: it is contribution margin after marketing, not net profit."
            ),
            correct_side="neither",
            why_correct=(
                "They answer different questions and both are legitimate. This entry "
                "exists so nobody puts CM3 and net_profit on one screen and reads the "
                "gap as an error. Compare CM3 to C3, never to net profit — which is what "
                "this job does."
            ),
            how_to_verify=(
                "costs.monthly_overheads pro-rated over the window is the whole of the "
                "gap between legacy net_profit and legacy C3."
            ),
            applies_to=_keys("cm3"),
        ),
        Explanation(
            key="paisa_rounding",
            title="Per-day paise rounding against unrounded legacy floats",
            summary=(
                "Cost rules resolve and round to the paisa once per day, because rates "
                "are effective-dated by day. profit_service multiplies Python floats and "
                "never rounds at all; AOV is a quotient computed at 28-digit Decimal "
                "precision on one side and in integer paise on the other."
            ),
            correct_side="new",
            why_correct=(
                "Money is integer paise end to end in the new subsystem; binary floating "
                "point cannot represent 0.10 and margin arithmetic feeds financial "
                "reporting. The cost is that an N-day window can differ by up to N paise "
                "per rounded component."
            ),
            how_to_verify=(
                "The bound is (days x rule-resolved components) and is reported as such "
                "on the difference. This is the only explanation that contributes a "
                "bound rather than a measured amount, and it can never absorb more than "
                "that bound."
            ),
            applies_to=_keys("cm1", "cm2", "cm3", "aov", "cost_coverage_pct"),
        ),
    )
}


def explanation(key: str) -> Explanation | None:
    """The register entry for ``key``, or ``None``. ``None`` means UNEXPLAINED."""
    return EXPECTED_DIFFERENCE_REGISTER.get(key)


# ===========================================================================
# Result shapes
# ===========================================================================


@dataclass(frozen=True)
class BridgeTerm:
    """One registered explanation's measured contribution to a delta.

    ``amount`` is signed in the direction ``new - legacy`` and is *queried from
    the source rows*, never inferred from the delta it is supposed to explain —
    inferring it would make every difference explicable by construction.

    ``bound`` is the alternative for arithmetic that has no exact answer (per-day
    paise rounding). It absorbs residual up to its own size and not one paisa
    more.
    """

    explanation: str
    amount: int = 0
    bound: int = 0
    note: str = ""


@dataclass(frozen=True)
class Difference:
    """One accounted-for, or unaccounted-for, gap on one metric."""

    metric: str
    kind: DifferenceKind
    #: "tz" (legacy_aligned - legacy) or "definitional" (new - legacy_aligned).
    component: str
    delta: int
    #: Sum of the exact bridge terms that were accepted.
    explained: int
    #: Bound available beyond the exact terms.
    bounded: int
    #: What is left. Within tolerance means EXPECTED; anything else is a defect.
    residual: int
    unit: str
    explanations: tuple[str, ...] = ()
    dimension: str = "-"
    dimension_value: str = "-"
    note: str = ""

    @property
    def is_unexplained(self) -> bool:
        return self.kind is DifferenceKind.UNEXPLAINED

    def describe(self) -> str:
        where = (
            f" [{self.dimension}={self.dimension_value}]"
            if self.dimension_value != "-"
            else ""
        )
        body = (
            f"{self.metric}{where} {self.component}: "
            f"{_fmt(self.delta, self.unit)} delta, "
            f"{_fmt(self.explained, self.unit)} explained"
        )
        if self.bounded:
            body += f" (+{_fmt(self.bounded, self.unit)} bounded)"
        body += f", residual {_fmt(self.residual, self.unit)}"
        if self.explanations:
            body += f" via {', '.join(self.explanations)}"
        if self.note:
            body += f" — {self.note}"
        return body


@dataclass(frozen=True)
class MetricComparison:
    """One metric measured three times: legacy, legacy-aligned, and new.

    ``new`` is ``None`` when the new subsystem *refused* to report a figure — a
    missing cost input blanks a CM level rather than assuming zero. That is a
    difference with an explanation, not an absence of data.
    """

    metric: str
    unit: str
    legacy: int | None
    legacy_aligned: int | None
    new: int | None
    terms: tuple[BridgeTerm, ...] = ()
    dimension: str = "-"
    dimension_value: str = "-"
    #: Explanations this metric may use at all. The anchor's is empty on purpose.
    permitted: frozenset[str] = frozenset()
    missing_reason: str = ""
    #: Overrides the unit default. Used only by the anchor, which is exact.
    tolerance_override: int | None = None
    new_source: str = ""

    @property
    def tolerance(self) -> int:
        if self.tolerance_override is not None:
            return self.tolerance_override
        return _TOLERANCE_BY_UNIT[self.unit]

    @property
    def tz_component(self) -> int:
        if self.legacy is None or self.legacy_aligned is None:
            return 0
        return self.legacy_aligned - self.legacy

    @property
    def definitional_component(self) -> int:
        if self.new is None or self.legacy_aligned is None:
            return 0
        return self.new - self.legacy_aligned

    @property
    def delta(self) -> int | None:
        if self.new is None or self.legacy is None:
            return None
        return self.new - self.legacy


@dataclass(frozen=True)
class ShadowReport:
    """One window's comparison, and the evidence behind every claim in it."""

    date_from: date
    date_to: date
    generated_at: datetime
    timezone_name: str
    tz_generation: int
    legacy_window: tuple[datetime, datetime]
    aligned_window: tuple[datetime, datetime]
    comparisons: tuple[MetricComparison, ...]
    differences: tuple[Difference, ...]
    #: Reporting days in the window with no agg_order_daily row. A rollup cannot
    #: report its own absence, so it is reported here.
    missing_buckets: tuple[date, ...] = ()
    #: Orders in the symmetric difference of the two windows: the tz evidence.
    boundary_orders: tuple[dict[str, Any], ...] = ()
    alert_ids: tuple[int, ...] = ()
    sync_run_id: int | None = None
    warnings: tuple[str, ...] = ()

    @property
    def days(self) -> tuple[date, ...]:
        span = (self.date_to - self.date_from).days
        return tuple(self.date_from + timedelta(days=n) for n in range(max(span, 0)))

    @property
    def expected(self) -> tuple[Difference, ...]:
        return tuple(d for d in self.differences if d.kind is DifferenceKind.EXPECTED)

    @property
    def unexplained(self) -> tuple[Difference, ...]:
        return tuple(
            d for d in self.differences if d.kind is DifferenceKind.UNEXPLAINED
        )

    @property
    def is_clean(self) -> bool:
        """No unexplained difference anywhere in this window."""
        return not self.unexplained

    def comparison(
        self, metric: str, dimension_value: str = "-"
    ) -> MetricComparison | None:
        return next(
            (
                c
                for c in self.comparisons
                if c.metric == metric and c.dimension_value == dimension_value
            ),
            None,
        )

    def differences_for(self, metric: str) -> tuple[Difference, ...]:
        return tuple(d for d in self.differences if d.metric == metric)

    def gate_metrics_match(self) -> tuple[bool, list[str]]:
        """Are revenue / orders / AOV within tolerance, and if not, which."""
        reasons: list[str] = []
        seen: set[str] = set()
        for c in self.comparisons:
            if c.metric not in GATE_METRICS:
                continue
            seen.add(c.metric)
            if c.new is None:
                reasons.append(
                    f"{self.date_from}: {c.metric} has no value from the new "
                    f"subsystem ({c.missing_reason or 'no reason recorded'})"
                )
                continue
            delta = c.delta
            if delta is not None and abs(delta) > c.tolerance:
                reasons.append(
                    f"{self.date_from}: {c.metric} differs by {_fmt(delta, c.unit)} "
                    f"(legacy {_fmt(c.legacy, c.unit)}, new {_fmt(c.new, c.unit)}); "
                    f"tolerance is {_fmt(c.tolerance, c.unit)}"
                )
        for metric in GATE_METRICS:
            if metric not in seen:
                reasons.append(
                    f"{self.date_from}: {metric} was not compared in this report"
                )
        return (not reasons), reasons

    def summary(self) -> str:
        ok, _ = self.gate_metrics_match()
        return (
            f"shadow {self.date_from}..{self.date_to} "
            f"({self.timezone_name}, gen {self.tz_generation}): "
            f"{len(self.comparisons)} metrics, {len(self.expected)} expected, "
            f"{len(self.unexplained)} unexplained, "
            f"gate metrics {'match' if ok else 'DIFFER'}"
        )


# ===========================================================================
# Formatting
# ===========================================================================


def _fmt(value: int | None, unit: str) -> str:
    if value is None:
        return "-"
    if unit == _UNIT_MONEY:
        return str(from_minor(value))
    if unit == _UNIT_PCT:
        return f"{Decimal(value) / 100}pp"
    return str(value)


# ===========================================================================
# Classification — the whole point of the module
# ===========================================================================


def classify(comparison: MetricComparison) -> list[Difference]:
    """Turn one metric's three measurements into zero, one or two differences.

    Read this function before trusting any report it produced. The default is
    UNEXPLAINED, and there is no branch that reaches EXPECTED without a
    *registered* and *permitted* explanation contributing a measured amount:

    * an unregistered explanation key contributes nothing and is named in the
      note, so the delta it claimed stays in the residual;
    * an explanation that is registered but not permitted for this metric is
      treated identically;
    * a bound absorbs residual only up to its own size.

    A delta inside tolerance is not a difference and is not reported at all.
    """
    out: list[Difference] = []
    tolerance = comparison.tolerance

    if comparison.new is None:
        # The new subsystem refused to report a number. That is a difference, and
        # it is EXPECTED only if it names a registered, permitted reason.
        key = "missing_cost_input"
        allowed = (
            key in comparison.permitted
            and key in EXPECTED_DIFFERENCE_REGISTER
            and bool(comparison.missing_reason)
        )
        out.append(
            Difference(
                metric=comparison.metric,
                kind=DifferenceKind.EXPECTED if allowed else DifferenceKind.UNEXPLAINED,
                component="definitional",
                delta=0,
                explained=0,
                bounded=0,
                residual=0,
                unit=comparison.unit,
                explanations=(key,) if allowed else (),
                dimension=comparison.dimension,
                dimension_value=comparison.dimension_value,
                note=(
                    comparison.missing_reason
                    or "the new subsystem reported no value and gave no reason"
                ),
            )
        )
        return out

    # ---- Component 1: timezone re-bucketing -------------------------------
    # Both sides here are the SAME legacy code over two windows, so nothing but
    # the window can be responsible. It still has to be a registered, permitted
    # explanation for this metric, or it is a defect like any other.
    tz_delta = comparison.tz_component
    if abs(tz_delta) > tolerance:
        allowed = (
            "tz_bucketing" in comparison.permitted
            and "tz_bucketing" in EXPECTED_DIFFERENCE_REGISTER
        )
        out.append(
            Difference(
                metric=comparison.metric,
                kind=DifferenceKind.EXPECTED if allowed else DifferenceKind.UNEXPLAINED,
                component="tz",
                delta=tz_delta,
                explained=tz_delta if allowed else 0,
                bounded=0,
                residual=0 if allowed else tz_delta,
                unit=comparison.unit,
                explanations=("tz_bucketing",) if allowed else (),
                dimension=comparison.dimension,
                dimension_value=comparison.dimension_value,
                note=(
                    "same legacy query, store-local window minus UTC window"
                    if allowed
                    else "the window moved this metric, but tz_bucketing is not a "
                    "registered explanation for it"
                ),
            )
        )

    # ---- Component 2: definitional, over one identical window -------------
    definitional = comparison.definitional_component
    if abs(definitional) <= tolerance:
        # The two systems agree on this component. Nothing to explain and
        # nothing to report — see test_delta_inside_tolerance_is_not_a_difference.
        return out

    exact = 0
    bounded = 0
    used: list[str] = []
    bounders: list[str] = []
    rejected: list[str] = []
    for term in comparison.terms:
        registered = term.explanation in EXPECTED_DIFFERENCE_REGISTER
        permitted = term.explanation in comparison.permitted
        if not (registered and permitted):
            # A term naming an unregistered — or not-permitted-here — explanation
            # contributes NOTHING. It cannot reduce the residual, so the delta it
            # claimed stays unexplained. This is the default the module exists to
            # protect.
            rejected.append(term.explanation)
            continue
        exact += term.amount
        bounded += abs(term.bound)
        # Only an explanation that actually moved the number is credited. A term
        # that measured zero explains nothing and must not appear as though it
        # did; a bound is credited below, and only if it absorbs something.
        if term.amount and term.explanation not in used:
            used.append(term.explanation)
        if term.bound and term.explanation not in bounders:
            bounders.append(term.explanation)

    residual = definitional - exact
    if bounded and abs(residual) <= bounded:
        if residual:
            used.extend(key for key in bounders if key not in used)
        residual = 0

    kind = (
        DifferenceKind.EXPECTED
        if (abs(residual) <= tolerance and used)
        else DifferenceKind.UNEXPLAINED
    )

    notes: list[str] = []
    if rejected:
        notes.append(
            "bridge term(s) rejected — not registered, or not permitted for this "
            f"metric: {', '.join(sorted(set(rejected)))}"
        )
    if kind is DifferenceKind.UNEXPLAINED:
        notes.append(
            "no registered explanation accounts for this; it is a defect in the "
            "legacy service, the new service, or the rollup between them"
        )
    out.append(
        Difference(
            metric=comparison.metric,
            kind=kind,
            component="definitional",
            delta=definitional,
            explained=exact,
            bounded=bounded,
            residual=residual,
            unit=comparison.unit,
            explanations=tuple(used),
            dimension=comparison.dimension,
            dimension_value=comparison.dimension_value,
            note="; ".join(notes),
        )
    )
    return out


# ===========================================================================
# Legacy side
# ===========================================================================


def _minor_from_float(value: float) -> int:
    """Recover a legacy float back to exact paise.

    ``repr(float)`` is the shortest string that round-trips, so
    ``Decimal(str(x))`` reproduces the Decimal the legacy service converted from,
    for any figure this store will hold. That is why money can be compared at one
    paisa rather than at some percentage.
    """
    return to_minor(Decimal(str(value)))


@dataclass(frozen=True)
class _LegacySales:
    """What the legacy Dashboard and Sales pages report for a window."""

    revenue_minor: int
    orders: int
    aov_minor: int
    discounts_minor: int
    by_category_minor: dict[str, int]


def _legacy_sales(db: Session, start: datetime, end: datetime) -> _LegacySales:
    """Run the real legacy code over an explicit window.

    ``DashboardService._revenue_summary`` and ``AnalyticsService._summary`` /
    ``_by_category`` all take explicit bounds, so this calls them rather than
    restating their SQL. A transcription could drift, and a drifted comparison
    would report the drift as a defect in the new system.
    """
    summary = DashboardService(db)._revenue_summary(start, end)
    sales = AnalyticsService(db)
    sales_summary = sales._summary(start, end)
    categories = sales._by_category(start, end)
    return _LegacySales(
        revenue_minor=to_minor(summary["revenue"]),
        orders=int(summary["count"]),
        # aov is Decimal(revenue)/count at full precision on the legacy side.
        aov_minor=to_minor(Decimal(summary["aov"]).quantize(Decimal("0.01"))),
        discounts_minor=_minor_from_float(sales_summary["discounts"]),
        by_category_minor={
            row["category"]: _minor_from_float(row["revenue"]) for row in categories
        },
    )


@dataclass(frozen=True)
class _LegacyProfit:
    """``ProfitService.profit()`` over an explicit window. A TRANSCRIPTION.

    ``profit_service.py`` inlines the whole cascade against ``period_start..now``
    and exposes no windowed helper, so it cannot be asked about an arbitrary
    range without editing it — and not editing it is the point of shadow mode.
    Every field below is the same expression as the corresponding local in
    ``ProfitService.profit``, **including its defects**, which are what this job
    measures. ``test_analytics_shadow.py`` pins the arithmetic against
    hand-computed figures.
    """

    line_revenue_minor: int
    product_cost_minor: int
    coverage_bp: int | None
    lines: int
    costed_lines: int
    shipping_minor: int
    orders: int
    packing_minor: int
    handling_minor: int
    c1_minor: int
    gateway_minor: int
    c2_minor: int
    marketing_discounts_minor: int
    ad_spend_minor: int
    c3_minor: int
    overheads_minor: int
    net_profit_minor: int


def _cost_setting(svc: SettingsService, key: str) -> float:
    """``profit_service._get_cost_float``, transcribed: missing/invalid -> 0.0."""
    raw = svc.get_raw(key, default="0")
    if not raw:
        return 0.0
    try:
        return float(raw)
    except (ValueError, TypeError):
        return 0.0


def _legacy_profit(db: Session, start: datetime, end: datetime) -> _LegacyProfit:
    """The legacy C1/C2/C3 cascade over ``[start, end)``."""
    svc = SettingsService(db)
    packing_per_order = _cost_setting(svc, "costs.packing_per_order")
    handling_per_order = _cost_setting(svc, "costs.handling_per_order")
    gateway_fee_pct = _cost_setting(svc, "costs.gateway_fee_pct")
    monthly_overheads = _cost_setting(svc, "costs.monthly_overheads")
    monthly_ad_spend = _cost_setting(svc, "costs.monthly_ad_spend")
    period_days = max((end - start).days, 0)

    window = (
        Order.status.in_(_REVENUE_STATUSES),
        Order.created_at >= start,
        Order.created_at < end,
    )

    item_agg = db.execute(
        select(
            func.coalesce(func.sum(OrderItem.quantity * OrderItem.unit_price), 0).label(
                "revenue"
            ),
            # COALESCE(unit_cost, 0) — inherited bug 2, transcribed on purpose.
            func.coalesce(
                func.sum(OrderItem.quantity * func.coalesce(OrderItem.unit_cost, 0)), 0
            ).label("product_cost"),
            func.count(OrderItem.id).label("total_items"),
            func.coalesce(
                func.sum(case((OrderItem.unit_cost.isnot(None), 1), else_=0)), 0
            ).label("costed_items"),
        )
        .select_from(OrderItem)
        .join(Order, Order.id == OrderItem.order_id)
        .where(*window)
    ).one()

    order_agg = db.execute(
        select(
            func.count(Order.id).label("order_count"),
            func.coalesce(func.sum(Order.shipping_amount), 0).label("shipping_cost"),
            func.coalesce(
                func.sum(
                    case(
                        (Order.payment_method == "prepaid", Order.total_amount),
                        else_=0,
                    )
                ),
                0,
            ).label("prepaid_total"),
            func.coalesce(
                func.sum(Order.discount_amount + Order.payment_discount_amount), 0
            ).label("marketing_discounts"),
        ).where(*window)
    ).one()

    revenue = float(item_agg.revenue or 0)
    product_cost = float(item_agg.product_cost or 0)
    total_items = int(item_agg.total_items or 0)
    costed_items = int(item_agg.costed_items or 0)
    order_count = int(order_agg.order_count or 0)
    shipping_cost = float(order_agg.shipping_cost or 0)
    prepaid_total = float(order_agg.prepaid_total or 0)
    marketing_discounts = float(order_agg.marketing_discounts or 0)

    packing_cost = packing_per_order * order_count
    handling_cost = handling_per_order * order_count
    c1 = revenue - product_cost - shipping_cost - packing_cost - handling_cost
    gateway_fees = (gateway_fee_pct / 100.0) * prepaid_total
    c2 = c1 - gateway_fees
    ad_spend = monthly_ad_spend * (period_days / 30.0)
    c3 = c2 - marketing_discounts - ad_spend
    overheads = monthly_overheads * (period_days / 30.0)
    net_profit = c3 - overheads

    coverage_bp = (
        int(round(round(100.0 * costed_items / total_items, 1) * 100))
        if total_items > 0
        else None
    )

    return _LegacyProfit(
        line_revenue_minor=_minor_from_float(revenue),
        product_cost_minor=_minor_from_float(product_cost),
        coverage_bp=coverage_bp,
        lines=total_items,
        costed_lines=costed_items,
        shipping_minor=_minor_from_float(shipping_cost),
        orders=order_count,
        packing_minor=_minor_from_float(packing_cost),
        handling_minor=_minor_from_float(handling_cost),
        c1_minor=_minor_from_float(c1),
        gateway_minor=_minor_from_float(gateway_fees),
        c2_minor=_minor_from_float(c2),
        marketing_discounts_minor=_minor_from_float(marketing_discounts),
        ad_spend_minor=_minor_from_float(ad_spend),
        c3_minor=_minor_from_float(c3),
        overheads_minor=_minor_from_float(overheads),
        net_profit_minor=_minor_from_float(net_profit),
    )


# ===========================================================================
# New side
# ===========================================================================


@dataclass(frozen=True)
class _Rollups:
    """What ``agg_order_daily`` / ``agg_product_daily`` hold for the window."""

    paid_order_value_minor: int
    net_revenue_minor: int
    discounts_minor: int
    orders: int
    gms_minor: int
    refunds_minor: int
    by_category_minor: dict[str, int]
    #: Reporting days in the window with no agg_order_daily row at all.
    missing_buckets: tuple[date, ...]


def _rollup_figures(
    db: Session, date_from: date, date_to: date, generation: int
) -> _Rollups:
    row = db.execute(
        select(
            func.coalesce(func.sum(AggOrderDaily.paid_order_value), 0).label("paid"),
            func.coalesce(func.sum(AggOrderDaily.net_revenue), 0).label("net"),
            func.coalesce(func.sum(AggOrderDaily.discount_sum), 0).label("discounts"),
            func.coalesce(func.sum(AggOrderDaily.gross_merchandise_sales), 0).label(
                "gms"
            ),
            func.coalesce(func.sum(AggOrderDaily.refund_sum), 0).label("refunds"),
            func.coalesce(
                func.sum(
                    AggOrderDaily.orders_paid
                    + AggOrderDaily.orders_shipped
                    + AggOrderDaily.orders_delivered
                ),
                0,
            ).label("orders"),
        ).where(
            AggOrderDaily.bucket_date >= date_from,
            AggOrderDaily.bucket_date < date_to,
            AggOrderDaily.tz_generation == generation,
        )
    ).one()

    present = set(
        db.execute(
            select(AggOrderDaily.bucket_date).where(
                AggOrderDaily.bucket_date >= date_from,
                AggOrderDaily.bucket_date < date_to,
                AggOrderDaily.tz_generation == generation,
            )
        )
        .scalars()
        .all()
    )
    span = max((date_to - date_from).days, 0)
    missing = tuple(
        day
        for day in (date_from + timedelta(days=n) for n in range(span))
        if day not in present
    )

    # Category mix from the product rollup, keyed on the SNAPSHOTTED category and
    # labelled through the live Category table: the id is the dimension, the name
    # is only a label, and renaming a category is not re-parenting a product.
    cat_rows = db.execute(
        select(
            AggProductDaily.category_id_snapshot,
            func.coalesce(func.sum(AggProductDaily.gross_merchandise_sales), 0),
        )
        .where(
            AggProductDaily.bucket_date >= date_from,
            AggProductDaily.bucket_date < date_to,
            AggProductDaily.tz_generation == generation,
        )
        .group_by(AggProductDaily.category_id_snapshot)
    ).all()
    names = _category_names(db, [r[0] for r in cat_rows if r[0] is not None])
    by_category: dict[str, int] = {}
    for category_id, revenue in cat_rows:
        label = (
            names.get(int(category_id), "Uncategorized")
            if category_id
            else "Uncategorized"
        )
        by_category[label] = by_category.get(label, 0) + to_minor(revenue)

    return _Rollups(
        paid_order_value_minor=to_minor(row.paid),
        net_revenue_minor=to_minor(row.net),
        discounts_minor=to_minor(row.discounts),
        orders=int(row.orders or 0),
        gms_minor=to_minor(row.gms),
        refunds_minor=to_minor(row.refunds),
        by_category_minor=by_category,
        missing_buckets=missing,
    )


def _category_names(db: Session, ids: Sequence[Any]) -> dict[int, str]:
    clean = sorted({int(i) for i in ids if i})
    if not clean:
        return {}
    rows = db.execute(
        select(Category.id, Category.name).where(Category.id.in_(clean))
    ).all()
    return {int(r[0]): r[1] for r in rows}


# ===========================================================================
# Evidence
# ===========================================================================


def _boundary_orders(
    db: Session,
    legacy_start: datetime,
    legacy_end: datetime,
    aligned_start: datetime,
    aligned_end: datetime,
    tz: ZoneInfo,
    *,
    limit: int = 25,
) -> tuple[dict[str, Any], ...]:
    """Revenue-status orders in the symmetric difference of the two windows.

    This is the tz explanation's evidence. Every row here is an order that one
    window contains and the other does not, with its ``created_at`` in both
    zones, so the claim "the timezone moved this" can be checked by eye against
    the order rows rather than taken on trust.
    """
    in_legacy = and_(Order.created_at >= legacy_start, Order.created_at < legacy_end)
    in_aligned = and_(Order.created_at >= aligned_start, Order.created_at < aligned_end)
    rows = db.execute(
        select(Order.id, Order.created_at, Order.total_amount, Order.status)
        .where(
            Order.status.in_(_REVENUE_STATUSES),
            or_(and_(in_legacy, ~in_aligned), and_(in_aligned, ~in_legacy)),
        )
        .order_by(Order.created_at)
        .limit(limit)
    ).all()

    out: list[dict[str, Any]] = []
    for order_id, created_at, total, status in rows:
        moment = (
            created_at
            if created_at.tzinfo
            else created_at.replace(tzinfo=timezone.utc)
        )
        out.append(
            {
                "order_id": int(order_id),
                "created_at_utc": moment.astimezone(timezone.utc).isoformat(),
                "created_at_local": moment.astimezone(tz).isoformat(),
                "total_amount": str(total),
                "status": status.value if hasattr(status, "value") else str(status),
                "only_in": (
                    "legacy_window"
                    if legacy_start <= moment < legacy_end
                    else "aligned_window"
                ),
            }
        )
    return tuple(out)


@dataclass(frozen=True)
class _Recognition:
    """The orders the new rule recognises that the legacy status filter drops."""

    orders: int
    total_minor: int
    discount_minor: int
    line_revenue_minor: int
    cogs_minor: int
    lines: int
    costed_lines: int


def _recognition_only(db: Session, start: datetime, end: datetime) -> _Recognition:
    """Measure the recognition difference directly from the order rows.

    ``_recognised_sale() AND NOT status IN _REVENUE_STATUSES`` is exactly the set
    of orders the two definitions disagree about: refunded orders for which a
    reversal can actually be dated. Everything this explanation attributes is a
    sum over these rows and nothing else.
    """
    only = and_(
        _recognised_sale(),
        ~Order.status.in_(_REVENUE_STATUSES),
        Order.created_at >= start,
        Order.created_at < end,
    )
    head = db.execute(
        select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.total_amount), 0),
            func.coalesce(
                func.sum(Order.discount_amount + Order.payment_discount_amount), 0
            ),
        ).where(only)
    ).one()
    lines = db.execute(
        select(
            func.coalesce(func.sum(OrderItem.quantity * OrderItem.unit_price), 0),
            func.coalesce(func.sum(OrderItem.quantity * OrderItem.unit_cost), 0),
            func.count(OrderItem.id),
            func.coalesce(
                func.sum(case((OrderItem.unit_cost.isnot(None), 1), else_=0)), 0
            ),
        )
        .select_from(OrderItem)
        .join(Order, Order.id == OrderItem.order_id)
        .where(only)
    ).one()
    return _Recognition(
        orders=int(head[0] or 0),
        total_minor=to_minor(head[1]),
        discount_minor=to_minor(head[2]),
        line_revenue_minor=to_minor(lines[0]),
        cogs_minor=to_minor(lines[1]),
        lines=int(lines[2] or 0),
        costed_lines=int(lines[3] or 0),
    )


def _payment_discount_minor(db: Session, start: datetime, end: datetime) -> int:
    """SUM(payment_discount_amount) over the LEGACY revenue set.

    The legacy set, not the recognised one: the recognition part of the discount
    delta is attributed separately by :func:`_recognition_only`, and attributing
    the same rows through two terms would over-explain.
    """
    return to_minor(
        db.execute(
            select(func.coalesce(func.sum(Order.payment_discount_amount), 0)).where(
                Order.status.in_(_REVENUE_STATUSES),
                Order.created_at >= start,
                Order.created_at < end,
            )
        ).scalar_one()
    )


def _category_revenue(
    db: Session, start: datetime, end: datetime, *, recognised: bool
) -> dict[str, int]:
    """``AnalyticsService._by_category``'s query under either revenue rule.

    The legacy rule reproduces the legacy page; the recognised rule isolates how
    much of a category's movement is recognition and how much is anything else.
    """
    predicate = _recognised_sale() if recognised else Order.status.in_(_REVENUE_STATUSES)
    rows = db.execute(
        select(
            Category.name,
            func.coalesce(func.sum(OrderItem.quantity * OrderItem.unit_price), 0).label(
                "revenue"
            ),
        )
        .select_from(OrderItem)
        .join(Order, Order.id == OrderItem.order_id)
        .join(Product, Product.id == OrderItem.product_id)
        .outerjoin(Category, Category.id == Product.category_id)
        .where(predicate, Order.created_at >= start, Order.created_at < end)
        .group_by(Category.name)
        .order_by(desc("revenue"))
    ).all()
    out: dict[str, int] = {}
    for name, revenue in rows:
        label = name or "Uncategorized"
        out[label] = out.get(label, 0) + to_minor(revenue)
    return out


def _category_snapshot_shift(
    db: Session, date_from: date, date_to: date, generation: int
) -> dict[str, int]:
    """Per-category revenue moved by a product being re-categorised since the sale.

    Signed in the direction ``new - legacy``: revenue leaves the product's
    *current* category (negative) and lands in its *snapshotted* one (positive).
    Exact, because both category ids are on the rollup row and the live product.
    """
    rows = db.execute(
        select(
            AggProductDaily.category_id_snapshot,
            Product.category_id,
            func.coalesce(func.sum(AggProductDaily.gross_merchandise_sales), 0),
        )
        .select_from(AggProductDaily)
        .join(Product, Product.id == AggProductDaily.product_id)
        .where(
            AggProductDaily.bucket_date >= date_from,
            AggProductDaily.bucket_date < date_to,
            AggProductDaily.tz_generation == generation,
            func.coalesce(AggProductDaily.category_id_snapshot, 0)
            != func.coalesce(Product.category_id, 0),
        )
        .group_by(AggProductDaily.category_id_snapshot, Product.category_id)
    ).all()
    if not rows:
        return {}
    names = _category_names(db, [r[0] for r in rows] + [r[1] for r in rows])
    shift: dict[str, int] = {}
    for snapshot_id, current_id, revenue in rows:
        minor = to_minor(revenue)
        to_label = (
            names.get(int(snapshot_id), "Uncategorized")
            if snapshot_id
            else "Uncategorized"
        )
        from_label = (
            names.get(int(current_id), "Uncategorized")
            if current_id
            else "Uncategorized"
        )
        shift[to_label] = shift.get(to_label, 0) + minor
        shift[from_label] = shift.get(from_label, 0) - minor
    return shift


# ===========================================================================
# compare()
# ===========================================================================


def compare(
    db: Session,
    date_from: date,
    date_to: date,
    *,
    alert: bool = True,
    worker_id: str = "shadow",
    log_run: bool = True,
) -> ShadowReport:
    """Compare the legacy pages and the new subsystem over ``[date_from, date_to)``.

    ``date_from`` / ``date_to`` are **store-local reporting dates**, half-open,
    matching ``timebox.range_bounds_utc`` and every other date range in this
    stack. Two UTC windows are derived from them:

    * the **legacy** window — the same dates read as UTC instants, which is what
      ``dashboard_service._period_bounds`` produces and therefore what the legacy
      pages actually show;
    * the **aligned** window — the same reporting days converted through the
      store timezone, which is what the new subsystem buckets on.

    Every unexplained difference raises an ``AnalyticsAlert`` unless ``alert`` is
    False. The run is logged to ``analytics_sync_runs`` unless ``log_run`` is
    False — a comparison that did not run has to be distinguishable from one that
    ran clean, and silence looks identical either way.

    The caller owns the transaction: the run row, the comparison and the alerts
    it raised are flushed but not committed, so they land together or not at all.
    (``timebox.active_generation`` commits if it has to seed generation 1 — once,
    on a database that has never aggregated anything.)
    """
    started = datetime.now(timezone.utc)
    generation = active_generation(db)
    tz = store_timezone(db)
    aligned_start, aligned_end = range_bounds_utc(date_from, date_to, tz)
    legacy_start = datetime(
        date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc
    )
    legacy_end = datetime(date_to.year, date_to.month, date_to.day, tzinfo=timezone.utc)
    days = max((date_to - date_from).days, 0)
    warnings: list[str] = []

    run: AnalyticsSyncRun | None = None
    if log_run:
        run = AnalyticsSyncRun(
            job=SHADOW_JOB_NAME,
            trigger=SyncTrigger.MANUAL,
            status=SyncStatus.RUNNING,
            worker_id=worker_id[:64],
            window_from=date_from,
            window_to=date_to,
            tz_generation=int(generation.generation),
            days_requested=days,
            days_processed=0,
            started_at=started.replace(tzinfo=None),
        )
        db.add(run)
        db.flush()

    # ---- Measure -----------------------------------------------------------
    legacy = _legacy_sales(db, legacy_start, legacy_end)
    aligned = _legacy_sales(db, aligned_start, aligned_end)
    legacy_profit = _legacy_profit(db, legacy_start, legacy_end)
    aligned_profit = _legacy_profit(db, aligned_start, aligned_end)

    rollups = _rollup_figures(db, date_from, date_to, int(generation.generation))
    service = MarginService(db)
    margin = service.compute(aligned_start, aligned_end)
    recognition = service.recognised_revenue(aligned_start, aligned_end)
    anchor_legacy = DashboardService(db)._revenue_summary(aligned_start, aligned_end)
    anchor_new = service.paid_order_value(aligned_start, aligned_end)

    only = _recognition_only(db, aligned_start, aligned_end)
    payment_discounts = _payment_discount_minor(db, aligned_start, aligned_end)

    if recognition.unvalued_refunds:
        warnings.append(
            f"{recognition.unvalued_refunds} refund(s) dated in this window carry no "
            "amount and cannot be valued; net_revenue is compared on the valued "
            "portion only and is understated by whatever they were worth"
        )
    warnings.extend(recognition.warnings)
    warnings.extend(margin.warnings)

    if rollups.missing_buckets:
        warnings.append(
            f"{len(rollups.missing_buckets)} of {days} reporting day(s) have no "
            f"agg_order_daily row under generation {generation.generation}: "
            + ", ".join(str(d) for d in rollups.missing_buckets[:5])
            + (" ..." if len(rollups.missing_buckets) > 5 else "")
            + ". Every rollup-sourced figure below is understated by whatever those "
            "days held; a rollup cannot report its own absence."
        )

    comparisons: list[MetricComparison] = []

    # ---- The parity anchor -------------------------------------------------
    # One window, one query on each side, zero tolerance, no explanation
    # permitted. If this moves, nothing else in the report means anything, so it
    # is compared first and on its own terms.
    comparisons.append(
        MetricComparison(
            metric=ANCHOR_METRIC,
            unit=_UNIT_MONEY,
            legacy=to_minor(anchor_legacy["revenue"]),
            legacy_aligned=to_minor(anchor_legacy["revenue"]),
            new=to_minor(anchor_new),
            permitted=frozenset(),
            tolerance_override=0,
            new_source="margin.MarginService.paid_order_value",
        )
    )

    # ---- Headline revenue, orders, AOV ------------------------------------
    comparisons.append(
        MetricComparison(
            metric="paid_order_value",
            unit=_UNIT_MONEY,
            legacy=legacy.revenue_minor,
            legacy_aligned=aligned.revenue_minor,
            new=rollups.paid_order_value_minor,
            permitted=_keys("tz_bucketing"),
            new_source="agg_order_daily.paid_order_value",
        )
    )
    comparisons.append(
        MetricComparison(
            metric="orders",
            unit=_UNIT_COUNT,
            legacy=legacy.orders,
            legacy_aligned=aligned.orders,
            new=rollups.orders,
            permitted=_keys("tz_bucketing"),
            new_source="agg_order_daily.orders_paid+shipped+delivered",
        )
    )
    comparisons.append(
        MetricComparison(
            metric="aov",
            unit=_UNIT_MONEY,
            legacy=legacy.aov_minor,
            legacy_aligned=aligned.aov_minor,
            new=(
                int(round(rollups.paid_order_value_minor / rollups.orders))
                if rollups.orders
                else 0
            ),
            permitted=_keys("tz_bucketing", "paisa_rounding"),
            terms=(
                BridgeTerm(
                    explanation="paisa_rounding",
                    bound=1,
                    note="AOV is a quotient: legacy divides in Decimal at 28 digits, "
                    "the rollup divides integer paise",
                ),
            ),
            new_source="agg_order_daily paid_order_value / orders",
        )
    )

    # ---- The other revenue definition, from both of its sources ------------
    net_revenue_terms = (
        BridgeTerm(
            explanation="revenue_recognition",
            amount=only.total_minor,
            note=f"{only.orders} refunded order(s) whose sale is recognised in this "
            "window and whose reversal is dated in its own",
        ),
        BridgeTerm(
            explanation="refund_timing",
            amount=-recognition.refunds_valued_minor,
            note="refunds dated in this window by their own refunded_at",
        ),
    )
    net_revenue_permitted = _keys(
        "tz_bucketing", "revenue_recognition", "refund_timing"
    )
    comparisons.append(
        MetricComparison(
            metric="net_revenue",
            unit=_UNIT_MONEY,
            legacy=legacy.revenue_minor,
            legacy_aligned=aligned.revenue_minor,
            new=(
                recognition.recognised_order_value_minor
                - recognition.refunds_valued_minor
            ),
            permitted=net_revenue_permitted,
            terms=net_revenue_terms,
            new_source="margin.MarginService.recognised_revenue",
        )
    )
    # The same definition as the dashboard will actually read it. A correction
    # that landed in the service but not in the table feeding the screen has not
    # landed, and only comparing both can tell the two apart.
    comparisons.append(
        MetricComparison(
            metric="net_revenue_rollup",
            unit=_UNIT_MONEY,
            legacy=legacy.revenue_minor,
            legacy_aligned=aligned.revenue_minor,
            new=rollups.net_revenue_minor,
            permitted=net_revenue_permitted,
            terms=net_revenue_terms,
            new_source="agg_order_daily.net_revenue",
        )
    )

    # ---- Discounts ---------------------------------------------------------
    comparisons.append(
        MetricComparison(
            metric="discounts",
            unit=_UNIT_MONEY,
            legacy=legacy.discounts_minor,
            legacy_aligned=aligned.discounts_minor,
            new=rollups.discounts_minor,
            permitted=_keys(
                "tz_bucketing", "revenue_recognition", "payment_discount_included"
            ),
            terms=(
                BridgeTerm(
                    explanation="payment_discount_included",
                    amount=payment_discounts,
                    note="SUM(orders.payment_discount_amount) over the legacy revenue "
                    "set",
                ),
                BridgeTerm(
                    explanation="revenue_recognition",
                    amount=only.discount_minor,
                    note="discounts on the orders only the recognised rule admits",
                ),
            ),
            new_source="agg_order_daily.discount_sum",
        )
    )

    # ---- Revenue by category ----------------------------------------------
    comparisons.extend(
        _category_comparisons(
            legacy=legacy.by_category_minor,
            aligned=aligned.by_category_minor,
            aligned_recognised=_category_revenue(
                db, aligned_start, aligned_end, recognised=True
            ),
            new=rollups.by_category_minor,
            snapshot_shift=_category_snapshot_shift(
                db, date_from, date_to, int(generation.generation)
            ),
        )
    )

    # ---- COGS and its coverage --------------------------------------------
    comparisons.append(
        MetricComparison(
            metric="cogs",
            unit=_UNIT_MONEY,
            legacy=legacy_profit.product_cost_minor,
            legacy_aligned=aligned_profit.product_cost_minor,
            new=margin.cogs_minor,
            permitted=_keys(
                "tz_bucketing", "revenue_recognition", "cogs_null_not_zeroed"
            ),
            terms=(
                BridgeTerm(
                    explanation="revenue_recognition",
                    amount=only.cogs_minor,
                    note="COGS of the orders only the recognised rule admits",
                ),
            ),
            new_source="margin.MarginService.compute().cogs_minor",
        )
    )
    comparisons.append(
        MetricComparison(
            metric="cost_coverage_pct",
            unit=_UNIT_PCT,
            legacy=legacy_profit.coverage_bp,
            legacy_aligned=aligned_profit.coverage_bp,
            new=int((margin.cost_coverage_pct * 100).quantize(Decimal("1"))),
            permitted=_keys(
                "tz_bucketing",
                "revenue_recognition",
                "cogs_null_not_zeroed",
                "paisa_rounding",
            ),
            terms=(
                BridgeTerm(
                    explanation="revenue_recognition",
                    amount=_coverage_shift_bp(aligned_profit, only),
                    note="the recognised-only lines move both sides of the ratio",
                ),
                BridgeTerm(
                    explanation="paisa_rounding",
                    bound=PCT_TOLERANCE_BP,
                    note="legacy rounds to 0.1pp; the new figure quantizes to 0.01pp",
                ),
            ),
            new_source="margin.MarginService.compute().cost_coverage_pct",
        )
    )

    # ---- The contribution-margin cascade ----------------------------------
    comparisons.extend(
        _cascade_comparisons(
            aligned_profit=aligned_profit,
            legacy_profit=legacy_profit,
            margin=margin,
            recognition_only=only,
            days=days,
        )
    )

    # ---- Classify ----------------------------------------------------------
    differences: list[Difference] = []
    for comparison in comparisons:
        differences.extend(classify(comparison))

    if rollups.missing_buckets:
        # Not a metric delta: an absence. Reported as UNEXPLAINED because a
        # rollup with no row is not a rollup that measured zero, and treating the
        # two the same is the exact mistake the whole subsystem is built against.
        differences.append(
            Difference(
                metric="rollup_coverage",
                kind=DifferenceKind.UNEXPLAINED,
                component="definitional",
                delta=len(rollups.missing_buckets),
                explained=0,
                bounded=0,
                residual=len(rollups.missing_buckets),
                unit=_UNIT_COUNT,
                note=(
                    f"{len(rollups.missing_buckets)} reporting day(s) have no "
                    "agg_order_daily row; the new figures cannot be compared against "
                    "the legacy ones until the aggregation has run for them"
                ),
            )
        )

    report = ShadowReport(
        date_from=date_from,
        date_to=date_to,
        generated_at=started,
        timezone_name=str(tz),
        tz_generation=int(generation.generation),
        legacy_window=(legacy_start, legacy_end),
        aligned_window=(aligned_start, aligned_end),
        comparisons=tuple(comparisons),
        differences=tuple(differences),
        missing_buckets=rollups.missing_buckets,
        boundary_orders=_boundary_orders(
            db, legacy_start, legacy_end, aligned_start, aligned_end, tz
        ),
        warnings=tuple(warnings),
    )

    alert_ids = _raise_alerts(db, report) if alert else ()

    sync_run_id: int | None = None
    if run is not None:
        finished = datetime.now(timezone.utc)
        run.status = SyncStatus.SUCCESS if report.is_clean else SyncStatus.PARTIAL
        run.days_processed = days
        run.rows_written = len(comparisons)
        run.watermark_date = date_to - timedelta(days=1) if days else None
        run.duration_ms = int((finished - started).total_seconds() * 1000)
        run.finished_at = finished.replace(tzinfo=None)
        if not report.is_clean:
            run.error = (
                f"{len(report.unexplained)} unexplained difference(s): "
                + "; ".join(d.describe() for d in report.unexplained[:3])
            )[:500]
        db.flush()
        sync_run_id = int(run.id)

    return dataclasses.replace(report, alert_ids=alert_ids, sync_run_id=sync_run_id)


def _coverage_shift_bp(aligned: _LegacyProfit, only: _Recognition) -> int:
    """How much of the coverage gap the recognition difference accounts for.

    Coverage is a ratio, so the recognised-only lines move the numerator and the
    denominator together and the effect is not a sum of anything. It is computed
    here by recomputing the ratio over the union of the two line sets — exact,
    from the same counts both services use.
    """
    if aligned.coverage_bp is None:
        return 0
    lines = aligned.lines + only.lines
    if lines == 0:
        return 0
    costed = aligned.costed_lines + only.costed_lines
    with_recognition_bp = int(
        (Decimal(costed) / Decimal(lines) * Decimal(10000)).quantize(Decimal("1"))
    )
    return with_recognition_bp - aligned.coverage_bp


def _category_comparisons(
    *,
    legacy: Mapping[str, int],
    aligned: Mapping[str, int],
    aligned_recognised: Mapping[str, int],
    new: Mapping[str, int],
    snapshot_shift: Mapping[str, int],
) -> list[MetricComparison]:
    """One comparison per category present on either side.

    A category that exists on only one side is compared against zero rather than
    skipped — a category appearing or vanishing between the two systems is
    precisely the kind of difference this job is for.
    """
    labels = sorted(set(legacy) | set(aligned) | set(new) | set(snapshot_shift))
    out: list[MetricComparison] = []
    for label in labels:
        aligned_value = aligned.get(label, 0)
        out.append(
            MetricComparison(
                metric="revenue_by_category",
                unit=_UNIT_MONEY,
                legacy=legacy.get(label, 0),
                legacy_aligned=aligned_value,
                new=new.get(label, 0),
                dimension="category",
                dimension_value=label,
                permitted=_keys(
                    "tz_bucketing", "revenue_recognition", "category_snapshot"
                ),
                terms=(
                    BridgeTerm(
                        explanation="revenue_recognition",
                        amount=aligned_recognised.get(label, 0) - aligned_value,
                        note="the same category query under the recognised rule",
                    ),
                    BridgeTerm(
                        explanation="category_snapshot",
                        amount=snapshot_shift.get(label, 0),
                        note="revenue whose product has been re-categorised since the "
                        "sale",
                    ),
                ),
                new_source=(
                    "agg_product_daily.gross_merchandise_sales by category_id_snapshot"
                ),
            )
        )
    return out


def _component_minor(margin: Any, cost_type: str) -> int | None:
    for component in margin.components:
        if component.cost_type == cost_type:
            return component.value_minor
    return None


def _cascade_comparisons(
    *,
    aligned_profit: _LegacyProfit,
    legacy_profit: _LegacyProfit,
    margin: Any,
    recognition_only: _Recognition,
    days: int,
) -> list[MetricComparison]:
    """CM1/CM2/CM3 against C1/C2/C3, as a term-by-term bridge.

    Each level is reconciled by naming every structural difference between the
    two formulas and measuring it. The residual after all of them is what the two
    cascades disagree about for no stated reason, and that is the number that has
    to be zero::

        legacy C1 = line_revenue - COGS - shipping_income - packing - handling
        new   CM1 = (line_revenue - discounts) - COGS

        legacy C2 = C1 - gateway_fees
        new   CM2 = CM1 + shipping_income - sum(CM2 cost rules)

        legacy C3 = C2 - marketing_discounts - ad_spend
        new   CM3 = CM2 - marketing_spend rule

    Levels are compared like for like: CM3 against C3, never against net_profit.
    ``overheads_excluded_from_cm3`` is in the register so a reader who does
    compare them knows why the gap is there.
    """
    shipping_income = _component_minor(margin, SHIPPING_INCOME) or 0
    recognition_cm1 = (
        recognition_only.line_revenue_minor
        - recognition_only.discount_minor
        - recognition_only.cogs_minor
    )

    cm1_terms = (
        BridgeTerm(
            explanation="shipping_income_not_a_cost",
            amount=aligned_profit.shipping_minor,
            note="legacy subtracts orders.shipping_amount here; the new cascade does "
            "not, and adds it as income at CM2",
        ),
        BridgeTerm(
            explanation="cost_rules_vs_settings",
            amount=aligned_profit.packing_minor + aligned_profit.handling_minor,
            note="legacy charges packing and handling at C1 from settings keys; the "
            "new cascade resolves them as CM2 cost rules",
        ),
        BridgeTerm(
            explanation="discount_in_nms",
            amount=-aligned_profit.marketing_discounts_minor,
            note="the new cascade starts from net merchandise sales; legacy subtracts "
            "discounts at C3",
        ),
        BridgeTerm(
            explanation="revenue_recognition",
            amount=recognition_cm1,
            note="orders only the recognised rule admits, at CM1 grain",
        ),
        BridgeTerm(
            explanation="paisa_rounding",
            bound=2,
            note="the legacy cascade is float arithmetic recovered to paise at the "
            "boundary",
        ),
    )
    cm1_permitted = _keys(
        "tz_bucketing",
        "revenue_recognition",
        "shipping_income_not_a_cost",
        "discount_in_nms",
        "cost_rules_vs_settings",
        "cogs_null_not_zeroed",
        "missing_cost_input",
        "paisa_rounding",
    )
    out = [
        MetricComparison(
            metric="cm1",
            unit=_UNIT_MONEY,
            legacy=legacy_profit.c1_minor,
            legacy_aligned=aligned_profit.c1_minor,
            new=margin.cm1_minor,
            permitted=cm1_permitted,
            terms=cm1_terms,
            missing_reason=_missing_reason(margin, "CM1"),
            new_source="margin.MarginService.compute().cm1_minor",
        )
    ]

    cm2_rules = {name: _component_minor(margin, name) for name in CM2_COST_TYPES}
    carrier_total = sum(
        int(cm2_rules.get(name) or 0)
        for name in (
            CostType.FORWARD_SHIPPING,
            CostType.RETURN_SHIPPING,
            CostType.RTO_LOGISTICS,
            CostType.MARKETPLACE_COMMISSION,
        )
    )
    gateway_rule = int(cm2_rules.get(CostType.GATEWAY_FEE) or 0)
    packaging_rule = int(cm2_rules.get(CostType.PACKAGING) or 0)
    handling_rule = int(cm2_rules.get(CostType.HANDLING) or 0)

    cm2_terms = cm1_terms + (
        BridgeTerm(
            explanation="shipping_income_not_a_cost",
            amount=shipping_income,
            note="orders.shipping_amount added as income inside CM2",
        ),
        BridgeTerm(
            explanation="gateway_fee_basis",
            amount=aligned_profit.gateway_minor - gateway_rule,
            note="legacy: gateway_fee_pct x prepaid total; new: a GATEWAY_FEE rule on "
            "(total_amount - cod_balance)",
        ),
        BridgeTerm(
            explanation="cost_rules_vs_settings",
            amount=-(packaging_rule + handling_rule),
            note="packaging and handling resolved as effective-dated rules at CM2",
        ),
        BridgeTerm(
            explanation="carrier_and_logistics_costs",
            amount=-carrier_total,
            note="forward shipping, return shipping, RTO logistics and marketplace "
            "commission — the legacy cascade has no term for any of them",
        ),
        BridgeTerm(
            explanation="paisa_rounding",
            bound=days * len(CM2_COST_TYPES),
            note="cost rules resolve and round once per day",
        ),
    )
    cm2_permitted = cm1_permitted | _keys(
        "gateway_fee_basis", "carrier_and_logistics_costs"
    )
    out.append(
        MetricComparison(
            metric="cm2",
            unit=_UNIT_MONEY,
            legacy=legacy_profit.c2_minor,
            legacy_aligned=aligned_profit.c2_minor,
            new=margin.cm2_minor,
            permitted=cm2_permitted,
            terms=cm2_terms,
            missing_reason=_missing_reason(margin, "CM2"),
            new_source="margin.MarginService.compute().cm2_minor",
        )
    )

    marketing_rule = sum(
        int(_component_minor(margin, name) or 0) for name in CM3_COST_TYPES
    )
    cm3_terms = cm2_terms + (
        BridgeTerm(
            explanation="discount_in_nms",
            amount=aligned_profit.marketing_discounts_minor,
            note="legacy subtracts discounts again at C3; the new cascade already "
            "removed them at CM1",
        ),
        BridgeTerm(
            explanation="cost_rules_vs_settings",
            amount=aligned_profit.ad_spend_minor - marketing_rule,
            note="legacy pro-rates costs.monthly_ad_spend across the window; the new "
            "cascade resolves a MARKETING_SPEND rule per day",
        ),
        BridgeTerm(
            explanation="paisa_rounding",
            bound=days * len(CM3_COST_TYPES),
            note="the marketing rule resolves and rounds once per day",
        ),
    )
    out.append(
        MetricComparison(
            metric="cm3",
            unit=_UNIT_MONEY,
            legacy=legacy_profit.c3_minor,
            legacy_aligned=aligned_profit.c3_minor,
            new=margin.cm3_minor,
            permitted=cm2_permitted | _keys("overheads_excluded_from_cm3"),
            terms=cm3_terms,
            missing_reason=_missing_reason(margin, "CM3"),
            new_source="margin.MarginService.compute().cm3_minor",
        )
    )
    return out


def _missing_reason(margin: Any, level: str) -> str:
    if not margin.missing_inputs:
        return ""
    return (
        f"{level} has no value: no cost rule covers "
        f"{', '.join(margin.missing_inputs)}. The new cascade blanks the level rather "
        "than assuming the cost is zero."
    )


# ===========================================================================
# Alerting
# ===========================================================================

#: ``AlertRuleKey.TRACKING_FAILURE`` rather than a new constant. That rule is for
#: "the numbers themselves are not trustworthy" — a pipeline that looks healthy
#: while the dashboard serves a figure that is quietly wrong — which is exactly
#: an unexplained shadow divergence. Inventing a rule_key here would create an
#: alert stream nobody has muted, acknowledged or routed anywhere, and
#: ``AlertRuleKey`` lives in a model file this change does not own. ``metric`` and
#: ``dimension_value`` carry the specificity instead.
_ALERT_RULE_KEY = AlertRuleKey.TRACKING_FAILURE
_ALERT_DIMENSION = "shadow_metric"


def _alert_value(value: int | None, unit: str) -> Decimal | None:
    """Store money in rupees and everything else as itself.

    ``analytics_alerts`` is read by humans triaging an alert, and "expected
    24,700,000, got 24,650,000" in paise is not the sentence anyone wants.
    """
    if value is None:
        return None
    return from_minor(value) if unit == _UNIT_MONEY else Decimal(value)


def _raise_alerts(db: Session, report: ShadowReport) -> tuple[int, ...]:
    """One OPEN alert per unexplained difference, de-duplicated per bucket.

    De-duplicated on (rule, metric, dimension value, bucket) so re-running the
    comparison over the same window does not create a second copy of a divergence
    somebody is already looking at. Re-running has to be free, or nobody re-runs.
    """
    ids: list[int] = []
    detected = report.generated_at.replace(tzinfo=None)
    for difference in report.unexplained:
        dimension_value = (
            f"{difference.metric}:{difference.dimension_value}"
            if difference.dimension_value != "-"
            else difference.metric
        )[:64]
        existing = (
            db.execute(
                select(AnalyticsAlert.id).where(
                    AnalyticsAlert.rule_key == _ALERT_RULE_KEY,
                    AnalyticsAlert.metric == difference.metric[:64],
                    AnalyticsAlert.dimension == _ALERT_DIMENSION,
                    AnalyticsAlert.dimension_value == dimension_value,
                    AnalyticsAlert.bucket_date == report.date_from,
                    AnalyticsAlert.status == AlertStatus.OPEN,
                )
            )
            .scalars()
            .first()
        )
        if existing is not None:
            ids.append(int(existing))
            continue

        comparison = report.comparison(difference.metric, difference.dimension_value)
        legacy_value = _alert_value(
            comparison.legacy if comparison else None, difference.unit
        )
        tolerance = (
            _alert_value(comparison.tolerance, difference.unit) or Decimal(0)
            if comparison
            else Decimal(0)
        )
        alert = AnalyticsAlert(
            rule_key=_ALERT_RULE_KEY,
            severity=(
                AlertSeverity.CRITICAL
                if difference.metric in GATE_METRICS
                or difference.metric == "rollup_coverage"
                else AlertSeverity.WARNING
            ),
            metric=difference.metric[:64],
            dimension=_ALERT_DIMENSION,
            dimension_value=dimension_value,
            expected_low=None if legacy_value is None else legacy_value - tolerance,
            expected_high=None if legacy_value is None else legacy_value + tolerance,
            actual_value=_alert_value(
                comparison.new if comparison else None, difference.unit
            ),
            bucket_date=report.date_from,
            detected_at=detected,
            status=AlertStatus.OPEN,
            context={
                "job": SHADOW_JOB_NAME,
                "window": [str(report.date_from), str(report.date_to)],
                "timezone": report.timezone_name,
                "tz_generation": report.tz_generation,
                "component": difference.component,
                "unit": difference.unit,
                "delta": difference.delta,
                "explained": difference.explained,
                "bounded": difference.bounded,
                "residual": difference.residual,
                "explanations": list(difference.explanations),
                "legacy_source": (
                    "dashboard_service / analytics_service / profit_service"
                ),
                "new_source": comparison.new_source if comparison else "",
                "detail": difference.describe(),
            },
        )
        db.add(alert)
        db.flush()
        ids.append(int(alert.id))
    return tuple(ids)


# ===========================================================================
# The gate
# ===========================================================================


def verify_register_is_documented(doc_path: Path | None = None) -> list[str]:
    """Check every registered explanation has written prose behind it.

    "Documented" has to mean a human wrote something another human can read. Each
    explanation must appear in ``RECONCILIATION.md`` under a heading that starts
    ``### <key>``, and its register entry must actually say which side is correct,
    why, and how to check. Returns the problems; empty means clean.
    """
    path = doc_path or RECONCILIATION_DOC
    problems: list[str] = []
    if not path.exists():
        return [
            f"the known-differences register {path} does not exist, so no difference "
            "can be called documented"
        ]
    text = path.read_text(encoding="utf-8")
    documented = {
        line.strip()[4:].strip().split()[0]
        for line in text.splitlines()
        if line.strip().startswith("### ") and line.strip()[4:].strip()
    }
    for key, entry in sorted(EXPECTED_DIFFERENCE_REGISTER.items()):
        if key not in documented:
            problems.append(
                f"explanation '{key}' has no '### {key}' section in {path.name}"
            )
        if entry.correct_side not in ("new", "legacy", "neither"):
            problems.append(f"explanation '{key}' does not say which side is correct")
        if not entry.why_correct.strip():
            problems.append(f"explanation '{key}' does not say why")
        if not entry.how_to_verify.strip():
            problems.append(f"explanation '{key}' does not say how to verify it")
    return problems


def is_ready_to_retire_legacy(
    report_history: Sequence[ShadowReport],
    *,
    db: Session | None = None,
    doc_path: Path | None = None,
) -> tuple[bool, list[str]]:
    """May the legacy Sales and Profit pages be switched off yet?

    Three conditions, all necessary:

    1. revenue, orders and AOV within tolerance across
       :data:`CONSECUTIVE_DAYS_REQUIRED` **consecutive** reporting days;
    2. every difference observed in that history carries a written explanation in
       ``docs/analytics/RECONCILIATION.md``;
    3. no unexplained difference is open — in the reports, and, when ``db`` is
       given, no open shadow alert in ``analytics_alerts`` either.

    Returns ``(ready, reasons)``. ``reasons`` is why it is **not** ready, one
    specific, actionable line at a time: which metric, on which day, by how much,
    or how many days of evidence are still missing. "Not ready" without a reason
    is unactionable, so an empty reason list is only ever returned with ``True``.
    """
    reasons: list[str] = []

    if not report_history:
        return False, [
            f"no shadow comparison has run yet: 0 of {CONSECUTIVE_DAYS_REQUIRED} "
            f"consecutive days of evidence, {CONSECUTIVE_DAYS_REQUIRED} still required"
        ]

    # ---- Condition 2: the register is actually written down ---------------
    reasons.extend(verify_register_is_documented(doc_path))

    # ---- Map every covered reporting day to its verdict --------------------
    verdicts: dict[date, list[str]] = {}
    for report in sorted(report_history, key=lambda r: (r.date_from, r.date_to)):
        _, day_reasons = report.gate_metrics_match()
        for difference in report.unexplained:
            day_reasons.append(
                f"{report.date_from}: unexplained — {difference.describe()}"
            )
        for day in report.days or (report.date_from,):
            verdicts.setdefault(day, []).extend(day_reasons)

    latest = max(verdicts)
    required = [
        latest - timedelta(days=n) for n in range(CONSECUTIVE_DAYS_REQUIRED - 1, -1, -1)
    ]
    missing = [day for day in required if day not in verdicts]
    if missing:
        reasons.append(
            f"only {CONSECUTIVE_DAYS_REQUIRED - len(missing)} of "
            f"{CONSECUTIVE_DAYS_REQUIRED} consecutive days ending {latest} are covered "
            f"by a shadow report; {len(missing)} more day(s) of clean comparison are "
            f"required (first missing {missing[0]}, last {missing[-1]})"
        )

    # ---- Conditions 1 and 3 over the qualifying window --------------------
    seen: set[str] = set()
    for day in required:
        for reason in verdicts.get(day, []):
            if reason not in seen:
                seen.add(reason)
                reasons.append(reason)

    # An unexplained difference that is still OPEN in the alerts table blocks the
    # gate whenever it was found. A divergence nobody closed out is not evidence
    # of equivalence just because it has scrolled off the 30-day window.
    if db is not None:
        for alert_id, metric, dimension_value, bucket in db.execute(
            select(
                AnalyticsAlert.id,
                AnalyticsAlert.metric,
                AnalyticsAlert.dimension_value,
                AnalyticsAlert.bucket_date,
            ).where(
                AnalyticsAlert.rule_key == _ALERT_RULE_KEY,
                AnalyticsAlert.dimension == _ALERT_DIMENSION,
                AnalyticsAlert.status == AlertStatus.OPEN,
            )
        ).all():
            reasons.append(
                f"shadow alert #{int(alert_id)} is still open: {metric} "
                f"({dimension_value}) on {bucket} — acknowledge it, resolve it, or fix "
                "the divergence"
            )

    return (not reasons), reasons
