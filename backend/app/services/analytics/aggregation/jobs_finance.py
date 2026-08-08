"""The commercial rollups: payments, geography, promotions and cohorts.

Registered here: ``payment_daily`` (``agg_payment_daily``), ``shipment_geo_daily``
(``agg_geo_daily``), ``promo_daily`` (``agg_promo_daily``) and ``cohort_monthly``
(``agg_customer_cohort_monthly``) — the last four of the twelve rollups. The two
write patterns and their guard rails are documented at length in
:mod:`app.services.analytics.aggregation.jobs`, whose helpers are imported rather
than restated: one copy of "COALESCE the dimension, overwrite never accumulate,
convert money once at the write" is the only way those rules survive twelve jobs.

Every job here is Pattern B, and that is not a coincidence
==========================================================
None of these four tables has a key set that is known before the data is read.
Which gateways processed money, which pincodes ordered, which coupon codes were
redeemed and which acquisition cohorts exist are all *discovered*, and every one
of them can **lose** a key between two runs of the same bucket: the day's only
UPI attempt is deleted, the day's only order from 560001 is cancelled out of the
revenue statuses, a coupon usage is backed out when the order is refunded, a
cohort's only customer has their first order removed. ``INSERT ... ON DUPLICATE
KEY UPDATE`` has no statement to make about a key that disappeared — it writes
nothing for it, and yesterday's row survives at yesterday's numbers, in the exact
tables an operator uses to decide which gateway to drop and which pincode to stop
shipping to. So all four DELETE their scope and reinsert, inside the runner's
single transaction.

Every dimension is COALESCEd to the ``'-'`` sentinel before it is written.
``gateway``, ``payment_method``, ``payment_instrument``, ``state``, ``pincode``
and ``coupon_code`` are all nullable at the source and all sit in a UNIQUE key,
where MySQL permits unlimited NULLs — one NULL gateway and the next run inserts a
second row for the same gateway instead of replacing it, silently, forever.

Recognition: one rule, the corrected one
========================================
Every revenue-shaped figure in this module uses ``margin._recognised_sale()``,
via ``jobs._revenue_window``, and never a locally restated status set. The reason
is in ``jobs.py``: a refunded order excluded from revenue *by its status* and
then subtracted *again* as a refund reports the sale twice reversed, and an order
placed and refunded inside one window went NEGATIVE while the revenue bridge kept
balancing, because both sides of the identity shared the wrong input.

``_REVENUE_STATUSES`` is therefore imported nowhere in this module. It is the
LEGACY rule and its one legitimate remaining use — the shadow-mode parity anchor
``agg_order_daily.paid_order_value`` — lives in ``jobs.py``. Nothing here is that
anchor, so nothing here may quietly reintroduce the definition it corrects. Note
that ``analytics_rollups``'s module docstring still describes every ``revenue``
column as computed over that status set; it predates the correction, and the
per-job docstrings below are the authority for what these four tables hold.

The one consequence worth naming up front: ``cohort_monthly`` assigns a customer
to the month of their first *recognised* sale, while
``agg_customer_snapshot.cohort_month`` (``jobs_customer.py``) still assigns on
the legacy set. The two agree for every customer whose first order was not later
refunded — which is the join ``AggCustomerSnapshot.cohort_month`` documents — and
where they differ, this table is the corrected one: the acquisition happened, and
the reversal is booked in the period of its own ``refunded_at``.

Two numbers this module refuses to compute
==========================================
``agg_geo_daily.net_revenue`` is GROSS OF REFUNDS, and says so
--------------------------------------------------------------
This is a *creation-cohort* table: it holds the orders placed in the bucket from
each geography, and their fulfilment outcomes as of the moment the bucket was
computed. A refund's only honest timestamp is when it was issued, and there is no
way to subtract a July refund from the March pincode bucket that produced the
order without either (a) moving the reversal to a day it did not happen or (b)
producing pincode-days with negative revenue and no orders. ``agg_order_hourly``
made the same call for the same reason. ``agg_order_daily`` remains the
reconciled revenue record; this table is for "where does demand come from and
where does it come back".

``agg_promo_daily`` holds no ROI, uplift or incrementality
-----------------------------------------------------------
The model docstring rules them out and this job does not smuggle them back in as
a derived column. What it stores is the pair that actually tells the story —
``discount_amount`` given away against ``order_revenue`` brought in — plus
``redemptions`` and ``orders`` kept **separate** rather than reconciled, because
the gap between "a code was redeemed" and "an order carrying that code was
recognised" is the interesting number, not an error to be averaged away.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.integrations.payments.base import PaymentStatus
from app.models.analytics_base import DIMENSION_UNKNOWN
from app.models.analytics_rollups import (
    AggCustomerCohortMonthly,
    AggGeoDaily,
    AggPaymentDaily,
    AggPromoDaily,
)
from app.models.coupon import Coupon, CouponUsage
from app.models.order import Order, OrderItem
from app.models.order_payment import OrderPayment, PaymentTxnStatus
from app.models.payment_event import PaymentEvent, PaymentEventType
from app.models.return_request import ReturnRequest
from app.models.shipment import Shipment, ShipmentStatus
from app.services.analytics.aggregation.base import JobRunResult, register

# The write patterns and their guard rails live in `jobs`, imported rather than
# re-implemented. `_dim` is what makes "a NULL dimension can never reach a UNIQUE
# key" a property of one function instead of a rule twelve jobs must remember.
from app.services.analytics.aggregation.jobs import (
    _as_utc,
    _bucket_window,
    _cap,
    _dim,
    _revenue_window,
    _utcnow,
)
from app.services.analytics.contracts import from_minor, to_minor
from app.services.analytics.timebox import day_bounds_utc, local_day, store_timezone

__all__ = [
    "PaymentDailyJob",
    "ShipmentGeoDailyJob",
    "PromoDailyJob",
    "CohortMonthlyJob",
    "COHORT_MONTHS_RECOMPUTED",
    "PINCODE_DIGITS",
]

#: How many acquisition cohorts each ``cohort_monthly`` run rebuilds from
#: scratch, counting back from the bucket's own month and including it. Straight
#: from the `AggCustomerCohortMonthly` docstring, which is the authority; the
#: constant exists so the number appears exactly once in code. Cohorts older than
#: this are frozen — if they ever need correcting, widen this rather than
#: patching rows, because a hand-patched cohort is indistinguishable from a
#: computed one on the next read.
COHORT_MONTHS_RECOMPUTED = 24

#: Length of an Indian PIN. Named because `agg_geo_daily.pincode` is a
#: `dimension_column(10)` and the width of the COLUMN is not the width of a valid
#: value — a 7-digit string fits the column and is not a pincode.
PINCODE_DIGITS = 6

#: Separators a human or an address form puts inside a pincode. Removed before
#: validation, so "560 001" and "560-001" are the same real pincode rather than
#: two different rows on the map.
_PINCODE_SEPARATORS = re.compile(r"[\s -]+")

#: A conforming Indian PIN: six digits, never leading zero (India Post allocates
#: the first digit from 1-8; 0 is not a postal region). Matched with `fullmatch`,
#: so a longer string is REJECTED rather than truncated — truncating "5600012"
#: to "560001" would invent a Bengaluru order out of a data-entry mistake, and
#: nothing downstream could ever tell.
_PINCODE_PATTERN = re.compile(rf"[1-9][0-9]{{{PINCODE_DIGITS - 1}}}")

#: `order_payments.payment_status` values that mean the attempt reversed money
#: after it had already taken it. PARTIALLY_REFUNDED is counted here with
#: REFUNDED on purpose: a partly reversed attempt really was refunded, in part,
#: and counting only full reversals makes the refund count fall as partial
#: refunds become the norm — the wrong direction, and the kind of error that gets
#: celebrated. `refunded` is a count of attempts, never an amount.
_REFUNDED_TXN_STATUSES: tuple[PaymentTxnStatus, ...] = (
    PaymentTxnStatus.REFUNDED,
    PaymentTxnStatus.PARTIALLY_REFUNDED,
)

#: Event types that record OUR RECORD DISAGREEING WITH THE GATEWAY'S, which is
#: what `agg_payment_daily.mismatch_events` counts:
#:
#: * AMOUNT_MISMATCH      — the gateway settled a different amount than we expect;
#: * SETTLED_AFTER_CANCEL — the gateway captured money for an order we cancelled;
#: * WEBHOOK_UNCONFIRMED  — the payload claimed one status and the gateway's own
#:                          status call reported another.
#:
#: WEBHOOK_SIGNATURE_INVALID and GATEWAY_ERROR are deliberately NOT here. A bad
#: signature is an authentication failure (quite possibly an attacker, not the
#: gateway at all) and a gateway error is a transport failure; neither is a
#: disagreement about a payment we both have a record of, and folding them in
#: would turn an operational alarm into a noise counter nobody acts on.
_MISMATCH_EVENT_TYPES: tuple[str, ...] = (
    PaymentEventType.AMOUNT_MISMATCH,
    PaymentEventType.SETTLED_AFTER_CANCEL,
    PaymentEventType.WEBHOOK_UNCONFIRMED,
)

#: Event types whose `message` is a failure reason even when the event carries no
#: resolved `payment_status`. Everything with `payment_status == 'failed'` also
#: qualifies; see `PaymentDailyJob._failure_reasons`.
_FAILURE_EVENT_TYPES: tuple[str, ...] = (PaymentEventType.GATEWAY_ERROR,)

#: Width of `agg_payment_daily.top_failure_reason`. The column is a label, so a
#: long gateway string is truncated rather than dropped — but truncation happens
#: exactly once, here, and never silently inside the driver.
_FAILURE_REASON_CHARS = 120

#: Shipment statuses that mean the parcel came back. RTO_DELIVERED is counted as
#: an RTO alongside RTO_INITIATED for the same reason `shipment_daily` does it: a
#: returned parcel was necessarily initiated, and counting only the first status
#: makes the RTO rate FALL as returns complete.
_RTO_STATUSES: tuple[ShipmentStatus, ...] = (
    ShipmentStatus.RTO_INITIATED,
    ShipmentStatus.RTO_DELIVERED,
)


# ===========================================================================
# Shared helpers
# ===========================================================================
def _pincode(value: str | None) -> str:
    """Normalise a pincode to six digits, or to the ``'-'`` sentinel.

    Whitespace and hyphens are removed first, so "560 001" and "560-001" are the
    one real pincode they obviously are. What survives must then be exactly
    :data:`PINCODE_DIGITS` digits with a non-zero lead, or the value becomes
    ``'-'``.

    **Never truncated.** ``"5600012"`` is not a pincode with a typo that can be
    cut back to ``"560001"``; it is an unusable value, and shortening it would
    invent an order from a real Bengaluru pincode that nobody placed — a fiction
    that renders on the geo map indistinguishable from a genuine one. ``'-'`` is
    a real, meaningful value in this table ("pincode unknown", and also
    "aggregated to state grain" after the 180-day retention rollup), so an
    unusable pincode has somewhere honest to go.
    """
    text = _PINCODE_SEPARATORS.sub("", (value or "").strip())
    if _PINCODE_PATTERN.fullmatch(text):
        return text
    return DIMENSION_UNKNOWN


def _snapshot_field(snapshot: Any, field: str) -> str | None:
    """Read one field out of ``orders.shipping_address_snapshot``.

    The column is JSON and nullable, and legacy orders predating the structured
    snapshot carry only the free-text ``shipping_address``. A missing snapshot is
    a missing dimension, not an empty string — the caller turns it into ``'-'``.
    """
    if not isinstance(snapshot, Mapping):
        return None
    value = snapshot.get(field)
    return value if isinstance(value, str) else None


def _duration_seconds(
    started_at: datetime | None, ended_at: datetime | None
) -> int | None:
    """Whole seconds between two instants, or ``None`` if it cannot be measured.

    ``None`` for a missing endpoint (the leg did not happen, and must not be
    counted in the pair's denominator) and ``None`` for a negative duration (the
    timestamps contradict each other). A negative summand silently pulls an
    average down and there is no way to notice it afterwards, so it is refused
    here and named by the caller in a warning.
    """
    if started_at is None or ended_at is None:
        return None
    seconds = int((_as_utc(ended_at) - _as_utc(started_at)).total_seconds())
    return seconds if seconds >= 0 else None


def _replace_bucket(
    db: Session,
    model: type,
    rows: Sequence[dict[str, Any]],
    *,
    where: Sequence[Any],
) -> int:
    """Pattern B: DELETE the scope, then INSERT the freshly computed rows.

    The DELETE is **unconditional** — including when ``rows`` is empty, which is
    exactly the case an upsert cannot express: a bucket that used to have a
    gateway, a pincode or a coupon and now has none. Both statements run inside
    the runner's single transaction, so a reader never sees the gap.

    Returns the number of rows deleted; the caller reports what it wrote.
    """
    deleted = int(db.execute(delete(model).where(*where)).rowcount or 0)
    if rows:
        db.execute(mysql_insert(model).values(list(rows)))
    return deleted


def _month_ordinal(day: date) -> int:
    """Months since year 0, so month arithmetic is integer arithmetic.

    ``period_index`` is a difference of two of these. Doing it with ``timedelta``
    instead would be wrong by a day every February and by a whole period on any
    31st, and the cohort grid would quietly shear.
    """
    return day.year * 12 + (day.month - 1)


def _month_start(ordinal: int) -> date:
    """First calendar day of the month an ordinal names."""
    return date(ordinal // 12, ordinal % 12 + 1, 1)


def _month_key(ordinal: int) -> str:
    """``'YYYY-MM'`` — the stored ``cohort_month``, matching the snapshot table."""
    start = _month_start(ordinal)
    return f"{start.year:04d}-{start.month:02d}"


# ===========================================================================
# payment_daily -> agg_payment_daily      (Pattern B: delete and reinsert)
# ===========================================================================
class PaymentDailyJob:
    """Payment outcomes per day x gateway x method x instrument.

    Pattern B. Which gateways and instruments carried money on a day is
    discovered, and the last UPI attempt of a day can be deleted; an upsert would
    leave UPI's row behind at its old numbers in the table an operator uses to
    decide which gateway is failing.

    All three dimensions are COALESCEd to ``'-'``: ``order_payments.gateway`` is
    NULL for a COD leg that never touches a gateway,
    ``order_payments.payment_method`` and ``orders.payment_instrument`` are
    nullable, and all three sit in the UNIQUE key where a NULL does not collide.

    **This is a CREATION COHORT of ATTEMPTS.** ``attempts`` counts
    ``order_payments`` rows created in the bucket, and ``paid`` / ``failed`` /
    ``cancelled`` / ``refunded`` are the state *those* attempts are in as of the
    moment the bucket was computed. That is what makes ``paid / attempts`` a real
    success rate: numerator and denominator describe the same population. The
    consequence is that a bucket keeps moving after its day ends — an attempt
    initiated Monday and captured Tuesday increments Monday's ``paid`` when
    Monday is recomputed — which is precisely what the recompute queue is for.
    Dating captures by the day they happened would produce a rate whose two
    halves are different populations and would read as a perfectly plausible
    number.

    The four outcome counters are **current statuses** and therefore mutually
    exclusive, so ``paid + failed + cancelled + refunded <= attempts``; the
    remainder is still in flight (PENDING / INITIATED / AUTHORIZED). One
    deliberate widening: ``refunded`` counts ``PARTIALLY_REFUNDED`` too — see
    :data:`_REFUNDED_TXN_STATUSES`.

    ``attempts``, not orders
    ------------------------
    One order can make several attempts (a retry after a decline, the prepaid and
    COD legs of a split-COD order), so this table's counts are **not** order
    counts and must never be labelled as such. ``registry.py`` deliberately does
    not bind ``orders_count`` to this source for exactly that reason.

    Settlement time follows the sum+count rule
    ------------------------------------------
    ``sum_settle_seconds`` accumulates ``created_at -> paid_at`` — the attempt row
    is written when we hand off to the gateway, so its ``created_at`` *is* the
    initiation instant and is the only one this schema records. ``n_settle`` is
    its own denominator and counts only attempts that are currently ``PAID`` and
    carry a ``paid_at``: a COD leg marked paid on delivery has no ``paid_at``, so
    dividing the sum by ``paid`` instead would understate settlement time by
    however many of those there were. No average is ever stored.

    ``mismatch_events`` is an alarm, and its dimensioning is best-effort
    -------------------------------------------------------------------
    ``payment_events`` is an append-only forensic log with no payment-attempt id
    on it — only ``order_id`` and ``gateway_code``. A mismatch is therefore keyed
    by the event's own gateway plus the *order's* ``payment_method`` and
    ``payment_instrument``, which agree with the attempt's own values in practice
    because ``order_payments.payment_method`` is documented as kept in step with
    ``orders.payment_method``. An event that never matched an order (an unmatched
    webhook) lands under the ``'-'`` sentinels with its gateway intact, which is
    the honest place for it: something disagreed, on that gateway, and we do not
    know whose order it was. Such a key can exist with ``attempts = 0``, and that
    row is a real measurement, not an artefact.

    ``top_failure_reason`` is a LABEL
    ----------------------------------
    The single most frequent failure ``message`` in the bucket for that key, ties
    broken alphabetically so it cannot flap between two equally common strings
    from one run to the next. It is nullable, it is not in the key, and it must
    never be counted — the full distribution lives in ``payment_events``. NULL
    means "no failure event in this bucket carried a message", which is a
    different statement from "nothing failed" and is why ``failed`` is stored
    next to it.
    """

    name = "payment_daily"

    def run(self, db: Session, bucket_date: date, tz_generation: int) -> JobRunResult:
        start, end, _tz = _bucket_window(db, bucket_date)
        warnings: list[str] = []

        buckets: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
            _empty_payment_bucket
        )
        self._attempts(db, start, end, buckets, warnings)
        self._mismatches(db, start, end, buckets)
        reasons = self._failure_reasons(db, start, end)
        for key in reasons:
            buckets[key]  # touch: a key known only from a failure event is real

        computed_at = _utcnow()
        rows = [
            {
                "bucket_date": bucket_date,
                "gateway": key[0],
                "payment_method": key[1],
                "payment_instrument": key[2],
                "tz_generation": tz_generation,
                "computed_at": computed_at,
                "attempts": bucket["attempts"],
                "paid": bucket["paid"],
                "failed": bucket["failed"],
                "cancelled": bucket["cancelled"],
                "refunded": bucket["refunded"],
                "paid_amount": from_minor(bucket["paid_minor"]),
                "failed_amount": from_minor(bucket["failed_minor"]),
                "sum_settle_seconds": bucket["sum_settle"],
                "n_settle": bucket["n_settle"],
                "mismatch_events": bucket["mismatch_events"],
                "top_failure_reason": reasons.get(key),
            }
            for key, bucket in sorted(buckets.items())
        ]

        deleted = _replace_bucket(
            db,
            AggPaymentDaily,
            rows,
            where=(
                AggPaymentDaily.bucket_date == bucket_date,
                AggPaymentDaily.tz_generation == tz_generation,
            ),
        )
        return JobRunResult(
            rows_written=len(rows), rows_deleted=deleted, warnings=_cap(warnings)
        )

    # -- pieces -----------------------------------------------------------
    @staticmethod
    def _attempts(
        db: Session,
        start: datetime,
        end: datetime,
        buckets: dict[tuple[str, str, str], dict[str, Any]],
        warnings: list[str],
    ) -> None:
        """Every payment attempt created in the bucket, counted by its state.

        LEFT JOIN to ``orders`` for the instrument. The FK is ``ON DELETE
        CASCADE`` so an orphaned attempt should be impossible, but an inner join
        would silently drop the money if one ever existed, and a payment that
        vanishes from the attempt count is the one failure mode this table cannot
        afford.
        """
        rows = db.execute(
            select(
                OrderPayment.id.label("payment_id"),
                OrderPayment.gateway.label("gateway"),
                OrderPayment.payment_method.label("method"),
                OrderPayment.payment_status.label("status"),
                OrderPayment.amount.label("amount"),
                OrderPayment.created_at.label("created_at"),
                OrderPayment.paid_at.label("paid_at"),
                Order.payment_instrument.label("instrument"),
            )
            .select_from(OrderPayment)
            .outerjoin(Order, Order.id == OrderPayment.order_id)
            .where(OrderPayment.created_at >= start, OrderPayment.created_at < end)
        ).all()

        for row in rows:
            bucket = buckets[
                (
                    _dim(row.gateway)[:40],
                    _dim(row.method)[:32],
                    _dim(row.instrument)[:32],
                )
            ]
            bucket["attempts"] += 1

            status = row.status
            if status == PaymentTxnStatus.PAID:
                bucket["paid"] += 1
                bucket["paid_minor"] += to_minor(row.amount)
            elif status == PaymentTxnStatus.FAILED:
                bucket["failed"] += 1
                # The size of the leak, not lost revenue: a customer who retried
                # and succeeded is counted here AND in the successful attempt.
                bucket["failed_minor"] += to_minor(row.amount)
            elif status == PaymentTxnStatus.CANCELLED:
                bucket["cancelled"] += 1
            elif status in _REFUNDED_TXN_STATUSES:
                bucket["refunded"] += 1

            if status != PaymentTxnStatus.PAID or row.paid_at is None:
                continue
            seconds = _duration_seconds(row.created_at, row.paid_at)
            if seconds is None:
                warnings.append(
                    f"payment_negative_settle: attempt {int(row.payment_id)} was "
                    "paid before it was created; its timestamps contradict each "
                    "other and the leg was excluded from sum_settle_seconds"
                )
                continue
            bucket["sum_settle"] += seconds
            bucket["n_settle"] += 1

    @staticmethod
    def _mismatches(
        db: Session,
        start: datetime,
        end: datetime,
        buckets: dict[tuple[str, str, str], dict[str, Any]],
    ) -> None:
        """Reconciliation disagreements in the bucket, keyed as well as they can be.

        See :data:`_MISMATCH_EVENT_TYPES` for what counts and what deliberately
        does not, and the class docstring for why the method and instrument come
        from the order rather than from the attempt.
        """
        rows = db.execute(
            select(
                PaymentEvent.gateway_code.label("gateway"),
                Order.payment_method.label("method"),
                Order.payment_instrument.label("instrument"),
                func.count(PaymentEvent.id).label("events"),
            )
            .select_from(PaymentEvent)
            .outerjoin(Order, Order.id == PaymentEvent.order_id)
            .where(
                PaymentEvent.event_type.in_(_MISMATCH_EVENT_TYPES),
                PaymentEvent.created_at >= start,
                PaymentEvent.created_at < end,
            )
            .group_by(
                PaymentEvent.gateway_code,
                Order.payment_method,
                Order.payment_instrument,
            )
        ).all()
        for row in rows:
            key = (
                _dim(row.gateway)[:40],
                _dim(row.method)[:32],
                _dim(row.instrument)[:32],
            )
            buckets[key]["mismatch_events"] += int(row.events or 0)

    @staticmethod
    def _failure_reasons(
        db: Session, start: datetime, end: datetime
    ) -> dict[tuple[str, str, str], str]:
        """The modal failure ``message`` per key. A label, never a count.

        An event qualifies when it resolved to a failed payment status, or when
        it is a gateway error (which carries the provider's own error string and
        often has no resolved status at all). Ties are broken by the message
        text, so a bucket with two equally common reasons reports the same one on
        every run — a label that flapped would make two identical recomputes look
        like a change in the failure mix.
        """
        rows = db.execute(
            select(
                PaymentEvent.gateway_code.label("gateway"),
                Order.payment_method.label("method"),
                Order.payment_instrument.label("instrument"),
                PaymentEvent.message.label("message"),
                func.count(PaymentEvent.id).label("events"),
            )
            .select_from(PaymentEvent)
            .outerjoin(Order, Order.id == PaymentEvent.order_id)
            .where(
                PaymentEvent.created_at >= start,
                PaymentEvent.created_at < end,
                PaymentEvent.message.isnot(None),
                PaymentEvent.message != "",
                (PaymentEvent.payment_status == PaymentStatus.FAILED.value)
                | PaymentEvent.event_type.in_(_FAILURE_EVENT_TYPES),
            )
            .group_by(
                PaymentEvent.gateway_code,
                Order.payment_method,
                Order.payment_instrument,
                PaymentEvent.message,
            )
        ).all()

        best: dict[tuple[str, str, str], tuple[int, str]] = {}
        for row in rows:
            message = (row.message or "").strip()
            if not message:  # pragma: no cover - excluded by the WHERE clause
                continue
            key = (
                _dim(row.gateway)[:40],
                _dim(row.method)[:32],
                _dim(row.instrument)[:32],
            )
            # Most frequent wins; on a tie the alphabetically first message does,
            # which is arbitrary but STABLE, and stability is the whole property.
            candidate = (int(row.events or 0), message)
            current = best.get(key)
            if (
                current is None
                or candidate[0] > current[0]
                or (candidate[0] == current[0] and candidate[1] < current[1])
            ):
                best[key] = candidate
        return {key: value[1][:_FAILURE_REASON_CHARS] for key, value in best.items()}


def _empty_payment_bucket() -> dict[str, Any]:
    """A zeroed accumulator for one gateway x method x instrument within a day."""
    return {
        "attempts": 0,
        "paid": 0,
        "failed": 0,
        "cancelled": 0,
        "refunded": 0,
        "paid_minor": 0,
        "failed_minor": 0,
        "sum_settle": 0,
        "n_settle": 0,
        "mismatch_events": 0,
    }


# ===========================================================================
# shipment_geo_daily -> agg_geo_daily     (Pattern B: delete and reinsert)
# ===========================================================================
class ShipmentGeoDailyJob:
    """Demand and fulfilment by day x state x pincode.

    Pattern B, and the strongest case for it in this module: which pincodes
    ordered on a day is as data-dependent as a key set gets, and the geo map and
    the "problem pincodes" list are exactly where a stale row does damage — an
    operator stops shipping to a pincode on the strength of an RTO rate that no
    longer has an order behind it.

    Both dimensions are ``dimension_column()`` and ``'-'`` is a **real value**
    here, not merely a fallback: the retention job rolls pincode-grain rows up to
    state grain after 180 days by rewriting ``pincode`` to ``'-'``. A NULL would
    both escape the UNIQUE key and make a pruned row indistinguishable from a
    bug.

    Pincodes are normalised, never truncated
    ----------------------------------------
    ``orders.shipping_pincode`` is free text parsed out of an address, so it
    carries spaces, hyphens, short strings and occasional nonsense. See
    :func:`_pincode`: whitespace and hyphens come out, what remains must be six
    digits with a non-zero lead, and anything else becomes ``'-'``. Cutting a
    seven-character value down to six would invent an order from a real pincode
    nobody ordered from, and it would render on the map looking exactly like a
    measurement.

    ``state`` comes from ``orders.shipping_address_snapshot``, the frozen
    structured copy taken at checkout — not from the customer's saved address,
    which they can edit afterwards, and not from ``order_addresses``, which is a
    second copy of the same snapshot. An order with no structured snapshot (the
    legacy free-text path) has no state and lands under ``'-'``.

    A CREATION COHORT again
    -----------------------
    ``orders``, ``net_revenue`` and ``units`` are the orders *placed* in the
    bucket from that geography; ``delivered`` and ``rto`` are the state their
    shipments are in as of the moment the bucket was computed. That is what makes
    ``rto / (delivered + rto)`` a real RTO rate — the model's own definition —
    rather than a ratio of two different populations. It also means the bucket
    keeps moving after its day ends, which is what the recompute queue exists
    for.

    ``net_revenue`` here is GROSS OF REFUNDS
    ----------------------------------------
    Stated in the module docstring and worth repeating on the table that carries
    the column: a refund is dated by when it was issued, and subtracting a July
    refund from the March pincode-day that produced the order would put the
    reversal on a day it did not happen. ``agg_order_daily`` remains the
    reconciled revenue record. This column is ``SUM(orders.total_amount)`` over
    the recognised sales placed in the bucket.

    ``cod_orders`` and ``prepaid_orders`` PARTITION ``orders``
    ----------------------------------------------------------
    Split by ``orders.cod_balance > 0`` — the money the carrier actually collects
    on delivery — rather than by the ``payment_method`` label. A ``split_cod``
    order has a prepaid leg *and* a cash-on-delivery balance, so a label-based
    split leaves it in neither column and the two counters silently stop adding
    up to ``orders``. Balance-based, a split-COD order counts as COD, which is
    also the right answer for the question this table is asked: RTO risk and
    cash-collection exposure follow the balance, not the label.
    """

    name = "shipment_geo_daily"

    def run(self, db: Session, bucket_date: date, tz_generation: int) -> JobRunResult:
        start, end, _tz = _bucket_window(db, bucket_date)
        warnings: list[str] = []

        placed = db.execute(
            select(
                Order.id.label("order_id"),
                Order.total_amount.label("total"),
                Order.cod_balance.label("cod_balance"),
                Order.shipping_pincode.label("pincode"),
                Order.shipping_address_snapshot.label("snapshot"),
            ).where(*_revenue_window(start, end))
        ).all()

        units = self._units(db, start, end)
        geo_of_order: dict[int, tuple[str, str]] = {}
        buckets: dict[tuple[str, str], dict[str, Any]] = defaultdict(_empty_geo_bucket)

        unusable_pincodes = 0
        stateless = 0
        for row in placed:
            state = _dim(_snapshot_field(row.snapshot, "state"))[:64]
            raw_pincode = row.pincode or _snapshot_field(row.snapshot, "pincode")
            pincode = _pincode(raw_pincode)
            if state == DIMENSION_UNKNOWN:
                stateless += 1
            if pincode == DIMENSION_UNKNOWN and (raw_pincode or "").strip():
                unusable_pincodes += 1

            key = (state, pincode)
            geo_of_order[int(row.order_id)] = key
            bucket = buckets[key]
            bucket["orders"] += 1
            bucket["revenue_minor"] += to_minor(row.total)
            bucket["units"] += units.get(int(row.order_id), 0)
            if to_minor(row.cod_balance) > 0:
                bucket["cod_orders"] += 1
            else:
                bucket["prepaid_orders"] += 1

        self._fulfilment(db, start, end, geo_of_order, buckets, warnings)

        if unusable_pincodes:
            warnings.append(
                f"geo_pincode_unusable: {unusable_pincodes} order(s) on {bucket_date} "
                f"carry a shipping_pincode that is not {PINCODE_DIGITS} digits; they "
                f"are filed under the '{DIMENSION_UNKNOWN}' pincode rather than "
                "truncated to a pincode nobody ordered from"
            )
        if stateless:
            warnings.append(
                f"geo_state_unknown: {stateless} order(s) on {bucket_date} have no "
                "state in shipping_address_snapshot (legacy free-text addresses); "
                f"they are filed under the '{DIMENSION_UNKNOWN}' state, so the geo "
                "table's totals are a subset of the store's"
            )

        computed_at = _utcnow()
        rows = [
            {
                "bucket_date": bucket_date,
                "state": key[0],
                "pincode": key[1],
                "tz_generation": tz_generation,
                "computed_at": computed_at,
                "orders": bucket["orders"],
                "net_revenue": from_minor(bucket["revenue_minor"]),
                "units": bucket["units"],
                "cod_orders": bucket["cod_orders"],
                "prepaid_orders": bucket["prepaid_orders"],
                "delivered": bucket["delivered"],
                "rto": bucket["rto"],
                "sum_delivery_seconds": bucket["sum_delivery"],
                "n_delivery": bucket["n_delivery"],
            }
            for key, bucket in sorted(buckets.items())
        ]

        deleted = _replace_bucket(
            db,
            AggGeoDaily,
            rows,
            where=(
                AggGeoDaily.bucket_date == bucket_date,
                AggGeoDaily.tz_generation == tz_generation,
            ),
        )
        return JobRunResult(
            rows_written=len(rows), rows_deleted=deleted, warnings=_cap(warnings)
        )

    # -- pieces -----------------------------------------------------------
    @staticmethod
    def _units(db: Session, start: datetime, end: datetime) -> dict[int, int]:
        """Units per order for the bucket's recognised sales.

        Aggregated separately and merged in Python rather than joined into the
        order query: joining lines to orders and then summing order-level money
        in the same statement multiplies ``total_amount`` by the line count, and
        the result is a plausible number that is wrong by a factor nobody can
        see.
        """
        rows = db.execute(
            select(
                OrderItem.order_id.label("order_id"),
                func.coalesce(func.sum(OrderItem.quantity), 0).label("units"),
            )
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .where(*_revenue_window(start, end))
            .group_by(OrderItem.order_id)
        ).all()
        return {int(row.order_id): int(row.units or 0) for row in rows}

    @staticmethod
    def _fulfilment(
        db: Session,
        start: datetime,
        end: datetime,
        geo_of_order: Mapping[int, tuple[str, str]],
        buckets: dict[tuple[str, str], dict[str, Any]],
        warnings: list[str],
    ) -> None:
        """Delivery and RTO outcomes for the bucket's orders, plus transit time.

        Counted per SHIPMENT, not per order: a split parcel is two chances to be
        delivered and two chances to come back, and collapsing them to the order
        would make ``rto / (delivered + rto)`` disagree with the parcels an
        operator can actually see. ``sum_delivery_seconds`` measures
        ``orders.created_at -> shipments.delivered_at`` — order to doorstep, which
        is the number a customer experiences — and ``n_delivery`` is its own
        denominator, smaller than ``delivered`` whenever a delivery was recorded
        without a timestamp.
        """
        if not geo_of_order:
            return
        rows = db.execute(
            select(
                Shipment.id.label("shipment_id"),
                Shipment.order_id.label("order_id"),
                Shipment.shipment_status.label("status"),
                Shipment.delivered_at.label("delivered_at"),
                Order.created_at.label("ordered_at"),
            )
            .select_from(Shipment)
            .join(Order, Order.id == Shipment.order_id)
            .where(*_revenue_window(start, end))
        ).all()

        for row in rows:
            key = geo_of_order.get(int(row.order_id))
            if key is None:  # pragma: no cover - same window as `placed`
                continue
            bucket = buckets[key]
            if row.status in _RTO_STATUSES:
                bucket["rto"] += 1
            if row.status != ShipmentStatus.DELIVERED:
                continue
            bucket["delivered"] += 1
            seconds = _duration_seconds(row.ordered_at, row.delivered_at)
            if seconds is None:
                if row.delivered_at is not None:
                    warnings.append(
                        f"geo_negative_delivery: shipment {int(row.shipment_id)} was "
                        "delivered before its order was placed; the timestamps "
                        "contradict each other and the leg was excluded from "
                        "sum_delivery_seconds"
                    )
                continue
            bucket["sum_delivery"] += seconds
            bucket["n_delivery"] += 1


def _empty_geo_bucket() -> dict[str, Any]:
    """A zeroed accumulator for one state x pincode within one day."""
    return {
        "orders": 0,
        "revenue_minor": 0,
        "units": 0,
        "cod_orders": 0,
        "prepaid_orders": 0,
        "delivered": 0,
        "rto": 0,
        "sum_delivery": 0,
        "n_delivery": 0,
    }


# ===========================================================================
# promo_daily -> agg_promo_daily          (Pattern B: delete and reinsert)
# ===========================================================================
class PromoDailyJob:
    """Coupon and loyalty-reward redemption per day x code.

    Pattern B: which codes were used on a day is discovered, and a code must be
    able to leave the bucket when its only redemption is backed out.
    ``coupon_code`` is COALESCEd to ``'-'`` — a usage row whose coupon has since
    been deleted has no code to report, and the redemption still happened.

    Two populations, deliberately not reconciled
    ---------------------------------------------
    ``redemptions`` and ``discount_amount`` come from ``coupon_usages``, dated by
    the usage row's own ``created_at``: a redemption is an event and it happened
    when it happened. ``orders`` and ``order_revenue`` come from ``orders``, dated
    by ``created_at`` and restricted to recognised sales, matched on
    ``orders.coupon_code``.

    Those two do **not** have to agree, and the model docstring says so: a
    redemption is recorded at checkout *before* the order is finalised, an order
    can be cancelled after its code was redeemed, and ``coupon_usages.order_id``
    is nullable precisely because of that ordering. Keeping both counts lets the
    discrepancy be seen instead of averaged away, and it is why the group key is
    the **union** of the codes on either side: a code redeemed into an order that
    never paid gets a row with ``redemptions = 1`` and ``orders = 0``, which is
    the correct and useful thing to be able to read.

    ``discount_amount`` is the cost side and ``order_revenue`` the return side.
    Both are stored because only the pair tells the story — a code that gives away
    40k to bring in 45k and one that gives away 40k to bring in 400k are the same
    number on the cost side and completely different decisions.

    ``is_loyalty_reward`` is an ATTRIBUTE of the code, not part of the grain: the
    code determines it, so putting it in the UNIQUE key would let one code occupy
    two rows on one day if the flag were ever computed inconsistently. A code with
    no surviving ``coupons`` row cannot be classified and is written ``False`` —
    the column's server default — and named in a warning, because guessing
    "loyalty" for a deleted code would move money between the marketing and
    loyalty ledgers on the strength of an absence.

    Deliberately absent: ROI, uplift and incrementality. None are measurable from
    redemption data — they need a control group we do not have — and a stored
    ``roi_pct`` would be a guess with a decimal point on it.
    """

    name = "promo_daily"

    def run(self, db: Session, bucket_date: date, tz_generation: int) -> JobRunResult:
        start, end, _tz = _bucket_window(db, bucket_date)
        warnings: list[str] = []

        buckets: dict[str, dict[str, Any]] = defaultdict(_empty_promo_bucket)
        orphan_usages = self._redemptions(db, start, end, buckets)
        self._orders(db, start, end, buckets)

        loyalty = self._loyalty_flags(db, buckets.keys())
        unclassified = sorted(
            code
            for code in buckets
            if code not in loyalty and code != DIMENSION_UNKNOWN
        )

        if orphan_usages:
            warnings.append(
                f"promo_usage_without_coupon: {orphan_usages} redemption(s) on "
                f"{bucket_date} reference a coupon row that no longer exists; they "
                f"are filed under the '{DIMENSION_UNKNOWN}' code so the discount "
                "they gave away is still counted"
            )
        if unclassified:
            warnings.append(
                f"promo_code_not_in_catalogue: {unclassified[:5]} appear on orders in "
                f"{bucket_date} with no coupons row, so is_loyalty_reward is written "
                "False by default rather than measured; a deleted code cannot be "
                "classified and guessing would move spend between the marketing and "
                "loyalty ledgers"
            )

        computed_at = _utcnow()
        rows = [
            {
                "bucket_date": bucket_date,
                "coupon_code": code,
                "tz_generation": tz_generation,
                "computed_at": computed_at,
                "redemptions": bucket["redemptions"],
                "discount_amount": from_minor(bucket["discount_minor"]),
                "orders": bucket["orders"],
                "order_revenue": from_minor(bucket["revenue_minor"]),
                "is_loyalty_reward": bool(loyalty.get(code, False)),
            }
            for code, bucket in sorted(buckets.items())
        ]

        deleted = _replace_bucket(
            db,
            AggPromoDaily,
            rows,
            where=(
                AggPromoDaily.bucket_date == bucket_date,
                AggPromoDaily.tz_generation == tz_generation,
            ),
        )
        return JobRunResult(
            rows_written=len(rows), rows_deleted=deleted, warnings=_cap(warnings)
        )

    # -- pieces -----------------------------------------------------------
    @staticmethod
    def _redemptions(
        db: Session,
        start: datetime,
        end: datetime,
        buckets: dict[str, dict[str, Any]],
    ) -> int:
        """Redemption events and the discount they gave away. Returns orphan count.

        LEFT JOIN to ``coupons``: the FK is ``ON DELETE CASCADE`` so a usage
        should never outlive its coupon, but an inner join would drop the
        discount silently if one ever did, and money that disappears from the
        cost side is worse than money filed under ``'-'``.
        """
        rows = db.execute(
            select(
                Coupon.code.label("code"),
                func.count(CouponUsage.id).label("redemptions"),
                func.coalesce(func.sum(CouponUsage.discount_amount), 0).label(
                    "discount"
                ),
            )
            .select_from(CouponUsage)
            .outerjoin(Coupon, Coupon.id == CouponUsage.coupon_id)
            .where(CouponUsage.created_at >= start, CouponUsage.created_at < end)
            .group_by(Coupon.code)
        ).all()

        orphans = 0
        for row in rows:
            code = _dim(row.code)[:64]
            if code == DIMENSION_UNKNOWN:
                orphans += int(row.redemptions or 0)
            bucket = buckets[code]
            bucket["redemptions"] += int(row.redemptions or 0)
            bucket["discount_minor"] += to_minor(row.discount)
        return orphans

    @staticmethod
    def _orders(
        db: Session,
        start: datetime,
        end: datetime,
        buckets: dict[str, dict[str, Any]],
    ) -> None:
        """Recognised orders carrying a code, and what they were worth.

        ``orders.coupon_code`` is written as ``coupon.code`` verbatim by
        ``PaymentService``, so the two sides of this table key on the same string
        without a second normalisation rule that could drift from the first.
        """
        rows = db.execute(
            select(
                Order.coupon_code.label("code"),
                func.count(Order.id).label("orders"),
                func.coalesce(func.sum(Order.total_amount), 0).label("revenue"),
            )
            .where(*_revenue_window(start, end), Order.coupon_code.isnot(None))
            .group_by(Order.coupon_code)
        ).all()
        for row in rows:
            code = _dim(row.code)[:64]
            bucket = buckets[code]
            bucket["orders"] += int(row.orders or 0)
            bucket["revenue_minor"] += to_minor(row.revenue)

    @staticmethod
    def _loyalty_flags(db: Session, codes: Iterable[str]) -> dict[str, bool]:
        """``{code: is_loyalty_reward}`` for the codes that still have a coupon row."""
        wanted = [code for code in codes if code != DIMENSION_UNKNOWN]
        if not wanted:
            return {}
        return {
            _dim(row.code)[:64]: bool(row.is_loyalty_reward)
            for row in db.execute(
                select(Coupon.code, Coupon.is_loyalty_reward).where(
                    Coupon.code.in_(wanted)
                )
            ).all()
        }


def _empty_promo_bucket() -> dict[str, Any]:
    """A zeroed accumulator for one coupon code within one day."""
    return {
        "redemptions": 0,
        "discount_minor": 0,
        "orders": 0,
        "revenue_minor": 0,
    }


# ===========================================================================
# cohort_monthly -> agg_customer_cohort_monthly  (Pattern B: full recompute)
# ===========================================================================
class CohortMonthlyJob:
    """Monthly acquisition cohorts x months-since-acquisition retention grid.

    Grain is ``(cohort_month, period_index)``. ``cohort_month`` is ``'YYYY-MM'``
    of the customer's first recognised order in **store-local** time;
    ``period_index`` is whole months elapsed since then, so ``period_index = 0``
    is the acquisition month itself.

    Full recompute, and that is the whole design
    ---------------------------------------------
    Every run deletes and reinserts the trailing
    :data:`COHORT_MONTHS_RECOMPUTED` cohorts. Cohort retention is exquisitely
    sensitive to late changes — a refund issued in August belongs to the March
    cohort's month-5 revenue, and a cancellation in August removes an order from
    the March cohort's month-5 activity — and an incrementally updated grid would
    need a reconciliation job to notice. Recomputing the whole window makes the
    table immune to late-arriving corrections **by construction**, which is a
    property rather than a promise. Cohorts older than the window are frozen; if
    they ever need correcting, widen the constant rather than patching rows.

    The DELETE is therefore scoped by ``cohort_month``, not by ``bucket_date``.
    ``bucket_date`` is not in the UNIQUE key — ``(cohort_month, period_index)``
    already determines it — so deleting by bucket would leave half the grid
    behind. That also means this job writes rows whose ``bucket_date`` is not the
    bucket it was handed: ``bucket_date`` is the first day of the *period being
    measured* (cohort month plus ``period_index`` months), exactly as
    ``AggCustomerCohortMonthly`` documents.

    A consequence for scheduling: the output depends only on the bucket's
    **month**, not on its day, so running this job over a backfilled year of days
    recomputes the same grid 365 times. Schedule it once per tick, not once per
    backfilled day.

    ``cohort_size`` MUST NEVER BE SUMMED
    -------------------------------------
    It is written **once per cohort** and then repeated on every one of that
    cohort's period rows, so that a single row is self-sufficient as a
    denominator for its own retention figure. Summing it across a cohort's rows
    multiplies the cohort by the number of periods in range; summing it across
    cohorts in a window does the same thing per cohort. ``metric_kind.classify``
    already returns ``DISTINCT`` for it and ``assert_summable`` refuses, and
    ``registry.py`` deliberately leaves ``ltv`` unbound on this source for the
    same reason. Retention is ``active_customers / cohort_size``, divided at read
    time — both sides are stored precisely so no percentage is materialised.

    What each column measures
    -------------------------
    * ``cohort_size`` — customers whose first recognised order fell in
      ``cohort_month``. A LEVEL of the cohort, constant across its rows.
    * ``active_customers`` — cohort members with at least one recognised order in
      that period. A distinct count within the period, so it is additive across
      *periods* only in the sense of "how many member-periods were active"; it is
      not additive across cohorts into a store-wide distinct count.
    * ``orders`` — recognised orders placed by cohort members in that period.
    * ``revenue`` — ``SUM(total_amount)`` over those orders, **less the refunds
      issued in that period** to members of that cohort, de-duplicated across the
      two refund sources exactly as ``jobs._refunds_minor`` does it. The sale sits
      in its own period and the reversal in its own, which is the recognition rule
      the whole subsystem runs on — and it is why a period can go negative when a
      cohort's only activity was money going back.

    ``period_index = 0`` always has ``active_customers == cohort_size`` by
    construction: a member's first order is, by definition, in the acquisition
    month. That invariant is checked on every run and a violation is reported
    rather than smoothed over, because the only way to break it is for the cohort
    assignment and the activity scan to disagree about what a month is.

    A cohort with no acquisitions gets **no rows at all**, rather than a row
    family of zeros. ``retention_pct`` for a zero-size cohort is undefined (the
    resolver returns ``None``, not 0%), and a month the store did not trade in has
    nothing to say — writing 300 zero rows for it would put cohorts that never
    existed on the heatmap.
    """

    name = "cohort_monthly"

    def run(self, db: Session, bucket_date: date, tz_generation: int) -> JobRunResult:
        tz = store_timezone(db)
        warnings: list[str] = []

        newest = _month_ordinal(bucket_date)
        oldest = newest - (COHORT_MONTHS_RECOMPUTED - 1)
        # Both boundaries come from `timebox`, like every other boundary in this
        # subsystem: the first instant of the oldest cohort month and the first
        # instant of the month AFTER the bucket's, both store-local.
        window_start, _ = day_bounds_utc(_month_start(oldest), tz)
        window_end, _ = day_bounds_utc(_month_start(newest + 1), tz)

        cohort_of = self._cohort_of_customer(db, window_end, tz, oldest, newest)
        members: dict[int, set[int]] = defaultdict(set)
        for user_id, cohort in cohort_of.items():
            members[cohort].add(user_id)

        cells: dict[tuple[int, int], dict[str, Any]] = defaultdict(_empty_cohort_cell)
        self._activity(db, window_start, window_end, tz, cohort_of, cells)
        self._reversals(db, window_start, window_end, tz, cohort_of, cells)

        computed_at = _utcnow()
        rows: list[dict[str, Any]] = []
        for cohort in sorted(members):
            size = len(members[cohort])
            if not size:  # pragma: no cover - a cohort exists because it has members
                continue
            cohort_month = _month_key(cohort)
            for period in range(0, newest - cohort + 1):
                cell = cells.get((cohort, period), _empty_cohort_cell())
                active = len(cell["customers"])
                if period == 0 and active != size:
                    warnings.append(
                        f"cohort_period_zero_mismatch: cohort {cohort_month} has "
                        f"{size} member(s) but only {active} were active in their "
                        "own acquisition month; the cohort assignment and the "
                        "activity scan disagree about month boundaries"
                    )
                rows.append(
                    {
                        "bucket_date": _month_start(cohort + period),
                        "cohort_month": cohort_month,
                        "period_index": period,
                        "tz_generation": tz_generation,
                        "computed_at": computed_at,
                        # Written once per cohort and repeated here. NEVER SUM IT.
                        "cohort_size": size,
                        "active_customers": active,
                        "orders": cell["orders"],
                        "revenue": from_minor(cell["revenue_minor"]),
                    }
                )

        deleted = _replace_bucket(
            db,
            AggCustomerCohortMonthly,
            rows,
            where=(
                AggCustomerCohortMonthly.tz_generation == tz_generation,
                AggCustomerCohortMonthly.cohort_month >= _month_key(oldest),
                AggCustomerCohortMonthly.cohort_month <= _month_key(newest),
            ),
        )
        return JobRunResult(
            rows_written=len(rows), rows_deleted=deleted, warnings=_cap(warnings)
        )

    # -- pieces -----------------------------------------------------------
    @staticmethod
    def _cohort_of_customer(
        db: Session,
        window_end: datetime,
        tz: ZoneInfo,
        oldest: int,
        newest: int,
    ) -> dict[int, int]:
        """``{user_id: cohort ordinal}`` for customers acquired inside the window.

        ``MIN(created_at)`` over the customer's **entire** recognised history, not
        just the window: a customer whose first order was in 2019 is not acquired
        in 2026 because that is the earliest order the window can see. Restricting
        the scan and then calling the earliest visible order a first order is the
        classic cohort bug, and it silently re-acquires the whole customer base
        every time the window slides.

        Customers whose first order falls outside ``[oldest, newest]`` are dropped
        here, so everything downstream works on cohort members only.
        """
        rows = db.execute(
            select(
                Order.user_id.label("user_id"),
                func.min(Order.created_at).label("first_at"),
            )
            .where(*_recognised_before(window_end))
            .group_by(Order.user_id)
        ).all()

        cohorts: dict[int, int] = {}
        for row in rows:
            if row.first_at is None:  # pragma: no cover - GROUP BY guarantees one
                continue
            cohort = _month_ordinal(local_day(_as_utc(row.first_at), tz))
            if oldest <= cohort <= newest:
                cohorts[int(row.user_id)] = cohort
        return cohorts

    @staticmethod
    def _activity(
        db: Session,
        window_start: datetime,
        window_end: datetime,
        tz: ZoneInfo,
        cohort_of: Mapping[int, int],
        cells: dict[tuple[int, int], dict[str, Any]],
    ) -> None:
        """Recognised orders in the window, attributed to their customer's cohort.

        Every order in the window is streamed and filtered in Python rather than
        constrained with ``user_id IN (...)``: the member list is the whole
        acquired customer base of two years, and an IN list that size is a query
        planner's worst case for no gain — the rows have to be read either way.
        """
        if not cohort_of:
            return
        rows = db.execute(
            select(
                Order.user_id.label("user_id"),
                Order.created_at.label("created_at"),
                Order.total_amount.label("total"),
            ).where(*_revenue_window(window_start, window_end))
        ).all()

        for row in rows:
            cohort = cohort_of.get(int(row.user_id))
            if cohort is None:
                continue
            period = _month_ordinal(local_day(_as_utc(row.created_at), tz)) - cohort
            if period < 0:  # pragma: no cover - the cohort IS the first order
                continue
            cell = cells[(cohort, period)]
            cell["customers"].add(int(row.user_id))
            cell["orders"] += 1
            cell["revenue_minor"] += to_minor(row.total)

    @staticmethod
    def _reversals(
        db: Session,
        window_start: datetime,
        window_end: datetime,
        tz: ZoneInfo,
        cohort_of: Mapping[int, int],
        cells: dict[tuple[int, int], dict[str, Any]],
    ) -> None:
        """Refunds *issued* in the window, subtracted from the period they landed in.

        Mirrors ``jobs._refunds_minor`` term for term, per refund rather than as a
        single sum: a returns-driven refund carries an explicit
        ``returns.refund_amount``, while a whole-order refund has no amount column
        at all and is valued at ``orders.total_amount`` — but only when the order
        has no refunded return row, or the same money is subtracted twice.

        A reversal only ever moves ``revenue``. It does **not** decrement
        ``orders`` or ``active_customers``: the customer really did order in that
        period, and erasing the activity would make a refunded month look like a
        month with no customer in it, which is the opposite of what a retention
        grid is for.
        """
        if not cohort_of:
            return

        def book(user_id: Any, moment: datetime | None, amount: Any) -> None:
            cohort = cohort_of.get(int(user_id))
            if cohort is None or moment is None:
                return
            period = _month_ordinal(local_day(_as_utc(moment), tz)) - cohort
            if period < 0:  # pragma: no cover - refunds postdate their order
                return
            cells[(cohort, period)]["revenue_minor"] -= to_minor(amount)

        from_returns = db.execute(
            select(
                ReturnRequest.user_id.label("user_id"),
                ReturnRequest.refunded_at.label("refunded_at"),
                ReturnRequest.refund_amount.label("amount"),
            ).where(
                ReturnRequest.refunded_at.isnot(None),
                ReturnRequest.refunded_at >= window_start,
                ReturnRequest.refunded_at < window_end,
            )
        ).all()
        for row in from_returns:
            book(row.user_id, row.refunded_at, row.amount)

        has_refunded_return = (
            select(ReturnRequest.id)
            .where(
                ReturnRequest.order_id == Order.id,
                ReturnRequest.refunded_at.isnot(None),
            )
            .correlate(Order)
            .exists()
        )
        whole_order = db.execute(
            select(
                Order.user_id.label("user_id"),
                Order.refunded_at.label("refunded_at"),
                Order.total_amount.label("amount"),
            ).where(
                Order.refunded_at.isnot(None),
                Order.refunded_at >= window_start,
                Order.refunded_at < window_end,
                ~has_refunded_return,
            )
        ).all()
        for row in whole_order:
            book(row.user_id, row.refunded_at, row.amount)


def _empty_cohort_cell() -> dict[str, Any]:
    """A zeroed accumulator for one (cohort, period) cell of the grid."""
    return {"customers": set(), "orders": 0, "revenue_minor": 0}


def _recognised_before(end: datetime) -> list[Any]:
    """Every order whose SALE is recognised at any time before ``end``.

    The open-ended twin of ``jobs._revenue_window``, and it imports
    ``margin._recognised_sale`` the same lazy way and for the same reason:
    ``margin`` pulls in the cost-rule engine and the dashboard service, and a
    module-level import here would make the aggregation package's import graph
    depend on them just to register four jobs.
    """
    from app.services.analytics.margin import _recognised_sale

    return [_recognised_sale(), Order.created_at < end]


register(PaymentDailyJob())
register(ShipmentGeoDailyJob())
register(PromoDailyJob())
register(CohortMonthlyJob())
