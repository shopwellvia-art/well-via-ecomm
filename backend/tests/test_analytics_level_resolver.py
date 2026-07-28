"""Tests for the level-aware resolvers: ``snapshot`` and ``cohort_matrix``.

What actually has to be true here
---------------------------------
Every one of these failures is silent. A level summed across a window and a
cohort denominator counted once per period row both produce a plausible number
that no reader can check, so the tests are written to fail on the *plausible*
answer rather than on an obviously broken one:

  * ``test_snapshot_reports_the_latest_bucket_not_the_sum`` — three snapshot
    days at 100 / 300 / 500. The answer is 500. The bug returns 900, which is a
    perfectly reasonable-looking LTV and is one customer's lifetime value
    counted three times.
  * ``test_no_snapshot_in_window_reports_no_rollup_yet`` — the resolver must not
    reach back to the newest snapshot BEFORE the window. That answer would be a
    real number measured on a real day, and it would silently answer a different
    question from the one asked.
  * ``test_comparison_is_level_against_level`` — the comparison window holds
    100 and 250. The previous value is 250, not 350. Comparing a level against a
    sum makes every delta look like a collapse proportional to the window length.
  * ``test_cohort_size_is_counted_once_per_cohort`` — four period rows carrying
    ``cohort_size = 100`` each. The denominator is 100, not 400; the bug
    quarters retention, which is the direction of error nobody investigates.
  * ``test_empty_cohort_retention_is_null_not_zero`` — a cohort nobody joined
    has no retention rate. 0% would rank a cohort that does not exist as the
    worst performer on the heatmap.

Isolation strategy
------------------
Follows ``test_analytics_resolvers.py``: no db fixture in ``conftest.py``, each
test owns its ``SessionLocal()`` and tears down in a ``finally``.

  1. Fixtures live in **May 2005**, a sandbox no other suite uses (the existing
     ones are June 2008, June 2009, Jan/Jul 2009 and 2011), so nothing else can
     be inside a window here.
  2. Direct-resolver tests write under their **own random ``tz_generation``**,
     which is what ``source_watermark`` filters on, so one test's rollup can
     never move another's watermark.
  3. The end-to-end test cannot pick its generation — ``AnalyticsViewService``
     reads the active one from the database — so it seeds under the active
     generation and isolates by date range instead, deleting its window both
     before seeding and in ``finally`` so a rerun after a crashed run is clean.

Run inside the analytics container:

    docker exec wvana-py python -m pytest tests/test_analytics_level_resolver.py -q
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from typing import Iterator

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.analytics_rollups import (
    AggCustomerCohortMonthly,
    AggCustomerDaily,
    AggCustomerSnapshot,
    AggInventoryDaily,
    AggOrderDaily,
)
from app.repositories.analytics_repository import AnalyticsRepository
from app.schemas.analytics_view import AnalyticsViewEnvelope, WarningCode
from app.services.analytics import registry
from app.services.analytics.filters import (
    AnalyticsFilters,
    Comparison,
    Granularity,
    Period,
)
from app.services.analytics.metric_kind import (
    MetricKind,
    NonAdditive,
    assert_summable,
    classify,
    combine_strategy,
)
from app.services.analytics.resolvers import RESOLVERS, ResolverContext
from app.services.analytics.resolvers.base import NOT_CONFIGURED
from app.services.analytics.resolvers.levels import (
    COHORT_SOURCE,
    LEVEL_AT_INSTANT,
    NOT_COMBINABLE,
    SNAPSHOT_SOURCE,
    CohortMatrixResolver,
    SnapshotResolver,
    latest_bucket_in,
)
from app.services.analytics.resolvers.special import CUSTOM_FUNCTIONS
from app.services.analytics.timebox import active_generation
from app.services.analytics.types import (
    AnalyticsViewDefinition,
    ChartSpec,
    FormatId,
    Freshness,
    ResolverId,
    TableColumn,
    TableSpec,
    ViewState,
)
from app.services.analytics.view_service import AnalyticsViewService

# May 2005 — no other analytics suite writes here, so a window in this month
# contains this suite's rows and nothing else.
SANDBOX_START = date(2005, 5, 1)
SANDBOX_TODAY = date(2005, 5, 11)
SANDBOX_END = date(2005, 5, 31)

#: Tables a test may write to. Teardown clears all of them.
_OWNED_MODELS = (
    AggCustomerSnapshot,
    AggCustomerCohortMonthly,
    AggCustomerDaily,
    AggOrderDaily,
    AggInventoryDaily,
)

#: The customer-retention views this change is responsible for.
CUSTOMER_VIEWS: tuple[tuple[int, str, str], ...] = (
    (9, "customers", "new-vs-returning-customers"),
    (10, "customers", "customer-lifetime-value"),
    (11, "customers", "customer-segmentation"),
    (12, "customers", "cohort-and-retention"),
    (13, "customers", "customer-churn"),
    (59, "customers", "rfm-customer-analysis"),
)

#: Views wired to a rollup today. The rest are deliberately unbound; see
#: `test_unbound_customer_views_declare_no_summing_binding`.
BOUND_CUSTOMER_VIEWS: tuple[tuple[int, str, str], ...] = (
    (9, "customers", "new-vs-returning-customers"),
    (10, "customers", "customer-lifetime-value"),
    (12, "customers", "cohort-and-retention"),
)


def _day(offset: int) -> date:
    return date.fromordinal(SANDBOX_START.toordinal() + offset)


@contextmanager
def sandbox() -> Iterator[tuple[Session, int]]:
    """A session plus a private ``tz_generation``, cleaned up unconditionally."""
    db = SessionLocal()
    # `tz_generation` is a SmallInteger (max 32767). 22000-30999 sits above the
    # 1000-20999 band `test_analytics_resolvers.py` draws from, so two suites
    # running back to back cannot collide on a generation.
    generation = 22000 + (uuid.uuid4().int % 9000)
    try:
        yield db, generation
    finally:
        try:
            db.rollback()
            for model in _OWNED_MODELS:
                db.execute(delete(model).where(model.tz_generation == generation))
            db.commit()
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _view(**kw) -> AnalyticsViewDefinition:
    base = dict(
        number=998,
        name="Level Fixture",
        slug="level-fixture",
        summary="fixture",
        permission="analytics.customers.view",
        resolver=ResolverId.CUSTOM,
        freshness=Freshness.DAILY,
        state=ViewState.LIVE,
    )
    base.update(kw)
    return AnalyticsViewDefinition(**base)  # type: ignore[arg-type]


def _ctx(
    db: Session,
    view: AnalyticsViewDefinition,
    generation: int,
    *,
    days: int = 10,
    comparison: Comparison = Comparison.NONE,
    start: date = SANDBOX_START,
    **filter_kw,
) -> ResolverContext:
    filters = AnalyticsFilters(
        period=Period.CUSTOM,
        date_from=start,
        date_to=date.fromordinal(start.toordinal() + days),
        comparison=comparison,
        **filter_kw,
    )
    return ResolverContext(
        db=db,
        repo=AnalyticsRepository(db),
        view=view,
        filters=filters,
        window=filters.resolve(SANDBOX_TODAY),
        tz_generation=generation,
        today=SANDBOX_TODAY,
    )


def _snapshot(
    db: Session,
    generation: int,
    day: date,
    customer: str,
    *,
    gross_ltv: str = "0",
    orders_count: int = 1,
    segment: str = "champions",
    churn_band: str = "low",
    is_active: bool = True,
) -> None:
    db.add(
        AggCustomerSnapshot(
            bucket_date=day,
            tz_generation=generation,
            customer_key=customer,
            orders_count=orders_count,
            units=orders_count,
            gross_ltv=Decimal(gross_ltv),
            net_ltv=Decimal(gross_ltv),
            margin_ltv=Decimal(gross_ltv),
            aov=Decimal(gross_ltv) / orders_count if orders_count else Decimal("0"),
            recency_days=3,
            frequency=orders_count,
            monetary=Decimal(gross_ltv),
            r_score=5,
            f_score=4,
            m_score=4,
            rfm_segment=segment,
            cohort_month="2005-01",
            tenure_days=30,
            is_active=is_active,
            churn_risk_band=churn_band,
            preferred_payment_method="upi",
            quality="AUTHORITATIVE",
        )
    )


def _cohort_cell(
    db: Session,
    generation: int,
    cohort_month: str,
    period_index: int,
    *,
    cohort_size: int,
    active_customers: int,
    orders: int = 0,
    revenue: str = "0",
) -> None:
    db.add(
        AggCustomerCohortMonthly(
            bucket_date=_day(period_index),
            tz_generation=generation,
            cohort_month=cohort_month,
            period_index=period_index,
            cohort_size=cohort_size,
            active_customers=active_customers,
            orders=orders,
            revenue=Decimal(revenue),
        )
    )


_LTV_VIEW = _view(kpis=("gross_ltv",))

_SEGMENT_VIEW = _view(
    kpis=("gross_ltv",),
    charts=(
        ChartSpec(
            id="segment_ltv",
            title="LTV by segment",
            type="hbar",
            x="segment",
            series=("gross_ltv", "lifetime_orders"),
            format=FormatId.MONEY,
        ),
    ),
)

_TREND_VIEW = _view(
    kpis=("gross_ltv",),
    charts=(
        ChartSpec(
            id="ltv_trend",
            title="Book LTV",
            type="line",
            x="date",
            series=("gross_ltv",),
            format=FormatId.MONEY,
        ),
    ),
)

_COHORT_VIEW = _view(
    kpis=("ltv",),
    charts=(
        ChartSpec(
            id="retention_curve",
            title="Retention",
            type="line",
            x="month",
            series=("retention_pct",),
            format=FormatId.PCT,
        ),
    ),
    tables=(
        TableSpec(
            id="cohort_grid",
            title="Cohorts",
            columns=(
                TableColumn(key="cohort_month", label="Cohort"),
                TableColumn(key="period_index", label="Month", format=FormatId.INT),
                TableColumn(key="retention_pct", label="Retention", format=FormatId.PCT),
            ),
        ),
    ),
)


# ---------------------------------------------------------------------------
# 1. A level's window value is its latest bucket, never its sum
# ---------------------------------------------------------------------------


def test_snapshot_reports_the_latest_bucket_not_the_sum():
    """Three snapshot days at 100 / 300 / 500 answer 500, not 900.

    900 is the failure this resolver exists to prevent: it is one customer's
    lifetime value added to itself once per day in range, it looks like a
    healthy book, and nothing downstream can tell it apart from one.
    """
    with sandbox() as (db, generation):
        _snapshot(db, generation, _day(1), "c1", gross_ltv="100")
        _snapshot(db, generation, _day(2), "c1", gross_ltv="300")
        _snapshot(db, generation, _day(3), "c1", gross_ltv="500")
        db.commit()

        result = SnapshotResolver().run(_ctx(db, _LTV_VIEW, generation))

        value = result.kpis["gross_ltv"].value
        assert value == Decimal("500"), "the level at the latest bucket in the window"
        assert value != Decimal("900"), "the sum of the window's days is not the level"


def test_the_instant_is_named_on_every_level_answer():
    """A level without its as-at date is not interpretable."""
    with sandbox() as (db, generation):
        _snapshot(db, generation, _day(1), "c1", gross_ltv="100")
        _snapshot(db, generation, _day(4), "c1", gross_ltv="700")
        db.commit()

        result = SnapshotResolver().run(_ctx(db, _LTV_VIEW, generation))

        stated = [w for w in result.warnings if w.code == LEVEL_AT_INSTANT]
        assert stated, "a level answer must say which instant it was measured at"
        assert stated[0].detail["as_at"] == _day(4).isoformat()


def test_latest_bucket_is_expressible_through_the_repository():
    """"Latest row per key in a window" needs no bespoke SQL.

    Group by the bucket, order by it descending, take one — then pin that date
    back onto the next read. Asserted directly because it is the single
    repository capability this whole resolver rests on.
    """
    with sandbox() as (db, generation):
        _snapshot(db, generation, _day(1), "c1", gross_ltv="100")
        _snapshot(db, generation, _day(6), "c1", gross_ltv="600")
        _snapshot(db, generation, _day(9), "c1", gross_ltv="900")
        db.commit()

        ctx = _ctx(db, _LTV_VIEW, generation, days=8)
        # The window is [1st, 9th); the 9th's row is outside it and must not win.
        assert latest_bucket_in(ctx, SNAPSHOT_SOURCE, ctx.window) == _day(6)


# ---------------------------------------------------------------------------
# 2. An empty window says so, and never reaches outside itself
# ---------------------------------------------------------------------------


def test_no_snapshot_in_window_reports_no_rollup_yet():
    """A window with no snapshot has no level to report — and no fallback.

    The rows seeded here are real, measured and just outside the window. Using
    them would produce a number that is correct about a day nobody asked about,
    which is indistinguishable in the response from one that is correct about
    the day they did.
    """
    with sandbox() as (db, generation):
        before = date.fromordinal(SANDBOX_START.toordinal() - 3)
        _snapshot(db, generation, before, "c1", gross_ltv="4242")
        db.commit()

        result = SnapshotResolver().run(_ctx(db, _TREND_VIEW, generation))

        codes = {w.code for w in result.warnings}
        assert WarningCode.NO_ROLLUP_YET in codes

        kpi = result.kpis["gross_ltv"]
        assert kpi.value is None, "no instant in the window means no level"
        assert kpi.value != Decimal("4242"), "the bucket before the window is not used"
        assert kpi.inputs_missing, "the missing input must be named, not implied"
        assert not result.series and not result.tables
        # Provenance is not optional even when there is nothing to report: an
        # empty `sources` list is this subsystem's way of saying "not wired up",
        # which is a different fact from "wired up and empty for this window".
        assert [s.id for s in result.sources] == [SNAPSHOT_SOURCE]


def test_a_completely_empty_source_is_also_no_rollup_yet():
    with sandbox() as (db, generation):
        result = SnapshotResolver().run(_ctx(db, _LTV_VIEW, generation))

        assert WarningCode.NO_ROLLUP_YET in {w.code for w in result.warnings}
        assert result.kpis["gross_ltv"].value is None


# ---------------------------------------------------------------------------
# 3. Grouping the population at one instant IS a legitimate sum
# ---------------------------------------------------------------------------


def test_breakdown_by_rfm_segment_sums_across_customers_at_one_instant():
    """Across customers on one day, not across days.

    At a single `bucket_date` each customer holds exactly one row, so grouping
    partitions the population. Day one's rows exist purely to be excluded: if
    the `bucket_date` pin were dropped, champions would read 3,300 instead of
    3,000 and would still look like a plausible segment total.
    """
    with sandbox() as (db, generation):
        for day, (a, b, c) in ((_day(1), ("100", "200", "50")), (_day(2), ("1000", "2000", "500"))):
            _snapshot(db, generation, day, "c1", gross_ltv=a, segment="champions")
            _snapshot(db, generation, day, "c2", gross_ltv=b, segment="champions")
            _snapshot(db, generation, day, "c3", gross_ltv=c, segment="at_risk")
        db.commit()

        result = SnapshotResolver().run(_ctx(db, _SEGMENT_VIEW, generation))

        rows = {r["segment"]: r for r in result.series["segment_ltv"]}
        assert rows["champions"]["gross_ltv"] == Decimal("3000")
        assert rows["at_risk"]["gross_ltv"] == Decimal("500")
        assert rows["champions"]["gross_ltv"] != Decimal("3300"), (
            "day one's rows must not be added to day two's"
        )
        # Two customers in champions, one in at_risk, each counted once.
        assert rows["champions"]["lifetime_orders"] == Decimal("2")
        assert rows["at_risk"]["lifetime_orders"] == Decimal("1")
        assert {r["as_at"] for r in result.series["segment_ltv"]} == {
            _day(2).isoformat()
        }


def test_a_level_series_takes_each_bucket_s_last_day_and_leaves_gaps_empty():
    """Re-bucketing a level to a week is the same error, one order smaller.

    A week's value is its last day. And a bucket with no snapshot is left OUT —
    a zero there would claim the entire customer base momentarily had no
    lifetime value, which `core.dense_points` would legitimately write for a
    flow and must never write for a level.
    """
    with sandbox() as (db, generation):
        # 1 May 2005 is a Sunday, so offsets 1-2 are the Mon/Tue of one calendar
        # week and 8-9 are the Mon/Tue of the next. Chosen deliberately: a
        # fixture that straddles a week boundary would make this test about the
        # calendar rather than about the combination rule.
        _snapshot(db, generation, _day(1), "c1", gross_ltv="100")
        _snapshot(db, generation, _day(2), "c1", gross_ltv="200")
        _snapshot(db, generation, _day(8), "c1", gross_ltv="800")
        _snapshot(db, generation, _day(9), "c1", gross_ltv="900")
        db.commit()

        ctx = _ctx(db, _TREND_VIEW, generation, days=14, granularity=Granularity.WEEK)
        points = SnapshotResolver().run(ctx).series["ltv_trend"]

        values = [p["gross_ltv"] for p in points]
        assert values == [Decimal("200"), Decimal("900")], (
            "each week is its latest day, not the sum of its days"
        )
        assert Decimal("300") not in values and Decimal("1700") not in values

        daily = SnapshotResolver().run(_ctx(db, _TREND_VIEW, generation, days=14))
        drawn = [p["date"] for p in daily.series["ltv_trend"]]
        assert drawn == [_day(i).isoformat() for i in (1, 2, 8, 9)], (
            "days 3-7 were never snapshotted; a level of zero there is a claim"
        )


# ---------------------------------------------------------------------------
# 4. The guard itself
# ---------------------------------------------------------------------------


def test_assert_summable_refuses_a_level_column():
    """The taxonomy, asserted directly, so the resolver's routing has a premise."""
    assert classify("gross_ltv", source=SNAPSHOT_SOURCE) is MetricKind.LEVEL
    assert combine_strategy("gross_ltv", source=SNAPSHOT_SOURCE) == "latest"
    with pytest.raises(NonAdditive) as exc:
        assert_summable("gross_ltv", source=SNAPSHOT_SOURCE)
    assert "level" in str(exc.value)

    # The source overrides the column name: nothing on a per-customer snapshot
    # is a flow, whatever it is called.
    assert combine_strategy("units", source=SNAPSHOT_SOURCE) == "latest"


