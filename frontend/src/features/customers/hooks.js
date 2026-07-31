import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { customersApi } from './api.js';

export function useCustomers(params = {}) {
  return useQuery({
    queryKey: ['customers', params],
    queryFn: () => customersApi.list(params),
    // Filter changes shouldn't blank the table while the next page loads.
    placeholderData: keepPreviousData,
  });
}

export function useCustomer(id) {
  return useQuery({
    queryKey: ['customers', id],
    queryFn: () => customersApi.get(id),
    enabled: id != null,
  });
}

export function useCustomerActivity(id, { kinds, page = 1, page_size = 50 } = {}) {
  return useQuery({
    queryKey: ['customers', id, 'activity', { kinds, page, page_size }],
    queryFn: () => customersApi.activity(id, { kinds, page, page_size }),
    enabled: id != null,
    placeholderData: keepPreviousData,
  });
}

/** Support edit of a shopper account (customers.manage). */
export function useUpdateCustomer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ customerId, data }) => customersApi.update(customerId, data),
    // The bare prefix covers the list, the detail and the activity feed — the
    // last of these because an account edit writes an audit row that shows up
    // on the timeline.
    onSuccess: () => qc.invalidateQueries({ queryKey: ['customers'] }),
  });
}

/** Admin-triggered password reset email (customers.manage). No cache impact. */
export function useTriggerCustomerPasswordReset() {
  return useMutation({
    mutationFn: (customerId) => customersApi.triggerPasswordReset(customerId),
  });
}
