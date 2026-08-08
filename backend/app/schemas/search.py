"""Global search — the response shape behind the header search box.

One request, every kind of destination. The storefront's search box used to do
nothing but `navigate('/products?q=…')`, so a shopper typing "sleep" got a full
page reload before finding out whether anything matched, and never found the
Sleep *category* at all. This is the payload that lets the header answer while
they are still typing.

The product hit is deliberately NOT `ProductRead`. That schema carries the whole
product document — description, faqs, specifications, usage_steps, box_contents,
the image list — and a suggestions dropdown is fetched on nearly every keystroke.
Sending ~8 of those per request would put tens of kilobytes on the wire to render
a 40px row. `SearchProductHit` is exactly what a result row draws, plus the
fields the shared ProductCard reads, so the same payload can back a results grid
without a second fetch.
"""
from __future__ import annotations

from decimal import Decimal

from pydantic import ConfigDict

from app.models.product import Category, Product
from app.schemas.base import AppSchema


class SearchProductHit(AppSchema):
    """A product as it appears in search results."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    name: str
    short_description: str | None = None
    price: Decimal
    compare_at_price: Decimal | None = None
    image_url: str | None = None
    stock: int
    is_combo: bool = False
    badge: str | None = None
    flavour: str | None = None
    brand: str | None = None
    rating_avg: Decimal = Decimal("0.00")
    rating_count: int = 0
    category_id: int | None = None
    # Resolved for display ("in Sleep & Calm") so the row can say where the
    # product lives without the client holding a category lookup table.
    category_name: str | None = None

    @classmethod
    def from_product(cls, product: Product, category_name: str | None = None) -> SearchProductHit:
        """Build a hit, taking the category name from the caller.

        The name is passed in rather than read off `product.category` on
        purpose: that is a lazy relationship, so reading it here would emit one
        SELECT per result row on a path that runs on every keystroke. The
        service resolves all the names it needs in a single query and hands them
        down.
        """
        hit = cls.model_validate(product)
        hit.category_name = category_name
        return hit


class SearchCategoryHit(AppSchema):
    """A category as it appears in search results."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    image_url: str | None = None
    parent_id: int | None = None
    product_count: int = 0

    @classmethod
    def from_category(cls, category: Category, product_count: int) -> SearchCategoryHit:
        hit = cls.model_validate(category)
        hit.product_count = product_count
        return hit


class GlobalSearchResults(AppSchema):
    """Everything matching one query, grouped by what kind of thing it is.

    `product_total` is the count of ALL matching products, not `len(products)` —
    the latter is capped by the request's `limit` so the dropdown can offer
    "See all 43 results" without a second round trip.
    """

    query: str
    products: list[SearchProductHit] = []
    product_total: int = 0
    categories: list[SearchCategoryHit] = []
    category_total: int = 0
