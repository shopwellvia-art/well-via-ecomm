import { Skeleton } from '@/components/ui/Skeleton.jsx';

/**
 * Skeleton that mirrors ProductCard's layout dimensions exactly —
 * 4:5 image area + info strip with category stub, name, and price.
 */
export function ProductCardSkeleton() {
  return (
    <div className="overflow-hidden rounded-md border border-line-subtle bg-bg-elevated shadow-sm">
      {/* Image area */}
      <Skeleton className="aspect-[4/5] rounded-none" />

      {/* Info strip */}
      <div className="flex flex-col gap-2.5 p-4">
        {/* SKU / category label */}
        <Skeleton variant="text" className="h-2.5 w-14" />
        {/* Product name — two lines */}
        <div className="flex flex-col gap-1.5">
          <Skeleton variant="text" className="h-3.5 w-full" />
          <Skeleton variant="text" className="h-3.5 w-3/5" />
        </div>
        {/* Price */}
        <Skeleton variant="text" className="mt-0.5 h-5 w-20" />
      </div>
    </div>
  );
}
