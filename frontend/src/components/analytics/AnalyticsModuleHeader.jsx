import { Link } from 'react-router-dom';
import { ChevronRight } from 'lucide-react';
import { Badge } from '@/components/ui/Badge.jsx';
import { getModuleIcon } from '@/features/analytics/registry.icons.js';
import { GATED_STATES, VIEW_STATE } from '@/features/analytics/viewState.js';
import { FreshnessBadge } from './FreshnessBadge.jsx';
import { QualityBadge } from './QualityBadge.jsx';
import { cn } from '@/lib/utils.js';

/**
 * Which module this view belongs to, and what the view is currently worth.
 *
 * Sits directly under the page heading as a context strip. Two jobs:
 *
 *  - a way back to the module, because 73 views across 12 modules is past the
 *    point where an operator remembers how they got somewhere;
 *  - the view's *declared* state next to its *measured* one. Those can differ:
 *    the registry declares a ceiling and a runtime probe may lower it, so a
 *    view marked LIVE in Python can arrive PARTIAL because its rollup has no
 *    rows yet. Showing the effective value is the honest one; the registry
 *    value is what the code promised.
 *
 * Props:
 *   module    — registry module (with `to`, `name`, `summary`, `number`)
 *   view      — registry view, or null on a module landing page
 *   envelope  — the resolved envelope, when there is one
 */

const STATE_TONE = {
  [VIEW_STATE.LIVE]: 'success',
  [VIEW_STATE.PARTIAL]: 'warning',
};

const STATE_LABEL = {
  [VIEW_STATE.LIVE]: 'Live',
  [VIEW_STATE.PARTIAL]: 'Partial',
  [VIEW_STATE.INTEGRATION_REQUIRED]: 'Needs an integration',
  [VIEW_STATE.FEATURE_REQUIRED]: 'Needs a feature',
  [VIEW_STATE.BLOCKED_BY_MISSING_SOURCE]: 'Source unavailable',
  [VIEW_STATE.NOT_APPLICABLE]: 'Not applicable',
};

const STATE_TITLE = {
  [VIEW_STATE.LIVE]: 'Real data, documented formula, source and freshness stated.',
  [VIEW_STATE.PARTIAL]: 'Real data with a stated limitation — read the notices above the charts.',
};

export function AnalyticsModuleHeader({ module, view, envelope, action, className }) {
  if (!module) return null;

  const ModuleIcon = getModuleIcon(module.slug);
  // Registry declares the ceiling; the envelope may have lowered it. Never the
  // other way round — see `resolveViewState`.
  const effective = envelope?.availability ?? view?.state;
  const downgraded = Boolean(view?.state && effective && effective !== view.state);
  const gated = GATED_STATES.has(effective);

  return (
    <div
      className={cn(
        'flex flex-col gap-3 rounded-lg border border-line-subtle bg-bg-elevated px-4 py-3',
        'sm:flex-row sm:items-center sm:justify-between',
        className,
      )}
    >
      <div className="flex min-w-0 items-center gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-sm bg-accent/12 text-accent">
          <ModuleIcon className="size-5" aria-hidden="true" />
        </span>
        <div className="min-w-0">
          <nav aria-label="Breadcrumb" className="flex items-center gap-1 text-xs">
            <Link
              to={module.to}
              className="rounded-xs font-medium text-ink-secondary transition-colors hover:text-ink-primary focus-visible:focus-ring"
            >
              {module.name}
            </Link>
            {view && (
              <>
                <ChevronRight className="size-3 text-ink-tertiary" aria-hidden="true" />
                <span className="truncate text-ink-tertiary" aria-current="page">
                  View {view.number}
                </span>
              </>
            )}
          </nav>
          <p className="mt-0.5 truncate text-[11px] text-ink-tertiary">
            {view?.summary || module.summary}
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 sm:justify-end">
        {effective && (
          <Badge
            tone={STATE_TONE[effective] ?? 'neutral'}
            dot
            title={
              downgraded
                ? `Declared ${STATE_LABEL[view.state] ?? view.state}, but the server ` +
                  `reported ${STATE_LABEL[effective] ?? effective} for this window.`
                : STATE_TITLE[effective]
            }
          >
            {STATE_LABEL[effective] ?? effective}
            {downgraded && <span className="opacity-70">(was {STATE_LABEL[view.state]})</span>}
          </Badge>
        )}

        {!gated && envelope && (
          <>
            <QualityBadge
              quality={envelope.quality}
              coveragePct={envelope.coverage_pct ?? null}
            />
            <FreshnessBadge
              freshness={envelope.freshness ?? view?.freshness}
              lastUpdatedAt={envelope.last_updated_at ?? null}
              computedAt={envelope.computed_at ?? null}
              showCadence
            />
          </>
        )}

        {action}
      </div>
    </div>
  );
}
