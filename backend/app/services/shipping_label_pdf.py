"""Local shipping-label PDF generator — a 4"x6" thermal label in the DTDC style.

This renders a printable shipping label from OUR OWN order data, so labels can be
produced WITHOUT a live carrier account (e.g. with the mock provider, or before
DTDC onboarding completes). For real DTDC shipments the carrier's own label
(fetched via `shippinglabel/stream`) stays the source of truth — this is the
in-house equivalent / fallback / preview.

We deliberately do NOT fabricate DTDC's internal artefacts (the big route code,
ORG/DST office codes, the long routing barcode) — those only exist after DTDC
processes the consignment. We render the fields we genuinely own: sender,
consignee, an AWB-or-order-number Code128 barcode, payment mode, pieces, weight
and date.

reportlab is a pure-Python PDF toolkit and ships Code128 support in
`reportlab.graphics.barcode`, so no extra barcode dependency is needed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO

from reportlab.graphics.barcode import code128
from reportlab.lib.units import inch, mm
from reportlab.pdfgen import canvas

# 4" x 6" thermal label (the DTDC SHIP_LABEL_4X6 size), in points (72 pt = 1").
LABEL_W = 4 * inch
LABEL_H = 6 * inch
_MARGIN = 8


@dataclass
class LabelData:
    """Everything the label renders. Built by the service from an Order +
    warehouse settings, so this module stays decoupled from the ORM."""

    store_name: str
    order_number: str
    awb: str | None
    created_at: datetime
    payment_label: str          # e.g. "PREPAID" or "COD ₹1,234.00"
    cod: bool
    pieces: int
    weight_grams: int
    # Consignee (TO)
    to_name: str
    to_address: str
    to_city: str | None
    to_state: str | None
    to_pincode: str | None
    to_phone: str | None
    # Sender (FROM)
    from_name: str
    from_address: str
    from_pincode: str | None
    from_phone: str | None


def _wrap(text: str, max_chars: int, max_lines: int) -> list[str]:
    """Greedy word-wrap to a character budget, truncating with an ellipsis once
    `max_lines` is hit (labels are fixed-height, so overflow must be bounded)."""
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


def _barcode(value: str, *, height_mm: float) -> code128.Code128:
    """Build a Code128 sized to fit the label width. We shrink the bar width
    until it fits the printable area so long AWBs don't run off the edge."""
    avail = LABEL_W - 2 * (_MARGIN + 6)
    for bw in (0.46, 0.40, 0.34, 0.28, 0.24, 0.20):
        bc = code128.Code128(value, barHeight=height_mm * mm, barWidth=bw * mm)
        if bc.width <= avail:
            return bc
    return code128.Code128(value, barHeight=height_mm * mm, barWidth=0.18 * mm)


def _draw_centered_barcode(c: canvas.Canvas, value: str, y: float, *, height_mm: float) -> None:
    bc = _barcode(value, height_mm=height_mm)
    bc.drawOn(c, (LABEL_W - bc.width) / 2.0, y)


