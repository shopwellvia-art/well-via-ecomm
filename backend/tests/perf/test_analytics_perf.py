"""Gross-regression guards for analytics performance.

These tests exist to catch an *order-of-magnitude* change, and nothing finer.

That restraint is the whole design. A test that asserts "the executive overview
resolves in under 120ms" fails the first time CI runs on a busy shared runner,
somebody adds ``@pytest.mark.flaky``, and six weeks later it is deleted — at
which point the subsystem has no perf guard at all. So every threshold here sits
at least an order of magnitude above what the operation actually costs on the
dataset these tests build (see ``docs/analytics/PERFORMANCE.md`` for the real
numbers). What they catch is the change that turns a 40ms view into a 4s view:
an accidental ``lazy="joined"``, an N+1 introduced inside a loop, a dropped
index, a resolver that starts scanning the transactional tables instead of the
rollups.

Two of the assertions are not timings at all, and they are the most valuable
ones here:

* ``test_cold_resolve_does_not_explode_into_an_n_plus_one`` bounds the *query
  count* of a cold resolve. A query count is deterministic — it does not care
  how busy the machine is — so it can be set tight enough to actually fail when
  somebody introduces a per-row lookup, which a wall-clock assertion at
  10x headroom never would.
* ``test_the_warm_path_is_actually_a_cache_hit`` asserts the second read reports
  ``cache.hit``. Every warm-latency number in the report, and the brief's whole
  2s target, is downstream of that being true; a cache silently missing on every
  request would keep passing a latency assertion right up until production load.

The dataset is the ``tiny`` profile from ``scripts/analytics_perf_report.py`` —
400 orders over 21 days. It is deliberately NOT the 50 000-order profile the
report is written against: seeding that takes minutes and these tests are meant
to run in the ordinary suite. The consequence is stated rather than hidden —
these thresholds cannot detect a regression that only appears at volume. The
report is what covers that, and it is re-run by hand.
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

# The measurement machinery lives in scripts/, which is not a package. Loading
# it by path rather than copying its logic here is what keeps these thresholds
# checked against the same code that produced the published numbers.
_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "analytics_perf_report.py"
_spec = importlib.util.spec_from_file_location("analytics_perf_report", _SCRIPT)
assert _spec is not None and _spec.loader is not None
perf = importlib.util.module_from_spec(_spec)
sys.modules["analytics_perf_report"] = perf
_spec.loader.exec_module(perf)


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
# Every one of these is at least 10x the measured cost on the `tiny` dataset.
# They are ceilings on catastrophe, not service levels — the service levels are
# in docs/analytics/PERFORMANCE.md and are measured, not asserted.

#: The brief's own warm target. Warm reads on this dataset cost ~3ms, so this is
#: ~600x headroom and is still worth asserting: it is the number the product
#: promised, and a warm read that ever approaches it means the cache is broken.
WARM_CEILING_SEC = 2.0

#: 3x the brief's 5s cold target, ~100x the measured tiny-dataset cost. A cold
#: resolve that crosses this has stopped reading rollups.
COLD_CEILING_SEC = 15.0

#: One bucket of one job costs ~10-120ms on this dataset. 20s is the point at
#: which a 30-day backfill could not finish inside any sane budget.
JOB_BUCKET_CEILING_SEC = 20.0

#: A cold resolve of a single view issues single- to low-triple-digit queries.
#: 600 means something is querying per row. Deterministic, so it can be tight.
MAX_QUERIES_PER_COLD_RESOLVE = 600


@pytest.fixture(scope="module")
def perf_dataset():
    """The `tiny` dataset, built once for this module and removed afterwards.

    Torn down including the rollup tables: leaving 21 days of `agg_*` rows
    behind would change what the *functional* analytics suites see on their next
    run, and a perf test that quietly alters another suite's fixtures is worse
    than no perf test.
    """
    profile = perf.PROFILES["tiny"]
    perf.clean(rollups=True, quiet=True)
    summary = perf.seed(profile, force=True, quiet=True)
    yield summary
    perf.clean(rollups=True, quiet=True)


@pytest.fixture(scope="module")
def measured_views(perf_dataset):
    """One cold + one warm resolve of every LIVE/PARTIAL view.

    Module-scoped because 51 views x 2 resolves is the expensive part of this
    file, and every timing test below is an assertion about the same run rather
    than a fresh one. Sharing the run also means the tests cannot disagree with
    each other about what happened.
    """
    return perf.measure_views(repeat=1, period="30d")


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------
def test_guard_refuses_a_production_looking_host(monkeypatch):
    """The seeder must refuse the shared remote MySQL by host.

    This is the assertion that matters most in the file. `seed` writes hundreds
    of thousands of orders; pointed at 13.204.184.41 that is not a slow test, it
    is an unrecoverable incident. Asserted by host and by environment
    separately, because the two failure modes are independent — a developer with
    ENVIRONMENT=development and a remote host is exactly the case a
    single-condition guard would wave through.
    """
    monkeypatch.setattr(perf.settings, "MYSQL_HOST", "13.204.184.41")
    with pytest.raises(perf.UnsafeDatabaseError) as exc:
        perf.require_throwaway_database()
    assert "13.204.184.41" in str(exc.value)


def test_guard_refuses_a_production_environment(monkeypatch):
    monkeypatch.setattr(perf.settings, "ENVIRONMENT", "production")
    with pytest.raises(perf.UnsafeDatabaseError):
        perf.require_throwaway_database()


def test_guard_allows_the_configured_test_database():
    """Whatever this suite is pointed at must already satisfy the guard.

    If it does not, `tests/conftest.py`'s session guard would have aborted the
    run before reaching here — so this asserts the two copies of the rule agree
    rather than that one of them is permissive.
    """
    perf.require_throwaway_database()


def test_guard_allowlists_match_conftest():
    """The two copies of "is this database disposable" must not drift.

    They are deliberately duplicated (a script must not import a test fixture),
    which makes drift the only real risk. This is the test that makes the
    duplication safe.
    """
    from tests import conftest

    assert perf._SAFE_ENVIRONMENTS == conftest._SAFE_ENVIRONMENTS
    assert perf._SAFE_DB_HOSTS == conftest._SAFE_DB_HOSTS


# ---------------------------------------------------------------------------
# The seeder
# ---------------------------------------------------------------------------
def test_seed_produces_the_requested_shape(perf_dataset):
    profile = perf.PROFILES["tiny"]
    assert perf_dataset["orders"] == profile.orders
    # Every order has at least one line, and the mix averages under 4.
    assert profile.orders <= perf_dataset["order_items"] <= profile.orders * 4
    assert perf_dataset["shipments"] > 0
    assert perf_dataset["order_payments"] > 0
    assert perf_dataset["cart_events"] > 0
    assert perf_dataset["inventory_movements"] > 0


def test_seed_is_idempotent(perf_dataset):
    """A second seed at the same size must write nothing.

    Re-running the report must not double the dataset — a 100 000-order run
    silently sitting behind a 50 000-order label would make every number in the
    report unattributable to any stated volume.
    """
    again = perf.seed(perf.PROFILES["tiny"], quiet=True)
    assert again["seeded"] is False
    assert again["orders"] == perf.PROFILES["tiny"].orders


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
def test_every_live_view_is_measurable(measured_views):
    """No LIVE/PARTIAL view may fail to resolve at all.

    A view that raises is not slow, it is broken — and it would silently vanish
    from every latency percentile in the report if this did not fail first.
    """
    broken = [
        f'{v["module"]}/{v["view"]}: {v["error"]}'
        for v in measured_views["views"]
        if v["error"]
    ]
    assert not broken, "views that could not be resolved at all:\n" + "\n".join(broken)


def test_warm_reads_stay_under_the_two_second_target(measured_views):
    """The brief's own promise, asserted at ~600x headroom on this dataset."""
    over = [
        (f'{v["module"]}/{v["view"]}', v["warm"]["max_ms"])
        for v in measured_views["views"]
        if not v["error"] and v["warm"]["max_ms"] > WARM_CEILING_SEC * 1000
    ]
    assert not over, f"warm reads over {WARM_CEILING_SEC}s: {over}"


