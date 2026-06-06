import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { analyticsApi } from './api.js';

export function useSalesAnalytics({ period = '30d', granularity = 'day' } = {}) {
  return useQuery({
    queryKey: ['analytics', 'sales', period, granularity],
    queryFn: () => analyticsApi.sales({ period, granularity }),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}
