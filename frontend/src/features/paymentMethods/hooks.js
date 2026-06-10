import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { paymentMethodsApi } from './api.js';

/** Query key for the admin list. */
const ADMIN_LIST_KEY = ['paymentMethods', 'admin', 'list'];

/** Query key for the public active list. */
const ACTIVE_LIST_KEY = ['paymentMethods', 'public', 'active'];

/**
 * Admin: full list of all configured payment gateways.
 * Stale for 30s — credentials state only changes when an admin edits.
 */
export function usePaymentMethods() {
  return useQuery({
    queryKey: ADMIN_LIST_KEY,
    queryFn: paymentMethodsApi.list,
    staleTime: 30 * 1000,
  });
}

/**
 * Admin: update a gateway's enabled state, environment, and/or credentials.
 * On success the list is invalidated so every row reflects the fresh server state.
 *
 * Returns the standard TanStack Query mutation object.
 * Usage: const mutation = useUpdatePaymentMethod();
 *        await mutation.mutateAsync({ code: 'razorpay', payload: { enabled: true } })
 */
export function useUpdatePaymentMethod() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ code, payload }) => paymentMethodsApi.update(code, payload),
    onSuccess: () => {
      // Invalidate the whole list so every row re-fetches the latest state,
      // including the row that was just updated.
      qc.invalidateQueries({ queryKey: ADMIN_LIST_KEY });
    },
  });
}

/**
 * Public: enabled+implemented+ready gateways sorted by sort_order.
 * Used by CheckoutPage to render the "Pay via" gateway selector.
 * Long stale time — this only changes when an admin edits something.
 */
export function useActivePaymentMethods() {
  return useQuery({
    queryKey: ACTIVE_LIST_KEY,
    queryFn: paymentMethodsApi.listActive,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
}
