"""GA4 Data API (``runReport``) — the READ side of GA4.

Its sibling :mod:`app.services.analytics.ga4` speaks the Measurement Protocol
and can only *write*: it posts events at ``/mp/collect`` and gets a 204 with no
body back. It cannot be asked what GA4 recorded. This module is the other half —
authenticated ``runReport`` calls against
``analyticsdata.googleapis.com/v1beta``, which is the only way anything in this
codebase can read sessions, users, channels, devices or landing pages.

Read that module first; the conventions here are deliberately its conventions,
and the three places this one diverges are called out below because each
divergence is a decision rather than an oversight.


Divergence 1: this client OWNS its retry
========================================
The MP client owns no retry at all, because every send it makes is driven by
``analytics_event_outbox``, and a client that retried on its own behalf would
spend the outbox's attempt budget without telling it.

There is no outbox on the read side. A ``runReport`` is a batch pull made by an
aggregation job inside a bounded time budget, with no queue row, no attempt
counter and nobody to hand a transient 429 back to. So backoff lives here (see
:func:`run_report`), it is bounded (:data:`MAX_ATTEMPTS`), and its sleep is
injectable so the tests exercise it without spending wall-clock time.


Divergence 2: the credential is NOT in the URL, and that is the whole point
==========================================================================
The Measurement Protocol takes its ``api_secret`` in the **query string**, which
is the single worst place for our purposes: httpx logs
``HTTP Request: POST <full url> "..."`` at INFO on every request, so the live
credential was written into the application log on the success path — no
exception, no error branch, nothing for a review to catch. ``ga4.py`` closes
that with a logging filter.

Google's Data API takes an OAuth2 bearer token in the ``Authorization``
**header**, and the service-account assertion is exchanged for it in a POST
**body**. Neither is ever rendered into a URL by anything, so the INFO line for
a report reads::

    HTTP Request: POST https://analyticsdata.googleapis.com/v1beta/properties/123456789:runReport "HTTP/1.1 200 OK"

— a property id and nothing else. The property id is not a credential; it is an
``ADMIN``-visibility setting only because a browser has no use for it.

That is a *structural* guarantee, not a careful one, and it is why this module
can be safe where the MP client had to be defended. :class:`_SecretRedactingFilter`
is still installed on top, because "structurally impossible" arguments have a
way of surviving the refactor that breaks them: it strips PEM private-key
blocks, ``assertion=`` bodies, ``access_token`` values and ``Authorization:
Bearer`` headers out of any record this process did not compose.
``tests/test_analytics_ga4_data_api.py`` captures every emitted record at every
level and asserts none of the credential material appears in any of them.


Divergence 3: failure is a RESULT, not an empty list
====================================================
Every call returns a :class:`Ga4Report` carrying a :class:`ReportStatus`:

``OK``
    The call happened. ``rows`` may still be empty — that is a real measurement
    ("nobody visited"), and ``empty_reason`` says so when GA4 explains it.
``NOT_CONFIGURED``
    No property id, no service-account JSON, or the feature flag is off. Nothing
    was attempted.
``UNAVAILABLE``
    Something was attempted and it failed. ``reason`` is a machine token,
    ``detail`` is the redacted human message.

The three are kept apart because collapsing them is the defect that outlives
everyone: an empty success is indistinguishable from a real zero, so a view
backed by a silently failing integration renders "0 sessions" forever, looks
exactly like a quiet week, and nobody ever has a reason to look. A
``Ga4Report`` cannot even be constructed with rows unless its status is ``OK``.


Authentication, without a new dependency
========================================
``google-auth`` is not in ``requirements.txt`` and this module does not add it.
The service-account flow it would perform is small and fully specified, so it is
done here with ``cryptography`` (already a dependency, it is what Fernet runs
on):

1. Build an RS256 JWT asserting ``iss=client_email``,
   ``scope=analytics.readonly``, ``aud=token_uri``, signed with the PEM private
   key from the service-account JSON.
2. POST it to the token endpoint as ``grant_type=jwt-bearer``.
3. Use the returned ``access_token`` as a bearer for the report calls, cached
   in-process until shortly before it expires.

The JSON itself is stored Fernet-encrypted in ``system_settings``, the same
pattern as ``PaymentMethod.credentials_encrypted``, and is read through
``analytics.integrations.read_secret`` so the encryption, the validation and the
admin write path all stay owned by one module.


The settings keys, verified rather than assumed
===============================================
:data:`SETTING_PROPERTY_ID` and :data:`SETTING_CREDENTIALS` are the exact
strings ``analytics/integrations.py`` writes, and ``integrations.py`` is
authoritative because it owns the field schema, the validation, the Fernet
encryption and the admin UI. This is not a stylistic preference: the MP client
originally used *dotted* names (``analytics.ga4.measurement_id``) while the
admin screen wrote *underscore* ones (``analytics.ga4_measurement_id``), so an
operator could save a credential, see it persisted, and have the worker report
"not configured" forever with no error anywhere. Silent non-delivery with no
error is the worst shape a bug can take, and it is prevented structurally here:
:func:`assert_settings_keys_are_canonical` checks both constants against
``integrations.FIELD_BY_KEY`` at import of the test suite, so a rename breaks a
test instead of an integration.


What this module deliberately does not do
=========================================
* **It is never a source of money.** GA4 revenue is browser-tag revenue: it is
  lossy to ad blockers, it double-counts across the server outbox and the tag,
  and it is not the ledger. Revenue, orders and margin come from the internal
  transactional tables. Nothing landed from this module may be graded
  ``AUTHORITATIVE``; :data:`MAX_QUALITY` is ``ACTUAL`` and
  :func:`quality_for` cannot return anything better.
* **It does not decide what a day is.** GA4 buckets by the *property's*
  configured timezone, which is not necessarily ``store.timezone``. The
  property's zone comes back on every response as
  :attr:`Ga4Report.property_timezone`; reconciling or recording that is the
  ingest job's problem, and it must not be done silently.
"""
from __future__ import annotations

import base64
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings as env_settings
from app.services.analytics.types import MetricQuality

