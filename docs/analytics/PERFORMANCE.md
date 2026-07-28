# Analytics — measured performance

**Measured on 2026-07-28.** Every number in this document was produced by
`backend/scripts/analytics_perf_report.py` against the dataset and hardware
described in §2. Nothing here is an estimate, a target restated as a result, or
a figure carried over from a design note.

The brief this replaces claimed that "cached dashboard responses return within
2s and uncached aggregates within 5s". Nobody had measured either. Both of those
turn out to be true with room to spare — and while checking them, four things
that nobody had claimed anything about turned out to be broken:

| | |
|---|---|
| **The read path is fast and comfortably inside target.** | Warm p95 **5.4 ms**, cold p95 **771 ms** across all 51 LIVE/PARTIAL views. |
| **A 30-day backfill takes 11 minutes against a 45 s budget.** | 659 s measured. 97% of it is one job. |
| **The admin "recompute inline" button 504s.** | Measured **65.9 s** at the endpoint's own maximum budget, against nginx's 60 s ceiling. |
| **CSV export cannot produce more than 200 rows.** | The 50 000-row cap and the 45 s build budget in `export.py` are unreachable by construction. |
| **16 of 30 exportable views export nothing at all.** | They resolve, return no table, and the export 404s. |

---

## 1. Verdict against the stated targets

| Target | Measured | Met? |
|---|---|---|
| Cached (warm) dashboard response < **2 s** | p50 3.9 ms · p95 **5.4 ms** · max 7.3 ms, over 51 views | **Yes**, by ~370x |
| Uncached (cold) aggregate < **5 s** | p50 22 ms · p95 **771 ms** · max **1 482 ms**, over 51 views | **Yes**, by ~3.4x at the worst view |
| Nothing exceeds nginx `proxy_read_timeout` (60 s) | No *view* read comes close (max 1.5 s). The **inline recompute endpoint measured 65.9 s**. | **No** — see §6 |
| Aggregation fits the runner's 45 s budget | One bucket of one job: yes for all 12 (worst is `customer_snapshot` at **22.9 s**). A 30-day backfill of all 12: **659 s**. | **No** for any real backfill — see §4.3 |
| CSV export fits the 45 s build budget and the 50 000-row cap | Slowest export **1.7 s**; largest export **200 rows**. | Budget yes; **the cap is unreachable** — see §4.5 |

All 51 LIVE/PARTIAL views were measurable. None raised. 22 gated views were not
measured and that is stated in §7 rather than silently omitted.

---

## 2. Environment and dataset

A p95 without these is meaningless, so they come before the results.

### Hardware and software

| | |
|---|---|
| Host | Apple MacBook Pro 15,1 (6-core Intel, 12 logical), 16 GiB RAM, macOS 24.6.0 |
| Container runtime | Docker Desktop Linux VM, 12 vCPU / 7.75 GiB, kernel 6.12.76-linuxkit |
| Python | 3.12.13 |
| MySQL | 8.4.11, `innodb_buffer_pool_size` **128 MiB**, `innodb_flush_log_at_trx_commit=1` |
| Redis | 7.4.10 |
| App settings | `ENVIRONMENT=ci`, `OBS_SLOW_QUERY_MS=200` |

**The working set does not fit in the buffer pool.** The dataset is 318 MiB of
data + indexes against a 128 MiB pool, so a meaningful share of these timings
includes real page reads. That is deliberate — it is also true of the production
instance, which runs stock MySQL defaults — but it means these numbers are *not*
a pure CPU measurement and will move on a host with a larger pool.

**The measurement ran on an isolated stack, not the shared `wvana-*` one.** A
first full run against the shared `wvana-mysql` was invalidated mid-flight when
another process truncated the database underneath it. Everything below comes
from a dedicated `wvperf-mysql` / `wvperf-redis` / `wvperf-py` stack with no
other traffic on it. Reproduction instructions are in §9.

### Dataset

Built by `analytics_perf_report.py seed --profile full`, deterministic under a
fixed RNG seed.

| Table | Rows | Size |
|---|---:|---:|
| `orders` | 50 000 | 26.2 MiB |
| `order_items` | 99 907 | 11.5 MiB |
| `order_payments` | 40 585 | 12.0 MiB |
| `shipments` | 27 588 | 5.1 MiB |
| `returns` | 731 | — |
| `cart_events` | 440 792 | 225.7 MiB |
| `inventory_movements` | 87 516 | 31.6 MiB |
| `users` | 12 001 | 4.2 MiB |
| `products` | 120 | — |
| **Total** | | **318.5 MiB** |

* **Window** — 400 store-local days, 2025-06-23 → 2026-07-27, ending yesterday
  so the last bucket is closed.
* **Shape** — weekly seasonality (×1.35 at weekends) over a 0.7 → 1.3 growth
  trend, so daily series are not flat. Orders are spread across all 24 UTC hours
  so the hourly rollup is fully populated and the Asia/Kolkata day boundary
  really does split orders across two UTC dates.
* **Skew** — a third of orders come from the most active 15% of customers, so
  cohort, RFM and LTV views see a realistic repeat-purchase distribution rather
  than a degenerate one.
* **Deliberate gaps** — 1 product in 10 carries no `unit_cost` (so
  `costed_units / units < 1` and the margin views report real coverage), 1 in 12
  is out of stock (so `inventory_daily`'s per-product `days_oos` lookup is
  exercised), 1 shipment in 20 has no `shipment_cost`, and ~8% of prepaid orders
  carry a failed first attempt.
* **Rollups** — `agg_*` built by a 30-day backfill of all 12 jobs before the read
  measurements, so views read populated rollups. Windows longer than 30 days
  correctly report `ROLLUP_STALE`; that is the honest state of a store that has
  aggregated 30 days, not a measurement artefact.

