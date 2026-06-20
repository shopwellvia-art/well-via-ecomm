"""Diagnose the SMTP email backend the app uses.

Mirrors app/email/smtp.py exactly: it resolves the SMTP config the same way
(the `system_settings` table first, env vars as fallback) and runs the same
smtplib flow (SMTP -> STARTTLS -> AUTH login -> send), but in *layers* so a
failure tells you precisely where it broke instead of one opaque traceback:

  1. Resolve + print the config (password masked).
  2. Raw TCP reach to host:port  -> separates "host/port/firewall" from "auth".
  3. SMTP greeting + EHLO        -> the server is really an SMTP server.
  4. STARTTLS (if enabled)       -> TLS upgrade works.
  5. AUTH login                  -> THE credential check ("is it correct?").
  6. (optional) send a real test email with --send <recipient>.

You can test credentials WITHOUT saving them first by passing flags:

    docker compose run --rm --no-deps backend python scripts/test_smtp_connection.py \
        --host smtp.gmail.com --port 587 --user you@gmail.com --password 'app-pass' \
        --from no-reply@example.com

Or test what's actually configured in the DB / env (no flags):

    docker compose run --rm --no-deps backend python scripts/test_smtp_connection.py

Add --send someone@example.com to actually deliver a test message.

Exits 0 on success, 1 on any failure.
"""
from __future__ import annotations

import argparse
import os
import smtplib
import socket
import ssl
import sys
from email.message import EmailMessage

# Make the `app` package importable no matter how this script is launched.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Windows consoles default to cp1252, which can't encode the glyphs we print.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

CONNECT_TIMEOUT = 15  # seconds — matches send_smtp's smtplib timeout


def _resolve_from_app() -> dict:
    """Resolve SMTP config the same way app/email/smtp.py does: settings table
    first (when a DB session is available), env vars as the fallback. Returns
    an empty-ish dict if the app can't be imported (then flags must supply it)."""
    try:
        from app.core.config import settings as env_settings  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        print(f"  (could not import app settings: {exc})")
        return {}

    svc = None
    db = None
    try:
        from app.db.session import SessionLocal  # noqa: PLC0415
        from app.services.settings_service import SettingsService  # noqa: PLC0415

        db = SessionLocal()
        svc = SettingsService(db)
    except Exception as exc:  # noqa: BLE001
        print(f"  (no DB settings, using env only: {exc})")

    def _setting(key, env_value, default=None):
        if svc is None:
            return env_value if env_value not in ("", None) else default
        v = svc.get_raw(key)
        if v is None or v == "":
            return env_value if env_value not in ("", None) else default
        return v

    cfg = {
        "backend": _setting("email.backend", env_settings.EMAIL_BACKEND or "console"),
        "host": _setting("smtp.host", env_settings.SMTP_HOST, default=""),
        "port": int(_setting("smtp.port", str(env_settings.SMTP_PORT), default="587")),
        "user": _setting("smtp.user", env_settings.SMTP_USER, default=""),
        "password": _setting("smtp.password", env_settings.SMTP_PASSWORD, default=""),
        "use_tls": str(
            _setting("smtp.use_tls", "true" if env_settings.SMTP_USE_TLS else "false")
        ).strip().lower() in ("1", "true", "yes", "on"),
        "sender": _setting("email.from", env_settings.EMAIL_FROM, default=""),
    }
    if db is not None:
        db.close()
    return cfg


def _resolve(args) -> dict:
    cfg = _resolve_from_app()
    # CLI flags override whatever the app resolved (test creds before saving).
    if args.host is not None:
        cfg["host"] = args.host
    if args.port is not None:
        cfg["port"] = args.port
    if args.user is not None:
        cfg["user"] = args.user
    if args.password is not None:
        cfg["password"] = args.password
    if args.sender is not None:
        cfg["sender"] = args.sender
    if args.no_tls:
        cfg["use_tls"] = False
    cfg.setdefault("backend", "smtp")
    cfg.setdefault("use_tls", True)
    return cfg


