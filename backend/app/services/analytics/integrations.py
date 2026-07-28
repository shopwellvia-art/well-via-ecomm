"""Analytics integration settings, honest connection tests, and the tracking-
health probe behind the Analytics Control Centre.

Three things live here, and they exist together because they answer one
question: *is the store's tracking actually working, and can we prove it?*

1. `describe_settings()` — the schema the admin UI renders from.
2. `test_connection(db, provider)` — what we can genuinely verify from the
   server, and an explicit refusal for what we cannot.
3. `tracking_health(db)` — configured/enabled state, consent, the server-side
   outbox, environment mismatch, and rollup watermarks.


Public vs secret is the load-bearing distinction
================================================
Every setting here carries a `visibility`, and it decides where the value is
allowed to travel:

``PUBLIC``
    Ships to every browser anyway — a GTM container id, a GA4 measurement id
    and a Clarity project id are all embedded verbatim in the page source of
    any site that uses them. Treating them as secrets would be theatre. These
    are the keys that are safe to add to `_PUBLIC_KEYS` in
    `endpoints/settings.py`; `describe_settings()["public_keys"]` is the list.

``ADMIN``
    Not a credential and not something the browser ever needs — the GA4 Data
    API property id is the only one today. Visible in the admin response,
    never in `/settings/public`.

``SECRET``
    A credential. The GA4 Measurement Protocol `api_secret` can write events
    into the property from anywhere on the internet; a Data API service-account
    JSON can *read* the property. Both are stored **Fernet-encrypted** (same
    `app.core.crypto` helpers as `PaymentMethod.credentials_encrypted`),
    redacted to ``***`` in every response, and never in `/settings/public`.

The rule the code enforces rather than documents: a SECRET value is only ever
decrypted by server code that is about to use it. `current_values()` — the only
function the API layer calls to build a response — cannot return one.


Why the connection tests refuse to lie
======================================
A "Test connection" button that always goes green is worse than no button. It
converts "we did not check" into "we checked and it is fine", and the operator
stops looking. So:

* **GA4** is genuinely verifiable. The Measurement Protocol exposes
  ``/debug/mp/collect``, which validates the payload *and* the
  measurement-id/api-secret pair, and — unlike the production collect endpoint
  — reports what is wrong. That is a real check, and it returns ``verified``.
* **GTM** and **Clarity** have no server-side validation API at all.
  ``gtm.js?id=…`` returns 200 for any well-formed container id including ones
  that were never created, and Clarity publishes no equivalent endpoint. Both
  therefore return ``cannot_verify_server_side`` with ``verified: False`` and a
  plain-language note about what would actually establish the answer.

Every result carries `checked` (what was done) and `not_checked` (what the
result cannot possibly prove), so the UI never has to guess how much a status
is worth.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx
from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings as env_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.exceptions import AppError
from app.models.analytics_control import (
    AnalyticsEventOutbox,
    AnalyticsSyncRun,
    OutboxStatus,
    SyncStatus,
)
from app.models.system_setting import SystemSetting
from app.models.user import User
from app.services.analytics.tracking_events import SCHEMA_VERSION, assert_no_pii
from app.services.audit_service import AuditService
from app.services.settings_service import SettingsService

logger = logging.getLogger(__name__)

__all__ = [
    "CATEGORY",
    "REDACTED",
    "PROVIDERS",
    "Visibility",
    "IntegrationField",
    "BadIntegrationRequest",
    "InvalidIntegrationValue",
    "FIELDS",
    "FIELD_BY_KEY",
    "PUBLIC_KEYS",
    "SECRET_KEYS",
    "describe_settings",
    "ensure_rows",
    "current_values",
    "stored_flags",
    "read_secret",
    "secret_state",
    "apply_updates",
    "test_connection",
    "tracking_health",
]

#: `system_settings.category` every row written by this module carries. It is
#: deliberately NOT one of the categories in `AdminSettingsPage`'s
#: `CATEGORY_GROUPS`, so these rows do not leak into the generic settings page —
#: they have their own screen, with a health panel the generic page cannot show.
CATEGORY = "analytics"

#: Same sentinel `SettingsService` uses. Sending it back on a PUT means "the
#: admin did not touch this field" and the stored ciphertext is kept.
REDACTED = "***"

#: Providers `test_connection` accepts.
PROVIDERS = ("gtm", "ga4", "clarity")

#: The MP **validation** endpoint. Note the `/debug/` segment: this URL
#: validates and reports, it does NOT ingest. Pointing a connection test at the
#: production `/mp/collect` would write a junk event into the customer's real
#: property and still tell us nothing, because that endpoint answers 204 to
#: everything including a wholly invalid api_secret.
GA4_DEBUG_URL = "https://www.google-analytics.com/debug/mp/collect"

#: A connection test reaches a third party over the network from inside a
#: request worker. 6s is long enough for a healthy round trip and short enough
#: that a Google outage cannot pin a worker behind nginx's 60s proxy timeout.
GA4_TEST_TIMEOUT_SEC = 6.0

#: `ENVIRONMENT` values that mean "this is the real store". Anything else is a
#: staging/dev deployment for mismatch purposes.
PRODUCTION_ENVIRONMENTS = frozenset({"production", "prod", "live"})


class Visibility:
    """Where a setting's value is allowed to travel. See the module docstring."""

    #: Embedded in the page source anyway — safe for `/settings/public`.
    PUBLIC = "public"
    #: Admin-only, but not a credential. Never sent to a browser.
    ADMIN = "admin"
    #: A credential. Fernet-encrypted at rest, redacted in every response.
    SECRET = "secret"


