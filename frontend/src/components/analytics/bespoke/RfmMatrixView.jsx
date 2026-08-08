import { Grid3x3 } from 'lucide-react';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
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
  buildRfmGrid,
  notConfigured,
  seriesFor,
  tableBlock,
} from './bespokeHelpers.js';

/**
 * View 59 — RFM Customer Analysis.
 *
 * The 5x5 grid is built client-side from each customer's `cell` label, and the
 * honest part is what happens when a label does not parse into two 1-5 scores:
 * the customer is counted as *unplaced* and named, never rounded into the
 * nearest cell. A grid whose counts do not sum to the population is a grid
 * that quietly lost people.
 *
 * A cell with `count: 0` here IS a real zero — every customer in the table was
 * placed somewhere, so an empty cell means nobody scored that way. That is a
 * different situation from the cohort heatmap, where a null retention means
 * the cohort does not exist, and the two are drawn differently on purpose.
 */
export function RfmMatrixView({ envelope, viewDef }) {
  if (notConfigured(envelope)) {
    return <NotConfiguredPanel envelope={envelope} viewDef={viewDef} />;
  }

  const block = tableBlock(envelope, 'rfm_table');
  const rows = block?.rows ?? [];
  const { grid, unplaced, placed, maxCount, buildable } = buildRfmGrid(rows);
  const chartSpec = (viewDef?.charts ?? [])[0];

  return (
    <div className="space-y-4">
      <Panel
        title="Recency x Frequency"
        action={
          unplaced.length > 0 ? (
            <StatusPill tone="warning">
              {`${unplaced.length} unplaced`}
            </StatusPill>
          ) : null
        }
        description={
          'Rows are recency scores (5 = most recent), columns are frequency ' +
          'scores (5 = most orders). Tint strength is the number of customers ' +
          'in the cell.'
        }
      >
        {!buildable ? (
          <EmptyState
            icon={Grid3x3}
            size="sm"
            bordered={false}
            title="No customer could be placed on the grid"
            description={
              rows.length === 0
                ? 'No customer rows were returned for this period.'
                : `${rows.length} row${rows.length === 1 ? '' : 's'} came back ` +
                  'without an RFM cell label that resolves to two 1-5 scores. ' +
                  'They are listed below rather than assigned to a guessed cell.'
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="border-collapse text-sm">
              <caption className="sr-only">
                Customer counts by recency score (rows) and frequency score
                (columns). An empty cell means no customer scored that
                combination.
              </caption>
              <thead>
                <tr>
                  <th className="px-2 py-1 text-xs font-medium text-ink-tertiary">
                    R \ F
                  </th>
                  {[1, 2, 3, 4, 5].map((f) => (
                    <th
                      key={f}
                      scope="col"
                      className="px-2 py-1 text-center text-xs font-medium text-ink-tertiary"
                    >
                      {f}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {grid.map((row) => (
                  <tr key={row.r}>
                    <th
                      scope="row"
                      className="px-2 py-1 text-xs font-medium text-ink-tertiary"
                    >
                      {row.r}
                    </th>
                    {row.cells.map((cell) => (
                      <RfmCell
                        key={`${cell.r}-${cell.f}`}
                        cell={cell}
                        maxCount={maxCount}
                      />
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <p className="mt-3 text-xs text-ink-tertiary">
          {`${placed} of ${rows.length} customer${rows.length === 1 ? '' : 's'} placed. `}
          {unplaced.length > 0
            ? `${unplaced.length} had no readable cell label and were left off the ` +
              'grid rather than assigned to the nearest one.'
            : 'An empty cell here means nobody scored that combination — a measured zero.'}
        </p>
      </Panel>

      <ChartCard
        spec={
          chartSpec ?? {
            id: 'rfm_revenue',
            type: 'hbar',
            x: 'segment',
            series: ['net_revenue'],
            format: 'money',
            title: 'Revenue by RFM segment',
          }
        }
        data={seriesFor(envelope, chartSpec?.id ?? 'rfm_revenue')}
        labels={{ net_revenue: 'Net revenue' }}
      />

      <Panel title="Customers by RFM cell" bodyClassName="p-0">
        {rows.length === 0 ? (
          <div className="p-5">
            <EmptyState
              icon={Grid3x3}
              size="sm"
              bordered={false}
              title="No customers in range"
              description="No customer rows were returned for the selected period."
            />
          </div>
        ) : (
          <MiniTable
            caption="Customers with their recency, frequency, monetary value and cell"
            head={
              <>
                <Th>Customer</Th>
                <Th align="right">Recency</Th>
                <Th align="right">Frequency</Th>
                <Th align="right">Monetary</Th>
                <Th>Cell</Th>
              </>
            }
          >
            {rows.map((row, index) => (
              <tr
                key={`${row.customer ?? 'row'}-${index}`}
                className="border-b border-line-subtle/60 last:border-0"
              >
                <Td>{row.customer ?? MISSING_DASH}</Td>
                <Td align="right">{formatValue(row.recency_days, 'days')}</Td>
                <Td align="right">{formatValue(row.frequency, 'int')}</Td>
                <Td align="right">{formatValue(row.monetary, 'money')}</Td>
                <Td>
                  {row.cell ? (
                    row.cell
                  ) : (
                    <StatusPill tone="unchecked">no cell</StatusPill>
                  )}
                </Td>
              </tr>
            ))}
          </MiniTable>
        )}
        {block?.truncated && (
          <p className="border-t border-line-subtle px-5 py-3 text-xs text-ink-tertiary">
            Capped at the row limit — the grid above counts only the customers on
            this page, not the whole base.
          </p>
        )}
      </Panel>
    </div>
  );
}

/**
 * One grid cell. Tint strength is the cell's share of the busiest cell, so a
 * grid where one cell dominates still shows structure in the rest.
 */
function RfmCell({ cell, maxCount }) {
  const share = maxCount > 0 ? cell.count / maxCount : 0;
  const filled = cell.count > 0;
  return (
    <td className="p-0.5">
      <span
        title={
          `R${cell.r} F${cell.f}: ${cell.count} customer(s), ` +
          `${formatValue(cell.monetary, 'money')}`
        }
        className={
          'flex h-12 w-16 flex-col items-center justify-center rounded-xs ' +
          'text-xs tabular-nums'
        }
        style={{
          backgroundColor: filled
            ? `rgba(99, 102, 241, ${(0.1 + share * 0.7).toFixed(3)})`
            : 'var(--fill)',
          color: share > 0.55 ? 'var(--ink-inverse)' : 'var(--ink-primary)',
        }}
      >
        <span className="font-medium">{cell.count}</span>
        <span className="text-[10px] opacity-80">
          {filled ? formatValue(cell.monetary, 'money') : ''}
        </span>
      </span>
    </td>
  );
}

export default RfmMatrixView;
