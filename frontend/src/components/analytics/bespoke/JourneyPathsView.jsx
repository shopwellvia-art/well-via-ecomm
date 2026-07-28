import { Route } from 'lucide-react';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { formatValue } from '@/features/analytics/format.js';
import { MISSING_DASH } from '../charts/seriesGuards.js';
import {
  MiniTable,
  NotConfiguredPanel,
  Panel,
  Td,
  Th,
} from './BespokeParts.jsx';
import { notConfigured, tableBlock } from './bespokeHelpers.js';

/**
 * View 65 — Customer Journey and Attribution.
 *
 * This view is `INTEGRATION_REQUIRED` in the registry and its resolver returns
 * `not_configured` with no sources at all, so in practice the gate upstream
 * renders first and this component is reached only if the state ever changes.
 * It still refuses to draw anything on its own.
 *
 * The reason is worth keeping in front of whoever reads this next: internally
 * only cart and order events exist, so the earliest touchpoint a journey could
 * start from is add-to-cart — which is not a journey, it is the last step of
 * one. A two-node "path" rendered as an attribution diagram would be a more
 * misleading answer than no answer, because it looks like the real thing.
 *
 * If GA4 is ever connected the resolver will send real path rows, and this
 * renders them as a table of paths with their conversions. It builds no
 * Sankey, no first/last-click split and no attributed revenue out of anything
 * it was not sent.
 */
export function JourneyPathsView({ envelope, viewDef }) {
  const reason = notConfigured(envelope);
  if (reason || !envelope) {
    return (
      <NotConfiguredPanel
        envelope={envelope}
        viewDef={viewDef}
        title="Journeys need a cross-session source"
      />
    );
  }

  const block = tableBlock(envelope, 'journey_paths', 'paths');
  const rows = block?.rows ?? [];

  if (rows.length === 0) {
    return (
      <Panel title="Paths to purchase">
        <EmptyState
          icon={Route}
          size="sm"
          bordered={false}
          title="No path was returned"
          description={
            viewDef?.limitation ||
            'No touchpoint path was reported for this window. Nothing is inferred ' +
              'from order data alone — a path built from cart and order events is ' +
              'the end of a journey, not a journey.'
          }
        />
      </Panel>
    );
  }

  return (
    <Panel
      title="Paths to purchase"
      description={
        'Each row is a touchpoint sequence exactly as the source reported it. ' +
        'Attribution columns appear only when the source sends them.'
      }
      bodyClassName="p-0"
    >
      <MiniTable
        caption="Touchpoint paths with conversions and attributed revenue"
        head={
          <>
            <Th>Path</Th>
            <Th align="right">Journeys</Th>
            <Th align="right">Conversions</Th>
            <Th align="right">Attributed revenue</Th>
          </>
        }
      >
        {rows.map((row, index) => (
          <tr
            key={`${row.path ?? 'path'}-${index}`}
            className="border-b border-line-subtle/60 last:border-0"
          >
            <Td>{formatPath(row)}</Td>
            <Td align="right">{formatValue(row.journeys, 'int')}</Td>
            <Td align="right">{formatValue(row.conversions, 'int')}</Td>
            <Td align="right">{formatValue(row.attributed_revenue, 'money')}</Td>
          </tr>
        ))}
      </MiniTable>
      {block?.truncated && (
        <p className="border-t border-line-subtle px-5 py-3 text-xs text-ink-tertiary">
          Capped at the row limit — the long tail of one-off paths is not shown.
        </p>
      )}
    </Panel>
  );
}

function formatPath(row) {
  const path = row?.path ?? row?.touchpoints;
  if (Array.isArray(path)) return path.join(' → ');
  if (typeof path === 'string' && path.length > 0) return path;
  return MISSING_DASH;
}

export default JourneyPathsView;
