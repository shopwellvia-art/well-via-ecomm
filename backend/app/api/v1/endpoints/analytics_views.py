"""The analytics v2 read surface — navigation, one view, and CSV export.

Routes (mounted by the lead under the `/analytics` prefix)
---------------------------------------------------------
============================================ ====== ==========================
GET  /modules                                200    permission-filtered nav
GET  /modules/{module_slug}                  200    one module, visible views
GET  /modules/{module_slug}/views/{view_slug} 200   THE view envelope
GET  /kpis                                   200    KPI catalogue for tooltips
POST /exports                                200    CSV `StreamingResponse`
============================================ ====== ==========================

All paths are relative and all of them start `/modules`, `/kpis` or `/exports`,
so none can shadow the two legacy static routes the lead keeps mounted at
`/analytics/sales` and `/analytics/profit`. Those three endpoints keep their
exact current contract while this surface is reconciled beside them.

Permissions are layered, never merged
-------------------------------------
`analytics.view` opens the section and is asserted as a router-level dependency,
so a route added later cannot forget it. It is deliberately NOT sufficient for
anything: the view's own permission is then checked inside
`AnalyticsViewService.resolve_view`, before the cache and before any query. A
holder of `analytics.view` alone gets 403 on every finance and customer view,
which is the entire point of `analytics.finance.view` and
`analytics.customers.view` being marked SENSITIVE in `permissions_registry`.

`POST /exports` needs `analytics.export` on top of both, writes an `AuditEvent`
naming the actor, the view, the filters and the row count, and is IP rate
limited — a CSV leaves the building, and the audit row is the only record that
it did.

Filters
-------
Bound from query parameters straight onto `AnalyticsFilters`, which owns the
window rules (half-open, `from < to`, 400-day cap, 14-day cap at hourly
granularity). A violation raises `RequestValidationError` and the app-wide
handler renders 422 with the reason in `details.errors` — the caps are errors
rather than silent truncations because a quietly shortened window leaves a hole
in a chart that nobody can trace back to its cause.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, rate_limit_by_ip, require_permission
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.core.rate_limit import get_client_ip
from app.models.user import User
from app.schemas.analytics_view import AnalyticsViewEnvelope, GatedViewEnvelope
from app.services.analytics import kpis as kpi_catalogue
from app.services.analytics import registry
from app.services.analytics.export import (
    EXPORT_ROW_CAP,
    build_csv_export,
    csv_streaming_response,
    export_scope,
)
from app.services.analytics.filters import AnalyticsFilters
from app.services.analytics.types import GATED_STATES
from app.services.analytics.view_service import (
    BASE_PERMISSION,
    EXPORT_PERMISSION,
    AnalyticsViewService,
)
from app.services.audit_service import AuditService

#: `analytics.view` on the router, not on each route. A route added to this file
#: without an explicit dependency still cannot be reached anonymously or by a
#: staff user with no analytics access at all.
router = APIRouter(dependencies=[Depends(require_permission(BASE_PERMISSION))])

#: Audit action name. Dotted, matching `role.assign` / `coupon.delete`.
EXPORT_AUDIT_ACTION = "analytics.export"

#: Reading is expected to be chatty — a dashboard opens several views and the
#: command menu prefetches navigation. The limit is high enough not to be felt
#: and low enough that a script cannot walk all 73 views in a tight loop.
_READ_RATE_LIMIT = Depends(
    rate_limit_by_ip(scope="analytics.views.read.ip", limit=600, window_sec=300)
)
#: Export is the expensive one: it resolves a view uncached at the export row
#: ceiling. Twenty per five minutes is more than any human needs and far less
#: than a loop.
_EXPORT_RATE_LIMIT = Depends(
    rate_limit_by_ip(scope="analytics.export.ip", limit=20, window_sec=300)
)

#: Server-side row ceiling for an export, applied in place of the request's
#: `limit`. `AnalyticsFilters.limit` is capped at 200 because that is the most a
#: *screen* should ever ask for; an export is the one path that legitimately
#: wants more. The client still cannot influence this number — it is substituted
#: here, and `build_csv_export` enforces the file cap plus the time budget.
#:
#: Substituting it is necessary but NOT sufficient: every resolver clamps the
#: limit it was handed, and clamped it back to 200 whatever this said. What
#: makes the substitution stick is `export_scope()` below, which is the only
#: thing that tells a resolver it is serving an export rather than a screen.
EXPORT_ROW_LIMIT = EXPORT_ROW_CAP


class ExportRequest(BaseModel):
    """What to export. Filters stay in the query string; the target is a body.

    Split that way for a mechanical reason worth stating, because the failure is
    silent: FastAPI flattens an `Annotated[Model, Query()]` into individual
    query parameters ONLY when it is the sole query-parameter source on the
    route. Add a second `Query(...)` parameter and `filters` collapses into one
    opaque `?filters=` param — every real filter is dropped and the request
    422s. A body model does not compete, so the target lives here and the whole
    of `AnalyticsFilters` stays bindable from the query string exactly as it is
    on the GET routes.

    Declared in this module rather than in `app/schemas/` because it is the
    request shape of one route and has no other reader.
    """

    model_config = ConfigDict(extra="forbid")

    #: Registry slugs. Validated against the registry before anything else —
    #: they are matched, never used to name a table, a column or a file path.
    module_slug: str = Field(max_length=64)
    view_slug: str = Field(max_length=64)
    #: Which of the view's declared tables. Defaults to the first.
    table_id: str | None = Field(default=None, max_length=64)


def _service(db: Session, user: User) -> AnalyticsViewService:
    return AnalyticsViewService(db, user)


# ---------------------------------------------------------------------------
# GET /modules
# ---------------------------------------------------------------------------
@router.get("/modules", dependencies=[_READ_RATE_LIMIT])
def list_modules(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """The sidebar: modules and views this user may open, and nothing else.

    A module whose every view is hidden does not appear, and nothing in the
    payload counts or names what was filtered out. "3 more views (locked)" would
    tell a sales user precisely which margin views exist, which is a disclosure
    dressed as a courtesy.
    """
    return _service(db, user).navigation()


# ---------------------------------------------------------------------------
# GET /modules/{module_slug}
# ---------------------------------------------------------------------------
@router.get("/modules/{module_slug}", dependencies=[_READ_RATE_LIMIT])
def get_module(
    module_slug: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """One module with its visible views in full — charts, tables, filter keys.

    404 for an unknown slug, 403 when every view in it is hidden. The 12 module
    slugs already ship to the browser in `registry.contract.json`, so telling
    the two apart discloses nothing and saves an operator a guess.
    """
    return _service(db, user).module_detail(module_slug)


# ---------------------------------------------------------------------------
# GET /modules/{module_slug}/views/{view_slug}
# ---------------------------------------------------------------------------
@router.get(
    "/modules/{module_slug}/views/{view_slug}",
    response_model=AnalyticsViewEnvelope | GatedViewEnvelope,
    dependencies=[_READ_RATE_LIMIT],
)
def get_view(
    module_slug: str,
    view_slug: str,
    filters: Annotated[AnalyticsFilters, Query()],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Resolve one view.

    The slug pair is looked up in the registry first and a miss is a 404, so a
    client-supplied string never reaches a resolver, a repository or a cache
    key. The view's own permission is then checked *before* the cache: a cache
    read that precedes the permission check is not a permission check.

    A gated view returns the small `GatedViewEnvelope` — no `kpis`, no `series`,
    no `tables` — without issuing a single query or cache operation.

    `refresh=true` bypasses the cache read and therefore requires
    `analytics.jobs.run`; without it this is 403, not a silent downgrade.
    """
    return _service(db, user).resolve_view(module_slug, view_slug, filters)


