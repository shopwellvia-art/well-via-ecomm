import { NavLink } from 'react-router-dom';
import { Lock } from 'lucide-react';
import { getViewIcon } from '@/features/analytics/registry.icons.js';
import { GATED_STATES } from '@/features/analytics/viewState.js';
import { cn } from '@/lib/utils.js';

/**
 * The views inside one module.
 *
 * Real `NavLink`s, not a tab widget. Each view is a distinct URL that must be
 * bookmarkable, shareable, openable in a new tab and reachable by the back
 * button — all of which a `role="tablist"` with click handlers quietly takes
 * away. The active styling is the only thing a tab control was ever offering
 * here, and `NavLink` already does that.
 *
 * A gated view is shown, dimmed, and still navigable. Hiding it would be the
 * kinder-looking choice and the worse one: an operator who has been told the
 * store has a funnel report needs to land on the page that explains why it is
 * empty, not to conclude the feature was removed.
 *
 * Views the *server* filtered out for lack of permission are a different case
 * and never reach this component — pass `allowedSlugs` from the navigation
 * payload and they are gone, with no "3 more (locked)" hint, because that hint
 * is a disclosure of what exists dressed up as a courtesy.
 *
 * Props:
 *   module        — registry module with its views
 *   allowedSlugs  — optional array/Set of view slugs this user may open
 */
export function AnalyticsViewSelector({ module, allowedSlugs = null, className }) {
  if (!module?.views?.length) return null;

  const allowed = allowedSlugs ? new Set(allowedSlugs) : null;
  const views = module.views.filter((v) => !allowed || allowed.has(v.slug));
  if (views.length === 0) return null;

  // `group` is optional in the contract and empty for most views; when nothing
  // declares one, the whole module is a single unlabelled row.
  const groups = [];
  for (const view of views) {
    const label = view.group || '';
    const existing = groups.find((g) => g.label === label);
    if (existing) existing.views.push(view);
    else groups.push({ label, views: [view] });
  }

  return (
    <nav
      aria-label={`${module.name} views`}
      className={cn('flex flex-col gap-2', className)}
    >
      {groups.map((group) => (
        <div key={group.label || '_'} className="flex flex-col gap-1.5">
          {group.label && (
            <span className="text-[11px] font-semibold uppercase tracking-wide text-ink-tertiary">
              {group.label}
            </span>
          )}
          {/*
           * Horizontal scroll rather than wrapping: a module with eight views
           * would otherwise reflow the whole page height between modules, and
           * the filter bar below it would jump as you navigate.
           */}
          <ul className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1">
            {group.views.map((view) => {
              const Icon = getViewIcon(view.slug);
              const gated = GATED_STATES.has(view.state);
              return (
                <li key={view.slug} className="shrink-0">
                  <NavLink
                    to={view.to}
                    end
                    title={gated ? `${view.name} — ${view.limitation || 'not available yet'}` : view.summary}
                    className={({ isActive }) =>
                      cn(
                        'inline-flex items-center gap-1.5 rounded-sm border px-3 py-1.5',
                        'text-xs font-medium transition-colors focus-visible:focus-ring',
                        isActive
                          ? 'border-accent bg-accent/12 text-accent'
                          : 'border-line-subtle bg-bg-elevated text-ink-secondary hover:border-line-strong hover:text-ink-primary',
                        gated && !isActive && 'opacity-60',
                      )
                    }
                  >
                    <Icon className="size-3.5 shrink-0" aria-hidden="true" />
                    {view.name}
                    {gated && (
                      <Lock className="size-3 shrink-0 text-ink-tertiary" aria-hidden="true" />
                    )}
                  </NavLink>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}
