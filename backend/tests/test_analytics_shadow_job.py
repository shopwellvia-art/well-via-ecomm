"""Tests for ``shadow_compare`` — the job that makes shadow mode actually run.

``shadow.py`` was already complete and already tested. What was missing was
anything that *called* it on a schedule, which meant the retirement gate had no
history to read: "the comparison was clean for thirty days" and "the comparison
has never run" produced identical silence. These tests are about that gap, so
they check the wiring rather than re-checking the arithmetic
``test_analytics_shadow.py`` already pins.

Four properties carry the weight:

* **It is in ``JOBS``.** ``worker._run_scheduled`` runs ``sorted(JOBS)`` over the
  trailing window, so registration *is* the scheduling. A job that had to be
  added to the worker by hand would be one deploy away from silently not
  running.
* **The verdict survives the process.** The gate spans thirty days; an in-memory
  report answers nothing about the twenty-nine before it. Every test that runs
  the job asserts the row it left in ``analytics_sync_runs`` and then reads it
  back through ``readiness`` rather than through the object it just held.
* **Re-running is free.** ``test_rerunning_a_dirty_bucket_does_not_duplicate_the_alert``
  proves the dedup key in ``shadow._raise_alerts`` genuinely covers a re-run.
  Nothing was added to make that true — the test is here to show it already is,
  because a job whose retry doubles the alert count is a job nobody retries.
* **"Not ready" always says why.** Every ``readiness`` assertion below is on the
  *reason text*, never on the boolean alone. A gate that refuses without naming
  the day, the metric or the number of days still missing is unactionable, and
  a boolean-only assertion would pass on one.

Isolation strategy
------------------
Every fixture lives in **2021**, years before this store's first order and a
year no other suite uses. That last part is a convenience and is deliberately
not load-bearing: ``_assert_sandbox_is_empty`` checks the **one reporting day**
under test rather than the whole year, and the cost rules seeded here are
effective for that day plus a margin of one, so this suite neither trips over
nor perturbs a suite that later lands in another month of 2021. A sandbox that
claims a year is a sandbox that breaks the next time somebody picks the same
one, which is a failure this file has already had once.

The four sandbox days are more than thirty days apart on purpose: ``readiness``
looks back thirty days from the day it is asked about, so no test can see
another test's evidence even before teardown runs.

``readiness`` reads global state — the run log and every open shadow alert — so
assertions here are on the presence or absence of *specific* reasons naming this
module's days and metrics, never on the length of the reason list, which
anything else in the database could change.

No db fixture exists in ``conftest.py``; each test owns its ``SessionLocal()``
and closes it in ``finally``. Teardown runs through a *fresh* session so a
half-rolled-back transaction cannot skip it.

``CostRuleResolver`` caches resolutions in Redis for 60s, **including misses**,
so every seed and teardown calls ``invalidate_all()``.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.analytics_control import (
    AlertStatus,
    AnalyticsAlert,
    AnalyticsCostRule,
    AnalyticsSyncRun,
    CostQuality,
    CostScope,
    CostType,
    CostUnit,
    SyncStatus,
)
from app.models.analytics_rollups import AggOrderDaily
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Category, Product
from app.models.user import User
from app.services.analytics.aggregation import JOBS, AggregationRunner
from app.services.analytics.aggregation.base import AggregationJob, get_job
from app.services.analytics.aggregation.jobs_shadow import (
    BUCKET_BUDGET_MS,
    VERDICT_MARKER,
    ShadowCompareJob,
    readiness,
    recorded_verdicts,
)
from app.services.analytics.cost_rules import CostRuleResolver
from app.services.analytics.shadow import (
    CONSECUTIVE_DAYS_REQUIRED,
    GATE_METRICS,
    SHADOW_JOB_NAME,
)
from app.services.analytics.timebox import (
    active_generation,
    day_bounds_utc,
    store_timezone,
)

# ---------------------------------------------------------------------------
# The 2021 sandbox
# ---------------------------------------------------------------------------

#: One reporting day per scenario, each more than `CONSECUTIVE_DAYS_REQUIRED`
#: days from the next, so `readiness(as_of=X)` — which looks back thirty days
#: from X — can never see another test's evidence.
#:
#: 2021 because nothing else in this repo touches it. Claiming a *year* is not
#: relied on, though: the guard below and the cost rules seeded here are scoped
#: to the single day under test, so a suite that later lands in another month of
#: 2021 cannot collide with this one and this one cannot perturb it.
CLEAN_DAY = date(2021, 3, 10)
DIRTY_DAY = date(2021, 5, 10)
EXPECTED_DAY = date(2021, 7, 10)
SHORT_DAY = date(2021, 9, 10)

ALL_SANDBOX_DAYS = [CLEAN_DAY, DIRTY_DAY, EXPECTED_DAY, SHORT_DAY]

RULE_SOURCE = "test_analytics_shadow_job"
WORKER_ID = "shadow-job-test"

#: Every input CM2/CM3 needs, so the cascade resolves and the comparison
#: exercises the real bridges instead of short-circuiting on a missing input.
COST_RULES: tuple[tuple[str, str, Decimal], ...] = (
    (CostType.GATEWAY_FEE, CostUnit.PCT, Decimal("2.0")),
    (CostType.PACKAGING, CostUnit.PER_ORDER, Decimal("15")),
    (CostType.HANDLING, CostUnit.PER_ORDER, Decimal("10")),
    (CostType.FORWARD_SHIPPING, CostUnit.PER_ORDER, Decimal("50")),
    (CostType.RETURN_SHIPPING, CostUnit.PER_ORDER, Decimal("45")),
    (CostType.RTO_LOGISTICS, CostUnit.PER_ORDER, Decimal("90")),
    (CostType.MARKETPLACE_COMMISSION, CostUnit.PCT, Decimal("0")),
    (CostType.MARKETING_SPEND, CostUnit.PER_MONTH, Decimal("280")),
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


class _Owned:
    """Ids this test created, so teardown deletes exactly them and nothing else."""

    def __init__(self) -> None:
        self.orders: list[int] = []
        self.products: list[int] = []
        self.categories: list[int] = []
        self.users: list[int] = []


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _create_user(db: Session, owned: _Owned) -> User:
    user = User(
        email=f"shadowjob-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.flush()
    owned.users.append(user.id)
    return user


def _create_product(db: Session, owned: _Owned) -> Product:
    suffix = _uid()
    category = Category(name=f"ShadowJob {suffix}", slug=f"shadowjob-{suffix}")
    db.add(category)
    db.flush()
    owned.categories.append(category.id)
    product = Product(
        sku=f"SKU-SHADOWJOB-{_uid()}",
        name=f"ShadowJobProduct {_uid()}",
        price=Decimal("500.00"),
        cost=Decimal("200.00"),
        stock=100,
        category_id=category.id,
    )
    db.add(product)
    db.flush()
    owned.products.append(product.id)
    return product


def _create_order(
    db: Session,
    owned: _Owned,
    user: User,
    product: Product,
    *,
    created_at: datetime,
    discount: str = "50.00",
    payment_discount: str = "0",
    shipping: str = "40.00",
) -> Order:
    """One paid order whose ``total_amount`` satisfies the revenue bridge."""
    gross = product.price * 2
    discounts = Decimal(discount) + Decimal(payment_discount)
    total = gross - discounts + Decimal(shipping)
    order = Order(
        user_id=user.id,
        status=OrderStatus.PAID,
        subtotal=gross,
        tax_amount=Decimal("0"),
        discount_amount=Decimal(discount),
        payment_discount_amount=Decimal(payment_discount),
        shipping_amount=Decimal(shipping),
        cod_surcharge_amount=Decimal("0"),
        cod_balance=Decimal("0.00"),
        total_amount=total,
        currency="INR",
        payment_method="prepaid",
        created_at=created_at,
    )
    order.items = [
        OrderItem(
            product_id=product.id,
            quantity=2,
            unit_price=product.price,
            unit_cost=product.cost,
        )
    ]
    db.add(order)
    db.flush()
    owned.orders.append(order.id)
    return order


def _rule_window(day: date) -> tuple[date, date]:
    """The effective window of this module's cost rules for ``day``.

    One day either side, and never open-ended. Cost rules are global and
    effective-dated, so a rule that reached beyond the bucket under test would
    silently change the margin figures of every other suite that resolves costs
    for a day in its range.
    """
    return day - timedelta(days=1), day + timedelta(days=1)


def _seed_cost_rules(db: Session, day: date) -> None:
    effective_from, effective_to = _rule_window(day)
    for cost_type, unit, value in COST_RULES:
        db.add(
            AnalyticsCostRule(
                cost_type=cost_type,
                scope=CostScope.GLOBAL,
                scope_value="-",
                value=value,
                unit=unit,
                currency="INR",
                quality=CostQuality.CONTRACTED,
                effective_from=effective_from,
                effective_to=effective_to,
                source=RULE_SOURCE,
                note="shadow job test fixture",
            )
        )
    db.commit()
    CostRuleResolver(db).invalidate_all()


def _assert_sandbox_is_empty(db: Session, day: date) -> None:
    """Fail loudly if anything foreign already lives in this bucket.

    Scoped to the one reporting day under test, not to a whole year: a stray
    order elsewhere in 2021 cannot change this bucket's figures, and failing on
    it would make this suite hostage to every other suite's choice of sandbox.
    What *would* change the figures is an order inside these bounds or a cost
    rule covering this day, and both are checked.
    """
    start, end = day_bounds_utc(day, store_timezone(db))
    stray = (
        db.execute(
            select(Order.id).where(Order.created_at >= start, Order.created_at < end)
        )
        .scalars()
        .all()
    )
    if stray:
        pytest.fail(
            f"orders this module did not create already live in the {day} bucket "
            f"and would change every expected figure: {stray[:10]}"
        )
    rule_from, rule_to = _rule_window(day)
    foreign = [
        r
        for r in db.execute(
            select(AnalyticsCostRule).where(
                AnalyticsCostRule.effective_from <= rule_to,
                or_(
                    AnalyticsCostRule.effective_to.is_(None),
                    AnalyticsCostRule.effective_to >= rule_from,
                ),
            )
        ).scalars()
        if (r.source or "") != RULE_SOURCE
    ]
    if foreign:
        pytest.fail(
            f"cost rules not owned by this test cover the {day} bucket: "
            + ", ".join(f"#{r.id} {r.cost_type}" for r in foreign)
        )


def _midday(db: Session, day: date) -> datetime:
    """Noon of the store-local reporting day ``day``, as a UTC instant.

    Mid-day on purpose: it sits inside the legacy UTC window *and* the aligned
    store-local one, so no timezone difference is in play and any divergence
    these tests see is the one they created.
    """
    start, _ = day_bounds_utc(day, store_timezone(db))
    return start + timedelta(hours=12)


def _seed_day(
    db: Session, owned: _Owned, day: date, *, payment_discount: str = "0"
) -> None:
    """One paid order on ``day``, with the rollups the new pages read built."""
    _assert_sandbox_is_empty(db, day)
    _seed_cost_rules(db, day)
    user = _create_user(db, owned)
    product = _create_product(db, owned)
    _create_order(
        db,
        owned,
        user,
        product,
        created_at=_midday(db, day),
        payment_discount=payment_discount,
    )
    db.commit()
    runner = AggregationRunner(db, worker_id=WORKER_ID)
    runner.run_bucket("order_daily", day)
    runner.run_bucket("product_daily", day)
    db.commit()


def _run_shadow(db: Session, day: date) -> AnalyticsSyncRun:
    """Run ``shadow_compare`` for one bucket exactly as the worker would.

    Through the runner, not by calling ``job.run`` directly: the runner owns the
    transaction and the enclosing run row, and both are part of what is being
    tested.
    """
    AggregationRunner(db, worker_id=WORKER_ID).run_bucket(SHADOW_JOB_NAME, day)
    return _verdict_run(db, day)


def _verdict_run(db: Session, day: date) -> AnalyticsSyncRun:
    """The latest verdict row this job wrote for ``day``."""
    run = (
        db.execute(
            select(AnalyticsSyncRun)
            .where(
                AnalyticsSyncRun.job == SHADOW_JOB_NAME,
                AnalyticsSyncRun.window_from == day,
                AnalyticsSyncRun.window_to == day,
                AnalyticsSyncRun.error.like(f"{VERDICT_MARKER}%"),
            )
            .order_by(AnalyticsSyncRun.id.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    assert run is not None, (
        f"shadow_compare left no verdict row for {day}; a comparison that is not "
        "persisted cannot be read by a gate that spans thirty days"
    )
    return run


def _verdict_runs(db: Session, day: date) -> list[AnalyticsSyncRun]:
    return list(
        db.execute(
            select(AnalyticsSyncRun)
            .where(
                AnalyticsSyncRun.job == SHADOW_JOB_NAME,
                AnalyticsSyncRun.window_from == day,
                AnalyticsSyncRun.error.like(f"{VERDICT_MARKER}%"),
            )
            .order_by(AnalyticsSyncRun.id.asc())
        )
        .scalars()
        .all()
    )


def _open_alerts(db: Session, day: date) -> list[AnalyticsAlert]:
    return list(
        db.execute(
            select(AnalyticsAlert)
            .where(
                AnalyticsAlert.dimension == "shadow_metric",
                AnalyticsAlert.bucket_date == day,
                AnalyticsAlert.status == AlertStatus.OPEN,
            )
            .order_by(AnalyticsAlert.id.asc())
        )
        .scalars()
        .all()
    )


def _nudge_discounts(db: Session, day: date, amount: str = "500.00") -> None:
    """Corrupt one rollup column by a plausible amount.

    ``agg_order_daily.discount_sum`` is chosen because exactly one comparison
    reads it (``discounts``), so the resulting divergence is one difference and
    one alert — which is what makes "exactly one alert" a real assertion rather
    than an artefact of how many metrics happen to share a column.
    """
    row = db.execute(
        select(AggOrderDaily).where(
            AggOrderDaily.bucket_date == day,
            AggOrderDaily.tz_generation == int(active_generation(db).generation),
        )
    ).scalar_one()
    row.discount_sum = row.discount_sum + Decimal(amount)
    db.commit()


def _cleanup(owned: _Owned) -> None:
    with SessionLocal() as s:
        if owned.orders:
            s.execute(
                text("DELETE FROM order_items WHERE order_id IN :ids"),
                {"ids": tuple(owned.orders)},
            )
            s.execute(
                text("DELETE FROM orders WHERE id IN :ids"),
                {"ids": tuple(owned.orders)},
            )
        if owned.products:
            s.execute(
                text("DELETE FROM products WHERE id IN :ids"),
                {"ids": tuple(owned.products)},
            )
        if owned.categories:
            s.execute(
                text("DELETE FROM categories WHERE id IN :ids"),
                {"ids": tuple(owned.categories)},
            )
        if owned.users:
            s.execute(
                text("DELETE FROM users WHERE id IN :ids"),
                {"ids": tuple(owned.users)},
            )
        s.execute(
            text("DELETE FROM analytics_cost_rules WHERE source = :src"),
            {"src": RULE_SOURCE},
        )
        for table in ("agg_order_daily", "agg_product_daily", "agg_order_hourly"):
            s.execute(
                text(f"DELETE FROM {table} WHERE bucket_date IN :days"),
                {"days": tuple(ALL_SANDBOX_DAYS)},
            )
        # Scoped, not "every shadow_compare row": this module must not delete a
        # real store's retirement evidence just because it ran on the same
        # database.
        s.execute(
            text(
                "DELETE FROM analytics_sync_runs WHERE worker_id = :w "
                "OR (job = :job AND window_from IN :days)"
            ),
            {"w": WORKER_ID, "job": SHADOW_JOB_NAME, "days": tuple(ALL_SANDBOX_DAYS)},
        )
        s.execute(
            text(
                "DELETE FROM analytics_alerts WHERE dimension = 'shadow_metric' "
                "AND bucket_date IN :days"
            ),
            {"days": tuple(ALL_SANDBOX_DAYS)},
        )
        s.commit()
    CostRuleResolver(SessionLocal()).invalidate_all()


# ---------------------------------------------------------------------------
# 1. Registration — the whole of the scheduling
# ---------------------------------------------------------------------------


def test_the_job_is_registered_and_that_is_the_whole_integration() -> None:
    """``shadow_compare`` is in ``JOBS``, so the existing worker already runs it.

    ``worker._run_scheduled`` iterates ``sorted(JOBS)``; the admin recompute
    endpoint and the dirty-bucket queue both resolve names through ``get_job``.
    Registration is therefore the entire integration, and a job that instead had
    to be named in the worker would be one deploy away from silently not
    running, with nothing in the run log to say so.
    """
    assert SHADOW_JOB_NAME == "shadow_compare"
    assert SHADOW_JOB_NAME in JOBS, sorted(JOBS)

    job = get_job(SHADOW_JOB_NAME)
    assert isinstance(job, ShadowCompareJob)
    assert job.name == SHADOW_JOB_NAME, (
        "the registry key and the job's own name must agree; they are the same "
        "string in analytics_recompute_queue.job and analytics_sync_runs.job"
    )
    assert isinstance(job, AggregationJob), (
        "the job must satisfy the AggregationJob protocol — run(db, bucket_date, "
        "tz_generation) -> JobRunResult — or the runner cannot call it"
    )
    # The worker iterates `sorted(JOBS)`, so this is literally the scheduling.
    assert SHADOW_JOB_NAME in sorted(JOBS)


# ---------------------------------------------------------------------------
# 2. A clean bucket
# ---------------------------------------------------------------------------


def test_a_clean_bucket_writes_a_success_run_and_raises_no_alert() -> None:
    """The base case, and the one that makes every refusal below mean something.

    A single mid-day paid order with every cost rule present: the two systems
    are looking at the same rows over the same instants and have nothing to
    disagree about. The bucket must leave a ``success`` row in
    ``analytics_sync_runs`` carrying a decodable verdict, and no alert.

    The run row is the point. An in-memory report answers nothing about the
    twenty-nine days before it, so the assertions here are on what survived the
    call, not on what it returned.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _seed_day(db, owned, CLEAN_DAY)

        run = _run_shadow(db, CLEAN_DAY)

        assert run.status == SyncStatus.SUCCESS
        assert run.window_from == CLEAN_DAY and run.window_to == CLEAN_DAY
        assert run.days_requested == 1 and run.days_processed == 1
        assert run.watermark_date == CLEAN_DAY, (
            "the watermark is a DATA watermark — the bucket that was compared, "
            "not the clock time the comparison ran"
        )
        assert run.worker_id == WORKER_ID, (
            "the verdict row inherits the enclosing runner run, so it ties back "
            "to the process that produced it"
        )
        assert run.duration_ms is not None

        # No alert may exist for a clean bucket.
        assert _open_alerts(db, CLEAN_DAY) == []

        # The verdict reads back, and says the bucket was clean.
        verdicts = recorded_verdicts(db, as_of=CLEAN_DAY)
        assert [v.bucket for v in verdicts] == [CLEAN_DAY]
        verdict = verdicts[0]
        assert verdict.unexplained == 0, verdict
        assert verdict.alerts == 0
        assert verdict.differences == run.rows_written, (
            "rows_written is the number of differences this job recorded; the "
            "encoded verdict must agree with the column"
        )
        # Every gate metric was compared, and the values were persisted so a
        # later refusal can quote real figures instead of the fact that figures
        # once existed.
        assert sorted(f.metric for f in verdict.gate) == sorted(GATE_METRICS)
        assert all(f.legacy is not None and f.new is not None for f in verdict.gate)

        # Bounded: one bucket must fit inside the share of the runner's budget it
        # can fairly claim, because this job runs alongside twelve rollups that
        # stop being rebuilt when the budget runs out.
        assert run.duration_ms < BUCKET_BUDGET_MS, (
            f"one bucket took {run.duration_ms}ms against a {BUCKET_BUDGET_MS}ms "
            "budget; shadow_compare runs the legacy services live against `orders`"
        )
    finally:
        db.close()
        _cleanup(owned)