---

## 3. Methodology

**What "cold" and "warm" mean.** Cold is `refresh=true`, which bypasses the
cache *read* and runs the resolver end to end before writing the entry. Warm is
the ordinary read issued immediately afterwards, which is guaranteed to hit that
entry — and the harness asserts it did (`warm_actually_hit` was true for all 51
views). Both go through the real `AnalyticsViewService`: registry lookup,
permission check, tz-generation read, resolver dispatch, envelope construction,
and the JSON round trip into and out of Redis.

**What is excluded.** FastAPI routing, request validation, response
serialisation and the network. Those are the same for every endpoint in the app
and are not what this exercise is about. The gap between these numbers and a
browser's timing panel is that overhead plus RTT — stated here rather than
folded in silently.

**Instrumentation is the production one.** `measured()` installs a real
`RequestCollector` in the same contextvar the SQLAlchemy listeners read, so
`db_ms`, `query_count` and the slow-query captures come from
`app/core/observability/` at the deployed `OBS_SLOW_QUERY_MS=200` threshold —
not from a second timer that could disagree with the APM dashboard.

**Repeats and percentiles.** 3 runs per view per mode, 3 per aggregation job,
200 requests for the cache mix, 1 per export. Percentiles are **nearest-rank**,
not interpolated: over 3 samples an interpolated p95 is a fabricated number
wearing a statistic's clothes. With n=3 the p95 and the max coincide, which is
why both are printed — the p95 column here means "worst of three", and is
labelled as such rather than implying a distribution nobody sampled.

**Buffer pool warm-up.** `SELECT COUNT(*)` over `orders`, `order_items`,
`order_payments` and `shipments` before timing anything, so the first measured
view does not pay for the whole dataset's page faults. `cart_events` and
`inventory_movements` are *not* warmed and at 257 MiB combined could not be.

**One caveat that matters for the query counts.** The cost-rule resolver caches
in Redis, keyed per `(cost_type, date, scope)`. Repeats 2 and 3 of a margin view
therefore run against a warm cost-rule cache, so the `queries` column below is
the *warm* count. §5.2 measures the cold count separately, because the
difference is a factor of twenty.

---

## 4. Results

### 4.1 View resolution — all 51 LIVE/PARTIAL views

Period `30d`, comparison `previous_period`, 3 runs each. Times in ms.

