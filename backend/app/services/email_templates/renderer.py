"""Jinja2-backed template renderer for email and SMS.

Security layers:
  1. SandboxedEnvironment — prevents arbitrary code execution in templates.
  2. autoescape=True for HTML emails — context values like customer names / addresses
     are HTML-escaped before being interpolated so stored-XSS via order data is
     impossible.
  3. bleach.clean on the rendered body with a tight allowlist — final line of
     defence against any tag that sneaked through.
  4. CSS inlining (best-effort) — wrapped in try/except so a library failure
     never breaks a transactional send.
"""
from __future__ import annotations

import html
import logging
import re
import textwrap
from typing import TYPE_CHECKING

import bleach
import markupsafe
from jinja2.sandbox import SandboxedEnvironment

# bleach >= 6 requires an explicit CSS sanitizer when 'style' attrs are allowed.
try:
    from bleach.css_sanitizer import CSSSanitizer as _CSSSanitizer
    _CSS_SANITIZER = _CSSSanitizer()
except Exception:  # noqa: BLE001 — tinycss2 not installed
    _CSS_SANITIZER = None

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CSS inliner — try css_inline, then premailer, then no-op
# ---------------------------------------------------------------------------
_CSS_INLINE_AVAILABLE = False

try:
    import css_inline as _css_inline_lib  # type: ignore[import]
    _CSS_INLINE_AVAILABLE = True
    _INLINER = _css_inline_lib.CSSInliner()

    def _inline_css(html_str: str) -> str:
        return _INLINER.inline(html_str)

except ImportError:
    try:
        import premailer as _premailer_lib  # type: ignore[import]
        _CSS_INLINE_AVAILABLE = True

        def _inline_css(html_str: str) -> str:
            return _premailer_lib.transform(html_str)

    except ImportError:
        logger.debug(
            "Neither css_inline nor premailer is installed — "
            "CSS inlining is disabled (emails will still render correctly in modern clients)."
        )

        def _inline_css(html_str: str) -> str:  # type: ignore[misc]
            return html_str

# ---------------------------------------------------------------------------
# bleach allowlist
# ---------------------------------------------------------------------------
_ALLOWED_TAGS = [
    "p", "br", "hr",
    "h1", "h2", "h3", "h4",
    "strong", "em", "u", "b", "i", "s",
    "a",
    "ul", "ol", "li",
    "span", "div",
    "img",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    "blockquote",
    "pre", "code",
]

_ALLOWED_ATTRS: dict = {
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "width", "height", "style"],
    "span": ["style"],
    "div": ["style"],
    "p": ["style"],
    "td": ["style", "colspan", "rowspan", "align", "valign", "width", "height", "bgcolor"],
    "th": ["style", "colspan", "rowspan", "align", "valign", "width", "height", "bgcolor"],
    "tr": ["style", "bgcolor"],
    "table": ["style", "width", "cellpadding", "cellspacing", "border", "bgcolor", "align"],
    "blockquote": ["style"],
    "h1": ["style"], "h2": ["style"], "h3": ["style"], "h4": ["style"],
}

# ---------------------------------------------------------------------------
# Jinja2 environments
# ---------------------------------------------------------------------------
# HTML email: autoescape=True so every context value is safe-escaped by default.
_HTML_ENV = SandboxedEnvironment(autoescape=True)

# SMS / plain text: autoescape=False — we want raw strings, no HTML entities.
_TEXT_ENV = SandboxedEnvironment(autoescape=False)


def _render_html_template(template_str: str, context: dict) -> str:
    tpl = _HTML_ENV.from_string(template_str)
    return tpl.render(**context)


def _render_text_template(template_str: str, context: dict) -> str:
    tpl = _TEXT_ENV.from_string(template_str)
    return tpl.render(**context)


def _sanitize_html(raw_html: str) -> str:
    kwargs = dict(
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        strip=True,
    )
    if _CSS_SANITIZER is not None:
        kwargs["css_sanitizer"] = _CSS_SANITIZER
    return bleach.clean(raw_html, **kwargs)


def _html_to_text(html_str: str) -> str:
    """Best-effort plain-text extraction: strip tags, unescape, collapse space."""
    no_tags = bleach.clean(html_str, tags=[], strip=True)
    unescaped = html.unescape(no_tags)
    # Collapse 3+ blank lines → single blank line; strip each line
    lines = [line.strip() for line in unescaped.splitlines()]
    collapsed: list[str] = []
    blank_run = 0
    for line in lines:
        if line == "":
            blank_run += 1
            if blank_run <= 1:
                collapsed.append("")
        else:
            blank_run = 0
            collapsed.append(line)
    return "\n".join(collapsed).strip()


def _load_or_default(db: "Session", key: str):
    """Return the EmailTemplate row if it exists, else load the seeded default dict."""
    from app.repositories.email_template_repository import EmailTemplateRepository
    repo = EmailTemplateRepository(db)
    row = repo.get_by_key(key)
    if row is not None:
        return row
    # Fall back to the seed defaults (the row may not have been inserted yet,
    # or this is a fresh DB that hasn't had the seeder run yet).
    from app.services.email_templates.seed import get_default
    return get_default(key)


