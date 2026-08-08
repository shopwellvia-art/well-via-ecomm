"""The transactional outbox: writing purchases down, and getting them to GA4.

Two halves that never run together, on purpose.

:func:`enqueue_purchase` runs **inside the order-paid transaction**. It writes a
row and returns; it opens no sockets, and it does not commit — it joins whatever
transaction the caller already has open, so the event and the order status
commit or roll back as one fact. An event describing a purchase that did not
happen is not merely wrong, it is unrecallable.

:func:`drain` runs in the delivery worker, minutes later and in another process
entirely. It owns the network, the retries, the backoff and the dead-lettering.
Nothing in the checkout path waits on it, which is the entire reason the outbox
exists: GA4 being slow must not make checkout slow, and GA4 being down must not
cost an order.

Why `attempts` is spent under the row lock
------------------------------------------
One row is claimed at a time with ``FOR UPDATE ... SKIP LOCKED``, and the lock
is held across the HTTP call. That is unusual and deliberate. The table has no
``claimed_by``/``lease`` columns (unlike the recompute queue) so a lock is the
only in-flight marker available, and without one a second worker would happily
pick up a row the first worker is mid-send on — sending the same purchase to GA4
twice, which inflates reported revenue and therefore ROAS. Holding the lock is
bounded by the client's own request timeout, and the batch is processed one row
per transaction so a slow send parks exactly one row rather than the batch.

Retry spacing without a `next_attempt_at` column
------------------------------------------------
There is no "retry after" column and this module may not add one, so backoff is
derived: a row with *n* failed attempts becomes eligible only once
``created_at`` is ``BACKOFF_AFTER_SECONDS[n]`` old. Since ``attempts`` only ever
increases and ``created_at`` never moves, the required age grows monotonically
and the retries space themselves out. It is durable across worker restarts,
which a process-local cooldown would not be, and it costs nothing but a
predicate. The known imprecision: after a long outage a row is instantly
eligible for *all* of its remaining attempts, so it burns them over a few ticks
rather than over an hour — which is the right behaviour anyway, since by then
the question is whether GA4 is reachable at all.
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.analytics_control import (
    AlertRuleKey,
    AlertSeverity,
    AnalyticsAlert,
    AnalyticsEventOutbox,
    ConsentState,
    OutboxEventName,
    OutboxStatus,
)
from app.services.analytics import ga4
from app.services.analytics.ga4 import (
    Ga4Config,
    Ga4ContractError,
    Ga4Error,
    load_ga4_config,
    redact,
)
from app.services.analytics.tracking_events import (
    SCHEMA_VERSION,
    Ev,
    PiiLeak,
    transaction_id_for,
)

__all__ = [
    "ALERT_METRIC",
    "ALERT_DIMENSION",
    "BACKOFF_AFTER_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "GA4_BACKDATE_LIMIT_HOURS",
    "build_purchase_payload",
    "client_id_for",
    "enqueue_purchase",
    "drain",
]

log = logging.getLogger("analytics.outbox")

#: `analytics_alerts.metric` for a conversion that will never reach GA4.
#: Constant so the Control Centre filters on a token rather than free text.
ALERT_METRIC = "event_outbox"

#: `analytics_alerts.dimension`; the *value* is the offending transaction id, so
#: an operator can go straight from the alert to the order.
ALERT_DIMENSION = "transaction_id"

#: Attempts before a row is dead-lettered. Five spread over the schedule below
#: covers roughly an hour of GA4 being unreachable, which is longer than any
#: incident that resolves itself.
DEFAULT_MAX_ATTEMPTS = 5

#: Minimum age (seconds since `created_at`) for a row with N failed attempts to
#: be retried. Index N == the current `attempts` value. See the module
#: docstring for why this is derived from `created_at` rather than stored.
BACKOFF_AFTER_SECONDS = (0, 60, 300, 900, 3600)

#: GA4 discards events timestamped more than this far in the past. Sending one
#: anyway would return 204 and record nothing; sending it *without*
#: `timestamp_micros` would be worse still, because the revenue would land on
#: today's date and quietly restate two days of reporting.
GA4_BACKDATE_LIMIT_HOURS = 72

#: Width of `analytics_event_outbox.last_error`.
ERROR_MAX_CHARS = 500

#: How many candidate rows one drain pass may look at, regardless of `limit`,
#: as a guard against a pathological loop.
_MAX_CLAIM_ITERATIONS = 500

#: The index the claim must be planned against. Discovered from the model so a
#: rename in a migration degrades to the optimiser's choice rather than an
#: error. `SKIP LOCKED` skips rows *as the scan reads them*, so a plan that
#: filesorts would lock every pending row for the duration of one HTTP send and
#: leave a second worker skipping the entire queue — the same trap documented at
#: `RecomputeQueue._lock_batch`.
_CLAIM_INDEX: str | None = next(
    (
        ix.name
        for ix in AnalyticsEventOutbox.__table__.indexes
        if [c.name for c in ix.columns] == ["status", "occurred_at"]
    ),
    None,
)

#: Consent states under which a third party may receive the event at all.
#: `UNKNOWN` is included deliberately: it means no banner answer was available,
#: not that one was refused, and treating "no signal" as a refusal would switch
#: server-side conversions off entirely for any storefront that never shipped a
#: consent banner — silently, and in exactly the deployments that most need the
#: server event. Only an explicit `DENIED` suppresses.
_SENDABLE_CONSENT = frozenset(
    {ConsentState.GRANTED, ConsentState.ANALYTICS_ONLY, ConsentState.UNKNOWN}
)

#: Consent states under which the GA cookie identifiers may be stored and used
#: to join the browser session.
_JOINABLE_CONSENT = frozenset({ConsentState.GRANTED, ConsentState.ANALYTICS_ONLY})

Sender = Callable[..., ga4.Ga4Result]


def _utcnow() -> datetime:
    """Naive UTC — what MySQL DATETIME columns actually hold."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_naive_utc(value: datetime | None) -> datetime:
    """Normalise to naive UTC so stored instants are comparable.

    An aware datetime handed to MySQL stores its wall clock and drops the
    offset, so an `Asia/Kolkata` timestamp would silently be read back as UTC
    and land 5.5 hours in the future — inside the backdating check, that reads
    as an event from the future rather than a stale one.
    """
    if value is None:
        return _utcnow()
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _money(value: Any) -> float:
    """Two-decimal float for a JSON payload.

    Money is Decimal everywhere upstream and stays Decimal everywhere that
    matters; this is the one place it has to become a JSON number, because that
    is what the Measurement Protocol accepts. Rounded at the boundary so the
    binary representation cannot drift a paisa away from the invoice.
    """
    if value is None:
        return 0.0
    return float(Decimal(str(value)).quantize(Decimal("0.01")))


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------
def build_purchase_payload(order: Any) -> dict[str, Any]:
    """The GA4 `purchase` parameters for an order, frozen as it is right now.

    Built at write time rather than at delivery time so a later edit to the
    order — a corrected address, a cancelled line, a changed product name —
    cannot retroactively change what was reported to GA4. The row is a record of
    what we said, not a view over what is currently true.

    `value` is ``orders.total_amount``: what the customer was actually charged,
    including tax and shipping and net of discounts. That choice has to match
    the browser tag exactly, or the Data Reconciliation view compares two
    different definitions of revenue and reports a permanent discrepancy that
    nobody can close. `tax` and `shipping` are sent alongside so GA4 can still
    break the total down.

    Contains no customer identity of any kind — see the denylist enforced at the
    delivery boundary.
    """
    items: list[dict[str, Any]] = []
    for index, line in enumerate(getattr(order, "items", None) or []):
        product = getattr(line, "product", None)
        # SKU, not the numeric product id: it is what a merchandiser recognises
        # in a GA4 report and it survives a database migration. The id-derived
        # fallback exists only so a line whose product row has gone missing
        # still produces a sendable event instead of failing the whole purchase.
        sku = getattr(product, "sku", None) or f"PRODUCT{line.product_id}"
        item: dict[str, Any] = {
            "item_id": str(sku)[:100],
            "item_name": str(getattr(product, "name", None) or sku)[:100],
            "price": _money(line.unit_price),
            "quantity": int(line.quantity or 0),
            "index": index,
        }
        category = getattr(getattr(product, "category", None), "name", None)
        if category:
            item["item_category"] = str(category)[:100]
        variant = getattr(product, "flavour", None)
        if variant:
            item["item_variant"] = str(variant)[:100]
        items.append(item)

    params: dict[str, Any] = {
        "transaction_id": transaction_id_for(
            getattr(order, "order_number", None), int(order.id)
        ),
        "value": _money(getattr(order, "total_amount", 0)),
        "currency": (getattr(order, "currency", None) or "INR").upper()[:3],
        "tax": _money(getattr(order, "tax_amount", 0)),
        "shipping": _money(getattr(order, "shipping_amount", 0)),
        "items": items,
        # Lets a GA4 property receiving two versions during a rollout tell them
        # apart instead of averaging two different shapes together.
        "event_schema_version": SCHEMA_VERSION,
    }
    coupon = getattr(order, "coupon_code", None)
    if coupon:
        params["coupon"] = str(coupon)[:100]
    return params


