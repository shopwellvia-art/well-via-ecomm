"""Guards that the granular permissions in the registry actually gate something.

THE BUG THIS EXISTS TO PREVENT
------------------------------
`products.create`, `products.update`, `products.delete`, `categories.create`,
`categories.update`, `categories.delete` and `hero_slides.manage` were all
declared in `permissions_registry.PERMISSIONS`, seeded into the database, listed
in the Roles admin UI, and honoured by the admin sidebar — while the routes they
name were gated on `require_admin`, the legacy superadmin flag.

So the permissions were grantable and inert. An admin could build a "catalogue
manager" role, grant it `products.create`, watch the Products nav appear for that
user, and watch every save return 403. Nothing failed loudly; the permission
simply did nothing.

A registry entry that gates no route is indistinguishable from a working one
until someone tries to use it, so the invariant is asserted here instead.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.services.permissions_registry import PERMISSIONS, all_permission_names

ENDPOINTS_DIR = Path(__file__).resolve().parent.parent / "app" / "api" / "v1" / "endpoints"

#: Permissions that legitimately gate no route today, with the reason. Anything
#: NOT listed here must be enforced somewhere, or the test fails.
UNWIRED_BY_DESIGN = {
    # Nav-only gates. The routes these would map to (GET /products,
    # GET /categories) are anonymous storefront traffic that also backs the
    # admin list — gating either takes the shop offline.
    "products.view": "frontend sidebar only; the listing route is public storefront",
    "categories.view": "frontend sidebar only; the listing route is public storefront",
    # Feature not built yet.
    "analytics.alerts.manage": "alert rules UI not implemented",
}


def _source(name: str) -> str:
    return (ENDPOINTS_DIR / name).read_text()


def _all_endpoint_source() -> str:
    return "\n".join(p.read_text() for p in ENDPOINTS_DIR.glob("*.py"))


APP_DIR = Path(__file__).resolve().parent.parent / "app"
ANALYTICS_DIR = APP_DIR / "services" / "analytics"
ANALYTICS_REGISTRY = ANALYTICS_DIR / "registry.py"

#: Every module that may enforce a permission. Endpoints do it with a
#: `require_permission` dependency; the analytics stack also does it in service
#: code via `user.has_permission(...)` against constants declared in
#: `view_service.py`. Both are enforcement, so both are scanned.
def _enforcement_sources() -> list[Path]:
    return sorted(ENDPOINTS_DIR.glob("*.py")) + sorted(ANALYTICS_DIR.glob("*.py"))


def _string_constants(tree: ast.AST) -> dict[str, str]:
    """Module-level NAME = "literal" bindings, so `require_permission(CONST)`
    resolves the same as `require_permission("literal")`."""
    consts: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        consts[target.id] = node.value.value
    return consts


def _enforced_permissions() -> set[str]:
    """Every permission this codebase actually enforces.

    Parsed rather than grepped, so a name in a comment or docstring does not
    count — catching permissions that only *look* wired up is the entire point.

    Two enforcement mechanisms, both real:

    1. `require_permission(...)` in an endpoint, whether passed a literal or a
       module-level constant (`analytics_views.py` uses BASE_PERMISSION and
       EXPORT_PERMISSION).
    2. The analytics view registry. `analytics_views.resolve_view` raises
       ForbiddenError on `view.permission`, so a permission named in
       `services/analytics/registry.py` is enforced on every view bound to it —
       table-driven, but enforcement all the same.
    """
    found: set[str] = set()
    trees = {p: ast.parse(p.read_text()) for p in _enforcement_sources()}

    # Constants are declared in one module and used in another — BASE_PERMISSION
    # lives in services/analytics/view_service.py and is applied in
    # endpoints/analytics_views.py — so resolve against the union.
    consts: dict[str, str] = {}
    for tree in trees.values():
        consts.update(_string_constants(tree))

    for tree in trees.values():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name not in ("require_permission", "has_permission"):
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.add(arg.value)
                elif isinstance(arg, ast.Name) and arg.id in consts:
                    found.add(consts[arg.id])

    reg = ast.parse(ANALYTICS_REGISTRY.read_text())
    reg_consts = _string_constants(reg)
    for node in ast.walk(reg):
        if isinstance(node, ast.keyword) and node.arg == "permission":
            v = node.value
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                found.add(v.value)
            elif isinstance(v, ast.Name) and v.id in reg_consts:
                found.add(reg_consts[v.id])

    return found


class TestRegistryIsWiredUp:
    def test_every_permission_gates_a_route_or_is_listed_as_unwired(self):
        enforced = _enforced_permissions()
        declared = set(all_permission_names())
        unwired = declared - enforced - set(UNWIRED_BY_DESIGN)
        assert not unwired, (
            "These permissions are declared in the registry and grantable in the "
            "Roles UI, but no route enforces them — granting one does nothing:\n  "
            + "\n  ".join(sorted(unwired))
            + "\n\nEither gate the matching routes with require_permission(...), or "
            "add the name to UNWIRED_BY_DESIGN with the reason."
        )

    def test_unwired_list_has_not_gone_stale(self):
        """If a permission on the exemption list gets wired up, remove it from
        the list — a stale exemption hides the next regression."""
        enforced = _enforced_permissions()
        now_wired = sorted(set(UNWIRED_BY_DESIGN) & enforced)
        assert not now_wired, (
            f"{now_wired} are now enforced; drop them from UNWIRED_BY_DESIGN."
        )

    def test_every_enforced_permission_exists_in_the_registry(self):
        """The reverse failure: a route gating on a permission string that was
        never declared. `has_permission` would return False for every non-admin
        forever, with no way to grant it."""
        declared = set(all_permission_names())
        unknown = _enforced_permissions() - declared
        assert not unknown, (
            "Routes gate on permissions missing from PERMISSIONS, so no role can "
            f"ever hold them: {sorted(unknown)}"
        )


class TestCatalogueRoutesUseGranularPermissions:
    """The specific regression: catalogue writes back on the superadmin flag."""

    @pytest.mark.parametrize(
        "module, expected",
        [
            ("products.py", {"products.create", "products.update", "products.delete"}),
            (
                "categories.py",
                {"categories.create", "categories.update", "categories.delete"},
            ),
            ("hero_slides.py", {"hero_slides.manage"}),
        ],
    )
    def test_module_gates_on_permissions_not_the_superadmin_flag(self, module, expected):
        src = _source(module)
        assert "require_admin" not in src, (
            f"{module} is back on require_admin — scoped staff holding "
            f"{sorted(expected)} would be locked out of the routes those "
            "permissions name."
        )
        for perm in expected:
            assert f'require_permission("{perm}")' in src, f"{module} never uses {perm}"


class TestPublicRoutesStayPublic:
    """The dangerous direction. Gating any of these takes the storefront down —
    they serve anonymous shoppers, and the first two also back the admin list
    screens, which have no separate endpoint."""

    def test_storefront_reads_are_ungated(self):
        for module, route in (
            ("products.py", '@router.get("", response_model=Page[ProductRead])'),
            ("categories.py", '@router.get("", response_model=list[CategoryRead])'),
            ("hero_slides.py", '@router.get("", response_model=list[HeroSlideRead])'),
        ):
            src = _source(module)
            assert route in src, f"{module}: public route signature changed — re-check"
            after = src.split(route, 1)[1].split("def ", 1)[0]
            assert "require_" not in after, (
                f"{module}: the public listing route grew an auth gate. This is the "
                "storefront's catalogue — gating it takes the shop offline."
            )


class TestDestructiveOpsStaySuperadminOnly:
    def test_database_admin_is_not_delegable(self):
        """`POST /admin/database/truncate` with scope=everything TRUNCATEs every
        table. There is no role for which that is a reasonable granular grant, so
        it stays on the superadmin flag by design, not by oversight."""
        src = _source("database.py")
        assert "require_admin" in src
        assert "require_permission" not in src, (
            "database.py moved onto RBAC — that makes irreversible full-data-loss "
            "grantable from the Roles UI. Revert unless this was a deliberate, "
            "reviewed decision."
        )

    def test_no_database_permission_was_invented(self):
        for name in ("database.truncate", "database.seed", "database.manage"):
            assert name not in {p.name for p in PERMISSIONS}, (
                f"{name} was added to the registry. Wiping production must not be "
                "a checkbox in the Roles UI."
            )
