"""Hermetic tests for the storefront listing filters and sorts.

Covers ProductService.search → ProductRepository.search with the query params
added for the Wellvia storefront redesign: flavours, price range, min_rating,
in_stock, is_combo, discounted, category_ids, and sort_by.

All in-process against in-memory SQLite — no live MySQL required:

    docker compose exec backend pytest tests/unit/test_product_filters.py -v
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.product import Category, Product
from app.services.product_service import ProductService


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def catalog(db: Session) -> Session:
    sleep = Category(name="Better Sleep", slug="better-sleep")
    immunity = Category(name="Immunity Support", slug="immunity-support")
    db.add_all([sleep, immunity])
    db.flush()

    def product(sku, **kw):
        defaults = dict(
            name=sku.replace("-", " ").title(),
            price=Decimal("349.00"),
            stock=10,
            rating_avg=Decimal("0.00"),
            rating_count=0,
        )
        defaults.update(kw)
        return Product(sku=sku, **defaults)

    db.add_all(
        [
            product(
                "sleep-gummies",
                category_id=sleep.id,
                flavour="Black Currant",
                price=Decimal("349.00"),
                compare_at_price=Decimal("449.00"),
                rating_avg=Decimal("4.60"),
                rating_count=100,
            ),
            product(
                "immunity-gummies",
                category_id=immunity.id,
                flavour="Orange",
                price=Decimal("499.00"),
                rating_avg=Decimal("3.20"),
                rating_count=5,
            ),
            product(
                "multivitamin-gummies",
                flavour="Grape",
                price=Decimal("199.00"),
                stock=0,
            ),
            product(
                "night-combo",
                is_combo=True,
                price=Decimal("899.00"),
                compare_at_price=Decimal("1099.00"),
            ),
        ]
    )
    db.commit()
    return db


def _skus(items: list[Product]) -> list[str]:
    return [p.sku for p in items]


def _search(db: Session, **kw):
    params = dict(q=None, category_id=None, offset=0, limit=20)
    params.update(kw)
    return ProductService(db).search(**params)


class TestFilters:
    def test_no_filters_returns_all(self, catalog):
        items, total = _search(catalog)
        assert total == 4

    def test_flavours_csv(self, catalog):
        items, total = _search(catalog, flavours="Grape, Orange")
        assert total == 2
        assert set(_skus(items)) == {"immunity-gummies", "multivitamin-gummies"}

    def test_price_range(self, catalog):
        items, total = _search(catalog, min_price=Decimal("300"), max_price=Decimal("500"))
        assert set(_skus(items)) == {"sleep-gummies", "immunity-gummies"}

    def test_min_rating_excludes_unrated(self, catalog):
        # multivitamin has rating_avg 0 / count 0 — must not match "0 & above".
        items, total = _search(catalog, min_rating=Decimal("0"))
        assert set(_skus(items)) == {"sleep-gummies", "immunity-gummies"}
        items, _ = _search(catalog, min_rating=Decimal("4"))
        assert _skus(items) == ["sleep-gummies"]

    def test_in_stock(self, catalog):
        items, total = _search(catalog, in_stock=True)
        assert "multivitamin-gummies" not in _skus(items)
        assert total == 3

    def test_is_combo(self, catalog):
        items, _ = _search(catalog, is_combo=True)
        assert _skus(items) == ["night-combo"]
        items, _ = _search(catalog, is_combo=False)
        assert "night-combo" not in _skus(items)

    def test_discounted(self, catalog):
        items, _ = _search(catalog, discounted=True)
        assert set(_skus(items)) == {"sleep-gummies", "night-combo"}

    def test_category_ids_csv(self, catalog):
        cats = {c.slug: c.id for c in catalog.query(Category).all()}
        items, _ = _search(
            catalog, category_ids=f"{cats['better-sleep']},{cats['immunity-support']}"
        )
        assert set(_skus(items)) == {"sleep-gummies", "immunity-gummies"}

    def test_bad_category_ids_csv_ignored(self, catalog):
        items, total = _search(catalog, category_ids="abc,,")
        assert total == 4

    def test_combined_filters(self, catalog):
        items, total = _search(
            catalog,
            in_stock=True,
            discounted=True,
            min_price=Decimal("300"),
            flavours="Black Currant",
        )
        assert _skus(items) == ["sleep-gummies"]


class TestSorts:
    def test_price_asc(self, catalog):
        items, _ = _search(catalog, sort_by="price_asc")
        prices = [float(p.price) for p in items]
        assert prices == sorted(prices)

    def test_price_desc(self, catalog):
        items, _ = _search(catalog, sort_by="price_desc")
        prices = [float(p.price) for p in items]
        assert prices == sorted(prices, reverse=True)

    def test_rating(self, catalog):
        items, _ = _search(catalog, sort_by="rating")
        assert _skus(items)[0] == "sleep-gummies"

    def test_unknown_sort_falls_back_to_newest(self, catalog):
        items, _ = _search(catalog, sort_by="bogus")
        assert len(items) == 4

    def test_pagination_respects_filters(self, catalog):
        items, total = _search(catalog, in_stock=True, limit=2, offset=0)
        assert total == 3 and len(items) == 2
        items2, _ = _search(catalog, in_stock=True, limit=2, offset=2)
        assert len(items2) == 1
        assert not set(_skus(items)) & set(_skus(items2))
