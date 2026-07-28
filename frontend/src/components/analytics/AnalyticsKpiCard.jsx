import {
  ArrowDownRight,
  ArrowUpRight,
  Clock,
  Hash,
  IndianRupee,
  Minus,
  Percent,
} from 'lucide-react';
import { KPICard } from '@/components/admin/KPICard.jsx';
import { formatValue, MISSING_DISPLAY } from '@/features/analytics/format.js';
import { isMissing } from '@/features/analytics/viewState.js';
import { FreshnessBadge } from './FreshnessBadge.jsx';
import { MetricTooltip } from './MetricTooltip.jsx';
import { QualityBadge } from './QualityBadge.jsx';
import { humanizeCapability } from './ViewStateGate.jsx';
import { cn } from '@/lib/utils.js';

/**
 * One KPI, with everything needed to judge it.
 *
 * The card body is the existing admin `KPICard` — same tile, same type scale,
 * same skeleton — with an honesty strip fused to its bottom edge. The split is
 * the point: above the line is the measurement, below it is what the
 * measurement is worth. A reader who only ever looks at the big number is no
 * worse off than before; a reader deciding something can see the grade,
 * the coverage, the provenance and the age without leaving the card.
 *
 * Three rules this component exists to keep:
 *
 *  1. A `null` value renders as an em-dash. `format.js` does that, and the
 *     value never touches any other formatter — `formatPrice` would render
 *     `₹0.00` for a figure the backend explicitly refused to compute.
 *
 *  2. An `INCOMPLETE` KPI names what is missing. "Incomplete" alone tells an
 *     admin to distrust the number; "Missing: gateway fee, packaging" tells
 *     them which two settings to go and fill in.
 *
 *  3. `higher_is_better` drives the delta's colour, and when it is `null` the
 *     delta is grey. Roughly a twelfth of the catalogue has no good direction
 *     (average order size, share of new customers); painting those green on a
 *     rise invents a judgement the business never made.
 *
 * Props:
 *   kpi           — the envelope's KpiValue: { value, previous, delta_pct,
 *                   format, quality, coverage_pct, inputs_missing }
 *   kpiDef        — the catalogue entry from registry.contract.json
 *   icon          — Lucide component; falls back to one implied by the unit
 *   lastUpdatedAt — envelope.last_updated_at (the card states its own age)
 *   freshness     — envelope.freshness
 *   loading       — skeleton state
 */

/** Icon + tile tone implied by a KPI's unit, when presentation says nothing. */
const UNIT_STYLE = {
  money: { icon: IndianRupee, tone: 'accent' },
  int: { icon: Hash, tone: 'info' },
  pct: { icon: Percent, tone: 'success' },
  ratio: { icon: Percent, tone: 'success' },
  days: { icon: Clock, tone: 'info' },
  hours: { icon: Clock, tone: 'info' },
};

/**
 * Colour and arrow for a delta.
 *
 * `higherIsBetter === null` and `delta === 0` both land on neutral. Neither is
 * a hedge: an undirected metric has no good side, and a period that did not
 * move did not improve.
 */
export function deltaPresentation(deltaPct, higherIsBetter) {
  const delta = Number(deltaPct);
  if (isMissing(deltaPct) || !Number.isFinite(delta)) return null;

  const Icon = delta > 0 ? ArrowUpRight : delta < 0 ? ArrowDownRight : Minus;
  let tone = 'neutral';
  if (delta !== 0 && higherIsBetter === true) tone = delta > 0 ? 'good' : 'bad';
  else if (delta !== 0 && higherIsBetter === false) tone = delta > 0 ? 'bad' : 'good';

  return { delta, Icon, tone };
}

const DELTA_TONE_CLASS = {
  good: 'bg-success/12 text-success',
  bad: 'bg-danger/12 text-danger',
  neutral: 'bg-fill-strong text-ink-secondary',
};

