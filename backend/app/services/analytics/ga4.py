"""GA4 Measurement Protocol client — the last gate before an event leaves.

This module is deliberately thin. It builds one HTTPS request, validates what
is about to go into it, and returns what came back. It owns **no** retry, no
queueing and no state: the outbox
(:mod:`app.services.analytics.outbox`) owns all of that, and a client that
retried on its own behalf would spend the outbox's attempt budget without ever
telling it.

What "success" means here, and why it is weaker than it looks
=============================================================
``/mp/collect`` answers **204 No Content** with an empty body. It answers 204
for a well-formed event, and it answers 204 for an event whose name is
misspelled, whose parameters are the wrong type, or which GA4 will drop on the
floor a moment later during processing. There is no acknowledgement, no event
id, and no error channel.

So a 204 proves exactly one thing: *the request was accepted for delivery*. It
is **not** proof the event was recorded, and no amount of client code can turn
it into that proof. The outbox marks a row ``delivered`` on a 204 because that
is the strongest signal that exists — the honest reading of it is "we handed
this to Google", not "GA4 has this purchase".

The one place a malformed event does get diagnosed is the validation endpoint,
``/debug/mp/collect``, which answers 200 with ``validationMessages``. It is not
a different pipeline — it is the same payload run through GA4's validator and
then discarded. Use ``debug=True`` from a script or an admin "test connection"
button when a property is being set up; never in the delivery path, because a
debug call *does not deliver the event*.

The api_secret
==============
The Measurement Protocol takes its credential in the **query string**
(``?measurement_id=G-...&api_secret=...``), which is the worst place a secret
can live for our purposes: it lands in every URL that any library, exception or
log formatter decides to render. httpx in particular puts the full URL into
``HTTPStatusError`` messages and into request reprs.

Everything raised or logged from this module is therefore built by hand from a
status code and a redacted body — the original exception is deliberately **not**
chained (``raise ... from None``), because a chained traceback prints the cause's
message, and that is the one string we cannot vouch for. :func:`redact` is
applied on top as a belt-and-braces pass.

The secret is stored Fernet-encrypted at rest, the same pattern as
``PaymentMethod.credentials_encrypted``; see :func:`load_ga4_config`.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings as env_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.services.analytics.tracking_events import (
    REQUIRED_PARAMS,
    assert_no_pii,
)
from app.services.settings_service import SettingsService

__all__ = [
    "COLLECT_URL",
    "DEBUG_COLLECT_URL",
    "MAX_EVENTS_PER_REQUEST",
    "Ga4Error",
    "Ga4ContractError",
    "Ga4TransportError",
    "Ga4Result",
    "Ga4Config",
    "SETTING_MEASUREMENT_ID",
    "SETTING_API_SECRET",
    "SETTING_DEBUG_ENDPOINT",
    "GA4_SETTING_KEYS",
    "encrypt_api_secret",
    "load_ga4_config",
    "redact",
    "send_events",
]

log = logging.getLogger("analytics.ga4")

#: Live collection. Returns 204 and an empty body — see the module docstring.
COLLECT_URL = "https://www.google-analytics.com/mp/collect"

#: Validation only. Returns 200 + `validationMessages`, and **does not deliver
#: the event**. Never call this from the delivery path.
DEBUG_COLLECT_URL = "https://www.google-analytics.com/debug/mp/collect"

#: GA4's documented cap on events per request.
MAX_EVENTS_PER_REQUEST = 25

#: Bounded, and bounded twice: a connect timeout so a black-holed route fails in
#: seconds rather than at the OS TCP timeout, and a total timeout so a server
#: that accepts the connection and then stalls cannot hold a worker (and, in the
#: outbox, a row lock) open indefinitely.
CONNECT_TIMEOUT_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 10.0

#: What a redacted secret is replaced with.
REDACTED = "***"

#: GA4 event names are lowercase snake_case and short. Validated rather than
#: trusted because the event name is the one field that bypasses
#: `assert_no_pii` — the denylist's deliberately broad "name" substring would
#: otherwise reject the Measurement Protocol's own `events[].name` key on every
#: legitimate event. Constraining the *value* is what keeps that exemption safe.
_EVENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

#: A GA4 web data-stream measurement id. Matched so a GTM container id
#: ("GTM-XXXX") or a Universal Analytics property ("UA-1234-5") pasted into the
#: admin field is rejected at config load instead of producing an endless stream
#: of 204s that record nothing anywhere.
_MEASUREMENT_ID_RE = re.compile(r"^G-[A-Z0-9]{4,20}$")

#: Anything shaped like the credential in a URL, whatever produced the string.
_SECRET_IN_QS_RE = re.compile(r"(api_secret=)[^&\s'\"]+", re.IGNORECASE)

#: How much of an upstream error body is worth keeping. Enough to identify the
#: complaint, short enough to fit `analytics_event_outbox.last_error`.
_ERROR_BODY_MAX_CHARS = 300


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
#: The GA4 measurement id (`G-XXXXXXXXXX`). Not a secret — it ships in the
#: browser tag — but it is not on the `/settings/public` allowlist either,
#: because nothing anonymous needs to read it from us.
SETTING_MEASUREMENT_ID = "analytics.ga4_measurement_id"

#: The Measurement Protocol api_secret, **Fernet ciphertext**. Must be seeded
#: with `is_secret=True` so the admin settings API redacts it, and must never
#: appear in `_PUBLIC_KEYS` in `app/api/v1/endpoints/settings.py` — that
#: endpoint is anonymous, and this credential can write events into the
#: property. Write it with :func:`encrypt_api_secret`.
SETTING_API_SECRET = "analytics.ga4_api_secret"

#: Route sends to the validation endpoint instead of live collection. For
#: bringing a property up; leaves GA4 permanently empty while it is on.
SETTING_DEBUG_ENDPOINT = "analytics.ga4_debug_endpoint"

#: Historical alias, accepted on read. The name a seeder might reasonably pick
#: for a ciphertext column; supported so a naming disagreement degrades to
#: "reads the other key" rather than "server-side conversions silently stop".
# Dotted variants, accepted on read only. These were this module's original
# names before it was reconciled with the field schema in
# `analytics/integrations.py`, which is authoritative because it owns the
# validation, the Fernet encryption and the admin UI that writes these rows.
#
# Keeping them readable costs one lookup and prevents the specific failure this
# reconciliation fixed: the admin saves a measurement id, sees it persisted, and
# the worker reports "not configured" forever. Silent non-delivery with no error
# anywhere is the worst shape a bug can take.
_SETTING_API_SECRET_ALIAS = "analytics.ga4.api_secret_encrypted"
_LEGACY_ALIASES = {
    SETTING_MEASUREMENT_ID: "analytics.ga4.measurement_id",
    SETTING_API_SECRET: "analytics.ga4.api_secret",
    SETTING_DEBUG_ENDPOINT: "analytics.ga4.debug_endpoint",
}

#: Every key this module reads. Exported so the settings seeder and its tests
#: can assert the set matches, and so the public-endpoint allowlist can be
#: checked against it.
GA4_SETTING_KEYS = (
    SETTING_MEASUREMENT_ID,
    SETTING_API_SECRET,
    SETTING_DEBUG_ENDPOINT,
)

#: Fernet ciphertext always starts with this (version byte 0x80, base64'd).
#: Used to tell "encrypted and we cannot read it" — a real failure — from
#: "someone saved the raw secret" — recoverable, and not worth losing every
#: conversion over.
_FERNET_PREFIX = "gAAAAA"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class Ga4Error(RuntimeError):
    """Base for everything this module raises. Never carries the api_secret."""


class Ga4ContractError(Ga4Error):
    """The request is wrong and will be wrong again next time.

    Missing required parameters, a malformed event name, an unusable
    measurement id, or a 4xx that is not rate limiting. The outbox treats this
    as **permanent**: retrying a payload that cannot become valid burns the
    attempt budget and delays the alert that a human actually needs to see.
    """


class Ga4TransportError(Ga4Error):
    """The request did not get through, and might next time.

    Connection failures, timeouts, 429 and 5xx. Retryable, on the outbox's
    schedule and never on this module's.
    """


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------
def redact(text: str, *secrets: str) -> str:
    """Strip credentials from a string bound for a log or an exception.

    Two passes, because they fail differently: literal replacement catches the
    secret wherever it was interpolated, and the query-string pattern catches a
    URL rendered by a library that never saw our variable — the case that
    actually leaks, since httpx builds those strings itself.
    """
    out = text or ""
    for secret in secrets:
        # A 1-3 char "secret" is not a credential and replacing it would shred
        # unrelated text into unreadability.
        if secret and len(secret) >= 4:
            out = out.replace(secret, REDACTED)
    return _SECRET_IN_QS_RE.sub(r"\1" + REDACTED, out)


class _SecretRedactingFilter(logging.Filter):
    """Redact `api_secret=...` out of records this process did not compose.

    httpx logs every request at INFO as ``HTTP Request: POST <full url> "..."``,
    built from its own `Request` object. With the Measurement Protocol putting
    the credential in the query string, that single line writes the api_secret
    into the application log on **every** successful send — no exception, no
    error path, nothing for a code review to notice.

    Careful wrapping of our own errors cannot reach that string, so it is
    intercepted where it is emitted instead.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a filter must never break logging
            return True
        if "api_secret=" not in message:
            return True
        record.msg = _SECRET_IN_QS_RE.sub(r"\1" + REDACTED, message)
        record.args = ()
        return True


