"""Append-only observability / APM telemetry tables.

These tables are written off the request hot path by a background flush thread
and pruned by a retention job.  Design constraints:

  - BigInteger PKs: both tables are expected to grow into the hundreds of
    millions of rows; Integer (31-bit) would overflow in a busy service.
  - No foreign keys on user_id or any other entity column: telemetry must stay
    fully decoupled so rows can be pruned, partitioned, or moved to a time-
    series store without touching application FK graphs.  Entity ids are stored
    as plain nullable integers, mirroring how audit.py keeps target_id loose.
  - No TimestampMixin / IDMixin: the PK type differs (BigInteger vs Integer)
    and these tables have no updated_at — they are append-only.
  - ts is set explicitly by the writer at request/query time (UTC); there is no
    server_default so the value reflects actual event time, not DB insert time.
  - SlowQuery.sql_normalized must NEVER contain raw literal values — only
    normalized SQL with literals replaced by '?'.  The fingerprint_hash (md5
    of the normalized SQL) allows aggregation across identical query shapes.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RequestLog(Base):
    """One row per HTTP request.

    Written by a background flush thread, not inline with the response.
    Pruned by a retention job — do not add FK constraints here.
    """

    __tablename__ = "obs_request_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Request start time in UTC.  Set explicitly by the writer — NOT a
    # server_default so the value reflects actual event time.
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    method: Mapped[str] = mapped_column(String(8), nullable=False)
    route: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    # Timing breakdown in milliseconds.
    total_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    db_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    query_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    # Actor — no FK, telemetry must survive independent of the users table.
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Correlation / diagnostics.
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_obs_req_ts", "ts"),
        Index("ix_obs_req_route_ts", "route", "ts"),
        Index("ix_obs_req_status_ts", "status", "ts"),
    )


class SlowQuery(Base):
    """One row per database query that exceeded the configured slow-query threshold.

    Written by a background flush thread, not inline with the response.
    sql_normalized must NEVER contain raw literal values — replace them with '?'
    before storing.  fingerprint_hash (md5 hex of sql_normalized) allows
    aggregating identical query shapes across many requests.
    """

    __tablename__ = "obs_slow_queries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Query event time in UTC.  Set explicitly by the writer.
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Correlation back to the HTTP request that triggered this query.
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    route: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Query identity — NEVER store raw SQL with literal values.
    fingerprint_hash: Mapped[str] = mapped_column(String(32), nullable=False)
    sql_normalized: Mapped[str] = mapped_column(Text, nullable=False)

    # Query classification parsed from sql_normalized.
    table_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    operation: Mapped[str | None] = mapped_column(String(16), nullable=True)

    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_obs_sq_ts", "ts"),
        Index("ix_obs_sq_fp_ts", "fingerprint_hash", "ts"),
        Index("ix_obs_sq_table_ts", "table_name", "ts"),
    )