def test_the_snapshot_resolver_never_attempts_to_sum_a_level(monkeypatch):
    """It must not reach the guard at all on a level source.

    Catching `NonAdditive` and recovering would be the wrong shape: the point is
    that the level path never asks to sum, not that it survives asking.
    """
    from app.services.analytics.resolvers import levels

    calls: list[tuple[str, str | None]] = []

    def _spy(column: str, *, source: str | None = None) -> None:
        calls.append((column, source))
        assert_summable(column, source=source)

    monkeypatch.setattr(levels, "assert_summable", _spy)

    with sandbox() as (db, generation):
        _snapshot(db, generation, _day(1), "c1", gross_ltv="100")
        _snapshot(db, generation, _day(2), "c1", gross_ltv="300")
        db.commit()

        result = levels.SnapshotResolver().run(_ctx(db, _SEGMENT_VIEW, generation))

    assert result.kpis["gross_ltv"].value == Decimal("300")
    assert calls == [], f"a level reached the SUM guard: {calls}"


def test_a_flow_on_a_level_resolver_is_still_summed_and_still_guarded(monkeypatch):
    """Pointed at a flow column, the same resolver sums the window — and proves it may.

    `agg_inventory_daily.units_sold` is a flow, so its window value IS the sum.
    This is the one path in the module that adds buckets together, so it is the
    one path that calls `assert_summable` first.
    """
    from app.services.analytics.resolvers import levels

    calls: list[str] = []
    monkeypatch.setattr(
        levels,
        "assert_summable",
        lambda column, *, source=None: calls.append(column),
    )

    view = _view(
        kpis=("units_sold",),
        params={
            "source": "agg_inventory_daily",
            "metrics": {"units_sold": {"add": ["units_sold"]}},
        },
    )
    with sandbox() as (db, generation):
        for offset in (1, 2, 3):
            db.add(
                AggInventoryDaily(
                    bucket_date=_day(offset),
                    tz_generation=generation,
                    product_id=1,
                    sku_snapshot="SKU-1",
                    stock_close=100,
                    stock_value_close=Decimal("500.00"),
                    units_sold=7,
                    units_restocked=0,
                )
            )
        db.commit()

        result = levels.SnapshotResolver().run(_ctx(db, view, generation))

    assert result.kpis["units_sold"].value == Decimal("21"), "3 days x 7 units"
    assert calls == ["units_sold"], "the flow path must prove it may sum"


