"""Exactness gate for the order -> line money allocator.

Every assertion here is integer equality with no tolerance, on purpose. The
whole point of `allocation.py` is that the line shares sum to the order total
*exactly*; a test written with `abs(a - b) < 0.01` would pass against precisely
the rounding leak the module exists to prevent, and the leak only becomes
visible after it has compounded across a reporting period.

Most cases are pure logic and need no database — they drive `allocate_amount`
directly, or `allocate_order` through small stub objects (the allocator is
duck-typed for exactly this reason). The two cases that must prove the
invariant against real rows build a real order and are marked with a `_db_`
prefix; they follow the house pattern from `test_sales_analytics_service.py`:
module-local `_create_*` helpers, `uuid4().hex[:8]` for uniqueness, their own
`SessionLocal()`, and explicit teardown in a `finally`.
"""
from __future__ import annotations

import random
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Category, Product
from app.models.tax import Tax
from app.models.user import User
from app.services.analytics.allocation import (
    SHIPPING_BASIS_WEIGHT,
    allocate_amount,
    allocate_order,
    verify,
)
from app.services.analytics.contracts import MINOR_UNITS_PER_MAJOR, to_minor
from app.services.analytics.types import MetricQuality


def _rupees(major: str | int) -> int:
    """Rupees as an integer paise literal, so the tests read in money."""
    return int(Decimal(str(major)) * MINOR_UNITS_PER_MAJOR)


# ---------------------------------------------------------------------------
# Stubs for the pure-logic path.  `allocate_order` only ever reads attributes,
# never SQLAlchemy internals, so these stand in for real rows exactly.
# ---------------------------------------------------------------------------


class _StubTax:
    def __init__(self, rate: str, *, is_active: bool = True) -> None:
        self.rate = Decimal(rate)
        self.is_active = is_active


class _StubProduct:
    def __init__(self, *, weight_grams: int | None = None, taxes: list | None = None) -> None:
        self.weight_grams = weight_grams
        self.taxes = [] if taxes is None else taxes


class _StubItem:
    def __init__(
        self,
        item_id: int,
        quantity: int,
        unit_price: str,
        *,
        product: _StubProduct | None = None,
    ) -> None:
        self.id = item_id
        self.quantity = quantity
        self.unit_price = Decimal(unit_price)
        self.product = _StubProduct() if product is None else product


class _StubOrder:
    def __init__(self, order_id: int, items: list[_StubItem], **amounts: str) -> None:
        self.id = order_id
        self.subtotal = sum(
            (it.unit_price * it.quantity for it in items), Decimal("0")
        )
        self.tax_amount = Decimal(amounts.get("tax_amount", "0"))
        self.discount_amount = Decimal(amounts.get("discount_amount", "0"))
        self.shipping_amount = Decimal(amounts.get("shipping_amount", "0"))
        self.cod_surcharge_amount = Decimal(amounts.get("cod_surcharge_amount", "0"))
        self.payment_discount_amount = Decimal(
            amounts.get("payment_discount_amount", "0")
        )


