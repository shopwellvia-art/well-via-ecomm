from __future__ import annotations

import logging
from datetime import date  # noqa: F401  (used in annotations)

from sqlalchemy import and_, func, or_, select
from sqlalchemy import false as sa_false
from sqlalchemy.exc import SQLAlchemyError

from app.models.customer import AccountStatus, Customer
from app.models.rbac import Role, user_roles
from app.models.user import User
from app.repositories.base import BaseRepository

logger = logging.getLogger(__name__)

#: The empty system role every shopper may carry (see `rbac_seed`). Holding it
#: is NOT staff — excluding it is what keeps the customer directory correct.
CUSTOMER_ROLE_NAME = "customer"


def staff_predicate():
    """SQL for "this account has admin access of some kind".

    Staff == the legacy superadmin flag OR at least one assigned role that
    isn't the empty `customer` system role. Returned as a clause so the two
    directory queries and the guardrails all classify identically — there is
    exactly one definition of staff in the codebase and this is it.
    """
    return or_(
        User.is_admin.is_(True),
        select(user_roles.c.user_id)
        .join(Role, Role.id == user_roles.c.role_id)
        .where(
            and_(
                user_roles.c.user_id == User.id,
                Role.name != CUSTOMER_ROLE_NAME,
            )
        )
        .exists(),
    )


