from sqlalchemy import func, or_, select

from app.models.customer import AccountStatus, Customer
from app.models.user import User
from app.repositories.base import BaseRepository


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
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[User], int]:
        stmt = select(User)
        count_stmt = select(func.count()).select_from(User)
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
