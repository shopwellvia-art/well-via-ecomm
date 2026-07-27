"""Canonical permission list.

The seeder upserts these on app startup so new perms become available without
manual SQL. Adding one here is the single source of truth — referenced from
require_permission("...") and the admin UI.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionDef:
    name: str
    description: str
    group: str


PERMISSIONS: tuple[PermissionDef, ...] = (
    # Dashboard
    PermissionDef("dashboard.view", "View the admin dashboard overview", "Dashboard"),
    # Products
    PermissionDef("products.view", "View products in admin", "Products"),
    PermissionDef("products.create", "Create products", "Products"),
    PermissionDef("products.update", "Update products", "Products"),
    PermissionDef("products.delete", "Delete products", "Products"),
    # Categories
    PermissionDef("categories.view", "View categories", "Categories"),
    PermissionDef("categories.create", "Create categories", "Categories"),
    PermissionDef("categories.update", "Update categories", "Categories"),
    PermissionDef("categories.delete", "Delete categories", "Categories"),
    # Orders
    PermissionDef("orders.view_all", "View all orders (not just own)", "Orders"),
    PermissionDef("orders.update_status", "Update order status", "Orders"),
    PermissionDef("orders.refund", "Issue refunds", "Orders"),
    # Returns
    PermissionDef("returns.view_all", "View all customer returns", "Returns"),
    PermissionDef("returns.manage", "Approve/reject returns + reverse pickups", "Returns"),
    # Users / RBAC
    PermissionDef("users.view", "View users", "Users"),
    PermissionDef("users.update", "Update users (activate/deactivate)", "Users"),
    PermissionDef(
        "users.manage",
        "Edit user accounts (name, active status) and trigger password resets",
        "Users",
    ),
    PermissionDef("users.assign_role", "Assign roles to users", "Users"),
    PermissionDef("roles.view", "View roles & permissions", "RBAC"),
    PermissionDef("roles.create", "Create roles", "RBAC"),
    PermissionDef("roles.update", "Update roles (incl. permissions)", "RBAC"),
    PermissionDef("roles.delete", "Delete roles", "RBAC"),
    # Hero slides
    PermissionDef("hero_slides.manage", "Manage hero slides", "Hero Slides"),
    # Coupons
    PermissionDef("coupons.view", "View coupons", "Coupons"),
    PermissionDef("coupons.create", "Create coupons", "Coupons"),
    PermissionDef("coupons.update", "Update coupons", "Coupons"),
    PermissionDef("coupons.delete", "Delete coupons", "Coupons"),
    # Taxes
    PermissionDef("taxes.view", "View tax rates", "Taxes"),
    PermissionDef("taxes.create", "Create tax rates", "Taxes"),
    PermissionDef("taxes.update", "Update tax rates", "Taxes"),
    PermissionDef("taxes.delete", "Delete tax rates", "Taxes"),
    # Reviews
    PermissionDef("reviews.view", "View all reviews in admin", "Reviews"),
    PermissionDef("reviews.create", "Create reviews on behalf of any author", "Reviews"),
    PermissionDef("reviews.update", "Moderate (edit) reviews", "Reviews"),
    PermissionDef("reviews.delete", "Delete reviews", "Reviews"),
    # Loyalty
    PermissionDef("loyalty.view", "View customer loyalty balances + ledgers", "Loyalty"),
    PermissionDef("loyalty.adjust", "Manually adjust customer points", "Loyalty"),
    PermissionDef("loyalty.configure", "Manage redemption tiers", "Loyalty"),
    PermissionDef("referrals.view", "View referral program activity", "Loyalty"),
    # Audit
    PermissionDef("audit.view", "View the admin action audit log", "Audit"),
    # Observability / APM
    PermissionDef(
        "observability.view",
        "View the backend observability / APM dashboard (latency, errors, slow queries)",
        "Observability",
    ),
    # Payments
    PermissionDef(
        "payments.manage",
        "Configure payment gateways (credentials, enable/disable, environment)",
        "Payments",
    ),
    # Settings
    PermissionDef(
        "settings.manage",
        "Read & edit runtime system settings (SMTP, SMS, security)",
        "Settings",
    ),
    # Frontend / storefront
    PermissionDef(
        "frontend.manage",
        "Manage storefront UI (footer, hero, etc.)",
        "Frontend",
    ),
)


def all_permission_names() -> list[str]:
    return [p.name for p in PERMISSIONS]
