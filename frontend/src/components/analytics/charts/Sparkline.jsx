import { useId } from 'react';
import { Line, LineChart, ResponsiveContainer } from 'recharts';
import { cn } from '@/lib/utils.js';
import { formatValue } from '@/features/analytics/format.js';
import { colorAt, MISSING_DASH } from './chartTokens.js';
import {
  isolatedIndexes,
  normalizeSeries,
  seriesCoverage,
} from './seriesGuards.js';

/**
 * A trend glyph for a KPI card: no axes, no grid, no tooltip.
 *
 * Because it has no axis, a sparkline is the easiest chart in the set to lie
 * with — a gap and a dip to the floor look the same when there is no floor
 * drawn. So `connectNulls` is off here too, and the accessible summary spells
 * out how many points are holes. A sparkline whose series is entirely missing
 * renders as a dash, not as a flat line: a flat line is a strong claim about a
 * stable metric.
 */
export function Sparkline({
  data,
  xKey = 'date',
  valueKey = 'value',
  format = 'int',
  label = 'Trend',
  colorIndex = 1,
  className,
  height = 'h-10',
}) {
  const uid = useId().replace(/:/g, '');
  const rows = normalizeSeries(data, xKey, [valueKey]);
  const coverage = seriesCoverage(rows, [valueKey]);

  if (rows.length === 0 || coverage.allMissing) {
    return (
      <span
        className={cn('text-xs text-ink-tertiary', className)}
        title="No trend could be computed for this metric."
      >
        {MISSING_DASH}
      </span>
    );
  }

  const color = colorAt(colorIndex);
  const isolated = isolatedIndexes(rows, valueKey);
  const present = rows.filter((row) => row[valueKey] !== null);
  const first = present[0]?.[valueKey];
  const last = present[present.length - 1]?.[valueKey];

  const summary =
    `${label}: ${present.length} of ${rows.length} points measured, ` +
    `from ${formatValue(first, format)} to ${formatValue(last, format)}.` +
    (coverage.missing > 0
      ? ` ${coverage.missing} point${coverage.missing === 1 ? '' : 's'} not ` +
        'computable and drawn as gaps.'
      : '');

  return (
    <div className={cn('w-full', height, className)}>
      <span id={`spark-${uid}`} className="sr-only">
        {summary}
      </span>
      <div role="img" aria-labelledby={`spark-${uid}`} className="h-full w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 2, right: 2, left: 2, bottom: 2 }}>
            <Line
              type="monotone"
              dataKey={valueKey}
              stroke={color}
              strokeWidth={1.75}
              connectNulls={false}
              isAnimationActive={false}
              dot={(props) => {
                const { cx, cy, index } = props;
                if (!isolated.has(index) || cx == null || cy == null) {
                  return <g key={`sp-gap-${index}`} />;
                }
                return (
                  <circle
                    key={`sp-dot-${index}`}
                    cx={cx}
                    cy={cy}
                    r={2}
                    fill={color}
                    stroke="none"
                  />
                );
              }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export default Sparkline;
