import { Skeleton } from '@/components/ui/Skeleton.jsx';

/**
 * Suspense fallback for lazily-loaded routes.
 * Matches the typical page header + product grid layout to keep CLS near zero.
 * Two layout modes:
 *   'grid'  (default) — header + product grid (storefront pages)
 *   'admin' — header + stat cards + table rows (admin pages)
 */
export function PageFallback({ layout = 'grid' }) {
  if (layout === 'admin') {
    return (
      <div className="mx-auto w-full max-w-content px-6 py-10">
        {/* Page title */}
        <Skeleton className="h-8 w-48 rounded-sm" />
        <Skeleton variant="text" className="mt-2 w-72 h-4" />
        {/* KPI row */}
        <div className="mt-8 grid grid-cols-2 gap-4 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-28 rounded-lg" />
          ))}
        </div>
        {/* Content row */}
        <div className="mt-6 grid gap-4 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-64 rounded-lg" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-content px-6 py-12">
      {/* Page title */}
      <Skeleton className="h-10 w-64 rounded-sm" />
      {/* Product grid */}
      <div className="mt-8 grid grid-cols-2 gap-5 md:grid-cols-3 lg:grid-cols-4">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="aspect-[4/5] rounded-lg" />
        ))}
      </div>
    </div>
  );
}