function DeltaChip({ deltaPct, higherIsBetter, previousDisplay }) {
  const presentation = deltaPresentation(deltaPct, higherIsBetter);

  if (!presentation) {
    return (
      <span className="text-[11px] text-ink-tertiary">
        No comparison for this window
      </span>
    );
  }

  const { delta, Icon, tone } = presentation;
  const title =
    tone === 'neutral' && higherIsBetter == null
      ? 'This metric has no better direction, so the change is shown without a verdict.'
      : undefined;

  return (
    <span className="flex flex-wrap items-center gap-1.5">
      <span
        className={cn(
          'nums inline-flex items-center gap-0.5 rounded-full px-2 py-0.5',
          'text-[11px] font-medium',
          DELTA_TONE_CLASS[tone],
        )}
        title={title}
      >
        <Icon className="size-3" aria-hidden="true" />
        {Math.abs(delta).toFixed(1)}%
      </span>
      <span className="text-[11px] text-ink-tertiary">
        {previousDisplay === MISSING_DISPLAY
          ? 'no prior period'
          : `vs ${previousDisplay}`}
      </span>
    </span>
  );
}

export function AnalyticsKpiCard({
  kpi,
  kpiDef,
  icon,
  lastUpdatedAt = null,
  freshness = 'daily',
  loading = false,
  className,
}) {
  const unit = kpiDef?.unit ?? 'int';
  const formatId = kpi?.format || unit || 'int';
  const unitStyle = UNIT_STYLE[unit] ?? UNIT_STYLE.int;
  const Icon = icon ?? unitStyle.icon;

  const quality = kpi?.quality ?? kpiDef?.default_quality ?? 'INCOMPLETE';
  const inputsMissing = Array.isArray(kpi?.inputs_missing) ? kpi.inputs_missing : [];
  const previousDisplay = formatValue(kpi?.previous, formatId);

  return (
    <div className={cn('flex h-full flex-col', className)}>
      {/*
       * `deltaPct={null}` and `previousValue={undefined}` deliberately silence
       * KPICard's own comparison UI: it hard-codes "up is good", which is wrong
       * for the eighteen catalogue metrics where it is not. The delta is
       * re-rendered below with the direction the catalogue actually declares.
       */}
      <KPICard
        label={
          <span className="inline-flex items-center gap-1.5">
            {kpiDef?.label ?? kpi?.kpi_id ?? 'Metric'}
            {kpiDef && <MetricTooltip kpi={kpiDef} />}
          </span>
        }
        icon={Icon}
        value={kpi?.value ?? null}
        previousValue={undefined}
        deltaPct={null}
        format={(v) => formatValue(v, formatId)}
        tone={unitStyle.tone}
        loading={loading}
      />

      <div
        className={cn(
          // Pulled up over the card's bottom padding so the two read as one
          // surface with a footer band rather than as two stacked cards.
          '-mt-2 flex flex-col gap-1.5 rounded-b-sm border border-t-0 border-line-subtle',
          'bg-bg-sunken px-5 pb-3 pt-2 shadow-sm',
        )}
      >
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5">
          {loading ? (
            <span className="text-[11px] text-ink-tertiary">…</span>
          ) : (
            <DeltaChip
              deltaPct={kpi?.delta_pct}
              higherIsBetter={kpiDef?.higher_is_better ?? null}
              previousDisplay={previousDisplay}
            />
          )}
          <QualityBadge quality={quality} coveragePct={kpi?.coverage_pct ?? null} />
        </div>

        {/*
         * The list is the whole value of an INCOMPLETE label. Without it the
         * admin knows only that the number is wrong, not what to go and fix.
         */}
        {inputsMissing.length > 0 && (
          <p className="text-[11px] leading-relaxed text-warning">
            <span className="font-medium">Missing:</span>{' '}
            {inputsMissing.map((m) => String(m).replace(/_/g, ' ')).join(', ')}
          </p>
        )}

        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
          <span className="text-[11px] text-ink-tertiary">
            {kpiDef?.source ? humanizeCapability(kpiDef.source) : 'Source not stated'}
          </span>
          <FreshnessBadge
            freshness={kpiDef?.freshness || freshness}
            lastUpdatedAt={lastUpdatedAt}
            className="scale-95 origin-right"
          />
        </div>
      </div>
    </div>
  );
}
