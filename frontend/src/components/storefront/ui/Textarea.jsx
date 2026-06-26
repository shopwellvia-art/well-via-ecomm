import { forwardRef, useId } from 'react';
import { cn } from '@/lib/utils.js';

/**
 * Wellness-twin of @/components/ui/Textarea — identical public API, wellness palette.
 *
 * Labeled multi-line field. Mirrors Input's states; helper/error reserve height.
 *
 * Props mirror @/components/ui/Textarea exactly:
 *   label, helper, error, required, rows, maxRows, id, className,
 *   plus all native textarea props.
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
          className="text-sm font-medium text-wink"
        >
          {label}
          {required && (
            <span className="ml-0.5 text-red-600" aria-hidden="true">
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
          'w-full rounded-xl bg-wpaper px-3.5 py-2.5 text-sm text-wink',
          'border border-wline placeholder:text-wmuted resize-y',
          'transition-[border-color,box-shadow] duration-200',
          'hover:border-wgreen/40',
          'focus-visible:border-wgreen focus-visible:outline-none',
          'disabled:opacity-40 disabled:pointer-events-none disabled:cursor-not-allowed',
          hasError && [
            'border-red-400',
            'focus-visible:border-red-400',
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
          hasError ? 'text-red-600' : 'text-wmuted',
        )}
      >
        {error || helper || ''}
      </p>
    </div>
  );
});
