"""Tests for the analytics cache and the analytics repository allowlist.

Two things in this pair can fail in ways that no chart would reveal, so they get
the direct assertions:

**A cache entry read by someone who may not read it.** The analytics cache is
keyed per *permission tier*, not per user, because per-user keys would have a
hit rate near zero. That makes the key itself the access control: if `visibility`
were missing from it, the first requester holding `analytics.finance.view` would
warm an entry containing margin and COGS that any later requester could read
straight back out, with the permission check on the endpoint passing happily
because it never runs against the cache. `test_a_finance_tier_entry_is_not_
readable_at_a_lower_tier` is the assertion that stops that.

**An identifier from a request reaching the SQL compiler.** The repository takes
`source`, `columns`, `group_by` and `order_by` as strings. Every one of them is
resolved through an allowlist reflected from the ORM models, so
`"1; DROP TABLE agg_order_daily"` must fail a dict lookup rather than be
rendered. The test asserts both halves: that it raises, *and* that the table is
still there afterwards.

Everything else here is about degradation. Redis being down must cost the API
its cache and nothing else — no exception escapes this module.

House style, matching test_analytics_queue.py / test_analytics_aggregation.py:
there is no shared db fixture in conftest, so each DB test opens its own
`SessionLocal()` and closes it in a `finally`. Fixture rows live under synthetic
`tz_generation` values in the 32000s that no real aggregation run can produce
(the writer always uses the active generation, which starts at 1), so these
tests cannot see or be seen by real rows, and teardown deletes exactly the rows
they own.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
import redis
from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.analytics_rollups import AggOrderDaily, AggProductDaily
from app.repositories.analytics_repository import (
    HARD_ROW_CAP,
    AnalyticsRepository,
    known_sources,
    measures_for,
)
from app.services.analytics.cache import (
    GENERATION_KEY,
    TTL_BY_FRESHNESS,
    AnalyticsCache,
    ttl_for,
)
from app.services.analytics.filters import ResolvedWindow

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeRedis:
    """Just enough Redis for this module: get / setex / incr, plus TTL capture."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def setex(self, key: str, ttl: int, value: str) -> bool:
        self.store[key] = value
        self.ttls[key] = ttl
        return True

    def incr(self, key: str) -> int:
        value = int(self.store.get(key, 0)) + 1
        self.store[key] = str(value)
        return value


class BrokenRedis:
    """Every call raises, exactly as a wedged or unreachable Redis does.

    `redis.Redis` has bounded socket timeouts (see app/db/redis.py), so a dead
    Redis surfaces as RedisError rather than a hang — this is that case.
    """

    def __getattr__(self, name: str):
        def _boom(*args, **kwargs):
            raise redis.ConnectionError(f"redis is down (called {name})")

        return _boom


def _cache(client=None) -> AnalyticsCache:
    return AnalyticsCache(redis_client=client if client is not None else FakeRedis())


# ---------------------------------------------------------------------------
# DB fixtures: synthetic generations no real run can produce
# ---------------------------------------------------------------------------

#: Two synthetic timezone generations, to exercise the mixed-generation guard.
GEN_A = 32000
GEN_B = 32001
#: A generation that deliberately never has a row, for the "no rows yet" case.
GEN_EMPTY = 32090
_ALL_GENERATIONS = (GEN_A, GEN_B, GEN_EMPTY)

#: Years before this store's first order, so nothing real can land in the window.
#: June **2008**, deliberately not the June 2009 sandbox used by
#: test_analytics_aggregation.py — that module deletes `agg_order_daily` rows by
#: bucket_date range across the whole month without filtering on tz_generation,
#: so sharing the month would couple the two suites' teardown.
DAY_1 = date(2008, 6, 1)
DAY_2 = date(2008, 6, 2)

WINDOW = ResolvedWindow(date_from=DAY_1, date_to=DAY_2 + timedelta(days=1))


