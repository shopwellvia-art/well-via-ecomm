"""Admin-managed multi-gateway payment methods catalogue.

Each row represents one payment gateway the store *may* accept.  The admin
enables/disables gateways and configures credentials from the Settings →
Payment Methods panel; the checkout flow queries `enabled=True` rows at runtime.

Credential storage
------------------
`credentials_encrypted` holds a **Fernet-encrypted JSON blob** mapping
credential keys to plaintext values, e.g.::

    {"api_key": "pk_live_...", "api_secret": "sk_live_..."}

Encryption uses ``app.core.crypto.encrypt_secret`` / ``decrypt_secret``
(same Fernet key as ``PaymentGatewayConfig.phonepe_salt_key_encrypted``).
An empty string ``""`` means no credentials have been saved yet.

The blob schema is deliberately untyped — each gateway integration knows which
keys it needs and validates them on save; the model layer just stores the
ciphertext without interpreting it.

Sort order
----------
``sort_order`` controls the display order in the admin panel and the checkout
method selector.  Lower values appear first.  The seed data uses 1-based
increments for the 25 real gateways; the mock / test row uses 99 so it sinks
to the bottom of the list.

Environment
-----------
``environment`` is a free-text tag stored alongside the credentials so the
gateway integration knows whether to hit sandbox or live endpoints.  The
application code does **not** validate this column — integrations read it
directly.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IDMixin, TimestampMixin


class PaymentMethod(Base, IDMixin, TimestampMixin):
    __tablename__ = "payment_methods"

    # Short, stable machine identifier for this gateway — used as a routing
    # key in the payment factory.  Examples: "razorpay", "stripe", "mock".
    gateway_code: Mapped[str] = mapped_column(
        String(40), unique=True, index=True, nullable=False
    )

    # Human-readable label shown in the admin panel and checkout UI.
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)

    # Whether this gateway is currently active.  Only enabled=True gateways
    # are offered at checkout; the mock gateway ships enabled=True so the
    # development store works out-of-the-box.
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # "sandbox" or "live" — passed through to the gateway integration so it
    # can select the correct endpoint base URL and API credentials.
    environment: Mapped[str] = mapped_column(
        String(16), default="sandbox", nullable=False
    )

    # Fernet-encrypted JSON dict of credential key→value pairs.
    # Encrypt with app.core.crypto.encrypt_secret(json.dumps({...})).
    # Decrypt with json.loads(app.core.crypto.decrypt_secret(value)).
    # "" means no credentials have been entered yet.
    credentials_encrypted: Mapped[str] = mapped_column(
        Text, default="", nullable=False
    )

    # Display order in admin panel and checkout selector; lower = first.
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