def _inventory_day(db: Session, generation: int, day: date, *, sold: int, stock: int) -> None:
    db.add(
        AggInventoryDaily(
            bucket_date=day,
            tz_generation=generation,
            product_id=1,
            sku_snapshot="SKU-1",
            stock_close=stock,
            stock_value_close=Decimal(stock),
            units_sold=sold,
            units_restocked=0,
        )
    )


_MIXED_SERIES_VIEW = _view(
    charts=(
        ChartSpec(
            id="stock_and_sales",
            title="Stock and sales",
            type="line",
            x="date",
            series=("on_hand", "units_out"),
        ),
    ),
    params={
        "source": "agg_inventory_daily",
        "metrics": {
            "on_hand": {"add": ["stock_close"]},
            "units_out": {"add": ["units_sold"]},
        },
    },
)


def test_one_bucket_combines_a_level_and_a_flow_by_two_different_rules():
    """A week's stock is its last day; a week's sales are its total.

    Both lines are drawn from the same three rows of the same bucket. Applying
    either rule to both metrics is wrong in one direction or the other, and both
    directions look plausible on a chart.
    """
    with sandbox() as (db, generation):
        # 2-4 May 2005 are Mon-Wed, one calendar week.
        _inventory_day(db, generation, _day(1), sold=1, stock=100)
        _inventory_day(db, generation, _day(2), sold=2, stock=90)
        _inventory_day(db, generation, _day(3), sold=4, stock=80)
        db.commit()

        ctx = _ctx(
            db, _MIXED_SERIES_VIEW, generation, days=7, granularity=Granularity.WEEK
        )
        points = SnapshotResolver().run(ctx).series["stock_and_sales"]

        assert len(points) == 1
        assert points[0]["on_hand"] == Decimal("80"), "the level is the last day"
        assert points[0]["on_hand"] != Decimal("270"), "levels are not added up"
        assert points[0]["units_out"] == Decimal("7"), "the flow is the bucket's sum"
        assert points[0]["as_at"] == _day(3).isoformat()


