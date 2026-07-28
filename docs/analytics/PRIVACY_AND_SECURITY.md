# Analytics v2 — privacy, retention and security

What the analytics subsystem stores about people, how long it keeps it, what it
deliberately refuses to store, how its secrets are held, who can read what, and
what an operator has to do when someone asks to be deleted.

**Scope note, read first.** Everything below describes what is *implemented in
this repository today*. Anything not built is marked **NOT IMPLEMENTED** and says
what would have to exist. Nothing here is aspirational: a privacy document that
describes intentions as if they were controls is worse than no document, because
it is the one an auditor will be handed.

The retention policy table is generated from
`backend/app/services/analytics/retention.py` — `describe_policies()` returns it
as data, and a test asserts the two agree. If this file and that module ever
disagree, the module is right.

---

## 1. What personal data is stored

The analytics schema is **pseudonymous by construction**. No analytics table has
an email, phone number, name, street address, IP address or user-agent column —
identity lives one join away in `users`, and the analytics tables carry keys
rather than people.

### Customer identity

| Where | Column | What it is |
|---|---|---|
| `agg_customer_snapshot` | `customer_key` | `u:<user_id>` — a stable analytics identity that survives account merges and leaves room for a future guest-checkout namespace. Not reversible without the `users` table. |
| `agg_customer_snapshot` | `user_id` | Nullable convenience join target. No foreign key, by design. |
| `cart_events` | `session_key` | NOT NULL anonymous funnel identity — a cookie or client-generated id, minted by the writer. It is the top of the funnel, where the visitor has no account. |
| `cart_events` | `user_id` | Nullable; set once the visitor authenticates, which is what stitches one session across the login boundary. |
| `analytics_order_line`, `analytics_order_adjustment` | `order_id` | Order references only. No customer column at all. |

**`cart_events` is defined but has no writer. NOT IMPLEMENTED.** The table
exists, `FunnelDailyJob` reads it, and `prune_all` has a policy for it — but
nothing in the codebase inserts a row, so **no session keys are stored today**.
The two consequences are opposite in sign and both worth stating: there is
currently no funnel-tracking privacy exposure at all, and there is also no
funnel data. Whoever builds the ingestion endpoint inherits the retention
policy in §2 already written and tested.

`agg_customer_snapshot` also holds derived judgements about a person —
`rfm_segment` ("champions", "at_risk"), `churn_risk_band`, `is_active`,
`gross_ltv`/`net_ltv`, `preferred_payment_method`. These are profiling outputs,
not raw data, and they are one of the two reasons `analytics.customers.view` is
a SENSITIVE permission tier (§5).

### Location

| Where | Column | What it is |
|---|---|---|
| `agg_geo_daily` | `state` | Coarse. Retained indefinitely. |
| `agg_geo_daily` | `pincode` | 6-digit Indian PIN, normalised by the writer. **The most identifying thing in the analytics schema.** A small pincode plus a date plus an order count is close to naming a household — which is exactly why it has the 180-day horizon in §2 and why "pincode" is on the outbound PII denylist in §3. |

`'-'` is a real value on both columns, meaning "unknown" or "rolled up to state
grain". It is never a NULL stand-in: a NULL escapes MySQL's UNIQUE-key semantics
and would make collapsed rows indistinguishable from a bug.

### Third-party analytics identifiers

| Where | Column | What it is |
|---|---|---|
| `analytics_event_outbox` | `client_id`, `session_id` | GA cookie identifiers (`_ga`, `_ga_<container>`) captured at checkout. Stored **only when analytics consent was granted** — `enqueue_purchase` writes `None` for both otherwise. |
| `analytics_event_outbox` | `consent_state` | `granted` / `analytics_only` / `denied` / `unknown`, captured at the moment of purchase rather than read at delivery time, because consent is a property of the interaction. |
| `analytics_event_outbox` | `payload` | The GA4 `purchase` payload: items, value, currency, tax, shipping, coupon. The model docstring is explicit — **never** customer email, phone or address. |
| `analytics_event_outbox` | `transaction_id` | The internal `orders.order_number`, not a gateway id. |

When consent is `denied` the row is still written, marked
`SUPPRESSED_NO_CONSENT`, and **never transmitted**. Internal reporting and
third-party transmission are different decisions and only the second one needs
consent; a missing row would be indistinguishable from a lost one.

