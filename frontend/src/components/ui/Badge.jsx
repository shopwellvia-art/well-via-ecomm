import { cva } from 'class-variance-authority';
import { cn } from '@/lib/utils.js';

/**
 * Status / label badge.
 *
 * Tones:   neutral | accent | success | warning | danger | info
 * Sizes:   sm (default) | md
 * dot      — prepends a colored dot indicator (useful for live/status badges)
 * outline  — border-only treatment instead of filled background
 *
 * @example
 *   <Badge tone="success">Active</Badge>
 *   <Badge tone="warning" dot>Low stock</Badge>
 *   <Badge tone="accent" outline>New</Badge>
 *   <Badge tone="info" size="md">Processing</Badge>
 */
const badgeVariants = cva(
  'inline-flex items-center gap-1.5 rounded-full font-medium',
  {
    variants: {
      tone: {
        neutral: 'bg-fill-strong text-ink-secondary',
        accent:  'bg-accent/12 text-accent',
        success: 'bg-success/12 text-success',
        warning: 'bg-warning/12 text-warning',
        danger:  'bg-danger/12 text-danger',
        info:    'bg-info/12 text-info',
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
      { tone: 'neutral', outline: true, class: 'ring-line-strong text-ink-secondary' },
      { tone: 'accent',  outline: true, class: 'ring-accent/50 text-accent' },
      { tone: 'success', outline: true, class: 'ring-success/50 text-success' },
      { tone: 'warning', outline: true, class: 'ring-warning/50 text-warning' },
      { tone: 'danger',  outline: true, class: 'ring-danger/50 text-danger' },
      { tone: 'info',    outline: true, class: 'ring-info/50 text-info' },
    ],
    defaultVariants: { tone: 'neutral', size: 'sm' },
  },
);

/** Maps a tone to a dot fill color class. */
const DOT_COLOR = {
  neutral: 'bg-ink-tertiary',
  accent:  'bg-accent',
  success: 'bg-success',
  warning: 'bg-warning',
  danger:  'bg-danger',
  info:    'bg-info',
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