# ---------------------------------------------------------------------------
# DB helpers (local, not shared with other test modules)
# ---------------------------------------------------------------------------


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _create_user(db: Session) -> User:
    u = User(
        email=f"alloctest-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(u)
    db.flush()
    return u


def _create_category(db: Session) -> Category:
    uid = _uid()
    c = Category(name=f"AllocTestCat-{uid}", slug=f"alloctest-{uid}")
    db.add(c)
    db.flush()
    return c


def _create_tax(db: Session, rate: str) -> Tax:
    t = Tax(name=f"AllocTestTax-{_uid()}", rate=Decimal(rate), is_active=True)
    db.add(t)
    db.flush()
    return t


def _create_product(
    db: Session,
    *,
    price: Decimal,
    weight_grams: int | None = None,
    category: Category | None = None,
    taxes: list[Tax] | None = None,
) -> Product:
    uid = _uid()
    p = Product(
        sku=f"SKU-ALLOC-{uid}",
        name=f"AllocTestProd {uid}",
        price=price,
        stock=100,
        weight_grams=weight_grams,
        category_id=category.id if category else None,
    )
    if taxes:
        p.taxes = list(taxes)
    db.add(p)
    db.flush()
    return p


def _create_order(
    db: Session,
    user: User,
    lines: list[tuple[Product, int]],
    *,
    tax_amount: Decimal = Decimal("0"),
    discount_amount: Decimal = Decimal("0"),
    shipping_amount: Decimal = Decimal("0"),
    cod_surcharge_amount: Decimal = Decimal("0"),
    payment_discount_amount: Decimal = Decimal("0"),
) -> Order:
    items = [
        OrderItem(product_id=p.id, quantity=q, unit_price=p.price) for p, q in lines
    ]
    subtotal = sum((p.price * q for p, q in lines), Decimal("0"))
    order = Order(
        user_id=user.id,
        status=OrderStatus.PAID,
        subtotal=subtotal,
        tax_amount=tax_amount,
        discount_amount=discount_amount,
        shipping_amount=shipping_amount,
        cod_surcharge_amount=cod_surcharge_amount,
        payment_discount_amount=payment_discount_amount,
        total_amount=(
            subtotal
            + tax_amount
            + shipping_amount
            + cod_surcharge_amount
            - discount_amount
            - payment_discount_amount
        ),
        currency="INR",
        payment_method="prepaid",
    )
    order.items = items
    db.add(order)
    db.flush()
    return order


def _cleanup(
    order_ids: list[int],
    product_ids: list[int],
    user_ids: list[int],
    category_ids: list[int],
    tax_ids: list[int],
) -> None:
    """Delete test-owned rows through a fresh session, so teardown never fails
    because the test session is sitting on a half-rolled-back transaction."""
    if not any([order_ids, product_ids, user_ids, category_ids, tax_ids]):
        return
    with SessionLocal() as s:
        if order_ids:
            s.execute(
                text("DELETE FROM order_items WHERE order_id IN :ids"),
                {"ids": tuple(order_ids)},
            )
            s.execute(text("DELETE FROM orders WHERE id IN :ids"), {"ids": tuple(order_ids)})
        if product_ids:
            s.execute(
                text("DELETE FROM product_taxes WHERE product_id IN :ids"),
                {"ids": tuple(product_ids)},
            )
            s.execute(
                text("DELETE FROM products WHERE id IN :ids"), {"ids": tuple(product_ids)}
            )
        if tax_ids:
            s.execute(text("DELETE FROM taxes WHERE id IN :ids"), {"ids": tuple(tax_ids)})
        if user_ids:
            s.execute(
                text("DELETE FROM points_transactions WHERE user_id IN :ids"),
                {"ids": tuple(user_ids)},
            )
            s.execute(text("DELETE FROM users WHERE id IN :ids"), {"ids": tuple(user_ids)})
        if category_ids:
            s.execute(
                text("DELETE FROM categories WHERE id IN :ids"),
                {"ids": tuple(category_ids)},
            )
        s.commit()


# ===========================================================================
# 1-7, 10: allocate_amount, the core largest-remainder loop
# ===========================================================================


class TestAllocateAmount:

    def test_single_line_takes_everything(self) -> None:
        """One line has nowhere else for the money to go."""
        assert allocate_amount(_rupees("123.45"), [7000], [42]) == [12345]

    def test_single_line_with_zero_weight_still_takes_everything(self) -> None:
        """The zero-weight fallback must not lose the amount on a one-line order."""
        assert allocate_amount(_rupees("99.99"), [0], [1]) == [9999]

    def test_even_split_across_many_lines(self) -> None:
        """Four equal lines, a total that divides: no remainder anywhere."""
        result = allocate_amount(_rupees("400.00"), [100, 100, 100, 100], [1, 2, 3, 4])
        assert result == [10000, 10000, 10000, 10000]
        assert sum(result) == _rupees("400.00")

    def test_one_paisa_across_three_lines(self) -> None:
        """The smallest possible amount still lands, exactly once, on one line."""
        result = allocate_amount(1, [100, 100, 100], [1, 2, 3])
        assert sorted(result) == [0, 0, 1]
        assert sum(result) == 1
        # Tie on fractional part -> smallest id wins.
        assert result == [1, 0, 0]

    def test_one_paisa_winner_is_stable_under_reordering(self) -> None:
        """Same three lines, three arrival orders, same winner every time."""
        ids = [7, 3, 11]
        weights = [100, 100, 100]
        by_id = {}
        for perm in ([0, 1, 2], [2, 0, 1], [1, 2, 0]):
            shares = allocate_amount(
                1, [weights[i] for i in perm], [ids[i] for i in perm]
            )
            mapping = {ids[perm[k]]: shares[k] for k in range(3)}
            if by_id:
                assert mapping == by_id, "winner changed when the input was reordered"
            by_id = mapping
        # id 3 is the smallest, so it takes the paisa.
        assert by_id == {3: 1, 7: 0, 11: 0}

    def test_indivisible_remainder_hundred_rupees_over_three_lines(self) -> None:
        """10000 / 3 floors to 3333 each; the stray paisa goes to the first id."""
        result = allocate_amount(_rupees("100.00"), [1, 1, 1], [1, 2, 3])
        assert result == [3334, 3333, 3333]
        assert sum(result) == 10000

    def test_two_paise_remainder_goes_to_the_two_largest_fractions(self) -> None:
        """Remainder is handed out one paisa at a time, largest fraction first."""
        # 100 paise over weights 1:1:1:1:1:1:1 -> 14 each (98), 2 left over.
        result = allocate_amount(100, [1] * 7, [10, 20, 30, 40, 50, 60, 70])
        assert result == [15, 15, 14, 14, 14, 14, 14]
        assert sum(result) == 100

    def test_largest_fraction_beats_smaller_id(self) -> None:
        """Id order is only a tiebreaker — it must never outrank the fraction."""
        # total 10, weights 1:2 -> bases 3,6 (fractions 1/3, 2/3), remainder 1.
        # Line 2 has the larger fraction and wins despite the larger id.
        assert allocate_amount(10, [1, 2], [1, 2]) == [3, 7]

    def test_proportional_shares_when_weights_differ(self) -> None:
        """Weight 3:1 on ₹100.00 splits 75/25, exactly."""
        assert allocate_amount(_rupees("100.00"), [300, 100], [1, 2]) == [7500, 2500]

    def test_zero_total_gives_all_zeros(self) -> None:
        result = allocate_amount(0, [500, 250, 125], [1, 2, 3])
        assert result == [0, 0, 0]
        assert sum(result) == 0

    def test_zero_weights_divide_equally(self) -> None:
        """A 100%-discounted order: no extended price anywhere, tax still real."""
        result = allocate_amount(_rupees("90.00"), [0, 0, 0], [1, 2, 3])
        assert result == [3000, 3000, 3000]
        assert sum(result) == _rupees("90.00")

    def test_zero_weights_divide_equally_with_a_remainder(self) -> None:
        result = allocate_amount(10, [0, 0, 0, 0], [4, 3, 2, 1])
        assert sum(result) == 10
        # 2 each, remainder 2 to the two smallest ids (1 and 2 -> positions 3, 2).
        assert result == [2, 2, 3, 3]

    def test_negative_total_is_the_exact_mirror_of_the_positive(self) -> None:
        """A refund must cancel the charge it reverses, line by line."""
        weights = [700, 250, 50]
        ids = [11, 12, 13]
        charge = allocate_amount(_rupees("100.00"), weights, ids)
        refund = allocate_amount(-_rupees("100.00"), weights, ids)
        assert refund == [-s for s in charge]
        assert sum(refund) == -_rupees("100.00")
        assert all(s <= 0 for s in refund)

    def test_negative_total_with_indivisible_remainder(self) -> None:
        result = allocate_amount(-10000, [1, 1, 1], [1, 2, 3])
        assert result == [-3334, -3333, -3333]
        assert sum(result) == -10000

    def test_negative_total_with_zero_weights(self) -> None:
        result = allocate_amount(-1, [0, 0], [9, 4])
        assert sum(result) == -1
        assert result == [0, -1]  # id 4 is smaller, and it sits second

    def test_empty_line_list(self) -> None:
        assert allocate_amount(0, [], []) == []
        with pytest.raises(ValueError, match="zero lines"):
            allocate_amount(1, [], [])

    def test_mismatched_lengths_raise(self) -> None:
        with pytest.raises(ValueError, match="same length"):
            allocate_amount(100, [1, 2, 3], [1, 2])

    def test_negative_weight_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            allocate_amount(100, [5, -1], [1, 2])

    @pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6, 7])
    def test_property_sweep_always_sums_exactly(self, n: int) -> None:
        """Totals 0..500 paise x 1..7 lines x four weight shapes, both signs.

        This is the invariant stated as a property rather than as examples: no
        combination of total and weights may ever leak or invent a paisa.
        """
        ids = list(range(1, n + 1))
        shapes = [
            [0] * n,                                  # 100%-discounted order
            [1] * n,                                  # even
            [i + 1 for i in range(n)],                # ascending
            [(i * 7 + 3) % 13 for i in range(n)],     # lumpy, some zeros
        ]
        for total in range(0, 501):
            for weights in shapes:
                for signed in (total, -total):
                    result = allocate_amount(signed, weights, ids)
                    assert len(result) == n
                    assert sum(result) == signed, (
                        f"leak: total={signed} weights={weights} -> {result}"
                    )

    def test_property_sweep_is_order_independent(self) -> None:
        """Shuffling the parallel inputs never changes any line's share."""
        rng = random.Random(20260728)
        ids = list(range(101, 113))
        weights = [rng.randrange(0, 5000) for _ in ids]
        expected = dict(zip(ids, allocate_amount(_rupees("777.77"), weights, ids)))
        for _ in range(25):
            perm = list(range(len(ids)))
            rng.shuffle(perm)
            shares = allocate_amount(
                _rupees("777.77"), [weights[i] for i in perm], [ids[i] for i in perm]
            )
            assert {ids[perm[k]]: shares[k] for k in range(len(perm))} == expected