__all__ = [
    "API_ROOT",
    "TOKEN_URI",
    "SCOPE",
    "MAX_ATTEMPTS",
    "MAX_DIMENSIONS",
    "MAX_METRICS",
    "MAX_LIMIT",
    "MAX_PAGES",
    "MAX_QUALITY",
    "PROVISIONAL_HOURS",
    "final_after",
    "SETTING_PROPERTY_ID",
    "SETTING_CREDENTIALS",
    "GA4_DATA_API_SETTING_KEYS",
    "ReportStatus",
    "Ga4DataApiError",
    "Ga4DataApiContractError",
    "Ga4DataApiTransportError",
    "Ga4DataApiAuthError",
    "Ga4DataApiConfig",
    "ReportRequest",
    "Ga4Row",
    "Ga4Report",
    "assert_settings_keys_are_canonical",
    "load_data_api_config",
    "quality_for",
    "redact",
    "run_report",
    "run_report_all",
    "reset_token_cache",
]

log = logging.getLogger("analytics.ga4_data_api")

#: v1beta is the generally-available surface. v1alpha exists and carries the
#: funnel/path report types; it is deliberately not used here, because an alpha
#: endpoint can change shape under a running deployment.
API_ROOT = "https://analyticsdata.googleapis.com/v1beta"

#: Google's OAuth2 token endpoint. The service-account JSON also carries a
#: `token_uri`; that value is preferred when present (it is what the key file
#: was issued for) and this is the fallback.
TOKEN_URI = "https://oauth2.googleapis.com/token"

#: Read-only. A service account with this scope cannot modify the property, and
#: asking for anything wider would be asking for a credential we do not need.
SCOPE = "https://www.googleapis.com/auth/analytics.readonly"

#: Bounded twice, exactly as the MP client is: a connect timeout so a black-holed
#: route fails in seconds rather than at the OS TCP timeout, and a total timeout
#: so a server that accepts the connection and then stalls cannot hold the
#: aggregation worker (and its bucket transaction) open.
CONNECT_TIMEOUT_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 30.0

#: Total attempts per request, including the first. Three is enough to ride out
#: a rate-limit blip and small enough that a genuine outage fails inside the
#: runner's time budget rather than consuming it.
MAX_ATTEMPTS = 3

#: Exponential backoff base and cap, in seconds. Jittered, because every store
#: on a shared schedule retrying at exactly 1s/2s/4s is a synchronised thundering
#: herd against the same quota bucket.
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_MAX_SECONDS = 8.0

#: A `Retry-After` longer than this is ignored in favour of failing now. Google
#: occasionally answers a quota exhaustion with a multi-hour hint, and sleeping
#: on it inside an aggregation bucket would hold a transaction open all night.
MAX_RETRY_AFTER_SECONDS = 30.0

#: GA4's documented caps on one `runReport`.
MAX_DIMENSIONS = 9
MAX_METRICS = 10

#: Rows per request. GA4 permits 250 000; 10 000 is the page size and 100 000 the
#: ceiling a caller may ask for, because a single bucket that genuinely produces
#: more rows than that is a grain mistake, not a paging problem.
DEFAULT_LIMIT = 10_000
MAX_LIMIT = 100_000

#: Pages `run_report_all` will follow before refusing. Bounded so a
#: mis-specified request cannot turn one bucket into an unbounded pull.
MAX_PAGES = 20

#: Google's documented ceiling on GA4 processing latency, and the reason
#: :func:`final_after` exists. The standard-property SLA is "most data within 24
#: hours, all within 48": the figure for a day is answerable the moment the day
#: ends and keeps *changing* for two more days as late hits, cross-device
#: stitching and session unification land. A number hardened from the first read
#: is wrong by an amount nobody measures, because nothing ever reads it again.
PROVISIONAL_HOURS = 48

#: The best grade anything sourced from GA4 may ever carry. GA4 is a
#: measurement of behaviour by a browser tag, not a record of what happened in
#: the business: it loses 10-40% to ad blockers, it samples, and it withholds
#: rows below a privacy threshold. `AUTHORITATIVE` is reserved for the internal
#: transactional record and must never be written from here.
MAX_QUALITY = MetricQuality.ACTUAL

#: What a redacted credential is replaced with. Same token as the MP client.
REDACTED = "***"

#: Numeric GA4 property id. Matched so that a measurement id (`G-XXXXXXXXXX`) or
#: a `properties/123` prefix pasted into the admin field is rejected at config
#: load, instead of producing a 404 on every nightly run.
_PROPERTY_ID_RE = re.compile(r"^\d{6,15}$")

#: GA4 API names: `sessions`, `sessionSourceMedium`, and the prefixed custom
#: forms `customEvent:foo` / `customUser:bar`. Validated rather than passed
#: through, so a caller cannot smuggle an arbitrary string into the request body.
_API_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}(:[A-Za-z0-9_]{1,64})?$")

#: How much of an upstream error body is worth keeping. Enough to identify the
#: complaint, short enough for `analytics_sync_runs.error`.
_ERROR_BODY_MAX_CHARS = 300

#: Fernet ciphertext always starts with this. Used only to tell a plausible
#: "someone pasted the raw JSON into the encrypted field" from real ciphertext.
_FERNET_PREFIX = "gAAAAA"

#: Refresh a token this long before it actually expires. A token that is valid
#: when checked and expired when the request lands produces a 401 that looks
#: exactly like a revoked key.
_TOKEN_SKEW_SECONDS = 120


# ---------------------------------------------------------------------------
# Settings keys — the exact strings `integrations.py` writes. See the module
# docstring for the dotted/underscore bug this pairing exists to prevent.
# ---------------------------------------------------------------------------
#: Numeric GA4 property id. `Visibility.ADMIN`: not a credential, but the
#: browser has no use for it, so it never reaches `/settings/public`.
SETTING_PROPERTY_ID = "analytics.ga4_property_id"

#: The whole service-account key file, **Fernet ciphertext**. `Visibility.SECRET`:
#: it contains an RSA private key that grants read access to the property, is
#: redacted to `***` in every admin response, and must never appear in
#: `_PUBLIC_KEYS` in `app/api/v1/endpoints/settings.py`.
SETTING_CREDENTIALS = "analytics.ga4_data_api_credentials"

#: Every key this module reads. Exported so a seeder, the public-endpoint
#: allowlist check and the tests can assert against the set rather than a
#: hand-copied list.
GA4_DATA_API_SETTING_KEYS = (SETTING_PROPERTY_ID, SETTING_CREDENTIALS)


