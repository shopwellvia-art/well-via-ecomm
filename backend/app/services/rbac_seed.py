"""One-shot RBAC bootstrap. Idempotent — safe to run on every app start.

- Upserts the canonical permission list (PERMISSIONS in permissions_registry).
- Ensures the `admin` (all perms) and `customer` (empty) system roles exist.
- Backfills the admin role onto every `is_admin=True` user that doesn't already
  have it, so the legacy flag and the new RBAC model stay in sync.
- Runs any ONE-TIME permission migrations (see `_run_once`), which — unlike
  everything above — must not be re-applied on every boot.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.rbac import Permission, Role
from app.models.system_setting import SystemSetting
from app.models.user import User
from app.services.permissions_registry import PERMISSIONS

logger = logging.getLogger(__name__)


def seed_rbac(db: Session) -> None:
    """Bring RBAC to its canonical state. Safe to run concurrently.

    The backend and both analytics workers boot together and all three call
    this, so every step here is a read-then-insert race. It stays invisible
    while the registry is unchanged — there is nothing to insert — and fires the
    moment a release ADDS a permission: two processes both see it missing, both
    insert, and the loser gets a duplicate-key error.

    Rather than lock, retry once. The second pass re-reads and finds the rows
    the winner committed, so it converges on exactly the same end state with no
    coordination. Anything still failing after that is a real error and
    propagates to the caller.
    """
    try:
        _seed_once(db)
    except IntegrityError:
        logger.info(
            "RBAC seed raced another process; retrying against the committed rows"
        )
        db.rollback()
        _seed_once(db)


def _seed_once(db: Session) -> None:
    _upsert_permissions(db)
    _ensure_system_roles(db)
    _backfill_admin_role(db)
    _run_once(db, "rbac.backfill.customers_perms", _backfill_customer_perms)
    db.commit()


def _upsert_permissions(db: Session) -> None:
    existing_by_name = {
        p.name: p for p in db.execute(select(Permission)).scalars().all()
    }
    for spec in PERMISSIONS:
        perm = existing_by_name.get(spec.name)
        if perm is None:
            db.add(
                Permission(
                    name=spec.name,
                    description=spec.description,
                    group_name=spec.group,
                )
            )
        else:
            # Keep the description / group fresh; admins should never need to
            # hand-edit these.
            perm.description = spec.description
            perm.group_name = spec.group
    db.flush()


def _ensure_system_roles(db: Session) -> None:
    all_perms = list(db.execute(select(Permission)).scalars().all())

    admin = db.execute(select(Role).where(Role.name == "admin")).scalar_one_or_none()
    if admin is None:
        admin = Role(
            name="admin",
            description="Full access to all resources",
            is_system=True,
            permissions=all_perms,
        )
        db.add(admin)
    else:
        # Admin role always tracks the full perm list.
        admin.permissions = all_perms
        admin.is_system = True

    customer = db.execute(select(Role).where(Role.name == "customer")).scalar_one_or_none()
    if customer is None:
        db.add(
            Role(
                name="customer",
                description="Default shopper role — no admin access",
                is_system=True,
                permissions=[],
            )
        )
    else:
        customer.is_system = True

    db.flush()


def _run_once(db: Session, marker_key: str, fn) -> None:
    """Run `fn(db)` the first time only, recording the fact in system_settings.

    The rest of this module is safe to repeat because it converges on a fixed
    target. A permission BACKFILL is not: re-running it would silently re-grant
    a permission an admin had deliberately revoked, on every restart. So it gets
    a marker row instead.
    """
    existing = db.execute(
        select(SystemSetting).where(SystemSetting.key == marker_key)
    ).scalar_one_or_none()
    if existing is not None and (existing.value or "").strip() == "1":
        return
    fn(db)
    if existing is None:
        db.add(
            SystemSetting(
                key=marker_key,
                value="1",
                category="general",
                description="Internal migration marker — do not edit.",
            )
        )
    else:
        existing.value = "1"
    db.flush()
    logger.info("RBAC seed: applied one-time migration %s", marker_key)


def _backfill_customer_perms(db: Session) -> None:
    """Grant the new `customers.*` tier to roles that already had the old
    combined `users.*` reach, so splitting the pages takes nobody's access away
    on the deploy that introduces them."""
    pairs = (("users.view", "customers.view"), ("users.manage", "customers.manage"))
    by_name = {p.name: p for p in db.execute(select(Permission)).scalars().all()}
    granted = 0
    for role in db.execute(select(Role)).scalars().all():
        held = {p.name for p in role.permissions}
        for old, new in pairs:
            new_perm = by_name.get(new)
            if old in held and new not in held and new_perm is not None:
                role.permissions.append(new_perm)
                held.add(new)
                granted += 1
    if granted:
        logger.info("RBAC seed: backfilled %d customers.* grant(s)", granted)


def _backfill_admin_role(db: Session) -> None:
    admin = db.execute(select(Role).where(Role.name == "admin")).scalar_one_or_none()
    if admin is None:
        return
    admin_users = list(db.execute(select(User).where(User.is_admin == True)).scalars().all())  # noqa: E712
    for user in admin_users:
        if admin not in user.roles:
            user.roles.append(admin)
    if admin_users:
        logger.info("RBAC seed: linked admin role to %d existing admin user(s)", len(admin_users))
