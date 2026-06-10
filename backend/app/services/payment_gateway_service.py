"""Payment gateway configuration service.

DEPRECATED: This service manages the legacy PaymentGatewayConfig table (single
active provider + PhonePe credentials).  The factory no longer reads from this
service.  New code should use PaymentMethodConfigService which manages the
multi-gateway payment_methods table.  This service will be removed in a future
release.

Owns the single config row and all salt-key crypto, so encryption lives in
exactly one place.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret, encrypt_secret
from app.models.payment_gateway import PaymentGatewayConfig
from app.repositories.payment_gateway_repository import PaymentGatewayRepository
from app.schemas.payment_gateway import SALT_KEY_KEEP, PaymentGatewayUpdate

logger = logging.getLogger(__name__)


class PaymentGatewayService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = PaymentGatewayRepository(db)

    def get(self) -> PaymentGatewayConfig:
        return self.repo.get_or_create()

    def update(self, data: PaymentGatewayUpdate) -> PaymentGatewayConfig:
        cfg = self.get()
        fields = data.model_dump(exclude_unset=True)

        # Salt key is handled specially: omit / "***" = keep; "" = clear;
        # anything else = encrypt + replace.
        salt_key = fields.pop("phonepe_salt_key", None)
        if salt_key is not None and salt_key != SALT_KEY_KEEP:
            cfg.phonepe_salt_key_encrypted = (
                encrypt_secret(salt_key) if salt_key else ""
            )

        for key, value in fields.items():
            setattr(cfg, key, value)

        self.db.commit()
        self.db.refresh(cfg)
        return cfg

    def decrypted_salt_key(self, cfg: PaymentGatewayConfig) -> str:
        """Plaintext salt key for the provider factory. Empty when unset or if
        the stored ciphertext can't be decrypted (e.g. SECRET_KEY rotated)."""
        if not cfg.phonepe_salt_key_encrypted:
            return ""
        try:
            return decrypt_secret(cfg.phonepe_salt_key_encrypted)
        except ValueError:
            logger.warning("payment gateway salt key could not be decrypted — treating as unset")
            return ""
