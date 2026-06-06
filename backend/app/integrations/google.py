"""Google OAuth 2.0 — server-side authorization-code flow."""
from urllib.parse import urlencode

import httpx

from app.core.config import settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def is_configured() -> bool:
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def build_auth_url(state: str) -> str:
    """The Google consent-screen URL to redirect the user to."""
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(code: str) -> str:
    """Exchange an authorization code for an access token."""
    response = httpx.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def fetch_userinfo(access_token: str) -> dict:
    """Fetch the Google profile and return it with a normalised `email_verified`
    key. Callers MUST check `email_verified` before trusting the email address.

    Google's v3 userinfo endpoint uses "email_verified" (bool). We normalise
    both spellings ("email_verified" / "verified_email") into the single key
    "email_verified" so the rest of the app has one canonical field to check.
    """
    response = httpx.get(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    # Normalise: prefer "email_verified", fall back to "verified_email".
    verified = data.get("email_verified", data.get("verified_email", False))
    data["email_verified"] = bool(verified)
    return data
