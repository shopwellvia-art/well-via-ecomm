import { apiClient } from '@/services/apiClient.js';

export const settingsApi = {
  list: () => apiClient.get('/settings').then((r) => r.data),
  update: (updates) =>
    apiClient.patch('/settings', { updates }).then((r) => r.data),
  testEmail: (to) =>
    apiClient.post('/settings/test-email', { to }).then((r) => r.data),
  testSms: (to, body) =>
    apiClient.post('/settings/test-sms', { to, body }).then((r) => r.data),
  testStorage: () =>
    apiClient.post('/settings/test-storage', {}).then((r) => r.data),
};
