"""Runtime settings + test-send endpoints. Permission: settings.manage."""
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission
from app.core.exceptions import AppError, ForbiddenError
from app.core.rate_limit import get_client_ip
from app.email import send_email
from app.models.user import User
from app.schemas.settings import (
    SettingItem,
    SettingsListResponse,
    SettingsUpdateRequest,
    TestEmailRequest,
    TestSmsRequest,
)
from app.services.audit_service import AuditService
from app.services.settings_service import SettingsService

router = APIRouter()

_SECRET_KEYS_FOR_AUDIT_LOG = {"smtp.password", "twilio.auth_token"}


def _analytics_public_keys() -> frozenset[str]:
    """Analytics keys safe for anonymous readers, DERIVED from the field schema.

    Imported lazily and defensively: this endpoint must keep serving the
    storefront's other public settings even if the analytics module fails to
    import, and a hard import here would turn an analytics bug into a
    site-wide outage on a page every visitor loads.
    """
    try:
        from app.services.analytics.integrations import PUBLIC_KEYS

        return frozenset(PUBLIC_KEYS)
    except Exception:  # noqa: BLE001 - never break /settings/public
        return frozenset()


def _analytics_managed_keys() -> frozenset[str]:
    """EVERY analytics integration key, whatever its visibility.

    All of them — public ids included, not just the credentials — are refused by
    the generic update path. A measurement id is not secret, but it is still
    validated against a per-field pattern, audited under its own action, and
    paired with an enable flag by the integrations service. Letting half the
    group through here would mean an admin could set a malformed container id
    that silently never loads, with nothing in the audit trail naming what
    changed it.

    Fails OPEN on import error: if the analytics module cannot load, this returns
    empty and the generic endpoint behaves exactly as it did before analytics
    existed. Failing closed would let an unrelated analytics bug lock an operator
    out of SMTP settings during an incident.
    """
    try:
        from app.services.analytics.integrations import FIELD_BY_KEY

        return frozenset(FIELD_BY_KEY)
    except Exception:  # noqa: BLE001 - never break ordinary settings management
        return frozenset()

# Settings keys safe to expose to anonymous customers. Anything not in this
# allowlist is admin-only — the public endpoint is a curated, hard-coded
# view onto the system_settings table.
_PUBLIC_KEYS: frozenset[str] = frozenset({
    # Free-shipping threshold drives the customer-facing nudge banner.
    "shipping.free_threshold",
    # Trust badges on the login page.
    "login.trust_badge_1_label", "login.trust_badge_1_icon",
    "login.trust_badge_2_label", "login.trust_badge_2_icon",
    "login.trust_badge_3_label", "login.trust_badge_3_icon",
    "login.trust_badge_4_label", "login.trust_badge_4_icon",
    # Whether the COD checkout flow needs to gate on an OTP — drives
    # the conditional OTP modal on CheckoutPage.
    "cod.require_otp",

    # ---- Analytics tracking -------------------------------------------------
    # Every value here is a PUBLIC identifier that ends up in the page source
    # the moment the tag loads — a GTM container id, a GA4 measurement id and a
    # Clarity project id are all visible to anyone who opens devtools. Exposing
    # them here is not a leak; it is how the browser learns which container to
    # load, and the storefront must be able to read them anonymously because a
    # logged-out visitor is exactly who tracking is for.
    #
    # NOTHING SECRET GOES IN THIS LIST. The GA4 Measurement Protocol api_secret
    # and any Data API service-account credentials are Fernet-encrypted,
    # backend-only, and must never appear here — they authorise WRITING events
    # as this property, which a browser has no business being able to do.
    # The exact keys are NOT written out here. They are DERIVED from each field's
    # declared visibility in `analytics/integrations.py`, so a newly added secret
    # cannot become anonymously readable because someone copied a stale list.
    # `analytics.ga4_purchase_delivery` in particular must be public: the
    # duplicate-purchase suppression happens in the BROWSER, at the push
    # boundary, and a client that cannot read the setting would send a second
    # purchase and inflate GA4 revenue in a way that looks like growth.
    *_analytics_public_keys(),
})


@router.get("/public")
def public_settings(db: Session = Depends(get_db)):
    """Curated, anonymous view of `system_settings`. Returns a flat dict of
    `{key: value}` for the allowlisted keys only — never any secrets."""
    svc = SettingsService(db)
    return {key: svc.get_raw(key) for key in _PUBLIC_KEYS}


@router.get(
    "",
    response_model=SettingsListResponse,
    dependencies=[Depends(require_permission("settings.manage"))],
)
def list_settings(db: Session = Depends(get_db)):
    svc = SettingsService(db)
    rows = svc.list_all()
    return SettingsListResponse(
        items=[
            SettingItem(
                key=r.key,
                value=svc.view_value(r),
                category=r.category,
                description=r.description,
                is_secret=r.is_secret,
            )
            for r in rows
        ]
    )


