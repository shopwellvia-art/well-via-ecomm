import { apiClient } from '@/services/apiClient.js';

export const productsApi = {
  list: (params = {}) =>
    apiClient.get('/products', { params }).then((r) => r.data),
  get: (id) => apiClient.get(`/products/${id}`).then((r) => r.data),
  // Admin edit form only. The public `get` above returns `ProductRead`, which
  // deliberately omits reorder_point / shelf_life_days — loading the form from
  // it would blank those inputs and the next save would clear the stored
  // values. Requires `products.update`.
  getForAdmin: (id) => apiClient.get(`/products/${id}/admin`).then((r) => r.data),
  related: (id, limit = 8) =>
    apiClient.get(`/products/${id}/related`, { params: { limit } }).then((r) => r.data),
  coPurchased: (id, limit = 12) =>
    apiClient.get(`/products/${id}/co-purchased`, { params: { limit } }).then((r) => r.data),
  likely: (id, limit = 12) =>
    apiClient.get(`/products/${id}/likely`, { params: { limit } }).then((r) => r.data),
  byIds: (ids) =>
    apiClient
      .get('/products/by-ids/batch', { params: { ids: ids.join(',') } })
      .then((r) => r.data),
  bestsellers: (limit = 8) =>
    apiClient.get('/products/bestsellers', { params: { limit } }).then((r) => r.data),
};
