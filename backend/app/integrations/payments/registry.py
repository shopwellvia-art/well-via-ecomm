"""Gateway catalogue — the single source of truth for which gateways exist,
what credentials they need, and whether a code-level integration is available.

The `implemented` flag means there is a working provider class in this package.
Setting it to False means the admin can see and configure the gateway in the UI
but the factory will refuse to activate it until someone writes the integration.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GatewayField:
    key: str
    label: str
    secret: bool = False
    required: bool = True
    placeholder: str = ""
    help: str = ""


@dataclass(frozen=True)
class GatewayDef:
    code: str
    name: str
    description: str
    fields: tuple[GatewayField, ...]
    supports_environment: bool = True
    implemented: bool = False


# ---------------------------------------------------------------------------
# Catalogue — 26 entries matching the seeded `payment_methods` rows.
# ---------------------------------------------------------------------------

_DEFS: tuple[GatewayDef, ...] = (
    GatewayDef(
        code="paypal",
        name="PayPal",
        description="Accept cards and PayPal balances worldwide via PayPal Checkout.",
        fields=(
            GatewayField("client_id", "Client ID", placeholder="AXxx..."),
            GatewayField("client_secret", "Client Secret", secret=True),
        ),
        implemented=True,
    ),
    GatewayDef(
        code="stripe",
        name="Stripe",
        description="Cards, wallets and local payment methods globally via Stripe Checkout.",
        fields=(
            GatewayField("publishable_key", "Publishable Key", placeholder="pk_live_..."),
            GatewayField("secret_key", "Secret Key", secret=True, placeholder="sk_live_..."),
            GatewayField(
                "webhook_secret",
                "Webhook Signing Secret",
                secret=True,
                required=False,
                placeholder="whsec_...",
                help="Signing secret for /api/v1/payments/webhook/stripe",
            ),
        ),
        implemented=True,
    ),
    GatewayDef(
        code="sslcommerz",
        name="SSLCommerz",
        description="Leading payment gateway in Bangladesh supporting cards and mobile banking.",
        fields=(
            GatewayField("store_id", "Store ID", placeholder="testbox"),
            GatewayField("store_password", "Store Password", secret=True),
        ),
        implemented=False,
    ),
    GatewayDef(
        code="instamojo",
        name="Instamojo",
        description="Indian payment gateway for small businesses — UPI, cards and net-banking.",
        fields=(
            GatewayField("api_key", "API Key"),
            GatewayField("auth_token", "Auth Token", secret=True),
            GatewayField(
                "salt",
                "Salt",
                secret=True,
                required=False,
                help="Used to verify webhook signatures.",
            ),
        ),
        implemented=False,
    ),
    GatewayDef(
        code="razorpay",
        name="Razorpay",
        description="India's leading payment gateway — UPI, cards, net-banking and EMI.",
        fields=(
            GatewayField("key_id", "Key ID", placeholder="rzp_live_..."),
            GatewayField("key_secret", "Key Secret", secret=True),
            GatewayField(
                "webhook_secret",
                "Webhook Secret",
                secret=True,
                required=False,
                help="Signing secret for webhook signature verification.",
            ),
        ),
        implemented=True,
    ),
    GatewayDef(
        code="paystack",
        name="Paystack",
        description="Cards, bank transfers and mobile money across Africa.",
        fields=(
            GatewayField("public_key", "Public Key", placeholder="pk_live_..."),
            GatewayField("secret_key", "Secret Key", secret=True, placeholder="sk_live_..."),
        ),
        implemented=True,
    ),
    GatewayDef(
        code="voguepay",
        name="VoguePay",
        description="Pan-African payment platform supporting multiple currencies.",
        fields=(
            GatewayField("merchant_id", "Merchant ID"),
            GatewayField("command_api_token", "Command API Token", secret=True),
        ),
        implemented=False,
    ),
    GatewayDef(
        code="payhere",
        name="PayHere",
        description="Sri Lanka's most popular payment gateway — cards and mobile wallets.",
        fields=(
            GatewayField("merchant_id", "Merchant ID"),
            GatewayField("merchant_secret", "Merchant Secret", secret=True),
        ),
        implemented=False,
    ),
    GatewayDef(
        code="ngenius",
        name="Network International (N-Genius)",
        description="Cards and digital wallets for the UAE and wider Middle East.",
        fields=(
            GatewayField("api_key", "API Key", secret=True),
            GatewayField("outlet_reference", "Outlet Reference"),
        ),
        implemented=False,
    ),
    GatewayDef(
        code="iyzico",
        name="iyzico",
        description="Cards and alternative payment methods in Turkey.",
        fields=(
            GatewayField("api_key", "API Key"),
            GatewayField("secret_key", "Secret Key", secret=True),
        ),
        implemented=False,
    ),
    GatewayDef(
        code="nagad",
        name="Nagad",
        description="Bangladesh mobile financial service operated by Bangladesh Post.",
        fields=(
            GatewayField("merchant_id", "Merchant ID"),
            GatewayField("merchant_number", "Merchant Number"),
            GatewayField("public_key", "Public Key", secret=True),
            GatewayField("private_key", "Private Key", secret=True),
        ),
        implemented=False,
    ),
    GatewayDef(
        code="bkash",
        name="bKash",
        description="Bangladesh's largest mobile financial service with 50M+ users.",
        fields=(
            GatewayField("app_key", "App Key"),
            GatewayField("app_secret", "App Secret", secret=True),
            GatewayField("username", "Username"),
            GatewayField("password", "Password", secret=True),
        ),
        implemented=False,
    ),
    GatewayDef(
        code="aamarpay",
        name="aamarPay",
        description="Multi-currency payment gateway serving Bangladesh, UK and USA.",
        fields=(
            GatewayField("store_id", "Store ID"),
            GatewayField("signature_key", "Signature Key", secret=True),
        ),
        implemented=False,
    ),
    GatewayDef(
        code="authorizenet",
        name="Authorize.Net",
        description="US card gateway from Visa — widely used for e-commerce in North America.",
        fields=(
            GatewayField("api_login_id", "API Login ID"),
            GatewayField("transaction_key", "Transaction Key", secret=True),
        ),
        implemented=False,
    ),
    GatewayDef(
        code="payku",
        name="Payku",
        description="Latin American payment gateway supporting cards and local methods.",
        fields=(
            GatewayField("public_token", "Public Token"),
            GatewayField("private_token", "Private Token", secret=True),
        ),
        implemented=False,
    ),
    GatewayDef(
        code="mercadopago",
        name="Mercado Pago",
        description="Dominant payment platform across Latin America.",
        fields=(
            GatewayField("public_key", "Public Key"),
            GatewayField("access_token", "Access Token", secret=True),
        ),
        implemented=False,
    ),
    GatewayDef(
        code="paymob",
        name="Paymob",
        description="Cards and mobile wallets across Egypt and the MENA region.",
        fields=(
            GatewayField("api_key", "API Key", secret=True),
            GatewayField("integration_id", "Integration ID"),
            GatewayField("iframe_id", "iFrame ID"),
            GatewayField(
                "hmac_secret",
                "HMAC Secret",
                secret=True,
                required=False,
                help="Used to verify webhook signatures.",
            ),
        ),
        implemented=False,
    ),
    GatewayDef(
        code="paytm",
        name="Paytm",
        description="India — UPI, wallets, cards and EMI via the Paytm superapp.",
        fields=(
            GatewayField("merchant_id", "Merchant ID"),
            GatewayField("merchant_key", "Merchant Key", secret=True),
            GatewayField("website", "Website", placeholder="DEFAULT"),
            GatewayField("industry_type", "Industry Type", placeholder="Retail"),
        ),
        implemented=False,
    ),
    GatewayDef(
        code="toyyibpay",
        name="toyyibPay",
        description="Malaysian payment gateway supporting FPX, credit cards and e-wallets.",
        fields=(
            GatewayField("secret_key", "Secret Key", secret=True),
            GatewayField("category_code", "Category Code"),
        ),
        implemented=False,
    ),
    GatewayDef(
        code="myfatoorah",
        name="MyFatoorah",
        description="Cards and local payment methods across the GCC and MENA.",
        fields=(
            GatewayField("api_token", "API Token", secret=True),
        ),
        implemented=False,
    ),
    GatewayDef(
        code="khalti",
        name="Khalti",
        description="Nepal digital wallet and payment gateway.",
        fields=(
            GatewayField("public_key", "Public Key"),
            GatewayField("secret_key", "Secret Key", secret=True),
        ),
        implemented=False,
    ),
    GatewayDef(
        code="phonepe",
        name="PhonePe",
        description="India — UPI and PhonePe wallet via PhonePe Payment Gateway.",
        fields=(
            GatewayField("merchant_id", "Merchant ID"),
            GatewayField("salt_key", "Salt Key", secret=True),
            GatewayField(
                "salt_index",
                "Salt Index",
                secret=False,
                required=False,
                placeholder="1",
                help="PhonePe key index; defaults to 1 if not set.",
            ),
        ),
        implemented=True,
    ),
    GatewayDef(
        code="flutterwave",
        name="Flutterwave",
        description="Cards, mobile money and bank transfers across Africa and globally.",
        fields=(
            GatewayField("public_key", "Public Key", placeholder="FLWPUBK_TEST-..."),
            GatewayField("secret_key", "Secret Key", secret=True, placeholder="FLWSECK_TEST-..."),
            GatewayField(
                "webhook_secret_hash",
                "Webhook Secret Hash",
                secret=True,
                required=False,
                help="verif-hash value for webhook signature verification.",
            ),
        ),
        implemented=True,
    ),
    GatewayDef(
        code="payfast",
        name="PayFast",
        description="South African payment gateway — cards, EFT and instant EFT.",
        fields=(
            GatewayField("merchant_id", "Merchant ID"),
            GatewayField("merchant_key", "Merchant Key", secret=True),
            GatewayField(
                "passphrase",
                "Passphrase",
                secret=True,
                required=False,
                help="Optional security passphrase for signature generation.",
            ),
        ),
        implemented=False,
    ),
    GatewayDef(
        code="tap",
        name="Tap Payments",
        description="Cards and digital wallets across the Gulf (UAE, Saudi Arabia, Kuwait).",
        fields=(
            GatewayField("secret_key", "Secret Key", secret=True),
            GatewayField("publishable_key", "Publishable Key"),
        ),
        implemented=False,
    ),
    GatewayDef(
        code="mock",
        name="Mock Gateway",
        description="In-process simulator for local development — no real charges.",
        fields=(),
        supports_environment=False,
        implemented=True,
    ),
)

# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

_BY_CODE: dict[str, GatewayDef] = {d.code: d for d in _DEFS}


def get_gateway(code: str) -> GatewayDef | None:
    """Return the GatewayDef for *code*, or None if unknown."""
    return _BY_CODE.get(code)


def all_gateways() -> tuple[GatewayDef, ...]:
    return _DEFS
