"""Tests for the registry's `params` — the binding from a view to a rollup.

`registry.py` says what a view *shows*. `params` says which stored column each
of those things *is*. Between them sits `analytics_repository`, whose allowlist
is reflected from the ORM models, so a typo in a binding raises at query time
rather than reaching SQL. These tests move that failure earlier still: to import
time, where a wrong column name is a red test instead of a 500 on a dashboard.

What actually has to be true here
---------------------------------
Three of these tests are about arithmetic and would fail loudly the first time
anyone opened the view. The ones carrying real weight are the four about *not*
binding something, because those failures are silent and permanent:

  * ``test_marketing_channel_performance_is_not_wired`` — view 19 needs GA4 and
    an ad platform, neither connected. A `params` block here would let a
    fabricated channel split render as measured fact, and nothing downstream
    could tell it apart from a real one. This is the single failure this whole
    subsystem exists to prevent.
  * ``test_no_kpi_is_bound_outside_its_declared_dimensions`` — `kpis.py` says
    which dimensions a metric may legitimately be split by. `net_revenue` is
    order-basis and includes tax and shipping, so it does NOT list `product`;
    binding it to `agg_product_daily.net_merchandise_sales` would publish a
    materially smaller number under a defined label. The catalogue already
    knows that, and this test makes it enforceable.
  * ``test_no_non_additive_column_is_ever_summed`` — every `params` column is
    summed, over the window for a card and over the bucket for a series. A
    level (`stock_close`) or a distinct count (`distinct_sessions`) summed
    across days is wrong by a factor of the window length, and wrong silently.
  * ``test_view_states_are_unchanged`` — `params` is a wiring change. If
    filling one in ever moved a view from FEATURE_REQUIRED to LIVE, this fails.

Isolation strategy
------------------
Follows ``test_analytics_resolvers.py``: no db fixture in ``conftest.py``; the
end-to-end test owns its ``SessionLocal()`` and tears down in a ``finally``.
Two differences, both forced by ``AnalyticsViewService`` reading the tz
generation from the database rather than taking one:

  1. Fixtures are written under the **active** generation, because that is the
     one the service will query.
  2. They live in a **per-run seven-day slot of 1982** — a year no other suite
     touches, cut into 52 slots keyed by this process's pid — so two runs of
     this module (concurrent or interleaved with ``test_analytics_resolvers``'s
     June 2009 sandbox) cannot see, or delete, each other's rows. Every delete
     is scoped to the run's own slot; see ``_clear_slot``.

No login: the service only ever calls ``user.has_permission``, so a stub that
grants exactly the eight view permissions exercises the real code path without
creating a user, and without touching the shared ``vinay@gmail.com`` account.

Run inside the analytics container:

    docker exec wvana-py python -m pytest tests/test_analytics_view_bindings.py -q
"""
from __future__ import annotations

import os
from collections import Counter
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterator

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import analytics_rollups as rollups
from app.repositories.analytics_repository import (
    columns_for,
    known_sources,
    measures_for,
)
from app.schemas.analytics_view import AnalyticsViewEnvelope, GatedViewEnvelope
from app.services.analytics import kpis as kpi_catalogue
from app.services.analytics import registry
from app.services.analytics.filters import AnalyticsFilters, Comparison, Period
from app.services.analytics.resolvers.base import NOT_CONFIGURED
from app.services.analytics.resolvers.core import (
    DIMENSION_BINDINGS,
    METRIC_BINDINGS,
    _binding_from_param,
)
from app.services.analytics.resolvers.special import CUSTOM_FUNCTIONS
from app.services.analytics.timebox import active_generation
from app.services.analytics.types import (
    GATED_STATES,
    AnalyticsViewDefinition,
    ResolverId,
    ViewState,
)
from app.services.analytics.view_service import AnalyticsViewService

# ---------------------------------------------------------------------------
# What the eight production dashboards are
# ---------------------------------------------------------------------------
# (view number, module slug, view slug). Slugs rather than indices, so a
# registry reorder cannot silently repoint a test at a different view.

PRIORITY_VIEWS: tuple[tuple[int, str, str], ...] = (
    (1, "executive", "executive-overview"),
    (3, "sales-finance", "revenue-and-profitability"),
    (5, "products", "product-performance"),
    (8, "customers", "customer-analytics"),
    (14, "website", "conversion-funnel"),
    (19, "marketing", "marketing-channel-performance"),
    (27, "inventory", "inventory-analytics"),
    (36, "orders", "order-fulfilment"),
)

#: View 19 is on the list above because it is one of the eight dashboards the
#: brief names — and it is the one that must stay empty. Marketing attribution
#: needs GA4 and the ad platforms; the store persists orders, not sessions.
MARKETING_CHANNEL_VIEW = 19

