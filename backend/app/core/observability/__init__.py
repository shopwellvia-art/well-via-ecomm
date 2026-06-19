"""Self-hosted observability / APM telemetry pipeline.

Collection (hot path): ``TimingMiddleware`` + SQLAlchemy listeners capture
per-request and per-query timing into a contextvar-scoped collector.
Buffering (off hot path): a bounded queue drained by a background flush thread
bulk-inserts into MySQL and prunes old rows. Reads are served by
``ObservabilityService`` / the ``/observability`` endpoints.
"""
from app.core.observability.buffer import telemetry_buffer
from app.core.observability.instrumentation import register_engine_instrumentation
from app.core.observability.middleware import TimingMiddleware

__all__ = ["telemetry_buffer", "register_engine_instrumentation", "TimingMiddleware"]
