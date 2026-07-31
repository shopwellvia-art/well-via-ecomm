"""Flow vs level vs ratio — the taxonomy that decides how a metric may be combined.

Two agents independently hit this while wiring views, from opposite directions:

* ``agg_customer_snapshot`` holds **lifetime** state per customer — LTV, order
  count, recency. Summing those over a 30-day window adds one customer's
  lifetime value to itself thirty times.
* ``agg_inventory_daily.stock_close`` is a closing balance. Summing seven daily
  balances into a weekly bucket reports **seven times** the real stock.

Both are the same mistake, and it is invisible: the query succeeds, the number is
plausible, and it is wrong by whatever factor the bucket count happens to be. A
reader has no way to notice, because there is nothing to compare it against.

So combination is a property of the metric, declared once here, and the resolver
asks rather than assumes.

    FLOW   measured over an interval        -> SUM across buckets
    LEVEL  measured at an instant           -> take the LATEST in the window
    RATIO  derived from two others          -> recompute from its parts
    DISTINCT  a count of unique things      -> recompute from facts; never combine

Averaging is the mirror image and is guarded separately by `assert_averagable`:
a FLOW is the one kind that may be SUMmed and the one kind that may NOT be
AVERAGEd, because the mean of a set of daily sums depends on how wide the bucket
happens to be. Only a LEVEL has a meaningful average across buckets, which is
what `analytics_repository`'s `avg:` projection enforces.

DISTINCT deserves its own kind rather than being lumped with LEVEL. Summing
``distinct_customers`` across days overcounts anyone who bought twice, and taking
the *latest* day's value is just as wrong. There is no way to combine it at all —
a weekly distinct count must be recomputed from the underlying rows, and the only
honest answer at query time is to refuse and say why.
"""
from __future__ import annotations

from enum import Enum


class MetricKind(str, Enum):
    #: Measured over an interval: revenue, units, orders. Additive across buckets.
    FLOW = "flow"
    #: Measured at an instant: stock on hand, lifetime value, RFM score. The value
    #: for a window is the value at its END, not the sum of its days.
    LEVEL = "level"
    #: Derived: AOV, margin %, conversion rate. Must be recomputed from numerator
    #: and denominator — an average of averages is wrong the moment you re-bucket.
    RATIO = "ratio"
    #: A count of unique things. Not combinable in either direction.
    DISTINCT = "distinct"


