import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { usersApi } from './api.js';

export function useUsers({ q = '', scope, page = 1, page_size = 50 } = {}) {
  return useQuery({
    queryKey: ['users', { q, scope, page, page_size }],
    queryFn: () => usersApi.list({ q, scope, page, page_size }),
    placeholderData: keepPreviousData,
  });
}

export function useUser(id) {
  return useQuery({
    queryKey: ['users', id],
    queryFn: () => usersApi.get(id),
    enabled: id != null,
  });
}

/** Staff edit of a user (users.manage): full_name and/or is_active. */
export function useUpdateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, data }) => usersApi.update(userId, data),
    // Invalidating the bare prefix covers both the paginated list
    // (['users', {q,page,...}]) and any single-user query (['users', id]).
    onSuccess: () => qc.invalidateQueries({ queryKey: ['users'] }),
  });
}

/** Admin-triggered password reset email (users.manage). No cache impact. */
export function useTriggerPasswordReset() {
  return useMutation({
    mutationFn: (userId) => usersApi.triggerPasswordReset(userId),
  });
}

/** Invite a new staff account by email (users.invite). */
export function useInviteStaff() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data) => usersApi.invite(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['users'] }),
  });
}
