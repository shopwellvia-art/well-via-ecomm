"""View 30 (Inventory Turnover) — the arithmetic, pinned by hand.

The formula under test, exactly as `resolvers/turnover.py` binds it::

    turnover          = COGS in window / average inventory value over window
    days_of_inventory = days in window / turnover     (None at 0 or unknown)

with the denominator computed through the repository's ``avg:`` projection —
per product, the mean of ``stock_value_close`` over the ledger days that EXIST
(a gap day is in neither numerator nor denominator), the store figure being the
sum of those per-product means. Every test here states its arithmetic in full,
because a turnover figure has three plausible wrong constructions (a zero-filled
average, an end-of-window level, a units-over-stock ratio) and the only defence
is a fixture where each wrong construction produces a DIFFERENT number that the
test rejects by name.

Isolation strategy
------------------
House style, following ``test_analytics_view_bindings.py``: no db fixture in
``conftest.py``; the module owns its ``SessionLocal()`` and tears down in a
``finally``. Fixtures are written under the database's **active** generation,
so isolation comes from the date range instead: **March 1975**, a sandbox no
other suite uses (1974, 1990, 1996-1999, 2001-2016, 2018, 2019, 2021, 2024 and
2026 are all taken, and the live demo's PERF- rows live in 2026-05..07 — this
suite touches none of them). Rows are deleted by ``(generation, bucket_date
range)`` both before seeding and in ``finally``, so a rerun after a crashed
run is clean.

Run inside the analytics container::

    docker exec wvana-py python -m pytest tests/test_analytics_turnover.py -q
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from typing import Callable, Iterator

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import analytics_rollups as rollups
from app.schemas.analytics_view import AnalyticsViewEnvelope, WarningCode
from app.services.analytics import registry
from app.services.analytics.filters import AnalyticsFilters, Comparison, Period
from app.services.analytics.resolvers.base import NOT_CONFIGURED
from app.services.analytics.resolvers.levels import DIMENSION_NOT_STORED
from app.services.analytics.resolvers.turnover import AVG_OVER_OBSERVED_DAYS
from app.services.analytics.timebox import active_generation
from app.services.analytics.types import ViewState
from app.services.analytics.view_service import AnalyticsViewService

# ---------------------------------------------------------------------------
# Sandbox — March 1975
# ---------------------------------------------------------------------------

SANDBOX_START = date(1975, 1, 1)
SANDBOX_END = date(1975, 12, 31)

DAY_ONE = date(1975, 3, 1)
DAY_TWO = date(1975, 3, 2)
DAY_THREE = date(1975, 3, 3)
#: Half-open: the window is [Mar 1, Mar 4), i.e. exactly the three seeded days.
WINDOW_END = date(1975, 3, 4)
DAYS_IN_WINDOW = 3

PRODUCT_A = 97001
PRODUCT_B = 97002

VIEW_30 = ("inventory", "inventory-turnover")

_OWNED_MODELS = (
    rollups.AggInventoryDaily,
    rollups.AggProductDaily,
    rollups.AggOrderDaily,
)


class _AnalyticsReader:
    """Exactly the permission view 30 needs, and nothing else.

    `AnalyticsViewService` only ever calls `has_permission`, so this exercises
    the real authorisation path without creating a user row.
    """

    is_admin = False

    def has_permission(self, permission: str) -> bool:
        return permission == "analytics.inventory.view"


def _clear(db: Session, generation: int) -> None:
    for model in _OWNED_MODELS:
        db.execute(
            delete(model).where(
                model.tz_generation == generation,
                model.bucket_date >= SANDBOX_START,
                model.bucket_date <= SANDBOX_END,
            )
        )
    db.commit()


@contextmanager
def sandbox(seed: Callable[[Session, int], None]) -> Iterator[tuple[Session, int]]:
    """A session plus one seeded 1975 window, deleted unconditionally."""
    db = SessionLocal()
    generation = int(active_generation(db).generation)
    try:
        _clear(db, generation)
        seed(db, generation)
        db.commit()
        yield db, generation
    finally:
        try:
            db.rollback()
            _clear(db, generation)
        finally:
            db.close()


def _stock(
    db: Session,
    generation: int,
    day: date,
    product_id: int,
    *,
    value: str,
    sold: int,
    restocked: int = 0,
) -> None:
    stock_value = Decimal(value)
    db.add(
        rollups.AggInventoryDaily(
            bucket_date=day,
            tz_generation=generation,
            product_id=product_id,
            sku_snapshot=f"SKU-{product_id}",
            # Units are value/10 so every fixture number stays round; the
            # resolver never reads `stock_close` for the maths.
            stock_close=int(stock_value / 10),
            stock_value_close=stock_value,
            units_sold=sold,
            units_restocked=restocked,
            is_oos=stock_value == 0,
            days_oos=1 if stock_value == 0 else 0,
        )
    )


def _product_day(
    db: Session,
    generation: int,
    day: date,
    product_id: int,
    *,
    units: int,
    costed_units: int,
    line_cost: str,
) -> None:
    db.add(
        rollups.AggProductDaily(
            bucket_date=day,
            tz_generation=generation,
            product_id=product_id,
            sku_snapshot=f"SKU-{product_id}",
            category_id_snapshot=None,
            units=units,
            orders=max(units, 1),
            gross_merchandise_sales=Decimal(line_cost) * 2,
            net_merchandise_sales=Decimal(line_cost) * 2,
            line_cost=Decimal(line_cost),
            costed_units=costed_units,
            returned_units=0,
            returned_value=Decimal("0"),
        )
    )


def _order_day(
    db: Session,
    generation: int,
    day: date,
    *,
    units: int,
    costed_units: int,
    cogs: str,
) -> None:
    value = Decimal(cogs) * 2
    db.add(
        rollups.AggOrderDaily(
            bucket_date=day,
            tz_generation=generation,
            orders_total=max(units, 1),
            orders_paid=max(units, 1),
            order_value_created=value,
            paid_order_value=value,
            gross_merchandise_sales=value,
            net_merchandise_sales=value,
            net_revenue=value,
            subtotal_sum=value,
            cogs_sum=Decimal(cogs),
            units=units,
            costed_units=costed_units,
            distinct_customers=1,
            new_customers=1,
        )
    )


def _filters() -> AnalyticsFilters:
    return AnalyticsFilters(
        period=Period.CUSTOM,
        date_from=DAY_ONE,
        date_to=WINDOW_END,
        comparison=Comparison.NONE,
    )


def _resolve(db: Session) -> AnalyticsViewEnvelope:
    envelope = AnalyticsViewService(db, _AnalyticsReader()).resolve_view(
        *VIEW_30, _filters(), use_cache=False
    )
    assert isinstance(envelope, AnalyticsViewEnvelope), (
        "view 30 is PARTIAL and must resolve to a data envelope, not a gated one"
    )
    return envelope


# ---------------------------------------------------------------------------
# Seeds
# ---------------------------------------------------------------------------


def _seed_hand_computed(db: Session, generation: int) -> None:
    """Two products, three complete ledger days. Every figure is checkable by eye.

    Denominator (mean of the level over the days each product has rows)::

        A: (100 + 150 + 200) / 3 = 150
        B: ( 50 +  50 +  50) / 3 =  50
        average inventory value  = 150 + 50 = 200

    Numerator (COGS on the unit_cost-snapshot basis)::

        agg_order_daily.cogs_sum: 30 + 30 + 40 = 100, over 10 units all costed
        per product (agg_product_daily.line_cost): A = 60, B = 40

    So::

        turnover          = 100 / 200 = 0.5
        days_of_inventory = 3 / 0.5   = 6
        per product       : A = 60/150 = 0.4 (slower), B = 40/50 = 0.8
        stock_value       = level at Mar 3 = 200 + 50 = 250
    """
    for day, values in (
        (DAY_ONE, ("100", "50")),
        (DAY_TWO, ("150", "50")),
        (DAY_THREE, ("200", "50")),
    ):
        _stock(db, generation, day, PRODUCT_A, value=values[0], sold=2)
        _stock(db, generation, day, PRODUCT_B, value=values[1], sold=2 if day is DAY_ONE else 1)

    _product_day(db, generation, DAY_TWO, PRODUCT_A, units=6, costed_units=6, line_cost="60")
    _product_day(db, generation, DAY_TWO, PRODUCT_B, units=4, costed_units=4, line_cost="40")

    _order_day(db, generation, DAY_ONE, units=4, costed_units=4, cogs="30")
    _order_day(db, generation, DAY_TWO, units=3, costed_units=3, cogs="30")
    _order_day(db, generation, DAY_THREE, units=3, costed_units=3, cogs="40")


def _seed_gap_day(db: Session, generation: int) -> None:
    """One product, a ledger row on Mar 1 (value 10) and Mar 3 (value 20), NONE
    on Mar 2 — the exact worked example in the repository's `_avg` docstring."""
    _stock(db, generation, DAY_ONE, PRODUCT_A, value="10", sold=1)
    _stock(db, generation, DAY_THREE, PRODUCT_A, value="20", sold=2)
    _product_day(db, generation, DAY_ONE, PRODUCT_A, units=3, costed_units=3, line_cost="30")
    _order_day(db, generation, DAY_ONE, units=2, costed_units=2, cogs="15")
    _order_day(db, generation, DAY_THREE, units=1, costed_units=1, cogs="15")


