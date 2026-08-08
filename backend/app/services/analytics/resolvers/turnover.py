"""The Inventory Turnover resolver — view 30, the read the `avg:` projection unlocked.

Registers one custom function, ``inventory_turnover``, reached from the registry
with ``resolver=ResolverId.CUSTOM`` and ``params={"fn": "inventory_turnover"}``.

The formula, and why neither generic resolver could compute it
--------------------------------------------------------------
::

    turnover          = COGS in window / average inventory value over window
    days_of_inventory = days in window / turnover        (None when turnover
                                                          is 0 or unavailable)

The numerator lives in ``agg_order_daily`` (``cogs_sum`` — the same
``order_items.unit_cost`` snapshot basis the margin engine uses, with
``costed_units / units`` as its coverage) and the denominator in
``agg_inventory_daily`` (``stock_value_close``, a LEVEL). A ``MetricBinding``
names one source and the repository's SUM cannot produce a mean of a level, so
this view stayed unbound until the repository grew ``avg:<column>`` — which
refuses anything that is not a LEVEL, exactly the guard this denominator needs.

What is deliberately NOT computed:

* **units-sold / latest-stock.** A level pinned at the window end understates
  turnover after a restock and overstates it after a sellout; the average is
  the entire point, and the test suite asserts the two answers differ.
* **A zero for a missing input.** Division by zero, an empty window, or zero
  cost coverage all come back as ``None`` with ``inputs_missing`` named. A
  turnover of 0 is reserved for the one thing it means: nothing sold.

The average's empty-bucket semantics, surfaced rather than assumed
------------------------------------------------------------------
``avg:`` is documented as **a mean over the rows that EXIST, not over calendar
days**: a day with no ledger row contributes to neither the numerator nor the
denominator — it is "no data", never "zero stock". This resolver follows that
per product (``avg:stock_value_close`` grouped by ``product_id`` is each
product's mean over ITS observed days) and the store-wide denominator is the
SUM of those per-product means — with a complete ledger this equals the mean of
the daily stock-value totals exactly, and with gaps it errs the conservative
direction (a denominator deflated by phantom zero-stock days would report
*higher* turnover, the flattering error nobody investigates). The
``AVG_OVER_OBSERVED_DAYS`` warning states this on every answer, with the
observed-day count against the window length.

Quality — why this view can never be better than ESTIMATED
----------------------------------------------------------
``stock_value_close`` values the closing stock at the product's CURRENT cost
(the aggregation job reads ``products.cost``, which has no history), so the
denominator is an estimate however complete the ledger is. COGS coverage below
100% additionally makes the figure INCOMPLETE, with the percentage carried on
the card. Quality rolls up worst-component, as everywhere else.

The view stays **PARTIAL**: the ledger is forward-only (history began the day
it was switched on and cannot be backfilled) and the valuation basis is current
cost. Both are stated in the registry ``limitation``.

The by-category chart and filter cannot be honoured: the ledger is keyed by
product and carries no category column, and no rollup joins stock to a
category. Both are reported as ``DIMENSION_NOT_STORED`` rather than regrouped
by something adjacent — the same refusal views 28/29 make on the same table.
The per-product table is the split that IS honest, ranked slow movers first,
because "what is not turning" is what an operator opens this view for.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Mapping

from app.repositories.analytics_repository import AVG_PREFIX, COUNT_ROWS
from app.schemas.analytics_view import AnalyticsWarning, TableBlock, WarningCode
from app.services.analytics.export import clamp_row_limit
from app.services.analytics.filters import ResolvedWindow
from app.services.analytics.resolvers.base import (
    ResolverContext,
    ResolverResult,
    build_kpi,
    missing_kpi,
    probe_source,
    warn,
)
from app.services.analytics.resolvers.core import compute_kpis
from app.services.analytics.resolvers.levels import (
    DIMENSION_NOT_STORED,
    INVENTORY_SOURCE,
    LEVEL_AT_INSTANT,
    latest_bucket_in,
)
from app.services.analytics.resolvers.special import custom_function
from app.services.analytics.types import MetricQuality

__all__ = [
    "AVG_OVER_OBSERVED_DAYS",
    "ORDER_SOURCE",
    "PRODUCT_SOURCE",
    "AVG_STOCK_VALUE",
    "inventory_turnover",
]

#: COGS and its coverage pair, on the same unit_cost-snapshot basis margin uses.
ORDER_SOURCE = "agg_order_daily"
#: Per-product COGS (`line_cost`) and per-product coverage, for the table.
PRODUCT_SOURCE = "agg_product_daily"

#: The projection the whole view exists around: the mean of a LEVEL over the
#: rows that exist. Spelled once; the repository validates it per query.
AVG_STOCK_VALUE = AVG_PREFIX + "stock_value_close"

#: Emitted on every computed answer. The denominator is a mean over the ledger
#: days that EXIST — a gap day is in neither the numerator nor the denominator,
#: because the ledger is forward-only and an absent bucket means "no data",
#: not "held no stock". `WarningCode` documents codes as plain strings so a new
#: one needs no migration and no coordinated frontend release.
AVG_OVER_OBSERVED_DAYS = "AVG_OVER_OBSERVED_DAYS"

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
#: Four places on a ratio (matches FormatId.RATIO usage elsewhere), two on days
#: and money, so equal rows display as equal.
_RATIO_Q = Decimal("0.0001")
_DAYS_Q = Decimal("0.01")
_MONEY_Q = Decimal("0.01")
_PCT_Q = Decimal("0.01")

#: Table sort keys a client may ask for. `inventory_turnover` and
#: `days_of_inventory` are computed here and exist in no table, so the
#: repository's allowlist cannot vet them; this set is where the equivalent
#: check lives (same pattern as resolvers/basket.py).
_SORTABLE = frozenset(
    {
        "inventory_turnover",
        "days_of_inventory",
        "units_sold",
        "cogs",
        "avg_stock_value",
        "days_observed",
    }
)


def _int(value: Any) -> int:
    return int(value or 0)


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _coverage_pct(costed: Any, units: Any) -> Decimal | None:
    """`costed_units / units` as a percentage, or None when it is vacuous.

    None for a window with no units at all — coverage of an empty population is
    not a measurement (the same call `margin._coverage` makes), and 0% here
    would wrongly convert "nothing sold" into "nothing costed".
    """
    if units is None or _int(units) <= 0:
        return None
    return (Decimal(_int(costed)) / Decimal(_int(units)) * _HUNDRED).quantize(_PCT_Q)


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _WindowMaths:
    """Everything one window's turnover is computed from, measured once."""

    #: DISTINCT ledger days inside the window. 0 means the window is empty of
    #: inventory data and nothing below it may be computed.
    ledger_days: int
    #: One row per product: `avg:stock_value_close`, summed `units_sold`, and
    #: `row_count` (the days THAT product was observed on).
    products: tuple[dict, ...]
    #: Σ of the per-product means — the window's average inventory value.
    #: None when there are no ledger rows; never invented as 0.
    avg_inventory_value: Decimal | None
    #: The ledger's own movement total, used only to tell "zero sales" (a real
    #: claim) from "the order rollup has not aggregated" (a missing input).
    ledger_units_sold: int
    # -- agg_order_daily totals; all None when the window has no order rows --
    cogs: Decimal | None
    units: int | None
    costed_units: int | None


