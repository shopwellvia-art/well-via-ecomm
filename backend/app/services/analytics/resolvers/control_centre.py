"""The three Analytics Control Centre views (62, 63, 73).

Module 12 is the one that keeps the other eleven honest, so these three views
have an obligation the other seventy do not: **they must be honest about their
own gaps.** A revenue chart that cannot see something renders a shorter line. A
health screen that cannot see something renders *green*, and green is a claim.

Every view here is therefore wiring, not computation. Three services already do
the work, each with its own documented refusals, and restating any of them here
would create a second definition that drifts:

===============  =========================================================
view             service
===============  =========================================================
62 Tracking      ``integrations.tracking_health()`` — provider configured /
   Health        enabled state, consent, the server-side outbox (including
                 ``SUPPRESSED_NO_CONSENT`` as a first-class count), last
                 delivery, last error, environment mismatch — plus the
                 per-rollup watermark scan this module keeps, because that
                 is the one question the rollups can answer about
                 themselves.
63 Data          ``reconciliation.run_reconciliation()`` — all six checks.
   Reconciliation ``CheckResult.to_row()`` already emits the exact row shape
                 the registry's ``variances`` table declares, so this is a
                 pass-through with provenance attached.
73 Alerts and    ``analytics_alerts`` for what is persisted, and
   Anomaly       ``anomalies.run()`` for the verdict on every rule — which
                 is the only way SKIPPED and CLEAR can be told apart.
===============  =========================================================

Why ``custom`` dispatch rather than new resolver ids
----------------------------------------------------
``tests/test_analytics_resolvers.py`` asserts ``set(RESOLVERS) == {r.value for r
in ResolverId}``, so a new resolver id is a change to ``types.py`` and to that
assertion. These three are one-view shapes with no reuse in them, which is
exactly what the ``custom`` dispatch table documented in ``resolvers/__init__``
exists for: the function name comes from ``params["fn"]``, a SERVER-TRUSTED
registry value, never from the request.

What the pre-existing resolvers in ``special.py`` already covered
----------------------------------------------------------------
``special.TrackingHealthResolver`` already gets the *rollup* half of view 62
right, including the rule that a never-built rollup reports ``lag_days = None``
and must not sort as the freshest thing on the screen. That logic is reproduced
here — with an explicit :data:`NEVER_BUILT_RANK` so the ordering survives a
client re-sort — because the resolver cannot be extended from this file and the
other three quarters of view 62 (providers, consent, outbox, environment) live
in ``integrations`` and were never wired to anything.

``special.ReconciliationResolver`` covered exactly one of the six checks (the
revenue-bridge identity) and listed two more as hardcoded ``not_configured``
stubs. It is still the right resolver for view 64, which is gated on a gateway
settlement API and has no partial answer to give. View 63 needs the service.

Three rules everything below obeys
----------------------------------
**A check that could not run is never a match.** ``CheckStatus.NOT_CONFIGURED``
travels through ``to_row()`` verbatim; nothing here maps it onto ``matched`` and
nothing here fills its NULL values with a 0.00% variance.

**A never-built rollup is the WORST state, not the freshest.** ``lag_days``
stays ``None``; :data:`NEVER_BUILT_RANK` carries the ordering.

**A rule that was skipped is not a rule that was clear.** ``RuleSkip`` and
``RuleClear`` are separate types in ``anomalies`` for that reason, and they stay
separate here — ``state`` on every row of ``rule_coverage`` is one of
:data:`RULE_ALERTING` / :data:`RULE_SKIPPED` / :data:`RULE_CLEAR` /
:data:`RULE_NOT_EVALUATED`, never a blank.

Neither of these views writes
-----------------------------
``run_reconciliation`` is called with ``write_alerts=False`` and the anomaly
detector runs inside a SAVEPOINT that is unconditionally rolled back. Opening a
dashboard must not author alerts: an alert list that grows because somebody
looked at it is a list nobody can reason about, and the aggregation worker —
which runs on a schedule, once — owns that write.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Mapping

from sqlalchemy import func, select

from app.models.analytics_control import (
    AlertSeverity,
    AlertStatus,
    AnalyticsAlert,
    OutboxStatus,
)
from app.schemas.analytics_view import (
    AnalyticsWarning,
    SourceRef,
    TableBlock,
    WarningCode,
)
from app.services.analytics import anomalies, integrations
from app.services.analytics.reconciliation import (
    CheckKey,
    ReconciliationReport,
    run_reconciliation,
)
from app.services.analytics.resolvers.base import (
    NOT_CONFIGURED,
    ResolverContext,
    ResolverResult,
    probe_source,
    source_ref,
    warn,
)
from app.services.analytics.resolvers.special import (
    HEALTH_SOURCES,
    RECONCILIATION_VARIANCE,
    custom_function,
)
from app.services.analytics.types import Capability, MetricQuality

__all__ = [
    "NEVER_BUILT_RANK",
    "GA4_DATA_API_UNAVAILABLE",
    "TRACKING_UNCONFIGURED",
    "DETECTOR_UNAVAILABLE",
    "ALERT_NOT_PERSISTED",
    "RULE_ALERTING",
    "RULE_SKIPPED",
    "RULE_CLEAR",
    "RULE_NOT_EVALUATED",
    "SEVERITY_RANK",
    "STATUS_RANK",
    "RULE_STATE_RANK",
    "tracking_health",
    "reconciliation_grid",
    "anomaly_feed",
]


# ===========================================================================
# Shared vocabulary
# ===========================================================================

#: Staleness rank for a rollup that has never been built. Larger is worse, and
#: this is deliberately far beyond any real lag: a rollup that does not exist is
#: not "N days behind", it is unmeasured, and every sort that puts the worst
#: first has to place it above a rollup that is merely a fortnight stale. The
#: row's ``lag_days`` stays ``None`` — the rank carries the ordering so the
#: honest null never has to be coerced into a number to be sortable.
NEVER_BUILT_RANK = 1_000_000

#: Emitted by view 62 and view 63, unconditionally, on every deployment.
#: ``analytics/ga4.py`` speaks the Measurement Protocol, whose whole vocabulary
#: is "here is an event"; there is no GA4 Data API client in this codebase, so
#: nothing here can ever be compared against GA4's own record of what it kept.
GA4_DATA_API_UNAVAILABLE = (
    "There is no GA4 Data API client in this deployment — app/services/analytics/"
    "ga4.py implements the Measurement Protocol, which can send events and cannot "
    "read back what GA4 recorded. Nothing on this screen has been checked against "
    "GA4's own record, and no credential alone would change that."
)

#: Warning code for "the storefront tag half of tracking is not set up". Kept
#: distinct from the rollup warnings: NO_ROLLUP_YET fills itself in when the
#: aggregation job next runs, this one never does until an admin acts.
TRACKING_UNCONFIGURED = "TRACKING_UNCONFIGURED"

#: The anomaly detector could not be evaluated at read time. Its silence is
#: unknown, not clean — the same distinction `RuleSkip` exists to make.
DETECTOR_UNAVAILABLE = "DETECTOR_UNAVAILABLE"

#: A rule is alerting right now against data no stored alert covers, which means
#: the detector has not been run since that data landed.
ALERT_NOT_PERSISTED = "ALERT_NOT_PERSISTED"

#: The four states one detection rule can be in for one bucket. `skipped` and
#: `clear` are the pair this whole table exists to separate: both render as an
#: absent alert, and only one of them means the store was actually checked.
RULE_ALERTING = "alerting"
RULE_NOT_EVALUATED = "not_evaluated"
RULE_SKIPPED = "skipped"
RULE_CLEAR = "clear"

#: Worst first. `AlertSeverity`'s values sort alphabetically as
#: critical < info < warning, which would put INFO above WARNING on the screen.
SEVERITY_RANK: dict[str, int] = {
    AlertSeverity.CRITICAL: 0,
    AlertSeverity.WARNING: 1,
    AlertSeverity.INFO: 2,
}

#: Secondary ordering within a severity: unowned before owned before closed.
STATUS_RANK: dict[str, int] = {
    AlertStatus.OPEN: 0,
    AlertStatus.ACKNOWLEDGED: 1,
    AlertStatus.MUTED: 2,
    AlertStatus.RESOLVED: 3,
}

#: `not_evaluated` outranks `skipped`: a documented skip is a rule working as
#: designed, a rule that produced no verdict at all is a bug in the detector.
RULE_STATE_RANK: dict[str, int] = {
    RULE_ALERTING: 0,
    RULE_NOT_EVALUATED: 1,
    RULE_SKIPPED: 2,
    RULE_CLEAR: 3,
}

#: `integrations` grades its own findings in its own vocabulary; the envelope
#: takes one of three. `critical` maps to `error` rather than `warn` because an
#: enabled tag with no id is not a caveat on a number, it is the absence of one.
_SEVERITY_MAP = {"critical": "error", "warning": "warn", "info": "info"}

_ORDER_SOURCE = "agg_order_daily"


def _severity(raw: str) -> str:
    return _SEVERITY_MAP.get((raw or "").strip().lower(), "warn")


def _last_reporting_day(ctx: ResolverContext) -> date:
    """The last day the half-open window actually asks about.

    ``date_to`` is exclusive, so a window ending tomorrow is asking about today
    and a watermark equal to ``date_to - 1`` is current, not one day behind.
    """
    return date.fromordinal(ctx.window.date_to.toordinal() - 1)


def _table_id(ctx: ResolverContext, fallback: str) -> str:
    return ctx.view.tables[0].id if ctx.view.tables else fallback


# ===========================================================================
# 62. Analytics Tracking Health
# ===========================================================================

#: Providers in the order an operator triages them. GA4 first: it is the only
#: one this deployment can verify server-side at all, and the only one the
#: purchase outbox depends on.
_PROVIDER_ORDER = ("ga4", "gtm", "clarity")

#: Rendered per outbox status. The second element is whether the count is
#: attribution *loss*. `SUPPRESSED_NO_CONSENT` is emphatically not: the purchase
#: was recorded internally and deliberately not transmitted, which is the system
#: working. Folding it into "not delivered" either raises a false alarm for
#: every consent-declining customer or hides a real outage behind a plausible
#: number, and both failures look identical on a count.
_OUTBOX_MEANING: dict[str, tuple[str, bool]] = {
    OutboxStatus.PENDING: (
        "Queued and not yet sent. Owed to GA4, not lost — unless nothing has "
        "ever been delivered, in which case the worker has never run.",
        False,
    ),
    OutboxStatus.DELIVERED: (
        "Accepted by the Measurement Protocol. The endpoint answers 204 to "
        "anything, so this is proof of transmission, never of ingestion.",
        False,
    ),
    OutboxStatus.FAILED: (
        "Retries exhausted. Attribution for these orders is permanently lost "
        "unless they are replayed.",
        True,
    ),
    OutboxStatus.SUPPRESSED_NO_CONSENT: (
        "Recorded internally and deliberately NOT transmitted, because analytics "
        "consent was not granted at purchase time. A correct outcome and never a "
        "delivery failure.",
        False,
    ),
}


def _provider_state(state: Mapping[str, Any]) -> str:
    """One word for a provider, and never a word that means "verified".

    No server-side check can establish that a browser tag loads on the
    storefront, fires on the right events, or survives an ad blocker —
    ``integrations.test_connection`` is explicit that only GA4 can return
    ``verified`` at all, and only about a credential pair. So the best state
    reachable here is ``configured_unverified``. A ``healthy`` would be the
    screen asserting the one thing it cannot see.
    """
    enabled = bool(state.get("enabled"))
    configured = bool(state.get("configured"))
    if enabled and not configured:
        return "enabled_without_id"
    if configured and not state.get("id_format_ok"):
        return "id_malformed"
    if not configured:
        return "not_configured"
    if not enabled:
        return "configured_but_off"
    return "configured_unverified"


def _provider_rows(probe: Mapping[str, Any]) -> list[dict[str, Any]]:
    providers = probe.get("providers") or {}
    rows: list[dict[str, Any]] = []
    for name in _PROVIDER_ORDER:
        state = providers.get(name) or {}
        rows.append(
            {
                "provider": name,
                "state": _provider_state(state),
                "enabled": bool(state.get("enabled")),
                "configured": bool(state.get("configured")),
                # Public ids by definition — echoing one leaks nothing the page
                # source of any site using them does not already publish.
                "id": state.get("id"),
                "id_format_ok": bool(state.get("id_format_ok")),
                # Never true from here. See `_provider_state`.
                "verified": False,
                "api_secret_state": state.get("api_secret_state"),
                "purchase_delivery": state.get("purchase_delivery"),
                "server_delivery_ready": bool(state.get("server_delivery_ready")),
            }
        )

    ga4 = providers.get("ga4") or {}
    rows.append(
        {
            # Not a tag — the READER. It is listed as its own row rather than
            # folded into the GA4 row because a store can have a perfectly
            # healthy measurement tag and still be unable to check a single
            # figure against GA4, which is exactly this deployment.
            "provider": "ga4_data_api",
            "state": "not_configured",
            "enabled": False,
            "configured": False,
            "id": None,
            "id_format_ok": False,
            "verified": False,
            "api_secret_state": ga4.get("data_api_credentials_state"),
            "purchase_delivery": None,
            "server_delivery_ready": False,
        }
    )
    return rows


def _outbox_rows(probe: Mapping[str, Any]) -> list[dict[str, Any]]:
    counts = (probe.get("outbox") or {}).get("counts") or {}
    return [
        {
            "status": status,
            "events": int(counts.get(status, 0) or 0),
            "is_delivery_loss": is_loss,
            "meaning": meaning,
        }
        for status, (meaning, is_loss) in _OUTBOX_MEANING.items()
    ]


def _summary_rows(probe: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Scalars the health probe measures, as facts. Severity lives in warnings.

    Deliberately not graded here. A key/value table that also carries a colour
    ends up being the thing a reader scans instead of the warnings, and the
    warnings are the half that is deduped, machine-readable and carries the
    remedy.
    """
    consent = probe.get("consent") or {}
    environment = probe.get("environment") or {}
    outbox = probe.get("outbox") or {}
    rollups = probe.get("rollups") or {}
    counts = outbox.get("counts") or {}

    return [
        {
            "item": "consent_mode",
            "value": consent.get("mode") or None,
            "detail": (
                "Decides whether a purchase with denied consent is recorded as "
                "SUPPRESSED_NO_CONSENT rather than transmitted."
            ),
        },
        {
            "item": "consent_default_analytics_storage",
            "value": consent.get("default_analytics_storage") or None,
            "detail": "Assumed before the visitor answers the banner.",
        },
        {
            "item": "consent_default_ad_storage",
            "value": consent.get("default_ad_storage") or None,
            "detail": "Independent of analytics storage.",
        },
        {
            "item": "consent_wait_for_update_ms",
            "value": consent.get("wait_for_update_ms") or None,
            "detail": (
                "Too short and a granted consent arrives after the page_view has "
                "already been suppressed."
            ),
        },
        {
            "item": "app_environment",
            "value": environment.get("app_environment"),
            "detail": f"Serving {environment.get('frontend_host') or 'an undeclared host'}.",
        },
        {
            "item": "declared_property_environment",
            "value": environment.get("declared_property_environment"),
            "detail": (
                "Undeclared: a deployment reporting into the wrong GA4 property "
                "cannot be detected, and every other signal looks healthy in both "
                "directions."
                if not environment.get("declared_property_environment")
                else "Which property the configured IDs point at."
            ),
        },
        {
            "item": "outbox_events_total",
            "value": int(counts.get("total", 0) or 0),
            "detail": "Every server-side GA4 event ever written, all statuses.",
        },
        {
            "item": "last_delivered_at",
            "value": outbox.get("last_delivered_at"),
            "detail": (
                "Transmission, not ingestion — the Measurement Protocol answers "
                "204 to everything."
            ),
        },
        {
            "item": "oldest_pending_occurred_at",
            "value": outbox.get("oldest_pending_occurred_at"),
            "detail": "The oldest conversion still owed to GA4.",
        },
        {
            "item": "last_error",
            "value": outbox.get("last_error"),
            "detail": f"At {outbox.get('last_error_at') or 'never'}.",
        },
        {
            "item": "rollup_watermark_oldest",
            "value": rollups.get("oldest_watermark_date"),
            "detail": (
                "The furthest-behind aggregation job. Every view built on it "
                "stops here, whatever the run log says."
            ),
        },
        {
            "item": "rollup_watermark_newest",
            "value": rollups.get("newest_watermark_date"),
            "detail": "The furthest-ahead aggregation job.",
        },
    ]


