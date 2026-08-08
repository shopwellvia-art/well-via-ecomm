"""Anomaly detection and the alerts engine.

Why this file is mostly about *not* firing
------------------------------------------
An alerting system has exactly one failure mode that matters, and it is not
"missed an incident". It is **being muted**. A rule that fires on noise trains
its reader to dismiss the channel, and a dismissed channel detects nothing at
all, forever — which is strictly worse than never having built it, because the
store now believes it is being watched. Every design decision below is bought
with that in mind:

* **A rule with no baseline does not fire, and says why.** Three days of history
  make "sales dropped 40%" arithmetic, not evidence. Each rule declares a
  minimum baseline (:data:`RULES`), and below it the rule is *skipped* with a
  machine-readable reason on :class:`DetectionRun.skips` — never downgraded to a
  quieter alert, because a quiet wrong alert is still a wrong alert.

* **The expected range comes from the forecast engine, not a percentage.**
  ``forecasting.fit()`` + ``forecasting.forecast()`` already produce an OLS
  prediction interval that widens with noise, with sparse history and with a
  day-of-week factor. A store whose Saturdays run 1.3x its average has that in
  the *band*, so Saturday is not an anomaly. A hardcoded "alert if 30% below
  last week" would page that store every weekend, and after three weekends
  nobody would read it. This module therefore contains **no second statistical
  method** — see :func:`forecast_range`.

* **A missing input is not an anomaly.** No cost rules configured raises
  :data:`AlertRuleKey.MISSING_COST_DATA` — a configuration alert — and can never
  raise ``NEGATIVE_CM3``. ``NEGATIVE_CM3`` asserts a business fact ("you lost
  money on every order today"); asserting it from an absent number is the same
  class of error as costing an uncosted line at zero, and points the same
  flattering-or-alarming direction depending only on which number went missing.
  :class:`MarginService` already refuses to compute CM3 when an input is absent
  (``cm3_minor is None``), so this rule is a straight read of that refusal.

* **The same rule on the same bucket updates one row.** See
  :meth:`AnomalyDetector._upsert`. An alert list that repeats itself is
  unreadable within a week, and "unreadable" is the same outcome as muted.

* **Severity is graded, never fixed.** :func:`severity_for` grades on how far
  outside the range the value landed, measured in half-widths of the range
  itself. Two stores with different volatility get different bands and therefore
  the same *meaning* out of the same severity word.

Why the confidence level is 0.95 and why INFO exists
-----------------------------------------------------
:data:`ALERT_CONFIDENCE` is 0.95, not the forecast module's 0.80. An 80% band is
right for a chart — "most days land in here" — and wrong for an alert, because
one day in five landing outside it means a rule that fires every week on nothing.
Even at 95%, roughly one day in twenty sits outside a band by chance. That
residue is what :data:`AlertSeverity.INFO` is for: a value just past the edge
grades INFO ("worth knowing, no action implied") and only a value well past it
reaches WARNING or CRITICAL. The noise does not disappear — it is *ranked*, which
is the only honest thing to do with it, since the detector genuinely cannot tell
a 1-in-20 day from a small real break.

What the rules read
-------------------
Everything band-shaped reads the rollups in ``analytics_rollups.py``, one
reporting day per point, under a single ``tz_generation`` — generations are never
mixed, for the reason ``AnalyticsTzGeneration`` gives. Column combination goes
through :mod:`metric_kind`, so a LEVEL (``stock_close``) is never summed across
days and a RATIO is always recomputed from its two stored parts rather than
averaged.

The two cost rules read :class:`MarginService`, which owns the CM1/CM2/CM3
cascade. This module does not restate the cascade: a second definition of CM3
that drifted from the first would make the alert and the dashboard disagree about
whether the store made money, and there would be no way to tell which was right.

The honest gaps
---------------
* ``MARKETING_COST_SPIKE`` watches marketing cost per rupee of revenue. With no
  ad-platform connection the spend input is a single blended ``MARKETING_SPEND``
  cost rule, pro-rated straight-line across the month — so this rule can see the
  *ratio* moving but can never attribute it to a campaign, and a step at a rule
  change or a month boundary is a real step in the input, not a market event.
  The alert's context says so.
* ``CONVERSION_DROP`` divides by ``agg_funnel_daily.distinct_sessions``, which is
  sessions that reached a tracked step — not site traffic (FUNNEL_STARTS_AT_CART).
  It is a checkout-health signal, not a marketing one, and it is skipped outright
  when the funnel rollup is not being written.
* ``MISSING_COST_DATA`` and ``NEGATIVE_CM3`` are gated on the store having
  actually traded on the bucket. Marketing spend accrues on a dead day too, so
  ungated they would fire 365 times a year at a store that has not launched —
  which is the muting failure again. The margin engine still grades those days
  INCOMPLETE; the alert channel is not the only place that is said.
* Those same two rules read :class:`MarginService`, which windows on
  ``orders.created_at`` rather than on a ``tz_generation``-cut reporting day.
  Under a non-UTC reporting calendar the margin window and the rollup bucket
  therefore disagree at the seam by a few hours of orders. That is inherited
  from the margin engine and is not papered over here: restating the cascade
  against the rollup would create a second definition of CM3, and two engines
  disagreeing about whether the store made money is a worse failure than a
  boundary that is off by one evening. The gate above (``orders_total > 0``
  from the rollup) is the one place the two are mixed, and it is used only to
  decide whether to ask the question, never to answer it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping, Optional

import redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.analytics_base import DIMENSION_UNKNOWN
from app.models.analytics_control import (
    AlertRuleKey,
    AlertSeverity,
    AlertStatus,
    AnalyticsAlert,
    AnalyticsEventOutbox,
    AnalyticsSyncRun,
    CostType,
    OutboxStatus,
    SyncStatus,
)
from app.models.analytics_rollups import (
    AggFunnelDaily,
    AggInventoryDaily,
    AggOrderDaily,
    AggPaymentDaily,
    AggProductDaily,
    AggShipmentDaily,
)
from app.services.analytics import forecasting
from app.services.analytics.contracts import from_minor
from app.services.analytics.margin import CM2_COST_TYPES, CM3_COST_TYPES, MarginService
from app.services.analytics.metric_kind import MetricKind, assert_summable, classify

logger = logging.getLogger(__name__)

__all__ = [
    "ALERT_CONFIDENCE",
    "BASELINE_WINDOW_DAYS",
    "MAX_BASELINE_GAP_DAYS",
    "RULES",
    "RuleSpec",
    "ExpectedRange",
    "RuleSkip",
    "RuleClear",
    "DetectionRun",
    "AnomalyDetector",
    "AlertTransitionError",
    "SkipReason",
    "RangeBasis",
    "detect",
    "run",
    "forecast_range",
    "deviation_ratio",
    "severity_for",
    "days_of_cover",
    "acknowledge",
    "resolve",
    "mute",
]


# ===========================================================================
# Constants
# ===========================================================================

#: Two-sided interval used for every band rule. 0.95 rather than the forecast
#: module's charting default of 0.80 — see the module docstring. Must be one of
#: ``forecasting.SUPPORTED_CONFIDENCE``; an unsupported level raises there rather
#: than being silently rounded to the nearest tabulated one.
ALERT_CONFIDENCE = 0.95

#: How far back a baseline reaches. Four months is long enough for the weekly
#: shape to be established several times over and short enough that a trend
#: fitted through it still describes the business the store is running now. Days
#: with no rollup row inside the window are absent, not zero — ``forecasting``
#: inflates the interval by ``1/sqrt(coverage)`` for exactly that.
BASELINE_WINDOW_DAYS = 120

#: The baseline must reach to within this many days of the bucket being judged.
#: A baseline that stops a fortnight ago is not a baseline for today: whatever
#: changed in the gap is invisible to it, and the interval would be narrow and
#: confident about a world that has moved.
MAX_BASELINE_GAP_DAYS = 7

#: Grading thresholds, in half-widths of the expected range past its edge.
#: ``0.4`` of a half-width outside a 95% band is comfortably inside the noise a
#: 95% band still admits; three half-widths outside is not.
SEVERITY_INFO_MAX = Decimal("0.5")
SEVERITY_WARNING_MAX = Decimal("1.5")

#: Worst last. Used to apply a rule's severity floor without letting the floor
#: cap an escalation.
SEVERITY_ORDER: tuple[str, ...] = (
    AlertSeverity.INFO,
    AlertSeverity.WARNING,
    AlertSeverity.CRITICAL,
)

#: Rounding for everything stored in `analytics_alerts` — the columns are
#: Numeric(18,4) and carry both currency totals and conversion rates.
_Q4 = Decimal("0.0001")

#: Aggregation job whose sync-run log is watched for a frozen watermark. The
#: order rollup is the spine every other rule reads through.
WATCHED_JOB = "order_daily"

#: Runs that count toward the watermark check. ``SKIPPED_LOCKED`` is excluded —
#: another worker held the lock and this tick did nothing, which is not an
#: outage and must not look like one.
_WATERMARK_STATUSES: tuple[str, ...] = (SyncStatus.SUCCESS, SyncStatus.PARTIAL)

#: Days of stock cover below which a selling product is at risk. Chosen as a
#: replenishment lead time, not as a stock level: "5 units left" means nothing
#: without the rate they leave at.
STOCK_COVER_DAYS = 7
#: Window the sales velocity behind the cover figure is measured over.
VELOCITY_WINDOW_DAYS = 14
#: Below this daily velocity a product is not selling, and "it will run out in
#: 3 days" is arithmetic on a rounding error. A slow mover going to zero is a
#: catalogue decision, not an incident.
MIN_VELOCITY_UNITS_PER_DAY = Decimal("0.5")
#: Cap on out-of-stock alerts per bucket. A hundred rows is a report, not an
#: alert list, and the reader stops at the tenth either way. The cap is recorded
#: in each alert's context so the truncation is never silent.
MAX_STOCK_ALERTS = 20

#: Outbox failure share above which GA4 attribution is degrading now.
GA4_MAX_FAILURE_PCT = Decimal("5")


class SkipReason:
    """Why a rule produced nothing. Strings, so a new reason needs no migration.

    These are the actual product of this module on most days: on a healthy store
    every rule reports one of these, and an operator asking "is the detector
    working?" needs to see the difference between "evaluated, clean" and "never
    ran because the funnel rollup has never been built".
    """

    #: The rule's source rollup holds no rows at all for this generation.
    NO_SOURCE_ROWS = "no_source_rows"
    #: Source rows exist, but none for the bucket being judged. The bucket was
    #: not measured — which is not the same as measuring zero.
    BUCKET_NOT_MEASURED = "bucket_not_measured"
    #: Fewer observed days than the rule's declared minimum baseline.
    INSUFFICIENT_BASELINE = "insufficient_baseline"
    #: The baseline stops more than MAX_BASELINE_GAP_DAYS before the bucket.
    BASELINE_GAP_TOO_WIDE = "baseline_gap_too_wide"
    #: The bucket's own denominator is too small to make a rate mean anything.
    INSUFFICIENT_BUCKET_SAMPLE = "insufficient_bucket_sample"
    #: The baseline has no scale — every observed day is zero — so there is
    #: nothing to measure a deviation against.
    DEGENERATE_BASELINE = "degenerate_baseline"
    #: An input this rule needs is not configured or not connected.
    INPUT_NOT_CONFIGURED = "input_not_configured"
    #: The store did not trade on this bucket, so the rule has no subject.
    NO_TRADING_ACTIVITY = "no_trading_activity"


class RangeBasis:
    """Where an expected range came from. Carried onto every alert."""

    #: `forecasting.forecast()` prediction interval for this day.
    FORECAST_INTERVAL = "forecast_interval"
    #: A stated policy threshold (stock cover days, acceptable failure share).
    #: Not a statistical claim, and labelled so a reader does not read it as one.
    POLICY_THRESHOLD = "policy_threshold"
    #: A definitional boundary — CM3 below zero, a missing input count above
    #: zero. Nothing was estimated at all.
    DEFINITIONAL = "definitional"


# ===========================================================================
# Rule specifications
# ===========================================================================


@dataclass(frozen=True)
class RuleSpec:
    """One detection rule and the evidence it refuses to work without.

    ``min_baseline_days`` is the number of **observed** days required before the
    rule may fire at all, and ``rationale`` is why that number and not a smaller
    one. Both are on the object rather than buried in the evaluator so an
    operator asking "why did this not fire?" gets the answer from the same place
    the code got it.
    """

    rule_key: str
    metric: str
    #: "below" fires under `expected_low`, "above" fires over `expected_high`.
    direction: str
    min_baseline_days: int
    rationale: str
    #: Minimum denominator on the judged bucket for a rate rule. 0 for flows.
    min_bucket_sample: int = 0
    sample_label: str = ""
    #: Never grade below this. Only NEGATIVE_CM3 sets it above INFO; see RULES.
    severity_floor: str = AlertSeverity.INFO


#: The four-complete-weeks minimum the forecast engine itself enforces. Every
#: band rule inherits it rather than picking its own smaller number, because the
#: thing that keeps a store's volatile Saturday out of the alert list is the
#: day-of-week factor, and that factor needs four observations of every weekday
#: before ``forecasting`` will apply it at all. A 14-day minimum would produce a
#: band with ``dow_applied=False`` — a band that does not know Saturdays exist,
#: and therefore flags every one of them.
_BAND_MIN_DAYS = forecasting.MIN_HISTORY_DAYS  # 28

RULES: dict[str, RuleSpec] = {
    AlertRuleKey.SALES_DROP: RuleSpec(
        rule_key=AlertRuleKey.SALES_DROP,
        metric="net_revenue",
        direction="below",
        min_baseline_days=_BAND_MIN_DAYS,
        rationale=(
            "28 observed days = four complete weeks, the shortest history in "
            "which the forecast engine will apply a day-of-week factor. Below "
            "it every Saturday and every Monday looks like a sales drop."
        ),
    ),
    AlertRuleKey.REVENUE_SPIKE: RuleSpec(
        rule_key=AlertRuleKey.REVENUE_SPIKE,
        metric="net_revenue",
        direction="above",
        min_baseline_days=_BAND_MIN_DAYS,
        rationale=(
            "Same band as SALES_DROP, read from the other side: one fit, two "
            "rules, so the two can never disagree about what a normal day is."
        ),
    ),
    AlertRuleKey.CONVERSION_DROP: RuleSpec(
        rule_key=AlertRuleKey.CONVERSION_DROP,
        metric="conversion_rate",
        direction="below",
        min_baseline_days=_BAND_MIN_DAYS,
        min_bucket_sample=50,
        sample_label="tracked sessions",
        rationale=(
            "28 days of baseline, and 50 tracked sessions on the day itself. A "
            "conversion rate over 9 sessions moves 11 points when one person "
            "buys; alerting on that is alerting on a coin flip."
        ),
    ),
    AlertRuleKey.PAYMENT_FAILURE_SPIKE: RuleSpec(
        rule_key=AlertRuleKey.PAYMENT_FAILURE_SPIKE,
        metric="payment_failure_rate",
        direction="above",
        min_baseline_days=_BAND_MIN_DAYS,
        min_bucket_sample=20,
        sample_label="payment attempts",
        rationale=(
            "28 days of baseline, and 20 attempts on the day. Two failures out "
            "of three attempts is a 67% failure rate and means nothing; the "
            "gateway outage this rule exists for shows up over dozens."
        ),
    ),
    AlertRuleKey.REFUND_SPIKE: RuleSpec(
        rule_key=AlertRuleKey.REFUND_SPIKE,
        metric="refund_sum",
        direction="above",
        min_baseline_days=_BAND_MIN_DAYS,
        rationale=(
            "28 days. Refunds are lumpy — most days are zero and one is a "
            "batch — so the band has to be fitted over enough days for the "
            "lumpiness to be inside sigma rather than inside the alert."
        ),
    ),
    AlertRuleKey.RETURN_SPIKE: RuleSpec(
        rule_key=AlertRuleKey.RETURN_SPIKE,
        metric="return_rate",
        direction="above",
        min_baseline_days=_BAND_MIN_DAYS,
        min_bucket_sample=20,
        sample_label="units sold",
        rationale=(
            "28 days of baseline, and 20 units on the day. A return rate over "
            "4 units is 0%, 25%, 50% or 75% and nothing in between."
        ),
    ),
    AlertRuleKey.RTO_SPIKE: RuleSpec(
        rule_key=AlertRuleKey.RTO_SPIKE,
        metric="rto_rate",
        direction="above",
        min_baseline_days=_BAND_MIN_DAYS,
        min_bucket_sample=10,
        sample_label="shipments",
        rationale=(
            "28 days of baseline, and 10 shipments on the day. RTO is the "
            "slowest-moving of these signals — a courier going bad takes days "
            "to show — so a thin day is never worth waking anyone for."
        ),
    ),
    AlertRuleKey.MARKETING_COST_SPIKE: RuleSpec(
        rule_key=AlertRuleKey.MARKETING_COST_SPIKE,
        metric="marketing_cost_ratio_pct",
        direction="above",
        min_baseline_days=_BAND_MIN_DAYS,
        rationale=(
            "28 days. The metric is marketing spend per 100 of net revenue — "
            "'rising faster than the revenue it buys'. Spend itself comes from "
            "a blended monthly cost rule, so the band must be wide enough to "
            "absorb the straight-line pro-rata step at a month boundary."
        ),
    ),
    AlertRuleKey.OUT_OF_STOCK_RISK: RuleSpec(
        rule_key=AlertRuleKey.OUT_OF_STOCK_RISK,
        metric="stock_cover_days",
        direction="below",
        min_baseline_days=VELOCITY_WINDOW_DAYS,
        rationale=(
            "14 days of inventory ledger for the product. Velocity over three "
            "days of a weekly-seasonal product is whichever three days those "
            "were. This is a policy threshold, not a forecast band: the "
            "question is 'will it last the lead time', which needs a lead "
            "time, not an interval."
        ),
    ),
    AlertRuleKey.NEGATIVE_CM3: RuleSpec(
        rule_key=AlertRuleKey.NEGATIVE_CM3,
        metric="contribution_margin_3",
        direction="below",
        min_baseline_days=0,
        rationale=(
            "No baseline: CM3 below zero is a definitional fact about the day, "
            "not a deviation from a normal one. The precondition is instead "
            "COMPLETENESS — every cost input resolved. MarginService returns "
            "cm3=None when any is missing, and this rule never fires on None."
        ),
        severity_floor=AlertSeverity.CRITICAL,
    ),
    AlertRuleKey.MISSING_COST_DATA: RuleSpec(
        rule_key=AlertRuleKey.MISSING_COST_DATA,
        metric="missing_cost_inputs",
        direction="above",
        min_baseline_days=0,
        rationale=(
            "No baseline: an absent cost rule is a configuration fact, visible "
            "on the first bucket. Gated only on the store having traded, so an "
            "unlaunched store is not told daily that it has no cost rules."
        ),
    ),
    AlertRuleKey.TRACKING_FAILURE: RuleSpec(
        rule_key=AlertRuleKey.TRACKING_FAILURE,
        metric="pipeline_lag_days",
        direction="above",
        min_baseline_days=7,
        rationale=(
            "7 prior days of order rollup, or 3 completed sync runs. Below "
            "that, 'the numbers stopped arriving' is indistinguishable from "
            "'the pipeline has not started yet', and the second is not an "
            "incident."
        ),
    ),
    AlertRuleKey.GA4_SYNC_FAILURE: RuleSpec(
        rule_key=AlertRuleKey.GA4_SYNC_FAILURE,
        metric="outbox_failure_pct",
        direction="above",
        min_baseline_days=0,
        min_bucket_sample=10,
        sample_label="outbox events",
        rationale=(
            "No baseline — a delivery failure is a fact, not a deviation — but "
            "10 events on the bucket before a percentage is quoted. One failed "
            "row out of two is a 50% failure rate and a coincidence."
        ),
    ),
}


# ===========================================================================
# Result shapes
# ===========================================================================


@dataclass(frozen=True)
class ExpectedRange:
    """The band a value was judged against, and what produced it.

    ``half_width`` is the scale deviations are measured in, and it is a separate
    field rather than ``(high - low) / 2`` because a one-sided range (CM3 below
    zero, stock cover below a lead time) has no width of its own and still needs
    a scale. Without one, "how far outside" has no units and severity could only
    ever be fixed per rule.
    """

    low: Decimal | None
    high: Decimal | None
    half_width: Decimal
    basis: str
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.half_width <= 0:
            raise ValueError(
                "half_width must be > 0: it is the scale a deviation is "
                "measured in, and a zero scale makes every value infinitely "
                "anomalous. A degenerate baseline must be skipped, not graded."
            )


@dataclass(frozen=True)
class RuleSkip:
    """A rule that could not be evaluated, and the reason in full.

    This is not a log line. It is the answer to "why is the alert list empty?",
    which is the question that decides whether a quiet dashboard means a healthy
    store or a detector that has never once run.
    """

    rule_key: str
    metric: str
    reason: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)
    dimension: str = DIMENSION_UNKNOWN
    dimension_value: str = DIMENSION_UNKNOWN


@dataclass(frozen=True)
class RuleClear:
    """A rule that WAS evaluated and found the value inside its expected range.

    Distinct from :class:`RuleSkip` on purpose: "checked, normal" and "could not
    check" are the two states an alerting system exists to tell apart, and they
    render identically as an absent alert.
    """

    rule_key: str
    metric: str
    actual: Decimal
    expected: ExpectedRange
    dimension: str = DIMENSION_UNKNOWN
    dimension_value: str = DIMENSION_UNKNOWN


@dataclass
class DetectionRun:
    """Everything one bucket's evaluation produced — including the silence."""

    bucket_date: date
    tz_generation: int
    detected_at: datetime
    alerts: list[AnalyticsAlert] = field(default_factory=list)
    skips: list[RuleSkip] = field(default_factory=list)
    clear: list[RuleClear] = field(default_factory=list)

    def skip_for(self, rule_key: str) -> RuleSkip | None:
        for skip in self.skips:
            if skip.rule_key == rule_key:
                return skip
        return None

    def clear_for(self, rule_key: str) -> RuleClear | None:
        for item in self.clear:
            if item.rule_key == rule_key:
                return item
        return None

    def alerts_for(self, rule_key: str) -> list[AnalyticsAlert]:
        return [a for a in self.alerts if a.rule_key == rule_key]

    def fired(self, rule_key: str) -> bool:
        return any(a.rule_key == rule_key for a in self.alerts)


