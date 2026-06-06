import { apiClient } from '@/services/apiClient.js';

export const analyticsApi = {
  sales: ({ period = '30d', granularity = 'day' } = {}) =>
    apiClient
      .get('/analytics/sales', { params: { period, granularity } })
      .then((r) => r.data),
};
