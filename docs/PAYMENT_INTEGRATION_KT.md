# Payment Integration — Knowledge Transfer

> Scope: the full order + payment subsystem of the storefront (backend FastAPI + React/Vite frontend).
> Audience: a developer who needs to operate, extend, or debug payments without prior context.
> Last verified against the codebase: 2026-06-17 (branch `vinay`).

---

## 0. TL;DR — the mental model

- A **single endpoint, `POST /checkout`**, creates a `PENDING` order (reserving stock) and, for prepaid orders, asks a **payment provider** for a **redirect URL**. The browser is sent there.
- The provider later calls us back via a **signed webhook** (or we **poll** it). That moves the order `PENDING → PAID` (success) or `PENDING → CANCELLED` (failure/cancel).
- **Our** identifier for the transaction is the **merchant transaction id (`mtid`)**, stored on `orders.payment_intent_id`. Everything (return page, webhook, status poll) looks the order back up by it.
- Gateways are **admin-configured rows** in the `payment_methods` table (26 catalogued, **7 implemented**). Credentials are **Fernet-encrypted** at rest. No redeploy is needed to switch or reconfigure a gateway.
- There are **3 payment methods**: `prepaid` (full charge via gateway), `cod` (no gateway, paid on delivery), `split_cod` (gateway charges a prepaid portion, carrier collects the rest).

```
                 ┌─────────────┐   POST /checkout   ┌────────────────┐
   Customer ───▶ │  Checkout   │ ─────────────────▶ │ PaymentService │ ── reserve stock, build PENDING order
   (browser)     │  Page (SPA) │                    │  .checkout()   │ ── ask provider.initiate()
                 └─────────────┘ ◀───redirect_url─── └───────┬────────┘
                       │                                     │
                       ▼ window.location.assign              ▼
                 ┌─────────────┐                     ┌────────────────┐
                 │  Gateway    │ ── pays / fails ──▶ │  Provider host │
                 │ hosted page │                     └───────┬────────┘
                 └──────┬──────┘                             │ signed webhook (S2S)
                        │ redirect back                      ▼
                        ▼                            POST /payments/webhook/{gw}
              /payments/return?mtid=...                      │
                        │ poll every 1.5s ──▶ GET /payments/{mtid}/status
                        ▼                            (also polls provider if still PENDING)
              success / failure screen
```

---

## 1. End-to-end flows

