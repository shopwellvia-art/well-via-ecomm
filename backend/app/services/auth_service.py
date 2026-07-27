import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

import redis
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.redis import get_redis
from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    TooManyRequestsError,
    UnauthorizedError,
)
from app.core.rate_limit import RateLimiter
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.email import send_email
from app.models.customer import AccountStatus, Customer
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.common import Token
from app.schemas.user import AdminUserUpdate, UserCreate
from app.services.loyalty_service import LoyaltyService
from app.services.referral_service import ReferralService
from app.services.session_service import SessionService

logger = logging.getLogger(__name__)


@dataclass
class LoginOutcome:
    """Discriminated result from `AuthService.login`. Either a real token
    pair (no 2FA needed) or a pending token (caller must finish via
    /auth/login/totp). Exactly one of `token`/`pending_token` is set."""

    token: Token | None = None
    needs_totp: bool = False
    pending_token: str | None = None


_OTP_MAX_ATTEMPTS = 5


def _otp_key(email: str) -> str:
    return f"otp:reset:{email.lower()}"


def _hash_otp(otp: str) -> str:
    """SHA-256 of the OTP so we never store the plaintext in Redis."""
    return hashlib.sha256(otp.encode("utf-8")).hexdigest()


def _split_name(full_name: str | None) -> tuple[str | None, str | None]:
    """Split a free-form display name into (first, last) on the first space —
    mirrors the SQL split used to backfill the customers table at migration."""
    cleaned = (full_name or "").strip()
    if not cleaned:
        return None, None
    parts = cleaned.split(None, 1)
    first = parts[0] or None
    last = parts[1].strip() if len(parts) > 1 else None
    return first, (last or None)


