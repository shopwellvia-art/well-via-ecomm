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


class NonAdditive(ValueError):
    """Raised when a caller tries to combine a metric in a way that is wrong.

    Deliberately an exception rather than a silently-corrected result: a resolver
    asking to SUM a level has a bug in its binding, and returning the "right"
    answer anyway would leave that bug in place to be copied.
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


def combine_strategy(column: str, *, source: str | None = None) -> str:
    """How a resolver should combine this column across buckets."""
    return {
        MetricKind.FLOW: "sum",
        MetricKind.LEVEL: "latest",
        MetricKind.RATIO: "recompute",
        MetricKind.DISTINCT: "refuse",
    }[classify(column, source=source)]
