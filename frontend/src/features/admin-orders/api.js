import { apiClient } from '@/services/apiClient.js';

export const adminOrdersApi = {
  list: ({ q, status, date_from, date_to, page = 1, page_size = 25 } = {}) => {
    const params = { page, page_size };
    if (q) params.q = q;
    if (status) params.status = status;
    if (date_from) params.date_from = date_from;
    if (date_to) params.date_to = date_to;
    return apiClient.get('/orders/admin', { params }).then((r) => r.data);
  },
  get: (id) => apiClient.get(`/orders/admin/${id}`).then((r) => r.data),
  ship: (id, data) =>
    apiClient.post(`/orders/admin/${id}/ship`, data).then((r) => r.data),
  deliver: (id) =>
    apiClient.post(`/orders/admin/${id}/deliver`, {}).then((r) => r.data),
  cancel: (id, reason) =>
    apiClient
      .post(`/orders/admin/${id}/cancel`, { reason })
      .then((r) => r.data),
  refund: (id, reason) =>
    apiClient
      .post(`/orders/admin/${id}/refund`, { reason })
      .then((r) => r.data),
  updateNotes: (id, internal_notes) =>
    apiClient
      .patch(`/orders/admin/${id}/notes`, { internal_notes })
      .then((r) => r.data),
  pushToCarrier: (id) =>
    apiClient
      .post(`/orders/admin/${id}/push-to-carrier`, {})
      .then((r) => r.data),
  schedulePickup: (id, { pickup_date, expected_package_count = 1 }) =>
    apiClient
      .post(`/orders/admin/${id}/schedule-pickup`, {
        pickup_date,
        expected_package_count,
      })
      .then((r) => r.data),
  // Returns the raw PDF blob — caller turns it into a download / new tab.
  fetchLabel: (id) =>
    apiClient
      .get(`/orders/admin/${id}/shipping-label`, { responseType: 'blob' })
      .then((r) => r.data),
  // In-house generated 4x6 label PDF (works for any order, no live carrier needed).
  fetchLocalLabel: (id) =>
    apiClient
      .get(`/orders/admin/${id}/label-local`, { responseType: 'blob' })
      .then((r) => r.data),
  syncTracking: (id) =>
    apiClient
      .post(`/orders/admin/${id}/sync-tracking`, {})
      .then((r) => r.data),
  cancelShipment: (id) =>
    apiClient
      .post(`/orders/admin/${id}/cancel-shipment`, {})
      .then((r) => r.data),
  // Dev-only: mock simulator. Only useful when shipping.provider=mock.
  mockSimulate: (awb, status, note = null) =>
    apiClient
      .post('/shipping/mock/simulate', null, {
        params: { awb, status, ...(note ? { note } : {}) },
      })
      .then((r) => r.data),
};
