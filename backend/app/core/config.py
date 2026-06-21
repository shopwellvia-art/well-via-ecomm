from functools import lru_cache
from typing import List
from urllib.parse import quote_plus

from pydantic import Field, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    PROJECT_NAME: str = "Ecommerce API"
    VERSION: str = "0.1.0"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: str = Field(default="development")
    DEBUG: bool = False

    # SECRET_KEY also seeds the PASETO v4.local symmetric key (see core/security.py).
    SECRET_KEY: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    MYSQL_HOST: str
    MYSQL_PORT: int = 3306
    MYSQL_USER: str
    MYSQL_PASSWORD: str
    MYSQL_DB: str
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20

    REDIS_URL: str = "redis://redis:6379/0"

    CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://localhost:3000"]

    STRIPE_API_KEY: str = ""
    EMAIL_FROM: str = "noreply@example.com"

    # Image storage. STORAGE_BACKEND: "local" (dev) or "s3" (AWS S3 / DO Spaces).
    STORAGE_BACKEND: str = "local"
    UPLOAD_DIR: str = "uploads"
    # Base URL prefixed onto locally-stored upload URLs. Blank (the default)
    # yields RELATIVE /media/... URLs, served same-origin through the Vite dev
    # proxy and the nginx /media location — so images work in dev, in prod, and
    # from any host. Set an absolute origin only when /media is served from a
    # different host than the app (then the browser fetches it cross-origin).
    MEDIA_BASE_URL: str = ""
    MAX_IMAGE_SIZE_MB: int = 15
    MAX_PRODUCT_IMAGES: int = 8

    # S3 / DigitalOcean Spaces (both S3-compatible — Spaces just needs an endpoint).
    S3_ENDPOINT_URL: str = ""
    S3_REGION: str = ""
    S3_BUCKET: str = ""
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""
    S3_PUBLIC_BASE_URL: str = ""
    # Object ACL on upload. Empty = send no ACL (required for buckets with ACLs
    # disabled / "Bucket owner enforced"); set "public-read" only for legacy
    # ACL-enabled buckets.
    S3_ACL: str = ""
    # Top-level folder every uploaded object lives under in the bucket, so a
    # shared bucket can namespace this project's media (e.g.
    # wellvia/products/2026/06/<uuid>.jpg). Overridable per-deploy and via the
    # admin Settings → Storage tab (storage.s3_root_prefix).
    S3_ROOT_PREFIX: str = "wellvia"

    # Email — "console" (dev: logs the message) or "smtp" (real delivery).
    EMAIL_BACKEND: str = "console"
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = True
    OTP_TTL_MINUTES: int = 10

    # Google OAuth (server-side redirect flow). Blank client id disables it.
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/google/callback"
    FRONTEND_URL: str = "http://localhost:5173"

    # Rate limits — defaults match the production guidance from the security
    # review (5 login attempts / 15 min per (email, IP), 30 per IP). Set
    # RATE_LIMIT_ENABLED=false to short-circuit every check (tests, local dev).
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_LOGIN_IP_PER_15MIN: int = 30
    RATE_LIMIT_LOGIN_EMAIL_PER_15MIN: int = 5
    RATE_LIMIT_REGISTER_IP_PER_HOUR: int = 10
    RATE_LIMIT_FORGOT_PASSWORD_EMAIL_PER_HOUR: int = 3
    # After this many consecutive failed logins for an email, the account is
    # soft-locked for ACCOUNT_LOCKOUT_MINUTES. A successful login clears both.
    ACCOUNT_LOCKOUT_THRESHOLD: int = 10
    ACCOUNT_LOCKOUT_MINUTES: int = 15
    # When set, requests carrying these IPs in X-Forwarded-For are trusted.
    # The nginx container is the only upstream that should be talking to the
    # backend; if you're behind a different proxy add its IP here.
    TRUSTED_PROXIES: List[str] = ["127.0.0.1", "::1"]

    # Payments. The active gateway ("mock"/"phonepe") and the PhonePe
    # credentials now live in the database (system_settings, editable in
    # Admin → Settings → Payments) and are read per-request by
    # get_payment_provider(db). Only the deployment-specific URLs stay here:
    #   PAYMENT_RETURN_URL  — where PhonePe sends the user's browser back to
    #                         after the payment screen; the frontend route
    #                         there polls /payments/{txn}/status.
    #   PAYMENT_WEBHOOK_URL — the S2S callback URL handed to PhonePe.
    PAYMENT_RETURN_URL: str = "http://localhost:5173/payments/return"
    PAYMENT_WEBHOOK_URL: str = "http://localhost:8000/api/v1/payments/webhook/phonepe"

    # Observability / APM. Per-request + slow-query timing is captured in-process
    # and persisted off the hot path by a background flush thread (see
    # app/core/observability/). Set OBS_ENABLED=false to disable instrumentation
    # entirely (no middleware, no listeners, no flush thread).
    OBS_ENABLED: bool = True
    # Only queries at/over this many ms are persisted as slow queries (all
    # queries still contribute to a request's db_ms regardless).
    OBS_SLOW_QUERY_MS: int = 200
    # Telemetry older than this is pruned by the flush thread's hourly sweep.
    # 7 days — matches the dashboard's longest period (7d), so the UI never
    # asks for data that has already been pruned.
    OBS_RETENTION_DAYS: int = 7
    # How long the flush thread waits to accumulate a batch before writing.
    OBS_FLUSH_INTERVAL_SEC: float = 2.0
    # Max buffered records; once full, new records are dropped (counted) rather
    # than blocking the request.
    OBS_BUFFER_MAX: int = 5000

    @model_validator(mode="after")
    def _validate_secret_key_in_production(self) -> "Settings":
        """Refuse to boot in production with an insecure SECRET_KEY."""
        _PLACEHOLDER = "replace-this-with-openssl-rand-hex-32-output"
        if self.ENVIRONMENT == "production":
            if self.SECRET_KEY == _PLACEHOLDER or len(self.SECRET_KEY) < 32:
                raise ValueError(
                    "SECRET_KEY must be at least 32 characters and must not be "
                    "the placeholder value when ENVIRONMENT=production. "
                    "Generate one with: openssl rand -hex 32"
                )
        return self

    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        user = quote_plus(self.MYSQL_USER)
        password = quote_plus(self.MYSQL_PASSWORD)
        return (
            f"mysql+pymysql://{user}:{password}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DB}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