def assert_settings_keys_are_canonical() -> None:
    """Check both constants against the authoritative field schema.

    ``integrations.py`` owns these rows — the validation, the Fernet encryption
    and the admin form that writes them — so a key this module reads that is not
    in ``FIELD_BY_KEY`` is a key nothing will ever write. That failure is
    invisible at runtime: the loader returns "not configured", the job no-ops,
    the run log stays green and the views stay dark forever.

    Raises ``AssertionError``. Called from the test suite; safe to call from a
    startup check.
    """
    from app.services.analytics import integrations

    prop = integrations.FIELD_BY_KEY.get(SETTING_PROPERTY_ID)
    assert prop is not None, (
        f"{SETTING_PROPERTY_ID!r} is not in integrations.FIELD_BY_KEY; nothing "
        "writes it, so the Data API would report 'not configured' forever"
    )
    assert prop.visibility == integrations.Visibility.ADMIN, (
        f"{SETTING_PROPERTY_ID!r} must stay ADMIN-visible: it is not a secret, "
        "but no browser needs it"
    )

    creds = integrations.FIELD_BY_KEY.get(SETTING_CREDENTIALS)
    assert creds is not None, (
        f"{SETTING_CREDENTIALS!r} is not in integrations.FIELD_BY_KEY; nothing "
        "writes it, so the Data API would report 'not configured' forever"
    )
    assert creds.visibility == integrations.Visibility.SECRET, (
        f"{SETTING_CREDENTIALS!r} holds an RSA private key and MUST be SECRET "
        "(Fernet-encrypted at rest, redacted in every response)"
    )
    assert SETTING_CREDENTIALS not in integrations.PUBLIC_KEYS, (
        f"{SETTING_CREDENTIALS!r} is on the public settings allowlist — a "
        "service-account private key would be served to anonymous browsers"
    )
    assert SETTING_PROPERTY_ID not in integrations.PUBLIC_KEYS, (
        f"{SETTING_PROPERTY_ID!r} is on the public settings allowlist"
    )


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class Ga4DataApiError(RuntimeError):
    """Base for everything this module raises. Never carries key material."""


class Ga4DataApiContractError(Ga4DataApiError):
    """The request is wrong and will be wrong again next time.

    A malformed dimension name, an out-of-range limit, an inverted date range,
    or a 400 from Google. Not retried: a payload that cannot become valid burns
    the attempt budget and delays the alert a human actually needs to see.
    """


class Ga4DataApiTransportError(Ga4DataApiError):
    """The request did not get through, and might next time.

    Connection failures, timeouts, 429 and 5xx. Retried here, bounded by
    :data:`MAX_ATTEMPTS`.
    """


class Ga4DataApiAuthError(Ga4DataApiError):
    """The credential was rejected (401/403) or could not be used at all.

    Separated from the contract error because the remedy is different and
    specific: re-issue the service-account key, or grant it Viewer on the
    property in GA4 Admin -> Property Access Management. Retrying cannot fix it.
    """


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------
#: A PEM private-key block, however it was interpolated. DOTALL because the key
#: is multi-line; non-greedy so two keys in one string are both caught.
_PEM_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
#: The JSON field, before it is ever parsed — a raw credentials blob logged as
#: a string would otherwise leak the key without a PEM newline in sight.
_PRIVATE_KEY_FIELD_RE = re.compile(
    r'("private_key"\s*:\s*")(?:\\.|[^"\\])*(")', re.IGNORECASE
)
#: The signed assertion, as it appears in a form-encoded token request body.
_ASSERTION_RE = re.compile(r"(assertion=)[^&\s'\"]+", re.IGNORECASE)
#: An access token, in a JSON body or a query string.
_ACCESS_TOKEN_JSON_RE = re.compile(
    r'("access_token"\s*:\s*")[^"]*(")', re.IGNORECASE
)
_ACCESS_TOKEN_QS_RE = re.compile(r"(access_token=)[^&\s'\"]+", re.IGNORECASE)
#: An Authorization header rendered into a repr or a traceback.
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}", re.IGNORECASE)


def redact(text: str, *secrets: str) -> str:
    """Strip credential material from a string bound for a log or an exception.

    Two passes, because they fail differently. Literal replacement catches a
    secret wherever *we* interpolated it; the patterns catch a string built by a
    library that never saw our variables — which is the case that actually
    leaks, since httpx and json compose those themselves.
    """
    out = text or ""
    for secret in secrets:
        # A 1-7 char "secret" is not a credential, and replacing it would shred
        # unrelated text into unreadability. Access tokens and PEM bodies are
        # both far longer than this.
        if secret and len(secret) >= 8:
            out = out.replace(secret, REDACTED)
    out = _PEM_RE.sub(REDACTED, out)
    out = _PRIVATE_KEY_FIELD_RE.sub(r"\1" + REDACTED + r"\2", out)
    out = _ASSERTION_RE.sub(r"\1" + REDACTED, out)
    out = _ACCESS_TOKEN_JSON_RE.sub(r"\1" + REDACTED + r"\2", out)
    out = _ACCESS_TOKEN_QS_RE.sub(r"\1" + REDACTED, out)
    return _BEARER_RE.sub(r"\1" + REDACTED, out)


class _SecretRedactingFilter(logging.Filter):
    """Redact credential material from records this process did not compose.

    The bearer token lives in a header and the assertion in a POST body, so —
    unlike the Measurement Protocol's query-string ``api_secret`` — neither is
    reachable by httpx's INFO ``HTTP Request: ...`` line today. This filter is
    the belt to that braces: it costs one substring test per record and it means
    the guarantee survives a refactor that moves a credential somewhere new, or
    a third-party library that decides to render a request repr on error.
    """

    #: Cheap pre-check. A record containing none of these cannot contain a
    #: credential in any of the shapes above, and the overwhelming majority of
    #: records contain none of them.
    _MARKERS = ("PRIVATE KEY", "private_key", "assertion=", "access_token", "Bearer ")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a filter must never break logging
            return True
        if not any(marker in message for marker in self._MARKERS):
            return True
        record.msg = redact(message)
        record.args = ()
        return True


def _install_log_redaction() -> None:
    """Attach the filter once, at import, to every logger that renders requests.

    Import-time and global on purpose, for the same reason the MP client's is:
    the leak this class of bug produces is on the *success* path, so it has to be
    closed before the first request rather than around the ones someone
    remembered to guard. Idempotent.
    """
    for name in ("httpx", "httpcore", "analytics.ga4_data_api"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, _SecretRedactingFilter) for f in logger.filters):
            logger.addFilter(_SecretRedactingFilter())


