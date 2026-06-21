import { apiClient } from '@/services/apiClient.js';

export const emailTemplatesApi = {
  /** GET /email-templates → { items: [...] } */
  list: () => apiClient.get('/email-templates').then((r) => r.data),

  /** GET /email-templates/{key} → detail object */
  get: (key) => apiClient.get(`/email-templates/${key}`).then((r) => r.data),

  /**
   * PUT /email-templates/{key}
   * body: { subject?, body_html?, body_design?, is_enabled? }
   * → returns the detail object
   */
  update: (key, payload) =>
    apiClient.put(`/email-templates/${key}`, payload).then((r) => r.data),

  /**
   * POST /email-templates/{key}/preview
   * body: { subject?, body_html? }  (unsaved draft; optional)
   * → { subject, html }
   */
  preview: (key, draft = {}) =>
    apiClient.post(`/email-templates/${key}/preview`, draft).then((r) => r.data),

  /**
   * POST /email-templates/{key}/test
   * body: { to }
   * → { detail }
   */
  test: (key, to) =>
    apiClient.post(`/email-templates/${key}/test`, { to }).then((r) => r.data),

  /**
   * POST /email-templates/{key}/reset
   * → returns the detail object
   */
  reset: (key) =>
    apiClient.post(`/email-templates/${key}/reset`).then((r) => r.data),
};