#: Columns whose name alone is enough to classify them. Checked as a suffix or
#: exact match so a new rollup column is classified without an edit here — the
#: alternative is a hand-maintained list, which is exactly what rots.
_LEVEL_SUFFIXES = (
    "_close",
    "_ltv",
    "_score",
    "_balance",
    "_on_hand",
    "_days",          # recency_days, tenure_days: an age, not an accumulation
)
_LEVEL_EXACT = frozenset(
    {
        "stock_close",
        "stock_value_close",
        # ---- the inventory ledger's three states -------------------------
        # All three are read off `agg_inventory_daily` and all three describe
        # the product's POSITION at the close of `bucket_date`, exactly like
        # `stock_close` beside them. Nothing in their names says so, so before
        # this entry they fell through to FLOW and were summable.
        #
        # `is_oos` — "stock was zero at close". Summing it across a window
        # yields a count of out-of-stock PRODUCT-DAYS, which is a real quantity
        # but is not the metric anybody asks for by that name:
        # `kpis.stockout_rate` is defined POINT IN TIME ("how many products are
        # out of stock right now"), and `SUM(is_oos)/COUNT(*)` over a window is
        # the time-weighted share of product-days — a different, entirely
        # plausible number published under a defined label. It is blocked here
        # rather than allowed as FLOW because the two are indistinguishable on
        # a chart. This became urgent with the `SUM(<Boolean>)` cast fix in
        # `analytics_repository`: until that fix the wrong query returned
        # `True`, which is obviously broken; now it returns a believable count,
        # so the *classification* is the only thing left standing between a
        # resolver and a wrong stockout rate. A caller who genuinely wants
        # product-days-out-of-stock can still ask the repository for that SUM
        # explicitly — the repository is a safety boundary, not a semantic one
        # — but no generic resolver may reach it by accident.
        #
        # `days_oos` — a RUNNING counter, not a per-bucket quantity.
        # `jobs_ops.InventoryDailyJob._days_oos` reads the PREVIOUS bucket's
        # value and writes `previous + 1`, so a three-day stockout is stored as
        # 1, 2, 3. Summing those reports 6 out-of-stock days for a 3-day
        # stockout — wrong by a triangular number, so the error grows with the
        # length of the very stockout it is describing.
        #
        # `reorder_gap` — `stock_close - reorder_point` as of this date, i.e. a
        # difference of two levels, and therefore a level. NULL means "no
        # reorder point configured" (the job writes NULL unconditionally
        # today), which is not 0; SUM would silently skip the NULLs and total
        # the rest into a number with no unit.
        "is_oos",
        "days_oos",
        "reorder_gap",
        # NOTE: deliberately three exact entries rather than a blanket `is_`
        # prefix rule. `agg_promo_daily.is_loyalty_reward` is the third Boolean
        # in the schema and it is a row ATTRIBUTE on a flow table, not a state
        # measured at an instant — the right answer for it is
        # `levels.COUNT_WHERE_PREFIX` ("how many rows had the flag set"), and
        # LEVEL's remedy text ("take the LATEST bucket") would describe that
        # wrongly. A shape that needs its own remedy needs its own kind, not a
        # prefix rule that quietly mislabels it.
        "gross_ltv",
        "net_ltv",
        "margin_ltv",
        "recency_days",
        "frequency",
        "monetary",
        "r_score",
        "f_score",
        "m_score",
        "tenure_days",
        "cohort_size",   # repeated on every period row of a cohort
        "orders_count",  # lifetime count on the customer snapshot
        "aov",
        "is_active",
        "churn_risk_band",
        "rfm_segment",
        "preferred_payment_method",
    }
)
_DISTINCT_EXACT = frozenset(
    {
        "distinct_customers",
        "distinct_sessions",
        "active_customers",
        "cohort_size",
    }
)
_RATIO_SUFFIXES = ("_pct", "_rate", "_ratio")

#: Columns that are classified LEVEL but are stored PER-ROW DERIVED values, so
#: averaging them across rows is an average of averages even though summing them
#: is blocked for the ordinary level reason.
#:
#: `agg_customer_snapshot.aov` is the case: `gross_ltv / orders_count` per
#: customer, and its own model docstring says in these words "never SUM it,
#: never AVG it across rows. A cross-customer AOV is SUM(gross_ltv) /
#: SUM(orders_count)". `classify` calls it LEVEL — which is right for combining
#: one customer's row ACROSS DAYS — so `assert_averagable` needs this second
#: list to stop an AVG ACROSS CUSTOMERS, which LEVEL alone would wave through.
#: Kept separate from `_RATIO_SUFFIXES` on purpose: moving `aov` to RATIO would
#: change `combine_strategy` from "latest" to "recompute" and re-route live
#: level-resolver bindings, which is a different change from closing this hole.
_NEVER_AVERAGE = frozenset({"aov"})


class NonAdditive(ValueError):
    """Raised when a caller tries to combine a metric in a way that is wrong.

    Deliberately an exception rather than a silently-corrected result: a resolver
    asking to SUM a level has a bug in its binding, and returning the "right"
    answer anyway would leave that bug in place to be copied.
    """


class NonAveragable(NonAdditive):
    """Raised when a caller tries to AVG a metric whose average is meaningless.

    A subclass of `NonAdditive` rather than a sibling of it so every existing
    `except NonAdditive` — the resolvers and the view service both have one —
    keeps catching it. The distinct class exists so a caller that wants to
    handle "cannot be averaged" differently from "cannot be summed" can, without
    parsing the message.
    """


def classify(column: str, *, source: str | None = None) -> MetricKind:
    """Best-effort kind for a rollup column.

    Name-based on purpose. The alternative — a per-column registry — is another
    thing to keep in sync with the schema, and the schema already names these
    consistently because the model docstrings insisted on it.
    """
    name = column.lower()
    if name in _DISTINCT_EXACT:
        return MetricKind.DISTINCT
    if name in _LEVEL_EXACT or name.endswith(_LEVEL_SUFFIXES):
        return MetricKind.LEVEL
    if name.endswith(_RATIO_SUFFIXES):
        return MetricKind.RATIO
    # A per-customer snapshot table holds nothing BUT levels, whatever the
    # column is called, so the source overrides the name.
    if source == "agg_customer_snapshot":
        return MetricKind.LEVEL
    return MetricKind.FLOW


