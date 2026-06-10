"""Payment gateway configuration (singleton GET/PUT).

DEPRECATED: This endpoint manages the legacy PaymentGatewayConfig table which
stored a single active provider and PhonePe credentials.  New code should use
the payment_methods endpoints (GET/PUT /admin/payment-methods) which manage the
multi-gateway payment_methods table.  This endpoint will be removed in a future
release.

The salt key is write-only over the API: it's accepted on PUT (encrypted at
rest) but never returned — reads only report whether one is set.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission
from app.schemas.payment_gateway import PaymentGatewayRead, PaymentGatewayUpdate
from app.services.payment_gateway_service import PaymentGatewayService

router = APIRouter()


@router.get(
    "",
    response_model=PaymentGatewayRead,
    dependencies=[Depends(require_permission("payments.manage"))],
)
def get_payment_gateway(db: Session = Depends(get_db)):
    return PaymentGatewayRead.from_model(PaymentGatewayService(db).get())


@router.put(
    "",
    response_model=PaymentGatewayRead,
    dependencies=[Depends(require_permission("payments.manage"))],
)
def update_payment_gateway(payload: PaymentGatewayUpdate, db: Session = Depends(get_db)):
    cfg = PaymentGatewayService(db).update(payload)
    return PaymentGatewayRead.from_model(cfg)