#: The states the registry declares today. Filling in `params` is a wiring
#: change and must not move a single view between these buckets.
#:
#: Restated once, deliberately, for two independent state corrections. Neither
#: was a wiring change, which is why the assertion is kept at full strength and
#: only the numbers move:
#:
#:   * View 66 (experiment-and-ab-testing) LIVE -> FEATURE_REQUIRED. It was LIVE
#:     with no `params` and a resolver returning `not_configured` on every path,
#:     because nothing in this deployment assigns a visitor to a variant — no
#:     assignment service, no exposure logging, no variant storage, and no
#:     experiment or variant column anywhere in the schema.
#:   * View 42 (fraud-and-risk-analytics) LIVE -> PARTIAL, audited separately
#:     and landed while this file was open. Recorded here because this constant
#:     is a single total and cannot show one change without the other.
#:   * View 58 (upsell-performance) PARTIAL -> FEATURE_REQUIRED. It was PARTIAL
#:     with an empty `params`, so it fetched with nothing to read — and the
#:     re-assessment against the real schema found no honest subset to bind:
#:     everything offer-shaped needs an impression the storefront never
#:     records, the pair rollup carries no prices, no rollup splits order value
#:     by basket size, and the one computable figure (the attach rate) is view
#:     57's headline KPI already. See the view-58 registry comment and
#:     tests/test_analytics_upsell.py.
#:
#: Net: LIVE 34 -> 32, PARTIAL 17 -> 18 -> 17, FEATURE_REQUIRED 6 -> 7 -> 8.
#:
#:   * View 64 (settlements-and-payouts) INTEGRATION_REQUIRED -> PARTIAL. Not a
#:     wiring change either: the CSV settlement-upload surface landed (endpoint,
#:     `settlement_daily` rollup, `settlements_payouts` resolver), so the view
#:     is re-keyed to GATEWAY_SETTLEMENT_REPORT and is fed by uploads. PARTIAL
#:     is its ceiling — fees are ACTUAL only on fully covered days — and the
#:     runtime probe still refuses (`not_configured`) while the table is empty.
#:     The settlements API integration remains future work.
#:     Net: PARTIAL 17 -> 18, INTEGRATION_REQUIRED 15 -> 14.
#:
#:   * View 21 (roas-and-marketing-profitability) INTEGRATION_REQUIRED ->
#:     PARTIAL. Not a wiring change: manually entered marketing spend
#:     (`analytics_marketing_spend`, admin-enterable) unlocked the two things
#:     spend alone can answer — blended ROAS/MER and spend by channel — via the
#:     `roas_blended` custom function. PARTIAL is its ceiling (typed spend,
#:     blended only, no per-channel ROAS without ad-platform attribution), and
#:     the resolver downgrades to the gated shape at runtime whenever the
#:     window holds zero spend rows. See tests/test_analytics_roas.py.
#:     Net: PARTIAL 18 -> 19, INTEGRATION_REQUIRED 14 -> 13.
#:
#: This test caught these edits and did its job. See
#: tests/test_analytics_view_state_honesty.py for the invariants that now stop a
#: view reaching LIVE without a way to produce data at all.
EXPECTED_STATE_COUNTS: dict[ViewState, int] = {
    ViewState.LIVE: 32,
    ViewState.PARTIAL: 19,
    ViewState.INTEGRATION_REQUIRED: 13,
    ViewState.FEATURE_REQUIRED: 8,
    ViewState.NOT_APPLICABLE: 1,
}

#: Exactly the keys some resolver reads out of `ctx.params`. Anything else is a
#: binding somebody believes is live and which nothing consults — the worst kind
#: of dead code, because it reads as configuration.
READABLE_PARAM_KEYS = frozenset(
    {
        "source",        # core: every resolver
        "hourly_source",  # core.TimeseriesResolver
        "metrics",       # core.binding_for
        "kpis",          # core.MetricsResolver
        "series",        # core.TimeseriesResolver
        "series_id",     # core.TimeseriesResolver
        "dimension",     # core.BreakdownResolver, special.GeoResolver
        "row_filters",   # core.TimeseriesResolver, core.TableResolver
        "table",         # core.TableResolver
        "table_id",      # core.BreakdownResolver
        "columns",       # core.TableResolver
        "group_by",      # core.TableResolver
        "fn",            # special.CustomResolver
        # ---- levels.py (views 11, 13, 28, 29, 59) ----
        # Which display measure a level breakdown ranks by, `-` prefixed for
        # descending. Needed because the default (primary measure, descending)
        # is backwards for view 29: a top-N by stock DESC under a heading that
        # says "Needs attention" shows the fullest shelves first.
        "breakdown_sort",     # levels.SnapshotResolver._order_by
        # ---- forecast.py (views 55, 56, 31) ----
        # A forecast needs its own vocabulary because its inputs are not a
        # projection: `history_days` is how far BACK it reads to fit, which is a
        # different axis from the request window, and `min_history_days` is the
        # threshold below which it refuses to answer at all. Both must be
        # inspectable configuration rather than constants buried in the resolver,
        # because a merchant is entitled to know what the number was built from.
        "forecast_column",    # forecast.sales_forecast — the series to project
        "actual_key",         # forecast.sales_forecast — datum key for actuals
        "forecast_key",       # forecast.sales_forecast — datum key for the projection
        "history_days",       # forecast.* — look-back used to fit
        "horizon_days",       # forecast.sales_forecast — how far forward, capped
        "min_history_days",   # forecast.* — refuse below this; no number is returned
        "confidence",         # forecast.sales_forecast — interval width
        "seasonality_column",  # forecast.seasonality
        "actuals_series_id",  # forecast.seasonality
        "inventory_source",   # forecast.demand_forecast
    }
)

