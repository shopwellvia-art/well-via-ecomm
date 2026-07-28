import { lazy } from 'react';

/**
 * The closed set of bespoke analytics views.
 *
 * Bespoke means "this view earns its own component". Everything else in the
 * 73-view registry is drawn by the metadata renderer out of `charts` and
 * `tables`, which is why the whole surface stays consistent: one place decides
 * how a chart looks, how a table paginates, how freshness and provenance are
 * shown. Every bespoke component is a hole in that guarantee, so there are ten
 * of them and there is a backend test that fails if the registry grows an
 * eleventh.
 *
 * The keys are the registry's `bespoke` field verbatim — the same strings the
 * `custom` resolver dispatches on server-side. They are not view slugs and not
 * component names, so a rename on either side is caught by the map failing to
 * resolve rather than by a blank panel nobody notices.
 *
 * **Adding an eleventh is a decision, not a drive-by.** It means the metadata
 * renderer could not express something, which is worth writing down in the PR
 * before it is worth writing code for. The backend registry has to grow the
 * key first; this map cannot invent one.
 *
 * Every component here takes `{ envelope, viewDef, filters }` and **fetches
 * nothing**. Caching, freshness, permission gating and provenance are decided
 * once, upstream, for all 73 views; a bespoke view that opened its own
 * connection would be exempt from all four.
 *
 * `ViewRenderer` resolves these by filename instead of by importing this map —
 * `import.meta.glob('./bespoke/*View.jsx')` keyed on
 * `pascalCase(bespokeKey) + 'View.jsx'`. So the filenames in this directory are
 * load-bearing: `anomaly_feed` MUST live in `AnomalyFeedView.jsx`. This map is
 * kept as the closed declaration of the set (a glob is open by construction —
 * any new `*View.jsx` file becomes a bespoke view), and as the invariant below
 * that fails loudly if the two ever drift.
 */

/** The ten keys, in registry order. Frozen so nothing appends at runtime. */
export const BESPOKE_KEYS = Object.freeze([
  'revenue_waterfall',
  'cohort_heatmap',
  'rfm_matrix',
  'journey_paths',
  'conversion_funnel',
  'geo_map',
  'tracking_health',
  'reconciliation_grid',
  'experiment_results',
  'anomaly_feed',
]);

/** The registry's cap. Mirrors the backend test that enforces it. */
export const MAX_BESPOKE_VIEWS = 10;

/**
 * slug (registry `bespoke` key) -> lazily-loaded component.
 *
 * Lazy because these are the heaviest components in the admin bundle — the geo
 * map alone pulls in Leaflet — and an admin looking at Executive Overview
 * should not download a mapping library to do it.
 */
export const BESPOKE_VIEWS = Object.freeze({
  revenue_waterfall: lazy(() => import('./RevenueWaterfallView.jsx')),
  cohort_heatmap: lazy(() => import('./CohortHeatmapView.jsx')),
  rfm_matrix: lazy(() => import('./RfmMatrixView.jsx')),
  journey_paths: lazy(() => import('./JourneyPathsView.jsx')),
  conversion_funnel: lazy(() => import('./ConversionFunnelView.jsx')),
  geo_map: lazy(() => import('./GeoMapView.jsx')),
  tracking_health: lazy(() => import('./TrackingHealthView.jsx')),
  reconciliation_grid: lazy(() => import('./ReconciliationGridView.jsx')),
  experiment_results: lazy(() => import('./ExperimentResultsView.jsx')),
  anomaly_feed: lazy(() => import('./AnomalyFeedView.jsx')),
});

/**
 * The component for a bespoke key, or `undefined`.
 *
 * Undefined on purpose rather than a fallback renderer: a registry key with no
 * component is a wiring gap, and the caller should say so out loud instead of
 * quietly drawing something generic under a bespoke view's name.
 */
export function getBespokeView(key) {
  if (!key) return undefined;
  return Object.prototype.hasOwnProperty.call(BESPOKE_VIEWS, key)
    ? BESPOKE_VIEWS[key]
    : undefined;
}

/** True when this registry key has a component in this build. */
export function hasBespokeView(key) {
  return getBespokeView(key) !== undefined;
}

// Structural invariant, checked in dev only so a mistake surfaces on the first
// page load rather than as a missing panel three screens deep. Not enforced in
// production, where throwing at module scope would take the whole admin down
// over a bookkeeping error.
if (import.meta.env?.DEV) {
  const mapped = Object.keys(BESPOKE_VIEWS);
  if (mapped.length > MAX_BESPOKE_VIEWS) {
    throw new Error(
      `bespoke/index.js maps ${mapped.length} views but the registry caps them ` +
        `at ${MAX_BESPOKE_VIEWS}. Adding one is a deliberate decision — see the ` +
        'module docstring.',
    );
  }
  const missing = BESPOKE_KEYS.filter((key) => !mapped.includes(key));
  const extra = mapped.filter((key) => !BESPOKE_KEYS.includes(key));
  if (missing.length || extra.length) {
    throw new Error(
      'bespoke/index.js disagrees with BESPOKE_KEYS. ' +
        `Missing: [${missing.join(', ')}]. Unexpected: [${extra.join(', ')}].`,
    );
  }
}
