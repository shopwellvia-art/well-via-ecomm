from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas._validators import validate_safe_url


class Perk(BaseModel):
    """A single trust feature shown on a sale slide (icon + label)."""

    model_config = ConfigDict(from_attributes=True)

    icon: str = "ShieldCheck"
    label: str


class HeroSlideRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    image_url: str
    alt: str | None
    sort_order: int
    is_active: bool
    # carousel upgrade fields
    kind: str
    eyebrow: str | None
    heading: str | None
    subtext: str | None
    badge_text: str | None
    cta_label: str | None
    cta_href: str | None
    cta2_label: str | None
    cta2_href: str | None
    countdown_end: datetime | None
    countdown_label: str | None
    perks: list[Perk] | None
    text_theme: str
    created_at: datetime
    updated_at: datetime


class HeroSlideUpdate(BaseModel):
    alt: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None
    # carousel upgrade fields
    kind: Literal["photo", "sale"] | None = None
    eyebrow: str | None = Field(default=None, max_length=120)
    heading: str | None = Field(default=None, max_length=200)
    subtext: str | None = Field(default=None, max_length=400)
    badge_text: str | None = Field(default=None, max_length=80)
    cta_label: str | None = Field(default=None, max_length=80)
    cta_href: str | None = Field(default=None, max_length=512)
    cta2_label: str | None = Field(default=None, max_length=80)
    cta2_href: str | None = Field(default=None, max_length=512)
    countdown_end: datetime | None = None
    countdown_label: str | None = Field(default=None, max_length=80)
    perks: list[Perk] | None = None
    text_theme: Literal["light", "dark"] | None = None

    @field_validator("cta_href", "cta2_href")
    @classmethod
    def _check_cta_href(cls, v: str | None) -> str | None:
        return validate_safe_url(v)


class HeroSlideReorder(BaseModel):
    ids: list[int]
