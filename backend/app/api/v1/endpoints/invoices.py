"""GST tax-invoice downloads.

Mounted under the ``/orders`` prefix (see app/api/v1/router.py):

* ``GET /orders/{order_id}/invoice`` — the authenticated OWNER of the order
  only. Someone else's order (and a missing order) both return the same 404 —
  no IDOR / existence leak. Available once the order has a real supply behind
  it (PAID / SHIPPED / DELIVERED / REFUNDED); PENDING and CANCELLED refuse
  with 409.
* ``GET /orders/admin/{order_id}/invoice`` — staff download, gated by the
  same ``orders.view_all`` permission as the other admin order reads
  (detail view, shipping label).

Both stream the derived PDF (no schema change — the invoice number is
computed from the order id + Indian fiscal year) as an attachment named
``invoice-<number>.pdf``. Read-only, so no audit rows — consistent with the
neighbouring admin GET endpoints (order detail, shipping-label).
"""
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_permission
from app.models.user import User
from app.services.invoice_service import InvoiceService

router = APIRouter()


def _pdf_response(pdf: bytes, filename: str) -> Response:
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Admin route first, mirroring orders.py — keeps /admin/... away from the
# customer route's dynamic segment.


@router.get("/admin/{order_id}/invoice")
def admin_order_invoice(
    order_id: int,
    _actor: User = Depends(require_permission("orders.view_all")),
    db: Session = Depends(get_db),
):
    """Streams the GST tax invoice for any order — staff-only."""
    pdf, filename = InvoiceService(db).invoice_pdf_for_admin(order_id)
    return _pdf_response(pdf, filename)


@router.get("/{order_id}/invoice")
def order_invoice(
    order_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Streams the GST tax invoice for the caller's OWN order."""
    pdf, filename = InvoiceService(db).invoice_pdf_for_user(user.id, order_id)
    return _pdf_response(pdf, filename)
