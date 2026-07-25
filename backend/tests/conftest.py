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
