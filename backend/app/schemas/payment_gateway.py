from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.models.payment_gateway import PaymentGatewayConfig
from app.schemas.base import AppSchema

# Sentinel the UI sends back for an unchanged secret — "keep what's stored".
SALT_KEY_KEEP = "***"


class PaymentGatewayRead(AppSchema):
    """Safe view of the gateway config. Never exposes the salt key (not even
    the ciphertext) — only whether one is on file."""

    provider: str
    phonepe_merchant_id: str
    phonepe_salt_index: int
    phonepe_environment: str
    salt_key_set: bool

    @classmethod
    def from_model(cls, cfg: PaymentGatewayConfig) -> "PaymentGatewayRead":
        return cls(
            provider=cfg.provider,
            phonepe_merchant_id=cfg.phonepe_merchant_id,
            phonepe_salt_index=cfg.phonepe_salt_index,
            phonepe_environment=cfg.phonepe_environment,
            salt_key_set=bool(cfg.phonepe_salt_key_encrypted),
        )


class PaymentGatewayUpdate(AppSchema):
    """Partial update. Any omitted field is left unchanged. For the salt key,
    omitting it OR sending the '***' sentinel keeps the stored value; an empty
    string clears it; any other value replaces it."""

    provider: Literal["mock", "phonepe"] | None = None
    phonepe_merchant_id: str | None = Field(default=None, max_length=64)
    phonepe_salt_key: str | None = None
    phonepe_salt_index: int | None = Field(default=None, ge=1, le=2)
    phonepe_environment: Literal["sandbox", "production"] | None = None
