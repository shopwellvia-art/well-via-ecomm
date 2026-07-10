import { apiClient } from '@/services/apiClient.js';

export const contactApi = {
  // POST /contact — { name, email, phone?, subject?, message } → 201 + saved row.
  submitMessage: (data) => apiClient.post('/contact', data).then((r) => r.data),

  // POST /newsletter/subscribe — { email } → 204 No Content.
  subscribeNewsletter: (email) =>
    apiClient.post('/newsletter/subscribe', { email }),
};