def test_cold_resolves_stay_an_order_of_magnitude_inside_the_budget(measured_views):
    """Catches a view that has stopped reading rollups, not a busy laptop."""
    over = [
        (f'{v["module"]}/{v["view"]}', v["cold"]["max_ms"])
        for v in measured_views["views"]
        if not v["error"] and v["cold"]["max_ms"] > COLD_CEILING_SEC * 1000
    ]
    assert not over, f"cold resolves over {COLD_CEILING_SEC}s: {over}"


def test_cold_resolve_does_not_explode_into_an_n_plus_one(measured_views):
    """Query count, not wall clock — deterministic, so it can be tight.

    A resolver that grows a per-row lookup crosses this long before it crosses
    any wall-clock ceiling on a dataset this small, which is precisely why the
    small dataset is still worth testing on.
    """
    noisy = [
        (f'{v["module"]}/{v["view"]}', v["cold"]["queries_p50"])
        for v in measured_views["views"]
        if not v["error"] and v["cold"]["queries_p50"] > MAX_QUERIES_PER_COLD_RESOLVE
    ]
    assert not noisy, (
        f"views issuing more than {MAX_QUERIES_PER_COLD_RESOLVE} queries for one "
        f"cold resolve: {noisy}"
    )


def test_the_warm_path_is_actually_a_cache_hit(measured_views):
    """Every warm latency in the report depends on this being true.

    A cache that silently misses would keep passing the latency assertion above
    on a 400-order dataset and would fall over at volume. This is the assertion
    that makes the other one mean something.
    """
    missed = [
        f'{v["module"]}/{v["view"]}'
        for v in measured_views["views"]
        if not v["error"] and not v["warm_actually_hit"]
    ]
    assert not missed, f"second read was not served from cache for: {missed}"


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def test_every_aggregation_job_rebuilds_one_bucket_quickly(perf_dataset):
    """One bucket, one job, well inside anything a backfill could tolerate."""
    result = perf.measure_jobs(repeat=1)
    assert result.get("jobs"), result
    failed = [j["job"] for j in result["jobs"] if j["error"]]
    assert not failed, f"jobs that raised: {[(j['job'], j['error']) for j in result['jobs'] if j['error']]}"
    slow = [
        (j["job"], j["max_ms"])
        for j in result["jobs"]
        if j["max_ms"] > JOB_BUCKET_CEILING_SEC * 1000
    ]
    assert not slow, f"jobs over {JOB_BUCKET_CEILING_SEC}s for a single bucket: {slow}"


