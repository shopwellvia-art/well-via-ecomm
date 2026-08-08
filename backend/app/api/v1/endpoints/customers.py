"""The admin CUSTOMER directory — shoppers, not staff.

Deliberately a separate router from `users`, on a separate permission tier:

  * `users.*`     — staff accounts and who may grant admin access (/admin/team)
  * `customers.*` — shoppers, their history, and account support (/admin/customers)

Splitting them is the point of this module. Before it, one list mixed both
populations and the role-assignment control rendered on every row, so supporting
a customer and granting admin access were one misclick apart.

Every route here refuses a STAFF target (`_get_customer`). A customer-support
operator must not be able to reach an admin account through the customer API
just because the underlying table is shared.

There is no DELETE. Accounts anchor order, payment and loyalty history — the
same reason `users.py` gives. Disable instead.
"""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission
from app.services.analytics.aggregation.jobs_customer import SEGMENT_NAMES
from app.core.exceptions import NotFoundError
from app.core.rate_limit import get_client_ip
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.customer_admin import (
    ActivityEventRead,
    ActivityPage,
    AdminCustomerUpdate,
    CustomerDetail,
    CustomerPage,
    CustomerRow,
    CustomerSnapshotBrief,
)
from app.schemas.user import AdminPasswordResetResponse, AdminUserUpdate
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.customer_activity_service import KINDS, CustomerActivityService

router = APIRouter()

#: Holding this lets a caller see per-customer MONEY. It is declared SENSITIVE
#: in the permission registry; `customers.view` alone deliberately does not
#: imply it. See `schemas/customer_admin` for why.
MONEY_PERMISSION = "analytics.customers.view"

#: Sort keys that order by a money column. Reachable only with MONEY_PERMISSION
#: — see the coercion in `list_customers`.
_MONEY_SORTS = frozenset({"ltv"})


def _timeline_money_visible(actor: User) -> bool:
    """Whether this actor may see per-event amounts on the activity feed.

    Separate from the directory's money gate because it has a second legitimate
    holder: `orders.view_all` already grants a full view of every order, so
    hiding an order total here from that actor would protect nothing. Everyone
    else sees the event without the amount — four order totals on a feed sum to
    roughly the lifetime spend the directory deliberately withholds.
    """
    return actor.has_permission(MONEY_PERMISSION) or actor.has_permission(
        "orders.view_all"
    )


def _get_customer(db: Session, user_id: int) -> User:
    """Resolve a customer by id, refusing staff accounts.

    Returns 404 rather than 403 for a staff target: this API's universe is
    shoppers, and confirming "that id exists but is an admin" would leak the
    shape of the staff directory to an operator who cannot see it.
    """
    repo = UserRepository(db)
    user = repo.get(user_id)
    if not user or repo.is_staff(user):
        raise NotFoundError("Customer not found")
    return user


def _snapshot_fields(snapshot, *, money_visible: bool) -> CustomerSnapshotBrief | None:
    """Project an `agg_customer_snapshot` row, nulling money when not allowed.

    The single place the sensitive tier is enforced, so the list and the detail
    cannot drift apart.
    """
    if snapshot is None:
        return None
    return CustomerSnapshotBrief(
        orders_count=snapshot.orders_count,
        units=snapshot.units,
        first_order_at=snapshot.first_order_at,
        last_order_at=snapshot.last_order_at,
        recency_days=snapshot.recency_days,
        rfm_segment=snapshot.rfm_segment,
        churn_risk_band=snapshot.churn_risk_band,
        cohort_month=snapshot.cohort_month,
        tenure_days=snapshot.tenure_days,
        is_active=snapshot.is_active,
        gross_ltv=snapshot.gross_ltv if money_visible else None,
        net_ltv=snapshot.net_ltv if money_visible else None,
        margin_ltv=snapshot.margin_ltv if money_visible else None,
        aov=snapshot.aov if money_visible else None,
        monetary=snapshot.monetary if money_visible else None,
    )


def _row(user: User, snapshot, *, money_visible: bool) -> CustomerRow:
    customer = user.customer
    return CustomerRow(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        phone=user.phone,
        is_active=user.is_active,
        account_status=user.account_status,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        points_balance=user.points_balance,
        vip_tier=customer.vip_tier.name if customer and customer.vip_tier else None,
        snapshot=_snapshot_fields(snapshot, money_visible=money_visible),
    )


