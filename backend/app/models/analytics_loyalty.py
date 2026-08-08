"""The loyalty ROLLUP: `agg_loyalty_daily`, the read side of the points ledger.

Same contract as :mod:`app.models.analytics_rollups` — read that module's
docstring first; it is the normative statement of the conventions and none of
them are re-argued here. BigInteger PK, no ForeignKey anywhere, `DECIMAL(14,2)`
for money, `dimension_column()` (NOT NULL, `'-'` sentinel) for every column in
the UNIQUE key, `tz_generation` inside that key, sums and counts and never a
stored average or a stored percentage.

This table lives in its own module rather than inside `analytics_rollups.py`
purely so that several people can add rollups at once without three-way merges;
it is not a different kind of table and must not become one.

Grain: one row per (store-local reporting day, ledger `reason`, tz generation).

Why `reason` is the dimension
-----------------------------
`points_transactions` already partitions cleanly by `reason` — the vocabulary is
a closed enum (`signup_bonus`, `place_order`, `write_review`, `redeem`,
`refund_reversal`, `expiry`, `admin_adjust`) and every row carries exactly one.
It is also the only partition anyone asks about: "how many points did orders
mint", "how much did we expire", "what is redemption costing us". Any other
split (per customer, per tier) either does not exist in the schema or belongs in
a different table at a different grain.

**`delta` is SIGNED, and this table refuses to store it as one number.**
`points_earned` and `points_debited` are two separate NON-NEGATIVE counters
derived from the sign of `delta`; `net_points` is the signed sum measured
independently. That is not redundancy, it is the whole point: a view that has
only a net figure cannot answer "how many points did we issue this month",
because 1 000 issued against 1 000 redeemed and 0 issued against 0 redeemed are
the same net zero. Issuance is a liability we created and redemption is one we
discharged, and they move for completely different reasons.

The identity that must hold on every row::

    points_earned - points_debited = net_points

`net_points` is measured directly as `SUM(delta)` rather than derived from the
other two, exactly as `agg_order_daily.net_revenue` is measured independently of
its bridge: deriving it would make the identity a restatement instead of a
check. The aggregation job asserts it per row and reports a mismatch as a
warning on the bucket it wrote.

`points_redeemed`, `points_expired` and `points_reversed` are *named subsets* of
`points_debited`, each non-zero only on its own reason's row. They exist so a
window total is a plain `SUM()` over every row in range with no row filter — the
timeseries resolver sums a bucket's rows, and asking it to filter to one reason
per series is not something a chart with two series can express. The residue
(`points_debited` minus those three) is the negative half of `admin_adjust`,
which is deliberately not given a column of its own: it is a correction, not a
programme mechanic, and naming it would invite it onto a chart.

The two columns that are NOT flows
----------------------------------
* **`points_outstanding_close` is a LEVEL.** It is the whole programme's
  outstanding points liability at the close of `bucket_date`, reconstructed as
  `SUM(delta)` over every ledger row written before the store-local end of that
  day. Never sum it across days: a window's value is the LATEST bucket in range,
  and thirty daily balances added together report thirty times the liability.
  The `_close` suffix is load-bearing — `analytics.metric_kind.classify` reads
  it and returns `LEVEL`, which is what makes `assert_summable` refuse.

  It is reconstructible for any past day (unlike `products.stock`, which is why
  `agg_inventory_daily` is forward-only) because the ledger is append-only: no
  row is ever updated or deleted in normal operation, so the sum below a
  timestamp is stable. It is NOT read from `customers.points_balance`, which is
  a mutable cached current value with no history and would be a fiction for any
  day but today.

* **`distinct_customers` is a DISTINCT count.** Members who moved points on this
  day under this reason. Not additive in either direction: summing a week
  double-counts anyone who transacted twice, and taking the latest day is just
  as wrong. A weekly figure must be recomputed from the ledger. The name is
  deliberately the one `metric_kind._DISTINCT_EXACT` already knows, so it
  classifies correctly without a hand-edit there.

The `'-'` row: store-level measures that have no reason
-------------------------------------------------------
`points_transactions.reason` is NOT NULL, so no real ledger row can ever produce
the `'-'` sentinel. That makes `'-'` unambiguous here, and this table uses it for
exactly one thing: the **store-level row**, carrying the measures that are
properties of the day rather than of a reason.

  * `referral_completions` — referrals that reached COMPLETED on this day.
  * `points_outstanding_close` — the programme-wide closing balance.

Both are written ONLY on the `'-'` row and are zero on every reason row, so a
window total over all rows counts each of them exactly once. Spreading either
across the reason rows would multiply it by the number of reasons that traded
that day, which is a factor that changes daily and would look like real
variation.

The `'-'` row is written for every bucket the job builds, including a day with
no ledger activity at all: a closing balance of 0 on a day when nobody has ever
earned a point is a measurement, and the outstanding liability is a real number
on every calendar day whether or not anyone transacted.

What this table deliberately CANNOT answer
------------------------------------------
**Redemptions by tier.** There is no tier reference anywhere on the redemption
path. `LoyaltyService.redeem` writes the ledger row with
`ref_type='coupon', ref_id=<coupon.id>`, and `coupons` has no `redemption_tier_id`
column — the only surviving trace of which `RedemptionTier` was traded is the
free-text `description` ("Redeemed: {tier.name}") on the ledger row and on the
minted coupon. Recovering a dimension by parsing a human-readable label would
re-partition history the moment a tier is renamed, and would merge two tiers
whose names share a prefix, while looking exactly like a measurement. So there
is no `tier` dimension and no `redemptions_by_tier` column here.

Matching on `cost_points` is not a substitute either: `-delta` on a REDEEM row
does equal the tier's `cost_points` at the moment of redemption, but two tiers
may legitimately cost the same, and a tier's price may be edited afterwards. The
honest fix is a `redemption_tier_id` column on `points_transactions` (or on
`coupons`), captured at redemption time and forward-only from that day. Until
then this is an unanswered question, not an approximated one.

**Points-to-money.** A point has no stored monetary value. The coupon a
redemption mints carries a `discount_type`/`discount_value`, but what it is
finally worth depends on the order it is applied to, and that lands in
`agg_promo_daily.discount_amount` where it belongs. There is no money column in
this table and there should not be one.

**Referral order value.** `referrals.completed_order_id` names the order that
completed a referral, so a referral-attributed revenue figure is joinable in
principle — but it is order-level money, it belongs to the revenue bridge's
definition set, and putting a second revenue number in the loyalty rollup is how
two screens end up disagreeing under the same word. `referral_completions` is a
count here; the money stays in the order rollups.
"""
from __future__ import annotations

