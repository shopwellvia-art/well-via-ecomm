import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestIDMiddleware, SecurityHeadersMiddleware
from app.core.observability import (
    TimingMiddleware,
    register_engine_instrumentation,
    telemetry_buffer,
)
from app.db.session import SessionLocal, engine
from app.services.email_templates.seed import seed_email_templates
from app.services.rbac_seed import seed_rbac
from app.services.settings_seed import seed_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    # Payment config sanity. CRITICAL, never fatal: a crash-loop here would
    # take the whole store down over a config mistake, with no remote rollback
    # once the deploy has already recreated the containers. Money movement is
    # protected separately — PaymentService.checkout refuses gateway payments
    # while the return URL is dev-shaped.
    from app.core.config import payment_config_problems

    for problem in payment_config_problems():
        logger.critical("PAYMENT CONFIG: %s", problem)
    # Bootstrap RBAC. Idempotent so it's safe on every boot. Failures don't
    # abort startup — the API still works; the admin just sees no perms.
    try:
        with SessionLocal() as db:
            seed_rbac(db)
            # Backfill any missing system_settings defaults (self-heals a DB
            # whose settings rows were wiped, e.g. by the superadmin truncate).
            seed_settings(db)
            # Backfill any missing email/SMS template defaults.
            seed_email_templates(db)
    except Exception as exc:
        logger.warning("RBAC seed skipped: %s", exc)
    # Start the observability flush thread (drains the in-process telemetry
    # buffer into MySQL off the request hot path).
    if settings.OBS_ENABLED:
        telemetry_buffer.start()
    try:
        yield
    finally:
        if settings.OBS_ENABLED:
            telemetry_buffer.stop()


def create_app() -> FastAPI:
    # Don't expose the interactive docs / OpenAPI schema in production — they
    # leak the full API surface to anonymous callers.
    is_prod = (settings.ENVIRONMENT or "").lower() == "production"
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        openapi_url=None if is_prod else f"{settings.API_V1_PREFIX}/openapi.json",
        docs_url=None if is_prod else "/docs",
        redoc_url=None if is_prod else "/redoc",
        lifespan=lifespan,
    )

    # Never combine a wildcard origin with credentials — browsers reject it and
    # it's a footgun (any site could ride a logged-in user's cookies). If the
    # config lists "*", drop credentials.
    allow_credentials = "*" not in settings.CORS_ORIGINS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    # NOTE: API-response gzip is done at nginx (frontend/nginx.conf, baked into
    # the frontend image), which correctly restricts it to text types. An
    # app-level GZipMiddleware here would also wrap the /media StaticFiles mount,
    # wastefully re-compressing image originals and breaking HTTP Range/206
    # requests — so compression is intentionally left to the proxy tier.

    # Observability: instrument the engine for per-query timing and add the
    # timing middleware last so it's outermost and measures the full request.
    if settings.OBS_ENABLED:
        register_engine_instrumentation(engine)
        app.add_middleware(TimingMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    # Serve locally-stored uploads. Mounted unconditionally so an admin can
    # switch the storage backend to local at runtime (Settings → Storage) and
    # still have /media served. With S3 selected this is simply unused —
    # images are served straight from the bucket / CDN.
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    app.mount("/media", StaticFiles(directory=settings.UPLOAD_DIR), name="media")

    @app.get("/health", tags=["system"])
    def health():
        # Liveness only: the process is up and serving. Cheap and dependency-free
        # so an orchestrator can tell "process alive" from "process wedged".
        return {"status": "ok"}

    @app.get("/version", tags=["system"])
    def version():
        # Build provenance baked into the image at build time (backend/Dockerfile
        # ARGs → env). Lets a deploy or operator confirm exactly which commit is
        # live without shell access:  curl http://localhost:8090/version
        return {
            "service": "backend",
            "version": settings.VERSION,
            "revision": settings.GIT_SHA,
            "built": settings.BUILD_TIME,
            "environment": settings.ENVIRONMENT,
        }

    @app.get("/ready", tags=["system"])
    def ready():
        # Readiness: can we actually serve a request? The likeliest production
        # failure is losing the network path to the remote MySQL, in which case
        # a static 200 would keep routing traffic to a backend that 500s every
        # request. Check MySQL and Redis with short timeouts; 503 on failure so
        # health-gated automation (compose/nginx/LB) can drain this instance.
        from fastapi.responses import JSONResponse
        from sqlalchemy import text

        checks: dict[str, str] = {}
        ok = True

        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["database"] = f"error: {type(exc).__name__}"
            ok = False

        try:
            from app.db.redis import get_redis

            get_redis().ping()
            checks["redis"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["redis"] = f"error: {type(exc).__name__}"
            ok = False

        body = {"status": "ready" if ok else "not_ready", "checks": checks}
        return JSONResponse(body, status_code=200 if ok else 503)

    return app


app = create_app()
