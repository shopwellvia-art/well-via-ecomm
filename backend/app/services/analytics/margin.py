"""Contribution-margin cascade (CM1/CM2/CM3) and the revenue reconciliation bridge.

This replaces ``app/services/profit_service.py``. It exists because that service
has two confirmed defects that both push reported margin in the *flattering*
direction — the one direction of error nobody investigates.

Inherited bug 1: shipping charged twice
---------------------------------------
``profit_service.py`` defines revenue as ``SUM(quantity * unit_price)`` (line
level, so shipping income is **excluded**), and then computes
``c1 = revenue - product_cost - shipping_cost - ...`` where ``shipping_cost`` is
``SUM(orders.shipping_amount)`` — the amount the **customer paid us**. Shipping is
therefore left out of revenue *and* deducted as an expense, a 2x penalty, while
``shipments.shipment_cost`` — the real carrier charge, the only genuine cost in
the picture — is never read at all.

Here ``orders.shipping_amount`` is income: it is added inside CM2 and never
subtracted anywhere. The carrier charge is a separate component sourced from
``shipments.shipment_cost`` where a real figure exists, and from a
FORWARD_SHIPPING cost rule only for the orders that have none.

Inherited bug 2: missing product cost silently zeroed
-----------------------------------------------------
``profit_service.py`` does ``func.coalesce(OrderItem.unit_cost, 0)``, so a line
with no cost snapshot contributes zero COGS and reports a 100% margin on itself.
Here the COGS sum is a plain ``SUM(quantity * unit_cost)``: SQL drops NULL rows
from the sum, and those same rows still count in the denominator of
``cost_coverage_pct``. Coverage below 100 makes the whole result INCOMPLETE.
Nothing is ever zero-filled.

Own bug, fixed here: revenue reversed twice
-------------------------------------------
This service used to define its revenue set as ``_REVENUE_STATUSES`` and then
*also* subtract ``refunds``. A refunded order is not in that status set, so the
same reversal was applied twice: once by dropping the order, once by the
subtraction. An order placed and fully refunded inside one window reported::

    gross_merchandise_sales :     0.00
    refunds                 :  1000.00
    net_revenue             : -1000.00   <- should be 0.00
    bridge balances         :    True    <- the identity did NOT catch it

The bridge balanced throughout, because both sides of the identity were built
from the same wrong input. A self-proving identity proves nothing about the
input it shares — which is why ``test_analytics_revenue_recognition.py`` asserts
absolute figures and treats ``balances()`` as necessary, never sufficient.

The second half was quieter and worse. ``orders.status`` is MUTABLE and
``REFUNDED`` is terminal, so a January sale refunded in March disappeared from
January retroactively: a closed month moved because of an event two months
later.

Recognition: the sale in its period, the reversal in its own
------------------------------------------------------------
A sale is recognised in the period of ``orders.created_at`` when the order
reached a paid state — **including** orders that have since been refunded. The
reversal is a separate event recognised in the period of its own ``refunded_at``
(``returns.refunded_at`` for returns-driven refunds). One window containing both
nets to zero; January keeps its sale and March takes the reversal.

Recognition is keyed to the reversal being *recognisable*, not to the order
having once been paid, and the two are not the same thing. A PAID order
cancelled by the customer has ``paid_at`` set and the money really did go back,
but ``OrderService._cancel_core`` stamps ``cancelled_at`` and never
``refunded_at`` — so no refund event exists to date. Recognising that sale would
book revenue that nothing ever reverses. ``_recognised_sale()`` therefore admits
a ``REFUNDED`` order only when a reversal can actually be dated for it, which
keeps the two sides symmetric by construction.

Two revenue definitions, deliberately
-------------------------------------
``paid_order_value`` keeps the LEGACY definition — refunded orders leave the set
entirely — because it is the shadow-mode parity anchor against
``DashboardService._revenue_summary``. If it moved, the reconciliation between
the legacy pages and this subsystem would stop meaning anything and there would
be no way left to prove the new numbers are right. ``_REVENUE_STATUSES`` is
imported for that one purpose and for nothing else; it is never restated and
never modified, because the legacy admin pages are still the operational source
of truth and changing it would silently move the numbers on them.

``gross_merchandise_sales``, ``net_merchandise_sales`` and ``net_revenue`` use
the corrected recognition. Both definitions are stated in ``kpis.py`` under
their own ids, exactly as the two pre-existing revenue bases already are.

The cascade, exactly as ``kpis.py`` declares it
-----------------------------------------------
::

    CM1 = net_merchandise_sales - COGS
    CM2 = CM1 + shipping_income
              - gateway_fee - packaging - handling
              - forward_shipping - return_shipping - rto_logistics
              - marketplace_commission
    CM3 = CM2 - marketing_spend

``net_merchandise_sales`` is line revenue less the order-level discount and
payment discount. The store-level total needs no allocation: the per-line shares
``allocation.py`` produces sum back to the order amount by construction, so
splitting and re-summing them would be arithmetic with no information in it.
``allocation.py`` is required only for a per-product/category/SKU cut.

Missing is never zero
---------------------
Every cost input is resolved through :class:`CostRuleResolver`. A component whose
rule is absent comes back with ``value_minor is None``; that makes the **whole CM
level** ``None``, adds the component's name to ``missing_inputs`` and forces the
result's quality to INCOMPLETE. CM2 is never quietly reported as CM1 because a
gateway rule was never entered.

A cost is only *needed* on a day whose driver is non-zero, and a driver measured
at zero is a real measurement rather than an absence. A day with only COD orders
collected no money through a gateway, so it owes no gateway fee and needs no
gateway rule; a window with no reverse pickups owes no return-shipping cost. This
is the one place a zero appears without a rule, and it is a zero *quantity*, not
a zero *rate*.

Money
-----
Integer paise end to end via ``contracts.to_minor``/``from_minor``. Sums are done
by MySQL over DECIMAL columns and converted once at the boundary. No float ever
touches a money value; ``to_minor`` raises on one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

import redis
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.models.analytics_control import CostScope, CostType
from app.models.order import Order, OrderItem, OrderStatus
from app.models.return_request import ReturnRequest
from app.models.shipment import Shipment, ShipmentStatus
from app.services.analytics.contracts import (
    CostComponent,
    MarginResult,
    RevenueBridge,
    from_minor,
    to_minor,
)
from app.services.analytics.cost_rules import CostRuleResolver
from app.services.analytics.types import MetricQuality, worst_quality
from app.services.dashboard_service import _REVENUE_STATUSES

__all__ = [
    "MarginService",
    "RevenueRecognition",
    "COGS",
    "SHIPPING_INCOME",
    "CM2_COST_TYPES",
    "CM3_COST_TYPES",
]

#: Pseudo cost-type ids for the two cascade inputs that are not cost rules. They
#: ride in `MarginResult.components` alongside the resolved rules so `waterfall()`
#: can be a pure function of the result — the view never needs a second query.
COGS = "cogs"
SHIPPING_INCOME = "shipping_income"

#: Subtracted inside CM2, in the order the waterfall renders them. Mirrors the
#: `cm2` formula in kpis.py; the catalogue's single `return_rto_cost` term is
#: reported here as its two underlying rules so an admin can see which is absent.
CM2_COST_TYPES: tuple[str, ...] = (
    CostType.GATEWAY_FEE,
    CostType.PACKAGING,
    CostType.HANDLING,
    CostType.FORWARD_SHIPPING,
    CostType.RETURN_SHIPPING,
    CostType.RTO_LOGISTICS,
    CostType.MARKETPLACE_COMMISSION,
)

#: Subtracted inside CM3. kpis.py calls this `attributable_marketing_spend`;
#: without an ad-platform connection the only source is a MARKETING_SPEND rule,
#: which is a store-level blended figure and not campaign-attributed.
CM3_COST_TYPES: tuple[str, ...] = (CostType.MARKETING_SPEND,)

#: The one component that is added rather than subtracted.
INCOME_COMPONENTS: frozenset[str] = frozenset({SHIPPING_INCOME})

_LABELS: dict[str, str] = {
    COGS: "COGS",
    SHIPPING_INCOME: "Shipping income",
    CostType.GATEWAY_FEE: "Gateway fees",
    CostType.PACKAGING: "Packaging",
    CostType.HANDLING: "Fulfilment / handling",
    CostType.FORWARD_SHIPPING: "Forward shipping",
    CostType.RETURN_SHIPPING: "Return shipping",
    CostType.RTO_LOGISTICS: "RTO logistics",
    CostType.MARKETPLACE_COMMISSION: "Marketplace commission",
    CostType.MARKETING_SPEND: "Marketing spend",
}

#: Scopes that map to a stored `orders` column and can therefore narrow the
#: revenue side as well as the cost side. `category`/`product` are refused rather
#: than half-applied: they are line-grained, and narrowing to them would leave
#: the order-level income terms (shipping, COD surcharge) unattributable without
#: running the allocator per order. Refusing is honest; a partial filter is not.
_SCOPE_ORDER_COLUMNS: dict[str, Any] = {
    CostScope.PAYMENT_METHOD: Order.payment_method,
    CostScope.GATEWAY: Order.gateway_code,
    CostScope.COURIER: Order.shipping_provider,
}

_RTO_STATUSES: tuple[ShipmentStatus, ...] = (
    ShipmentStatus.RTO_INITIATED,
    ShipmentStatus.RTO_DELIVERED,
)


# --------------------------------------------------------------------------
# Revenue recognition
# --------------------------------------------------------------------------


def _refund_is_recognisable() -> Any:
    """Can a reversal be dated for this order at all?

    True when the order carries ``orders.refunded_at``, or when any of its
    return rows reached a dated refund. Correlated to ``orders`` explicitly: the
    same clause is embedded in queries whose FROM is ``orders`` and in queries
    whose FROM is ``order_items JOIN orders``, and auto-correlation would pull
    ``returns`` out of the subquery in the second case.
    """
    refunded_return = (
        select(ReturnRequest.id)
        .where(
            ReturnRequest.order_id == Order.id,
            ReturnRequest.refunded_at.isnot(None),
        )
        .correlate(Order)
        .exists()
    )
    return or_(Order.refunded_at.isnot(None), refunded_return)


def _recognised_sale() -> Any:
    """Orders whose SALE belongs to the period they were created in.

    Defined here rather than by widening ``dashboard_service._REVENUE_STATUSES``,
    which the legacy admin endpoints import and which must not move.

    Two ways in:

    * the order is currently in a paid state — the legacy rule, unchanged; or
    * the order is ``REFUNDED`` **and** a reversal can be dated for it. The
      status graph only allows ``REFUNDED`` from PAID/SHIPPED/DELIVERED, so
      such an order was necessarily paid once, and the sale is real however the
      story ended.

    The second clause is deliberately *not* "was ever paid". A ``REFUNDED`` row
    with neither ``orders.refunded_at`` nor a dated return refund has a sale
    that nothing would ever reverse; admitting it would overstate revenue
    permanently. Excluding it keeps recognition and reversal symmetric, which is
    the property that makes the double count impossible rather than merely
    absent.
    """
    return or_(
        Order.status.in_(_REVENUE_STATUSES),
        and_(Order.status == OrderStatus.REFUNDED, _refund_is_recognisable()),
    )


@dataclass(frozen=True)
class RevenueRecognition:
    """The revenue bridge's terms, with the honesty metadata the bridge cannot carry.

    ``contracts.RevenueBridge`` types every term as ``int``, so it has nowhere to
    say "this reversal has no amount". This is the quality-graded figure;
    :meth:`MarginService.revenue_bridge` projects it onto the legacy shape for
    the callers that only want the five-term identity.

    ``refunds_minor`` and ``net_revenue_minor`` are ``None`` — never zero — when
    any refund dated in the window has no determinable amount, per the module's
    "missing is never zero" rule. ``refunds_valued_minor`` is the part that
    *is* known, and is what the projected bridge uses so the identity still
    closes for the callers that need a number.
    """

    gross_merchandise_sales_minor: int
    discounts_minor: int
    tax_minor: int
    shipping_minor: int
    cod_surcharge_minor: int
    #: SUM(orders.total_amount) over the recognised sales — the bridge's
    #: right-hand side before refunds.
    recognised_order_value_minor: int
    #: The refunds that could be valued. Always a number.
    refunds_valued_minor: int
    #: The refunds figure as a KPI: None when any of them could not be valued.
    refunds_minor: int | None
    net_revenue_minor: int | None
    #: The LEGACY definition, unchanged: refunded orders leave the set entirely.
    #: Byte-identical to ``DashboardService._revenue_summary()['revenue']``.
    paid_order_value_minor: int
    quality: MetricQuality = MetricQuality.AUTHORITATIVE
    warnings: tuple[str, ...] = ()
    #: Count of refund events dated in the window that carry no amount.
    unvalued_refunds: int = 0


# --------------------------------------------------------------------------
# Per-day drivers
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _DayBases:
    """Everything one reporting day can charge a cost rule against.

    A day is the resolution grain because cost rules are effective-dated by day:
    a rate that changed mid-window must be applied to the days on each side of
    the change, not averaged across it. That is the entire point of
    `analytics_cost_rules` and the reason this is not one resolve() per window.
    """

    day: date
    orders: int = 0
    units: int = 0
    lines: int = 0
    costed_lines: int = 0
    line_revenue_minor: int = 0
    discounts_minor: int = 0
    cogs_minor: int = 0
    shipping_income_minor: int = 0
    cod_surcharge_minor: int = 0
    tax_minor: int = 0
    order_total_minor: int = 0
    #: Money that actually travelled through a gateway: total less the balance
    #: the courier collects in cash. Exact for prepaid (cod_balance 0), for COD
    #: (cod_balance == total) and for split COD, without special-casing any.
    gateway_base_minor: int = 0
    gateway_orders: int = 0
    #: SUM(shipments.shipment_cost) where a real carrier figure exists.
    carrier_actual_minor: int = 0
    #: Orders with no shipment carrying a cost — the only ones a
    #: FORWARD_SHIPPING rule may be charged for.
    uncosted_orders: int = 0
    uncosted_units: int = 0
    uncosted_nms_minor: int = 0
    uncosted_weight_grams: int = 0
    #: Reverse pickups actually performed, dated by their own event.
    return_pickups: int = 0
    rto_events: int = 0

    @property
    def nms_minor(self) -> int:
        return self.line_revenue_minor - self.discounts_minor


@dataclass
class _Accumulator:
    """One cost type's running total across the window's days."""

    cost_type: str
    total_minor: int = 0
    missing: bool = False
    charged_days: int = 0

    def __post_init__(self) -> None:
        self.qualities: list[MetricQuality] = []
        self.rule_ids: set[int] = set()
        self.sources: set[str] = set()
        self.scopes: set[tuple[str, str]] = set()

    def add(self, component: CostComponent) -> None:
        self.charged_days += 1
        if component.is_missing:
            self.missing = True
            return
        self.total_minor += int(component.value_minor or 0)
        self.qualities.append(component.quality)
        if component.rule_id is not None:
            self.rule_ids.add(int(component.rule_id))
        if component.source:
            self.sources.add(component.source)
        self.scopes.add((component.scope, component.scope_value))

    def freeze(self, *, idle_source: str) -> CostComponent:
        """Collapse the window's days into the single component the API reports."""
        if self.missing:
            return CostComponent(
                cost_type=self.cost_type,
                value_minor=None,
                quality=MetricQuality.INCOMPLETE,
                source="",
            )
        if self.charged_days == 0:
            # No day in the window had a driver for this cost. Zero quantity is a
            # measurement, not an absence — see the module docstring.
            return CostComponent(
                cost_type=self.cost_type,
                value_minor=0,
                quality=MetricQuality.ACTUAL,
                source=idle_source,
            )
        scope, scope_value = (
            next(iter(self.scopes)) if len(self.scopes) == 1 else (CostScope.GLOBAL, "-")
        )
        return CostComponent(
            cost_type=self.cost_type,
            value_minor=self.total_minor,
            quality=worst_quality(self.qualities),
            scope=scope,
            scope_value=scope_value,
            source=(
                next(iter(self.sources))
                if len(self.sources) == 1
                else ("multiple rules" if self.sources else "")
            ),
            rule_id=next(iter(self.rule_ids)) if len(self.rule_ids) == 1 else None,
        )


