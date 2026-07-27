from datetime import date, datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field

from app.models.customer import AccountStatus


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
    # Accepts an email OR a phone number. The `email` alias keeps existing
    # clients that post {"email": ..., "password": ...} working unchanged.
    identifier: str = Field(validation_alias=AliasChoices("identifier", "email"))
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


class AdminUserUpdate(BaseModel):
    """Body of PATCH /users/{id} — the staff-facing user editor.

    Partial update: only the fields actually provided change (PATCH
    semantics via exclude_unset). Setting `is_active=false` also revokes
    every session for the user so a disabled account is logged out
    everywhere immediately.
    """

    full_name: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None


class AdminPasswordResetResponse(BaseModel):
    """Ack for POST /users/{id}/password-reset. Deliberately carries no
    token/OTP — the reset code goes to the user's email only."""

    detail: str


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    is_admin: bool
    # Login + contact phone (also the SMS opt-in when present).
    phone: str | None = None
    # Profile fields — physically on the customer satellite, surfaced flat here
    # via the read proxies on User so the existing response shape is preserved.
    first_name: str | None = None
    last_name: str | None = None
    gender: str | None = None
    date_of_birth: date | None = None
    profile_image: str | None = None
    account_status: str = "active"
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


class CustomerProfileUpdate(BaseModel):
    """Body of PATCH /auth/me — the self-service profile editor.

    All fields optional; only the ones provided are changed. `email` and `phone`
    update the auth `users` row (each uniqueness-checked); the rest update the
    customer profile. The client pre-fills `email` from /auth/me and may edit it.
    """

    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=32)
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    gender: str | None = Field(default=None, max_length=16)
    date_of_birth: date | None = None
    profile_image: str | None = Field(default=None, max_length=512)


class CustomerRead(BaseModel):
    """Full customer profile + loyalty record (admin / dedicated profile view)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    first_name: str | None = None
    last_name: str | None = None
    gender: str | None = None
    date_of_birth: date | None = None
    profile_image: str | None = None
    points_balance: int = 0
    lifetime_points: int = 0
    referral_code: str | None = None
    vip_tier_id: int | None = None
    account_status: AccountStatus = AccountStatus.ACTIVE
    deactivated_at: datetime | None = None
    deleted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