class AlertTransitionError(ValueError):
    """An illegal alert lifecycle move was attempted.

    Raised rather than ignored: silently refusing to acknowledge an alert leaves
    an operator believing they own something they do not, and silently allowing
    a resolved alert back to `open` erases the record that it was handled.
    """


# ===========================================================================
# The maths — pure, no session, no clock
# ===========================================================================


def _dec(value: Any) -> Decimal:
    """Anything numeric -> the Numeric(18,4) the alert columns hold.

    Via ``str`` for floats so a binary artefact of the forecast arithmetic does
    not arrive as ``1234.5600000000001`` in a column a human reads.
    """
    if isinstance(value, Decimal):
        return value.quantize(_Q4)
    return Decimal(str(round(float(value), 4))).quantize(_Q4)


def forecast_range(
    history: Mapping[date, float],
    target_day: date,
    *,
    min_days: int,
    confidence: float = ALERT_CONFIDENCE,
    non_negative: bool = True,
    max_gap_days: int = MAX_BASELINE_GAP_DAYS,
) -> ExpectedRange | tuple[str, str, dict[str, Any]]:
    """The band ``target_day`` was expected to land in, or why there is none.

    This is the *only* place an expected range for a business metric is produced,
    and it produces it by asking :mod:`forecasting` for a one-step-ahead
    prediction interval. There is deliberately no fallback path — no "if we
    cannot fit a model, compare against last week's average" — because a
    fallback is a second statistical method with different failure modes, firing
    under the same rule key, and nothing in the alert would say which one ran.

    ``history`` must **exclude** ``target_day``. A day that contributed to its
    own baseline widens the band by exactly the amount it deviates, which is the
    one direction that hides the thing being looked for.

    Returns an :class:`ExpectedRange`, or ``(reason, message, detail)`` for the
    caller to record as a :class:`RuleSkip`.
    """
    if target_day in history:
        raise ValueError(
            f"{target_day.isoformat()} is inside its own baseline. A bucket that "
            "contributes to the band it is judged against widens that band by "
            "its own deviation, which suppresses precisely the anomaly being "
            "looked for."
        )

    if not history:
        return (
            SkipReason.NO_SOURCE_ROWS,
            "No history at all in the baseline window, so there is no expected "
            "range and nothing to deviate from. This is 'not measured', not "
            "'measured as normal'.",
            {"observed_days": 0, "required_days": min_days},
        )

    model = forecasting.fit(
        history,
        min_days=min_days,
        confidence=confidence,
        non_negative=non_negative,
    )
    if not model.ok:
        return (
            SkipReason.INSUFFICIENT_BASELINE,
            model.message,
            {**model.as_detail(), "rule_min_days": min_days},
        )

    gap = (target_day - model.last_day).days
    if gap < 1:
        raise ValueError(
            f"baseline ends {model.last_day.isoformat()}, on or after the bucket "
            f"{target_day.isoformat()} being judged"
        )
    if gap > max_gap_days or gap > model.max_horizon_days:
        return (
            SkipReason.BASELINE_GAP_TOO_WIDE,
            f"The baseline stops at {model.last_day.isoformat()}, {gap} days "
            f"before {target_day.isoformat()} (limit {max_gap_days}). Whatever "
            "changed in that gap is invisible to the fit, so its interval would "
            "be narrow and confident about a business that has moved on.",
            {
                "baseline_last_day": model.last_day.isoformat(),
                "gap_days": gap,
                "max_gap_days": max_gap_days,
                "model_max_horizon_days": model.max_horizon_days,
            },
        )

    point = forecasting.forecast(model, gap)[-1]
    half = _dec((point.upper - point.lower) / 2)
    if half <= 0:
        return (
            SkipReason.DEGENERATE_BASELINE,
            "Every observed day in the baseline is zero, so the expected range "
            "has no width and there is no scale on which to call anything "
            "far outside it. A rule that fired here would grade every non-zero "
            "value identically CRITICAL.",
            {
                "observed_days": model.observed_days,
                "baseline_from": model.first_day.isoformat(),
                "baseline_to": model.last_day.isoformat(),
            },
        )

    return ExpectedRange(
        low=_dec(point.lower),
        high=_dec(point.upper),
        half_width=half,
        basis=RangeBasis.FORECAST_INTERVAL,
        detail={
            "point": str(_dec(point.point)),
            "confidence": confidence,
            "method_id": model.method_id,
            "method": model.method_name,
            "method_summary": model.method_summary,
            "baseline_from": model.first_day.isoformat(),
            "baseline_to": model.last_day.isoformat(),
            "baseline_observed_days": model.observed_days,
            "baseline_span_days": model.span_days,
            "baseline_coverage_pct": round(model.coverage * 100, 2),
            "day_of_week_factor_applied": model.dow_applied,
            "horizon_days": gap,
        },
    )


