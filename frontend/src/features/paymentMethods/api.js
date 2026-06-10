import { apiClient } from '@/services/apiClient.js';

export const paymentMethodsApi = {
  /**
   * Admin list — returns all 25+ gateways with their config, credentials
   * metadata, and readiness flags.
   * Requires: payments.manage
   */
  list: () =>
    apiClient.get('/admin/payment-methods').then((r) => r.data),

  /**
   * Update a gateway's enabled state, environment, and/or credentials.
   * `code`    — gateway identifier (e.g. "razorpay")
   * `payload` — partial: { enabled?, environment?, credentials? }
   *
   * Credentials is a partial merge — only send the keys the admin actually
   * edited (dirty tracking). Send "" to clear a key.
   * Server returns the updated item on success; raises 422 on validation
   * failures (e.g. enabling with missing required keys).
   */
  update: (code, payload) =>
    apiClient
      .put(`/admin/payment-methods/${code}`, payload)
      .then((r) => r.data),

  /**
   * Public endpoint — returns only enabled + implemented + ready gateways,
   * sorted by sort_order. Used by the checkout page gateway selector.
   */
  listActive: () =>
    apiClient.get('/payment-methods/active').then((r) => r.data),
};
