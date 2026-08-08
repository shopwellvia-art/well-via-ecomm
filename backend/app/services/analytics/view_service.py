"""Permission-aware resolution of the 73 analytics views.

This is the layer between HTTP and a resolver. It owns four things nothing else
is allowed to own, in this order, because the order *is* the security property:

  1. **The slug never reaches a resolver.** A view is looked up in the registry
     first; an unknown module or view is a 404 before anything else happens. The
     client sends two slugs and a filter set — it can never name a table, a
     column, a grouping key or a resolver.
  2. **Authorisation happens before the cache.** Not after, not alongside. A
     cache read that precedes the permission check leaks two things: whether an
     entry exists (a timing and behaviour oracle for "has anyone looked at
     margin today"), and — if the key were ever imperfectly scoped — the payload
     itself. Checking first makes both impossible regardless of how the key is
     built.
  3. **Gated views cost nothing.** A view whose registry state is in
     ``GATED_STATES`` returns a ``GatedViewEnvelope`` immediately: no resolver
     call, no repository call, no cache read, no cache write. 22 of the 73 views
     are gated today, and a UI that polls them must not be able to generate
     database load by doing so.
  4. **The visibility tier is part of the cache key.** ``analytics.finance.view``
     and ``analytics.customers.view`` are SENSITIVE tiers in
     ``permissions_registry``; a resolver may legitimately return more columns
     to a holder. Two requesters therefore share a cache entry only when they
     are entitled to exactly the same document.

Two smaller rules that are easy to get wrong and expensive to fix:

**``today`` comes from ``timebox``, never ``date.today()``.** Containers run
UTC; the store reports in Asia/Kolkata. ``date.today()`` rolls the window over
5.5 hours early, which silently moves roughly a fifth of a day's trade into the
neighbouring bucket. Every window in this module is resolved against
``local_day(now_utc, store_timezone(db))``.

**``refresh=true`` is a privileged operation.** It bypasses the cache read, so
an unauthenticated-for-that-purpose viewer holding it could stampede every
resolver in the registry with a loop over 73 slugs. It requires
``analytics.jobs.run`` and is refused with 403 — loudly, not silently ignored,
because a client that believes it forced a recompute and did not is worse off
than one that was told no.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import AppError, ForbiddenError, NotFoundError
from app.models.user import User
from app.schemas.analytics_view import (
    AnalyticsViewEnvelope,
    AnalyticsWarning,
    CacheMeta,
    GatedViewEnvelope,
    ResolvedFilters,
    SourceRef,
    ViewMeta,
    WarningCode,
)
from app.services.analytics import registry
from app.services.analytics.cache import AnalyticsCache, ttl_for
from app.services.analytics.filters import AnalyticsFilters, ResolvedWindow
from app.services.analytics.resolvers.base import (
    ResolverContext,
    ResolverError,
    ResolverResult,
    get_resolver,
)
from app.services.analytics.timebox import (
    active_generation,
    day_bounds_utc,
    local_day,
    store_timezone,
)
from app.services.analytics.types import (
    GATED_STATES,
    AnalyticsModuleDefinition,
    AnalyticsViewDefinition,
    Capability,
    FilterKey,
    MetricQuality,
    ViewState,
    module_to_dict,
    view_to_dict,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------

#: Everyone touching this surface needs this first. It only opens the section —
#: every module and every view still requires its own grant on top, which is why
#: holding it alone must not return a single number from a finance view.
BASE_PERMISSION = "analytics.view"

#: Bypassing the cache read is a load-generating operation, not a read.
REFRESH_PERMISSION = "analytics.jobs.run"

#: Exporting is a separate grant from viewing: a CSV leaves the building.
EXPORT_PERMISSION = "analytics.export"

#: The tiers marked SENSITIVE in `permissions_registry`. Order is part of the
#: cache-key contract — appending a new one lengthens every tier token, which
#: makes every previously-written key unreachable. That is the correct
#: behaviour: entries written before a new tier existed were computed without
#: knowing about it, and must not be served after it does.
SENSITIVE_PERMISSIONS: tuple[str, ...] = (
    "analytics.finance.view",
    "analytics.customers.view",
)

#: Currency is single-valued in this deployment. Stated on every envelope rather
#: than assumed, so the day a second currency appears the contract already has
#: somewhere to say so.
REPORTING_CURRENCY = "INR"


class ResolverUnavailableError(AppError):
    """503. The registry names a resolver that is not registered.

    Deliberately NOT rendered as a gated view. "This deployment cannot compute
    that yet" and "no resolver is implemented for this shape" look identical to
    a reader but are completely different to an operator: the first is a
    product statement, the second is a deployment gap that a redeploy fixes.
    Returning an empty-but-successful envelope for the second would hide a
    missing implementation behind a chart drawn through zero.
    """

    status_code = 503
    code = "resolver_unavailable"


# ---------------------------------------------------------------------------
# Filter-key mapping
# ---------------------------------------------------------------------------
# `AnalyticsFilters` carries the union of every dimension filter in the product.
# A view honours only the keys it declares in the registry. This map is what
# lets the envelope report `applied` vs `ignored` honestly — a client that sent
# `courier=DTDC` to a view with no courier dimension needs to be told the number
# it is looking at was NOT filtered, rather than left to assume it was.
_FIELD_TO_FILTER_KEY: dict[str, FilterKey] = {
    "product_id": FilterKey.PRODUCT,
    "sku": FilterKey.SKU,
    "category_id": FilterKey.CATEGORY,
    "customer_segment": FilterKey.CUSTOMER_SEGMENT,
    "new_or_returning": FilterKey.NEW_OR_RETURNING,
    "source": FilterKey.SOURCE,
    "medium": FilterKey.MEDIUM,
    "campaign": FilterKey.CAMPAIGN,
    "device": FilterKey.DEVICE,
    "country": FilterKey.COUNTRY,
    "state": FilterKey.STATE,
    "city": FilterKey.CITY,
    "payment_method": FilterKey.PAYMENT_METHOD,
    "payment_gateway": FilterKey.PAYMENT_GATEWAY,
    "courier": FilterKey.COURIER,
    "order_status": FilterKey.ORDER_STATUS,
    "coupon": FilterKey.COUPON,
}

#: Quality grades that mean "do not present this as measured fact".
_SOFT_QUALITIES = frozenset(
    {
        MetricQuality.ALLOCATED.value,
        MetricQuality.ESTIMATED.value,
        MetricQuality.INCOMPLETE.value,
    }
)


class AnalyticsViewService:
    """Resolve one analytics view for one user, honestly and only if allowed.

    Construct per request. ``cache`` and ``repo`` are injectable so a test can
    supply an in-memory double without Redis or MySQL; left unset they are built
    lazily on first use, which keeps a 403 and a gated view from touching either.
    """

    def __init__(
        self,
        db: Session,
        user: User,
        *,
        cache: Any | None = None,
        repo: Any | None = None,
    ) -> None:
        self.db = db
        self.user = user
        self._cache = cache
        self._repo = repo
        self._tz = None
        self._tz_generation: int | None = None

    # -- lazily-built collaborators ----------------------------------------
    # None of these are constructed in __init__: a request that 404s on an
    # unknown slug, 403s on a permission, or returns a gated envelope must not
    # open a Redis connection or read the tz-generation table to do it.

    @property
    def cache(self) -> Any:
        if self._cache is None:
            self._cache = AnalyticsCache()
        return self._cache

    @property
    def repo(self) -> Any:
        if self._repo is None:
            from app.repositories.analytics_repository import AnalyticsRepository

            self._repo = AnalyticsRepository(self.db)
        return self._repo

    @property
    def tz(self):
        if self._tz is None:
            self._tz = store_timezone(self.db)
        return self._tz

    @property
    def tz_generation(self) -> int:
        if self._tz_generation is None:
            self._tz_generation = int(active_generation(self.db).generation)
        return self._tz_generation

    def today(self) -> date:
        """The store-local reporting day.

        The single place this module learns what "today" is. Never
        ``date.today()``: see the module docstring.
        """
        return local_day(datetime.now(timezone.utc), self.tz)

    # ------------------------------------------------------------------
    # Permissions
    # ------------------------------------------------------------------
    def _holds(self, permission: str) -> bool:
        return bool(permission) and self.user.has_permission(permission)

    def can_see_view(self, view: AnalyticsViewDefinition) -> bool:
        """Whether this user may see `view` at all.

        The **view's** permission is authoritative, not its module's. Module 2
        is `sales-finance`, gated on `analytics.sales.view`, but three of its
        views are gated on the SENSITIVE `analytics.finance.view`. Requiring the
        module grant as well would lock a finance analyst out of the only views
        they exist to read; requiring only the module grant would hand margin to
        every sales user. Per-view is the only rule that gets both right.
        """
        return self._holds(view.permission)

    def visible_views(
        self, module: AnalyticsModuleDefinition
    ) -> tuple[AnalyticsViewDefinition, ...]:
        return tuple(v for v in module.views if self.can_see_view(v))

    def visibility_tier(self) -> str:
        """The cache-visibility token for this user's sensitive grants.

        A short deterministic flag string (`t00`, `t10`, `t01`, `t11`) over
        ``SENSITIVE_PERMISSIONS``, NOT a user id. Keying per user would give a
        hit rate near zero on a dashboard that 73 views deep-link into; keying
        per tier means two requesters share an entry only when they are entitled
        to byte-identical output.

        Matches `cache._SAFE_COMPONENT` (`[A-Za-z0-9_.-]+`) by construction, so
        it can never introduce a `:` and collide two tiers onto one key.
        """
        flags = "".join("1" if self._holds(p) else "0" for p in SENSITIVE_PERMISSIONS)
        return f"t{flags}"

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def navigation(self) -> dict[str, Any]:
        """Modules and views this user may open — and nothing else.

        A module appears only if at least one of its views is visible, and
        carries only the visible ones. Nothing in the payload counts, names or
        hints at what was filtered out: "3 more views (locked)" is a disclosure,
        not a feature, and on this surface it would tell a sales user exactly
        which margin views exist.

        `default_view_slug` is recomputed over the *visible* views rather than
        taken from the registry, otherwise the first module a restricted user
        opens redirects them straight into a 403.
        """
        modules: list[dict[str, Any]] = []
        for module in registry.MODULES:
            views = self.visible_views(module)
            if not views:
                continue
            modules.append(
                {
                    "number": module.number,
                    "name": module.name,
                    "slug": module.slug,
                    "summary": module.summary,
                    "icon": module.icon,
                    "permission": module.permission,
                    "default_view_slug": views[0].slug,
                    "views": [self._view_summary(v) for v in views],
                }
            )
        return {
            "modules": modules,
            "module_count": len(modules),
            "view_count": sum(len(m["views"]) for m in modules),
            "timezone": str(self.tz),
            "currency": REPORTING_CURRENCY,
        }

    def module_detail(self, module_slug: str) -> dict[str, Any]:
        """One module with its visible views in full (charts, tables, filters).

        404 for an unknown slug; 403 when the module exists but every view in it
        is hidden. The 12 module slugs are already public — they ship to the
        browser in `registry.contract.json` — so distinguishing the two here
        discloses nothing while giving an operator a usable error.
        """
        module = registry.get_module(module_slug)
        if module is None:
            raise NotFoundError(f"Unknown analytics module {module_slug!r}")
        views = self.visible_views(module)
        if not views:
            raise ForbiddenError(
                f"Missing required permission: {module.permission} "
                "(or the permission for any view in this module)"
            )
        payload = module_to_dict(module)
        payload["views"] = [view_to_dict(v) for v in views]
        payload["default_view_slug"] = views[0].slug
        return payload

    @staticmethod
    def _view_summary(view: AnalyticsViewDefinition) -> dict[str, Any]:
        """The light per-view payload used by navigation.

        Charts and table specs are deliberately absent: the sidebar renders 73
        of these and the full definitions already reach the browser through
        `registry.contract.json`. `gated` is precomputed so the UI can refuse to
        issue a request rather than learning it from a response.
        """
        return {
            "number": view.number,
            "name": view.name,
            "slug": view.slug,
            "summary": view.summary,
            "state": view.state.value,
            "gated": view.state in GATED_STATES,
            "freshness": view.freshness.value,
            "permission": view.permission,
            "requires": [c.value for c in view.requires],
            "limitation": view.limitation,
            "export": view.export,
            "group": view.group,
            "keywords": list(view.keywords),
        }

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------
    def resolve_view(
        self,
        module_slug: str,
        view_slug: str,
        filters: AnalyticsFilters,
        *,
        use_cache: bool = True,
    ) -> AnalyticsViewEnvelope | GatedViewEnvelope:
        """Resolve one view. The order of the steps below is the security model.

        ``use_cache=False`` is for the export path, which resolves the same view
        under a far larger row limit: writing that payload under the same key
        would poison the on-screen view with a 50 000-row table, and reading the
        on-screen entry would silently export 20 rows.
        """
        # 1. Registry first. An unknown slug never reaches a resolver, a
        #    repository or a cache key.
        view = registry.get_view(module_slug, view_slug)
        if view is None:
            if registry.get_module(module_slug) is None:
                raise NotFoundError(f"Unknown analytics module {module_slug!r}")
            raise NotFoundError(
                f"Unknown analytics view {view_slug!r} in module {module_slug!r}"
            )

        # 2. Authorise BEFORE the cache. See the module docstring.
        if not self.can_see_view(view):
            raise ForbiddenError(f"Missing required permission: {view.permission}")

        # 3. Gated views cost nothing: no query, no cache read, no cache write.
        if view.state in GATED_STATES:
            return self._gated_envelope(view)

        # 4. `refresh` is privileged — refused, never silently ignored.
        if filters.refresh and not self._holds(REFRESH_PERMISSION):
            raise ForbiddenError(
                "refresh=true bypasses the analytics cache and requires "
                f"{REFRESH_PERMISSION}. Omit it to read the cached answer."
            )

        today = self.today()
        window = filters.resolve(today)
        # Half-open: a window ending *tomorrow* is the one that contains today,
        # and today's bucket is still being written.
        includes_today = window.date_to > today
        tier = self.visibility_tier()

        # 5. Cache read. Only now, and only for a caller already authorised for
        #    exactly this view at exactly this tier.
        cache_key: str | None = None
        if use_cache:
            cache_key = self.cache.build_key(
                module=view_module_slug(view) or module_slug,
                view=view.slug,
                filter_hash=filters.cache_key_part(),
                tz_generation=self.tz_generation,
                visibility=tier,
            )
            if not filters.refresh:
                cached = self.cache.get(cache_key)
                if cached is not None:
                    envelope = _envelope_from_cache(cached)
                    if envelope is not None:
                        return envelope

        # 6. Dispatch.
        result = self._run_resolver(view, filters, window, today)

        envelope = self._build_envelope(
            module_slug=module_slug,
            view=view,
            filters=filters,
            window=window,
            result=result,
            includes_today=includes_today,
        )

        # 7. Cache write, TTL from the declared freshness, downgraded to the
        #    realtime TTL whenever the window touches today.
        if use_cache and cache_key is not None:
            ttl = ttl_for(view.freshness.value, includes_today)
            envelope.cache = CacheMeta(
                hit=False, ttl_sec=ttl, generation=self.cache.generation()
            )
            self.cache.set(cache_key, envelope.model_dump(mode="json"), ttl)
        return envelope

    def _run_resolver(
        self,
        view: AnalyticsViewDefinition,
        filters: AnalyticsFilters,
        window: ResolvedWindow,
        today: date,
    ) -> ResolverResult:
        """Build the resolver context and run it.

        The context deliberately carries no user and no permission set (see
        `ResolverContext`): a resolver that can see the caller is a resolver
        whose numbers can differ per caller in ways nothing downstream can
        reconcile. Column-level redaction, when it is needed, belongs to the
        tier that keys the cache — not to the query.
        """
        try:
            resolver = get_resolver(view.resolver.value)
        except ResolverError as exc:
            raise ResolverUnavailableError(
                f"No resolver is available for view {view.slug!r} "
                f"(resolver={view.resolver.value!r}). This is a deployment gap, "
                "not an empty result — no number is being reported as zero.",
                details={"resolver": view.resolver.value, "view": view.slug},
            ) from exc

        ctx = ResolverContext(
            db=self.db,
            repo=self.repo,
            view=view,
            filters=filters,
            window=window,
            tz_generation=self.tz_generation,
            today=today,
        )
        try:
            result = resolver.run(ctx)
        except ResolverError as exc:
            # A resolver that cannot run is a bad request shape, not a blank
            # chart. Surfacing it as an empty 200 is how "we had no sales" gets
            # written into a board deck.
            raise ResolverUnavailableError(
                f"Analytics view {view.slug!r} could not be resolved: {exc}",
                details={"view": view.slug},
            ) from exc
        return result.rolled_up()

    # ------------------------------------------------------------------
    # Envelope construction
    # ------------------------------------------------------------------
    def _gated_envelope(self, view: AnalyticsViewDefinition) -> GatedViewEnvelope:
        """The zero-cost answer for INTEGRATION_REQUIRED / FEATURE_REQUIRED /
        NOT_APPLICABLE / BLOCKED_BY_MISSING_SOURCE.

        Small on purpose: no `kpis`, no `series`, no `tables`. An
        empty-but-present data block invites a client to draw an axis through it
        as if the answer were zero, and this answer is not zero — it is "we
        cannot know".
        """
        return GatedViewEnvelope(
            view=_view_meta(view),
            availability=view.state.value,
            requires=[_capability_id(c) for c in view.requires],
            limitation=view.limitation,
            sources=[],
        )

    def _build_envelope(
        self,
        *,
        module_slug: str,
        view: AnalyticsViewDefinition,
        filters: AnalyticsFilters,
        window: ResolvedWindow,
        result: ResolverResult,
        includes_today: bool,
    ) -> AnalyticsViewEnvelope:
        warnings: list[AnalyticsWarning] = list(result.warnings)
        if includes_today:
            warnings.append(
                AnalyticsWarning(
                    code=WarningCode.PARTIAL_TODAY,
                    severity="info",
                    message=(
                        "The window includes today, which is still being "
                        "written. Today's figures will move."
                    ),
                    detail={"reporting_day": window.date_to.isoformat()},
                )
            )

        availability = _runtime_availability(view, result)
        quality = MetricQuality(result.quality).value
        last_updated = self._last_updated_at(result.sources)

        return AnalyticsViewEnvelope(
            view=_view_meta(view),
            filters=self._resolved_filters(view, filters, window),
            availability=availability,
            quality=quality,
            coverage_pct=_as_decimal(result.coverage_pct),
            is_partial=(
                availability != ViewState.LIVE.value
                or quality in _SOFT_QUALITIES
                or includes_today
            ),
            kpis=dict(result.kpis),
            series={k: list(v) for k, v in result.series.items()},
            tables=dict(result.tables),
            sources=list(result.sources),
            freshness=view.freshness.value,
            last_updated_at=last_updated,
            computed_at=datetime.now(timezone.utc),
            tz_generation=self.tz_generation,
            timezone=str(self.tz),
            currency=REPORTING_CURRENCY,
            cache=CacheMeta(hit=False, ttl_sec=0, generation=0),
            warnings=_dedupe(warnings),
            requires=[_capability_id(c) for c in view.requires],
            limitation=view.limitation,
        )

    def _last_updated_at(self, sources: list[SourceRef]) -> datetime | None:
        """The newest watermark across the sources, as a UTC instant.

        A watermark is a store-local reporting *day*; the honest instant to
        report is the moment that day closed, which is what
        ``day_bounds_utc(day, tz)[1]`` returns. Null when no source carries one
        at all — the UI must render that as "not yet computed", never as "now",
        because a timestamp of "now" over an empty rollup reads as a confident
        zero taken a second ago.
        """
        watermarks = [s.through for s in sources if s.through is not None]
        if not watermarks:
            return None
        newest = max(watermarks)
        return day_bounds_utc(newest, self.tz)[1]

    def _resolved_filters(
        self,
        view: AnalyticsViewDefinition,
        filters: AnalyticsFilters,
        window: ResolvedWindow,
    ) -> ResolvedFilters:
        """What the server ACTUALLY used, including what it threw away.

        `AnalyticsFilters` accepts the union of every dimension in the product so
        a URL copied between two views still opens. That convenience is only
        honest if the response says which of those keys this view ignored —
        otherwise a reader filters by courier on a view with no courier
        dimension and reads the unfiltered number as a filtered one.
        """
        declared = set(view.filters)
        applied: dict[str, Any] = {}
        ignored: list[str] = []
        for field_name, key in _FIELD_TO_FILTER_KEY.items():
            value = getattr(filters, field_name, None)
            if value in (None, ""):
                continue
            if key in declared:
                applied[field_name] = value
            else:
                ignored.append(field_name)

        return ResolvedFilters(
            period=filters.period.value,
            date_from=window.date_from,
            date_to=window.date_to,
            compare_from=window.compare_from,
            compare_to=window.compare_to,
            granularity=filters.granularity.value,
            comparison=filters.comparison.value,
            status_basis=filters.status_basis.value,
            dimension=filters.dimension,
            limit=filters.limit,
            applied=applied,
            ignored=sorted(ignored),
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------
# Free functions rather than methods where they need no user and no session, so
# the export path and the tests can reuse them without constructing a service.


_VIEW_MODULE_SLUG: dict[int, str] = {
    v.number: m.slug for m in registry.MODULES for v in m.views
}


def view_module_slug(view: AnalyticsViewDefinition) -> str | None:
    """The slug of the module a view belongs to.

    `AnalyticsViewDefinition` does not carry its parent — the registry nests
    them — so this is resolved once at import from the stable 1..73 `number`.
    """
    return _VIEW_MODULE_SLUG.get(view.number)


def _view_meta(view: AnalyticsViewDefinition) -> ViewMeta:
    return ViewMeta(
        module=view_module_slug(view) or "",
        view=view.slug,
        number=view.number,
        title=view.name,
    )


def _capability_id(cap: Capability | str) -> str:
    return cap.value if isinstance(cap, Capability) else str(cap)


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _runtime_availability(
    view: AnalyticsViewDefinition, result: ResolverResult
) -> str:
    """The declared state, downgraded by what the run actually found.

    Downgrade only, never upgrade — `types.ViewState` is explicit that the
    registry declares a ceiling. A LIVE view whose numbers came back INCOMPLETE
    is PARTIAL for this request; a PARTIAL view can never become LIVE because a
    single clean run happened to have every input.
    """
    if view.state is not ViewState.LIVE:
        return view.state.value
    if MetricQuality(result.quality) is MetricQuality.INCOMPLETE:
        return ViewState.PARTIAL.value
    return ViewState.LIVE.value


def _dedupe(items: list[AnalyticsWarning]) -> list[AnalyticsWarning]:
    """One warning per (code, detail). A repeated warning reads as two faults."""
    seen: set[tuple[str, str]] = set()
    out: list[AnalyticsWarning] = []
    for item in items:
        key = (item.code, repr(sorted(item.detail.items())))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _envelope_from_cache(payload: dict) -> AnalyticsViewEnvelope | None:
    """Rehydrate a cached payload, or None if it no longer validates.

    A payload written by an older deploy is a MISS, not a 500. The shape is
    versioned by `cache.KEY_NAMESPACE`, but a partial rollout can still put two
    shapes in flight; recomputing is always safe and overwrites the old entry on
    the way back.
    """
    try:
        envelope = AnalyticsViewEnvelope.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - any validation failure is a miss
        logger.debug("analytics cache payload no longer validates: %s", exc)
        return None
    envelope.cache = CacheMeta(
        hit=True,
        ttl_sec=envelope.cache.ttl_sec if envelope.cache else 0,
        generation=envelope.cache.generation if envelope.cache else 0,
    )
    return envelope