def deviation_ratio(actual: Decimal, expected: ExpectedRange) -> Decimal:
    """How far outside ``expected`` the value landed, in half-widths.

    ``0`` means inside the range. The unit is the range's own half-width, which
    is what makes one severity ladder usable across a currency total, a
    percentage and a count of days: a volatile store gets a wide band, so the
    same rupee deviation is a smaller ratio there, which is the correct answer.
    """
    if expected.low is not None and actual < expected.low:
        excess = expected.low - actual
    elif expected.high is not None and actual > expected.high:
        excess = actual - expected.high
    else:
        return Decimal("0")
    return (excess / expected.half_width).quantize(_Q4)


def severity_for(ratio: Decimal, *, floor: str = AlertSeverity.INFO) -> str:
    """Grade a deviation. ``floor`` raises the result, never caps it.

    A floor exists for exactly one rule (``NEGATIVE_CM3``, floored CRITICAL by
    ``AlertRuleKey``'s own docstring: at a negative contribution margin every
    additional order loses money, however shallow the loss). The grading still
    runs for it and the ratio is still recorded, so a reader can see the depth —
    the floor changes how loudly it is said, not what was measured.
    """
    if ratio <= 0:
        raise ValueError("a value inside its expected range has no severity")
    if ratio <= SEVERITY_INFO_MAX:
        graded = AlertSeverity.INFO
    elif ratio <= SEVERITY_WARNING_MAX:
        graded = AlertSeverity.WARNING
    else:
        graded = AlertSeverity.CRITICAL
    return max(graded, floor, key=SEVERITY_ORDER.index)


