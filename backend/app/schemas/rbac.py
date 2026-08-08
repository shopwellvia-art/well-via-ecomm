from datetime import datetime

from pydantic import ConfigDict, Field
from app.schemas.base import AppSchema


class PermissionRead(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    group_name: str | None


class RoleCreate(AppSchema):
    name: str = Field(min_length=2, max_length=50)
    description: str | None = None
    permission_ids: list[int] = Field(default_factory=list)


class RoleUpdate(AppSchema):
    name: str | None = Field(default=None, min_length=2, max_length=50)
    description: str | None = None
    permission_ids: list[int] | None = None


class RoleRead(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    is_system: bool
    permissions: list[PermissionRead]
    created_at: datetime
    updated_at: datetime


class UserRolesUpdate(AppSchema):
    role_ids: list[int]
