import { apiClient } from '@/services/apiClient.js';

/**
 * The shopper directory (/admin/customers). Distinct from `features/users`,
 * which serves the STAFF directory — the two sit behind different permission
 * tiers (customers.* vs users.*) and must not share a client.
 */
export const customersApi = {
  list: ({
    q,
    status,
    segment,
    has_ordered,
    joined_from,
    joined_to,
    sort = 'recent',
    page = 1,
    page_size = 25,
  } = {}) => {
    const params = new URLSearchParams();
    if (q) params.set('q', q);
    if (status) params.set('status', status);
    if (segment) params.set('segment', segment);
    if (has_ordered != null) params.set('has_ordered', String(has_ordered));
    if (joined_from) params.set('joined_from', joined_from);
    if (joined_to) params.set('joined_to', joined_to);
    params.set('sort', sort);
    params.set('page', String(page));
    params.set('page_size', String(page_size));
    return apiClient.get(`/customers?${params.toString()}`).then((r) => r.data);
  },

  get: (id) => apiClient.get(`/customers/${id}`).then((r) => r.data),

  activity: (id, { kinds, page = 1, page_size = 50 } = {}) => {
    const params = new URLSearchParams();
    (kinds || []).forEach((k) => params.append('kinds', k));
    params.set('page', String(page));
    params.set('page_size', String(page_size));
    return apiClient
      .get(`/customers/${id}/activity?${params.toString()}`)
      .then((r) => r.data);
  },

  // PATCH semantics — send only what's changing. Requires customers.manage.
  update: (id, data) =>
    apiClient.patch(`/customers/${id}`, data).then((r) => r.data),

  // Sends the standard forgot-password OTP to the customer. 202 {detail};
  // the code is never in the response.
  triggerPasswordReset: (id) =>
    apiClient.post(`/customers/${id}/password-reset`).then((r) => r.data),
};
