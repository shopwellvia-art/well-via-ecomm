"""Admin-facing service for the payment_methods table.

NOTE: app/services/payment_methods_service.py already exists and handles
UPI/card instrument toggles — a completely different domain.  This service
owns gateway-level configuration: enabling/disabling gateways, credential
storage, and checkout resolution.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.exceptions import NotFoundError, ValidationError
from app.integrations.payments.base import normalize_environment
from app.integrations.payments.registry import GatewayDef, get_gateway
from app.models.payment_method import PaymentMethod
from app.schemas.payment_method import (
    PaymentMethodFieldRead,
    PaymentMethodRead,
    PaymentMethodUpdate,
)

logger = logging.getLogger(__name__)


class PaymentMethodConfigService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_items(self) -> list[PaymentMethodRead]:
        """Return all payment methods ordered by sort_order."""
        stmt = select(PaymentMethod).order_by(PaymentMethod.sort_order)
        rows = self.db.execute(stmt).scalars().all()
        return [self._to_read(row) for row in rows]

    def update(self, code: str, payload: PaymentMethodUpdate) -> PaymentMethodRead:
        """Apply a partial update to the gateway with *code*.

        Rules:
        - credentials is a partial merge; key="" deletes that key; unknown
          keys (not in the registry for this gateway) raise ValidationError.
        - enabled=True requires implemented=True and all required fields present
          after the merge.
        - Returns the updated PaymentMethodRead.
        """
        row = self._get_row(code)
        gw = get_gateway(code)
        if gw is None:
            # Row exists in DB but not in registry — treat as unknown.
            raise NotFoundError(f"Payment gateway '{code}' is not in the registry.")

        # --- environment ---
        if payload.environment is not None:
            row.environment = normalize_environment(payload.environment)

        # --- credentials merge ---
        current_creds = self._decrypt_creds(row)
        if payload.credentials is not None:
            known_keys = {f.key for f in gw.fields}
            unknown = set(payload.credentials.keys()) - known_keys
            if unknown:
                raise ValidationError(
                    f"Unknown credential field(s) for {gw.name}: "
                    + ", ".join(sorted(unknown))
                )
            for k, v in payload.credentials.items():
                if v == "":
                    current_creds.pop(k, None)
                else:
                    current_creds[k] = v
            row.credentials_encrypted = (
                encrypt_secret(json.dumps(current_creds)) if current_creds else ""
            )

        # --- enabled toggle ---
        if payload.enabled is not None:
            if payload.enabled:
                if not gw.implemented:
                    raise ValidationError(
                        f"{gw.name} integration module is not yet available."
                    )
                missing = self._missing_required_fields(gw, current_creds)
                if missing:
                    labels = ", ".join(missing)
                    raise ValidationError(
                        f"Cannot enable {gw.name}: missing required credentials: {labels}."
                    )
            row.enabled = payload.enabled

        self.db.commit()
        self.db.refresh(row)
        return self._to_read(row)

    def credentials_for(self, code: str) -> dict[str, str]:
        """Return decrypted credential dict for the given gateway code."""
        row = self._get_row(code)
        return self._decrypt_creds(row)

    def resolve_for_checkout(self, gateway_code: str | None) -> PaymentMethod:
        """Select the gateway row to use for a checkout.

        - gateway_code given: must be enabled + implemented + ready, else
          raises ValidationError with a customer-friendly message.
        - gateway_code=None: use the lowest sort_order row that is
          enabled + implemented + ready.
        - If nothing qualifies: fall back to the mock row if it's enabled,
          else raise ValidationError("No payment gateway is currently available...").
        """
        if gateway_code is not None:
            row = self._get_row_or_none(gateway_code)
            if row is None:
                raise ValidationError(
                    f"Payment gateway '{gateway_code}' is not available."
                )
            gw = get_gateway(gateway_code)
            if gw is None or not gw.implemented:
                raise ValidationError(
                    f"Payment gateway '{gateway_code}' is not supported."
                )
            if not row.enabled:
                raise ValidationError(
                    f"Payment gateway '{gateway_code}' is not currently enabled."
                )
            creds = self._decrypt_creds(row)
            if self._missing_required_fields(gw, creds):
                raise ValidationError(
                    f"Payment gateway '{gateway_code}' is not fully configured."
                )
            return row

        # Auto-resolve: lowest sort_order that passes all gates.
        stmt = (
            select(PaymentMethod)
            .where(PaymentMethod.enabled.is_(True))
            .order_by(PaymentMethod.sort_order)
        )
        rows = self.db.execute(stmt).scalars().all()
        for row in rows:
            gw = get_gateway(row.gateway_code)
            if gw is None or not gw.implemented:
                continue
            # mock has no required fields — always ready when enabled
            if row.gateway_code == "mock":
                return row
            creds = self._decrypt_creds(row)
            if not self._missing_required_fields(gw, creds):
                return row

        # Last resort: mock
        mock_row = self._get_row_or_none("mock")
        if mock_row and mock_row.enabled:
            return mock_row

        raise ValidationError(
            "No payment gateway is currently available. "
            "Please contact support or try again later."
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_row(self, code: str) -> PaymentMethod:
        row = self._get_row_or_none(code)
        if row is None:
            raise NotFoundError(f"Payment gateway '{code}' not found.")
        return row

    def _get_row_or_none(self, code: str) -> PaymentMethod | None:
        stmt = select(PaymentMethod).where(PaymentMethod.gateway_code == code)
        return self.db.execute(stmt).scalar_one_or_none()

    def _decrypt_creds(self, row: PaymentMethod) -> dict[str, str]:
        if not row.credentials_encrypted:
            return {}
        try:
            return json.loads(decrypt_secret(row.credentials_encrypted))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not decrypt credentials for gateway %s: %s — treating as empty",
                row.gateway_code,
                exc,
            )
            return {}

    @staticmethod
    def _missing_required_fields(gw: GatewayDef, creds: dict[str, str]) -> list[str]:
        """Return labels of required fields that are absent from *creds*."""
        missing: list[str] = []
        for f in gw.fields:
            if f.required and not creds.get(f.key, "").strip():
                missing.append(f.label)
        return missing

    def _to_read(self, row: PaymentMethod) -> PaymentMethodRead:
        gw = get_gateway(row.gateway_code)
        creds = self._decrypt_creds(row)

        if gw is None:
            # Unknown code in DB — return minimal safe representation.
            return PaymentMethodRead(
                code=row.gateway_code,
                name=row.display_name,
                description="",
                enabled=row.enabled,
                environment=row.environment,
                supports_environment=True,
                implemented=False,
                ready=False,
                sort_order=row.sort_order,
                fields=[],
            )

        field_reads: list[PaymentMethodFieldRead] = []
        for f in gw.fields:
            stored = creds.get(f.key, "")
            is_set = bool(stored)
            field_reads.append(
                PaymentMethodFieldRead(
                    key=f.key,
                    label=f.label,
                    secret=f.secret,
                    required=f.required,
                    placeholder=f.placeholder,
                    help=f.help,
                    # Secret fields: never expose value; show only set flag.
                    value=None if f.secret else (stored or None),
                    set=is_set,
                )
            )

        ready = len(self._missing_required_fields(gw, creds)) == 0

        return PaymentMethodRead(
            code=row.gateway_code,
            name=row.display_name,
            description=gw.description,
            enabled=row.enabled,
            environment=row.environment,
            supports_environment=gw.supports_environment,
            implemented=gw.implemented,
            ready=ready,
            sort_order=row.sort_order,
            fields=field_reads,
        )
