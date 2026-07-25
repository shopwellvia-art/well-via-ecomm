# Payment reconciliation: scheduled job

The payment engine ships **one** scheduled job — the pending-order reconciliation
sweep — and it is exposed as a regular HTTP endpoint rather than an in-process
scheduler. Reasons:

1. The app runs Uvicorn workers. An in-process scheduler (APScheduler etc.)
   would fire once per worker, multiplying gateway polling calls and creating
   race conditions on the same stale orders.
2. Treating the job as a plain HTTP call means any external scheduler works:
   docker-compose cron sidecar, Kubernetes CronJob, GitHub Actions, AWS
   EventBridge, your laptop's `crontab`.
3. The endpoint is already idempotent — duplicate runs are harmless. If two
   scheduler ticks overlap, the second call simply finds nothing new to settle.

## The endpoint

```
POST /api/v1/payments/admin/reconcile-pending
Authorization: Bearer <token-with-payments.manage>
Content-Type: application/json   (optional body)

{
  "older_than_minutes": 30,   // default 30, bounds 1–1440
  "limit": 100                // default 100, bounds 1–500
}
```

Returns:

```json
{
  "checked": 12,
  "settled_paid": 5,
  "cancelled": 3,
  "still_pending": 4,
  "errors": 0
}
```

All fields are counts for the current run. `errors` counts orders where the
gateway poll itself failed (network timeout, gateway 5xx, etc.); those orders
remain PENDING and will be retried on the next tick.

## Wiring it from docker-compose

Payment settlement is time-sensitive: a customer whose payment succeeded at the
gateway but whose webhook was dropped should not wait hours to see their order
confirmed. Run the sweep every **10 minutes**. This is already wired up as the
`payment-reconcile-cron` service in `docker-compose.yml` (the production stack);
the sketch below shows the shape if you need to adapt it:

```yaml
payment-reconcile-cron:
  image: alpine:3.20
  restart: always
  depends_on:
    - backend
  environment:
    BACKEND_URL: http://backend:8000
    # Use a long-lived token issued to a service account scoped to
    # `payments.manage` only. Rotate periodically.
    PAYMENT_RECONCILE_TOKEN: ${PAYMENT_RECONCILE_TOKEN}
  command: >
    sh -c "apk add --no-cache curl &&
           while true; do
             sleep 600
             curl -sS -X POST \
               -H 'Authorization: Bearer '\"$$PAYMENT_RECONCILE_TOKEN\" \
               -H 'Content-Type: application/json' \
               -d '{\"older_than_minutes\":30,\"limit\":100}' \
               $$BACKEND_URL/api/v1/payments/admin/reconcile-pending
           done"
```

The first tick fires 10 minutes after the sidecar starts (sleep-first loop),
so a deploy that immediately starts the container does not hammer the backend
before it is ready. Adjust `older_than_minutes` if your gateway SLA differs —
PhonePe and Razorpay typically confirm within 2 minutes; a 30-minute window
provides a generous buffer before cancelling.

## Wiring it from host cron

Simpler approach if you don't want a sidecar:

```cron
# /etc/cron.d/payment-reconcile
*/10 * * * * www-data curl -sS -X POST \
  -H "Authorization: Bearer ${PAYMENT_RECONCILE_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"older_than_minutes":30,"limit":100}' \
  https://yourdomain.com/api/v1/payments/admin/reconcile-pending >> /var/log/payment-reconcile.log 2>&1
```

## Manual trigger (admin panel)

Admins with `payments.manage` can POST to the endpoint at any time — useful
after a gateway outage to immediately settle the backlog without waiting for the
next scheduled tick. No body is required; the defaults kick in.

## What the sweep does

1. Queries all orders in `PENDING` status whose `gateway_code` is non-null and
   whose `updated_at` is older than `older_than_minutes`.
2. For each order, calls the relevant gateway adapter's status-check API using
   the `merchant_transaction_id` stored on the order.
3. Maps the gateway response to an internal status:
   - Gateway says **paid/success** → order moved to `PAID`, fulfillment hooks
     fire, the customer receives their confirmation.
   - Gateway says **failed/cancelled/expired** → order moved to `CANCELLED`,
     reserved stock is released, the customer is notified.
   - Gateway says **still pending** or poll fails → order is left in `PENDING`
     and will be retried on the next tick.
4. Returns aggregate counts for the run; each settled order is also logged at
   `INFO` with its `order_id` and final status.

Because each status transition is guarded by a database row-level check
(`status == PENDING`) before writing, concurrent runs cannot double-settle the
same order.

## Monitoring

The endpoint logs at `INFO`. Point your log aggregator at `payment_service` and
set two alerts:

- **`errors` stays > 0 across multiple consecutive runs** — indicates a gateway
  adapter or network problem; investigate before the error orders age past any
  refund window.
- **`settled_paid` stays 0 while the number of PENDING orders in the database
  keeps growing** — suggests the cron itself is not firing, the bearer token has
  expired, or all gateway polls are silently failing. Check the sidecar logs and
  verify `PAYMENT_RECONCILE_TOKEN` is set.

A healthy deployment will show `errors: 0` and `still_pending` shrinking to 0
within one or two ticks after any gateway hiccup.
