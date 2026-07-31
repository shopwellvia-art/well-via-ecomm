"""Settlement ingest, matching and reconciliation.

This module turns a gateway settlement report into rows in
``payment_settlements``, ties each line to the payment it belongs to, and reports
the two gaps that matter: lines the gateway paid us that we cannot explain, and
payments we captured that the gateway has not paid us for.

The join key, established from the real data
============================================
Matching is the whole problem here, so this is the finding the rest of the module
is built on rather than an implementation detail.

**This deployment never stores a Razorpay ``pay_...`` id.** The integration is
built on the Payment Links API (``app/integrations/payments/razorpay.py``), so
the "provider ref" written at checkout and at settlement is a payment-*link* id,
``plink_...``:

* ``orders.payment_provider_ref``      <- ``plink_...``
* ``order_payments.gateway_order_id``  <- ``plink_...``
* ``order_payments.gateway_payment_id``<- ``plink_...`` (the same string again)

The codebase already knows this: ``RazorpayProvider._resolve_captured_payment_id``
has to make two live API calls at refund time purely to discover the ``pay_...``
id, because it was never recorded. A settlement report, meanwhile, is keyed on
``pay_...`` / ``rfnd_...`` and carries the Razorpay ``order_...`` — none of which
exist as a column in this database. So the obvious join does not exist.

What *does* exist is the merchant transaction id. ``payment_service`` mints
``"ORD" + 24 uppercase hex`` and:

* stores it in ``orders.payment_intent_id``, which is **UNIQUE**, and
* sends it to Razorpay as the payment link's ``reference_id``.

Razorpay propagates that into the underlying order's receipt, which is what
surfaces in the settlement report's ``order_receipt`` / ``description`` /
``notes`` columns. So:

    **The primary join key is the merchant transaction id (``ORD`` + 24 hex),
    scanned out of whichever free-text column of the settlement row carries it,
    resolved against the UNIQUE ``orders.payment_intent_id``.**

:data:`MATCH_ORDER` lists the full cascade — ``pay_...`` first because it is
exact when it appears at all, then the merchant transaction id, then
``plink_...``, then refund references. Each row records which key fired in
``payment_settlements.match_key``, so a reconciliation can be audited by
strength of evidence rather than taken on trust.

Because the primary key is a string the gateway echoes back rather than one it
owns, matching is *expected* to be imperfect. That is designed for, not
apologised for: an unmatched line is stored with its money and dates intact and
surfaces as a finding.

Two ingest paths, and only one of them works today
==================================================
**CSV upload** is primary. It needs no credentials, it is what a finance person
can do this afternoon, and it is the only path implemented here. The parser is
defensive on purpose — header aliases, currency symbols, thousands separators,
bracketed negatives, epoch or ISO timestamps — because a settlement report is a
human-facing export whose column names change without notice.

**The settlements API is not implemented.** :class:`RazorpaySettlementApiClient`
exists so the shape is agreed, and it *raises*. It does not return an empty list:
a client that silently returns nothing would make "the API is not wired up"
indistinguishable from "there were no settlements", which is precisely the
confusion this whole subsystem exists to prevent. Razorpay is on test keys here
and no live endpoint is called from this module by any code path.

What a rejected file does
=========================
Nothing. The parser validates every row and collects every problem before a
single ``INSERT`` is issued, so a file with one bad amount on row 40 writes zero
rows rather than 39. A partial ingest is worse than a rejected one: it looks like
a successful upload, it reconciles against nothing, and the missing rows are
invisible until someone chases a number months later.

Money
=====
Integer paise throughout, via ``contracts.to_minor``, which refuses ``float``.
Amounts are never parsed through ``float()`` at any point — ``Decimal`` from the
raw string, straight to ``int``. A major-unit amount with more than two decimal
places is a *parse error*, not something to round: sub-paisa precision in a
settlement report means the column is not what we think it is.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.models.analytics_settlement import (
    PaymentSettlement,
    SettlementMatchKey,
    SettlementMatchStatus,
    SettlementSource,
    SettlementTxnType,
)
from app.models.order import Order
from app.models.order_payment import OrderPayment, PaymentTxnStatus
from app.models.payment_event import PaymentEvent, PaymentEventType
from app.models.return_request import ReturnRequest
from app.services.analytics.contracts import from_minor, to_minor
from app.services.analytics.reconciliation import (
    MAX_REPORTED_IDS,
    CheckStatus,
)
from app.services.analytics.timebox import (
    DEFAULT_TIMEZONE,
    active_generation,
    local_day,
    range_bounds_utc,
    store_timezone,
)
from app.services.analytics.types import MetricQuality

__all__ = [
    "DEFAULT_GATEWAY",
    "UNSETTLED_AFTER_DAYS",
    "MATCH_ORDER",
    "AmountUnit",
    "SettlementIngestError",
    "SettlementApiNotImplemented",
    "ParsedSettlementRow",
    "IngestResult",
    "ObservedFee",
    "SettlementCheck",
    "SettlementReconciliation",
    "parse_settlement_csv",
    "ingest_settlement_csv",
    "unmatched_settlements",
    "unsettled_captured_payments",
    "observed_gateway_fee",
    "settlement_reconciliation",
    "RazorpaySettlementApiClient",
    "api_availability",
]

log = logging.getLogger("analytics.settlements")

#: The only gateway this deployment settles through. Not hardcoded into the
#: parser — every public function takes a ``gateway`` — but it is the default so
#: an admin uploading a Razorpay report does not have to know the code.
DEFAULT_GATEWAY = "razorpay"

#: How long a captured payment may go unsettled before it is a finding.
#:
#: Razorpay's standard cycle is T+2 working days; a capture on Friday can
#: legitimately settle on Wednesday. Five days covers a weekend plus one public
#: holiday. Shorter and every Monday morning reports a false gap; much longer and
#: a genuinely stuck payout hides for a week. This is a judgement, it is the only
#: place it is written down, and it is a parameter on every function that uses it.
UNSETTLED_AFTER_DAYS = 5

#: Merchant transaction id minted by ``payment_service._new_mtid``: "ORD" plus
#: 24 uppercase hex characters. Anchored to a word boundary so it can be pulled
#: out of a free-text description without matching a longer accidental string.
_MTID_PATTERN = re.compile(r"\bORD[0-9A-F]{24}\b", re.IGNORECASE)

_PAY_ID_PATTERN = re.compile(r"\bpay_[A-Za-z0-9]+\b")
_PLINK_ID_PATTERN = re.compile(r"\bplink_[A-Za-z0-9]+\b")
_RFND_ID_PATTERN = re.compile(r"\brfnd_[A-Za-z0-9]+\b")
#: Our own deterministic manual-refund references, minted by
#: ``order_service`` (``RFNDORD{order}-{leg}``) and ``return_service``
#: (``RFND{return}-{hex}``). Both are stamped into Razorpay's ``receipt`` and
#: ``notes.reference`` at refund time, so both can come back in a report.
_OUR_REFUND_REF_PATTERN = re.compile(r"\bRFND(?:ORD)?[0-9A-Za-z]+(?:-[0-9A-Za-z]+)?\b")

#: The cascade, strongest evidence first. Each entry is
#: ``(match_key, extractor)``; the first key that resolves to exactly one of our
#: payments wins, and its name is stored on the row.
MATCH_ORDER: tuple[str, ...] = (
    SettlementMatchKey.GATEWAY_PAYMENT_ID,
    SettlementMatchKey.MERCHANT_TXN_ID,
    SettlementMatchKey.PAYMENT_LINK_ID,
    SettlementMatchKey.REFUND_REFERENCE,
)


class AmountUnit:
    """Whether the report's money columns are rupees or paise.

    **Never guessed.** The Razorpay dashboard's settlement export is in major
    units with two decimals; the settlements API returns minor units. Getting it
    wrong is a factor-of-100 error that reconciles perfectly against itself and
    is invisible in every internal check, so the caller must say which it has and
    the parser refuses to infer it from the values. Guessing "if it has a decimal
    point it must be rupees" fails on the first whole-rupee amount.
    """

    MAJOR = "major"
    MINOR = "minor"

    ALL = (MAJOR, MINOR)


# ===========================================================================
# Errors
# ===========================================================================


class SettlementIngestError(ValueError):
    """The file could not be ingested, and nothing was written.

    Carries **every** problem found, not just the first. A finance person
    re-exporting a report should learn about all four bad rows in one pass
    rather than discovering them one upload at a time.
    """

    def __init__(self, summary: str, problems: Sequence[str] = ()) -> None:
        self.problems: tuple[str, ...] = tuple(problems)
        detail = summary
        if self.problems:
            shown = list(self.problems[:MAX_PROBLEMS_REPORTED])
            if len(self.problems) > MAX_PROBLEMS_REPORTED:
                shown.append(
                    f"... and {len(self.problems) - MAX_PROBLEMS_REPORTED} "
                    "further problem(s)"
                )
            detail = f"{summary} Nothing was ingested. Problems: " + "; ".join(shown)
        super().__init__(detail)


class SettlementApiNotImplemented(NotImplementedError):
    """The gateway settlements API client does not exist in this deployment.

    Deliberately an exception rather than an empty result. A stub returning
    ``[]`` would make "we never built this" look exactly like "the gateway
    settled nothing", and the second of those is a legitimate business fact that
    an operator would act on.
    """


#: Problems listed verbatim in the exception message before it is summarised. A
#: malformed 50 000-row export would otherwise produce an error nobody can read.
MAX_PROBLEMS_REPORTED = 12


# ===========================================================================
# CSV parsing
# ===========================================================================
#
# Header handling. A settlement report is a human-facing export: columns get
# renamed, reordered, and added between gateway releases, and an ingest that
# breaks on a reordered column is an ingest finance stops using. So headers are
# normalised (case, punctuation and spacing folded away) and resolved through an
# alias table; unknown columns are kept in `raw` and otherwise ignored.

_CANONICAL_ALIASES: dict[str, tuple[str, ...]] = {
    "transaction_id": ("transaction_id", "entity_id", "id", "txn_id"),
    "transaction_type": ("type", "transaction_type", "entity_type", "txn_type"),
    "credit": ("credit", "credit_amount"),
    "debit": ("debit", "debit_amount"),
    "amount": ("amount", "transaction_amount", "gross", "gross_amount"),
    "fee": ("fee", "fees", "mdr", "gateway_fee", "commission"),
    "tax": ("tax", "gst", "tax_on_fee", "service_tax", "fee_tax"),
    "net": ("net", "net_amount", "settlement_amount", "settled_amount"),
    "currency": ("currency", "currency_code"),
    "transacted_at": (
        "created_at",
        "transaction_date",
        "payment_date",
        "transacted_at",
        "date",
    ),
    "settled_at": ("settled_at", "settlement_date", "settled_on", "payout_date"),
    "settlement_id": ("settlement_id", "settlement", "batch_id", "payout_id"),
    "settlement_utr": ("settlement_utr", "utr", "bank_reference", "rrn"),
    "gateway_payment_id": ("payment_id", "gateway_payment_id"),
    "gateway_order_id": ("order_id", "gateway_order_id"),
    "gateway_reference": (
        "order_receipt",
        "receipt",
        "reference_id",
        "merchant_reference",
        "description",
        "notes",
    ),
    "method": ("method", "payment_method", "instrument"),
}

#: Header -> canonical, built once. Later aliases never override earlier ones, so
#: a report carrying both ``order_receipt`` and ``description`` resolves the
#: reference from the more specific column.
_HEADER_MAP: dict[str, str] = {
    alias: canonical
    for canonical, aliases in _CANONICAL_ALIASES.items()
    for alias in aliases
}

#: Columns without which the file is not a settlement report. ``fee`` and ``tax``
#: are required rather than defaulted to zero: a report with no fee column is a
#: different report, and silently ingesting it as "this gateway charged us
#: nothing" would replace an ESTIMATED fee with a confident, wrong, ACTUAL zero —
#: the single worst outcome this module could produce.
_REQUIRED_COLUMNS: tuple[str, ...] = (
    "transaction_id",
    "transaction_type",
    "fee",
    "tax",
    "transacted_at",
)

#: Transaction types that move money OUT. Used only when the report has a single
#: signed-by-convention ``amount`` column instead of ``credit``/``debit``.
_REVERSAL_TYPES: frozenset[str] = frozenset(
    {"refund", "chargeback", "dispute", "reversal", "settlement_reversal"}
)
#: Types that move money IN, under the same circumstances.
_CREDIT_TYPES: frozenset[str] = frozenset({"payment", "capture", "sale", "transfer"})

#: Raw report type -> the normalised :class:`SettlementTxnType`.
_TYPE_MAP: dict[str, str] = {
    "payment": SettlementTxnType.PAYMENT,
    "capture": SettlementTxnType.PAYMENT,
    "sale": SettlementTxnType.PAYMENT,
    "refund": SettlementTxnType.REFUND,
    "chargeback": SettlementTxnType.CHARGEBACK,
    "dispute": SettlementTxnType.CHARGEBACK,
    "adjustment": SettlementTxnType.ADJUSTMENT,
    "transfer": SettlementTxnType.TRANSFER,
    "reversal": SettlementTxnType.REFUND,
    "settlement_reversal": SettlementTxnType.REFUND,
}

#: Cell values that mean "no value", as opposed to zero. Distinguished because a
#: blank fee column and a zero fee column say different things — but both parse
#: to 0 paise, so the difference is preserved only in ``raw``.
_BLANKS: frozenset[str] = frozenset({"", "-", "na", "n/a", "null", "none", "nil"})

_CURRENCY_WORDS = re.compile(r"(?i)\b(?:rs|inr|usd|rupees)\b\.?")
_CURRENCY_SYMBOLS = re.compile(r"[₹$€£,\s]")


def _normalise_header(raw: str) -> str:
    """Fold a header to a comparable token.

    Strips the UTF-8 BOM Excel prepends to the first header of a re-saved CSV,
    which is otherwise invisible and turns ``entity_id`` into an unrecognised
    column on a file that looks identical to one that worked.
    """
    text = raw.replace("﻿", "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


@dataclass(frozen=True)
class ParsedSettlementRow:
    """One validated settlement line, in integer paise, before it is matched.

    Deliberately separate from the ORM object: parsing must be provably complete
    and correct before anything touches the database, and a dataclass is what
    lets ``parse_settlement_csv`` be tested without one.
    """

    source_row: int
    transaction_id: str
    transaction_type: str
    settlement_id: str
    settlement_utr: str
    currency: str
    method: str
    gross_minor: int
    fee_minor: int
    tax_minor: int
    transacted_at: datetime
    settled_at: datetime | None
    gateway_payment_id: str | None
    gateway_order_id: str | None
    gateway_reference: str | None
    merchant_transaction_id: str | None
    raw: dict[str, str]

    @property
    def net_minor(self) -> int:
        """``gross - fee - tax``, to the paisa. The identity the DB re-checks."""
        return self.gross_minor - self.fee_minor - self.tax_minor

    @property
    def is_reversal(self) -> bool:
        return self.gross_minor < 0


def parse_settlement_csv(
    text: str,
    *,
    amount_unit: str,
    gateway: str = DEFAULT_GATEWAY,
    tz: ZoneInfo | None = None,
) -> tuple[ParsedSettlementRow, ...]:
    """Parse a settlement report, or raise having produced nothing.

    ``amount_unit`` is required and must be one of :class:`AmountUnit`. See that
    class for why it is never inferred.

    ``tz`` is the zone a *naive* timestamp in the file is expressed in — the
    gateway dashboard exports in the store's own timezone. It is threaded in
    rather than read from a session so this function stays pure and testable
    without a database. Defaults to the same IST fallback ``timebox`` uses, and
    :func:`ingest_settlement_csv` always passes the configured store timezone.

    Every row is validated; every problem is collected. The function either
    returns a complete set of rows or raises :class:`SettlementIngestError`
    listing what is wrong with the file. There is no partial return, because a
    caller handed 39 of 40 rows has no way to tell that it happened.
    """
    zone = tz or ZoneInfo(DEFAULT_TIMEZONE)
    if amount_unit not in AmountUnit.ALL:
        raise SettlementIngestError(
            f"amount_unit must be one of {list(AmountUnit.ALL)}, got "
            f"{amount_unit!r}. It is never inferred: reading paise as rupees is "
            "a 100x error that reconciles perfectly against itself."
        )
    if not (text or "").strip():
        raise SettlementIngestError("The uploaded settlement file is empty.")

    reader = csv.reader(io.StringIO(text))
    try:
        header_row = next(reader)
    except StopIteration:  # pragma: no cover - guarded by the emptiness check
        raise SettlementIngestError("The uploaded settlement file has no header row.")

    headers = [_normalise_header(h) for h in header_row]
    # Resolved by ALIAS PRIORITY, not by column order: a report carrying both
    # `order_receipt` and `description` must take the reference from the more
    # specific one whichever way round the exporter put them, or the join key
    # silently changes when a gateway reorders its columns.
    by_header: dict[str, int] = {}
    for position, header in enumerate(headers):
        by_header.setdefault(header, position)
    columns: dict[str, int] = {}
    for canonical, aliases in _CANONICAL_ALIASES.items():
        for alias in aliases:
            if alias in by_header:
                columns[canonical] = by_header[alias]
                break

    missing = [c for c in _REQUIRED_COLUMNS if c not in columns]
    has_amount = "amount" in columns or ("credit" in columns and "debit" in columns)
    if missing or not has_amount:
        wanted = list(missing)
        if not has_amount:
            wanted.append("amount (or the credit/debit pair)")
        raise SettlementIngestError(
            "The file does not look like a settlement report: required column(s) "
            f"{wanted} are absent. Columns found: {sorted(set(headers))}. "
            "Nothing was ingested."
        )

    rows: list[ParsedSettlementRow] = []
    problems: list[str] = []
    seen: dict[str, int] = {}

    for offset, raw_row in enumerate(reader, start=1):
        if not any((cell or "").strip() for cell in raw_row):
            continue  # a trailing blank line is not a problem worth reporting
        record = _row_record(headers, raw_row)
        try:
            parsed = _parse_row(
                record, columns, raw_row, offset, amount_unit=amount_unit, tz=zone
            )
        except _RowProblem as problem:
            problems.append(str(problem))
            continue
        previous = seen.get(parsed.transaction_id)
        if previous is not None:
            problems.append(
                f"row {offset}: transaction id {parsed.transaction_id!r} already "
                f"appeared on row {previous}. A settlement report must not list "
                "the same transaction twice; ingesting it would make the "
                "idempotency key arbitrary about which figures survive"
            )
            continue
        seen[parsed.transaction_id] = offset
        rows.append(parsed)

    if problems:
        raise SettlementIngestError(
            f"{len(problems)} of {len(problems) + len(rows)} data row(s) in this "
            f"{gateway} settlement file could not be parsed.",
            problems,
        )
    if not rows:
        raise SettlementIngestError(
            "The settlement file has a valid header but no data rows. An empty "
            "file is not an empty settlement period — nothing was ingested, and "
            "no day has been marked as reconciled."
        )
    return tuple(rows)


class _RowProblem(Exception):
    """One row's parse failure. Collected, never raised past the parser."""


