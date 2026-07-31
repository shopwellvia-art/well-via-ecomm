"""Tests for the basket co-occurrence rollup and the cross-sell view it feeds.

Covers ``basket_pair_daily`` -> ``agg_basket_pair_daily``
(``app/services/analytics/aggregation/jobs_basket.py``) and view 57, which reads
it through ``resolvers/basket.py``. The other aggregation test modules cover the
runner and the twelve original rollups; nothing here repeats them.

What actually has to be true
----------------------------
Four of these tests are about arithmetic that is wrong *silently*, which is the
only kind worth this much fixture:

  * ``test_pair_is_stored_in_canonical_order`` — {A,B} and {B,A} are two
    different rows under the UNIQUE key. Get the ordering wrong and every
    support figure is exactly halved; nothing raises, no row is missing, and the
    two half-rows look like two ordinary pairs.
  * ``test_two_lines_of_the_same_product_produce_no_pair`` — a basket is a SET.
    Pairing lines instead of products makes a product co-occur with itself, and
    "bought with itself" is a quantity question that ``units_sold`` already
    answers.
  * ``test_weekly_lift_from_summed_counts_is_not_the_average_of_daily_lifts`` —
    the test this table's whole design exists for. Lift is a RATIO; the average
    of seven daily lifts is not the weekly lift, and the gap grows with the
    variance in daily volume. Storing a daily lift column would pass every other
    test in this file and be wrong on every week anybody looked at.
  * ``test_bucketing_is_store_local`` — 23:00 IST and 00:30 IST the next day
    share a UTC date and belong to two different reporting days. A UTC bucketer
    merges them and is wrong by 5.5 hours of trade, every day.

Then the two that keep the table and the view honest about their own limits:
the line cap (``test_order_over_the_line_cap_is_skipped_and_the_skip_recorded``)
and the small-sample floor (``test_view_57_demotes_and_warns_about_small_sample``).

Isolation strategy
------------------
Follows ``tests/test_analytics_aggregation_jobs3.py``: no db fixture in
``conftest.py``; every test owns its ``SessionLocal()`` and tears down in a
``finally``.

The sandbox is **2011**, a year this store has never traded in and which no
other analytics test module uses (they sit in 2008, 2009 and 2014-2015), so no
two modules can see each other's rows even mid-run.

The fixtures are ``analytics_order_line`` rows written directly. That is not a
shortcut — it is the job's actual and only input, so building orders, items,
products and users to reach it would test the ingestion path instead of this
one, and would couple these assertions to a catalogue the rollup deliberately
never reads.

Run inside the analytics container:

    docker exec wvana-py python -m pytest tests/test_analytics_basket_rollup.py -q
"""
from __future__ import annotations

import contextlib
import io
import itertools
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import pytest
from sqlalchemy import UniqueConstraint, delete, inspect, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.analytics_base import DIMENSION_UNKNOWN
from app.models.analytics_basket import AggBasketPairDaily
from app.models.analytics_facts import AnalyticsOrderLine, IdentitySource
from app.schemas.analytics_view import AnalyticsViewEnvelope, WarningCode
from app.services.analytics import registry
from app.services.analytics.aggregation import get_job
from app.services.analytics.aggregation.jobs_basket import (
    BasketPairDailyJob,
    MAX_DISTINCT_PRODUCTS_PER_ORDER,
    MAX_PAIRS_PER_ORDER,
)
from app.services.analytics.filters import AnalyticsFilters, Comparison, Period
from app.services.analytics.resolvers.basket import SMALL_SAMPLE_MIN_PAIR_ORDERS
from app.services.analytics.timebox import (
    active_generation,
    day_bounds_utc,
    local_day,
    store_timezone,
)
from app.services.analytics.types import MetricQuality, ResolverId, ViewState
from app.services.analytics.view_service import AnalyticsViewService

# ---------------------------------------------------------------------------
# The 2011 sandbox
# ---------------------------------------------------------------------------

SANDBOX_FIRST = date(2011, 1, 1)
SANDBOX_LAST = date(2011, 12, 31)

DAY_IDEMPOTENT = date(2011, 2, 3)
DAY_DELETE = date(2011, 2, 5)
DAY_CANONICAL = date(2011, 2, 7)
DAY_SELF_PAIR = date(2011, 2, 9)
DAY_FANOUT = date(2011, 2, 11)
DAY_CAP = date(2011, 2, 15)
DAY_MISBUCKETED = date(2011, 2, 17)

#: 23:00 IST on DAY_TZ_LATE is 17:30 UTC that day; 00:30 IST on DAY_TZ_EARLY is
#: 19:00 UTC on DAY_TZ_LATE. The two share a UTC date and belong to different
#: store-local reporting days, which is the whole point of the pair.
DAY_TZ_LATE = date(2011, 2, 20)
DAY_TZ_EARLY = date(2011, 2, 21)

#: Seven consecutive days for the lift re-bucketing test.
WEEK_START = date(2011, 3, 1)
WEEK_DAYS = 7

#: Five consecutive days behind the view-57 end-to-end tests.
VIEW_START = date(2011, 4, 4)
VIEW_DAYS = 5

#: Product ids high enough that they cannot be mistaken for catalogue rows, and
#: unique per concern so two tests cannot describe the same pair.
P_A, P_B, P_C, P_D = 910_001, 910_002, 910_003, 910_004
#: The pair the view tests rank first, and the pair that must be demoted.
V_STRONG_A, V_STRONG_B = 920_001, 920_002
V_WEAK_A, V_WEAK_B = 920_003, 920_004
V_FILLER = 920_005

NAMES: dict[int, str] = {
    P_A: "Ashwagandha Capsules",
    P_B: "Brahmi Tablets",
    P_C: "Chyawanprash",
    P_D: "Draksharishta",
    V_STRONG_A: "Turmeric Latte Mix",
    V_STRONG_B: "Golden Milk Frother",
    V_WEAK_A: "Neem Face Wash",
    V_WEAK_B: "Kumkumadi Oil",
    V_FILLER: "Triphala Churna",
}

TWIN_SQL = Path(__file__).resolve().parents[1] / "scripts/sql/2026-07-29_agg_basket_pair_daily.sql"
MIGRATION_PREVIOUS = "f2a91c4e7b30"
MIGRATION_REVISION = "b7e40c9a2d15"

