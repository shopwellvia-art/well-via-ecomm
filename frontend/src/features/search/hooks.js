import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useDebounced } from '@/lib/hooks.js';
import { searchApi } from './api.js';

/**
 * Shortest query worth sending. Mirrors `MIN_QUERY_LENGTH` in
 * `backend/app/services/search_service.py` — a single letter matches most of the
 * catalogue, so the results are noise and the query is expensive. Both ends
 * enforce it: this one to skip the request, the backend because the endpoint is
 * public and reachable without going through this hook.
 */
export const MIN_QUERY_LENGTH = 2;

/**
 * Live global-search results for `query`.
 *
 * Debounced at 200ms — long enough that a typed word costs one request rather
 * than one per letter, short enough to still feel instant. `keepPreviousData`
 * keeps the previous hits on screen while the next request resolves, so the
 * dropdown does not blank out and jump between keystrokes.
 *
 * @param enabled pass false while the dropdown is closed so a stale query in the
 *                input does not keep fetching in the background
 */
export function useGlobalSearch(query, { limit = 6, enabled = true } = {}) {
  const debounced = useDebounced(query, 200);
  const term = (debounced || '').trim();
  const ready = enabled && term.length >= MIN_QUERY_LENGTH;

  const result = useQuery({
    queryKey: ['search', term, limit],
    queryFn: () => searchApi.global(term, limit),
    enabled: ready,
    placeholderData: keepPreviousData,
    // Catalogue copy does not change mid-session; re-typing a term the shopper
    // already tried should be instant rather than a second round trip.
    staleTime: 60_000,
  });

  return {
    ...result,
    /** The term the current data actually describes — the debounced one, not
     *  what is in the box this millisecond. Callers render "No results for X"
     *  from this so the message can never name a query that was not searched. */
    term,
    /** True while the shopper is mid-keystroke and no request has gone out. */
    isTooShort: term.length > 0 && term.length < MIN_QUERY_LENGTH,
    isPending: ready && result.isFetching && !result.data,
  };
}
