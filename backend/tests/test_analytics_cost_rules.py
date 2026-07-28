"""Regression tests for CostRuleResolver.

The behaviour under test is not "does the arithmetic work" — it is "does a
number the system does not know ever get reported as zero". Everything else
here (scope precedence, effective dating, unit maths) exists to serve that.

Strategy mirrors test_sales_analytics_service.py:
- no shared db fixture; each test opens its own SessionLocal();
- every test-owned row is deleted in a finally block, via a fresh session so
  teardown never fails because of a half-rolled-back transaction;
- each test uses a UNIQUE synthetic cost_type, so a resolution can never pick
  up another test's rows or a real configured rule from the DB.
"""
from __future__ import annotations

import random
import uuid
from datetime import date, timedelta
from decimal import Decimal

import redis
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.analytics_control import (
    AnalyticsCostRule,
    CostQuality,
    CostScope,
    CostType,
    CostUnit,
)
from app.models.system_setting import SystemSetting
from app.services.analytics.cost_rules import (
    LEGACY_SETTING_MAP,
    LEGACY_SOURCE,
    CostRuleResolver,
)
from app.services.analytics.types import MetricQuality


# ---------------------------------------------------------------------------
# Redis doubles
# ---------------------------------------------------------------------------

class _FakeRedis:
    """In-process stand-in. Per-instance state, so one test can never serve a
    cached answer to another — which for a cache of *missing* rules would hide
    exactly the bug these tests exist to catch."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.gets = 0

    def get(self, key: str):
        self.gets += 1
        return self.store.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.store[key] = value

    def delete(self, key: str) -> None:
        self.store.pop(key, None)

    def scan_iter(self, match: str = "*", count: int = 100):
        prefix = match.rstrip("*")
        return [k for k in list(self.store) if k.startswith(prefix)]


class _BrokenRedis:
    """Every call raises, the way a real client does when Redis is unreachable."""

    def get(self, key: str):
        raise redis.RedisError("connection refused")

    def setex(self, key: str, ttl: int, value: str):
        raise redis.RedisError("connection refused")

    def delete(self, key: str):
        raise redis.RedisError("connection refused")

    def scan_iter(self, match: str = "*", count: int = 100):
        raise redis.RedisError("connection refused")


# ---------------------------------------------------------------------------
# DB helpers (local, not shared with other test modules)
# ---------------------------------------------------------------------------

def _cost_type() -> str:
    """A cost_type nothing else in the DB can be using."""
    return f"test_cost_{uuid.uuid4().hex[:12]}"


def _rule(
    db: Session,
    cost_type: str,
    *,
    value: str,
    unit: str = CostUnit.PER_ORDER,
    scope: str = CostScope.GLOBAL,
    scope_value: str = "-",
    effective_from: date,
    effective_to: date | None = None,
    quality: str = CostQuality.CONTRACTED,
    source: str = "test",
) -> AnalyticsCostRule:
    row = AnalyticsCostRule(
        cost_type=cost_type,
        scope=scope,
        scope_value=scope_value,
        value=Decimal(value),
        unit=unit,
        currency="INR",
        quality=quality,
        effective_from=effective_from,
        effective_to=effective_to,
        source=source,
    )
    db.add(row)
    db.flush()
    return row


def _resolver(db: Session) -> CostRuleResolver:
    """A resolver with a private cache, so each call is decided by the DB."""
    return CostRuleResolver(db, redis_client=_FakeRedis())


def _cleanup_rules(rule_ids: list[int]) -> None:
    if not rule_ids:
        return
    with SessionLocal() as s:
        s.execute(
            text("DELETE FROM analytics_cost_rules WHERE id IN :ids"),
            {"ids": tuple(rule_ids)},
        )
        s.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCostRuleResolution:

    def test_no_rule_resolves_to_missing_not_zero(self) -> None:
        """The whole point of the module: an absent rule is MISSING.

        A zero would be indistinguishable from free packaging and would make
        every margin built on it read better than reality."""
        db = SessionLocal()
        try:
            component = _resolver(db).resolve(_cost_type(), date(2026, 3, 15))

            assert component.is_missing is True
            assert component.quality is MetricQuality.INCOMPLETE
            assert component.value_minor is None
            # Spelled out separately because `None == 0` is False but
            # `not None` and `not 0` are both True — a downstream falsy check
            # would collapse the two, and this is the assertion that catches
            # anyone "fixing" the resolver by defaulting to 0.
            assert component.value_minor != 0
            assert component.rule_id is None
        finally:
            db.close()

    def test_rule_does_not_apply_before_its_effective_from(self) -> None:
        """A rate that starts on 1 March says nothing about 28 February."""
        rule_ids: list[int] = []
        db = SessionLocal()
        try:
            ct = _cost_type()
            rule_ids.append(
                _rule(db, ct, value="8.00", effective_from=date(2026, 3, 1)).id
            )
            db.commit()

            before = _resolver(db).resolve(ct, date(2026, 2, 28))
            assert before.is_missing is True
            assert before.quality is MetricQuality.INCOMPLETE

            on_day = _resolver(db).resolve(ct, date(2026, 3, 1))
            assert on_day.is_missing is False
            assert on_day.value_minor == 800  # ₹8.00 in paise
        finally:
            _cleanup_rules(rule_ids)
            db.close()

    def test_rule_stops_applying_after_effective_to(self) -> None:
        """A closed window is closed — the day after is uncovered, not stale."""
        rule_ids: list[int] = []
        db = SessionLocal()
        try:
            ct = _cost_type()
            rule_ids.append(
                _rule(
                    db,
                    ct,
                    value="8.00",
                    effective_from=date(2026, 1, 1),
                    effective_to=date(2026, 6, 30),
                ).id
            )
            db.commit()

            assert _resolver(db).resolve(ct, date(2026, 6, 30)).value_minor == 800
            after = _resolver(db).resolve(ct, date(2026, 7, 1))
            assert after.is_missing is True
            assert after.value_minor is None
        finally:
            _cleanup_rules(rule_ids)
            db.close()

    def test_scope_precedence_product_beats_category_beats_global(self) -> None:
        """Most specific matching scope wins, for the same date."""
        rule_ids: list[int] = []
        db = SessionLocal()
        try:
            ct = _cost_type()
            eff = date(2026, 1, 1)
            rule_ids.append(_rule(db, ct, value="1.00", effective_from=eff).id)
            rule_ids.append(
                _rule(
                    db,
                    ct,
                    value="2.00",
                    scope=CostScope.CATEGORY,
                    scope_value="9",
                    effective_from=eff,
                ).id
            )
            rule_ids.append(
                _rule(
                    db,
                    ct,
                    value="3.00",
                    scope=CostScope.PRODUCT,
                    scope_value="123",
                    effective_from=eff,
                ).id
            )
            db.commit()

            on = date(2026, 3, 15)

            full = _resolver(db).resolve(
                ct, on, scope_candidates={"product": "123", "category": "9"}
            )
            assert full.scope == CostScope.PRODUCT
            assert full.value_minor == 300

            cat = _resolver(db).resolve(ct, on, scope_candidates={"category": "9"})
            assert cat.scope == CostScope.CATEGORY
            assert cat.value_minor == 200

            # A bucket on a different product/category falls back to global —
            # a scoped rule applies to its own scope value and nothing else.
            other = _resolver(db).resolve(
                ct, on, scope_candidates={"product": "999", "category": "42"}
            )
            assert other.scope == CostScope.GLOBAL
            assert other.value_minor == 100

            assert _resolver(db).resolve(ct, on).value_minor == 100
        finally:
            _cleanup_rules(rule_ids)
            db.close()

    def test_same_scope_latest_effective_from_wins(self) -> None:
        """Two open-ended global rules: the later start is the current rate."""
        rule_ids: list[int] = []
        db = SessionLocal()
        try:
            ct = _cost_type()
            rule_ids.append(
                _rule(db, ct, value="8.00", effective_from=date(2026, 1, 1)).id
            )
            rule_ids.append(
                _rule(db, ct, value="12.00", effective_from=date(2026, 7, 1)).id
            )
            db.commit()

            after_both = _resolver(db).resolve(ct, date(2026, 9, 1))
            assert after_both.value_minor == 1200
        finally:
            _cleanup_rules(rule_ids)
            db.close()

    def test_editing_a_rate_does_not_rewrite_history(self) -> None:
        """The regression the entire design exists for.

        Under the old `costs.*` setting, raising the rate in July silently
        changed what March had always reported. With effective dating, March
        keeps resolving March's rule."""
        rule_ids: list[int] = []
        db = SessionLocal()
        try:
            ct = _cost_type()
            rule_ids.append(
                _rule(db, ct, value="8.00", effective_from=date(2026, 1, 1)).id
            )
            db.commit()

            march = date(2026, 3, 15)
            assert _resolver(db).resolve(ct, march).value_minor == 800

            # The admin "edits the rate" the correct way: a new row from July.
            rule_ids.append(
                _rule(db, ct, value="12.00", effective_from=date(2026, 7, 1)).id
            )
            db.commit()

            assert _resolver(db).resolve(ct, date(2026, 7, 15)).value_minor == 1200
            # ...and March is untouched.
            assert _resolver(db).resolve(ct, march).value_minor == 800
        finally:
            _cleanup_rules(rule_ids)
            db.close()

    def test_rule_quality_maps_onto_metric_quality(self) -> None:
        """ACTUAL survives; everything else is ESTIMATED, never better."""
        rule_ids: list[int] = []
        db = SessionLocal()
        try:
            actual_ct, contracted_ct, assumed_ct = (
                _cost_type(),
                _cost_type(),
                _cost_type(),
            )
            eff = date(2026, 1, 1)
            for ct, q in (
                (actual_ct, CostQuality.ACTUAL),
                (contracted_ct, CostQuality.CONTRACTED),
                (assumed_ct, CostQuality.ASSUMED),
            ):
                rule_ids.append(
                    _rule(db, ct, value="5.00", effective_from=eff, quality=q).id
                )
            db.commit()

            on = date(2026, 4, 1)
            r = _resolver(db)
            assert r.resolve(actual_ct, on).quality is MetricQuality.ACTUAL
            assert r.resolve(contracted_ct, on).quality is MetricQuality.ESTIMATED
            assert r.resolve(assumed_ct, on).quality is MetricQuality.ESTIMATED
        finally:
            _cleanup_rules(rule_ids)
            db.close()


