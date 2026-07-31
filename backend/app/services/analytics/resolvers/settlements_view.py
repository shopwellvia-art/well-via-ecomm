"""View 64 (Settlements and Payouts) — the read side of the settlement subsystem.

One custom function, ``settlements_payouts``, dispatched exactly like the other
custom views (63 ``reconciliation_grid``, 62 ``tracking_health``): the name is a
SERVER-TRUSTED registry value and this module registers it at import via
``special.custom_function``.

What it reads
-------------
* ``agg_settlement_daily`` through the generic repository allowlist — the daily
  table and both charts. The two column families are kept strictly apart:
  ``*_transacted`` (payment-date bucketed, the fee side) feeds ``fees_trend``
  and the fee columns; ``*_settled`` (settlement-date bucketed, the cash side)
  feeds ``payout_trend`` and the payout columns. They are **never summed
  together** — on any real day they are different populations of the same
  lines, and adding them counts most transactions twice.
* ``settlements.settlement_reconciliation`` — the four checks, always all four,
  rendered through ``SettlementCheck.to_row`` which matches
  ``reconciliation.CheckResult.to_row`` field for field. A check that could not
  run appears as ``not_configured`` with NULL values, never as a 0.00% variance.
* ``settlements.unmatched_settlements`` / ``unsettled_captured_payments`` — the
  two gaps, as first-class table content. They are the product of this view,
  not footnotes: money the gateway paid us that we cannot explain, and money we
  captured that the gateway has not paid us.

The runtime probe
-----------------
The registry declares PARTIAL — the ceiling: CSV-fed, fees ACTUAL only on fully
covered days. When ``payment_settlements`` holds no rows at all (for the
requested gateway, or any gateway when none is requested) this function returns
``not_configured`` naming ``gateway_settlement_report`` — the same runtime
"downgrade to the gated answer" the other probed views make before their first
data arrives. That refusal carries an explicitly empty ``sources`` list, so
"no report has ever been uploaded" can never render as "everything settled".

The fact-table existence probe reads ``payment_settlements`` directly, the same
route ``risk.py`` and ``control_centre.py`` take to tables outside the rollup
allowlist. It is a bounded ``LIMIT 1`` — never a count over the whole table.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.models.analytics_settlement import PaymentSettlement
from app.schemas.analytics_view import AnalyticsWarning, TableBlock
from app.services.analytics.contracts import from_minor
from app.services.analytics.export import clamp_row_limit
from app.services.analytics.resolvers.base import (
    ResolverContext,
    ResolverResult,
    not_configured,
    probe_source,
    warn,
)
from app.services.analytics.resolvers.special import custom_function
from app.services.analytics.settlements import (
    settlement_reconciliation,
    unmatched_settlements,
    unsettled_captured_payments,
)
from app.services.analytics.types import Capability, MetricQuality

__all__ = ["settlements_payouts", "SETTLEMENT_SOURCE"]

SETTLEMENT_SOURCE = "agg_settlement_daily"

#: Warning code for the two reconciliation gaps. Distinct from NOT_CONFIGURED —
#: a gap is a *finding* on real data, not an absence of data.
SETTLEMENT_GAP = "SETTLEMENT_GAP"

#: The payment-dated family, exactly as `AggSettlementDaily` names it.
_TRANSACTED_COLUMNS: tuple[str, ...] = (
    "txns_transacted",
    "gross_transacted",
    "fee_transacted",
    "tax_transacted",
    "matched_txns",
    "unmatched_txns",
    "unsettled_payments",
    "unsettled_amount",
)

#: The settlement-dated family. Kept in a separate tuple so no future edit can
#: accidentally interleave the two populations into one sum.
_SETTLED_COLUMNS: tuple[str, ...] = (
    "txns_settled",
    "settlement_batches",
    "payout_amount",
)


def _gateway_filter(ctx: ResolverContext) -> str | None:
    """The requested gateway, or None for "every gateway"."""
    value = (ctx.filters.payment_gateway or "").strip()
    return value or None


def _report_exists(ctx: ResolverContext, gateway: str | None) -> bool:
    """Whether ANY settlement row exists (for the gateway, if one is named).

    A bounded existence probe on the fact table, not the rollup: the rollup can
    legitimately lag an upload until the `settlement_daily` job runs, and the
    honest gate is "has a report ever been ingested", which only
    `payment_settlements` can answer.
    """
    stmt = select(PaymentSettlement.id).limit(1)
    if gateway:
        stmt = stmt.where(PaymentSettlement.gateway == gateway)
    return ctx.db.execute(stmt).first() is not None


@custom_function("settlements_payouts")
def settlements_payouts(ctx: ResolverContext) -> ResolverResult:
    gateway = _gateway_filter(ctx)

    if not _report_exists(ctx, gateway):
        scope = f"gateway {gateway!r}" if gateway else "any gateway"
        return not_configured(
            f"No settlement report has ever been ingested for {scope}. "
            + (
                ctx.view.limitation
                or "Upload the gateway settlement report CSV via "
                "POST /analytics/admin/settlements/upload."
            ),
            requires=[Capability.GATEWAY_SETTLEMENT_REPORT.value],
            detail={"view": ctx.view.slug},
        )

    state = probe_source(ctx, SETTLEMENT_SOURCE, label="Settlements (daily)")
    warnings: list[AnalyticsWarning] = list(state.warnings)

    series: dict[str, list[dict]] = {}
    tables: dict[str, TableBlock] = {}

    if state.has_rows:
        row_filters = {"gateway": gateway} if gateway else None
        rows = ctx.repo.fetch_rollup(
            SETTLEMENT_SOURCE,
            columns=list(_TRANSACTED_COLUMNS + _SETTLED_COLUMNS),
            window=ctx.window,
            tz_generation=ctx.tz_generation,
            group_by=["bucket_date", "gateway"],
            filters=row_filters,
            order_by="bucket_date",
        )
        if rows:
            tables["settlement_days"] = _daily_table(rows)
            series.update(_charts(ctx, rows))

    # The reconciliation and the two gap tables come from the fact table via
    # the settlements service, so they are current the moment a file is
    # ingested — deliberately not gated on the rollup having caught up.
    recon = settlement_reconciliation(
        ctx.db,
        ctx.window.date_from,
        ctx.window.date_to,
        **({"gateway": gateway} if gateway else {}),
    )
    tables["variances"] = TableBlock(
        rows=recon.to_rows(), total_rows=len(recon.checks), truncated=False
    )

    limit = clamp_row_limit(ctx.filters.limit)
    orphan_rows = _unmatched_table(ctx, gateway, limit)
    tables["unmatched_lines"] = orphan_rows
    outstanding_rows = _unsettled_table(ctx, gateway, limit)
    tables["unsettled_payments"] = outstanding_rows

    quality = _quality(recon)
    warnings.extend(_gap_warnings(orphan_rows, outstanding_rows))

    return ResolverResult(
        series=series,
        tables=tables,
        sources=[state.ref],
        warnings=warnings,
        # Never AUTHORITATIVE: every figure here is a third party's report.
        # ACTUAL only when every line matched and every capture is explained;
        # any gap makes the window's settlement picture INCOMPLETE.
        quality=quality,
    ).rolled_up()


# ---------------------------------------------------------------------------
# Shaping
# ---------------------------------------------------------------------------


def _daily_table(rows: list[dict]) -> TableBlock:
    shaped = [
        {
            "date": _day(row.get("bucket_date")),
            "gateway": row.get("gateway"),
            # payment-dated family only:
            "gross_transacted": row.get("gross_transacted"),
            "fee_transacted": row.get("fee_transacted"),
            "tax_transacted": row.get("tax_transacted"),
            "matched_txns": _int(row.get("matched_txns")),
            "unmatched_txns": _int(row.get("unmatched_txns")),
            "unsettled_payments": _int(row.get("unsettled_payments")),
            # settlement-dated family only:
            "payout_amount": row.get("payout_amount"),
            "settlement_batches": _int(row.get("settlement_batches")),
        }
        for row in rows
    ]
    return TableBlock(rows=shaped, total_rows=len(shaped), truncated=False)


def _charts(ctx: ResolverContext, rows: list[dict]) -> dict[str, list[dict]]:
    """Both charts, each from exactly one column family.

    Points are emitted only for days the rollup holds — never densified to
    zero, because a day with no row means "no settlement information", which is
    not a measured zero fee or a measured zero payout.
    """
    by_day: dict[str, dict[str, Decimal]] = {}
    for row in rows:
        day = _day(row.get("bucket_date"))
        acc = by_day.setdefault(
            day,
            {"fee_transacted": Decimal("0"), "tax_transacted": Decimal("0"),
             "payout_amount": Decimal("0")},
        )
        for key in ("fee_transacted", "tax_transacted", "payout_amount"):
            acc[key] += Decimal(str(row.get(key) or 0))

    fee_points = [
        {"date": day, "fee_transacted": v["fee_transacted"],
         "tax_transacted": v["tax_transacted"]}
        for day, v in sorted(by_day.items())
    ]
    payout_points = [
        {"date": day, "payout_amount": v["payout_amount"]}
        for day, v in sorted(by_day.items())
    ]

    series: dict[str, list[dict]] = {}
    for chart in ctx.view.charts:
        if chart.id == "fees_trend":
            series[chart.id] = fee_points
        elif chart.id == "payout_trend":
            series[chart.id] = payout_points
    return series


def _unmatched_table(
    ctx: ResolverContext, gateway: str | None, limit: int
) -> TableBlock:
    orphans = unmatched_settlements(
        ctx.db,
        ctx.window.date_from,
        ctx.window.date_to,
        **({"gateway": gateway} if gateway else {}),
        tz_generation=ctx.tz_generation,
        limit=limit + 1,
    )
    truncated = len(orphans) > limit
    rows = [
        {
            "transaction_id": line.transaction_id,
            "transaction_type": line.transaction_type,
            "payment_date": line.payment_date.isoformat(),
            "gross": from_minor(int(line.gross_minor)),
            "fee": from_minor(int(line.fee_minor)),
            "match_status": line.match_status,
            "reference": line.gateway_reference or line.merchant_transaction_id or "-",
        }
        for line in orphans[:limit]
    ]
    return TableBlock(rows=rows, total_rows=len(rows), truncated=truncated)


def _unsettled_table(
    ctx: ResolverContext, gateway: str | None, limit: int
) -> TableBlock:
    outstanding = unsettled_captured_payments(
        ctx.db,
        ctx.window.date_from,
        ctx.window.date_to,
        **({"gateway": gateway} if gateway else {}),
        limit=limit + 1,
    )
    truncated = len(outstanding) > limit
    rows = [
        {
            "order_id": payment.order_id,
            "order_payment_id": payment.order_payment_id,
            "gateway": payment.gateway,
            "amount": from_minor(payment.amount_minor),
            "paid_at": payment.paid_at.isoformat(),
            "days_outstanding": payment.days_outstanding,
        }
        for payment in outstanding[:limit]
    ]
    return TableBlock(rows=rows, total_rows=len(rows), truncated=truncated)


def _quality(recon: Any) -> MetricQuality:
    """ACTUAL only when every check ran and matched; INCOMPLETE otherwise.

    The service's own rule projected onto the view: fees are ACTUAL only at
    full coverage, and full coverage is exactly "no unmatched lines, no
    unsettled captures, and a report actually covering the window". A window
    with a `not_configured` check has an unknown side, which is INCOMPLETE —
    never ESTIMATED, because the shortfall is a measurement gap.
    """
    from app.services.analytics.reconciliation import CheckStatus

    statuses = {check.status for check in recon.checks}
    if statuses == {CheckStatus.MATCH}:
        return MetricQuality.ACTUAL
    return MetricQuality.INCOMPLETE


def _gap_warnings(
    orphans: TableBlock, outstanding: TableBlock
) -> list[AnalyticsWarning]:
    warnings: list[AnalyticsWarning] = []
    if orphans.rows:
        warnings.append(
            warn(
                SETTLEMENT_GAP,
                f"{len(orphans.rows)} settlement line(s) in this window could not "
                "be tied to any payment we recorded. They are listed in full with "
                "their money and dates intact — a finding, not an ingest failure.",
                severity="warn",
                unmatched_lines=len(orphans.rows),
            )
        )
    if outstanding.rows:
        warnings.append(
            warn(
                SETTLEMENT_GAP,
                f"{len(outstanding.rows)} captured payment(s) in this window have "
                "no settlement line after the gateway's settlement cycle. This is "
                "money the store believes it is owed.",
                severity="warn",
                unsettled_payments=len(outstanding.rows),
            )
        )
    return warnings


def _day(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _int(value: Any) -> int:
    return int(value or 0)
