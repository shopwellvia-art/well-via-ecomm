# Analytics worker: the rollup and delivery processes

The analytics subsystem ships **two long-running worker processes** — one image,
two roles — plus a small job-control API that only ever *enqueues* and *reports*.
This document covers what they are, how to run them, how to trigger work by
hand, and the two alerts worth paging on.

Everything here is inert until `ANALYTICS_ROLLUPS_ENABLED=true`. That is the
primary rollback: turn the flag off and the worker exits cleanly on its next
start, no deploy required.

---

## Why a dedicated process, not an in-process scheduler

The payment reconcile job is a plain HTTP endpoint driven by an external cron
(see `payment-reconcile-cron.md`). Analytics deliberately is **not**, and the
difference matters:

1. **Uvicorn runs 4 workers.** An in-process scheduler (APScheduler and friends)
   lives inside each one, so every tick fires **four times**, from four
   processes, against the same buckets. The recompute queue's lease would absorb
   some of that, but three of the four workers would spend their tick colliding
   on claims instead of serving requests — and the fourth is still doing heavy
   aggregation on a thread the request path needs.
2. **Aggregation is not request-shaped.** A bucket rebuild is a
   `DELETE`-then-`INSERT` over a day of facts. It takes seconds to minutes, not
   milliseconds. Anything that long inside a request worker is a request worker
   that is not serving requests, and under `proxy_read_timeout` it eventually
   becomes a killed run that wrote half a bucket.
3. **The work is continuous, not periodic.** The recompute queue is fed by
   whatever changed — a refund at 02:00, a settlement batch at 06:00, a cost-rule
   edit at any time. A worker that loops every 15 seconds and drains what is
   there fits that shape; a cron that fires on a schedule does not.
4. **A crash must be recoverable, not silent.** Claims carry a lease. A worker
   that dies mid-bucket has its rows reclaimed by the next sweep. That
   guarantee needs a process whose identity (`worker_id`) is stable and
   attributable — a request worker handling an arbitrary HTTP call is neither.

The job-control API still exists, because an operator needs a way to say "these
numbers are wrong, rebuild them". It just never does the rebuilding itself.

---

## The two roles

Selected by `ANALYTICS_WORKER_ROLE`. One image, one entrypoint, two behaviours.

### `rollup` (default)

Each tick, in this order:

1. **Reclaim expired leases** — rows still `claimed` past `claim_expires_at` go
   back to `pending`. This is what makes a worker crash a non-event. Without it
   a killed process leaves its buckets claimed *forever*: no error, no retry,
   just a bucket that quietly stops updating.
2. **Drain the recompute queue** — claim a batch of `pending` rows by priority
   then bucket date, recompute each, mark `done`. Failures increment `attempts`
   and record `last_error`; past `ANALYTICS_WORKER_MAX_ATTEMPTS` the row goes
   `failed` and stops burning worker time.
3. **Run the due scheduled window** — every
   `ANALYTICS_WORKER_SCHEDULE_INTERVAL_SEC`, recompute the trailing
   `ANALYTICS_WORKER_SCHEDULE_LOOKBACK_DAYS` store-local days for every
   registered job. Today's bucket is dirty by definition (it is still accruing)
   and yesterday's may have settled late, so neither is reliably in the queue.
   These run **directly**, not through the queue: they are due on a clock, not
   dirty because something changed, and pushing routine work through the
   dirty-bucket queue would make "what do we currently believe is stale?" — the
   one question that table answers — unreadable under a constant drip.
4. **Prune** — every `ANALYTICS_WORKER_PRUNE_INTERVAL_SEC`, delete up to
   `ANALYTICS_WORKER_PRUNE_BATCH` `done` queue rows older than
   `ANALYTICS_WORKER_RETENTION_DAYS`. Batched because an unbounded DELETE over a
   year of completed rows holds a table lock for its whole duration and every
   claim queues behind it. `failed` rows are **never** pruned; they are what
   alerting reads, and deleting them would silently close the only report of a
   bucket that will never rebuild itself.
5. **Sleep** `ANALYTICS_WORKER_INTERVAL_SEC` (default 15s).

### `delivery`

Each tick, drains the GA4 outbox and sleeps
`ANALYTICS_WORKER_INTERVAL_SEC` (default 30s for this role).

> **The outbox drain is currently a no-op.** `deliver_pending()` in
> `app/services/analytics/worker.py` returns zero counts and sends nothing. The
> loop, the role wiring, the shutdown handling and the error accounting are all
> real; the Measurement Protocol call is not implemented yet and lands in P8
> alongside `ANALYTICS_TRACKING_ENABLED`. Running the `delivery` role today
> gives you a healthy process that delivers nothing — this is stated plainly
> rather than stubbed to look successful, because an outbox that *reports*
> delivery it did not perform is worse than one that is obviously idle.