class TestCostRuleComputation:

    def test_each_unit_computes_correctly(self) -> None:
        """One rule per unit, each against a hand-checked expected paisa value."""
        rule_ids: list[int] = []
        db = SessionLocal()
        try:
            eff = date(2026, 1, 1)
            pct_ct, order_ct, unit_ct, kg_ct, month_ct = (_cost_type() for _ in range(5))
            rule_ids += [
                _rule(db, pct_ct, value="2.5000", unit=CostUnit.PCT,
                      effective_from=eff).id,
                _rule(db, order_ct, value="8.0000", unit=CostUnit.PER_ORDER,
                      effective_from=eff).id,
                _rule(db, unit_ct, value="1.2500", unit=CostUnit.PER_UNIT,
                      effective_from=eff).id,
                _rule(db, kg_ct, value="40.0000", unit=CostUnit.PER_KG,
                      effective_from=eff).id,
                _rule(db, month_ct, value="30000.0000", unit=CostUnit.PER_MONTH,
                      effective_from=eff).id,
            ]
            db.commit()

            on = date(2026, 4, 10)
            r = _resolver(db)

            # 2.5% of ₹1,000.00 = ₹25.00
            pct = r.compute(r.resolve(pct_ct, on), base_minor=100_000)
            assert pct.value_minor == 2_500

            # ₹8.00 x 3 orders = ₹24.00
            per_order = r.compute(r.resolve(order_ct, on), orders=3)
            assert per_order.value_minor == 2_400

            # ₹1.25 x 4 units = ₹5.00
            per_unit = r.compute(r.resolve(unit_ct, on), units=4)
            assert per_unit.value_minor == 500

            # ₹40.00/kg x 2.5kg = ₹100.00
            per_kg = r.compute(r.resolve(kg_ct, on), weight_grams=2_500)
            assert per_kg.value_minor == 10_000

            # ₹30,000/month, 10 of April's 30 days = ₹10,000.00
            per_month = r.compute(
                r.resolve(month_ct, on), days_in_period=10, on_date=on
            )
            assert per_month.value_minor == 1_000_000

            # Same rule, same 7-day period, different month lengths. February
            # 2026 has 28 days, so 7 days is a quarter of the month; March has
            # 31, so it is not. Pro-rating against a hardcoded 30 would get
            # both wrong.
            feb = date(2026, 2, 10)
            feb_part = r.compute(
                r.resolve(month_ct, feb), days_in_period=7, on_date=feb
            )
            assert feb_part.value_minor == 750_000  # 30000 * 7/28 = ₹7,500

            mar = date(2026, 3, 10)
            mar_part = r.compute(
                r.resolve(month_ct, mar), days_in_period=7, on_date=mar
            )
            assert mar_part.value_minor == 677_419  # 30000 * 7/31 = ₹6,774.19

            # Every computed component keeps the rule's provenance.
            assert per_order.rule_id is not None
            assert per_order.quality is MetricQuality.ESTIMATED
        finally:
            _cleanup_rules(rule_ids)
            db.close()

    def test_per_kg_with_unknown_weight_is_incomplete_not_zero(self) -> None:
        """A rule existing does not mean its input is known.

        No shipment weighs nothing, so weight_grams=0 means "not captured".
        Multiplying a known rate by an unknown weight must not produce a
        confident ₹0 shipping cost."""
        rule_ids: list[int] = []
        db = SessionLocal()
        try:
            ct = _cost_type()
            rule_ids.append(
                _rule(
                    db,
                    ct,
                    value="40.0000",
                    unit=CostUnit.PER_KG,
                    effective_from=date(2026, 1, 1),
                    quality=CostQuality.ACTUAL,
                ).id
            )
            db.commit()

            r = _resolver(db)
            resolved = r.resolve(ct, date(2026, 4, 1))
            assert resolved.is_missing is False  # the rule itself is present

            computed = r.compute(resolved, weight_grams=0)
            assert computed.value_minor is None
            assert computed.value_minor != 0
            assert computed.quality is MetricQuality.INCOMPLETE
            # Provenance is retained — an admin can see which rule could not be
            # applied, rather than just an unexplained hole.
            assert computed.rule_id == resolved.rule_id
        finally:
            _cleanup_rules(rule_ids)
            db.close()

    def test_per_month_without_a_period_or_date_is_incomplete(self) -> None:
        """A zero-day period is not a measurement, and a month whose length is
        unknown cannot be pro-rated. Neither may fall back to a guess."""
        rule_ids: list[int] = []
        db = SessionLocal()
        try:
            ct = _cost_type()
            rule_ids.append(
                _rule(
                    db,
                    ct,
                    value="30000.0000",
                    unit=CostUnit.PER_MONTH,
                    effective_from=date(2026, 1, 1),
                ).id
            )
            db.commit()

            on = date(2026, 4, 10)
            r = _resolver(db)
            resolved = r.resolve(ct, on)

            no_days = r.compute(resolved, days_in_period=0, on_date=on)
            assert no_days.value_minor is None
            assert no_days.quality is MetricQuality.INCOMPLETE

            no_date = r.compute(resolved, days_in_period=10)
            assert no_date.value_minor is None
            assert no_date.quality is MetricQuality.INCOMPLETE
        finally:
            _cleanup_rules(rule_ids)
            db.close()

    def test_genuine_zero_volume_is_a_real_zero(self) -> None:
        """The counterpart to the tests above: a bucket with no orders really
        does incur no per-order packing cost. That 0 is a measurement and must
        NOT be downgraded to INCOMPLETE, or every quiet day would poison the
        margin's quality label."""
        rule_ids: list[int] = []
        db = SessionLocal()
        try:
            ct = _cost_type()
            rule_ids.append(
                _rule(
                    db,
                    ct,
                    value="8.0000",
                    unit=CostUnit.PER_ORDER,
                    effective_from=date(2026, 1, 1),
                ).id
            )
            db.commit()

            r = _resolver(db)
            computed = r.compute(r.resolve(ct, date(2026, 4, 1)), orders=0)
            assert computed.value_minor == 0
            assert computed.is_missing is False
            assert computed.quality is MetricQuality.ESTIMATED
        finally:
            _cleanup_rules(rule_ids)
            db.close()

    def test_computing_a_missing_component_stays_missing(self) -> None:
        """MISSING propagates through compute() untouched — it never acquires a
        value just because bases were supplied."""
        db = SessionLocal()
        try:
            r = _resolver(db)
            missing = r.resolve(_cost_type(), date(2026, 4, 1))
            computed = r.compute(missing, base_minor=100_000, orders=5, units=9)
            assert computed.value_minor is None
            assert computed.quality is MetricQuality.INCOMPLETE
        finally:
            db.close()


