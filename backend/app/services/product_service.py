from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.product import Product, ProductImage
from app.repositories.product_repository import ProductRepository
from app.schemas.product import ProductCreate, ProductUpdate
from app.storage import get_storage
from app.storage.base import CONTENT_TYPE_EXT, MediaFolder


class ProductService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = ProductRepository(db)
        self._storage = None

    @property
    def storage(self):
        # Built lazily so read-only paths (listing/search) never construct a
        # storage backend; resolves admin-configured settings via self.db.
        if self._storage is None:
            self._storage = get_storage(self.db)
        return self._storage

    def create(self, data: ProductCreate) -> Product:
        if self.repo.get_by_sku(data.sku):
            raise ConflictError(f"SKU {data.sku} already exists")
        product = Product(**data.model_dump())
        self.repo.add(product)
        self.db.commit()
        return self.get(product.id)

    def get(self, product_id: int) -> Product:
        product = self.repo.get_with_images(product_id)
        if not product:
            raise NotFoundError("Product not found")
        return product

    def update(self, product_id: int, data: ProductUpdate) -> Product:
        product = self.get(product_id)
        patch = data.model_dump(exclude_unset=True)

        # Validate against the post-patch state: the new compare_at_price must
        # exceed the new price (which may itself come from the patch or remain
        # the existing value). Schema-level validation can't see the row.
        effective_price = patch.get("price", product.price)
        effective_compare = patch.get("compare_at_price", product.compare_at_price)
        if effective_compare is not None and effective_compare <= effective_price:
            raise ValidationError("compare_at_price must be greater than price")

        for key, value in patch.items():
            setattr(product, key, value)
        self.db.commit()
        return self.get(product_id)

    def delete(self, product_id: int) -> None:
        product = self.get(product_id)
        for image in list(product.images):
            self.storage.delete(image.url)
        self.repo.delete(product)
        self.db.commit()

    def search(
        self,
        *,
        q: str | None,
        category_id: int | None,
        offset: int,
        limit: int,
        category_ids: str | None = None,
        flavours: str | None = None,
        min_price: Decimal | None = None,
        max_price: Decimal | None = None,
        min_rating: Decimal | None = None,
        in_stock: bool | None = None,
        is_combo: bool | None = None,
        discounted: bool | None = None,
        sort_by: str = "newest",
    ) -> tuple[list[Product], int]:
        # CSV params arrive raw from the query string; parse them here so the
        # repository deals only in typed lists.
        parsed_category_ids: list[int] | None = None
        if category_ids:
            try:
                parsed_category_ids = [int(x) for x in category_ids.split(",") if x.strip()]
            except ValueError:
                parsed_category_ids = None
        parsed_flavours = (
            [x.strip() for x in flavours.split(",") if x.strip()] if flavours else None
        )
        return self.repo.search(
            q=q,
            category_id=category_id,
            category_ids=parsed_category_ids,
            flavours=parsed_flavours,
            min_price=min_price,
            max_price=max_price,
            min_rating=min_rating,
            in_stock=in_stock,
            is_combo=is_combo,
            discounted=discounted,
            sort_by=sort_by,
            offset=offset,
            limit=limit,
        )

    def bestsellers(self, *, limit: int = 8) -> list[Product]:
        """Top sellers, with a graceful fallback for fresh catalogs.

        On a brand-new store with zero qualifying orders, returning an empty
        list would make the homepage section disappear. Fall back to the most
        recently added products instead, so the section is never empty.
        """
        items = self.repo.bestsellers(limit=limit)
        if items:
            return items
        return self.repo.newest(limit=limit)

    def co_purchased(self, product_id: int, *, limit: int = 12) -> list[Product]:
        """Items bought in the same orders as this one. Falls back to related
        when the product has no co-purchase history yet so the rail still has
        content on a fresh catalog."""
        # Ensures the product exists (raises NotFoundError otherwise).
        self.get(product_id)
        items = self.repo.co_purchased(product_id=product_id, limit=limit)
        if items:
            return items
        return self.related(product_id, limit=limit)

    def likely_to_buy(self, product_id: int, *, limit: int = 12) -> list[Product]:
        """Bestsellers scoped to the product's category, with sensible fallbacks.

        Order of preference:
          1. Top sellers within the same category (excluding self)
          2. Top sellers overall (excluding self)
          3. Newest products (so the rail is never empty on a fresh store)
        """
        product = self.get(product_id)
        category_id = product.category_id

        if category_id is not None:
            scoped = self.repo.bestsellers(limit=limit + 1, category_id=category_id)
            scoped = [p for p in scoped if p.id != product_id][:limit]
            if scoped:
                return scoped

        overall = self.repo.bestsellers(limit=limit + 1)
        overall = [p for p in overall if p.id != product_id][:limit]
        if overall:
            return overall

        newest = self.repo.newest(limit=limit + 1)
        return [p for p in newest if p.id != product_id][:limit]

    def by_ids(self, ids: list[int]) -> list[Product]:
        return self.repo.by_ids(ids)

    def related(self, product_id: int, *, limit: int = 8) -> list[Product]:
        """Products to surface alongside this one — same category, excluding self.

        Falls back to newest products overall if the source product has no
        category, so the rail is never empty on a fresh catalog.
        """
        product = self.get(product_id)
        candidates, _ = self.repo.search(
            q=None,
            category_id=product.category_id,
            offset=0,
            limit=limit + 1,
        )
        related = [p for p in candidates if p.id != product.id][:limit]
        if not related and product.category_id is not None:
            # Category had only this product — widen to anything else.
            candidates, _ = self.repo.search(q=None, category_id=None, offset=0, limit=limit + 1)
            related = [p for p in candidates if p.id != product.id][:limit]
        return related

    # ---- Images ----

    def add_images(
        self, product_id: int, files: list[tuple[bytes, str, str]]
    ) -> Product:
        """Attach uploaded images. `files` is a list of (data, filename, content_type)."""
        product = self.get(product_id)
        if not files:
            raise ValidationError("No image files were provided")
        if len(product.images) + len(files) > settings.MAX_PRODUCT_IMAGES:
            raise ConflictError(
                f"A product can have at most {settings.MAX_PRODUCT_IMAGES} images"
            )

        max_bytes = settings.MAX_IMAGE_SIZE_MB * 1024 * 1024
        for data, _filename, content_type in files:
            if (content_type or "").lower() not in CONTENT_TYPE_EXT:
                raise ValidationError(f"Unsupported image type: {content_type or 'unknown'}")
            if len(data) > max_bytes:
                raise ValidationError(
                    f"Each image must be under {settings.MAX_IMAGE_SIZE_MB} MB"
                )

        next_position = max((img.position for img in product.images), default=-1) + 1
        for data, filename, content_type in files:
            url = self.storage.save(
                data=data,
                filename=filename,
                content_type=content_type,
                folder=MediaFolder.PRODUCTS,
            )
            product.images.append(
                ProductImage(url=url, position=next_position, is_primary=False)
            )
            next_position += 1

        self.db.flush()
        self._sync_primary(product)
        self.db.commit()
        return self.get(product_id)

    def delete_image(self, product_id: int, image_id: int) -> Product:
        product = self.get(product_id)
        image = next((img for img in product.images if img.id == image_id), None)
        if image is None:
            raise NotFoundError("Image not found")

        self.storage.delete(image.url)
        product.images.remove(image)
        self.db.flush()
        self._sync_primary(product)
        self.db.commit()
        return self.get(product_id)

    def set_primary_image(self, product_id: int, image_id: int) -> Product:
        product = self.get(product_id)
        if not any(img.id == image_id for img in product.images):
            raise NotFoundError("Image not found")

        for img in product.images:
            img.is_primary = img.id == image_id
        self.db.flush()
        self._sync_primary(product)
        self.db.commit()
        return self.get(product_id)

    def _sync_primary(self, product: Product) -> None:
        """Ensure exactly one primary image and mirror its URL onto the product."""
        images = sorted(product.images, key=lambda i: i.position)
        if not images:
            product.image_url = None
            return
        primary = next((img for img in images if img.is_primary), None)
        if primary is None:
            primary = images[0]
            primary.is_primary = True
        product.image_url = primary.url