| View | State | Resolver | cold p50 | cold p95 | cold max | warm p95 | db ms | queries |
|---|---|---|---:|---:|---:|---:|---:|---:|
| `sales-finance/pricing-and-margin` | PARTIAL | custom | 791 | **1482** | 1482 | 4.4 | 616 | 28 |
| `sales-finance/unit-economics` | PARTIAL | custom | 656 | **916** | 916 | 4.9 | 477 | 29 |
| `sales-finance/contribution-margin` | PARTIAL | custom | 686 | **771** | 771 | 3.9 | 462 | 28 |
| `customers/customer-segmentation` | LIVE | breakdown | 431 | **547** | 547 | 3.9 | 425 | 4 |
| `customers/rfm-customer-analysis` | LIVE | breakdown | 351 | **353** | 353 | 3.4 | 346 | 4 |
| `control-centre/analytics-tracking-health` | LIVE | tracking_health | 130 | 323 | 323 | 4.2 | 121 | 13 |
| `orders/returns-and-refunds` | LIVE | breakdown | 43 | 53 | 53 | 3.6 | 29 | 15 |
| `website/geographic-sales` | LIVE | geo | 35 | 52 | 52 | 3.2 | 20 | 15 |
| `products/product-performance` | LIVE | breakdown | 43 | 46 | 46 | 4.1 | 28 | 15 |
| `payments/cod-performance` | LIVE | timeseries | 34 | 42 | 42 | 3.8 | 19 | 15 |
| `executive/executive-overview` | LIVE | metrics | 17 | 42 | 42 | 4.5 | 7 | 6 |
| `executive/real-time-sales` | LIVE | timeseries | 33 | 40 | 40 | 4.4 | 15 | 15 |
| `website/checkout-performance` | LIVE | funnel | 27 | 37 | 37 | 5.4 | 12 | 16 |
| `website/cart-abandonment` | LIVE | timeseries | 26 | 35 | 35 | 4.3 | 12 | 15 |
| `sales-finance/revenue-and-profitability` | PARTIAL | custom | 29 | 34 | 34 | 4.0 | 11 | 11 |
| `executive/seasonal-and-festival-sales` | PARTIAL | custom | 29 | 33 | 33 | 4.2 | 13 | 13 |
| `executive/sales-forecasting` | PARTIAL | custom | 26 | 31 | 31 | 4.0 | 11 | 13 |
| `products/sku-level-analytics` | PARTIAL | table | 29 | 30 | 30 | 3.9 | 19 | 10 |
| `products/category-performance` | LIVE | breakdown | 27 | 30 | 30 | 3.8 | 15 | 10 |
| `payments/payment-analytics` | LIVE | breakdown | 22 | 29 | 29 | 3.9 | 10 | 15 |
| `orders/courier-performance` | PARTIAL | breakdown | 25 | 27 | 27 | 3.6 | 11 | 15 |
| `sales-finance/orders-and-average-order-value` | LIVE | timeseries | 25 | 26 | 26 | 3.3 | 9 | 10 |
| `customers/new-vs-returning-customers` | LIVE | breakdown | 22 | 24 | 24 | 5.1 | 9 | 10 |
| `payments/payment-failure` | LIVE | table | 22 | 24 | 24 | 3.1 | 11 | 15 |
| `customers/customer-lifetime-value` | LIVE | breakdown | 20 | 24 | 24 | 5.9 | 8 | 10 |
| `orders/order-fulfilment` | LIVE | breakdown | 17 | 22 | 22 | 3.7 | 7 | 10 |
| `sales-finance/coupon-and-discount-performance` | LIVE | breakdown | 15 | 21 | 21 | 3.2 | 7 | 10 |
| `sales-finance/tax-and-gst` | PARTIAL | table | 21 | 21 | 21 | 4.3 | 8 | 10 |
| `orders/shipping-and-delivery` | PARTIAL | timeseries | 18 | 21 | 21 | 5.0 | 8 | 9 |
| `orders/cancellation-analytics` | LIVE | breakdown | 20 | 21 | 21 | 3.9 | 8 | 10 |
| `customers/loyalty-and-rewards` | LIVE | timeseries | 18 | 20 | 20 | 3.8 | 7 | 10 |
| `inventory/inventory-analytics` | PARTIAL | timeseries | 17 | 18 | 18 | 4.7 | 6 | 10 |
| `executive/budget-vs-actual` | LIVE | custom | 16 | 18 | 18 | 4.2 | 7 | 7 |
| `executive/ecommerce-business-health` | LIVE | metrics | 11 | 17 | 17 | 4.5 | 4 | 6 |
| `website/conversion-funnel` | PARTIAL | funnel | 15 | 16 | 16 | 4.1 | 5 | 5 |
| `products/product-bundling-and-cross-sell` | LIVE | table | 12 | 16 | 16 | 7.3 | 4 | 4 |
| `control-centre/data-reconciliation` | LIVE | reconciliation | 13 | 15 | 15 | 3.5 | 5 | 5 |
| `customer-experience/website-speed-and-technical-performance` | PARTIAL | timeseries | 8 | 14 | 14 | 5.0 | 4 | 4 |
| `customers/cohort-and-retention` | LIVE | cohort | 11 | 12 | 12 | 4.8 | 4 | 5 |
| `inventory/inventory-turnover` | PARTIAL | breakdown | 10 | 12 | 12 | 3.7 | 6 | 4 |
| `customers/customer-analytics` | LIVE | metrics | 11 | 11 | 11 | 3.4 | 4 | 6 |
| `inventory/stock-availability` | LIVE | breakdown | 11 | 11 | 11 | 3.9 | 6 | 4 |
| `inventory/low-stock-and-out-of-stock` | LIVE | table | 7 | 10 | 10 | 3.9 | 3 | 4 |
| `customers/customer-churn` | LIVE | timeseries | 9 | 9 | 9 | 3.8 | 3 | 4 |
| `payments/fraud-and-risk-analytics` | LIVE | table | 6 | 9 | 9 | 3.8 | 3 | 4 |
| `control-centre/alerts-and-anomaly` | LIVE | table | 8 | 8 | 8 | 3.7 | 3 | 4 |
| `customer-experience/customer-support-and-complaint` | PARTIAL | breakdown | 7 | 7 | 7 | 3.2 | 3 | 4 |
| `inventory/demand-forecasting` | PARTIAL | custom | 6 | 6 | 6 | 3.6 | 2 | 2 |
| `products/upsell-performance` | PARTIAL | breakdown | 4 | 5 | 5 | 3.4 | 1 | 1 |
| `control-centre/experiment-and-ab-testing` | LIVE | custom | 4 | 4 | 4 | 3.7 | 1 | 1 |
| `customer-experience/reviews-and-ratings` | LIVE | breakdown | 3 | 3 | 3 | 3.2 | 1 | 1 |

**Distribution across the 51 views:** cold p50 **22 ms**, p95 **771 ms**, max
**1 482 ms**. Warm p50 **3.9 ms**, p95 **5.4 ms**, max **7.3 ms**.

The warm number is essentially constant regardless of view: a warm read is one
Redis `GET`, one `GET` for the generation counter, a Pydantic validation and a
tz-generation lookup. It does not depend on how expensive the view is, which is
exactly what the cache is for and is why the 2 s target is met by a factor of
hundreds.

### 4.2 Aggregation — one bucket, at full volume

Bucket `2026-07-26`, 3 runs each, via `AggregationRunner.run_bucket`. Times in ms.

| Job | p50 | p95 | max | db ms p50 | queries | rows written |
|---|---:|---:|---:|---:|---:|---:|
| `customer_snapshot` | **21 326** | **22 871** | 22 871 | 4 826 | 35 | 11 024 |
| `cohort_monthly` | **2 030** | **2 615** | 2 615 | 1 227 | 9 | 105 |
| `product_daily` | 969 | 971 | 971 | 421 | **238** | 113 |
| `inventory_daily` | 468 | 791 | 791 | 379 | 10 | 120 |
| `payment_daily` | 120 | 192 | 192 | 76 | 8 | 8 |
| `shipment_geo_daily` | 122 | 158 | 158 | 37 | 8 | 86 |
| `order_daily` | 114 | 126 | 126 | 62 | 13 | 1 |
| `shipment_daily` | 78 | 116 | 116 | 49 | 6 | 5 |
| `funnel_daily` | 54 | 63 | 63 | 29 | 6 | 1 |
| `promo_daily` | 44 | 53 | 53 | 12 | 8 | 1 |
| `order_hourly` | 47 | 48 | 48 | 16 | 5 | 24 |
| `customer_daily` | 43 | 46 | 46 | 20 | 6 | 1 |

Twelve jobs are registered, not the eight that
`app/services/analytics/aggregation/__init__.py`'s docstring still claims —
`cohort_monthly`, `payment_daily`, `promo_daily` and `shipment_geo_daily` now
exist in `jobs_finance.py`. That docstring is stale; it is a peer's file and is
not changed here.

`customer_snapshot` is **22x** the next slowest job and **440x** the median one.
Only 23% of its time is in SQL (4.8 s of 21.3 s) — the rest is Python building
11 024 row dicts and RFM quintiles.

