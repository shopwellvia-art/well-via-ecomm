import { useMemo, useState } from 'react';
import { ArrowUpDown, ChevronDown, ChevronUp, Table2 } from 'lucide-react';
import { Button } from '@/components/ui/Button.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { formatValue } from '@/features/analytics/format.js';
import { isMissing } from '@/features/analytics/viewState.js';
import { cn } from '@/lib/utils.js';

/**
 * The tabular primitive the admin never had.
 *
 * Thirteen pages hand-roll a `<table>` with the same wrapper, the same sunken
 * header row and the same uppercase tertiary labels; three of them have drifted
 * a padding step. This is that markup, once, with the three things a
 * hand-rolled table always skips: sorting that puts missing values last,
 * pagination, and an explicit notice when the backend capped the rows.
 *
 * The truncation notice is not decoration. `TableBlock.truncated` means the
 * rows on screen are a top-N of a larger population — so the column totals a
 * reader might add up are not the real totals, and a client-side sort reorders
 * the sample rather than the population. Both are stated rather than implied.
 *
 * Props:
 *   columns        — TableSpec columns: [{ key, label, format, align, sortable }]
 *   rows           — TableBlock rows
 *   totalRows      — TableBlock total_rows (the population, not the page)
 *   truncated      — TableBlock truncated
 *   defaultSort    — column key to sort by initially
 *   defaultSortDir — 'asc' | 'desc'
 *   pageSize       — rows per page (0 disables pagination)
 *   density        — 'comfortable' (default) | 'compact'
 *   emptyHint      — copy for the no-rows state
 *   renderCell     — (row, column) => node, escape hatch for links and badges
 */

const ALIGN_CLASS = { left: 'text-left', right: 'text-right', center: 'text-center' };

const DENSITY = {
  comfortable: { head: 'px-5 py-3', cell: 'px-5 py-3.5' },
  compact: { head: 'px-4 py-2', cell: 'px-4 py-2' },
};

/**
 * Missing sorts last in BOTH directions.
 *
 * The alternative — treating `null` as smaller than every number — puts every
 * un-computable row at the top of an ascending sort, which reads as "these are
 * the smallest" rather than "these are unknown". Same reasoning as the em-dash.
 */
function compareValues(a, b, direction) {
  const aMissing = isMissing(a) || a === '';
  const bMissing = isMissing(b) || b === '';
  if (aMissing && bMissing) return 0;
  if (aMissing) return 1;
  if (bMissing) return -1;

  const aNum = typeof a === 'number' ? a : Number(a);
  const bNum = typeof b === 'number' ? b : Number(b);
  const numeric =
    Number.isFinite(aNum) && Number.isFinite(bNum) && String(a).trim() !== '' && String(b).trim() !== '';

  const result = numeric
    ? aNum - bNum
    : String(a).localeCompare(String(b), 'en-IN', { numeric: true, sensitivity: 'base' });

  return direction === 'asc' ? result : -result;
}

function SortIcon({ active, direction }) {
  if (!active) return <ArrowUpDown className="size-3 opacity-40" aria-hidden="true" />;
  return direction === 'asc' ? (
    <ChevronUp className="size-3" aria-hidden="true" />
  ) : (
    <ChevronDown className="size-3" aria-hidden="true" />
  );
}

