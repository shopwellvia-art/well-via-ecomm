"""Canonical defaults for the ``email_templates`` table.

Mirrors ``settings_seed.py`` in spirit:
  - ``DEFAULT_TEMPLATES`` is the single source of truth for shipped defaults.
  - ``seed_email_templates(db)`` is idempotent and safe on every boot / DB-truncate.
  - Missing rows are INSERTED with the full default; existing rows have their
    metadata (name, description, group_name, channel) refreshed, but
    ``subject``/``body_html``/``body_design`` are NEVER overwritten so
    admin customisations survive restarts.
  - ``get_default(key)`` returns the static default dict for a given key — used
    by the reset endpoint.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.email_template import EmailTemplate

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layout shell
# ---------------------------------------------------------------------------
_LAYOUT_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{{ store_name }}</title>
</head>
<body style="margin:0;padding:0;background-color:#f4f4f5;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;color:#1a1a2e;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f4f5;padding:32px 16px;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">

          <!-- Header -->
          <tr>
            <td style="background-color:#16213e;padding:24px 32px;text-align:center;">
              {% if logo_url %}
              <img src="{{ logo_url }}" alt="{{ store_name }}" height="48" style="display:block;margin:0 auto;max-height:48px;" />
              {% else %}
              <span style="font-size:22px;font-weight:700;color:#ffffff;letter-spacing:0.5px;">{{ store_name }}</span>
              {% endif %}
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:32px 32px 24px;">
              {{ content }}
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background-color:#f8f8fa;border-top:1px solid #e8e8ed;padding:20px 32px;text-align:center;">
              <p style="margin:0 0 4px;font-size:12px;color:#888888;">
                {{ footer_address }}
              </p>
              <p style="margin:0;font-size:12px;color:#888888;">
                Need help? <a href="mailto:{{ support_email }}" style="color:#0066cc;text-decoration:none;">{{ support_email }}</a>
              </p>
              <p style="margin:8px 0 0;font-size:11px;color:#bbbbbb;">
                &copy; {{ current_year }} {{ store_name }}. All rights reserved.
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Order: paid
# ---------------------------------------------------------------------------
_ORDER_PAID_SUBJECT = "Order #{{ order_id }} confirmed — thanks for shopping with us"
_ORDER_PAID_HTML = """\
<h2 style="margin:0 0 16px;font-size:22px;color:#16213e;">Hi {{ customer_name }},</h2>
<p style="margin:0 0 12px;font-size:15px;line-height:1.6;color:#333333;">
  Thanks for your order! We&#39;ve received your payment and are getting things ready.
</p>

