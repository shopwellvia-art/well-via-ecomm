import secrets

import httpx
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, rate_limit_by_ip, require_permission
from app.core.config import settings
from app.core.exceptions import NotFoundError, UnauthorizedError
from app.integrations import google
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.common import Token
from app.schemas.user import (
    ForgotPasswordRequest,
    LoginResponse,
    LoginTotpRequest,
    LogoutRequest,
    ProfileUpdateRequest,
    RefreshRequest,
    ResetPasswordRequest,
    SessionsRevokedResponse,
    TotpConfirmRequest,
    UserCreate,
    UserLogin,
    UserRead,
)
from app.services.auth_service import AuthService

router = APIRouter()


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(
            rate_limit_by_ip(
                scope="register.ip",
                limit=settings.RATE_LIMIT_REGISTER_IP_PER_HOUR,
                window_sec=3600,
            )
        )
    ],
)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    return AuthService(db).register(payload)


@router.post(
    "/login",
    response_model=LoginResponse,
    dependencies=[
        Depends(
            rate_limit_by_ip(
                scope="login.ip",
                limit=settings.RATE_LIMIT_LOGIN_IP_PER_15MIN,
                window_sec=15 * 60,
            )
        )
    ],
)
def login(payload: UserLogin, db: Session = Depends(get_db)):
    """Step 1 of authentication.

    - No 2FA on the account → returns access + refresh tokens (single-step).
    - Account has 2FA enabled and the system has TOTP enabled (mode != off) →
      returns `needs_totp=true` + a `pending_token` the caller submits to
      /auth/login/totp with the 6-digit code (or a backup code).
    """
    outcome = AuthService(db).login(payload.email, payload.password)
    if outcome.needs_totp:
        return LoginResponse(needs_totp=True, pending_token=outcome.pending_token)
    return LoginResponse(
        access_token=outcome.token.access_token,
        refresh_token=outcome.token.refresh_token,
    )


@router.post(
    "/login/totp",
    response_model=Token,
    dependencies=[
        Depends(
            rate_limit_by_ip(
                scope="login.totp.ip",
                limit=settings.RATE_LIMIT_LOGIN_IP_PER_15MIN,
                window_sec=15 * 60,
            )
        )
    ],
)
def login_totp(payload: LoginTotpRequest, db: Session = Depends(get_db)):
    """Step 2 of authentication when /auth/login returned needs_totp=true."""
    return AuthService(db).complete_login_totp(payload.pending_token, payload.code)


