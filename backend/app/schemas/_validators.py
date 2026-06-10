"""Shared Pydantic field validators.

`validate_safe_url` rejects dangerous URL schemes (javascript:, data:, vbscript:
…) on admin-editable URL fields that the storefront renders into anchor `href`
attributes. Without this an admin/staffer with content-management permission
could plant a `javascript:` URI that executes in a visitor's browser (stored
XSS). The frontend `safeUrl()` helper is the second layer of this defense.

`normalize_phone` strips whitespace/dashes and an optional leading +91 so that
"98765 43210", "+91-98765-43210", and "9876543210" all become "9876543210".
Used in AddressCreate and reusable for any phone field in this codebase.
"""
from __future__ import annotations

import re

# Schemes we allow in a link target. Anything else (javascript, data, vbscript,
# file, …) is rejected. Empty strings and site-relative paths are also allowed.
_SAFE_SCHEMES = {"http", "https", "mailto", "tel"}

# Browsers ignore ASCII control characters inside a URL, so an attacker can hide
# a dangerous scheme as e.g. "java\tscript:alert(1)". Strip them before parsing.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.\-]*):")


def is_safe_url(value: str | None) -> bool:
    if value is None:
        return True
    cleaned = _CONTROL_CHARS.sub("", value).strip()
    if cleaned == "":
        return True
    # Protocol-relative ("//host") inherits the page scheme and points off-site —
    # disallow. Must be checked BEFORE the single-"/" relative-path case.
    if cleaned.startswith("//"):
        return False
    # Site-relative path or in-page anchor — always safe.
    if cleaned.startswith("/") or cleaned.startswith("#"):
        return True
    match = _SCHEME_RE.match(cleaned.lower())
    if match:
        return match.group(1) in _SAFE_SCHEMES
    # No scheme and not "//": a bare relative path like "page" — safe.
    return True


def normalize_phone(value: str | None) -> str | None:
    """Strip spaces, dashes, and the optional leading country code (+91 or 91)
    from a phone number string, then validate that the result is 8–20 digits.

    Returns the normalized digit-only string, or raises ValueError for the
    Pydantic field_validator to surface.  None passes through unchanged
    (use a separate Required check when the field is mandatory).
    """
    if value is None:
        return None
    # Remove spaces, dashes, dots — common formatting characters.
    cleaned = re.sub(r"[\s\-.]", "", value)
    # Strip optional leading + sign.
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    # Strip optional Indian country code (91) if the result would still be
    # a plausible 10-digit mobile number.  Only strip when it's followed by
    # exactly 10 digits so "919191919191" (a 12-digit number starting with 91)
    # is kept intact.
    if re.match(r"^91\d{10}$", cleaned):
        cleaned = cleaned[2:]
    if not cleaned.isdigit():
        raise ValueError("Phone number must contain only digits (and optional +91 prefix / spaces / dashes)")
    if not (8 <= len(cleaned) <= 20):
        raise ValueError("Phone number must be 8–20 digits")
    return cleaned


def validate_safe_url(value: str | None) -> str | None:
    """Pydantic-friendly validator: returns the value if its URL scheme is safe,
    otherwise raises ValueError. Use with `field_validator(..., mode='after')`."""
    if not is_safe_url(value):
        raise ValueError(
            "Unsafe URL scheme — use http(s), mailto, tel, or a relative path"
        )
    return value
