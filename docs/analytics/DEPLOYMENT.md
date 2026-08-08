# Analytics v2 — deployment and rollback

Operator runbook for the analytics subsystem. Read this **before** deploying the
branch, because one step must happen a deploy earlier than the rest.

This covers phases P0–P5. It is updated as later phases land; anything not
listed here is not yet deployable.

---

## The one-line summary

Everything ships **inert**. Three feature flags default to `false`, so merging
and deploying this branch changes nothing an admin or a customer can see. The
schema is purely additive and no existing table is touched. Turning a flag off
is the rollback, and it needs no deploy.

---

## What this adds

| Thing | Detail |
|---|---|
| Database tables | **23 new**, all additive: 4 immutable facts, 12 rollups, 7 control |
| Containers | **2**, both running the existing backend image with a different command |
| Env vars | 4 — three flags plus one shared secret |
| API routes | 5 job-control routes under `/api/v1/analytics/admin/*` |
| Existing behaviour changed | **None.** `/dashboard/overview`, `/analytics/sales` and `/analytics/profit` are untouched. |

---

## Step 1 — apply the schema, ONE DEPLOY EARLY

The shared remote MySQL is on the `conpay001` migration lineage, which this repo
does not contain. **Never run `alembic upgrade` against it** (`DEPLOY.md` §6).
The schema ships as reviewed SQL instead:

```
backend/scripts/sql/2026-07-28_analytics_v2_schema.sql
```

That file was **generated from** Alembic revision `d7f3a9c2e814` with
`alembic upgrade c4a1d0e7b93f:d7f3a9c2e814 --sql`, so the two cannot silently
disagree. Verified identical: 355 columns and 164 index rows produced by both
paths, byte for byte.

```bash
# 1. Back up first. This is not optional.
bash backend/scripts/backup_db.sh

# 2. Check nothing is already there
mysql -h <host> -u <user> -p <db> -e "
  SELECT table_name FROM information_schema.tables
   WHERE table_schema = DATABASE()
     AND (table_name LIKE 'analytics%' OR table_name LIKE 'agg\\_%'
          OR table_name IN ('cart_events','inventory_movements'));"

# 3. Apply in a reviewed window
mysql -h <host> -u <user> -p <db> < backend/scripts/sql/2026-07-28_analytics_v2_schema.sql
```

**Why one deploy early:** the tables are additive and unread, so the currently
deployed backend tolerates them completely. Doing it in the same deploy as the
code means a failed SQL step leaves code running against a schema that isn't
there. Additive-first is the same pattern the `categories.parent_id` change used.

The file deliberately does **not** stamp `alembic_version` — the remote DB's
migration state belongs to a lineage this repo doesn't own, and writing a
revision id from our chain into it would corrupt that state.

---

## Step 2 — environment

Add to `backend/.env` **on the host**. All three flags stay `false` for now.

```bash
ANALYTICS_ROLLUPS_ENABLED=false
ANALYTICS_V2_ENABLED=false
ANALYTICS_TRACKING_ENABLED=false

# Machine auth for manual/external job triggers. The workers do NOT need this —
# they call the runner in-process. Generate: openssl rand -hex 32
# Blank disables machine auth; a signed-in admin with analytics.jobs.run still works.
ANALYTICS_CRON_TOKEN=
```

The compose file reads `./backend/.env` for the workers too, so setting it once
keeps the API and the workers in sync automatically.

---

## Step 3 — deploy the code

Normal pipeline: merge to `production`, CI builds and pushes, EC2 pulls.

`docker-compose.yml` gains two services, so **the updated compose file must reach
the host** — the deploy step passes `--remove-orphans`, and a stale compose file
on the host would remove containers it doesn't know about.

Both workers exit 0 immediately while `ANALYTICS_ROLLUPS_ENABLED` is false, so
after this deploy you should see them start and stop cleanly. That is correct,
not a failure.

**Verify nothing changed:**

```bash
docker compose ps                      # backend, frontend, redis healthy
curl -fsS http://localhost:8090/api/v1/dashboard/overview   # unchanged
docker compose logs analytics-rollup-worker --tail 5        # "refusing to start"
```

---

## Step 4 — enable rollups (write-only, nothing reads yet)

```bash
ANALYTICS_ROLLUPS_ENABLED=true
docker compose up -d analytics-rollup-worker analytics-delivery-worker
```

This is the safe first switch: the workers write to `agg_*` tables that no
endpoint reads while `ANALYTICS_V2_ENABLED` is false. Let them run and warm up.

**Watch these, in order of importance:**

```bash
# 1. Is the watermark advancing? A stalled watermark is the primary failure.
curl -fsS -H "X-Analytics-Token: $ANALYTICS_CRON_TOKEN" \
  http://localhost:8090/api/v1/analytics/admin/health | jq

# 2. Queue depth and the oldest pending bucket
#    (oldest_pending_age_seconds is computed on the DB clock, so clock drift
#     between hosts is not misreported as backlog)

# 3. Recent runs
mysql ... -e "SELECT job, \`trigger\`, status, window_from, window_to,
                     days_processed, rows_written, watermark_date, duration_ms
                FROM analytics_sync_runs ORDER BY id DESC LIMIT 20;"
```

### Backfill

Paged on purpose — an unbounded rebuild is an outage on a live store.

```bash
curl -fsS -X POST -H "X-Analytics-Token: $ANALYTICS_CRON_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"job":"order_daily","date_from":"2026-01-01","date_to":"2026-02-01","max_days":30}' \
  http://localhost:8090/api/v1/analytics/admin/backfill
```

Returns `next_date_from`; loop until `done: true`. It **rejects** a range beyond
`max_days` rather than truncating, because a silently shortened backfill leaves a
gap in a chart that nobody can trace.