def _install_log_redaction() -> None:
    """Attach the filter once, at import, to the loggers that render URLs.

    Import-time and global on purpose: the leak is on the success path, so it
    has to be closed before the first request rather than around the ones
    someone remembered to guard. Idempotent, and it only ever rewrites a
    credential-shaped query parameter.
    """
    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, _SecretRedactingFilter) for f in logger.filters):
            logger.addFilter(_SecretRedactingFilter())


_install_log_redaction()


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Ga4Result:
    """What came back. Read `accepted` with the module docstring in mind."""

    #: 204 from live collection, 200 from the validation endpoint.
    status_code: int
    #: Whether this went to `/debug/mp/collect` and therefore delivered nothing.
    debug: bool = False
    #: Only ever populated on a debug call. Empty means GA4's validator had no
    #: complaint; on a live call it means nothing at all, because live
    #: collection has no error channel.
    validation_messages: tuple[dict[str, Any], ...] = ()

    @property
    def accepted(self) -> bool:
        """GA4 took the request. **Not** a claim that the event was recorded."""
        return self.status_code in (200, 204)

    @property
    def valid(self) -> bool | None:
        """Validator verdict, or None when we did not ask for one.

        `None` is the honest answer for a live send and is deliberately not
        collapsed into `True` — "we did not check" and "it checked out" are the
        two states this whole module exists to keep apart.
        """
        if not self.debug:
            return None
        return not self.validation_messages


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Ga4Config:
    """Resolved GA4 credentials, or the reason there are none.

    `api_secret` is `repr=False`, so this object can be dropped into a log line
    or an exception without leaking — including by accident, via an f-string
    someone adds later.
    """

    measurement_id: str = ""
    api_secret: str = field(default="", repr=False)
    debug_endpoint: bool = False
    #: Why this config is unusable, or None when it is usable. A machine token
    #: (`tracking_disabled`, `no_measurement_id`, ...) rather than prose, so the
    #: worker log and the admin panel can branch on it.
    reason: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.measurement_id and self.api_secret and self.reason is None)