def client_id_for(transaction_id: str) -> str:
    """A stable, derived GA4 client id for an event with no cookie to join.

    Derived from the transaction id rather than random so a retry of the same
    row is the same pseudonymous "user" — a fresh id on every attempt would turn
    one retried purchase into several new users if a send ever succeeded twice.

    It joins nothing: the purchase lands in GA4 attributed to `(direct)/(none)`.
    That is a real loss and it is the documented cost of having no consented
    `_ga` cookie; the alternative — dropping the conversion — loses the revenue
    figure as well as the attribution.
    """
    digest = hashlib.sha256(f"wellvia-ga4:{transaction_id}".encode()).digest()
    left = int.from_bytes(digest[0:5], "big") % 10_000_000_000
    right = int.from_bytes(digest[5:10], "big") % 10_000_000_000
    return f"{left}.{right}"


# ---------------------------------------------------------------------------
# Write path — runs inside the caller's transaction
# ---------------------------------------------------------------------------
def enqueue_purchase(
    db: Session,
    order: Any,
    *,
    consent_state: str,
    client_id: str | None = None,
    session_id: str | None = None,
) -> AnalyticsEventOutbox:
    """Record that GA4 is owed a `purchase` for this order. Never commits.

    Call this **inside the order-paid transaction**. It joins that transaction:
    if the caller rolls back, the row goes with it, and there is no event
    describing a payment that was never taken.

    Enqueuing the same order twice is a no-op that returns the existing row, not
    an error — the UNIQUE key on ``(event_name, transaction_id)`` is what makes
    delivery exactly-once, and every caller that might fire twice (a retried
    webhook, a reconciliation sweep, an admin re-marking an order paid) is
    supposed to be able to call this without checking first.

    The INSERT is wrapped in a SAVEPOINT for a reason that matters more than the
    duplicate: an IntegrityError that reaches the caller's transaction poisons
    it, and this runs inside the transaction that is taking the customer's
    money. A duplicate analytics row must never be able to roll back an order.

    When consent is `DENIED` the row is still written, with status
    `SUPPRESSED_NO_CONSENT`. Skipping the write would be the mistake: a missing
    row is indistinguishable from a lost one, and the store's own reconciliation
    needs to know the purchase existed and was withheld deliberately.
    """
    transaction_id = transaction_id_for(
        getattr(order, "order_number", None), int(order.id)
    )
    consent = consent_state or ConsentState.UNKNOWN
    sendable = consent in _SENDABLE_CONSENT
    joinable = consent in _JOINABLE_CONSENT

    row = AnalyticsEventOutbox(
        event_name=OutboxEventName.PURCHASE,
        transaction_id=transaction_id,
        order_id=int(order.id),
        # When the purchase happened, not when this row was written: GA4's
        # backdating window is measured against this, and so is the reporting
        # day the revenue lands on.
        occurred_at=_as_naive_utc(getattr(order, "paid_at", None)),
        payload=build_purchase_payload(order),
        # The GA cookie ids are stored only with analytics consent. Without
        # consent they are not ours to keep, and an unsent row has no use for
        # them anyway.
        client_id=(client_id or None) if joinable else None,
        session_id=(session_id or None) if joinable else None,
        consent_state=consent,
        status=OutboxStatus.PENDING if sendable else OutboxStatus.SUPPRESSED_NO_CONSENT,
        attempts=0,
    )

    # Settle whatever the caller already had pending *before* opening the
    # savepoint, so rolling back to it can only ever discard our INSERT and
    # never their order.
    db.flush()

    savepoint = db.begin_nested()
    try:
        db.add(row)
        db.flush([row])
    except IntegrityError:
        savepoint.rollback()
        # A locking read: under REPEATABLE READ a plain SELECT would serve this
        # transaction's older snapshot and could report "no row" for the very
        # row whose unique key just rejected us.
        existing = db.execute(
            select(AnalyticsEventOutbox)
            .where(
                AnalyticsEventOutbox.event_name == OutboxEventName.PURCHASE,
                AnalyticsEventOutbox.transaction_id == transaction_id,
            )
            .with_for_update()
        ).scalars().first()
        if existing is None:
            # The unique key rejected the insert but no row is visible: not a
            # duplicate, so the caller must see the real error.
            raise
        log.debug(
            "outbox: purchase %s already enqueued (status=%s)",
            transaction_id,
            existing.status,
        )
        return existing

    savepoint.commit()
    log.info(
        "outbox: enqueued purchase %s order_id=%s status=%s consent=%s",
        transaction_id,
        row.order_id,
        row.status,
        consent,
    )
    return row


