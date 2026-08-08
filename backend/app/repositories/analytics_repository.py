"""The only module that executes analytics rollup queries.

**This file is the boundary that makes the whole analytics API safe against a
client-supplied identifier.** Everything above it — the view endpoint, the
resolvers, `AnalyticsFilters` — deals in labels chosen by the server-side
registry. This is the one place where a name becomes a table, a column, a GROUP
BY key or an ORDER BY term, and it is therefore the one place where getting it
wrong would turn `?sort=` into arbitrary SQL.

The rule, without exception:

* `source`, `columns`, `group_by` and `order_by` are looked up in a per-source
  **allowlist reflected from the ORM models at import time**. A name that is not
  a real mapped column of that exact model raises `ValueError` before any
  statement is built. Nothing is ever interpolated, concatenated or `text()`-ed
  into SQL — the lookup returns the mapped `InstrumentedAttribute` itself, so
  the identifier that reaches the database was written in `analytics_rollups.py`,
  never in a request.
* Filter **values** are bound as query parameters by SQLAlchemy. A value can be
  hostile; it can never be an identifier.
* `"1; DROP TABLE agg_order_daily"` is not a column of anything, so it raises
  rather than executing. That is the property the tests assert directly.

Layering (see `.claude/docs/architecture-rules.md` §1): resolvers shape data,
this repository runs SQL. Resolvers must not execute queries. The three older
analytics services (`kpis`, `margin`, `allocation`) call `select()` directly,
contrary to that rule; do not propagate the pattern — this repository is how the
new subsystem stays compliant.

What this module deliberately does **not** do:

* It does not decide what is additive. `SUM(aov)` is an average of averages and
  `SUM(stock_close)` sums a level, and both are allowed here because the
  allowlist is a *safety* boundary, not a semantic one. Additivity is documented
  per column in `analytics_rollups.py` and is the resolver's responsibility.

  The `avg:<column>` projection is the one deliberate exception, and it is an
  exception because the shapes differ: a SUM has correct callers for both flows
  and levels depending on what the resolver is asking, whereas `AVG(<flow>)` has
  no correct caller at all — averaging daily sums produces a figure that changes
  when the same period is re-bucketed. So `avg:` asks `metric_kind.classify` and
  refuses anything that is not a LEVEL, rather than building a wrong number and
  trusting every future caller to know not to ask for it.
* It does not return a truth value where a number was asked for. `func.sum()`
  inherits its column's type, so `SUM(<Boolean column>)` used to come back as
  `True` rather than a count — see `_sum`, which casts before summing so no
  present or future rollup flag can regress to that.
* It does not invent zeros. `fetch_totals` over an empty window returns `None`
  per column, and `source_watermark` returns `(None, 0)` — "no rows yet" stays
  distinguishable from "genuinely zero", which is the same rule the rest of the
  analytics stack follows.
* It does not silently truncate. A result larger than `HARD_ROW_CAP` raises,
  because a quietly shortened result set leaves a hole in a chart that nobody
  can trace back to its cause (`filters.py` makes the same call about windows).
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from sqlalchemy import (
    Integer,
    Select,
    cast,
    distinct,
    func,
    inspect as sa_inspect,
    select,
)
from sqlalchemy.orm import InstrumentedAttribute, Session
from sqlalchemy.sql.schema import UniqueConstraint

from app.models.analytics_basket import AggBasketPairDaily
from app.models.analytics_cx import AggCxDaily
from app.models.analytics_ga4 import AggGa4Daily
from app.models.analytics_loyalty import AggLoyaltyDaily
from app.models.analytics_rollups import (
    AggCustomerCohortMonthly,
    AggCustomerDaily,
    AggCustomerSnapshot,
    AggFunnelDaily,
    AggGeoDaily,
    AggInventoryDaily,
    AggOrderDaily,
    AggOrderHourly,
    AggPaymentDaily,
    AggProductDaily,
    AggPromoDaily,
    AggShipmentDaily,
)
from app.models.analytics_settlement import AggSettlementDaily
from app.services.analytics.filters import ResolvedWindow
from app.services.analytics.metric_kind import assert_averagable

logger = logging.getLogger(__name__)

#: A single analytics read must never be able to pull an unbounded result set
#: into memory. 400 days x 24 hours is 9,600 rows, which is already beyond what
#: any view renders, so anything past this is a bug in the caller rather than a
#: legitimate request. Exceeding it raises — it is NOT truncated.
HARD_ROW_CAP = 5000

#: Pseudo-columns a caller may project that are aggregates over ROWS rather than
#: over a stored measure.
#:
#: These are deliberately NOT part of the per-source column allowlist: they name
#: no column, so there is nothing for a client-supplied string to reach. The
#: distinct variant still resolves its argument through the allowlist, so
#: `count_distinct:<col>` is exactly as safe as projecting `<col>` directly.
COUNT_ROWS = "row_count"
COUNT_DISTINCT_PREFIX = "count_distinct:"

#: `avg:<column>` — the MEAN of a stored column over the matched rows.
#:
#: Needed because there are questions a SUM cannot answer at all. Inventory
#: Turnover is COGS over *average* stock held, and `stock_close` is a LEVEL:
#: summing it counts the same units once per day, and taking the latest bucket
#: answers "what do we hold now", not "what did we hold on average". Neither
#: existing projection can produce the denominator.
#:
#: Unlike the count projections this one names a real column, so its argument
#: goes through the same allowlist and the same `measures` check as a SUM — and
#: then through `metric_kind.assert_averagable`, which refuses anything that is
#: not a LEVEL. See `_avg` for why that guard is not optional.
AVG_PREFIX = "avg:"


def _is_count_projection(name: str) -> bool:
    return name == COUNT_ROWS or name.startswith(COUNT_DISTINCT_PREFIX)


def _is_avg_projection(name: Any) -> bool:
    return isinstance(name, str) and name.startswith(AVG_PREFIX)

#: Columns present on every rollup that are bookkeeping, not data.
_META_COLUMNS = frozenset({"id", "computed_at"})

_ORDER_DIRECTIONS = frozenset({"asc", "desc"})


def _is_numeric(column: Any) -> bool:
    """True for column types that can meaningfully appear inside SUM()."""
    try:
        python_type = column.type.python_type
    except (NotImplementedError, AttributeError):  # pragma: no cover - defensive
        return False
    return python_type in (int, float, bool, Decimal)


def _is_boolean(column: Any) -> bool:
    """True for a column whose PYTHON-side type is a truth value.

    Tested through `python_type` rather than `isinstance(column.type, Boolean)`
    so a `TypeDecorator` wrapping a Boolean is caught too. `python_type` is
    precisely the property that predicts the coercion in `_numeric` below: it is
    what tells SQLAlchemy to attach a result processor that turns whatever the
    driver hands back into `True`/`False`.
    """
    try:
        return column.type.python_type is bool
    except (NotImplementedError, AttributeError):  # pragma: no cover - defensive
        return False


def _numeric(column: InstrumentedAttribute) -> Any:
    """The column as an aggregate-safe integer expression.

    **This is the fix for a live wrong number, and it belongs here rather than
    at any call site.** `func.sum(col)` inherits `col`'s SQLAlchemy type. For a
    `Boolean` column that means the aggregate is typed `Boolean`, so the result
    processor MySQL's driver output passes through is the Boolean one — and a
    day with 7 out-of-stock products comes back as `True`. Verified against
    MySQL 8, same SQL both ways:

        SELECT sum(agg_inventory_daily.is_oos)                 -> True   (bool)
        SELECT sum(CAST(agg_inventory_daily.is_oos AS SIGNED)) -> 7      (int)

    The database was never wrong: a raw `text("SELECT SUM(is_oos) ...")` returns
    `Decimal('7')`. The truncation to a truth value happens entirely in the
    result processor, which is why nothing raises and why the number is
    unfalsifiable downstream — `True` formats as `1`, and one product out of
    stock is an utterly ordinary reading.

    Casting inside the repository — the one module that builds this SQL — is
    what makes the fix durable. Fixing it per caller (as `resolvers/levels.py`
    did, by routing every flag through a GROUP BY instead of a SUM) leaves the
    next rollup Boolean one binding away from the same silent failure, and a
    reviewer has nothing to notice.

    Applied to AVG as well as SUM. `func.avg` happens NOT to inherit the column
    type today (`avg` is not a registered generic function, unlike `sum`, `min`
    and `max`, all of which do), so `AVG(<bool>)` is currently correct by
    accident. One line here means it stays correct on purpose.
    """
    return cast(column, Integer) if _is_boolean(column) else column


def _sum(column: InstrumentedAttribute) -> Any:
    """`SUM(column)`, never a truth value. See `_numeric`.

    The OUTER cast is not about correctness — `SUM(CAST(b AS SIGNED))` already
    returns the right count — it is about the Python type. MySQL's `SUM()` over
    an integer returns `DECIMAL`, so without it a Boolean sum arrives as
    `Decimal('7')`. A sum over a Boolean is a COUNT of the rows whose flag was
    set, and every other count this module projects (`row_count`,
    `count_distinct:`) arrives as a plain `int`; `CAST(SUM(...) AS SIGNED)`
    returns BIGINT and so does this one. It also makes the test that pins this
    behaviour able to assert `type(value) is int`, which is the ONLY assertion
    that positively excludes a bool — `isinstance(True, int)` is `True` in
    Python, so an `isinstance` check would pass on exactly the bug it is there
    to catch.
    """
    if _is_boolean(column):
        return cast(func.sum(_numeric(column)), Integer)
    return func.sum(column)


def _is_identity(key: str) -> bool:
    """Entity references (`product_id`, `user_id`, `category_id_snapshot`).

    Numeric, but summing one is never meaningful, so they are groupable
    dimensions rather than measures.
    """
    return key == "id" or key.endswith("_id") or "_id_" in key


class _SourceSpec:
    """The reflected allowlist for one rollup table.

    Built once at import time from the mapped model. Nothing here can be
    influenced by a request; the only thing a request does is *look up* a name.
    """

    __slots__ = ("name", "model", "columns", "grain", "measures")

    def __init__(self, model: type) -> None:
        mapper = sa_inspect(model)
        self.name: str = model.__tablename__
        self.model = model

        # Attribute name -> the mapped attribute itself. The values are what
        # reach the SQL compiler, so a hostile string can only ever fail a dict
        # lookup; it cannot become an identifier.
        self.columns: Mapping[str, InstrumentedAttribute] = MappingProxyType(
            {attr.key: getattr(model, attr.key) for attr in mapper.column_attrs}
        )

        # The grain: primary key + every column in the table's UNIQUE key. That
        # constraint *is* the idempotency key of the rollup, so its members are
        # dimensions by construction (bucket_date, tz_generation, bucket_hour,
        # gateway, cohort_month, period_index...). Derived, not hand-listed, so
        # a new rollup table classifies itself correctly.
        grain: set[str] = {c.key for c in model.__table__.primary_key.columns}
        for constraint in model.__table__.constraints:
            if isinstance(constraint, UniqueConstraint):
                grain.update(c.key for c in constraint.columns)
        self.grain = frozenset(grain)

        self.measures = frozenset(
            key
            for key, col in self.columns.items()
            if _is_numeric(col)
            and key not in self.grain
            and key not in _META_COLUMNS
            and not _is_identity(key)
        )


def _build_registry() -> Mapping[str, _SourceSpec]:
    """Reflect every rollup model into the allowlist, once, at import time.

    Each table is registered under its real table name and under the same name
    with the `agg_` prefix stripped, so a resolver may say either
    `"agg_order_daily"` or `"order_daily"`. Both are still exact matches against
    a fixed dict — an alias is a convenience, never a wildcard.
    """
    registry: dict[str, _SourceSpec] = {}
    for model in (
        AggOrderDaily,
        AggOrderHourly,
        AggProductDaily,
        AggCustomerDaily,
        AggCustomerSnapshot,
        AggCustomerCohortMonthly,
        AggPaymentDaily,
        AggShipmentDaily,
        AggGeoDaily,
        AggPromoDaily,
        AggFunnelDaily,
        AggInventoryDaily,
        AggLoyaltyDaily,
        AggCxDaily,
        AggBasketPairDaily,
        # GA4 traffic, from the Data API. Registered so a view CAN be bound to
        # it; none is yet. Note for whoever binds one: `total_users` is a
        # DISTINCT count and `metric_kind.classify` does not yet know that, so
        # it must not be summed — see `analytics_ga4.NON_ADDITIVE_COLUMNS`.
        AggGa4Daily,
        # `agg_settlement_daily` only. `payment_settlements` is a FACT table —
        # one row per gateway settlement line — and is read through
        # `services/analytics/settlements.py`, not through the generic
        # source allowlist, so it deliberately does not appear here.
        AggSettlementDaily,
    ):
        spec = _SourceSpec(model)
        registry[spec.name] = spec
        alias = spec.name[4:] if spec.name.startswith("agg_") else spec.name
        registry.setdefault(alias, spec)
    return MappingProxyType(registry)


#: source name -> reflected allowlist. Frozen at import; never mutated.
SOURCES: Mapping[str, _SourceSpec] = _build_registry()


def known_sources() -> list[str]:
    """Canonical (table-name) sources, for diagnostics and tests."""
    return sorted({spec.name for spec in SOURCES.values()})


def columns_for(source: str) -> list[str]:
    """Every column a caller may name for `source`. Raises on unknown source."""
    return sorted(_spec(source).columns)


def measures_for(source: str) -> list[str]:
    """Columns of `source` that may appear inside SUM()."""
    return sorted(_spec(source).measures)


def _reject(kind: str, value: Any, allowed: Iterable[str]) -> None:
    """Raise for a name that is not in the allowlist.

    Logged at WARNING as well as raised: a rejected identifier is either a bug
    in a resolver or somebody probing the API with a column name, and both are
    worth seeing. `repr()` + truncation keeps a hostile value from injecting
    newlines into the log or filling a line with a payload.
    """
    shown = repr(value)
    if len(shown) > 80:
        shown = shown[:77] + "..."
    logger.warning("analytics repository rejected %s %s", kind, shown)
    raise ValueError(
        f"unknown analytics {kind} {shown}. Allowed: {', '.join(sorted(allowed))}. "
        "Names are matched against an allowlist reflected from the ORM models "
        "and are never interpolated into SQL."
    )


def _spec(source: Any) -> _SourceSpec:
    if not isinstance(source, str) or source not in SOURCES:
        _reject("source", source, known_sources())
    return SOURCES[source]


def _column(spec: _SourceSpec, name: Any) -> InstrumentedAttribute:
    if not isinstance(name, str) or name not in spec.columns:
        _reject(f"column for {spec.name}", name, spec.columns)
    return spec.columns[name]


def _measure(
    spec: _SourceSpec, name: Any, *, kind: str = "summable"
) -> InstrumentedAttribute:
    column = _column(spec, name)
    if name not in spec.measures:
        _reject(f"{kind} column for {spec.name}", name, spec.measures)
    return column


def _avg(spec: _SourceSpec, projection: str) -> Any:
    """`AVG(<column>)` for the `avg:<column>` projection.

    Two guards, in this order, because they answer different questions:

    1. `_measure` — is this a real, numeric, non-dimension column of THIS table?
       The safety boundary, identical to the one a SUM goes through, so
       `avg:1; DROP TABLE x` fails a dict lookup rather than reaching the
       compiler.
    2. `metric_kind.assert_averagable` — is averaging this column *meaningful*?
       The semantic boundary, and the reason this projection is not simply a
       third `func.<agg>` in `fetch_rollup`. `AVG(units_sold)` over a window is
       "units per bucket", which silently changes value when the same period is
       re-bucketed from days to weeks; only a LEVEL has an average that means
       what a caller asking for one expects. Refusing is the whole point — the
       alternative is a plausible wrong figure, which is the failure mode this
       subsystem is built around.

    This is the one place in the repository that consults `metric_kind`. The
    module docstring's "it does not decide what is additive" still holds for
    SUM, where the allowlist is a safety boundary and additivity is the
    resolver's call. AVG is different: there is no correct caller for
    `AVG(<flow>)`, so there is nothing for a resolver to decide and no reason to
    let the wrong number be constructed at all.

    **Empty buckets: this is a mean over the rows that EXIST, not over calendar
    days.** A day on which a product has no row contributes to neither the
    numerator nor the denominator — `AVG` skips it, it does not read as zero
    stock. That is deliberate and it is the conservative direction:

    * `agg_inventory_daily` is forward-only and documents outright that an
      absent bucket means "we have no data", not "we held no stock" (drawing it
      as zero is the specific failure its `inventory_history_since` setting
      exists to prevent). Dividing by calendar days would encode the opposite.
    * It matches this module's standing rule that it does not invent zeros —
      `fetch_totals` returns `None`, not 0, for an empty window, and `AVG` over
      zero rows likewise returns `None`.
    * It errs the safe way for the metric that motivated it. Turnover is COGS
      over average stock, so a denominator deflated by phantom zero-stock days
      would report *higher* turnover — the flattering direction, and the one
      nobody investigates. Measured over 1997-03-01 (stock 10), no row on the
      2nd, and 1997-03-03 (stock 20), this returns 15, not 10.

    A caller who genuinely wants the calendar-day average can still have it, and
    has to say so: project the SUM and divide by a denominator it names itself.
    An implicit denominator is exactly what makes the wrong version invisible.
    """
    name = projection[len(AVG_PREFIX):]
    column = _measure(spec, name, kind="averageable")
    assert_averagable(name, source=spec.name)
    return func.avg(_numeric(column))


def _unique(kind: str, names: Iterable[str]) -> list[str]:
    """Preserve order, reject duplicates — two `net_revenue` columns in one
    projection would produce a dict with one key and a silently dropped value."""
    seen: list[str] = []
    for name in names:
        if name in seen:
            raise ValueError(f"duplicate {kind} {name!r} in analytics query")
        seen.append(name)
    return seen


class AnalyticsRepository:
    """Reads the 12 rollup tables. The only place analytics SQL is executed."""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------

    def fetch_rollup(
        self,
        source: str,
        *,
        columns: list[str],
        window: ResolvedWindow,
        tz_generation: int,
        group_by: list[str] | None = None,
        filters: dict | None = None,
        order_by: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        """Rows from one rollup over ``[window.date_from, window.date_to)``.

        Half-open on `bucket_date`, matching `timebox` and `filters` — a closed
        upper bound double-counts the boundary day.

        With no `group_by` this is a straight projection. With `group_by`, every
        requested column that is not itself a grouping key is wrapped in `SUM()`
        and must be a summable column of that source (see `measures_for`).

        A `Boolean` column's SUM is cast to an integer first — see `_sum`, which
        is where a rollup flag stopped reporting its count as `True`.

        `avg:<column>` and the count pseudo-columns are aggregates whatever the
        grouping, so like `row_count` they are recognised before that split.

        Values come back in their native Python types (`Decimal`, `date`,
        `datetime`); serialisation belongs to the schema layer.
        """
        spec = _spec(source)
        columns = _unique("column", columns or [])
        if not columns:
            raise ValueError("fetch_rollup requires at least one column")
        group_keys = _unique("group_by", group_by or [])

        grouped = set(group_keys)
        projection = []
        # Output label -> expression, so ORDER BY can target an aggregate by the
        # same name the caller asked for.
        expressions: dict[str, Any] = {}

        for key in group_keys:
            expr = _column(spec, key).label(key)
            expressions[key] = expr
            if key not in columns:
                # Grouping keys are always projected; a grouped result whose key
                # is missing is unreadable.
                projection.append(expr)

        for name in columns:
            if _is_count_projection(name):
                # A row count, not a column. Needed because several legitimate
                # questions are counts of rows rather than sums of a measure:
                # "how many customers are in each RFM segment" is COUNT(*) at one
                # snapshot instant, and there is no column that holds it.
                #
                # Without this the only expressible answer is SUM(some_level),
                # which is both wrong and plausible — segment "champions" would
                # report the sum of its members' lifetime values under a heading
                # that says "customers".
                expr = (
                    func.count()
                    if name == COUNT_ROWS
                    else func.count(func.distinct(_column(spec, name[len(COUNT_DISTINCT_PREFIX):])))
                ).label(name)
            elif _is_avg_projection(name):
                # An aggregate however the query is grouped, exactly like the
                # counts above — `avg:stock_close` grouped by `bucket_date` is
                # the mean across products on each day, ungrouped it is the mean
                # across the whole window. Both are averages; neither is a
                # column, so this branch precedes the group-key split.
                expr = _avg(spec, name).label(name)
            elif not group_keys or name in grouped:
                expr = _column(spec, name).label(name)
            else:
                expr = _sum(_measure(spec, name)).label(name)
            expressions[name] = expr
            projection.append(expr)

        stmt = select(*projection).where(*self._scope(spec, window, tz_generation, filters))

        if group_keys:
            stmt = stmt.group_by(*(expressions[k] for k in group_keys))

        stmt = self._ordered(spec, stmt, expressions, order_by, grouped=bool(group_keys))
        return self._execute_capped(stmt, limit=limit, offset=offset)

    # ------------------------------------------------------------------
    # Totals
    # ------------------------------------------------------------------

    def fetch_totals(
        self,
        source: str,
        *,
        columns: list[str],
        window: ResolvedWindow,
        tz_generation: int,
        filters: dict | None = None,
    ) -> dict:
        """One SUM per requested column over the whole window.

        A column whose window contains no rows comes back as ``None``, not 0.
        That distinction is deliberate and load-bearing: 0 means "measured, and
        it was zero"; `None` means "there is nothing to measure yet". Pair it
        with `source_watermark` to tell the two apart in the envelope.

        `avg:<column>` is accepted here too, and is the form a window-level
        average is asked for: Inventory Turnover's denominator is one number for
        the whole window, not one per bucket. It obeys the same `None`-not-zero
        rule — `AVG` over no rows is `None`.

        A `Boolean` column's total is a count of the rows whose flag was set,
        returned as an `int`. It used to be returned as `True`; see `_sum`.
        """
        spec = _spec(source)
        columns = _unique("column", columns or [])
        if not columns:
            raise ValueError("fetch_totals requires at least one column")

        projection = [
            (
                _avg(spec, name)
                if _is_avg_projection(name)
                else _sum(_measure(spec, name))
            ).label(name)
            for name in columns
        ]
        stmt = select(*projection).where(*self._scope(spec, window, tz_generation, filters))
        row = self.db.execute(stmt).mappings().first()
        if row is None:  # pragma: no cover - an aggregate always returns a row
            return {name: None for name in columns}
        return {name: row[name] for name in columns}

    # ------------------------------------------------------------------
    # Freshness / generation guards
    # ------------------------------------------------------------------

    def source_watermark(self, source: str, tz_generation: int) -> tuple[date | None, int]:
        """``(newest bucket_date, row count)`` for a source and generation.

        The newest bucket is what the response envelope reports as
        `last_updated_at`. The count is what stops an empty chart from being
        read as a bad week: `(None, 0)` is "this rollup has never been built for
        this generation", which is a different fact from a real zero and must be
        surfaced differently.
        """
        spec = _spec(source)
        bucket_date = _column(spec, "bucket_date")
        generation = _column(spec, "tz_generation")
        stmt = select(func.max(bucket_date), func.count()).where(
            generation == int(tz_generation)
        )
        newest, count = self.db.execute(stmt).one()
        return newest, int(count or 0)

    def distinct_tz_generations(self, source: str, window: ResolvedWindow) -> set[int]:
        """Every `tz_generation` with rows inside the window.

        Feeds `timebox.assert_single_generation`. More than one means the window
        straddles a reporting-timezone change, and the buckets on either side
        were computed under different day boundaries — they must be refused, not
        summed, because the resulting number is wrong in a way no later check
        can detect.

        An empty set means the window has no rows at all; the caller decides
        whether that is an empty state or an error.
        """
        spec = _spec(source)
        bucket_date = _column(spec, "bucket_date")
        generation = _column(spec, "tz_generation")
        stmt = select(distinct(generation)).where(
            bucket_date >= window.date_from, bucket_date < window.date_to
        )
        return {int(g) for (g,) in self.db.execute(stmt).all() if g is not None}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _scope(
        self,
        spec: _SourceSpec,
        window: ResolvedWindow,
        tz_generation: int,
        filters: dict | None,
    ) -> list[Any]:
        """WHERE terms shared by every read: window, generation, filters.

        `tz_generation` is always pinned. Omitting it would let a window that
        happens to span a timezone rebuild sum two generations of the same day
        together — the single failure mode `timebox` exists to prevent.
        """
        bucket_date = _column(spec, "bucket_date")
        generation = _column(spec, "tz_generation")
        conditions: list[Any] = [
            bucket_date >= window.date_from,
            bucket_date < window.date_to,
            generation == int(tz_generation),
        ]
        for name, value in (filters or {}).items():
            column = _column(spec, name)
            if value is None:
                conditions.append(column.is_(None))
            elif isinstance(value, (list, tuple, set, frozenset)):
                values = list(value)
                if not values:
                    # An empty IN () is a syntax error on MySQL and, worse, an
                    # ambiguous intent. Refuse rather than guess.
                    raise ValueError(
                        f"filter {name!r} was given an empty collection; omit the "
                        "filter instead of passing an empty set"
                    )
                conditions.append(column.in_(values))
            else:
                conditions.append(column == value)
        return conditions

    def _ordered(
        self,
        spec: _SourceSpec,
        stmt: Select,
        expressions: dict[str, Any],
        order_by: str | None,
        *,
        grouped: bool,
    ) -> Select:
        """Apply a validated ORDER BY, or a deterministic default.

        `order_by` accepts `"net_revenue"`, `"-net_revenue"` or
        `"net_revenue desc"`. The column half is validated against the same
        allowlist as everything else, so `"net_revenue; DROP TABLE x"` fails the
        lookup instead of reaching the compiler.

        The default matters for pagination: without a total order, MySQL may
        return a different page 2 for the same offset, which shows up as rows
        that appear twice in a report and rows that never appear at all.
        """
        if order_by is not None:
            name, direction = self._parse_order_by(spec, order_by)
            expr = expressions.get(name)
            if expr is None:
                # Ordering by a column that is not in the projection is fine for
                # a plain select, but meaningless (and invalid on MySQL's
                # ONLY_FULL_GROUP_BY) for a grouped one.
                if grouped:
                    _reject(
                        f"order_by column for {spec.name} (must be selected or grouped)",
                        name,
                        expressions,
                    )
                expr = _column(spec, name)
            return stmt.order_by(expr.desc() if direction == "desc" else expr.asc())

        tiebreakers = []
        if "bucket_date" in expressions:
            tiebreakers.append(expressions["bucket_date"].asc())
        if not grouped and "id" in spec.columns:
            tiebreakers.append(spec.columns["id"].asc())
        return stmt.order_by(*tiebreakers) if tiebreakers else stmt

    @staticmethod
    def _parse_order_by(spec: _SourceSpec, order_by: Any) -> tuple[str, str]:
        if not isinstance(order_by, str):
            _reject(f"order_by for {spec.name}", order_by, spec.columns)
        token = order_by.strip()
        direction = "asc"
        if token.startswith("-"):
            direction, token = "desc", token[1:]
        elif " " in token:
            token, _, tail = token.partition(" ")
            direction = tail.strip().lower()
        if direction not in _ORDER_DIRECTIONS:
            raise ValueError(
                f"invalid sort direction {direction!r}; expected asc or desc"
            )
        name = token.strip()
        _column(spec, name)  # raises unless allowlisted
        return name, direction

    def _execute_capped(
        self, stmt: Select, *, limit: int | None, offset: int
    ) -> list[dict]:
        offset = int(offset)
        if offset < 0:
            raise ValueError("offset must be >= 0")

        if limit is None:
            # No explicit limit still gets a ceiling — an unbounded scan of a
            # rollup is an incident. Fetch one row past the cap so a result that
            # would have been truncated can be *reported* instead.
            effective = HARD_ROW_CAP + 1
        else:
            limit = int(limit)
            if limit < 0:
                raise ValueError("limit must be >= 0")
            if limit > HARD_ROW_CAP:
                raise ValueError(
                    f"limit {limit} exceeds the {HARD_ROW_CAP}-row cap for a single "
                    "analytics read; narrow the window or paginate"
                )
            effective = limit

        stmt = stmt.offset(offset).limit(effective)
        rows = [dict(m) for m in self.db.execute(stmt).mappings().all()]

        if limit is None and len(rows) > HARD_ROW_CAP:
            raise ValueError(
                f"analytics read returned more than {HARD_ROW_CAP} rows. It is not "
                "truncated silently — a shortened result leaves an untraceable gap "
                "in the chart. Narrow the window or pass an explicit limit."
            )
        return rows
