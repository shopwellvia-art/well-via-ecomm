from typing import Generic, Type, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.base import Base

ModelT = TypeVar("ModelT", bound=Base)

# LIKE treats % and _ as wildcards, so a shopper typing "50%" or "vitamin_b"
# would otherwise match far more than they asked for — and a bare "%" would
# match the entire table. Every free-text repository search escapes through
# `like_pattern`; pass LIKE_ESCAPE as the `escape=` argument alongside it.
LIKE_ESCAPE = "\\"


def like_pattern(term: str, *, prefix_only: bool = False) -> str:
    """`term` as a LIKE pattern with user-supplied wildcards neutralised."""
    safe = term
    # The escape character itself goes first — doing it later would
    # double-escape the backslashes introduced by the % and _ passes.
    for ch in (LIKE_ESCAPE, "%", "_"):
        safe = safe.replace(ch, LIKE_ESCAPE + ch)
    return f"{safe}%" if prefix_only else f"%{safe}%"


class BaseRepository(Generic[ModelT]):
    model: Type[ModelT]

    def __init__(self, db: Session):
        self.db = db

    def get(self, id_: int) -> ModelT | None:
        return self.db.get(self.model, id_)

    def list(self, *, offset: int = 0, limit: int = 20) -> list[ModelT]:
        stmt = select(self.model).offset(offset).limit(limit)
        return list(self.db.execute(stmt).scalars().all())

    def count(self) -> int:
        return self.db.execute(select(func.count()).select_from(self.model)).scalar_one()

    def add(self, obj: ModelT) -> ModelT:
        self.db.add(obj)
        self.db.flush()
        return obj

    def delete(self, obj: ModelT) -> None:
        self.db.delete(obj)
        self.db.flush()