def _row_record(headers: Sequence[str], raw_row: Sequence[str]) -> dict[str, str]:
    """The row as ``{normalised header: cell}``, kept verbatim for ``raw``.

    Includes columns we do not understand. A settlement dispute months later is
    argued against the gateway's file, and a column we ignored today is exactly
    the one somebody will need.
    """
    record: dict[str, str] = {}
    for index, header in enumerate(headers):
        key = header or f"column_{index + 1}"
        value = raw_row[index] if index < len(raw_row) else ""
        record[key] = (value or "").strip()
    return record


def _cell(
    columns: Mapping[str, int], raw_row: Sequence[str], name: str
) -> str | None:
    """The raw cell for a canonical column, or None when the column is absent."""
    index = columns.get(name)
    if index is None or index >= len(raw_row):
        return None
    return (raw_row[index] or "").strip()


def _parse_row(
    record: Mapping[str, str],
    columns: Mapping[str, int],
    raw_row: Sequence[str],
    row_no: int,
    *,
    amount_unit: str,
    tz: ZoneInfo,
) -> ParsedSettlementRow:
    transaction_id = (_cell(columns, raw_row, "transaction_id") or "").strip()
    if not transaction_id:
        raise _RowProblem(
            f"row {row_no}: transaction id is blank. Without the gateway's own id "
            "this line has no idempotency key and a re-upload would duplicate it"
        )
    if len(transaction_id) > 128:
        raise _RowProblem(
            f"row {row_no}: transaction id is {len(transaction_id)} characters, "
            "longer than the 128 the column holds; truncating it would silently "
            "merge two transactions"
        )

    raw_type = _normalise_header(_cell(columns, raw_row, "transaction_type") or "")
    if not raw_type:
        raise _RowProblem(
            f"row {row_no}: transaction type is blank, so the direction of the "
            "money cannot be established"
        )
    txn_type = _TYPE_MAP.get(raw_type, SettlementTxnType.OTHER)

    gross_minor = _parse_gross(columns, raw_row, row_no, raw_type, amount_unit)
    fee_minor = _parse_money(
        _cell(columns, raw_row, "fee"), row_no, "fee", amount_unit
    )
    tax_minor = _parse_money(
        _cell(columns, raw_row, "tax"), row_no, "tax", amount_unit
    )

    # If the report states its own net, it must agree with ours exactly. This is
    # the only place a third party can contradict our arithmetic, so no tolerance
    # is allowed: a one-paisa gap means a column was mapped wrongly.
    stated_net = _cell(columns, raw_row, "net")
    if stated_net is not None and stated_net.strip().lower() not in _BLANKS:
        stated = _parse_money(stated_net, row_no, "net", amount_unit)
        computed = gross_minor - fee_minor - tax_minor
        if stated != computed:
            raise _RowProblem(
                f"row {row_no}: the report states a net of {stated} paise but "
                f"gross - fee - tax is {computed} paise (gross {gross_minor}, "
                f"fee {fee_minor}, tax {tax_minor}). A settlement that "
                "reconciles to the rupee but not the paisa has not reconciled"
            )

    transacted_at = _parse_instant(
        _cell(columns, raw_row, "transacted_at"), row_no, "transaction date", tz
    )
    if transacted_at is None:
        raise _RowProblem(
            f"row {row_no}: transaction date is blank. Without it the fee cannot "
            "be attributed to the day of the sale it was charged against"
        )
    settled_at = _parse_instant(
        _cell(columns, raw_row, "settled_at"), row_no, "settlement date", tz
    )

    reference = _cell(columns, raw_row, "gateway_reference") or ""
    # The merchant transaction id can hide in any free-text column, so every one
    # of them is searched rather than only the column we mapped as the reference.
    haystack = " ".join(v for v in record.values() if v)
    mtid_match = _MTID_PATTERN.search(haystack)

    return ParsedSettlementRow(
        source_row=row_no,
        transaction_id=transaction_id,
        transaction_type=txn_type,
        settlement_id=_dim(_cell(columns, raw_row, "settlement_id"), 64),
        settlement_utr=_dim(_cell(columns, raw_row, "settlement_utr"), 64),
        currency=(_cell(columns, raw_row, "currency") or "INR").strip().upper()[:3]
        or "INR",
        method=_dim(_cell(columns, raw_row, "method"), 32),
        gross_minor=gross_minor,
        fee_minor=fee_minor,
        tax_minor=tax_minor,
        transacted_at=transacted_at,
        settled_at=settled_at,
        gateway_payment_id=_opt(_cell(columns, raw_row, "gateway_payment_id"), 128),
        gateway_order_id=_opt(_cell(columns, raw_row, "gateway_order_id"), 128),
        gateway_reference=_opt(reference, 255),
        merchant_transaction_id=(
            mtid_match.group(0).upper() if mtid_match else None
        ),
        raw=dict(record),
    )


