import { FlaskConical } from 'lucide-react';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { formatValue } from '@/features/analytics/format.js';
import { isMissing } from '@/features/analytics/viewState.js';
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
import { notConfigured, seriesFor, tableBlock } from './bespokeHelpers.js';

/**
 * View 66 — Experiment and A/B Testing.
 *
 * Today the resolver returns `not_configured`: there is no experiment
 * definition table and no variant assignment table, so there is no way to know
 * which visitor saw which variant. Reporting the store's overall conversion
 * rate under a single "control" variant would look like a running experiment
 * that found no effect, which is a far worse answer than none.
 *
 * The rule this component enforces if data ever does arrive: **never render a
 * significance claim the backend did not send.** No p-value is computed here,
 * no confidence interval is derived from a conversion count, and no variant is
 * called a winner. Those need the assignment log, the stopping rule and the
 * multiple-comparison correction that the experiment store would define — and
 * a significance badge invented in a React component is exactly the kind of
 * number that ends a debate it should not have been allowed into.
 *
 * When `significant`, `p_value` or `confidence` are absent, the verdict column
 * says "not reported" rather than "not significant". Those are different
 * claims.
 */
export function ExperimentResultsView({ envelope, viewDef }) {
  if (notConfigured(envelope) || !envelope) {
    return (
      <NotConfiguredPanel
        envelope={envelope}
        viewDef={viewDef}
        title="No experiment store is wired up"
      />
    );
  }

  const block = tableBlock(envelope, 'experiments', 'variants');
  const rows = block?.rows ?? [];
  const chartSpec = (viewDef?.charts ?? [])[0];
  const anyVerdict = rows.some((row) => verdictOf(row) !== null);

  return (
    <div className="space-y-4">
      <Panel title="Variants" bodyClassName="p-0">
        {rows.length === 0 ? (
          <div className="p-5">
            <EmptyState
              icon={FlaskConical}
              size="sm"
              bordered={false}
              title="No experiment has been defined"
              description={
                chartSpec?.empty_hint ||
                'Nothing was returned to split by variant. A single-variant ' +
                  'result would read as an experiment that ran and found no ' +
                  'difference.'
              }
            />
          </div>
        ) : (
          <MiniTable
            caption="Experiment variants with exposure, conversion, AOV and revenue"
            head={
              <>
                <Th>Experiment</Th>
                <Th>Variant</Th>
                <Th align="right">Exposed</Th>
                <Th align="right">Conversion</Th>
                <Th align="right">AOV</Th>
                <Th align="right">Revenue</Th>
                <Th>Verdict</Th>
              </>
            }
          >
            {rows.map((row, index) => (
              <tr
                key={`${row.experiment ?? 'exp'}-${row.variant ?? index}`}
                className="border-b border-line-subtle/60 last:border-0"
              >
                <Td>{row.experiment ?? MISSING_DASH}</Td>
                <Td>
                  <span className="font-medium">{row.variant ?? MISSING_DASH}</span>
                </Td>
                <Td align="right">{formatValue(row.exposed ?? row.users, 'int')}</Td>
                <Td align="right">{formatValue(row.conversion_rate, 'pct')}</Td>
                <Td align="right">{formatValue(row.aov, 'money')}</Td>
                <Td align="right">{formatValue(row.net_revenue, 'money')}</Td>
                <Td>
                  <Verdict row={row} />
                </Td>
              </tr>
            ))}
          </MiniTable>
        )}

        {rows.length > 0 && !anyVerdict && (
          <p className="border-t border-line-subtle px-5 py-3 text-xs text-ink-tertiary">
            No significance was reported for any variant. That is not the same as
            &ldquo;no significant difference&rdquo; — the test was not reported,
            so no verdict is shown. Nothing here is computed from the conversion
            counts on this page.
          </p>
        )}
      </Panel>

      <ChartCard
        spec={
          chartSpec ?? {
            id: 'variant_conversion',
            type: 'bar',
            x: 'variant',
            series: ['conversion_rate'],
            format: 'pct',
            title: 'Conversion by variant',
          }
        }
        data={seriesFor(envelope, chartSpec?.id ?? 'variant_conversion')}
        labels={{ conversion_rate: 'Conversion rate' }}
        footnote={
          'Bars are point estimates. No confidence interval is drawn because none ' +
          'was sent, and one drawn from these counts alone would imply a test ' +
          'design nobody declared.'
        }
      />
    </div>
  );
}

/**
 * The significance the backend reported, or `null`.
 *
 * Reads only fields the server would have to set deliberately. Nothing is
 * derived, so a row with conversion counts and no test result yields `null`.
 */
function verdictOf(row) {
  if (!isMissing(row?.significant)) {
    return { significant: Boolean(row.significant), pValue: row.p_value ?? null };
  }
  if (!isMissing(row?.p_value)) {
    return { significant: null, pValue: row.p_value };
  }
  return null;
}

function Verdict({ row }) {
  const verdict = verdictOf(row);
  if (!verdict) {
    return (
      <StatusPill
        tone="unchecked"
        title="The backend reported no significance test for this variant."
      >
        not reported
      </StatusPill>
    );
  }
  if (verdict.significant === true) {
    return (
      <StatusPill tone="success">
        {verdict.pValue == null
          ? 'significant'
          : `significant (p=${formatValue(verdict.pValue, 'ratio')})`}
      </StatusPill>
    );
  }
  if (verdict.significant === false) {
    return (
      <StatusPill tone="neutral">
        {verdict.pValue == null
          ? 'not significant'
          : `not significant (p=${formatValue(verdict.pValue, 'ratio')})`}
      </StatusPill>
    );
  }
  return (
    <StatusPill tone="info">{`p=${formatValue(verdict.pValue, 'ratio')}`}</StatusPill>
  );
}

export default ExperimentResultsView;
