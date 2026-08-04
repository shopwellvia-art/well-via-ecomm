"""Pydantic schemas for the storefront footer configuration.

The footer is stored as a single JSON document in the `footer_configs` table.
DEFAULT_FOOTER is the single source of truth for factory defaults — the
service falls back to it when no row exists or when a top-level key is absent.
"""
from __future__ import annotations

from pydantic import ConfigDict, field_validator

from app.schemas._validators import validate_safe_url
from app.schemas.base import AppSchema


# ---------------------------------------------------------------------------
# Nested models
# ---------------------------------------------------------------------------

class TrustFeature(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    icon: str
    title: str
    sub: str


class Brand(AppSchema):
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


class Newsletter(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    enabled: bool
    placeholder: str
    note: str
    success: str


class FooterLink(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    label: str
    to: str


class LinkColumn(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    title: str
    links: list[FooterLink]


class MailUs(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    heading: str
    lines: list[str]


class Phone(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    display: str
    tel: str


class RegisteredOffice(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    heading: str
    lines: list[str]
    cin: str
    phones: list[Phone]


class SocialLink(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    icon: str
    label: str
    href: str

    @field_validator("href")
    @classmethod
    def _check_href(cls, v: str) -> str:
        return validate_safe_url(v)


class BottomLink(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    icon: str
    label: str
    to: str


# ---------------------------------------------------------------------------
# Canonical defaults — single source of truth used by FooterService
# ---------------------------------------------------------------------------

# Admin-editable address blocks — hidden until real lines are filled in.
_ADDRESS_LINES: list[str] = []

DEFAULT_FOOTER: dict = {
    "trust_features": [
        {"icon": "Truck", "title": "Free Shipping", "sub": "On orders over ₹500"},
        {"icon": "RotateCcw", "title": "Easy Returns", "sub": "30-day return window"},
        {"icon": "ShieldCheck", "title": "Secure Payment", "sub": "256-bit SSL encryption"},
        {"icon": "Headphones", "title": "24/7 Support", "sub": "Real humans, anytime"},
    ],
    "brand": {
        "name": "WELLVIA",
        "tagline": "Wellness Redefined",
        "logo_url": "",
    },
    "newsletter": {
        "enabled": True,
        "placeholder": "you@example.com",
        "note": "No spam. Unsubscribe anytime.",
        "success": "You're on the list. Welcome to Wellvia.",
    },
    "link_columns": [
        {
            "title": "Shop",
            "links": [
                {"label": "All Products", "to": "/products"},
                {"label": "Best Sellers", "to": "/bestsellers"},
                {"label": "New Arrivals", "to": "/new-arrivals"},
                {"label": "Combos", "to": "/categories"},
                {"label": "Shop by Goal", "to": "/products"},
            ],
        },
        {
            "title": "Explore",
            "links": [
                {"label": "About Us", "to": "/about"},
                {"label": "Blog", "to": "/stories"},
                {"label": "FAQs", "to": "/contact"},
                {"label": "Contact Us", "to": "/contact"},
                {"label": "Track Order", "to": "/orders"},
            ],
        },
        {
            "title": "Customer Care",
            "links": [
                {"label": "Shipping Policy", "to": "/shipping"},
                {"label": "Refund & Cancellation", "to": "/refund"},
                {"label": "Terms & Conditions", "to": "/terms"},
                {"label": "Privacy Policy", "to": "/privacy"},
                {"label": "Contact Us", "to": "/contact"},
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
        "cin": "",
        "phones": [],
    },
    "social_links": [
        {"icon": "Facebook", "label": "Facebook", "href": "https://facebook.com/wellvia"},
        {"icon": "Twitter", "label": "Twitter", "href": "https://twitter.com/wellvia"},
        {"icon": "Youtube", "label": "YouTube", "href": "https://youtube.com/wellvia"},
        {"icon": "Instagram", "label": "Instagram", "href": "https://instagram.com/wellvia"},
    ],
    "bottom_links": [
        {"icon": "Store", "label": "Become a Seller", "to": "/sell"},
        {"icon": "Megaphone", "label": "Advertise", "to": "/advertise"},
        {"icon": "Gift", "label": "Gift Cards", "to": "/gift-cards"},
        {"icon": "LifeBuoy", "label": "Help Center", "to": "/help"},
    ],
    "payment_methods": ["VISA", "Mastercard", "RuPay", "UPI", "AMEX", "PayPal"],
    "copyright": "© Wellvia. All rights reserved.",
}


# ---------------------------------------------------------------------------
# Top-level document schemas
# ---------------------------------------------------------------------------

class FooterConfigRead(AppSchema):
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


class LogoUploadResponse(AppSchema):
    """Returned by POST /footer/logo after a successful upload."""

    url: str


class FooterConfigUpdate(AppSchema):
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