class BadIntegrationRequest(AppError):
    """400 — an unknown settings key or an unknown provider.

    Defined here rather than in `core/exceptions.py` for the same reason
    `analytics_admin.BadRequestError` is: it is a *bad request*, not a schema
    violation (422) and not a missing resource (404), and this is the only
    surface that needs the distinction.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"


class InvalidIntegrationValue(AppError):
    """422 — a value that would break the tag if it were saved.

    Rejected rather than normalised. A measurement id of ``G-ABC 123`` silently
    coerced to something plausible is a tag that loads, reports nothing, and
    gives no one a reason to look at it again.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "validation_error"


# ===========================================================================
# Schema
# ===========================================================================
@dataclass(frozen=True)
class IntegrationField:
    """One settings row, plus everything the admin UI needs to render it."""

    key: str
    group: str
    label: str
    type: str  # bool | text | number | password | textarea | select
    visibility: str
    default: str = ""
    description: str = ""
    placeholder: str = ""
    #: (value, label) pairs for `type == "select"`.
    options: tuple[tuple[str, str], ...] = ()
    #: Anchored regex the non-empty value must satisfy.
    pattern: str | None = None
    #: Human-readable restatement of `pattern`, shown when validation fails.
    pattern_hint: str = ""

    @property
    def is_secret(self) -> bool:
        return self.visibility == Visibility.SECRET

    def as_dict(self, *, value: str, has_value: bool) -> dict[str, Any]:
        return {
            "key": self.key,
            "group": self.group,
            "label": self.label,
            "type": self.type,
            "visibility": self.visibility,
            "is_secret": self.is_secret,
            "description": self.description,
            "placeholder": self.placeholder,
            "options": [{"value": v, "label": lbl} for v, lbl in self.options],
            "pattern": self.pattern,
            "pattern_hint": self.pattern_hint,
            "default": self.default,
            "value": value,
            "has_value": has_value,
        }


@dataclass(frozen=True)
class IntegrationGroup:
    id: str
    label: str
    blurb: str
    #: Provider this group configures, when a connection test applies to it.
    provider: str | None = None
    fields: tuple[IntegrationField, ...] = dc_field(default_factory=tuple)


_YES = frozenset({"true", "1", "yes", "on"})
_NO = frozenset({"false", "0", "no", "off", ""})

_ON_OFF = (("true", "Enabled"), ("false", "Disabled"))
_GRANT = (("granted", "Granted"), ("denied", "Denied"))


FIELDS: tuple[IntegrationField, ...] = (
    # ---- Google Tag Manager -------------------------------------------------
    IntegrationField(
        key="analytics.gtm_enabled",
        group="gtm",
        label="Load Google Tag Manager",
        type="bool",
        visibility=Visibility.PUBLIC,
        default="false",
        description=(
            "Injects the GTM container on the storefront. Off means no GTM "
            "script is served at all — not merely an empty container."
        ),
    ),
    IntegrationField(
        key="analytics.gtm_container_id",
        group="gtm",
        label="Container ID",
        type="text",
        visibility=Visibility.PUBLIC,
        placeholder="GTM-XXXXXXX",
        description=(
            "Public by design — it is embedded verbatim in the page source of "
            "every site that loads GTM."
        ),
        pattern=r"GTM-[A-Z0-9]{4,10}",
        pattern_hint="GTM- followed by 4–10 uppercase letters or digits.",
    ),
    # ---- GA4 ----------------------------------------------------------------
    IntegrationField(
        key="analytics.ga4_enabled",
        group="ga4",
        label="Send events to GA4",
        type="bool",
        visibility=Visibility.PUBLIC,
        default="false",
        description="Master switch for both the browser tag and the server outbox.",
    ),
    IntegrationField(
        key="analytics.ga4_measurement_id",
        group="ga4",
        label="Measurement ID",
        type="text",
        visibility=Visibility.PUBLIC,
        placeholder="G-XXXXXXXXXX",
        description="Public — shipped to every browser as part of the gtag config.",
        pattern=r"G-[A-Z0-9]{4,12}",
        pattern_hint="G- followed by 4–12 uppercase letters or digits.",
    ),
    IntegrationField(
        key="analytics.ga4_purchase_delivery",
        group="ga4",
        label="Purchase events are sent from",
        type="select",
        visibility=Visibility.PUBLIC,
        default="server",
        options=(
            ("browser", "Browser only — loses 10–40% to ad blockers"),
            ("server", "Server only — authoritative, from the order-paid transaction"),
            ("both", "Both — GA4 dedupes on transaction_id"),
        ),
        description=(
            "The browser tag reads this to decide whether it must also fire "
            "purchase. It is public because the tag cannot work without it."
        ),
    ),
    IntegrationField(
        key="analytics.ga4_api_secret",
        group="ga4",
        label="Measurement Protocol API secret",
        type="password",
        visibility=Visibility.SECRET,
        description=(
            "A write credential: anyone holding it can inject events into your "
            "GA4 property from anywhere. Stored encrypted, never returned, and "
            "never sent to a browser. Create it under Admin → Data Streams → "
            "Measurement Protocol API secrets."
        ),
        pattern=r"[A-Za-z0-9_\-]{16,64}",
        pattern_hint="16–64 characters: letters, digits, underscore or hyphen.",
    ),
    IntegrationField(
        key="analytics.ga4_property_id",
        group="ga4",
        label="Data API property ID",
        type="text",
        visibility=Visibility.ADMIN,
        placeholder="123456789",
        description=(
            "Numeric GA4 property id used by the Data API for reconciliation "
            "pulls. Not a credential, but the browser has no use for it, so it "
            "stays admin-only."
        ),
        pattern=r"\d{6,15}",
        pattern_hint="6–15 digits.",
    ),
    IntegrationField(
        key="analytics.ga4_data_api_credentials",
        group="ga4",
        label="Data API service-account JSON",
        type="textarea",
        visibility=Visibility.SECRET,
        description=(
            "The whole service-account key file. It grants read access to the "
            "property and contains a private key — stored encrypted, redacted "
            "in every response, never sent to a browser."
        ),
    ),
    IntegrationField(
        key="analytics.property_environment",
        group="ga4",
        label="These IDs belong to",
        type="select",
        visibility=Visibility.PUBLIC,
        default="",
        options=(
            ("", "Not declared"),
            ("production", "The production property"),
            ("staging", "A staging / test property"),
            ("development", "A local development property"),
        ),
        description=(
            "Declares which property the IDs above point at, so the health "
            "probe can catch a production deployment reporting into a staging "
            "property (live revenue missing) or the reverse (staging traffic "
            "polluting real reports). Nothing else can detect this: both "
            "configurations look perfectly healthy from every other signal."
        ),
    ),
    # ---- Microsoft Clarity ---------------------------------------------------
    IntegrationField(
        key="analytics.clarity_enabled",
        group="clarity",
        label="Load Microsoft Clarity",
        type="bool",
        visibility=Visibility.PUBLIC,
        default="false",
        description="Session recording and heatmaps. Records real customer sessions.",
    ),
    IntegrationField(
        key="analytics.clarity_project_id",
        group="clarity",
        label="Project ID",
        type="text",
        visibility=Visibility.PUBLIC,
        placeholder="abcdefghij",
        description="Public — embedded in the Clarity snippet on every page.",
        pattern=r"[a-z0-9]{6,15}",
        pattern_hint="6–15 lowercase letters or digits.",
    ),
    # ---- Consent -------------------------------------------------------------
    IntegrationField(
        key="analytics.consent_mode",
        group="consent",
        label="Consent Mode",
        type="select",
        visibility=Visibility.PUBLIC,
        default="basic",
        options=(
            ("off", "Off — tags fire without a consent signal"),
            ("basic", "Basic — tags are withheld until consent is granted"),
            ("advanced", "Advanced — cookieless pings sent before consent"),
        ),
        description=(
            "Read by the browser before any tag loads, so it must be public. "
            "Also decides whether a purchase with denied consent is recorded "
            "as SUPPRESSED_NO_CONSENT rather than transmitted."
        ),
    ),
    IntegrationField(
        key="analytics.consent_default_analytics_storage",
        group="consent",
        label="Default analytics_storage",
        type="select",
        visibility=Visibility.PUBLIC,
        default="denied",
        options=_GRANT,
        description="The state assumed before the visitor answers the banner.",
    ),
    IntegrationField(
        key="analytics.consent_default_ad_storage",
        group="consent",
        label="Default ad_storage",
        type="select",
        visibility=Visibility.PUBLIC,
        default="denied",
        options=_GRANT,
        description="Advertising storage default. Independent of analytics storage.",
    ),
    IntegrationField(
        key="analytics.consent_wait_for_update_ms",
        group="consent",
        label="wait_for_update (ms)",
        type="number",
        visibility=Visibility.PUBLIC,
        default="500",
        placeholder="500",
        description=(
            "How long tags wait for a consent decision before acting on the "
            "defaults. Too short and a granted consent arrives after the "
            "page_view has already been suppressed."
        ),
        pattern=r"\d{1,5}",
        pattern_hint="0–99999 milliseconds.",
    ),
)

