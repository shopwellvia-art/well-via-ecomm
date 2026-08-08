"""The resolver contract — what every analytics view runs through.

A resolver turns a *registry view definition* plus *request filters* into the
data half of the response envelope. It does not know about HTTP, permissions,
caching or Redis, and it does not write SQL: every query goes through
``AnalyticsRepository``, which owns the allowlists that keep a table name from
ever being client-supplied.

Three rules are enforced here rather than left to each resolver, because each of
them has exactly one correct answer and dozens of tempting wrong ones:

**Quality rolls up to the worst component.** ``ResolverResult.rolled_up()``
takes ``worst_quality`` over every KPI it carries. One INCOMPLETE metric makes
the whole view incomplete — an average or a "mostly authoritative" would let a
view built on a missing cost rule present itself as measured fact.

**A number that cannot be computed is None, never 0.** ``build_kpi`` refuses to
emit a value when ``inputs_missing`` is non-empty. A zero is a measurement; a
null is an admission. The two must never be rendered by the same code path,
because a chart cannot tell them apart and neither can the reader.

**Provenance is not optional.** ``source_ref`` builds the ``SourceRef`` from the
repository's own watermark, so "which rollup" and "through when" are read from
the data rather than asserted by the resolver. ``watermark_warnings`` turns that
same watermark into ``NO_ROLLUP_YET`` / ``ROLLUP_STALE``, which is the only
thing standing between "nothing has aggregated yet" and "you made no sales".

Registration mirrors ``services/analytics/aggregation/base.py``: a module-level
``RESOLVERS`` dict and a ``@register`` decorator, so importing
``app.services.analytics.resolvers`` is all the endpoint needs to do.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Iterable, Protocol, runtime_checkable

from sqlalchemy.orm import Session

from app.schemas.analytics_view import (
    AnalyticsWarning,
    KpiValue,
    SourceRef,
    TableBlock,
    WarningCode,
)
from app.services.analytics import kpis as kpi_catalogue
from app.services.analytics.filters import AnalyticsFilters, ResolvedWindow
from app.services.analytics.types import (
    AnalyticsViewDefinition,
    MetricQuality,
    worst_quality,
)

# Reused rather than restated: it already returns None when the previous value
# is <= 0, which is the correct answer. Growth from zero is undefined, not
# infinite, and not 100%.
from app.services.dashboard_service import _pct_delta

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.repositories.analytics_repository import AnalyticsRepository


__all__ = [
    "ResolverContext",
    "ResolverResult",
    "Resolver",
    "ResolverError",
    "UnknownResolver",
    "RESOLVERS",
    "register",
    "get_resolver",
    "build_kpi",
    "missing_kpi",
    "source_ref",
    "watermark_warnings",
    "warn",
    "SourceState",
    "probe_source",
    "guard_tz_generation",
    "not_configured",
    "NOT_CONFIGURED",
]

#: Warning code for "this view needs something that is not connected".
#: Deliberately distinct from NO_ROLLUP_YET: a rollup that has not run yet will
#: fill in on its own, whereas this one never will until an admin connects
#: something. Collapsing the two would make "wait" and "act" look identical.
#: `WarningCode` documents that codes are plain strings precisely so a new one
#: needs no migration and no coordinated frontend release.
NOT_CONFIGURED = "NOT_CONFIGURED"


class ResolverError(Exception):
    """A resolver could not run at all.

    Distinct from "ran and found nothing": an empty result is a legitimate
    answer that the envelope can carry with a warning, whereas this means the
    request was malformed (an unknown ``custom`` function, a sort column the
    table spec does not declare) and must surface as a 4xx/5xx rather than as a
    blank chart the reader will interpret as zero.
    """


class UnknownResolver(ResolverError):
    """No resolver is registered under that id."""


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolverContext:
    """Everything a resolver is allowed to know about the request.

    Deliberately does NOT carry the raw request, the user, or the permission
    set. A resolver that can see the caller is a resolver that can be made to
    return different numbers for different people, and reconciling *that* after
    the fact is impossible.

    ``today`` is the STORE-LOCAL reporting day from ``timebox``, never
    ``date.today()`` — containers run UTC and would roll the window over 5.5
    hours early.
    """

    db: Session
    repo: "AnalyticsRepository"
    view: AnalyticsViewDefinition
    filters: AnalyticsFilters
    window: ResolvedWindow
    tz_generation: int
    today: date

    @property
    def params(self) -> dict[str, Any]:
        """The view's SERVER-TRUSTED parameters.

        These come from the registry, never from the request. The client sends a
        view slug and filters; it can never name a table, a column or a grouping
        key. The repository re-validates whatever is read from here against its
        own allowlist, so a bad registry entry fails loudly instead of reaching
        SQL.
        """
        return self.view.params or {}

    def param(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolverResult:
    """The data half of the response envelope.

    Frozen because the endpoint composes results (a view may run more than one
    resolver-shaped step) and a shared mutable default would leak rows between
    two views served by the same process.
    """

    kpis: dict[str, KpiValue] = field(default_factory=dict)
    series: dict[str, list[dict]] = field(default_factory=dict)
    tables: dict[str, TableBlock] = field(default_factory=dict)
    sources: list[SourceRef] = field(default_factory=list)
    warnings: list[AnalyticsWarning] = field(default_factory=list)
    quality: MetricQuality = MetricQuality.AUTHORITATIVE
    coverage_pct: Decimal | None = None

    # -- quality -----------------------------------------------------------

    def rolled_up(self) -> "ResolverResult":
        """Return a copy whose ``quality`` is the worst of its components.

        Called once at the end of every resolver. A view carrying one INCOMPLETE
        KPI is INCOMPLETE, full stop — there is no weighting and no majority
        vote, because the reader is going to act on the worst number on the
        screen, not the average of them.
        """
        components = [MetricQuality(k.quality) for k in self.kpis.values()]
        if not components:
            # Nothing to roll up: keep whatever the resolver declared. Empty
            # input to `worst_quality` is INCOMPLETE, which would wrongly
            # condemn a table-only view that is perfectly authoritative.
            return self
        components.append(self.quality)
        return replace(self, quality=worst_quality(components))

    def merge(self, other: "ResolverResult") -> "ResolverResult":
        """Combine two results, taking the worst quality of the two."""
        return ResolverResult(
            kpis={**self.kpis, **other.kpis},
            series={**self.series, **other.series},
            tables={**self.tables, **other.tables},
            sources=[*self.sources, *other.sources],
            warnings=_dedupe_warnings([*self.warnings, *other.warnings]),
            quality=worst_quality([self.quality, other.quality]),
            coverage_pct=_min_coverage(self.coverage_pct, other.coverage_pct),
        )

    @property
    def is_empty(self) -> bool:
        return not (self.kpis or self.series or self.tables)


def _min_coverage(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def _dedupe_warnings(items: list[AnalyticsWarning]) -> list[AnalyticsWarning]:
    """One warning per (code, detail) pair.

    Two series read off the same stale rollup should say ROLLUP_STALE once, not
    twice — a duplicated warning reads as two separate problems.
    """
    seen: set[tuple[str, str]] = set()
    out: list[AnalyticsWarning] = []
    for w in items:
        key = (w.code, repr(sorted(w.detail.items())))
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
    return out


# ---------------------------------------------------------------------------
# Protocol + registry
# ---------------------------------------------------------------------------


@runtime_checkable
class Resolver(Protocol):
    """One reusable server-side query shape. A registry view names one."""

    id: str

    def run(self, ctx: ResolverContext) -> ResolverResult: ...


RESOLVERS: dict[str, Resolver] = {}


def register(resolver: Resolver) -> Resolver:
    """Register a resolver under its ``id``.

    Raises on a duplicate id rather than overwriting: two resolvers answering to
    ``timeseries`` would make which one runs depend on import order, and the
    symptom would be a chart that changes shape between deploys.
    """
    if resolver.id in RESOLVERS:
        raise ResolverError(f"resolver id {resolver.id!r} is already registered")
    RESOLVERS[resolver.id] = resolver
    return resolver


def get_resolver(resolver_id: str) -> Resolver:
    try:
        return RESOLVERS[resolver_id]
    except KeyError as exc:
        raise UnknownResolver(
            f"no resolver registered for {resolver_id!r}; "
            f"known: {sorted(RESOLVERS)}"
        ) from exc


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def warn(
    code: str,
    message: str,
    *,
    severity: str = "warn",
    **detail: Any,
) -> AnalyticsWarning:
    return AnalyticsWarning(
        code=code, severity=severity, message=message, detail=detail
    )


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    # str() first: Decimal(0.1) is 0.1000000000000000055511151231257827, and
    # money arithmetic downstream would inherit that.
    return Decimal(str(value))


def build_kpi(
    kpi_id: str,
    value: Any,
    *,
    previous: Any = None,
    coverage_pct: Any = None,
    inputs_missing: Iterable[str] = (),
    quality: MetricQuality | None = None,
) -> KpiValue:
    """Build one KPI card, taking format/quality/tooltip from the catalogue.

    The formula is never restated here — ``kpis.by_id`` is the single source for
    what a metric means, so a definition change lands in one file and every card
    that shows it follows.

    ``inputs_missing`` is load-bearing: if anything is named there the value is
    forced to None and the grade to INCOMPLETE, whatever was passed in. A caller
    that computes 0 from a missing input and then also names the missing input
    would otherwise publish a confident zero with a footnote nobody reads.
    """
    missing = [m for m in inputs_missing if m]
    meta = kpi_catalogue.by_id(kpi_id)

    if missing:
        return KpiValue(
            kpi_id=kpi_id,
            value=None,
            previous=None,
            delta_pct=None,
            format=meta.unit if meta else "int",
            quality=MetricQuality.INCOMPLETE.value,
            coverage_pct=_as_decimal(coverage_pct),
            inputs_missing=missing,
        )

    now_val = _as_decimal(value)
    prev_val = _as_decimal(previous)

    delta: Decimal | None = None
    if now_val is not None and prev_val is not None:
        raw = _pct_delta(float(now_val), float(prev_val))
        delta = None if raw is None else Decimal(str(raw))

    if quality is not None:
        grade = quality
    elif now_val is None:
        # No value and nothing named as missing still is not a measurement.
        grade = MetricQuality.INCOMPLETE
    elif meta is not None:
        grade = meta.default_quality
    else:
        grade = MetricQuality.AUTHORITATIVE

    return KpiValue(
        kpi_id=kpi_id,
        value=now_val,
        previous=prev_val,
        delta_pct=delta,
        format=meta.unit if meta else "int",
        quality=grade.value,
        coverage_pct=_as_decimal(coverage_pct),
        inputs_missing=[],
    )


def missing_kpi(kpi_id: str, *inputs: str) -> KpiValue:
    """A KPI that could not be computed. Value is None; the inputs are named."""
    return build_kpi(kpi_id, None, inputs_missing=inputs or ("unknown",))


def source_ref(
    source_id: str,
    *,
    label: str = "",
    kind: str = "rollup",
    rows: int | None = None,
    through: date | None = None,
) -> SourceRef:
    """Provenance for one input, with the watermark the caller measured."""
    return SourceRef(
        id=source_id,
        kind=kind,
        label=label or source_id,
        rows=rows,
        through=through,
    )


def watermark_warnings(
    source_id: str,
    watermark: date | None,
    window: ResolvedWindow,
) -> list[AnalyticsWarning]:
    """Turn a rollup watermark into the honest warning about what it covers.

    Two distinct conditions, deliberately not collapsed into one code:

    ``NO_ROLLUP_YET``  — the source holds nothing at all. There is no series to
    draw and no zero to report; the aggregation job has not run.

    ``ROLLUP_STALE``   — the source holds data, but it stops before the end of
    the requested window. The tail of the chart is missing, not flat.

    The window is half-open, so the last reporting day it asks for is
    ``date_to - 1``; a watermark equal to that is current, not stale.
    """
    last_requested = window.date_to.toordinal() - 1
    if watermark is None:
        return [
            warn(
                WarningCode.NO_ROLLUP_YET,
                f"{source_id} has no rows yet, so this period has not been "
                "aggregated. This is 'not computed', not 'zero'.",
                severity="warn",
                source=source_id,
                requested_through=window.date_to.isoformat(),
            )
        ]
    if watermark.toordinal() < last_requested:
        return [
            warn(
                WarningCode.ROLLUP_STALE,
                f"{source_id} is aggregated only through {watermark.isoformat()}; "
                f"the window runs to {date.fromordinal(last_requested).isoformat()}. "
                "Days after the watermark are missing, not empty.",
                severity="warn",
                source=source_id,
                through=watermark.isoformat(),
                requested_through=date.fromordinal(last_requested).isoformat(),
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Source probing + guards
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceState:
    """One rollup's freshness, as measured rather than assumed."""

    source: str
    watermark: date | None
    rows: int
    ref: SourceRef
    warnings: list[AnalyticsWarning] = field(default_factory=list)

    @property
    def has_rows(self) -> bool:
        """Whether the source holds anything at all for this generation.

        False is the condition under which a resolver must return *nothing*
        rather than zeros: with no rows anywhere there is no measurement to
        report, and a zero would be manufactured.
        """
        return self.rows > 0 and self.watermark is not None

    def covered_through(self, window: ResolvedWindow) -> date | None:
        """The last reporting day of `window` this source can actually answer.

        None means it can answer no day of the window at all. Everything after
        this date is a gap, and must be left out of a series rather than
        zero-filled — filling it draws a confident line through missing data,
        which is the single failure this whole subsystem exists to prevent.
        """
        if self.watermark is None:
            return None
        last_requested = date.fromordinal(window.date_to.toordinal() - 1)
        end = min(self.watermark, last_requested)
        return end if end >= window.date_from else None


