import { forwardRef, useId } from 'react';
import { cn } from '@/lib/utils.js';

/**
 * Labeled form field.
 *
 * The label is persistent and associated — placeholder text is never the only
 * label. Helper/error text reserves height (no CLS).
 *
 * Props:
 *   label    — visible label text (rendered above the input)
 *   helper   — helper/hint text shown below (muted)
 *   error    — error message (shown below in danger color, overrides helper)
 *   icon     — Lucide icon component prepended inside the input
 *   suffix   — Lucide icon component appended inside the input (e.g. clear/eye)
 *   required — adds a * to the label for visual required indication
 */
export const Input = forwardRef(function Input(
  {
    className,
    label,
    helper,
    error,
    id,
    type = 'text',
    icon: Icon,
    suffix: Suffix,
    required,
    ...props
  },
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
        {Icon && (
          <Icon
            className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-ink-tertiary"
            aria-hidden="true"
          />
        )}
        <input
          ref={ref}
          id={fieldId}
          type={type}
          required={required}
          aria-invalid={hasError ? 'true' : undefined}
          aria-describedby={helper || hasError ? describedById : undefined}
          aria-required={required ? 'true' : undefined}
          className={cn(
            'h-11 w-full rounded-sm bg-bg-sunken px-3.5 text-sm text-ink-primary',
            'border border-line-subtle placeholder:text-ink-tertiary',
            'transition-[border-color,box-shadow] duration-200',
            'hover:border-line-strong',
            // Focus: accent border + soft glow — legible at AA contrast
            'focus-visible:border-accent focus-visible:outline-none',
            'focus-visible:[box-shadow:var(--accent-glow)]',
            'disabled:opacity-40 disabled:pointer-events-none disabled:cursor-not-allowed',
            Icon && 'pl-10',
            Suffix && 'pr-10',
            hasError && [
              'border-danger',
              'focus-visible:border-danger',
              'focus-visible:[box-shadow:0_0_0_3px_rgba(239,68,68,0.18)]',
            ],
            className,
          )}
          {...props}
        />
        {Suffix && (
          <Suffix
            className="pointer-events-none absolute right-3.5 top-1/2 size-4 -translate-y-1/2 text-ink-tertiary"
            aria-hidden="true"
          />
        )}
      </div>
      {/* Reserved line so layout never shifts when an error appears */}
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
