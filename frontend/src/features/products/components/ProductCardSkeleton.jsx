import { Skeleton } from '@/components/storefront/ui/Skeleton.jsx';

/**
 * Skeleton that mirrors ProductCard's layout dimensions exactly —
 * square image area + info strip with name stubs, price, and rating bar.
 * Wellness wcard surface with wline border; rounded-xl2 to match cards.
 */
export function ProductCardSkeleton() {
  return (
    <div className="overflow-hidden rounded-xl2 border border-wline bg-wcard shadow-sm">
      {/* Square image area */}
      <Skeleton className="aspect-square rounded-none" />

      {/* Info strip */}
      <div className="flex flex-col gap-2 p-3">
        {/* Product name — two lines */}
        <div className="flex flex-col gap-1.5">
          <Skeleton variant="text" className="h-3 w-full" />
          <Skeleton variant="text" className="h-3 w-4/5" />
        </div>
        {/* Rating stub */}
        <Skeleton variant="text" className="h-2.5 w-20" />
        {/* Price */}
        <Skeleton variant="text" className="h-5 w-24" />
        {/* Add to cart button stub */}
        <Skeleton className="mt-1 h-8 w-full rounded-full" />
      </div>
    </div>
  );
}
