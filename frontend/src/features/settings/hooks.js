import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { settingsApi } from './api.js';

const KEY = ['settings'];

export function useSettings() {
  return useQuery({
    queryKey: KEY,
    queryFn: settingsApi.list,
  });
}

export function useUpdateSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (updates) => settingsApi.update(updates),
    onSuccess: (data) => {
      qc.setQueryData(KEY, data);
      // /auth/config exposes totp_enabled_system_wide which depends on a
      // settings row — bump it so the login page picks up changes.
      qc.invalidateQueries({ queryKey: ['auth-config'] });
    },
  });
}

export function useTestEmail() {
  return useMutation({ mutationFn: (to) => settingsApi.testEmail(to) });
}

export function useTestSms() {
  return useMutation({
    mutationFn: ({ to, body }) => settingsApi.testSms(to, body),
  });
}

export function useTestStorage() {
  return useMutation({ mutationFn: () => settingsApi.testStorage() });
}
