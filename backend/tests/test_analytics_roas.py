"""Tests for view 21 (blended ROAS from entered spend) and capability honesty.

What actually has to be true here
---------------------------------
View 21 was INTEGRATION_REQUIRED until manually entered marketing spend
(`analytics_marketing_spend`) unlocked the two things spend alone can answer:
blended ROAS/MER and the spend split by channel. The tests carrying real weight
are the refusals, because those failures are silent and permanent:

  * A window with NO spend rows must gate at runtime — empty ``sources``, a
    ``NOT_CONFIGURED`` warning, and no fabricated ratio. Zero spend under real
    revenue would read as infinite (or free) marketing.
  * A bucket with no spend inside a window that has spend elsewhere gets
    ``None`` — not 0 and not infinity.
  * The per-channel table carries SPEND ONLY. A revenue or ROAS column there
    would be per-channel attribution that does not exist anywhere in this
    deployment (no session->order key), i.e. fabrication.
  * `special.py`'s satisfiability change must leave every GA4-gated view's
    behaviour byte-identical while no credentials are configured — pinned
    below against the registry-derived gated envelope.

Isolation strategy
------------------
Follows ``test_analytics_resolvers.py``: no db fixture in ``conftest.py``; each
test owns its ``SessionLocal()`` and tears down in a ``finally``. Fixtures live
in **June 1980** — a year no other suite uses (1974-1979, 1990, 1996-1999,
2001-2016, 2018, 2019, 2021, 2024 and 2026 are taken; the live demo PERF- rows
sit in 2026-05..07 and are never touched) — under a private random
``tz_generation``. The one service-level test writes under the ACTIVE
generation (the service reads it from the database and cannot be told
otherwise) and deletes by ``(generation, June 1980 date range)`` on the way
out, exactly as ``test_analytics_view_bindings.py`` does.

Run inside the analytics container:

    docker exec wvana-py python -m pytest tests/test_analytics_roas.py -q
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterator

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.analytics_rollups import AggOrderDaily
from app.models.analytics_spend import (
    AnalyticsMarketingSpend,
    SpendGrain,
    SpendQuality,
)
from app.repositories.analytics_repository import AnalyticsRepository
from app.schemas.analytics_view import AnalyticsViewEnvelope, GatedViewEnvelope
from app.services.analytics import registry
from app.services.analytics.filters import (
    AnalyticsFilters,
    Comparison,
    Granularity,
    Period,
)
from app.services.analytics.resolvers import (
    NOT_CONFIGURED,
    RESOLVERS,
    ResolverContext,
)
from app.services.analytics.resolvers import special
from app.services.analytics.resolvers.marketing import (
    MANUAL_SPEND,
    REVENUE_SOURCE,
    SPEND_SOURCE,
    roas_blended,
)
from app.services.analytics.resolvers.special import (
    CUSTOM_FUNCTIONS,
    EXTERNAL_CAPABILITIES,
    external_capability_satisfied,
    unsatisfied_external_requirements,
)
from app.services.analytics.timebox import active_generation
from app.services.analytics.types import (
    GATED_STATES,
    Capability,
    MetricQuality,
    ViewState,
)
from app.services.analytics.view_service import AnalyticsViewService

# June 1980 — this module's private sandbox year. 1980-06-02 is a Monday, which
# the week-bucketing test relies on; asserted rather than assumed.
SANDBOX_START = date(1980, 6, 1)
SANDBOX_MONDAY = date(1980, 6, 2)
SANDBOX_TODAY = date(1980, 7, 20)
assert SANDBOX_MONDAY.weekday() == 0

JUNE_END = date(1980, 6, 30)
JULY_END = date(1980, 7, 31)

VIEW_21 = registry.get_view("marketing", "roas-and-marketing-profitability")
assert VIEW_21 is not None and VIEW_21.number == 21


@contextmanager
def sandbox() -> Iterator[tuple[Session, int]]:
    """A session plus a private ``tz_generation``, cleaned up unconditionally."""
    db = SessionLocal()
    generation = 1000 + (uuid.uuid4().int % 20000)
    try:
        yield db, generation
    finally:
        try:
            db.rollback()
            db.execute(
                delete(AggOrderDaily).where(AggOrderDaily.tz_generation == generation)
            )
            db.execute(
                delete(AnalyticsMarketingSpend).where(
                    AnalyticsMarketingSpend.tz_generation == generation
                )
            )
            db.commit()
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _ctx(
    db: Session,
    generation: int,
    *,
    date_from: date,
    date_to: date,
    granularity: Granularity = Granularity.DAY,
    comparison: Comparison = Comparison.NONE,
    view=None,
) -> ResolverContext:
    filters = AnalyticsFilters(
        period=Period.CUSTOM,
        date_from=date_from,
        date_to=date_to,
        granularity=granularity,
        comparison=comparison,
    )
    return ResolverContext(
        db=db,
        repo=AnalyticsRepository(db),
        view=view or VIEW_21,
        filters=filters,
        window=filters.resolve(SANDBOX_TODAY),
        tz_generation=generation,
        today=SANDBOX_TODAY,
    )


def _order_day(db: Session, generation: int, day: date, net_revenue: str) -> None:
    db.add(
        AggOrderDaily(
            bucket_date=day,
            tz_generation=generation,
            net_revenue=Decimal(net_revenue),
        )
    )


def _spend(
    db: Session,
    generation: int,
    *,
    start: date,
    end: date | None = None,
    channel: str = "meta",
    amount: str,
    grain: str = SpendGrain.DAILY,
    quality: str = SpendQuality.ASSUMED,
    currency: str = "INR",
) -> None:
    db.add(
        AnalyticsMarketingSpend(
            grain=grain,
            period_start=start,
            period_end=end or start,
            channel=channel,
            campaign="-",
            amount=Decimal(amount),
            currency=currency,
            quality=quality,
            source="manual",
            tz_generation=generation,
        )
    )


def _run(ctx: ResolverContext):
    """Dispatch through the real CUSTOM resolver, so the registry wiring
    (``params={"fn": "roas_blended"}``) is in the tested path, not bypassed."""
    return RESOLVERS["custom"].run(ctx)


def _day(offset: int) -> date:
    return date.fromordinal(SANDBOX_START.toordinal() + offset)


# ---------------------------------------------------------------------------
# 1. Blended ROAS is sums-then-divide, exactly
# ---------------------------------------------------------------------------


def test_blended_roas_is_exactly_revenue_over_spend():
    """50,000 of revenue over 10,000 of spend is exactly 5.0 — one division."""
    with sandbox() as (db, generation):
        for offset in range(5):
            _order_day(db, generation, _day(offset), "10000.00")
            _spend(db, generation, start=_day(offset), channel="meta", amount="1500.00")
            _spend(db, generation, start=_day(offset), channel="google", amount="500.00")
        db.commit()

        result = _run(
            _ctx(db, generation, date_from=SANDBOX_START, date_to=_day(5))
        )

        assert result.kpis["blended_roas"].value == Decimal("5")
        assert result.kpis["total_spend"].value == Decimal("10000.00")
        assert result.kpis["net_revenue"].value == Decimal("50000.00")
        # Provenance names both sides of the ratio.
        assert {s.id for s in result.sources} >= {SPEND_SOURCE, REVENUE_SOURCE}
        # The permanent caveat rides on every data-bearing response.
        assert MANUAL_SPEND in {w.code for w in result.warnings}
        # Typed (ASSUMED) spend grades the ratio ESTIMATED, never AUTHORITATIVE.
        assert result.kpis["blended_roas"].quality == MetricQuality.ESTIMATED.value
        assert result.quality is MetricQuality.ESTIMATED


def test_rebucketed_week_is_sums_then_divide_not_mean_of_daily_ratios():
    """A weekly bucket's ROAS is sum(revenue)/sum(spend), never mean(daily ROAS).

    Monday: 100 revenue / 10 spend (daily ratio 10). Tuesday: 300 / 100 (daily
    ratio 3). The week is 400/110 = 3.6364; the mean of daily ratios is 6.5 and
    would double-count the cheap day.
    """
    with sandbox() as (db, generation):
        _order_day(db, generation, SANDBOX_MONDAY, "100.00")
        _order_day(db, generation, SANDBOX_MONDAY + timedelta(days=1), "300.00")
        # Watermark row so the whole week is covered by the rollup.
        _order_day(db, generation, SANDBOX_MONDAY + timedelta(days=6), "0.00")
        _spend(db, generation, start=SANDBOX_MONDAY, amount="10.00")
        _spend(db, generation, start=SANDBOX_MONDAY + timedelta(days=1), amount="100.00")
        db.commit()

        result = _run(
            _ctx(
                db,
                generation,
                date_from=SANDBOX_MONDAY,
                date_to=SANDBOX_MONDAY + timedelta(days=7),
                granularity=Granularity.WEEK,
            )
        )

        points = result.series["roas_trend"]
        assert len(points) == 1, "seven Monday-aligned days are one weekly bucket"
        week = points[0]
        expected = (Decimal("400") / Decimal("110")).quantize(Decimal("0.0001"))
        assert week["blended_roas"] == expected
        assert week["blended_roas"] != Decimal("6.5"), (
            "mean of daily ratios — the wrong aggregation this test exists to forbid"
        )
        # The window KPI agrees with the single bucket: same sums, same division.
        assert result.kpis["blended_roas"].value == expected


# ---------------------------------------------------------------------------
# 2. Zero spend: gate, never fabricate
# ---------------------------------------------------------------------------


def test_zero_spend_rows_gates_the_view_at_runtime():
    """Revenue with no entered spend answers with the reason and nothing else."""
    with sandbox() as (db, generation):
        for offset in range(3):
            _order_day(db, generation, _day(offset), "10000.00")
        db.commit()

        result = _run(_ctx(db, generation, date_from=SANDBOX_START, date_to=_day(3)))

        assert result.is_empty, "no kpis, no series, no tables — nothing to draw"
        assert result.sources == [], (
            "empty sources is the machine-readable half of 'not configured'"
        )
        gate = [w for w in result.warnings if w.code == NOT_CONFIGURED]
        assert gate and gate[0].severity == "error"
        assert Capability.AD_PLATFORM.value in gate[0].detail["requires"]
        assert result.quality is MetricQuality.INCOMPLETE


def test_zero_spend_bucket_inside_a_spending_window_is_none_not_zero():
    """Spend on one day only: other buckets have no ROAS — None, not 0 or inf."""
    with sandbox() as (db, generation):
        for offset in range(7):
            _order_day(db, generation, _day(offset + 1), "500.00")  # Jun 2..8
        _spend(db, generation, start=SANDBOX_MONDAY, amount="100.00")  # Jun 2 only
        db.commit()

        result = _run(
            _ctx(
                db,
                generation,
                date_from=SANDBOX_MONDAY,
                date_to=SANDBOX_MONDAY + timedelta(days=7),
            )
        )

        by_date = {p["date"]: p for p in result.series["roas_trend"]}
        spent = by_date[SANDBOX_MONDAY.isoformat()]
        assert spent["blended_roas"] == Decimal("5")

        unspent = by_date[(SANDBOX_MONDAY + timedelta(days=1)).isoformat()]
        assert unspent["blended_roas"] is None, "no denominator, no ratio"
        assert unspent["spend"] is None, (
            "an absent entry in a hand-typed ledger is 'not recorded', not zero"
        )
        assert unspent["revenue"] == Decimal("500.00"), "the revenue side is real"
        # The window total still divides once over the whole sums.
        assert result.kpis["blended_roas"].value == Decimal("35")  # 3500 / 100


# ---------------------------------------------------------------------------
# 3. The per-channel table is spend-only
# ---------------------------------------------------------------------------


def test_channel_table_contains_spend_and_no_per_channel_roas_or_revenue():
    with sandbox() as (db, generation):
        _order_day(db, generation, SANDBOX_START, "9000.00")
        _spend(db, generation, start=SANDBOX_START, channel="meta", amount="750.00")
        _spend(db, generation, start=SANDBOX_START, channel="google", amount="250.00")
        db.commit()

        result = _run(_ctx(db, generation, date_from=SANDBOX_START, date_to=_day(1)))

        rows = result.tables["channel_spend"].rows
        assert [r["channel"] for r in rows] == ["meta", "google"]
        forbidden = {
            "roas",
            "blended_roas",
            "revenue",
            "net_revenue",
            "revenue_share_pct",
        }
        for row in rows:
            assert set(row) == {"channel", "spend", "spend_share_pct", "quality"}, (
                f"unexpected column in the per-channel table: {sorted(set(row))}. "
                "Per-channel revenue/ROAS cannot exist without a session->order "
                "key and must never appear here."
            )
            assert not (set(row) & forbidden)
        assert rows[0]["spend"] == Decimal("750.00")
        assert rows[0]["spend_share_pct"] == Decimal("75")
        assert rows[1]["spend_share_pct"] == Decimal("25")

        # The registry's declared table spec makes the same promise.
        spec_keys = {c.key for t in VIEW_21.tables for c in t.columns}
        assert not (spec_keys & forbidden)
        # And no chart plots a per-channel ratio either.
        for chart in VIEW_21.charts:
            if chart.x == "channel":
                assert not (set(chart.series) & forbidden)


# ---------------------------------------------------------------------------
# 4. Monthly lumps: exact allocation, honest grade
# ---------------------------------------------------------------------------


def test_monthly_lump_cut_mid_window_allocates_to_the_paisa_and_reports_allocated():
    """Rs.40,000 across 30-day June: 4,000,000 paise = 133,333 x 30 r 10, the
    remainder on the earliest days. June 1-15 is therefore 10 x 1333.34 +
    5 x 1333.33 = 20,000.05 exactly — not 20,000.00, which is what naive
    per-day rounding (or fractional-rupee arithmetic) would report."""
    with sandbox() as (db, generation):
        _spend(
            db,
            generation,
            start=SANDBOX_START,
            end=JUNE_END,
            grain=SpendGrain.MONTHLY,
            quality=SpendQuality.ACTUAL,
            amount="40000.00",
        )
        db.commit()

        half = _run(
            _ctx(db, generation, date_from=SANDBOX_START, date_to=date(1980, 6, 16))
        )
        assert half.kpis["total_spend"].value == Decimal("20000.05")
        # An ACTUAL monthly figure spread over days is ALLOCATED per day — the
        # spread is this module's inference, not anyone's observation.
        assert half.kpis["total_spend"].quality == MetricQuality.ALLOCATED.value
        assert half.tables["channel_spend"].rows[0]["quality"] == (
            MetricQuality.ALLOCATED.value
        )
        # No revenue rollup in range: the ratio is missing and NAMES the gap.
        roas = half.kpis["blended_roas"]
        assert roas.value is None
        assert REVENUE_SOURCE in roas.inputs_missing

        # The whole month reassembles the lump exactly: nothing lost to rounding.
        full = _run(
            _ctx(db, generation, date_from=SANDBOX_START, date_to=date(1980, 7, 1))
        )
        assert full.kpis["total_spend"].value == Decimal("40000.00")


# ---------------------------------------------------------------------------
# 5. Regression pin: GA4-gated behaviour is unchanged with no credentials
# ---------------------------------------------------------------------------


class _Reader:
    """Stub holding exactly the permissions asked for; no user row needed."""

    is_admin = False

    def __init__(self, permissions: set[str]) -> None:
        self._permissions = permissions

    def has_permission(self, permission: str) -> bool:
        return permission in self._permissions


def _module_slug_of(view) -> str:
    return next(m.slug for m in registry.MODULES for v in m.views if v.number == view.number)


def test_ga4_gated_views_behave_byte_identically_with_no_credentials():
    """The satisfiability change must be invisible until something is configured.

    Before the change, `_external_requirements` was a static filter over
    `EXTERNAL_CAPABILITIES`. With no GA4 credentials configured — the state of
    every current deployment — the dynamic version must return exactly that
    static list for every view, and every GA4-gated view must still answer with
    the registry-derived gated envelope, field for field.
    """
    db = SessionLocal()
    try:
        assert not special._ga4_data_api_configured(db), (
            "This deployment has GA4 Data API credentials configured; the "
            "no-credentials regression pin no longer applies as written and "
            "must be revisited deliberately, not skipped."
        )

        spend_recorded = special._marketing_spend_recorded(db)
        for view in registry.all_views():
            static = sorted(
                c.value for c in view.requires if c in EXTERNAL_CAPABILITIES
            )
            dynamic = unsatisfied_external_requirements(db, view)
            if not spend_recorded:
                assert dynamic == static, f"view {view.number} ({view.slug})"
            else:
                # Entered spend may satisfy AD_PLATFORM's spend half; nothing
                # else is allowed to move.
                assert [r for r in dynamic if r != Capability.AD_PLATFORM.value] == [
                    r for r in static if r != Capability.AD_PLATFORM.value
                ], f"view {view.number} ({view.slug})"

        ga4_caps = {Capability.GA4_DATA_API, Capability.GA4_MEASUREMENT}
        ga4_gated = [
            v
            for v in registry.all_views()
            if (set(v.requires) & ga4_caps) and v.state in GATED_STATES
        ]
        assert ga4_gated, "the registry lost its GA4-gated views entirely?"
        for view in ga4_gated:
            module_slug = _module_slug_of(view)
            service = AnalyticsViewService(db, _Reader({view.permission}))
            envelope = service.resolve_view(
                module_slug,
                view.slug,
                AnalyticsFilters(
                    period=Period.CUSTOM,
                    date_from=SANDBOX_START,
                    date_to=_day(5),
                    comparison=Comparison.NONE,
                ),
                use_cache=False,
            )
            assert isinstance(envelope, GatedViewEnvelope)
            # Byte-identical: the whole gated answer is registry-derived.
            assert envelope.model_dump(mode="json") == {
                "view": {
                    "module": module_slug,
                    "view": view.slug,
                    "number": view.number,
                    "title": view.name,
                },
                "availability": view.state.value,
                "requires": [c.value for c in view.requires],
                "limitation": view.limitation,
                "sources": [],
            }, f"view {view.number} ({view.slug})"
    finally:
        db.close()


def test_journey_paths_still_refuses_and_names_ga4():
    """View 65's custom function keeps refusing with the same requirement."""
    view_65 = registry.get_view("marketing", "customer-journey-and-attribution")
    assert view_65 is not None
    with sandbox() as (db, generation):
        result = CUSTOM_FUNCTIONS["journey_paths"](
            _ctx(db, generation, date_from=SANDBOX_START, date_to=_day(5), view=view_65)
        )
        assert result.sources == []
        gate = [w for w in result.warnings if w.code == NOT_CONFIGURED]
        assert gate and gate[0].detail["requires"] == [Capability.GA4_DATA_API.value]


