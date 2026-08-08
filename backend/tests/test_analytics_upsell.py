"""View 58 (Upsell Performance): the record of a decision to bind NOTHING.

The view was PARTIAL with an empty ``params`` — it fetched with nothing to
read. This wave re-assessed it against the schema that has landed since the
original "no source" verdict (``agg_basket_pair_daily``, ``agg_order_daily``)
and moved it PARTIAL -> FEATURE_REQUIRED instead of binding a subset. These
tests pin both halves of that decision: that the gate is real, and that the
evidence it rests on is still true.

Why nothing was bound
---------------------
Three candidate subsets were checked, and each fails on a fact these tests
assert rather than assume:

  * **Offer conversion / acceptance / attributed revenue** — needs an upsell
    placement that records impressions and clicks. The storefront has none, so
    those metrics have neither a numerator nor a denominator. This is the
    original gap, unchanged.
  * **"Traded up to a higher-value item"** — ``agg_basket_pair_daily``
    deliberately carries no money column ("a basket composition table, not a
    revenue table"), and its name snapshots must not be resolved through the
    live catalogue for a price. ``test_the_pair_rollup_still_carries_no_money``
    is that sentence as an assertion: if a price ever lands on the table, the
    test fails and view 58's state has to be reargued.
  * **"Baskets with an add-on are worth more" (AOV multi- vs single-item)** —
    needs order value split by basket size, which no rollup stores.
    ``test_no_rollup_splits_order_value_by_basket_size`` pins that. Building
    such a rollup is an aggregation change, deliberately not made here.

What survives all three cuts is the multi-item order share (the attach rate)
— and that is ``basket_attach_rate``, view 57's headline KPI, served from the
same rows. ``test_attach_rate_is_a_share_of_orders_not_of_rows`` computes it
end to end through view 57 on hand-checked fixtures to show the figure is
computable, exact, and *already published*: binding it to view 58 would have
republished the cross-sell number under an upsell heading, which is the
duplicate the registry refuses to ship.

Isolation strategy
------------------
House style, following ``test_analytics_basket_rollup.py``: no db fixture in
``conftest.py``; the module owns its ``SessionLocal()`` and tears down in a
``finally``. Fixtures are written under the database's ACTIVE tz generation
(``AnalyticsViewService`` reads it and cannot be told otherwise), so isolation
comes from the date range: **1976**, a year this store never traded in and no
other analytics suite uses. Rows are wiped by (generation, 1976 range) both
before seeding and in ``finally``, so a rerun after a crash is clean.

Run inside the analytics container::

    docker exec wvana-py python -m pytest tests/test_analytics_upsell.py -q
"""
from __future__ import annotations

import contextlib
import itertools
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterator, Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.analytics_basket import AggBasketPairDaily
from app.models.analytics_facts import AnalyticsOrderLine, IdentitySource
from app.repositories.analytics_repository import (
    columns_for,
    known_sources,
    measures_for,
)
from app.schemas.analytics_view import AnalyticsViewEnvelope, GatedViewEnvelope
from app.services.analytics import registry
from app.services.analytics.aggregation.jobs_basket import BasketPairDailyJob
from app.services.analytics.filters import AnalyticsFilters, Comparison, Period
from app.services.analytics.timebox import (
    active_generation,
    day_bounds_utc,
    store_timezone,
)
from app.services.analytics.types import (
    GATED_STATES,
    Capability,
    MetricQuality,
    ViewState,
)
from app.services.analytics.view_service import AnalyticsViewService

# ---------------------------------------------------------------------------
# The 1976 sandbox
# ---------------------------------------------------------------------------

SANDBOX_FIRST = date(1976, 1, 1)
SANDBOX_LAST = date(1976, 12, 31)

#: The one seeded day for the attach-rate arithmetic.
DAY_ATTACH = date(1976, 6, 15)

#: Product ids that exist in no catalogue and in no other suite's id space.
P_MAIN, P_ADDON, P_EXTRA, P_SOLO = 976_001, 976_002, 976_003, 976_004

NAMES: dict[int, str] = {
    P_MAIN: "Shilajit Resin",
    P_ADDON: "Gokshura Tablets",
    P_EXTRA: "Safed Musli Powder",
    P_SOLO: "Amla Juice",
}

#: order_id / order_item_id space this module owns. `order_item_id` is UNIQUE
#: on the fact table, so the counter is monotonic across the whole module.
_IDS = itertools.count(6_976_001)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def sandbox() -> Iterator[tuple[Session, int]]:
    """A session and the ACTIVE tz generation, with 1976 wiped on the way out."""
    db = SessionLocal()
    generation = int(active_generation(db).generation)
    try:
        _wipe(db, generation)
        yield db, generation
    finally:
        try:
            db.rollback()
            _wipe(db, generation)
        finally:
            db.close()


def _wipe(db: Session, generation: int) -> None:
    for model in (AggBasketPairDaily, AnalyticsOrderLine):
        db.execute(
            delete(model).where(
                model.tz_generation == generation,
                model.bucket_date >= SANDBOX_FIRST,
                model.bucket_date <= SANDBOX_LAST,
            )
        )
    db.commit()


