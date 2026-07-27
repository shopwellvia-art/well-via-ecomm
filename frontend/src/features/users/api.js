import { apiClient } from '@/services/apiClient.js';

export const usersApi = {
  list: ({ q, page = 1, page_size = 50 } = {}) => {
    const params = new URLSearchParams();
    if (q) params.set('q', q);
    params.set('page', String(page));
    params.set('page_size', String(page_size));
    return apiClient.get(`/users?${params.toString()}`).then((r) => r.data);
  },

  get: (id) => apiClient.get(`/users/${id}`).then((r) => r.data),

  // PATCH semantics — send only the fields being changed:
  // { full_name?: string|null, is_active?: boolean }. Requires users.manage.
  update: (id, data) => apiClient.patch(`/users/${id}`, data).then((r) => r.data),

  // Sends the standard forgot-password OTP email to the user. 202 {detail}.
  // The response never contains the code. Requires users.manage.
  triggerPasswordReset: (id) =>
    apiClient.post(`/users/${id}/password-reset`).then((r) => r.data),
};
