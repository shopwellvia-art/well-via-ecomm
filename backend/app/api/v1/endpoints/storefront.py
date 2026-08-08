"""Storefront configuration (singleton GET/PUT + image upload).

GET  /storefront-config        — public, no auth required.
PUT  /storefront-config        — requires the `frontend.manage` permission.
POST /storefront-config/image  — requires the `frontend.manage` permission.
"""
from fastapi import APIRouter, Depends, Request, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission
from app.core.rate_limit import get_client_ip
from app.models.user import User
from app.schemas.storefront import (
    ImageUploadResponse,
    StorefrontConfigRead,
    StorefrontConfigUpdate,
)
from app.services.audit_service import AuditService
from app.services.storefront_service import StorefrontConfigService

router = APIRouter()


@router.get("", response_model=StorefrontConfigRead)
def get_storefront_config(db: Session = Depends(get_db)):
    """Return the current storefront configuration.  Public — no authentication
    required so the storefront can fetch it without a token."""
    return StorefrontConfigService(db).get_config()


@router.put("", response_model=StorefrontConfigRead)
def update_storefront_config(
    payload: StorefrontConfigUpdate,
    request: Request,
    actor: User = Depends(require_permission("frontend.manage")),
    db: Session = Depends(get_db),
):
    """Replace the storefront configuration.  Requires `frontend.manage`."""
    # Flush the audit row before update_config so both land in the one commit
    # the service owns — an audit row exists iff the change committed. Nothing
    # in this document is secret.
    AuditService(db).record(
        actor=actor,
        actor_ip=get_client_ip(request),
        action="storefront_config.update",
        target_type="setting",
        target_label="storefront",
        summary="Updated storefront configuration",
    )
    return StorefrontConfigService(db).update_config(payload, actor=actor)


@router.post(
    "/image",
    response_model=ImageUploadResponse,
    dependencies=[Depends(require_permission("frontend.manage"))],
)
async def upload_storefront_image(file: UploadFile, db: Session = Depends(get_db)):
    """Upload a storefront image (logo / favicon) and return its public URL.
    The admin UI then stores the URL on the config via the normal PUT.
    Requires `frontend.manage`."""
    file_bytes = await file.read()
    url = StorefrontConfigService(db).upload_image(
        file_bytes=file_bytes,
        filename=file.filename or "image",
        content_type=file.content_type or "",
    )
    return {"url": url}
