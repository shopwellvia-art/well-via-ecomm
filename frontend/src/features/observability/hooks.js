import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { observabilityApi } from './api.js';

export function useObservabilityOverview({ period = '24h' } = {}) {
  return useQuery({
    queryKey: ['observability', 'overview', period],
    queryFn: () => observabilityApi.overview({ period }),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

export function useObservabilityRequests({
  period = '24h',
  method,
  status,
  q,
  page = 1,
  page_size = 50,
} = {}) {
  return useQuery({
    queryKey: ['observability', 'requests', period, method, status, q, page, page_size],
    queryFn: () => observabilityApi.requests({ period, method, status, q, page, page_size }),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

export function useObservabilityRoutes({ period = '24h', sort = 'avg' } = {}) {
  return useQuery({
    queryKey: ['observability', 'routes', period, sort],
    queryFn: () => observabilityApi.routes({ period, sort }),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

export function useObservabilitySlowQueries({
  period = '24h',
  view = 'queries',
  page = 1,
  page_size = 50,
} = {}) {
  return useQuery({
    queryKey: ['observability', 'slow-queries', period, view, page, page_size],
    queryFn: () => observabilityApi.slowQueries({ period, view, page, page_size }),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}
