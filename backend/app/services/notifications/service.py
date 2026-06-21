"""Dispatcher that fans out a single domain event to email + SMS.

``notify(order, event)`` is the only entry point. Callers don't decide which
channels fire — that's a function of (event, settings, customer profile):

  - The per-event setting must be ON (admin toggle in /admin/settings).
  - Email always fires when the event is enabled (transactional).
  - SMS fires only if the event has an SMS template key AND the user has a phone.

Template rendering goes through the ``email_templates`` engine so admins can
customise every message without a redeploy. The hardcoded wording in
``notifications/templates.py`` has been retired; context builders live in
``services/email_templates/catalog.py``.

Failures are logged and swallowed. Order actions never fail on notification.
"""
from __future__ import annotations

import enum
import logging
from typing import NamedTuple

from sqlalchemy.orm import Session

from app.email import send_email
from app.models.order import Order
from app.services.email_templates.catalog import (
    order_context,
    return_context,
    sms_order_paid_context,
    sms_order_shipped_context,
)
from app.services.email_templates.renderer import render_email, render_sms
from app.services.settings_service import SettingsService
from app.sms import send_sms

logger = logging.getLogger(__name__)


class NotificationEvent(str, enum.Enum):
    ORDER_PAID = "order_paid"
    ORDER_SHIPPED = "order_shipped"
    ORDER_DELIVERED = "order_delivered"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_REFUNDED = "order_refunded"
    # Returns workflow — operate on a ReturnRequest, not an Order.
    RETURN_REFUNDED = "return_refunded"
    RETURN_REJECTED = "return_rejected"


class _EventEntry(NamedTuple):
    settings_key: str          # e.g. "notifications.order_paid"
    email_template_key: str    # e.g. "order_paid"
    sms_template_key: str | None  # e.g. "sms_order_paid", or None for email-only


_EVENT_TABLE: dict[NotificationEvent, _EventEntry] = {
    NotificationEvent.ORDER_PAID: _EventEntry(
        "notifications.order_paid", "order_paid", "sms_order_paid"
    ),
    NotificationEvent.ORDER_SHIPPED: _EventEntry(
        "notifications.order_shipped", "order_shipped", "sms_order_shipped"
    ),
    NotificationEvent.ORDER_DELIVERED: _EventEntry(
        "notifications.order_delivered", "order_delivered", None
    ),
    NotificationEvent.ORDER_CANCELLED: _EventEntry(
        "notifications.order_cancelled", "order_cancelled", None
    ),
    NotificationEvent.ORDER_REFUNDED: _EventEntry(
        "notifications.order_refunded", "order_refunded", None
    ),
}


# Return-flow events map (settings_key, email_template_key). These are
# email-only and keyed off a ReturnRequest, so they live outside _EVENT_TABLE
# (which is order-shaped) and are dispatched via notify_return().
_RETURN_EVENT_TABLE: dict[NotificationEvent, tuple[str, str]] = {
    NotificationEvent.RETURN_REFUNDED: (
        "notifications.return_refunded", "return_refunded"
    ),
    NotificationEvent.RETURN_REJECTED: (
        "notifications.return_rejected", "return_rejected"
    ),
}


def _sms_context(event: NotificationEvent, order: Order) -> dict:
    """Return the appropriate SMS context dict for an event."""
    if event == NotificationEvent.ORDER_PAID:
        return sms_order_paid_context(order)
    if event == NotificationEvent.ORDER_SHIPPED:
        return sms_order_shipped_context(order)
    # No other events currently have SMS templates.
    return {}


class NotificationService:
    def __init__(self, db: Session):
        self.db = db

    def notify(self, order: Order, event: NotificationEvent) -> None:
        entry = _EVENT_TABLE.get(event)
        if entry is None:
            logger.warning("unknown notification event: %s", event)
            return

        if not SettingsService(self.db).get_bool(entry.settings_key, default=True):
            return

        user = order.user
        if user is None or not user.email:
            logger.info(
                "notify %s skipped — order %s has no customer email",
                event.value,
                order.id,
            )
            return

        # ---- Email ----
        try:
            ctx = order_context(order)
            subject, html, text = render_email(self.db, entry.email_template_key, ctx)
            if subject or html or text:
                send_email(
                    to=user.email,
                    subject=subject or f"Update for order #{order.id}",
                    body=text,
                    html=html or None,
                    db=self.db,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "notify %s email failed for order %s: %s", event.value, order.id, exc
            )

        # ---- SMS ----
        if entry.sms_template_key is not None and (user.phone or "").strip():
            try:
                sms_ctx = _sms_context(event, order)
                body_text = render_sms(self.db, entry.sms_template_key, sms_ctx)
                if body_text:
                    send_sms(to=user.phone.strip(), body=body_text, db=self.db)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "notify %s sms failed for order %s: %s",
                    event.value,
                    order.id,
                    exc,
                )

    def notify_return(
        self,
        req,  # ReturnRequest — untyped to avoid a model import cycle
        event: NotificationEvent,
        *,
        timeline_days: int,
    ) -> None:
        """Email the customer about a return outcome (refund issued / rejected).

        Email-only and best-effort — a notification failure never breaks the
        admin's return action. ``timeline_days`` is quoted to the customer as
        the expected time for the refund to reflect.
        """
        entry = _RETURN_EVENT_TABLE.get(event)
        if entry is None:
            logger.warning("unknown return notification event: %s", event)
            return
        settings_key, template_key = entry
        if not SettingsService(self.db).get_bool(settings_key, default=True):
            return

        user = req.user
        if user is None or not user.email:
            logger.info(
                "notify %s skipped — return %s has no customer email",
                event.value, req.id,
            )
            return

        try:
            ctx = return_context(req, timeline_days=timeline_days)
            subject, html, text = render_email(self.db, template_key, ctx)
            if subject or html or text:
                send_email(
                    to=user.email,
                    subject=subject or f"Update on your return #{req.id}",
                    body=text,
                    html=html or None,
                    db=self.db,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "notify %s email failed for return %s: %s",
                event.value, req.id, exc,
            )
