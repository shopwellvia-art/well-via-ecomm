"""Pydantic v2 schemas for the email/SMS template admin API."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import ConfigDict, EmailStr
from app.schemas.base import AppSchema


class EmailTemplateListItem(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    key: str
    channel: str
    name: str
    description: str | None
    group_name: str
    subject: str | None
    is_enabled: bool
    updated_at: datetime


class EmailTemplateDetail(EmailTemplateListItem):
    body_html: str
    body_design: dict | None
    # Catalog-supplied variable list for the editor sidebar.
    variables: list[dict] = []
    # The factory-default subject/body so the UI can show a diff.
    default_subject: str | None = None
    default_body_html: str | None = None


class EmailTemplateUpdate(AppSchema):
    subject: str | None = None
    body_html: str | None = None
    body_design: dict | None = None
    is_enabled: bool | None = None


class EmailTemplatePreviewRequest(AppSchema):
    subject: str | None = None
    body_html: str | None = None


class EmailTemplatePreviewResponse(AppSchema):
    subject: str | None
    html: str


class EmailTemplateTestRequest(AppSchema):
    to: EmailStr


class EmailTemplateListResponse(AppSchema):
    items: list[EmailTemplateListItem]
