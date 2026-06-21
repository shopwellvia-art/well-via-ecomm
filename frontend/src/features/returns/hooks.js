import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { returnsApi } from './api.js';

const MY_LIST = ['returns', 'mine'];
const ADMIN_LIST = (status) => ['returns', 'admin', status || 'all'];
const ADMIN_DETAIL = (id) => ['returns', 'admin', 'detail', id];

function useInvalidate() {
  const qc = useQueryClient();
  return (id) => {
    qc.invalidateQueries({ queryKey: ['returns'] });
    if (id != null) qc.invalidateQueries({ queryKey: ADMIN_DETAIL(id) });
  };
}

// Customer

export function useMyReturns() {
  return useQuery({ queryKey: MY_LIST, queryFn: returnsApi.listMine, retry: false });
}

export function useCreateReturn() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (payload) => returnsApi.create(payload),
    onSuccess: () => invalidate(),
  });
}

export function useCancelReturn() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id) => returnsApi.cancel(id),
    onSuccess: () => invalidate(),
  });
}

// Admin

export function useAdminReturns(status) {
  return useQuery({
    queryKey: ADMIN_LIST(status),
    queryFn: () => returnsApi.adminList(status),
  });
}

export function useAdminReturn(id) {
  return useQuery({
    queryKey: ADMIN_DETAIL(id),
    queryFn: () => returnsApi.adminGet(id),
    enabled: id != null,
  });
}

export function useApproveReturn() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, ...body }) => returnsApi.adminApprove(id, body),
    onSuccess: (_d, vars) => invalidate(vars.id),
  });
}

export function useRejectReturn() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, ...body }) => returnsApi.adminReject(id, body),
    onSuccess: (_d, vars) => invalidate(vars.id),
  });
}

export function useMarkReturnPickedUp() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id) => returnsApi.adminMarkPickedUp(id),
    onSuccess: (_d, id) => invalidate(id),
  });
}

export function useMarkReturnReceived() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id) => returnsApi.adminMarkReceived(id),
    onSuccess: (_d, id) => invalidate(id),
  });
}

export function useInspectReturn() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, ...body }) => returnsApi.adminInspect(id, body),
    onSuccess: (_d, vars) => invalidate(vars.id),
  });
}

export function useMarkReturnRefunded() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id) => returnsApi.adminMarkRefunded(id),
    onSuccess: (_d, id) => invalidate(id),
  });
}
