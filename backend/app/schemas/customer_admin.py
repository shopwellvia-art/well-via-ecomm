"""Response shapes for the admin CUSTOMER directory (/admin/customers).

Kept apart from `schemas/user.py`, which serves the staff directory. The two
answer different questions about the same table and are gated by different
permissions, so sharing one model would mean one page's requirements quietly
widening the other's payload.

MONEY IS OPTIONAL ON PURPOSE
----------------------------
`gross_ltv`, `net_ltv`, `margin_ltv`, `aov` and `monetary` are `None` for
callers who do not hold `analytics.customers.view`. That permission is declared
SENSITIVE in the registry precisely to keep a named person's purchase history
away from general ops staff, and rendering spend on this list for anyone with
`customers.view` would route straight around it. The nulling happens in
`_snapshot_fields` so the list and the detail cannot diverge.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, EmailStr, Field


class CustomerSnapshotBrief(BaseModel):
    """Per-customer analytics, read off `agg_customer_snapshot`.

    Every field is "as of" the parent payload's `snapshot_date`, NOT live. A
    customer who registered but never ordered has no snapshot row at all and
    gets `None` here rather than a row of zeros — "no data" and "zero" are
    different answers.
    """

    orders_count: int = 0
    units: int = 0
    first_order_at: datetime | None = None
    last_order_at: datetime | None = None
    recency_days: int | None = None
    rfm_segment: str | None = None
    churn_risk_band: str | None = None
    cohort_month: str | None = None
    tenure_days: int | None = None
    is_active: bool = False

    # Sensitive — see the module docstring.
    gross_ltv: Decimal | None = None
    net_ltv: Decimal | None = None
    margin_ltv: Decimal | None = None
    aov: Decimal | None = None
    monetary: Decimal | None = None


class CustomerRow(BaseModel):
    """One line in the customer directory."""

    id: int
    email: EmailStr
    full_name: str | None = None
    phone: str | None = None
    is_active: bool
    account_status: str = "active"
    created_at: datetime
    last_login_at: datetime | None = None

    # Loyalty lives on the customers satellite and is always current (not a
    # rollup), so it is safe to show next to the "as of" snapshot figures.
    points_balance: int = 0
    vip_tier: str | None = None

    snapshot: CustomerSnapshotBrief | None = None


class CustomerPage(BaseModel):
    items: list[CustomerRow]
    total: int
    page: int
    page_size: int
    #: The rollup day every `snapshot` block was read from. None when the
    #: rollup has never run. The UI must show this — an unlabelled number
    #: implies "now", which these are not.
    snapshot_date: date | None = None
    #: False when the caller lacks `analytics.customers.view`, so the frontend
    #: can hide the columns entirely instead of rendering a row of blanks.
    money_visible: bool = False


class CustomerDetail(CustomerRow):
    """The 360 header: profile, counters and live session state."""

    first_name: str | None = None
    last_name: str | None = None
    gender: str | None = None
    date_of_birth: date | None = None
    profile_image: str | None = None
    lifetime_points: int = 0
    referral_code: str | None = None
    deactivated_at: datetime | None = None
    deleted_at: datetime | None = None
    totp_enabled: bool = False

    #: Live count of refresh-token families in Redis — how many devices this
    #: person is signed in on right now.
    active_sessions: int = 0
    #: Per-kind activity totals, from the same union the timeline reads, so a
    #: tab badge can never disagree with the tab.
    activity_counts: dict[str, int] = Field(default_factory=dict)
    snapshot_date: date | None = None
    money_visible: bool = False


class ActivityEventRead(BaseModel):
    occurred_at: datetime
    kind: str
    ref_id: int
    title: str
    detail: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class ActivityPage(BaseModel):
    items: list[ActivityEventRead]
    total: int
    page: int
    page_size: int


class AdminCustomerUpdate(BaseModel):
    """Body of PATCH /customers/{id}. Same two fields the staff editor allows —
    this route exists so `customers.manage` can support a shopper without also
    being able to touch a staff account."""

    full_name: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None


class StaffInvite(BaseModel):
    """Body of POST /users/invite."""

    email: EmailStr
    full_name: str | None = Field(default=None, max_length=255)
    #: Roles to grant on creation. Passed through the same escalation guards as
    #: a later role assignment — an inviter cannot hand out more than they hold.
    role_ids: list[int] = Field(default_factory=list)


class StaffInviteResponse(BaseModel):
    id: int
    email: EmailStr
    detail: str
