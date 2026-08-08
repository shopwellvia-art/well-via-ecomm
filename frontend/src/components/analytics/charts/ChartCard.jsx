import { CircleSlash, HelpCircle } from 'lucide-react';
import { Card, CardHeader } from '@/components/ui/Card.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn } from '@/lib/utils.js';
import { CHART_TYPES } from './chartTokens.js';
import { specSeries } from './seriesGuards.js';
import { LineOrAreaChart } from './LineOrAreaChart.jsx';
import { BarChartCard } from './BarChartCard.jsx';
import { PieDonutChart } from './PieDonutChart.jsx';
import { WaterfallChart } from './WaterfallChart.jsx';
import { ScatterChart } from './ScatterChart.jsx';
import { Sparkline } from './Sparkline.jsx';

/** Registry `span` → wrapper height. Wide charts get more room, not more ink. */
const HEIGHT_BY_SPAN = { 1: 'h-64', 2: 'h-72', 3: 'h-80' };

/**
 * One chart, chosen from a registry `ChartSpec`.
 *
 * The spec is server-owned metadata (`{ id, title, type, x, series, format,
 * span, empty_hint }`), so nothing here is chosen by the client except the
 * pixels.
 *
 * Three states this deliberately keeps apart, because they mean different
 * things and collapsing them is how a dashboard starts lying:
 *
 *  - **the series was not returned at all** (`undefined`). Several resolvers
 *    omit a series on purpose — `TrackingHealthResolver` will not emit
 *    `event_volume` because an empty axis would read as "zero events received"
 *    rather than "we cannot see events". So an absent series says so, and
 *    never renders a chart frame.
 *  - **the series was returned and is empty** (`[]`). Measured, nothing in it.
 *  - **the series has points, some of which are holes.** That is the chart
 *    components' problem, and they draw the holes as holes.
 */
export function ChartCard({
  spec,
  data,
  title,
  format,
  height,
  className,
  action,
  footnote,
  bordered = true,
  labels = {},
  emptyHint,
}) {
  const heading = title ?? spec?.title ?? 'Chart';
  const type = spec?.type ?? 'line';
  const xKey = spec?.x ?? 'date';
  const keys = specSeries(spec);
  const fmt = format ?? spec?.format ?? 'int';
  const wrapperHeight = height ?? HEIGHT_BY_SPAN[spec?.span] ?? 'h-64';
  const hint = emptyHint ?? spec?.empty_hint ?? '';

  const body = renderBody({
    type,
    data,
    xKey,
    keys,
    fmt,
    heading,
    wrapperHeight,
    hint,
    labels,
  });

  if (!bordered) {
    return (
      <div className={className}>
        {body}
        {footnote && (
          <p className="mt-2 text-xs text-ink-tertiary">{footnote}</p>
        )}
      </div>
    );
  }

  return (
    <Card className={cn('overflow-hidden', className)}>
      <CardHeader title={heading} action={action} />
      <div className="p-5 pt-4">
        {body}
        {footnote && (
          <p className="mt-2 text-xs text-ink-tertiary">{footnote}</p>
        )}
      </div>
    </Card>
  );
}

function renderBody({ type, data, xKey, keys, fmt, heading, wrapperHeight, hint, labels }) {
  // Absent is not empty. See the component docstring.
  if (data === undefined || data === null) {
    return (
      <EmptyState
        icon={CircleSlash}
        size="sm"
        bordered={false}
        title="Not returned for this view"
        description={
          hint ||
          'The server did not send this series. That is not a zero — an empty ' +
            'axis would read as a measurement nobody took.'
        }
      />
    );
  }

  const shared = {
    data,
    xKey,
    keys,
    format: fmt,
    title: heading,
    height: wrapperHeight,
    emptyHint: hint,
    labels,
  };

  switch (type) {
    case 'line':
      return <LineOrAreaChart {...shared} variant="line" />;
    case 'area':
      return <LineOrAreaChart {...shared} variant="area" />;
    case 'bar':
      return <BarChartCard {...shared} horizontal={false} />;
    case 'stacked-bar':
      return <BarChartCard {...shared} horizontal={false} stacked />;
    case 'hbar':
      return <BarChartCard {...shared} horizontal />;
    case 'waterfall':
      return (
        <WaterfallChart
          data={data}
          xKey={xKey}
          valueKey={keys[0] ?? 'amount'}
          format={fmt}
          title={heading}
          height={wrapperHeight}
          emptyHint={hint}
        />
      );
    case 'donut':
    case 'pie':
      return (
        <PieDonutChart
          data={data}
          labelKey={xKey}
          valueKey={keys[0] ?? 'value'}
          format={fmt}
          title={heading}
          height={wrapperHeight}
          emptyHint={hint}
          donut={type === 'donut'}
        />
      );
    case 'scatter':
      return (
        <ScatterChart
          data={data}
          xKey={xKey}
          yKey={keys[0] ?? 'value'}
          zKey={keys[2]}
          nameKey={keys[1]}
          xLabel={labels[xKey] ?? xKey}
          yLabel={labels[keys[0]] ?? keys[0]}
          yFormat={fmt}
          title={heading}
          height={wrapperHeight}
          emptyHint={hint}
        />
      );
    case 'sparkline':
      return (
        <Sparkline
          data={data}
          xKey={xKey}
          valueKey={keys[0] ?? 'value'}
          format={fmt}
          label={heading}
          height={wrapperHeight}
        />
      );
    default:
      // A registry type this build does not know how to draw is a wiring gap.
      // Falling back to a line would publish a shape the spec never asked for.
      return (
        <EmptyState
          icon={HelpCircle}
          size="sm"
          bordered={false}
          title={`No renderer for chart type "${type}"`}
          description={
            `This build draws: ${CHART_TYPES.join(', ')}. Guessing a substitute ` +
            'would show the data in a shape the view did not ask for.'
          }
        />
      );
  }
}

export default ChartCard;