def assert_summable(column: str, *, source: str | None = None) -> None:
    """Guard before a SUM. Raises with the reason and the correct alternative."""
    kind = classify(column, source=source)
    if kind is MetricKind.FLOW:
        return
    remedy = {
        MetricKind.LEVEL: (
            "it is a level (measured at an instant), so a window's value is the "
            "LATEST bucket, not the sum of its days"
        ),
        MetricKind.RATIO: (
            "it is a ratio, so recompute it from its numerator and denominator; "
            "an average of averages is wrong as soon as buckets are combined"
        ),
        MetricKind.DISTINCT: (
            "it is a distinct count, which cannot be combined in either "
            "direction — recompute it from the underlying facts"
        ),
    }[kind]
    raise NonAdditive(
        f"refusing to SUM {source + '.' if source else ''}{column}: {remedy}. "
        "The query would succeed and return a plausible number that is wrong by "
        "roughly the number of buckets in the window."
    )


def assert_averagable(column: str, *, source: str | None = None) -> None:
    """Guard before an AVG. Only a LEVEL may be averaged across buckets.

    The mirror image of `assert_summable`, and the asymmetry is the point: a
    FLOW is the one kind that may be SUMmed and the one kind that may NOT be
    AVERAGEd.

    Averaging a flow answers a different question from the one asked.
    `AVG(units_sold)` over a window is "units per bucket on average", which
    changes value the moment the bucket width changes (daily vs weekly rollups
    of the same period give different answers for the same underlying sales)
    and which is not what any caller reaching for "average X over the window"
    means. The window value of a flow is its SUM; if a per-day rate is wanted it
    must be written as `SUM(x) / <an explicit denominator>` so the denominator
    is visible and auditable rather than implied by the row count.

    A LEVEL is the opposite: it exists at every instant, so the mean of its
    observations is a real quantity — average stock on hand over a window is
    exactly the denominator Inventory Turnover needs, and it cannot be derived
    from a SUM of the level (that would be stock counted once per day).

    RATIO and DISTINCT are refused for the same reasons they cannot be summed:
    an average of stored ratios is an average of averages, and a distinct count
    cannot be combined in any direction at all.
    """
    name = column.lower()
    if name in _NEVER_AVERAGE:
        raise NonAveragable(
            f"refusing to AVG {source + '.' if source else ''}{column}: it is a "
            "per-row DERIVED value, so averaging it across rows is an average of "
            "averages — every row counts equally however large its denominator "
            "was. Recompute it from the two stored components instead."
        )
    kind = classify(column, source=source)
    if kind is MetricKind.LEVEL:
        return
    remedy = {
        MetricKind.FLOW: (
            "it is a flow (measured over an interval), so a window's value is the "
            "SUM of its buckets. Averaging daily sums answers 'how much per "
            "bucket', which is a different metric and changes value when the "
            "bucket width changes — write SUM(x) over an explicit denominator if "
            "that is what you meant"
        ),
        MetricKind.RATIO: (
            "it is a ratio, so recompute it from its numerator and denominator; "
            "the mean of stored ratios weights every bucket equally regardless "
            "of how big its denominator was"
        ),
        MetricKind.DISTINCT: (
            "it is a distinct count, which cannot be combined in either "
            "direction — recompute it from the underlying facts"
        ),
    }[kind]
    raise NonAveragable(
        f"refusing to AVG {source + '.' if source else ''}{column}: {remedy}. "
        "Only a LEVEL may be averaged across buckets."
    )


def combine_strategy(column: str, *, source: str | None = None) -> str:
    """How a resolver should combine this column across buckets."""
    return {
        MetricKind.FLOW: "sum",
        MetricKind.LEVEL: "latest",
        MetricKind.RATIO: "recompute",
        MetricKind.DISTINCT: "refuse",
    }[classify(column, source=source)]