# ---------------------------------------------------------------------------
# 6. The unlock path exists — and unlocks no view state yet
# ---------------------------------------------------------------------------


def test_configured_ga4_reports_satisfiable_but_no_view_state_changes(monkeypatch):
    """With credentials mocked as configured the capability is satisfiable, but
    every GA4 view keeps its registry state and its gated answer — ungating any
    of them is deliberate future work, not a side effect of this probe."""
    monkeypatch.setattr(special, "_ga4_data_api_configured", lambda _db: True)
    db = SessionLocal()
    try:
        assert external_capability_satisfied(db, Capability.GA4_DATA_API) is True

        view_19 = registry.get_view("marketing", "marketing-channel-performance")
        assert view_19 is not None
        assert Capability.GA4_DATA_API.value not in unsatisfied_external_requirements(
            db, view_19
        )

        # Registry states are a static ceiling and must not have moved. View 14
        # is legitimately PARTIAL-with-GA4-requirement (the funnel shows its
        # internal half); everything else GA4-flavoured is gated, and none of
        # them may be promoted by the probe reporting satisfiable.
        ga4_views = [
            v
            for v in registry.all_views()
            if set(v.requires) & {Capability.GA4_DATA_API, Capability.GA4_MEASUREMENT}
        ]
        assert ga4_views
        for view in ga4_views:
            assert view.state is not ViewState.LIVE, (
                f"view {view.number} ({view.slug}) reached LIVE: satisfiability "
                "must not ungate anything by itself"
            )
        for view in [v for v in ga4_views if v.state in GATED_STATES]:
            service = AnalyticsViewService(db, _Reader({view.permission}))
            envelope = service.resolve_view(
                _module_slug_of(view),
                view.slug,
                AnalyticsFilters(
                    period=Period.CUSTOM,
                    date_from=SANDBOX_START,
                    date_to=_day(5),
                    comparison=Comparison.NONE,
                ),
                use_cache=False,
            )
            assert isinstance(envelope, GatedViewEnvelope)
    finally:
        db.close()


