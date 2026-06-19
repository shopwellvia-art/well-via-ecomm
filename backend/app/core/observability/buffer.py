"""Bounded in-process telemetry buffer + background flush thread.

Request records are pushed onto a bounded queue by the middleware (a cheap,
non-blocking ``put_nowait``) and drained in batches by a daemon thread that
bulk-inserts them into MySQL. This keeps every database write off the request
hot path.

Design notes:
  - **Bounded + lossy under pressure.** If the queue is full we drop the record
    and bump a counter rather than block the request — telemetry must never add
    latency or back-pressure to real traffic.
  - **Recursion-safe.** The flush thread runs with no request collector set, so
    the SQLAlchemy listeners ignore its own INSERT/DELETE statements.
  - **Per-worker.** With multiple uvicorn workers each has its own buffer, but
    all flush to the same tables, so the dashboards aggregate correctly. Only a
    small unflushed window per worker is at risk on a hard crash.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.observability.collector import SlowQueryRecord

logger = logging.getLogger(__name__)

_PRUNE_INTERVAL_SEC = 3600.0  # retention sweep cadence
_FLUSH_BATCH_MAX = 500


@dataclass
class RequestRecord:
    """One finished request, ready to be persisted."""

    ts: datetime
    method: str
    route: str
    status: int
    total_ms: int
    db_ms: int
    query_count: int
    user_id: int | None = None
    request_id: str | None = None
    ip: str | None = None
    slow: list[SlowQueryRecord] = field(default_factory=list)


class TelemetryBuffer:
    def __init__(self, maxsize: int) -> None:
        self._q: queue.Queue[RequestRecord] = queue.Queue(maxsize=maxsize)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.dropped = 0

    # ---- producer side (hot path) ----

    def offer(self, record: RequestRecord) -> None:
        try:
            self._q.put_nowait(record)
        except queue.Full:
            self.dropped += 1

    # ---- lifecycle ----

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="obs-flush", daemon=True
        )
        self._thread.start()
        logger.info("obs: telemetry flush thread started")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        # Final drain so we don't lose the last partial batch on shutdown.
        batch = self._drain(_FLUSH_BATCH_MAX * 4, wait=0.0)
        if batch:
            self._flush(batch)

    # ---- consumer side (background thread) ----

    def _run(self) -> None:
        last_prune = time.monotonic()
        while not self._stop.is_set():
            batch = self._drain(_FLUSH_BATCH_MAX, wait=settings.OBS_FLUSH_INTERVAL_SEC)
            if batch:
                self._flush(batch)
            now = time.monotonic()
            if now - last_prune >= _PRUNE_INTERVAL_SEC:
                self._prune()
                last_prune = now

    def _drain(self, max_items: int, wait: float) -> list[RequestRecord]:
        items: list[RequestRecord] = []
        try:
            items.append(self._q.get(timeout=wait if wait > 0 else 0.05))
        except queue.Empty:
            return items
        for _ in range(max_items - 1):
            try:
                items.append(self._q.get_nowait())
            except queue.Empty:
                break
        return items

    def _flush(self, batch: list[RequestRecord]) -> None:
        # Imported here so the module is importable without eagerly pulling the
        # DB/ORM layer at process start.
        from app.db.session import SessionLocal
        from app.models.observability import RequestLog, SlowQuery

        session = SessionLocal()
        try:
            objs: list[object] = []
            for rec in batch:
                objs.append(
                    RequestLog(
                        ts=rec.ts,
                        method=rec.method,
                        route=rec.route,
                        status=rec.status,
                        total_ms=rec.total_ms,
                        db_ms=rec.db_ms,
                        query_count=rec.query_count,
                        user_id=rec.user_id,
                        request_id=rec.request_id,
                        ip=rec.ip,
                    )
                )
                for sq in rec.slow:
                    objs.append(
                        SlowQuery(
                            ts=rec.ts,
                            request_id=rec.request_id,
                            route=rec.route,
                            fingerprint_hash=sq.fingerprint_hash,
                            sql_normalized=sq.sql_normalized,
                            table_name=sq.table_name,
                            operation=sq.operation,
                            duration_ms=sq.duration_ms,
                        )
                    )
            session.add_all(objs)
            session.commit()
        except Exception:  # noqa: BLE001 — a flush failure must not crash the thread
            session.rollback()
            logger.warning("obs: telemetry flush failed (%d records)", len(batch), exc_info=True)
        finally:
            session.close()

    def _prune(self) -> None:
        from app.db.session import SessionLocal
        from app.models.observability import RequestLog, SlowQuery

        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.OBS_RETENTION_DAYS)
        session = SessionLocal()
        try:
            session.query(RequestLog).filter(RequestLog.ts < cutoff).delete(
                synchronize_session=False
            )
            session.query(SlowQuery).filter(SlowQuery.ts < cutoff).delete(
                synchronize_session=False
            )
            session.commit()
        except Exception:  # noqa: BLE001
            session.rollback()
            logger.warning("obs: retention prune failed", exc_info=True)
        finally:
            session.close()


# Module-level singleton — created from settings, shared by the middleware
# (producer) and the app lifespan (start/stop).
telemetry_buffer = TelemetryBuffer(maxsize=settings.OBS_BUFFER_MAX)
