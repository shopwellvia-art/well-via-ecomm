/**
 * The pure half of every chart in here: deciding what is a number and what is
 * a hole.
 *
 * All of it is plain functions with no React and no recharts import, so the
 * peer's vitest suite (node environment, no jsdom) can reach the logic that
 * actually matters. The components above are deliberately thin wrappers around
 * these.
 *
 * The one rule
 * ------------
 * `null` is a GAP, not a zero. Recharts will happily plot `null` at the
 * baseline, where it is pixel-identical to a measured zero, so the distinction
 * has to survive all the way from the envelope into the chart config. Nothing
 * in this file ever writes `?? 0`.
 */

/** What a missing value reads as in text. Defined once, in the token file. */
export { MISSING_DASH } from './chartTokens.js';

/**
 * Read one field as a number, or `null`.
 *
 * `null`, `undefined` and `''` are missing. A non-finite number (`NaN`,
 * `Infinity`) is also missing — it is the shape a bad division takes, and
 * plotting it is worse than not plotting it. A real `0` survives.
 *
 * Decimal-typed fields arrive as JSON numbers or as strings depending on the
 * serializer, so both are accepted; anything unparseable is missing rather
 * than coerced.
 */
export function valueAt(row, key) {
  if (!row || typeof row !== 'object') return null;
  const raw = row[key];
  if (raw === null || raw === undefined || raw === '') return null;
  if (typeof raw === 'boolean') return null;
  const n = typeof raw === 'number' ? raw : Number(raw);
  return Number.isFinite(n) ? n : null;
}

/** True when a value is a hole. Mirrors `viewState.isMissing` for numbers. */
export function isGap(value) {
  return value === null || value === undefined || !Number.isFinite(Number(value));
}

/**
 * Project raw envelope points onto `{ [xKey], ...keys }` with every measure
 * normalised through `valueAt`.
 *
 * The x value is passed through untouched (it may be a date string, a step
 * label or an integer month index) but a point with no x at all is dropped —
 * an unlabelled point cannot be placed on an axis honestly.
 */
export function normalizeSeries(points, xKey, keys) {
  if (!Array.isArray(points)) return [];
  const measures = Array.isArray(keys) ? keys : [keys];
  const out = [];
  for (const point of points) {
    if (!point || typeof point !== 'object') continue;
    const x = point[xKey];
    if (x === null || x === undefined) continue;
    const row = { [xKey]: x };
    for (const key of measures) row[key] = valueAt(point, key);
    out.push(row);
  }
  return out;
}

/**
 * How much of a normalised series is actually there.
 *
 * `allMissing` is the flag a chart uses to decide between drawing an axis and
 * explaining itself: a series where nothing could be computed must not render
 * as an empty plot area, because an empty plot area reads as "we looked and
 * the answer was nothing".
 */
export function seriesCoverage(rows, keys) {
  const measures = Array.isArray(keys) ? keys : [keys];
  const per = {};
  for (const key of measures) per[key] = { present: 0, missing: 0 };

  let present = 0;
  let missing = 0;
  for (const row of rows ?? []) {
    for (const key of measures) {
      if (isGap(row?.[key])) {
        per[key].missing += 1;
        missing += 1;
      } else {
        per[key].present += 1;
        present += 1;
      }
    }
  }

  return {
    points: rows?.length ?? 0,
    keys: measures,
    per,
    present,
    missing,
    /** Nothing to draw at all. */
    allMissing: present === 0,
    anyMissing: missing > 0,
    /** Measures for which every single point is a hole. */
    emptyKeys: measures.filter((key) => per[key].present === 0),
  };
}

/**
 * One sentence naming the holes, or `null` when there are none.
 *
 * Said out loud rather than left to the eye: a break in a line is easy to read
 * as a rendering artefact, and a reader who assumes that will fill it in with
 * a straight line in their head.
 */
export function gapNote(coverage) {
  if (!coverage || !coverage.anyMissing) return null;
  const total = coverage.present + coverage.missing;
  const n = coverage.missing;
  return (
    `${n} of ${total} value${total === 1 ? '' : 's'} could not be computed. ` +
    `${n === 1 ? 'It is' : 'They are'} drawn as a gap, never as zero.`
  );
}

/**
 * A one-line spoken description of a chart, for its accessible name.
 *
 * A screen reader announcing "graph" and nothing else is the audio equivalent
 * of a chart with no title. This says what is plotted and how much of it is
 * real; the sr-only data table that follows carries the numbers themselves.
 */
export function chartSummary(title, coverage, kind = 'chart') {
  if (!coverage || coverage.points === 0) {
    return `${title}: ${kind} with no data.`;
  }
  const measures = coverage.keys.length;
  const base =
    `${title}: ${kind} of ${measures} ` +
    `series over ${coverage.points} point${coverage.points === 1 ? '' : 's'}.`;
  if (!coverage.anyMissing) return base;
  return (
    `${base} ${coverage.missing} value${coverage.missing === 1 ? '' : 's'} ` +
    'could not be computed and are shown as gaps, not zeros.'
  );
}

