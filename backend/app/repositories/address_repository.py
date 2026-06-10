"""Repository for customer address book.

Follows the same flush-only, caller-commits convention as every other
repository in this codebase (see base.py).  No `db.commit()` here.
"""
from __future__ import annotations

from sqlalchemy import select, update

from app.models.address import Address
from app.repositories.base import BaseRepository


class AddressRepository(BaseRepository[Address]):
    model = Address

    def list_for_user(self, user_id: int) -> list[Address]:
        """All addresses for a user, default first then most-recently-updated."""
        stmt = (
            select(Address)
            .where(Address.user_id == user_id)
            .order_by(
                Address.is_default.desc(),
                Address.updated_at.desc(),
                # id tiebreaker: updated_at has second resolution, so two
                # addresses touched in the same second would otherwise make
                # default-promotion after delete non-deterministic.
                Address.id.desc(),
            )
        )
        return list(self.db.execute(stmt).scalars().all())

    def count_for_user(self, user_id: int) -> int:
        from sqlalchemy import func

        return self.db.execute(
            select(func.count()).select_from(Address).where(Address.user_id == user_id)
        ).scalar_one()

    def get_default(self, user_id: int) -> Address | None:
        stmt = (
            select(Address)
            .where(Address.user_id == user_id, Address.is_default.is_(True))
            .limit(1)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def clear_default(self, user_id: int) -> None:
        """Bulk-clear the is_default flag for all of a user's addresses.

        Uses a single UPDATE statement so it's a flush-friendly, atomic
        operation — the caller decides when to commit.
        """
        stmt = (
            update(Address)
            .where(Address.user_id == user_id)
            .values(is_default=False)
            .execution_options(synchronize_session="evaluate")
        )
        self.db.execute(stmt)
        self.db.flush()
