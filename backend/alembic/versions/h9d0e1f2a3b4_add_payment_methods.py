"""add payment_methods table and gateway columns on orders

Creates the `payment_methods` catalogue table (admin-managed multi-gateway
configuration) and adds two new columns to `orders` so every order records
which gateway processed it and the provider-side transaction reference.

New table
---------
`payment_methods` — one row per supported gateway:

  id                    INT PK autoincrement
  gateway_code          VARCHAR(40) UNIQUE NOT NULL   routing key ("razorpay")
  display_name          VARCHAR(80) NOT NULL           human label
  enabled               TINYINT(1) NOT NULL default 0
  environment           VARCHAR(16) NOT NULL default 'sandbox'
  credentials_encrypted TEXT NOT NULL default ''       Fernet-encrypted JSON
  sort_order            INT NOT NULL default 0
  created_at / updated_at  DATETIME(tz)

Seeded with 25 real gateways (all disabled/sandbox) + 1 mock row
(enabled=True, sort_order=99) so development checkout keeps working.

New columns on `orders`
-----------------------
  gateway_code          VARCHAR(40) NULL  + index  — which gateway processed it
  payment_provider_ref  VARCHAR(128) NULL           — provider-side txn/session id

Data migration
--------------
Best-effort only (wrapped in try/except): if `payment_gateway_config` has a
PhonePe row with credentials already entered, the ciphertext is re-encrypted
into the new `credentials_encrypted` blob and that row is enabled.  On any
failure the step is skipped and a warning is logged — the admin can re-enter
credentials manually.

Downgrade
---------
Drops the two orders columns (index first, MySQL rule) and the
payment_methods table.  Does NOT restore payment_gateway_config — that table
is left untouched throughout.

Revision ID: h9d0e1f2a3b4
Revises: g8b9c0d1e2f3
Create Date: 2026-06-10 00:01:00.000000
"""
import json
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

log = logging.getLogger("alembic.runtime.migration")

