# Knowledge Transfer — Simple Ecommerce Platform

> Onboarding doc for the `simple ecomers` repo. Last updated 2026-05-29.

## 1. What this is

A **production-style ecommerce platform**: a FastAPI backend + React SPA frontend, backed by
MySQL and Redis, orchestrated with Docker + Nginx. It is a single-store (not multi-vendor)
shop with a rich feature set: catalog, cart, checkout, payments (PhonePe + COD), shipping
(Delhivery), returns/RMA, reviews, coupons, a full **loyalty/rewards/referral** engine, RBAC,
2FA, and an admin console.

```
.
├── backend/          FastAPI service  (api → service → repository → model)
├── frontend/         React 18 SPA     (Vite, feature-sliced)
├── docker/           nginx + mysql init assets
├── demo_DB/          *** reference SQL dump — NOT this app's schema (see §8) ***
├── scripts/          check_db.py (DB connectivity smoke test)
├── xyz/              design/research screenshots (shopflow refs) — not code
├── _t-*.{jpg,png,…}  image-upload test fixtures (small/big/bad-format)
└── docker-compose*.yml
```

## 2. Tech stack

| Layer        | Tech |
|--------------|------|
| Backend      | FastAPI 0.115, SQLAlchemy 2.0, Alembic, Pydantic v2, Uvicorn |
| Auth         | **PASETO v4.local** tokens (not JWT), bcrypt, pyotp (TOTP 2FA) |
| DB           | MySQL 8.4, PyMySQL driver |
| Cache/queue  | Redis 7 (rate limits, sessions, OTP, shipping cache), Celery present |
| Frontend     | React 18.3, Vite 6, React Router 6, TanStack Query 5, Zustand 5 |
| Styling      | Tailwind 3.4 + CSS-variable dark/light theme, Framer Motion, Recharts |
| Integrations | PhonePe (payments), Delhivery (shipping), Twilio (SMS), Google OAuth, S3/DO Spaces (images) |
| Infra        | Docker Compose, Nginx reverse proxy |

## 3. Backend architecture

One-way dependency: **`api → service → repository → model`**.

- `app/api/v1/endpoints/*` — HTTP only; ~24 routers, all under `/api/v1`.
- `app/services/*` — business rules + transactions (~25 services, the heart of the app).
- `app/repositories/*` — only layer that touches the ORM.
- `app/models/*` — SQLAlchemy ORM (18 tables + join tables).
- `app/schemas/*` — Pydantic DTOs.
- `app/core/*` — config, security (PASETO), rate limiting, exceptions, middleware.
- `app/integrations/*` — payment & shipping providers behind abstract base + factory
  (mock/phonepe, mock/delhivery/none) so providers swap without touching endpoints.

Entry point `app/main.py`; config in `app/core/config.py` (Pydantic settings from `.env`).

### Key domains
- **Catalog**: Product (sku, price, stock, weight, rating aggregates), ProductImage, Category, Tax (m2m).
- **Orders**: Order + OrderItem. Status machine PENDING→PAID→SHIPPED→DELIVERED (+CANCELLED/REFUNDED).
  Order carries full money breakdown (subtotal/tax/discount/shipping), payment method
  (prepaid/cod/split_cod), and fulfillment fields (AWB, carrier, tracking_events JSON).
- **Payments**: checkout → gateway redirect → webhook. PhonePe (signed S2S) + mock provider.
  COD with availability checks + optional SMS OTP gate; split-COD (prepay + collect balance).
- **Shipping**: serviceability by pincode (Redis-cached), rate quotes, pickup scheduling, tracking.
- **Returns (RMA)**: REQUESTED→APPROVED→PICKED_UP→RECEIVED→REFUNDED state machine + reverse shipment.
- **Loyalty** (`loyalty_service.py`, the biggest service):
  - `PointsTransaction` = append-only ledger (earn/spend/expiry/refund/adjust), idempotent via
    `(reason, ref_type, ref_id)` unique index.
  - `EarnRule` (configurable points per action), `RedemptionTier` (points→coupon),
    `VipTier` (lifetime-points thresholds → earn multipliers).
  - Points expiry runs via `GET /loyalty/admin/expire-points` (cron-able; see `docs/loyalty-cron.md`).
- **Referrals**: signup with code → friend welcome coupon; referrer earns on friend's first PAID order.
- **RBAC**: fine-grained dotted permissions (`products.create`, `orders.refund`) grouped into Roles.
  `User.is_admin` legacy boolean bypasses checks. Roles seeded idempotently on startup (`rbac_seed.py`).
- **Audit**: append-only `AuditEvent` log of admin actions.

### Auth specifics
- **PASETO v4.local** (symmetric, encrypted) — key derived from `SECRET_KEY`. Replaces JWT.
- Access token (~30 min) + refresh token (~7 days) with `family_id`+`jti` rotation & replay
  detection in Redis. 2-step login when TOTP enabled (`/auth/login` → `/auth/login/totp`).
- 2FA: TOTP secret encrypted at rest, 10 bcrypt-hashed backup codes.
- Rate limiting: Redis sliding window (login/register/reset/COD/shipping); account lockout.

