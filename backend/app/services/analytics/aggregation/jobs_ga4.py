"""The GA4 traffic rollup: ``ga4_daily`` -> ``agg_ga4_daily``.

Registered here: one job, ``ga4_daily``. The two write patterns and their guard
rails are documented in :mod:`app.services.analytics.aggregation.jobs` and its
helpers are imported rather than restated. This is **Pattern B**
(delete-and-reinsert), because which channels, sources, devices and landing
pages a day produced is discovered from the answer, not declared in advance.

Every other job in this package reads the store's own database. This one reads
**Google**, through :mod:`app.services.analytics.ga4_data_api`, and that changes
four things — each of which is a way the obvious implementation would be quietly
wrong.


1. An outage must not look like a quiet day
===========================================
Pattern B deletes the bucket before it inserts. That is right when the source is
a local table, because "the rows went away" is a real answer. It is catastrophic
when the source is a third party: a 503 from Google would DELETE a day of real
traffic and insert nothing, and the chart would show an honest-looking zero.

So the delete happens **only after a successful report**, and every failure path
returns or raises before touching a row:

* not configured  -> no-op, warn, delete nothing (the common case: GA4 is not
  connected, and a store that has never configured it must not have its rollup
  emptied nightly);
* unavailable     -> raise, so the runner records a FAILED sync run, delete nothing;
* OK with no rows -> delete and insert nothing. This one *is* a real answer, and
  it is the only case where an empty bucket is allowed to mean "no traffic".

``ga4_data_api`` keeps those three apart at the type level precisely so this
function can branch on them. See its module docstring on ``ReportStatus``.


2. GA4 restates its own history, and nothing else can observe that
==================================================================
``queue.py``'s design rests on "the writer that caused a change enqueues the
bucket it invalidated". For GA4 the writer is Google: the figures for a day keep
moving for up to :data:`~app.services.analytics.ga4_data_api.PROVISIONAL_HOURS`
after it ends, and no code in this process can ever see it happen.

So this job is the only party that can know, and it enqueues the trailing
provisional window itself. The enqueue set is **strictly older** than the bucket
being run and is filtered to buckets that are still provisional, which is what
makes the cascade terminate instead of looping:

    run(D)   enqueues {D-1, D-2}   (still provisional)
    run(D-1) enqueues {D-2}
    run(D-2) enqueues {}           (D-3 is already final)

One nightly ``run_bucket("ga4_daily", today)`` therefore re-pulls the whole
provisional window through the queue, and a re-pull *restates* the day rather
than doubling it, because Pattern B replaces rather than accumulates.

:func:`provisional_window` exposes the same set for a scheduler that would
rather drive it directly (``run_window("ga4_daily", today - 2, today + 1)``).


3. A GA4 day is the PROPERTY's day
==================================
``bucket_date`` is store-local, from ``timebox``. GA4 buckets by the timezone
configured on the property, which lives in a different console and is regularly
left on whatever the account defaulted to. There is no way to ask ``runReport``
for a foreign day boundary at ``date`` granularity.

This job therefore **detects and records** rather than silently reconciling: it
compares the property timezone from the response metadata against
``store.timezone``, sets ``tz_mismatch`` on every row it writes, records the
property's own ``YYYYMMDD`` label in ``ga4_date``, and warns on every run while
the mismatch lasts so it lands in ``analytics_sync_runs`` and not only in a
column nobody queries. Treating a New York day as an IST day is exactly the
class of error ``timebox``'s docstring exists to prevent, and it would arrive
here through a side door.


4. A withheld row is not a zero
===============================
GA4 suppresses rows below a privacy threshold and folds high-cardinality tails
into an "(other)" bucket. Neither produces a zero — they produce an **absence**,
and the difference is invisible downstream unless it is recorded.

* Rows GA4 omitted cannot be counted from the response at all. The response-level
  ``subjectToThresholding`` flag is written to every row of the bucket as
  ``is_thresholded``, and the row's ``quality`` drops to ``INCOMPLETE``, so a view
  can warn that the day's totals are a **floor**.
* A row that comes back with a withheld metric cell (GA4 sends ``""``) is
  **skipped, not zero-filled**. There is no NOT NULL integer that can hold
  "unknown", and writing 0 is the precise conversion this whole module refuses
  to make. The count of skipped rows rides back as a warning.
* Nothing here ever fabricates a row for a dimension GA4 did not return.

The residual gap is stated plainly: if GA4 withholds *every* row for a day, this
job writes nothing, and an empty bucket is what a genuinely quiet day also looks
like. The warning (prefixed ``ga4_all_rows_suppressed``) is the only trace, and
it is in ``analytics_sync_runs.error`` rather than in the rollup. Closing that
would need a per-day marker row or a nullable-metric representation, and both
are worse than the gap they fix — a marker row of zeros is the fabricated zero
this section exists to forbid.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy import delete
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.core.config import settings as env_settings
from app.models.analytics_base import DIMENSION_UNKNOWN
from app.models.analytics_ga4 import LANDING_PAGE_MAX_CHARS, AggGa4Daily
from app.services.analytics.aggregation.base import JobRunResult, register
from app.services.analytics.aggregation.jobs import _cap, _utcnow
from app.services.analytics.ga4_data_api import (
    PROVISIONAL_HOURS,
    Ga4Report,
    ReportRequest,
    ReportStatus,
    final_after,
    load_data_api_config,
    run_report_all,
)
from app.services.analytics.timebox import store_timezone

__all__ = [
    "Ga4DailyJob",
    "Ga4IngestUnavailable",
    "DIMENSIONS",
    "METRICS",
    "JOB_NAME",
    "PROVISIONAL_AGE_DAYS",
    "REINGEST_WINDOW_DAYS",
    "INSERT_CHUNK",
    "provisional_window",
    "is_provisional",
]

log = logging.getLogger("analytics.aggregation.ga4")

#: The registry name. A constant because the queue and the sync-run log both key
#: on it, so a typo in one of three string literals would silently orphan work.
JOB_NAME = "ga4_daily"

#: The report's dimensions, in the order their values arrive. `date` is included
#: even though the range is a single day: it is GA4's own label for the bucket in
#: the PROPERTY's timezone, and keeping it is what makes a timezone mismatch
#: provable from the stored row rather than merely suspected.
DIMENSIONS: tuple[str, ...] = (
    "date",
    "sessionDefaultChannelGroup",
    "sessionSourceMedium",
    "deviceCategory",
    "landingPagePlusQueryString",
)

#: The metrics, in the order their values arrive. Six of GA4's ten-per-report
#: budget, and deliberately no revenue metric: GA4's revenue is browser-tag
#: revenue and this stack takes money from the transactional tables only.
METRICS: tuple[str, ...] = (
    "sessions",
    "engagedSessions",
    "totalUsers",
    "newUsers",
    "screenPageViews",
    "userEngagementDuration",
)

#: A bucket is provisional until it is this many store-local days old. Derived
#: from `PROVISIONAL_HOURS`: a day D closes at the start of D+1, and GA4 may
#: revise it for 48 hours after that, so it is final from D+3 onward.
PROVISIONAL_AGE_DAYS = 1 + PROVISIONAL_HOURS // 24

#: How many trailing buckets a scheduler should re-pull. The provisional ones
#: (ages 0..2) plus the first final one (age 3), because that last pull is what
#: writes `is_provisional = False`.
REINGEST_WINDOW_DAYS = PROVISIONAL_AGE_DAYS + 1

#: Rows per INSERT. One statement carrying an entire day's landing-page
#: cross-product would grow with the catalogue and eventually exceed MySQL's
#: `max_allowed_packet`; the same reasoning as `INVENTORY_INSERT_CHUNK`.
INSERT_CHUNK = 500

#: GA4's placeholder for a dimension value it could not attribute. Mapped to the
#: schema's own `'-'` sentinel so "unknown" has exactly one spelling in the
#: table, rather than two that no GROUP BY will ever combine.
_GA4_UNKNOWN_VALUES = frozenset({"", "(not set)", "(not provided)", "(none)"})


class Ga4IngestUnavailable(RuntimeError):
    """GA4 was configured, was asked, and did not answer.

    Raised rather than returned so the runner writes a FAILED
    ``analytics_sync_runs`` row with the reason in ``error``. Returning a
    zero-row success instead would advance the watermark over a day that was
    never ingested, and nothing downstream could ever tell that day from a
    quiet one — which is the entire failure mode this module is built against.
    """


# ===========================================================================
# Provisional-window policy
# ===========================================================================
def is_provisional(bucket: date, today: date) -> bool:
    """Whether GA4 may still revise ``bucket`` as of ``today`` (store-local)."""
    return (today - bucket).days < PROVISIONAL_AGE_DAYS


def provisional_window(today: date) -> tuple[date, ...]:
    """Every bucket GA4 may still revise as of ``today``, newest first.

    ``(today, today-1, today-2)`` under the 48-hour policy. Exposed so a
    scheduler can drive the re-pull directly instead of relying on the queue
    cascade described in the module docstring — the two are equivalent and
    either is fine, but doing both is just extra queue rows.
    """
    return tuple(
        today - timedelta(days=offset) for offset in range(PROVISIONAL_AGE_DAYS)
    )


# ===========================================================================
# Helpers
# ===========================================================================
def _dimension(value: str | None, length: int) -> str:
    """COALESCE a GA4 dimension value to the `'-'` sentinel, bounded to `length`.

    `jobs._dim` is not reused because it hard-codes 64 characters and landing
    pages need 255. The `'-'` mapping is the load-bearing part and it is
    identical: a NULL (or, here, GA4's `(not set)`) inside a UNIQUE key does not
    collide on MySQL, so it would silently defeat the idempotency key and the
    nightly provisional re-pull would insert a second row instead of replacing
    the first.
    """
    text = (value or "").strip()
    if text in _GA4_UNKNOWN_VALUES:
        return DIMENSION_UNKNOWN
    return text[:length] if text else DIMENSION_UNKNOWN


def _tz_offset_hours(tz_name: str, moment: date) -> float | None:
    """The zone's UTC offset in hours on that date, or None if unresolvable."""
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    reference = datetime.combine(moment, datetime.min.time(), tzinfo=tz)
    offset = reference.utcoffset()
    return None if offset is None else offset.total_seconds() / 3600.0


# ===========================================================================
# ga4_daily -> agg_ga4_daily            (Pattern B: delete-and-reinsert)
# ===========================================================================
class Ga4DailyJob:
    """Pull one store-local day of GA4 traffic and land it in ``agg_ga4_daily``.

    Reads nothing from the local database except the credentials, the store
    timezone and the bucket it is about to replace. Writes nothing when GA4 is
    not connected, and deletes nothing unless GA4 answered.
    """

    name = JOB_NAME

    def __init__(
        self,
        *,
        transport: "httpx.BaseTransport | None" = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """``transport`` and ``sleep`` are the seams the tests use.

        Production constructs this with neither — the instance in the ``JOBS``
        registry at the bottom of this module is the real one. Injecting them at
        construction rather than threading them through ``run()`` keeps the
        ``AggregationJob`` protocol (``run(db, bucket_date, tz_generation)``)
        exactly as every other job implements it, which is what lets the runner,
        the queue and the admin API stay ignorant of the difference between a
        job that reads MySQL and one that reads Google.
        """
        self._transport = transport
        self._sleep = sleep

    def run(self, db: Session, bucket_date: date, tz_generation: int) -> JobRunResult:
        warnings: list[str] = []

        if not env_settings.ANALYTICS_ROLLUPS_ENABLED:
            # Belt to the runner's braces. The worker refuses to start with this
            # flag off, but `run_bucket` is also reachable from the admin API and
            # from a shell, and a job that reached out to Google with the whole
            # subsystem disabled would be a surprising thing to discover in an
            # egress log.
            return JobRunResult(
                warnings=(
                    "ga4_rollups_disabled: ANALYTICS_ROLLUPS_ENABLED is false, so "
                    "no GA4 Data API call was made and no row was touched.",
                )
            )

        config = load_data_api_config(db)
        if not config.configured:
            # The common case, and it must stay cheap and silent-ish: most stores
            # have not connected GA4. Crucially it deletes NOTHING — a store that
            # connects GA4, ingests a month, then has its credential expire must
            # not have that month emptied by the next nightly run.
            return JobRunResult(
                warnings=(
                    f"ga4_not_configured ({config.reason}): {config.detail} "
                    "No GA4 row was written or deleted for "
                    f"{bucket_date}.",
                )
            )

        report = run_report_all(
            config,
            ReportRequest(
                # INCLUSIVE on both ends — GA4's convention, not this stack's.
                # One bucket, so both bounds are the same day. The conversion
                # from the half-open windows used everywhere else happens here,
                # explicitly, and nowhere else.
                date_from=bucket_date,
                date_to=bucket_date,
                dimensions=DIMENSIONS,
                metrics=METRICS,
            ),
            transport=self._transport,
            sleep=self._sleep,
        )

        if report.status is ReportStatus.NOT_CONFIGURED:  # pragma: no cover
            # Unreachable given the check above; kept because the alternative to
            # an explicit branch is falling through to the OK path and deleting
            # a day on the strength of a report that was never made.
            return JobRunResult(
                warnings=(f"ga4_not_configured ({report.reason}): {report.detail}",)
            )

        if report.status is ReportStatus.UNAVAILABLE:
            raise Ga4IngestUnavailable(
                f"GA4 Data API unavailable for {bucket_date} "
                f"({report.reason}): {report.detail}"
            )

        return self._land(db, bucket_date, tz_generation, report, warnings)

    # -- pieces -----------------------------------------------------------
    def _land(
        self,
        db: Session,
        bucket_date: date,
        tz_generation: int,
        report: Ga4Report,
        warnings: list[str],
    ) -> JobRunResult:
        """Replace the bucket with what GA4 just returned. The only writer."""
        store_tz = store_timezone(db)
        store_tz_name = getattr(store_tz, "key", str(store_tz))
        property_tz_name = report.property_timezone or ""
        tz_mismatch = bool(property_tz_name) and property_tz_name != store_tz_name

        if tz_mismatch:
            # Warned on EVERY run, not once: this does not fix itself, and a
            # warning that only fired the first time would be invisible to
            # whoever eventually looks at the numbers.
            warnings.append(
                f"ga4_timezone_mismatch: the GA4 property reports in "
                f"{property_tz_name} but store.timezone is {store_tz_name}, so "
                f"the rows filed under {bucket_date} are a {property_tz_name} "
                "day, not a store-local one. Align the property timezone in GA4 "
                "Admin -> Property Settings, or read tz_mismatch/ga4_date before "
                "comparing these figures to internal daily totals."
            )
        elif not property_tz_name:
            warnings.append(
                "ga4_timezone_unknown: the runReport response carried no "
                "metadata.timeZone, so the property's day boundary could not be "
                "confirmed against store.timezone."
            )

        offset_hours = _tz_offset_hours(property_tz_name or store_tz_name, bucket_date)
        final_at = (
            final_after(bucket_date, tz_offset_hours=offset_hours)
            if offset_hours is not None
            else None
        )
        now = datetime.now(timezone.utc)
        provisional = final_at is None or now < final_at
        if final_at is None:
            warnings.append(
                f"ga4_final_after_unknown: {property_tz_name or store_tz_name!r} "
                "is not a resolvable timezone, so this bucket is held provisional "
                "indefinitely rather than being declared final on a guess."
            )

        if report.is_sampled:
            warnings.append(
                "ga4_sampled: GA4 answered from a sample and scaled the result "
                f"up ({', '.join(f'{read}/{space}' for read, space in report.sampling)}). "
                "These figures are estimates; quality is recorded as ESTIMATED."
            )
        if report.subject_to_thresholding:
            warnings.append(
                "ga4_thresholded: GA4 withheld rows below its privacy threshold, "
                "so this day's totals are a FLOOR and not a total. The withheld "
                "rows are absent, not zero; quality is recorded as INCOMPLETE."
            )
        if report.data_loss_from_other_row:
            warnings.append(
                "ga4_cardinality_loss: the dimension combination overflowed GA4's "
                "cardinality limit and the tail was folded into '(other)', so the "
                "breakdown does not add up to the property total."
            )
        if report.empty_reason:
            warnings.append(f"ga4_empty_reason: {report.empty_reason}")

        quality = report.quality
        rows, skipped, truncated = self._rows(
            report,
            bucket_date=bucket_date,
            tz_generation=tz_generation,
            property_tz_name=property_tz_name,
            tz_mismatch=tz_mismatch,
            provisional=provisional,
            final_at=final_at,
            quality=quality.value,
        )

        if skipped:
            warnings.append(
                f"ga4_suppressed_rows: {skipped} row(s) came back with a withheld "
                "metric and were SKIPPED rather than written as zero — a withheld "
                "value is an absence, and there is no NOT NULL counter that can "
                "hold 'unknown'."
            )
        if skipped and not rows:
            warnings.append(
                "ga4_all_rows_suppressed: every row GA4 returned for "
                f"{bucket_date} was withheld, so this bucket is empty for a "
                "reason that an empty bucket cannot express. Do not read it as "
                "zero traffic."
            )
        if truncated:
            warnings.append(
                f"ga4_landing_page_truncated: {truncated} landing page(s) were "
                f"longer than {LANDING_PAGE_MAX_CHARS} characters and were "
                "truncated, so two long URLs sharing a prefix are one row here."
            )
        if len(report.rows) < report.row_count:
            warnings.append(
                f"ga4_partial_pull: GA4 reported {report.row_count} matching rows "
                f"and returned {len(report.rows)}; this bucket is incomplete."
            )
        if provisional and final_at is not None:
            warnings.append(
                f"ga4_provisional: GA4 may still revise {bucket_date} until "
                f"{final_at.isoformat()}. Rows are marked is_provisional=1."
            )

        # DELETE first, unconditionally — including when `rows` is empty, which
        # is precisely what an upsert cannot express: a bucket that used to have
        # a landing page and now has none. Safe here, and only here, because
        # every path that did not get a successful report has already returned.
        deleted = db.execute(
            delete(AggGa4Daily).where(
                AggGa4Daily.bucket_date == bucket_date,
                AggGa4Daily.tz_generation == tz_generation,
            )
        ).rowcount

        for start in range(0, len(rows), INSERT_CHUNK):
            db.execute(mysql_insert(AggGa4Daily).values(rows[start : start + INSERT_CHUNK]))

        self._enqueue_trailing_window(
            db, bucket_date, tz_generation, store_tz, warnings
        )

        return JobRunResult(
            rows_written=len(rows),
            rows_deleted=int(deleted or 0),
            warnings=_cap(warnings),
        )

    def _rows(
        self,
        report: Ga4Report,
        *,
        bucket_date: date,
        tz_generation: int,
        property_tz_name: str,
        tz_mismatch: bool,
        provisional: bool,
        final_at: datetime | None,
        quality: str,
    ) -> tuple[list[dict[str, Any]], int, int]:
        """Turn report rows into table rows. Returns (rows, skipped, truncated).

        Rows are keyed by their dimension tuple and **merged** rather than
        appended. GA4 will not normally return two rows for one combination, but
        `_dimension` collapses several GA4 spellings of "unknown" onto the `'-'`
        sentinel and truncates long landing pages, so two distinct GA4 rows can
        legitimately land on one key here. Appending both would violate the
        UNIQUE constraint mid-INSERT and fail the whole bucket; merging is the
        honest resolution, since they are the same row at this grain.
        """
        read, space = report.sampling[0] if report.sampling else (0, 0)
        merged: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        skipped = 0
        truncated = 0

        for row in report.rows:
            if row.has_suppressed_metric:
                # NOT zero-filled. See the module docstring, section 4.
                skipped += 1
                continue

            channel = _dimension(
                row.dimension(report.dimension_headers, "sessionDefaultChannelGroup"), 64
            )
            source_medium = _dimension(
                row.dimension(report.dimension_headers, "sessionSourceMedium"), 128
            )
            device = _dimension(
                row.dimension(report.dimension_headers, "deviceCategory"), 32
            )
            raw_landing = (
                row.dimension(report.dimension_headers, "landingPagePlusQueryString")
                or ""
            ).strip()
            if len(raw_landing) > LANDING_PAGE_MAX_CHARS:
                truncated += 1
            landing = _dimension(raw_landing, LANDING_PAGE_MAX_CHARS)

            sessions = row.metric(report.metric_headers, "sessions") or 0
            measures = {
                "sessions": sessions,
                "engaged_sessions": row.metric(report.metric_headers, "engagedSessions")
                or 0,
                "total_users": row.metric(report.metric_headers, "totalUsers") or 0,
                "new_users": row.metric(report.metric_headers, "newUsers") or 0,
                "screen_page_views": row.metric(
                    report.metric_headers, "screenPageViews"
                )
                or 0,
                "sum_engagement_seconds": row.metric(
                    report.metric_headers, "userEngagementDuration"
                )
                or 0,
                # The denominator for the engagement sum. Equal to `sessions`
                # today; stored separately so the average stays recomputable if
                # GA4 ever reports engagement over a different base.
                "n_engagement": sessions,
            }

            key = (channel, source_medium, device, landing)
            existing = merged.get(key)
            if existing is not None:
                # Same key at this grain. Add the flows. `total_users` is added
                # too and is the one number that can go wrong here — it is a
                # distinct count, so a merge can overstate it. Not hidden: the
                # column is named in `analytics_ga4.NON_ADDITIVE_COLUMNS` and is
                # already unsummable, which is exactly the property being relied
                # on. Appending a second row instead would violate the UNIQUE
                # constraint mid-INSERT and fail the whole bucket.
                for column, value in measures.items():
                    existing[column] += value
                continue

            merged[key] = {
                "bucket_date": bucket_date,
                "tz_generation": tz_generation,
                "computed_at": _utcnow(),
                "channel_group": channel,
                "source_medium": source_medium,
                "device_category": device,
                "landing_page": landing,
                **measures,
                "ga4_date": _dimension(
                    row.dimension(report.dimension_headers, "date"), 8
                ),
                "property_timezone": _dimension(property_tz_name, 64),
                "tz_mismatch": tz_mismatch,
                "is_provisional": provisional,
                "final_after": final_at,
                "is_sampled": report.is_sampled,
                "samples_read_count": read,
                "sampling_space_size": space,
                "is_thresholded": report.subject_to_thresholding,
                "data_loss_high_cardinality": report.data_loss_from_other_row,
                "quality": quality,
            }

        return list(merged.values()), skipped, truncated

    @staticmethod
    def _enqueue_trailing_window(
        db: Session,
        bucket_date: date,
        tz_generation: int,
        store_tz: ZoneInfo,
        warnings: list[str],
    ) -> None:
        """Mark the still-provisional buckets OLDER than this one dirty.

        Two filters, and BOTH are load-bearing:

        * **strictly older than the bucket in hand** — so the cascade walks in
          one direction and cannot re-enqueue itself;
        * **still provisional as of the real store-local today** — so it
          terminates. Measuring "provisional" against ``bucket_date`` instead
          would make every bucket enqueue the two before it forever, and a
          backfill of 2024 would enqueue its way backwards through the calendar
          with a perfectly plausible-looking queue.

        Together they mean a backfill of an old day enqueues **nothing**, and a
        nightly run of today enqueues exactly ``{today-1, today-2}``, which in
        turn enqueue ``{today-2}`` and ``{}``. See the module docstring, §2.

        A queue failure must not fail an otherwise-good ingest: the bucket in
        hand is already correct, and losing the re-pull costs freshness rather
        than correctness. It is warned about so the loss is visible.
        """
        from app.services.analytics.queue import RecomputeQueue
        from app.services.analytics.timebox import local_day

        today = local_day(datetime.now(timezone.utc), store_tz)
        stale = [
            day
            for day in provisional_window(today)
            if day < bucket_date and is_provisional(day, today)
        ]
        if not stale:
            return
        try:
            queue = RecomputeQueue(db)
            for day in stale:
                queue.enqueue(
                    JOB_NAME,
                    day,
                    reason="ga4_provisional_reingest",
                    tz_generation=tz_generation,
                )
        except Exception as exc:  # noqa: BLE001 - freshness, not correctness
            log.warning("could not enqueue GA4 provisional re-ingest: %s", exc)
            warnings.append(
                f"ga4_reingest_enqueue_failed: {type(exc).__name__} — the trailing "
                "provisional window was not queued for re-pull, so those days keep "
                "their earlier provisional figures until something re-runs them."
            )


register(Ga4DailyJob())