def _parse_gross(
    columns: Mapping[str, int],
    raw_row: Sequence[str],
    row_no: int,
    raw_type: str,
    amount_unit: str,
) -> int:
    """The SIGNED transaction amount in paise.

    Two shapes, and the sign is established explicitly in both:

    * ``credit`` / ``debit`` columns — unambiguous by construction. ``credit -
      debit``, and a row carrying both is a net movement we do not have to
      interpret.
    * a single ``amount`` column — the sign has to come from the transaction
      type. Reversal types are negated, payment types are not, and a type we do
      not recognise is a **parse error** rather than an assumption. Guessing
      "positive unless it says minus" is how a refund becomes revenue.
    """
    credit_raw = _cell(columns, raw_row, "credit")
    debit_raw = _cell(columns, raw_row, "debit")
    if credit_raw is not None and debit_raw is not None:
        credit = _parse_money(credit_raw, row_no, "credit", amount_unit)
        debit = _parse_money(debit_raw, row_no, "debit", amount_unit)
        if credit < 0 or debit < 0:
            raise _RowProblem(
                f"row {row_no}: credit ({credit}) and debit ({debit}) are "
                "magnitudes and must not themselves be negative; a negative "
                "credit is a debit and the two would cancel the wrong way"
            )
        return credit - debit

    amount = _parse_money(
        _cell(columns, raw_row, "amount"), row_no, "amount", amount_unit
    )
    if raw_type in _REVERSAL_TYPES:
        return -abs(amount)
    if raw_type in _CREDIT_TYPES:
        return abs(amount)
    if amount == 0:
        return 0
    raise _RowProblem(
        f"row {row_no}: transaction type {raw_type!r} is not a known credit "
        f"({sorted(_CREDIT_TYPES)}) or reversal ({sorted(_REVERSAL_TYPES)}) "
        "type, and the file has a single `amount` column, so the direction of "
        "the money cannot be established. Re-export the report with the "
        "credit/debit columns"
    )


def _parse_money(
    raw: str | None, row_no: int, field_name: str, amount_unit: str
) -> int:
    """One money cell to integer paise. Never touches ``float``.

    Handles what real exports contain: ``"₹1,234.56"``, ``"Rs. 1,234.56"``,
    ``"(123.45)"`` for a negative, ``"1234"``, and the blank/``-``/``N/A``
    spellings of "nothing here". Anything else is a problem, named with the row,
    the column and the offending text.
    """
    text = (raw or "").strip()
    if text.lower() in _BLANKS:
        return 0

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1].strip()
    text = _CURRENCY_WORDS.sub("", text)
    text = _CURRENCY_SYMBOLS.sub("", text).strip()
    if text.startswith("+"):
        text = text[1:]
    if not text or text in {"-", "."}:
        return 0

    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError, ArithmeticError):
        raise _RowProblem(
            f"row {row_no}: {field_name} {raw!r} is not a number. It was not "
            "coerced to zero — a fee that failed to parse is unknown, and "
            "storing it as zero would report the gateway charged us nothing"
        ) from None
    if not value.is_finite():
        raise _RowProblem(
            f"row {row_no}: {field_name} {raw!r} is not a finite number"
        )
    if negative:
        value = -value

    if amount_unit == AmountUnit.MINOR:
        if value != value.to_integral_value():
            raise _RowProblem(
                f"row {row_no}: {field_name} {raw!r} was declared to be in minor "
                "units (paise) but is not a whole number. A fractional paisa "
                "means the column is not what the upload said it is"
            )
        return int(value)

    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise _RowProblem(
            f"row {row_no}: {field_name} {raw!r} has more than two decimal "
            "places. It was not rounded — sub-paisa precision in a settlement "
            "report means the column is not the one we think it is"
        )
    # `to_minor` refuses float by contract; the value here is always a Decimal.
    return to_minor(value)