def _cleanup() -> None:
    """Delete every row these tests own, through a fresh session."""
    with SessionLocal() as s:
        s.execute(
            delete(AggOrderDaily).where(AggOrderDaily.tz_generation.in_(_ALL_GENERATIONS))
        )
        s.execute(
            delete(AggProductDaily).where(
                AggProductDaily.tz_generation.in_(_ALL_GENERATIONS)
            )
        )
        s.commit()


def _order_row(db: Session, *, bucket: date, generation: int, **cols) -> None:
    db.add(AggOrderDaily(bucket_date=bucket, tz_generation=generation, **cols))


# ===========================================================================
# 1. Round trip
# ===========================================================================


def test_set_and_get_round_trips_a_payload() -> None:
    cache = _cache()
    key = cache.build_key(
        module="sales", view="revenue_trend", filter_hash="abc123",
        tz_generation=1, visibility="standard",
    )
    payload = {"rows": [{"bucket_date": "2026-07-01", "net_revenue": "1180.00"}]}

    cache.set(key, payload, ttl=TTL_BY_FRESHNESS["daily"])

    assert cache.get(key) == payload


def test_set_uses_the_ttl_it_was_given() -> None:
    client = FakeRedis()
    cache = _cache(client)
    key = cache.build_key(
        module="sales", view="revenue_trend", filter_hash="abc123",
        tz_generation=1, visibility="standard",
    )
    cache.set(key, {"ok": True}, ttl=TTL_BY_FRESHNESS["hourly"])
    assert client.ttls[key] == 900


# ===========================================================================
# 2. Generation bump makes old entries unreachable
# ===========================================================================


def test_bumping_the_generation_makes_an_old_key_unreachable() -> None:
    """Invalidation is O(1): nothing is deleted, the key is simply never built
    again. The old entry lingers in Redis until its TTL, which is fine — it is
    unreachable, because every key carries the generation it was written under."""
    client = FakeRedis()
    writer = _cache(client)
    key_args = dict(
        module="sales", view="revenue_trend", filter_hash="abc123",
        tz_generation=1, visibility="standard",
    )
    old_key = writer.build_key(**key_args)
    writer.set(old_key, {"net_revenue": "1180.00"}, ttl=3600)
    assert writer.get(old_key) == {"net_revenue": "1180.00"}

    new_generation = writer.invalidate_all()

    # A fresh reader (no in-process memo) builds a different key and misses.
    reader = AnalyticsCache(redis_client=client)
    new_key = reader.build_key(**key_args)
    assert new_key != old_key
    assert f":g{new_generation}:" in new_key
    assert reader.get(new_key) is None
    # And nothing was SCANned or deleted to achieve that.
    assert old_key in client.store


def test_bump_generation_increments_the_shared_counter() -> None:
    client = FakeRedis()
    cache = _cache(client)
    before = cache.generation()
    cache.bump_generation()
    assert int(client.store[GENERATION_KEY]) == before + 1


# ===========================================================================
# 3. Visibility tiers cannot share an entry
# ===========================================================================


def test_a_finance_tier_entry_is_not_readable_at_a_lower_tier() -> None:
    """The whole reason `visibility` is in the key.

    A requester with `analytics.finance.view` warms an entry containing COGS and
    margin. A requester without it must not be able to read that entry back —
    and because the tier is part of the key, they cannot even name it.
    """
    client = FakeRedis()
    finance = _cache(client)
    standard = AnalyticsCache(redis_client=client)

    key_args = dict(
        module="finance", view="margin_by_product", filter_hash="deadbeef",
        tz_generation=1,
    )
    finance_key = finance.build_key(visibility="finance", **key_args)
    standard_key = standard.build_key(visibility="standard", **key_args)

    assert finance_key != standard_key

    finance.set(finance_key, {"cogs_sum": "44000.00", "margin_pct": "38.1"}, ttl=900)

    assert standard.get(standard_key) is None
    assert finance.get(finance_key) == {"cogs_sum": "44000.00", "margin_pct": "38.1"}


def test_key_components_may_not_smuggle_a_separator() -> None:
    """Defence in depth: a `visibility` of `"standard:view:finance"` would let a
    lower tier construct a higher tier's key by collision."""
    cache = _cache()
    for bad in ("standard:finance", "", "has space", "a/b"):
        with pytest.raises(ValueError):
            cache.build_key(
                module="finance", view="margin", filter_hash="abc",
                tz_generation=1, visibility=bad,
            )


