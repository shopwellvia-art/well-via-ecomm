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
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import engine
from app.models.base import Base
from app.models.customer import Customer
from app.models.user import User
from app.services import sample_data
from app.services.email_templates.seed import seed_email_templates
from app.services.rbac_seed import seed_rbac
from app.services.settings_seed import seed_settings

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
    # The truncate also emptied system_settings; migrations won't re-run
    # (alembic_version survives), so re-seed the shipped defaults here or the
    # admin Settings page comes back blank.
    seed_settings(db)
    # Re-seed email/SMS template defaults so the template editor doesn't go blank.
    seed_email_templates(db)
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


# --------------------------------------------------------------------------- #
# Scoped, per-domain truncate + seed
# --------------------------------------------------------------------------- #
# The single big "DELETE EVERYTHING" button is the nuclear option above. The
# registry below carves the schema into domain groups so an operator can reset
# just one slice — e.g. wipe test orders without touching the product catalog —
# and (optionally) re-seed that slice with sample data afterwards.
#
# Truncation is made FK-safe by a dependency planner (`_plan_truncation`) rather
# than by hand-maintaining table orders: given a group's primary tables it pulls
# in every table with a NON-nullable FK into the set (those rows can't survive
# without their parent) and dissociates (SET NULL) the NULLABLE FKs pointing in
# from tables left out of the group — so no scoped wipe ever leaves a dangling
# reference, and unrelated domains keep their rows.

# Pseudo-scope handled specially (full wipe + admin re-seed + session reset).
EVERYTHING_SCOPE = "everything"


@dataclass(frozen=True)
class DomainGroup:
    key: str
    label: str
    description: str
    # The tables the operator thinks of as "this domain". The planner expands
    # this into the actual FK-safe truncation set.
    primary_tables: tuple[str, ...]
    # Optional sample-data loader; None means the group is truncate-only.
    seeder: Callable[[Session], dict] | None = None

    @property
    def confirm_phrase(self) -> str:
        return f"DELETE {self.key.upper()}"


DOMAIN_GROUPS: tuple[DomainGroup, ...] = (
    DomainGroup(
        key="catalog",
        label="Catalog",
        description="Categories, products and their images.",
        primary_tables=("categories", "products", "product_images"),
        seeder=sample_data.seed_catalog,
    ),
    DomainGroup(
        key="orders",
        label="Orders & payments",
        description="Orders, line items, payment events, returns and coupon usage.",
        primary_tables=(
            "orders",
            "order_items",
            "payment_events",
            "returns",
            "return_items",
            "coupon_usages",
        ),
        seeder=sample_data.seed_orders,
    ),
    DomainGroup(
        key="reviews",
        label="Reviews & ratings",
        description="Customer product reviews (product rating aggregates reset to zero).",
        primary_tables=("reviews",),
        seeder=sample_data.seed_reviews,
    ),
    DomainGroup(
        key="loyalty",
        label="Loyalty & marketing",
        description="Coupons, points ledger, VIP tiers, earn rules, redemptions and referrals.",
        primary_tables=(
            "coupons",
            "coupon_usages",
            "points_transactions",
            "redemption_tiers",
            "earn_rules",
            "vip_tiers",
            "referrals",
        ),
        seeder=sample_data.seed_loyalty,
    ),
    DomainGroup(
        key="content",
        label="Storefront content",
        description="Hero carousel slides, company pages and footer config.",
        primary_tables=("hero_slides", "site_pages", "footer_config"),
        seeder=sample_data.seed_content,
    ),
    DomainGroup(
        key="logs",
        label="Logs & telemetry",
        description="Audit trail and observability request / slow-query logs.",
        primary_tables=("audit_events", "obs_request_logs", "obs_slow_queries"),
        seeder=None,
    ),
)

_GROUPS_BY_KEY = {g.key: g for g in DOMAIN_GROUPS}


def get_group(key: str) -> DomainGroup | None:
    return _GROUPS_BY_KEY.get(key)


def _foreign_keys() -> list[tuple[str, str, str, bool]]:
    """Every FK as (referencing_table, referenced_table, referencing_column, nullable)."""
    out: list[tuple[str, str, str, bool]] = []
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            out.append(
                (
                    fk.parent.table.name,
                    fk.column.table.name,
                    fk.parent.name,
                    bool(fk.parent.nullable),
                )
            )
    return out


