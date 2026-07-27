import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { paymentsApi } from './api.js';

export function useCheckout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: paymentsApi.checkout,
    onSuccess: () => {
      // Cart will be cleared server-side once payment succeeds, but the order
      // we just created already reserved stock — invalidate so listings refresh.
      qc.invalidateQueries({ queryKey: ['products'] });
    },
  });
}

export function usePaymentStatus(mtid, { enabled = true, refetchInterval } = {}) {
  return useQuery({
    queryKey: ['payment-status', mtid],
    queryFn: () => paymentsApi.status(mtid),
    enabled: !!mtid && enabled,
    refetchInterval,
    // Bounded retry so a single network blip doesn't permanently strand the
    // post-payment page; polling via refetchInterval continues regardless.
    retry: 3,
  });
}

export function useMockDecision() {
  return useMutation({
    mutationFn: ({ mtid, action }) => paymentsApi.mockDecision(mtid, action),
  });
}
