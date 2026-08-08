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
    # Staff accounts / RBAC.
    # `users.*` is the STAFF directory (/admin/team): accounts that hold admin
    # access. Shoppers are a separate tier — see `customers.*` below. The split
    # exists so an ops user who must look up a customer never gets the ability
    # to grant admin access as a side effect.
    PermissionDef("users.view", "View staff accounts and their roles", "Users"),
    PermissionDef("users.update", "Update users (activate/deactivate)", "Users"),
    PermissionDef(
        "users.manage",
        "Edit staff accounts (name, active status) and trigger password resets",
        "Users",
    ),
    PermissionDef("users.assign_role", "Assign roles to users", "Users"),
    PermissionDef(
        "users.invite",
        "Invite new staff accounts by email",
        "Users",
    ),
    # Customers — the shopper directory (/admin/customers). Deliberately NOT
    # `users.*`: this tier can look up and support a shopper but can never
    # touch a staff account or grant a role.
    PermissionDef(
        "customers.view",
        "View the customer directory and individual customer profiles",
        "Customers",
    ),
    PermissionDef(
        "customers.manage",
        "Edit customer accounts (name, disable/re-enable) and trigger password resets",
        "Customers",
    ),
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
    # Analytics (v2) — one gate per sidebar module, plus action perms.
    # Granting a module perm does NOT imply the others: finance and customers
    # are deliberately separate tiers so an ops user can see operations without
    # seeing margin or a named customer's purchase history.
    PermissionDef(
        "analytics.view",
        "Open the analytics section (base access; each module still needs its own permission)",
        "Analytics",
    ),
    PermissionDef(
        "analytics.executive.view",
        "View executive summary, business health, forecasting and budget-vs-actual views",
        "Analytics",
    ),
    PermissionDef(
        "analytics.sales.view",
        "View sales, revenue, order and discount performance views",
        "Analytics",
    ),
    PermissionDef(
        "analytics.finance.view",
        "SENSITIVE: view margin, COGS, contribution, unit economics, cash flow, tax and "
        "settlement figures",
        "Analytics",
    ),
    PermissionDef(
        "analytics.products.view",
        "View product, category, SKU and merchandising performance views",
        "Analytics",
    ),
    PermissionDef(
        "analytics.customers.view",
        "SENSITIVE: view customer-level analytics including personal drill-down "
        "(lifetime value, segments, cohorts, RFM)",
        "Analytics",
    ),
    PermissionDef(
        "analytics.marketing.view",
        "View marketing channel, campaign, SEO and attribution views",
        "Analytics",
    ),
    PermissionDef(
        "analytics.website.view",
        "View website traffic, funnel, checkout and on-site conversion views",
        "Analytics",
    ),
    PermissionDef(
        "analytics.inventory.view",
        "View inventory, stock availability and supply-chain views",
        "Analytics",
    ),
    PermissionDef(
        "analytics.orders.view",
        "View order fulfilment, shipping, returns and cancellation views",
        "Analytics",
    ),
    PermissionDef(
        "analytics.payments.view",
        "View payment success, failure, COD and fraud-risk views",
        "Analytics",
    ),
    PermissionDef(
        "analytics.marketplace.view",
        "View marketplace, store/branch and B2B account views",
        "Analytics",
    ),
    PermissionDef(
        "analytics.cx.view",
        "View customer experience, support, review and UX views",
        "Analytics",
    ),
    PermissionDef(
        "analytics.control_centre.view",
        "View the analytics control centre (tracking health, reconciliation, experiments, alerts)",
        "Analytics",
    ),
    PermissionDef(
        "analytics.export",
        "Export analytics tables to CSV/XLSX (still limited to views the user can open)",
        "Analytics",
    ),
    PermissionDef(
        "analytics.integrations.manage",
        "Connect and configure analytics integrations (GA4, Search Console, ads, Clarity)",
        "Analytics",
    ),
    PermissionDef(
        "analytics.budgets.manage",
        "Create and edit analytics budgets and targets used by budget-vs-actual",
        "Analytics",
    ),
    PermissionDef(
        "analytics.alerts.manage",
        "Create and edit analytics alert rules, thresholds and anomaly settings",
        "Analytics",
    ),
    PermissionDef(
        "analytics.jobs.run",
        "Manually trigger analytics rollup, backfill and reconciliation jobs",
        "Analytics",
    ),
)


def all_permission_names() -> list[str]:
    return [p.name for p in PERMISSIONS]
