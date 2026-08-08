import { useId } from 'react';
import { BarChart3 } from 'lucide-react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { formatValue } from '@/features/analytics/format.js';
import {
  animationProps,
  AXIS_PROPS,
  axisLabel,
  colorAt,
  compactTick,
  GRID_PROPS,
  GRID_PROPS_HORIZONTAL_BARS,
  TOOLTIP_LABEL_STYLE,
  TOOLTIP_STYLE,
} from './chartTokens.js';
import {
  chartSummary,
  gapNote,
  normalizeSeries,
  seriesCoverage,
} from './seriesGuards.js';
import { ChartDataTable, ChartFrame, ChartNote } from './ChartA11y.jsx';

const MISSING_MARKER = '__missing__';

/**
 * Categorical bars, vertical (`horizontal={false}`) or horizontal.
 *
 * The zero-versus-gap problem is sharper here than on a line. Recharts draws
 * nothing for a `null` datum, and "nothing" is exactly what a zero-height bar
 * looks like — so a category we could not measure would sit in the chart
 * looking like the worst performer.
 *
 * The fix is to draw the hole on purpose: a category with no value gets a
 * hatched track across the plot area, which is visibly not a bar, plus a named
 * line underneath. A reader can then tell "this category sold nothing" from
 * "this category was not measured", which is the entire job.
 */
export function BarChartCard({
  data,
  xKey = 'category',
  keys,
  labels = {},
  format = 'int',
  formats = {},
  horizontal = false,
  stacked = false,
  title = 'Chart',
  height = 'h-64',
  emptyHint,
  categoryWidth = 96,
  // Horizontal bars rank categories against each other, and the house style
  // (see AdminSalesAnalyticsPage) tints them. A vertical time-series bar keeps
  // one colour — recolouring consecutive days would imply a grouping.
  colorByCategory,
}) {
  const uid = useId().replace(/:/g, '');
  const measures = Array.isArray(keys) ? keys : [keys].filter(Boolean);
  const base = normalizeSeries(data, xKey, measures);
  const coverage = seriesCoverage(base, measures);

  if (base.length === 0 || coverage.allMissing) {
    return (
      <EmptyState
        icon={BarChart3}
        size="sm"
        bordered={false}
        title={base.length === 0 ? 'Nothing to plot yet' : 'No value could be computed'}
        description={
          emptyHint ||
          (base.length === 0
            ? 'No category has data for the selected period.'
            : 'Every category came back as not computable. Bars of zero height ' +
              'would read as measured zeros.')
        }
      />
    );
  }

  // Only single-measure charts get the hatched marker: on a stacked or
  // multi-series chart a full-width band would sit on top of the other series
  // and be more confusing than the hole it explains.
  const single = measures.length === 1;
  const domainMax = Math.max(
    0,
    ...base.flatMap((row) => measures.map((key) => row[key] ?? 0)),
  );
  const rows = base.map((row) => ({
    ...row,
    [MISSING_MARKER]:
      single && row[measures[0]] === null ? domainMax || 1 : null,
  }));

  const missingCategories = base
    .filter((row) => measures.every((key) => row[key] === null))
    .map((row) => String(row[xKey]));

  const tableId = `chart-data-${uid}`;
  const anim = animationProps();
  const radius = horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0];
  const tintCells = single && (colorByCategory ?? horizontal);

  return (
    <div>
      <ChartFrame
        height={height}
        label={chartSummary(title, coverage, horizontal ? 'horizontal bar chart' : 'bar chart')}
      >
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={rows}
            layout={horizontal ? 'vertical' : 'horizontal'}
            margin={{ top: 8, right: 8, left: horizontal ? 4 : -18, bottom: 0 }}
            barCategoryGap="26%"
          >
            <defs>
              <pattern
                id={`gap-${uid}`}
                width={6}
                height={6}
                patternUnits="userSpaceOnUse"
                patternTransform="rotate(45)"
              >
                <rect width={6} height={6} fill="var(--fill)" />
                <line
                  x1={0}
                  y1={0}
                  x2={0}
                  y2={6}
                  stroke="var(--line-strong)"
                  strokeWidth={1.5}
                />
              </pattern>
            </defs>
            <CartesianGrid
              {...(horizontal ? GRID_PROPS_HORIZONTAL_BARS : GRID_PROPS)}
            />
            {/* Axes are separate expressions rather than one fragment: recharts
                discovers them by walking its own children, and not putting a
                wrapper in that path is one less thing to be surprised by. */}
            {horizontal ? (
              <XAxis type="number" {...AXIS_PROPS} tickFormatter={compactTick} />
            ) : (
              <XAxis
                dataKey={xKey}
                {...AXIS_PROPS}
                minTickGap={12}
                tickFormatter={(value) => axisLabel(value)}
              />
            )}
            {horizontal ? (
              <YAxis
                type="category"
                dataKey={xKey}
                width={categoryWidth}
                {...AXIS_PROPS}
                tickFormatter={(value) => axisLabel(value)}
              />
            ) : (
              <YAxis type="number" {...AXIS_PROPS} tickFormatter={compactTick} />
            )}
            <Tooltip
              cursor={{ fill: 'var(--fill)' }}
              contentStyle={TOOLTIP_STYLE}
              labelStyle={TOOLTIP_LABEL_STYLE}
              labelFormatter={(value) => axisLabel(value, { long: true })}
              formatter={(value, name) =>
                name === MISSING_MARKER
                  ? ['not computable', 'No value']
                  : [formatValue(value, formats[name] ?? format), labels[name] ?? name]
              }
            />
            {measures.length > 1 && (
              <Legend
                wrapperStyle={{ fontSize: 11, color: 'var(--ink-tertiary)' }}
                formatter={(name) => labels[name] ?? name}
              />
            )}
            {single && (
              <Bar
                dataKey={MISSING_MARKER}
                fill={`url(#gap-${uid})`}
                radius={radius}
                maxBarSize={horizontal ? 18 : 44}
                isAnimationActive={false}
                legendType="none"
              />
            )}
            {measures.map((key, index) => (
              <Bar
                key={key}
                dataKey={key}
                stackId={stacked ? 'stack' : undefined}
                radius={radius}
                maxBarSize={horizontal ? 18 : 44}
                fill={colorAt(index)}
                {...anim}
              >
                {tintCells &&
                  rows.map((row, cellIndex) => (
                    <Cell
                      key={`${row[xKey]}-${cellIndex}`}
                      fill={colorAt(cellIndex)}
                    />
                  ))}
              </Bar>
            ))}
          </BarChart>
        </ResponsiveContainer>
      </ChartFrame>

      <ChartDataTable
        id={tableId}
        caption={`${title} — data table`}
        xKey={xKey}
        xLabel={xKey}
        rows={base}
        keys={measures}
        labels={labels}
        format={format}
        formats={formats}
      />

      <ChartNote>{gapNote(coverage)}</ChartNote>
      {missingCategories.length > 0 && (
        <ChartNote>
          {`Hatched, not zero: ${missingCategories.slice(0, 6).join(', ')}` +
            `${missingCategories.length > 6 ? ` and ${missingCategories.length - 6} more` : ''}` +
            ' could not be measured in this window.'}
        </ChartNote>
      )}
    </div>
  );
}

export default BarChartCard;
