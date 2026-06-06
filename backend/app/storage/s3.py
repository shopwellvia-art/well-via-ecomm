import uuid

import boto3

from app.core.config import settings
from app.core.exceptions import ValidationError
from app.storage.base import CONTENT_TYPE_EXT, Storage

KEY_PREFIX = "products/"


class S3Storage(Storage):
    """S3-compatible object storage — works with AWS S3 and DigitalOcean Spaces.

    For AWS S3 leave S3_ENDPOINT_URL blank. For DO Spaces set it to e.g.
    https://blr1.digitaloceanspaces.com.
    """

    def __init__(self) -> None:
        self.bucket = settings.S3_BUCKET
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL or None,
            region_name=settings.S3_REGION or None,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
        )
        # Public base for reading objects (a CDN domain, or the bucket URL).
        self.public_base = (
            settings.S3_PUBLIC_BASE_URL.rstrip("/")
            or f"{(settings.S3_ENDPOINT_URL or '').rstrip('/')}/{self.bucket}"
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
