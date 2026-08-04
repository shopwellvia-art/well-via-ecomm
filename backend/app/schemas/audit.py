from datetime import datetime
from typing import Any

from pydantic import ConfigDict
from app.schemas.base import AppSchema


class AuditActorBrief(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str


class AuditEventRead(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    id: int
    # Denormalized copies survive actor deletion; we surface them either way.
    actor_user_id: int | None
    actor_email: str | None
    actor_ip: str | None
    action: str
    target_type: str | None
    target_id: int | None
    target_label: str | None
    summary: str
    extra: dict[str, Any] | None
    created_at: datetime


class AuditEventListPage(AppSchema):
    items: list[AuditEventRead]
    total: int
    page: int
    page_size: int
    # Helper for the UI's action filter dropdown — the distinct list of
    # actions that have ever fired. Cheap because there are only a handful.
    available_actions: list[str]