def test_a_flow_is_left_out_of_an_at_an_instant_breakdown():
    """A breakdown pinned to one day cannot carry a window's flow.

    One day's sales under a window's label understates them by roughly the
    window length. `core.BreakdownResolver` splits a flow by a dimension
    correctly; this one says so instead of printing the small number.
    """
    view = _view(
        charts=(
            ChartSpec(
                id="by_sku",
                title="By SKU",
                type="hbar",
                x="sku",
                series=("on_hand", "units_out"),
            ),
        ),
        params=_MIXED_SERIES_VIEW.params,
    )
    with sandbox() as (db, generation):
        _inventory_day(db, generation, _day(1), sold=1, stock=100)
        _inventory_day(db, generation, _day(2), sold=2, stock=90)
        db.commit()

        result = SnapshotResolver().run(_ctx(db, view, generation))

        rows = result.series["by_sku"]
        assert rows[0]["on_hand"] == Decimal("90"), "the latest day's stock"
        assert "units_out" not in rows[0], "a flow must not be pinned to one day"
        assert NOT_COMBINABLE in {w.code for w in result.warnings}


def test_mixing_a_level_and_a_flow_in_one_binding_is_refused():
    """One query cannot pin an instant and span a window at the same time.

    Picking either rule silently mislabels half the answer, so the metric is
    reported unavailable with the reason named instead.
    """
    view = _view(
        kpis=("mixed",),
        params={
            "source": "agg_inventory_daily",
            "metrics": {"mixed": {"add": ["stock_close", "units_sold"]}},
        },
    )
    with sandbox() as (db, generation):
        db.add(
            AggInventoryDaily(
                bucket_date=_day(1),
                tz_generation=generation,
                product_id=1,
                sku_snapshot="SKU-1",
                stock_close=100,
                stock_value_close=Decimal("500.00"),
                units_sold=7,
                units_restocked=0,
            )
        )
        db.commit()

        result = SnapshotResolver().run(_ctx(db, view, generation))

    assert result.kpis["mixed"].value is None
    assert result.kpis["mixed"].inputs_missing
    assert NOT_COMBINABLE in {w.code for w in result.warnings}