def _freshness_scan(
    ctx: ResolverContext,
) -> tuple[list[dict[str, Any]], list[SourceRef], list[str], list[str]]:
    """Watermark and row count for every rollup worth watching.

    Reads ``source_watermark`` directly rather than through ``probe_source``, on
    purpose: ``probe_source`` guards against a window that straddles a
    reporting-timezone rebuild and refuses to answer. Refusing here would black
    out the health screen during precisely the event an operator opens it to
    watch.
    """
    last_requested = _last_reporting_day(ctx)
    rows: list[dict[str, Any]] = []
    refs: list[SourceRef] = []
    never_built: list[str] = []
    stale: list[str] = []

    for source, label in HEALTH_SOURCES:
        watermark, count = ctx.repo.source_watermark(source, ctx.tz_generation)
        if watermark is None or count == 0:
            status, lag, rank = "never_built", None, NEVER_BUILT_RANK
            never_built.append(source)
        else:
            lag = (last_requested - watermark).days
            rank = lag
            if lag > 0:
                status = "stale"
                stale.append(source)
            else:
                status = "current"
        rows.append(
            {
                "source": source,
                "label": label,
                "status": status,
                "through": watermark.isoformat() if watermark else None,
                "rows": count,
                # None, not 0. A rollup that was never built is not "0 days
                # behind"; that is the freshest possible value and the exact
                # opposite of the truth.
                "lag_days": lag,
                # Larger is worse, and never null, so any client sort produces
                # the same worst-first order this list is already in.
                "staleness_rank": rank,
            }
        )
        refs.append(
            source_ref(source, label=label, rows=count, through=watermark)
        )

    rows.sort(key=lambda row: (-row["staleness_rank"], row["source"]))
    return rows, refs, never_built, stale


