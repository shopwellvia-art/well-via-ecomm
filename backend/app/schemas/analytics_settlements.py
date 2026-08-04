"""Request/response models for the settlement upload and status admin API.

Two shapes, and one rule each is built to make ungettable-wrong:

**The upload response reports facts about THIS call.** ``rows_new`` is the
number of rows that did not exist before the call — zero on a re-upload of an
identical file, which is the number that proves idempotency to the person
staring at the screen wondering whether they just double-counted a month of
fees. ``rows_parsed`` is the file; the two are different questions and both are
answered.

**Money is integer paise here, matching the fact table.** ``payment_settlements``
stores signed integer paise because its whole purpose is a paisa-exact equality
against a third party's arithmetic, and this response quotes those sums as the
service produced them. Fields are suffixed ``_minor`` so nobody reads 472000 as
rupees. Rates (``match_rate_pct``) are ``Decimal``, never float.

``amount_unit`` is a required *request* field with no default. The dashboard CSV
export is rupees and the settlements API is paise; getting it wrong is a 100x
error that reconciles perfectly against itself, so the parser refuses to infer
it and this schema refuses to assume it.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import Field
from app.schemas.base import AppSchema

#: Hard ceiling on an uploaded settlement file. A year of settlements for this
#: store is well under a megabyte; 10 MiB accommodates a big exporter without
#: letting an upload endpoint double as a disk-filling primitive.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class SettlementUploadResponse(AppSchema):
    """What one upload did — every number is a fact about this call."""

    gateway: str
    source_file: str
    amount_unit: str
    #: Data rows the file contained, all of which validated.
    rows_parsed: int
    #: Rows that did not exist before this call. Zero on a re-upload.
    rows_new: int
    matched: int
    unmatched: int
    ambiguous: int
    #: Store-local payment days this file touched (the fee side).
    payment_dates: list[date]
    #: Store-local settlement days this file touched (the cash side).
    settlement_dates: list[date]
    #: Signed sums over the file, integer paise.
    gross_minor: int
    fee_minor: int
    tax_minor: int
    net_minor: int
    #: Buckets enqueued for the `settlement_daily` rollup rebuild.
    recompute_queued: int
    recompute_jobs: list[str]
    warnings: list[str] = Field(default_factory=list)


class SettlementLastUpload(AppSchema):
    """Provenance of the most recently ingested settlement line."""

    source_file: str
    source: str
    ingested_at: datetime
    rows_in_file: int


class SettlementGatewayStatus(AppSchema):
    """Match-rate and coverage for one gateway's ingested lines."""

    gateway: str
    settlement_rows: int
    matched: int
    unmatched: int
    ambiguous: int
    #: matched / total, 0..100. None when there are no rows to rate.
    match_rate_pct: Decimal | None
    #: Earliest and latest payment-dated coverage.
    first_payment_date: date | None
    last_payment_date: date | None
    #: Latest settlement-dated line — how far the cash side reaches.
    settled_through: date | None


class SettlementStatusResponse(AppSchema):
    """Everything the admin UI needs to say where the settlement feed stands.

    ``api`` is `settlements.api_availability()` verbatim: the reasons the
    automated feed does not exist, stated as a list so an admin who fixes one
    blocker sees the remaining ones. ``report_capability_satisfied`` is the
    runtime answer to the capability view 64 is keyed on — true the moment any
    settlement row exists.
    """

    settlement_rows: int
    report_capability_satisfied: bool
    gateways: list[SettlementGatewayStatus]
    last_upload: SettlementLastUpload | None
    csv_upload_available: bool = True
    api: dict
