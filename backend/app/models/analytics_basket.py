"""Market-basket co-occurrence rollup — which products are bought together.

One table, `agg_basket_pair_daily`, holding **unordered product pairs that
appeared in the same order**, per store-local reporting day. It is the physical
backing for view 57 (Product Bundling and Cross-Sell).

Read `analytics_base.py` first. BigInteger PK, no foreign keys, `'-'` dimension
sentinels, `tz_generation` in the UNIQUE key and "sums and counts, never stored
averages" are all decided there and are not re-litigated here.

It lives in its own module rather than in `analytics_rollups.py` for one
practical reason: that module's twelve tables are a fixed set several tests
count (`test_analytics_schema.py::test_expected_table_counts`), and a
co-occurrence table is a different kind of object anyway — its grain is a *pair*
of entities, which nothing else in the schema has.

The source is `analytics_order_line`, never `order_items ⋈ products`
--------------------------------------------------------------------
Pairs are read from the immutable line fact, which snapshots
`product_name_snapshot` at the moment of sale. Resolving pair identity through
the live catalogue would mean a product rename retroactively rewrote every
"frequently bought together" row that ever mentioned it, and a product delete
would erase the pair entirely — the affinity happened, and the fact table is
what remembers it. `product_a_name` / `product_b_name` are carried onto this
row for the same reason, and so the view renders with no catalogue join at all.

Four decisions that are expensive to get wrong
----------------------------------------------

**1. Canonical pair ordering.** `product_a_id < product_b_id`, always,
enforced by the writer. Without it {A,B} and {B,A} are two different rows under
the UNIQUE key, every support figure halves, and the table looks fine — there
is no error, just two half-sized rows where one whole one belongs. The
constraint is not expressible in MySQL as a CHECK that anything enforces
reliably, so it is the job's invariant and there is a test for it.

**2. A pair is per ORDER, not per line.** Two lines of the same product in one
order are one product in one basket, and a product is never paired with itself.
The writer deduplicates each order to a SET of product ids before pairing, so
`product_a_id == product_b_id` can never be written.

**3. Counts, never ratios.** Support, confidence and lift are RATIOs and are
recomputed at read time. A stored daily lift cannot be re-bucketed into a week:
lift is `P(A∩B) / (P(A)·P(B))`, and the average of seven daily lifts is not the
weekly lift — it is not even close when volumes differ between days. So four
counts are stored and the arithmetic happens where the window is known.

**4. The fan-out is bounded.** An order with N distinct products produces
N·(N−1)/2 pairs: 3 for a 3-line order, 190 for a 20-line one, 780 for a 40-line
one. One bulk order can therefore write more rows than a normal trading week.
`jobs_basket.MAX_DISTINCT_PRODUCTS_PER_ORDER` caps it, and an order above the
cap is skipped **entirely** — from the pairs, from the marginals and from the
bucket denominator alike — so every ratio derived from this table stays over one
population. The count of skipped orders is stored on the row so the view can say
so rather than quietly reporting a smaller business.

How a reader combines these columns
-----------------------------------
For a window, sum each count over the rows of ONE pair, then::

    support     = Σ pair_orders    / Σ total_orders_in_bucket
    confidence  = Σ pair_orders    / Σ orders_with_a
    lift        = (Σ pair_orders × Σ total_orders_in_bucket)
                  / (Σ orders_with_a × Σ orders_with_b)

All four sums must be taken over **the same rows** — the days on which that
pair co-occurred. `total_orders_in_bucket` and `orders_with_any_pair` are
bucket-level scalars repeated on every pair row of their day, so summing them
across a *day's* rows multiplies them by the number of pairs; that is the one
trap in this table and it is why they are documented per column below. A
window-wide order count (for an attach rate, say) is obtained by grouping on
the scalar itself, which yields one row per day, and summing those.
"""
from __future__ import annotations

from sqlalchemy import Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.analytics_base import (
    BigIDMixin,
    RollupMixin,
    count_column,
    dimension_column,
)
from app.models.base import Base

__all__ = ["AggBasketPairDaily"]


