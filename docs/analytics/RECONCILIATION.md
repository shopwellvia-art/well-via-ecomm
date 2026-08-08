# Analytics — the known-differences register

**Read this when a number changed and you want to know whether to worry.**

The legacy admin pages (Dashboard, Sales & Revenue, Profit) and the new analytics
subsystem report the same business over the same days and **do not always agree**.
Most of those disagreements are deliberate: a definition was corrected, and the
new number is the right one. A few would be defects.

This file is the list of the deliberate ones. It exists so that "the number
moved" can be answered in ten seconds instead of by reproducing two queries by
hand.

| If the difference is… | Then… |
| --- | --- |
| listed below, and shadow mode attributes the exact amount to it | expected. Nothing to do. The new figure is the one to trust unless the entry says otherwise. |
| **not** listed below | a defect. Shadow mode raises an `analytics_alerts` row for it and the legacy pages cannot be retired until it is closed. |

---

## How the two systems are kept comparable

`backend/app/services/analytics/shadow.py` runs both over the same window every
day and books every rupee of difference against one of the entries below. It is
what turns "the new numbers look right" into evidence.

It measures each metric **three times**:

| Measurement | What it is |
| --- | --- |
| `legacy` | the legacy service, over the window read as **UTC instants** — what the legacy page actually shows |
| `legacy_aligned` | the **same legacy code**, over the same days read as **store-local reporting days** |
| `new` | the new subsystem: `agg_order_daily` / `agg_product_daily` where the new pages read a rollup, `MarginService` where they do not |

That splits every delta in two:

- `legacy_aligned - legacy` is the timezone re-bucketing, and nothing else can
  live there — both sides are the same code. It is an **observation**, not a
  model of one.
- `new - legacy_aligned` is definitional, over one identical window. Each entry
  below contributes a **measured amount** queried from the source rows. What is
  left over after all of them is the **residual**, and a residual outside
  tolerance is a defect.

**A partially explained difference is not an explained difference.** Explaining
₹900 of a ₹1,000 delta leaves ₹100 unexplained and alerts on ₹100.

**A difference with no entry below is UNEXPLAINED by default.** There is no path
through `shadow.classify()` that reaches EXPECTED without a registered
explanation naming the exact number it accounts for.

### Tolerance

| Unit | Tolerance | Why exactly that |
| --- | ---: | --- |
| Money | **1 paisa** | The smallest unit of money that exists. The legacy services cross a `Decimal → float` boundary and `profit_service` does its cost arithmetic in binary floating point; the new side is integer paise end to end. One paisa is the width of that gap and nothing wider. |
| Counts | **0** | An order count cannot round. Any difference in one is real. |
| Percentages | **0.05pp** | Half of the 0.1pp that `profit_service`'s `round(x, 1)` throws away. |
| The parity anchor | **0** | Same query on both sides. "Close" would defeat the point of pinning it. |

Per-day paise rounding is handled separately, as `paisa_rounding` below. It is
the only entry that contributes an upper **bound** instead of a measured amount,
and a bound absorbs residual only up to its own stated size.

### The parity anchor

`MarginService.paid_order_value()` is pinned byte-identical to
`DashboardService._revenue_summary()`: the same statuses, the same window, the
same `Decimal(x or 0)` boundary. It is compared over one identical window at zero
tolerance with **no explanation permitted**.

If the anchor ever differs, stop. The reconciliation between the legacy pages and
this subsystem has lost its meaning and nothing else in the report can be trusted
either. It is deliberately *not* "improved" — `net_revenue` is where the
correction lives.

---

## The register

Each entry below is keyed by the id shadow mode uses. The heading **is** the key;
`shadow.verify_register_is_documented()` checks that every registered explanation
has a section here, and the retirement gate fails while any is missing.

### tz_bucketing

**Store-local reporting days vs the legacy UTC rolling window.**
Affects: every revenue, order, discount, category and margin figure.

**What it is.** The legacy pages filter `orders.created_at` on a UTC instant
range — `dashboard_service._period_bounds` is `now - N days`, so the day boundary
is whatever time of day the page was loaded, in UTC. The new subsystem buckets on
store-local reporting days through `timebox.day_bounds_utc`.

**Which is correct: the new system.** With `store.timezone = Asia/Kolkata` an
order at 23:00 IST is 17:30 UTC *the same day*, but one at 05:00 IST is 23:30 UTC
the day *before*. Bucketing on UTC therefore moves roughly a fifth of each day's
orders into the neighbouring bucket, and "yesterday's sales" on the legacy page
is wrong by 5.5 hours' worth of trade. Unlike most defects this one cannot be
fixed by redeploying — once rows are written under one day boundary, changing the
boundary re-buckets history, which is why every rollup row records its
`tz_generation`.