# ===========================================================================
# 4. tz_generation is part of the key
# ===========================================================================


def test_a_different_tz_generation_is_a_different_key() -> None:
    """A reporting-timezone change re-buckets history, so the same filters over
    the same dates are a different answer. A shared entry would serve the old
    day boundaries under the new generation."""
    client = FakeRedis()
    cache = _cache(client)
    key_args = dict(
        module="sales", view="revenue_trend", filter_hash="abc123",
        visibility="standard",
    )
    key_gen_1 = cache.build_key(tz_generation=1, **key_args)
    key_gen_2 = cache.build_key(tz_generation=2, **key_args)

    assert key_gen_1 != key_gen_2
    assert ":t1:" in key_gen_1 and ":t2:" in key_gen_2

    cache.set(key_gen_1, {"net_revenue": "1180.00"}, ttl=900)
    assert cache.get(key_gen_2) is None


def test_the_key_format_is_exactly_the_documented_one() -> None:
    cache = _cache()
    key = cache.build_key(
        module="sales", view="revenue_trend", filter_hash="abc123",
        tz_generation=4, visibility="finance",
    )
    assert key == f"an:v1:g{cache.generation()}:t4:finance:view:sales/revenue_trend:abc123"


# ===========================================================================
# 5. A window including today is never stable
# ===========================================================================


def test_ttl_downgrades_to_realtime_when_the_window_includes_today() -> None:
    """A 30-day window ending today is not a daily answer — today's bucket is
    rewritten by every aggregation run."""
    assert ttl_for("daily", window_includes_today=False) == TTL_BY_FRESHNESS["daily"]
    assert ttl_for("daily", window_includes_today=True) == TTL_BY_FRESHNESS["realtime"]
    assert ttl_for("hourly", window_includes_today=True) == TTL_BY_FRESHNESS["realtime"]
    assert ttl_for("hourly", window_includes_today=False) == TTL_BY_FRESHNESS["hourly"]
    # An unrecognised class gets the shortest TTL, never the longest.
    assert ttl_for("weekly", window_includes_today=False) == TTL_BY_FRESHNESS["realtime"]


# ===========================================================================
# 6. Redis down degrades, never raises
# ===========================================================================


def test_redis_unavailable_degrades_and_never_raises() -> None:
    cache = AnalyticsCache(redis_client=BrokenRedis())

    assert cache.generation() == 0

    key = cache.build_key(
        module="sales", view="revenue_trend", filter_hash="abc123",
        tz_generation=1, visibility="standard",
    )
    assert key.startswith("an:v1:g0:")

    assert cache.set(key, {"net_revenue": "1180.00"}, ttl=900) is None  # no-op
    assert cache.get(key) is None
    assert cache.bump_generation() is None
    assert cache.invalidate_all() == 0


def test_a_generation_counter_holding_garbage_reads_as_zero() -> None:
    client = FakeRedis()
    client.store[GENERATION_KEY] = "not-a-number"
    assert AnalyticsCache(redis_client=client).generation() == 0


# ===========================================================================
# 7. Corrupt payloads are a miss, not an exception
# ===========================================================================


def test_corrupt_cached_json_is_treated_as_a_miss() -> None:
    client = FakeRedis()
    cache = _cache(client)
    key = cache.build_key(
        module="sales", view="revenue_trend", filter_hash="abc123",
        tz_generation=1, visibility="standard",
    )

    client.store[key] = '{"rows": [{"net_rev'  # truncated write
    assert cache.get(key) is None

    client.store[key] = json.dumps([1, 2, 3])  # valid JSON, wrong shape
    assert cache.get(key) is None

    # And the miss is recoverable: recomputing overwrites the bad entry.
    cache.set(key, {"rows": []}, ttl=45)
    assert cache.get(key) == {"rows": []}


# ===========================================================================
# 8. The repository allowlist
# ===========================================================================


