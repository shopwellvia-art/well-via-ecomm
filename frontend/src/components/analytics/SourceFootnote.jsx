import { Database } from 'lucide-react';
import { FreshnessBadge } from './FreshnessBadge.jsx';
import { cn } from '@/lib/utils.js';

/**
 * Where the numbers above came from.
 *
 * Every view ends with this. Not as a legal disclaimer — as the thing that lets
 * a disagreement be settled. When the finance sheet and the dashboard differ,
 * the first question is always "through when, and over how many rows", and a
 * dashboard that cannot answer it loses the argument by default.
 *
 * `through` is the newest bucket the source actually holds. The gap between it
 * and the requested window is precisely how stale the answer is, which is what
 * stops "no rows yet" from reading as "zero sales".
 *
 * Props:
 *   envelope       — the analytics view envelope
 *   showFreshness  — render the FreshnessBadge inline (default true)
 */

/** rollup | live | external | derived — said in words a reader can act on. */
const KIND_LABEL = {
  rollup: 'pre-aggregated',
  live: 'queried live',
  external: 'external feed',
  derived: 'derived',
};

function formatThrough(through) {
  if (!through) return null;
  const day = new Date(`${through}T00:00:00`);
  if (Number.isNaN(day.getTime())) return String(through);
  return day.toLocaleDateString('en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

function formatRows(rows) {
  if (rows == null || !Number.isFinite(Number(rows))) return null;
  return new Intl.NumberFormat('en-IN').format(Number(rows));
}

export function SourceFootnote({ envelope, showFreshness = true, className }) {
  if (!envelope) return null;

  const sources = Array.isArray(envelope.sources) ? envelope.sources : [];
  const timezone = envelope.timezone || 'Asia/Kolkata';
  const currency = envelope.currency || 'INR';
  const resolved = envelope.filters ?? null;

  return (
    <footer
      className={cn(
        'mt-2 flex flex-col gap-2 rounded-lg border border-line-subtle bg-bg-sunken/60 px-4 py-3',
        'text-[11px] leading-relaxed text-ink-tertiary',
        className,
      )}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="inline-flex items-center gap-1.5 font-medium text-ink-secondary">
          <Database className="size-3.5 shrink-0" aria-hidden="true" />
          {sources.length === 1 ? 'Source' : 'Sources'}
        </span>

        {sources.length === 0 ? (
          <span>Not stated by the server for this view.</span>
        ) : (
          <ul className="flex flex-wrap items-center gap-x-3 gap-y-1">
            {sources.map((s) => {
              const through = formatThrough(s.through);
              const rows = formatRows(s.rows);
              return (
                <li key={s.id} className="flex items-center gap-1.5">
                  <span className="text-ink-secondary">{s.label || s.id}</span>
                  <span className="opacity-70">
                    ({KIND_LABEL[s.kind] ?? s.kind}
                    {through ? `, through ${through}` : ', no watermark'}
                    {rows ? `, ${rows} rows` : ''})
                  </span>
                </li>
              );
            })}
          </ul>
        )}

        {showFreshness && (
          <FreshnessBadge
            freshness={envelope.freshness}
            lastUpdatedAt={envelope.last_updated_at ?? null}
            computedAt={envelope.computed_at ?? null}
            showCadence
            className="ml-auto"
          />
        )}
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span>
          Times in <span className="text-ink-secondary">{timezone}</span>
        </span>
        <span aria-hidden="true">·</span>
        <span>
          Amounts in <span className="text-ink-secondary">{currency}</span>
        </span>
        {resolved?.date_from && resolved?.date_to && (
          <>
            <span aria-hidden="true">·</span>
            <span>
              Window{' '}
              <span className="nums text-ink-secondary">
                {resolved.date_from} → {resolved.date_to}
              </span>
              {resolved.compare_from && resolved.compare_to && (
                <>
                  {' '}
                  vs{' '}
                  <span className="nums text-ink-secondary">
                    {resolved.compare_from} → {resolved.compare_to}
                  </span>
                </>
              )}
            </span>
          </>
        )}
        {Array.isArray(resolved?.ignored) && resolved.ignored.length > 0 && (
          <>
            <span aria-hidden="true">·</span>
            <span
              className="text-warning"
              title="This view does not honour these filters, so they were not applied."
            >
              Ignored: {resolved.ignored.join(', ')}
            </span>
          </>
        )}
      </div>
    </footer>
  );
}
