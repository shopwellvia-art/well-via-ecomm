"""Payment provider factory.

Resolves the active gateway from the `payment_methods` table (via
PaymentMethodConfigService) and builds the appropriate provider instance.
Each request that needs a provider calls `get_payment_provider(db, ...)` and
receives a fresh instance — no caching, no restart required.

The legacy `PaymentGatewayConfig` table / `PaymentGatewayService` is no longer
consulted by this factory. The admin endpoints in
`app/api/v1/endpoints/payment_gateway.py` and `payment_gateway_service.py`
continue to exist but are DEPRECATED and will be removed in a future release.

Deployment-specific URLs (return URL, webhook callback URL) stay in `.env`
via `app.core.config.settings`.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.config import settings as env_settings
from app.integrations.payments.base import PaymentProvider
from app.integrations.payments.flutterwave import FlutterwaveProvider
from app.integrations.payments.mock import MockProvider
from app.integrations.payments.paypal import PayPalProvider
from app.integrations.payments.paystack import PaystackProvider
from app.integrations.payments.phonepe import PhonePeProvider
from app.integrations.payments.razorpay import RazorpayProvider
from app.integrations.payments.stripe import StripeProvider
from app.models.order import Order
from app.models.payment_method import PaymentMethod

logger = logging.getLogger(__name__)

_PHONEPE_BASE = {
    "sandbox": "https://api-preprod.phonepe.com/apis/pg-sandbox",
    "live": "https://api.phonepe.com/apis/hermes",
}


def get_payment_provider(
    db: Session, gateway_code: str | None = None
) -> PaymentProvider:
    """Resolve a provider for checkout.

    Args:
        db: SQLAlchemy session.
        gateway_code: Optional explicit gateway code from the checkout request.
            If None, the lowest sort_order enabled+implemented+ready gateway is
            chosen. Falls back to mock if nothing else qualifies.

    Returns:
        A ready-to-use PaymentProvider instance.
    """
    # Import here to avoid a circular import at module load time
    # (service imports factory; factory imports service).
    from app.services.payment_method_config_service import PaymentMethodConfigService

    svc = PaymentMethodConfigService(db)
    row = svc.resolve_for_checkout(gateway_code)
    creds = svc.credentials_for(row.gateway_code)
    return _build_provider(row, creds)


def get_provider_for_order(db: Session, order: Order) -> PaymentProvider:
    """Build a provider from an existing order's gateway_code.

    Falls back to the default resolved gateway when gateway_code is NULL
    (legacy orders placed before the multi-gateway feature was deployed).
    """
    if order.gateway_code:
        return get_payment_provider(db, order.gateway_code)
    # Legacy row — resolve the default.
    return get_payment_provider(db, None)


def _build_provider(row: PaymentMethod, creds: dict[str, str]) -> PaymentProvider:
    code = row.gateway_code
    env = row.environment or "sandbox"

    if code == "mock":
        return MockProvider(
            frontend_url=env_settings.FRONTEND_URL,
            redis_url=env_settings.REDIS_URL,
        )

    if code == "phonepe":
        salt_index_raw = creds.get("salt_index", "1")
        try:
            salt_index = int(salt_index_raw)
        except (ValueError, TypeError):
            salt_index = 1
        return PhonePeProvider(
            merchant_id=creds.get("merchant_id", ""),
            salt_key=creds.get("salt_key", ""),
            salt_index=salt_index,
            base_url=_PHONEPE_BASE.get(env, _PHONEPE_BASE["sandbox"]),
            callback_url=env_settings.PAYMENT_WEBHOOK_URL,
        )

    if code == "razorpay":
        return RazorpayProvider(
            key_id=creds.get("key_id", ""),
            key_secret=creds.get("key_secret", ""),
            webhook_secret=creds.get("webhook_secret", ""),
        )

    if code == "stripe":
        return StripeProvider(
            publishable_key=creds.get("publishable_key", ""),
            secret_key=creds.get("secret_key", ""),
            webhook_secret=creds.get("webhook_secret", ""),
        )

    if code == "paypal":
        return PayPalProvider(
            client_id=creds.get("client_id", ""),
            client_secret=creds.get("client_secret", ""),
            environment=env,
        )

    if code == "paystack":
        return PaystackProvider(
            public_key=creds.get("public_key", ""),
            secret_key=creds.get("secret_key", ""),
        )

    if code == "flutterwave":
        return FlutterwaveProvider(
            public_key=creds.get("public_key", ""),
            secret_key=creds.get("secret_key", ""),
            webhook_secret_hash=creds.get("webhook_secret_hash", ""),
        )

    # Should not happen — resolve_for_checkout checks implemented=True.
    logger.error("factory: no provider implementation for code=%s; falling back to mock", code)
    return MockProvider(
        frontend_url=env_settings.FRONTEND_URL,
        redis_url=env_settings.REDIS_URL,
    )
