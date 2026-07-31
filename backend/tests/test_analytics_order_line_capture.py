"""Gate for the `analytics_order_line` writer — capture, snapshot, backfill.

The table has been read since the analytics v2 schema landed and written by
nothing, so every assertion here is about a defect that was live in production,
not a hypothetical. In particular:

  * **I1** — ``SUM(alloc_x) == orders.x``, exactly, to the paisa. Asserted with
    integer equality on a 3-line order splitting ₹0.01 five different ways. A
    tolerance-based assertion would pass against precisely the rounding leak
    the allocator exists to prevent.
  * **I2** — renaming, re-SKU-ing or recategorising a product after the sale
    must not move the sale's history. This is the single reason the table
    exists; ``test_rename_does_not_rewrite_the_snapshot`` is the test that
    proves it, and it is the one to look at first if this file ever goes red.

House pattern, from ``test_analytics_allocation.py`` and
``test_sales_analytics_service.py``: module-local ``_create_*`` helpers, each
test owns its ``SessionLocal()``, and teardown runs in a ``finally`` against a
fresh session so it cannot fail on a half-rolled-back transaction.

Namespacing: every row these tests create carries the ``TESTAOL-`` prefix (SKU,
order number, category slug, email) and teardown deletes **by collected primary
key**, never by prefix scan and never by date range. Nothing here can reach a
``PERF-`` row from ``analytics_perf_report.py``, and nothing deletes on a date
predicate, so live 2026 trading data is untouchable from this file even though
these orders are themselves stamped today.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.analytics_facts import AnalyticsOrderLine, IdentitySource
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Category, Product
from app.models.user import User
from app.schemas.order import OrderCreate
from app.services.analytics import order_line_facts
from app.services.analytics.contracts import to_minor
from app.services.analytics.order_line_facts import (
    build_fact_rows,
    capture_order_lines,
)
from app.services.analytics.types import MetricQuality
from app.services.order_service import OrderService

PREFIX = "TESTAOL-"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


class _Bag:
    """Ids to delete on the way out. Collected as rows are made, so a test that
    fails halfway still tears down everything it managed to create."""

    def __init__(self) -> None:
        self.orders: list[int] = []
        self.products: list[int] = []
        self.users: list[int] = []
        self.categories: list[int] = []


def _create_user(db: Session, bag: _Bag) -> User:
    user = User(
        email=f"{PREFIX.lower()}{_uid()}@example.invalid",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.flush()
    bag.users.append(int(user.id))
    return user


def _create_category(db: Session, bag: _Bag, name: str | None = None) -> Category:
    uid = _uid()
    category = Category(
        name=name or f"{PREFIX}Cat-{uid}", slug=f"{PREFIX.lower()}cat-{uid}"
    )
    db.add(category)
    db.flush()
    bag.categories.append(int(category.id))
    return category


def _create_product(
    db: Session,
    bag: _Bag,
    *,
    price: Decimal,
    cost: Decimal | None = None,
    category: Category | None = None,
    stock: int = 500,
) -> Product:
    uid = _uid()
    product = Product(
        sku=f"{PREFIX}SKU-{uid}",
        name=f"{PREFIX}Product {uid}",
        price=price,
        cost=cost,
        stock=stock,
        category_id=category.id if category else None,
    )
    db.add(product)
    db.flush()
    bag.products.append(int(product.id))
    return product


def _create_raw_order(
    db: Session,
    bag: _Bag,
    user: User,
    lines: list[tuple[Product, int]],
    *,
    tax_amount: Decimal = Decimal("0.00"),
    discount_amount: Decimal = Decimal("0.00"),
    shipping_amount: Decimal = Decimal("0.00"),
    cod_surcharge_amount: Decimal = Decimal("0.00"),
    payment_discount_amount: Decimal = Decimal("0.00"),
    snapshot_cost: bool = True,
) -> Order:
    """An order written straight to the table — the shape the BACKFILL sees.

    Deliberately not through ``OrderService.create``: the backfill's whole job is
    orders that exist with no fact rows, which is not a state the live capture
    can produce any more.
    """
    subtotal = sum((p.price * q for p, q in lines), Decimal("0.00"))
    order = Order(
        user_id=user.id,
        order_number=f"{PREFIX}{_uid()}",
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
    order.items = [
        OrderItem(
            product_id=p.id,
            quantity=q,
            unit_price=p.price,
            unit_cost=(p.cost if snapshot_cost else None),
        )
        for p, q in lines
    ]
    db.add(order)
    db.flush()
    bag.orders.append(int(order.id))
    return order


def _facts(db: Session, order_id: int) -> list[AnalyticsOrderLine]:
    return list(
        db.execute(
            select(AnalyticsOrderLine)
            .where(AnalyticsOrderLine.order_id == order_id)
            .order_by(AnalyticsOrderLine.order_item_id)
        ).scalars()
    )


def _cleanup(bag: _Bag) -> None:
    """Delete this test's rows, by id, through a fresh session.

    Order matters (facts, items, orders, products, users, categories) and every
    predicate is an explicit id list — no ``LIKE``, no date range, so there is no
    predicate here that could widen to a row the test did not create.
    """
    with SessionLocal() as s:
        if bag.orders:
            s.execute(
                delete(AnalyticsOrderLine).where(
                    AnalyticsOrderLine.order_id.in_(bag.orders)
                )
            )
            s.execute(
                text("DELETE FROM order_items WHERE order_id IN :ids"),
                {"ids": tuple(bag.orders)},
            )
            s.execute(
                text("DELETE FROM order_payments WHERE order_id IN :ids"),
                {"ids": tuple(bag.orders)},
            )
            s.execute(
                text("DELETE FROM order_addresses WHERE order_id IN :ids"),
                {"ids": tuple(bag.orders)},
            )
            s.execute(
                text("DELETE FROM orders WHERE id IN :ids"), {"ids": tuple(bag.orders)}
            )
        if bag.products:
            s.execute(
                text("DELETE FROM product_taxes WHERE product_id IN :ids"),
                {"ids": tuple(bag.products)},
            )
            s.execute(
                text("DELETE FROM products WHERE id IN :ids"),
                {"ids": tuple(bag.products)},
            )
        if bag.users:
            s.execute(
                text("DELETE FROM points_transactions WHERE user_id IN :ids"),
                {"ids": tuple(bag.users)},
            )
            s.execute(
                text("DELETE FROM users WHERE id IN :ids"), {"ids": tuple(bag.users)}
            )
        if bag.categories:
            s.execute(
                text("DELETE FROM categories WHERE id IN :ids"),
                {"ids": tuple(bag.categories)},
            )
        s.commit()


# ---------------------------------------------------------------------------
# 1. Forward capture
# ---------------------------------------------------------------------------


def test_creating_an_order_writes_one_fact_per_line() -> None:
    """The defect in one assertion: this used to write nothing at all."""
    bag = _Bag()
    db = SessionLocal()
    try:
        user = _create_user(db, bag)
        category = _create_category(db, bag)
        p1 = _create_product(db, bag, price=Decimal("449.00"), cost=Decimal("180.00"),
                             category=category)
        p2 = _create_product(db, bag, price=Decimal("129.50"), cost=Decimal("60.25"),
                             category=category)
        db.commit()

        order = OrderService(db).create(
            user.id,
            OrderCreate.model_validate(
                {
                    "items": [
                        {"product_id": p1.id, "quantity": 2},
                        {"product_id": p2.id, "quantity": 1},
                    ],
                    "shipping_address": f"{PREFIX}addr",
                }
            ),
        )
        bag.orders.append(int(order.id))

        rows = _facts(db, int(order.id))
        assert len(rows) == 2, "one fact row per order line, no more and no fewer"

        by_product = {int(r.product_id): r for r in rows}
        assert set(by_product) == {int(p1.id), int(p2.id)}

        first = by_product[int(p1.id)]
        assert first.identity_source == IdentitySource.CAPTURED_AT_SALE
        assert first.sku_snapshot == p1.sku
        assert first.product_name_snapshot == p1.name
        assert int(first.category_id_snapshot) == int(category.id)
        assert first.category_name_snapshot == category.name
        assert first.quantity == 2
        assert Decimal(first.unit_price) == Decimal("449.00")
        assert Decimal(first.extended_price) == Decimal("898.00")
        # Cost WAS snapshotted at sale, so it is authoritative — not allocated,
        # not estimated.
        assert Decimal(first.unit_cost) == Decimal("180.00")
        assert first.cost_quality == MetricQuality.AUTHORITATIVE.value
        assert first.alloc_quality == MetricQuality.ALLOCATED.value
        # `products` has no brand column today; the getattr fallback yields NULL
        # rather than inventing one from the product name.
        assert first.brand_snapshot is None
        # Bucketed on the store-local reporting day, with a generation stamped.
        assert first.bucket_date is not None
        assert int(first.tz_generation) >= 1
        assert first.order_status == OrderStatus.PENDING.value
        assert first.payment_method == "prepaid"
    finally:
        db.rollback()
        db.close()
        _cleanup(bag)


def test_capture_is_idempotent_within_the_same_order() -> None:
    """A second capture on an already-captured order writes nothing.

    UNIQUE on `order_item_id` is what enforces it; this proves the writer reports
    the skip honestly instead of relying on MySQL's affected-rows, which cannot
    distinguish "duplicate ignored" from "row unchanged".
    """
    bag = _Bag()
    db = SessionLocal()
    try:
        user = _create_user(db, bag)
        product = _create_product(db, bag, price=Decimal("99.00"))
        db.commit()

        order = OrderService(db).create(
            user.id,
            OrderCreate.model_validate(
                {"items": [{"product_id": product.id, "quantity": 1}]}
            ),
        )
        bag.orders.append(int(order.id))
        assert len(_facts(db, int(order.id))) == 1

        assert capture_order_lines(db, order) == 0
        db.commit()
        assert len(_facts(db, int(order.id))) == 1
    finally:
        db.rollback()
        db.close()
        _cleanup(bag)


# ---------------------------------------------------------------------------
# 2. I2 — the snapshot does not move when the catalogue does
# ---------------------------------------------------------------------------


def test_rename_does_not_rewrite_the_snapshot() -> None:
    """Rename, re-SKU and recategorise after the sale; history must not move.

    This is the invariant the whole table exists for. Before it, product identity
    on every historical report resolved through a live join to `products`, so an
    admin editing a product name silently rewrote last quarter's numbers with
    nothing to compare against.
    """
    bag = _Bag()
    db = SessionLocal()
    try:
        user = _create_user(db, bag)
        old_category = _create_category(db, bag)
        new_category = _create_category(db, bag)
        product = _create_product(
            db, bag, price=Decimal("250.00"), category=old_category
        )
        original_sku, original_name = product.sku, product.name
        db.commit()

        order = OrderService(db).create(
            user.id,
            OrderCreate.model_validate(
                {"items": [{"product_id": product.id, "quantity": 3}]}
            ),
        )
        bag.orders.append(int(order.id))
        assert len(_facts(db, int(order.id))) == 1

        # The catalogue edit that used to rewrite history.
        product.name = f"{PREFIX}RENAMED {_uid()}"
        product.sku = f"{PREFIX}RESKU-{_uid()}"
        product.category_id = new_category.id
        db.commit()
        db.expire_all()

        fact = _facts(db, int(order.id))[0]
        assert fact.product_name_snapshot == original_name
        assert fact.sku_snapshot == original_sku
        assert int(fact.category_id_snapshot) == int(old_category.id)
        assert fact.category_name_snapshot == old_category.name
        # And the live catalogue really did change — otherwise this test would
        # pass for the wrong reason.
        assert product.name != original_name
        assert product.sku != original_sku
        assert int(product.category_id) == int(new_category.id)
    finally:
        db.rollback()
        db.close()
        _cleanup(bag)


# ---------------------------------------------------------------------------
# 3. I1 — allocation reconciles exactly, including a 1-paisa 3-way split
# ---------------------------------------------------------------------------


ALLOC_FIELDS = (
    ("alloc_discount", "discount_amount"),
    ("alloc_tax", "tax_amount"),
    ("alloc_shipping", "shipping_amount"),
    ("alloc_cod_surcharge", "cod_surcharge_amount"),
    ("alloc_payment_discount", "payment_discount_amount"),
)


def test_allocation_reconciles_to_the_paisa_on_a_one_paisa_three_way_split() -> None:
    """₹0.01 across three lines: the residue lands somewhere, never nowhere.

    One paisa cannot be divided three ways. The largest-remainder allocator hands
    the indivisible unit to a single line by a deterministic rule, so the shares
    sum back to ₹0.01 exactly. A per-line ``round(total * share, 2)`` would give
    three zeros and quietly lose the paisa — which is the failure this asserts
    against, five separate times.
    """
    bag = _Bag()
    db = SessionLocal()
    try:
        user = _create_user(db, bag)
        p1 = _create_product(db, bag, price=Decimal("100.00"))
        p2 = _create_product(db, bag, price=Decimal("100.00"))
        p3 = _create_product(db, bag, price=Decimal("100.00"))
        db.commit()

        penny = Decimal("0.01")
        order = _create_raw_order(
            db, bag, user, [(p1, 1), (p2, 1), (p3, 1)],
            tax_amount=penny,
            discount_amount=penny,
            shipping_amount=penny,
            cod_surcharge_amount=penny,
            payment_discount_amount=penny,
        )
        db.commit()

        rows = build_fact_rows(
            db, order, identity_source=IdentitySource.CAPTURED_AT_SALE
        )
        order_line_facts.write_fact_rows(db, rows)
        db.commit()

        facts = _facts(db, int(order.id))
        assert len(facts) == 3

        for fact_field, order_field in ALLOC_FIELDS:
            allocated = sum(to_minor(Decimal(getattr(f, fact_field))) for f in facts)
            expected = to_minor(Decimal(getattr(order, order_field)))
            assert allocated == expected, (
                f"{fact_field} summed to {allocated} paise but "
                f"orders.{order_field} is {expected} paise — the allocator leaked"
            )
            # And the split is genuinely indivisible: exactly one line carries it.
            assert expected == 1
            carriers = [
                f for f in facts if to_minor(Decimal(getattr(f, fact_field))) == 1
            ]
            assert len(carriers) == 1

        # Gross value still reconciles to the order's own subtotal.
        extended = sum(to_minor(Decimal(f.extended_price)) for f in facts)
        assert extended == to_minor(Decimal(order.subtotal)) == 30000

        # No settlement feed exists, so no gateway fee is claimed.
        assert all(Decimal(f.alloc_gateway_fee) == Decimal("0.00") for f in facts)
    finally:
        db.rollback()
        db.close()
        _cleanup(bag)


def test_allocation_reconciles_on_an_uneven_multi_line_order() -> None:
    """The same invariant on prices that do not divide evenly."""
    bag = _Bag()
    db = SessionLocal()
    try:
        user = _create_user(db, bag)
        p1 = _create_product(db, bag, price=Decimal("449.00"))
        p2 = _create_product(db, bag, price=Decimal("129.50"))
        p3 = _create_product(db, bag, price=Decimal("79.99"))
        db.commit()

        order = _create_raw_order(
            db, bag, user, [(p1, 2), (p2, 1), (p3, 3)],
            tax_amount=Decimal("57.33"),
            discount_amount=Decimal("99.99"),
            shipping_amount=Decimal("49.00"),
            cod_surcharge_amount=Decimal("25.00"),
            payment_discount_amount=Decimal("13.37"),
        )
        db.commit()

        rows = build_fact_rows(db, order)
        order_line_facts.write_fact_rows(db, rows)
        db.commit()

        facts = _facts(db, int(order.id))
        assert len(facts) == 3
        for fact_field, order_field in ALLOC_FIELDS:
            allocated = sum(to_minor(Decimal(getattr(f, fact_field))) for f in facts)
            assert allocated == to_minor(Decimal(getattr(order, order_field)))
    finally:
        db.rollback()
        db.close()
        _cleanup(bag)


# ---------------------------------------------------------------------------
# 4 + 5. Backfill: idempotent, and honestly flagged
# ---------------------------------------------------------------------------


def _run_backfill(**kwargs):
    from scripts.backfill_order_lines import backfill

    return backfill(quiet=True, **kwargs)


def test_backfill_flags_rows_as_backfilled_not_captured() -> None:
    """A historical order gets facts, labelled with where its identity came from.

    ``BACKFILLED_CURRENT_CATALOG`` is not decoration: it says the name and SKU
    were read from today's catalogue, which is right for every SKU never renamed
    and quietly wrong for every SKU that was. A report can only account for that
    if the row says so.
    """
    bag = _Bag()
    db = SessionLocal()
    try:
        user = _create_user(db, bag)
        category = _create_category(db, bag)
        product = _create_product(
            db, bag, price=Decimal("310.00"), cost=Decimal("120.00"), category=category
        )
        order = _create_raw_order(db, bag, user, [(product, 2)],
                                  tax_amount=Decimal("55.80"))
        db.commit()
        order_id = int(order.id)

        assert _facts(db, order_id) == []

        stats = _run_backfill(order_id=order_id)
        assert stats["rows_written"] == 1
        assert stats["orders_with_gaps"] == 1

        # End this session's read view before re-reading. The backfill commits
        # on its OWN session, and under MySQL's REPEATABLE READ the SELECT above
        # pinned a snapshot that predates it — `expire_all()` would re-query and
        # still be served the old view. This is the same discipline
        # `order_service._lock_order` documents.
        db.rollback()
        facts = _facts(db, order_id)
        assert len(facts) == 1
        fact = facts[0]
        assert fact.identity_source == IdentitySource.BACKFILLED_CURRENT_CATALOG
        assert fact.identity_source != IdentitySource.CAPTURED_AT_SALE
        assert fact.sku_snapshot == product.sku
        assert fact.product_name_snapshot == product.name
        assert int(fact.category_id_snapshot) == int(category.id)
        # The money is NOT degraded by the backfill — allocation still reconciles.
        assert to_minor(Decimal(fact.alloc_tax)) == to_minor(Decimal("55.80"))
        assert Decimal(fact.extended_price) == Decimal("620.00")
    finally:
        db.rollback()
        db.close()
        _cleanup(bag)


def test_rerunning_the_backfill_over_captured_orders_writes_nothing() -> None:
    """Idempotency, asserted as a count of zero rather than "no duplicates".

    Also proves the backfill does not relabel: an order captured at sale keeps
    ``CAPTURED_AT_SALE`` after the backfill has walked over it.
    """
    bag = _Bag()
    db = SessionLocal()
    try:
        user = _create_user(db, bag)
        product = _create_product(db, bag, price=Decimal("75.00"))
        db.commit()

        order = OrderService(db).create(
            user.id,
            OrderCreate.model_validate(
                {"items": [{"product_id": product.id, "quantity": 4}]}
            ),
        )
        order_id = int(order.id)
        bag.orders.append(order_id)
        before = _facts(db, order_id)
        assert len(before) == 1

        first = _run_backfill(order_id=order_id)
        assert first["rows_written"] == 0
        assert first["orders_already_complete"] == 1
        assert first["lines_already_present"] == 1

        # And again, for good measure — a re-run must stay a no-op.
        second = _run_backfill(order_id=order_id)
        assert second["rows_written"] == 0

        db.rollback()  # end the read view — the backfill commits on its own session
        after = _facts(db, order_id)
        assert len(after) == 1
        assert after[0].identity_source == IdentitySource.CAPTURED_AT_SALE
        assert int(after[0].id) == int(before[0].id), "the row was not rewritten"
    finally:
        db.rollback()
        db.close()
        _cleanup(bag)


def test_backfill_dry_run_writes_nothing_and_tiny_datasets_are_fine() -> None:
    """A dry run reports the gap without filling it, and paging survives a
    dataset far smaller than one page — production has 8 orders."""
    bag = _Bag()
    db = SessionLocal()
    try:
        user = _create_user(db, bag)
        product = _create_product(db, bag, price=Decimal("42.00"))
        order = _create_raw_order(db, bag, user, [(product, 1)])
        db.commit()
        order_id = int(order.id)

        stats = _run_backfill(order_id=order_id, dry_run=True, page_size=1000)
        assert stats["dry_run"] is True
        assert stats["rows_written"] == 1
        db.rollback()  # end the read view — see the note in the test above
        assert _facts(db, order_id) == [], "a dry run must not write"

        stats = _run_backfill(order_id=order_id, page_size=1)
        assert stats["rows_written"] == 1
        db.rollback()
        assert len(_facts(db, order_id)) == 1
    finally:
        db.rollback()
        db.close()
        _cleanup(bag)


def test_backfill_refuses_a_non_throwaway_database_without_the_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production guard, and the shape of its deliberate override."""
    from app.core.config import settings as app_settings
    from scripts import backfill_order_lines as script

    monkeypatch.setattr(app_settings, "MYSQL_HOST", "13.204.184.41", raising=False)
    monkeypatch.setattr(app_settings, "ENVIRONMENT", "production", raising=False)

    with pytest.raises(script.UnsafeDatabaseError) as excinfo:
        script.guard_database(yes_production=False)
    assert "Refusing to run" in str(excinfo.value)

    # The CLI turns the refusal into exit code 3, matching conftest's guard.
    assert script.main(["--dry-run"]) == 3

    # With the flag it proceeds, and says exactly what it is about to write to.
    assert script.guard_database(yes_production=True, quiet=True) is False


