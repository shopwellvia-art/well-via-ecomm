"""Return-flow Pydantic schemas.

Two perspectives:
  - Customer  : creates a return, lists their own, sees status updates.
  - Admin     : sees everyone's returns, approves/rejects, marks states.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


# ---- Customer-side ---------------------------------------------------------


class ReturnItemCreate(BaseModel):
    """One line of the return: which OrderItem, how many units back."""

    order_item_id: int
    quantity: int = Field(gt=0)


class ReturnCreateRequest(BaseModel):
    order_id: int
    items: list[ReturnItemCreate] = Field(min_length=1)
    # Controlled vocabulary; the frontend renders the same list as radio
    # buttons. We don't enforce the enum here so the customer's input
    # validation lives in the service (cleaner error messages).
    reason: str = Field(min_length=1, max_length=64)
    customer_notes: str | None = Field(default=None, max_length=2000)


class ReturnItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_item_id: int
    quantity: int


class ReturnRead(BaseModel):
    """Customer-visible return record. Hides admin_notes."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    status: str
    reason: str
    customer_notes: str | None = None
    reverse_awb: str | None = None
    refund_amount: Decimal | None = None
    # Inspection verdict + refund routing (visible to the customer so they can
    # see the outcome and where the money was sent).
    inspection_passed: bool | None = None
    inspected_at: datetime | None = None
    refund_method: str | None = None
    refund_reference: str | None = None
    requested_at: datetime
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
    picked_up_at: datetime | None = None
    received_at: datetime | None = None
    refunded_at: datetime | None = None
    cancelled_at: datetime | None = None
    items: list[ReturnItemRead]


# ---- Admin-side ------------------------------------------------------------


class AdminReturnCustomerBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str | None


class AdminReturnRead(ReturnRead):
    """Admin-only superset of ReturnRead. Adds notes + customer summary."""

    admin_notes: str | None = None
    inspection_notes: str | None = None
    reverse_pickup_id: str | None = None
    customer: AdminReturnCustomerBrief


class AdminReturnDecisionRequest(BaseModel):
    """Body for approve / reject. `refund_amount` only matters on approve;
    defaults to the sum of returned-item subtotals when omitted.
    `admin_notes` is the rejection/approval rationale shown in the audit
    log + internal admin view (never to the customer)."""

    admin_notes: str | None = Field(default=None, max_length=4000)
    refund_amount: Decimal | None = Field(default=None, ge=0, decimal_places=2)


class AdminReturnInspectRequest(BaseModel):
    """Body for the post-receipt inspection action.

    `passed` is the verdict: True = the item matches the customer's claim and
    meets the return policy (unlocks the refund); False = it does not, so the
    return is rejected. `inspection_notes` records what the inspector found
    (shown to the customer on rejection). `refund_amount` optionally overrides
    the computed refund and is only applied on a passing inspection.
    """

    passed: bool
    inspection_notes: str | None = Field(default=None, max_length=4000)
    refund_amount: Decimal | None = Field(default=None, ge=0, decimal_places=2)
