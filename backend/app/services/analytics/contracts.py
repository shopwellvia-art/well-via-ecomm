"""Result shapes for the allocation, cost and margin engines.

Written before the three engines so they compose without negotiating. Pure
stdlib + Decimal; no SQLAlchemy, no FastAPI.

The one idea running through all of it: **an unknown value is never a number.**
Missing product cost is not zero cost. A missing gateway-fee rule is not a free
transaction. Every result therefore carries a quality label, a coverage
percentage, and the explicit list of what was missing — and the API surfaces all
three rather than rendering a confident figure built on a silent assumption.

That rule is not theoretical here. `ProfitService` today does
``func.coalesce(OrderItem.unit_cost, 0)``, so every order line with no cost
snapshot contributes zero COGS and reports 100% margin. This module exists so
the replacement cannot repeat it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.services.analytics.types import MetricQuality, worst_quality

#: Money is carried through these engines as integer MINOR UNITS (paise), not
#: Decimal. Allocation must be exact to the last paisa, and integers make that a
#: property of the arithmetic rather than something to assert afterwards.
#: Convert at the boundary with to_minor() / from_minor().
MINOR_UNITS_PER_MAJOR = 100


def to_minor(amount: Decimal | int | float | None) -> int:
    """Decimal rupees -> integer paise. None becomes 0 minor units.

    Callers must not pass float; the signature allows it only so a stray float
    from legacy code fails loudly in tests rather than silently rounding.
    """
    if amount is None:
        return 0
    if isinstance(amount, float):  # pragma: no cover - defensive
        raise TypeError(
            "float is not accepted for money; pass Decimal or int minor units"
        )
    return int((Decimal(amount) * MINOR_UNITS_PER_MAJOR).to_integral_value())


def from_minor(minor: int) -> Decimal:
    """Integer paise -> Decimal rupees, exact."""
    return (Decimal(minor) / MINOR_UNITS_PER_MAJOR).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class LineAllocation:
    """What one order line received from each order-level amount, in paise.

    Every field is an exact integer share. The invariant the whole engine exists
    to guarantee: for any order, summing a given field across its lines equals
    the order-level amount exactly — no tolerance, no rounding drift.
    """

    order_item_id: int
    extended_price: int
    discount: int = 0
    tax: int = 0
    shipping: int = 0
    cod_surcharge: int = 0
    payment_discount: int = 0
    gateway_fee: int = 0
    #: ALLOCATED whenever any share was derived from an order-level total, which
    #: is always: `order_items` carries no tax, discount or shipping column, so
    #: these can never legitimately be AUTHORITATIVE.
    quality: MetricQuality = MetricQuality.ALLOCATED


@dataclass(frozen=True)
class AllocationResult:
    """All lines of one order, plus what could not be allocated and why."""

    order_id: int
    lines: tuple[LineAllocation, ...]
    warnings: tuple[str, ...] = ()

    def total(self, field_name: str) -> int:
        return sum(getattr(line, field_name) for line in self.lines)


@dataclass(frozen=True)
class CostComponent:
    """One resolved cost input to the margin cascade.

    `value_minor is None` means MISSING — no rule covered this bucket. That is
    materially different from a rule that resolved to zero, which is a real
    measurement. Collapsing the two is the bug this type prevents.
    """

    cost_type: str
    value_minor: int | None
    quality: MetricQuality
    scope: str = "global"
    scope_value: str = "-"
    source: str = ""
    rule_id: int | None = None

    @property
    def is_missing(self) -> bool:
        return self.value_minor is None


@dataclass(frozen=True)
class MarginResult:
    """CM1 / CM2 / CM3 with the honesty metadata attached.

    A margin whose inputs are incomplete is still reported — suppressing it
    entirely would be its own kind of lie — but it is labelled INCOMPLETE, its
    coverage is stated, and every missing input is named so an admin can see
    exactly which cost to configure. It must not be presented as authoritative
    and the view carrying it cannot be marked LIVE.
    """

    net_merchandise_sales_minor: int
    cogs_minor: int | None
    cm1_minor: int | None
    cm2_minor: int | None
    cm3_minor: int | None
    components: tuple[CostComponent, ...] = ()
    #: Share of order lines that had a real unit_cost snapshot, 0..100.
    cost_coverage_pct: Decimal = Decimal("0")
    missing_inputs: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def quality(self) -> MetricQuality:
        """Worst component wins — one unknown input makes the whole figure
        incomplete, however solid the rest of the arithmetic is."""
        if self.missing_inputs or self.cogs_minor is None:
            return MetricQuality.INCOMPLETE
        return worst_quality([c.quality for c in self.components] or [MetricQuality.INCOMPLETE])

    def pct(self, cm_minor: int | None) -> Decimal | None:
        """Margin percentage against net merchandise sales.

        Returns None rather than 0 when the base is zero — a period with no
        sales has an undefined margin, not a 0% one, and charting 0% would draw
        a confident flat line through a gap in the data.
        """
        if cm_minor is None or self.net_merchandise_sales_minor == 0:
            return None
        return (
            Decimal(cm_minor) / Decimal(self.net_merchandise_sales_minor) * 100
        ).quantize(Decimal("0.01"))


@dataclass
class RevenueBridge:
    """The five-term identity that ties the two revenue bases together.

    ``gross_merchandise_sales - discounts + tax + shipping + cod_surcharge
      - refunds == net_revenue``

    This is self-proving: if it does not balance, an aggregation job is wrong.
    It is rendered as the Revenue Reconciliation waterfall and asserted as a
    test, which is why the codebase's two existing revenue definitions can be
    reconciled rather than merely disagreeing.
    """

    gross_merchandise_sales_minor: int = 0
    discounts_minor: int = 0
    tax_minor: int = 0
    shipping_minor: int = 0
    cod_surcharge_minor: int = 0
    refunds_minor: int = 0
    net_revenue_minor: int = 0
    steps: list[tuple[str, int]] = field(default_factory=list)

    def balances(self) -> bool:
        computed = (
            self.gross_merchandise_sales_minor
            - self.discounts_minor
            + self.tax_minor
            + self.shipping_minor
            + self.cod_surcharge_minor
            - self.refunds_minor
        )
        return computed == self.net_revenue_minor

    def imbalance_minor(self) -> int:
        computed = (
            self.gross_merchandise_sales_minor
            - self.discounts_minor
            + self.tax_minor
            + self.shipping_minor
            + self.cod_surcharge_minor
            - self.refunds_minor
        )
        return computed - self.net_revenue_minor