For an event with no consented cookie, `outbox.client_id_for()` derives a
pseudonymous client id by SHA-256 of `wellvia-ga4:<transaction_id>`. It joins
nothing — the purchase lands in GA4 as `(direct)/(none)` — and it is derived
rather than random so a retry is the same pseudonymous user rather than a new
one.

### Staff identity

`analytics_cost_rules.created_by_user_id`, `analytics_budgets.created_by_user_id`,
`analytics_alerts.acknowledged_by_user_id` and `inventory_movements.actor_user_id`
are plain nullable integers with no foreign key, deliberately: the audit trail
must outlive the staff account that created it. `analytics_sync_runs.worker_id`
is a hostname/pid, not a person.

### Free-text fields that could contain anything

Three columns are typed by a human or copied from an upstream payload and are
therefore only as clean as their writers:

* `analytics_order_adjustment.note` — usually the admin's refund reason.
* `analytics_order_adjustment.raw` — the originating payload, kept for forensics.
* `analytics_alerts.context` — detector evidence, which includes "top
  contributing dimension values" and can therefore contain a pincode.

Each carries the same prohibition as `payment_events`: never gateway
signatures, tokens or card data. **NOT IMPLEMENTED:** nothing enforces that at
write time for these three. `assert_no_pii` (§3) guards the *outbound* boundary
only.

---

## 2. Retention, and why each horizon is what it is

Implemented in `app/services/analytics/retention.py` as `prune_all()`. It runs
from the rollup worker's prune phase, deletes in bounded batches with a per-run
ceiling, supports a real `dry_run` preview, and writes an `analytics_sync_runs`
row under the job name `retention_prune` on every call — so a deletion is as
auditable as an aggregation.

| Table / data | Horizon | Where the policy comes from | Why |
|---|---|---|---|
| `agg_order_hourly` | 90 days | `AggOrderHourly` docstring | 24x the row count of the daily table, and it only answers "what does a normal Tuesday look like" and "when did checkout break". `agg_order_daily` is the long-term record. |
| `agg_customer_snapshot` | 90 days daily, then **month-end rows only** | `AggCustomerSnapshot` docstring | Grain is customers × days: 50k customers is 18M rows a year for a table whose main use is "current state". Long-term LTV and cohort curves need a monthly sample, not a daily one. |
| `agg_geo_daily` pincode grain | 180 days, **collapsed to state grain** | `AggGeoDaily` docstring | Two reasons, and the second is binding: ~19k live pincodes make it the widest table in the module, and fine-grained geographic history is personal-data adjacent with no analytical payoff at that age. |
| `analytics_recompute_queue` | 90 days, `done` rows only | Decided in `retention.py` | `failed` rows are what alerting reads; deleting one closes the only report of a bucket that will never rebuild itself. |
| `analytics_sync_runs` | 90 days, `success` / `skipped_locked` only | Decided in `retention.py` | See the caveat below — this one contradicts its model docstring on purpose. |
| `analytics_event_outbox` | 90 days, `delivered` rows only | Decided in `retention.py` | 90 days after delivery the GA cookie ids have served their only purpose. `pending` rows are conversions still owed, `failed` rows feed the GA4_SYNC_FAILURE alert, and `suppressed_no_consent` rows are the record that consent was honoured — none are pruned. |
| `cart_events` | 400 days, then aggregate-only | Decided in `retention.py` | Highest-volume table in the schema. 400 days keeps raw sessions on both sides of a year-over-year funnel comparison with a month of slack; beyond that `agg_funnel_daily` is the record. |
| CSP violation reports | 30 days | Decided in `retention.py` | **NOT IMPLEMENTED — nothing stores one.** See §2.3. |

Everything else in the analytics schema is retained **indefinitely**: §2.4 lists
what that means and where it is a gap.

### 2.1 The geo collapse is the one irreversible policy

Every other rollup row the prune removes is derived from a fact table and can be
rebuilt. There is **no pincode-grain fact table anywhere in this schema**, so a
pincode row deleted without first being summed into its state row is gone for
good. The implementation therefore aggregates a batch up to state grain, adds it
into the state-grain row, and deletes the pincode originals **inside one
transaction**. Because the upsert accumulates rather than overwrites, a run
interrupted between batches still leaves state totals exactly correct.

