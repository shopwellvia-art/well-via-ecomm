"""Superadmin-only database maintenance.

Gated by require_admin — i.e. the legacy full-access (is_admin=True) tier, which
the product treats as "superadmin". Scoped RBAC staff cannot reach this even
holding every granular permission, because require_admin checks is_admin
directly rather than going through has_permission().
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_admin
from app.core.config import settings
from app.core.exceptions import ValidationError
from app.core.rate_limit import get_client_ip
from app.models.audit import AuditEvent
from app.models.user import User
from app.schemas.database_admin import TruncateRequest, TruncateResponse
from app.services.database_admin_service import CONFIRM_PHRASE, truncate_all_and_reseed

router = APIRouter()


@router.post("/truncate", response_model=TruncateResponse)
def truncate_database(
    payload: TruncateRequest,
    request: Request,
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> TruncateResponse:
    """Wipe every application table and re-seed the bootstrap admin + RBAC.

    Destructive and irreversible. The caller's own user row is wiped, so their
    session dies — the client must clear local auth and prompt a fresh sign-in.
    """
    if payload.confirm != CONFIRM_PHRASE:
        raise ValidationError(f'Type "{CONFIRM_PHRASE}" exactly to confirm.')

    # Capture identity now — the wipe deletes the actor's own user row.
    actor_email = actor.email
    actor_ip = get_client_ip(request)

    result = truncate_all_and_reseed(db)

    # The audit table was truncated too. Re-open the log with this event so the
    # destructive action is on record going forward. The original actor row is
    # gone (actor_user_id=None); the denormalized email preserves who did it.
    db.add(
        AuditEvent(
            actor_user_id=None,
            actor_email=actor_email,
            actor_ip=actor_ip,
            action="database.truncate",
            target_type="database",
            target_label=settings.MYSQL_DB,
            summary=f"Truncated all tables and re-seeded admin; triggered by {actor_email}",
            extra=result,
        )
    )
    db.commit()
    return TruncateResponse(**result)
