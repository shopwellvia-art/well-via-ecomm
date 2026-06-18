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
from app.db.session import SessionLocal
from app.services.rbac_seed import seed_rbac

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    # Bootstrap RBAC. Idempotent so it's safe on every boot. Failures don't
    # abort startup — the API still works; the admin just sees no perms.
    try:
        with SessionLocal() as db:
            seed_rbac(db)
    except Exception as exc:
        logger.warning("RBAC seed skipped: %s", exc)
    yield


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
        return {"status": "ok"}

    @app.get("/ready", tags=["system"])
    def ready():
        return {"status": "ready"}

    return app


app = create_app()
