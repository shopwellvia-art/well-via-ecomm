import { useState } from 'react';
import { AlertCircle, AlertTriangle, ChevronDown, Info } from 'lucide-react';
import { toCoverage } from './QualityBadge.jsx';
import { cn } from '@/lib/utils.js';

/**
 * The caveats that came back with the numbers.
 *
 * The server attaches a warning whenever a figure is less than it appears — a
 * stale rollup, a cost rule that did not resolve, a funnel that can only start
 * at the cart because there is no page-view stream. Dropping these on the floor
 * would leave a chart that looks complete and is not, which is worse than an
 * empty one because nobody goes looking for the caveat they were never shown.
 *
 * Rendered above the data, not below it. A caveat found after the reader has
 * already drawn a conclusion has done nothing.
 *
 * Props:
 *   warnings     — envelope.warnings: [{ code, severity, message, detail }]
 *   coveragePct  — envelope.coverage_pct (0..100), or null when not measured
 *   isPartial    — envelope.is_partial
 *   limitation   — the view's declared limitation, shown when partial
 *   collapseAfter — how many rows to show before "N more" (default 2)
 */

const SEVERITY = {
  error: { tone: 'danger', icon: AlertCircle },
  warn: { tone: 'warning', icon: AlertTriangle },
  info: { tone: 'info', icon: Info },
};

const TONE_CLASS = {
  danger: 'border-danger/30 bg-danger/8 text-danger',
  warning: 'border-warning/30 bg-warning/8 text-warning',
  info: 'border-info/30 bg-info/8 text-info',
};

/** Highest severity present drives the summary row's tone. */
const SEVERITY_RANK = { info: 0, warn: 1, error: 2 };

function Row({ severity, message, detail }) {
  const meta = SEVERITY[severity] ?? SEVERITY.warn;
  const Icon = meta.icon;
  const detailPairs =
    detail && typeof detail === 'object' ? Object.entries(detail) : [];

  return (
    <li
      className={cn(
        'flex items-start gap-2.5 rounded-sm border px-3 py-2 text-xs leading-relaxed',
        TONE_CLASS[meta.tone],
      )}
    >
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <div className="min-w-0">
        <p className="text-ink-primary">{message}</p>
        {detailPairs.length > 0 && (
          <p className="mt-0.5 text-[11px] text-ink-tertiary">
            {detailPairs
              .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${formatDetail(v)}`)
              .join(' · ')}
          </p>
        )}
      </div>
    </li>
  );
}

function formatDetail(value) {
  if (value == null) return '—';
  if (Array.isArray(value)) return value.join(', ');
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

export function DataCompletenessWarning({
  warnings = [],
  coveragePct = null,
  isPartial = false,
  limitation = '',
  collapseAfter = 2,
  className,
}) {
  const [expanded, setExpanded] = useState(false);

  const rows = [];

  if (isPartial) {
    rows.push({
      key: 'partial',
      severity: 'warn',
      message:
        limitation ||
        'This view is partial — some of the window it covers has not been ' +
          'aggregated, so treat totals as a floor rather than a final figure.',
    });
  }

  // `toCoverage` and not `Number()`: an unmeasured coverage must not become a
  // banner announcing that 0% of the rows were usable.
  const coverage = toCoverage(coveragePct);
  if (coverage !== null && coverage < 100) {
    rows.push({
      key: 'coverage',
      severity: coverage < 60 ? 'error' : 'warn',
      message:
        `Built from ${coverage.toFixed(0)}% of the underlying rows. The ` +
        'remainder had inputs that could not be resolved and were left out ' +
        'rather than counted as zero.',
    });
  }

  // Server warnings last, de-duplicated by code: the same rollup gap can be
  // reported by several resolvers on one view and saying it three times just
  // trains the reader to skip the banner.
  const seen = new Set();
  for (const w of Array.isArray(warnings) ? warnings : []) {
    if (!w?.message) continue;
    const key = w.code || w.message;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({
      key,
      severity: w.severity || 'warn',
      message: w.message,
      detail: w.detail,
    });
  }

  if (rows.length === 0) return null;

  const visible = expanded ? rows : rows.slice(0, collapseAfter);
  const hidden = rows.length - visible.length;
  const worst = rows.reduce(
    (acc, r) =>
      (SEVERITY_RANK[r.severity] ?? 1) > (SEVERITY_RANK[acc] ?? 1) ? r.severity : acc,
    'info',
  );

  return (
    <section
      className={cn('flex flex-col gap-1.5', className)}
      aria-label="Data completeness notices"
      // `status` rather than `alert`: these arrive with the data on every
      // refresh, and an assertive live region would interrupt a screen-reader
      // user mid-sentence every 60 seconds.
      role="status"
    >
      <ul className="flex flex-col gap-1.5">
        {visible.map((r) => (
          <Row key={r.key} severity={r.severity} message={r.message} detail={r.detail} />
        ))}
      </ul>
      {hidden > 0 && (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className={cn(
            'inline-flex items-center gap-1 self-start rounded-xs px-1.5 py-1',
            'text-[11px] font-medium text-ink-secondary transition-colors',
            'hover:text-ink-primary focus-visible:focus-ring',
            worst === 'error' && 'text-danger',
          )}
        >
          <ChevronDown className="size-3" aria-hidden="true" />
          {hidden} more {hidden === 1 ? 'notice' : 'notices'}
        </button>
      )}
    </section>
  );
}