#: Columns that exist, are numeric, and pass the repository's allowlist — and
#: which must still never appear in a `params` binding, because every params
#: column is summed. A level added across days reports thirty closing balances
#: as one; a distinct count added across days counts a returning visitor twice.
#: Each entry restates a rule already written in `analytics_rollups.py`.
NON_ADDITIVE_COLUMNS: dict[str, frozenset[str]] = {
    # "NOT additive across days — summing it over a week overcounts anyone who
    # ordered twice."
    "agg_order_daily": frozenset({"distinct_customers"}),
    "agg_customer_daily": frozenset({"active_customers"}),
    # "NOT additive: summing seven days overcounts returning sessions."
    "agg_funnel_daily": frozenset({"distinct_sessions"}),
    # "a LEVEL, not a flow ... never summed across days", plus two running
    # counters that are states rather than quantities.
    "agg_inventory_daily": frozenset(
        {"stock_close", "stock_value_close", "days_oos", "reorder_gap", "is_oos"}
    ),
    # "Constant across the cohort's rows; repeated per row" — summing it over a
    # cohort's period rows multiplies the denominator by the number of periods.
    "agg_customer_cohort_monthly": frozenset({"cohort_size"}),
    # The whole table is a per-day snapshot of LIFETIME state, so every measure
    # on it is a level. Summing any of them over a window multiplies by the
    # number of snapshot days in range.
    "agg_customer_snapshot": frozenset(measures_for("agg_customer_snapshot")),
    # Bucket-level scalars repeated on EVERY pair row of their day. Summing them
    # across a day's rows multiplies the day by the number of pairs it produced
    # — a store with 40 orders and 300 pairs would report 12 000 orders, which
    # reads as a good month. The window total is obtained by GROUPing on the
    # scalar itself alongside bucket_date (one row per day) and summing those;
    # `resolvers/basket.py::_window_totals` is the worked example. The per-pair
    # marginals are additive across DAYS for one pair and are not listed here.
    "agg_basket_pair_daily": frozenset(
        {"total_orders_in_bucket", "orders_with_any_pair", "orders_skipped_over_cap"}
    ),
}

# A per-RUN seven-day slot inside 1982 — a year before this store's first
# order, and one no other analytics suite writes to. The slot index is this
# process's pid, so two concurrent invocations of this module (which is how the
# old fixed June 2008 window got corrupted: each run's teardown deleted the
# other's rows by date range mid-test) hold disjoint windows. `AnalyticsViewService`
# reads the ACTIVE tz generation from the database and cannot be handed a
# private one, so a private generation band is not available to this suite —
# date disjointness is its only isolation axis, and every read and delete below
# stays inside this run's own slot. Two simultaneously spawned runs have
# distinct pids and collide only if those pids differ by an exact multiple of
# 52 — never the case for processes forked in the same wave.
_SLOT_DAYS = 7
_SLOT = os.getpid() % 52
SANDBOX_START = date(1982, 1, 1) + timedelta(days=_SLOT * _SLOT_DAYS)
SANDBOX_DAYS = 5
SANDBOX_END = SANDBOX_START + timedelta(days=_SLOT_DAYS - 1)

_OWNED_MODELS = (
    rollups.AggOrderDaily,
    rollups.AggOrderHourly,
    rollups.AggProductDaily,
    rollups.AggCustomerDaily,
    rollups.AggCustomerSnapshot,
    rollups.AggCustomerCohortMonthly,
    rollups.AggPaymentDaily,
    rollups.AggShipmentDaily,
    rollups.AggGeoDaily,
    rollups.AggPromoDaily,
    rollups.AggFunnelDaily,
    rollups.AggInventoryDaily,
)


def _views() -> tuple[AnalyticsViewDefinition, ...]:
    return registry.all_views()


def _bound_views() -> list[AnalyticsViewDefinition]:
    return [v for v in _views() if v.params]


def _view_ids(views: list[AnalyticsViewDefinition]) -> list[str]:
    return [f"{v.number}-{v.slug}" for v in views]


def _source_of(view: AnalyticsViewDefinition) -> str | None:
    return view.params.get("source")


def _metric_specs(view: AnalyticsViewDefinition) -> dict[str, dict]:
    declared = view.params.get("metrics") or {}
    assert isinstance(declared, dict), (
        f"View {view.number} ({view.slug}) declares params['metrics'] as "
        f"{type(declared).__name__}; `core.binding_for` only reads a mapping, so "
        "anything else is silently ignored."
    )
    return declared


def _spec_columns(spec: object) -> list[str]:
    """Every column a single metric spec names, in one flat list."""
    if isinstance(spec, str):
        return [spec]
    assert isinstance(spec, dict), f"metric spec must be a column name or mapping: {spec!r}"
    out: list[str] = []
    for key in ("add", "sub", "over", "over_add", "over_sub"):
        out.extend(spec.get(key) or ())
    return out


# ---------------------------------------------------------------------------
# 1. Every source is a real rollup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("view", _bound_views(), ids=_view_ids(_bound_views()))
def test_every_declared_source_is_a_known_rollup(view: AnalyticsViewDefinition):
    """`params['source']` is looked up in the repository's reflected allowlist.

    An unknown name raises there, at query time, on a live request. Asserting it
    here turns that into a failing test on the change that introduced it.
    """
    source = _source_of(view)
    if source is None:
        return
    assert source in known_sources(), (
        f"View {view.number} ({view.slug}) names source {source!r}, which is not a "
        f"rollup the repository can read. Known: {', '.join(known_sources())}."
    )

    hourly = view.params.get("hourly_source")
    if hourly is not None:
        assert hourly in known_sources(), (
            f"View {view.number} ({view.slug}) names hourly_source {hourly!r}, "
            "which is not a known rollup."
        )
        assert "bucket_hour" in columns_for(hourly), (
            f"View {view.number} ({view.slug}) names {hourly!r} as its hourly "
            "rollup, but that table has no bucket_hour column, so an hourly "
            "request would be served day buckets under an hour axis."
        )

    for spec in _metric_specs(view).values():
        declared = spec.get("source") if isinstance(spec, dict) else None
        if declared is not None:
            assert declared in known_sources(), (
                f"View {view.number} ({view.slug}) binds a metric to source "
                f"{declared!r}, which is not a known rollup."
            )