@dataclass(frozen=True)
class _Outcome:
    """A computed figure plus everything needed to grade it honestly."""

    value: Decimal | None = None
    coverage_pct: Decimal | None = None
    inputs_missing: tuple[str, ...] = ()
    quality: MetricQuality | None = None


def _measure_window(ctx: ResolverContext, window: ResolvedWindow) -> _WindowMaths:
    """Read one window's inputs. Three reads when the ledger has data, one when not."""
    # One row per ledger day that EXISTS in the window; their count is the
    # observed-day denominator the AVG semantics warning reports. Grouped
    # rather than projected as an ungrouped `count_distinct:` because the
    # repository's deterministic-order default would attach a non-aggregated
    # ORDER BY to an aggregate-only projection, which ONLY_FULL_GROUP_BY
    # rejects — and a grouped read is the same number with a real key to
    # order on.
    day_rows = ctx.repo.fetch_rollup(
        INVENTORY_SOURCE,
        columns=[COUNT_ROWS],
        window=window,
        tz_generation=ctx.tz_generation,
        group_by=["bucket_date"],
    )
    ledger_days = len(day_rows)
    if ledger_days == 0:
        return _WindowMaths(0, (), None, 0, None, None, None)

    # Per product: the mean of its level over the days it has rows (the
    # documented `avg:` semantics — a gap day is in neither numerator nor
    # denominator), its movement, and how many days it was observed on.
    products = ctx.repo.fetch_rollup(
        INVENTORY_SOURCE,
        columns=[AVG_STOCK_VALUE, "units_sold", COUNT_ROWS],
        window=window,
        tz_generation=ctx.tz_generation,
        group_by=["product_id"],
    )
    # Σ of per-product means. With a complete ledger this IS the mean of the
    # daily totals (Σ_p (Σ_d v / D) == (1/D) Σ_d Σ_p v); with per-product gaps
    # it is the conservative generalisation — each product averaged over its
    # own observed days, never zero-filled, so the denominator cannot be
    # deflated into a flattering turnover.
    avg_value = sum(
        (_dec(row[AVG_STOCK_VALUE]) or _ZERO for row in products), _ZERO
    ).quantize(_MONEY_Q)
    ledger_sold = sum(_int(row["units_sold"]) for row in products)

    totals = ctx.repo.fetch_totals(
        ORDER_SOURCE,
        columns=["cogs_sum", "units", "costed_units"],
        window=window,
        tz_generation=ctx.tz_generation,
    )
    units = totals["units"]
    return _WindowMaths(
        ledger_days=ledger_days,
        products=tuple(products),
        avg_inventory_value=avg_value,
        ledger_units_sold=ledger_sold,
        cogs=_dec(totals["cogs_sum"]),
        units=None if units is None else _int(units),
        costed_units=None if totals["costed_units"] is None else _int(totals["costed_units"]),
    )