# ---------------------------------------------------------------------------
# Delivery path — runs in the delivery worker
# ---------------------------------------------------------------------------
def drain(
    db: Session,
    *,
    limit: int = 50,
    budget_ms: int = 20_000,
    sender: Sender | None = None,
    config: Ga4Config | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> dict[str, Any]:
    """Send what GA4 is owed, within a bounded batch and a bounded wall clock.

    Owns and commits `db` — one short transaction per row, so a slow send holds
    exactly one row lock and a crash mid-batch loses at most the row in flight.
    Do **not** pass a session with uncommitted caller work.

    A row is marked `DELIVERED` only after a send returns successfully, and
    never in advance. The outbox's entire value is that a `pending` row is a
    debt GA4 is still owed; marking rows optimistically would discharge that
    debt without paying it and leave a queue that looks healthy while
    conversions quietly vanish.

    `SUPPRESSED_NO_CONSENT` rows are never selected, so they are never sent and
    can never be marked delivered. They are also never counted against the batch
    limit — if they were, a backlog of withheld purchases could starve the
    pending ones behind them forever.

    Both bounds exist so a GA4 outage cannot wedge the worker: `limit` caps the
    rows, `budget_ms` caps the wall clock, and whichever binds first ends the
    pass with the rest still `pending` for the next tick.

    Returns ``{sent, suppressed, failed, skipped, remaining, ...}`` where:

    * **sent** — rows delivered on this pass.
    * **suppressed** — purchases currently withheld for lack of consent. A
      gauge over the table, not a delta: these rows are never processed, so a
      per-pass count of them would always be zero and would tell an operator
      nothing about how much is being held back.
    * **failed** — attempts that failed on this pass, retryable and
      dead-lettered alike.
    * **skipped** — pending rows this pass did not attempt: waiting out their
      backoff, or left over when the batch limit or time budget ran out.
    * **remaining** — pending rows still owed to GA4 after this pass.
    """
    started = time.monotonic()
    deadline = started + max(budget_ms, 0) / 1000.0

    cfg = config if config is not None else load_ga4_config(db)
    # `load_ga4_config` may have opened a read transaction; end it so the claim
    # below can set its own isolation level.
    db.rollback()

    if not cfg.configured:
        # Nothing is touched. In particular nothing is marked delivered — an
        # unconfigured GA4 is a reason the debt cannot be paid, not a reason to
        # write it off.
        return _idle_result(db, cfg.reason)

    if cfg.debug_endpoint:
        # `/debug/mp/collect` validates a payload and then discards it. Marking
        # a row delivered off that response would be the worst outcome this
        # module can produce: the conversion is destroyed, the queue drains
        # cleanly, and nothing anywhere records that anything was lost. So the
        # drain refuses to run while validation mode is on. Rows keep piling up
        # as `pending`, which is recoverable, and the reason is in every tick's
        # log line.
        return _idle_result(db, "debug_endpoint_enabled")

    send: Sender = sender or ga4.send_events
    seen: set[int] = set()
    sent = 0
    failed = 0
    retryable_failures = 0
    budget_exhausted = False

    for _ in range(_MAX_CLAIM_ITERATIONS):
        if len(seen) >= limit:
            break
        if time.monotonic() >= deadline:
            budget_exhausted = True
            break

        row = _claim_one(db, exclude=seen, max_attempts=max_attempts)
        if row is None:
            break
        seen.add(int(row.id))

        outcome = _deliver_row(db, row, cfg=cfg, send=send, max_attempts=max_attempts)
        db.commit()

        if outcome == "sent":
            sent += 1
        else:
            failed += 1
            if outcome == "retry":
                retryable_failures += 1

    remaining = _count(db, OutboxStatus.PENDING)
    result = {
        "sent": sent,
        "suppressed": _count(db, OutboxStatus.SUPPRESSED_NO_CONSENT),
        "failed": failed,
        # Everything still pending that this pass never touched. Rows we
        # attempted and that failed retryably are still pending, so they are
        # subtracted back out — they were not skipped, they were tried.
        "skipped": max(0, remaining - retryable_failures),
        "remaining": remaining,
        "attempted": len(seen),
        "configured": True,
        "reason": None,
        "budget_exhausted": budget_exhausted,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
    log.info("outbox drain: %s", result)
    return result


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _idle_result(db: Session, reason: str | None) -> dict[str, Any]:
    """A pass that deliberately did nothing, and says why.

    Shaped identically to a real pass so a caller never has to branch on which
    kind of result it got, and reports the backlog it declined to touch rather
    than a comfortable row of zeroes.
    """
    remaining = _count(db, OutboxStatus.PENDING)
    log.info("outbox drain: not sending (%s); %d row(s) waiting", reason, remaining)
    return {
        "sent": 0,
        "suppressed": _count(db, OutboxStatus.SUPPRESSED_NO_CONSENT),
        "failed": 0,
        "skipped": remaining,
        "remaining": remaining,
        "attempted": 0,
        "configured": False,
        "reason": reason,
        "budget_exhausted": False,
    }


def _count(db: Session, status: str) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(AnalyticsEventOutbox)
            .where(AnalyticsEventOutbox.status == status)
        ).scalar_one()
        or 0
    )