# ---------------------------------------------------------------------------
# 2. Every column exists on the source it is read from
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("view", _bound_views(), ids=_view_ids(_bound_views()))
def test_every_column_named_in_params_exists_on_its_source(view: AnalyticsViewDefinition):
    """No binding may name a column the rollup does not have.

    Covers all four places a column can appear: metric numerators and
    denominators, table column maps, GROUP BY keys and row filters.
    """
    source = _source_of(view)
    if source is None:
        assert not (
            view.params.keys() & {"metrics", "columns", "group_by", "row_filters"}
        ), (
            f"View {view.number} ({view.slug}) names columns but no source, so "
            "there is nothing to validate them against."
        )
        return

    available = set(columns_for(source))

    for metric_id, spec in _metric_specs(view).items():
        metric_source = (
            spec.get("source", source) if isinstance(spec, dict) else source
        )
        metric_available = set(columns_for(metric_source))
        for column in _spec_columns(spec):
            assert column in metric_available, (
                f"View {view.number} ({view.slug}) binds metric {metric_id!r} to "
                f"column {column!r}, which is not a column of {metric_source}."
            )

    for key, column in (view.params.get("columns") or {}).items():
        assert column in available, (
            f"View {view.number} ({view.slug}) maps table column {key!r} to "
            f"{column!r}, which is not a column of {source}."
        )

    for column in view.params.get("group_by") or ():
        assert column in available, (
            f"View {view.number} ({view.slug}) groups by {column!r}, which is not "
            f"a column of {source}."
        )

    for column in view.params.get("row_filters") or {}:
        assert column in available, (
            f"View {view.number} ({view.slug}) filters rows on {column!r}, which "
            f"is not a column of {source}."
        )


# ---------------------------------------------------------------------------
# 3. Everything the resolver will SUM must be summable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("view", _bound_views(), ids=_view_ids(_bound_views()))
def test_every_summed_column_is_a_measure(view: AnalyticsViewDefinition):
    """A metric column is always aggregated; the repository refuses non-measures.

    `fetch_totals` wraps every requested column in SUM(), and `fetch_rollup`
    does the same for any column that is not a grouping key. Both call
    `_measure`, which rejects anything outside `measures_for(source)` —
    identity columns and dimensions included.
    """
    source = _source_of(view)
    if source is None:
        return

    for metric_id, spec in _metric_specs(view).items():
        metric_source = (
            spec.get("source", source) if isinstance(spec, dict) else source
        )
        summable = set(measures_for(metric_source))
        for column in _spec_columns(spec):
            assert column in summable, (
                f"View {view.number} ({view.slug}) sums {column!r} for metric "
                f"{metric_id!r}, but it is not a summable column of "
                f"{metric_source}. Allowed: {', '.join(sorted(summable))}."
            )

    # A table view only SUMs when it groups; without `group_by` the projection
    # is straight columns, so a dimension there is legitimate.
    group_keys = set(view.params.get("group_by") or ())
    if group_keys:
        summable = set(measures_for(source))
        for key, column in (view.params.get("columns") or {}).items():
            if column in group_keys:
                continue
            assert column in summable, (
                f"View {view.number} ({view.slug}) groups by "
                f"{sorted(group_keys)} and projects {key!r} -> {column!r}, which "
                f"is not summable on {source}. Either add it to group_by or drop "
                "it from the column map."
            )


def test_non_summable_columns_are_only_used_where_nothing_sums_them():
    """The one view that projects raw dimension columns declares no group_by.

    View 40 lists payment failures with the bucket's `top_failure_reason`, a
    column `analytics_rollups` documents as a label that must NEVER be counted.
    Reading it is fine; grouping by it is not, and this asserts the difference
    has not been erased by adding a `group_by`.
    """
    view = registry.get_view("payments", "payment-failure")
    assert view is not None
    columns = view.params.get("columns") or {}
    if "top_failure_reason" in columns.values():
        assert not view.params.get("group_by"), (
            "View 40 projects top_failure_reason, which AggPaymentDaily documents "
            "as 'label only — never a key, never counted'. Grouping by it turns a "
            "convenience label into a distribution it cannot support."
        )


# ---------------------------------------------------------------------------
# 4 + 5. The eight dashboards, and the one that must stay unwired
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "number,module_slug,view_slug",
    [p for p in PRIORITY_VIEWS if p[0] != MARKETING_CHANNEL_VIEW],
    ids=[p[2] for p in PRIORITY_VIEWS if p[0] != MARKETING_CHANNEL_VIEW],
)
def test_priority_dashboards_declare_a_binding(
    number: int, module_slug: str, view_slug: str
):
    """Seven of the eight name the rollup they read.

    Without `params` the resolvers fall back to guesswork: a breakdown asks for
    a metric that lives in a different rollup, finds none, and reports itself
    `not_configured`. Naming the source is the minimum that stops that.
    """
    view = registry.get_view(module_slug, view_slug)
    assert view is not None, f"View {number} ({view_slug}) is missing from the registry"
    assert view.number == number
    assert view.params, (
        f"View {number} ({view_slug}) has empty params. It is one of the eight "
        "production dashboards and must be bound to a rollup."
    )
    assert view.params.get("source") or view.params.get("fn"), (
        f"View {number} ({view_slug}) declares params but names neither a source "
        "nor a custom function, so nothing in it binds to stored data."
    )


