import { forwardRef, useId } from 'react';
import { cn } from '@/lib/utils.js';

/**
 * Labeled multi-line field. Mirrors Input's states; helper/error reserve height.
 *
 * Props:
 *   label    — visible label text
 *   helper   — hint text below
 *   error    — error message (overrides helper, shown in danger color)
 *   required — adds visual * to label
 *   rows     — visible row count (default 4)
 *   maxRows  — clamps max-height via CSS (optional, in row units — each row ≈ 1.5rem)
 */
export const Textarea = forwardRef(function Textarea(
  { className, label, helper, error, id, rows = 4, maxRows, required, ...props },
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
      <textarea
        ref={ref}
        id={fieldId}
        rows={rows}
        required={required}
        aria-invalid={hasError ? 'true' : undefined}
        aria-describedby={helper || hasError ? describedById : undefined}
        aria-required={required ? 'true' : undefined}
        style={maxRows ? { maxHeight: `${maxRows * 1.5 + 1.25}rem` } : undefined}
        className={cn(
          'w-full rounded-sm bg-bg-sunken px-3.5 py-2.5 text-sm text-ink-primary',
          'border border-line-subtle placeholder:text-ink-tertiary resize-y',
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
      />
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
