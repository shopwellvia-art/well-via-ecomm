import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { addressApi } from './api.js';
import { useAuthStore } from '@/features/auth/store.js';

const ADDR_KEY = ['addresses'];

function useInvalidateAddresses() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: ADDR_KEY });
}

/** Fetch the signed-in user's saved addresses. */
export function useAddresses() {
  const token = useAuthStore((s) => s.accessToken);
  return useQuery({
    queryKey: ADDR_KEY,
    queryFn: addressApi.list,
    enabled: !!token,
    retry: false,
  });
}

export function useCreateAddress() {
  const invalidate = useInvalidateAddresses();
  return useMutation({
    mutationFn: (data) => addressApi.create(data),
    onSuccess: invalidate,
  });
}

export function useUpdateAddress() {
  const invalidate = useInvalidateAddresses();
  return useMutation({
    mutationFn: ({ id, data }) => addressApi.update(id, data),
    onSuccess: invalidate,
  });
}

export function useDeleteAddress() {
  const invalidate = useInvalidateAddresses();
  return useMutation({
    mutationFn: (id) => addressApi.remove(id),
    onSuccess: invalidate,
  });
}

export function useSetDefaultAddress() {
  const invalidate = useInvalidateAddresses();
  return useMutation({
    mutationFn: (id) => addressApi.setDefault(id),
    onSuccess: invalidate,
  });
}

/**
 * Auto-fill city/state from pincode.
 * Only fires when pincode matches the 6-digit Indian pin format.
 * Caches positives for 24h; never retries on failure.
 */
export function usePincodeLookup(pincode) {
  const clean = (pincode || '').trim();
  const enabled = /^[1-9][0-9]{5}$/.test(clean);
  return useQuery({
    queryKey: ['pincode', clean],
    queryFn: () => addressApi.lookupPincode(clean),
    enabled,
    staleTime: 24 * 60 * 60 * 1000, // 24h — postal data rarely changes
    retry: false,
  });
}
