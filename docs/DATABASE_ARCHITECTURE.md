# Database Architecture — ShopWellVia E‑Commerce Platform

> Principal Database Architect review of the FastAPI + SQLAlchemy + MySQL 8.0 schema.
> Source of truth: `backend/app/models/*.py` and `backend/alembic/versions/*.py`.
> Scope: **30 tables** (27 mapped entities + 3 association tables) across **7 business domains**.

**Contents**
1. Executive Summary
2. Database Domain Overview
3. Table‑by‑Table Documentation
4. ER Diagram
5. Architecture Review
6. Recommended Improvements
7. Final Recommended Database Structure

---

## 1. Executive Summary

This is the operational database of a single‑tenant, India‑focused (INR, GST, COD, PhonePe) direct‑to‑consumer e‑commerce platform. It started ~3 weeks ago as 5 tables (`users`, `categories`, `products`, `orders`, `order_items`) and grew to **30 tables across ~35 Alembic migrations**, layering RBAC, tax, coupons, reviews, wishlists, a full loyalty/referral engine, shipping & returns, COD/split‑COD, a pluggable payment gateway, CMS content, system settings, and an audit log.

**What the schema does well**

- A consistent structural idiom: every table inherits `IDMixin` (auto‑increment `INT` PK) and `TimestampMixin` (`created_at`/`updated_at`).
- Every foreign key declares an explicit `ON DELETE` rule (`CASCADE` / `RESTRICT` / `SET NULL`) and the choices are deliberate (e.g. `order_items → products` is `RESTRICT` so financial history can't be deleted).
- Strong domain patterns where it counts: an **append‑only loyalty ledger** with a real idempotency index, **price/cost snapshotting** on `order_items` for historically accurate margins, and a **`user_security` satellite table** that keeps 2FA secrets off the hot `users` row.
- Sensitive 2FA and payment secrets are **Fernet‑encrypted** at the application layer.

**Top risks (read these first)**

| # | Risk | Severity | Where |
|---|------|----------|-------|
| 1 | `users.is_admin` boolean **bypasses the entire RBAC system** (`has_permission()` short‑circuits) | High | `users` |
| 2 | `system_settings` stores **secrets in plaintext** (SMTP/Twilio creds); `is_secret` only masks the API read | High | `system_settings` |
| 3 | `orders` is a **30+ column "god table"** mixing money, payment, shipping, tracking, pickup, returns metadata, and status timestamps | High | `orders` |
| 4 | **No `sessions`/`refresh_tokens` table** → audited "force logout / revoke all sessions" cannot actually revoke anything | High | Identity domain |
| 5 | `products.stock` is a single mutable `INT` with no ledger, no `CHECK (stock >= 0)`, no reservation → **oversell race** under concurrency | High | `products` |
| 6 | **Heavy denormalization maintained only by service code** (`users.points_balance/lifetime_points/vip_tier_id`, `products.rating_*`, `products.image_url`) → silent drift on any direct write | Medium | multiple |
| 7 | **Polymorphic loose refs with no FK integrity**: `points_transactions.(ref_type, ref_id)`, `audit_events.(target_type, target_id)` | Medium | Loyalty, Audit |
| 8 | **No `addresses` table** — delivery address is a `String(512)` free‑text blob on `orders` | Medium | Orders |
| 9 | Unbounded append‑only tables (`audit_events`, `points_transactions`, `coupon_usages`) with **no partitioning/archival** | Medium | scale |
| 10 | **Code bug:** `app/db/base.py` does not import `PaymentGatewayConfig`, `FooterConfig`, `SitePages` → Alembic autogenerate is blind to them | Medium | `app/db/base.py` |

The architecture is coherent and was clearly built incrementally with good instincts; the debt is concentrated in (a) the `orders` table, (b) inconsistent secret handling, (c) the `is_admin` legacy bypass, and (d) a handful of missing tables (addresses, carts, sessions, payment/inventory ledgers, order shipments/status history).

---

## 2. Database Domain Overview

| Domain | Tables | Purpose |
|--------|--------|---------|
| **Identity, Access & Audit** | `users`, `user_security`, `roles`, `permissions`, `role_permissions`*, `user_roles`*, `audit_events` | Accounts, credentials, RBAC, immutable admin audit log |
| **Product Catalog** | `categories`, `products`, `product_images`, `taxes`, `product_taxes`*, `reviews`, `wishlists` | Browsable catalog, pricing, tax, ratings, saved items |
| **Orders, Fulfillment & Returns** | `orders`, `order_items`, `returns`, `return_items` | Purchase lifecycle, shipping/tracking, reverse logistics |
| **Payments** | `payment_gateway_config` | Singleton active‑provider + PhonePe credential config |
| **Promotions** | `coupons`, `coupon_usages` | Discount codes + redemption audit trail |
| **Loyalty & Referrals** | `points_transactions`, `earn_rules`, `redemption_tiers`, `vip_tiers`, `referrals` | Points ledger, earn/redeem rules, VIP tiers, referral program |
| **CMS & Administration** | `hero_slides`, `footer_config`, `site_pages`, `system_settings` | Storefront content + runtime settings |

`*` = pure association (join) table, defined as a SQLAlchemy Core `Table` (no `id`/timestamps).

**Note on cross‑domain coupling:** the `users` row physically carries four Loyalty‑domain columns (`points_balance`, `lifetime_points`, `referral_code`, `vip_tier_id`). They belong conceptually to Loyalty but live on `users` as read‑path caches.

---

## 3. Table‑by‑Table Documentation

### 3.1 Identity, Access Control & Audit

#### Table: `users`
- **Purpose:** Central account record for every human — customer, staff, or admin. Holds credentials, profile, the `is_admin` bypass flag, and denormalized loyalty totals.
- **Primary Key:** `id` (INT, auto‑increment)
- **Foreign Keys:** `vip_tier_id` → `vip_tiers.id` (ON DELETE SET NULL)
- **Important Columns:**
  - `email` `String(255)` — login id; **unique**, indexed, NOT NULL
  - `full_name` `String(255)` nullable; `phone` `String(32)` nullable (SMS notifications)
  - `hashed_password` `String(255)` NOT NULL
  - `is_active` `Boolean` (default `True`) — soft‑disable
  - `is_admin` `Boolean` (default `False`) — **legacy superuser bypass**
  - `points_balance` `Integer` — denorm `SUM(delta)`; can go negative on refund reversals
  - `lifetime_points` `Integer` — monotonic; positive deltas only; drives VIP tier
  - `referral_code` `String(32)` **unique**, indexed, nullable (lazily assigned)
  - `vip_tier_id` `Integer` nullable FK — cached VIP tier
- **Indexes / Constraints:** unique+index on `email`; unique+index on `referral_code`; index on `vip_tier_id`
- **Relationships:** M:N `Role` via `user_roles`; 1:1 `UserSecurity` (shared‑PK satellite, `selectin`, delete‑orphan); has‑many `PointsTransaction`; referrer/referred sides of `Referral`; belongs‑to `VipTier`
- **Notes:** `totp_enabled` is a read‑only `@property` delegating to the satellite (no such column on `users`). `has_permission()`/`permissions` **short‑circuit on `is_admin`** — admins never touch the RBAC graph. The four loyalty columns are deliberate denormalizations and the main cross‑domain coupling point.

#### Table: `user_security`
- **Purpose:** Satellite for per‑user TOTP/2FA secrets + backup codes, split off `users` so account queries never surface secrets. Created lazily on first enrollment, hard‑deleted on disable.
- **Primary Key:** `user_id` (INT) — **shared PK**, not auto‑increment
- **Foreign Keys:** `user_id` → `users.id` (ON DELETE CASCADE)
- **Important Columns:** `totp_secret` `String(255)` nullable (**Fernet‑encrypted** base32 seed); `totp_enabled` `Boolean`; `totp_confirmed_at` `DateTime(tz)` nullable; `backup_codes` `JSON` nullable (≤10 single‑use salted hashes)
- **Indexes / Constraints:** PK only; **no `TimestampMixin`** (no `created_at`/`updated_at`)
- **Relationships:** belongs‑to `User` (1:1)
- **Notes:** No timestamp columns → no record of enrollment/rotation beyond `totp_confirmed_at`. `backup_codes` is a flat JSON list with no per‑code `used_at`/used flag and **no encryption at rest** (unlike `totp_secret`).

#### Table: `permissions`
- **Purpose:** Registry of named capabilities, dotted `<resource>.<action>` (e.g. `orders.refund`).
- **Primary Key:** `id`
- **Foreign Keys:** None
- **Important Columns:** `name` `String(100)` **unique**, indexed; `description` `String(255)`; `group_name` `String(50)` indexed (UI grouping)
- **Relationships:** M:N `Role` via `role_permissions`
- **Notes:** No `is_active`/soft‑delete; `group_name` is free‑text (drift risk).

#### Table: `roles`
- **Purpose:** Named collections of permissions assigned to users; `is_system` protects bootstrap roles.
- **Primary Key:** `id`
- **Foreign Keys:** None
- **Important Columns:** `name` `String(50)` indexed; **unique** `uq_roles_name`; `description`; `is_system` `Boolean`
- **Relationships:** M:N `Permission` via `role_permissions` (`selectin`); M:N `User` via `user_roles`
- **Notes:** `Role.permissions` is `selectin` and `User.roles` is `selectin`, so loading a user always pulls the full role→permission graph (great for auth, expensive for bulk user lists). `is_system` is service‑enforced only — a raw `DELETE` still works.

#### Table: `role_permissions` *(association)*
- **Purpose:** Pure M:N join Role ↔ Permission.
- **Primary Key:** composite `(role_id, permission_id)`
- **Foreign Keys:** `role_id` → `roles.id` (CASCADE); `permission_id` → `permissions.id` (CASCADE)
- **Notes:** Core `Table`, no payload, **no `created_at`** → grant timestamps not stored.

#### Table: `user_roles` *(association)*
- **Purpose:** Pure M:N join User ↔ Role.
- **Primary Key:** composite `(user_id, role_id)`
- **Foreign Keys:** `user_id` → `users.id` (CASCADE); `role_id` → `roles.id` (CASCADE)
- **Notes:** Core `Table`, **no assignment timestamp**.

#### Table: `audit_events`
- **Purpose:** Immutable, append‑only log of sensitive admin actions. Uses denormalized labels + loose string targets so rows survive deletion of the audited object.
- **Primary Key:** `id`
- **Foreign Keys:** `actor_user_id` → `users.id` (ON DELETE SET NULL), nullable
- **Important Columns:** `actor_email` `String(255)` indexed (denorm); `actor_ip` `String(64)`; `action` `String(64)` NOT NULL indexed; `target_type` `String(32)` indexed; `target_id` `Integer` indexed; `target_label` `String(255)` (denorm); `summary` `String(500)` NOT NULL; `extra` `JSON`
- **Indexes / Constraints:** single‑column indexes on `actor_user_id`, `actor_email`, `action`, `target_type`, `target_id`; **no unique** (additive only). *(Note: the cross‑cutting reviewer reports a `(created_at, id)` pagination index added by migration; confirm in the migration vs. model.)*
- **Relationships:** belongs‑to `User` (nullable, `joined`)
- **Notes:** `updated_at` is a semantic dead column on an append‑only table. No composite `(target_type, target_id)` index. No retention/partitioning — unbounded growth. Not tamper‑evident (a DB superuser can edit rows undetected).

---

### 3.2 Product Catalog

#### Table: `categories`
- **Purpose:** Top‑level product grouping (flat, single level).
- **Primary Key:** `id`
- **Foreign Keys:** None
- **Important Columns:** `name` `String(120)` **unique**; `slug` `String(140)` **unique** (routing); `image_url` `String(512)` nullable
- **Relationships:** has‑many `products` (FK on `products.category_id`)
- **Notes:** No `parent_id` (no hierarchy), no product↔category M:N, no soft‑delete.

#### Table: `products`
- **Purpose:** Core sellable SKU — the catalog hub.
- **Primary Key:** `id`
- **Foreign Keys:** `category_id` → `categories.id` (ON DELETE SET NULL), indexed, nullable
- **Important Columns:**
  - `sku` `String(64)` **unique**, indexed; `name` `String(255)` indexed; `description` `Text`
  - `price` `Numeric(12,2)` NOT NULL; `compare_at_price` `Numeric(12,2)` (strikethrough); `cost` `Numeric(12,2)` (margin analytics)
  - `stock` `Integer` (default 0) — single mutable counter
  - `weight_grams` `Integer` (200g fallback); `cod_blocked` `Boolean` (disables COD if in cart)
  - `image_url` `String(512)` — **denorm** primary image
  - `rating_avg` `Numeric(3,2)`, `rating_count` `Integer`, `rating_distribution` `JSON` — **denorm** aggregates recomputed by `ReviewService`
- **Indexes / Constraints:** unique on `sku`; index on `name`, `category_id`
- **Relationships:** belongs‑to `Category`; has‑many `ProductImage` (delete‑orphan, ordered by `position`); M:N `Tax` via `product_taxes`; has‑many `Review`; referenced by `order_items.product_id` (RESTRICT) and `wishlists.product_id` (CASCADE)
- **Notes:** No soft‑delete + `RESTRICT` from `order_items` means ordered products can never be hard‑deleted (and will accumulate). No `CHECK (compare_at_price > price)`. No FULLTEXT index → keyword search falls back to `LIKE '%…%'`. Four denorm columns drift if written outside the service.

#### Table: `product_images`
- **Purpose:** Ordered image gallery; one row may be primary.
- **Primary Key:** `id`
- **Foreign Keys:** `product_id` → `products.id` (ON DELETE CASCADE), indexed
- **Important Columns:** `url` `String(512)` NOT NULL; `position` `Integer`; `is_primary` `Boolean`
- **Notes:** **No partial‑unique on one‑primary‑per‑product** (MySQL 8 limitation) → two rows can be `is_primary=TRUE`, making `products.image_url` ambiguous.

#### Table: `taxes`
- **Purpose:** Named tax rates (GST, cess); multiple summed per product.
- **Primary Key:** `id`
- **Foreign Keys:** None
- **Important Columns:** `name` `String(100)` (**not unique**); `rate` `Numeric(6,3)` (18.000 = 18%); `is_active` `Boolean` indexed
- **Relationships:** M:N `Product` via `product_taxes`
- **Notes:** No unique on `name` → duplicate "GST 18%" rows possible. Deactivating a tax does not detach existing `product_taxes` rows.

#### Table: `product_taxes` *(association)*
- **Purpose:** Pure M:N Product ↔ Tax.
- **Primary Key:** composite `(product_id, tax_id)`
- **Foreign Keys:** `product_id` → `products.id` (CASCADE); `tax_id` → `taxes.id` (CASCADE)
- **Notes:** Core `Table`, no `effective_from/to`, no rate snapshot → no historical tax recalculation.

#### Table: `reviews`
- **Purpose:** Star ratings + written reviews (customer or admin‑seeded).
- **Primary Key:** `id`
- **Foreign Keys:** `product_id` → `products.id` (CASCADE), indexed; `user_id` → `users.id` (SET NULL), indexed, nullable
- **Important Columns:** `rating` `SmallInteger` (CHECK 1–5); `title` `String(160)`; `body` `Text`; `author_name` `String(120)` (fallback when `user_id` NULL); `is_verified_purchase` `Boolean`; `is_approved` `Boolean` (**default TRUE**, indexed); `helpful_count` `Integer`
- **Indexes / Constraints:** **unique** `(product_id, user_id)` `uq_reviews_product_user` (NULL user_ids don't collide → unlimited admin reviews); **CHECK** `rating BETWEEN 1 AND 5`; indexes on `product_id`, `user_id`, `is_approved`
- **Notes:** `is_approved` default TRUE = no pre‑moderation. `helpful_count` has no dedup table (votable repeatedly).

#### Table: `wishlists`
- **Purpose:** Per‑user saved products (one unnamed list).
- **Primary Key:** `id`
- **Foreign Keys:** `user_id` → `users.id` (CASCADE); `product_id` → `products.id` (CASCADE); both indexed, NOT NULL
- **Indexes / Constraints:** **unique** `(user_id, product_id)` `uq_wishlists_user_product`
- **Notes:** No ORM `relationship()` back to user/product → needs explicit joins. No multi‑list / priority support.

---

### 3.3 Orders, Fulfillment & Returns

#### Table: `orders`
- **Purpose:** Central transaction record — money, payment, COD, shipping address, carrier shipment, live tracking, per‑status timestamps, admin notes — all in one wide row.
- **Primary Key:** `id`
- **Foreign Keys:** `user_id` → `users.id` (ON DELETE RESTRICT), indexed
- **Important Columns (grouped):**
  - *Money* (`Numeric(12,2)`, NOT NULL, default 0): `subtotal`, `tax_amount`, `discount_amount`, `shipping_amount`, `total_amount` (= subtotal+tax+shipping−discount), `cod_surcharge_amount`, `cod_balance`, `payment_discount_amount`
  - *Payment/currency:* `currency` `String(3)` default `INR`; `payment_method` `String(32)` default `prepaid` indexed (`prepaid`/`cod`/`split_cod`); `payment_instrument` `String(32)` nullable; `coupon_code` `String(64)`; `payment_intent_id` `String(255)` **unique**
  - *Shipping/carrier:* `shipping_address` `String(512)` free‑text; `shipping_pincode` `String(20)`; `tracking_number`; `carrier`; `shipping_provider`; `shipping_awb` `String(64)` indexed; `shipping_label_url`; `shipment_created_at`
  - *Pickup:* `pickup_id`, `pickup_scheduled_for`
  - *Tracking:* `tracking_events` `JSON` (append‑only `{status,occurred_at,location,note}`, deduped in service); `last_tracking_at`
  - *Status timestamps:* `paid_at`, `shipped_at`, `delivered_at`, `cancelled_at`, `refunded_at`
  - *Admin:* `refund_reason`, `internal_notes` `Text`
  - *Status:* `status` `Enum(OrderStatus)` default `PENDING`, indexed
- **Indexes / Constraints:** unique on `payment_intent_id`; indexes on `user_id`, `status`, `shipping_awb`, `payment_method`
- **Relationships:** belongs‑to `User` (`joined`); has‑many `OrderItem` (delete‑orphan); has‑many `ReturnRequest`
- **Notes:** `total_amount` is derived but **not** enforced by a CHECK. `currency` is effectively decorative (no FX table, no per‑order currency invariant). No human‑readable order number separate from `id`. The five `*_at` columns are a poor‑man's status history (overwritten on re‑transition).

#### Table: `order_items`
- **Purpose:** One row per product line; freezes `unit_price`/`unit_cost` at sale.
- **Primary Key:** `id`
- **Foreign Keys:** `order_id` → `orders.id` (CASCADE), indexed; `product_id` → `products.id` (**RESTRICT**, *not indexed*)
- **Important Columns:** `quantity` `Integer` NOT NULL; `unit_price` `Numeric(12,2)` NOT NULL; `unit_cost` `Numeric(12,2)` nullable (NULL for pre‑cost‑tracking orders)
- **Notes:** **`product_id` lacks an index** → "which orders contain product X" is a full scan. No `product_name`/`sku` snapshot → renamed products lose historical labels.

#### Table: `returns` *(model `ReturnRequest`)*
- **Purpose:** Customer‑initiated return against an order, request → reverse‑logistics → refund.
- **Primary Key:** `id`
- **Foreign Keys:** `order_id` → `orders.id` (RESTRICT), indexed; `user_id` → `users.id` (RESTRICT), indexed
- **Important Columns:** `status` `Enum(ReturnStatus)` default `REQUESTED` indexed; `reason` **`String(64)`** (holds a `ReturnReason` value but typed as string); `customer_notes`/`admin_notes` `Text`; `reverse_awb` `String(64)` indexed; `reverse_pickup_id`; `refund_amount` `Numeric(12,2)` nullable; `requested_at` NOT NULL + `approved_at`/`rejected_at`/`picked_up_at`/`received_at`/`refunded_at`/`cancelled_at`
- **Relationships:** belongs‑to `Order` (`joined`), `User` (`joined`); has‑many `ReturnItem` (delete‑orphan, `selectin`)
- **Notes:** `reason` should be `Enum(ReturnReason)`. No unique on `(order_id, status)` → multiple open returns per order not DB‑blocked.

#### Table: `return_items`
- **Purpose:** Slice of an `OrderItem` being returned; supports partial returns.
- **Primary Key:** `id`
- **Foreign Keys:** `return_id` → `returns.id` (CASCADE), indexed; `order_item_id` → `order_items.id` (**RESTRICT**, *not indexed*)
- **Important Columns:** `quantity` `Integer` NOT NULL
- **Notes:** Over‑return guard (`SUM(quantity) ≤ order_item.quantity`) is **service‑layer only** — no CHECK/trigger.

#### Table: `payment_gateway_config`
- **Purpose:** Singleton active‑provider + PhonePe credential config, read per checkout (hot‑swap without restart).
- **Primary Key:** `id`
- **Foreign Keys:** None
- **Important Columns:** `provider` `String(16)` default `mock` (`mock`/`phonepe`); `phonepe_merchant_id` `String(64)`; `phonepe_salt_key_encrypted` `String(500)` (**Fernet ciphertext**, `""`=unset); `phonepe_salt_index` `Integer`; `phonepe_environment` `String(16)` default `sandbox`
- **Notes:** **No singleton constraint** (`CHECK (id=1)`) → a second row could shadow config. Columns are PhonePe‑specific; a 2nd provider needs schema change. *(Reminder: this model is missing from `app/db/base.py`.)*

**`OrderStatus` enum:** `pending → paid → shipped → delivered`, plus `cancelled` / `refunded`. Transitions are **service‑enforced** (the model defines values only; status timestamps record each hop).

**`ReturnStatus` state machine (from the module docstring):**
```
requested ─approve→ approved ─pickup→ picked_up → received → refunded (terminal)
   │                    │
   ├─reject→ rejected (terminal)
   └─cancel→ cancelled (terminal, customer-initiated)
```

---

### 3.4 Promotions, Loyalty & Referrals

#### Table: `coupons`
- **Purpose:** Master discount‑code record (admin promos and loyalty‑minted rewards).
- **Primary Key:** `id`
- **Foreign Keys:** None (referenced by `coupon_usages.coupon_id`)
- **Important Columns:** `code` `String(64)` **unique**, indexed; `discount_type` `Enum(DiscountType)` (`fixed`/`percent`); `discount_value` `Numeric(12,2)`; `min_order_amount`; `max_discount`; `starts_at`; `expires_at` indexed; `usage_limit` / `usage_count` (default 0); `per_user_limit`; `is_active` indexed; `is_loyalty_reward` indexed
- **Relationships:** has‑many `CouponUsage` (delete‑orphan)
- **Notes:** `usage_count ≤ usage_limit` is service‑enforced (no DB CHECK). No FK back to the minting `RedemptionTier`/user.

#### Table: `coupon_usages`
- **Purpose:** Redemption audit trail (who used which coupon on which order).
- **Primary Key:** `id`
- **Foreign Keys:** `coupon_id` → `coupons.id` (CASCADE); `user_id` → `users.id` (CASCADE); `order_id` → `orders.id` (SET NULL, nullable); all indexed
- **Important Columns:** `discount_amount` `Numeric(12,2)` (actual applied)
- **Notes:** **Two‑phase write** — inserted at checkout with `order_id=NULL`, set on PAID. A crash in between leaves an orphaned usage that still counts against limits.

#### Table: `points_transactions`
- **Purpose:** Immutable append‑only points ledger. Balance = `SUM(delta)`; cached on `users.points_balance`.
- **Primary Key:** `id`
- **Foreign Keys:** `user_id` → `users.id` (CASCADE)
- **Important Columns:** `delta` `Integer` (±); `reason` `Enum(PointsReason)` indexed (`signup_bonus`/`place_order`/`write_review`/`redeem`/`refund_reversal`/`expiry`/`admin_adjust`); `ref_type` `String(32)` + `ref_id` `Integer` (**loose polymorphic ref**, no FK); `description`; `expires_at` indexed (set on positive earns)
- **Indexes / Constraints:** **unique** `ux_points_tx_idempotency (reason, ref_type, ref_id)` (NULLs don't collide → admin adjusts allowed repeatedly); composite `ix_points_tx_user_created (user_id, created_at)`; indexes on `user_id`, `reason`, `expires_at`
- **Notes:** Idempotency relies on **MySQL NULL‑distinct** semantics — would break on a naive PostgreSQL port. `users.points_balance/lifetime_points` are caches that drift if bypassed.

#### Table: `redemption_tiers`
- **Purpose:** Admin‑configurable points→coupon trades shown on `/rewards`.
- **Primary Key:** `id`
- **Foreign Keys:** None
- **Important Columns:** `name`; `cost_points` `Integer` indexed; `discount_type` **`String(16)`** (`percent`/`fixed`, *not* the `DiscountType` enum); `discount_value`; `max_discount`; `expires_after_days` `Integer` (default 30); `is_active` indexed
- **Notes:** Type inconsistency with `Coupon.discount_type` (enum vs raw string). Mints `Coupon` rows but no FK records the linkage.

#### Table: `earn_rules`
- **Purpose:** Admin‑tunable points value per earn event key.
- **Primary Key:** `id`
- **Foreign Keys:** None
- **Important Columns:** `key` `String(64)` **unique**, indexed (matches a `PointsReason` value); `display_name`; `description`; `points_value` `Integer` (meaning varies by key); `is_active` indexed
- **Notes:** `key` ↔ `PointsReason` correspondence is by convention, not constraint.

#### Table: `vip_tiers`
- **Purpose:** VIP ladder; current tier = highest threshold ≤ `users.lifetime_points`.
- **Primary Key:** `id`
- **Foreign Keys:** referenced by `users.vip_tier_id` (SET NULL)
- **Important Columns:** `name` `String(50)` **unique**; `threshold_lifetime_points` `Integer` indexed; `earn_multiplier` `Numeric(4,2)` (1.00 = none); `benefits`; `color`; `sort_order`
- **Notes:** Tier recompute is **earn‑only** → no automatic downgrade if `lifetime_points` is reduced. Deleting an in‑use tier silently NULLs users.

#### Table: `referrals`
- **Purpose:** One row per (referrer → referred) pairing; `pending → completed` on referred user's first PAID order.
- **Primary Key:** `id`
- **Foreign Keys:** `referrer_user_id` → `users.id` (CASCADE), indexed; `referred_user_id` → `users.id` (CASCADE), indexed; `completed_order_id` → `orders.id` (SET NULL, *not indexed*)
- **Important Columns:** `code` `String(32)` indexed (snapshot); `status` `Enum(ReferralStatus)` indexed; `completed_at`
- **Indexes / Constraints:** **unique** `uq_referrals_referred_user (referred_user_id)` (a user can be referred only once)
- **Notes:** Reward reversal on cancelled order is a documented v3 TODO and unimplemented. `completed_order_id` lacks an index.

---

### 3.5 CMS & Administration

#### Table: `hero_slides`
- **Purpose:** Homepage carousel slides (image, copy, dual CTAs, countdown, perks).
- **Primary Key:** `id`
- **Foreign Keys:** None
- **Important Columns:** `image_url` `String(512)` NOT NULL; `alt`; `sort_order` `Integer` indexed; `is_active` `Boolean` indexed; `kind` `String(16)` (`photo`…); `eyebrow`/`heading`/`subtext`/`badge_text`; `cta_label`/`cta_href` + `cta2_label`/`cta2_href`; `countdown_end` `DateTime(tz)` + `countdown_label`; `perks` `JSON` (NULL→default trio, `[]`→hidden); `text_theme` `String(16)` (`light`/`dark`)
- **Indexes / Constraints:** composite `ix_hero_slides_is_active_sort_order (is_active, sort_order)` (the storefront query), plus single‑column indexes on each
- **Notes:** Uses correct dual `server_default`+`default` pattern on `kind`/`text_theme`. Heavily presentation‑oriented columns → UI changes require migrations.

#### Table: `footer_config`
- **Purpose:** Single‑row JSON document holding the whole footer.
- **Primary Key:** `id`
- **Foreign Keys:** None
- **Important Columns:** `data` `JSON` NOT NULL (ORM default `dict`, **`server_default='{}'`**)
- **Notes:** No singleton guard; defaults merged in app layer when row absent.

#### Table: `site_pages`
- **Purpose:** Single‑row JSON document holding all company pages (About/Contact/Careers/…).
- **Primary Key:** `id`
- **Foreign Keys:** None
- **Important Columns:** `data` `JSON` NOT NULL (ORM default `dict`, **no `server_default`** — MySQL 8 rejects it on JSON)
- **Notes:** **Inconsistent with `footer_config`**, which does declare `server_default='{}'` (likely silently ignored/failing on the same MySQL 8 target).

#### Table: `system_settings`
- **Purpose:** Runtime key/value config (SMTP, Twilio, TOTP toggle, flags).
- **Primary Key:** `id`
- **Foreign Keys:** None
- **Important Columns:** `key` `String(120)` **unique**, indexed (dotted); `value` `Text` nullable; `category` `String(40)` indexed; `description`; `is_secret` `Boolean`
- **Notes:** `is_secret` **only masks API reads — value is plaintext on disk.** Inconsistent with the Fernet encryption used for `user_security.totp_secret` and `payment_gateway_config`. `payment_gateway_config` was introduced specifically to migrate PhonePe creds out of this table.

---

## 4. ER Diagram

```
IDENTITY, ACCESS & AUDIT
========================
users (hub)
 ├─ 1:1 ──────────── user_security            (user_id = PK = FK, CASCADE)
 ├─ M:N ─ user_roles ─ roles ─ role_permissions ─ permissions
 ├─ 1:N ──────────── audit_events             (actor_user_id, SET NULL)
 └─ N:1 ──────────── vip_tiers                (vip_tier_id, SET NULL)   [Loyalty]

PRODUCT CATALOG
===============
categories
 └─ 1:N ─ products (hub)
            ├─ 1:N ─ product_images           (CASCADE, order_by position)
            ├─ M:N ─ product_taxes ─ taxes
            ├─ 1:N ─ reviews                   (user_id → users, SET NULL)
            └─ 1:N ─ wishlists                 (user_id → users, CASCADE)
products ◄── order_items.product_id            (RESTRICT)   [Orders]

ORDERS, FULFILLMENT & RETURNS
=============================
users
 └─ 1:N ─ orders (hub)                         (user_id, RESTRICT)
            ├─ 1:N ─ order_items               (CASCADE)
            │           └─ 1:N ─ return_items  (order_item_id, RESTRICT)
            └─ 1:N ─ returns                   (order_id RESTRICT; user_id RESTRICT)
                        └─ 1:N ─ return_items  (return_id, CASCADE)

payment_gateway_config        (standalone singleton, no FKs)

PROMOTIONS, LOYALTY & REFERRALS
===============================
coupons
 └─ 1:N ─ coupon_usages        (coupon_id CASCADE; user_id CASCADE; order_id SET NULL)
users
 ├─ 1:N ─ points_transactions  (CASCADE; loose ref_type/ref_id → orders|reviews|coupons|users)
 ├─ referrals (referrer_user_id, CASCADE)
 └─ referrals (referred_user_id, CASCADE, UNIQUE) ──► orders.completed_order_id (SET NULL)
earn_rules · redemption_tiers · vip_tiers      (config; redemption_tiers mint coupons)

CMS & ADMINISTRATION  (all standalone, no FKs)
====================
hero_slides · footer_config · site_pages · system_settings
```

**Relationship legend:** 1:1 / 1:N / M:N. `CASCADE` deletes children; `RESTRICT` blocks parent deletion while children exist; `SET NULL` orphans the reference.

---

## 5. Architecture Review

### 5.1 Schema Evolution (how it got here)
Born as 5 tables (init `5bfb07ce98a5`), the schema grew in readable waves: catalog & storefront → RBAC + tax + coupons + wishlists → reviews + loyalty + VIP + referrals → audit + settings + TOTP + phone → a long shipping/returns/COD buildout (~15 columns bolted onto `orders` across ~10 narrow `ADD COLUMN` migrations) → maturation (PhonePe creds extracted into `payment_gateway_config`, CMS tables, cost tracking, and finally the TOTP→`user_security` satellite split). The sharpest debts (no address table, fat `orders`, plaintext settings secrets, `is_admin` bypass) all trace back to early init/first‑wave decisions.

### 5.2 Strengths
- Uniform `IDMixin`/`TimestampMixin` across all 30 tables.
- Explicit, sensible `ON DELETE` on every FK (`RESTRICT` protects financial history).
- Append‑only loyalty ledger with a real idempotency index; clean delta semantics.
- Price/cost snapshotting on `order_items` (historically accurate margins).
- `user_security` satellite keeps secrets off the hot `users` row (shared‑PK point lookup).
- Fernet encryption for TOTP secrets and the PhonePe salt key.
- Good hot‑path indexing (`email`, `sku`, `code`, `referral_code`, `(user_id, created_at)`, `shipping_awb`/`reverse_awb`).
- Proper typed enums for `OrderStatus`, `ReturnStatus`, `ReturnReason`, `DiscountType`, `PointsReason`, `ReferralStatus`.
- Engine hygiene: `pool_pre_ping=True`, `NullPool` in Alembic `env.py`, `expire_on_commit=False`.

### 5.3 Weaknesses
- `orders` is a 30+ column god table (money + payment + shipping + tracking + pickup + returns metadata + status timestamps).
- `is_admin` bypasses RBAC entirely; RBAC tables exist but are operationally skipped for admins.
- `system_settings` secrets are plaintext (contradicts the Fernet approach elsewhere).
- String‑typed values where enums exist: `orders.payment_method`, `returns.reason`, `redemption_tiers.discount_type`.
- Polymorphic refs with no integrity: `points_transactions.(ref_type, ref_id)`, `audit_events.(target_type, target_id)`.
- Denormalization drift risk (loyalty caches on `users`; rating aggregates + `image_url` on `products`).
- No soft‑delete anywhere; no singleton guard on single‑row config tables.
- `backup_codes` unencrypted at rest.
- **Code bug:** `payment_gateway_config`, `footer_config`, `site_pages` are not imported in `app/db/base.py` (Alembic autogenerate blind spot).

### 5.4 Scalability Concerns
- Unbounded append‑only tables (`audit_events`, `points_transactions`, `coupon_usages`) with no partition/archival; `INT` PKs risk exhaustion at high cardinality (→ `BIGINT`).
- `orders.tracking_events` JSON grows in place on the hot row; every webhook rewrites the whole blob under a row lock.
- `products.stock` single mutable int → oversell race; no `CHECK (stock >= 0)`, no reservation/ledger.
- Denormalized counters (`points_balance`, `usage_count`, `rating_count`) are write‑hotspots / lost‑update candidates.
- No cache layer; `selectin` eager loads on `users.roles`, `Role.permissions`, `users.vip_tier`, `products.taxes` hit MySQL on every relevant fetch.

### 5.5 Performance Concerns
- Missing composite indexes for common admin filters: `orders(status, created_at)`, `audit_events(action, created_at)` / `(target_type, target_id)`.
- `order_items.product_id` and `return_items.order_item_id` FKs are **unindexed** → full scans for product‑centric and partial‑return lookups; `referrals.completed_order_id` unindexed.
- `Order.items` has no explicit loader → default `select` → N+1 when listing orders unless caller adds `selectinload`.
- `products.rating_*` recomputed via full aggregate scan on every review mutation.
- Wide‑row I/O: list views over `orders` pull `internal_notes`/`tracking_events` even when unneeded.
- Auth critical path: each authenticated request loads `users` → roles → permissions (3 queries minimum).

### 5.6 Security Concerns
- Plaintext SMTP/Twilio secrets in `system_settings`.
- `is_admin` god‑flag: unconditional all‑permissions, no granularity, no elevation audit.
- **No `sessions`/`refresh_tokens` table** → "force logout / revoke all" is unenforceable; JWTs valid until expiry.
- `audit_events` append‑only by convention but not tamper‑evident (no hash chain / WORM).
- PII (`email`, `phone`, `full_name`, `orders.shipping_address`, `audit_events.actor_ip`) unencrypted; no retention/masking.
- `backup_codes` hashes stored without at‑rest encryption.
- No rate‑limit/lockout table for failed TOTP/password/coupon attempts.

---

## 6. Recommended Improvements

### 6.1 Tables to ADD
| Table | Why |
|-------|-----|
| `addresses` | Normalize `orders.shipping_address`; enable saved/reusable addresses, validation, zone analytics |
| `carts`, `cart_items` | Server‑side cart → abandoned‑cart recovery, cross‑device merge |
| `order_status_history` | Replace 5 overwrite‑prone `*_at` columns with a full `(from,to,actor,at)` audit trail |
| `order_shipments` (+ `shipment_tracking_events`) | Move carrier/AWB/pickup/tracking off `orders`; support multi‑parcel/re‑ship |
| `payment_transactions` (+ `refunds`) | Persist gateway auth/capture/refund/chargeback events (only `payment_intent_id` exists today) |
| `inventory_movements` | Stock ledger (orders, returns, manual, imports) → audit + reservations |
| `sessions` / `refresh_tokens` | Real revocation backing the audited force‑logout action |
| `product_variants` (+ option model) | Size/color without duplicate product rows |
| `notifications_log` | Record SMS/email sends; dedup retries |
| `review_helpful_votes` | Dedup `helpful_count` per user |
| `coupon_products` / `coupon_categories` | DB‑enforceable coupon scoping |
| `permission_groups` *(optional)* | Replace free‑text `permissions.group_name` |

### 6.2 Tables to MODIFY
- **`orders`:** add `order_number` (human ref); add `CHECK (total_amount = subtotal + tax_amount + shipping_amount - discount_amount)`; add composite index `(status, created_at)`; enforce single currency per order (or drop the column until multi‑currency is real).
- **`order_items`:** add index on `product_id`; add `product_name_snapshot` / `sku_snapshot`.
- **`products`:** add `is_active`/`deleted_at` soft‑delete; `CHECK (stock >= 0)`; `CHECK (compare_at_price IS NULL OR compare_at_price > price)`; FULLTEXT index on `(name, description)`.
- **`system_settings`:** Fernet‑encrypt `value` for `is_secret=True` rows (or move secrets into typed encrypted tables like `payment_gateway_config`).
- **`user_security`:** add `TimestampMixin`; encrypt `backup_codes`; consider a per‑code `(hash, used_at)` child table.
- **Type tightening:** `returns.reason` → `Enum(ReturnReason)`; `redemption_tiers.discount_type` → `Enum(DiscountType)`; `orders.payment_method`/`payment_instrument` → enums.
- **Single‑row config tables** (`footer_config`, `site_pages`, `payment_gateway_config`): add `CHECK (id = 1)` (or a one‑row enforcement) and align `server_default` handling.
- **Indexes:** add `referrals(completed_order_id)`, `return_items(order_item_id)`, `audit_events(target_type, target_id)`.
- **Code:** import the 3 missing models in `app/db/base.py`.

### 6.3 Tables to SPLIT
- **`orders` → `orders` (core) + `order_payments` + `order_shipments` + `order_status_history` (+ `order_tracking_events`).** This is the single highest‑value refactor.
- **`payment_gateway_config` → generic `payment_providers` + per‑provider credential rows** (encrypted), so adding Razorpay etc. is data, not DDL.

### 6.4 Tables to REMOVE / CONSOLIDATE
- **`is_admin` column** → replace with a seeded `superadmin` system role so every access goes through RBAC (and is auditable).
- **`footer_config` + `site_pages`** → consolidate into one `cms_documents(key UNIQUE, data JSON, version)` table (singleton‑per‑key) to end the `server_default` inconsistency and add versioning.
- **`audit_events.updated_at`** → drop (semantically dead on an append‑only table).

---

## 7. Final Recommended Database Structure

Target‑state, grouped by domain. **Bold** = new table; *(mod)* = modified; ~~strike~~ = removed/folded.

```
IDENTITY & ACCESS
  users (mod: drop is_admin, encrypt PII later)
  user_security (mod: timestamps, encrypt backup_codes)
  roles · permissions · role_permissions · user_roles
  **permission_groups** (optional)
  **sessions / refresh_tokens**           ← real revocation
  audit_events (mod: + (target_type,target_id) idx; drop updated_at; hash-chain for tamper-evidence)

CUSTOMER PROFILE
  **addresses**                            ← normalized, reusable

PRODUCT CATALOG
  categories (mod: + parent_id hierarchy)
  products (mod: soft-delete, CHECK stock>=0, FULLTEXT, price checks)
  **product_variants** (+ option/value)
  product_images · taxes · product_taxes (mod: + effective dates)
  reviews · **review_helpful_votes**
  wishlists

ORDERS & FULFILLMENT
  orders (slimmed core: money + status + user + order_number + CHECK total)
  order_items (mod: index product_id, name/sku snapshot)
  **order_payments**                       ← split from orders
  **order_shipments** (+ **shipment_tracking_events**)  ← split from orders
  **order_status_history**                 ← split from orders
  returns (mod: reason→Enum) · return_items (mod: index order_item_id)
  **inventory_movements**                  ← stock ledger

PAYMENTS
  **payment_providers** (+ encrypted credentials)   ← replaces payment_gateway_config singleton
  **payment_transactions** (+ **refunds**)

PROMOTIONS
  coupons · coupon_usages
  **coupon_products / coupon_categories**  ← scoping

LOYALTY & REFERRALS
  points_transactions (consider BIGINT PK + range partition by created_at)
  earn_rules · redemption_tiers (mod: discount_type→Enum) · vip_tiers
  referrals (mod: index completed_order_id; implement reward reversal)

CMS & ADMIN
  hero_slides
  **cms_documents(key, data, version)**    ← folds footer_config + site_pages
  system_settings (mod: encrypt secret values)
  **notifications_log**
```

**Suggested sequencing (lowest risk → highest value):**
1. **Quick wins (non‑breaking):** add missing indexes; fix `app/db/base.py` imports; add `CHECK` constraints; tighten string→enum columns; add `order_number`.
2. **Security:** encrypt `system_settings` secrets + `backup_codes`; add `sessions/refresh_tokens`; retire `is_admin` in favor of a `superadmin` role.
3. **Integrity & growth:** `addresses`, `inventory_movements`, `payment_transactions`, `order_status_history`.
4. **Structural refactor:** split `orders` into core + `order_payments` + `order_shipments`; partition the big ledgers; introduce `product_variants`.

---

*Generated by a 6‑agent specialist review team (5 domain agents + 1 cross‑cutting architecture reviewer), each verified against the live model and migration source.*
