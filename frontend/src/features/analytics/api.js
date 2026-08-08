import { apiClient } from '@/services/apiClient.js';

/**
 * Analytics data layer.
 *
 * The two legacy thunks stay exactly as they were — the old Sales and Profit
 * pages remain the operational source of truth until shadow-mode reconciliation
 * passes, so their contract must not move.
 *
 * The v2 surface is deliberately small: ONE call renders one view. There is no
 * per-chart endpoint, because composing several in the browser would push
 * resolver logic into JS and make view definitions client-executable.
 */
export const analyticsApi = {
  // ---- legacy (unchanged) -------------------------------------------------
  sales: ({ period = '30d', granularity = 'day' } = {}) =>
    apiClient
      .get('/analytics/sales', { params: { period, granularity } })
      .then((r) => r.data),

  profit: ({ period = '30d' } = {}) =>
    apiClient
      .get('/analytics/profit', { params: { period } })
      .then((r) => r.data),

  // ---- v2 -----------------------------------------------------------------

  /** Modules and views this user may see — already permission-filtered server-side. */
  navigation: () => apiClient.get('/analytics/modules').then((r) => r.data),

  module: (moduleSlug) =>
    apiClient.get(`/analytics/modules/${moduleSlug}`).then((r) => r.data),

  /** THE view call. `params` is the serialized filter set. */
  view: (moduleSlug, viewSlug, params = {}) =>
    apiClient
      .get(`/analytics/modules/${moduleSlug}/views/${viewSlug}`, { params })
      .then((r) => r.data),

  /** KPI catalogue — formula, treatment and tooltip text for every metric. */
  kpis: () => apiClient.get('/analytics/kpis').then((r) => r.data),

  /**
   * CSV export. `responseType: 'blob'` matches the existing invoice/label
   * download idiom in features/orders. Note that errors also arrive as a Blob,
   * which is why callers must use `readApiErrorMessage` rather than reading
   * `err.response.data.error.message` directly.
   */
  export: (payload, params = {}) =>
    apiClient.post('/analytics/exports', payload, { params, responseType: 'blob' }),

  /** Worker watermarks, queue depth, last error. Requires analytics.jobs.run. */
  health: () => apiClient.get('/analytics/admin/health').then((r) => r.data),
};
