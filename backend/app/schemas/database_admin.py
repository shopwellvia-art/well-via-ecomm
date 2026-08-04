from pydantic import Field
from app.schemas.base import AppSchema

# Default scope keeps the original single-button behaviour working: a request
# with no scope means the full "DELETE EVERYTHING" wipe.
EVERYTHING_SCOPE = "everything"


class TruncateRequest(AppSchema):
    # Which domain group to wipe; "everything" (default) is the full reset.
    scope: str = Field(default=EVERYTHING_SCOPE, description="Domain group key or 'everything'.")
    # Must equal the scope's confirm phrase exactly (e.g. "DELETE ORDERS" /
    # "DELETE EVERYTHING"). Validated in the endpoint so the mismatch returns a
    # friendly 422 with the expected phrase.
    confirm: str = Field(..., description="Confirmation phrase echoed back by the caller.")


class TruncateResponse(AppSchema):
    scope: str = EVERYTHING_SCOPE
    tables_truncated: int
    # Full blast radius actually emptied (primary + FK-dependent tables).
    cleared_tables: list[str] = []
    # "table.column" references SET NULL in other domains to avoid dangling FKs.
    dissociated: list[str] = []
    # True only for the full wipe — the caller's own row is gone, session dead.
    ends_session: bool = False
    # Present only when the bootstrap admin was re-seeded (full wipe).
    reseeded_admin: str | None = None
    # One-time random password for the re-seeded admin, returned only when no
    # BOOTSTRAP_ADMIN_PASSWORD was configured. Shown once — not stored anywhere.
    generated_password: str | None = None
    detail: str


class SeedRequest(AppSchema):
    scope: str = Field(..., description="Domain group key to load sample data for.")


class SeedResponse(AppSchema):
    scope: str
    # {table: rows_created}
    created: dict[str, int] = {}
    detail: str


class DatabaseGroup(AppSchema):
    key: str
    label: str
    description: str
    primary_tables: list[str]
    cleared_tables: list[str]
    row_count: int
    seedable: bool
    confirm_phrase: str
    ends_session: bool


class DatabaseGroupsResponse(AppSchema):
    groups: list[DatabaseGroup]