FIELD_BY_KEY: dict[str, IntegrationField] = {f.key: f for f in FIELDS}

#: Exactly the keys that are safe to add to `_PUBLIC_KEYS` in
#: `endpoints/settings.py`. Derived, not hand-maintained, so a new PUBLIC field
#: cannot be forgotten and — more importantly — a new SECRET one can never be
#: added to the anonymous endpoint by copying a stale list.
PUBLIC_KEYS: tuple[str, ...] = tuple(
    f.key for f in FIELDS if f.visibility == Visibility.PUBLIC
)
SECRET_KEYS: frozenset[str] = frozenset(
    f.key for f in FIELDS if f.visibility == Visibility.SECRET
)

GROUPS: tuple[IntegrationGroup, ...] = (
    IntegrationGroup(
        id="gtm",
        label="Google Tag Manager",
        provider="gtm",
        blurb="The container that loads every other tag on the storefront.",
        fields=tuple(f for f in FIELDS if f.group == "gtm"),
    ),
    IntegrationGroup(
        id="ga4",
        label="Google Analytics 4",
        provider="ga4",
        blurb="Measurement id for the browser, API secret for server-side purchases.",
        fields=tuple(f for f in FIELDS if f.group == "ga4"),
    ),
    IntegrationGroup(
        id="clarity",
        label="Microsoft Clarity",
        provider="clarity",
        blurb="Session recordings and heatmaps.",
        fields=tuple(f for f in FIELDS if f.group == "clarity"),
    ),
    IntegrationGroup(
        id="consent",
        label="Consent",
        provider=None,
        blurb="What tags may do before the visitor has answered the banner.",
        fields=tuple(f for f in FIELDS if f.group == "consent"),
    ),
)


def describe_settings() -> dict[str, Any]:
    """The schema the admin UI renders from — no values, no DB access.

    `public_keys` and `secret_keys` are part of the contract on purpose: the
    admin screen labels each field with where its value can travel, and the
    lead wiring `_PUBLIC_KEYS` in `endpoints/settings.py` copies the first list
    rather than re-deriving it by eye.
    """
    return {
        "groups": [
            {
                "id": g.id,
                "label": g.label,
                "blurb": g.blurb,
                "provider": g.provider,
                "fields": [
                    f.as_dict(value="", has_value=False) for f in g.fields
                ],
            }
            for g in GROUPS
        ],
        "providers": list(PROVIDERS),
        "public_keys": list(PUBLIC_KEYS),
        "secret_keys": sorted(SECRET_KEYS),
        "category": CATEGORY,
    }


