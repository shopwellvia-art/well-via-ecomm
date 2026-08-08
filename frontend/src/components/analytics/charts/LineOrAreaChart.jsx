import { useId } from 'react';
import { TrendingUp } from 'lucide-react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
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
  TOOLTIP_LABEL_STYLE,
  TOOLTIP_STYLE,
} from './chartTokens.js';
import {
  chartSummary,
  gapNote,
  isolatedIndexes,
  normalizeSeries,
  seriesCoverage,
} from './seriesGuards.js';
import { ChartDataTable, ChartFrame, ChartNote } from './ChartA11y.jsx';

/**
 * Time series as a line or an area.
 *
 * The whole point of this component is the gap handling. `connectNulls` is
 * `false` and is not a prop: a caller who could turn it on would be able to
 * draw a straight, confident line across days that were never aggregated, and
 * that line is indistinguishable from measurement. A hole in the data is a
 * hole in the line.
 *
 * Two consequences fall out of that and are handled here rather than left to
 * whoever notices them:
 *
 *  - an isolated point (holes on both sides) has no segment to draw, so
 *    recharts renders nothing for it. Those indexes get an explicit dot, or a
 *    real measurement would silently vanish.
 *  - a break is easy to read as a rendering artefact, so the count of holes is
 *    stated in words underneath.
 */
export function LineOrAreaChart({
  data,
  xKey = 'date',
  keys,
  labels = {},
  format = 'int',
  formats = {},
  variant = 'line',
  title = 'Chart',
  height = 'h-64',
  emptyHint,
  stacked = false,
}) {
  const uid = useId().replace(/:/g, '');
  const measures = Array.isArray(keys) ? keys : [keys].filter(Boolean);
  const rows = normalizeSeries(data, xKey, measures);
  const coverage = seriesCoverage(rows, measures);

  if (rows.length === 0 || coverage.allMissing) {
    return (
      <EmptyState
        icon={TrendingUp}
        size="sm"
        bordered={false}
        title={rows.length === 0 ? 'Nothing to plot yet' : 'No value could be computed'}
        description={
          emptyHint ||
          (rows.length === 0
            ? 'This series has no points for the selected period.'
            : 'Every point in this series came back as not computable, so there is ' +
              'nothing to draw. An axis with no line would read as a flat zero.')
        }
      />
    );
  }

  const isArea = variant === 'area';
  const Root = isArea ? AreaChart : LineChart;
  const tableId = `chart-data-${uid}`;
  const note = gapNote(coverage);
  const anim = animationProps();

  // A measure that is entirely absent is named rather than drawn: an empty
  // legend entry invites the reader to hunt for a line that was never there.
  const drawn = measures.filter((key) => coverage.per[key].present > 0);
  const absent = coverage.emptyKeys;

  return (
    <div>
      <ChartFrame
        height={height}
        label={chartSummary(title, coverage, isArea ? 'area chart' : 'line chart')}
      >
        <ResponsiveContainer width="100%" height="100%">
          <Root data={rows} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
            {isArea && (
              <defs>
                {drawn.map((key, index) => (
                  <linearGradient
                    key={key}
                    id={`fill-${uid}-${index}`}
                    x1="0"
                    y1="0"
                    x2="0"
                    y2="1"
                  >
                    <stop offset="0%" stopColor={colorAt(index)} stopOpacity={0.26} />
                    <stop offset="100%" stopColor={colorAt(index)} stopOpacity={0} />
                  </linearGradient>
                ))}
              </defs>
            )}
            <CartesianGrid {...GRID_PROPS} />
            <XAxis
              dataKey={xKey}
              {...AXIS_PROPS}
              minTickGap={36}
              tickFormatter={(value) => axisLabel(value)}
            />
            <YAxis {...AXIS_PROPS} tickFormatter={compactTick} />
            <Tooltip
              contentStyle={TOOLTIP_STYLE}
              labelStyle={TOOLTIP_LABEL_STYLE}
              labelFormatter={(value) => axisLabel(value, { long: true })}
              formatter={(value, name) => [
                formatValue(value, formats[name] ?? format),
                labels[name] ?? name,
              ]}
            />
            {drawn.length > 1 && (
              <Legend
                wrapperStyle={{ fontSize: 11, color: 'var(--ink-tertiary)' }}
                formatter={(name) => labels[name] ?? name}
              />
            )}
            {drawn.map((key, index) => {
              const color = colorAt(index);
              const isolated = isolatedIndexes(rows, key);
              const dot = (props) => {
                const { cx, cy, index: pointIndex } = props;
                if (!isolated.has(pointIndex) || cx == null || cy == null) {
                  return <g key={`${key}-gap-${pointIndex}`} />;
                }
                return (
                  <circle
                    key={`${key}-dot-${pointIndex}`}
                    cx={cx}
                    cy={cy}
                    r={3}
                    fill={color}
                    stroke="none"
                  />
                );
              };
              const shared = {
                key,
                type: 'monotone',
                dataKey: key,
                stroke: color,
                strokeWidth: 2,
                // Not configurable. A gap is a gap.
                connectNulls: false,
                dot,
                activeDot: { r: 4, fill: color, strokeWidth: 0 },
                ...anim,
              };
              return isArea ? (
                <Area
                  {...shared}
                  fill={`url(#fill-${uid}-${index})`}
                  stackId={stacked ? 'stack' : undefined}
                />
              ) : (
                <Line {...shared} />
              );
            })}
          </Root>
        </ResponsiveContainer>
      </ChartFrame>

      <ChartDataTable
        id={tableId}
        caption={`${title} — data table`}
        xKey={xKey}
        xLabel={xKey}
        rows={rows}
        keys={measures}
        labels={labels}
        format={format}
        formats={formats}
      />

      <ChartNote>{note}</ChartNote>
      {absent.length > 0 && (
        <ChartNote tone="warning">
          {`Not drawn: ${absent
            .map((key) => labels[key] ?? key)
            .join(', ')} — no point in this window could be computed.`}
        </ChartNote>
      )}
    </div>
  );
}

export default LineOrAreaChart;
