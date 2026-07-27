import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { storefrontConfigApi } from './api.js';
import { STOREFRONT_DEFAULTS } from './defaults.js';

const KEY = ['storefront-config'];

export function useStorefrontConfig() {
  return useQuery({
    queryKey: KEY,
    queryFn: storefrontConfigApi.get,
    // Public storefront data — stale after 5 minutes, but renders instantly
    // from cache while revalidating in the background.
    staleTime: 5 * 60 * 1000,
  });
}

/**
 * Config document with factory defaults merged under the API response. The
 * backend returns a complete document, so a shallow merge only matters when
 * the API is unreachable and `data` is undefined.
 */
export function useStorefrontConfigWithDefaults() {
  const query = useStorefrontConfig();
  return { ...query, config: { ...STOREFRONT_DEFAULTS, ...(query.data ?? {}) } };
}

export function useUpdateStorefrontConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: storefrontConfigApi.update,
    onSuccess: (data) => qc.setQueryData(KEY, data),
  });
}

export function useUploadStorefrontImage() {
  return useMutation({ mutationFn: storefrontConfigApi.uploadImage });
}
