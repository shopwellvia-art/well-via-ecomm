"""Variable catalog and context-builder helpers for email/SMS templates.

``VARIABLES`` declares the whitelisted tokens for each template key so the
admin editor can render a "variables" sidebar.  ``sample_context`` returns
realistic dummy data for preview renders.  The real context builders
(``order_context``, ``password_reset_context``, etc.) build live data from
domain objects and are used by ``NotificationService`` and ``AuthService``.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.order import Order
    from app.models.return_request import ReturnRequest
    from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Variable catalog
# ---------------------------------------------------------------------------
# Each entry: {"token": str, "label": str, "sample": str}
# For list tokens (items) the sample field describes the shape.

_ORDER_VARS = [
    {"token": "customer_name", "label": "Customer first name", "sample": "Asha"},
    {"token": "order_id", "label": "Order ID", "sample": "1042"},
    {"token": "order_currency", "label": "Currency code", "sample": "INR"},
    {"token": "order_subtotal", "label": "Order subtotal", "sample": "1 499.00"},
    {"token": "order_tax", "label": "Tax amount", "sample": "75.00"},
    {"token": "order_discount", "label": "Discount amount", "sample": "100.00"},
    {"token": "order_total", "label": "Order total", "sample": "1 474.00"},
    {"token": "shipping_address", "label": "Shipping address", "sample": "12, MG Road, Bengaluru 560001"},
    {
        "token": "items",
        "label": "Order items (list — use {% for item in items %})",
        "sample": "[{quantity, name, unit_price, line_total}]",
    },
    {"token": "tracking_number", "label": "Tracking number", "sample": "DL1234567890"},
    {"token": "carrier", "label": "Carrier name", "sample": "Delhivery"},
    {"token": "refund_reason", "label": "Cancellation / refund reason", "sample": "Out of stock"},
    {"token": "store_name", "label": "Store name", "sample": "ShopWellvia"},
    {"token": "store_url", "label": "Store URL", "sample": "https://shopwellvia.in"},
    {"token": "order_url", "label": "Order detail URL", "sample": "https://shopwellvia.in/orders/1042"},
]

_RETURN_VARS = [
    {"token": "customer_name", "label": "Customer first name", "sample": "Asha"},
    {"token": "order_id", "label": "Order ID", "sample": "1042"},
    {"token": "return_id", "label": "Return ID", "sample": "57"},
    {"token": "refund_currency", "label": "Currency code", "sample": "INR"},
    {"token": "refund_amount", "label": "Refund amount", "sample": "698.00"},
    {"token": "refund_method", "label": "Where the refund was routed", "sample": "your original payment method (PhonePe)"},
    {"token": "refund_reference", "label": "Refund reference id", "sample": "T2606211108350210407761"},
    {"token": "refund_timeline_days", "label": "Business days to reflect", "sample": "7"},
    {"token": "reason", "label": "Return reason", "sample": "Arrived damaged"},
    {"token": "admin_notes", "label": "Inspection / rejection notes", "sample": "Item matched the reported damage."},
    {"token": "store_name", "label": "Store name", "sample": "ShopWellvia"},
    {"token": "store_url", "label": "Store URL", "sample": "https://shopwellvia.in"},
    {"token": "order_url", "label": "Order detail URL", "sample": "https://shopwellvia.in/orders/1042"},
]

VARIABLES: dict[str, list[dict]] = {
    "_layout": [
        {"token": "content", "label": "Rendered body HTML (injected by the layout engine)", "sample": "<p>Email body here</p>"},
        {"token": "store_name", "label": "Store name", "sample": "ShopWellvia"},
        {"token": "store_url", "label": "Store URL", "sample": "https://shopwellvia.in"},
        {"token": "logo_url", "label": "Logo image URL", "sample": "https://shopwellvia.in/logo.png"},
        {"token": "footer_address", "label": "Company address for email footer", "sample": "ShopWellvia Pvt Ltd, Bengaluru, Karnataka 560001"},
        {"token": "support_email", "label": "Support email address", "sample": "support@shopwellvia.in"},
        {"token": "current_year", "label": "Current year", "sample": "2026"},
    ],
    "order_paid": _ORDER_VARS,
    "order_shipped": _ORDER_VARS,
    "order_delivered": _ORDER_VARS,
    "order_cancelled": _ORDER_VARS,
    "order_refunded": _ORDER_VARS,
    "return_refunded": _RETURN_VARS,
    "return_rejected": _RETURN_VARS,
    "password_reset": [
        {"token": "customer_name", "label": "Customer first name", "sample": "Asha"},
        {"token": "otp_code", "label": "6-digit OTP", "sample": "482910"},
        {"token": "expiry_minutes", "label": "OTP expiry in minutes", "sample": "10"},
        {"token": "store_name", "label": "Store name", "sample": "ShopWellvia"},
    ],
    "sms_order_paid": [
        {"token": "order_id", "label": "Order ID", "sample": "1042"},
        {"token": "order_total", "label": "Order total (formatted)", "sample": "INR 1 474.00"},
    ],
    "sms_order_shipped": [
        {"token": "order_id", "label": "Order ID", "sample": "1042"},
        {"token": "carrier", "label": "Carrier name", "sample": "Delhivery"},
        {"token": "tracking_number", "label": "Tracking number", "sample": "DL1234567890"},
    ],
}

# ---------------------------------------------------------------------------
# Sample contexts (for preview + smoke tests)
# ---------------------------------------------------------------------------

_SAMPLE_ITEMS = [
    {"quantity": 2, "name": "Organic Wellness Tea (250g)", "unit_price": "349.00", "line_total": "698.00"},
    {"quantity": 1, "name": "Ashwagandha Capsules (60 caps)", "unit_price": "799.00", "line_total": "799.00"},
]


def sample_context(key: str) -> dict:
    """Return realistic sample data for a template key — used in preview renders."""
    base_order = {
        "customer_name": "Asha",
        "order_id": "1042",
        "order_currency": "INR",
        "order_subtotal": "1 497.00",
        "order_tax": "75.00",
        "order_discount": "100.00",
        "order_total": "1 472.00",
        "shipping_address": "12, MG Road, Apt 4B\nBengaluru, Karnataka 560001",
        "items": _SAMPLE_ITEMS,
        "tracking_number": "DL1234567890",
        "carrier": "Delhivery",
        "refund_reason": "Item out of stock",
        "store_name": "ShopWellvia",
        "store_url": "https://shopwellvia.in",
        "order_url": "https://shopwellvia.in/orders/1042",
    }

    if key in ("order_paid", "order_shipped", "order_delivered", "order_cancelled", "order_refunded"):
        return base_order

    if key in ("return_refunded", "return_rejected"):
        return {
            "customer_name": "Asha",
            "order_id": "1042",
            "return_id": "57",
            "refund_currency": "INR",
            "refund_amount": "698.00",
            "refund_method": "your original payment method (PhonePe)",
            "refund_reference": "T2606211108350210407761",
            "refund_timeline_days": "7",
            "reason": "Arrived damaged",
            "admin_notes": "Item matched the reported damage.",
            "store_name": "ShopWellvia",
            "store_url": "https://shopwellvia.in",
            "order_url": "https://shopwellvia.in/orders/1042",
        }

    if key == "password_reset":
        return {
            "customer_name": "Asha",
            "otp_code": "482910",
            "expiry_minutes": "10",
            "store_name": "ShopWellvia",
        }

    if key == "sms_order_paid":
        return {"order_id": "1042", "order_total": "INR 1 472.00"}

    if key == "sms_order_shipped":
        return {"order_id": "1042", "carrier": "Delhivery", "tracking_number": "DL1234567890"}

    if key == "_layout":
        return {
            "content": "<p>Sample email body content goes here.</p>",
            "store_name": "ShopWellvia",
            "store_url": "https://shopwellvia.in",
            "logo_url": "",
            "footer_address": "ShopWellvia Pvt Ltd, Bengaluru, Karnataka 560001",
            "support_email": "support@shopwellvia.in",
            "current_year": str(datetime.now().year),
        }

    # Fallback for unknown keys
    return {}


# ---------------------------------------------------------------------------
# Real context builders
# ---------------------------------------------------------------------------

def _customer_name(order: "Order") -> str:
    if order.user and order.user.full_name:
        return order.user.full_name.split(" ")[0]
    return "there"


def _money(currency: str, value) -> str:
    return f"{currency} {float(value):.2f}"


def _items_list(order: "Order") -> list[dict]:
    if not order.items:
        return []
    return [
        {
            "quantity": item.quantity,
            "name": getattr(item, "product_name", None) or f"Product #{item.product_id}",
            "unit_price": f"{float(item.unit_price):.2f}",
            "line_total": f"{float(item.unit_price * item.quantity):.2f}",
        }
        for item in order.items
    ]


def order_context(order: "Order") -> dict:
    """Build the live context dict for any order-related template."""
    currency = order.currency or "INR"
    return {
        "customer_name": _customer_name(order),
        "order_id": str(order.id),
        "order_currency": currency,
        "order_subtotal": f"{float(order.subtotal):.2f}",
        "order_tax": f"{float(order.tax_amount):.2f}",
        "order_discount": f"{float(order.discount_amount):.2f}",
        "order_total": f"{float(order.total_amount):.2f}",
        "shipping_address": order.shipping_address or "No address on file",
        "items": _items_list(order),
        "tracking_number": order.tracking_number or "",
        "carrier": order.carrier or "",
        "refund_reason": (order.refund_reason or "").strip() or "Cancelled by the store",
        "store_name": "ShopWellvia",
        "store_url": "https://shopwellvia.in",
        "order_url": f"https://shopwellvia.in/orders/{order.id}",
    }


_REASON_LABELS = {
    "defective": "Defective",
    "wrong_item": "Wrong item",
    "not_as_described": "Not as described",
    "arrived_damaged": "Arrived damaged",
    "no_longer_needed": "No longer needed",
    "other": "Other",
}

# Friendly names for the gateway a refund is routed back through.
_GATEWAY_LABELS = {
    "phonepe": "PhonePe",
    "razorpay": "Razorpay",
    "stripe": "Stripe",
    "paypal": "PayPal",
    "paystack": "Paystack",
    "flutterwave": "Flutterwave",
    "mock": "the payment gateway",
}


def _refund_method_label(method: str | None) -> str:
    """Human phrasing for where the refund lands, used in the email body."""
    if not method or method == "manual":
        return "a manual bank transfer to your account"
    pretty = _GATEWAY_LABELS.get(method, method)
    return f"your original payment method ({pretty})"


def return_context(req: "ReturnRequest", *, timeline_days: int) -> dict:
    """Build the live context for the return refund / rejection emails."""
    order = getattr(req, "order", None)
    user = getattr(req, "user", None)
    currency = (getattr(order, "currency", None) or "INR")
    if user and user.full_name:
        name = user.full_name.split(" ")[0]
    else:
        name = "there"
    reason = req.reason or ""
    return {
        "customer_name": name,
        "order_id": str(req.order_id),
        "return_id": str(req.id),
        "refund_currency": currency,
        "refund_amount": f"{float(req.refund_amount or 0):.2f}",
        "refund_method": _refund_method_label(req.refund_method),
        "refund_reference": req.refund_reference or "",
        "refund_timeline_days": str(timeline_days),
        "reason": _REASON_LABELS.get(reason, reason.replace("_", " ").title() or "—"),
        "admin_notes": (req.admin_notes or "").strip(),
        "store_name": "ShopWellvia",
        "store_url": "https://shopwellvia.in",
        "order_url": f"https://shopwellvia.in/orders/{req.order_id}",
    }


def password_reset_context(customer_name: str, otp_code: str, expiry_minutes: int) -> dict:
    return {
        "customer_name": customer_name,
        "otp_code": otp_code,
        "expiry_minutes": str(expiry_minutes),
        "store_name": "ShopWellvia",
    }


def sms_order_paid_context(order: "Order") -> dict:
    currency = order.currency or "INR"
    return {
        "order_id": str(order.id),
        "order_total": _money(currency, order.total_amount),
    }


def sms_order_shipped_context(order: "Order") -> dict:
    return {
        "order_id": str(order.id),
        "carrier": order.carrier or "",
        "tracking_number": order.tracking_number or "",
    }


def branding_context(db: "Session") -> dict:
    """Pull branding values from system_settings where sensible keys exist,
    with clean defaults. Never raises — missing settings → defaults."""
    from datetime import datetime as _dt
    from app.core.config import settings as env_settings

    store_name = "ShopWellvia"
    store_url = "https://shopwellvia.in"
    logo_url = ""
    footer_address = "ShopWellvia Pvt Ltd, Bengaluru, Karnataka 560001"
    support_email = env_settings.EMAIL_FROM or "support@shopwellvia.in"

    try:
        from app.services.settings_service import SettingsService
        svc = SettingsService(db)
        _sn = svc.get_raw("store.name")
        if _sn:
            store_name = _sn
        _su = svc.get_raw("store.url")
        if _su:
            store_url = _su
        _lu = svc.get_raw("store.logo_url")
        if _lu:
            logo_url = _lu
        _fa = svc.get_raw("store.footer_address")
        if _fa:
            footer_address = _fa
        _se = svc.get_raw("store.support_email")
        if _se:
            support_email = _se
        # Fallback: email.from is a reliable key
        _ef = svc.get_raw("email.from")
        if _ef and support_email == env_settings.EMAIL_FROM:
            support_email = _ef
    except Exception:  # noqa: BLE001 — always return something usable
        pass

    return {
        "store_name": store_name,
        "store_url": store_url,
        "logo_url": logo_url,
        "footer_address": footer_address,
        "support_email": support_email,
        "current_year": str(_dt.now().year),
    }
