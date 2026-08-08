"""Projection semantics in ``analytics_repository``: Boolean SUM, and ``avg:``.

Two defects and one new capability, all of the same species — a query that
succeeds and returns a number nobody can check.

1. ``SUM(<Boolean column>)`` reported a COUNT as a TRUTH VALUE
--------------------------------------------------------------
``func.sum(col)`` inherits ``col``'s SQLAlchemy type, so summing a ``Boolean``
produced an aggregate typed ``Boolean`` and the result went back through the
Boolean result processor. A day with seven out-of-stock products came back as
``True``. Against MySQL 8, same SQL both ways::

    SELECT sum(agg_inventory_daily.is_oos)                 -> True   (bool)
    SELECT sum(CAST(agg_inventory_daily.is_oos AS SIGNED)) -> 7      (int)

The database was never wrong — a raw ``text("SELECT SUM(is_oos) ...")`` returns
``Decimal('7')``. Everything was lost in the result processor, which is why
nothing raised: ``True`` formats as ``1``, and one product out of stock is an
utterly ordinary reading. Three rollup columns were reachable this way —
``agg_inventory_daily.is_oos``, ``agg_customer_snapshot.is_active`` and
``agg_promo_daily.is_loyalty_reward``.

``test_the_unguarded_sum_still_returns_a_truth_value`` keeps the bug itself
pinned, so the fix can never quietly become a no-op, and
``test_every_boolean_rollup_column_sums_as_an_integer_count`` REFLECTS over the
models rather than naming those three, so a fourth Boolean added to any
``agg_*`` table is covered the day it lands.

Asserting the type is not pedantry. ``isinstance(True, int)`` is ``True`` in
Python and ``Decimal('1') == True``, so both ``assert result == 1`` and
``assert isinstance(result, int)`` PASS on exactly the bug they would be there
to catch. Only ``type(result) is int`` excludes a bool.

2. ``avg:<column>`` — a projection that did not exist
-----------------------------------------------------
Inventory Turnover is COGS over *average* stock held, and ``stock_close`` is a
LEVEL: summing it counts the same units once per day, and the latest bucket
answers "what do we hold now". Neither existing projection can produce that
denominator.

The two things that make it safe are asserted here directly:

  * it is REFUSED for a FLOW (``avg:units_sold``), because the mean of a set of
    daily sums changes value when the same period is re-bucketed; and
  * **empty buckets are skipped, not read as zero.** ``AVG`` averages the rows
    that EXIST. A gap day is absent from both numerator and denominator.
    ``test_avg_skips_the_gap_day_rather_than_reading_it_as_zero`` pins the exact
    figure that distinguishes the two readings: 10 on the 1st, no row on the
    2nd, 20 on the 3rd is **15**, not 10. The calendar-day reading deflates
    average stock, and average stock is turnover's denominator, so it would
    report higher turnover — the flattering direction, the one nobody
    investigates. It also contradicts ``agg_inventory_daily``'s own rule that an
    absent bucket means "we have no data", not "we held no stock".

3. ``metric_kind`` classification of the inventory states
---------------------------------------------------------
``is_oos``, ``days_oos`` and ``reorder_gap`` all describe the product's position
at the CLOSE of ``bucket_date``, and all three fell through to FLOW because
nothing in their names says so. ``days_oos`` is the sharpest: the job writes
``previous + 1``, so a three-day stockout is stored 1, 2, 3 and summing it
reports **6** out-of-stock days — wrong by a triangular number, so the error
grows with the length of the stockout it describes. That is asserted against
real rows rather than argued.

Note the interaction between (1) and (3), which is why they land together: while
``SUM(is_oos)`` returned ``True`` the wrong query was obviously broken. Fixing
the cast makes it return a believable count, so the classification is now the
only thing standing between a resolver and a stockout rate that is silently the
time-weighted product-day share instead of the point-in-time figure the KPI
catalogue defines.

Isolation strategy
------------------
House style, following ``test_analytics_level_resolver.py``: no db fixture in
``conftest.py``; every test owns its ``SessionLocal()`` and tears down in a
``finally``.

  1. Fixtures live in **March 1997**, a year no other suite writes in (the
     existing sandboxes are 1990, 2001-2015, 2024 and 2026), so no peer's rows
     can be inside a window here and none of this suite's rows can be inside
     theirs.
  2. Each test also takes a **private ``tz_generation``** from the 32700-32759
     band, above every band in use (1000-20999, 21000-21999, 22000-30999,
     31000-32699).
  3. Teardown deletes on **generation AND date range together**. Generation
     alone would be enough for this suite's own hygiene, but the band is narrow
     and a collision would then delete a concurrently-running suite's rows; the
     date clause makes that impossible.

Run inside the analytics container:

    docker exec wvana-py python -m pytest tests/test_analytics_repository_projections.py -q
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterator

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.schema import UniqueConstraint

from app.db.session import SessionLocal, engine
from app.models.analytics_rollups import AggInventoryDaily
from app.repositories.analytics_repository import (
    SOURCES,
    AVG_PREFIX,
    AnalyticsRepository,
    _is_boolean,
    _measure,
    _sum,
    measures_for,
)
from app.services.analytics.filters import ResolvedWindow
from app.services.analytics.metric_kind import (
    MetricKind,
    NonAdditive,
    NonAveragable,
    assert_averagable,
    assert_summable,
    classify,
    combine_strategy,
)

# March 1997 — no other analytics suite writes in this year.
SANDBOX_START = date(1997, 3, 1)
SANDBOX_END = date(1997, 4, 1)

INVENTORY = "agg_inventory_daily"


def _day(offset: int) -> date:
    return date.fromordinal(SANDBOX_START.toordinal() + offset)


def _window(days: int = 31) -> ResolvedWindow:
    return ResolvedWindow(date_from=SANDBOX_START, date_to=_day(days))


def _distinct_sources() -> list[Any]:
    """One spec per real table — `SOURCES` registers each under two aliases."""
    seen: dict[str, Any] = {}
    for spec in SOURCES.values():
        seen.setdefault(spec.name, spec)
    return [seen[name] for name in sorted(seen)]


#: Every rollup model reachable through the repository, so teardown can clear
#: anything a reflective test decided to write to.
_OWNED_MODELS = tuple(spec.model for spec in _distinct_sources())


@contextmanager
def sandbox() -> Iterator[tuple[Session, int]]:
    """A session plus a private ``tz_generation``, cleaned up unconditionally."""
    db = SessionLocal()
    # `tz_generation` is a SmallInteger (max 32767). 32700-32759 sits above
    # every band the other suites draw from; see the module docstring.
    generation = 32700 + (uuid.uuid4().int % 60)
    try:
        yield db, generation
    finally:
        try:
            db.rollback()
            for model in _OWNED_MODELS:
                db.execute(
                    delete(model).where(
                        model.tz_generation == generation,
                        model.bucket_date >= SANDBOX_START,
                        model.bucket_date < SANDBOX_END,
                    )
                )
            db.commit()
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Reflective row builder
# ---------------------------------------------------------------------------
# The guard test writes to whichever tables happen to carry a Boolean column,
# which is a set that must be allowed to grow without editing this file. So a
# row is BUILT from the mapper rather than hand-written per model.


def _grain_keys(model: type) -> frozenset[str]:
    """The UNIQUE-key columns other than the two every rollup shares.

    Each seeded row must differ on these or the second INSERT collides with the
    first on the rollup's idempotency key.
    """
    keys: set[str] = set()
    for constraint in model.__table__.constraints:
        if isinstance(constraint, UniqueConstraint):
            keys.update(c.key for c in constraint.columns)
    return frozenset(keys - {"bucket_date", "tz_generation"})


def _filler(column: Any, index: int) -> Any:
    """A per-row-distinct value of the right Python type."""
    try:
        python_type = column.type.python_type
    except (NotImplementedError, AttributeError):  # pragma: no cover - defensive
        return None
    if python_type is bool:
        return False
    if python_type is int:
        return 970000 + index
    if python_type is str:
        return f"t{index}"[: getattr(column.type, "length", None) or 8]
    if python_type in (float, Decimal):
        return Decimal("0")
    if python_type is date:
        return SANDBOX_START
    if python_type is datetime:
        return datetime.now(timezone.utc)
    return None  # pragma: no cover - defensive


def _row(model: type, *, generation: int, day: date, index: int, **overrides: Any) -> dict:
    """A valid row for any rollup model, distinct from its siblings."""
    grain = _grain_keys(model)
    values: dict[str, Any] = {"bucket_date": day, "tz_generation": generation}
    for column in model.__table__.columns:
        key = column.key
        if key in values or key in overrides or column.primary_key:
            continue
        has_default = column.server_default is not None or column.default is not None
        if key in grain or not (column.nullable or has_default):
            values[key] = _filler(column, index)
    values.update(overrides)
    return values


def _seed(db: Session, model: type, rows: list[dict]) -> None:
    db.execute(model.__table__.insert(), rows)
    db.commit()


def _inventory_row(*, generation: int, day: date, index: int, **overrides: Any) -> dict:
    """An inventory row with everything at rest, so a test states only its point."""
    values: dict[str, Any] = {
        "sku_snapshot": f"SKU-{index}",
        "stock_close": 0,
        "stock_value_close": Decimal("0"),
        "units_sold": 0,
        "units_restocked": 0,
        "is_oos": False,
        "days_oos": 0,
        "reorder_gap": None,
    }
    values.update(overrides)
    return _row(
        AggInventoryDaily, generation=generation, day=day, index=index, **values
    )


# ---------------------------------------------------------------------------
# 1. SUM over a Boolean column
# ---------------------------------------------------------------------------


def test_the_unguarded_sum_still_returns_a_truth_value():
    """The defect itself, pinned so the fix cannot become a no-op.

    This builds the expression the repository USED to build. If SQLAlchemy ever
    stops coercing here, this test fails and the cast can be reconsidered on
    purpose rather than left in place as cargo. The raw-SQL read alongside it is
    the other half of the proof: MySQL returns 7, so nothing about this is a
    database behaviour — it happens entirely in the result processor.
    """
    with sandbox() as (db, generation):
        day = _day(0)
        _seed(
            db,
            AggInventoryDaily,
            [
                _inventory_row(generation=generation, day=day, index=i, is_oos=i < 7)
                for i in range(9)
            ],
        )

        scope = (
            AggInventoryDaily.tz_generation == generation,
            AggInventoryDaily.bucket_date == day,
        )
        unguarded = db.execute(
            select(func.sum(AggInventoryDaily.is_oos)).where(*scope)
        ).scalar()
        assert unguarded is True, (
            "the unguarded SUM no longer coerces to a truth value — the "
            "repository's cast may now be unnecessary; verify before removing it"
        )

        from_the_database = db.execute(
            text(
                "SELECT SUM(is_oos) FROM agg_inventory_daily "
                "WHERE tz_generation = :g AND bucket_date = :d"
            ),
            {"g": generation, "d": day},
        ).scalar()
        assert from_the_database == Decimal("7"), (
            "MySQL was never wrong; the count is lost in SQLAlchemy's Boolean "
            "result processor, not in the query"
        )


def test_boolean_sum_is_an_integer_count_not_a_truth_value():
    """Seven out-of-stock products report 7, and report it as an ``int``.

    ``type(...) is int`` rather than ``isinstance`` on purpose: ``isinstance(
    True, int)`` is ``True``, so an isinstance check passes on the bug.
    """
    with sandbox() as (db, generation):
        day = _day(0)
        _seed(
            db,
            AggInventoryDaily,
            [
                _inventory_row(generation=generation, day=day, index=i, is_oos=i < 7)
                for i in range(9)
            ],
        )

        totals = AnalyticsRepository(db).fetch_totals(
            INVENTORY,
            columns=["is_oos"],
            window=_window(),
            tz_generation=generation,
        )
        value = totals["is_oos"]
        assert type(value) is int, (
            f"expected an integer count, got {value!r} ({type(value).__name__}). "
            "A bool here is the original defect: `== 7` and `isinstance(v, int)` "
            "both pass on it when the count is 1."
        )
        assert value == 7


def test_a_single_flagged_row_is_the_case_the_bug_hides_in():
    """One out-of-stock product. ``True == 1``, so only the type tells them apart.

    Every other count in this suite would catch the bug on its value. This one
    would not, and one flagged row is the commonest reading there is.
    """
    with sandbox() as (db, generation):
        day = _day(0)
        _seed(
            db,
            AggInventoryDaily,
            [
                _inventory_row(generation=generation, day=day, index=i, is_oos=i == 0)
                for i in range(4)
            ],
        )

        value = AnalyticsRepository(db).fetch_totals(
            INVENTORY,
            columns=["is_oos"],
            window=_window(),
            tz_generation=generation,
        )["is_oos"]
        assert value == 1
        assert type(value) is int, "the value is right and the type is the bug"


def test_every_boolean_rollup_column_sums_as_an_integer_count():
    """Reflected over the models, so a NEW Boolean column is covered on arrival.

    Deliberately not a list of the three known names. The whole reason the fix
    lives in the repository rather than at each call site is that a per-caller
    fix is one new rollup column away from regressing; a test that hardcodes
    today's columns has the same defect. Any Boolean measure on any registered
    ``agg_*`` model is seeded and checked here.
    """
    checked: list[str] = []
    wrong: list[str] = []
    for spec in _distinct_sources():
        booleans = sorted(
            name
            for name in measures_for(spec.name)
            if _is_boolean(spec.columns[name])
        )
        for name in booleans:
            with sandbox() as (db, generation):
                day = _day(0)
                # 5 true, 3 false — a count no truth value can imitate, and one
                # that also catches a cast that accidentally counts every row.
                _seed(
                    db,
                    spec.model,
                    [
                        _row(
                            spec.model,
                            generation=generation,
                            day=day,
                            index=i,
                            **{name: i < 5},
                        )
                        for i in range(8)
                    ],
                )

                value = AnalyticsRepository(db).fetch_totals(
                    spec.name,
                    columns=[name],
                    window=_window(),
                    tz_generation=generation,
                )[name]
                checked.append(f"{spec.name}.{name}")
                # Collected rather than asserted in the loop: the point of this
                # test is to name WHICH column regressed, and stopping at the
                # first one hides the rest from whoever added them.
                if type(value) is not int or value != 5:
                    wrong.append(
                        f"{spec.name}.{name} -> {value!r} "
                        f"({type(value).__name__}), expected 5 (int)"
                    )

    assert not wrong, (
        "these Boolean rollup columns do not sum to an integer count:\n  "
        + "\n  ".join(wrong)
        + "\nThe fix belongs in `analytics_repository._sum`, which casts a "
        "Boolean before summing it, not at the call site."
    )
    assert set(checked) >= {
        "agg_inventory_daily.is_oos",
        "agg_customer_snapshot.is_active",
        "agg_promo_daily.is_loyalty_reward",
    }, (
        "the three Boolean rollup columns known at the time of the fix are no "
        f"longer all reachable as summable projections; found {checked}. If a "
        "column was renamed or removed, update this floor — do not delete it, "
        "it is what proves the reflection above actually reached anything."
    )


def test_every_boolean_projection_is_cast_in_the_sql():
    """The rendered SQL, not just the returned value.

    The value assertions above would also pass if a future change fixed the
    coercion in Python instead of in the query — and a Python-side fix is
    exactly the kind that gets bypassed by the next code path. This pins the
    shape the fix actually takes, against every Boolean measure the models
    expose and against no non-Boolean one (a blanket cast would be a different
    change with different consequences for money columns).
    """
    for spec in _distinct_sources():
        for name in measures_for(spec.name):
            rendered = str(_sum(_measure(spec, name)).compile(engine)).upper()
            if _is_boolean(spec.columns[name]):
                assert "CAST" in rendered, (
                    f"{spec.name}.{name} is a Boolean summed without a cast, so "
                    f"it will report its count as a truth value: {rendered}"
                )
            else:
                assert "CAST" not in rendered, (
                    f"{spec.name}.{name} is not a Boolean but is being cast: "
                    f"{rendered}. Casting a DECIMAL money column to an integer "
                    "would truncate paise."
                )


def test_boolean_sum_survives_grouping():
    """The grouped path builds its own SUM, so it needs its own assertion."""
    with sandbox() as (db, generation):
        rows = []
        for offset, flagged in ((0, 3), (1, 1)):
            rows.extend(
                _inventory_row(
                    generation=generation,
                    day=_day(offset),
                    index=offset * 10 + i,
                    is_oos=i < flagged,
                )
                for i in range(5)
            )
        _seed(db, AggInventoryDaily, rows)

        grouped = AnalyticsRepository(db).fetch_rollup(
            INVENTORY,
            columns=["is_oos"],
            window=_window(),
            tz_generation=generation,
            group_by=["bucket_date"],
        )
        by_day = {r["bucket_date"]: r["is_oos"] for r in grouped}
        assert by_day == {_day(0): 3, _day(1): 1}
        for value in by_day.values():
            assert type(value) is int


def test_boolean_sum_over_an_empty_window_is_none_not_false():
    """No rows is "nothing measured", and must not arrive as ``False`` or 0.

    The whole module's rule — 0 means "measured, and it was zero"; ``None``
    means "there is nothing to measure yet". A ``False`` here would read as a
    measured zero AND carry the coercion bug's fingerprint.
    """
    with sandbox() as (db, generation):
        value = AnalyticsRepository(db).fetch_totals(
            INVENTORY,
            columns=["is_oos"],
            window=_window(),
            tz_generation=generation,
        )["is_oos"]
        assert value is None
        assert value is not False


# ---------------------------------------------------------------------------
# 2. The avg: projection
# ---------------------------------------------------------------------------


def test_avg_of_a_level_returns_the_mean():
    """Average stock over the window — turnover's denominator."""
    with sandbox() as (db, generation):
        _seed(
            db,
            AggInventoryDaily,
            [
                _inventory_row(
                    generation=generation, day=_day(i), index=1, stock_close=stock
                )
                for i, stock in enumerate((10, 20, 30))
            ],
        )

        value = AnalyticsRepository(db).fetch_totals(
            INVENTORY,
            columns=[AVG_PREFIX + "stock_close"],
            window=_window(),
            tz_generation=generation,
        )[AVG_PREFIX + "stock_close"]
        assert value == Decimal("20")