#: order_id / order_item_id space this module owns. Both are plain integers with
#: no FK, and `order_item_id` is UNIQUE on the fact table, so the counter has to
#: be monotonic across the whole module rather than per test.
_IDS = itertools.count(9_100_001)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def sandbox() -> Iterator[tuple[Session, int]]:
    """A session and the ACTIVE tz generation, with 2011 wiped on the way out.

    The active generation rather than a private one, because
    ``AnalyticsViewService`` reads it from the database and cannot be told
    otherwise. Isolation therefore comes from the year, which this store has
    never traded in.
    """
    db = SessionLocal()
    generation = int(active_generation(db).generation)
    try:
        _wipe(db, generation)
        yield db, generation
    finally:
        try:
            db.rollback()
            _wipe(db, generation)
        finally:
            db.close()


def _wipe(db: Session, generation: int) -> None:
    for model in (AggBasketPairDaily, AnalyticsOrderLine):
        db.execute(
            delete(model).where(
                model.tz_generation == generation,
                model.bucket_date >= SANDBOX_FIRST,
                model.bucket_date <= SANDBOX_LAST,
            )
        )
    db.commit()


def _at(db: Session, day: date, hour: int, minute: int = 0) -> datetime:
    """The UTC instant that is ``hour:minute`` STORE-LOCAL on ``day``.

    Derived from ``timebox.day_bounds_utc``, the same function the job uses, so
    the bucketing assertions are about bucketing rather than about two
    independent copies of the same timezone arithmetic.
    """
    start, _end = day_bounds_utc(day, store_timezone(db))
    return start + timedelta(hours=hour, minutes=minute)


def _basket(
    db: Session,
    generation: int,
    day: date,
    products: Sequence[int],
    *,
    at: datetime | None = None,
    bucket_date: date | None = None,
    order_id: int | None = None,
) -> int:
    """Write one order's worth of line facts. Returns the order id.

    ``products`` is a sequence, not a set, so a test can deliberately repeat a
    product (two lines, one product) or list them out of id order — both are
    things the job has to handle and neither is expressible if the fixture
    deduplicates first.

    ``bucket_date`` defaults to ``local_day(ordered_at)``, i.e. what the real
    ingestion path would compute, so the bucketing tests exercise the actual
    function rather than a hand-written date.
    """
    moment = at if at is not None else _at(db, day, 12)
    bucket = bucket_date if bucket_date is not None else local_day(moment, store_timezone(db))
    order = order_id if order_id is not None else next(_IDS)
    for product_id in products:
        db.add(
            AnalyticsOrderLine(
                order_id=order,
                order_item_id=next(_IDS),
                ordered_at=moment,
                bucket_date=bucket,
                tz_generation=generation,
                product_id=product_id,
                sku_snapshot=f"SKU-{product_id}",
                product_name_snapshot=NAMES.get(product_id, f"Product {product_id}"),
                quantity=1,
                unit_price=Decimal("100.00"),
                extended_price=Decimal("100.00"),
                cost_quality=MetricQuality.INCOMPLETE.value,
                alloc_quality=MetricQuality.ALLOCATED.value,
                identity_source=IdentitySource.CAPTURED_AT_SALE,
                order_status="paid",
                payment_method="prepaid",
            )
        )
    db.flush()
    return order


def _run(db: Session, day: date, generation: int):
    """Run the job for one bucket and commit, as the runner would."""
    result = BasketPairDailyJob().run(db, day, generation)
    db.commit()
    return result


def _rows(db: Session, day: date, generation: int) -> list[AggBasketPairDaily]:
    return list(
        db.execute(
            select(AggBasketPairDaily)
            .where(
                AggBasketPairDaily.bucket_date == day,
                AggBasketPairDaily.tz_generation == generation,
            )
            .order_by(AggBasketPairDaily.product_a_id, AggBasketPairDaily.product_b_id)
        )
        .scalars()
        .all()
    )


def _snapshot(rows: Iterable[AggBasketPairDaily]) -> list[tuple]:
    """Every value a re-run must reproduce exactly. Not just the row count.

    `computed_at` and `id` are excluded: both legitimately change on a rebuild,
    and including them would make the idempotency assertion impossible to state.
    """
    return [
        (
            row.product_a_id,
            row.product_b_id,
            row.product_a_name,
            row.product_b_name,
            row.pair_orders,
            row.orders_with_a,
            row.orders_with_b,
            row.total_orders_in_bucket,
            row.orders_with_any_pair,
            row.orders_skipped_over_cap,
        )
        for row in rows
    ]


def _pair(rows: Iterable[AggBasketPairDaily], a: int, b: int) -> AggBasketPairDaily:
    lo, hi = min(a, b), max(a, b)
    for row in rows:
        if row.product_a_id == lo and row.product_b_id == hi:
            return row
    raise AssertionError(f"no row for pair ({lo}, {hi}) in {_snapshot(rows)}")


# ---------------------------------------------------------------------------
# 1. Idempotency
# ---------------------------------------------------------------------------


def test_running_a_bucket_twice_changes_nothing():
    """The same bucket, twice, identical VALUES — not merely one row per pair.

    The failure this catches is an `ON DUPLICATE KEY UPDATE col = col +
    VALUES(col)` or a missing DELETE: both leave exactly the right number of
    rows and double every count inside them. The recompute queue's lease can
    expire and hand the same bucket to a second worker, so this is a routine
    event, not a pathological one.
    """
    with sandbox() as (db, generation):
        _basket(db, generation, DAY_IDEMPOTENT, [P_A, P_B])
        _basket(db, generation, DAY_IDEMPOTENT, [P_A, P_B, P_C])
        _basket(db, generation, DAY_IDEMPOTENT, [P_A])
        db.commit()

        first = _run(db, DAY_IDEMPOTENT, generation)
        before = _snapshot(_rows(db, DAY_IDEMPOTENT, generation))

        second = _run(db, DAY_IDEMPOTENT, generation)
        after = _snapshot(_rows(db, DAY_IDEMPOTENT, generation))

        assert before == after, (
            "the second run changed the bucket. Identical inputs must produce "
            "identical rows; a difference here is a double-count, not a refresh."
        )
        # Three DISTINCT pairs across the three baskets — {A,B} twice, plus
        # {A,C} and {B,C} from the three-product one. Rows are per pair, not per
        # co-occurrence, which is why this is 3 and not 4.
        assert first.rows_written == second.rows_written == 3
        # 3 orders, all three contain A, two contain B, one contains C.
        pair_ab = _pair(_rows(db, DAY_IDEMPOTENT, generation), P_A, P_B)
        assert pair_ab.pair_orders == 2
        assert pair_ab.orders_with_a == 3
        assert pair_ab.orders_with_b == 2
        assert pair_ab.total_orders_in_bucket == 3
        assert pair_ab.orders_with_any_pair == 2


# ---------------------------------------------------------------------------
# 2. Delete and reinsert
# ---------------------------------------------------------------------------


