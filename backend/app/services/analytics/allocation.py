"""Deterministic order -> line money allocation, exact to the last paisa.

`order_items` records only `quantity`, `unit_price` and `unit_cost`. There is no
per-line tax, discount, shipping or surcharge column anywhere in the schema, and
there never was — those amounts are recorded once, on the order. Every
per-product, per-category or per-SKU margin figure therefore has to push the
order-level totals back down onto the lines before it can say anything at all.

That push is where money quietly goes missing. Do it with floats, or with
`round(total * share, 2)` per line, and the line shares stop summing to the order
total: ₹0.01 here, ₹0.02 there. Nothing looks wrong on a single order. Across a
month of orders the per-product margin table and the order-level P&L disagree by
an amount nobody can explain, and the only honest answer is "the allocator
leaks". So this module does not round at all.

The algorithm — largest remainder, integer paise
------------------------------------------------
Given an order-level `total_minor` and a non-negative integer weight per line:

  1. **Zero total weight** falls back to an equal split. This is not a
     degenerate case to ignore: a 100%-discounted order has zero extended price
     on every line, yet its tax, shipping and COD surcharge are real money that
     still has to land somewhere. Proportional-to-nothing is undefined, so an
     equal split is the only neutral answer — and it is still exact.
  2. **Base share** per line is `total * weight_i // total_weight`, an integer
     floor. Flooring every line always under-distributes, never over.
  3. **The remainder** — `total - sum(base_shares)`, always strictly less than
     the number of lines — is handed out **one paisa at a time** to the lines
     with the largest fractional part. Ties break on **ascending
     tie_break_id** (the `order_items.id`), so the answer depends on the set of
     lines and not on the order they happen to arrive in. Two callers that load
     the same order with different `ORDER BY` clauses get identical shares.
  4. **Negative totals** (refund-style) are allocated on the magnitude and then
     negated, so `allocate(-x) == [-s for s in allocate(x)]` exactly. A refund
     is the mirror image of the charge it reverses, which is what makes a
     charge and its full refund cancel to zero per line rather than leaving
     ±1 paisa of residue.

The postcondition, unconditionally: **`sum(result) == total_minor`**. It is
asserted inside `allocate_amount` rather than left to the tests, because a
silent leak here is exactly the failure mode this module exists to prevent.

Quality
-------
Every `LineAllocation` this module produces is `MetricQuality.ALLOCATED` and can
never be anything better. `AUTHORITATIVE` means "straight from the transactional
record", and none of these per-line values were ever recorded per line — they
are the output of a rule, however exact that rule is. Labelling them
authoritative would be a lie of provenance even when the arithmetic is perfect.

Basis choices, and the one that is arguable
-------------------------------------------
Discount, payment discount, COD surcharge and the gateway fee all split on line
extended price; that is uncontroversial. Shipping splits on extended price by
default and on weight share when the caller asks and every line has a weight.

Tax is the interesting one. The *correct* basis is each line's own tax rate —
a 5% line and an 18% line in the same order do not owe tax in proportion to
their prices — so when every line resolves a rate through the `product_taxes`
many-to-many, the weight is `extended_price x rate` and the split is right.
When any line cannot resolve one, this falls back to extended price and says so
in `warnings`. It never silently picks one basis and presents it as the other.

Note the provenance caveat on that path: `product_taxes` is read *now*, but the
order's `tax_amount` was snapshotted at checkout. If an admin has since edited a
product's tax rows, the rate basis reconstructs the split using today's rates.
The total still reconciles exactly — only its distribution across lines shifts.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import inspect as sa_inspect, select
from sqlalchemy.orm import object_session
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.orm.util import identity_key

from app.services.analytics.contracts import (
    AllocationResult,
    LineAllocation,
    from_minor,
    to_minor,
)
from app.services.analytics.types import MetricQuality

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps runtime dependency-free
    from app.models.order import Order, OrderItem

__all__ = [
    "ORDER_AMOUNT_FIELDS",
    "SHIPPING_BASES",
    "SHIPPING_BASIS_EXTENDED",
    "SHIPPING_BASIS_WEIGHT",
    "allocate_amount",
    "allocate_order",
    "verify",
]

#: Product rows fetched per pre-load statement. The pre-load is bounded by the
#: catalogue rather than by one order, so a very wide catalogue would otherwise
#: build a single enormous `IN` list. See `_preload_products`.
_PRELOAD_CHUNK = 500

#: Tax rates are `Numeric(6, 3)` percentages (18.000 == 18%). Scaling by 1000
#: turns the rate into an exact integer, so the tax weight
#: `extended_minor * rate_milli` stays in integer arithmetic end to end. The
#: constant factor cancels out of the ratio, so its magnitude never matters.
_RATE_SCALE = 1000

SHIPPING_BASIS_EXTENDED = "extended_price"
SHIPPING_BASIS_WEIGHT = "weight"
SHIPPING_BASES: tuple[str, ...] = (SHIPPING_BASIS_EXTENDED, SHIPPING_BASIS_WEIGHT)

#: `LineAllocation` field -> the `orders` column whose total it splits. Ordered
#: as the allocation runs, so `verify()` reports failures in a stable order.
#: `gateway_fee` is deliberately absent: no order column carries it, it is
#: passed in by the caller, and `verify()` only checks it when told the value.
ORDER_AMOUNT_FIELDS: dict[str, str] = {
    "discount": "discount_amount",
    "payment_discount": "payment_discount_amount",
    "tax": "tax_amount",
    "shipping": "shipping_amount",
    "cod_surcharge": "cod_surcharge_amount",
}


# --------------------------------------------------------------------------
# The core: largest-remainder allocation over integers
# --------------------------------------------------------------------------


def allocate_amount(
    total_minor: int,
    weights: Sequence[int],
    tie_break_ids: Sequence[int],
) -> list[int]:
    """Split `total_minor` across lines in proportion to `weights`, exactly.

    Returns one integer share per weight. The shares always sum to
    `total_minor` — no tolerance, no drift, in either direction of sign.

    `tie_break_ids` must be parallel to `weights` and should be unique (the
    `order_items.id` values are). They are what makes the result independent of
    input ordering: when two lines have the same fractional part, the paisa goes
    to the smaller id, not to whichever row the database returned first.
    """
    n = len(weights)
    if len(tie_break_ids) != n:
        raise ValueError(
            f"weights ({n}) and tie_break_ids ({len(tie_break_ids)}) must be "
            "the same length"
        )
    if n == 0:
        if total_minor != 0:
            raise ValueError(
                f"cannot allocate {total_minor} minor units across zero lines"
            )
        return []
    if any(w < 0 for w in weights):
        raise ValueError(f"weights must be non-negative, got {list(weights)}")

    total_weight = sum(weights)
    if total_weight == 0:
        # A 100%-discounted order has zero extended price on every line, but its
        # tax/shipping/surcharge is still real money that must land somewhere.
        # Equal division is the only neutral answer, and the remainder loop
        # below keeps it exact just the same.
        effective: list[int] = [1] * n
        total_weight = n
    else:
        effective = list(weights)

    # Allocate on the magnitude and mirror the sign back on, so a refund is the
    # exact negation of the charge it reverses rather than a separately-rounded
    # figure that happens to be close.
    sign = -1 if total_minor < 0 else 1
    magnitude = abs(total_minor)

    shares = [magnitude * w // total_weight for w in effective]
    # magnitude*w / total_weight == shares[i] + (magnitude*w % total_weight)/total_weight,
    # so the modulus below IS the fractional part's numerator. Every line shares
    # the same denominator, so comparing numerators compares the fractions.
    fractions = [(magnitude * w) % total_weight for w in effective]

    remainder = magnitude - sum(shares)
    assert 0 <= remainder < n, (  # noqa: S101 - invariant of flooring n fractions
        f"remainder {remainder} outside [0, {n}) — flooring cannot lose a whole unit"
    )

    # Largest fraction first; ties to the smallest tie_break_id; position last so
    # duplicate ids still produce a total order rather than an unstable one.
    order = sorted(range(n), key=lambda i: (-fractions[i], tie_break_ids[i], i))
    for i in order[:remainder]:
        shares[i] += 1

    result = [sign * s for s in shares]
    assert sum(result) == sign * magnitude == total_minor, (  # noqa: S101
        f"allocation leaked: sum({result}) != {total_minor}"
    )
    return result


# --------------------------------------------------------------------------
# Basis resolution
# --------------------------------------------------------------------------


def _amount_minor(order: Any, attr: str) -> int:
    """Read one order-level money column as integer paise. Absent/NULL is 0."""
    return to_minor(getattr(order, attr, None))


def _extended_price_minor(item: Any) -> int:
    """`quantity * unit_price` in paise.

    Multiplying before converting rounds once instead of once per unit. A float
    `unit_price` reaches `to_minor` as a float and raises there, on purpose —
    money must not arrive here through binary floating point.
    """
    return to_minor(item.unit_price * int(item.quantity))


def _preload_products(items: Sequence[Any]) -> None:
    """Bring the lines' `product` rows (and their taxes) into the Session at once.

    `_tax_rate_milli` and `_line_weight_grams` both reach through
    `item.product`, which is `lazy="select"` on `OrderItem` (order.py:220). Left
    alone that is one `SELECT products` the first time each distinct product is
    touched, plus one more for `Product.taxes` (`lazy="selectin"`) — bounded by
    the catalogue rather than by the work, so it grows as the store adds SKUs
    and does not shrink on a quiet day. `docs/analytics/PERFORMANCE.md` §5.5
    measured **238 queries for one `product_daily` bucket** over a 113-product
    catalogue, and traced every one of them to the two `getattr` calls below.

    Why this looks past the order it was given
    ------------------------------------------
    `allocate_order` is called once per order, inside a loop over a whole
    bucket's orders. Pre-loading only *this* order's products would move the
    N+1 rather than remove it: the second order still introduces a product the
    first did not, and the count stays proportional to the catalogue.

    So when a product is missing, this widens the fetch to every order line the
    Session is **already holding** — which is exactly the set the caller loaded
    on purpose (`ProductDailyJob._sold` loads the bucket's orders with
    `selectinload(Order.items)` before the loop). One query then covers the
    whole bucket and every later call finds its products in the identity map
    and issues nothing. It can only ever load rows the caller's own working set
    refers to, and loading a row can only make a later read cheaper.

    The rows are then attached with `set_committed_value`, which is what makes
    the pre-load stick. Merely executing the query is not enough: the Session's
    identity map holds **weak** references, so products nobody points at are
    collected before the next line asks for them and every order re-fetches the
    same catalogue. Attaching them is also strictly cheaper than leaving the
    lazy loader to find them — it never runs at all.

    `set_committed_value` rather than `item.product = ...` because the
    relationship is `viewonly`: this records a value that was loaded, it does
    not assign one. Nothing is marked dirty and nothing is written back, which
    is the same guarantee `viewonly` itself makes.

    The result is a pre-load, not a second source of truth. The allocator still
    reads `item.product` and would still be correct, just slow again, if this
    function did nothing at all — which is exactly what it does for anything
    that is not a persistent ORM instance. This module is duck-typed on purpose
    — `allocate_order` is called in tests with plain objects carrying
    `quantity`/`unit_price`/`product` — so every step that cannot be satisfied
    returns rather than raising, and the caller falls back to per-item attribute
    access. The relationship's target class is read off the mapper for the same
    reason: allocation stays free of a runtime import of the model modules.
    """
    session = None
    item_mapper = None
    mapper = None
    target = None
    primary_key = None
    #: item -> the product id it is waiting for.
    pending: dict[Any, Any] = {}

    for item in items:
        state = sa_inspect(item, raiseerr=False)
        if state is None or not hasattr(state, "mapper"):
            return  # not an ORM object: nothing to pre-load
        if "product" not in state.unloaded:
            continue  # already loaded — by an earlier call, or upstream
        product_id = getattr(item, "product_id", None)
        if product_id is None:
            return
        if session is None:
            session = object_session(item)
            if session is None:
                return  # detached: a lazy load would raise, not query
            relationship = state.mapper.relationships.get("product")
            if relationship is None:
                return
            item_mapper = state.mapper
            mapper = relationship.mapper
            target = mapper.class_
            primary_key = mapper.primary_key[0]
        pending[item] = product_id

    if session is None:
        return  # every line already carried its product

    # The widening. `list()` because attaching below mutates the identity map.
    for state in list(session.identity_map.all_states()):
        if state.mapper is not item_mapper or "product" not in state.unloaded:
            continue
        # `state.dict`, not `getattr`: reading through the instance would
        # unexpire an expired row, which is a query — the thing being avoided.
        sibling_id = state.dict.get("product_id")
        if sibling_id is not None and state.obj() is not None:
            pending.setdefault(state.obj(), sibling_id)

    by_id: dict[Any, Any] = {}
    missing: list[Any] = []
    for product_id in sorted(set(pending.values())):
        known = session.identity_map.get(identity_key(target, (product_id,)))
        if known is None:
            missing.append(product_id)
        else:
            by_id[product_id] = known

    # Chunked so a very wide catalogue produces bounded statements: a query per
    # `_PRELOAD_CHUNK` products per Session, against two per product per Session.
    for start in range(0, len(missing), _PRELOAD_CHUNK):
        rows = session.execute(
            select(target).where(
                primary_key.in_(missing[start : start + _PRELOAD_CHUNK])
            )
        ).scalars()
        for product in rows:
            by_id[mapper.primary_key_from_instance(product)[0]] = product

    for item, product_id in pending.items():
        product = by_id.get(product_id)
        if product is not None:
            set_committed_value(item, "product", product)


def _tax_rate_milli(item: Any) -> int | None:
    """This line's own combined tax rate in thousandths of a percent.

    `None` means unresolvable — the product could not be reached at all. A
    product with no tax rows resolves to 0, which is a real rate (an exempt SKU)
    and materially different from "we don't know".
    """
    product = getattr(item, "product", None)
    if product is None:
        return None
    taxes = getattr(product, "taxes", None)
    if taxes is None:
        return None
    # Inactive taxes are excluded: the same rule the checkout applies. Note this
    # reads today's configuration, not the order's snapshot — see module docstring.
    total = Decimal(0)
    for tax in taxes:
        if getattr(tax, "is_active", True):
            total += Decimal(tax.rate)
    return int((total * _RATE_SCALE).to_integral_value())


def _line_weight_grams(item: Any) -> int | None:
    """Total shipped grams for this line, or `None` if the SKU has no weight."""
    product = getattr(item, "product", None)
    if product is None:
        return None
    grams = getattr(product, "weight_grams", None)
    if grams is None:
        return None
    return int(grams) * int(item.quantity)


def _tax_weights(
    items: Sequence[Any],
    extended: Sequence[int],
    tax_minor: int,
    warnings: list[str],
) -> tuple[list[int], str]:
    """Per-line tax basis: each line's own rate when all resolve, else price."""
    rates = [_tax_rate_milli(item) for item in items]
    unresolved = sum(1 for r in rates if r is None)
    if unresolved:
        warnings.append(
            f"tax_basis_fallback: {unresolved} of {len(items)} line(s) could not "
            "resolve a tax rate from product_taxes, so tax was allocated by "
            "extended price rather than by each line's own rate"
        )
        return list(extended), "extended price"

    weights = [e * r for e, r in zip(extended, rates)]
    if tax_minor != 0 and sum(weights) == 0:
        warnings.append(
            "tax_basis_fallback: every line resolved a 0% tax rate but the order "
            f"carries {from_minor(tax_minor)} of tax, so tax was allocated by "
            "extended price rather than by each line's own rate"
        )
        return list(extended), "extended price"
    return weights, "tax base (extended price x rate)"