def test_an_unknown_source_raises_before_any_query() -> None:
    db = SessionLocal()
    try:
        repo = AnalyticsRepository(db)
        with pytest.raises(ValueError, match="unknown analytics source"):
            repo.fetch_rollup(
                "orders",  # not a rollup table
                columns=["net_revenue"],
                window=WINDOW,
                tz_generation=GEN_A,
            )
        assert "agg_order_daily" in known_sources()
    finally:
        db.close()


def test_an_unknown_column_raises() -> None:
    db = SessionLocal()
    try:
        repo = AnalyticsRepository(db)
        with pytest.raises(ValueError, match="unknown analytics column"):
            repo.fetch_rollup(
                "agg_order_daily",
                columns=["profit_margin_pct"],  # not a column of this rollup
                window=WINDOW,
                tz_generation=GEN_A,
            )
        # A real column of a *different* rollup is still rejected here.
        with pytest.raises(ValueError, match="unknown analytics column"):
            repo.fetch_totals(
                "agg_order_daily",
                columns=["courier_partner"],
                window=WINDOW,
                tz_generation=GEN_A,
            )
    finally:
        db.close()


def test_a_column_containing_sql_raises_rather_than_executing() -> None:
    """The property this file exists for.

    Every identifier position — column, group_by, order_by, filter key — is a
    dict lookup against columns reflected from the ORM. A hostile string cannot
    be rendered, so the table is still there afterwards.
    """
    injection = "1; DROP TABLE agg_order_daily"
    db = SessionLocal()
    try:
        repo = AnalyticsRepository(db)

        with pytest.raises(ValueError):
            repo.fetch_rollup(
                "agg_order_daily", columns=[injection], window=WINDOW, tz_generation=GEN_A
            )
        with pytest.raises(ValueError):
            repo.fetch_rollup(
                "agg_order_daily",
                columns=["net_revenue"],
                group_by=[injection],
                window=WINDOW,
                tz_generation=GEN_A,
            )
        with pytest.raises(ValueError):
            repo.fetch_rollup(
                "agg_order_daily",
                columns=["net_revenue"],
                order_by="net_revenue; DROP TABLE agg_order_daily",
                window=WINDOW,
                tz_generation=GEN_A,
            )
        with pytest.raises(ValueError):
            repo.fetch_rollup(
                "agg_order_daily",
                columns=["net_revenue"],
                filters={injection: 1},
                window=WINDOW,
                tz_generation=GEN_A,
            )
        with pytest.raises(ValueError):
            repo.fetch_rollup(
                injection, columns=["net_revenue"], window=WINDOW, tz_generation=GEN_A
            )
        with pytest.raises(ValueError):
            repo.source_watermark(injection, GEN_A)

        # Nothing was executed: the table still answers.
        db.rollback()
        assert db.execute(text("SELECT COUNT(*) FROM agg_order_daily")).scalar() is not None
        assert repo.source_watermark("agg_order_daily", GEN_EMPTY) == (None, 0)
    finally:
        db.close()


def test_a_non_summable_column_cannot_be_aggregated() -> None:
    """`SUM(rfm_segment)` is nonsense; so is `SUM(bucket_hour)`. Grain columns
    and identity references are groupable dimensions, never measures."""
    assert "net_revenue" in measures_for("agg_order_daily")
    assert "bucket_date" not in measures_for("agg_order_daily")
    assert "tz_generation" not in measures_for("agg_order_daily")
    assert "product_id" not in measures_for("agg_product_daily")
    assert "bucket_hour" not in measures_for("agg_order_hourly")

    db = SessionLocal()
    try:
        repo = AnalyticsRepository(db)
        with pytest.raises(ValueError, match="summable"):
            repo.fetch_rollup(
                "agg_product_daily",
                columns=["units", "sku_snapshot"],
                group_by=["product_id"],
                window=WINDOW,
                tz_generation=GEN_A,
            )
    finally:
        db.close()


