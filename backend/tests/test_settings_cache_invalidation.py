"""The settings cache must never outlive the value it caches.

`SettingsService.get_raw` caches every read in Redis for 60 seconds, including
the ABSENT case (as the `__NONE__` sentinel). That is the right trade for a
value read on every email send and every login — and it means a stale entry is
indistinguishable from a real one for a full minute.

The failure this file exists to prevent
---------------------------------------
`set_many` used to invalidate only the keys whose value actually MOVED. The
assumption underneath is that the cache agrees with the database whenever a
write is a no-op, and it does not:

  * Another process may have written the row. Each container has its own Redis
    and they share one MySQL, and `aggregation/jobs_ops.py` writes
    `analytics.inventory_history_since` as a row directly.
  * A reader caches `__NONE__` when the value is absent, so a value saved
    elsewhere inside that window reads back as "not configured".

Either way the operator sees a saved credential behave as though it were never
saved — and the one thing anybody tries, pressing Save again, was exactly the
path that skipped the invalidation, because the second save changes nothing.
It surfaced here as an analytics suite that was green in isolation and red in a
long run: `test_analytics_integrations` configures GA4, then reads it back and
gets "Missing: measurement id, Measurement Protocol API secret".

Isolation strategy
------------------
House style: no db fixture in `conftest.py`; each test owns its `SessionLocal()`
and restores what it touched in a `finally`. The key under test is a real seeded
settings row, so its original value is read first and written back on the way
out, cache included.

Run inside the analytics container::

    docker exec wvana-py python -m pytest tests/test_settings_cache_invalidation.py -q
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from app.db.redis import get_redis
from app.db.session import SessionLocal
from app.services.settings_service import SettingsService, _cache_key

#: A seeded, non-secret key with no behavioural side effects when it changes.
KEY = "store.timezone"
PROBE = "Etc/GMT-3"


@contextmanager
def _restored(key: str) -> Iterator[tuple[SettingsService, object]]:
    """A service plus Redis, with `key` put back exactly as it was found."""
    db = SessionLocal()
    redis_client = get_redis()
    service = SettingsService(db, redis_client)
    row = service.repo.get_by_key(key)
    assert row is not None, (
        f"{key!r} is not a seeded settings row on this database; conftest runs "
        "seed_settings, so its absence is a fixture problem, not a test bug."
    )
    original = row.value
    try:
        yield service, redis_client
    finally:
        try:
            db.rollback()
            row = service.repo.get_by_key(key)
            if row is not None:
                row.value = original
            db.commit()
        finally:
            redis_client.delete(_cache_key(key))
            db.close()


def test_a_no_op_write_still_clears_a_stale_cache_entry():
    """Saving the value that is already stored must heal the cache.

    The cache is poisoned the way another process poisons it: the row holds a
    real value and Redis holds `__NONE__`. Then the value is written again with
    no change — which is what an operator does when a saved setting is behaving
    as if it were missing. Before this was fixed, `changed` came back empty, no
    key was deleted, and the read kept answering None until the TTL expired.
    """
    with _restored(KEY) as (service, redis_client):
        service.set_many({KEY: PROBE})
        service.db.commit()
        assert service.get_raw(KEY) == PROBE

        redis_client.setex(_cache_key(KEY), 60, "__NONE__")
        assert service.get_raw(KEY) is None, "fixture: the cache is now lying"

        changed = service.set_many({KEY: PROBE})
        service.db.commit()

        assert changed == [], "the value did not move; this is the no-op case"
        assert service.get_raw(KEY) == PROBE, (
            "a no-op write must still invalidate. Pressing Save again is the "
            "only lever an operator has, and it was the one path that skipped "
            "the delete."
        )


def test_a_stale_value_from_another_process_is_cleared_too():
    """Not just the absent sentinel: any stale value must go.

    `jobs_ops` writes a settings row directly, which invalidates nothing. The
    next write through the service is the first chance to notice, and it must
    take it whether or not it is changing anything itself.
    """
    with _restored(KEY) as (service, redis_client):
        service.set_many({KEY: PROBE})
        service.db.commit()
        redis_client.setex(_cache_key(KEY), 60, "Antarctica/Troll")

        service.set_many({KEY: PROBE})
        service.db.commit()

        assert service.get_raw(KEY) == PROBE


def test_a_changed_write_still_invalidates():
    """The case that always worked, kept honest.

    Widening the invalidation must not have been done by narrowing it somewhere
    else, so the ordinary path is asserted alongside.
    """
    with _restored(KEY) as (service, redis_client):
        service.set_many({KEY: PROBE})
        service.db.commit()
        assert service.get_raw(KEY) == PROBE
        assert redis_client.get(_cache_key(KEY)) == PROBE

        service.set_many({KEY: "Etc/GMT-4"})
        service.db.commit()

        assert service.get_raw(KEY) == "Etc/GMT-4"


#: A key with no settings row at all, so `default` is the only thing that can
#: answer. Nothing seeds it and nothing reads it in production.
ABSENT = "no.such.setting.for.default.tests"


def test_a_default_is_honoured_on_the_cached_absent_path_too():
    """`get_raw` must answer the same thing hot or cold.

    The absent case is cached as `__NONE__`, and the hit path used to return
    early on that sentinel — so a bare `get_raw(k)` running first poisoned every
    later `get_raw(k, default=...)` into returning None for a full minute. The
    first call and the second call are identical code; only the cache differed.
    """
    redis_client = get_redis()
    db = SessionLocal()
    try:
        service = SettingsService(db, redis_client)
        redis_client.delete(_cache_key(ABSENT))

        assert service.get_raw(ABSENT) is None
        assert redis_client.get(_cache_key(ABSENT)) == "__NONE__", (
            "fixture: the absent case is what we mean to have cached"
        )

        assert service.get_raw(ABSENT, default="fallback") == "fallback", (
            "a cached absence is still an absence — the caller's default has to "
            "apply, or the answer depends on who asked first"
        )
    finally:
        redis_client.delete(_cache_key(ABSENT))
        db.close()


def test_one_callers_default_is_never_cached_as_another_callers_value():
    """The cache stores the database's answer, not the caller's fallback.

    `storage/__init__.py` reads a dozen keys as `get_raw(key, env_value)`. If the
    fallback were cached, a bare read of the same key would report a configured
    value that exists nowhere — an env default promoted to a stored setting, and
    indistinguishable from one for a minute.
    """
    redis_client = get_redis()
    db = SessionLocal()
    try:
        service = SettingsService(db, redis_client)
        redis_client.delete(_cache_key(ABSENT))

        assert service.get_raw(ABSENT, default="from-env") == "from-env"
        assert redis_client.get(_cache_key(ABSENT)) == "__NONE__", (
            "the fallback must not be written to the cache as though it were "
            "the stored value"
        )
        assert service.get_raw(ABSENT) is None, (
            "nothing is configured for this key, and a read with no default "
            "must keep saying so"
        )
    finally:
        redis_client.delete(_cache_key(ABSENT))
        db.close()


def test_an_empty_stored_value_reads_as_unset_hot_and_cold():
    """An empty row means "unset", so the default applies — on both paths.

    `set_many` writes the empty string for a cleared field precisely so the env
    fallback kicks back in. That contract has to survive the cache.
    """
    with _restored(KEY) as (service, redis_client):
        service.set_many({KEY: ""})
        service.db.commit()

        cold = service.get_raw(KEY, default="Etc/GMT-5")
        hot = service.get_raw(KEY, default="Etc/GMT-5")
        assert cold == hot == "Etc/GMT-5"
        assert redis_client.get(_cache_key(KEY)) == "__NONE__"


def test_an_unknown_key_in_the_batch_is_ignored_and_not_cached():
    """An unknown key is logged and skipped; it must not raise or write.

    `set_many` is called with whatever the admin form posted, so a key that no
    longer exists in the catalogue has to be survivable.
    """
    with _restored(KEY) as (service, _redis):
        changed = service.set_many({KEY: PROBE, "no.such.setting": "x"})
        service.db.commit()

        assert [row.key for row, _b, _a in changed] == [KEY]
        assert service.get_raw("no.such.setting") is None