@custom_function("tracking_health")
def tracking_health(ctx: ResolverContext) -> ResolverResult:
    """View 62 — is anything actually being collected, and how stale is it.

    The view that must never look healthy by default. Four tables, and the
    reason there are four rather than one is that each answers a question with a
    different remedy: a stale rollup is a job to run, an unconfigured tag is a
    field to fill in, a FAILED outbox row is a replay, and a consent default is
    a policy decision.

    The registry declares an ``event_volume`` chart and it is deliberately NOT
    emitted. Per-hour event counts need a client-side stream that is not
    connected; returning ``{"event_volume": []}`` would let the chart draw an
    axis and read as "zero events received", which is a far stronger claim than
    "we cannot see events at all".
    """
    probe = integrations.tracking_health(ctx.db)
    rows, refs, never_built, stale = _freshness_scan(ctx)

    warnings: list[AnalyticsWarning] = []

    # Everything `integrations` found, in its own words. Restating these here
    # would give the health screen and the settings screen two vocabularies for
    # the same fault.
    for item in probe.get("warnings") or []:
        warnings.append(
            warn(
                str(item.get("code", "")).upper() or NOT_CONFIGURED,
                str(item.get("message", "")),
                severity=_severity(str(item.get("severity", ""))),
                source="analytics_integrations",
            )
        )

    ga4 = (probe.get("providers") or {}).get("ga4") or {}
    if not (ga4.get("configured") and ga4.get("enabled")):
        warnings.append(
            warn(
                TRACKING_UNCONFIGURED,
                "GA4 is not configured and enabled on this deployment, so no "
                "client-side analytics is being collected at all. Nothing on this "
                "screen may be read as 'tracking is healthy': the rollup table "
                "below is measured and current, and the tag half of the pipeline "
                "does not exist yet. An empty event stream here is unconfigured, "
                "not quiet.",
                severity="error",
                requires=[
                    Capability.GA4_MEASUREMENT.value,
                    Capability.GTM_CONTAINER.value,
                ],
                configured=bool(ga4.get("configured")),
                enabled=bool(ga4.get("enabled")),
            )
        )

    warnings.append(
        warn(
            NOT_CONFIGURED,
            "Event-level tracking health (events received per hour, tag coverage, "
            "consent state as the browser actually applied it) needs a client-side "
            "analytics stream. None is connected, so no event series is returned — "
            "an empty chart would read as 'zero events', which is a stronger claim "
            "than 'we cannot see events'. " + GA4_DATA_API_UNAVAILABLE,
            severity="warn",
            requires=[
                Capability.GA4_MEASUREMENT.value,
                Capability.GA4_DATA_API.value,
                Capability.GTM_CONTAINER.value,
            ],
        )
    )

    if never_built:
        warnings.append(
            warn(
                WarningCode.NO_ROLLUP_YET,
                f"{len(never_built)} rollup(s) have never been built for generation "
                f"{ctx.tz_generation}: {', '.join(never_built)}. They are listed "
                "first, with a null lag rather than a zero one — never built is the "
                "worst state a rollup can be in, not the freshest.",
                severity="warn",
                sources=never_built,
            )
        )
    if stale:
        warnings.append(
            warn(
                WarningCode.ROLLUP_STALE,
                f"{len(stale)} rollup(s) stop before "
                f"{_last_reporting_day(ctx).isoformat()}: " + ", ".join(stale),
                severity="warn",
                sources=stale,
            )
        )

    outbox_total = int(
        ((probe.get("outbox") or {}).get("counts") or {}).get("total", 0) or 0
    )
    refs.extend(
        [
            source_ref(
                "analytics_event_outbox",
                label="Server-side GA4 outbox",
                kind="live",
                rows=outbox_total,
            ),
            source_ref(
                "analytics_sync_runs",
                label="Aggregation job log",
                kind="live",
                rows=len((probe.get("rollups") or {}).get("jobs") or []),
            ),
            source_ref(
                "system_settings",
                label="Analytics integration settings",
                kind="live",
            ),
        ]
    )

    tables = {
        "rollup_freshness": TableBlock(
            rows=rows, total_rows=len(rows), truncated=False
        ),
        "tracking_providers": _block(_provider_rows(probe)),
        "event_delivery": _block(_outbox_rows(probe)),
        "tracking_summary": _block(_summary_rows(probe)),
    }

    return ResolverResult(
        tables=tables,
        sources=refs,
        warnings=warnings,
        # The rollup half is measured directly and is authoritative. The tag
        # half cannot be seen from a server at all, and no configuration would
        # change that, so the view as a whole is permanently INCOMPLETE.
        quality=MetricQuality.INCOMPLETE,
    ).rolled_up()


