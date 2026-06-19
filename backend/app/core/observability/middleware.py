"""Request-timing middleware.

Outermost user middleware so it measures the full request (all inner
middleware + routing + handler). It sets the per-request collector *before*
``call_next`` so the SQLAlchemy listeners can find it, then on completion builds
one telemetry record and hands it to the (non-blocking) buffer.

It must never affect the response: all telemetry work happens in a guarded
``finally`` and any failure is swallowed.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from time import perf_counter

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.config import settings
from app.core.observability.buffer import RequestRecord, telemetry_buffer
from app.core.observability.collector import (
    RequestCollector,
    reset_collector,
    set_collector,
)

logger = logging.getLogger(__name__)

# Paths we never record: infra probes, static media, docs, and the
# observability API itself (so the dashboard doesn't observe its own reads).
_SKIP_PREFIXES = (
    "/health",
    "/ready",
    "/media",
    "/docs",
    "/redoc",
    "/openapi",
    f"{settings.API_V1_PREFIX}/observability",
)

# For unmatched paths (404s, etc.) collapse high-cardinality segments so the
# route dimension can't explode. Matched routes use the template instead.
_NUM_SEG = re.compile(r"/\d+")
_UUID_SEG = re.compile(
    r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _should_skip(request: Request) -> bool:
    if request.method == "OPTIONS":  # CORS preflight noise
        return True
    path = request.url.path
    return path.startswith(_SKIP_PREFIXES)


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if template:
        return template[:255]
    # No matched route (e.g. 404): normalize the raw path.
    path = _UUID_SEG.sub("/:id", request.url.path)
    path = _NUM_SEG.sub("/:id", path)
    return path[:255] or "/"


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    if request.client:
        return request.client.host[:64]
    return None


class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _should_skip(request):
            return await call_next(request)

        collector = RequestCollector()
        token = set_collector(collector)
        ts = datetime.now(timezone.utc)
        start = perf_counter()
        status_code = 500  # if call_next raises, it became a 500 upstream
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            try:
                total_ms = int((perf_counter() - start) * 1000)
                telemetry_buffer.offer(
                    RequestRecord(
                        ts=ts,
                        method=request.method[:8],
                        route=_route_template(request),
                        status=status_code,
                        total_ms=total_ms,
                        db_ms=int(collector.db_ms),
                        query_count=collector.query_count,
                        user_id=getattr(request.state, "obs_user_id", None),
                        request_id=getattr(request.state, "request_id", None),
                        ip=_client_ip(request),
                        slow=collector.slow,
                    )
                )
            except Exception:  # noqa: BLE001 — telemetry must never break a response
                logger.debug("obs: failed to record request", exc_info=True)
            finally:
                reset_collector(token)