def test_a_limit_past_the_hard_cap_raises_instead_of_truncating() -> None:
    db = SessionLocal()
    try:
        repo = AnalyticsRepository(db)
        with pytest.raises(ValueError, match="cap"):
            repo.fetch_rollup(
                "agg_order_daily",
                columns=["net_revenue"],
                window=WINDOW,
                tz_generation=GEN_A,
                limit=HARD_ROW_CAP + 1,
            )
    finally:
        db.close()


# ===========================================================================
# 9. "No rows yet" is distinguishable from a real zero
# ===========================================================================


def test_source_watermark_on_an_empty_table_is_none_and_zero() -> None:
    _cleanup()
    db = SessionLocal()
    try:
        repo = AnalyticsRepository(db)

        # Nothing built for this generation at all.
        assert repo.source_watermark("agg_order_daily", GEN_EMPTY) == (None, 0)
        empty_window = ResolvedWindow(date_from=DAY_1, date_to=DAY_2)
        assert repo.fetch_totals(
            "agg_order_daily",
            columns=["net_revenue"],
            window=empty_window,
            tz_generation=GEN_EMPTY,
        ) == {"net_revenue": None}

        # A day that WAS built and genuinely earned nothing looks different:
        # the watermark is real and the total is an actual 0.
        _order_row(
            db, bucket=DAY_1, generation=GEN_A,
            orders_total=0, net_revenue=Decimal("0.00"), units=0,
        )
        db.commit()

        newest, count = repo.source_watermark("agg_order_daily", GEN_A)
        assert newest == DAY_1
        assert count == 1
        totals = repo.fetch_totals(
            "agg_order_daily",
            columns=["net_revenue"],
            window=WINDOW,
            tz_generation=GEN_A,
        )
        assert totals["net_revenue"] == Decimal("0.00")
        assert totals["net_revenue"] is not None
    finally:
        db.close()
        _cleanup()


# ===========================================================================
# 10. Mixed-generation detection
# ===========================================================================


def test_distinct_tz_generations_reports_both_generations() -> None:
    """Feeds `timebox.assert_single_generation`. A window straddling a timezone
    rebuild must be refusable, which requires seeing both generations."""
    _cleanup()
    db = SessionLocal()
    try:
        repo = AnalyticsRepository(db)
        assert repo.distinct_tz_generations("agg_order_daily", WINDOW) == set()

        _order_row(db, bucket=DAY_1, generation=GEN_A, net_revenue=Decimal("100.00"))
        _order_row(db, bucket=DAY_1, generation=GEN_B, net_revenue=Decimal("110.00"))
        _order_row(db, bucket=DAY_2, generation=GEN_B, net_revenue=Decimal("120.00"))
        db.commit()

        assert repo.distinct_tz_generations("agg_order_daily", WINDOW) == {GEN_A, GEN_B}

        # A window covering only DAY_2 sees one generation, so it is servable.
        single = ResolvedWindow(date_from=DAY_2, date_to=DAY_2 + timedelta(days=1))
        assert repo.distinct_tz_generations("agg_order_daily", single) == {GEN_B}
    finally:
        db.close()
        _cleanup()


# ===========================================================================
# The queries themselves actually run
# ===========================================================================