def _shipping_weights(
    items: Sequence[Any],
    extended: Sequence[int],
    shipping_basis: str,
    warnings: list[str],
) -> tuple[list[int], str]:
    """Per-line shipping basis: weight share only when every line has a weight."""
    if shipping_basis == SHIPPING_BASIS_EXTENDED:
        return list(extended), "extended price"

    grams = [_line_weight_grams(item) for item in items]
    missing = sum(1 for g in grams if g is None)
    if missing:
        warnings.append(
            f"shipping_basis_fallback: {missing} of {len(items)} line(s) have no "
            "product weight_grams, so shipping was allocated by extended price "
            "rather than by weight share"
        )
        return list(extended), "extended price"
    if sum(grams) == 0:  # type: ignore[arg-type]
        warnings.append(
            "shipping_basis_fallback: the order's total shipped weight is zero "
            "grams, so shipping was allocated by extended price rather than by "
            "weight share"
        )
        return list(extended), "extended price"
    return [g for g in grams if g is not None], "weight share"


def _allocate_field(
    field_name: str,
    amount_minor: int,
    weights: Sequence[int],
    tie_break_ids: Sequence[int],
    warnings: list[str],
    *,
    basis: str,
) -> list[int]:
    """`allocate_amount` plus the one warning the caller genuinely needs."""
    if amount_minor != 0 and sum(weights) == 0:
        warnings.append(
            f"{field_name}_split_equally: {basis} is zero on every line, so "
            f"{from_minor(amount_minor)} of {field_name} was divided equally — "
            "the split is exact but not proportional to anything"
        )
    return allocate_amount(amount_minor, weights, tie_break_ids)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def allocate_order(
    order: "Order",
    items: "Iterable[OrderItem]",
    *,
    gateway_fee_minor: int | None = None,
    shipping_basis: str = SHIPPING_BASIS_EXTENDED,
) -> AllocationResult:
    """Push every order-level money amount down onto the order's lines.

    Lines come back sorted by `order_item_id`, so the result is byte-identical
    however the caller ordered `items`. Every share is `MetricQuality.ALLOCATED`
    — see the module docstring for why it can never be better than that.

    `gateway_fee_minor` is supplied by the caller (the cost-rule engine owns
    fee resolution); `None` means "no fee known", which allocates zero and is
    reported as zero rather than guessed at.
    """
    if shipping_basis not in SHIPPING_BASES:
        raise ValueError(
            f"shipping_basis must be one of {SHIPPING_BASES}, got {shipping_basis!r}"
        )

    warnings: list[str] = []
    fee_minor = int(gateway_fee_minor) if gateway_fee_minor is not None else 0

    lines: list[Any] = list(items)
    ids: list[int] = []
    for item in lines:
        item_id = getattr(item, "id", None)
        if item_id is None:
            raise ValueError(
                "every order item needs a persisted id to allocate against — "
                "flush the session before calling allocate_order"
            )
        ids.append(int(item_id))
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate order_item ids would break determinism: {ids}")

    # Sort by id so the output depends on the set of lines, never their arrival
    # order. `zip(*sorted(...))` keeps ids and items in lockstep.
    if lines:
        ids, lines = (list(t) for t in zip(*sorted(zip(ids, lines), key=lambda p: p[0])))

    order_id = int(getattr(order, "id", 0) or 0)

    if not lines:
        pending = [f for f, attr in ORDER_AMOUNT_FIELDS.items() if _amount_minor(order, attr)]
        if fee_minor:
            pending.append("gateway_fee")
        if pending:
            warnings.append(
                "no_lines: the order has no line items, so "
                + ", ".join(pending)
                + " could not be allocated at all"
            )
        return AllocationResult(order_id=order_id, lines=(), warnings=tuple(warnings))

    # Before any basis is computed: both the tax and the shipping basis reach
    # through `item.product`, and doing that one line at a time is the N+1 in
    # PERFORMANCE.md §5.5. Purely an access-pattern change — see the docstring.
    _preload_products(lines)

    extended = [_extended_price_minor(item) for item in lines]

    subtotal_minor = _amount_minor(order, "subtotal")
    if subtotal_minor != sum(extended):
        warnings.append(
            f"subtotal_mismatch: order.subtotal is {from_minor(subtotal_minor)} but "
            f"the lines extend to {from_minor(sum(extended))} — every basis below "
            "is derived from the lines, so the shares are exact but the order the "
            "accountant sees is not the order being split"
        )

    shares: dict[str, list[int]] = {}
    for field_name in ("discount", "payment_discount", "cod_surcharge"):
        shares[field_name] = _allocate_field(
            field_name,
            _amount_minor(order, ORDER_AMOUNT_FIELDS[field_name]),
            extended,
            ids,
            warnings,
            basis="extended price",
        )

    tax_minor = _amount_minor(order, "tax_amount")
    tax_weights, tax_basis = _tax_weights(lines, extended, tax_minor, warnings)
    shares["tax"] = _allocate_field(
        "tax", tax_minor, tax_weights, ids, warnings, basis=tax_basis
    )

    ship_weights, ship_basis = _shipping_weights(lines, extended, shipping_basis, warnings)
    shares["shipping"] = _allocate_field(
        "shipping",
        _amount_minor(order, "shipping_amount"),
        ship_weights,
        ids,
        warnings,
        basis=ship_basis,
    )

    shares["gateway_fee"] = _allocate_field(
        "gateway_fee", fee_minor, extended, ids, warnings, basis="extended price"
    )

    allocations = tuple(
        LineAllocation(
            order_item_id=ids[i],
            extended_price=extended[i],
            discount=shares["discount"][i],
            tax=shares["tax"][i],
            shipping=shares["shipping"][i],
            cod_surcharge=shares["cod_surcharge"][i],
            payment_discount=shares["payment_discount"][i],
            gateway_fee=shares["gateway_fee"][i],
            # Never AUTHORITATIVE: no order line ever recorded these values.
            quality=MetricQuality.ALLOCATED,
        )
        for i in range(len(lines))
    )
    return AllocationResult(
        order_id=order_id, lines=allocations, warnings=tuple(warnings)
    )


def verify(
    result: AllocationResult,
    order: "Order",
    *,
    gateway_fee_minor: int | None = None,
) -> list[str]:
    """Names of any field whose allocated shares do not sum to the order amount.

    Should always be empty — `allocate_amount` asserts the same invariant
    internally. It exists so the reconciliation test proves the property against
    real rows rather than trusting the assertion, and so a future caller that
    hand-builds an `AllocationResult` cannot skip the check.

    `gateway_fee` is only checked when `gateway_fee_minor` is supplied, since no
    column on `orders` records it.
    """
    failed: list[str] = []
    for field_name, attr in ORDER_AMOUNT_FIELDS.items():
        if result.total(field_name) != _amount_minor(order, attr):
            failed.append(field_name)
    if gateway_fee_minor is not None and result.total("gateway_fee") != int(
        gateway_fee_minor
    ):
        failed.append("gateway_fee")
    return failed
