"""Gallery ordering: `PATCH /products/{id}/images/order` and the service
method behind it.

`ProductImage.position` has existed since the model was written and the
relationship is `order_by="ProductImage.position"`, but until now nothing could
write a position other than "append at the end". What this suite protects:

  1. **The response is not stale.** Sessions run with `expire_on_commit=False`,
     and a `selectinload` never overwrites an already-loaded collection. Without
     the explicit expire in `reorder_images`, the endpoint answers with the OLD
     order while the database holds the new one — the admin drags a tile, the
     grid snaps back, and nothing in the logs looks wrong.
  2. **The payload is all-or-nothing.** A partial list would leave the omitted
     images holding stale positions that collide with the reordered ones, and
     gallery order would silently fall back to row insertion order.
  3. **Reordering is not a primary switch.** `is_primary` is set by its own
     control; moving a tile must not reassign it, and `product.image_url` must
     keep mirroring the primary rather than whatever now sits first.
  4. **A rejected call writes nothing.**

House style, as in `test_product_analytics_fields.py`: no shared DB fixture —
every test owns its `SessionLocal()` and tears down in `finally`. Sandbox is
the `TESTIMG-` SKU prefix, swept before and after the module.

    docker exec wvana-py python -m pytest tests/test_product_image_reorder.py -q
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.exceptions import ValidationError
from app.db.session import SessionLocal
from app.models.product import Product, ProductImage
from app.services.product_service import ProductService
from tests.conftest import get_test_admin_token

SKU_PREFIX = "TESTIMG-"


def _purge_sandbox() -> None:
    """Delete every `TESTIMG-` product (images cascade). Scoped by prefix, so a
    run can never reach a real row or a `PERF-` benchmark fixture."""
    db = SessionLocal()
    try:
        db.execute(delete(Product).where(Product.sku.like(f"{SKU_PREFIX}%")))
        db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module", autouse=True)
def _sandbox():
    _purge_sandbox()
    yield
    _purge_sandbox()


def _seed(db, count: int = 3) -> Product:
    """A product with `count` images, positions 0..n-1, first one primary.

    Rows are built directly rather than through `add_images` so the test never
    touches a storage backend — ordering is what is under test, not upload.
    """
    sku = f"{SKU_PREFIX}{uuid.uuid4().hex[:10]}"
    product = Product(sku=sku, name=f"Gallery probe {sku}", price=Decimal("199.00"), stock=3)
    product.images = [
        ProductImage(url=f"/uploads/products/{sku}-{i}.jpg", position=i, is_primary=(i == 0))
        for i in range(count)
    ]
    product.image_url = product.images[0].url
    db.add(product)
    db.commit()
    return product


def test_reorder_returns_and_persists_the_new_order():
    db = SessionLocal()
    try:
        product = _seed(db)
        svc = ProductService(db)
        ids = [img.id for img in svc.get(product.id).images]

        updated = svc.reorder_images(product.id, list(reversed(ids)))

        # The response itself must carry the new order — see (1) in the docstring.
        assert [img.id for img in updated.images] == list(reversed(ids))
        assert [img.position for img in updated.images] == [0, 1, 2]
    finally:
        db.close()

    # And it is on disk, not just in the returning session's identity map.
    db = SessionLocal()
    try:
        assert [img.id for img in ProductService(db).get(product.id).images] == list(
            reversed(ids)
        )
    finally:
        db.close()


def test_reorder_leaves_the_primary_image_alone():
    db = SessionLocal()
    try:
        product = _seed(db)
        svc = ProductService(db)
        images = svc.get(product.id).images
        primary_id, primary_url = images[0].id, images[0].url

        updated = svc.reorder_images(product.id, [img.id for img in reversed(images)])

        assert [img.id for img in updated.images if img.is_primary] == [primary_id]
        assert updated.images[-1].id == primary_id  # moved to the back, still primary
        assert updated.image_url == primary_url
    finally:
        db.close()


@pytest.mark.parametrize(
    "mangle",
    [
        pytest.param(lambda ids: ids[:2], id="partial_list"),
        pytest.param(lambda ids: [*ids, ids[0]], id="duplicate_id"),
        pytest.param(lambda ids: [*ids[:-1], 10**9], id="foreign_id"),
        pytest.param(lambda ids: [], id="empty"),
    ],
)
def test_reorder_rejects_anything_but_the_full_set(mangle):
    db = SessionLocal()
    try:
        product = _seed(db)
        svc = ProductService(db)
        ids = [img.id for img in svc.get(product.id).images]

        with pytest.raises(ValidationError):
            svc.reorder_images(product.id, mangle(ids))

        db.rollback()
        # A rejected call writes nothing — see (4).
        assert [img.id for img in ProductService(SessionLocal()).get(product.id).images] == ids
    finally:
        db.close()


def test_reorder_endpoint_round_trip(client: TestClient):
    token = get_test_admin_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    db = SessionLocal()
    try:
        product = _seed(db)
        ids = [img.id for img in ProductService(db).get(product.id).images]
    finally:
        db.close()

    resp = client.patch(
        f"/api/v1/products/{product.id}/images/order",
        json={"image_ids": list(reversed(ids))},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert [img["id"] for img in resp.json()["images"]] == list(reversed(ids))
    assert [img["position"] for img in resp.json()["images"]] == [0, 1, 2]

    # A partial list is a 4xx, not a silent partial write.
    bad = client.patch(
        f"/api/v1/products/{product.id}/images/order",
        json={"image_ids": ids[:1]},
        headers=headers,
    )
    assert bad.status_code in (400, 422), bad.text


def test_reorder_endpoint_requires_auth(client: TestClient):
    db = SessionLocal()
    try:
        product = _seed(db)
        ids = [img.id for img in ProductService(db).get(product.id).images]
    finally:
        db.close()

    resp = client.patch(
        f"/api/v1/products/{product.id}/images/order",
        json={"image_ids": list(reversed(ids))},
    )
    assert resp.status_code in (401, 403), resp.text
