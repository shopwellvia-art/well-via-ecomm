"""Shared column conventions for every analytics table.

The analytics schema is written by several hands, so the decisions that are
expensive to get wrong live here once instead of being re-derived per table.
Read this before adding an analytics model.

Why analytics tables differ from the transactional ones:

* **BigInteger primary keys.** Fact and queue tables accumulate a row per line
  item / event / bucket-dimension and are never pruned as aggressively as you
  expect. `payment_events` and `obs_request_logs` already set this precedent.

* **No foreign keys.** Deliberate, and the same reasoning `observability.py`
  uses: rollups must be independently truncatable and rebuildable. An FK to
  `products` would block a product delete and would make "recompute 2026-03"
  a referential-integrity problem instead of a DELETE + INSERT. Identity is
  snapshotted into the fact row instead (see `analytics_order_line`), so a
  deleted product does not erase history.

* **`DECIMAL(14,2)` for money, wider than the transactional `Numeric(12,2)`.**
  Daily and monthly sums accumulate well past a single order's magnitude.
  Never `Float` — binary floating point cannot represent 0.10 and margin
  arithmetic here feeds financial reporting.

* **Sums and counts, never stored averages.** An average of averages is wrong
  the moment a daily bucket is re-bucketed to a week. Store
  `sum_delivery_seconds` + `n_delivery` and divide at query time. This is the
  single most common rollup defect and it is silent.

* **Dimension columns are NOT NULL with a `'-'` sentinel.** MySQL permits many
  NULLs under a UNIQUE index, so a nullable `gateway` or `courier` column
  silently defeats the idempotency key and the job double-counts on its next
  run. `COALESCE` at write time; `'-'` means "not applicable / unknown".

* **`tz_generation` on every date-bucketed table.** Reporting days are computed
  in the store's timezone. Changing that timezone re-buckets everything, so
  each row records which generation produced it and queries refuse to mix two.
  Without this a timezone change silently corrupts history.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, Integer, Numeric, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

#: Money precision for analytics aggregates. Wider than the transactional
#: Numeric(12,2) because sums accumulate.
MONEY = Numeric(14, 2)

#: Ratios/percentages that must be stored rather than derived (rare — prefer
#: storing numerator + denominator and dividing at query time).
RATE = Numeric(9, 4)

#: Sentinel for an unknown/not-applicable dimension value. Never NULL — see the
#: module docstring for why NULL breaks UNIQUE-key idempotency on MySQL.
DIMENSION_UNKNOWN = "-"


class BigIDMixin:
    """BigInteger surrogate key. Analytics tables outgrow INT."""

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)


class CreatedAtMixin:
    """Append-only tables need only a creation stamp — they are never UPDATEd."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class RollupMixin:
    """Columns every date-bucketed rollup carries.

    `bucket_date` is a store-local reporting day, not a UTC day. `tz_generation`
    records which timezone generation computed it; a query spanning two
    generations must refuse rather than silently mix them.

    `computed_at` is when the aggregation job last wrote this row, and is what
    the API surfaces as the view's `last_updated_at`.
    """

    bucket_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    tz_generation: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="1"
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


def dimension_column(length: int = 64, **kw) -> Mapped[str]:
    """A NOT NULL dimension column defaulting to the '-' sentinel.

    Use for every column that participates in a rollup's UNIQUE key (gateway,
    courier, coupon code, payment method, state...). Writers must COALESCE the
    source value so a NULL from the transactional table becomes '-' rather than
    a row that silently escapes the idempotency key.
    """
    return mapped_column(
        String(length),
        nullable=False,
        server_default=DIMENSION_UNKNOWN,
        **kw,
    )


def money_column(**kw) -> Mapped[Decimal]:
    """A non-null money column defaulting to 0.

    Zero here means "measured, and it was zero" — it is only ever written by an
    aggregation job that actually summed the source rows. A metric that is
    *unknown* is represented by a quality label and coverage percentage in the
    API envelope, never by writing 0 into a rollup.
    """
    return mapped_column(MONEY, nullable=False, server_default="0", **kw)


def count_column(**kw) -> Mapped[int]:
    """A non-null integer counter defaulting to 0. Same semantics as money_column."""
    return mapped_column(Integer, nullable=False, server_default="0", **kw)


def seconds_column(**kw) -> Mapped[int]:
    """Accumulated duration in whole seconds.

    Always pair with a matching `count_column` (e.g. `sum_delivery_seconds` +
    `n_delivery`) so the average can be recomputed correctly at any granularity.
    Never store the average itself.
    """
    return mapped_column(BigInteger, nullable=False, server_default="0", **kw)
