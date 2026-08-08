"""Tests for the GA4 Data API read path: the client, the ingest job, the table.

Nothing here talks to Google. Every response is scripted through an
``httpx.MockTransport``, and the service-account key is generated in-process at
import — there is no credential in this repository and this suite must never
need one.

What actually has to be true here
---------------------------------
Most of what could go wrong in this subsystem is *invisible*. A GA4 integration
that half-works renders a plausible chart, and there is nothing on the screen to
compare it against. So the tests carrying the weight are the ones that pin down
the distinctions a plausible-looking implementation collapses:

  * ``test_missing_credentials_is_not_configured_not_an_empty_success`` — the
    single most important assertion in the file. An unconfigured integration and
    a genuinely quiet day both produce zero rows. If the client cannot tell them
    apart, every traffic view renders "0 sessions" forever and nothing ever
    raises. The test asserts the *distinction*, not merely the emptiness.

  * ``test_a_suppressed_metric_is_never_stored_as_zero`` — GA4 sends ``""`` for
    a value it withheld. ``int(value or 0)`` is the obvious parse and it turns a
    privacy suppression into a measurement of zero. The row must be skipped, and
    the skip must be reported.

  * ``test_persistent_5xx_fails_loudly_and_the_run_records_it`` and
    ``test_an_outage_never_deletes_an_already_ingested_day`` — this is a
    delete-and-reinsert rollup reading a third party. A 503 that reaches the
    DELETE empties a real day and inserts nothing, and the chart shows an honest
    -looking zero. Both the raise and the untouched rows are asserted.

  * ``test_provisional_reingest_restates_rather_than_doubling`` — GA4 revises a
    day for 48 hours, so this table is re-pulled nightly. An
    ``ON DUPLICATE KEY UPDATE col = col + VALUES(col)`` passes every other test
    in this file and fails this one.

  * ``test_no_credential_material_appears_in_any_log_record`` — the Measurement
    Protocol client leaked a live ``api_secret`` into the application log via
    httpx's INFO ``HTTP Request: <full url>`` line, on the success path, with no
    error anywhere. This suite drives a full token exchange plus a report with
    httpx logging on at DEBUG and asserts the private key, its id, the signed
    assertion and the bearer token appear in none of the emitted records.

  * ``test_property_timezone_mismatch_is_recorded_on_every_row`` — GA4 buckets by
    the *property's* timezone. Filing a New York day under an IST ``bucket_date``
    without saying so is the ``tz_generation`` defect arriving through a side
    door, and it is unrecoverable once written.

  * ``test_migration_and_twin_sql_agree`` — the analytics schema ships as two
    artifacts (an Alembic revision for CI, hand-applied SQL for the shared
    production MySQL on a lineage this repo does not contain). If they drift,
    production 500s while CI stays green.

Isolation strategy
------------------
Every bucket lives in **2014**, a window this store has never traded in, and each
test additionally scopes its rows by a **unique channel-group dimension** minted
from a uuid, so a concurrent suite can neither move these numbers nor be counted
by them. ``agg_ga4_daily`` is dimensioned, so a key this test invented cannot
collide with anyone else's.

Two tests deliberately use *today* rather than the sandbox: the provisional-flag
tests, because "is this bucket still provisional" is a question about the real
clock and pinning it to 2014 would assert nothing. They still scope by a unique
dimension and delete what they wrote.

No db fixture exists in ``conftest.py``; each test owns its ``SessionLocal()``
and closes it in ``finally``.
"""
from __future__ import annotations

import io
import json
import logging
import re
import subprocess
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import UniqueConstraint, delete, inspect, select

from app.core.config import settings as env_settings
from app.db.session import SessionLocal
from app.models.analytics_base import DIMENSION_UNKNOWN
from app.models.analytics_control import AnalyticsRecomputeQueue, AnalyticsSyncRun
from app.models.analytics_ga4 import (
    FORBIDDEN_QUALITIES,
    LANDING_PAGE_MAX_CHARS,
    NON_ADDITIVE_COLUMNS,
    AggGa4Daily,
)
from app.services.analytics import ga4_data_api as api
from app.services.analytics import integrations
from app.services.analytics.aggregation import JOBS
from app.services.analytics.aggregation import jobs_ga4
from app.services.analytics.aggregation.jobs_ga4 import Ga4DailyJob, Ga4IngestUnavailable
from app.services.analytics.types import MetricQuality

# ===========================================================================
# Sandbox
# ===========================================================================
#: A date this store has never traded in, and old enough that GA4 would long
#: since have stopped revising it — so `is_provisional` is deterministically
#: False for it and does not depend on when the suite runs.
BUCKET = date(2014, 3, 17)
OTHER_BUCKET = date(2014, 3, 18)

WORKER_ID = "test-ga4-data-api"

#: The `store.timezone` default this codebase falls back to, and what the
#: mismatch tests deliberately disagree with.
STORE_TZ = "Asia/Kolkata"

PROPERTY_ID = "123456789"


def _tag() -> str:
    """A channel-group value no other suite can collide with."""
    return f"zz-test-{uuid.uuid4().hex[:12]}"


