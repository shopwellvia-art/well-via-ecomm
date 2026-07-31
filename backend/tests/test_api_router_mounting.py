"""Every endpoint module that defines routes must actually be mounted.

Why this file exists
--------------------
`app/api/v1/endpoints/analytics_integrations.py` was written, reviewed, tested
and shipped **without ever being included in `api/v1/router.py`**. Its own
module docstring said "mounted under the `/analytics` prefix by
`api/v1/router.py`", the frontend called `PUT /analytics/integrations`, and the
route 404ed.

Nothing caught it, and the reason is worth recording: every test of that module
called the SERVICE (`integrations.apply_updates`) directly rather than going
through HTTP. Service-level tests are the right place to assert validation and
encryption, but they cannot see the wiring, so a fully-tested module was
completely unreachable.

The damage was not a missing feature. It was a missing feature *combined with a
working guard*: `PATCH /settings` deliberately rejects every `analytics.*`
integration key (so credentials cannot be written through a path that neither
encrypts nor audits them), which meant that with the router unmounted there was
**no route in the application capable of saving a GA4 credential at all**. The
system was closed in both directions and reported neither.

What this asserts
-----------------
1. Every module under `api/v1/endpoints/` that declares an `APIRouter` with at
   least one route contributes at least one path to the mounted application.
2. The specific paths the admin UI depends on resolve.

A module that is deliberately unmounted (feature-flagged off in this
environment, or superseded) belongs in `NOT_MOUNTED` with the reason, so the
choice is visible rather than indistinguishable from this bug.
"""
from __future__ import annotations

import importlib
import pkgutil

from fastapi import APIRouter

from app.api.v1 import endpoints as endpoints_pkg
from app.core.config import settings
from app.main import app

#: Modules that legitimately contribute no route in THIS configuration, with the
#: reason. Anything else missing is the bug this file exists to catch.
#:
#: Keep this EMPTY where possible. A flag-gated module belongs in
#: `FLAG_GATED` instead, so it is still checked whenever its flag is on.
NOT_MOUNTED: dict[str, str] = {}

#: module -> the settings flag that must be true for it to be mounted. When the
#: flag is off the module is expected to be absent; when it is on the module is
#: held to the same standard as everything else. Encoding it this way means the
#: check does not go blind in whichever environment happens to have the flag off
#: — which is how an unmounted router hides.
FLAG_GATED: dict[str, str] = {
    "analytics_views": "ANALYTICS_V2_ENABLED",
    "analytics_cost_admin": "ANALYTICS_V2_ENABLED",
    "analytics_settlements": "ANALYTICS_V2_ENABLED",
}

#: Routes the admin analytics UI calls. Listed explicitly because a typo'd
#: prefix would still satisfy the generic check above while 404ing in the
#: browser, which is the failure this whole file is about.
REQUIRED_PATHS: tuple[str, ...] = (
    "/api/v1/analytics/integrations",
    "/api/v1/analytics/integrations/health",
    "/api/v1/analytics/modules",
    "/api/v1/analytics/admin/health",
)


def _mounted_paths() -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


def _module_endpoints(module_name: str) -> set[object]:
    """The handler FUNCTIONS a module's router declares.

    Identity of the endpoint function, not the path. `include_router` copies
    route objects and applies a prefix, so paths cannot be compared naively — a
    router that declares `path=""` and gets its whole path from the prefix has
    no usable suffix to match on, and `audit`/`site_pages` do exactly that. The
    function object survives the copy unchanged, so it is the one thing that
    reliably identifies "this module's route is in the app".
    """
    module = importlib.import_module(f"{endpoints_pkg.__name__}.{module_name}")
    router = getattr(module, "router", None)
    if not isinstance(router, APIRouter):
        return set()
    return {
        route.endpoint
        for route in router.routes
        if getattr(route, "endpoint", None) is not None
    }


def _mounted_endpoints() -> set[object]:
    return {
        route.endpoint
        for route in app.routes
        if getattr(route, "endpoint", None) is not None
    }


def _endpoint_modules() -> list[str]:
    return [m.name for m in pkgutil.iter_modules(endpoints_pkg.__path__)]


def test_every_endpoint_module_with_routes_is_mounted() -> None:
    """A router nobody includes is dead code that reads as a live feature."""
    mounted = _mounted_endpoints()
    unmounted: list[str] = []

    for name in _endpoint_modules():
        declared = _module_endpoints(name)
        if not declared:
            continue  # no router, or a router with no routes — nothing to mount
        if name in NOT_MOUNTED:
            continue
        flag = FLAG_GATED.get(name)
        if flag is not None and not getattr(settings, flag, False):
            continue  # legitimately absent: its flag is off in this environment
        if not (declared & mounted):
            unmounted.append(name)

    assert not unmounted, (
        "These endpoint modules declare routes that are not reachable in the "
        f"mounted app: {sorted(unmounted)}.\n"
        "Either include the router in app/api/v1/router.py, or add the module "
        "to NOT_MOUNTED in this file with the reason it is deliberately absent.\n"
        "This exact bug shipped once: analytics_integrations.py was written, "
        "tested at the service layer, and never mounted — so GA4 credentials "
        "could not be saved through any route, because PATCH /settings "
        "correctly refuses them."
    )


def test_the_admin_analytics_ui_routes_resolve() -> None:
    """The specific paths the admin UI calls must exist.

    `/analytics/integrations` is mounted unconditionally: it is the only write
    path for tracking credentials, and the documented rollout configures
    tracking BEFORE enabling ANALYTICS_V2_ENABLED.
    """
    mounted = _mounted_paths()
    missing = [
        path
        for path in REQUIRED_PATHS
        if path not in mounted
        and not (
            path.startswith("/api/v1/analytics/modules")
            and not settings.ANALYTICS_V2_ENABLED
        )
    ]
    assert not missing, (
        f"Admin UI routes are not mounted: {missing}. "
        f"ANALYTICS_V2_ENABLED={settings.ANALYTICS_V2_ENABLED}."
    )


def test_integration_credentials_are_writable_somewhere() -> None:
    """The guard and the endpoint must not both be closed.

    `PATCH /settings` refuses `analytics.*` integration keys on purpose. That
    refusal is only safe while a route that DOES accept them exists — otherwise
    the credentials are unsettable and the system reports no error, it simply
    never works. This asserts the pair, not either half.
    """
    from app.services.analytics import integrations

    assert integrations.FIELD_BY_KEY, "no integration fields are defined"
    mounted = _mounted_paths()
    assert "/api/v1/analytics/integrations" in mounted, (
        "PATCH /settings rejects analytics integration keys, so "
        "PUT /analytics/integrations is the only way to set them. With it "
        "unmounted, GA4/GTM/Clarity cannot be configured at all."
    )
