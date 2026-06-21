"""Admin email/SMS template management.  Permission: settings.manage."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission
from app.core.exceptions import AppError
from app.core.rate_limit import get_client_ip
from app.models.user import User
from app.schemas.email_template import (
    EmailTemplateDetail,
    EmailTemplateListResponse,
    EmailTemplatePreviewRequest,
    EmailTemplatePreviewResponse,
    EmailTemplateTestRequest,
    EmailTemplateUpdate,
)
from app.services.email_templates.catalog import VARIABLES
from app.services.email_templates.seed import get_default
from app.services.email_templates.service import EmailTemplateService

router = APIRouter()


def _to_detail(row, svc: EmailTemplateService | None = None) -> EmailTemplateDetail:
    """Convert an ORM row to EmailTemplateDetail, injecting catalog extras."""
    default = get_default(row.key) or {}
    return EmailTemplateDetail(
        key=row.key,
        channel=row.channel,
        name=row.name,
        description=row.description,
        group_name=row.group_name,
        subject=row.subject,
        is_enabled=row.is_enabled,
        updated_at=row.updated_at,
        body_html=row.body_html,
        body_design=row.body_design,
        variables=VARIABLES.get(row.key, []),
        default_subject=default.get("subject"),
        default_body_html=default.get("body_html"),
    )


@router.get(
    "",
    response_model=EmailTemplateListResponse,
    dependencies=[Depends(require_permission("settings.manage"))],
)
def list_templates(db: Session = Depends(get_db)):
    svc = EmailTemplateService(db)
    rows = svc.list()
    return EmailTemplateListResponse(items=rows)


@router.get(
    "/{key}",
    response_model=EmailTemplateDetail,
    dependencies=[Depends(require_permission("settings.manage"))],
)
def get_template(key: str, db: Session = Depends(get_db)):
    svc = EmailTemplateService(db)
    row = svc.get_detail(key)
    return _to_detail(row)


@router.put(
    "/{key}",
    response_model=EmailTemplateDetail,
)
def update_template(
    key: str,
    payload: EmailTemplateUpdate,
    request: Request,
    actor: User = Depends(require_permission("settings.manage")),
    db: Session = Depends(get_db),
):
    svc = EmailTemplateService(db)
    row = svc.update(key, payload, actor, actor_ip=get_client_ip(request))
    return _to_detail(row)


@router.post(
    "/{key}/preview",
    response_model=EmailTemplatePreviewResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("settings.manage"))],
)
def preview_template(
    key: str,
    payload: EmailTemplatePreviewRequest,
    db: Session = Depends(get_db),
):
    svc = EmailTemplateService(db)
    rendered_subject, rendered_html = svc.preview(
        key,
        draft_subject=payload.subject,
        draft_body_html=payload.body_html,
    )
    return EmailTemplatePreviewResponse(subject=rendered_subject, html=rendered_html)


@router.post(
    "/{key}/test",
    status_code=status.HTTP_200_OK,
)
def send_test(
    key: str,
    payload: EmailTemplateTestRequest,
    actor: User = Depends(require_permission("settings.manage")),
    db: Session = Depends(get_db),
):
    svc = EmailTemplateService(db)
    try:
        svc.send_test(key, str(payload.to), actor)
    except Exception as exc:
        raise AppError(f"Send failed: {exc}") from exc
    return {"detail": f"Test email sent to {payload.to}."}


@router.post(
    "/{key}/reset",
    response_model=EmailTemplateDetail,
    status_code=status.HTTP_200_OK,
)
def reset_template(
    key: str,
    request: Request,
    actor: User = Depends(require_permission("settings.manage")),
    db: Session = Depends(get_db),
):
    svc = EmailTemplateService(db)
    row = svc.reset(key, actor, actor_ip=get_client_ip(request))
    return _to_detail(row)
