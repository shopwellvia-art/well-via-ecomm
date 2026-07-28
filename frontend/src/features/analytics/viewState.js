/**
 * Decides what an analytics view is allowed to render. Pure, no React, no
 * network — so it is testable under the project's node-environment vitest
 * config, which has no jsdom.
 *
 * This is the frontend half of the guarantee the backend makes: a number that
 * could not be computed is `null`, and `null` must never reach a chart as a
 * zero. Recharts will happily plot `null` as a point on the baseline, and a
 * reader cannot tell that apart from a real zero — so the decision has to be
 * made here, before anything is handed to a chart.
 *
 * The ordering matters and is deliberate:
 *
 *   gated -> error -> loading -> no-data -> data
 *
 * `gated` comes first because a gated view must issue no request at all. If it
 * were checked after `loading`, the component would fire a query for a view the
 * backend is only going to refuse.
 */

/** States the backend can declare for a view. Mirrors ViewState in types.py. */
export const VIEW_STATE = {
  LIVE: 'LIVE',
  PARTIAL: 'PARTIAL',
  INTEGRATION_REQUIRED: 'INTEGRATION_REQUIRED',
  FEATURE_REQUIRED: 'FEATURE_REQUIRED',
  BLOCKED_BY_MISSING_SOURCE: 'BLOCKED_BY_MISSING_SOURCE',
  NOT_APPLICABLE: 'NOT_APPLICABLE',
};

/**
 * States for which we must NOT call the API. Mirrors GATED_STATES in types.py.
 * Kept as a Set so `shouldFetch` is a membership test rather than a chain of
 * comparisons that someone later forgets to extend.
 */
export const GATED_STATES = new Set([
  VIEW_STATE.INTEGRATION_REQUIRED,
  VIEW_STATE.FEATURE_REQUIRED,
  VIEW_STATE.BLOCKED_BY_MISSING_SOURCE,
  VIEW_STATE.NOT_APPLICABLE,
]);

/** Metric quality, worst-first. Mirrors QUALITY_RANK in types.py. */
export const QUALITY_RANK = [
  'INCOMPLETE',
  'ESTIMATED',
  'ALLOCATED',
  'ACTUAL',
  'AUTHORITATIVE',
];

/** What the renderer should put on screen. */
export const RENDER_KIND = {
  GATED: 'gated',
  ERROR: 'error',
  LOADING: 'loading',
  NO_DATA: 'no-data',
  DATA: 'data',
};

/**
 * True when this view is allowed to hit the API.
 *
 * Called by the query hook as `enabled:`, so a gated view produces no network
 * request, no spinner and no cache entry — it costs nothing.
 */
export function shouldFetch(viewDef) {
  if (!viewDef) return false;
  return !GATED_STATES.has(viewDef.state);
}

/**
 * Human-facing copy for a gated view.
 *
 * Deliberately distinguishes "not built yet" from "will never apply here".
 * `NOT_APPLICABLE` is not a roadmap item — telling an admin to wait for
 * branch-comparison in a single-store deployment would be a small lie that
 * costs them a support ticket.
 */
export function gateCopy(viewDef) {
  const requires = viewDef?.requires ?? [];
  const limitation = viewDef?.limitation ?? '';
  switch (viewDef?.state) {
    case VIEW_STATE.INTEGRATION_REQUIRED:
      return {
        title: 'Connect a data source to use this view',
        body: limitation || 'This view needs an external source that is not connected yet.',
        requires,
        actionable: true,
      };
    case VIEW_STATE.FEATURE_REQUIRED:
      return {
        title: 'Not available in this setup',
        body: limitation || 'This view needs a capability this store does not have yet.',
        requires,
        actionable: false,
      };
    case VIEW_STATE.BLOCKED_BY_MISSING_SOURCE:
      return {
        title: 'Source unavailable',
        body: limitation || 'The data this view needs cannot be read right now.',
        requires,
        actionable: true,
      };
    case VIEW_STATE.NOT_APPLICABLE:
      return {
        title: 'Does not apply to this store',
        body: limitation || 'This concept does not apply to a single-store deployment.',
        requires,
        actionable: false,
      };
    default:
      return null;
  }
}