# ---------------------------------------------------------------------------
# 3-4. An unexplained difference, and re-running it
# ---------------------------------------------------------------------------


def test_an_unexplained_difference_raises_exactly_one_alert_naming_the_metric() -> None:
    """A corrupted rollup is caught, named, and recorded — not absorbed.

    ``agg_order_daily.discount_sum`` is nudged by ₹500. No registered
    explanation can account for it: ``payment_discount_included`` and
    ``revenue_recognition`` both measure zero on this fixture, so the residual is
    the whole delta and the difference defaults to UNEXPLAINED, which is the
    property the entire module rests on.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _seed_day(db, owned, DIRTY_DAY)

        clean = _run_shadow(db, DIRTY_DAY)
        assert _open_alerts(db, DIRTY_DAY) == [], (
            "the bucket must be clean before it is corrupted, or the alert below "
            "proves nothing about the corruption"
        )
        assert clean.status == SyncStatus.SUCCESS

        _nudge_discounts(db, DIRTY_DAY)
        run = _run_shadow(db, DIRTY_DAY)

        alerts = _open_alerts(db, DIRTY_DAY)
        assert len(alerts) == 1, [
            (a.metric, a.dimension_value) for a in alerts
        ]
        alert = alerts[0]
        assert alert.metric == "discounts"
        assert alert.status == AlertStatus.OPEN
        assert alert.bucket_date == DIRTY_DAY
        assert alert.context["job"] == SHADOW_JOB_NAME
        assert alert.context["residual"] == 50000, (
            "the residual must be the whole delta — no registered explanation may "
            "absorb any part of a corrupted rollup"
        )
        # 50.00 of real discount plus the 500.00 nudge.
        assert alert.actual_value == Decimal("550.00")

        # The run recorded it too, so the gate can see it without the alert.
        verdict = recorded_verdicts(db, as_of=DIRTY_DAY)[0]
        assert verdict.unexplained == 1
        assert verdict.unexplained_metrics == ("discounts",)
        assert verdict.alerts == 1
        assert run.rows_written == verdict.differences >= 1

        # The run itself is a success: the comparison ran. A divergence is not a
        # failed run, and marking it `partial` would tell
        # `AggregationRunner._resume_from` to skip this bucket next tick —
        # silently retiring the comparison for exactly the day that diverged.
        assert run.status == SyncStatus.SUCCESS
    finally:
        db.close()
        _cleanup(owned)


def test_rerunning_a_dirty_bucket_does_not_duplicate_the_alert() -> None:
    """Re-running has to be free, or nobody re-runs.

    ``shadow._raise_alerts`` de-duplicates on
    ``(rule, metric, dimension value, bucket, OPEN)``. Nothing was added here to
    make that true — this test exists to prove the existing key genuinely covers
    a re-run of the same bucket, because the recompute queue's lease can expire
    and hand the same bucket to a second worker.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _seed_day(db, owned, DIRTY_DAY)
        _run_shadow(db, DIRTY_DAY)
        _nudge_discounts(db, DIRTY_DAY)

        _run_shadow(db, DIRTY_DAY)
        first = _open_alerts(db, DIRTY_DAY)
        assert len(first) == 1

        _run_shadow(db, DIRTY_DAY)
        _run_shadow(db, DIRTY_DAY)
        again = _open_alerts(db, DIRTY_DAY)

        assert [a.id for a in again] == [a.id for a in first], (
            "re-running the bucket created a second copy of a divergence somebody "
            "is already looking at"
        )
        assert again[0].detected_at == first[0].detected_at, (
            "the existing alert must be left exactly as it was — an alert has to "
            "be able to justify itself later"
        )

        # The run log, by contract, is append-only: each re-run is its own row,
        # and the gate reads the latest one per bucket.
        runs = _verdict_runs(db, DIRTY_DAY)
        assert len(runs) == 4, [r.id for r in runs]
        assert len(recorded_verdicts(db, as_of=DIRTY_DAY)) == 1, (
            "four runs of one bucket are one day of evidence, not four"
        )
    finally:
        db.close()
        _cleanup(owned)


