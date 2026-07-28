"""The SP1 gate for the analytics registry — 12 modules, 73 views, no lies.

Regression cover for the two failure modes that make a 73-view analytics
subsystem rot:

1. **Structural drift.** A view gets added twice, renumbered, given a slug that
   collides with another module's, or points at a permission / KPI / resolver
   that does not exist. Nobody notices until a route 404s in production.
2. **Dishonest states.** A view claims `LIVE` while depending on a GA4 property
   nobody connected, or sits in `INTEGRATION_REQUIRED` with an empty
   `limitation`, so the admin gets an unexplained blank panel instead of "this
   needs GA4". The whole point of `ViewState` is that the UI can be honest, and
   that only holds if every non-LIVE view is *forced* to say what it is missing.

Plus the Python <-> JS drift guard: the committed
`frontend/src/features/analytics/registry.contract.json` must be byte-identical
to freshly generated content, so React can never restate a stale copy of the 73
slugs.

Pure logic — no database, no network, no fixtures. Safe to run anywhere::

    pytest tests/unit/test_analytics_registry.py -q
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.services.analytics.kpis import KPIS, all_kpi_ids, kpi_to_dict
from app.services.analytics.registry import MODULES
from app.services.analytics.types import (
    AnalyticsModuleDefinition,
    AnalyticsViewDefinition,
    Capability,
    ChartSpec,
    DataSource,
    FormatId,
    ResolverId,
    TableColumn,
    ViewState,
    module_to_dict,
    view_to_dict,
)
from app.services.permissions_registry import all_permission_names

# --------------------------------------------------------------------------
# Constants the whole suite agrees on
# --------------------------------------------------------------------------

EXPECTED_MODULE_COUNT = 12
EXPECTED_VIEW_COUNT = 73
EXPECTED_VIEW_NUMBERS = frozenset(range(1, EXPECTED_VIEW_COUNT + 1))

#: A slug goes straight into `/admin/analytics/{module}/{view}`. Lowercase,
#: kebab-case, starts with a letter — no underscores, no trailing dash, nothing
#: that needs URL-encoding or that differs only by case on a case-folding host.
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]*$")

#: Views carrying money must resolve to the transactional record. GA4 purchase
#: revenue is a marketing estimate, never the accounting value.
FINANCE_PERMISSION = "analytics.finance.view"

#: A bespoke React component is the expensive escape hatch: it cannot be reused,
#: cannot be generated, and has to be maintained by hand forever. Ten is the
#: budget for genuinely unique visualisations (waterfalls, cohort grids,
#: reconciliation ledgers). Everything else must go through a resolver.
MAX_BESPOKE_VIEWS = 10

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent
DUMP_SCRIPT = BACKEND_DIR / "scripts" / "dump_analytics_registry.py"

# --------------------------------------------------------------------------
# Capability classification
# --------------------------------------------------------------------------
# Mirrors the three groups in `types.py`. Split out here because the honesty
# invariant "a LIVE view may not depend on an external integration" needs to
# know which capabilities are somebody else's system. `test_every_capability_is_
# classified` fails loudly if a new Capability is added without being sorted
# into one of these buckets, so the invariant can never silently go stale.

EXTERNAL_INTEGRATION_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.GA4_MEASUREMENT,
        Capability.GA4_DATA_API,
        Capability.GTM_CONTAINER,
        Capability.CLARITY_PROJECT,
        Capability.SEARCH_CONSOLE,
        Capability.AD_PLATFORM,
        Capability.EMAIL_SMS_PLATFORM,
        Capability.AFFILIATE_PLATFORM,
        Capability.GATEWAY_SETTLEMENT_API,
        Capability.COURIER_SCAN_API,
        Capability.SOCIAL_COMMERCE_API,
        Capability.BANK_CASH_FEED,
    }
)

INTERNAL_INSTRUMENTATION_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.CART_EVENTS,
        Capability.INVENTORY_LEDGER,
        Capability.COST_RULES,
        Capability.ORDER_LINE_FACT,
        Capability.BUDGETS,
        Capability.EXPERIMENTS,
    }
)

BUSINESS_FEATURE_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.PRODUCT_VARIANTS,
        Capability.MULTI_STORE,
        Capability.MARKETPLACE_CHANNEL,
        Capability.B2B_ACCOUNTS,
        Capability.SUBSCRIPTIONS,
        Capability.SUPPLIERS,
        Capability.WAREHOUSES,
        Capability.RECOMMENDATION_ENGINE,
        Capability.SUPPORT_TICKETING,
        Capability.HSN_TAX_DETAIL,
    }
)

# --------------------------------------------------------------------------
# Flattened views + parametrize helpers
# --------------------------------------------------------------------------

ALL_VIEWS: tuple[AnalyticsViewDefinition, ...] = tuple(
    view for module in MODULES for view in module.views
)
VIEW_OWNER: dict[str, AnalyticsModuleDefinition] = {
    view.slug: module for module in MODULES for view in module.views
}


def _view_cases(views=ALL_VIEWS):
    """One parametrize case per view, id'd by number+slug.

    A failure then reads `test_x[41-cart-abandonment]` instead of `test_x[17]`,
    which is the difference between a two-second fix and a bisect.
    """
    return [pytest.param(view, id=f"{view.number:02d}-{view.slug}") for view in views]


def _module_cases():
    return [pytest.param(module, id=f"{module.number:02d}-{module.slug}") for module in MODULES]


def _view_by_number(number: int) -> AnalyticsViewDefinition:
    matches = [view for view in ALL_VIEWS if view.number == number]
    assert matches, f"No view numbered {number} in the registry."
    return matches[0]


ALL_VIEW_CASES = _view_cases()
MODULE_CASES = _module_cases()
FINANCE_VIEWS = tuple(view for view in ALL_VIEWS if view.permission == FINANCE_PERMISSION)


# ==========================================================================
# Structure
# ==========================================================================


def test_exactly_twelve_modules():
    assert len(MODULES) == EXPECTED_MODULE_COUNT, (
        f"The analytics parent has exactly {EXPECTED_MODULE_COUNT} modules; the "
        f"registry declares {len(MODULES)}: {[m.slug for m in MODULES]}"
    )


def test_exactly_seventy_three_views():
    per_module = {m.slug: len(m.views) for m in MODULES}
    assert len(ALL_VIEWS) == EXPECTED_VIEW_COUNT, (
        f"The brief specifies exactly {EXPECTED_VIEW_COUNT} views; the registry "
        f"declares {len(ALL_VIEWS)}. Per module: {per_module}"
    )


def test_view_numbers_are_exactly_one_to_seventy_three():
    numbers = [view.number for view in ALL_VIEWS]
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    missing = sorted(EXPECTED_VIEW_NUMBERS - set(numbers))
    extra = sorted(set(numbers) - EXPECTED_VIEW_NUMBERS)
    assert not duplicates, f"Duplicate view numbers: {duplicates}"
    assert not missing, f"Missing view numbers (gaps in 1..73): {missing}"
    assert not extra, f"View numbers outside 1..73: {extra}"
    assert set(numbers) == EXPECTED_VIEW_NUMBERS


def test_every_view_number_belongs_to_exactly_one_module():
    owners: dict[int, list[str]] = {}
    for module in MODULES:
        for view in module.views:
            owners.setdefault(view.number, []).append(module.slug)
    shared = {number: mods for number, mods in owners.items() if len(mods) > 1}
    assert not shared, f"View numbers claimed by more than one module: {shared}"


def test_view_slugs_unique_across_all_modules():
    """Not just unique within a module — the command menu and deep links are flat."""
    seen: dict[str, list[str]] = {}
    for module in MODULES:
        for view in module.views:
            seen.setdefault(view.slug, []).append(f"{module.slug}#{view.number}")
    collisions = {slug: where for slug, where in seen.items() if len(where) > 1}
    assert not collisions, f"View slugs used by more than one view: {collisions}"


def test_module_slugs_unique():
    slugs = [module.slug for module in MODULES]
    duplicates = sorted({s for s in slugs if slugs.count(s) > 1})
    assert not duplicates, f"Duplicate module slugs: {duplicates}"


def test_module_numbers_unique():
    numbers = [module.number for module in MODULES]
    assert len(set(numbers)) == len(numbers), f"Duplicate module numbers: {sorted(numbers)}"


@pytest.mark.parametrize("module", MODULE_CASES)
def test_module_slug_is_route_safe(module: AnalyticsModuleDefinition):
    assert SLUG_RE.match(module.slug), (
        f"Module {module.number} slug {module.slug!r} is not route-safe "
        f"(must match {SLUG_RE.pattern})"
    )


@pytest.mark.parametrize("view", ALL_VIEW_CASES)
def test_view_slug_is_route_safe(view: AnalyticsViewDefinition):
    assert SLUG_RE.match(view.slug), (
        f"View {view.number} slug {view.slug!r} is not route-safe "
        f"(must match {SLUG_RE.pattern}) — it goes straight into "
        f"/admin/analytics/<module>/<view>"
    )


@pytest.mark.parametrize("module", MODULE_CASES)
def test_module_has_views_and_resolvable_default(module: AnalyticsModuleDefinition):
    assert module.views, f"Module {module.slug} has no views — the sidebar entry would dead-end."
    default = module.default_view_slug
    assert default, f"Module {module.slug} has no default_view_slug."
    assert default in {view.slug for view in module.views}, (
        f"Module {module.slug} default_view_slug={default!r} is not one of its own views "
        f"{sorted(v.slug for v in module.views)}"
    )


@pytest.mark.parametrize("view", ALL_VIEW_CASES)
def test_view_has_human_readable_identity(view: AnalyticsViewDefinition):
    assert view.name.strip(), f"View {view.number} has no name."
    assert view.summary.strip(), (
        f"View {view.number} ({view.slug}) has no summary — the command menu and the "
        "module index both show it."
    )


# ==========================================================================
# Referential integrity
# ==========================================================================


@pytest.mark.parametrize("view", ALL_VIEW_CASES)
def test_view_permission_exists(view: AnalyticsViewDefinition):
    known = set(all_permission_names())
    assert view.permission in known, (
        f"View {view.number} ({view.slug}) requires permission {view.permission!r}, "
        "which is not in permissions_registry.PERMISSIONS. Add it there or the route "
        "can never be granted to anyone."
    )


@pytest.mark.parametrize("module", MODULE_CASES)
def test_module_permission_exists(module: AnalyticsModuleDefinition):
    known = set(all_permission_names())
    assert module.permission in known, (
        f"Module {module.slug} requires permission {module.permission!r}, which is not "
        "in permissions_registry.PERMISSIONS."
    )


@pytest.mark.parametrize("view", ALL_VIEW_CASES)
def test_view_kpis_exist_in_catalogue(view: AnalyticsViewDefinition):
    known = set(all_kpi_ids())
    unknown = [kpi_id for kpi_id in view.kpis if kpi_id not in known]
    assert not unknown, (
        f"View {view.number} ({view.slug}) references KPI ids {unknown} that are not in "
        "kpis.KPIS. Every KPI must carry a documented formula and quality grade."
    )


def test_kpi_ids_are_unique():
    ids = list(all_kpi_ids())
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    assert not duplicates, f"Duplicate KPI ids in the catalogue: {duplicates}"
    assert len(ids) == len(KPIS)


@pytest.mark.parametrize("view", ALL_VIEW_CASES)
def test_view_resolver_is_valid(view: AnalyticsViewDefinition):
    assert isinstance(view.resolver, ResolverId), (
        f"View {view.number} ({view.slug}) resolver={view.resolver!r} is not a ResolverId "
        f"member. Valid: {[r.value for r in ResolverId]}"
    )


@pytest.mark.parametrize("view", ALL_VIEW_CASES)
def test_view_state_and_freshness_are_enum_members(view: AnalyticsViewDefinition):
    assert isinstance(view.state, ViewState), (
        f"View {view.number} state={view.state!r} is not a ViewState member."
    )
    assert view.freshness is not None, f"View {view.number} has no freshness class."


@pytest.mark.parametrize("view", ALL_VIEW_CASES)
def test_chart_and_table_formats_are_valid(view: AnalyticsViewDefinition):
    """`format.js` on the frontend exports exactly one renderer per FormatId."""
    bad: list[str] = []
    for chart in view.charts:
        assert isinstance(chart, ChartSpec)
        if not isinstance(chart.format, FormatId):
            bad.append(f"chart {chart.id!r} format={chart.format!r}")
    for table in view.tables:
        for column in table.columns:
            assert isinstance(column, TableColumn)
            if not isinstance(column.format, FormatId):
                bad.append(f"table {table.id!r} column {column.key!r} format={column.format!r}")
    assert not bad, (
        f"View {view.number} ({view.slug}) uses formats the frontend cannot render: {bad}. "
        f"Valid: {[f.value for f in FormatId]}"
    )


@pytest.mark.parametrize("view", ALL_VIEW_CASES)
def test_requires_are_valid_capabilities(view: AnalyticsViewDefinition):
    bad = [cap for cap in view.requires if not isinstance(cap, Capability)]
    assert not bad, (
        f"View {view.number} ({view.slug}) declares requires={bad} that are not Capability "
        "members — the gated UI would not know what to tell the admin."
    )


def test_every_capability_is_classified():
    """Adding a Capability without classifying it must fail here, not silently.

    The "a LIVE view may not depend on an external integration" invariant is only
    as good as this classification.
    """
    classified = (
        EXTERNAL_INTEGRATION_CAPABILITIES
        | INTERNAL_INSTRUMENTATION_CAPABILITIES
        | BUSINESS_FEATURE_CAPABILITIES
    )
    unclassified = sorted(c.value for c in set(Capability) - classified)
    assert not unclassified, (
        f"New Capability members {unclassified} are not classified in this test. Add each to "
        "EXTERNAL_INTEGRATION_CAPABILITIES, INTERNAL_INSTRUMENTATION_CAPABILITIES or "
        "BUSINESS_FEATURE_CAPABILITIES."
    )
    overlaps = (
        (EXTERNAL_INTEGRATION_CAPABILITIES & INTERNAL_INSTRUMENTATION_CAPABILITIES)
        | (EXTERNAL_INTEGRATION_CAPABILITIES & BUSINESS_FEATURE_CAPABILITIES)
        | (INTERNAL_INSTRUMENTATION_CAPABILITIES & BUSINESS_FEATURE_CAPABILITIES)
    )
    assert not overlaps, f"Capabilities classified twice: {sorted(c.value for c in overlaps)}"


# ==========================================================================
# Honesty invariants — the point of the whole exercise
# ==========================================================================


@pytest.mark.parametrize("view", ALL_VIEW_CASES)
def test_non_live_view_states_what_it_needs(view: AnalyticsViewDefinition):
    """A view that cannot show real data must say what is missing, and why.

    Without this, a gated view renders as an unexplained empty panel and the
    admin concludes the product is broken rather than unconfigured.
    """
    if view.state is ViewState.LIVE:
        pytest.skip("LIVE views are covered by test_live_view_needs_no_external_integration")

    assert view.requires, (
        f"View {view.number} ({view.slug}) is {view.state.value} but declares no `requires`. "
        "Name the capability it is waiting on so the gated UI can tell the admin exactly "
        "what to connect."
    )
    assert view.limitation.strip(), (
        f"View {view.number} ({view.slug}) is {view.state.value} but has an empty "
        "`limitation`. Write the one line the admin will read instead of a blank chart."
    )


@pytest.mark.parametrize("view", ALL_VIEW_CASES)
def test_live_view_needs_no_external_integration(view: AnalyticsViewDefinition):
    """LIVE means "works right now" — it cannot hinge on somebody else's system."""
    if view.state is not ViewState.LIVE:
        pytest.skip("Only LIVE views are constrained here")

    external = sorted(
        cap.value for cap in view.requires if cap in EXTERNAL_INTEGRATION_CAPABILITIES
    )
    assert not external, (
        f"View {view.number} ({view.slug}) claims LIVE while requiring external "
        f"integrations {external}. If those are unconfigured the view shows nothing, so "
        f"its honest state is INTEGRATION_REQUIRED (or PARTIAL with a limitation)."
    )