**How to verify.** `ShadowReport.boundary_orders` lists the orders in the
symmetric difference of the two windows with `created_at` in both zones and which
window each falls in. The tz component of every metric is measured by running the
*same legacy query* over both windows, so it is an observation rather than an
estimate.

```sql
SELECT id, created_at, CONVERT_TZ(created_at,'UTC','Asia/Kolkata') AS local, total_amount
FROM orders
WHERE status IN ('paid','shipped','delivered')
  AND created_at >= :legacy_start AND created_at < :aligned_start;
```

---

### revenue_recognition

**A refunded order's sale stays in the period it was made.**
Affects: every revenue, order, discount, category and margin figure.

**What it is.** The legacy rule is a status filter — `PAID / SHIPPED /
DELIVERED` — so an order refunded later leaves its original period
retroactively. The new rule recognises the sale in the period of
`orders.created_at` when the order reached a paid state, **including one refunded
since**, provided a reversal can actually be dated for it
(`orders.refunded_at`, or a `returns` row with `refunded_at`).

**Which is correct: the new system.** `orders.status` is mutable and `REFUNDED`
is terminal, so under the legacy rule a January sale refunded in March silently
removes itself from January — a closed month changes because of an event two
months later, and the page gives no sign of it. The sale and its reversal are two
events in two periods and each belongs in its own.

The condition matters. A `REFUNDED` order with *neither* `orders.refunded_at`
*nor* a dated return refund has a sale that nothing would ever reverse; admitting
it would overstate revenue permanently. Excluding it keeps recognition and
reversal symmetric by construction. (`OrderService._cancel_core` stamps
`cancelled_at` and never `refunded_at`, which is exactly this case.)

**How to verify.**

```sql
SELECT id, created_at, refunded_at, status, total_amount
FROM orders
WHERE status = 'refunded'
  AND created_at >= :start AND created_at < :end;
```

The sum of `total_amount` over the rows that have a datable reversal is exactly
what shadow mode attributes as the `revenue_recognition` bridge term.

---

### refund_timing

**A reversal is recognised in the period of its own `refunded_at`.**
Affects: `net_revenue`, `net_revenue_rollup`.

**What it is.** `net_revenue` subtracts refunds *dated in the window* by
`orders.refunded_at` / `returns.refunded_at`, whatever period the order they
reverse was created in. The legacy pages have no refund term at all — they
reverse by dropping the order from the status filter.

**Which is correct: the new system.** Doing both — dropping the order *and*
subtracting the refund — reverses the same money twice and reports a **negative**
figure for a window whose true answer is zero. That was a real defect, fixed in
`margin.py`, and the revenue bridge identity balanced all the way through it
because both of its sides were built from the same wrong input. That is why the
tests assert absolute figures and treat `balances()` as necessary, never
sufficient.

A consequence worth knowing: a window with no orders at all can still have a
non-zero `refund_sum`, because a July refund of a March order is a July event.

**How to verify.**

```sql
SELECT SUM(refund_amount) FROM returns
WHERE refunded_at >= :start AND refunded_at < :end;
```

plus whole-order refunds with no refunded return row, valued at
`orders.total_amount`. The total equals
`MarginService.recognised_revenue().refunds_valued_minor`.

> **Caveat, and it is not small.** `returns.refund_amount` is nullable and is
> only stamped at approval, so a refund can be *dated* without ever being
> *valued*. Those rows are counted and named rather than coalesced to zero:
> `RevenueRecognition.refunds_minor` and `net_revenue_minor` come back `None`, and
> shadow mode compares the valued portion and warns.

---

### payment_discount_included

**Discounts include the payment/gateway offer.**
Affects: `discounts`.

**What it is.** `AnalyticsService._summary` reports
`SUM(orders.discount_amount)`. The new `discounts` figure is
`SUM(discount_amount + payment_discount_amount)`.

**Which is correct: the new system.** A 10% card offer is money the store gave
away exactly as a coupon is. Leaving it out understates discounting and
overstates net merchandise sales by the same amount — and it is invisible on the
legacy page, because nothing there shows the column exists.

**How to verify.**

```sql
SELECT SUM(payment_discount_amount) FROM orders
WHERE status IN ('paid','shipped','delivered')
  AND created_at >= :start AND created_at < :end;
```

That figure is the whole of this difference.

---

### category_snapshot

**Category mix uses the category as it was on the sale date.**
Affects: `revenue_by_category`.

