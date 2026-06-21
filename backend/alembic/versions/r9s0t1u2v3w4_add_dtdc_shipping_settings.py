"""seed DTDC + warehouse shipping settings into system_settings

Adds the 11 new keys required by the DTDC carrier integration (Shipsy API)
and the additional warehouse origin fields it needs (city, state, phone).
No schema change — data-only migration.

Revision ID: r9s0t1u2v3w4
Revises: q8r9s0t1u2v3
Create Date: 2026-06-21 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "r9s0t1u2v3w4"
down_revision: Union[str, None] = "q8r9s0t1u2v3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (key, default_value, description, is_secret_int)
_SEED = [
    ("shipping.dtdc.api_key", "",
     "DTDC api-key for order upload / label / cancel (Shipsy)", 1),
    ("shipping.dtdc.customer_code", "",
     "DTDC customer code (sent in softdata body + cancel)", 0),
    ("shipping.dtdc.tracking_username", "",
     "DTDC tracking API username (dtdc.com)", 0),
    ("shipping.dtdc.tracking_password", "",
     "DTDC tracking API password (dtdc.com)", 1),
    ("shipping.dtdc.service_type_id", "B2C PRIORITY",
     "DTDC service type id (e.g. B2C PRIORITY)", 0),
    ("shipping.dtdc.load_type", "NON-DOCUMENT",
     "DTDC load type (NON-DOCUMENT or DOCUMENT)", 0),
    ("shipping.dtdc.commodity_id", "99",
     "DTDC commodity id", 0),
    ("shipping.dtdc.label_code", "SHIP_LABEL_4X6",
     "DTDC label code (SHIP_LABEL_4X6 / SHIP_LABEL_A4 / ...)", 0),
    ("shipping.warehouse.city", "",
     "Pickup warehouse city (required by DTDC origin_details)", 0),
    ("shipping.warehouse.state", "",
     "Pickup warehouse state (required by DTDC origin_details)", 0),
    ("shipping.warehouse.phone", "",
     "Pickup warehouse contact phone (printed on labels)", 0),
]


def upgrade() -> None:
    for key, value, description, is_secret in _SEED:
        op.execute(
            "INSERT INTO system_settings (`key`, value, category, description, is_secret) "
            f"VALUES (:k, :v, 'shipping', :d, {int(is_secret)}) "
            "ON DUPLICATE KEY UPDATE `key` = `key`"
            .replace(":k", repr(key))
            .replace(":v", repr(value))
            .replace(":d", repr(description))
        )


def downgrade() -> None:
    op.execute(
        "DELETE FROM system_settings WHERE `key` IN ("
        "'shipping.dtdc.api_key',"
        "'shipping.dtdc.customer_code',"
        "'shipping.dtdc.tracking_username',"
        "'shipping.dtdc.tracking_password',"
        "'shipping.dtdc.service_type_id',"
        "'shipping.dtdc.load_type',"
        "'shipping.dtdc.commodity_id',"
        "'shipping.dtdc.label_code',"
        "'shipping.warehouse.city',"
        "'shipping.warehouse.state',"
        "'shipping.warehouse.phone'"
        ")"
    )
