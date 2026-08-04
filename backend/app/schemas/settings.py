from pydantic import ConfigDict, EmailStr
from app.schemas.base import AppSchema


class SettingItem(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    key: str
    # Masked when is_secret=True. Use the writer's intent: empty string means
    # "leave it alone if it's a secret", otherwise "set to this value".
    value: str | None
    category: str
    description: str | None
    is_secret: bool


class SettingsListResponse(AppSchema):
    items: list[SettingItem]


class SettingsUpdateRequest(AppSchema):
    """Map of {key: new_value}. Unknown keys are ignored server-side. Passing
    the literal "***" value for a secret means "no change"."""

    updates: dict[str, str | None]


class TestEmailRequest(AppSchema):
    to: EmailStr


class TestSmsRequest(AppSchema):
    # Loose validation — Twilio accepts E.164 or pre-formatted numbers; we
    # don't try to second-guess.
    to: str
    body: str = "Test SMS from your Wellvia admin panel."
