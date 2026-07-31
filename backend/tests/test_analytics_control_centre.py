"""The three Analytics Control Centre views: 62, 63 and 73.

These are the only views in the registry whose subject is the reporting system
itself, which gives them an obligation the other seventy do not have. Every
other view that cannot see something renders a shorter line and the reader draws
the right conclusion. A health screen that cannot see something renders *green*.

So every test below is a test about an absence being visible:

  * ``test_ga4_parity_reports_not_configured_and_never_a_match`` — the one that
    matters most. ``ga4_purchase_parity`` cannot run on any deployment of this
    codebase: ``analytics/ga4.py`` speaks the Measurement Protocol, which sends
    events and cannot read back what GA4 kept, so there is no counterparty to
    compare against and no credential that would create one. The check must be
    listed with NULL values and status ``not_configured``. The failure this
    catches is it reporting ``match`` — six green ticks on a screen where only
    five checks ran, which is the strongest claim the system makes, made from
    an absence.
  * ``test_tracking_health_reports_unconfigured_rather_than_healthy`` — with no
    GA4 configuration the view must say so at ``error`` severity and grade
    itself INCOMPLETE. The failure it catches is a screen with no warnings on
    it, which reads as "tracking is fine" when nothing is being collected at
    all.
  * ``test_a_never_built_rollup_sorts_as_the_worst_state`` — a rollup that has
    never been built has ``lag_days = None``, and a table sorted by lag would
    put it at the top as the *freshest* thing on the screen. The row therefore
    carries ``staleness_rank``, which is never null and on which larger is
    worse.
  * ``test_rule_coverage_tells_skipped_apart_from_clear`` — a skipped rule and
    a clear rule both render as an absent alert. Only one of them means the
    store was actually checked, and an alerting system whose silence cannot be
    interpreted is an alerting system that has been muted.
  * ``test_opening_the_alerts_view_writes_no_alerts`` — the anomaly detector
    writes rows. This view re-runs it to get the skip/clear verdict and must
    leave the alert table byte-identical, because an alert list that grows when
    somebody looks at it is a list nobody can reason about.

Isolation strategy
------------------
House style, following ``test_analytics_view_bindings.py`` and
``test_analytics_remaining_views.py``: no db fixture in ``conftest.py``; the
module owns its ``SessionLocal()`` and tears down in a ``finally``. Fixtures are
written under the database's **active** generation, because
``AnalyticsViewService`` reads that from the database and cannot be told
otherwise, so isolation comes from the date range instead: **February 1996**, a
sandbox no other suite uses (1990, 2001-2015, 2024 and 2026 are all taken).
Everything is deleted both before seeding and in ``finally``, so a rerun after a
crashed run is clean.

Forty consecutive days are seeded rather than five. The anomaly rules refuse to
fire below ``forecasting.MIN_HISTORY_DAYS`` (28) observed days — that refusal is
half of what these tests assert, and a five-day fixture could only ever produce
skips, so CLEAR would be untestable.

No login: the service only ever calls ``user.has_permission``, so a stub that
grants exactly ``analytics.control_centre.view`` exercises the real
authorisation path without creating a user row.

Run inside the analytics container::

    docker exec wvana-py python -m pytest tests/test_analytics_control_centre.py -q
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Iterator

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import analytics_rollups as rollups
from app.models.analytics_control import (
    AlertRuleKey,
    AlertSeverity,
    AlertStatus,
    AnalyticsAlert,
    AnalyticsEventOutbox,
    ConsentState,
    OutboxEventName,
    OutboxStatus,
)
from app.schemas.analytics_view import AnalyticsViewEnvelope
from app.services.analytics import integrations, registry
from app.services.analytics.anomalies import RULES, SkipReason
from app.services.analytics.filters import AnalyticsFilters, Comparison, Period
from app.services.analytics.reconciliation import CHECK_ORDER, CheckKey, CheckStatus
from app.services.analytics.resolvers.base import NOT_CONFIGURED, ResolverContext
from app.services.analytics.resolvers.control_centre import (
    NEVER_BUILT_RANK,
    RULE_CLEAR,
    RULE_SKIPPED,
    SEVERITY_RANK,
    TRACKING_UNCONFIGURED,
    _freshness_scan,
)
from app.services.analytics.timebox import active_generation
from app.services.analytics.types import MetricQuality, ResolverId, ViewState
from app.services.analytics.view_service import AnalyticsViewService

# ---------------------------------------------------------------------------
# The three views
# ---------------------------------------------------------------------------
# (number, module slug, view slug). Slugs rather than indices, so a registry
# reorder cannot silently repoint a test at a different view.

MODULE = "control-centre"
TRACKING_HEALTH = (62, MODULE, "analytics-tracking-health")
RECONCILIATION = (63, MODULE, "data-reconciliation")
ALERTS = (73, MODULE, "alerts-and-anomaly")
CONTROL_VIEWS = (TRACKING_HEALTH, RECONCILIATION, ALERTS)

CONTROL_PERMISSION = "analytics.control_centre.view"

# February 1996 — no other analytics suite uses it.
SANDBOX_START = date(1996, 2, 1)
#: Enough observed days for `forecasting.fit` to apply a day-of-week factor
#: (28) with room to spare, so a rule can reach CLEAR rather than only SKIPPED.
SANDBOX_DAYS = 40
#: The last reporting day of the window, and therefore the bucket every resolver
#: here judges: the window is half-open, so `date_to - 1` is what it asks about.
BUCKET = SANDBOX_START + timedelta(days=SANDBOX_DAYS - 1)
SANDBOX_END = SANDBOX_START + timedelta(days=90)

#: Prefix on every seeded outbox transaction id, so teardown can delete exactly
#: this module's rows out of a table it shares with the real store.
TXN_PREFIX = "CC-SANDBOX-1996-"

#: Ten is `RULES[GA4_SYNC_FAILURE].min_bucket_sample`; below it the rule reports
#: INSUFFICIENT_BUCKET_SAMPLE instead of a verdict, and CLEAR is what this
#: fixture exists to produce. The suppressed row is deliberately extra: the rule
#: excludes it from both sides, so it must not count toward the ten.
DELIVERED_EVENTS = 11
SUPPRESSED_EVENTS = 1


class _ControlCentreReader:
    """Holds exactly the Control Centre permission, and nothing else.

    `AnalyticsViewService` only ever calls `has_permission`, so this exercises
    the real authorisation path without creating a user row — and without
    reaching for a real admin account, whose `is_admin` short circuit would make
    every check pass for the wrong reason.
    """

    is_admin = False

    def has_permission(self, permission: str) -> bool:
        return permission == CONTROL_PERMISSION


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _day(offset: int) -> date:
    return SANDBOX_START + timedelta(days=offset)


def _purge(db: Session, generation: int) -> None:
    db.execute(
        delete(rollups.AggOrderDaily).where(
            rollups.AggOrderDaily.tz_generation == generation,
            rollups.AggOrderDaily.bucket_date >= SANDBOX_START,
            rollups.AggOrderDaily.bucket_date <= SANDBOX_END,
        )
    )
    db.execute(
        delete(AnalyticsAlert).where(
            AnalyticsAlert.bucket_date >= SANDBOX_START,
            AnalyticsAlert.bucket_date <= SANDBOX_END,
        )
    )
    db.execute(
        delete(AnalyticsEventOutbox).where(
            AnalyticsEventOutbox.transaction_id.like(f"{TXN_PREFIX}%")
        )
    )
    db.commit()


def _seed(db: Session, generation: int) -> None:
    """Forty flat trading days, one day of GA4 deliveries, three alerts.

    The revenue bridge balances on every row (900 - 50 + 50 + 30 + 0 - 0 = 930),
    so `revenue_bridge` reports `match` and the reconciliation table's variance
    rows are the checks that genuinely could not run rather than arithmetic this
    fixture got wrong.

    Revenue is flat rather than random because two of the rules under test are
    supposed to come back CLEAR. `forecasting` floors the residual scale at 5%
    of the mean level, so a constant series still yields a band with width —
    a flat fixture produces a confident "inside the range", not a degenerate one.
    """
    for offset in range(SANDBOX_DAYS):
        db.add(
            rollups.AggOrderDaily(
                bucket_date=_day(offset),
                tz_generation=generation,
                orders_total=10,
                orders_pending=1,
                orders_paid=5,
                orders_shipped=2,
                orders_delivered=1,
                orders_cancelled=1,
                order_value_created=Decimal("1000.00"),
                paid_order_value=Decimal("800.00"),
                gross_merchandise_sales=Decimal("900.00"),
                net_merchandise_sales=Decimal("850.00"),
                net_revenue=Decimal("930.00"),
                subtotal_sum=Decimal("900.00"),
                tax_sum=Decimal("50.00"),
                discount_sum=Decimal("50.00"),
                shipping_income=Decimal("30.00"),
                refund_sum=Decimal("0.00"),
                cogs_sum=Decimal("400.00"),
                units=20,
                costed_units=20,
                distinct_customers=8,
                new_customers=5,
                returning_customers=3,
            )
        )

    # `_ga4_sync_failure` buckets the outbox on the UTC calendar day of
    # `occurred_at`, so these are written as naive UTC midnight-plus-an-hour on
    # BUCKET — the same frame every other analytics timestamp uses.
    occurred = datetime.combine(BUCKET, datetime.min.time()) + timedelta(hours=1)
    for index in range(DELIVERED_EVENTS):
        db.add(
            AnalyticsEventOutbox(
                event_name=OutboxEventName.PURCHASE,
                transaction_id=f"{TXN_PREFIX}D{index:03d}",
                order_id=None,
                occurred_at=occurred,
                payload={"value": 100, "currency": "INR"},
                consent_state=ConsentState.GRANTED,
                status=OutboxStatus.DELIVERED,
                delivered_at=occurred + timedelta(minutes=1),
            )
        )
    for index in range(SUPPRESSED_EVENTS):
        db.add(
            AnalyticsEventOutbox(
                event_name=OutboxEventName.PURCHASE,
                transaction_id=f"{TXN_PREFIX}S{index:03d}",
                order_id=None,
                occurred_at=occurred,
                payload={"value": 100, "currency": "INR"},
                consent_state=ConsentState.DENIED,
                status=OutboxStatus.SUPPRESSED_NO_CONSENT,
            )
        )

    # Dated five to seven days before BUCKET on purpose. `AnomalyDetector._upsert`
    # matches on (rule_key, bucket_date, dimension, dimension_value); putting
    # these on BUCKET would let the read-time re-evaluation adopt and rewrite
    # them, and the feed is supposed to show what is stored.
    for offset, (severity, status) in enumerate(
        (
            (AlertSeverity.INFO, AlertStatus.RESOLVED),
            (AlertSeverity.CRITICAL, AlertStatus.OPEN),
            (AlertSeverity.WARNING, AlertStatus.ACKNOWLEDGED),
        )
    ):
        bucket = BUCKET - timedelta(days=5 + offset)
        detected = datetime.combine(bucket, datetime.min.time()) + timedelta(hours=6)
        db.add(
            AnalyticsAlert(
                rule_key=AlertRuleKey.SALES_DROP,
                severity=severity,
                metric="net_revenue",
                dimension="-",
                dimension_value="-",
                expected_low=Decimal("900.0000"),
                expected_high=Decimal("960.0000"),
                actual_value=Decimal("410.0000"),
                bucket_date=bucket,
                detected_at=detected,
                status=status,
                acknowledged_at=(
                    detected + timedelta(hours=1)
                    if status == AlertStatus.ACKNOWLEDGED
                    else None
                ),
                acknowledged_by_user_id=(
                    99 if status == AlertStatus.ACKNOWLEDGED else None
                ),
                resolution_note=(
                    "Coupon misconfiguration, corrected."
                    if status == AlertStatus.RESOLVED
                    else None
                ),
                context={"evidence": {"direction": "below", "deviation_ratio": "2.5"}},
            )
        )
    db.commit()


@contextmanager
def sandbox() -> Iterator[tuple[Session, int]]:
    """A session plus one seeded February 1996 window, deleted unconditionally.

    The generation is the database's ACTIVE one rather than a private random
    value, because `AnalyticsViewService` reads it from the database and cannot
    be told otherwise. Isolation therefore comes from the date range: 1996
    predates this store entirely.
    """
    db = SessionLocal()
    generation = int(active_generation(db).generation)
    try:
        _purge(db, generation)
        _seed(db, generation)
        yield db, generation
    finally:
        try:
            db.rollback()
            _purge(db, generation)
        finally:
            db.close()


def _filters() -> AnalyticsFilters:
    return AnalyticsFilters(
        period=Period.CUSTOM,
        date_from=SANDBOX_START,
        date_to=BUCKET + timedelta(days=1),
        comparison=Comparison.NONE,
    )


def _resolve(db: Session, view_slug: str) -> AnalyticsViewEnvelope:
    service = AnalyticsViewService(db, _ControlCentreReader())
    envelope = service.resolve_view(MODULE, view_slug, _filters(), use_cache=False)
    assert isinstance(envelope, AnalyticsViewEnvelope), (
        f"{view_slug} returned a gated envelope; all three Control Centre views "
        "are LIVE and must resolve."
    )
    return envelope


def _rows(envelope: AnalyticsViewEnvelope, table_id: str) -> list[dict[str, Any]]:
    assert table_id in envelope.tables, (
        f"expected a {table_id!r} table; got {sorted(envelope.tables)}"
    )
    return envelope.tables[table_id].rows


def _codes(envelope: AnalyticsViewEnvelope) -> set[str]:
    return {w.code for w in envelope.warnings}


# ---------------------------------------------------------------------------
# 1. Wiring: the registry points these three at registered functions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "number,module_slug,view_slug", CONTROL_VIEWS, ids=[v[2] for v in CONTROL_VIEWS]
)
def test_the_three_views_dispatch_to_a_custom_function(
    number: int, module_slug: str, view_slug: str
):
    """No database. A wiring typo must fail at import, not at request time."""
    from app.services.analytics.resolvers.special import CUSTOM_FUNCTIONS

    view = registry.get_view(module_slug, view_slug)
    assert view is not None and view.number == number
    assert view.resolver is ResolverId.CUSTOM
    fn = view.params.get("fn")
    assert fn in CUSTOM_FUNCTIONS, (
        f"View {number} ({view_slug}) dispatches to {fn!r}, which is not "
        f"registered. Known: {sorted(CUSTOM_FUNCTIONS)}."
    )
    assert view.state is ViewState.LIVE, (
        "Wiring a view must not change what it claims it can show."
    )


def test_all_three_resolve_end_to_end_with_provenance():
    """Each returns a data envelope naming what it read.

    `sources` is the machine-readable half of the answer: `not_configured`
    returns an explicitly EMPTY list, so a non-empty one is the difference
    between "nothing is connected" and "connected, and here is what it says".
    """
    with sandbox() as (db, _generation):
        for number, _module, slug in CONTROL_VIEWS:
            envelope = _resolve(db, slug)
            assert envelope.sources, (
                f"View {number} ({slug}) returned no sources, which is how this "
                "subsystem says 'nothing is wired up'."
            )
            assert envelope.tables, (
                f"View {number} ({slug}) returned an envelope with no tables."
            )
            assert envelope.quality == MetricQuality.INCOMPLETE.value, (
                f"View {number} ({slug}) graded itself {envelope.quality}. Every "
                "Control Centre view depends on something it cannot see — the GA4 "
                "Data API at minimum — so none of them may present as complete."
            )
            assert envelope.availability == ViewState.PARTIAL.value, (
                f"View {number} ({slug}) reports availability "
                f"{envelope.availability}. An INCOMPLETE run downgrades a LIVE "
                "view to PARTIAL for that request; reporting LIVE would put a "
                "green badge over a screen with holes in it."
            )


# ---------------------------------------------------------------------------
# 2. View 63: a check that cannot run is never a match
# ---------------------------------------------------------------------------


def test_ga4_parity_reports_not_configured_and_never_a_match():
    """The test this file exists for.

    There is no GA4 Data API client anywhere in this codebase, so backend paid
    orders have no counterparty to be compared against. The check must occupy a
    visible row with NULL values and status `not_configured` — never `match`,
    never `matched`, and never a 0.00% variance, all three of which read as
    "we checked".
    """
    with sandbox() as (db, _generation):
        envelope = _resolve(db, RECONCILIATION[2])
        rows = {r["check_name"]: r for r in _rows(envelope, "variances")}

        assert set(rows) == set(CHECK_ORDER), (
            "The variance table must carry one row per check whatever the data "
            "looks like. A check that disappears when it cannot run leaves an "
            "empty table, and an empty variance table reads as a clean bill of "
            f"health. Got: {sorted(rows)}"
        )

        ga4 = rows[CheckKey.GA4_PURCHASE_PARITY]
        assert ga4["status"] == CheckStatus.NOT_CONFIGURED
        assert ga4["status"] not in (CheckStatus.MATCH, "matched"), (
            "ga4_purchase_parity reported as a passing check. Nothing was "
            "compared: analytics/ga4.py implements the Measurement Protocol, "
            "which can send events and cannot read back what GA4 recorded, so "
            "there is no counterparty and no credential that would create one."
        )
        for field in ("source_value", "rollup_value", "variance_pct", "coverage_pct"):
            assert ga4[field] is None, (
                f"ga4_purchase_parity carries {field}={ga4[field]!r}. A check "
                "that did not run must have NULL values — a 0.00% variance and "
                "a 0% coverage both read as a measurement that was taken."
            )
        assert ga4["ran"] is False

        assert NOT_CONFIGURED in _codes(envelope), (
            "The unrunnable checks must be named in a warning as well as listed, "
            "so a caller that reads only the warnings still learns the table is "
            "not a complete audit."
        )


def test_a_check_that_ran_reports_a_status_the_unrunnable_ones_cannot():
    """The bridge identity is self-proving, so it must actually be evaluated.

    Without this, `test_ga4_parity_reports_not_configured_and_never_a_match`
    would still pass over a table where *every* check said `not_configured` —
    which is a screen that has stopped checking anything.
    """
    with sandbox() as (db, _generation):
        envelope = _resolve(db, RECONCILIATION[2])
        rows = {r["check_name"]: r for r in _rows(envelope, "variances")}

        bridge = rows[CheckKey.REVENUE_BRIDGE]
        assert bridge["status"] == "matched", (
            "The seeded rows satisfy gross - discounts + tax + shipping + "
            "cod_surcharge - refunds == net_revenue on every day, so the bridge "
            f"identity must report a match. Got {bridge['status']!r}: "
            f"{bridge['detail']}"
        )
        assert bridge["ran"] is True
        assert bridge["source_value"] is not None
        assert bridge["rollup_value"] is not None
        assert bridge["days_compared"] == SANDBOX_DAYS


def test_the_variance_trend_only_charts_days_that_were_compared():
    """Sparse by construction: a 0 here means "checked and balanced".

    Zero-filling a day nobody compared would assert a check that never ran, in
    the one place on the product a reader is looking specifically for gaps.
    """
    with sandbox() as (db, _generation):
        envelope = _resolve(db, RECONCILIATION[2])
        points = envelope.series.get("variance_trend") or []
        assert points, "40 seeded days carry a rollup row and must be charted."
        assert len(points) == SANDBOX_DAYS
        charted = {p["date"] for p in points}
        assert charted == {_day(o).isoformat() for o in range(SANDBOX_DAYS)}
        assert {p["check_name"] for p in points} == {CheckKey.ROLLUP_VS_LIVE}, (
            "Every point must name the check it belongs to. The chart declares "
            "one series on a date axis, so pooling two checks would put two "
            "points on one x with nothing to tell them apart."
        )


# ---------------------------------------------------------------------------
# 3. View 62: unconfigured is not healthy
# ---------------------------------------------------------------------------


def test_tracking_health_reports_unconfigured_rather_than_healthy(monkeypatch):
    """With no GA4 configuration the view must say so, loudly.

    `integrations.current_values` is patched rather than the settings rows
    rewritten: the rest of `tracking_health()` — the outbox scan, the secret
    state, the environment report — still runs for real against the database,
    so this exercises the actual code path under a configuration the test
    controls rather than one an operator might change tomorrow.
    """
    real_values = integrations.current_values

    def blank_ga4(db) -> dict[str, str]:
        values = dict(real_values(db))
        values.update(
            {
                "analytics.ga4_enabled": "false",
                "analytics.ga4_measurement_id": "",
                "analytics.ga4_property_id": "",
                "analytics.gtm_enabled": "false",
                "analytics.gtm_container_id": "",
            }
        )
        return values

    monkeypatch.setattr(integrations, "current_values", blank_ga4)

    with sandbox() as (db, _generation):
        envelope = _resolve(db, TRACKING_HEALTH[2])

        assert TRACKING_UNCONFIGURED in _codes(envelope), (
            "No GA4 measurement id is saved and the tag is switched off, so "
            "nothing client-side is being collected. The view must state that; "
            "a health screen with no warnings on it reads as 'tracking is fine'."
        )
        unconfigured = next(
            w for w in envelope.warnings if w.code == TRACKING_UNCONFIGURED
        )
        assert unconfigured.severity == "error"
        assert envelope.quality == MetricQuality.INCOMPLETE.value
        assert envelope.availability != ViewState.LIVE.value

        providers = {r["provider"]: r for r in _rows(envelope, "tracking_providers")}
        assert providers["ga4"]["state"] == "not_configured"
        assert providers["ga4"]["configured"] is False
        assert not any(r["verified"] for r in providers.values()), (
            "No server-side check can establish that a browser tag loads, fires "
            "on the right events, or survives an ad blocker. `verified` must "
            "stay False for every provider on this screen."
        )

        assert providers["ga4_data_api"]["state"] == "not_configured", (
            "The GA4 *reader* is listed separately from the GA4 tag and is "
            "permanently unconfigured: there is no Data API client in this "
            "codebase, so a store with a perfectly healthy tag still cannot "
            "check a single figure against GA4."
        )


def test_tracking_health_reports_suppressed_consent_as_its_own_count():
    """`SUPPRESSED_NO_CONSENT` is a correct outcome, not a delivery failure.

    Folding it into "not delivered" either raises a false alarm for every
    consent-declining customer or hides a real outage behind a plausible
    number — and on a bare count those two look identical.
    """
    with sandbox() as (db, _generation):
        envelope = _resolve(db, TRACKING_HEALTH[2])
        delivery = {r["status"]: r for r in _rows(envelope, "event_delivery")}

        assert set(delivery) == {
            OutboxStatus.PENDING,
            OutboxStatus.DELIVERED,
            OutboxStatus.FAILED,
            OutboxStatus.SUPPRESSED_NO_CONSENT,
        }
        suppressed = delivery[OutboxStatus.SUPPRESSED_NO_CONSENT]
        assert suppressed["events"] >= SUPPRESSED_EVENTS
        assert suppressed["is_delivery_loss"] is False, (
            "A suppressed event was deliberately never sent. Counting a respected "
            "consent decision as attribution loss makes a privacy-compliant store "
            "look broken."
        )
        assert delivery[OutboxStatus.FAILED]["is_delivery_loss"] is True
        assert delivery[OutboxStatus.DELIVERED]["events"] >= DELIVERED_EVENTS

        consent = {r["item"]: r["value"] for r in _rows(envelope, "tracking_summary")}
        assert "consent_mode" in consent


def test_a_never_built_rollup_sorts_as_the_worst_state():
    """`lag_days = None` must never sort or render as the freshest.

    Driven through `_freshness_scan` with a stubbed repository rather than the
    live database, because the assertion is about ORDER and the live rollup
    population changes as the aggregation jobs run. A test that only passes
    while some rollup happens to be empty is a test that will one day pass for
    the wrong reason and then stop.
    """
    view = registry.get_view(*TRACKING_HEALTH[1:])
    assert view is not None
    filters = _filters()
    window = filters.resolve(BUCKET + timedelta(days=1))
    last_requested = BUCKET

    watermarks = {
        # never built: no watermark and no rows.
        "agg_order_daily": (None, 0),
        # built once and abandoned: a fortnight behind.
        "agg_order_hourly": (last_requested - timedelta(days=14), 500),
        # current.
        "agg_product_daily": (last_requested, 900),
    }

    class _StubRepo:
        def source_watermark(self, source: str, tz_generation: int):
            return watermarks.get(source, (last_requested, 10))

    ctx = ResolverContext(
        db=None,  # `_freshness_scan` reads the repository, never the session.
        repo=_StubRepo(),
        view=view,
        filters=filters,
        window=window,
        tz_generation=1,
        today=BUCKET,
    )
    rows, _refs, never_built, stale = _freshness_scan(ctx)

    assert never_built == ["agg_order_daily"]
    assert "agg_order_hourly" in stale

    assert rows[0]["source"] == "agg_order_daily", (
        "A rollup that has never been built is the WORST state a source can be "
        "in, not the freshest. Sorting the table by lag would put its null at "
        f"one end or the other by accident. Got order: {[r['source'] for r in rows]}"
    )
    assert rows[0]["status"] == "never_built"
    assert rows[0]["lag_days"] is None, (
        "A never-built rollup is not '0 days behind' — that is the best possible "
        "value and the exact opposite of the truth."
    )
    assert rows[0]["staleness_rank"] == NEVER_BUILT_RANK

    ranks = [r["staleness_rank"] for r in rows]
    assert ranks == sorted(ranks, reverse=True), (
        f"Rows must arrive worst-first: {list(zip(ranks, [r['source'] for r in rows]))}"
    )
    assert all(r["staleness_rank"] is not None for r in rows), (
        "`staleness_rank` is the sortable companion to a deliberately null "
        "`lag_days`; a null here would put the problem straight back."
    )
    stale_row = next(r for r in rows if r["source"] == "agg_order_hourly")
    assert stale_row["lag_days"] == 14
    assert stale_row["staleness_rank"] < NEVER_BUILT_RANK


def test_the_event_volume_chart_is_left_empty_rather_than_zeroed():
    """No client-side stream is connected, so there is no event series at all.

    `{"event_volume": []}` would let the chart draw an axis and read as "zero
    events received", which is a much stronger claim than "we cannot see events".
    """
    with sandbox() as (db, _generation):
        envelope = _resolve(db, TRACKING_HEALTH[2])
        assert "event_volume" not in envelope.series
        assert NOT_CONFIGURED in _codes(envelope)


# ---------------------------------------------------------------------------
# 4. View 73: skipped is not clear, and severity is the sort key
# ---------------------------------------------------------------------------


def test_rule_coverage_tells_skipped_apart_from_clear():
    """Both render as an absent alert. Only one means the store was checked.

    `ga4_sync_failure` has 11 non-suppressed outbox events on the bucket and no
    failures, so it is evaluated and comes back CLEAR. `conversion_drop` reads
    `agg_funnel_daily`, which holds nothing in 1996, so it is SKIPPED with a
    machine-readable reason. A screen that showed only "no alert" for both would
    make a detector that never ran indistinguishable from a healthy store.
    """
    with sandbox() as (db, _generation):
        envelope = _resolve(db, ALERTS[2])
        coverage = {r["rule"]: r for r in _rows(envelope, "rule_coverage")}

        assert set(coverage) == set(RULES), (
            "Every declared rule must appear, including the ones that produced "
            f"nothing. Missing: {sorted(set(RULES) - set(coverage))}"
        )

        cleared = coverage[AlertRuleKey.GA4_SYNC_FAILURE]
        skipped = coverage[AlertRuleKey.CONVERSION_DROP]

        assert cleared["state"] == RULE_CLEAR, (
            f"{DELIVERED_EVENTS} delivered outbox events and no failures on the "
            "bucket is an evaluated, in-range verdict. Got "
            f"{cleared['state']!r} ({cleared['skip_reason']!r}: "
            f"{cleared['skip_message']!r})"
        )
        assert skipped["state"] == RULE_SKIPPED
        assert cleared["state"] != skipped["state"], (
            "A skipped rule and a clear rule must be distinguishable on the row, "
            "not only in a log."
        )

        assert skipped["skip_reason"] == SkipReason.NO_SOURCE_ROWS, (
            "agg_funnel_daily has never been built for this window, so the rule "
            "never ran. That is a pipeline state, not a business one, and the "
            f"row must say which. Got {skipped['skip_reason']!r}."
        )
        assert skipped["skip_message"]
        assert skipped["expected_low"] is None and skipped["expected_high"] is None, (
            "A skipped rule was judged against nothing, so it has no expected "
            "range. Printing one would imply a comparison was made."
        )

        assert cleared["skip_reason"] is None
        assert cleared["expected_high"] is not None, (
            "A cleared rule must carry the band it was actually judged against — "
            "that band is the whole difference between 'checked' and 'silent'."
        )
        assert cleared["actual"] is not None

        skipped_rules = [r for r in coverage.values() if r["state"] == RULE_SKIPPED]
        assert skipped_rules, "the fixture is supposed to produce skips"
        for row in skipped_rules:
            assert row["skip_reason"], (
                f"Rule {row['rule']} is skipped with no reason. 'Why is the alert "
                "list empty?' is the question this table exists to answer."
            )


def test_the_alert_feed_orders_by_severity():
    """Critical first. `AlertSeverity`'s own values sort alphabetically as
    critical < info < warning, which would put INFO above WARNING on screen."""
    with sandbox() as (db, _generation):
        envelope = _resolve(db, ALERTS[2])
        rows = _rows(envelope, "alert_feed")

        assert len(rows) == 3, f"three seeded alerts, got {len(rows)}"
        ranks = [r["severity_rank"] for r in rows]
        assert ranks == sorted(ranks), (
            "Rows must arrive worst-first: "
            f"{[(r['severity'], r['status']) for r in rows]}"
        )
        assert [r["severity"] for r in rows] == [
            AlertSeverity.CRITICAL,
            AlertSeverity.WARNING,
            AlertSeverity.INFO,
        ]
        assert ranks[0] == SEVERITY_RANK[AlertSeverity.CRITICAL]

        summary = {r["severity"]: r for r in _rows(envelope, "alert_summary")}
        assert summary[AlertSeverity.CRITICAL]["open"] == 1
        assert summary[AlertSeverity.WARNING]["acknowledged"] == 1
        assert summary[AlertSeverity.INFO]["resolved"] == 1
        assert summary[AlertSeverity.INFO]["open"] == 0, (
            "A severity whose alerts are all resolved and a severity that never "
            "fired both count zero open. The other columns are what tell them "
            "apart."
        )


def test_the_alert_feed_carries_the_band_and_the_lifecycle():
    """An alert that records only "sales dropped" is not actionable."""
    with sandbox() as (db, _generation):
        envelope = _resolve(db, ALERTS[2])
        by_status = {r["status"]: r for r in _rows(envelope, "alert_feed")}

        critical = by_status[AlertStatus.OPEN]
        assert critical["observed"] == Decimal("410.0000")
        assert critical["expected_low"] == Decimal("900.0000")
        assert critical["expected_high"] == Decimal("960.0000")
        assert critical["expected"] == Decimal("900.0000"), (
            "The single `expected` column carries the edge that was actually "
            "breached — 410 came in under the low bound, so the number the "
            "reader wants is the bound it should not have gone past."
        )
        assert critical["acknowledged_at"] is None
        assert critical["resolution_note"] is None

        acknowledged = by_status[AlertStatus.ACKNOWLEDGED]
        assert acknowledged["acknowledged_at"] is not None
        assert acknowledged["acknowledged_by_user_id"] == 99

        resolved = by_status[AlertStatus.RESOLVED]
        assert resolved["resolution_note"], (
            "A resolved alert with no note is indistinguishable from a dismissed "
            "one, and the next person to see the rule fire starts from nothing."
        )


def test_the_alerts_trend_charts_only_days_that_produced_an_alert():
    """A day with no bar may be a clean day or a day nobody evaluated.

    `analytics_alerts` alone cannot tell those apart, so zero-filling the window
    would pick the flattering reading and draw it as a measurement.
    """
    with sandbox() as (db, _generation):
        envelope = _resolve(db, ALERTS[2])
        points = envelope.series.get("alerts_trend") or []
        assert len(points) == 3, (
            "Three alerts on three distinct buckets — and forty days in the "
            f"window. Got {len(points)} points."
        )
        assert sum(p["alerts"] for p in points) == 3
        assert sum(p["critical"] for p in points) == 1


def test_opening_the_alerts_view_writes_no_alerts():
    """The view re-runs the detector to get the skip/clear verdict. It must not
    leave a trace: an alert list that grows because somebody looked at it is
    dated to whenever they happened to look, and nobody can reason about it."""
    with sandbox() as (db, _generation):
        before = db.execute(select(func.count()).select_from(AnalyticsAlert)).scalar_one()
        ids_before = set(
            db.execute(select(AnalyticsAlert.id)).scalars().all()
        )

        envelope = _resolve(db, ALERTS[2])
        assert _rows(envelope, "rule_coverage"), "the detector must have run"

        db.expire_all()
        after = db.execute(select(func.count()).select_from(AnalyticsAlert)).scalar_one()
        ids_after = set(db.execute(select(AnalyticsAlert.id)).scalars().all())

        assert after == before, (
            f"Resolving view 73 wrote {after - before} alert row(s). The "
            "aggregation worker owns that write; this page is a reader."
        )
        assert ids_after == ids_before


def test_opening_the_reconciliation_view_writes_no_alerts():
    """`run_reconciliation` writes one alert per material variance by default.

    View 63 passes `write_alerts=False` for the same reason view 73 rolls its
    detector back: a finding dated to a page view is a finding nobody can
    triage.
    """
    with sandbox() as (db, _generation):
        before = db.execute(select(func.count()).select_from(AnalyticsAlert)).scalar_one()
        _resolve(db, RECONCILIATION[2])
        db.expire_all()
        after = db.execute(select(func.count()).select_from(AnalyticsAlert)).scalar_one()
        assert after == before


# ---------------------------------------------------------------------------
# 5. The generated frontend contract must carry the new wiring
# ---------------------------------------------------------------------------


def test_frontend_contract_carries_the_control_centre_bindings():
    """`params` survives the dump, so React dispatches on the same wiring.

    The committed JSON is regenerated, never hand-edited:
    `python backend/scripts/dump_analytics_registry.py`.
    """
    import scripts.dump_analytics_registry as dump

    payload = json.loads(dump.render_contract())
    by_number = {v["number"]: v for m in payload["modules"] for v in m["views"]}
    for number, module_slug, view_slug in CONTROL_VIEWS:
        view = registry.get_view(module_slug, view_slug)
        assert view is not None
        assert by_number[number]["params"] == view.params, (
            f"View {number} ({view_slug}) params did not survive the dump."
        )
        assert by_number[number]["resolver"] == ResolverId.CUSTOM.value