def _basket(db: Session, generation: int, day: date, products: Sequence[int]) -> None:
    """One order's worth of line facts, at store-local noon on ``day``.

    Written straight to ``analytics_order_line`` because that is the basket
    job's actual and only input; building orders and products would test the
    ingestion path instead and couple this module to a catalogue the rollup
    deliberately never reads.
    """
    start, _end = day_bounds_utc(day, store_timezone(db))
    moment = start + timedelta(hours=12)
    order = next(_IDS)
    for product_id in products:
        db.add(
            AnalyticsOrderLine(
                order_id=order,
                order_item_id=next(_IDS),
                ordered_at=moment,
                bucket_date=day,
                tz_generation=generation,
                product_id=product_id,
                sku_snapshot=f"SKU-{product_id}",
                product_name_snapshot=NAMES[product_id],
                quantity=1,
                unit_price=Decimal("100.00"),
                extended_price=Decimal("100.00"),
                cost_quality=MetricQuality.INCOMPLETE.value,
                alloc_quality=MetricQuality.ALLOCATED.value,
                identity_source=IdentitySource.CAPTURED_AT_SALE,
                order_status="paid",
                payment_method="prepaid",
            )
        )
    db.flush()


class _AnalyticsReader:
    """Exactly the one permission these views need, and nothing else.

    `AnalyticsViewService` only calls `has_permission`, so this exercises the
    real authorisation path without creating a user — and without the shared
    admin account, whose `is_admin` short circuit passes for the wrong reason.
    """

    is_admin = False

    def __init__(self, permission: str) -> None:
        self._permission = permission

    def has_permission(self, permission: str) -> bool:
        return permission == self._permission


def _filters(date_from: date, date_to: date) -> AnalyticsFilters:
    return AnalyticsFilters(
        period=Period.CUSTOM,
        date_from=date_from,
        date_to=date_to,
        comparison=Comparison.NONE,
    )


def _view_58():
    view = registry.get_view("products", "upsell-performance")
    assert view is not None and view.number == 58
    return view


# ---------------------------------------------------------------------------
# 1. The gate itself
# ---------------------------------------------------------------------------


def test_view_58_is_gated_with_the_reason_on_it():
    """FEATURE_REQUIRED, nothing bound, and the limitation states BOTH gaps.

    The state must name what is missing (an upsell placement) and why the
    basket data that exists cannot stand in — an admin reading only the gated
    panel has to come away knowing that this is not view 57 wearing a second
    name, and not a wiring oversight either.
    """
    view = _view_58()
    assert view.state is ViewState.FEATURE_REQUIRED
    assert view.params == {}, (
        f"view 58 acquired a binding: {view.params!r}. Whatever this points at, "
        "it is not an upsell offer — no impression, click or acceptance is "
        "recorded anywhere. If a placement landed, change the state, not just "
        "the params."
    )
    assert not view.bespoke
    assert Capability.RECOMMENDATION_ENGINE in view.requires

    limitation = view.limitation.lower()
    assert "impression" in limitation, (
        "the limitation no longer names the impression gap, which is the "
        "entire reason the view is gated"
    )
    assert "cross-sell" in limitation, (
        "the limitation must say why the basket data cannot stand in — that "
        "without prices a trade-up is indistinguishable from the co-purchase "
        "view 57 already reports"
    )


def test_view_58_state_stops_the_client_fetching():
    """The state is in GATED_STATES, which is the set the client consults.

    This is the invariant view 58 previously needed a strict-xfail excuse for
    in test_analytics_view_state_honesty.py: PARTIAL fetches, and it had
    nothing to answer with. FEATURE_REQUIRED does not fetch, so the excuse is
    gone rather than moved.
    """
    assert _view_58().state in GATED_STATES


def test_view_58_answers_with_the_gated_envelope_and_no_data_block():
    """End to end through the service: the gated shape, never a data page.

    `GatedViewEnvelope` deliberately has no kpis, series or tables — an
    empty-but-present data block invites a client to draw an axis through it
    as if the answer were zero. `sources` must be empty too: a gated view
    that reports provenance is claiming to have read something.
    """
    with sandbox() as (db, _generation):
        view = _view_58()
        service = AnalyticsViewService(db, _AnalyticsReader(view.permission))
        envelope = service.resolve_view(
            "products",
            "upsell-performance",
            _filters(DAY_ATTACH, DAY_ATTACH + timedelta(days=1)),
            use_cache=False,
        )
        assert isinstance(envelope, GatedViewEnvelope), (
            "view 58 returned a data envelope; FEATURE_REQUIRED must resolve "
            "to the gated shape with nothing renderable inside it"
        )
        assert envelope.availability == ViewState.FEATURE_REQUIRED.value
        assert Capability.RECOMMENDATION_ENGINE.value in envelope.requires
        assert envelope.limitation
        assert envelope.sources == []


# ---------------------------------------------------------------------------
# 2. The evidence the decision rests on, pinned
# ---------------------------------------------------------------------------

