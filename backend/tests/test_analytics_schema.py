"""SP2 gate — the live analytics schema must match the ORM, and must obey the
four conventions that are cheap to enforce now and expensive to discover later.

Why this test exists at all: the analytics schema ships as TWO artifacts that
can silently diverge — an Alembic revision (applied to CI and local databases)
and a hand-applied SQL file for the shared production MySQL, which is on a
migration lineage this repo does not contain (DEPLOY.md §6). If the hand-written
SQL drifts from the models, production 500s on every analytics call while CI
stays green. This test reflects what the database ACTUALLY has and compares it
to what the ORM says it should have.

It also enforces the four conventions from app/models/analytics_base.py, each of
which corresponds to a real defect class:

  1. No foreign keys       — rollups must stay independently truncatable.
  2. tz_generation in every rollup's unique key — otherwise a reporting-timezone
     change silently mixes buckets computed under two different day boundaries.
  3. No nullable column inside a unique key — MySQL permits MANY NULLs under a
     UNIQUE index, so a nullable dimension defeats the idempotency key and the
     aggregation job double-counts on its next run. This is the subtlest of the
     four and the one most likely to be reintroduced.
  4. No stored averages    — an average of averages is wrong the moment a daily
     bucket is re-bucketed to a week.

Requires a database. Run with the rest of the suite:

    docker compose exec backend pytest tests/test_analytics_schema.py -v
"""
from __future__ import annotations

import pytest
from sqlalchemy import inspect

from app.db.base import Base
from app.db.session import SessionLocal
from app.models import analytics_control, analytics_facts, analytics_rollups

# --------------------------------------------------------------------------
# Collect the analytics tables straight from the ORM modules, so a newly added
# model is covered automatically rather than needing to be listed here.
# --------------------------------------------------------------------------


def _tables(module) -> dict:
    return {
        v.__table__.name: v.__table__
        for v in vars(module).values()
        if hasattr(v, "__table__")
    }


FACT_TABLES = _tables(analytics_facts)
ROLLUP_TABLES = _tables(analytics_rollups)
CONTROL_TABLES = _tables(analytics_control)
ALL_TABLES = {**FACT_TABLES, **ROLLUP_TABLES, **CONTROL_TABLES}


@pytest.fixture(scope="module")
def inspector():
    with SessionLocal() as session:
        yield inspect(session.get_bind())


def test_expected_table_counts():
    """4 facts + 12 rollups + 7 control = 23. A dropped model is a silent
    regression otherwise — the aggregation job would just skip it."""
    assert len(FACT_TABLES) == 4, sorted(FACT_TABLES)
    assert len(ROLLUP_TABLES) == 12, sorted(ROLLUP_TABLES)
    assert len(CONTROL_TABLES) == 7, sorted(CONTROL_TABLES)
    assert len(ALL_TABLES) == 23


@pytest.mark.parametrize("name", sorted(ALL_TABLES))
def test_table_exists_in_database(inspector, name):
    assert inspector.has_table(name), (
        f"{name} is declared in the ORM but missing from the database. "
        "Run `alembic upgrade head` locally, or apply "
        "backend/scripts/sql/2026-07-28_analytics_v2_schema.sql on the shared DB."
    )


@pytest.mark.parametrize("name", sorted(ALL_TABLES))
def test_columns_match_orm(inspector, name):
    """Every ORM column exists in the database with a compatible type.

    Compares the type *family* rather than the exact rendering: MySQL reports
    DECIMAL where SQLAlchemy says NUMERIC, and BIGINT/INTEGER widths render
    differently across versions. A family mismatch (say NUMERIC modelled but
    DOUBLE in the database) is the failure that actually matters, because
    binary floating point silently corrupts money.
    """
    table = ALL_TABLES[name]
    actual = {c["name"]: c for c in inspector.get_columns(name)}

    missing = sorted(set(table.columns.keys()) - set(actual))
    assert not missing, f"{name}: columns in ORM but not in DB: {missing}"

    for col in table.columns:
        db_type = str(actual[col.name]["type"]).upper()
        orm_type = str(col.type).upper()

        def family(t: str) -> str:
            # BOOL must be tested before INT. MySQL has no native boolean —
            # SQLAlchemy's Boolean becomes TINYINT, and MySQL 8.4 no longer
            # reports the historic (1) display width, so the reflected type is
            # a bare "TINYINT". An INT-first check would classify every boolean
            # as an integer and report a spurious mismatch. Nothing in this
            # schema uses TINYINT as a genuine small integer (SmallInteger maps
            # to SMALLINT), so folding TINYINT into BOOL is unambiguous here.
            if "BOOL" in t or "TINYINT" in t:
                return "BOOL"
            for key in ("DECIMAL", "NUMERIC"):
                if key in t:
                    return "NUMERIC"
            for key in ("BIGINT", "SMALLINT", "TINYINT", "INTEGER", "INT"):
                if key in t:
                    return "INT"
            for key in ("VARCHAR", "CHAR", "TEXT"):
                if key in t:
                    return "STRING"
            for key in ("DATETIME", "TIMESTAMP"):
                if key in t:
                    return "DATETIME"
            if "DATE" in t:
                return "DATE"
            if "JSON" in t:
                return "JSON"
            if "FLOAT" in t or "DOUBLE" in t or "REAL" in t:
                return "FLOAT"
            return t

        assert family(db_type) == family(orm_type), (
            f"{name}.{col.name}: ORM says {orm_type}, database has {db_type}"
        )