class AggBasketPairDaily(Base, BigIDMixin, RollupMixin):
    """One row per (day, unordered product pair) that co-occurred in an order.

    Answers: which two products end up in the same basket, how often, and
    whether that is more often than their individual popularity would predict.

    Grain is a PAIR, which is unlike every other rollup here, so two things that
    are usually implicit are worth saying:

    * `pair_orders` counts ORDERS, not lines and not units. An order containing
      three of A and one of B contributes exactly 1.
    * A product never pairs with itself. There is no row where
      `product_a_id == product_b_id`, and a self-pair would not mean anything —
      "bought with itself" is a quantity, which `agg_product_daily.units`
      already answers.

    **Every order in the bucket counts, whatever its status.** The line fact's
    `order_status` is a snapshot taken at ingestion and keeps moving afterwards
    (see `AnalyticsOrderLine`), so filtering on it would make a bucket depend on
    when ingestion happened to run rather than on what customers did. Both the
    numerator and the denominator here use the identical population, so a
    cancelled order neither inflates nor deflates lift — it widens the base
    slightly, and the view says so. This is a *basket composition* table, not a
    revenue table; no money column belongs on it, and there is none.
    """

    __tablename__ = "agg_basket_pair_daily"

    #: The lower product id of the pair. `product_a_id < product_b_id` ALWAYS —
    #: the writer sorts before pairing. Storing {A,B} and {B,A} as two rows
    #: would halve every support figure and nothing would raise.
    product_a_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The higher product id of the pair. Never equal to `product_a_id`.
    product_b_id: Mapped[int] = mapped_column(Integer, nullable=False)

    #: `product_name_snapshot` from the line fact, so the view renders without
    #: joining the live catalogue and a later rename does not rewrite history.
    #: NOT NULL with the `'-'` sentinel, matching every other snapshotted string
    #: in the schema, so an unrecoverable identity groups with the other unknowns
    #: instead of scattering under an invented placeholder.
    product_a_name: Mapped[str] = dimension_column(255)
    product_b_name: Mapped[str] = dimension_column(255)

    #: Orders in this bucket that contained BOTH products. The numerator of
    #: support and of confidence, and additive across days for one pair.
    pair_orders: Mapped[int] = count_column()

    #: Orders in this bucket that contained product A (regardless of B). The
    #: marginal, repeated on every row of this bucket that mentions A — so it is
    #: additive across DAYS for one pair, and must never be summed across the
    #: pair rows of a single day.
    orders_with_a: Mapped[int] = count_column()
    #: The same for product B.
    orders_with_b: Mapped[int] = count_column()

    #: Every order in this bucket that entered the co-occurrence universe,
    #: including single-product baskets — a basket that could have contained a
    #: pair and did not is exactly what makes support meaningful. Orders skipped
    #: for exceeding the line cap are NOT counted here, so numerator and
    #: denominator describe one population.
    #:
    #: A BUCKET-LEVEL SCALAR repeated on every pair row of its day. Additive
    #: across days (one row per day), never across a day's rows. To recover the
    #: window total, GROUP BY this column together with `bucket_date` — that
    #: yields one row per day — and sum those.
    total_orders_in_bucket: Mapped[int] = count_column()

    #: Orders in this bucket that contained at least two distinct products, i.e.
    #: contributed at least one pair. Paired with `total_orders_in_bucket` it is
    #: the attach rate's numerator/denominator; the rate itself is never stored.
    #: Same bucket-level-scalar rules as above.
    orders_with_any_pair: Mapped[int] = count_column()

    #: Orders excluded from this bucket entirely for exceeding
    #: `jobs_basket.MAX_DISTINCT_PRODUCTS_PER_ORDER` distinct products. Stored
    #: rather than only logged, because the view has to be able to warn: a
    #: bucket whose largest baskets were dropped reports affinities over a
    #: population that is not all of the trade, and a run-log warning nobody
    #: reads is not a disclosure. Same bucket-level-scalar rules as above.
    #:
    #: Known gap: a bucket in which EVERY order exceeded the cap produces no
    #: pair rows, and therefore has nowhere to carry this count. The job emits a
    #: run warning for exactly that case.
    orders_skipped_over_cap: Mapped[int] = count_column()

    __table_args__ = (
        # The idempotency key. The job deletes and reinserts by
        # (bucket_date, tz_generation), and this is what makes a re-run replace
        # a pair's row instead of adding a second one.
        UniqueConstraint(
            "bucket_date",
            "product_a_id",
            "product_b_id",
            "tz_generation",
            name="uq_agg_basket_pair_daily_key",
        ),
        # "the strongest pairs in this window" — the only query the view makes,
        # and a range scan on bucket_date alone would read every pair of every
        # day before sorting.
        Index("ix_agg_basket_pair_daily_bucket_pair_orders", "bucket_date", "pair_orders"),
        # "what is bought with product X". A pair mentioning X may store it in
        # either column, so both are indexed; one index cannot serve both halves
        # of an unordered pair.
        Index("ix_agg_basket_pair_daily_product_a", "product_a_id", "bucket_date"),
        Index("ix_agg_basket_pair_daily_product_b", "product_b_id", "bucket_date"),
    )
