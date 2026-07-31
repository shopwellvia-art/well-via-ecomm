from fastapi import APIRouter, Depends, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission
from app.schemas.category import CategoryCreate, CategoryRead, CategoryUpdate
from app.services.category_service import CategoryService

router = APIRouter()


@router.get("", response_model=list[CategoryRead])
def list_categories(db: Session = Depends(get_db)):
    return CategoryService(db).list()


@router.post(
    "",
    response_model=CategoryRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("categories.create"))],
)
def create_category(payload: CategoryCreate, db: Session = Depends(get_db)):
    return CategoryService(db).create(payload)


@router.patch(
    "/{category_id}",
    response_model=CategoryRead,
    dependencies=[Depends(require_permission("categories.update"))],
)
def update_category(
    category_id: int, payload: CategoryUpdate, db: Session = Depends(get_db)
):
    return CategoryService(db).update(category_id, payload)


@router.delete(
    "/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("categories.delete"))],
)
def delete_category(category_id: int, db: Session = Depends(get_db)):
    CategoryService(db).delete(category_id)


@router.post(
    "/{category_id}/image",
    response_model=CategoryRead,
    dependencies=[Depends(require_permission("categories.update"))],
)
async def set_category_image(
    category_id: int,
    file: UploadFile,
    db: Session = Depends(get_db),
):
    file_bytes = await file.read()
    return CategoryService(db).set_image(
        category_id,
        file_bytes=file_bytes,
        filename=file.filename or "image",
        content_type=file.content_type or "",
    )


@router.delete(
    "/{category_id}/image",
    response_model=CategoryRead,
    dependencies=[Depends(require_permission("categories.update"))],
)
def remove_category_image(category_id: int, db: Session = Depends(get_db)):
    return CategoryService(db).remove_image(category_id)
