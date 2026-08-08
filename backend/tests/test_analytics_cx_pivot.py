"""Tests for ``resolvers/cx.py`` — view 51's pivot of the stored star counts.

``agg_cx_daily`` stores the rating distribution as five COLUMNS
(``rating_1``..``rating_5``) plus the additive pair ``rating_sum`` /
``rated_reviews``. The generic breakdown resolver groups by one dimension and
cannot pivot columns into rows, so view 51's two declared charts rendered
empty. ``cx_reviews`` is the custom resolver that draws them; this file proves
the four claims it makes:

  * ``test_distribution_pivots_the_five_columns...`` — five rows, always,
    including a star nobody gave. A histogram with a missing bar reads as "no
    such option", not "zero", and the zero here is measured. The five counts
    must sum to ``SUM(rated_reviews)`` over the window.
  * ``test_trend_daily_averages...`` — each day is ``rating_sum /
    rated_reviews`` for that day, hand-computed here; a day with no rated
    reviews (including a store-wide message-only day) is ABSENT from the
    series. An average of nothing is not 0 stars, and 0 stars is the worst
    rating a chart can draw.
  * ``test_trend_weekly_rebucket...`` — a week is the sums divided once, never
    the mean of the daily means. The rollup suite proves those two differ by
    over a whole star on plausible data; this proves the resolver uses the
    right one.
  * ``test_rated_products_table_is_identical...`` — the regression pin. The
    table was the working half of the view; the custom resolver must produce
    byte-for-byte what ``BreakdownResolver`` produced, which is asserted by
    running BOTH against the same context rather than against a copy of the
    expected rows.

Plus the export path (view 51 is ``export=True``), the out-of-range-rating
warning (a rating of 7 is counted as submitted, excluded from the average, and
named), and the end-to-end resolve through ``AnalyticsViewService``.

Isolation strategy
------------------
Every fixture lives in **1978**, a year this store has never traded in and no
other analytics suite uses (1974-1977, 1990, 1996-1999, 2001-2016, 2018, 2019,
2021, 2024 and 2026 are taken — and the live demo PERF- rows in 2026-05..07
are never touched). Rollup rows are written directly with synthetic product
ids (``agg_cx_daily`` has no FK, exactly so history survives the catalogue),
so no products, reviews, messages or users are created and teardown is one
DELETE over the sandbox year.

No db fixture exists in ``conftest.py``; each test owns its ``SessionLocal()``
and tears down in a ``finally`` through a fresh session.

Run inside the analytics container:

    docker exec wvana-py python -m pytest tests/test_analytics_cx_pivot.py -q
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.db.session import SessionLocal
from app.models.analytics_cx import RATING_COLUMNS, STORE_WIDE_PRODUCT_ID, AggCxDaily
from app.repositories.analytics_repository import AnalyticsRepository
from app.schemas.analytics_view import AnalyticsViewEnvelope
from app.services.analytics import registry
from app.services.analytics.export import (
    TRUNCATION_MARKER,
    build_csv_export,
    export_scope,
)
from app.services.analytics.filters import (
    AnalyticsFilters,
    Comparison,
    Granularity,
    Period,
)
from app.services.analytics.resolvers.base import NOT_CONFIGURED, ResolverContext
from app.services.analytics.resolvers.core import BreakdownResolver
from app.services.analytics.resolvers.cx import (
    CX_RATING_IDENTITY_BROKEN,
    CX_RATING_OUT_OF_RANGE,
    cx_reviews,
)
from app.services.analytics.resolvers.special import CUSTOM_FUNCTIONS
from app.services.analytics.timebox import active_generation
from app.services.analytics.types import MetricQuality
from app.services.analytics.view_service import AnalyticsViewService

# ---------------------------------------------------------------------------
# The 1978 sandbox
# ---------------------------------------------------------------------------

SANDBOX_FIRST = date(1978, 1, 1)
SANDBOX_LAST = date(1978, 12, 31)

#: Three days for the distribution, the table pin, the export and the e2e.
DIST_START = date(1978, 3, 6)
DIST_DAYS = 3

#: Four days for the gap test: reviews on the 1st and 3rd, a message-only day
#: on the 2nd, nothing at all on the 4th. 1978-06-05 is a Monday.
TREND_START = date(1978, 6, 5)
TREND_DAYS = 4

#: One Monday-aligned week for the re-bucket test. 1978-07-03 is a Monday.
WEEK_START = date(1978, 7, 3)
WEEK_DAYS = 7

#: One day carrying the rating-of-7 fixture.
RANGE_DAY = date(1978, 5, 10)

#: Synthetic product ids. `agg_cx_daily` carries no FK — identity is
#: snapshotted into the row — so no `products` rows are needed, and a
#: concurrent suite can neither move these numbers nor be counted by them.
HOT = 978001
COLD = 978002
LONE = 978003

CX_MODULE = "customer-experience"
VIEW_SLUG = "reviews-and-ratings"
TABLE_NAME = AggCxDaily.__tablename__

#: Any date after the sandbox: `today` only decides PARTIAL_TODAY, and no 1978
#: window may ever look like it includes it.
TODAY = date(1978, 12, 31)


# ---------------------------------------------------------------------------
# Fixture builders + teardown
# ---------------------------------------------------------------------------


def _cleanup() -> None:
    """Delete the sandbox year, through a fresh session.

    A fresh session so teardown cannot be skipped by a half-rolled-back
    transaction in the test's own session. Nothing else is owned: no products,
    reviews, messages, users, queue or run-log rows are ever created here.
    """
    with SessionLocal() as session:
        session.execute(
            text(
                f"DELETE FROM {TABLE_NAME} "
                "WHERE bucket_date BETWEEN :first AND :last"
            ),
            {"first": SANDBOX_FIRST, "last": SANDBOX_LAST},
        )
        session.commit()


def _row(day: date, product_id: int, generation: int, **columns) -> AggCxDaily:
    if product_id != STORE_WIDE_PRODUCT_ID:
        columns.setdefault("sku_snapshot", f"SKU-PIV-{product_id}")
    return AggCxDaily(
        bucket_date=day,
        product_id=product_id,
        tz_generation=generation,
        **columns,
    )


def _seed_distribution_days(db, generation: int) -> None:
    """Three identical days: a well-rated product, a one-star magnet, and the
    store-wide message row. rating_2 and rating_3 are zero EVERYWHERE — the
    zero-count stars the histogram must still draw.

    Per day: HOT 8x5 + 2x4 (sum 48 over 10), COLD 2x1 (sum 2 over 2).
    Window totals: {1: 6, 2: 0, 3: 0, 4: 6, 5: 24}, rated 36, submitted 36.
    """
    for offset in range(DIST_DAYS):
        day = DIST_START + timedelta(days=offset)
        db.add(
            _row(
                day, HOT, generation,
                reviews_submitted=10, reviews_approved=10,
                rating_5=8, rating_4=2, rating_sum=48, rated_reviews=10,
                verified_purchase_reviews=6, helpful_votes=12,
            )
        )
        db.add(
            _row(
                day, COLD, generation,
                reviews_submitted=2, reviews_approved=1, reviews_unapproved=1,
                rating_1=2, rating_sum=2, rated_reviews=2,
            )
        )
        db.add(
            _row(
                day, STORE_WIDE_PRODUCT_ID, generation,
                messages_received=5, messages_new=2, messages_replied=2,
                messages_closed=1,
            )
        )
    db.commit()


class _AnalyticsReader:
    """Holds exactly the permission view 51 needs, and nothing else.

    ``AnalyticsViewService`` only ever calls ``has_permission``; non-admin is
    load-bearing, because ``is_admin`` short-circuits every check True.
    """

    is_admin = False

    def __init__(self, permissions: set[str]) -> None:
        self._permissions = permissions

    def has_permission(self, permission: str) -> bool:
        return permission in self._permissions


def _filters(
    start: date, days: int, granularity: Granularity = Granularity.DAY
) -> AnalyticsFilters:
    return AnalyticsFilters(
        period=Period.CUSTOM,
        date_from=start,
        date_to=start + timedelta(days=days),
        comparison=Comparison.NONE,
        granularity=granularity,
    )


def _view():
    view = registry.get_view(CX_MODULE, VIEW_SLUG)
    assert view is not None and view.number == 51
    return view


def _resolve(db, filters: AnalyticsFilters) -> AnalyticsViewEnvelope:
    view = _view()
    service = AnalyticsViewService(db, _AnalyticsReader({view.permission}))
    envelope = service.resolve_view(CX_MODULE, VIEW_SLUG, filters, use_cache=False)
    assert isinstance(envelope, AnalyticsViewEnvelope)
    return envelope


def _ctx(db, filters: AnalyticsFilters) -> ResolverContext:
    """A resolver context over the REAL repository, for the direct-run tests."""
    return ResolverContext(
        db=db,
        repo=AnalyticsRepository(db),
        view=_view(),
        filters=filters,
        window=filters.resolve(TODAY),
        tz_generation=int(active_generation(db).generation),
        today=TODAY,
    )


# ===========================================================================
# 1. The distribution: five rows, always, and they sum to rated_reviews
# ===========================================================================


def test_distribution_pivots_the_five_columns_with_zero_count_stars_included():
    """The pivot the breakdown resolver could not express.

    Five points whatever the data holds: rating_2 and rating_3 were measured
    at zero in this window, and a bar of height 0 is that measurement. A
    missing bar would read as "there is no 2-star option", which is a claim
    about the scale, not about the reviews. And the five counts must sum to
    ``SUM(rated_reviews)`` — the identity that makes this a distribution of
    the rated population rather than of something else.
    """
    db = SessionLocal()
    try:
        _seed_distribution_days(db, int(active_generation(db).generation))
        envelope = _resolve(db, _filters(DIST_START, DIST_DAYS))

        points = envelope.series["rating_distribution"]
        assert [p["rating"] for p in points] == [1, 2, 3, 4, 5], (
            "all five stars, in order, every time — a histogram with a "
            "missing bar misreads as a missing option"
        )
        by_star = {p["rating"]: p["reviews"] for p in points}
        assert by_star == {1: 6, 2: 0, 3: 0, 4: 6, 5: 24}
        assert by_star[2] == 0 and by_star[3] == 0, (
            "the zero-count stars are present as REAL zeros: the window was "
            "measured and nobody gave 2 or 3 stars"
        )

        # 3 days x (10 + 2) rated reviews. The identity the model asserts
        # per row must survive the window sum.
        assert sum(by_star.values()) == 36
        assert len(by_star) == len(RATING_COLUMNS)

        codes = {w.code for w in envelope.warnings}
        assert CX_RATING_IDENTITY_BROKEN not in codes
        assert CX_RATING_OUT_OF_RANGE not in codes, (
            "every rating in this fixture is in range; the warning must not "
            "fire on clean data"
        )
    finally:
        _cleanup()
        db.close()


# ===========================================================================
# 2. The trend: hand-computed daily averages, and gaps that stay gaps
# ===========================================================================


def test_trend_daily_averages_match_and_a_no_review_day_is_a_gap_not_a_zero():
    """Day 1 has two products (the average must be review-weighted across
    them), day 2 has only the store-wide message row (rated_reviews = 0), day
    3 has one product, day 4 has no row at all. The series is exactly days 1
    and 3 — the other two are gaps, because an average of nothing is not 0
    stars.
    """
    day1, day2, day3 = (TREND_START + timedelta(days=n) for n in range(3))
    db = SessionLocal()
    try:
        generation = int(active_generation(db).generation)
        # Day 1: HOT 48 stars over 10, COLD 2 stars over 2 -> 50/12.
        db.add(_row(day1, HOT, generation, reviews_submitted=10,
                    reviews_approved=10, rating_5=8, rating_4=2,
                    rating_sum=48, rated_reviews=10))
        db.add(_row(day1, COLD, generation, reviews_submitted=2,
                    reviews_approved=2, rating_1=2, rating_sum=2,
                    rated_reviews=2))
        # Day 2: messages only. Measured, but nothing was rated.
        db.add(_row(day2, STORE_WIDE_PRODUCT_ID, generation,
                    messages_received=3, messages_new=3))
        # Day 3: a single 1-star pair -> 2/2.
        db.add(_row(day3, COLD, generation, reviews_submitted=2,
                    reviews_approved=2, rating_1=2, rating_sum=2,
                    rated_reviews=2))
        db.commit()

        envelope = _resolve(db, _filters(TREND_START, TREND_DAYS))
        points = envelope.series["rating_trend"]

        assert [p["date"] for p in points] == [day1.isoformat(), day3.isoformat()], (
            "the message-only day and the empty day must be ABSENT — not "
            "present with a 0, which would draw the worst possible rating on "
            "days nobody said anything"
        )
        # Hand-computed: (48 + 2) / (10 + 2) — weighted across both products,
        # not the mean of 4.8 and 1.0 (which would be 2.9).
        assert points[0]["avg_rating"] == Decimal("4.1667")
        assert points[0]["avg_rating"] == (
            Decimal(50) / Decimal(12)
        ).quantize(Decimal("0.0001"))
        assert points[1]["avg_rating"] == Decimal("1.0000")
        assert all(p["avg_rating"] > 0 for p in points), (
            "no fabricated zeros anywhere in the series"
        )
    finally:
        _cleanup()
        db.close()


# ===========================================================================
# 3. Weekly re-bucket: sums first, divide once
# ===========================================================================


def test_trend_weekly_rebucket_equals_the_true_weekly_average():
    """Wildly uneven volume — one 1-star day, a twenty-review 5-star day, a
    two-review 4-star day. The week's true average is SUM(stars)/COUNT, which
    only survives if the resolver sums the stored columns per bucket and
    divides once. The mean of the daily means is 3.33 against a true 4.74 —
    computed here so the test fails loudly if the resolver ever switches to it.
    """
    db = SessionLocal()
    try:
        generation = int(active_generation(db).generation)
        # (day offset, rating, count): sum 1 + 100 + 8 = 109 over 23 reviews.
        plan = ((0, 1, 1), (1, 5, 20), (3, 4, 2))
        for offset, rating, count in plan:
            db.add(
                _row(
                    WEEK_START + timedelta(days=offset), HOT, generation,
                    reviews_submitted=count, reviews_approved=count,
                    rating_sum=rating * count, rated_reviews=count,
                    **{RATING_COLUMNS[rating]: count},
                )
            )
        db.commit()

        envelope = _resolve(
            db, _filters(WEEK_START, WEEK_DAYS, granularity=Granularity.WEEK)
        )
        points = envelope.series["rating_trend"]
        assert len(points) == 1, "one Monday-aligned week, one bucket"
        assert points[0]["date"] == WEEK_START.isoformat()

        true_average = (Decimal(109) / Decimal(23)).quantize(Decimal("0.0001"))
        assert points[0]["avg_rating"] == true_average == Decimal("4.7391")

        mean_of_means = ((Decimal(1) + Decimal(5) + Decimal(4)) / 3).quantize(
            Decimal("0.0001")
        )
        assert mean_of_means != true_average, (
            "this fixture exists to make the two disagree; if they ever "
            "match, the test has stopped proving anything"
        )
        assert points[0]["avg_rating"] != mean_of_means, (
            f"the week must be {true_average} (sums first, divide once), not "
            f"{mean_of_means} (mean of daily means) — more than a whole star "
            "apart on data that looks entirely reasonable"
        )
    finally:
        _cleanup()
        db.close()


# ===========================================================================
# 4. The regression pin: the table is what the breakdown produced
# ===========================================================================


def test_rated_products_table_is_identical_to_the_breakdown_resolvers():
    """Run BOTH resolvers against the same context and compare the block.

    Not against a copy of the expected rows: `BreakdownResolver` still reads
    the same `params` (`dimension`, `metrics`, `source`), so running it IS the
    old behaviour, and equality with it is the strongest available statement
    that the working half of the view did not move. The value assertions
    after it pin the arithmetic to hand-computed numbers so the pair cannot
    drift together.
    """
    db = SessionLocal()
    try:
        _seed_distribution_days(db, int(active_generation(db).generation))
        ctx = _ctx(db, _filters(DIST_START, DIST_DAYS))

        ours = cx_reviews(ctx)
        legacy = BreakdownResolver().run(ctx)

        assert "rated_products" in ours.tables and "rated_products" in legacy.tables
        assert ours.tables["rated_products"] == legacy.tables["rated_products"], (
            "the custom resolver changed the one element of view 51 that "
            "already worked"
        )

        rows = {row["product"]: row for row in ours.tables["rated_products"].rows}
        # 3 days x 10 reviews at (8x5 + 2x4)/10; 3 days x 2 one-star reviews.
        assert rows[HOT]["reviews"] == 30
        assert rows[HOT]["avg_rating"] == Decimal("4.8000")
        assert rows[HOT]["one_star"] == 0
        assert rows[COLD]["reviews"] == 6
        assert rows[COLD]["avg_rating"] == Decimal("1.0000")
        assert rows[COLD]["one_star"] == 6

        store_wide = rows[STORE_WIDE_PRODUCT_ID]
        assert store_wide["reviews"] == 0
        assert store_wide["avg_rating"] is None, (
            "no reviews means no average; an undefined ratio must be null and "
            "never 0, which would read as the worst-rated product in the store"
        )
        # Ranked by review volume, store-wide row last — the breakdown's
        # ordering, preserved.
        assert [r["product"] for r in ours.tables["rated_products"].rows] == [
            HOT, COLD, STORE_WIDE_PRODUCT_ID
        ]

        assert CUSTOM_FUNCTIONS["cx_reviews"] is cx_reviews, (
            "the registry dispatches on this name; the decorator must have "
            "registered it"
        )
    finally:
        _cleanup()
        db.close()


# ===========================================================================
# 5. Export: view 51 is export=True and the table reaches the file
# ===========================================================================


def test_export_still_produces_the_rated_products_table():
    """Resolve inside `export_scope()` — the path `POST /exports` takes — and
    build the CSV from the block, exactly as the endpoint does. The button
    must keep working now that the view runs a custom resolver."""
    db = SessionLocal()
    try:
        _seed_distribution_days(db, int(active_generation(db).generation))
        view = _view()
        assert view.export is True, "view 51 advertises an export"

        with export_scope():
            envelope = _resolve(db, _filters(DIST_START, DIST_DAYS))
        block = envelope.tables["rated_products"]
        assert block.rows and not block.truncated

        export = build_csv_export(
            view=view,
            module_slug=CX_MODULE,
            table_id="rated_products",
            table_spec=view.tables[0],
            rows=block.rows,
            resolved=envelope.filters,
            exported_by="test-cx-pivot",
            timezone_name=envelope.timezone,
            source_truncated=block.truncated,
        )
        text_out = export.text()

        assert export.row_count == 3
        assert not export.truncated and TRUNCATION_MARKER not in text_out
        assert "Product,Reviews,Average,1-star" in text_out, (
            "the declared column labels, in the declared order"
        )
        assert f"{HOT},30,4.8000,0" in text_out
        assert f"{COLD},6,1.0000,6" in text_out
    finally:
        _cleanup()
        db.close()


# ===========================================================================
# 6. Out-of-range ratings are named, and excluded from the average
# ===========================================================================


def test_out_of_range_rating_warns_and_is_excluded_from_the_average():
    """A review carrying a rating of 7: counted as submitted, excluded from
    ``rated_reviews``, the buckets and the average — which is exactly what the
    rollup stores, and exactly what the reader must be told. Three reviews
    (5, 4 and 7): submitted 3, rated 2, sum 9. The average is 4.5, not 9/3."""
    db = SessionLocal()
    try:
        generation = int(active_generation(db).generation)
        db.add(
            _row(
                RANGE_DAY, LONE, generation,
                reviews_submitted=3, reviews_approved=3,
                rating_5=1, rating_4=1, rating_sum=9, rated_reviews=2,
            )
        )
        db.commit()

        envelope = _resolve(db, _filters(RANGE_DAY, 1))

        by_code = {w.code: w for w in envelope.warnings}
        assert CX_RATING_OUT_OF_RANGE in by_code, (
            "reviews_submitted > rated_reviews means a rating fell outside "
            "1..5; the average describes a smaller population than the volume "
            "and the reader must be told"
        )
        warning = by_code[CX_RATING_OUT_OF_RANGE]
        assert warning.detail["out_of_range_reviews"] == 1
        assert warning.detail["reviews_submitted"] == 3
        assert warning.detail["rated_reviews"] == 2

        # The average is over the RATED population: 9/2, never 9/3.
        points = envelope.series["rating_trend"]
        assert len(points) == 1
        assert points[0]["avg_rating"] == Decimal("4.5000")

        # And the distribution counts only the rated reviews: 1 + 1, not 3.
        by_star = {
            p["rating"]: p["reviews"]
            for p in envelope.series["rating_distribution"]
        }
        assert sum(by_star.values()) == 2 == by_star[4] + by_star[5]

        # The five counts still agree with rated_reviews — an out-of-range
        # rating breaks the submitted total away, not the identity.
        assert CX_RATING_IDENTITY_BROKEN not in by_code
    finally:
        _cleanup()
        db.close()


# ===========================================================================
# 7. End to end through AnalyticsViewService
# ===========================================================================


def test_view_51_resolves_end_to_end_with_all_three_declared_elements():
    """The whole envelope: provenance, both charts, the table, no refusals.

    This is the claim the LIVE badge makes, resolved through the same service,
    permission check and registry lookup the endpoint uses.
    """
    db = SessionLocal()
    try:
        _seed_distribution_days(db, int(active_generation(db).generation))
        envelope = _resolve(db, _filters(DIST_START, DIST_DAYS))

        assert envelope.sources, (
            "an empty source list is how this subsystem says 'nothing is "
            "wired up'; the view is bound to agg_cx_daily and must report "
            "provenance"
        )
        assert {s.id for s in envelope.sources} == {TABLE_NAME}
        codes = {w.code for w in envelope.warnings}
        assert NOT_CONFIGURED not in codes, "; ".join(
            w.message for w in envelope.warnings if w.code == NOT_CONFIGURED
        )
        assert envelope.availability == "LIVE"
        assert envelope.quality == MetricQuality.AUTHORITATIVE.value

        # Every element the registry declares is on the wire: the two charts
        # that used to render empty, and the table that always worked.
        assert len(envelope.series["rating_distribution"]) == 5
        trend = envelope.series["rating_trend"]
        assert [p["date"] for p in trend] == [
            (DIST_START + timedelta(days=n)).isoformat() for n in range(DIST_DAYS)
        ]
        assert all(p["avg_rating"] > 0 for p in trend)
        assert envelope.tables["rated_products"].total_rows == 3
    finally:
        _cleanup()
        db.close()


if __name__ == "__main__":  # pragma: no cover - convenience only
    raise SystemExit(pytest.main([__file__, "-q"]))