def render_label_pdf(data: LabelData) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(LABEL_W, LABEL_H))
    left = _MARGIN + 6
    right = LABEL_W - _MARGIN - 6
    inner_w = right - left

    # Outer border.
    c.setLineWidth(1)
    c.rect(_MARGIN, _MARGIN, LABEL_W - 2 * _MARGIN, LABEL_H - 2 * _MARGIN)

    y = LABEL_H - _MARGIN - 4

    # ---- Header: store name (left) + payment badge (right) ----
    y -= 16
    c.setFont("Helvetica-Bold", 13)
    c.drawString(left, y, (data.store_name or "Shipping label")[:24])
    badge = data.payment_label or ("COD" if data.cod else "PREPAID")
    c.setFont("Helvetica-Bold", 9)
    bw = c.stringWidth(badge, "Helvetica-Bold", 9)
    c.rect(right - bw - 8, y - 3, bw + 8, 14, stroke=1, fill=0)
    c.drawString(right - bw - 4, y, badge)
    y -= 6
    c.line(_MARGIN, y, LABEL_W - _MARGIN, y)

    # ---- FROM (sender / warehouse) ----
    y -= 13
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(left, y, "FROM:")
    c.setFont("Helvetica", 7.5)
    from_bits = [data.from_name, data.from_address]
    if data.from_pincode:
        from_bits.append(f"PIN: {data.from_pincode}")
    if data.from_phone:
        from_bits.append(f"Ph: {data.from_phone}")
    for ln in _wrap(", ".join(b for b in from_bits if b), 62, 3):
        y -= 9
        c.drawString(left, y, ln)
    y -= 6
    c.line(_MARGIN, y, LABEL_W - _MARGIN, y)

    # ---- TO (consignee) — the prominent block ----
    y -= 15
    c.setFont("Helvetica-Bold", 9)
    c.drawString(left, y, "TO:")
    y -= 13
    c.setFont("Helvetica-Bold", 11)
    c.drawString(left, y, (data.to_name or "Customer")[:34])
    c.setFont("Helvetica", 8.5)
    for ln in _wrap(data.to_address, 50, 3):
        y -= 11
        c.drawString(left, y, ln)
    locality = ", ".join(b for b in [data.to_city, data.to_state] if b)
    if locality:
        y -= 11
        c.drawString(left, y, locality[:50])
    if data.to_phone:
        y -= 11
        c.drawString(left, y, f"Phone: {data.to_phone}")
    # Big destination PIN (mirrors the large pincode on the DTDC sample).
    if data.to_pincode:
        y -= 22
        c.setFont("Helvetica-Bold", 20)
        c.drawString(left, y, str(data.to_pincode))
    y -= 8
    c.line(_MARGIN, y, LABEL_W - _MARGIN, y)

    # ---- AWB / reference barcode ----
    code_value = data.awb or data.order_number
    y -= 12
    c.setFont("Helvetica-Bold", 8)
    label_txt = "AWB" if data.awb else "ORDER"
    c.drawString(left, y, f"{label_txt}: {code_value}")
    y -= 46
    _draw_centered_barcode(c, code_value, y, height_mm=13)
    y -= 4

    # ---- Meta row: order #, date, pieces, weight ----
    y -= 12
    c.line(_MARGIN, y + 6, LABEL_W - _MARGIN, y + 6)
    c.setFont("Helvetica", 8)
    c.drawString(left, y, f"Order: {data.order_number}")
    created = data.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    c.drawRightString(right, y, created.astimezone(timezone.utc).strftime("%d-%m-%Y"))
    y -= 11
    weight_kg = max(data.weight_grams, 0) / 1000.0
    c.drawString(left, y, f"Pcs: {data.pieces:03d}")
    c.drawRightString(right, y, f"Weight: {weight_kg:.3f} kg")
    if data.cod:
        y -= 11
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(LABEL_W / 2.0, y, data.payment_label)
    else:
        y -= 11
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(LABEL_W / 2.0, y, "PREPAID - Do not collect cash")

    # ---- Bottom routing barcode + footer ----
    _draw_centered_barcode(c, code_value, _MARGIN + 34, height_mm=12)
    c.setFont("Helvetica", 6.5)
    c.drawCentredString(LABEL_W / 2.0, _MARGIN + 26, code_value)
    c.line(_MARGIN, _MARGIN + 20, LABEL_W - _MARGIN, _MARGIN + 20)
    c.setFont("Helvetica", 6.5)
    c.drawString(left, _MARGIN + 11, f"Ref: {data.order_number}")
    c.drawRightString(
        right,
        _MARGIN + 11,
        "Generated " + datetime.now(timezone.utc).strftime("%d-%m-%Y %H:%M UTC"),
    )

    c.showPage()
    c.save()
    return buf.getvalue()
