"""The two level-aware resolvers: ``snapshot`` and ``cohort_matrix``.

Everything else in this package answers a window by **adding its buckets up**.
That is correct for a flow (revenue, units, orders) and wrong for everything in
``agg_customer_snapshot`` and for ``agg_inventory_daily.stock_close``, because
those tables store a *state*, not an *event*. `metric_kind` states the taxonomy
once; this module is the query shape that honours it.

The distinction that makes ``snapshot`` correct
-----------------------------------------------
There are two different sums in play and only one of them is a bug.

* **Summing a level across TIME is wrong.** ``agg_customer_snapshot`` writes one
  row per customer per day holding that customer's *lifetime* state. Thirty days
  of ``gross_ltv`` added together reports one customer's lifetime value thirty
  times over. Nothing raises; the number is simply wrong by the number of
  snapshot days in range, and no reader can tell.
* **Summing a level across CUSTOMERS at ONE instant is right.** At a single
  ``bucket_date`` every customer appears exactly once, so
  ``SUM(gross_ltv) WHERE bucket_date = <that day>`` is the total book value of
  the customer base on that day — a real measurement, and the only way to answer
  "how much LTV sits in the champions segment".

So this resolver never widens a level over time and never refuses to widen it
over the population. It pins one instant — the **latest bucket inside the
requested window** — and does all of its arithmetic there.

Two consequences worth stating outright, because both are places a plausible
shortcut would lie:

* A window containing **no snapshot bucket at all** returns no data and
  ``NO_ROLLUP_YET``. It does NOT reach back to the newest bucket before the
  window. Answering "as of the 3rd" when the caller asked about the 20th-27th
  silently answers a different question, and the response would look identical
  to one that was actually about the 20th-27th.
* A **comparison** value is the level at the latest bucket of the *comparison*
  window, so a delta is level-vs-level. Comparing this instant's level against
  the previous period's *sum* would produce a delta of roughly minus the number
  of days in the window, every time, and it would look like a collapse.

Counting at an instant
----------------------
``AnalyticsRepository`` projects ``row_count`` and ``count_distinct:<column>``
alongside stored columns, and that is what makes the population questions on
these tables expressible at all. "How many customers are in this segment", "what
share of the catalogue is out of stock" and "how many customers have lapsed" are
all **counts of rows at one instant**, not sums of a stored measure — and the
nearest summable column is always a plausible wrong answer (a segment's
``gross_ltv`` under a heading that says *customers*, off by a factor of the
average lifetime value).

A count is combined here as a LEVEL, for the same reason everything else on
these tables is: adding two days' counts counts an entity once per day it
existed. ``metric_kind`` cannot say so itself — a count projection names no
column, so there is nothing for it to classify — which is why ``_strategies_of``
answers for it rather than asking.

Counting a **flag** (out of stock, active) is a count and not a sum, and that is
not a stylistic preference: ``SUM(<boolean column>)`` comes back through
SQLAlchemy's Boolean result processor, so any number of out-of-stock products is
reported as ``True``, which is 1. See ``COUNT_WHERE_PREFIX`` for what is done
instead and why it needs nothing new from the repository.

``cohort_matrix``
-----------------
``agg_customer_cohort_monthly`` repeats ``cohort_size`` on every period row of a
cohort so that a single row is self-sufficient as a denominator. That makes it
the easiest number in the schema to double count: summing it over a cohort's
period rows multiplies the denominator by however many periods happen to be in
range, which *deflates* retention — the direction of error nobody investigates.
This resolver reads the table as what it is, a matrix of ``(cohort_month,
period_index)`` cells, and takes ``cohort_size`` **once per cohort**.

A cohort with ``cohort_size = 0`` gets ``retention_pct = None``, never ``0``.
Zero customers acquired has no retention rate, and a 0% cell would put a cohort
that does not exist on the heatmap as the worst performer on it.

Registration
------------
Both are registered into ``special.CUSTOM_FUNCTIONS`` rather than into
``RESOLVERS``. ``RESOLVERS`` is asserted to be exactly the ``ResolverId`` enum
(``test_analytics_resolvers.test_every_resolver_id_in_the_enum_is_registered``),
and adding a member to that enum is a change to ``types.py``, which this change
does not own. The ``custom`` dispatch table is the escape hatch that already
exists for a shape the enum does not name, and it is equally server-trusted: a
view reaches these with ``resolver=ResolverId.CUSTOM`` plus
``params={"fn": "snapshot"}``, both registry values, never request values.
Promoting them to first-class ``ResolverId`` entries later is a two-line change
and needs nothing here.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable, Mapping

# Two NAMES, not a query. `COUNT_ROWS` / `COUNT_DISTINCT_PREFIX` are the
# spellings the repository recognises for a count projection; importing them
# keeps this module from restating two magic strings that would then have to
# agree with the allowlist by hand. The layering rule (resolvers shape data, the
# repository runs SQL) is untouched — nothing here executes anything.
from app.repositories.analytics_repository import (
    COUNT_DISTINCT_PREFIX,
    COUNT_ROWS,
    HARD_ROW_CAP as REPOSITORY_ROW_CAP,
    columns_for,
)
from app.schemas.analytics_view import (
    AnalyticsWarning,
    KpiValue,
    SourceRef,
    TableBlock,
    WarningCode,
)
from app.services.analytics.export import clamp_row_limit
from app.services.analytics.filters import Granularity, ResolvedWindow
from app.services.analytics.metric_kind import assert_summable, combine_strategy
from app.services.analytics.resolvers.base import (
    ResolverContext,
    ResolverResult,
    SourceState,
    build_kpi,
    missing_kpi,
    probe_source,
    warn,
)
from app.services.analytics.resolvers.core import (
    DIMENSION_BINDINGS,
    METRIC_NOT_BOUND,
    MetricBinding,
    binding_for,
    compute_kpis,
    evaluate,
)
from app.services.analytics.resolvers.special import custom_function
from app.services.analytics.types import MetricQuality

__all__ = [
    "SNAPSHOT_SOURCE",
    "COHORT_SOURCE",
    "INVENTORY_SOURCE",
    "SNAPSHOT_BINDINGS",
    "INVENTORY_BINDINGS",
    "LEVEL_BINDINGS",
    "LEVEL_AT_INSTANT",
    "NOT_COMBINABLE",
    "DIMENSION_NOT_STORED",
    "SnapshotResolver",
    "CohortMatrixResolver",
    "latest_bucket_in",
]

#: The per-customer lifetime-state table. Every measure on it is a LEVEL —
#: `metric_kind.classify` says so from the source name alone, whatever the
#: column happens to be called.
SNAPSHOT_SOURCE = "agg_customer_snapshot"

#: The retention triangle. `cohort_size` is repeated per period row.
COHORT_SOURCE = "agg_customer_cohort_monthly"

#: The per-product end-of-day stock ledger. Unlike the customer snapshot it
#: mixes kinds: `stock_close` / `stock_value_close` / `is_oos` are the position
#: at the close of the day, `units_sold` / `units_restocked` are that day's
#: movement. The resolver therefore has to route column by column here, which is
#: exactly what `_partition` does.
INVENTORY_SOURCE = "agg_inventory_daily"

#: Emitted on every level answer, naming the instant it was measured at. A level
#: without its as-at date is not interpretable: "12,40,000 of LTV" over a 30-day
#: window means nothing until you know it is the position on one specific day
#: rather than something accumulated over thirty.
LEVEL_AT_INSTANT = "LEVEL_AT_INSTANT"

#: Emitted when a requested measure cannot be combined at all — a distinct count,
#: or a binding that mixes a level with a flow. Named rather than dropped: a card
#: that quietly disappears is a bug report nobody files.
NOT_COMBINABLE = "METRIC_NOT_COMBINABLE"

#: Emitted when a view asks to be grouped by a dimension the rollup it reads does
#: not carry — view 28 wants stock availability *by category*, and the inventory
#: ledger is per product with no category column anywhere on it. Deliberately its
#: own code rather than METRIC_NOT_BOUND: the measures are fine, it is the SPLIT
#: that cannot be done, and the fix is a different rollup rather than a binding.
#: `WarningCode` documents codes as plain strings precisely so a new one needs no
#: migration and no coordinated frontend release.
DIMENSION_NOT_STORED = "DIMENSION_NOT_STORED"

_HUNDRED = Decimal("100")
_PCT_Q = Decimal("0.0001")

#: Labels for the rollups this module reads, so the envelope's source footnote
#: says what it is rather than repeating the table name.
_LABELS: Mapping[str, str] = {
    SNAPSHOT_SOURCE: "Customer snapshots (per-customer lifetime state)",
    COHORT_SOURCE: "Monthly cohorts",
    INVENTORY_SOURCE: "Inventory ledger (per-product close of day)",
}

#: COUNT(DISTINCT customer_key) at the pinned instant. The argument goes through
#: the repository's allowlist exactly like any projected column, so this is as
#: safe as naming `customer_key` directly.
_CUSTOMERS = COUNT_DISTINCT_PREFIX + "customer_key"

#: "How many rows had this flag set", written as a projection name.
#:
#: **`SUM(<boolean column>)` is not an alternative, and the reason is a silent
#: wrong number.** ``func.sum()`` inherits its type from the column, so summing a
#: ``Boolean`` returns through the Boolean result processor: twelve products out
#: of stock comes back as ``True``, which is 1, and none comes back as ``False``,
#: which is 0. Nothing raises, the rate looks plausible, and no reader can check
#: it. That is fixable only inside the repository, which owns the SQL.
#:
#: What this does instead needs no cast and no new repository capability: the
#: flag becomes a **GROUP BY key**, where it is projected as a value rather than
#: aggregated (so no coercion), ``row_count`` counts each group, and the resolver
#: adds up the groups whose key is true. One read, and the arithmetic is a count
#: of rows throughout.
COUNT_WHERE_PREFIX = "count_where:"

#: Display measures the snapshot resolver can compute without a registry
#: binding, mirroring `special.FUNNEL_BINDINGS`. They live here rather than in a
#: view's `params` for a reason that is not cosmetic: every column named in
#: `params` is summed across the window by the generic resolvers, so a level in
#: a `params["metrics"]` block is a latent bug even when the view that declares
#: it happens to use this resolver. Keeping them here means a level column is
#: only ever reachable through the code path that pins an instant first.
#:
#: `lifetime_aov` is deliberately NOT called `aov`. The catalogue's `aov` is
#: order-basis (`paid_order_value / paid orders` on `agg_order_daily`); this one
#: is customer-basis. Two different figures under one label is how two screens
#: end up disagreeing, so they get two names.
SNAPSHOT_BINDINGS: Mapping[str, MetricBinding] = {
    "gross_ltv": MetricBinding(SNAPSHOT_SOURCE, add=("gross_ltv",)),
    "net_ltv": MetricBinding(SNAPSHOT_SOURCE, add=("net_ltv",)),
    "margin_ltv": MetricBinding(SNAPSHOT_SOURCE, add=("margin_ltv",)),
    "lifetime_orders": MetricBinding(SNAPSHOT_SOURCE, add=("orders_count",)),
    "lifetime_units": MetricBinding(SNAPSHOT_SOURCE, add=("units",)),
    "rfm_monetary": MetricBinding(SNAPSHOT_SOURCE, add=("monetary",)),
    "rfm_frequency": MetricBinding(SNAPSHOT_SOURCE, add=("frequency",)),
    # A count of the customers for whom the flag is true, at one instant.
    # Counted rather than summed: `SUM(is_active)` returns through SQLAlchemy's
    # Boolean processor and reports any number of active customers as `True`,
    # i.e. 1 (see COUNT_WHERE_PREFIX). It was written as a sum here, which was
    # wrong from the start and unreachable until a view asked for it.
    "active_customers": MetricBinding(
        SNAPSHOT_SOURCE, add=(COUNT_WHERE_PREFIX + "is_active",)
    ),
    # The cross-customer AOV `AggCustomerSnapshot` prescribes: SUM(gross_ltv) /
    # SUM(orders_count), recomputed from the two stored components. Averaging
    # the stored per-customer `aov` column would be an average of averages.
    "lifetime_aov": MetricBinding(
        SNAPSHOT_SOURCE, add=("gross_ltv",), over_add=("orders_count",)
    ),
    # -- counts of the population at the instant ---------------------------
    # How many customers, not how much they are worth. There is no column that
    # holds this, and the nearest summable one (`gross_ltv`) would report a
    # segment's MONEY under a heading that says people — a number wrong by the
    # average lifetime value and impossible to spot on a bar chart.
    "customers": MetricBinding(SNAPSHOT_SOURCE, add=(_CUSTOMERS,)),
    # Lapsed = in the population and not active. `is_active` is "ordered within
    # the activity window defined by the scoring job", which is exactly the
    # non-subscription definition of churn view 13 states in the registry: has
    # not ordered within the window. Expressed as a subtraction because there is
    # no "inactive" flag to count directly, and `row_count` is the matched
    # partner for it — both sides then count the same rows.
    "lapsed_customers": MetricBinding(
        SNAPSHOT_SOURCE, add=(COUNT_ROWS,), sub=(COUNT_WHERE_PREFIX + "is_active",)
    ),
    "lapse_rate": MetricBinding(
        SNAPSHOT_SOURCE,
        add=(COUNT_ROWS,),
        sub=(COUNT_WHERE_PREFIX + "is_active",),
        over_add=(COUNT_ROWS,),
        scale=_HUNDRED,
    ),
}

#: The same idea for the stock ledger. Only the POSITION measures are here; the
#: movements (`units_sold`, `units_restocked`) are flows and belong to
#: `core.TimeseriesResolver`, which sums them correctly — view 27 already binds
#: them there and must keep doing so.
#:
#: `stockout_rate` is the catalogue KPI restated in the columns that exist:
#: "COUNT(products) WHERE stock <= 0 / COUNT(products)", read at ONE instant,
#: which is what `kpis.stockout_rate` defines ("POINT IN TIME, not a period
#: rate"). Evaluated over a window instead, the identical expression would come
#: back as the time-weighted share of product-days out of stock — a different,
#: perfectly plausible figure that the catalogue says outright does not exist
#: yet, published under a defined label.
INVENTORY_BINDINGS: Mapping[str, MetricBinding] = {
    "stock": MetricBinding(INVENTORY_SOURCE, add=("stock_close",)),
    "out_of_stock": MetricBinding(
        INVENTORY_SOURCE, add=(COUNT_WHERE_PREFIX + "is_oos",)
    ),
    "stockout_rate": MetricBinding(
        INVENTORY_SOURCE,
        add=(COUNT_WHERE_PREFIX + "is_oos",),
        over_add=(COUNT_ROWS,),
        scale=_HUNDRED,
    ),
}

#: source -> the display bindings this module may compute on it. Looked up
#: before the catalogue so a level is only ever reachable through the code path
#: that pins an instant first.
LEVEL_BINDINGS: Mapping[str, Mapping[str, MetricBinding]] = {
    SNAPSHOT_SOURCE: SNAPSHOT_BINDINGS,
    INVENTORY_SOURCE: INVENTORY_BINDINGS,
}

#: Columns that are a POSITION at the close of a bucket, and whose NAME gives
#: `metric_kind.classify` nothing to go on.
#:
#: `classify` is name-based on purpose, and it is right about everything on
#: `agg_customer_snapshot` (the source name settles it) and about `stock_close`
#: (the `_close` suffix does). It is wrong about `is_oos`: "stock was zero at
#: close" is a closing state exactly like the balance beside it, but nothing in
#: the name says so, so it falls through to FLOW and would be summed across the
#: window — counting one product once per day it was out, and turning
#: `stockout_rate` into the time-weighted rate the KPI catalogue says does not
#: exist. `days_oos` and `reorder_gap` are the same shape (a running count and a
#: gap to a threshold, both stated as of this date) and are listed so the next
#: binding to reach for one gets the right rule rather than the plausible one.
#:
#: `tests/test_analytics_view_bindings.NON_ADDITIVE_COLUMNS` already lists these
#: three as non-additive on this rollup, so this is the same rule restated where
#: THIS resolver can act on it — not a new claim. The durable fix is a
#: `metric_kind._LEVEL_EXACT` entry, which is a change to a file this one does
#: not own.
_INSTANT_COLUMNS: Mapping[str, frozenset[str]] = {
    INVENTORY_SOURCE: frozenset({"is_oos", "days_oos", "reorder_gap"}),
}

#: KPI ids that look computable from a snapshot and are not, with the input that
#: is actually missing. Stated so the card names its gap instead of falling
#: through to the generic "no rollup binding", which reads like an oversight
#: rather than a limit of the stored data.
_UNCOMPUTABLE_ON_SNAPSHOT: Mapping[str, str] = {
    # LTV per customer needs COUNT(DISTINCT customer) as its denominator. That
    # was unavailable when this entry was written and no longer is — the
    # repository projects `count_distinct:<column>`, and this table's population
    # is exactly the catalogue's denominator (`jobs_customer` collects "every
    # customer with at least one revenue-status order"). It stays UNBOUND all
    # the same, because putting a number on a defined money card is a change to
    # what that card means rather than a wiring change, and the assertion that
    # pins the current answer lives in a test this change does not own
    # (`test_analytics_level_resolver.py`). Binding it is a one-line follow-up
    # there and here, to be made deliberately.
    #
    # `lifetime_aov` (SUM(gross_ltv)/SUM(orders_count)) is available and is a
    # DIFFERENT figure: per order, not per customer.
    "ltv": "count of customers as the denominator (not bound on this view)",
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _ratio(numerator: Any, denominator: Any, *, scale: Decimal = _HUNDRED) -> Decimal | None:
    """A rate, or None when the base is empty or absent.

    None rather than 0 in both cases. A cohort nobody joined has no retention
    rate; printing 0% claims everybody left.
    """
    if numerator is None or denominator in (None, 0):
        return None
    den = Decimal(str(denominator))
    if den == 0:
        return None
    return (Decimal(str(numerator)) / den * scale).quantize(_PCT_Q)


def latest_bucket_in(
    ctx: ResolverContext,
    source: str,
    window: ResolvedWindow,
    *,
    row_filters: dict | None = None,
) -> date | None:
    """The newest ``bucket_date`` this source holds INSIDE ``window``.

    This is "latest row per key in a window" expressed entirely through
    `AnalyticsRepository`: group by the bucket, order by it descending, take one.
    Pinning that date back onto a second read (`filters={"bucket_date": ...}`)
    then gives every key's row at that instant, because the rollup's UNIQUE key
    already guarantees one row per (bucket, key, generation). No window function
    and no bespoke SQL is needed, so nothing here bypasses the allowlist.

    Returns None when the window holds nothing. The caller must NOT widen the
    search: the newest bucket before the window would answer a question the
    caller did not ask, and the answer would look identical to one that did.
    """
    rows = ctx.repo.fetch_rollup(
        source,
        columns=["bucket_date"],
        window=window,
        tz_generation=ctx.tz_generation,
        group_by=["bucket_date"],
        order_by="-bucket_date",
        limit=1,
        filters=row_filters or None,
    )
    return rows[0]["bucket_date"] if rows else None


def _compare_window(window: ResolvedWindow) -> ResolvedWindow | None:
    if not window.has_comparison:
        return None
    return ResolvedWindow(
        date_from=window.compare_from,  # type: ignore[arg-type]
        date_to=window.compare_to,  # type: ignore[arg-type]
    )


def _first_sortable(binding: MetricBinding | None) -> str | None:
    """The first numerator column of `binding` the repository can ORDER BY.

    A count projection is skipped: it is not a column, so it is not in the
    allowlist that validates an ORDER BY, and naming it raises rather than
    sorting.
    """
    if binding is None:
        return None
    for column in binding.add:
        if not _is_count(column):
            return column
    return None


def _stores_dimension(source: str, column: str) -> bool:
    """Whether `source` actually has the column a dimension label maps to.

    `DIMENSION_BINDINGS` maps a label to (rollup, column), and a view may read a
    DIFFERENT rollup from the one the label was defined against — legitimately,
    since `product` is `product_id` on three of them. Checked rather than
    assumed because the failure mode is not a blank chart: an absent column
    raises inside the repository's allowlist, which is a 500 on a LIVE view.
    """
    return column in set(columns_for(source))


def _totals_at(
    ctx: ResolverContext,
    source: str,
    bindings: Mapping[str, MetricBinding],
    window: ResolvedWindow,
    *,
    filters: dict,
) -> Mapping[str, Any]:
    """One row of totals for `bindings`, with `filters` pinning a single bucket.

    ``fetch_totals`` wraps every requested name in ``SUM()`` and validates it as
    a stored *measure*, so it cannot carry a count projection — there is no
    column to sum. ``fetch_rollup`` grouped by ``bucket_date`` can, and with the
    bucket pinned in `filters` that group is a single day, so the row it returns
    is that day's totals and nothing else.

    Only used on the PINNED path. A binding containing a count never reaches the
    flow path (`_strategies_of` calls a count a level, and a count mixed with a
    flow is refused), so this cannot quietly collapse a multi-day result to its
    first row.
    """
    projections, flags = _plan(bindings)
    if not flags and not any(_is_count(column) for column in projections):
        return ctx.repo.fetch_totals(
            source,
            columns=projections,
            window=window,
            tz_generation=ctx.tz_generation,
            filters=filters or None,
        )
    rows = ctx.repo.fetch_rollup(
        source,
        columns=projections,
        window=window,
        tz_generation=ctx.tz_generation,
        group_by=["bucket_date", *flags],
        filters=filters or None,
    )
    if flags:
        rows = _fold_flags(rows, keys=["bucket_date"], flags=flags)
    # No row is "nothing was measured", which `evaluate` reports as missing
    # rather than as a zero — same as `fetch_totals` returning all-None.
    return rows[0] if rows else {}


def _is_count(name: str) -> bool:
    """True for a projection that counts ROWS rather than reading a column."""
    return (
        name == COUNT_ROWS
        or name.startswith(COUNT_DISTINCT_PREFIX)
        or name.startswith(COUNT_WHERE_PREFIX)
    )


def _flag_of(name: str) -> str | None:
    """The column a ``count_where:`` projection counts, or None."""
    if name.startswith(COUNT_WHERE_PREFIX):
        return name[len(COUNT_WHERE_PREFIX):]
    return None


def _plan(bindings: Mapping[str, MetricBinding]) -> tuple[list[str], list[str]]:
    """(projections to ask the repository for, flag columns to group by).

    A ``count_where:<flag>`` is not a projection the repository knows. It is
    turned into ``row_count`` plus a GROUP BY on ``<flag>``, which the resolver
    folds back afterwards — see `COUNT_WHERE_PREFIX` for why counting groups is
    the only honest way to total a boolean here.
    """
    projections: set[str] = set()
    flags: list[str] = []
    for binding in bindings.values():
        for column in binding.columns:
            flag = _flag_of(column)
            if flag is None:
                projections.add(column)
                continue
            if flag not in flags:
                flags.append(flag)
            projections.add(COUNT_ROWS)
    return sorted(projections), flags


def _fold_flags(rows: list[dict], *, keys: list[str], flags: list[str]) -> list[dict]:
    """Collapse the flag groups of each key back into one row.

    Grouping by a flag splits every key into (at most) a true row and a false
    row. This adds them back up, so a measure that never asked about the flag is
    unchanged, and adds one ``count_where:<flag>`` per flag holding the row count
    of the TRUE groups only.

    A key whose true group is absent gets 0, not a missing value: the group was
    read and held nothing, which is a measurement. The raw flag column is
    deliberately never carried through — a boolean that reached `evaluate` would
    be the coercion this whole detour exists to avoid.
    """
    folded: dict[tuple, dict] = {}
    for row in rows:
        key = tuple(row.get(k) for k in keys)
        acc = folded.get(key)
        if acc is None:
            acc = {k: row.get(k) for k in keys}
            for flag in flags:
                acc[COUNT_WHERE_PREFIX + flag] = Decimal("0")
            folded[key] = acc
        count = Decimal(str(row.get(COUNT_ROWS) or 0))
        for name, value in row.items():
            if name in keys or name in flags or value is None:
                continue
            acc[name] = Decimal(str(value)) + Decimal(str(acc.get(name, 0)))
        for flag in flags:
            if row.get(flag):
                acc[COUNT_WHERE_PREFIX + flag] += count
    return list(folded.values())


def _strategies_of(binding: MetricBinding) -> set[str]:
    """How each of this binding's columns may be combined across buckets.

    ``metric_kind`` owns the answer for a stored column — sum / latest /
    recompute / refuse — so the routing below asks rather than assuming, and a
    new rollup column gets the right treatment without an edit here.

    Two things it is not in a position to answer, both settled here:

    * A **count projection** names no column, so there is nothing for
      ``classify`` to read. Counting the rows a bucket holds is a position by
      construction — two days added together count an entity once per day it
      existed — so it combines as ``latest``, like every other level.
    * A column in ``_INSTANT_COLUMNS`` is a closing state that the name-based
      classifier reads as a flow. See that mapping for why each is listed.
    """
    strategies: set[str] = set()
    for column in binding.columns:
        if _is_count(column) or column in _INSTANT_COLUMNS.get(
            binding.source, frozenset()
        ):
            strategies.add("latest")
        else:
            strategies.add(combine_strategy(column, source=binding.source))
    return strategies


def _partition(
    bindings: Mapping[str, MetricBinding],
) -> tuple[dict[str, MetricBinding], dict[str, MetricBinding], list[str]]:
    """Split bindings into (pinned, summed, refused) by how they may combine.

    * **pinned** — every column is a LEVEL or a RATIO over levels. Its value is
      read at one instant.
    * **summed** — every column is a FLOW. Its value is the sum over the window
      or the bucket, exactly as everywhere else in this package.
    * **refused** — a DISTINCT count, which cannot be combined in either
      direction, or a binding MIXING a level with a flow. The latter has no
      single correct query: one query cannot pin an instant and span a window at
      once, and either choice silently mislabels half the answer.
    """
    pinned: dict[str, MetricBinding] = {}
    summed: dict[str, MetricBinding] = {}
    refused: list[str] = []
    for metric_id, binding in bindings.items():
        strategies = _strategies_of(binding)
        if "refuse" in strategies:
            refused.append(metric_id)
            continue
        at_instant = bool(strategies & {"latest", "recompute"})
        over_window = "sum" in strategies
        if at_instant and over_window:
            refused.append(metric_id)
        elif at_instant:
            pinned[metric_id] = binding
        else:
            summed[metric_id] = binding
    return pinned, summed, sorted(refused)


def _not_combinable(source: str, refused: list[str]) -> AnalyticsWarning:
    return warn(
        NOT_COMBINABLE,
        f"{', '.join(refused)} cannot be reported over this window: the binding "
        "either counts distinct things (which cannot be combined in either "
        "direction) or mixes a level with a flow (which cannot be pinned to an "
        "instant and summed over a window at the same time). Reported as "
        "unavailable rather than as a plausible wrong number.",
        severity="warn",
        metrics=refused,
        source=source,
    )


def _sum_rows(rows: list[dict]) -> dict[str, Any]:
    """Add a bucket's rows together, column by column, skipping the key."""
    totals: dict[str, Any] = {}
    for row in rows:
        for key, value in row.items():
            if key == "bucket_date" or value is None:
                continue
            totals[key] = Decimal(str(value)) + Decimal(str(totals.get(key, 0)))
    return totals


