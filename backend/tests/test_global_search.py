"""Global search: `GET /search?q=` and the widened product/category matching.

The storefront's search box used to hand `q` to `GET /products`, which matched
`Product.name` and nothing else. A shopper typing "sleep" therefore missed every
product whose name is "Beauty Rest Gummies" — including the ones filed under a
Sleep category — and had no way to reach the category itself. What this suite
protects:

  1. **Match breadth.** sku, brand, flavour, badge, short_description,
     description and the product's CATEGORY NAME all count as matches. Each is
     asserted separately, because a widened predicate that silently loses one
     column looks identical to a working one from the outside.
  2. **Relevance order.** An exact name match outranks a prefix, which outranks
     a description hit. Without ranking, "sleep" returned the newest product
     that happened to mention sleep in its footnotes above the product actually
     called Sleep Gummies.
  3. **LIKE wildcards are escaped.** `%` is a wildcard, so an unescaped query of
     "%" would return the entire catalogue and report it as a search result.
  4. **Explicit sorts still win.** Relevance ranking must not override a shopper
     who asked for price ascending.
  5. **The endpoint is safe to call on every keystroke** — a 1-character query
     is an empty result, not a 422, and not a full-catalogue scan.

House style, as in `test_product_image_reorder.py`: no shared DB fixture — every
test owns its `SessionLocal()` and tears down in `finally`. Sandbox is the
`TESTSRCH-` SKU prefix plus the `TestSrch ` category-name prefix, swept before
and after the module.

    docker exec wvana-py python -m pytest tests/test_global_search.py -q
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db.session import SessionLocal
from app.main import app
from app.models.product import Category, Product
from app.repositories.category_repository import CategoryRepository
from app.repositories.product_repository import ProductRepository
from app.services.search_service import SearchService

SKU_PREFIX = "TESTSRCH-"
CATEGORY_PREFIX = "TestSrch "

# A nonsense stem no real catalogue row can contain, so every assertion below is
# scoped to rows this module created even though the queries hit the whole table.
TOKEN = "zqxwv"


def _purge_sandbox() -> None:
    """Delete every `TESTSRCH-` product and `TestSrch ` category.

    Products go first: `Product.category_id` references categories, so deleting
    the categories while a sandbox product still points at one would fail on the
    foreign key.
    """
    db = SessionLocal()
    try:
        db.execute(delete(Product).where(Product.sku.like(f"{SKU_PREFIX}%")))
        db.execute(delete(Category).where(Category.name.like(f"{CATEGORY_PREFIX}%")))
        db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module", autouse=True)
def _sandbox():
    _purge_sandbox()
    yield
    _purge_sandbox()


def _category(db, name: str) -> Category:
    slug = f"testsrch-{uuid.uuid4().hex[:10]}"
    category = Category(name=f"{CATEGORY_PREFIX}{name}", slug=slug)
    db.add(category)
    db.commit()
    return category


def _product(db, name: str, **fields) -> Product:
    """A sandbox product. `stock` defaults to in-stock so ordering assertions are
    not quietly decided by the out-of-stock-last tiebreak."""
    product = Product(
        sku=fields.pop("sku", f"{SKU_PREFIX}{uuid.uuid4().hex[:10]}"),
        name=name,
        price=fields.pop("price", Decimal("499.00")),
        stock=fields.pop("stock", 10),
        **fields,
    )
    db.add(product)
    db.commit()
    return product


def _names(products) -> list[str]:
    return [p.name for p in products]


# ---------------------------------------------------------------------------
# 1. Match breadth
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,value,query",
    [
        ("sku", f"{SKU_PREFIX}{TOKEN}01", TOKEN),
        ("brand", f"Brand{TOKEN}", TOKEN),
        ("flavour", f"Flav{TOKEN}", TOKEN),
        ("badge", f"Badge{TOKEN}", TOKEN),
        ("short_description", f"A short line mentioning {TOKEN}.", TOKEN),
        ("description", f"Long prose that happens to contain {TOKEN} in it.", TOKEN),
    ],
)
def test_matches_on_every_widened_column(field, value, query):
    db = SessionLocal()
    try:
        # The name deliberately does NOT contain the token — only the field
        # under test does, so a regression to name-only matching fails here.
        _product(db, "Unrelated product name", **{field: value})

        found, total = ProductRepository(db).search(q=query, limit=20)

        assert total == 1, f"q={query!r} should have matched via {field}"
        assert _names(found) == ["Unrelated product name"]
    finally:
        _purge_sandbox()
        db.close()


def test_matches_products_by_their_category_name():
    """The "sleep" case: the word is on the category, not the product."""
    db = SessionLocal()
    try:
        category = _category(db, f"Deep {TOKEN} Support")
        _product(db, "Beauty Rest Gummies", category_id=category.id)
        _product(db, "Unrelated, no category")

        found, total = ProductRepository(db).search(q=TOKEN, limit=20)

        assert total == 1
        assert _names(found) == ["Beauty Rest Gummies"]
    finally:
        _purge_sandbox()
        db.close()


def test_category_search_returns_the_category_with_its_product_count():
    db = SessionLocal()
    try:
        category = _category(db, f"{TOKEN} Care")
        _product(db, "One", category_id=category.id)
        _product(db, "Two", category_id=category.id)

        rows = CategoryRepository(db).search(TOKEN)

        assert len(rows) == 1
        found, count = rows[0]
        assert found.id == category.id
        assert count == 2
    finally:
        _purge_sandbox()
        db.close()


def test_category_with_no_products_is_still_returned():
    """A real category must not vanish from search just because it is empty —
    it is still a valid destination."""
    db = SessionLocal()
    try:
        _category(db, f"{TOKEN} Empty")

        rows = CategoryRepository(db).search(TOKEN)

        assert len(rows) == 1
        assert rows[0][1] == 0
    finally:
        _purge_sandbox()
        db.close()


# ---------------------------------------------------------------------------
# 2. Relevance order
# ---------------------------------------------------------------------------


def test_ranks_exact_name_then_prefix_then_body_text():
    db = SessionLocal()
    try:
        # Inserted worst-first so passing cannot be an accident of insertion
        # order (the "newest" tiebreak would otherwise reverse this list).
        _product(db, "Immunity Boost", description=f"Also good for {TOKEN}.")
        _product(db, f"{TOKEN} Plus Gummies")
        _product(db, TOKEN)

        found, _ = ProductRepository(db).search(q=TOKEN, limit=20)

        assert _names(found) == [TOKEN, f"{TOKEN} Plus Gummies", "Immunity Boost"]
    finally:
        _purge_sandbox()
        db.close()


def test_in_stock_products_outrank_sold_out_ones_in_the_same_bucket():
    db = SessionLocal()
    try:
        _product(db, f"{TOKEN} Sold Out", stock=0)
        _product(db, f"{TOKEN} Available", stock=5)

        found, _ = ProductRepository(db).search(q=TOKEN, limit=20)

        assert _names(found) == [f"{TOKEN} Available", f"{TOKEN} Sold Out"]
    finally:
        _purge_sandbox()
        db.close()


def test_an_explicit_sort_is_not_overridden_by_relevance():
    """Point (4): the shopper asked for cheapest first and must get it, even
    though the pricier product is the better name match."""
    db = SessionLocal()
    try:
        _product(db, TOKEN, price=Decimal("900.00"))
        _product(db, f"Budget {TOKEN} pack", price=Decimal("100.00"))

        found, _ = ProductRepository(db).search(q=TOKEN, limit=20, sort_by="price_asc")

        assert _names(found) == [f"Budget {TOKEN} pack", TOKEN]
    finally:
        _purge_sandbox()
        db.close()


# ---------------------------------------------------------------------------
# 3. Wildcard escaping
# ---------------------------------------------------------------------------


def test_a_bare_percent_matches_nothing_rather_than_the_whole_catalogue():
    db = SessionLocal()
    try:
        _product(db, "Ordinary product")

        _, total = ProductRepository(db).search(q="%", limit=5)

        assert total == 0, "unescaped % turned the search into SELECT *"
    finally:
        _purge_sandbox()
        db.close()


def test_percent_is_matched_literally_when_it_is_part_of_the_text():
    db = SessionLocal()
    try:
        _product(db, f"{TOKEN} 20% Off Bundle")
        _product(db, f"{TOKEN} Plain Bundle")

        found, total = ProductRepository(db).search(q="20% Off", limit=5)

        assert total == 1
        assert _names(found) == [f"{TOKEN} 20% Off Bundle"]
    finally:
        _purge_sandbox()
        db.close()


def test_underscore_is_not_a_single_character_wildcard():
    db = SessionLocal()
    try:
        # With `_` live, "a_c" would match "abc". It must not.
        _product(db, f"{TOKEN} abc")

        _, total = ProductRepository(db).search(q="a_c", limit=5)

        assert total == 0
    finally:
        _purge_sandbox()
        db.close()


# ---------------------------------------------------------------------------
# 4. The service + endpoint
# ---------------------------------------------------------------------------


def test_service_groups_products_and_categories_and_resolves_category_names():
    db = SessionLocal()
    try:
        category = _category(db, f"{TOKEN} Range")
        _product(db, f"{TOKEN} Gummies", category_id=category.id)

        results = SearchService(db).global_search(TOKEN)

        assert results.query == TOKEN
        assert [p.name for p in results.products] == [f"{TOKEN} Gummies"]
        # Resolved for display, and resolved in ONE query rather than per row.
        assert results.products[0].category_name == f"{CATEGORY_PREFIX}{TOKEN} Range"
        assert [c.name for c in results.categories] == [f"{CATEGORY_PREFIX}{TOKEN} Range"]
    finally:
        _purge_sandbox()
        db.close()


def test_product_total_counts_all_matches_not_just_the_returned_page():
    """The dropdown's "See all N results" link depends on this."""
    db = SessionLocal()
    try:
        for i in range(5):
            _product(db, f"{TOKEN} variant {i}")

        results = SearchService(db).global_search(TOKEN, limit=2)

        assert len(results.products) == 2
        assert results.product_total == 5
    finally:
        _purge_sandbox()
        db.close()


