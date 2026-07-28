"""Tests for the analytics resolver engine.

What actually has to be true here
---------------------------------
Most of these resolvers are arithmetic, and arithmetic that is wrong fails
loudly the first time somebody reads the dashboard. The tests carrying real
weight are the ones about *not showing a number*, because those failures are
silent and permanent:

  * ``test_timeseries_does_not_densify_past_the_watermark`` — three days of
    rollup, a ten-day request. The series must be three points long. Ten points
    with seven zeros is a chart that says "you sold nothing all week", which is
    indistinguishable from the truth at a glance and is not the truth.
  * ``test_timeseries_densifies_a_genuine_internal_gap`` — the mirror image. A
    day *inside* the aggregated range with no orders really is a zero, and
    dropping it would leave a chart that silently skips a bad day.
  * ``test_kpi_with_missing_input_is_null_not_zero`` — a margin with no cost
    data must be null. A zero COGS reports 100% margin, which reads *better*
    than reality: the one direction of error nobody ever investigates.
  * ``test_empty_source_reports_no_rollup_yet`` — an unbuilt rollup must not
    render as a measured zero anywhere on the response.
  * ``test_mixed_tz_generations_are_refused`` — two day-boundary calendars
    summed together produce a number that is wrong in a way no later check can
    detect, because the rows look identical.

Isolation strategy
------------------
Two layers, because the shared throwaway MySQL already holds rows:

  1. Every fixture lives in **June 2009**, years before this store's first
     order, so assertions are absolute rather than deltas.
  2. Every test writes under its **own random ``tz_generation``**. That is what
     ``source_watermark`` filters on, so one test's rollup can never move
     another's watermark — which is the one value these tests are actually
     about.

No db fixture exists in ``conftest.py``; each test owns its ``SessionLocal()``
and tears down in ``finally`` via the ``sandbox()`` context manager, which
deletes every row it wrote by generation.
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
    AggFunnelDaily,
    AggGeoDaily,
    AggOrderDaily,
    AggProductDaily,
    AggShipmentDaily,
)
from app.repositories.analytics_repository import AnalyticsRepository
from app.schemas.analytics_view import KpiValue, WarningCode
from app.services.analytics.filters import AnalyticsFilters, Comparison, Period
from app.services.analytics.resolvers import (
    RESOLVERS,
    NOT_CONFIGURED,
    ResolverContext,
    ResolverError,
    ResolverResult,
)
from app.services.analytics.resolvers.core import (
    METRIC_NOT_BOUND,
    BreakdownResolver,
    MetricsResolver,
    TableResolver,
    TimeseriesResolver,
)
from app.services.analytics.resolvers.special import (
    CustomResolver,
    FunnelResolver,
    ReconciliationResolver,
    TrackingHealthResolver,
)
from app.services.analytics.timebox import TzGenerationMixed
from app.services.analytics.types import (
    AnalyticsViewDefinition,
    ChartSpec,
    FilterKey,
    FormatId,
    Freshness,
    MetricQuality,
    ResolverId,
    TableColumn,
    TableSpec,
    ViewState,
)

# June 2009 — long before this store's first order, so nothing else can be in
# the window and every assertion can be absolute.
SANDBOX_START = date(2009, 6, 1)
SANDBOX_TODAY = date(2009, 6, 11)

#: Tables a test may write to. Teardown clears all of them by generation, so a
#: test that adds a new fixture table only has to list it here.
_OWNED_MODELS = (
    AggOrderDaily,
    AggProductDaily,
    AggFunnelDaily,
    AggGeoDaily,
    AggShipmentDaily,
    AggCustomerCohortMonthly,
)


@contextmanager
def sandbox() -> Iterator[tuple[Session, int]]:
    """A session plus a private ``tz_generation``, cleaned up unconditionally.

    The generation is the isolation key: ``source_watermark`` filters on it, and
    the watermark is the single value most of these tests turn on. Sharing a
    generation with another test would make "the rollup stops on the 3rd" depend
    on execution order.
    """
    db = SessionLocal()
    generation = 1000 + (uuid.uuid4().int % 20000)
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


def _view(resolver: ResolverId, **kw) -> AnalyticsViewDefinition:
    base = dict(
        number=999,
        name="Test View",
        slug="test-view",
        summary="fixture",
        permission="analytics.executive.view",
        resolver=resolver,
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
    **filter_kw,
) -> ResolverContext:
    filters = AnalyticsFilters(
        period=Period.CUSTOM,
        date_from=SANDBOX_START,
        date_to=date.fromordinal(SANDBOX_START.toordinal() + days),
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


def _order_day(db: Session, generation: int, day: date, **columns) -> None:
    db.add(AggOrderDaily(bucket_date=day, tz_generation=generation, **columns))


def _day(offset: int) -> date:
    return date.fromordinal(SANDBOX_START.toordinal() + offset)


# ---------------------------------------------------------------------------
# 1. metrics: deltas, and the undefined delta from zero
# ---------------------------------------------------------------------------


def test_metrics_computes_delta_against_the_comparison_window():
    view = _view(ResolverId.METRICS, kpis=("net_revenue", "orders_count"))
    with sandbox() as (db, generation):
        # Current window: 1000. Comparison window (the ten days before it): 500.
        _order_day(db, generation, _day(1), net_revenue=Decimal("1000.00"), orders_total=4)
        _order_day(
            db,
            generation,
            date.fromordinal(SANDBOX_START.toordinal() - 5),
            net_revenue=Decimal("500.00"),
            orders_total=2,
        )
        db.commit()

        ctx = _ctx(db, view, generation, comparison=Comparison.PREVIOUS_PERIOD)
        result = MetricsResolver().run(ctx)

        revenue = result.kpis["net_revenue"]
        assert revenue.value == Decimal("1000.00")
        assert revenue.previous == Decimal("500.00")
        assert revenue.delta_pct == Decimal("100.0")

        orders = result.kpis["orders_count"]
        assert orders.value == Decimal("4")
        assert orders.delta_pct == Decimal("100.0")


def test_delta_from_a_zero_previous_is_none_not_infinity():
    """Growth from zero is undefined, not infinite and not 100%.

    The comparison row is seeded with an explicit measured 0 rather than left
    absent, so this asserts the "previous <= 0" branch of ``_pct_delta`` and not
    merely the "no previous data" one.
    """
    view = _view(ResolverId.METRICS, kpis=("net_revenue",))
    with sandbox() as (db, generation):
        _order_day(db, generation, _day(1), net_revenue=Decimal("2500.00"), orders_total=9)
        _order_day(
            db,
            generation,
            date.fromordinal(SANDBOX_START.toordinal() - 5),
            net_revenue=Decimal("0.00"),
            orders_total=0,
        )
        db.commit()

        ctx = _ctx(db, view, generation, comparison=Comparison.PREVIOUS_PERIOD)
        result = MetricsResolver().run(ctx)

        revenue = result.kpis["net_revenue"]
        assert revenue.value == Decimal("2500.00")
        assert revenue.previous == Decimal("0")
        assert revenue.delta_pct is None


# ---------------------------------------------------------------------------
# 2 + 3. The densification boundary
# ---------------------------------------------------------------------------


_TREND_VIEW = _view(
    ResolverId.TIMESERIES,
    kpis=("net_revenue",),
    charts=(
        ChartSpec(
            id="revenue_trend",
            title="Net revenue",
            type="area",
            x="date",
            series=("net_revenue",),
            format=FormatId.MONEY,
        ),
    ),
)


def test_timeseries_does_not_densify_past_the_watermark():
    """Three days of rollup, ten days requested -> three points.

    This is the single most important behaviour in the resolver engine. Ten
    points with seven trailing zeros is a confident line drawn through a hole in
    the data, and once it is drawn nothing downstream can tell it apart from a
    week with no sales.
    """
    with sandbox() as (db, generation):
        for offset in range(3):
            _order_day(
                db,
                generation,
                _day(offset),
                net_revenue=Decimal("100.00") * (offset + 1),
                orders_total=offset + 1,
            )
        db.commit()

        ctx = _ctx(db, _TREND_VIEW, generation, days=10)
        result = TimeseriesResolver().run(ctx)

        points = result.series["revenue_trend"]
        assert [p["date"] for p in points] == [
            _day(0).isoformat(),
            _day(1).isoformat(),
            _day(2).isoformat(),
        ]
        assert len(points) == 3, "the window is 10 days; only 3 are aggregated"

        # And specifically: no zero-valued point past the watermark.
        assert not [p for p in points if p["date"] > _day(2).isoformat()]

        codes = {w.code for w in result.warnings}
        assert WarningCode.ROLLUP_STALE in codes


def test_timeseries_densifies_a_genuine_internal_gap():
    """A day inside the aggregated range with no orders is a real zero.

    The mirror of the test above. Dropping this point would leave a chart that
    silently skips a bad day, which is its own kind of lie.
    """
    with sandbox() as (db, generation):
        _order_day(db, generation, _day(0), net_revenue=Decimal("400.00"), orders_total=2)
        # _day(1) deliberately absent — the store traded nothing that day.
        _order_day(db, generation, _day(2), net_revenue=Decimal("600.00"), orders_total=3)
        db.commit()

        # A three-day window, fully covered by the watermark on _day(2).
        ctx = _ctx(db, _TREND_VIEW, generation, days=3)
        result = TimeseriesResolver().run(ctx)

        points = result.series["revenue_trend"]
        assert len(points) == 3
        assert points[1]["date"] == _day(1).isoformat()
        assert points[1]["net_revenue"] == Decimal("0"), "an aggregated quiet day is a zero"
        assert points[0]["net_revenue"] == Decimal("400.00")
        assert points[2]["net_revenue"] == Decimal("600.00")

        codes = {w.code for w in result.warnings}
        assert WarningCode.ROLLUP_STALE not in codes
        assert WarningCode.NO_ROLLUP_YET not in codes


# ---------------------------------------------------------------------------
# 4. breakdown
# ---------------------------------------------------------------------------


def test_breakdown_respects_the_limit_and_flags_truncation():
    view = _view(
        ResolverId.BREAKDOWN,
        params={
            "source": "agg_product_daily",
            "dimension": "product",
            "metrics": {"units_sold": "units"},
        },
        tables=(
            TableSpec(
                id="top_products",
                title="Top products",
                columns=(TableColumn(key="product", label="Product"),),
            ),
        ),
    )
    with sandbox() as (db, generation):
        for index in range(5):
            db.add(
                AggProductDaily(
                    bucket_date=_day(0),
                    tz_generation=generation,
                    product_id=900_000 + index,
                    sku_snapshot=f"SANDBOX-{index}",
                    units=10 * (index + 1),
                    orders=1,
                )
            )
        db.commit()

        ctx = _ctx(db, view, generation, limit=3)
        result = BreakdownResolver().run(ctx)

        block = result.tables["top_products"]
        assert len(block.rows) == 3
        assert block.total_rows == 3
        assert block.truncated is True, "5 products exist; only 3 were returned"
        # Top-N really is the top: ordered by the metric, descending.
        assert [r["units_sold"] for r in block.rows] == [
            Decimal("50"),
            Decimal("40"),
            Decimal("30"),
        ]

        # And the un-truncated case must not claim truncation.
        wide = BreakdownResolver().run(_ctx(db, view, generation, limit=20))
        assert wide.tables["top_products"].truncated is False
        assert len(wide.tables["top_products"].rows) == 5


# ---------------------------------------------------------------------------
# 5. table sort validation
# ---------------------------------------------------------------------------


_TABLE_SPEC = TableSpec(
    id="daily_orders",
    title="Daily orders",
    columns=(
        TableColumn(key="bucket_date", label="Date"),
        TableColumn(key="orders_total", label="Orders", format=FormatId.INT, align="right"),
        TableColumn(key="net_revenue", label="Revenue", format=FormatId.MONEY, align="right"),
    ),
    default_sort="net_revenue",
)
_TABLE_VIEW = _view(ResolverId.TABLE, tables=(_TABLE_SPEC,))


def test_table_rejects_a_sort_column_the_spec_does_not_declare():
    """`cogs_sum` is a real column of the rollup — and not on this table.

    Validating against the *spec* rather than the source is the point: the spec
    is what the client can see. Silently ignoring an unknown sort returns a
    different page than was asked for, and nothing anywhere says so.
    """
    with sandbox() as (db, generation):
        _order_day(db, generation, _day(0), net_revenue=Decimal("10.00"), orders_total=1)
        db.commit()

        ctx = _ctx(db, _TABLE_VIEW, generation, sort="cogs_sum")
        with pytest.raises(ResolverError) as excinfo:
            TableResolver().run(ctx)
        assert "cogs_sum" in str(excinfo.value)

        # A declared column sorts fine, so the rejection is about the spec and
        # not about sorting being broken.
        ok = TableResolver().run(_ctx(db, _TABLE_VIEW, generation, sort="net_revenue"))
        assert ok.tables["daily_orders"].rows


def test_table_honours_limit_and_offset():
    with sandbox() as (db, generation):
        for offset in range(4):
            _order_day(
                db,
                generation,
                _day(offset),
                net_revenue=Decimal("100.00") * (offset + 1),
                orders_total=offset + 1,
            )
        db.commit()

        first = TableResolver().run(
            _ctx(db, _TABLE_VIEW, generation, sort="net_revenue", limit=2)
        )
        assert len(first.tables["daily_orders"].rows) == 2
        assert first.tables["daily_orders"].truncated is True
        assert first.tables["daily_orders"].rows[0]["net_revenue"] == Decimal("400.00")

        second = TableResolver().run(
            _ctx(db, _TABLE_VIEW, generation, sort="net_revenue", limit=2, offset=2)
        )
        assert second.tables["daily_orders"].rows[0]["net_revenue"] == Decimal("200.00")
        assert second.tables["daily_orders"].truncated is False


# ---------------------------------------------------------------------------
# 6. quality roll-up
# ---------------------------------------------------------------------------


def test_quality_rolls_up_to_the_worst_component():
    """One INCOMPLETE metric makes the whole view incomplete. No averaging."""
    result = ResolverResult(
        kpis={
            "a": KpiValue(kpi_id="a", quality=MetricQuality.AUTHORITATIVE.value),
            "b": KpiValue(kpi_id="b", quality=MetricQuality.ACTUAL.value),
            "c": KpiValue(kpi_id="c", quality=MetricQuality.INCOMPLETE.value),
        }
    ).rolled_up()
    assert result.quality is MetricQuality.INCOMPLETE

    healthy = ResolverResult(
        kpis={
            "a": KpiValue(kpi_id="a", quality=MetricQuality.AUTHORITATIVE.value),
            "b": KpiValue(kpi_id="b", quality=MetricQuality.ALLOCATED.value),
        }
    ).rolled_up()
    assert healthy.quality is MetricQuality.ALLOCATED


def test_a_view_with_one_incomplete_metric_is_incomplete():
    view = _view(ResolverId.METRICS, kpis=("net_revenue", "cm1"))
    with sandbox() as (db, generation):
        # Units sold, but not one of them carried a unit_cost snapshot.
        _order_day(
            db,
            generation,
            _day(0),
            net_revenue=Decimal("900.00"),
            net_merchandise_sales=Decimal("800.00"),
            orders_total=3,
            units=6,
            costed_units=0,
        )
        db.commit()

        result = MetricsResolver().run(_ctx(db, view, generation))
        assert result.kpis["net_revenue"].quality == MetricQuality.AUTHORITATIVE.value
        assert result.kpis["cm1"].quality == MetricQuality.INCOMPLETE.value
        assert result.quality is MetricQuality.INCOMPLETE


# ---------------------------------------------------------------------------
# 7. a missing input is null, never zero
# ---------------------------------------------------------------------------


def test_kpi_with_missing_input_is_null_not_zero():
    """Zero COGS reports 100% margin — the error nobody investigates."""
    view = _view(ResolverId.METRICS, kpis=("cm1", "cm1_pct", "ltv"))
    with sandbox() as (db, generation):
        _order_day(
            db,
            generation,
            _day(0),
            net_revenue=Decimal("900.00"),
            net_merchandise_sales=Decimal("800.00"),
            orders_total=3,
            units=6,
            costed_units=0,
            cogs_sum=Decimal("0.00"),
        )
        db.commit()

        result = MetricsResolver().run(_ctx(db, view, generation))

        cm1 = result.kpis["cm1"]
        assert cm1.value is None
        assert cm1.value != Decimal("800.00"), "uncosted units must not become free"
        assert "unit_cost" in cm1.inputs_missing
        assert cm1.quality == MetricQuality.INCOMPLETE.value

        assert result.kpis["cm1_pct"].value is None

        # A KPI the engine has no binding for at all is also null, and says so
        # rather than quietly vanishing from the response.
        ltv = result.kpis["ltv"]
        assert ltv.value is None
        assert ltv.inputs_missing == ["no rollup binding"]
        assert METRIC_NOT_BOUND in {w.code for w in result.warnings}


def test_partial_cost_coverage_is_reported_not_suppressed():
    """Half-covered margin is still shown — labelled, with coverage stated.

    Suppressing it entirely would be its own kind of lie; the honest answer is
    the number plus how much of the business it covers.
    """
    view = _view(ResolverId.METRICS, kpis=("cm1",))
    with sandbox() as (db, generation):
        _order_day(
            db,
            generation,
            _day(0),
            net_merchandise_sales=Decimal("1000.00"),
            cogs_sum=Decimal("300.00"),
            orders_total=4,
            units=10,
            costed_units=5,
        )
        db.commit()

        result = MetricsResolver().run(_ctx(db, view, generation))
        cm1 = result.kpis["cm1"]
        assert cm1.value == Decimal("700.00")
        assert cm1.coverage_pct == Decimal("50.00")
        assert cm1.quality == MetricQuality.INCOMPLETE.value
        assert cm1.inputs_missing == []


# ---------------------------------------------------------------------------
# 8. timezone generations
# ---------------------------------------------------------------------------


def test_mixed_tz_generations_are_refused():
    """Buckets cut on two calendars must not be summed.

    The failure this prevents has no symptom: both rows look identical, and the
    only trace is a number that no longer reconciles.
    """
    view = _view(ResolverId.METRICS, kpis=("net_revenue",))
    db = SessionLocal()
    generation = 1000 + (uuid.uuid4().int % 20000)
    other = generation + 1
    try:
        _order_day(db, generation, _day(0), net_revenue=Decimal("100.00"), orders_total=1)
        _order_day(db, other, _day(1), net_revenue=Decimal("200.00"), orders_total=1)
        db.commit()

        ctx = _ctx(db, view, generation)
        with pytest.raises(TzGenerationMixed):
            MetricsResolver().run(ctx)
    finally:
        db.rollback()
        for model in _OWNED_MODELS:
            db.execute(
                delete(model).where(model.tz_generation.in_([generation, other]))
            )
        db.commit()
        db.close()


# ---------------------------------------------------------------------------
# 9. the funnel's permanent caveat
# ---------------------------------------------------------------------------


_FUNNEL_VIEW = _view(
    ResolverId.FUNNEL,
    kpis=("add_to_cart_rate", "cart_abandonment_rate", "conversion_rate"),
    charts=(
        ChartSpec(
            id="funnel_steps",
            title="Funnel steps",
            type="bar",
            x="step",
            series=("users",),
        ),
    ),
    filters=(FilterKey.DATE_RANGE,),
)


def test_funnel_always_carries_funnel_starts_at_cart():
    """With data and without it. The empty case matters more, not less.

    An empty funnel is precisely when a reader goes looking for the missing top
    of it, so that is the worst possible moment to drop the explanation.
    """
    with sandbox() as (db, generation):
        empty = FunnelResolver().run(_ctx(db, _FUNNEL_VIEW, generation))
        assert WarningCode.FUNNEL_STARTS_AT_CART in {w.code for w in empty.warnings}
        assert empty.series == {}, "no rows must not become a ladder of zeros"

        db.add(
            AggFunnelDaily(
                bucket_date=_day(0),
                tz_generation=generation,
                product_views=1000,
                cart_views=400,
                items_added=300,
                checkouts_started=120,
                shipping_submitted=100,
                payments_initiated=90,
                orders_placed=60,
                distinct_sessions=800,
            )
        )
        db.commit()

        populated = FunnelResolver().run(_ctx(db, _FUNNEL_VIEW, generation, days=3))
        assert WarningCode.FUNNEL_STARTS_AT_CART in {
            w.code for w in populated.warnings
        }

        steps = populated.series["funnel_steps"]
        assert [s["step_key"] for s in steps][0] == "product_views"
        assert steps[0]["users"] == 1000
        assert steps[-1]["users"] == 60
        # 300 added of 1000 viewed.
        assert populated.kpis["add_to_cart_rate"].value == Decimal("30.0000")

        # distinct_sessions is not additive across days, so a multi-day
        # conversion rate is reported unavailable rather than understated.
        conversion = populated.kpis["conversion_rate"]
        assert conversion.value is None
        assert conversion.inputs_missing == [
            "distinct_sessions is not additive across days"
        ]


# ---------------------------------------------------------------------------
# 10. an unbuilt rollup is not a measured zero
# ---------------------------------------------------------------------------


def test_empty_source_reports_no_rollup_yet_and_shows_no_zeros():
    view = _view(
        ResolverId.TIMESERIES,
        kpis=("net_revenue", "orders_count"),
        charts=(
            ChartSpec(
                id="revenue_trend",
                title="Net revenue",
                type="area",
                x="date",
                series=("net_revenue",),
                format=FormatId.MONEY,
            ),
        ),
    )
    with sandbox() as (db, generation):
        # Nothing written at all: this generation's rollup has never been built.
        result = TimeseriesResolver().run(_ctx(db, view, generation))

        assert WarningCode.NO_ROLLUP_YET in {w.code for w in result.warnings}
        assert result.series == {}, "an empty series key still lets a chart draw an axis"

        for kpi in result.kpis.values():
            assert kpi.value is None, f"{kpi.kpi_id} was presented as a measured value"
            assert kpi.value != Decimal("0")
            assert kpi.inputs_missing

        # Provenance is still reported, with a null watermark: the reader can
        # see WHICH rollup is empty and that it has never been computed.
        assert [s.id for s in result.sources] == ["agg_order_daily"]
        assert result.sources[0].through is None
        assert result.sources[0].rows == 0


def test_tracking_health_distinguishes_never_built_from_current():
    """`never_built` must not sort as "0 days behind" — it is the worst state."""
    view = _view(ResolverId.TRACKING_HEALTH, filters=(FilterKey.DATE_RANGE,))
    with sandbox() as (db, generation):
        _order_day(db, generation, _day(9), net_revenue=Decimal("50.00"), orders_total=1)
        db.commit()

        result = TrackingHealthResolver().run(_ctx(db, view, generation))
        rows = {r["source"]: r for r in result.tables["rollup_freshness"].rows}

        assert rows["agg_order_daily"]["status"] == "current"
        assert rows["agg_order_daily"]["lag_days"] == 0
        assert rows["agg_funnel_daily"]["status"] == "never_built"
        assert rows["agg_funnel_daily"]["lag_days"] is None
        assert rows["agg_funnel_daily"]["through"] is None

        # The event stream cannot be seen at all, so no event series is drawn.
        assert result.series == {}
        assert NOT_CONFIGURED in {w.code for w in result.warnings}
        assert result.quality is MetricQuality.INCOMPLETE


def test_reconciliation_lists_checks_it_could_not_run():
    """An empty variance table would look like a clean bill of health."""
    view = _view(
        ResolverId.RECONCILIATION,
        tables=(
            TableSpec(
                id="variances",
                title="Variances",
                columns=(TableColumn(key="check_name", label="Check"),),
            ),
        ),
    )
    with sandbox() as (db, generation):
        _order_day(
            db,
            generation,
            _day(0),
            gross_merchandise_sales=Decimal("1000.00"),
            discount_sum=Decimal("100.00"),
            tax_sum=Decimal("50.00"),
            shipping_income=Decimal("40.00"),
            cod_surcharge_sum=Decimal("10.00"),
            refund_sum=Decimal("0.00"),
            net_revenue=Decimal("1000.00"),
            orders_total=3,
        )
        db.commit()

        result = ReconciliationResolver().run(_ctx(db, view, generation))
        checks = {r["check_name"]: r for r in result.tables["variances"].rows}

        assert checks["revenue_bridge"]["status"] == "matched"
        for name in ("gateway_settlement", "ga4_purchase_parity"):
            assert checks[name]["status"] == "not_configured"
            assert checks[name]["variance_pct"] is None, "a check that did not run has no variance"
            assert checks[name]["rollup_value"] is None

        assert NOT_CONFIGURED in {w.code for w in result.warnings}
        assert result.quality is MetricQuality.INCOMPLETE


def test_reconciliation_flags_a_bridge_that_does_not_balance():
    view = _view(
        ResolverId.RECONCILIATION,
        tables=(
            TableSpec(
                id="variances",
                title="Variances",
                columns=(TableColumn(key="check_name", label="Check"),),
            ),
        ),
    )
    with sandbox() as (db, generation):
        _order_day(
            db,
            generation,
            _day(0),
            gross_merchandise_sales=Decimal("1000.00"),
            discount_sum=Decimal("100.00"),
            net_revenue=Decimal("777.00"),  # does not satisfy the identity
            orders_total=3,
        )
        db.commit()

        result = ReconciliationResolver().run(_ctx(db, view, generation))
        checks = {r["check_name"]: r for r in result.tables["variances"].rows}
        assert checks["revenue_bridge"]["status"] == "variance"
        assert checks["revenue_bridge"]["source_value"] == Decimal("900.00")
        assert checks["revenue_bridge"]["rollup_value"] == Decimal("777.00")


# ---------------------------------------------------------------------------
# 11. custom dispatch
# ---------------------------------------------------------------------------


def test_custom_with_an_unknown_fn_raises():
    """A registry typo must not render as a view that loaded and found nothing."""
    view = _view(ResolverId.CUSTOM, bespoke="no_such_function")
    with sandbox() as (db, generation):
        with pytest.raises(ResolverError) as excinfo:
            CustomResolver().run(_ctx(db, view, generation))
        assert "no_such_function" in str(excinfo.value)


def test_custom_with_no_fn_at_all_raises():
    view = _view(ResolverId.CUSTOM)
    with sandbox() as (db, generation):
        with pytest.raises(ResolverError):
            CustomResolver().run(_ctx(db, view, generation))


def test_custom_revenue_waterfall_builds_the_bridge():
    view = _view(
        ResolverId.CUSTOM,
        bespoke="revenue_waterfall",
        kpis=("gross_merchandise_sales", "net_revenue"),
    )
    with sandbox() as (db, generation):
        _order_day(
            db,
            generation,
            _day(0),
            gross_merchandise_sales=Decimal("1000.00"),
            discount_sum=Decimal("100.00"),
            tax_sum=Decimal("50.00"),
            shipping_income=Decimal("40.00"),
            cod_surcharge_sum=Decimal("10.00"),
            refund_sum=Decimal("0.00"),
            net_revenue=Decimal("1000.00"),
            net_merchandise_sales=Decimal("900.00"),
            orders_total=3,
            units=4,
            costed_units=2,
        )
        db.commit()

        result = CustomResolver().run(_ctx(db, view, generation))
        steps = {s["step"]: s["amount"] for s in result.series["revenue_waterfall"]}
        assert steps["Gross merchandise sales"] == Decimal("1000.00")
        assert steps["Discounts"] == Decimal("-100.00")
        assert steps["Net revenue"] == Decimal("1000.00")
        assert result.coverage_pct == Decimal("50.00")
        assert WarningCode.COST_COVERAGE_LOW in {w.code for w in result.warnings}


def test_gated_custom_view_returns_reason_and_no_sources():
    """"Nothing configured" and "configured and healthy" must not look alike."""
    view = _view(ResolverId.CUSTOM, bespoke="journey_paths")
    with sandbox() as (db, generation):
        result = CustomResolver().run(_ctx(db, view, generation))
        assert result.sources == [], "empty sources is how a caller detects 'not wired up'"
        assert result.series == {}
        assert result.tables == {}
        assert result.kpis == {}
        assert NOT_CONFIGURED in {w.code for w in result.warnings}
        assert result.quality is MetricQuality.INCOMPLETE


# ---------------------------------------------------------------------------
# cohort + geo
# ---------------------------------------------------------------------------


def test_cohort_divides_at_read_time_and_never_prints_a_zero_size_cohort():
    """`retention_pct` is None for an empty cohort, not 0%.

    Zero customers acquired has no retention rate. Printing 0% would put a
    cohort that does not exist on the heatmap as the worst-performing one.
    """
    view = _view(
        ResolverId.COHORT,
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
                columns=(TableColumn(key="cohort_month", label="Cohort"),),
            ),
        ),
    )
    with sandbox() as (db, generation):
        db.add_all(
            [
                AggCustomerCohortMonthly(
                    bucket_date=_day(0),
                    tz_generation=generation,
                    cohort_month="2009-04",
                    period_index=0,
                    cohort_size=100,
                    active_customers=100,
                    orders=140,
                    revenue=Decimal("50000.00"),
                ),
                AggCustomerCohortMonthly(
                    bucket_date=_day(1),
                    tz_generation=generation,
                    cohort_month="2009-04",
                    period_index=1,
                    cohort_size=100,
                    active_customers=40,
                    orders=44,
                    revenue=Decimal("16000.00"),
                ),
                AggCustomerCohortMonthly(
                    bucket_date=_day(2),
                    tz_generation=generation,
                    cohort_month="2009-05",
                    period_index=0,
                    cohort_size=0,
                    active_customers=0,
                    orders=0,
                    revenue=Decimal("0.00"),
                ),
            ]
        )
        db.commit()

        result = RESOLVERS["cohort"].run(_ctx(db, view, generation))
        grid = {
            (r["cohort_month"], r["period_index"]): r
            for r in result.tables["cohort_grid"].rows
        }
        assert grid[("2009-04", 0)]["retention_pct"] == Decimal("100.0000")
        assert grid[("2009-04", 1)]["retention_pct"] == Decimal("40.0000")
        assert grid[("2009-05", 0)]["retention_pct"] is None

        # The pooled curve divides summed components once, never averaging the
        # per-cohort percentages.
        curve = {p["month"]: p["retention_pct"] for p in result.series["retention_curve"]}
        assert curve[0] == Decimal("100.0000")  # 100 active / 100 sized
        assert curve[1] == Decimal("40.0000")


def test_geo_reports_its_own_partial_scope():
    view = _view(
        ResolverId.GEO,
        kpis=("net_revenue",),
        charts=(
            ChartSpec(
                id="geo_revenue",
                title="Revenue by state",
                type="hbar",
                x="state",
                series=("net_revenue",),
                format=FormatId.MONEY,
            ),
        ),
        tables=(
            TableSpec(
                id="geo_table",
                title="States",
                columns=(TableColumn(key="state", label="State"),),
            ),
        ),
    )
    with sandbox() as (db, generation):
        db.add_all(
            [
                AggGeoDaily(
                    bucket_date=_day(0),
                    tz_generation=generation,
                    state="Karnataka",
                    pincode="560001",
                    orders=10,
                    net_revenue=Decimal("5000.00"),
                    units=20,
                    cod_orders=4,
                    prepaid_orders=6,
                    delivered=8,
                    rto=2,
                ),
                AggGeoDaily(
                    bucket_date=_day(0),
                    tz_generation=generation,
                    state="Kerala",
                    pincode="682001",
                    orders=3,
                    net_revenue=Decimal("900.00"),
                    units=4,
                    cod_orders=3,
                    prepaid_orders=0,
                    delivered=3,
                    rto=0,
                ),
            ]
        )
        db.commit()

        result = RESOLVERS["geo"].run(_ctx(db, view, generation))
        rows = {r["state"]: r for r in result.tables["geo_table"].rows}
        assert rows["Karnataka"]["revenue"] == Decimal("5000.00")
        # rto / (delivered + rto), exactly as the rollup documents the rate.
        assert rows["Karnataka"]["rto_rate"] == Decimal("20.0000")
        assert rows["Kerala"]["rto_rate"] == Decimal("0.0000")
        assert rows["Kerala"]["cod_share"] == Decimal("100.0000")

        assert "GEO_SCOPE_PARTIAL" in {w.code for w in result.warnings}


# ---------------------------------------------------------------------------
# Wiring gaps report themselves instead of crashing
# ---------------------------------------------------------------------------


def test_table_with_an_unstored_default_sort_still_returns_rows():
    """Regression: a spec whose `default_sort` names an unstored column.

    The registry declares presentation columns; some have no rollup behind them
    yet. That must degrade to bucket ordering with a note, not blow up the whole
    view on a KeyError.
    """
    spec = TableSpec(
        id="daily_orders",
        title="Daily orders",
        columns=(
            TableColumn(key="bucket_date", label="Date"),
            TableColumn(key="net_revenue", label="Revenue", format=FormatId.MONEY),
            TableColumn(key="margin_pct", label="Margin", format=FormatId.PCT),
        ),
        default_sort="margin_pct",
    )
    view = _view(ResolverId.TABLE, tables=(spec,))
    with sandbox() as (db, generation):
        _order_day(db, generation, _day(0), net_revenue=Decimal("10.00"), orders_total=1)
        db.commit()

        result = TableResolver().run(_ctx(db, view, generation))
        assert len(result.tables["daily_orders"].rows) == 1
        assert METRIC_NOT_BOUND in {w.code for w in result.warnings}
        # The unstored column is simply absent, not filled with a zero.
        assert "margin_pct" not in result.tables["daily_orders"].rows[0]


def test_breakdown_by_an_unstored_dimension_reports_rather_than_crashes():
    view = _view(
        ResolverId.BREAKDOWN,
        charts=(
            ChartSpec(
                id="by_stage",
                title="By stage",
                type="bar",
                x="stage",
                series=("orders_count",),
            ),
        ),
    )
    with sandbox() as (db, generation):
        result = BreakdownResolver().run(_ctx(db, view, generation))
        assert result.tables == {}
        assert result.series == {}
        assert result.sources == []
        assert NOT_CONFIGURED in {w.code for w in result.warnings}


def test_no_registry_view_crashes_its_resolver():
    """Every non-gated registry view must produce an envelope, not a traceback.

    A view whose registry entry lacks the `params` its resolver needs reports
    itself unwired — which renders as "not configured yet" and is fixable — and
    never as a 500 or, worse, as a screen of zeros.
    """
    from app.services.analytics.registry import all_views
    from app.services.analytics.types import GATED_STATES

    with sandbox() as (db, generation):
        for view in all_views():
            if view.state in GATED_STATES:
                continue
            ctx = _ctx(db, view, generation)
            result = RESOLVERS[view.resolver.value].run(ctx)
            # Nothing may present a fabricated zero on an empty generation.
            for kpi in result.kpis.values():
                assert kpi.value is None or kpi.inputs_missing == [], (
                    f"{view.slug}/{kpi.kpi_id} reported a value with missing inputs"
                )


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def test_every_resolver_id_in_the_enum_is_registered():
    """A registry view naming a resolver that does not exist is a boot-time bug,
    not a runtime 500 on one screen."""
    assert set(RESOLVERS) == {r.value for r in ResolverId}