class MarginService:
    """CM1/CM2/CM3 and the revenue bridge over a half-open date window.

    The window is ``[date_from, date_to)`` on ``orders.created_at``, matching
    ``ProfitService`` and ``DashboardService`` so the three surfaces cannot
    disagree about which orders belong to a period.

    Orders qualify on :func:`_recognised_sale` — the paid statuses plus orders
    that reached a paid state and were later reversed. Refunds are then
    subtracted once, dated by their own event. ``_REVENUE_STATUSES`` is used in
    exactly one place, :meth:`paid_order_value`, where the legacy definition is
    genuinely what is wanted; see the module docstring.
    """

    def __init__(
        self,
        db: Session,
        cost_resolver: CostRuleResolver | None = None,
        redis_client: Optional[redis.Redis] = None,
    ):
        self.db = db
        #: Forwarded to the resolver, which caches rule lookups for 60s. This
        #: service caches nothing itself: a margin figure carries a quality label
        #: and a coverage percentage, and a stale copy of those is worse than a
        #: slow fresh one.
        self.redis = redis_client
        self.resolver = cost_resolver or CostRuleResolver(db, redis_client)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def compute(
        self,
        date_from: date | datetime,
        date_to: date | datetime,
        *,
        scope: Mapping[str, Any] | None = None,
    ) -> MarginResult:
        """The cascade over ``[date_from, date_to)``.

        ``scope`` is what the slice *is*, e.g. ``{"payment_method": "cod"}``. It
        narrows the orders considered **and** is passed to the resolver as
        ``scope_candidates`` so a per-gateway or per-courier rule beats the
        global one. Line-grained scopes are refused — see ``_SCOPE_ORDER_COLUMNS``.
        """
        start, end = self._window(date_from, date_to)
        candidates = self._validate_scope(scope)
        days = self._days(start, end)
        bases = self._collect_bases(start, end, days, candidates)

        warnings: list[str] = []
        lines = sum(b.lines for b in bases)
        costed_lines = sum(b.costed_lines for b in bases)
        nms_minor = sum(b.nms_minor for b in bases)
        cogs_minor = sum(b.cogs_minor for b in bases)
        shipping_income_minor = sum(b.shipping_income_minor for b in bases)

        coverage, cogs_quality = self._coverage(lines, costed_lines, warnings)
        self._warn_about_reversed_sales(start, end, candidates, warnings)

        components: list[CostComponent] = [
            CostComponent(
                cost_type=COGS,
                value_minor=cogs_minor,
                quality=cogs_quality,
                source="order_items.unit_cost",
            ),
            CostComponent(
                cost_type=SHIPPING_INCOME,
                value_minor=shipping_income_minor,
                quality=MetricQuality.AUTHORITATIVE,
                source="orders.shipping_amount",
            ),
        ]

        resolved = self._resolve_costs(bases, candidates, warnings)
        components.extend(resolved[name] for name in CM2_COST_TYPES + CM3_COST_TYPES)

        missing_inputs = tuple(
            name
            for name in CM2_COST_TYPES + CM3_COST_TYPES
            if resolved[name].is_missing
        )

        cm1_minor = nms_minor - cogs_minor

        cm2_minor: int | None = None
        if not any(resolved[name].is_missing for name in CM2_COST_TYPES):
            cm2_minor = cm1_minor + shipping_income_minor - sum(
                int(resolved[name].value_minor or 0) for name in CM2_COST_TYPES
            )

        cm3_minor: int | None = None
        if cm2_minor is not None and not any(
            resolved[name].is_missing for name in CM3_COST_TYPES
        ):
            cm3_minor = cm2_minor - sum(
                int(resolved[name].value_minor or 0) for name in CM3_COST_TYPES
            )

        return MarginResult(
            net_merchandise_sales_minor=nms_minor,
            cogs_minor=cogs_minor,
            cm1_minor=cm1_minor,
            cm2_minor=cm2_minor,
            cm3_minor=cm3_minor,
            components=tuple(components),
            cost_coverage_pct=coverage,
            missing_inputs=missing_inputs,
            warnings=tuple(warnings),
        )

    def paid_order_value(
        self, date_from: date | datetime, date_to: date | datetime
    ) -> Decimal:
        """The LEGACY revenue figure, byte-identical to the dashboard's headline.

        ``SUM(orders.total_amount)`` over ``_REVENUE_STATUSES`` in the window —
        the same query, the same statuses and the same ``Decimal(x or 0)``
        boundary as ``DashboardService._revenue_summary``. A refunded order
        leaves the set entirely here; the reversal is *not* subtracted, because
        subtracting it as well is exactly the double count this module fixed.

        This is the shadow-mode parity anchor. It exists so the legacy admin
        pages and this subsystem can be reconciled row for row while both are
        live, which is the only way to prove the corrected figures are right.
        It must never be "improved" — use ``net_revenue`` for that.
        """
        start, end = self._window(date_from, date_to)
        total = self.db.execute(
            select(func.coalesce(func.sum(Order.total_amount), 0)).where(
                Order.status.in_(_REVENUE_STATUSES),
                Order.created_at >= start,
                Order.created_at < end,
            )
        ).scalar_one()
        return Decimal(total or 0)

    def recognised_revenue(
        self, date_from: date | datetime, date_to: date | datetime
    ) -> RevenueRecognition:
        """The revenue bridge's terms with their quality attached.

        The sale side is every order recognised in this window (see
        :func:`_recognised_sale`); the reversal side is every refund *dated* in
        this window, whenever the order it belongs to was created. The two are
        independent measurements of two different events, which is what makes a
        window containing both net to zero and a window containing only the
        reversal go negative — correctly, because that is what happened.

        Refunds are de-duplicated per the ``refunds`` KPI: a whole-order refund
        counts at ``orders.total_amount`` only when the order has no refunded
        return row, since ``orders`` carries no refund-amount column of its own.
        """
        start, end = self._window(date_from, date_to)
        window = self._order_window(start, end)

        agg = self.db.execute(
            select(
                func.coalesce(
                    func.sum(
                        Order.discount_amount + Order.payment_discount_amount
                    ),
                    0,
                ).label("discounts"),
                func.coalesce(func.sum(Order.tax_amount), 0).label("tax"),
                func.coalesce(func.sum(Order.shipping_amount), 0).label("shipping"),
                func.coalesce(func.sum(Order.cod_surcharge_amount), 0).label("cod"),
                func.coalesce(func.sum(Order.total_amount), 0).label("total"),
            ).where(*window)
        ).one()

        gms = self.db.execute(
            select(
                func.coalesce(
                    func.sum(OrderItem.quantity * OrderItem.unit_price), 0
                ).label("gms")
            )
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .where(*window)
        ).scalar_one()

        warnings: list[str] = []
        refunds_valued_minor, unvalued = self._refunds(start, end, warnings)
        recognised_total_minor = to_minor(agg.total)

        refunds_minor = None if unvalued else refunds_valued_minor
        net_revenue_minor = (
            None if refunds_minor is None else recognised_total_minor - refunds_minor
        )
        return RevenueRecognition(
            gross_merchandise_sales_minor=to_minor(gms),
            discounts_minor=to_minor(agg.discounts),
            tax_minor=to_minor(agg.tax),
            shipping_minor=to_minor(agg.shipping),
            cod_surcharge_minor=to_minor(agg.cod),
            recognised_order_value_minor=recognised_total_minor,
            refunds_valued_minor=refunds_valued_minor,
            refunds_minor=refunds_minor,
            net_revenue_minor=net_revenue_minor,
            paid_order_value_minor=to_minor(self.paid_order_value(start, end)),
            quality=(
                MetricQuality.INCOMPLETE if unvalued else MetricQuality.AUTHORITATIVE
            ),
            warnings=tuple(warnings),
            unvalued_refunds=unvalued,
        )

    def revenue_bridge(
        self, date_from: date | datetime, date_to: date | datetime
    ) -> RevenueBridge:
        """The five-term identity tying merchandise sales to accounting revenue.

        ``net_revenue`` is measured independently — ``SUM(orders.total_amount)``
        over the recognised orders, less refunds — rather than derived from the
        other five terms. That is what makes ``balances()`` a real check: it
        proves the order rows are internally consistent instead of restating an
        assumption.

        Necessary, not sufficient. The identity balanced all the way through the
        double count described in the module docstring, because both of its
        sides were built from the same wrong input. Tests must assert the terms,
        not merely that they add up.

        ``RevenueBridge`` types every term as ``int`` and so cannot express a
        refund with no amount; the refunds here are the *valued* portion.
        :meth:`recognised_revenue` is the quality-graded figure and is what a
        caller should use when it needs to know whether the number is complete.
        """
        r = self.recognised_revenue(date_from, date_to)
        refunds_minor = r.refunds_valued_minor
        net_revenue_minor = r.recognised_order_value_minor - refunds_minor

        bridge = RevenueBridge(
            gross_merchandise_sales_minor=r.gross_merchandise_sales_minor,
            discounts_minor=r.discounts_minor,
            tax_minor=r.tax_minor,
            shipping_minor=r.shipping_minor,
            cod_surcharge_minor=r.cod_surcharge_minor,
            refunds_minor=refunds_minor,
            net_revenue_minor=net_revenue_minor,
        )
        # Signed terms, so a UI renders the waterfall without re-deriving signs.
        # The closing entry is the identity's right-hand side, not another step.
        bridge.steps = [
            ("Gross merchandise sales", r.gross_merchandise_sales_minor),
            ("Discounts", -r.discounts_minor),
            ("Tax collected", r.tax_minor),
            ("Shipping income", r.shipping_minor),
            ("COD surcharge", r.cod_surcharge_minor),
            ("Refunds", -refunds_minor),
            ("Net revenue", net_revenue_minor),
        ]
        return bridge

    def waterfall(self, result: MarginResult) -> list[dict[str, Any]]:
        """UI-ready steps for the Revenue Reconciliation view.

        ``kind`` is one of start / cost / income / subtotal / result. Amounts are
        Decimal rupees, signed as they act on the running total, and ``None``
        where the input is missing — a missing step is drawn as a gap, never as a
        zero-height bar. ``quality`` and ``missing`` ride on every step so the
        view can mark exactly which bar is not to be trusted.
        """
        by_type = {c.cost_type: c for c in result.components}

        def step(
            label: str,
            minor: int | None,
            kind: str,
            *,
            quality: MetricQuality,
            cost_type: str = "",
        ) -> dict[str, Any]:
            return {
                "label": label,
                "amount": None if minor is None else from_minor(minor),
                "amount_minor": minor,
                "kind": kind,
                "quality": quality.value,
                "missing": minor is None,
                "cost_type": cost_type,
            }

        def component_step(cost_type: str) -> dict[str, Any]:
            comp = by_type.get(cost_type)
            if comp is None:  # pragma: no cover - compute() always populates these
                return step(
                    _LABELS.get(cost_type, cost_type), None, "cost",
                    quality=MetricQuality.INCOMPLETE, cost_type=cost_type,
                )
            income = cost_type in INCOME_COMPONENTS
            signed = (
                None
                if comp.value_minor is None
                else (comp.value_minor if income else -comp.value_minor)
            )
            return step(
                _LABELS.get(cost_type, cost_type),
                signed,
                "income" if income else "cost",
                quality=comp.quality,
                cost_type=cost_type,
            )

        steps: list[dict[str, Any]] = [
            step(
                "Net merchandise sales",
                result.net_merchandise_sales_minor,
                "start",
                quality=MetricQuality.ALLOCATED,
            ),
            component_step(COGS),
            step(
                "CM1", result.cm1_minor, "subtotal",
                quality=by_type[COGS].quality if COGS in by_type else result.quality,
            ),
            component_step(SHIPPING_INCOME),
        ]
        steps.extend(component_step(name) for name in CM2_COST_TYPES)
        steps.append(step("CM2", result.cm2_minor, "subtotal", quality=result.quality))
        steps.extend(component_step(name) for name in CM3_COST_TYPES)
        steps.append(step("CM3", result.cm3_minor, "result", quality=result.quality))
        return steps

    # ------------------------------------------------------------------
    # Window / scope plumbing
    # ------------------------------------------------------------------
    @staticmethod
    def _as_datetime(value: date | datetime) -> datetime:
        """Accept a date or a datetime; naive datetimes are read as UTC."""
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)

    @classmethod
    def _window(
        cls, date_from: date | datetime, date_to: date | datetime
    ) -> tuple[datetime, datetime]:
        start, end = cls._as_datetime(date_from), cls._as_datetime(date_to)
        if end <= start:
            raise ValueError(
                f"date_to ({end.isoformat()}) must be after date_from "
                f"({start.isoformat()}); the window is half-open [from, to)"
            )
        return start, end

    @staticmethod
    def _days(start: datetime, end: datetime) -> list[date]:
        """Every reporting day the window touches, inclusive of a partial last day."""
        first, last = start.date(), (end - timedelta(microseconds=1)).date()
        return [first + timedelta(days=n) for n in range((last - first).days + 1)]

    @staticmethod
    def _validate_scope(scope: Mapping[str, Any] | None) -> dict[str, str]:
        if not scope:
            return {}
        out: dict[str, str] = {}
        for key, value in scope.items():
            if key not in _SCOPE_ORDER_COLUMNS:
                raise ValueError(
                    f"scope {key!r} is not supported by MarginService. Supported: "
                    f"{sorted(_SCOPE_ORDER_COLUMNS)}. Line-grained scopes need the "
                    "per-line allocator and a different result shape."
                )
            text = str(value).strip()
            if not text or text == "-":
                raise ValueError(f"scope {key!r} needs a value, got {value!r}")
            out[key] = text
        return out

    def _order_window(
        self, start: datetime, end: datetime, scope: Mapping[str, str] | None = None
    ) -> list[Any]:
        clauses: list[Any] = [
            _recognised_sale(),
            Order.created_at >= start,
            Order.created_at < end,
        ]
        for key, value in (scope or {}).items():
            clauses.append(_SCOPE_ORDER_COLUMNS[key] == value)
        return clauses

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------
    def _collect_bases(
        self,
        start: datetime,
        end: datetime,
        days: Sequence[date],
        scope: Mapping[str, str],
    ) -> list[_DayBases]:
        """One `_DayBases` per day in the window, zero-filled where nothing happened.

        Zero-filling a *quantity* is correct and is not the zero-fill this module
        forbids: a day with no orders genuinely sold nothing. Zero-filling a
        *rate* is what would be a lie, and that never happens here.
        """
        window = self._order_window(start, end, scope)
        # correlate(Order) explicitly: the same EXISTS is embedded in queries
        # whose FROM is `orders` and in queries whose FROM is `order_items JOIN
        # orders`, and auto-correlation would pull `shipments` out of the
        # subquery in the second case.
        costed_shipment = (
            select(Shipment.id)
            .where(Shipment.order_id == Order.id, Shipment.shipment_cost.isnot(None))
            .correlate(Order)
            .exists()
        )
        day = func.date(Order.created_at).label("day")

        order_rows = self.db.execute(
            select(
                day,
                func.count(Order.id).label("orders"),
                func.coalesce(
                    func.sum(Order.discount_amount + Order.payment_discount_amount), 0
                ).label("discounts"),
                func.coalesce(func.sum(Order.shipping_amount), 0).label("shipping"),
                func.coalesce(func.sum(Order.cod_surcharge_amount), 0).label("cod"),
                func.coalesce(func.sum(Order.tax_amount), 0).label("tax"),
                func.coalesce(func.sum(Order.total_amount), 0).label("total"),
                func.coalesce(
                    func.sum(Order.total_amount - Order.cod_balance), 0
                ).label("gateway_base"),
                func.coalesce(
                    func.sum(
                        case((Order.total_amount - Order.cod_balance > 0, 1), else_=0)
                    ),
                    0,
                ).label("gateway_orders"),
            )
            .where(*window)
            .group_by(day)
        ).all()

        # NULL unit_cost rows fall out of SUM() on their own — no COALESCE inside
        # the sum, which is precisely the inherited bug. They still count in
        # `lines`, so the coverage denominator stays honest.
        line_rows = self.db.execute(
            select(
                day,
                func.count(OrderItem.id).label("lines"),
                func.coalesce(func.sum(OrderItem.quantity), 0).label("units"),
                func.coalesce(
                    func.sum(OrderItem.quantity * OrderItem.unit_price), 0
                ).label("line_revenue"),
                func.coalesce(
                    func.sum(OrderItem.quantity * OrderItem.unit_cost), 0
                ).label("cogs"),
                func.coalesce(
                    func.sum(case((OrderItem.unit_cost.isnot(None), 1), else_=0)), 0
                ).label("costed_lines"),
            )
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .where(*window)
            .group_by(day)
        ).all()

        carrier_rows = self.db.execute(
            select(
                day,
                func.coalesce(func.sum(Shipment.shipment_cost), 0).label("carrier"),
            )
            .select_from(Shipment)
            .join(Order, Order.id == Shipment.order_id)
            .where(*window, Shipment.shipment_cost.isnot(None))
            .group_by(day)
        ).all()

        uncosted_order_rows = self.db.execute(
            select(
                day,
                func.count(Order.id).label("orders"),
                func.coalesce(
                    func.sum(Order.discount_amount + Order.payment_discount_amount), 0
                ).label("discounts"),
            )
            .where(*window, ~costed_shipment)
            .group_by(day)
        ).all()

        uncosted_line_rows = self.db.execute(
            select(
                day,
                func.coalesce(func.sum(OrderItem.quantity), 0).label("units"),
                func.coalesce(
                    func.sum(OrderItem.quantity * OrderItem.unit_price), 0
                ).label("line_revenue"),
            )
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .where(*window, ~costed_shipment)
            .group_by(day)
        ).all()

        uncosted_weight_rows = self.db.execute(
            select(
                day,
                func.coalesce(func.sum(Shipment.package_weight_grams), 0).label("grams"),
            )
            .select_from(Shipment)
            .join(Order, Order.id == Shipment.order_id)
            .where(*window, ~costed_shipment)
            .group_by(day)
        ).all()

        # Return and RTO costs are period costs dated by their OWN event, not by
        # the order's creation date — a March pickup for a February order is a
        # March cost. Same rule the `refunds` KPI uses for refunded_at.
        pickup_day = func.date(ReturnRequest.picked_up_at).label("day")
        return_rows = self.db.execute(
            select(pickup_day, func.count(ReturnRequest.id).label("n"))
            .where(
                ReturnRequest.picked_up_at.isnot(None),
                ReturnRequest.picked_up_at >= start,
                ReturnRequest.picked_up_at < end,
            )
            .group_by(pickup_day)
        ).all()

        rto_day = func.date(Shipment.returned_at).label("day")
        rto_rows = self.db.execute(
            select(rto_day, func.count(Shipment.id).label("n"))
            .where(
                Shipment.shipment_status.in_(_RTO_STATUSES),
                Shipment.returned_at.isnot(None),
                Shipment.returned_at >= start,
                Shipment.returned_at < end,
            )
            .group_by(rto_day)
        ).all()

        orders_by = _index(order_rows)
        lines_by = _index(line_rows)
        carrier_by = _index(carrier_rows)
        unc_orders_by = _index(uncosted_order_rows)
        unc_lines_by = _index(uncosted_line_rows)
        unc_weight_by = _index(uncosted_weight_rows)
        returns_by = _index(return_rows)
        rto_by = _index(rto_rows)

        bases: list[_DayBases] = []
        for d in days:
            o = orders_by.get(d)
            li = lines_by.get(d)
            uo = unc_orders_by.get(d)
            ul = unc_lines_by.get(d)
            bases.append(
                _DayBases(
                    day=d,
                    orders=int(o.orders) if o else 0,
                    units=int(li.units) if li else 0,
                    lines=int(li.lines) if li else 0,
                    costed_lines=int(li.costed_lines) if li else 0,
                    line_revenue_minor=to_minor(li.line_revenue) if li else 0,
                    discounts_minor=to_minor(o.discounts) if o else 0,
                    cogs_minor=to_minor(li.cogs) if li else 0,
                    shipping_income_minor=to_minor(o.shipping) if o else 0,
                    cod_surcharge_minor=to_minor(o.cod) if o else 0,
                    tax_minor=to_minor(o.tax) if o else 0,
                    order_total_minor=to_minor(o.total) if o else 0,
                    gateway_base_minor=to_minor(o.gateway_base) if o else 0,
                    gateway_orders=int(o.gateway_orders) if o else 0,
                    carrier_actual_minor=(
                        to_minor(carrier_by[d].carrier) if d in carrier_by else 0
                    ),
                    uncosted_orders=int(uo.orders) if uo else 0,
                    uncosted_units=int(ul.units) if ul else 0,
                    uncosted_nms_minor=(
                        (to_minor(ul.line_revenue) if ul else 0)
                        - (to_minor(uo.discounts) if uo else 0)
                    ),
                    uncosted_weight_grams=(
                        int(unc_weight_by[d].grams) if d in unc_weight_by else 0
                    ),
                    return_pickups=int(returns_by[d].n) if d in returns_by else 0,
                    rto_events=int(rto_by[d].n) if d in rto_by else 0,
                )
            )
        return bases

    def _refunds_minor(self, start: datetime, end: datetime) -> int:
        """Refunds issued in the window, de-duplicated across the two sources.

        A returns-driven refund carries an explicit ``returns.refund_amount``. A
        whole-order refund has no amount column at all and is valued at
        ``orders.total_amount`` — but only when the order has no refunded return
        row, or the same money is counted twice.

        The valued total only. ``_refunds`` is the same figure with the count of
        refunds that could not be valued; this signature is kept because
        ``aggregation/jobs.py`` mirrors it term for term.
        """
        return self._refunds(start, end, [])[0]

    def _refunds(
        self, start: datetime, end: datetime, warnings: list[str]
    ) -> tuple[int, int]:
        """``(valued_minor, unvalued_count)`` for the refunds dated in the window.

        Dated by ``refunded_at`` on either table, never by the order's creation
        date: a March refund of a January order is a March event. That is the
        whole point of recognising the reversal separately from the sale.

        ``returns.refund_amount`` is nullable and is only stamped at approval, so
        a refund can be dated without ever being valued. ``COALESCE(SUM(...), 0)``
        would report that as 0.00 and hand back a confident, wrong net revenue —
        the module's "missing is never zero" rule applies to reversals exactly as
        it applies to costs, so those rows are counted and named instead.
        """
        from_returns = self.db.execute(
            select(func.coalesce(func.sum(ReturnRequest.refund_amount), 0)).where(
                ReturnRequest.refunded_at.isnot(None),
                ReturnRequest.refunded_at >= start,
                ReturnRequest.refunded_at < end,
            )
        ).scalar_one()

        unvalued_ids = (
            self.db.execute(
                select(ReturnRequest.id).where(
                    ReturnRequest.refunded_at.isnot(None),
                    ReturnRequest.refunded_at >= start,
                    ReturnRequest.refunded_at < end,
                    ReturnRequest.refund_amount.is_(None),
                )
            )
            .scalars()
            .all()
        )
        if unvalued_ids:
            warnings.append(
                f"refunds: {len(unvalued_ids)} refund(s) dated in this window carry "
                f"no returns.refund_amount and CANNOT be valued (return "
                f"{', '.join('#' + str(i) for i in unvalued_ids)}). Refunds and net "
                "revenue are reported as unknown rather than as the zero a COALESCE "
                "would produce."
            )

        # Whole-order refunds, and what their return rows already reversed. The
        # join is not date-bounded on the returns side on purpose: the question
        # is whether this order's money is already accounted for anywhere, not
        # whether it was accounted for in this window.
        rows = self.db.execute(
            select(
                Order.id.label("order_id"),
                Order.total_amount.label("total"),
                func.count(ReturnRequest.id).label("refunded_returns"),
                func.coalesce(func.sum(ReturnRequest.refund_amount), 0).label(
                    "already_reversed"
                ),
            )
            .select_from(Order)
            .outerjoin(
                ReturnRequest,
                and_(
                    ReturnRequest.order_id == Order.id,
                    ReturnRequest.refunded_at.isnot(None),
                ),
            )
            .where(
                Order.refunded_at.isnot(None),
                Order.refunded_at >= start,
                Order.refunded_at < end,
            )
            .group_by(Order.id, Order.total_amount)
        ).all()

        whole_order_minor = 0
        for row in rows:
            total_minor = to_minor(row.total)
            if not int(row.refunded_returns):
                whole_order_minor += total_minor
                continue
            # De-duplicated: the return rows own this order's reversal. When they
            # reversed LESS than the order total, the remainder — which
            # OrderService.refund really did send back to the gateway — is
            # invisible, because `orders` has no refund-amount column to read it
            # from. Under-reported, and said out loud rather than discovered.
            shortfall = total_minor - to_minor(row.already_reversed)
            if shortfall > 0:
                warnings.append(
                    f"refunds: order #{int(row.order_id)} was refunded in full at "
                    f"order level but is already partially reversed by its return "
                    f"row(s) ({from_minor(to_minor(row.already_reversed))} of "
                    f"{from_minor(total_minor)}). Only the return amount is counted "
                    "— orders has no refund-amount column — so this window "
                    f"under-reverses by {from_minor(shortfall)}."
                )

        return to_minor(from_returns) + whole_order_minor, len(unvalued_ids)

    def _warn_about_reversed_sales(
        self,
        start: datetime,
        end: datetime,
        scope: Mapping[str, str],
        warnings: list[str],
    ) -> None:
        """Name the recognised sales that were later reversed.

        The cascade is a merchandise-basis measure and has no contra-revenue
        term: refunds carry tax, shipping and the COD surcharge, none of which
        are in ``net_merchandise_sales``, so there is no honest way to net them
        off CM1. That is accrual-correct — the sale and the costs really were
        incurred in this period, and the reversal is an event of its own period,
        where the returned goods also come back to stock — but it means a window
        whose sales were later refunded shows a contribution margin the store
        did not keep. The revenue bridge is where the reversal lives; this
        warning is the pointer to it.
        """
        row = self.db.execute(
            select(
                func.count(Order.id).label("orders"),
                func.coalesce(func.sum(Order.total_amount), 0).label("total"),
            ).where(
                *self._order_window(start, end, scope),
                Order.status == OrderStatus.REFUNDED,
            )
        ).one()
        if not int(row.orders):
            return
        warnings.append(
            f"{int(row.orders)} recognised sale(s) worth {from_minor(to_minor(row.total))} "
            "in this window were later refunded. The sale and its costs stay in this "
            "period (accrual basis); the reversal is recognised in the period of its "
            "own refunded_at and appears in the revenue bridge, NOT in this cascade — "
            "so CM1/CM2/CM3 here are margin on sales the store may not have kept."
        )

    # ------------------------------------------------------------------
    # Cost resolution
    # ------------------------------------------------------------------
    @staticmethod
    def _coverage(
        lines: int, costed_lines: int, warnings: list[str]
    ) -> tuple[Decimal, MetricQuality]:
        """Share of lines carrying a real ``unit_cost``, and what COGS is worth.

        Quality rises to AUTHORITATIVE only at 100% coverage, per the ``cogs``
        KPI: below that it stays INCOMPLETE however small the gap, because the
        missing lines contribute nothing to the sum and every margin built on it
        reads better than reality.
        """
        if lines == 0:
            warnings.append(
                "no order lines in the window; cost coverage is vacuously complete "
                "and every margin figure is zero by construction"
            )
            return Decimal("100.00"), MetricQuality.AUTHORITATIVE
        pct = (Decimal(costed_lines) / Decimal(lines) * 100).quantize(Decimal("0.01"))
        if costed_lines < lines:
            warnings.append(
                f"cogs: {lines - costed_lines} of {lines} order line(s) have no "
                f"unit_cost snapshot and are EXCLUDED from COGS (coverage {pct}%). "
                "The margin is understated-cost and therefore overstated."
            )
            return pct, MetricQuality.INCOMPLETE
        return pct, MetricQuality.AUTHORITATIVE

    def _resolve_costs(
        self,
        bases: Sequence[_DayBases],
        scope: Mapping[str, str],
        warnings: list[str],
    ) -> dict[str, CostComponent]:
        """Resolve every cost type, day by day, against that day's drivers."""
        accs = {
            name: _Accumulator(name) for name in CM2_COST_TYPES + CM3_COST_TYPES
        }
        carrier_actual_minor = sum(b.carrier_actual_minor for b in bases)

        for b in bases:
            for name in CM2_COST_TYPES + CM3_COST_TYPES:
                if not self._is_charged(name, b):
                    continue
                component = self.resolver.resolve(
                    name, b.day, scope_candidates=dict(scope) or None
                )
                accs[name].add(
                    self.resolver.compute(
                        component,
                        base_minor=self._pct_base(name, b),
                        units=self._units_base(name, b),
                        weight_grams=self._weight_base(name, b),
                        orders=self._order_base(name, b),
                        days_in_period=1,
                        on_date=b.day,
                    )
                )

        resolved = {
            name: acc.freeze(idle_source=_IDLE_SOURCE[name])
            for name, acc in accs.items()
        }

        # Forward shipping is the one component with a real observed source. The
        # rule only ever covered the orders with no carrier figure; the measured
        # charge is added on top, and the pair is graded by its weaker half.
        resolved[CostType.FORWARD_SHIPPING] = self._merge_carrier_actual(
            resolved[CostType.FORWARD_SHIPPING], carrier_actual_minor, warnings
        )

        if not any(b.orders for b in bases):
            warnings.append(
                "no qualifying orders in the window, so no volume-driven cost rule "
                "was required; costs shown as zero are zero quantities, not zero rates"
            )
        return resolved

    @staticmethod
    def _merge_carrier_actual(
        rule_component: CostComponent, actual_minor: int, warnings: list[str]
    ) -> CostComponent:
        if actual_minor == 0:
            return rule_component
        if rule_component.is_missing:
            warnings.append(
                f"forward_shipping: {from_minor(actual_minor)} of real carrier cost "
                "was read from shipments.shipment_cost, but at least one order has "
                "no shipment cost and no FORWARD_SHIPPING rule covers it"
            )
            return rule_component
        return CostComponent(
            cost_type=CostType.FORWARD_SHIPPING,
            value_minor=int(rule_component.value_minor or 0) + actual_minor,
            quality=worst_quality([rule_component.quality, MetricQuality.ACTUAL]),
            scope=rule_component.scope,
            scope_value=rule_component.scope_value,
            source=(
                "shipments.shipment_cost"
                if rule_component.value_minor == 0
                else "shipments.shipment_cost + cost rule"
            ),
            rule_id=rule_component.rule_id,
        )

    @staticmethod
    def _is_charged(cost_type: str, b: _DayBases) -> bool:
        """Does this day have a non-zero driver for this cost?

        A zero driver needs no rule: no gateway money means no gateway fee, no
        reverse pickup means no return-shipping charge. Marketing is the one
        time-driven cost — it accrues on a day with no orders at all, so it is
        always charged and a window with no MARKETING_SPEND rule is INCOMPLETE
        rather than free.
        """
        if cost_type == CostType.MARKETING_SPEND:
            return True
        if cost_type == CostType.GATEWAY_FEE:
            return b.gateway_base_minor > 0
        if cost_type == CostType.FORWARD_SHIPPING:
            return b.uncosted_orders > 0
        if cost_type == CostType.RETURN_SHIPPING:
            return b.return_pickups > 0
        if cost_type == CostType.RTO_LOGISTICS:
            return b.rto_events > 0
        if cost_type == CostType.MARKETPLACE_COMMISSION:
            # There is no channel column on `orders`, so no order in this
            # deployment can have come from a marketplace and the driver is
            # measured at zero — the same kind of zero as "no reverse pickups".
            # kpis.py keeps the term only so the cascade does not change shape
            # if a marketplace is ever added; adding one means adding the
            # channel column, and this driver becomes real in the same change.
            return False
        return b.orders > 0

    @staticmethod
    def _pct_base(cost_type: str, b: _DayBases) -> int:
        if cost_type == CostType.GATEWAY_FEE:
            return b.gateway_base_minor
        if cost_type == CostType.FORWARD_SHIPPING:
            return b.uncosted_nms_minor
        return b.nms_minor

    @staticmethod
    def _order_base(cost_type: str, b: _DayBases) -> int:
        if cost_type == CostType.GATEWAY_FEE:
            return b.gateway_orders
        if cost_type == CostType.FORWARD_SHIPPING:
            return b.uncosted_orders
        if cost_type == CostType.RETURN_SHIPPING:
            return b.return_pickups
        if cost_type == CostType.RTO_LOGISTICS:
            return b.rto_events
        return b.orders

    @staticmethod
    def _units_base(cost_type: str, b: _DayBases) -> int:
        if cost_type == CostType.FORWARD_SHIPPING:
            return b.uncosted_units
        return b.units

    @staticmethod
    def _weight_base(cost_type: str, b: _DayBases) -> int:
        # Only forward shipping is ever quoted per kg, and only for the orders a
        # rule still has to cover. Everything else passes 0, which makes the
        # resolver return INCOMPLETE for a PER_KG rule — correct, since a per-kg
        # packaging rule has no weight to charge against here.
        if cost_type == CostType.FORWARD_SHIPPING:
            return b.uncosted_weight_grams
        return 0