@pytest.mark.parametrize("q", ["", " ", "a", None])
def test_a_too_short_query_is_an_empty_result_not_an_error(q):
    """Point (5): the header fires this while the shopper is still typing."""
    db = SessionLocal()
    try:
        results = SearchService(db).global_search(q)

        assert results.products == []
        assert results.categories == []
        assert results.product_total == 0
    finally:
        db.close()


def test_endpoint_is_public_and_returns_the_grouped_shape():
    db = SessionLocal()
    try:
        category = _category(db, f"{TOKEN} Shelf")
        _product(db, f"{TOKEN} Endpoint Probe", category_id=category.id)
    finally:
        db.close()

    # No Authorization header — global search must work for anonymous visitors.
    with TestClient(app) as client:
        response = client.get("/api/v1/search", params={"q": TOKEN})

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == TOKEN
    assert body["product_total"] == 1
    assert body["products"][0]["name"] == f"{TOKEN} Endpoint Probe"
    assert body["products"][0]["category_name"] == f"{CATEGORY_PREFIX}{TOKEN} Shelf"
    assert body["categories"][0]["product_count"] == 1
    # The hit is the compact shape, not the whole product document — the panel
    # is fetched on nearly every keystroke.
    assert "faqs" not in body["products"][0]
    assert "specifications" not in body["products"][0]
    _purge_sandbox()


def test_endpoint_rejects_an_absurdly_long_query():
    """`max_length` keeps a pasted essay from becoming a full scan."""
    with TestClient(app) as client:
        response = client.get("/api/v1/search", params={"q": "x" * 500})

    assert response.status_code == 422
