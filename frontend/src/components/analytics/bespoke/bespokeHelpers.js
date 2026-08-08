/**
 * The pure half of the ten bespoke views.
 *
 * Same reason as `charts/seriesGuards.js`: no React and no recharts in here, so
 * the peer's node-environment vitest suite can reach the logic. The components
 * are meant to stay thin enough that reading them tells you what is on screen,
 * and every decision that could be got *wrong* lives here where it can be
 * asserted.
 *
 * Most of these functions exist to keep two things apart that the backend went
 * to some trouble to keep apart: "we measured, and the answer is zero" and "we
 * could not measure". Once those two collapse into each other, an admin reads a
 * clean bill of health off a screen that never ran a single check.
 */

import { isMissing } from '@/features/analytics/viewState.js';

/** Envelope accessors ---------------------------------------------------- */

/** The rows of the first table whose id matches, or `[]`. */
export function tableRows(envelope, ...ids) {
  const tables = envelope?.tables ?? {};
  for (const id of ids) {
    if (tables[id]) return tables[id].rows ?? [];
  }
  const first = Object.values(tables)[0];
  return first?.rows ?? [];
}

/** The whole block (rows + `truncated`), so a top-N is never read as a total. */
export function tableBlock(envelope, ...ids) {
  const tables = envelope?.tables ?? {};
  for (const id of ids) {
    if (tables[id]) return tables[id];
  }
  return Object.values(tables)[0] ?? null;
}

/**
 * One series, or `undefined` when the resolver did not send it.
 *
 * `undefined` is load-bearing: several resolvers omit a series on purpose
 * rather than send `[]`, because an empty axis reads as a measured zero. It is
 * never defaulted to `[]` here.
 */
export function seriesFor(envelope, ...ids) {
  const series = envelope?.series ?? {};
  for (const id of ids) {
    if (series[id] !== undefined) return series[id];
  }
  return undefined;
}

/** Every warning carrying `code`. */
export function warningsWith(envelope, code) {
  return (envelope?.warnings ?? []).filter((w) => w?.code === code);
}

/** The first warning carrying `code`, or `null`. */
export function warningWith(envelope, code) {
  return warningsWith(envelope, code)[0] ?? null;
}

/**
 * The `NOT_CONFIGURED` reason, if the resolver returned one.
 *
 * `base.not_configured()` returns the reason and an explicitly EMPTY `sources`
 * list, which is the machine-readable half of "nothing is connected". Both
 * halves are checked, so a view cannot be mistaken for configured-and-empty.
 */
export function notConfigured(envelope) {
  const warning = warningWith(envelope, 'NOT_CONFIGURED');
  if (!warning) return null;
  return {
    message: warning.message ?? '',
    requires: warning.detail?.requires ?? [],
    noSources: (envelope?.sources ?? []).length === 0,
  };
}

/** Reconciliation ------------------------------------------------------- */

export const CHECK_STATUS = {
  MATCHED: 'matched',
  VARIANCE: 'variance',
  NOT_RUN: 'not_configured',
};

/**
 * Counts by outcome, and the headline claim the board is allowed to make.
 *
 * `allClear` is true ONLY when at least one check ran and every check that ran
 * matched AND nothing is unrun. A board with two matched checks and three that
 * never ran is not a clean bill of health, and this is the function that
 * refuses to call it one.
 */
export function reconciliationSummary(rows) {
  let matched = 0;
  let variance = 0;
  let notRun = 0;
  for (const row of rows ?? []) {
    if (row?.status === CHECK_STATUS.VARIANCE) variance += 1;
    else if (row?.status === CHECK_STATUS.NOT_RUN) notRun += 1;
    else if (row?.status === CHECK_STATUS.MATCHED) matched += 1;
    else notRun += 1; // an unrecognised status has not demonstrated anything
  }
  const total = matched + variance + notRun;
  return {
    total,
    matched,
    variance,
    notRun,
    ran: matched + variance,
    allClear: total > 0 && variance === 0 && notRun === 0 && matched > 0,
  };
}

/** Tracking health ------------------------------------------------------ */

/**
 * Worst first. A rollup that was never built is the WORST state, not the
 * freshest — its `lag_days` is `null`, and any sort that treats `null` as 0
 * (which is what `a.lag - b.lag` does with a null) puts it at the top of a
 * "most current" list. That inversion is the whole reason this exists.
 */
export const HEALTH_RANK = { never_built: 0, stale: 1, current: 2 };

export function healthRank(row) {
  const rank = HEALTH_RANK[row?.status];
  // An unknown status has not proved it is healthy, so it sorts with the worst.
  return rank === undefined ? 0 : rank;
}

