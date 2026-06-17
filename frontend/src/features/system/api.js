import { apiClient } from '@/services/apiClient.js';

export const systemApi = {
  // Superadmin-only. Wipes every table then re-seeds the bootstrap admin.
  // `confirm` must equal the phrase the backend expects ("DELETE EVERYTHING").
  truncateDatabase: (confirm) =>
    apiClient.post('/admin/database/truncate', { confirm }).then((r) => r.data),
};
