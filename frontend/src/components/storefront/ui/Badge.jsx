import { cva } from 'class-variance-authority';
import { cn } from '@/lib/utils.js';

/**
 * Wellness-twin of @/components/ui/Badge — identical public API, wellness palette.
 *
 * Status / label badge — soft-tinted pill style (wellness aesthetic).
 *
 * Tones match the original exactly:
 *   neutral | accent | success | warning | danger | info | rating
 *
 * Token mapping from Flipkart → wellness:
 *   accent  → wgreen (brand primary)
 *   success → wgreen (same green family)
 *   warning → wgold  (warm gold accent)
 *   danger  → red-600 / red-50 (no wellness danger token)
 *   info    → wgreen (mapped to brand; no separate info token)
 *   rating  → solid wgreen pill (replaces Flipkart rating green)
 *   neutral → wpaper bg / wmuted text
 *
 * Props: tone, size, outline, dot, children, className, plus any span props.
 * `badgeVariants` exported for external cva composition.
 */
const badgeVariants = cva(
  'inline-flex items-center gap-1.5 rounded-full font-medium',
  {
    variants: {
      tone: {
        neutral: 'bg-wpaper text-wmuted',
        accent:  'bg-wgreen/10 text-wgreen',
        success: 'bg-wgreen/10 text-wgreen',
        warning: 'bg-wgold/10 text-wgold',
        danger:  'bg-red-50 text-red-600',
        info:    'bg-wgreen/10 text-wgreen',
        rating:  'bg-wgreen text-white font-semibold',
      },
      size: {
        sm: 'px-2.5 py-1 text-xs',
        md: 'px-3 py-1.5 text-xs',
      },
      outline: {
        true: 'bg-transparent ring-1 ring-inset',
      },
    },
    compoundVariants: [
      { tone: 'neutral', outline: true, class: 'ring-wline text-wmuted' },
      { tone: 'accent',  outline: true, class: 'ring-wgreen/40 text-wgreen' },
      { tone: 'success', outline: true, class: 'ring-wgreen/40 text-wgreen' },
      { tone: 'warning', outline: true, class: 'ring-wgold/40 text-wgold' },
      { tone: 'danger',  outline: true, class: 'ring-red-300 text-red-600' },
      { tone: 'info',    outline: true, class: 'ring-wgreen/40 text-wgreen' },
    ],
    defaultVariants: { tone: 'neutral', size: 'sm' },
  },
);

/** Maps a tone to a dot fill color class. */
const DOT_COLOR = {
  neutral: 'bg-wmuted',
  accent:  'bg-wgreen',
  success: 'bg-wgreen',
  warning: 'bg-wgold',
  danger:  'bg-red-600',
  info:    'bg-wgreen',
  rating:  'bg-white',
};

export function Badge({
  className,
  tone = 'neutral',
  size,
  outline,
  dot = false,
  children,
  ...props
}) {
  return (
    <span
      className={cn(badgeVariants({ tone, size, outline }), className)}
      {...props}
    >
      {dot && (
        <span
          className={cn('inline-block size-1.5 shrink-0 rounded-full', DOT_COLOR[tone])}
          aria-hidden="true"
        />
      )}
      {children}
    </span>
  );
}

export { badgeVariants };