# ===========================================================================
# 8, 9, 12: allocate_order over stub rows
# ===========================================================================


class TestAllocateOrder:

    def test_every_field_reconciles_on_a_simple_order(self) -> None:
        items = [
            _StubItem(1, 2, "249.50"),
            _StubItem(2, 1, "100.00"),
            _StubItem(3, 3, "33.33"),
        ]
        order = _StubOrder(
            900,
            items,
            tax_amount="63.44",
            discount_amount="50.00",
            shipping_amount="79.00",
            cod_surcharge_amount="49.00",
            payment_discount_amount="17.77",
        )
        result = allocate_order(order, items, gateway_fee_minor=_rupees("12.34"))

        assert result.order_id == 900
        assert len(result.lines) == 3
        assert verify(result, order, gateway_fee_minor=_rupees("12.34")) == []
        assert result.total("extended_price") == to_minor(order.subtotal)

    def test_quality_is_always_allocated(self) -> None:
        """None of these values was ever recorded per line, so none is
        authoritative however exact the arithmetic is."""
        items = [_StubItem(1, 1, "10.00"), _StubItem(2, 1, "20.00")]
        order = _StubOrder(1, items, tax_amount="5.40")
        result = allocate_order(order, items)
        assert [ln.quality for ln in result.lines] == [MetricQuality.ALLOCATED] * 2
        assert all(ln.quality is not MetricQuality.AUTHORITATIVE for ln in result.lines)

    def test_large_fifty_line_order_reconciles_on_every_field(self) -> None:
        """Fifty lumpy lines, awkward totals — nothing may leak."""
        rng = random.Random(4242)
        items = [
            _StubItem(
                1000 + i,
                rng.randrange(1, 9),
                f"{rng.randrange(1999, 499999) / 100:.2f}",
                product=_StubProduct(weight_grams=rng.randrange(10, 3000)),
            )
            for i in range(50)
        ]
        order = _StubOrder(
            7777,
            items,
            tax_amount="4831.77",
            discount_amount="1234.56",
            shipping_amount="399.01",
            cod_surcharge_amount="49.00",
            payment_discount_amount="87.13",
        )
        fee = _rupees("531.29")
        result = allocate_order(order, items, gateway_fee_minor=fee)

        assert len(result.lines) == 50
        assert verify(result, order, gateway_fee_minor=fee) == []
        assert result.total("tax") == _rupees("4831.77")
        assert result.total("discount") == _rupees("1234.56")
        assert result.total("shipping") == _rupees("399.01")
        assert result.total("cod_surcharge") == _rupees("49.00")
        assert result.total("payment_discount") == _rupees("87.13")
        assert result.total("gateway_fee") == fee

    def test_shuffling_the_input_lines_changes_nothing(self) -> None:
        """The whole result tuple, not just the per-line sums, must be identical."""
        rng = random.Random(99)
        items = [
            _StubItem(500 - i * 7, rng.randrange(1, 5), f"{rng.randrange(100, 90000) / 100:.2f}")
            for i in range(12)
        ]
        order = _StubOrder(
            55,
            items,
            tax_amount="611.11",
            discount_amount="99.99",
            shipping_amount="79.00",
            cod_surcharge_amount="49.00",
            payment_discount_amount="7.07",
        )
        baseline = allocate_order(order, items, gateway_fee_minor=1234)
        for _ in range(10):
            shuffled = list(items)
            rng.shuffle(shuffled)
            assert allocate_order(order, shuffled, gateway_fee_minor=1234).lines == (
                baseline.lines
            )

    def test_fully_discounted_order_still_places_tax_and_shipping(self) -> None:
        """Zero-priced lines: the split is equal, exact, and says so."""
        items = [_StubItem(1, 1, "0.00"), _StubItem(2, 4, "0.00")]
        order = _StubOrder(3, items, tax_amount="9.00", shipping_amount="79.01")
        result = allocate_order(order, items)

        assert verify(result, order) == []
        assert [ln.tax for ln in result.lines] == [450, 450]
        assert sum(ln.shipping for ln in result.lines) == 7901
        assert any(w.startswith("tax_split_equally") for w in result.warnings)
        assert any(w.startswith("shipping_split_equally") for w in result.warnings)

    def test_no_gateway_fee_allocates_zero_rather_than_guessing(self) -> None:
        items = [_StubItem(1, 1, "10.00"), _StubItem(2, 1, "10.00")]
        order = _StubOrder(4, items)
        result = allocate_order(order, items)
        assert [ln.gateway_fee for ln in result.lines] == [0, 0]

    def test_refund_shaped_negative_amounts_reconcile(self) -> None:
        """Negative order-level amounts allocate with the right signs."""
        items = [_StubItem(1, 1, "300.00"), _StubItem(2, 1, "100.00")]
        order = _StubOrder(5, items, tax_amount="-72.01", shipping_amount="-79.00")
        result = allocate_order(order, items)
        assert verify(result, order) == []
        assert all(ln.tax <= 0 for ln in result.lines)
        assert result.total("tax") == -7201

    def test_order_with_no_items_reports_what_it_could_not_place(self) -> None:
        order = _StubOrder(6, [], tax_amount="18.00", shipping_amount="79.00")
        result = allocate_order(order, [], gateway_fee_minor=500)
        assert result.lines == ()
        assert len(result.warnings) == 1
        warning = result.warnings[0]
        assert warning.startswith("no_lines")
        assert "tax" in warning and "shipping" in warning and "gateway_fee" in warning
        # It genuinely did not reconcile, and verify() must say so.
        assert verify(result, order, gateway_fee_minor=500) == ["tax", "shipping", "gateway_fee"]

    def test_unflushed_line_is_rejected(self) -> None:
        items = [_StubItem(1, 1, "10.00")]
        items[0].id = None  # type: ignore[assignment]
        with pytest.raises(ValueError, match="persisted id"):
            allocate_order(_StubOrder(7, []), items)

    def test_duplicate_line_ids_are_rejected(self) -> None:
        items = [_StubItem(1, 1, "10.00"), _StubItem(1, 1, "20.00")]
        with pytest.raises(ValueError, match="duplicate order_item ids"):
            allocate_order(_StubOrder(8, items), items)

    def test_unknown_shipping_basis_is_rejected(self) -> None:
        items = [_StubItem(1, 1, "10.00")]
        with pytest.raises(ValueError, match="shipping_basis"):
            allocate_order(_StubOrder(9, items), items, shipping_basis="volume")

    def test_subtotal_mismatch_is_reported_not_hidden(self) -> None:
        items = [_StubItem(1, 1, "10.00")]
        order = _StubOrder(10, items)
        order.subtotal = Decimal("99.00")  # lines extend to 10.00
        result = allocate_order(order, items)
        assert any(w.startswith("subtotal_mismatch") for w in result.warnings)


