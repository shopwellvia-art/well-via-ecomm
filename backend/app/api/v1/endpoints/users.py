"""Admin user-management endpoints.

Self-service operations (login, register, me) live in `auth`. This module
is staff-facing: list / search users, inspect role assignment, edit accounts
(name, activate/deactivate) and trigger password resets.

There is deliberately NO user deletion — accounts anchor order, payment and
loyalty history, so removing one would corrupt data integrity. Disable the
account instead (PATCH is_active=false), which also force-logs it out.
"""
from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission
from app.core.exceptions import NotFoundError
from app.core.rate_limit import get_client_ip
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.common import Page
from app.schemas.customer_admin import StaffInvite, StaffInviteResponse
from app.schemas.user import AdminPasswordResetResponse, AdminUserUpdate, UserRead
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.role_service import RoleService

router = APIRouter()


@router.get(
    "",
    response_model=Page[UserRead],
    dependencies=[Depends(require_permission("users.view"))],
)
def list_users(
    q: str | None = Query(default=None, description="Match against email or full name"),
    scope: str = Query(
        default="all",
        description=(
            "'staff' narrows to accounts that hold admin access (the legacy "
            "is_admin flag, or any role other than the empty 'customer' system "
            "role). 'all' is the pre-split behaviour."
        ),
        pattern="^(all|staff)$",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    offset = (page - 1) * page_size
    items, total = UserRepository(db).search(
        q=q, scope=scope, offset=offset, limit=page_size
    )
    return Page[UserRead](items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/{user_id}",
    response_model=UserRead,
    dependencies=[Depends(require_permission("users.view"))],
)
def get_user(user_id: int, db: Session = Depends(get_db)):
    user = UserRepository(db).get(user_id)
    if not user:
        raise NotFoundError("User not found")
    return user


@router.patch(
    "/{user_id}",
    response_model=UserRead,
)
def update_user(
    user_id: int,
    payload: AdminUserUpdate,
    request: Request,
    actor: User = Depends(require_permission("users.manage")),
    db: Session = Depends(get_db),
):
    """Staff edit of a user account: display name and/or the active flag.

    Deactivation (is_active=false) also revokes every session so the user is
    logged out on all devices immediately. Superadmin (is_admin) accounts can
    only be edited by another superadmin.
    """
    user, changes = AuthService(db).admin_update_user(actor, user_id, payload)
    AuditService(db).record(
        actor=actor,
        actor_ip=get_client_ip(request),
        action="user.update",
        target_type="user",
        target_id=user.id,
        target_label=user.email,
        summary=(
            f"Updated user {user.email} "
            f"({', '.join(sorted(changes)) or 'no changes'})"
        ),
        extra={"changes": changes} if changes else None,
    )
    db.commit()
    db.refresh(user)
    return user


@router.post(
    "/invite",
    response_model=StaffInviteResponse,
    status_code=status.HTTP_201_CREATED,
)
def invite_staff(
    payload: StaffInvite,
    request: Request,
    actor: User = Depends(require_permission("users.invite")),
    db: Session = Depends(get_db),
):
    """Create a staff account and email it a set-password code.

    Before this existed the only way to onboard a colleague was to have them
    register on the storefront as a shopper and then hand them a role, which
    meant every staff account started life indistinguishable from a customer.

    The password is random and discarded — the emailed one-time code is the only
    way in. Roles go through `RoleService.assign_to_user`, so the same
    escalation guard applies here as anywhere else: an inviter cannot create an
    account more powerful than themselves.
    """
    auth = AuthService(db)
    user = auth.admin_invite_staff(payload.email, payload.full_name)
    if payload.role_ids:
        RoleService(db).assign_to_user(actor, user.id, payload.role_ids)
    granted = sorted(r.name for r in user.roles)
    AuditService(db).record(
        actor=actor,
        actor_ip=get_client_ip(request),
        action="user.invite",
        target_type="user",
        target_id=user.id,
        target_label=user.email,
        summary=(
            f"Invited {user.email} as staff"
            + (f" with role(s): {', '.join(granted)}" if granted else " with no roles")
        ),
        extra={"roles": granted},
    )
    db.commit()
    db.refresh(user)
    # Sent AFTER the commit: an email promising a code the database never
    # stored would be worse than no email at all.
    auth.request_password_reset(user.email)
    return StaffInviteResponse(
        id=user.id,
        email=user.email,
        detail=(
            "Account created. A set-password code has been emailed — it expires "
            "in 10 minutes, after which they can use 'Forgot password' on the "
            "login page at any time."
        ),
    )


@router.post(
    "/{user_id}/password-reset",
    response_model=AdminPasswordResetResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_password_reset(
    user_id: int,
    request: Request,
    actor: User = Depends(require_permission("users.manage")),
    db: Session = Depends(get_db),
):
    """Admin-triggered password reset. Sends the user a one-time reset code
    over the standard forgot-password email machinery; the code is never
    returned in the response. Superadmin targets require a superadmin actor.
    """
    target = AuthService(db).admin_trigger_password_reset(actor, user_id)
    AuditService(db).record(
        actor=actor,
        actor_ip=get_client_ip(request),
        action="user.password_reset",
        target_type="user",
        target_id=target.id,
        target_label=target.email,
        summary=f"Triggered a password reset email for {target.email}",
    )
    db.commit()
    return AdminPasswordResetResponse(
        detail="Password reset code sent to the user's email."
    )