def _parse_instant(
    raw: str | None, row_no: int, field_name: str, tz: ZoneInfo
) -> datetime | None:
    """One timestamp cell to naive UTC, or ``None`` for a blank.

    Accepts, and accepts nothing else:

    * Unix epoch seconds (10 digits) or milliseconds (13) — what the settlements
      API returns. Unambiguously UTC.
    * ISO 8601 with an offset — converted to UTC.
    * ISO 8601 or ``YYYY-MM-DD HH:MM:SS`` without an offset, and bare
      ``YYYY-MM-DD`` — interpreted in the **store's** timezone, because that is
      what the gateway dashboard exports and what the finance team reads.

    ``DD/MM/YYYY`` and ``MM/DD/YYYY`` are deliberately refused. They are
    indistinguishable for the first twelve days of every month, and a settlement
    silently booked five months early is not recoverable from the stored data.
    """
    text = (raw or "").strip()
    if text.lower() in _BLANKS:
        return None

    if text.isdigit():
        number = int(text)
        if len(text) == 13:
            return _EPOCH + timedelta(milliseconds=number)
        if len(text) == 10:
            return _EPOCH + timedelta(seconds=number)
        raise _RowProblem(
            f"row {row_no}: {field_name} {raw!r} looks like a Unix timestamp but "
            f"has {len(text)} digits, not 10 (seconds) or 13 (milliseconds)"
        )

    candidate = text.replace("Z", "+00:00") if text.endswith("Z") else text
    try:
        moment = datetime.fromisoformat(candidate)
    except ValueError:
        raise _RowProblem(
            f"row {row_no}: {field_name} {raw!r} is not a recognised timestamp. "
            "Accepted: a Unix epoch, or ISO 8601 / 'YYYY-MM-DD HH:MM:SS'. "
            "Day-first and month-first slash dates are refused because they are "
            "indistinguishable for twelve days of every month"
        ) from None

    if moment.tzinfo is not None:
        return moment.astimezone(timezone.utc).replace(tzinfo=None)
    # Naive: the gateway dashboard exports in the store's own timezone.
    return moment.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


_EPOCH = datetime(1970, 1, 1)


def _dim(value: str | None, length: int) -> str:
    """A dimension value, or the ``'-'`` sentinel. Never NULL, never blank."""
    text = (value or "").strip()
    return text[:length] if text else "-"


def _opt(value: str | None, length: int) -> str | None:
    """An optional identifier: the trimmed value, or NULL when truly absent."""
    text = (value or "").strip()
    return text[:length] if text else None


# ===========================================================================
# Ingest
# ===========================================================================


@dataclass(frozen=True)
class IngestResult:
    """What one upload did. Every number here is a fact about this call."""

    gateway: str
    source_file: str
    rows_parsed: int
    #: Rows presented to the database. Equal to ``rows_parsed`` — the upsert
    #: presents every row, and MySQL decides which are inserts and which are
    #: no-op updates. Not a count of NEW rows; see ``rows_new``.
    rows_written: int
    #: Rows that did not exist before this call. Zero on a re-upload of an
    #: identical file, which is the whole point of the unique key.
    rows_new: int
    matched: int
    unmatched: int
    ambiguous: int
    #: Distinct store-local payment days touched, so the caller can enqueue the
    #: right settlement_daily buckets for recompute.
    payment_dates: tuple[date, ...]
    #: Distinct store-local settlement days touched. Usually different days.
    settlement_dates: tuple[date, ...]
    tz_generation: int
    gross_minor: int
    fee_minor: int
    tax_minor: int
    net_minor: int
    warnings: tuple[str, ...] = ()

    @property
    def dirty_dates(self) -> tuple[date, ...]:
        """Every bucket this upload changed, both bases, sorted."""
        return tuple(sorted(set(self.payment_dates) | set(self.settlement_dates)))


def ingest_settlement_csv(
    db: Session,
    text: str,
    *,
    amount_unit: str,
    gateway: str = DEFAULT_GATEWAY,
    source_file: str = "-",
    source: str = SettlementSource.CSV_UPLOAD,
    commit: bool = True,
) -> IngestResult:
    """Parse, match and store a settlement report. All of it, or none of it.

    Order of operations matters and is not an accident:

    1. **Parse and validate the entire file.** Raises before any statement is
       issued, so a rejected file leaves the table byte-identical.
    2. **Match** every row against our payments, in one pass with bulk lookups.
    3. **Upsert** on ``(gateway, transaction_id)``. Re-uploading the same file
       presents the same values and MySQL changes nothing.

    ``commit`` exists for the caller that wants the ingest inside a larger
    transaction. The default commits, because the normal caller is an admin
    upload endpoint whose unit of work is the file.
    """
    tz = store_timezone(db)
    generation = int(active_generation(db).generation)

    # Naive timestamps in a dashboard export are in the store's own timezone.
    rows = parse_settlement_csv(
        text, amount_unit=amount_unit, gateway=gateway, tz=tz
    )
    matches = _match_rows(db, rows, gateway=gateway)

    payment_dates: set[date] = set()
    settlement_dates: set[date] = set()
    payload: list[dict[str, Any]] = []
    counts = {
        SettlementMatchStatus.MATCHED: 0,
        SettlementMatchStatus.UNMATCHED: 0,
        SettlementMatchStatus.AMBIGUOUS: 0,
    }
    totals = {"gross": 0, "fee": 0, "tax": 0, "net": 0}

    for row in rows:
        payment_date = local_day(_aware(row.transacted_at), tz)
        settlement_date = (
            local_day(_aware(row.settled_at), tz) if row.settled_at else None
        )
        payment_dates.add(payment_date)
        if settlement_date is not None:
            settlement_dates.add(settlement_date)

        match = matches[row.transaction_id]
        counts[match.status] += 1
        totals["gross"] += row.gross_minor
        totals["fee"] += row.fee_minor
        totals["tax"] += row.tax_minor
        totals["net"] += row.net_minor

        payload.append(
            {
                "gateway": gateway[:40] or "-",
                "transaction_id": row.transaction_id,
                "transaction_type": row.transaction_type,
                "settlement_id": row.settlement_id,
                "settlement_utr": row.settlement_utr,
                "currency": row.currency,
                "method": row.method,
                "gross_minor": row.gross_minor,
                "fee_minor": row.fee_minor,
                "tax_minor": row.tax_minor,
                "net_minor": row.net_minor,
                "transacted_at": row.transacted_at,
                "settled_at": row.settled_at,
                "tz_generation": generation,
                "payment_date": payment_date,
                "settlement_date": settlement_date,
                "gateway_payment_id": row.gateway_payment_id,
                "gateway_order_id": row.gateway_order_id,
                "gateway_reference": row.gateway_reference,
                "merchant_transaction_id": row.merchant_transaction_id,
                "match_status": match.status,
                "match_key": match.key,
                "order_id": match.order_id,
                "order_payment_id": match.order_payment_id,
                "source": source,
                "source_file": _dim(source_file, 255),
                "source_row": row.source_row,
                "raw": row.raw,
            }
        )

    existing = _existing_transaction_ids(
        db, gateway, [r.transaction_id for r in rows]
    )
    rows_new = sum(1 for r in rows if r.transaction_id not in existing)

    try:
        _upsert_settlements(db, payload)
        if commit:
            db.commit()
    except Exception:
        db.rollback()
        raise

    warnings: list[str] = []
    if counts[SettlementMatchStatus.UNMATCHED]:
        warnings.append(
            f"{counts[SettlementMatchStatus.UNMATCHED]} settlement line(s) could "
            "not be tied to a payment we recorded. They are stored with their "
            "money and dates intact and appear in the reconciliation view; they "
            "are a finding, not an ingest failure"
        )
    if counts[SettlementMatchStatus.AMBIGUOUS]:
        warnings.append(
            f"{counts[SettlementMatchStatus.AMBIGUOUS]} settlement line(s) "
            "resolved to more than one of our payments and were left unattributed "
            "rather than assigned to one arbitrarily"
        )

    return IngestResult(
        gateway=gateway,
        source_file=source_file,
        rows_parsed=len(rows),
        rows_written=len(payload),
        rows_new=rows_new,
        matched=counts[SettlementMatchStatus.MATCHED],
        unmatched=counts[SettlementMatchStatus.UNMATCHED],
        ambiguous=counts[SettlementMatchStatus.AMBIGUOUS],
        payment_dates=tuple(sorted(payment_dates)),
        settlement_dates=tuple(sorted(settlement_dates)),
        tz_generation=generation,
        gross_minor=totals["gross"],
        fee_minor=totals["fee"],
        tax_minor=totals["tax"],
        net_minor=totals["net"],
        warnings=tuple(warnings),
    )


#: Columns the upsert refreshes. ``created_at`` is excluded on purpose: it
#: records when a line was FIRST seen, and a re-upload must not rewrite the
#: history of when we learned about it.
_UPSERT_COLUMNS: tuple[str, ...] = (
    "transaction_type",
    "settlement_id",
    "settlement_utr",
    "currency",
    "method",
    "gross_minor",
    "fee_minor",
    "tax_minor",
    "net_minor",
    "transacted_at",
    "settled_at",
    "tz_generation",
    "payment_date",
    "settlement_date",
    "gateway_payment_id",
    "gateway_order_id",
    "gateway_reference",
    "merchant_transaction_id",
    "match_status",
    "match_key",
    "order_id",
    "order_payment_id",
    "source",
    "source_file",
    "source_row",
    "raw",
)

#: Rows per INSERT. A year of settlements is tens of thousands of lines and a
#: single statement that large exceeds max_allowed_packet on a default MySQL.
_INSERT_CHUNK = 500