# ===========================================================================
# 12: shipping basis
# ===========================================================================


class TestShippingBasis:

    def test_weight_basis_splits_by_grams_not_price(self) -> None:
        """A heavy cheap line must carry more shipping than a light dear one."""
        items = [
            _StubItem(1, 1, "1000.00", product=_StubProduct(weight_grams=100)),
            _StubItem(2, 1, "100.00", product=_StubProduct(weight_grams=900)),
        ]
        order = _StubOrder(11, items, shipping_amount="100.00")
        by_weight = allocate_order(order, items, shipping_basis=SHIPPING_BASIS_WEIGHT)

        assert [ln.shipping for ln in by_weight.lines] == [1000, 9000]
        assert verify(by_weight, order) == []
        assert not any(w.startswith("shipping_basis_fallback") for w in by_weight.warnings)

        by_price = allocate_order(order, items)
        assert [ln.shipping for ln in by_price.lines] == [9091, 909]
        assert verify(by_price, order) == []

    def test_weight_basis_multiplies_by_quantity(self) -> None:
        items = [
            _StubItem(1, 3, "50.00", product=_StubProduct(weight_grams=100)),  # 300g
            _StubItem(2, 1, "50.00", product=_StubProduct(weight_grams=100)),  # 100g
        ]
        order = _StubOrder(12, items, shipping_amount="80.00")
        result = allocate_order(order, items, shipping_basis=SHIPPING_BASIS_WEIGHT)
        assert [ln.shipping for ln in result.lines] == [6000, 2000]

    def test_missing_weight_falls_back_to_price_and_warns(self) -> None:
        items = [
            _StubItem(1, 1, "300.00", product=_StubProduct(weight_grams=100)),
            _StubItem(2, 1, "100.00", product=_StubProduct(weight_grams=None)),
        ]
        order = _StubOrder(13, items, shipping_amount="100.00")
        result = allocate_order(order, items, shipping_basis=SHIPPING_BASIS_WEIGHT)

        assert [ln.shipping for ln in result.lines] == [7500, 2500]  # 3:1 on price
        assert verify(result, order) == []
        fallback = [w for w in result.warnings if w.startswith("shipping_basis_fallback")]
        assert len(fallback) == 1
        assert "1 of 2" in fallback[0] and "weight_grams" in fallback[0]

    def test_zero_total_weight_falls_back_to_price_and_warns(self) -> None:
        items = [
            _StubItem(1, 1, "300.00", product=_StubProduct(weight_grams=0)),
            _StubItem(2, 1, "100.00", product=_StubProduct(weight_grams=0)),
        ]
        order = _StubOrder(14, items, shipping_amount="100.00")
        result = allocate_order(order, items, shipping_basis=SHIPPING_BASIS_WEIGHT)
        assert [ln.shipping for ln in result.lines] == [7500, 2500]
        assert any("zero grams" in w for w in result.warnings)


