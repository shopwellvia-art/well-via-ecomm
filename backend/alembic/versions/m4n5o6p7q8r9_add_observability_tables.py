"""add observability / APM tables: obs_request_logs and obs_slow_queries

Creates two append-only telemetry tables for the Observability/APM feature.
Rows are written off the request hot path by a background flush thread and
pruned by a retention job — no FK constraints are added so these tables stay
fully decoupled from the rest of the schema.

obs_request_logs — one row per HTTP request:
  * BigInteger PK (high-volume; Integer wraps at ~2 B rows)
  * ts set explicitly by the writer at request-start time (UTC)
  * Indexes: (ts), (route, ts), (status, ts)

obs_slow_queries — one row per query over the slow-query threshold:
  * BigInteger PK
  * sql_normalized MUST NOT contain raw literal values (replaced by '?')
  * fingerprint_hash is md5 hex of sql_normalized for aggregation
  * Indexes: (ts), (fingerprint_hash, ts), (table_name, ts)

Downgrade drops indexes then tables in reverse creation order.

Revision ID: m4n5o6p7q8r9
Revises: l3m4n5o6p7q8
Create Date: 2026-06-19 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "m4n5o6p7q8r9"
down_revision: Union[str, None] = "l3m4n5o6p7q8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Table: obs_request_logs
    # ------------------------------------------------------------------
    op.create_table(
        "obs_request_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        # ts: request start time UTC; set by writer, no server_default.
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("route", sa.String(length=255), nullable=False),
        sa.Column("status", sa.SmallInteger(), nullable=False),
        sa.Column("total_ms", sa.Integer(), nullable=False),
        sa.Column("db_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "query_count",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        # user_id: no FK — telemetry must survive independent of the users table.
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # Named indexes on obs_request_logs
    op.create_index("ix_obs_req_ts", "obs_request_logs", ["ts"], unique=False)
    op.create_index(
        "ix_obs_req_route_ts", "obs_request_logs", ["route", "ts"], unique=False
    )
    op.create_index(
        "ix_obs_req_status_ts", "obs_request_logs", ["status", "ts"], unique=False
    )

    # ------------------------------------------------------------------
    # Table: obs_slow_queries
    # ------------------------------------------------------------------
    op.create_table(
        "obs_slow_queries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        # ts: query event time UTC; set by writer, no server_default.
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        # Correlation back to the HTTP request that triggered this query.
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("route", sa.String(length=255), nullable=True),
        # Query identity — sql_normalized NEVER contains raw literal values.
        # fingerprint_hash is md5 hex of sql_normalized.
        sa.Column("fingerprint_hash", sa.String(length=32), nullable=False),
        sa.Column("sql_normalized", sa.Text(), nullable=False),
        # Query classification parsed from sql_normalized.
        sa.Column("table_name", sa.String(length=128), nullable=True),
        sa.Column("operation", sa.String(length=16), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # Named indexes on obs_slow_queries
    op.create_index("ix_obs_sq_ts", "obs_slow_queries", ["ts"], unique=False)
    op.create_index(
        "ix_obs_sq_fp_ts",
        "obs_slow_queries",
        ["fingerprint_hash", "ts"],
        unique=False,
    )
    op.create_index(
        "ix_obs_sq_table_ts",
        "obs_slow_queries",
        ["table_name", "ts"],
        unique=False,
    )


def downgrade() -> None:
    # Drop in reverse creation order: indexes first, then tables.

    # obs_slow_queries indexes
    op.drop_index("ix_obs_sq_table_ts", table_name="obs_slow_queries")
    op.drop_index("ix_obs_sq_fp_ts", table_name="obs_slow_queries")
    op.drop_index("ix_obs_sq_ts", table_name="obs_slow_queries")

    # obs_slow_queries table
    op.drop_table("obs_slow_queries")

    # obs_request_logs indexes
    op.drop_index("ix_obs_req_status_ts", table_name="obs_request_logs")
    op.drop_index("ix_obs_req_route_ts", table_name="obs_request_logs")
    op.drop_index("ix_obs_req_ts", table_name="obs_request_logs")

    # obs_request_logs table
    op.drop_table("obs_request_logs")
