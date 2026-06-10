import { cn } from '@/lib/utils.js';

/**
 * Loading placeholder. Match the final layout's dimensions to keep CLS = 0.
 * Shimmer is a 1.6s ease-in-out loop (the only permitted infinite animation).
 *
 * Props:
 *   variant  — 'default' (block) | 'text' (narrower, rounded-full, auto-heights)
 *              | 'circle' (aspect-square, rounded-full)
 *   lines    — when variant="text", how many lines to stack (default 1)
 *
 * @example — card placeholder
 *   <Skeleton className="h-48 w-full rounded-lg" />
 *
 * @example — text block
 *   <Skeleton variant="text" lines={3} />
 *
 * @example — avatar
 *   <Skeleton variant="circle" className="size-10" />
 */
export function Skeleton({ className, variant = 'default', lines = 1, ...props }) {
  if (variant === 'text') {
    return (
      <div className="flex flex-col gap-2" aria-hidden="true">
        {Array.from({ length: lines }).map((_, i) => (
          <SkeletonBase
            key={i}
            className={cn(
              'h-4 rounded-full',
              // Last line is shorter — mimics natural text wrap
              i === lines - 1 && lines > 1 ? 'w-3/4' : 'w-full',
              className,
            )}
          />
        ))}
      </div>
    );
  }

  if (variant === 'circle') {
    return (
      <SkeletonBase
        className={cn('rounded-full aspect-square', className)}
        {...props}
      />
    );
  }

  return <SkeletonBase className={className} {...props} />;
}

/** Internal base element — shimmer via a ::after pseudo in global.css. */
function SkeletonBase({ className, ...props }) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        'relative overflow-hidden rounded-sm bg-fill',
        // Shimmer wave — uses the keyframe defined in tailwind.config.js
        'after:absolute after:inset-0 after:-translate-x-full after:animate-shimmer',
        'after:bg-gradient-to-r after:from-transparent after:via-white/[0.06] after:to-transparent',
        className,
      )}
      {...props}
    />
  );
}