# ===========================================================================
# Tax basis
# ===========================================================================


class TestTaxBasis:

    def test_line_rates_are_used_when_every_line_resolves_one(self) -> None:
        """18% and 5% lines at the same price owe tax 18:5, not 1:1."""
        items = [
            _StubItem(1, 1, "100.00", product=_StubProduct(taxes=[_StubTax("18.000")])),
            _StubItem(2, 1, "100.00", product=_StubProduct(taxes=[_StubTax("5.000")])),
        ]
        order = _StubOrder(20, items, tax_amount="23.00")
        result = allocate_order(order, items)

        assert [ln.tax for ln in result.lines] == [1800, 500]
        assert verify(result, order) == []
        assert not any(w.startswith("tax_basis_fallback") for w in result.warnings)

    def test_multiple_taxes_on_one_product_are_summed(self) -> None:
        items = [
            _StubItem(
                1, 1, "100.00",
                product=_StubProduct(taxes=[_StubTax("9.000"), _StubTax("9.000")]),
            ),
            _StubItem(2, 1, "100.00", product=_StubProduct(taxes=[_StubTax("6.000")])),
        ]
        order = _StubOrder(21, items, tax_amount="24.00")
        result = allocate_order(order, items)
        assert [ln.tax for ln in result.lines] == [1800, 600]

    def test_inactive_taxes_are_excluded(self) -> None:
        items = [
            _StubItem(
                1, 1, "100.00",
                product=_StubProduct(
                    taxes=[_StubTax("18.000"), _StubTax("100.000", is_active=False)]
                ),
            ),
            _StubItem(2, 1, "100.00", product=_StubProduct(taxes=[_StubTax("6.000")])),
        ]
        order = _StubOrder(22, items, tax_amount="24.00")
        result = allocate_order(order, items)
        assert [ln.tax for ln in result.lines] == [1800, 600]

    def test_unresolvable_rate_falls_back_to_price_and_names_the_fallback(self) -> None:
        items = [
            _StubItem(1, 1, "300.00", product=_StubProduct(taxes=[_StubTax("18.000")])),
            _StubItem(2, 1, "100.00"),
        ]
        items[1].product = None  # type: ignore[assignment]
        order = _StubOrder(23, items, tax_amount="72.00")
        result = allocate_order(order, items)

        assert [ln.tax for ln in result.lines] == [5400, 1800]  # 3:1 on price
        assert verify(result, order) == []
        fallback = [w for w in result.warnings if w.startswith("tax_basis_fallback")]
        assert len(fallback) == 1
        assert "1 of 2" in fallback[0] and "extended price" in fallback[0]

    def test_all_zero_rates_with_tax_charged_falls_back_and_warns(self) -> None:
        """Every product exempt but the order carries tax: the rate basis cannot
        explain it, so say so rather than dividing by an all-zero weight."""
        items = [_StubItem(1, 1, "300.00"), _StubItem(2, 1, "100.00")]
        order = _StubOrder(24, items, tax_amount="40.00")
        result = allocate_order(order, items)

        assert [ln.tax for ln in result.lines] == [3000, 1000]
        assert verify(result, order) == []
        assert any("0% tax rate" in w for w in result.warnings)

    def test_zero_tax_on_exempt_lines_needs_no_warning(self) -> None:
        items = [_StubItem(1, 1, "300.00"), _StubItem(2, 1, "100.00")]
        order = _StubOrder(25, items)
        result = allocate_order(order, items)
        assert [ln.tax for ln in result.lines] == [0, 0]
        assert result.warnings == ()