@router.patch(
    "",
    response_model=SettingsListResponse,
)
def update_settings(
    payload: SettingsUpdateRequest,
    request: Request,
    actor: User = Depends(require_permission("settings.manage")),
    db: Session = Depends(get_db),
):
    # Analytics integration keys are OFF LIMITS to this endpoint, even for a
    # holder of settings.manage.
    #
    # They are not ordinary settings: the GA4 credentials are Fernet-encrypted
    # by `analytics/integrations.py`, validated against a per-field schema, and
    # audited under their own action. Writing them through the generic path
    # would store a plaintext credential where ciphertext is expected, skip
    # validation entirely, and log the change as a plain `settings.update` —
    # so a rotation of a live analytics credential would be indistinguishable
    # from someone editing the SMTP port.
    #
    # It also collapses two permission tiers into one: `analytics.integrations.
    # manage` exists precisely so that managing store settings does not imply
    # managing tracking credentials.
    rejected = sorted(set(payload.updates) & _analytics_managed_keys())
    if rejected:
        raise ForbiddenError(
            "Analytics integration settings cannot be changed here. Use "
            "Admin → Analytics → Integrations, which encrypts credentials, "
            "validates each field and audits the change under its own action.",
            {"rejected_keys": rejected},
        )

    svc = SettingsService(db)
    changed = svc.set_many(payload.updates, actor=actor)
    if changed:
        audit = AuditService(db)
        ip = get_client_ip(request)
        for row, before, after in changed:
            # Don't log secret values in the audit trail — record only that
            # they changed, never the cleartext.
            redact = row.is_secret or row.key in _SECRET_KEYS_FOR_AUDIT_LOG
            audit.record(
                actor=actor,
                actor_ip=ip,
                action="settings.update",
                target_type="setting",
                target_id=row.id,
                target_label=row.key,
                summary=f"Updated setting {row.key}",
                extra={
                    "before": "***" if redact else before,
                    "after": "***" if redact else after,
                },
            )
    db.commit()
    rows = svc.list_all()
    return SettingsListResponse(
        items=[
            SettingItem(
                key=r.key,
                value=svc.view_value(r),
                category=r.category,
                description=r.description,
                is_secret=r.is_secret,
            )
            for r in rows
        ]
    )


@router.post(
    "/test-email",
    status_code=status.HTTP_200_OK,
)
def send_test_email(
    payload: TestEmailRequest,
    actor: User = Depends(require_permission("settings.manage")),
    db: Session = Depends(get_db),
):
    try:
        send_email(
            to=payload.to,
            subject="Wellvia — test email",
            body=(
                "If you're reading this, your SMTP settings are working.\n\n"
                f"Sent by: {actor.email}\n"
            ),
            db=db,
        )
    except Exception as exc:  # surface delivery errors to the admin
        raise AppError(f"Send failed: {exc}") from exc
    return {"detail": f"Test email sent to {payload.to}."}


@router.post(
    "/test-sms",
    status_code=status.HTTP_200_OK,
)
def send_test_sms(
    payload: TestSmsRequest,
    actor: User = Depends(require_permission("settings.manage")),
    db: Session = Depends(get_db),
):
    from app.sms import send_sms
    from app.sms.twilio import SmsConfigError

    try:
        send_sms(to=payload.to, body=payload.body, db=db)
    except SmsConfigError as exc:
        raise AppError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise AppError(f"SMS send failed: {exc}") from exc
    return {"detail": f"Test SMS sent to {payload.to}."}


@router.post(
    "/test-storage",
    status_code=status.HTTP_200_OK,
)
def test_storage_connection(
    actor: User = Depends(require_permission("settings.manage")),
    db: Session = Depends(get_db),
):
    """Verify the saved S3 settings with a head_bucket call.

    Mirrors test-email / test-sms: 200 with {detail} on success, AppError
    otherwise (the admin UI renders the message in a banner). Reads the
    effective config (DB settings + env fallback) via resolve_config, so the
    real secret value is used — never the masked view_value.
    """
    from app.storage import resolve_config

    cfg = resolve_config(db)
    if cfg["backend"] != "s3":
        raise AppError(
            "Storage backend is not set to S3. Switch it to S3 and save before testing."
        )
    if not (cfg.get("bucket") or "").strip():
        raise AppError("No S3 bucket configured.")

    from app.storage.s3 import S3Storage  # boto3 imported lazily

    store = S3Storage(cfg)
    try:
        store.client.head_bucket(Bucket=store.bucket)
    except Exception as exc:  # noqa: BLE001 — surface any boto3 / network error
        raise AppError(f"S3 test failed: {exc}") from exc
    return {"detail": f"S3 connection OK (bucket {store.bucket})."}