Run backfills off-peak. The aggregation queries are the heaviest thing this
subsystem does to a database that is also serving checkout.

---

## Step 5 — enable the v2 API

```bash
ANALYTICS_V2_ENABLED=true
docker compose up -d backend
```

The legacy Sales and Profit pages **stay primary**. Shadow-mode reconciliation
runs against them; see `docs/analytics/RECONCILIATION.md` when P11 lands.

---

## Rollback

In order of preference.

**1. Turn the flag off.** No deploy, no image pull, immediate.

```bash
# edit backend/.env -> ANALYTICS_ROLLUPS_ENABLED=false
docker compose up -d analytics-rollup-worker analytics-delivery-worker backend
```

**2. Stop the workers.** The queue persists; work resumes on restart.

```bash
docker compose stop analytics-rollup-worker analytics-delivery-worker
```

**3. Roll the image back** (`DEPLOY.md` §5):

```bash
IMAGE_TAG=<previous-sha> docker compose pull backend frontend
IMAGE_TAG=<previous-sha> docker compose up -d
```

**4. The schema.** Additive and unread by any pre-existing code, so **leaving it
in place is safe and is the recommended rollback**. Every rollup read tolerates a
missing table by reporting `NO_ROLLUP_YET` rather than raising, so even a partial
schema degrades to an unavailable panel instead of a 500. If you must drop it,
there are no foreign keys by design, so order does not matter — the statement is
in the header of the SQL file.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Worker logs "refusing to start", exits 0 | `ANALYTICS_ROLLUPS_ENABLED` is false | Intended. Set it true when ready. |
| Worker exits 2 immediately | Bad `ANALYTICS_WORKER_ROLE` | Must be `rollup` or `delivery`. |
| Watermark not advancing | Worker down, or every bucket failing | Check `analytics_sync_runs` for `status='failed'` and `last_error`. |
| Queue depth growing | Drain slower than enqueue | Check `duration_ms` per run; consider a wider `budget_ms`. |
| Rows appear one day "late" | Almost always a timezone question | Buckets are **store-local** (`store.timezone`, default `Asia/Kolkata`), not UTC. An order at 05:00 IST is 23:30 UTC the previous day. This is correct. |
| A dashboard number differs from the legacy page | Expected during shadow mode | Differences are documented and must be explained before legacy is retired. |
| Manual trigger times out at 60s | nginx `proxy_read_timeout` | Use the queued route, not `?inline=true`. Inline is capped at 14 days / 55s for exactly this reason. |

---

## Production remediation — GA4 purchases not delivering (`ga4_server_delivery_without_secret`)

The production deployment is currently in this state: `analytics.ga4_purchase_delivery`
is a **server mode** while **no Measurement Protocol API secret is saved**. In that
combination every purchase event is written to the outbox and can never be delivered —
the delivery worker idles with reason `no_api_secret` — so **GA4 shows zero ecommerce
revenue while the outbox grows silently**. Tracking Health reports it as the warning
code above, and the Analytics Integrations page banners it.

The save path now **refuses to create or keep this state** (a 422 with the same code:
choosing a server delivery mode with no secret, or clearing the secret while a server
mode is active, is rejected). But a guard cannot repair a deployment that is already
broken — only the operator holds the secret. One of the following, via
**Admin → Analytics → Integrations → Google Analytics 4**:

1. **Enter the Measurement Protocol API secret** (preferred — server-side delivery is
   the authoritative mode). Get it from **GA4 Admin → Data Streams → choose the web
   stream → Measurement Protocol API secrets** (create one if none exists), paste it
   into *Measurement Protocol API secret*, and save. It may be saved in the same
   request as the delivery mode — no ordering is forced. Then press *Test connection*:
   it validates the measurement-id/secret pair against Google's debug endpoint without
   writing an event.
2. **Or switch “Purchase events are sent from” to browser-only** — but only once the
   GTM tag is verified to actually fire `purchase` (GTM Preview against the live
   storefront), otherwise revenue is lost from both sides instead of one.

Until one of these is done, **purchases do not reach GA4**. The queued `pending` rows
are a preserved debt, not a loss: once the secret is saved, the delivery worker drains
them on its next ticks. `FAILED` rows (retries exhausted) stay lost unless replayed —
check the outbox counts on the Tracking Health panel after remediation.

---

## Changing the reporting timezone

**Do not edit `store.timezone` directly once rollups exist.** Every bucketed row
records the `tz_generation` that produced it, and a query spanning two
generations is refused rather than silently summed — because mixing buckets built
under two different day boundaries produces a number that is wrong in a way
nobody can detect afterwards.

The supported procedure (P4.7): open a new generation as `rebuilding`, enqueue a
full recompute under it, keep serving the old generation meanwhile, flip the
active generation only when the rebuild finishes, retire the old one after a hold
period.

---

## What is NOT yet deployable

Listed so nobody assumes otherwise:

- **GA4 / GTM / Clarity** — `ANALYTICS_TRACKING_ENABLED` exists but the delivery
  worker's `deliver_pending()` is an explicit no-op returning
  `implemented: False`. It does **not** mark outbox rows delivered. Lands in P8,
  and needs the CSP change in `frontend/nginx.conf` (rolled out Report-Only first).
- **9 of 12 rollup jobs** — `order_daily`, `order_hourly` and `product_daily` are
  implemented. The rest are deliberately **not stubbed**: a stub would report
  success, advance a watermark, and make an unbuilt table indistinguishable from
  a quiet one.
- **The admin UI** — no React routes yet (P6).
- **Exports, reconciliation, alerts** — P5/P11.