# ===========================================================================
# verify()
# ===========================================================================


class TestVerify:

    def test_reports_nothing_for_a_correct_allocation(self) -> None:
        items = [_StubItem(1, 2, "19.99"), _StubItem(2, 5, "3.33")]
        order = _StubOrder(
            30, items, tax_amount="8.41", discount_amount="1.11", shipping_amount="79.00"
        )
        assert verify(allocate_order(order, items), order) == []

    def test_reports_the_field_when_the_order_amount_moves_underneath(self) -> None:
        """Simulates the failure mode: the split is stale relative to the order."""
        items = [_StubItem(1, 1, "10.00"), _StubItem(2, 1, "10.00")]
        order = _StubOrder(31, items, tax_amount="3.60", shipping_amount="79.00")
        result = allocate_order(order, items)
        order.tax_amount = Decimal("4.00")
        assert verify(result, order) == ["tax"]

    def test_gateway_fee_is_only_checked_when_supplied(self) -> None:
        items = [_StubItem(1, 1, "10.00")]
        order = _StubOrder(32, items)
        result = allocate_order(order, items, gateway_fee_minor=250)
        assert verify(result, order) == []
        assert verify(result, order, gateway_fee_minor=250) == []
        assert verify(result, order, gateway_fee_minor=251) == ["gateway_fee"]


