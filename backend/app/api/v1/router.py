from fastapi import APIRouter

# Aliased: `settings` is also the name of the settings ENDPOINT module
# imported below, which would shadow this one.
from app.core.config import settings as app_settings
from app.api.v1.endpoints import (
    addresses,
    analytics,
    analytics_admin,
    analytics_cost_admin,
    analytics_integrations,
    analytics_settlements,
    analytics_views,
    audit,
    auth,
    cart,
    categories,
    cod,
    contact,
    coupons,
    csp_report,
    customers,
    dashboard,
    database,
    email_templates,
    footer,
    hero_slides,
    invoices,
    loyalty,
    observability,
    orders,
    payment_instruments,
    payment_methods,
    payments,
    products,
    returns,
    reviews,
    roles,
    search,
    settings,
    shipping,
    site_pages,
    storefront,
    taxes,
    users,
    wishlist,
)

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(categories.router, prefix="/categories", tags=["categories"])
api_router.include_router(products.router, prefix="/products", tags=["products"])
# Global search: GET /search?q=… — products AND categories in one response, for
# the header's suggestions dropdown. Its own prefix rather than /products/search
# because it is not products-only.
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(hero_slides.router, prefix="/hero-slides", tags=["hero-slides"])
api_router.include_router(cart.router, prefix="/cart", tags=["cart"])
api_router.include_router(orders.router, prefix="/orders", tags=["orders"])
# Derived GST invoices: GET /orders/{id}/invoice (owner) and
# GET /orders/admin/{id}/invoice (staff). Separate module so orders.py stays
# focused on order lifecycle; the paths can't shadow the orders routes above
# (different segment shapes).
api_router.include_router(invoices.router, prefix="/orders", tags=["invoices"])
api_router.include_router(payments.checkout_router, prefix="/checkout", tags=["checkout"])
api_router.include_router(payments.payments_router, prefix="/payments", tags=["payments"])
api_router.include_router(payment_instruments.router, prefix="/payments", tags=["payments"])
# Admin payment-method config: GET/PUT /admin/payment-methods[/{code}]
api_router.include_router(
    payment_methods.admin_router,
    prefix="/admin/payment-methods",
    tags=["payment-methods"],
)
# Public active-gateway list: GET /payment-methods/active
api_router.include_router(
    payment_methods.public_router,
    prefix="/payment-methods/active",
    tags=["payment-methods"],
)
api_router.include_router(roles.router, prefix="/roles", tags=["roles"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
# The shopper directory. A sibling of /users rather than a sub-path of it: the
# two are gated by different permission tiers (customers.* vs users.*), and
# nesting would have implied that reaching a customer requires staff-directory
# access.
api_router.include_router(customers.router, prefix="/customers", tags=["customers"])
api_router.include_router(taxes.router, prefix="/taxes", tags=["taxes"])
api_router.include_router(coupons.router, prefix="/coupons", tags=["coupons"])
api_router.include_router(wishlist.router, prefix="/wishlist", tags=["wishlist"])
api_router.include_router(addresses.router, prefix="/addresses", tags=["addresses"])
# Reviews are surfaced under /products/{id}/reviews (public + user-create) and
# under /reviews (user-edit/delete + admin CRUD).
api_router.include_router(reviews.public_router, prefix="/products", tags=["reviews"])
api_router.include_router(reviews.admin_router, prefix="/reviews", tags=["reviews"])
api_router.include_router(loyalty.router, prefix="/loyalty", tags=["loyalty"])
api_router.include_router(audit.router, prefix="/audit-events", tags=["audit"])
api_router.include_router(settings.router, prefix="/settings", tags=["settings"])
api_router.include_router(shipping.router, prefix="/shipping", tags=["shipping"])
api_router.include_router(cod.router, prefix="/cod", tags=["cod"])
# Admin router first so /returns/admin/... matches before the customer
# router's /returns/{return_id} dynamic segment.
api_router.include_router(returns.admin_router, prefix="/returns/admin", tags=["returns"])
api_router.include_router(returns.customer_router, prefix="/returns", tags=["returns"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
# Superadmin-only destructive maintenance: POST /admin/database/truncate
api_router.include_router(database.router, prefix="/admin/database", tags=["database"])
# CSP violation collector. Anonymous and always mounted: the browser posts it
# with no credentials, and it must accept reports the moment the Report-Only
# policy ships — before ANALYTICS_V2_ENABLED, since the point is to collect
# evidence while everything else is still off.
api_router.include_router(csp_report.router, prefix="/analytics", tags=["analytics"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
# Analytics v2 job control. Enqueue-and-report only — the heavy aggregation runs
# in the analytics-worker container, never in a request worker. Mounted under the
# same /analytics prefix but on /admin/* paths, which cannot shadow the two legacy
# static routes above (/analytics/sales, /analytics/profit).
api_router.include_router(
    analytics_admin.router, prefix="/analytics", tags=["analytics-admin"]
)
# Tracking credentials — GTM, GA4, Clarity, consent: /integrations*.
#
# Mounted UNCONDITIONALLY, outside the ANALYTICS_V2_ENABLED block below, and the
# ordering matters. This is the ONLY write path for these keys: the generic
# PATCH /settings deliberately rejects every `analytics.*` integration key with
# a ForbiddenError, because that path neither encrypts secrets nor validates the
# field nor audits under its own action. So if this router is absent, the keys
# cannot be set through any route at all — which is exactly what happened: the
# module was written with a docstring claiming router.py mounted it, the
# frontend called PUT /analytics/integrations, and the endpoint 404ed. The
# service-layer tests all called `integrations.apply_updates` directly, so
# nothing caught it.
#
# Not inside the flag block, because the rollout order in
# docs/analytics/DEPLOYMENT.md configures tracking BEFORE enabling V2 — gating
# it on V2 would make the documented sequence impossible to follow. The keys are
# inert until ANALYTICS_TRACKING_ENABLED is on regardless, so mounting this
# early changes no behaviour; it only makes the credentials enterable.
api_router.include_router(
    analytics_integrations.router, prefix="/analytics", tags=["analytics-integrations"]
)
# Analytics v2 read surface: navigation, the single view endpoint, the KPI
# catalogue and CSV export. Paths start /modules, /kpis, /exports, so none of
# them can shadow the two legacy static routes registered above.
# Mounted ONLY when the flag is on.
#
# This was mounted unconditionally until a performance audit noticed the flag
# gated nothing. That is not a cosmetic bug: the entire staged rollout in
# docs/analytics/DEPLOYMENT.md rests on "deploy with all three flags off and
# confirm zero behaviour change", and an unconditionally-mounted read surface
# makes that step a no-op that reads as a pass. It also means turning the flag
# off — the documented primary rollback, the one that needs no deploy — would
# not have rolled anything back.
#
# Read at import, so flipping the flag needs a restart. That is deliberate:
# a per-request check would let a half-enabled state exist across four uvicorn
# workers, and the flag's job is to make the subsystem wholly present or
# wholly absent.
if app_settings.ANALYTICS_V2_ENABLED:
    api_router.include_router(
        analytics_views.router, prefix="/analytics", tags=["analytics-views"]
    )
    # Cost-rule and marketing-spend entry: /admin/cost-rules,
    # /admin/marketing-spend. Inside the flag block with the read surface it
    # feeds — entering a rate is only meaningful once the views that consume it
    # are mounted, and the documented rollback (flag off, no deploy) must take
    # the whole subsystem with it. Paths start /admin/, so they cannot shadow
    # the legacy /analytics/sales and /analytics/profit routes above.
    api_router.include_router(
        analytics_cost_admin.router, prefix="/analytics", tags=["analytics-costs"]
    )
    # Settlement report upload + status: /admin/settlements/*. Inside the flag
    # block with the view (64) it feeds, for the same reason as the cost admin:
    # the documented rollback (flag off, no deploy) must take the whole
    # subsystem with it, and uploading a report is only meaningful once the
    # view that reads it is mounted.
    api_router.include_router(
        analytics_settlements.router, prefix="/analytics", tags=["analytics-settlements"]
    )
api_router.include_router(footer.router, prefix="/footer", tags=["footer"])
api_router.include_router(
    storefront.router, prefix="/storefront-config", tags=["storefront-config"]
)
api_router.include_router(observability.router, prefix="/observability", tags=["observability"])
api_router.include_router(site_pages.router, prefix="/site-pages", tags=["site-pages"])
# Public contact-form + newsletter intake: POST /contact, POST /newsletter/subscribe
api_router.include_router(contact.router, tags=["contact"])
api_router.include_router(
    email_templates.router, prefix="/email-templates", tags=["email-templates"]
)
