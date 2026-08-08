"""The analytics response envelope — one shape for all 73 views.

Built centrally so no resolver can forget the honesty metadata. Every response
carries where its numbers came from, how fresh they are, what they are worth,
and what was missing — because a dashboard that shows a number without those is
asking to be trusted more than it deserves.

Deliberately a NEW module rather than an addition to `schemas/analytics.py`, so
the three existing endpoints (`/dashboard/overview`, `/analytics/sales`,
`/analytics/profit`) keep their exact current contract while the v2 surface is
built and reconciled beside them.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import Field
from app.schemas.base import AppSchema


class WarningCode:
    """Machine-readable reasons a number is less than it appears.

    Strings, not an enum, so a new code never needs a migration or a coordinated
    frontend release — an unrecognised code still renders its message.
    """

    NO_ROLLUP_YET = "NO_ROLLUP_YET"
    ROLLUP_STALE = "ROLLUP_STALE"
    PARTIAL_TODAY = "PARTIAL_TODAY"
    COST_RULE_MISSING = "COST_RULE_MISSING"
    COST_COVERAGE_LOW = "COST_COVERAGE_LOW"
    GATEWAY_FEE_ESTIMATED = "GATEWAY_FEE_ESTIMATED"
    LINE_DISCOUNT_ALLOCATED = "LINE_DISCOUNT_ALLOCATED"
    TAX_RATE_BASIS_CURRENT = "TAX_RATE_BASIS_CURRENT"
    BACKFILLED_CURRENT_CATALOG = "BACKFILLED_CURRENT_CATALOG"
    PRODUCT_NAME_NOT_SNAPSHOTTED = "PRODUCT_NAME_NOT_SNAPSHOTTED"
    NO_INVENTORY_HISTORY_BEFORE = "NO_INVENTORY_HISTORY_BEFORE"
    FUNNEL_STARTS_AT_CART = "FUNNEL_STARTS_AT_CART"
    GST_NOT_COMPLIANCE_GRADE = "GST_NOT_COMPLIANCE_GRADE"
    TZ_REBUILD_IN_PROGRESS = "TZ_REBUILD_IN_PROGRESS"
    SMALL_SAMPLE = "SMALL_SAMPLE"
    REFUND_TIMING_APPROXIMATE = "REFUND_TIMING_APPROXIMATE"


class AnalyticsWarning(AppSchema):
    code: str
    severity: str = Field(default="warn", pattern="^(info|warn|error)$")
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class SourceRef(AppSchema):
    """Provenance for one input. Rendered as the view's source footnote.

    `through` is the newest bucket the source actually holds — the difference
    between that and the requested window is exactly how stale the answer is,
    and stating it is what stops "no rows yet" reading as "zero sales".
    """

    id: str
    kind: str = Field(pattern="^(rollup|live|external|derived)$")
    label: str
    rows: int | None = None
    through: date | None = None


class KpiValue(AppSchema):
    """One metric, with everything needed to judge it.

    `value` is nullable on purpose. Null means *not computable* — a missing cost
    rule, an unconnected integration, an undefined ratio over a zero base. It is
    never coerced to 0, because a chart cannot distinguish a real zero from a
    fabricated one and the reader cannot either.
    """

    kpi_id: str
    value: Decimal | None = None
    previous: Decimal | None = None
    delta_pct: Decimal | None = None
    format: str = "int"
    quality: str = "AUTHORITATIVE"
    coverage_pct: Decimal | None = None
    inputs_missing: list[str] = Field(default_factory=list)


class TableBlock(AppSchema):
    rows: list[dict[str, Any]] = Field(default_factory=list)
    total_rows: int = 0
    #: True when the row cap bit. Surfaced so a reader knows the table is a
    #: top-N and not the whole population.
    truncated: bool = False


class ViewMeta(AppSchema):
    module: str
    view: str
    number: int
    title: str


class ResolvedFilters(AppSchema):
    """What the server ACTUALLY used, echoed back.

    Not what was requested — a client that omitted a filter, or sent one this
    view ignores, must be able to see the window the numbers really cover.
    """

    period: str
    date_from: date
    date_to: date
    compare_from: date | None = None
    compare_to: date | None = None
    granularity: str
    comparison: str
    status_basis: str
    dimension: str | None = None
    limit: int = 20
    applied: dict[str, Any] = Field(default_factory=dict)
    ignored: list[str] = Field(default_factory=list)


class CacheMeta(AppSchema):
    hit: bool = False
    ttl_sec: int = 0
    generation: int = 0


class AnalyticsViewEnvelope(AppSchema):
    """The single response shape for every analytics view."""

    view: ViewMeta
    filters: ResolvedFilters
    availability: str
    #: Roll-up of the worst KPI quality on the view. A view carrying any
    #: INCOMPLETE metric can never be presented as authoritative.
    quality: str = "AUTHORITATIVE"
    coverage_pct: Decimal | None = None
    is_partial: bool = False

    kpis: dict[str, KpiValue] = Field(default_factory=dict)
    series: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    tables: dict[str, TableBlock] = Field(default_factory=dict)

    sources: list[SourceRef] = Field(default_factory=list)
    freshness: str = "daily"
    #: Newest watermark across the sources. Null means nothing has aggregated
    #: yet — which the UI must render as "not yet computed", not as "now".
    last_updated_at: datetime | None = None
    computed_at: datetime | None = None
    tz_generation: int = 1
    timezone: str = "Asia/Kolkata"
    currency: str = "INR"
    cache: CacheMeta = Field(default_factory=CacheMeta)
    warnings: list[AnalyticsWarning] = Field(default_factory=list)

    #: Set only for gated views. Names what is missing and what would fix it, so
    #: the UI can say something useful instead of rendering an empty chart.
    requires: list[str] = Field(default_factory=list)
    limitation: str = ""


class GatedViewEnvelope(AppSchema):
    """Returned for INTEGRATION_REQUIRED / FEATURE_REQUIRED / NOT_APPLICABLE.

    A distinct, deliberately small shape: no `kpis`, no `series`, no `tables`.
    There is nothing to render, and an empty-but-present data block invites a
    client to draw an axis through it as if the answer were zero.
    """

    view: ViewMeta
    availability: str
    requires: list[str] = Field(default_factory=list)
    limitation: str = ""
    sources: list[SourceRef] = Field(default_factory=list)