def _seed_restock(db: Session, generation: int) -> None:
    """A restock on the last day: 100, 100, then 700 at close of Mar 3."""
    _stock(db, generation, DAY_ONE, PRODUCT_A, value="100", sold=2)
    _stock(db, generation, DAY_TWO, PRODUCT_A, value="100", sold=2)
    _stock(db, generation, DAY_THREE, PRODUCT_A, value="700", sold=1, restocked=61)
    _product_day(db, generation, DAY_TWO, PRODUCT_A, units=5, costed_units=5, line_cost="150")
    _order_day(db, generation, DAY_TWO, units=5, costed_units=5, cogs="150")


def _seed_zero_sales(db: Session, generation: int) -> None:
    """Stock held all window, nothing moved, and no order rollup rows at all."""
    for day in (DAY_ONE, DAY_TWO, DAY_THREE):
        _stock(db, generation, day, PRODUCT_A, value="100", sold=0)


def _seed_partial_coverage(db: Session, generation: int) -> None:
    """Same shape as the hand-computed seed, but only half the units costed.

    10 units sold, 5 costed; the summed cogs_sum/line_cost covers the costed
    lines only (the rollup jobs SUM over lines with a unit_cost — SQL drops the
    NULL rows), so COGS = 50 and coverage = 5/10 = 50%.
    """
    for day, values in (
        (DAY_ONE, ("100", "50")),
        (DAY_TWO, ("150", "50")),
        (DAY_THREE, ("200", "50")),
    ):
        _stock(db, generation, day, PRODUCT_A, value=values[0], sold=2)
        _stock(db, generation, day, PRODUCT_B, value=values[1], sold=2 if day is DAY_ONE else 1)
    _product_day(db, generation, DAY_TWO, PRODUCT_A, units=6, costed_units=3, line_cost="30")
    _product_day(db, generation, DAY_TWO, PRODUCT_B, units=4, costed_units=2, line_cost="20")
    _order_day(db, generation, DAY_TWO, units=10, costed_units=5, cogs="50")


