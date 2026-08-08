import { apiClient } from '@/services/apiClient.js';

export const ordersApi = {
  // Customer
  listMine: () => apiClient.get('/orders').then((r) => r.data),
  getMine: (id) => apiClient.get(`/orders/${id}`).then((r) => r.data),

  // Self-service cancellation. Body is optional — a blank reason defaults
  // server-side to "Cancelled by customer".
  cancel: (id, reason) =>
    apiClient
      .post(
        `/orders/${id}/cancel`,
        reason && reason.trim() ? { reason: reason.trim() } : {},
      )
      .then((r) => r.data),

  // GST invoice PDF. Must go through apiClient (Authorization header), so a
  // bare <a href> cannot work — we fetch the blob and return the full axios
  // response: headers carry the filename via Content-Disposition.
  invoice: (id) =>
    apiClient.get(`/orders/${id}/invoice`, { responseType: 'blob' }),
};

// ── Status rules (mirror backend) ────────────────────────────────────────────

// Backend invoices.py: PENDING / CANCELLED orders 409 — no invoice exists.
const INVOICEABLE_STATUSES = new Set(['paid', 'shipped', 'delivered', 'refunded']);

export function hasInvoice(order) {
  return INVOICEABLE_STATUSES.has(order?.status);
}

// Shipment rows still on our side of the carrier hand-off
// (OrderService._carrier_engaged treats anything else as "with the courier").
const PRE_CARRIER_SHIPMENT_STATUSES = new Set([
  'pending',
  'ready_to_ship',
  'cancelled',
]);

/**
 * Mirrors OrderService.cancel_for_customer: cancellable while PENDING, or
 * PAID with no carrier-side identity yet (no AWB / tracking / pickup and no
 * shipment row advanced past the pre-carrier states). The backend re-checks
 * and 409s if we guess wrong — this only gates button visibility.
 */
export function isOrderCancellable(order) {
  if (!order) return false;
  if (order.status === 'pending') return true;
  if (order.status !== 'paid') return false;
  if (
    order.shipping_awb ||
    order.tracking_number ||
    order.pickup_id ||
    order.shipment_created_at
  ) {
    return false;
  }
  for (const s of order.shipments ?? []) {
    if (s.awb_number || s.tracking_number) return false;
    if (!PRE_CARRIER_SHIPMENT_STATUSES.has(s.shipment_status)) return false;
  }
  return true;
}

// ── Error helper ─────────────────────────────────────────────────────────────

/**
 * Extract the backend error-envelope message from an axios error. Handles the
 * blob case: with responseType 'blob' the error body arrives as a Blob that
 * must be read + parsed before the envelope is visible.
 */
export async function readApiErrorMessage(err, fallback) {
  const data = err?.response?.data;
  if (typeof Blob !== 'undefined' && data instanceof Blob) {
    try {
      return JSON.parse(await data.text())?.error?.message || fallback;
    } catch {
      return fallback;
    }
  }
  return data?.error?.message || fallback;
}
