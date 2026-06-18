import uuid

import boto3

from app.core.exceptions import ValidationError
from app.storage.base import CONTENT_TYPE_EXT, Storage

KEY_PREFIX = "products/"


class S3Storage(Storage):
    """S3-compatible object storage — works with AWS S3 and DigitalOcean Spaces.

    Configured from a resolved ``cfg`` dict (built by ``app.storage.get_storage``
    from admin settings with an env fallback), with keys: ``bucket``,
    ``endpoint_url``, ``region``, ``access_key``, ``secret_key``,
    ``public_base_url``. For AWS S3 leave ``endpoint_url`` blank; for DO Spaces
    set it to e.g. https://blr1.digitaloceanspaces.com. Blank credentials fall
    through to boto3's default chain (env vars / IAM instance role).
    """

    def __init__(self, cfg: dict) -> None:
        endpoint_url = (cfg.get("endpoint_url") or "").rstrip("/")
        self.bucket = cfg.get("bucket") or ""
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            region_name=cfg.get("region") or None,
            aws_access_key_id=cfg.get("access_key") or None,
            aws_secret_access_key=cfg.get("secret_key") or None,
        )
        # Public base for reading objects (a CDN domain, or the bucket URL).
        self.public_base = (
            (cfg.get("public_base_url") or "").rstrip("/")
            or f"{endpoint_url}/{self.bucket}"
        )

    def save(self, *, data: bytes, filename: str, content_type: str) -> str:
        # Derive the extension solely from the validated content-type allowlist.
        # Never trust the user-controlled filename suffix — it can be used to
        # store arbitrary file types (e.g. .html, .svg) that lead to XSS when
        # served same-origin.
        ext = CONTENT_TYPE_EXT.get((content_type or "").lower())
        if not ext:
            raise ValidationError(
                f"Unsupported image content type: {content_type or 'unknown'}"
            )
        key = f"{KEY_PREFIX}{uuid.uuid4().hex}{ext}"
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            ACL="public-read",
        )
        return f"{self.public_base}/{key}"

    def delete(self, url: str) -> None:
        if not url.startswith(self.public_base):
            return
        key = url[len(self.public_base):].lstrip("/")
        self.client.delete_object(Bucket=self.bucket, Key=key)