**What it is.** The legacy breakdown joins `products.category_id` **live**, so
re-categorising a product rewrites every past period's category mix.
`agg_product_daily` stores `category_id_snapshot` — what the product was when the
bucket was computed.

**Which is correct: the new system.** A report about March must not change in
July because someone tidied the catalogue. Snapshotting surprises people once;
a silently restated history surprises them repeatedly and without warning.

**How to verify.**

```sql
SELECT p.id, p.category_id AS current, a.category_id_snapshot AS at_sale,
       SUM(a.gross_merchandise_sales) AS moved
FROM agg_product_daily a JOIN products p ON p.id = a.product_id
WHERE a.bucket_date >= :from AND a.bucket_date < :to
  AND COALESCE(a.category_id_snapshot,0) <> COALESCE(p.category_id,0)
GROUP BY p.id, p.category_id, a.category_id_snapshot;
```

Revenue leaves the product's *current* category and lands in its *snapshotted*
one; shadow mode books both sides of that move.

---

### shipping_income_not_a_cost

**`orders.shipping_amount` is income, not an expense.**
Affects: `cm1`, `cm2`, `cm3`.

**What it is.** `profit_service.py` defines revenue as
`SUM(quantity * unit_price)` — line level, so shipping income is **excluded** —
and then computes `c1 = revenue - product_cost - shipping_cost - …` where
`shipping_cost` is `SUM(orders.shipping_amount)`, the amount the **customer paid
us**. Shipping is left out of revenue *and* deducted as an expense: a 2x penalty.
Meanwhile `shipments.shipment_cost`, the real carrier charge and the only genuine
cost in the picture, is never read at all.

**Which is correct: the new system.** In the new cascade `orders.shipping_amount`
is added inside CM2 and never subtracted anywhere. The carrier charge is a
separate component sourced from `shipments.shipment_cost` where a real figure
exists, and from a `FORWARD_SHIPPING` cost rule only for the orders that have
none.

The legacy C1 is understated by twice the shipping income, and the error grows
with delivery volume — it is largest exactly when the business is busiest.

**How to verify.**

```sql
SELECT SUM(shipping_amount) FROM orders
WHERE status IN ('paid','shipped','delivered')
  AND created_at >= :start AND created_at < :end;
```

That figure appears twice in the legacy cascade with the wrong sign, and once in
the new one with the right sign.

---

### discount_in_nms

**Discounts are deducted at CM1, not at C3.**
Affects: `cm1`, `cm2`, `cm3`.

**What it is.** The new cascade starts from `net_merchandise_sales` — line
revenue less `discount_amount + payment_discount_amount`. `profit_service` starts
from gross line revenue and subtracts `marketing_discounts` three levels later,
at C3.

**Which is correct: the new system.** A discount is a reduction of the sale
price, not a marketing expense incurred after gross profit. Placing it at C3
inflates C1 and C2 by the full discount and makes gross margin look better than
it is — again in the flattering direction.

**How to verify.**

```sql
SELECT SUM(discount_amount + payment_discount_amount) FROM orders
WHERE status IN ('paid','shipped','delivered')
  AND created_at >= :start AND created_at < :end;
```

CM1 and C1 differ by exactly this amount plus the shipping and packing/handling
terms; shadow mode books all three and the residual must be zero.

---

### cost_rules_vs_settings

**Costs come from effective-dated rules, not five settings keys.**
Affects: `cm1`, `cm2`, `cm3`.

**What it is.** `profit_service` reads `costs.packing_per_order`,
`costs.handling_per_order`, `costs.monthly_overheads` and
`costs.monthly_ad_spend` as flat current values and applies **today's** number to
the whole window. The new cascade resolves `analytics_cost_rules` per day against
that day's drivers, and charges packing/handling at CM2 rather than C1.

**Which is correct: the new system.** A settings key has no history, so raising
packing cost today retroactively restates every past period on the legacy page.
Rules are effective-dated, so a rate that changed mid-window is applied to the
days on each side of the change instead of being averaged across it — which is
the entire reason `analytics_cost_rules` exists.

**How to verify.**

```sql
SELECT * FROM analytics_cost_rules
WHERE cost_type IN ('packaging','handling','marketing_spend')
  AND effective_from <= :day AND (effective_to IS NULL OR effective_to >= :day);
```

against the matching `costs.*` rows in `system_settings`.

---

### gateway_fee_basis

**Gateway fees are charged on money that went through a gateway.**
Affects: `cm2`, `cm3`.