# ---------------------------------------------------------------------------
# 5. Deltas compare like with like
# ---------------------------------------------------------------------------


def test_comparison_is_level_against_level():
    """Previous is the comparison window's own latest bucket, not its sum.

    The comparison window holds 100 then 250. A level-vs-sum comparison would
    report 350 and turn a doubling into a 43% rise — an error that scales with
    the window length and always points the same way.
    """
    with sandbox() as (db, generation):
        _snapshot(db, generation, _day(2), "c1", gross_ltv="500")
        _snapshot(db, generation, date.fromordinal(SANDBOX_START.toordinal() - 6), "c1", gross_ltv="100")
        _snapshot(db, generation, date.fromordinal(SANDBOX_START.toordinal() - 1), "c1", gross_ltv="250")
        db.commit()

        ctx = _ctx(db, _LTV_VIEW, generation, comparison=Comparison.PREVIOUS_PERIOD)
        kpi = SnapshotResolver().run(ctx).kpis["gross_ltv"]

        assert kpi.value == Decimal("500")
        assert kpi.previous == Decimal("250"), "the level at the comparison instant"
        assert kpi.previous != Decimal("350"), "not the comparison window's sum"
        assert kpi.delta_pct == Decimal("100.0")


def test_a_comparison_window_with_no_snapshot_has_no_previous():
    """No instant to compare against is None, not zero — a delta from a
    fabricated zero renders as infinite growth."""
    with sandbox() as (db, generation):
        _snapshot(db, generation, _day(2), "c1", gross_ltv="500")
        db.commit()

        ctx = _ctx(db, _LTV_VIEW, generation, comparison=Comparison.PREVIOUS_PERIOD)
        kpi = SnapshotResolver().run(ctx).kpis["gross_ltv"]

        assert kpi.value == Decimal("500")
        assert kpi.previous is None
        assert kpi.delta_pct is None


