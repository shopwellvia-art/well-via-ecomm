"""The basket co-occurrence rollup: ``basket_pair_daily`` -> ``agg_basket_pair_daily``.

Pattern B (delete-and-reinsert), for the reason Pattern B exists: which pairs
occurred on a day is *discovered*, not declared, so a pair must be able to leave
a bucket when the only order containing it is deleted. An upsert would leave
that pair sitting in the cross-sell table forever at its last known count,
recommending a basket nobody ever bought. The two write patterns and their
helpers are documented in :mod:`app.services.analytics.aggregation.jobs` and are
imported rather than restated.

The source is the line fact, and that is not a preference
--------------------------------------------------------
Pairs are read from ``analytics_order_line``. Reading them from
``order_items ⋈ products`` would resolve the identity of a *historical* basket
through the *current* catalogue: rename a product and every past pair renames
with it, delete one and the pair vanishes although the affinity really happened.
The fact table snapshotted ``product_name_snapshot`` at the moment of sale
precisely so that cannot occur, and this job carries those snapshots straight
onto the rollup row so the view needs no catalogue join at all.

It also means bucketing is already done. ``analytics_order_line.bucket_date`` is
a store-local reporting day computed by ``timebox`` when the fact was written,
and it carries the ``tz_generation`` that produced it. This job selects on those
two columns rather than re-deriving a day from ``ordered_at``: re-deriving would
give two answers the moment the store's timezone changed, and the fact's answer
is the one every other rollup already agrees with. As a check rather than a
recomputation, the job verifies each line's ``ordered_at`` really does fall
inside the store-local window for its bucket, and warns if it does not — that is
the signature of rows bucketed under a different generation.

Three arithmetic invariants
---------------------------
**Canonical ordering.** Every pair is written with ``product_a_id <
product_b_id``. The writer sorts the order's product ids and walks ``i < j``, so
the invariant holds by construction rather than by convention. Without it {A,B}
and {B,A} are two rows under the UNIQUE key and every support figure is halved,
silently.

**A pair is per order, not per line.** Each order is reduced to a SET of product
ids before pairing, so three lines of the same product are one product, and no
product is ever paired with itself.

**Counts, never ratios.** Support, confidence and lift are computed at read
time from summed counts. Storing a daily lift would make a weekly lift
un-derivable: the average of daily lifts is not the lift of the union, and the
gap grows with the variance in daily volume.

The line cap, and why a skipped order is skipped everywhere
-----------------------------------------------------------
An order with N distinct products yields N·(N−1)/2 pairs. That is 3 for a
three-line order, 190 at the cap, and 780 for a forty-line one — a single bulk
or test order can outweigh a week of real trading and it does so quadratically.
``MAX_DISTINCT_PRODUCTS_PER_ORDER`` bounds it.

An order above the cap is dropped from **everything**: the pairs, the marginals
(``orders_with_a`` / ``orders_with_b``) and the denominator
(``total_orders_in_bucket``). Dropping it from the pairs but keeping it in the
denominators would depress every support figure in the bucket by an amount
nothing records; keeping it in the marginals but not the pairs would depress
lift the same way. One population, or the ratios mean nothing. The count of
what was dropped is written to ``orders_skipped_over_cap`` on every row of the
bucket, so the view can warn rather than quietly reporting a smaller business.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
from itertools import combinations
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.models.analytics_base import DIMENSION_UNKNOWN
from app.models.analytics_basket import AggBasketPairDaily
from app.models.analytics_facts import AnalyticsOrderLine
from app.services.analytics.aggregation.base import JobRunResult, register
from app.services.analytics.aggregation.jobs import (
    _as_utc,
    _bucket_window,
    _cap,
    _utcnow,
)

__all__ = [
    "BasketPairDailyJob",
    "MAX_DISTINCT_PRODUCTS_PER_ORDER",
    "MAX_PAIRS_PER_ORDER",
    "BASKET_INSERT_CHUNK",
    "PRODUCT_NAME_LENGTH",
]

#: Distinct products per order above which the order is skipped entirely.
#:
#: Chosen at 20 because the fan-out is quadratic and the cost of being wrong is
#: asymmetric. C(20,2) = 190 rows is the most any single order can contribute;
#: C(40,2) = 780 and C(100,2) = 4 950, so one imported bulk order could write
#: more rows than a month of real baskets and would dominate every affinity on
#: the page. Against that, a retail basket in this store is one to five distinct
#: products — 20 is several times the largest genuine basket observed, so the
#: cap does not touch normal trade.
#:
#: An order above it is almost certainly a wholesale, migration or test order,
#: and its "affinity" is not a shopping pattern anybody can merchandise
#: against: forty products bought together once tells you about a purchase
#: order, not about a customer. Skipping it is therefore both cheaper and more
#: honest than including it — and the skip is recorded rather than assumed away.
MAX_DISTINCT_PRODUCTS_PER_ORDER = 20

#: The most pairs one order can contribute, given the cap. Derived, never
#: hand-written, so changing the cap cannot leave a stale bound behind.
MAX_PAIRS_PER_ORDER = (
    MAX_DISTINCT_PRODUCTS_PER_ORDER * (MAX_DISTINCT_PRODUCTS_PER_ORDER - 1) // 2
)

#: Same reasoning as the inventory and snapshot jobs: one INSERT carrying every
#: pair of a busy day would grow with the square of the basket size and
#: eventually exceed `max_allowed_packet`.
BASKET_INSERT_CHUNK = 500

#: Matches `AggBasketPairDaily.product_a_name` / `products.name`. The shared
#: `_dim` helper truncates to 64, which is the width of a SKU, not of a product
#: name — truncating names there would merge two distinct products into one
#: label on the cross-sell table.
PRODUCT_NAME_LENGTH = 255


def _name(value: str | None) -> str:
    """COALESCE a product name snapshot to the `'-'` sentinel, at name width.

    NOT NULL like every other snapshotted string, so an unrecoverable identity
    groups with the other unknowns instead of scattering under an invented
    placeholder.
    """
    text = (value or "").strip()
    return text[:PRODUCT_NAME_LENGTH] if text else DIMENSION_UNKNOWN


class BasketPairDailyJob:
    """Unordered product pairs co-occurring in one order, per store-local day.

    Pattern B. DELETE by ``(bucket_date, tz_generation)`` then INSERT the
    freshly computed pairs, both inside the runner's single transaction. The
    DELETE runs unconditionally — including when the bucket produces no pairs at
    all, which is the case an upsert cannot express and the case that matters:
    cancel the only two-product order of a quiet day and its pair must leave the
    table, not linger at yesterday's count.

    Idempotent by construction: the bucket is rebuilt from the line facts every
    time, so a second run over the same bucket writes the same rows with the
    same values. Nothing accumulates.

    What one run does, in order:

      1. read every ``analytics_order_line`` row for this ``(bucket_date,
         tz_generation)``;
      2. reduce each order to a SET of product ids (per order, not per line) and
         remember one name snapshot per product;
      3. drop orders above ``MAX_DISTINCT_PRODUCTS_PER_ORDER`` entirely, and
         count them;
      4. count, over the surviving orders: every pair (``pair_orders``), every
         product (``orders_with_a`` / ``orders_with_b``), the orders themselves
         (``total_orders_in_bucket``) and those holding more than one distinct
         product (``orders_with_any_pair``);
      5. write one row per pair with ``product_a_id < product_b_id``.
    """

    name = "basket_pair_daily"

    def run(self, db: Session, bucket_date: date, tz_generation: int) -> JobRunResult:
        start, end, _tz = _bucket_window(db, bucket_date)
        warnings: list[str] = []

        baskets, names, mis_bucketed = self._read_baskets(db, bucket_date, tz_generation, start, end)
        if mis_bucketed:
            warnings.append(
                f"basket_bucket_window_mismatch: {mis_bucketed} line fact(s) carry "
                f"bucket_date={bucket_date} and tz_generation={tz_generation} but an "
                "ordered_at outside that store-local day. They were still counted — "
                "the fact's own bucket is authoritative — but this is the signature "
                "of rows written under a different timezone generation"
            )

        eligible, skipped = self._apply_cap(baskets)
        if skipped:
            warnings.append(
                f"basket_order_over_cap: {skipped} order(s) on {bucket_date} held more "
                f"than {MAX_DISTINCT_PRODUCTS_PER_ORDER} distinct products and were "
                "excluded from this bucket entirely — pairs, marginals AND the "
                "denominator — so every ratio derived from it stays over one "
                f"population. Pair fan-out is quadratic ({MAX_PAIRS_PER_ORDER} rows at "
                "the cap), and an order that large is a bulk or migration order rather "
                "than a shopping basket"
            )

        pair_counts, marginals, orders_with_any_pair = self._count(eligible)
        total_orders = len(eligible)

        computed_at = _utcnow()
        rows: list[dict[str, Any]] = [
            {
                "bucket_date": bucket_date,
                "tz_generation": tz_generation,
                "computed_at": computed_at,
                "product_a_id": product_a,
                "product_b_id": product_b,
                "product_a_name": names.get(product_a, DIMENSION_UNKNOWN),
                "product_b_name": names.get(product_b, DIMENSION_UNKNOWN),
                "pair_orders": count,
                "orders_with_a": marginals[product_a],
                "orders_with_b": marginals[product_b],
                "total_orders_in_bucket": total_orders,
                "orders_with_any_pair": orders_with_any_pair,
                "orders_skipped_over_cap": skipped,
            }
            for (product_a, product_b), count in sorted(pair_counts.items())
        ]

        if skipped and not rows:
            # The skip count rides on the pair rows, so a bucket in which every
            # order was over the cap has nowhere to store it. Saying so is the
            # only place that fact can survive.
            warnings.append(
                f"basket_skip_unrecorded: {skipped} skipped order(s) on {bucket_date} "
                "produced no pair rows to carry orders_skipped_over_cap, so the "
                "exclusion is visible only in this run log"
            )

        # DELETE first, unconditionally — the empty-`rows` case is the whole
        # reason this is Pattern B: a pair whose only order was deleted must
        # leave the bucket, and an upsert would leave it behind at its old count.
        deleted = int(
            db.execute(
                delete(AggBasketPairDaily).where(
                    AggBasketPairDaily.bucket_date == bucket_date,
                    AggBasketPairDaily.tz_generation == tz_generation,
                )
            ).rowcount
            or 0
        )
        for index in range(0, len(rows), BASKET_INSERT_CHUNK):
            db.execute(
                mysql_insert(AggBasketPairDaily).values(
                    rows[index : index + BASKET_INSERT_CHUNK]
                )
            )

        return JobRunResult(
            rows_written=len(rows), rows_deleted=deleted, warnings=_cap(warnings)
        )

    # -- pieces -----------------------------------------------------------
    @staticmethod
    def _read_baskets(
        db: Session,
        bucket_date: date,
        tz_generation: int,
        start: datetime,
        end: datetime,
    ) -> tuple[dict[int, set[int]], dict[int, str], int]:
        """`{order_id: {product_id}}`, one name per product, and a sanity count.

        Selecting on ``bucket_date`` + ``tz_generation`` rather than on a UTC
        range over ``ordered_at`` is deliberate: the fact table already did the
        store-local bucketing with ``timebox``, and re-deriving it here would
        give a second answer the moment the reporting timezone changed. The
        window is still computed, but only to *check* the fact's bucketing —
        a line whose ``ordered_at`` falls outside its own bucket's store-local
        day is counted and reported, never silently dropped.

        A set per order, not a list: two lines of the same product are one
        product in one basket, so the deduplication happens here rather than
        being corrected for later, and a self-pair becomes unrepresentable
        rather than merely unwanted.
        """
        rows = db.execute(
            select(
                AnalyticsOrderLine.order_id,
                AnalyticsOrderLine.product_id,
                AnalyticsOrderLine.product_name_snapshot,
                AnalyticsOrderLine.ordered_at,
            )
            .where(
                AnalyticsOrderLine.bucket_date == bucket_date,
                AnalyticsOrderLine.tz_generation == tz_generation,
            )
            # Deterministic: the last line wins the name snapshot, so a product
            # renamed mid-day resolves the same way on every re-run.
            .order_by(AnalyticsOrderLine.id)
        ).all()

        baskets: dict[int, set[int]] = defaultdict(set)
        names: dict[int, str] = {}
        mis_bucketed = 0
        for row in rows:
            baskets[int(row.order_id)].add(int(row.product_id))
            names[int(row.product_id)] = _name(row.product_name_snapshot)
            moment = _as_utc(row.ordered_at)
            if not (start <= moment < end):
                mis_bucketed += 1
        return dict(baskets), names, mis_bucketed

    @staticmethod
    def _apply_cap(
        baskets: dict[int, set[int]]
    ) -> tuple[dict[int, set[int]], int]:
        """Split the baskets into those within the cap and a count of the rest.

        The skipped orders are not returned: nothing downstream may use them,
        because using them for the denominator while excluding them from the
        pairs is exactly the population mismatch the cap must not introduce.
        """
        eligible = {
            order_id: products
            for order_id, products in baskets.items()
            if len(products) <= MAX_DISTINCT_PRODUCTS_PER_ORDER
        }
        return eligible, len(baskets) - len(eligible)

    @staticmethod
    def _count(
        eligible: dict[int, set[int]]
    ) -> tuple[Counter, Counter, int]:
        """Pair counts, per-product marginals, and multi-product order count.

        ``combinations(sorted(products), 2)`` gives every unordered pair exactly
        once with ``a < b``, which IS the canonical ordering — it is not applied
        afterwards, so there is no path by which a {B,A} row could be written.
        A one-product basket yields no pairs and a three-product basket yields
        exactly three, both by construction.
        """
        pair_counts: Counter = Counter()
        marginals: Counter = Counter()
        orders_with_any_pair = 0

        for products in eligible.values():
            ordered = sorted(products)
            marginals.update(ordered)
            if len(ordered) < 2:
                continue
            orders_with_any_pair += 1
            pair_counts.update(combinations(ordered, 2))

        return pair_counts, marginals, orders_with_any_pair


register(BasketPairDailyJob())
