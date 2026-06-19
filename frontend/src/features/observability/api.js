import { apiClient } from '@/services/apiClient.js';

export const observabilityApi = {
  overview: ({ period = '24h' } = {}) =>
    apiClient
      .get('/observability/overview', { params: { period } })
      .then((r) => r.data),

  requests: ({ period = '24h', method, status, q, page = 1, page_size = 50 } = {}) =>
    apiClient
      .get('/observability/requests', {
        params: { period, method, status, q, page, page_size },
      })
      .then((r) => r.data),

  routes: ({ period = '24h', sort = 'avg' } = {}) =>
    apiClient
      .get('/observability/routes', { params: { period, sort } })
      .then((r) => r.data),

  slowQueries: ({ period = '24h', view = 'queries', page = 1, page_size = 50 } = {}) =>
    apiClient
      .get('/observability/slow-queries', { params: { period, view, page, page_size } })
      .then((r) => r.data),
};