def _block(rows: list[dict[str, Any]]) -> TableBlock:
    return TableBlock(rows=rows, total_rows=len(rows), truncated=False)


# ===========================================================================
# 63. Data Reconciliation
# ===========================================================================

#: Checks that produce per-day variances, in the order the trend chart prefers
#: them. `rollup_vs_live` first because it is literally "the rollup against the
#: source of truth", which is what the chart is titled.
_TREND_PREFERENCE: tuple[str, ...] = (
    CheckKey.ROLLUP_VS_LIVE,
    CheckKey.REVENUE_BRIDGE,
)


def _variance_series(
    ctx: ResolverContext, report: ReconciliationReport
) -> dict[str, list[dict[str, Any]]]:
    """Daily variance for ONE check, and only for days it actually compared.

    Deliberately not densified and deliberately not pooled across checks.

    Not densified because ``0`` here means "checked, and it balanced". Filling a
    day that was never compared with a zero variance asserts a check that never
    ran, in the one place a reader is looking specifically for gaps.

    Not pooled because the chart declares a single ``variance_pct`` series on a
    date axis: two checks would put two points on one x, and the reader cannot
    see which check either belongs to. The chosen check is named on every point
    and in the empty case there is simply no series.
    """
    charts = [c for c in ctx.view.charts if c.x == "date"]
    if not charts:
        return {}
    for key in _TREND_PREFERENCE:
        try:
            check = report.by_key(key)
        except KeyError:  # pragma: no cover - CHECK_ORDER guarantees the row
            continue
        if not check.days:
            continue
        points = [
            {
                "date": day.bucket_date.isoformat(),
                "check_name": check.check_key,
                "variance_pct": day.difference_pct,
                "difference": day.difference,
            }
            for day in sorted(check.days, key=lambda d: d.bucket_date)
        ]
        return {charts[0].id: points}
    return {}