def _upsert_settlements(db: Session, payload: Sequence[Mapping[str, Any]]) -> None:
    """``INSERT ... ON DUPLICATE KEY UPDATE`` on ``(gateway, transaction_id)``.

    Every non-key column is **overwritten**, never accumulated — the same rule
    ``aggregation/jobs._upsert`` documents. Accumulation is what turns a
    re-upload into a doubled fee, and building the update map mechanically from
    a named tuple of columns leaves nowhere for an accumulating column to hide.
    """
    for start in range(0, len(payload), _INSERT_CHUNK):
        chunk = list(payload[start : start + _INSERT_CHUNK])
        statement = mysql_insert(PaymentSettlement).values(chunk)
        db.execute(
            statement.on_duplicate_key_update(
                **{c: statement.inserted[c] for c in _UPSERT_COLUMNS}
            )
        )


def _existing_transaction_ids(
    db: Session, gateway: str, transaction_ids: Sequence[str]
) -> set[str]:
    found: set[str] = set()
    for start in range(0, len(transaction_ids), _INSERT_CHUNK):
        chunk = transaction_ids[start : start + _INSERT_CHUNK]
        found.update(
            db.execute(
                select(PaymentSettlement.transaction_id).where(
                    PaymentSettlement.gateway == gateway,
                    PaymentSettlement.transaction_id.in_(chunk),
                )
            ).scalars()
        )
    return found


# ===========================================================================
# Matching
# ===========================================================================


@dataclass(frozen=True)
class _Match:
    status: str
    key: str
    order_id: int | None = None
    order_payment_id: int | None = None


@dataclass(frozen=True)
class _Candidate:
    """One resolution of a token: which order, and which payment leg."""

    order_id: int
    order_payment_id: int | None


def _match_rows(
    db: Session,
    rows: Sequence[ParsedSettlementRow],
    *,
    gateway: str,
) -> dict[str, _Match]:
    """Resolve every row against our payment records, in four bulk queries.

    Per-row queries would be correct and would also make a 20 000-line report an
    outage. Every candidate token from the whole file is collected first, looked
    up once per key family, and then the cascade in :data:`MATCH_ORDER` is walked
    in memory.

    A token that resolves to more than one order is recorded as ``AMBIGUOUS`` and
    left unattributed. Picking one would produce a settlement filed against the
    wrong order, which reconciles cleanly and is wrong — strictly worse than an
    unmatched line, which at least shows up as a finding.
    """
    per_row = {row.transaction_id: _row_tokens(row) for row in rows}
    tokens: dict[str, set[str]] = {key: set() for key in MATCH_ORDER}
    for row_tokens in per_row.values():
        for key, values in row_tokens.items():
            tokens[key].update(values)
    lookups = {
        SettlementMatchKey.GATEWAY_PAYMENT_ID: _lookup_provider_refs(
            db, tokens[SettlementMatchKey.GATEWAY_PAYMENT_ID], gateway
        ),
        SettlementMatchKey.MERCHANT_TXN_ID: _lookup_merchant_txn_ids(
            db, tokens[SettlementMatchKey.MERCHANT_TXN_ID]
        ),
        SettlementMatchKey.PAYMENT_LINK_ID: _lookup_provider_refs(
            db, tokens[SettlementMatchKey.PAYMENT_LINK_ID], gateway
        ),
        SettlementMatchKey.REFUND_REFERENCE: _lookup_refund_references(
            db, tokens[SettlementMatchKey.REFUND_REFERENCE]
        ),
    }

    return {
        row.transaction_id: _match_one(per_row[row.transaction_id], lookups)
        for row in rows
    }


def _row_tokens(row: ParsedSettlementRow) -> dict[str, list[str]]:
    """Every candidate identifier this row offers, grouped by key family.

    The whole row is searched, not only the columns we mapped: gateways move
    these ids between ``description``, ``notes`` and ``order_receipt`` between
    report versions, and a matcher that only reads one of them silently stops
    matching after an export change.
    """
    haystack = " ".join(v for v in row.raw.values() if v)
    explicit_pay = row.gateway_payment_id or ""
    return {
        SettlementMatchKey.GATEWAY_PAYMENT_ID: sorted(
            set(_PAY_ID_PATTERN.findall(f"{explicit_pay} {row.transaction_id} {haystack}"))
        ),
        SettlementMatchKey.MERCHANT_TXN_ID: (
            [row.merchant_transaction_id] if row.merchant_transaction_id else []
        ),
        SettlementMatchKey.PAYMENT_LINK_ID: sorted(
            set(_PLINK_ID_PATTERN.findall(haystack))
        ),
        SettlementMatchKey.REFUND_REFERENCE: sorted(
            set(_RFND_ID_PATTERN.findall(f"{row.transaction_id} {haystack}"))
            | set(_OUR_REFUND_REF_PATTERN.findall(haystack))
        ),
    }


def _match_one(
    tokens: Mapping[str, list[str]],
    lookups: Mapping[str, Mapping[str, list[_Candidate]]],
) -> _Match:
    for key in MATCH_ORDER:
        table = lookups[key]
        for token in tokens[key]:
            candidates = table.get(token)
            if not candidates:
                continue
            distinct_orders = {c.order_id for c in candidates}
            if len(distinct_orders) > 1:
                return _Match(status=SettlementMatchStatus.AMBIGUOUS, key=key)
            candidate = candidates[0]
            return _Match(
                status=SettlementMatchStatus.MATCHED,
                key=key,
                order_id=candidate.order_id,
                order_payment_id=candidate.order_payment_id,
            )
    return _Match(status=SettlementMatchStatus.UNMATCHED, key=SettlementMatchKey.NONE)


def _chunked(values: Iterable[str], size: int = _INSERT_CHUNK) -> Iterable[list[str]]:
    batch: list[str] = []
    for value in values:
        batch.append(value)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _lookup_merchant_txn_ids(
    db: Session, tokens: set[str]
) -> dict[str, list[_Candidate]]:
    """``ORD...`` -> our order, via the UNIQUE ``orders.payment_intent_id``.

    The prepaid ``order_payments`` leg is picked up in the same query so the
    match can name the exact leg, which is what a split-COD order needs: its cash
    leg never touches the gateway and must never be attributed a settlement.
    """
    if not tokens:
        return {}
    found: dict[str, list[_Candidate]] = {}
    for batch in _chunked(sorted(tokens)):
        rows = db.execute(
            select(
                Order.payment_intent_id,
                Order.id,
                OrderPayment.id.label("leg_id"),
            )
            .select_from(Order)
            .outerjoin(
                OrderPayment,
                (OrderPayment.order_id == Order.id)
                & (OrderPayment.gateway.isnot(None)),
            )
            .where(Order.payment_intent_id.in_(batch))
        ).all()
        for row in rows:
            found.setdefault(row.payment_intent_id, []).append(
                _Candidate(
                    order_id=int(row.id),
                    order_payment_id=int(row.leg_id) if row.leg_id else None,
                )
            )
    return _dedupe(found)


def _lookup_provider_refs(
    db: Session, tokens: set[str], gateway: str
) -> dict[str, list[_Candidate]]:
    """A provider ref -> our order, across the three columns that can hold one.

    ``order_payments.gateway_payment_id``, ``order_payments.gateway_order_id``
    and the legacy ``orders.payment_provider_ref`` are all searched, because the
    normalisation migration backfilled them unevenly: orders predating it carry
    the ref only on ``orders``, and ``gateway_payment_id`` was explicitly left
    NULL for every one of them.
    """
    if not tokens:
        return {}
    found: dict[str, list[_Candidate]] = {}
    for batch in _chunked(sorted(tokens)):
        wanted = set(batch)
        legs = db.execute(
            select(
                OrderPayment.id,
                OrderPayment.order_id,
                OrderPayment.gateway_payment_id,
                OrderPayment.gateway_order_id,
            ).where(
                OrderPayment.gateway_payment_id.in_(batch)
                | OrderPayment.gateway_order_id.in_(batch)
            )
        ).all()
        for leg in legs:
            for ref in (leg.gateway_payment_id, leg.gateway_order_id):
                if ref in wanted:
                    found.setdefault(ref, []).append(
                        _Candidate(
                            order_id=int(leg.order_id),
                            order_payment_id=int(leg.id),
                        )
                    )
        legacy = db.execute(
            select(Order.id, Order.payment_provider_ref).where(
                Order.payment_provider_ref.in_(batch)
            )
        ).all()
        for order in legacy:
            found.setdefault(order.payment_provider_ref, []).append(
                _Candidate(order_id=int(order.id), order_payment_id=None)
            )
    return _dedupe(found)


def _lookup_refund_references(
    db: Session, tokens: set[str]
) -> dict[str, list[_Candidate]]:
    """A refund reference -> the order it reversed.

    Two sources, both of which really carry it: ``returns.refund_reference``
    (which stores the gateway's ``rfnd_...`` when the API call succeeded, and our
    deterministic ``RFND...`` when it fell back to manual) and the
    ``REFUND_ATTEMPT`` payment event, which is the only record for an admin
    refund or a customer cancellation.
    """
    if not tokens:
        return {}
    found: dict[str, list[_Candidate]] = {}
    for batch in _chunked(sorted(tokens)):
        returns = db.execute(
            select(ReturnRequest.refund_reference, ReturnRequest.order_id).where(
                ReturnRequest.refund_reference.in_(batch)
            )
        ).all()
        for row in returns:
            found.setdefault(row.refund_reference, []).append(
                _Candidate(order_id=int(row.order_id), order_payment_id=None)
            )
        events = db.execute(
            select(PaymentEvent.provider_ref, PaymentEvent.order_id).where(
                PaymentEvent.provider_ref.in_(batch),
                PaymentEvent.event_type == PaymentEventType.REFUND_ATTEMPT,
                PaymentEvent.order_id.isnot(None),
            )
        ).all()
        for row in events:
            found.setdefault(row.provider_ref, []).append(
                _Candidate(order_id=int(row.order_id), order_payment_id=None)
            )
    return _dedupe(found)


