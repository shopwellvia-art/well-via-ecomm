"""Legacy plain-text template functions — superseded by the email_templates engine.

These wrappers exist only for backward compatibility; no code in this repo
imports them any more.  The active send path uses
``app.services.email_templates.renderer.render_email`` / ``render_sms``
and the context builders in ``app.services.email_templates.catalog``.

Do NOT add new templates here.  To extend notifications, add a row to
``services/email_templates/seed.py`` and wire the key into
``notifications/service.py``'s ``_EVENT_TABLE``.
"""
from __future__ import annotations

from app.models.order import Order
from app.services.email_templates.catalog import (
    order_context,
    sms_order_paid_context,
    sms_order_shipped_context,
)


# ---- helpers (kept for any external callers) ----

def _customer_name(order: Order) -> str:
    if order.user and order.user.full_name:
        return order.user.full_name.split(" ")[0]
    return "there"


def _money(order: Order, value) -> str:
    return f"{order.currency} {float(value):.2f}"


def _items_block(order: Order) -> str:
    if not order.items:
        return ""
    lines = []
    for item in order.items:
        lines.append(
            f"  • {item.quantity}× #{item.product_id} @ {_money(order, item.unit_price)}"
        )
    return "\n".join(lines)


# ---- email stubs (plain-text) ----

def email_order_paid(order: Order) -> tuple[str, str]:
    ctx = order_context(order)
    subject = f"Order #{ctx['order_id']} confirmed — thanks for shopping with us"
    body = (
        f"Hi {ctx['customer_name']},\n\n"
        f"Thanks for your order! Total: {ctx['order_currency']} {ctx['order_total']}\n"
    )
    return subject, body


def email_order_shipped(order: Order) -> tuple[str, str]:
    ctx = order_context(order)
    subject = f"Your order #{ctx['order_id']} is on its way"
    body = f"Hi {ctx['customer_name']},\n\nYour order #{ctx['order_id']} has shipped.\n"
    return subject, body


def email_order_delivered(order: Order) -> tuple[str, str]:
    ctx = order_context(order)
    subject = f"Your order #{ctx['order_id']} has arrived"
    body = f"Hi {ctx['customer_name']},\n\nYour order #{ctx['order_id']} was delivered.\n"
    return subject, body


def email_order_cancelled(order: Order) -> tuple[str, str]:
    ctx = order_context(order)
    subject = f"Order #{ctx['order_id']} cancelled"
    body = f"Hi {ctx['customer_name']},\n\nYour order #{ctx['order_id']} was cancelled.\n"
    return subject, body


def email_order_refunded(order: Order) -> tuple[str, str]:
    ctx = order_context(order)
    subject = f"Order #{ctx['order_id']} refunded"
    body = (
        f"Hi {ctx['customer_name']},\n\n"
        f"A refund of {ctx['order_currency']} {ctx['order_total']} has been issued.\n"
    )
    return subject, body


# ---- SMS stubs ----

def sms_order_paid(order: Order) -> str:
    ctx = sms_order_paid_context(order)
    return (
        f"Order #{ctx['order_id']} confirmed. {ctx['order_total']} paid. "
        "We'll text you when it ships."
    )


def sms_order_shipped(order: Order) -> str:
    ctx = sms_order_shipped_context(order)
    parts = [f"Order #{ctx['order_id']} shipped"]
    if ctx.get("carrier"):
        parts.append(f"via {ctx['carrier']}")
    if ctx.get("tracking_number"):
        parts.append(f"#{ctx['tracking_number']}")
    return " ".join(parts) + "."
