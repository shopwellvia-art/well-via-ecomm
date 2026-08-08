"""Blended marketing efficiency from manually entered spend — view 21.

What this resolver is allowed to claim
--------------------------------------
`analytics_marketing_spend` holds money a human typed from an invoice (or, one
day, an ads API delivered). Money is the ONE marketing figure this deployment
actually possesses, and it supports exactly two honest answers:

  * **Blended ROAS / MER** — total store revenue over total recorded spend.
    Revenue is the internal AUTHORITATIVE `net_revenue`; the ratio is only ever
    as good as the spend rows underneath it, so it carries their quality grade
    (typed figures are ESTIMATED unless reconciled ACTUAL, monthly lumps are
    ALLOCATED per day) and a `MANUAL_SPEND` warning on every response.
  * **Spend by channel** — real, because the entered rows carry a channel.

What it must never claim is **per-channel ROAS**. Attributing revenue to a
channel needs a session->order key (GA4 attribution, click ids), and no such key
exists anywhere in this deployment. Dividing blended revenue by one channel's
spend would manufacture a per-channel ratio out of nothing — so the per-channel
table carries spend and its share of spend, and no revenue or ROAS column at
all. The same reasoning removes the CHANNEL/CAMPAIGN filters from view 21:
filtering the spend side of a blended ratio *is* a per-channel ROAS wearing a
filter's clothes.

Arithmetic rules
----------------
Ratios are computed **sums first, one division** — never an average of daily
ratios, which weights a Rs.10 day the same as a Rs.10,000 day. A bucket with no
recorded spend has no ROAS: None, not 0 (a claim of free revenue) and not
infinity. A monthly lump cut by the window edge contributes the ALLOCATED daily
spread `allocate_row_to_days` already provides — paisa-exact, deterministic —
and its days are graded ALLOCATED, never promoted to the row's own quality.

When zero spend rows touch the window the view downgrades itself at runtime to
the gated shape: `not_configured` with an explicitly empty `sources` list. A
window nobody entered spend for has no ROAS, and it is never assumed zero —
zero spend under real revenue would print infinite efficiency.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models.analytics_spend import (
    AnalyticsMarketingSpend,
    SpendQuality,
    allocate_row_to_days,
)
from app.schemas.analytics_view import AnalyticsWarning, KpiValue, TableBlock
from app.services.analytics.filters import Granularity, ResolvedWindow
from app.services.analytics.resolvers.base import (
    ResolverContext,
    ResolverResult,
    build_kpi,
    missing_kpi,
    not_configured,
    probe_source,
    source_ref,
    warn,
)
from app.services.analytics.resolvers.core import GRANULARITY_DOWNGRADED, compute_kpis
from app.services.analytics.resolvers.special import custom_function
from app.services.analytics.types import Capability, MetricQuality, worst_quality

__all__ = [
    "MANUAL_SPEND",
    "SPEND_CURRENCY_SKIPPED",
    "SPEND_SOURCE",
    "REVENUE_SOURCE",
    "roas_blended",
]

#: Every response from this resolver carries this code: the spend side is a
#: hand-entered ledger, and the ratio is blended (MER), not per-channel.
MANUAL_SPEND = "MANUAL_SPEND"

#: Spend rows in a currency other than the reporting currency were excluded
#: rather than summed as if rupees. Naive summation would be silently wrong by
#: the exchange rate.
SPEND_CURRENCY_SKIPPED = "SPEND_CURRENCY_SKIPPED"

SPEND_SOURCE = "analytics_marketing_spend"
REVENUE_SOURCE = "agg_order_daily"
REPORTING_CURRENCY = "INR"

_RATIO_Q = Decimal("0.0001")
_PCT_Q = Decimal("0.0001")
_HUNDRED = Decimal("100")

#: SpendQuality -> MetricQuality, mirroring `cost_rules._quality_of` so a typed
#: spend figure and a typed cost rule grade identically: reconciled ACTUAL keeps
#: its grade, everything a human asserted is ESTIMATED.
_SPEND_QUALITY: dict[str, MetricQuality] = {
    SpendQuality.ACTUAL: MetricQuality.ACTUAL,
    SpendQuality.CONTRACTED: MetricQuality.ESTIMATED,
    SpendQuality.ESTIMATED: MetricQuality.ESTIMATED,
    SpendQuality.ASSUMED: MetricQuality.ESTIMATED,
}


@dataclass(frozen=True)
class _Share:
    """One store-local day's share of one spend row, inside the window."""

    day: date
    channel: str
    amount: Decimal
    quality: MetricQuality


