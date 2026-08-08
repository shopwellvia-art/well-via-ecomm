"""Hermetic tests for ProductService.bestsellers sparse-ranking top-up.

Regression for the homepage rail rendering a single card: on a young store
where only one product has qualifying orders, the sales ranking returns one
row and the old code only fell back to newest() when the ranking was *empty*.
The rail must always fill to `limit` — real sellers first, newest products
after.

All in-process against in-memory SQLite — no live MySQL required:

    docker compose exec backend pytest tests/unit/test_bestsellers_topup.py -v
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Product
from app.models.user import User
from app.services.product_service import ProductService


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _product(db: Session, n: int) -> Product:
    product = Product(
        sku=f"BS-{n:02d}",
        name=f"Bestseller Test {n:02d}",
        price=Decimal("299.00"),
        stock=100,
        rating_avg=Decimal("0.00"),
        rating_count=0,
    )
    db.add(product)
    db.flush()
    return product


def _order(db: Session, user: User, product: Product, qty: int, status: OrderStatus) -> Order:
    order = Order(
        user_id=user.id,
        status=status,
        subtotal=product.price * qty,
        total_amount=product.price * qty,
    )
    order.items = [
        OrderItem(product_id=product.id, quantity=qty, unit_price=product.price)
    ]
    db.add(order)
    db.flush()
    return order


@pytest.fixture()
def user(db: Session) -> User:
    u = User(email="bestsellers@test.local", hashed_password="x")
    db.add(u)
    db.flush()
    return u


def test_sparse_ranking_tops_up_with_newest(db: Session, user: User) -> None:
    products = [_product(db, n) for n in range(1, 10)]

    # One real seller mid-catalog; a PENDING order must not count as a sale.
    _order(db, user, products[2], qty=3, status=OrderStatus.PAID)
    _order(db, user, products[4], qty=5, status=OrderStatus.PENDING)

    items = ProductService(db).bestsellers(limit=8)

    ids = [p.id for p in items]
    sold_id = products[2].id
    # Ranked seller leads; the rest fill newest-first (created same instant,
    # so id desc), with the seller not repeated.
    expected_fill = [p.id for p in reversed(products) if p.id != sold_id][:7]
    assert ids == [sold_id, *expected_fill]
    assert len(ids) == len(set(ids))


def test_zero_sales_falls_back_to_newest(db: Session) -> None:
    products = [_product(db, n) for n in range(1, 10)]

    items = ProductService(db).bestsellers(limit=8)

    assert [p.id for p in items] == [p.id for p in reversed(products)][:8]


def test_full_ranking_needs_no_fill(db: Session, user: User) -> None:
    products = [_product(db, n) for n in range(1, 4)]
    _order(db, user, products[1], qty=5, status=OrderStatus.PAID)
    _order(db, user, products[0], qty=3, status=OrderStatus.DELIVERED)
    _order(db, user, products[2], qty=1, status=OrderStatus.SHIPPED)

    items = ProductService(db).bestsellers(limit=2)

    # Sales order only — the newest top-up must not run or reorder anything.
    assert [p.id for p in items] == [products[1].id, products[0].id]