def render_email(db: "Session", key: str, context: dict) -> tuple[str, str, str]:
    """Render a full email using the stored template.

    Returns ``(subject, html, text)`` where:
      * ``subject`` — rendered Jinja2 subject line
      * ``html``    — layout-wrapped, sanitized, CSS-inlined HTML
      * ``text``    — plain-text fallback
    """
    from app.services.email_templates.catalog import branding_context

    row_or_default = _load_or_default(db, key)

    # Unknown key (not stored, no shipped default) — nothing to render.
    if row_or_default is None:
        logger.warning("render_email: unknown template key %r — nothing rendered", key)
        return "", "", ""

    # Accept both ORM objects and plain dicts (the seed-default fallback is a dict).
    if isinstance(row_or_default, dict):
        raw_subject = row_or_default.get("subject") or ""
        raw_body_html = row_or_default.get("body_html") or ""
        is_enabled = row_or_default.get("is_enabled", True)
    else:
        raw_subject = row_or_default.subject or ""
        raw_body_html = row_or_default.body_html or ""
        is_enabled = row_or_default.is_enabled

    if not is_enabled:
        logger.debug("render_email: template %s is disabled — returning empty output", key)
        return "", "", ""

    # 1. Render subject and body_html with autoescape=True.
    rendered_subject = _render_html_template(raw_subject, context)
    # Unescape the subject — it's plain text, not HTML.
    rendered_subject = html.unescape(rendered_subject)

    rendered_body = _render_html_template(raw_body_html, context)

    # 2. Sanitize the rendered body (stored-XSS defence).
    sanitized_body = _sanitize_html(rendered_body)

    # 3. Load the _layout template and wrap. The body is injected as a Markup
    #    object so the layout's {{ content }} slot renders it un-escaped.
    layout_row_or_default = _load_or_default(db, "_layout")
    if isinstance(layout_row_or_default, dict):
        raw_layout = layout_row_or_default.get("body_html") or "{{ content }}"
    else:
        raw_layout = layout_row_or_default.body_html or "{{ content }}"

    brand = branding_context(db)
    layout_ctx = {**brand, "content": markupsafe.Markup(sanitized_body)}
    final_html = _render_html_template(raw_layout, layout_ctx)

    # 4. CSS inline (best-effort — never raises).
    try:
        final_html = _inline_css(final_html)
    except Exception as exc:  # noqa: BLE001
        logger.debug("CSS inlining failed (non-fatal): %s", exc)

    # 5. Plain-text fallback from the laid-out HTML.
    text = _html_to_text(final_html)

    return html.unescape(rendered_subject), final_html, text


def render_sms(db: "Session", key: str, context: dict) -> str:
    """Render an SMS template — no autoescape, no layout, plain text out."""
    row_or_default = _load_or_default(db, key)

    if row_or_default is None:
        logger.warning("render_sms: unknown template key %r — nothing rendered", key)
        return ""

    if isinstance(row_or_default, dict):
        raw_body = row_or_default.get("body_html") or ""
        is_enabled = row_or_default.get("is_enabled", True)
    else:
        raw_body = row_or_default.body_html or ""
        is_enabled = row_or_default.is_enabled

    if not is_enabled:
        return ""

    return _render_text_template(raw_body, context).strip()


def preview(
    db: "Session",
    key: str,
    draft_subject: str | None = None,
    draft_body_html: str | None = None,
) -> tuple[str | None, str]:
    """Render a preview using sample_context.

    When ``draft_subject``/``draft_body_html`` are supplied, render those
    strings instead of the stored row — lets the admin see unsaved edits.

    For SMS keys returns ``(None, rendered_text)``.
    For email keys returns ``(rendered_subject, rendered_html)``.
    """
    from app.services.email_templates.catalog import sample_context, branding_context

    ctx = sample_context(key)

    # Determine channel from the row (or default).
    row_or_default = _load_or_default(db, key)
    if row_or_default is None:
        # Unknown key: still let a passed-in draft preview render; infer the
        # channel from the key prefix and treat stored content as empty.
        channel = "sms" if key.startswith("sms") else "email"
        stored_subject = ""
        stored_body = ""
    elif isinstance(row_or_default, dict):
        channel = row_or_default.get("channel", "email")
        stored_subject = row_or_default.get("subject") or ""
        stored_body = row_or_default.get("body_html") or ""
    else:
        channel = row_or_default.channel
        stored_subject = row_or_default.subject or ""
        stored_body = row_or_default.body_html or ""

    if channel == "sms":
        body_to_render = draft_body_html if draft_body_html is not None else stored_body
        rendered = _render_text_template(body_to_render, ctx).strip()
        return None, rendered

    # Email path.
    subject_to_render = draft_subject if draft_subject is not None else stored_subject
    body_to_render = draft_body_html if draft_body_html is not None else stored_body

    rendered_subject = html.unescape(_render_html_template(subject_to_render, ctx))
    rendered_body = _render_html_template(body_to_render, ctx)
    sanitized_body = _sanitize_html(rendered_body)

    # Wrap in layout.
    layout_row = _load_or_default(db, "_layout")
    if isinstance(layout_row, dict):
        raw_layout = layout_row.get("body_html") or "{{ content }}"
    else:
        raw_layout = layout_row.body_html or "{{ content }}"

    brand = branding_context(db)
    layout_ctx = {**brand, "content": markupsafe.Markup(sanitized_body)}
    final_html = _render_html_template(raw_layout, layout_ctx)

    try:
        final_html = _inline_css(final_html)
    except Exception as exc:  # noqa: BLE001
        logger.debug("CSS inlining failed (non-fatal): %s", exc)

    return rendered_subject, final_html
