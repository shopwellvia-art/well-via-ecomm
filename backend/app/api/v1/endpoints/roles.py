from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission
from app.core.rate_limit import get_client_ip
from app.models.user import User
from app.repositories.permission_repository import PermissionRepository
from app.schemas.rbac import (
    PermissionRead,
    RoleCreate,
    RoleRead,
    RoleUpdate,
    UserRolesUpdate,
)
from app.schemas.user import UserRead
from app.services.audit_service import AuditService
from app.services.role_service import RoleService

router = APIRouter()


@router.get(
    "/permissions",
    response_model=list[PermissionRead],
    dependencies=[Depends(require_permission("roles.view"))],
)
def list_permissions(db: Session = Depends(get_db)):
    return PermissionRepository(db).list_all()


@router.get(
    "",
    response_model=list[RoleRead],
    dependencies=[Depends(require_permission("roles.view"))],
)
def list_roles(db: Session = Depends(get_db)):
    return RoleService(db).list_all()


@router.post(
    "",
    response_model=RoleRead,
    status_code=status.HTTP_201_CREATED,
)
def create_role(
    payload: RoleCreate,
    request: Request,
    actor: User = Depends(require_permission("roles.create")),
    db: Session = Depends(get_db),
):
    role = RoleService(db).create(actor, payload)
    AuditService(db).record(
        actor=actor,
        actor_ip=get_client_ip(request),
        action="role.create",
        target_type="role",
        target_id=role.id,
        target_label=role.name,
        summary=f"Created role '{role.name}' with {len(role.permissions)} permission(s)",
        extra={
            "permissions": [p.name for p in role.permissions],
            "description": role.description,
        },
    )
    db.commit()
    return role


@router.get(
    "/{role_id}",
    response_model=RoleRead,
    dependencies=[Depends(require_permission("roles.view"))],
)
def get_role(role_id: int, db: Session = Depends(get_db)):
    return RoleService(db).get(role_id)


@router.patch(
    "/{role_id}",
    response_model=RoleRead,
)
def update_role(
    role_id: int,
    payload: RoleUpdate,
    request: Request,
    actor: User = Depends(require_permission("roles.update")),
    db: Session = Depends(get_db),
):
    # Snapshot before to record what changed.
    svc = RoleService(db)
    before = svc.get(role_id)
    before_perms = sorted(p.name for p in before.permissions)
    before_name = before.name

    role = svc.update(actor, role_id, payload)
    after_perms = sorted(p.name for p in role.permissions)
    changes = {}
    if role.name != before_name:
        changes["name"] = {"before": before_name, "after": role.name}
    if after_perms != before_perms:
        changes["permissions"] = {
            "added": [p for p in after_perms if p not in before_perms],
            "removed": [p for p in before_perms if p not in after_perms],
        }

    AuditService(db).record(
        actor=actor,
        actor_ip=get_client_ip(request),
        action="role.update",
        target_type="role",
        target_id=role.id,
        target_label=role.name,
        summary=f"Updated role '{role.name}'",
        extra={"changes": changes} if changes else None,
    )
    db.commit()
    return role


@router.delete(
    "/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_role(
    role_id: int,
    request: Request,
    actor: User = Depends(require_permission("roles.delete")),
    db: Session = Depends(get_db),
):
    role = RoleService(db).get(role_id)
    name = role.name
    RoleService(db).delete(role_id)
    AuditService(db).record(
        actor=actor,
        actor_ip=get_client_ip(request),
        action="role.delete",
        target_type="role",
        target_id=role_id,
        target_label=name,
        summary=f"Deleted role '{name}'",
    )
    db.commit()


@router.put(
    "/users/{user_id}",
    response_model=UserRead,
)
def assign_roles_to_user(
    user_id: int,
    payload: UserRolesUpdate,
    request: Request,
    actor: User = Depends(require_permission("users.assign_role")),
    db: Session = Depends(get_db),
):
    svc = RoleService(db)
    before_user = svc.users.get(user_id)
    before_roles = sorted(r.name for r in (before_user.roles if before_user else []))

    user = svc.assign_to_user(actor, user_id, payload.role_ids)
    after_roles = sorted(r.name for r in user.roles)
    AuditService(db).record(
        actor=actor,
        actor_ip=get_client_ip(request),
        action="role.assign",
        target_type="user",
        target_id=user.id,
        target_label=user.email,
        summary=(
            f"Assigned roles to {user.email}: "
            f"{', '.join(after_roles) or '(none)'}"
        ),
        extra={
            "before": before_roles,
            "after": after_roles,
            "added": [r for r in after_roles if r not in before_roles],
            "removed": [r for r in before_roles if r not in after_roles],
        },
    )
    db.commit()
    return user