_install_log_redaction()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Ga4DataApiConfig:
    """Resolved Data API credentials, or the machine-readable reason there are none.

    ``credentials`` is ``repr=False`` and ``compare=False`` so this object can be
    dropped into a log line, an f-string or an assertion diff without leaking the
    RSA private key it holds — including by accident, by code written later.
    """

    property_id: str = ""
    credentials: Mapping[str, Any] = field(
        default_factory=dict, repr=False, compare=False
    )
    #: Why this config is unusable, or None when it is usable. A machine token
    #: (`rollups_disabled`, `no_property_id`, ...) rather than prose, so the
    #: worker log, the health probe and the admin panel can all branch on it.
    reason: str | None = None
    #: Plain-language expansion of `reason`, safe to show an operator. Never
    #: contains a value read from the credential.
    detail: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.property_id and self.credentials and self.reason is None)

    @property
    def client_email(self) -> str:
        """The service account's identity. Not a secret — it is the `iss` claim
        and is what an operator must grant Viewer to in GA4 Admin."""
        return str(self.credentials.get("client_email") or "")

    @property
    def token_uri(self) -> str:
        return str(self.credentials.get("token_uri") or TOKEN_URI)


def load_data_api_config(
    db: Session, *, ignore_feature_flag: bool = False
) -> Ga4DataApiConfig:
    """Read the Data API credentials from `system_settings`, or say why we cannot.

    Never raises. An unusable configuration is a normal operating state — most
    stores have not connected GA4 — and it must produce a ``reason`` the job can
    log and no-op on, rather than an exception that reads like a fault and turns
    a green pipeline red on a store that simply has not configured anything.

    ``ignore_feature_flag`` exists so an admin "test connection" button can
    resolve credentials while ``ANALYTICS_ROLLUPS_ENABLED`` is still off — which
    is exactly the moment somebody is trying to check their setup. It does not
    bypass any other check.
    """
    if not ignore_feature_flag and not env_settings.ANALYTICS_ROLLUPS_ENABLED:
        # The documented kill switch for the whole rollup subsystem. Not an
        # error, and it must not read credentials it has been told not to use.
        return Ga4DataApiConfig(
            reason="rollups_disabled",
            detail=(
                "ANALYTICS_ROLLUPS_ENABLED is false, so the GA4 Data API is not "
                "consulted."
            ),
        )

    # Imported here rather than at module scope: `integrations` imports the
    # analytics control models and the audit service, and this module is
    # imported by the settings-facing code paths that `integrations` itself
    # reaches. A local import keeps the dependency one-directional.
    from app.services.analytics import integrations
    from app.services.settings_service import SettingsService

    property_id = (
        SettingsService(db).get_raw(SETTING_PROPERTY_ID, default="") or ""
    ).strip()
    if not property_id:
        return Ga4DataApiConfig(
            reason="no_property_id",
            detail=(
                "No GA4 Data API property id. Set it in Admin -> Analytics -> "
                "Google Analytics 4 (GA4 Admin -> Property Settings shows the "
                "numeric id)."
            ),
        )
    if not _PROPERTY_ID_RE.match(property_id):
        # A measurement id here produces a 404 on every nightly run, which
        # presents as "GA4 is down" rather than "that is the wrong id".
        log.warning(
            "GA4 Data API property id %r is not numeric — the Data API takes "
            "the numeric property id (123456789), not the measurement id "
            "(G-XXXXXXXXXX) and not a 'properties/' prefix",
            property_id,
        )
        return Ga4DataApiConfig(
            reason="invalid_property_id",
            detail=(
                "The GA4 Data API property id must be 6-15 digits. A "
                "measurement id (G-XXXXXXXXXX) will not work."
            ),
        )

    state = integrations.secret_state(db, SETTING_CREDENTIALS)
    if state == "unset":
        return Ga4DataApiConfig(
            property_id=property_id,
            reason="no_credentials",
            detail=(
                "No GA4 Data API service-account JSON. Create a service account "
                "in Google Cloud IAM, download its key, grant it Viewer on the "
                "property, and paste the whole file into Admin -> Analytics."
            ),
        )
    if state == "undecryptable":
        # SECRET_KEY rotated under the stored ciphertext. Recoverable only by
        # re-entering the key, so say so rather than retrying nightly forever.
        log.error(
            "GA4 Data API credentials could not be decrypted — SECRET_KEY has "
            "most likely been rotated. Re-paste the service-account JSON in "
            "Admin -> Analytics."
        )
        return Ga4DataApiConfig(
            property_id=property_id,
            reason="credentials_undecryptable",
            detail=(
                "The stored service-account JSON cannot be decrypted; SECRET_KEY "
                "has most likely been rotated. Re-paste the key file."
            ),
        )

    raw = integrations.read_secret(db, SETTING_CREDENTIALS)
    try:
        parsed = json.loads(raw)
    except ValueError:
        # Deliberately does NOT echo `raw` — it is the key file.
        log.error(
            "GA4 Data API credentials are not valid JSON. Re-paste the whole "
            "service-account key file."
        )
        return Ga4DataApiConfig(
            property_id=property_id,
            reason="credentials_malformed",
            detail="The stored service-account credentials are not valid JSON.",
        )

    missing = [
        key
        for key in ("client_email", "private_key")
        if not (isinstance(parsed, dict) and parsed.get(key))
    ]
    if missing:
        return Ga4DataApiConfig(
            property_id=property_id,
            reason="credentials_incomplete",
            detail=(
                "The stored JSON is missing "
                f"{', '.join(missing)} — that is not a service-account key file. "
                "Download it from Google Cloud IAM -> Service Accounts -> Keys."
            ),
        )
    if raw.startswith(_FERNET_PREFIX):  # pragma: no cover - defensive
        # Double-encrypted: `read_secret` decrypted once and the result is still
        # ciphertext. Means the value was encrypted before it was saved.
        return Ga4DataApiConfig(
            property_id=property_id,
            reason="credentials_malformed",
            detail="The stored credentials decrypt to more ciphertext.",
        )

    return Ga4DataApiConfig(property_id=property_id, credentials=parsed)


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReportRequest:
    """One bounded ``runReport``. Every field is validated before it is sent.

    **``date_from`` and ``date_to`` are INCLUSIVE**, because GA4's ``dateRanges``
    are, and reshaping them here would put a half-open/closed conversion between
    the caller and the wire where nobody would look for it. Everything else in
    this analytics stack is half-open (``timebox.range_bounds_utc``,
    ``AggregationRunner.run_window``), so the conversion happens once, explicitly,
    at the call site — see ``jobs_ga4``, which passes a single day as
    ``date_from == date_to``.

    The dates are interpreted by GA4 in the **property's** timezone, not the
    store's and not UTC. That is not something this type can fix; it is why
    :attr:`Ga4Report.property_timezone` exists.
    """

    date_from: date
    date_to: date
    metrics: tuple[str, ...]
    dimensions: tuple[str, ...] = ()
    limit: int = DEFAULT_LIMIT
    offset: int = 0
    #: GA4 omits rows whose every metric is zero. Left off: a zero row is not
    #: information, it is `limit` spent on nothing, and this stack never
    #: zero-fills a dimensioned rollup anyway.
    keep_empty_rows: bool = False

    def __post_init__(self) -> None:
        if self.date_to < self.date_from:
            raise Ga4DataApiContractError(
                f"date_to ({self.date_to}) precedes date_from ({self.date_from}); "
                "GA4 dateRanges are inclusive and must be ordered oldest-first"
            )
        if not self.metrics:
            raise Ga4DataApiContractError(
                "a runReport with no metrics returns dimension rows with nothing "
                "measured on them"
            )
        if len(self.metrics) > MAX_METRICS:
            raise Ga4DataApiContractError(
                f"GA4 accepts at most {MAX_METRICS} metrics per report, got "
                f"{len(self.metrics)}"
            )
        if len(self.dimensions) > MAX_DIMENSIONS:
            raise Ga4DataApiContractError(
                f"GA4 accepts at most {MAX_DIMENSIONS} dimensions per report, got "
                f"{len(self.dimensions)}"
            )
        for name in (*self.metrics, *self.dimensions):
            if not _API_NAME_RE.match(name or ""):
                raise Ga4DataApiContractError(
                    f"{name!r} is not a GA4 API name (letters, digits and "
                    "underscores, optionally prefixed as 'customEvent:foo')"
                )
        if len(set(self.metrics)) != len(self.metrics):
            raise Ga4DataApiContractError(f"duplicate metric in {self.metrics}")
        if len(set(self.dimensions)) != len(self.dimensions):
            raise Ga4DataApiContractError(f"duplicate dimension in {self.dimensions}")
        if not 1 <= self.limit <= MAX_LIMIT:
            raise Ga4DataApiContractError(
                f"limit must be between 1 and {MAX_LIMIT}, got {self.limit}"
            )
        if self.offset < 0:
            raise Ga4DataApiContractError(f"offset must not be negative, got {self.offset}")

    def to_body(self) -> dict[str, Any]:
        """The JSON body GA4 expects. Nothing here is a credential."""
        return {
            "dateRanges": [
                {
                    "startDate": self.date_from.isoformat(),
                    "endDate": self.date_to.isoformat(),
                }
            ],
            "dimensions": [{"name": name} for name in self.dimensions],
            "metrics": [{"name": name} for name in self.metrics],
            "limit": self.limit,
            "offset": self.offset,
            "keepEmptyRows": self.keep_empty_rows,
        }

    def at_offset(self, offset: int) -> "ReportRequest":
        """The same request, paged forward. Used by :func:`run_report_all`."""
        return ReportRequest(
            date_from=self.date_from,
            date_to=self.date_to,
            metrics=self.metrics,
            dimensions=self.dimensions,
            limit=self.limit,
            offset=offset,
            keep_empty_rows=self.keep_empty_rows,
        )


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------
class ReportStatus(str, Enum):
    """Three states that must never collapse into each other.

    See the module docstring: an empty ``OK`` is a real zero, and rendering a
    failure as one produces "0 sessions" forever with nothing to notice.
    """

    #: The call happened and the answer is what GA4 has. `rows` may be empty.
    OK = "ok"
    #: Nothing was attempted — no credentials, or the feature flag is off.
    NOT_CONFIGURED = "not_configured"
    #: Something was attempted and it failed. `reason` and `detail` say what.
    UNAVAILABLE = "unavailable"