def _dedupe(
    found: Mapping[str, list[_Candidate]]
) -> dict[str, list[_Candidate]]:
    """Collapse repeats of the same order so one order twice is not ambiguous.

    A ref appearing on both the prepaid leg's ``gateway_payment_id`` and its
    ``gateway_order_id`` — which is exactly what the Payment Links flow produces —
    is one candidate, not two, and must not be reported as an ambiguity.
    """
    result: dict[str, list[_Candidate]] = {}
    for token, candidates in found.items():
        by_order: dict[int, _Candidate] = {}
        for candidate in candidates:
            current = by_order.get(candidate.order_id)
            if current is None or (
                current.order_payment_id is None
                and candidate.order_payment_id is not None
            ):
                by_order[candidate.order_id] = candidate
        result[token] = [by_order[k] for k in sorted(by_order)]
    return result


# ===========================================================================
# The two gaps
# ===========================================================================


def unmatched_settlements(
    db: Session,
    date_from: date,
    date_to: date,
    *,
    gateway: str | None = None,
    tz_generation: int | None = None,
    limit: int = MAX_REPORTED_IDS,
) -> list[PaymentSettlement]:
    """Settlement lines in ``[date_from, date_to)`` with no payment behind them.

    Payment-dated, because the question is "what did the gateway process on this
    day that we cannot account for" — a cash-dated version would file the finding
    under the day the money arrived, two days after the sale it belongs to.

    Ambiguous lines are included: a line attributed to more than one order is as
    unexplained as one attributed to none.
    """
    conditions = [
        PaymentSettlement.payment_date >= date_from,
        PaymentSettlement.payment_date < date_to,
        PaymentSettlement.match_status != SettlementMatchStatus.MATCHED,
    ]
    if gateway:
        conditions.append(PaymentSettlement.gateway == gateway)
    if tz_generation is not None:
        conditions.append(PaymentSettlement.tz_generation == tz_generation)
    return list(
        db.execute(
            select(PaymentSettlement)
            .where(*conditions)
            .order_by(
                PaymentSettlement.payment_date, PaymentSettlement.transaction_id
            )
            .limit(limit)
        )
        .scalars()
        .all()
    )


@dataclass(frozen=True)
class UnsettledPayment:
    """A payment we captured that no settlement line explains."""

    order_id: int
    order_payment_id: int
    gateway: str
    amount_minor: int
    paid_at: datetime
    days_outstanding: int