def _check_row(check: Any, period: str) -> dict[str, Any]:
    """``CheckResult.to_row`` plus the evidence the five columns cannot hold.

    ``to_row`` is the authority on the five declared columns — including that a
    passing check spells ``matched`` and that everything else, ``not_configured``
    included, travels through untranslated. The extras below are additive only;
    nothing here recomputes or overrides a value ``to_row`` produced.
    """
    row = check.to_row(period)
    row.update(
        {
            "label": check.label,
            "left_label": check.left_label,
            "left_value": check.left_value,
            "right_label": check.right_label,
            "right_value": check.right_value,
            "difference": check.difference,
            # NULL for a check that did not run. "0% of the window was compared"
            # and "the comparison is unavailable" are the same fact and only one
            # of them needs a number.
            "coverage_pct": check.coverage_pct,
            "population": check.population,
            "compared": check.compared,
            "ran": check.ran,
            "missing_ids": list(check.missing_ids),
            "duplicate_ids": list(check.duplicate_ids),
            "days_compared": len(check.days),
        }
    )
    return row


@custom_function("reconciliation_grid")
def reconciliation_grid(ctx: ResolverContext) -> ResolverResult:
    """View 63 — all six checks, including the ones that cannot run.

    ``run_reconciliation`` guarantees exactly one row per check in
    ``CHECK_ORDER`` whatever the data looks like, which is the property this
    view depends on: an empty variance table reads as "everything balances",
    the strongest claim the system can make, made from an absence.

    ``write_alerts=False``. The service writes one ``AnalyticsAlert`` per
    material variance so the Control Centre sees it whether or not anyone opens
    this page — which is exactly why *this page* must not be the thing that
    writes it. Alerts authored by a page view are dated to whenever somebody
    happened to look.
    """
    state = probe_source(ctx, _ORDER_SOURCE, label="Order rollup (daily)")
    warnings = list(state.warnings)

    report = run_reconciliation(
        ctx.db, ctx.window.date_from, ctx.window.date_to, write_alerts=False
    )
    rows = [_check_row(check, report.period) for check in report.checks]

    variances = report.variances
    not_configured_checks = report.not_configured
    errors = report.errors

    if variances:
        warnings.append(
            warn(
                RECONCILIATION_VARIANCE,
                f"{len(variances)} check(s) did not balance: "
                + ", ".join(c.check_key for c in variances)
                + ". The money identities here are exact by construction — both "
                "sides are the same query or the same arithmetic — so a difference "
                "is a defect, not a rounding artefact or a tolerance question.",
                severity="error",
                checks=[c.check_key for c in variances],
            )
        )
    if not_configured_checks:
        warnings.append(
            warn(
                NOT_CONFIGURED,
                f"{len(not_configured_checks)} check(s) could not be run: "
                + ", ".join(c.check_key for c in not_configured_checks)
                + ". They are listed with NULL values rather than omitted, so an "
                "empty variance table is never mistaken for a clean one, and none "
                "of them is reported as a 0.00% variance. "
                + GA4_DATA_API_UNAVAILABLE,
                severity="warn",
                checks=[c.check_key for c in not_configured_checks],
                requires=[Capability.GA4_DATA_API.value],
            )
        )
    if errors:
        warnings.append(
            warn(
                # An engineering failure, not a missing integration. Graded
                # `error` and named separately so it reaches a developer rather
                # than being triaged as another thing to go and connect.
                "RECONCILIATION_CHECK_ERROR",
                f"{len(errors)} check(s) raised while running: "
                + ", ".join(c.check_key for c in errors)
                + ". Their values are NULL because nothing was compared.",
                severity="error",
                checks=[c.check_key for c in errors],
            )
        )

    return ResolverResult(
        tables={
            _table_id(ctx, "variances"): TableBlock(
                rows=rows, total_rows=len(rows), truncated=False
            )
        },
        series=_variance_series(ctx, report),
        sources=[
            state.ref,
            source_ref(
                "orders",
                label="Orders (recomputed live)",
                kind="live",
            ),
        ],
        warnings=warnings,
        # The screen can never be better than its weakest check, and
        # `ga4_purchase_parity` is unrunnable on every deployment of this
        # codebase, so this is INCOMPLETE today and would stay INCOMPLETE even
        # with GA4 credentials pasted in.
        quality=(
            MetricQuality.INCOMPLETE
            if (not_configured_checks or errors)
            else MetricQuality.AUTHORITATIVE
        ),
    ).rolled_up()


