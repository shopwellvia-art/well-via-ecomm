import { Filter } from 'lucide-react';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn } from '@/lib/utils.js';
import { formatValue } from '@/features/analytics/format.js';
import { ChartCard } from '../charts/ChartCard.jsx';
import { MISSING_DASH } from '../charts/seriesGuards.js';
import { CaveatBanner, NotConfiguredPanel, Panel } from './BespokeParts.jsx';
import {
  funnelSteps,
  notConfigured,
  seriesFor,
  warningWith,
} from './bespokeHelpers.js';

/**
 * View 14 — Conversion Funnel.
 *
 * This is NOT a traffic funnel and the component says so before it draws
 * anything. `agg_funnel_daily` begins at product-view and cart events we emit
 * ourselves; there is no server-side record of a visitor who never touched a
 * product, so there is no visitor-level top of funnel and no site-wide
 * conversion rate to be had here.
 *
 * The resolver emits `FUNNEL_STARTS_AT_CART` unconditionally — on empty
 * responses too, because an empty funnel is exactly when someone goes looking
 * for the missing top of it. So it is surfaced as a banner above the chart,
 * with the backend's own wording, rather than as a footnote or a tooltip. A
 * caveat that has to be hovered to be found is a caveat nobody reads.
 *
 * The header deliberately never uses the words "traffic", "visitors" or "site
 * conversion".
 */
export function ConversionFunnelView({ envelope, viewDef }) {
  if (notConfigured(envelope)) {
    return <NotConfiguredPanel envelope={envelope} viewDef={viewDef} />;
  }

  const startsAtCart = warningWith(envelope, 'FUNNEL_STARTS_AT_CART');
  const notBound = warningWith(envelope, 'METRIC_NOT_BOUND');

  const charts = viewDef?.charts ?? [];
  const stepSpec = charts.find((chart) => chart.x === 'step' && chart.type !== 'line');
  const rateSpec = charts.find((chart) => chart.x === 'step' && chart.type === 'line');

  const rawSteps = seriesFor(envelope, stepSpec?.id ?? 'funnel_steps');
  const steps = funnelSteps(rawSteps);

  return (
    <div className="space-y-4">
      <CaveatBanner
        warning={startsAtCart}
        tone="warning"
        title="This funnel starts at the cart, not at a session"
      />
      {notBound && <CaveatBanner warning={notBound} tone="warning" />}

      <Panel
        title="Checkout funnel"
        description={
          'Each bar is the count of events at that step. The steps below are ' +
          'checkout stages, not audience segments — the population above ' +
          'product-view is not observable from this deployment.'
        }
      >
        {rawSteps === undefined ? (
          <EmptyState
            icon={Filter}
            size="sm"
            bordered={false}
            title="No funnel returned for this period"
            description={
              'The funnel rollup holds no rows for this window, so no ladder is ' +
              'drawn. A ladder of zeros would be a picture of a store nobody ' +
              'visited, which is a different claim.'
            }
          />
        ) : steps.length === 0 ? (
          <EmptyState
            icon={Filter}
            size="sm"
            bordered={false}
            title="No steps in this window"
            description="The funnel rollup returned no steps for the selected period."
          />
        ) : (
          <ol className="space-y-2">
            {steps.map((step, index) => (
              <FunnelStep key={step.stepKey ?? step.step} step={step} index={index} />
            ))}
          </ol>
        )}
      </Panel>

      <ChartCard
        spec={
          rateSpec ?? {
            id: 'step_conversion',
            type: 'line',
            x: 'step',
            series: ['conversion_rate'],
            format: 'pct',
            title: 'Step-to-step conversion',
          }
        }
        data={seriesFor(envelope, rateSpec?.id ?? 'step_conversion')}
        labels={{ conversion_rate: 'Conversion from previous step' }}
        footnote={
          'Step-to-step only. The first step has no predecessor, so it has no ' +
          'conversion rate — that is a gap, not 0%.'
        }
      />
    </div>
  );
}

function FunnelStep({ step, index }) {
  const measured = step.users !== null;
  return (
    <li className="rounded-sm border border-line-subtle bg-bg-elevated p-3">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm font-medium text-ink-primary">
          {`${index + 1}. ${step.step}`}
        </span>
        <span className="text-sm tabular-nums text-ink-primary">
          {measured ? formatValue(step.users, 'int') : MISSING_DASH}
        </span>
      </div>

      <div className="mt-2 h-2 w-full overflow-hidden rounded-xs bg-fill">
        {measured && step.widthPct !== null ? (
          <div
            className="h-full rounded-xs bg-accent"
            style={{ width: `${Math.max(1, Math.min(100, step.widthPct))}%` }}
          />
        ) : (
          <div
            className={cn(
              'h-full w-full rounded-xs border border-dashed border-line-strong',
            )}
            title="not computable"
          />
        )}
      </div>

      <p className="mt-1.5 text-xs text-ink-tertiary">
        {step.conversionRate === null ? (
          index === 0 ? (
            'First step — no previous step to convert from.'
          ) : (
            'Conversion from the previous step could not be computed.'
          )
        ) : (
          <>
            {`${formatValue(step.conversionRate, 'pct')} of the previous step`}
            {step.dropped !== null && step.dropped > 0 && (
              <span>{` · ${formatValue(step.dropped, 'int')} dropped here`}</span>
            )}
          </>
        )}
      </p>
    </li>
  );
}

export default ConversionFunnelView;
