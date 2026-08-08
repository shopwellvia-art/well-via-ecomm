import { useId } from 'react';
import { Dot } from 'lucide-react';
import {
  CartesianGrid,
  ResponsiveContainer,
  Scatter,
  ScatterChart as RechartsScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { formatValue } from '@/features/analytics/format.js';
import {
  animationProps,
  AXIS_PROPS,
  colorAt,
  compactTick,
  GRID_PROPS,
  TOOLTIP_LABEL_STYLE,
  TOOLTIP_STYLE,
} from './chartTokens.js';
import { valueAt } from './seriesGuards.js';
import { ChartDataTable, ChartFrame, ChartNote } from './ChartA11y.jsx';

/**
 * Two measures against each other, one dot per row.
 *
 * A dot needs both coordinates. A row missing either one is dropped rather
 * than pinned to an axis at zero — a point sitting on the y-axis is a claim
 * that x was measured and was zero. Dropped rows are counted underneath and
 * still appear in the data table, so the population is never quietly smaller
 * than it looks.
 */
export function ScatterChart({
  data,
  xKey = 'x',
  yKey = 'y',
  zKey,
  nameKey,
  xLabel,
  yLabel,
  xFormat = 'int',
  yFormat = 'int',
  title = 'Chart',
  height = 'h-72',
  emptyHint,
}) {
  const uid = useId().replace(/:/g, '');
  const source = Array.isArray(data) ? data : [];

  const points = [];
  let dropped = 0;
  for (const row of source) {
    const x = valueAt(row, xKey);
    const y = valueAt(row, yKey);
    if (x === null || y === null) {
      dropped += 1;
      continue;
    }
    points.push({
      [xKey]: x,
      [yKey]: y,
      ...(zKey ? { [zKey]: valueAt(row, zKey) ?? 1 } : {}),
      name: nameKey ? row?.[nameKey] : undefined,
    });
  }

  if (points.length === 0) {
    return (
      <EmptyState
        icon={Dot}
        size="sm"
        bordered={false}
        title={dropped > 0 ? 'No point has both coordinates' : 'Nothing to plot yet'}
        description={
          emptyHint ||
          (dropped > 0
            ? `${dropped} row${dropped === 1 ? '' : 's'} are missing one or both ` +
              'measures. Placing them at zero would assert a measurement.'
            : 'No rows were returned for the selected period.')
        }
      />
    );
  }

  const tableId = `chart-data-${uid}`;

  return (
    <div>
      <ChartFrame
        height={height}
        label={
          `${title}: scatter plot of ${points.length} point${points.length === 1 ? '' : 's'}, ` +
          `${yLabel ?? yKey} against ${xLabel ?? xKey}.` +
          (dropped > 0
            ? ` ${dropped} row${dropped === 1 ? '' : 's'} not plotted because a ` +
              'coordinate could not be computed.'
            : '')
        }
      >
        <ResponsiveContainer width="100%" height="100%">
          <RechartsScatterChart margin={{ top: 8, right: 12, left: -12, bottom: 8 }}>
            <CartesianGrid {...GRID_PROPS} vertical />
            <XAxis
              type="number"
              dataKey={xKey}
              name={xLabel ?? xKey}
              {...AXIS_PROPS}
              tickFormatter={compactTick}
            />
            <YAxis
              type="number"
              dataKey={yKey}
              name={yLabel ?? yKey}
              {...AXIS_PROPS}
              tickFormatter={compactTick}
            />
            {zKey && <ZAxis type="number" dataKey={zKey} range={[36, 320]} />}
            <Tooltip
              cursor={{ strokeDasharray: '3 3', stroke: 'var(--line-strong)' }}
              contentStyle={TOOLTIP_STYLE}
              labelStyle={TOOLTIP_LABEL_STYLE}
              formatter={(value, name) => [
                formatValue(value, name === (yLabel ?? yKey) ? yFormat : xFormat),
                name,
              ]}
            />
            <Scatter
              data={points}
              fill={colorAt(1)}
              fillOpacity={0.75}
              {...animationProps()}
            />
          </RechartsScatterChart>
        </ResponsiveContainer>
      </ChartFrame>

      <ChartDataTable
        id={tableId}
        caption={`${title} — data table`}
        xKey={nameKey ?? xKey}
        xLabel={nameKey ? 'Item' : (xLabel ?? xKey)}
        rows={source}
        keys={[xKey, yKey]}
        labels={{ [xKey]: xLabel ?? xKey, [yKey]: yLabel ?? yKey }}
        formats={{ [xKey]: xFormat, [yKey]: yFormat }}
      />

      {dropped > 0 && (
        <ChartNote>
          {`${dropped} row${dropped === 1 ? '' : 's'} not plotted: one or both ` +
            'measures could not be computed, and a dot needs both coordinates.'}
        </ChartNote>
      )}
    </div>
  );
}

export default ScatterChart;