**The 180-day pincode horizon is enforced *eventually*, not *continuously*.**
`ShipmentGeoDailyJob` (`jobs_finance.py`) writes this table by replacing the
whole `(bucket_date, tz_generation)` — DELETE then INSERT — and it does **not**
refuse to rewrite buckets older than 180 days the way `CustomerSnapshotJob`
refuses aged-out daily rows. So recomputing a year-old bucket (a refund against
a year-old order, or a manual backfill) re-materialises that day's pincode rows,
and they stay until the next prune.

Totals are not at risk: a full replace wipes the collapsed `'-'` row too, so
re-collapsing cannot double-count. Had the job been an upsert it would. But the
*privacy* half of the control is bounded by the prune interval (hourly by
default), not by the horizon. Closing that gap means teaching the geo job to
refuse aged-out pincode grain, which is a change to `jobs_finance.py`.

### 2.2 The `analytics_sync_runs` policy contradicts its own model docstring

`AnalyticsSyncRun`'s docstring says rows are "never DELETEd by the application",
and the reason is sound: a run log that can be edited after the fact cannot
answer "did the pipeline actually run on the 3rd?", which is the only reason the
table exists. The 90-day policy is a deliberate, narrow exception, scoped so that
everything the docstring protects survives:

* `failed` and `partial` runs are kept **forever** — those are the incidents.
* Rows stuck at `running` are kept forever — that is the sole trace of a process
  that died mid-run.
* Each job's highest `watermark_date` is kept forever, because that value is what
  the retention job itself reads to decide what it is allowed to delete.
* Only `success` and `skipped_locked` runs older than 90 days are removed, and
  what they record — a routine tick doing routine work — is the one thing in the
  table nobody has needed at that age.

A deployment that does not accept that trade turns it off with
`prune_all(db, only=[...])` omitting `analytics_sync_runs`. Nothing else depends
on it.

### 2.3 CSP reports: declared, not implemented

`SecurityHeadersMiddleware` sets a `Content-Security-Policy` header on every
non-docs response, but it carries **no `report-uri` and no `report-to`
directive**, there is no endpoint that accepts a violation report, and no table
holds one. The 30-day policy exists in the policy table and reports
`skipped: "not_implemented"` rather than a silent zero, so an auditor can tell
"nothing to delete" from "this is not a thing yet". It engages by itself the day
a `csp_reports` table appears.

To actually implement it: add a `report-uri` directive, an endpoint that accepts
`application/csp-report`, and a table with a `created_at` column.

### 2.4 What has no retention policy at all

Stated plainly, because an unstated horizon is "forever":

* **`analytics_order_line` and `analytics_order_adjustment`** — the immutable
  revenue facts. Retained indefinitely, deliberately: they are the financial
  record and every rollup is rebuilt from them. They carry no customer identity
  beyond `order_id`.
* **`inventory_movements`** — the stock ledger, append-only and forward-only.
  Indefinite. Carries `actor_user_id` for manual adjustments.
* **`agg_order_daily`, `agg_product_daily`, `agg_customer_daily`,
  `agg_payment_daily`, `agg_shipment_daily`, `agg_promo_daily`,
  `agg_funnel_daily`, `agg_inventory_daily`, `agg_customer_cohort_monthly`** —
  aggregate rollups with no personal grain. Indefinite, and small.
* **`agg_geo_daily` at state grain** — indefinite. Only the pincode grain ages
  out.
* **`analytics_alerts`** — indefinite, including `context`, which can carry a
  pincode (§1). **This is the clearest retention gap in the subsystem.**
* **`analytics_budgets`, `analytics_cost_rules`, `analytics_tz_generations`** —
  authored configuration with effective dating. Indefinite by design; deleting an
  old cost rule would retroactively change a historical margin.
* **`obs_request_logs`** (not part of analytics v2, but adjacent and it stores an
  `ip` column) — pruned at `OBS_RETENTION_DAYS`, default **7 days**, by the
  observability buffer's hourly sweep.

---

## 3. What is deliberately NOT stored

### No PII leaves the building — enforced, not reviewed

`app/services/analytics/tracking_events.py` defines `PII_DENYLIST` and
`assert_no_pii()`, checked **at the delivery boundary** rather than at review
time, so it catches a leak introduced by any caller rather than only the ones
someone remembered to review.