# ===========================================================================
# A service-account key, generated here so the repo never holds one
# ===========================================================================
def _make_service_account() -> dict[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    return {
        "type": "service_account",
        "project_id": "wellvia-analytics-test",
        "private_key_id": "kid-" + uuid.uuid4().hex,
        "private_key": pem,
        "client_email": "ga4-reader@wellvia-analytics-test.iam.gserviceaccount.com",
        "token_uri": api.TOKEN_URI,
    }


#: Generated once — RSA keygen is the slowest thing in this file by an order of
#: magnitude and nothing here needs a different key per test.
SERVICE_ACCOUNT = _make_service_account()
SERVICE_ACCOUNT_JSON = json.dumps(SERVICE_ACCOUNT)

ACCESS_TOKEN = "ya29." + uuid.uuid4().hex * 2


def _config(property_id: str = PROPERTY_ID) -> api.Ga4DataApiConfig:
    return api.Ga4DataApiConfig(
        property_id=property_id, credentials=dict(SERVICE_ACCOUNT)
    )


# ===========================================================================
# The scripted GA4
# ===========================================================================
def _report_payload(
    rows: list[tuple[list[str], list[str]]],
    *,
    time_zone: str = STORE_TZ,
    sampling: list[tuple[int, int]] | None = None,
    thresholded: bool = False,
    data_loss: bool = False,
    empty_reason: str = "",
    row_count: int | None = None,
) -> dict:
    """A `runReport` body in GA4's own shape. Metric values stay STRINGS."""
    metadata: dict = {
        "currencyCode": "INR",
        "timeZone": time_zone,
        "dataLossFromOtherRow": data_loss,
        "subjectToThresholding": thresholded,
    }
    if sampling:
        metadata["samplingMetadatas"] = [
            {"samplesReadCount": str(read), "samplingSpaceSize": str(space)}
            for read, space in sampling
        ]
    if empty_reason:
        metadata["emptyReason"] = empty_reason
    return {
        "dimensionHeaders": [{"name": name} for name in jobs_ga4.DIMENSIONS],
        "metricHeaders": [
            {"name": name, "type": "TYPE_INTEGER"} for name in jobs_ga4.METRICS
        ],
        "rows": [
            {
                "dimensionValues": [{"value": v} for v in dims],
                "metricValues": [{"value": v} for v in mets],
            }
            for dims, mets in rows
        ],
        "rowCount": len(rows) if row_count is None else row_count,
        "metadata": metadata,
        "kind": "analyticsData#runReport",
    }


class ScriptedGa4:
    """Token endpoint plus a queue of `runReport` answers.

    Responses are *factories* rather than `httpx.Response` objects because a
    Response's stream is consumed on read, so replaying one across a retry would
    silently hand back an empty body — which would make the retry tests pass for
    the wrong reason.
    """

    def __init__(self, *responses):
        self._responses = list(responses)
        self.token_calls = 0
        self.report_calls = 0
        self.report_bodies: list[dict] = []
        self.slept: list[float] = []

    # -- seams --------------------------------------------------------------
    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    # -- handler ------------------------------------------------------------
    def _handle(self, request: httpx.Request) -> httpx.Response:
        if "oauth2.googleapis.com" in str(request.url):
            self.token_calls += 1
            return httpx.Response(
                200,
                json={
                    "access_token": ACCESS_TOKEN,
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        self.report_calls += 1
        self.report_bodies.append(json.loads(request.content.decode("utf-8")))
        factory = (
            self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        )
        return factory()


def _ok(payload: dict):
    return lambda: httpx.Response(200, json=payload)


def _status(code: int, *, body: str = "upstream said no", headers: dict | None = None):
    return lambda: httpx.Response(code, text=body, headers=headers or {})


# ===========================================================================
# DB helpers — no conftest fixture; each test owns its session.
# ===========================================================================
#: `system_settings` holds ONE row per key for the whole database, and this
#: sandbox is shared with every other suite running at the same time. One of them
#: — `test_analytics_integrations.test_service_account_json_is_redacted_too` —
#: deliberately writes a FAKE service-account key ("LEAKME-abc123", not a
#: parseable PEM) into the very row this module reads, and does not restore it.
#:
#: A rollup row can be scoped by a uuid dimension; a settings row cannot. So the
#: settings-backed tests write, read back, and retry if somebody moved it under
#: them. Everything else injects its configuration directly, which is both
#: deterministic and a truer unit boundary — the job's behaviour is a function of
#: the config it was handed, not of how the config was stored.
_SETTINGS_ATTEMPTS = 4


def _write_settings(db, updates: dict[str, str]) -> None:
    """Save through the REAL admin write path.

    Deliberately `integrations.apply_updates` rather than a direct row write:
    that function owns the validation, the Fernet encryption, the settings-cache
    invalidation and the audit row, and "the admin saved it and the worker still
    says not configured" is the exact bug this module's key constants exist to
    prevent. Writing the row by hand would test around it.
    """
    integrations.ensure_rows(db)
    integrations.apply_updates(db, updates, actor=None, actor_ip=None)


def _settled_config(db, updates: dict[str, str], accept) -> api.Ga4DataApiConfig:
    """Write `updates`, read the config back, and retry a concurrent clobber.

    `accept(config)` says whether the config is the one this write should have
    produced. A peer suite overwriting the shared row between the write and the
    read is the only thing that makes it false, and it is a millisecond window,
    so a bounded retry turns a genuine race into a deterministic assertion
    without hiding a real failure — after `_SETTINGS_ATTEMPTS` it gives up and
    the test fails with the config it actually saw.
    """
    config = api.load_data_api_config(db)
    for _ in range(_SETTINGS_ATTEMPTS):
        _write_settings(db, dict(updates))
        config = api.load_data_api_config(db)
        if accept(config):
            return config
    return config


def _configure_credentials(db, *, property_id: str = PROPERTY_ID) -> api.Ga4DataApiConfig:
    return _settled_config(
        db,
        {
            api.SETTING_PROPERTY_ID: property_id,
            api.SETTING_CREDENTIALS: SERVICE_ACCOUNT_JSON,
        },
        lambda c: c.configured
        and c.credentials.get("private_key") == SERVICE_ACCOUNT["private_key"],
    )


def _clear_credentials(db) -> api.Ga4DataApiConfig:
    return _settled_config(
        db,
        {api.SETTING_PROPERTY_ID: "", api.SETTING_CREDENTIALS: ""},
        lambda c: c.reason == "no_property_id",
    )


def _cleanup(tags: list[str], buckets: list[date]) -> None:
    """Delete only what this test owned, through a fresh session."""
    db = SessionLocal()
    try:
        if tags:
            db.execute(delete(AggGa4Daily).where(AggGa4Daily.channel_group.in_(tags)))
        if buckets:
            db.execute(
                delete(AnalyticsRecomputeQueue).where(
                    AnalyticsRecomputeQueue.job == jobs_ga4.JOB_NAME,
                    AnalyticsRecomputeQueue.bucket_date.in_(buckets),
                )
            )
        db.execute(
            delete(AnalyticsSyncRun).where(AnalyticsSyncRun.worker_id == WORKER_ID)
        )
        db.commit()
    finally:
        db.close()


def _rows_for(db, tag: str) -> list[AggGa4Daily]:
    return list(
        db.execute(
            select(AggGa4Daily)
            .where(AggGa4Daily.channel_group == tag)
            .order_by(AggGa4Daily.landing_page)
        )
        .scalars()
        .all()
    )


@pytest.fixture()
def rollups_on(monkeypatch):
    """Turn the rollup subsystem on for one test, and reset the token cache.

    The token cache is module-global and keyed by the service account, so a test
    that scripts a 401 would otherwise be served a token minted by the test
    before it and never reach the token endpoint at all.
    """
    monkeypatch.setattr(env_settings, "ANALYTICS_ROLLUPS_ENABLED", True)
    api.reset_token_cache()
    yield
    api.reset_token_cache()


@pytest.fixture()
def ga4_configured(rollups_on, monkeypatch):
    """Hand the job a known-good configuration, bypassing the shared row.

    The settings round trip is proved once, on purpose, by
    `test_credentials_saved_by_the_admin_are_read_back_by_the_worker`. Every
    other job test cares about what the job does with a configuration, not about
    where the configuration came from, and reading the globally-shared
    `system_settings` row for that would make each of them fail whenever a
    concurrent suite happens to be writing its own fake key into it.
    """
    monkeypatch.setattr(
        jobs_ga4, "load_data_api_config", lambda db, **kw: _config()
    )
    return _config()


@pytest.fixture()
def ga4_not_configured(rollups_on, monkeypatch):
    """The same seam, holding the 'nobody has connected GA4' state."""
    unconfigured = api.Ga4DataApiConfig(
        reason="no_credentials", detail="No GA4 Data API service-account JSON."
    )
    monkeypatch.setattr(
        jobs_ga4, "load_data_api_config", lambda db, **kw: unconfigured
    )
    return unconfigured


def _dims(tag: str, landing: str = "/", *, device: str = "mobile") -> list[str]:
    """Dimension values in `DIMENSIONS` order: date, channel, source/medium,
    device, landing page."""
    return ["20140317", tag, "google / organic", device, landing]


# ===========================================================================
# 1. Settings keys, verified against the schema that writes them
# ===========================================================================
def test_settings_keys_match_the_module_that_writes_them():
    """The dotted-vs-underscore bug, made impossible to reintroduce silently.

    `analytics/integrations.py` owns the field schema, the validation, the Fernet
    encryption and the admin form. A key this client reads that is not in
    `FIELD_BY_KEY` is a key nothing will ever write, and the failure is invisible:
    the loader returns "not configured", the job no-ops, the run log stays green
    and every traffic view stays dark. That is precisely what happened to the
    Measurement Protocol client before it was reconciled.
    """
    api.assert_settings_keys_are_canonical()

    assert api.SETTING_PROPERTY_ID == "analytics.ga4_property_id"
    assert api.SETTING_CREDENTIALS == "analytics.ga4_data_api_credentials"
    assert set(api.GA4_DATA_API_SETTING_KEYS) <= set(integrations.FIELD_BY_KEY)


def test_the_service_account_never_reaches_a_browser():
    """A Data API key can read the whole property. It must never be public."""
    assert api.SETTING_CREDENTIALS in integrations.SECRET_KEYS
    assert api.SETTING_CREDENTIALS not in integrations.PUBLIC_KEYS
    assert api.SETTING_PROPERTY_ID not in integrations.PUBLIC_KEYS

    from app.api.v1.endpoints import settings as settings_endpoint

    public = getattr(settings_endpoint, "_PUBLIC_KEYS", set())
    assert api.SETTING_CREDENTIALS not in public, (
        "the GA4 service-account JSON is on the anonymous /settings/public "
        "allowlist — that endpoint needs no authentication and this credential "
        "grants read access to the whole property"
    )


def test_config_object_does_not_render_its_credentials():
    """`repr` is where secrets escape: an f-string in a log line, an assertion
    diff, a traceback frame. The private key must not be in any of them."""
    rendered = repr(_config())
    assert SERVICE_ACCOUNT["private_key"] not in rendered
    assert "BEGIN PRIVATE KEY" not in rendered
    assert PROPERTY_ID in rendered, "the property id is not a secret and is useful"


# ===========================================================================
# 2. Not configured is NOT an empty success
# ===========================================================================
def test_missing_credentials_is_not_configured_not_an_empty_success(rollups_on):
    """The distinction the whole module exists to preserve.

    Both of these produce zero rows. Exactly one of them means "nobody visited".
    If a caller cannot tell them apart, a store whose credential expired shows a
    flat zero line that is indistinguishable from a quiet fortnight, and nothing
    anywhere raises.
    """
    db = SessionLocal()
    try:
        config = _clear_credentials(db)
        assert not config.configured
        assert config.reason == "no_property_id"
        assert config.detail, "a machine token without prose helps nobody"

        missing = api.run_report(
            config,
            api.ReportRequest(
                date_from=BUCKET, date_to=BUCKET, metrics=("sessions",)
            ),
        )
    finally:
        db.close()

    scripted = ScriptedGa4(_ok(_report_payload([])))
    genuinely_empty = api.run_report(
        _config(),
        api.ReportRequest(date_from=BUCKET, date_to=BUCKET, metrics=("sessions",)),
        transport=scripted.transport,
        sleep=scripted.sleep,
    )

    # Same rows. Different status. That is the entire point.
    assert missing.rows == () and genuinely_empty.rows == ()

    assert missing.status is api.ReportStatus.NOT_CONFIGURED
    assert missing.ok is False
    assert missing.configured is False

    assert genuinely_empty.status is api.ReportStatus.OK
    assert genuinely_empty.ok is True
    assert genuinely_empty.configured is True

    # And nothing was attempted for the unconfigured one.
    assert scripted.report_calls == 1


def test_an_unavailable_report_is_configured_but_not_ok(rollups_on):
    """Three states, not two. `UNAVAILABLE` needs an alert; `NOT_CONFIGURED`
    needs an onboarding prompt. Collapsing them sends the wrong one."""
    scripted = ScriptedGa4(_status(503))
    report = api.run_report(
        _config(),
        api.ReportRequest(date_from=BUCKET, date_to=BUCKET, metrics=("sessions",)),
        transport=scripted.transport,
        sleep=scripted.sleep,
    )
    assert report.status is api.ReportStatus.UNAVAILABLE
    assert report.ok is False
    assert report.configured is True


def test_a_failed_report_cannot_be_constructed_with_rows():
    """Structural, not conventional: a partial result presented as data is how a
    failed integration renders as a real zero."""
    with pytest.raises(ValueError):
        api.Ga4Report(
            status=api.ReportStatus.UNAVAILABLE,
            rows=(api.Ga4Row(dimensions=("x",), metrics=(1,)),),
        )
    with pytest.raises(ValueError):
        api.Ga4Report(status=api.ReportStatus.OK, reason="http_500")


def test_rollups_disabled_is_its_own_reason(monkeypatch):
    """The kill switch must not read credentials it has been told not to use,
    and must not look like a missing configuration."""
    monkeypatch.setattr(env_settings, "ANALYTICS_ROLLUPS_ENABLED", False)
    db = SessionLocal()
    try:
        config = api.load_data_api_config(db)
        assert config.reason == "rollups_disabled"
        assert config.property_id == ""

        # ...but an admin checking their setup can still resolve credentials,
        # which is exactly the moment somebody needs to.
        override = api.load_data_api_config(db, ignore_feature_flag=True)
        assert override.reason != "rollups_disabled"
    finally:
        db.close()


def test_a_measurement_id_in_the_property_id_field_is_rejected(rollups_on):
    """G-XXXXXXXXXX here produces a 404 on every nightly run, which presents as
    'GA4 is down' rather than 'that is the wrong id'."""
    db = SessionLocal()
    try:
        _configure_credentials(db, property_id="123456789")
        integrations.apply_updates(
            db, {api.SETTING_PROPERTY_ID: ""}, actor=None, actor_ip=None
        )
        # The admin field's own pattern refuses a measurement id, so the
        # invalid-value path is exercised at the loader with a raw row write —
        # the state a legacy row or a hand-run UPDATE could leave behind.
        from app.models.system_setting import SystemSetting

        row = db.execute(
            select(SystemSetting).where(SystemSetting.key == api.SETTING_PROPERTY_ID)
        ).scalar_one()
        row.value = "G-ABC12345"
        db.commit()
        # A direct row write invalidates nobody's cache — the exact hazard
        # `conftest._drop_stale_settings_cache` documents. Drop the one key.
        from app.db.redis import get_redis
        from app.services.settings_service import _cache_key

        get_redis().delete(_cache_key(api.SETTING_PROPERTY_ID))

        config = api.load_data_api_config(db)
        assert config.reason == "invalid_property_id"
    finally:
        _clear_credentials(db)
        db.close()


# ===========================================================================
# 3. Retry and backoff
# ===========================================================================
def test_429_retries_with_backoff_and_eventually_succeeds(rollups_on):
    payload = _report_payload([(_dims("chan"), ["5", "3", "4", "2", "9", "300"])])
    scripted = ScriptedGa4(
        _status(429, body="rate limited"),
        _status(429, body="rate limited"),
        _ok(payload),
    )
    report = api.run_report(
        _config(),
        api.ReportRequest(
            date_from=BUCKET,
            date_to=BUCKET,
            dimensions=jobs_ga4.DIMENSIONS,
            metrics=jobs_ga4.METRICS,
        ),
        transport=scripted.transport,
        sleep=scripted.sleep,
    )

    assert report.ok
    assert len(report.rows) == 1
    assert scripted.report_calls == 3
    assert len(scripted.slept) == 2, "a retry that does not back off is a hammer"
    assert all(0.0 <= s <= api.BACKOFF_MAX_SECONDS for s in scripted.slept)


def test_a_bounded_retry_after_is_honoured(rollups_on):
    scripted = ScriptedGa4(
        _status(429, headers={"Retry-After": "2"}),
        _ok(_report_payload([])),
    )
    report = api.run_report(
        _config(),
        api.ReportRequest(date_from=BUCKET, date_to=BUCKET, metrics=("sessions",)),
        transport=scripted.transport,
        sleep=scripted.sleep,
    )
    assert report.ok
    assert scripted.slept == [2.0]


def test_an_absurd_retry_after_is_ignored(rollups_on):
    """Google answers a quota exhaustion with a multi-hour hint. Sleeping on it
    inside an aggregation bucket would hold a transaction open all night."""
    scripted = ScriptedGa4(_status(429, headers={"Retry-After": "7200"}))
    api.run_report(
        _config(),
        api.ReportRequest(date_from=BUCKET, date_to=BUCKET, metrics=("sessions",)),
        transport=scripted.transport,
        sleep=scripted.sleep,
    )
    assert all(s <= api.BACKOFF_MAX_SECONDS for s in scripted.slept)


def test_persistent_5xx_gives_up_loudly(rollups_on):
    scripted = ScriptedGa4(_status(503, body="backend error"))
    report = api.run_report(
        _config(),
        api.ReportRequest(date_from=BUCKET, date_to=BUCKET, metrics=("sessions",)),
        transport=scripted.transport,
        sleep=scripted.sleep,
    )
    assert report.status is api.ReportStatus.UNAVAILABLE
    assert report.reason == "http_503"
    assert "503" in report.detail
    assert str(api.MAX_ATTEMPTS) in report.detail
    assert scripted.report_calls == api.MAX_ATTEMPTS


def test_a_4xx_is_not_retried(rollups_on):
    """A malformed request will be exactly as malformed next time. Retrying it
    burns the budget and delays the alert naming the bad dimension."""
    scripted = ScriptedGa4(_status(400, body="unknown dimension"))
    report = api.run_report(
        _config(),
        api.ReportRequest(date_from=BUCKET, date_to=BUCKET, metrics=("sessions",)),
        transport=scripted.transport,
        sleep=scripted.sleep,
    )
    assert report.reason == "http_400"
    assert scripted.report_calls == 1
    assert scripted.slept == []


def test_a_403_names_the_service_account_and_drops_the_token(rollups_on):
    """The remedy for a 403 is specific and nobody guesses it: the key is fine,
    it has not been granted Viewer on the property."""
    scripted = ScriptedGa4(_status(403, body="permission denied"))
    report = api.run_report(
        _config(),
        api.ReportRequest(date_from=BUCKET, date_to=BUCKET, metrics=("sessions",)),
        transport=scripted.transport,
        sleep=scripted.sleep,
    )
    assert report.reason == "http_403"
    assert SERVICE_ACCOUNT["client_email"] in report.detail
    assert "Property Access Management" in report.detail
    assert scripted.report_calls == 1


def test_the_request_builder_refuses_what_ga4_would_reject():
    with pytest.raises(api.Ga4DataApiContractError):
        api.ReportRequest(date_from=BUCKET, date_to=BUCKET, metrics=())
    with pytest.raises(api.Ga4DataApiContractError):
        api.ReportRequest(
            date_from=OTHER_BUCKET, date_to=BUCKET, metrics=("sessions",)
        )
    with pytest.raises(api.Ga4DataApiContractError):
        api.ReportRequest(
            date_from=BUCKET, date_to=BUCKET, metrics=("sessions; DROP",)
        )
    with pytest.raises(api.Ga4DataApiContractError):
        api.ReportRequest(
            date_from=BUCKET, date_to=BUCKET, metrics=("sessions",), limit=0
        )
    with pytest.raises(api.Ga4DataApiContractError):
        api.ReportRequest(
            date_from=BUCKET,
            date_to=BUCKET,
            metrics=("sessions",),
            limit=api.MAX_LIMIT + 1,
        )
    with pytest.raises(api.Ga4DataApiContractError):
        api.ReportRequest(
            date_from=BUCKET,
            date_to=BUCKET,
            metrics=tuple(f"m{i}" for i in range(api.MAX_METRICS + 1)),
        )

    body = api.ReportRequest(
        date_from=BUCKET, date_to=OTHER_BUCKET, metrics=("sessions",)
    ).to_body()
    # INCLUSIVE, matching GA4 and deliberately unlike every half-open window in
    # the rest of this stack.
    assert body["dateRanges"] == [
        {"startDate": "2014-03-17", "endDate": "2014-03-18"}
    ]


# ===========================================================================
# 4. Sampling, thresholding and suppression
# ===========================================================================
def test_sampling_and_thresholding_are_detected_and_graded(rollups_on):
    payload = _report_payload(
        [(_dims("chan"), ["5", "3", "4", "2", "9", "300"])],
        sampling=[(1_000, 10_000)],
        thresholded=True,
        data_loss=True,
    )
    scripted = ScriptedGa4(_ok(payload))
    report = api.run_report(
        _config(),
        api.ReportRequest(
            date_from=BUCKET,
            date_to=BUCKET,
            dimensions=jobs_ga4.DIMENSIONS,
            metrics=jobs_ga4.METRICS,
        ),
        transport=scripted.transport,
        sleep=scripted.sleep,
    )
    assert report.is_sampled
    assert report.sampling == ((1_000, 10_000),)
    assert report.subject_to_thresholding
    assert report.data_loss_from_other_row
    # Rows are MISSING, which is worse than merely estimated.
    assert report.quality is MetricQuality.INCOMPLETE


def test_a_full_read_reported_as_sampling_metadata_is_not_a_sample(rollups_on):
    """`samplesReadCount == samplingSpaceSize` is a full read. Reporting it as a
    sample would put an ESTIMATED badge on every figure GA4 ever returns."""
    scripted = ScriptedGa4(
        _ok(_report_payload([], sampling=[(10_000, 10_000)]))
    )
    report = api.run_report(
        _config(),
        api.ReportRequest(date_from=BUCKET, date_to=BUCKET, metrics=("sessions",)),
        transport=scripted.transport,
        sleep=scripted.sleep,
    )
    assert report.is_sampled is False
    assert report.quality is MetricQuality.ACTUAL


def test_a_withheld_metric_parses_to_none_and_never_to_zero():
    """`int(value or 0)` is the obvious parse, and it converts a privacy
    suppression into a measurement of zero."""
    assert api._parse_metric("") is None
    assert api._parse_metric(None) is None
    assert api._parse_metric("0") == 0, "a real zero must survive"
    assert api._parse_metric("1234") == 1234
    # Duration metrics carry a fractional part; Decimal, never float.
    assert api._parse_metric("1234.9") == 1234

    row = api.Ga4Row(dimensions=("a",), metrics=(5, None))
    assert row.has_suppressed_metric
    assert api.Ga4Row(dimensions=("a",), metrics=(5, 0)).has_suppressed_metric is False


def test_gradings_never_reach_authoritative():
    """GA4 measures behaviour with a browser tag. It is not the ledger."""
    for sampled in (True, False):
        for thresholded in (True, False):
            for data_loss in (True, False):
                grade = api.quality_for(
                    sampled=sampled, thresholded=thresholded, data_loss=data_loss
                )
                assert grade is not MetricQuality.AUTHORITATIVE
                assert grade.value not in FORBIDDEN_QUALITIES
    assert api.MAX_QUALITY is MetricQuality.ACTUAL


# ===========================================================================
# 5. The ingest job
# ===========================================================================
def _run_job(scripted: ScriptedGa4, bucket: date = BUCKET, generation: int = 1):
    """Run one bucket. Requires the `ga4_configured` fixture to be active."""
    db = SessionLocal()
    try:
        job = Ga4DailyJob(transport=scripted.transport, sleep=scripted.sleep)
        result = job.run(db, bucket, generation)
        db.commit()
        return result
    finally:
        db.close()


def test_credentials_saved_by_the_admin_are_read_back_by_the_worker(rollups_on):
    """The round trip, proved once and properly.

    This is the bug the key constants exist to prevent, and the only way to
    prove it is absent is to save through the admin path and read through the
    worker path: the Measurement Protocol client wrote dotted keys while the
    admin form wrote underscore ones, so an operator could save a credential,
    see it persisted, and have the worker report "not configured" forever with
    no error anywhere.
    """
    db = SessionLocal()
    try:
        config = _configure_credentials(db)
        assert config.configured, (
            f"the admin path saved credentials and the worker path did not see "
            f"them: reason={config.reason!r}"
        )
        assert config.property_id == PROPERTY_ID
        assert config.client_email == SERVICE_ACCOUNT["client_email"]
        assert config.credentials["private_key"] == SERVICE_ACCOUNT["private_key"]

        # ...and the value on disk is ciphertext, not the key file.
        from app.models.system_setting import SystemSetting

        stored = db.execute(
            select(SystemSetting).where(SystemSetting.key == api.SETTING_CREDENTIALS)
        ).scalar_one().value
        assert stored.startswith("gAAAAA"), "the service-account key is not encrypted"
        assert "BEGIN PRIVATE KEY" not in stored
    finally:
        _clear_credentials(db)
        db.close()


def test_a_successful_report_lands_rows_and_re_running_is_idempotent(ga4_configured):
    tag = _tag()
    payload = _report_payload(
        [
            (_dims(tag, "/"), ["10", "6", "8", "3", "24", "600"]),
            (_dims(tag, "/products/ashwagandha"), ["4", "1", "4", "1", "5", "90"]),
        ]
    )
    try:
        first = _run_job(ScriptedGa4(_ok(payload)))
        assert first.rows_written == 2

        db = SessionLocal()
        try:
            landed = _rows_for(db, tag)
            assert len(landed) == 2
            root = next(r for r in landed if r.landing_page == "/")
            assert root.sessions == 10
            assert root.engaged_sessions == 6
            assert root.total_users == 8
            assert root.new_users == 3
            assert root.screen_page_views == 24
            assert root.sum_engagement_seconds == 600
            assert root.n_engagement == 10, "the denominator must be stored, not the average"
            assert root.source_medium == "google / organic"
            assert root.device_category == "mobile"
            assert root.ga4_date == "20140317"
            assert root.quality == MetricQuality.ACTUAL.value
            assert root.is_provisional is False, (
                "a 2014 bucket cannot still be being revised by GA4"
            )
            before = {(r.landing_page, r.sessions, r.total_users) for r in landed}
        finally:
            db.close()

        # Same bucket, again. An `ON DUPLICATE KEY UPDATE col = col + VALUES(col)`
        # passes every other assertion in this file and fails here.
        second = _run_job(ScriptedGa4(_ok(payload)))
        assert second.rows_written == 2
        assert second.rows_deleted == 2

        db = SessionLocal()
        try:
            landed = _rows_for(db, tag)
            assert len(landed) == 2
            assert {
                (r.landing_page, r.sessions, r.total_users) for r in landed
            } == before
        finally:
            db.close()
    finally:
        _cleanup([tag], [BUCKET])


def test_a_group_key_that_disappeared_is_removed(ga4_configured):
    """Delete-and-reinsert is the only pattern that can express this. An upsert
    leaves the vacated landing page at its old numbers forever."""
    tag = _tag()
    try:
        _run_job(
            ScriptedGa4(
                _ok(
                    _report_payload(
                        [
                            (_dims(tag, "/"), ["10", "6", "8", "3", "24", "600"]),
                            (_dims(tag, "/gone"), ["2", "1", "2", "0", "3", "40"]),
                        ]
                    )
                )
            )
        )
        _run_job(
            ScriptedGa4(
                _ok(
                    _report_payload(
                        [(_dims(tag, "/"), ["10", "6", "8", "3", "24", "600"])]
                    )
                )
            )
        )
        db = SessionLocal()
        try:
            landed = _rows_for(db, tag)
            assert [r.landing_page for r in landed] == ["/"]
        finally:
            db.close()
    finally:
        _cleanup([tag], [BUCKET])


def test_a_suppressed_metric_is_never_stored_as_zero(ga4_configured):
    tag = _tag()
    payload = _report_payload(
        [
            (_dims(tag, "/"), ["10", "6", "8", "3", "24", "600"]),
            # GA4 withheld this row's session count.
            (_dims(tag, "/rare-page"), ["", "", "", "", "", ""]),
        ],
        thresholded=True,
    )
    try:
        result = _run_job(ScriptedGa4(_ok(payload)))
        assert result.rows_written == 1, "the withheld row must not be written"

        db = SessionLocal()
        try:
            landed = _rows_for(db, tag)
            assert [r.landing_page for r in landed] == ["/"]
            assert not any(r.landing_page == "/rare-page" for r in landed)
            # And the surviving row carries the fact that rows are missing.
            assert landed[0].is_thresholded is True
            assert landed[0].quality == MetricQuality.INCOMPLETE.value
        finally:
            db.close()

        joined = " ".join(result.warnings)
        assert "ga4_suppressed_rows" in joined
        assert "ga4_thresholded" in joined
    finally:
        _cleanup([tag], [BUCKET])


def test_sampling_metadata_is_landed_as_counts_not_a_ratio(ga4_configured):
    tag = _tag()
    payload = _report_payload(
        [(_dims(tag, "/"), ["10", "6", "8", "3", "24", "600"])],
        sampling=[(2_500, 10_000)],
    )
    try:
        result = _run_job(ScriptedGa4(_ok(payload)))
        db = SessionLocal()
        try:
            row = _rows_for(db, tag)[0]
            assert row.is_sampled is True
            assert row.samples_read_count == 2_500
            assert row.sampling_space_size == 10_000
            assert row.quality == MetricQuality.ESTIMATED.value
        finally:
            db.close()
        assert any("ga4_sampled" in w for w in result.warnings)

        # A stored ratio could not be re-aggregated. There must not be one.
        columns = set(AggGa4Daily.__table__.columns.keys())
        assert not {c for c in columns if c.endswith(("_pct", "_rate", "_ratio"))}
    finally:
        _cleanup([tag], [BUCKET])


def test_missing_credentials_make_the_job_a_no_op_that_deletes_nothing(
    ga4_configured, monkeypatch
):
    """A credential that expires must not empty the history it already ingested."""
    tag = _tag()
    try:
        _run_job(
            ScriptedGa4(
                _ok(
                    _report_payload(
                        [(_dims(tag, "/"), ["10", "6", "8", "3", "24", "600"])]
                    )
                )
            )
        )

        # The credential goes away — revoked, rotated, or SECRET_KEY changed.
        monkeypatch.setattr(
            jobs_ga4,
            "load_data_api_config",
            lambda db, **kw: api.Ga4DataApiConfig(
                property_id=PROPERTY_ID,
                reason="credentials_undecryptable",
                detail="SECRET_KEY has most likely been rotated.",
            ),
        )

        db = SessionLocal()
        try:
            scripted = ScriptedGa4(_ok(_report_payload([])))
            job = Ga4DailyJob(transport=scripted.transport, sleep=scripted.sleep)
            result = job.run(db, BUCKET, 1)
            db.commit()
        finally:
            db.close()

        assert result.rows_written == 0
        assert result.rows_deleted == 0
        assert scripted.report_calls == 0, "nothing should have been attempted"
        assert any("ga4_not_configured" in w for w in result.warnings)

        db = SessionLocal()
        try:
            assert len(_rows_for(db, tag)) == 1, (
                "an unconfigured GA4 emptied a day of real traffic"
            )
        finally:
            db.close()
    finally:
        _cleanup([tag], [BUCKET])


def test_the_job_is_inert_while_the_rollup_flag_is_off(monkeypatch):
    monkeypatch.setattr(env_settings, "ANALYTICS_ROLLUPS_ENABLED", False)
    scripted = ScriptedGa4(_ok(_report_payload([])))
    db = SessionLocal()
    try:
        job = Ga4DailyJob(transport=scripted.transport, sleep=scripted.sleep)
        result = job.run(db, BUCKET, 1)
    finally:
        db.close()
    assert result.rows_written == 0
    assert scripted.report_calls == 0 and scripted.token_calls == 0
    assert any("ga4_rollups_disabled" in w for w in result.warnings)


def test_an_outage_never_deletes_an_already_ingested_day(ga4_configured, monkeypatch):
    tag = _tag()
    try:
        _run_job(
            ScriptedGa4(
                _ok(
                    _report_payload(
                        [(_dims(tag, "/"), ["10", "6", "8", "3", "24", "600"])]
                    )
                )
            )
        )

        # Two layers, because they fail independently.
        #
        # First, the job on its own: query the SAME session after the raise and
        # before any rollback. `AggregationRunner._execute_bucket` does roll back
        # on an exception, so a DELETE issued before the raise would be
        # invisible through the runner — but `job.run()` is also reachable from a
        # script and from the admin API, where nothing rolls anything back. The
        # guarantee has to hold at the job, so it is asserted at the job.
        scripted = ScriptedGa4(_status(503, body="backend error"))
        db = SessionLocal()
        try:
            job = Ga4DailyJob(transport=scripted.transport, sleep=scripted.sleep)
            with pytest.raises(Ga4IngestUnavailable):
                job.run(db, BUCKET, 1)
            assert len(_rows_for(db, tag)) == 1, (
                "the job issued a DELETE on a report it never got — under a "
                "caller with no rollback that empties a real day"
            )
            db.rollback()
        finally:
            db.close()

        # Second, end to end through the runner, which is how production reaches
        # it and which also proves the failure is recorded rather than swallowed.
        from app.services.analytics.aggregation import AggregationRunner

        scripted = ScriptedGa4(_status(503, body="backend error"))
        monkeypatch.setitem(
            JOBS,
            jobs_ga4.JOB_NAME,
            Ga4DailyJob(transport=scripted.transport, sleep=scripted.sleep),
        )
        db = SessionLocal()
        try:
            runner = AggregationRunner(db, worker_id=WORKER_ID)
            with pytest.raises(Ga4IngestUnavailable) as excinfo:
                runner.run_bucket(jobs_ga4.JOB_NAME, BUCKET)
        finally:
            db.close()

        assert "503" in str(excinfo.value)

        db = SessionLocal()
        try:
            landed = _rows_for(db, tag)
            assert len(landed) == 1 and landed[0].sessions == 10, (
                "a 503 from Google deleted a day of real traffic and inserted "
                "nothing — the chart would show an honest-looking zero"
            )
        finally:
            db.close()
    finally:
        _cleanup([tag], [BUCKET])


def test_persistent_5xx_fails_loudly_and_the_run_records_it(ga4_configured, monkeypatch):
    """Through the real runner, so the failure lands in `analytics_sync_runs`
    rather than only in an exception nobody stored."""
    from app.services.analytics.aggregation import AggregationRunner

    scripted = ScriptedGa4(_status(500, body="internal error"))
    monkeypatch.setitem(
        JOBS,
        jobs_ga4.JOB_NAME,
        Ga4DailyJob(transport=scripted.transport, sleep=scripted.sleep),
    )

    db = SessionLocal()
    try:
        runner = AggregationRunner(db, worker_id=WORKER_ID)
        with pytest.raises(Ga4IngestUnavailable):
            runner.run_bucket(jobs_ga4.JOB_NAME, BUCKET)
        db.rollback()
    finally:
        db.close()

    db = SessionLocal()
    try:
        run = db.execute(
            select(AnalyticsSyncRun)
            .where(AnalyticsSyncRun.worker_id == WORKER_ID)
            .order_by(AnalyticsSyncRun.id.desc())
        ).scalars().first()
        assert run is not None
        assert run.status == "failed"
        assert "Ga4IngestUnavailable" in (run.error or "")
        assert "500" in (run.error or "")
    finally:
        db.close()
        _cleanup([], [BUCKET])


# ===========================================================================
# 6. Provisional window
# ===========================================================================
def test_the_provisional_policy_follows_ga4s_documented_latency():
    assert api.PROVISIONAL_HOURS == 48
    assert jobs_ga4.PROVISIONAL_AGE_DAYS == 3
    assert jobs_ga4.REINGEST_WINDOW_DAYS == 4

    today = date(2026, 7, 29)
    assert jobs_ga4.provisional_window(today) == (
        date(2026, 7, 29),
        date(2026, 7, 28),
        date(2026, 7, 27),
    )
    assert jobs_ga4.is_provisional(date(2026, 7, 27), today) is True
    assert jobs_ga4.is_provisional(date(2026, 7, 26), today) is False

    # A day closes at the start of the next one; GA4 may revise for 48h after.
    assert api.final_after(date(2026, 7, 29), tz_offset_hours=0.0) == datetime(
        2026, 8, 1, 0, 0, tzinfo=timezone.utc
    )
    assert api.final_after(date(2026, 7, 29), tz_offset_hours=5.5) == datetime(
        2026, 7, 31, 18, 30, tzinfo=timezone.utc
    )


def test_provisional_reingest_restates_rather_than_doubling(ga4_configured):
    """GA4's first answer for a day is not its last. A second pull must replace
    the first, and the row must stop claiming to be provisional once it isn't."""
    tag = _tag()
    try:
        _run_job(
            ScriptedGa4(
                _ok(
                    _report_payload(
                        [(_dims(tag, "/"), ["10", "6", "8", "3", "24", "600"])]
                    )
                )
            )
        )
        # GA4 revised the same day upward, as it does.
        result = _run_job(
            ScriptedGa4(
                _ok(
                    _report_payload(
                        [(_dims(tag, "/"), ["25", "14", "19", "7", "61", "1500"])]
                    )
                )
            )
        )
        assert result.rows_deleted == 1
        assert result.rows_written == 1

        db = SessionLocal()
        try:
            landed = _rows_for(db, tag)
            assert len(landed) == 1
            assert landed[0].sessions == 25, (
                f"expected the restated 25, got {landed[0].sessions} — 35 means "
                "the re-ingest accumulated instead of replacing"
            )
            assert landed[0].sum_engagement_seconds == 1500
        finally:
            db.close()
    finally:
        _cleanup([tag], [BUCKET])


def test_a_fresh_bucket_is_marked_provisional_and_an_old_one_is_not(ga4_configured):
    """The flag is a fact about the clock, so this test uses the real one."""
    tag = _tag()
    today = date.today()
    try:
        db = SessionLocal()
        try:
            scripted = ScriptedGa4(
                _ok(
                    _report_payload(
                        [
                            (
                                [
                                    today.strftime("%Y%m%d"),
                                    tag,
                                    "google / organic",
                                    "mobile",
                                    "/",
                                ],
                                ["10", "6", "8", "3", "24", "600"],
                            )
                        ]
                    )
                )
            )
            Ga4DailyJob(transport=scripted.transport, sleep=scripted.sleep).run(
                db, today, 1
            )
            db.commit()
        finally:
            db.close()

        db = SessionLocal()
        try:
            row = _rows_for(db, tag)[0]
            assert row.is_provisional is True
            assert row.final_after is not None
            assert row.bucket_date == today

            # ...and the trailing window it invalidates is queued for a re-pull.
            # Nothing else in this process can observe that GA4 restated a day,
            # so if this job does not enqueue it, nobody ever does.
            queued = {
                q.bucket_date
                for q in db.execute(
                    select(AnalyticsRecomputeQueue).where(
                        AnalyticsRecomputeQueue.job == jobs_ga4.JOB_NAME
                    )
                )
                .scalars()
                .all()
            }
            assert {today - timedelta(days=1), today - timedelta(days=2)} <= queued
            assert today not in queued, (
                "the bucket just built enqueued itself; the cascade would loop"
            )
        finally:
            db.close()
    finally:
        _cleanup([tag], [today, today - timedelta(days=1), today - timedelta(days=2)])


def test_a_backfill_does_not_enqueue_its_way_backwards(ga4_configured):
    """The termination property of the re-ingest cascade.

    Measuring "still provisional" against the bucket instead of against the real
    today would make every bucket enqueue the two before it, forever, and a
    backfill of 2014 would walk backwards through the calendar with a perfectly
    plausible-looking queue.
    """
    tag = _tag()
    try:
        _run_job(
            ScriptedGa4(
                _ok(
                    _report_payload(
                        [(_dims(tag, "/"), ["10", "6", "8", "3", "24", "600"])]
                    )
                )
            )
        )
        db = SessionLocal()
        try:
            queued = db.execute(
                select(AnalyticsRecomputeQueue).where(
                    AnalyticsRecomputeQueue.job == jobs_ga4.JOB_NAME,
                    AnalyticsRecomputeQueue.bucket_date < BUCKET,
                )
            ).scalars().all()
            assert queued == [], (
                "a 2014 backfill queued its predecessors for a GA4 re-pull; the "
                "cascade does not terminate"
            )
        finally:
            db.close()
    finally:
        _cleanup(
            [tag], [BUCKET, BUCKET - timedelta(days=1), BUCKET - timedelta(days=2)]
        )


# ===========================================================================
# 7. Property timezone
# ===========================================================================
def test_property_timezone_mismatch_is_recorded_on_every_row(ga4_configured):
    tag = _tag()
    payload = _report_payload(
        [(_dims(tag, "/"), ["10", "6", "8", "3", "24", "600"])],
        time_zone="America/New_York",
    )
    try:
        result = _run_job(ScriptedGa4(_ok(payload)))
        db = SessionLocal()
        try:
            row = _rows_for(db, tag)[0]
            assert row.tz_mismatch is True
            assert row.property_timezone == "America/New_York"
            # GA4's own day label survives being filed under a store-local date.
            assert row.ga4_date == "20140317"
        finally:
            db.close()

        warning = next(w for w in result.warnings if "ga4_timezone_mismatch" in w)
        assert "America/New_York" in warning
        assert STORE_TZ in warning
    finally:
        _cleanup([tag], [BUCKET])


def test_a_matching_property_timezone_sets_no_mismatch(ga4_configured):
    tag = _tag()
    payload = _report_payload(
        [(_dims(tag, "/"), ["10", "6", "8", "3", "24", "600"])], time_zone=STORE_TZ
    )
    try:
        result = _run_job(ScriptedGa4(_ok(payload)))
        db = SessionLocal()
        try:
            row = _rows_for(db, tag)[0]
            assert row.tz_mismatch is False
            assert row.property_timezone == STORE_TZ
        finally:
            db.close()
        assert not any("ga4_timezone_mismatch" in w for w in result.warnings)
    finally:
        _cleanup([tag], [BUCKET])


def test_a_missing_property_timezone_is_reported_rather_than_assumed(ga4_configured):
    tag = _tag()
    payload = _report_payload(
        [(_dims(tag, "/"), ["10", "6", "8", "3", "24", "600"])], time_zone=""
    )
    try:
        result = _run_job(ScriptedGa4(_ok(payload)))
        assert any("ga4_timezone_unknown" in w for w in result.warnings)
        db = SessionLocal()
        try:
            row = _rows_for(db, tag)[0]
            assert row.property_timezone == DIMENSION_UNKNOWN
            assert row.tz_mismatch is False
        finally:
            db.close()
    finally:
        _cleanup([tag], [BUCKET])


# ===========================================================================
# 8. Dimensions
# ===========================================================================
def test_ga4s_spellings_of_unknown_all_collapse_to_the_sentinel(ga4_configured):
    """`(not set)` and `''` in a UNIQUE key are two spellings of the same thing,
    and no GROUP BY will ever combine them."""
    tag = _tag()
    payload = _report_payload(
        [
            (
                ["20140317", tag, "(not set)", "", "/"],
                ["10", "6", "8", "3", "24", "600"],
            )
        ]
    )
    try:
        _run_job(ScriptedGa4(_ok(payload)))
        db = SessionLocal()
        try:
            row = _rows_for(db, tag)[0]
            assert row.source_medium == DIMENSION_UNKNOWN
            assert row.device_category == DIMENSION_UNKNOWN
        finally:
            db.close()
    finally:
        _cleanup([tag], [BUCKET])


def test_a_long_landing_page_is_truncated_and_the_truncation_is_reported(ga4_configured):
    tag = _tag()
    long_path = "/products/" + ("x" * 400)
    payload = _report_payload(
        [(_dims(tag, long_path), ["10", "6", "8", "3", "24", "600"])]
    )
    try:
        result = _run_job(ScriptedGa4(_ok(payload)))
        db = SessionLocal()
        try:
            row = _rows_for(db, tag)[0]
            assert len(row.landing_page) == LANDING_PAGE_MAX_CHARS
        finally:
            db.close()
        assert any("ga4_landing_page_truncated" in w for w in result.warnings)
    finally:
        _cleanup([tag], [BUCKET])


def test_rows_colliding_after_normalisation_are_merged_not_duplicated(ga4_configured):
    """Two GA4 rows can land on one key here — truncation and the `'-'` sentinel
    both collapse distinct GA4 values. Appending both would violate the UNIQUE
    constraint mid-INSERT and fail the whole bucket."""
    tag = _tag()
    payload = _report_payload(
        [
            (["20140317", tag, "(not set)", "mobile", "/"], ["10", "6", "8", "3", "24", "600"]),
            (["20140317", tag, "", "mobile", "/"], ["5", "2", "4", "1", "11", "150"]),
        ]
    )
    try:
        result = _run_job(ScriptedGa4(_ok(payload)))
        assert result.rows_written == 1
        db = SessionLocal()
        try:
            row = _rows_for(db, tag)[0]
            assert row.sessions == 15
            assert row.sum_engagement_seconds == 750
            assert row.n_engagement == 15
        finally:
            db.close()
    finally:
        _cleanup([tag], [BUCKET])


# ===========================================================================
# 9. Logging — no credential material, at any level
# ===========================================================================
def test_no_credential_material_appears_in_any_log_record(ga4_configured, caplog):
    """The Measurement Protocol client leaked a live `api_secret` into the
    application log through httpx's INFO `HTTP Request: <full url>` line — on the
    success path, with no exception and nothing for a review to catch.

    This drives the whole flow (assertion -> token exchange -> bearer -> report)
    with every logger capturing at DEBUG, and asserts the private key, its id,
    the signed assertion and the access token appear in none of the records.
    """
    caplog.set_level(logging.DEBUG)
    for name in ("httpx", "httpcore", "analytics.ga4_data_api", "app"):
        logging.getLogger(name).setLevel(logging.DEBUG)

    tag = _tag()
    payload = _report_payload(
        [(_dims(tag, "/"), ["10", "6", "8", "3", "24", "600"])]
    )
    try:
        # A failure path too: an error body is the other place a secret escapes.
        _run_job(ScriptedGa4(_ok(payload)))
        scripted = ScriptedGa4(_status(500, body="boom"))
        api.run_report(
            _config(),
            api.ReportRequest(
                date_from=BUCKET, date_to=BUCKET, metrics=("sessions",)
            ),
            transport=scripted.transport,
            sleep=scripted.sleep,
        )
    finally:
        _cleanup([tag], [BUCKET])

    messages = [r.getMessage() for r in caplog.records]

    # The test must have seen the exact line that leaked in the MP client —
    # httpx's INFO `HTTP Request: <full url>`. Without this it could pass by
    # capturing nothing at all.
    request_lines = [m for m in messages if "HTTP Request" in m]
    assert len(request_lines) >= 2, (
        "httpx logged no request lines, so this test proved nothing about the "
        f"channel that leaked the api_secret. Captured: {messages}"
    )
    assert any("oauth2.googleapis.com/token" in m for m in request_lines)
    assert any(":runReport" in m for m in request_lines)

    haystack = "\n".join(
        messages
        + [str(r.args) for r in caplog.records]
        + [str(getattr(r, "exc_text", "") or "") for r in caplog.records]
    )
    for needle, label in (
        (SERVICE_ACCOUNT["private_key"], "the RSA private key"),
        (SERVICE_ACCOUNT["private_key_id"], "the private key id"),
        (ACCESS_TOKEN, "the OAuth access token"),
        ("BEGIN PRIVATE KEY", "a PEM private-key header"),
        ("assertion=", "a signed JWT assertion"),
    ):
        assert needle not in haystack, f"{label} was written to the log"

    # The property id is NOT a credential, and a request line without it would
    # be useless for debugging. Redaction must not have taken the whole URL.
    assert any(f"properties/{PROPERTY_ID}" in m for m in request_lines)


def test_the_redaction_filter_strips_material_this_process_did_not_compose():
    """Defence in depth for the guarantee above. The bearer token lives in a
    header today, so httpx cannot render it into a request line — but
    "structurally impossible" arguments have a way of surviving the refactor
    that breaks them."""
    logger = logging.getLogger("httpx")
    record = logger.makeRecord(
        "httpx",
        logging.INFO,
        __file__,
        1,
        "HTTP Request: POST https://x/token?access_token=%s "
        'body={"private_key": "%s"} header=Bearer %s',
        (ACCESS_TOKEN, SERVICE_ACCOUNT["private_key"], ACCESS_TOKEN),
        None,
    )
    for log_filter in logger.filters:
        log_filter.filter(record)
    message = record.getMessage()
    assert ACCESS_TOKEN not in message
    assert SERVICE_ACCOUNT["private_key"] not in message
    assert api.REDACTED in message


def test_redact_handles_every_shape_a_credential_arrives_in():
    pem = SERVICE_ACCOUNT["private_key"]
    secret_assertion = uuid.uuid4().hex * 2

    cases = {
        "form body": f"assertion={secret_assertion}&grant_type=x",
        "token json": f'{{"access_token": "{ACCESS_TOKEN}", "expires_in": 3599}}',
        "auth header": f"Authorization: Bearer {ACCESS_TOKEN}",
        "escaped json key": (
            '{"private_key": "-----BEGIN PRIVATE KEY-----\\nabc\\n'
            '-----END PRIVATE KEY-----"}'
        ),
        "raw pem": pem,
    }
    for label, text in cases.items():
        out = api.redact(text, ACCESS_TOKEN, pem)
        assert ACCESS_TOKEN not in out, label
        assert pem not in out, label
        assert secret_assertion not in out, label
        assert api.REDACTED in out, f"{label} was not redacted at all"

    # The multi-line PEM body must go, not merely its literal copy.
    assert "MII" not in api.redact(pem)

    # Short strings are not credentials and must not be shredded — replacing
    # them would render unrelated log text unreadable.
    assert api.redact("the quick brown fox", "the") == "the quick brown fox"


def test_the_credential_is_never_in_a_url(rollups_on):
    """The structural guarantee, asserted rather than asserted-in-prose: the
    Measurement Protocol put its secret in the query string and httpx logged it.
    Nothing this module sends carries a credential in a URL."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "oauth2" in str(request.url):
            return httpx.Response(
                200, json={"access_token": ACCESS_TOKEN, "expires_in": 3600}
            )
        return httpx.Response(200, json=_report_payload([]))

    api.run_report(
        _config(),
        api.ReportRequest(date_from=BUCKET, date_to=BUCKET, metrics=("sessions",)),
        transport=httpx.MockTransport(handler),
    )

    assert seen
    for url in seen:
        assert "?" not in url, f"a query string appeared on {url}"
        assert ACCESS_TOKEN not in url
        assert "assertion" not in url
    assert any(f"properties/{PROPERTY_ID}:runReport" in u for u in seen)


# ===========================================================================
# 10. Schema: reflection, conventions, and the twin SQL
# ===========================================================================
TABLE = AggGa4Daily.__table__


def test_table_exists_and_columns_match_the_orm():
    db = SessionLocal()
    try:
        inspector = inspect(db.get_bind())
        assert inspector.has_table(TABLE.name), (
            f"{TABLE.name} is declared in the ORM but missing from the database. "
            "Run `alembic upgrade c9f13ab6e207`, or apply "
            "backend/scripts/sql/2026-07-29_agg_ga4_daily.sql on the shared DB."
        )
        actual = {c["name"]: c for c in inspector.get_columns(TABLE.name)}
        missing = sorted(set(TABLE.columns.keys()) - set(actual))
        assert not missing, f"columns in ORM but not in DB: {missing}"

        def family(text: str) -> str:
            text = text.upper()
            if "BOOL" in text or "TINYINT" in text:
                return "BOOL"
            for key in ("DECIMAL", "NUMERIC"):
                if key in text:
                    return "NUMERIC"
            for key in ("BIGINT", "SMALLINT", "INTEGER", "INT"):
                if key in text:
                    return "INT"
            for key in ("VARCHAR", "CHAR", "TEXT"):
                if key in text:
                    return "STRING"
            for key in ("DATETIME", "TIMESTAMP"):
                if key in text:
                    return "DATETIME"
            if "DATE" in text:
                return "DATE"
            if "FLOAT" in text or "DOUBLE" in text or "REAL" in text:
                return "FLOAT"
            return text

        for column in TABLE.columns:
            assert family(str(actual[column.name]["type"])) == family(
                str(column.type)
            ), (
                f"{column.name}: ORM says {column.type}, database has "
                f"{actual[column.name]['type']}"
            )
    finally:
        db.close()


def test_indexes_and_unique_key_exist_in_the_database():
    db = SessionLocal()
    try:
        inspector = inspect(db.get_bind())
        declared = {ix.name for ix in TABLE.indexes}
        actual = {ix["name"] for ix in inspector.get_indexes(TABLE.name)}
        missing = sorted(declared - actual)
        assert not missing, f"indexes declared in the ORM but absent from DB: {missing}"

        unique = {
            tuple(c["column_names"])
            for c in inspector.get_unique_constraints(TABLE.name)
        } | {
            tuple(ix["column_names"])
            for ix in inspector.get_indexes(TABLE.name)
            if ix.get("unique")
        }
        assert (
            "bucket_date",
            "channel_group",
            "source_medium",
            "device_category",
            "landing_page",
            "tz_generation",
        ) in unique
    finally:
        db.close()


def test_the_four_rollup_conventions_hold():
    db = SessionLocal()
    try:
        inspector = inspect(db.get_bind())

        # 1. No foreign keys — rollups stay independently truncatable.
        assert not TABLE.foreign_keys
        assert not inspector.get_foreign_keys(TABLE.name)

        # 2. tz_generation inside the unique key.
        uniques = [c for c in TABLE.constraints if isinstance(c, UniqueConstraint)]
        assert len(uniques) == 1
        key_columns = [c.name for c in uniques[0].columns]
        assert "tz_generation" in key_columns

        # 3. No nullable column inside the unique key. MySQL permits many NULLs
        #    under UNIQUE, so one would defeat the idempotency key entirely.
        assert not [c.name for c in uniques[0].columns if c.nullable]

        # 4. No stored averages, and no stored rates.
        offenders = [
            c.name
            for c in TABLE.columns
            if c.name.startswith("avg_")
            or c.name in {"aov", "average"}
            or c.name.endswith(("_pct", "_rate", "_ratio"))
        ]
        assert not offenders, f"stored averages/rates forbidden: {offenders}"

        # Every duration sum has its denominator.
        columns = set(TABLE.columns.keys())
        for name in [c for c in columns if c.startswith("sum_") and c.endswith("_seconds")]:
            assert f"n_{name[len('sum_'):-len('_seconds')]}" in columns

        # No floats anywhere.
        assert not [
            c.name
            for c in TABLE.columns
            if any(k in str(c.type).upper() for k in ("FLOAT", "DOUBLE", "REAL"))
        ]
    finally:
        db.close()


def test_the_table_holds_no_money_column():
    """GA4 revenue is browser-tag revenue. Money comes from the transactional
    tables, and the way to keep that true is for there to be nowhere to put it."""
    money_ish = {
        c.name
        for c in TABLE.columns
        if "revenue" in c.name
        or "amount" in c.name
        or "value" in c.name
        or "NUMERIC" in str(c.type).upper()
        or "DECIMAL" in str(c.type).upper()
    }
    assert not money_ish, f"GA4 must never be a source of money: {money_ish}"


def test_the_model_is_registered_for_autogenerate():
    from app.db.base import Base

    assert TABLE.name in Base.metadata.tables, (
        "app/db/base.py does not import AggGa4Daily — Alembic autogenerate "
        "cannot see the table and will propose dropping it"
    )


def test_non_additive_columns_are_declared_for_whoever_binds_this_table():
    """`metric_kind.classify` is name-based and defaults unknown names to FLOW,
    which is the dangerous direction: SUMming `total_users` across landing pages
    counts anyone who visited two of them twice, and the query succeeds.

    `metric_kind.py` is not this change's to edit, so the contract is exported
    instead — and adding `total_users` to `metric_kind._DISTINCT_EXACT` is a
    prerequisite for binding this column to any view.
    """
    from app.services.analytics.metric_kind import MetricKind, classify

    assert NON_ADDITIVE_COLUMNS == {"total_users"}
    assert NON_ADDITIVE_COLUMNS <= set(TABLE.columns.keys())

    # The invariant a binding must rely on: a column is safe to SUM only when
    # `metric_kind` calls it a FLOW *and* this table does not veto it. The union
    # is the guard; either half alone is currently insufficient, which is the
    # whole reason `NON_ADDITIVE_COLUMNS` is exported.
    def summable(name: str) -> bool:
        return classify(name) is MetricKind.FLOW and name not in NON_ADDITIVE_COLUMNS

    assert not summable("total_users"), (
        "total_users would be summed across landing pages and days, counting "
        "anyone who appears in two of them twice"
    )

    # Everything else here really is additive, so nothing else needs the guard.
    additive = {
        "sessions",
        "engaged_sessions",
        "new_users",
        "screen_page_views",
        "sum_engagement_seconds",
        "n_engagement",
    }
    for name in additive:
        assert classify(name) is MetricKind.FLOW
        assert name not in NON_ADDITIVE_COLUMNS


def test_the_job_is_registered_under_its_name():
    assert jobs_ga4.JOB_NAME in JOBS
    assert JOBS[jobs_ga4.JOB_NAME].name == jobs_ga4.JOB_NAME


# --------------------------------------------------------------------------
# The twin
# --------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parents[1]
TWIN_SQL = BACKEND_DIR / "scripts" / "sql" / "2026-07-29_agg_ga4_daily.sql"
REVISION = "c9f13ab6e207"
DOWN_REVISION = "d51e8072ca43"


def _statements(sql: str) -> list[str]:
    """Comment-free, whitespace-normalised statements, minus the alembic stamp.

    The stamp is excluded on both sides on purpose: the shared production MySQL
    is on a migration lineage this repo does not contain, so the twin file must
    NOT carry it, and comparing it would guarantee a mismatch.
    """
    body = re.sub(r"(?m)^\s*--.*$", "", sql)
    out = []
    for statement in body.split(";"):
        collapsed = " ".join(statement.split())
        if not collapsed:
            continue
        if "alembic_version" in collapsed:
            continue
        out.append(collapsed)
    return out


def test_migration_and_twin_sql_agree():
    """Two artifacts, one schema. If they drift, production 500s on every
    analytics call while CI stays green — the failure mode DEPLOY.md §6 exists
    to describe and this test exists to prevent."""
    assert TWIN_SQL.exists(), f"{TWIN_SQL} is missing"

    generated = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "upgrade",
            f"{DOWN_REVISION}:{REVISION}",
            "--sql",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert generated.returncode == 0, generated.stderr[-2000:]

    assert _statements(generated.stdout) == _statements(TWIN_SQL.read_text()), (
        "backend/scripts/sql/2026-07-29_agg_ga4_daily.sql no longer matches "
        f"revision {REVISION}. Regenerate it with\n"
        f"    alembic upgrade {DOWN_REVISION}:{REVISION} --sql\n"
        "and strip the alembic_version stamp."
    )


def test_the_twin_carries_no_alembic_stamp():
    """Stamping the shared remote DB with a revision id from this repo's chain
    would corrupt its migration state — it is on the `conpay001` lineage."""
    assert "alembic_version" not in TWIN_SQL.read_text().split("-- Running upgrade")[-1]


def test_the_revision_declares_the_chain_it_was_written_against():
    module = (
        BACKEND_DIR / "alembic" / "versions" / f"{REVISION}_add_agg_ga4_daily.py"
    ).read_text()
    assert f"revision: str = '{REVISION}'" in module
    assert f"down_revision: Union[str, None] = '{DOWN_REVISION}'" in module