@pytest.mark.parametrize("view", ALL_VIEW_CASES)
def test_gated_view_is_not_marked_live(view: AnalyticsViewDefinition):
    """A limitation without a downgraded state is a state that lies by omission."""
    if view.state is ViewState.LIVE:
        assert not view.limitation.strip(), (
            f"View {view.number} ({view.slug}) is LIVE but carries a limitation "
            f"({view.limitation!r}). If there is a real caveat the state is PARTIAL."
        )


def test_bespoke_component_budget():
    used = sorted(
        (view.number, view.slug, view.bespoke) for view in ALL_VIEWS if view.bespoke
    )
    assert len(used) <= MAX_BESPOKE_VIEWS, (
        f"{len(used)} views declare a bespoke component; the budget is "
        f"{MAX_BESPOKE_VIEWS}.\n"
        f"Bespoke views: {used}\n"
        "A bespoke React component cannot be reused, generated or covered by the shared "
        "resolver tests — it is hand-maintained forever. When a view needs a shape the "
        "resolvers do not produce, the correct fix is a NEW RESOLVER (server-side, "
        "reusable, testable), not a new component. Reserve bespoke for genuinely unique "
        "visualisations: waterfalls, cohort grids, reconciliation ledgers."
    )


def test_bespoke_keys_are_unique():
    keys = [view.bespoke for view in ALL_VIEWS if view.bespoke]
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    assert not duplicates, (
        f"Bespoke component keys reused by several views: {duplicates}. The frontend's "
        "bespoke map is CLOSED and keyed by this string — two views sharing a key means "
        "one of them renders the wrong component."
    )