def _seed_zero_coverage(db: Session, generation: int) -> None:
    """Units sold, not one of them costed. COGS is unknowable, not zero."""
    for day in (DAY_ONE, DAY_TWO, DAY_THREE):
        _stock(db, generation, day, PRODUCT_A, value="100", sold=2)
    _product_day(db, generation, DAY_TWO, PRODUCT_A, units=6, costed_units=0, line_cost="0")
    _order_day(db, generation, DAY_TWO, units=6, costed_units=0, cogs="0")


def _seed_nothing(db: Session, generation: int) -> None:
    """An empty window: the ledger has no 1975 rows at all."""


# ---------------------------------------------------------------------------
# 1. The hand-computed window, end to end through AnalyticsViewService
# ---------------------------------------------------------------------------


def test_turnover_matches_the_hand_computed_arithmetic():
    """100 of COGS over 200 of average stock is 0.5 turns and 6 days of cover.

    Resolved through `AnalyticsViewService` (permissions, gating, envelope
    construction all in the path), and asserted against the arithmetic stated
    in `_seed_hand_computed`'s docstring. The three wrong constructions all
    produce different numbers here and are rejected by name below.
    """
    with sandbox(_seed_hand_computed) as (db, _generation):
        envelope = _resolve(db)

        turnover = envelope.kpis["inventory_turnover"]
        assert turnover.value == Decimal("0.5"), "COGS 100 / avg inventory 200"
        assert turnover.inputs_missing == []
        assert turnover.quality == "ESTIMATED", (
            "full cost coverage, but the denominator is valued at CURRENT cost — "
            "ESTIMATED is the ceiling"
        )
        assert turnover.coverage_pct == Decimal("100")

        # The wrong constructions, each a different number:
        assert turnover.value != Decimal("0.4"), (
            "100 / 250 — dividing by the END-of-window level, not the average"
        )
        assert turnover.value != Decimal("0.6667"), (
            "100 / 150 — dividing by the FIRST day's level"
        )
        assert turnover.value != Decimal("0.1667"), (
            "100 / 600 — dividing by the SUM of the level across days, the "
            "non-additive arithmetic metric_kind refuses"
        )

        days = envelope.kpis["days_of_inventory"]
        assert days.value == Decimal("6"), "3 days in window / 0.5 turns"

        stock_value = envelope.kpis["stock_value"]
        assert stock_value.value == Decimal("250"), (
            "the position at Mar 3, the latest ledger day in window (200 + 50) — "
            "never the window's summed 600"
        )
        assert stock_value.quality == "ESTIMATED"

        assert envelope.kpis["units_sold"].value == Decimal("10")

        assert {s.id for s in envelope.sources} >= {
            "agg_inventory_daily",
            "agg_order_daily",
        }
        assert NOT_CONFIGURED not in {w.code for w in envelope.warnings}


