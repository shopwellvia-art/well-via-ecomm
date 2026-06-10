"""Customer address book endpoints.

Pattern: wishlist.py (get_current_user + Depends(get_db) per call, thin
routing with all logic delegated to the service layer).
"""
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.address import AddressCreate, AddressRead, AddressUpdate
from app.services.address_service import AddressService

router = APIRouter()


@router.get("", response_model=list[AddressRead])
def list_addresses(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return AddressService(db).list_for_user(user.id)


@router.post("", response_model=AddressRead, status_code=status.HTTP_201_CREATED)
def create_address(
    payload: AddressCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return AddressService(db).create(user.id, payload)


@router.put("/{address_id}", response_model=AddressRead)
def update_address(
    address_id: int,
    payload: AddressUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return AddressService(db).update(user.id, address_id, payload)


@router.delete("/{address_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_address(
    address_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    AddressService(db).delete(user.id, address_id)


@router.post("/{address_id}/default", response_model=AddressRead)
def set_default_address(
    address_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return AddressService(db).set_default(user.id, address_id)