def encrypt_api_secret(plain: str) -> str:
    """Ciphertext for `SETTING_API_SECRET`. Call this before writing the value.

    Thin on purpose — the point is that there is one obvious function for the
    admin write path to reach for, so the encryption cannot be forgotten in the
    place it matters.
    """
    return encrypt_secret(plain)


def load_ga4_config(db: Session) -> Ga4Config:
    """Read GA4 credentials from `system_settings`, or say why we cannot.

    Never raises: an unusable configuration is a normal operating state (the
    store has not connected GA4 yet), and it must produce a `reason` the worker
    can log rather than an exception that looks like a fault.

    The api_secret is expected Fernet-encrypted. If the stored value is not
    ciphertext, it is used as-is and a warning is logged **without the value** —
    because refusing to send would turn "an admin pasted the secret into the
    wrong shaped field" into a silent, total loss of server-side conversions,
    which is a far worse outcome than a warning that says to re-save it.
    """
    if not env_settings.ANALYTICS_TRACKING_ENABLED:
        # The documented kill switch. Not an error, and it must not read
        # credentials it has been told not to use.
        return Ga4Config(reason="tracking_disabled")

    svc = SettingsService(db)
    measurement_id = (svc.get_raw(SETTING_MEASUREMENT_ID) or "").strip()
    if not measurement_id:
        return Ga4Config(reason="no_measurement_id")
    if not _MEASUREMENT_ID_RE.match(measurement_id):
        log.warning(
            "GA4 measurement id %r is not a G-XXXXXXXXXX web data-stream id "
            "(a GTM container id or a UA property id will be accepted by "
            "/mp/collect with a 204 and recorded nowhere)",
            measurement_id,
        )
        return Ga4Config(
            measurement_id=measurement_id, reason="invalid_measurement_id"
        )

    stored = (
        svc.get_raw(SETTING_API_SECRET)
        or svc.get_raw(_SETTING_API_SECRET_ALIAS)
        or ""
    ).strip()
    if not stored:
        return Ga4Config(measurement_id=measurement_id, reason="no_api_secret")

    if stored.startswith(_FERNET_PREFIX):
        try:
            api_secret = decrypt_secret(stored)
        except ValueError:
            # SECRET_KEY rotated under the stored ciphertext. Recoverable only
            # by re-entering the secret, so say so rather than retrying forever.
            log.error(
                "GA4 api_secret could not be decrypted — SECRET_KEY has most "
                "likely been rotated. Re-enter the Measurement Protocol secret "
                "in Admin -> Settings."
            )
            return Ga4Config(
                measurement_id=measurement_id, reason="api_secret_undecryptable"
            )
    else:
        log.warning(
            "GA4 api_secret in %s is not encrypted at rest. Re-save it through "
            "the admin settings API so it is stored as ciphertext.",
            SETTING_API_SECRET,
        )
        api_secret = stored

    return Ga4Config(
        measurement_id=measurement_id,
        api_secret=api_secret,
        debug_endpoint=svc.get_bool(SETTING_DEBUG_ENDPOINT, False),
    )


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------
def send_events(
    measurement_id: str,
    api_secret: str,
    client_id: str,
    events: Sequence[Mapping[str, Any]],
    *,
    debug: bool = False,
    timestamp_micros: int | None = None,
    non_personalized_ads: bool | None = None,
    consent: Mapping[str, str] | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
    transport: httpx.BaseTransport | None = None,
) -> Ga4Result:
    """POST one Measurement Protocol request. One attempt, no retries.

    `events` is a sequence of ``{"name": ..., "params": {...}}`` in GA4's own
    shape, passed through unchanged.

    **A 204 is not proof the event was accepted.** Live collection has no error
    channel: a misspelled event name, a wrong-typed parameter and a perfect
    payload all return 204 with an empty body. Pass ``debug=True`` to send the
    identical payload to GA4's validator instead — it answers 200 with
    ``validationMessages`` — but note that a debug call **delivers nothing**, so
    it belongs in a "test connection" button and never in the delivery loop.

    Every payload is run through ``assert_no_pii`` here, at the boundary, and
    that check is the reason this function exists as the single exit point.
    Validating only at the caller means the next caller — a refund event, a
    backfill script, an admin re-send — is unprotected the day it is written.

    Raises `Ga4ContractError` for anything that will fail identically on a
    retry, `Ga4TransportError` for anything that might not, and lets `PiiLeak`
    through untouched: it names a bug in the caller and must not be smoothed
    into a delivery failure that a retry could "fix".

    `transport` is the seam the tests use; production leaves it None.
    """
    measurement_id = (measurement_id or "").strip()
    api_secret = (api_secret or "").strip()
    client_id = (client_id or "").strip()

    if not measurement_id or not api_secret:
        raise Ga4ContractError("GA4 measurement_id and api_secret are both required")
    if not client_id:
        # GA4 has no way to attribute an event without one, and inventing a
        # client_id here would hide a caller that forgot to derive a stable one.
        raise Ga4ContractError("GA4 client_id is required")
    if not events:
        raise Ga4ContractError("refusing to send an empty GA4 event batch")
    if len(events) > MAX_EVENTS_PER_REQUEST:
        raise Ga4ContractError(
            f"GA4 accepts at most {MAX_EVENTS_PER_REQUEST} events per request, "
            f"got {len(events)}"
        )

    body: dict[str, Any] = {
        "client_id": client_id,
        "events": [_validated_event(event) for event in events],
    }
    if timestamp_micros is not None:
        body["timestamp_micros"] = int(timestamp_micros)
    if non_personalized_ads is not None:
        body["non_personalized_ads"] = bool(non_personalized_ads)
    if consent:
        body["consent"] = dict(consent)

    # The envelope, minus `events` — those were checked param-by-param above.
    # Excluded here rather than reshaped because the envelope's own
    # `events[].name` key trips the PII denylist's broad "name" substring, and
    # weakening that substring to accommodate the protocol would also let
    # `customer_name` through.
    assert_no_pii({k: v for k, v in body.items() if k != "events"})

    url = DEBUG_COLLECT_URL if debug else COLLECT_URL
    endpoint = "/debug/mp/collect" if debug else "/mp/collect"
    params = {"measurement_id": measurement_id, "api_secret": api_secret}

    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout, connect=CONNECT_TIMEOUT_SECONDS),
            transport=transport,
        ) as client:
            response = client.post(
                url,
                params=params,
                json=body,
                headers={
                    "Content-Type": "application/json",
                    "accept": "application/json",
                },
            )
    except httpx.HTTPError as exc:
        # `from None`: a chained traceback prints the cause's message, and httpx
        # builds those strings itself — including, for some of them, the full
        # URL with the api_secret in the query. The type name plus a redacted
        # message is everything that is safe to keep.
        detail = redact(str(exc), api_secret)
        raise Ga4TransportError(
            f"GA4 {endpoint} unreachable: {type(exc).__name__}: {detail}"
        ) from None

    return _interpret(response, api_secret=api_secret, endpoint=endpoint, debug=debug)


