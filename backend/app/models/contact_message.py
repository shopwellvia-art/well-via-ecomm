from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IDMixin, TimestampMixin


class ContactMessage(Base, IDMixin, TimestampMixin):
    """Customer enquiry submitted from the public Contact page.

    Stored for follow-up by support staff; `status` is a free-form workflow
    tag ("new" → "replied"/"closed") rather than an enum so ops can adopt
    their own vocabulary without a migration.
    """

    __tablename__ = "contact_messages"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    phone: Mapped[str | None] = mapped_column(String(20))
    subject: Mapped[str | None] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="new", server_default="new"
    )


class NewsletterSubscriber(Base, IDMixin, TimestampMixin):
    """Footer newsletter opt-in. Subscribing twice is a silent no-op."""

    __tablename__ = "newsletter_subscribers"

    email: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
