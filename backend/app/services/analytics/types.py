"""Shared contract for the analytics subsystem — enums and definition shapes.

This module is deliberately dependency-free (stdlib only, no SQLAlchemy, no
FastAPI, no app imports). Three things build directly on it and must agree:

  * ``registry.py``  — the 12 modules and 73 views
  * ``kpis.py``      — the KPI catalogue
  * ``backend/scripts/dump_analytics_registry.py`` — serialises both into
    ``frontend/src/features/analytics/registry.contract.json``

Everything here is JSON-serialisable on purpose. React needs the same slugs,
permissions, availability and KPI metadata the backend uses, and the only way
to guarantee they never drift is to generate the frontend's copy from this one.
Presentation (Lucide icons, chart colour choices, grid spans) deliberately does
NOT live here — it stays in ``frontend/src/features/analytics/presentation.js``,
keyed by the slugs below, because React components cannot be serialised.

House style follows ``app/services/permissions_registry.py``: frozen dataclasses
plus module-level tuples, so the whole contract is importable, diffable and
testable without a database.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ViewState(str, Enum):
    """What a view can currently show.

    The registry declares a *ceiling*. A runtime probe may only ever downgrade
    it (LIVE -> PARTIAL when a rollup has no rows yet, for example), never
    upgrade it — otherwise a fresh install would claim data it does not have.
    """

    #: Real data, documented formula, tests pass, source + freshness displayed.
    LIVE = "LIVE"
    #: Real data with a stated limitation (short history, allocated costs, gap).
    PARTIAL = "PARTIAL"
    #: Needs an external source that is not connected (GA4, ads, Clarity, GSC).
    INTEGRATION_REQUIRED = "INTEGRATION_REQUIRED"
    #: Needs a business capability this system does not have (suppliers, B2B).
    FEATURE_REQUIRED = "FEATURE_REQUIRED"
    #: Source exists in principle but is unreachable or unusable here.
    BLOCKED_BY_MISSING_SOURCE = "BLOCKED_BY_MISSING_SOURCE"
    #: The concept does not apply to this deployment (branch comparison in a
    #: single-store install). Unlike FEATURE_REQUIRED this will never become
    #: LIVE without a change to the business model, so the UI says so plainly
    #: instead of implying "coming soon".
    NOT_APPLICABLE = "NOT_APPLICABLE"


#: States for which the frontend must NOT issue a network request at all.
GATED_STATES: frozenset[ViewState] = frozenset(
    {
        ViewState.INTEGRATION_REQUIRED,
        ViewState.FEATURE_REQUIRED,
        ViewState.NOT_APPLICABLE,
        ViewState.BLOCKED_BY_MISSING_SOURCE,
    }
)


class MetricQuality(str, Enum):
    """What a specific number is actually worth.

    Carried per KPI and per cost component, then rolled up to the view. A view
    containing any INCOMPLETE metric can never be reported as LIVE.
    """

    #: Straight from the internal transactional record. Money's highest grade.
    AUTHORITATIVE = "AUTHORITATIVE"
    #: Observed real value from a trusted source (e.g. a real settled gateway fee).
    ACTUAL = "ACTUAL"
    #: Derived by a documented, reconciled allocation rule (order -> line).
    ALLOCATED = "ALLOCATED"
    #: Derived from a cost rule or assumption rather than an observation.
    ESTIMATED = "ESTIMATED"
    #: Inputs are missing. coverage_pct says how much is known. NEVER zero-filled.
    INCOMPLETE = "INCOMPLETE"


#: Ordered worst -> best. Rolling a view's quality up takes the WORST component,
#: because one incomplete input makes the whole figure incomplete.
QUALITY_RANK: tuple[MetricQuality, ...] = (
    MetricQuality.INCOMPLETE,
    MetricQuality.ESTIMATED,
    MetricQuality.ALLOCATED,
    MetricQuality.ACTUAL,
    MetricQuality.AUTHORITATIVE,
)


def worst_quality(qualities: "list[MetricQuality] | tuple[MetricQuality, ...]") -> MetricQuality:
    """Roll several component qualities up to one. Empty input is INCOMPLETE."""
    if not qualities:
        return MetricQuality.INCOMPLETE
    return min(qualities, key=QUALITY_RANK.index)


class Freshness(str, Enum):
    """How current a view's data is expected to be. Drives the cache TTL."""

    REALTIME = "realtime"  # direct query / Redis counter, ~45s TTL
    HOURLY = "hourly"  # hourly rollup, ~900s TTL
    DAILY = "daily"  # daily rollup, ~3600s TTL


