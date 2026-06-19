from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.storage.base import Storage
from app.storage.local import LocalStorage

# Built-storage instances, memoised by their resolved config tuple. Any change
# to a storage.* admin setting changes the tuple, so the next get_storage()
# rebuilds — and we avoid reconstructing a boto3 client on every call.
_memo: dict[tuple, Storage] = {}


def resolve_config(db: Optional[Session]) -> dict:
    """Resolve the effective storage config (also used by the test endpoint).

    DB settings (category ``storage``) override the env defaults; a blank or
    missing DB value falls back to the env value, so an env-only deployment is
    unaffected until an admin overrides something. Returns a dict with keys
    ``backend``, ``endpoint_url``, ``region``, ``bucket``, ``access_key``,
    ``secret_key``, ``public_base_url``.
    """
    backend = settings.STORAGE_BACKEND
    cfg = {
        "endpoint_url": settings.S3_ENDPOINT_URL,
        "region": settings.S3_REGION,
        "bucket": settings.S3_BUCKET,
        "access_key": settings.S3_ACCESS_KEY,
        "secret_key": settings.S3_SECRET_KEY,
        "public_base_url": settings.S3_PUBLIC_BASE_URL,
        "acl": settings.S3_ACL,
    }
    if db is not None:
        # Lazy import to keep the storage package importable without the
        # services layer (and to avoid any import-order surprises).
        from app.services.settings_service import SettingsService

        svc = SettingsService(db)
        backend = svc.get_raw("storage.backend", backend) or backend
        cfg["endpoint_url"] = svc.get_raw("storage.s3_endpoint_url", cfg["endpoint_url"])
        cfg["region"] = svc.get_raw("storage.s3_region", cfg["region"])
        cfg["bucket"] = svc.get_raw("storage.s3_bucket", cfg["bucket"])
        cfg["access_key"] = svc.get_raw("storage.s3_access_key", cfg["access_key"])
        cfg["secret_key"] = svc.get_raw("storage.s3_secret_key", cfg["secret_key"])
        cfg["public_base_url"] = svc.get_raw(
            "storage.s3_public_base_url", cfg["public_base_url"]
        )
        cfg["acl"] = svc.get_raw("storage.s3_acl", cfg["acl"])
    cfg["backend"] = backend
    return cfg


def get_storage(db: Optional[Session] = None) -> Storage:
    """Return the configured storage backend.

    Pass a DB session so admin-configured settings (category ``storage``) take
    effect; without one this falls back to pure env config (e.g. for callers
    outside a request).
    """
    cfg = resolve_config(db)
    if cfg["backend"] == "s3":
        sig = (
            "s3",
            cfg["bucket"],
            cfg["endpoint_url"],
            cfg["region"],
            cfg["access_key"],
            cfg["secret_key"],
            cfg["public_base_url"],
            cfg["acl"],
        )
    else:
        # Local config is env-driven (no admin settings), so the signature only
        # needs to distinguish it from any s3 instance.
        sig = ("local", settings.UPLOAD_DIR, settings.MEDIA_BASE_URL)

    inst = _memo.get(sig)
    if inst is None:
        if cfg["backend"] == "s3":
            from app.storage.s3 import S3Storage  # boto3 imported lazily

            inst = S3Storage(cfg)
        else:
            inst = LocalStorage()
        _memo[sig] = inst
    return inst