def test_avg_skips_the_gap_day_rather_than_reading_it_as_zero():
    """**The empty-bucket decision, pinned.** Rows that exist, not calendar days.

    Stock 10 on the 1st, NO ROW on the 2nd, stock 20 on the 3rd, over a window
    that spans all three days.

      * mean over the rows that exist  = (10 + 20) / 2 = **15**  <- implemented
      * mean over calendar days        = (10 + 20) / 3 = 10

    Both are numbers a reader would accept. The implemented one is right because
    an absent bucket in ``agg_inventory_daily`` means "we have no data", not "we
    held no stock" — that table is forward-only and its ``inventory_history_since``
    setting exists precisely to stop absence being drawn as zero. It is also the
    conservative direction: average stock is Inventory Turnover's DENOMINATOR,
    so the calendar-day reading would deflate it and report higher turnover.

    A caller who wants the calendar-day figure can still have it, and has to
    name its denominator to get it — which is the point, because an implicit
    denominator is what makes the wrong version invisible.
    """
    with sandbox() as (db, generation):
        _seed(
            db,
            AggInventoryDaily,
            [
                _inventory_row(
                    generation=generation, day=_day(0), index=1, stock_close=10
                ),
                # _day(1) deliberately absent.
                _inventory_row(
                    generation=generation, day=_day(2), index=1, stock_close=20
                ),
            ],
        )

        repo = AnalyticsRepository(db)
        window = ResolvedWindow(date_from=_day(0), date_to=_day(3))
        result = repo.fetch_totals(
            INVENTORY,
            columns=[AVG_PREFIX + "stock_close", "stock_close"],
            window=window,
            tz_generation=generation,
        )

        assert result[AVG_PREFIX + "stock_close"] == Decimal("15"), (
            "the gap day was counted in the denominator — that reads an absent "
            "bucket as zero stock and inflates Inventory Turnover"
        )
        assert result[AVG_PREFIX + "stock_close"] != Decimal("10")
        # The calendar-day average remains expressible, with a stated
        # denominator: the caller divides the SUM by a day count it chose.
        assert result["stock_close"] / 3 == Decimal("10")