# ===========================================================================
# Row storage
# ===========================================================================
def _rows(db: Session) -> dict[str, SystemSetting]:
    """Every integration row that currently exists, keyed by settings key.

    Looked up by key rather than by category so this keeps working if the boot
    seeder later files the same keys under a different category.
    """
    return {
        row.key: row
        for row in db.execute(
            select(SystemSetting).where(SystemSetting.key.in_(list(FIELD_BY_KEY)))
        )
        .scalars()
        .all()
    }


def ensure_rows(db: Session) -> int:
    """Create any missing `system_settings` row for this schema. Idempotent.

    Returns the number of rows created.

    Why this exists: `SettingsService.set_many` silently *ignores* keys with no
    row, so without this every save would 200 and write nothing. The boot
    seeder (`settings_seed.py`) is owned elsewhere and may not carry these keys
    yet; this makes the screen work regardless, and becomes a no-op the moment
    it does. It never touches an existing row's value — an admin-configured
    credential must survive every restart, reseed and redeploy untouched.

    Same write-on-read shape as `timebox.active_generation`. A concurrent
    request racing to insert the same key hits the UNIQUE index; that is caught
    and re-read rather than surfaced, because losing the race means the row now
    exists, which is the outcome the caller wanted.
    """
    existing = _rows(db)
    missing = [f for f in FIELDS if f.key not in existing]
    if not missing:
        return 0
    for f in missing:
        db.add(
            SystemSetting(
                key=f.key,
                value=(
                    "" if f.visibility == Visibility.SECRET else f.default
                ),
                category=CATEGORY,
                description=f.description[:255],
                is_secret=f.is_secret,
            )
        )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.info("analytics integration rows raced; re-reading")
        return 0
    return len(missing)


def read_secret(db: Session, key: str) -> str:
    """Decrypt one SECRET setting. Server-side callers only.

    Returns "" when unset OR when the stored ciphertext cannot be decrypted —
    the caller is about to *use* the credential, and an undecryptable one is
    functionally absent. `secret_state` is the function that tells the two
    apart, and the health probe surfaces the difference so a `SECRET_KEY`
    rotation does not present as "nobody ever configured this".
    """
    field = FIELD_BY_KEY.get(key)
    if field is None or not field.is_secret:
        raise BadIntegrationRequest(f"{key!r} is not a secret integration setting.")
    row = _rows(db).get(key)
    stored = (row.value if row else "") or ""
    if not stored:
        return ""
    try:
        return decrypt_secret(stored)
    except ValueError:
        logger.warning("could not decrypt %s — SECRET_KEY may have rotated", key)
        return ""


def secret_state(db: Session, key: str) -> str:
    """``unset`` | ``set`` | ``undecryptable`` — never the value itself."""
    field = FIELD_BY_KEY.get(key)
    if field is None or not field.is_secret:
        raise BadIntegrationRequest(f"{key!r} is not a secret integration setting.")
    row = _rows(db).get(key)
    stored = (row.value if row else "") or ""
    if not stored:
        return "unset"
    try:
        decrypt_secret(stored)
    except ValueError:
        return "undecryptable"
    return "set"


def current_values(db: Session) -> dict[str, str]:
    """Current values, **with every secret already redacted**.

    This is the only read the API layer uses to build a response, and it is
    incapable of returning a decrypted credential: SECRET keys resolve to
    ``***`` when set and ``""`` when not. Making the redaction a property of the
    accessor rather than of each call site is the point — a future endpoint
    that forgets to redact cannot exist, because there is nothing to forget.
    """
    rows = _rows(db)
    out: dict[str, str] = {}
    for f in FIELDS:
        row = rows.get(f.key)
        stored = (row.value if row else "") or ""
        if f.is_secret:
            out[f.key] = REDACTED if stored else ""
            continue
        # Empty means "unset", so the shipped default kicks back in — same
        # contract `SettingsService.set_many` documents for an empty write.
        # Without this a row seeded blank by a boot seeder would leave the
        # consent mode reading as "" and the browser with no instruction.
        out[f.key] = stored or f.default
    return out


def stored_flags(db: Session) -> dict[str, bool]:
    """`{key: whether a value is actually stored}` — one query, no values.

    The admin UI needs to distinguish "secret is set, masked" from "secret has
    never been entered", and it cannot infer that from `***` alone.
    """
    rows = _rows(db)
    return {f.key: bool((rows[f.key].value if f.key in rows else "") or "") for f in FIELDS}


# ===========================================================================
# Validation
# ===========================================================================
def _normalise_bool(raw: str) -> str:
    lowered = raw.strip().lower()
    if lowered in _YES:
        return "true"
    if lowered in _NO:
        return "false"
    raise InvalidIntegrationValue(
        f"{raw!r} is not a boolean.", details={"expected": ["true", "false"]}
    )


def _validate(field: IntegrationField, value: str) -> str:
    """Return the value to store, or raise 422. Never silently coerces."""
    if field.type == "bool":
        return _normalise_bool(value)

    if value == "":
        # Empty is always "unset" — it lets the env/default fall back through
        # and is how an admin removes a credential.
        return ""

    if field.type == "select":
        allowed = [v for v, _lbl in field.options]
        if value not in allowed:
            raise InvalidIntegrationValue(
                f"{value!r} is not a valid choice for {field.label}.",
                details={"allowed": allowed},
            )
        return value

    if field.key == "analytics.ga4_data_api_credentials":
        # A service-account key that is not valid JSON cannot possibly work, and
        # the failure would otherwise surface days later as a silent
        # reconciliation gap rather than at the moment of the paste.
        try:
            parsed = json.loads(value)
        except ValueError as exc:
            raise InvalidIntegrationValue(
                f"Service-account credentials must be valid JSON: {exc}"
            ) from exc
        if not isinstance(parsed, dict) or "private_key" not in parsed:
            raise InvalidIntegrationValue(
                "That JSON is not a service-account key — it has no "
                "`private_key` field. Download the key file from Google Cloud "
                "IAM → Service Accounts → Keys."
            )
        return value

    if field.pattern and not re.fullmatch(field.pattern, value):
        raise InvalidIntegrationValue(
            f"{field.label} is not in the expected format.",
            details={"expected": field.pattern_hint or field.pattern},
        )
    return value