# ---------------------------------------------------------------------------
# 5-7. readiness()
# ---------------------------------------------------------------------------


def test_readiness_names_how_many_days_of_evidence_are_still_missing() -> None:
    """One clean day is not thirty, and the refusal has to be a number."""
    owned = _Owned()
    db = SessionLocal()
    try:
        _seed_day(db, owned, SHORT_DAY)
        _run_shadow(db, SHORT_DAY)

        ready, reasons = readiness(db, as_of=SHORT_DAY)

        assert not ready
        assert reasons, "'not ready' without a reason is unactionable"
        coverage = [
            r for r in reasons if f"of {CONSECUTIVE_DAYS_REQUIRED} consecutive" in r
        ]
        assert len(coverage) == 1, reasons
        assert f"only 1 of {CONSECUTIVE_DAYS_REQUIRED}" in coverage[0], coverage
        assert f"{CONSECUTIVE_DAYS_REQUIRED - 1} more day(s)" in coverage[0], coverage
        assert str(SHORT_DAY) in coverage[0], coverage

        # And with nothing recorded at all it is a different, equally specific
        # message — "the pipeline never ran" must not read as "it ran clean".
        _cleanup(owned)
        # Teardown ran on another session, and MySQL's REPEATABLE READ would
        # otherwise keep serving this transaction's snapshot of rows that are
        # already gone.
        db.rollback()
        ready, reasons = readiness(db, as_of=SHORT_DAY)
        assert not ready
        assert reasons and "no shadow comparison has run yet" in reasons[0], reasons
    finally:
        db.close()
        _cleanup(owned)