def test_avg_over_an_empty_window_is_none_not_zero():
    """Same rule as `fetch_totals`: absence is not a measured zero."""
    with sandbox() as (db, generation):
        value = AnalyticsRepository(db).fetch_totals(
            INVENTORY,
            columns=[AVG_PREFIX + "stock_close"],
            window=_window(),
            tz_generation=generation,
        )[AVG_PREFIX + "stock_close"]
        assert value is None


def test_avg_of_a_flow_column_is_refused():
    """``AVG(units_sold)`` has no correct caller, so it is never built.

    "Units per bucket" changes value when the same period is re-bucketed from
    days to weeks. Refusing beats returning it: the number is plausible and the
    reader has nothing to compare it against.
    """
    with sandbox() as (db, generation):
        repo = AnalyticsRepository(db)
        with pytest.raises(NonAveragable) as excinfo:
            repo.fetch_totals(
                INVENTORY,
                columns=[AVG_PREFIX + "units_sold"],
                window=_window(),
                tz_generation=generation,
            )
        assert "flow" in str(excinfo.value).lower()
        # A `NonAdditive` subclass, so every existing handler still catches it.
        assert isinstance(excinfo.value, NonAdditive)

        with pytest.raises(NonAveragable):
            repo.fetch_rollup(
                INVENTORY,
                columns=[AVG_PREFIX + "units_restocked"],
                window=_window(),
                tz_generation=generation,
                group_by=["bucket_date"],
            )