def test_marketing_channel_performance_is_not_wired():
    """View 19 must stay empty. This is the test that matters most in this file.

    Channel and source/medium attribution needs GA4, and spend needs an ad
    platform; neither is connected, and nothing internal is a substitute —
    the store records orders, not sessions. Any binding here would render a
    fabricated channel split that is indistinguishable from a measured one.
    """
    view = registry.get_view("marketing", "marketing-channel-performance")
    assert view is not None
    assert view.number == MARKETING_CHANNEL_VIEW
    assert view.params == {}, (
        "View 19 (Marketing Channel Performance) has acquired a binding: "
        f"{view.params!r}. There is no internal source for channel attribution. "
        "Whatever column this points at, it is not sessions by source/medium, and "
        "publishing it under a marketing label is the exact failure this "
        "subsystem exists to prevent."
    )
    assert view.state is ViewState.INTEGRATION_REQUIRED
    assert view.state in GATED_STATES, (
        "View 19 must stay gated so the frontend never issues a request for it."
    )


def test_no_gated_view_carries_a_data_binding():
    """A gated view is answered without touching a resolver, so a binding on one
    is either dead or an argument for ungating it. Neither should pass review."""
    offenders = [
        f"{v.number}-{v.slug}: {sorted(v.params)}"
        for v in _views()
        if v.state in GATED_STATES and (v.params.get("metrics") or v.params.get("columns"))
    ]
    assert not offenders, (
        "Gated views declare metric or column bindings that no request will ever "
        "reach: " + "; ".join(offenders)
    )


# ---------------------------------------------------------------------------
# 6. Wiring must not change what a view claims it can show
# ---------------------------------------------------------------------------


def test_view_states_are_unchanged():
    """`params` is wiring. `state` is a promise. Filling one must not move the other.

    Asserted as an exact distribution rather than per view, so both directions
    fail: a FEATURE_REQUIRED view promoted to LIVE because a vaguely related
    column turned up, and a LIVE view quietly demoted to hide a broken binding.
    """
    counts = Counter(v.state for v in _views())
    assert dict(counts) == EXPECTED_STATE_COUNTS, (
        "The distribution of view states changed while wiring params.\n"
        f"  expected: { {s.value: n for s, n in EXPECTED_STATE_COUNTS.items()} }\n"
        f"  actual:   { {s.value: n for s, n in counts.items()} }\n"
        "A binding never earns a view a better state — the registry declares a "
        "ceiling and only a runtime probe may lower it."
    )
    assert sum(counts.values()) == 73


# ---------------------------------------------------------------------------
# 7. Honesty guards on the bindings themselves
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("view", _bound_views(), ids=_view_ids(_bound_views()))
def test_params_use_only_keys_a_resolver_reads(view: AnalyticsViewDefinition):
    unknown = set(view.params) - READABLE_PARAM_KEYS
    assert not unknown, (
        f"View {view.number} ({view.slug}) declares params {sorted(unknown)}, which "
        "no resolver reads. A key nothing consults still reads as configuration to "
        f"the next person. Readable keys: {', '.join(sorted(READABLE_PARAM_KEYS))}."
    )


@pytest.mark.parametrize("view", _bound_views(), ids=_view_ids(_bound_views()))
def test_declared_dimension_is_groupable(view: AnalyticsViewDefinition):
    """A dimension label must map to a real grouping column.

    `BreakdownResolver` reports `not_configured` for an unknown label rather than
    substituting an adjacent one, so an unknown dimension here is a view that
    silently shows nothing.
    """
    dimension = view.params.get("dimension")
    if dimension is None:
        return
    assert dimension in DIMENSION_BINDINGS, (
        f"View {view.number} ({view.slug}) breaks down by {dimension!r}, which no "
        f"rollup stores as a dimension. Groupable: {', '.join(sorted(DIMENSION_BINDINGS))}."
    )
    source = _source_of(view)
    if source is not None:
        column = DIMENSION_BINDINGS[dimension].column
        assert column in columns_for(source), (
            f"View {view.number} ({view.slug}) groups {source} by {dimension!r} "
            f"({column}), which is not a column of that rollup."
        )


@pytest.mark.parametrize("view", _bound_views(), ids=_view_ids(_bound_views()))
def test_custom_views_name_a_registered_function(view: AnalyticsViewDefinition):
    fn = view.params.get("fn")
    if fn is None:
        return
    assert view.resolver is ResolverId.CUSTOM, (
        f"View {view.number} ({view.slug}) names params['fn'] but uses the "
        f"{view.resolver.value!r} resolver, which never dispatches on it."
    )
    assert fn in CUSTOM_FUNCTIONS, (
        f"View {view.number} ({view.slug}) dispatches to custom function {fn!r}, "
        f"which is not registered. Known: {sorted(CUSTOM_FUNCTIONS)}."
    )


