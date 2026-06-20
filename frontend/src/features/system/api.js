import { apiClient } from '@/services/apiClient.js';

// Superadmin-only database maintenance. All endpoints sit behind require_admin
// on the backend; the UI also self-gates on is_admin.
export const systemApi = {
  // List the domain groups (Catalog, Orders, …, Everything) with row counts,
  // blast radius, confirm phrase and whether each can be re-seeded.
  listDatabaseGroups: () =>
    apiClient.get('/admin/database/groups').then((r) => r.data.groups),

  // Truncate one domain group (or the full database when scope === 'everything').
  // `confirm` must equal that scope's confirm phrase exactly.
  truncateScope: (scope, confirm) =>
    apiClient.post('/admin/database/truncate', { scope, confirm }).then((r) => r.data),

  // Load sample/dummy data into one domain group. Additive + idempotent.
  seedScope: (scope) =>
    apiClient.post('/admin/database/seed', { scope }).then((r) => r.data),

  // Back-compat shim: the original single-button full wipe.
  truncateDatabase: (confirm) =>
    apiClient
      .post('/admin/database/truncate', { scope: 'everything', confirm })
      .then((r) => r.data),
};
