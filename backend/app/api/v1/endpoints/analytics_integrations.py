"""Analytics integration settings API — GTM, GA4, Clarity and Consent.

Routes (mounted under the `/analytics` prefix by `api/v1/router.py`)
===================================================================
============================= ============================================
GET  /integrations            schema + current values, secrets REDACTED
PUT  /integrations            update, validated and audited
POST /integrations/{p}/test   connection test for one provider
GET  /integrations/health     the Analytics Control Centre probe
============================= ============================================

Permissions
-----------
The three configuration routes require ``analytics.integrations.manage``.
Health requires only ``analytics.control_centre.view`` — reading whether
tracking is working is an operational, look-don't-touch activity, and gating it
behind the credential-editing permission would mean the people who watch the
dashboards cannot see that it has stopped updating.

Secrets
-------
`GET /integrations` is built exclusively from
`integrations.current_values()`, which redacts SECRET keys to ``***`` before
the API layer ever sees them — the response cannot contain a credential
because this module never holds one. The same values are absent from
`/settings/public` (that endpoint serves a hard-coded allowlist, and
`integrations.PUBLIC_KEYS` is the only part of this schema that belongs in it),
and they are stored Fernet-encrypted rather than in cleartext.

`PUT` accepts ``***`` for a secret field and treats it as "unchanged", exactly
as `AdminSettingsPage` and `SettingsService` already do, so a form round-trip
cannot overwrite a credential with its own mask.

Rate limits
-----------
Reads are polled by a dashboard, so their bucket is generous. Writes touch
credentials. `POST .../test` makes an outbound HTTPS call to a third party from
inside a request worker and gets the tightest bucket of the three — it is the
one route here whose cost is not bounded by this machine.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db, rate_limit_by_ip, require_permission
from app.core.rate_limit import get_client_ip
from app.models.user import User
from app.services.analytics import integrations as integrations_service

router = APIRouter()

#: Editing what a third party is told about the store's customers, and holding
#: the credential that lets the server write into the GA4 property.
INTEGRATIONS_MANAGE_PERMISSION = "analytics.integrations.manage"

#: Read-only visibility into whether tracking is working. Deliberately separate
#: — see the module docstring.
CONTROL_CENTRE_VIEW_PERMISSION = "analytics.control_centre.view"

_READ_RATE_LIMIT = Depends(
    rate_limit_by_ip(scope="analytics.integrations.read.ip", limit=300, window_sec=300)
)
_WRITE_RATE_LIMIT = Depends(
    rate_limit_by_ip(scope="analytics.integrations.write.ip", limit=60, window_sec=300)
)
#: A connection test leaves the building. Ten per five minutes is more than an
#: operator needs and far less than a useful outbound-request amplifier.
_TEST_RATE_LIMIT = Depends(
    rate_limit_by_ip(scope="analytics.integrations.test.ip", limit=10, window_sec=300)
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class SelectOption(BaseModel):
    value: str
    label: str


class IntegrationFieldOut(BaseModel):
    """One editable setting, with everything the admin form needs.

    `value` is already redacted for secrets — see `is_secret` / `has_value`.
    `visibility` is surfaced so the UI can label a field as browser-visible
    rather than leaving the operator to guess which of these are public.
    """

    key: str
    group: str
    label: str
    type: str
    visibility: str
    is_secret: bool
    description: str
    placeholder: str
    options: list[SelectOption]
    pattern: str | None
    pattern_hint: str
    default: str
    value: str
    has_value: bool


class IntegrationGroupOut(BaseModel):
    id: str
    label: str
    blurb: str
    #: Provider key for `POST /integrations/{provider}/test`; None when the
    #: group has nothing testable (Consent).
    provider: str | None
    fields: list[IntegrationFieldOut]


class IntegrationsResponse(BaseModel):
    groups: list[IntegrationGroupOut]
    providers: list[str]
    #: The subset of these keys that is safe to expose anonymously. Returned so
    #: the screen can say so per field rather than relying on a convention.
    public_keys: list[str]
    secret_keys: list[str]


class IntegrationsUpdateRequest(BaseModel):
    """`{"updates": {key: value}}` — same shape as `PATCH /settings`.

    A secret whose value is `***` is skipped: the admin did not touch it.
    """

    updates: dict[str, str | None] = Field(default_factory=dict)


class IntegrationsUpdateResponse(BaseModel):
    #: Keys that actually changed. Empty means the submission was a no-op, which
    #: is worth saying rather than implying a save happened.
    changed: list[str]
    integrations: IntegrationsResponse


class ConnectionTestResponse(BaseModel):
    """The result of one connection test.

    `verified` is the only field a green tick may be gated on, and it is true
    **only** when a remote system confirmed something. `status` is one of
    ``verified`` | ``rejected`` | ``not_configured`` | ``invalid_format`` |
    ``unreachable`` | ``cannot_verify_server_side``.
    """

    provider: str
    status: str
    verified: bool
    message: str
    #: Plain-language statement of what was actually done.
    checked: str
    #: What this result cannot prove, however green it looks.
    not_checked: list[str]
    details: dict[str, Any]
    checked_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _build(db: Session) -> IntegrationsResponse:
    """Schema + current values, secrets already masked.

    Values come from `current_values`, which redacts before returning, so there
    is no path through this function that could emit a credential even if a
    field were mis-flagged downstream.
    """
    integrations_service.ensure_rows(db)
    values = integrations_service.current_values(db)
    stored = integrations_service.stored_flags(db)
    schema = integrations_service.describe_settings()

    groups: list[IntegrationGroupOut] = []
    for group in schema["groups"]:
        fields = []
        for field in group["fields"]:
            key = field["key"]
            fields.append(
                IntegrationFieldOut(
                    **{
                        **field,
                        "value": values.get(key, ""),
                        "has_value": stored.get(key, False),
                    }
                )
            )
        groups.append(
            IntegrationGroupOut(
                id=group["id"],
                label=group["label"],
                blurb=group["blurb"],
                provider=group["provider"],
                fields=fields,
            )
        )

    return IntegrationsResponse(
        groups=groups,
        providers=schema["providers"],
        public_keys=schema["public_keys"],
        secret_keys=schema["secret_keys"],
    )


# ---------------------------------------------------------------------------
# GET /integrations
# ---------------------------------------------------------------------------
@router.get(
    "/integrations",
    response_model=IntegrationsResponse,
    dependencies=[
        Depends(require_permission(INTEGRATIONS_MANAGE_PERMISSION)),
        _READ_RATE_LIMIT,
    ],
)
def get_integrations(db: Session = Depends(get_db)):
    """The schema the admin screen renders, plus the current values.

    Secrets come back as `***` when set and `""` when not — never the stored
    ciphertext and never the plaintext. `has_value` is what lets the UI tell
    "configured, hidden" from "never entered" without revealing either.
    """
    return _build(db)


# ---------------------------------------------------------------------------
# PUT /integrations
# ---------------------------------------------------------------------------
@router.put(
    "/integrations",
    response_model=IntegrationsUpdateResponse,
    dependencies=[_WRITE_RATE_LIMIT],
)
def update_integrations(
    payload: IntegrationsUpdateRequest,
    request: Request,
    actor: User = Depends(require_permission(INTEGRATIONS_MANAGE_PERMISSION)),
    db: Session = Depends(get_db),
):
    """Update integration settings. Validated, encrypted where secret, audited.

    An unknown key is a 400 and a malformed value is a 422 — neither is
    normalised into something plausible. A measurement id quietly coerced into
    a valid-looking string produces a tag that loads, reports nothing, and
    gives nobody a reason to look at it again.

    One *combination* is also refused, with a 422 whose error code is
    ``ga4_server_delivery_without_secret``: putting `ga4_purchase_delivery`
    in a server mode (``server`` / ``both``) while no Measurement Protocol
    api_secret is stored and none arrives in the same request — and, mirrored,
    clearing the secret while delivery is a server mode. That state queues
    every purchase in the outbox with nothing able to send it. The rejection
    names the two fixes (save the secret, or choose browser-only) and an
    already-inconsistent deployment can always save its way out, because the
    guard fires only on requests that touch one of the two fields. See
    `integrations._guard_deliverable`.

    Every accepted change writes an `AuditEvent`. Secret values are recorded as
    `***` on both sides: the trail records that a credential changed, never
    what it became.
    """
    changed = integrations_service.apply_updates(
        db,
        payload.updates,
        actor=actor,
        actor_ip=get_client_ip(request),
    )
    return IntegrationsUpdateResponse(changed=changed, integrations=_build(db))


# ---------------------------------------------------------------------------
# POST /integrations/{provider}/test
# ---------------------------------------------------------------------------
@router.post(
    "/integrations/{provider}/test",
    response_model=ConnectionTestResponse,
    dependencies=[
        Depends(require_permission(INTEGRATIONS_MANAGE_PERMISSION)),
        _TEST_RATE_LIMIT,
    ],
)
def test_integration(
    provider: Literal["gtm", "ga4", "clarity"],
    db: Session = Depends(get_db),
):
    """Test one provider, and report how much the answer is actually worth.

    **This always returns 200**, including when the provider cannot be checked
    or the check failed. The outcome is data, not a transport error: an
    operator needs to read `status`, `checked` and `not_checked`, and a 4xx
    would collapse "your API secret is wrong" and "Google is unreachable" into
    the same red box.

    Only `ga4` can ever come back `verified: true`. GTM and Clarity return
    `cannot_verify_server_side` because neither publishes an endpoint that
    distinguishes a real container/project from a well-formed invented one —
    see `services/analytics/integrations.py`.
    """
    return ConnectionTestResponse(**integrations_service.test_connection(db, provider))


# ---------------------------------------------------------------------------
# GET /integrations/health
# ---------------------------------------------------------------------------
@router.get(
    "/integrations/health",
    dependencies=[
        Depends(require_permission(CONTROL_CENTRE_VIEW_PERMISSION)),
        _READ_RATE_LIMIT,
    ],
)
def integrations_health(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Tracking Health: is the instrumentation working, and can we prove it?

    Per-provider configured/enabled state, consent mode, outbox counts by
    status (including `suppressed_no_consent`), last successful delivery, last
    error, environment mismatch and rollup watermarks — plus a `warnings` list
    of the conditions under which every other signal still looks green.

    Requires `analytics.control_centre.view`, not the manage permission: the
    people who watch dashboards must be able to see that one has stopped
    updating without also holding the GA4 write credential.

    Returned as a plain mapping rather than a fixed response model because the
    provider map grows with the schema in `integrations.py`; a duplicated
    Pydantic mirror would silently drop new fields instead of surfacing them.
    Nothing here is a secret value — credential state is reported as
    `unset` / `set` / `undecryptable`.
    """
    return integrations_service.tracking_health(db)