def test_avg_of_a_per_row_derived_column_is_refused():
    """``agg_customer_snapshot.aov`` classifies LEVEL but must not be averaged.

    Its model docstring says so in these words: "never SUM it, never AVG it
    across rows. A cross-customer AOV is SUM(gross_ltv) / SUM(orders_count)".
    LEVEL alone would have waved this through, which is why `assert_averagable`
    checks the derived list first.
    """
    with sandbox() as (db, generation):
        with pytest.raises(NonAveragable) as excinfo:
            AnalyticsRepository(db).fetch_totals(
                "agg_customer_snapshot",
                columns=[AVG_PREFIX + "aov"],
                window=_window(),
                tz_generation=generation,
            )
        assert "average of averages" in str(excinfo.value)


def test_avg_is_a_grouped_aggregate_too():
    """Grouped by day: the mean across products on each day."""
    with sandbox() as (db, generation):
        rows = []
        for offset, stocks in ((0, (10, 30)), (1, (4, 4, 10))):
            rows.extend(
                _inventory_row(
                    generation=generation,
                    day=_day(offset),
                    index=i,
                    stock_close=stock,
                )
                for i, stock in enumerate(stocks)
            )
        _seed(db, AggInventoryDaily, rows)

        rows_out = AnalyticsRepository(db).fetch_rollup(
            INVENTORY,
            columns=[AVG_PREFIX + "stock_close"],
            window=_window(),
            tz_generation=generation,
            group_by=["bucket_date"],
        )
        by_day = {r["bucket_date"]: r[AVG_PREFIX + "stock_close"] for r in rows_out}
        assert by_day == {_day(0): Decimal("20"), _day(1): Decimal("6")}