# ---------------------------------------------------------------------------
# GET /kpis
# ---------------------------------------------------------------------------
@router.get("/kpis", dependencies=[_READ_RATE_LIMIT])
def list_kpis(
    kpi_id: str | None = Query(
        default=None, max_length=64, description="Return only this KPI."
    ),
) -> dict[str, Any]:
    """The KPI catalogue: definition, formula, caveats and the tooltip text.

    Not permission-filtered beyond `analytics.view`, and deliberately so: these
    are *definitions*, not measurements. They contain no store data, they name
    no view, and the identical metadata is already dumped into
    `frontend/src/features/analytics/registry.contract.json` and shipped to
    every browser. Filtering them per tier would cost a request per tooltip and
    protect nothing.
    """
    if kpi_id is not None:
        found = kpi_catalogue.by_id(kpi_id)
        if found is None:
            raise NotFoundError(f"Unknown KPI {kpi_id!r}")
        return {"kpis": [kpi_catalogue.kpi_to_dict(found)], "count": 1}
    items = [kpi_catalogue.kpi_to_dict(k) for k in kpi_catalogue.KPIS]
    return {"kpis": items, "count": len(items)}


# ---------------------------------------------------------------------------
# POST /exports
# ---------------------------------------------------------------------------
@router.post(
    "/exports",
    status_code=status.HTTP_200_OK,
    dependencies=[
        Depends(require_permission(EXPORT_PERMISSION)),
        _EXPORT_RATE_LIMIT,
    ],
    # The route returns a `StreamingResponse`, which FastAPI passes through
    # untouched. `response_model=None` stops it trying to build a JSON schema
    # for a body that is text/csv.
    response_model=None,
)
def create_export(
    request: Request,
    payload: ExportRequest,
    filters: Annotated[AnalyticsFilters, Query()],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Export one of a view's tables as CSV.

    Three permissions in sequence: `analytics.view` (router), `analytics.export`
    (this route), and the view's own permission (inside `resolve_view`). The
    third is what stops `analytics.export` becoming a side door into finance —
    export is a *format*, never an escalation.

    **Not cached, either way.** The export resolves the same view under the
    server-side export row limit. Reading the on-screen cache entry would export
    20 rows; writing this payload under the on-screen key would put the whole
    table on the dashboard. So the export path uses neither.

    **Bounded and honest.** Three bounds, and every one of them says so in the
    file: the resolver's own row limit, `CSV_ROW_CAP`, and a ~45s build budget
    (nginx's `proxy_read_timeout` is 60s). Hitting any of them ends the file
    with an explicit final row and sets `X-Analytics-Truncated` on the response
    — a CSV that stops silently looks complete and reconciles against nothing.

    **Audited.** One `AuditEvent` naming the actor, the view, the resolved
    filters, the row count and whether it truncated, committed before the
    response streams.
    """
    module_slug = payload.module_slug
    view_slug = payload.view_slug
    table_id = payload.table_id

    view = registry.get_view(module_slug, view_slug)
    if view is None:
        if registry.get_module(module_slug) is None:
            raise NotFoundError(f"Unknown analytics module {module_slug!r}")
        raise NotFoundError(
            f"Unknown analytics view {view_slug!r} in module {module_slug!r}"
        )

    service = _service(db, user)

    # Authorise BEFORE saying anything about the view's state or exportability.
    # A 409 "this view needs GA4" to a caller who may not open the view at all
    # would confirm the view exists and describe what it does — the same
    # disclosure a 403 exists to prevent. `resolve_view` re-checks this below;
    # the duplication is deliberate, because the ordering is the property.
    if not service.can_see_view(view):
        raise ForbiddenError(f"Missing required permission: {view.permission}")

    if view.state in GATED_STATES:
        # A gated view has nothing to export. An empty CSV would be read as
        # "there were no rows", which is a different and false statement.
        raise ConflictError(
            f"View {view_slug!r} has no data to export: {view.limitation or view.state.value}",
            details={
                "availability": view.state.value,
                "requires": [c.value for c in view.requires],
            },
        )
    if not view.export:
        raise ConflictError(
            f"View {view_slug!r} is not exportable. Its output is a chart or a "
            "set of KPI cards, not a table.",
            details={"view": view_slug},
        )

    # The row limit is raised SERVER-SIDE, never from the request.
    # `model_copy` skips re-validation on purpose: `MAX_LIMIT` bounds what a
    # client may ask for, and this is the server choosing to ask for more.
    export_filters = filters.model_copy(update={"limit": EXPORT_ROW_LIMIT})

    # The scope, not the number, is what raises the ceiling. Every resolver
    # clamps the limit it is handed, and the clamp reads this flag to decide
    # between the screen ceiling and the export one. Held across `resolve_view`
    # only: the flag is a statement about which query to run, and nothing after
    # this line issues one.
    with export_scope():
        envelope = service.resolve_view(
            module_slug, view_slug, export_filters, use_cache=False
        )
    if isinstance(envelope, GatedViewEnvelope):  # pragma: no cover - guarded above
        raise ConflictError(f"View {view_slug!r} has no data to export.")

    spec = _table_spec(view, table_id)
    chosen_id, block = _chosen_table(view, spec, table_id, envelope)

    export = build_csv_export(
        view=view,
        module_slug=module_slug,
        table_id=chosen_id,
        table_spec=spec,
        rows=block.rows,
        resolved=envelope.filters,
        exported_by=user.email,
        timezone_name=envelope.timezone,
        currency=envelope.currency,
        availability=envelope.availability,
        quality=envelope.quality,
        # The resolver's ceiling is lower than the file's, so it is the bound
        # that bites first. Passing its verdict through is what puts the
        # `# TRUNCATED` row and the header on a file that stopped there.
        source_truncated=bool(block.truncated),
        source_row_limit=EXPORT_ROW_LIMIT,
    )

    # Audited before the response is handed back. The row count is real because
    # the file is already built — an audit row written around a lazy generator
    # would have to guess, and a guessed count in an audit log is worse than no
    # count at all.
    AuditService(db).record(
        actor=user,
        actor_ip=get_client_ip(request),
        action=EXPORT_AUDIT_ACTION,
        target_type="analytics_view",
        target_id=view.number,
        target_label=f"{module_slug}/{view_slug}",
        summary=(
            f"Exported {export.row_count} row(s) from {module_slug}/{view_slug}"
            f" [{chosen_id}] as CSV"
        ),
        extra={
            "module": module_slug,
            "view": view_slug,
            "table": chosen_id,
            "row_count": export.row_count,
            "truncated": export.truncated,
            "truncation_reason": export.truncation_reason,
            "sanitised_cells": export.sanitised_cells,
            "filters": envelope.filters.model_dump(mode="json"),
            "filename": export.filename,
        },
    )
    db.commit()

    return csv_streaming_response(export)


def _table_spec(view, table_id: str | None):
    """Resolve the requested table against the view's REGISTRY specs.

    The client sends a table id and it is matched against the server-side spec
    list; it is never used to name anything. An unknown id is a 404 rather than
    a fallback to the first table, because silently exporting a different table
    than the one asked for is how the wrong numbers end up in a board pack.
    """
    specs = view.tables or ()
    if table_id is None:
        return specs[0] if specs else None
    for spec in specs:
        if spec.id == table_id:
            return spec
    raise NotFoundError(
        f"View {view.slug!r} has no table {table_id!r}.",
        details={"available": [s.id for s in specs]},
    )


def _chosen_table(view, spec, table_id: str | None, envelope):
    """Pick the block to export, and 404 rather than export a different one.

    The normal path is exact: the registry spec names an id and the resolver
    filled that id. The fallback covers the one legitimate gap — a view whose
    resolver produces a table it never declared a `TableSpec` for (view 12's
    `cohort_grid` is the live example). There is no id to match on, so with no
    fallback the export 404s on a table that exists and is sitting in the
    envelope.

    Only ever taken when the resolver produced exactly one table and the
    registry declared none — `_table_spec` has already raised for a named id it
    does not know, so a null `spec` here means the caller named nothing. Two
    undeclared tables is ambiguous and stays a 404: guessing which one the
    caller meant is how the wrong numbers reach a board pack. There is no
    guessing about the columns either — `build_csv_export` falls back to the
    row keys when it has no spec.
    """
    if spec is not None:
        chosen_id = spec.id
    elif len(envelope.tables) == 1:
        chosen_id = next(iter(envelope.tables))
    else:
        chosen_id = table_id or ""

    block = envelope.tables.get(chosen_id)
    if block is None:
        raise NotFoundError(
            f"View {view.slug!r} produced no table {chosen_id!r}.",
            details={"available": sorted(envelope.tables)},
        )
    return chosen_id, block