export function sortHealthRows(rows) {
  return [...(rows ?? [])].sort((a, b) => {
    const byStatus = healthRank(a) - healthRank(b);
    if (byStatus !== 0) return byStatus;
    // Within `stale`, further behind is worse. A null lag never reaches here
    // (it only occurs on never_built) but is pinned worst if it ever does.
    const lagA = isMissing(a?.lag_days) ? Number.POSITIVE_INFINITY : Number(a.lag_days);
    const lagB = isMissing(b?.lag_days) ? Number.POSITIVE_INFINITY : Number(b.lag_days);
    if (lagA !== lagB) return lagB - lagA;
    return String(a?.label ?? a?.source ?? '').localeCompare(
      String(b?.label ?? b?.source ?? ''),
    );
  });
}

/** How a lag reads in words. `null` is never "0 days behind". */
export function lagLabel(row) {
  if (row?.status === 'never_built' || isMissing(row?.lag_days)) return 'never built';
  const lag = Number(row.lag_days);
  if (!Number.isFinite(lag)) return 'unknown';
  if (lag <= 0) return 'up to date';
  return `${lag} day${lag === 1 ? '' : 's'} behind`;
}

/** Anomalies ------------------------------------------------------------ */

/**
 * Severity order, worst first. Several vocabularies are accepted because the
 * envelope's `severity` field is a free string (`info|warn|error` on warnings,
 * but an alert rule may carry its own).
 */
export const SEVERITY_ORDER = [
  'critical',
  'fatal',
  'error',
  'high',
  'severe',
  'warn',
  'warning',
  'medium',
  'moderate',
  'low',
  'info',
  'notice',
];

/**
 * Rank for a severity string. An absent or unrecognised severity is NOT
 * promoted to critical and NOT demoted to info — it sorts after everything
 * known, and the row is labelled "unclassified" so nobody reads a rank the
 * backend never assigned.
 */
export function severityRank(value) {
  if (value === null || value === undefined) return SEVERITY_ORDER.length;
  const index = SEVERITY_ORDER.indexOf(String(value).toLowerCase());
  return index === -1 ? SEVERITY_ORDER.length : index;
}

/** Which field, if any, actually carries a severity. */
export function anomalySeverity(row) {
  const raw = row?.severity ?? row?.level ?? row?.priority ?? null;
  return raw === null || raw === undefined ? null : String(raw).toLowerCase();
}

/**
 * Severity first, then most recent.
 *
 * `stable` in the sense that rows the backend did not classify keep their
 * server order relative to each other — that order is `fired_at desc`, which
 * is the registry's declared default sort.
 */
export function sortAnomalies(rows) {
  return (rows ?? [])
    .map((row, index) => ({ row, index }))
    .sort((a, b) => {
      const bySeverity =
        severityRank(anomalySeverity(a.row)) - severityRank(anomalySeverity(b.row));
      if (bySeverity !== 0) return bySeverity;
      const timeA = Date.parse(a.row?.fired_at ?? '');
      const timeB = Date.parse(b.row?.fired_at ?? '');
      if (Number.isFinite(timeA) && Number.isFinite(timeB) && timeA !== timeB) {
        return timeB - timeA;
      }
      return a.index - b.index;
    })
    .map((entry) => entry.row);
}

/** True when the backend told us this alert has been dealt with. */
export function anomalyState(row) {
  const raw = row?.status ?? row?.state ?? null;
  if (raw === null || raw === undefined) return 'open';
  const value = String(raw).toLowerCase();
  if (['resolved', 'closed', 'fixed'].includes(value)) return 'resolved';
  if (['acknowledged', 'ack', 'acked', 'muted'].includes(value)) return 'acknowledged';
  return 'open';
}

/** Cohorts -------------------------------------------------------------- */

/**
 * Pivot the long cohort rows into a retention triangle.
 *
 * Three distinct cell states come out of this, and keeping them apart is the
 * point:
 *
 *  - `absent`  — no row at all. That period has not happened yet for this
 *                cohort; it is outside the triangle and gets no cell content.
 *  - `empty`   — a row exists, `cohort_size` is 0 and `retention_pct` is null.
 *                Nobody was acquired that month, so there is no rate. Printing
 *                0% would put a cohort that does not exist on the heatmap as
 *                the worst-performing one.
 *  - `value`   — a real percentage, including a real 0%.
 */
export function pivotCohorts(rows) {
  const byMonth = new Map();
  const periods = new Set();

  for (const row of rows ?? []) {
    const month = row?.cohort_month;
    if (month === null || month === undefined) continue;
    const key = String(month);
    const index = Number(row?.period_index);
    if (!Number.isInteger(index)) continue;
    periods.add(index);
    if (!byMonth.has(key)) byMonth.set(key, new Map());
    byMonth.get(key).set(index, row);
  }

  const periodList = [...periods].sort((a, b) => a - b);
  const months = [...byMonth.keys()].sort();

  const grid = months.map((month) => {
    const cells = byMonth.get(month);
    const anchor = cells.get(periodList[0]) ?? [...cells.values()][0];
    const size = Number(anchor?.cohort_size);
    return {
      month,
      cohortSize: Number.isFinite(size) ? size : null,
      cells: periodList.map((index) => {
        const row = cells.get(index);
        if (!row) return { periodIndex: index, state: 'absent' };
        const pct = row.retention_pct;
        if (isMissing(pct)) {
          return {
            periodIndex: index,
            state: 'empty',
            cohortSize: Number(row.cohort_size) || 0,
            active: Number(row.active_customers) || 0,
            reason:
              Number(row.cohort_size) === 0
                ? 'no customers were acquired in this cohort, so it has no retention rate'
                : 'retention could not be computed for this cell',
          };
        }
        return {
          periodIndex: index,
          state: 'value',
          retention: Number(pct),
          cohortSize: Number(row.cohort_size) || 0,
          active: Number(row.active_customers) || 0,
        };
      }),
    };
  });

  return { grid, periods: periodList, months };
}

