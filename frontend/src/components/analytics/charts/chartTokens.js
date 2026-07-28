/**
 * Chart design tokens, lifted out of AdminSalesAnalyticsPage.jsx.
 *
 * They were trapped inside one page, which meant the next chart anyone drew
 * either re-declared them slightly differently or hard-coded a hex. Both of
 * those show up as two charts on one screen that do not look like one system.
 *
 * No React in here on purpose: everything is a value or a pure function, so the
 * peer test suite can import it under the project's node-environment vitest
 * config (there is no jsdom).
 */

/**
 * Categorical palette. Token-aligned: accent gradient midpoints plus the
 * status tones. Ordered so the first three are distinguishable at a glance and
 * do not read as a sequential ramp — a categorical scale that looks sequential
 * invites a reader to see an ordering that is not there.
 */
export const CAT_COLORS = [
  '#22c55e', // success
  '#6366f1', // accent
  '#f59e0b', // warning
  '#818cf8', // accent-mid
  '#f87171', // danger-ish
  '#34d399', // success lighter
  '#fb923c', // orange
];

/** Colour for series index `i`, wrapping. */
export function colorAt(index) {
  return CAT_COLORS[((index % CAT_COLORS.length) + CAT_COLORS.length) % CAT_COLORS.length];
}

/** Recharts tooltip surface, built from the CSS variable tokens. */
export const TOOLTIP_STYLE = {
  background: 'var(--bg-elevated)',
  border: '1px solid var(--line-subtle)',
  borderRadius: 8,
  fontSize: 12,
  color: 'var(--ink-primary)',
  boxShadow: 'var(--shadow-md)',
};

export const TOOLTIP_LABEL_STYLE = {
  color: 'var(--ink-tertiary)',
  marginBottom: 4,
};

export const AXIS_TICK = { fontSize: 11, fill: 'currentColor' };

/**
 * Axis conventions. `axisLine`/`tickLine` off and the tick colour inherited
 * from `text-ink-tertiary` — the axis is scaffolding, the data is the subject.
 */
export const AXIS_PROPS = {
  tick: AXIS_TICK,
  axisLine: false,
  tickLine: false,
  className: 'text-ink-tertiary',
};

/** Horizontal rules only: vertical grid lines compete with the series. */
export const GRID_PROPS = {
  strokeDasharray: '3 3',
  stroke: 'var(--grid-line)',
  vertical: false,
};

/** The mirror image, for charts whose category axis is the Y axis. */
export const GRID_PROPS_HORIZONTAL_BARS = {
  strokeDasharray: '3 3',
  stroke: 'var(--grid-line)',
  horizontal: false,
};

/** Fixed wrapper heights — ResponsiveContainer needs a sized parent. */
export const CHART_HEIGHT = {
  xs: 'h-24',
  sm: 'h-40',
  md: 'h-64',
  lg: 'h-72',
  xl: 'h-96',
};

/** Waterfall / delta tones. Deliberately not the categorical palette. */
export const DELTA_COLORS = {
  positive: '#22c55e',
  negative: '#f87171',
  total: '#6366f1',
};

/**
 * The colour a *missing* datum is drawn in when it has to be drawn at all
 * (heatmap cells, matrix cells). Nothing is plotted in the data palette for a
 * value that does not exist.
 */
export const MISSING_FILL = 'var(--fill)';

/** What a missing value reads as. Never `0`, never blank. */
export const MISSING_DASH = '—';

/** Chart types `ChartCard` knows how to draw. Closed on purpose. */
export const CHART_TYPES = [
  'line',
  'area',
  'bar',
  'stacked-bar',
  'hbar',
  'waterfall',
  'donut',
  'pie',
  'scatter',
  'sparkline',
];

/**
 * True when the viewer has asked for less motion.
 *
 * Read at render rather than subscribed to: chart animation is a 300ms entry
 * tween, so picking up a preference change on the next render is soon enough,
 * and this keeps the module free of React.
 */
export function prefersReducedMotion() {
  if (typeof window === 'undefined' || !window.matchMedia) return false;
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}

/**
 * Recharts animation props. The brief asks for minimal animation, so this is a
 * short entry tween at most, and nothing at all under reduced motion.
 */
export function animationProps(duration = 280) {
  const reduced = prefersReducedMotion();
  return {
    isAnimationActive: !reduced,
    animationDuration: reduced ? 0 : duration,
  };
}

/** True for the `YYYY-MM-DD` / `YYYY-MM-DDTHH` x values the resolvers emit. */
export function looksLikeDate(value) {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}/.test(value);
}

/**
 * Short label for a categorical or date x value.
 *
 * Dates are shortened; anything else is passed through as its own label. A
 * category axis is never re-parsed as a date, because `2026` as a cohort name
 * and `2026` as a year are different things and guessing gets it wrong.
 */
export function axisLabel(value, { long = false } = {}) {
  if (value === null || value === undefined) return MISSING_DASH;
  if (!looksLikeDate(value)) return String(value);
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleDateString(
    undefined,
    long
      ? { weekday: 'short', month: 'short', day: 'numeric' }
      : { month: 'short', day: 'numeric' },
  );
}

/** Axis tick shortener — `1.2k`, `3.4M`. Never invents precision. */
export function compactTick(value) {
  if (value === null || value === undefined) return MISSING_DASH;
  const n = Number(value);
  if (!Number.isFinite(n)) return MISSING_DASH;
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}