# ---------------------------------------------------------------------------
# 6. Cost honesty
# ---------------------------------------------------------------------------


def test_missing_unit_cost_is_incomplete_never_zero() -> None:
    """A NULL cost stays NULL and is graded INCOMPLETE.

    ``ProfitService`` does ``coalesce(OrderItem.unit_cost, 0)``, which reports
    100% margin on every order placed before cost tracking existed. A fact table
    that repeated that would bake the lie into the rollups it feeds.
    """
    bag = _Bag()
    db = SessionLocal()
    try:
        user = _create_user(db, bag)
        with_cost = _create_product(db, bag, price=Decimal("200.00"),
                                    cost=Decimal("90.00"))
        without_cost = _create_product(db, bag, price=Decimal("200.00"), cost=None)
        order = _create_raw_order(db, bag, user, [(with_cost, 1), (without_cost, 1)])
        db.commit()
        order_id = int(order.id)

        assert _run_backfill(order_id=order_id)["rows_written"] == 2
        db.rollback()  # end the read view — the backfill commits on its own session

        by_product = {int(f.product_id): f for f in _facts(db, order_id)}
        priced = by_product[int(with_cost.id)]
        assert Decimal(priced.unit_cost) == Decimal("90.00")
        assert priced.cost_quality == MetricQuality.AUTHORITATIVE.value

        missing = by_product[int(without_cost.id)]
        assert missing.unit_cost is None, "a missing cost must stay NULL"
        assert Decimal(missing.unit_cost or -1) != Decimal("0")
        assert missing.cost_quality == MetricQuality.INCOMPLETE.value
        # And it is not silently ESTIMATED from today's catalogue cost either.
        assert missing.cost_quality != MetricQuality.ESTIMATED.value
    finally:
        db.rollback()
        db.close()
        _cleanup(bag)


