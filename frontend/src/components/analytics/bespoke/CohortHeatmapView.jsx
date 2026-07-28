import { Users } from 'lucide-react';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn } from '@/lib/utils.js';
import { formatValue } from '@/features/analytics/format.js';
import { ChartCard } from '../charts/ChartCard.jsx';
import { MISSING_DASH } from '../charts/seriesGuards.js';
import { CaveatBanner, NotConfiguredPanel, Panel } from './BespokeParts.jsx';
import {
  maxRetention,
  notConfigured,
  pivotCohorts,
  retentionIntensity,
  seriesFor,
  tableRows,
  warningWith,
} from './bespokeHelpers.js';

/**
 * View 12 — Cohort and Retention.
 *
 * The heatmap has three cell states and they must not be collapsed:
 *
 *  - **outside the triangle** — that month has not happened yet for this
 *    cohort. No cell content at all.
 *  - **explicitly empty** — a row exists but `retention_pct` is `null`, which
 *    the resolver emits when `cohort_size` is 0. Nobody was acquired that
 *    month, so there is no rate to report. This is the case the whole
 *    component is shaped around: rendering it as 0% would tint a cohort that
 *    does not exist in the darkest "worst retention" colour and park it at the
 *    top of the map as the thing to go and fix.
 *  - **a real percentage** — including a real 0%, which means customers were
 *    acquired and none of them came back. That one *is* the worst performer,
 *    and it has to be able to look like it without competition from cohorts
 *    that never existed.
 *
 * The empty state is drawn as a hatched cell with an em-dash, which is the
 * same visual language the charts use for a hole.
 */
export function CohortHeatmapView({ envelope, viewDef }) {
  if (notConfigured(envelope)) {
    return <NotConfiguredPanel envelope={envelope} viewDef={viewDef} />;
  }

  const rows = tableRows(envelope, 'cohort_grid');
  const { grid, periods } = pivotCohorts(rows);
  const ceiling = maxRetention(grid);
  const smallSample = warningWith(envelope, 'SMALL_SAMPLE');

  const curveSpec = (viewDef?.charts ?? []).find((chart) =>
    ['month', 'period_index'].includes(chart.x),
  );
  const curve = seriesFor(envelope, curveSpec?.id ?? 'retention_curve');

  const emptyCells = grid.reduce(
    (count, row) => count + row.cells.filter((cell) => cell.state === 'empty').length,
    0,
  );

  return (
    <div className="space-y-4">
      {smallSample && <CaveatBanner warning={smallSample} tone="info" />}

      <Panel
        title="Retention by acquisition month"
        description={
          'Each row is the month a customer first ordered; each column is the ' +
          'number of months after that. Retention is active customers divided by ' +
          'the cohort size, computed at read time from both stored components.'
        }
        bodyClassName="p-0"
      >
        {grid.length === 0 ? (
          <EmptyState
            icon={Users}
            size="sm"
            bordered={false}
            title="No cohorts in range"
            description="No acquisition month in the selected period has been rolled up yet."
          />
        ) : (
          <>
            <div className="overflow-x-auto p-5 pt-4">
              <table className="w-full border-collapse text-sm">
                <caption className="sr-only">
                  Retention percentage by acquisition month and months since
                  acquisition. Cells reading an em-dash are cohorts with no
                  customers, which have no retention rate — they are not zero.
                </caption>
                <thead>
                  <tr>
                    <th
                      scope="col"
                      className="px-2 py-2 text-left text-xs font-medium text-ink-tertiary"
                    >
                      Cohort
                    </th>
                    <th
                      scope="col"
                      className="px-2 py-2 text-right text-xs font-medium text-ink-tertiary"
                    >
                      Size
                    </th>
                    {periods.map((period) => (
                      <th
                        key={period}
                        scope="col"
                        className="px-2 py-2 text-center text-xs font-medium text-ink-tertiary"
                      >
                        {`M${period}`}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {grid.map((row) => (
                    <tr key={row.month}>
                      <th
                        scope="row"
                        className={cn(
                          'whitespace-nowrap px-2 py-1.5 text-left text-xs',
                          'font-medium text-ink-primary',
                        )}
                      >
                        {row.month}
                      </th>
                      <td className="px-2 py-1.5 text-right text-xs tabular-nums text-ink-secondary">
                        {row.cohortSize === null
                          ? MISSING_DASH
                          : formatValue(row.cohortSize, 'int')}
                      </td>
                      {row.cells.map((cell) => (
                        <HeatCell key={cell.periodIndex} cell={cell} ceiling={ceiling} />
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div
              className={cn(
                'flex flex-wrap items-center gap-4 border-t border-line-subtle',
                'px-5 py-3 text-xs text-ink-tertiary',
              )}
            >
              <Legend ceiling={ceiling} />
              {emptyCells > 0 && (
                <span>
                  {`${emptyCells} cell${emptyCells === 1 ? '' : 's'} hatched: the cohort has no `}
                  customers, so it has no retention rate. That is not 0%.
                </span>
              )}
            </div>
          </>
        )}
      </Panel>

      <ChartCard
        spec={
          curveSpec ?? {
            id: 'retention_curve',
            type: 'line',
            x: 'month',
            series: ['retention_pct'],
            format: 'pct',
            title: 'Retention by month since first order',
          }
        }
        data={curve}
        labels={{ retention_pct: 'Retention' }}
        footnote={
          'Pooled across cohorts by summing both components and dividing once, ' +
          'not by averaging each cohort’s rate — an unweighted mean would let a ' +
          'four-customer cohort count as much as a four-thousand-customer one.'
        }
      />
    </div>
  );
}

function HeatCell({ cell, ceiling }) {
  if (cell.state === 'absent') {
    return (
      <td
        className="px-2 py-1.5 text-center text-xs text-ink-tertiary/40"
        aria-label="not reached yet"
      >
        <span className="sr-only">Not reached yet</span>
      </td>
    );
  }

  if (cell.state === 'empty') {
    return (
      <td className="px-1 py-1">
        <span
          title={cell.reason}
          className={cn(
            'flex h-7 items-center justify-center rounded-xs',
            'border border-dashed border-line-strong text-xs text-ink-tertiary',
          )}
        >
          {MISSING_DASH}
          <span className="sr-only">{` — ${cell.reason}`}</span>
        </span>
      </td>
    );
  }

  const intensity = retentionIntensity(cell.retention, ceiling);
  return (
    <td className="px-1 py-1">
      <span
        title={`${cell.active} of ${cell.cohortSize} customers active`}
        className="flex h-7 items-center justify-center rounded-xs text-xs tabular-nums"
        style={{
          backgroundColor: `rgba(34, 197, 94, ${(0.08 + intensity * 0.72).toFixed(3)})`,
          color: intensity > 0.55 ? 'var(--ink-inverse)' : 'var(--ink-primary)',
        }}
      >
        {formatValue(cell.retention, 'pct')}
      </span>
    </td>
  );
}

function Legend({ ceiling }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span>0%</span>
      <span
        className="h-2 w-24 rounded-xs"
        style={{
          background:
            'linear-gradient(90deg, rgba(34,197,94,0.08), rgba(34,197,94,0.80))',
        }}
        aria-hidden="true"
      />
      <span>
        {ceiling === null ? MISSING_DASH : formatValue(ceiling, 'pct')}
      </span>
      <span className="text-ink-tertiary">
        (scaled to the highest cell, not to 100%)
      </span>
    </span>
  );
}

export default CohortHeatmapView;
