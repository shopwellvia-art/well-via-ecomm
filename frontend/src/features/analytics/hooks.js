import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { analyticsApi } from './api.js';
import { shouldFetch } from './viewState.js';

/**
 * Refetch cadence by the view's declared freshness class.
 *
 * Matches the backend cache TTLs (45s / 900s / 3600s) so the client refetches
 * just after an entry expires rather than repeatedly hitting a warm cache and
 * learning nothing. A daily view polling every 60s would be 60x the requests
 * for the same answer.
 */
const REFETCH_BY_FRESHNESS = {
  realtime: 45_000,
  hourly: 300_000,
  daily: 900_000,
};

export function useSalesAnalytics({ period = '30d', granularity = 'day' } = {}) {
  return useQuery({
    queryKey: ['analytics', 'sales', period, granularity],
    queryFn: () => analyticsApi.sales({ period, granularity }),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

export function useProfitAnalytics({ period = '30d' } = {}) {
  return useQuery({
    queryKey: ['analytics', 'profit', period],
    queryFn: () => analyticsApi.profit({ period }),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

// ---- v2 -------------------------------------------------------------------

/** Modules + views visible to this user. Long staleTime: permissions rarely move. */
export function useAnalyticsNavigation() {
  return useQuery({
    queryKey: ['analytics', 'navigation'],
    queryFn: () => analyticsApi.navigation(),
    staleTime: 5 * 60_000,
  });
}

/**
 * THE view hook — one call renders one view.
 *
 * `enabled` is driven by `shouldFetch(viewDef)`, so an INTEGRATION_REQUIRED,
 * FEATURE_REQUIRED or NOT_APPLICABLE view issues **no network request at all**.
 * Gating in the component instead would still fire the query and merely hide
 * the result; gating here is what makes those views genuinely free.
 *
 * `keepPreviousData` matters more here than elsewhere: without it, every filter
 * tweak blanks the whole dashboard to skeletons, which reads as data loss.
 *
 * IMPORTANT — `params` must be ALREADY SERIALIZED, i.e.
 *
 *     Object.fromEntries(toSearchParams(filters, viewDef))
 *
 * not the raw filter object. Two reasons, both silent failures:
 *
 *   1. The raw object carries every default (~24 keys), so the request would
 *      send filters the view never declared and the URL would stop matching
 *      what the user actually chose.
 *   2. `params` is also part of the react-query key. The backend's
 *      `cache_key_part()` drops defaults before hashing, so an unserialized
 *      object desynchronises the client cache key from the server's — two
 *      requests the backend considers identical would occupy different client
 *      cache entries, and the hit rate quietly collapses.
 */
export function useAnalyticsView(viewDef, moduleSlug, viewSlug, params = {}) {
  const freshness = viewDef?.freshness ?? 'daily';
  return useQuery({
    queryKey: ['analytics', 'view', moduleSlug, viewSlug, params],
    queryFn: () => analyticsApi.view(moduleSlug, viewSlug, params),
    enabled: Boolean(moduleSlug && viewSlug) && shouldFetch(viewDef),
    placeholderData: keepPreviousData,
    staleTime: (REFETCH_BY_FRESHNESS[freshness] ?? 900_000) / 2,
    refetchInterval: REFETCH_BY_FRESHNESS[freshness] ?? 900_000,
  });
}

/** KPI catalogue for metric tooltips. Ships with the app; effectively static. */
export function useKpiCatalogue() {
  return useQuery({
    queryKey: ['analytics', 'kpis'],
    queryFn: () => analyticsApi.kpis(),
    staleTime: 60 * 60_000,
  });
}

/** Worker health. Short staleTime — this is the page you open when worried. */
export function useAnalyticsHealth({ enabled = true } = {}) {
  return useQuery({
    queryKey: ['analytics', 'health'],
    queryFn: () => analyticsApi.health(),
    enabled,
    staleTime: 15_000,
    refetchInterval: 30_000,
    retry: false,
  });
}