class DataSource(str, Enum):
    """Where a view's numbers come from.

    Financial figures must always resolve to INTERNAL_DB. GA4 supplies traffic,
    campaigns, devices and web journeys; Clarity supplies qualitative UX
    investigation only. GA4 purchase revenue is never the accounting value.
    """

    INTERNAL_DB = "internal_db"
    GA4 = "ga4"
    GOOGLE_SEARCH_CONSOLE = "google_search_console"
    CLARITY = "microsoft_clarity"
    ADS = "ad_platforms"
    PAYMENT_GATEWAY = "payment_gateway"
    COURIER = "courier"


class ResolverId(str, Enum):
    """The reusable server-side query shapes. A view names one.

    Adding a resolver is cheap and reusable; adding a bespoke React component is
    neither. When a view needs a shape none of these produce, the correct fix is
    a new resolver, not a new component.
    """

    METRICS = "metrics"
    TIMESERIES = "timeseries"
    BREAKDOWN = "breakdown"
    TABLE = "table"
    FUNNEL = "funnel"
    COHORT = "cohort"
    GEO = "geo"
    RECONCILIATION = "reconciliation"
    TRACKING_HEALTH = "tracking_health"
    CUSTOM = "custom"


class FormatId(str, Enum):
    """How a value is rendered. Frontend `format.js` must export one per entry."""

    MONEY = "money"
    INT = "int"
    PCT = "pct"
    DAYS = "days"
    HOURS = "hours"
    RATIO = "ratio"
    COMPACT = "compact"
    TEXT = "text"


class FilterKey(str, Enum):
    """Shared filter vocabulary. A view honours only the keys it declares."""

    DATE_RANGE = "date_range"
    COMPARISON = "comparison"
    GRANULARITY = "granularity"
    PRODUCT = "product"
    SKU = "sku"
    CATEGORY = "category"
    CUSTOMER_SEGMENT = "customer_segment"
    NEW_OR_RETURNING = "new_or_returning"
    SOURCE = "source"
    MEDIUM = "medium"
    CAMPAIGN = "campaign"
    DEVICE = "device"
    COUNTRY = "country"
    STATE = "state"
    CITY = "city"
    PAYMENT_METHOD = "payment_method"
    PAYMENT_GATEWAY = "payment_gateway"
    COURIER = "courier"
    ORDER_STATUS = "order_status"
    FULFILMENT_STATUS = "fulfilment_status"
    RETURN_REASON = "return_reason"
    COUPON = "coupon"
    MARKETPLACE = "marketplace"
    WAREHOUSE = "warehouse"
    CHANNEL = "channel"


#: Capability ids a view can declare in `requires`. A non-LIVE view MUST name at
#: least one of these, so the gated UI can tell the admin exactly what is
#: missing rather than showing an unexplained empty state.
class Capability(str, Enum):
    # External integrations
    GA4_MEASUREMENT = "ga4_measurement"
    GA4_DATA_API = "ga4_data_api"
    GTM_CONTAINER = "gtm_container"
    CLARITY_PROJECT = "clarity_project"
    SEARCH_CONSOLE = "search_console"
    AD_PLATFORM = "ad_platform"
    EMAIL_SMS_PLATFORM = "email_sms_platform"
    AFFILIATE_PLATFORM = "affiliate_platform"
    GATEWAY_SETTLEMENT_API = "gateway_settlement_api"
    COURIER_SCAN_API = "courier_scan_api"
    SOCIAL_COMMERCE_API = "social_commerce_api"
    BANK_CASH_FEED = "bank_cash_feed"

    # Internal instrumentation this project adds
    CART_EVENTS = "cart_events"
    INVENTORY_LEDGER = "inventory_ledger"
    COST_RULES = "cost_rules"
    ORDER_LINE_FACT = "order_line_fact"
    BUDGETS = "budgets"
    EXPERIMENTS = "experiments"
    #: Settlement rows exist in `payment_settlements` — satisfied the moment a
    #: gateway settlement report is uploaded (CSV) or, one day, pulled by an API
    #: client. Deliberately distinct from GATEWAY_SETTLEMENT_API: the API is an
    #: external integration that remains unbuilt, whereas the *report* is
    #: internal data a finance person can supply this afternoon. A view keyed on
    #: this capability is fed by uploads and must not claim it needs the API.
    GATEWAY_SETTLEMENT_REPORT = "gateway_settlement_report"

    # Business features this deployment does not have
    PRODUCT_VARIANTS = "product_variants"
    MULTI_STORE = "multi_store"
    MARKETPLACE_CHANNEL = "marketplace_channel"
    B2B_ACCOUNTS = "b2b_accounts"
    SUBSCRIPTIONS = "subscriptions"
    SUPPLIERS = "suppliers"
    WAREHOUSES = "warehouses"
    RECOMMENDATION_ENGINE = "recommendation_engine"
    SUPPORT_TICKETING = "support_ticketing"
    HSN_TAX_DETAIL = "hsn_tax_detail"


