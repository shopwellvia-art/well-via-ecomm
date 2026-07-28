"""Reporting-day boundaries and timezone generations.

Every analytics bucket is a *store-local* reporting day, not a UTC day. This
module is the only place that conversion happens, because getting it wrong is
both easy and unrecoverable:

  * Containers run UTC. The only other timezone anywhere in this codebase is the
    hardcoded IST constant in `invoice_service.py:55`.
  * With `store.timezone = Asia/Kolkata`, an order placed at 23:00 IST is
    17:30 UTC *the same day*, but one placed at 05:00 IST is 23:30 UTC the day
    *before*. Bucketing on UTC therefore moves roughly a fifth of each day's
    orders into the neighbouring bucket, and "yesterday's sales" is wrong by
    5.5 hours' worth of trade.
  * Unlike most bugs this one cannot be fixed by redeploying. Once rows are
    written under one day boundary, changing the boundary re-buckets history —
    so every bucketed row records the `tz_generation` that produced it, and a
    query spanning two generations must refuse rather than silently mix them.

Day boundaries are computed in Python with stdlib `zoneinfo` and applied as UTC
range filters. MySQL's `CONVERT_TZ()` is deliberately not used: it needs the
timezone tables loaded (`mysql_tzinfo_to_sql`), which is not guaranteed on the
shared remote host, and it would fail silently by returning NULL.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analytics_control import AnalyticsTzGeneration

#: Used only when no store.timezone setting row exists at all. Matches the
#: hardcoded IST assumption already baked into invoice_service.py.
DEFAULT_TIMEZONE = "Asia/Kolkata"


class TzGenerationMixed(Exception):
    """Raised when a query window would span two timezone generations.

    Refusing is the whole point: silently mixing buckets computed under two
    different day boundaries produces a number that is wrong in a way nobody can
    detect afterwards, because the rows look identical.
    """


def store_timezone(db: Session) -> ZoneInfo:
    """Resolve the configured reporting timezone.

    Falls back to DEFAULT_TIMEZONE — and specifically does NOT fall back to UTC.
    A missing setting is a configuration gap, not an instruction to report in a
    timezone the business does not operate in; silently switching to UTC would
    shift every bucket boundary by 5.5 hours.
    """
    from app.services.settings_service import SettingsService

    name = SettingsService(db).get_raw("store.timezone", default=DEFAULT_TIMEZONE)
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except ZoneInfoNotFoundError:
        # A typo'd timezone must not silently become UTC.
        return ZoneInfo(DEFAULT_TIMEZONE)


def local_day(moment: datetime, tz: ZoneInfo) -> date:
    """The store-local reporting day a UTC instant falls in."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(tz).date()


def day_bounds_utc(bucket: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """UTC half-open range ``[start, end)`` covering one store-local day.

    Half-open on purpose: a closed upper bound double-counts anything landing
    exactly on midnight, and `BETWEEN` on DATETIME(6) is a classic off-by-one
    against sub-second precision.
    """
    start_local = datetime.combine(bucket, time.min, tzinfo=tz)
    end_local = datetime.combine(bucket + timedelta(days=1), time.min, tzinfo=tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def range_bounds_utc(
    date_from: date, date_to: date, tz: ZoneInfo
) -> tuple[datetime, datetime]:
    """UTC half-open range covering store-local days ``[date_from, date_to)``."""
    start, _ = day_bounds_utc(date_from, tz)
    end, _ = day_bounds_utc(date_to, tz)
    return start, end


def active_generation(db: Session) -> AnalyticsTzGeneration:
    """The generation rows should currently be written under.

    Seeds generation 1 on first call so nothing can aggregate before a
    generation exists — a row with `tz_generation` pointing at nothing would be
    unattributable forever.
    """
    row = db.execute(
        select(AnalyticsTzGeneration)
        .where(AnalyticsTzGeneration.status == "active")
        .order_by(AnalyticsTzGeneration.generation.desc())
    ).scalars().first()
    if row is not None:
        return row

    from app.services.settings_service import SettingsService

    tz_name = SettingsService(db).get_raw("store.timezone", default=DEFAULT_TIMEZONE)
    row = AnalyticsTzGeneration(
        generation=1,
        timezone=tz_name or DEFAULT_TIMEZONE,
        effective_from=datetime.now(timezone.utc),
        status="active",
        note="seeded automatically on first analytics run",
    )
    db.add(row)
    db.commit()
    return row


def assert_single_generation(generations: "set[int] | list[int]") -> int:
    """Guard for any read spanning bucketed rows.

    Callers pass the distinct `tz_generation` values their result set touched.
    More than one means the window straddles a reporting-timezone change and the
    numbers cannot be added together.
    """
    distinct = set(generations)
    if not distinct:
        raise TzGenerationMixed("no rows carried a tz_generation")
    if len(distinct) > 1:
        raise TzGenerationMixed(
            f"window spans timezone generations {sorted(distinct)}; "
            "buckets built under different day boundaries must not be combined. "
            "Serve the active generation and surface TZ_REBUILD_IN_PROGRESS."
        )
    return distinct.pop()