def test_ltv_per_customer_names_the_input_the_repository_cannot_give():
    """A per-customer average needs COUNT(DISTINCT customer). Nothing offers it.

    `fetch_rollup` projects columns or SUMs them; there is no COUNT aggregate,
    and a resolver may not write its own SELECT. Reporting SUM(gross_ltv) under
    the `ltv` label instead would publish the whole book's value as one
    customer's — so the card is null and says what is missing.
    """
    with sandbox() as (db, generation):
        _snapshot(db, generation, _day(1), "c1", gross_ltv="100")
        _snapshot(db, generation, _day(1), "c2", gross_ltv="300")
        db.commit()

        result = SnapshotResolver().run(_ctx(db, _view(kpis=("ltv",)), generation))

        kpi = result.kpis["ltv"]
        assert kpi.value is None
        assert any("count" in m.lower() for m in kpi.inputs_missing)


# ---------------------------------------------------------------------------
# 6 + 7. The cohort matrix
# ---------------------------------------------------------------------------


def _seed_cohorts(db: Session, generation: int) -> None:
    """One four-period cohort of 100, plus a cohort nobody joined."""
    for period, active in enumerate((100, 50, 25, 10)):
        _cohort_cell(
            db,
            generation,
            "2005-01",
            period,
            # Repeated on every row, exactly as the aggregation job writes it.
            cohort_size=100,
            active_customers=active,
            orders=active,
            revenue=str(active * 10),
        )
    _cohort_cell(db, generation, "2005-02", 0, cohort_size=0, active_customers=0)
    db.commit()


def test_cohort_size_is_counted_once_per_cohort():
    """Four period rows carrying 100 make a denominator of 100, not 400.

    The bug quarters retention here. It is invisible: 12.5% month-1 retention
    reads as a bad month rather than as arithmetic.
    """
    with sandbox() as (db, generation):
        _seed_cohorts(db, generation)

        result = CohortMatrixResolver().run(_ctx(db, _COHORT_VIEW, generation))
        grid = result.tables["cohort_grid"].rows

        january = [r for r in grid if r["cohort_month"] == "2005-01"]
        assert len(january) == 4
        assert {r["cohort_size"] for r in january} == {100}, (
            "cohort_size belongs to the cohort, not to the cell"
        )
        assert sum(r["cohort_size"] for r in january) == 400, (
            "the four copies are still in the data; the resolver must not add them"
        )

        by_period = {r["period_index"]: r for r in january}
        assert by_period[0]["retention_pct"] == Decimal("100.0000")
        assert by_period[1]["retention_pct"] == Decimal("50.0000")
        assert by_period[1]["retention_pct"] != Decimal("12.5000"), (
            "the denominator was multiplied by the number of period rows"
        )
        assert by_period[3]["retention_pct"] == Decimal("10.0000")


