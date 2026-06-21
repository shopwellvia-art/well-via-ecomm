from pathlib import Path

from app.core.config import settings
from app.core.exceptions import ValidationError
from app.storage.base import (
    CONTENT_TYPE_EXT,
    MediaFolder,
    Storage,
    build_object_key,
)

MEDIA_PREFIX = "/media/"


class LocalStorage(Storage):
    """Stores uploads on the local filesystem, served by FastAPI at /media.

    Objects are laid out under the upload dir with the same entity/date folder
    hierarchy as S3 (e.g. ``uploads/products/2026/06/<uuid>.jpg``), so the local
    media tree is just as browsable. The ``wellvia`` root prefix is an
    S3-bucket-namespacing concern only — the local ``uploads`` dir is already
    this project's namespace, so no root prefix is applied here.
    """

    def __init__(self) -> None:
        self.dir = Path(settings.UPLOAD_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.base_url = settings.MEDIA_BASE_URL.rstrip("/")

    def save(
        self,
        *,
        data: bytes,
        filename: str,
        content_type: str,
        folder: str = MediaFolder.PRODUCTS,
    ) -> str:
        # Derive the extension solely from the validated content-type allowlist.
        # Never trust the user-controlled filename suffix — it can be used to
        # store arbitrary file types (e.g. .html, .svg) that lead to XSS when
        # served same-origin.
        ext = CONTENT_TYPE_EXT.get((content_type or "").lower())
        if not ext:
            raise ValidationError(
                f"Unsupported image content type: {content_type or 'unknown'}"
            )
        key = build_object_key(folder, ext)
        path = self.dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"{self.base_url}{MEDIA_PREFIX}{key}"

    def delete(self, url: str) -> None:
        if MEDIA_PREFIX not in url:
            return
        key = url.rsplit(MEDIA_PREFIX, 1)[-1]
        path = self.dir / key
        if path.is_file():
            path.unlink()