# ===========================================================================
# 11: end-to-end against real rows
# ===========================================================================


class TestAllocationAgainstRealOrders:

    def test_db_realistic_order_reconciles_on_every_field(self) -> None:
        """A real order + items with tax, discount, shipping and COD surcharge.

        The awkward numbers are deliberate: ₹163.51 of tax over three lines
        priced 449.00 x2, 129.50 x1 and 79.99 x3 does not divide cleanly on any
        basis, so this fails the moment the allocator rounds instead of
        distributing remainders.
        """
        order_ids, product_ids, user_ids, category_ids, tax_ids = [], [], [], [], []
        db = SessionLocal()
        try:
            user = _create_user(db)
            user_ids.append(user.id)
            cat = _create_category(db)
            category_ids.append(cat.id)

            p1 = _create_product(db, price=Decimal("449.00"), weight_grams=250, category=cat)
            p2 = _create_product(db, price=Decimal("129.50"), weight_grams=90, category=cat)
            p3 = _create_product(db, price=Decimal("79.99"), weight_grams=40, category=cat)
            product_ids.extend([p1.id, p2.id, p3.id])

            order = _create_order(
                db,
                user,
                [(p1, 2), (p2, 1), (p3, 3)],
                tax_amount=Decimal("163.51"),
                discount_amount=Decimal("111.11"),
                shipping_amount=Decimal("79.00"),
                cod_surcharge_amount=Decimal("49.00"),
                payment_discount_amount=Decimal("17.77"),
            )
            order_ids.append(order.id)
            db.commit()

            db.expire_all()
            order = db.get(Order, order_ids[0])
            fee = _rupees("23.87")
            result = allocate_order(order, order.items, gateway_fee_minor=fee)

            assert verify(result, order, gateway_fee_minor=fee) == [], result.warnings
            assert len(result.lines) == 3
            assert result.total("extended_price") == to_minor(order.subtotal)
            assert result.total("tax") == _rupees("163.51")
            assert result.total("discount") == _rupees("111.11")
            assert result.total("shipping") == _rupees("79.00")
            assert result.total("cod_surcharge") == _rupees("49.00")
            assert result.total("payment_discount") == _rupees("17.77")
            assert result.total("gateway_fee") == fee
            assert all(ln.quality is MetricQuality.ALLOCATED for ln in result.lines)

            # Loading the lines in the opposite order must change nothing.
            reversed_result = allocate_order(
                order, list(reversed(list(order.items))), gateway_fee_minor=fee
            )
            assert reversed_result.lines == result.lines

            # Weight basis on real weight_grams: 500 + 90 + 120 = 710 grams.
            by_weight = allocate_order(
                order, order.items, shipping_basis=SHIPPING_BASIS_WEIGHT
            )
            assert verify(by_weight, order) == []
            assert [ln.shipping for ln in by_weight.lines] == [5563, 1002, 1335]
            assert sum(ln.shipping for ln in by_weight.lines) == _rupees("79.00")

        finally:
            _cleanup(order_ids, product_ids, user_ids, category_ids, tax_ids)
            db.close()

    def test_db_tax_uses_real_product_taxes_when_all_lines_resolve(self) -> None:
        """With real `product_taxes` rows, tax splits on rate, not on price."""
        order_ids, product_ids, user_ids, category_ids, tax_ids = [], [], [], [], []
        db = SessionLocal()
        try:
            user = _create_user(db)
            user_ids.append(user.id)
            gst18 = _create_tax(db, "18.000")
            gst5 = _create_tax(db, "5.000")
            tax_ids.extend([gst18.id, gst5.id])

            p1 = _create_product(db, price=Decimal("100.00"), taxes=[gst18])
            p2 = _create_product(db, price=Decimal("100.00"), taxes=[gst5])
            product_ids.extend([p1.id, p2.id])

            order = _create_order(
                db, user, [(p1, 1), (p2, 1)], tax_amount=Decimal("23.00")
            )
            order_ids.append(order.id)
            db.commit()

            db.expire_all()
            order = db.get(Order, order_ids[0])
            result = allocate_order(order, order.items)

            # Equal prices, unequal rates: 18:5, not 1:1.
            assert [ln.tax for ln in result.lines] == [1800, 500]
            assert verify(result, order) == []
            assert not any(w.startswith("tax_basis_fallback") for w in result.warnings)

        finally:
            _cleanup(order_ids, product_ids, user_ids, category_ids, tax_ids)
            db.close()

    def test_db_untaxed_products_fall_back_to_price_with_a_warning(self) -> None:
        """The common case today: no product_taxes rows anywhere, tax charged."""
        order_ids, product_ids, user_ids, category_ids, tax_ids = [], [], [], [], []
        db = SessionLocal()
        try:
            user = _create_user(db)
            user_ids.append(user.id)
            p1 = _create_product(db, price=Decimal("300.00"))
            p2 = _create_product(db, price=Decimal("100.00"))
            product_ids.extend([p1.id, p2.id])

            order = _create_order(
                db, user, [(p1, 1), (p2, 1)], tax_amount=Decimal("40.00")
            )
            order_ids.append(order.id)
            db.commit()

            db.expire_all()
            order = db.get(Order, order_ids[0])
            result = allocate_order(order, order.items)

            assert [ln.tax for ln in result.lines] == [3000, 1000]
            assert verify(result, order) == []
            assert any(w.startswith("tax_basis_fallback") for w in result.warnings)

        finally:
            _cleanup(order_ids, product_ids, user_ids, category_ids, tax_ids)
            db.close()
