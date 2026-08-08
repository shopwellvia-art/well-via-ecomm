"""Shared base for every API schema.

Why this exists — the timezone bug it fixes
-------------------------------------------
DB datetimes are **naive UTC** by deliberate convention: containers run UTC and
`services/analytics/timebox.py` documents the invariant that day-bucketing
depends on. MySQL `DATETIME` carries no offset, so SQLAlchemy hands back naive
values even where a column declares `DateTime(timezone=True)` — that flag is a
no-op on MySQL.

Serialising a naive datetime yields ``"2026-08-04T17:04:37"`` with no zone
marker. Every JS ``new Date(...)`` then reads that as *local* time, so an IST
client renders a 22:34:37 IST event as 17:04:37 — exactly 5h30m early. The
frontend has 17 separate copy-pasted ``formatDate``/``formatDateTime`` helpers,
all making that same assumption, so fixing the wire format here fixes all of
them at once instead of 17 times over.

This stamps UTC onto naive datetimes at the response boundary, so the wire
format is ``"2026-08-04T17:04:37Z"`` and clients convert correctly.

What this deliberately does NOT do
----------------------------------
It does not change storage. Nothing here touches what is written to MySQL, and
the naive-UTC invariant the analytics rollups rely on is untouched — the 19
``.replace(tzinfo=None)`` call sites that maintain it keep working unchanged.
Making ORM reads tz-aware instead would fight that invariant and re-bucket
history, which `timebox.py` warns is unrecoverable.

Inbound request bodies get the same treatment, which is correct: a client that
sends a bare ``"2026-08-04T17:04:37"`` means UTC here, and stamping it makes
that explicit rather than leaving it ambiguous.
"""
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel


class AppSchema(BaseModel):
    """BaseModel that treats naive datetimes as UTC when serialising.

    Subclasses keep their own ``model_config`` (a subclass's config wins), so
    inheriting this changes nothing except datetime zone handling.
    """

    def model_post_init(self, __context: Any) -> None:
        # Stamp in a second pass: mutating __dict__ while iterating it is only
        # safe because we never add or remove keys, but collecting first keeps
        # that guarantee explicit rather than incidental.
        naive = [
            name
            for name, value in self.__dict__.items()
            if isinstance(value, datetime) and value.tzinfo is None
        ]
        for name in naive:
            self.__dict__[name] = self.__dict__[name].replace(tzinfo=timezone.utc)
