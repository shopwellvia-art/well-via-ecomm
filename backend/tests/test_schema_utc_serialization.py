"""Naive DB datetimes must serialise as explicit UTC.

Regression test for the 2026-08-04 report of "wrong timing in the DB". The
stored instants were correct (naive UTC, per the invariant documented in
services/analytics/timebox.py) — but the API emitted them with no zone marker,
so `new Date("2026-08-04T17:04:37")` in an IST browser rendered a 22:34 event as
17:04, exactly 5h30m early.

The assertions themselves touch no database, but `tests/conftest.py` has
session-scoped autouse fixtures that connect to MySQL, so these still need the
usual local MySQL + Redis like the rest of the suite. Note the CI backend job
was removed on 2026-08-04 — nothing runs this automatically any more, so run it
by hand before pushing to `production`.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.schemas.base import AppSchema
from app.schemas.common import Page


class _Row(AppSchema):
    id: int
    created_at: datetime
    paid_at: datetime | None = None


class _Obj:
    """Stands in for a SQLAlchemy row (the from_attributes read path)."""

    def __init__(self, id: int, created_at: datetime, paid_at: datetime | None = None):
        self.id = id
        self.created_at = created_at
        self.paid_at = paid_at


# The two values from the original report.
NAIVE_A = datetime(2026, 8, 4, 17, 4, 37)
NAIVE_B = datetime(2026, 8, 4, 17, 6, 19)


def test_naive_datetime_serialises_with_utc_marker():
    out = _Row(id=1, created_at=NAIVE_A).model_dump_json()
    assert '"created_at":"2026-08-04T17:04:37Z"' in out


def test_already_aware_datetime_is_not_shifted_twice():
    aware = datetime(2026, 8, 4, 17, 4, 37, tzinfo=timezone.utc)
    out = _Row(id=1, created_at=aware).model_dump_json()
    assert '"created_at":"2026-08-04T17:04:37Z"' in out


def test_none_stays_null():
    out = _Row(id=1, created_at=NAIVE_A, paid_at=None).model_dump_json()
    assert '"paid_at":null' in out


def test_from_attributes_read_path_is_stamped():
    """FastAPI reads ORM rows this way; naive values must still get UTC."""
    row = _Row.model_validate(_Obj(1, NAIVE_A), from_attributes=True)
    assert row.created_at.tzinfo is not None
    assert "17:04:37Z" in row.model_dump_json()


def test_nested_rows_inside_a_page_are_stamped():
    """The admin orders list is Page[OrderRead] — nesting must not bypass this."""
    page = Page[_Row](
        items=[_Row(id=1, created_at=NAIVE_A), _Row(id=2, created_at=NAIVE_B)],
        total=2,
        page=1,
        page_size=20,
    )
    body = page.model_dump_json()
    assert '"2026-08-04T17:04:37Z"' in body
    assert '"2026-08-04T17:06:19Z"' in body


def test_utc_instant_renders_as_expected_ist_wall_clock():
    """The actual user-visible assertion: 17:04:37Z is 10:34:37 PM in IST."""
    row = _Row(id=1, created_at=NAIVE_A)
    ist = row.created_at.astimezone(ZoneInfo("Asia/Kolkata"))
    assert (ist.hour, ist.minute, ist.second) == (22, 34, 37)
    assert ist.date().isoformat() == "2026-08-04"


@pytest.mark.parametrize(
    "module_name",
    ["order", "payment", "customer_admin", "audit", "loyalty", "return_request"],
)
def test_response_schemas_inherit_appschema(module_name):
    """Guards against a new schema file going back to plain BaseModel."""
    import importlib

    mod = importlib.import_module(f"app.schemas.{module_name}")
    dated = [
        obj
        for obj in vars(mod).values()
        if isinstance(obj, type)
        and issubclass(obj, AppSchema | object)
        and hasattr(obj, "model_fields")
        and any(
            f.annotation in (datetime, datetime | None)
            for f in getattr(obj, "model_fields", {}).values()
        )
    ]
    assert dated, f"no datetime-bearing schemas found in {module_name}"
    for obj in dated:
        assert issubclass(obj, AppSchema), f"{obj.__name__} is not an AppSchema"