def test_the_pooled_curve_counts_each_cohort_once_per_period():
    """Pooling across cohorts is a disjoint union; pooling across periods is not.

    A customer belongs to exactly one cohort, so cohorts at the same
    `period_index` can be added. The same customer appears in every period row
    of their own cohort, which is what makes the other axis a multiplication.
    """
    with sandbox() as (db, generation):
        _seed_cohorts(db, generation)

        result = CohortMatrixResolver().run(_ctx(db, _COHORT_VIEW, generation))
        curve = {p["month"]: p for p in result.series["retention_curve"]}

        # Month 0 pools the 100-customer cohort with the empty one: 100 / 100.
        assert curve[0]["cohort_size"] == 100
        assert curve[0]["retention_pct"] == Decimal("100.0000")
        assert curve[3]["cohort_size"] == 100, "not 400"
        assert curve[3]["retention_pct"] == Decimal("10.0000")


def test_empty_cohort_retention_is_null_not_zero():
    """A cohort nobody joined has no retention rate.

    0% would put a cohort that does not exist on the heatmap as the
    worst-performing one, and a heatmap sorts by exactly that number.
    """
    with sandbox() as (db, generation):
        _seed_cohorts(db, generation)

        result = CohortMatrixResolver().run(_ctx(db, _COHORT_VIEW, generation))
        empty = [r for r in result.tables["cohort_grid"].rows if r["cohort_month"] == "2005-02"]

        assert len(empty) == 1
        assert empty[0]["cohort_size"] == 0
        assert empty[0]["retention_pct"] is None
        assert empty[0]["retention_pct"] != Decimal("0"), "null, not a measured 0%"
        assert empty[0]["retention_pct"] != 0


def test_a_cohort_whose_rows_disagree_is_reported_not_averaged():
    """`cohort_size` is constant by definition, so disagreement is a job bug."""
    with sandbox() as (db, generation):
        _cohort_cell(db, generation, "2005-03", 0, cohort_size=100, active_customers=100)
        _cohort_cell(db, generation, "2005-03", 1, cohort_size=90, active_customers=45)
        db.commit()

        result = CohortMatrixResolver().run(_ctx(db, _COHORT_VIEW, generation))

        assert NOT_COMBINABLE in {w.code for w in result.warnings}
        assert {r["cohort_size"] for r in result.tables["cohort_grid"].rows} == {100}


def test_cohort_matrix_with_no_rows_reports_provenance_and_nothing_else():
    with sandbox() as (db, generation):
        result = CohortMatrixResolver().run(_ctx(db, _COHORT_VIEW, generation))

        assert not result.tables and not result.series
        assert [s.id for s in result.sources] == [COHORT_SOURCE]
        assert WarningCode.NO_ROLLUP_YET in {w.code for w in result.warnings}


# ---------------------------------------------------------------------------
# 8. Registration and dispatch
# ---------------------------------------------------------------------------


def test_both_resolvers_are_reachable_through_the_custom_dispatch_table():
    """Registered where a registry view can actually name them.

    Not in `RESOLVERS`: that dict is asserted to be exactly the `ResolverId`
    enum, and neither of these has an enum member yet. `custom` is the
    server-trusted escape hatch that already exists for a shape the enum does
    not name.
    """
    assert set(CUSTOM_FUNCTIONS) >= {"snapshot", "cohort_matrix"}
    assert set(RESOLVERS) == {r.value for r in ResolverId}, (
        "adding an id to RESOLVERS without an enum member breaks "
        "test_analytics_resolvers.test_every_resolver_id_in_the_enum_is_registered"
    )


def test_custom_dispatch_runs_the_snapshot_resolver():
    view = _view(kpis=("gross_ltv",), params={"fn": "snapshot"})
    with sandbox() as (db, generation):
        _snapshot(db, generation, _day(1), "c1", gross_ltv="100")
        _snapshot(db, generation, _day(3), "c1", gross_ltv="700")
        db.commit()

        result = RESOLVERS["custom"].run(_ctx(db, view, generation))

        assert result.kpis["gross_ltv"].value == Decimal("700")


# ---------------------------------------------------------------------------
# 9. The registry bindings, end to end
# ---------------------------------------------------------------------------


class _CustomerReader:
    """Holds exactly `analytics.customers.view` — no admin short circuit."""

    is_admin = False

    def has_permission(self, permission: str) -> bool:
        return permission == "analytics.customers.view"


