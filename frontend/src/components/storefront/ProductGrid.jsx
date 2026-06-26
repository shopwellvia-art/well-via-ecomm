import ProductCard from '@/components/storefront/ProductCard';

/**
 * ProductGrid — responsive 2 / 3 / 4-up grid of ProductCards.
 *
 * Props:
 *   products  — array of real product objects (from API / hooks)
 *   cols      — target column count: 2 | 3 | 4 (default 4)
 *
 * Handles an empty products array with a subtle placeholder message.
 */
export default function ProductGrid({ products, cols = 4 }) {
  if (!products || products.length === 0) {
    return (
      <div className="py-16 text-center text-wmuted text-[14px]">
        No products found.
      </div>
    );
  }

  const colClass =
    cols === 4
      ? 'grid-cols-2 md:grid-cols-3 lg:grid-cols-4'
      : cols === 3
      ? 'grid-cols-2 md:grid-cols-3'
      : 'grid-cols-1 sm:grid-cols-2';

  return (
    <div className={`grid ${colClass} gap-3.5 lg:gap-[22px]`}>
      {products.map((p) => (
        <ProductCard key={p.id} product={p} />
      ))}
    </div>
  );
}