def test_view_70_tax_and_gst_is_partial_and_not_compliance_grade():
    view = _view_by_number(70)
    assert view.state is ViewState.PARTIAL, (
        f"View 70 ({view.name}) must be PARTIAL, not {view.state.value}. It reports tax "
        "from order records without HSN-level detail."
    )
    limitation = view.limitation.lower()
    assert "compliance" in limitation, (
        f"View 70 ({view.name}) limitation must state plainly that it is NOT "
        f"compliance-grade — an admin must never file a return from it. Got: "
        f"{view.limitation!r}"
    )


def test_view_69_cash_flow_is_integration_required():
    view = _view_by_number(69)
    assert view.state is ViewState.INTEGRATION_REQUIRED, (
        f"View 69 ({view.name}) must be INTEGRATION_REQUIRED, not {view.state.value} — "
        "cash flow needs a bank/settlement feed this deployment does not have."
    )


def test_view_49_store_or_branch_performance_is_not_applicable():
    view = _view_by_number(49)
    assert view.state is ViewState.NOT_APPLICABLE, (
        f"View 49 ({view.name}) must be NOT_APPLICABLE, not {view.state.value} — this is "
        "a single-store deployment, so branch comparison will never become LIVE without a "
        "change to the business model. NOT_APPLICABLE says that; FEATURE_REQUIRED would "
        "imply 'coming soon'."
    )


