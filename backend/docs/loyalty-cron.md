# Loyalty: scheduled jobs

The loyalty engine ships **one** scheduled job — the points-expiry sweep — and
it's exposed as a regular HTTP endpoint rather than an in-process scheduler.
Reasons:

1. The app runs Uvicorn workers. An in-process scheduler (APScheduler etc.)
   would fire once per worker, multiplying the work.
2. Treating the job as a plain HTTP call means any external scheduler works:
   docker-compose cron sidecar, Kubernetes CronJob, GitHub Actions, AWS
   EventBridge, your laptop's `crontab`.
3. The endpoint is already idempotent — duplicate runs are harmless.

## The endpoint

```
POST /api/v1/loyalty/admin/expire-points
Authorization: Bearer <token-with-loyalty.configure>
```

Returns:

```json
{ "users_processed": 12, "rows_expired": 30, "points_expired": 4350 }
```

## Wiring it from docker-compose

Add a tiny sidecar that calls the endpoint at 3 AM daily. Drop this into
`docker-compose.yml` (the production stack), alongside the existing
`payment-reconcile-cron` service:

```yaml
loyalty-cron:
  image: alpine:3.20
  restart: unless-stopped
  depends_on:
    - backend
  environment:
    BACKEND_URL: http://backend:8000
    # Use a long-lived token issued to a service account with
    # `loyalty.configure` only. Rotate periodically.
    LOYALTY_TOKEN: ${LOYALTY_CRON_TOKEN}
  command: >
    sh -c "apk add --no-cache curl &&
           while true; do
             # Sleep until 3:00 AM UTC.
             SECS=$$(( ($(date -u +%s -d 'tomorrow 03:00') - $(date -u +%s)) ))
             sleep $$SECS
             curl -sS -X POST \
               -H \"Authorization: Bearer $$LOYALTY_TOKEN\" \
               $$BACKEND_URL/api/v1/loyalty/admin/expire-points
           done"
```

## Wiring it from host cron

Simpler approach if you don't want a sidecar:

```cron
# /etc/cron.d/loyalty-expiry
0 3 * * * www-data curl -sS -X POST \
  -H "Authorization: Bearer ${LOYALTY_CRON_TOKEN}" \
  https://yourdomain.com/api/v1/loyalty/admin/expire-points >> /var/log/loyalty-expiry.log 2>&1
```

## Manual trigger (admin UI)

Admins with `loyalty.configure` can also run the sweep on demand from
**/admin/loyalty → Maintenance tab → Run expiry sweep now**. This is the same
endpoint — useful for first-time runs, support tickets, and verification.

## What the sweep does

1. Finds every earn row whose `expires_at` has passed and which is still
   partially unconsumed.
2. Walks each affected user's ledger oldest-first (FIFO) to compute how much of
   each expired earn the user has already spent.
3. Writes one `EXPIRY` ledger row per unconsumed earn, with `ref_type='ledger'`
   and `ref_id=<earn row id>`. The `(reason, ref_type, ref_id)` unique index
   means re-running the job that same day is a no-op.

If a user later refunds an order tied to an already-expired earn, the refund
reversal will push their balance negative — that's intentional and matches
real-world ledger semantics.

## Monitoring

The endpoint logs at `INFO`. In a real deploy point your log aggregator at
`loyalty_service` and alert if `users_processed` stays at 0 for >36 hours
(suggests the cron is broken).