def _fail(msg: str, cause: str | None = None) -> int:
    print(f"      FAIL: {msg}")
    print("-" * 64)
    if cause:
        print(f"CAUSE: {cause}")
    print("RESULT: ❌  SMTP check FAILED.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Test the SMTP email backend.")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--user")
    parser.add_argument("--password")
    parser.add_argument("--from", dest="sender")
    parser.add_argument("--no-tls", action="store_true", help="disable STARTTLS")
    parser.add_argument("--send", metavar="RECIPIENT", help="actually send a test email")
    args = parser.parse_args()

    cfg = _resolve(args)
    host, port = cfg["host"], cfg["port"]
    user, password = cfg["user"], cfg["password"]
    use_tls, sender = cfg["use_tls"], cfg["sender"]

    print("=" * 64)
    print("SMTP connection diagnostic")
    print("=" * 64)
    print(f"backend : {cfg.get('backend')}")
    print(f"host    : {host}")
    print(f"port    : {port}")
    print(f"user    : {user}")
    print(f"password: {'*' * len(password) if password else '(empty)'}")
    print(f"use_tls : {use_tls}")
    print(f"from    : {sender}")
    print("-" * 64)

    if not host:
        return _fail(
            "no SMTP host configured.",
            "smtp.host is empty in both the settings table and env. Save the SMTP "
            "settings (Admin -> Settings -> Backend) or pass --host.",
        )

    # ---- Step 1: raw TCP reachability ----
    print(f"[1/4] TCP connect to {host}:{port} (timeout {CONNECT_TIMEOUT}s)...")
    try:
        with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT):
            print("      OK - port is open.")
    except socket.gaierror as exc:
        return _fail(f"DNS could not resolve host: {exc}", "smtp.host is wrong/unresolvable.")
    except (TimeoutError, OSError) as exc:
        return _fail(
            f"could not reach the port: {exc}",
            "wrong host/port, firewall, or your network/egress blocks outbound SMTP.",
        )

    # ---- Steps 2-4: greeting, STARTTLS, AUTH ----
    try:
        print("[2/4] SMTP greeting + EHLO...")
        server = smtplib.SMTP(host, port, timeout=CONNECT_TIMEOUT)
        server.ehlo()
        print("      OK - server greeted.")

        if use_tls:
            print("[3/4] STARTTLS upgrade...")
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            print("      OK - TLS established.")
        else:
            print("[3/4] STARTTLS skipped (use_tls=false).")

        print("[4/4] AUTH login (the real credential check)...")
        if not user:
            print("      SKIPPED - no username set (server may allow anonymous relay).")
        else:
            server.login(user, password)
            print(f"      OK - authenticated as {user}. ✅ Credentials are CORRECT.")

        # ---- optional real send ----
        if args.send:
            print(f"[+]   Sending a test email to {args.send}...")
            msg = EmailMessage()
            msg["From"] = sender or user
            msg["To"] = args.send
            msg["Subject"] = "SMTP test from shopwellvia"
            msg.set_content(
                "This is a test message from scripts/test_smtp_connection.py. "
                "If you received it, SMTP delivery works end-to-end."
            )
            server.send_message(msg)
            print("      OK - message accepted by the server for delivery.")

        server.quit()
        print("-" * 64)
        print("RESULT: ✅  SMTP check PASSED.")
        return 0

    except smtplib.SMTPAuthenticationError as exc:
        code = getattr(exc, "smtp_code", "?")
        detail = getattr(exc, "smtp_error", b"")
        detail = detail.decode() if isinstance(detail, bytes) else str(detail)
        cause = f"server rejected the username/password (code {code}): {detail}"
        if "gmail" in host.lower() or "google" in detail.lower():
            cause += (
                "\n       Gmail does NOT accept your normal account password over SMTP. "
                "You must:\n"
                "         1. Enable 2-Step Verification on the Google account, then\n"
                "         2. create a 16-character App Password "
                "(myaccount.google.com -> Security -> App passwords)\n"
                "         3. use THAT as smtp.password (spaces optional)."
            )
        return _fail("authentication rejected.", cause)
    except smtplib.SMTPSenderRefused as exc:
        return _fail(
            f"sender address refused: {exc}",
            f"the server won't let you send as '{sender}'. With Gmail the From must be "
            "the authenticated account or a verified 'Send mail as' alias.",
        )
    except smtplib.SMTPServerDisconnected as exc:
        return _fail(f"server disconnected: {exc}", "often a TLS mismatch (try toggling use_tls) or a wrong port.")
    except smtplib.SMTPException as exc:
        return _fail(f"{type(exc).__name__}: {exc}")
    except ssl.SSLError as exc:
        return _fail(f"TLS error: {exc}", "STARTTLS failed - check port (587 for STARTTLS, 465 is implicit TLS).")
    except (TimeoutError, OSError) as exc:
        return _fail(f"connection error: {exc}")


if __name__ == "__main__":
    sys.exit(main())
