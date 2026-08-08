"""``agg_inventory_daily.reorder_gap``: three values, three different statements.

``products.reorder_point`` exists now, so ``InventoryDailyJob`` computes
``reorder_gap = stock_close - reorder_point`` instead of writing NULL
unconditionally. The whole risk of that change is one line of arithmetic and one
temptation, and every test here exists for the temptation rather than the
arithmetic.

What actually has to be true
----------------------------
``COALESCE(reorder_point, 0)`` is the shape this job would take if somebody
wanted the column to be "always a number". It does not raise, it does not draw a
blank chart, and it does not look wrong: it fills ``reorder_gap`` for the entire
catalogue with ``stock_close - 0``, i.e. the stock level itself, and every
product with no configured threshold is then filed as sitting at or above its
reorder point. On a view called "Needs attention" that reads as good news. So:

  * ``test_gap_is_negative_when_stock_is_below_the_reorder_point`` — 4 units
    against a threshold of 10 is -6, not 0 and not 6. The sign carries the
    signal and the magnitude carries the urgency; a clamp at zero would flatten
    "one short" and "two hundred short" into the same row.
  * ``test_reorder_point_zero_is_measured_not_treated_as_missing`` — a product
    configured to reorder at an empty shelf, holding 7 units, has a gap of 7.
    **NOT NULL.** ``if not reorder_point`` and ``reorder_point or None`` both
    pass every other test in this file and fail this one, because 0 is falsey
    and "reorder at empty" is a real, entered configuration.
  * ``test_unconfigured_reorder_point_stays_null`` — NULL in, NULL out. 0 here
    would assert "reorder at zero stock, and we are exactly there", which is a
    claim nobody entered.
  * ``test_rerunning_the_bucket_does_not_flip_null_to_zero`` — Pattern B deletes
    and reinserts the bucket, so a second run is a second chance to normalise
    the NULL away. It must not.
  * ``test_configured_and_unconfigured_products_share_a_bucket`` — the two
    semantics in one INSERT chunk, which is where a per-row branch written as a
    per-batch default would collapse them.
  * ``test_low_stock_view_still_lists_every_product_lowest_first`` — a
    regression pin. View 29 lists EVERY product ordered by stock, because the
    repository filters on equality and a threshold needs an inequality. A
    populated ``reorder_gap`` does not change that, and this test fails if the
    view quietly starts filtering on it.
  * ``test_reorder_gap_is_a_level_and_is_refused_for_summing`` — the
    classification the column always had, pinned now that the column finally
    carries values worth summing by mistake. ``SUM`` over it skips NULLs and
    totals the rest into a number with no unit.

Isolation strategy
------------------
House style: no db fixture in ``conftest.py``; every test owns its
``SessionLocal()`` and tears down in a ``finally``.

  1. Fixtures live in **1983** — a decade no other analytics suite writes to
     (2001, 2004, 2005, 2008, 2009, 2010-2016, 1997 and 1982 are all taken) and
     a year before this store's first order. Never 2026, and never a ``PERF-``
     row.
  2. Every product this module creates is prefixed ``TESTRG-`` and is deleted by
     id in ``finally``, along with its ledger movements and the rollup rows for
     the 1983 window.
  3. ``InventoryDailyJob`` is FORWARD-ONLY: it refuses any bucket earlier than
     ``analytics.inventory_history_since``. That marker is a single global
     settings row, so the sandbox points it at 1983-01-01 on entry and **puts
     back exactly what it found** — value, or absence — on exit. Deleting it
     outright (which is what ``test_analytics_aggregation_jobs2`` does, because
     that suite is testing the first-ever run) would move another suite's start
     of history.
  4. ``_levels`` only includes ledger-covered products once the bucket's day has
     closed, and 1983 has certainly closed. Every product here therefore carries
     an ``INITIAL_SEED`` movement inside its bucket, which is also why no other
     suite's products can appear in these buckets: their movements are all dated
     after 1983 and the ledger sum is taken over ``occurred_at < end``.
  5. Each test uses its own bucket date, so one test's ``days_oos`` carry-over
     and rollup rows cannot reach another's.

Run inside the analytics container::

    docker exec wvana-py python -m pytest tests/test_analytics_reorder_gap.py -q
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Iterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.analytics_facts import InventoryMovement, MovementType
from app.models.analytics_rollups import AggInventoryDaily
from app.models.product import Product
from app.models.system_setting import SystemSetting
from app.schemas.analytics_view import AnalyticsViewEnvelope
from app.services.analytics import registry
from app.services.analytics.aggregation import AggregationRunner
from app.services.analytics.aggregation.jobs_ops import INVENTORY_HISTORY_SINCE_KEY
from app.services.analytics.filters import AnalyticsFilters, Comparison, Period
from app.services.analytics.metric_kind import (
    MetricKind,
    NonAdditive,
    assert_summable,
    classify,
)
from app.services.analytics.timebox import active_generation, day_bounds_utc, store_timezone
from app.services.analytics.view_service import AnalyticsViewService

# ---------------------------------------------------------------------------
# The 1983 sandbox
# ---------------------------------------------------------------------------

SANDBOX_FIRST = date(1983, 1, 1)
SANDBOX_LAST = date(1983, 12, 31)

#: One bucket per test, so no test can inherit another's `days_oos` carry-over.
DAY_BELOW = date(1983, 6, 5)
DAY_AT_ZERO = date(1983, 6, 6)
DAY_UNSET = date(1983, 6, 7)
DAY_RERUN = date(1983, 6, 8)
DAY_MIXED = date(1983, 6, 9)

#: The view-29 pin gets its own month so its window cannot see another test's
#: leftovers even if that test crashed before its teardown ran.
DAY_VIEW_ONE = date(1983, 9, 5)
DAY_VIEW_TWO = date(1983, 9, 6)
VIEW_WINDOW_FROM = date(1983, 9, 1)
VIEW_WINDOW_TO = date(1983, 9, 10)

LOW_STOCK_VIEW = (29, "inventory", "low-stock-and-out-of-stock")

#: Distinctive enough that a stuck run row in the shared throwaway MySQL is
#: traceable back to this module.
WORKER_ID = "test-reorder-gap"

#: Every product this module creates. Teardown deletes by id, but the prefix is
#: what makes a leaked row identifiable and is what the entry sweep looks for.
SKU_PREFIX = "TESTRG-"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _at(db: Session, day: date, hour: int) -> datetime:
    """The UTC instant that is ``hour:00`` **store-local** on ``day``.

    Derived from the same `timebox` helper the job buckets with, so a movement
    lands in the bucket under test rather than in the one 5.5 hours away.
    """
    start, _ = day_bounds_utc(day, store_timezone(db))
    return start + timedelta(hours=hour)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _Owned:
    """Ids this module created, so teardown removes exactly them."""

    def __init__(self) -> None:
        self.products: list[int] = []
        self.movements: list[int] = []


def _seed_product(
    db: Session,
    owned: _Owned,
    *,
    day: date,
    stock: int,
    reorder_point: int | None,
    cost: str | None = "40.00",
) -> Product:
    """A product whose closing level on ``day`` is exactly ``stock``.

    The level comes from an ``INITIAL_SEED`` ledger movement rather than from
    `products.stock`: for a day that has already closed — and 1983 has — the job
    refuses today's mutable integer and reads the ledger, which is the whole
    point of that refusal. ``INITIAL_SEED`` is neither a sale nor a restock, so
    both flow columns stay 0 and the only column moving here is the one under
    test.
    """
    product = Product(
        sku=f"{SKU_PREFIX}{_uid()}",
        name=f"Reorder Gap Fixture {_uid()}",
        price=Decimal("500.00"),
        cost=None if cost is None else Decimal(cost),
        # Deliberately NOT the seeded level. If the job ever falls back to this
        # mutable integer for a closed day, `stock_close` — and with it
        # `reorder_gap` — comes out at 999 and the test says so.
        stock=999,
        reorder_point=reorder_point,
    )
    db.add(product)
    db.flush()
    owned.products.append(product.id)

    movement = InventoryMovement(
        product_id=product.id,
        occurred_at=_at(db, day, 2),
        movement_type=MovementType.INITIAL_SEED,
        delta=stock,
        event_key=f"reorder-gap:{_uid()}:{product.id}",
    )
    db.add(movement)
    db.flush()
    owned.movements.append(movement.id)
    db.commit()
    return product


# ---------------------------------------------------------------------------
# Sandbox: the forward-only marker, and unconditional teardown
# ---------------------------------------------------------------------------


def _sweep(owned: _Owned | None) -> None:
    """Delete this module's rows through a fresh session.

    Fresh so teardown cannot be skipped by a half-rolled-back transaction in the
    test's own session. Rollup rows go by sandbox date range because they are
    written by Core statements whose ids the test never sees; products and
    movements go by id when this run owns them, and by SKU prefix otherwise —
    the prefix sweep is what cleans up after a run that crashed before its
    ``finally``.
    """
    with SessionLocal() as session:
        if owned is not None and owned.movements:
            session.execute(
                text("DELETE FROM inventory_movements WHERE id IN :ids"),
                {"ids": tuple(owned.movements)},
            )
        orphan_products = session.execute(
            select(Product.id).where(Product.sku.like(f"{SKU_PREFIX}%"))
        ).scalars().all()
        product_ids = sorted(
            {*(owned.products if owned is not None else []), *map(int, orphan_products)}
        )
        if product_ids:
            session.execute(
                text("DELETE FROM inventory_movements WHERE product_id IN :ids"),
                {"ids": tuple(product_ids)},
            )
            session.execute(
                text("DELETE FROM products WHERE id IN :ids"),
                {"ids": tuple(product_ids)},
            )
        session.execute(
            text(
                "DELETE FROM agg_inventory_daily "
                "WHERE bucket_date BETWEEN :first AND :last"
            ),
            {"first": SANDBOX_FIRST, "last": SANDBOX_LAST},
        )
        session.execute(
            text(
                "DELETE FROM analytics_recompute_queue "
                "WHERE bucket_date BETWEEN :first AND :last"
            ),
            {"first": SANDBOX_FIRST, "last": SANDBOX_LAST},
        )
        session.execute(
            text("DELETE FROM analytics_sync_runs WHERE worker_id = :worker"),
            {"worker": WORKER_ID},
        )
        session.commit()


@contextmanager
def _history_since_pointed_at_1983() -> Iterator[None]:
    """Point the forward-only marker at 1983 and put back exactly what was there.

    ``InventoryDailyJob`` refuses any bucket before
    ``analytics.inventory_history_since``, and that marker is one global row, not
    a per-test one. Deleting it — which ``test_analytics_aggregation_jobs2`` does
    on purpose, because that suite asserts what the FIRST ever run does — would
    hand the next suite a different start of history than it had. So this saves
    the row's exact prior state (value, or absence) and restores it, and it also
    stops the job writing the marker itself: with a value already present,
    ``_set_history_since`` never runs.
    """
    with SessionLocal() as session:
        row = session.execute(
            select(SystemSetting).where(SystemSetting.key == INVENTORY_HISTORY_SINCE_KEY)
        ).scalars().first()
        existed = row is not None
        previous = row.value if row is not None else None
        if row is None:
            session.add(
                SystemSetting(
                    key=INVENTORY_HISTORY_SINCE_KEY,
                    value=SANDBOX_FIRST.isoformat(),
                    category="store",
                    description="Set by tests/test_analytics_reorder_gap.py; removed on exit.",
                )
            )
        else:
            row.value = SANDBOX_FIRST.isoformat()
        session.commit()
    try:
        yield
    finally:
        with SessionLocal() as session:
            if existed:
                session.execute(
                    text("UPDATE system_settings SET value = :v WHERE `key` = :k"),
                    {"v": previous, "k": INVENTORY_HISTORY_SINCE_KEY},
                )
            else:
                session.execute(
                    text("DELETE FROM system_settings WHERE `key` = :k"),
                    {"k": INVENTORY_HISTORY_SINCE_KEY},
                )
            session.commit()


@contextmanager
def sandbox() -> Iterator[tuple[Session, _Owned, int]]:
    """A session, an ownership ledger and the active generation.

    Sweeps on entry as well as exit so a crashed previous run cannot poison this
    one — a leftover `TESTRG-` product still carrying a ledger movement in 1983
    would join every bucket here.
    """
    _sweep(None)
    db = SessionLocal()
    owned = _Owned()
    try:
        with _history_since_pointed_at_1983():
            yield db, owned, int(active_generation(db).generation)
    finally:
        try:
            db.rollback()
        finally:
            db.close()
            _sweep(owned)


def _run(db: Session, bucket: date) -> None:
    AggregationRunner(db, worker_id=WORKER_ID).run_bucket("inventory_daily", bucket)


def _row(db: Session, product_id: int, bucket: date, generation: int) -> AggInventoryDaily:
    db.expire_all()
    row = db.execute(
        select(AggInventoryDaily).where(
            AggInventoryDaily.bucket_date == bucket,
            AggInventoryDaily.tz_generation == generation,
            AggInventoryDaily.product_id == product_id,
        )
    ).scalars().first()
    assert row is not None, (
        f"product {product_id} has no agg_inventory_daily row for {bucket}. The job "
        "only covers products the ledger reaches on a day that has closed — check "
        "the INITIAL_SEED movement landed inside the bucket."
    )
    return row


# ===========================================================================
# 1. Configured and below: the signal is the sign
# ===========================================================================


def test_gap_is_negative_when_stock_is_below_the_reorder_point() -> None:
    """4 units against a threshold of 10 is -6.

    Not 0 (a clamp, which throws away how far below the shelf has fallen and
    makes "one short" and "two hundred short" the same row) and not 6 (the
    operands the wrong way round, which reports a shortfall as a surplus of the
    same size — a sign error nobody re-reads a dashboard to catch).
    """
    with sandbox() as (db, owned, generation):
        product = _seed_product(db, owned, day=DAY_BELOW, stock=4, reorder_point=10)

        _run(db, DAY_BELOW)

        row = _row(db, product.id, DAY_BELOW, generation)
        assert row.stock_close == 4, (
            "the level must come from the ledger, not from products.stock (999)"
        )
        assert row.reorder_gap == -6, (
            f"stock_close(4) - reorder_point(10) is -6; got {row.reorder_gap!r}"
        )
        assert row.reorder_gap != 0, "clamping at zero discards the depth of the shortfall"
        assert row.reorder_gap != 6, "the operands are the wrong way round"


# ===========================================================================
# 2. Configured at zero: falsey is not missing
# ===========================================================================


def test_reorder_point_zero_is_measured_not_treated_as_missing() -> None:
    """The distinction the whole column is built on, from the 0 side.

    ``reorder_point = 0`` is a real, entered configuration: "reorder only once
    the shelf is empty". With 7 units on hand the gap is 7. Every falsey test —
    ``if not reorder_point``, ``reorder_point or None``, ``if reorder_point:`` —
    passes the NULL case, passes the negative case, and turns this row's answer
    into NULL, which reads on every downstream view as "nobody configured this
    product" when somebody did.
    """
    with sandbox() as (db, owned, generation):
        product = _seed_product(db, owned, day=DAY_AT_ZERO, stock=7, reorder_point=0)

        _run(db, DAY_AT_ZERO)

        row = _row(db, product.id, DAY_AT_ZERO, generation)
        assert row.reorder_gap is not None, (
            "reorder_point=0 is configured, not missing; NULL here loses the "
            "configuration behind a falsey check"
        )
        assert row.reorder_gap == 7, (
            f"stock_close(7) - reorder_point(0) is 7; got {row.reorder_gap!r}"
        )


# ===========================================================================
# 3. Unconfigured: NULL in, NULL out
# ===========================================================================


def test_unconfigured_reorder_point_stays_null() -> None:
    """No threshold configured means no gap to report — not a gap of zero.

    ``COALESCE(reorder_point, 0)`` fills this row with ``stock_close`` and files
    the product as comfortably above a threshold it does not have. Nothing
    raises; the "needs attention" list just gets quieter.
    """
    with sandbox() as (db, owned, generation):
        product = _seed_product(db, owned, day=DAY_UNSET, stock=12, reorder_point=None)

        _run(db, DAY_UNSET)

        row = _row(db, product.id, DAY_UNSET, generation)
        assert row.reorder_gap is None, (
            f"no reorder point is configured, so the gap is unknown; got "
            f"{row.reorder_gap!r}"
        )
        assert row.reorder_gap != 0, (
            "0 asserts 'reorder at zero stock, and we are exactly there' — a claim "
            "nobody entered"
        )
        assert row.reorder_gap != row.stock_close, (
            "COALESCE(reorder_point, 0) makes the gap equal the stock level"
        )


# ===========================================================================
# 4. Idempotence: a rerun is a second chance to normalise the NULL away
# ===========================================================================


def test_rerunning_the_bucket_does_not_flip_null_to_zero() -> None:
    """Pattern B deletes the bucket and reinserts it, so every run recomputes.

    A default applied on the INSERT path rather than in the computation would
    show up here and nowhere else: the first run writes NULL, the second writes
    0, and the column's meaning changes on a schedule nobody is watching. The
    configured row is rerun alongside it so "identical" cannot be "identically
    NULL because the job did nothing".
    """
    with sandbox() as (db, owned, generation):
        unset = _seed_product(db, owned, day=DAY_RERUN, stock=15, reorder_point=None)
        configured = _seed_product(db, owned, day=DAY_RERUN, stock=3, reorder_point=20)

        _run(db, DAY_RERUN)
        first = {
            unset.id: _row(db, unset.id, DAY_RERUN, generation).reorder_gap,
            configured.id: _row(db, configured.id, DAY_RERUN, generation).reorder_gap,
        }

        _run(db, DAY_RERUN)
        second = {
            unset.id: _row(db, unset.id, DAY_RERUN, generation).reorder_gap,
            configured.id: _row(db, configured.id, DAY_RERUN, generation).reorder_gap,
        }

        assert first == {unset.id: None, configured.id: -17}, (
            f"the first run got {first!r}; expected NULL for the unconfigured "
            "product and 3 - 20 = -17 for the configured one"
        )
        assert second == first, (
            f"re-running the bucket changed reorder_gap. first={first} second={second}"
        )
        assert second[unset.id] is None, "a rerun must not normalise NULL into 0"
        assert second[configured.id] is not None, "and must not normalise a value into NULL"


# ===========================================================================
# 5. Both semantics inside one bucket
# ===========================================================================


def test_configured_and_unconfigured_products_share_a_bucket() -> None:
    """Two products, one INSERT, two different statements about the same day.

    The rows are written in chunks by a single ``INSERT ... VALUES`` per chunk,
    so a decision made once per batch rather than once per row collapses both
    products onto whichever answer was computed first. Asserted as a pair, not
    as two independent tests, because it is the coexistence that is at risk.
    """
    with sandbox() as (db, owned, generation):
        configured = _seed_product(db, owned, day=DAY_MIXED, stock=2, reorder_point=5)
        unconfigured = _seed_product(db, owned, day=DAY_MIXED, stock=2, reorder_point=None)

        _run(db, DAY_MIXED)

        configured_row = _row(db, configured.id, DAY_MIXED, generation)
        unconfigured_row = _row(db, unconfigured.id, DAY_MIXED, generation)

        assert configured_row.stock_close == unconfigured_row.stock_close == 2, (
            "the two products are deliberately identical except for the threshold, "
            "so the only column that may differ is reorder_gap"
        )
        assert configured_row.reorder_gap == -3, (
            f"2 - 5 = -3; got {configured_row.reorder_gap!r}"
        )
        assert unconfigured_row.reorder_gap is None, (
            f"no threshold configured, so no gap; got {unconfigured_row.reorder_gap!r}"
        )


# ===========================================================================
# 6. Regression pin: view 29 behaves exactly as it did
# ===========================================================================


class _InventoryReader:
    """Exactly the permission view 29 needs.

    `AnalyticsViewService` only ever calls `has_permission`, so this exercises
    the real authorisation path without creating a user row — and without the
    shared admin account, whose `is_admin` short circuit would make the check
    pass for the wrong reason.
    """

    is_admin = False

    def __init__(self, permissions: set[str]) -> None:
        self._permissions = permissions

    def has_permission(self, permission: str) -> bool:
        return permission in self._permissions


def test_low_stock_view_still_lists_every_product_lowest_first() -> None:
    """Populating `reorder_gap` must not change what view 29 shows.

    The view's own comment is explicit that it lists EVERY product, lowest stock
    first, rather than only those below their reorder point — the repository
    filters on equality and a threshold needs an inequality. Now that the column
    finally carries values, the tempting next step is to filter on it, which
    would silently drop every unconfigured product (the majority of the
    catalogue) off a list headed "Needs attention". Three products here: one
    below its threshold, one above, one with no threshold at all. All three must
    appear, ordered by stock ascending, at the LATEST ledger day in the window.
    """
    _number, module_slug, view_slug = LOW_STOCK_VIEW
    view = registry.get_view(module_slug, view_slug)
    assert view is not None, f"{module_slug}/{view_slug} is not in the registry"

    with sandbox() as (db, owned, generation):
        # Day one exists so "the latest ledger day" is a real choice rather than
        # the only one: `below`'s stock falls from 40 to 1 between the days.
        below = _seed_product(db, owned, day=DAY_VIEW_ONE, stock=40, reorder_point=10)
        above = _seed_product(db, owned, day=DAY_VIEW_ONE, stock=90, reorder_point=10)
        unconfigured = _seed_product(
            db, owned, day=DAY_VIEW_ONE, stock=5, reorder_point=None
        )
        movement = InventoryMovement(
            product_id=below.id,
            occurred_at=_at(db, DAY_VIEW_TWO, 3),
            movement_type=MovementType.COMMIT_SALE,
            delta=-39,
            event_key=f"reorder-gap:{_uid()}:{below.id}",
        )
        db.add(movement)
        db.flush()
        owned.movements.append(movement.id)
        db.commit()

        _run(db, DAY_VIEW_ONE)
        _run(db, DAY_VIEW_TWO)

        # The gaps are real on the rollup rows...
        assert _row(db, below.id, DAY_VIEW_TWO, generation).reorder_gap == -9
        assert _row(db, above.id, DAY_VIEW_TWO, generation).reorder_gap == 80
        assert _row(db, unconfigured.id, DAY_VIEW_TWO, generation).reorder_gap is None

        # ...and the view is unmoved by them.
        service = AnalyticsViewService(db, _InventoryReader({view.permission}))
        envelope = service.resolve_view(
            module_slug,
            view_slug,
            AnalyticsFilters(
                period=Period.CUSTOM,
                date_from=VIEW_WINDOW_FROM,
                date_to=VIEW_WINDOW_TO,
                comparison=Comparison.NONE,
            ),
            use_cache=False,
        )
        assert isinstance(envelope, AnalyticsViewEnvelope), (
            f"view 29 returned {type(envelope).__name__}; it is LIVE and must carry data"
        )

        rows = envelope.tables["low_stock_table"].rows
        listed = {int(r["product"]) for r in rows}
        assert listed == {below.id, above.id, unconfigured.id}, (
            f"view 29 lists every product in the window; got {sorted(listed)} for "
            f"{sorted((below.id, above.id, unconfigured.id))}. A product missing here "
            "means the view started filtering on reorder_gap — which would hide every "
            "unconfigured SKU from a list headed 'Needs attention'."
        )
        assert len(rows) == 3, (
            f"one row per product at one instant, not per product per day; got {len(rows)}"
        )
        assert {r["as_at"] for r in rows} == {DAY_VIEW_TWO.isoformat()}, (
            "the snapshot resolver pins the newest ledger day inside the window"
        )

        by_product = {int(r["product"]): r["stock"] for r in rows}
        assert by_product[below.id] == Decimal("1"), "40 seeded - 39 sold on day two"
        assert by_product[above.id] == Decimal("90")
        assert by_product[unconfigured.id] == Decimal("5")
        assert [r["stock"] for r in rows] == sorted(r["stock"] for r in rows), (
            "'Needs attention' is ranked emptiest shelf first (breakdown_sort), not by "
            "the default top-N descending"
        )
        assert all("reorder_gap" not in row for row in rows), (
            "the table's columns are declared in the registry and this change adds "
            "none; a reorder_gap column appearing here is a contract change (the "
            "frontend renders the registry's column list), not a wiring change"
        )


# ===========================================================================
# 7. Still a LEVEL, and still refused for summing
# ===========================================================================


def test_reorder_gap_is_a_level_and_is_refused_for_summing() -> None:
    """The classification was written when the column was always NULL.

    A SUM over it used to return ``None`` for every window, which is obviously
    broken and could not have shipped. Now that some rows carry values, the same
    SUM returns a believable integer that skips the NULLs — a total of "the
    configured subset" under a heading that implies the whole catalogue, in a
    unit (units-below-threshold summed across days) that means nothing. The
    classification is the only thing standing between a generic resolver and
    that number, so it is pinned here rather than left to the file that declares
    it.
    """
    source = "agg_inventory_daily"

    assert classify("reorder_gap", source=source) is MetricKind.LEVEL, (
        "reorder_gap is stock_close - reorder_point as of one date: a difference of "
        "two levels, and therefore a level"
    )
    assert classify("reorder_gap") is MetricKind.LEVEL, (
        "the classification must not depend on the caller passing a source — a "
        "resolver that omits it would get FLOW and sum the column"
    )
    with pytest.raises(NonAdditive):
        assert_summable("reorder_gap", source=source)
    with pytest.raises(NonAdditive):
        assert_summable("reorder_gap")