def guard_tz_generation(ctx: ResolverContext, source: str) -> None:
    """Refuse a window that straddles a reporting-timezone rebuild.

    Buckets cut under two different day boundaries cannot be added together,
    and the resulting number is wrong in a way no later check can detect —
    the rows look identical. `TzGenerationMixed` propagates to the endpoint,
    which turns it into `TZ_REBUILD_IN_PROGRESS`.
    """
    from app.services.analytics.timebox import assert_single_generation

    windows = [ctx.window]
    if ctx.window.has_comparison:
        windows.append(
            ResolvedWindow(
                date_from=ctx.window.compare_from,  # type: ignore[arg-type]
                date_to=ctx.window.compare_to,  # type: ignore[arg-type]
            )
        )
    seen: set[int] = set()
    for window in windows:
        seen |= ctx.repo.distinct_tz_generations(source, window)
    # An empty set is "no rows in the window", which is an empty state for the
    # caller to describe — not a mixed-generation error.
    if len(seen) > 1:
        assert_single_generation(seen)


def probe_source(
    ctx: ResolverContext, source: str, *, label: str = "", kind: str = "rollup"
) -> SourceState:
    """Read a source's watermark, build its `SourceRef`, and guard generations.

    Every resolver calls this before reading, so provenance and freshness come
    from the data rather than from a resolver's assertion, and no result can be
    returned without saying which rollup it read and how far that rollup goes.
    """
    guard_tz_generation(ctx, source)
    watermark, rows = ctx.repo.source_watermark(source, ctx.tz_generation)
    return SourceState(
        source=source,
        watermark=watermark,
        rows=rows,
        ref=source_ref(source, label=label or source, kind=kind, rows=rows, through=watermark),
        warnings=watermark_warnings(source, watermark, ctx.window),
    )


def not_configured(
    message: str,
    *,
    requires: Iterable[str] = (),
    detail: dict[str, Any] | None = None,
) -> ResolverResult:
    """A result for a view whose underlying data does not exist yet.

    Carries the reason and NOTHING else: no series, no tables, no zeroed KPIs,
    and an explicitly EMPTY `sources` list. The empty source list is the
    machine-readable half of the answer — a caller can tell "nothing is
    configured" from "configured and healthy" by the absence of any provenance,
    without parsing a message. Returning `{"series": {"x": []}}` here would let
    a chart draw an axis through nothing and read as a measured zero.
    """
    return ResolverResult(
        sources=[],
        warnings=[
            warn(
                NOT_CONFIGURED,
                message,
                severity="error",
                requires=sorted(requires),
                **(detail or {}),
            )
        ],
        quality=MetricQuality.INCOMPLETE,
    )
