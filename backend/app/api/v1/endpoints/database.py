"""Superadmin-only database maintenance.

Gated by require_admin — i.e. the legacy full-access (is_admin=True) tier, which
the product treats as "superadmin". Scoped RBAC staff cannot reach this even
holding every granular permission, because require_admin checks is_admin
directly rather than going through has_permission().

Three operations:
  * GET  /admin/database/groups   — describe the domain groups + row counts.
  * POST /admin/database/truncate — wipe a domain group (or "everything").
  * POST /admin/database/seed     — load sample data into a domain group.
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_admin
from app.core.config import settings
from app.core.exceptions import ValidationError
from app.core.rate_limit import get_client_ip
from app.models.audit import AuditEvent
from app.models.user import User
from app.schemas.database_admin import (
    DatabaseGroupsResponse,
    SeedRequest,
    SeedResponse,
    TruncateRequest,
    TruncateResponse,
)
from app.services.database_admin_service import (
    CONFIRM_PHRASE,
    EVERYTHING_SCOPE,
    get_group,
    list_groups,
    seed_group,
    truncate_all_and_reseed,
    truncate_group,
)

router = APIRouter()


@router.get("/groups", response_model=DatabaseGroupsResponse)
def get_database_groups(
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> DatabaseGroupsResponse:
    """List the domain groups, each with its current row count, full blast
    radius, confirm phrase and whether it can be re-seeded with sample data."""
    return DatabaseGroupsResponse(groups=list_groups(db))


@router.post("/truncate", response_model=TruncateResponse)
def truncate_database(
    payload: TruncateRequest,
    request: Request,
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> TruncateResponse:
    """Wipe a domain group, or the whole database when scope == "everything".

    Destructive and irreversible. For the full wipe the caller's own user row is
    deleted (then the bootstrap admin is re-seeded), so their session dies and
    the client must clear local auth; scoped wipes leave the session intact.
    """
    actor_email = actor.email
    actor_ip = get_client_ip(request)

    if payload.scope == EVERYTHING_SCOPE:
        if payload.confirm != CONFIRM_PHRASE:
            raise ValidationError(f'Type "{CONFIRM_PHRASE}" exactly to confirm.')
        result = truncate_all_and_reseed(db)
        result.setdefault("scope", EVERYTHING_SCOPE)
        result.setdefault("ends_session", True)
        result.setdefault("cleared_tables", [])
        result.setdefault("dissociated", [])
        # The audit table was truncated too. Re-open the log with this event so
        # the destructive action is on record. The original actor row is gone
        # (actor_user_id=None); the denormalized email preserves who did it.
        db.add(
            AuditEvent(
                actor_user_id=None,
                actor_email=actor_email,
                actor_ip=actor_ip,
                action="database.truncate",
                target_type="database",
                target_label=settings.MYSQL_DB,
                summary=f"Truncated ALL tables and re-seeded admin; triggered by {actor_email}",
                extra={
                    "tables_truncated": result.get("tables_truncated"),
                    "reseeded_admin": result.get("reseeded_admin"),
                },
            )
        )
        db.commit()
        return TruncateResponse(**result)

    group = get_group(payload.scope)
    if group is None:
        raise ValidationError(f'Unknown scope "{payload.scope}".')
    if payload.confirm != group.confirm_phrase:
        raise ValidationError(f'Type "{group.confirm_phrase}" exactly to confirm.')

    result = truncate_group(db, group)
    # Audit survives a scoped wipe unless the "logs" group itself was cleared —
    # either way recording it now keeps the trail going. The actor's row is
    # intact for scoped wipes, so we can attribute it directly.
    surviving_actor = db.query(User).filter(User.id == actor.id).one_or_none()
    db.add(
        AuditEvent(
            actor_user_id=surviving_actor.id if surviving_actor else None,
            actor_email=actor_email,
            actor_ip=actor_ip,
            action="database.truncate",
            target_type="database",
            target_label=group.key,
            summary=f"Truncated '{group.label}' ({result['tables_truncated']} tables); by {actor_email}",
            extra={
                "scope": group.key,
                "cleared_tables": result["cleared_tables"],
                "dissociated": result["dissociated"],
            },
        )
    )
    db.commit()
    return TruncateResponse(**result)


@router.post("/seed", response_model=SeedResponse)
def seed_database(
    payload: SeedRequest,
    request: Request,
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> SeedResponse:
    """Load sample/dummy data into one domain group. Additive and idempotent —
    re-running skips rows that already exist."""
    group = get_group(payload.scope)
    if group is None:
        raise ValidationError(f'Unknown scope "{payload.scope}".')
    if group.seeder is None:
        raise ValidationError(f'"{group.label}" has no sample data to seed.')

    result = seed_group(db, group)
    db.add(
        AuditEvent(
            actor_user_id=actor.id,
            actor_email=actor.email,
            actor_ip=get_client_ip(request),
            action="database.seed",
            target_type="database",
            target_label=group.key,
            summary=f"Seeded sample data for '{group.label}'; by {actor.email}",
            extra={"scope": group.key, "created": result["created"]},
        )
    )
    db.commit()
    return SeedResponse(**result)