def test_the_per_product_table_ranks_slow_movers_first():
    """A turns 0.4 and B turns 0.8; the table opens on A — that is the point.

    Per product: A = 60/150 = 0.4 with 3/0.4 = 7.5 days of cover, B = 40/50 =
    0.8 with 3.75. The default order is turnover ASCENDING because the operator
    opens this view for what is NOT turning; a top-N descending would celebrate
    the healthiest stock.
    """
    with sandbox(_seed_hand_computed) as (db, _generation):
        envelope = _resolve(db)
        block = envelope.tables["turnover_by_product"]
        assert [r["product"] for r in block.rows] == [PRODUCT_A, PRODUCT_B]

        by_product = {r["product"]: r for r in block.rows}
        a, b = by_product[PRODUCT_A], by_product[PRODUCT_B]
        assert a["inventory_turnover"] == Decimal("0.4")
        assert a["days_of_inventory"] == Decimal("7.5")
        assert a["avg_stock_value"] == Decimal("150")
        assert a["cogs"] == Decimal("60")
        assert a["units_sold"] == 6
        assert a["days_observed"] == 3
        assert a["cost_coverage_pct"] == Decimal("100")
        assert a["sku"] == f"SKU-{PRODUCT_A}"
        assert b["inventory_turnover"] == Decimal("0.8")
        assert b["days_of_inventory"] == Decimal("3.75")

        # The row shape is exactly the registry's declared table, so the
        # contract and the resolver cannot drift.
        view = registry.get_view(*VIEW_30)
        assert view is not None
        assert set(block.rows[0]) == {c.key for c in view.tables[0].columns}