def test_finance_permission_is_actually_used():
    """Guards the next test from passing vacuously if the permission is renamed."""
    assert FINANCE_VIEWS, (
        f"No view uses {FINANCE_PERMISSION!r}. Either the finance module lost its "
        "permission or it was renamed — update FINANCE_PERMISSION here so the "
        "internal-DB invariant below keeps biting."
    )


@pytest.mark.parametrize("view", _view_cases(FINANCE_VIEWS))
def test_financial_view_reads_the_internal_database(view: AnalyticsViewDefinition):
    """Money never comes only from GA4.

    GA4 purchase revenue is sampled, ad-blocked and client-reported. It is a
    marketing signal, not an accounting value.
    """
    sources = [source.value for source in view.sources]
    assert DataSource.INTERNAL_DB in view.sources, (
        f"Financial view {view.number} ({view.slug}) declares sources {sources} without "
        "internal_db. Financial figures must resolve to the transactional record; GA4 "
        "revenue is never the accounting value."
    )


# ==========================================================================
# Serialisation and drift
# ==========================================================================


def test_registry_round_trips_through_plain_json():
    """No custom encoder anywhere: str-Enums and frozen dataclasses only."""
    payload: dict[str, Any] = {
        "modules": [module_to_dict(module) for module in MODULES],
        "kpis": [kpi_to_dict(kpi) for kpi in KPIS],
    }
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    assert json.loads(text) == payload, "Registry does not survive a JSON round-trip."


