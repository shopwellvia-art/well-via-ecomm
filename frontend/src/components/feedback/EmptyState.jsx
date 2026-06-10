import { cn } from '@/lib/utils.js';

/**
 * Empty / error display: icon + heading + optional description + a clear next action.
 * Never leave a blank region — always render this instead.
 *
 * Props:
 *   icon         — Lucide icon component (rendered at 24px inside a tinted circle)
 *   iconTone     — color tone of the icon container: 'neutral' | 'accent' | 'success'
 *                  | 'warning' | 'danger' (default: 'neutral')
 *   title        — required: short headline (h3)
 *   description  — optional: one or two sentences of context (p)
 *   action       — optional: CTA node, typically a <Button> or <Link> + <Button>
 *   size         — 'default' | 'sm' (compact, less vertical padding; for inline states)
 *   bordered     — renders a border + bg card shell (default true)
 *
 * @example — empty cart
 *   <EmptyState
 *     icon={ShoppingBag}
 *     title="Your cart is empty"
 *     description="Browse the collection and add something you love."
 *     action={<Link to="/products"><Button size="sm">Browse products</Button></Link>}
 *   />
 *
 * @example — inline / compact (no card shell)
 *   <EmptyState icon={PackageX} title="No results" size="sm" bordered={false} />
 */

const ICON_TONE_CLASSES = {
  neutral: 'bg-fill text-ink-secondary',
  accent:  'bg-accent/12 text-accent',
  success: 'bg-success/12 text-success',
  warning: 'bg-warning/12 text-warning',
  danger:  'bg-danger/12 text-danger',
};

export function EmptyState({
  icon: Icon,
  iconTone = 'neutral',
  title,
  description,
  action,
  size = 'default',
  bordered = true,
  className,
}) {
  const isCompact = size === 'sm';

  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center text-center',
        isCompact ? 'gap-2 px-4 py-8' : 'gap-3 px-6 py-16',
        bordered && 'rounded-lg border border-line-subtle bg-bg-elevated',
        className,
      )}
    >
      {Icon && (
        <span
          className={cn(
            'grid shrink-0 place-items-center rounded-full',
            ICON_TONE_CLASSES[iconTone] ?? ICON_TONE_CLASSES.neutral,
            isCompact ? 'size-10' : 'size-12',
          )}
        >
          <Icon
            className={cn(isCompact ? 'size-5' : 'size-6')}
            aria-hidden="true"
          />
        </span>
      )}

      <h3
        className={cn(
          'text-ink-primary font-semibold',
          isCompact ? 'text-sm' : 'text-h3',
        )}
      >
        {title}
      </h3>

      {description && (
        <p
          className={cn(
            'text-ink-secondary text-balance',
            isCompact ? 'text-xs max-w-xs' : 'text-sm max-w-sm',
          )}
        >
          {description}
        </p>
      )}

      {action && <div className={cn(isCompact ? 'mt-1' : 'mt-2')}>{action}</div>}
    </div>
  );
}