# ---------------------------------------------------------------------------
# 7. The failure mode this writer actually chose
# ---------------------------------------------------------------------------


def test_analytics_failure_never_fails_checkout_and_stays_recoverable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The savepoint decision, asserted end to end.

    The writer runs in the order's own transaction (so facts can never outlive a
    rolled-back order) but inside ``begin_nested()`` (so a fact failure can never
    cost a sale). All three consequences are checked here:

      1. the checkout completes and the order is committed and readable;
      2. the partial fact write is discarded — the savepoint rolls back, so the
         table is not left with half an order's lines;
      3. the failure is logged at ERROR, and the backfill finds the gap
         afterwards without being told where it is.
    """
    bag = _Bag()
    db = SessionLocal()
    try:
        user = _create_user(db, bag)
        p1 = _create_product(db, bag, price=Decimal("120.00"))
        p2 = _create_product(db, bag, price=Decimal("340.00"))
        db.commit()

        real_write = order_line_facts.write_fact_rows

        def _write_then_explode(session, rows):
            # Write the first row for real, then fail — the shape of a genuine
            # mid-statement failure, which is what the savepoint has to undo.
            real_write(session, list(rows)[:1])
            raise RuntimeError("simulated analytics fact write failure")

        monkeypatch.setattr(
            order_line_facts, "write_fact_rows", _write_then_explode
        )

        with caplog.at_level("ERROR", logger=order_line_facts.logger.name):
            order = OrderService(db).create(
                user.id,
                OrderCreate.model_validate(
                    {
                        "items": [
                            {"product_id": p1.id, "quantity": 1},
                            {"product_id": p2.id, "quantity": 2},
                        ]
                    }
                ),
            )
        order_id = int(order.id)
        bag.orders.append(order_id)

        # 1. Checkout survived.
        assert order_id > 0
        assert order.order_number
        with SessionLocal() as fresh:
            committed = fresh.get(Order, order_id)
            assert committed is not None, "the order must be committed"
            assert len(list(committed.items)) == 2
            # 2. The savepoint discarded the partial write.
            assert _facts(fresh, order_id) == []

        # 3. The failure is loud.
        assert any(
            "capture FAILED" in record.message for record in caplog.records
        ), "an analytics write failure must be logged, never swallowed silently"

        # ...and recoverable: the backfill finds it by its own predicate.
        monkeypatch.setattr(order_line_facts, "write_fact_rows", real_write)
        stats = _run_backfill(order_id=order_id)
        assert stats["rows_written"] == 2
        with SessionLocal() as fresh:
            recovered = _facts(fresh, order_id)
            assert len(recovered) == 2
            assert all(
                r.identity_source == IdentitySource.BACKFILLED_CURRENT_CATALOG
                for r in recovered
            )
    finally:
        db.rollback()
        db.close()
        _cleanup(bag)


def test_a_rolled_back_order_leaves_no_orphan_facts() -> None:
    """The other half of the asymmetry: facts never outlive their order.

    Same transaction is what buys this. A fact row pointing at a rolled-back
    `order_items.id` would be attributed to whatever row later reuses that id.
    """
    bag = _Bag()
    db = SessionLocal()
    try:
        user = _create_user(db, bag)
        product = _create_product(db, bag, price=Decimal("55.00"))
        db.commit()

        order = _create_raw_order(db, bag, user, [(product, 1)])
        order_id = int(order.id)
        item_ids = [int(i.id) for i in order.items]
        written = capture_order_lines(db, order)
        assert written == 1

        db.rollback()  # the checkout failed after the capture

        with SessionLocal() as fresh:
            assert fresh.get(Order, order_id) is None
            orphans = fresh.execute(
                select(func.count(AnalyticsOrderLine.id)).where(
                    AnalyticsOrderLine.order_item_id.in_(item_ids)
                )
            ).scalar_one()
            assert orphans == 0
    finally:
        db.rollback()
        db.close()
        _cleanup(bag)


# ---------------------------------------------------------------------------
# 8. brand: correct before, during and after the concurrent column migration
# ---------------------------------------------------------------------------


class _StubCategory:
    def __init__(self, name: str) -> None:
        self.name = name


class _StubProduct:
    """A product row with the `brand` attribute controllable per instance.

    Duck-typed rather than a real ``Product`` so BOTH sides of the migration
    stay covered permanently. ``products.brand`` landed while this was being
    written; without a stub, the "attribute absent" branch would become
    untestable the moment it did, and would then be the branch nobody notices
    is broken if the column is ever renamed or the model reordered.
    """

    def __init__(self, *, brand: str | None = None, has_brand: bool = True) -> None:
        self.sku = f"{PREFIX}BRAND"
        self.name = f"{PREFIX}Branded"
        self.category_id = 7
        self.category = _StubCategory("Gummies")
        if has_brand:
            self.brand = brand


def test_brand_getattr_fallback_with_and_without_the_column() -> None:
    """``getattr(product, "brand", None)`` is correct on both sides of the
    concurrent `products.brand` migration.

    Attribute absent (pre-migration) and attribute present-but-NULL both mean
    "no brand" and both write NULL. Neither ever derives a brand from the
    product name, which the model docstring explicitly forbids. Present and set
    is copied through verbatim, clipped to the column's 120 chars.
    """
    bag = _Bag()
    db = SessionLocal()
    try:
        # --- attribute absent: `products` before the brand column landed ---
        absent = order_line_facts._identity_snapshot(_StubProduct(has_brand=False))
        assert not hasattr(_StubProduct(has_brand=False), "brand")
        assert absent["resolved"] is True
        assert absent["brand_snapshot"] is None
        assert absent["sku_snapshot"] == f"{PREFIX}BRAND"

        # --- attribute present ---
        branded = order_line_facts._identity_snapshot(_StubProduct(brand="Wellvia"))
        assert branded["brand_snapshot"] == "Wellvia"
        assert branded["category_name_snapshot"] == "Gummies"
        assert branded["category_id_snapshot"] == 7

        # Present but NULL / blank is still "no brand", not an empty string.
        assert order_line_facts._identity_snapshot(
            _StubProduct(brand=None)
        )["brand_snapshot"] is None
        assert order_line_facts._identity_snapshot(
            _StubProduct(brand="   ")
        )["brand_snapshot"] is None

        # Clipped to the column width rather than left to MySQL truncation.
        long_brand = order_line_facts._identity_snapshot(_StubProduct(brand="B" * 300))
        assert len(long_brand["brand_snapshot"]) == 120

        # --- and against the real mapped Product, whatever state it is in ---
        product = _create_product(db, bag, price=Decimal("10.00"))
        snapshot = order_line_facts._identity_snapshot(product)
        assert snapshot["resolved"] is True
        assert snapshot["sku_snapshot"] == product.sku
        # Unset today either way: no column, or a NULL column.
        assert snapshot["brand_snapshot"] is None
        if hasattr(Product, "brand"):
            # The column has landed — prove a real value flows into the fact.
            product.brand = "Wellvia Labs"
            db.flush()
            assert order_line_facts._identity_snapshot(product)[
                "brand_snapshot"
            ] == "Wellvia Labs"

        # --- no product at all: provenance degrades, values are never invented ---
        gone = order_line_facts._identity_snapshot(None)
        assert gone["resolved"] is False
        assert gone["sku_snapshot"] == "-"
        assert gone["product_name_snapshot"] == "-"
        assert gone["brand_snapshot"] is None
    finally:
        db.rollback()
        db.close()
        _cleanup(bag)
