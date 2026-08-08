import { cn } from '@/lib/utils.js';

/**
 * Mutually-exclusive choice across a short, stable set of options.
 *
 * Lifted verbatim from the local copy in `AdminSalesAnalyticsPage` so the
 * granularity, comparison and status-basis pickers cannot drift apart across 73
 * views. Same markup, same tokens; only the props grew.
 *
 * Not a tab list and not routing: this changes a *parameter*, and the URL that
 * carries it is written by `filters.js`. Views are navigated with real links —
 * see `AnalyticsViewSelector`.
 *
 * Props:
 *   options   — [{ value, label, title?, disabled? }]
 *   value     — currently selected value
 *   onChange  — (value) => void
 *   size      — 'sm' (default) | 'md'
 *   label     — accessible group label
 *   block     — stretch to fill its container (mobile filter rows)
 */
export function SegmentedControl({
  options = [],
  value,
  onChange,
  size = 'sm',
  label,
  block = false,
  className,
}) {
  return (
    <div
      className={cn(
        'inline-flex rounded-sm border border-line-subtle bg-bg-elevated',
        block && 'flex w-full',
        className,
      )}
      role="group"
      aria-label={label}
    >
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange?.(opt.value)}
          aria-pressed={value === opt.value}
          disabled={opt.disabled}
          title={opt.title}
          className={cn(
            'font-medium transition-colors',
            size === 'md' ? 'px-4 py-2 text-sm' : 'px-3 py-1.5 text-xs',
            'first:rounded-l-sm last:rounded-r-sm focus-visible:focus-ring',
            block && 'flex-1',
            'disabled:cursor-not-allowed disabled:opacity-40',
            value === opt.value
              ? 'bg-accent text-ink-inverse shadow-glow-sm'
              : 'text-ink-secondary hover:bg-fill hover:text-ink-primary',
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
