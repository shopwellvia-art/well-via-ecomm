import { cn } from '@/lib/utils.js';

/**
 * Wellness-twin of @/components/ui/Skeleton — identical public API, wellness palette.
 *
 * Loading placeholder. Match the final layout's dimensions to keep CLS = 0.
 * Shimmer uses bg-wpaper (warm paper) as the base instead of Flipkart's bg-fill.
 *
 * Props mirror @/components/ui/Skeleton exactly:
 *   variant  — 'default' | 'text' | 'circle'
 *   lines    — (text variant) number of lines to stack (default 1)
 *   className, plus any div props.
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
              // Last line shorter — mimics natural text wrap
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

/** Internal base element — wellness shimmer on warm wpaper base. */
function SkeletonBase({ className, ...props }) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        'relative overflow-hidden rounded-xl bg-wpaper',
        // Shimmer wave — uses the keyframe defined in tailwind.config.js
        'after:absolute after:inset-0 after:-translate-x-full after:animate-shimmer',
        'after:bg-gradient-to-r after:from-transparent after:via-wgreen/[0.04] after:to-transparent',
        className,
      )}
      {...props}
    />
  );
}