def test_a_pair_leaves_the_bucket_when_its_only_order_goes():
    """Pattern B's reason for existing, at this table's grain.

    An upsert cannot express a key that DISAPPEARS. Delete the only order that
    contained {A,B}, re-run, and an upsert-based job leaves the pair sitting in
    the cross-sell table at its old count — recommending a bundle that, as far
    as the facts now go, nobody ever bought.
    """
    with sandbox() as (db, generation):
        doomed = _basket(db, generation, DAY_DELETE, [P_A, P_B])
        _basket(db, generation, DAY_DELETE, [P_C, P_D])
        db.commit()

        _run(db, DAY_DELETE, generation)
        assert _pair(_rows(db, DAY_DELETE, generation), P_A, P_B).pair_orders == 1

        db.execute(
            delete(AnalyticsOrderLine).where(AnalyticsOrderLine.order_id == doomed)
        )
        db.commit()

        result = _run(db, DAY_DELETE, generation)
        rows = _rows(db, DAY_DELETE, generation)
        assert [(r.product_a_id, r.product_b_id) for r in rows] == [(P_C, P_D)], (
            "the {A,B} pair survived the deletion of the only order containing it"
        )
        assert result.rows_deleted == 2
        # The survivor's denominator moved too: one order left, not two.
        assert rows[0].total_orders_in_bucket == 1


def test_a_bucket_that_loses_every_pair_is_emptied_not_left_behind():
    """The case an upsert cannot reach at all: zero rows to write.

    With nothing to INSERT, an upsert-shaped job does nothing and yesterday's
    pairs stay. The DELETE has to run unconditionally.
    """
    with sandbox() as (db, generation):
        order = _basket(db, generation, DAY_DELETE, [P_A, P_B])
        db.commit()
        _run(db, DAY_DELETE, generation)
        assert _rows(db, DAY_DELETE, generation)

        db.execute(delete(AnalyticsOrderLine).where(AnalyticsOrderLine.order_id == order))
        db.commit()

        result = _run(db, DAY_DELETE, generation)
        assert _rows(db, DAY_DELETE, generation) == []
        assert result.rows_written == 0
        assert result.rows_deleted == 1


# ---------------------------------------------------------------------------
# 3. Canonical ordering
# ---------------------------------------------------------------------------


def test_pair_is_stored_in_canonical_order():
    """An order whose lines list B before A still writes one row with a < b.

    Without canonical ordering the bucket holds {B,A} here and {A,B} in the next
    order that happens to list them the other way round — two rows, each with
    half the co-occurrences, both plausible. The UNIQUE key does not save you:
    (B,A) and (A,B) are genuinely different tuples.
    """
    with sandbox() as (db, generation):
        # B listed first, and B has the HIGHER id, so a writer that preserved
        # line order would store (B, A).
        _basket(db, generation, DAY_CANONICAL, [P_B, P_A])
        _basket(db, generation, DAY_CANONICAL, [P_A, P_B])
        db.commit()

        _run(db, DAY_CANONICAL, generation)
        rows = _rows(db, DAY_CANONICAL, generation)

        assert len(rows) == 1, (
            "the same unordered pair was stored twice under two orderings: "
            f"{_snapshot(rows)}"
        )
        row = rows[0]
        assert (row.product_a_id, row.product_b_id) == (P_A, P_B)
        assert row.product_a_id < row.product_b_id
        assert row.pair_orders == 2, "both orders must count toward the one pair"
        assert row.product_a_name == NAMES[P_A]
        assert row.product_b_name == NAMES[P_B]


def test_no_row_anywhere_violates_the_canonical_ordering():
    """Asserted over the whole sandbox, not just the pair a test built.

    The invariant is a property of the writer, so it is worth checking against
    everything the writer produced rather than against one crafted order.
    """
    with sandbox() as (db, generation):
        _basket(db, generation, DAY_CANONICAL, [P_D, P_C, P_B, P_A])
        _basket(db, generation, DAY_CANONICAL, [P_C, P_A])
        db.commit()
        _run(db, DAY_CANONICAL, generation)

        rows = _rows(db, DAY_CANONICAL, generation)
        assert rows
        offenders = [
            (r.product_a_id, r.product_b_id)
            for r in rows
            if r.product_a_id >= r.product_b_id
        ]
        assert not offenders, f"rows not in canonical order: {offenders}"


# ---------------------------------------------------------------------------
# 4. No self-pairs
# ---------------------------------------------------------------------------


def test_two_lines_of_the_same_product_produce_no_pair():
    """A basket is a SET of products. Two lines of one product is one product.

    Pairing lines rather than products makes A co-occur with A, which is not a
    cross-sell relationship — it is a quantity, and `agg_product_daily.units`
    already answers it. It would also inflate every marginal, because A would be
    counted twice in one basket.
    """
    with sandbox() as (db, generation):
        _basket(db, generation, DAY_SELF_PAIR, [P_A, P_A, P_A])
        db.commit()

        result = _run(db, DAY_SELF_PAIR, generation)
        rows = _rows(db, DAY_SELF_PAIR, generation)

        assert rows == [], f"a single-product order produced pairs: {_snapshot(rows)}"
        assert result.rows_written == 0


def test_a_repeated_product_is_counted_once_in_the_marginals():
    """Three lines of A plus one of B is one A-basket, not three."""
    with sandbox() as (db, generation):
        _basket(db, generation, DAY_SELF_PAIR, [P_A, P_A, P_A, P_B])
        db.commit()
        _run(db, DAY_SELF_PAIR, generation)

        row = _pair(_rows(db, DAY_SELF_PAIR, generation), P_A, P_B)
        assert row.pair_orders == 1
        assert row.orders_with_a == 1, "A was counted more than once in one basket"
        assert row.orders_with_b == 1
        assert row.total_orders_in_bucket == 1


# ---------------------------------------------------------------------------
# 5. Fan-out arithmetic
# ---------------------------------------------------------------------------


def test_fan_out_is_n_choose_2():
    """3 distinct products -> exactly 3 pairs; 1 product -> 0; 2 -> 1.

    The count is not incidental: it is the whole reason the line cap exists, and
    a writer that emitted ordered pairs would produce 6 here rather than 3.
    """
    with sandbox() as (db, generation):
        _basket(db, generation, DAY_FANOUT, [P_A, P_B, P_C])
        db.commit()
        _run(db, DAY_FANOUT, generation)

        rows = _rows(db, DAY_FANOUT, generation)
        assert [(r.product_a_id, r.product_b_id) for r in rows] == [
            (P_A, P_B),
            (P_A, P_C),
            (P_B, P_C),
        ]
        assert all(r.pair_orders == 1 for r in rows)
        assert all(r.total_orders_in_bucket == 1 for r in rows)
        assert all(r.orders_with_any_pair == 1 for r in rows)


