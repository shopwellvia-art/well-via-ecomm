"""Tests for the derived GST tax-invoice feature (no schema change).

Covers, per the Phase 3 spec:
  * the invoice number is DERIVED and format-stable
    (``WV-{Indian FY}-{order.id:06d}``, FY = April–March from created_at);
  * the owner of a PAID order can download the PDF;
  * a different authenticated user gets the same 404 as a missing order
    (no IDOR / existence leak);
  * a PENDING order is refused (409 — nothing was supplied yet);
  * the admin route is permission-gated (403 for a plain customer,
    200 for staff with ``orders.view_all`` via the is_admin bypass);
  * the settings registry ships the ``store.*`` seller-identity keys.

Two layers, mirroring ``test_order_detail_display.py``:

* ``TestInvoiceNumberUnit`` / ``TestGstSplitUnit`` / ``TestRenderUnit`` —
  pure, in-memory (no database).
* ``TestInvoiceHttp`` — integration through the real API + DB, auto-skipping
  when the database is unreachable. Rows are created via SessionLocal and
  cleaned up FK-safe (same pattern as ``test_returns_refund_flow.py``).

Run inside the backend container:
    docker compose exec -T backend pytest tests/test_invoices.py -v
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.security import create_access_token, hash_password
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Product
from app.models.user import User
from app.services.invoice_service import (
    InvoiceData,
    InvoiceLine,
    fiscal_year_for,
    invoice_number_for,
    render_invoice_pdf,
    state_code_for,
)
from app.services.settings_seed import DEFAULT_SETTINGS


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# --------------------------------------------------------------------------- #
# Unit tests — no database                                                     #
# --------------------------------------------------------------------------- #
class TestInvoiceNumberUnit:
    def test_fiscal_year_april_onwards_is_current_year(self):
        assert fiscal_year_for(datetime(2026, 7, 15, tzinfo=timezone.utc)) == "2026-27"
        assert fiscal_year_for(datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)) == "2026-27"

    def test_fiscal_year_january_to_march_is_previous_year(self):
        assert fiscal_year_for(datetime(2026, 2, 10, tzinfo=timezone.utc)) == "2025-26"
        assert fiscal_year_for(datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)) == "2025-26"

    def test_fiscal_year_boundary_is_evaluated_in_ist(self):
        # 31 Mar 23:00 UTC is already 1 Apr 04:30 IST — the new fiscal year.
        assert fiscal_year_for(datetime(2026, 3, 31, 23, 0, tzinfo=timezone.utc)) == "2026-27"

    def test_naive_datetimes_are_treated_as_utc(self):
        assert fiscal_year_for(datetime(2026, 7, 15)) == "2026-27"

    def test_invoice_number_format_is_stable(self):
        created = datetime(2026, 7, 15, tzinfo=timezone.utc)
        assert invoice_number_for(123, created) == "WV-2026-27-000123"
        assert invoice_number_for(7, datetime(2026, 2, 1, tzinfo=timezone.utc)) == "WV-2025-26-000007"
        # Zero-padding caps at 6 but never truncates larger ids.
        assert invoice_number_for(1234567, created) == "WV-2026-27-1234567"

    def test_invoice_number_is_deterministic(self):
        created = datetime(2026, 5, 5, tzinfo=timezone.utc)
        assert invoice_number_for(42, created) == invoice_number_for(42, created)


class TestGstSplitUnit:
    def test_state_code_mapping(self):
        assert state_code_for("Karnataka") == "29"
        assert state_code_for(" tamil  nadu ") == "33"
        assert state_code_for("Jammu & Kashmir") == "01"
        assert state_code_for("Orissa") == "21"  # legacy spelling

    def test_unknown_state_maps_to_none(self):
        assert state_code_for("Narnia") is None
        assert state_code_for("") is None
        assert state_code_for(None) is None

    def test_cgst_sgst_halves_always_sum_to_line_tax(self):
        # Odd paise: 53.65 → 26.83 + 26.82 (remainder half), never 53.66.
        line = InvoiceLine(
            description="x", sku=None, quantity=1, unit_price=Decimal("298.00"),
            taxable_value=Decimal("298.00"), tax_rate=Decimal("18"),
            tax_amount=Decimal("53.65"),
        )
        assert line.cgst + line.sgst == line.tax_amount
        assert line.total == Decimal("351.65")


class TestRenderUnit:
    def _data(self, mode: str) -> InvoiceData:
        return InvoiceData(
            invoice_number="WV-2026-27-000123",
            invoice_date=datetime(2026, 7, 15, tzinfo=timezone.utc),
            order_number="WV-2026-000123",
            order_status="paid",
            payment_method="prepaid",
            seller_legal_name="Wellvia Commerce Pvt Ltd",
            seller_address="12, 3rd Cross, Bengaluru, Karnataka 560001",
            seller_gstin="29ABCDE1234F1Z5",
            seller_state_code="29" if mode != "single" else "",
            place_of_supply="Karnataka (29)",
            bill_to=["Asha Rao", "42 Test Lane", "Bengaluru, Karnataka - 560001"],
            ship_to=["Asha Rao", "42 Test Lane", "Bengaluru, Karnataka - 560001"],
            lines=[
                InvoiceLine("Gummies", "SKU1", 2, Decimal("499.00"),
                            Decimal("998.00"), Decimal("18"), Decimal("179.64")),
            ],
            subtotal=Decimal("998.00"),
            tax_amount=Decimal("179.64"),
            total_amount=Decimal("1177.64"),
            tax_mode=mode,  # type: ignore[arg-type]
            tax_note="note" if mode == "single" else None,
        )

    @pytest.mark.parametrize("mode", ["cgst_sgst", "igst", "single"])
    def test_renders_a_pdf_in_every_tax_mode(self, mode: str):
        pdf = render_invoice_pdf(self._data(mode))
        assert pdf.startswith(b"%PDF")
        assert len(pdf) > 1000


class TestSettingsRegistryUnit:
    def test_store_identity_keys_are_seeded(self):
        keys = {k for k, *_rest in DEFAULT_SETTINGS}
        assert {
            "store.legal_name", "store.address", "store.gstin", "store.state_code",
        } <= keys

    def test_store_keys_default_blank_and_public_category(self):
        rows = {k: (v, cat, secret) for k, v, cat, _d, secret in DEFAULT_SETTINGS}
        for key in ("store.legal_name", "store.address", "store.gstin", "store.state_code"):
            value, category, is_secret = rows[key]
            assert value == ""  # placeholder — admin fills it in Settings UI
            assert category == "store"
            assert is_secret is False


# --------------------------------------------------------------------------- #
# Integration tests — real API + DB (auto-skip when unreachable)               #
# --------------------------------------------------------------------------- #
@pytest.fixture
def db():
    from sqlalchemy.exc import OperationalError

    from app.db.session import SessionLocal

    try:
        session = SessionLocal()
        session.execute(text("SELECT 1"))
    except OperationalError as exc:  # pragma: no cover - environmental
        pytest.skip(f"database unreachable: {exc}")
    try:
        yield session
    finally:
        session.close()


def _make_user(db, *, is_admin: bool = False) -> User:
    user = User(
        email=f"inv-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=is_admin,
    )
    db.add(user)
    db.flush()
    return user


def _make_order(db, user, *, status: OrderStatus) -> tuple[Order, Product]:
    """A minimal order with one line + a structured address snapshot —
    the shape checkout leaves behind (see PaymentService._build_order)."""
    product = Product(
        sku=f"SKU-INV-{_uid()}", name=f"InvProd-{_uid()}",
        price=Decimal("499.00"), stock=100,
    )
    db.add(product)
    db.flush()
    order = Order(
        user_id=user.id,
        status=status,
        subtotal=Decimal("998.00"),
        tax_amount=Decimal("179.64"),
        discount_amount=Decimal("0.00"),
        shipping_amount=Decimal("49.00"),
        total_amount=Decimal("1226.64"),
        currency="INR",
        payment_method="prepaid",
        shipping_address="Asha Rao, 42 Test Lane, Bengaluru, Karnataka - 560001",
        shipping_pincode="560001",
        shipping_address_snapshot={
            "full_name": "Asha Rao", "phone": "9999999999",
            "line1": "42 Test Lane", "line2": None, "landmark": None,
            "city": "Bengaluru", "state": "Karnataka", "pincode": "560001",
            "country": "IN", "label": "home", "latitude": None, "longitude": None,
        },
        paid_at=datetime.now(timezone.utc) if status != OrderStatus.PENDING else None,
        # Explicit so the derived invoice number is computable pre-refresh
        # (created_at is otherwise a server default, unset until reload).
        created_at=datetime.now(timezone.utc),
    )
    order.items.append(
        OrderItem(product_id=product.id, quantity=2, unit_price=Decimal("499.00"))
    )
    db.add(order)
    db.flush()
    order.order_number = f"WV-{order.created_at.year}-{order.id:06d}"
    db.commit()
    db.refresh(order)
    return order, product


def _cleanup(user_ids: list[int], product_ids: list[int], order_ids: list[int]) -> None:
    from app.db.session import SessionLocal

    with SessionLocal() as s:
        for oid in order_ids:
            for tbl in ("order_items", "order_payments", "order_addresses", "shipments"):
                s.execute(text(f"DELETE FROM {tbl} WHERE order_id = :o"), {"o": oid})
            s.execute(text("DELETE FROM orders WHERE id = :o"), {"o": oid})
        for pid in product_ids:
            s.execute(text("DELETE FROM product_taxes WHERE product_id = :p"), {"p": pid})
            s.execute(text("DELETE FROM products WHERE id = :p"), {"p": pid})
        for uid in user_ids:
            s.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})
        s.commit()


def _headers(user_id: int, *, admin: bool = False) -> dict[str, str]:
    token = create_access_token(user_id, {"admin": True} if admin else None)
    return {"Authorization": f"Bearer {token}"}


class TestInvoiceHttp:
    def test_owner_downloads_pdf_for_paid_order(self, client: TestClient, db):
        owner = _make_user(db)
        order, product = _make_order(db, owner, status=OrderStatus.PAID)
        try:
            r = client.get(
                f"/api/v1/orders/{order.id}/invoice", headers=_headers(owner.id)
            )
            assert r.status_code == 200, r.text
            assert r.headers["content-type"] == "application/pdf"
            assert r.content.startswith(b"%PDF")
            expected = invoice_number_for(order.id, order.created_at)
            assert (
                r.headers["content-disposition"]
                == f'attachment; filename="invoice-{expected}.pdf"'
            )
        finally:
            _cleanup([owner.id], [product.id], [order.id])

    def test_non_owner_gets_404_not_403(self, client: TestClient, db):
        """IDOR guard: someone else's order must be indistinguishable from a
        missing one — the same 404 envelope, never a 403 existence leak."""
        owner = _make_user(db)
        stranger = _make_user(db)
        order, product = _make_order(db, owner, status=OrderStatus.PAID)
        try:
            r = client.get(
                f"/api/v1/orders/{order.id}/invoice", headers=_headers(stranger.id)
            )
            assert r.status_code == 404, r.text
            assert r.json()["error"]["code"] == "not_found"

            missing = client.get(
                "/api/v1/orders/99999999/invoice", headers=_headers(stranger.id)
            )
            assert missing.status_code == 404
            assert missing.json() == r.json()
        finally:
            _cleanup([owner.id, stranger.id], [product.id], [order.id])

    def test_pending_order_is_refused(self, client: TestClient, db):
        owner = _make_user(db)
        order, product = _make_order(db, owner, status=OrderStatus.PENDING)
        try:
            r = client.get(
                f"/api/v1/orders/{order.id}/invoice", headers=_headers(owner.id)
            )
            assert r.status_code == 409, r.text
            assert r.json()["error"]["code"] == "conflict"
        finally:
            _cleanup([owner.id], [product.id], [order.id])

    def test_unauthenticated_is_401(self, client: TestClient):
        r = client.get("/api/v1/orders/1/invoice")
        assert r.status_code == 401

    def test_admin_route_rejects_plain_customer(self, client: TestClient, db):
        """The admin route needs orders.view_all — a plain customer (even the
        order's owner) gets 403 from require_permission."""
        owner = _make_user(db)
        order, product = _make_order(db, owner, status=OrderStatus.PAID)
        try:
            r = client.get(
                f"/api/v1/orders/admin/{order.id}/invoice", headers=_headers(owner.id)
            )
            assert r.status_code == 403, r.text
            assert r.json()["error"]["code"] == "forbidden"
        finally:
            _cleanup([owner.id], [product.id], [order.id])

    def test_admin_route_streams_any_users_invoice(self, client: TestClient, db):
        owner = _make_user(db)
        admin = _make_user(db, is_admin=True)
        order, product = _make_order(db, owner, status=OrderStatus.PAID)
        try:
            r = client.get(
                f"/api/v1/orders/admin/{order.id}/invoice",
                headers=_headers(admin.id, admin=True),
            )
            assert r.status_code == 200, r.text
            assert r.headers["content-type"] == "application/pdf"
            assert r.content.startswith(b"%PDF")
            expected = invoice_number_for(order.id, order.created_at)
            assert f'invoice-{expected}.pdf' in r.headers["content-disposition"]
        finally:
            _cleanup([owner.id, admin.id], [product.id], [order.id])

    def test_invoice_number_reflects_indian_fiscal_year(self, client: TestClient, db):
        """Format-stability at the HTTP layer: the filename embeds the
        WV-{FY}-{id:06d} number derived from the order's created_at."""
        owner = _make_user(db)
        order, product = _make_order(db, owner, status=OrderStatus.PAID)
        try:
            expected = invoice_number_for(order.id, order.created_at)
            assert expected.startswith("WV-")
            fy = expected.split("-")
            # WV-{start}-{end2}-{id} → 4 chunks once split on "-"
            assert len(fy) == 4
            start_year, end_two = int(fy[1]), int(fy[2])
            assert (start_year + 1) % 100 == end_two
            assert fy[3] == f"{order.id:06d}"

            r = client.get(
                f"/api/v1/orders/{order.id}/invoice", headers=_headers(owner.id)
            )
            assert r.status_code == 200
            assert f'invoice-{expected}.pdf' in r.headers["content-disposition"]
        finally:
            _cleanup([owner.id], [product.id], [order.id])