@pytest.mark.parametrize("view", ALL_VIEW_CASES)
def test_view_serialises_to_json_native_types(view: AnalyticsViewDefinition):
    data = view_to_dict(view)
    json.dumps(data)  # raises TypeError on any non-native leaf
    assert data["number"] == view.number
    assert data["state"] == view.state.value
    assert data["resolver"] == view.resolver.value


def _load_dump_script():
    spec = importlib.util.spec_from_file_location("dump_analytics_registry", DUMP_SCRIPT)
    assert spec and spec.loader, f"Cannot load {DUMP_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frontend_contract_json_is_not_stale():
    """The one test that makes Python <-> JS drift impossible.

    React must never restate the 73 slugs. It imports
    `registry.contract.json`, which is generated from this registry — so the
    committed file has to be byte-identical to what the generator produces now.
    """
    dump = _load_dump_script()
    expected = dump.render_contract()
    path = Path(dump.CONTRACT_PATH)

    assert path.exists(), (
        f"{path.relative_to(REPO_ROOT)} is missing. Generate it:\n\n"
        f"    {dump.REGENERATE_COMMAND}\n"
    )
    actual = path.read_text(encoding="utf-8")
    assert actual == expected, (
        f"{path.relative_to(REPO_ROOT)} is STALE — it no longer matches "
        "app/services/analytics/registry.py + kpis.py.\n"
        "The JSON is generated; never hand-edit it. Regenerate and commit it with the "
        "Python change:\n\n"
        f"    {dump.REGENERATE_COMMAND}\n"
    )


def test_generated_contract_is_deterministic():
    """Two runs must be identical — no timestamp, hostname or set iteration order."""
    dump = _load_dump_script()
    assert dump.render_contract() == dump.render_contract()


def test_generated_contract_shape():
    dump = _load_dump_script()
    payload = json.loads(dump.render_contract())
    assert set(payload) == {"version", "generated_by", "modules", "kpis"}
    assert payload["version"] == dump.CONTRACT_VERSION
    assert payload["generated_by"] == "backend/scripts/dump_analytics_registry.py"
    assert len(payload["modules"]) == EXPECTED_MODULE_COUNT
    assert sum(len(m["views"]) for m in payload["modules"]) == EXPECTED_VIEW_COUNT
    # The frontend routes off these three keys; losing one silently breaks it.
    for module in payload["modules"]:
        assert {"slug", "views", "default_view_slug"} <= set(module)
        for view in module["views"]:
            assert {"number", "slug", "state", "permission", "resolver"} <= set(view)