/**
 * Indexes whose value is present but whose neighbours are both holes.
 *
 * With `connectNulls={false}` an isolated point has no segment to either side,
 * so recharts draws nothing at all for it and a real measurement disappears.
 * These indexes get an explicit dot so they stay visible.
 */
export function isolatedIndexes(rows, key) {
  const out = new Set();
  const list = rows ?? [];
  for (let i = 0; i < list.length; i += 1) {
    if (isGap(list[i]?.[key])) continue;
    const before = i > 0 ? list[i - 1]?.[key] : undefined;
    const after = i < list.length - 1 ? list[i + 1]?.[key] : undefined;
    const noLeft = i === 0 || isGap(before);
    const noRight = i === list.length - 1 || isGap(after);
    if (noLeft && noRight) out.add(i);
  }
  return out;
}

/**
 * Lay out waterfall bars from signed step amounts.
 *
 * Each non-total step floats from the running subtotal; a step flagged
 * `is_total` is re-anchored to the axis and restarts the run.
 *
 * The interesting case is a missing step. A waterfall is a chain — if one link
 * is unknown, every bar after it sits at an unknown height. Rather than carry
 * the running total across the hole (which would draw the later bars in
 * confidently wrong positions), accumulation stops: the missing step and every
 * step after it are returned as `missing`, with `reason` saying which. Only an
 * explicit total re-anchors and lets the chain resume, because a total is
 * measured from the axis rather than from its predecessor.
 */
export function toWaterfallBars(points, options = {}) {
  const {
    xKey = 'step',
    valueKey = 'amount',
    totalKey = 'is_total',
  } = options;

  let running = 0;
  let broken = false;
  let brokenAt = null;

  return (points ?? []).map((point) => {
    const label = point?.[xKey];
    const isTotal = Boolean(point?.[totalKey]);
    const value = valueAt(point, valueKey);

    if (value === null) {
      if (!isTotal) {
        broken = true;
        brokenAt = brokenAt ?? label;
      }
      return {
        [xKey]: label,
        label,
        value: null,
        base: null,
        span: null,
        start: null,
        end: null,
        isTotal,
        missing: true,
        reason: 'not computable',
      };
    }

    if (isTotal) {
      // A total is measured from zero, so it is trustworthy even when the
      // steps leading to it were not — and it re-anchors the chain.
      running = value;
      broken = false;
      brokenAt = null;
      return {
        [xKey]: label,
        label,
        value,
        base: Math.min(0, value),
        span: Math.abs(value),
        start: 0,
        end: value,
        isTotal: true,
        missing: false,
        reason: '',
      };
    }

    if (broken) {
      return {
        [xKey]: label,
        label,
        value,
        base: null,
        span: null,
        start: null,
        end: null,
        isTotal: false,
        missing: true,
        reason: `cannot be placed: ${brokenAt ?? 'an earlier step'} is missing`,
      };
    }

    const start = running;
    const end = running + value;
    running = end;
    return {
      [xKey]: label,
      label,
      value,
      base: Math.min(start, end),
      span: Math.abs(value),
      start,
      end,
      isTotal: false,
      missing: false,
      reason: '',
    };
  });
}

/**
 * Slices for a pie/donut, with shares.
 *
 * A slice whose value is missing is NOT given an arc — an arc is a claim about
 * a proportion of a whole, and a value we do not have has no proportion. It is
 * returned separately in `excluded` so the caller can name it instead of
 * silently dropping it. Negative values are excluded for the same reason: they
 * have no meaningful angle.
 */
export function toSlices(points, labelKey, valueKey) {
  const included = [];
  const excluded = [];

  for (const point of points ?? []) {
    const label = point?.[labelKey];
    const value = valueAt(point, valueKey);
    if (value === null) {
      excluded.push({ label, value: null, reason: 'not computable' });
    } else if (value < 0) {
      excluded.push({ label, value, reason: 'negative' });
    } else {
      included.push({ label, value });
    }
  }

  const total = included.reduce((sum, slice) => sum + slice.value, 0);
  return {
    slices: included.map((slice) => ({
      ...slice,
      share: total > 0 ? (slice.value / total) * 100 : null,
    })),
    excluded,
    total,
  };
}

/**
 * The series ids a chart spec asks for, always as an array.
 * Registry specs carry `series: [...]`; a hand-built spec may carry a string.
 */
export function specSeries(spec) {
  if (!spec) return [];
  if (Array.isArray(spec.series)) return spec.series.filter(Boolean);
  if (typeof spec.series === 'string') return [spec.series];
  return [];
}
