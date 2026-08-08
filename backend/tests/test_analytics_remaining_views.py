"""The last unbound views: what COUNT projections unlocked, and what they did not.

`AnalyticsRepository` grew two projections — `row_count` and
`count_distinct:<column>` — and that changed the answer for four views that had
been left unbound because the only expressible measure was a SUM of a stored
column. It did not change the answer for two others, and the tests that matter
most here are the ones asserting that.

What actually has to be true
----------------------------
Every failure below is silent. None of them raises, none of them draws an empty
chart; each returns a plausible number under a defined label, which is the one
failure mode this subsystem exists to prevent.

  * ``test_customer_segmentation_counts_customers_not_their_money`` — three
    customers in a segment holding 7,000 of lifetime value between them. The
    answer under a heading that says *Customers* is 3. The bug returns 7,000
    (the nearest summable column) or 6 (the count summed over both snapshot
    days), and both look like a segment size on a bar chart.
  * ``test_low_stock_lists_each_product_once_at_its_latest_stock`` — two
    products over two ledger days. The table has two rows, not four, and the
    stock on them is the LATEST day's. A `TableResolver` binding returns four
    rows, top one first, each product listed at its start-of-window stock —
    a "needs attention" list whose numbers are as old as the window.
  * ``test_stockout_rate_is_the_point_in_time_share`` — one of two products is
    out of stock on the last day, and one of four product-days is out of stock
    over the window. 50% and 25% are both real figures; only the first is what
    `kpis.stockout_rate` defines, and the catalogue says outright that the
    second "does not exist yet".
  * ``test_inventory_turnover_binding_did_not_upgrade_its_claims`` — view 30
    is now bound (the repository's `avg:` projection made the denominator
    expressible), and the tests that used to pin its refusal now pin the
    bound behaviour: real sources, real values, state still PARTIAL.

Isolation strategy
------------------
House style, following ``test_analytics_view_bindings.py``: no db fixture in
``conftest.py``; the module owns its ``SessionLocal()`` and tears down in a
``finally``. Fixtures are written under the database's **active** generation,
because ``AnalyticsViewService`` reads that from the database and cannot be told
otherwise, so isolation comes from the date range instead: **April 2001**, a
sandbox no other suite uses (June 2008, May 2005, June 2009, March 2007, 2004,
2010-2016 are all taken). Rows are deleted by ``(generation, bucket_date range)``
both before seeding and in ``finally``, so a rerun after a crashed run is clean.

No login: the service only ever calls ``user.has_permission``, so a stub that
grants exactly the two permissions these views need exercises the real
authorisation path without creating a user row.

Run inside the analytics container::

    docker exec wvana-py python -m pytest tests/test_analytics_remaining_views.py -q
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from typing import Iterator

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import analytics_rollups as rollups
from app.repositories.analytics_repository import columns_for, measures_for
from app.schemas.analytics_view import AnalyticsViewEnvelope
from app.services.analytics import kpis as kpi_catalogue
from app.services.analytics import registry
from app.services.analytics.filters import AnalyticsFilters, Comparison, Period
from app.services.analytics.metric_kind import MetricKind, classify
from app.services.analytics.resolvers.base import NOT_CONFIGURED
from app.services.analytics.resolvers.levels import (
    COUNT_WHERE_PREFIX as LEVELS_COUNT_WHERE,
    DIMENSION_NOT_STORED,
    INVENTORY_SOURCE,
    LEVEL_BINDINGS,
    _flag_of,
    _is_count,
    _partition,
)
from app.services.analytics.timebox import active_generation
from app.services.analytics.types import GATED_STATES, ViewState
from app.services.analytics.view_service import AnalyticsViewService

# ---------------------------------------------------------------------------
# What this change is responsible for
# ---------------------------------------------------------------------------

#: (number, module slug, view slug) for the views bound here. Slugs rather than
#: indices, so a registry reorder cannot silently repoint a test at another view.
NEWLY_BOUND: tuple[tuple[int, str, str], ...] = (
    (11, "customers", "customer-segmentation"),
    (13, "customers", "customer-churn"),
    (28, "inventory", "stock-availability"),
    (29, "inventory", "low-stock-and-out-of-stock"),
    (59, "customers", "rfm-customer-analysis"),
)

#: View 30 was re-assessed and left alone when COUNT projections landed —
#: turnover is COGS over AVERAGE inventory at cost, and an average over time of
#: a LEVEL was not expressible through a repository that offered only SUM and
#: COUNT. It is bound now: the repository grew `avg:<column>` (LEVEL-only, so
#: `avg:stock_value_close` is legal and `avg:units_sold` refuses), and
#: `resolvers/turnover.py` combines it with the `agg_order_daily` COGS totals
#: the margin engine already uses. `tests/test_analytics_turnover.py` owns the
#: arithmetic; the tests here pin that the binding replaced the old refusal
#: without upgrading the view's claims.
INVENTORY_TURNOVER = (30, "inventory", "inventory-turnover")

#: Every non-gated view still carrying empty `params`, with the reason it does.
#: An entry leaving this set is a deliberate wiring change and should show up as
#: a failing test, not as a view that quietly started answering.
#: Two shapes of gap, and they are not the same conversation. "no source" means
#: the fact is not recorded anywhere and someone has to build or connect it.
#: "no rollup" means the rows exist and the repository cannot reach them,
#: because it reads exactly the 12 `agg_*` tables — that one is a day of
#: aggregation work, not a product decision.
STILL_UNBOUND: dict[int, str] = {
    # 30 left this set when the repository grew the `avg:<column>` projection
    # (LEVEL-only by construction) and cost rules became enterable by admins —
    # the two facts its old entry said were missing. `resolvers/turnover.py`
    # divides the `agg_order_daily` COGS totals (the same unit_cost-snapshot
    # basis margin uses) by the mean of `stock_value_close` over the ledger
    # days that exist. The view stays PARTIAL: the ledger is forward-only and
    # stock is valued at current cost, so the figure is an estimate at best.
    # 42 left this set when the risk view was bound to observable payment and
    # order signals. It also moved LIVE -> PARTIAL: it was previously declared
    # LIVE with no binding at all, so it issued a fetch and rendered a data page
    # for numbers it could not produce. No fraud SCORE is computed — the view
    # shows observed signals and lets the operator draw the conclusion.
    # 50 and 51 left this set when `agg_cx_daily` was built (models/analytics_cx.py,
    # aggregation/jobs_cx.py). 51 now breaks reviews down by product off the stored
    # star counts. 50 is bound to inbound VOLUME only and stays PARTIAL with
    # support_ticketing in `requires`: contact_messages still carries no topic, no
    # assignment and no resolution time, so everything the view promises beyond the
    # trend is deliberately unbound — see the comments on the view in registry.py.
    # 57 left this set when `agg_basket_pair_daily` was built
    # (models/analytics_basket.py, aggregation/jobs_basket.py). Pairs are read
    # from `analytics_order_line`, NOT from order_items joined to products — a
    # historical basket's identity must not resolve through the live catalogue.
    # The view is on the custom resolver rather than the table one because the
    # rollup stores counts and the view shows ratios: support, confidence and
    # lift are recomputed from summed counts in resolvers/basket.py, since a
    # stored daily lift cannot be re-bucketed into a week.
    # 58 left this set by becoming GATED, not by being bound — the same move as
    # 66. "No source: needs a recommendation impression" is still true for
    # everything offer-shaped, and the basket data that landed since cannot
    # stand in: `agg_basket_pair_daily` stores counts and name snapshots with
    # deliberately no money column, and no rollup splits order value by basket
    # size, so a trade-up is indistinguishable from a co-purchase — which view
    # 57 already reports. The one computable figure (the attach rate) is view
    # 57's headline KPI, and binding it to 58 would have republished it under
    # an upsell heading. So: PARTIAL -> FEATURE_REQUIRED, `params` still empty
    # on purpose. See the view-58 comment in registry.py and
    # tests/test_analytics_upsell.py, which pins the schema facts.
    # 62, 63 and 73 left this set when the Control Centre resolvers were built
    # (resolvers/control_centre.py). All three are read-only by construction:
    # reconciliation runs with write_alerts=False and the anomaly feed runs the
    # detector inside a SAVEPOINT rolled back unconditionally, with results
    # snapshotted to plain values INSIDE the savepoint, because run.alerts are
    # live ORM rows the rollback expunges.
    #
    # 66 left this set by becoming GATED, not by being bound. There is no
    # experiment infrastructure in this codebase at all — no assignment, no
    # exposure logging, no variant storage, and zero tables or columns matching
    # experiment/variant/exposure in the live schema. It was declared LIVE, so
    # it promised A/B results it could never produce; it is now FEATURE_REQUIRED
    # with the four missing pieces named in its `limitation`. Note that
    # `experiment_id` appears in the Clarity tag allowlist, but nothing ever
    # populates it and Clarity tags are write-only to Microsoft, so that
    # parameter could never return to this database.
    #
    # 71 left this set when it was bound to obs_request_logs. What it shows is
    # SERVER request latency, not what a browser experiences — there is no RUM
    # feed, so no TTFB, LCP or CLS. That limitation is stated on the view so
    # nobody reads it as Core Web Vitals.
}

# April 2001 — before this store's first order, and outside every other
# analytics sandbox, so a window here holds this suite's rows and nothing else.
SANDBOX_START = date(2001, 4, 1)
SANDBOX_END = date(2001, 4, 30)
DAY_ONE = date(2001, 4, 2)
DAY_TWO = date(2001, 4, 3)
WINDOW_END = date(2001, 4, 5)  # half-open: the last reporting day is the 4th

PRODUCT_A = 90001
PRODUCT_B = 90002
PRODUCT_C = 90003

_OWNED_MODELS = (
    rollups.AggCustomerSnapshot,
    rollups.AggInventoryDaily,
    rollups.AggOrderDaily,
)


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------


class _AnalyticsReader:
    """Exactly the permissions these views need, and nothing else.

    `AnalyticsViewService` only ever calls `has_permission`, so this exercises
    the real authorisation path without creating a user row — and without
    reaching for the shared admin account, whose `is_admin` short circuit would
    make every check pass for the wrong reason.
    """

    is_admin = False

    def __init__(self, permissions: set[str]) -> None:
        self._permissions = permissions

    def has_permission(self, permission: str) -> bool:
        return permission in self._permissions


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
def sandbox() -> Iterator[tuple[Session, int]]:
    """A session plus one seeded April 2001 window, deleted unconditionally."""
    db = SessionLocal()
    generation = int(active_generation(db).generation)
    try:
        _clear(db, generation)
        _seed(db, generation)
        yield db, generation
    finally:
        try:
            db.rollback()
            _clear(db, generation)
        finally:
            db.close()


def _snapshot(
    db: Session,
    generation: int,
    day: date,
    customer: str,
    *,
    gross_ltv: str,
    segment: str,
    is_active: bool,
    churn_band: str = "low",
) -> None:
    db.add(
        rollups.AggCustomerSnapshot(
            bucket_date=day,
            tz_generation=generation,
            customer_key=customer,
            orders_count=2,
            units=4,
            gross_ltv=Decimal(gross_ltv),
            net_ltv=Decimal(gross_ltv),
            margin_ltv=Decimal(gross_ltv),
            aov=Decimal(gross_ltv) / 2,
            recency_days=7,
            frequency=2,
            monetary=Decimal(gross_ltv),
            r_score=4,
            f_score=3,
            m_score=3,
            rfm_segment=segment,
            cohort_month="2001-04",
            tenure_days=60,
            is_active=is_active,
            churn_risk_band=churn_band,
            preferred_payment_method="upi",
            quality="AUTHORITATIVE",
        )
    )


def _stock(
    db: Session,
    generation: int,
    day: date,
    product_id: int,
    *,
    stock: int,
    is_oos: bool,
    sold: int = 1,
) -> None:
    db.add(
        rollups.AggInventoryDaily(
            bucket_date=day,
            tz_generation=generation,
            product_id=product_id,
            sku_snapshot=f"SKU-{product_id}",
            stock_close=stock,
            stock_value_close=Decimal(stock) * Decimal("10.00"),
            units_sold=sold,
            units_restocked=0,
            is_oos=is_oos,
            days_oos=1 if is_oos else 0,
        )
    )


def _seed(db: Session, generation: int) -> None:
    """Two ledger days, chosen so every wrong answer is a different number.

    Customers: the population GROWS between the two days and the money grows
    tenfold, so "count at the latest instant" (3 champions), "count over the
    window" (6) and "sum of the money" (7,000) are three distinct values.

    Stock: three products, of which C is empty throughout and B empties on day
    two. Every wrong reading of "what share is out of stock" is a different
    number — 2 of 3 at the instant (66.67%), 3 of 6 product-days over the window
    (50%), and 1 of 3 if a SUM over the boolean flag is coerced back to `True`
    (33.33%). A's stock falls, so the latest reading (80) is not the first (100).
    """
    for day, ltvs in ((DAY_ONE, ("100", "200", "50")), (DAY_TWO, ("1000", "2000", "500"))):
        _snapshot(db, generation, day, "c1", gross_ltv=ltvs[0], segment="champions", is_active=True)
        _snapshot(db, generation, day, "c2", gross_ltv=ltvs[1], segment="champions", is_active=True)
        _snapshot(
            db, generation, day, "c3", gross_ltv=ltvs[2], segment="at_risk",
            is_active=False, churn_band="high",
        )
    # Joins on day two only: the count must move, which a sum of the window
    # would hide inside a bigger number.
    _snapshot(
        db, generation, DAY_TWO, "c4", gross_ltv="4000", segment="champions",
        is_active=False, churn_band="medium",
    )

    _stock(db, generation, DAY_ONE, PRODUCT_A, stock=100, is_oos=False)
    _stock(db, generation, DAY_ONE, PRODUCT_B, stock=5, is_oos=False)
    _stock(db, generation, DAY_ONE, PRODUCT_C, stock=0, is_oos=True, sold=0)
    _stock(db, generation, DAY_TWO, PRODUCT_A, stock=80, is_oos=False)
    _stock(db, generation, DAY_TWO, PRODUCT_B, stock=0, is_oos=True, sold=5)
    _stock(db, generation, DAY_TWO, PRODUCT_C, stock=0, is_oos=True, sold=0)

    # A small, complete order day so the cards these views borrow from
    # `agg_order_daily` (aov, orders_count, units_sold, net_revenue) are real
    # numbers rather than nulls — those come back through `compute_kpis`
    # unchanged and are not what this suite is testing.
    for day in (DAY_ONE, DAY_TWO):
        db.add(
            rollups.AggOrderDaily(
                bucket_date=day,
                tz_generation=generation,
                orders_total=10,
                orders_paid=5,
                orders_shipped=2,
                orders_delivered=1,
                orders_cancelled=1,
                order_value_created=Decimal("1000.00"),
                paid_order_value=Decimal("800.00"),
                gross_merchandise_sales=Decimal("900.00"),
                net_merchandise_sales=Decimal("850.00"),
                net_revenue=Decimal("930.00"),
                subtotal_sum=Decimal("900.00"),
                tax_sum=Decimal("50.00"),
                discount_sum=Decimal("50.00"),
                shipping_income=Decimal("30.00"),
                refund_sum=Decimal("0.00"),
                cogs_sum=Decimal("400.00"),
                units=20,
                costed_units=20,
                distinct_customers=8,
                new_customers=5,
                returning_customers=3,
            )
        )
    db.commit()


def _filters() -> AnalyticsFilters:
    """The seeded window, with no comparison — deltas are not what is tested."""
    return AnalyticsFilters(
        period=Period.CUSTOM,
        date_from=SANDBOX_START,
        date_to=WINDOW_END,
        comparison=Comparison.NONE,
    )


def _service(db: Session, *slugs: tuple[int, str, str]) -> AnalyticsViewService:
    permissions = {
        registry.get_view(module, slug).permission  # type: ignore[union-attr]
        for _n, module, slug in slugs
    }
    return AnalyticsViewService(db, _AnalyticsReader(permissions))


def _resolve(db: Session, target: tuple[int, str, str]) -> AnalyticsViewEnvelope:
    _number, module_slug, view_slug = target
    envelope = _service(db, target).resolve_view(
        module_slug, view_slug, _filters(), use_cache=False
    )
    assert isinstance(envelope, AnalyticsViewEnvelope), (
        f"{view_slug} returned a gated envelope; it is a non-gated view and must resolve"
    )
    return envelope


# ---------------------------------------------------------------------------
# 1. Every newly bound view answers, and says where the answer came from
# ---------------------------------------------------------------------------


def test_every_newly_bound_view_returns_a_data_envelope():
    """`sources` is the machine-readable half of the answer.

    `not_configured` returns an explicitly EMPTY list, so a non-empty one is the
    difference between "nothing is wired up" and "wired up, and here is the
    number". Asserted through `AnalyticsViewService` rather than the resolver
    directly, so permissions, gating and envelope construction are in the path.
    """
    with sandbox() as (db, _generation):
        for target in NEWLY_BOUND:
            number, _module_slug, view_slug = target
            envelope = _resolve(db, target)

            assert envelope.sources, (
                f"View {number} ({view_slug}) reported no provenance, which is how "
                "this subsystem says 'nothing is wired up'."
            )
            codes = {w.code for w in envelope.warnings}
            assert NOT_CONFIGURED not in codes, (
                f"View {number} ({view_slug}) still reports NOT_CONFIGURED: "
                + "; ".join(
                    w.message for w in envelope.warnings if w.code == NOT_CONFIGURED
                )
            )
            assert envelope.kpis or envelope.series or envelope.tables, (
                f"View {number} ({view_slug}) returned an envelope with no kpis, no "
                "series and no tables."
            )


def test_every_level_answer_names_the_instant_it_was_measured_at():
    """A position without its as-at date is not interpretable.

    "3 customers" over a 30-day window means nothing until you know it is the
    population on one specific day rather than something accumulated over
    thirty — so every row carries `as_at` and the envelope carries the note.
    """
    with sandbox() as (db, _generation):
        envelope = _resolve(db, NEWLY_BOUND[0])
        assert any(w.code == "LEVEL_AT_INSTANT" for w in envelope.warnings)
        rows = envelope.series["segment_size"]
        assert {r["as_at"] for r in rows} == {DAY_TWO.isoformat()}, (
            "every row is measured at the latest snapshot inside the window"
        )


# ---------------------------------------------------------------------------
# 2. View 11: a count of customers, not a sum of their money
# ---------------------------------------------------------------------------


def test_customer_segmentation_counts_customers_not_their_money():
    """The measure under a heading that says *Customers* must be a COUNT.

    Champions holds three customers on the latest snapshot day, worth 7,000
    between them, and appears on both seeded days. Three wrong answers are
    available and every one of them renders as a plausible segment size:

      7000  SUM(gross_ltv) — the nearest summable column
         6  the count summed across both snapshot days
      1200  the money on day one only

    This is the binding COUNT projections made possible; before them the only
    expressible answer was the first one.
    """
    with sandbox() as (db, _generation):
        envelope = _resolve(db, NEWLY_BOUND[0])

        rows = {r["segment"]: r for r in envelope.series["segment_size"]}
        assert set(rows) == {"champions", "at_risk"}

        assert rows["champions"]["customers"] == Decimal("3"), (
            "three distinct customers were in champions on the latest snapshot day"
        )
        assert rows["at_risk"]["customers"] == Decimal("1")

        assert rows["champions"]["customers"] != Decimal("7000"), (
            "the segment's lifetime value is not its number of customers"
        )
        assert rows["champions"]["customers"] != Decimal("6"), (
            "counting both snapshot days counts each customer once per day"
        )

        # The same rows fill the table, so the screen cannot disagree with itself.
        table = envelope.tables["segment_table"]
        assert {r["segment"]: r["customers"] for r in table.rows} == {
            "champions": Decimal("3"),
            "at_risk": Decimal("1"),
        }


def test_customer_segmentation_leaves_revenue_per_segment_undrawn():
    """No rollup carries revenue at customer-segment grain, so the chart is empty.

    `gross_ltv` is sitting right there and is the wrong number: lifetime value
    published under a period-revenue label. The chart is left undrawn and the
    warning names the metric — an absent line is honest, a relabelled one is not.
    """
    with sandbox() as (db, _generation):
        envelope = _resolve(db, NEWLY_BOUND[0])
        assert "segment_revenue" not in envelope.series
        unbound = [
            w for w in envelope.warnings
            if w.code == "METRIC_NOT_BOUND" and "net_revenue" in w.detail.get("metrics", [])
        ]
        assert unbound, "the missing revenue series must be named, not silently dropped"


# ---------------------------------------------------------------------------
# 3. View 29: latest per product, not product x days
# ---------------------------------------------------------------------------


def test_low_stock_lists_each_product_once_at_its_latest_stock():
    """Three products over two ledger days make three rows, not six.

    A `TableResolver` binding projects the ledger straight through: six rows,
    each product listed once per day, ordered oldest first — so the "needs
    attention" list would open on a stock reading as old as the window. The
    snapshot resolver pins the newest ledger day and groups there.
    """
    with sandbox() as (db, _generation):
        envelope = _resolve(db, (29, "inventory", "low-stock-and-out-of-stock"))

        rows = envelope.tables["low_stock_table"].rows
        assert len(rows) == 3, (
            f"expected one row per product, got {len(rows)} — three products x two "
            "seeded days is 6, which is the shape this binding exists to avoid"
        )
        assert {r["product"] for r in rows} == {PRODUCT_A, PRODUCT_B, PRODUCT_C}
        assert {r["as_at"] for r in rows} == {DAY_TWO.isoformat()}

        by_product = {r["product"]: r["stock"] for r in rows}
        assert by_product[PRODUCT_A] == Decimal("80"), "the latest day's stock"
        assert by_product[PRODUCT_A] != Decimal("100"), "not the window's first day"
        assert by_product[PRODUCT_A] != Decimal("180"), "and never the sum of the days"
        assert by_product[PRODUCT_B] == Decimal("0")


def test_low_stock_puts_the_emptiest_shelf_first():
    """"Needs attention" ranked by stock DESCENDING would list the fullest first.

    The default for a breakdown is a top-N by its primary measure, descending,
    which is right for revenue and backwards here. `breakdown_sort` is what says
    so, and this asserts the table honours it rather than the default.
    """
    with sandbox() as (db, _generation):
        envelope = _resolve(db, (29, "inventory", "low-stock-and-out-of-stock"))
        rows = envelope.tables["low_stock_table"].rows
        assert [r["stock"] for r in rows] == sorted(r["stock"] for r in rows)
        assert rows[-1]["product"] == PRODUCT_A, "the fullest shelf comes last"
        assert rows[0]["stock"] == Decimal("0")


def test_products_out_of_stock_is_a_level_series_not_a_running_total():
    """Each point is that bucket's position, so the line can go DOWN.

    Summed instead, a restock could never reduce the line. And two products out
    of stock must read as 2: `SUM(is_oos)` returns through SQLAlchemy's Boolean
    processor as `True`, so the whole catalogue being empty and one SKU being
    empty would draw the same line at 1.
    """
    with sandbox() as (db, _generation):
        envelope = _resolve(db, (29, "inventory", "low-stock-and-out-of-stock"))
        points = {p["date"]: p["out_of_stock"] for p in envelope.series["stockouts_trend"]}
        assert points[DAY_ONE.isoformat()] == Decimal("1"), "product C alone"
        assert points[DAY_TWO.isoformat()] == Decimal("2"), (
            "B joins C; a boolean SUM would report True, which is 1"
        )


# ---------------------------------------------------------------------------
# 4. View 28: the point-in-time stockout share, and the split it cannot do
# ---------------------------------------------------------------------------


def test_stockout_rate_is_the_point_in_time_share():
    """66.67%, not 50% and not 33.33%. All three are real; one is the label.

    Two of three products are out of stock on the latest ledger day. Over the
    window it is three of six product-days (50%), and with `SUM(is_oos)` coerced
    back to `True` it is one of three (33.33%). `kpis.stockout_rate` is defined
    as "POINT IN TIME, not a period rate", and its caveats say the time-weighted
    version "does not exist yet" — so publishing 50% under this id would put a
    figure the catalogue disclaims behind a defined name.
    """
    with sandbox() as (db, _generation):
        for target in ((28, "inventory", "stock-availability"),
                       (29, "inventory", "low-stock-and-out-of-stock")):
            envelope = _resolve(db, target)
            rate = envelope.kpis["stockout_rate"]
            assert rate.value == Decimal("66.6667"), (
                f"{target[2]}: two of three products are out of stock at the instant"
            )
            assert rate.value != Decimal("50.0000"), (
                "the window's product-day share is the time-weighted rate, which "
                "the catalogue says does not exist yet"
            )
            assert rate.value != Decimal("33.3333"), (
                "a boolean SUM reports any number of out-of-stock products as True"
            )
            assert rate.inputs_missing == []


def test_stock_availability_says_it_cannot_split_by_category():
    """The ledger is per product and carries no category, so the chart is empty.

    Named rather than left blank, and NOT regrouped by SKU under a heading that
    says category — an adjacent split is a different question with the same
    picture.
    """
    with sandbox() as (db, _generation):
        envelope = _resolve(db, (28, "inventory", "stock-availability"))

        assert "availability_by_category" not in envelope.series
        assert "availability_table" not in envelope.tables
        gaps = [w for w in envelope.warnings if w.code == DIMENSION_NOT_STORED]
        assert gaps, "the missing category dimension must be reported"
        assert gaps[0].detail["dimension"] == "category"
        assert gaps[0].detail["source"] == INVENTORY_SOURCE


# ---------------------------------------------------------------------------
# 5. View 13: lapsed is the population minus the active part of it
# ---------------------------------------------------------------------------


def test_lapsed_customers_is_the_population_minus_its_active_part():
    """Churn without subscriptions is lapse, and the snapshot stores it.

    On day two the population is four and two of them ordered inside the
    activity window, so two have lapsed. Day one has three customers, all but
    one active, so the line moves — a series that could only ever be computed as
    a count.
    """
    with sandbox() as (db, _generation):
        envelope = _resolve(db, (13, "customers", "customer-churn"))

        points = {p["date"]: p["lapsed_customers"] for p in envelope.series["lapsed_trend"]}
        assert points[DAY_ONE.isoformat()] == Decimal("1"), "c3 alone on day one"
        assert points[DAY_TWO.isoformat()] == Decimal("2"), "c3 and c4 on day two"

        rows = {r["segment"]: r["lapse_rate"] for r in envelope.series["lapse_by_segment"]}
        # champions: one of three has lapsed. at_risk: the only member has.
        assert rows["champions"] == Decimal("33.3333")
        assert rows["at_risk"] == Decimal("100.0000")


def test_customer_churn_cards_stay_null_and_name_what_they_need():
    """Retention and churn are set operations over two windows of history.

    No rollup stores the customer sets, so the cards report null with the input
    named. A zero here reads as "nobody churned", which is the same shape as the
    truth and the opposite of it.
    """
    with sandbox() as (db, _generation):
        envelope = _resolve(db, (13, "customers", "customer-churn"))
        for kpi_id in ("customer_churn_rate", "customer_retention_rate", "ltv"):
            kpi = envelope.kpis[kpi_id]
            assert kpi.value is None, f"{kpi_id} must not be computed from a snapshot"
            assert kpi.inputs_missing, f"{kpi_id} must name what it is missing"


# ---------------------------------------------------------------------------
# 6. What stayed unbound, and why
# ---------------------------------------------------------------------------


def test_inventory_turnover_binding_did_not_upgrade_its_claims():
    """View 30 is bound now — and the binding earned it NOTHING else.

    The two blockers its old entry in ``STILL_UNBOUND`` named are gone: the
    repository projects `avg:<column>` (LEVEL-only), and cost rules are
    enterable by admins. What must NOT have moved with the wiring:

      * the state stays PARTIAL — the ledger is forward-only and stock is
        valued at current cost, and a binding never earns a view a better
        state;
      * the limitation still says so, in words an admin can act on;
      * `stock_value_close` still classifies as a LEVEL, which is exactly why
        the denominator goes through `avg:` and never through a SUM across
        days.
    """
    number, module_slug, view_slug = INVENTORY_TURNOVER
    view = registry.get_view(module_slug, view_slug)
    assert view is not None and view.number == number
    assert view.params == {"fn": "inventory_turnover"}, (
        f"View {number} ({view_slug}) declares {view.params!r}. The binding is a "
        "custom function because the figure is a two-rollup ratio over an "
        "averaged level; anything else here is a different (and suspect) wiring."
    )
    assert view.state is ViewState.PARTIAL, (
        "the binding is wiring, not new data — PARTIAL is still the honest ceiling"
    )
    assert "forward-only" in view.limitation.lower()
    assert "current cost" in view.limitation.lower()

    # `stock_value_close` is a level: the average the denominator needs is the
    # one thing the `avg:` projection permits, and a SUM across days remains
    # the arithmetic this subsystem exists to refuse.
    assert classify("stock_value_close", source=INVENTORY_SOURCE) is MetricKind.LEVEL


def test_inventory_turnover_reports_real_sources_now_that_it_is_bound():
    """The refusal this test used to pin is gone; pin what replaced it.

    This test asserted `sources == []` and NOT_CONFIGURED — the machine-readable
    "nothing is wired up". View 30 is wired now, so the same request must carry
    provenance for BOTH rollups the ratio reads and a real value, computed from
    this suite's seed:

      denominator = Σ per-product mean of `stock_value_close` over the days
                    each product has rows (the documented `avg:` semantics)
                  = A:(1000+800)/2 + B:(50+0)/2 + C:(0+0)/2 = 900 + 25 + 0 = 925
      numerator   = Σ cogs_sum over the window = 400 + 400 = 800
      turnover    = 800 / 925 = 0.8649 (4 dp)

    Cost coverage is 40/40 = 100%, so the figure is ESTIMATED (stock is valued
    at current cost — never better), not INCOMPLETE.
    """
    with sandbox() as (db, _generation):
        envelope = _resolve(db, INVENTORY_TURNOVER)

        assert {s.id for s in envelope.sources} >= {
            "agg_inventory_daily",
            "agg_order_daily",
        }, "both halves of the ratio must state their provenance"
        assert NOT_CONFIGURED not in {w.code for w in envelope.warnings}, (
            "the view is bound; a NOT_CONFIGURED here means the binding regressed"
        )

        turnover = envelope.kpis["inventory_turnover"]
        assert turnover.value == Decimal("0.8649"), (
            "COGS 800 over average inventory 925 — see the docstring arithmetic"
        )
        assert turnover.inputs_missing == []
        assert turnover.quality == "ESTIMATED", (
            "stock_value_close is valued at CURRENT cost, so full coverage earns "
            "ESTIMATED and nothing better"
        )


def test_the_set_of_unbound_non_gated_views_is_exactly_what_is_documented():
    """Every remaining gap is listed with its reason, so none of them is an oversight.

    A view leaving this set is a deliberate wiring change and shows up here as a
    failure; a view JOINING it is a binding that was quietly removed. Both are
    worth a conversation rather than a silent diff.
    """
    actual = {
        view.number
        for view in registry.all_views()
        if not view.params and view.state not in GATED_STATES
    }
    assert actual == set(STILL_UNBOUND), (
        "The set of non-gated views with no binding changed.\n"
        f"  no longer unbound: {sorted(set(STILL_UNBOUND) - actual)}\n"
        f"  newly unbound:     {sorted(actual - set(STILL_UNBOUND))}\n"
        "Update STILL_UNBOUND with the reason, or restore the binding."
    )


# ---------------------------------------------------------------------------
# 7. Honesty guards on the bindings this change added
# ---------------------------------------------------------------------------


def test_no_level_binding_can_ever_be_summed_across_buckets():
    """Every binding in `LEVEL_BINDINGS` must partition as PINNED.

    This is the whole premise of the module: a level is only reachable through
    the path that pins an instant first. One that partitioned as `summed` would
    be added across the window and wrong by the number of days in range, with
    nothing raising.
    """
    for source, bindings in LEVEL_BINDINGS.items():
        pinned, summed, refused = _partition(bindings)
        assert not summed, f"{source}: {sorted(summed)} would be summed across buckets"
        assert not refused, f"{source}: {refused} cannot be combined at all"
        assert set(pinned) == set(bindings)


def test_every_level_binding_names_a_real_column_or_a_count():
    """A binding is either a stored column of its source, or a count projection.

    The repository validates both against the same allowlist at query time; this
    turns a typo into a red test rather than a 500 on a live view. A
    ``count_where:`` names its flag through the same allowlist, because the flag
    becomes a GROUP BY key and reaches exactly the same lookup.
    """
    for source, bindings in LEVEL_BINDINGS.items():
        available = set(columns_for(source))
        summable = set(measures_for(source))
        for metric_id, binding in bindings.items():
            for column in binding.columns:
                flag = _flag_of(column)
                if flag is not None:
                    assert flag in available, (
                        f"{source}.{metric_id} counts rows where {flag!r} is true, "
                        "which is not a column of that rollup"
                    )
                    continue
                if _is_count(column):
                    continue
                assert column in available, (
                    f"{source}.{metric_id} names {column!r}, not a column of that rollup"
                )
                assert column in summable, (
                    f"{source}.{metric_id} aggregates {column!r}, which the repository "
                    "will not put inside SUM()"
                )


def test_no_level_binding_ever_sums_a_boolean_column():
    """`SUM(<boolean>)` returns `True` or `False`, which is 1 or 0.

    `func.sum()` takes its type from the column, so the Boolean result processor
    runs on the aggregate: twelve products out of stock comes back as `True`.
    Nothing raises, the rate is plausible, and no reader can check it — the exact
    shape of failure this package exists to prevent, and one that cannot be
    fixed from a resolver because the repository owns the SQL. Every count over
    a flag therefore goes through `count_where:`, which counts GROUPS.

    Asserted from the mapped columns rather than a hand-written list, so a new
    boolean rollup column is covered the day it is added.
    """
    from sqlalchemy import inspect as sa_inspect

    from app.repositories.analytics_repository import SOURCES

    for source, bindings in LEVEL_BINDINGS.items():
        model = SOURCES[source].model
        booleans = {
            attr.key
            for attr in sa_inspect(model).column_attrs
            if getattr(attr.expression.type, "python_type", None) is bool
        }
        assert booleans, f"{source} has no boolean column; this test would be vacuous"
        for metric_id, binding in bindings.items():
            offenders = sorted(set(binding.columns) & booleans)
            assert not offenders, (
                f"{source}.{metric_id} sums {offenders}, which SQLAlchemy returns as "
                f"True/False. Count them with {LEVELS_COUNT_WHERE}<column> instead."
            )


def test_no_catalogue_kpi_is_displayed_outside_its_declared_dimensions():
    """`kpis.py` says which splits a metric survives. A level binding honours it.

    The peer suite asserts this for `params["metrics"]`; these bindings live in
    code instead, so the same rule needs stating where they are. `stockout_rate`
    is the only catalogue id among them, and its dimensions are product / sku /
    category — never `date`, because it is a position and not a period rate.
    """
    for _number, module_slug, view_slug in NEWLY_BOUND:
        view = registry.get_view(module_slug, view_slug)
        assert view is not None
        dimension = view.params.get("dimension")
        if dimension is None:
            continue
        source = view.params["source"]
        for metric_id in LEVEL_BINDINGS.get(source, {}):
            meta = kpi_catalogue.by_id(metric_id)
            if meta is None or not meta.dimensions:
                continue
            assert dimension in meta.dimensions, (
                f"View {view.number} ({view_slug}) can display {metric_id!r} split by "
                f"{dimension!r}, which the catalogue does not list among its "
                f"legitimate dimensions ({', '.join(meta.dimensions)})."
            )


def test_the_bound_views_declare_no_metrics_block():
    """`params["metrics"]` is consumed by the generic resolvers, which SUM it.

    A level named there is a latent bug whatever resolver the view uses today,
    which is why these bindings live in `resolvers/levels.py` instead. Restated
    here for the five views this change bound.
    """
    for number, module_slug, view_slug in NEWLY_BOUND:
        view = registry.get_view(module_slug, view_slug)
        assert view is not None
        assert not view.params.get("metrics"), (
            f"View {number} ({view_slug}) declares params['metrics'] "
            f"{view.params.get('metrics')!r}; its rollup stores levels and every "
            "column named there is summed across the window."
        )
        assert view.params.get("fn") == "snapshot"
        assert view.params.get("source") in LEVEL_BINDINGS


# ---------------------------------------------------------------------------
# 8. The generated contract carries the new wiring
# ---------------------------------------------------------------------------


def test_frontend_contract_is_regenerated_from_this_registry():
    """The committed JSON must be byte-identical to a fresh dump.

    The same guard `tests/unit/test_analytics_registry.py` applies, restated
    here because filling in `params` changes the artifact and the two must land
    in one change. The fix is always to regenerate, never to edit the JSON.
    """
    import scripts.dump_analytics_registry as dump

    expected = dump.render_contract()
    actual = dump.CONTRACT_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        "frontend/src/features/analytics/registry.contract.json is stale. "
        "Regenerate it: python backend/scripts/dump_analytics_registry.py"
    )


@pytest.mark.parametrize(
    "number,module_slug,view_slug", NEWLY_BOUND, ids=[t[2] for t in NEWLY_BOUND]
)
def test_the_contract_carries_each_new_binding(
    number: int, module_slug: str, view_slug: str
):
    """`params` and the resolver survive serialisation, so the frontend agrees."""
    import json

    import scripts.dump_analytics_registry as dump

    payload = json.loads(dump.render_contract())
    by_number = {v["number"]: v for m in payload["modules"] for v in m["views"]}
    view = registry.get_view(module_slug, view_slug)
    assert view is not None
    assert by_number[number]["params"] == view.params
    assert by_number[number]["resolver"] == "custom"
