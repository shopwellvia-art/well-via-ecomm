import { cn } from '@/lib/utils.js';

/**
 * Wellness-twin of @/components/ui/Card — identical public API, wellness palette.
 *
 * Elevated surface card. Uses wcard/wline wellness tokens in place of
 * the Flipkart bg-bg-elevated/border-line-subtle tokens.
 *
 * Props mirror @/components/ui/Card exactly:
 *   glass, interactive, flat, children, className, plus any div props.
 *
 * Sub-exports: CardBody, CardHeader — identical signatures.
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
        'rounded-xl2',
        glass
          ? 'bg-wcard/70 backdrop-blur-md border border-wline/60'
          : [
              'border border-wline bg-wcard',
              flat ? 'shadow-none' : 'shadow-sm',
            ],
        interactive && 'cursor-pointer transition-shadow hover:shadow-md',
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
 * Bordered header row; wellness wline border.
 */
export function CardHeader({ title, action, className, children, ...props }) {
  return (
    <div
      className={cn(
        'flex items-center justify-between border-b border-wline px-5 py-4',
        className,
      )}
      {...props}
    >
      {title && (
        <p className="text-sm font-medium text-wink">{title}</p>
      )}
      {children}
      {action && <div className="ml-auto shrink-0">{action}</div>}
    </div>
  );
}