def test_the_category_split_is_refused_not_substituted():
    """The ledger stores no category; the chart is left undrawn and NAMED.

    Grouping the COGS side by `category_id_snapshot` and pretending the stock
    side matches would divide one category's cost by the whole catalogue's
    stock. The honest answer is DIMENSION_NOT_STORED — the same refusal views
    28/29 make on this table.
    """
    with sandbox(_seed_hand_computed) as (db, _generation):
        envelope = _resolve(db)
        assert "turnover_by_category" not in envelope.series
        gaps = [w for w in envelope.warnings if w.code == DIMENSION_NOT_STORED]
        assert gaps, "the impossible split must be reported, not silently dropped"
        assert any(w.detail.get("dimension") == "category" for w in gaps)


# ---------------------------------------------------------------------------
# 2. Gap days follow the documented `avg:` semantics
# ---------------------------------------------------------------------------


def test_a_gap_day_is_in_neither_the_numerator_nor_the_denominator():
    """Value 10 on Mar 1, no row Mar 2, value 20 on Mar 3: the average is 15.

    The repository's `_avg` docstring pins this exact example: the mean is over
    the rows that EXIST, so the absent day contributes nothing — it is "no
    data", not "zero stock". With COGS of 30::

        turnover  = 30 / ((10 + 20) / 2) = 30 / 15 = 2
        wrong     = 30 / ((10 + 0 + 20) / 3) = 30 / 10 = 3   (zero-filled gap)

    Zero-filling errs in the flattering direction (higher turnover), which is
    why the conservative semantics exist, and why this test pins them.
    """
    with sandbox(_seed_gap_day) as (db, _generation):
        envelope = _resolve(db)
        turnover = envelope.kpis["inventory_turnover"]
        assert turnover.value == Decimal("2"), "30 of COGS over a mean of 15"
        assert turnover.value != Decimal("3"), (
            "a zero-filled gap day would deflate the denominator to 10 and "
            "report the flattering 3.0"
        )

        # The caveat is surfaced, with the observed-day count against the window.
        caveats = [w for w in envelope.warnings if w.code == AVG_OVER_OBSERVED_DAYS]
        assert caveats, "the empty-bucket semantics must be stated on the answer"
        assert caveats[0].detail["days_in_window"] == 3
        assert caveats[0].detail["ledger_days_observed"] == 2


# ---------------------------------------------------------------------------
# 3. A restock mid-window: the average, never the end-pinned level
# ---------------------------------------------------------------------------


def test_restock_mid_window_uses_the_average_and_the_end_level_differs():
    """The test that proves why `avg:` exists.

    Stock value runs 100, 100, 700 (a restock lands on the last day) with COGS
    of 150::

        average   = (100 + 100 + 700) / 3 = 300 -> turnover = 150/300 = 0.5
        end-level =                       700   -> turnover = 150/700 = 0.2143

    The end-pinned figure UNDERSTATES turnover by more than half — a restock
    the day before the report would make every stock line look sluggish. The
    two answers must differ, and the average must win.
    """
    with sandbox(_seed_restock) as (db, _generation):
        envelope = _resolve(db)
        turnover = envelope.kpis["inventory_turnover"]
        assert turnover.value == Decimal("0.5"), "150 of COGS over an average of 300"

        end_pinned = (Decimal("150") / Decimal("700")).quantize(Decimal("0.0001"))
        assert end_pinned == Decimal("0.2143")
        assert turnover.value != end_pinned, (
            "the window-end level is not the average; after a restock it "
            "understates turnover"
        )
        assert end_pinned < turnover.value


# ---------------------------------------------------------------------------
# 4. Zeroes and absences are three different answers
# ---------------------------------------------------------------------------


