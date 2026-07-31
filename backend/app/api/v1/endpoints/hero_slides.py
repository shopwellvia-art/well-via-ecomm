import json
from datetime import datetime

from fastapi import APIRouter, Depends, Form, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission
from app.core.exceptions import ValidationError
from app.schemas.hero_slide import (
    HeroSlideRead,
    HeroSlideReorder,
    HeroSlideUpdate,
    Perk,
)
from app.services.hero_slide_service import HeroSlideService

router = APIRouter()


@router.get("", response_model=list[HeroSlideRead])
def list_hero_slides(db: Session = Depends(get_db)):
    return HeroSlideService(db).list_active()


# Staff-only sibling of the public route above: `list_all` returns unpublished
# and inactive slides, `list_active` (line 20) returns only what the storefront
# should render. Same shape, different audience — hence the gate on one and not
# the other.
@router.get(
    "/all",
    response_model=list[HeroSlideRead],
    dependencies=[Depends(require_permission("hero_slides.manage"))],
)
def list_all_hero_slides(db: Session = Depends(get_db)):
    return HeroSlideService(db).list_all()


@router.post(
    "",
    response_model=HeroSlideRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("hero_slides.manage"))],
)
async def create_hero_slide(
    file: UploadFile,
    alt: str | None = Form(default=None),
    kind: str | None = Form(default=None),
    eyebrow: str | None = Form(default=None),
    heading: str | None = Form(default=None),
    subtext: str | None = Form(default=None),
    badge_text: str | None = Form(default=None),
    cta_label: str | None = Form(default=None),
    cta_href: str | None = Form(default=None),
    cta2_label: str | None = Form(default=None),
    cta2_href: str | None = Form(default=None),
    countdown_end: datetime | None = Form(default=None),
    countdown_label: str | None = Form(default=None),
    perks: str | None = Form(default=None),  # JSON-encoded list[{icon,label}]
    text_theme: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    """Create a hero slide with its image and (optionally) its full content in
    a single multipart request. Only `file` is required."""
    file_bytes = await file.read()

    # Collect the optional content columns; omit any that weren't supplied so
    # they fall back to model/server defaults.
    raw = {
        "alt": alt,
        "kind": kind,
        "eyebrow": eyebrow,
        "heading": heading,
        "subtext": subtext,
        "badge_text": badge_text,
        "cta_label": cta_label,
        "cta_href": cta_href,
        "cta2_label": cta2_label,
        "cta2_href": cta2_href,
        "countdown_end": countdown_end,
        "countdown_label": countdown_label,
        "text_theme": text_theme,
    }
    fields = {k: v for k, v in raw.items() if v is not None}

    if perks is not None:
        try:
            parsed = json.loads(perks) if perks.strip() else []
        except json.JSONDecodeError as exc:
            raise ValidationError("Invalid perks JSON") from exc
        # Validate each entry against the Perk schema, store as plain dicts.
        fields["perks"] = [Perk(**p).model_dump() for p in parsed]

    return HeroSlideService(db).create(
        file_bytes=file_bytes,
        filename=file.filename or "image",
        content_type=file.content_type or "",
        fields=fields,
    )


@router.patch(
    "/{slide_id}",
    response_model=HeroSlideRead,
    dependencies=[Depends(require_permission("hero_slides.manage"))],
)
def update_hero_slide(
    slide_id: int, payload: HeroSlideUpdate, db: Session = Depends(get_db)
):
    return HeroSlideService(db).update(slide_id, payload)


@router.delete(
    "/{slide_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("hero_slides.manage"))],
)
def delete_hero_slide(slide_id: int, db: Session = Depends(get_db)):
    HeroSlideService(db).delete(slide_id)


@router.post(
    "/reorder",
    response_model=list[HeroSlideRead],
    dependencies=[Depends(require_permission("hero_slides.manage"))],
)
def reorder_hero_slides(payload: HeroSlideReorder, db: Session = Depends(get_db)):
    return HeroSlideService(db).reorder(payload.ids)