def _cogs_outcome(
    *,
    sold: int,
    cogs: Decimal | None,
    units: int | None,
    costed_units: int | None,
) -> _Outcome:
    """The window's COGS, graded. Shared by the store KPI and every table row.

    The rules, in order, each returning rather than falling through:

    * No order-side rows at all: a ledger that measured zero movement makes 0 a
      real COGS (nothing sold incurs no cost); a ledger that measured sales
      with nothing on the order side is a missing input, not a zero.
    * Order rows with zero units: the same split, judged from the other side.
    * Zero cost coverage: COGS is unknowable, not zero — `value_minor=None`
      territory, exactly as cost_rules and margin treat it.
    * Partial coverage: the summed `cogs_sum`/`line_cost` covers only the
      costed lines, so the figure is INCOMPLETE with the percentage carried.
    * Full coverage: ESTIMATED, never better — the turnover it feeds divides by
      a denominator valued at current cost.
    """
    if units is None:
        if sold == 0:
            return _Outcome(value=_ZERO, quality=MetricQuality.ESTIMATED)
        return _Outcome(
            inputs_missing=(f"{ORDER_SOURCE} rows covering this window's sales",)
        )
    if units == 0:
        if sold > 0:
            return _Outcome(
                inputs_missing=(
                    f"consistent rollups ({INVENTORY_SOURCE} records "
                    f"{sold} unit(s) sold; {ORDER_SOURCE} records none)",
                )
            )
        return _Outcome(value=_ZERO, quality=MetricQuality.ESTIMATED)

    coverage = _coverage_pct(costed_units, units)
    if coverage == 0:
        return _Outcome(coverage_pct=coverage, inputs_missing=("unit_cost",))
    quality = (
        MetricQuality.INCOMPLETE
        if coverage is not None and coverage < _HUNDRED
        else MetricQuality.ESTIMATED
    )
    return _Outcome(value=cogs or _ZERO, coverage_pct=coverage, quality=quality)