def _plan_truncation(primary: set[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """Expand a primary table set into an FK-safe truncation plan.

    Returns ``(tables_to_truncate, columns_to_null)``:
      * ``tables_to_truncate`` — the primary tables plus every table with a
        NON-nullable FK into the set (transitively); their rows can't exist
        without the parent so they're cleared too.
      * ``columns_to_null`` — ``[(table, column), ...]`` for NULLABLE FKs into
        the set from tables left OUT of it. We SET NULL there to drop the
        reference while keeping the (unrelated-domain) row.
    """
    fks = _foreign_keys()
    in_set = set(primary)

    # Fixpoint: absorb mandatory (non-nullable) dependents.
    changed = True
    while changed:
        changed = False
        for ref_tbl, target_tbl, _col, nullable in fks:
            if (
                target_tbl in in_set
                and ref_tbl not in in_set
                and ref_tbl != target_tbl
                and not nullable
            ):
                in_set.add(ref_tbl)
                changed = True

    # Nullable FKs pointing in from outside the final set → dissociate, don't delete.
    to_null: list[tuple[str, str]] = []
    for ref_tbl, target_tbl, col, nullable in fks:
        if target_tbl in in_set and ref_tbl not in in_set and nullable:
            to_null.append((ref_tbl, col))

    # Order children-before-parents for the non-MySQL DELETE fallback.
    order = {t.name: i for i, t in enumerate(Base.metadata.sorted_tables)}
    ordered = sorted(in_set, key=lambda n: order.get(n, 0), reverse=True)
    return ordered, to_null


def cleared_tables_for(group: DomainGroup) -> list[str]:
    """The full set of tables a scoped truncate of this group would empty."""
    tables, _ = _plan_truncation(set(group.primary_tables))
    return sorted(tables)


def _row_count(db: Session, table_name: str) -> int:
    table = Base.metadata.tables.get(table_name)
    if table is None:
        return 0
    return int(db.scalar(select(func.count()).select_from(table)) or 0)


def truncate_group(db: Session, group: DomainGroup) -> dict:
    """Empty one domain group's tables (FK-safe) and return a summary."""
    logger.warning("SCOPED TRUNCATE requested — group=%s", group.key)
    tables, to_null = _plan_truncation(set(group.primary_tables))
    dialect = engine.dialect.name

    if dialect == "mysql":
        db.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        try:
            for tbl, col in to_null:
                db.execute(text(f"UPDATE `{tbl}` SET `{col}` = NULL"))
            for name in tables:
                db.execute(text(f"TRUNCATE TABLE `{name}`"))
        finally:
            db.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    else:
        for tbl, col in to_null:
            db.execute(text(f"UPDATE {tbl} SET {col} = NULL"))
        # `tables` is already ordered children-first.
        meta = Base.metadata.tables
        for name in tables:
            if name in meta:
                db.execute(meta[name].delete())

    db.commit()
    db.expunge_all()

    dissociated = [f"{t}.{c}" for t, c in to_null]
    logger.warning(
        "SCOPED TRUNCATE complete — group=%s, %d tables emptied%s",
        group.key,
        len(tables),
        f", dissociated {dissociated}" if dissociated else "",
    )
    detail = f"Cleared {len(tables)} table(s) for “{group.label}”."
    if dissociated:
        detail += f" Dissociated {len(dissociated)} reference(s) in other domains."
    return {
        "scope": group.key,
        "tables_truncated": len(tables),
        "cleared_tables": sorted(tables),
        "dissociated": dissociated,
        "ends_session": False,
        "reseeded_admin": None,
        "detail": detail,
    }


def seed_group(db: Session, group: DomainGroup) -> dict:
    """Load sample data for one domain group. No-op summary if not seedable."""
    if group.seeder is None:
        return {"scope": group.key, "created": {}, "detail": f"“{group.label}” has no sample data."}
    logger.info("SAMPLE SEED requested — group=%s", group.key)
    created = group.seeder(db)
    db.commit()
    db.expunge_all()
    total = sum(created.values())
    if total:
        parts = ", ".join(f"{n} {t}" for t, n in created.items())
        detail = f"Seeded {group.label}: {parts}."
    else:
        detail = f"{group.label} already had sample data — nothing to add."
    return {"scope": group.key, "created": created, "detail": detail}


def list_groups(db: Session) -> list[dict]:
    """Describe every group (incl. row counts + blast radius) for the admin UI.

    Appends the special ``everything`` scope last so the frontend can render the
    full-wipe control alongside the scoped ones from a single source of truth.
    """
    groups: list[dict] = []
    for g in DOMAIN_GROUPS:
        cleared = cleared_tables_for(g)
        groups.append(
            {
                "key": g.key,
                "label": g.label,
                "description": g.description,
                "primary_tables": list(g.primary_tables),
                "cleared_tables": cleared,
                "row_count": sum(_row_count(db, t) for t in g.primary_tables),
                "seedable": g.seeder is not None,
                "confirm_phrase": g.confirm_phrase,
                "ends_session": False,
            }
        )

    all_tables = [t.name for t in Base.metadata.sorted_tables]
    groups.append(
        {
            "key": EVERYTHING_SCOPE,
            "label": "Everything",
            "description": (
                "Every table — orders, products, customers, reviews, coupons, "
                "payments and all other data. Re-creates the bootstrap admin and "
                "signs you out."
            ),
            "primary_tables": all_tables,
            "cleared_tables": sorted(all_tables),
            "row_count": sum(_row_count(db, t) for t in all_tables),
            "seedable": False,
            "confirm_phrase": CONFIRM_PHRASE,
            "ends_session": True,
        }
    )
    return groups