# ===========================================================================
# Writes
# ===========================================================================
def apply_updates(
    db: Session,
    updates: dict[str, str | None],
    *,
    actor: User | None,
    actor_ip: str | None,
) -> list[str]:
    """Validate, encrypt, persist and **audit** a batch of integration changes.

    Returns the keys that actually changed.

    Delegates the write itself to `SettingsService.set_many` rather than
    touching rows directly, so the Redis settings cache is invalidated by the
    same tested code path every other settings write uses. A hand-rolled
    `row.value = x` here would leave `get_raw` serving the previous container id
    for up to 60 seconds — long enough for an operator to conclude the save did
    not work and press it again.

    Audit is not optional. Every one of these keys changes what a third party
    is told about the store's customers; "who turned Clarity on?" must have an
    answer. Secret values are recorded as ``***`` on both sides — the audit
    trail records *that* a credential changed, never what it became.
    """
    ensure_rows(db)

    prepared: dict[str, str] = {}
    for key, raw in updates.items():
        field = FIELD_BY_KEY.get(key)
        if field is None:
            raise BadIntegrationRequest(
                f"Unknown analytics integration setting {key!r}.",
                details={"known_keys": sorted(FIELD_BY_KEY)},
            )
        text = "" if raw is None else str(raw)

        if field.is_secret:
            if text == REDACTED:
                # The admin never focused the field — the UI round-trips the
                # mask. Keep the stored ciphertext.
                continue
            plain = text.strip()
            if plain == "":
                prepared[key] = ""  # explicit clear
                continue
            _validate(field, plain)
            if read_secret(db, key) == plain:
                # Fernet is randomised, so re-encrypting an unchanged secret
                # would look like a change and emit a misleading audit row
                # every time the form is saved.
                continue
            prepared[key] = encrypt_secret(plain)
            continue

        value = text if field.type == "textarea" else text.strip()
        prepared[key] = _validate(field, value)

    changed = SettingsService(db).set_many(prepared, actor=actor)

    audit = AuditService(db)
    for row, before, after in changed:
        redact = row.is_secret or row.key in SECRET_KEYS
        audit.record(
            actor=actor,
            actor_ip=actor_ip,
            action="analytics.integrations.update",
            target_type="setting",
            target_id=row.id,
            target_label=row.key,
            summary=f"Updated analytics integration setting {row.key}",
            extra={
                "before": REDACTED if redact else before,
                "after": REDACTED if redact else after,
            },
        )
    db.commit()
    return [row.key for row, _b, _a in changed]