@dataclass(frozen=True)
class _WindowSpend:
    """Everything the window's spend rows amount to, graded honestly."""

    shares: tuple[_Share, ...]
    rows_touching_window: int
    skipped_currencies: tuple[str, ...]

    @property
    def total(self) -> Decimal:
        return sum((s.amount for s in self.shares), Decimal("0"))

    @property
    def quality(self) -> MetricQuality:
        qualities = [s.quality for s in self.shares]
        if self.skipped_currencies:
            # Part of the entered spend could not be included, so no total
            # built here covers everything the admin recorded.
            qualities.append(MetricQuality.INCOMPLETE)
        return worst_quality(qualities)

    @property
    def newest_day(self) -> date | None:
        return max((s.day for s in self.shares), default=None)


def _last_reporting_day(window: ResolvedWindow) -> date:
    """The window is half-open, so its last reporting day is `date_to - 1`."""
    return window.date_to - timedelta(days=1)


def _window_spend(ctx: ResolverContext, window: ResolvedWindow) -> _WindowSpend:
    """Per-day spend shares for every recorded row overlapping `window`.

    Reads the same table `analytics_cost_admin.py` writes and spreads rows with
    the model's own `allocate_row_to_days` — the ONE allocation rule, so this
    view, the admin preview and any future CAC read of this ledger can never
    disagree about which day a lump belongs to.
    """
    last_day = _last_reporting_day(window)
    rows = (
        ctx.db.execute(
            select(AnalyticsMarketingSpend).where(
                AnalyticsMarketingSpend.tz_generation == ctx.tz_generation,
                AnalyticsMarketingSpend.period_start <= last_day,
                AnalyticsMarketingSpend.period_end >= window.date_from,
            )
        )
        .scalars()
        .all()
    )

    shares: list[_Share] = []
    skipped: set[str] = set()
    for row in rows:
        if (row.currency or REPORTING_CURRENCY) != REPORTING_CURRENCY:
            skipped.add(row.currency)
            continue
        base = _SPEND_QUALITY.get(row.quality, MetricQuality.ESTIMATED)
        for day_amount in allocate_row_to_days(row):
            if not (window.date_from <= day_amount.day <= last_day):
                continue
            quality = (
                worst_quality([base, MetricQuality.ALLOCATED])
                if day_amount.allocated
                else base
            )
            shares.append(
                _Share(
                    day=day_amount.day,
                    channel=row.channel,
                    amount=day_amount.amount,
                    quality=quality,
                )
            )
    return _WindowSpend(
        shares=tuple(shares),
        rows_touching_window=len(rows),
        skipped_currencies=tuple(sorted(skipped)),
    )


def _ratio(revenue: Decimal | None, spend: Decimal | None) -> Decimal | None:
    """Blended ROAS for one total pair, or None where it is undefined.

    None on zero/negative spend: revenue over no recorded spend is not an
    efficiency of zero and it is not infinity — it is a question the ledger
    cannot answer for that span.
    """
    if revenue is None or spend is None or spend <= 0:
        return None
    return (revenue / spend).quantize(_RATIO_Q)


def _bucket_start(day: date, granularity: Granularity) -> date:
    if granularity is Granularity.WEEK:
        return day - timedelta(days=day.weekday())
    if granularity is Granularity.MONTH:
        return day.replace(day=1)
    return day