@pytest.mark.parametrize("view", _bound_views(), ids=_view_ids(_bound_views()))
def test_rebinding_a_catalogue_metric_restates_it_exactly(view: AnalyticsViewDefinition):
    """A registry binding beats the catalogue one, so it must not disagree with it.

    `binding_for` checks `params['metrics']` before `METRIC_BINDINGS`. A view
    that redeclares an already-bound metric therefore silently overrides it for
    that view only — which is how one screen ends up reporting a different
    `rto_rate` from the next. Redeclaring is allowed (a breakdown needs the
    metric named in `params` to appear in its rows at all); disagreeing is not.
    """
    source = _source_of(view) or ""
    for metric_id, spec in _metric_specs(view).items():
        catalogue = METRIC_BINDINGS.get(metric_id)
        if catalogue is None:
            continue
        declared = _binding_from_param(spec, source)
        mismatch = [
            field
            for field in ("source", "add", "sub", "over_add", "over_sub", "complement")
            if getattr(declared, field) != getattr(catalogue, field)
        ]
        if declared.scale != catalogue.scale:
            mismatch.append("scale")
        assert not mismatch, (
            f"View {view.number} ({view.slug}) redefines the catalogue metric "
            f"{metric_id!r} and disagrees with `METRIC_BINDINGS` on "
            f"{mismatch}. Two definitions of one metric id means two screens "
            "showing different numbers under the same label."
        )


@pytest.mark.parametrize("view", _bound_views(), ids=_view_ids(_bound_views()))
def test_no_kpi_is_bound_outside_its_declared_dimensions(view: AnalyticsViewDefinition):
    """`kpis.py` already says which splits a metric survives. Honour it.

    `net_revenue` is order-basis and includes tax and shipping, so its
    `dimensions` tuple deliberately omits `product`, `sku` and `category` —
    neither tax nor shipping can be attributed to a line. Binding it at product
    grain would publish net *merchandise* sales under the net *revenue* label,
    a number smaller by every non-merchandise component. This test is what
    stops that from looking like a reasonable fix for an empty chart.
    """
    dimension = view.params.get("dimension")
    if dimension is None:
        return
    for metric_id in _metric_specs(view):
        meta = kpi_catalogue.by_id(metric_id)
        if meta is None or not meta.dimensions:
            # Not a catalogue KPI (a display key from the view's own table or
            # chart), so there is no catalogue label to contradict.
            continue
        assert dimension in meta.dimensions, (
            f"View {view.number} ({view.slug}) breaks {metric_id!r} down by "
            f"{dimension!r}, which the KPI catalogue does not list among its "
            f"legitimate dimensions ({', '.join(meta.dimensions)}). The split "
            "would carry a defined label on a number that is not that figure."
        )


@pytest.mark.parametrize("view", _bound_views(), ids=_view_ids(_bound_views()))
def test_no_non_additive_column_is_ever_summed(view: AnalyticsViewDefinition):
    """Every params column is summed. Levels and distinct counts are not summable.

    `fetch_totals` sums over the whole window for a KPI card and `dense_points`
    sums over the bucket for a series. A closing stock balance added across
    thirty days reports thirty times the stock; a distinct session count added
    across seven days counts a returning visitor seven times. Both are wrong by
    the length of the window and neither raises.
    """
    source = _source_of(view)
    if source is None:
        return

    for metric_id, spec in _metric_specs(view).items():
        metric_source = (
            spec.get("source", source) if isinstance(spec, dict) else source
        )
        banned = NON_ADDITIVE_COLUMNS.get(metric_source, frozenset())
        for column in _spec_columns(spec):
            assert column not in banned, (
                f"View {view.number} ({view.slug}) sums {metric_source}.{column} "
                f"for {metric_id!r}. That column is a level or a distinct count "
                "and is not additive across buckets — see the class docstring in "
                "analytics_rollups.py."
            )

    if view.params.get("group_by"):
        banned = NON_ADDITIVE_COLUMNS.get(source, frozenset())
        for key, column in (view.params.get("columns") or {}).items():
            assert column not in banned, (
                f"View {view.number} ({view.slug}) groups rows and projects "
                f"{key!r} -> {source}.{column}, which would be summed across "
                "buckets even though it is not additive."
            )


def test_inventory_analytics_does_not_sum_the_stock_level():
    """The specific case the rule above exists for, asserted by name.

    View 27's headline chart is "Units on hand", and `agg_inventory_daily` holds
    exactly that as `stock_close`. It is deliberately NOT bound: the timeseries
    resolver sums a bucket's rows, so a weekly bucket would add seven closing
    balances together and draw a stock line seven times too high. The flows
    (`units_restocked`, `units_sold`) are bound instead, because they are.
    """
    view = registry.get_view("inventory", "inventory-analytics")
    assert view is not None
    metrics = _metric_specs(view)
    assert metrics, "View 27 must bind the stock movement flows"
    bound_columns = {c for spec in metrics.values() for c in _spec_columns(spec)}
    assert "stock_close" not in bound_columns
    assert "stock_value_close" not in bound_columns
    assert {"units_restocked", "units_sold"} <= bound_columns


# ---------------------------------------------------------------------------
# 8. End to end: the eight dashboards against a real database
# ---------------------------------------------------------------------------


class _AnalyticsReader:
    """Holds exactly the permissions the eight dashboards need, and nothing else.

    `AnalyticsViewService` only ever calls `has_permission`, so this exercises
    the real authorisation path without creating a user row — and without
    reaching for the shared `vinay@gmail.com` account, whose `is_admin` short
    circuit would make every check pass for the wrong reason.
    """

    is_admin = False

    def __init__(self, permissions: set[str]) -> None:
        self._permissions = permissions

    def has_permission(self, permission: str) -> bool:
        return permission in self._permissions


