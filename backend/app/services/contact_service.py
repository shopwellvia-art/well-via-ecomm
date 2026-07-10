from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.contact_message import ContactMessage, NewsletterSubscriber
from app.schemas.contact import ContactMessageCreate


class ContactService:
    """Public contact-form + newsletter intake.

    v1 is persist-only: messages land in `contact_messages` for support staff
    to work from the DB/admin. Email notification can be layered on later
    without touching the endpoint contract.
    """

    def __init__(self, db: Session):
        self.db = db

    def submit_message(self, data: ContactMessageCreate) -> ContactMessage:
        msg = ContactMessage(**data.model_dump())
        self.db.add(msg)
        self.db.commit()
        self.db.refresh(msg)
        return msg

    def subscribe_newsletter(self, email: str) -> None:
        """Idempotent: re-subscribing an existing address is a silent no-op."""
        normalized = email.strip().lower()
        exists = (
            self.db.query(NewsletterSubscriber)
            .filter(NewsletterSubscriber.email == normalized)
            .first()
        )
        if exists:
            return
        self.db.add(NewsletterSubscriber(email=normalized))
        try:
            self.db.commit()
        except IntegrityError:
            # Lost a race with a concurrent subscribe — same outcome, no-op.
            self.db.rollback()
