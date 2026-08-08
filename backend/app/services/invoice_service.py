"""Indian GST tax-invoice PDF generation — derived entirely from existing data.

NO schema change: the invoice is a pure projection of an Order row.

* Invoice number is DERIVED, never stored:
      WV-{Indian fiscal year, e.g. 2026-27}-{order.id zero-padded to 6}
  The fiscal year (April–March) is taken from ``order.created_at`` converted
  to IST, so the number is stable across re-downloads and app restarts.

* Seller identity (legal name, address, GSTIN, place-of-supply state code)
  comes from runtime settings keys (``store.legal_name``, ``store.address``,
  ``store.gstin``, ``store.state_code``) registered in
  ``app/services/settings_seed.py`` — admins fill them in the existing
  Settings UI. Everything degrades gracefully while they're blank.

* Per-line tax: checkout stores only the order-level ``tax_amount`` aggregate
  (see PaymentService._build_order — ``compute_line_tax`` per line, summed).
  We re-derive per-line figures the same way from the snapshotted
  ``unit_price`` and the product's tax config; when the recomputed sum still
  matches the stored ``order.tax_amount`` the real GST rates are shown.
  If rates changed since checkout, the STORED total stays authoritative and
  is allocated across lines proportionally to taxable value (rounding
  remainder on the last line), with effective rates shown.

* CGST/SGST vs IGST: same-state supply (seller state code == destination
  state code) splits each line's tax into equal CGST + SGST halves;
  inter-state renders IGST. If the seller state code is unset (or the
  destination state can't be mapped), the tax renders as a single GST line
  with an explanatory note on the invoice.

Mirrors the conventions of ``app/services/shipping_label_pdf.py``: a pure
dataclass + render function decoupled from the ORM (reportlab canvas,
Helvetica), plus a thin service that loads the Order and enforces access.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from io import BytesIO
from typing import Literal

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.models.order import Order, OrderStatus
from app.repositories.order_repository import OrderRepository
from app.services.settings_service import SettingsService
from app.services.tax_service import quantize_money

# Indian Standard Time — fiscal-year bucketing happens in the seller's zone.
IST = timezone(timedelta(hours=5, minutes=30))

# Order states that have a real supply/payment behind them. PENDING and
# CANCELLED orders never get a tax invoice (nothing was supplied/collected).
INVOICEABLE_STATUSES: frozenset[OrderStatus] = frozenset(
    {
        OrderStatus.PAID,
        OrderStatus.SHIPPED,
        OrderStatus.DELIVERED,
        OrderStatus.REFUNDED,
    }
)

# GST state codes (CBIC list). Keys are normalized state names — see
# `_normalize_state`. Includes common aliases/legacy spellings.
GST_STATE_CODES: dict[str, str] = {
    "jammu and kashmir": "01",
    "himachal pradesh": "02",
    "punjab": "03",
    "chandigarh": "04",
    "uttarakhand": "05",
    "uttaranchal": "05",
    "haryana": "06",
    "delhi": "07",
    "new delhi": "07",
    "rajasthan": "08",
    "uttar pradesh": "09",
    "bihar": "10",
    "sikkim": "11",
    "arunachal pradesh": "12",
    "nagaland": "13",
    "manipur": "14",
    "mizoram": "15",
    "tripura": "16",
    "meghalaya": "17",
    "assam": "18",
    "west bengal": "19",
    "jharkhand": "20",
    "odisha": "21",
    "orissa": "21",
    "chhattisgarh": "22",
    "chattisgarh": "22",
    "madhya pradesh": "23",
    "gujarat": "24",
    "dadra and nagar haveli and daman and diu": "26",
    "dadra and nagar haveli": "26",
    "daman and diu": "26",
    "maharashtra": "27",
    "andhra pradesh": "37",
    "karnataka": "29",
    "goa": "30",
    "lakshadweep": "31",
    "kerala": "32",
    "tamil nadu": "33",
    "tamilnadu": "33",
    "puducherry": "34",
    "pondicherry": "34",
    "andaman and nicobar islands": "35",
    "telangana": "36",
    "ladakh": "38",
}


def _normalize_state(name: str | None) -> str:
    return (
        " ".join((name or "").strip().lower().replace("&", "and").replace(".", " ").split())
    )


def state_code_for(state_name: str | None) -> str | None:
    """GST state code for a free-text state name, or None if unmappable."""
    return GST_STATE_CODES.get(_normalize_state(state_name))


def fiscal_year_for(created_at: datetime) -> str:
    """Indian fiscal year (Apr–Mar) label for a timestamp, e.g. "2026-27".

    Naive datetimes are assumed UTC (how TimestampMixin stores them), then
    converted to IST so the Mar-31/Apr-1 boundary lands on the Indian day.
    """
    dt = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(IST)
    start = dt.year if dt.month >= 4 else dt.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def invoice_number_for(order_id: int, created_at: datetime) -> str:
    """Derived, stable invoice reference — never persisted."""
    return f"WV-{fiscal_year_for(created_at)}-{order_id:06d}"


# --------------------------------------------------------------------------- #
# Render model — plain data, decoupled from the ORM                            #
# --------------------------------------------------------------------------- #


@dataclass
class InvoiceLine:
    description: str
    sku: str | None
    quantity: int
    unit_price: Decimal
    taxable_value: Decimal
    # Percent, e.g. Decimal("18") — the snapshot-consistent GST rate when the
    # recomputed tax matched the stored aggregate, otherwise the effective
    # rate implied by the proportional allocation. None when taxable is 0.
    tax_rate: Decimal | None
    tax_amount: Decimal

    @property
    def cgst(self) -> Decimal:
        return quantize_money(self.tax_amount / 2)

    @property
    def sgst(self) -> Decimal:
        # Remainder half — the pair always sums exactly to tax_amount.
        return self.tax_amount - self.cgst

    @property
    def total(self) -> Decimal:
        return self.taxable_value + self.tax_amount


@dataclass
class InvoiceData:
    """Everything the invoice renders. Built by InvoiceService from an Order +
    settings, so the render function stays ORM-free (same pattern as
    shipping_label_pdf.LabelData)."""

    invoice_number: str
    invoice_date: datetime
    order_number: str
    order_status: str
    payment_method: str
    # Seller (from runtime settings; blank strings while unconfigured)
    seller_legal_name: str
    seller_address: str
    seller_gstin: str
    seller_state_code: str
    place_of_supply: str
    # Buyer blocks — pre-rendered display lines
    bill_to: list[str] = field(default_factory=list)
    ship_to: list[str] = field(default_factory=list)
    # Lines + money
    lines: list[InvoiceLine] = field(default_factory=list)
    subtotal: Decimal = Decimal("0.00")
    discount_amount: Decimal = Decimal("0.00")
    payment_discount_amount: Decimal = Decimal("0.00")
    shipping_amount: Decimal = Decimal("0.00")
    cod_surcharge_amount: Decimal = Decimal("0.00")
    tax_amount: Decimal = Decimal("0.00")
    total_amount: Decimal = Decimal("0.00")
    currency: str = "INR"
    # "cgst_sgst" | "igst" | "single" — how the tax renders
    tax_mode: Literal["cgst_sgst", "igst", "single"] = "single"
    tax_note: str | None = None


# --------------------------------------------------------------------------- #
# PDF rendering (A4, reportlab canvas — mirrors shipping_label_pdf style)      #
# --------------------------------------------------------------------------- #

PAGE_W, PAGE_H = A4
_MARGIN = 14 * mm
_LEFT = _MARGIN
_RIGHT = PAGE_W - _MARGIN


def _wrap(text: str, max_chars: int, max_lines: int) -> list[str]:
    """Greedy word-wrap to a character budget, truncating with an ellipsis
    once `max_lines` is hit (same helper contract as shipping_label_pdf)."""
    words = (text or "").split()
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if len(cand) <= max_chars:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = w
            if len(lines) == max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and (words and " ".join(lines) != " ".join(words)):
        lines[-1] = (lines[-1][: max_chars - 2] + "..") if lines[-1] else ".."
    return lines or [""]


def _money(value: Decimal, currency: str = "INR") -> str:
    # Helvetica has no rupee glyph — same "Rs." convention as the shipping label.
    symbol = "Rs." if currency == "INR" else f"{currency} "
    v = quantize_money(Decimal(value))
    sign = "- " if v < 0 else ""
    return f"{sign}{symbol}{abs(v):,.2f}"


def _rate(value: Decimal | None) -> str:
    if value is None:
        return "—"
    v = Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = f"{v:f}".rstrip("0").rstrip(".")
    return f"{text or '0'}%"


# Item-table column layout, per tax mode: (header, width-weight, align)
_COLUMNS: dict[str, list[tuple[str, float, str]]] = {
    "cgst_sgst": [
        ("#", 0.5, "L"), ("Item", 5.4, "L"), ("Qty", 0.8, "R"),
        ("Unit price", 1.7, "R"), ("Taxable", 1.8, "R"), ("GST %", 1.0, "R"),
        ("CGST", 1.6, "R"), ("SGST", 1.6, "R"), ("Total", 1.9, "R"),
    ],
    "igst": [
        ("#", 0.5, "L"), ("Item", 6.6, "L"), ("Qty", 0.8, "R"),
        ("Unit price", 1.8, "R"), ("Taxable", 1.9, "R"), ("GST %", 1.1, "R"),
        ("IGST", 1.8, "R"), ("Total", 2.0, "R"),
    ],
    "single": [
        ("#", 0.5, "L"), ("Item", 6.9, "L"), ("Qty", 0.8, "R"),
        ("Unit price", 1.8, "R"), ("Taxable", 1.9, "R"), ("GST %", 1.1, "R"),
        ("GST", 1.8, "R"), ("Total", 2.0, "R"),
    ],
}


def _line_cells(idx: int, ln: InvoiceLine, mode: str, currency: str) -> list[str]:
    desc = ln.description + (f" ({ln.sku})" if ln.sku else "")
    common = [
        str(idx),
        desc,
        str(ln.quantity),
        _money(ln.unit_price, currency),
        _money(ln.taxable_value, currency),
        _rate(ln.tax_rate),
    ]
    if mode == "cgst_sgst":
        return common + [
            _money(ln.cgst, currency),
            _money(ln.sgst, currency),
            _money(ln.total, currency),
        ]
    return common + [_money(ln.tax_amount, currency), _money(ln.total, currency)]


def render_invoice_pdf(data: InvoiceData) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    mode = data.tax_mode
    columns = _COLUMNS[mode]
    weight_sum = sum(w for _h, w, _a in columns)
    inner_w = _RIGHT - _LEFT
    col_w = [inner_w * w / weight_sum for _h, w, _a in columns]

    def col_x(i: int) -> float:
        return _LEFT + sum(col_w[:i])

    def draw_cell(y: float, i: int, text: str, *, bold: bool = False, size: float = 8.5) -> None:
        font = "Helvetica-Bold" if bold else "Helvetica"
        c.setFont(font, size)
        pad = 3
        if columns[i][2] == "R":
            c.drawRightString(col_x(i) + col_w[i] - pad, y, text)
        else:
            c.drawString(col_x(i) + pad, y, text)

    def table_header(y: float) -> float:
        c.setLineWidth(0.7)
        c.line(_LEFT, y + 4, _RIGHT, y + 4)
        y -= 8
        for i, (header, _w, _a) in enumerate(columns):
            draw_cell(y, i, header, bold=True)
        y -= 5
        c.line(_LEFT, y, _RIGHT, y)
        return y - 11

    def new_page() -> float:
        c.showPage()
        c.setFont("Helvetica", 8)
        c.drawString(_LEFT, PAGE_H - _MARGIN, f"Tax Invoice {data.invoice_number} (contd.)")
        return table_header(PAGE_H - _MARGIN - 16)

    y = PAGE_H - _MARGIN

    # ---- Header: title + seller identity ----
    c.setFont("Helvetica-Bold", 15)
    c.drawString(_LEFT, y - 12, "TAX INVOICE")
    c.setFont("Helvetica", 8)
    c.drawRightString(_RIGHT, y - 6, "Original for recipient")
    y -= 26
    c.setFont("Helvetica-Bold", 11)
    c.drawString(_LEFT, y, data.seller_legal_name or "Seller name not configured")
    y -= 12
    c.setFont("Helvetica", 8.5)
    for ln in _wrap(data.seller_address or "Seller address not configured", 95, 3):
        c.drawString(_LEFT, y, ln)
        y -= 10
    if data.seller_gstin:
        c.drawString(_LEFT, y, f"GSTIN: {data.seller_gstin}")
        y -= 10
    if data.seller_state_code:
        c.drawString(_LEFT, y, f"State code: {data.seller_state_code}")
        y -= 10
    y -= 2
    c.setLineWidth(1)
    c.line(_LEFT, y, _RIGHT, y)

    # ---- Invoice meta (left) ----
    y -= 14
    meta_left = [
        ("Invoice no.", data.invoice_number),
        ("Invoice date", data.invoice_date.strftime("%d-%m-%Y")),
        ("Order no.", data.order_number),
        ("Payment", data.payment_method.upper()),
        ("Place of supply", data.place_of_supply or "—"),
    ]
    c.setFont("Helvetica", 8.5)
    meta_y = y
    for label, value in meta_left:
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(_LEFT, meta_y, f"{label}:")
        c.setFont("Helvetica", 8.5)
        c.drawString(_LEFT + 78, meta_y, value)
        meta_y -= 11

    # ---- Bill to / Ship to (right half, two columns) ----
    half = _LEFT + inner_w * 0.42
    block_w = (_RIGHT - half) / 2
    addr_y = y
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(half, addr_y, "BILL TO")
    c.drawString(half + block_w, addr_y, "SHIP TO")
    addr_y -= 11
    c.setFont("Helvetica", 8)
    max_chars = 34
    bill_lines = [w for line in data.bill_to for w in _wrap(line, max_chars, 2)][:7]
    ship_lines = [w for line in data.ship_to for w in _wrap(line, max_chars, 2)][:7]
    by, sy = addr_y, addr_y
    for ln in bill_lines:
        c.drawString(half, by, ln)
        by -= 10
    for ln in ship_lines:
        c.drawString(half + block_w, sy, ln)
        sy -= 10
    y = min(meta_y, by, sy) - 4

    if data.order_status == OrderStatus.REFUNDED.value:
        c.setFont("Helvetica-Bold", 9)
        c.drawString(_LEFT, y, "NOTE: this order has been REFUNDED.")
        y -= 12

    # ---- Item table ----
    y = table_header(y)
    for idx, ln in enumerate(data.lines, start=1):
        if y < _MARGIN + 90:
            y = new_page()
        cells = _line_cells(idx, ln, mode, data.currency)
        # Item description wraps within its column; the other cells sit on
        # the first row of the wrapped block.
        desc_lines = _wrap(cells[1], max(int(col_w[1] / 4.1), 12), 2)
        for i, text in enumerate(cells):
            draw_cell(y, i, desc_lines[0] if i == 1 else text)
        for extra in desc_lines[1:]:
            y -= 10
            draw_cell(y, 1, extra)
        y -= 13

    c.setLineWidth(0.7)
    c.line(_LEFT, y + 6, _RIGHT, y + 6)

    # ---- Totals block (right-aligned) ----
    if y < _MARGIN + 160:
        c.showPage()
        y = PAGE_H - _MARGIN - 16
        c.setFont("Helvetica", 8)
        c.drawString(_LEFT, PAGE_H - _MARGIN, f"Tax Invoice {data.invoice_number} (contd.)")

    totals: list[tuple[str, Decimal, bool]] = [("Taxable value", data.subtotal, False)]
    if data.discount_amount:
        totals.append(("Discount", -data.discount_amount, False))
    if data.payment_discount_amount:
        totals.append(("Payment-method discount", -data.payment_discount_amount, False))
    if mode == "cgst_sgst":
        # Sum the per-line halves so the block always reconciles with the
        # table (line taxes themselves always sum to order.tax_amount).
        totals.append(("CGST", sum((ln.cgst for ln in data.lines), Decimal("0.00")), False))
        totals.append(("SGST", sum((ln.sgst for ln in data.lines), Decimal("0.00")), False))
    elif mode == "igst":
        totals.append(("IGST", data.tax_amount, False))
    else:
        totals.append(("GST", data.tax_amount, False))
    if data.shipping_amount:
        totals.append(("Shipping", data.shipping_amount, False))
    if data.cod_surcharge_amount:
        totals.append(("COD surcharge", data.cod_surcharge_amount, False))
    totals.append(("Grand total", data.total_amount, True))

    label_x = _RIGHT - 200
    y -= 14
    for label, amount, bold in totals:
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 9.5 if bold else 8.5)
        c.drawString(label_x, y, label)
        c.drawRightString(_RIGHT, y, _money(amount, data.currency))
        if bold:
            c.setLineWidth(0.7)
            c.line(label_x, y + 10, _RIGHT, y + 10)
        y -= 13

    # ---- Notes + footer ----
    y -= 4
    c.setFont("Helvetica-Oblique", 8)
    if data.tax_note:
        for ln in _wrap(f"Note: {data.tax_note}", 110, 3):
            c.drawString(_LEFT, y, ln)
            y -= 10
    c.setFont("Helvetica", 7.5)
    c.drawString(
        _LEFT,
        _MARGIN + 10,
        "This is a computer-generated invoice and does not require a signature.",
    )
    c.drawRightString(
        _RIGHT,
        _MARGIN + 10,
        "Generated " + datetime.now(timezone.utc).strftime("%d-%m-%Y %H:%M UTC"),
    )

    c.showPage()
    c.save()
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Service — loads the Order, enforces access, builds InvoiceData               #
# --------------------------------------------------------------------------- #


class InvoiceService:
    def __init__(self, db: Session):
        self.db = db
        self.orders = OrderRepository(db)
        self.settings = SettingsService(db)

    # ---- Public API ----

    def invoice_pdf_for_user(self, user_id: int, order_id: int) -> tuple[bytes, str]:
        """Owner-only download. Missing order and someone else's order return
        the SAME NotFoundError (no IDOR / existence leak) — mirrors
        OrderService.get_for_user."""
        order = self.orders.get_with_items(order_id)
        if not order or order.user_id != user_id:
            raise NotFoundError("Order not found")
        return self._render(order)

    def invoice_pdf_for_admin(self, order_id: int) -> tuple[bytes, str]:
        order = self.orders.get_with_items(order_id)
        if not order:
            raise NotFoundError("Order not found")
        return self._render(order)

    # ---- Internals ----

    def _render(self, order: Order) -> tuple[bytes, str]:
        if order.status not in INVOICEABLE_STATUSES:
            raise ConflictError(
                f"Invoice isn't available while order #{order.id} is "
                f"{order.status.value.upper()} — it can be downloaded once the "
                "order is paid."
            )
        data = self.build_invoice_data(order)
        return render_invoice_pdf(data), f"invoice-{data.invoice_number}.pdf"

    def build_invoice_data(self, order: Order) -> InvoiceData:
        seller_legal_name = self.settings.get_raw("store.legal_name") or ""
        seller_address = self.settings.get_raw("store.address") or ""
        seller_gstin = self.settings.get_raw("store.gstin") or ""
        seller_state_code = (self.settings.get_raw("store.state_code") or "").strip()

        ship_snap = order.shipping_address_snapshot or {}
        # NULL billing snapshot means "same as shipping" (see Order model).
        bill_snap = order.billing_address_snapshot or ship_snap

        dest_state_name = ship_snap.get("state") or ""
        dest_code = state_code_for(dest_state_name)

        tax_note: str | None = None
        if not seller_state_code:
            tax_mode: Literal["cgst_sgst", "igst", "single"] = "single"
            tax_note = (
                "Seller GST state code is not configured — tax is shown as a "
                "single GST amount instead of a CGST/SGST or IGST split."
            )
        elif dest_code is None:
            tax_mode = "single"
            tax_note = (
                "The destination state could not be determined — tax is shown "
                "as a single GST amount instead of a CGST/SGST or IGST split."
            )
        elif dest_code == seller_state_code.zfill(2):
            tax_mode = "cgst_sgst"
        else:
            tax_mode = "igst"

        if dest_code:
            place_of_supply = f"{dest_state_name} ({dest_code})"
        else:
            place_of_supply = dest_state_name or "—"

        return InvoiceData(
            invoice_number=invoice_number_for(order.id, order.created_at),
            invoice_date=order.created_at,
            order_number=order.order_number or f"ORD{order.id}",
            order_status=order.status.value,
            payment_method=order.payment_method or "prepaid",
            seller_legal_name=seller_legal_name,
            seller_address=seller_address,
            seller_gstin=seller_gstin,
            seller_state_code=seller_state_code,
            place_of_supply=place_of_supply,
            bill_to=self._address_lines(bill_snap, order),
            ship_to=self._address_lines(ship_snap, order),
            lines=self._build_lines(order),
            subtotal=Decimal(order.subtotal or 0),
            discount_amount=Decimal(order.discount_amount or 0),
            payment_discount_amount=Decimal(order.payment_discount_amount or 0),
            shipping_amount=Decimal(order.shipping_amount or 0),
            cod_surcharge_amount=Decimal(order.cod_surcharge_amount or 0),
            tax_amount=Decimal(order.tax_amount or 0),
            total_amount=Decimal(order.total_amount or 0),
            currency=order.currency or "INR",
            tax_mode=tax_mode,
            tax_note=tax_note,
        )

    @staticmethod
    def _address_lines(snap: dict, order: Order) -> list[str]:
        """Display lines from an address snapshot; falls back to the legacy
        free-text `order.shipping_address` and the customer's account."""
        if snap:
            locality = ", ".join(
                p for p in [snap.get("city"), snap.get("state")] if p
            )
            if snap.get("pincode"):
                locality = f"{locality} - {snap['pincode']}" if locality else str(snap["pincode"])
            lines = [
                snap.get("full_name"),
                snap.get("line1"),
                snap.get("line2"),
                snap.get("landmark"),
                locality,
                f"Phone: {snap['phone']}" if snap.get("phone") else None,
            ]
            return [ln for ln in lines if ln]
        user = order.user
        fallback = [
            (user.full_name if user and user.full_name else None) or "Customer",
            order.shipping_address or "",
        ]
        return [ln for ln in fallback if ln]

    @staticmethod
    def _build_lines(order: Order) -> list[InvoiceLine]:
        """Per-line taxable value + tax.

        First recompute each line the exact way checkout did
        (``compute_line_tax`` over the product's CURRENT tax config, but the
        SNAPSHOTTED unit_price). If the recomputed sum matches the stored
        ``order.tax_amount``, those figures (with real GST rates) are used.
        Otherwise the stored aggregate is authoritative: it is allocated
        proportionally to taxable value, remainder on the last line, and the
        rate shown is the effective one.
        """
        from app.services.tax_service import compute_line_tax

        total_tax = quantize_money(Decimal(order.tax_amount or 0))
        recomputed: list[tuple[Decimal, Decimal | None, Decimal]] = []
        meta: list[tuple[str, str | None, int, Decimal]] = []
        recomputed_sum = Decimal("0.00")
        for item in order.items:
            taxable = quantize_money(Decimal(item.unit_price) * item.quantity)
            product = getattr(item, "product", None)
            taxes = list(product.taxes) if product is not None else []
            active = [t for t in taxes if t.is_active]
            rate = sum((Decimal(t.rate) for t in active), Decimal("0")) if active else None
            tax = compute_line_tax(Decimal(item.unit_price), item.quantity, taxes)
            recomputed.append((taxable, rate, tax))
            recomputed_sum += tax
            meta.append(
                (
                    (product.name if product is not None else None) or f"Product #{item.product_id}",
                    product.sku if product is not None else None,
                    item.quantity,
                    Decimal(item.unit_price),
                )
            )

        lines: list[InvoiceLine] = []
        if recomputed_sum == total_tax:
            # Tax config unchanged since checkout — real rates line up.
            for (taxable, rate, tax), (name, sku, qty, unit_price) in zip(recomputed, meta):
                lines.append(
                    InvoiceLine(
                        description=name,
                        sku=sku,
                        quantity=qty,
                        unit_price=unit_price,
                        taxable_value=taxable,
                        tax_rate=rate,
                        tax_amount=tax,
                    )
                )
            return lines

        # Rates drifted since checkout — the stored aggregate stays
        # authoritative. Allocate proportionally to taxable value.
        taxable_sum = sum((t for t, _r, _x in recomputed), Decimal("0.00"))
        allocated = Decimal("0.00")
        for i, ((taxable, _rate, _tax), (name, sku, qty, unit_price)) in enumerate(
            zip(recomputed, meta)
        ):
            if i == len(recomputed) - 1:
                tax = total_tax - allocated  # exact remainder on the last line
            elif taxable_sum > 0:
                tax = quantize_money(total_tax * taxable / taxable_sum)
            else:
                tax = Decimal("0.00")
            allocated += tax
            effective = (
                (tax / taxable * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if taxable > 0 and tax
                else None
            )
            lines.append(
                InvoiceLine(
                    description=name,
                    sku=sku,
                    quantity=qty,
                    unit_price=unit_price,
                    taxable_value=taxable,
                    tax_rate=effective,
                    tax_amount=tax,
                )
            )
        return lines