---

## Running it

```bash
python -m app.services.analytics.worker
```

That is the entrypoint for both roles; the role comes from the environment.

```bash
# rollup worker, local
ANALYTICS_ROLLUPS_ENABLED=true ANALYTICS_WORKER_ROLE=rollup \
  python -m app.services.analytics.worker

# delivery worker, local
ANALYTICS_ROLLUPS_ENABLED=true ANALYTICS_WORKER_ROLE=delivery \
  python -m app.services.analytics.worker

# one tick and exit — the shape used by a CI smoke test
ANALYTICS_ROLLUPS_ENABLED=true ANALYTICS_WORKER_ONCE=true \
  python -m app.services.analytics.worker
```

In the container stack the two roles are separate services (wired in
`docker-compose.yml` by the lead). Both should carry `restart: always` — and
both are safe under it, because of the next section.

### Refusing to start is not crashing

With `ANALYTICS_ROLLUPS_ENABLED=false` the worker logs one clear line and
**exits 0**:

```
analytics-worker: ANALYTICS_ROLLUPS_ENABLED is false — refusing to start.
Set ANALYTICS_ROLLUPS_ENABLED=true to enable the analytics rollup pipeline.
```

Exit 0, not a raised exception, and specifically not `sys.exit(1)`. Under
`restart: always` a non-zero exit produces a crash loop: the container restarts,
fails, restarts, and fills the logs and the restart-count metric with a
condition that is not an error at all — the flag is off on purpose. Exit 0 stops
cleanly and stays stopped.

### Shutdown

`SIGTERM` (what `docker compose down`, `docker stop` and Kubernetes all send)
and `SIGINT` both trigger a graceful stop:

1. Stop claiming new work immediately.
2. **Finish the bucket in flight** — a bucket is a `DELETE` + `INSERT`; killed
   between the two it leaves the bucket empty and the queue row claimed.
3. Release any leases the process still holds, so the rows are `pending` again
   the instant the next worker looks — rather than invisible until the lease
   expires minutes later.
4. Exit 0.

A second signal during shutdown escalates: the worker stops waiting and exits.
The lease is the backstop if it does — nothing is lost, it just takes
`ANALYTICS_WORKER_LEASE_SEC` for the rows to come back.

Give the container a `stop_grace_period` at least as long as one bucket takes
(30s is a reasonable start), otherwise Docker `SIGKILL`s mid-bucket and the
graceful path never runs.

---

## Environment

| Variable | Default | What it does |
| --- | --- | --- |
| `ANALYTICS_ROLLUPS_ENABLED` | `false` | Master switch for the worker. False → the worker exits 0 at startup. The job-control routes still enqueue (the queue is durable and drains once the worker is enabled); `/admin/health` reports the flag so "nothing is draining" has an obvious answer. |
| `ANALYTICS_WORKER_ROLE` | `rollup` | `rollup` or `delivery`. Anything else exits **2** — unlike the flag above, nobody chose a typo, so a restart loop is the correct visible signal. |
| `ANALYTICS_WORKER_INTERVAL_SEC` | `15` (rollup) / `30` (delivery) | Sleep between ticks. |
| `ANALYTICS_WORKER_BATCH_SIZE` | `25` | Queue rows claimed per tick. |
| `ANALYTICS_WORKER_LEASE_SEC` | `300` | Claim lease. Must exceed the slowest single bucket, or a healthy worker gets reclaimed underneath itself. |
| `ANALYTICS_WORKER_DRAIN_BUDGET_MS` | `(lease − 30s)` | Wall-clock budget for one drain, sized to fit inside the lease it was granted. |
| `ANALYTICS_WORKER_MAX_ATTEMPTS` | `5` | Retries before a queue row goes `failed`. |
| `ANALYTICS_WORKER_SCHEDULE_INTERVAL_SEC` | `300` | How often the trailing window is recomputed. |
| `ANALYTICS_WORKER_SCHEDULE_LOOKBACK_DAYS` | `3` | How many trailing days that window covers. |
| `ANALYTICS_WORKER_PRUNE_INTERVAL_SEC` | `3600` | How often the prune runs. |
| `ANALYTICS_WORKER_PRUNE_BATCH` | `5000` | Max `done` rows deleted per prune. |
| `ANALYTICS_WORKER_RETENTION_DAYS` | `30` | Age at which `done` queue rows are pruned. `failed` rows are never pruned. |
| `ANALYTICS_WORKER_ONCE` | `false` | Run exactly one tick and exit. For smoke tests. |
| `ANALYTICS_WORKER_ID` | `<hostname>-<pid>` | Override the worker identity. Leave unset in normal operation. |
| `ANALYTICS_WORKER_LOG_LEVEL` | `INFO` | Root log level for the process. |
| `ANALYTICS_CRON_TOKEN` | `""` (blank) | Shared secret for the job-control API. **Blank disables machine auth entirely** — the human path still works. |

