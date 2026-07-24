# Load testing

k6 load tests for the storefront read path, run against an **isolated local
stack** (throwaway MySQL + Redis + the backend image) so nothing ever touches
the shared production database.

> **Never point these at production.** The write flows would corrupt real order
> data and the read flows would exhaust the shared MySQL connection limit and
> take down the co-tenant apps on that host. The compose file wires the backend
> to a local `lt-mysql` container on purpose.

## Prerequisites

- Docker running, `k6` installed (`brew install k6`).
- The backend image built: `docker build -t well-via-ecomm-backend:latest ../backend`.

## Run

```sh
cd loadtest
export SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_hex(32))")

docker compose -f docker-compose.loadtest.yml up -d lt-mysql lt-redis
docker compose -f docker-compose.loadtest.yml run --rm --no-deps lt-backend alembic upgrade head
docker compose -f docker-compose.loadtest.yml run --rm --no-deps lt-backend python /loadtest/bootstrap_lt.py
docker compose -f docker-compose.loadtest.yml up -d lt-backend

k6 run -e BASE_URL=http://localhost:8001 browse.js    # steady browse, ramps to 200 VUs
k6 run -e BASE_URL=http://localhost:8001 stress.js    # arrival-rate ramp to find the knee

docker compose -f docker-compose.loadtest.yml down -v
```

## Watching the logs live (observability dashboard)

The admin Observability page on the main app (http://localhost:5174) reads from
the **main** backend and its database — it will NOT show load-test traffic,
because k6 hits the isolated `lt-backend` on :8001 with its own throwaway DB.

To watch the load test's request logs / slow queries live, bring up the lt
dashboard (same frontend image, proxied to `lt-backend`):

```sh
docker compose -f docker-compose.loadtest.yml up -d lt-frontend
```

Then open http://localhost:5175/admin/observability and sign in with the
bootstrap admin (`admin@lt.local` / `lt-admin-pass`, overridable via
`LT_ADMIN_PASSWORD`). The page auto-refreshes every 60s; switch the period to
`1h` while a test is running.

## Scripts

- **`browse.js`** — realistic browse mix (catalog list, product detail,
  bestsellers, categories) with think time. Ramps 5→200 concurrent VUs.
  Thresholds: p95 < 800ms, errors < 2%.
- **`stress.js`** — open-model arrival-rate ramp (no think time) that pushes
  request rate until latency explodes and errors appear, to locate the ceiling.
- **`bootstrap_lt.py`** — creates the schema and seeds a small catalog into the
  throwaway DB (run once after `alembic upgrade head`).

## Interpreting results

The local DB is ~sub-millisecond; **production's MySQL is remote over WAN**, so
each request holds a pooled connection longer and the real ceiling is *lower*
than a local run. Watch two limits: `Threads_connected` on MySQL (caps at
`workers × (pool_size + max_overflow)` = 4 × 30 = 120) and backend CPU. When
either saturates, `dropped_iterations` climb and p99 latency runs to the timeout.

Baseline captured 2026-07-16 (4 workers, local DB, laptop): ~90 req/s sustained
on the read path, knee around 300 req/s, MySQL connections pegged at 121.
