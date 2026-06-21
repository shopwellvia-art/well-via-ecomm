"""Email entry point. Backend selection is now driven by the runtime
`system_settings` table (key `email.backend`) with the env-only `EMAIL_BACKEND`
as a fallback for the first run before an admin touches settings."""
from sqlalchemy.orm import Session

from app.core.config import settings as env_settings
from app.email.console import send_console


def send_email(
    *, to: str, subject: str, body: str, db: Session | None = None, html: str | None = None
) -> None:
    """Send an email via the configured backend.

    When ``html`` is provided the email is sent as multipart/alternative with
    ``body`` (plain text) as the fallback and ``html`` as the preferred part.
    When ``html`` is omitted the email is sent as plain text only.

    Pass ``db`` when the caller already has a session — we'd rather read the
    settings table than open a new connection. When omitted (e.g. background
    tasks without a session), we fall back to env-only configuration.
    """
    backend = _resolve_backend(db)
    if backend == "smtp":
        from app.email.smtp import send_smtp  # lazy import — only needed in prod

        send_smtp(to=to, subject=subject, body=body, db=db, html=html)
    else:
        send_console(to=to, subject=subject, body=body, html=html)


def _resolve_backend(db: Session | None) -> str:
    """settings table first, env second. We never want the system to silently
    "stop sending email" because the settings table got cleared."""
    if db is not None:
        from app.services.settings_service import SettingsService

        v = SettingsService(db).get_raw("email.backend")
        if v:
            return v.strip().lower()
    return (env_settings.EMAIL_BACKEND or "console").strip().lower()
