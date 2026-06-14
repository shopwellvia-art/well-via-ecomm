import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { productsApi } from './api.js';

export function useProducts(params) {
  return useQuery({
    queryKey: ['products', params],
    queryFn: () => productsApi.list(params),
    placeholderData: keepPreviousData,
  });
}

export function useProduct(id) {
  return useQuery({
    queryKey: ['product', id],
    queryFn: () => productsApi.get(id),
    enabled: !!id,
  });
}

export function useRelatedProducts(id, limit = 8) {
  return useQuery({
    queryKey: ['product', id, 'related', limit],
    queryFn: () => productsApi.related(id, limit),
    enabled: !!id,
    staleTime: 5 * 60 * 1000,
  });
}

export function useCoPurchasedProducts(id, limit = 12) {
  return useQuery({
    queryKey: ['product', id, 'co-purchased', limit],
    queryFn: () => productsApi.coPurchased(id, limit),
    enabled: !!id,
    staleTime: 5 * 60 * 1000,
  });
}

export function useLikelyProducts(id, limit = 12) {
  return useQuery({
    queryKey: ['product', id, 'likely', limit],
    queryFn: () => productsApi.likely(id, limit),
    enabled: !!id,
    staleTime: 5 * 60 * 1000,
  });
}

export function useProductsByIds(ids) {
  // The joined string in the queryKey preserves order, so re-renders with the
  // same ids in the same order hit the cache.
  const key = (ids || []).join(',');
  return useQuery({
    queryKey: ['products', 'by-ids', key],
    queryFn: () => productsApi.byIds(ids),
    enabled: Array.isArray(ids) && ids.length > 0,
    staleTime: 5 * 60 * 1000,
  });
}

export function useBestsellers(limit = 8) {
  return useQuery({
    queryKey: ['products', 'bestsellers', limit],
    queryFn: () => productsApi.bestsellers(limit),
    // Rankings barely move between renders. 5 min keeps the homepage snappy.
    staleTime: 5 * 60 * 1000,
  });
}
