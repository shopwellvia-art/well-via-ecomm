from pydantic import BaseModel, Field


class TruncateRequest(BaseModel):
    # Must equal database_admin_service.CONFIRM_PHRASE exactly. Validated in the
    # endpoint (not here) so the mismatch returns a friendly 422 with the phrase.
    confirm: str = Field(..., description="Confirmation phrase echoed back by the caller.")


class TruncateResponse(BaseModel):
    tables_truncated: int
    reseeded_admin: str
    detail: str