### 4.3 Backfill — 30 days × 12 jobs

`AggregationRunner.run_window`, budget deliberately raised to 20 minutes so the
measurement is the *cost of the work*, not the point at which the runner gave up.

| | |
|---|---:|
| Wall clock | **659.2 s** (11 min 0 s) |
| Of which SQL | 179.1 s (**27%**) |
| Queries | 9 004 |
| Buckets processed | 360 (30 days × 12 jobs), all `success` |
| Rows written | 337 362 — of which **327 400 (97%) by `customer_snapshot`** |
| Runner's production budget (`DEFAULT_BUDGET_MS`) | 45 000 ms |
| Fits in it? | **No.** 14.6x over. |
| Buckets one 45 s budget covers | **24.6** of the 360 requested |

Per-job share of the 659 s, from the per-bucket p50:

| Job | 30 buckets | Share |
|---|---:|---:|
| `customer_snapshot` | 640 s | **97%** |
| `cohort_monthly` | 61 s | 9% |
| `product_daily` | 29 s | 4% |
| `inventory_daily` | 14 s | 2% |
| everything else combined | 17 s | 3% |

(Shares exceed 100% because the per-bucket p50s were taken on a warmer cache
than the backfill ran with; the ranking is what matters.)

**Three quarters of a backfill is Python, not the database.** That is the single
most useful fact in this section: adding an index will not fix it.

### 4.4 Cache behaviour under a realistic request mix

200 sequential requests drawn from a weighted distribution — 70% of traffic to
the 10 hottest views (executive module first, as a real user lands there), the
rest across all 51; periods weighted 60/30/10 across `30d`/`7d`/`90d`. Real
TTLs, nothing pre-warmed.

| | |
|---|---:|
| Requests | 200 |
| Distinct views reachable | 51 |
| Hits | **168** |
| Misses | 32 |
| **Hit rate** | **84.0%** |
| Errors | 0 |
| Latency p50 / p95 / max | 3.7 ms / 25.7 ms / 3 500 ms |
| Elapsed | 7.5 s |

The 32 misses are the compulsory first touch of each distinct
(view, period) pair — 51 views × 3 periods is 153 possible keys, and 200
requests over 7.5 s never expire a 45 s `realtime` TTL. **This is therefore a
ceiling, not a steady state:** a real dashboard left open past 45 s re-misses
every `realtime` view. The single 3 500 ms sample is the first cold touch of
`pricing-and-margin` with an empty cost-rule cache (see §5.2), and it is the
only sample in the run over 600 ms.

### 4.5 CSV export at the 50 000-row limit

Exactly what `POST /analytics/exports` does: resolve the view uncached with
`limit = CSV_ROW_CAP`, then `build_csv_export`. Audit write and streaming
excluded.

| View | Table | Rows | ms |
|---|---|---:|---:|
| `payments/payment-failure` | `failure_reasons` | **200** | 40 |
| `sales-finance/pricing-and-margin` | `pricing_table` | 120 | **1 722** |
| `products/product-performance` | `product_table` | 120 | 54 |
| `products/sku-level-analytics` | `sku_table` | 120 | 30 |
| `orders/returns-and-refunds` | `returns_table` | 120 | 69 |
| `sales-finance/tax-and-gst` | `tax_table` | 30 | 18 |
| `orders/order-fulfilment` | `ageing_orders` | 30 | 33 |
| `orders/cancellation-analytics` | `cancellation_table` | 30 | 15 |
| `customers/customer-lifetime-value` | `top_customers` | 14 | 22 |
| `products/category-performance` | `category_table` | 6 | 22 |
| `orders/courier-performance` | `courier_table` | 5 | 37 |
| `control-centre/data-reconciliation` | `variances` | 3 | 18 |
| `sales-finance/coupon-and-discount-performance` | `coupon_table` | 1 | 18 |
| `website/geographic-sales` | `geo_table` | 1 | 48 |
| 16 further views | — | **produced no table** | see §7 |

* **Largest export produced: 200 rows.** Not 50 000. Not close.
* Slowest build: **1.72 s**, against a 45 s budget and a 60 s proxy timeout.
* Nothing truncated. Nothing came near either bound.

The 200 is not a coincidence — it is `MAX_LIMIT`. See §5.3.

### 4.6 The heaviest queries

Captured through the production instrumentation at the deployed 200 ms
threshold, and written to `obs_slow_queries` with `route = 'perf:<operation>'`
so the existing `/api/v1/observability/slow-queries` dashboard can read this
run's output with no special case. **220 slow queries across 11 distinct
fingerprints.**

Ranked by total time across the run:

| Total | Worst | n | Table | Seen in | Query |
|---:|---:|---:|---|---|---|
| 31.5 s | 1 257 ms | 33 | `orders` | job, backfill | `SELECT user_id, created_at, total_amount FROM orders WHERE (status IN (…) OR status=… AND (refunded_at IS NOT NULL OR EXISTS(…returns…)))` |
| 24.3 s | 994 ms | 33 | `order_items` | job, backfill | `SELECT orders.user_id, SUM(qty), SUM(qty*unit_price), SUM(CASE unit_cost…), SUM(qty*unit_cost) … JOIN orders … GROUP BY orders.user_id` |
| 16.8 s | 747 ms | 33 | `orders` | job, backfill | `SELECT user_id, payment_method, COUNT(id), MAX(created_at) … GROUP BY user_id, payment_method` |
| 16.2 s | 875 ms | 33 | `orders` | job, backfill | `SELECT user_id, MIN(created_at), MAX(created_at), COUNT(id), SUM(total_amount), SUM(discount+payment_discount) … GROUP BY user_id` |
| 11.5 s | 521 ms | 33 | `orders` | job, backfill | `SELECT user_id, MIN(created_at) … (recognition predicate) … GROUP BY user_id` |
| 10.3 s | 460 ms | 33 | `orders` | job, backfill | `SELECT user_id, COUNT(id), SUM(total_amount) … created_at >= ? AND < ? GROUP BY user_id` |
| **2.2 s** | **341 ms** | 9 | `agg_customer_snapshot` | **view.cold, mix, export** | `SELECT DISTINCT tz_generation FROM agg_customer_snapshot WHERE bucket_date >= ? AND < ?` |
| 2.2 s | 1 139 ms | 3 | `agg_customer_snapshot` | job, backfill | `DELETE FROM agg_customer_snapshot WHERE bucket_date = ? AND tz_generation = ?` |
| 1.8 s | 645 ms | 4 | `inventory_movements` | job, backfill | `SELECT product_id, SUM(delta) FROM inventory_movements WHERE occurred_at < ? GROUP BY product_id` |
| 1.2 s | 274 ms | 5 | `agg_customer_snapshot` | backfill | `INSERT INTO agg_customer_snapshot (…) VALUES …` |

The first six are all `CustomerSnapshotJob._collect` — six full-history
`GROUP BY user_id` scans of `orders`/`order_items`, run once per bucket.

**Only one slow-query fingerprint appears on the read path**, and it is the
seventh row: `SELECT DISTINCT tz_generation` over `agg_customer_snapshot`. See
§5.4.

---

## 5. Why the slow things are slow

### 5.1 `customer_snapshot` — a full-history rescan, once per bucket

*22.9 s per bucket · 97% of a 30-day backfill · 327 400 rows written*

`CustomerSnapshotJob._collect`
(`backend/app/services/analytics/aggregation/jobs_customer.py:520`) issues six
aggregate queries, each of the shape

```sql
… FROM orders WHERE orders.status IN (…) AND orders.created_at < :end GROUP BY orders.user_id
```