class TestLegacySettingsSeed:

    def test_seed_is_idempotent_and_estimated(self) -> None:
        """Second run creates nothing, and everything it created is ESTIMATED.

        Zero-valued legacy settings are deliberately skipped — the seeded
        default for all five is "0", which is indistinguishable from
        never-configured, and writing it as a rule would turn an honest MISSING
        into a permanent "this costs nothing"."""
        rule_ids: list[int] = []
        original: dict[str, str | None] = {}
        db = SessionLocal()
        try:
            # Real, non-zero values so the migration has something to migrate.
            # Two of the five are left at 0 to prove they are skipped.
            wanted = {
                "costs.gateway_fee_pct": "2.3625",
                "costs.packing_per_order": "8",
                "costs.handling_per_order": "12.50",
                "costs.monthly_overheads": "0",
                "costs.monthly_ad_spend": "0",
            }
            for key, value in wanted.items():
                row = (
                    db.query(SystemSetting).filter(SystemSetting.key == key).one()
                )
                original[key] = row.value
                row.value = value
            db.commit()

            # An effective_from far outside any real data, so the UNIQUE key
            # (cost_type, scope, scope_value, effective_from) cannot collide
            # with a rule a human actually wrote.
            eff = date(1990, 1, 1) + timedelta(days=random.randrange(3650))

            created = CostRuleResolver.seed_from_legacy_settings(db, eff)
            db.commit()
            assert created == 3, "only the three non-zero settings migrate"

            rows = (
                db.query(AnalyticsCostRule)
                .filter(
                    AnalyticsCostRule.effective_from == eff,
                    AnalyticsCostRule.source == LEGACY_SOURCE,
                )
                .all()
            )
            rule_ids = [r.id for r in rows]
            assert len(rows) == 3

            by_type = {r.cost_type: r for r in rows}
            assert by_type[CostType.GATEWAY_FEE].unit == CostUnit.PCT
            assert by_type[CostType.GATEWAY_FEE].value == Decimal("2.3625")
            assert by_type[CostType.PACKAGING].unit == CostUnit.PER_ORDER
            assert by_type[CostType.HANDLING].unit == CostUnit.PER_ORDER
            assert CostType.MONTHLY_OVERHEAD not in by_type
            assert CostType.MARKETING_SPEND not in by_type

            for row in rows:
                assert row.quality == CostQuality.ESTIMATED
                assert row.scope == CostScope.GLOBAL
                assert row.scope_value == "-"

            # Idempotent: rerunning the migration is a no-op, not a duplicate
            # and not an IntegrityError on the UNIQUE key.
            again = CostRuleResolver.seed_from_legacy_settings(db, eff)
            db.commit()
            assert again == 0
            assert (
                db.query(AnalyticsCostRule)
                .filter(
                    AnalyticsCostRule.effective_from == eff,
                    AnalyticsCostRule.source == LEGACY_SOURCE,
                )
                .count()
                == 3
            )

            # And the migrated rules resolve, carrying ESTIMATED through.
            resolved = _resolver(db).resolve(
                CostType.GATEWAY_FEE, eff + timedelta(days=30)
            )
            assert resolved.rule_id == by_type[CostType.GATEWAY_FEE].id
            assert resolved.quality is MetricQuality.ESTIMATED
            assert resolved.source == LEGACY_SOURCE
        finally:
            with SessionLocal() as s:
                for key, value in original.items():
                    row = (
                        s.query(SystemSetting)
                        .filter(SystemSetting.key == key)
                        .one_or_none()
                    )
                    if row is not None:
                        row.value = value
                s.commit()
            _cleanup_rules(rule_ids)
            db.close()

    def test_legacy_map_covers_exactly_the_five_costs_settings(self) -> None:
        """The map is the migration contract; a dropped key would silently
        leave a configured cost behind."""
        assert {key for key, _t, _u in LEGACY_SETTING_MAP} == {
            "costs.gateway_fee_pct",
            "costs.packing_per_order",
            "costs.handling_per_order",
            "costs.monthly_overheads",
            "costs.monthly_ad_spend",
        }


