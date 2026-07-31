"""The loyalty rollup: ``loyalty_daily`` -> ``agg_loyalty_daily``.

One job, Pattern B (delete-and-reinsert). The two write patterns and the shared
helpers are documented in :mod:`app.services.analytics.aggregation.jobs` and are
imported from there rather than restated; the dimensioned shape follows
:mod:`~app.services.analytics.aggregation.jobs_ops` and
:mod:`~app.services.analytics.aggregation.jobs_finance`.

What this job refuses to produce
================================
Every other job in this package has a number it declines to invent, and so does
this one — twice.

**No tier on a redemption.** `LoyaltyService.redeem` writes the ledger row with
``ref_type='coupon'`` and the minted coupon's id, and neither
``points_transactions`` nor ``coupons`` carries a `redemption_tier_id`. The only
surviving trace of which `RedemptionTier` was traded is the free-text
``description`` — "Redeemed: {tier.name}" — and recovering a *dimension* by
parsing a human-readable label would re-partition every past month the first
time a tier is renamed, while looking exactly like a measurement. Matching on
``cost_points`` is no better: two tiers may cost the same and a tier's price may
be edited afterwards. So ``agg_loyalty_daily`` has no tier dimension, this job
computes none, and "redemptions by tier" stays an unanswered question rather
than an approximated one. The fix is a captured `redemption_tier_id`, forward
only from the day it ships.

**No points-to-money.** A point has no stored monetary value anywhere in this
schema. What a redemption is finally worth depends on the order the minted
coupon lands on, and that is already measured — as real money, at order grain —
by ``agg_promo_daily.discount_amount``. A second monetary figure here, derived
from a points-to-rupee rate nobody has configured, would be a guess with a
decimal point on it.

Signed `delta`, unsigned counters
=================================
``points_transactions.delta`` is signed: positive is an earn, negative is a
spend, a reversal, an expiry or a debit adjustment. This job splits it by sign
into two **non-negative** counters, ``points_earned`` and ``points_debited``, and
also measures ``net_points`` as ``SUM(delta)`` independently. Storing only the
net would make "how many points did we issue" unanswerable — 1 000 issued
against 1 000 redeemed and a completely dormant month are the same net zero —
and issuance and redemption are different events with different causes.

The identity ``points_earned - points_debited = net_points`` is checked per row
on every run. It is a real check rather than a restatement precisely because the
third term is measured rather than derived; a mismatch is reported as a warning
on the bucket that was written, never swallowed and never silently corrected.

Store-local, like everything else
=================================
Day boundaries come from :mod:`app.services.analytics.timebox`. A transaction at
23:00 IST is 17:30 UTC on the same date and belongs to that IST day; one at
00:30 IST is 19:00 UTC on the *previous* date and belongs to the later IST day.
The same window bounds ``referrals.completed_at`` and the ``< end`` cutoff that
reconstructs the closing balance.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.analytics_base import DIMENSION_UNKNOWN
from app.models.analytics_loyalty import AggLoyaltyDaily
from app.models.loyalty import PointsReason, PointsTransaction
from app.models.referral import Referral, ReferralStatus
from app.services.analytics.aggregation.base import JobRunResult, register
from app.services.analytics.aggregation.jobs import _bucket_window, _cap, _dim, _utcnow
from app.services.analytics.aggregation.jobs_finance import _replace_bucket

__all__ = ["LoyaltyDailyJob", "STORE_LEVEL_REASON", "DEBIT_SUBSET_COLUMNS"]

#: The `reason` value on the row carrying measures that belong to the DAY rather
#: than to a reason (referral completions, the closing balance). It is the `'-'`
#: sentinel, and it is unambiguous here because `points_transactions.reason` is
#: NOT NULL — no real ledger row can ever land on this key.
STORE_LEVEL_REASON = DIMENSION_UNKNOWN

#: reason -> the named-subset column its debits are ALSO counted into. Each of
#: these is a subset of `points_debited` and is zero on every other reason's row,
#: so a window total is a plain SUM over every row in range with no row filter.
#: The negative half of `admin_adjust` is deliberately absent: it is a
#: correction, not a programme mechanic, and a column would invite it onto a
#: chart as though it were one.
DEBIT_SUBSET_COLUMNS: dict[PointsReason, str] = {
    PointsReason.REDEEM: "points_redeemed",
    PointsReason.EXPIRY: "points_expired",
    PointsReason.REFUND_REVERSAL: "points_reversed",
}


# ===========================================================================
# loyalty_daily -> agg_loyalty_daily     (Pattern B: delete and reinsert)
# ===========================================================================
class LoyaltyDailyJob:
    """Points movement per store-local day x ledger reason, plus referrals.

    Pattern B: which reasons moved points on a day is discovered, not declared,
    so a reason must be able to *leave* the bucket when its only transaction is
    backed out. An upsert cannot express a vanishing key — it would write
    nothing for that reason and leave yesterday's row in place, still reporting
    points that are no longer in the ledger.

    ``reason`` is written as the enum's public lowercase value (``place_order``,
    ``redeem``) and is never NULL: the column sits in the UNIQUE key, where a
    NULL does not collide on MySQL and the next run would insert a second row
    for the same reason rather than replacing it.

    The three shapes of row this job writes
    ---------------------------------------
    1. **One row per reason that traded**, carrying the split-by-sign counters,
       the signed net, the transaction count and the distinct-member count.
    2. **The store-level ``'-'`` row**, carrying ``referral_completions`` and
       ``points_outstanding_close`` — the two measures that are properties of
       the day, not of a reason. Written for every bucket, including a silent
       one: an outstanding liability is a real number on every calendar day.
    3. **Nothing else.** A reason with no rows in the bucket has no row here,
       which is what makes the delete-and-reinsert meaningful.

    ``points_outstanding_close`` is the only LEVEL in the table and it is
    reconstructed rather than read: ``SUM(delta)`` over every ledger row written
    before the store-local close of the bucket. The ledger is append-only, so
    that sum is stable for any past day. ``customers.points_balance`` is
    deliberately NOT consulted — it is a mutable cached current value with no
    history, and writing today's cache as a past day's closing balance is the
    same fiction ``agg_inventory_daily`` exists to refuse.
    """

    name = "loyalty_daily"

    def run(self, db: Session, bucket_date: date, tz_generation: int) -> JobRunResult:
        start, end, _tz = _bucket_window(db, bucket_date)
        warnings: list[str] = []

        buckets = self._ledger(db, start, end, warnings)
        distinct = self._distinct_members(db, start, end)

        computed_at = _utcnow()
        rows: list[dict[str, Any]] = []
        for reason, bucket in sorted(buckets.items()):
            self._check_identity(reason, bucket, bucket_date, warnings)
            rows.append(
                {
                    "bucket_date": bucket_date,
                    "reason": reason,
                    "tz_generation": tz_generation,
                    "computed_at": computed_at,
                    "points_earned": bucket["points_earned"],
                    "points_debited": bucket["points_debited"],
                    "points_redeemed": bucket["points_redeemed"],
                    "points_expired": bucket["points_expired"],
                    "points_reversed": bucket["points_reversed"],
                    "net_points": bucket["net_points"],
                    "transactions": bucket["transactions"],
                    "distinct_customers": distinct.get(reason, 0),
                    # Reason rows carry no store-level measure — see the model
                    # docstring. Spreading either across them would multiply it
                    # by however many reasons happened to trade that day.
                    "referral_completions": 0,
                    "points_outstanding_close": 0,
                }
            )

        rows.append(
            {
                "bucket_date": bucket_date,
                "reason": STORE_LEVEL_REASON,
                "tz_generation": tz_generation,
                "computed_at": computed_at,
                **{column: 0 for column in _REASON_COLUMNS},
                "distinct_customers": 0,
                "referral_completions": self._referral_completions(db, start, end),
                "points_outstanding_close": self._outstanding_close(db, end),
            }
        )

        if not buckets:
            warnings.extend(self._quiet_bucket_warning(db, bucket_date))

        deleted = _replace_bucket(
            db,
            AggLoyaltyDaily,
            rows,
            where=(
                AggLoyaltyDaily.bucket_date == bucket_date,
                AggLoyaltyDaily.tz_generation == tz_generation,
            ),
        )
        return JobRunResult(
            rows_written=len(rows), rows_deleted=deleted, warnings=_cap(warnings)
        )

    # -- pieces -----------------------------------------------------------
    def _ledger(
        self,
        db: Session,
        start: datetime,
        end: datetime,
        warnings: list[str],
    ) -> dict[str, dict[str, int]]:
        """Split-by-sign counters per reason, from one grouped scan.

        ``delta`` is signed, so the two counters are built with CASE arms rather
        than by summing it: ``points_earned`` takes the positive rows as they
        are and ``points_debited`` takes the NEGATION of the negative ones, so
        both come out non-negative. ``net_points`` is the plain ``SUM(delta)``
        alongside them, which is what makes the identity check below a check on
        two independent measurements rather than on one number written twice.
        """
        earned = func.coalesce(
            func.sum(
                case((PointsTransaction.delta > 0, PointsTransaction.delta), else_=0)
            ),
            0,
        )
        debited = func.coalesce(
            func.sum(
                case((PointsTransaction.delta < 0, -PointsTransaction.delta), else_=0)
            ),
            0,
        )
        rows = db.execute(
            select(
                PointsTransaction.reason.label("reason"),
                earned.label("earned"),
                debited.label("debited"),
                func.coalesce(func.sum(PointsTransaction.delta), 0).label("net"),
                func.count(PointsTransaction.id).label("transactions"),
            )
            .where(
                PointsTransaction.created_at >= start,
                PointsTransaction.created_at < end,
            )
            .group_by(PointsTransaction.reason)
        ).all()

        buckets: dict[str, dict[str, int]] = defaultdict(_empty_reason_bucket)
        for row in rows:
            key = _reason_key(row.reason)
            if key == STORE_LEVEL_REASON:
                # Unreachable while `reason` is a NOT NULL enum, and named
                # loudly if it ever stops being: a ledger row landing on the
                # sentinel would collide with the store-level row and its points
                # would be silently overwritten by the referral/balance row.
                warnings.append(
                    "loyalty_reason_missing: a points_transactions row in this "
                    f"bucket has no usable reason ({row.reason!r}) and would "
                    f"collide with the '{STORE_LEVEL_REASON}' store-level row; "
                    "its points are counted under that key and the two measures "
                    "on it can no longer be read apart"
                )
            bucket = buckets[key]
            bucket["points_earned"] += int(row.earned or 0)
            bucket["points_debited"] += int(row.debited or 0)
            bucket["net_points"] += int(row.net or 0)
            bucket["transactions"] += int(row.transactions or 0)
            subset = DEBIT_SUBSET_COLUMNS.get(row.reason)
            if subset is not None:
                bucket[subset] += int(row.debited or 0)
        return dict(buckets)

    @staticmethod
    def _distinct_members(
        db: Session, start: datetime, end: datetime
    ) -> dict[str, int]:
        """Distinct `user_id` per reason. Its own query, and deliberately so.

        A COUNT(DISTINCT ...) cannot ride along in the grouped scan above
        without changing what the other aggregates count, and it is not additive
        with them either: this figure must be recomputed for any window wider
        than a day, which is exactly why it is stored under a name
        `metric_kind` already classifies as DISTINCT.
        """
        rows = db.execute(
            select(
                PointsTransaction.reason.label("reason"),
                func.count(func.distinct(PointsTransaction.user_id)).label("members"),
            )
            .where(
                PointsTransaction.created_at >= start,
                PointsTransaction.created_at < end,
            )
            .group_by(PointsTransaction.reason)
        ).all()
        return {_reason_key(row.reason): int(row.members or 0) for row in rows}

    @staticmethod
    def _referral_completions(db: Session, start: datetime, end: datetime) -> int:
        """Referrals that reached COMPLETED inside this store-local day.

        Dated by ``completed_at``, not by ``created_at``: a referral signed up in
        March and completed in July is July's event, because that is when the
        friend's first order was paid and when the referrer's reward was minted.
        Status is checked as well as the timestamp — ``completed_at`` is only
        ever stamped alongside the status move, and requiring both means a
        hand-edited row cannot contribute a completion that did not happen.
        """
        return int(
            db.execute(
                select(func.count(Referral.id)).where(
                    Referral.status == ReferralStatus.COMPLETED,
                    Referral.completed_at.isnot(None),
                    Referral.completed_at >= start,
                    Referral.completed_at < end,
                )
            ).scalar_one()
            or 0
        )

    @staticmethod
    def _outstanding_close(db: Session, end: datetime) -> int:
        """Programme-wide outstanding points at the close of the bucket.

        ``SUM(delta)`` over the whole ledger below the store-local end of the
        day — a LEVEL, and reconstructible for any past day only because
        `points_transactions` is append-only. It is NOT
        ``SUM(customers.points_balance)``: that column is a mutable cache of the
        CURRENT balance, so using it would write today's number onto every
        historical bucket and the trend line would be flat by construction.
        """
        return int(
            db.execute(
                select(func.coalesce(func.sum(PointsTransaction.delta), 0)).where(
                    PointsTransaction.created_at < end
                )
            ).scalar_one()
            or 0
        )

    @staticmethod
    def _check_identity(
        reason: str,
        bucket: dict[str, int],
        bucket_date: date,
        warnings: list[str],
    ) -> None:
        """`points_earned - points_debited` must equal the measured `net_points`.

        Reported, never repaired. The two sides come from the same scan but from
        different expressions, so a mismatch means the CASE arms and the plain
        SUM disagree about a row — which is a bug in this job, and correcting
        one side to match the other would hide it while leaving the numbers
        wrong.
        """
        expected = bucket["points_earned"] - bucket["points_debited"]
        if expected != bucket["net_points"]:
            warnings.append(
                f"loyalty_net_mismatch: {reason} on {bucket_date} has "
                f"points_earned - points_debited = {expected} but a measured "
                f"net_points of {bucket['net_points']}; the row is written as "
                "measured and the two figures must not be reconciled by hand"
            )

    @staticmethod
    def _quiet_bucket_warning(db: Session, bucket_date: date) -> list[str]:
        """Separate "no points ever" from "no points that day".

        Both write a bucket whose only row is the store-level one, and only one
        of them is a quiet Tuesday. One ``LIMIT 1`` probe of the whole ledger is
        what lets the run log tell them apart — the same distinction
        ``funnel_daily`` draws, for the same reason.
        """
        any_row = db.execute(select(PointsTransaction.id).limit(1)).scalars().first()
        if any_row is not None:
            return []
        return [
            "loyalty_source_empty: points_transactions holds no rows at all, so "
            f"{bucket_date} has only the store-level row and an outstanding "
            "balance of 0 because the programme has never issued a point — not "
            "because a day of activity was missed"
        ]


#: The per-reason measure columns, zeroed on the store-level row. Derived from
#: one place so a column added to the model cannot be forgotten on one of the
#: two row shapes and silently default to 0 on inserts that name every column.
_REASON_COLUMNS: tuple[str, ...] = (
    "points_earned",
    "points_debited",
    "points_redeemed",
    "points_expired",
    "points_reversed",
    "net_points",
    "transactions",
)


def _empty_reason_bucket() -> dict[str, int]:
    """A zeroed accumulator for one reason within one day."""
    return {column: 0 for column in _REASON_COLUMNS}


def _reason_key(reason: Any) -> str:
    """The ledger `reason` as the lowercase value written to the dimension.

    SQLAlchemy hands back a `PointsReason` member (MySQL stores the enum NAME),
    and the rollup stores the public `.value` — the vocabulary the model, the
    API and this docstring all use. `_dim` is applied on the way out so a value
    that somehow arrives empty becomes the `'-'` sentinel rather than a NULL
    that would escape the UNIQUE key.
    """
    value = reason.value if isinstance(reason, PointsReason) else reason
    return _dim(None if value is None else str(value))


register(LoyaltyDailyJob())
