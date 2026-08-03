# Payment-Launch Deploy Runbook — vinay → production

Written 2026-08-03 after the eng review of the 12-commit `vinay` branch and the
same-day payment-strand incident. **The git push is the cheapest step in this
list.** Everything that actually decides whether a customer can pay lives in
config and data outside git — this runbook orders all of it. Do the steps in
order; each one names its owner and its check.

Context (why these steps exist): a real test-mode Razorpay payment was captured
on 2026-08-03 and stranded PENDING because (a) `PAYMENT_RETURN_URL` fell back to
its dev default and sent the payer to `localhost:5173`, (b) the return page
polled forever on the resulting 401, and (c) the reconcile cron had 403'd every
cycle since deploy — `PAYMENT_RECONCILE_TOKEN` was never set. (a) and (c) are
env facts the pipeline cannot fix; (b) is fixed in code on this branch.

## Phase 0 — before the push (owner: whoever holds EC2 SSH)

1. **Verify/patch the EC2 `backend/.env`** (the deploy never touches it):
   - `PAYMENT_RETURN_URL=https://<customer-facing origin>/payments/return`
     (today that origin is `http://<EC2-IP>:8090` until TLS lands — see
     DEPLOY.md §TLS. It must NEVER contain localhost/127.0.0.1.)
   - `PAYMENT_RECONCILE_TOKEN=<mint fresh: openssl rand -hex 32>` — do NOT
     reuse a token that appeared in any chat/log/local .env.
   - Sanity: `FRONTEND_URL` should also be the customer-facing origin.
   - Check: after the deploy (phase 2), backend boot log contains **zero**
     `PAYMENT CONFIG:` CRITICAL lines. Code refuses gateway checkout while
     the return URL is dev-shaped, so a miss here is loud, not silent.
2. **Remote-DB schema pre-check** (deploys never migrate — DEPLOY.md §6):
   - `SHOW COLUMNS FROM categories LIKE 'parent_id'` — if absent, apply
     `backend/scripts/sql/2026-07-28_categories_parent_id.sql` first.
   - This branch itself needs **no** schema change (verified in review: no new
     tables/columns).
3. **Confirm the sprint-gate position.** The approved launch plan schedules a
   day-7 deploy of written work regardless of gate outcome, but the gate
   result (≥3/5 pre-payment conversions) decides what happens AFTER this
   deploy. Know which side you're on before pushing.

## Phase 1 — the push (owner: repo committer)

4. Push order: `git push origin vinay`, then fast-forward/merge `vinay` into
   `production` and push. Only the `production` push triggers the pipeline.
5. **Watch the first run end-to-end.** This branch bumps the workflow's action
   versions (checkout@v5, setup-node@v5, setup-python@v6) and the workflow
   only ever executes on production pushes — this deploy is its first run.

## Phase 2 — immediately after the deploy (owner: EC2 SSH holder)

6. The pipeline now force-recreates **all** app services (fixed in this
   branch): backend, frontend, payment-reconcile-cron, loyalty-cron, both
   analytics workers. Check: `docker compose ps` shows fresh creation times
   for all six; `docker logs <reconcile-cron>` shows `200` (not 403) on its
   next cycle (≤10 min).
7. Backend boot log: no `PAYMENT CONFIG:` CRITICAL lines (see step 1).
8. If the payment_methods razorpay row was saved before the strip fix ever
   ran against this DB, run the idempotent repairer once:
   `docker cp backend/scripts/repair_payment_method_whitespace.py <backend>:/tmp/ &&
   docker exec <backend> python /tmp/repair_payment_method_whitespace.py`
   (2026-08-03: already executed against the shared DB — expect "nothing to
   repair".)

## Phase 3 — payment go-live (owner: founder, Razorpay dashboard access)

9. **Register the webhook** (until then settlement rests on the browser's
   verify call + the 10-min reconcile cron): Razorpay dashboard → Webhooks →
   add `https://<api origin>/api/v1/payments/webhook/razorpay`, subscribe
   **`payment.captured` and `order.paid`** (Standard Checkout — these are the
   events that settle new orders) plus `payment_link.paid` while any legacy
   payment link can still be paid. Set a webhook secret and paste it into
   Admin → Settings → Payments → Razorpay → webhook secret.
10. **Live keys, when going live for real money:** generate LIVE key pair in
    the dashboard (needs completed KYC/activation), paste into Admin →
    Payments (the backend now strips stray whitespace on save), and re-run a
    ₹-small end-to-end order. Test keys (`rzp_test_…`) decline real cards.
10b. **Auto-capture setting (Standard Checkout):** dashboard → Account &
    Settings → Payment Capture → auto-capture ON. Authorized-but-uncaptured
    payments are AUTO-REFUNDED by Razorpay after a timeout, and the backend
    deliberately refuses to mark an order PAID until the payment is captured
    — with auto-capture off, every payment would bounce back to customers.
11. **Smoke test** (mirrors the launch plan's day-0 step): one real prepaid
    order end-to-end — pay, land on `/payments/return`, see "Payment
    Successful", order PAID in admin. Then one COD order; cancel it.

## Phase 4 — before pointing traffic at the store (owner: founder)

12. **Catalogue gate:** the mega menu and SEO metadata now funnel visitors to
    live PDPs. Until prices/stock are set (CATALOGUE-TODO.md), every PDP is
    ₹0/out-of-stock — structured data deliberately emits no Offer at price 0,
    but a human visitor still sees an unbuyable product. Fill price + stock
    for at least the hero SKU before sharing links.
13. site_pages row is currently EMPTY on the shared DB, so the new About and
    Contact defaults (no fabricated stats, real hours block) take effect on
    deploy with no data step. Note for later: the first admin save of Pages
    creates the row, and from then on DB content overrides code defaults —
    that's by design, just remember it when a future default edit "doesn't
    show up".

## Rollback

The pipeline keeps no old containers. Rollback = point `production` at the
prior commit and push (full pipeline re-run, ~10 min), or on the host:
`docker compose pull` a previous `:<git-sha>` tag from GHCR and
`docker compose up -d --force-recreate <services>`. The env file is never
touched by either path.