def _bucket_start(day: date, granularity: Granularity) -> date:
    if granularity is Granularity.WEEK:
        return day - timedelta(days=day.weekday())
    if granularity is Granularity.MONTH:
        return day.replace(day=1)
    return day


def _merge_sources(*groups: Iterable[SourceRef]) -> list[SourceRef]:
    out: list[SourceRef] = []
    seen: set[str] = set()
    for group in groups:
        for ref in group:
            if ref.id in seen:
                continue
            seen.add(ref.id)
            out.append(ref)
    return out


# ===========================================================================
# snapshot
# ===========================================================================


class SnapshotResolver:
    """Level-aware answers from a per-instant rollup.

    Three query shapes, all pinned to the same instant so the numbers on one
    screen are internally consistent:

    * **KPI card** — ``SUM(column) WHERE bucket_date = <latest in window>``,
      compared against the same expression at the latest bucket of the
      comparison window. Level against level.
    * **Breakdown** — the same sum, grouped by a dimension
      (``rfm_segment``, ``churn_risk_band``, ...). At one ``bucket_date`` each
      customer contributes exactly one row, so this is a partition of the
      population, not a re-count of it.
    * **Series** — one point per bucket that actually holds a snapshot, each
      point being that bucket's *latest* day. Gaps are left empty rather than
      zero-filled: a day with no snapshot is a day nobody measured, and a level
      of zero is a very specific claim (nobody has any lifetime value at all).

    A binding whose columns are FLOWS is still honoured — summed over the whole
    window, guarded by `metric_kind.assert_summable`, because that is what a
    flow's window value is. A binding that MIXES a level and a flow is refused:
    one query cannot both pin an instant and span a window, and picking either
    one silently mislabels half the answer.
    """

    id = "snapshot"

    def run(self, ctx: ResolverContext) -> ResolverResult:
        source = str(ctx.param("source") or SNAPSHOT_SOURCE)
        state = probe_source(ctx, source, label=_LABELS.get(source, source))
        warnings: list[AnalyticsWarning] = list(state.warnings)

        row_filters = dict(ctx.param("row_filters") or {})
        instant = latest_bucket_in(ctx, source, ctx.window, row_filters=row_filters)
        compare = _compare_window(ctx.window)
        previous_instant = (
            latest_bucket_in(ctx, source, compare, row_filters=row_filters)
            if compare is not None
            else None
        )

        if instant is None:
            # No bucket inside the window. Everything below would have to invent
            # its instant, so nothing below runs.
            return self._nothing_in_window(ctx, source, state, warnings)

        warnings.append(
            warn(
                LEVEL_AT_INSTANT,
                f"{source} holds a position, not a total. Every figure here is "
                f"measured at {instant.isoformat()}, the latest snapshot inside "
                "the window — it is not the sum of the window's days, which "
                "would report one customer's lifetime value once per day.",
                severity="info",
                source=source,
                as_at=instant.isoformat(),
                compared_at=previous_instant.isoformat() if previous_instant else None,
            )
        )

        kpis, kpi_sources, kpi_warnings = self._kpis(
            ctx, source, instant, previous_instant, row_filters
        )
        warnings.extend(kpi_warnings)

        series, tables, shape_warnings = self._shapes(
            ctx, source, state, instant, row_filters
        )
        warnings.extend(shape_warnings)

        return ResolverResult(
            kpis=kpis,
            series=series,
            tables=tables,
            sources=_merge_sources([state.ref], kpi_sources),
            warnings=warnings,
        ).rolled_up()

    # -- empty state -------------------------------------------------------

    def _nothing_in_window(
        self,
        ctx: ResolverContext,
        source: str,
        state: SourceState,
        warnings: list[AnalyticsWarning],
    ) -> ResolverResult:
        """No snapshot inside the window: say so, and reach back for nothing.

        KPIs bound to another rollup are still computed — they are a different
        measurement with a different window rule, and suppressing them would
        hide facts that are available. Everything bound to this source comes
        back null with the reason named.
        """
        if not any(w.code == WarningCode.NO_ROLLUP_YET for w in warnings):
            warnings.append(
                warn(
                    WarningCode.NO_ROLLUP_YET,
                    f"{source} holds no snapshot between "
                    f"{ctx.window.date_from.isoformat()} and "
                    f"{ctx.window.date_to.isoformat()}, so there is no instant to "
                    "report a level at. The newest snapshot BEFORE the window is "
                    "deliberately not used: it would answer a different question "
                    "and the answer would look the same.",
                    severity="warn",
                    source=source,
                    requested_from=ctx.window.date_from.isoformat(),
                    requested_to=ctx.window.date_to.isoformat(),
                )
            )

        kpis: dict[str, KpiValue] = {}
        mine, others = self._split_kpi_ids(ctx, source)
        for kpi_id in mine:
            kpis[kpi_id] = missing_kpi(
                kpi_id, f"no {source} row inside the requested window"
            )
        bundle = compute_kpis(ctx, others)
        kpis.update(bundle.kpis)

        return ResolverResult(
            kpis=kpis,
            sources=_merge_sources([state.ref], bundle.sources),
            warnings=[*warnings, *bundle.warnings],
            quality=MetricQuality.INCOMPLETE,
        ).rolled_up()

    # -- KPIs --------------------------------------------------------------

    def _split_kpi_ids(
        self, ctx: ResolverContext, source: str
    ) -> tuple[list[str], list[str]]:
        """Requested KPI ids split into "this snapshot's" and "somebody else's".

        A view may legitimately mix: a customer screen showing lifetime value at
        an instant next to orders placed over the window. The second half is
        delegated to `compute_kpis` unchanged, so a flow keeps its flow rule.
        """
        wanted = [k for k in (ctx.param("kpis") or ctx.view.kpis) if k]
        mine: list[str] = []
        others: list[str] = []
        for kpi_id in wanted:
            if kpi_id in _UNCOMPUTABLE_ON_SNAPSHOT:
                mine.append(kpi_id)
                continue
            binding = self._binding(ctx, kpi_id, source)
            if binding is not None and binding.source == source:
                mine.append(kpi_id)
            else:
                others.append(kpi_id)
        return mine, others

    @staticmethod
    def _binding(
        ctx: ResolverContext, metric_id: str, source: str
    ) -> MetricBinding | None:
        """Registry params first, then this module's level bindings, then the
        catalogue. The registry wins so a view can restate a measure; the level
        bindings come next so a level is never reachable through the generic
        summing path."""
        declared = ctx.params.get("metrics")
        if isinstance(declared, Mapping) and metric_id in declared:
            return binding_for(ctx, metric_id, default_source=source)
        for_source = LEVEL_BINDINGS.get(source)
        if for_source is not None and metric_id in for_source:
            return for_source[metric_id]
        return binding_for(ctx, metric_id, default_source=source)

    def _kpis(
        self,
        ctx: ResolverContext,
        source: str,
        instant: date,
        previous_instant: date | None,
        row_filters: dict,
    ) -> tuple[dict[str, KpiValue], list[SourceRef], list[AnalyticsWarning]]:
        kpis: dict[str, KpiValue] = {}
        warnings: list[AnalyticsWarning] = []
        mine, others = self._split_kpi_ids(ctx, source)

        bindings: dict[str, MetricBinding] = {}
        for kpi_id in mine:
            reason = _UNCOMPUTABLE_ON_SNAPSHOT.get(kpi_id)
            if reason:
                kpis[kpi_id] = missing_kpi(kpi_id, reason)
                continue
            binding = self._binding(ctx, kpi_id, source)
            if binding is None:  # pragma: no cover - _split only keeps bound ids
                kpis[kpi_id] = missing_kpi(kpi_id, "no rollup binding")
                continue
            bindings[kpi_id] = binding

        compare = _compare_window(ctx.window)
        values = self._evaluate_all(
            ctx, source, bindings, ctx.window, instant, row_filters, warnings
        )
        previous = (
            self._evaluate_all(
                ctx, source, bindings, compare, previous_instant, row_filters, []
            )
            if previous_instant is not None and compare is not None
            else {}
        )

        for kpi_id in bindings:
            outcome = values.get(kpi_id)
            if outcome is None:
                kpis[kpi_id] = missing_kpi(kpi_id, "not combinable over this window")
                continue
            prior = previous.get(kpi_id)
            kpis[kpi_id] = build_kpi(
                kpi_id,
                outcome.value,
                # Level against level: the comparison figure is the level at the
                # comparison window's own latest bucket, never a sum of its days.
                previous=prior.value if prior is not None else None,
                inputs_missing=outcome.inputs_missing,
                quality=outcome.quality,
            )

        bundle = compute_kpis(ctx, others)
        kpis.update(bundle.kpis)
        warnings.extend(bundle.warnings)
        return kpis, bundle.sources, warnings

    # -- evaluation --------------------------------------------------------

    def _evaluate_all(
        self,
        ctx: ResolverContext,
        source: str,
        bindings: Mapping[str, MetricBinding],
        window: ResolvedWindow,
        instant: date | None,
        row_filters: dict,
        warnings: list[AnalyticsWarning],
    ) -> dict[str, Any]:
        """Evaluate every binding, routing each by how it may be combined.

        Two reads at most: one pinned to `instant` for the levels and ratios,
        one over the whole of `window` for the flows. Mixed bindings are refused
        before either runs.

        ``window`` is passed rather than taken from ``ctx`` because this method
        answers for the comparison period as well, and its instant lies OUTSIDE
        the current window. Scoping the comparison read to the current window
        would return no rows, and a level with no rows is null — so every delta
        would silently disappear rather than being wrong, which is harder to
        notice than it sounds.
        """
        if not bindings or instant is None:
            return {}

        level_bindings, flow_bindings, refused = _partition(bindings)
        if refused:
            warnings.append(_not_combinable(source, refused))

        outcomes: dict[str, Any] = {}

        if level_bindings:
            totals = _totals_at(
                ctx,
                source,
                level_bindings,
                window,
                # The instant. Every column below is therefore aggregated across
                # the POPULATION on one day, never across days.
                filters={**row_filters, "bucket_date": instant},
            )
            for metric_id, binding in level_bindings.items():
                outcomes[metric_id] = evaluate(binding, totals)

        if flow_bindings:
            columns = sorted({c for b in flow_bindings.values() for c in b.columns})
            # Belt and braces: this is the only path in this module that adds
            # buckets together, so it is the only one that has to prove it may.
            # A level reaching here raises `NonAdditive` loudly instead of
            # returning a number that is wrong by the window length.
            for column in columns:
                assert_summable(column, source=source)
            totals = ctx.repo.fetch_totals(
                source,
                columns=columns,
                window=window,
                tz_generation=ctx.tz_generation,
                filters=row_filters or None,
            )
            for metric_id, binding in flow_bindings.items():
                outcomes[metric_id] = evaluate(binding, totals)

        return outcomes

    # -- series + tables ---------------------------------------------------

    def _display_bindings(
        self, ctx: ResolverContext, source: str, metric_ids: Iterable[str]
    ) -> tuple[dict[str, MetricBinding], list[str]]:
        bindings: dict[str, MetricBinding] = {}
        unbound: list[str] = []
        for metric_id in dict.fromkeys(m for m in metric_ids if m):
            binding = self._binding(ctx, metric_id, source)
            if binding is None or binding.source != source:
                unbound.append(metric_id)
                continue
            bindings[metric_id] = binding
        return bindings, sorted(unbound)

    def _shapes(
        self,
        ctx: ResolverContext,
        source: str,
        state: SourceState,
        instant: date,
        row_filters: dict,
    ) -> tuple[dict[str, list[dict]], dict[str, TableBlock], list[AnalyticsWarning]]:
        series: dict[str, list[dict]] = {}
        tables: dict[str, TableBlock] = {}
        warnings: list[AnalyticsWarning] = []

        dimension = self._dimension(ctx)
        dim_binding = DIMENSION_BINDINGS.get(dimension) if dimension else None
        if dim_binding is not None and not _stores_dimension(source, dim_binding.column):
            # The label is groupable *somewhere*, just not on the rollup this
            # view reads: view 28 asks for stock availability by category, and
            # the ledger is keyed by product with no category column on it. Say
            # so and drop the breakdown — grouping by an adjacent column would
            # answer a different question under this view's heading, and passing
            # the column through would raise inside the repository's allowlist.
            warnings.append(
                warn(
                    DIMENSION_NOT_STORED,
                    f"This view groups by {dimension!r}, which {source} does not "
                    f"carry ({dim_binding.column} is not one of its columns). The "
                    "breakdown is left out rather than regrouped by something "
                    "adjacent; showing it needs a rollup that holds both.",
                    severity="warn",
                    source=source,
                    dimension=dimension,
                    column=dim_binding.column,
                    view=ctx.view.slug,
                )
            )
            dim_binding = None

        for chart in ctx.view.charts or ():
            metric_ids = list(chart.series)
            bindings, unbound = self._display_bindings(ctx, source, metric_ids)
            if unbound:
                warnings.append(
                    warn(
                        METRIC_NOT_BOUND,
                        f"No series drawn for {', '.join(unbound)}: not stored in "
                        f"{source}. An absent line is honest; a flat zero is not.",
                        severity="warn",
                        metrics=unbound,
                        source=source,
                        chart=chart.id,
                    )
                )
            if not bindings:
                continue
            if chart.x in ("date", "week", "month"):
                points, drawn = self._level_series(
                    ctx, source, state, bindings, row_filters, warnings
                )
                if points:
                    series[chart.id] = [
                        # Only the metrics that were actually computed. A refused
                        # metric is left off the line entirely rather than carried
                        # as a column of nulls, which renders as a measured gap.
                        {chart.x: p["_x"], "as_at": p["as_at"],
                         **{k: p[k] for k in drawn}}
                        for p in points
                    ]
            elif dim_binding is not None:
                rows = self._breakdown(
                    ctx, source, dim_binding.column, dimension, bindings, instant,
                    row_filters, warnings,
                )
                if rows:
                    series[chart.id] = rows

        if dim_binding is not None and ctx.view.tables:
            spec = ctx.view.tables[0]
            metric_ids = [c.key for c in spec.columns]
            bindings, _unbound = self._display_bindings(ctx, source, metric_ids)
            if bindings:
                rows = self._breakdown(
                    ctx, source, dim_binding.column, dimension, bindings, instant,
                    row_filters, warnings,
                )
                if rows:
                    tables[spec.id] = TableBlock(
                        rows=rows, total_rows=len(rows), truncated=False
                    )
        return series, tables, warnings

    @staticmethod
    def _dimension(ctx: ResolverContext) -> str:
        """The grouping label: the request only when the view declares it."""
        requested = ctx.filters.dimension
        honoured = {f.value for f in ctx.view.filters}
        if requested and requested in honoured and requested in DIMENSION_BINDINGS:
            return requested
        declared = ctx.param("dimension")
        if declared:
            return str(declared)
        if ctx.view.charts:
            return ctx.view.charts[0].x
        return ""

    def _breakdown(
        self,
        ctx: ResolverContext,
        source: str,
        column: str,
        dimension: str,
        bindings: Mapping[str, MetricBinding],
        instant: date,
        row_filters: dict,
        warnings: list[AnalyticsWarning],
    ) -> list[dict]:
        """Group the population at ONE instant by a dimension.

        This is the sum the module docstring calls legitimate: at a single
        `bucket_date` every key appears exactly once, so grouping partitions the
        population instead of re-counting it. The `bucket_date` filter is what
        makes that true, and it is not optional — without it the same customer
        would be added to their segment once per day in range.

        Only PINNED measures appear here. A flow's value for a window is the sum
        of its days, which this shape cannot produce — pinned to one day it would
        report that day's flow under a window's label, understating it by
        roughly the window length. `core.BreakdownResolver` is the shape that
        splits a flow by a dimension, and it does it correctly.
        """
        level_only, over_window, refused = _partition(bindings)
        not_at_an_instant = sorted([*over_window, *refused])
        if not_at_an_instant:
            warnings.append(
                warn(
                    NOT_COMBINABLE,
                    f"{', '.join(not_at_an_instant)} is not shown in this "
                    f"{dimension} breakdown: it is a flow or a distinct count, "
                    "and this breakdown is a position at one instant. Reading a "
                    "single day's flow under a window's label would understate "
                    "it by roughly the length of the window.",
                    severity="warn",
                    metrics=not_at_an_instant,
                    source=source,
                )
            )
        if not level_only:
            return []

        # 200 on a screen, the export ceiling inside `export_scope()`.
        limit = clamp_row_limit(ctx.filters.limit)
        projections, flags = _plan(level_only)
        # A flag in the GROUP BY splits every key into at most a true row and a
        # false row, so the same number of KEYS needs 2**flags times the rows.
        # Capped at the repository's own ceiling, which raises rather than
        # truncating: a shortened breakdown loses whole segments silently.
        fetch = min((limit + 1) * (2 ** len(flags)), REPOSITORY_ROW_CAP)
        rows = ctx.repo.fetch_rollup(
            source,
            columns=projections,
            window=ctx.window,
            tz_generation=ctx.tz_generation,
            group_by=[column, *flags],
            filters={**row_filters, "bucket_date": instant},
            order_by=self._order_by(ctx, level_only, column, grouped_flags=bool(flags)),
            limit=fetch,
        )
        if flags:
            rows = _fold_flags(rows, keys=[column], flags=flags)
        truncated = len(rows) > limit
        if truncated:
            warnings.append(
                warn(
                    WarningCode.SMALL_SAMPLE,
                    f"Showing the top {limit} {dimension} values; the remainder is "
                    "not included in these rows.",
                    severity="info",
                    dimension=dimension,
                    limit=limit,
                )
            )
        return [
            {
                dimension: row.get(column),
                "as_at": instant.isoformat(),
                **{
                    metric_id: evaluate(binding, row).value
                    for metric_id, binding in level_only.items()
                },
            }
            for row in rows[:limit]
        ]

    @staticmethod
    def _order_by(
        ctx: ResolverContext,
        bindings: Mapping[str, MetricBinding],
        dimension_column: str,
        *,
        grouped_flags: bool = False,
    ) -> str:
        """A validated ORDER BY for the breakdown.

        ``grouped_flags`` forces the key: with a flag in the GROUP BY the
        database ranks (key, flag) pairs, so a top-N by a measure would cut the
        list halfway through a key and rank on half its rows.

        Three cases, in order:

        * ``params["breakdown_sort"]`` names the display measure to rank by,
          ``-`` prefixed for descending. It exists for the view whose "worst
          first" is not "biggest first" — view 29's low-stock table wants the
          emptiest shelf at the top, and a top-N by stock DESCENDING under a
          heading that says "Needs attention" is precisely backwards.
        * Otherwise the first display measure, descending: a breakdown is a
          top-N by its primary measure, which is what this always did.
        * Otherwise the grouping key itself. A measure that is a COUNT
          projection cannot be ordered on — the repository's allowlist covers
          stored columns, and a count is not one — so a breakdown whose only
          measure is a count (customers per segment) falls back to the key. That
          is deterministic and explainable; NO order by would let MySQL return a
          different top-N for the same request, which is how a segment
          disappears from one refresh to the next.
        """
        if grouped_flags:
            return dimension_column
        requested = str(ctx.param("breakdown_sort") or "")
        if requested:
            direction = "-" if requested.startswith("-") else ""
            column = _first_sortable(bindings.get(requested.lstrip("-")))
            # A named sort that cannot be honoured falls through to the key
            # rather than to a different measure: silently ranking by something
            # else is the failure this branch exists to avoid.
            return f"{direction}{column}" if column else dimension_column
        for binding in bindings.values():
            column = _first_sortable(binding)
            if column:
                return f"-{column}"
        return dimension_column

    def _level_series(
        self,
        ctx: ResolverContext,
        source: str,
        state: SourceState,
        bindings: Mapping[str, MetricBinding],
        row_filters: dict,
        warnings: list[AnalyticsWarning],
    ) -> tuple[list[dict], list[str]]:
        """One point per bucket that actually holds data. Returns (points, drawn).

        Each bucket's rows are read once and combined **per metric kind**, which
        is the whole difference from `core.dense_points`:

        * A **level** takes the bucket's LATEST day. Re-bucketing a level to a
          week by adding seven days together is the same error as summing a
          window, one order of magnitude smaller and just as invisible.
        * A **flow** takes the bucket's SUM, guarded by `assert_summable` — this
          and the KPI path are the only places in this module that add buckets
          together, so they are the only ones that have to prove they may.
        * **Gaps are not filled.** In a flow series a missing day inside the
          covered range is a measured zero, and `dense_points` rightly writes
          one. In a level series it is a day nobody snapshotted, and a 0 there
          claims the entire customer base momentarily had no lifetime value.
        """
        covered = state.covered_through(ctx.window)
        if covered is None:
            return [], []

        pinned, summed, refused = _partition(bindings)
        if refused:
            warnings.append(_not_combinable(source, refused))
        if not (pinned or summed):
            return [], []
        for binding in summed.values():
            for column in binding.columns:
                assert_summable(column, source=source)

        drawn = [m for m in bindings if m in pinned or m in summed]
        projections, flags = _plan({m: bindings[m] for m in drawn})
        rows = ctx.repo.fetch_rollup(
            source,
            columns=projections,
            window=ctx.window,
            tz_generation=ctx.tz_generation,
            group_by=["bucket_date", *flags],
            filters=row_filters or None,
        )
        if flags:
            # Back to one row per day before anything is bucketed, so a week
            # still takes its LAST day rather than its last flag group.
            rows = _fold_flags(rows, keys=["bucket_date"], flags=flags)
        granularity = ctx.filters.granularity
        if granularity is Granularity.HOUR:
            # There is no hourly snapshot; a level is written once a day.
            granularity = Granularity.DAY

        buckets: dict[date, list[dict]] = {}
        for row in rows:
            day = row["bucket_date"]
            if day > covered:
                continue
            buckets.setdefault(_bucket_start(day, granularity), []).append(row)

        points: list[dict] = []
        for bucket, bucket_rows in sorted(buckets.items()):
            latest = max(bucket_rows, key=lambda r: r["bucket_date"])
            point: dict[str, Any] = {
                "_x": bucket.isoformat(),
                # The day the levels on this point were measured. For a daily
                # bucket it equals `_x`; for a week it is the day the position
                # was actually read, which is the only honest label for it.
                "as_at": latest["bucket_date"].isoformat(),
            }
            for metric_id, binding in pinned.items():
                point[metric_id] = evaluate(binding, latest).value
            if summed:
                totals = _sum_rows(bucket_rows)
                for metric_id, binding in summed.items():
                    point[metric_id] = evaluate(binding, totals).value
            points.append(point)
        return points, drawn


