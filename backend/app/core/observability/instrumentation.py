"""SQLAlchemy engine instrumentation: per-query timing + slow-query capture.

Attaches ``before_cursor_execute`` / ``after_cursor_execute`` listeners to the
shared engine. Both listeners short-circuit when there is no active request
collector (startup, the flush thread, skipped paths) — that's the recursion
guard: the telemetry writer's own INSERTs are never recorded.
"""
from __future__ import annotations

import logging
import time

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.core.config import settings
from app.core.observability.collector import SlowQueryRecord, get_collector
from app.core.observability.sql import extract_table, normalize_sql

logger = logging.getLogger(__name__)

_REGISTERED: set[int] = set()


def register_engine_instrumentation(engine: Engine) -> None:
    """Idempotently attach timing listeners to ``engine``."""
    if id(engine) in _REGISTERED:
        return
    _REGISTERED.add(id(engine))

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        # Only time queries that belong to an instrumented request.
        if get_collector() is None:
            return
        context._obs_start = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):
        collector = get_collector()
        if collector is None:
            return
        start = getattr(context, "_obs_start", None)
        if start is None:
            return
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        collector.db_ms += elapsed_ms
        collector.query_count += 1

        if elapsed_ms < settings.OBS_SLOW_QUERY_MS:
            return
        # Capture the slow query — normalized only, never raw literals.
        try:
            normalized, fingerprint, operation = normalize_sql(statement)
            collector.slow.append(
                SlowQueryRecord(
                    sql_normalized=normalized[:4000],
                    fingerprint_hash=fingerprint,
                    table_name=extract_table(statement),
                    operation=operation,
                    duration_ms=int(elapsed_ms),
                )
            )
        except Exception:  # noqa: BLE001 — instrumentation must never break a query
            logger.debug("obs: failed to capture slow query", exc_info=True)