class AuthService:
    def __init__(self, db: Session, redis_client: redis.Redis | None = None):
        self.db = db
        self.users = UserRepository(db)
        self.redis = redis_client or get_redis()
        # Share the Redis client with the rate limiter so we don't open two
        # connections per request.
        self.rl = RateLimiter(self.redis)

    def register(self, data: UserCreate) -> User:
        if self.users.get_by_email(data.email):
            raise ConflictError("Email already registered")
        user = User(
            email=data.email,
            hashed_password=hash_password(data.password),
        )
        self.users.add(user)
        self.db.flush()  # so user.id is set when we award the bonus
        # Every user is a customer — create the profile row eagerly, splitting
        # the provided name into first/last. Flushed before the loyalty/referral
        # hooks so their points/lifetime/referral writes land on this row.
        first, last = _split_name(data.full_name)
        customer = Customer(
            user_id=user.id,
            first_name=first,
            last_name=last,
            account_status=AccountStatus.ACTIVE,
        )
        self.db.add(customer)
        self.db.flush()
        # Sign-up bonus runs inside the same transaction so a failure here
        # rolls the user creation back too — easier to debug than a half-baked
        # account with no points row.
        try:
            LoyaltyService(self.db, self.redis).award_signup_bonus(user)
        except Exception as exc:
            # Don't fail registration on a loyalty hiccup; this is a courtesy.
            logger.warning("signup bonus failed for %s: %s", data.email, exc)
        # Referral: create the pending row + mint the friend-welcome coupon.
        # Failures are logged and swallowed — never let referral block signup.
        if data.referral_code:
            try:
                ReferralService(self.db).register_referred_user(user, data.referral_code)
            except Exception as exc:
                logger.warning(
                    "referral processing failed for %s: %s", data.email, exc
                )
        self.db.commit()
        self.db.refresh(user)
        return user

    def login(self, identifier: str, password: str) -> "LoginOutcome":
        # `identifier` is an email OR a phone — users can sign in with either.
        # Three gates layered cheapest-first so we never run bcrypt on an
        # already-blocked request:
        #   1. Account lockout (a previous failure streak)
        #   2. Per-identifier sliding window (slows enumeration attacks)
        #   3. The actual password verify
        normalised = (identifier or "").strip().lower()
        self.rl.check_lockout(normalised)
        self.rl.enforce(
            scope="login.email",
            identifier=normalised,
            limit=settings.RATE_LIMIT_LOGIN_EMAIL_PER_15MIN,
            window_sec=15 * 60,
        )

        user = self.users.get_by_email_or_phone(identifier)
        if not user or not verify_password(password, user.hashed_password):
            # Bump the failure counter (15-minute reset window) and lock the
            # account if the threshold is crossed. We deliberately count
            # against the *attempted* email — locking a non-existent email is
            # fine and avoids leaking signal to an attacker.
            count = self.rl.increment_failure(normalised, window_sec=15 * 60)
            if (
                settings.ACCOUNT_LOCKOUT_THRESHOLD > 0
                and count >= settings.ACCOUNT_LOCKOUT_THRESHOLD
            ):
                self.rl.set_lockout(
                    normalised, minutes=settings.ACCOUNT_LOCKOUT_MINUTES
                )
                logger.warning(
                    "auth: locked %s for %d min after %d failures",
                    normalised,
                    settings.ACCOUNT_LOCKOUT_MINUTES,
                    count,
                )
            raise UnauthorizedError("Invalid credentials")
        if not user.is_active:
            raise UnauthorizedError("Account disabled")
        # Lifecycle gate. Deactivate / soft-delete also flip is_active=False
        # (above), so this is defense-in-depth on the eagerly-loaded customer
        # row — no extra query — and gives a precise reason for the block.
        if user.customer is not None and user.customer.account_status in (
            AccountStatus.DEACTIVATED,
            AccountStatus.DELETED,
        ):
            raise UnauthorizedError("Account disabled")

        # Password is good. If the user has TOTP enabled AND the system has
        # 2FA at least in "optional" mode, bridge to the second-factor step.
        # We do NOT clear failure counters yet — the second factor still has
        # to clear before this counts as a "successful login".
        from app.services.totp_service import TotpService

        totp_svc = TotpService(self.db)
        if user.totp_enabled and totp_svc.is_enabled_system_wide():
            pending = secrets.token_urlsafe(24)
            # 5-minute TTL: enough time to grab the phone, not so much that a
            # stolen browser tab can replay it later.
            self.redis.setex(f"auth:pending_totp:{pending}", 300, str(user.id))
            return LoginOutcome(needs_totp=True, pending_token=pending)

        self.rl.clear_failures(normalised)
        token = self._issue_tokens(user)
        return LoginOutcome(token=token)

    def complete_login_totp(self, pending_token: str, code: str) -> Token:
        """Second step of 2FA login. Trades the pending token + a valid TOTP
        (or backup code) for a real token pair."""
        from app.services.totp_service import TotpService

        key = f"auth:pending_totp:{pending_token}"
        user_id_raw = self.redis.get(key)
        if not user_id_raw:
            raise UnauthorizedError("Verification expired — please sign in again.")
        try:
            user_id = int(user_id_raw)
        except (TypeError, ValueError) as exc:
            raise UnauthorizedError("Invalid verification token") from exc

        user = self.users.get(user_id)
        if not user or not user.is_active:
            self.redis.delete(key)
            raise UnauthorizedError("Account no longer active")

        # Rate-limit verification attempts per pending token to prevent a
        # 6-digit brute force inside the 5-minute window.
        self.rl.enforce(
            scope="login.totp",
            identifier=pending_token,
            limit=8,
            window_sec=300,
        )

        if not TotpService(self.db).verify(user, code):
            raise UnauthorizedError("That code didn't match.")

        # Consume the pending token (single-use) and finalize.
        self.redis.delete(key)
        self.rl.clear_failures((user.email or "").lower())
        self.db.commit()  # persist backup-code consumption from TotpService.verify
        return self._issue_tokens(user)

    def _issue_tokens(self, user: User) -> Token:
        """Start a new refresh-token family for the user. Used on a fresh
        login (password or Google). Subsequent /auth/refresh calls rotate
        within this family."""
        sessions = SessionService(self.redis)
        family_id, jti = sessions.start_family(user.id)
        return Token(
            access_token=create_access_token(user.id, {"admin": user.is_admin}),
            refresh_token=create_refresh_token(
                user.id, family_id=family_id, jti=jti
            ),
        )

    # ---- Refresh / logout ----

    def refresh(self, refresh_token: str) -> Token:
        """Trade a valid refresh token for a fresh access + rotated refresh.
        Raises UnauthorizedError when the token is bad, expired, or has been
        reused — see SessionService.rotate for the failure semantics."""
        payload = decode_token(refresh_token)
        if payload.get("type") != "refresh":
            raise UnauthorizedError("Wrong token type")
        family_id = payload.get("fid")
        old_jti = payload.get("jti")
        sub = payload.get("sub")
        if not (family_id and old_jti and sub):
            # Pre-rotation tokens didn't carry these. They MUST log in again
            # rather than ride a stateless refresh — that's the whole point
            # of this feature.
            raise UnauthorizedError("Please sign in again")

        user_id = int(sub)
        user = self.users.get(user_id)
        if not user or not user.is_active:
            raise UnauthorizedError("Account no longer active")

        sessions = SessionService(self.redis)
        new_jti = sessions.rotate(family_id, old_jti, user_id)

        return Token(
            access_token=create_access_token(user.id, {"admin": user.is_admin}),
            refresh_token=create_refresh_token(
                user.id, family_id=family_id, jti=new_jti
            ),
        )

    def logout(self, refresh_token: str | None) -> None:
        """End the session corresponding to this refresh token. Silent on a
        bad/expired token — logout should never expose extra signal."""
        if not refresh_token:
            return
        try:
            payload = decode_token(refresh_token)
        except UnauthorizedError:
            return
        if payload.get("type") != "refresh":
            return
        family_id = payload.get("fid")
        sub = payload.get("sub")
        if not (family_id and sub):
            return
        SessionService(self.redis).revoke_family(family_id, int(sub))

    def revoke_all_sessions(self, user_id: int) -> int:
        """Admin/self action — kicks every device this user is signed in on."""
        return SessionService(self.redis).revoke_all_for_user(user_id)

    def active_session_count(self, user_id: int) -> int:
        return SessionService(self.redis).session_count(user_id)

    # ---- Password reset (OTP over email) ----

    def request_password_reset(self, email: str) -> None:
        """Generate and email a one-time code. Silent if the email is unknown
        — the caller must not reveal whether an account exists."""
        # Per-email rate limit. We *silently* swallow the 429 here because the
        # endpoint is response-uniform by design — surfacing a 429 would leak
        # whether the email exists ("oh, this address gets rate limited =>
        # someone already requested for it"). The attacker just sees the same
        # "if registered, a code has been sent" reply.
        normalised = (email or "").strip().lower()
        try:
            self.rl.enforce(
                scope="forgot.email",
                identifier=normalised,
                limit=settings.RATE_LIMIT_FORGOT_PASSWORD_EMAIL_PER_HOUR,
                window_sec=3600,
            )
        except TooManyRequestsError:
            logger.info("forgot-password rate limited for %s", normalised)
            return

        user = self.users.get_by_email(email)
        if not user:
            return
        otp = f"{secrets.randbelow(1_000_000):06d}"
        # Store hash:attempts so we never hold the cleartext OTP in Redis and
        # can cap wrong guesses without a separate key (mirrors cod_otp_service).
        self.redis.setex(
            _otp_key(email),
            settings.OTP_TTL_MINUTES * 60,
            f"{_hash_otp(otp)}:0",
        )
        # Render the password_reset template via the email_templates engine.
        # Falls back to a plain-text body if the template render fails so the
        # OTP is always delivered even when the template engine has a bug.
        _plain_body = (
            f"Your one-time password reset code is: {otp}\n\n"
            f"It expires in {settings.OTP_TTL_MINUTES} minutes. "
            "If you didn't request this, you can ignore this email."
        )
        try:
            from app.services.email_templates.catalog import password_reset_context
            from app.services.email_templates.renderer import render_email

            first_name = "there"
            if user.full_name:
                first_name = user.full_name.split(" ")[0]
            _ctx = password_reset_context(first_name, otp, settings.OTP_TTL_MINUTES)
            _subject, _html, _text = render_email(self.db, "password_reset", _ctx)
            send_email(
                to=email,
                subject=_subject or "Your password reset code",
                body=_text or _plain_body,
                html=_html or None,
                db=self.db,
            )
        except Exception as _exc:  # noqa: BLE001
            logger.warning("password_reset template render failed (%s); using plain text", _exc)
            send_email(
                to=email,
                subject="Your password reset code",
                body=_plain_body,
                db=self.db,
            )

    def reset_password(self, email: str, otp: str, new_password: str) -> None:
        raw = self.redis.get(_otp_key(email))
        if not raw:
            raise UnauthorizedError("Invalid or expired code")

        # Record is stored as "<hash>:<attempts>" (see request_password_reset).
        try:
            stored_hash, attempts_str = raw.rsplit(":", 1)
            attempts = int(attempts_str)
        except (ValueError, AttributeError):
            self.redis.delete(_otp_key(email))
            raise UnauthorizedError("Invalid or expired code")

        if attempts >= _OTP_MAX_ATTEMPTS:
            # Already burned — belt and braces in case of a race.
            self.redis.delete(_otp_key(email))
            raise UnauthorizedError("Invalid or expired code")

        # Constant-time comparison to prevent timing oracle attacks.
        if not hmac.compare_digest(_hash_otp((otp or "").strip()), stored_hash):
            new_attempts = attempts + 1
            if new_attempts >= _OTP_MAX_ATTEMPTS:
                # Too many wrong guesses — invalidate the OTP entirely.
                self.redis.delete(_otp_key(email))
                raise UnauthorizedError("Invalid or expired code")
            # Preserve the remaining TTL so the window doesn't widen on failures.
            ttl = self.redis.ttl(_otp_key(email))
            self.redis.setex(
                _otp_key(email),
                max(ttl, 1),
                f"{stored_hash}:{new_attempts}",
            )
            raise UnauthorizedError("Invalid or expired code")

        # Code is correct — reset the password and consume the OTP.
        user = self.users.get_by_email(email)
        if not user:
            self.redis.delete(_otp_key(email))
            raise UnauthorizedError("Invalid or expired code")
        user.hashed_password = hash_password(new_password)
        self.db.commit()
        self.redis.delete(_otp_key(email))

    # ---- Google sign-in ----

    def login_with_google(
        self, email: str, full_name: str | None, *, email_verified: bool = False
    ) -> Token:
        """Find or create a user for a verified Google profile, then issue tokens.

        email_verified MUST be True — if Google reports the address as unverified
        we refuse to create/match a local account to prevent account-takeover via
        an unverified Google identity.
        """
        if not email_verified:
            raise UnauthorizedError("Google account email is not verified")
        user = self.users.get_by_email(email)
        if not user:
            user = User(
                email=email,
                # No usable password — a Google user signs in via Google
                # (or sets a password later through the reset flow).
                hashed_password=hash_password(secrets.token_urlsafe(32)),
                is_active=True,
            )
            self.users.add(user)
            self.db.flush()
            # Eager customer row, name split from the Google profile.
            first, last = _split_name(full_name)
            customer = Customer(
                user_id=user.id,
                first_name=first,
                last_name=last,
                account_status=AccountStatus.ACTIVE,
            )
            self.db.add(customer)
            self.db.flush()
            try:
                LoyaltyService(self.db, self.redis).award_signup_bonus(user)
            except Exception as exc:
                logger.warning("signup bonus failed for %s: %s", email, exc)
            self.db.commit()
            self.db.refresh(user)
        elif not user.is_active:
            raise UnauthorizedError("Account disabled")
        return self._issue_tokens(user)

    # ------------------------------------------------------------------
    # Admin user management (staff actions from /api/v1/users).
    #
    # Deliberately separate from the self-service flows above: these run on
    # a *target* user chosen by a staff actor, never on the caller. Both are
    # guarded by the same superadmin shield — a non-superadmin staff member
    # (RBAC-granted users.manage) can never touch an is_admin account, the
    # same way role assignment shields privileged accounts.
    # ------------------------------------------------------------------

    def _get_managed_target(self, actor: User, user_id: int) -> User:
        """Resolve + authorize the target of an admin user-management action.

        Raises NotFoundError for an unknown id and ForbiddenError when a
        non-superadmin targets a superadmin (is_admin=True) account.
        """
        target = self.users.get(user_id)
        if not target:
            raise NotFoundError("User not found")
        if target.is_admin and not actor.is_admin:
            raise ForbiddenError(
                "Only a superadmin can modify a superadmin account"
            )
        return target

    def admin_update_user(
        self, actor: User, user_id: int, data: AdminUserUpdate
    ) -> tuple[User, dict]:
        """PATCH /users/{id}: display-name edit and activate/deactivate.

        Deactivation also revokes every session via SessionService so a
        disabled user is logged out everywhere immediately; reactivation
        restores the login gates (is_active + customer.account_status).

        Flushes but does NOT commit — the endpoint owns the commit so its
        audit row lands in the same transaction (same shape as the roles
        endpoints). Returns (user, changes-dict-for-audit).
        """
        target = self._get_managed_target(actor, user_id)
        fields = data.model_dump(exclude_unset=True)
        changes: dict = {}

        if "full_name" in fields:
            before_name = target.full_name
            first, last = _split_name(fields["full_name"])
            customer = self.users.get_or_create_customer(target.id)
            customer.first_name = first
            customer.last_name = last
            if target.full_name != before_name:
                changes["full_name"] = {
                    "before": before_name,
                    "after": target.full_name,
                }

        new_active = fields.get("is_active")
        if new_active is not None and new_active != target.is_active:
            customer = self.users.get_or_create_customer(target.id)
            if new_active:
                # Mirror of deactivation below — restore every gate the login
                # path checks so the account actually works again.
                target.is_active = True
                customer.account_status = AccountStatus.ACTIVE
                customer.deactivated_at = None
                customer.deleted_at = None
                changes["is_active"] = {"before": False, "after": True}
            else:
                # Mirror of the self-service deactivate flow, plus a forced
                # logout everywhere: get_current_user already rejects
                # is_active=False, revoking sessions kills refresh too.
                target.is_active = False
                customer.account_status = AccountStatus.DEACTIVATED
                customer.deactivated_at = datetime.now(timezone.utc)
                revoked = self.revoke_all_sessions(target.id)
                changes["is_active"] = {"before": True, "after": False}
                changes["sessions_revoked"] = revoked

        self.db.flush()
        return target, changes

    def admin_trigger_password_reset(self, actor: User, user_id: int) -> User:
        """POST /users/{id}/password-reset: email the user a one-time reset
        code, reusing the self-service forgot-password machinery unchanged
        (same OTP store, hashing, TTL, template, and per-email rate limit).
        The code is emailed to the target only — never returned to the
        caller, so a staff account can't capture it.
        """
        target = self._get_managed_target(actor, user_id)
        self.request_password_reset(target.email)
        return target
