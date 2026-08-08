"""Public global search: GET /search?q=…

Anonymous and unauthenticated — it only reads the same catalogue the storefront
already renders. Kept in its own module rather than added to products.py because
it spans product AND category results; hanging a cross-entity route off
/products would have implied it returns products alone.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.search import GlobalSearchResults
from app.services.search_service import SearchService

router = APIRouter()


@router.get("", response_model=GlobalSearchResults)
def global_search(
    q: str | None = Query(default=None, max_length=100, description="Free-text query"),
    limit: int = Query(
        default=6,
        ge=1,
        le=20,
        description="Max hits per group. `product_total` still reports every match.",
    ),
    db: Session = Depends(get_db),
):
    """Products and categories matching `q`, grouped and relevance-ranked.

    `max_length` on the query caps the LIKE pattern the database is asked to
    build; nothing legitimate is 100 characters, and it keeps a pasted essay
    from turning into an expensive full scan on a public endpoint.

    Returns an empty result — not a 422 — for a missing or too-short `q`, since
    the header calls this while the shopper is still typing.
    """
    return SearchService(db).global_search(q, limit=limit)
