"""The cross-sell resolver — view 57, read side of ``agg_basket_pair_daily``.

Registers one custom function, ``basket_cross_sell``, reached from the registry
with ``resolver=ResolverId.CUSTOM`` and ``params={"fn": "basket_cross_sell"}``.

It exists as a custom function rather than as a ``TABLE`` binding for one
reason that is not negotiable: **the rollup stores counts and the view shows
ratios**. ``TableResolver`` can project and SUM stored columns, and there is no
stored ``lift`` column to project — deliberately, because a stored daily lift
cannot be re-bucketed into a week. Everything below is the arithmetic that turns
four summed counts into support, confidence and lift at the moment the window is
known.

The ratios, and the population they describe
--------------------------------------------
For one pair, over the rows it has in the window::

    support     = Σ pair_orders / Σ total_orders_in_bucket
    confidence  = Σ pair_orders / Σ orders_with_a
    lift        = (Σ pair_orders × Σ total_orders_in_bucket)
                  / (Σ orders_with_a × Σ orders_with_b)

Every sum is taken over **the same rows** — the days on which that pair
co-occurred — so numerator and denominator always describe one population. That
is what makes the figure internally consistent, and it is also the honest
statement of what it is: a pair seen on one day of a thirty-day window is
described by that day, and ``days_observed`` is on the table so the reader can
see it rather than infer a month of evidence that does not exist.

Mixing a window-wide order count into a per-pair marginal would look more
"correct" and would not be: the marginals are only recoverable for the days the
pair has rows, so the denominator would cover thirty days while the numerator
covered one, and lift would be inflated by roughly the ratio between them.

The attach rate is a different shape and is computed differently
----------------------------------------------------------------
``total_orders_in_bucket`` and ``orders_with_any_pair`` are bucket-level scalars
repeated on every pair row of their day, so summing them across a *window* the
naive way multiplies each day by its pair count. The window totals are obtained
instead by grouping on ``bucket_date`` alone, projecting the sums **and** the
row count, and dividing the two — see ``_per_bucket``. The denominator is named
in the projection rather than implied by the grouping, which is both exact and
auditable, and it keeps every ``group_by`` key a genuine dimension.

Nothing in this module ever groups by a MEASURE. ``group_by`` means "dimension"
throughout this subsystem — the repository's grain, ``metric_kind``, and the
export test double all encode it — and a measure placed there is a category
error even when the number that comes back is right.

SMALL_SAMPLE
------------
A lift of 40 computed from two co-occurrences is noise wearing a decimal point,
and showing it as a merchandising recommendation is worse than showing nothing —
somebody will build a bundle out of it. Pairs below
``SMALL_SAMPLE_MIN_PAIR_ORDERS`` are therefore ranked BELOW every pair that
clears it, flagged per row, and named in a ``SMALL_SAMPLE`` warning. They are
not deleted: a pair with two orders is a real observation and hiding it would
make the table look like the whole population. It is demoted and labelled.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.schemas.analytics_view import TableBlock, WarningCode
from app.services.analytics.export import clamp_row_limit
from app.services.analytics.resolvers.base import (
    ResolverContext,
    ResolverResult,
    build_kpi,
    not_configured,
    probe_source,
    warn,
)
from app.services.analytics.resolvers.special import custom_function
from app.services.analytics.types import MetricQuality

__all__ = [
    "BASKET_SOURCE",
    "SMALL_SAMPLE_MIN_PAIR_ORDERS",
    "CANDIDATE_PAIR_LIMIT",
    "basket_cross_sell",
]

#: The rollup this view reads. Named once.
BASKET_SOURCE = "agg_basket_pair_daily"

#: Co-occurrences below which a pair's lift is not reportable as a
#: recommendation.
#:
#: Five, and the reasoning is about the width of the interval rather than about
#: taste. Pair counts are approximately Poisson, so the relative standard error
#: of an observed count n is about 1/√n: 71% at n=2, 45% at n=5, 32% at n=10.
#: Lift divides that count by two marginals which carry their own error, so at
#: n=2 the plausible range of the lift spans well over an order of magnitude —
#: a "lift of 40" and a "lift of 3" are the same observation. At n=5 the figure
#: is still rough but it is directional, which is what a merchandising decision
#: actually needs.
#:
#: Below the threshold the row is kept, ranked last and flagged, and the view
#: warns. Removing them would be worse: the table would then look like the whole
#: population when it is the confident subset of it.
SMALL_SAMPLE_MIN_PAIR_ORDERS = 5

#: How many pairs are pulled from the database (ordered by co-occurrence count)
#: before they are re-ranked by lift in Python.
#:
#: Lift cannot be an ORDER BY: it is not a stored column, and it must not
#: become one. So the candidate set is the top N pairs by support, which is the
#: one ordering the database CAN do, and the lift ranking happens over that set.
#: The cut is safe in the direction that matters — anything excluded had fewer
#: co-occurrences than 500 other pairs in the window, which puts it far below
#: SMALL_SAMPLE_MIN_PAIR_ORDERS on any real catalogue, i.e. in the band this
#: view refuses to recommend from anyway. `truncated` is set when the cut bites.
CANDIDATE_PAIR_LIMIT = 500

#: Pairs the "Most frequent pairs" chart shows. A horizontal bar chart with
#: fifty bars is not a chart.
TOP_PAIRS_CHART_LIMIT = 12

#: Table sort keys a client may ask for. Not the raw column names: `lift` and
#: `support` are computed here and exist in no table, so the repository's
#: allowlist cannot validate them and this is where the equivalent check lives.
_SORTABLE = frozenset({"orders", "lift", "support", "confidence", "days_observed"})

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
#: Four places on a ratio, matching the RATIO format used elsewhere; two on a
#: percentage. Quantised rather than left as a full-precision Decimal so two
#: rows that are equal display as equal.
_RATIO_PLACES = Decimal("0.0001")
_PCT_PLACES = Decimal("0.0001")


def _int(value: Any) -> int:
    """A count, as an int. Deliberately STRICT about what it accepts.

    It is only ever handed a projected measure (a count the repository SUMmed)
    or a grain column the schema declares `Integer NOT NULL`. Neither can be a
    label, so a non-numeric value here means a caller passed a DIMENSION where a
    measure belongs — a category error that would otherwise surface as a
    plausible zero rather than as an exception. Widening this to swallow
    `"P00000"` would hide exactly the bug it caught.
    """
    return int(value or 0)


def _per_bucket(summed: Any, rows_in_bucket: Any) -> int:
    """Recover a BUCKET-LEVEL SCALAR from its sum over that bucket's rows.

    `total_orders_in_bucket`, `orders_with_any_pair` and
    `orders_skipped_over_cap` are measures whose value is CONSTANT across every
    pair row of their day. The repository SUMs measures, so grouping by
    `bucket_date` alone returns the value multiplied by the number of pairs that
    day produced; dividing by that same row count gives the value back exactly.

    The denominator is named rather than implied, which is the rule the
    repository states for exactly this shape: "project the SUM and divide by a
    denominator it names itself. An implicit denominator is exactly what makes
    the wrong version invisible." (`_avg`'s docstring, which refuses
    `avg:<flow>` for the same reason — these columns classify as FLOW, so the
    `avg:` projection is correctly closed to them.)

    An earlier version put the scalars in `group_by` to make them GROUP BY keys
    instead of SUM targets. That returned the right number against MySQL and was
    the wrong shape: `group_by` means "dimension" everywhere else in this
    subsystem — the repository's grain, `metric_kind`, and the export test
    double all encode it — so it made three measures masquerade as dimensions.

    Rounds rather than truncates. Exact whenever the constant-per-bucket
    invariant holds, which is by construction; a fractional result would mean it
    had broken, and rounding keeps that a small visible error rather than an
    off-by-one on every bucket.
    """
    rows = _int(rows_in_bucket)
    if rows <= 0:
        return 0
    return int((Decimal(_int(summed)) / Decimal(rows)).to_integral_value())


def _ratio(numerator: int, denominator: int, places: Decimal) -> Decimal | None:
    """A ratio, or None when it is undefined. Never 0 for a zero denominator.

    A zero denominator means "no baskets to divide by", which is not the same
    statement as "the ratio is zero" — and only one of the two is safe to draw.
    """
    if denominator <= 0:
        return None
    return (Decimal(numerator) / Decimal(denominator)).quantize(places)


@custom_function("basket_cross_sell")
def basket_cross_sell(ctx: ResolverContext) -> ResolverResult:
    """Frequently-bought-together pairs, ranked by lift with support as tiebreak.

    Ranking by lift alone puts two obscure products that happened to meet twice
    at the top of a merchandising page. Ranking by support alone just repeats
    the bestseller list — the two most popular products co-occur most often
    whether or not there is any affinity between them. Lift with support as the
    tiebreak, plus the SMALL_SAMPLE floor, is what makes the ordering say
    "unusually often, and often enough to believe".
    """
    source = str(ctx.param("source") or BASKET_SOURCE)
    state = probe_source(ctx, source, label="Basket pair rollup (daily)")
    warnings = list(state.warnings)
    spec = ctx.view.tables[0] if ctx.view.tables else None
    if spec is None:  # pragma: no cover - registry declares the table
        return not_configured(
            f"View {ctx.view.slug!r} declares no table for the basket pairs to "
            "fill, so there is nothing to render them into.",
            requires=["registry:tables"],
            detail={"view": ctx.view.slug},
        )

    if not state.has_rows:
        # No rows anywhere for this generation. Return the provenance and the
        # freshness warnings and NOTHING else — a table of zeros here would read
        # as "nothing is ever bought together", which is a measurement, and this
        # is the absence of one.
        return ResolverResult(
            tables={spec.id: TableBlock(rows=[], total_rows=0, truncated=False)},
            sources=[state.ref],
            warnings=warnings,
            quality=MetricQuality.INCOMPLETE,
        ).rolled_up()

    totals = _window_totals(ctx, source)
    pairs = _pair_rows(ctx, source)

    rows = [_describe(pair) for pair in pairs]
    below = [row for row in rows if row["_small_sample"]]
    if below:
        warnings.append(
            warn(
                WarningCode.SMALL_SAMPLE,
                f"{len(below)} of {len(rows)} pairs were bought together fewer than "
                f"{SMALL_SAMPLE_MIN_PAIR_ORDERS} times in this window. Their lift is "
                "computed from a handful of baskets and swings by an order of "
                "magnitude on one more order, so they are ranked last and marked "
                "rather than presented as recommendations.",
                severity="warn",
                threshold=SMALL_SAMPLE_MIN_PAIR_ORDERS,
                pairs_below_threshold=len(below),
                pairs_total=len(rows),
            )
        )
    if totals["skipped"]:
        warnings.append(
            warn(
                WarningCode.SMALL_SAMPLE,
                f"{totals['skipped']} order(s) in this window held more than the "
                "basket-size cap in distinct products and were excluded from the "
                "co-occurrence counts entirely — pairs, marginals and denominator "
                "alike — so every figure here describes the remaining "
                f"{totals['orders']} order(s) and no more.",
                severity="info",
                orders_skipped=totals["skipped"],
                orders_counted=totals["orders"],
            )
        )

    sort_key, descending = _sort(ctx, warnings)
    rows.sort(key=lambda row: _sort_value(row, sort_key), reverse=descending)
    # The small-sample floor outranks any requested sort: a demoted row must
    # stay demoted, or the sort silently undoes the one guard on this page.
    rows.sort(key=lambda row: row["_small_sample"])

    limit = clamp_row_limit(ctx.filters.limit, default=spec.page_size or 25)
    offset = int(ctx.filters.offset or 0)
    page = rows[offset : offset + limit]
    truncated = len(rows) > offset + limit or len(pairs) >= CANDIDATE_PAIR_LIMIT

    block = TableBlock(
        rows=[{k: v for k, v in row.items() if not k.startswith("_")} for row in page],
        total_rows=len(page),
        truncated=truncated,
    )

    return ResolverResult(
        kpis=_kpis(ctx, rows, totals),
        series=_series(ctx, rows),
        tables={spec.id: block},
        sources=[state.ref],
        warnings=warnings,
        # Counted from the immutable line fact, with no allocation and no
        # estimate anywhere in the chain.
        quality=MetricQuality.AUTHORITATIVE,
    ).rolled_up()


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _pair_rows(ctx: ResolverContext, source: str) -> list[dict]:
    """One row per pair in the window, with every count already summed.

    `product_a_name` / `product_b_name` are GROUP BY keys rather than projected
    columns because they are strings: grouping on them means a product renamed
    mid-window splits into two rows under its two names, which is the honest
    rendering of what the snapshots actually say and is visible, rather than one
    row silently carrying whichever name the database happened to pick.

    `row_count` is the number of DAYS the pair co-occurred — the population every
    ratio below is computed over, and the number that distinguishes "twenty
    baskets across a month" from "twenty baskets on Diwali".
    """
    return ctx.repo.fetch_rollup(
        source,
        columns=[
            "pair_orders",
            "orders_with_a",
            "orders_with_b",
            "total_orders_in_bucket",
            "row_count",
        ],
        window=ctx.window,
        tz_generation=ctx.tz_generation,
        group_by=["product_a_id", "product_b_id", "product_a_name", "product_b_name"],
        order_by="-pair_orders",
        limit=CANDIDATE_PAIR_LIMIT,
    )


#: The three bucket-level scalars, and the only columns `_per_bucket` applies to.
_BUCKET_SCALARS = (
    "total_orders_in_bucket",
    "orders_with_any_pair",
    "orders_skipped_over_cap",
)

#: Every column this resolver ever puts in a `group_by`. All of them are part of
#: `agg_basket_pair_daily`'s GRAIN — the primary key plus the UNIQUE key, which
#: is what the repository classifies as a dimension — so none of them is ever
#: SUMmed and none of them is a measure wearing a dimension's clothes.
#: `test_analytics_basket_rollup.py` asserts this set against
#: `measures_for()` so the category error cannot come back.
GROUPABLE_COLUMNS = frozenset(
    {"bucket_date", "product_a_id", "product_b_id", "product_a_name", "product_b_name"}
)


def _window_totals(ctx: ResolverContext, source: str) -> dict[str, int]:
    """Window-wide order counts, counted once per DAY rather than once per row.

    `total_orders_in_bucket`, `orders_with_any_pair` and
    `orders_skipped_over_cap` are bucket-level scalars repeated on every pair row
    of their day. Summing them straight would multiply each day by the number of
    pairs it produced — a store with 40 orders and 300 pairs would report 12 000
    orders, and the number would look like a good month.

    So the query groups by `bucket_date` ALONE — the only dimension involved —
    projects the sums and the row count together, and `_per_bucket` divides one
    by the other to recover each day's constant. The denominator is named in the
    projection rather than implied by the grouping, which is what makes it
    auditable; see `_per_bucket` for why the earlier "group by the scalar" form
    was the wrong shape even though it returned the right number.
    """
    per_day = ctx.repo.fetch_rollup(
        source,
        columns=["row_count", *_BUCKET_SCALARS],
        window=ctx.window,
        tz_generation=ctx.tz_generation,
        group_by=["bucket_date"],
    )
    totals = {
        "orders": 0,
        "multi_item_orders": 0,
        "skipped": 0,
        "days": len(per_day),
    }
    for row in per_day:
        rows_in_bucket = row["row_count"]
        totals["orders"] += _per_bucket(row["total_orders_in_bucket"], rows_in_bucket)
        totals["multi_item_orders"] += _per_bucket(
            row["orders_with_any_pair"], rows_in_bucket
        )
        totals["skipped"] += _per_bucket(
            row["orders_skipped_over_cap"], rows_in_bucket
        )
    return totals


# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------


def _describe(pair: dict) -> dict[str, Any]:
    """Turn one pair's summed counts into the row the table renders.

    This is the entire reason the rollup stores counts. `support`, `confidence`
    and `lift` are computed here, from sums taken over the same rows, at the
    moment the window is known — never read from a column, because a stored
    daily ratio is not re-bucketable and a weekly average of daily lifts is a
    different and wrong number.
    """
    together = _int(pair["pair_orders"])
    with_a = _int(pair["orders_with_a"])
    with_b = _int(pair["orders_with_b"])
    baskets = _int(pair["total_orders_in_bucket"])

    lift: Decimal | None = None
    if with_a > 0 and with_b > 0 and baskets > 0:
        # lift = P(A∩B) / (P(A)·P(B)), with every probability over the SAME
        # denominator, which cancels to (together × baskets) / (with_a × with_b).
        # Written as one expression so the two denominators cannot drift apart.
        lift = (
            Decimal(together) * Decimal(baskets) / (Decimal(with_a) * Decimal(with_b))
        ).quantize(_RATIO_PLACES)

    return {
        "product_a": pair["product_a_name"],
        "product_b": pair["product_b_name"],
        "orders": together,
        "days_observed": _int(pair["row_count"]),
        "support": _pct(_ratio(together, baskets, _PCT_PLACES)),
        "confidence": _pct(_ratio(together, with_a, _PCT_PLACES)),
        "lift": lift,
        # Underscored keys are working state and are stripped before the block
        # is built — the client sees the ranked, flagged rows, not the levers.
        "_small_sample": together < SMALL_SAMPLE_MIN_PAIR_ORDERS,
        "_lift": lift if lift is not None else _ZERO,
        "_product_a_id": _int(pair["product_a_id"]),
        "_product_b_id": _int(pair["product_b_id"]),
    }


def _pct(value: Decimal | None) -> Decimal | None:
    """A proportion as a percentage, keeping None as None."""
    return None if value is None else (value * _HUNDRED).quantize(_PCT_PLACES)


def _sort(ctx: ResolverContext, warnings: list) -> tuple[str, bool]:
    """The requested sort, validated here because two keys exist in no table.

    `lift`, `support` and `confidence` are computed in this module, so the
    repository's column allowlist cannot vet them and a rejected key would
    otherwise be silently ignored — which returns a different page than was
    asked for and says nothing about it.
    """
    requested = (ctx.filters.sort or "").strip()
    if not requested:
        return "lift", True
    if requested not in _SORTABLE:
        warnings.append(
            warn(
                WarningCode.SMALL_SAMPLE,
                f"Cannot sort the cross-sell table by {requested!r}; it is ranked by "
                f"lift instead. Sortable: {', '.join(sorted(_SORTABLE))}.",
                severity="info",
                requested_sort=requested,
            )
        )
        return "lift", True
    return requested, (ctx.filters.sort_dir or "desc") != "asc"


def _sort_value(row: dict, key: str) -> tuple:
    """Sort tuple: the requested key, then support, then a stable id tiebreak.

    Support is the second term whatever the first is, because lift ties are
    common — every pair seen once alongside two products seen once has a lift
    that is exactly equal — and breaking those by co-occurrence count puts the
    better-evidenced pair first. The ids come last so the order is total and a
    page boundary cannot shuffle between requests.
    """
    primary = row["_lift"] if key == "lift" else row.get(key)
    if primary is None:
        primary = _ZERO
    return (primary, row["orders"], -row["_product_a_id"], -row["_product_b_id"])


# ---------------------------------------------------------------------------
# KPIs and chart
# ---------------------------------------------------------------------------


def _kpis(ctx: ResolverContext, rows: list[dict], totals: dict[str, int]) -> dict:
    """Two cards: how much of the business attaches, and how much is believable.

    `basket_attach_rate` is the share of orders that contained more than one
    distinct product — the headline this view exists to move, and the ceiling on
    what any cross-sell work can achieve. It comes from the per-day scalars, so
    its numerator and denominator are both window-wide and both exact.

    `basket_pairs_observed` counts only pairs that CLEAR the small-sample floor.
    Counting all of them would make a long tail of one-off coincidences read as
    hundreds of merchandising opportunities, which is the number somebody would
    put in a deck.
    """
    declared = set(ctx.view.kpis)
    kpis: dict = {}

    if "basket_attach_rate" in declared:
        kpis["basket_attach_rate"] = build_kpi(
            "basket_attach_rate",
            _pct(_ratio(totals["multi_item_orders"], totals["orders"], _PCT_PLACES)),
            inputs_missing=() if totals["orders"] else ("orders in window",),
            quality=MetricQuality.AUTHORITATIVE,
        )

    if "basket_pairs_observed" in declared:
        kpis["basket_pairs_observed"] = build_kpi(
            "basket_pairs_observed",
            sum(1 for row in rows if not row["_small_sample"]),
            quality=MetricQuality.AUTHORITATIVE,
        )

    return kpis


def _series(ctx: ResolverContext, rows: list[dict]) -> dict[str, list[dict]]:
    """The "most frequent pairs" bar chart: co-occurrence count, not lift.

    Deliberately ranked by `orders` rather than by the table's lift ordering.
    The chart answers "what actually happens most", the table answers "what
    happens more than chance would predict", and drawing the second under a
    heading that says the first is how a two-basket curiosity ends up as the
    longest bar on the page. Small-sample pairs are excluded from the chart
    entirely — a bar chart carries no room for the caveat the table row does.
    """
    charts = [chart for chart in ctx.view.charts if chart.x == "pair"]
    if not charts:  # pragma: no cover - registry declares the chart
        return {}
    ranked = sorted(
        (row for row in rows if not row["_small_sample"]),
        key=lambda row: (row["orders"], -row["_product_a_id"]),
        reverse=True,
    )[:TOP_PAIRS_CHART_LIMIT]
    return {
        charts[0].id: [
            {
                "pair": f"{row['product_a']} + {row['product_b']}",
                "orders_count": row["orders"],
            }
            for row in ranked
        ]
    }
