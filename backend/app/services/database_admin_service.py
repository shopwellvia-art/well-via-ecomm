"""Superadmin-only destructive maintenance: full database truncate + re-seed.

Empties every application table, then re-seeds the RBAC permission/role set and
the bootstrap admin account so the operator can sign back in. The acting user's
own row is among those wiped, so their session is dead afterwards and a fresh
sign-in as the re-seeded admin is required.

This is intentionally NOT reachable by scoped RBAC staff: the endpoint that
calls it is gated by require_admin (is_admin=True), the legacy full-access tier
the product treats as "superadmin".
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import engine
from app.models.base import Base
from app.models.customer import Customer
from app.models.user import User
from app.services.rbac_seed import seed_rbac

logger = logging.getLogger(__name__)

# Phrase the caller must echo back to confirm. Shared with the API layer (and
# mirrored in the frontend panel) so all three agree on the exact string.
CONFIRM_PHRASE = "DELETE EVERYTHING"

# Mirrors scripts/seed.py ADMIN so a wiped database can be brought back to a
# loggable state. Keep these in sync with scripts/seed.py.
_BOOTSTRAP_ADMIN = {
    "email": "vinay@gmail.com",
    "full_name": "Vinay",
    "password": "vinay@123",
}


def _truncate_all(db: Session) -> int:
    """Empty every application table and return how many were cleared.

    `alembic_version` is created by Alembic, not declared as a SQLAlchemy model,
    so it is absent from Base.metadata and survives untouched — migration state
    is preserved across the wipe.
    """
    tables = list(Base.metadata.sorted_tables)
    dialect = engine.dialect.name
    if dialect == "mysql":
        # FK checks off so we can TRUNCATE in any order; TRUNCATE also resets
        # AUTO_INCREMENT, which DELETE would not.
        db.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        try:
            for table in tables:
                db.execute(text(f"TRUNCATE TABLE `{table.name}`"))
        finally:
            db.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    else:
        # Generic fallback (sqlite/postgres dev): delete children before parents.
        for table in reversed(tables):
            db.execute(table.delete())
    db.commit()
    # The actor and any other rows loaded earlier in this request are now stale
    # in the identity map; drop them so the re-seed starts from a clean slate.
    db.expunge_all()
    return len(tables)


def _reseed_admin(db: Session) -> str:
    """Recreate the bootstrap admin + its customer profile, then re-seed RBAC so
    the permission/role set exists and the admin role is linked back onto the
    freshly-created is_admin user. Returns the admin email."""
    admin = User(
        email=_BOOTSTRAP_ADMIN["email"],
        hashed_password=hash_password(_BOOTSTRAP_ADMIN["password"]),
        is_active=True,
        is_admin=True,
    )
    db.add(admin)
    db.flush()  # need admin.id for the customer satellite
    first, _, last = _BOOTSTRAP_ADMIN["full_name"].partition(" ")
    db.add(Customer(user_id=admin.id, first_name=first or None, last_name=last or None))
    db.commit()
    # Idempotent — same path as app startup. Upserts permissions, ensures the
    # admin/customer system roles, and backfills the admin role onto the new
    # is_admin user (commits internally).
    seed_rbac(db)
    return _BOOTSTRAP_ADMIN["email"]


def truncate_all_and_reseed(db: Session) -> dict:
    """Empty every table then re-seed the bootstrap admin + RBAC.

    Returns a small JSON-serializable summary for the API response and audit log.
    """
    logger.warning("DATABASE TRUNCATE requested — wiping all application tables")
    count = _truncate_all(db)
    admin_email = _reseed_admin(db)
    logger.warning(
        "DATABASE TRUNCATE complete — %d tables emptied, admin %s re-seeded",
        count,
        admin_email,
    )
    return {
        "tables_truncated": count,
        "reseeded_admin": admin_email,
        "detail": (
            f"Database truncated — {count} tables emptied and the admin account "
            f"({admin_email}) was re-created. Sign in again to continue."
        ),
    }
