import { useState } from 'react';
import { Download } from 'lucide-react';
import { Button } from '@/components/ui/Button.jsx';
import { toast } from '@/components/ui/Toaster.jsx';
import { analyticsApi } from '@/features/analytics/api.js';
import { toSearchParams } from '@/features/analytics/filters.js';
import { readApiErrorMessage } from '@/features/orders/api.js';

/**
 * Download one of a view's tables as CSV.
 *
 * Goes through the API rather than serialising the rows already on screen, and
 * the difference matters: the page holds a top-N capped at 200 rows, the export
 * endpoint resolves the same view at up to 50,000. Writing a CSV from the DOM
 * would silently hand someone a truncated file labelled as the full export, and
 * it would skip the audit row the backend writes for every export that leaves
 * the building.
 *
 * A blob download rather than an `<a href>`: the endpoint needs the bearer
 * token, and a plain link would arrive unauthenticated. Same idiom as the
 * invoice download in `features/orders/hooks.js`.
 *
 * When the server truncates it says so in `X-Analytics-Truncated`, and that is
 * re-raised as a warning toast — a CSV that stops early looks complete and
 * reconciles against nothing.
 *
 * Props:
 *   view      — the registry view (needs `slug`, `moduleSlug`, `export`)
 *   filters   — the current filter object
 *   tableId   — which of the view's tables; defaults to the first
 *   disabled  — force-disable (e.g. while the view is still loading)
 */
export function ExportButton({
  view,
  filters,
  tableId = null,
  disabled = false,
  size = 'sm',
  variant = 'secondary',
  className,
}) {
  const [busy, setBusy] = useState(false);

  // The registry says whether a view has anything to export. A view whose
  // output is a set of KPI cards has no table, and the endpoint 409s — better
  // to not offer the button than to explain the 409.
  if (!view?.export) return null;

  async function handleExport() {
    setBusy(true);
    try {
      const params = Object.fromEntries(toSearchParams(filters, view));
      const resp = await analyticsApi.export(
        {
          module_slug: view.moduleSlug,
          view_slug: view.slug,
          ...(tableId ? { table_id: tableId } : {}),
        },
        params,
      );

      const stamp = filters?.date_from && filters?.date_to
        ? `${filters.date_from}_${filters.date_to}`
        : (filters?.period ?? 'window');
      const filename = `${view.slug}${tableId ? `-${tableId}` : ''}-${stamp}.csv`;

      const blob = new Blob([resp.data], { type: 'text/csv;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);

      const truncated = resp.headers?.['x-analytics-truncated'];
      if (truncated && truncated !== 'false') {
        toast.error(
          'The export hit the server row cap and stops early. Narrow the window ' +
            'before reconciling against it.',
        );
      } else {
        toast.success(`Exported ${filename}`);
      }
    } catch (err) {
      toast.error(await readApiErrorMessage(err, 'Export failed.'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Button
      size={size}
      variant={variant}
      loading={busy}
      disabled={disabled}
      onClick={handleExport}
      className={className}
    >
      <Download className="size-4" aria-hidden="true" />
      Export CSV
    </Button>
  );
}