@pytest.mark.parametrize("name", sorted(ALL_TABLES))
def test_no_float_columns_anywhere(name):
    """Money and margin arithmetic feeds financial reporting. Binary floating
    point cannot represent 0.10, so a FLOAT column here is a correctness bug,
    not a style preference."""
    table = ALL_TABLES[name]
    floats = [
        c.name
        for c in table.columns
        if any(k in str(c.type).upper() for k in ("FLOAT", "DOUBLE", "REAL"))
    ]
    assert not floats, f"{name}: floating-point columns forbidden: {floats}"


@pytest.mark.parametrize("name", sorted(ALL_TABLES))
def test_no_foreign_keys(inspector, name):
    """Convention 1. Rollups must be independently truncatable and rebuildable;
    an FK to products would turn 'recompute March' into a referential-integrity
    problem and would block a product delete. Identity is snapshotted into the
    fact row instead, so history survives a deleted product."""
    assert not ALL_TABLES[name].foreign_keys, f"{name}: ORM declares a foreign key"
    assert not inspector.get_foreign_keys(name), f"{name}: database has a foreign key"


@pytest.mark.parametrize("name", sorted(ROLLUP_TABLES))
def test_rollup_unique_key_includes_tz_generation(name):
    """Convention 2. Reporting days are computed in the store's timezone.
    Without tz_generation in the idempotency key, changing that timezone lets
    rows bucketed under two different day boundaries collide in one key — which
    corrupts history invisibly and cannot be untangled afterwards."""
    from sqlalchemy import UniqueConstraint

    table = ROLLUP_TABLES[name]
    uniques = [c for c in table.constraints if isinstance(c, UniqueConstraint)]
    assert len(uniques) == 1, f"{name}: expected exactly one UNIQUE key, got {len(uniques)}"
    cols = [c.name for c in uniques[0].columns]
    assert "tz_generation" in cols, f"{name}: unique key {cols} omits tz_generation"


@pytest.mark.parametrize("name", sorted(ALL_TABLES))
def test_no_nullable_column_inside_a_unique_key(name):
    """Convention 3, and the subtlest of the four.

    MySQL permits MANY rows with NULL in a UNIQUE-indexed column. A nullable
    `gateway` or `courier` therefore does NOT enforce one-row-per-bucket, so the
    aggregation job's ON DUPLICATE KEY UPDATE never fires and the next run
    inserts a second row — silently doubling that bucket's numbers. Writers
    COALESCE to the '-' sentinel precisely so this can't happen.
    """
    from sqlalchemy import UniqueConstraint

    table = ALL_TABLES[name]
    offenders = [
        (u.name, c.name)
        for u in table.constraints
        if isinstance(u, UniqueConstraint)
        for c in u.columns
        if c.nullable
    ]
    assert not offenders, (
        f"{name}: nullable column(s) inside a UNIQUE key {offenders}. "
        "MySQL allows many NULLs under UNIQUE, so this does not enforce "
        "uniqueness and the aggregation job will double-count."
    )


@pytest.mark.parametrize("name", sorted(ROLLUP_TABLES))
def test_no_stored_averages(name):
    """Convention 4. Store sum + count and divide at query time; an average of
    averages is wrong as soon as daily buckets are re-bucketed to a week.

    agg_customer_snapshot.aov is the one deliberate exception — the snapshot row
    IS the grain and is never re-bucketed, and it is the segmentation sort key.
    It is documented at the column as derived: never SUM it, never AVG it across
    rows; a cross-customer AOV is SUM(gross_ltv) / SUM(orders_count).
    """
    allowed = {("agg_customer_snapshot", "aov")}
    table = ROLLUP_TABLES[name]
    offenders = [
        c.name
        for c in table.columns
        if (c.name.startswith("avg_") or c.name in {"aov", "average"})
        and (name, c.name) not in allowed
    ]
    assert not offenders, f"{name}: stored averages forbidden: {offenders}"


@pytest.mark.parametrize("name", sorted(ROLLUP_TABLES))
def test_every_duration_sum_has_a_matching_count(name):
    """A `sum_*_seconds` column without its `n_*` partner cannot be turned back
    into an average at any granularity, which defeats the point of storing the
    sum in the first place."""
    table = ROLLUP_TABLES[name]
    cols = set(table.columns.keys())
    for col in sorted(c for c in cols if c.startswith("sum_") and c.endswith("_seconds")):
        stem = col[len("sum_") : -len("_seconds")]
        assert f"n_{stem}" in cols, (
            f"{name}.{col} has no matching n_{stem} counter — the average it "
            "feeds could never be recomputed correctly."
        )


@pytest.mark.parametrize("name", sorted(ALL_TABLES))
def test_indexes_present_in_database(inspector, name):
    """Every index the ORM declares actually exists. A missing index is not a
    correctness bug but it is a production incident waiting to happen: these
    tables are scanned by date range on every dashboard request."""
    table = ALL_TABLES[name]
    declared = {ix.name for ix in table.indexes}
    actual = {ix["name"] for ix in inspector.get_indexes(name)}
    missing = sorted(declared - actual)
    assert not missing, f"{name}: indexes declared in ORM but absent from DB: {missing}"


def test_analytics_tables_are_registered_for_autogenerate():
    """app/db/base.py must import every analytics model. If it doesn't, Alembic
    autogenerate cannot see the table and will happily propose dropping it."""
    registered = set(Base.metadata.tables)
    missing = sorted(set(ALL_TABLES) - registered)
    assert not missing, (
        f"not imported in app/db/base.py: {missing} — Alembic would propose "
        "dropping these on the next autogenerate."
    )