def _use_read_committed(db: Session) -> None:
    """Claim without gap locks.

    `SKIP LOCKED` fixes the SELECT half of a claim and does nothing for the
    UPDATE that follows: under MySQL's default REPEATABLE READ a locking read
    over an index range also takes the gaps around the records, and marking a
    row delivered rewrites its entry in that same `(status, occurred_at)` index.
    Two workers then deadlock on gaps neither of them selected. Set per
    transaction; SQLAlchemy restores the level when the connection is returned.
    """
    if db.in_transaction():
        return
    db.connection(execution_options={"isolation_level": "READ COMMITTED"})


def _backoff_predicate(now: datetime):
    """Rows whose next attempt is due. See the module docstring."""
    clauses = [
        and_(
            AnalyticsEventOutbox.attempts == attempts,
            AnalyticsEventOutbox.created_at <= now - timedelta(seconds=delay),
        )
        for attempts, delay in enumerate(BACKOFF_AFTER_SECONDS)
    ]
    # Anything beyond the table's last entry waits out the longest delay rather
    # than falling through the OR and never being claimed again.
    clauses.append(
        and_(
            AnalyticsEventOutbox.attempts >= len(BACKOFF_AFTER_SECONDS),
            AnalyticsEventOutbox.created_at
            <= now - timedelta(seconds=BACKOFF_AFTER_SECONDS[-1]),
        )
    )
    return or_(*clauses)


