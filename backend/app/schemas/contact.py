from datetime import datetime

from pydantic import ConfigDict, EmailStr, Field
from app.schemas.base import AppSchema


class ContactMessageCreate(AppSchema):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=20)
    subject: str | None = Field(default=None, max_length=200)
    message: str = Field(min_length=10, max_length=5000)


class ContactMessageRead(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    phone: str | None
    subject: str | None
    message: str
    status: str
    created_at: datetime


class NewsletterSubscribeIn(AppSchema):
    email: EmailStr