@dataclass(frozen=True)
class ChartSpec:
    """One chart on a view. `id` must match a key in the envelope's `series`."""

    id: str
    title: str
    type: str  # line | area | bar | stacked-bar | hbar | pie | donut | scatter | waterfall | sparkline
    x: str
    series: tuple[str, ...]
    format: FormatId = FormatId.INT
    span: int = 1  # grid columns out of 2
    empty_hint: str = ""


@dataclass(frozen=True)
class TableColumn:
    key: str
    label: str
    format: FormatId = FormatId.TEXT
    align: str = "left"
    sortable: bool = True


@dataclass(frozen=True)
class TableSpec:
    """One table on a view. `id` must match a key in the envelope's `tables`."""

    id: str
    title: str
    columns: tuple[TableColumn, ...]
    default_sort: str = ""
    default_sort_dir: str = "desc"
    page_size: int = 25
    empty_hint: str = ""


@dataclass(frozen=True)
class AnalyticsViewDefinition:
    """One of the 73 detailed views.

    `number` is the stable 1..73 id from the product brief and never changes,
    even if a view moves module or is renamed — it is what VIEW_STATUS.md and
    every downstream discussion refers to.

    `params` are SERVER-TRUSTED. The client sends only a view slug plus filters;
    it can never supply a table name, column list or grouping key. Resolvers
    validate `params.source` and `params.metrics` against per-source allowlists.
    """

    number: int
    name: str
    slug: str
    summary: str
    permission: str
    resolver: ResolverId
    freshness: Freshness
    state: ViewState
    sources: tuple[DataSource, ...] = (DataSource.INTERNAL_DB,)
    requires: tuple[Capability, ...] = ()
    filters: tuple[FilterKey, ...] = ()
    kpis: tuple[str, ...] = ()
    charts: tuple[ChartSpec, ...] = ()
    tables: tuple[TableSpec, ...] = ()
    params: dict[str, Any] = field(default_factory=dict)
    bespoke: str = ""  # key into the frontend's CLOSED bespoke component map
    export: bool = False
    group: str = ""  # optional tab-group label inside the module
    keywords: tuple[str, ...] = ()  # extra command-menu search terms
    limitation: str = ""  # one line shown on gated/partial views


@dataclass(frozen=True)
class AnalyticsModuleDefinition:
    """One of the 12 sidebar modules."""

    number: int
    name: str
    slug: str
    summary: str
    permission: str
    icon: str  # presentation key resolved to a Lucide component in React
    views: tuple[AnalyticsViewDefinition, ...]

    @property
    def default_view_slug(self) -> str:
        return self.views[0].slug if self.views else ""


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------
# `asdict` on a frozen dataclass returns plain dicts; Enum members inherit from
# `str`, so json.dumps emits their values without a custom encoder. Keys are
# sorted at dump time so the generated artifact is byte-stable and a drift test
# can compare it verbatim.


def _clean(value: Any) -> Any:
    """Recursively convert a dataclass tree to JSON-native types."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def view_to_dict(view: AnalyticsViewDefinition) -> dict[str, Any]:
    return _clean(asdict(view))


def module_to_dict(module: AnalyticsModuleDefinition) -> dict[str, Any]:
    data = _clean(asdict(module))
    data["default_view_slug"] = module.default_view_slug
    return data
