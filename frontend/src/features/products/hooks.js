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

// Separate cache key from `useProduct` on purpose: the two responses have
// different shapes, and sharing a key would let a storefront read of the same
// product evict the admin copy (or vice versa) and drop the ops fields.
export function useProductForAdmin(id) {
  return useQuery({
    queryKey: ['product', id, 'admin'],
    queryFn: () => productsApi.getForAdmin(id),
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

/**
 * Products for the header "Shop" mega menu.
 *
 * `enabled` is gated on the menu actually being open so the header does not
 * fetch a catalog page for every visitor who never opens it. The query key is
 * the ordinary ['products', params] shape, so once open it shares React Query's
 * cache with the listing page rather than refetching.
 */
export function useShopMenuProducts(limit = 9, enabled = true) {
  const params = { page_size: limit, sort_by: 'newest' };
  return useQuery({
    queryKey: ['products', params],
    queryFn: () => productsApi.list(params),
    enabled,
    // The nav rarely changes mid-session; keep reopening the menu instant.
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
