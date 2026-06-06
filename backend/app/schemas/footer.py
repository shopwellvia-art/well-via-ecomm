"""Pydantic schemas for the storefront footer configuration.

The footer is stored as a single JSON document in the `footer_configs` table.
DEFAULT_FOOTER is the single source of truth for factory defaults — the
service falls back to it when no row exists or when a top-level key is absent.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

from app.schemas._validators import validate_safe_url


# ---------------------------------------------------------------------------
# Nested models
# ---------------------------------------------------------------------------

class TrustFeature(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    icon: str
    title: str
    sub: str


class Brand(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    tagline: str
    # Optional uploaded logo. When set, the storefront renders this image in
    # place of the icon + wordmark in both the navbar and footer.
    logo_url: str = ""

    @field_validator("logo_url")
    @classmethod
    def _check_logo_url(cls, v: str) -> str:
        return validate_safe_url(v)


class Newsletter(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    enabled: bool
    placeholder: str
    note: str
    success: str


class FooterLink(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    label: str
    to: str


class LinkColumn(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    title: str
    links: list[FooterLink]


class MailUs(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    heading: str
    lines: list[str]


class Phone(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    display: str
    tel: str


class RegisteredOffice(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    heading: str
    lines: list[str]
    cin: str
    phones: list[Phone]


class SocialLink(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    icon: str
    label: str
    href: str

    @field_validator("href")
    @classmethod
    def _check_href(cls, v: str) -> str:
        return validate_safe_url(v)


class BottomLink(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    icon: str
    label: str
    to: str


# ---------------------------------------------------------------------------
# Canonical defaults — single source of truth used by FooterService
# ---------------------------------------------------------------------------

_ADDRESS_LINES = [
    "Lumen Internet Pvt. Ltd.,",
    "Buildings Alyssa, Begonia &",
    "Clove Embassy Tech Village,",
    "Outer Ring Road, Devarabeesanahalli Village,",
    "Bengaluru, 560103,",
    "Karnataka, India",
]

DEFAULT_FOOTER: dict = {
    "trust_features": [
        {"icon": "Truck", "title": "Free Shipping", "sub": "On orders over ₹500"},
        {"icon": "RotateCcw", "title": "Easy Returns", "sub": "30-day return window"},
        {"icon": "ShieldCheck", "title": "Secure Payment", "sub": "256-bit SSL encryption"},
        {"icon": "Headphones", "title": "24/7 Support", "sub": "Real humans, anytime"},
    ],
    "brand": {
        "name": "Lumen",
        "tagline": (
            "Modern essentials, thoughtfully sourced. "
            "Join our newsletter for early drops and member-only pricing."
        ),
        "logo_url": "",
    },
    "newsletter": {
        "enabled": True,
        "placeholder": "you@example.com",
        "note": "No spam. Unsubscribe anytime.",
        "success": "You're on the list. Welcome to Lumen.",
    },
    "link_columns": [
        {
            "title": "About",
            "links": [
                {"label": "Contact Us", "to": "/contact"},
                {"label": "About Us", "to": "/about"},
                {"label": "Careers", "to": "/careers"},
                {"label": "Lumen Stories", "to": "/stories"},
                {"label": "Press", "to": "/press"},
                {"label": "Corporate Information", "to": "/corporate"},
            ],
        },
        {
            "title": "Group",
            "links": [
                {"label": "Aura", "to": "/brands/aura"},
                {"label": "Voyage", "to": "/brands/voyage"},
                {"label": "Forge", "to": "/brands/forge"},
            ],
        },
        {
            "title": "Help",
            "links": [
                {"label": "Payments", "to": "/help/payments"},
                {"label": "Shipping", "to": "/help/shipping"},
                {"label": "Cancellation & Returns", "to": "/help/returns"},
                {"label": "FAQ", "to": "/help/faq"},
            ],
        },
        {
            "title": "Consumer Policy",
            "links": [
                {"label": "Cancellation & Returns", "to": "/policy/returns"},
                {"label": "Terms of Use", "to": "/terms"},
                {"label": "Security", "to": "/security"},
                {"label": "Privacy", "to": "/privacy"},
                {"label": "Sitemap", "to": "/sitemap"},
                {"label": "Grievance Redressal", "to": "/grievance"},
                {"label": "EPR Compliance", "to": "/epr"},
            ],
        },
    ],
    "mail_us": {
        "heading": "Mail Us",
        "lines": _ADDRESS_LINES,
    },
    "registered_office": {
        "heading": "Registered Office Address",
        "lines": _ADDRESS_LINES,
        "cin": "U51109KA2026PTC066107",
        "phones": [
            {"display": "044-4561 4700", "tel": "+914445614700"},
            {"display": "044-6741 5800", "tel": "+914467415800"},
        ],
    },
    "social_links": [
        {"icon": "Facebook", "label": "Facebook", "href": "https://facebook.com/lumen"},
        {"icon": "Twitter", "label": "Twitter", "href": "https://twitter.com/lumen"},
        {"icon": "Youtube", "label": "YouTube", "href": "https://youtube.com/lumen"},
        {"icon": "Instagram", "label": "Instagram", "href": "https://instagram.com/lumen"},
    ],
    "bottom_links": [
        {"icon": "Store", "label": "Become a Seller", "to": "/sell"},
        {"icon": "Megaphone", "label": "Advertise", "to": "/advertise"},
        {"icon": "Gift", "label": "Gift Cards", "to": "/gift-cards"},
        {"icon": "LifeBuoy", "label": "Help Center", "to": "/help"},
    ],
    "payment_methods": ["VISA", "MC", "AmEx", "UPI", "RuPay", "Net Banking", "COD", "EMI"],
    "copyright": "© 2007–{year} Lumen.com",
}


# ---------------------------------------------------------------------------
# Top-level document schemas
# ---------------------------------------------------------------------------

class FooterConfigRead(BaseModel):
    """Full footer document returned by GET /footer."""

    model_config = ConfigDict(from_attributes=True)

    trust_features: list[TrustFeature]
    brand: Brand
    newsletter: Newsletter
    link_columns: list[LinkColumn]
    mail_us: MailUs
    registered_office: RegisteredOffice
    social_links: list[SocialLink]
    bottom_links: list[BottomLink]
    payment_methods: list[str]
    copyright: str


class LogoUploadResponse(BaseModel):
    """Returned by POST /footer/logo after a successful upload."""

    url: str


class FooterConfigUpdate(BaseModel):
    """PUT body — full replace semantics.

    Every top-level field defaults to the canonical default so a client that
    omits a section doesn't accidentally blank it out. In practice the admin UI
    sends the complete document back.
    """

    model_config = ConfigDict(from_attributes=True)

    trust_features: list[TrustFeature] = [
        TrustFeature(**item) for item in DEFAULT_FOOTER["trust_features"]
    ]
    brand: Brand = Brand(**DEFAULT_FOOTER["brand"])
    newsletter: Newsletter = Newsletter(**DEFAULT_FOOTER["newsletter"])
    link_columns: list[LinkColumn] = [
        LinkColumn(**col) for col in DEFAULT_FOOTER["link_columns"]
    ]
    mail_us: MailUs = MailUs(**DEFAULT_FOOTER["mail_us"])
    registered_office: RegisteredOffice = RegisteredOffice(
        **DEFAULT_FOOTER["registered_office"]
    )
    social_links: list[SocialLink] = [
        SocialLink(**item) for item in DEFAULT_FOOTER["social_links"]
    ]
    bottom_links: list[BottomLink] = [
        BottomLink(**item) for item in DEFAULT_FOOTER["bottom_links"]
    ]
    payment_methods: list[str] = DEFAULT_FOOTER["payment_methods"]
    copyright: str = DEFAULT_FOOTER["copyright"]