`ANALYTICS_ROLLUPS_ENABLED` and `ANALYTICS_CRON_TOKEN` are `Settings` fields
(`app/core/config.py`); the `ANALYTICS_WORKER_*` knobs are read from the process
environment directly, because they configure a process rather than the app.

### `worker_id`

Every process derives `<hostname>-<pid>` (truncated to the column's 64 chars)
and writes it to `analytics_recompute_queue.claimed_by` and
`analytics_sync_runs.worker_id`. That is what turns "some rows are stuck
claimed" into "container `analytics-rollup-7f4c9` died at 04:12", which is the
difference between an incident you can investigate and one you can only observe.

---

## Triggering work by hand

All routes live under the analytics admin prefix and are **authenticated two
ways** — copy of the `reconcile_auth` pattern in
`app/api/v1/endpoints/payments.py`:

* `X-Analytics-Token: <ANALYTICS_CRON_TOKEN>`, compared with
  `secrets.compare_digest`. For machines. A blank configured token disables this
  path completely so an unset secret can never be matched by an empty header.
* **or** a signed-in user holding `analytics.jobs.run`. For humans and the admin
  UI.

Anything else is `403`.

### Rebuild a known-bad window

```bash
curl -sS -X POST \
  -H "X-Analytics-Token: $ANALYTICS_CRON_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"job":"order_daily","date_from":"2026-07-01","date_to":"2026-07-15"}' \
  https://yourdomain.com/api/v1/analytics/admin/recompute
```

`202 Accepted`:

```json
{"job":"order_daily","queued":14,"job_ids":[901,902,"…"],
 "date_from":"2026-07-01","date_to":"2026-07-15"}
```

**202, not 200.** Nothing has been computed when this returns — queue rows
exist, and the worker will get to them. A 200 would claim the rebuild had
happened, and an operator who reloads the dashboard on the strength of that
claim sees the old numbers and concludes the endpoint is broken.

Windows are **half-open**: `[date_from, date_to)`. The call above covers
2026-07-01 through 2026-07-14. Same convention as `timebox.py`, and the reason
backfill pages can be chained without double-counting the seam.

### Backfill history, one page at a time

```bash
curl -sS -X POST \
  -H "X-Analytics-Token: $ANALYTICS_CRON_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"job":"order_daily","date_from":"2025-01-01","date_to":"2026-01-01","max_days":30}' \
  https://yourdomain.com/api/v1/analytics/admin/backfill
```

```json
{"job":"order_daily","queued":30,"date_from":"2025-01-01","date_to":"2025-01-31",
 "next_date_from":"2025-01-31","done":false,"requested_date_to":"2026-01-01"}
```

Feed `next_date_from` back as `date_from` and repeat until `done` is `true`.
`max_days` is capped at **60**; asking for more is rejected with a `422` rather
than silently reduced — a truncated backfill leaves a hole in the chart that
nobody can trace back to the request that caused it.

Backfill enqueues at a *higher* priority number (lower urgency) than recompute
by default, so a year of history can never starve today's buckets.

### The bounded inline run (debug only)

```bash
curl -sS -X POST \
  -H "X-Analytics-Token: $ANALYTICS_CRON_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"job":"order_daily","date_from":"2026-07-27","date_to":"2026-07-28","budget_ms":20000}' \
  'https://yourdomain.com/api/v1/analytics/admin/aggregate?inline=true'
```

Returns **200** — this one actually ran. It is bounded twice: `budget_ms` ≤
55 000 (under nginx's 60s `proxy_read_timeout`) and `max_days` ≤ 14. When either
bound is hit the run stops at the next bucket boundary and reports
`next_date_from`; it never abandons a half-written bucket.

**The worker is the normal path.** Inline exists for two situations: proving the
pipeline works before the worker container is deployed, and reproducing a single
bad bucket in front of a log tail. Using it for routine aggregation puts heavy
work back in a request worker, which is the entire thing this design avoids.

Without `?inline=true` the same route enqueues and returns `202`.

### Check on a run

```bash
curl -sS -H "X-Analytics-Token: $ANALYTICS_CRON_TOKEN" \
  https://yourdomain.com/api/v1/analytics/admin/jobs/1234
```

One `analytics_sync_runs` row. Unknown id → `404`.

---

## Reading `/admin/health`

```bash
curl -sS -H "X-Analytics-Token: $ANALYTICS_CRON_TOKEN" \
  https://yourdomain.com/api/v1/analytics/admin/health
```

```json
{
  "generated_at": "2026-07-28T09:15:04Z",
  "rollups_enabled": true,
  "tz_generation": 1,
  "queue_depth": {"pending": 4, "claimed": 1, "done": 812, "failed": 0, "total": 817},
  "oldest_pending_bucket": "2026-07-28",
  "stale_claims": 0,
  "jobs": [
    {"job": "order_daily", "watermark_date": "2026-07-27",
     "last_run_status": "success", "consecutive_failures": 0,
     "pending_buckets": 2, "failed_buckets": 0,
     "oldest_pending_bucket": "2026-07-28", "last_error": null}
  ],
  "last_error": null
}
```

What each field is actually for:

* **`watermark_date`** — "every bucket through this day is computed". A *data*
  watermark, not a clock. It is what the dashboard prints beside a revenue
  figure, because "the job ran at 04:00" and "the numbers include everything
  through the 27th" are different claims and only the second is safe to show.
* **`queue_depth`** — `pending` should oscillate near zero and `claimed` should
  be small and moving. `pending` climbing monotonically means the worker is down
  or wedged. `failed` above zero is always worth a look; those rows never retry
  on their own.
* **`oldest_pending_bucket`** — the sharpest liveness signal in the payload. If
  it does not move between two polls a few minutes apart, nothing is draining.
* **`stale_claims`** — rows claimed past their lease. Briefly non-zero after a
  worker restart is normal; persistently non-zero means the reclaim sweep is not
  running, and those buckets are stuck.
* **`consecutive_failures`** — failures since the last success. `skipped_locked`
  runs are excluded; a busy lock is not an outage and must not page anyone.
* **`rollups_enabled`** — the first thing to check when everything looks idle
  and healthy at the same time.

A healthy steady state: `pending` in the low single digits, `failed` at 0,
`stale_claims` at 0, and every job's `watermark_date` at yesterday or today.

---

## The two alerts that matter

Everything else is noise next to these. Both read `analytics_sync_runs` and the
recompute queue, and both catch failures that are **invisible** in ordinary
signals — the process is up, the logs are clean, the HTTP checks are green.

### 1. Watermark not advancing (`AlertRuleKey.TRACKING_FAILURE`)

**Condition:** a job's `watermark_date` is unchanged across runs while those
runs keep reporting `success` — or `oldest_pending_bucket` does not move for
more than ~15 minutes.

**Why it is the important one:** a pipeline that runs and does nothing looks
*exactly* like a healthy pipeline. Green run log, no errors, no restarts, and a
dashboard serving last Tuesday's revenue as though it were today's. Every
business alert (`SALES_DROP`, `CONVERSION_DROP`, …) is silent in this state,
because from their point of view nothing changed. Causes: an empty source
window, a stuck lock, a queue never handing out work, a `tz_generation` mismatch
filtering everything out.

**Suggested threshold:** page when a job's `watermark_date` is more than 2 days
behind the current store-local day, or when `oldest_pending_bucket` is unchanged
across three consecutive polls 5 minutes apart.

### 2. Repeated failures (pipeline health)

**Condition:** two or more consecutive runs of the same job terminate `failed`,
or `queue_depth.failed` is non-zero and rising.

**Why two, not one:** a single failure is a lock contention, a deploy restart, a
transient DB blip — retrying costs nothing and it clears. Two in a row is a
broken pipeline and needs a human. Runs with status `skipped_locked` **must be
excluded from the streak**: another worker held the lock, this tick did nothing,
and that is normal operation. Counting them turns a busy queue into a false
page, and a few of those teach everyone to ignore the real one.

**Where to look first:** `jobs[].last_error` in `/admin/health`, then the run
row via `/admin/jobs/{run_id}` for `worker_id` — which points straight at the
container log that has the traceback.

### Not alerts

* `queue_depth.pending` spiking after a cost-rule edit or a timezone rebuild —
  that is the queue doing its job. Alert on it *not draining*, not on its depth.
* `skipped_locked` runs — see above.
* The `delivery` worker reporting zero deliveries — the drain is a documented
  no-op until P8. It will report zero forever until then, and that is correct.