def test_avg_resolves_its_argument_through_the_allowlist():
    """`avg:` names a real column, so it goes through the same gate a SUM does.

    The prefix must not become a hole in the boundary this module exists to be.
    """
    with sandbox() as (db, generation):
        repo = AnalyticsRepository(db)
        for hostile in (
            AVG_PREFIX + "1; DROP TABLE agg_inventory_daily",
            AVG_PREFIX + "stock_close) FROM agg_order_daily --",
            AVG_PREFIX + "",
        ):
            with pytest.raises(ValueError):
                repo.fetch_totals(
                    INVENTORY,
                    columns=[hostile],
                    window=_window(),
                    tz_generation=generation,
                )

        # A real column that is a DIMENSION, not a measure, is refused too.
        with pytest.raises(ValueError) as excinfo:
            repo.fetch_totals(
                INVENTORY,
                columns=[AVG_PREFIX + "sku_snapshot"],
                window=_window(),
                tz_generation=generation,
            )
        assert "averageable" in str(excinfo.value)


def test_avg_of_a_boolean_level_is_a_share_not_a_truth_value():
    """`is_oos` is a LEVEL, so its mean is legal — and must not coerce either.

    ``func.avg`` does not inherit the column type today (unlike ``sum``, ``min``
    and ``max``, which all do), so this is currently correct by accident. The
    repository casts anyway; this is what keeps that honest.
    """
    with sandbox() as (db, generation):
        day = _day(0)
        _seed(
            db,
            AggInventoryDaily,
            [
                _inventory_row(generation=generation, day=day, index=i, is_oos=i < 3)
                for i in range(4)
            ],
        )
        value = AnalyticsRepository(db).fetch_totals(
            INVENTORY,
            columns=[AVG_PREFIX + "is_oos"],
            window=_window(),
            tz_generation=generation,
        )[AVG_PREFIX + "is_oos"]
        assert type(value) is not bool
        assert value == Decimal("0.75")