def _bucket_end(bucket: date, granularity: Granularity, last_day: date) -> date:
    """Last reporting day the bucket covers, clamped to the window."""
    if granularity is Granularity.WEEK:
        end = bucket + timedelta(days=6)
    elif granularity is Granularity.MONTH:
        next_month = (bucket.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = next_month - timedelta(days=1)
    else:
        end = bucket
    return min(end, last_day)


def _manual_spend_warning() -> AnalyticsWarning:
    return warn(
        MANUAL_SPEND,
        "Marketing spend here is manually entered in the admin, not read from "
        "an ad platform, so ROAS is blended (MER): total store revenue over "
        "total recorded spend, graded by the entered rows' own quality. "
        "Revenue cannot be attributed to a channel without ad-platform click "
        "attribution — no session-to-order key exists — so per-channel ROAS is "
        "not computable and is not shown; the per-channel figures are spend "
        "only.",
        severity="info",
        source=SPEND_SOURCE,
    )


@custom_function("roas_blended")
def roas_blended(ctx: ResolverContext) -> ResolverResult:
    """Blended ROAS, total entered spend, and the per-channel spend split."""
    window_spend = _window_spend(ctx, ctx.window)

    if not window_spend.shares:
        # Runtime downgrade to the gated shape: empty `sources`, the reason,
        # and nothing else. No spend entered is "cannot know", never "zero
        # spend" — zero spend under real revenue reads as infinite efficiency.
        return not_configured(
            "No marketing spend is recorded for "
            f"{ctx.window.date_from.isoformat()}..{_last_reporting_day(ctx.window).isoformat()}. "
            "Blended ROAS is total revenue over recorded spend, and with no "
            "spend rows in the window there is no denominator — it is never "
            "assumed to be zero. Enter spend (daily or monthly, per channel) "
            "in Admin -> Analytics -> Costs, or connect an ad platform.",
            requires=[Capability.AD_PLATFORM.value],
            detail={
                "view": ctx.view.slug,
                "rows_excluded_for_currency": len(window_spend.skipped_currencies),
            },
        )

    warnings: list[AnalyticsWarning] = [_manual_spend_warning()]

    if window_spend.skipped_currencies:
        warnings.append(
            warn(
                SPEND_CURRENCY_SKIPPED,
                "Spend rows in "
                + ", ".join(window_spend.skipped_currencies)
                + " were excluded from these totals rather than summed as if "
                "they were rupees; the spend figures are therefore incomplete.",
                severity="warn",
                currencies=list(window_spend.skipped_currencies),
            )
        )

    granularity = ctx.filters.granularity
    if granularity is Granularity.HOUR:
        warnings.append(
            warn(
                GRANULARITY_DOWNGRADED,
                "Spend is recorded per day (or per month) — there is no hourly "
                "spend grain, so this view is served at day granularity.",
                severity="info",
                requested="hour",
                served="day",
            )
        )
        granularity = Granularity.DAY

    # -- revenue: the internal AUTHORITATIVE side of the ratio ---------------
    state = probe_source(ctx, REVENUE_SOURCE, label="Order rollup (daily)")
    warnings.extend(state.warnings)
    revenue_now = ctx.repo.fetch_totals(
        REVENUE_SOURCE,
        columns=["net_revenue"],
        window=ctx.window,
        tz_generation=ctx.tz_generation,
    ).get("net_revenue")
    revenue_now = None if revenue_now is None else Decimal(str(revenue_now))

    # Catalogue KPIs the view declares beyond the two computed here (the
    # net_revenue card, typically) go through the shared path so their
    # definitions, deltas and provenance stay identical to every other view.
    bundle = compute_kpis(
        ctx, [k for k in ctx.view.kpis if k not in ("blended_roas", "total_spend")]
    )
    warnings.extend(bundle.warnings)

    # -- comparison window, sums-then-divide on both sides -------------------
    prev_spend_total: Decimal | None = None
    revenue_prev: Decimal | None = None
    if ctx.window.has_comparison:
        compare = ResolvedWindow(
            date_from=ctx.window.compare_from,  # type: ignore[arg-type]
            date_to=ctx.window.compare_to,  # type: ignore[arg-type]
        )
        prev = _window_spend(ctx, compare)
        prev_spend_total = prev.total if prev.shares else None
        raw = ctx.repo.fetch_totals(
            REVENUE_SOURCE,
            columns=["net_revenue"],
            window=compare,
            tz_generation=ctx.tz_generation,
        ).get("net_revenue")
        revenue_prev = None if raw is None else Decimal(str(raw))

    # -- KPIs ----------------------------------------------------------------
    spend_total = window_spend.total
    spend_quality = window_spend.quality
    kpis: dict[str, KpiValue] = dict(bundle.kpis)
    kpis["total_spend"] = build_kpi(
        "total_spend",
        spend_total,
        previous=prev_spend_total,
        quality=spend_quality,
    )
    if revenue_now is None:
        kpis["blended_roas"] = missing_kpi("blended_roas", REVENUE_SOURCE)
    elif spend_total <= 0:
        kpis["blended_roas"] = missing_kpi("blended_roas", "non-zero recorded spend")
    else:
        kpis["blended_roas"] = build_kpi(
            "blended_roas",
            _ratio(revenue_now, spend_total),
            previous=_ratio(revenue_prev, prev_spend_total),
            # Revenue is AUTHORITATIVE; the ratio inherits the weaker side.
            quality=worst_quality([spend_quality, MetricQuality.AUTHORITATIVE]),
        )

    # -- series: ROAS per bucket, sums first, one division per bucket --------
    series: dict[str, list[dict]] = {}
    last_day = _last_reporting_day(ctx.window)
    covered = state.covered_through(ctx.window)

    revenue_by_day: dict[date, Decimal] = {}
    if state.has_rows:
        for row in ctx.repo.fetch_rollup(
            REVENUE_SOURCE,
            columns=["net_revenue"],
            window=ctx.window,
            tz_generation=ctx.tz_generation,
            group_by=["bucket_date"],
        ):
            if row.get("net_revenue") is not None:
                revenue_by_day[row["bucket_date"]] = Decimal(str(row["net_revenue"]))

    spend_by_bucket: dict[date, Decimal] = {}
    for share in window_spend.shares:
        key = _bucket_start(share.day, granularity)
        spend_by_bucket[key] = spend_by_bucket.get(key, Decimal("0")) + share.amount

    points: list[dict] = []
    bucket = _bucket_start(ctx.window.date_from, granularity)
    while bucket <= last_day:
        bucket_last = _bucket_end(bucket, granularity, last_day)
        first_covered_day = max(bucket, ctx.window.date_from)
        if covered is None or bucket_last > covered:
            # The rollup does not cover the whole bucket. A partial-revenue
            # ROAS against full-bucket spend would understate; report the
            # bucket's ratio as unknown rather than as a smaller number.
            bucket_revenue: Decimal | None = None
        else:
            # Inside the covered range a day with no row genuinely earned
            # nothing, so missing days are real zeros here — the densification
            # rule `core.py` documents.
            bucket_revenue = sum(
                (
                    revenue_by_day.get(first_covered_day + timedelta(days=n), Decimal("0"))
                    for n in range((bucket_last - first_covered_day).days + 1)
                ),
                Decimal("0"),
            )
        bucket_spend = spend_by_bucket.get(bucket)
        points.append(
            {
                "date": bucket.isoformat(),
                # None when nothing was entered for the bucket: an absent entry
                # in a hand-typed ledger is "not recorded", not "zero spent".
                "spend": bucket_spend,
                "revenue": bucket_revenue,
                "blended_roas": _ratio(bucket_revenue, bucket_spend),
            }
        )
        bucket = _bucket_end(bucket, granularity, last_day) + timedelta(days=1)

    date_charts = [c for c in ctx.view.charts if c.x == "date"]
    if date_charts and points:
        series[date_charts[0].id] = points

    # -- per-channel spend. Spend ONLY: no revenue, no ROAS ------------------
    per_channel: dict[str, list[_Share]] = {}
    for share in window_spend.shares:
        per_channel.setdefault(share.channel, []).append(share)
    channel_rows = [
        {
            "channel": channel,
            "spend": sum((s.amount for s in shares), Decimal("0")),
            "spend_share_pct": (
                (
                    sum((s.amount for s in shares), Decimal("0"))
                    / spend_total
                    * _HUNDRED
                ).quantize(_PCT_Q)
                if spend_total > 0
                else None
            ),
            "quality": worst_quality([s.quality for s in shares]).value,
        }
        for channel, shares in per_channel.items()
    ]
    channel_rows.sort(key=lambda r: r["spend"], reverse=True)

    tables = {
        (ctx.view.tables[0].id if ctx.view.tables else "channel_spend"): TableBlock(
            rows=channel_rows, total_rows=len(channel_rows), truncated=False
        )
    }
    channel_charts = [c for c in ctx.view.charts if c.x == "channel"]
    if channel_charts:
        series[channel_charts[0].id] = channel_rows

    spend_ref = source_ref(
        SPEND_SOURCE,
        label="Marketing spend (entered)",
        # "live": a direct read of the admin-written table, not a rollup — the
        # schema's SourceRef.kind vocabulary is rollup|live|external|derived.
        kind="live",
        rows=window_spend.rows_touching_window,
        through=window_spend.newest_day,
    )
    sources = [spend_ref, state.ref]
    seen_ids = {ref.id for ref in sources}
    for ref in bundle.sources:
        if ref.id not in seen_ids:
            sources.append(ref)
            seen_ids.add(ref.id)

    return ResolverResult(
        kpis=kpis,
        series=series,
        tables=tables,
        sources=sources,
        warnings=warnings,
    ).rolled_up()
