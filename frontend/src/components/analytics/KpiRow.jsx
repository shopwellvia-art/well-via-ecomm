import { getKpi } from '@/features/analytics/registry.js';
import { AnalyticsKpiCard } from './AnalyticsKpiCard.jsx';
import { cn } from '@/lib/utils.js';

/**
 * The view's headline metrics, in the order the registry declares them.
 *
 * Order is the backend's, not a sort: `kpis.py` puts `order_value_created`
 * before `paid_order_value` because the gap between them is the drop-off, and
 * a grid that reordered by value would destroy that adjacency.
 *
 * A KPI the envelope omitted still gets a card, marked INCOMPLETE with an
 * em-dash. Dropping it would be the quieter failure: the view would look
 * complete while a metric it promised had silently gone missing, and nobody
 * would go looking for a card that was never there.
 *
 * Props:
 *   view      — registry view definition
 *   envelope  — the analytics view envelope
 *   loading   — true during a background refetch (values keep showing)
 *   icons     — optional { [kpiId]: LucideComponent } override
 */

/**
 * Static column classes per count — Tailwind scans source text, so these
 * cannot be built by interpolation. Five KPIs get their own row rather than
 * wrapping 4 + 1, which reads as a stray card.
 */
const GRID_BY_COUNT = {
  1: 'grid-cols-1',
  2: 'grid-cols-1 sm:grid-cols-2',
  3: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3',
  4: 'grid-cols-1 sm:grid-cols-2 xl:grid-cols-4',
  5: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5',
  6: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6',
};

const GRID_FALLBACK = 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4';

export function KpiRow({ view, envelope, loading = false, icons = {}, className }) {
  const ids = Array.isArray(view?.kpis) ? view.kpis : [];
  if (ids.length === 0) return null;

  return (
    <div
      className={cn('grid gap-4', GRID_BY_COUNT[ids.length] ?? GRID_FALLBACK, className)}
    >
      {ids.map((id) => {
        const kpiDef = getKpi(id);
        const returned = envelope?.kpis?.[id];
        // Absent is not zero and not authoritative — it is a metric the
        // resolver could not produce, so it says so on the card.
        const kpi = returned ?? {
          kpi_id: id,
          value: null,
          previous: null,
          delta_pct: null,
          format: kpiDef?.unit ?? 'int',
          quality: 'INCOMPLETE',
          coverage_pct: null,
          inputs_missing: [],
        };

        return (
          <AnalyticsKpiCard
            key={id}
            kpi={kpi}
            kpiDef={kpiDef}
            icon={icons[id]}
            lastUpdatedAt={envelope?.last_updated_at ?? null}
            freshness={envelope?.freshness ?? view?.freshness ?? 'daily'}
            loading={loading}
          />
        );
      })}
    </div>
  );
}