There is **no lower bound on `created_at`**. Every one of those six queries
scans the customer's entire order history, for every customer, for every bucket.
The job's semantics genuinely are "lifetime state as of the close of day D", so
the scan is not a mistake — but rebuilding day D from scratch when day D−1 is
already materialised is O(all orders) per bucket where it could be
O(that day's orders).

Rebuilding a year costs 365 full-history scans. The measured 30-day backfill did
30 of them and wrote 327 400 rows to express 30 days of change over an 11 024-row
customer population.

Three multipliers on top:

* Only **23%** of the time is SQL (4.8 s of 21.3 s). The rest is Python:
  `_CustomerState` objects, RFM quintile sorting, and 11 024 row dicts per bucket.
* Rows are inserted in chunks of `SNAPSHOT_INSERT_CHUNK = 500`, so 23 INSERT
  round trips per bucket.
* `_prune_expired` (`jobs_customer.py:483`) runs a retention DELETE across the
  **whole table** on every bucket, with `bucket_date != LAST_DAY(bucket_date)` —
  not sargable. It was measured at up to **1 139 ms** per call.

The table's own docstring predicts this: *"50k customers is 50k rows per day, 18M
rows a year"*. At the measured 11 024 rows/day and 90-day daily retention, steady
state is ~992 000 daily rows plus month-ends — for a table whose primary use is
"current state".

### 5.2 The margin views — an N+1 over cost rules, one query per day of window

*`pricing-and-margin` 1 482 ms · `unit-economics` 916 ms · `contribution-margin` 771 ms*

These three are the slowest views measured, and all three run
`margin_cascade` → `cascade_for`
(`backend/app/services/analytics/resolvers/finance.py:285`), which calls
`MarginService.compute()` **twice** — once for the window and once for the
comparison window.

Inside, `MarginService._resolve_costs` (`margin.py:1183`) loops over every day of
the window and, for each day, over all 8 cost types
(`CM2_COST_TYPES` + `CM3_COST_TYPES`, `margin.py:167`), calling
`CostRuleResolver.resolve(cost_type, day, …)`. That resolver caches in Redis, but
**the cache key includes the date** (`cost_rules.py:473`), so a cold cache means
one `SELECT` against `analytics_cost_rules` per (cost type, day).

Measured directly, for `pricing-and-margin`:

| Window | SQL queries, cold cost-rule cache | SQL queries, warm |
|---|---:|---:|
| `7d` | **99** | 28 |
| `30d` | **328** | 28 |
| `90d` | **568** | 28 |

The 28 in the table in §4.1 is the *warm* figure — repeats 2 and 3 of each view.
The first cold run paid the full count, which is why `pricing-and-margin`'s p50
is 791 ms but its max is 1 482 ms, and why the single 3.5 s outlier in the cache
mix (§4.4) exists.

**This is also a Redis-outage amplifier.** `cache.py` degrades gracefully to
uncached reads by design — but with Redis down, *every* request to a margin view
pays the cold count. A 90-day margin view goes from 28 queries to 568.

One query fetching every rule covering `[date_from, date_to)` and resolving in
Python would replace all of them; the rule set is tiny.

### 5.3 CSV export — the 50 000-row cap is unreachable by construction

`analytics_views.py:96` substitutes a server-side `limit = CSV_ROW_CAP = 50 000`
for the export path, precisely so an export can return more than a screen. Every
resolver then clamps it straight back down:

| Resolver | Clamp |
|---|---|
| `BreakdownResolver` | `core.py:882` — `min(filters.limit or 20, MAX_LIMIT)` |
| `TableResolver` | `core.py:1020` — `min(filters.limit or page_size or 25, MAX_LIMIT)` |
| `LevelBreakdownResolver` | `levels.py:775` — `min(filters.limit or 20, MAX_LIMIT)` |
| `GeoResolver` | `special.py:504` — `min(filters.limit or 20, 200)` (hard-coded) |

`MAX_LIMIT` is **200** (`filters.py:38`). Measured maximum across every
exportable view: **200 rows** (`payments/payment-failure`, which is the only one
with enough distinct dimension values to reach it).

So the whole truncation apparatus in `export.py` — the 50 000-row cap, the 45 s
build budget, the `# TRUNCATED` final row, the `X-Analytics-Truncated` header —
is correct, careful, well-tested code that **cannot fire**. It also means the
45 s/60 s timeout analysis for exports is moot today: the slowest export
measured 1.72 s.

This is reported, not fixed: raising the clamp without also revisiting
`AnalyticsRepository.HARD_ROW_CAP` (5 000, `analytics_repository.py:78`, which
*raises* rather than truncates) would turn a 200-row export into a 500 error at
5 001 rows.

### 5.4 The one read-path query worth an index

`SELECT DISTINCT tz_generation FROM agg_customer_snapshot WHERE bucket_date >= ? AND bucket_date < ?`

is `AnalyticsRepository.distinct_tz_generations`
(`analytics_repository.py:398`), called by `probe_source` → `guard_tz_generation`
on **every** read of a view backed by `agg_customer_snapshot`. Measured at up to
**341 ms**, and it is why `customers/customer-segmentation` (547 ms, 425 ms of it
SQL, only 4 queries) and `customers/rfm-customer-analysis` (353 ms, 346 ms SQL,
4 queries) are the two slowest non-margin views.

`EXPLAIN` on the 327 400-row table:

```
type: range   key: uq_agg_customer_snapshot_key   key_len: 3   rows: 167373
```

It scans 167 373 index entries to return one distinct value. The unique key is
`(bucket_date, customer_key, tz_generation)`; `customer_key` sits between the two
columns the query needs, so MySQL cannot do a loose index scan. An index on
`(bucket_date, tz_generation)` would make this a loose scan and effectively free.

The guard is right to exist — mixing tz generations is the one error nothing
downstream can detect — but on the largest rollup it currently costs more than
the query it protects.

### 5.5 `product_daily` — a lazy-load N+1 through the tax allocator

*971 ms per bucket · **238 queries***

`ProductDailyJob._sold` (`jobs.py:768`) loads the bucket's orders with
`selectinload(Order.items)` — correct — and then calls `allocate_order` per
order. Inside, `allocation._tax_rate_milli` (`allocation.py:222`) does
`getattr(item, "product", None)`. `OrderItem.product` is `lazy="select"`
(`order.py:220`), so each distinct product triggers a `SELECT products.*`, and
each of those triggers a second `SELECT … taxes …` for `Product.taxes`.

Traced call stack, confirmed:

```
jobs.py:813   _sold: allocation = allocate_order(order, order.items)
allocation.py:409  allocate_order: tax_weights, tax_basis = _tax_weights(...)
allocation.py:255  _tax_weights: rates = [_tax_rate_milli(item) for item in items]
allocation.py:222  _tax_rate_milli: product = getattr(item, "product", None)
```

The identity map collapses this to two queries per *distinct product per
session*, so it is 2 × (distinct products sold that day) — 238 queries for 113
products here. It grows with daily order volume until it saturates at catalogue
size, so a busier day on a wider catalogue costs proportionally more, and the
per-bucket cost is unbounded in a way the rest of this job is not.

### 5.6 `cohort_monthly` — recomputes a 24-month grid for every backfilled day

*2 615 ms per bucket · 61 s of a 30-day backfill*

`CohortMonthlyJob` (`jobs_finance.py:1079`) deletes and reinserts the trailing
`COHORT_MONTHS_RECOMPUTED = 24` cohorts on every run — deliberately, so the grid
is immune to late-arriving refunds by construction. Its own docstring says:
*"running this job over a backfilled year of days recomputes the same grid 365
times. Schedule it once per tick, not once per backfilled day."*

`AggregationRunner.run_window` does not know that. It runs every named job for
every bucket, so a 30-day backfill did 30 identical recomputes — 61 s of which
~59 s was redundant. The job is correct; the *scheduling contract* it documents
has no mechanism behind it.

### 5.7 `inventory_daily` — a second unbounded scan, plus a bounded N+1

*791 ms per bucket*

`InventoryDailyJob._levels` (`jobs_ops.py:388`) computes closing stock with

```sql
SELECT product_id, SUM(delta) FROM inventory_movements WHERE occurred_at < :end GROUP BY product_id
```

— again no lower bound, so it scans the whole 87 516-row ledger per bucket
(measured at up to 645 ms). And `_days_oos` (`jobs_ops.py:511`) issues one
`SELECT` per out-of-stock product per bucket to read the previous day's counter.
It is bounded by the OOS count rather than the catalogue, so it is mild today
(10 products), but it is a per-row query inside a per-bucket loop.

---

## 6. What would time out behind nginx

`frontend/nginx.conf:38` sets `proxy_read_timeout 60s` on `location /api/`.
Anything slower is a 504 in production regardless of what the backend eventually
does.

### Confirmed: `POST /api/v1/analytics/admin/aggregate?inline=true`

Measured, not inferred:

```
window 2026-06-02..2026-06-12, job=customer_snapshot, budget_ms=55000 (the endpoint's own cap)
status=partial  budget_exhausted=True  days_processed=4/10
ACTUAL WALL = 65.9 s          nginx proxy_read_timeout = 60 s
```

**The cause is that the budget is a start gate, not a completion guarantee.**
`AggregationRunner.run_window` checks the clock *before* each `(bucket, job)`
pair (`runner.py:269`) — which is the right place for it, since a check
afterwards has already spent the time it was meant to protect. But it means the
worst case is `budget + (duration of one bucket)`. With
`INLINE_BUDGET_MS_CAP = 55_000` (`schemas/analytics_admin.py:43`) and
`customer_snapshot` at 22.9 s per bucket, the worst case is ~78 s and the
measured case was 65.9 s.

The operator sees a 504. The run continues to completion server-side, writes its
buckets, and closes its `analytics_sync_runs` row as `partial` — so the work is
not lost and `next_date_from` is still correct, but the caller never learns any
of that and an impatient second click starts another run alongside the first.

The endpoint's default budget (`INLINE_BUDGET_MS_DEFAULT = 20_000`) is safe today
at 20 + 22.9 = 43 s. **The margin is 17 s and shrinks as the customer table
grows** — this becomes a 504 on the default budget once one bucket exceeds ~40 s.

### Not at risk

* **Every view read.** Worst cold max measured is 1 482 ms — 40x inside the
  timeout, even before the cache.
* **Every CSV export.** Worst build 1.72 s, and §5.3 explains why it cannot grow.
* **The worker container.** `ANALYTICS_WORKER_DRAIN_BUDGET_MS` derives from a
  300 s lease and it is not behind nginx at all.
* **The enqueue path** (`?inline=false`, the default) writes queue rows and
  returns 202.

---

## 7. What could not be measured, and why

Nothing was silently omitted.

**22 gated views (of 73) were not timed.** `resolve_view` returns a
`GatedViewEnvelope` for `INTEGRATION_REQUIRED` / `FEATURE_REQUIRED` /
`NOT_APPLICABLE` / `BLOCKED_BY_MISSING_SOURCE` without a query, a cache read or a
cache write. Timing them measures dict construction. They are enumerated in the
`gated_views_not_measured` block of the JSON report.

**16 of the 30 exportable LIVE/PARTIAL views produce no table at all**, so their
export path could not be measured — it 404s (`analytics_views.py:320`). Two
distinct causes:

| View | Resolver | Declared table | Why nothing came back |
|---|---|---|---|
| `products/product-bundling-and-cross-sell` | table | `basket_pairs` | `NOT_CONFIGURED` — no source binding |
| `customers/customer-segmentation` | breakdown | `segment_table` | `NOT_CONFIGURED` |
| `customers/rfm-customer-analysis` | breakdown | `rfm_table` | `NOT_CONFIGURED` |
| `inventory/stock-availability` | breakdown | `availability_table` | `NOT_CONFIGURED` |
| `inventory/low-stock-and-out-of-stock` | table | `low_stock_table` | `NOT_CONFIGURED` |
| `payments/fraud-and-risk-analytics` | table | `flagged_orders` | `NOT_CONFIGURED` |
| `customer-experience/customer-support-and-complaint` | breakdown | `message_table` | `NOT_CONFIGURED` |
| `customer-experience/reviews-and-ratings` | breakdown | `rated_products` | `NOT_CONFIGURED` |
| `control-centre/alerts-and-anomaly` | table | `alert_feed` | `NOT_CONFIGURED` |
| `executive/budget-vs-actual` | custom | `budget_lines` | `NOT_CONFIGURED` |
| `executive/real-time-sales` | **timeseries** | `recent_orders` | a timeseries resolver emits `series`, never `tables` |
| `customers/customer-churn` | **timeseries** | `at_risk` | same |
| `customers/loyalty-and-rewards` | **timeseries** | `loyalty_summary` | same |
| `website/cart-abandonment` | **timeseries** | `abandoned_products` | same |
| `payments/cod-performance` | **timeseries** | `cod_by_state` | same |
| `customer-experience/website-speed-and-technical-performance` | **timeseries** | `slow_endpoints` | same |

The first ten are unwired sources — they will fill in when the binding lands. The
last six are a **registry/resolver shape mismatch**: the registry declares
`export=True` and a table id for a view whose resolver structurally cannot
produce a table. Those six will never work without a registry or resolver change,
and today they present an export button that always 404s.

**`ANALYTICS_V2_ENABLED` gates nothing.** It appears exactly twice in the
codebase — its definition at `core/config.py:179` and a docstring in
`services/analytics/__init__.py:1`. No route registration, dependency or
frontend check reads it. The read surface measured here is mounted
unconditionally at `api/v1/router.py:101` and is reachable in production today.
The measurements are therefore of live code, not of a dark launch.

---

## 8. Prioritised fixes

Reported, not applied — these files have other owners.

| # | Fix | Evidence | Expected effect |
|---|---|---|---|
| **1** | **Raise the inline recompute's effective ceiling below nginx's**, e.g. cap `budget_ms` at `55 000 − (slowest observed bucket)`, or make `run_window` refuse to *start* a bucket that cannot finish inside the remaining budget. | §6 — measured 65.9 s against a 60 s proxy timeout, at the endpoint's own documented cap. | Removes a reproducible 504 on an admin action. |
| **2** | **Make `customer_snapshot` incremental**, or exclude it from `run_window`'s per-bucket loop and give it its own cadence. | §5.1 — 22.9 s/bucket, 97% of a 659 s backfill, 327 400 rows for 30 days of change. | 659 s backfill → ~20 s. Also removes the input to fix #1. |
| **3** | **Batch the cost-rule lookup** — one query for all rules covering `[date_from, date_to)`, resolved in Python. | §5.2 — 568 SQL queries for a 90-day margin view on a cold Redis; 28 warm. | Slowest three views 1 482/916/771 ms → a few hundred ms, and removes the Redis-outage cliff. |
| **4** | **Decide what the CSV export is for**, then make the code say it: either raise the resolver clamps for the export path (and `HARD_ROW_CAP` with them), or lower `CSV_ROW_CAP` to 200 and delete the truncation machinery. | §5.3 — measured maximum 200 rows against a 50 000 cap. | Either a working large export, or ~150 lines of unreachable code gone. Today it is neither. |
| **5** | **Add `INDEX (bucket_date, tz_generation)` to `agg_customer_snapshot`.** | §5.4 — `EXPLAIN` scans 167 373 rows for one distinct value; measured at up to 341 ms, on the read path of every view backed by that table. | Removes up to ~340 ms of the 425 ms / 346 ms measured DB time in `customer-segmentation` / `rfm-customer-analysis`. One migration. |
| **6** | **Eager-load `OrderItem.product` and `Product.taxes` in `ProductDailyJob._sold`.** | §5.5 — 238 queries/bucket, traced to `allocation.py:222`. | 238 → ~6 queries; grows with catalogue size if left. |
| **7** | **Give `cohort_monthly` a month-grain schedule**, or let a job declare its own bucket grain so `run_window` can skip repeats. | §5.6 — 30 identical 24-month recomputes in one 30-day backfill; the job's docstring already asks for this. | ~59 s off every 30-day backfill. |
| **8** | **Bound `InventoryDailyJob._levels`** with an opening-balance column instead of re-summing the ledger from the beginning of time, and batch `_days_oos`. | §5.7 — 645 ms full-ledger scan per bucket, plus one query per OOS product. | 791 → <100 ms per bucket. |
| **9** | **Fix the six timeseries views that declare an unproducible export table** — drop `export=True`, or give them a table. | §7 — an export button that always 404s. | Correctness, not speed. |
| **10** | **Update `aggregation/__init__.py`'s docstring** — it says eight of twelve rollups are implemented; twelve are registered. | §4.2. | Documentation only. |

Nothing on this list is a read-path emergency. The dashboard, warm or cold, is
comfortably inside its targets — items 3, 5 and 6 are the ones that keep it that
way as the store grows, and items 1 and 2 are the ones that are broken now.

---

## 9. Reproducing this

### The measurement tool

```bash
python scripts/analytics_perf_report.py --help
python scripts/analytics_perf_report.py seed    --profile full     # ~50k orders / 400 days
python scripts/analytics_perf_report.py measure --repeat 3 --backfill-days 30 \
                                                --json /tmp/analytics-perf.json
python scripts/analytics_perf_report.py clean   --rollups
```

`seed` is idempotent (a matching dataset is left alone; `--force` rebuilds) and
everything it writes is namespaced — `orders.order_number LIKE 'PERF-%'`,
`products.sku LIKE 'PERF-SKU-%'`, `users.email LIKE '%@analytics-perf.invalid'`,
`obs_slow_queries.route LIKE 'perf:%'` — so `clean` removes exactly what it made.
Profiles: `full` (50 000 / 400 d), `small` (3 000 / 90 d), `tiny` (400 / 21 d).

**It refuses to run against anything but a local throwaway database.** The guard
is copied from `backend/tests/conftest.py` — `ENVIRONMENT` in
`{test, development, ci}` *and* `MYSQL_HOST` in `{localhost, 127.0.0.1, mysql, db}`
— and runs before any command opens a session, `clean` included. Seeding 50 000
orders into the shared production MySQL would be unrecoverable.

### The isolated stack these numbers came from

Any container that can `import app` will do. This run used a snapshot of the
repo's existing CI runner:

```bash
docker commit wvana-py wvperf-runner:local
docker network create wvperf-net
docker run -d --name wvperf-mysql --network wvperf-net --network-alias db \
  -e MYSQL_ROOT_PASSWORD=root -e MYSQL_DATABASE=ecommerce \
  -e MYSQL_USER=ecom -e MYSQL_PASSWORD=ecom mysql:8
docker run -d --name wvperf-redis --network wvperf-net --network-alias redis redis:7-alpine
docker run -d --name wvperf-py --network wvperf-net \
  -v "$REPO:/repo" -v "$REPO/perf.env:/repo/backend/.env:ro" \
  -w /repo/backend wvperf-runner:local sleep infinity
docker exec wvperf-py alembic upgrade head
```

`perf.env` sets `ENVIRONMENT=ci`, `MYSQL_HOST=db`, `MYSQL_DB=ecommerce`,
`REDIS_URL=redis://redis:6379/0`. The network alias `db` is what satisfies the
guard's host allowlist. **Use a dedicated stack, not the shared `wvana-*` one:**
the first attempt at this measurement was invalidated when another process
truncated the shared database mid-run.

### Getting comparable numbers

* Report the host, the vCPU count and `innodb_buffer_pool_size` alongside any
  result. At 128 MiB against a 318 MiB dataset, pool size is the single largest
  lever on these figures.
* Run the 30-day backfill **before** the view measurements — the script does this
  by ordering `jobs → backfill → views → cache → exports`. Views read against
  empty rollups return in ~3 ms and measure nothing.
* Keep `--repeat 3`. With n=3 the p95 equals the max; both are printed so nobody
  mistakes it for a sampled distribution.
* Expect the cost-rule Redis cache to be warm from repeat 2 onward. To measure a
  genuinely cold margin view, delete `analytics:costrule:*` first.

### The regression guard

```bash
python -m pytest tests/perf/ -q          # 17 tests, ~9 s on a warm database
```

`backend/tests/perf/test_analytics_perf.py` runs the same machinery against the
`tiny` profile and asserts **order-of-magnitude** ceilings only — a test that
fails because a laptop was busy is a test people delete. Its two most useful
assertions are not timings at all: a bound on the *query count* of a cold resolve
(deterministic, so it can be tight enough to actually catch an N+1), and an
assertion that the warm read really did report `cache.hit`. Those thresholds
cannot detect a regression that only appears at volume — that is what this
document is for, and it is re-run by hand.
