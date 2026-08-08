import { BellOff, Check, EyeOff } from 'lucide-react';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn } from '@/lib/utils.js';
import { formatValue } from '@/features/analytics/format.js';
import { ChartCard } from '../charts/ChartCard.jsx';
import { MISSING_DASH } from '../charts/seriesGuards.js';
import {
  MiniTable,
  NotConfiguredPanel,
  Panel,
  StatusPill,
  Td,
  Th,
} from './BespokeParts.jsx';
import {
  anomalySeverity,
  anomalyState,
  notConfigured,
  seriesFor,
  sortAnomalies,
  tableBlock,
} from './bespokeHelpers.js';

/** Severity string -> pill tone. Unknown severities stay neutral. */
const SEVERITY_TONE = {
  critical: 'danger',
  fatal: 'danger',
  error: 'danger',
  high: 'danger',
  severe: 'danger',
  warn: 'warning',
  warning: 'warning',
  medium: 'warning',
  moderate: 'warning',
  low: 'info',
  info: 'info',
  notice: 'info',
};

/**
 * View 73 — Alerts and Anomaly.
 *
 * Ordered worst-first by the severity the backend sent. A row with no severity
 * is labelled "unclassified" and sorts after everything classified — it is
 * neither promoted to critical (which would cry wolf) nor demoted to info
 * (which would bury it). Deriving a severity here from how far `observed` sits
 * from `expected` would be inventing a threshold the alert rule never set.
 *
 * Acknowledge and resolve are affordances, not fetches. This component renders
 * what it is given and calls back; the mutation belongs to whoever owns the
 * query client, so caching, permissions and freshness stay in one place. With
 * no handler wired the buttons render disabled and say why, rather than
 * pretending to work.
 */
export function AnomalyFeedView({
  envelope,
  viewDef,
  onAcknowledge,
  onResolve,
  pendingId,
}) {
  if (notConfigured(envelope)) {
    return <NotConfiguredPanel envelope={envelope} viewDef={viewDef} />;
  }

  const block = tableBlock(envelope, 'alert_feed');
  const rows = sortAnomalies(block?.rows ?? []);
  const chartSpec = (viewDef?.charts ?? [])[0];

  const unclassified = rows.filter((row) => anomalySeverity(row) === null).length;
  const open = rows.filter((row) => anomalyState(row) === 'open').length;

  return (
    <div className="space-y-4">
      <Panel
        title="Alerts"
        action={
          rows.length > 0 ? (
            <StatusPill tone={open > 0 ? 'warning' : 'success'}>
              {`${open} open of ${rows.length}`}
            </StatusPill>
          ) : null
        }
        description="Sorted by the severity the alert rule assigned, then most recent first."
        bodyClassName="p-0"
      >
        {rows.length === 0 ? (
          <div className="p-5">
            <EmptyState
              icon={BellOff}
              size="sm"
              bordered={false}
              title="Nothing has breached an alert rule"
              description={
                'No metric moved outside its expected band in this window. This ' +
                'is a measured result — the rules ran and none fired.'
              }
            />
          </div>
        ) : (
          <MiniTable
            caption="Alerts with severity, rule, metric, observed and expected values"
            head={
              <>
                <Th>Severity</Th>
                <Th>Fired</Th>
                <Th>Rule / metric</Th>
                <Th align="right">Observed</Th>
                <Th align="right">Expected</Th>
                <Th align="right">Action</Th>
              </>
            }
          >
            {rows.map((row, index) => (
              <AlertRow
                key={`${row.rule ?? 'rule'}-${row.fired_at ?? index}`}
                row={row}
                busy={pendingId != null && pendingId === (row.id ?? row.alert_id)}
                onAcknowledge={onAcknowledge}
                onResolve={onResolve}
              />
            ))}
          </MiniTable>
        )}

        {unclassified > 0 && (
          <p className="border-t border-line-subtle px-5 py-3 text-xs text-ink-tertiary">
            {`${unclassified} alert${unclassified === 1 ? '' : 's'} arrived with no `}
            severity. They are shown as unclassified and sorted last — assigning
            one here would be a judgement the alert rule never made.
          </p>
        )}
      </Panel>

      <ChartCard
        spec={
          chartSpec ?? {
            id: 'alerts_trend',
            type: 'bar',
            x: 'date',
            series: ['alerts'],
            format: 'int',
            title: 'Alerts fired',
          }
        }
        data={seriesFor(envelope, chartSpec?.id ?? 'alerts_trend')}
        labels={{ alerts: 'Alerts fired' }}
      />
    </div>
  );
}

function AlertRow({ row, busy, onAcknowledge, onResolve }) {
  const severity = anomalySeverity(row);
  const state = anomalyState(row);
  const tone = severity ? (SEVERITY_TONE[severity] ?? 'neutral') : 'unchecked';

  return (
    <tr
      className={cn(
        'border-b border-line-subtle/60 last:border-0',
        state !== 'open' && 'opacity-60',
      )}
    >
      <Td>
        <StatusPill tone={tone}>{severity ?? 'unclassified'}</StatusPill>
      </Td>
      <Td className="whitespace-nowrap text-ink-secondary">
        {row.fired_at ?? MISSING_DASH}
      </Td>
      <Td>
        <span className="font-medium">{row.rule ?? MISSING_DASH}</span>
        <p className="text-xs text-ink-tertiary">{row.metric ?? MISSING_DASH}</p>
      </Td>
      <Td align="right">{formatValue(row.observed, 'ratio')}</Td>
      <Td align="right">{formatValue(row.expected, 'ratio')}</Td>
      <Td align="right">
        {state === 'resolved' ? (
          <StatusPill tone="success">resolved</StatusPill>
        ) : (
          <span className="inline-flex gap-1">
            <ActionButton
              icon={EyeOff}
              label="Acknowledge"
              disabled={!onAcknowledge || busy || state === 'acknowledged'}
              active={state === 'acknowledged'}
              onClick={() => onAcknowledge?.(row)}
            />
            <ActionButton
              icon={Check}
              label="Resolve"
              disabled={!onResolve || busy}
              onClick={() => onResolve?.(row)}
            />
          </span>
        )}
      </Td>
    </tr>
  );
}

function ActionButton({ icon: Icon, label, disabled, active, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={
        disabled && !active
          ? `${label} is not wired up in this view.`
          : active
            ? `Already ${label.toLowerCase()}d.`
            : label
      }
      className={cn(
        'inline-flex items-center gap-1 rounded-xs border px-2 py-1 text-xs',
        'focus-visible:focus-ring transition-colors',
        disabled
          ? 'cursor-not-allowed border-line-subtle text-ink-tertiary'
          : 'border-line-subtle text-ink-secondary hover:bg-fill hover:text-ink-primary',
        active && 'border-success/30 text-success',
      )}
    >
      <Icon className="size-3.5" aria-hidden="true" />
      <span>{label}</span>
    </button>
  );
}

export default AnomalyFeedView;
