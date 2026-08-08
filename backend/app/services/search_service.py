"""Global storefront search — one query, every kind of destination.

Products and categories are searched here. Company/help pages (Shipping,
Returns, Privacy, …) are NOT: those are frontend routes with no database row
behind them, so the registry of what exists lives with the router that owns them
(`frontend/src/features/search/pages.js`) and is matched client-side. Putting a
hardcoded list of React paths in the backend would mean a redirect or a renamed
route silently returning dead links from the API.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.product import Category
from app.repositories.category_repository import CategoryRepository
from app.repositories.product_repository import ProductRepository
from app.schemas.search import (
    GlobalSearchResults,
    SearchCategoryHit,
    SearchProductHit,
)

# Below this length a query matches so much of the catalogue that the results
# are noise — "a" would return most rows ranked by nothing meaningful. The
# frontend also gates on it, but the endpoint cannot rely on that: it is public
# and reachable directly.
MIN_QUERY_LENGTH = 2


class SearchService:
    def __init__(self, db: Session):
        self.db = db
        self.products = ProductRepository(db)
        self.categories = CategoryRepository(db)

    def global_search(self, q: str | None, *, limit: int = 6) -> GlobalSearchResults:
        term = (q or "").strip()
        if len(term) < MIN_QUERY_LENGTH:
            # An empty result, not an error: the header fires this as the box is
            # typed into, and a 422 on the first character would show an error
            # state for a query the shopper has not finished writing.
            return GlobalSearchResults(query=term)

        products, product_total = self.products.search(
            q=term,
            category_id=None,
            offset=0,
            limit=limit,
            # Relevance-ranked by the repository whenever q is set and the sort
            # is the default; passing "newest" explicitly documents that this
            # path wants that ranking rather than a catalogue ordering.
            sort_by="newest",
        )
        category_rows = self.categories.search(term, limit=limit)
        names = self._category_names(products)

        return GlobalSearchResults(
            query=term,
            products=[
                SearchProductHit.from_product(p, names.get(p.category_id)) for p in products
            ],
            product_total=product_total,
            categories=[
                SearchCategoryHit.from_category(c, count) for c, count in category_rows
            ],
            category_total=len(category_rows),
        )

    def _category_names(self, products) -> dict[int, str]:
        """`{category_id: name}` for the categories these products belong to.

        One query for the whole result page, resolved before the hits are built.
        Reading `product.category` per row instead would emit a SELECT per
        result on a path that runs on nearly every keystroke.
        """
        ids = {p.category_id for p in products if p.category_id is not None}
        if not ids:
            return {}
        rows = self.db.execute(
            select(Category.id, Category.name).where(Category.id.in_(ids))
        ).all()
        return {row.id: row.name for row in rows}
