import { formatValue } from '@/features/analytics/format.js';
import { isMissing, trimToWatermark } from '@/features/analytics/viewState.js';
import { ChartCard } from '../charts/ChartCard.jsx';
import {
  CaveatBanner,
  NotConfiguredPanel,
  Panel,
  StatusPill,
} from './BespokeParts.jsx';
import { notConfigured, seriesFor, warningWith } from './bespokeHelpers.js';

/**
 * View 3 — Revenue and Profitability.
 *
 * The waterfall is the revenue bridge, which balances by construction:
 * `gross - discounts + tax + shipping + cod_surcharge - refunds = net_revenue`.
 * That part is safe.
 *
 * The margin trend below it is not, and the difference is the reason this view
 * is bespoke rather than two generic chart cards. Margin exists only for order
 * lines that carried a `unit_cost` snapshot, so the envelope arrives with a
 * `coverage_pct` and, below 100%, a `COST_COVERAGE_LOW` warning. That coverage
 * is put next to the margin chart rather than in a footer, because a CM1 line
 * covering 40% of the business looks exactly like a CM1 line covering all of
 * it.
 *
 * The two margin series are deliberately drawn as two charts. `cm1` is money
 * and `cm1_pct` is a percentage; sharing one y-axis would put a rupee value
 * and a percentage on the same scale, which makes one of them look flat.
 */
export function RevenueWaterfallView({ envelope, viewDef }) {
  if (notConfigured(envelope)) {
    return <NotConfiguredPanel envelope={envelope} viewDef={viewDef} />;
  }

  const charts = viewDef?.charts ?? [];
  const waterfallSpec = charts.find((chart) => chart.type === 'waterfall');
  const trendSpec = charts.find((chart) => chart.x === 'date');
  const watermark = envelope?.last_updated_at?.slice?.(0, 10) ?? null;

  const waterfall = seriesFor(envelope, waterfallSpec?.id ?? 'revenue_waterfall');
  const rawTrend = seriesFor(envelope, trendSpec?.id ?? 'margin_trend');
  const trend =
    rawTrend === undefined ? undefined : trimToWatermark(rawTrend, watermark, 'date');

  const coverage = envelope?.coverage_pct;
  const coverageWarning = warningWith(envelope, 'COST_COVERAGE_LOW');

  return (
    <div className="space-y-4">
      <ChartCard
        spec={
          waterfallSpec ?? {
            id: 'revenue_waterfall',
            type: 'waterfall',
            x: 'step',
            series: ['amount'],
            format: 'money',
            title: 'Gross to contribution',
          }
        }
        data={waterfall}
        footnote={
          'Every step is a term of the revenue bridge, which balances to the paisa ' +
          'by construction. A step that could not be computed is hatched, and the ' +
          'steps after it are not placed at all.'
        }
      />

      <Panel
        title="Contribution margin over time"
        action={<CoveragePill coverage={coverage} />}
        description={
          'Margin covers only order lines that carried a unit-cost snapshot. ' +
          'Uncovered lines are excluded from the cost side, never costed at zero, ' +
          'so a low coverage figure means these lines describe part of the business.'
        }
      >
        {coverageWarning && (
          <CaveatBanner warning={coverageWarning} tone="warning" className="mb-4" />
        )}
        <div className="grid gap-4 md:grid-cols-2">
          <ChartCard
            bordered={false}
            spec={{
              id: 'cm1_money',
              type: 'line',
              x: 'date',
              series: ['cm1'],
              format: 'money',
              title: 'CM1',
            }}
            data={trend}
            labels={{ cm1: 'CM1' }}
          />
          <ChartCard
            bordered={false}
            spec={{
              id: 'cm1_pct',
              type: 'line',
              x: 'date',
              series: ['cm1_pct'],
              format: 'pct',
              title: 'CM1 %',
            }}
            data={trend}
            labels={{ cm1_pct: 'CM1 %' }}
          />
        </div>
        <p className="mt-3 text-xs text-ink-tertiary">
          Drawn as two charts on purpose: CM1 is money and CM1 % is a ratio, and
          one shared axis would flatten whichever of the two has the smaller
          numbers.
        </p>
      </Panel>
    </div>
  );
}

function CoveragePill({ coverage }) {
  if (isMissing(coverage)) {
    return (
      <StatusPill
        tone="unchecked"
        title="No unit was sold in this window, so cost coverage has no denominator."
      >
        cost coverage not measured
      </StatusPill>
    );
  }
  const value = Number(coverage);
  const tone = value >= 100 ? 'success' : value >= 60 ? 'warning' : 'danger';
  return (
    <StatusPill tone={tone}>{`${formatValue(coverage, 'pct')} of units costed`}</StatusPill>
  );
}

export default RevenueWaterfallView;
