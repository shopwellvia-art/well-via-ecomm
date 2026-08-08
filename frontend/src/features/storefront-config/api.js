import { apiClient } from '@/services/apiClient.js';

export const storefrontConfigApi = {
  get: () => apiClient.get('/storefront-config').then((r) => r.data),
  update: (data) => apiClient.put('/storefront-config', data).then((r) => r.data),
  uploadImage: (file) => {
    const form = new FormData();
    form.append('file', file);
    return apiClient
      .post('/storefront-config/image', form, { timeout: 60000 })
      .then((r) => r.data);
  },
};