**What it is.** `profit_service` applies `costs.gateway_fee_pct` to
`SUM(total_amount) WHERE payment_method = 'prepaid'`. The new cascade applies a
`GATEWAY_FEE` rule to `(total_amount - cod_balance)`.

**Which is correct: the new system.** `total_amount - cod_balance` is the money
that actually travelled through a gateway. It is exact for prepaid
(`cod_balance` 0), for COD (`cod_balance == total`) and for split COD, without
special-casing any of them. The legacy string test on `payment_method` charges a
full fee on the cash leg of a split order, and charges nothing at all on an
online method that is not literally labelled `prepaid`.

**How to verify.**

```sql
SELECT SUM(total_amount - cod_balance) AS gateway_base,
       SUM(CASE WHEN payment_method='prepaid' THEN total_amount ELSE 0 END) AS legacy_base
FROM orders
WHERE status IN ('paid','shipped','delivered')
  AND created_at >= :start AND created_at < :end;
```

Any gap between the two is split COD or a mislabelled payment method.

---

### carrier_and_logistics_costs

**Forward shipping, return shipping, RTO and marketplace commission exist.**
Affects: `cm2`, `cm3`.

**What it is.** CM2 subtracts `FORWARD_SHIPPING`, `RETURN_SHIPPING`,
`RTO_LOGISTICS` and `MARKETPLACE_COMMISSION`. `profit_service` has no term for
any of them.

**Which is correct: the new system.** These are real cash costs. A return that
was picked up cost a reverse shipment. An RTO cost the forward leg *and* the
return leg and produced no revenue at all. Omitting them makes contribution
margin flattering in the one direction nobody investigates.

**How to verify.** Shadow mode reports each component with its resolved value.
Cross-check against `shipments.shipment_cost` for the orders in the window, and
against the `FORWARD_SHIPPING` rule for the orders that have no shipment cost —
those are the only orders the rule may be charged for, so the two sources never
double-count.

---

### missing_cost_input

**A missing cost input blanks the level instead of assuming zero.**
Affects: `cm1`, `cm2`, `cm3`.

**What it is.** When no `analytics_cost_rules` row covers a component, the new
cascade reports that CM level as `None`, names the missing input in
`missing_inputs`, and grades the whole result `INCOMPLETE`. `profit_service`
reads a missing settings key as `0.0` and prints a confident number.

**Which is correct: the new system.** A zero cost silently inflates margin, and
an inflated margin is the one error nobody goes looking for. Reporting nothing
forces the gap to be closed; reporting zero hides it behind a plausible figure.

Note the one place a zero legitimately appears without a rule: a cost is only
*needed* on a day whose driver is non-zero. A day with only COD orders collected
no money through a gateway, so it owes no gateway fee and needs no gateway rule.
That is a zero **quantity**, not a zero **rate**.

**How to verify.** `MarginResult.missing_inputs` names the components; each maps
to a `cost_type` with no covering `analytics_cost_rules` row for some day in the
window. `AlertRuleKey.MISSING_COST_DATA` is the alert for the same condition
outside shadow mode.

---

### cogs_null_not_zeroed

**A line with no `unit_cost` is excluded from COGS, not costed at zero.**
Affects: `cogs`, `cost_coverage_pct`, `cm1`, `cm2`, `cm3`.

**What it is.** `profit_service` does `COALESCE(unit_cost, 0)`, so an order line
with no cost snapshot contributes zero COGS and reports 100% margin on itself.
The new COGS sum is a plain `SUM(quantity * unit_cost)` — SQL drops NULL rows
from the sum — and those same rows still count in the denominator of
`cost_coverage_pct`.

**Which is correct: the new system — but note that the money is the same.** Both
produce an identical COGS *total*. The difference is in what the number claims
about itself: coverage below 100% makes the new result `INCOMPLETE`, while the
legacy page prints a confident margin over partial cost data with nothing on
screen to say so.

Two grains of coverage are in play and they are not interchangeable.
`MarginService` and `profit_service` both measure coverage over **lines**
(`COUNT(order_items.id)`), which is what shadow mode compares. `agg_order_daily`
stores `costed_units / units`, i.e. **quantity-weighted** coverage. Both are
honest; they answer slightly different questions and will not match on an order
with mixed quantities.

**How to verify.**

```sql
SELECT COUNT(*) FROM order_items oi JOIN orders o ON o.id = oi.order_id
WHERE oi.unit_cost IS NULL
  AND o.status IN ('paid','shipped','delivered')
  AND o.created_at >= :start AND o.created_at < :end;
```

Compare `cost_coverage_pct` on both sides and read `MarginResult.quality`.

---

### overheads_excluded_from_cm3

