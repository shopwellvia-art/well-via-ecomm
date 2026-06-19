"""Per-request telemetry collector held in a contextvar.

The collector is created by ``TimingMiddleware`` at the start of each request
and stored in a :class:`contextvars.ContextVar`. The SQLAlchemy instrumentation
(:mod:`app.core.observability.instrumentation`) reads it on every query to
accumulate DB time and capture slow queries.

Why a contextvar: the middleware sets it *before* ``call_next``, so the value
propagates into the child task and — for sync endpoints — into the threadpool
thread that anyio copies the context into. That lets the SQLAlchemy listeners
(which have no access to the request object) find the right collector.

Crucially, code that runs *outside* a request — the background flush thread and
app startup — has no collector set, so the listeners no-op there. That is the
recursion guard: the telemetry writer's own INSERTs are never themselves
recorded, with no need for a separate engine.
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field


@dataclass
class SlowQueryRecord:
    """A single query that exceeded the slow-query threshold during a request."""

    sql_normalized: str
    fingerprint_hash: str
    table_name: str | None
    operation: str | None
    duration_ms: int


@dataclass
class RequestCollector:
    """Mutable accumulator for one in-flight request."""

    db_ms: float = 0.0
    query_count: int = 0
    slow: list[SlowQueryRecord] = field(default_factory=list)


# Default None — only set inside an instrumented request. The instrumentation
# treats "no collector" as "do not record", which is what we want everywhere
# else (startup, the flush thread, skipped paths).
_collector: contextvars.ContextVar[RequestCollector | None] = contextvars.ContextVar(
    "obs_collector", default=None
)


def get_collector() -> RequestCollector | None:
    return _collector.get()


def set_collector(collector: RequestCollector | None) -> contextvars.Token:
    return _collector.set(collector)


def reset_collector(token: contextvars.Token) -> None:
    _collector.reset(token)
