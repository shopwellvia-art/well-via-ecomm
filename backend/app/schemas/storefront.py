"""Pydantic schemas for the admin-controlled storefront configuration.

The config lives as seven `storefront.*` rows in `system_settings` (boot-seeded
by settings_seed.py): scalars as plain strings, nav_items / homepage_sections
as JSON strings. DEFAULT_STOREFRONT is the single source of truth for factory
defaults — the service falls back to it for any key whose stored value is the
empty string. Must stay in sync with STOREFRONT_DEFAULTS in
frontend/src/features/storefront-config/defaults.js.
"""
from __future__ import annotations

from pydantic import ConfigDict, field_validator

from app.schemas._validators import validate_safe_url
from app.schemas.base import AppSchema


# ---------------------------------------------------------------------------
# Nested models
# ---------------------------------------------------------------------------

class NavItem(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    label: str
    to: str
    # camelCase field names mirror the frontend document verbatim.
    end: bool = False
    megaMenu: bool = False
    visible: bool = True

    @field_validator("label")
    @classmethod
    def _check_label(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Nav label must not be empty")
        if len(v) > 200:
            raise ValueError("Nav label must be 200 characters or fewer")
        return v

    @field_validator("to")
    @classmethod
    def _check_to(cls, v: str) -> str:
        # Nav links stay on-site: a relative path only, never an absolute or
        # protocol-relative URL (which would let an admin point "Home" off-site).
        v = v.strip()
        if not v.startswith("/") or v.startswith("//"):
            raise ValueError("Nav link must be a site-relative path starting with '/'")
        return v


class HomepageSection(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    key: str
    label: str
    visible: bool = True
    # Optional admin heading override; blank = the section's built-in heading.
    title: str = ""

    @field_validator("label")
    @classmethod
    def _check_label(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Section label must not be empty")
        if len(v) > 200:
            raise ValueError("Section label must be 200 characters or fewer")
        return v

    @field_validator("title")
    @classmethod
    def _check_title(cls, v: str) -> str:
        v = v.strip()
        if len(v) > 200:
            raise ValueError("Section title must be 200 characters or fewer")
        return v


# ---------------------------------------------------------------------------
# Canonical defaults — single source of truth used by StorefrontConfigService
# ---------------------------------------------------------------------------

DEFAULT_STOREFRONT: dict = {
    "site_title": "Wellvia — Wellness Redefined",
    "brand_name": "WELLVIA",
    "tagline": "Wellness Redefined",
    "logo_url": "",
    "favicon_url": "",
    "nav_items": [
        {"label": "Home", "to": "/", "end": True, "visible": True},
        {"label": "Shop", "to": "/products", "megaMenu": True, "visible": True},
        {"label": "Categories", "to": "/categories", "visible": True},
        {"label": "Best Sellers", "to": "/bestsellers", "visible": True},
        {"label": "New Arrivals", "to": "/new-arrivals", "visible": True},
        {"label": "Contact", "to": "/contact", "visible": True},
        {"label": "About", "to": "/about", "visible": True},
    ],
    "homepage_sections": [
        {"key": "hero", "label": "Hero banner", "visible": True, "title": ""},
        {"key": "why-we-exist", "label": "Why We Exist", "visible": True, "title": ""},
        {"key": "bestsellers-rail", "label": "Bestsellers rail", "visible": True, "title": ""},
        {"key": "new-launches", "label": "New Launches", "visible": True, "title": ""},
        {"key": "daily-routine", "label": "Build Your Daily Routine", "visible": True, "title": ""},
        {"key": "blog", "label": "Blog cards", "visible": True, "title": ""},
        {"key": "reviews", "label": "Reviews carousel", "visible": True, "title": ""},
        {"key": "brand-philosophy", "label": "Brand philosophy (puzzle)", "visible": True, "title": ""},
    ],
}

# The homepage renderer only knows these sections — the admin can reorder,
# retitle, or hide them, but never invent or drop one.
SECTION_KEYS: tuple[str, ...] = tuple(
    section["key"] for section in DEFAULT_STOREFRONT["homepage_sections"]
)


# ---------------------------------------------------------------------------
# Top-level document schemas
# ---------------------------------------------------------------------------

class StorefrontConfigRead(AppSchema):
    """Full config document returned by GET /storefront-config."""

    model_config = ConfigDict(from_attributes=True)

    site_title: str
    brand_name: str
    tagline: str
    logo_url: str
    favicon_url: str
    nav_items: list[NavItem]
    homepage_sections: list[HomepageSection]


class ImageUploadResponse(AppSchema):
    """Returned by POST /storefront-config/image after a successful upload."""

    url: str


class StorefrontConfigUpdate(AppSchema):
    """PUT body — full replace semantics.

    Every field defaults to the canonical default so a client that omits a
    field doesn't accidentally blank it out. In practice the admin UI sends
    the complete document back.
    """

    model_config = ConfigDict(from_attributes=True)

    site_title: str = DEFAULT_STOREFRONT["site_title"]
    brand_name: str = DEFAULT_STOREFRONT["brand_name"]
    tagline: str = DEFAULT_STOREFRONT["tagline"]
    logo_url: str = DEFAULT_STOREFRONT["logo_url"]
    favicon_url: str = DEFAULT_STOREFRONT["favicon_url"]
    nav_items: list[NavItem] = [
        NavItem(**item) for item in DEFAULT_STOREFRONT["nav_items"]
    ]
    homepage_sections: list[HomepageSection] = [
        HomepageSection(**item) for item in DEFAULT_STOREFRONT["homepage_sections"]
    ]

    @field_validator("site_title", "brand_name", "tagline")
    @classmethod
    def _check_text(cls, v: str) -> str:
        # Empty is allowed — it means "unset, fall back to the default".
        v = v.strip()
        if len(v) > 200:
            raise ValueError("Must be 200 characters or fewer")
        return v

    @field_validator("logo_url", "favicon_url")
    @classmethod
    def _check_image_url(cls, v: str) -> str:
        return validate_safe_url(v)

    @field_validator("nav_items")
    @classmethod
    def _check_nav_items(cls, v: list[NavItem]) -> list[NavItem]:
        if not 1 <= len(v) <= 20:
            raise ValueError("nav_items must contain between 1 and 20 links")
        return v

    @field_validator("homepage_sections")
    @classmethod
    def _check_homepage_sections(cls, v: list[HomepageSection]) -> list[HomepageSection]:
        keys = [section.key for section in v]
        expected = set(SECTION_KEYS)
        duplicates = sorted({k for k in keys if keys.count(k) > 1})
        if duplicates:
            raise ValueError(f"homepage_sections contains duplicate keys: {duplicates}")
        unknown = sorted(set(keys) - expected)
        if unknown:
            raise ValueError(
                f"homepage_sections contains unknown keys: {unknown} — "
                f"valid keys are {sorted(expected)}"
            )
        missing = sorted(expected - set(keys))
        if missing:
            raise ValueError(f"homepage_sections is missing keys: {missing}")
        return v