The denylist is matched by **substring**, deliberately — `customer_email`,
`billing_email` and `email_address` are all caught by `email`:

```
email, phone, mobile, name, address, street, pincode, postcode, zip,
password, otp, card, cvv, upi, gstin, pan, dob, birth
```

`PII_ALLOWED_EXACT` is the documented escape hatch for GA4's own vocabulary
(`item_name`, `item_list_name`, `promotion_name`, `creative_name`, `currency`),
matched **exactly** so `item_name` passes while `customer_name` does not.

A violation raises `PiiLeak` and is **fatal, not sanitising**. A payload carrying
PII means the caller is wrong, and silently stripping the field would let the bug
ship and recur everywhere that caller is copied.

### Column-level prohibitions

* `agg_customer_snapshot` — "Deliberately not here: PII. No email, no phone, no
  name." This table is exported, sampled and joined casually; identity lives one
  join away in `users`.
* `analytics_event_outbox.payload` — "NEVER put customer email/phone/address in
  here."
* `analytics_order_adjustment.note` / `.raw` — never gateway signatures, tokens
  or card data.
* `analytics_sync_runs.error` — "Never store credentials or connection strings."
* No analytics table has an IP address or user-agent column.

---

## 4. How secrets are held

Analytics integration credentials are **Fernet-encrypted at rest**, using the
same `app.core.crypto` helpers as `PaymentMethod.credentials_encrypted`.

* The key is derived from `SECRET_KEY` via SHA-256 with the domain label
  `fernet-at-rest:`, which keeps it distinct from the PASETO token key derived
  from the same secret. Rotating `SECRET_KEY` re-keys this subsystem and
  invalidates every encrypted value — acceptable and desired after a deliberate
  rotation.
* No separate key management system, no envelope encryption, no HSM. **NOT
  IMPLEMENTED**, and worth knowing: the encryption key is derivable from the
  application secret, so anything that can read `SECRET_KEY` can read the
  ciphertext.

Every integration setting carries a `visibility` that decides where the value is
allowed to travel:

| Visibility | Example | Where it may appear |
|---|---|---|
| `PUBLIC` | GA4 measurement id, GTM container id, Clarity project id | Already embedded verbatim in every page; safe in `/settings/public`. Treating these as secrets would be theatre. |
| `ADMIN` | GA4 Data API property id | Admin responses only, never `/settings/public`. |
| `SECRET` | GA4 Measurement Protocol `api_secret`, Data API service-account JSON | Fernet-encrypted, redacted to `***` in every response, decrypted only by server code about to use it. |

The rule the code enforces rather than documents: `current_values()` — the only
function the API layer calls to build a response — **cannot return a SECRET
value**. Backend-only, by construction rather than by discipline.

---

## 5. Permission tiers

Defined in `app/services/permissions_registry.py`; enforced by
`require_permission` on the routers.

`analytics.view` is the base grant. It **only opens the section** — every module
still requires its own permission on top, which is why holding the base alone
must not return a single number from a finance view.

| Permission | Opens |
|---|---|
| `analytics.view` | Base access to the analytics section |
| `analytics.executive.view` | Executive summary, business health, forecasting, budget-vs-actual |
| `analytics.sales.view` | Sales, revenue, orders, discount performance |
| **`analytics.finance.view`** | **SENSITIVE** — margin, COGS, contribution, unit economics, cash flow, tax, settlement |
| `analytics.products.view` | Product, category, SKU, merchandising |
| **`analytics.customers.view`** | **SENSITIVE** — customer-level analytics including personal drill-down (LTV, segments, cohorts, RFM) |
| `analytics.marketing.view` | Channels, campaigns, SEO, attribution |
| `analytics.website.view` | Traffic, funnel, checkout, on-site conversion |
| `analytics.inventory.view` | Inventory, stock availability, supply chain |
| `analytics.orders.view` | Fulfilment, shipping, returns, cancellations |
| `analytics.payments.view` | Payment success/failure, COD, fraud risk |
| `analytics.marketplace.view` | Marketplace, store/branch, B2B accounts |
| `analytics.cx.view` | Support, reviews, UX |
| `analytics.control_centre.view` | Tracking health, reconciliation, experiments, alerts |
| `analytics.export` | Export to CSV/XLSX — still limited to views the user can open |
| `analytics.integrations.manage` | Connect and configure GA4, Search Console, ads, Clarity |
| `analytics.budgets.manage` | Create and edit budgets and targets |
| `analytics.alerts.manage` | Create and edit alert rules and thresholds |
| `analytics.jobs.run` | Manually trigger rollup, backfill and reconciliation jobs; also bypass the cache |

