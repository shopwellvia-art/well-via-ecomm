"""Password-reset delivery failures must not change the endpoint's reply.

`/auth/forgot-password` deliberately answers identically for a registered and
an unregistered address so it cannot be used to enumerate accounts. That
guarantee is only as strong as the weakest failure path: an unknown address
returns early and never sends, so if a send failure escapes for a KNOWN
address, the two cases stop matching and the endpoint becomes an oracle.

Regression: a dead SMTP credential (Gmail 535 BadCredentials) escaped as a 500
because the delivery call in the template-fallback handler sat outside any
try/except. The first send raised, the handler logged it as a render failure,
re-sent, and the second raise had nothing left to catch it.
"""
import uuid

import pytest

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.user import User
from app.services.auth_service import AuthService


@pytest.fixture
def reset_user():
    """A throwaway active user, removed afterwards."""
    db = SessionLocal()
    email = f"reset-probe-{uuid.uuid4().hex[:10]}@example.com"
    user = User(
        email=email,
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.commit()
    yield email
    db.query(User).filter(User.email == email).delete()
    db.commit()
    db.close()


def _boom(*args, **kwargs):
    """Stand-in for smtplib raising 535 during login."""
    raise RuntimeError("(535, b'5.7.8 Username and Password not accepted')")


def test_delivery_failure_does_not_escape(monkeypatch, reset_user):
    """A send that raises must not propagate out of the service."""
    monkeypatch.setattr("app.services.auth_service.send_email", _boom)
    db = SessionLocal()
    try:
        # Before the fix this raised RuntimeError out of the except handler.
        AuthService(db).request_password_reset(reset_user)
    finally:
        db.close()


def test_delivery_failure_still_stores_the_otp(monkeypatch, reset_user):
    """The OTP is persisted before the send, so a dead mailer does not also
    destroy the reset flow for an operator who fixes SMTP and retries."""
    monkeypatch.setattr("app.services.auth_service.send_email", _boom)
    from app.db.redis import get_redis
    from app.services.auth_service import _otp_key

    redis_client = get_redis()
    redis_client.delete(_otp_key(reset_user))
    db = SessionLocal()
    try:
        AuthService(db).request_password_reset(reset_user)
    finally:
        db.close()
    assert redis_client.get(_otp_key(reset_user)) is not None


def test_known_and_unknown_addresses_answer_identically(
    monkeypatch, client, reset_user
):
    """The enumeration guarantee itself: same status, same body, both ways."""
    monkeypatch.setattr("app.services.auth_service.send_email", _boom)

    known = client.post("/api/v1/auth/forgot-password", json={"email": reset_user})
    unknown = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": f"absent-{uuid.uuid4().hex[:10]}@example.com"},
    )

    assert known.status_code == unknown.status_code, (
        f"registered address answered {known.status_code}, unregistered "
        f"answered {unknown.status_code} — that difference IS the oracle"
    )
    assert known.json() == unknown.json()