def test_readiness_refuses_while_an_unexplained_difference_is_open() -> None:
    """The refusal names the metric, the day, and where to go next."""
    owned = _Owned()
    db = SessionLocal()
    try:
        _seed_day(db, owned, DIRTY_DAY)
        _run_shadow(db, DIRTY_DAY)
        _nudge_discounts(db, DIRTY_DAY)
        _run_shadow(db, DIRTY_DAY)

        ready, reasons = readiness(db, as_of=DIRTY_DAY)

        assert not ready
        assert any("discounts" in r and str(DIRTY_DAY) in r for r in reasons), reasons
        # Two independent records block it, and both are actionable on their own:
        # the run log says the bucket diverged, the alerts table says which alert
        # is still open. Both assertions name the day, so nothing else in this
        # shared database can satisfy them by accident.
        assert any(
            "unexplained difference(s) on discounts" in r and str(DIRTY_DAY) in r
            for r in reasons
        ), reasons
        assert any(
            "is still open" in r and "discounts" in r and str(DIRTY_DAY) in r
            for r in reasons
        ), reasons
    finally:
        db.close()
        _cleanup(owned)


def test_an_expected_difference_is_recorded_and_does_not_block_readiness() -> None:
    """A registered, documented difference is evidence, not a blocker.

    The order carries a ₹30 payment discount, which the legacy Sales page does
    not count and the new subsystem does. That is
    ``payment_discount_included`` — registered, documented, and attributed to
    the paisa — so the bucket records a difference, raises no alert, and adds no
    reason to the gate. A gate that treated every difference as a defect would
    never open, and a gate that never opens is indistinguishable from a broken
    one.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        _seed_day(db, owned, EXPECTED_DAY, payment_discount="30.00")

        run = _run_shadow(db, EXPECTED_DAY)

        assert _open_alerts(db, EXPECTED_DAY) == [], (
            "an expected difference must not alert; alerting on documented "
            "differences is how an alert stream stops being read"
        )
        verdict = recorded_verdicts(db, as_of=EXPECTED_DAY)[0]
        assert verdict.expected >= 1, (
            "the fixture must actually produce a registered difference, or this "
            "test proves nothing"
        )
        assert verdict.unexplained == 0
        assert run.rows_written == verdict.differences >= 1

        ready, reasons = readiness(db, as_of=EXPECTED_DAY)

        # Still not ready — one day is not thirty — but the *only* thing standing
        # in the way is the missing evidence, not the expected difference.
        assert not ready
        # Scoped to this day: anything else in this shared database may have its
        # own open divergences, and this test is about whether *this* bucket
        # blocks the gate.
        blocking = [r for r in reasons if str(EXPECTED_DAY) in r]
        assert not any("discounts" in r for r in blocking), blocking
        assert not any("unexplained" in r for r in blocking), blocking
        assert not any("differs by" in r for r in blocking), blocking
        assert [
            r for r in reasons if f"of {CONSECUTIVE_DAYS_REQUIRED} consecutive" in r
        ], reasons
    finally:
        db.close()
        _cleanup(owned)