### 1.1 Prepaid (default)
1. SPA `CheckoutPage` collects items, address, instrument (UPI/card/…), optional coupon, optional explicit gateway, and submits `POST /checkout`.
2. `PaymentService.checkout()`:
   - Resolves shipping + billing address (saved id / inline / legacy free-text).
   - `_build_order()` validates + **decrements stock** (reservation), computes subtotal, per-line tax, shipping (authoritative `rate_quote`, never trusted from client), coupon discount, per-instrument discount, applies free-shipping threshold. Order is `PENDING`.
   - Generates `mtid` (`_new_mtid()` → `"ORD" + 24 hex` = 27 chars, within PhonePe's 35-char limit) and stores it on `order.payment_intent_id`. Flush (gets an id) but **no commit yet**.
   - Resolves provider via factory, sets `order.gateway_code = provider.name`, calls `provider.initiate(InitiateRequest(...))`, stores `order.payment_provider_ref = initiate.provider_transaction_id`.
   - **If `initiate` throws → `db.rollback()`** so reserved stock is returned, error propagates.
   - On success → `db.commit()`, returns `(order, mtid, initiate.redirect_url)`.
3. SPA does `window.location.assign(redirect_url)`.
4. Gateway processes payment, fires webhook → `_apply_status()` → `PAID`/`CANCELLED`.
5. Gateway redirects browser to `PAYMENT_RETURN_URL?mtid=...`. `PaymentReturnPage` polls `/payments/{mtid}/status` every 1.5 s until terminal (`paid`/`cancelled`/`refunded`).

### 1.2 COD (cash on delivery)
- No gateway call at all.
- Server **re-enforces COD availability** (`CodService.check_availability` on the trusted cart + pincode — client cannot bypass) and **OTP** if `cod.require_otp` is on (default **true**); the verified OTP marker is **consumed** so a second COD order needs a fresh OTP.
- COD **surcharge** (flat, snapshotted from `cod.flat_surcharge`) is added. `cod_balance = total` (carrier collects the whole amount).
- Order transitions **straight to `PAID`** via `_mark_paid()` (same side effects as the webhook success path: coupon usage, loyalty, referral, cart clear, notification, optional auto-shipment).
- `redirect_url` is just the return page so the SPA renders a confirmation immediately.

### 1.3 Split COD
- Gateway charges only the **prepaid portion**; carrier collects `cod_balance = total − prepaid`.
- Prepaid portion is snapshotted at order-build (`CodService.split_prepaid_for(total)`) so an admin bumping the setting can't overcharge an in-flight order.
- Same gateway/webhook path as prepaid for the prepaid leg. The balance is handed to the carrier via the `ShipmentRequest.cod_amount` hook in `ShippingService`.
- Guard: if the computed prepaid portion is ≤ 0 (admin lowered the setting between `/cod/check` and submit) checkout is refused with a "re-select payment method" error rather than silently degrading.

---

## 2. Data model

### 2.1 `payment_methods` — the live gateway catalogue (`backend/app/models/payment_method.py`)
| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `gateway_code` | varchar(40) UNIQUE | machine id: `razorpay`, `stripe`, `mock`, … |
| `display_name` | varchar(80) | UI label |
| `enabled` | bool (default false) | admin toggle |
| `environment` | varchar(16) (default `sandbox`) | `sandbox` / `live` |
| `credentials_encrypted` | text (default `""`) | **Fernet-encrypted JSON** dict of credential key→value; `""` = unset |
| `sort_order` | int | lower = first (used to auto-pick default gateway) |
| `created_at`, `updated_at` | datetime(tz) | |

### 2.2 `orders` — payment-relevant columns (`backend/app/models/order.py`)
| Column | Meaning |
|---|---|
| `payment_method` | `prepaid` / `cod` / `split_cod` |
| `payment_instrument` | `upi` / `netbanking` / `card` / `wallet` (nullable) |
| `payment_discount_amount` | per-instrument discount applied |
| `cod_surcharge_amount` | snapshotted flat COD fee |
| `cod_balance` | amount carrier collects (0 prepaid, full COD, partial split) |
| `gateway_code` | which gateway processed it (indexed; matches `payment_methods.gateway_code`) |
| `payment_intent_id` | **our** `mtid` (UNIQUE) — the lookup key everywhere |
| `payment_provider_ref` | provider-side id (Stripe session id, Razorpay payment-link id, …) |
| `currency` | default `INR` |
| `paid_at`, `refunded_at` | lifecycle timestamps |

`OrderStatus` enum: `PENDING | PAID | SHIPPED | DELIVERED | CANCELLED | REFUNDED`.

### 2.3 `payment_gateway_config` — **DEPRECATED** single-row legacy table (`backend/app/models/payment_gateway.py`)
Held the old single active provider + PhonePe creds. **The checkout factory no longer reads it.** Endpoints/service still exist but are slated for removal. Trusting its "active provider" row used to cause a split-brain — see §11.

---

## 3. Provider abstraction (`backend/app/integrations/payments/`)

### 3.1 The contract (`base.py`)
```python
class PaymentStatus(str, Enum):  # PENDING | SUCCESS | FAILED

@dataclass
class InitiateRequest:   order_id, user_id, amount_minor, currency,
                         merchant_transaction_id, return_url, user_email?, user_phone?
@dataclass
class InitiateResponse:  redirect_url, provider_transaction_id?, raw?
@dataclass
class StatusResponse:    merchant_transaction_id, status, provider_transaction_id?,
                         amount_minor?, raw?

class PaymentProvider(Protocol):
    name: str
    def initiate(req) -> InitiateResponse
    def fetch_status(mtid, provider_ref=None) -> StatusResponse
    def verify_webhook(body: bytes, signature: str | None) -> bool
    def parse_webhook(body: bytes) -> StatusResponse
```
- `amount_minor` is always the smallest currency unit (paise for INR, cents for USD).
- `merchant_transaction_id` is **ours** (`payment_intent_id`), distinct from any id the provider assigns.

### 3.2 Shared error surfacing (`base.py`, recent feature — commit `01a3908`)
Historically a rejected charge was wrapped in a generic "rejected" `AppError` and the gateway's real reason was lost. Two helpers fix this:
- `extract_provider_error(response) -> (code, message)` — best-effort parse of each gateway's documented error envelope:
  - Razorpay/Stripe: `{"error": {"code"|"type", "description"|"message"}}`
  - PayPal token: `{"error", "error_description"}`; PayPal Orders: `{"name", "message", "details":[{"issue","description"}]}`
  - Paystack: `{"status": false, "message", "code"?}`; Flutterwave: `{"status":"error","message"}`; PhonePe: `{"code","message"}`
  - Returns `(None, None)` for non-JSON bodies (e.g. an HTML 502). Values trimmed and capped at 300 chars.
- `provider_rejection(error_cls, prefix, response) -> AppError` — builds e.g. `"Razorpay rejected the request: BAD_REQUEST_ERROR — amount is invalid"` and attaches `details={status, provider_code, provider_message}`. **This is what the SPA surfaces** via `err.response.data.error.message`.

### 3.3 Factory (`factory.py`)
- `get_payment_provider(db, gateway_code=None)` → resolves the row via `PaymentMethodConfigService.resolve_for_checkout()`, decrypts credentials, builds a **fresh instance** (no caching, no restart to switch).
- `get_provider_for_order(db, order)` → uses `order.gateway_code`, falling back to the default for legacy NULL rows.
- `_build_provider(row, creds)` → big `if/elif` on `gateway_code` constructing each provider with its creds. PhonePe base URL chosen from `{"sandbox": api-preprod…, "live": api.phonepe…/hermes}` by `row.environment`.
- Unknown/unimplemented code logs an error and falls back to mock (shouldn't happen — `resolve_for_checkout` already filters to `implemented=True`).

### 3.4 Registry (`registry.py`) — the 26-gateway catalogue
`GatewayDef(code, name, description, fields[GatewayField], supports_environment, implemented)`; `get_gateway(code)`, `all_gateways()`.

**Implemented (7):** `paypal`, `stripe`, `razorpay`, `paystack`, `phonepe`, `flutterwave`, `mock`.
**Catalogued but not implemented (19):** sslcommerz, instamojo, voguepay, payhere, ngenius, iyzico, nagad, bkash, aamarpay, authorizenet, payku, mercadopago, paymob, paytm, toyyibpay, myfatoorah, khalti, payfast, tap. Admin can see/configure them but they cannot be enabled for checkout.

### 3.5 The implemented providers
| Provider | initiate (hosted-page API) | status API | webhook header | webhook verify |
|---|---|---|---|---|
| **PhonePe** | `POST /pg/v1/pay` (base64 payload, `paymentInstrument: PAY_PAGE`) | `GET /pg/v1/status/{mid}/{mtid}` | `X-VERIFY` | HMAC-SHA256(salt_key, base64body+path+salt_index) |
| **Razorpay** | `POST /payment_links` → `short_url` | `GET /payment_links/{id}` (`paid`→SUCCESS) | `X-Razorpay-Signature` | HMAC-SHA256(webhook_secret, body); `payment_link.paid`→SUCCESS |
| **Stripe** | `POST /checkout/sessions` → session url | `GET /checkout/sessions/{id}` | `Stripe-Signature` (`t=…,v1=…`) | HMAC-SHA256(secret, `"{t}.{body}"`); `checkout.session.completed`→SUCCESS, `…expired`→FAILED |
| **PayPal** | `POST /v2/checkout/orders` (intent CAPTURE) → approve link | `GET /v2/checkout/orders/{id}` then `POST /capture` | — | **Not used — `verify_webhook` always False; status-polling only** |
| **Paystack** | `POST /transaction/initialize` → `authorization_url` | `GET /transaction/verify/{ref}` | `x-paystack-signature` | HMAC-SHA512(secret, body); `charge.success`→SUCCESS |
| **Flutterwave** | `POST /payments` → `data.link` | `GET /transactions/verify_by_reference?tx_ref=` | `verif-hash` | **plain `compare_digest`** of stored hash (NOT HMAC) |
| **Mock** | redirects to SPA `/payments/mock/{mtid}` | reads Redis | — | none (dev only) |

**Mock provider** stores state in Redis (`payment:mock:{mtid}`, TTL 1 h). Its webhook is unsigned and **hard-disabled in production**.

---

## 4. API surface

### 4.1 Checkout & payment (`api/v1/endpoints/payments.py`)
| Method | Path | Auth | Body / params | Response |
|---|---|---|---|---|
| POST | `/checkout` | user | `CheckoutRequest` | 201 `CheckoutResponse` |
| GET | `/payments/{mtid}/status` | user (owner) | — | `PaymentStatusResponse` (polls provider if still PENDING) |
| GET | `/payments/{mtid}/order` | user (owner) | — | `OrderRead` (full order keyed by mtid — used by return page) |
| POST | `/payments/webhook/phonepe` | signature | raw body + `X-VERIFY` | `{ok:true}` |
| POST | `/payments/webhook/mock` | none (dev) | `MockWebhookRequest` | settles; **403 in production** |
| POST | `/payments/webhook/{gateway_code}` | signature | raw body + per-gw header | validates code is known+implemented+enabled, picks the right signature header, verifies & applies |

`CheckoutResponse` includes `amount_minor` = what the gateway was asked to charge: **0** for COD, **prepaid portion** for split COD, **full total** for prepaid.

### 4.2 Admin gateway config (`api/v1/endpoints/payment_methods.py`)
| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/admin/payment-methods` | `payments.manage` | all gateways + config state (`enabled/implemented/ready/environment/fields`) |
| PUT | `/admin/payment-methods/{code}` | `payments.manage` | partial merge of `{enabled?, environment?, credentials?}`; enabling requires `implemented` + all required fields |
| GET | `/payment-methods/active` | **public** | only `enabled && implemented && ready`, sorted by `sort_order` — drives the SPA gateway selector |

### 4.3 Payment instruments (`api/v1/endpoints/payment_instruments.py`)
- `GET /payments/instruments` (public, rate-limited) → UPI / netbanking / card / wallet with `enabled`, `discount_percent`, `suggested`.

### 4.4 Legacy (`payment_gateway.py`) — DEPRECATED
`GET/PUT /admin/payment-gateway` for the old single-provider PhonePe config. Kept for now; do not build on it.

---

## 5. Schemas (`backend/app/schemas/`)
- `payment.py`: `CheckoutRequest` (items, address routing, payment_method, payment_instrument, customer_phone, coupon_code, gateway_code, billing fields, currency), `CheckoutResponse`, `PaymentStatusResponse`, `MockWebhookRequest`.
- `payment_method.py`: `PaymentMethodRead` (+`PaymentMethodFieldRead`), `PaymentMethodUpdate`, `PaymentMethodActiveRead`.
- `payment_instruments.py`: `PaymentInstrumentItem`, `PaymentInstrumentsResponse`.
- `payment_gateway.py`: legacy `PaymentGatewayRead/Update` (salt key write-only via `"***"` sentinel).

---

## 6. Services (`backend/app/services/`)
- **`payment_service.py`** — `PaymentService` orchestrates the whole lifecycle (see §1). Key methods: `checkout`, `handle_webhook`, `get_status`, `mark_mock_decision`; internals `_build_order`, `_apply_status`, `_mark_paid`, `_restore_stock`, side-effect fan-out (`_send_notification`, `_maybe_auto_push_shipment`, `_award_loyalty_points`, `_complete_referral`, `_record_coupon_usage`, `_clear_cart`).
- **`payment_method_config_service.py`** — `PaymentMethodConfigService`: `list_items`, `update`, `credentials_for` (decrypt), `resolve_for_checkout` (explicit code validation, else lowest-sort-order enabled+implemented+ready, else mock fallback).
- **`payment_methods_service.py`** — `PaymentMethodsService`: the **instrument** toggles + discounts (UPI/card/…), reads `payments.instruments.{code}.enabled` / `.discount_percent` / `payments.suggested_instrument` from settings. **Not** gateway config — note the easily confused name.
- **`payment_gateway_service.py`** — DEPRECATED legacy PhonePe config service.

---

## 7. Configuration & secrets

### 7.1 Env vars (`backend/app/core/config.py`)
- `PAYMENT_RETURN_URL` (default `http://localhost:5173/payments/return`) — where providers send the customer back.
- `PAYMENT_WEBHOOK_URL` (default `http://localhost:8000/api/v1/payments/webhook/phonepe`) — PhonePe callback URL.
- `REDIS_URL` — mock provider state.
- `FRONTEND_URL` — used to build the mock redirect.
- `SECRET_KEY` — seeds the Fernet key for credential encryption.

### 7.2 Encryption at rest (`backend/app/core/crypto.py`)
- Fernet (AES-128-CBC + HMAC-SHA256). Key = SHA256(`"fernet-at-rest:" + SECRET_KEY`).
- `encrypt_secret` / `decrypt_secret`. The **whole credentials JSON blob** per gateway is encrypted as one unit. Rotating `SECRET_KEY` invalidates all stored credentials (decrypt raises `InvalidToken`).

### 7.3 Relevant settings keys (DB-backed `SettingsService`)
`payments.suggested_instrument`, `payments.instruments.{code}.enabled`, `payments.instruments.{code}.discount_percent`, `shipping.free_threshold`, `shipping.auto_create_on_paid`, `cod.require_otp`, `cod.flat_surcharge`, `cod.split_prepaid_amount`.

---

## 8. Webhooks, status polling & correctness guarantees

- **Signature verification** is per-gateway (table in §3.5). The generic `/webhook/{gateway_code}` route first checks the gateway is known, implemented, and enabled, then selects the right header. Invalid signature → `403 Invalid payment signature`.
- **Idempotency** (`_apply_status`): only orders in `PENDING` move; re-delivered webhooks on a terminal order are a no-op. Notifications/side-effects fire **after** commit so no "paid" email goes out for an unsaved row.
- **Status polling** (`get_status`): if the customer beats the webhook back, a still-`PENDING` order triggers a live `provider.fetch_status()`. A flaky provider is caught and logged — the poll never 500s.
- **Amount-tampering protection**: on SUCCESS, if the gateway-reported `amount_minor` ≠ expected (`total_amount × 100`), the order is **left PENDING** and an error is logged for manual review — it is **not** marked PAID. This blocks amount tampering and cross-order webhook replay.
- **Stock**: reserved (decremented) at checkout; **restored** only on FAILED/CANCELLED.

---

## 9. Frontend (`frontend/src/`)

### 9.1 Pages
- **`pages/CheckoutPage.jsx`** — 2-step (address → payment). Selects `paymentMethod` (`prepaid`/`cod`/`split_cod`), `paymentInstrument`, `selectedGateway` (only shown when `instrumentApplies && activeGateways.length > 1`). On submit → `useCheckout().mutateAsync(payload)` → `window.location.assign(resp.redirect_url)`. Errors surfaced from `err.response.data.error.message` in a red banner.
- **`pages/PaymentReturnPage.jsx`** — route `/payments/return?mtid=`. `usePaymentStatus(mtid)` polls every 1.5 s, stops on terminal state (`paid`/`cancelled`/`refunded`). Renders pending / success / failure / error / bad-params states.
- **`pages/PaymentMockPage.jsx`** — route `/payments/mock/:txnId?return=&amount=`. Approve/Decline → `paymentsApi.mockDecision()` → redirect. Has an **open-redirect guard** (`return` must start with `/`).
- **`pages/admin/AdminPaymentMethodsPage.jsx`** — gateway admin: expandable rows, dynamic credential fields (secret fields masked with "saved — type to replace"), sandbox/live toggle, dirty-tracked partial save, status badges (Enabled / Coming soon / Keys required / Sandbox|Live).

### 9.2 API client & hooks (`features/payments/`, `features/cod/`, `features/paymentMethods/`)
- `paymentsApi`: `checkout`, `status(mtid)`, `orderForPayment(mtid)`, `mockDecision(mtid, action)`.
- `paymentMethodsApi`: `list()` (admin), `update(code, payload)` (admin), `listActive()` (public selector).
- `instrumentsApi.list()`; `codApi`: `check`, `sendOtp`, `verifyOtp`.
- Hooks: `useCheckout` (mutation, invalidates products on success), `usePaymentStatus` (query, caller-supplied `refetchInterval`, `retry:false`), `usePaymentInstruments`, `usePaymentMethods`/`useUpdatePaymentMethod`/`useActivePaymentMethods`, `useCodCheck`/`useSendCodOtp`/`useVerifyCodOtp`.
- **COD OTP**: `features/cod/components/CodOtpModal.jsx` auto-sends OTP on mount, 30 s resend cooldown, re-fires checkout on verify.

---

## 10. Refunds — current state
Refunds are **order-level bookkeeping only**: admin `POST /admin/orders/{id}/refund` (perm `orders.refund`) sets `status=REFUNDED`, `refunded_at`, `refund_reason`. **No provider-side reversal API call is made** — the money is not actually returned through the gateway by the app. Treat this as a known gap if true refunds are required.

---

## 11. Gotchas & known issues
- **Two similarly-named services**: `payment_methods_service.py` (instruments/discounts) vs `payment_method_config_service.py` (gateway config). Don't confuse them.
- **Deprecated legacy config split-brain (fixed)**: `mark_mock_decision`/`mock_webhook` used to read the legacy `PaymentGatewayConfig` "active provider" row, which the factory no longer consults. That let a *real* order be mock-settled or a genuine mock order be blocked. **Now authorization is per-order**: a mock settle is allowed only if `order.gateway_code == "mock"` (this is the uncommitted change currently in your working tree on `payments.py` + `payment_service.py`).
- **PayPal has no webhook** — relies entirely on return-page status polling + auto-capture. If the customer closes the tab before returning, reconciliation depends on the next status poll.
- **Flutterwave verify is a plain hash compare**, not HMAC — correct per their docs but worth knowing.
- **`SECRET_KEY` rotation wipes all gateway credentials** (decrypt fails) — re-enter them after rotation.
- **Mock webhook is unsigned** — hard-disabled in production via `settings.ENVIRONMENT == "production"`.
- From project memory: PhonePe credentials may need re-entry after config edits; PayPal webhook intentionally off.
- Security audit (2026-06) found 35 vulns, ~65% remediated then paused — payments may have outstanding items; the prod DB credential rotation/history-purge was still pending.

---

## 12. How to add a new gateway
1. **Registry** (`registry.py`): add/flip a `GatewayDef` to `implemented=True` with its credential `fields`.
2. **Provider class** (`integrations/payments/<name>.py`): implement `initiate / fetch_status / verify_webhook / parse_webhook`. Use `provider_rejection(...)` in the `httpx.HTTPStatusError` branch so the gateway's real error surfaces.
3. **Factory** (`factory.py` `_build_provider`): add an `if code == "<name>":` branch wiring creds → constructor.
4. **Webhook header** (`payments.py` generic route): add the gateway's signature header to the selection block.
5. **Migration**: ensure a `payment_methods` row exists (the seed migration `…add_payment_methods` already inserts all 26 — new ones go in a follow-up migration).
6. Admin enables it in `/admin/payment-methods`, enters credentials, sets sandbox/live. No redeploy needed to activate.
7. Add tests (see §13) and verify the full redirect→webhook→status loop with the mock first.

---

## 13. Local dev & testing
- Default gateway in dev is **mock** (`sort_order` 99). Seeded enabled, but the seed migration disables it once a real gateway is configured — so a live DB may have it off; enable it explicitly before relying on it. Checkout returns a redirect to `/payments/mock/{mtid}`; click Approve/Decline to drive `_apply_status` exactly like a real webhook.
- Backend changes require a **restart**; frontend changes a **rebuild** (Windows bind-mount watch is unreliable — see project memory).
- MySQL host port is **3307** in this environment.
- Tests: `qa-agent` runs pytest (backend) / vitest (frontend). Cover: amount-mismatch rejection, idempotent re-delivery, signature failure 403, COD OTP gate, split-COD prepaid math, stock restore on failure.

---

## 14. File manifest
**Backend**
- `app/api/v1/endpoints/payments.py` — checkout + status + webhooks
- `app/api/v1/endpoints/payment_methods.py` — admin + public gateway config
- `app/api/v1/endpoints/payment_instruments.py` — instrument list
- `app/api/v1/endpoints/payment_gateway.py` — DEPRECATED legacy config
- `app/services/payment_service.py` — lifecycle orchestrator
- `app/services/payment_method_config_service.py` — gateway config service
- `app/services/payment_methods_service.py` — instrument toggles/discounts
- `app/services/payment_gateway_service.py` — DEPRECATED
- `app/integrations/payments/{__init__,base,registry,factory,phonepe,razorpay,stripe,paypal,paystack,flutterwave,mock}.py`
- `app/models/{payment_method,payment_gateway,order}.py`
- `app/schemas/{payment,payment_method,payment_instruments,payment_gateway}.py`
- `app/core/crypto.py` — Fernet encrypt/decrypt
- `alembic/versions/*add_payment_methods*.py`, `*add_payment_gateway_table*.py`

**Frontend**
- `pages/{CheckoutPage,PaymentReturnPage,PaymentMockPage}.jsx`, `pages/admin/AdminPaymentMethodsPage.jsx`
- `features/payments/{api,hooks,instruments}.js`, `features/cod/{api,hooks,components/CodOtpModal}.jsx`, `features/paymentMethods/{api,hooks}.js`
```
