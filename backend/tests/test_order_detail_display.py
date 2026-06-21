"""Tests for the order-detail display enrichment (2026-06-21).

The customer order pages (``/orders`` list + ``/orders/:id`` detail) need to
show product names, thumbnails, and a dated status timeline. The order line
itself doesn't snapshot the product name/image, so:

* ``OrderItem`` exposes ``name`` / ``image_url`` resolved from the linked
  ``Product`` (via a read-only ``product`` relationship).
* ``OrderItemRead`` carries those display fields.
* ``OrderRead`` carries the status timestamps the storefront stepper renders.

Two layers, mirroring ``test_order_normalization.py``:

* ``TestOrderItemDisplayUnit`` — pure, in-memory (no database).
* ``TestOrderDetailDisplayDB`` — integration through the real service + DB,
  auto-skipping when the database is unreachable.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.order import OrderItem
from app.models.product import Product
from app.schemas.order import OrderItemRead, OrderRead


# --------------------------------------------------------------------------- #
# Unit tests — no database                                                     #
# --------------------------------------------------------------------------- #
class TestOrderItemDisplayUnit:
    def test_name_and_image_are_none_without_product(self):
        """A line with no resolvable product degrades gracefully (no crash)."""
        item = OrderItem(product_id=1, quantity=1, unit_price=Decimal("10.00"))
        assert item.name is None
        assert item.image_url is None

    def test_name_and_image_resolve_from_linked_product(self):
        item = OrderItem(product_id=5, quantity=2, unit_price=Decimal("99.00"))
        item.product = Product(
            id=5,
            sku="SKU5",
            name="Test Widget",
            price=Decimal("99.00"),
            image_url="/media/widget.jpg",
        )
        assert item.name == "Test Widget"
        assert item.image_url == "/media/widget.jpg"

    def test_item_read_exposes_display_fields(self):
        fields = OrderItemRead.model_fields
        assert "name" in fields
        assert "image_url" in fields

    def test_item_read_picks_up_product_fields_via_from_attributes(self):
        item = OrderItem(product_id=7, quantity=1, unit_price=Decimal("12.50"))
        item.product = Product(
            id=7, sku="SKU7", name="Gizmo", price=Decimal("12.50"), image_url=None
        )
        # id is required by the schema; set it so model_validate succeeds.
        item.id = 7
        dto = OrderItemRead.model_validate(item)
        assert dto.name == "Gizmo"
        assert dto.image_url is None
        assert dto.product_id == 7

    def test_order_read_exposes_status_timestamps(self):
        fields = OrderRead.model_fields
        for ts in ("paid_at", "shipped_at", "delivered_at", "cancelled_at", "refunded_at"):
            assert ts in fields


# --------------------------------------------------------------------------- #
# Integration tests — real DB (auto-skip when unreachable)                     #
# --------------------------------------------------------------------------- #
@pytest.fixture
def db():
    from sqlalchemy.exc import OperationalError

    from app.db.session import SessionLocal

    try:
        session = SessionLocal()
        from sqlalchemy import text

        session.execute(text("SELECT 1"))
    except OperationalError as exc:  # pragma: no cover - environmental
        pytest.skip(f"database unreachable: {exc}")
    try:
        yield session
    finally:
        session.close()


class TestOrderDetailDisplayDB:
    def _seed_user_and_product(self, db):
        from app.models.user import User

        user = db.query(User).first()
        product = db.query(Product).filter(Product.stock > 0).first()
        assert user is not None and product is not None, "seed data missing"
        return user, product

    def _create_order(self, db, user, product):
        from app.schemas.order import OrderCreate, OrderItemCreate
        from app.services.order_service import OrderService

        return OrderService(db).create(
            user.id,
            OrderCreate(
                items=[OrderItemCreate(product_id=product.id, quantity=1)],
                shipping_address="42 Test Lane, Bengaluru 560001",
            ),
        )

    def test_get_for_user_serializes_item_name_and_image(self, db):
        from app.services.order_service import OrderService

        user, product = self._seed_user_and_product(db)
        created = self._create_order(db, user, product)
        try:
            loaded = OrderService(db).get_for_user(user.id, created.id)
            dto = OrderRead.model_validate(loaded)
            assert dto.items, "order should have at least one item"
            line = dto.items[0]
            assert line.product_id == product.id
            assert line.name == product.name
            assert line.image_url == product.image_url
            # A freshly placed order hasn't progressed past "placed".
            assert dto.paid_at is None
            assert dto.shipped_at is None
            assert dto.delivered_at is None
        finally:
            db.delete(created)
            db.commit()

    def test_list_for_user_serializes_item_name(self, db):
        from app.services.order_service import OrderService

        user, product = self._seed_user_and_product(db)
        created = self._create_order(db, user, product)
        try:
            svc = OrderService(db)
            orders = svc.list_for_user(user.id, offset=0, limit=50)
            mine = next(o for o in orders if o.id == created.id)
            dto = OrderRead.model_validate(mine)
            assert dto.items[0].name == product.name
        finally:
            db.delete(created)
            db.commit()