export function DataTable({
  columns = [],
  rows = [],
  totalRows = null,
  truncated = false,
  defaultSort = '',
  defaultSortDir = 'desc',
  pageSize = 25,
  density = 'comfortable',
  emptyHint = '',
  renderCell,
  getRowKey,
  minWidth = 760,
  className,
}) {
  const [sortKey, setSortKey] = useState(defaultSort || '');
  const [sortDir, setSortDir] = useState(defaultSortDir === 'asc' ? 'asc' : 'desc');
  const [page, setPage] = useState(0);

  const pad = DENSITY[density] ?? DENSITY.comfortable;

  const sorted = useMemo(() => {
    if (!sortKey) return rows;
    return [...rows].sort((a, b) => compareValues(a?.[sortKey], b?.[sortKey], sortDir));
  }, [rows, sortKey, sortDir]);

  const paginated = pageSize > 0;
  const pageCount = paginated ? Math.max(1, Math.ceil(sorted.length / pageSize)) : 1;
  const safePage = Math.min(page, pageCount - 1);
  const visible = paginated
    ? sorted.slice(safePage * pageSize, safePage * pageSize + pageSize)
    : sorted;

  function toggleSort(key) {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
    setPage(0);
  }

  if (rows.length === 0) {
    return (
      <EmptyState
        icon={Table2}
        size="sm"
        title="No rows for this window"
        description={emptyHint || undefined}
        className={className}
      />
    );
  }

  return (
    <div className={cn('flex flex-col gap-3', className)}>
      <div className="overflow-x-auto rounded-lg border border-line-subtle bg-bg-elevated">
        <table className="w-full" style={{ minWidth }}>
          <thead>
            <tr className="border-b border-line-subtle bg-bg-sunken text-left">
              {columns.map((col) => {
                const active = sortKey === col.key;
                const align = ALIGN_CLASS[col.align] ?? ALIGN_CLASS.left;
                return (
                  <th
                    key={col.key}
                    scope="col"
                    aria-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
                    className={cn(
                      pad.head,
                      align,
                      'text-xs font-semibold uppercase tracking-wide text-ink-tertiary',
                    )}
                  >
                    {col.sortable === false ? (
                      col.label
                    ) : (
                      <button
                        type="button"
                        onClick={() => toggleSort(col.key)}
                        className={cn(
                          'inline-flex items-center gap-1 rounded-xs uppercase',
                          'transition-colors hover:text-ink-primary focus-visible:focus-ring',
                          active && 'text-ink-secondary',
                          col.align === 'right' && 'flex-row-reverse',
                        )}
                      >
                        {col.label}
                        <SortIcon active={active} direction={sortDir} />
                      </button>
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {visible.map((row, i) => (
              <tr
                key={getRowKey?.(row, i) ?? `${safePage}-${i}`}
                className="border-t border-line-subtle transition-colors duration-150 hover:bg-fill/50"
              >
                {columns.map((col) => {
                  const custom = renderCell?.(row, col);
                  const isNumeric = col.format && col.format !== 'text';
                  return (
                    <td
                      key={col.key}
                      className={cn(
                        pad.cell,
                        ALIGN_CLASS[col.align] ?? ALIGN_CLASS.left,
                        'text-sm text-ink-primary',
                        isNumeric && 'nums',
                      )}
                    >
                      {custom !== undefined && custom !== null
                        ? custom
                        : formatValue(row?.[col.key], col.format ?? 'text')}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-ink-tertiary">
          {paginated && sorted.length > pageSize ? (
            <>
              Showing{' '}
              <span className="nums text-ink-secondary">
                {safePage * pageSize + 1}–{Math.min((safePage + 1) * pageSize, sorted.length)}
              </span>{' '}
              of <span className="nums text-ink-secondary">{sorted.length}</span> rows
            </>
          ) : (
            <>
              <span className="nums text-ink-secondary">{sorted.length}</span>{' '}
              {sorted.length === 1 ? 'row' : 'rows'}
            </>
          )}
          {totalRows != null && totalRows > rows.length && (
            <>
              {' '}
              of <span className="nums text-ink-secondary">{totalRows}</span> in the window
            </>
          )}
        </p>

        {paginated && pageCount > 1 && (
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="secondary"
              disabled={safePage === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              Previous
            </Button>
            <span className="nums text-xs text-ink-tertiary">
              {safePage + 1} / {pageCount}
            </span>
            <Button
              size="sm"
              variant="secondary"
              disabled={safePage >= pageCount - 1}
              onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
            >
              Next
            </Button>
          </div>
        )}
      </div>

      {/*
       * Stated, not implied. A capped table's columns do not add up to the
       * window's totals, and sorting it reorders the sample the server chose —
       * both of which a reader will otherwise assume the opposite of.
       */}
      {truncated && (
        <p className="rounded-sm border border-warning/30 bg-warning/8 px-3 py-2 text-[11px] leading-relaxed text-ink-secondary">
          This table was capped by the server
          {totalRows != null && (
            <>
              {' '}
              at <span className="nums">{rows.length}</span> of{' '}
              <span className="nums">{totalRows}</span> rows
            </>
          )}
          . It is a top-N, so these columns do not sum to the window&rsquo;s totals and
          sorting here reorders the returned rows, not the whole population. Narrow the
          window or export the full table for a complete answer.
        </p>
      )}
    </div>
  );
}
