"""The GA4 traffic ROLLUP: ``agg_ga4_daily``, the landing table for the Data API.

Same contract as :mod:`app.models.analytics_rollups` — read that module's
docstring first; it is the normative statement of the conventions and none of
them are re-argued here. BigInteger PK, no ForeignKey anywhere,
``dimension_column()`` (NOT NULL, ``'-'`` sentinel) for every column in the
UNIQUE key, ``tz_generation`` inside that key, sums and counts and never a
stored average or a stored percentage.

This table lives in its own module rather than inside ``analytics_rollups.py``
so that several people can add rollups at once without three-way merges — the
same reason ``analytics_loyalty.py`` does. It is not a different kind of table.

Grain: one row per (store-local reporting day, channel group, source/medium,
device category, landing page, tz generation), pulled from one GA4 ``runReport``
per day by ``analytics.aggregation.jobs_ga4``.


This table is NOT a source of money, and cannot become one
==========================================================
There is no revenue column here and there must never be one. GA4's revenue is
whatever the browser tag managed to report: it loses 10-40% of purchases to ad
blockers, it double-counts against the server-side outbox unless
``transaction_id`` dedupes perfectly, and it is not the ledger. Revenue, orders
and margin come from ``agg_order_daily`` and the transactional tables, which are
the authoritative record of what the business actually took.

Consequently :attr:`AggGa4Daily.quality` is capped at ``ACTUAL`` and can never
be ``AUTHORITATIVE``. ``ACTUAL`` here means "GA4 genuinely observed this", which
is the strongest claim a behavioural measurement can make; ``AUTHORITATIVE`` is
reserved for the internal transactional record. The writer enforces the cap
(``ga4_data_api.quality_for``) and ``FORBIDDEN_QUALITIES`` names it so a test
can too.

What this table joins to is traffic *next to* revenue, never traffic *as*
revenue: sessions by channel here, orders by day there, and any conversion rate
computed at query time from the two — with the caveat that the denominator is
GA4's and the numerator is ours.


Every number here is provisional at first, and says so
======================================================
GA4 does not answer a day and then stand behind it. It answers immediately and
keeps revising for up to 48 hours as late hits arrive, sessions are unified and
cross-device identity resolves. A first read is genuinely useful and genuinely
not final, and the failure mode is silent: the day is ingested once, the number
hardens, the correction never lands, and nothing anywhere records that the
figure on screen is an early estimate of itself.

So every row carries :attr:`is_provisional` and :attr:`final_after`. A view
showing a provisional row must say so. The ingest job re-pulls the trailing
provisional window and *overwrites* (delete-and-reinsert, Pattern B), so a
re-ingest restates a day rather than doubling it.


Sampling and thresholding: a withheld row is not a zero
=======================================================
Two ways GA4's answer can be less than it looks, both recorded per row because
both are properties of the query that produced it:

:attr:`is_sampled` with :attr:`samples_read_count` / :attr:`sampling_space_size`
    GA4 answered from a subset of sessions and scaled up. The counts are stored
    and the ratio is not — a stored percentage cannot be re-aggregated, and this
    one is needed at whatever granularity the view asks for.

:attr:`is_thresholded`
    GA4 **withheld rows** to avoid identifying individuals. The rows are absent,
    not zero. A day's totals under thresholding are a **floor**, and a view that
    presents them as a total is wrong in the flattering-to-nobody direction and
    unfalsifiable from the data. Nothing in the ingest path fabricates a zero row
    for a dimension GA4 declined to return.

:attr:`data_loss_high_cardinality`
    The dimension combination overflowed GA4's cardinality limit and the tail was
    folded into an "(other)" bucket, so the breakdown does not add to the total.


Timezone: GA4's day is the PROPERTY's day
=========================================
``bucket_date`` is a store-local reporting day everywhere else in this schema,
computed from ``store.timezone`` through :mod:`app.services.analytics.timebox`.
GA4 has its own opinion: it buckets by the timezone configured on the *property*,
which an operator sets in a different console and frequently leaves on whatever
the account defaulted to.

When those disagree, a GA4 "2026-07-28" is not an IST 2026-07-28, and pretending
otherwise shifts a fraction of every day's traffic into the neighbouring bucket —
the exact defect ``timebox``'s module docstring exists to prevent, arriving
through a side door. There is no way to make ``runReport`` bucket by a foreign
timezone at ``date`` granularity, so this table does not silently reconcile.
It records:

* :attr:`property_timezone` — what GA4 said its day boundary was, per row.
* :attr:`ga4_date` — GA4's own ``YYYYMMDD`` label for the row, so the property's
  day is recoverable even though the row is filed under a store-local one.
* :attr:`tz_mismatch` — set when the two zones differ, so a view can refuse or
  warn rather than quietly present a 5.5-hour-shifted day.

The ingest job additionally emits a warning on every run while the mismatch
lasts, so it lands in ``analytics_sync_runs`` instead of only in a column
nobody queries.


One column here is NOT additive
===============================
:attr:`total_users` is a distinct count. Summing it across landing pages,
channels or days counts anyone who appears in two of them twice, and the query
succeeds and returns a plausible number.

``metric_kind.classify`` cannot currently tell: it is name-based, its
``_DISTINCT_EXACT`` set does not contain ``total_users``, and unknown names fall
through to ``FLOW`` — the dangerous direction. :data:`NON_ADDITIVE_COLUMNS` names
it explicitly so a resolver binding this table has something to consult, and
**adding ``"total_users"`` to ``metric_kind._DISTINCT_EXACT`` is a prerequisite
for binding it to any view**. Every other measure here is a genuine flow:
sessions, engaged sessions, page views and engagement seconds accumulate, and a
new user is counted exactly once, on one row, on the day of their first session.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.analytics_base import (
    BigIDMixin,
    RollupMixin,
    count_column,
    dimension_column,
    seconds_column,
)
from app.models.base import Base

__all__ = [
    "AggGa4Daily",
    "NON_ADDITIVE_COLUMNS",
    "FORBIDDEN_QUALITIES",
    "LANDING_PAGE_MAX_CHARS",
]

#: Columns on this table that must never be SUMmed across buckets or dimensions.
#: See "One column here is NOT additive" above. Exported so a resolver, a test or
#: the repository can check against it instead of re-deriving the judgement.
NON_ADDITIVE_COLUMNS: frozenset[str] = frozenset({"total_users"})

#: Quality grades this table may never carry. GA4 measures behaviour with a
#: browser tag; it is not the record of what the business took.
FORBIDDEN_QUALITIES: frozenset[str] = frozenset({"AUTHORITATIVE"})

#: Landing pages are truncated to this many characters before they are written.
#: GA4's `landingPagePlusQueryString` is unbounded in principle (utm parameters
#: stack up), and this column sits inside the UNIQUE key — at utf8mb4 the whole
#: key must stay under InnoDB's 3072-byte index limit, and a longer column would
#: put it there. The writer records how many rows it truncated as a warning
#: rather than silently collapsing two URLs into one row without saying so.
LANDING_PAGE_MAX_CHARS = 255


class AggGa4Daily(Base, BigIDMixin, RollupMixin):
    """Daily GA4 traffic and engagement, by channel, source/medium, device and
    landing page.

    Powers the traffic, landing-page, device and channel views. Every metric is
    a count or a sum; every rate a view needs is a division done at query time:

        bounce rate      = (sessions - engaged_sessions) / sessions
        engagement rate  = engaged_sessions / sessions
        avg engagement   = sum_engagement_seconds / n_engagement
        pages / session  = screen_page_views / sessions

    None of those are stored, for the reason ``analytics_base`` gives: a stored
    rate is an average of averages the moment a daily bucket is re-bucketed to a
    week, and it is wrong silently.
    """

    __tablename__ = "agg_ga4_daily"

    # -- dimensions ---------------------------------------------------------
    #: GA4 `sessionDefaultChannelGroup`: Organic Search, Paid Social, Direct,
    #: Referral, Email... GA4's own channel grouping, not one we invent, so the
    #: numbers reconcile against the GA4 UI a marketer is looking at.
    channel_group: Mapped[str] = dimension_column(64)
    #: GA4 `sessionSourceMedium`, e.g. `google / organic`. 128 because a source
    #: is frequently a full hostname and the medium adds a suffix.
    source_medium: Mapped[str] = dimension_column(128)
    #: GA4 `deviceCategory`: desktop, mobile, tablet, smart tv.
    device_category: Mapped[str] = dimension_column(32)
    #: GA4 `landingPagePlusQueryString`, truncated to LANDING_PAGE_MAX_CHARS.
    landing_page: Mapped[str] = dimension_column(LANDING_PAGE_MAX_CHARS)

    # -- measures -----------------------------------------------------------
    #: GA4 `sessions`. Additive.
    sessions: Mapped[int] = count_column()
    #: GA4 `engagedSessions` — 10s+, a conversion, or 2+ pageviews. Additive.
    #: Bounces are `sessions - engaged_sessions`, which is why no bounce column
    #: exists: GA4's `bounceRate` is a ratio and ratios are never stored.
    engaged_sessions: Mapped[int] = count_column()
    #: GA4 `totalUsers`. **NOT ADDITIVE** — see the module docstring and
    #: :data:`NON_ADDITIVE_COLUMNS`. Correct only at exactly this row's grain.
    total_users: Mapped[int] = count_column()
    #: GA4 `newUsers`. Additive: a user is new exactly once, on one row.
    new_users: Mapped[int] = count_column()
    #: GA4 `screenPageViews`. Additive.
    screen_page_views: Mapped[int] = count_column()

    #: GA4 `userEngagementDuration`, in whole seconds. Paired with `n_engagement`
    #: so the average is recomputable at any granularity — never stored as an
    #: average, which is the single most common rollup defect and it is silent.
    sum_engagement_seconds: Mapped[int] = seconds_column()
    #: The denominator for `sum_engagement_seconds`: the sessions this row's
    #: engagement was measured over. Equal to `sessions` today, and stored
    #: separately anyway, because the denominator is a property of the metric
    #: rather than of the row — exactly as `n_delivery` is stored beside
    #: `delivered` in `agg_geo_daily` even though it is usually the same number.
    n_engagement: Mapped[int] = count_column()

    # -- provenance and honesty --------------------------------------------
    #: GA4's own `date` dimension for this row, `YYYYMMDD`, as the *property's*
    #: timezone bucketed it. Kept so the property's day survives being filed
    #: under a store-local `bucket_date`. `'-'` when the report did not ask for
    #: the dimension.
    ga4_date: Mapped[str] = dimension_column(8)
    #: The property's configured reporting timezone, from the response metadata.
    property_timezone: Mapped[str] = dimension_column(64)
    #: The property's timezone differs from `store.timezone`, so this row's
    #: GA4 day is NOT the store-local day it is filed under. See the module
    #: docstring; a view must warn or refuse rather than present it as aligned.
    tz_mismatch: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="0"
    )

    #: GA4 may still revise this day. Set on ingest and cleared by a later
    #: re-ingest once `final_after` has passed.
    is_provisional: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="1"
    )
    #: The UTC instant after which GA4 is expected to have stopped revising this
    #: bucket — its day end in the property's timezone, plus
    #: `ga4_data_api.PROVISIONAL_HOURS`. Nullable only because a row ingested
    #: before the property timezone was known cannot compute it; it is outside
    #: the UNIQUE key, so a NULL here cannot defeat idempotency.
    final_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: GA4 answered from a sample and scaled the result up.
    is_sampled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="0"
    )
    #: `samplesReadCount` and `samplingSpaceSize` from the response. The two
    #: counts, never their ratio — a stored percentage cannot be re-aggregated.
    samples_read_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    sampling_space_size: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    #: GA4 withheld rows below its privacy threshold. The day's totals are a
    #: FLOOR, not a total. A missing row is not a zero row.
    is_thresholded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="0"
    )
    #: GA4's `dataLossFromOtherRow`: the dimension cardinality overflowed and the
    #: tail was folded into an "(other)" bucket, so the breakdown does not add up
    #: to the property total.
    data_loss_high_cardinality: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="0"
    )

    #: `MetricQuality` for this row: ACTUAL, ESTIMATED (sampled) or INCOMPLETE
    #: (rows withheld). Never AUTHORITATIVE — see :data:`FORBIDDEN_QUALITIES`.
    #: A string rather than an enum column so a new grade in `types.py` does not
    #: require an ALTER on a table with no other reason to change.
    quality: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="ACTUAL"
    )

    __table_args__ = (
        # The idempotency key. Every dimension in it is NOT NULL with a '-'
        # sentinel: MySQL permits unlimited NULLs under a UNIQUE index, so one
        # nullable dimension would let the nightly re-ingest of the provisional
        # window INSERT a second row instead of replacing the first, and the day
        # would double. At utf8mb4 this key is 1921 bytes, inside InnoDB's 3072.
        UniqueConstraint(
            "bucket_date",
            "channel_group",
            "source_medium",
            "device_category",
            "landing_page",
            "tz_generation",
            name="uq_agg_ga4_daily_key",
        ),
        # The channel breakdown is the most-read shape of this table (marketing
        # channel performance, and the traffic view's channel split), and it is
        # always bounded by a date range first.
        Index("ix_agg_ga4_daily_bucket_date_channel_group", "bucket_date", "channel_group"),
        # Every read of this table goes through `guard_tz_generation`, which runs
        # SELECT DISTINCT tz_generation ... WHERE bucket_date BETWEEN ?. Declared
        # here from the start rather than retrofitted the way
        # `agg_customer_snapshot`'s had to be (revision e1c5b7a04d92).
        Index("ix_agg_ga4_daily_bucket_date_tz_generation", "bucket_date", "tz_generation"),
    )