def test_zero_sales_is_a_real_zero_and_days_of_cover_is_unbounded():
    """Nothing sold: turnover 0.0 is a CLAIM, and days of cover is None.

    The ledger measured a full window of held stock and zero movement, so 0 is
    a measurement, not a fabrication — `inputs_missing` stays empty. Days of
    inventory is days/turnover, which at zero turnover is unbounded: None,
    never infinity, and emphatically never 0 (which would claim NO cover).
    """
    with sandbox(_seed_zero_sales) as (db, _generation):
        envelope = _resolve(db)
        turnover = envelope.kpis["inventory_turnover"]
        assert turnover.value == Decimal("0")
        assert turnover.inputs_missing == []

        days = envelope.kpis["days_of_inventory"]
        assert days.value is None
        assert days.inputs_missing, "the unbounded cover must name its reason"


def test_partial_cost_coverage_is_incomplete_with_the_percentage_carried():
    """5 of 10 units costed: turnover 0.25 from the costed lines, INCOMPLETE.

    COGS covers only the costed half (50), so turnover = 50/200 = 0.25 — a real
    but partial figure. It is graded INCOMPLETE with coverage 50% on the card
    and a COST_COVERAGE_LOW warning, never silently presented as complete and
    never scaled up by a guessed factor.
    """
    with sandbox(_seed_partial_coverage) as (db, _generation):
        envelope = _resolve(db)
        turnover = envelope.kpis["inventory_turnover"]
        assert turnover.value == Decimal("0.25")
        assert turnover.quality == "INCOMPLETE"
        assert turnover.coverage_pct == Decimal("50")
        assert any(
            w.code == WarningCode.COST_COVERAGE_LOW for w in envelope.warnings
        )


def test_zero_cost_coverage_makes_turnover_missing_not_zero():
    """Units sold with no unit_cost anywhere: COGS is unknowable.

    A zero here would read as "nothing sold" — the opposite of the truth. The
    card is None with `unit_cost` named, the same substitution-refusal
    cost_rules and margin make.
    """
    with sandbox(_seed_zero_coverage) as (db, _generation):
        envelope = _resolve(db)
        turnover = envelope.kpis["inventory_turnover"]
        assert turnover.value is None
        assert turnover.inputs_missing == ["unit_cost"]
        assert envelope.kpis["days_of_inventory"].value is None


def test_an_empty_window_reports_missing_inputs_not_zero():
    """No ledger rows in the window: every inventory card is None and says why.

    The window genuinely holds nothing — the ledger is forward-only and March
    1975 predates it by decades — so a 0 would be manufactured. The table is
    empty rather than zero-filled, and the window gap is named in a warning.
    """
    with sandbox(_seed_nothing) as (db, _generation):
        envelope = _resolve(db)
        for kpi_id in ("inventory_turnover", "days_of_inventory", "stock_value"):
            kpi = envelope.kpis[kpi_id]
            assert kpi.value is None, f"{kpi_id} must not invent a zero"
            assert kpi.inputs_missing, f"{kpi_id} must name its missing input"

        block = envelope.tables["turnover_by_product"]
        assert block.rows == [] and block.total_rows == 0

        gaps = [
            w
            for w in envelope.warnings
            if w.code == WarningCode.NO_ROLLUP_YET
            and w.detail.get("requested_from") == DAY_ONE.isoformat()
        ]
        assert gaps, "the empty window must be reported as unmeasured, not zero"
        assert NOT_CONFIGURED not in {w.code for w in envelope.warnings}, (
            "an empty window is 'wired up, nothing to measure' — not 'unwired'"
        )


# ---------------------------------------------------------------------------
# 5. The registry binding itself
# ---------------------------------------------------------------------------


def test_view_30_is_bound_and_still_partial():
    """The binding is wiring; the promise did not move.

    PARTIAL is still the honest ceiling — the ledger is forward-only and stock
    is valued at current cost, and the limitation must keep saying both so the
    admin reads the figure as the estimate it is.
    """
    view = registry.get_view(*VIEW_30)
    assert view is not None and view.number == 30
    assert view.params == {"fn": "inventory_turnover"}
    assert view.state is ViewState.PARTIAL
    assert "forward-only" in view.limitation.lower()
    assert "current cost" in view.limitation.lower()
    assert view.tables and view.tables[0].id == "turnover_by_product"
