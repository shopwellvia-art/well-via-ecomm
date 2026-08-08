"""Effective-dated cost-rule resolution for the contribution-margin engines.

Why this file exists
--------------------
Costs live today as five mutable rows in ``system_settings``
(``costs.gateway_fee_pct``, ``costs.packing_per_order``, ...). A single mutable
cell has no time axis. It answers "what does packing cost?" and can never answer
"what did packing cost in March?" — and the margin report only ever asks the
second question. The consequence is not a missing feature, it is silent
falsification: editing ``costs.gateway_fee_pct`` from 2% to 2.4% in July
rewrites *every* margin the store has ever reported. March's contribution margin
changes retroactively, last quarter's deck no longer reproduces, and nothing
anywhere records that an edit happened.

``analytics_cost_rules`` is effective-dated, so a rate change writes a **new
row** with a new ``effective_from`` and closes the old one. Recomputing March
resolves March's rule. This module is the read side of that table.

Missing is not zero
-------------------
If no rule covers a bucket, :meth:`CostRuleResolver.resolve` returns a
``CostComponent`` with ``value_minor=None`` and ``MetricQuality.INCOMPLETE``. It
never returns 0. A zero packaging cost is indistinguishable from free packaging,
and the resulting margin reads *better* than reality — the one direction of
error nobody investigates. This is the single most important behaviour here; the
rest of the file is bookkeeping around it.

The gap the quality ladder does not cover
-----------------------------------------
``CostQuality`` grades how much a *rule* is worth trusting, and resolution grades
whether a rule exists at all. Neither says anything about whether the **inputs**
to that rule are known. A per-kg forward-shipping rule is useless if the
shipment's billed weight was never captured: the rule is present, contracted,
and multiplying it by an unknown weight of 0 yields a confident ₹0 shipping
cost. :meth:`CostRuleResolver.compute` therefore returns INCOMPLETE — not zero —
when an input whose absence is meaningless is absent (``PER_KG`` with no weight,
``PER_MONTH`` with no period). See :meth:`compute` for exactly which inputs are
treated that way and which zeroes are accepted as real measurements.

Two-step API
------------
``resolve()`` answers "which rule applied on this day?" and returns the **rate**;
``compute()`` answers "what does that rate cost for this bucket?" and returns the
**amount**. Both are ``CostComponent``s because the margin cascade consumes one
shape, but they carry different things in ``value_minor`` — see :meth:`resolve`.

Resolution is per day, but the READ is per window
-------------------------------------------------
The margin cascade resolves every cost type for every day of a window — 8 types
x 90 days x 2 windows — and the Redis key includes the date, so a cold cache
used to mean one ``SELECT`` per (type, day): a measured **568 queries** for one
90-day margin view against 28 warm (``docs/analytics/PERFORMANCE.md`` §5.2). The
date is in the key for a reason (two days genuinely resolve to different rules),
so the fix is not to widen the key but to stop asking the database per day.

Rules are **effective-dated**, which means one query returns every rule any day
of a window could resolve to: the date predicate is the only thing that varies,
and it can be applied in Python. :meth:`resolve` therefore loads the candidate
rules for a ``(cost_type, scope candidates)`` pair **once per resolver
instance** and picks per day in memory, and :meth:`resolve_range` exposes the
same thing as a window API for callers that know their window up front. The
Redis layer in front of it is untouched — same keys, same TTL, same
degrade-to-uncached behaviour when Redis is down. What changes is that a Redis
outage now costs one query per cost type instead of one per (type, day).

The memo lives on the instance, not on the class or the process: a resolver is
built per computation (``MarginService.__init__``) and is short-lived, exactly
like the ``_meta_by_id`` memo that was already here. :meth:`invalidate_all`
clears it alongside the Redis namespace.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import logging
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Mapping, Optional, Sequence

import redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.redis import get_redis
from app.models.analytics_control import (
    AnalyticsCostRule,
    CostQuality,
    CostScope,
    CostType,
    CostUnit,
)
from app.models.system_setting import SystemSetting
from app.services.analytics.contracts import CostComponent, to_minor
from app.services.analytics.types import MetricQuality

logger = logging.getLogger(__name__)

__all__ = ["CostRuleResolver", "LEGACY_SETTING_MAP", "LEGACY_SOURCE"]

# Mirrors settings_service._CACHE_TTL. Short on purpose: writing a cost rule
# enqueues the affected buckets for recompute, and a stale cached MISS that
# outlived the enqueue would make the recompute produce INCOMPLETE for a bucket
# that is now covered. 60s bounds that window; `invalidate_all()` closes it.
_CACHE_TTL = 60
#: Same sentinel idiom as settings_service — lets the *absent* case be cached,
#: which matters more here than there: a store with no rules configured is the
#: common case and would otherwise hit the DB on every bucket.
_CACHE_MISS = "__NONE__"
_NAMESPACE = "analytics:costrule:"

#: Cache keys read per Redis round trip by the window API. A five-year range
#: over eight cost types is ~15 000 keys, and one MGET that size is a single
#: multi-megabyte reply that stalls the connection for every other caller. The
#: chunk keeps the reply bounded without turning the read back into an N+1.
_MGET_CHUNK = 500

#: The five legacy `costs.*` settings this table replaces, and what each becomes.
#: (setting_key, CostType, CostUnit)
LEGACY_SETTING_MAP: tuple[tuple[str, str, str], ...] = (
    ("costs.gateway_fee_pct", CostType.GATEWAY_FEE, CostUnit.PCT),
    ("costs.packing_per_order", CostType.PACKAGING, CostUnit.PER_ORDER),
    ("costs.handling_per_order", CostType.HANDLING, CostUnit.PER_ORDER),
    ("costs.monthly_overheads", CostType.MONTHLY_OVERHEAD, CostUnit.PER_MONTH),
    ("costs.monthly_ad_spend", CostType.MARKETING_SPEND, CostUnit.PER_MONTH),
)
LEGACY_SOURCE = "legacy_settings_migration"


def _quality_of(rule_quality: str) -> MetricQuality:
    """Rule-level CostQuality -> metric-level MetricQuality.

    Only a settled invoice or settlement report earns ACTUAL. CONTRACTED and
    ASSUMED both collapse to ESTIMATED: a rate card is a promise about the
    future, not an observation of the past, and the margin envelope must not
    present either with the confidence of a reconciled number.
    """
    if (rule_quality or "").strip().lower() == CostQuality.ACTUAL:
        return MetricQuality.ACTUAL
    return MetricQuality.ESTIMATED


def _round_minor(amount: Decimal) -> int:
    """Round a Decimal already expressed in minor units to a whole paisa.

    ROUND_HALF_UP rather than Decimal's default banker's rounding so a rate
    applied to the same base always produces the same paisa, whatever the
    process-wide decimal context happens to be.
    """
    return int(amount.to_integral_value(rounding=ROUND_HALF_UP))


def _precedence_index(scope: str) -> int:
    """Position in CostScope.PRECEDENCE; unknown scopes sort least-specific."""
    try:
        return CostScope.PRECEDENCE.index(scope)
    except ValueError:
        return len(CostScope.PRECEDENCE)


@dataclass(frozen=True)
class _RuleMeta:
    """The fields of an `analytics_cost_rules` row resolution actually needs.

    A detached snapshot rather than the ORM object, because it round-trips
    through Redis as JSON and must not carry a Session with it.
    """

    rule_id: int
    cost_type: str
    scope: str
    scope_value: str
    value: Decimal
    unit: str
    quality: str
    source: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "rule_id": self.rule_id,
                "cost_type": self.cost_type,
                "scope": self.scope,
                "scope_value": self.scope_value,
                # str(), never float(): a rate is exact and float would round it.
                "value": str(self.value),
                "unit": self.unit,
                "quality": self.quality,
                "source": self.source,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> "_RuleMeta":
        data = json.loads(raw)
        return cls(
            rule_id=int(data["rule_id"]),
            cost_type=data["cost_type"],
            scope=data["scope"],
            scope_value=data["scope_value"],
            value=Decimal(data["value"]),
            unit=data["unit"],
            quality=data["quality"],
            source=data["source"] or "",
        )

    @classmethod
    def from_row(cls, row: AnalyticsCostRule) -> "_RuleMeta":
        return cls(
            rule_id=int(row.id),
            cost_type=row.cost_type,
            scope=row.scope,
            scope_value=row.scope_value,
            value=Decimal(row.value),
            unit=row.unit,
            quality=row.quality,
            source=row.source or "",
        )


@dataclass(frozen=True)
class _DatedRule:
    """A candidate rule plus the window it is in force for.

    Deliberately NOT part of :class:`_RuleMeta`: that one is the Redis payload,
    where the effective dates are already answered by the key. These two fields
    exist only for the in-memory pick, which is the whole point of loading a
    rule once and reusing it for every day of a window.
    """

    meta: _RuleMeta
    effective_from: date
    effective_to: date | None

    def covers(self, on_date: date) -> bool:
        """``effective_from <= on_date AND (effective_to IS NULL OR >= on_date)``.

        The same predicate the per-date SQL used to carry, moved into Python
        without loosening it — a rule that ends *on* the bucket still covers it.
        """
        if self.effective_from > on_date:
            return False
        return self.effective_to is None or self.effective_to >= on_date

    @property
    def sort_key(self) -> tuple[int, date, int]:
        """Descending precedence: specificity, then recency, then id.

        Identical to the ordering the per-date query applied, and shared by both
        APIs so a window resolve can never disagree with a single-day one.
        """
        return (
            -_precedence_index(self.meta.scope),
            self.effective_from,
            self.meta.rule_id,
        )


class CostRuleResolver:
    """Reads `analytics_cost_rules` as of a bucket date, never as of "now".

    Usage::

        resolver = CostRuleResolver(db)
        component = resolver.resolve(
            CostType.GATEWAY_FEE, bucket_date,
            scope_candidates={"gateway": "razorpay"},
        )
        fee = resolver.compute(component, base_minor=prepaid_sales_minor)
        if fee.is_missing:
            ...  # margin for this bucket is INCOMPLETE; do NOT substitute 0
    """

    def __init__(self, db: Session, redis_client: Optional[redis.Redis] = None):
        self.db = db
        self.redis = redis_client or get_redis()
        # rule_id -> meta, so compute() can read the rule's exact Decimal value
        # and unit without a second query. Populated by resolve(); compute()
        # falls back to a primary-key read for components it did not resolve.
        self._meta_by_id: dict[int, _RuleMeta] = {}
        # (cost_type, scope digest) -> every rule that ANY date could resolve
        # to for that pair. One read per pair per resolver instead of one per
        # (type, date); the date predicate is applied in `_DatedRule.covers`.
        # See the module docstring for why the instance is the right lifetime.
        self._candidates_by_type: dict[tuple[str, str], list[_DatedRule]] = {}

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------
    def resolve(
        self,
        cost_type: str,
        on_date: date,
        *,
        scope_candidates: Mapping[str, Any] | None = None,
    ) -> CostComponent:
        """Which rule of `cost_type` was in force on `on_date`, if any.

        Selection is, in order:

        1. rules whose window contains ``on_date`` — ``effective_from <=
           on_date AND (effective_to IS NULL OR effective_to >= on_date)``,
           judged by the *bucket's* date and never by today's;
        2. the most specific scope that matches, walking
           ``CostScope.PRECEDENCE``. Only scopes named in ``scope_candidates``
           can match beyond ``global``; a per-courier rule applies to that
           courier and to nothing else;
        3. on a tie within one scope, the latest ``effective_from`` — that is
           the rate change that most recently took effect on or before the
           bucket.

        ``scope_candidates`` is what the bucket *is*, e.g.
        ``{"product": "123", "category": "9", "gateway": "razorpay"}``.

        The returned component carries the **rate**, not an amount:
        ``value_minor`` is ``to_minor(rule.value)``, which for money units is
        the rate in paise and for ``PCT`` is hundredths of a percent (2.5% ->
        250). Pass it to :meth:`compute` to turn it into money; do not sum
        resolved components as if they were already money. ``compute`` reads the
        rule's full-precision Decimal, so no rate precision is lost by this.

        With no covering rule, the component is MISSING: ``value_minor is
        None``, quality INCOMPLETE. Never 0.
        """
        candidates = self._normalise_candidates(scope_candidates)
        key = self._cache_key(cost_type, on_date, candidates)

        meta, hit = self._cache_get(key)
        if not hit:
            meta = self._select_rule(cost_type, on_date, candidates)
            self._cache_set(key, meta)

        return self._component(cost_type, meta)

    def resolve_range(
        self,
        cost_types: Sequence[str],
        date_from: date,
        date_to: date,
        *,
        scope_candidates: Mapping[str, Any] | None = None,
    ) -> dict[tuple[str, date], CostComponent]:
        """:meth:`resolve` for a whole window, in one database read per type.

        Returns ``{(cost_type, day): CostComponent}`` for every day in the
        **inclusive** range ``[date_from, date_to]`` — inclusive because callers
        ask it for the days of a window they have already half-open'd, and a
        second off-by-one convention in the same file is a bug waiting to be
        written. An empty or inverted range returns ``{}``.

        Every value is the value :meth:`resolve` would return for that
        ``(cost_type, day)``, and that is a property the test suite asserts
        day by day rather than a claim made here: both APIs pick with
        :meth:`_select_rule` over the same candidate list, so there is one
        implementation of "which rule applied", not two that must be kept in
        step.

        What changes is only the number of round trips: one Redis ``MGET`` per
        :data:`_MGET_CHUNK` keys instead of one ``GET`` per key, and one
        ``SELECT`` per ``(cost_type, scope candidates)`` pair instead of one per
        ``(cost_type, day)``. The cache keys, their TTL, and what a Redis
        failure does are all unchanged — a failed read still falls through to
        the database and a failed write is still swallowed, so a Redis outage
        degrades this to *uncached*, never to *wrong*.
        """
        days = self._days_in(date_from, date_to)
        if not days or not cost_types:
            return {}

        candidates = self._normalise_candidates(scope_candidates)
        wanted = [
            (cost_type, day, self._cache_key(cost_type, day, candidates))
            for cost_type in cost_types
            for day in days
        ]

        cached = self._cache_get_many([key for _type, _day, key in wanted])

        resolved: dict[tuple[str, date], CostComponent] = {}
        to_write: dict[str, _RuleMeta | None] = {}
        for cost_type, day, key in wanted:
            if key in cached:
                meta = cached[key]
            else:
                meta = self._select_rule(cost_type, day, candidates)
                to_write[key] = meta
            resolved[(cost_type, day)] = self._component(cost_type, meta)

        self._cache_set_many(to_write)
        return resolved

    @staticmethod
    def _days_in(date_from: date, date_to: date) -> list[date]:
        """Every day of an inclusive range, or [] if the range is inverted."""
        if date_to < date_from:
            return []
        span = (date_to - date_from).days
        return [date_from + timedelta(days=offset) for offset in range(span + 1)]

    def _component(self, cost_type: str, meta: _RuleMeta | None) -> CostComponent:
        """The one place a resolved rule becomes a component.

        Shared by the per-date and the window API so neither can drift into a
        differently-shaped answer, and the single place the ``compute()`` memo
        is populated.
        """
        if meta is None:
            return self._missing(cost_type)
        self._meta_by_id[meta.rule_id] = meta
        return CostComponent(
            cost_type=meta.cost_type,
            value_minor=to_minor(meta.value),
            quality=_quality_of(meta.quality),
            scope=meta.scope,
            scope_value=meta.scope_value,
            source=meta.source,
            rule_id=meta.rule_id,
        )

    @staticmethod
    def _missing(cost_type: str) -> CostComponent:
        """The no-rule answer. The one thing this module must never get wrong."""
        return CostComponent(
            cost_type=cost_type,
            value_minor=None,
            quality=MetricQuality.INCOMPLETE,
            scope=CostScope.GLOBAL,
            scope_value="-",
            source="",
            rule_id=None,
        )

    @staticmethod
    def _normalise_candidates(raw: Mapping[str, Any] | None) -> dict[str, str]:
        """Drop unknown scopes, the global pseudo-scope, and empty values.

        A candidate of ``None``/``""``/``"-"`` is not a scope the bucket has; it
        is the absence of one, and must not be allowed to match a rule whose
        ``scope_value`` happens to hold the same sentinel.
        """
        out: dict[str, str] = {}
        for scope, value in (raw or {}).items():
            if scope not in CostScope.PRECEDENCE or scope == CostScope.GLOBAL:
                continue
            if value is None:
                continue
            text = str(value).strip()
            if not text or text == "-":
                continue
            out[scope] = text
        return out

    def _select_rule(
        self, cost_type: str, on_date: date, candidates: Mapping[str, str]
    ) -> _RuleMeta | None:
        """Which of the candidate rules was in force on `on_date`.

        Pure Python over :meth:`_candidate_rules`, which is what makes a window
        cost one read rather than one per day. The three-part ordering is
        unchanged: specificity first, then the most recent ``effective_from``,
        then id purely so a same-day tie the UNIQUE key cannot see (two scopes,
        one bucket) is deterministic.
        """
        best: _DatedRule | None = None
        for rule in self._candidate_rules(cost_type, candidates):
            if not rule.covers(on_date):
                continue
            if best is None or rule.sort_key > best.sort_key:
                best = rule
        return best.meta if best is not None else None

    def _candidate_rules(
        self, cost_type: str, candidates: Mapping[str, str]
    ) -> list[_DatedRule]:
        """Every rule of `cost_type` that any date could resolve to, memoised.

        The DB read, and the only one resolution makes. Deliberately **not**
        bounded by date: the date is the one predicate that varies across a
        window, so pushing it into SQL is what forced a query per day. Both
        other predicates are pushed down as before — ``cost_type`` and the
        scopes the caller declared — so a store with per-product rules reads the
        rules for the products in its bucket, not the whole table.

        The scope match is applied here rather than per day because
        ``candidates`` is fixed for the memo key: a rule that does not cover
        this bucket's scope cannot start covering it tomorrow.

        Small by construction — one row per scope per rate change — which is
        the same assumption the previous per-date query made when it read every
        matching row and picked in Python.
        """
        key = (cost_type, self._candidate_digest(candidates))
        memo = self._candidates_by_type.get(key)
        if memo is not None:
            return memo

        stmt = select(AnalyticsCostRule).where(
            AnalyticsCostRule.cost_type == cost_type,
            AnalyticsCostRule.scope.in_([CostScope.GLOBAL, *candidates.keys()]),
        )
        rules = [
            _DatedRule(
                meta=_RuleMeta.from_row(row),
                effective_from=row.effective_from,
                effective_to=row.effective_to,
            )
            for row in self.db.execute(stmt).scalars().all()
            if self._matches(row, candidates)
        ]
        self._candidates_by_type[key] = rules
        return rules

    @staticmethod
    def _matches(rule: AnalyticsCostRule, candidates: Mapping[str, str]) -> bool:
        """Does this rule's scope cover the bucket?

        Global covers everything. Any narrower scope covers the bucket only if
        the bucket declared that scope AND the value matches. Comparison is
        case-insensitive because scope values are codes typed by humans in the
        admin ("Razorpay") and written by jobs in lower case, and MySQL's
        default collation would have matched them anyway.
        """
        if rule.scope == CostScope.GLOBAL:
            return True
        want = candidates.get(rule.scope)
        if want is None:
            return False
        return str(rule.scope_value or "").strip().casefold() == want.casefold()

    # ------------------------------------------------------------------
    # Computation
    # ------------------------------------------------------------------
    def compute(
        self,
        component: CostComponent,
        *,
        base_minor: int = 0,
        units: int = 0,
        weight_grams: int = 0,
        orders: int = 0,
        days_in_period: int = 0,
        on_date: date | None = None,
    ) -> CostComponent:
        """Turn a resolved rate into money for one bucket, in paise.

        ============  ==================================================
        ``PCT``       ``base_minor * value / 100``
        ``PER_ORDER`` ``value * orders``
        ``PER_UNIT``  ``value * units``
        ``PER_KG``    ``value * weight_grams / 1000``
        ``PER_MONTH`` ``value * days_in_period / days_in_that_month``
        ============  ==================================================

        ``on_date`` is required for ``PER_MONTH`` and ignored otherwise: a
        straight-line pro-rate needs the length of the month being pro-rated,
        and February and July give answers that differ by 10%. It is an explicit
        argument rather than something remembered from ``resolve()`` because
        guessing 30 days would be exactly the class of confident-but-invented
        number this module exists to prevent.

        Zero inputs, and the ones that are not measurements
        ---------------------------------------------------
        ``orders``, ``units`` and ``base_minor`` of 0 are honoured as **real
        zeroes**: a bucket with no orders genuinely incurs no per-order packing
        cost, and reporting 0 there is correct.

        ``weight_grams`` and ``days_in_period`` of 0 are not measurements. No
        shipment weighs nothing and no reporting period is zero days long, so a
        0 in either position means the caller does not know the value — and
        multiplying a known rate by an unknown quantity produces a confident
        ₹0, which reads as *better* margin than reality. Both therefore return
        MISSING (``value_minor=None``, INCOMPLETE) instead.

        This is the gap in the quality ladder noted in the module docstring:
        ``CostQuality`` grades the rule, resolution grades the rule's existence,
        and nothing upstream of here grades the inputs. The zero-default
        signature means ``compute(c)`` with a forgotten ``base_minor`` still
        returns a confident 0 for a ``PCT`` rule — callers must pass the bases
        they actually measured, and the margin engine's coverage percentage is
        what makes a systematically forgotten input visible.
        """
        if component.is_missing or component.rule_id is None:
            # Nothing to compute against; MISSING propagates unchanged.
            return component

        meta = self._meta_for(component.rule_id)
        if meta is None:
            # The rule was deleted between resolve and compute. Incomplete, not
            # zero — we know a cost applied, we just can no longer say how much.
            logger.warning(
                "cost rule %s vanished between resolve and compute", component.rule_id
            )
            return self._incomplete(component)

        unit = (meta.unit or "").strip().lower()
        value = meta.value

        if unit == CostUnit.PCT:
            return replace(
                component, value_minor=_round_minor(Decimal(base_minor) * value / 100)
            )
        if unit == CostUnit.PER_ORDER:
            return replace(component, value_minor=to_minor(value * Decimal(orders)))
        if unit == CostUnit.PER_UNIT:
            return replace(component, value_minor=to_minor(value * Decimal(units)))
        if unit == CostUnit.PER_KG:
            if weight_grams <= 0:
                return self._incomplete(component)
            return replace(
                component,
                value_minor=to_minor(value * Decimal(weight_grams) / Decimal(1000)),
            )
        if unit == CostUnit.PER_MONTH:
            if days_in_period <= 0 or on_date is None:
                return self._incomplete(component)
            days_in_month = calendar.monthrange(on_date.year, on_date.month)[1]
            return replace(
                component,
                value_minor=to_minor(
                    value * Decimal(days_in_period) / Decimal(days_in_month)
                ),
            )

        # An unrecognised unit is uninterpretable, and 2.5 is either 2.5% or
        # ₹2.50 — a 100x error. Refuse rather than pick one.
        logger.warning("cost rule %s has unknown unit %r", meta.rule_id, meta.unit)
        return self._incomplete(component)

    @staticmethod
    def _incomplete(component: CostComponent) -> CostComponent:
        """Same rule provenance, but the amount is not knowable."""
        return replace(
            component, value_minor=None, quality=MetricQuality.INCOMPLETE
        )

    def _meta_for(self, rule_id: int) -> _RuleMeta | None:
        """Rule fields for a component, from the resolve memo or by primary key."""
        meta = self._meta_by_id.get(rule_id)
        if meta is not None:
            return meta
        row = self.db.get(AnalyticsCostRule, rule_id)
        if row is None:
            return None
        meta = _RuleMeta.from_row(row)
        self._meta_by_id[rule_id] = meta
        return meta

    # ------------------------------------------------------------------
    # Cache — same shape and same failure handling as settings_service
    # ------------------------------------------------------------------
    @staticmethod
    def _candidate_digest(candidates: Mapping[str, str]) -> str:
        """Stable short name for a scope-candidate set.

        Canonicalised through ``CostScope.PRECEDENCE`` so two callers that build
        the same candidates in different dict orders share a cache entry — and
        the same digest keys the in-memory candidate memo, so the two layers
        partition the world identically.
        """
        if not candidates:
            return "global"
        canon = "|".join(
            f"{scope}={candidates[scope]}"
            for scope in CostScope.PRECEDENCE
            if scope in candidates
        )
        return hashlib.blake2b(canon.encode("utf-8"), digest_size=8).hexdigest()

    @classmethod
    def _cache_key(
        cls, cost_type: str, on_date: date, candidates: Mapping[str, str]
    ) -> str:
        """`analytics:costrule:<type>:<date>:<scope digest>`.

        The scope digest is part of the key because the candidate set changes
        which rules can match: the same type and date resolve to a different
        rule for a razorpay bucket than for a cod one.
        """
        return (
            f"{_NAMESPACE}{cost_type}:{on_date.isoformat()}:"
            f"{cls._candidate_digest(candidates)}"
        )

    def _cache_get(self, key: str) -> tuple[_RuleMeta | None, bool]:
        """Returns (meta, hit). A cached MISS is a hit carrying None."""
        try:
            cached = self.redis.get(key)
        except redis.RedisError as exc:
            logger.debug("cost rule cache read failed: %s", exc)
            return None, False
        if cached is None:
            return None, False
        if cached == _CACHE_MISS:
            return None, True
        try:
            return _RuleMeta.from_json(cached), True
        except (ValueError, KeyError, TypeError) as exc:
            # Corrupt or older-shaped payload: fall through to the DB rather
            # than let a bad cache entry decide a margin.
            logger.debug("cost rule cache decode failed: %s", exc)
            return None, False

    def _cache_set(self, key: str, meta: _RuleMeta | None) -> None:
        try:
            self.redis.setex(
                key, _CACHE_TTL, meta.to_json() if meta is not None else _CACHE_MISS
            )
        except redis.RedisError as exc:
            logger.debug("cost rule cache write failed: %s", exc)

    def _cache_get_many(
        self, keys: Sequence[str]
    ) -> dict[str, _RuleMeta | None]:
        """`_cache_get` for many keys. Only HITS appear in the result.

        A key absent from the returned mapping means "not cached" — the same
        distinction ``_cache_get``'s ``hit`` flag carries, expressed as
        membership so a cached MISS (present, value ``None``) stays
        distinguishable from an uncached key.

        Every failure mode degrades to "not cached" and therefore to a database
        read: a Redis error, a payload that will not decode, a shorter reply
        than the request. That is exactly what the per-key path does; a bad
        cache entry must never be allowed to decide a margin.
        """
        if not keys:
            return {}
        found: dict[str, _RuleMeta | None] = {}
        for start in range(0, len(keys), _MGET_CHUNK):
            chunk = list(keys[start : start + _MGET_CHUNK])
            try:
                values = self.redis.mget(chunk)
            except redis.RedisError as exc:
                logger.debug("cost rule cache bulk read failed: %s", exc)
                return found
            for key, cached in zip(chunk, values or []):
                if cached is None:
                    continue
                if cached == _CACHE_MISS:
                    found[key] = None
                    continue
                try:
                    found[key] = _RuleMeta.from_json(cached)
                except (ValueError, KeyError, TypeError) as exc:
                    logger.debug("cost rule cache decode failed: %s", exc)
        return found

    def _cache_set_many(self, entries: Mapping[str, _RuleMeta | None]) -> None:
        """`_cache_set` for many keys, in one pipeline.

        Each entry keeps its own ``SETEX`` and its own TTL — the pipeline is a
        transport detail, not a change of expiry semantics — and a Redis failure
        is swallowed here exactly as it is for a single write, because a cache
        that cannot be written is still a correct system, only a slower one.
        """
        if not entries:
            return
        try:
            pipe = self.redis.pipeline(transaction=False)
            for key, meta in entries.items():
                pipe.setex(
                    key, _CACHE_TTL, meta.to_json() if meta is not None else _CACHE_MISS
                )
            pipe.execute()
        except redis.RedisError as exc:
            logger.debug("cost rule cache bulk write failed: %s", exc)

    def invalidate_all(self) -> int:
        """Drop every cached resolution. Call after writing a cost rule.

        Entries expire in 60s anyway, so this is not required for correctness
        over the long run — but a rule write enqueues buckets for recompute, and
        a worker draining that queue within the TTL would otherwise recompute
        them against the cached pre-edit answer. The namespace holds at most a
        few entries per (cost_type, date) in flight, so the SCAN is cheap.

        The instance's own memos go with it. They have no TTL at all, so a
        resolver that outlived a rule edit would keep answering from the rules
        it read before it — the same staleness this method exists to close, one
        layer further in.
        """
        self._candidates_by_type.clear()
        self._meta_by_id.clear()
        removed = 0
        try:
            for key in self.redis.scan_iter(match=f"{_NAMESPACE}*", count=500):
                self.redis.delete(key)
                removed += 1
        except redis.RedisError as exc:
            logger.debug("cost rule cache invalidate failed: %s", exc)
        return removed

    # ------------------------------------------------------------------
    # One-time migration off the legacy settings
    # ------------------------------------------------------------------
    @staticmethod
    def seed_from_legacy_settings(db: Session, effective_from: date) -> int:
        """Copy the five `costs.*` settings into effective-dated rules.

        Run once, with ``effective_from`` set to the earliest date the legacy
        values should be believed to have applied. Returns how many rules were
        created.

        Idempotent: a rule already existing for the same ``(cost_type, scope,
        scope_value, effective_from)`` — the table's UNIQUE key — is left alone
        and not counted, so re-running the migration is a no-op rather than an
        IntegrityError.

        Everything created is ``quality='ESTIMATED'``, because that is what the
        legacy values are: a number an admin typed into a settings form at some
        unrecorded time, with no invoice behind it and no evidence it was the
        rate on the day of any particular bucket. Marking them CONTRACTED or
        ACTUAL would launder an assumption into a measurement.

        **Zero and blank values are skipped.** All five settings seed as ``"0"``,
        so a zero is indistinguishable from never-configured, and writing it as
        a rule would convert an honest MISSING into an authoritative-looking
        "this costs nothing" for all of history — the exact substitution this
        table was built to stop. A store that genuinely has no packing cost can
        say so by writing an explicit rule.
        """
        created = 0
        for setting_key, cost_type, unit in LEGACY_SETTING_MAP:
            row = db.execute(
                select(SystemSetting).where(SystemSetting.key == setting_key)
            ).scalar_one_or_none()
            if row is None:
                logger.info("cost rule seed: %s not present, skipped", setting_key)
                continue
            raw = (row.value or "").strip()
            if not raw:
                logger.info("cost rule seed: %s is blank, skipped", setting_key)
                continue
            try:
                value = Decimal(raw)
            except InvalidOperation:
                logger.warning(
                    "cost rule seed: %s holds unparseable %r, skipped",
                    setting_key,
                    raw,
                )
                continue
            if value == 0:
                logger.info(
                    "cost rule seed: %s is 0 (unconfigured), skipped — a missing "
                    "cost must stay missing, not become a zero rule",
                    setting_key,
                )
                continue

            exists = db.execute(
                select(AnalyticsCostRule.id).where(
                    AnalyticsCostRule.cost_type == cost_type,
                    AnalyticsCostRule.scope == CostScope.GLOBAL,
                    AnalyticsCostRule.scope_value == "-",
                    AnalyticsCostRule.effective_from == effective_from,
                )
            ).scalar_one_or_none()
            if exists is not None:
                continue

            db.add(
                AnalyticsCostRule(
                    cost_type=cost_type,
                    scope=CostScope.GLOBAL,
                    scope_value="-",
                    value=value,
                    unit=unit,
                    currency="INR",
                    quality=CostQuality.ESTIMATED,
                    effective_from=effective_from,
                    effective_to=None,
                    source=LEGACY_SOURCE,
                    note=f"migrated from {setting_key}",
                )
            )
            created += 1

        db.flush()
        return created
