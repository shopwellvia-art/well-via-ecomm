"""Payment-method configuration endpoints.

Admin routes (require payments.manage):
  GET  /admin/payment-methods              list all gateways with config state
  PUT  /admin/payment-methods/{code}       update gateway credentials/toggle

Public route (no auth):
  GET  /payment-methods/active             only gateways that are live & ready

Note: Two separate APIRouter instances are created so they can be mounted
at different prefixes in router.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission
from app.schemas.payment_method import (
    PaymentMethodActiveListResponse,
    PaymentMethodActiveRead,
    PaymentMethodListResponse,
    PaymentMethodRead,
    PaymentMethodUpdate,
)
from app.services.payment_method_config_service import PaymentMethodConfigService

admin_router = APIRouter()
public_router = APIRouter()


@admin_router.get(
    "",
    response_model=PaymentMethodListResponse,
    dependencies=[Depends(require_permission("payments.manage"))],
    summary="List all payment gateways with configuration state",
)
def list_payment_methods(db: Session = Depends(get_db)):
    items = PaymentMethodConfigService(db).list_items()
    return PaymentMethodListResponse(items=items)


@admin_router.put(
    "/{code}",
    response_model=PaymentMethodRead,
    dependencies=[Depends(require_permission("payments.manage"))],
    summary="Update gateway credentials, environment, or enabled flag",
)
def update_payment_method(
    code: str,
    payload: PaymentMethodUpdate,
    db: Session = Depends(get_db),
):
    return PaymentMethodConfigService(db).update(code, payload)


@public_router.get(
    "",
    response_model=PaymentMethodActiveListResponse,
    summary="List payment gateways available at checkout",
)
def list_active_payment_methods(db: Session = Depends(get_db)):
    """Public endpoint — returns only gateways that are enabled, implemented,
    and fully configured (all required credential fields are stored)."""
    all_items = PaymentMethodConfigService(db).list_items()
    active = [
        PaymentMethodActiveRead(
            code=item.code,
            name=item.name,
            description=item.description,
        )
        for item in all_items
        if item.enabled and item.implemented and item.ready
    ]
    return PaymentMethodActiveListResponse(items=active)
