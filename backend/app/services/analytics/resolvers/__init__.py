"""The analytics resolver engine.

Importing this package registers every resolver, so the view endpoint only ever
needs::

    from app.services.analytics.resolvers import RESOLVERS, ResolverContext

`core` and `special` are imported for their side effects — each module calls
`register()` at the bottom. `register()` refuses a duplicate id rather than
overwriting, so two modules claiming `timeseries` fail on boot instead of making
which one runs depend on import order.

`finance` and `levels` are imported for the same reason but register into the
`custom` dispatch table rather than into `RESOLVERS`: `RESOLVERS` is asserted to
be exactly the `ResolverId` enum, and `snapshot` / `cohort_matrix` /
`margin_cascade` / `unit_economics` / `budget_vs_actual` have no enum member
yet. They are reached today with `resolver=ResolverId.CUSTOM` plus
`params={"fn": "snapshot"}` — both server-trusted registry values. Both must be
imported AFTER `core` and `special`, because they build on `core` (metric
bindings) and on `special` (the `custom_function` registry).

The engine's whole job is to turn a registry view definition plus filters into
the data half of the envelope, without ever executing SQL itself and without
ever printing a number it did not measure. Three invariants carry that:

  * a KPI that cannot be computed is `None` with its missing inputs named,
  * a series stops at the source's watermark instead of running flat at zero,
  * a view's quality is the WORST of its components, never an average.
"""
from __future__ import annotations

from app.services.analytics.resolvers import core as _core  # noqa: F401
from app.services.analytics.resolvers import special as _special  # noqa: F401
from app.services.analytics.resolvers import finance as _finance  # noqa: F401
from app.services.analytics.resolvers import levels as _levels  # noqa: F401
# Imported for the registration side effect ONLY. The @custom_function
# decorators run at import, so a module that is never imported silently
# contributes nothing — and the view that names its function 503s at request
# time rather than failing at startup. Keep every resolver module listed here.
from app.services.analytics.resolvers import forecast as _forecast  # noqa: F401
from app.services.analytics.resolvers import control_centre as _control_centre  # noqa: F401
from app.services.analytics.resolvers import basket as _basket  # noqa: F401
from app.services.analytics.resolvers import cx as _cx  # noqa: F401
from app.services.analytics.resolvers import risk as _risk  # noqa: F401
from app.services.analytics.resolvers import marketing as _marketing  # noqa: F401
from app.services.analytics.resolvers import turnover as _turnover  # noqa: F401
from app.services.analytics.resolvers import settlements_view as _settlements_view  # noqa: F401
from app.services.analytics.resolvers.base import (
    NOT_CONFIGURED,
    RESOLVERS,
    Resolver,
    ResolverContext,
    ResolverError,
    ResolverResult,
    SourceState,
    UnknownResolver,
    build_kpi,
    get_resolver,
    missing_kpi,
    not_configured,
    probe_source,
    register,
    source_ref,
    warn,
    watermark_warnings,
)
from app.services.analytics.resolvers.levels import (
    CohortMatrixResolver,
    SnapshotResolver,
)

__all__ = [
    "RESOLVERS",
    "CohortMatrixResolver",
    "SnapshotResolver",
    "Resolver",
    "ResolverContext",
    "ResolverError",
    "ResolverResult",
    "SourceState",
    "UnknownResolver",
    "NOT_CONFIGURED",
    "build_kpi",
    "get_resolver",
    "missing_kpi",
    "not_configured",
    "probe_source",
    "register",
    "source_ref",
    "warn",
    "watermark_warnings",
]
