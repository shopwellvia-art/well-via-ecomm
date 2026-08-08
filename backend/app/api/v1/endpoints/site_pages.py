"""Storefront company/content pages (singleton GET/PUT).

GET /site-pages  — public, no auth required.
PUT /site-pages  — requires the `frontend.manage` permission.

Backs the footer "About" links: Contact Us, About Us, Careers, Wellvia Stories,
Press, and Corporate Information.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission
from app.schemas.site_pages import SitePagesRead, SitePagesUpdate
from app.services.site_pages_service import SitePagesService

router = APIRouter()


@router.get("", response_model=SitePagesRead)
def get_site_pages(db: Session = Depends(get_db)):
    """Return the current company-pages configuration. Public — no
    authentication required so the storefront can fetch it without a token."""
    return SitePagesService(db).get()


@router.put(
    "",
    response_model=SitePagesRead,
    dependencies=[Depends(require_permission("frontend.manage"))],
)
def update_site_pages(payload: SitePagesUpdate, db: Session = Depends(get_db)):
    """Replace the company-pages configuration. Requires `frontend.manage`."""
    return SitePagesService(db).update(payload)
