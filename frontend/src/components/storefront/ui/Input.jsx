import { forwardRef, useId } from 'react';
import { cn } from '@/lib/utils.js';

/**
 * Wellness-twin of @/components/ui/Input — identical public API, wellness palette.
 *
 * Labeled form field. Label is persistent and associated — placeholder text is
 * never the only label. Helper/error text reserves height (no CLS).
 *
 * Props mirror @/components/ui/Input exactly:
 *   label, helper, error, icon, suffix, required, id, type, className,
 *   plus all native input props.
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
      <div className="relative">
        {Icon && (
          <Icon
            className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-wmuted"
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
            'h-11 w-full rounded-xl bg-wpaper px-3.5 text-sm text-wink',
            'border border-wline placeholder:text-wmuted',
            'transition-[border-color,box-shadow] duration-200',
            'hover:border-wgreen/40',
            'focus-visible:border-wgreen focus-visible:outline-none',
            'disabled:opacity-40 disabled:pointer-events-none disabled:cursor-not-allowed',
            Icon && 'pl-10',
            Suffix && 'pr-10',
            hasError && [
              'border-red-400',
              'focus-visible:border-red-400',
              'focus-visible:[box-shadow:0_0_0_3px_rgba(239,68,68,0.18)]',
            ],
            className,
          )}
          {...props}
        />
        {Suffix && (
          <Suffix
            className="pointer-events-none absolute right-3.5 top-1/2 size-4 -translate-y-1/2 text-wmuted"
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
          hasError ? 'text-red-600' : 'text-wmuted',
        )}
      >
        {error || helper || ''}
      </p>
    </div>
  );
});
