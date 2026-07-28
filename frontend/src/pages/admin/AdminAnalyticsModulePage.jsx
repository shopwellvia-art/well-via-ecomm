import { useMemo } from 'react';
import { Navigate, useParams } from 'react-router-dom';

import { AnalyticsShell } from '@/components/analytics/AnalyticsShell.jsx';
import { useUrlFilters } from '@/components/analytics/FilterBar.jsx';
import { ViewRenderer } from '@/components/analytics/ViewRenderer.jsx';
import { defaultView, getModule, getView, moduleRoute, viewRoute } from '@/features/analytics/registry.js';
import { toSearchParams } from '@/features/analytics/filters.js';
import { useAnalyticsView } from '@/features/analytics/hooks.js';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { AdminPage } from '@/components/admin/AdminPage.jsx';

/**
 * Serves both `/admin/analytics/:moduleSlug` and
 * `/admin/analytics/:moduleSlug/:viewSlug`.
 *
 * One page for all 73 views. Everything that varies between them lives in the
 * registry — this file contains no view-specific logic and must stay that way,
 * because the moment it grows a `if (viewSlug === ...)` the metadata-driven
 * design has quietly become 73 pages again.
 */
export default function AdminAnalyticsModulePage() {
  const { moduleSlug, viewSlug } = useParams();
  const module = getModule(moduleSlug);
  const view = viewSlug ? getView(moduleSlug, viewSlug) : defaultView(moduleSlug);

  // Filters live in the URL, so a refresh or a shared link reopens the same
  // question. `useUrlFilters` is view-aware: it drops keys this view does not
  // declare rather than sending filters the backend would ignore.
  const { filters, setFilters, resetFilters, activeCount } = useUrlFilters(view);

  // Serialized, not raw: the raw object carries ~24 defaults, which would both
  // over-send and desynchronise the react-query key from the backend's
  // default-dropping cache key.
  const params = useMemo(
    () => (view ? Object.fromEntries(toSearchParams(filters, view)) : {}),
    [filters, view]
  );

  const query = useAnalyticsView(view, moduleSlug, view?.slug, params);

  if (!module) {
    return (
      <AdminPage title="Analytics">
        <EmptyState
          iconTone="warning"
          title="Unknown analytics module"
          description={`No module named "${moduleSlug}".`}
        />
      </AdminPage>
    );
  }

  // A bare module URL redirects to its first view, so the module route is a
  // shareable shortcut rather than a dead end.
  if (!viewSlug && view) {
    return <Navigate to={viewRoute(module.slug, view.slug)} replace />;
  }

  if (!view) {
    return (
      <AdminPage title={module.name}>
        <EmptyState
          iconTone="warning"
          title="Unknown view"
          description={`"${viewSlug}" is not a view of ${module.name}.`}
          action={<a className="text-accent underline" href={moduleRoute(module.slug)}>Back to {module.name}</a>}
        />
      </AdminPage>
    );
  }

  return (
    <AnalyticsShell
      module={module}
      view={view}
      envelope={query.data ?? null}
      filters={filters}
      onFiltersChange={setFilters}
      onFiltersReset={resetFilters}
      activeFilterCount={activeCount}
    >
      <ViewRenderer viewDef={view} envelope={query.data ?? null} query={query} filters={filters} />
    </AnalyticsShell>
  );
}