def _turnover_outcome(m: _WindowMaths) -> _Outcome:
    """COGS over average inventory value, or None with the gap named."""
    if m.ledger_days == 0 or m.avg_inventory_value is None:
        return _Outcome(
            inputs_missing=(f"{INVENTORY_SOURCE} rows inside the requested window",)
        )
    cogs = _cogs_outcome(
        sold=m.ledger_units_sold,
        cogs=m.cogs,
        units=m.units,
        costed_units=m.costed_units,
    )
    if cogs.value is None:
        return cogs
    if m.avg_inventory_value == 0:
        # Division by zero. Stock was held at zero value on every observed day,
        # so "how often did it turn" has no finite answer — None, never a
        # number, and never infinity dressed as one.
        return _Outcome(
            coverage_pct=cogs.coverage_pct,
            inputs_missing=("a non-zero average inventory value to divide by",),
        )
    return _Outcome(
        value=(cogs.value / m.avg_inventory_value).quantize(_RATIO_Q),
        coverage_pct=cogs.coverage_pct,
        quality=cogs.quality,
    )


def _days_outcome(turnover: _Outcome, days_in_window: int) -> _Outcome:
    """days in window / turnover. None when turnover is 0 or unavailable.

    Zero sales has no finite days-of-cover — the stock lasts indefinitely at
    this rate — and 0 would claim the opposite (no cover at all). Infinity is
    not a number the envelope can carry honestly, so the card is None with the
    reason named.
    """
    if turnover.value is None:
        return _Outcome(
            coverage_pct=turnover.coverage_pct,
            inputs_missing=turnover.inputs_missing,
        )
    if turnover.value == 0:
        return _Outcome(
            coverage_pct=turnover.coverage_pct,
            inputs_missing=(
                "a non-zero turnover (nothing sold in this window, so days of "
                "cover is unbounded, not zero)",
            ),
        )
    return _Outcome(
        value=(Decimal(days_in_window) / turnover.value).quantize(_DAYS_Q),
        coverage_pct=turnover.coverage_pct,
        quality=turnover.quality,
    )


# ---------------------------------------------------------------------------
# The stock_value card — a LEVEL, pinned to the latest ledger day in window
# ---------------------------------------------------------------------------


def _stock_value_at(ctx: ResolverContext, instant: date | None) -> Decimal | None:
    """Total stock value at one instant: SUM across products on ONE day.

    The legitimate sum on this table — every product appears exactly once at a
    single `bucket_date`, so this partitions the position rather than
    re-counting it. Summing across days is the wrong number this resolver
    exists to avoid.
    """
    if instant is None:
        return None
    rows = ctx.repo.fetch_rollup(
        INVENTORY_SOURCE,
        columns=["stock_value_close"],
        window=_one_day(instant),
        tz_generation=ctx.tz_generation,
        group_by=["bucket_date"],
        filters={"bucket_date": instant},
    )
    return _dec(rows[0]["stock_value_close"]) if rows else None


def _one_day(day: date) -> ResolvedWindow:
    """The half-open window holding exactly `day`."""
    return ResolvedWindow(date_from=day, date_to=date.fromordinal(day.toordinal() + 1))


