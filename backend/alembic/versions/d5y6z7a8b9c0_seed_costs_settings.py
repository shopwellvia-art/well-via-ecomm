"""seed costs.* system settings for contribution-margin analytics

Adds five admin-editable settings (category='costs') that the ProfitService
reads to compute C1/C2/C3/Net Profit.  All values are stored as strings
(numeric content) with a safe default of "0" so the feature degrades
gracefully on a fresh install — zero overheads means the margin report only
reflects product/shipping/gateway costs until the admin fills these in.

Revision ID: d5y6z7a8b9c0
Revises: c4x5y6z7a8b9
Create Date: 2026-06-06 01:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d5y6z7a8b9c0"
down_revision: Union[str, None] = "c4x5y6z7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (key, default_value, description)
_COSTS_SETTINGS = [
    (
        "costs.packing_per_order",
        "0",
        "Packing material cost per dispatched order (₹). Added to C1 cost stack.",
    ),
    (
        "costs.handling_per_order",
        "0",
        "Warehouse handling / labour cost per order (₹). Added to C1 cost stack.",
    ),
    (
        "costs.gateway_fee_pct",
        "0",
        "Payment gateway fee as a percentage of prepaid order value (e.g. 2 = 2%). COD orders are exempt.",
    ),
    (
        "costs.monthly_overheads",
        "0",
        "Fixed monthly overhead costs — rent, salaries, SaaS etc. (₹). Pro-rated to the analytics window.",
    ),
    (
        "costs.monthly_ad_spend",
        "0",
        "Total monthly advertising / marketing spend (₹). Pro-rated to the analytics window for C3.",
    ),
]


def upgrade() -> None:
    for key, value, description in _COSTS_SETTINGS:
        op.execute(
            "INSERT INTO system_settings (`key`, value, category, description, is_secret) "
            f"VALUES (:k, :v, 'costs', :d, 0)"
            .replace(":k", repr(key))
            .replace(":v", repr(value))
            .replace(":d", repr(description))
        )


def downgrade() -> None:
    op.execute("DELETE FROM system_settings WHERE category = 'costs'")
