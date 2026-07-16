from decimal import Decimal

from sqlalchemy import and_, func, select, update
from sqlalchemy.orm import aliased, selectinload

from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Product
from app.repositories.base import BaseRepository

# Orders only count toward bestseller rank once they're confirmed paid. PENDING
# is a cart-abandonment risk; CANCELLED/REFUNDED clearly shouldn't count.
_BESTSELLER_ORDER_STATUSES = (
    OrderStatus.PAID,
    OrderStatus.SHIPPED,
    OrderStatus.DELIVERED,
)


class ProductRepository(BaseRepository[Product]):
    model = Product

    def get_by_sku(self, sku: str) -> Product | None:
        return self.db.execute(select(Product).where(Product.sku == sku)).scalar_one_or_none()

    def get_with_images(self, product_id: int) -> Product | None:
        stmt = (
            select(Product)
            .options(selectinload(Product.images))
            .where(Product.id == product_id)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    # sort_by → ORDER BY clauses. "newest" keeps id desc as a tiebreaker so
    # products created in the same second still page deterministically.
    _SORTS = {
        "newest": (Product.created_at.desc(), Product.id.desc()),
        "price_asc": (Product.price.asc(), Product.id.desc()),
        "price_desc": (Product.price.desc(), Product.id.desc()),
        "rating": (Product.rating_avg.desc(), Product.rating_count.desc(), Product.id.desc()),
    }

    def search(
        self,
        *,
        q: str | None = None,
        category_id: int | None = None,
        category_ids: list[int] | None = None,
        flavours: list[str] | None = None,
        min_price: Decimal | None = None,
        max_price: Decimal | None = None,
        min_rating: Decimal | None = None,
        in_stock: bool | None = None,
        is_combo: bool | None = None,
        discounted: bool | None = None,
        sort_by: str = "newest",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Product], int]:
        conditions = []
        if q:
            conditions.append(Product.name.ilike(f"%{q}%"))
        if category_id is not None:
            conditions.append(Product.category_id == category_id)
        if category_ids:
            conditions.append(Product.category_id.in_(category_ids))
        if flavours:
            conditions.append(Product.flavour.in_(flavours))
        if min_price is not None:
            conditions.append(Product.price >= min_price)
        if max_price is not None:
            conditions.append(Product.price <= max_price)
        if min_rating is not None:
            # rating_count guard: unrated products default to avg 0 and must
            # not slip through a "0★ & above" style filter as false positives.
            conditions.append(Product.rating_avg >= min_rating)
            conditions.append(Product.rating_count > 0)
        if in_stock:
            conditions.append(Product.stock > 0)
        if is_combo is not None:
            conditions.append(Product.is_combo == is_combo)
        if discounted:
            conditions.append(Product.compare_at_price.isnot(None))
            conditions.append(Product.compare_at_price > Product.price)

        stmt = select(Product).options(selectinload(Product.images))
        count_stmt = select(func.count()).select_from(Product)
        if conditions:
            stmt = stmt.where(*conditions)
            count_stmt = count_stmt.where(*conditions)

        order_by = self._SORTS.get(sort_by, self._SORTS["newest"])
        total = self.db.execute(count_stmt).scalar_one()
        items = list(
            self.db.execute(stmt.order_by(*order_by).offset(offset).limit(limit))
            .scalars()
            .all()
        )
        return items, total

    def decrement_stock(self, product: Product, qty: int) -> None:
        # Atomic conditional UPDATE prevents TOCTOU oversell under concurrent
        # requests. The WHERE stock >= qty ensures we never go negative; 0 rows
        # updated means insufficient stock at the moment of write.
        result = self.db.execute(
            update(Product)
            .where(Product.id == product.id, Product.stock >= qty)
            .values(stock=Product.stock - qty)
            .execution_options(synchronize_session="fetch")
        )
        if result.rowcount == 0:
            from app.core.exceptions import ConflictError
            raise ConflictError(f"Insufficient stock for product {product.sku}")
        self.db.flush()

    def increment_stock(self, product: Product, qty: int) -> None:
        # Atomic counterpart to decrement_stock. Restock paths (cancellation,
        # refund, payment failure) must NOT read-modify-write product.stock in
        # Python: a concurrent sale's atomic decrement committing in between
        # would be clobbered by the stale restore, silently inflating stock and
        # causing oversell. Expression-based UPDATE keeps both operations atomic.
        self.db.execute(
            update(Product)
            .where(Product.id == product.id)
            .values(stock=Product.stock + qty)
            .execution_options(synchronize_session="fetch")
        )
        self.db.flush()

    def bestsellers(self, *, limit: int, category_id: int | None = None) -> list[Product]:
        """Top products by units sold across paid/shipped/delivered orders.

        Optionally scoped to a single `category_id` — used by the product detail
        page's "Customers are likely to buy" rail, which prefers items trending
        in the same category. Returns rows ordered by units sold desc, or [] if
        no qualifying orders exist yet (caller picks a fallback).
        """
        sold_qty = func.sum(OrderItem.quantity).label("sold_qty")
        ranking = (
            select(OrderItem.product_id, sold_qty)
            .join(Order, Order.id == OrderItem.order_id)
            .where(Order.status.in_(_BESTSELLER_ORDER_STATUSES))
            .group_by(OrderItem.product_id)
            .order_by(sold_qty.desc())
            .limit(limit)
            .subquery()
        )

        stmt = (
            select(Product)
            .join(ranking, ranking.c.product_id == Product.id)
            .options(selectinload(Product.images))
        )
        if category_id is not None:
            stmt = stmt.where(Product.category_id == category_id)
        stmt = stmt.order_by(ranking.c.sold_qty.desc())
        return list(self.db.execute(stmt).scalars().all())

    def co_purchased(self, *, product_id: int, limit: int) -> list[Product]:
        """Products purchased alongside `product_id` in the same orders.

        Ranked by how often each pair shows up. Only counts paid/shipped/
        delivered orders so abandoned carts don't pollute the signal.
        Returns [] if `product_id` has never been ordered — caller falls back.
        """
        OtherItem = aliased(OrderItem)
        co_count = func.count(OtherItem.id).label("co_count")
        ranking = (
            select(OtherItem.product_id, co_count)
            .join(OrderItem, OrderItem.order_id == OtherItem.order_id)
            .join(Order, Order.id == OtherItem.order_id)
            .where(
                and_(
                    OrderItem.product_id == product_id,
                    OtherItem.product_id != product_id,
                    Order.status.in_(_BESTSELLER_ORDER_STATUSES),
                )
            )
            .group_by(OtherItem.product_id)
            .order_by(co_count.desc())
            .limit(limit)
            .subquery()
        )

        stmt = (
            select(Product)
            .join(ranking, ranking.c.product_id == Product.id)
            .options(selectinload(Product.images))
            .order_by(ranking.c.co_count.desc())
        )
        return list(self.db.execute(stmt).scalars().all())

    def by_ids(self, ids: list[int]) -> list[Product]:
        """Bulk-fetch products by id list, preserving the input order.

        Used by the browsing-history rail — the client sends the last-viewed
        ids and expects them back in the same order they were viewed.
        """
        if not ids:
            return []
        stmt = (
            select(Product)
            .options(selectinload(Product.images))
            .where(Product.id.in_(ids))
        )
        rows = list(self.db.execute(stmt).scalars().all())
        by_id = {p.id: p for p in rows}
        return [by_id[i] for i in ids if i in by_id]

    def newest(self, *, limit: int) -> list[Product]:
        stmt = (
            select(Product)
            .options(selectinload(Product.images))
            .order_by(Product.created_at.desc(), Product.id.desc())
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())