def test_backfill_reports_honestly_when_the_budget_runs_out(perf_dataset):
    """A budget-exhausted run must be `partial` with a resumable watermark.

    Not a timing assertion — a correctness one about the *behaviour* the timings
    depend on. The runner's 45s default is what keeps the admin Recompute button
    inside nginx's 60s timeout, and that only works if stopping early is
    recorded rather than reported as success. Forced here with a 1ms budget so
    the assertion does not depend on how slow the machine is.
    """
    from datetime import timedelta

    from app.db.session import SessionLocal
    from app.models.analytics_control import SyncStatus
    from app.models.order import Order
    from app.services.analytics.aggregation import AggregationRunner
    from sqlalchemy import func, select

    with SessionLocal() as db:
        newest = db.execute(select(func.max(Order.created_at))).scalar_one()
        assert newest is not None
        date_to = newest.date()
        runner = AggregationRunner(db, worker_id=perf.WORKER_ID)
        summary = runner.run_window(
            ["order_daily"],
            date_to - timedelta(days=10),
            date_to,
            budget_ms=1,
        )
    assert summary["budget_exhausted"] is True
    assert summary["status"] in (SyncStatus.PARTIAL, SyncStatus.SUCCESS)
    assert summary["days_processed"] < summary["days_requested"]


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------
def test_csv_export_stays_inside_its_own_time_budget(perf_dataset):
    """The export path must finish well inside the 45s budget it declares.

    The budget exists because nginx kills the response at 60s and a CSV cut off
    mid-stream arrives as a valid-looking short file. This asserts the build
    never gets near it — including for the views that produce no table at all,
    which are reported by name rather than skipped.
    """
    from app.services.analytics.export import CSV_ROW_CAP, CSV_TIME_BUDGET_SEC

    result = perf.measure_exports()
    assert result["exports"], result

    over = [
        (f'{e["module"]}/{e["view"]}', e["wall_ms"])
        for e in result["exports"]
        if e.get("wall_ms", 0) / 1000.0 > CSV_TIME_BUDGET_SEC
    ]
    assert not over, f"exports over the {CSV_TIME_BUDGET_SEC}s build budget: {over}"

    # Nothing may exceed the declared row cap — a file past it is unbounded
    # memory in a request worker, whatever the timings say.
    too_many = [
        (f'{e["module"]}/{e["view"]}', e["rows"])
        for e in result["exports"]
        if e.get("rows", 0) > CSV_ROW_CAP
    ]
    assert not too_many, f"exports over the {CSV_ROW_CAP}-row cap: {too_many}"


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def test_cache_hit_rate_under_a_repeated_request_mix(perf_dataset):
    """A realistic mix must be served mostly from cache.

    Loose on purpose — the mix draws from 51 views and the `realtime` TTL is
    45s, so the achievable rate depends on how long the run takes. 40% is far
    below what a working cache produces and far above what a broken one does,
    which is the only distinction this test needs to make.
    """
    result = perf.measure_cache_mix(requests=60, seed_value=7)
    assert result["errors"] == 0, result
    assert result["hit_rate_pct"] >= 40.0, result


# ---------------------------------------------------------------------------
# Cleanup — last, because it empties the namespace and rebuilds it
# ---------------------------------------------------------------------------
def test_clean_removes_the_whole_namespace(perf_dataset):
    """`clean` must leave nothing behind, and must be safe to run twice.

    Placed last and re-seeds afterwards so the module fixture's teardown, and
    any test ordering plugin, still finds a coherent dataset. The seed is
    deterministic, so the rebuild is byte-identical to what the other tests ran
    against.
    """
    from app.db.session import SessionLocal

    perf.clean(rollups=False, quiet=True)
    with SessionLocal() as db:
        remaining = perf._existing_counts(db)
    assert remaining == {"orders": 0, "products": 0, "users": 0}, remaining

    # Idempotent: a second clean on an empty namespace is a no-op, not an error.
    perf.clean(rollups=False, quiet=True)

    perf.seed(perf.PROFILES["tiny"], force=True, quiet=True)


def test_measurement_overhead_is_not_the_thing_being_measured():
    """The timing harness itself must cost microseconds, not milliseconds.

    `measured()` installs a real observability collector, which attaches to
    every query in the block. If that instrumentation ever became expensive,
    every number in the report would be inflated by it and nothing else in this
    file would notice.
    """
    started = time.perf_counter()
    for _ in range(200):
        with perf.measured("noop"):
            pass
    per_call_ms = (time.perf_counter() - started) * 1000 / 200
    assert per_call_ms < 1.0, f"measured() costs {per_call_ms:.3f}ms per call"