@router.get(
    "",
    response_model=CustomerPage,
)
def list_customers(
    q: str | None = Query(default=None, description="Email, phone or name"),
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="active | deactivated | deleted",
    ),
    # Constrained rather than free-text: the repository filters on raw equality,
    # so an unknown value used to return 200 with an empty list — indistinguishable
    # from a segment that genuinely has no customers in it. A 422 says "that
    # segment does not exist" instead of quietly lying about the business.
    segment: Literal[SEGMENT_NAMES] | None = Query(  # type: ignore[valid-type]
        default=None,
        description="RFM segment — one of: " + ", ".join(SEGMENT_NAMES),
    ),
    has_ordered: bool | None = Query(
        default=None, description="true = has placed an order; false = never ordered"
    ),
    joined_from: date | None = Query(default=None),
    joined_to: date | None = Query(default=None),
    sort: str = Query(
        default="recent",
        description="recent | oldest | email | ltv | orders | last_order",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    actor: User = Depends(require_permission("customers.view")),
    db: Session = Depends(get_db),
):
    money_visible = actor.has_permission(MONEY_PERMISSION)
    # Sorting by spend leaks spend. A caller who cannot see `gross_ltv` must not
    # be able to rank customers by it either — the ORDER of the rows would hand
    # them the relative figures the nulled columns are withholding. Fall back to
    # the default rather than erroring: the request is answerable, just not the
    # way it was asked.
    if sort in _MONEY_SORTS and not money_visible:
        sort = "recent"
    rows, total, snapshot_date = UserRepository(db).search_customers(
        q=q,
        status=status_filter,
        segment=segment,
        has_ordered=has_ordered,
        joined_from=joined_from,
        joined_to=joined_to,
        sort=sort,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    return CustomerPage(
        items=[_row(u, s, money_visible=money_visible) for u, s in rows],
        total=total,
        page=page,
        page_size=page_size,
        snapshot_date=snapshot_date,
        money_visible=money_visible,
    )


@router.get(
    "/{user_id}",
    response_model=CustomerDetail,
)
def get_customer(
    user_id: int,
    actor: User = Depends(require_permission("customers.view")),
    db: Session = Depends(get_db),
):
    user = _get_customer(db, user_id)
    repo = UserRepository(db)
    money_visible = actor.has_permission(MONEY_PERMISSION)

    snapshot_date = repo.latest_snapshot_date()
    snapshot = None
    if snapshot_date is not None:
        from sqlalchemy import select

        from app.models.analytics_rollups import AggCustomerSnapshot

        snapshot = db.execute(
            select(AggCustomerSnapshot).where(
                AggCustomerSnapshot.user_id == user.id,
                AggCustomerSnapshot.bucket_date == snapshot_date,
            )
        ).scalars().first()

    base = _row(user, snapshot, money_visible=money_visible)
    customer = user.customer
    return CustomerDetail(
        **base.model_dump(),
        first_name=user.first_name,
        last_name=user.last_name,
        gender=user.gender,
        date_of_birth=user.date_of_birth,
        profile_image=user.profile_image,
        lifetime_points=user.lifetime_points,
        referral_code=user.referral_code,
        deactivated_at=customer.deactivated_at if customer else None,
        deleted_at=customer.deleted_at if customer else None,
        totp_enabled=user.totp_enabled,
        active_sessions=AuthService(db).active_session_count(user.id),
        activity_counts=CustomerActivityService(db).counters(user),
        snapshot_date=snapshot_date,
        money_visible=money_visible,
    )


@router.get(
    "/{user_id}/activity",
    response_model=ActivityPage,
)
def get_customer_activity(
    user_id: int,
    kinds: list[str] | None = Query(
        default=None, description=f"Filter by event kind. One of: {', '.join(KINDS)}"
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    actor: User = Depends(require_permission("customers.view")),
    db: Session = Depends(get_db),
):
    """The merged activity timeline — orders, returns, reviews, loyalty,
    referrals, wishlist, cart, contact messages and staff actions on the
    account — newest first.

    Contact messages are matched by EMAIL, not by a foreign key; each such event
    carries `meta.email_matched = true` so the UI can say so.
    """
    user = _get_customer(db, user_id)
    events, total = CustomerActivityService(
        db, show_money=_timeline_money_visible(actor)
    ).timeline(
        user,
        kinds=kinds,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    return ActivityPage(
        items=[ActivityEventRead(**vars(e)) for e in events],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.patch(
    "/{user_id}",
    response_model=CustomerRow,
)
def update_customer(
    user_id: int,
    payload: AdminCustomerUpdate,
    request: Request,
    actor: User = Depends(require_permission("customers.manage")),
    db: Session = Depends(get_db),
):
    """Support edit of a shopper account: display name and/or active flag.

    Delegates to the same `AuthService.admin_update_user` the staff editor uses,
    so deactivation still revokes every session — one implementation of what
    "disable an account" means, reached through two permissions.
    """
    user = _get_customer(db, user_id)
    updated, changes = AuthService(db).admin_update_user(
        actor, user.id, AdminUserUpdate(**payload.model_dump(exclude_unset=True))
    )
    AuditService(db).record(
        actor=actor,
        actor_ip=get_client_ip(request),
        action="customer.update",
        target_type="user",
        target_id=updated.id,
        target_label=updated.email,
        summary=(
            f"Updated customer {updated.email} "
            f"({', '.join(sorted(changes)) or 'no changes'})"
        ),
        extra={"changes": changes} if changes else None,
    )
    db.commit()
    db.refresh(updated)
    return _row(updated, None, money_visible=False)


@router.post(
    "/{user_id}/password-reset",
    response_model=AdminPasswordResetResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_customer_password_reset(
    user_id: int,
    request: Request,
    actor: User = Depends(require_permission("customers.manage")),
    db: Session = Depends(get_db),
):
    """Email the customer a one-time reset code. The code goes to their inbox
    only — it is never returned here, so a support operator cannot capture it
    and take the account over."""
    user = _get_customer(db, user_id)
    AuthService(db).admin_trigger_password_reset(actor, user.id)
    AuditService(db).record(
        actor=actor,
        actor_ip=get_client_ip(request),
        action="customer.password_reset",
        target_type="user",
        target_id=user.id,
        target_label=user.email,
        summary=f"Triggered a password reset email for customer {user.email}",
    )
    db.commit()
    return AdminPasswordResetResponse(
        detail="Password reset code sent to the customer's email."
    )
