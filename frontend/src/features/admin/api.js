import { apiClient } from '@/services/apiClient.js';

/** Admin-only product mutations. The server enforces require_admin on each. */
export const adminApi = {
  createProduct: (data) => apiClient.post('/products', data).then((r) => r.data),
  updateProduct: (id, data) =>
    apiClient.patch(`/products/${id}`, data).then((r) => r.data),
  deleteProduct: (id) => apiClient.delete(`/products/${id}`),

  // Image management — all return the updated product (with its images).
  uploadProductImages: (id, files) => {
    const form = new FormData();
    for (const file of files) form.append('files', file);
    // Image uploads need more headroom than the default 15s API timeout.
    return apiClient
      .post(`/products/${id}/images`, form, { timeout: 60000 })
      .then((r) => r.data);
  },
  deleteProductImage: (id, imageId) =>
    apiClient.delete(`/products/${id}/images/${imageId}`).then((r) => r.data),
  setPrimaryImage: (id, imageId) =>
    apiClient.post(`/products/${id}/images/${imageId}/primary`).then((r) => r.data),
  // Full gallery order, first id first — the server rejects partial lists.
  reorderProductImages: (id, imageIds) =>
    apiClient
      .patch(`/products/${id}/images/order`, { image_ids: imageIds })
      .then((r) => r.data),
};