def test_fetch_rollup_projects_only_the_requested_columns_within_the_window() -> None:
    _cleanup()
    db = SessionLocal()
    try:
        repo = AnalyticsRepository(db)
        _order_row(
            db, bucket=DAY_1, generation=GEN_A,
            net_revenue=Decimal("100.00"), orders_total=2,
        )
        _order_row(
            db, bucket=DAY_2, generation=GEN_A,
            net_revenue=Decimal("250.00"), orders_total=3,
        )
        # Same days, other generation — must not leak into a GEN_A read.
        _order_row(
            db, bucket=DAY_1, generation=GEN_B,
            net_revenue=Decimal("999.00"), orders_total=9,
        )
        db.commit()

        rows = repo.fetch_rollup(
            "agg_order_daily",
            columns=["bucket_date", "net_revenue", "orders_total"],
            window=WINDOW,
            tz_generation=GEN_A,
        )
        assert [set(r) for r in rows] == [
            {"bucket_date", "net_revenue", "orders_total"}
        ] * 2
        assert [r["bucket_date"] for r in rows] == [DAY_1, DAY_2]
        assert [r["net_revenue"] for r in rows] == [Decimal("100.00"), Decimal("250.00")]

        # Half-open upper bound: DAY_2 is excluded when date_to == DAY_2.
        first_day_only = repo.fetch_rollup(
            "agg_order_daily",
            columns=["bucket_date"],
            window=ResolvedWindow(date_from=DAY_1, date_to=DAY_2),
            tz_generation=GEN_A,
        )
        assert [r["bucket_date"] for r in first_day_only] == [DAY_1]

        # Ordering and limit.
        top = repo.fetch_rollup(
            "agg_order_daily",
            columns=["bucket_date", "net_revenue"],
            window=WINDOW,
            tz_generation=GEN_A,
            order_by="-net_revenue",
            limit=1,
        )
        assert top == [{"bucket_date": DAY_2, "net_revenue": Decimal("250.00")}]

        # Totals respect the same scope.
        assert repo.fetch_totals(
            "agg_order_daily",
            columns=["net_revenue", "orders_total"],
            window=WINDOW,
            tz_generation=GEN_A,
        ) == {"net_revenue": Decimal("350.00"), "orders_total": 5}
    finally:
        db.close()
        _cleanup()


def test_group_by_sums_measures_and_binds_filter_values() -> None:
    _cleanup()
    db = SessionLocal()
    try:
        repo = AnalyticsRepository(db)
        for bucket in (DAY_1, DAY_2):
            db.add(
                AggProductDaily(
                    bucket_date=bucket, tz_generation=GEN_A, product_id=901,
                    sku_snapshot="SBX-901", units=2,
                    net_merchandise_sales=Decimal("50.00"),
                )
            )
        db.add(
            AggProductDaily(
                bucket_date=DAY_1, tz_generation=GEN_A, product_id=902,
                sku_snapshot="SBX-902", units=7,
                net_merchandise_sales=Decimal("70.00"),
            )
        )
        db.commit()

        rows = repo.fetch_rollup(
            "agg_product_daily",
            columns=["units", "net_merchandise_sales"],
            group_by=["product_id"],
            window=WINDOW,
            tz_generation=GEN_A,
            order_by="-units",
        )
        assert rows == [
            {"product_id": 902, "units": 7, "net_merchandise_sales": Decimal("70.00")},
            {"product_id": 901, "units": 4, "net_merchandise_sales": Decimal("100.00")},
        ]

        # A filter value is bound, never interpolated — including one that is
        # nothing but SQL.
        filtered = repo.fetch_rollup(
            "agg_product_daily",
            columns=["units"],
            group_by=["product_id"],
            window=WINDOW,
            tz_generation=GEN_A,
            filters={"sku_snapshot": "SBX-901' OR '1'='1"},
        )
        assert filtered == []

        by_id = repo.fetch_rollup(
            "agg_product_daily",
            columns=["units"],
            group_by=["product_id"],
            window=WINDOW,
            tz_generation=GEN_A,
            filters={"product_id": [901]},
        )
        assert by_id == [{"product_id": 901, "units": 4}]

        # A grouping key that is also named in `columns` is projected once, not
        # twice — a duplicate label would silently drop one of the two values
        # when the row is turned into a dict.
        both = repo.fetch_rollup(
            "agg_product_daily",
            columns=["product_id", "units"],
            group_by=["product_id"],
            window=WINDOW,
            tz_generation=GEN_A,
            filters={"product_id": 902},
        )
        assert both == [{"product_id": 902, "units": 7}]
    finally:
        db.close()
        _cleanup()


def test_every_rollup_table_is_reachable_by_name() -> None:
    """All 12 rollups are in the allowlist, and every one of them can answer a
    watermark query — a table renamed in the model but not here would otherwise
    only fail the first time someone opened that view."""
    canonical = known_sources()
    assert len(canonical) == 12
    db = SessionLocal()
    try:
        repo = AnalyticsRepository(db)
        for source in canonical:
            newest, count = repo.source_watermark(source, GEN_EMPTY)
            assert (newest, count) == (None, 0)
    finally:
        db.close()