def test_entered_spend_satisfies_the_ad_platform_spend_half():
    with sandbox() as (db, generation):
        _spend(db, generation, start=SANDBOX_START, amount="10.00")
        db.commit()
        assert external_capability_satisfied(db, Capability.AD_PLATFORM) is True
        # Everything with no internal substitute stays unconditionally unsatisfied.
        for capability in (
            Capability.CLARITY_PROJECT,
            Capability.SEARCH_CONSOLE,
            Capability.GATEWAY_SETTLEMENT_API,
            Capability.COURIER_SCAN_API,
            Capability.BANK_CASH_FEED,
        ):
            assert external_capability_satisfied(db, capability) is False


# ---------------------------------------------------------------------------
# 7. End to end: view 21 through the service
# ---------------------------------------------------------------------------


def test_view_21_resolves_through_the_service_as_partial():
    """The whole path: registry -> permission -> resolver -> envelope.

    Written under the ACTIVE generation because the service reads it from the
    database; isolation comes from the June 1980 date range, and teardown
    deletes by (generation, range) the same way the bindings suite does.
    """
    db = SessionLocal()
    generation = int(active_generation(db).generation)
    try:
        for offset in range(5):
            _order_day(db, generation, _day(offset), "10000.00")
            _spend(db, generation, start=_day(offset), amount="2000.00")
        db.commit()

        service = AnalyticsViewService(db, _Reader({VIEW_21.permission}))
        envelope = service.resolve_view(
            "marketing",
            VIEW_21.slug,
            AnalyticsFilters(
                period=Period.CUSTOM,
                date_from=SANDBOX_START,
                date_to=_day(5),
                comparison=Comparison.NONE,
            ),
            use_cache=False,
        )

        assert isinstance(envelope, AnalyticsViewEnvelope), (
            "view 21 is PARTIAL now: it must fetch and answer, not gate statically"
        )
        assert envelope.availability == ViewState.PARTIAL.value
        assert envelope.is_partial is True
        assert envelope.kpis["blended_roas"].value == Decimal("5")
        assert envelope.kpis["total_spend"].value == Decimal("10000.00")
        assert MANUAL_SPEND in {w.code for w in envelope.warnings}
        assert {s.id for s in envelope.sources} >= {SPEND_SOURCE, REVENUE_SOURCE}
    finally:
        try:
            db.rollback()
            db.execute(
                delete(AggOrderDaily).where(
                    AggOrderDaily.tz_generation == generation,
                    AggOrderDaily.bucket_date >= SANDBOX_START,
                    AggOrderDaily.bucket_date <= JULY_END,
                )
            )
            db.execute(
                delete(AnalyticsMarketingSpend).where(
                    AnalyticsMarketingSpend.tz_generation == generation,
                    AnalyticsMarketingSpend.period_start >= SANDBOX_START,
                    AnalyticsMarketingSpend.period_start <= JULY_END,
                )
            )
            db.commit()
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 8. Registry honesty for views 19, 20 and 21
# ---------------------------------------------------------------------------