<!-- Items table -->
<table width="100%" cellpadding="0" cellspacing="0" style="margin:20px 0;border-collapse:collapse;">
  <thead>
    <tr style="background-color:#f0f4ff;">
      <th style="padding:10px 12px;text-align:left;font-size:13px;color:#555555;border-bottom:2px solid #dde3f0;">Item</th>
      <th style="padding:10px 12px;text-align:center;font-size:13px;color:#555555;border-bottom:2px solid #dde3f0;">Qty</th>
      <th style="padding:10px 12px;text-align:right;font-size:13px;color:#555555;border-bottom:2px solid #dde3f0;">Price</th>
      <th style="padding:10px 12px;text-align:right;font-size:13px;color:#555555;border-bottom:2px solid #dde3f0;">Total</th>
    </tr>
  </thead>
  <tbody>
    {% for item in items %}
    <tr>
      <td style="padding:10px 12px;font-size:14px;color:#333333;border-bottom:1px solid #eeeeee;">{{ item.name }}</td>
      <td style="padding:10px 12px;font-size:14px;color:#333333;text-align:center;border-bottom:1px solid #eeeeee;">{{ item.quantity }}</td>
      <td style="padding:10px 12px;font-size:14px;color:#333333;text-align:right;border-bottom:1px solid #eeeeee;">{{ order_currency }} {{ item.unit_price }}</td>
      <td style="padding:10px 12px;font-size:14px;color:#333333;text-align:right;border-bottom:1px solid #eeeeee;">{{ order_currency }} {{ item.line_total }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>

<!-- Totals -->
<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:20px;">
  <tr>
    <td style="padding:4px 0;font-size:14px;color:#666666;">Subtotal</td>
    <td style="padding:4px 0;font-size:14px;color:#333333;text-align:right;">{{ order_currency }} {{ order_subtotal }}</td>
  </tr>
  <tr>
    <td style="padding:4px 0;font-size:14px;color:#666666;">Tax</td>
    <td style="padding:4px 0;font-size:14px;color:#333333;text-align:right;">{{ order_currency }} {{ order_tax }}</td>
  </tr>
  <tr>
    <td style="padding:4px 0;font-size:14px;color:#666666;">Discount</td>
    <td style="padding:4px 0;font-size:14px;color:#333333;text-align:right;">&minus;{{ order_currency }} {{ order_discount }}</td>
  </tr>
  <tr>
    <td style="padding:8px 0 0;font-size:16px;font-weight:700;color:#16213e;border-top:2px solid #dde3f0;">Total</td>
    <td style="padding:8px 0 0;font-size:16px;font-weight:700;color:#16213e;text-align:right;border-top:2px solid #dde3f0;">{{ order_currency }} {{ order_total }}</td>
  </tr>
</table>

<p style="margin:0 0 8px;font-size:14px;color:#666666;"><strong>Shipping to:</strong></p>
<p style="margin:0 0 20px;font-size:14px;color:#333333;white-space:pre-line;">{{ shipping_address }}</p>

<p style="margin:0;font-size:14px;color:#555555;">
  We&#39;ll email you once your order ships. View your order anytime:
  <a href="{{ order_url }}" style="color:#0066cc;text-decoration:none;">Order #{{ order_id }}</a>
</p>
"""

# ---------------------------------------------------------------------------
# Order: shipped
# ---------------------------------------------------------------------------
_ORDER_SHIPPED_SUBJECT = "Your order #{{ order_id }} is on its way"
_ORDER_SHIPPED_HTML = """\
<h2 style="margin:0 0 16px;font-size:22px;color:#16213e;">Hi {{ customer_name }},</h2>
<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#333333;">
  Great news &#8212; your order <strong>#{{ order_id }}</strong> has shipped!
</p>

{% if carrier or tracking_number %}
<table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 20px;background-color:#f0f9f4;border-left:4px solid #27ae60;border-radius:4px;padding:16px;">
  <tr>
    <td style="padding:12px 16px;">
      {% if carrier %}
      <p style="margin:0 0 6px;font-size:14px;color:#555555;"><strong>Carrier:</strong> {{ carrier }}</p>
      {% endif %}
      {% if tracking_number %}
      <p style="margin:0;font-size:14px;color:#555555;"><strong>Tracking #:</strong> {{ tracking_number }}</p>
      {% endif %}
    </td>
  </tr>
</table>
{% endif %}

<p style="margin:0 0 8px;font-size:14px;color:#666666;"><strong>Shipping to:</strong></p>
<p style="margin:0 0 20px;font-size:14px;color:#333333;white-space:pre-line;">{{ shipping_address }}</p>

<p style="margin:0;font-size:14px;color:#555555;">
  Track your order:
  <a href="{{ order_url }}" style="color:#0066cc;text-decoration:none;">Order #{{ order_id }}</a>
</p>
"""

# ---------------------------------------------------------------------------
# Order: delivered
# ---------------------------------------------------------------------------
_ORDER_DELIVERED_SUBJECT = "Your order #{{ order_id }} has arrived"
_ORDER_DELIVERED_HTML = """\
<h2 style="margin:0 0 16px;font-size:22px;color:#16213e;">Hi {{ customer_name }},</h2>
<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#333333;">
  Your order <strong>#{{ order_id }}</strong> was just marked delivered. We hope you love it!
</p>
<p style="margin:0 0 16px;font-size:14px;color:#555555;">
  If anything isn&#39;t right, simply reply to this email and we&#39;ll make it right.
</p>
<p style="margin:0;font-size:14px;color:#555555;">
  Enjoyed your purchase? Leave a review:
  <a href="{{ order_url }}" style="color:#0066cc;text-decoration:none;">View Order #{{ order_id }}</a>
</p>
"""

# ---------------------------------------------------------------------------
# Order: cancelled
# ---------------------------------------------------------------------------
_ORDER_CANCELLED_SUBJECT = "Order #{{ order_id }} has been cancelled"
_ORDER_CANCELLED_HTML = """\
<h2 style="margin:0 0 16px;font-size:22px;color:#16213e;">Hi {{ customer_name }},</h2>
<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#333333;">
  Your order <strong>#{{ order_id }}</strong> has been cancelled.
</p>

<table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 20px;background-color:#fff5f5;border-left:4px solid #e74c3c;border-radius:4px;">
  <tr>
    <td style="padding:12px 16px;font-size:14px;color:#555555;">
      <strong>Reason:</strong> {{ refund_reason }}
    </td>
  </tr>
</table>

<p style="margin:0 0 16px;font-size:14px;color:#555555;">
  Any payment will be returned to your original payment method within a few business days.
</p>
<p style="margin:0;font-size:14px;color:#555555;">
  Questions? Reply to this email or visit
  <a href="{{ store_url }}" style="color:#0066cc;text-decoration:none;">{{ store_name }}</a>.
</p>
"""

# ---------------------------------------------------------------------------
# Order: refunded
# ---------------------------------------------------------------------------
_ORDER_REFUNDED_SUBJECT = "Refund issued for order #{{ order_id }}"
_ORDER_REFUNDED_HTML = """\
<h2 style="margin:0 0 16px;font-size:22px;color:#16213e;">Hi {{ customer_name }},</h2>
<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#333333;">
  A refund of <strong>{{ order_currency }} {{ order_total }}</strong> has been issued for
  order <strong>#{{ order_id }}</strong>.
</p>

<table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 20px;background-color:#f0f9f4;border-left:4px solid #27ae60;border-radius:4px;">
  <tr>
    <td style="padding:12px 16px;font-size:14px;color:#555555;">
      <strong>Reason:</strong> {{ refund_reason }}
    </td>
  </tr>
</table>

<p style="margin:0 0 16px;font-size:14px;color:#555555;">
  The amount should appear on your statement within a few business days.
</p>
<p style="margin:0;font-size:14px;color:#555555;">
  Questions? Reply to this email or visit
  <a href="{{ store_url }}" style="color:#0066cc;text-decoration:none;">{{ store_name }}</a>.
</p>
"""

# ---------------------------------------------------------------------------
# Account: password reset
# ---------------------------------------------------------------------------
_PASSWORD_RESET_SUBJECT = "Your {{ store_name }} password reset code"
_PASSWORD_RESET_HTML = """\
<h2 style="margin:0 0 16px;font-size:22px;color:#16213e;">Hi {{ customer_name }},</h2>
<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#333333;">
  We received a request to reset the password for your account. Use the code below to proceed.
</p>

<table width="100%" cellpadding="0" cellspacing="0" style="margin:24px 0;">
  <tr>
    <td align="center">
      <div style="display:inline-block;background-color:#16213e;color:#ffffff;font-size:28px;font-weight:700;letter-spacing:8px;padding:18px 32px;border-radius:8px;font-family:'Courier New',Courier,monospace;">
        {{ otp_code }}
      </div>
    </td>
  </tr>
</table>

<p style="margin:0 0 16px;font-size:14px;color:#666666;text-align:center;">
  This code expires in <strong>{{ expiry_minutes }} minutes</strong>.
</p>
<p style="margin:0;font-size:13px;color:#999999;text-align:center;">
  If you didn&#39;t request a password reset, you can safely ignore this email.
  Your password won&#39;t change.
</p>
"""

# ---------------------------------------------------------------------------
# SMS templates (plain Jinja2 text, no HTML)
# ---------------------------------------------------------------------------
_SMS_ORDER_PAID_BODY = (
    "Order #{{ order_id }} confirmed. {{ order_total }} paid. "
    "We'll text you when it ships."
)

_SMS_ORDER_SHIPPED_BODY = (
    "Order #{{ order_id }} shipped"
    "{% if carrier %} via {{ carrier }}{% endif %}"
    "{% if tracking_number %} #{{ tracking_number }}{% endif %}."
)

# ---------------------------------------------------------------------------
# Master defaults list
# ---------------------------------------------------------------------------
# (key, channel, name, description, group_name, subject, body_html, body_design)
DEFAULT_TEMPLATES: list[dict] = [
    {
        "key": "_layout",
        "channel": "email",
        "name": "Email Layout (master shell)",
        "description": (
            "The outer HTML wrapper that wraps every email body. "
            "Use {{ content }} to inject the per-email body."
        ),
        "group_name": "branding",
        "subject": None,
        "body_html": _LAYOUT_HTML,
        "body_design": None,
        "is_enabled": True,
    },
    {
        "key": "order_paid",
        "channel": "email",
        "name": "Order Paid",
        "description": "Sent when a customer's payment is confirmed.",
        "group_name": "orders",
        "subject": _ORDER_PAID_SUBJECT,
        "body_html": _ORDER_PAID_HTML,
        "body_design": None,
        "is_enabled": True,
    },
    {
        "key": "order_shipped",
        "channel": "email",
        "name": "Order Shipped",
        "description": "Sent when an order is dispatched with a tracking number.",
        "group_name": "orders",
        "subject": _ORDER_SHIPPED_SUBJECT,
        "body_html": _ORDER_SHIPPED_HTML,
        "body_design": None,
        "is_enabled": True,
    },
    {
        "key": "order_delivered",
        "channel": "email",
        "name": "Order Delivered",
        "description": "Sent when an order is marked as delivered.",
        "group_name": "orders",
        "subject": _ORDER_DELIVERED_SUBJECT,
        "body_html": _ORDER_DELIVERED_HTML,
        "body_design": None,
        "is_enabled": True,
    },
    {
        "key": "order_cancelled",
        "channel": "email",
        "name": "Order Cancelled",
        "description": "Sent when an order is cancelled.",
        "group_name": "orders",
        "subject": _ORDER_CANCELLED_SUBJECT,
        "body_html": _ORDER_CANCELLED_HTML,
        "body_design": None,
        "is_enabled": True,
    },
    {
        "key": "order_refunded",
        "channel": "email",
        "name": "Order Refunded",
        "description": "Sent when a refund is issued for an order.",
        "group_name": "orders",
        "subject": _ORDER_REFUNDED_SUBJECT,
        "body_html": _ORDER_REFUNDED_HTML,
        "body_design": None,
        "is_enabled": True,
    },
    {
        "key": "password_reset",
        "channel": "email",
        "name": "Password Reset OTP",
        "description": "Delivers the 6-digit OTP for password resets.",
        "group_name": "account",
        "subject": _PASSWORD_RESET_SUBJECT,
        "body_html": _PASSWORD_RESET_HTML,
        "body_design": None,
        "is_enabled": True,
    },
    {
        "key": "sms_order_paid",
        "channel": "sms",
        "name": "SMS — Order Paid",
        "description": "SMS notification sent when a customer's order is confirmed.",
        "group_name": "sms",
        "subject": None,
        "body_html": _SMS_ORDER_PAID_BODY,
        "body_design": None,
        "is_enabled": True,
    },
    {
        "key": "sms_order_shipped",
        "channel": "sms",
        "name": "SMS — Order Shipped",
        "description": "SMS notification sent when an order is dispatched.",
        "group_name": "sms",
        "subject": None,
        "body_html": _SMS_ORDER_SHIPPED_BODY,
        "body_design": None,
        "is_enabled": True,
    },
]

_DEFAULTS_BY_KEY: dict[str, dict] = {d["key"]: d for d in DEFAULT_TEMPLATES}


def get_default(key: str) -> dict | None:
    """Return the static default dict for a key (used by reset + renderer fallback)."""
    return _DEFAULTS_BY_KEY.get(key)


def seed_email_templates(db: Session) -> int:
    """Ensure every shipped default exists in ``email_templates``.

    Idempotent — safe on every app boot and after a DB truncate.
    * Missing keys are inserted with the full default content.
    * Existing keys have name/description/group_name/channel refreshed only;
      subject/body_html/body_design are NEVER overwritten.

    Returns the number of newly inserted rows.
    """
    existing: dict[str, EmailTemplate] = {
        row.key: row
        for row in db.execute(select(EmailTemplate)).scalars().all()
    }
    inserted = 0
    for tpl in DEFAULT_TEMPLATES:
        key = tpl["key"]
        row = existing.get(key)
        if row is None:
            db.add(
                EmailTemplate(
                    key=key,
                    channel=tpl["channel"],
                    name=tpl["name"],
                    description=tpl.get("description"),
                    group_name=tpl["group_name"],
                    subject=tpl.get("subject"),
                    body_html=tpl["body_html"],
                    body_design=tpl.get("body_design"),
                    is_enabled=tpl.get("is_enabled", True),
                )
            )
            inserted += 1
        else:
            # Refresh metadata only — never overwrite customised content.
            row.name = tpl["name"]
            row.description = tpl.get("description")
            row.group_name = tpl["group_name"]
            row.channel = tpl["channel"]
            # is_enabled: only set if the row has never been explicitly toggled
            # by an admin (heuristic: description change triggers this code path
            # — we leave is_enabled as stored and let the admin control it).
    db.commit()
    if inserted:
        logger.info(
            "email_templates seed: inserted %d missing default template(s)", inserted
        )
    return inserted
