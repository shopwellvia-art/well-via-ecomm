from fastapi import APIRouter

from app.api.v1.endpoints import (
    addresses,
    analytics,
    audit,
    auth,
    cart,
    categories,
    cod,
    contact,
    coupons,
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
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
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
