import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


# Paths whose responses must keep a relaxed CSP so the interactive API docs
# (Swagger UI / ReDoc) can load their own CSS/JS. These are only served in
# non-production anyway (see create_app), but we exclude them defensively.
_CSP_EXEMPT_PREFIXES = ("/docs", "/redoc")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline security response headers on every response.

    Defense in depth alongside the nginx headers — nginx may be absent in some
    deployments (e.g. running uvicorn directly), so the app sets its own. We use
    setdefault semantics (only set when absent) so a more specific handler — or
    nginx — can still override per-route.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        headers = response.headers
        if "X-Content-Type-Options" not in headers:
            headers["X-Content-Type-Options"] = "nosniff"
        if "X-Frame-Options" not in headers:
            headers["X-Frame-Options"] = "DENY"
        if "Referrer-Policy" not in headers:
            headers["Referrer-Policy"] = "no-referrer"
        # Harmless over plain HTTP (browsers ignore it); takes effect once TLS
        # terminates in front of the app.
        if "Strict-Transport-Security" not in headers:
            headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        # The API only ever returns JSON, and same-origin uploads under /media
        # are additionally sandboxed at nginx. A strict CSP here neutralises any
        # accidental HTML/script response. Docs UIs need a relaxed policy.
        path = request.url.path
        if not path.startswith(_CSP_EXEMPT_PREFIXES) and "Content-Security-Policy" not in headers:
            headers["Content-Security-Policy"] = (
                "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
            )
        return response
