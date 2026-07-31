"""add users.last_login_at

WHAT THIS DOES
--------------
Adds one nullable column, `users.last_login_at`. Nothing else is touched.

WHY
---
Nothing in the schema records when an account last signed in. Refresh sessions
live in Redis under a TTL, so once they expire that information is gone — "when
was this account last used" is unanswerable, for the staff directory (is this
admin account dormant and should it be disabled?) and the customer directory
(is this shopper still active?) alike.

Written once per fresh login in `AuthService._issue_tokens`, which is the single
point every login method converges on (password, 2FA completion, Google).
Deliberately NOT written on `/auth/refresh` — a rotating refresh token would
turn "last login" into "last API call".

NULL means "has not logged in since this column existed", which is why the
column is nullable with no default and no backfill: there is no honest value to
backfill it with. Readers must render NULL as unknown, not as "never".

NOTE FOR THE SHARED REMOTE DB
-----------------------------
The live database's migration lineage diverges from this repo and `alembic
upgrade` must never be run against it. Apply
`scripts/sql/2026-07-31_users_last_login_at.sql` by hand there, one deploy
BEFORE the code that reads the column ships. This revision covers local and
fresh installs.

Revision ID: f4c7a1b90d23
Revises: e8b207fd93c1
"""
from alembic import op
import sqlalchemy as sa

revision = "f4c7a1b90d23"
down_revision = "e8b207fd93c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "last_login_at")
