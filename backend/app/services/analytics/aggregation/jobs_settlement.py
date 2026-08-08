"""``settlement_daily`` -> ``agg_settlement_daily``.

The thirteenth rollup, and the first whose source is not a table this
application writes: ``payment_settlements`` holds what the *gateway* reported,
ingested from its settlement file by
:mod:`app.services.analytics.settlements`. This job turns those lines into the
daily row the reconciliation view reads.

Pattern B, and it has to be
===========================
``gateway`` is a discovered dimension — which gateways settled on a given day is
known only after the data is read — and it can *disappear* between two runs of
the same bucket. A settlement file re-uploaded after a correction can remove the
only line a gateway had on that day, and ``INSERT ... ON DUPLICATE KEY UPDATE``
has nothing to say about a key that vanished: it would leave yesterday's row at
yesterday's numbers, in the exact table an operator uses to decide whether the
gateway paid what it owed. So the bucket's scope is DELETEd and reinserted inside
the runner's single transaction, exactly as ``jobs_finance`` does.

Two populations, one row, and they are never added together
============================================================
The columns suffixed ``_transacted`` are aggregated over settlement lines whose
``payment_date`` is this bucket. The columns suffixed ``_settled`` are aggregated
over lines whose ``settlement_date`` is this bucket. On a normal day these are
almost disjoint sets — a payment captured on the 30th settles on the 2nd — and
the split is the entire point:

* the **fee** is a cost of the sale and must land in the same bucket as the
  revenue it was charged against, i.e. the payment's day;
* the **cash** moved on the day the payout cleared, i.e. the settlement's day.

Conflating them is the classic error in settlement reporting and it misstates
both numbers simultaneously, plausibly, and silently. Three separate queries run
here rather than one clever grouped one, because a single query would have to
pick one date column and the second population would have to be reconstructed —
which is precisely where the conflation gets reintroduced.

The third query is the one that finds an absence
=================================================
``unsettled_payments`` counts payments we captured on this day that no settlement
line accounts for, once the gateway's own T+2/T+3 cycle has had time to run. It
cannot come from ``payment_settlements`` — the whole point is that there is no
row there — so it is computed against ``order_payments``. It is stored rather
than derived at read time because a rollup that carried only the settlements it
*has* would make a day nobody uploaded a report for look exactly like a day that
fully settled.

A quiet day still writes a row
==============================
If a bucket has no settlement lines and no unsettled captures, the job writes no
row for it, and that absence means "we have no settlement information for this
day" — which is honest and is what the reconciliation report reads as
``not_configured``. It does NOT write a zero row, because a zero row would assert
that the gateway settled nothing, and "no report uploaded" and "nothing settled"
are the two readings this subsystem exists to keep apart.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from sqlalchemy.orm import Session

from app.models.analytics_base import DIMENSION_UNKNOWN
from app.models.analytics_settlement import (
    AggSettlementDaily,
    PaymentSettlement,
    SettlementMatchStatus,
)
from app.models.order_payment import OrderPayment, PaymentTxnStatus
from app.services.analytics.aggregation.base import JobRunResult, register
from app.services.analytics.aggregation.jobs import _bucket_window, _cap, _dim, _utcnow
from app.services.analytics.aggregation.jobs_finance import _replace_bucket
from app.services.analytics.contracts import from_minor, to_minor
from app.services.analytics.settlements import UNSETTLED_AFTER_DAYS

__all__ = ["SettlementDailyJob"]


def _empty() -> dict[str, Any]:
    """A gateway's counters for one bucket, both bases, all zero.

    Every key is present from the start so a gateway that appears in only one of
    the three queries still writes a complete row. A missing key would become a
    ``KeyError`` on the first day a gateway settles without transacting, which is
    every single day for a gateway that has stopped taking new payments.
    """
    return {
        # payment-dated
        "txns_transacted": 0,
        "payments_transacted": 0,
        "reversals_transacted": 0,
        "gross_transacted": 0,
        "fee_transacted": 0,
        "tax_transacted": 0,
        "net_transacted": 0,
        "matched_txns": 0,
        "matched_gross": 0,
        "unmatched_txns": 0,
        "unmatched_gross": 0,
        "unsettled_payments": 0,
        "unsettled_amount": 0,
        # settlement-dated
        "txns_settled": 0,
        "gross_settled": 0,
        "fee_settled": 0,
        "tax_settled": 0,
        "payout_amount": 0,
        "batches": set(),
    }


class SettlementDailyJob:
    """Settlement activity per store-local day x gateway.

    Idempotent by construction: it reads ``payment_settlements`` and
    ``order_payments``, neither of which it writes, deletes the bucket's scope
    and reinserts. Running it twice on the same bucket leaves the table
    identical.

    Bucket-scoped: it touches only ``bucket_date`` under ``tz_generation``. Note
    the consequence, which is real and is the reason the recompute queue exists —
    a settlement file uploaded today changes buckets from three days ago (the
    payment dates) *and* today's (the settlement date). ``IngestResult.dirty_dates``
    lists exactly which, so the caller can enqueue them rather than guessing.

    The tz_generation filter on ``payment_settlements`` is not decoration.
    ``payment_date`` and ``settlement_date`` on that table are derived from the
    stored instants under whichever generation ingested them; a bucket computed
    under generation 2 must not read rows still carrying generation 1's day
    boundaries, or a timezone change would quietly mix two definitions of "day".
    """

    name = "settlement_daily"

    def run(self, db: Session, bucket_date: date, tz_generation: int) -> JobRunResult:
        start, end, _tz = _bucket_window(db, bucket_date)
        warnings: list[str] = []
        buckets: dict[str, dict[str, Any]] = defaultdict(_empty)

        self._transacted(db, bucket_date, tz_generation, buckets, warnings)
        self._settled(db, bucket_date, tz_generation, buckets)
        self._unsettled(db, start, end, buckets)

        computed_at = _utcnow()
        rows = [
            {
                "bucket_date": bucket_date,
                "gateway": gateway,
                "tz_generation": tz_generation,
                "computed_at": computed_at,
                "txns_transacted": bucket["txns_transacted"],
                "payments_transacted": bucket["payments_transacted"],
                "reversals_transacted": bucket["reversals_transacted"],
                "gross_transacted": from_minor(bucket["gross_transacted"]),
                "fee_transacted": from_minor(bucket["fee_transacted"]),
                "tax_transacted": from_minor(bucket["tax_transacted"]),
                "net_transacted": from_minor(bucket["net_transacted"]),
                "matched_txns": bucket["matched_txns"],
                "matched_gross": from_minor(bucket["matched_gross"]),
                "unmatched_txns": bucket["unmatched_txns"],
                "unmatched_gross": from_minor(bucket["unmatched_gross"]),
                "unsettled_payments": bucket["unsettled_payments"],
                "unsettled_amount": from_minor(bucket["unsettled_amount"]),
                "txns_settled": bucket["txns_settled"],
                "settlement_batches": len(bucket["batches"]),
                "gross_settled": from_minor(bucket["gross_settled"]),
                "fee_settled": from_minor(bucket["fee_settled"]),
                "tax_settled": from_minor(bucket["tax_settled"]),
                "payout_amount": from_minor(bucket["payout_amount"]),
            }
            for gateway, bucket in sorted(buckets.items())
        ]

        deleted = _replace_bucket(
            db,
            AggSettlementDaily,
            rows,
            where=(
                AggSettlementDaily.bucket_date == bucket_date,
                AggSettlementDaily.tz_generation == tz_generation,
            ),
        )
        return JobRunResult(
            rows_written=len(rows), rows_deleted=deleted, warnings=_cap(warnings)
        )

    # -- payment-dated: what the gateway processed on this day -------------
    @staticmethod
    def _transacted(
        db: Session,
        bucket_date: date,
        tz_generation: int,
        buckets: dict[str, dict[str, Any]],
        warnings: list[str],
    ) -> None:
        """Lines whose ``payment_date`` is this bucket. **Fees live here.**

        Rows are read individually rather than pre-aggregated in SQL because the
        sign of ``gross_minor`` splits the population three ways at once —
        captures from reversals, matched from unmatched — and expressing that as
        CASE expressions over a GROUP BY makes a query nobody can check against
        the column docstrings. A day's settlement file is hundreds of lines, not
        millions.
        """
        rows = db.execute(
            select(
                PaymentSettlement.gateway,
                PaymentSettlement.gross_minor,
                PaymentSettlement.fee_minor,
                PaymentSettlement.tax_minor,
                PaymentSettlement.net_minor,
                PaymentSettlement.match_status,
                PaymentSettlement.transaction_id,
            ).where(
                PaymentSettlement.payment_date == bucket_date,
                PaymentSettlement.tz_generation == tz_generation,
            )
        ).all()

        for row in rows:
            bucket = buckets[_dim(row.gateway)[:40]]
            bucket["txns_transacted"] += 1
            if row.gross_minor < 0:
                bucket["reversals_transacted"] += 1
            else:
                bucket["payments_transacted"] += 1
            bucket["gross_transacted"] += int(row.gross_minor)
            bucket["fee_transacted"] += int(row.fee_minor)
            bucket["tax_transacted"] += int(row.tax_minor)
            bucket["net_transacted"] += int(row.net_minor)

            if row.match_status == SettlementMatchStatus.MATCHED:
                bucket["matched_txns"] += 1
                bucket["matched_gross"] += int(row.gross_minor)
            else:
                bucket["unmatched_txns"] += 1
                bucket["unmatched_gross"] += int(row.gross_minor)

            # Defence in depth. ck_payment_settlements_net enforces this per row
            # in the database, so reaching here means someone dropped the
            # constraint on the shared host — where the twin SQL is applied by
            # hand and a CHECK is exactly the sort of clause that gets edited out.
            expected = int(row.gross_minor) - int(row.fee_minor) - int(row.tax_minor)
            if int(row.net_minor) != expected:
                warnings.append(
                    f"settlement_net_identity_broken: {row.transaction_id} stores "
                    f"net {row.net_minor} but gross - fee - tax is {expected}; "
                    "the row was aggregated as stored and the payout total for "
                    "this bucket cannot be trusted"
                )

    # -- settlement-dated: what actually moved on this day ------------------
    @staticmethod
    def _settled(
        db: Session,
        bucket_date: date,
        tz_generation: int,
        buckets: dict[str, dict[str, Any]],
    ) -> None:
        """Lines whose ``settlement_date`` is this bucket. **Cash lives here.**

        A different population from ``_transacted`` over the same table, and the
        overlap on any real day is small: these are mostly the payments of two or
        three days ago. ``settlement_batches`` counts distinct settlement ids
        rather than rows, because the payout is the batch — twenty lines in one
        batch is one credit on the bank statement.
        """
        rows = db.execute(
            select(
                PaymentSettlement.gateway,
                PaymentSettlement.settlement_id,
                PaymentSettlement.gross_minor,
                PaymentSettlement.fee_minor,
                PaymentSettlement.tax_minor,
                PaymentSettlement.net_minor,
            ).where(
                PaymentSettlement.settlement_date == bucket_date,
                PaymentSettlement.tz_generation == tz_generation,
            )
        ).all()

        for row in rows:
            bucket = buckets[_dim(row.gateway)[:40]]
            bucket["txns_settled"] += 1
            bucket["gross_settled"] += int(row.gross_minor)
            bucket["fee_settled"] += int(row.fee_minor)
            bucket["tax_settled"] += int(row.tax_minor)
            bucket["payout_amount"] += int(row.net_minor)
            if row.settlement_id and row.settlement_id != DIMENSION_UNKNOWN:
                bucket["batches"].add(row.settlement_id)

    # -- the absence --------------------------------------------------------
    @staticmethod
    def _unsettled(
        db: Session,
        start: datetime,
        end: datetime,
        buckets: dict[str, dict[str, Any]],
    ) -> None:
        """Captures on this day that no settlement line explains, after a grace period.

        Payment-dated by definition: the capture happened on this day and the
        settlement did not happen at all, so there is no settlement date to file
        it under.

        Grace period. A bucket younger than :data:`UNSETTLED_AFTER_DAYS` reports
        zero here rather than flagging every capture as missing — the gateway has
        not had time to settle them yet, and a rollup that lit up red for three
        days after every sale would be switched off within a week. The number is
        stated once, in ``settlements.UNSETTLED_AFTER_DAYS``, and imported.

        The consequence is that this counter *changes* as a bucket ages: it is
        zero on the day, non-zero three days later if nothing settled, and back
        to zero once the report arrives. That is correct and it is why the
        recompute queue exists; a figure that froze on the day would answer a
        question nobody asked.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=UNSETTLED_AFTER_DAYS)
        window_end = min(_aware(end), cutoff)
        if window_end <= _aware(start):
            return

        settled = (
            select(PaymentSettlement.id)
            .where(
                PaymentSettlement.order_id == OrderPayment.order_id,
                PaymentSettlement.gross_minor > 0,
                PaymentSettlement.match_status == SettlementMatchStatus.MATCHED,
            )
            .correlate(OrderPayment)
            .exists()
        )
        rows = db.execute(
            select(
                OrderPayment.gateway,
                func.count(OrderPayment.id).label("legs"),
                func.coalesce(func.sum(OrderPayment.amount), 0).label("amount"),
            )
            .where(
                OrderPayment.payment_status == PaymentTxnStatus.PAID,
                OrderPayment.paid_at.isnot(None),
                OrderPayment.paid_at >= _aware(start),
                OrderPayment.paid_at < window_end,
                # A COD leg never touches a gateway and can never appear in any
                # settlement report. Counting it here would report a permanent,
                # growing gap that no action could ever close.
                OrderPayment.gateway.isnot(None),
                ~settled,
            )
            .group_by(OrderPayment.gateway)
        ).all()

        for row in rows:
            bucket = buckets[_dim(row.gateway)[:40]]
            bucket["unsettled_payments"] += int(row.legs or 0)
            bucket["unsettled_amount"] += to_minor(row.amount)


def _aware(moment: datetime) -> datetime:
    """UTC-aware, so a naive bound and an aware `now()` can be compared.

    ``_bucket_window`` returns aware instants and ``datetime.now(timezone.utc)``
    is aware, but a caller passing a naive bound would otherwise raise deep
    inside a ``min()``. Mirrors ``jobs._as_utc``.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


register(SettlementDailyJob())
