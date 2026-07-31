"""Marketing spend, recorded the way a store actually knows it.

Why this table exists
---------------------
`analytics_cost_rules` can already express marketing spend — `CostType.
MARKETING_SPEND` with `CostUnit.PER_MONTH` — and `margin.py` reads exactly that
for CM3 and CAC. So why a second table?

Because a cost *rule* answers "what rate was in force?", and marketing spend is
not a rate. It is a **measurement of money that was actually spent**, and it has
three properties a rule cannot carry:

1. **A channel and a campaign.** A blended store-level number gives you CM3 but
   it cannot give you per-channel ROAS, and per-channel ROAS is the entire
   reason anyone asks for this data. A cost rule's `scope_value` is one column
   and its precedence ladder (`CostScope.PRECEDENCE`) has no marketing axis.
2. **Two grains.** A store that runs its own ads knows Meta spend day by day. A
   store whose agency sends one invoice knows "₹40,000 on Meta in June" and
   nothing finer. Both are real, both are usable, and forcing the second into a
   per-day rate would either fabricate precision or lose the number entirely.
3. **A provenance that changes later.** The first figure is typed by a human
   from an invoice. Months later an ads API backfills the same month with the
   platform's own number. That is an *upsert onto the same key*, not a new
   effective-dated rule — the June figure did not change on the day the API was
   connected, our knowledge of it did.

The cost rule stays the right home for "packaging costs ₹8 per order from
2026-03-01". This table is the right home for "we spent ₹40,000 on Meta in
June". They are not competing designs; `analytics_cost_rules` is a rate card and
this is a ledger of observations.

Conventions
-----------
Everything in `analytics_base.py` applies and for the same reasons:

* **BigInteger PK, no foreign keys.** A channel is a string, not a row; there is
  nothing to reference. Deleting a user must not take an entered spend figure
  with it, so `entered_by_user_id` is a plain nullable int exactly as
  `analytics_cost_rules.created_by_user_id` and `audit.py:target_id` are.
* **`DECIMAL(14,2)`.** Never `Float`: this number is subtracted from revenue to
  produce contribution, and binary floating point cannot represent 0.10.
* **`'-'` sentinel on every dimension in the UNIQUE key.** MySQL permits
  unlimited NULLs under a UNIQUE index, so a nullable `campaign` would silently
  disable the idempotency key and an ads-API sync would insert a second June/Meta
  row on every run instead of replacing the first. `'-'` in `campaign` is a real
  value: it means "channel total, not attributed to a campaign".
* **`tz_generation`.** Reporting days are store-local. A daily spend row is
  pinned to a store-local day, so a reporting-timezone change re-buckets it like
  everything else and a query must not mix two generations.

What is deliberately NOT here
-----------------------------
No clicks, impressions or conversions. Those only exist inside the ad platform
and cannot be typed in usefully; a hand-entered impression count would look like
measurement and be fiction. This table carries the one number a human can
actually supply from an invoice — money — and leaves the rest to the API build.

No `effective_from`/`effective_to`. Spend is not effective-dated: it is a
measurement of a period that already happened. Correcting a wrong figure is an
UPDATE, because the June total was always what it was and the earlier row was
simply wrong — unlike a rate, where the old value was correct *at the time* and
must survive. The audit event and the recompute enqueue carry the change; see
`endpoints/analytics_cost_admin.py`.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import Date, Index, Integer, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.analytics_base import MONEY, BigIDMixin, dimension_column
from app.models.base import Base, TimestampMixin

__all__ = [
    "SpendGrain",
    "SpendQuality",
    "SpendSource",
    "AnalyticsMarketingSpend",
    "DayAmount",
    "month_bounds",
    "allocate_amount_across_days",
    "allocate_row_to_days",
]


class SpendGrain:
    """Which period one row measures. Plain varchar, never a DB enum.

    Recorded per row rather than inferred from the dates, because a monthly row
    for a month that has already ended and a daily row are otherwise
    indistinguishable in a 1-day February... and more importantly because the
    grain is what decides whether a daily number is `ACTUAL` or `ALLOCATED`. A
    reader must never have to re-derive that from the calendar.
    """

    #: `period_start == period_end`; one store-local reporting day.
    DAILY = "daily"
    #: `period_start` is the 1st, `period_end` the last day of the same month.
    MONTHLY = "monthly"

    ALL = (DAILY, MONTHLY)


class SpendQuality:
    """How much the figure is worth trusting.

    Mirrors `analytics_control.CostQuality` deliberately — the same four grades,
    the same meanings — so an operator does not learn two vocabularies for the
    same idea. It is a separate class only because importing the cost-rule
    vocabulary into a table that is not a cost rule would suggest a coupling
    that does not exist.
    """

    #: Reconciled against the ad platform's own billing / an agency invoice.
    ACTUAL = "actual"
    #: From a signed retainer or contracted media plan.
    CONTRACTED = "contracted"
    #: Derived by the system from historical averages.
    ESTIMATED = "estimated"
    #: A human typed a plausible number. Better than nothing, worse than a bill.
    ASSUMED = "assumed"

    ALL = (ACTUAL, CONTRACTED, ESTIMATED, ASSUMED)


class SpendSource:
    """Where the row came from. Free text, but these two are the ones that matter.

    `source` is NOT part of the UNIQUE key on purpose: when an ads API later
    delivers the real number for a month a human already estimated, that is an
    upsert onto the same (grain, period, channel, campaign) key which replaces
    the estimate *and* its source. Two rows disagreeing about June would be
    strictly worse than one row that got better.
    """

    #: Typed into the admin UI by a person.
    MANUAL = "manual"
    #: Delivered by an ad-platform integration (not built yet).
    API = "api"


class AnalyticsMarketingSpend(Base, BigIDMixin, TimestampMixin):
    """Money spent on marketing, per channel/campaign, daily or monthly.

    One row is one observation of one period. `(grain, period_start, channel,
    campaign, tz_generation)` is the idempotency key: re-entering June/Meta
    replaces the figure rather than adding to it, which is what makes an ads-API
    sync safe to run repeatedly and what stops a double-click in the admin form
    from doubling the store's reported spend.

    Reading it for a daily view
    ---------------------------
    Use `allocate_row_to_days`. A DAILY row contributes its own amount to its own
    day. A MONTHLY row is spread across the days of its month by
    `allocate_amount_across_days`, which distributes to the paisa with no
    rounding drift — and every day it produces is labelled **ALLOCATED**, never
    ACTUAL, because "we spent ₹1,333.33 on Meta on 12 June" is an inference this
    module made, not something anyone observed. Presenting it as ACTUAL would
    make a monthly lump indistinguishable from a real daily feed, and the whole
    point of recording the grain is that they are not the same evidence.
    """

    __tablename__ = "analytics_marketing_spend"

    # SpendGrain constant. Inside the UNIQUE key so a daily row and a monthly row
    # can coexist for the same month — a store may know Meta daily and Google
    # only monthly, and neither should block the other.
    grain: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=SpendGrain.DAILY
    )

    # Store-local reporting days, same calendar as `bucket_date` on every rollup.
    # Both bounds are stored and both are inclusive: a monthly row carries the
    # 1st and the last day of its month, so allocation never has to re-derive the
    # month length and a range query never has to guess the grain.
    period_start: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    # 'meta', 'google', 'affiliate'... NOT NULL with the '-' sentinel: it sits
    # inside the UNIQUE key, and a NULL there would let MySQL accept unlimited
    # duplicate rows for the same month. '-' means "all marketing, unattributed",
    # which is a legitimate entry for a store that only knows one total.
    channel: Mapped[str] = dimension_column(48)

    # Campaign / ad set within the channel. '-' means the channel total.
    campaign: Mapped[str] = dimension_column(96)

    # The money. No server default: for a rollup, 0 means "summed the source rows
    # and they came to zero"; for a figure a human is supposed to supply, an
    # omitted amount is NOT zero, and a column that quietly defaults it to 0
    # would report free advertising — the one direction of error nobody
    # investigates. The database refuses the row instead.
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)

    # ISO-4217. Spend is invoiced in the platform's billing currency; a store
    # billed in USD must not have that silently summed with rupees.
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default="INR"
    )

    # SpendQuality constant. Rides through to the metric quality label so a
    # margin built on a typed estimate is never presented like one built on a
    # settled invoice.
    quality: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=SpendQuality.ASSUMED
    )

    # SpendSource constant or a specific integration id ('meta_ads_api'). This is
    # the column that distinguishes a number a person typed from one an API
    # delivered later, which is the difference between "we think" and "we know".
    source: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=SpendSource.MANUAL
    )

    # Free text: the invoice number, the agency, why the figure was revised.
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Who typed it. Plain int, no FK — the record of who entered a financial
    # figure must outlive the account, same reasoning as audit.py.
    entered_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Which reporting-timezone generation these day boundaries were computed
    # under. In the UNIQUE key so a tz rebuild can write the same periods under a
    # new generation without colliding with the live rows.
    tz_generation: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="1"
    )

    __table_args__ = (
        # The idempotency key. Every column in it is NOT NULL — see the module
        # docstring for why that is a correctness requirement on MySQL and not
        # tidiness.
        UniqueConstraint(
            "grain",
            "period_start",
            "channel",
            "campaign",
            "tz_generation",
            name="uq_analytics_marketing_spend_key",
        ),
        # The read path: "all spend overlapping [from, to]". Both bounds, because
        # a monthly row starting before the window can still overlap it.
        Index(
            "ix_analytics_marketing_spend_period",
            "period_start",
            "period_end",
        ),
        # The other read path: one channel's spend over time, for ROAS by channel.
        Index(
            "ix_analytics_marketing_spend_channel_period",
            "channel",
            "period_start",
        ),
    )


# ===========================================================================
# Allocation — pure arithmetic, no session, no I/O
# ===========================================================================
@dataclass(frozen=True)
class DayAmount:
    """One store-local day's share of a spend row.

    `allocated` is the whole point: True means this number was derived by
    spreading a lump, and the caller must label the resulting metric ALLOCATED
    rather than ACTUAL. It is a separate flag rather than a recomputed
    `grain == MONTHLY` check so a caller cannot forget the mapping.
    """

    day: date
    amount: Decimal
    allocated: bool


def month_bounds(any_day_in_month: date) -> tuple[date, date]:
    """First and last day of the month containing `any_day_in_month`.

    Used to normalise a monthly entry: an admin who types "June" and picks the
    12th means the whole of June, and storing 12 June -> 12 June as a MONTHLY row
    would allocate across the wrong days for the rest of the system's life.
    """
    first = any_day_in_month.replace(day=1)
    last_day = calendar.monthrange(any_day_in_month.year, any_day_in_month.month)[1]
    return first, any_day_in_month.replace(day=last_day)


def allocate_amount_across_days(
    amount: Decimal, period_start: date, period_end: date
) -> list[DayAmount]:
    """Spread `amount` over the inclusive day range, exactly and deterministically.

    Exactly
    -------
    The arithmetic is done in **paise (integer minor units)**, never in
    fractional rupees. ₹40,000 over 30 days is ₹1,333.333...; rounding each day
    to ₹1,333.33 and summing gives ₹39,999.90 and the store's June marketing
    spend is ten paise short — small, invisible, and permanently wrong in a
    number that is subtracted from revenue. Here the total is converted to paise,
    integer-divided, and the remainder distributed one paisa at a time, so
    ``sum(d.amount for d in result) == amount`` holds for every input. The test
    suite asserts that identity rather than trusting this comment.

    Deterministically
    -----------------
    The remainder goes to the **earliest** days, always. Any rule works as long
    as it is fixed; what must never happen is two runs producing different daily
    splits of the same month, because a recompute would then silently change a
    historical daily chart with no rule change behind it.

    A range of one day returns the whole amount on that day. An inverted range
    returns `[]` — the caller has nothing to allocate, and inventing a day would
    put money in a bucket the row does not cover.
    """
    if period_end < period_start:
        return []

    days = [
        period_start + timedelta(days=offset)
        for offset in range((period_end - period_start).days + 1)
    ]
    n = len(days)

    # Quantise to paise first: the stored column is DECIMAL(14,2), so any extra
    # scale on the way in is not money the store can actually have spent.
    total_paise = int((amount * 100).to_integral_value())
    sign = -1 if total_paise < 0 else 1
    magnitude = abs(total_paise)

    base, remainder = divmod(magnitude, n)
    return [
        DayAmount(
            day=day,
            amount=Decimal(sign * (base + (1 if index < remainder else 0))) / 100,
            allocated=n > 1,
        )
        for index, day in enumerate(days)
    ]


def allocate_row_to_days(row: AnalyticsMarketingSpend) -> list[DayAmount]:
    """Daily contributions of one spend row.

    A DAILY row is one day at its own amount, `allocated=False` — it was
    observed, not inferred. A MONTHLY row is spread by
    :func:`allocate_amount_across_days` and every day comes back
    `allocated=True`.

    The `allocated` flag is derived from the **grain**, not from the day count,
    so a single-day month (there is no such thing, but a malformed row could
    claim one) still reports itself as an inference rather than being promoted to
    an observation by an accident of the calendar.
    """
    if row.grain == SpendGrain.DAILY:
        return [DayAmount(day=row.period_start, amount=row.amount, allocated=False)]

    spread = allocate_amount_across_days(row.amount, row.period_start, row.period_end)
    return [DayAmount(day=d.day, amount=d.amount, allocated=True) for d in spread]
