"""The KPI catalogue — one entry per number this admin is allowed to display.

Why this is a Python module
---------------------------
It is not YAML. There is no YAML parser in ``requirements.txt``, and adding one
to read a static file the app already ships would be a dependency bought for
nothing. More importantly a YAML file cannot hold the *computing* expression —
the SQLAlchemy fragment, the status tuple, the enum member — so a YAML
catalogue would immediately need a parallel Python file to make the numbers,
and the two would drift. One file, one truth.

It is not a database table either. A KPI table would mean every new metric is a
hand-written INSERT (and every correction an UPDATE) against a *shared
production MySQL* — no review, no diff, no test, no rollback, and no way to know
which revision of a definition produced a number in last month's screenshot.
Definitions of money belong in version control next to the code that computes
them. ``version`` on each entry is what a changed definition bumps, so a stored
figure can always be traced back to the rule that produced it.

Reading the money definitions
-----------------------------
The single most expensive mistake this subsystem can make is putting two
different revenue numbers on one screen under two labels that sound the same.
There are FIVE distinct revenue figures here and they are deliberately named so
that no two can be confused. The exact bridge between the merchandise basis and
the accounting basis is::

    gross_merchandise_sales
      - discounts
      + tax_collected
      + shipping_income
      + cod_surcharge_collected
      - refunds
      = net_revenue

``paid_order_value`` is the figure ``DashboardService._revenue_summary`` returns
today, byte for byte. It is kept under its own id precisely so that the
dashboard's headline can never silently become one of the other four.

When a sale is recognised, and when it is reversed
--------------------------------------------------
These are two events, in two periods, and every figure above states which of
them it measures.

A **sale** is recognised in the period of ``orders.created_at`` when the order
reached a paid state — *including* an order that has since been refunded. The
sale happened; it belongs to the period it happened in. A **reversal** is
recognised in the period of its own ``refunded_at`` (``returns.refunded_at`` for
returns-driven refunds), whenever the original order was created.

So an order placed and fully refunded inside one window nets to zero, and a
January sale refunded in March leaves January untouched and takes the reversal
in March. Applying both — dropping the refunded order *and* subtracting the
refund — reverses the same money twice and reports a **negative** figure for a
window whose true answer is zero. The revenue bridge below balances either way,
because both of its sides are built from the same input, so the identity cannot
be used as proof that the recognition is right.

``paid_order_value`` deliberately does NOT follow this rule. It keeps the legacy
definition, in which a refunded order leaves the set entirely and no refund is
subtracted, because it is the shadow-mode parity anchor against the legacy admin
pages — the pages the business is still reading. Two definitions, both stated
under their own id, exactly as the two pre-existing revenue bases already are.
Anyone comparing two revenue cards on one screen can read here which is which:
``paid_order_value`` answers "what did the orders that are still paid come to",
``net_revenue`` answers "what did this period actually earn, net of what we gave
back in it".

Quality is declared, never assumed
----------------------------------
``default_quality`` is the honest grade a KPI carries given the data model *as
it stands today*. Order-level money read straight from ``orders`` is
AUTHORITATIVE. Anything that needs an order-level amount pushed down to a line
is ALLOCATED (there is no per-line tax or per-line discount column — the
allocation is a documented largest-remainder rule, see ``net_merchandise_sales``
below). Anything resolved through an effective-dated cost rule is ESTIMATED
until real settlement data replaces it. Anything that needs GA4, an ad platform
or an instrumentation table this project has not built yet is INCOMPLETE, and
stays INCOMPLETE — a missing cost input is NEVER treated as zero, because a zero
cost silently inflates margin and that is the one lie an analytics system must
never tell. A resolver may raise or lower the runtime grade against measured
coverage; the value here is the starting point and the worst case it may claim
without measuring anything.

Naming rule: ``id`` is a stable snake_case machine name. It is referenced by
``registry.py`` views, by the frontend's generated contract JSON and by stored
report snapshots — renaming one is a breaking change, so add a new id and
deprecate the old one instead.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.services.analytics.types import (
    DataSource,
    FormatId,
    Freshness,
    MetricQuality,
    _clean,
)

# --------------------------------------------------------------------------
# Controlled vocabularies
# --------------------------------------------------------------------------
# Everything below is validated at import time by `_validate()`. A typo in a
# basis or a dimension fails the process on boot rather than rendering a broken
# breakdown in production.

#: What a KPI counts over. Determines which table the resolver drives from.
BASES: tuple[str, ...] = (
    "order",  # one row per orders row
    "line",  # one row per order_items row
    "shipment",  # one row per shipments row
    "payment",  # one row per order_payments row
    "customer",  # one row per customer (users/customers)
    "inventory",  # products.stock snapshot
    "event",  # external event stream (GA4 / ads); no internal table
)

#: How refunds are handled in the figure.
REFUND_TREATMENTS: tuple[str, ...] = ("excluded", "deducted", "included", "n/a")

#: Whether the figure contains tax.
TAX_TREATMENTS: tuple[str, ...] = ("inclusive", "exclusive", "n/a")

#: Whether the figure contains the shipping charge.
SHIPPING_TREATMENTS: tuple[str, ...] = ("included", "excluded", "n/a")

#: Every dimension a KPI may declare. Mirrors `FilterKey` plus the two derived
#: keys (`date`, `cohort_month`) that are axes rather than filters.
DIMENSIONS: tuple[str, ...] = (
    "date",
    "cohort_month",
    "product",
    "sku",
    "category",
    "customer_segment",
    "new_or_returning",
    "source",
    "medium",
    "campaign",
    "device",
    "country",
    "state",
    "city",
    "payment_method",
    "payment_instrument",
    "payment_gateway",
    "courier",
    "order_status",
    "return_reason",
    "coupon",
    "channel",
)

# --------------------------------------------------------------------------
# Shared constants
# --------------------------------------------------------------------------
# Canonical revenue rule. Identical to `dashboard_service._REVENUE_STATUSES`,
# expressed as strings because this module is deliberately import-free of
# SQLAlchemy and the models — it must stay testable without a database.
_REVENUE_STATUSES: tuple[str, ...] = ("paid", "shipped", "delivered")
_ALL_STATUSES: tuple[str, ...] = (
    "pending",
    "paid",
    "shipped",
    "delivered",
    "cancelled",
    "refunded",
)
_NON_REVENUE_STATUSES: tuple[str, ...] = ("pending", "cancelled", "refunded")

# Recognition rule. A sale counts in the period it was made, whatever happened
# to it afterwards; the reversal is a separate event in its own period. The
# status graph only allows `refunded` from a paid state, so a refunded order was
# necessarily paid once. `refunded` is admitted only when a reversal can
# actually be DATED for the order (orders.refunded_at, or a dated return refund)
# — a refunded row with neither has a sale nothing would ever reverse, and
# admitting it would overstate revenue permanently.
#: `refunded` is in the list, but conditionally — see `_RECOGNISED_WHERE` for the
#: condition. The tuple stays in the real status vocabulary so the frontend can
#: render it as status chips; the prose lives in the formula and the caveats.
_RECOGNISED_SALE_STATUSES: tuple[str, ...] = (
    "paid",
    "shipped",
    "delivered",
    "refunded",
)
_UNRECOGNISED_STATUSES: tuple[str, ...] = ("pending", "cancelled")

_RECOGNISED_WHERE = (
    "WHERE orders.status IN (paid, shipped, delivered) OR (orders.status = refunded "
    "AND a reversal is dated for the order)"
)

_RECOGNITION_NOTE = (
    "Recognition: the sale counts in the period of orders.created_at if the order "
    "reached a paid state, INCLUDING one refunded later; the reversal is a separate "
    "event counted in the period of its own refunded_at. A sale refunded in a later "
    "month therefore does not restate the month it was made in — orders.status is "
    "mutable and REFUNDED is terminal, so a status-only rule silently rewrites closed "
    "periods."
)

_DOUBLE_REVERSAL_NOTE = (
    "Excluding refunded orders AND subtracting refunds reverses the same money twice "
    "and reports a negative figure for a window whose answer is zero. The revenue "
    "bridge balances either way — both sides share the input — so it cannot be used "
    "as evidence that this is right; see tests/test_analytics_revenue_recognition.py."
)

_PAID_VS_NET_NOTE = (
    "paid_order_value and net_revenue deliberately differ and must never be shown as "
    "the same series: paid_order_value keeps the LEGACY rule (a refunded order leaves "
    "the set entirely, nothing is subtracted) because it is the parity anchor against "
    "the legacy admin pages, while net_revenue recognises the sale and then deducts "
    "the reversal in the period it was issued."
)

_CCY_INR = (
    "Single-currency store. Every orders row carries currency='INR'; figures are "
    "summed as Decimal(12,2) with no FX conversion anywhere in the stack. The "
    "resolver asserts exactly one distinct currency per window and downgrades the "
    "metric to INCOMPLETE rather than summing mixed currencies naively."
)
_CCY_NA = "Not a monetary figure — currency does not apply."
_CCY_RATIO = (
    "Ratio of two INR figures computed over the same window, so the currency "
    "cancels; both sides must be single-currency for the ratio to be meaningful."
)

# Dimension bundles, so a breakdown that is valid for one order-level money KPI
# is valid for all of them and the frontend never offers a split that 500s.
_D_ORDER: tuple[str, ...] = (
    "date",
    "payment_method",
    "payment_instrument",
    "payment_gateway",
    "coupon",
    "order_status",
    "state",
    "city",
    "new_or_returning",
    "customer_segment",
    "channel",
)
_D_LINE: tuple[str, ...] = (
    "date",
    "product",
    "sku",
    "category",
    "payment_method",
    "coupon",
    "state",
    "city",
    "new_or_returning",
    "channel",
)
_D_SHIP: tuple[str, ...] = ("date", "courier", "state", "city", "payment_method", "channel")
_D_PAY: tuple[str, ...] = (
    "date",
    "payment_gateway",
    "payment_method",
    "payment_instrument",
    "device",
    "channel",
)
_D_CUST: tuple[str, ...] = (
    "date",
    "cohort_month",
    "customer_segment",
    "new_or_returning",
    "state",
    "city",
    "channel",
)
_D_MKT: tuple[str, ...] = ("date", "source", "medium", "campaign", "device", "channel")
_D_INV: tuple[str, ...] = ("product", "sku", "category")

# The allocation rule referenced by every ALLOCATED metric. Stated once so the
# tooltips and the reconciliation view quote the same sentence.
_ALLOCATION_RULE = (
    "order_items has no tax or discount column, so order-level discount_amount + "
    "payment_discount_amount is pushed down to lines pro-rata on quantity*unit_price "
    "using largest-remainder rounding to the paisa; the allocated parts always sum "
    "back to the order-level amount exactly."
)

_COST_RULE_NOTE = (
    "Resolved through effective-dated cost rules, not an observed charge; the value "
    "in force on the order date is used, so back-dating a rule restates history."
)

_MISSING_INPUT_NOTE = (
    "A missing cost input is never treated as zero — the figure is reported "
    "INCOMPLETE and names the input that is absent."
)


# --------------------------------------------------------------------------
# The definition shape
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class KpiDef:
    """One KPI: what it means, how it is computed, and what it is worth.

    Everything is JSON-serialisable so `dump_analytics_registry.py` can emit the
    identical metadata to the frontend. Presentation (icon, colour, card size)
    deliberately lives in the frontend's `presentation.js`, keyed by `id`.
    """

    #: Stable snake_case machine name. Referenced by views and stored snapshots.
    id: str
    #: Human label shown on the card.
    label: str
    #: One business sentence — what a founder would say this number means.
    description: str
    #: FormatId-compatible: money | int | pct | days | hours | ratio | compact.
    unit: str
    #: Human-readable computing expression, precise enough to argue with.
    formula: str
    #: One of BASES — which table the resolver drives from.
    basis: str
    #: For rates/ratios: what is on top. "" for absolute figures.
    numerator: str = ""
    #: For rates/ratios: what is underneath. "" for absolute figures.
    denominator: str = ""
    #: Order statuses that count toward the figure.
    included_statuses: tuple[str, ...] = ()
    #: Order statuses explicitly excluded (stated, never implied).
    excluded_statuses: tuple[str, ...] = ()
    #: excluded | deducted | included | n/a
    refund_treatment: str = "n/a"
    #: inclusive | exclusive | n/a
    tax_treatment: str = "n/a"
    #: included | excluded | n/a
    shipping_treatment: str = "n/a"
    #: How currency is handled / what would break with more than one.
    currency_handling: str = _CCY_NA
    #: Where the numbers come from. Financial figures must be INTERNAL_DB.
    source: DataSource = DataSource.INTERNAL_DB
    #: The honest grade given today's data model. See the module docstring.
    default_quality: MetricQuality = MetricQuality.AUTHORITATIVE
    #: Expected currency of the data; drives the cache TTL.
    freshness: Freshness = Freshness.DAILY
    #: Dimensions this KPI can legitimately be broken down by.
    dimensions: tuple[str, ...] = ()
    #: True = up is good, False = down is good, None = neither (context metric).
    higher_is_better: bool | None = None
    #: Bumped whenever the definition changes meaning. Start at 1.
    version: int = 1
    #: The exact sentence shown in the UI on hover. One sentence, no jargon.
    tooltip: str = ""
    #: Everything that would make someone misread the number.
    caveats: tuple[str, ...] = ()


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------

KPIS: tuple[KpiDef, ...] = (
    # ======================================================================
    # Revenue — five distinct figures, deliberately never interchangeable
    # ======================================================================
    KpiDef(
        id="order_value_created",
        label="Order Value Created",
        description=(
            "Everything customers asked us to sell them in the window, before we know "
            "whether the order stuck."
        ),
        unit="money",
        formula="SUM(orders.total_amount) over ALL statuses",
        basis="order",
        included_statuses=_ALL_STATUSES,
        excluded_statuses=(),
        refund_treatment="included",
        tax_treatment="inclusive",
        shipping_treatment="included",
        currency_handling=_CCY_INR,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.REALTIME,
        dimensions=_D_ORDER,
        higher_is_better=True,
        tooltip=(
            "Total value of every order created in this period, including orders that "
            "were never paid, were cancelled or were refunded."
        ),
        caveats=(
            "This is demand, not revenue. It includes pending, cancelled and refunded "
            "orders and will always be the largest of the five revenue figures.",
            "Includes tax, shipping and the COD surcharge; excludes nothing.",
            "Never put this on the same card row as paid_order_value without both "
            "labels visible — the gap between them is the drop-off, not an error.",
        ),
    ),
    KpiDef(
        id="paid_order_value",
        label="Paid Order Value",
        description=(
            "Value of orders that reached a paid state — the store's headline revenue "
            "figure as the dashboard reports it today."
        ),
        unit="money",
        formula=(
            "SUM(orders.total_amount) WHERE orders.status IN (paid, shipped, delivered)"
        ),
        basis="order",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        refund_treatment="excluded",
        tax_treatment="inclusive",
        shipping_treatment="included",
        currency_handling=_CCY_INR,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.REALTIME,
        dimensions=_D_ORDER,
        higher_is_better=True,
        tooltip=(
            "Value of orders that were paid, shipped or delivered in this period, "
            "including tax and shipping."
        ),
        caveats=(
            "Byte-identical to DashboardService._revenue_summary()['revenue'] and to "
            "AnalyticsService._summary()['revenue']. If this number ever disagrees "
            "with the dashboard headline, one of the two has a bug.",
            "This is the LEGACY definition, kept deliberately and frozen. It is the "
            "shadow-mode parity anchor: while the legacy admin pages and this "
            "subsystem are both live, it is the one figure that can be reconciled "
            "between them row for row, which is what makes the corrected figures "
            "provable. It must never be 'improved' — MarginService.paid_order_value "
            "restates the legacy query on purpose and is pinned by an equality test.",
            "Refunds are removed by status transition, not by subtraction: a refunded "
            "order leaves the set entirely, so a refund restates history for past "
            "periods rather than showing up as a deduction in the current one. That "
            "is a known defect of this definition, tolerated only because parity with "
            "the legacy pages is worth more than the correction here. net_revenue is "
            "the figure that recognises the sale and deducts the reversal instead.",
            _PAID_VS_NET_NOTE,
            "Includes tax, shipping and the COD surcharge, so it is not a merchandise "
            "figure and must not be compared with gross_merchandise_sales.",
        ),
    ),
    KpiDef(
        id="gross_merchandise_sales",
        label="Gross Merchandise Sales",
        description=(
            "Value of the goods themselves on orders that reached a paid state, before "
            "any discount, in the period the sale was made."
        ),
        unit="money",
        formula=(
            "SUM(order_items.quantity * order_items.unit_price) "
            f"{_RECOGNISED_WHERE}, dated by orders.created_at"
        ),
        basis="line",
        included_statuses=_RECOGNISED_SALE_STATUSES,
        excluded_statuses=_UNRECOGNISED_STATUSES,
        # The sale is not reduced by the refund; the reversal is a separate
        # event under `refunds` and reaches revenue through net_revenue.
        refund_treatment="included",
        tax_treatment="exclusive",
        shipping_treatment="excluded",
        currency_handling=_CCY_INR,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.HOURLY,
        dimensions=_D_LINE,
        higher_is_better=True,
        version=2,
        tooltip=(
            "Value of the products sold at list price in this period, before discounts "
            "and excluding tax and shipping — including sales that were refunded later."
        ),
        caveats=(
            _RECOGNITION_NOTE,
            "refund_treatment is 'included' and that is not an oversight: a sale that "
            "was refunded is still a sale that happened in this period. The reversal "
            "is measured by `refunds` and reaches the accounting basis through "
            "net_revenue, so it is deducted exactly once. " + _DOUBLE_REVERSAL_NOTE,
            "A cancelled order is in NEITHER this figure nor refunds. A paid order "
            "cancelled by the customer stamps cancelled_at and never refunded_at, so "
            "no reversal can be dated for it; recognising the sale would book revenue "
            "nothing ever reverses.",
            "Excludes tax, shipping and the COD surcharge, and is BEFORE discount — so "
            "it will not equal paid_order_value and is not supposed to.",
            "AUTHORITATIVE rather than ALLOCATED because quantity and unit_price are "
            "stored columns on order_items; nothing is pushed down from the order.",
            "This is what profit_service.py calls `revenue` and what "
            "AnalyticsService._by_category sums — which is why the category breakdown "
            "on the Sales page does not add up to that page's own revenue card. That "
            "conflation is the bug this catalogue exists to end.",
            "The bridge to accounting revenue is: gross_merchandise_sales - discounts "
            "+ tax_collected + shipping_income + cod_surcharge_collected - refunds "
            "= net_revenue.",
        ),
    ),
    KpiDef(
        id="net_merchandise_sales",
        label="Net Merchandise Sales",
        description=(
            "What the goods actually sold for after every discount — the base every "
            "contribution margin is measured against."
        ),
        unit="money",
        formula=(
            "gross_merchandise_sales - SUM(allocated discount_amount + allocated "
            f"payment_discount_amount) {_RECOGNISED_WHERE}, dated by orders.created_at"
        ),
        basis="line",
        included_statuses=_RECOGNISED_SALE_STATUSES,
        excluded_statuses=_UNRECOGNISED_STATUSES,
        refund_treatment="included",
        tax_treatment="exclusive",
        shipping_treatment="excluded",
        currency_handling=_CCY_INR,
        default_quality=MetricQuality.ALLOCATED,
        freshness=Freshness.HOURLY,
        dimensions=_D_LINE,
        higher_is_better=True,
        version=2,
        tooltip=(
            "Value of the products sold in this period after discounts, excluding tax "
            "and shipping — including sales that were refunded later."
        ),
        caveats=(
            _RECOGNITION_NOTE,
            "Same recognition as gross_merchandise_sales, so CM1's base is the sales "
            "the period actually made. The contribution-margin cascade has NO "
            "contra-revenue term — refunds carry tax, shipping and the COD surcharge, "
            "none of which are in this figure, so they cannot honestly be netted off "
            "CM1. A window whose sales were refunded later therefore shows margin the "
            "store did not keep; MarginService.compute() emits a warning naming those "
            "orders, and the reversal itself is in the revenue bridge.",
            _ALLOCATION_RULE,
            "ALLOCATED only when broken down by product, SKU or category. The "
            "store-level total is exact, because the allocated parts sum back to the "
            "order-level discount by construction.",
            "Includes both coupon discount (orders.discount_amount) and payment-method "
            "incentive (orders.payment_discount_amount); loyalty-minted coupons land "
            "in the first of those, so they reduce this figure too.",
            "Still excludes tax, shipping and the COD surcharge. This is CM1's base, "
            "not an accounting revenue figure.",
        ),
    ),
    KpiDef(
        id="net_revenue",
        label="Net Revenue",
        description=(
            "What this period earned: the sales it made, less the money it gave back "
            "in it — the accounting-basis revenue figure."
        ),
        unit="money",
        formula=(
            f"SUM(orders.total_amount) {_RECOGNISED_WHERE}, dated by orders.created_at "
            "- refunds dated by refunded_at in the same window"
        ),
        basis="order",
        included_statuses=_RECOGNISED_SALE_STATUSES,
        excluded_statuses=_UNRECOGNISED_STATUSES,
        refund_treatment="deducted",
        tax_treatment="inclusive",
        shipping_treatment="included",
        currency_handling=_CCY_INR,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.HOURLY,
        dimensions=_D_ORDER,
        higher_is_better=True,
        version=2,
        tooltip=(
            "Sales made in this period, including ones refunded later, minus every "
            "refund issued in this period. Includes tax and shipping."
        ),
        caveats=(
            _RECOGNITION_NOTE,
            "The reversal is applied ONCE, by subtraction. The refunded order stays in "
            "the sale side; only `refunds` removes it. " + _DOUBLE_REVERSAL_NOTE,
            "An order placed and fully refunded inside one window nets to 0.00 here. A "
            "window that contains only the reversal — the January sale refunded in "
            "March, read for March — is legitimately NEGATIVE: that is what happened "
            "in March, and it is not an error to be filtered out.",
            _PAID_VS_NET_NOTE,
            "Includes tax collected on behalf of the government. It is revenue, not "
            "income; GST payable is not netted off.",
            "Exact bridge from the merchandise basis: gross_merchandise_sales "
            "- discounts + tax_collected + shipping_income + cod_surcharge_collected "
            "- refunds = net_revenue. The reconciliation view asserts this to the "
            "paisa — but the identity balances even when the recognition is wrong, "
            "because both of its sides are built from the same input. It proves the "
            "arithmetic, never the definition.",
            "Reported as UNKNOWN, never as a number, when any refund dated in the "
            "window carries no determinable amount — see the refunds KPI.",
        ),
    ),
    # ======================================================================
    # Revenue components — the terms of the bridge
    # ======================================================================
    KpiDef(
        id="gross_merchandise_value",
        label="Gross Merchandise Value (GMV)",
        description=(
            "Merchandise value of every order placed in the window regardless of "
            "whether it was ever paid."
        ),
        unit="money",
        formula="SUM(order_items.quantity * order_items.unit_price) over ALL statuses",
        basis="line",
        included_statuses=_ALL_STATUSES,
        excluded_statuses=(),
        refund_treatment="included",
        tax_treatment="exclusive",
        shipping_treatment="excluded",
        currency_handling=_CCY_INR,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.HOURLY,
        dimensions=_D_LINE,
        higher_is_better=True,
        tooltip=(
            "List-price value of the products on every order placed in this period, "
            "including orders that were never paid."
        ),
        caveats=(
            "GMV is a demand measure used for marketplace-style comparison. It is the "
            "all-status twin of gross_merchandise_sales, exactly as "
            "order_value_created is the all-status twin of paid_order_value.",
            "Excludes tax, shipping and the COD surcharge, and is before discount.",
            "Do not report GMV as revenue. On a COD-heavy store the gap between GMV "
            "and net_revenue is routinely 20-40%.",
        ),
    ),
    KpiDef(
        id="discounts",
        label="Discounts",
        description="Total value given away as coupon and payment-method discounts.",
        unit="money",
        formula=(
            "SUM(orders.discount_amount + orders.payment_discount_amount) "
            f"{_RECOGNISED_WHERE}"
        ),
        basis="order",
        included_statuses=_RECOGNISED_SALE_STATUSES,
        excluded_statuses=_UNRECOGNISED_STATUSES,
        refund_treatment="included",
        tax_treatment="exclusive",
        shipping_treatment="excluded",
        currency_handling=_CCY_INR,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.HOURLY,
        dimensions=_D_ORDER,
        higher_is_better=None,
        version=2,
        tooltip=(
            "Total coupon and payment-method discount given on the sales this period "
            "made, including ones refunded later."
        ),
        caveats=(
            "A term of the revenue bridge, so it is summed over exactly the same "
            "recognised sales as gross_merchandise_sales — otherwise the identity "
            "stops closing. " + _RECOGNITION_NOTE,
            "Two distinct pots are summed: orders.discount_amount (coupon engine, "
            "including loyalty-minted coupons) and orders.payment_discount_amount "
            "(instrument incentive such as a UPI cashback). Split them by the coupon "
            "and payment_instrument dimensions when the distinction matters.",
            "Discount never applies to shipping in this store — see the Order model "
            "comment — so this can never exceed the merchandise subtotal.",
            "AnalyticsService's `discounts` card sums orders.discount_amount ONLY and "
            "therefore under-reports giveaway by the whole payment-incentive pot.",
            "Direction is deliberately unset: discounting is a lever, not a fault.",
        ),
    ),
    KpiDef(
        id="tax_collected",
        label="Tax Collected",
        description=(
            "Tax charged to customers on paid orders and held on behalf of the "
            "government."
        ),
        unit="money",
        formula=f"SUM(orders.tax_amount) {_RECOGNISED_WHERE}",
        basis="order",
        included_statuses=_RECOGNISED_SALE_STATUSES,
        excluded_statuses=_UNRECOGNISED_STATUSES,
        refund_treatment="included",
        tax_treatment="n/a",
        shipping_treatment="excluded",
        currency_handling=_CCY_INR,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.HOURLY,
        dimensions=_D_ORDER,
        higher_is_better=None,
        version=2,
        tooltip=(
            "Tax charged on the sales this period made, snapshotted at order time."
        ),
        caveats=(
            "A term of the revenue bridge, so it is summed over exactly the same "
            "recognised sales as gross_merchandise_sales. " + _RECOGNITION_NOTE,
            "Tax refunded with a returned order is inside `refunds`, not netted off "
            "here — this figure is what was charged, which is what a GST return asks "
            "for; the credit note belongs to the period it was issued in.",
            "Snapshotted from the product's tax rows at checkout, so changing a tax "
            "rate today does not restate historical orders — which is correct.",
            "No tax is charged on shipping in this store; the carrier issues its own "
            "invoice.",
            "This is a liability, not income. It is inside paid_order_value and "
            "net_revenue and outside every merchandise and margin figure.",
            "HSN is now captured per product but is not snapshotted onto the order "
            "line, so this total still cannot be broken down by HSN; the per-line "
            "rate slab and the place of supply that decides CGST/SGST against IGST "
            "are not recorded anywhere. It is not compliance-grade and cannot be "
            "split for a GST return without the HSN_TAX_DETAIL capability.",
        ),
    ),
    KpiDef(
        id="shipping_income",
        label="Shipping Income",
        description="Shipping charged to customers on paid orders.",
        unit="money",
        formula=f"SUM(orders.shipping_amount) {_RECOGNISED_WHERE}",
        basis="order",
        included_statuses=_RECOGNISED_SALE_STATUSES,
        excluded_statuses=_UNRECOGNISED_STATUSES,
        refund_treatment="included",
        tax_treatment="exclusive",
        shipping_treatment="n/a",
        currency_handling=_CCY_INR,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.HOURLY,
        dimensions=_D_ORDER,
        higher_is_better=True,
        version=2,
        tooltip=(
            "Shipping charged to customers on the sales this period made, including "
            "ones refunded later."
        ),
        caveats=(
            "A term of the revenue bridge, so it is summed over exactly the same "
            "recognised sales as gross_merchandise_sales. " + _RECOGNITION_NOTE,
            "orders.shipping_amount is what the CUSTOMER PAID. It is a carrier-quoted "
            "figure passed through at checkout, but it is income, not cost.",
            "profit_service.py subtracts orders.shipping_amount as a pure cost and "
            "never adds it back as income, which double-penalises every C-level. CM2 "
            "here adds this in and subtracts the real carrier charge separately.",
            "The actual carrier charge is shipments.shipment_cost (nullable) or a cost "
            "rule — see forward_shipping inside cm2, never this column.",
        ),
    ),
    KpiDef(
        id="cod_surcharge_collected",
        label="COD Surcharge Collected",
        description="Cash-on-delivery fees charged to customers on paid orders.",
        unit="money",
        formula=f"SUM(orders.cod_surcharge_amount) {_RECOGNISED_WHERE}",
        basis="order",
        included_statuses=_RECOGNISED_SALE_STATUSES,
        excluded_statuses=_UNRECOGNISED_STATUSES,
        refund_treatment="included",
        tax_treatment="exclusive",
        shipping_treatment="excluded",
        currency_handling=_CCY_INR,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.HOURLY,
        dimensions=_D_ORDER,
        higher_is_better=None,
        version=2,
        tooltip=(
            "COD fees collected on the sales this period made, including ones refunded "
            "later."
        ),
        caveats=(
            "A term of the revenue bridge, so it is summed over exactly the same "
            "recognised sales as gross_merchandise_sales. " + _RECOGNITION_NOTE,
            "Snapshotted from the `cod.flat_surcharge` setting at checkout; changing "
            "the setting does not retro-apply to existing orders.",
            "Zero for prepaid orders by construction — always split by payment_method "
            "before drawing conclusions from a trend.",
            "This is a term of the revenue bridge; it sits inside paid_order_value and "
            "net_revenue and outside every merchandise figure.",
        ),
    ),
    KpiDef(
        id="refunds",
        label="Refunds",
        description="Money returned to customers in the window.",
        unit="money",
        formula=(
            "SUM(returns.refund_amount) WHERE returns.refunded_at IN period "
            "+ SUM(orders.total_amount) WHERE orders.refunded_at IN period AND the "
            "order has no refunded return row"
        ),
        basis="order",
        # Not just `refunded`: a partially refunded order keeps its paid status,
        # and the refund is dated by refunded_at whatever the order's status is.
        included_statuses=_RECOGNISED_SALE_STATUSES,
        excluded_statuses=_UNRECOGNISED_STATUSES,
        refund_treatment="n/a",
        tax_treatment="inclusive",
        shipping_treatment="included",
        currency_handling=_CCY_INR,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.HOURLY,
        dimensions=_D_ORDER + ("return_reason",),
        higher_is_better=False,
        version=2,
        tooltip="Money refunded to customers in this period, dated to the refund.",
        caveats=(
            "This is the ONLY place a reversal is applied. The refunded order stays "
            "inside gross_merchandise_sales and net_revenue's sale side, and this "
            "figure removes it once. " + _DOUBLE_REVERSAL_NOTE,
            "Two sources must be unioned without double counting: returns-driven "
            "refunds carry an explicit returns.refund_amount, while a whole-order "
            "refund has NO amount column and is valued at orders.total_amount. The "
            "second source is only counted when the order has no refunded return row.",
            "KNOWN UNDER-COUNT from that de-duplication: an order partially refunded "
            "through a return and THEN refunded in full at order level reverses only "
            "the return amount, because orders has no column holding the remainder "
            "OrderService.refund actually sent back. MarginService emits a warning "
            "naming the order and the shortfall. Fixing it means valuing the "
            "whole-order refund at total_amount minus the returns already reversed, "
            "which changes this definition and the aggregation job that mirrors it.",
            "returns.refund_amount is NULLABLE and is only stamped at approval, so a "
            "refund can be dated without ever being valued. Those rows are counted and "
            "named, and refunds + net_revenue are reported as UNKNOWN — never as the "
            "zero a COALESCE would produce.",
            "Dated by refunded_at, not by the original order date — so a refund lands "
            "in the period it was issued and does not restate a closed month.",
            "A partially refunded order keeps its paid/shipped/delivered status, so "
            "partial refunds are visible here but invisible in paid_order_value.",
            "COD refunds route through refund_method='manual' and may be settled "
            "outside the gateway; the amount is still recorded here.",
        ),
    ),
    KpiDef(
        id="returns",
        label="Returns",
        description="Number of return requests customers opened in the window.",
        unit="int",
        formula="COUNT(returns.id) WHERE returns.requested_at IN period",
        basis="order",
        included_statuses=(),
        excluded_statuses=(),
        refund_treatment="n/a",
        currency_handling=_CCY_NA,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.HOURLY,
        dimensions=("date", "return_reason", "product", "sku", "category", "state", "city"),
        higher_is_better=False,
        tooltip="Number of return requests opened by customers in this period.",
        caveats=(
            "Counts requests, not outcomes. Rejected and cancelled returns are "
            "included here; filter by status for approved-only volume.",
            "Dated by requested_at, so a return opened against an old order counts in "
            "the current period — this is intentional and is why returns divided by "
            "this period's orders is a misleading ratio. Use return_rate.",
            "One return request can cover several lines of one order; it is never "
            "split across orders.",
        ),
    ),
    # ======================================================================
    # Order and unit counts
    # ======================================================================
    KpiDef(
        id="orders_count",
        label="Orders",
        description="Every order created in the window, whatever became of it.",
        unit="int",
        formula="COUNT(orders.id) over ALL statuses",
        basis="order",
        included_statuses=_ALL_STATUSES,
        currency_handling=_CCY_NA,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.REALTIME,
        dimensions=_D_ORDER,
        higher_is_better=True,
        tooltip="Number of orders created in this period, in any status.",
        caveats=(
            "A pending order row is created when checkout is initiated, so this counts "
            "checkout attempts that got as far as an order, not completed purchases.",
            "The dashboard's `orders` card counts paid_orders, not this. The two "
            "diverge by exactly the abandoned and cancelled orders.",
        ),
    ),
    KpiDef(
        id="paid_orders",
        label="Paid Orders",
        description="Orders that reached a paid state in the window.",
        unit="int",
        formula="COUNT(orders.id) WHERE orders.status IN (paid, shipped, delivered)",
        basis="order",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        currency_handling=_CCY_NA,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.REALTIME,
        dimensions=_D_ORDER,
        higher_is_better=True,
        tooltip="Number of orders that were paid, shipped or delivered in this period.",
        caveats=(
            "Identical to DashboardService._revenue_summary()['count'] and the "
            "denominator of aov.",
            "A COD order counts as paid from the moment it is confirmed, before the "
            "carrier has collected anything — cod_delivery_rate is what tells you "
            "whether the cash actually arrived.",
        ),
    ),
    KpiDef(
        id="completed_orders",
        label="Completed Orders",
        description="Orders that made it all the way to delivered.",
        unit="int",
        formula="COUNT(orders.id) WHERE orders.status = 'delivered'",
        basis="order",
        included_statuses=("delivered",),
        excluded_statuses=("pending", "paid", "shipped", "cancelled", "refunded"),
        currency_handling=_CCY_NA,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.HOURLY,
        dimensions=_D_ORDER,
        higher_is_better=True,
        tooltip="Number of orders delivered to the customer in this period.",
        caveats=(
            "Dated by orders.created_at like every other order KPI, so a recent window "
            "always looks short — orders placed near the end of it have not had time "
            "to be delivered. Compare cohorts of equal age, not calendar periods.",
            "Status is the latest hop only. An order delivered and later refunded is "
            "no longer counted here.",
        ),
    ),
    KpiDef(
        id="cancelled_orders",
        label="Cancelled Orders",
        description="Orders cancelled before completion in the window.",
        unit="int",
        formula="COUNT(orders.id) WHERE orders.status = 'cancelled'",
        basis="order",
        included_statuses=("cancelled",),
        excluded_statuses=("pending", "paid", "shipped", "delivered", "refunded"),
        currency_handling=_CCY_NA,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.HOURLY,
        dimensions=_D_ORDER,
        higher_is_better=False,
        tooltip="Number of orders cancelled in this period.",
        caveats=(
            "Mixes two very different events: a customer cancelling an unpaid order "
            "and an admin cancelling a paid one. orders.cancelled_at combined with "
            "paid_at separates them; orders.refund_reason usually carries why.",
            "A cancelled order that was already paid should normally end as refunded, "
            "not cancelled — a rising count of paid-then-cancelled orders is an "
            "operational signal, not an analytics one.",
        ),
    ),
    KpiDef(
        id="units_sold",
        label="Units Sold",
        description="Number of individual items sold on paid orders.",
        unit="int",
        formula=(
            "SUM(order_items.quantity) WHERE orders.status IN (paid, shipped, delivered)"
        ),
        basis="line",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        currency_handling=_CCY_NA,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.HOURLY,
        dimensions=_D_LINE,
        higher_is_better=True,
        tooltip="Number of individual items sold on paid orders in this period.",
        caveats=(
            "Returned units are not deducted — this is units shipped out, not units "
            "kept. Pair with return_rate.",
            "There are no product variants in this store, so a unit is always one SKU; "
            "a combo pack counts as one unit even though it contains several products.",
        ),
    ),
    KpiDef(
        id="aov",
        label="Average Order Value",
        description="Average amount a customer pays per paid order, all-in.",
        unit="money",
        formula="paid_order_value / paid_orders",
        basis="order",
        numerator="SUM(orders.total_amount) WHERE status IN (paid, shipped, delivered)",
        denominator="COUNT(orders.id) WHERE status IN (paid, shipped, delivered)",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        refund_treatment="excluded",
        tax_treatment="inclusive",
        shipping_treatment="included",
        currency_handling=_CCY_INR,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.HOURLY,
        dimensions=_D_ORDER,
        higher_is_better=True,
        tooltip=(
            "Average value of a paid order in this period, including tax and shipping."
        ),
        caveats=(
            "Includes tax, shipping and the COD surcharge, so it is larger than the "
            "average basket of goods. average_selling_price is the merchandise view.",
            "Identical to the dashboard's AOV card; zero orders yields 0, not an error.",
            "A mean, so a single large order moves it. Median AOV is the honest "
            "headline on low volume.",
        ),
    ),
    KpiDef(
        id="average_selling_price",
        label="Average Selling Price",
        description="Average list price realised per unit of merchandise sold.",
        unit="money",
        formula="gross_merchandise_sales / units_sold",
        basis="line",
        numerator="SUM(order_items.quantity * order_items.unit_price)",
        denominator="SUM(order_items.quantity)",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        refund_treatment="excluded",
        tax_treatment="exclusive",
        shipping_treatment="excluded",
        currency_handling=_CCY_INR,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.HOURLY,
        dimensions=_D_LINE,
        higher_is_better=True,
        tooltip=(
            "Average price per unit sold before discount, excluding tax and shipping."
        ),
        caveats=(
            "Before discount, because unit_price is the snapshotted list price and "
            "there is no per-line discount column. Divide net_merchandise_sales by "
            "units_sold for the discounted equivalent.",
            "A mix metric: it moves when the product mix shifts even if no price "
            "changed. Split by category before reading it as a pricing signal.",
        ),
    ),
    # ======================================================================
    # Cost and margin — every input coverage-checked, none ever zero-filled
    # ======================================================================
    KpiDef(
        id="cogs",
        label="COGS",
        description="What the goods sold in the window cost us to buy or make.",
        unit="money",
        formula=(
            "SUM(order_items.quantity * order_items.unit_cost) "
            "WHERE orders.status IN (paid, shipped, delivered) AND unit_cost IS NOT NULL"
        ),
        basis="line",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        refund_treatment="excluded",
        tax_treatment="exclusive",
        shipping_treatment="excluded",
        currency_handling=_CCY_INR,
        default_quality=MetricQuality.INCOMPLETE,
        freshness=Freshness.HOURLY,
        dimensions=_D_LINE,
        higher_is_better=False,
        tooltip=(
            "Cost of the goods sold on paid orders in this period, shown with the share "
            "of lines that actually have a cost recorded."
        ),
        caveats=(
            "order_items.unit_cost is NULLABLE — orders placed before cost tracking "
            "existed have no snapshot. Every figure derived from COGS must be shown "
            "with cost_coverage_pct = costed lines / total lines.",
            "Lines with no unit_cost are EXCLUDED from the sum, never counted as zero "
            "cost. profit_service.py coalesces them to 0, which understates COGS and "
            "overstates every C-level whenever coverage is below 100%.",
            "Quality rises to AUTHORITATIVE only when cost_coverage_pct is 100 for the "
            "window; below that it stays INCOMPLETE however small the gap.",
            "unit_cost is a snapshot of products.cost at the moment of sale, so "
            "correcting a product's cost today does not restate historical margin.",
        ),
    ),
    KpiDef(
        id="gross_profit",
        label="Gross Profit",
        description="Merchandise sales after discount, less the cost of those goods.",
        unit="money",
        formula="net_merchandise_sales - cogs",
        basis="line",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        refund_treatment="excluded",
        tax_treatment="exclusive",
        shipping_treatment="excluded",
        currency_handling=_CCY_INR,
        default_quality=MetricQuality.INCOMPLETE,
        freshness=Freshness.HOURLY,
        dimensions=_D_LINE,
        higher_is_better=True,
        tooltip=(
            "Discounted merchandise sales minus the cost of those goods, before any "
            "operating cost."
        ),
        caveats=(
            "Numerically identical to cm1 under the current definitions. Both ids "
            "exist because the margin cascade needs an explicit C1 term, but they must "
            "never appear as two separate figures on one screen.",
            "Inherits COGS's coverage gap and its INCOMPLETE grade.",
            "Excludes shipping, gateway fees, packaging and fulfilment entirely — it "
            "is not a measure of whether an order made money. CM2 is.",
        ),
    ),
    KpiDef(
        id="gross_margin_pct",
        label="Gross Margin %",
        description="Gross profit as a share of discounted merchandise sales.",
        unit="pct",
        formula="gross_profit / net_merchandise_sales * 100",
        basis="line",
        numerator="net_merchandise_sales - cogs",
        denominator="net_merchandise_sales",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        refund_treatment="excluded",
        tax_treatment="exclusive",
        shipping_treatment="excluded",
        currency_handling=_CCY_RATIO,
        default_quality=MetricQuality.INCOMPLETE,
        freshness=Freshness.HOURLY,
        dimensions=_D_LINE,
        higher_is_better=True,
        tooltip=(
            "Gross profit as a percentage of discounted merchandise sales, excluding "
            "tax and shipping."
        ),
        caveats=(
            "Denominator is net_merchandise_sales (after discount). profit_service.py "
            "divides by pre-discount line revenue instead, so its c1_margin_pct reads "
            "several points lower than this on a discounted period.",
            "Returns None rather than 0 when the denominator is zero — a zero-sales "
            "period has no margin, it does not have 0% margin.",
            "Inherits COGS's coverage gap and its INCOMPLETE grade.",
        ),
    ),
    KpiDef(
        id="cm1",
        label="CM1 (Contribution Margin 1)",
        description="Merchandise contribution after product cost, before any operations.",
        unit="money",
        formula="net_merchandise_sales - cogs",
        basis="line",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        refund_treatment="excluded",
        tax_treatment="exclusive",
        shipping_treatment="excluded",
        currency_handling=_CCY_INR,
        default_quality=MetricQuality.INCOMPLETE,
        freshness=Freshness.HOURLY,
        dimensions=_D_LINE,
        higher_is_better=True,
        tooltip=(
            "What the goods contributed after their own cost, before shipping, fees or "
            "marketing."
        ),
        caveats=(
            "Base is net_merchandise_sales, so tax, shipping and the COD surcharge are "
            "all outside CM1 on both sides.",
            "Numerically identical to gross_profit — see that entry.",
            "INCOMPLETE whenever cost_coverage_pct < 100. " + _MISSING_INPUT_NOTE,
            "profit_service.py's C1 additionally subtracts shipping, packing and "
            "handling, so its C1 is closer to this catalogue's CM2 than to CM1. The "
            "two cascades are not interchangeable.",
        ),
    ),
    KpiDef(
        id="cm1_pct",
        label="CM1 %",
        description="CM1 as a share of discounted merchandise sales.",
        unit="pct",
        formula="cm1 / net_merchandise_sales * 100",
        basis="line",
        numerator="cm1",
        denominator="net_merchandise_sales",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        refund_treatment="excluded",
        tax_treatment="exclusive",
        shipping_treatment="excluded",
        currency_handling=_CCY_RATIO,
        default_quality=MetricQuality.INCOMPLETE,
        freshness=Freshness.HOURLY,
        dimensions=_D_LINE,
        higher_is_better=True,
        tooltip="CM1 as a percentage of discounted merchandise sales.",
        caveats=(
            "All three CM percentages share the same denominator "
            "(net_merchandise_sales) so CM1% > CM2% > CM3% is always a like-for-like "
            "comparison.",
            "None, not 0, when the denominator is zero.",
            "Inherits CM1's INCOMPLETE grade and coverage note.",
        ),
    ),
    KpiDef(
        id="cm2",
        label="CM2 (Contribution Margin 2)",
        description=(
            "What an order contributes after every variable cost of getting it to the "
            "customer."
        ),
        unit="money",
        formula=(
            "cm1 + shipping_income - gateway_fees - packaging - fulfilment "
            "- forward_shipping - return_rto_cost - marketplace_commission"
        ),
        basis="order",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        refund_treatment="deducted",
        tax_treatment="exclusive",
        shipping_treatment="included",
        currency_handling=_CCY_INR,
        default_quality=MetricQuality.INCOMPLETE,
        freshness=Freshness.HOURLY,
        dimensions=_D_ORDER,
        higher_is_better=True,
        tooltip=(
            "What orders contributed after product cost, shipping, payment fees, "
            "packaging, fulfilment and returns — before marketing."
        ),
        caveats=(
            "shipping_income is ADDED (orders.shipping_amount is what the customer "
            "paid) and forward_shipping is subtracted separately from "
            "shipments.shipment_cost, or from a cost rule when that is null. Treating "
            "orders.shipping_amount as a cost, as profit_service.py does, charges "
            "shipping twice.",
            "packaging, fulfilment, forward_shipping, return_rto_cost and "
            "marketplace_commission all resolve through effective-dated cost rules. "
            + _COST_RULE_NOTE,
            "gateway_fees are ESTIMATED — there is no gateway-fee column anywhere in "
            "the schema. They become ACTUAL only when settlement data is ingested.",
            _MISSING_INPUT_NOTE
            + " The response names every absent component, so an admin can see that "
            "CM2 is understated by, say, packaging rather than guessing.",
            "marketplace_commission is structurally zero in this single-channel "
            "deployment and is kept in the formula only so the cascade does not change "
            "shape if a marketplace channel is ever added.",
        ),
    ),
    KpiDef(
        id="cm2_pct",
        label="CM2 %",
        description="CM2 as a share of discounted merchandise sales.",
        unit="pct",
        formula="cm2 / net_merchandise_sales * 100",
        basis="order",
        numerator="cm2",
        denominator="net_merchandise_sales",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        refund_treatment="deducted",
        tax_treatment="exclusive",
        shipping_treatment="included",
        currency_handling=_CCY_RATIO,
        default_quality=MetricQuality.INCOMPLETE,
        freshness=Freshness.HOURLY,
        dimensions=_D_ORDER,
        higher_is_better=True,
        tooltip="CM2 as a percentage of discounted merchandise sales.",
        caveats=(
            "The numerator includes shipping income while the denominator excludes it, "
            "which is deliberate: it keeps all three CM percentages on one comparable "
            "base, but it means CM2% can exceed 100% on a heavily shipping-subsidised "
            "period.",
            "None, not 0, when the denominator is zero.",
            "Inherits every INCOMPLETE input of CM2.",
        ),
    ),
    KpiDef(
        id="cm3",
        label="CM3 (Contribution Margin 3)",
        description="What orders contributed after the marketing spent to win them.",
        unit="money",
        formula="cm2 - attributable_marketing_spend",
        basis="order",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        refund_treatment="deducted",
        tax_treatment="exclusive",
        shipping_treatment="included",
        currency_handling=_CCY_INR,
        source=DataSource.INTERNAL_DB,
        default_quality=MetricQuality.INCOMPLETE,
        freshness=Freshness.DAILY,
        dimensions=_D_ORDER + ("source", "medium", "campaign"),
        higher_is_better=True,
        tooltip=(
            "What orders contributed after product, operating and marketing costs — "
            "the closest figure to true per-order profit."
        ),
        caveats=(
            "Requires an ad-platform connection. With no spend data this is INCOMPLETE "
            "and is NOT shown as equal to CM2.",
            "Without campaign-level attribution the spend can only be applied at the "
            "store level, making CM3 a blended figure — a per-campaign CM3 needs both "
            "AD_PLATFORM and GA4 attribution.",
            "Excludes fixed overheads (rent, salaries, tooling). CM3 is a unit-economics "
            "figure, not net profit; profit_service.py's net_profit subtracts overheads "
            "on top.",
            _MISSING_INPUT_NOTE,
        ),
    ),
    KpiDef(
        id="cm3_pct",
        label="CM3 %",
        description="CM3 as a share of discounted merchandise sales.",
        unit="pct",
        formula="cm3 / net_merchandise_sales * 100",
        basis="order",
        numerator="cm3",
        denominator="net_merchandise_sales",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        refund_treatment="deducted",
        tax_treatment="exclusive",
        shipping_treatment="included",
        currency_handling=_CCY_RATIO,
        default_quality=MetricQuality.INCOMPLETE,
        freshness=Freshness.DAILY,
        dimensions=_D_ORDER,
        higher_is_better=True,
        tooltip="CM3 as a percentage of discounted merchandise sales.",
        caveats=(
            "Same denominator as CM1% and CM2%, so the three read as one cascade.",
            "None, not 0, when the denominator is zero. Can legitimately be negative "
            "during an acquisition push.",
            "Inherits every INCOMPLETE input of CM3, including the missing ad spend.",
        ),
    ),
    KpiDef(
        id="gateway_fees",
        label="Gateway Fees",
        description="What payment providers charge us to collect prepaid money.",
        unit="money",
        formula=(
            "SUM(order_payments.amount * fee_rule.pct + fee_rule.flat) for captured "
            "attempts, where fee_rule is the cost rule effective on paid_at for "
            "(gateway, payment_instrument)"
        ),
        basis="payment",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        refund_treatment="excluded",
        tax_treatment="exclusive",
        shipping_treatment="n/a",
        currency_handling=_CCY_INR,
        default_quality=MetricQuality.ESTIMATED,
        freshness=Freshness.DAILY,
        dimensions=_D_PAY,
        higher_is_better=False,
        tooltip=(
            "Estimated payment-provider fees on captured payments, from the fee rules "
            "in force at the time of payment."
        ),
        caveats=(
            "There is NO gateway-fee column anywhere in the schema. This is always "
            "ESTIMATED until settlement data is ingested, at which point it becomes "
            "ACTUAL and the two are reconciled.",
            _COST_RULE_NOTE,
            "Zero on COD legs, which never touch a gateway — the COD cost is the "
            "carrier's collection fee and lives in the fulfilment cost rule instead.",
            "GST on the fee is a separate rule component; a fee rule that omits it "
            "understates this by ~18%.",
        ),
    ),
    KpiDef(
        id="marketing_spend",
        label="Marketing Spend",
        description="Money spent on paid acquisition in the window.",
        unit="money",
        formula="SUM(ad_platform.spend) over connected accounts WHERE date IN period",
        basis="event",
        currency_handling=_CCY_INR,
        source=DataSource.ADS,
        default_quality=MetricQuality.INCOMPLETE,
        freshness=Freshness.DAILY,
        dimensions=_D_MKT,
        higher_is_better=None,
        tooltip="Total paid-media spend recorded for this period.",
        caveats=(
            "Requires an ad-platform connection. There is no internal table of ad "
            "spend, so with nothing connected this is INCOMPLETE, never zero.",
            "Ad platforms restate the last 24-72 hours as conversions settle; a "
            "yesterday figure will move.",
            "Agency fees, creative production and influencer payments are not in ad "
            "platform spend and must be added as a cost rule if they are to be "
            "included in CAC.",
        ),
    ),
    # ======================================================================
    # Acquisition and lifetime value
    # ======================================================================
    KpiDef(
        id="cac",
        label="Customer Acquisition Cost",
        description="What it costs in marketing to win one new customer.",
        unit="money",
        formula="marketing_spend / new_customers",
        basis="customer",
        numerator="SUM(ad_platform.spend) WHERE date IN period",
        denominator="COUNT(DISTINCT customers whose first paid order falls in period)",
        currency_handling=_CCY_INR,
        source=DataSource.ADS,
        default_quality=MetricQuality.INCOMPLETE,
        freshness=Freshness.DAILY,
        dimensions=_D_MKT,
        higher_is_better=False,
        tooltip=(
            "Marketing spend in this period divided by the number of customers who "
            "placed their first paid order in it."
        ),
        caveats=(
            "Requires an ad-platform connection; INCOMPLETE until one exists, and "
            "never computed as zero-cost acquisition.",
            "This is blended CAC: all spend over all new customers, including the ones "
            "who arrived organically. Paid-only CAC needs GA4 attribution.",
            "Spend and acquisition are dated to the same window, which mis-states CAC "
            "whenever spend and conversion are separated by a long consideration lag.",
            "None, not 0, when there were no new customers.",
        ),
    ),
    KpiDef(
        id="ltv",
        label="Customer Lifetime Value",
        description=(
            "Realised revenue an average customer has produced across all their orders "
            "to date."
        ),
        unit="money",
        formula=(
            "SUM(orders.total_amount) WHERE status IN (paid, shipped, delivered) "
            "- refunds, over each customer's whole history, / COUNT(DISTINCT customers "
            "with at least one paid order)"
        ),
        basis="customer",
        numerator="lifetime net_revenue per customer",
        denominator="COUNT(DISTINCT customers with >= 1 paid order)",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        refund_treatment="deducted",
        tax_treatment="inclusive",
        shipping_treatment="included",
        currency_handling=_CCY_INR,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.DAILY,
        dimensions=_D_CUST,
        higher_is_better=True,
        tooltip=(
            "Average net revenue a customer has produced across their whole history "
            "so far."
        ),
        caveats=(
            "REALISED, not predicted. This is history to date, not a forecast — no "
            "survival model, no discount rate, no extrapolation.",
            "Systematically understated for a young store: recent cohorts have had less "
            "time to repeat. Only compare cohorts of equal age, via cohort_month.",
            "Revenue-based, not margin-based. Margin LTV requires full cost coverage "
            "and inherits COGS's INCOMPLETE grade; it is not this number.",
            "Guest checkout is not supported, so every order belongs to a user and no "
            "customer is double counted.",
        ),
    ),
    KpiDef(
        id="ltv_to_cac_ratio",
        label="LTV : CAC",
        description="How many rupees of lifetime revenue each acquisition rupee buys.",
        unit="ratio",
        formula="ltv / cac",
        basis="customer",
        numerator="ltv",
        denominator="cac",
        currency_handling=_CCY_RATIO,
        source=DataSource.ADS,
        default_quality=MetricQuality.INCOMPLETE,
        freshness=Freshness.DAILY,
        dimensions=_D_CUST,
        higher_is_better=True,
        tooltip=(
            "Lifetime revenue per customer divided by what it cost to acquire one."
        ),
        caveats=(
            "INCOMPLETE while CAC is INCOMPLETE — a missing spend feed makes this "
            "unreportable, not infinite.",
            "The usual 3:1 benchmark assumes a MARGIN-based LTV. This LTV is "
            "revenue-based, so the same ratio here is a weaker result than it looks.",
            "LTV is under-counted for young cohorts, so this ratio improves simply by "
            "waiting.",
        ),
    ),
    KpiDef(
        id="cac_payback_period",
        label="CAC Payback Period",
        description="How long a customer takes to repay what we spent acquiring them.",
        unit="days",
        formula=(
            "days until cumulative CM2 per acquired customer equals CAC; computed as "
            "cac / ((cm2 / new_customers) / days_in_period)"
        ),
        basis="customer",
        numerator="cac",
        denominator="CM2 per new customer per day",
        currency_handling=_CCY_RATIO,
        source=DataSource.ADS,
        default_quality=MetricQuality.INCOMPLETE,
        freshness=Freshness.DAILY,
        dimensions=_D_CUST,
        higher_is_better=False,
        tooltip=(
            "Number of days a new customer takes to generate enough contribution "
            "margin to cover their acquisition cost."
        ),
        caveats=(
            "Needs BOTH ad spend and full cost coverage, so it is INCOMPLETE on two "
            "counts today.",
            "Reported in days; the figure most founders quote is months — divide by 30.",
            "Assumes contribution accrues at the period's average rate. On a store with "
            "a strong repeat cadence the true payback curve is stepped, not linear.",
            "None when CM2 per customer is zero or negative — an unprofitable cohort "
            "never pays back and must not be shown as a large number of days.",
        ),
    ),
    KpiDef(
        id="roas",
        label="ROAS",
        description="Revenue attributed to advertising per rupee of ad spend.",
        unit="ratio",
        formula="attributed net_revenue / marketing_spend",
        basis="event",
        numerator="net_revenue attributed to a campaign",
        denominator="marketing_spend",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        refund_treatment="deducted",
        tax_treatment="inclusive",
        shipping_treatment="included",
        currency_handling=_CCY_RATIO,
        source=DataSource.ADS,
        default_quality=MetricQuality.INCOMPLETE,
        freshness=Freshness.DAILY,
        dimensions=_D_MKT,
        higher_is_better=True,
        tooltip="Revenue attributed to ads for every rupee of ad spend in this period.",
        caveats=(
            "Requires an ad platform AND an attribution join. Without attribution this "
            "degrades to blended ROAS, which is the same number as "
            "marketing_efficiency_ratio — show that instead rather than mislabelling it.",
            "The revenue side is always this store's own accounting figure, never the "
            "platform's reported conversion value; platform-reported ROAS will be "
            "higher because of view-through and multi-touch double counting.",
            "Ignores COGS and every operating cost. A 4x ROAS on a 20% margin product "
            "still loses money.",
        ),
    ),
    KpiDef(
        id="marketing_efficiency_ratio",
        label="Marketing Efficiency Ratio (MER)",
        description="Total store revenue per rupee of total marketing spend.",
        unit="ratio",
        formula="net_revenue / marketing_spend",
        basis="event",
        numerator="net_revenue (whole store)",
        denominator="marketing_spend (all channels)",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        refund_treatment="deducted",
        tax_treatment="inclusive",
        shipping_treatment="included",
        currency_handling=_CCY_RATIO,
        source=DataSource.ADS,
        default_quality=MetricQuality.INCOMPLETE,
        freshness=Freshness.DAILY,
        dimensions=("date",),
        higher_is_better=True,
        tooltip=(
            "Whole-store revenue for every rupee of marketing spend, with no "
            "attribution involved."
        ),
        caveats=(
            "Deliberately attribution-free: it is the honest whole-business number and "
            "cannot be gamed by a platform's conversion window.",
            "Because the numerator includes organic and repeat revenue, MER always "
            "reads higher than a true incremental ROAS.",
            "Cannot be split by campaign — a per-campaign MER is a contradiction. Use "
            "the date dimension only.",
            "Requires an ad-platform connection for the denominator; INCOMPLETE "
            "without one.",
        ),
    ),
    # ======================================================================
    # Funnel and conversion
    # ======================================================================
    KpiDef(
        id="conversion_rate",
        label="Conversion Rate",
        description="Share of sessions that ended in a paid order.",
        unit="pct",
        formula="paid_orders / ga4.sessions * 100",
        basis="event",
        numerator="COUNT(orders.id) WHERE status IN (paid, shipped, delivered)",
        denominator="GA4 sessions",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        currency_handling=_CCY_NA,
        source=DataSource.GA4,
        default_quality=MetricQuality.INCOMPLETE,
        freshness=Freshness.DAILY,
        dimensions=_D_MKT + ("country", "state", "city"),
        higher_is_better=True,
        tooltip="Percentage of website sessions that resulted in a paid order.",
        caveats=(
            "Requires GA4. There is no session table in this database, so without GA4 "
            "the denominator does not exist and the metric is INCOMPLETE — never "
            "estimated from order counts.",
            "The numerator is deliberately this store's own order count, not GA4's "
            "purchase count; GA4 purchases are always the lower-trust figure and will "
            "differ by a few percent from consent and ad-blocking loss.",
            "Mixing an internal numerator with an external denominator means the two "
            "sides can be dated differently — both are windowed on the same calendar "
            "days in the store's timezone.",
        ),
    ),
    KpiDef(
        id="add_to_cart_rate",
        label="Add-to-Cart Rate",
        description="Share of product views that led to an add to cart.",
        unit="pct",
        formula="ga4.add_to_cart events / ga4.view_item events * 100",
        basis="event",
        numerator="add_to_cart events",
        denominator="view_item events",
        currency_handling=_CCY_NA,
        source=DataSource.GA4,
        default_quality=MetricQuality.INCOMPLETE,
        freshness=Freshness.DAILY,
        dimensions=_D_MKT + ("product", "sku", "category"),
        higher_is_better=True,
        tooltip="Percentage of product views that resulted in an add to cart.",
        caveats=(
            "Requires GA4 or the internal CART_EVENTS instrumentation. The cart is "
            "Redis-backed with no event log, so nothing in this database can produce "
            "either side of this ratio today.",
            "A Redis cart write is idempotent per product, so even with CART_EVENTS a "
            "quantity bump would need to be distinguished from a first add.",
            "Consent-gated and ad-blocked traffic is missing from both sides; the ratio "
            "survives that better than either raw count does.",
        ),
    ),
    KpiDef(
        id="cart_abandonment_rate",
        label="Cart Abandonment Rate",
        description="Share of carts that never became a checkout.",
        unit="pct",
        formula="(1 - begin_checkout events / carts with >= 1 add_to_cart) * 100",
        basis="event",
        numerator="carts that never reached begin_checkout",
        denominator="carts with at least one add to cart",
        currency_handling=_CCY_NA,
        source=DataSource.GA4,
        default_quality=MetricQuality.INCOMPLETE,
        freshness=Freshness.DAILY,
        dimensions=_D_MKT,
        higher_is_better=False,
        tooltip="Percentage of carts that were built but never taken to checkout.",
        caveats=(
            "Requires GA4 or CART_EVENTS. The cart lives in Redis as a mutable hash "
            "with no history, so an abandoned cart leaves no trace in this database "
            "once it expires — this cannot be backfilled.",
            "Distinct from checkout_abandonment_rate, which starts one step later and "
            "IS computable internally. Never show one as a proxy for the other.",
        ),
    ),
    KpiDef(
        id="checkout_abandonment_rate",
        label="Checkout Abandonment Rate",
        description="Share of started checkouts that never reached payment.",
        unit="pct",
        formula=(
            "COUNT(orders.id) WHERE orders.paid_at IS NULL / COUNT(orders.id) "
            "over ALL statuses * 100"
        ),
        basis="order",
        numerator="orders created that never reached a paid state",
        denominator="all orders created in the period",
        included_statuses=_ALL_STATUSES,
        currency_handling=_CCY_NA,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.HOURLY,
        dimensions=_D_ORDER,
        higher_is_better=False,
        tooltip=(
            "Percentage of checkouts that created an order but never reached a "
            "successful payment."
        ),
        caveats=(
            "Computable internally because a pending orders row is created the moment "
            "checkout is initiated — so this measures order-creation to payment, not "
            "the whole checkout.",
            "UNDER-COUNTS true abandonment: anyone who left the checkout page before "
            "an order row existed is invisible here. The full funnel needs GA4 or "
            "CART_EVENTS.",
            "Uses orders.paid_at rather than status, so an order that was paid and "
            "later cancelled is correctly not counted as abandoned.",
            "Recent windows over-report, because an order placed minutes ago may still "
            "be mid-payment. Exclude the last hour for a stable reading.",
        ),
    ),
    KpiDef(
        id="payment_success_rate",
        label="Payment Success Rate",
        description="Share of payment attempts the gateway actually captured.",
        unit="pct",
        formula=(
            "COUNT(order_payments) WHERE payment_status IN (paid, refunded, "
            "partially_refunded) / COUNT(order_payments) WHERE payment_status IN "
            "(paid, refunded, partially_refunded, failed) * 100"
        ),
        basis="payment",
        numerator="attempts that were captured at least once",
        denominator="attempts that reached a terminal captured-or-failed outcome",
        currency_handling=_CCY_NA,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.REALTIME,
        dimensions=_D_PAY,
        higher_is_better=True,
        tooltip=(
            "Percentage of payment attempts that were successfully captured by the "
            "gateway."
        ),
        caveats=(
            "A later refund does not make the original capture a failure, so refunded "
            "and partially_refunded attempts count as successes.",
            "CANCELLED attempts are excluded from the denominator entirely — they are "
            "abandonments, not declines, and belong to checkout_abandonment_rate. "
            "Including them would make a UX problem look like a gateway problem.",
            "PENDING and INITIATED attempts are still in flight and are excluded; a "
            "very recent window will therefore have a small denominator.",
            "Only counts attempts that produced an order_payments row. Orders predating "
            "the 2026-06-21 normalisation have none and are invisible here.",
            "COD legs never touch a gateway; filter by payment_gateway before reading "
            "this as a provider scorecard.",
        ),
    ),
    KpiDef(
        id="payment_failure_rate",
        label="Payment Failure Rate",
        description="Share of payment attempts the gateway declined.",
        unit="pct",
        formula=(
            "COUNT(order_payments) WHERE payment_status = 'failed' / COUNT(order_payments) "
            "WHERE payment_status IN (paid, refunded, partially_refunded, failed) * 100"
        ),
        basis="payment",
        numerator="attempts that failed at the gateway",
        denominator="attempts that reached a terminal captured-or-failed outcome",
        currency_handling=_CCY_NA,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.REALTIME,
        dimensions=_D_PAY,
        higher_is_better=False,
        tooltip="Percentage of payment attempts that were declined by the gateway.",
        caveats=(
            "Exactly 100 - payment_success_rate on the same denominator; showing both "
            "on one screen is redundant, not corroborating.",
            "A customer who retries and succeeds contributes one failure and one "
            "success, so this is an attempt rate, not a share of customers affected.",
            "Split by payment_instrument before acting: UPI collect and card 3DS fail "
            "for entirely different reasons.",
        ),
    ),
    # ======================================================================
    # Retention and customers
    # ======================================================================
    KpiDef(
        id="new_customers",
        label="New Customers",
        description="Customers who placed their first paid order in the window.",
        unit="int",
        formula=(
            "COUNT(DISTINCT orders.user_id) WHERE the customer's earliest order with "
            "status IN (paid, shipped, delivered) falls in the period"
        ),
        basis="customer",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        currency_handling=_CCY_NA,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.HOURLY,
        dimensions=_D_CUST,
        higher_is_better=True,
        tooltip=(
            "Number of customers who placed their first ever paid order in this period."
        ),
        caveats=(
            "First PURCHASE, not first registration. DashboardService's new_customers "
            "card counts users.created_at instead, which is signups — a materially "
            "different and always larger number. Do not compare the two.",
            "Judged against the customer's whole history, not just the window, so a "
            "returning customer is never miscounted as new.",
            "The denominator of CAC.",
        ),
    ),
    KpiDef(
        id="returning_customers",
        label="Returning Customers",
        description="Customers who bought in the window and had bought before.",
        unit="int",
        formula=(
            "COUNT(DISTINCT orders.user_id) WHERE the customer has a paid order in the "
            "period AND an earlier paid order before it"
        ),
        basis="customer",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        currency_handling=_CCY_NA,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.HOURLY,
        dimensions=_D_CUST,
        higher_is_better=True,
        tooltip=(
            "Number of customers who bought in this period and had also bought before "
            "it."
        ),
        caveats=(
            "new_customers + returning_customers equals the distinct buyers in the "
            "period exactly — every buyer is one or the other, never both.",
            "A customer who bought twice within the window counts once as new, not "
            "once as new and once as returning.",
        ),
    ),
    KpiDef(
        id="repeat_purchase_rate",
        label="Repeat Purchase Rate",
        description="Share of customers who have ever bought more than once.",
        unit="pct",
        formula=(
            "COUNT(DISTINCT customers with >= 2 paid orders as at period end) / "
            "COUNT(DISTINCT customers with >= 1 paid order as at period end) * 100"
        ),
        basis="customer",
        numerator="customers with 2 or more paid orders",
        denominator="customers with at least 1 paid order",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        currency_handling=_CCY_NA,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.DAILY,
        dimensions=_D_CUST,
        higher_is_better=True,
        tooltip=(
            "Percentage of all customers to date who have placed more than one paid "
            "order."
        ),
        caveats=(
            "Lifetime-to-date, not windowed: the period selects the as-at moment, not "
            "the orders counted. A period filter therefore moves this much more slowly "
            "than other cards.",
            "Structurally depressed by recent acquisition — a good month of new "
            "customers pushes this DOWN. Read it by cohort_month, not by calendar.",
            "For a supplement store the meaningful version is repeat within the "
            "product's consumption cycle; this unbounded version flatters slow repeaters.",
        ),
    ),
    KpiDef(
        id="customer_retention_rate",
        label="Customer Retention Rate",
        description="Share of the previous period's buyers who bought again in this one.",
        unit="pct",
        formula=(
            "COUNT(DISTINCT customers with a paid order in BOTH the previous and the "
            "current period) / COUNT(DISTINCT customers with a paid order in the "
            "previous period) * 100"
        ),
        basis="customer",
        numerator="customers active in both the previous and the current period",
        denominator="customers active in the previous period",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        currency_handling=_CCY_NA,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.DAILY,
        dimensions=_D_CUST,
        higher_is_better=True,
        tooltip=(
            "Percentage of customers who bought in the previous period and bought "
            "again in this one."
        ),
        caveats=(
            "The comparison period is the immediately preceding window of equal length, "
            "matching dashboard_service._period_bounds — change the date range and the "
            "definition of 'retained' changes with it.",
            "Highly sensitive to window length on a store whose repeat cycle is longer "
            "than the window: a 7-day view will read near zero for a 30-day product.",
            "None, not 0, when nobody bought in the previous period.",
        ),
    ),
    KpiDef(
        id="customer_churn_rate",
        label="Customer Churn Rate",
        description="Share of the previous period's buyers who did not come back.",
        unit="pct",
        formula="100 - customer_retention_rate",
        basis="customer",
        numerator="customers active in the previous period but not the current one",
        denominator="customers active in the previous period",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        currency_handling=_CCY_NA,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.DAILY,
        dimensions=_D_CUST,
        higher_is_better=False,
        tooltip=(
            "Percentage of customers who bought in the previous period and did not buy "
            "again in this one."
        ),
        caveats=(
            "The exact complement of customer_retention_rate — the same fact stated "
            "twice. Pick one per screen.",
            "'Churn' in a non-subscription store is lapse, not cancellation: nobody has "
            "left, they simply have not returned yet. A long enough window makes almost "
            "all of it reverse.",
            "None, not 100, when nobody bought in the previous period.",
        ),
    ),
    KpiDef(
        id="rfm_score",
        label="RFM Score",
        description=(
            "Composite recency, frequency and monetary score ranking each customer's "
            "value."
        ),
        unit="int",
        formula=(
            "quintile(days since last paid order, reversed) + quintile(count of paid "
            "orders) + quintile(lifetime net revenue); each 1-5, total 3-15"
        ),
        basis="customer",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        refund_treatment="deducted",
        tax_treatment="inclusive",
        shipping_treatment="included",
        currency_handling=_CCY_NA,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.DAILY,
        dimensions=_D_CUST,
        higher_is_better=True,
        tooltip=(
            "A 3 to 15 score combining how recently, how often and how much a customer "
            "buys."
        ),
        caveats=(
            "Quintiles are RELATIVE to the current customer base, so a customer's score "
            "can fall without their behaviour changing, simply because better customers "
            "joined. It ranks, it does not measure.",
            "Meaningless below roughly 100 customers — quintile boundaries collapse and "
            "the score becomes noise. The view states this rather than rendering it.",
            "Displayed as a 3-15 sum; the 555-style three-digit cell code is available "
            "as a segment label but is not this number.",
            "Monetary uses lifetime net revenue including tax and shipping, so a "
            "customer in a high-shipping region scores slightly higher for the same "
            "merchandise.",
        ),
    ),
    # ======================================================================
    # Returns, cancellations and fulfilment quality
    # ======================================================================
    KpiDef(
        id="return_rate",
        label="Return Rate",
        description="Share of units sold that customers sent back.",
        unit="pct",
        formula=(
            "SUM(return_items.quantity) WHERE returns.status NOT IN (rejected, "
            "cancelled) / SUM(order_items.quantity) WHERE orders.status IN (paid, "
            "shipped, delivered) * 100"
        ),
        basis="line",
        numerator="units on returns that were not rejected or cancelled",
        denominator="units sold on paid orders",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        currency_handling=_CCY_NA,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.DAILY,
        dimensions=_D_LINE + ("return_reason",),
        higher_is_better=False,
        tooltip=(
            "Percentage of units sold in this period that customers returned, by unit "
            "not by order."
        ),
        caveats=(
            "Numerator and denominator are dated to the ORDER, not the return request, "
            "so a return raised today is attributed back to the period the item was "
            "sold in. This restates recent periods as returns come in.",
            "Recent windows always under-report: the return window has not closed on "
            "the most recent sales.",
            "Rejected and customer-cancelled returns are excluded — they are claims, "
            "not returns. Partial returns are counted at unit granularity via "
            "return_items.quantity.",
            "Unit-based, not order-based. A one-line return from a five-line order is "
            "20% of that order, not a whole returned order.",
        ),
    ),
    KpiDef(
        id="refund_rate",
        label="Refund Rate",
        description="Share of paid order value that was given back.",
        unit="pct",
        formula="refunds / paid_order_value * 100",
        basis="order",
        numerator="refunds issued in the period",
        denominator="paid_order_value in the period",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=("pending", "cancelled"),
        refund_treatment="n/a",
        tax_treatment="inclusive",
        shipping_treatment="included",
        currency_handling=_CCY_RATIO,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.DAILY,
        dimensions=_D_ORDER + ("return_reason",),
        higher_is_better=False,
        tooltip="Refunds issued in this period as a percentage of paid order value.",
        caveats=(
            "MIXED DATING: the numerator is dated to the refund and the denominator to "
            "the order. On a growing store this understates the true cohort refund "
            "rate; on a shrinking one it overstates it.",
            "Money-based, unlike return_rate which is unit-based. The two will not "
            "agree and are not meant to.",
            "Includes refunds that had no return (goodwill, failed delivery, duplicate "
            "charge), so it is always the broader of the two measures.",
        ),
    ),
    KpiDef(
        id="cancellation_rate",
        label="Cancellation Rate",
        description="Share of orders that were cancelled.",
        unit="pct",
        formula=(
            "COUNT(orders.id) WHERE status = 'cancelled' / COUNT(orders.id) over ALL "
            "statuses * 100"
        ),
        basis="order",
        numerator="orders with status cancelled",
        denominator="all orders created in the period",
        included_statuses=_ALL_STATUSES,
        currency_handling=_CCY_NA,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.HOURLY,
        dimensions=_D_ORDER,
        higher_is_better=False,
        tooltip="Percentage of orders created in this period that were cancelled.",
        caveats=(
            "The denominator is ALL orders including never-paid pending ones, so a "
            "surge in abandoned checkouts dilutes this rate downward. Filter to paid "
            "orders for the operationally meaningful version.",
            "Does not distinguish customer-initiated from admin-initiated cancellation; "
            "orders.refund_reason usually carries the why.",
        ),
    ),
    KpiDef(
        id="rto_rate",
        label="RTO Rate",
        description="Share of dispatched shipments that came back undelivered.",
        unit="pct",
        formula=(
            "COUNT(shipments) WHERE shipment_status IN (rto_initiated, rto_delivered) / "
            "COUNT(shipments) WHERE shipment_status IN (shipped, in_transit, "
            "out_for_delivery, delivered, delivery_failed, rto_initiated, rto_delivered) "
            "* 100"
        ),
        basis="shipment",
        numerator="shipments in an RTO state",
        denominator="shipments that were actually dispatched",
        currency_handling=_CCY_NA,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.DAILY,
        dimensions=_D_SHIP,
        higher_is_better=False,
        tooltip=(
            "Percentage of dispatched shipments that were returned to origin instead "
            "of being delivered."
        ),
        caveats=(
            "The denominator counts only shipments that actually left — pending, "
            "ready_to_ship, pickup_scheduled and cancelled shipments are excluded so a "
            "backlog cannot flatter the rate.",
            "delivery_failed is NOT RTO. A failed attempt often succeeds on reattempt; "
            "only rto_initiated and rto_delivered count.",
            "Shipment rows exist only from the 2026-06-21 order normalisation onwards. "
            "Orders before that have no shipments row and are absent from both sides.",
            "Status is mirrored from courier webhooks, so it is only as current as the "
            "last scan; an in-flight RTO may still read in_transit.",
            "Split by payment_method — COD RTO is the number that matters and is "
            "typically several times the prepaid rate.",
        ),
    ),
    KpiDef(
        id="cod_delivery_rate",
        label="COD Delivery Rate",
        description="Share of dispatched COD orders where the cash was actually collected.",
        unit="pct",
        formula=(
            "COUNT(orders.id) WHERE payment_method IN (cod, split_cod) AND status = "
            "'delivered' / COUNT(orders.id) WHERE payment_method IN (cod, split_cod) "
            "AND shipped_at IS NOT NULL * 100"
        ),
        basis="order",
        numerator="dispatched COD orders that were delivered",
        denominator="COD orders that were dispatched",
        included_statuses=("shipped", "delivered", "cancelled", "refunded"),
        excluded_statuses=("pending",),
        currency_handling=_CCY_NA,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.DAILY,
        dimensions=_D_SHIP + ("state", "city"),
        higher_is_better=True,
        tooltip=(
            "Percentage of dispatched cash-on-delivery orders that reached the customer "
            "and were paid for."
        ),
        caveats=(
            "The economic inverse of RTO on the COD book: every point lost here is a "
            "parcel shipped twice for zero revenue.",
            "Includes split_cod, where only the balance was collected on delivery — the "
            "prepaid leg was already captured, so the loss on a failure is smaller.",
            "Delivered means the carrier says delivered; cash remittance is a separate "
            "settlement step this schema does not track. A BANK_CASH_FEED would be "
            "needed to confirm the money arrived.",
            "In-flight shipments sit in the denominator and not the numerator, so a "
            "recent window always reads low.",
        ),
    ),
    KpiDef(
        id="on_time_delivery_rate",
        label="On-Time Delivery Rate",
        description="Share of deliveries that arrived by the date promised to the customer.",
        unit="pct",
        formula=(
            "COUNT(shipments) WHERE delivered_at <= promised_delivery_at / "
            "COUNT(shipments) WHERE shipment_status = 'delivered' * 100"
        ),
        basis="shipment",
        numerator="shipments delivered on or before the promise date",
        denominator="shipments delivered",
        currency_handling=_CCY_NA,
        source=DataSource.COURIER,
        default_quality=MetricQuality.INCOMPLETE,
        freshness=Freshness.DAILY,
        dimensions=_D_SHIP,
        higher_is_better=True,
        tooltip=(
            "Percentage of delivered shipments that arrived on or before the promised "
            "date."
        ),
        caveats=(
            "THERE IS NO PROMISE DATE IN THE SCHEMA. Neither orders nor shipments "
            "carries a promised or estimated delivery date, so the numerator cannot be "
            "computed at all today — this is INCOMPLETE, and no substitute threshold is "
            "silently applied.",
            "Becomes computable in one of two ways: a courier SLA feed supplying an EDD "
            "per shipment, or an admin-configured promise window per courier and zone "
            "stored as a cost-rule-style effective-dated setting. The second route makes "
            "it ESTIMATED, not AUTHORITATIVE.",
            "avg_delivery_days is the honest fulfilment-speed metric available today "
            "and should be shown in this metric's place until a promise date exists.",
        ),
    ),
    KpiDef(
        id="avg_delivery_days",
        label="Average Delivery Time",
        description="Average days from dispatch to delivery.",
        unit="days",
        formula=(
            "AVG(shipments.delivered_at - shipments.shipped_at) in days WHERE "
            "shipment_status = 'delivered'"
        ),
        basis="shipment",
        numerator="sum of days from dispatch to delivery",
        denominator="count of delivered shipments",
        currency_handling=_CCY_NA,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.DAILY,
        dimensions=_D_SHIP,
        higher_is_better=False,
        tooltip="Average number of days a delivered shipment took from dispatch to arrival.",
        caveats=(
            "Measures the CARRIER leg only. Time from order to dispatch is the store's "
            "own handling time and is not in this figure — order-to-door is the sum of "
            "the two.",
            "Falls back to orders.shipped_at and orders.delivered_at for orders that "
            "predate the shipments table; those columns are dual-written and cover the "
            "whole history.",
            "Only delivered shipments are in scope, so a stuck or RTO'd parcel makes "
            "this look BETTER by leaving the sample. Read alongside rto_rate.",
            "A mean over a long-tailed distribution; the p90 is the number a customer "
            "experiences on a bad week.",
        ),
    ),
    # ======================================================================
    # Inventory
    # ======================================================================
    KpiDef(
        id="stock_value",
        label="Stock Value",
        description="What the inventory currently on hand cost us.",
        unit="money",
        formula="SUM(products.stock * products.cost) WHERE products.cost IS NOT NULL",
        basis="inventory",
        currency_handling=_CCY_INR,
        default_quality=MetricQuality.INCOMPLETE,
        freshness=Freshness.REALTIME,
        dimensions=_D_INV,
        higher_is_better=None,
        tooltip=(
            "Cost value of stock currently on hand, shown with the share of SKUs that "
            "have a cost recorded."
        ),
        caveats=(
            "products.cost is NULLABLE. SKUs without a cost are excluded from the sum "
            "and reported as a coverage gap; they are never valued at zero.",
            "A point-in-time snapshot of the CURRENT stock column, not a historical "
            "series. Selecting a past date range does not change this number, because "
            "there is no inventory ledger to read history from.",
            "Valued at cost. The retail-value variant multiplies by products.price "
            "instead and is a different, much larger number.",
            "Direction is unset: high stock is either healthy cover or trapped cash, "
            "and only days_of_inventory can tell them apart.",
        ),
    ),
    KpiDef(
        id="inventory_turnover",
        label="Inventory Turnover",
        description="How many times the stock on hand is sold through in a year.",
        unit="ratio",
        formula="annualised cogs / average inventory value at cost",
        basis="inventory",
        numerator="COGS for the period, annualised",
        denominator="average stock value at cost across the period",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        currency_handling=_CCY_RATIO,
        default_quality=MetricQuality.INCOMPLETE,
        freshness=Freshness.DAILY,
        dimensions=_D_INV,
        higher_is_better=True,
        tooltip=(
            "How many times a year the business sells through its average stock "
            "holding."
        ),
        caveats=(
            "Average inventory comes from the FORWARD-ONLY ledger "
            "(agg_inventory_daily via the avg: projection): a mean over the ledger "
            "days that exist in the window. A gap day is in neither numerator nor "
            "denominator — it is 'no data', not 'zero stock'; zero-filling would "
            "inflate turnover. Windows before the ledger began cannot be "
            "reconstructed and report the gap rather than substituting today's "
            "stock.",
            "Stock is valued at CURRENT cost, not the cost in force on the bucket "
            "day, so the denominator is ESTIMATED at best.",
            "Also inherits COGS's coverage gap, so it drops to INCOMPLETE with the "
            "percentage carried until unit_cost coverage reaches 100%.",
            "Annualising a short window on a seasonal product produces a wildly "
            "misleading turn figure; 90 days is the shortest window worth reading.",
        ),
    ),
    KpiDef(
        id="days_of_inventory",
        label="Days of Inventory",
        description="How many days of selling the current stock would cover.",
        unit="days",
        formula="365 / inventory_turnover",
        basis="inventory",
        numerator="average stock value at cost",
        denominator="COGS per day",
        included_statuses=_REVENUE_STATUSES,
        excluded_statuses=_NON_REVENUE_STATUSES,
        currency_handling=_CCY_RATIO,
        default_quality=MetricQuality.INCOMPLETE,
        freshness=Freshness.DAILY,
        dimensions=_D_INV,
        higher_is_better=False,
        tooltip=(
            "Number of days the stock currently on hand would last at the recent rate "
            "of sale."
        ),
        caveats=(
            "The reciprocal of inventory_turnover and INCOMPLETE for exactly the same "
            "reason: no inventory ledger, so no average stock level.",
            "Assumes demand continues at the window's rate, which is the assumption most "
            "likely to be wrong right before a promotion.",
            "Direction is set to lower-is-better for working capital, but a very low "
            "figure means an imminent stockout — this is a band, not a race to zero.",
            "Shelf life is now captured per product (`products.shelf_life_days`) but is "
            "NOT compared against this figure, and it is NULL on most of the catalogue. "
            "So a supplement can still show plenty of cover and expire before it sells, "
            "and nothing here flags it.",
        ),
    ),
    KpiDef(
        id="stockout_rate",
        label="Stockout Rate",
        description="Share of sellable products that are currently out of stock.",
        unit="pct",
        formula=(
            "COUNT(products.id) WHERE stock <= 0 / COUNT(products.id) over sellable "
            "products * 100"
        ),
        basis="inventory",
        numerator="products with stock at or below zero",
        denominator="all sellable products",
        currency_handling=_CCY_NA,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.REALTIME,
        dimensions=_D_INV,
        higher_is_better=False,
        tooltip="Percentage of sellable products that are out of stock right now.",
        caveats=(
            "POINT IN TIME, not a period rate. It answers 'what is out of stock now', "
            "not 'what share of the month was a SKU unavailable' — the time-weighted "
            "version needs an inventory ledger and does not exist yet.",
            "Because it is a live snapshot, a date-range filter has no effect on it. "
            "The view says so rather than silently ignoring the filter.",
            "Unweighted by demand: a dead SKU being out of stock counts the same as the "
            "bestseller being out. Weight by units_sold for the version that matters.",
        ),
    ),
    KpiDef(
        id="basket_attach_rate",
        label="Basket Attach Rate",
        description="Share of orders that contained more than one distinct product.",
        unit="pct",
        formula=(
            "SUM(agg_basket_pair_daily.orders_with_any_pair) / "
            "SUM(agg_basket_pair_daily.total_orders_in_bucket) * 100, both summed "
            "once per bucket_date"
        ),
        basis="order",
        numerator="orders holding at least two distinct products",
        denominator="orders that entered the co-occurrence universe",
        currency_handling=_CCY_NA,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.DAILY,
        dimensions=("date",),
        higher_is_better=True,
        tooltip=(
            "Percentage of orders in this period that contained more than one "
            "distinct product. It is the ceiling on what cross-sell can move."
        ),
        caveats=(
            "Counts DISTINCT PRODUCTS, not lines or units. Three of one product is a "
            "single-product basket and does not attach — quantity is a different "
            "question from breadth, and units_sold already answers it.",
            "Both halves are bucket-level scalars repeated on every pair row of their "
            "day, so they must be summed once per bucket_date. Summing them across "
            "rows multiplies each day by its pair count and reports a store many times "
            "larger than it is.",
            "Orders above the basket-size cap in agg_basket_pair_daily are excluded "
            "from BOTH halves, so the rate stays over one population; the view warns "
            "when any were dropped.",
            "Every order in the bucket counts regardless of status. The line fact's "
            "order_status is an ingestion-time snapshot that keeps moving, so filtering "
            "on it would make the figure depend on when ingestion ran.",
        ),
    ),
    KpiDef(
        id="basket_pairs_observed",
        label="Product Pairs Observed",
        description=(
            "Distinct product pairs bought together often enough for their lift to "
            "mean something."
        ),
        unit="int",
        formula=(
            "COUNT(DISTINCT (product_a_id, product_b_id)) WHERE "
            "SUM(pair_orders) >= resolvers.basket.SMALL_SAMPLE_MIN_PAIR_ORDERS"
        ),
        basis="order",
        numerator="pairs clearing the small-sample floor",
        currency_handling=_CCY_NA,
        default_quality=MetricQuality.AUTHORITATIVE,
        freshness=Freshness.DAILY,
        dimensions=("date",),
        higher_is_better=True,
        tooltip=(
            "How many product pairs were bought together often enough in this period "
            "for their affinity to be worth acting on."
        ),
        caveats=(
            "Pairs below the small-sample floor are deliberately NOT counted. Counting "
            "them would turn a long tail of one-off coincidences into hundreds of "
            "apparent merchandising opportunities.",
            "NOT additive across periods: a pair that clears the floor over a month may "
            "clear it in none of that month's weeks. Recompute it for the window you "
            "are reporting.",
            "Grows roughly with the square of catalogue breadth, so it is not "
            "comparable between stores of different sizes.",
        ),
    ),
    # ======================================================================
    # Marketing efficiency from ENTERED spend (view 21)
    # ======================================================================
    # These two are deliberately separate from `marketing_spend`/`cac` above,
    # which define the AD-PLATFORM figures and stay INCOMPLETE until one is
    # connected. The ids below are computed from `analytics_marketing_spend`,
    # the internal ledger of spend a human typed from an invoice — a different
    # provenance under a different id, exactly as the five revenue figures are
    # kept apart.
    KpiDef(
        id="total_spend",
        label="Marketing Spend (entered)",
        description=(
            "Money recorded as spent on marketing in the window — typed into the "
            "admin from invoices, per channel, daily or as monthly totals."
        ),
        unit="money",
        formula=(
            "SUM(analytics_marketing_spend rows allocated to the window's days); "
            "MONTHLY rows are spread across their month to the paisa "
            "(allocate_row_to_days) and only their in-window days count"
        ),
        basis="event",
        currency_handling=_CCY_INR,
        source=DataSource.INTERNAL_DB,
        default_quality=MetricQuality.ESTIMATED,
        freshness=Freshness.DAILY,
        dimensions=("date", "channel", "campaign"),
        higher_is_better=None,
        tooltip=(
            "Marketing spend recorded for this period in the admin. A typed "
            "figure, graded by the entry's own quality — not a number read from "
            "an ad platform."
        ),
        caveats=(
            "Manually entered. The grade rides on each row's stated quality: "
            "reconciled invoices are ACTUAL, everything else a human asserted is "
            "ESTIMATED, and days spread out of a monthly lump are ALLOCATED.",
            "No rows in the window means NOT RECORDED, never zero — the view "
            "gates itself rather than reporting free marketing.",
            "Distinct from `marketing_spend`, which is defined as ad-platform "
            "spend over connected accounts and remains INCOMPLETE until an ads "
            "API is connected. The two must never be summed or swapped.",
            "A monthly row cut by the window edge contributes only its in-window "
            "daily shares, allocated to the paisa with no rounding drift.",
        ),
    ),
    KpiDef(
        id="blended_roas",
        label="Blended ROAS (MER)",
        description=(
            "Total store revenue per rupee of recorded marketing spend — the "
            "marketing efficiency ratio, blended across every channel."
        ),
        unit="ratio",
        formula=(
            "net_revenue / total_spend over the SAME window — sums first, one "
            "division; never an average of daily ratios"
        ),
        basis="order",
        numerator="net_revenue over the window (internal, AUTHORITATIVE)",
        denominator=(
            "SUM(analytics_marketing_spend allocated to the window's days)"
        ),
        included_statuses=_RECOGNISED_SALE_STATUSES,
        excluded_statuses=_UNRECOGNISED_STATUSES,
        refund_treatment="deducted",
        tax_treatment="inclusive",
        shipping_treatment="included",
        currency_handling=_CCY_RATIO,
        source=DataSource.INTERNAL_DB,
        default_quality=MetricQuality.ESTIMATED,
        freshness=Freshness.DAILY,
        dimensions=("date",),
        higher_is_better=True,
        tooltip=(
            "Net revenue divided by recorded marketing spend for the same "
            "period. Blended across all channels — this is MER, not a "
            "per-channel return."
        ),
        caveats=(
            "BLENDED, never per-channel. Revenue cannot be attributed to a "
            "channel without ad-platform click attribution (no session-to-order "
            "key exists), so `dimensions` deliberately excludes channel — a "
            "per-channel split of this ratio would be fabricated.",
            "The denominator is typed by a human, so the ratio is at best as "
            "good as the entered rows: it inherits their quality grade and "
            "never reports better than ESTIMATED while spend is manual.",
            "Undefined — None, not 0 and not infinity — for any span with no "
            "recorded spend, including a single bucket inside a window that has "
            "spend elsewhere.",
            "Recomputed from window sums on every re-bucketing. Averaging daily "
            "ROAS values weights a Rs.10 day equal to a Rs.10,000 day and is "
            "wrong silently.",
            "Uses accounting net_revenue (tax and shipping in, refunds "
            "deducted), so it is comparable with the revenue cards, not with "
            "an ad platform's own conversion-value ROAS.",
        ),
    ),
)


# --------------------------------------------------------------------------
# Lookup helpers
# --------------------------------------------------------------------------

KPIS_BY_ID: dict[str, KpiDef] = {k.id: k for k in KPIS}


def by_id(kpi_id: str) -> KpiDef | None:
    """Return the KPI with this id, or None. Non-raising by design: callers are
    usually validating a list of ids from a view definition and want to collect
    every unknown one, not blow up on the first."""
    return KPIS_BY_ID.get(kpi_id)


def all_kpi_ids() -> tuple[str, ...]:
    """Every KPI id in catalogue order."""
    return tuple(k.id for k in KPIS)


def kpi_to_dict(k: KpiDef) -> dict[str, Any]:
    """JSON-native form, using the same `_clean` as the view/module serialisers
    so the dumped contract is byte-stable across all three."""
    return _clean(asdict(k))


# --------------------------------------------------------------------------
# Import-time self-check
# --------------------------------------------------------------------------
# Cheap, runs once, and turns a typo into a boot failure instead of a broken
# breakdown in the admin UI. The registry and the dump script both rely on these
# invariants holding.

_UNITS = frozenset(f.value for f in FormatId)


def _validate() -> None:
    seen: set[str] = set()
    for k in KPIS:
        if k.id in seen:
            raise ValueError(f"duplicate KPI id: {k.id}")
        seen.add(k.id)
        if not k.id or k.id != k.id.lower() or " " in k.id or "-" in k.id:
            raise ValueError(f"KPI id must be lowercase snake_case: {k.id!r}")
        if k.unit not in _UNITS:
            raise ValueError(f"{k.id}: unit {k.unit!r} is not a FormatId")
        if k.basis not in BASES:
            raise ValueError(f"{k.id}: basis {k.basis!r} not in BASES")
        if k.refund_treatment not in REFUND_TREATMENTS:
            raise ValueError(f"{k.id}: bad refund_treatment {k.refund_treatment!r}")
        if k.tax_treatment not in TAX_TREATMENTS:
            raise ValueError(f"{k.id}: bad tax_treatment {k.tax_treatment!r}")
        if k.shipping_treatment not in SHIPPING_TREATMENTS:
            raise ValueError(f"{k.id}: bad shipping_treatment {k.shipping_treatment!r}")
        for dim in k.dimensions:
            if dim not in DIMENSIONS:
                raise ValueError(f"{k.id}: unknown dimension {dim!r}")
        if not k.tooltip:
            raise ValueError(f"{k.id}: tooltip is required — it is the UI contract")
        if k.version < 1:
            raise ValueError(f"{k.id}: version must start at 1")


_validate()