from sqlalchemy import Index, UniqueConstraint
from sqlalchemy.orm import Mapped

from app.models.analytics_base import (
    BigIDMixin,
    RollupMixin,
    count_column,
    dimension_column,
)
from app.models.base import Base

__all__ = ["AggLoyaltyDaily"]


class AggLoyaltyDaily(Base, BigIDMixin, RollupMixin):
    """Points movement per store-local day x ledger reason. See module docstring.

    Written by ``analytics.aggregation.jobs_loyalty.LoyaltyDailyJob`` under
    Pattern B (delete-and-reinsert), because which reasons traded on a day is
    discovered rather than declared and a reason must be able to leave the
    bucket when its only transaction is backed out.
    """

    __tablename__ = "agg_loyalty_daily"

    #: The ledger `reason` as its public lowercase value (`place_order`,
    #: `redeem`, ...), or the `'-'` sentinel for the store-level row. In the
    #: UNIQUE key, so NOT NULL — a NULL here would not collide on MySQL and the
    #: next run would insert a second row for the same reason instead of
    #: replacing it.
    reason: Mapped[str] = dimension_column(32)

    # ---- Flows: additive across days AND across reasons -------------------
    #: Points created in this bucket: SUM(delta) over rows with delta > 0.
    #: NON-NEGATIVE by construction. This is issuance — a liability we took on.
    points_earned: Mapped[int] = count_column()
    #: Points removed in this bucket: SUM(-delta) over rows with delta < 0.
    #: NON-NEGATIVE by construction. Every negative movement, whatever caused it.
    points_debited: Mapped[int] = count_column()
    #: Subset of `points_debited` on the `redeem` row. Zero elsewhere.
    points_redeemed: Mapped[int] = count_column()
    #: Subset of `points_debited` on the `expiry` row. Zero elsewhere.
    points_expired: Mapped[int] = count_column()
    #: Subset of `points_debited` on the `refund_reversal` row. Zero elsewhere.
    points_reversed: Mapped[int] = count_column()
    #: SUM(delta), SIGNED — measured directly, not derived. The identity
    #: `points_earned - points_debited = net_points` is a check, and the job
    #: warns when it fails rather than silently correcting either side.
    net_points: Mapped[int] = count_column()
    #: Ledger rows in this bucket. On the `redeem` row this is the redemption
    #: count; on `expiry` it is the number of expiry entries the job minted.
    transactions: Mapped[int] = count_column()

    # ---- NOT additive -----------------------------------------------------
    #: Distinct members who moved points here. A DISTINCT count: never summed
    #: across days or across reasons — recompute from the ledger instead.
    distinct_customers: Mapped[int] = count_column()

    # ---- Store-level: written ONLY on the `'-'` row -----------------------
    #: Referrals that reached COMPLETED on this day (`referrals.completed_at`).
    #: A flow, and counted once per day because it lives on one row only.
    referral_completions: Mapped[int] = count_column()
    #: Programme-wide outstanding points at the CLOSE of this day. A **LEVEL**:
    #: a window's value is the latest bucket in range, never the sum of its
    #: days. SIGNED — a ledger with more debits than credits below the cutoff is
    #: reported as measured rather than clamped to zero.
    points_outstanding_close: Mapped[int] = count_column()

    __table_args__ = (
        UniqueConstraint(
            "bucket_date", "reason", "tz_generation", name="uq_agg_loyalty_daily_key"
        ),
        Index("ix_agg_loyalty_daily_bucket_date_reason", "bucket_date", "reason"),
    )
