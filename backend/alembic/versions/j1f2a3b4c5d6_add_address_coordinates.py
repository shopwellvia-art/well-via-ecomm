"""add latitude and longitude columns to addresses

Persists GPS coordinates captured by the map picker / browser geolocation API.

  - `latitude`  (DOUBLE NULL) — WGS-84 latitude  in decimal degrees.
  - `longitude` (DOUBLE NULL) — WGS-84 longitude in decimal degrees.

MySQL DOUBLE (8-byte IEEE 754) is used rather than FLOAT (4-byte) to preserve
the ~7 decimal-place precision needed for sub-metre GPS accuracy.

Columns are nullable: addresses entered manually will never have coordinates.
Nothing reads them for fulfilment (pincode drives shipping zone lookup); they
flow into order snapshots as plain floats for display / analytics use.

No index is added — nothing queries by coordinates.

Downgrade: drop both columns (no FK/index to clean up first).

Revision ID: j1f2a3b4c5d6
Revises: i0e1f2a3b4c5
Create Date: 2026-06-12 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "j1f2a3b4c5d6"
down_revision: Union[str, None] = "i0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # latitude: WGS-84 decimal degrees, nullable, no index.
    op.add_column(
        "addresses",
        sa.Column("latitude", sa.Double(), nullable=True),
    )
    # longitude: WGS-84 decimal degrees, nullable, no index.
    op.add_column(
        "addresses",
        sa.Column("longitude", sa.Double(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("addresses", "longitude")
    op.drop_column("addresses", "latitude")
