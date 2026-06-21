from datetime import datetime

from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import joinedload, selectinload

from app.models.order import Order, OrderItem, OrderStatus
from app.models.user import User
from app.repositories.base import BaseRepository


class OrderRepository(BaseRepository[Order]):
    model = Order

    def get_with_items(self, id_: int) -> Order | None:
        stmt = (
            select(Order)
            .options(
                # Eager-load the catalog product on each line so OrderItemRead
                # can surface its name + image without an N+1 per line.
                selectinload(Order.items).selectinload(OrderItem.product),
                # Normalized children surfaced on the detail responses — eager
                # loaded so serialization never lazy-loads on a closed session.
                selectinload(Order.payments),
                selectinload(Order.shipments),
                selectinload(Order.addresses),
                joinedload(Order.user),
            )
            .where(Order.id == id_)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def list_for_user(self, user_id: int, *, offset: int = 0, limit: int = 20) -> list[Order]:
        stmt = (
            select(Order)
            .options(
                selectinload(Order.items).selectinload(OrderItem.product),
                selectinload(Order.payments),
                selectinload(Order.shipments),
                selectinload(Order.addresses),
            )
            .where(Order.user_id == user_id)
            .order_by(Order.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())

    def admin_search(
        self,
        *,
        q: str | None = None,
        status: OrderStatus | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> tuple[list[Order], int]:
        """Paginated admin list with optional filters. Joins users so we can
        search by email — the join is cheap because we eagerly join anyway."""
        stmt = select(Order).options(joinedload(Order.user))
        count_stmt = select(func.count()).select_from(Order)

        if q:
            cleaned = q.strip()
            # Numeric query → match order id directly.
            try:
                order_id = int(cleaned)
                cond = or_(Order.id == order_id, User.email.ilike(f"%{cleaned}%"))
            except ValueError:
                cond = User.email.ilike(f"%{cleaned}%")
            stmt = stmt.join(User, User.id == Order.user_id).where(cond)
            count_stmt = count_stmt.join(User, User.id == Order.user_id).where(cond)
        if status is not None:
            stmt = stmt.where(Order.status == status)
            count_stmt = count_stmt.where(Order.status == status)
        if date_from is not None:
            stmt = stmt.where(Order.created_at >= date_from)
            count_stmt = count_stmt.where(Order.created_at >= date_from)
        if date_to is not None:
            stmt = stmt.where(Order.created_at <= date_to)
            count_stmt = count_stmt.where(Order.created_at <= date_to)

        total = self.db.execute(count_stmt).scalar_one()
        items = list(
            self.db.execute(
                stmt.order_by(desc(Order.created_at), desc(Order.id))
                .offset(offset)
                .limit(limit)
            )
            .unique()
            .scalars()
            .all()
        )
        return items, total

    def counts_by_status(self) -> dict[str, int]:
        """Aggregate counts for the admin dashboard / filter chips."""
        rows = self.db.execute(
            select(Order.status, func.count(Order.id)).group_by(Order.status)
        ).all()
        return {str(row[0].value if hasattr(row[0], "value") else row[0]): int(row[1]) for row in rows}