#: What a component's `source` says when no day in the window had a driver for
#: it. Stated per cost type so the admin reads "there were no returns", not an
#: unexplained zero.
_IDLE_SOURCE: dict[str, str] = {
    CostType.GATEWAY_FEE: "no money collected through a gateway in the window",
    CostType.PACKAGING: "no qualifying orders in the window",
    CostType.HANDLING: "no qualifying orders in the window",
    CostType.FORWARD_SHIPPING: "every order carries a real shipments.shipment_cost",
    CostType.RETURN_SHIPPING: "no reverse pickups in the window",
    CostType.RTO_LOGISTICS: "no RTO legs in the window",
    CostType.MARKETPLACE_COMMISSION: "single-channel deployment: no marketplace orders",
    CostType.MARKETING_SPEND: "no days in the window",
}


def _as_day(value: Any) -> date:
    """Normalise whatever the driver returns for DATE() to a `date`.

    mysqlclient hands back a `date`, but a string or a `datetime` from another
    driver would key the day map with something the lookup never matches — and
    the failure mode is a silently all-zero window, not an error.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _index(rows: Sequence[Any]) -> dict[date, Any]:
    """Row list -> {day: row}, keyed by a real `date` whatever the driver gave."""
    return {_as_day(row.day): row for row in rows}
