"""Tests for the RBAC bootstrap that runs on every app start.

The seed is called by the backend AND both analytics workers, which boot
together, so it is inherently concurrent. That is invisible while the permission
registry is unchanged — there is nothing to insert — and bites the moment a
release adds one, which is exactly when nobody is watching the boot logs.

Observed on production 2026-07-31: adding `customers.view` / `customers.manage`
/ `users.invite` made all three processes log
`RBAC seed skipped: (1062, "Duplicate entry 'users.invite'")` on every restart.
The end state was still correct (the winner's insert stood), but a seeder that
reports failure every boot trains everyone to ignore the one time it matters.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — register every table on Base.metadata
from app.models.base import Base
from app.models.rbac import Permission, Role
from app.models.system_setting import SystemSetting
from app.services.permissions_registry import PERMISSIONS
from app.services.rbac_seed import seed_rbac

MARKER = "rbac.backfill.customers_perms"


@pytest.fixture
def db_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _names(db: Session) -> set[str]:
    return {p.name for p in db.execute(select(Permission)).scalars().all()}


class TestSeedRbac:
    def test_seeds_the_whole_registry_and_system_roles(self, db_factory):
        with db_factory() as db:
            seed_rbac(db)
            assert _names(db) == {p.name for p in PERMISSIONS}
            roles = {r.name: r for r in db.execute(select(Role)).scalars().all()}
            assert set(roles) == {"admin", "customer"}
            # The admin role tracks the full permission list; customer is empty.
            assert len(roles["admin"].permissions) == len(PERMISSIONS)
            assert roles["customer"].permissions == []

    def test_is_idempotent(self, db_factory):
        with db_factory() as db:
            seed_rbac(db)
            seed_rbac(db)
            seed_rbac(db)
            assert len(_names(db)) == len(PERMISSIONS)
            assert len(db.execute(select(Role)).scalars().all()) == 2

    def test_survives_a_concurrent_seeder_inserting_the_same_permission(
        self, db_factory
    ):
        """The production failure, reproduced.

        Sequence on a real boot: process A and process B both find `users.invite`
        missing and both queue an INSERT. A commits first, so B's flush violates
        `uq_permissions_name` and the whole seed aborts — which is why every
        restart logged `RBAC seed skipped: (1062, "Duplicate entry ...")`.

        Reproduced deterministically by giving `loser` the pending INSERT that a
        stale read would have produced, then letting the winner commit first.
        Without the retry in `seed_rbac`, this raises IntegrityError.
        """
        with db_factory() as winner, db_factory() as loser:
            seed_rbac(winner)  # the winner commits the whole registry

            # What a process that read the table a moment earlier is holding:
            # an INSERT for a row that now already exists.
            loser.add(Permission(name="users.invite", group_name="Users"))

            seed_rbac(loser)

            assert _names(loser) == {p.name for p in PERMISSIONS}
            # Exactly one row per permission — the retry must not duplicate.
            all_perms = loser.execute(select(Permission)).scalars().all()
            assert len(all_perms) == len(PERMISSIONS)
            assert len([p for p in all_perms if p.name == "users.invite"]) == 1


class TestCustomersBackfill:
    def test_grants_the_new_customer_tier_to_roles_that_had_users_reach(
        self, db_factory
    ):
        """Splitting the pages must not take access away on the deploy that
        introduces them: a role that could already manage users keeps the
        equivalent reach over customers."""
        with db_factory() as db:
            seed_rbac(db)  # creates the permissions; marks the backfill done
            # Simulate a pre-existing role from before the split, then re-run the
            # backfill by clearing the marker.
            perms = {
                p.name: p for p in db.execute(select(Permission)).scalars().all()
            }
            legacy = Role(
                name="legacy-support",
                is_system=False,
                permissions=[perms["users.view"], perms["users.manage"]],
            )
            db.add(legacy)
            marker = db.execute(
                select(SystemSetting).where(SystemSetting.key == MARKER)
            ).scalar_one()
            marker.value = ""
            db.commit()

            seed_rbac(db)

            granted = {p.name for p in legacy.permissions}
            assert "customers.view" in granted
            assert "customers.manage" in granted

    def test_does_not_re_grant_what_an_admin_revoked(self, db_factory):
        """The backfill is one-shot on purpose. The seeder runs every boot, so a
        repeating backfill would silently undo a deliberate revocation — the
        admin would remove the permission and find it back after a restart."""
        with db_factory() as db:
            seed_rbac(db)
            perms = {
                p.name: p for p in db.execute(select(Permission)).scalars().all()
            }
            role = Role(
                name="ops",
                is_system=False,
                permissions=[perms["users.view"], perms["customers.view"]],
            )
            db.add(role)
            db.commit()

            # An admin deliberately revokes the customer tier from this role.
            role.permissions = [perms["users.view"]]
            db.commit()

            seed_rbac(db)  # a restart

            assert {p.name for p in role.permissions} == {"users.view"}

    def test_marker_is_recorded_so_the_backfill_runs_once(self, db_factory):
        with db_factory() as db:
            seed_rbac(db)
            marker = db.execute(
                select(SystemSetting).where(SystemSetting.key == MARKER)
            ).scalar_one()
            assert marker.value == "1"
