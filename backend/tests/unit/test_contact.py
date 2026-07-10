"""Hermetic tests for the contact-form + newsletter intake service.

All in-process against in-memory SQLite — no live MySQL required:

    docker compose exec backend pytest tests/unit/test_contact.py -v
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.contact_message import ContactMessage, NewsletterSubscriber
from app.schemas.contact import ContactMessageCreate
from app.services.contact_service import ContactService


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_submit_message_persists(db: Session):
    msg = ContactService(db).submit_message(
        ContactMessageCreate(
            name="Priya S",
            email="priya@example.com",
            phone="+911234567890",
            subject="Order query",
            message="Where is my order? It has been a week.",
        )
    )
    assert msg.id is not None
    assert msg.status == "new"
    stored = db.execute(select(ContactMessage)).scalars().all()
    assert len(stored) == 1
    assert stored[0].email == "priya@example.com"


def test_newsletter_subscribe_idempotent_and_normalized(db: Session):
    svc = ContactService(db)
    svc.subscribe_newsletter("User@Example.COM ")
    svc.subscribe_newsletter("user@example.com")
    rows = db.execute(select(NewsletterSubscriber)).scalars().all()
    assert len(rows) == 1
    assert rows[0].email == "user@example.com"


def test_contact_schema_rejects_short_message():
    with pytest.raises(ValueError):
        ContactMessageCreate(name="A", email="a@b.co", message="hi")