#: Substrings that would mean money landed on the pair rollup. `value` alone
#: would also catch a hypothetical `stock_value`-style column, which is money
#: too as far as this decision is concerned.
_MONEY_MARKERS = ("price", "value", "amount", "revenue", "aov", "cost", "total_paid")


def test_the_pair_rollup_still_carries_no_money():
    """`agg_basket_pair_daily` is composition, not revenue — asserted, not quoted.

    "A price step between paired products" was one of the three candidate
    upsell readings, and it is impossible precisely because this table stores
    counts and name snapshots only. The model docstring says no money column
    belongs on it; this test makes that sentence load-bearing. If a Decimal
    column or a money-named column ever appears here, view 58's
    FEATURE_REQUIRED state has to be reargued — which is exactly the failure
    this produces.
    """
    for column in AggBasketPairDaily.__table__.columns:
        assert column.type.python_type is not Decimal, (
            f"agg_basket_pair_daily.{column.name} is a Decimal column. Money "
            "landed on the pair rollup; re-assess view 58 (registry.py) before "
            "touching this test."
        )
        assert not any(marker in column.name.lower() for marker in _MONEY_MARKERS), (
            f"agg_basket_pair_daily.{column.name} looks like a money column"
        )

    # And every measure the repository will aggregate from it is a count.
    assert set(measures_for("agg_basket_pair_daily")) == {
        "pair_orders",
        "orders_with_a",
        "orders_with_b",
        "total_orders_in_bucket",
        "orders_with_any_pair",
        "orders_skipped_over_cap",
    }


def test_no_rollup_splits_order_value_by_basket_size():
    """No source anywhere stores single- vs multi-item money — asserted, not assumed.

    "AOV with vs without add-on" (the chart view 58 used to declare) needs
    order value split by basket size. `agg_order_daily` is one row a day with
    aggregate money; nothing else stores the split either. The check is by
    column name over EVERY source the repository can read, so an
    `agg_order_daily.net_revenue_multi_item` — or a whole new rollup carrying
    the split — fails it and forces the view-58 re-assessment this module
    documents.
    """
    offenders = [
        f"{source}.{column}"
        for source in known_sources()
        for column in columns_for(source)
        if "multi" in column.lower() or "single" in column.lower()
    ]
    assert not offenders, (
        f"columns splitting by basket size appeared: {offenders}. If order "
        "value by item count is now stored, the 'AOV with vs without add-on' "
        "half of upsell-performance may be bindable — re-assess view 58's "
        "state in registry.py before weakening this test."
    )


# ---------------------------------------------------------------------------
# 3. The one computable figure, computed where it lives (view 57)
# ---------------------------------------------------------------------------


def test_attach_rate_is_a_share_of_orders_not_of_rows():
    """4 orders, 1 multi-item -> exactly 25%. Through the real pipeline.

    This is the figure an upsell binding on view 58 would have republished,
    demonstrated end to end where it actually lives: line facts -> basket job
    -> view 57's envelope. Hand-checked: one basket holds three distinct
    products, three baskets hold one, so the attach rate is 1/4 = 25%.

    The three-product basket is what arms the trap. It writes THREE pair rows
    for the day, each repeating the bucket scalars, so every wrong shape is a
    different number: a resolver that counted pair rows as multi-item orders
    reports 75% (3 of 4), and one that summed the repeated scalars straight
    across rows reports 3/12 — plausible, and wrong. Only the share of ORDERS
    is 25%, to four places, exactly.
    """
    with sandbox() as (db, generation):
        _basket(db, generation, DAY_ATTACH, [P_MAIN, P_ADDON, P_EXTRA])
        _basket(db, generation, DAY_ATTACH, [P_SOLO])
        _basket(db, generation, DAY_ATTACH, [P_MAIN])
        _basket(db, generation, DAY_ATTACH, [P_ADDON])
        db.commit()

        BasketPairDailyJob().run(db, DAY_ATTACH, generation)
        db.commit()

        # The trap is armed: more pair rows than multi-item orders.
        rows = list(
            db.execute(
                select(AggBasketPairDaily).where(
                    AggBasketPairDaily.bucket_date == DAY_ATTACH,
                    AggBasketPairDaily.tz_generation == generation,
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 3, (
            "the fixture must produce three pair rows from ONE basket, or the "
            "rows-vs-orders distinction below is not being exercised"
        )
        assert all(row.total_orders_in_bucket == 4 for row in rows)
        assert all(row.orders_with_any_pair == 1 for row in rows)

        view = registry.get_view("products", "product-bundling-and-cross-sell")
        assert view is not None
        service = AnalyticsViewService(db, _AnalyticsReader(view.permission))
        envelope = service.resolve_view(
            "products",
            "product-bundling-and-cross-sell",
            _filters(DAY_ATTACH, DAY_ATTACH + timedelta(days=1)),
            use_cache=False,
        )
        assert isinstance(envelope, AnalyticsViewEnvelope)

        attach = envelope.kpis["basket_attach_rate"].value
        assert attach == Decimal("25.0000"), (
            f"attach rate came back {attach}: 75 means pair rows were counted "
            "as orders, anything else means the repeated bucket scalars were "
            "summed across a day's rows"
        )
