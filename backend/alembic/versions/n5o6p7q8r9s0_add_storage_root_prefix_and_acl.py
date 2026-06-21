"""seed storage.s3_root_prefix + storage.s3_acl system settings

Adds two runtime-editable rows to the existing "Storage" settings tab:

  * storage.s3_root_prefix — the top-level bucket folder every uploaded object
    is organised under (e.g. wellvia/products/2026/06/<id>.jpg). Seeded with the
    concrete default "wellvia" so it is visible/editable in the admin UI; the
    env var S3_ROOT_PREFIX provides the same default when the row is blank.

  * storage.s3_acl — the object ACL sent on upload. Seeded EMPTY (no ACL), which
    is correct for modern buckets with Object Ownership = "Bucket owner enforced"
    (ACLs disabled). The S3Storage layer already read this key via get_raw with
    an env fallback; this migration simply surfaces it as an admin field.

Inserts are guarded with WHERE NOT EXISTS so they are a no-op if the in-code
seeder (app.services.settings_seed) already created the rows on boot — the two
must stay in sync. Mirrors l3m4n5o6p7q8 (the original storage seed).

Revision ID: n5o6p7q8r9s0
Revises: m4n5o6p7q8r9
Create Date: 2026-06-21 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "n5o6p7q8r9s0"
down_revision: Union[str, None] = "m4n5o6p7q8r9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (key, value, description, is_secret) — all category "storage".
_STORAGE_SETTINGS = [
    ("storage.s3_root_prefix", "wellvia",
     "Top-level bucket folder all uploads are organised under, e.g. "
     "wellvia/products/2026/06/<id>.jpg. Blank = the env default (wellvia).", 0),
    ("storage.s3_acl", "",
     "Object ACL on upload — blank for modern buckets (Object Ownership = "
     "Bucket owner enforced / ACLs disabled); 'public-read' only for legacy "
     "ACL-enabled buckets.", 0),
]

_INSERT = sa.text(
    "INSERT INTO system_settings (`key`, value, category, description, is_secret) "
    "SELECT :k, :v, 'storage', :d, :s FROM DUAL "
    "WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE `key` = :k)"
)


def upgrade() -> None:
    conn = op.get_bind()
    for key, value, description, is_secret in _STORAGE_SETTINGS:
        conn.execute(_INSERT, {"k": key, "v": value, "d": description, "s": is_secret})


def downgrade() -> None:
    op.execute(
        "DELETE FROM system_settings WHERE `key` IN "
        "('storage.s3_root_prefix', 'storage.s3_acl')"
    )