def _use_read_committed(db: Session) -> None:
    """Run this fixture transaction without gap locks (cf. queue.py).

    Under MySQL's default REPEATABLE READ, `_clear_slot`'s range DELETE over a
    mostly-EMPTY index range next-key-locks the gap up to the next populated
    index key — which, in a table whose nearest rows live decades away, spans
    every other run's 1982 slot too. Two concurrent runs then deadlock through
    gaps neither of them owns a row in, despite fully disjoint dates (observed:
    1213 deadlocks between one run's slot clear and the other's seed). READ
    COMMITTED takes no gap locks, so each run locks exactly the rows it deletes
    or inserts — all inside its own slot. Set per transaction, exactly as
    `RecomputeQueue._use_read_committed` does and for the same reason.
    """
    if not db.in_transaction():
        db.connection(execution_options={"isolation_level": "READ COMMITTED"})


def _clear_slot(db: Session) -> None:
    """Delete every rollup row in THIS RUN's date slot, whatever its generation.

    The slot is this run's private property — pid-derived, inside a year no
    other suite touches — so anything found in it is either this run's own rows
    or an orphan from a crashed run that landed on the same slot, possibly
    under a generation that has since been rotated. Deleting across generations
    here is what makes a rerun after a crash clean rather than a duplicate-key
    error or a doubled sum, and every predicate is a 1982 date, so this delete
    is provably incapable of touching live rollups (2026) or any other suite's
    sandbox.
    """
    _use_read_committed(db)
    for model in _OWNED_MODELS:
        db.execute(
            delete(model).where(
                model.bucket_date >= SANDBOX_START,
                model.bucket_date <= SANDBOX_END,
            )
        )
    db.commit()


@contextmanager
def sandbox() -> Iterator[tuple[Session, int]]:
    """A session plus one seeded five-day window in this run's private slot.

    The generation is the database's ACTIVE one rather than a private random
    value, because `AnalyticsViewService` reads it from the database and cannot
    be told otherwise. Isolation therefore comes entirely from the per-run date
    slot (see `_SLOT`). The slot is cleared before seeding as well as in the
    `finally`, so a run following a crashed run is clean — and no delete in
    this module ever names a date outside the run's own slot, which is what
    makes two concurrent invocations of this file safe.
    """
    db = SessionLocal()
    generation = int(active_generation(db).generation)
    try:
        db.rollback()
        _clear_slot(db)
        _seed(db, generation)
        yield db, generation
    finally:
        try:
            db.rollback()
            _clear_slot(db)
        finally:
            db.close()


def _day(offset: int) -> date:
    return date.fromordinal(SANDBOX_START.toordinal() + offset)


def _seed(db: Session, generation: int) -> None:
    """One small, complete day written five times across every rollup read here."""
    _use_read_committed(db)
    for offset in range(SANDBOX_DAYS):
        day = _day(offset)
        db.add(
            rollups.AggOrderDaily(
                bucket_date=day,
                tz_generation=generation,
                orders_total=10,
                orders_pending=1,
                orders_paid=5,
                orders_shipped=2,
                orders_delivered=1,
                orders_cancelled=1,
                order_value_created=Decimal("1000.00"),
                paid_order_value=Decimal("800.00"),
                gross_merchandise_sales=Decimal("900.00"),
                net_merchandise_sales=Decimal("850.00"),
                # The revenue bridge balances: 900 - 50 + 50 + 30 + 0 - 0 = 930.
                net_revenue=Decimal("930.00"),
                subtotal_sum=Decimal("900.00"),
                tax_sum=Decimal("50.00"),
                discount_sum=Decimal("50.00"),
                shipping_income=Decimal("30.00"),
                refund_sum=Decimal("0.00"),
                cogs_sum=Decimal("400.00"),
                units=20,
                costed_units=20,
                distinct_customers=8,
                new_customers=5,
                returning_customers=3,
            )
        )
        for product_id in (1, 2):
            db.add(
                rollups.AggProductDaily(
                    bucket_date=day,
                    tz_generation=generation,
                    product_id=product_id,
                    sku_snapshot=f"SKU-{product_id}",
                    category_id_snapshot=product_id,
                    units=10,
                    orders=5,
                    gross_merchandise_sales=Decimal("450.00"),
                    net_merchandise_sales=Decimal("425.00"),
                    line_cost=Decimal("200.00"),
                    costed_units=10,
                    returned_units=1,
                    returned_value=Decimal("42.00"),
                )
            )
            db.add(
                rollups.AggInventoryDaily(
                    bucket_date=day,
                    tz_generation=generation,
                    product_id=product_id,
                    sku_snapshot=f"SKU-{product_id}",
                    stock_close=100,
                    stock_value_close=Decimal("2000.00"),
                    units_sold=10,
                    units_restocked=5,
                    is_oos=False,
                    days_oos=0,
                )
            )
        db.add(
            rollups.AggFunnelDaily(
                bucket_date=day,
                tz_generation=generation,
                product_views=200,
                cart_views=90,
                items_added=80,
                checkouts_started=40,
                shipping_submitted=30,
                payments_initiated=25,
                payments_failed=5,
                orders_placed=10,
                distinct_sessions=150,
            )
        )
    db.commit()


def _filters() -> AnalyticsFilters:
    return AnalyticsFilters(
        period=Period.CUSTOM,
        date_from=SANDBOX_START,
        date_to=_day(SANDBOX_DAYS),
        comparison=Comparison.NONE,
    )


