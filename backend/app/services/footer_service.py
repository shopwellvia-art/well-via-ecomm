"""Footer configuration service.

Owns the single-row footer config. The public storefront reads through
`get()`, which always returns a fully-populated document (falling back to
hard-coded defaults when no row exists). Admin `update()` replaces the
document after Pydantic validation.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ValidationError
from app.repositories.footer_repository import FooterRepository
from app.schemas.footer import DEFAULT_FOOTER, FooterConfigUpdate
from app.storage import get_storage
from app.storage.base import CONTENT_TYPE_EXT

# Logo uploads are restricted to raster types only. SVG is intentionally
# excluded: it is an active document format and when served same-origin from
# /media it can execute arbitrary JavaScript, enabling stored XSS.
_LOGO_CONTENT_TYPES = {**CONTENT_TYPE_EXT}


class FooterService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = FooterRepository(db)

    def get(self) -> dict:
        """Return the stored footer data, merged with defaults for any
        missing top-level keys.  A shallow merge is enough because the
        admin PUT always sends the full document.
        """
        row = self.repo.get()
        stored: dict = (row.data or {}) if row is not None else {}
        if not stored:
            return dict(DEFAULT_FOOTER)
        # Backfill any top-level keys added after a row was first saved.
        merged = dict(DEFAULT_FOOTER)
        merged.update(stored)
        return merged

    def update(self, payload: FooterConfigUpdate) -> dict:
        """Validate and replace the footer document.  Commits the session."""
        data = payload.model_dump()
        self.repo.upsert(data)
        self.db.commit()
        return data

    def upload_logo(self, *, file_bytes: bytes, filename: str, content_type: str) -> str:
        """Persist an uploaded brand logo and return its public URL.

        The URL is not written to the footer document here — the admin UI sets
        it on `brand.logo_url` and saves via the normal PUT, mirroring how hero
        slides and category images are handled.
        """
        if (content_type or "").lower() not in _LOGO_CONTENT_TYPES:
            raise ValidationError(f"Unsupported image type: {content_type or 'unknown'}")
        max_bytes = settings.MAX_IMAGE_SIZE_MB * 1024 * 1024
        if len(file_bytes) > max_bytes:
            raise ValidationError(f"Logo must be under {settings.MAX_IMAGE_SIZE_MB} MB")
        return get_storage(self.db).save(
            data=file_bytes, filename=filename, content_type=content_type
        )
