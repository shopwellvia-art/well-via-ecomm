import { cn } from '@/lib/utils.js';
import { formatValue } from '@/features/analytics/format.js';
import { MISSING_DASH } from './seriesGuards.js';

/**
 * The text half of every chart.
 *
 * An SVG full of `<path>` elements is not data to a screen reader, and it is
 * not data to anyone copying a figure into an email either. Every chart in this
 * directory renders one of these alongside it, visually hidden, holding exactly
 * the numbers that were plotted — including the holes, which appear as an
 * em-dash and are announced as "not computable" rather than read as zero.
 *
 * `sr-only` rather than `display:none`: hidden content is skipped by assistive
 * technology, which would make the chart unreachable rather than accessible.
 */
export function ChartDataTable({
  id,
  caption,
  xKey,
  xLabel = 'Point',
  rows,
  keys,
  labels = {},
  format = 'int',
  formats = {},
  className,
}) {
  const measures = Array.isArray(keys) ? keys : [keys];
  return (
    <table id={id} className={cn('sr-only', className)}>
      <caption>{caption}</caption>
      <thead>
        <tr>
          <th scope="col">{xLabel}</th>
          {measures.map((key) => (
            <th key={key} scope="col">
              {labels[key] ?? key}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {(rows ?? []).map((row, index) => (
          <tr key={`${row?.[xKey] ?? 'row'}-${index}`}>
            <th scope="row">{String(row?.[xKey] ?? MISSING_DASH)}</th>
            {measures.map((key) => {
              const value = row?.[key];
              const missing = value === null || value === undefined;
              return (
                <td key={key}>
                  {missing
                    ? `${MISSING_DASH} (not computable)`
                    : formatValue(value, formats[key] ?? format)}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * A visible one-line caveat under a chart.
 *
 * `tone="gap"` is the default and is used for the "n values are drawn as a gap"
 * note. It is visible, not sr-only: a break in a line reads as a rendering
 * glitch to a sighted reader, who then mentally joins it up.
 */
export function ChartNote({ children, tone = 'gap', className }) {
  if (!children) return null;
  return (
    <p
      className={cn(
        'mt-2 text-xs leading-relaxed',
        tone === 'warning' ? 'text-warning' : 'text-ink-tertiary',
        className,
      )}
    >
      {children}
    </p>
  );
}

/**
 * The fixed-height wrapper every `ResponsiveContainer` needs.
 *
 * ResponsiveContainer measures its parent; a parent with no height measures
 * zero and the chart silently does not render. Centralised so no chart in here
 * can forget it.
 *
 * `role="img"` with a summarising `aria-label` collapses the SVG's hundreds of
 * path elements into one announcement. The numbers are not lost: the sr-only
 * `ChartDataTable` sits immediately after and is navigable as a real table,
 * which is far more usable than the same content flattened into an
 * `aria-describedby` string.
 */
export function ChartFrame({ height = 'h-64', label, describedBy, children, className }) {
  return (
    <div
      className={cn('w-full', height, className)}
      role="img"
      aria-label={label}
      aria-describedby={describedBy}
    >
      {children}
    </div>
  );
}