# ===========================================================================
# 73. Alerts and Anomaly
# ===========================================================================


def _band(alert: AnalyticsAlert) -> Decimal | None:
    """The single ``expected`` number the declared column can carry.

    The band's breached edge, not its midpoint and not its width: the reader is
    asking "what should it not have gone past", and for an exact identity —
    where ``expected_low == expected_high`` — every candidate collapses to the
    same value anyway. ``expected_low`` and ``expected_high`` travel alongside
    untouched, so the full band is never lost.
    """
    low, high = alert.expected_low, alert.expected_high
    actual = alert.actual_value
    if actual is not None:
        if low is not None and actual < low:
            return low
        if high is not None and actual > high:
            return high
    return high if high is not None else low


def _evidence(alert: AnalyticsAlert) -> Mapping[str, Any]:
    return ((alert.context or {}).get("evidence") or {}) if alert.context else {}


def _alert_row(alert: AnalyticsAlert) -> dict[str, Any]:
    evidence = _evidence(alert)
    lifecycle = ((alert.context or {}).get("lifecycle") or {}) if alert.context else {}
    return {
        "alert_id": int(alert.id),
        "fired_at": alert.detected_at.isoformat() if alert.detected_at else None,
        "rule": alert.rule_key,
        "metric": alert.metric,
        "observed": alert.actual_value,
        "expected": _band(alert),
        "expected_low": alert.expected_low,
        "expected_high": alert.expected_high,
        "severity": alert.severity,
        "severity_rank": SEVERITY_RANK.get(alert.severity, len(SEVERITY_RANK)),
        "status": alert.status,
        # Acknowledged and resolved are different answers and both are different
        # from muted: a muted alert is a condition somebody decided to live with,
        # not one that was fixed.
        "acknowledged_at": (
            alert.acknowledged_at.isoformat() if alert.acknowledged_at else None
        ),
        "acknowledged_by_user_id": alert.acknowledged_by_user_id,
        "resolution_note": alert.resolution_note,
        "bucket_date": alert.bucket_date.isoformat() if alert.bucket_date else None,
        "dimension": alert.dimension,
        "dimension_value": alert.dimension_value,
        "direction": evidence.get("direction"),
        "deviation_ratio": evidence.get("deviation_ratio"),
        "range_basis": evidence.get("range_basis"),
        "evaluations": lifecycle.get("evaluations"),
    }


def _summary_by_severity(alerts: list[AnalyticsAlert]) -> list[dict[str, Any]]:
    """Open counts first, because that is the number an operator acts on.

    Every status is carried per severity rather than only the open ones: a
    severity whose alerts are all resolved and a severity that never fired
    render identically as a zero, and only one of them means the store had a
    problem this week.
    """
    rows: list[dict[str, Any]] = []
    for severity in (AlertSeverity.CRITICAL, AlertSeverity.WARNING, AlertSeverity.INFO):
        of_severity = [a for a in alerts if a.severity == severity]
        rows.append(
            {
                "severity": severity,
                "severity_rank": SEVERITY_RANK[severity],
                "open": sum(1 for a in of_severity if a.status == AlertStatus.OPEN),
                "acknowledged": sum(
                    1 for a in of_severity if a.status == AlertStatus.ACKNOWLEDGED
                ),
                "resolved": sum(
                    1 for a in of_severity if a.status == AlertStatus.RESOLVED
                ),
                "muted": sum(1 for a in of_severity if a.status == AlertStatus.MUTED),
                "total": len(of_severity),
            }
        )
    return rows


