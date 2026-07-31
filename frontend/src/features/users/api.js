import { apiClient } from '@/services/apiClient.js';

export const usersApi = {
  // `scope: 'staff'` narrows to accounts that hold admin access — the Team
  // page. Omitted, the endpoint keeps its pre-split "every user" behaviour.
  list: ({ q, scope, page = 1, page_size = 50 } = {}) => {
    const params = new URLSearchParams();
    if (q) params.set('q', q);
    if (scope) params.set('scope', scope);
    params.set('page', String(page));
    params.set('page_size', String(page_size));
    return apiClient.get(`/users?${params.toString()}`).then((r) => r.data);
  },

  // Creates a staff account and emails it a set-password code. Requires
  // users.invite. Roles pass through the same escalation guard as any other
  // grant, so this can 403 on the roles rather than the invite itself.
  invite: ({ email, full_name, role_ids = [] }) =>
    apiClient
      .post('/users/invite', { email, full_name, role_ids })
      .then((r) => r.data),

  get: (id) => apiClient.get(`/users/${id}`).then((r) => r.data),

  // PATCH semantics — send only the fields being changed:
  // { full_name?: string|null, is_active?: boolean }. Requires users.manage.
  update: (id, data) => apiClient.patch(`/users/${id}`, data).then((r) => r.data),

  // Sends the standard forgot-password OTP email to the user. 202 {detail}.
  // The response never contains the code. Requires users.manage.
  triggerPasswordReset: (id) =>
    apiClient.post(`/users/${id}/password-reset`).then((r) => r.data),
};