def test_views_19_and_20_stay_integration_required():
    """Their promises need click/impression/session data nobody can type in."""
    view_19 = registry.get_view("marketing", "marketing-channel-performance")
    view_20 = registry.get_view("marketing", "campaign-performance")
    assert view_19 is not None and view_20 is not None
    assert view_19.state is ViewState.INTEGRATION_REQUIRED
    assert view_19.params == {}, "view 19 must stay unwired — no internal substitute"
    assert view_20.state is ViewState.INTEGRATION_REQUIRED
    assert Capability.AD_PLATFORM in view_20.requires


def test_view_21_is_partial_blended_only_and_says_so():
    assert VIEW_21.state is ViewState.PARTIAL
    assert VIEW_21.state not in GATED_STATES, "PARTIAL fetches"
    assert VIEW_21.params.get("fn") == "roas_blended"
    assert "roas_blended" in CUSTOM_FUNCTIONS
    assert Capability.AD_PLATFORM in VIEW_21.requires
    limitation = VIEW_21.limitation.lower()
    for term in ("manually entered", "blended", "per-channel"):
        assert term in limitation, (
            f"view 21's limitation must state {term!r}: it is the sentence the "
            "admin reads instead of a fabricated per-channel table"
        )
    # The blended-only rule is structural, not just prose: no channel filter,
    # because filtering one side of a blended ratio IS per-channel ROAS.
    from app.services.analytics.types import FilterKey

    assert FilterKey.CHANNEL not in VIEW_21.filters
    assert FilterKey.CAMPAIGN not in VIEW_21.filters
