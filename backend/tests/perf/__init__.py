"""Performance regression tests for the analytics subsystem.

Separate from ``tests/`` proper because these are the only tests in the suite
whose *duration* is the assertion. They still run on every invocation of
``pytest tests/`` — a perf guard nobody runs is a perf guard that does not
exist — but they live in their own package so ``pytest tests/ --ignore=tests/perf``
is a single obvious flag for someone bisecting a functional failure.

The measurement machinery itself is ``backend/scripts/analytics_perf_report.py``.
These tests import it rather than reimplementing it, so the thresholds below are
checked against exactly the code path that produced the numbers in
``docs/analytics/PERFORMANCE.md``.
"""