# ---------------------------------------------------------------------------
# 3. metric_kind classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("column", ["is_oos", "days_oos", "reorder_gap"])
def test_inventory_states_classify_as_level(column: str):
    """All three describe the position at the CLOSE of the bucket.

    None of their names carries a `_close` suffix, so before the `_LEVEL_EXACT`
    entries every one of them fell through to FLOW and was summable.
    """
    assert classify(column, source=INVENTORY) is MetricKind.LEVEL
    assert combine_strategy(column, source=INVENTORY) == "latest"
    with pytest.raises(NonAdditive):
        assert_summable(column, source=INVENTORY)
    # LEVEL is exactly the kind that MAY be averaged.
    assert_averagable(column, source=INVENTORY)


@pytest.mark.parametrize("column", ["units_sold", "units_restocked"])
def test_inventory_movements_are_still_flows(column: str):
    """The classification must not have swept up the ledger's real flows.

    A wrong call in this direction blocks a legitimate query — view 27 binds
    both of these to the timeseries resolver and sums them correctly.
    """
    assert classify(column, source=INVENTORY) is MetricKind.FLOW
    assert_summable(column, source=INVENTORY)
    with pytest.raises(NonAveragable):
        assert_averagable(column, source=INVENTORY)


def test_summing_days_oos_would_report_a_triangular_number():
    """Why `days_oos` is a LEVEL, demonstrated on rows shaped as the job writes.

    ``jobs_ops.InventoryDailyJob._days_oos`` reads the PREVIOUS bucket and
    writes ``previous + 1``, so a three-day stockout is stored 1, 2, 3. The
    correct answer to "how many days was this product out of stock" is 3 — the
    LATEST value. SUM gives 6, and the error grows with the length of the very
    stockout being described, which is the direction that makes it least
    likely to be noticed on a short test window and most wrong in production.
    """
    with sandbox() as (db, generation):
        _seed(
            db,
            AggInventoryDaily,
            [
                _inventory_row(
                    generation=generation,
                    day=_day(offset),
                    index=1,
                    is_oos=True,
                    days_oos=offset + 1,
                )
                for offset in range(3)
            ],
        )

        summed = AnalyticsRepository(db).fetch_totals(
            INVENTORY,
            columns=["days_oos"],
            window=_window(),
            tz_generation=generation,
        )["days_oos"]
        assert summed == 6, "fixture no longer shaped like the job's output"

        latest = db.execute(
            select(AggInventoryDaily.days_oos)
            .where(
                AggInventoryDaily.tz_generation == generation,
                AggInventoryDaily.bucket_date >= SANDBOX_START,
                AggInventoryDaily.bucket_date < SANDBOX_END,
            )
            .order_by(AggInventoryDaily.bucket_date.desc())
            .limit(1)
        ).scalar()
        assert latest == 3

        # The repository still EXECUTES that sum — it is a safety boundary, not
        # a semantic one. `metric_kind` is what stops a resolver reaching it.
        with pytest.raises(NonAdditive):
            assert_summable("days_oos", source=INVENTORY)