def test_a_single_line_order_produces_no_pairs_but_still_counts_as_a_basket():
    """It has no pair, and it is still a basket that could have had one.

    Dropping it from `total_orders_in_bucket` would make support the share of
    *multi-item* orders rather than of orders, which quietly inflates every
    support figure on a store where most baskets hold one thing.
    """
    with sandbox() as (db, generation):
        _basket(db, generation, DAY_FANOUT, [P_A, P_B])
        _basket(db, generation, DAY_FANOUT, [P_C])
        db.commit()
        _run(db, DAY_FANOUT, generation)

        rows = _rows(db, DAY_FANOUT, generation)
        assert len(rows) == 1
        assert rows[0].pair_orders == 1
        assert rows[0].total_orders_in_bucket == 2, "the solo basket left the denominator"
        assert rows[0].orders_with_any_pair == 1


# ---------------------------------------------------------------------------
# 6. Why counts are stored and ratios are not
# ---------------------------------------------------------------------------


def _daily_lift(row: AggBasketPairDaily) -> Decimal:
    return (
        Decimal(row.pair_orders)
        * Decimal(row.total_orders_in_bucket)
        / (Decimal(row.orders_with_a) * Decimal(row.orders_with_b))
    )


def test_weekly_lift_from_summed_counts_is_not_the_average_of_daily_lifts():
    """The test this table's design exists for.

    Seven days are built so their volumes differ: one small day where {A,B}
    co-occurs in every A-basket (lift 2.0) and six larger days where it does not
    (lift 1.0). Then:

      * lift recomputed from the SUMMED counts equals the lift computed directly
        from the union of all seven days' baskets — which is what a weekly
        figure must mean;
      * the average of the seven daily lifts is a different number.

    A stored `lift` column would have to give the second answer, and would give
    it under a heading that says "this week". This is exactly why
    `analytics_base` forbids stored averages and why the same rule applies to
    ratios.
    """
    with sandbox() as (db, generation):
        # Day 0 — 4 baskets: 2 hold {A,B}, 2 hold {C,D}. A:2 B:2 AB:2 N:4.
        for _ in range(2):
            _basket(db, generation, WEEK_START, [P_A, P_B])
        for _ in range(2):
            _basket(db, generation, WEEK_START, [P_C, P_D])

        # Days 1..6 — 8 baskets each: 2 x {A,B}, 2 x {A,C}, 2 x {B,C}, 2 x {C,D}.
        # A:4 B:4 AB:2 N:8, so the daily lift is exactly 1.0.
        for offset in range(1, WEEK_DAYS):
            day = WEEK_START + timedelta(days=offset)
            for pair in ([P_A, P_B], [P_A, P_C], [P_B, P_C], [P_C, P_D]):
                for _ in range(2):
                    _basket(db, generation, day, pair)
        db.commit()

        rows: list[AggBasketPairDaily] = []
        for offset in range(WEEK_DAYS):
            day = WEEK_START + timedelta(days=offset)
            _run(db, day, generation)
            rows.append(_pair(_rows(db, day, generation), P_A, P_B))

        # --- the honest weekly figure, from the summed counts ---------------
        together = sum(r.pair_orders for r in rows)
        with_a = sum(r.orders_with_a for r in rows)
        with_b = sum(r.orders_with_b for r in rows)
        baskets = sum(r.total_orders_in_bucket for r in rows)
        summed_lift = (
            Decimal(together) * Decimal(baskets) / (Decimal(with_a) * Decimal(with_b))
        )

        # --- the same figure computed from the raw baskets, independently ----
        # Recounted from the fact table rather than restated as a constant, so
        # this asserts the property and not the author's arithmetic.
        truth = _lift_from_facts(db, generation, WEEK_START, WEEK_DAYS, P_A, P_B)

        assert summed_lift == truth, (
            "lift recomputed from the summed counts disagrees with the lift of the "
            f"union of the week's baskets: {summed_lift} vs {truth}"
        )

        # --- and the number a stored daily lift would have produced ----------
        daily = [_daily_lift(row) for row in rows]
        average_of_dailies = sum(daily) / Decimal(len(daily))
        assert average_of_dailies != summed_lift, (
            "the average of the daily lifts happens to equal the weekly lift in "
            "this fixture, so it proves nothing — rebuild the days with different "
            "volumes"
        )
        assert daily[0] == Decimal(2), "the small day should be the high-lift one"
        assert all(value == Decimal(1) for value in daily[1:])


def _lift_from_facts(
    db: Session, generation: int, start: date, days: int, a: int, b: int
) -> Decimal:
    """Lift over the union of the window's baskets, counted from the facts.

    An independent second implementation on purpose: if the rollup and this
    agreed only because both contained the same mistake, the test above would
    be worthless.
    """
    lines = db.execute(
        select(AnalyticsOrderLine.order_id, AnalyticsOrderLine.product_id).where(
            AnalyticsOrderLine.tz_generation == generation,
            AnalyticsOrderLine.bucket_date >= start,
            AnalyticsOrderLine.bucket_date < start + timedelta(days=days),
        )
    ).all()

    baskets: dict[int, set[int]] = {}
    for order_id, product_id in lines:
        baskets.setdefault(int(order_id), set()).add(int(product_id))

    total = len(baskets)
    with_a = sum(1 for products in baskets.values() if a in products)
    with_b = sum(1 for products in baskets.values() if b in products)
    together = sum(1 for products in baskets.values() if {a, b} <= products)
    return Decimal(together) * Decimal(total) / (Decimal(with_a) * Decimal(with_b))


# ---------------------------------------------------------------------------
# 7. The line cap
# ---------------------------------------------------------------------------


def test_order_over_the_line_cap_is_skipped_and_the_skip_recorded():
    """One oversized order must not be able to outweigh a week of real baskets.

    It is excluded from EVERYTHING — pairs, marginals and the denominator — so
    the surviving ratios stay over one population, and the count of what went is
    written to every row of the bucket rather than only to the run log, because
    the view has to be able to say so.
    """
    with sandbox() as (db, generation):
        oversized = list(range(950_001, 950_001 + MAX_DISTINCT_PRODUCTS_PER_ORDER + 1))
        _basket(db, generation, DAY_CAP, oversized)
        _basket(db, generation, DAY_CAP, [P_A, P_B])
        db.commit()

        result = _run(db, DAY_CAP, generation)
        rows = _rows(db, DAY_CAP, generation)

        assert [(r.product_a_id, r.product_b_id) for r in rows] == [(P_A, P_B)], (
            "the over-cap order contributed pairs: "
            f"{[(r.product_a_id, r.product_b_id) for r in rows][:5]}"
        )
        assert rows[0].orders_skipped_over_cap == 1
        assert rows[0].total_orders_in_bucket == 1, (
            "the skipped order stayed in the denominator, which depresses every "
            "support figure in the bucket by an amount nothing records"
        )
        assert any("basket_order_over_cap" in w for w in result.warnings)


