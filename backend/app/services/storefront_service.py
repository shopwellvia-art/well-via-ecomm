"""Storefront configuration service.

Owns the seven `storefront.*` rows in `system_settings` (boot-seeded — see
settings_seed.py). The public storefront reads through `get_config()`, which
always returns a fully-populated document: an empty stored value means "unset"
and falls back to DEFAULT_STOREFRONT, and the two list keys are stored as JSON
strings. Admin `update_config()` replaces the document after Pydantic
validation.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ValidationError
from app.models.user import User
from app.schemas.storefront import DEFAULT_STOREFRONT, StorefrontConfigUpdate
from app.services.settings_service import SettingsService
from app.storage import get_storage
from app.storage.base import CONTENT_TYPE_EXT, MediaFolder

logger = logging.getLogger(__name__)

# Image uploads are restricted to raster types only. SVG is intentionally
# excluded: it is an active document format and when served same-origin from
# /media it can execute arbitrary JavaScript, enabling stored XSS.
_IMAGE_CONTENT_TYPES = {**CONTENT_TYPE_EXT}

_KEY_PREFIX = "storefront."
_TEXT_FIELDS = ("site_title", "brand_name", "tagline", "logo_url", "favicon_url")
# Stored as JSON strings in system_settings.
_JSON_FIELDS = ("nav_items", "homepage_sections")


class StorefrontConfigService:
    def __init__(self, db: Session):
        self.db = db
        self.settings = SettingsService(db)

    def get_config(self) -> dict:
        """Return the stored config overlaid on DEFAULT_STOREFRONT.

        `get_raw` surfaces an empty stored value as None, so unset keys keep
        the shipped default. A shallow copy is enough because stored values
        replace whole top-level keys, never mutate the nested defaults.
        """
        config = dict(DEFAULT_STOREFRONT)
        for field in _TEXT_FIELDS:
            stored = self.settings.get_raw(_KEY_PREFIX + field)
            if stored:
                config[field] = stored
        for field in _JSON_FIELDS:
            stored = self.settings.get_raw(_KEY_PREFIX + field)
            if not stored:
                continue
            try:
                parsed = json.loads(stored)
            except ValueError:
                parsed = None
            if not isinstance(parsed, list):
                logger.warning(
                    "storefront config: %s%s holds invalid JSON — using the default",
                    _KEY_PREFIX,
                    field,
                )
                continue
            config[field] = parsed
        return config

    def update_config(
        self, payload: StorefrontConfigUpdate, *, actor: User | None = None
    ) -> dict:
        """Validate and replace the storefront config. Commits the session."""
        data = payload.model_dump()
        updates = {_KEY_PREFIX + field: data[field] for field in _TEXT_FIELDS}
        for field in _JSON_FIELDS:
            updates[_KEY_PREFIX + field] = json.dumps(data[field])
        # All seven keys are boot-seeded — set_many silently ignores keys that
        # have no row, so the seed in settings_seed.py is what makes this stick.
        self.settings.set_many(updates, actor=actor)
        self.db.commit()
        return self.get_config()

    def upload_image(self, *, file_bytes: bytes, filename: str, content_type: str) -> str:
        """Persist an uploaded storefront image (logo / favicon) and return its
        public URL.

        The URL is not written to the config here — the admin UI sets it on
        `logo_url` / `favicon_url` and saves via the normal PUT, mirroring how
        the footer logo is handled.
        """
        if (content_type or "").lower() not in _IMAGE_CONTENT_TYPES:
            raise ValidationError(f"Unsupported image type: {content_type or 'unknown'}")
        max_bytes = settings.MAX_IMAGE_SIZE_MB * 1024 * 1024
        if len(file_bytes) > max_bytes:
            raise ValidationError(f"Image must be under {settings.MAX_IMAGE_SIZE_MB} MB")
        return get_storage(self.db).save(
            data=file_bytes,
            filename=filename,
            content_type=content_type,
            folder=MediaFolder.BRAND,
        )
