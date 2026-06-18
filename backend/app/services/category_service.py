import re

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.product import Category
from app.repositories.category_repository import CategoryRepository
from app.schemas.category import CategoryCreate, CategoryUpdate
from app.storage import get_storage
from app.storage.base import CONTENT_TYPE_EXT


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "category"


class CategoryService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = CategoryRepository(db)
        self._storage = None

    @property
    def storage(self):
        # Built lazily so read-only paths never construct a storage backend;
        # resolves admin-configured settings via self.db.
        if self._storage is None:
            self._storage = get_storage(self.db)
        return self._storage

    def list(self) -> list[Category]:
        return self.repo.list_all()

    def create(self, data: CategoryCreate) -> Category:
        slug = slugify(data.slug or data.name)
        if self.repo.get_by_slug(slug):
            raise ConflictError(f"Category slug '{slug}' already exists")
        if self.repo.get_by_name(data.name):
            raise ConflictError(f"Category '{data.name}' already exists")

        category = Category(name=data.name, slug=slug)
        self.repo.add(category)
        self.db.commit()
        self.db.refresh(category)
        return category

    def update(self, category_id: int, data: CategoryUpdate) -> Category:
        category = self.repo.get(category_id)
        if not category:
            raise NotFoundError("Category not found")

        payload = data.model_dump(exclude_unset=True)
        if payload.get("name"):
            category.name = payload["name"]
        if payload.get("slug"):
            new_slug = slugify(payload["slug"])
            clash = self.repo.get_by_slug(new_slug)
            if clash and clash.id != category.id:
                raise ConflictError(f"Category slug '{new_slug}' already exists")
            category.slug = new_slug

        self.db.commit()
        self.db.refresh(category)
        return category

    def delete(self, category_id: int) -> None:
        # The products.category_id FK is ON DELETE SET NULL — products survive.
        category = self.repo.get(category_id)
        if not category:
            raise NotFoundError("Category not found")
        if category.image_url:
            try:
                self.storage.delete(category.image_url)
            except Exception:
                pass
        self.repo.delete(category)
        self.db.commit()

    def set_image(
        self,
        category_id: int,
        *,
        file_bytes: bytes,
        filename: str,
        content_type: str,
    ) -> Category:
        if (content_type or "").lower() not in CONTENT_TYPE_EXT:
            raise ValidationError(f"Unsupported image type: {content_type or 'unknown'}")
        max_bytes = settings.MAX_IMAGE_SIZE_MB * 1024 * 1024
        if len(file_bytes) > max_bytes:
            raise ValidationError(f"Image must be under {settings.MAX_IMAGE_SIZE_MB} MB")

        category = self.repo.get(category_id)
        if not category:
            raise NotFoundError("Category not found")

        if category.image_url:
            try:
                self.storage.delete(category.image_url)
            except Exception:
                pass

        url = self.storage.save(data=file_bytes, filename=filename, content_type=content_type)
        category.image_url = url
        self.db.commit()
        self.db.refresh(category)
        return category

    def remove_image(self, category_id: int) -> Category:
        category = self.repo.get(category_id)
        if not category:
            raise NotFoundError("Category not found")

        if category.image_url:
            try:
                self.storage.delete(category.image_url)
            except Exception:
                pass
            category.image_url = None
            self.db.commit()
            self.db.refresh(category)

        return category