/** Worst quality wins — one unknown input condemns the whole view. */
export function rollUpQuality(qualities) {
  const known = (qualities ?? []).filter((q) => QUALITY_RANK.includes(q));
  if (known.length === 0) return 'INCOMPLETE';
  return known.reduce((worst, q) =>
    QUALITY_RANK.indexOf(q) < QUALITY_RANK.indexOf(worst) ? q : worst
  );
}

/**
 * True when a KPI has nothing to show.
 *
 * `0` is data. `null` and `undefined` are not. Written out explicitly because
 * the falsy shorthand (`!value`) treats a genuine zero as missing, which is the
 * exact inversion of what this whole system is for.
 */
export function isMissing(value) {
  return value === null || value === undefined;
}

/**
 * True when every KPI on the view came back missing.
 *
 * Distinguishes "the query ran and found nothing" from "the query could not
 * answer". Used to pick NO_DATA over DATA so the UI shows an explanation rather
 * than a grid of blank cards.
 */
export function allKpisMissing(kpis) {
  const values = Object.values(kpis ?? {});
  if (values.length === 0) return true;
  return values.every((k) => isMissing(k?.value));
}

/**
 * The single decision function.
 *
 * @param viewDef  registry entry (state, requires, limitation)
 * @param envelope the API response, or null
 * @param query    { isLoading, isError, error }
 */
export function resolveViewState(viewDef, envelope, query = {}) {
  if (!viewDef) {
    return { kind: RENDER_KIND.ERROR, message: 'Unknown view.' };
  }

  // First: a gated view never fetches, so it must never show a spinner or an
  // error just because no request was made.
  if (GATED_STATES.has(viewDef.state)) {
    return { kind: RENDER_KIND.GATED, gate: gateCopy(viewDef), viewDef };
  }

  if (query.isError) {
    return {
      kind: RENDER_KIND.ERROR,
      message:
        query.error?.response?.data?.error?.message ||
        'Could not load this view.',
      retryable: true,
    };
  }

  if (query.isLoading || !envelope) {
    return { kind: RENDER_KIND.LOADING };
  }

  // The backend can downgrade at runtime — a view declared LIVE whose rollup has
  // no rows yet comes back PARTIAL. Trust the envelope over the static registry,
  // and NEVER upgrade: a runtime probe may only ever lower the claim.
  const effectiveState =
    envelope.availability && envelope.availability !== viewDef.state
      ? envelope.availability
      : viewDef.state;

  if (GATED_STATES.has(effectiveState)) {
    return {
      kind: RENDER_KIND.GATED,
      gate: gateCopy({ ...viewDef, state: effectiveState, requires: envelope.requires, limitation: envelope.limitation }),
      viewDef,
    };
  }

  const noSeries = Object.keys(envelope.series ?? {}).length === 0;
  const noTables = Object.keys(envelope.tables ?? {}).length === 0;
  if (allKpisMissing(envelope.kpis) && noSeries && noTables) {
    return {
      kind: RENDER_KIND.NO_DATA,
      message:
        envelope.warnings?.[0]?.message ||
        'Nothing has been aggregated for this period yet.',
      warnings: envelope.warnings ?? [],
      viewDef,
    };
  }

  return {
    kind: RENDER_KIND.DATA,
    envelope,
    viewDef,
    state: effectiveState,
    quality: envelope.quality ?? 'AUTHORITATIVE',
    isPartial: Boolean(envelope.is_partial) || effectiveState === VIEW_STATE.PARTIAL,
    warnings: envelope.warnings ?? [],
  };
}

/**
 * Trim a series to the source watermark.
 *
 * A defensive second line: the backend already refuses to densify past the
 * watermark, but if a series ever arrives with trailing points beyond it, this
 * drops them rather than letting a chart draw a confident line across days that
 * were never aggregated.
 */
export function trimToWatermark(points, watermark, dateKey = 'date') {
  if (!watermark || !Array.isArray(points)) return points ?? [];
  return points.filter((p) => p?.[dateKey] && p[dateKey] <= watermark);
}