@router.post(
    "/refresh",
    response_model=Token,
    dependencies=[
        # Same IP envelope as login — a refresh is an authentication event.
        Depends(
            rate_limit_by_ip(
                scope="refresh.ip",
                limit=settings.RATE_LIMIT_LOGIN_IP_PER_15MIN,
                window_sec=15 * 60,
            )
        )
    ],
)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    """Rotate a refresh token. Returns a fresh access + refresh pair.

    Reuse detection: presenting a refresh token whose `jti` is no longer the
    current one for its family revokes the entire family. The legitimate
    holder is forced to sign in again — better that than letting an attacker
    keep riding the chain.
    """
    return AuthService(db).refresh(payload.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(payload: LogoutRequest, db: Session = Depends(get_db)):
    """End the session corresponding to this refresh token. Silent on a
    missing or invalid token — never leaks whether the session existed."""
    AuthService(db).logout(payload.refresh_token)


@router.post(
    "/sessions/revoke-all",
    response_model=SessionsRevokedResponse,
    status_code=status.HTTP_200_OK,
)
def revoke_all_my_sessions(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """End every session this user has, on every device. Their *current*
    access token survives until its 30-min TTL; refresh from anywhere will
    now fail."""
    n = AuthService(db).revoke_all_sessions(user.id)
    return SessionsRevokedResponse(revoked=n)


@router.post(
    "/sessions/users/{user_id}/revoke",
    response_model=SessionsRevokedResponse,
)
def admin_revoke_user_sessions(
    user_id: int,
    request: Request,
    actor: User = Depends(require_permission("users.update")),
    db: Session = Depends(get_db),
):
    """Admin force-logout. Wipes every refresh-token family for the target
    user — useful for incident response or when disabling an employee
    account."""
    target = UserRepository(db).get(user_id)
    if not target:
        raise NotFoundError("User not found")
    n = AuthService(db).revoke_all_sessions(user_id)

    # Lazy import to avoid pulling AuditService into the auth module's top
    # level — keeps the dependency graph shallow.
    from app.core.rate_limit import get_client_ip as _ip
    from app.services.audit_service import AuditService

    AuditService(db).record(
        actor=actor,
        actor_ip=_ip(request),
        action="session.revoke_all",
        target_type="user",
        target_id=user_id,
        target_label=target.email,
        summary=f"Force-logged out {target.email} ({n} session(s))",
        extra={"sessions_revoked": n},
    )
    db.commit()
    return SessionsRevokedResponse(revoked=n)


@router.get("/me", response_model=UserRead)
def me(user: User = Depends(get_current_user)):
    return user


@router.patch("/me", response_model=UserRead)
def update_my_profile(
    payload: ProfileUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Self-service profile edit. Currently `full_name` + `phone` — the phone
    is the opt-in switch for SMS notifications."""
    data = payload.model_dump(exclude_unset=True)
    if "full_name" in data:
        user.full_name = (data["full_name"] or None)
    if "phone" in data:
        # Normalize empty string → None so "" clears the opt-in.
        user.phone = (data["phone"] or "").strip() or None
    db.commit()
    db.refresh(user)
    return user


@router.get("/config")
def auth_config(db: Session = Depends(get_db)):
    """Public capability flags the frontend uses to render auth options."""
    from app.services.settings_service import SettingsService

    totp_mode = (SettingsService(db).get_raw("auth.totp_mode") or "off").strip().lower()
    return {
        "google_login": google.is_configured(),
        "totp_enabled_system_wide": totp_mode != "off",
    }


# ---- TOTP self-service ----


@router.post("/me/totp/start")
def totp_start(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Begin enrollment. Returns the otpauth URI for the QR code + the raw
    secret (so the user can manually type it if QR scan fails). Calling this
    while already enrolled returns 409."""
    from app.services.totp_service import TotpService

    data = TotpService(db).start_enrollment(user)
    db.commit()
    return data


@router.post("/me/totp/confirm")
def totp_confirm(
    payload: TotpConfirmRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Confirm a code from the authenticator app. Flips totp_enabled=True
    and returns 10 backup codes IN PLAINTEXT — the caller must surface them
    once and never store them on the client."""
    from app.core.rate_limit import get_client_ip
    from app.services.audit_service import AuditService
    from app.services.totp_service import TotpService

    code = payload.code
    backup = TotpService(db).confirm_enrollment(user, code)
    AuditService(db).record(
        actor=user,
        actor_ip=get_client_ip(request),
        action="totp.enable",
        target_type="user",
        target_id=user.id,
        target_label=user.email,
        summary=f"{user.email} enabled two-factor authentication",
    )
    db.commit()
    return {"backup_codes": backup}


@router.post("/me/totp/disable", status_code=status.HTTP_204_NO_CONTENT)
def totp_disable(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Turn off 2FA on the user's account. Wipes the secret + backup codes."""
    from app.services.audit_service import AuditService
    from app.services.totp_service import TotpService

    if user.totp_enabled:
        TotpService(db).disable(user)
        AuditService(db).record(
            actor=user,
            actor_ip=None,
            action="totp.disable",
            target_type="user",
            target_id=user.id,
            target_label=user.email,
            summary=f"{user.email} disabled two-factor authentication",
        )
    db.commit()


@router.post(
    "/forgot-password",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[
        Depends(
            rate_limit_by_ip(
                scope="forgot.ip",
                limit=settings.RATE_LIMIT_LOGIN_IP_PER_15MIN,
                window_sec=15 * 60,
            )
        )
    ],
)
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    # Always the same response — never reveals whether the email exists.
    AuthService(db).request_password_reset(payload.email)
    return {"detail": "If that email is registered, a reset code has been sent."}


@router.post(
    "/reset-password",
    dependencies=[
        Depends(
            rate_limit_by_ip(
                scope="reset.ip",
                limit=settings.RATE_LIMIT_LOGIN_IP_PER_15MIN,
                window_sec=15 * 60,
            )
        )
    ],
)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    AuthService(db).reset_password(payload.email, payload.otp, payload.new_password)
    return {"detail": "Your password has been reset. You can now sign in."}


# ---- Google OAuth (server-side redirect flow) ----


@router.get("/google/login")
def google_login():
    frontend_cb = f"{settings.FRONTEND_URL}/auth/callback"
    if not google.is_configured():
        return RedirectResponse(f"{frontend_cb}#error=google_disabled")
    state = secrets.token_urlsafe(24)
    response = RedirectResponse(google.build_auth_url(state))
    response.set_cookie(
        "g_oauth_state", state, max_age=600, httponly=True, samesite="lax"
    )
    return response


@router.get("/google/callback")
def google_callback(
    request: Request,
    db: Session = Depends(get_db),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    frontend_cb = f"{settings.FRONTEND_URL}/auth/callback"
    if error or not code or not state:
        return RedirectResponse(f"{frontend_cb}#error=google")

    cookie_state = request.cookies.get("g_oauth_state")
    if not cookie_state or cookie_state != state:
        return RedirectResponse(f"{frontend_cb}#error=state")

    try:
        access_token = google.exchange_code(code)
        info = google.fetch_userinfo(access_token)
    except (httpx.HTTPError, KeyError):
        return RedirectResponse(f"{frontend_cb}#error=google")

    email = info.get("email")
    if not email:
        return RedirectResponse(f"{frontend_cb}#error=google")

    # Reject sign-ins where Google has not verified ownership of the email address.
    # An unverified email would let a bad actor claim any address they like through
    # a specially-crafted Google account (account-takeover vector).
    if not info.get("email_verified"):
        return RedirectResponse(f"{frontend_cb}#error=google_unverified_email")

    # SECURITY TODO: access/refresh tokens are currently passed in the URL fragment
    # (#access_token=…). This should be replaced with a short-lived one-time code
    # redeemed by the frontend via a back-channel POST to avoid token leakage in
    # browser history, referrer headers, and server logs. Deferred because it
    # requires a coordinated frontend change.
    try:
        tokens = AuthService(db).login_with_google(
            email, info.get("name"), email_verified=info.get("email_verified", False)
        )
    except UnauthorizedError:
        return RedirectResponse(f"{frontend_cb}#error=disabled")

    response = RedirectResponse(
        f"{frontend_cb}#access_token={tokens.access_token}"
        f"&refresh_token={tokens.refresh_token}"
    )
    response.delete_cookie("g_oauth_state")
    return response
