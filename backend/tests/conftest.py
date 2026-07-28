import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

# The integration suite creates/mutates/drops rows through SessionLocal, so it
# must only ever run against a DISPOSABLE database. The shared REMOTE production
# MySQL (13.204.184.41) must never appear below — this guard is the last line of
# defence against an accidental `pytest` pointed at live data.
_SAFE_ENVIRONMENTS = {"test", "development", "ci"}
_SAFE_DB_HOSTS = {"localhost", "127.0.0.1", "mysql", "db"}


@pytest.fixture(scope="session", autouse=True)
def _guard_against_remote_db() -> None:
    """Hard-fail the whole session unless we're pointed at a throwaway local DB.

    Runs once, before any test body. If either the environment or the DB host
    looks like production, abort immediately (non-zero exit) rather than letting
    a single query touch the shared remote MySQL.
    """
    if settings.ENVIRONMENT not in _SAFE_ENVIRONMENTS:
        pytest.exit(
            f"Refusing to run tests: ENVIRONMENT={settings.ENVIRONMENT!r} is not "
            f"one of {sorted(_SAFE_ENVIRONMENTS)}. Point at a test/dev/ci env.",
            returncode=3,
        )
    if settings.MYSQL_HOST not in _SAFE_DB_HOSTS:
        pytest.exit(
            f"Refusing to run tests: MYSQL_HOST={settings.MYSQL_HOST!r} is not a "
            f"known-local host {sorted(_SAFE_DB_HOSTS)} — this looks like the "
            "shared remote production MySQL. Aborting before any DB access.",
            returncode=3,
        )


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Test admin account
# ---------------------------------------------------------------------------
# Six suites used to authenticate as a developer's personal account
# ("vinay@gmail.com"), which exists only on the shared remote production MySQL —
# the exact database the guard above refuses to connect to. Those tests could
# therefore never pass anywhere they were allowed to run, and they shipped a
# live admin password in plaintext in the repo.
#
# This fixture creates a disposable admin on whatever throwaway database the
# session is pointed at, so the suites authenticate against data they own.

TEST_ADMIN_EMAIL = "pytest-admin@analytics.local"
TEST_ADMIN_PASSWORD = "pytest-Admin-Passw0rd!"


@pytest.fixture(scope="session", autouse=True)
def _ensure_test_admin(_guard_against_remote_db) -> None:
    """Create (once per session) a disposable superadmin for HTTP-facing tests.

    Idempotent: reuses the row if a previous run left it behind, and resets the
    password so a stale hash from an older run cannot fail the login.

    Depends on the remote-DB guard so it can never run before that check — this
    fixture WRITES a user, and doing so against production would be far worse
    than merely reading from it.

    Deliberately NOT torn down. Later suites in the same session reuse it, and
    it lives only on a throwaway database that is destroyed with the container.
    """
    from app.core.security import hash_password
    from app.db.session import SessionLocal
    from app.models.user import User
    from app.services.email_templates.seed import seed_email_templates
    from app.services.rbac_seed import seed_rbac
    from app.services.settings_seed import seed_settings

    # Replay the boot seeders. `TestClient(app)` used as a plain constructor
    # (which every suite here does) never enters the lifespan context, so none
    # of them run — permissions, settings and email templates are all absent on
    # a fresh database. Tests then fail on missing seed data in ways that read
    # like feature bugs. All three are idempotent, so this is safe on a database
    # that already has them.
    with SessionLocal() as db:
        try:
            seed_rbac(db)
            seed_settings(db)
            seed_email_templates(db)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - seeding must not abort the run
            db.rollback()
            print(f"warning: boot seeders failed in test setup: {exc}")

    with SessionLocal() as db:
        user = db.query(User).filter(User.email == TEST_ADMIN_EMAIL).first()
        if user is None:
            user = User(
                email=TEST_ADMIN_EMAIL,
                hashed_password=hash_password(TEST_ADMIN_PASSWORD),
                is_active=True,
                is_admin=True,
            )
            db.add(user)
        else:
            user.hashed_password = hash_password(TEST_ADMIN_PASSWORD)
            user.is_active = True
            user.is_admin = True
        db.commit()


def get_test_admin_token(client: TestClient) -> str:
    """Bearer token for the disposable admin.

    Rate limiting is patched off for the login call: repeated runs of the same
    suite would otherwise exhaust the Redis bucket and fail on a limit rather
    than on the behaviour under test.
    """
    from app.core import config as _config

    original = _config.settings.RATE_LIMIT_ENABLED
    _config.settings.RATE_LIMIT_ENABLED = False
    try:
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": TEST_ADMIN_EMAIL, "password": TEST_ADMIN_PASSWORD},
        )
        assert resp.status_code == 200, f"Test-admin login failed: {resp.text}"
        return resp.json()["access_token"]
    finally:
        _config.settings.RATE_LIMIT_ENABLED = original
