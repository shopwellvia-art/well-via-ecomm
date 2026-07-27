from sqlalchemy import select

from app.models.product import Category
from app.repositories.base import BaseRepository


class CategoryRepository(BaseRepository[Category]):
    model = Category

    def get_by_slug(self, slug: str) -> Category | None:
        return self.db.execute(
            select(Category).where(Category.slug == slug)
        ).scalar_one_or_none()

    def get_by_name(self, name: str) -> Category | None:
        return self.db.execute(
            select(Category).where(Category.name == name)
        ).scalar_one_or_none()

    def list_all(self) -> list[Category]:
        return list(
            self.db.execute(select(Category).order_by(Category.name)).scalars().all()
        )

    def has_children(self, category_id: int) -> bool:
        return (
            self.db.execute(
                select(Category.id).where(Category.parent_id == category_id).limit(1)
            ).scalar_one_or_none()
            is not None
        )