#: A metric value GA4 withheld rather than measured. GA4 signals a suppressed
#: cell as an empty string, which `int("")` cannot parse and `int(x or 0)`
#: silently turns into a zero — the exact conversion this constant exists to
#: prevent anyone from writing.
SUPPRESSED = None


@dataclass(frozen=True)
class Ga4Row:
    """One report row. Dimension values as strings, metric values as parsed ints.

    A metric that GA4 withheld is ``None``, **not** ``0``. The two are different
    facts — "fewer than the privacy threshold used this landing page" versus
    "nobody did" — and every consumer must decide which it means rather than
    inheriting a zero from a parser.
    """

    dimensions: tuple[str, ...]
    metrics: tuple[int | None, ...]

    def dimension(self, headers: Sequence[str], name: str) -> str:
        """Look a dimension up by GA4 API name; ``''`` when the report lacks it."""
        try:
            return self.dimensions[list(headers).index(name)]
        except (ValueError, IndexError):
            return ""

    def metric(self, headers: Sequence[str], name: str) -> int | None:
        """Look a metric up by GA4 API name. ``None`` means withheld OR absent."""
        try:
            return self.metrics[list(headers).index(name)]
        except (ValueError, IndexError):
            return None

    @property
    def has_suppressed_metric(self) -> bool:
        return any(value is SUPPRESSED for value in self.metrics)


