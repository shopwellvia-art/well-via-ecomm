import { Suspense } from 'react';
import { HelpCircle } from 'lucide-react';
import { Card, CardHeader } from '@/components/ui/Card.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { chartPresentation, tablePresentation } from '@/features/analytics/presentation.js';
import { RENDER_KIND, resolveViewState } from '@/features/analytics/viewState.js';
import { getBespokeView } from './bespoke/index.js';
import { CHART_HEIGHT, ChartCard } from './charts/index.js';
import { DataCompletenessWarning } from './DataCompletenessWarning.jsx';
import { DataTable } from './DataTable.jsx';
import { ExportButton } from './ExportButton.jsx';
import { KpiRow } from './KpiRow.jsx';
import { SourceFootnote } from './SourceFootnote.jsx';
import { ViewStateGate } from './ViewStateGate.jsx';
import { cn } from '@/lib/utils.js';

/**
 * Renders any of the 73 analytics views, knowing nothing about any of them.
 *
 * Everything on screen comes from two places: the registry definition (what
 * this view is) and the envelope (what the server measured). There is no
 * per-view branch here and there must never be one — the moment a view earns
 * an `if` in this file, the 73rd view costs as much to add as the first did.
 *
 * The order is the contract:
 *
 *   1. `resolveViewState` decides. It is the only thing allowed to.
 *   2. Anything other than `data` is the gate's problem, and we return.
 *   3. A bespoke view gets the SAME envelope this component was handed.
 *   4. KPIs, 5. charts, 6. tables, 7. provenance.
 *
 * Step 3 is worth being stubborn about. A bespoke component that fetched its
 * own data would have its own cache entry, its own freshness, its own error
 * path and its own permission check — four things that would then be right in
 * 63 views and subtly wrong in 10. Bespoke means "draws differently", never
 * "sources differently".
 *
 * For the same reason a bespoke view keeps steps 1, 2, 4 and 7: the notices,
 * the KPI row and the source footnote are rendered here for all 73 views. Six
 * of the ten bespoke views declare KPIs in the registry and none of the
 * components read `envelope.kpis`, so letting bespoke own the whole page would
 * drop those metrics with nothing on screen to say they had gone. Bespoke owns
 * the middle of the page, not the honesty around it.
 */

export function ViewRenderer({ viewDef, envelope, query = {}, filters, className }) {
  const state = resolveViewState(viewDef, envelope, query);

  if (state.kind !== RENDER_KIND.DATA) {
    return (
      <ViewStateGate
        state={state}
        viewDef={viewDef}
        availability={envelope?.availability}
        onRetry={query.refetch}
        className={className}
      />
    );
  }

  const data = state.envelope;
  const notices = (
    <DataCompletenessWarning
      warnings={state.warnings}
      coveragePct={data.coverage_pct ?? null}
      isPartial={state.isPartial}
      limitation={viewDef.limitation}
    />
  );

  if (viewDef.bespoke) {
    // The closed key → component map lives in `bespoke/index.js`; an unmapped
    // key is a wiring gap and says so rather than falling through to charts.
    const Bespoke = getBespokeView(viewDef.bespoke);
    return (
      <div className={cn('space-y-6', className)}>
        {notices}
        <KpiRow view={viewDef} envelope={data} loading={query.isFetching && !query.isLoading} />
        {Bespoke ? (
          <Suspense fallback={<ViewStateGate state={{ kind: RENDER_KIND.LOADING }} viewDef={viewDef} />}>
            <Bespoke envelope={data} viewDef={viewDef} filters={filters} />
          </Suspense>
        ) : (
          <EmptyState
            icon={HelpCircle}
            title="This view has no renderer in this build"
            description={`The registry asks for the "${viewDef.bespoke}" visual, which this
              build does not ship. Falling back to generic charts would draw a shape the
              view did not ask for.`}
          />
        )}
        <SourceFootnote envelope={data} />
      </div>
    );
  }

  const viewSpan = viewDef.presentation?.span ?? 2;
  const charts = viewDef.charts ?? [];
  const tables = viewDef.tables ?? [];

  return (
    <div className={cn('space-y-6', className)}>
      {notices}

      <KpiRow view={viewDef} envelope={data} loading={query.isFetching && !query.isLoading} />

      {charts.length > 0 && (
        <div className={cn('grid gap-4', viewSpan === 1 ? 'grid-cols-1' : 'grid-cols-1 lg:grid-cols-2')}>
          {charts.map((spec) => {
            const pres = chartPresentation(viewDef.slug, spec);
            return (
              <ChartCard
                key={spec.id}
                spec={spec}
                data={data.series?.[spec.id]}
                height={pres.height === 'tall' ? CHART_HEIGHT.xl : undefined}
                className={cn(viewSpan === 2 && pres.span === 2 && 'lg:col-span-2')}
              />
            );
          })}
        </div>
      )}

      {tables.map((spec) => {
        const block = data.tables?.[spec.id];
        const pres = tablePresentation(viewDef.slug, spec);
        return (
          <Card key={spec.id} className="overflow-hidden">
            <CardHeader
              title={spec.title}
              action={
                <ExportButton view={viewDef} filters={filters} tableId={spec.id} />
              }
            />
            <div className="p-5 pt-4">
              <DataTable
                columns={spec.columns}
                rows={block?.rows ?? []}
                totalRows={block?.total_rows ?? null}
                truncated={Boolean(block?.truncated)}
                defaultSort={spec.default_sort}
                defaultSortDir={spec.default_sort_dir}
                pageSize={spec.page_size}
                density={pres.density}
                emptyHint={spec.empty_hint}
              />
            </div>
          </Card>
        );
      })}

      <SourceFootnote envelope={data} />
    </div>
  );
}
