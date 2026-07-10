from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, rate_limit_by_ip
from app.schemas.contact import (
    ContactMessageCreate,
    ContactMessageRead,
    NewsletterSubscribeIn,
)
from app.services.contact_service import ContactService

router = APIRouter()


@router.post(
    "/contact",
    response_model=ContactMessageRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(rate_limit_by_ip(scope="contact.ip", limit=5, window_sec=3600))
    ],
)
def submit_contact_message(payload: ContactMessageCreate, db: Session = Depends(get_db)):
    return ContactService(db).submit_message(payload)


@router.post(
    "/newsletter/subscribe",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[
        Depends(rate_limit_by_ip(scope="newsletter.ip", limit=10, window_sec=3600))
    ],
)
def subscribe_newsletter(payload: NewsletterSubscribeIn, db: Session = Depends(get_db)):
    ContactService(db).subscribe_newsletter(payload.email)