@dataclass(frozen=True)
class Ga4Report:
    """What one ``runReport`` produced, including everything that qualifies it.

    The metadata fields are not decoration. Each one names a way GA4's answer can
    be less than it appears, and a view that renders the numbers without them is
    presenting an estimate as a measurement:

    ``is_sampled``
        GA4 answered from a sample of sessions and scaled up. The figure is an
        extrapolation with an error bar nobody computed.
    ``subject_to_thresholding``
        GA4 **withheld rows** to protect identifiable individuals. The rows are
        missing, not zero, so the totals are a floor and not a total.
    ``data_loss_from_other_row``
        Cardinality overflowed and GA4 folded the tail into an "(other)" row.
    ``property_timezone``
        Which day boundary these rows were bucketed by. Not necessarily
        ``store.timezone``.
    """

    status: ReportStatus
    rows: tuple[Ga4Row, ...] = ()
    dimension_headers: tuple[str, ...] = ()
    metric_headers: tuple[str, ...] = ()
    #: Total rows matching the query, across all pages. `len(rows)` is this page.
    row_count: int = 0
    property_timezone: str = ""
    currency_code: str = ""
    is_sampled: bool = False
    #: `(samplesReadCount, samplingSpaceSize)` per date range, as GA4 reported
    #: them. Stored as the two counts, never as their ratio — the ratio cannot
    #: be re-aggregated and this stack does not store derivable percentages.
    sampling: tuple[tuple[int, int], ...] = ()
    subject_to_thresholding: bool = False
    data_loss_from_other_row: bool = False
    #: GA4's own explanation for a zero-row answer, when it gives one.
    empty_reason: str = ""
    #: Machine token when `status` is not OK: `no_credentials`, `http_503`, ...
    reason: str | None = None
    #: Redacted human message. Safe to store in `analytics_sync_runs.error`.
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status is not ReportStatus.OK and self.rows:
            raise ValueError(
                "a Ga4Report that is not OK must carry no rows — a partial "
                "result presented as data is how a failed integration renders "
                "as a real zero"
            )
        if self.status is ReportStatus.OK and self.reason is not None:
            raise ValueError("an OK Ga4Report must not carry a failure reason")

    @property
    def ok(self) -> bool:
        return self.status is ReportStatus.OK

    @property
    def configured(self) -> bool:
        """False only for ``NOT_CONFIGURED``. An ``UNAVAILABLE`` report IS
        configured — it was tried and it broke, which needs an alert, not an
        onboarding prompt."""
        return self.status is not ReportStatus.NOT_CONFIGURED

    @property
    def suppressed_rows(self) -> int:
        """Rows GA4 returned with at least one withheld metric.

        Note this is a **floor** on suppression: rows GA4 omitted entirely are
        not here and cannot be counted from the response at all. When
        `subject_to_thresholding` is set, assume more is missing than this.
        """
        return sum(1 for row in self.rows if row.has_suppressed_metric)

    @property
    def quality(self) -> MetricQuality:
        """The best grade anything from this report may carry. Never AUTHORITATIVE."""
        return quality_for(
            sampled=self.is_sampled,
            thresholded=self.subject_to_thresholding,
            data_loss=self.data_loss_from_other_row,
        )

    @classmethod
    def not_configured(cls, reason: str, detail: str) -> "Ga4Report":
        return cls(status=ReportStatus.NOT_CONFIGURED, reason=reason, detail=detail)

    @classmethod
    def unavailable(cls, reason: str, detail: str) -> "Ga4Report":
        return cls(status=ReportStatus.UNAVAILABLE, reason=reason, detail=detail)


def quality_for(*, sampled: bool, thresholded: bool, data_loss: bool) -> MetricQuality:
    """Grade a GA4 figure. Capped at :data:`MAX_QUALITY` by construction.

    ``INCOMPLETE`` beats ``ESTIMATED`` when both apply, matching
    ``types.worst_quality``: one missing input makes the whole figure
    incomplete, and a sampled figure with rows also missing is the worse of the
    two problems.
    """
    if thresholded or data_loss:
        # Rows are MISSING. `coverage_pct` is unknowable from the response, and
        # a total built from a withheld set is a floor, not a total.
        return MetricQuality.INCOMPLETE
    if sampled:
        # Measured on a subset and scaled. That is a documented estimation
        # procedure, which is exactly what ESTIMATED means here.
        return MetricQuality.ESTIMATED
    return MAX_QUALITY


# ---------------------------------------------------------------------------
# Access tokens
# ---------------------------------------------------------------------------
@dataclass
class _CachedToken:
    """A live bearer token. ``repr=False`` on the value; never log this."""

    value: str = field(repr=False)
    expires_at: float

    def usable(self, now: float) -> bool:
        return bool(self.value) and now < self.expires_at - _TOKEN_SKEW_SECONDS


#: Keyed by (client_email, token_uri). In-process only: a token is a bearer
#: credential and does not belong in Redis next to cacheable page data, and the
#: aggregation worker is a single long-lived process that would otherwise mint a
#: fresh one for every bucket in a backfill.
_TOKEN_CACHE: dict[tuple[str, str], _CachedToken] = {}


