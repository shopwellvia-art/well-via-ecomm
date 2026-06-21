import smtplib
from email.message import EmailMessage

from sqlalchemy.orm import Session

from app.core.config import settings as env_settings


def _setting(svc, key, env_value, default=None):
    if svc is None:
        return env_value if env_value not in ("", None) else default
    v = svc.get_raw(key)
    if v is None or v == "":
        return env_value if env_value not in ("", None) else default
    return v


def send_smtp(
    *, to: str, subject: str, body: str, db: Session | None = None, html: str | None = None
) -> None:
    """SMTP email backend. Reads creds from the runtime settings table when a
    session is available; falls back to env vars otherwise so the system
    keeps working with no DB rows yet.

    When ``html`` is supplied the message is sent as multipart/alternative
    (plain ``body`` as the text/plain part + ``html`` as the preferred
    text/html alternative). Clients that understand HTML will render the HTML;
    older clients fall back to the plain text.
    """
    svc = None
    if db is not None:
        from app.services.settings_service import SettingsService

        svc = SettingsService(db)

    host = _setting(svc, "smtp.host", env_settings.SMTP_HOST)
    port = int(_setting(svc, "smtp.port", str(env_settings.SMTP_PORT), default="587"))
    user = _setting(svc, "smtp.user", env_settings.SMTP_USER, default="")
    password = _setting(svc, "smtp.password", env_settings.SMTP_PASSWORD, default="")
    use_tls_raw = _setting(
        svc, "smtp.use_tls", "true" if env_settings.SMTP_USE_TLS else "false"
    )
    use_tls = str(use_tls_raw).strip().lower() in ("1", "true", "yes", "on")
    sender = _setting(svc, "email.from", env_settings.EMAIL_FROM)

    if not host:
        # No host configured — defensive. The console backend is the right
        # fallback rather than throwing into the request flow.
        from app.email.console import send_console

        send_console(to=to, subject=subject, body=body, html=html)
        return

    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject

    # Set the plain-text body first; then add HTML as an alternative part.
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")

    with smtplib.SMTP(host, port, timeout=15) as server:
        if use_tls:
            server.starttls()
        if user:
            server.login(user, password)
        server.send_message(message)
