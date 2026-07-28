import { useId } from 'react';
import { Layers } from 'lucide-react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
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
  compactTick,
  DELTA_COLORS,
  GRID_PROPS,
  TOOLTIP_LABEL_STYLE,
  TOOLTIP_STYLE,
} from './chartTokens.js';
import { toWaterfallBars } from './seriesGuards.js';
import { ChartDataTable, ChartFrame, ChartNote } from './ChartA11y.jsx';

/**
 * A bridge: signed steps floating from a running subtotal, with explicit
 * totals re-anchored to the axis.
 *
 * Drawn as a stacked bar chart — an invisible `base` bar carries each step up
 * to its starting height, and a coloured `span` bar draws the step itself.
 *
 * A waterfall is a chain, and `toWaterfallBars` refuses to carry the running
 * total across a step it could not compute: the missing step and everything
 * after it come back flagged, and are drawn as hatched placeholders rather
 * than as bars at confidently-wrong heights. An explicit total re-anchors the
 * chain, because a total is measured from zero rather than from its
 * predecessor.
 */
export function WaterfallChart({
  data,
  xKey = 'step',
  valueKey = 'amount',
  totalKey = 'is_total',
  format = 'money',
  title = 'Waterfall',
  height = 'h-72',
  emptyHint,
}) {
  const uid = useId().replace(/:/g, '');
  const bars = toWaterfallBars(data, { xKey, valueKey, totalKey });
  const drawable = bars.filter((bar) => !bar.missing);

  if (bars.length === 0 || drawable.length === 0) {
    return (
      <EmptyState
        icon={Layers}
        size="sm"
        bordered={false}
        title={bars.length === 0 ? 'No bridge to draw' : 'No step could be placed'}
        description={
          emptyHint ||
          (bars.length === 0
            ? 'No steps were returned for the selected period.'
            : 'Every step came back as not computable, so no bar can be positioned.')
        }
      />
    );
  }

  const span = Math.max(
    1,
    ...drawable.map((bar) => Math.abs(bar.end ?? 0)),
    ...drawable.map((bar) => Math.abs(bar.start ?? 0)),
  );
  const rows = bars.map((bar) => ({
    ...bar,
    // The hatch is a full-height marker, not a value — it says "this step
    // exists and we could not place it", which a zero-height bar cannot say.
    __gap: bar.missing ? span : null,
  }));

  const tableId = `chart-data-${uid}`;
  const missing = bars.filter((bar) => bar.missing);

  return (
    <div>
      <ChartFrame
        height={height}
        label={
          `${title}: waterfall of ${bars.length} step${bars.length === 1 ? '' : 's'}.` +
          (missing.length > 0
            ? ` ${missing.length} step${missing.length === 1 ? '' : 's'} could not ` +
              'be placed and are shown hatched, not as zero.'
            : '')
        }
      >
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
            <defs>
              <pattern
                id={`wf-gap-${uid}`}
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
            <CartesianGrid {...GRID_PROPS} />
            <XAxis dataKey={xKey} {...AXIS_PROPS} interval={0} height={44} />
            <YAxis {...AXIS_PROPS} tickFormatter={compactTick} />
            <ReferenceLine y={0} stroke="var(--line-strong)" strokeWidth={1} />
            <Tooltip
              cursor={{ fill: 'var(--fill)' }}
              contentStyle={TOOLTIP_STYLE}
              labelStyle={TOOLTIP_LABEL_STYLE}
              formatter={(value, name, entry) => {
                const bar = entry?.payload;
                if (name === '__gap') {
                  return [bar?.reason || 'not computable', 'No value'];
                }
                return [
                  formatValue(bar?.value, format),
                  bar?.isTotal ? 'Total' : 'Step',
                ];
              }}
            />
            <Bar
              dataKey="base"
              stackId="wf"
              fill="transparent"
              tooltipType="none"
              isAnimationActive={false}
            />
            <Bar dataKey="span" stackId="wf" radius={[3, 3, 0, 0]} {...animationProps()}>
              {rows.map((bar, index) => (
                <Cell
                  key={`${bar.label}-${index}`}
                  fill={
                    bar.isTotal
                      ? DELTA_COLORS.total
                      : (bar.value ?? 0) < 0
                        ? DELTA_COLORS.negative
                        : DELTA_COLORS.positive
                  }
                />
              ))}
            </Bar>
            <Bar
              dataKey="__gap"
              fill={`url(#wf-gap-${uid})`}
              isAnimationActive={false}
            />
          </BarChart>
        </ResponsiveContainer>
      </ChartFrame>

      <ChartDataTable
        id={tableId}
        caption={`${title} — data table. Steps in order, with running total.`}
        xKey={xKey}
        xLabel="Step"
        rows={bars}
        keys={['value', 'end']}
        labels={{ value: 'Step amount', end: 'Running total' }}
        format={format}
      />

      {missing.length > 0 && (
        <ChartNote tone="warning">
          {`${missing.length} step${missing.length === 1 ? '' : 's'} could not be ` +
            'placed and are hatched, not zeroed: ' +
            missing.map((bar) => `${bar.label} (${bar.reason})`).join('; ') +
            '. A bridge cannot carry a running total across a step it does not have.'}
        </ChartNote>
      )}
    </div>
  );
}

export default WaterfallChart;
