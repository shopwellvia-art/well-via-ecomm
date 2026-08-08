"""The reviews-and-ratings resolver — view 51, read side of ``agg_cx_daily``.

Registers one custom function, ``cx_reviews``, reached from the registry with
``resolver=ResolverId.CUSTOM`` and ``params={"fn": "cx_reviews"}``.

It exists because the rating distribution is five COLUMNS, not a dimension.
``agg_cx_daily`` stores ``rating_1`` .. ``rating_5`` (plus the additive pair
``rating_sum`` / ``rated_reviews``) precisely so the average can be re-bucketed
— see the model's docstring — but a group-by resolver can only pivot a
*dimension* into rows, and no rollup groups by "rating". So under the generic
``BreakdownResolver`` two of the three elements view 51 declares rendered
empty: ``rating_distribution`` (x = "rating") received product-grain rows with
no ``rating`` key, and ``rating_trend`` (x = "date") received nothing, because
a breakdown groups by exactly one dimension and this view's is ``product``.

A view runs ONE resolver, so this function emits everything the view declares:

* ``rated_products`` — the product table, built with the same bindings, the
  same grouping, the same ordering and the same row cap as ``BreakdownResolver``
  produced it. The helpers are *called from* that class rather than restated,
  so this table cannot drift from what the generic resolver would have shown.
* ``rating_distribution`` — the five columns pivoted into five rows. All five
  stars are always present, **including a star nobody gave**: a histogram with
  a missing bar reads as "there is no 1-star option", not "zero 1-star
  reviews", and zero here is a REAL zero — the window was measured and that is
  what it held. The five counts are cross-checked against ``rated_reviews``
  from the same query; a mismatch is announced as an error rather than drawn
  over.
* ``rating_trend`` — ``SUM(rating_sum) / SUM(rated_reviews)`` per bucket,
  summed first and divided once, so a week re-bucketed from days equals the
  true weekly average. Never an average of the daily averages: that weights a
  day with three reviews the same as a day with three hundred, and the error
  is silent (``tests/test_analytics_cx_rollup.py`` demonstrates it is more
  than a whole star on plausible data).

A day with no reviews is a GAP in the trend, not a zero
-------------------------------------------------------
An average of nothing is not 0 stars — a 0 would draw the worst possible
rating on a day nobody said anything. So the series is SPARSE, the same
convention ``control_centre._alerts_trend`` follows: a bucket whose
``rated_reviews`` sums to zero (a store-wide message-only day, or no row at
all) is simply absent. ``evaluate`` already returns ``None`` for a ratio over
an empty base, so the gap decision and the arithmetic share one code path.

Out-of-range ratings
--------------------
``rated_reviews`` is a separate denominator from ``reviews_submitted`` on
purpose: a review whose rating falls outside 1..5 (which a CHECK constraint
should prevent, and which the aggregation job already flags as
``cx_rating_out_of_range`` in its run log) is counted as submitted, excluded
from the average and from the five buckets. When the window shows
``reviews_submitted > rated_reviews`` the difference is exactly those reviews,
and this resolver says so in a warning — the average genuinely describes a
smaller population than the volume figure, and only one of those two numbers
should be read as "the rating".
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.models.analytics_cx import RATING_COLUMNS
from app.schemas.analytics_view import TableBlock, WarningCode
from app.services.analytics.export import clamp_row_limit
from app.services.analytics.filters import Granularity
from app.services.analytics.resolvers.base import (
    ResolverContext,
    ResolverResult,
    not_configured,
    probe_source,
    warn,
)

# `_bucket_start` is reused rather than restated (the same call `base.py` makes
# about `_pct_delta`): it is the one definition of where a week and a month
# begin, and a second copy here would be the two-calendars bug waiting to file
# itself. `BreakdownResolver`'s staticmethods are reused for the same reason —
# the table below must stay byte-identical to what the generic resolver
# produced, and calling its code is the only version of "identical" that
# survives a refactor.
from app.services.analytics.resolvers.core import (
    DIMENSION_BINDINGS,
    GRANULARITY_DOWNGRADED,
    METRIC_NOT_BOUND,
    BreakdownResolver,
    MetricBinding,
    _bucket_start,
    binding_for,
    evaluate,
)
from app.services.analytics.resolvers.special import custom_function
from app.services.analytics.types import MetricQuality

__all__ = [
    "CX_SOURCE",
    "CX_RATING_OUT_OF_RANGE",
    "CX_RATING_IDENTITY_BROKEN",
    "cx_reviews",
]

#: The rollup this view reads. Named once.
CX_SOURCE = "agg_cx_daily"

#: The window holds reviews whose rating fell outside 1..5. They are counted in
#: `reviews_submitted`, excluded from `rated_reviews`, the distribution and the
#: average — so the average describes fewer reviews than the volume suggests,
#: and the reader is told rather than left to reconcile the two totals.
#: The aggregation job's run log names the same fact `cx_rating_out_of_range`.
CX_RATING_OUT_OF_RANGE = "CX_RATING_OUT_OF_RANGE"

#: The five star counts do not sum to `rated_reviews` over the window. That
#: identity holds on every row the job writes (`rating_1 + ... + rating_5 ==
#: rated_reviews`, asserted by the rollup's own tests), so a mismatch here
#: means the table has been written by something other than the job and every
#: rating figure on the screen is suspect. Announced as an error rather than
#: raised: the review volumes and the product table do not depend on the
#: identity, and taking the whole view down would hide the evidence.
CX_RATING_IDENTITY_BROKEN = "CX_RATING_IDENTITY_BROKEN"

#: The five distribution columns, in star order. Taken from the model's own
#: map so the pivot and the schema cannot drift apart.
_DISTRIBUTION_COLUMNS: tuple[str, ...] = tuple(
    RATING_COLUMNS[star] for star in sorted(RATING_COLUMNS)
)


@custom_function("cx_reviews")
def cx_reviews(ctx: ResolverContext) -> ResolverResult:
    """View 51 — the products table, the distribution and the trend.

    One resolver, three shapes, one source. The table is the breakdown the
    generic resolver already got right; the two charts are the pivots it could
    not express. All three read ``agg_cx_daily`` and nothing else, so the
    provenance is a single ref and the freshness warnings apply to the whole
    screen at once.
    """
    source = str(ctx.param("source") or CX_SOURCE)
    state = probe_source(ctx, source, label="Customer-experience rollup (daily)")
    warnings = list(state.warnings)

    spec = ctx.view.tables[0] if ctx.view.tables else None
    if spec is None:  # pragma: no cover - registry declares the table
        return not_configured(
            f"View {ctx.view.slug!r} declares no table for the rated products to "
            "fill, so there is nothing to render them into.",
            requires=["registry:tables"],
            detail={"view": ctx.view.slug},
        )

    if not state.has_rows:
        # No rows anywhere for this generation. Return the provenance and the
        # freshness warnings and NOTHING else — a table of zeros would read as
        # "every product is unreviewed" and an all-zero histogram as "nobody
        # rates us", both measurements, and this is the absence of one.
        return ResolverResult(
            sources=[state.ref],
            warnings=warnings,
            quality=MetricQuality.INCOMPLETE,
        ).rolled_up()

    dimension = BreakdownResolver._dimension(ctx)
    binding_dim = DIMENSION_BINDINGS.get(dimension)
    if binding_dim is None:  # pragma: no cover - registry declares "product"
        return not_configured(
            f"This view breaks down by {dimension!r}, which no rollup stores as "
            f"a dimension. Groupable dimensions: "
            f"{', '.join(sorted(DIMENSION_BINDINGS))}.",
            requires=[f"dimension:{dimension}"],
            detail={"view": ctx.view.slug, "dimension": dimension},
        )

    bindings = _table_bindings(ctx, source, warnings)
    if not bindings:  # pragma: no cover - registry binds all three metrics
        return not_configured(
            f"None of this view's measures are stored in {source}. The view "
            "needs an explicit metric binding.",
            requires=[f"binding:{source}"],
            detail={"view": ctx.view.slug, "source": source},
        )

    tables = {
        BreakdownResolver._table_id(ctx): _rated_products(
            ctx, source, dimension, binding_dim.column, bindings, warnings
        )
    }

    series: dict[str, list[dict]] = {}
    quality = MetricQuality.AUTHORITATIVE

    totals = ctx.repo.fetch_totals(
        source,
        columns=[*_DISTRIBUTION_COLUMNS, "rated_reviews", "reviews_submitted"],
        window=ctx.window,
        tz_generation=ctx.tz_generation,
    )
    if not all(totals.get(column) is None for column in _DISTRIBUTION_COLUMNS):
        # The window was measured. All-None means the rollup exists but holds
        # nothing inside this window — then there is no distribution to draw
        # and no population to warn about, and the freshness warnings from
        # `probe_source` already say why the screen is empty.
        series.update(_distribution_series(ctx, totals))
        quality = _check_rating_identity(totals, warnings)
        _warn_out_of_range(totals, warnings)

    series.update(_trend_series(ctx, source, warnings))

    return ResolverResult(
        series=series,
        tables=tables,
        sources=[state.ref],
        warnings=warnings,
        # Counted straight from the reviews people wrote, with no allocation
        # and no estimate — unless the stored distribution contradicts its own
        # denominator, in which case the grade says so.
        quality=quality,
    ).rolled_up()


# ---------------------------------------------------------------------------
# The table — BreakdownResolver's shape, produced by its own helpers
# ---------------------------------------------------------------------------


def _table_bindings(
    ctx: ResolverContext, source: str, warnings: list
) -> dict[str, MetricBinding]:
    """The declared metrics resolved to bindings, unbound ones named.

    Byte-for-byte the resolution `BreakdownResolver.run` performs: registry
    `params` first, catalogue second, and a metric stored elsewhere is omitted
    with a warning rather than shown as zero.
    """
    bindings: dict[str, MetricBinding] = {}
    unbound: list[str] = []
    for metric_id in BreakdownResolver._metrics(ctx, source):
        binding = binding_for(ctx, metric_id, default_source=source)
        if binding is None or binding.source != source:
            unbound.append(metric_id)
            continue
        bindings[metric_id] = binding
    if unbound:
        warnings.append(
            warn(
                METRIC_NOT_BOUND,
                f"{', '.join(sorted(unbound))} is not stored in {source}, so it "
                "is omitted from this breakdown rather than shown as zero.",
                severity="warn",
                metrics=sorted(unbound),
                source=source,
            )
        )
    return bindings


def _rated_products(
    ctx: ResolverContext,
    source: str,
    dimension: str,
    dimension_column: str,
    bindings: dict[str, MetricBinding],
    warnings: list,
) -> TableBlock:
    """The "Products by rating" table, exactly as the breakdown produced it.

    Same clamp, same one-past-the-cap truncation probe, same ordering (the
    first declared metric's first column, descending — most-reviewed first, so
    the top-N is the population worth reading), same row shape. The store-wide
    ``product_id = 0`` message row appears with zero reviews and a null
    average, sorted last, exactly as before — asserted by the regression pin
    in ``tests/test_analytics_cx_pivot.py`` rather than hidden here.
    """
    limit = clamp_row_limit(ctx.filters.limit)
    primary = next(iter(bindings.values()))
    columns = sorted({c for b in bindings.values() for c in b.columns})
    rows = ctx.repo.fetch_rollup(
        source,
        columns=columns,
        window=ctx.window,
        tz_generation=ctx.tz_generation,
        group_by=[dimension_column],
        order_by=f"-{primary.add[0]}",
        # One past the cap: enough to know the cap bit, not enough to be a
        # second page.
        limit=limit + 1,
    )
    truncated = len(rows) > limit
    rows = rows[:limit]

    shaped = [
        {
            dimension: row.get(dimension_column),
            **{
                metric_id: evaluate(binding, row).value
                for metric_id, binding in bindings.items()
            },
        }
        for row in rows
    ]
    if truncated:
        warnings.append(
            warn(
                WarningCode.SMALL_SAMPLE,
                f"Showing the top {limit} of a longer list; the remainder is "
                "not included in these rows.",
                severity="info",
                dimension=dimension,
                limit=limit,
            )
        )
    return TableBlock(rows=shaped, total_rows=len(shaped), truncated=truncated)


# ---------------------------------------------------------------------------
# The distribution — five columns pivoted into five rows
# ---------------------------------------------------------------------------


def _distribution_series(
    ctx: ResolverContext, totals: dict[str, Any]
) -> dict[str, list[dict]]:
    """The ``rating_distribution`` bar chart: one row per star, ALWAYS five.

    A star nobody gave is emitted as a zero, not omitted: within a measured
    window that zero is a real measurement ("nobody gave one star"), and a
    five-bucket histogram missing a bar reads as a four-point scale. This is
    the opposite call from the sparse trend below, and both are right — a
    day with no reviews was not measured *for an average*, whereas every star
    value was measured for its count the moment the window had any row at all.
    """
    charts = [c for c in ctx.view.charts if c.x == "rating"]
    if not charts:  # pragma: no cover - registry declares the chart
        return {}
    chart = charts[0]
    key = chart.series[0] if chart.series else "reviews"
    return {
        chart.id: [
            {chart.x: star, key: int(totals.get(RATING_COLUMNS[star]) or 0)}
            for star in sorted(RATING_COLUMNS)
        ]
    }


def _check_rating_identity(totals: dict[str, Any], warnings: list) -> MetricQuality:
    """Cross-check the pivot against its own denominator, from the same query.

    ``rating_1 + ... + rating_5 == rated_reviews`` holds on every row the job
    writes; both sides here are summed over the same rows in one
    ``fetch_totals`` call, so over any window the identity survives. A
    mismatch means the table was written by something else. It is reported as
    an error-severity warning and an INCOMPLETE grade rather than raised —
    raising would blank the whole view (volumes and all) to protect a chart,
    which hides the corruption instead of exhibiting it.
    """
    pivot_total = sum(int(totals.get(c) or 0) for c in _DISTRIBUTION_COLUMNS)
    rated = int(totals.get("rated_reviews") or 0)
    if pivot_total == rated:
        return MetricQuality.AUTHORITATIVE
    warnings.append(
        warn(
            CX_RATING_IDENTITY_BROKEN,
            f"The five star counts sum to {pivot_total} but rated_reviews sums "
            f"to {rated} over this window. The rollup guarantees these are "
            "equal on every row it writes, so agg_cx_daily has been modified "
            "outside the aggregation job and every rating figure on this "
            "screen is suspect until the affected buckets are recomputed.",
            severity="error",
            star_count_total=pivot_total,
            rated_reviews=rated,
        )
    )
    return MetricQuality.INCOMPLETE


def _warn_out_of_range(totals: dict[str, Any], warnings: list) -> None:
    submitted = int(totals.get("reviews_submitted") or 0)
    rated = int(totals.get("rated_reviews") or 0)
    if submitted <= rated:
        return
    unrated = submitted - rated
    warnings.append(
        warn(
            CX_RATING_OUT_OF_RANGE,
            f"{unrated} review(s) in this window carry a rating outside 1..5. "
            "They are counted in the review volume and EXCLUDED from the "
            "average and the distribution, so the average rating describes "
            f"{rated} review(s), not {submitted}. A CHECK constraint on "
            "reviews.rating should make this impossible; the aggregation "
            "job's run log names the same buckets as cx_rating_out_of_range.",
            severity="warn",
            reviews_submitted=submitted,
            rated_reviews=rated,
            out_of_range_reviews=unrated,
        )
    )


# ---------------------------------------------------------------------------
# The trend — summed first, divided once, gaps left as gaps
# ---------------------------------------------------------------------------


def _trend_series(
    ctx: ResolverContext, source: str, warnings: list
) -> dict[str, list[dict]]:
    """The ``rating_trend`` line: ``rating_sum / rated_reviews`` per bucket.

    The daily rows are summed into the requested bucket FIRST and divided
    once, through the very binding the table's ``avg_rating`` column uses —
    so a week here equals the true weekly average by construction, never the
    mean of seven daily means (`test_a_weeks_average_from_stored_counts_...`
    in the rollup suite is the proof that those two differ).

    Sparse on purpose. `evaluate` returns None for a ratio over an empty base,
    and a bucket whose rated_reviews sums to zero — a message-only day, or no
    row at all — is dropped, not zero-filled: an average of nothing is not 0
    stars, and 0 stars is the worst rating a chart can draw.
    """
    charts = [c for c in ctx.view.charts if c.x == "date"]
    if not charts:  # pragma: no cover - registry declares the chart
        return {}
    chart = charts[0]
    key = chart.series[0] if chart.series else "avg_rating"

    binding = binding_for(ctx, key, default_source=source)
    if binding is None or binding.source != source or not binding.is_ratio:
        warnings.append(
            warn(
                METRIC_NOT_BOUND,
                f"No trend drawn for {key!r}: it has no sum-over-count binding "
                f"on {source}. An absent line is honest; a flat zero is not.",
                severity="warn",
                metrics=[key],
                source=source,
            )
        )
        return {}

    granularity = ctx.filters.granularity
    if granularity is Granularity.HOUR:
        # No hourly grain exists for reviews. Downgrading is announced, exactly
        # as `TimeseriesResolver` announces it.
        granularity = Granularity.DAY
        warnings.append(
            warn(
                GRANULARITY_DOWNGRADED,
                f"{source} has no hourly grain; this series is bucketed by "
                "day. Hourly detail would need an hourly rollup.",
                severity="info",
                source=source,
                requested="hour",
                served="day",
            )
        )

    columns = list(binding.columns)
    rows = ctx.repo.fetch_rollup(
        source,
        columns=columns,
        window=ctx.window,
        tz_generation=ctx.tz_generation,
        group_by=["bucket_date"],
    )

    buckets: dict[date, dict[str, Decimal]] = {}
    for row in rows:
        bucket = _bucket_start(row["bucket_date"], granularity)
        acc = buckets.setdefault(bucket, {})
        for name in columns:
            value = row.get(name)
            if value is None:
                continue
            acc[name] = Decimal(str(value)) + acc.get(name, Decimal("0"))

    points: list[dict] = []
    for bucket in sorted(buckets):
        outcome = evaluate(binding, buckets[bucket])
        if outcome.value is None:
            # Zero rated reviews in this bucket: a gap, never a 0. See the
            # module docstring.
            continue
        points.append({chart.x: bucket.isoformat(), key: outcome.value})
    if not points:
        # No bucket had a rated review. Omit the series entirely rather than
        # returning an empty list a chart would draw an axis through.
        return {}
    return {chart.id: points}
