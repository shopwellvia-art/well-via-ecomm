"""The shared analytics filter contract.

One model for every one of the 73 views. A view honours only the filter keys it
declares in the registry; unknown keys are ignored rather than rejected, so a
URL copied between two views still opens instead of erroring — shareable links
matter more here than strict validation, and an ignored key cannot change a
number.

Two properties this file exists to guarantee:

**Nothing here reaches SQL as an identifier.** Every field is either a bounded
scalar or a value bound as a query parameter. Table names, column names and
grouping keys come from the server-side registry, never from the request. A
client can say `dimension=courier`; it can never say `dimension=(SELECT ...)`.

**Windows are half-open and bounded.** `[date_from, date_to)` throughout,
matching `timebox`. An unbounded range against `order_items` on a live store is
an outage, so the range is capped and the cap is an error rather than a silent
truncation — a quietly shortened window leaves a hole in a chart that nobody can
trace back to its cause.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: An unbounded scan of the transactional tables is an incident, not a report.
MAX_RANGE_DAYS = 400
#: Hourly granularity multiplies row count by 24; keep the window short.
MAX_HOURLY_RANGE_DAYS = 14
#: Caps high-cardinality breakdowns (products, pincodes) so one request cannot
#: return a six-figure result set.
MAX_LIMIT = 200


class Period(str, Enum):
    LAST_7D = "7d"
    LAST_30D = "30d"
    LAST_90D = "90d"
    MTD = "mtd"
    QTD = "qtd"
    YTD = "ytd"
    CUSTOM = "custom"


class Comparison(str, Enum):
    NONE = "none"
    PREVIOUS_PERIOD = "previous_period"
    PREVIOUS_YEAR = "previous_year"


class Granularity(str, Enum):
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class StatusBasis(str, Enum):
    #: PAID/SHIPPED/DELIVERED — the canonical revenue rule from dashboard_service.
    REVENUE = "revenue"
    #: Every status, including cancelled and refunded.
    ALL = "all"


class AnalyticsFilters(BaseModel):
    """Normalised request filters. `resolve()` turns presets into real dates."""

    model_config = ConfigDict(extra="ignore", frozen=False)

    period: Period = Period.LAST_30D
    date_from: date | None = None
    date_to: date | None = None
    comparison: Comparison = Comparison.PREVIOUS_PERIOD
    granularity: Granularity = Granularity.DAY
    status_basis: StatusBasis = StatusBasis.REVENUE

    #: Dimension to break down by. Validated against the view's registry entry by
    #: the resolver — this is a label, never a column name.
    dimension: str | None = None
    limit: int = Field(default=20, ge=1, le=MAX_LIMIT)
    offset: int = Field(default=0, ge=0)
    sort: str | None = None
    sort_dir: str = Field(default="desc", pattern="^(asc|desc)$")

    # Dimension value filters. All bound as parameters, never interpolated.
    product_id: int | None = None
    category_id: int | None = None
    sku: str | None = Field(default=None, max_length=64)
    customer_segment: str | None = Field(default=None, max_length=32)
    new_or_returning: str | None = Field(default=None, pattern="^(new|returning)$")
    source: str | None = Field(default=None, max_length=64)
    medium: str | None = Field(default=None, max_length=64)
    campaign: str | None = Field(default=None, max_length=120)
    device: str | None = Field(default=None, max_length=32)
    country: str | None = Field(default=None, max_length=2)
    state: str | None = Field(default=None, max_length=64)
    city: str | None = Field(default=None, max_length=64)
    payment_method: str | None = Field(default=None, max_length=32)
    payment_gateway: str | None = Field(default=None, max_length=40)
    courier: str | None = Field(default=None, max_length=64)
    order_status: str | None = Field(default=None, max_length=20)
    coupon: str | None = Field(default=None, max_length=64)

    #: Bypasses the cache read (still writes). Gated on analytics.jobs.run at the
    #: endpoint — otherwise it is a free cache-stampede lever for any viewer.
    refresh: bool = False

    @field_validator("sku", "campaign", "source", "medium", mode="before")
    @classmethod
    def _blank_to_none(cls, v: Any) -> Any:
        """An empty query param means "not filtering", not "match empty"."""
        return None if v == "" else v

    @model_validator(mode="after")
    def _validate_window(self) -> "AnalyticsFilters":
        if self.period is Period.CUSTOM:
            if self.date_from is None or self.date_to is None:
                raise ValueError("period=custom requires both date_from and date_to")
        if self.date_from and self.date_to:
            if self.date_from >= self.date_to:
                raise ValueError(
                    "date_from must be strictly before date_to (the window is "
                    "half-open [from, to), so a single day is from=D, to=D+1)"
                )
            span = (self.date_to - self.date_from).days
            if span > MAX_RANGE_DAYS:
                raise ValueError(
                    f"date range of {span} days exceeds the {MAX_RANGE_DAYS}-day "
                    "cap. Narrow the window — it is not truncated silently, "
                    "because a shortened window leaves an untraceable gap."
                )
            if self.granularity is Granularity.HOUR and span > MAX_HOURLY_RANGE_DAYS:
                raise ValueError(
                    f"granularity=hour is capped at {MAX_HOURLY_RANGE_DAYS} days; "
                    f"requested {span}"
                )
        return self

    def resolve(self, today: date) -> "ResolvedWindow":
        """Turn the preset into concrete half-open windows.

        `today` is the STORE-LOCAL day from `timebox`, never `date.today()` —
        containers run UTC and would roll the window over 5.5 hours early.
        """
        if self.period is Period.CUSTOM:
            start, end = self.date_from, self.date_to  # validated above
        elif self.period is Period.LAST_7D:
            end, start = today, today - timedelta(days=7)
        elif self.period is Period.LAST_30D:
            end, start = today, today - timedelta(days=30)
        elif self.period is Period.LAST_90D:
            end, start = today, today - timedelta(days=90)
        elif self.period is Period.MTD:
            end, start = today + timedelta(days=1), today.replace(day=1)
        elif self.period is Period.QTD:
            q_first_month = 3 * ((today.month - 1) // 3) + 1
            end, start = today + timedelta(days=1), today.replace(month=q_first_month, day=1)
        else:  # YTD
            end, start = today + timedelta(days=1), today.replace(month=1, day=1)

        span = (end - start).days
        if self.comparison is Comparison.PREVIOUS_PERIOD:
            cmp_end, cmp_start = start, start - timedelta(days=span)
        elif self.comparison is Comparison.PREVIOUS_YEAR:
            try:
                cmp_start = start.replace(year=start.year - 1)
                cmp_end = end.replace(year=end.year - 1)
            except ValueError:
                # 29 Feb in a non-leap year. Shift by 365 days rather than
                # failing the whole request over a calendar edge case.
                cmp_start, cmp_end = start - timedelta(days=365), end - timedelta(days=365)
        else:
            cmp_start = cmp_end = None

        return ResolvedWindow(
            date_from=start, date_to=end, compare_from=cmp_start, compare_to=cmp_end
        )

    def cache_key_part(self) -> str:
        """Stable hash of the NORMALISED filters.

        Normalised so `?period=30d` and `?period=30d&limit=20` (the default)
        collapse to one cache entry instead of two. Defaults are excluded, keys
        sorted, so the hash depends on meaning rather than on how the URL was
        written.
        """
        default_dump = AnalyticsFilters().model_dump(mode="json", exclude={"refresh"})
        current = self.model_dump(mode="json", exclude={"refresh"})
        payload = {k: v for k, v in current.items() if v != default_dump.get(k)}
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha1(blob.encode()).hexdigest()[:16]


class ResolvedWindow(BaseModel):
    """Concrete half-open windows in store-local reporting days."""

    date_from: date
    date_to: date
    compare_from: date | None = None
    compare_to: date | None = None

    @property
    def days(self) -> int:
        return (self.date_to - self.date_from).days

    @property
    def has_comparison(self) -> bool:
        return self.compare_from is not None and self.compare_to is not None