# ===========================================================================
# cohort_matrix
# ===========================================================================


class CohortMatrixResolver:
    """The retention triangle, with the denominator counted once per cohort.

    Read as a matrix rather than as a list of rows. Every cell is one
    ``(cohort_month, period_index)`` pair; ``cohort_size`` belongs to the
    *cohort*, not to the cell, and is therefore taken once per cohort however
    many cells that cohort has in range.

    The rows are fetched with **no GROUP BY**, which is not a detail: without a
    grouping key the repository projects columns straight through instead of
    wrapping them in ``SUM()``, so there is no code path in which the repeated
    denominator can be added to itself. ``(cohort_month, period_index,
    tz_generation)`` is already the table's UNIQUE key, so one row per cell is
    exactly what comes back.

    Where sums DO happen they are across cohorts at a fixed ``period_index``,
    never across periods. A customer belongs to exactly one cohort, so cohorts
    at the same period index are disjoint sets and their sum is a real total;
    the same customer appears in *every* period row of their own cohort, which
    is what makes summing along the other axis a multiplication.
    """

    id = "cohort_matrix"

    def run(self, ctx: ResolverContext) -> ResolverResult:
        source = str(ctx.param("source") or COHORT_SOURCE)
        state = probe_source(ctx, source, label=_LABELS.get(source, source))
        warnings: list[AnalyticsWarning] = list(state.warnings)

        bundle = compute_kpis(ctx, ctx.view.kpis)
        warnings.extend(bundle.warnings)

        series: dict[str, list[dict]] = {}
        tables: dict[str, TableBlock] = {}

        cells = self._cells(ctx, source) if state.has_rows else []
        if cells:
            sizes, inconsistent = self._cohort_sizes(cells)
            if inconsistent:
                warnings.append(
                    warn(
                        NOT_COMBINABLE,
                        "cohort_size disagrees between period rows of the same "
                        f"cohort ({', '.join(sorted(inconsistent))}). It is "
                        "constant by definition, so this is an aggregation bug; "
                        "the largest value is used and the disagreement is "
                        "reported rather than averaged away.",
                        severity="error",
                        cohorts=sorted(inconsistent),
                        source=source,
                    )
                )

            grid = self._grid(cells, sizes)
            tables[self._table_id(ctx)] = TableBlock(
                rows=grid, total_rows=len(grid), truncated=False
            )
            series.update(self._curve(ctx, grid))

            empty_cohorts = sorted(m for m, size in sizes.items() if size <= 0)
            if empty_cohorts:
                warnings.append(
                    warn(
                        WarningCode.SMALL_SAMPLE,
                        f"{len(empty_cohorts)} cohort(s) have a size of zero "
                        f"({', '.join(empty_cohorts)}); their retention is null, "
                        "not 0%. A cohort nobody joined has no retention rate, and "
                        "0% would rank a cohort that does not exist as the worst "
                        "one on the heatmap.",
                        severity="info",
                        cohorts=empty_cohorts,
                    )
                )
            if len(sizes) < 3:
                warnings.append(
                    warn(
                        WarningCode.SMALL_SAMPLE,
                        "Fewer than three cohorts are in range; a retention curve "
                        "over one or two months is a description, not a trend.",
                        severity="info",
                        cohorts=len(sizes),
                    )
                )

        return ResolverResult(
            kpis=bundle.kpis,
            series=series,
            tables=tables,
            sources=_merge_sources([state.ref], bundle.sources),
            warnings=warnings,
        ).rolled_up()

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _table_id(ctx: ResolverContext) -> str:
        return ctx.view.tables[0].id if ctx.view.tables else "cohort_grid"

    @staticmethod
    def _cells(ctx: ResolverContext, source: str) -> list[dict]:
        """Raw cells, projected rather than aggregated. See the class docstring.

        No `limit` is passed, so the repository applies its own cap and RAISES
        past it instead of truncating — a silently shortened matrix would drop
        cells out of a heatmap with nothing to show for it.
        """
        return ctx.repo.fetch_rollup(
            source,
            columns=[
                "cohort_month",
                "period_index",
                "cohort_size",
                "active_customers",
                "orders",
                "revenue",
            ],
            window=ctx.window,
            tz_generation=ctx.tz_generation,
            filters=ctx.param("row_filters") or None,
        )

    @staticmethod
    def _cohort_sizes(cells: list[dict]) -> tuple[dict[str, int], set[str]]:
        """``cohort_month -> size``, taken ONCE per cohort.

        Not a sum. The second return value names any cohort whose rows disagree,
        which cannot happen if the aggregation job is correct and is worth
        shouting about if it ever does.
        """
        sizes: dict[str, int] = {}
        inconsistent: set[str] = set()
        for cell in cells:
            month = str(cell["cohort_month"])
            size = int(cell["cohort_size"] or 0)
            if month in sizes and sizes[month] != size:
                inconsistent.add(month)
                sizes[month] = max(sizes[month], size)
            else:
                sizes[month] = size
        return sizes, inconsistent

    @staticmethod
    def _grid(cells: list[dict], sizes: Mapping[str, int]) -> list[dict]:
        rows = []
        for cell in sorted(
            cells, key=lambda c: (str(c["cohort_month"]), int(c["period_index"]))
        ):
            month = str(cell["cohort_month"])
            size = sizes.get(month, 0)
            active = int(cell["active_customers"] or 0)
            rows.append(
                {
                    "cohort_month": month,
                    "period_index": int(cell["period_index"]),
                    # The cohort's size, not the cell's copy summed with anything.
                    "cohort_size": size,
                    "active_customers": active,
                    "orders": int(cell["orders"] or 0),
                    "revenue": cell["revenue"],
                    # None, never 0, when the cohort is empty.
                    "retention_pct": _ratio(active, size),
                }
            )
        return rows

    @staticmethod
    def _curve(ctx: ResolverContext, grid: list[dict]) -> dict[str, list[dict]]:
        """Retention by months-since-acquisition, pooled across cohorts.

        Pooled by summing the two components and dividing once, never by
        averaging each cohort's percentage — an unweighted mean lets a
        4-customer cohort count as much as a 4,000-customer one. Each cohort
        contributes to a given `period_index` at most once, so the denominator
        counts every cohort exactly once here too.
        """
        pooled: dict[int, list[int]] = {}
        for row in grid:
            acc = pooled.setdefault(row["period_index"], [0, 0])
            acc[0] += row["active_customers"]
            acc[1] += row["cohort_size"]
        points = [
            {
                "month": index,
                "retention_pct": _ratio(active, size),
                "cohort_size": size,
                "active_customers": active,
            }
            for index, (active, size) in sorted(pooled.items())
        ]
        charts = [c for c in ctx.view.charts if c.x in ("month", "period_index")]
        if charts:
            return {charts[0].id: points}
        return {"retention_curve": points}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
# Into the `custom` dispatch table, not `RESOLVERS` — see the module docstring.
# Both are still full `Resolver`-protocol objects (an `id` and a `run`), so
# promoting them later needs no change here.

SNAPSHOT_RESOLVER = SnapshotResolver()
COHORT_MATRIX_RESOLVER = CohortMatrixResolver()

custom_function(SNAPSHOT_RESOLVER.id)(SNAPSHOT_RESOLVER.run)
custom_function(COHORT_MATRIX_RESOLVER.id)(COHORT_MATRIX_RESOLVER.run)