def _claim_one(
    db: Session, *, exclude: Iterable[int], max_attempts: int
) -> AnalyticsEventOutbox | None:
    """Lock the oldest due `PENDING` row, or None. Leaves the lock held.

    The caller must commit (or roll back) before claiming again — the lock is
    the only in-flight marker this table has, and it is what stops a second
    worker sending the same purchase while this one is mid-request.
    """
    _use_read_committed(db)
    now = _utcnow()

    query = select(AnalyticsEventOutbox).where(
        AnalyticsEventOutbox.status == OutboxStatus.PENDING,
        AnalyticsEventOutbox.attempts < max_attempts,
        _backoff_predicate(now),
    )
    excluded = [int(i) for i in exclude]
    if excluded:
        # Rows already handled in this pass. A retryable failure leaves the row
        # `pending`, and without this the next claim would pick the same row
        # again and spin on it for the whole budget.
        query = query.where(AnalyticsEventOutbox.id.notin_(excluded))
    if _CLAIM_INDEX:
        query = query.with_hint(
            AnalyticsEventOutbox, f"FORCE INDEX ({_CLAIM_INDEX})", "mysql"
        )
    query = (
        query.order_by(AnalyticsEventOutbox.occurred_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )

    row = db.execute(query).scalars().first()
    if row is None:
        # The SELECT opened a transaction; end it rather than leaving the
        # connection idle-in-transaction holding a snapshot.
        db.commit()
    return row


def _deliver_row(
    db: Session,
    row: AnalyticsEventOutbox,
    *,
    cfg: Ga4Config,
    send: Sender,
    max_attempts: int,
) -> str:
    """One send attempt against a locked row. Mutates it; does not commit.

    Returns ``"sent"``, ``"retry"`` or ``"dead"``.
    """
    now = _utcnow()
    age_hours = (now - _as_naive_utc(row.occurred_at)).total_seconds() / 3600.0
    if age_hours > GA4_BACKDATE_LIMIT_HOURS:
        # Past GA4's backdating window. Sending it would earn a 204 and record
        # nothing; sending it undated would put the revenue on today. Neither is
        # delivery, so this is dead-lettered where a human can see it.
        return _fail(
            db,
            row,
            error=(
                f"occurred {age_hours:.1f}h ago, past GA4's "
                f"{GA4_BACKDATE_LIMIT_HOURS}h backdating window — not sent"
            ),
            permanent=True,
            max_attempts=max_attempts,
        )

    params = dict(row.payload or {})
    if row.session_id:
        # Both are required together for GA4 to attach the event to the browser
        # session; session_id alone is ignored.
        params.setdefault("session_id", row.session_id)
        params.setdefault("engagement_time_msec", 1)

    consent_state = row.consent_state or ConsentState.UNKNOWN
    ads_granted = consent_state == ConsentState.GRANTED
    consent = {
        "ad_user_data": "GRANTED" if ads_granted else "DENIED",
        "ad_personalization": "GRANTED" if ads_granted else "DENIED",
    }

    try:
        send(
            cfg.measurement_id,
            cfg.api_secret,
            row.client_id or client_id_for(row.transaction_id),
            [{"name": row.event_name or Ev.PURCHASE, "params": params}],
            # Never the validation endpoint: it returns 200 without delivering
            # anything, and this function's success branch writes `delivered`.
            # `drain` refuses to start at all when validation mode is on.
            debug=False,
            timestamp_micros=int(_as_naive_utc(row.occurred_at).replace(
                tzinfo=timezone.utc
            ).timestamp() * 1_000_000),
            non_personalized_ads=not ads_granted,
            consent=consent,
        )
    except (PiiLeak, Ga4ContractError) as exc:
        # A payload that carries PII, or an event GA4 cannot accept, is wrong in
        # a way no retry improves. Failing it immediately spends one attempt
        # instead of five and gets the alert in front of someone today.
        return _fail(
            db,
            row,
            error=redact(f"{type(exc).__name__}: {exc}", cfg.api_secret),
            permanent=True,
            max_attempts=max_attempts,
        )
    except Ga4Error as exc:
        return _fail(
            db,
            row,
            error=redact(f"{type(exc).__name__}: {exc}", cfg.api_secret),
            permanent=False,
            max_attempts=max_attempts,
        )
    except Exception as exc:  # noqa: BLE001 - one bad row must not end the drain
        return _fail(
            db,
            row,
            error=redact(f"{type(exc).__name__}: {exc}", cfg.api_secret),
            permanent=False,
            max_attempts=max_attempts,
        )

    row.attempts = int(row.attempts or 0) + 1
    row.status = OutboxStatus.DELIVERED
    row.delivered_at = now
    row.last_error = None
    return "sent"


def _fail(
    db: Session,
    row: AnalyticsEventOutbox,
    *,
    error: str,
    permanent: bool,
    max_attempts: int,
) -> str:
    """Record a failed attempt: retry, or dead-letter with an alert."""
    row.attempts = int(row.attempts or 0) + 1
    row.last_error = (error or "")[:ERROR_MAX_CHARS]

    if permanent or row.attempts >= max_attempts:
        row.status = OutboxStatus.FAILED
        db.add(_dead_letter_alert(row, at=_utcnow()))
        log.error(
            "outbox: giving up on %s after %d attempt(s): %s",
            row.transaction_id,
            row.attempts,
            row.last_error,
        )
        return "dead"

    # Stays `pending`. `attempts` and the row's fixed `created_at` are together
    # what schedules the next try.
    log.warning(
        "outbox: attempt %d/%d failed for %s: %s",
        row.attempts,
        max_attempts,
        row.transaction_id,
        row.last_error,
    )
    return "retry"


def _dead_letter_alert(row: AnalyticsEventOutbox, *, at: datetime) -> AnalyticsAlert:
    """The Control Centre row for a conversion GA4 will never receive.

    `GA4_SYNC_FAILURE` is the rule this exact condition is named by. WARNING
    rather than CRITICAL: nothing about the store is broken and no money is at
    risk — the order is intact and internal reporting is complete — but GA4's
    revenue is now short by this purchase, so campaign ROAS is understated until
    someone looks. Without the alert the only symptom is a number in a
    third-party console that is quietly a little too low, which nobody ever
    investigates.
    """
    return AnalyticsAlert(
        rule_key=AlertRuleKey.GA4_SYNC_FAILURE,
        severity=AlertSeverity.WARNING,
        metric=ALERT_METRIC,
        dimension=ALERT_DIMENSION,
        dimension_value=str(row.transaction_id)[:64],
        detected_at=at,
        context={
            "event_name": row.event_name,
            "order_id": row.order_id,
            "attempts": int(row.attempts or 0),
            "consent_state": row.consent_state,
            "last_error": row.last_error,
        },
    )