def _alerts_trend(
    ctx: ResolverContext, alerts: list[AnalyticsAlert]
) -> dict[str, list[dict[str, Any]]]:
    """Alerts per reporting day, SPARSE.

    A day with no bar is a day nothing was recorded against, which may mean the
    detector ran and found nothing or that it never ran at all — and
    ``analytics_alerts`` alone cannot tell those apart. Zero-filling would pick
    the flattering one and draw it as a measurement.
    """
    charts = [c for c in ctx.view.charts if c.x == "date"]
    if not charts:
        return {}
    buckets: dict[date, dict[str, int]] = {}
    for alert in alerts:
        if alert.bucket_date is None:
            continue
        entry = buckets.setdefault(
            alert.bucket_date,
            {"alerts": 0, AlertSeverity.CRITICAL: 0, AlertSeverity.WARNING: 0,
             AlertSeverity.INFO: 0},
        )
        entry["alerts"] += 1
        if alert.severity in entry:
            entry[alert.severity] += 1
    if not buckets:
        return {}
    points = [
        {
            "date": day.isoformat(),
            "alerts": counts["alerts"],
            "critical": counts[AlertSeverity.CRITICAL],
            "warning": counts[AlertSeverity.WARNING],
            "info": counts[AlertSeverity.INFO],
        }
        for day, counts in sorted(buckets.items())
    ]
    return {charts[0].id: points}


def _rule_verdicts(ctx: ResolverContext, bucket: date) -> dict[str, Any]:
    """Re-evaluate every rule for ``bucket`` WITHOUT writing anything.

    ``AnomalyDetector`` upserts alert rows and flushes — the caller owns the
    transaction, which for the aggregation worker is correct and for a page view
    is not. A SAVEPOINT rolled back unconditionally gives this view the
    detector's verdict at zero cost to the alert table.

    Everything needed is snapshotted into plain values *inside* the savepoint:
    ``run.alerts`` are live ORM objects that the rollback expunges, whereas
    ``RuleSkip`` and ``RuleClear`` are frozen dataclasses and survive it. The
    thing this function must never return is a detached ORM row that lazily
    reloads and resurrects an alert nobody detected.
    """
    savepoint = ctx.db.begin_nested()
    try:
        run = anomalies.run(ctx.db, bucket, tz_generation=ctx.tz_generation)
        fired: dict[str, int] = {}
        for alert in run.alerts:
            fired[alert.rule_key] = fired.get(alert.rule_key, 0) + 1
        skips = {
            skip.rule_key: {
                "reason": skip.reason,
                "message": skip.message,
                "detail": dict(skip.detail),
            }
            for skip in run.skips
        }
        clear = {
            item.rule_key: {
                "actual": item.actual,
                "expected_low": item.expected.low,
                "expected_high": item.expected.high,
                "range_basis": item.expected.basis,
            }
            for item in run.clear
        }
    finally:
        savepoint.rollback()
    return {"fired": fired, "skips": skips, "clear": clear}


def _rule_coverage_rows(
    verdicts: Mapping[str, Any] | None,
    stored_by_rule: Mapping[str, int],
    bucket: date,
) -> list[dict[str, Any]]:
    """One row per detection rule, with SKIPPED and CLEAR kept apart.

    ``RuleSkip`` and ``RuleClear`` exist as separate types in ``anomalies``
    precisely because both render as an absent alert, and the difference decides
    whether a quiet screen means a healthy store or a detector that has never
    once run. That difference is carried here on ``state``, plus the skip's
    machine-readable ``reason`` and the minimum baseline the rule refused to
    work without — so "why is this empty?" is answered on the row rather than in
    a log nobody reads.
    """
    fired = (verdicts or {}).get("fired") or {}
    skips = (verdicts or {}).get("skips") or {}
    clear = (verdicts or {}).get("clear") or {}

    rows: list[dict[str, Any]] = []
    for rule_key, spec in anomalies.RULES.items():
        skip = skips.get(rule_key)
        cleared = clear.get(rule_key)
        alerts_now = int(fired.get(rule_key, 0))
        if verdicts is None:
            state = RULE_NOT_EVALUATED
        elif alerts_now:
            state = RULE_ALERTING
        elif skip is not None:
            state = RULE_SKIPPED
        elif cleared is not None:
            state = RULE_CLEAR
        else:
            state = RULE_NOT_EVALUATED
        rows.append(
            {
                "rule": rule_key,
                "metric": spec.metric,
                "direction": spec.direction,
                "state": state,
                "state_rank": RULE_STATE_RANK[state],
                "bucket_date": bucket.isoformat(),
                "alerts_now": alerts_now,
                "alerts_stored": int(stored_by_rule.get(rule_key, 0)),
                "skip_reason": (skip or {}).get("reason"),
                "skip_message": (skip or {}).get("message"),
                "actual": (cleared or {}).get("actual"),
                "expected_low": (cleared or {}).get("expected_low"),
                "expected_high": (cleared or {}).get("expected_high"),
                "range_basis": (cleared or {}).get("range_basis"),
                "min_baseline_days": spec.min_baseline_days,
                "baseline_rationale": spec.rationale,
            }
        )
    rows.sort(key=lambda row: (row["state_rank"], row["rule"]))
    return rows


