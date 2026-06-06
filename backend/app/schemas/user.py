from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RoleBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class UserBase(BaseModel):
    email: EmailStr
    full_name: str | None = None


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)
    # Optional. When present we look up the referrer at register time, create a
    # pending Referral row, and mint the friend-welcome coupon. Failures are
    # logged but never block account creation.
    referral_code: str | None = Field(default=None, max_length=32)


class UserUpdate(BaseModel):
    full_name: str | None = None
    # E.164-ish. Free-form so we accept any format the user enters; the
    # SMS backend handles whatever Twilio accepts.
    phone: str | None = Field(default=None, max_length=32)
    password: str | None = Field(default=None, min_length=8, max_length=128)


class ProfileUpdateRequest(BaseModel):
    """Self-service profile editor. Password changes live on /auth/me/password
    (future) — this endpoint is for plain profile fields only."""

    full_name: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=32)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    """The login endpoint returns either a token pair OR (when the account has
    TOTP enabled) a pending_token + needs_totp flag. The frontend checks
    `needs_totp` first to decide whether to render the 2FA challenge."""

    needs_totp: bool = False
    pending_token: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"


class LoginTotpRequest(BaseModel):
    pending_token: str
    code: str


class TotpConfirmRequest(BaseModel):
    """Body of POST /auth/me/totp/confirm — the code from the authenticator app."""

    code: str = Field(min_length=6, max_length=10)


class RefreshRequest(BaseModel):
    """Body of POST /auth/refresh. The refresh token is sent in the body
    rather than a header so it never ends up in an access log or referrer
    by accident."""

    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class SessionsRevokedResponse(BaseModel):
    revoked: int


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    otp: str = Field(min_length=6, max_length=6)
    new_password: str = Field(min_length=8, max_length=128)


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    is_admin: bool
    # Optional. When present the customer opts into SMS notifications.
    phone: str | None = None
    roles: list[RoleBrief] = []
    # Flat list of permission names. Populated from User.permissions_list so the
    # frontend can mirror server-side checks without an extra round-trip.
    permissions: list[str] = []
    # Loyalty: surfaced on /auth/me so AccountMenu can show a balance chip.
    points_balance: int = 0
    lifetime_points: int = 0
    # 2FA state — used by the Account/Security page to show whether TOTP is on.
    totp_enabled: bool = False
    created_at: datetime
