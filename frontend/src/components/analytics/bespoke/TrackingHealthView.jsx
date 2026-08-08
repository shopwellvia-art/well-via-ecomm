import { Activity } from 'lucide-react';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn } from '@/lib/utils.js';
import { formatValue } from '@/features/analytics/format.js';
import { ChartCard } from '../charts/ChartCard.jsx';
import { MISSING_DASH } from '../charts/seriesGuards.js';
import {
  CaveatBanner,
  MiniTable,
  NotConfiguredPanel,
  Panel,
  StatusPill,
  Td,
  Th,
} from './BespokeParts.jsx';
import {
  lagLabel,
  notConfigured,
  seriesFor,
  sortHealthRows,
  tableRows,
  warningWith,
} from './bespokeHelpers.js';

/**
 * View 62 — Analytics Tracking Health.
 *
 * The one view that must never look healthy by default.
 *
 * A rollup that has never been built comes back as `status: "never_built"` with
 * `lag_days: null`. The obvious sort — `a.lag_days - b.lag_days` — coerces that
 * null to 0 and puts the never-built rollup at the very top of a "most current"
 * list, which is precisely backwards: never built is the WORST state, not the
 * freshest. `sortHealthRows` ranks by status first and only then by lag, and
 * `lagLabel` renders a null as "never built", never as "0 days behind".
 *
 * The event-volume chart the registry declares is deliberately not sent by the
 * resolver. `ChartCard` renders an absent series as "not returned", which is
 * the honest reading — an empty hourly axis would say "zero events received",
 * a much stronger claim than "we cannot see events at all".
 */
export function TrackingHealthView({ envelope, viewDef }) {
  if (notConfigured(envelope)) {
    return <NotConfiguredPanel envelope={envelope} viewDef={viewDef} />;
  }

  const rows = sortHealthRows(tableRows(envelope, 'rollup_freshness'));
  const neverBuilt = rows.filter((row) => row.status === 'never_built').length;
  const stale = rows.filter((row) => row.status === 'stale').length;
  const current = rows.filter((row) => row.status === 'current').length;

  const noRollupWarning = warningWith(envelope, 'NO_ROLLUP_YET');
  const staleWarning = warningWith(envelope, 'ROLLUP_STALE');
  const noEvents = warningWith(envelope, 'NOT_CONFIGURED');

  const chartSpec = (viewDef?.charts ?? [])[0];

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <Tile
          tone={neverBuilt > 0 ? 'danger' : 'neutral'}
          label="Never built"
          value={neverBuilt}
          hint="No rows for this timezone generation. Worst state, not the freshest."
        />
        <Tile
          tone={stale > 0 ? 'warning' : 'neutral'}
          label="Stale"
          value={stale}
          hint="Built, but the watermark stops before the end of the window."
        />
        <Tile
          tone={current > 0 ? 'success' : 'neutral'}
          label="Current"
          value={current}
          hint="Watermark reaches the end of the requested window."
        />
      </div>

      {noRollupWarning && <CaveatBanner warning={noRollupWarning} tone="warning" />}
      {staleWarning && <CaveatBanner warning={staleWarning} tone="warning" />}

      <Panel
        title="Rollup freshness"
        description="Sorted worst first: never built, then furthest behind, then current."
        bodyClassName="p-0"
      >
        {rows.length === 0 ? (
          <div className="p-5">
            <EmptyState
              icon={Activity}
              size="sm"
              bordered={false}
              title="No rollup reported a state"
              description={
                'Nothing was probed. An empty freshness table is not a healthy ' +
                'pipeline — it is no answer at all.'
              }
            />
          </div>
        ) : (
          <MiniTable
            caption="Every watched rollup with its status, watermark, row count and lag"
            head={
              <>
                <Th>Rollup</Th>
                <Th>State</Th>
                <Th>Data through</Th>
                <Th align="right">Rows</Th>
                <Th align="right">Lag</Th>
              </>
            }
          >
            {rows.map((row) => (
              <HealthRow key={row.source} row={row} />
            ))}
          </MiniTable>
        )}
      </Panel>

      <ChartCard
        spec={
          chartSpec ?? {
            id: 'event_volume',
            type: 'bar',
            x: 'hour',
            series: ['events'],
            format: 'int',
            title: 'Events received per hour',
          }
        }
        data={seriesFor(envelope, chartSpec?.id ?? 'event_volume')}
        emptyHint={
          noEvents?.message ||
          'Event-level tracking needs a client-side analytics stream. None is ' +
            'connected, so no event series is returned — an empty chart would ' +
            'read as "zero events", which is a stronger claim than "we cannot ' +
            'see events".'
        }
      />
    </div>
  );
}

function HealthRow({ row }) {
  const neverBuilt = row.status === 'never_built';
  const stale = row.status === 'stale';
  return (
    <tr
      className={cn(
        'border-b border-line-subtle/60 last:border-0',
        neverBuilt && 'bg-danger-soft/40',
      )}
    >
      <Td>
        <span className="font-medium">{row.label ?? row.source}</span>
        <p className="text-xs text-ink-tertiary">{row.source}</p>
      </Td>
      <Td>
        {neverBuilt ? (
          <StatusPill tone="danger">never built</StatusPill>
        ) : stale ? (
          <StatusPill tone="warning">stale</StatusPill>
        ) : (
          <StatusPill tone="success">current</StatusPill>
        )}
      </Td>
      <Td className={cn(neverBuilt && 'text-ink-tertiary')}>
        {row.through ?? MISSING_DASH}
      </Td>
      <Td align="right">{formatValue(row.rows, 'int')}</Td>
      <Td
        align="right"
        className={cn(
          neverBuilt && 'font-medium text-danger',
          stale && 'text-warning',
        )}
      >
        {lagLabel(row)}
      </Td>
    </tr>
  );
}

function Tile({ tone, label, value, hint }) {
  return (
    <div
      className={cn(
        'rounded-sm border p-4',
        tone === 'danger' && 'border-danger/30 bg-danger-soft',
        tone === 'warning' && 'border-warning/30 bg-warning-soft',
        tone === 'success' && 'border-success/30 bg-success-soft',
        tone === 'neutral' && 'border-line-subtle bg-bg-elevated',
      )}
    >
      <p className="text-xs font-medium text-ink-secondary">{label}</p>
      <p className="mt-1 text-h3 tabular-nums text-ink-primary">{value}</p>
      <p className="mt-1 text-xs leading-relaxed text-ink-tertiary">{hint}</p>
    </div>
  );
}

export default TrackingHealthView;
