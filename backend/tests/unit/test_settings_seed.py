"""Unit tests for `seed_settings` — the canonical system_settings seeder.

Regression cover for the bug where a superadmin DB truncate emptied
`system_settings` (migrations don't re-run, so nothing restored the rows) and
the admin Settings page came back blank.

Fully isolated: builds just the `system_settings` table in an in-memory SQLite
DB, so this never touches the live MySQL instance and is safe to run anywhere.

    docker compose exec backend pytest tests/unit/test_settings_seed.py -v
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.system_setting import SystemSetting
from app.services.settings_seed import DEFAULT_SETTINGS, seed_settings


@pytest.fixture
def db() -> Session:
    """A throwaway in-memory DB with only the system_settings table."""
    engine = create_engine("sqlite://")
    SystemSetting.__table__.create(engine)
    with Session(engine) as session:
        yield session


def _count(db: Session) -> int:
    return len(db.execute(select(SystemSetting)).scalars().all())


def test_seeds_all_defaults_into_empty_table(db: Session):
    inserted = seed_settings(db)
    assert inserted == len(DEFAULT_SETTINGS)
    assert _count(db) == len(DEFAULT_SETTINGS)
    # Spot-check the SMTP rows that drive the reported-broken Email tab.
    keys = {s.key for s in db.execute(select(SystemSetting)).scalars()}
    assert {"smtp.host", "smtp.password", "email.backend"} <= keys


def test_is_idempotent(db: Session):
    seed_settings(db)
    # A second run inserts nothing and leaves the row count unchanged.
    assert seed_settings(db) == 0
    assert _count(db) == len(DEFAULT_SETTINGS)


def test_preserves_admin_configured_value(db: Session):
    seed_settings(db)
    row = db.execute(
        select(SystemSetting).where(SystemSetting.key == "smtp.host")
    ).scalar_one()
    row.value = "smtp.sendgrid.net"  # admin configured it
    db.commit()

    # Re-seeding (e.g. on the next app boot) must NOT clobber the stored value.
    seed_settings(db)
    row = db.execute(
        select(SystemSetting).where(SystemSetting.key == "smtp.host")
    ).scalar_one()
    assert row.value == "smtp.sendgrid.net"


def test_restores_rows_after_truncate(db: Session):
    """The actual truncate scenario: rows wiped, then re-seeded."""
    seed_settings(db)
    db.query(SystemSetting).delete()  # simulate the truncate
    db.commit()
    assert _count(db) == 0

    inserted = seed_settings(db)
    assert inserted == len(DEFAULT_SETTINGS)
    assert _count(db) == len(DEFAULT_SETTINGS)


def test_secret_flag_set_correctly(db: Session):
    seed_settings(db)
    rows = {s.key: s for s in db.execute(select(SystemSetting)).scalars()}
    assert rows["smtp.password"].is_secret is True
    assert rows["smtp.host"].is_secret is False