## 4. Frontend architecture

Feature-sliced under `frontend/src/`:
- `app/App.jsx` — router; guards `RequireAdmin` / `RequirePermission`; `AuthBootstrap`.
- `features/<domain>/` — each has `api.js`, `hooks.js` (React Query), optional `store.js` (Zustand).
  24 features incl. auth, cart, products, loyalty, payments, shipping, returns, totp, cod, roles…
- `pages/` — lazy-loaded route pages; `pages/admin/` — 16 admin pages.
- `services/apiClient.js` — Axios with auth interceptor + **single-flight 401 refresh** + token rotation.
- `components/ui` — CVA-based primitives (Button, Card, Input…). `styles/global.css` — theme tokens.

State model: **Zustand** for persisted client state (auth, theme, browsing-history ring buffer);
**React Query** for server state (60s staleTime, no retry on 4xx, one retry on 5xx).

Storefront routes: `/`, `/products`, `/products/:id`, `/cart`, `/wishlist`, `/rewards`,
`/checkout`, `/orders`, `/account/security`, `/login`, payment return pages.
Admin routes under `/admin/*` gated by permissions.

## 5. Infra & running it

`docker-compose.yml` services: **mysql** (8.4), **redis** (7), **backend** (8000), **frontend**
(5173→80), **nginx** (80, reverse proxy: `/api`,`/docs`→backend, `/`→frontend, 20MB upload cap).
Health-aware deps: backend waits for healthy mysql+redis.

- Dev overlay `docker-compose.dev.yml`: frontend → node:20 Vite dev server w/ HMR, nginx `dev.conf`.
- Prod overlay `docker-compose.prod.yml`: 4 uvicorn workers, `restart: always`, internal-only db/redis.

```bash
cp .env.example .env
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
docker compose up --build           # app → http://localhost
docker compose -f docker-compose.yml -f docker-compose.dev.yml up   # dev w/ HMR
```
First migration:
```bash
docker compose exec backend alembic upgrade head
```
Tests: `docker compose exec backend pytest` · `docker compose exec frontend npm test`.
Backend E2E scripts live in `backend/scripts/test_*_e2e.py`; frontend Playwright `verify-*.mjs`.

## 6. Config / env keys (values redacted)

Root `.env`: `MYSQL_ROOT_PASSWORD`, `MYSQL_DB`, `MYSQL_USER`, `MYSQL_PASSWORD`.
Backend `.env`: `SECRET_KEY`, token TTLs, `MYSQL_*`, `REDIS_URL`, `CORS_ORIGINS`,
`PAYMENT_RETURN_URL`/`WEBHOOK_URL`, email/SMS/S3 keys. (Gateway choice + PhonePe creds
now live in the DB — Admin → Settings → Payments — not `.env`.)
Frontend `.env`: `VITE_API_BASE_URL` (`/api/v1`), `VITE_BACKEND_URL`, `VITE_APP_ENV`.

## 7. Feature status from git history
- `b2d3ae4` first commit → `43d035e` base→advanced → `14821ba`/`3baa29d` rewards + loyalty done →
  `7da22d7` **shopflow implementation under process, phase 1** (in progress; references in `xyz/shopflow/`).

## 8. ⚠️ Gotchas / things to verify

1. **`demo_DB/db_ecommerce.sql` is NOT this app's schema.** It's a 118-table phpMyAdmin dump of a
   *Laravel multi-vendor* platform (has `migrations`, `model_has_roles`, `personal_access_tokens`,
   seller/wallet/translation tables). The real app uses ~18 SQLAlchemy tables defined by Alembic in
   `backend/alembic/versions/`. Treat `demo_DB/` as external reference only.
2. **Backend `.env` points at a remote AWS RDS** (host `<DB_HOST>`, db `ecommercesimple`), not the
   local Docker MySQL. So Compose's mysql service may be unused in your current setup — confirm which
   DB is authoritative before running migrations.
   *(Live secrets redacted — host IP and DB password were previously exposed here and must be rotated
   and purged from git history.)*
3. **Hardcoded credentials** sit in `scripts/check_db.py` and `backend/.env` (host/user/pass). `.env`
   is gitignored, but rotate/secret-manage these before any real deployment. `SECRET_KEY` is still
   the placeholder value.
4. **`frontend;C/`** is an empty, accidentally-created folder (stray `;C` from a shell paste) — safe to delete.
5. **Root `_t-*` files** are image-upload test fixtures (incl. a 14MB `_t-big.jpg`); not app code.
6. README claims "JWT auth" but the code uses **PASETO** — docs lag the implementation.
7. Few automated unit tests; coverage is mostly E2E scripts. Add tests around money math
   (order totals, loyalty ledger, COD/split-COD) first.

## 9. Where to start reading
- `backend/app/main.py` → `app/core/config.py` → `app/api/v1/router.py`
- A vertical slice end-to-end: `endpoints/loyalty.py` → `services/loyalty_service.py` →
  `repositories/loyalty_repository.py` → `models/loyalty.py`
- Frontend mirror: `features/loyalty/{api,hooks}.js` → `pages/RewardsPage.jsx`
