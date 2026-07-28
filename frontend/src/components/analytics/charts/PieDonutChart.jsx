import { useId } from 'react';
import { PieChart as PieChartIcon } from 'lucide-react';
import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { formatValue } from '@/features/analytics/format.js';
import {
  animationProps,
  colorAt,
  TOOLTIP_LABEL_STYLE,
  TOOLTIP_STYLE,
} from './chartTokens.js';
import { toSlices } from './seriesGuards.js';
import { ChartDataTable, ChartFrame, ChartNote } from './ChartA11y.jsx';

/**
 * Composition as a donut (or a pie when `donut={false}`).
 *
 * A slice is a claim about a share of a whole, so a category whose value could
 * not be computed gets no slice at all — giving it one would require inventing
 * the number, and giving it a zero-width slice would silently shrink every
 * other share's denominator without saying so. Excluded categories are named
 * underneath and appear in the data table as an em-dash.
 *
 * Negative values are excluded on the same grounds: a negative has no angle,
 * and folding its magnitude in would make the donut sum to something that is
 * not the total.
 */
export function PieDonutChart({
  data,
  labelKey = 'label',
  valueKey = 'value',
  format = 'int',
  title = 'Chart',
  height = 'h-64',
  donut = true,
  emptyHint,
}) {
  const uid = useId().replace(/:/g, '');
  const { slices, excluded, total } = toSlices(data, labelKey, valueKey);

  if (slices.length === 0) {
    return (
      <EmptyState
        icon={PieChartIcon}
        size="sm"
        bordered={false}
        title={excluded.length > 0 ? 'No share could be computed' : 'Nothing to plot yet'}
        description={
          emptyHint ||
          (excluded.length > 0
            ? 'Every category came back as not computable, so there is no whole ' +
              'to take a share of.'
            : 'No category has data for the selected period.')
        }
      />
    );
  }

  const tableId = `chart-data-${uid}`;
  const rows = [
    ...slices.map((slice) => ({
      [labelKey]: slice.label,
      [valueKey]: slice.value,
      share: slice.share,
    })),
    ...excluded.map((slice) => ({
      [labelKey]: slice.label,
      [valueKey]: slice.value,
      share: null,
    })),
  ];

  return (
    <div>
      <ChartFrame
        height={height}
        label={
          `${title}: ${donut ? 'donut' : 'pie'} chart of ${slices.length} ` +
          `slice${slices.length === 1 ? '' : 's'}.` +
          (excluded.length > 0
            ? ` ${excluded.length} categor${excluded.length === 1 ? 'y' : 'ies'} ` +
              'have no slice because their value could not be computed.'
            : '')
        }
      >
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={slices}
              dataKey="value"
              nameKey="label"
              innerRadius={donut ? '55%' : 0}
              outerRadius="80%"
              paddingAngle={1}
              stroke="var(--bg-elevated)"
              strokeWidth={2}
              {...animationProps()}
            >
              {slices.map((slice, index) => (
                <Cell key={`${slice.label}-${index}`} fill={colorAt(index)} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={TOOLTIP_STYLE}
              labelStyle={TOOLTIP_LABEL_STYLE}
              formatter={(value, name, entry) => {
                const share = entry?.payload?.share;
                const shown = formatValue(value, format);
                return [
                  share === null || share === undefined
                    ? shown
                    : `${shown} (${share.toFixed(1)}%)`,
                  name,
                ];
              }}
            />
            <Legend
              wrapperStyle={{ fontSize: 11, color: 'var(--ink-tertiary)' }}
            />
          </PieChart>
        </ResponsiveContainer>
      </ChartFrame>

      <ChartDataTable
        id={tableId}
        caption={`${title} — data table. Total ${formatValue(total, format)}.`}
        xKey={labelKey}
        xLabel="Category"
        rows={rows}
        keys={[valueKey, 'share']}
        labels={{ [valueKey]: 'Value', share: 'Share %' }}
        format={format}
        formats={{ share: 'pct' }}
      />

      {excluded.length > 0 && (
        <ChartNote>
          {`Not given a slice: ${excluded
            .map((slice) => `${slice.label} (${slice.reason})`)
            .join(', ')}. A share cannot be drawn for a value we do not have.`}
        </ChartNote>
      )}
    </div>
  );
}

export default PieDonutChart;
