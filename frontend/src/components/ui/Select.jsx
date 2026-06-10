import { forwardRef, useId } from 'react';
import { ChevronDown } from 'lucide-react';
import { cn } from '@/lib/utils.js';

/**
 * Labeled native select. Mirrors Input's states; helper/error reserve height.
 *
 * Props:
 *   label    — visible label text
 *   helper   — hint text below
 *   error    — error message (overrides helper, shown in danger color)
 *   required — adds visual * to label
 */
export const Select = forwardRef(function Select(
  { className, label, helper, error, id, required, children, ...props },
  ref,
) {
  const autoId = useId();
  const fieldId = id || autoId;
  const describedById = `${fieldId}-desc`;
  const hasError = Boolean(error);

  return (
    <div className="flex flex-col gap-1.5">
      {label && (
        <label
          htmlFor={fieldId}
          className="text-sm font-medium text-ink-secondary"
        >
          {label}
          {required && (
            <span className="ml-0.5 text-danger" aria-hidden="true">
              *
            </span>
          )}
        </label>
      )}
      <div className="relative">
        <select
          ref={ref}
          id={fieldId}
          required={required}
          aria-invalid={hasError ? 'true' : undefined}
          aria-describedby={helper || hasError ? describedById : undefined}
          aria-required={required ? 'true' : undefined}
          className={cn(
            'h-11 w-full appearance-none rounded-sm bg-bg-sunken pl-3.5 pr-10 text-sm text-ink-primary',
            'border border-line-subtle',
            'transition-[border-color,box-shadow] duration-200',
            'hover:border-line-strong',
            'focus-visible:border-accent focus-visible:outline-none',
            'focus-visible:[box-shadow:var(--accent-glow)]',
            'disabled:opacity-40 disabled:pointer-events-none disabled:cursor-not-allowed',
            hasError && [
              'border-danger',
              'focus-visible:border-danger',
              'focus-visible:[box-shadow:0_0_0_3px_rgba(239,68,68,0.18)]',
            ],
            className,
          )}
          {...props}
        >
          {children}
        </select>
        <ChevronDown
          className="pointer-events-none absolute right-3.5 top-1/2 size-4 -translate-y-1/2 text-ink-tertiary"
          aria-hidden="true"
        />
      </div>
      <p
        id={describedById}
        role={hasError ? 'alert' : undefined}
        className={cn(
          'min-h-[1.25rem] text-xs',
          hasError ? 'text-danger' : 'text-ink-tertiary',
        )}
      >
        {error || helper || ''}
      </p>
    </div>
  );
});
