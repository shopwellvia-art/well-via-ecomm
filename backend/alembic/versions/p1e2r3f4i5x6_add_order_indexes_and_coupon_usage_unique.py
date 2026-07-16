"""add order hot-path indexes and coupon-usage uniqueness

Two production-readiness fixes (see the audit):
  * orders.created_at was unindexed despite driving the admin list sort, the
    date-range filters and the reconcile cron's `created_at < cutoff` scan —
    the default admin view was a full-table filesort that worsens as orders
    accumulate. Adds a plain index plus (status, created_at) and
    (user_id, created_at) composites for the cron and the customer history.
  * coupon_usages had no uniqueness, so a double-settlement (now also blocked by
    row locking in _apply_status) could record a coupon twice for one order.
    A unique (coupon_id, order_id) is the backstop; NULL order_ids stay distinct.

NOTE: the shared remote MySQL is applied MANUALLY from reviewed SQL, never via a
blind `alembic upgrade head` (its lineage diverges from this repo). The raw DDL
for that manual window lives in backend/scripts/sql/ alongside this revision.

Revision ID: p1e2r3f4i5x6
Revises: t1u2v3w4x5y6
Create Date: 2026-07-16 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "p1e2r3f4i5x6"
down_revision: Union[str, None] = "t1u2v3w4x5y6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_orders_created_at", "orders", ["created_at"])
    op.create_index(
        "ix_orders_status_created_at", "orders", ["status", "created_at"]
    )
    op.create_index(
        "ix_orders_user_id_created_at", "orders", ["user_id", "created_at"]
    )
    op.create_unique_constraint(
        "uq_coupon_usages_coupon_order",
        "coupon_usages",
        ["coupon_id", "order_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_coupon_usages_coupon_order", "coupon_usages", type_="unique"
    )
    op.drop_index("ix_orders_user_id_created_at", table_name="orders")
    op.drop_index("ix_orders_status_created_at", table_name="orders")
    op.drop_index("ix_orders_created_at", table_name="orders")
