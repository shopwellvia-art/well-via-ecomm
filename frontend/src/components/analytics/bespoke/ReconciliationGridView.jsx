import { CircleCheck, CircleHelp, ShieldAlert } from 'lucide-react';
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
  CHECK_STATUS,
  notConfigured,
  reconciliationSummary,
  seriesFor,
  tableRows,
} from './bespokeHelpers.js';

/**
 * View 63 — Data Reconciliation.
 *
 * Checks arrive in three states and the board draws three things, because two
 * of them are easy to confuse and the confusion is expensive:
 *
 *  - **matched** — the check ran and balanced. Solid, green, with both values.
 *  - **variance** — the check ran and did not balance. That is an aggregation
 *    bug, so it is an error tone, not a warning tone.
 *  - **not_configured** — the check could not run at all. Its counterparty (the
 *    gateway settlement API, GA4) is not connected, so `source_value`,
 *    `rollup_value` and `variance_pct` are all null.
 *
 * An unrunnable check is drawn dashed and hollow, and its numbers read as
 * em-dashes rather than as 0.00%. The failure mode this prevents is the one
 * the resolver's own comment names: an empty variance table looks exactly like
 * a clean bill of health, and an admin will read it as one. So the headline
 * counts what RAN, not what passed, and it refuses to say "all clear" while
 * anything is unchecked.
 */
export function ReconciliationGridView({ envelope, viewDef }) {
  if (notConfigured(envelope)) {
    return <NotConfiguredPanel envelope={envelope} viewDef={viewDef} />;
  }

  const rows = tableRows(envelope, 'variances');
  const summary = reconciliationSummary(rows);
  const chartSpec = (viewDef?.charts ?? [])[0];

  return (
    <div className="space-y-4">
      <Headline summary={summary} />

      <Panel title="Checks" bodyClassName="p-0">
        {rows.length === 0 ? (
          <div className="p-5">
            <EmptyState
              icon={CircleHelp}
              size="sm"
              bordered={false}
              title="No check was returned"
              description={
                'No check ran and none was reported as unrunnable either. An ' +
                'empty board is not a passing board — this is a wiring gap.'
              }
            />
          </div>
        ) : (
          <MiniTable
            caption="Reconciliation checks with their outcome, source value, rollup value and variance"
            head={
              <>
                <Th>Check</Th>
                <Th>Outcome</Th>
                <Th align="right">Source</Th>
                <Th align="right">Rollup</Th>
                <Th align="right">Variance</Th>
              </>
            }
          >
            {rows.map((row, index) => (
              <CheckRow key={`${row.check_name ?? 'check'}-${index}`} row={row} />
            ))}
          </MiniTable>
        )}
      </Panel>

      <ChartCard
        spec={
          chartSpec ?? {
            id: 'variance_trend',
            type: 'line',
            x: 'date',
            series: ['variance_pct'],
            format: 'pct',
            title: 'Variance vs source of truth',
          }
        }
        data={seriesFor(envelope, chartSpec?.id ?? 'variance_trend')}
        labels={{ variance_pct: 'Bridge variance' }}
        footnote={
          'Points only for days that were actually checked. This series is ' +
          'deliberately not densified: elsewhere a missing day is a real zero, ' +
          'but here 0.00% means "checked and balanced", so filling a gap would ' +
          'assert a check that never ran.'
        }
      />
    </div>
  );
}

function Headline({ summary }) {
  const tone = summary.variance > 0 ? 'danger' : summary.notRun > 0 ? 'warning' : 'success';
  const Icon =
    summary.variance > 0 ? ShieldAlert : summary.notRun > 0 ? CircleHelp : CircleCheck;

  const title =
    summary.total === 0
      ? 'Nothing was checked'
      : summary.variance > 0
        ? `${summary.variance} check${summary.variance === 1 ? '' : 's'} did not balance`
        : summary.notRun > 0
          ? `${summary.ran} of ${summary.total} checks ran`
          : 'Every check ran and balanced';

  const body =
    summary.total === 0
      ? 'No check reported an outcome, which is not the same as passing.'
      : summary.variance > 0
        ? 'A rollup that disagrees with its own identity is a bug in an ' +
          'aggregation job, not a rounding artefact.'
        : summary.notRun > 0
          ? `${summary.notRun} check${summary.notRun === 1 ? '' : 's'} could not ` +
            'run because the counterparty is not connected. This board is not a ' +
            'clean bill of health until they do.'
          : 'Both sides of every identity agree to the paisa.';

  return (
    <div
      className={cn(
        'flex items-start gap-3 rounded-sm border p-4',
        tone === 'danger' && 'border-danger/30 bg-danger-soft',
        tone === 'warning' && 'border-warning/30 bg-warning-soft',
        tone === 'success' && 'border-success/30 bg-success-soft',
      )}
    >
      <Icon
        className={cn(
          'mt-0.5 size-5 shrink-0',
          tone === 'danger' && 'text-danger',
          tone === 'warning' && 'text-warning',
          tone === 'success' && 'text-success',
        )}
        aria-hidden="true"
      />
      <div className="min-w-0">
        <p className="text-sm font-semibold text-ink-primary">{title}</p>
        <p className="mt-0.5 text-xs leading-relaxed text-ink-secondary">{body}</p>
        <div className="mt-2 flex flex-wrap gap-2">
          <StatusPill tone="success">{`${summary.matched} balanced`}</StatusPill>
          <StatusPill tone={summary.variance > 0 ? 'danger' : 'neutral'}>
            {`${summary.variance} variance`}
          </StatusPill>
          <StatusPill tone="unchecked">{`${summary.notRun} not checked`}</StatusPill>
        </div>
      </div>
    </div>
  );
}

function CheckRow({ row }) {
  const status = row?.status ?? CHECK_STATUS.NOT_RUN;
  const notRun = status === CHECK_STATUS.NOT_RUN;
  const variance = status === CHECK_STATUS.VARIANCE;

  return (
    <tr
      className={cn(
        'border-b border-line-subtle/60 last:border-0',
        // The unrunnable row is visibly hollow: a muted, dashed row cannot be
        // skim-read as "passed".
        notRun && 'bg-fill/40',
      )}
    >
      <Td>
        <span className="font-medium">{row?.check_name ?? MISSING_DASH}</span>
        {row?.detail && (
          <p className="mt-0.5 max-w-md text-xs leading-relaxed text-ink-tertiary">
            {row.detail}
          </p>
        )}
      </Td>
      <Td>
        {notRun ? (
          <StatusPill tone="unchecked" title="This check did not run.">
            not checked
          </StatusPill>
        ) : variance ? (
          <StatusPill tone="danger">variance</StatusPill>
        ) : (
          <StatusPill tone="success">balanced</StatusPill>
        )}
      </Td>
      <Td align="right" className={cn(notRun && 'text-ink-tertiary')}>
        {formatValue(row?.source_value, 'money')}
      </Td>
      <Td align="right" className={cn(notRun && 'text-ink-tertiary')}>
        {formatValue(row?.rollup_value, 'money')}
      </Td>
      <Td
        align="right"
        className={cn(
          notRun && 'text-ink-tertiary',
          variance && 'font-medium text-danger',
        )}
      >
        {formatValue(row?.variance_pct, 'pct')}
      </Td>
    </tr>
  );
}

export default ReconciliationGridView;
