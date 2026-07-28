import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/services/apiClient.js';

/**
 * Analytics integration settings + Tracking Health.
 *
 * Deliberately separate from `features/analytics/api.js`: that module is the
 * read path for dashboards and is polled constantly, while this one edits
 * credentials. Sharing a query key between them would mean a routine dashboard
 * refetch could evict — or worse, repopulate — the settings form under the
 * operator's cursor mid-edit.
 *
 * Secrets
 * -------
 * The server returns `***` for any secret that is set and `""` for one that is
 * not, alongside `has_value`. Nothing here ever holds a real credential except
 * for the instant between an admin typing one and `PUT` sending it, and
 * `buildUpdates` in the page is what guarantees the mask is never sent back as
 * if it were a new value.
 */

/** The literal the server uses for a masked secret. Must match `REDACTED` in
 *  `services/analytics/integrations.py`. */
export const REDACTED = '***';

const INTEGRATIONS_KEY = ['analytics', 'integrations'];
const HEALTH_KEY = ['analytics', 'integrations', 'health'];

export const analyticsIntegrationsApi = {
  /** Schema + current values. Secrets arrive already redacted. */
  get: () => apiClient.get('/analytics/integrations').then((r) => r.data),

  /** `updates` is a flat `{key: value}` map; omit a key to leave it alone. */
  update: (updates) =>
    apiClient.put('/analytics/integrations', { updates }).then((r) => r.data),

  /**
   * Connection test for one provider. Always resolves with a result object on
   * a reachable server — a failed *check* is data, not a transport error, so
   * `status` / `verified` carry the outcome rather than an HTTP code.
   */
  test: (provider) =>
    apiClient
      .post(`/analytics/integrations/${provider}/test`)
      .then((r) => r.data),

  /** The Tracking Health probe. Needs `analytics.control_centre.view`. */
  health: () =>
    apiClient.get('/analytics/integrations/health').then((r) => r.data),
};

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

/**
 * The settings form's source of truth.
 *
 * `staleTime: Infinity` and no refetch interval on purpose. This is an edit
 * surface: a background refetch while a form is dirty either discards what the
 * admin typed or leaves the draft silently diverged from the "server value" the
 * dirty check compares against. It is invalidated explicitly after a save.
 */
export function useAnalyticsIntegrations() {
  return useQuery({
    queryKey: INTEGRATIONS_KEY,
    queryFn: analyticsIntegrationsApi.get,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
}

export function useUpdateAnalyticsIntegrations() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (updates) => analyticsIntegrationsApi.update(updates),
    onSuccess: (data) => {
      // The PUT returns the full, freshly-redacted payload, so seed the cache
      // from it rather than triggering another round trip.
      qc.setQueryData(INTEGRATIONS_KEY, data.integrations);
      // Changing an id, a flag or the consent mode changes what health reports.
      qc.invalidateQueries({ queryKey: HEALTH_KEY });
    },
  });
}

/**
 * Connection test. Kept as a mutation, not a query, because it is an action
 * with an outbound side effect (GA4 posts to Google) and must fire only when
 * the operator asks — never on mount, focus or retry.
 *
 * Keyed per provider so three concurrent tests do not overwrite each other's
 * result banner.
 */
export function useTestAnalyticsProvider() {
  return useMutation({
    mutationFn: (provider) => analyticsIntegrationsApi.test(provider),
  });
}

/**
 * Tracking Health.
 *
 * Polls, unlike the settings query: this panel answers "is tracking working
 * *right now*", and a stale answer to that question is the failure it exists to
 * catch. `retry: false` so a 403 (the operator holds manage but not
 * control_centre.view) surfaces immediately instead of after three attempts.
 */
export function useTrackingHealth({ enabled = true } = {}) {
  return useQuery({
    queryKey: HEALTH_KEY,
    queryFn: analyticsIntegrationsApi.health,
    enabled,
    staleTime: 30_000,
    // Stop polling once it has failed. A 403 (the operator holds manage but
    // not control_centre.view) is not going to fix itself, and a panel that
    // re-requests it every minute forever is a self-inflicted load generator.
    refetchInterval: (query) => (query.state.error ? false : 60_000),
    retry: false,
  });
}

// ---------------------------------------------------------------------------
// Presentation helpers
// ---------------------------------------------------------------------------

/**
 * How a connection-test result should be presented.
 *
 * `tone` is derived from `verified` and `status`, never from "the request
 * returned 200". `cannot_verify_server_side` is deliberately NOT a success
 * tone: a green tick that means "we did not check" is worse than no tick,
 * because it stops the operator looking.
 */
export function testResultTone(result) {
  if (!result) return 'neutral';
  if (result.verified) return 'success';
  switch (result.status) {
    case 'rejected':
    case 'invalid_format':
      return 'danger';
    case 'not_configured':
    case 'unreachable':
    case 'cannot_verify_server_side':
      return 'warning';
    default:
      return 'neutral';
  }
}

/** Short label for the result badge. */
export function testResultLabel(result) {
  if (!result) return '';
  if (result.verified) return 'Verified';
  switch (result.status) {
    case 'cannot_verify_server_side':
      return 'Not verifiable from the server';
    case 'invalid_format':
      return 'Malformed ID';
    case 'not_configured':
      return 'Not configured';
    case 'unreachable':
      return 'Could not reach the provider';
    case 'rejected':
      return 'Rejected by the provider';
    default:
      return result.status;
  }
}

const SEVERITY_TONE = { critical: 'danger', warning: 'warning', info: 'info' };

export function warningTone(severity) {
  return SEVERITY_TONE[severity] || 'neutral';
}

/** Pull a readable message out of an axios error from any of these routes. */
export function integrationsErrorMessage(error, fallback = 'Something went wrong.') {
  return error?.response?.data?.error?.message || error?.message || fallback;
}
