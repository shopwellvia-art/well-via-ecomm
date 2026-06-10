import { apiClient } from '@/services/apiClient.js';

export const addressApi = {
  list: () => apiClient.get('/addresses').then((r) => r.data),

  create: (data) => apiClient.post('/addresses', data).then((r) => r.data),

  update: (id, data) => apiClient.put(`/addresses/${id}`, data).then((r) => r.data),

  remove: (id) => apiClient.delete(`/addresses/${id}`),

  setDefault: (id) => apiClient.post(`/addresses/${id}/default`).then((r) => r.data),

  // Pincode auto-fill — proxied from api.postalpincode.in via backend.
  // Always resolves (never throws); { found: false } on miss/error.
  lookupPincode: (pincode) =>
    apiClient.get(`/shipping/pincode/${pincode}`).then((r) => r.data),
};
