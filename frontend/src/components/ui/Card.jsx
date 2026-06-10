import { cn } from '@/lib/utils.js';

/**
 * Elevated surface card.
 *
 * Props:
 *   glass        — translucent glass surface (for over-media / hero contexts)
 *   interactive  — adds hover-lift + cursor-pointer; use for clickable cards
 *   flat         — removes shadow; for tightly-nested cards inside surfaces
 *   padding      — removes the default padding (for full-bleed media inside a card)
 *
 * Compose with CardBody for standard padding.
 *
 * @example — product card
 *   <Card interactive className="rounded-lg" onClick={…}>
 *     <img … />
 *     <CardBody>…</CardBody>
 *   </Card>
 *
 * @example — glass surface
 *   <Card glass className="p-6">…</Card>
 */
export function Card({
  className,
  glass = false,
  interactive = false,
  flat = false,
  children,
  ...props
}) {
  return (
    <div
      className={cn(
        'rounded-lg',
        // Base surface
        glass
          ? 'glass'
          : [
              'border border-line-subtle bg-bg-elevated',
              flat ? 'shadow-none' : 'shadow-md',
            ],
        // Interactive variant — lift on hover
        interactive && 'card-interactive',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

export function CardBody({ className, children, ...props }) {
  return (
    <div className={cn('p-5', className)} {...props}>
      {children}
    </div>
  );
}

/**
 * CardHeader — consistent top section with title + optional action slot.
 * Use inside a Card when you need a bordered header row.
 */
export function CardHeader({ title, action, className, children, ...props }) {
  return (
    <div
      className={cn(
        'flex items-center justify-between border-b border-line-subtle px-5 py-4',
        className,
      )}
      {...props}
    >
      {title && (
        <p className="text-sm font-medium text-ink-primary">{title}</p>
      )}
      {children}
      {action && <div className="ml-auto shrink-0">{action}</div>}
    </div>
  );
}