def test_an_order_exactly_at_the_cap_is_kept():
    """The cap is inclusive, and the fan-out bound is what the constant says."""
    with sandbox() as (db, generation):
        at_cap = list(range(960_001, 960_001 + MAX_DISTINCT_PRODUCTS_PER_ORDER))
        _basket(db, generation, DAY_CAP, at_cap)
        db.commit()

        result = _run(db, DAY_CAP, generation)
        rows = _rows(db, DAY_CAP, generation)

        assert len(rows) == MAX_PAIRS_PER_ORDER
        assert result.rows_written == MAX_PAIRS_PER_ORDER
        assert all(r.orders_skipped_over_cap == 0 for r in rows)
        assert not any("basket_order_over_cap" in w for w in result.warnings)


def test_a_bucket_of_only_oversized_orders_says_the_skip_went_unrecorded():
    """No pair rows means nowhere to store the skip count. Say so, don't lose it."""
    with sandbox() as (db, generation):
        oversized = list(range(970_001, 970_001 + MAX_DISTINCT_PRODUCTS_PER_ORDER + 1))
        _basket(db, generation, DAY_CAP, oversized)
        db.commit()

        result = _run(db, DAY_CAP, generation)
        assert _rows(db, DAY_CAP, generation) == []
        assert any("basket_skip_unrecorded" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# 8. Store-local bucketing
# ---------------------------------------------------------------------------


def test_bucketing_is_store_local():
    """23:00 IST and 00:30 IST the next day share a UTC date, not a bucket.

    23:00 IST is 17:30 UTC the same day; 00:30 IST is 19:00 UTC the day BEFORE.
    A bucketer that used UTC dates would merge them into one reporting day and
    would be wrong by 5.5 hours of trade, every day, unrecoverably.

    The fixture derives its ``bucket_date`` from ``timebox.local_day`` — the
    real ingestion rule — so this asserts the chain (instant -> local day ->
    bucket) rather than a hand-written date.
    """
    with sandbox() as (db, generation):
        late = _at(db, DAY_TZ_LATE, 23, 0)
        early = _at(db, DAY_TZ_EARLY, 0, 30)
        assert late.date() == early.date(), (
            "the fixture is not exercising the boundary: the two instants must "
            f"share a UTC date, got {late.date()} and {early.date()}"
        )

        _basket(db, generation, DAY_TZ_LATE, [P_A, P_B], at=late)
        _basket(db, generation, DAY_TZ_EARLY, [P_C, P_D], at=early)
        db.commit()

        late_result = _run(db, DAY_TZ_LATE, generation)
        early_result = _run(db, DAY_TZ_EARLY, generation)

        late_rows = _rows(db, DAY_TZ_LATE, generation)
        early_rows = _rows(db, DAY_TZ_EARLY, generation)

        assert [(r.product_a_id, r.product_b_id) for r in late_rows] == [(P_A, P_B)]
        assert [(r.product_a_id, r.product_b_id) for r in early_rows] == [(P_C, P_D)]
        assert late_rows[0].total_orders_in_bucket == 1
        assert early_rows[0].total_orders_in_bucket == 1
        # The job's own window check agrees with the fact's bucketing.
        assert not any("basket_bucket_window_mismatch" in w for w in late_result.warnings)
        assert not any("basket_bucket_window_mismatch" in w for w in early_result.warnings)


def test_a_line_bucketed_outside_its_own_store_local_day_is_reported():
    """The guard, exercised. A fact whose bucket disagrees with its timestamp is
    the signature of rows written under a different timezone generation, and it
    is counted rather than dropped — the numbers stay whole and the run says so.
    """
    with sandbox() as (db, generation):
        _basket(
            db,
            generation,
            DAY_MISBUCKETED,
            [P_A, P_B],
            at=_at(db, DAY_MISBUCKETED + timedelta(days=3), 12),
            bucket_date=DAY_MISBUCKETED,
        )
        db.commit()

        result = _run(db, DAY_MISBUCKETED, generation)
        rows = _rows(db, DAY_MISBUCKETED, generation)

        assert len(rows) == 1, "the mis-bucketed line was silently dropped"
        assert any("basket_bucket_window_mismatch" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# 9. View 57, end to end
# ---------------------------------------------------------------------------


class _AnalyticsReader:
    """Exactly the one permission view 57 needs, and nothing else.

    `AnalyticsViewService` only calls `has_permission`, so this exercises the
    real authorisation path without creating a user — and without reaching for
    the shared admin account, whose `is_admin` short circuit would make every
    check pass for the wrong reason.
    """

    is_admin = False

    def __init__(self, permission: str) -> None:
        self._permission = permission

    def has_permission(self, permission: str) -> bool:
        return permission == self._permission


def _seed_view_window(db: Session, generation: int) -> None:
    """Five days holding one strong pair, one small-sample pair and filler.

    The strong pair co-occurs on every day, clearing the small-sample floor with
    room to spare. The weak pair co-occurs exactly once, which is the case the
    view must demote rather than crown: it is two rare products meeting once,
    and its lift is the largest number on the page.
    """
    for offset in range(VIEW_DAYS):
        day = VIEW_START + timedelta(days=offset)
        for _ in range(2):
            _basket(db, generation, day, [V_STRONG_A, V_STRONG_B])
        _basket(db, generation, day, [V_STRONG_A, V_FILLER])
        _basket(db, generation, day, [V_FILLER])
    # One basket, one day: two products that appear nowhere else at all.
    _basket(db, generation, VIEW_START, [V_WEAK_A, V_WEAK_B])
    db.commit()

    for offset in range(VIEW_DAYS):
        _run(db, VIEW_START + timedelta(days=offset), generation)


def _resolve_view_57(db: Session) -> AnalyticsViewEnvelope:
    return _resolve_view_57_over(db, VIEW_START, VIEW_START + timedelta(days=VIEW_DAYS))


def _resolve_view_57_over(
    db: Session, date_from: date, date_to: date
) -> AnalyticsViewEnvelope:
    view = registry.get_view("products", "product-bundling-and-cross-sell")
    assert view is not None
    service = AnalyticsViewService(db, _AnalyticsReader(view.permission))
    envelope = service.resolve_view(
        "products",
        "product-bundling-and-cross-sell",
        AnalyticsFilters(
            period=Period.CUSTOM,
            date_from=date_from,
            date_to=date_to,
            comparison=Comparison.NONE,
        ),
        use_cache=False,
    )
    assert isinstance(envelope, AnalyticsViewEnvelope), (
        "view 57 returned a gated envelope; it is LIVE and must resolve"
    )
    return envelope


def test_view_57_is_wired_to_the_basket_rollup():
    """The registry entry itself, before any data touches it."""
    view = registry.get_view("products", "product-bundling-and-cross-sell")
    assert view is not None
    assert view.number == 57
    assert view.state is ViewState.LIVE
    assert view.resolver is ResolverId.CUSTOM
    assert view.params.get("fn") == "basket_cross_sell"
    assert view.params.get("source") == "agg_basket_pair_daily"
    assert view.kpis == ("basket_attach_rate", "basket_pairs_observed")
    spec = view.tables[0]
    assert spec.default_sort == "lift", (
        "ranking by co-occurrence count reprints the bestseller list; the point "
        "of this view is the pairs that beat chance"
    )
    keys = [column.key for column in spec.columns]
    assert {"product_a", "product_b", "orders", "lift", "support"} <= set(keys)


def test_view_57_resolves_with_real_values():
    """End to end through the service: real rows, real ratios, real provenance."""
    with sandbox() as (db, generation):
        _seed_view_window(db, generation)
        envelope = _resolve_view_57(db)

        assert envelope.sources, "no provenance — the view reported nothing wired up"
        assert envelope.sources[0].id == "agg_basket_pair_daily"
        assert envelope.sources[0].rows

        block = envelope.tables["basket_pairs"]
        assert block.rows, "the cross-sell table came back empty on seeded data"

        rows = {(row["product_a"], row["product_b"]): row for row in block.rows}
        strong = rows[(NAMES[V_STRONG_A], NAMES[V_STRONG_B])]

        # 5 days x 2 baskets. 20 baskets total in the window (4 a day), plus the
        # single weak-pair basket on day one = 21.
        assert strong["orders"] == 10
        assert strong["days_observed"] == VIEW_DAYS
        assert strong["lift"] is not None and strong["lift"] > 1, (
            "the strong pair must beat chance"
        )
        assert strong["support"] is not None and strong["support"] > 0
        assert strong["confidence"] is not None and strong["confidence"] > 0

        # Support is a percentage of the baskets, so it cannot exceed 100.
        for row in block.rows:
            if row["support"] is not None:
                assert Decimal(0) <= row["support"] <= Decimal(100)

        assert envelope.kpis["basket_attach_rate"].value is not None
        assert envelope.kpis["basket_attach_rate"].value > 0
        # Exactly one pair clears the small-sample floor: {STRONG_A, STRONG_B}
        # at 10, and {STRONG_A, FILLER} at 5. The weak pair is at 1.
        assert envelope.kpis["basket_pairs_observed"].value == Decimal(2)


def test_view_57_demotes_and_warns_about_small_sample():
    """A lift of 40 from one basket is noise, and must not head the page.

    The weak pair has the highest lift in the window by a wide margin — two
    products that appear nowhere else, meeting once — which is precisely the
    figure that must not be presented as a recommendation. It is kept, ranked
    below every pair that clears the floor, and named in a SMALL_SAMPLE warning.
    """
    with sandbox() as (db, generation):
        _seed_view_window(db, generation)
        envelope = _resolve_view_57(db)

        block = envelope.tables["basket_pairs"]
        order = [(row["product_a"], row["product_b"]) for row in block.rows]
        weak = (NAMES[V_WEAK_A], NAMES[V_WEAK_B])
        strong = (NAMES[V_STRONG_A], NAMES[V_STRONG_B])

        assert weak in order, "the small-sample pair was hidden rather than demoted"
        assert order.index(strong) < order.index(weak), (
            "the one-basket pair outranked a pair seen ten times, which is the "
            f"failure the small-sample floor exists to prevent: {order}"
        )

        by_pair = {(r["product_a"], r["product_b"]): r for r in block.rows}
        assert by_pair[weak]["orders"] < SMALL_SAMPLE_MIN_PAIR_ORDERS
        assert by_pair[weak]["lift"] > by_pair[strong]["lift"], (
            "the fixture no longer demonstrates the problem: the demoted pair "
            "must be the one with the flattering lift"
        )

        codes = {w.code for w in envelope.warnings}
        assert WarningCode.SMALL_SAMPLE in codes, (
            f"no SMALL_SAMPLE warning on a page containing a one-basket pair: {codes}"
        )


def test_view_57_reports_nothing_rather_than_zeros_on_an_empty_window():
    """An empty window is an absence of measurement, not a measurement of zero.

    A table of zeros here reads as "nothing is ever bought together", which is a
    finding. There is no finding; there are no rows.
    """
    with sandbox() as (db, generation):
        envelope = _resolve_view_57(db)
        assert envelope.tables["basket_pairs"].rows == []
        assert not any(
            kpi.value not in (None, Decimal(0)) for kpi in envelope.kpis.values()
        )


# ---------------------------------------------------------------------------
# 10. Schema, migration and the twin SQL
# ---------------------------------------------------------------------------


def test_table_matches_the_orm_in_the_database():
    """Reflection: the live table has every ORM column, with a compatible type.

    The analytics schema ships as TWO artifacts that can silently diverge — the
    Alembic revision applied here, and the hand-applied SQL for the shared
    production MySQL. This is the half that proves the model and the database
    agree; `test_twin_sql_matches_the_migration` is the half that proves the
    hand-applied file cannot drift from the revision.
    """
    db = SessionLocal()
    try:
        inspector = inspect(db.get_bind())
        table = AggBasketPairDaily.__table__

        assert inspector.has_table(table.name), (
            f"{table.name} is declared in the ORM but missing from the database. "
            "Run `alembic upgrade heads`, or apply "
            "backend/scripts/sql/2026-07-29_agg_basket_pair_daily.sql."
        )

        actual = {c["name"]: c for c in inspector.get_columns(table.name)}
        missing = sorted(set(table.columns.keys()) - set(actual))
        assert not missing, f"{table.name}: columns in ORM but not in DB: {missing}"

        for column in table.columns:
            assert _family(str(actual[column.name]["type"])) == _family(str(column.type)), (
                f"{table.name}.{column.name}: ORM says {column.type}, "
                f"database has {actual[column.name]['type']}"
            )

        declared = {index.name for index in table.indexes}
        present = {index["name"] for index in inspector.get_indexes(table.name)}
        assert not declared - present, (
            f"indexes declared in the ORM but absent from the DB: "
            f"{sorted(declared - present)}"
        )

        assert not inspector.get_foreign_keys(table.name), (
            "a foreign key appeared on a rollup; they must stay independently "
            "truncatable and a deleted product must not erase its affinities"
        )
    finally:
        db.close()


def _family(rendered: str) -> str:
    """Type family, not exact rendering. MySQL says DECIMAL where SQLAlchemy says
    NUMERIC and widths render differently across versions; a family mismatch is
    what actually matters."""
    text = rendered.upper()
    if "BOOL" in text or "TINYINT" in text:
        return "BOOL"
    for key in ("DECIMAL", "NUMERIC"):
        if key in text:
            return "NUMERIC"
    for key in ("BIGINT", "SMALLINT", "INTEGER", "INT"):
        if key in text:
            return "INT"
    for key in ("VARCHAR", "CHAR", "TEXT"):
        if key in text:
            return "STRING"
    for key in ("DATETIME", "TIMESTAMP"):
        if key in text:
            return "DATETIME"
    if "DATE" in text:
        return "DATE"
    if any(key in text for key in ("FLOAT", "DOUBLE", "REAL")):
        return "FLOAT"
    return text


def test_the_table_obeys_the_rollup_conventions():
    """The four rules from `analytics_base`, applied to this table by name.

    `test_analytics_schema.py` enforces them over the twelve tables in
    `analytics_rollups.py` and collects its list from that module, so a rollup
    living anywhere else is outside its reach. These are the same rules, asserted
    where this model actually is.
    """
    table = AggBasketPairDaily.__table__

    assert not table.foreign_keys, "rollups carry no foreign keys"

    uniques = [c for c in table.constraints if isinstance(c, UniqueConstraint)]
    assert len(uniques) == 1, f"expected exactly one UNIQUE key, got {len(uniques)}"
    key = [c.name for c in uniques[0].columns]
    assert "tz_generation" in key, (
        f"unique key {key} omits tz_generation; a reporting-timezone change would "
        "let rows cut under two different day boundaries collide"
    )
    assert not [c.name for c in uniques[0].columns if c.nullable], (
        "a nullable column inside the UNIQUE key. MySQL allows many NULLs under "
        "UNIQUE, so the idempotency key would not hold and the job would double-count"
    )

    floats = [
        c.name
        for c in table.columns
        if any(k in str(c.type).upper() for k in ("FLOAT", "DOUBLE", "REAL"))
    ]
    assert not floats, f"floating-point columns forbidden: {floats}"

    derived = [
        c.name
        for c in table.columns
        if c.name.startswith("avg_")
        or c.name.endswith(("_rate", "_pct", "_ratio"))
        or c.name in {"aov", "average", "lift", "support", "confidence"}
    ]
    assert not derived, (
        f"stored ratios/averages forbidden on a rollup: {derived}. Support, "
        "confidence and lift are recomputed at read time from the counts — a "
        "stored daily lift cannot be re-bucketed into a week."
    )


def test_the_rollup_is_registered_for_autogenerate_and_for_reads():
    """Two registrations, both of which fail silently when they are missing.

    Without `app/db/base.py`, Alembic autogenerate cannot see the table and will
    propose dropping it. Without the repository's source allowlist, view 57
    raises `unknown analytics source` on every request.
    """
    from app.db.base import Base
    from app.repositories.analytics_repository import known_sources, measures_for

    assert AggBasketPairDaily.__tablename__ in Base.metadata.tables
    assert AggBasketPairDaily.__tablename__ in known_sources()

    measures = set(measures_for(AggBasketPairDaily.__tablename__))
    assert {
        "pair_orders",
        "orders_with_a",
        "orders_with_b",
        "total_orders_in_bucket",
        "orders_with_any_pair",
        "orders_skipped_over_cap",
    } == measures
    # The pair ids are the grain, not measures: SUM(product_a_id) is nonsense and
    # the repository must refuse it rather than return a large number.
    assert "product_a_id" not in measures
    assert "product_b_id" not in measures


def test_the_resolver_never_groups_by_a_measure():
    """`group_by` means DIMENSION. A measure there is a category error.

    The resolver needs three BUCKET-LEVEL SCALARS —
    `total_orders_in_bucket`, `orders_with_any_pair`, `orders_skipped_over_cap`
    — each repeated on every pair row of its day. An earlier version got them by
    putting them in `group_by`, which turned them from SUM targets into GROUP BY
    keys and returned exactly the right number against MySQL.

    It was still wrong, and it broke in the one place that models the schema
    rather than the SQL: `tests/test_analytics_export.py`'s repository double
    synthesises a LABEL for every grouping key, because everywhere else in this
    subsystem a grouping key is a dimension. `int('P00000')` is what a measure
    masquerading as a dimension looks like from the outside.

    Asserted against the repository's own reflected split rather than a
    hand-written list, so a new measure on this table is covered without an edit
    here.
    """
    from app.repositories.analytics_repository import columns_for, measures_for
    from app.services.analytics.resolvers.basket import (
        BASKET_SOURCE,
        GROUPABLE_COLUMNS,
    )

    measures = set(measures_for(BASKET_SOURCE))
    offenders = sorted(GROUPABLE_COLUMNS & measures)
    assert not offenders, (
        f"{offenders} are summable measures of {BASKET_SOURCE} and must never "
        "appear in a group_by. Recover a bucket-level scalar by projecting its "
        "SUM alongside row_count and dividing (see resolvers/basket.py::"
        "_per_bucket), not by grouping on the value."
    )
    unknown = sorted(GROUPABLE_COLUMNS - set(columns_for(BASKET_SOURCE)))
    assert not unknown, f"{unknown} are not columns of {BASKET_SOURCE} at all"


def test_a_bucket_level_scalar_is_recovered_from_its_sum():
    """`_per_bucket` divides by the row count it was handed, and names it.

    The scalar is constant across a day's pair rows, so SUM/COUNT is that
    constant exactly. The zero-row case returns 0 rather than dividing.
    """
    from app.services.analytics.resolvers.basket import _per_bucket

    # 40 orders repeated across 300 pair rows: the sum is 12 000.
    assert _per_bucket(12_000, 300) == 40
    assert _per_bucket(Decimal("12000"), Decimal("300")) == 40
    assert _per_bucket(0, 0) == 0
    assert _per_bucket(7, 1) == 7


def test_window_totals_are_not_multiplied_by_the_pair_count():
    """End to end, on a day whose pair count exceeds its order count.

    Four baskets producing six pair rows is the shape that makes the bug
    visible: a resolver that summed `total_orders_in_bucket` straight would
    report 24 orders and a 150% attach rate, both of which are plausible enough
    to ship.
    """
    with sandbox() as (db, generation):
        _basket(db, generation, DAY_FANOUT, [P_A, P_B, P_C, P_D])
        _basket(db, generation, DAY_FANOUT, [P_A, P_B])
        _basket(db, generation, DAY_FANOUT, [P_C])
        _basket(db, generation, DAY_FANOUT, [P_D])
        db.commit()
        _run(db, DAY_FANOUT, generation)

        rows = _rows(db, DAY_FANOUT, generation)
        # C(4,2) = 6 DISTINCT pairs. The second basket repeats {A,B}, so it
        # increments that row rather than adding a seventh — rows are per pair,
        # not per co-occurrence. Six rows against four orders is the shape that
        # makes the multiplication bug visible.
        assert len(rows) == 6
        assert _pair(rows, P_A, P_B).pair_orders == 2
        assert all(r.total_orders_in_bucket == 4 for r in rows)
        assert all(r.orders_with_any_pair == 2 for r in rows)

        envelope = _resolve_view_57_over(db, DAY_FANOUT, DAY_FANOUT + timedelta(days=1))
        attach = envelope.kpis["basket_attach_rate"].value
        # 2 of 4 baskets held more than one distinct product.
        assert attach == Decimal("50.0000"), (
            f"attach rate came back {attach}; a rate above 100 means the "
            "bucket-level scalars were multiplied by the day's pair count"
        )


def test_the_job_is_registered_under_its_name():
    """The recompute queue and the sync-run log both key on this string."""
    assert get_job("basket_pair_daily").__class__ is BasketPairDailyJob


def _statements(sql: str) -> list[str]:
    """DDL statements, normalised: comments out, whitespace collapsed.

    Comparing normalised statements rather than bytes lets the twin file carry
    the explanatory header it is required to have without making the comparison
    fragile.
    """
    without_comments = "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )
    return [
        re.sub(r"\s+", " ", statement).strip()
        for statement in without_comments.split(";")
        if statement.strip()
    ]


def test_twin_sql_matches_the_migration():
    """The hand-applied production file cannot drift from the revision.

    Production applies reviewed SQL and never runs alembic (DEPLOY.md §6), so
    these two artifacts describe the same table twice. If they diverge,
    production 500s on every cross-sell request while CI stays green — the exact
    failure `test_analytics_schema.py` was written for, one table at a time.

    Generated in-process from the revision itself, so the comparison is against
    what alembic actually emits rather than against a transcription of it.
    """
    from alembic import command
    from alembic.config import Config

    buffer = io.StringIO()
    config = Config("alembic.ini")
    # Both, on purpose. `alembic/env.py` does not pass an `output_buffer` to
    # `context.configure`, so offline DDL lands on whatever `sys.stdout` is at
    # the moment the migration runs — which under pytest is the capture plugin's
    # replacement, not `config.stdout`. Setting the config and redirecting the
    # stream covers either behaviour and neither can lose the output silently.
    config.stdout = buffer
    with contextlib.redirect_stdout(buffer):
        command.upgrade(config, f"{MIGRATION_PREVIOUS}:{MIGRATION_REVISION}", sql=True)

    generated = _statements(buffer.getvalue())
    assert generated, (
        "alembic produced no offline SQL for this revision, so the comparison "
        "below would pass vacuously"
    )
    # The alembic_version stamp is deliberately absent from the twin: the shared
    # remote DB is on a different migration lineage and stamping it with a
    # revision id from this repo's chain would corrupt its state.
    generated = [s for s in generated if "alembic_version" not in s]

    twin = _statements(TWIN_SQL.read_text(encoding="utf-8"))

    assert twin == generated, (
        "backend/scripts/sql/2026-07-29_agg_basket_pair_daily.sql no longer "
        f"matches revision {MIGRATION_REVISION}. Regenerate it:\n"
        f"    alembic upgrade {MIGRATION_PREVIOUS}:{MIGRATION_REVISION} --sql\n"
        "and strip the alembic_version statement.\n"
        f"  migration: {generated}\n"
        f"  twin file: {twin}"
    )


def test_twin_sql_still_carries_its_reasoning():
    """The header is the artifact a reviewer reads in the production window.

    A regenerated file that lost it is a wall of DDL applied to a live database
    by somebody who cannot tell what it is for.
    """
    text = TWIN_SQL.read_text(encoding="utf-8")
    for expected in ("alembic_version", "ROLLBACK", "canonical", "lift"):
        assert expected.lower() in text.lower(), (
            f"the twin SQL header no longer mentions {expected!r}"
        )


# ---------------------------------------------------------------------------
# 11. Identity comes from the fact, never from the catalogue
# ---------------------------------------------------------------------------


def test_names_are_the_sale_time_snapshots():
    """The rollup carries what the line fact said, not what the catalogue says.

    Resolving a historical pair's identity through the live catalogue would let
    a rename rewrite every past pair and a delete erase one. The fixture uses
    product ids that exist in no catalogue at all, so a job that joined
    `products` would produce the `'-'` sentinel or no row.
    """
    with sandbox() as (db, generation):
        _basket(db, generation, DAY_FANOUT, [P_A, P_B])
        db.commit()
        _run(db, DAY_FANOUT, generation)

        row = _pair(_rows(db, DAY_FANOUT, generation), P_A, P_B)
        assert row.product_a_name == NAMES[P_A]
        assert row.product_b_name == NAMES[P_B]
        assert row.product_a_name != DIMENSION_UNKNOWN


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_missing_name_snapshot_becomes_the_shared_sentinel(blank: str):
    """NOT NULL with `'-'`, like every other snapshotted string in the schema,
    so unknown identities group together instead of scattering."""
    with sandbox() as (db, generation):
        order = _basket(db, generation, DAY_FANOUT, [P_A, P_B])
        db.execute(
            AnalyticsOrderLine.__table__.update()
            .where(
                AnalyticsOrderLine.order_id == order,
                AnalyticsOrderLine.product_id == P_A,
            )
            .values(product_name_snapshot=blank)
        )
        db.commit()
        _run(db, DAY_FANOUT, generation)

        row = _pair(_rows(db, DAY_FANOUT, generation), P_A, P_B)
        assert row.product_a_name == DIMENSION_UNKNOWN
        assert row.product_b_name == NAMES[P_B]
