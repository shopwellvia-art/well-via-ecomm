from sqlalchemy import case, func, select

from app.models.product import Category, Product
from app.repositories.base import LIKE_ESCAPE, BaseRepository, like_pattern


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

    def search(self, q: str, *, limit: int = 6) -> list[tuple[Category, int]]:
        """Categories whose name matches `q`, each with its product count.

        Returns `(category, product_count)` pairs. The count comes from a
        correlated subquery rather than a GROUP BY join so that a category with
        zero products is still returned — global search shows it as a
        destination, and dropping it would make a real category look
        nonexistent to whoever just typed its name.

        Ordered exact name → name-prefix → the rest, alphabetically inside each
        bucket, so typing "sleep" puts the Sleep category above "Sleep & Calm".
        """
        pattern = like_pattern(q)
        prefix = like_pattern(q, prefix_only=True)
        product_count = (
            select(func.count(Product.id))
            .where(Product.category_id == Category.id)
            .correlate(Category)
            .scalar_subquery()
        )
        stmt = (
            select(Category, product_count.label("product_count"))
            .where(Category.name.ilike(pattern, escape=LIKE_ESCAPE))
            .order_by(
                case(
                    (func.lower(Category.name) == q.strip().lower(), 0),
                    (Category.name.ilike(prefix, escape=LIKE_ESCAPE), 1),
                    else_=2,
                ),
                Category.name,
            )
            .limit(limit)
        )
        return [(row[0], row[1] or 0) for row in self.db.execute(stmt).all()]

    def has_children(self, category_id: int) -> bool:
        return (
            self.db.execute(
                select(Category.id).where(Category.parent_id == category_id).limit(1)
            ).scalar_one_or_none()
            is not None
        )