def days_of_cover(stock: int, velocity_per_day: Decimal) -> Decimal | None:
    """How long the stock lasts at the observed rate. ``None`` if it is not selling.

    None rather than a large number: a product with no sales has an *undefined*
    cover, not an infinite one, and "999 days of cover" sorts and charts as a
    measurement.
    """
    if velocity_per_day <= 0:
        return None
    return (Decimal(stock) / velocity_per_day).quantize(_Q4)


def _json_safe(value: Any) -> Any:
    """Decimals and dates into a JSON column without losing precision to float."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


# ===========================================================================
# The detector
# ===========================================================================


class AnomalyDetector:
    """Evaluates every rule for one bucket and writes the alerts.

    Rows are written with ``db.flush()``, not ``db.commit()`` — the caller owns
    the transaction, the same way ``CostRuleResolver.seed_from_legacy_settings``
    does. A detector that committed could not be run inside the aggregation
    transaction that produced the bucket it is judging.
    """

    def __init__(
        self,
        db: Session,
        *,
        tz_generation: int,
        now: datetime | None = None,
        margin_service: MarginService | None = None,
        redis_client: Optional["redis.Redis"] = None,
    ):
        self.db = db
        self.tz_generation = int(tz_generation)
        self.now = now or datetime.now(timezone.utc)
        self._margin_service = margin_service
        self._redis = redis_client

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    def run(self, bucket_date: date) -> DetectionRun:
        """Every rule, in order, for one reporting day."""
        run = DetectionRun(
            bucket_date=bucket_date,
            tz_generation=self.tz_generation,
            detected_at=self.now,
        )
        for evaluate in (
            self._revenue_band_rules,
            self._conversion_drop,
            self._payment_failure_spike,
            self._refund_spike,
            self._return_spike,
            self._rto_spike,
            self._marketing_cost_spike,
            self._out_of_stock_risk,
            self._cost_and_margin_rules,
            self._tracking_failure,
            self._ga4_sync_failure,
        ):
            try:
                evaluate(run)
            except Exception:  # pragma: no cover - defensive
                # One rule's bug must not silence the other twelve. The failure
                # is logged loudly and recorded as a skip, because a rule that
                # crashed and a rule that found nothing look identical from the
                # outside and only one of them needs a developer.
                logger.exception(
                    "anomaly rule %s failed for bucket %s",
                    getattr(evaluate, "__name__", "?"),
                    bucket_date,
                )
                run.skips.append(
                    RuleSkip(
                        rule_key=getattr(evaluate, "__name__", "?"),
                        metric="-",
                        reason="evaluator_error",
                        message=(
                            "This rule raised while evaluating and produced no "
                            "verdict. Treat its silence as unknown, not clean."
                        ),
                        detail={"bucket_date": bucket_date.isoformat()},
                    )
                )
        self.db.flush()
        for skip in run.skips:
            logger.info(
                "anomaly rule skipped: rule=%s bucket=%s reason=%s detail=%s",
                skip.rule_key,
                bucket_date.isoformat(),
                skip.reason,
                skip.detail,
            )
        return run

    # ------------------------------------------------------------------
    # Rules: one forecast band, read from both sides
    # ------------------------------------------------------------------
    def _revenue_band_rules(self, run: DetectionRun) -> None:
        """SALES_DROP and REVENUE_SPIKE — one fit on ``net_revenue``.

        Fitting once and reading both edges is not an optimisation: two separate
        fits could disagree about what a normal Saturday is, and then the same
        day could be simultaneously a drop and not a spike against two different
        notions of normal.
        """
        drop = RULES[AlertRuleKey.SALES_DROP]
        spike = RULES[AlertRuleKey.REVENUE_SPIKE]

        history = self._flow_history(AggOrderDaily, AggOrderDaily.net_revenue, run)
        actual = self._flow_bucket(AggOrderDaily, AggOrderDaily.net_revenue, run)

        for spec in (drop, spike):
            skip = self._bucket_guard(spec, actual, AggOrderDaily, run)
            if skip is not None:
                run.skips.append(skip)
        if actual is None:
            return

        expected = forecast_range(
            history, run.bucket_date, min_days=drop.min_baseline_days
        )
        if not isinstance(expected, ExpectedRange):
            for spec in (drop, spike):
                run.skips.append(self._skip(spec, expected))
            return

        for spec in (drop, spike):
            self._judge(run, spec, _dec(actual), expected, extra={"source": "agg_order_daily"})

    # ------------------------------------------------------------------
    def _conversion_drop(self, run: DetectionRun) -> None:
        """Orders placed per tracked session. A checkout-health signal, not traffic."""
        spec = RULES[AlertRuleKey.CONVERSION_DROP]
        self._rate_rule(
            run,
            spec,
            model=AggFunnelDaily,
            numerator=AggFunnelDaily.orders_placed,
            denominator=AggFunnelDaily.distinct_sessions,
            source="agg_funnel_daily",
            one_row_per_day=True,
            extra={
                "caveat": (
                    "FUNNEL_STARTS_AT_CART: distinct_sessions counts sessions "
                    "that reached a tracked cart/product step, not site "
                    "traffic. This is checkout completion, not site conversion."
                )
            },
        )

    def _payment_failure_spike(self, run: DetectionRun) -> None:
        spec = RULES[AlertRuleKey.PAYMENT_FAILURE_SPIKE]
        self._rate_rule(
            run,
            spec,
            model=AggPaymentDaily,
            numerator=AggPaymentDaily.failed,
            denominator=AggPaymentDaily.attempts,
            source="agg_payment_daily",
        )

    def _refund_spike(self, run: DetectionRun) -> None:
        """Refund value, as a flow.

        Deliberately the amount and not a refund *rate*: a rate divides by the
        day's sales, so a quiet trading day would report a refund spike for a
        perfectly ordinary refund — and SALES_DROP would already have said the
        interesting half of that.
        """
        spec = RULES[AlertRuleKey.REFUND_SPIKE]
        history = self._flow_history(AggOrderDaily, AggOrderDaily.refund_sum, run)
        actual = self._flow_bucket(AggOrderDaily, AggOrderDaily.refund_sum, run)

        skip = self._bucket_guard(spec, actual, AggOrderDaily, run)
        if skip is not None:
            run.skips.append(skip)
            return

        expected = forecast_range(history, run.bucket_date, min_days=spec.min_baseline_days)
        if not isinstance(expected, ExpectedRange):
            run.skips.append(self._skip(spec, expected))
            return
        self._judge(run, spec, _dec(actual), expected, extra={"source": "agg_order_daily"})

    def _return_spike(self, run: DetectionRun) -> None:
        spec = RULES[AlertRuleKey.RETURN_SPIKE]
        self._rate_rule(
            run,
            spec,
            model=AggProductDaily,
            numerator=AggProductDaily.returned_units,
            denominator=AggProductDaily.units,
            source="agg_product_daily",
        )

    def _rto_spike(self, run: DetectionRun) -> None:
        """RTO rate as ``AggShipmentDaily`` defines it: rto_initiated / shipments."""
        spec = RULES[AlertRuleKey.RTO_SPIKE]
        self._rate_rule(
            run,
            spec,
            model=AggShipmentDaily,
            numerator=AggShipmentDaily.rto_initiated,
            denominator=AggShipmentDaily.shipments,
            source="agg_shipment_daily",
        )

    # ------------------------------------------------------------------
    def _marketing_cost_spike(self, run: DetectionRun) -> None:
        """Marketing spend per 100 of net revenue.

        The spend series is resolved day by day through :class:`MarginService`'s
        own cost resolver, so it obeys the same effective dating as the margin
        report and cannot drift from it. Where no MARKETING_SPEND rule covers a
        day, that day is absent from the baseline rather than zero — an
        unconfigured cost is MISSING_COST_DATA's business, not this rule's.
        """
        spec = RULES[AlertRuleKey.MARKETING_COST_SPIKE]
        margin = self._margin()
        resolver = margin.resolver

        revenue_history = self._flow_history(
            AggOrderDaily, AggOrderDaily.net_revenue, run
        )
        revenue_now = self._flow_bucket(AggOrderDaily, AggOrderDaily.net_revenue, run)

        if revenue_now is None:
            run.skips.append(
                RuleSkip(
                    spec.rule_key,
                    spec.metric,
                    SkipReason.BUCKET_NOT_MEASURED,
                    "agg_order_daily has no row for this bucket, so there is no "
                    "revenue to measure marketing against.",
                    {"bucket_date": run.bucket_date.isoformat()},
                )
            )
            return

        spend_now = self._marketing_spend(resolver, run.bucket_date)
        if spend_now is None:
            run.skips.append(
                RuleSkip(
                    spec.rule_key,
                    spec.metric,
                    SkipReason.INPUT_NOT_CONFIGURED,
                    "No MARKETING_SPEND cost rule covers this bucket, so there "
                    "is no spend figure to compare against revenue. The absent "
                    "rule is reported as MISSING_COST_DATA, which is a "
                    "configuration problem and not a marketing one.",
                    {"bucket_date": run.bucket_date.isoformat()},
                )
            )
            return
        if revenue_now <= 0:
            run.skips.append(
                RuleSkip(
                    spec.rule_key,
                    spec.metric,
                    SkipReason.INSUFFICIENT_BUCKET_SAMPLE,
                    "Net revenue on this bucket is zero, so cost per unit of "
                    "revenue is undefined. A day with spend and no sales is a "
                    "sales problem; SALES_DROP is the rule that owns it.",
                    {"net_revenue": "0"},
                )
            )
            return

        history: dict[date, float] = {}
        for day, revenue in revenue_history.items():
            if revenue <= 0:
                continue
            spend = self._marketing_spend(resolver, day)
            if spend is None:
                continue
            history[day] = float(spend / Decimal(str(revenue)) * 100)

        expected = forecast_range(
            history, run.bucket_date, min_days=spec.min_baseline_days
        )
        if not isinstance(expected, ExpectedRange):
            run.skips.append(self._skip(spec, expected))
            return

        actual = _dec(spend_now / Decimal(str(revenue_now)) * 100)
        self._judge(
            run,
            spec,
            actual,
            expected,
            extra={
                "source": "analytics_cost_rules + agg_order_daily",
                "marketing_spend": str(_dec(spend_now)),
                "net_revenue": str(_dec(revenue_now)),
                "caveat": (
                    "Marketing spend is a single blended MARKETING_SPEND cost "
                    "rule pro-rated straight-line across the month, not "
                    "campaign-attributed spend. A step at a rule change or a "
                    "month boundary is a step in the input, not in the market."
                ),
            },
        )

    def _marketing_spend(self, resolver: Any, day: date) -> Decimal | None:
        """One day's marketing spend in rupees, or None when no rule covers it."""
        component = resolver.resolve(CostType.MARKETING_SPEND, day)
        computed = resolver.compute(component, days_in_period=1, on_date=day)
        if computed.is_missing or computed.value_minor is None:
            return None
        return from_minor(int(computed.value_minor))

    # ------------------------------------------------------------------
    def _out_of_stock_risk(self, run: DetectionRun) -> None:
        """Products that will not last the replenishment lead time.

        Not a forecast band. The question is "will this last N days", which
        needs a lead time and a rate — an interval around a stock level would be
        answering a question nobody asked. ``stock_close`` is a LEVEL and is
        never summed; ``units_sold`` is a FLOW and is.
        """
        spec = RULES[AlertRuleKey.OUT_OF_STOCK_RISK]
        assert_summable("units_sold", source="agg_inventory_daily")

        closing = self.db.execute(
            select(
                AggInventoryDaily.product_id,
                AggInventoryDaily.sku_snapshot,
                AggInventoryDaily.stock_close,
                AggInventoryDaily.days_oos,
            ).where(
                AggInventoryDaily.tz_generation == self.tz_generation,
                AggInventoryDaily.bucket_date == run.bucket_date,
            )
        ).all()

        if not closing:
            run.skips.append(
                RuleSkip(
                    spec.rule_key,
                    spec.metric,
                    SkipReason.BUCKET_NOT_MEASURED,
                    "agg_inventory_daily has no closing position for this "
                    "bucket. Inventory history is forward-only and cannot be "
                    "backfilled, so an absent day is genuinely unknown stock, "
                    "not zero stock.",
                    {"bucket_date": run.bucket_date.isoformat()},
                )
            )
            return

        velocity_from = run.bucket_date - timedelta(days=VELOCITY_WINDOW_DAYS - 1)
        sold = {
            row[0]: (int(row[1] or 0), int(row[2] or 0))
            for row in self.db.execute(
                select(
                    AggInventoryDaily.product_id,
                    func.sum(AggInventoryDaily.units_sold),
                    func.count(AggInventoryDaily.bucket_date),
                )
                .where(
                    AggInventoryDaily.tz_generation == self.tz_generation,
                    AggInventoryDaily.bucket_date >= velocity_from,
                    AggInventoryDaily.bucket_date <= run.bucket_date,
                )
                .group_by(AggInventoryDaily.product_id)
            ).all()
        }

        at_risk: list[tuple[Decimal, dict[str, Any]]] = []
        thin_history = 0
        not_selling = 0

        for product_id, sku, stock_close, days_oos in closing:
            units, observed_days = sold.get(product_id, (0, 0))
            if observed_days < spec.min_baseline_days:
                thin_history += 1
                continue
            velocity = (
                Decimal(units) / Decimal(observed_days)
            ).quantize(_Q4)
            if velocity < MIN_VELOCITY_UNITS_PER_DAY:
                not_selling += 1
                continue
            cover = days_of_cover(int(stock_close or 0), velocity)
            if cover is None or cover >= STOCK_COVER_DAYS:
                continue
            at_risk.append(
                (
                    cover,
                    {
                        "product_id": int(product_id),
                        "sku": sku,
                        "stock_close": int(stock_close or 0),
                        "velocity_units_per_day": str(velocity),
                        "velocity_window_days": observed_days,
                        "units_sold_in_window": units,
                        "days_oos": int(days_oos or 0),
                        "cover_days": str(cover),
                    },
                )
            )

        if thin_history or not_selling:
            run.skips.append(
                RuleSkip(
                    spec.rule_key,
                    spec.metric,
                    SkipReason.INSUFFICIENT_BASELINE,
                    f"{thin_history} product(s) have fewer than "
                    f"{spec.min_baseline_days} days of inventory ledger and "
                    f"{not_selling} sell below "
                    f"{MIN_VELOCITY_UNITS_PER_DAY} units/day. Neither gets a "
                    "cover figure: a runway needs a rate, and a rate needs "
                    "enough days to be one.",
                    {
                        "products_thin_history": thin_history,
                        "products_not_selling": not_selling,
                        "products_evaluated": len(closing),
                    },
                )
            )

        at_risk.sort(key=lambda pair: pair[0])
        truncated = max(0, len(at_risk) - MAX_STOCK_ALERTS)
        # Policy range: cover must reach the lead time. Half-width is half the
        # lead time, so "out of stock now" grades two half-widths out (CRITICAL)
        # and "a day short" grades a fraction of one (INFO).
        expected = ExpectedRange(
            low=Decimal(STOCK_COVER_DAYS),
            high=None,
            half_width=Decimal(STOCK_COVER_DAYS) / 2,
            basis=RangeBasis.POLICY_THRESHOLD,
            detail={
                "lead_time_days": STOCK_COVER_DAYS,
                "velocity_window_days": VELOCITY_WINDOW_DAYS,
                "min_velocity_units_per_day": str(MIN_VELOCITY_UNITS_PER_DAY),
                "source": "agg_inventory_daily",
            },
        )
        for cover, evidence in at_risk[:MAX_STOCK_ALERTS]:
            self._judge(
                run,
                spec,
                cover,
                expected,
                dimension="product",
                dimension_value=str(evidence["sku"] or evidence["product_id"]),
                extra={
                    **evidence,
                    "alerts_capped_at": MAX_STOCK_ALERTS,
                    "products_at_risk_not_alerted": truncated,
                },
            )

    # ------------------------------------------------------------------
    def _cost_and_margin_rules(self, run: DetectionRun) -> None:
        """MISSING_COST_DATA and NEGATIVE_CM3, in that order and never both.

        The order is the point. ``MarginService`` returns ``cm3_minor=None`` the
        moment any cost input is unresolved, so a store with no cost rules can
        only ever produce the configuration alert. It is impossible, by
        construction rather than by an ``if``, for an absent number to be
        reported as a negative margin.
        """
        missing_spec = RULES[AlertRuleKey.MISSING_COST_DATA]
        cm3_spec = RULES[AlertRuleKey.NEGATIVE_CM3]

        traded = self.db.execute(
            select(
                func.sum(AggOrderDaily.orders_total),
                func.sum(AggOrderDaily.net_revenue),
            ).where(
                AggOrderDaily.tz_generation == self.tz_generation,
                AggOrderDaily.bucket_date == run.bucket_date,
            )
        ).one()
        orders_total = int(traded[0] or 0)
        if traded[0] is None or orders_total <= 0:
            for spec in (missing_spec, cm3_spec):
                run.skips.append(
                    RuleSkip(
                        spec.rule_key,
                        spec.metric,
                        SkipReason.NO_TRADING_ACTIVITY,
                        "The store recorded no orders on this bucket, so there "
                        "is no margin to judge and no cost configuration this "
                        "day depended on. Firing here would tell an unlaunched "
                        "store the same thing every day until it stopped "
                        "reading.",
                        {
                            "bucket_date": run.bucket_date.isoformat(),
                            "orders_total": orders_total,
                            "bucket_measured": traded[0] is not None,
                        },
                    )
                )
            return

        result = self._margin().compute(
            run.bucket_date, run.bucket_date + timedelta(days=1)
        )

        all_inputs = CM2_COST_TYPES + CM3_COST_TYPES
        if result.missing_inputs:
            # Half-width is half the cascade's inputs: one absent rule out of
            # eight is a gap worth naming (INFO), most of them absent means no
            # margin figure exists at all (CRITICAL).
            expected = ExpectedRange(
                low=Decimal("0"),
                high=Decimal("0"),
                half_width=Decimal(len(all_inputs)) / 2,
                basis=RangeBasis.DEFINITIONAL,
                detail={
                    "cascade_inputs": list(all_inputs),
                    "source": "analytics_cost_rules",
                },
            )
            self._judge(
                run,
                missing_spec,
                Decimal(len(result.missing_inputs)),
                expected,
                extra={
                    "missing_inputs": list(result.missing_inputs),
                    "cost_coverage_pct": str(result.cost_coverage_pct),
                    "margin_warnings": list(result.warnings),
                    "consequence": (
                        "Contribution margin for this bucket is INCOMPLETE and "
                        "cm3 was not computed. NEGATIVE_CM3 is deliberately not "
                        "raised: it would assert a business fact from an absent "
                        "number."
                    ),
                },
            )
            run.skips.append(
                RuleSkip(
                    cm3_spec.rule_key,
                    cm3_spec.metric,
                    SkipReason.INPUT_NOT_CONFIGURED,
                    "CM3 was not computed because "
                    f"{', '.join(result.missing_inputs)} resolved to no cost "
                    "rule. A margin built by substituting zero for an unknown "
                    "cost reads better than reality, and a negative-margin "
                    "alert built the same way would be an accusation with no "
                    "evidence behind it.",
                    {"missing_inputs": list(result.missing_inputs)},
                )
            )
            return

        if result.cm3_minor is None:
            run.skips.append(
                RuleSkip(
                    cm3_spec.rule_key,
                    cm3_spec.metric,
                    SkipReason.INPUT_NOT_CONFIGURED,
                    "CM3 is None with no named missing input — COGS itself "
                    "could not be established. Not judged.",
                    {"cost_coverage_pct": str(result.cost_coverage_pct)},
                )
            )
            return

        cm3 = _dec(from_minor(int(result.cm3_minor)))
        nms = _dec(from_minor(int(result.net_merchandise_sales_minor)))
        # Scale: 5% of the day's merchandise sales. A loss that size is a real
        # hole; a loss of a few rupees on a large day is a rounding artefact and
        # grades accordingly — before the rule's CRITICAL floor is applied.
        scale = max((nms * Decimal("0.05")).quantize(_Q4), Decimal("1.0000"))
        expected = ExpectedRange(
            low=Decimal("0"),
            high=None,
            half_width=scale,
            basis=RangeBasis.DEFINITIONAL,
            detail={
                "definition": "CM3 = CM2 - marketing spend; below zero, every "
                "additional order loses money",
                "scale_basis": "5% of net merchandise sales",
                "net_merchandise_sales": str(nms),
                "source": "MarginService",
            },
        )
        self._judge(
            run,
            cm3_spec,
            cm3,
            expected,
            extra={
                "cm1": str(_dec(from_minor(int(result.cm1_minor or 0)))),
                "cm2": str(_dec(from_minor(int(result.cm2_minor or 0)))),
                "cm3": str(cm3),
                "net_merchandise_sales": str(nms),
                "cost_coverage_pct": str(result.cost_coverage_pct),
                "quality": result.quality.value,
                "source": "MarginService",
            },
        )

    # ------------------------------------------------------------------
    def _tracking_failure(self, run: DetectionRun) -> None:
        """The pipeline is green and the numbers are not moving.

        Two independent signatures, either of which is the failure:

        a. the order rollup has a healthy run of prior days and **no row** for
           this bucket — the day's numbers never landed, and every business rule
           above is therefore silent for a reason that is not calm trading;
        b. the sync log shows consecutive completed runs whose ``watermark_date``
           did not move — a job that runs, succeeds, and processes nothing.

        Signal (b) is the one a green dashboard cannot show you, and it is why
        ``AnalyticsSyncRun`` stores a data watermark rather than a timestamp.
        """
        spec = RULES[AlertRuleKey.TRACKING_FAILURE]

        prior_days = self.db.execute(
            select(func.count(func.distinct(AggOrderDaily.bucket_date))).where(
                AggOrderDaily.tz_generation == self.tz_generation,
                AggOrderDaily.bucket_date < run.bucket_date,
                AggOrderDaily.bucket_date
                >= run.bucket_date - timedelta(days=BASELINE_WINDOW_DAYS),
            )
        ).scalar_one()
        bucket_present = (
            self.db.execute(
                select(func.count()).select_from(AggOrderDaily).where(
                    AggOrderDaily.tz_generation == self.tz_generation,
                    AggOrderDaily.bucket_date == run.bucket_date,
                )
            ).scalar_one()
            > 0
        )

        runs = self.db.execute(
            select(AnalyticsSyncRun.watermark_date, AnalyticsSyncRun.started_at)
            .where(
                AnalyticsSyncRun.job == WATCHED_JOB,
                AnalyticsSyncRun.tz_generation == self.tz_generation,
                AnalyticsSyncRun.status.in_(_WATERMARK_STATUSES),
            )
            .order_by(AnalyticsSyncRun.started_at.desc())
            .limit(3)
        ).all()

        expected = ExpectedRange(
            low=Decimal("0"),
            high=Decimal("0"),
            half_width=Decimal("1"),
            basis=RangeBasis.POLICY_THRESHOLD,
            detail={
                "unit": "days the reporting data is behind the bucket",
                "source": "agg_order_daily + analytics_sync_runs",
            },
        )

        if not bucket_present and prior_days >= spec.min_baseline_days:
            self._judge(
                run,
                spec,
                Decimal("1"),
                expected,
                extra={
                    "signal": "bucket_never_written",
                    "prior_days_present": int(prior_days),
                    "consequence": (
                        "Every business rule for this bucket was skipped for "
                        "want of data. A dashboard reading this range shows a "
                        "short week, not an outage."
                    ),
                },
            )
            return

        if len(runs) >= 3 and all(r[0] is not None for r in runs):
            watermarks = [r[0] for r in runs]
            if len(set(watermarks)) == 1 and watermarks[0] < run.bucket_date:
                behind = (run.bucket_date - watermarks[0]).days
                self._judge(
                    run,
                    spec,
                    Decimal(behind),
                    expected,
                    extra={
                        "signal": "watermark_frozen",
                        "job": WATCHED_JOB,
                        "watermark_date": watermarks[0].isoformat(),
                        "completed_runs_inspected": len(runs),
                        "consequence": (
                            "The job keeps reporting success and the data "
                            "watermark has not moved across "
                            f"{len(runs)} completed runs. The dashboard is "
                            "serving numbers as current that stop at "
                            f"{watermarks[0].isoformat()}."
                        ),
                    },
                )
                return

        if bucket_present and len(runs) < 3:
            run.skips.append(
                RuleSkip(
                    spec.rule_key,
                    spec.metric,
                    SkipReason.INSUFFICIENT_BASELINE,
                    f"The bucket was written, and only {len(runs)} completed "
                    f"sync run(s) are on record for {WATCHED_JOB} under this "
                    "generation — fewer than the 3 needed to say a watermark "
                    "has stopped moving rather than never having started.",
                    {"completed_runs": len(runs), "bucket_present": True},
                )
            )
            return

        if not bucket_present:
            run.skips.append(
                RuleSkip(
                    spec.rule_key,
                    spec.metric,
                    SkipReason.INSUFFICIENT_BASELINE,
                    f"No row for this bucket, but only {int(prior_days)} prior "
                    f"day(s) of order rollup exist (minimum "
                    f"{spec.min_baseline_days}). A pipeline that has not "
                    "started yet is not a pipeline that has stopped.",
                    {"prior_days_present": int(prior_days)},
                )
            )
            return

        run.clear.append(
            RuleClear(
                spec.rule_key,
                spec.metric,
                Decimal("0"),
                expected,
            )
        )

    # ------------------------------------------------------------------
    def _ga4_sync_failure(self, run: DetectionRun) -> None:
        """Server-side conversions failing to reach GA4.

        ``SUPPRESSED_NO_CONSENT`` rows are excluded from both sides: they were
        deliberately never sent, and counting a respected consent decision as a
        delivery failure would make a privacy-compliant store look broken.
        """
        spec = RULES[AlertRuleKey.GA4_SYNC_FAILURE]
        day_start = datetime.combine(run.bucket_date, datetime.min.time(), timezone.utc)
        day_end = day_start + timedelta(days=1)

        rows = self.db.execute(
            select(AnalyticsEventOutbox.status, func.count())
            .where(
                AnalyticsEventOutbox.occurred_at >= day_start,
                AnalyticsEventOutbox.occurred_at < day_end,
                AnalyticsEventOutbox.status != OutboxStatus.SUPPRESSED_NO_CONSENT,
            )
            .group_by(AnalyticsEventOutbox.status)
        ).all()
        counts = {status: int(count) for status, count in rows}
        total = sum(counts.values())
        failed = counts.get(OutboxStatus.FAILED, 0)

        if total == 0:
            run.skips.append(
                RuleSkip(
                    spec.rule_key,
                    spec.metric,
                    SkipReason.NO_SOURCE_ROWS,
                    "No server-side GA4 events are owed for this bucket, so "
                    "none can have failed. Silence here is the absence of a "
                    "question, not a clean answer to one.",
                    {"bucket_date": run.bucket_date.isoformat()},
                )
            )
            return
        if total < spec.min_bucket_sample:
            run.skips.append(
                RuleSkip(
                    spec.rule_key,
                    spec.metric,
                    SkipReason.INSUFFICIENT_BUCKET_SAMPLE,
                    f"Only {total} outbox event(s) on this bucket, below the "
                    f"{spec.min_bucket_sample} needed before a failure "
                    "percentage means anything.",
                    {"outbox_events": total, "failed": failed},
                )
            )
            return

        rate = (Decimal(failed) / Decimal(total) * 100).quantize(_Q4)
        expected = ExpectedRange(
            low=None,
            high=GA4_MAX_FAILURE_PCT,
            half_width=GA4_MAX_FAILURE_PCT / 2,
            basis=RangeBasis.POLICY_THRESHOLD,
            detail={
                "acceptable_failure_pct": str(GA4_MAX_FAILURE_PCT),
                "source": "analytics_event_outbox",
            },
        )
        self._judge(
            run,
            spec,
            rate,
            expected,
            extra={
                "outbox_events": total,
                "failed": failed,
                "delivered": counts.get(OutboxStatus.DELIVERED, 0),
                "pending": counts.get(OutboxStatus.PENDING, 0),
                "consequence": (
                    "Purchases are not reaching GA4 from the server, so "
                    "attribution for those orders is degrading now and cannot "
                    "be recovered later."
                ),
            },
        )

    # ------------------------------------------------------------------
    # Shared rule machinery
    # ------------------------------------------------------------------
    def _rate_rule(
        self,
        run: DetectionRun,
        spec: RuleSpec,
        *,
        model: Any,
        numerator: Any,
        denominator: Any,
        source: str,
        one_row_per_day: bool = False,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """A percentage rule: recomputed from its two stored parts every time.

        Never an average of stored rates — ``metric_kind`` refuses to sum a
        RATIO for exactly this reason, and a daily rate averaged over a month
        weights a 3-order day the same as a 300-order one.

        ``one_row_per_day`` relaxes the additivity guard for a rollup whose
        UNIQUE key already holds it to one row per reporting day. It exists for
        ``agg_funnel_daily.distinct_sessions``, which ``metric_kind`` classifies
        DISTINCT and rightly refuses to SUM: the aggregate here spans a single
        row, and each day stays its own point — the value is never combined
        across days anywhere in this module.

        Days whose denominator is below ``min_bucket_sample`` are **dropped from
        the baseline**, not zero-filled: a 0% conversion rate from a day with
        four sessions is not evidence about conversion, and leaving it in drags
        the band down and then flags the normal days that follow.
        """
        assert_summable(numerator.key, source=source)
        if one_row_per_day:
            kind = classify(denominator.key, source=source)
            if kind is MetricKind.FLOW:  # pragma: no cover - defensive
                assert_summable(denominator.key, source=source)
        else:
            assert_summable(denominator.key, source=source)

        rows = self.db.execute(
            select(
                model.bucket_date,
                func.sum(numerator),
                func.sum(denominator),
            )
            .where(
                model.tz_generation == self.tz_generation,
                model.bucket_date
                >= run.bucket_date - timedelta(days=BASELINE_WINDOW_DAYS),
                model.bucket_date <= run.bucket_date,
            )
            .group_by(model.bucket_date)
        ).all()

        if not rows:
            run.skips.append(
                RuleSkip(
                    spec.rule_key,
                    spec.metric,
                    SkipReason.NO_SOURCE_ROWS,
                    f"{source} holds no rows at all for generation "
                    f"{self.tz_generation} in the baseline window. The rollup "
                    "has never been built here, which is a pipeline state and "
                    "not a business one.",
                    {"source": source, "tz_generation": self.tz_generation},
                )
            )
            return

        history: dict[date, float] = {}
        bucket: tuple[float, int] | None = None
        thin_days = 0
        for day, num, den in rows:
            den_int = int(den or 0)
            num_f = float(num or 0)
            if day == run.bucket_date:
                bucket = (num_f, den_int)
                continue
            if den_int < spec.min_bucket_sample:
                thin_days += 1
                continue
            history[day] = num_f / den_int * 100 if den_int else 0.0

        if bucket is None:
            run.skips.append(
                RuleSkip(
                    spec.rule_key,
                    spec.metric,
                    SkipReason.BUCKET_NOT_MEASURED,
                    f"{source} has no row for this bucket, so the day was not "
                    "measured. That is different from measuring zero.",
                    {"source": source, "bucket_date": run.bucket_date.isoformat()},
                )
            )
            return

        num_now, den_now = bucket
        if den_now < spec.min_bucket_sample:
            run.skips.append(
                RuleSkip(
                    spec.rule_key,
                    spec.metric,
                    SkipReason.INSUFFICIENT_BUCKET_SAMPLE,
                    f"Only {den_now} {spec.sample_label or 'observations'} on "
                    f"this bucket, below the {spec.min_bucket_sample} this rule "
                    f"requires. {spec.rationale}",
                    {
                        "denominator": den_now,
                        "min_bucket_sample": spec.min_bucket_sample,
                        "source": source,
                    },
                )
            )
            return

        expected = forecast_range(
            history, run.bucket_date, min_days=spec.min_baseline_days
        )
        if not isinstance(expected, ExpectedRange):
            reason, message, detail = expected
            run.skips.append(
                RuleSkip(
                    spec.rule_key,
                    spec.metric,
                    reason,
                    message,
                    {
                        **detail,
                        "source": source,
                        "baseline_days_dropped_thin": thin_days,
                        "min_bucket_sample": spec.min_bucket_sample,
                    },
                )
            )
            return

        actual = _dec(num_now / den_now * 100)
        self._judge(
            run,
            spec,
            actual,
            expected,
            extra={
                "source": source,
                "numerator": num_now,
                "denominator": den_now,
                "baseline_days_dropped_thin": thin_days,
                **(extra or {}),
            },
        )

    def _judge(
        self,
        run: DetectionRun,
        spec: RuleSpec,
        actual: Decimal,
        expected: ExpectedRange,
        *,
        dimension: str = DIMENSION_UNKNOWN,
        dimension_value: str = DIMENSION_UNKNOWN,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """Compare, and either record an alert or record that it was clean.

        The direction check is not redundant with the range. SALES_DROP and
        REVENUE_SPIKE share one band; without it, a revenue spike would fire the
        drop rule as well, and an operator would read "sales dropped" on the
        best day of the quarter.
        """
        ratio = deviation_ratio(actual, expected)
        if ratio <= 0:
            run.clear.append(
                RuleClear(
                    spec.rule_key,
                    spec.metric,
                    actual,
                    expected,
                    dimension=dimension,
                    dimension_value=dimension_value,
                )
            )
            return

        below = expected.low is not None and actual < expected.low
        if (spec.direction == "below") != below:
            run.clear.append(
                RuleClear(
                    spec.rule_key,
                    spec.metric,
                    actual,
                    expected,
                    dimension=dimension,
                    dimension_value=dimension_value,
                )
            )
            return

        severity = severity_for(ratio, floor=spec.severity_floor)
        alert = self._upsert(
            run,
            spec,
            severity=severity,
            actual=actual,
            expected=expected,
            ratio=ratio,
            dimension=dimension,
            dimension_value=dimension_value,
            extra=extra or {},
        )
        if alert is not None:
            run.alerts.append(alert)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _upsert(
        self,
        run: DetectionRun,
        spec: RuleSpec,
        *,
        severity: str,
        actual: Decimal,
        expected: ExpectedRange,
        ratio: Decimal,
        dimension: str,
        dimension_value: str,
        extra: Mapping[str, Any],
    ) -> AnalyticsAlert | None:
        """One row per (rule, bucket, dimension). Re-running refreshes, never adds.

        Re-detection of an alert a human has already touched does **not** rewrite
        it. ``AnalyticsAlert`` promises that the measured values are never
        rewritten after detection, and once someone has acknowledged, resolved or
        muted a row, those numbers are what they were told — editing them under
        an operator is how an alert becomes unciteable. An OPEN row has been told
        to nobody, so refreshing it is free, and it is what keeps a bucket that
        gets recomputed four times from producing four identical rows.

        Detector evidence lives under ``context['evidence']`` and lifecycle under
        ``context['lifecycle']``, so the two can be written on different
        schedules without one clobbering the other.
        """
        existing = self.db.execute(
            select(AnalyticsAlert).where(
                AnalyticsAlert.rule_key == spec.rule_key,
                AnalyticsAlert.bucket_date == run.bucket_date,
                AnalyticsAlert.dimension == dimension,
                AnalyticsAlert.dimension_value == dimension_value,
            )
        ).scalars().first()

        evidence = _json_safe(
            {
                "rule": spec.rule_key,
                "metric": spec.metric,
                "direction": spec.direction,
                "deviation_ratio": ratio,
                "severity_floor": spec.severity_floor,
                "min_baseline_days": spec.min_baseline_days,
                "baseline_rationale": spec.rationale,
                "range_basis": expected.basis,
                "range": {
                    "low": expected.low,
                    "high": expected.high,
                    "half_width": expected.half_width,
                    **expected.detail,
                },
                "tz_generation": self.tz_generation,
                **dict(extra),
            }
        )

        if existing is not None:
            if existing.status != AlertStatus.OPEN:
                logger.info(
                    "alert %s (%s/%s) re-detected while %s; left untouched",
                    existing.id,
                    spec.rule_key,
                    run.bucket_date,
                    existing.status,
                )
                return None
            lifecycle = dict((existing.context or {}).get("lifecycle") or {})
            lifecycle["evaluations"] = int(lifecycle.get("evaluations") or 1) + 1
            lifecycle.setdefault(
                "first_detected_at",
                existing.detected_at.isoformat() if existing.detected_at else None,
            )
            lifecycle["last_evaluated_at"] = self.now.isoformat()
            existing.severity = severity
            existing.expected_low = expected.low
            existing.expected_high = expected.high
            existing.actual_value = actual
            existing.detected_at = self.now
            existing.context = {"evidence": evidence, "lifecycle": lifecycle}
            return existing

        alert = AnalyticsAlert(
            rule_key=spec.rule_key,
            severity=severity,
            metric=spec.metric,
            dimension=dimension,
            dimension_value=dimension_value,
            expected_low=expected.low,
            expected_high=expected.high,
            actual_value=actual,
            bucket_date=run.bucket_date,
            detected_at=self.now,
            status=AlertStatus.OPEN,
            context={
                "evidence": evidence,
                "lifecycle": {
                    "first_detected_at": self.now.isoformat(),
                    "last_evaluated_at": self.now.isoformat(),
                    "evaluations": 1,
                    "transitions": [],
                },
            },
        )
        self.db.add(alert)
        return alert

    # ------------------------------------------------------------------
    # Source readers
    # ------------------------------------------------------------------
    def _flow_history(self, model: Any, column: Any, run: DetectionRun) -> dict[date, float]:
        """A FLOW column, one point per day, strictly BEFORE the judged bucket."""
        assert_summable(column.key, source=model.__tablename__)
        rows = self.db.execute(
            select(model.bucket_date, func.sum(column))
            .where(
                model.tz_generation == self.tz_generation,
                model.bucket_date
                >= run.bucket_date - timedelta(days=BASELINE_WINDOW_DAYS),
                model.bucket_date < run.bucket_date,
            )
            .group_by(model.bucket_date)
        ).all()
        return {day: float(value or 0) for day, value in rows}

    def _flow_bucket(self, model: Any, column: Any, run: DetectionRun) -> float | None:
        """The bucket's own value, or None when the bucket has no row at all.

        None and 0.0 are different answers and the caller must be able to tell
        them apart: an unwritten bucket is a pipeline problem, a measured zero is
        a business one.
        """
        assert_summable(column.key, source=model.__tablename__)
        row = self.db.execute(
            select(func.sum(column), func.count())
            .select_from(model)
            .where(
                model.tz_generation == self.tz_generation,
                model.bucket_date == run.bucket_date,
            )
        ).one()
        if int(row[1] or 0) == 0:
            return None
        return float(row[0] or 0)

    def _bucket_guard(
        self, spec: RuleSpec, actual: float | None, model: Any, run: DetectionRun
    ) -> RuleSkip | None:
        if actual is not None:
            return None
        return RuleSkip(
            spec.rule_key,
            spec.metric,
            SkipReason.BUCKET_NOT_MEASURED,
            f"{model.__tablename__} has no row for {run.bucket_date.isoformat()} "
            f"under generation {self.tz_generation}. The bucket was not "
            "measured, which is not the same as measuring zero — a zero would "
            "be the largest sales drop the store has ever had.",
            {
                "source": model.__tablename__,
                "bucket_date": run.bucket_date.isoformat(),
                "tz_generation": self.tz_generation,
            },
        )

    @staticmethod
    def _skip(spec: RuleSpec, refusal: tuple[str, str, dict[str, Any]]) -> RuleSkip:
        reason, message, detail = refusal
        return RuleSkip(spec.rule_key, spec.metric, reason, message, detail)

    def _margin(self) -> MarginService:
        if self._margin_service is None:
            self._margin_service = MarginService(self.db, redis_client=self._redis)
        return self._margin_service


# ===========================================================================
# Module-level entry points
# ===========================================================================


def run(
    db: Session,
    bucket_date: date,
    *,
    tz_generation: int,
    now: datetime | None = None,
    margin_service: MarginService | None = None,
    redis_client: Optional["redis.Redis"] = None,
) -> DetectionRun:
    """Evaluate every rule for one bucket and return the full verdict.

    Prefer this over :func:`detect` anywhere the *absence* of alerts has to be
    explained — a health panel, an operator asking why nothing fired, a test.
    """
    return AnomalyDetector(
        db,
        tz_generation=tz_generation,
        now=now,
        margin_service=margin_service,
        redis_client=redis_client,
    ).run(bucket_date)


def detect(
    db: Session,
    bucket_date: date,
    *,
    tz_generation: int,
    now: datetime | None = None,
    margin_service: MarginService | None = None,
    redis_client: Optional["redis.Redis"] = None,
) -> list[AnalyticsAlert]:
    """Evaluate every rule for one bucket, write the alerts, return them.

    Rows are flushed, not committed — the caller owns the transaction.
    """
    return run(
        db,
        bucket_date,
        tz_generation=tz_generation,
        now=now,
        margin_service=margin_service,
        redis_client=redis_client,
    ).alerts


# ===========================================================================
# Lifecycle
# ===========================================================================

#: Legal moves. Everything absent from here is refused, loudly.
_TRANSITIONS: dict[str, frozenset[str]] = {
    AlertStatus.OPEN: frozenset(
        {AlertStatus.ACKNOWLEDGED, AlertStatus.RESOLVED, AlertStatus.MUTED}
    ),
    AlertStatus.ACKNOWLEDGED: frozenset({AlertStatus.RESOLVED, AlertStatus.MUTED}),
    #: Terminal. Re-opening would erase the record that it was handled; the
    #: honest way to say "it happened again" is a new bucket's alert.
    AlertStatus.RESOLVED: frozenset(),
    #: Terminal by intent. Un-muting is a deliberate act, not a detector's.
    AlertStatus.MUTED: frozenset(),
}


def _transition(
    db: Session,
    alert: AnalyticsAlert,
    to_status: str,
    *,
    user_id: int | None,
    note: str | None,
    now: datetime | None,
) -> AnalyticsAlert:
    """Move an alert along its lifecycle and record that it moved.

    The trail goes in ``context['lifecycle']['transitions']`` rather than
    overwriting the detector's evidence, so an alert can still justify itself
    after it has been handled — which is the only time anyone re-reads one.
    """
    current = alert.status or AlertStatus.OPEN
    allowed = _TRANSITIONS.get(current, frozenset())
    if to_status not in allowed:
        raise AlertTransitionError(
            f"alert {alert.id} is {current!r} and cannot move to {to_status!r}; "
            f"legal moves from {current!r} are "
            f"{sorted(allowed) or 'none (terminal)'}. A terminal alert is a "
            "record of what was done, not a mutable row."
        )

    stamp = now or datetime.now(timezone.utc)
    context = dict(alert.context or {})
    lifecycle = dict(context.get("lifecycle") or {})
    trail = list(lifecycle.get("transitions") or [])
    trail.append(
        {
            "from": current,
            "to": to_status,
            "at": stamp.isoformat(),
            "by_user_id": user_id,
            "note": note,
        }
    )
    lifecycle["transitions"] = trail
    context["lifecycle"] = lifecycle
    alert.context = context

    alert.status = to_status
    if to_status == AlertStatus.ACKNOWLEDGED:
        alert.acknowledged_by_user_id = user_id
        alert.acknowledged_at = stamp
    if note:
        alert.resolution_note = note[:500]
    db.flush()
    return alert


def acknowledge(
    db: Session,
    alert: AnalyticsAlert,
    *,
    user_id: int | None,
    note: str | None = None,
    now: datetime | None = None,
) -> AnalyticsAlert:
    """A human has seen it and owns it. Stops the detector rewriting its numbers."""
    return _transition(
        db, alert, AlertStatus.ACKNOWLEDGED, user_id=user_id, note=note, now=now
    )


def resolve(
    db: Session,
    alert: AnalyticsAlert,
    *,
    note: str,
    user_id: int | None = None,
    now: datetime | None = None,
) -> AnalyticsAlert:
    """The condition cleared or was fixed. ``note`` is required, not optional.

    An alert resolved with no explanation is the same alert next quarter, and
    the person re-diagnosing it will not be the person who fixed it.
    """
    if not (note or "").strip():
        raise AlertTransitionError(
            "resolving an alert requires a note. 'Resolved' with no reason is "
            "indistinguishable from 'dismissed', and the next person to see "
            "this rule fire has to start the diagnosis from nothing."
        )
    return _transition(
        db, alert, AlertStatus.RESOLVED, user_id=user_id, note=note, now=now
    )


def mute(
    db: Session,
    alert: AnalyticsAlert,
    *,
    note: str,
    user_id: int | None = None,
    now: datetime | None = None,
) -> AnalyticsAlert:
    """Known and deliberately silenced. Never the same thing as resolved."""
    if not (note or "").strip():
        raise AlertTransitionError(
            "muting an alert requires a note saying why it is safe to silence."
        )
    return _transition(
        db, alert, AlertStatus.MUTED, user_id=user_id, note=note, now=now
    )