def _validated_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """One event, checked against the shared contract and stripped of nothing.

    Required parameters are enforced as *non-empty*, not merely present: an
    event carrying ``items: []`` or ``transaction_id: ""`` satisfies a
    key-presence check and is still unusable — a purchase with no
    ``transaction_id`` cannot be deduplicated against the browser's, which is
    the single failure that inflates reported revenue.
    """
    name = str(event.get("name") or "").strip()
    if not _EVENT_NAME_RE.match(name):
        raise Ga4ContractError(
            f"GA4 event name {name!r} is not a valid lowercase snake_case "
            "event name of 1-40 characters"
        )

    params = event.get("params") or {}
    if not isinstance(params, Mapping):
        raise Ga4ContractError(f"GA4 event {name!r} params must be an object")

    missing = [
        key
        for key in REQUIRED_PARAMS.get(name, ())
        if params.get(key) in (None, "", [], {})
    ]
    if missing:
        raise Ga4ContractError(
            f"GA4 event {name!r} is missing required parameter(s) {missing}. "
            "Sending it would produce a row GA4 cannot report on."
        )

    assert_no_pii(dict(params))
    return {"name": name, "params": dict(params)}


def _interpret(
    response: httpx.Response, *, api_secret: str, endpoint: str, debug: bool
) -> Ga4Result:
    """Turn a response into a result or a typed error, leaking nothing."""
    status = response.status_code

    if debug and status == 200:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        messages = tuple(payload.get("validationMessages") or ())
        if messages:
            log.warning("GA4 %s validation messages: %s", endpoint, messages)
        return Ga4Result(status_code=status, debug=True, validation_messages=messages)

    if status == 204 and not debug:
        return Ga4Result(status_code=status, debug=False)

    body = redact((response.text or "")[:_ERROR_BODY_MAX_CHARS], api_secret)

    # 429 and 5xx are the upstream having a moment; everything else in 4xx is
    # this request being wrong, and will be exactly as wrong on the next
    # attempt. Splitting them here is what lets the outbox retry one and
    # dead-letter the other instead of treating every failure the same way.
    if status == 429 or status >= 500:
        raise Ga4TransportError(f"GA4 {endpoint} returned {status}: {body}")
    raise Ga4ContractError(f"GA4 {endpoint} rejected the request ({status}): {body}")
