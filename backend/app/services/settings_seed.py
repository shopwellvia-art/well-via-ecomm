"""Canonical defaults for the `system_settings` table + an idempotent seeder.

Historically every settings row was created by an Alembic migration's INSERT.
That works on first migrate, but it breaks two cases:

  1. A full database truncate (superadmin DB-reset) empties `system_settings`
     but leaves `alembic_version` intact, so the migrations never re-run and the
     admin Settings page comes back blank.
  2. A fresh test/dev DB built without replaying the data-seeding migrations.

`seed_settings` is the single source of truth for the shipped defaults. It is
idempotent and safe on every boot (same contract as `seed_rbac`): it only
INSERTs keys that are missing and refreshes the descriptive metadata of rows
that already exist — it NEVER overwrites a stored `value`, so admin-configured
credentials survive restarts untouched.

Keep this list in sync with the seeding migrations. The five legacy
`payments.provider` / `payments.phonepe.*` keys are intentionally absent: they
were migrated out of `system_settings` into the `payment_gateway_config` table
(migration w8r9s0t1u2v3) and no longer belong here.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.system_setting import SystemSetting

logger = logging.getLogger(__name__)


# (key, default_value, category, description, is_secret)
DEFAULT_SETTINGS: list[tuple[str, str, str, str, bool]] = [
    # ---- Email / SMTP (h3c8d9e0f1a2) ----
    ("email.backend", "", "email", "Active backend: console | smtp", False),
    ("smtp.host", "", "email", "SMTP host (e.g. smtp.sendgrid.net)", False),
    ("smtp.port", "587", "email", "SMTP port", False),
    ("smtp.user", "", "email", "SMTP username", False),
    ("smtp.password", "", "email", "SMTP password", True),
    ("smtp.use_tls", "true", "email", "Use STARTTLS", False),
    ("email.from", "", "email", "Default From address", False),

    # ---- SMS (h3c8d9e0f1a2) ----
    ("sms.backend", "console", "sms", "Active backend: console | twilio", False),
    ("twilio.account_sid", "", "sms", "Twilio Account SID", False),
    ("twilio.auth_token", "", "sms", "Twilio Auth Token", True),
    ("twilio.from_number", "", "sms", "Twilio From phone number (E.164)", False),

    # ---- Security (h3c8d9e0f1a2) ----
    ("auth.totp_mode", "optional", "security", "TOTP availability: off | optional", False),

    # ---- Notifications (j5e0f1a2b3c4) ----
    ("notifications.order_paid", "true", "notifications", "Email + SMS on order PAID", False),
    ("notifications.order_shipped", "true", "notifications", "Email + SMS on order SHIPPED", False),
    ("notifications.order_delivered", "true", "notifications", "Email on order DELIVERED", False),
    ("notifications.order_cancelled", "true", "notifications", "Email on order CANCELLED", False),
    ("notifications.order_refunded", "true", "notifications", "Email on order REFUNDED", False),
    ("notifications.return_refunded", "true", "notifications",
     "Email the customer when a return refund is issued", False),
    ("notifications.return_rejected", "true", "notifications",
     "Email the customer when a return is rejected after inspection", False),

    # ---- Shipping (k6f7a8b9c0d1) ----
    ("shipping.provider", "none", "shipping", "Shipping carrier: none, mock, delhivery, or dtdc", False),
    ("shipping.environment", "staging", "shipping", "Carrier environment: staging or production", False),
    ("shipping.delhivery.api_token", "", "shipping",
     "Delhivery API token (Authorization: Token <value>)", True),
    ("shipping.delhivery.client_name", "", "shipping",
     "Delhivery client name (provided by Delhivery on onboarding)", False),
    ("shipping.warehouse.name", "", "shipping",
     "Pickup warehouse name (must match Delhivery registration)", False),
    ("shipping.warehouse.pincode", "", "shipping",
     "Pickup warehouse pincode (used for rate quoting)", False),
    ("shipping.warehouse.address", "", "shipping",
     "Pickup warehouse street address (used on shipping labels)", False),
    ("shipping.dtdc.api_key", "", "shipping", "DTDC api-key for order upload / label / cancel (Shipsy)", True),
    ("shipping.dtdc.customer_code", "", "shipping", "DTDC customer code (sent in softdata body + cancel)", False),
    ("shipping.dtdc.tracking_username", "", "shipping", "DTDC tracking API username (dtdc.com)", False),
    ("shipping.dtdc.tracking_password", "", "shipping", "DTDC tracking API password (dtdc.com)", True),
    ("shipping.dtdc.service_type_id", "B2C PRIORITY", "shipping", "DTDC service type id (e.g. B2C PRIORITY)", False),
    ("shipping.dtdc.load_type", "NON-DOCUMENT", "shipping", "DTDC load type (NON-DOCUMENT or DOCUMENT)", False),
    ("shipping.dtdc.commodity_id", "99", "shipping", "DTDC commodity id", False),
    ("shipping.dtdc.label_code", "SHIP_LABEL_4X6", "shipping", "DTDC label code (SHIP_LABEL_4X6 / SHIP_LABEL_A4 / ...)", False),
    ("shipping.warehouse.city", "", "shipping", "Pickup warehouse city (required by DTDC origin_details)", False),
    ("shipping.warehouse.state", "", "shipping", "Pickup warehouse state (required by DTDC origin_details)", False),
    ("shipping.warehouse.phone", "", "shipping", "Pickup warehouse contact phone (printed on labels)", False),
    ("shipping.auto_create_on_paid", "false", "shipping",
     "Push shipment to carrier automatically when an order goes PAID", False),
    ("shipping.serviceability_cache_minutes", "60", "shipping",
     "Minutes to cache pincode serviceability lookups in Redis", False),
    ("shipping.webhook_secret", "", "shipping",
     "Shared secret expected on inbound tracking webhooks", True),
    # Free shipping threshold (t5o6p7q8r9s0).
    ("shipping.free_threshold", "999", "shipping",
     "Free shipping when cart subtotal ≥ this amount (₹). 0 disables.", False),

    # ---- Returns (p1k2l3m4n5o6) ----
    ("returns.window_days", "7", "returns",
     "Days after delivery a customer may request a return", False),
    ("returns.refund_timeline_days", "7", "returns",
     "Business days quoted to the customer for a refund to reflect in their account", False),

    # ---- Reviews (seeded at boot only — no migration;
    #      consumed by app/services/review_service.py) ----
    ("reviews.auto_approve", "true", "reviews",
     "Publish user reviews immediately; 'false' holds new and edited reviews "
     "in the admin moderation queue until approved", False),

    # ---- COD (q2l3m4n5o6p7) ----
    ("cod.enabled", "true", "cod", "Allow Cash on Delivery as a checkout option", False),
    ("cod.flat_surcharge", "40", "cod", "Flat fee in INR added when the customer picks COD", False),
    ("cod.min_order_total", "199", "cod", "Disallow COD for orders below this subtotal", False),
    ("cod.max_order_total", "5000", "cod", "Disallow COD for orders above this subtotal (RTO risk)", False),
    ("cod.block_first_time_customer", "false", "cod",
     "Disallow COD on a customer's very first order", False),
    ("cod.block_rto_customers", "false", "cod",
     "Disallow COD for customers with prior refunded returns", False),
    # Split COD (r3m4n5o6p7q8).
    ("cod.split_enabled", "false", "cod",
     "Offer Split COD (partial prepaid, balance on delivery)", False),
    ("cod.split_prepaid_amount", "100", "cod",
     "Flat ₹ collected upfront via the gateway for Split COD", False),
    # COD OTP (u6p7q8r9s0t1).
    ("cod.require_otp", "true", "cod",
     "Require SMS OTP verification before a COD order can be placed", False),

    # ---- Payment instruments (s4n5o6p7q8r9) ----
    ("payments.instruments.upi.enabled", "true", "payments", "Show UPI as a checkout option", False),
    ("payments.instruments.upi.discount_percent", "5", "payments",
     "Discount % applied when the customer pays via UPI", False),
    ("payments.instruments.netbanking.enabled", "true", "payments",
     "Show Netbanking as a checkout option", False),
    ("payments.instruments.netbanking.discount_percent", "0", "payments",
     "Discount % applied when the customer pays via Netbanking", False),
    ("payments.instruments.card.enabled", "true", "payments",
     "Show Debit/Credit cards as a checkout option", False),
    ("payments.instruments.card.discount_percent", "0", "payments",
     "Discount % applied when the customer pays via card", False),
    ("payments.instruments.wallet.enabled", "true", "payments",
     "Show Wallets as a checkout option", False),
    ("payments.instruments.wallet.discount_percent", "0", "payments",
     "Discount % applied when the customer pays via wallets", False),
    ("payments.suggested_instrument", "upi", "payments",
     "Which instrument shows the 'Suggested' chip (upi|netbanking|card|wallet)", False),

    # ---- Login trust badges (t5o6p7q8r9s0) ----
    ("login.trust_badge_1_label", "5★ Rating from our customers", "login",
     "Trust badge 1 — label (leave blank to hide)", False),
    ("login.trust_badge_1_icon", "star", "login",
     "Trust badge 1 — icon (star/shield/truck/clock/package/badge)", False),
    ("login.trust_badge_2_label", "Secure payment gateway", "login",
     "Trust badge 2 — label (leave blank to hide)", False),
    ("login.trust_badge_2_icon", "shield", "login", "Trust badge 2 — icon", False),
    ("login.trust_badge_3_label", "Pan-India delivery", "login",
     "Trust badge 3 — label (leave blank to hide)", False),
    ("login.trust_badge_3_icon", "truck", "login", "Trust badge 3 — icon", False),
    ("login.trust_badge_4_label", "On-time delivery", "login",
     "Trust badge 4 — label (leave blank to hide)", False),
    ("login.trust_badge_4_icon", "clock", "login", "Trust badge 4 — icon", False),

    # ---- Costs / contribution-margin (d5y6z7a8b9c0) ----
    ("costs.packing_per_order", "0", "costs",
     "Packing material cost per dispatched order (₹). Added to C1 cost stack.", False),
    ("costs.handling_per_order", "0", "costs",
     "Warehouse handling / labour cost per order (₹). Added to C1 cost stack.", False),
    ("costs.gateway_fee_pct", "0", "costs",
     "Payment gateway fee as a percentage of prepaid order value (e.g. 2 = 2%). COD orders are exempt.", False),
    ("costs.monthly_overheads", "0", "costs",
     "Fixed monthly overhead costs — rent, salaries, SaaS etc. (₹). Pro-rated to the analytics window.", False),
    ("costs.monthly_ad_spend", "0", "costs",
     "Total monthly advertising / marketing spend (₹). Pro-rated to the analytics window for C3.", False),

    # ---- Store identity / GST invoices (seeded at boot only — no migration;
    #      consumed by app/services/invoice_service.py) ----
    ("store.legal_name", "", "store",
     "Registered legal name of the seller, printed on GST tax invoices", False),
    ("store.address", "", "store",
     "Registered address of the seller, printed on GST tax invoices", False),
    ("store.gstin", "", "store",
     "Seller GSTIN (15 characters), printed on GST tax invoices. "
     "Leave blank if not GST-registered.", False),
    ("store.state_code", "", "store",
     "Seller GST state code (e.g. 29 for Karnataka). Drives the CGST/SGST "
     "vs IGST split on invoices; blank renders a single GST line.", False),

    # ---- Reporting configuration (seeded at boot only — no migration) ----
    # Read by the analytics subsystem to bucket rollups on store-local calendar
    # days instead of UTC. Containers run UTC and the only other timezone in the
    # codebase is the hardcoded IST constant in invoice_service.py, so without
    # these an evening-IST order lands in the next UTC day and "yesterday's
    # sales" is wrong by 5.5 hours.
    #
    # store.timezone is NOT freely editable once rollups exist: changing it
    # opens a new row in analytics_tz_generations and requires a full rebuild
    # before the new generation goes active, because buckets built under two
    # different zones must never be mixed. Edit it through the analytics
    # integrations screen, which runs that procedure — not by hand.
    ("store.timezone", "Asia/Kolkata", "store",
     "IANA timezone used to bucket analytics reporting days (e.g. Asia/Kolkata). "
     "Changing this requires a full analytics rollup rebuild.", False),
    ("store.currency", "INR", "store",
     "ISO-4217 reporting currency for analytics and exports (e.g. INR).", False),
    ("store.week_start", "monday", "store",
     "First day of the reporting week for weekly analytics buckets: monday | sunday.", False),

    # ---- Storage (l3m4n5o6p7q8, n5o6p7q8r9s0) ----
    # Credentials/endpoint rows are blank so the env fallback stays in effect.
    # Two rows ship with a concrete default on purpose: s3_root_prefix is
    # seeded "wellvia" so the operator can see the bucket folder the project
    # writes under, and s3_acl is "" (no ACL — correct for modern buckets).
    ("storage.backend", "", "storage",
     "Image storage backend: 'local' or 's3' (blank = use the env default)", False),
    ("storage.s3_region", "", "storage", "AWS region, e.g. ap-south-1", False),
    ("storage.s3_bucket", "", "storage", "S3 bucket name", False),
    ("storage.s3_endpoint_url", "", "storage",
     "Custom endpoint for S3-compatible stores (e.g. DigitalOcean Spaces); leave blank for AWS S3", False),
    ("storage.s3_public_base_url", "", "storage",
     "Public base URL / CDN used to serve objects; blank = the bucket URL", False),
    ("storage.s3_access_key", "", "storage", "AWS access key ID", False),
    ("storage.s3_secret_key", "", "storage", "AWS secret access key", True),
    ("storage.s3_root_prefix", "wellvia", "storage",
     "Top-level bucket folder all uploads are organised under, e.g. "
     "wellvia/products/2026/06/<id>.jpg. Blank = the env default (wellvia).", False),
    ("storage.s3_acl", "", "storage",
     "Object ACL on upload — blank for modern buckets (Object Ownership = "
     "Bucket owner enforced / ACLs disabled); 'public-read' only for legacy "
     "ACL-enabled buckets.", False),

    # ---- Storefront identity / layout (seeded at boot only — no migration;
    #      consumed by app/services/storefront_service.py; blank = the shipped
    #      DEFAULT_STOREFRONT value) ----
    ("storefront.site_title", "", "storefront",
     "Browser tab / document title of the storefront", False),
    ("storefront.brand_name", "", "storefront",
     "Brand wordmark shown in the navbar and footer", False),
    ("storefront.tagline", "", "storefront",
     "Short brand tagline shown next to the wordmark", False),
    ("storefront.logo_url", "", "storefront",
     "Uploaded logo URL rendered in place of the wordmark", False),
    ("storefront.favicon_url", "", "storefront",
     "Uploaded favicon URL", False),
    ("storefront.nav_items", "", "storefront",
     "JSON array of navbar links (label/to/visibility)", False),
    ("storefront.homepage_sections", "", "storefront",
     "JSON array of homepage sections (order/visibility/title)", False),
]


def seed_settings(db: Session) -> int:
    """Ensure every shipped default exists in `system_settings`.

    Idempotent — safe on every app boot and after a DB truncate. Missing keys
    are inserted with their default value; existing keys keep their stored
    value (only the description / category / is_secret metadata is refreshed).
    Returns the number of rows newly inserted.

    Mirrors `seed_rbac`: commits internally.
    """
    existing = {s.key: s for s in db.execute(select(SystemSetting)).scalars().all()}
    inserted = 0
    for key, value, category, description, is_secret in DEFAULT_SETTINGS:
        row = existing.get(key)
        if row is None:
            db.add(
                SystemSetting(
                    key=key,
                    value=value,
                    category=category,
                    description=description,
                    is_secret=is_secret,
                )
            )
            inserted += 1
        else:
            # Keep the admin's stored value; just keep metadata fresh so a
            # shipped re-categorization / wording change lands without a
            # migration. Never touch `value`.
            row.category = category
            row.description = description
            row.is_secret = is_secret
    db.commit()
    if inserted:
        logger.info("settings seed: inserted %d missing default setting(s)", inserted)
    return inserted