def unsettled_captured_payments(
    db: Session,
    date_from: date,
    date_to: date,
    *,
    gateway: str | None = None,
    older_than_days: int = UNSETTLED_AFTER_DAYS,
    as_of: datetime | None = None,
    limit: int = MAX_REPORTED_IDS,
) -> list[UnsettledPayment]:
    """Captured payments in the window with no settlement line, after a grace period.

    The mirror image of :func:`unmatched_settlements`, and the harder half: an
    unmatched settlement announces itself by existing, whereas a payment the
    gateway never settled is an *absence* and can only be found by looking for
    it. This is money we believe we are owed and have no gateway record of
    receiving.

    ``older_than_days`` exists so that yesterday's captures are not reported as
    missing. See :data:`UNSETTLED_AFTER_DAYS` for why five.

    Derived rather than stored. The inputs — ``order_payments`` and
    ``payment_settlements`` — are both durable, and materialising a third table
    of absences would need its own invalidation the moment a late settlement
    arrived. The daily counts ARE stored, in
    ``agg_settlement_daily.unsettled_payments``, so the finding survives on the
    reconciliation view without a second source of truth.
    """
    tz = store_timezone(db)
    start, end = range_bounds_utc(date_from, date_to, tz)
    # `range_bounds_utc` returns AWARE instants; `as_of` may be either. Both
    # sides are forced aware before they are compared, because a naive/aware
    # comparison raises and a silently naive one would be five and a half hours
    # wrong in this store's timezone.
    now = _aware(as_of) if as_of else datetime.now(timezone.utc)
    end = min(end, now - timedelta(days=older_than_days))
    if end <= start:
        return []

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
    conditions = [
        OrderPayment.payment_status == PaymentTxnStatus.PAID,
        OrderPayment.paid_at.isnot(None),
        OrderPayment.paid_at >= start,
        OrderPayment.paid_at < end,
        # A COD leg never touches a gateway and can never appear in a settlement
        # report. Counting it as unsettled would report a permanent, growing gap
        # that no action could ever close.
        OrderPayment.gateway.isnot(None),
        ~settled,
    ]
    if gateway:
        conditions.append(OrderPayment.gateway == gateway)

    rows = db.execute(
        select(
            OrderPayment.id,
            OrderPayment.order_id,
            OrderPayment.gateway,
            OrderPayment.amount,
            OrderPayment.paid_at,
        )
        .where(*conditions)
        .order_by(OrderPayment.paid_at, OrderPayment.id)
        .limit(limit)
    ).all()

    return [
        UnsettledPayment(
            order_id=int(row.order_id),
            order_payment_id=int(row.id),
            gateway=row.gateway or "-",
            amount_minor=to_minor(row.amount),
            paid_at=row.paid_at,
            days_outstanding=max(
                0, int((_naive(now) - _naive(row.paid_at)).total_seconds() // 86400)
            ),
        )
        for row in rows
    ]


# ===========================================================================
# ESTIMATED -> ACTUAL
# ===========================================================================


@dataclass(frozen=True)
class ObservedFee:
    """The gateway fee actually charged on one reporting day, and its coverage.

    This is the handoff that turns an estimate into an observation. It is
    deliberately NOT a single number: a day where three of ten payments have
    settled has an observed fee for three payments and an unknown fee for seven,
    and collapsing that to one figure is how a partial measurement becomes a
    confident wrong total.

    Callers apply the cost rule to the unmatched remainder and take
    ``fee_minor + tax_minor`` for the matched part. ``quality`` is ``ACTUAL``
    only at full coverage; below that it is ``INCOMPLETE``, never ``ESTIMATED``,
    because the shortfall is a measurement gap rather than a modelling choice.
    """

    bucket_date: date
    gateway: str
    #: Observed fee on the MATCHED lines only, in paise.
    fee_minor: int
    #: Observed tax on that fee, in paise.
    tax_minor: int
    #: Gross value of the matched lines. The base the fee was charged on.
    matched_gross_minor: int
    matched_txns: int
    unmatched_txns: int
    #: Captured payments on this day that no settlement explains yet. These are
    #: the ones the cost rule must still cover.
    unsettled_payments: int
    unsettled_amount_minor: int
    source: str

    @property
    def total_minor(self) -> int:
        """Fee plus tax on fee — the whole observed cost of taking the money."""
        return self.fee_minor + self.tax_minor

    @property
    def coverage_pct(self) -> Decimal:
        """Share of this day's known payments that a settlement line explains."""
        known = self.matched_txns + self.unmatched_txns + self.unsettled_payments
        if not known:
            return Decimal("0")
        return (Decimal(self.matched_txns) / Decimal(known) * 100).quantize(
            Decimal("0.01")
        )

    @property
    def quality(self) -> MetricQuality:
        if self.matched_txns == 0:
            return MetricQuality.INCOMPLETE
        if self.unmatched_txns or self.unsettled_payments:
            return MetricQuality.INCOMPLETE
        return MetricQuality.ACTUAL


def observed_gateway_fee(
    db: Session,
    bucket_date: date,
    *,
    gateway: str = DEFAULT_GATEWAY,
    tz_generation: int | None = None,
    older_than_days: int = UNSETTLED_AFTER_DAYS,
    as_of: datetime | None = None,
) -> ObservedFee | None:
    """The observed gateway fee for one store-local day, or ``None``.

    ``None`` — not a zero-fee result — when no settlement line exists for the
    day at all. A day nobody has uploaded a settlement report for has an
    *unknown* fee, and the cost rule remains the only answer; returning a
    zero-valued ``ObservedFee`` would let a caller book an ACTUAL zero against a
    day whose report simply has not arrived yet.

    Fees are attributed to ``payment_date``, never ``settlement_date``. The fee
    was charged against a sale, so it belongs in the bucket where that sale's
    revenue is, two or three days before the money moved.
    """
    generation = (
        tz_generation
        if tz_generation is not None
        else int(active_generation(db).generation)
    )
    row = db.execute(
        select(
            func.count(PaymentSettlement.id).label("txns"),
            func.coalesce(
                func.sum(
                    _case_when(
                        PaymentSettlement.match_status
                        == SettlementMatchStatus.MATCHED,
                        PaymentSettlement.fee_minor,
                    )
                ),
                0,
            ).label("fee_minor"),
            func.coalesce(
                func.sum(
                    _case_when(
                        PaymentSettlement.match_status
                        == SettlementMatchStatus.MATCHED,
                        PaymentSettlement.tax_minor,
                    )
                ),
                0,
            ).label("tax_minor"),
            func.coalesce(
                func.sum(
                    _case_when(
                        PaymentSettlement.match_status
                        == SettlementMatchStatus.MATCHED,
                        PaymentSettlement.gross_minor,
                    )
                ),
                0,
            ).label("matched_gross"),
            func.coalesce(
                func.sum(
                    _case_when(
                        PaymentSettlement.match_status
                        == SettlementMatchStatus.MATCHED,
                        1,
                    )
                ),
                0,
            ).label("matched_txns"),
        ).where(
            PaymentSettlement.gateway == gateway,
            PaymentSettlement.payment_date == bucket_date,
            PaymentSettlement.tz_generation == generation,
        )
    ).one()

    if int(row.txns or 0) == 0:
        return None

    outstanding = unsettled_captured_payments(
        db,
        bucket_date,
        bucket_date + timedelta(days=1),
        gateway=gateway,
        older_than_days=older_than_days,
        as_of=as_of,
        limit=10_000,
    )
    matched = int(row.matched_txns or 0)
    return ObservedFee(
        bucket_date=bucket_date,
        gateway=gateway,
        fee_minor=int(row.fee_minor or 0),
        tax_minor=int(row.tax_minor or 0),
        matched_gross_minor=int(row.matched_gross or 0),
        matched_txns=matched,
        unmatched_txns=int(row.txns or 0) - matched,
        unsettled_payments=len(outstanding),
        unsettled_amount_minor=sum(p.amount_minor for p in outstanding),
        source=f"{gateway}_settlement",
    )


# ===========================================================================
# Reconciliation
# ===========================================================================
#
# Deliberately built on `reconciliation.CheckStatus` rather than a second set of
# statuses. That module's central rule — a check that could NOT run is listed
# with NULL values, never omitted and never reported as a 0.00% variance — is the
# whole reason this report exists, and re-declaring the vocabulary here would let
# the two drift into meaning subtly different things.


class SettlementCheckKey:
    """Stable identifiers for the settlement checks."""

    SETTLED_VS_CAPTURED = "settlement_vs_captured"
    PAYOUT_IDENTITY = "settlement_payout_identity"
    UNMATCHED_LINES = "settlement_unmatched_lines"
    UNSETTLED_PAYMENTS = "settlement_unsettled_payments"


SETTLEMENT_CHECK_ORDER: tuple[str, ...] = (
    SettlementCheckKey.SETTLED_VS_CAPTURED,
    SettlementCheckKey.PAYOUT_IDENTITY,
    SettlementCheckKey.UNMATCHED_LINES,
    SettlementCheckKey.UNSETTLED_PAYMENTS,
)


@dataclass(frozen=True)
class SettlementCheck:
    """One row of the settlement variance table.

    Same invariant as ``reconciliation.CheckResult``: when ``status`` is
    ``not_configured`` every numeric field is ``None``. No settlement report
    uploaded for a window is not a window that balanced.
    """

    check_key: str
    label: str
    status: str
    left_label: str = ""
    left_value: Decimal | None = None
    right_label: str = ""
    right_value: Decimal | None = None
    difference: Decimal | None = None
    coverage_pct: Decimal | None = None
    population: int = 0
    compared: int = 0
    missing_ids: tuple[str, ...] = ()
    detail: str = ""
    context: Mapping[str, Any] = field(default_factory=dict)

    def to_row(self, period: str) -> dict[str, Any]:
        """The shape ``ResolverId.RECONCILIATION`` renders, matching
        ``reconciliation.CheckResult.to_row`` field for field."""
        return {
            "check_name": self.check_key,
            "period": period,
            "status": "matched" if self.status == CheckStatus.MATCH else self.status,
            "source_value": self.left_value,
            "rollup_value": self.right_value,
            "variance_pct": _pct(self.difference, self.left_value),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class SettlementReconciliation:
    """Every settlement check, always — including the ones that could not run."""

    date_from: date
    date_to: date
    gateway: str
    tz_generation: int
    generated_at: datetime
    checks: tuple[SettlementCheck, ...]

    @property
    def period(self) -> str:
        return f"{self.date_from.isoformat()}..{self.date_to.isoformat()}"

    def by_key(self, check_key: str) -> SettlementCheck:
        for check in self.checks:
            if check.check_key == check_key:
                return check
        raise KeyError(check_key)

    def to_rows(self) -> list[dict[str, Any]]:
        return [c.to_row(self.period) for c in self.checks]


def settlement_reconciliation(
    db: Session,
    date_from: date,
    date_to: date,
    *,
    gateway: str = DEFAULT_GATEWAY,
    older_than_days: int = UNSETTLED_AFTER_DAYS,
    as_of: datetime | None = None,
) -> SettlementReconciliation:
    """Reconcile settlements against our payments over ``[date_from, date_to)``.

    Half-open and in store-local reporting days, matching every other window in
    this stack.

    All four checks are always present. When no settlement report covers the
    window they are all ``not_configured`` with NULL values — because "no report
    uploaded" and "everything balanced" are the two readings a settlement screen
    must never be able to confuse, and an empty variance table reads as the
    second one.
    """
    if date_to <= date_from:
        raise ValueError(
            f"date_to ({date_to.isoformat()}) must be after date_from "
            f"({date_from.isoformat()}); the window is half-open [from, to)"
        )
    generation = int(active_generation(db).generation)
    tz = store_timezone(db)

    lines = db.execute(
        select(
            func.count(PaymentSettlement.id).label("txns"),
            func.coalesce(func.sum(PaymentSettlement.gross_minor), 0).label("gross"),
            func.coalesce(func.sum(PaymentSettlement.fee_minor), 0).label("fee"),
            func.coalesce(func.sum(PaymentSettlement.tax_minor), 0).label("tax"),
            func.coalesce(func.sum(PaymentSettlement.net_minor), 0).label("net"),
        ).where(*_window(date_from, date_to, gateway, generation))
    ).one()
    total_txns = int(lines.txns or 0)

    checks: list[SettlementCheck] = []
    if total_txns == 0:
        detail = (
            f"No {gateway} settlement line covers "
            f"{date_from.isoformat()}..{date_to.isoformat()} under tz generation "
            f"{generation}, so nothing was compared. Upload the gateway's "
            "settlement report for this period. Values are NULL rather than "
            "zero: a window with no report is unreconciled, not reconciled."
        )
        for key in SETTLEMENT_CHECK_ORDER:
            checks.append(
                SettlementCheck(
                    check_key=key,
                    label=_CHECK_LABELS[key],
                    status=CheckStatus.NOT_CONFIGURED,
                    detail=detail,
                    context={"settlement_lines": 0, "gateway": gateway},
                )
            )
        return SettlementReconciliation(
            date_from=date_from,
            date_to=date_to,
            gateway=gateway,
            tz_generation=generation,
            generated_at=_utcnow(),
            checks=tuple(checks),
        )

    # -- 1. matched settlement gross vs what we recorded capturing ---------
    matched = db.execute(
        select(
            func.count(PaymentSettlement.id).label("txns"),
            func.coalesce(func.sum(PaymentSettlement.gross_minor), 0).label("gross"),
        ).where(
            *_window(date_from, date_to, gateway, generation),
            PaymentSettlement.match_status == SettlementMatchStatus.MATCHED,
        )
    ).one()
    start, end = range_bounds_utc(date_from, date_to, tz)
    captured_minor = to_minor(
        db.execute(
            select(func.coalesce(func.sum(OrderPayment.amount), 0)).where(
                OrderPayment.payment_status == PaymentTxnStatus.PAID,
                OrderPayment.gateway == gateway,
                OrderPayment.paid_at.isnot(None),
                OrderPayment.paid_at >= start,
                OrderPayment.paid_at < end,
            )
        ).scalar_one()
    )
    matched_gross = int(matched.gross or 0)
    checks.append(
        SettlementCheck(
            check_key=SettlementCheckKey.SETTLED_VS_CAPTURED,
            label=_CHECK_LABELS[SettlementCheckKey.SETTLED_VS_CAPTURED],
            status=(
                CheckStatus.MATCH
                if matched_gross == captured_minor
                else CheckStatus.VARIANCE
            ),
            left_label="payment_settlements (matched, gross)",
            left_value=from_minor(matched_gross),
            right_label="order_payments (captured)",
            right_value=from_minor(captured_minor),
            difference=from_minor(matched_gross - captured_minor),
            coverage_pct=_coverage(int(matched.txns or 0), total_txns),
            population=total_txns,
            compared=int(matched.txns or 0),
            detail=(
                f"{int(matched.txns or 0)} of {total_txns} settlement line(s) in "
                "this window were tied to a payment we recorded. The gateway is "
                "authoritative for what it processed; our order records are "
                "authoritative for what the customer was charged. A gap is a "
                "capture one side has and the other does not — never a reason to "
                "overwrite recognised revenue with a settlement figure."
            ),
            context={
                "settlement_lines": total_txns,
                "matched_lines": int(matched.txns or 0),
                "gross_minor": int(lines.gross or 0),
            },
        )
    )

    # -- 2. the paisa-exact payout identity --------------------------------
    computed = int(lines.gross or 0) - int(lines.fee or 0) - int(lines.tax or 0)
    stored = int(lines.net or 0)
    checks.append(
        SettlementCheck(
            check_key=SettlementCheckKey.PAYOUT_IDENTITY,
            label=_CHECK_LABELS[SettlementCheckKey.PAYOUT_IDENTITY],
            status=(
                CheckStatus.MATCH if computed == stored else CheckStatus.VARIANCE
            ),
            left_label="gross - fee - tax",
            left_value=from_minor(computed),
            right_label="stored net payout",
            right_value=from_minor(stored),
            difference=from_minor(computed - stored),
            coverage_pct=Decimal("100.00"),
            population=total_txns,
            compared=total_txns,
            detail=(
                "Self-proving, to the paisa, with no tolerance: every stored line "
                "must satisfy gross - fee - tax = net. The database enforces it "
                "per row via ck_payment_settlements_net; this check re-asserts it "
                "over the window's totals, where a compensating pair of errors "
                "would otherwise hide."
            ),
            context={
                "gross_minor": int(lines.gross or 0),
                "fee_minor": int(lines.fee or 0),
                "tax_minor": int(lines.tax or 0),
                "net_minor": stored,
            },
        )
    )

    # -- 3. settlement lines we cannot explain -----------------------------
    orphans = unmatched_settlements(
        db,
        date_from,
        date_to,
        gateway=gateway,
        tz_generation=generation,
        limit=MAX_REPORTED_IDS,
    )
    orphan_count = total_txns - int(matched.txns or 0)
    checks.append(
        SettlementCheck(
            check_key=SettlementCheckKey.UNMATCHED_LINES,
            label=_CHECK_LABELS[SettlementCheckKey.UNMATCHED_LINES],
            status=(
                CheckStatus.MATCH if orphan_count == 0 else CheckStatus.VARIANCE
            ),
            left_label="settlement lines with no payment of ours",
            left_value=Decimal(orphan_count),
            right_label="expected",
            right_value=Decimal(0),
            difference=Decimal(orphan_count),
            coverage_pct=_coverage(int(matched.txns or 0), total_txns),
            population=total_txns,
            compared=total_txns,
            missing_ids=tuple(o.transaction_id for o in orphans),
            detail=(
                f"{orphan_count} of {total_txns} settlement line(s) could not be "
                "tied to any payment we have a record of. The primary join key "
                "in this deployment is the merchant transaction id we send "
                "Razorpay as the payment link's reference_id; a line without one "
                "in any of its free-text columns cannot be attributed. These "
                "lines are stored in full and are listed here rather than "
                "dropped."
            ),
            context={
                "unmatched_lines": orphan_count,
                "listed": len(orphans),
                "unmatched_gross_minor": sum(o.gross_minor for o in orphans),
            },
        )
    )

    # -- 4. captures the gateway has not settled ---------------------------
    outstanding = unsettled_captured_payments(
        db,
        date_from,
        date_to,
        gateway=gateway,
        older_than_days=older_than_days,
        as_of=as_of,
        limit=MAX_REPORTED_IDS,
    )
    checks.append(
        SettlementCheck(
            check_key=SettlementCheckKey.UNSETTLED_PAYMENTS,
            label=_CHECK_LABELS[SettlementCheckKey.UNSETTLED_PAYMENTS],
            status=(
                CheckStatus.MATCH if not outstanding else CheckStatus.VARIANCE
            ),
            left_label="captured payments with no settlement",
            left_value=Decimal(len(outstanding)),
            right_label="expected",
            right_value=Decimal(0),
            difference=Decimal(len(outstanding)),
            population=len(outstanding),
            compared=len(outstanding),
            missing_ids=tuple(
                f"order#{p.order_id}/leg#{p.order_payment_id}" for p in outstanding
            ),
            detail=(
                f"{len(outstanding)} payment(s) captured in this window are still "
                f"unaccounted for by any settlement line more than {older_than_days} "
                "day(s) later. This is the harder half of the reconciliation: an "
                "unmatched settlement announces itself by existing, whereas money "
                "the gateway never paid out is an absence and only shows up if "
                "something looks for it."
            ),
            context={
                "unsettled_payments": len(outstanding),
                "unsettled_amount_minor": sum(p.amount_minor for p in outstanding),
                "grace_days": older_than_days,
            },
        )
    )

    return SettlementReconciliation(
        date_from=date_from,
        date_to=date_to,
        gateway=gateway,
        tz_generation=generation,
        generated_at=_utcnow(),
        checks=tuple(checks),
    )


_CHECK_LABELS: dict[str, str] = {
    SettlementCheckKey.SETTLED_VS_CAPTURED: "Settled transactions vs captured payments",
    SettlementCheckKey.PAYOUT_IDENTITY: "Payout identity (gross - fee - tax = net)",
    SettlementCheckKey.UNMATCHED_LINES: "Settlement lines with no matching payment",
    SettlementCheckKey.UNSETTLED_PAYMENTS: "Captured payments with no settlement",
}


# ===========================================================================
# The API path, explicitly not built
# ===========================================================================


class RazorpaySettlementApiClient:
    """The shape a real Razorpay settlements client will have. It does not work.

    Razorpay exposes ``GET /v1/settlements`` and
    ``GET /v1/settlements/recon/combined?year=&month=&day=``, which returns
    exactly the rows this module already parses from CSV. Wiring it up is a
    matter of authenticating with the same key id / secret the payment provider
    uses, paging, and handing each page to :func:`ingest_settlement_csv`'s
    matching and upsert path with ``source=SettlementSource.API`` and
    ``amount_unit=AmountUnit.MINOR`` — the API reports paise.

    None of that is done here, and this class **raises** rather than returning an
    empty page, for the reason spelled out on
    :class:`SettlementApiNotImplemented`. It also makes no network call of any
    kind: Razorpay is on test keys in this deployment, the webhook path does not
    yet re-verify with the gateway, and a settlements client that quietly reached
    a live endpoint from an analytics job would be the wrong thing to discover
    later.
    """

    #: Documented so the eventual implementation does not have to rediscover it.
    RECON_ENDPOINT = "/v1/settlements/recon/combined"
    #: The API reports every amount in paise, unlike the dashboard CSV export.
    AMOUNT_UNIT = AmountUnit.MINOR

    def __init__(self, gateway: str = DEFAULT_GATEWAY) -> None:
        self.gateway = gateway

    def fetch(self, day: date) -> Sequence[ParsedSettlementRow]:
        raise SettlementApiNotImplemented(
            "There is no gateway settlements API client in this deployment. "
            f"{self.RECON_ENDPOINT} would return the same rows the CSV upload "
            "path already ingests, but nothing here authenticates against it and "
            "no credentials for it are configured. Use the CSV upload path "
            "(ingest_settlement_csv) — it needs no credentials and is what "
            "finance can do today. This raises rather than returning an empty "
            "result so that 'not built' can never be mistaken for 'the gateway "
            "settled nothing on this day'."
        )


def api_availability(db: Session) -> dict[str, Any]:
    """Everything standing between this deployment and an automated settlement feed.

    Reported as a list rather than a boolean, and deliberately not
    short-circuited, so an admin who adds one missing piece sees the *remaining*
    blocker immediately instead of discovering them one at a time. Mirrors
    ``reconciliation._ga4_unavailable_reasons``.
    """
    reasons = [
        "no gateway settlements API client exists in this deployment — "
        "RazorpaySettlementApiClient raises by design; the CSV upload path is "
        "the supported route",
        "Razorpay is configured with TEST keys here, so a live settlements "
        "endpoint would return the test account's settlements, not the store's",
    ]
    latest = db.execute(
        select(
            func.max(PaymentSettlement.payment_date),
            func.count(PaymentSettlement.id),
        )
    ).one()
    return {
        "available": False,
        "capability": "gateway_settlement_api",
        "reasons": reasons,
        "csv_upload_available": True,
        "settlement_rows": int(latest[1] or 0),
        "settled_through": latest[0].isoformat() if latest[0] else None,
    }


# ===========================================================================
# Small helpers
# ===========================================================================


def _window(
    date_from: date, date_to: date, gateway: str, generation: int
) -> list[Any]:
    """The payment-dated window. Fees and matching are payment-dated, always."""
    return [
        PaymentSettlement.gateway == gateway,
        PaymentSettlement.payment_date >= date_from,
        PaymentSettlement.payment_date < date_to,
        PaymentSettlement.tz_generation == generation,
    ]


def _case_when(condition: Any, value: Any) -> Any:
    """``CASE WHEN cond THEN value ELSE 0 END`` — a conditional summand.

    ``ELSE 0`` rather than ``ELSE NULL`` because these feed ``SUM()`` over a
    population that is real even when the condition never holds: a day whose
    settlement lines are all unmatched has a matched fee of zero, which is a
    measurement, not a missing value.
    """
    return case((condition, value), else_=0)


def _coverage(compared: int, population: int) -> Decimal | None:
    if not population:
        return None
    return (Decimal(compared) / Decimal(population) * 100).quantize(Decimal("0.01"))


def _pct(difference: Decimal | None, base: Decimal | None) -> Decimal | None:
    """Relative error, or None when there is no base to be relative to.

    None rather than 0: 0.0000% must only ever mean "compared, and equal".
    """
    if difference is None or not base:
        return None
    return (difference / base * 100).quantize(Decimal("0.0001"))


def _utcnow() -> datetime:
    """Naive UTC, matching every other analytics timestamp in this stack."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive(moment: datetime) -> datetime:
    return moment.replace(tzinfo=None) if moment.tzinfo is not None else moment


def _aware(moment: datetime) -> datetime:
    """Attach UTC to a naive instant so ``timebox`` can convert it correctly."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
