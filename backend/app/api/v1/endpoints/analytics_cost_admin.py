"""Cost rules and marketing spend — the admin surface that lets a human type a number.

Routes
------
============================================ ====== =====================================
GET    /admin/cost-rules                     200    list rules + the form vocabularies
POST   /admin/cost-rules                     201    insert a rule for an uncovered window
POST   /admin/cost-rules/{id}/supersede      201    change a rate from a given day
GET    /admin/marketing-spend                200    list spend (+ daily allocation)
POST   /admin/marketing-spend                200/201 record what was spent in a period
============================================ ====== =====================================

Why this module exists
----------------------
`analytics_cost_rules` has had a table and a resolver since the v2 schema
landed, and no way to put a row in it. The consequence is not "a missing admin
screen": every margin metric resolves MISSING, every derived figure is labelled
INCOMPLETE, and the finance views stay gated forever — not because the
arithmetic is wrong but because nobody can enter a rate. Connecting Google and
Meta ad APIs is a much larger build; a form that lets a store say "packaging is
₹8 an order from March, and we spent ₹40,000 on Meta in June" unblocks the same
views today, and is what a real store actually has.

The one rule this module exists to enforce
------------------------------------------
**Editing a rate must not rewrite history.** Cost rules are effective-dated and
append-only in effect. There is no route here that UPDATEs `value`, and that is
not an oversight — it is the entire design. `POST /{id}/supersede` closes the
current row by setting its `effective_to`, and inserts a new row from the new
`effective_from`. March keeps resolving March's ₹8 afterwards, so last quarter's
board deck still reproduces.

An admin who thinks they are fixing a typo is versioning a rate, and the UI says
so out loud (`features/analytics/CostRulesAdmin.jsx`). The alternative — an
in-place UPDATE — restates every historical margin the store has ever reported,
silently, with nothing anywhere recording that it happened. That failure has no
error message and no symptom until someone tries to reproduce an old number.

Overlaps are refused, not resolved
----------------------------------
Two rules for the same `(cost_type, scope, scope_value)` whose windows intersect
make resolution order-dependent: `CostRuleResolver._select_rule` sorts by
precedence then `effective_from`, so whichever row sorts first wins and the
answer depends on data rather than on intent. The UNIQUE key catches only the
exact-same-start case (MySQL cannot express a range exclusion constraint), so the
range check is enforced here, on write, with a 409 that names the conflicting
row and its window.

Every write enqueues
--------------------
A rate change that quietly leaves the stored rollups alone has changed nothing
an admin can see. Each write enqueues every affected bucket into
`analytics_recompute_queue` with reason `COST_RULE_CHANGE`, and invalidates the
resolver's Redis memo so a worker draining that queue inside the 60s TTL cannot
recompute against the pre-edit answer. The response reports the queued window
and the row count, because "did my change take effect?" must be answerable
without reading the database.

The enqueue window is capped at `MAX_RECOMPUTE_DAYS`. A rate effective from two
years ago is legitimate; enqueuing 730 days × every cost-consuming job from one
form submission is not. Past the cap the response carries an explicit warning
naming the earliest day that was NOT enqueued and pointing at
`POST /analytics/admin/backfill` — loud and partial, never silent and partial.

Auth
----
Reads need `analytics.finance.view` — cost rates and ad spend are margin inputs
and sit in the same SENSITIVE tier as the margin itself. Writes need
`analytics.budgets.manage`, the existing manage-level grant for "what the
business said it intended to spend"; there is no separate costs permission in
`services/permissions_registry.py` and inventing one here would leave it
unseeded on every existing deployment.

Note that `require_permission` lets `is_admin` bypass every check — see
`User.has_permission`. That is the legacy admin flag working as designed, but it
means a test proving isolation with an admin account proves nothing. The tests
for this module use non-admin users with explicitly seeded grants and assert the
permission lookup itself, not only the status code.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, rate_limit_by_ip, require_permission
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.rate_limit import get_client_ip
from app.models.analytics_control import (
    AnalyticsCostRule,
    CostQuality,
    CostScope,
    CostType,
    CostUnit,
    RecomputeReason,
)
from app.models.analytics_spend import (
    AnalyticsMarketingSpend,
    SpendGrain,
    SpendQuality,
    allocate_row_to_days,
    month_bounds,
)
from app.models.user import User
from app.schemas.analytics_cost_admin import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    MAX_RECOMPUTE_DAYS,
    CostRuleCreate,
    CostRuleListResponse,
    CostRuleRead,
    CostRuleSupersedeRequest,
    CostRuleWriteResponse,
    MarketingSpendListResponse,
    MarketingSpendRead,
    MarketingSpendUpsert,
    MarketingSpendWriteResponse,
    SpendDayAllocation,
)
from app.services.analytics.cost_rules import CostRuleResolver
from app.services.analytics.queue import RecomputeQueue
from app.services.analytics.timebox import active_generation, local_day, store_timezone
from app.services.analytics.types import MetricQuality
from app.services.audit_service import AuditService

router = APIRouter()

#: Reading a cost rate tells you the store's margin structure, and reading ad
#: spend tells you its acquisition economics. Same SENSITIVE tier as the margin
#: views those numbers feed.
READ_PERMISSION = "analytics.finance.view"

#: Writing. The existing manage-level grant for planned/committed money; see the
#: module docstring for why this module does not mint a new permission name.
WRITE_PERMISSION = "analytics.budgets.manage"

#: Aggregation jobs whose stored buckets can contain a cost- or spend-derived
#: figure, and which therefore must be rebuilt when either changes. Validated
#: against the JOBS registry at enqueue time so a rename here can never write
#: queue rows no worker will ever claim — an invisible permanent backlog that
#: looks exactly like a successful write.
COST_AFFECTED_JOBS: tuple[str, ...] = (
    "order_daily",
    "product_daily",
    "payment_daily",
    "shipment_daily",
)

#: Writes are cheap per call but each one can touch four jobs × 400 days of
#: queue rows and invalidate the resolver cache. Generous for an admin working
#: through a rate card, low enough that a stolen session cannot churn the queue.
_WRITE_RATE_LIMIT = Depends(
    rate_limit_by_ip(scope="analytics.costs.write.ip", limit=30, window_sec=300)
)
#: Reads back a small table; a dashboard may poll it.
_READ_RATE_LIMIT = Depends(
    rate_limit_by_ip(scope="analytics.costs.read.ip", limit=300, window_sec=300)
)

_READ_AUTH = Depends(require_permission(READ_PERMISSION))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _store_today(db: Session) -> date:
    """Today as a store-local reporting day.

    Not `date.today()`: reporting days are store-local (see `timebox.py`), and a
    UTC "today" would enqueue tomorrow's bucket for 5.5 hours every evening in
    IST — a bucket with no data in it yet, which the worker would compute as
    empty and cache.
    """
    return local_day(datetime.now(timezone.utc), store_timezone(db))


def _validate_choice(value: str, allowed: tuple[str, ...], field: str) -> str:
    """Reject a value outside a known vocabulary, with 422 and the valid set.

    `cost_type`, `unit`, `scope` and `quality` are plain varchars in the database
    so a new cost line needs no migration. The cost of that flexibility is that
    a typo — `per_oder`, `pct ` — inserts happily and then never resolves, which
    presents as "the margin is still INCOMPLETE" with no error anywhere. The
    vocabulary check belongs here, at the one write path, not in the column type.
    """
    if value not in allowed:
        raise ValidationError(
            f"Unknown {field} {value!r}.",
            details={"field": field, "allowed": list(allowed)},
        )
    return value


def _overlapping(
    db: Session,
    *,
    cost_type: str,
    scope: str,
    scope_value: str,
    effective_from: date,
    effective_to: date | None,
    exclude_id: int | None = None,
) -> AnalyticsCostRule | None:
    """The first existing rule whose window intersects the proposed one, if any.

    Two inclusive windows `[a1, a2]` and `[b1, b2]` overlap iff
    ``a1 <= b2 AND b1 <= a2``, with a NULL end read as +infinity. Written out as
    two predicates rather than a clever expression because getting this wrong
    fails *open* — a missed overlap is not an error, it is two contradictory
    rules coexisting and a margin that depends on which one the optimiser
    reaches first.
    """
    stmt = select(AnalyticsCostRule).where(
        AnalyticsCostRule.cost_type == cost_type,
        AnalyticsCostRule.scope == scope,
        AnalyticsCostRule.scope_value == scope_value,
    )
    if exclude_id is not None:
        stmt = stmt.where(AnalyticsCostRule.id != exclude_id)

    # `effective_from <= proposed_to` — an open-ended proposal has no upper
    # bound, so every existing rule starting at or after ours qualifies.
    if effective_to is not None:
        stmt = stmt.where(AnalyticsCostRule.effective_from <= effective_to)

    # `proposed_from <= effective_to`, with NULL meaning still in force.
    stmt = stmt.where(
        (AnalyticsCostRule.effective_to.is_(None))
        | (AnalyticsCostRule.effective_to >= effective_from)
    )
    return db.execute(stmt.order_by(AnalyticsCostRule.effective_from.asc())).scalars().first()


def _reject_overlap(
    db: Session,
    *,
    cost_type: str,
    scope: str,
    scope_value: str,
    effective_from: date,
    effective_to: date | None,
    exclude_id: int | None = None,
) -> None:
    """409 if the proposed window collides with an existing rule.

    The message names the conflicting row's id and window, and says what to do
    instead, because "overlapping cost rule" with no coordinates is a dead end
    for the admin who hit it — the rule they need to supersede may be one of
    dozens on the screen.
    """
    clash = _overlapping(
        db,
        cost_type=cost_type,
        scope=scope,
        scope_value=scope_value,
        effective_from=effective_from,
        effective_to=effective_to,
        exclude_id=exclude_id,
    )
    if clash is None:
        return
    ends = clash.effective_to.isoformat() if clash.effective_to else "open-ended"
    raise ConflictError(
        f"Cost rule #{clash.id} already covers {cost_type}/{scope}:{scope_value} "
        f"from {clash.effective_from.isoformat()} to {ends}, which overlaps "
        f"{effective_from.isoformat()}–"
        f"{effective_to.isoformat() if effective_to else 'open-ended'}. "
        "Two overlapping rules would make the resolved rate depend on row order. "
        f"Supersede rule #{clash.id} instead of inserting alongside it.",
        details={
            "conflicting_rule_id": clash.id,
            "conflicting_effective_from": clash.effective_from.isoformat(),
            "conflicting_effective_to": (
                clash.effective_to.isoformat() if clash.effective_to else None
            ),
        },
    )


def _enqueue_affected(
    db: Session,
    *,
    date_from: date,
    date_to: date | None,
    reason: str = RecomputeReason.COST_RULE_CHANGE,
) -> tuple[int, list[str], date | None, date | None, list[str]]:
    """Mark every bucket a cost/spend change touched as dirty.

    Returns `(rows, jobs, from, to, warnings)`.

    The window runs from `date_from` to `min(date_to or today, today)`: future
    buckets have no facts to rebuild from, so enqueuing them would create work
    that computes an empty day and caches it. It is capped at
    `MAX_RECOMPUTE_DAYS` counted back from the end, and when the cap bites the
    caller is told the exact date that was left alone. Partial-and-loud beats
    partial-and-silent: a hole in a chart that nobody can trace to the request
    that caused it is the worse outcome by a distance.

    Flushes but does not commit — `RecomputeQueue.enqueue_many` is built to run
    inside the caller's transaction so an enqueue for a change that rolls back
    rolls back with it.
    """
    warnings: list[str] = []
    today = _store_today(db)
    window_end = min(date_to, today) if date_to is not None else today
    if window_end < date_from:
        # Wholly in the future: nothing computed yet, so nothing is stale.
        return 0, [], None, None, warnings

    span_days = (window_end - date_from).days + 1
    window_start = date_from
    if span_days > MAX_RECOMPUTE_DAYS:
        window_start = window_end - timedelta(days=MAX_RECOMPUTE_DAYS - 1)
        warnings.append(
            f"Only the most recent {MAX_RECOMPUTE_DAYS} days "
            f"({window_start.isoformat()} onwards) were enqueued for recompute. "
            f"Buckets from {date_from.isoformat()} to "
            f"{(window_start - timedelta(days=1)).isoformat()} are now stale and "
            "were NOT queued — rebuild them with POST /analytics/admin/backfill."
        )

    # Import here, not at module scope: `aggregation/__init__` imports every job
    # module for its registration side effect, and pulling that whole tree in at
    # router-import time would make this endpoint's import cost the aggregation
    # package's import cost for every request worker.
    from app.services.analytics.aggregation import JOBS

    jobs = [name for name in COST_AFFECTED_JOBS if name in JOBS]
    missing = [name for name in COST_AFFECTED_JOBS if name not in JOBS]
    if missing:
        # Never write a queue row for an unregistered job: nothing will ever
        # claim it, so the backlog is permanent and invisible.
        warnings.append(
            "These jobs are not registered and were skipped: "
            + ", ".join(sorted(missing))
        )
    if not jobs:
        return 0, [], window_start, window_end, warnings

    days = [
        window_start + timedelta(days=offset)
        for offset in range((window_end - window_start).days + 1)
    ]
    queued = RecomputeQueue(db).enqueue_many(
        ((job, day) for job in jobs for day in days), reason=reason
    )
    return queued, jobs, window_start, window_end, warnings


def _invalidate_cost_cache(db: Session) -> None:
    """Drop the resolver's cached answers after a write.

    Entries live 60 seconds, so this is not needed for eventual correctness — but
    the write we just made enqueued buckets, and a worker draining that queue
    inside the TTL would recompute them against the *pre-edit* rule and then
    write that answer into the rollup, where it would stay until something else
    marked the bucket dirty. A stale cache that outlives its own invalidation
    window is the one case where a 60s TTL is not self-healing.
    """
    CostRuleResolver(db).invalidate_all()


def _rule_snapshot(rule: AnalyticsCostRule) -> dict:
    """The audit payload for one rule. Decimals as strings — JSON has no Decimal
    and `float(Decimal("2.3625"))` is the start of the exact class of error this
    whole subsystem is built to avoid."""
    return {
        "id": rule.id,
        "cost_type": rule.cost_type,
        "scope": rule.scope,
        "scope_value": rule.scope_value,
        "value": str(rule.value),
        "unit": rule.unit,
        "currency": rule.currency,
        "quality": rule.quality,
        "effective_from": rule.effective_from.isoformat(),
        "effective_to": (
            rule.effective_to.isoformat() if rule.effective_to else None
        ),
        "source": rule.source,
        "note": rule.note,
    }


def _spend_snapshot(row: AnalyticsMarketingSpend) -> dict:
    return {
        "id": row.id,
        "grain": row.grain,
        "period_start": row.period_start.isoformat(),
        "period_end": row.period_end.isoformat(),
        "channel": row.channel,
        "campaign": row.campaign,
        "amount": str(row.amount),
        "currency": row.currency,
        "quality": row.quality,
        "source": row.source,
        "note": row.note,
    }


def _daily_quality(grain: str, row_quality: str) -> str:
    """The metric-quality label one allocated day should carry.

    A day derived from a monthly lump is **ALLOCATED**, always — a June invoice
    is excellent evidence about June and an inference about the 12th, and the
    label is the only thing that tells a reader which they are looking at.
    Promoting it to ACTUAL because the underlying invoice was settled would make
    a monthly figure indistinguishable from a real daily feed.

    A daily row inherits from its own quality: a settled platform figure is
    ACTUAL, anything a human asserted is ESTIMATED. Never AUTHORITATIVE — that
    grade is reserved for the internal transactional record, and ad spend lives
    in someone else's system.
    """
    if grain != SpendGrain.DAILY:
        return MetricQuality.ALLOCATED.value
    if row_quality == SpendQuality.ACTUAL:
        return MetricQuality.ACTUAL.value
    return MetricQuality.ESTIMATED.value


# ===========================================================================
# GET /admin/cost-rules
# ===========================================================================
@router.get(
    "/admin/cost-rules",
    response_model=CostRuleListResponse,
    dependencies=[_READ_AUTH, _READ_RATE_LIMIT],
)
def list_cost_rules(
    db: Session = Depends(get_db),
    cost_type: str | None = Query(default=None, max_length=48),
    scope: str | None = Query(default=None, max_length=24),
    on_date: date | None = Query(
        default=None,
        description="Only rules in force on this store-local day.",
    ),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
):
    """Every cost rule, newest window first, with the form's vocabularies.

    Ordered by `effective_from` descending so the rate currently in force is the
    first thing an admin sees; the superseded rows below it are the audit trail
    of what the rate used to be, which is the other half of what this screen is
    for.
    """
    stmt = select(AnalyticsCostRule)
    count_stmt = select(func.count(AnalyticsCostRule.id))
    for clause in (
        (AnalyticsCostRule.cost_type == cost_type) if cost_type else None,
        (AnalyticsCostRule.scope == scope) if scope else None,
    ):
        if clause is not None:
            stmt = stmt.where(clause)
            count_stmt = count_stmt.where(clause)

    if on_date is not None:
        # Same predicate the resolver uses: judged by the named day, never "now".
        in_force = (AnalyticsCostRule.effective_from <= on_date) & (
            AnalyticsCostRule.effective_to.is_(None)
            | (AnalyticsCostRule.effective_to >= on_date)
        )
        stmt = stmt.where(in_force)
        count_stmt = count_stmt.where(in_force)

    rows = (
        db.execute(
            stmt.order_by(
                AnalyticsCostRule.cost_type.asc(),
                AnalyticsCostRule.effective_from.desc(),
                AnalyticsCostRule.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    total = int(db.execute(count_stmt).scalar() or 0)

    return CostRuleListResponse(
        rules=[CostRuleRead.model_validate(r) for r in rows],
        total=total,
        cost_types=list(CostType_ALL),
        scopes=list(CostScope.PRECEDENCE),
        units=list(CostUnit_ALL),
        qualities=list(CostQuality_ALL),
    )


# ===========================================================================
# POST /admin/cost-rules
# ===========================================================================
@router.post(
    "/admin/cost-rules",
    response_model=CostRuleWriteResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_WRITE_RATE_LIMIT],
)
def create_cost_rule(
    payload: CostRuleCreate,
    request: Request,
    actor: User = Depends(require_permission(WRITE_PERMISSION)),
    db: Session = Depends(get_db),
):
    """Insert a rule covering a window no existing rule covers.

    For a rate that does not exist yet. **Changing** an existing rate is
    `POST /admin/cost-rules/{id}/supersede`; an insert whose window overlaps a
    live rule is a 409 here rather than a second contradictory row, because two
    rules covering the same day make the resolved rate depend on which one the
    query happens to reach first, and that is not reproducible.
    """
    _validate_choice(payload.unit, CostUnit_ALL, "unit")
    _validate_choice(payload.quality, CostQuality_ALL, "quality")
    _validate_choice(payload.scope, tuple(CostScope.PRECEDENCE), "scope")
    _validate_choice(payload.cost_type, CostType_ALL, "cost_type")

    _reject_overlap(
        db,
        cost_type=payload.cost_type,
        scope=payload.scope,
        scope_value=payload.scope_value,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
    )

    rule = AnalyticsCostRule(
        cost_type=payload.cost_type,
        scope=payload.scope,
        scope_value=payload.scope_value,
        value=payload.value,
        unit=payload.unit,
        currency=payload.currency,
        quality=payload.quality,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        source=payload.source,
        note=payload.note,
        created_by_user_id=actor.id,
    )
    db.add(rule)
    db.flush()

    queued, jobs, q_from, q_to, warnings = _enqueue_affected(
        db, date_from=payload.effective_from, date_to=payload.effective_to
    )

    AuditService(db).record(
        actor=actor,
        actor_ip=get_client_ip(request),
        action="analytics.cost_rule.create",
        target_type="analytics_cost_rule",
        target_id=rule.id,
        target_label=f"{rule.cost_type}/{rule.scope}:{rule.scope_value}",
        summary=(
            f"Created cost rule {rule.cost_type} = {rule.value} {rule.unit} "
            f"from {rule.effective_from.isoformat()}"
        ),
        extra={
            "old": None,
            "new": _rule_snapshot(rule),
            "recompute": {
                "queued": queued,
                "jobs": jobs,
                "from": q_from.isoformat() if q_from else None,
                "to": q_to.isoformat() if q_to else None,
            },
        },
    )
    db.commit()
    db.refresh(rule)
    _invalidate_cost_cache(db)

    return CostRuleWriteResponse(
        rule=CostRuleRead.model_validate(rule),
        superseded_rule=None,
        recompute_queued=queued,
        recompute_jobs=jobs,
        recompute_from=q_from,
        recompute_to=q_to,
        warnings=warnings,
    )


# ===========================================================================
# POST /admin/cost-rules/{rule_id}/supersede
# ===========================================================================
@router.post(
    "/admin/cost-rules/{rule_id}/supersede",
    response_model=CostRuleWriteResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_WRITE_RATE_LIMIT],
)
def supersede_cost_rule(
    rule_id: int,
    payload: CostRuleSupersedeRequest,
    request: Request,
    actor: User = Depends(require_permission(WRITE_PERMISSION)),
    db: Session = Depends(get_db),
):
    """Change a rate from `effective_from`, without touching what it used to be.

    Two writes, one transaction:

    1. the current rule's `effective_to` is set to the day **before** the new
       `effective_from` — closing it, not editing it. Its `value` is untouched,
       so a report for any date inside its window still resolves the old rate.
       That property is what makes last quarter's numbers reproducible, and the
       test suite asserts it directly rather than trusting this docstring;
    2. a new row is inserted with the new value from `effective_from`.

    Then every affected bucket is enqueued for recompute, so the change
    propagates deliberately and traceably instead of appearing the next time
    something unrelated happened to rebuild those days.

    `effective_from` must be strictly after the superseded rule's own
    `effective_from`. Equal would close the old row on the day before it started
    — a rule covering no days at all, which is an in-place edit wearing a
    version's clothes.
    """
    old = db.get(AnalyticsCostRule, rule_id)
    if old is None:
        raise NotFoundError(f"No cost rule with id {rule_id}.")

    _validate_choice(payload.unit, CostUnit_ALL, "unit")
    _validate_choice(payload.quality, CostQuality_ALL, "quality")

    if payload.effective_from <= old.effective_from:
        raise ValidationError(
            f"effective_from must be after {old.effective_from.isoformat()}, the day "
            f"rule #{rule_id} took effect. Superseding on or before that date would "
            "leave the old rule covering no days, which is an in-place edit of "
            "history rather than a new version of the rate.",
            details={"rule_effective_from": old.effective_from.isoformat()},
        )
    if old.effective_to is not None and old.effective_to < payload.effective_from:
        raise ValidationError(
            f"Rule #{rule_id} already ended on {old.effective_to.isoformat()}, before "
            f"{payload.effective_from.isoformat()}. There is nothing to supersede — "
            "create a new rule for the uncovered window instead.",
            details={"rule_effective_to": old.effective_to.isoformat()},
        )

    old_snapshot = _rule_snapshot(old)
    previous_effective_to = old.effective_to

    # Close, never rewrite. `value` is deliberately not in this statement.
    old.effective_to = payload.effective_from - timedelta(days=1)
    db.flush()

    _reject_overlap(
        db,
        cost_type=old.cost_type,
        scope=old.scope,
        scope_value=old.scope_value,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        exclude_id=old.id,
    )

    new_rule = AnalyticsCostRule(
        cost_type=old.cost_type,
        scope=old.scope,
        scope_value=old.scope_value,
        value=payload.value,
        unit=payload.unit,
        currency=payload.currency,
        quality=payload.quality,
        effective_from=payload.effective_from,
        # An open-ended supersession inherits the window the old rule had, so
        # closing a bounded rate and reopening it forever is an explicit choice
        # rather than a side effect of leaving a field blank.
        effective_to=payload.effective_to if payload.effective_to else previous_effective_to,
        source=payload.source,
        note=payload.note,
        created_by_user_id=actor.id,
    )
    db.add(new_rule)
    db.flush()

    queued, jobs, q_from, q_to, warnings = _enqueue_affected(
        db, date_from=payload.effective_from, date_to=new_rule.effective_to
    )

    AuditService(db).record(
        actor=actor,
        actor_ip=get_client_ip(request),
        action="analytics.cost_rule.supersede",
        target_type="analytics_cost_rule",
        target_id=new_rule.id,
        target_label=f"{old.cost_type}/{old.scope}:{old.scope_value}",
        summary=(
            f"Superseded cost rule #{old.id}: {old.cost_type} {old_snapshot['value']} "
            f"-> {new_rule.value} {new_rule.unit} from "
            f"{new_rule.effective_from.isoformat()}"
        ),
        extra={
            "old": old_snapshot,
            "new": _rule_snapshot(new_rule),
            "superseded_rule_id": old.id,
            "superseded_effective_to": old.effective_to.isoformat(),
            "recompute": {
                "queued": queued,
                "jobs": jobs,
                "from": q_from.isoformat() if q_from else None,
                "to": q_to.isoformat() if q_to else None,
            },
        },
    )
    db.commit()
    db.refresh(old)
    db.refresh(new_rule)
    _invalidate_cost_cache(db)

    return CostRuleWriteResponse(
        rule=CostRuleRead.model_validate(new_rule),
        superseded_rule=CostRuleRead.model_validate(old),
        recompute_queued=queued,
        recompute_jobs=jobs,
        recompute_from=q_from,
        recompute_to=q_to,
        warnings=warnings,
    )


# ===========================================================================
# GET /admin/marketing-spend
# ===========================================================================
@router.get(
    "/admin/marketing-spend",
    response_model=MarketingSpendListResponse,
    dependencies=[_READ_AUTH, _READ_RATE_LIMIT],
)
def list_marketing_spend(
    db: Session = Depends(get_db),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(
        default=None, description="Inclusive. Rows overlapping the window are returned."
    ),
    channel: str | None = Query(default=None, max_length=48),
    include_daily: bool = Query(
        default=False,
        description=(
            "Also return the per-day allocation. Monthly rows are spread across "
            "their month and every resulting day is labelled ALLOCATED."
        ),
    ),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
):
    """Entered spend, and optionally what it looks like day by day.

    The window matches rows that **overlap** it, not rows contained by it: a
    monthly row starting before `date_from` still contributes spend inside the
    window, and dropping it would under-report the month by however much of it
    the window covers.
    """
    stmt = select(AnalyticsMarketingSpend)
    count_stmt = select(func.count(AnalyticsMarketingSpend.id))
    clauses = []
    if date_from is not None:
        clauses.append(AnalyticsMarketingSpend.period_end >= date_from)
    if date_to is not None:
        clauses.append(AnalyticsMarketingSpend.period_start <= date_to)
    if channel:
        clauses.append(AnalyticsMarketingSpend.channel == channel)
    for clause in clauses:
        stmt = stmt.where(clause)
        count_stmt = count_stmt.where(clause)

    rows = (
        db.execute(
            stmt.order_by(
                AnalyticsMarketingSpend.period_start.desc(),
                AnalyticsMarketingSpend.channel.asc(),
                AnalyticsMarketingSpend.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )

    daily: list[SpendDayAllocation] = []
    daily_total = None
    if include_daily:
        for row in rows:
            for share in allocate_row_to_days(row):
                if date_from is not None and share.day < date_from:
                    continue
                if date_to is not None and share.day > date_to:
                    continue
                daily.append(
                    SpendDayAllocation(
                        day=share.day,
                        channel=row.channel,
                        campaign=row.campaign,
                        amount=share.amount,
                        currency=row.currency,
                        grain=row.grain,
                        quality=_daily_quality(row.grain, row.quality),
                        spend_id=row.id,
                    )
                )
        daily.sort(key=lambda d: (d.day, d.channel, d.campaign))
        # `start=Decimal("0")`, not 0: summing Decimals onto an int start is
        # fine today and becomes a float the first time someone "simplifies" it.
        daily_total = sum((d.amount for d in daily), start=Decimal("0"))

    return MarketingSpendListResponse(
        spend=[MarketingSpendRead.model_validate(r) for r in rows],
        total=int(db.execute(count_stmt).scalar() or 0),
        grains=list(SpendGrain.ALL),
        qualities=list(SpendQuality.ALL),
        daily=daily,
        daily_total=daily_total,
    )


# ===========================================================================
# POST /admin/marketing-spend
# ===========================================================================
@router.post(
    "/admin/marketing-spend",
    response_model=MarketingSpendWriteResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_WRITE_RATE_LIMIT],
)
def upsert_marketing_spend(
    payload: MarketingSpendUpsert,
    request: Request,
    response: Response,
    actor: User = Depends(require_permission(WRITE_PERMISSION)),
    db: Session = Depends(get_db),
):
    """Record what was spent on a channel in a day or a month.

    **Upsert, not insert.** The key is `(grain, period_start, channel, campaign,
    tz_generation)`; re-submitting June/Meta replaces the figure rather than
    adding to it, which is what makes a double-clicked form harmless and what
    will let an ads-platform sync later overwrite a human's estimate with the
    platform's own number without producing two rows that disagree about June.

    Unlike a cost rule this **does** update in place, and the difference is not
    inconsistency. A rate has a time axis: the old value was correct at the time
    and history must keep resolving it. A spend figure has none — June's total
    was always whatever it was, and an earlier row saying ₹38,000 was simply
    wrong. The change is still audited with both values and still enqueues the
    affected buckets; what it does not do is pretend the store spent two
    different amounts in June.

    201 for a new row, 200 when an existing period was replaced.
    """
    _validate_choice(payload.grain, tuple(SpendGrain.ALL), "grain")
    _validate_choice(payload.quality, tuple(SpendQuality.ALL), "quality")

    if payload.grain == SpendGrain.MONTHLY:
        period_start, period_end = month_bounds(payload.period)
    else:
        period_start = period_end = payload.period

    generation = active_generation(db).generation
    existing = (
        db.execute(
            select(AnalyticsMarketingSpend).where(
                AnalyticsMarketingSpend.grain == payload.grain,
                AnalyticsMarketingSpend.period_start == period_start,
                AnalyticsMarketingSpend.channel == payload.channel,
                AnalyticsMarketingSpend.campaign == payload.campaign,
                AnalyticsMarketingSpend.tz_generation == generation,
            )
        )
        .scalars()
        .first()
    )

    created = existing is None
    old_snapshot = None if created else _spend_snapshot(existing)
    previous_amount = None if created else existing.amount

    if created:
        row = AnalyticsMarketingSpend(
            grain=payload.grain,
            period_start=period_start,
            period_end=period_end,
            channel=payload.channel,
            campaign=payload.campaign,
            amount=payload.amount,
            currency=payload.currency,
            quality=payload.quality,
            source=payload.source,
            note=payload.note,
            entered_by_user_id=actor.id,
            tz_generation=generation,
        )
        db.add(row)
    else:
        row = existing
        row.period_end = period_end
        row.amount = payload.amount
        row.currency = payload.currency
        row.quality = payload.quality
        row.source = payload.source
        row.note = payload.note
        row.entered_by_user_id = actor.id
    db.flush()

    queued, jobs, q_from, q_to, warnings = _enqueue_affected(
        db, date_from=period_start, date_to=period_end
    )

    AuditService(db).record(
        actor=actor,
        actor_ip=get_client_ip(request),
        action="analytics.marketing_spend.create"
        if created
        else "analytics.marketing_spend.update",
        target_type="analytics_marketing_spend",
        target_id=row.id,
        target_label=f"{row.channel}/{row.campaign}",
        summary=(
            f"{'Recorded' if created else 'Revised'} {row.grain} marketing spend "
            f"{row.channel} {row.period_start.isoformat()}: "
            f"{'' if created else f'{previous_amount} -> '}{row.amount} {row.currency}"
        ),
        extra={
            "old": old_snapshot,
            "new": _spend_snapshot(row),
            "recompute": {
                "queued": queued,
                "jobs": jobs,
                "from": q_from.isoformat() if q_from else None,
                "to": q_to.isoformat() if q_to else None,
            },
        },
    )
    db.commit()
    db.refresh(row)

    if not created:
        response.status_code = status.HTTP_200_OK

    return MarketingSpendWriteResponse(
        spend=MarketingSpendRead.model_validate(row),
        created=created,
        previous_amount=previous_amount,
        recompute_queued=queued,
        recompute_jobs=jobs,
        recompute_from=q_from,
        recompute_to=q_to,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Vocabularies, materialised once at import.
#
# `CostType`/`CostUnit`/`CostQuality` are plain constant classes rather than
# Enums (adding a cost line must not need a migration), so the valid set has to
# be read off the class. Done here, at module scope, rather than per request:
# the tuples are what every write validates against and rebuilding them on each
# call is pure waste.
# ---------------------------------------------------------------------------
def _constants(cls) -> tuple[str, ...]:
    return tuple(
        sorted(
            v
            for k, v in vars(cls).items()
            if not k.startswith("_") and isinstance(v, str)
        )
    )


CostType_ALL = _constants(CostType)
CostUnit_ALL = _constants(CostUnit)
CostQuality_ALL = _constants(CostQuality)
