import { apiClient } from '@/services/apiClient.js';

export const searchApi = {
  /**
   * Products + categories matching `q`, grouped and relevance-ranked.
   *
   * `limit` caps each group, NOT the total — the response's `product_total`
   * still reports every match so the dropdown can offer "See all 43 results".
   */
  global: (q, limit = 6) =>
    apiClient.get('/search', { params: { q, limit } }).then((r) => r.data),
};