class TestCaching:

    def test_resolution_works_when_redis_is_unavailable(self) -> None:
        """Redis down degrades to a straight DB read. It must never raise, and
        it must never turn a present rule into a missing one."""
        rule_ids: list[int] = []
        db = SessionLocal()
        try:
            ct = _cost_type()
            rule_ids.append(
                _rule(db, ct, value="8.00", effective_from=date(2026, 1, 1)).id
            )
            db.commit()

            resolver = CostRuleResolver(db, redis_client=_BrokenRedis())

            found = resolver.resolve(ct, date(2026, 4, 1))
            assert found.value_minor == 800
            assert found.quality is MetricQuality.ESTIMATED

            computed = resolver.compute(found, orders=2)
            assert computed.value_minor == 1_600

            missing = resolver.resolve(_cost_type(), date(2026, 4, 1))
            assert missing.is_missing is True

            # invalidate_all() swallows the error too rather than breaking the
            # admin write path when the cache is down.
            assert resolver.invalidate_all() == 0
        finally:
            _cleanup_rules(rule_ids)
            db.close()

    def test_second_resolution_is_served_from_cache(self) -> None:
        """Including the miss — a store with no rules configured must not hit
        the DB once per bucket."""
        rule_ids: list[int] = []
        db = SessionLocal()
        try:
            ct = _cost_type()
            rule_ids.append(
                _rule(db, ct, value="8.00", effective_from=date(2026, 1, 1)).id
            )
            db.commit()

            fake = _FakeRedis()
            resolver = CostRuleResolver(db, redis_client=fake)
            on = date(2026, 4, 1)

            first = resolver.resolve(ct, on)
            assert len(fake.store) == 1

            second = resolver.resolve(ct, on)
            assert second == first
            assert fake.gets == 2

            # A different scope candidate set is a different key, because it
            # changes which rules are allowed to match.
            resolver.resolve(ct, on, scope_candidates={"gateway": "razorpay"})
            assert len(fake.store) == 2

            # The miss is cached under its own sentinel, not as a rule.
            resolver.resolve(_cost_type(), on)
            assert len(fake.store) == 3

            assert resolver.invalidate_all() == 3
            assert fake.store == {}
        finally:
            _cleanup_rules(rule_ids)
            db.close()
