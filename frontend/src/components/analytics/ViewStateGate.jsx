import { Link } from 'react-router-dom';
import { AlertCircle, Ban, Lock, PlugZap, RotateCcw, Table2 } from 'lucide-react';
import { Badge } from '@/components/ui/Badge.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Card } from '@/components/ui/Card.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { RENDER_KIND, VIEW_STATE } from '@/features/analytics/viewState.js';
import { DataCompletenessWarning } from './DataCompletenessWarning.jsx';
import { cn } from '@/lib/utils.js';

/**
 * Everything a view can be when it is not data.
 *
 * The four non-data kinds are rendered here and nowhere else, so a gated view
 * looks the same in all 73 places it can occur and no page can invent its own
 * "coming soon" copy.
 *
 * The distinction that earns this component its keep: an *actionable* gate gets
 * a "Set up" affordance and a non-actionable one does not. `NOT_APPLICABLE`
 * means the concept does not exist in a single-store deployment — offering to
 * fix that would send an admin looking for a setting that will never exist, and
 * the cost of that is a support ticket and a bit of lost trust.
 *
 * Props:
 *   state        — the result of resolveViewState()
 *   viewDef      — registry entry, used to shape the loading skeleton
 *   availability — the EFFECTIVE state when the server downgraded the view;
 *                  falls back to the registry's declared one
 *   onRetry      — optional retry handler for the error kind
 */

/** Acronyms that look wrong in Title Case. */
const ACRONYMS = new Set([
  'ga4', 'gtm', 'api', 'b2b', 'sms', 'hsn', 'rfm', 'utm', 'crm', 'sku', 'cod', 'db',
]);

/**
 * `ga4_data_api` -> "GA4 data API". Generic on purpose: a capability added to
 * the backend enum next month renders correctly here without a frontend
 * release, which is the only way a hard-coded label table ever stays honest.
 */
export function humanizeCapability(id) {
  return String(id || '')
    .split('_')
    .map((word, i) =>
      ACRONYMS.has(word)
        ? word.toUpperCase()
        : i === 0
          ? word.charAt(0).toUpperCase() + word.slice(1)
          : word,
    )
    .join(' ');
}

/** Which icon fronts a gate — the shape of the blocker, not its severity. */
function gateIcon(availability, actionable) {
  if (availability === VIEW_STATE.NOT_APPLICABLE) return Ban;
  if (availability === VIEW_STATE.FEATURE_REQUIRED) return Lock;
  return actionable ? PlugZap : Lock;
}

function LoadingSkeleton({ viewDef }) {
  const kpiCount = Math.min(viewDef?.kpis?.length || 4, 6);
  const charts = viewDef?.charts?.length ? viewDef.charts : [{ id: 'a', span: 2 }];
  const tables = viewDef?.tables ?? [];

  return (
    <div className="space-y-6" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading this view…</span>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: kpiCount }).map((_, i) => (
          <Skeleton key={i} className="h-[168px] w-full rounded-sm" />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {charts.map((c, i) => (
          <Skeleton
            key={c.id ?? i}
            className={cn('h-80 w-full rounded-lg', c.span === 2 && 'lg:col-span-2')}
          />
        ))}
      </div>
      {tables.map((t, i) => (
        <Skeleton key={t.id ?? i} className="h-64 w-full rounded-lg" />
      ))}
    </div>
  );
}

function GatePanel({ gate, viewDef, availability }) {
  const actionable = Boolean(gate?.actionable);
  const Icon = gateIcon(availability, actionable);
  const requires = gate?.requires ?? [];

  return (
    <Card className="px-6 py-12">
      <div className="mx-auto flex max-w-md flex-col items-center gap-3 text-center">
        <span
          className={cn(
            'grid size-12 shrink-0 place-items-center rounded-full',
            actionable ? 'bg-accent/12 text-accent' : 'bg-fill text-ink-secondary',
          )}
        >
          <Icon className="size-6" aria-hidden="true" />
        </span>

        <h3 className="text-h3 font-semibold text-ink-primary">{gate?.title}</h3>
        <p className="text-balance text-sm leading-relaxed text-ink-secondary">
          {gate?.body}
        </p>

        {requires.length > 0 && (
          <div className="mt-1 flex flex-col items-center gap-2">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-tertiary">
              {actionable ? 'Needs' : 'Would need'}
            </p>
            <ul className="flex flex-wrap justify-center gap-1.5">
              {requires.map((cap) => (
                <li key={cap}>
                  <Badge tone={actionable ? 'accent' : 'neutral'} outline>
                    {humanizeCapability(cap)}
                  </Badge>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/*
         * Only an actionable gate gets a way forward. A NOT_APPLICABLE view has
         * no setting behind it, and a button that leads nowhere is worse than
         * no button — it costs the reader a trip to find that out.
         */}
        {actionable ? (
          <Link to="/admin/settings" className="mt-3 rounded-xs focus-visible:focus-ring">
            <Button size="sm" variant="secondary">
              Set up in Settings
            </Button>
          </Link>
        ) : (
          <p className="mt-2 text-[11px] text-ink-tertiary">
            Nothing to configure — this is a property of how the store is set up,
            not a missing switch.
          </p>
        )}

        {viewDef?.summary && (
          <p className="mt-4 border-t border-line-subtle pt-4 text-[11px] leading-relaxed text-ink-tertiary">
            When available, this view would show: {viewDef.summary}
          </p>
        )}
      </div>
    </Card>
  );
}

export function ViewStateGate({ state, viewDef, availability, onRetry, className }) {
  if (!state) return null;

  if (state.kind === RENDER_KIND.LOADING) {
    return (
      <div className={className}>
        <LoadingSkeleton viewDef={viewDef} />
      </div>
    );
  }

  if (state.kind === RENDER_KIND.GATED) {
    return (
      <div className={className}>
        <GatePanel
          gate={state.gate}
          viewDef={state.viewDef ?? viewDef}
          availability={availability ?? (state.viewDef ?? viewDef)?.state}
        />
      </div>
    );
  }

  if (state.kind === RENDER_KIND.ERROR) {
    return (
      <div className={className}>
        <EmptyState
          icon={AlertCircle}
          iconTone="danger"
          title="This view could not be loaded"
          description={state.message}
          action={
            state.retryable && onRetry ? (
              <Button size="sm" variant="secondary" onClick={onRetry}>
                <RotateCcw className="size-4" aria-hidden="true" />
                Try again
              </Button>
            ) : null
          }
        />
      </div>
    );
  }

  // NO_DATA — the query ran and found nothing. Deliberately not a chart of
  // zeroes: "we aggregated nothing yet" and "the answer is zero" are different
  // facts and only one of them is a business result.
  return (
    <div className={cn('space-y-4', className)}>
      {state.warnings?.length > 0 && (
        <DataCompletenessWarning warnings={state.warnings} collapseAfter={3} />
      )}
      <EmptyState
        icon={Table2}
        title="Nothing to show for this window"
        description={state.message}
      />
    </div>
  );
}