@custom_function("anomaly_feed")
def anomaly_feed(ctx: ResolverContext) -> ResolverResult:
    """View 73 — the alerts that fired, and the rules that did not.

    Two tables, and the second is the one that matters. ``alert_feed`` is what
    the detector recorded; ``rule_coverage`` is why everything else is silent.
    An alerting system's failure mode is not a missed incident, it is total
    silence that reads as calm — so a screen that shows only the alerts cannot
    distinguish a healthy store from a detector that has never run.
    """
    warnings: list[AnalyticsWarning] = []
    bucket = _last_reporting_day(ctx)

    # Read the persisted feed BEFORE re-evaluating. The detector refreshes OPEN
    # rows in place, and the savepoint rollback would expire anything it touched
    # — the feed must show what is stored, not what a dry run rewrote.
    alerts = list(
        ctx.db.execute(
            select(AnalyticsAlert)
            .where(
                AnalyticsAlert.bucket_date >= ctx.window.date_from,
                AnalyticsAlert.bucket_date < ctx.window.date_to,
            )
            .order_by(AnalyticsAlert.detected_at.desc(), AnalyticsAlert.id.desc())
        )
        .scalars()
        .all()
    )
    rows = [_alert_row(a) for a in alerts]
    # Severity is the primary key on purpose: it is what the column headings
    # promise and what an operator triages by. Status breaks ties so an
    # unacknowledged critical sits above a resolved one of the same grade.
    rows.sort(
        key=lambda row: (
            row["severity_rank"],
            STATUS_RANK.get(row["status"], len(STATUS_RANK)),
            row["fired_at"] or "",
        )
    )

    stored_by_rule: dict[str, int] = {}
    for alert in alerts:
        stored_by_rule[alert.rule_key] = stored_by_rule.get(alert.rule_key, 0) + 1

    # Alerts about the system rather than a bucket carry a NULL bucket_date and
    # fall outside every window. Counted rather than silently dropped: an alert
    # no window can show is an alert nobody will ever action.
    undated = int(
        ctx.db.execute(
            select(func.count())
            .select_from(AnalyticsAlert)
            .where(AnalyticsAlert.bucket_date.is_(None))
        ).scalar_one()
        or 0
    )
    if undated:
        warnings.append(
            warn(
                "ALERTS_OUTSIDE_WINDOW",
                f"{undated} alert(s) carry no bucket date and therefore appear in "
                "no date window, including this one. They are counted here rather "
                "than omitted, because an alert that no window can show is an alert "
                "nobody will action.",
                severity="warn",
                undated_alerts=undated,
            )
        )

    verdicts: dict[str, Any] | None
    try:
        verdicts = _rule_verdicts(ctx, bucket)
    except Exception as exc:  # noqa: BLE001 - a detector bug must not blank the screen
        verdicts = None
        warnings.append(
            warn(
                DETECTOR_UNAVAILABLE,
                f"The anomaly rules could not be re-evaluated for "
                f"{bucket.isoformat()}: {type(exc).__name__}: {exc}. Every rule "
                "below is reported as not evaluated rather than clear — a rule "
                "whose evaluator raised has produced no verdict, and treating its "
                "silence as a clean bill of health is how an alerting system goes "
                "quiet without anyone noticing.",
                severity="error",
                bucket_date=bucket.isoformat(),
            )
        )

    coverage = _rule_coverage_rows(verdicts, stored_by_rule, bucket)

    skipped = [r["rule"] for r in coverage if r["state"] == RULE_SKIPPED]
    if skipped:
        warnings.append(
            warn(
                WarningCode.SMALL_SAMPLE,
                f"{len(skipped)} rule(s) could not be evaluated for "
                f"{bucket.isoformat()} and were skipped, not cleared: "
                + ", ".join(skipped)
                + ". Each row names the reason. A skipped rule is watching nothing; "
                "it is not evidence that nothing happened.",
                severity="warn",
                rules=skipped,
                bucket_date=bucket.isoformat(),
            )
        )

    unpersisted = [
        r["rule"]
        for r in coverage
        if r["state"] == RULE_ALERTING and r["alerts_stored"] == 0
    ]
    if unpersisted:
        warnings.append(
            warn(
                ALERT_NOT_PERSISTED,
                f"{len(unpersisted)} rule(s) breach their expected range for "
                f"{bucket.isoformat()} right now with no alert recorded: "
                + ", ".join(unpersisted)
                + ". This page never writes alerts, so the detector has not run "
                "since that data landed. Nothing has been routed, acknowledged or "
                "sent to anyone for these.",
                severity="error",
                rules=unpersisted,
                bucket_date=bucket.isoformat(),
            )
        )

    watermark, order_rows = ctx.repo.source_watermark(_ORDER_SOURCE, ctx.tz_generation)

    return ResolverResult(
        series=_alerts_trend(ctx, alerts),
        tables={
            _table_id(ctx, "alert_feed"): TableBlock(
                rows=rows, total_rows=len(rows), truncated=False
            ),
            "alert_summary": _block(_summary_by_severity(alerts)),
            "rule_coverage": _block(coverage),
        },
        sources=[
            source_ref(
                "analytics_alerts",
                label="Detected alerts",
                kind="live",
                rows=len(alerts),
            ),
            source_ref(
                _ORDER_SOURCE,
                label="Order rollup (daily)",
                rows=order_rows,
                through=watermark,
            ),
        ],
        warnings=warnings,
        # Every band rule reads a rollup, so the alert list is only as complete
        # as the pipeline underneath it — and at least one rule is skipped on
        # any store without a full baseline. Graded INCOMPLETE whenever a rule
        # did not produce a verdict, AUTHORITATIVE only when all of them did.
        quality=(
            MetricQuality.AUTHORITATIVE
            if coverage
            and all(
                row["state"] in (RULE_ALERTING, RULE_CLEAR) for row in coverage
            )
            else MetricQuality.INCOMPLETE
        ),
    ).rolled_up()