def test_reorder_gap_nulls_are_not_zeros():
    """`reorder_gap` NULL means "no reorder point configured", not "at it".

    The job writes NULL unconditionally today, so a SUM would skip every row and
    return ``None``; where some rows were configured it would total a subset
    under a heading that implies all of them. Both are levels-of-a-difference,
    which is why the column is classified rather than left summable.
    """
    with sandbox() as (db, generation):
        _seed(
            db,
            AggInventoryDaily,
            [
                _inventory_row(
                    generation=generation, day=_day(0), index=1, reorder_gap=None
                ),
                _inventory_row(
                    generation=generation, day=_day(0), index=2, reorder_gap=-5
                ),
            ],
        )
        summed = AnalyticsRepository(db).fetch_totals(
            INVENTORY,
            columns=["reorder_gap"],
            window=_window(),
            tz_generation=generation,
        )["reorder_gap"]
        assert summed == -5, "NULL is skipped, so the total covers one row of two"
        assert classify("reorder_gap", source=INVENTORY) is MetricKind.LEVEL


def test_is_oos_blocking_is_what_the_cast_fix_made_necessary():
    """The two changes interact, and the interaction is the justification.

    Before the cast, ``SUM(is_oos)`` returned ``True`` — obviously broken, so no
    resolver could have shipped a wrong stockout rate on it. After the cast it
    returns a believable count of out-of-stock PRODUCT-DAYS, which divided by
    ``COUNT(*)`` is the time-weighted share. That is a real quantity, but it is
    NOT ``kpis.stockout_rate``, which the catalogue defines as POINT IN TIME.
    Two different figures under one label is how two screens disagree, so the
    classification refuses the generic path and `levels.COUNT_WHERE_PREFIX`
    remains the way to count flagged rows at one instant.
    """
    with sandbox() as (db, generation):
        rows = []
        for offset in range(4):
            rows.extend(
                _inventory_row(
                    generation=generation,
                    day=_day(offset),
                    index=i,
                    is_oos=(i == 0),
                )
                for i in range(4)
            )
        _seed(db, AggInventoryDaily, rows)

        product_days = AnalyticsRepository(db).fetch_totals(
            INVENTORY,
            columns=["is_oos"],
            window=_window(),
            tz_generation=generation,
        )["is_oos"]
        # One product out of stock on each of four days. Point-in-time stockout
        # count is 1; this is 4, and 4/16 = 25% would be published as a
        # "stockout rate" against the point-in-time 1/4 = 25%... which happens
        # to agree here only because the fixture is uniform. It does not in
        # general, and neither number announces which one it is.
        assert product_days == 4
        assert type(product_days) is int

        with pytest.raises(NonAdditive) as excinfo:
            assert_summable("is_oos", source=INVENTORY)
        assert "level" in str(excinfo.value).lower()
