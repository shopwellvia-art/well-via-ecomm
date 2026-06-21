from sqlalchemy import select

from app.models.email_template import EmailTemplate
from app.repositories.base import BaseRepository


class EmailTemplateRepository(BaseRepository[EmailTemplate]):
    model = EmailTemplate

    def get_by_key(self, key: str) -> EmailTemplate | None:
        return self.db.execute(
            select(EmailTemplate).where(EmailTemplate.key == key)
        ).scalar_one_or_none()

    def list_all(self) -> list[EmailTemplate]:
        """Return all templates ordered by group_name then key — stable admin list."""
        return list(
            self.db.execute(
                select(EmailTemplate).order_by(
                    EmailTemplate.group_name, EmailTemplate.key
                )
            )
            .scalars()
            .all()
        )