@contextmanager
def live_sandbox() -> Iterator[Session]:
    """The ACTIVE generation, isolated by date range instead.

    `AnalyticsViewService` reads the generation from the database and cannot be
    told otherwise, so isolation here is May 2005. The window is cleared before
    seeding as well as after, so a rerun following a crashed run is clean rather
    than a duplicate-key error.
    """
    db = SessionLocal()
    generation = int(active_generation(db).generation)

    def _clear() -> None:
        for model in _OWNED_MODELS:
            db.execute(
                delete(model).where(
                    model.tz_generation == generation,
                    model.bucket_date >= SANDBOX_START,
                    model.bucket_date <= SANDBOX_END,
                )
            )
        db.commit()

    try:
        db.rollback()
        _clear()
        for offset in range(5):
            day = _day(offset)
            db.add(
                AggOrderDaily(
                    bucket_date=day,
                    tz_generation=generation,
                    orders_total=10,
                    orders_paid=6,
                    orders_shipped=2,
                    orders_delivered=2,
                    order_value_created=Decimal("1000.00"),
                    paid_order_value=Decimal("800.00"),
                    gross_merchandise_sales=Decimal("900.00"),
                    net_merchandise_sales=Decimal("850.00"),
                    net_revenue=Decimal("930.00"),
                    tax_sum=Decimal("50.00"),
                    discount_sum=Decimal("50.00"),
                    shipping_income=Decimal("30.00"),
                    units=20,
                    costed_units=20,
                    new_customers=5,
                    returning_customers=3,
                )
            )
            db.add(
                AggCustomerDaily(
                    bucket_date=day,
                    tz_generation=generation,
                    new_customers=5,
                    returning_customers=3,
                    active_customers=8,
                    orders_new=5,
                    orders_returning=5,
                    revenue_new=Decimal("500.00"),
                    revenue_returning=Decimal("430.00"),
                )
            )
        _seed_cohorts(db, generation)
        db.commit()
        yield db
    finally:
        try:
            db.rollback()
            _clear()
        finally:
            db.close()


def test_bound_customer_views_resolve_end_to_end():
    """Every customer view carrying a binding must answer with data and provenance.

    `sources` is the machine-readable half: `not_configured` returns an
    explicitly EMPTY list, so a non-empty one is the difference between "nothing
    is wired up" and "wired up, and here is the number".
    """
    filters = AnalyticsFilters(
        period=Period.CUSTOM,
        date_from=SANDBOX_START,
        date_to=_day(5),
        comparison=Comparison.NONE,
    )
    with live_sandbox() as db:
        service = AnalyticsViewService(db, _CustomerReader())
        for number, module_slug, view_slug in BOUND_CUSTOMER_VIEWS:
            envelope = service.resolve_view(
                module_slug, view_slug, filters, use_cache=False
            )
            assert isinstance(envelope, AnalyticsViewEnvelope), (
                f"View {number} ({view_slug}) returned a gated envelope"
            )
            assert envelope.sources, (
                f"View {number} ({view_slug}) reported no provenance, which is how "
                "this subsystem says 'nothing is wired up'."
            )
            codes = {w.code for w in envelope.warnings}
            assert NOT_CONFIGURED not in codes, (
                f"View {number} ({view_slug}) reports NOT_CONFIGURED: "
                + "; ".join(
                    w.message for w in envelope.warnings if w.code == NOT_CONFIGURED
                )
            )
            assert envelope.kpis or envelope.series or envelope.tables


def test_unbound_customer_views_declare_no_summing_binding():
    """The unwired ones must stay unwired rather than acquire a plausible sum.

    Views 11, 13 and 59 read `agg_customer_snapshot`, where EVERY measure is a
    level. `params["metrics"]` is consumed by the generic resolvers, which sum
    it over the window, so any binding written there would be wrong by the
    number of snapshot days in range whatever resolver the view later uses.
    A correct wiring needs no `metrics` block at all — `SNAPSHOT_BINDINGS` lives
    in code precisely so a level is only reachable through the path that pins an
    instant first — so this test survives that change and fails the shortcut.
    """
    for number, module_slug, view_slug in CUSTOMER_VIEWS:
        view = registry.get_view(module_slug, view_slug)
        assert view is not None
        if (number, module_slug, view_slug) in BOUND_CUSTOMER_VIEWS:
            continue
        assert not view.params.get("metrics"), (
            f"View {number} ({view_slug}) declares params['metrics'] "
            f"{view.params.get('metrics')!r}. Its rollup stores levels, and every "
            "column named there is summed across the window."
        )


def test_snapshot_backed_views_never_name_a_level_in_params():
    """Restated across the whole registry for the two level rollups.

    A companion to the peer suite's `test_no_non_additive_column_is_ever_summed`,
    narrowed to the two tables this change is about and stated in terms of what
    goes wrong rather than of a column list.
    """
    from app.repositories.analytics_repository import measures_for

    banned = {
        SNAPSHOT_SOURCE: set(measures_for(SNAPSHOT_SOURCE)),
        COHORT_SOURCE: {"cohort_size"},
    }
    for view in registry.all_views():
        source = view.params.get("source")
        for metric_id, spec in (view.params.get("metrics") or {}).items():
            metric_source = spec.get("source", source) if isinstance(spec, dict) else source
            forbidden = banned.get(metric_source or "", set())
            columns = (
                [spec]
                if isinstance(spec, str)
                else [c for key in ("add", "sub", "over", "over_add", "over_sub")
                      for c in (spec.get(key) or ())]
            )
            offenders = sorted(set(columns) & forbidden)
            assert not offenders, (
                f"View {view.number} ({view.slug}) sums {metric_source}.{offenders} "
                f"for {metric_id!r}. Those are levels or a repeated denominator; "
                "the snapshot resolver reads them at an instant instead."
            )