def test_priority_dashboards_return_a_data_envelope():
    """The seven wirable dashboards must answer with data and say where it came from.

    `sources` is the machine-readable half of the answer: `not_configured`
    returns an explicitly EMPTY list, so a non-empty one is the difference
    between "nothing is connected" and "connected, and here is the number".
    Asserted through `AnalyticsViewService` rather than the resolvers directly,
    so permissions, gating and envelope construction are all in the path.
    """
    targets = [p for p in PRIORITY_VIEWS if p[0] != MARKETING_CHANNEL_VIEW]
    with sandbox() as (db, _generation):
        permissions = {
            registry.get_view(module, slug).permission  # type: ignore[union-attr]
            for _n, module, slug in PRIORITY_VIEWS
        }
        service = AnalyticsViewService(db, _AnalyticsReader(permissions))

        for number, module_slug, view_slug in targets:
            envelope = service.resolve_view(
                module_slug, view_slug, _filters(), use_cache=False
            )
            assert isinstance(envelope, AnalyticsViewEnvelope), (
                f"View {number} ({view_slug}) returned a gated envelope; it is a "
                "non-gated dashboard and must resolve."
            )
            assert envelope.sources, (
                f"View {number} ({view_slug}) returned no sources, which is how "
                "this subsystem says 'nothing is wired up'. Its params bind it to "
                "a rollup, so it should be reporting provenance."
            )
            codes = {w.code for w in envelope.warnings}
            assert NOT_CONFIGURED not in codes, (
                f"View {number} ({view_slug}) still reports NOT_CONFIGURED: "
                + "; ".join(
                    w.message for w in envelope.warnings if w.code == NOT_CONFIGURED
                )
            )
            assert envelope.kpis or envelope.series or envelope.tables, (
                f"View {number} ({view_slug}) returned an envelope with no kpis, "
                "no series and no tables."
            )


def test_marketing_channel_performance_costs_nothing_and_returns_no_data():
    """View 19 answers with the gated envelope: no resolver, no query, no numbers."""
    with sandbox() as (db, _generation):
        view = registry.get_view("marketing", "marketing-channel-performance")
        assert view is not None
        service = AnalyticsViewService(db, _AnalyticsReader({view.permission}))

        envelope = service.resolve_view(
            "marketing", "marketing-channel-performance", _filters(), use_cache=False
        )
        assert isinstance(envelope, GatedViewEnvelope), (
            "View 19 must return the gated envelope. Anything with a data block "
            "invites a chart to be drawn through it."
        )
        assert envelope.sources == []
        assert envelope.availability == ViewState.INTEGRATION_REQUIRED.value
        assert "ga4_data_api" in envelope.requires
        assert envelope.limitation


def test_the_eight_dashboards_report_real_numbers():
    """Spot-check that the numbers coming back are the seeded ones, not zeros.

    Five seeded days at 10 orders and 930.00 net revenue each. If the bindings
    pointed at the wrong column this would come back null or zero, both of which
    look like a quiet week rather than a broken wire.
    """
    with sandbox() as (db, _generation):
        exec_view = registry.get_view("executive", "executive-overview")
        product_view = registry.get_view("products", "product-performance")
        assert exec_view is not None and product_view is not None
        service = AnalyticsViewService(
            db, _AnalyticsReader({exec_view.permission, product_view.permission})
        )

        overview = service.resolve_view(
            "executive", "executive-overview", _filters(), use_cache=False
        )
        assert overview.kpis["net_revenue"].value == Decimal("4650.00")
        assert overview.kpis["orders_count"].value == Decimal("50")
        # 4000.00 of paid order value over 40 orders in a revenue status.
        assert overview.kpis["aov"].value == Decimal("100.0000")
        # Lifetime repeat rate needs a customer population no rollup stores. It
        # must be null and must NAME what is missing, never read as 0%.
        repeat = overview.kpis["repeat_purchase_rate"]
        assert repeat.value is None
        assert repeat.inputs_missing

        products = service.resolve_view(
            "products", "product-performance", _filters(), use_cache=False
        )
        rows = products.tables["product_table"].rows
        assert len(rows) == 2, "two seeded products"
        assert {r["product"] for r in rows} == {1, 2}
        # 5 days x 425.00 per product, and 5 returned units out of 50 sold.
        assert all(r["revenue"] == Decimal("2125.00") for r in rows)
        assert all(r["return_rate"] == Decimal("10.0000") for r in rows)


# ---------------------------------------------------------------------------
# 9. The generated contract must carry the new params
# ---------------------------------------------------------------------------


def test_frontend_contract_is_regenerated_from_this_registry():
    """The committed JSON must be byte-identical to a fresh dump.

    The same guard `tests/unit/test_analytics_registry.py` applies, restated
    here because filling in `params` changes the artifact and the two must land
    in one change. The fix is always to regenerate, never to edit the JSON.
    """
    import scripts.dump_analytics_registry as dump

    expected = dump.render_contract()
    actual = dump.CONTRACT_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        "frontend/src/features/analytics/registry.contract.json is stale. "
        "Regenerate it: python backend/scripts/dump_analytics_registry.py"
    )


def test_contract_carries_the_bindings_it_was_generated_from():
    """`params` survives serialisation, so the frontend sees the same wiring."""
    import json

    import scripts.dump_analytics_registry as dump

    payload = json.loads(dump.render_contract())
    by_number = {
        v["number"]: v for m in payload["modules"] for v in m["views"]
    }
    for view in _views():
        assert by_number[view.number]["params"] == view.params, (
            f"View {view.number} ({view.slug}) params did not survive the dump."
        )
