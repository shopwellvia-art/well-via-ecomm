import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { systemApi } from './api.js';

const GROUPS_KEY = ['admin', 'database', 'groups'];

// Superadmin-only. Lists the domain groups + their current row counts.
export function useDatabaseGroups() {
  return useQuery({
    queryKey: GROUPS_KEY,
    queryFn: systemApi.listDatabaseGroups,
    staleTime: 0,
  });
}

// Truncate a domain group (or 'everything'). Refreshes the group row counts on
// success so the panel reflects the now-empty tables.
export function useTruncateScope() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ scope, confirm }) => systemApi.truncateScope(scope, confirm),
    onSuccess: () => qc.invalidateQueries({ queryKey: GROUPS_KEY }),
  });
}

// Seed sample data into a domain group, then refresh row counts.
export function useSeedScope() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (scope) => systemApi.seedScope(scope),
    onSuccess: () => qc.invalidateQueries({ queryKey: GROUPS_KEY }),
  });
}

// Kept for back-compat with any caller of the original full-wipe mutation.
export function useTruncateDatabase() {
  return useMutation({
    mutationFn: (confirm) => systemApi.truncateDatabase(confirm),
  });
}
