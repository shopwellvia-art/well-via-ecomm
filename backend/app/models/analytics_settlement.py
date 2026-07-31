"""Gateway settlement records — what the gateway actually paid out, per line.

Everything else in the analytics schema describes what a *customer was charged*.
This module is the first that describes what the *gateway actually paid us*, and
the two are not the same number: between them sit the MDR, the tax on the MDR,
refunds netted off, chargebacks, and two to three days of float.

Why this exists
===============
``app/models/analytics_facts.py`` states it plainly: *"No gateway fee is stored
anywhere in this schema — not on ``orders``, not on ``order_payments``, not on
``payment_events``."* So every gateway fee this system reports today is
``ESTIMATED`` — the output of a ``costs.gateway_fee_pct`` cost rule applied to
prepaid order value. A cost rule is a forecast. A settlement report is a
measurement. These two tables are where the measurement lands, so that a fee
which was matched to a real settlement line can be labelled ``ACTUAL``
(:class:`app.services.analytics.types.MetricQuality`) and one that was not stays
``ESTIMATED`` and says so.

Two tables, two jobs
====================
``payment_settlements``
    One row per settlement *line* exactly as the gateway reported it: a captured
    payment, a refund, a chargeback, an adjustment. Immutable facts about an
    external system, upserted on the gateway's own transaction id so re-uploading
    the same report is a no-op.

``agg_settlement_daily``
    The daily rollup the reconciliation view reads, one row per
    ``(bucket_date, gateway, tz_generation)``.

Money is integer paise here, and that is a deliberate departure
===============================================================
Every other analytics table stores money as ``DECIMAL(14,2)`` (see
``analytics_base.MONEY``). ``payment_settlements`` stores **signed integer
paise** in ``BigInteger`` columns instead, for one reason: this is the only table
in the schema whose whole purpose is an equality against a third party's
arithmetic. ``gross - fee - tax = net`` has to hold *to the paisa*, and a
settlement that reconciles to the rupee but not the paisa has not reconciled.
Integers make that a property of the column type rather than something asserted
afterwards — and it is asserted anyway, by ``ck_payment_settlements_net``, in the
database, so a bad row cannot be inserted by any code path including a hand-run
``INSERT``.

The rollup goes back to ``DECIMAL(14,2)`` because it is read by the same
resolvers as every other ``agg_*`` table and must not be the one that needs
special handling. The conversion happens exactly once, at the rollup write, via
``contracts.from_minor``.

Signs are real and are never normalised away
=============================================
``gross_minor``, ``fee_minor``, ``tax_minor`` and ``net_minor`` are all SIGNED.
A refund is a negative gross. A chargeback is a negative gross with a *positive*
fee (the gateway charges for the dispute), so its net is more negative than its
gross. Storing magnitudes plus a type flag would put the sign in two places and
guarantee they eventually disagree; the arithmetic identity above is the check
that keeps the single stored sign honest.

Two dates, and they are not interchangeable
============================================
A payment captured on the 30th settles on the 2nd. Both instants are recorded:

* ``transacted_at`` / ``payment_date`` — when the gateway created the
  transaction. **The fee belongs to this day**, because it is a cost of that
  sale and must land in the same bucket as the revenue it was charged against.
* ``settled_at`` / ``settlement_date`` — when the money actually moved to the
  bank. **The cash belongs to this day.** NULL while the line is on hold.

Conflating them is the classic error in settlement reporting and it misstates
both figures at once: it moves fees into a bucket with no matching revenue and
reports cash on a day the bank saw nothing. ``agg_settlement_daily`` therefore
carries two families of columns, suffixed ``_transacted`` and ``_settled``, over
two different populations. They are never summed together.

No foreign keys, per the convention in ``analytics_base``
==========================================================
``order_id`` and ``order_payment_id`` are plain integers. A settlement line is a
record of something that happened at a third party; it must survive an order
being deleted, and the table must stay independently truncatable and re-ingestable
from the source file.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.analytics_base import (
    BigIDMixin,
    CreatedAtMixin,
    DIMENSION_UNKNOWN,
    RollupMixin,
    count_column,
    dimension_column,
    money_column,
)
from app.models.base import Base

__all__ = [
    "PaymentSettlement",
    "AggSettlementDaily",
    "SettlementTxnType",
    "SettlementMatchStatus",
    "SettlementMatchKey",
    "SettlementSource",
]


class SettlementTxnType:
    """What kind of line this is, normalised across gateways.

    Plain strings rather than a DB enum, for the same reason
    ``payment_events.event_type`` is a varchar: a gateway inventing a new
    transaction type must not require a schema migration before its report can
    be ingested. ``OTHER`` is the honest landing place for one we do not
    recognise — the row is still stored, still carries its money, and still
    reconciles.
    """

    PAYMENT = "payment"
    REFUND = "refund"
    CHARGEBACK = "chargeback"
    ADJUSTMENT = "adjustment"
    TRANSFER = "transfer"
    OTHER = "other"


class SettlementMatchStatus:
    """Whether this line could be tied to a payment we have a record of.

    ``UNMATCHED`` is a finding, not an error, and is the single most valuable
    thing this table produces: a line the gateway paid us for that our own
    records cannot account for. It is stored with its full money and dates
    intact — never dropped, never zeroed — so the reconciliation view can list
    it.

    ``AMBIGUOUS`` means the join key resolved to more than one of our payments.
    That is deliberately NOT collapsed into ``MATCHED`` by picking one: a
    settlement attributed to the wrong order is worse than one attributed to no
    order, because it looks resolved.
    """

    MATCHED = "matched"
    UNMATCHED = "unmatched"
    AMBIGUOUS = "ambiguous"


class SettlementMatchKey:
    """Which identifier actually produced the match.

    Stored per row because the keys are not equally strong and a reconciliation
    that cannot say *how* it matched cannot be audited. See
    ``app/services/analytics/settlements.py`` for what each one is worth in this
    deployment — in particular why ``MERCHANT_TXN_ID`` is the primary key here
    and ``GATEWAY_PAYMENT_ID`` almost never fires.
    """

    #: The gateway's own captured-payment id (``pay_...``) against one of our
    #: stored provider refs.
    GATEWAY_PAYMENT_ID = "gateway_payment_id"
    #: Our merchant transaction id (``ORD`` + 24 hex), which we send to Razorpay
    #: as the payment link's ``reference_id``, against ``orders.payment_intent_id``.
    MERCHANT_TXN_ID = "merchant_txn_id"
    #: A Razorpay payment-link id (``plink_...``) against the provider refs we
    #: store for the Payment Links flow.
    PAYMENT_LINK_ID = "payment_link_id"
    #: A refund reference (``rfnd_...`` or our deterministic ``RFNDORD...``)
    #: against ``returns.refund_reference`` / a REFUND_ATTEMPT payment event.
    REFUND_REFERENCE = "refund_reference"
    #: Nothing matched. Paired with ``SettlementMatchStatus.UNMATCHED``.
    NONE = DIMENSION_UNKNOWN


class SettlementSource:
    """How the row got here.

    ``CSV_UPLOAD`` is the path that works today: a finance person downloads the
    settlement report and uploads it. ``API`` exists as a value so that rows
    ingested by a future settlements-API client are distinguishable from
    hand-uploaded ones — provenance a reconciliation needs and cannot recover
    later. Nothing writes ``API`` yet; see ``settlements.RazorpaySettlementApiClient``,
    which raises rather than pretending.
    """

    CSV_UPLOAD = "csv_upload"
    API = "api"


class PaymentSettlement(Base, BigIDMixin, CreatedAtMixin):
    """One line of a gateway settlement report, exactly as reported.

    Idempotency
    -----------
    ``UNIQUE (gateway, transaction_id)`` is what makes re-uploading the same file
    a no-op instead of a doubling, and finance *will* upload the same file twice.
    The key is the gateway's own transaction id and deliberately does **not**
    include ``settlement_id``: Razorpay reports a captured payment that is still
    on hold with an empty settlement id, then reports the *same* transaction
    again once it settles. Keying on both would store that payment twice and
    double-count its fee. Keying on the transaction alone lets the second report
    update the first in place, which is what actually happened.

    The cost of that choice is stated rather than hidden: a transaction genuinely
    split across two settlement batches collapses to the later batch. Razorpay
    does not split a payment across batches, so this is a theoretical loss here;
    a gateway that does would need ``settlement_id`` in the key and a different
    on-hold story.

    ``tz_generation`` and the two derived dates
    -------------------------------------------
    ``transacted_at`` / ``settled_at`` are the authoritative instants, stored as
    naive UTC like every other timestamp in this stack. ``payment_date`` and
    ``settlement_date`` are the store-local reporting days derived from them, and
    ``tz_generation`` records which timezone generation did the deriving. They are
    recomputed on every ingest, so a store that changes its reporting timezone
    fixes them by re-uploading rather than by a migration — and until it does,
    ``tz_generation`` makes the staleness visible instead of silent.

    ``raw`` keeps the parsed source row
    -----------------------------------
    A settlement dispute is argued months later against the file the gateway
    sent, not against our interpretation of it. ``raw`` is the row as parsed,
    verbatim, so a column we mapped wrongly can be re-derived without asking
    finance to find the file again. It carries no credentials — a settlement
    report has none.
    """

    __tablename__ = "payment_settlements"

    #: Gateway code, matching ``payment_methods.gateway_code`` (``'razorpay'``).
    #: NOT NULL with the ``'-'`` sentinel because it is in the UNIQUE key and
    #: MySQL permits unlimited NULLs under one.
    gateway: Mapped[str] = dimension_column(40)

    #: The gateway's own id for this line (``pay_...``, ``rfnd_...``, an
    #: adjustment id). The other half of the idempotency key.
    transaction_id: Mapped[str] = mapped_column(String(128), nullable=False)

    #: One of :class:`SettlementTxnType`. Never NULL — an unrecognised type
    #: becomes ``'other'`` rather than a NULL that every later query must handle.
    transaction_type: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=SettlementTxnType.OTHER
    )

    #: The payout batch this line was paid in (``setl_...``). ``'-'`` while the
    #: line is captured but not yet settled — a real, meaningful state, not
    #: missing data.
    settlement_id: Mapped[str] = dimension_column(64)

    #: Bank UTR of the payout, when the report carries one. ``'-'`` otherwise.
    #: This is the string a finance person matches against the bank statement,
    #: which is the step this table does NOT perform — see view 69.
    settlement_utr: Mapped[str] = dimension_column(64)

    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default="INR"
    )

    #: Instrument the gateway reported (``upi``, ``card``, ``netbanking``).
    method: Mapped[str] = dimension_column(32)

    # -- money, signed integer paise ------------------------------------
    #: Transaction amount. NEGATIVE for a refund or chargeback.
    gross_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    #: Gateway fee (MDR) deducted from this line. Positive on a payment; also
    #: positive on a chargeback, where the gateway charges for the dispute.
    fee_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    #: Tax charged on the fee (GST on MDR). Separate from the fee because it is
    #: separately claimable and separately reported.
    tax_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    #: What this line contributed to the payout. Enforced by
    #: ``ck_payment_settlements_net`` to equal ``gross - fee - tax`` exactly.
    net_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )

    # -- the two dates ---------------------------------------------------
    #: When the gateway created the transaction. Naive UTC.
    transacted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    #: When the payout reached the bank. NULL while on hold — and NULL is right
    #: here: "not yet settled" is not "settled on no day".
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    tz_generation: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="1"
    )
    #: Store-local reporting day of ``transacted_at``. **Fees bucket here.**
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: Store-local reporting day of ``settled_at``. **Cash buckets here.**
    settlement_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # -- identifiers carried by the report -------------------------------
    gateway_payment_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    gateway_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: Whatever free-text reference the report carried (``order_receipt``,
    #: ``description``, ``notes``). This is where our merchant transaction id
    #: hides, so it is stored whole rather than only the token we extracted.
    gateway_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: The ``ORD``-prefixed merchant transaction id extracted from the above, if
    #: any. Stored separately because it is the join key that actually works.
    merchant_transaction_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    # -- matching --------------------------------------------------------
    match_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=SettlementMatchStatus.UNMATCHED
    )
    match_key: Mapped[str] = dimension_column(32)
    #: Our order, when matched. Plain integer, no FK (see module docstring).
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The specific ``order_payments`` leg, when the key resolved that far.
    order_payment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # -- provenance ------------------------------------------------------
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=SettlementSource.CSV_UPLOAD
    )
    #: Name of the uploaded file, or a label for an API pull. ``'-'`` if unnamed.
    source_file: Mapped[str] = dimension_column(255)
    #: 1-based data row number within that file, so a disputed figure can be
    #: pointed at in the original.
    source_row: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    #: The parsed source row, verbatim.
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "gateway", "transaction_id", name="uq_payment_settlements_txn"
        ),
        # The identity this table exists to preserve, enforced by the database so
        # no code path — including a hand-run INSERT during an incident — can
        # store a line whose arithmetic does not close.
        CheckConstraint(
            "net_minor = gross_minor - fee_minor - tax_minor",
            name="ck_payment_settlements_net",
        ),
        Index("ix_payment_settlements_payment_date", "payment_date", "gateway"),
        Index("ix_payment_settlements_settlement_date", "settlement_date", "gateway"),
        Index("ix_payment_settlements_match_status", "match_status", "payment_date"),
        Index("ix_payment_settlements_order_id", "order_id"),
        Index("ix_payment_settlements_settlement_id", "settlement_id"),
    )


class AggSettlementDaily(Base, BigIDMixin, RollupMixin):
    """Settlement activity per store-local day x gateway.

    Two populations in one row, and they must never be added together
    ==================================================================
    Columns suffixed ``_transacted`` describe the lines whose **payment_date**
    is this bucket: the sales the gateway processed today and the fee it charged
    for them. Columns suffixed ``_settled`` describe the lines whose
    **settlement_date** is this bucket: the money that actually reached the bank
    today, most of which is for sales made two or three days ago.

    On any real day the two populations overlap barely at all. Summing
    ``gross_transacted`` and ``gross_settled`` counts most transactions twice and
    is never a meaningful figure; the suffixes exist so that a query which does
    it is obviously wrong on sight.

    Which one to use:

    * *"What did payments cost us in July?"* — ``fee_transacted`` +
      ``tax_transacted``. This is the ``ACTUAL`` replacement for the estimated
      gateway-fee cost rule and lines up with July's revenue.
    * *"How much money arrived in July?"* — ``payout_amount``. This is a cash
      movement out of the gateway, which is still **not** a bank balance; see
      the note on view 69 below.

    Reconciliation counters, payment-dated
    ---------------------------------------
    ``matched_txns`` / ``unmatched_txns`` split this day's settlement lines by
    whether they could be tied to a payment we recorded. ``unsettled_payments``
    is the mirror-image gap: payments we captured on this day that no settlement
    line has yet accounted for, after a grace period long enough for the
    gateway's own T+2/T+3 cycle. Both directions are stored because they mean
    completely different things — the first is money we received and cannot
    explain, the second is money we believe we are owed.

    These are counts and amounts, never rates. ``matched_txns /
    (matched_txns + unmatched_txns)`` is computed at query time, because a stored
    percentage cannot be re-aggregated to a week.

    This table does not make view 69 (Cash Flow) LIVE
    --------------------------------------------------
    ``payout_amount`` is what the *gateway* says it sent. Only a bank feed says
    money arrived, and a payout can be reversed, held, or netted against a
    negative balance after the report was generated. The registry's decision —
    "settlement-derived cash is not cash" — survives this table intact, and the
    column is named ``payout_amount`` rather than ``cash_in`` for exactly that
    reason.
    """

    __tablename__ = "agg_settlement_daily"

    gateway: Mapped[str] = dimension_column(40)

    # -- payment-dated: lines TRANSACTED in this bucket -------------------
    #: Every settlement line whose payment_date is this bucket.
    txns_transacted: Mapped[int] = count_column()
    #: Positive-gross lines (captures). Denominator for match rate.
    payments_transacted: Mapped[int] = count_column()
    #: Negative-gross lines (refunds, chargebacks). Counted separately because
    #: a day of many reversals and a day of few large ones read identically in
    #: the money column alone.
    reversals_transacted: Mapped[int] = count_column()

    #: SIGNED sum of gross for this day's lines: captures minus reversals.
    gross_transacted: Mapped[Decimal] = money_column()
    #: **The observed gateway fee for this day.** ACTUAL, not estimated.
    fee_transacted: Mapped[Decimal] = money_column()
    #: Tax charged on that fee.
    tax_transacted: Mapped[Decimal] = money_column()
    #: ``gross_transacted - fee_transacted - tax_transacted``.
    net_transacted: Mapped[Decimal] = money_column()

    matched_txns: Mapped[int] = count_column()
    matched_gross: Mapped[Decimal] = money_column()
    #: Settlement lines with no payment of ours behind them. The finding.
    unmatched_txns: Mapped[int] = count_column()
    unmatched_gross: Mapped[Decimal] = money_column()

    #: Payments we captured on this day that no settlement line explains, once
    #: the gateway's settlement cycle has had time to run. The other finding.
    unsettled_payments: Mapped[int] = count_column()
    unsettled_amount: Mapped[Decimal] = money_column()

    # -- settlement-dated: cash that MOVED in this bucket ------------------
    txns_settled: Mapped[int] = count_column()
    #: Distinct payout batches credited on this day. A day usually has one.
    settlement_batches: Mapped[int] = count_column()
    gross_settled: Mapped[Decimal] = money_column()
    fee_settled: Mapped[Decimal] = money_column()
    tax_settled: Mapped[Decimal] = money_column()
    #: What the gateway says it paid out on this day. Not a bank balance.
    payout_amount: Mapped[Decimal] = money_column()

    __table_args__ = (
        UniqueConstraint(
            "bucket_date", "gateway", "tz_generation", name="uq_agg_settlement_daily_key"
        ),
        Index("ix_agg_settlement_daily_bucket_date_gateway", "bucket_date", "gateway"),
    )