# ===========================================================================
# Connection tests
# ===========================================================================
def _result(
    provider: str,
    *,
    status_: str,
    verified: bool,
    message: str,
    checked: str,
    not_checked: Iterable[str] = (),
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One connection-test result.

    `verified` is the field the UI must gate its green tick on, and it is
    **only** true when a remote system actually confirmed something. `checked`
    and `not_checked` exist so the operator can see the size of the claim
    instead of inferring it from a colour.
    """
    return {
        "provider": provider,
        "status": status_,
        "verified": verified,
        "message": message,
        "checked": checked,
        "not_checked": list(not_checked),
        "details": details or {},
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


#: What no server-side check can establish for a browser tag, whatever the
#: provider. Reused by GTM and Clarity so the two lists cannot drift apart.
_BROWSER_TAG_UNCHECKABLE = (
    "that the container/project actually exists",
    "that it is published to a live environment",
    "that it loads on your storefront without being blocked",
    "that it is wired to the events in tracking_events.py",
)


def _format_only(provider: str, field_key: str, value: str, note: str) -> dict[str, Any]:
    """Shared shape for the two providers with no server-side validation API."""
    field = FIELD_BY_KEY[field_key]
    if not value:
        return _result(
            provider,
            status_="not_configured",
            verified=False,
            message=f"No {field.label.lower()} has been saved yet.",
            checked="Nothing — there is no value to check.",
            not_checked=_BROWSER_TAG_UNCHECKABLE,
        )
    if field.pattern and not re.fullmatch(field.pattern, value):
        return _result(
            provider,
            status_="invalid_format",
            verified=False,
            message=(
                f"{field.label} {value!r} is not in the expected format "
                f"({field.pattern_hint}). The tag will not load."
            ),
            checked="The saved id against the provider's documented format.",
            not_checked=_BROWSER_TAG_UNCHECKABLE,
            details={"id": value, "expected": field.pattern_hint},
        )
    return _result(
        provider,
        status_="cannot_verify_server_side",
        verified=False,
        message=note,
        checked=(
            "The saved id is well-formed. That is the only thing this server "
            "is able to establish."
        ),
        not_checked=_BROWSER_TAG_UNCHECKABLE,
        details={"id": value, "format_ok": True},
    )


def _test_gtm(values: dict[str, str]) -> dict[str, Any]:
    return _format_only(
        "gtm",
        "analytics.gtm_container_id",
        values.get("analytics.gtm_container_id", ""),
        note=(
            "Google Tag Manager publishes no server-side validation API. "
            "https://www.googletagmanager.com/gtm.js?id=… answers 200 for any "
            "well-formed container id, including ones that were never created, "
            "so fetching it would produce a green tick that means nothing. "
            "Confirm this container in GTM Preview mode against the live "
            "storefront, or with the Tag Assistant extension — those are the "
            "only checks that actually observe the container loading."
        ),
    )


def _test_clarity(values: dict[str, str]) -> dict[str, Any]:
    return _format_only(
        "clarity",
        "analytics.clarity_project_id",
        values.get("analytics.clarity_project_id", ""),
        note=(
            "Microsoft Clarity publishes no server-side validation API and no "
            "endpoint that distinguishes a real project id from a well-formed "
            "invented one. Confirm it by loading the storefront and watching "
            "the session appear in the Clarity dashboard — a recording is the "
            "only proof that the snippet is running."
        ),
    )


def _test_ga4(db: Session, values: dict[str, str]) -> dict[str, Any]:
    """The one genuinely verifiable provider — via the MP **debug** endpoint."""
    measurement_id = values.get("analytics.ga4_measurement_id", "")
    api_secret = read_secret(db, "analytics.ga4_api_secret")

    missing = [
        name
        for name, present in (
            ("measurement id", bool(measurement_id)),
            ("Measurement Protocol API secret", bool(api_secret)),
        )
        if not present
    ]
    if missing:
        state = secret_state(db, "analytics.ga4_api_secret")
        extra = (
            " The stored API secret exists but could not be decrypted — "
            "SECRET_KEY has probably been rotated. Re-enter it."
            if state == "undecryptable"
            else ""
        )
        return _result(
            "ga4",
            status_="not_configured",
            verified=False,
            message=f"Missing: {', '.join(missing)}.{extra}",
            checked="Nothing — the credentials needed to check are not saved.",
            not_checked=("anything about the GA4 property",),
            details={"missing": missing, "api_secret_state": state},
        )

    # A minimal, deliberately boring event. The params are run through the
    # shared PII guard even though nothing here is personal — the guard sits at
    # the delivery boundary so it catches every caller, and a connection test is
    # a caller. It is applied to the params rather than the whole Measurement
    # Protocol envelope on purpose: the envelope's own `events[].name` key is
    # GA4 vocabulary and would trip the deliberately-broad "name" substring rule.
    event_params: dict[str, Any] = {
        "engagement_time_msec": "1",
        "event_schema_version": SCHEMA_VERSION,
    }
    assert_no_pii(event_params)
    payload = {
        "client_id": "555555555.1234567890",
        "non_personalized_ads": True,
        "events": [{"name": "wellvia_connection_test", "params": event_params}],
    }

    try:
        with httpx.Client(timeout=GA4_TEST_TIMEOUT_SEC) as client:
            resp = client.post(
                GA4_DEBUG_URL,
                params={
                    "measurement_id": measurement_id,
                    "api_secret": api_secret,
                },
                json=payload,
            )
    except httpx.HTTPError as exc:
        return _result(
            "ga4",
            status_="unreachable",
            verified=False,
            message=(
                "Could not reach Google's Measurement Protocol validation "
                f"endpoint: {exc}. This says nothing about your credentials — "
                "only that the check could not run."
            ),
            checked="Attempted a POST to the MP validation endpoint.",
            not_checked=("the measurement id", "the API secret"),
            details={"error": str(exc)},
        )

    if resp.status_code != 200:
        return _result(
            "ga4",
            status_="unreachable",
            verified=False,
            message=(
                f"The MP validation endpoint answered HTTP {resp.status_code}. "
                "The check could not be completed."
            ),
            checked="POSTed a validation event to the MP debug endpoint.",
            not_checked=("the measurement id", "the API secret"),
            details={"http_status": resp.status_code, "body": resp.text[:400]},
        )

    try:
        messages = resp.json().get("validationMessages") or []
    except ValueError:
        messages = []

    if messages:
        return _result(
            "ga4",
            status_="rejected",
            verified=False,
            message=(
                "Google rejected the validation event: "
                + "; ".join(
                    str(m.get("description") or m) for m in messages
                )
            ),
            checked=(
                "POSTed a validation event to Google's MP debug endpoint with "
                "the saved measurement id and API secret."
            ),
            not_checked=("the browser tag", "GTM", "consent gating"),
            details={"validation_messages": messages},
        )

    return _result(
        "ga4",
        status_="verified",
        verified=True,
        message=(
            "Google accepted the measurement id and API secret and raised no "
            "validation messages. Server-side purchase delivery can reach this "
            "property."
        ),
        checked=(
            "POSTed a validation event to Google's MP debug endpoint "
            "(/debug/mp/collect — it validates and reports, it does not ingest, "
            "so no event was written to your property)."
        ),
        not_checked=(
            "that the browser tag fires — the production /mp/collect endpoint "
            "answers 204 to everything, so only GA4 Realtime proves ingestion",
            "the GTM container",
            "consent gating on the storefront",
        ),
        details={"measurement_id": measurement_id, "validation_messages": []},
    )


def test_connection(db: Session, provider: str) -> dict[str, Any]:
    """Test one provider, and report exactly how much the answer is worth.

    Only ``ga4`` can return ``verified: True``; see the module docstring for
    why GTM and Clarity cannot, and why inventing a check for them would be
    worse than admitting it.
    """
    key = (provider or "").strip().lower()
    if key not in PROVIDERS:
        raise BadIntegrationRequest(
            f"Unknown analytics provider {provider!r}.",
            details={"providers": list(PROVIDERS)},
        )
    ensure_rows(db)
    values = current_values(db)  # secrets already redacted; GA4 reads its own
    if key == "ga4":
        return _test_ga4(db, values)
    if key == "gtm":
        return _test_gtm(values)
    return _test_clarity(values)


# ===========================================================================
# Tracking health
# ===========================================================================
def _is_true(value: str | None) -> bool:
    return (value or "").strip().lower() in _YES


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _provider_state(
    values: dict[str, str],
    *,
    provider: str,
    enabled_key: str,
    id_key: str,
) -> dict[str, Any]:
    field = FIELD_BY_KEY[id_key]
    ident = (values.get(id_key) or "").strip()
    format_ok = bool(ident) and (
        field.pattern is None or bool(re.fullmatch(field.pattern, ident))
    )
    return {
        "provider": provider,
        "enabled": _is_true(values.get(enabled_key)),
        "configured": bool(ident),
        # Public ids by definition — echoing one here leaks nothing that the
        # page source does not already publish.
        "id": ident or None,
        "id_format_ok": format_ok,
    }


def _environment_report(values: dict[str, str]) -> dict[str, Any]:
    """Detect a deployment reporting into the wrong GA4 property.

    Both directions are real incidents and neither shows up anywhere else:

    * production traffic into a staging property — the real reports are simply
      missing the revenue, and the staging property looks suspiciously healthy;
    * staging traffic into the production property — conversions that no
      customer generated inflate ROAS, and the business spends against them.

    Every other signal is green in both cases. The tag loads, events send, the
    dashboard draws a line. Only the declared property environment can catch it.
    """
    app_env = (env_settings.ENVIRONMENT or "").strip().lower()
    declared = (values.get("analytics.property_environment") or "").strip().lower()
    host = urlparse(env_settings.FRONTEND_URL or "").hostname or ""

    app_is_production = app_env in PRODUCTION_ENVIRONMENTS
    declared_is_production = declared in PRODUCTION_ENVIRONMENTS

    code: str | None = None
    message: str | None = None
    if declared:
        if app_is_production and not declared_is_production:
            code = "production_host_non_production_property"
            message = (
                f"This deployment runs as ENVIRONMENT={app_env!r}"
                + (f" and serves {host}" if host else "")
                + f", but the configured analytics IDs are declared as "
                f"{declared!r}. Live traffic is being reported into a "
                "non-production property — the production reports are missing "
                "all of it, and nothing else will ever tell you."
            )
        elif not app_is_production and declared_is_production:
            code = "non_production_host_production_property"
            message = (
                f"This deployment runs as ENVIRONMENT={app_env!r}"
                + (f" ({host})" if host else "")
                + " but is configured with the production analytics IDs. "
                "Test traffic and test orders are being written into the real "
                "property, inflating conversions the business spends against."
            )

    return {
        "app_environment": app_env or "unknown",
        "declared_property_environment": declared or None,
        "frontend_host": host or None,
        "mismatch": code is not None,
        "mismatch_code": code,
        "mismatch_message": message,
    }


def _outbox_report(db: Session) -> dict[str, Any]:
    """Server-side GA4 delivery: counts by status, last success, last error.

    `SUPPRESSED_NO_CONSENT` is reported as a first-class count, not folded into
    "not delivered". A suppressed row is a *correct* outcome — the purchase was
    recorded internally and deliberately not transmitted — whereas a FAILED row
    is attribution actively being lost. Collapsing them would either raise a
    false alarm for every consent-declining customer, or hide a real outage
    behind a plausible-looking number.
    """
    counts = {
        OutboxStatus.PENDING: 0,
        OutboxStatus.DELIVERED: 0,
        OutboxStatus.FAILED: 0,
        OutboxStatus.SUPPRESSED_NO_CONSENT: 0,
    }
    for row_status, count in db.execute(
        select(AnalyticsEventOutbox.status, func.count(AnalyticsEventOutbox.id))
        .group_by(AnalyticsEventOutbox.status)
    ).all():
        counts[str(row_status)] = int(count or 0)
    counts["total"] = sum(v for k, v in counts.items() if k != "total")

    last_delivered = (
        db.execute(
            select(AnalyticsEventOutbox)
            .where(
                AnalyticsEventOutbox.status == OutboxStatus.DELIVERED,
                AnalyticsEventOutbox.delivered_at.is_not(None),
            )
            .order_by(
                AnalyticsEventOutbox.delivered_at.desc(),
                AnalyticsEventOutbox.id.desc(),
            )
            .limit(1)
        )
        .scalars()
        .first()
    )

    last_failure = (
        db.execute(
            select(AnalyticsEventOutbox)
            .where(AnalyticsEventOutbox.last_error.is_not(None))
            .order_by(
                AnalyticsEventOutbox.created_at.desc(),
                AnalyticsEventOutbox.id.desc(),
            )
            .limit(1)
        )
        .scalars()
        .first()
    )

    oldest_pending = db.execute(
        select(func.min(AnalyticsEventOutbox.occurred_at)).where(
            AnalyticsEventOutbox.status == OutboxStatus.PENDING
        )
    ).scalar()

    return {
        "counts": counts,
        "last_delivered_at": _iso(
            last_delivered.delivered_at if last_delivered else None
        ),
        "last_delivered_transaction_id": (
            last_delivered.transaction_id if last_delivered else None
        ),
        "oldest_pending_occurred_at": _iso(oldest_pending),
        "last_error": last_failure.last_error if last_failure else None,
        "last_error_transaction_id": (
            last_failure.transaction_id if last_failure else None
        ),
        "last_error_at": _iso(last_failure.created_at if last_failure else None),
    }


def _rollup_report(db: Session) -> dict[str, Any]:
    """Per-job data watermarks — "these numbers include everything through X".

    Deliberately the watermark and not "the job last ran at 04:00": a pipeline
    that ticks green and advances no watermark serves last week's numbers as if
    they were today's, and the run timestamp reports that as health.
    """
    watermarks = {
        job: value
        for job, value in db.execute(
            select(
                AnalyticsSyncRun.job, func.max(AnalyticsSyncRun.watermark_date)
            ).group_by(AnalyticsSyncRun.job)
        ).all()
    }
    successes = {
        job: value
        for job, value in db.execute(
            select(AnalyticsSyncRun.job, func.max(AnalyticsSyncRun.finished_at))
            .where(AnalyticsSyncRun.status == SyncStatus.SUCCESS)
            .group_by(AnalyticsSyncRun.job)
        ).all()
    }

    jobs = [
        {
            "job": job,
            "watermark_date": (
                watermarks[job].isoformat() if watermarks.get(job) else None
            ),
            "last_success_at": _iso(successes.get(job)),
        }
        for job in sorted(set(watermarks) | set(successes))
    ]
    dated = [w for w in watermarks.values() if w is not None]
    return {
        "jobs": jobs,
        "oldest_watermark_date": min(dated).isoformat() if dated else None,
        "newest_watermark_date": max(dated).isoformat() if dated else None,
    }


def _warnings(
    *,
    values: dict[str, str],
    providers: dict[str, dict[str, Any]],
    environment: dict[str, Any],
    outbox: dict[str, Any],
    ga4_secret_state: str,
) -> list[dict[str, str]]:
    """Conditions where every other signal looks fine and the data is wrong."""
    out: list[dict[str, str]] = []

    def warn(code: str, severity: str, message: str) -> None:
        out.append({"code": code, "severity": severity, "message": message})

    if environment["mismatch"]:
        warn("environment_mismatch", "critical", environment["mismatch_message"])

    for name, state in providers.items():
        if state["enabled"] and not state["configured"]:
            warn(
                f"{name}_enabled_without_id",
                "critical",
                f"{name.upper()} is switched on but no id is saved, so nothing "
                "is being collected. The storefront looks instrumented and is "
                "not.",
            )
        elif state["configured"] and not state["id_format_ok"]:
            warn(
                f"{name}_id_malformed",
                "critical",
                f"The saved {name.upper()} id is not in the documented format; "
                "the tag will fail to load.",
            )

    delivery = values.get("analytics.ga4_purchase_delivery", "")
    if delivery in ("server", "both"):
        if ga4_secret_state == "unset":
            warn(
                "ga4_server_delivery_without_secret",
                "critical",
                "Purchases are configured to be sent from the server, but no "
                "Measurement Protocol API secret is saved. Every outbox row "
                "will accumulate undelivered.",
            )
        elif ga4_secret_state == "undecryptable":
            warn(
                "ga4_secret_undecryptable",
                "critical",
                "The stored Measurement Protocol API secret cannot be "
                "decrypted — SECRET_KEY has almost certainly been rotated. "
                "Server-side purchase delivery is broken until it is re-entered.",
            )

    counts = outbox["counts"]
    if counts.get(OutboxStatus.FAILED, 0) > 0:
        warn(
            "outbox_failed_rows",
            "critical",
            f"{counts[OutboxStatus.FAILED]} purchase event(s) exhausted their "
            "retries and will never reach GA4. Attribution for those orders is "
            "permanently lost unless they are replayed.",
        )
    if counts.get(OutboxStatus.PENDING, 0) > 0 and outbox["last_delivered_at"] is None:
        warn(
            "outbox_never_delivered",
            "warning",
            "There are pending server-side events and nothing has ever been "
            "delivered. The delivery worker has probably never run.",
        )

    suppressed = counts.get(OutboxStatus.SUPPRESSED_NO_CONSENT, 0)
    if suppressed and values.get("analytics.consent_mode") == "off":
        warn(
            "suppressed_events_with_consent_off",
            "warning",
            f"{suppressed} event(s) were suppressed for lack of consent, but "
            "Consent Mode is set to 'off'. One of the two is stale — the "
            "suppressions were recorded at purchase time under a different "
            "setting.",
        )

    return out


def tracking_health(db: Session) -> dict[str, Any]:
    """The Analytics Control Centre probe.

    Answers "can I trust what the dashboard is telling me?" rather than "did
    the process exit 0?". Every field here exists because its absence is
    indistinguishable from health: a disabled tag, a staging measurement id, a
    stalled outbox and a frozen watermark all present as a quiet trading day.

    Contains no secret values — only `ga4_api_secret_state`, which is one of
    ``unset`` / ``set`` / ``undecryptable``.
    """
    ensure_rows(db)
    values = current_values(db)

    providers = {
        "gtm": _provider_state(
            values,
            provider="gtm",
            enabled_key="analytics.gtm_enabled",
            id_key="analytics.gtm_container_id",
        ),
        "ga4": _provider_state(
            values,
            provider="ga4",
            enabled_key="analytics.ga4_enabled",
            id_key="analytics.ga4_measurement_id",
        ),
        "clarity": _provider_state(
            values,
            provider="clarity",
            enabled_key="analytics.clarity_enabled",
            id_key="analytics.clarity_project_id",
        ),
    }

    ga4_secret = secret_state(db, "analytics.ga4_api_secret")
    providers["ga4"]["api_secret_state"] = ga4_secret
    providers["ga4"]["purchase_delivery"] = values.get(
        "analytics.ga4_purchase_delivery", ""
    )
    providers["ga4"]["server_delivery_ready"] = bool(
        providers["ga4"]["configured"] and ga4_secret == "set"
    )
    providers["ga4"]["data_api_credentials_state"] = secret_state(
        db, "analytics.ga4_data_api_credentials"
    )

    environment = _environment_report(values)
    outbox = _outbox_report(db)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": environment,
        "providers": providers,
        "consent": {
            "mode": values.get("analytics.consent_mode", ""),
            "default_analytics_storage": values.get(
                "analytics.consent_default_analytics_storage", ""
            ),
            "default_ad_storage": values.get(
                "analytics.consent_default_ad_storage", ""
            ),
            "wait_for_update_ms": values.get(
                "analytics.consent_wait_for_update_ms", ""
            ),
        },
        "outbox": outbox,
        "rollups": _rollup_report(db),
        "warnings": _warnings(
            values=values,
            providers=providers,
            environment=environment,
            outbox=outbox,
            ga4_secret_state=ga4_secret,
        ),
    }
