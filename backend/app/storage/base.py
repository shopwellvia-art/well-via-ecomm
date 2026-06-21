import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone

# Extensions allowed for uploaded images, mapped from content type.
CONTENT_TYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
}


class MediaFolder:
    """Logical media classes → top-level sub-folders in the object store.

    The layout is intentionally human-browsable so anyone opening the bucket
    (or the local uploads dir) can tell what an object is from its path alone::

        <root>/products/<YYYY>/<MM>/<uuid>.<ext>   product gallery images
        <root>/categories/<uuid>.<ext>             category thumbnails
        <root>/hero/<uuid>.<ext>                    homepage hero slides
        <root>/brand/<uuid>.<ext>                   brand logo / footer assets

    ``<root>`` is the storage root prefix (e.g. ``wellvia`` on S3). Pass one of
    these constants as the ``folder`` argument to ``Storage.save``.
    """

    PRODUCTS = "products"
    CATEGORIES = "categories"
    HERO = "hero"
    BRAND = "brand"


# Folders whose object count is unbounded are sharded by upload month so no
# single prefix grows without limit. This keeps S3 LIST operations fast and the
# tree easy to browse ("what did we upload in 2026/06?"). The bounded classes
# (a handful of categories / hero slides / one logo) stay flat.
_DATE_PARTITIONED = frozenset({MediaFolder.PRODUCTS})


def build_object_key(folder: str, ext: str, *, root_prefix: str = "") -> str:
    """Build the storage key (object path) for a new upload.

    Returns e.g. ``wellvia/products/2026/06/3f2a1c….jpg``. The filename is a
    server-generated UUID — the user-supplied filename is never part of the
    path, so there is no traversal risk and no collisions. ``folder`` is one of
    the :class:`MediaFolder` constants; ``ext`` includes the leading dot and is
    derived from the validated content-type allowlist (never the upload name).
    ``root_prefix`` is the optional top-level namespace (blank for local disk,
    e.g. ``wellvia`` for a shared S3 bucket).
    """
    parts: list[str] = []
    root = (root_prefix or "").strip("/")
    if root:
        parts.append(root)
    folder = (folder or MediaFolder.PRODUCTS).strip("/")
    parts.append(folder)
    if folder in _DATE_PARTITIONED:
        now = datetime.now(timezone.utc)
        parts.append(f"{now:%Y}")
        parts.append(f"{now:%m}")
    parts.append(f"{uuid.uuid4().hex}{ext}")
    return "/".join(parts)


class Storage(ABC):
    """A pluggable image store. Implementations return a public URL on save."""

    @abstractmethod
    def save(
        self,
        *,
        data: bytes,
        filename: str,
        content_type: str,
        folder: str = MediaFolder.PRODUCTS,
    ) -> str:
        """Persist file bytes under ``folder`` and return a publicly reachable URL."""

    @abstractmethod
    def delete(self, url: str) -> None:
        """Remove a previously saved file. Missing files are ignored."""