def _compare_window(window: ResolvedWindow) -> ResolvedWindow | None:
    if not window.has_comparison:
        return None
    return ResolvedWindow(
        date_from=window.compare_from,  # type: ignore[arg-type]
        date_to=window.compare_to,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Per-product table — slow movers first
# ---------------------------------------------------------------------------


def _product_costs(ctx: ResolverContext, window: ResolvedWindow) -> dict[int, dict]:
    """product_id -> summed (line_cost, units, costed_units) over the window.

    From `agg_product_daily`, the per-product restatement of the same
    unit_cost-snapshot COGS `agg_order_daily` totals — "Sum of (unit_cost *
    qty) over lines that had a known unit cost", with `costed_units` kept so
    coverage stays computable per product rather than assumed from the store's.
    """
    rows = ctx.repo.fetch_rollup(
        PRODUCT_SOURCE,
        columns=["line_cost", "units", "costed_units"],
        window=window,
        tz_generation=ctx.tz_generation,
        group_by=["product_id"],
    )
    return {_int(row["product_id"]): row for row in rows}


def _latest_skus(ctx: ResolverContext, instant: date | None) -> dict[int, str]:
    """product_id -> sku_snapshot at the pinned instant, for row labels.

    Read at ONE bucket (the table's UNIQUE key guarantees one row per product
    there) rather than grouped over the window, where a mid-window SKU rename
    would split every figure for that product across two half-rows.
    """
    if instant is None:
        return {}
    rows = ctx.repo.fetch_rollup(
        INVENTORY_SOURCE,
        columns=["product_id", "sku_snapshot"],
        window=_one_day(instant),
        tz_generation=ctx.tz_generation,
        group_by=["product_id", "sku_snapshot"],
        filters={"bucket_date": instant},
    )
    return {_int(row["product_id"]): str(row["sku_snapshot"] or "") for row in rows}


def _describe_product(
    ledger_row: Mapping[str, Any],
    costs: Mapping[int, Mapping[str, Any]],
    skus: Mapping[int, str],
    days_in_window: int,
) -> dict[str, Any]:
    """One product's turnover row, by exactly the store-level rules."""
    product_id = _int(ledger_row["product_id"])
    avg_value = _dec(ledger_row[AVG_STOCK_VALUE]) or _ZERO
    sold = _int(ledger_row["units_sold"])
    days_observed = _int(ledger_row[COUNT_ROWS])
    cost_row = costs.get(product_id)

    maths = _WindowMaths(
        ledger_days=days_observed,
        products=(),
        avg_inventory_value=avg_value.quantize(_MONEY_Q),
        ledger_units_sold=sold,
        cogs=_dec(cost_row["line_cost"]) if cost_row else None,
        units=_int(cost_row["units"]) if cost_row else None,
        costed_units=_int(cost_row["costed_units"]) if cost_row else None,
    )
    turnover = _turnover_outcome(maths)
    days = _days_outcome(turnover, days_in_window)

    return {
        "product": product_id,
        "sku": skus.get(product_id, ""),
        "units_sold": sold,
        "cogs": maths.cogs,
        "avg_stock_value": maths.avg_inventory_value,
        "inventory_turnover": turnover.value,
        "days_of_inventory": days.value,
        "cost_coverage_pct": turnover.coverage_pct,
        "days_observed": days_observed,
        # Working state, stripped before the block is built.
        "_incomplete": turnover.value is None,
        "_low_coverage": (
            turnover.coverage_pct is not None and turnover.coverage_pct < _HUNDRED
        ) or (turnover.value is None and "unit_cost" in turnover.inputs_missing),
    }


def _sort(ctx: ResolverContext, warnings: list[AnalyticsWarning]) -> tuple[str, bool]:
    """(key, descending). Default: turnover ASCENDING — slow movers first.

    A top-N by turnover descending under this view's heading would celebrate
    the healthiest stock; the operator opened it for what is NOT turning.
    """
    requested = (ctx.filters.sort or "").strip()
    if not requested:
        return "inventory_turnover", False
    if requested not in _SORTABLE:
        warnings.append(
            warn(
                WarningCode.SMALL_SAMPLE,
                f"Cannot sort the turnover table by {requested!r}; it is ranked "
                f"slowest-turning first instead. Sortable: {', '.join(sorted(_SORTABLE))}.",
                severity="info",
                requested_sort=requested,
            )
        )
        return "inventory_turnover", False
    return requested, (ctx.filters.sort_dir or "desc") != "asc"


def _sort_value(row: dict, key: str) -> tuple:
    """Total order: the key, cash-at-stake as tiebreak, product id last.

    The tiebreak is negated so that in the default ascending (slowest-first)
    order, two equally slow products list the one holding MORE stock value
    first — that is where the cash is trapped. The product id makes the order
    total, so a page boundary cannot shuffle rows between requests.
    """
    primary = row.get(key)
    if primary is None:
        # A None never ranks; the _incomplete pin below puts the whole row at
        # the bottom regardless of the requested sort.
        primary = _ZERO
    tiebreak = row["avg_stock_value"] or _ZERO
    return (primary, -tiebreak, row["product"])


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


def _category_warnings(ctx: ResolverContext) -> list[AnalyticsWarning]:
    """The split and the filter this view cannot honour, named per request.

    The ledger is keyed by product with no category column, and no rollup holds
    both a stock level and a category. The chart is left undrawn and a supplied
    category filter is reported as un-applicable rather than silently ignored —
    figures under a filter that was not applied are the quiet version of a
    wrong number.
    """
    warnings: list[AnalyticsWarning] = []
    for chart in ctx.view.charts or ():
        if chart.x != "category":
            continue
        warnings.append(
            warn(
                DIMENSION_NOT_STORED,
                f"This view's chart groups by 'category', which {INVENTORY_SOURCE} "
                "does not carry — the ledger is keyed by product and no rollup "
                "joins a stock level to a category. The breakdown is left out "
                "rather than regrouped by something adjacent; the per-product "
                "table below is the split the ledger can answer.",
                severity="warn",
                source=INVENTORY_SOURCE,
                dimension="category",
                chart=chart.id,
                view=ctx.view.slug,
            )
        )
    if ctx.filters.category_id is not None:
        warnings.append(
            warn(
                DIMENSION_NOT_STORED,
                "The category filter cannot be applied to inventory turnover: "
                f"{INVENTORY_SOURCE} stores no category, so filtering the COGS "
                "side alone would divide one category's cost by the whole "
                "catalogue's stock. Every figure here covers the full catalogue.",
                severity="warn",
                source=INVENTORY_SOURCE,
                dimension="category",
                filter="category_id",
                view=ctx.view.slug,
            )
        )
    return warnings


def _avg_semantics_warning(days_in_window: int, m: _WindowMaths) -> AnalyticsWarning:
    return warn(
        AVG_OVER_OBSERVED_DAYS,
        "Average inventory value is the mean over the ledger days that exist: "
        f"{m.ledger_days} of the window's {days_in_window} day(s). A day with "
        "no ledger row counts in neither the numerator nor the denominator — "
        "the ledger is forward-only, so an absent day is 'no data', not 'held "
        "no stock', and zero-filling it would inflate turnover. Stock is "
        "valued at current cost, so the average is an estimate.",
        severity="info",
        source=INVENTORY_SOURCE,
        days_in_window=days_in_window,
        ledger_days_observed=m.ledger_days,
    )


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------


@custom_function("inventory_turnover")
def inventory_turnover(ctx: ResolverContext) -> ResolverResult:
    """COGS over average stock held, per the module docstring."""
    inv_state = probe_source(
        ctx, INVENTORY_SOURCE, label="Inventory ledger (per-product close of day)"
    )
    order_state = probe_source(
        ctx, ORDER_SOURCE, label="Daily order rollup (COGS from unit_cost snapshots)"
    )
    warnings: list[AnalyticsWarning] = [*inv_state.warnings, *order_state.warnings]
    warnings.extend(_category_warnings(ctx))

    declared = set(ctx.view.kpis)
    spec = ctx.view.tables[0] if ctx.view.tables else None
    days_in_window = (ctx.window.date_to - ctx.window.date_from).days

    now = _measure_window(ctx, ctx.window)

    if now.ledger_days == 0:
        return _nothing_in_window(ctx, declared, spec, warnings, [inv_state.ref, order_state.ref])

    warnings.append(_avg_semantics_warning(days_in_window, now))

    compare = _compare_window(ctx.window)
    prev = _measure_window(ctx, compare) if compare is not None else None
    prev_days = (compare.date_to - compare.date_from).days if compare is not None else 0

    turnover = _turnover_outcome(now)
    days = _days_outcome(turnover, days_in_window)
    prev_turnover = _turnover_outcome(prev) if prev is not None else _Outcome()
    prev_days_of = (
        _days_outcome(prev_turnover, prev_days) if prev is not None else _Outcome()
    )

    if turnover.coverage_pct is not None and turnover.coverage_pct < _HUNDRED:
        warnings.append(
            warn(
                WarningCode.COST_COVERAGE_LOW,
                f"Only {turnover.coverage_pct}% of units sold in this window carry "
                "a unit_cost snapshot. COGS — and therefore turnover — is computed "
                "from the costed lines only and is INCOMPLETE, not adjusted.",
                severity="warn",
                coverage_pct=str(turnover.coverage_pct),
                source=ORDER_SOURCE,
            )
        )

    kpis: dict[str, Any] = {}
    if "inventory_turnover" in declared:
        kpis["inventory_turnover"] = build_kpi(
            "inventory_turnover",
            turnover.value,
            previous=prev_turnover.value,
            coverage_pct=turnover.coverage_pct,
            inputs_missing=turnover.inputs_missing,
            quality=turnover.quality,
        )
    if "days_of_inventory" in declared:
        kpis["days_of_inventory"] = build_kpi(
            "days_of_inventory",
            days.value,
            previous=prev_days_of.value,
            coverage_pct=days.coverage_pct,
            inputs_missing=days.inputs_missing,
            quality=days.quality,
        )

    instant = latest_bucket_in(ctx, INVENTORY_SOURCE, ctx.window)
    if "stock_value" in declared:
        value = _stock_value_at(ctx, instant)
        previous_instant = (
            latest_bucket_in(ctx, INVENTORY_SOURCE, compare) if compare else None
        )
        kpis["stock_value"] = build_kpi(
            "stock_value",
            value,
            previous=_stock_value_at(ctx, previous_instant),
            inputs_missing=()
            if value is not None
            else (f"a {INVENTORY_SOURCE} row inside the window",),
            # Valued at CURRENT cost — an estimate of the position, never
            # AUTHORITATIVE, however complete the ledger.
            quality=MetricQuality.ESTIMATED,
        )
        if instant is not None:
            warnings.append(
                warn(
                    LEVEL_AT_INSTANT,
                    f"Stock value is a position, measured at {instant.isoformat()} "
                    "— the latest ledger day inside the window, not a sum of the "
                    "window's days. Turnover's denominator is the window AVERAGE, "
                    "which is a different (and deliberately different) number.",
                    severity="info",
                    source=INVENTORY_SOURCE,
                    as_at=instant.isoformat(),
                )
            )

    # `units_sold` keeps its catalogue binding (agg_order_daily.units) so this
    # card cannot disagree with the same card on views 27/28/29.
    bundle = compute_kpis(ctx, [k for k in ctx.view.kpis if k == "units_sold"])
    kpis.update(bundle.kpis)
    warnings.extend(bundle.warnings)

    tables: dict[str, TableBlock] = {}
    if spec is not None:
        tables[spec.id] = _product_table(ctx, now, days_in_window, instant, warnings)

    sources = _merge_refs([inv_state.ref, order_state.ref], bundle.sources)
    return ResolverResult(
        kpis=kpis,
        tables=tables,
        sources=sources,
        warnings=warnings,
        # The ceiling, not the verdict: stock is valued at current cost, so
        # nothing on this view is better than ESTIMATED. `rolled_up` worsens
        # this to INCOMPLETE the moment any card is.
        quality=MetricQuality.ESTIMATED,
        coverage_pct=turnover.coverage_pct,
    ).rolled_up()


def _product_table(
    ctx: ResolverContext,
    now: _WindowMaths,
    days_in_window: int,
    instant: date | None,
    warnings: list[AnalyticsWarning],
) -> TableBlock:
    costs = _product_costs(ctx, ctx.window)
    skus = _latest_skus(ctx, instant)
    rows = [
        _describe_product(row, costs, skus, days_in_window) for row in now.products
    ]

    low_coverage = [r for r in rows if r["_low_coverage"]]
    if low_coverage:
        warnings.append(
            warn(
                WarningCode.COST_COVERAGE_LOW,
                f"{len(low_coverage)} of {len(rows)} product(s) have unit_cost "
                "snapshots on fewer than all of their sold lines; their turnover "
                "is computed from the costed lines only (or withheld outright at "
                "zero coverage), never padded with a guessed cost.",
                severity="warn",
                products_below_full_coverage=len(low_coverage),
                products_total=len(rows),
            )
        )
    gaps = [r for r in rows if r["days_observed"] < now.ledger_days]
    if gaps:
        warnings.append(
            warn(
                WarningCode.SMALL_SAMPLE,
                f"{len(gaps)} of {len(rows)} product(s) have ledger rows on fewer "
                f"than the window's {now.ledger_days} observed day(s); their "
                "average stock value rests on the days they were measured, and "
                "days_observed on each row says how many that is.",
                severity="info",
                products_with_gaps=len(gaps),
                ledger_days_observed=now.ledger_days,
            )
        )

    sort_key, descending = _sort(ctx, warnings)
    rows.sort(key=lambda row: _sort_value(row, sort_key), reverse=descending)
    # Rows whose turnover could not be computed are pinned BELOW every row
    # whose turnover could: an unknown is not a ranking, and "slowest first"
    # must not open on a product whose real problem is a missing cost.
    rows.sort(key=lambda row: row["_incomplete"])

    limit = clamp_row_limit(ctx.filters.limit, default=25)
    offset = int(ctx.filters.offset or 0)
    page = rows[offset : offset + limit]
    return TableBlock(
        rows=[{k: v for k, v in row.items() if not k.startswith("_")} for row in page],
        total_rows=len(page),
        truncated=len(rows) > offset + limit,
    )


def _nothing_in_window(
    ctx: ResolverContext,
    declared: set[str],
    spec: Any,
    warnings: list[AnalyticsWarning],
    refs: list,
) -> ResolverResult:
    """No ledger day inside the window: say so, and invent nothing.

    Mirrors `SnapshotResolver._nothing_in_window`: the inventory-bound cards
    come back None with the gap named, cards bound elsewhere are still
    computed, and no table row or zero is manufactured.
    """
    # Dedupe on the DETAIL, not the code. When the rollup has never been built
    # at all, `probe_source` has already appended a generic NO_ROLLUP_YET — but
    # "this table has never been aggregated" and "your requested window holds no
    # measured day" are different statements, and the second carries the
    # `requested_from` bound that tells the reader which days are unmeasured.
    # Deduping on the bare code suppressed the window warning exactly when the
    # table was empty, so the answer got LESS specific as the data got thinner.
    already_named = any(
        w.code == WarningCode.NO_ROLLUP_YET
        and w.detail.get("requested_from") == ctx.window.date_from.isoformat()
        for w in warnings
    )
    if not already_named:
        warnings.append(
            warn(
                WarningCode.NO_ROLLUP_YET,
                f"{INVENTORY_SOURCE} holds no ledger day between "
                f"{ctx.window.date_from.isoformat()} and "
                f"{ctx.window.date_to.isoformat()}, so there is no average "
                "inventory to divide by. This is 'not measured', not 'zero "
                "stock' — the ledger is forward-only and days before it began "
                "cannot be reconstructed.",
                severity="warn",
                source=INVENTORY_SOURCE,
                requested_from=ctx.window.date_from.isoformat(),
                requested_to=ctx.window.date_to.isoformat(),
            )
        )
    reason = f"no {INVENTORY_SOURCE} rows inside the requested window"
    kpis = {
        kpi_id: missing_kpi(kpi_id, reason)
        for kpi_id in ("inventory_turnover", "days_of_inventory", "stock_value")
        if kpi_id in declared
    }
    bundle = compute_kpis(ctx, [k for k in ctx.view.kpis if k == "units_sold"])
    kpis.update(bundle.kpis)
    warnings.extend(bundle.warnings)

    tables = (
        {spec.id: TableBlock(rows=[], total_rows=0, truncated=False)}
        if spec is not None
        else {}
    )
    return ResolverResult(
        kpis=kpis,
        tables=tables,
        sources=_merge_refs(refs, bundle.sources),
        warnings=warnings,
        quality=MetricQuality.INCOMPLETE,
    ).rolled_up()


def _merge_refs(*groups) -> list:
    out = []
    seen: set[str] = set()
    for group in groups:
        for ref in group:
            if ref.id in seen:
                continue
            seen.add(ref.id)
            out.append(ref)
    return out
