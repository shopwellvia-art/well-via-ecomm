"""seed storage.* system settings

Surfaces the existing S3 / image-storage configuration (previously env-only:
STORAGE_BACKEND + S3_* in app.core.config) as runtime-editable admin settings,
rendered on the admin Settings page under a new "Storage" tab.

All rows are seeded EMPTY on purpose: SettingsService.get_raw(key, default)
falls back to the env value when the stored value is None/"", so an existing
deployment that sets STORAGE_BACKEND / S3_* via .env keeps behaving exactly as
before until an admin overrides a value here. Seeding storage.backend with a
concrete value (e.g. "local") would silently override a live env S3 setup.

Only storage.s3_secret_key is marked secret (masked in the admin list response
and redacted in the audit log); the access key id is left visible, mirroring
how twilio.account_sid / smtp.user are treated.

Revision ID: l3m4n5o6p7q8
Revises: k2g3h4i5j6k7
Create Date: 2026-06-18 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "l3m4n5o6p7q8"
down_revision: Union[str, None] = "k2g3h4i5j6k7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (key, value, category, description, is_secret)
_STORAGE_SETTINGS = [
    ("storage.backend", "", "storage",
     "Image storage backend: 'local' or 's3' (blank = use the env default)", 0),
    ("storage.s3_region", "", "storage",
     "AWS region, e.g. ap-south-1", 0),
    ("storage.s3_bucket", "", "storage",
     "S3 bucket name", 0),
    ("storage.s3_endpoint_url", "", "storage",
     "Custom endpoint for S3-compatible stores (e.g. DigitalOcean Spaces); "
     "leave blank for AWS S3", 0),
    ("storage.s3_public_base_url", "", "storage",
     "Public base URL / CDN used to serve objects; blank = the bucket URL", 0),
    ("storage.s3_access_key", "", "storage",
     "AWS access key ID", 0),
    ("storage.s3_secret_key", "", "storage",
     "AWS secret access key", 1),
]


def upgrade() -> None:
    for key, value, category, description, is_secret in _STORAGE_SETTINGS:
        op.execute(
            "INSERT INTO system_settings (`key`, value, category, description, is_secret) "
            f"VALUES (:k, :v, :c, :d, {int(is_secret)})"
            .replace(":k", repr(key))
            .replace(":v", repr(value))
            .replace(":c", repr(category))
            .replace(":d", repr(description))
        )


def downgrade() -> None:
    op.execute("DELETE FROM system_settings WHERE category = 'storage'")