/**
 * 0..1 tint strength for a retention percentage.
 *
 * Scaled against the largest observed value rather than against 100, because
 * an ecommerce retention triangle rarely exceeds 30% after month 1 and a
 * 0-100 ramp renders the entire chart as one flat colour.
 */
export function retentionIntensity(pct, max) {
  if (isMissing(pct)) return 0;
  const ceiling = Number.isFinite(max) && max > 0 ? max : 100;
  const ratio = Number(pct) / ceiling;
  if (!Number.isFinite(ratio)) return 0;
  return Math.min(1, Math.max(0, ratio));
}

/** Largest retention value on the grid, or `null` when there are none. */
export function maxRetention(grid) {
  let max = null;
  for (const row of grid ?? []) {
    for (const cell of row.cells ?? []) {
      if (cell.state !== 'value') continue;
      if (max === null || cell.retention > max) max = cell.retention;
    }
  }
  return max;
}

/** Funnel --------------------------------------------------------------- */

/**
 * Step rows with the drop between them.
 *
 * `dropped` is `null` whenever either side is missing — a drop-off computed
 * against an unknown previous step is a made-up number, and it is the kind
 * that gets screenshotted.
 */
export function funnelSteps(points) {
  const rows = Array.isArray(points) ? points : [];
  const first = rows[0]?.users;
  let previous = null;

  return rows.map((row) => {
    const users = isMissing(row?.users) ? null : Number(row.users);
    const dropped =
      users === null || previous === null ? null : Math.max(0, previous - users);
    const widthPct =
      users === null || isMissing(first) || Number(first) <= 0
        ? null
        : (users / Number(first)) * 100;
    const out = {
      step: row?.step ?? row?.step_key ?? '',
      stepKey: row?.step_key ?? null,
      users,
      dropped,
      conversionRate: isMissing(row?.conversion_rate) ? null : Number(row.conversion_rate),
      conversionFromFirst: isMissing(row?.conversion_from_first)
        ? null
        : Number(row.conversion_from_first),
      widthPct,
    };
    previous = users;
    return out;
  });
}

/** RFM ------------------------------------------------------------------ */

/**
 * Parse an RFM cell label into `{ r, f }` scores, or `null`.
 *
 * Accepts `R5F4`, `5-4`, `54`, `R5F4M3`. Returns null rather than guessing
 * when the label does not yield two 1-5 scores: an unparsed customer is
 * reported as unplaced, never dropped into the nearest cell.
 */
export function parseRfmCell(cell) {
  if (cell === null || cell === undefined) return null;
  const digits = String(cell).match(/\d/g);
  if (!digits || digits.length < 2) return null;
  const r = Number(digits[0]);
  const f = Number(digits[1]);
  if (!(r >= 1 && r <= 5) || !(f >= 1 && f <= 5)) return null;
  return { r, f };
}

/**
 * A 5x5 recency-by-frequency grid of customer counts and monetary totals.
 *
 * Cells with no customers are `count: 0` — that IS a measured zero, because
 * every customer in the table was placed somewhere. Customers whose cell label
 * did not parse are returned in `unplaced` and are excluded from the grid, so
 * the grid's counts always sum to `placed` and never silently to less.
 */
export function buildRfmGrid(rows) {
  const cells = new Map();
  const unplaced = [];
  let placed = 0;

  for (const row of rows ?? []) {
    const parsed = parseRfmCell(row?.cell ?? row?.rfm_cell ?? row?.segment);
    if (!parsed) {
      unplaced.push(row);
      continue;
    }
    const key = `${parsed.r}-${parsed.f}`;
    const bucket = cells.get(key) ?? { r: parsed.r, f: parsed.f, count: 0, monetary: 0 };
    bucket.count += 1;
    const monetary = Number(row?.monetary);
    if (Number.isFinite(monetary)) bucket.monetary += monetary;
    cells.set(key, bucket);
    placed += 1;
  }

  const grid = [];
  for (let r = 5; r >= 1; r -= 1) {
    const rowCells = [];
    for (let f = 1; f <= 5; f += 1) {
      rowCells.push(cells.get(`${r}-${f}`) ?? { r, f, count: 0, monetary: 0 });
    }
    grid.push({ r, cells: rowCells });
  }

  const maxCount = Math.max(0, ...[...cells.values()].map((cell) => cell.count));
  return { grid, unplaced, placed, maxCount, buildable: placed > 0 };
}