revision: str = "h9d0e1f2a3b4"
down_revision: Union[str, None] = "g8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ---------------------------------------------------------------------------
# Seed data: (gateway_code, display_name, sort_order, enabled)
# All real gateways start disabled + sandbox; mock starts enabled (sort 99).
# ---------------------------------------------------------------------------
_GATEWAY_SEED = [
    ("paypal",        "PayPal",                          1,  False),
    ("stripe",        "Stripe",                          2,  False),
    ("sslcommerz",    "SSLCommerz",                      3,  False),
    ("instamojo",     "Instamojo",                       4,  False),
    ("razorpay",      "Razorpay",                        5,  False),
    ("paystack",      "Paystack",                        6,  False),
    ("voguepay",      "VoguePay",                        7,  False),
    ("payhere",       "PayHere",                         8,  False),
    ("ngenius",       "N-Genius (Network International)", 9,  False),
    ("iyzico",        "iyzico",                          10, False),
    ("nagad",         "Nagad",                           11, False),
    ("bkash",         "bKash",                           12, False),
    ("aamarpay",      "aamarPay",                        13, False),
    ("authorizenet",  "Authorize.Net",                   14, False),
    ("payku",         "Payku",                           15, False),
    ("mercadopago",   "Mercado Pago",                    16, False),
    ("paymob",        "Paymob",                          17, False),
    ("paytm",         "Paytm",                           18, False),
    ("toyyibpay",     "toyyibPay",                       19, False),
    ("myfatoorah",    "MyFatoorah",                      20, False),
    ("khalti",        "Khalti",                          21, False),
    ("phonepe",       "PhonePe",                         22, False),
    ("flutterwave",   "Flutterwave",                     23, False),
    ("payfast",       "PayFast",                         24, False),
    ("tap",           "Tap Payments",                    25, False),
    # Development / test gateway — enabled by default so checkout works without
    # real credentials.  Admin can disable it before going live.
    ("mock",          "Test Mode (Mock Gateway)",        99, True),
]


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. Create payment_methods table                                      #
    # ------------------------------------------------------------------ #
    op.create_table(
        "payment_methods",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("gateway_code", sa.String(length=40), nullable=False),
        sa.Column("display_name", sa.String(length=80), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "environment",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'sandbox'"),
        ),
        sa.Column(
            "credentials_encrypted",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("gateway_code", name="uq_payment_methods_gateway_code"),
    )
    op.create_index(
        op.f("ix_payment_methods_gateway_code"),
        "payment_methods",
        ["gateway_code"],
        unique=True,
    )

    # ------------------------------------------------------------------ #
    # 2. Seed gateway rows (idempotent: skip if gateway_code already exists)
    # ------------------------------------------------------------------ #
    bind = op.get_bind()
    for gateway_code, display_name, sort_order, enabled in _GATEWAY_SEED:
        existing = bind.execute(
            sa.text(
                "SELECT id FROM payment_methods WHERE gateway_code = :gc"
            ),
            {"gc": gateway_code},
        ).fetchone()
        if existing is None:
            bind.execute(
                sa.text(
                    "INSERT INTO payment_methods "
                    "(gateway_code, display_name, enabled, environment, "
                    " credentials_encrypted, sort_order) "
                    "VALUES (:gc, :dn, :en, 'sandbox', '', :so)"
                ),
                {
                    "gc": gateway_code,
                    "dn": display_name,
                    "en": 1 if enabled else 0,
                    "so": sort_order,
                },
            )

    # ------------------------------------------------------------------ #
    # 3. Best-effort migration from legacy payment_gateway_config          #
    # ------------------------------------------------------------------ #
    try:
        row = bind.execute(
            sa.text(
                "SELECT provider, phonepe_merchant_id, "
                "phonepe_salt_key_encrypted, phonepe_salt_index, "
                "phonepe_environment "
                "FROM payment_gateway_config ORDER BY id LIMIT 1"
            )
        ).fetchone()

        if (
            row is not None
            and row[0] == "phonepe"
            and row[2]  # phonepe_salt_key_encrypted is non-empty
        ):
            # Import here so alembic env can load without the app fully wired.
            from app.core.crypto import decrypt_secret, encrypt_secret

            plain_salt_key = decrypt_secret(row[2])
            creds = {
                "merchant_id": row[1] or "",
                "salt_key": plain_salt_key,
                "salt_index": str(row[3] or 1),
            }
            ciphertext = encrypt_secret(json.dumps(creds))
            phonepe_env = (
                "live" if (row[4] or "").lower() == "production" else "sandbox"
            )

            bind.execute(
                sa.text(
                    "UPDATE payment_methods "
                    "SET credentials_encrypted = :ct, "
                    "    enabled = 1, "
                    "    environment = :env "
                    "WHERE gateway_code = 'phonepe'"
                ),
                {"ct": ciphertext, "env": phonepe_env},
            )
            # When a real gateway is live, disable the mock so checkout
            # doesn't accidentally fall through to the test path.
            bind.execute(
                sa.text(
                    "UPDATE payment_methods SET enabled = 0 "
                    "WHERE gateway_code = 'mock'"
                )
            )
            log.info(
                "payment_methods: migrated PhonePe credentials from "
                "payment_gateway_config"
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "payment_methods: could not migrate from payment_gateway_config "
            "(%s) — admin must re-enter credentials manually",
            exc,
        )

    # ------------------------------------------------------------------ #
    # 4. Add gateway_code + payment_provider_ref columns to orders         #
    # ------------------------------------------------------------------ #
    op.add_column(
        "orders",
        sa.Column("gateway_code", sa.String(length=40), nullable=True),
    )
    op.create_index(
        op.f("ix_orders_gateway_code"),
        "orders",
        ["gateway_code"],
        unique=False,
    )
    op.add_column(
        "orders",
        sa.Column("payment_provider_ref", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    # ---- orders: drop index then columns (MySQL rule) ----
    op.drop_index(op.f("ix_orders_gateway_code"), table_name="orders")
    op.drop_column("orders", "payment_provider_ref")
    op.drop_column("orders", "gateway_code")

    # ---- payment_methods table ----
    op.drop_index(
        op.f("ix_payment_methods_gateway_code"), table_name="payment_methods"
    )
    op.drop_table("payment_methods")