Three properties worth stating:

1. **Module grants do not imply each other.** Finance and customers are separate
   tiers specifically so an ops user can see operations without seeing margin or
   a named customer's purchase history.
2. **Export is a separate grant from viewing**, because a CSV leaves the
   building.
3. **The SENSITIVE tiers are part of the cache key.**
   `view_service.SENSITIVE_PERMISSIONS` is ordered, and that order is a contract:
   appending a tier lengthens every tier token and makes every previously-written
   cache key unreachable. That is correct — an entry written before a tier
   existed was computed without knowing about it and must not be served after it
   does.

**NOT IMPLEMENTED:** there is no per-row or per-customer access control. Anyone
holding `analytics.customers.view` sees every customer.

---

## 6. Data-subject deletion requests

**There is no automated erasure path. NOT IMPLEMENTED.** No endpoint, no service
function, no CLI, no audit record of an erasure having been performed. What
follows is the manual runbook, and the first step is the one people get wrong.

### The thing that makes this non-obvious

**The analytics rollups are derived. Deleting a row from them does not erase
anything — the aggregator will rewrite it from `orders` on the next recompute.**
Erasure has to happen at the source first, or the deletion silently undoes
itself within the hour.

There are no foreign keys anywhere in the analytics schema, so **nothing
cascades**. Every table below must be handled explicitly.

### Runbook

1. **Resolve the identity.** Find `users.id`. The analytics identity is
   `customer_key = 'u:<user_id>'` (`jobs_customer._customer_key`).

2. **Erase at the source, first.** The rollups are rebuilt from `orders`,
   `order_items`, `returns` and `users`. Until the source is anonymised or
   removed, every step below is temporary.

   Note that `orders.user_id` is `ON DELETE RESTRICT`: a customer with orders
   **cannot** be hard-deleted while those orders exist. In practice that means
   anonymising the `users` row (and `customers`, `addresses`, `order_addresses`)
   rather than deleting it, and keeping the orders as the financial record —
   which is normally the correct outcome anyway, since invoices are subject to
   their own statutory retention.

3. **Then remove the analytics-side identity:**

   | Table | What to do |
   |---|---|
   | `agg_customer_snapshot` | Delete `WHERE customer_key = 'u:<id>' OR user_id = <id>`. Safe: fully derived, rebuildable, and it will not come back once the source is anonymised. |
   | `cart_events` | Delete `WHERE user_id = <id>`. Also delete by `session_key` for the anonymous half of the same sessions — the funnel identity is the session, not the account, and the pre-login events carry no `user_id`. **Not rebuildable.** `agg_funnel_daily` keeps the aggregate. |
   | `analytics_event_outbox` | Delete `WHERE order_id IN (<their orders>)`. Removes the stored GA cookie ids. Only prune rows already `delivered` or `suppressed_no_consent`; a `pending` row is a conversion still owed and deleting it loses revenue reporting. |
   | `analytics_order_line`, `analytics_order_adjustment` | **Keep.** They carry `order_id` only, no personal identifier, and they are the revenue record every margin figure is rebuilt from. |
   | `agg_geo_daily` | **Keep.** Not per-customer. If the request is specifically about location, the 180-day pincode collapse (§2) is the control that applies. |
   | `analytics_alerts` | Inspect `context` for the customer's pincode or ids and clear it manually. There is no query for this. |

4. **Ask GA4 separately.** Anything already transmitted to GA4 is in Google's
   systems and this codebase cannot recall it. Use GA4's own User Deletion API /
   data-deletion request with the `client_id` from `analytics_event_outbox`
   before you delete that row. **NOT IMPLEMENTED** — there is no integration for
   this.

5. **Record it.** There is no erasure audit trail in this schema. Use the
   existing `audit` table so the action is attributable.

### What would make this safe

A `delete_customer_analytics(user_id)` service that runs all of the above in one
transaction, writes an audit row, and is covered by a test. Until that exists,
this is a manual procedure and should be treated as one — including the part
where a manual procedure is run by a human at 6pm on a Friday.