def reset_token_cache() -> None:
    """Drop every cached access token. For tests and for credential rotation."""
    _TOKEN_CACHE.clear()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _build_assertion(config: Ga4DataApiConfig, *, now: int) -> str:
    """An RS256 JWT asserting the service account's identity for one hour.

    Hand-built rather than pulled from ``google-auth``: the flow is small and
    fully specified, and adding an auth library to the production image for
    three signatures is a dependency with its own transitive surface.

    Every failure here is an :class:`Ga4DataApiAuthError` and none of them echo
    the key. ``cryptography`` renders informative messages about malformed PEM
    data, and "informative about a private key" is precisely what must not reach
    a log.
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    private_key_pem = str(config.credentials.get("private_key") or "")
    client_email = config.client_email
    if not private_key_pem or not client_email:  # pragma: no cover - loader checks
        raise Ga4DataApiAuthError(
            "service-account JSON is missing client_email or private_key"
        )

    try:
        key = serialization.load_pem_private_key(
            private_key_pem.encode("utf-8"), password=None
        )
    except Exception as exc:  # noqa: BLE001 - message may quote the key material
        raise Ga4DataApiAuthError(
            "the service-account private key is not a readable PEM key "
            f"({type(exc).__name__}). Re-download the key file from Google "
            "Cloud IAM."
        ) from None
    if not isinstance(key, rsa.RSAPrivateKey):
        raise Ga4DataApiAuthError(
            "the service-account private key is not RSA; Google issues RSA keys "
            "for service accounts and the assertion must be RS256"
        )

    header: dict[str, Any] = {"alg": "RS256", "typ": "JWT"}
    key_id = config.credentials.get("private_key_id")
    if key_id:
        # Not a secret — it names which of the account's keys signed this, and
        # Google needs it when several are live.
        header["kid"] = str(key_id)

    claims = {
        "iss": client_email,
        "scope": SCOPE,
        "aud": config.token_uri,
        "iat": now,
        # One hour is Google's maximum for a self-signed assertion. Longer is
        # rejected outright; shorter just means more token exchanges.
        "exp": now + 3600,
    }
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    ).encode("ascii")
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return signing_input.decode("ascii") + "." + _b64url(signature)


def _fetch_access_token(
    config: Ga4DataApiConfig,
    *,
    transport: httpx.BaseTransport | None,
    now: float,
) -> str:
    """Exchange a signed assertion for a bearer token, and cache it.

    The assertion goes in the **form body**, never the URL, so nothing that
    renders a request line can carry it. The response body holds the token, so
    every error path below reports the status code and a redacted excerpt rather
    than the body itself.
    """
    cache_key = (config.client_email, config.token_uri)
    cached = _TOKEN_CACHE.get(cache_key)
    if cached is not None and cached.usable(now):
        return cached.value

    assertion = _build_assertion(config, now=int(now))
    try:
        with httpx.Client(
            timeout=httpx.Timeout(
                REQUEST_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS
            ),
            transport=transport,
        ) as client:
            response = client.post(
                config.token_uri,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as exc:
        # `from None`, exactly as the MP client does it: a chained traceback
        # prints the cause's message, httpx composes those itself, and a request
        # repr can include the body we just put the assertion in.
        raise Ga4DataApiTransportError(
            f"GA4 token endpoint unreachable: {type(exc).__name__}: "
            f"{redact(str(exc), assertion)}"
        ) from None

    if response.status_code != 200:
        body = redact((response.text or "")[:_ERROR_BODY_MAX_CHARS], assertion)
        if response.status_code in (400, 401, 403):
            # `invalid_grant` here almost always means the key was deleted, the
            # account was disabled, or the server clock is skewed past the
            # assertion's `iat`. None of those improve on a retry.
            raise Ga4DataApiAuthError(
                f"GA4 token endpoint rejected the service account "
                f"({response.status_code}): {body}"
            )
        raise Ga4DataApiTransportError(
            f"GA4 token endpoint returned {response.status_code}: {body}"
        )

    try:
        payload = response.json()
    except ValueError:
        raise Ga4DataApiTransportError(
            "GA4 token endpoint returned a non-JSON body"
        ) from None

    token = str(payload.get("access_token") or "")
    if not token:
        raise Ga4DataApiAuthError(
            "GA4 token endpoint returned no access_token"
        )
    try:
        lifetime = int(payload.get("expires_in") or 3600)
    except (TypeError, ValueError):
        lifetime = 3600

    _TOKEN_CACHE[cache_key] = _CachedToken(value=token, expires_at=now + lifetime)
    return token


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _backoff_seconds(attempt: int, retry_after: float | None) -> float:
    """Jittered exponential backoff, honouring a bounded ``Retry-After``."""
    if retry_after is not None and 0 < retry_after <= MAX_RETRY_AFTER_SECONDS:
        return retry_after
    ceiling = min(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), BACKOFF_MAX_SECONDS)
    # Full jitter. Half the stores on one cron retrying in lockstep is how a
    # transient 429 becomes a sustained one.
    return random.uniform(0.0, ceiling)


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        # The HTTP-date form. Not worth parsing: the caller falls back to
        # exponential backoff, which is bounded and always correct-enough.
        return None


def run_report(
    config: Ga4DataApiConfig,
    request: ReportRequest,
    *,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> Ga4Report:
    """One ``runReport``, with bounded retry. **Never raises for an operational failure.**

    Returns a :class:`Ga4Report` whose ``status`` is ``OK``, ``NOT_CONFIGURED``
    or ``UNAVAILABLE``. That is the contract the whole module exists for: an
    exception would have to be caught identically at every call site, and an
    empty list would be indistinguishable from a real zero. A caller that wants
    a failure to be loud checks ``report.ok`` and raises its own — which is what
    the ingest job does, so a failed pull lands in ``analytics_sync_runs`` as
    FAILED rather than as a quiet day of no traffic.

    Retries 429 and 5xx up to :data:`MAX_ATTEMPTS` with jittered exponential
    backoff. Does **not** retry 4xx (the request is wrong and will be wrong
    again) or 401/403 (the credential is wrong; retrying is how a revoked key
    becomes a quota problem).

    ``transport``, ``sleep`` and ``clock`` are the seams the tests use;
    production passes none of them.
    """
    if not config.configured:
        return Ga4Report.not_configured(
            reason=config.reason or "not_configured",
            detail=config.detail or "GA4 Data API credentials are not configured.",
        )

    url = f"{API_ROOT}/properties/{config.property_id}:runReport"
    body = request.to_body()
    last_detail = ""
    last_reason = "unavailable"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            token = _fetch_access_token(
                config, transport=transport, now=clock()
            )
        except Ga4DataApiAuthError as exc:
            return Ga4Report.unavailable(
                reason="auth_failed", detail=redact(str(exc))
            )
        except Ga4DataApiTransportError as exc:
            last_reason, last_detail = "token_unavailable", redact(str(exc))
            if attempt == MAX_ATTEMPTS:
                break
            sleep(_backoff_seconds(attempt, None))
            continue

        try:
            with httpx.Client(
                timeout=httpx.Timeout(
                    REQUEST_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS
                ),
                transport=transport,
            ) as client:
                response = client.post(
                    url,
                    json=body,
                    headers={
                        # The credential, in a header. Nothing renders this into
                        # a URL, which is the entire security argument of this
                        # module over its Measurement Protocol sibling.
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        "accept": "application/json",
                    },
                )
        except httpx.HTTPError as exc:
            last_reason = "transport_error"
            last_detail = redact(f"{type(exc).__name__}: {exc}", token)
            if attempt == MAX_ATTEMPTS:
                break
            sleep(_backoff_seconds(attempt, None))
            continue

        status = response.status_code
        if status == 200:
            try:
                payload = response.json()
            except ValueError:
                return Ga4Report.unavailable(
                    reason="malformed_response",
                    detail="GA4 runReport returned a 200 with a non-JSON body.",
                )
            return _parse_report(payload)

        excerpt = redact((response.text or "")[:_ERROR_BODY_MAX_CHARS], token)

        if status in (401, 403):
            # The cached token may simply have been revoked mid-flight; drop it
            # so the next scheduled run mints a fresh one rather than replaying
            # a dead credential for an hour.
            _TOKEN_CACHE.pop((config.client_email, config.token_uri), None)
            return Ga4Report.unavailable(
                reason=f"http_{status}",
                detail=(
                    f"GA4 rejected the credential ({status}): {excerpt} — grant "
                    f"{config.client_email or 'the service account'} Viewer on "
                    f"property {config.property_id} in GA4 Admin -> Property "
                    "Access Management."
                ),
            )
        if status == 429 or status >= 500:
            last_reason = f"http_{status}"
            last_detail = f"GA4 runReport returned {status}: {excerpt}"
            if attempt == MAX_ATTEMPTS:
                break
            sleep(_backoff_seconds(attempt, _retry_after(response)))
            continue

        # Every other 4xx: the request is wrong and will be wrong identically on
        # the next attempt. Failing now is what lets the run log name a bad
        # dimension instead of three timeouts.
        return Ga4Report.unavailable(
            reason=f"http_{status}",
            detail=f"GA4 rejected the report request ({status}): {excerpt}",
        )

    return Ga4Report.unavailable(
        reason=last_reason,
        detail=(
            f"{last_detail} (gave up after {MAX_ATTEMPTS} attempts)"
            if last_detail
            else f"GA4 runReport failed after {MAX_ATTEMPTS} attempts"
        ),
    )


def run_report_all(
    config: Ga4DataApiConfig,
    request: ReportRequest,
    *,
    max_pages: int = MAX_PAGES,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> Ga4Report:
    """:func:`run_report`, following ``rowCount`` across pages.

    Merges the pages into one report and keeps the **first** page's metadata,
    which is the only sound choice: sampling and thresholding are properties of
    the query, so they are identical on every page, while a later page's
    ``rowCount`` can shift under a concurrent GA4 processing pass.

    A failure on any page returns the failure, discarding earlier pages. A half
    a day's rows written as if they were the day is worse than no day at all —
    the number is plausible, low, and nothing marks it.
    """
    first = run_report(
        config, request, transport=transport, sleep=sleep, clock=clock
    )
    if not first.ok:
        return first

    rows = list(first.rows)
    pages = 1
    while len(rows) < first.row_count and pages < max_pages:
        page = run_report(
            config,
            request.at_offset(request.offset + len(rows)),
            transport=transport,
            sleep=sleep,
            clock=clock,
        )
        if not page.ok:
            return page
        if not page.rows:
            # GA4 said there were more and then returned none. Stop rather than
            # spin; the caller sees fewer rows than `row_count` and the job
            # warns about the shortfall.
            break
        rows.extend(page.rows)
        pages += 1

    if len(rows) < first.row_count and pages >= max_pages:
        log.warning(
            "GA4 runReport for %s..%s stopped at %d pages with %d of %d rows; "
            "the bucket's grain is producing more rows than one pull should",
            request.date_from,
            request.date_to,
            pages,
            len(rows),
            first.row_count,
        )

    from dataclasses import replace

    return replace(first, rows=tuple(rows))


def _parse_report(payload: Mapping[str, Any]) -> Ga4Report:
    """Turn GA4's JSON into a typed report. Never invents a value.

    The one judgement call is metric parsing: GA4 returns every value as a
    string, and a withheld cell comes back as ``""``. ``int(value or 0)`` — the
    obvious line — turns that into a zero, which is the single most damaging
    thing this parser could do, so :func:`_parse_metric` returns ``None`` and the
    row is marked suppressed instead.
    """
    dimension_headers = tuple(
        str(header.get("name") or "")
        for header in payload.get("dimensionHeaders") or ()
    )
    metric_headers = tuple(
        str(header.get("name") or "") for header in payload.get("metricHeaders") or ()
    )

    rows: list[Ga4Row] = []
    for raw in payload.get("rows") or ():
        rows.append(
            Ga4Row(
                dimensions=tuple(
                    str(cell.get("value") or "")
                    for cell in raw.get("dimensionValues") or ()
                ),
                metrics=tuple(
                    _parse_metric(cell.get("value"))
                    for cell in raw.get("metricValues") or ()
                ),
            )
        )

    metadata = payload.get("metadata") or {}
    sampling: list[tuple[int, int]] = []
    for entry in metadata.get("samplingMetadatas") or ():
        read = _parse_metric(entry.get("samplesReadCount")) or 0
        space = _parse_metric(entry.get("samplingSpaceSize")) or 0
        sampling.append((read, space))

    try:
        row_count = int(payload.get("rowCount") or len(rows))
    except (TypeError, ValueError):
        row_count = len(rows)

    return Ga4Report(
        status=ReportStatus.OK,
        rows=tuple(rows),
        dimension_headers=dimension_headers,
        metric_headers=metric_headers,
        row_count=row_count,
        property_timezone=str(metadata.get("timeZone") or ""),
        currency_code=str(metadata.get("currencyCode") or ""),
        # Sampling is present iff GA4 says how much of the space it read. A
        # `samplingMetadatas` entry whose two counts are equal is a full read,
        # not a sample, and must not be reported as one.
        is_sampled=any(read < space for read, space in sampling if space),
        sampling=tuple(sampling),
        subject_to_thresholding=bool(metadata.get("subjectToThresholding")),
        data_loss_from_other_row=bool(metadata.get("dataLossFromOtherRow")),
        empty_reason=str(metadata.get("emptyReason") or ""),
    )


def _parse_metric(value: Any) -> int | None:
    """Parse one metric cell. ``None`` for a value GA4 did not give us.

    GA4 sends every metric as a string, and duration metrics come back with a
    fractional part (``"1234.5"`` seconds). Parsed through ``Decimal`` and
    truncated: ``float`` would be a binary approximation of a number this stack
    stores as an integer, and ``int("1234.5")`` raises.

    ``""`` and ``None`` mean **withheld**, and they are the reason this function
    exists at all — see the class docstring on :class:`Ga4Row`.
    """
    if value is None:
        return SUPPRESSED
    text = str(value).strip()
    if not text:
        return SUPPRESSED
    try:
        return int(Decimal(text))
    except (InvalidOperation, ValueError):
        log.warning("GA4 returned an unparseable metric value %r", text)
        return SUPPRESSED


def final_after(bucket: date, *, tz_offset_hours: float = 0.0) -> datetime:
    """When a GA4 bucket stops being provisional, as a UTC instant.

    GA4 documents up to **48 hours** of processing latency: the figure for a day
    is answerable immediately and keeps changing afterwards, as late hits, cross
    -device stitching and session unification land. So a bucket closes at the end
    of its own day and is only trustworthy 48 hours after that.

    ``tz_offset_hours`` is the property's UTC offset, because the day this
    describes is the *property's* day. It is passed rather than looked up so this
    stays a pure function of what the response reported.
    """
    day_end_utc = datetime.combine(
        bucket + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
    ) - timedelta(hours=tz_offset_hours)
    return day_end_utc + timedelta(hours=PROVISIONAL_HOURS)
