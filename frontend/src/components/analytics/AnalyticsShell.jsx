import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { CornerDownLeft, Search } from 'lucide-react';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { getViewIcon } from '@/features/analytics/registry.icons.js';
import { searchViews } from '@/features/analytics/search.js';
import { GATED_STATES } from '@/features/analytics/viewState.js';
import { AnalyticsModuleHeader } from './AnalyticsModuleHeader.jsx';
import { AnalyticsViewSelector } from './AnalyticsViewSelector.jsx';
import { FilterBar, useUrlFilters } from './FilterBar.jsx';
import { cn } from '@/lib/utils.js';

/**
 * The frame every analytics view is rendered inside.
 *
 * `AdminPage` gives the heading, the divider and the staggered reveal the rest
 * of the admin already uses, so an analytics view does not announce itself as a
 * different product. On top of that: module context, the view switcher, the
 * filter bar, and a command menu.
 *
 * The command menu is not a flourish. Twelve modules of seventy-three views is
 * well past what anyone navigates by reading a sidebar — by view forty the
 * scroll is longer than the screen and people stop exploring. Typing is the
 * primary way in, so it is bound to both `/` (what every reader expects) and
 * `⌘K` (what every operator expects), and the ranking behind it lives in the
 * pure `search.js` where it can be tested.
 */

/** True when a keystroke belongs to whatever the operator is typing into. */
function isTypingTarget(target) {
  if (!target) return false;
  const tag = target.tagName;
  return (
    tag === 'INPUT' ||
    tag === 'TEXTAREA' ||
    tag === 'SELECT' ||
    target.isContentEditable === true
  );
}

function CommandMenu({ open, onClose, module }) {
  const navigate = useNavigate();
  const inputRef = useRef(null);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);

  const results = useMemo(() => {
    if (query.trim() === '') {
      // An empty box offers the module you are already in rather than an
      // arbitrary alphabetical slice — the next view someone wants is almost
      // always a neighbour of the one they are looking at.
      return (module?.views ?? []).slice(0, 8).map((view) => ({ view, score: 0 }));
    }
    return searchViews(query, { limit: 8 });
  }, [query, module]);

  useEffect(() => {
    setActive(0);
  }, [query]);

  useEffect(() => {
    if (open) {
      setQuery('');
      // Autofocus after paint so the dialog is in the tree when focus moves.
      const id = requestAnimationFrame(() => inputRef.current?.focus());
      return () => cancelAnimationFrame(id);
    }
    return undefined;
  }, [open]);

  if (!open) return null;

  function go(index) {
    const hit = results[index];
    if (!hit) return;
    navigate(hit.view.to);
    onClose();
  }

  function onKeyDown(e) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((i) => Math.min(results.length - 1, i + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((i) => Math.max(0, i - 1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      go(active);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 px-4 pt-[12vh]"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Find an analytics view"
        onClick={(e) => e.stopPropagation()}
        className="animate-scaleIn w-full max-w-xl overflow-hidden rounded-lg border border-line-subtle bg-bg-elevated shadow-lg"
      >
        <div className="flex items-center gap-2 border-b border-line-subtle px-4">
          <Search className="size-4 shrink-0 text-ink-tertiary" aria-hidden="true" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Search 73 views — try “cohort”, “refunds”, “courier”…"
            aria-label="Search analytics views"
            aria-controls="analytics-command-results"
            className="h-12 w-full bg-transparent text-sm text-ink-primary outline-none placeholder:text-ink-tertiary"
          />
          <kbd className="hidden shrink-0 rounded-xs border border-line-subtle px-1.5 py-0.5 text-[10px] text-ink-tertiary sm:block">
            Esc
          </kbd>
        </div>

        <ul id="analytics-command-results" className="max-h-[50vh] overflow-y-auto py-1">
          {results.length === 0 && (
            <li className="px-4 py-6 text-center text-sm text-ink-tertiary">
              Nothing matches “{query}”.
            </li>
          )}
          {results.map((hit, i) => {
            const Icon = getViewIcon(hit.view.slug);
            const gated = GATED_STATES.has(hit.view.state);
            return (
              <li key={hit.view.to}>
                <button
                  type="button"
                  onMouseEnter={() => setActive(i)}
                  onClick={() => go(i)}
                  className={cn(
                    'flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors',
                    'focus-visible:focus-ring',
                    i === active ? 'bg-fill' : 'hover:bg-fill/60',
                  )}
                >
                  <Icon className="size-4 shrink-0 text-ink-tertiary" aria-hidden="true" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-ink-primary">
                      {hit.view.name}
                    </span>
                    <span className="block truncate text-[11px] text-ink-tertiary">
                      {hit.view.moduleName}
                    </span>
                  </span>
                  {gated && (
                    <Badge tone="neutral" outline>
                      Gated
                    </Badge>
                  )}
                  {i === active && (
                    <CornerDownLeft className="size-3.5 shrink-0 text-ink-tertiary" aria-hidden="true" />
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

export function AnalyticsShell({
  module,
  view,
  envelope = null,
  filters: filtersProp = null,
  onFiltersChange = null,
  onFiltersReset = null,
  activeFilterCount = null,
  allowedSlugs = null,
  optionsByKey = {},
  headerAction = null,
  filterAction = null,
  children,
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const triggerRef = useRef(null);

  // Always called; the props win when the page owns the filter state. Keeping
  // the hook unconditional is what lets the shell work either way.
  const own = useUrlFilters(view);
  const filters = filtersProp ?? own.filters;
  const setFilters = onFiltersChange ?? own.setFilters;
  const resetFilters = onFiltersReset ?? own.reset;
  const activeCount = activeFilterCount ?? own.activeCount;

  const closeMenu = useCallback(() => {
    setMenuOpen(false);
    triggerRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKeyDown(e) {
      if (isTypingTarget(e.target)) return;
      if (e.key === '/' || ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k')) {
        e.preventDefault();
        setMenuOpen(true);
      }
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, []);

  return (
    <>
      <AdminPage
        title={view?.name ?? module?.name ?? 'Analytics'}
        description={view?.summary ?? module?.summary}
        maxWidth="max-w-[1440px]"
        action={
          <div className="flex items-center gap-2">
            <button
              ref={triggerRef}
              type="button"
              onClick={() => setMenuOpen(true)}
              className={cn(
                'inline-flex items-center gap-2 rounded-sm border border-line-subtle bg-bg-elevated',
                'px-3 py-2 text-xs text-ink-tertiary transition-colors',
                'hover:border-line-strong hover:text-ink-secondary focus-visible:focus-ring',
              )}
            >
              <Search className="size-3.5" aria-hidden="true" />
              <span className="hidden sm:inline">Find a view</span>
              <kbd className="rounded-xs border border-line-subtle px-1 py-0.5 text-[10px]">/</kbd>
            </button>
            {headerAction}
          </div>
        }
      >
        <AnalyticsModuleHeader module={module} view={view} envelope={envelope} />

        <AnalyticsViewSelector module={module} allowedSlugs={allowedSlugs} />

        {view && (
          <FilterBar
            view={view}
            filters={filters}
            onChange={setFilters}
            onReset={resetFilters}
            activeCount={activeCount}
            resolved={envelope?.filters ?? null}
            optionsByKey={optionsByKey}
            action={filterAction}
          />
        )}

        {children}
      </AdminPage>

      <CommandMenu open={menuOpen} onClose={closeMenu} module={module} />
    </>
  );
}