class UserRepository(BaseRepository[User]):
    model = User

    def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email)
        return self.db.execute(stmt).scalar_one_or_none()

    def get_by_email_or_phone(self, identifier: str) -> User | None:
        """Resolve a login identifier that may be either an email or a phone.

        Email is tried first so an email-shaped identifier never collides with a
        free-form phone value (both columns are unique, but a phone string could
        in theory equal an email). Returns None when neither matches. MySQL's
        case-insensitive collation makes the email match case-insensitive.
        """
        ident = (identifier or "").strip()
        if not ident:
            return None
        by_email = self.db.execute(
            select(User).where(User.email == ident)
        ).scalar_one_or_none()
        if by_email is not None:
            return by_email
        return self.db.execute(
            select(User).where(User.phone == ident)
        ).scalar_one_or_none()

    # ---- Staff / superadmin ----

    def count_active_superadmins(self, *, excluding_user_id: int | None = None) -> int:
        """Active `is_admin` accounts, optionally ignoring one id.

        Used to refuse the action that locks everybody out of the admin: there
        must always be at least one enabled superadmin left.
        """
        stmt = select(func.count()).select_from(User).where(
            User.is_admin.is_(True), User.is_active.is_(True)
        )
        if excluding_user_id is not None:
            stmt = stmt.where(User.id != excluding_user_id)
        return self.db.execute(stmt).scalar_one()

    def is_staff(self, user: User) -> bool:
        """In-Python mirror of `staff_predicate()`. Same rule, no query — the
        roles are already eagerly loaded on User."""
        if user.is_admin:
            return True
        return any(r.name != CUSTOMER_ROLE_NAME for r in user.roles)

    # ---- Customer satellite ----

    def get_customer(self, user_id: int) -> Customer | None:
        return self.db.execute(
            select(Customer).where(Customer.user_id == user_id)
        ).scalar_one_or_none()

    def get_or_create_customer(self, user_id: int) -> Customer:
        """Return the user's customer row, creating a blank ACTIVE one if it's
        missing. Rows are created eagerly at registration, so this is mostly a
        defensive fallback for legacy/edge accounts."""
        customer = self.get_customer(user_id)
        if customer is None:
            customer = Customer(user_id=user_id, account_status=AccountStatus.ACTIVE)
            self.db.add(customer)
            self.db.flush()
        return customer

    def search(
        self,
        *,
        q: str | None = None,
        scope: str = "all",
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[User], int]:
        """Paginated user search.

        `scope="staff"` narrows to accounts with admin access (see
        `staff_predicate`); the default "all" is the pre-split behaviour and is
        kept so existing callers of GET /users are unaffected.
        """
        stmt = select(User)
        count_stmt = select(func.count()).select_from(User)
        if scope == "staff":
            stmt = stmt.where(staff_predicate())
            count_stmt = count_stmt.where(staff_predicate())
        if q:
            like = f"%{q.strip()}%"
            # first/last name now live on the customers satellite, so search has
            # to reach across the 1:1 join (outer — defensive against a user
            # with no customer row).
            join_cond = Customer.user_id == User.id
            match = or_(
                User.email.ilike(like),
                Customer.first_name.ilike(like),
                Customer.last_name.ilike(like),
            )
            stmt = stmt.outerjoin(Customer, join_cond).where(match)
            count_stmt = count_stmt.outerjoin(Customer, join_cond).where(match)
        total = self.db.execute(count_stmt).scalar_one()
        items = list(
            self.db.execute(stmt.order_by(User.id.desc()).offset(offset).limit(limit))
            .scalars()
            .all()
        )
        return items, total

    # ---- Customer directory -------------------------------------------------

    def latest_snapshot_date(self) -> "date | None":
        """Most recent `agg_customer_snapshot` day for the active tz generation.

        The snapshot is a daily rollup, so every figure read from it is "as of"
        this date and must be labelled that way rather than presented as live.
        Returns None when there is nothing to read, in which case the directory
        still lists every customer — just without the analytics columns.

        Two deliberate choices, both about not letting analytics take the
        customer directory down with it:

        * It reads `analytics_tz_generations` directly instead of calling
          `timebox.active_generation`, which SEEDS a generation row when none
          exists. Listing customers is a GET; a GET must not write.
        * Any failure degrades to None. Support staff looking someone up must
          not get a 500 because a rollup table is mid-migration or a generation
          row is missing — they lose the spend column, not the page.
        """
        # Imported lazily: the analytics stack pulls in a large module graph and
        # this repository is on the auth path, which must stay cheap to import.
        try:
            from app.models.analytics_control import AnalyticsTzGeneration
            from app.models.analytics_rollups import AggCustomerSnapshot

            generation = self.db.execute(
                select(AnalyticsTzGeneration.generation)
                .where(AnalyticsTzGeneration.status == "active")
                .order_by(AnalyticsTzGeneration.generation.desc())
                .limit(1)
            ).scalar_one_or_none()
            if generation is None:
                return None
            return self.db.execute(
                select(func.max(AggCustomerSnapshot.bucket_date)).where(
                    AggCustomerSnapshot.tz_generation == generation
                )
            ).scalar_one_or_none()
        except SQLAlchemyError:
            logger.warning(
                "customer directory: analytics snapshot unavailable; "
                "listing without per-customer analytics",
                exc_info=True,
            )
            return None

    def search_customers(
        self,
        *,
        q: str | None = None,
        status: str | None = None,
        segment: str | None = None,
        has_ordered: bool | None = None,
        joined_from: "date | None" = None,
        joined_to: "date | None" = None,
        sort: str = "recent",
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[tuple[User, object | None]], int, "date | None"]:
        """The shopper directory: every account that is NOT staff.

        Returns `(rows, total, snapshot_date)` where each row is
        `(User, AggCustomerSnapshot | None)`. The snapshot is LEFT JOINed — a
        customer who registered but never ordered has no snapshot row and must
        still appear, which is exactly the population a "never ordered" filter
        needs to find.
        """
        from app.models.analytics_rollups import AggCustomerSnapshot

        snapshot_date = self.latest_snapshot_date()

        snap = AggCustomerSnapshot
        join_cond = [snap.user_id == User.id]
        if snapshot_date is not None:
            join_cond.append(snap.bucket_date == snapshot_date)
        else:
            # No rollup yet — force the join to match nothing rather than
            # fanning every customer out across every historical snapshot row.
            join_cond.append(sa_false())

        stmt = (
            select(User, snap)
            .outerjoin(Customer, Customer.user_id == User.id)
            .outerjoin(snap, and_(*join_cond))
            .where(~staff_predicate())
        )
        count_stmt = (
            select(func.count())
            .select_from(User)
            .outerjoin(Customer, Customer.user_id == User.id)
            .where(~staff_predicate())
        )

        filters = []
        if q:
            like = f"%{q.strip()}%"
            filters.append(
                or_(
                    User.email.ilike(like),
                    User.phone.ilike(like),
                    Customer.first_name.ilike(like),
                    Customer.last_name.ilike(like),
                )
            )
        if status:
            try:
                filters.append(Customer.account_status == AccountStatus(status))
            except ValueError:
                # Unknown status — match nothing rather than silently ignoring
                # the filter and implying the whole list satisfies it.
                filters.append(sa_false())
        if joined_from is not None:
            filters.append(User.created_at >= joined_from)
        if joined_to is not None:
            filters.append(User.created_at < joined_to)

        for f in filters:
            stmt = stmt.where(f)
            count_stmt = count_stmt.where(f)

        # Snapshot-derived filters only apply to the joined statement; the count
        # has to repeat them through a correlated EXISTS so both agree.
        snap_filters = []
        if segment:
            snap_filters.append(snap.rfm_segment == segment)
        if has_ordered is True:
            snap_filters.append(snap.orders_count > 0)

        for f in snap_filters:
            stmt = stmt.where(f)
        if snap_filters:
            count_stmt = count_stmt.where(
                select(snap.id)
                .where(and_(snap.user_id == User.id, *join_cond[1:], *snap_filters))
                .exists()
            )
        if has_ordered is False:
            # "Never ordered" is the ABSENCE of a snapshot row (or a zero count),
            # so it cannot be expressed as a filter on the joined row.
            never = or_(snap.id.is_(None), snap.orders_count == 0)
            stmt = stmt.where(never)
            count_stmt = count_stmt.where(
                ~select(snap.id)
                .where(and_(snap.user_id == User.id, *join_cond[1:], snap.orders_count > 0))
                .exists()
            )

        # A customer with no snapshot row must sort BELOW one with data rather
        # than heading a "highest spend" list. `.nullslast()` expresses that but
        # compiles to `NULLS LAST`, which MySQL does not support (1064) — and
        # the test suite runs on SQLite, which accepts it, so the failure only
        # ever appears in production. `col IS NULL` sorts False(0) before
        # True(1) on every dialect we run, so it says the same thing portably.
        def nulls_last(col):
            return (col.is_(None), col.desc())

        order_by = {
            "recent": (User.id.desc(),),
            "oldest": (User.id.asc(),),
            "email": (User.email.asc(),),
            "ltv": nulls_last(snap.gross_ltv),
            "orders": nulls_last(snap.orders_count),
            "last_order": nulls_last(snap.last_order_at),
        }.get(sort, (User.id.desc(),))

        total = self.db.execute(count_stmt).scalar_one()
        rows = self.db.execute(
            stmt.order_by(*order_by).offset(offset).limit(limit)
        ).all()
        return [(r[0], r[1]) for r in rows], total, snapshot_date
