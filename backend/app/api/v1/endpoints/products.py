from decimal import Decimal

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_admin
from app.schemas.common import Page, PaginationParams
from app.schemas.product import ProductCreate, ProductRead, ProductUpdate
from app.services.product_service import ProductService

router = APIRouter()


@router.get("", response_model=Page[ProductRead])
def list_products(
    q: str | None = Query(default=None),
    category_id: int | None = Query(default=None),
    category_ids: str | None = Query(
        default=None, description="CSV of category ids; superset of category_id"
    ),
    flavours: str | None = Query(default=None, description="CSV of flavour tags"),
    min_price: Decimal | None = Query(default=None, ge=0),
    max_price: Decimal | None = Query(default=None, ge=0),
    min_rating: Decimal | None = Query(default=None, ge=0, le=5),
    in_stock: bool | None = Query(default=None),
    is_combo: bool | None = Query(default=None),
    discounted: bool | None = Query(default=None),
    sort_by: str = Query(default="newest", pattern="^(newest|price_asc|price_desc|rating)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    pagination = PaginationParams(page=page, page_size=page_size)
    items, total = ProductService(db).search(
        q=q,
        category_id=category_id,
        category_ids=category_ids,
        flavours=flavours,
        min_price=min_price,
        max_price=max_price,
        min_rating=min_rating,
        in_stock=in_stock,
        is_combo=is_combo,
        discounted=discounted,
        sort_by=sort_by,
        offset=pagination.offset,
        limit=pagination.page_size,
    )
    return Page[ProductRead](
        items=[ProductRead.model_validate(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/bestsellers", response_model=list[ProductRead])
def bestsellers(
    limit: int = Query(default=8, ge=1, le=24),
    db: Session = Depends(get_db),
):
    """Top products by units sold; falls back to newest when no orders yet."""
    items = ProductService(db).bestsellers(limit=limit)
    return [ProductRead.model_validate(i) for i in items]


@router.get("/by-ids/batch", response_model=list[ProductRead])
def products_by_ids(
    ids: str = Query(..., description="Comma-separated product ids, preserves order"),
    db: Session = Depends(get_db),
):
    """Bulk product lookup. Backs the browsing-history rail — the client sends
    its locally-stored last-viewed ids and gets back full product objects in
    the same order."""
    try:
        id_list = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError:
        return []
    if not id_list:
        return []
    # Cap to keep the request bounded.
    id_list = id_list[:24]
    items = ProductService(db).by_ids(id_list)
    return [ProductRead.model_validate(i) for i in items]


@router.get("/{product_id}", response_model=ProductRead)
def get_product(product_id: int, db: Session = Depends(get_db)):
    return ProductService(db).get(product_id)


@router.get("/{product_id}/related", response_model=list[ProductRead])
def related_products(
    product_id: int,
    limit: int = Query(default=8, ge=1, le=24),
    db: Session = Depends(get_db),
):
    items = ProductService(db).related(product_id, limit=limit)
    return [ProductRead.model_validate(i) for i in items]


@router.get("/{product_id}/co-purchased", response_model=list[ProductRead])
def co_purchased_products(
    product_id: int,
    limit: int = Query(default=12, ge=1, le=24),
    db: Session = Depends(get_db),
):
    """Products customers bought alongside this one."""
    items = ProductService(db).co_purchased(product_id, limit=limit)
    return [ProductRead.model_validate(i) for i in items]


@router.get("/{product_id}/likely", response_model=list[ProductRead])
def likely_to_buy(
    product_id: int,
    limit: int = Query(default=12, ge=1, le=24),
    db: Session = Depends(get_db),
):
    """Items customers are likely to buy — bestsellers in the same category."""
    items = ProductService(db).likely_to_buy(product_id, limit=limit)
    return [ProductRead.model_validate(i) for i in items]


@router.post(
    "",
    response_model=ProductRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
def create_product(payload: ProductCreate, db: Session = Depends(get_db)):
    return ProductService(db).create(payload)


@router.patch(
    "/{product_id}",
    response_model=ProductRead,
    dependencies=[Depends(require_admin)],
)
def update_product(product_id: int, payload: ProductUpdate, db: Session = Depends(get_db)):
    return ProductService(db).update(product_id, payload)


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
def delete_product(product_id: int, db: Session = Depends(get_db)):
    ProductService(db).delete(product_id)


@router.post(
    "/{product_id}/images",
    response_model=ProductRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def upload_product_images(
    product_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    payloads = [
        (await f.read(), f.filename or "image", f.content_type or "")
        for f in files
    ]
    return ProductService(db).add_images(product_id, payloads)


@router.delete(
    "/{product_id}/images/{image_id}",
    response_model=ProductRead,
    dependencies=[Depends(require_admin)],
)
def delete_product_image(
    product_id: int, image_id: int, db: Session = Depends(get_db)
):
    return ProductService(db).delete_image(product_id, image_id)


@router.post(
    "/{product_id}/images/{image_id}/primary",
    response_model=ProductRead,
    dependencies=[Depends(require_admin)],
)
def set_primary_product_image(
    product_id: int, image_id: int, db: Session = Depends(get_db)
):
    return ProductService(db).set_primary_image(product_id, image_id)
