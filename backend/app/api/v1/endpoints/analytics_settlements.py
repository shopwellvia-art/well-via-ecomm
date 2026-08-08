"""Settlement report upload and status — the admin surface for view 64's data.

Routes
------
==================================================== ====== ========================
POST   /admin/settlements/upload                     201    ingest one report CSV
GET    /admin/settlements/status                     200    match-rate + coverage
==================================================== ====== ========================

Why this module exists
----------------------
``payment_settlements``, ``agg_settlement_daily`` and the whole ingest/matching
service (``services/analytics/settlements.py``) have existed since the schema
landed, and nothing could put a row in them: the settlements API client raises
by design and no upload route was mounted. The consequence is the same one the
cost-admin module names — every gateway fee stays ESTIMATED forever and view 64
stays gated — not because the reconciliation is wrong but because finance has
no way to hand the system the file they already download every week.

All of it, or none of it
------------------------
The service parses and validates the ENTIRE file before a single INSERT — a
file with one bad amount on row 40 writes zero rows. This endpoint leans on
that guarantee rather than restating it: a ``SettlementIngestError`` maps to a
422 whose detail lists every problem found, and the table is byte-identical
afterwards. The API test asserts that through this route, not just the service.

Idempotency is the response's headline number: ``rows_new`` is 0 on a re-upload
of an identical file, because the upsert key is the gateway's own transaction
id. Finance WILL upload the same file twice, and the second upload must be a
visible no-op, not a doubled fee.

Every upload enqueues
---------------------
A settlement file changes buckets days apart — the payment dates it covers
(the fee side) and the settlement dates (the cash side). ``IngestResult.dirty_dates``
lists exactly which; each is enqueued for the ``settlement_daily`` rollup with
reason ``PAYMENT_SETTLEMENT``, so the view catches up when the worker drains
the queue rather than when someone notices it is stale. The heavy aggregation
never runs in this request worker.

Auth
----
Reads need ``analytics.finance.view`` — what the gateway charged the store is a
margin input in the same SENSITIVE tier as the margin itself. Writes need
``analytics.budgets.manage``, the existing manage-level grant this subsystem
already uses for cost/spend entry (see ``analytics_cost_admin.py``): there is
no settlements permission in ``services/permissions_registry.py``, and minting
a new name here would leave it unseeded on every existing deployment — a
permission no role holds is an endpoint nobody can call, including the owner.

``require_permission`` lets ``is_admin`` bypass every check, so the tests for
this module use non-admin users with explicitly seeded grants and assert the
permission lookup itself, per the cost-admin precedent.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, rate_limit_by_ip, require_permission
from app.core.exceptions import ValidationError
from app.core.rate_limit import get_client_ip
from app.models.analytics_control import RecomputeReason
from app.models.analytics_settlement import PaymentSettlement, SettlementMatchStatus
from app.models.user import User
from app.schemas.analytics_settlements import (
    MAX_UPLOAD_BYTES,
    SettlementGatewayStatus,
    SettlementLastUpload,
    SettlementStatusResponse,
    SettlementUploadResponse,
)
from app.services.analytics.queue import RecomputeQueue
from app.services.analytics.settlements import (
    DEFAULT_GATEWAY,
    AmountUnit,
    SettlementIngestError,
    api_availability,
    ingest_settlement_csv,
)
from app.services.audit_service import AuditService

router = APIRouter()

#: Same tier as the margin views this data feeds. Reading match rates discloses
#: gateway economics, which is finance-sensitive.
READ_PERMISSION = "analytics.finance.view"

#: The existing manage-level grant; see the module docstring for why no new
#: permission name is minted here.
WRITE_PERMISSION = "analytics.budgets.manage"

#: The rollup job an upload dirties. Validated against the JOBS registry at
#: enqueue time so a rename can never write queue rows nothing will claim.
SETTLEMENT_JOB = "settlement_daily"

#: Uploads parse and match a whole file and can enqueue days of recompute.
#: Ten per five minutes covers a finance person working through a backlog of
#: monthly reports; a stolen session cannot use this as a load generator.
_UPLOAD_RATE_LIMIT = Depends(
    rate_limit_by_ip(scope="analytics.settlements.upload.ip", limit=10, window_sec=300)
)
#: Status is one aggregate query; a dashboard may poll it.
_STATUS_RATE_LIMIT = Depends(
    rate_limit_by_ip(scope="analytics.settlements.status.ip", limit=300, window_sec=300)
)


# ===========================================================================
# POST /admin/settlements/upload
# ===========================================================================
@router.post(
    "/admin/settlements/upload",
    response_model=SettlementUploadResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_UPLOAD_RATE_LIMIT],
)
async def upload_settlement_report(
    request: Request,
    file: UploadFile = File(..., description="The gateway settlement report CSV."),
    amount_unit: str = Form(
        ...,
        description=(
            "Whether the file's money columns are rupees ('major' — the "
            "dashboard export) or paise ('minor' — the settlements API shape). "
            "Never inferred: reading paise as rupees is a 100x error that "
            "reconciles perfectly against itself."
        ),
    ),
    gateway: str = Form(default=DEFAULT_GATEWAY, max_length=40),
    actor: User = Depends(require_permission(WRITE_PERMISSION)),
    db: Session = Depends(get_db),
):
    """Ingest one settlement report: parse it all, match it all, or write nothing.

    The unit of work is the file. Ingest, recompute enqueue and the audit row
    land in ONE transaction, so a failure anywhere leaves no half-recorded
    upload — no settlement rows without queue rows, no audit row for an ingest
    that rolled back.
    """
    if amount_unit not in AmountUnit.ALL:
        raise ValidationError(
            f"amount_unit must be one of {list(AmountUnit.ALL)}, got {amount_unit!r}. "
            "It is never inferred from the values.",
            details={"field": "amount_unit", "allowed": list(AmountUnit.ALL)},
        )
    gateway = (gateway or "").strip().lower() or DEFAULT_GATEWAY

    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"The uploaded file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB. "
            "A settlement report is a small CSV; nothing was ingested.",
            details={"max_bytes": MAX_UPLOAD_BYTES},
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError(
            "The uploaded file is not UTF-8 text. Re-export the settlement "
            "report as CSV; nothing was ingested.",
            details={"decode_error": str(exc)},
        ) from None

    source_file = (file.filename or "-").strip() or "-"

    try:
        # commit=False: the ingest joins this endpoint's transaction so the
        # settlement rows, the queue rows and the audit row commit together.
        result = ingest_settlement_csv(
            db,
            text,
            amount_unit=amount_unit,
            gateway=gateway,
            source_file=source_file,
            commit=False,
        )
    except SettlementIngestError as exc:
        db.rollback()
        raise ValidationError(
            str(exc),
            details={"problems": list(exc.problems), "source_file": source_file},
        ) from None

    queued, jobs = _enqueue_settlement_recompute(db, result.dirty_dates)

    AuditService(db).record(
        actor=actor,
        actor_ip=get_client_ip(request),
        action="analytics.settlements.upload",
        target_type="payment_settlements",
        target_label=f"{gateway}/{source_file}",
        summary=(
            f"Uploaded {gateway} settlement report {source_file!r}: "
            f"{result.rows_parsed} row(s) ({result.rows_new} new), "
            f"{result.matched} matched, {result.unmatched} unmatched, "
            f"{result.ambiguous} ambiguous"
        ),
        extra={
            "source_file": source_file,
            "gateway": gateway,
            "amount_unit": amount_unit,
            "bytes": len(raw),
            "rows_parsed": result.rows_parsed,
            "rows_new": result.rows_new,
            "matched": result.matched,
            "unmatched": result.unmatched,
            "ambiguous": result.ambiguous,
            "gross_minor": result.gross_minor,
            "fee_minor": result.fee_minor,
            "tax_minor": result.tax_minor,
            "net_minor": result.net_minor,
            "payment_dates": [d.isoformat() for d in result.payment_dates],
            "settlement_dates": [d.isoformat() for d in result.settlement_dates],
            "recompute": {"queued": queued, "jobs": jobs},
        },
    )
    db.commit()

    return SettlementUploadResponse(
        gateway=result.gateway,
        source_file=result.source_file,
        amount_unit=amount_unit,
        rows_parsed=result.rows_parsed,
        rows_new=result.rows_new,
        matched=result.matched,
        unmatched=result.unmatched,
        ambiguous=result.ambiguous,
        payment_dates=list(result.payment_dates),
        settlement_dates=list(result.settlement_dates),
        gross_minor=result.gross_minor,
        fee_minor=result.fee_minor,
        tax_minor=result.tax_minor,
        net_minor=result.net_minor,
        recompute_queued=queued,
        recompute_jobs=jobs,
        warnings=list(result.warnings),
    )


def _enqueue_settlement_recompute(db: Session, dirty_dates) -> tuple[int, list[str]]:
    """Mark every bucket this upload changed as dirty. Flushes, never commits.

    Only the registered job is enqueued — a queue row for an unregistered job
    is a permanent invisible backlog that reads as a successful write. Imported
    lazily for the same reason ``analytics_cost_admin`` does: pulling the whole
    aggregation package in at router import would tax every request worker.
    """
    if not dirty_dates:
        return 0, []
    from app.services.analytics.aggregation import JOBS

    if SETTLEMENT_JOB not in JOBS:
        return 0, []
    queued = RecomputeQueue(db).enqueue_many(
        ((SETTLEMENT_JOB, day) for day in dirty_dates),
        reason=RecomputeReason.PAYMENT_SETTLEMENT,
    )
    return queued, [SETTLEMENT_JOB]


# ===========================================================================
# GET /admin/settlements/status
# ===========================================================================
@router.get(
    "/admin/settlements/status",
    response_model=SettlementStatusResponse,
    dependencies=[Depends(require_permission(READ_PERMISSION)), _STATUS_RATE_LIMIT],
)
def settlement_status(db: Session = Depends(get_db)):
    """Where the settlement feed stands: match rate, coverage, last upload.

    Per gateway, because a second gateway would otherwise hide inside a blended
    match rate. ``report_capability_satisfied`` is the runtime answer to the
    ``gateway_settlement_report`` capability view 64 is keyed on, and ``api``
    is the unedited list of reasons the automated feed does not exist yet.
    """
    matched = _status_case(SettlementMatchStatus.MATCHED)
    unmatched = _status_case(SettlementMatchStatus.UNMATCHED)
    ambiguous = _status_case(SettlementMatchStatus.AMBIGUOUS)

    rows = db.execute(
        select(
            PaymentSettlement.gateway,
            func.count(PaymentSettlement.id).label("rows"),
            func.coalesce(func.sum(matched), 0).label("matched"),
            func.coalesce(func.sum(unmatched), 0).label("unmatched"),
            func.coalesce(func.sum(ambiguous), 0).label("ambiguous"),
            func.min(PaymentSettlement.payment_date).label("first_payment"),
            func.max(PaymentSettlement.payment_date).label("last_payment"),
            func.max(PaymentSettlement.settlement_date).label("settled_through"),
        ).group_by(PaymentSettlement.gateway)
    ).all()

    gateways = [
        SettlementGatewayStatus(
            gateway=row.gateway,
            settlement_rows=int(row.rows or 0),
            matched=int(row.matched or 0),
            unmatched=int(row.unmatched or 0),
            ambiguous=int(row.ambiguous or 0),
            match_rate_pct=_rate(int(row.matched or 0), int(row.rows or 0)),
            first_payment_date=row.first_payment,
            last_payment_date=row.last_payment,
            settled_through=row.settled_through,
        )
        for row in sorted(rows, key=lambda r: r.gateway)
    ]
    total_rows = sum(g.settlement_rows for g in gateways)

    return SettlementStatusResponse(
        settlement_rows=total_rows,
        report_capability_satisfied=total_rows > 0,
        gateways=gateways,
        last_upload=_last_upload(db),
        csv_upload_available=True,
        api=api_availability(db),
    )


def _status_case(match_status: str):
    return case((PaymentSettlement.match_status == match_status, 1), else_=0)


def _rate(matched: int, total: int) -> Decimal | None:
    """matched/total as a percentage, or None over an empty population.

    None rather than 0: a gateway with no rows has no match rate, and 0.00%
    would read as "everything failed to match".
    """
    if total <= 0:
        return None
    return (Decimal(matched) / Decimal(total) * 100).quantize(Decimal("0.01"))


def _last_upload(db: Session) -> SettlementLastUpload | None:
    """Provenance of the newest ingested line, plus how many lines share its file.

    ``created_at`` records when a line was FIRST seen (the upsert never rewrites
    it), so this is genuinely "the newest information", not "the last button
    press" — a re-upload of an old file does not move it.
    """
    newest = db.execute(
        select(
            PaymentSettlement.source_file,
            PaymentSettlement.source,
            PaymentSettlement.created_at,
        )
        .order_by(PaymentSettlement.created_at.desc(), PaymentSettlement.id.desc())
        .limit(1)
    ).first()
    if newest is None:
        return None
    rows_in_file = int(
        db.execute(
            select(func.count(PaymentSettlement.id)).where(
                PaymentSettlement.source_file == newest.source_file
            )
        ).scalar_one()
        or 0
    )
    return SettlementLastUpload(
        source_file=newest.source_file,
        source=newest.source,
        ingested_at=newest.created_at,
        rows_in_file=rows_in_file,
    )