**CM3 is a contribution figure and stops before overheads.**
Affects: `cm3`.

**What it is.** `profit_service`'s `net_profit` subtracts
`costs.monthly_overheads`, pro-rated. CM3 does not: it is contribution margin
after marketing, not net profit.

**Which is correct: neither.** They answer different questions and both are
legitimate. This entry exists so nobody puts CM3 and `net_profit` on one screen
and reads the gap as an error. **Compare CM3 to C3, never to net profit** — which
is what shadow mode does.

**How to verify.** `costs.monthly_overheads` pro-rated over the window is the
whole of the gap between legacy `net_profit` and legacy `C3`.

---

### paisa_rounding

**Per-day paise rounding against unrounded legacy floats.**
Affects: `cm1`, `cm2`, `cm3`, `aov`, `cost_coverage_pct`.

**What it is.** Cost rules resolve and round to the paisa once **per day**,
because rates are effective-dated by day. `profit_service` multiplies Python
floats and never rounds at all. AOV is a quotient computed at 28-digit `Decimal`
precision on the legacy side and in integer paise on the new one.

**Which is correct: the new system.** Money is integer paise end to end;
`to_minor()` raises on a float. Binary floating point cannot represent 0.10 and
this arithmetic feeds financial reporting. The cost of that choice is that an
N-day window can differ from an unrounded float by up to N paise per rounded
component.

**How to verify.** The bound is `days × rule-resolved components` and is printed
on the difference as `bounded`. This is the **only** entry that contributes an
upper bound rather than a measured amount, and it can never absorb more than that
bound — a residual larger than the bound stays unexplained and alerts.

---

## What is *not* in this register

These are differences a reader might notice that shadow mode does **not** treat
as expected, deliberately:

- **`agg_order_daily.net_revenue` disagreeing with
  `MarginService.recognised_revenue()`.** These are two renderings of one
  definition and they must agree. Shadow mode compares both against the legacy
  figure under the metric names `net_revenue` (service) and `net_revenue_rollup`
  (table) for exactly this reason: a correction that lands in a service but not in
  the table the dashboard reads has not landed.

  > **Open at the time of writing.** They do not agree. An order placed and fully
  > refunded inside one window nets to **₹0** in the service and reports
  > **−₹1,000** (for a ₹1,000 order) in `agg_order_daily.net_revenue`.
  > `OrderDailyJob._money` builds `net_revenue` from the **legacy**
  > `paid_order_value` — which has already dropped the refunded order — and then
  > subtracts the refund again. That is the double reversal `margin.py` documents
  > as fixed, still live in the table the dashboard reads, and it makes the day's
  > revenue negative. Shadow mode flags it UNEXPLAINED and alerts; the fix belongs
  > in `backend/app/services/analytics/aggregation/jobs.py`, and
  > `test_rollup_net_revenue_diverges_from_the_service_and_is_caught` pins the
  > current behaviour until then.
- **A reporting day with no `agg_order_daily` row.** A missing bucket is not a
  bucket that measured zero. It is reported as an unexplained `rollup_coverage`
  difference and alerts CRITICAL — a rollup cannot report its own absence, and a
  day with no row looks exactly like a quiet trading day on every chart.
- **The parity anchor moving.** See above. There is no explanation for this and
  there must never be one.

---

## The retirement gate

`shadow.is_ready_to_retire_legacy(report_history)` returns `(ready, reasons)`.
All three conditions must hold:

1. revenue, orders and AOV within tolerance across **30 consecutive** reporting
   days;
2. every difference observed carries a written explanation — meaning a `###`
   section *in this file*, checked by
   `shadow.verify_register_is_documented()`;
3. no unexplained difference is open, in the reports or in `analytics_alerts`.

When it says no, it says why: which metric, on which day, by how much, or how
many days of evidence are still missing. "Not ready" without a reason is
unactionable, so the function never returns `False` with an empty reason list.

## Adding an entry

When a definition changes deliberately:

1. add an `Explanation` to `EXPECTED_DIFFERENCE_REGISTER` in `shadow.py`, naming
   the metrics it `applies_to`;
2. emit a `BridgeTerm` for it from the comparison builder with the **measured**
   amount, queried from the source rows — never inferred from the delta it is
   meant to explain, which would make every difference explicable by
   construction;
3. add a `### <key>` section here saying what it is, which side is correct and
   why, and how to check it on real data;
4. add a test that the difference is classified EXPECTED *and* that a corrupted
   version of the same number is still classified UNEXPLAINED.

Step 4 is the one that is tempting to skip and the one that keeps the register
honest. An explanation that cannot be made to fail explains nothing.
