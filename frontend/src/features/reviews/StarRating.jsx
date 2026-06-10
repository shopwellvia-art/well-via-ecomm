import { motion, useReducedMotion } from 'framer-motion';
import { Star, StarHalf } from 'lucide-react';
import { cn } from '@/lib/utils.js';

const SIZES = {
  xs: 'size-3',
  sm: 'size-3.5',
  md: 'size-4',
  lg: 'size-5',
  xl: 'size-6',
};

/**
 * Read-only stars when no onChange is provided. Pass onChange to make it
 * interactive (used by the "Write a review" form).
 *
 * `value` may be a fractional number (e.g. 4.3) — we render filled/half/empty
 * stars accordingly. Interactive mode snaps to integers.
 */
export function StarRating({
  value = 0,
  onChange,
  size = 'md',
  className,
  ariaLabel,
}) {
  const reduce = useReducedMotion();
  const v = Math.max(0, Math.min(5, Number(value) || 0));
  const sizeClass = SIZES[size] || SIZES.md;
  const interactive = typeof onChange === 'function';

  const label = ariaLabel || `${v.toFixed(1)} out of 5 stars`;

  if (interactive) {
    return (
      <div
        role="radiogroup"
        aria-label="Star rating"
        className={cn('inline-flex items-center gap-1', className)}
      >
        {[1, 2, 3, 4, 5].map((i) => {
          const active = i <= Math.round(v);
          return (
            <motion.button
              key={i}
              type="button"
              role="radio"
              aria-checked={Math.round(v) === i}
              aria-label={`${i} star${i === 1 ? '' : 's'}`}
              onClick={() => onChange(i)}
              whileHover={reduce ? undefined : { scale: 1.2 }}
              whileTap={reduce ? undefined : { scale: 0.9 }}
              className={cn(
                'rounded-sm p-0.5 transition-colors focus-visible:focus-ring',
                active ? 'text-warning' : 'text-ink-tertiary hover:text-warning/70',
              )}
            >
              <Star className={cn(sizeClass, active && 'fill-current')} aria-hidden="true" />
            </motion.button>
          );
        })}
      </div>
    );
  }

  // Read-only mode — supports half stars.
  return (
    <span
      role="img"
      aria-label={label}
      className={cn('inline-flex items-center gap-0.5 text-warning', className)}
    >
      {[1, 2, 3, 4, 5].map((i) => {
        const full = v >= i;
        const half = !full && v >= i - 0.5;
        if (full) {
          return (
            <Star key={i} className={cn(sizeClass, 'fill-current')} aria-hidden="true" />
          );
        }
        if (half) {
          return (
            <StarHalf
              key={i}
              className={cn(sizeClass, 'fill-current')}
              aria-hidden="true"
            />
          );
        }
        return (
          <Star
            key={i}
            className={cn(sizeClass, 'text-ink-tertiary/40')}
            aria-hidden="true"
          />
        );
      })}
    </span>
  );
}
