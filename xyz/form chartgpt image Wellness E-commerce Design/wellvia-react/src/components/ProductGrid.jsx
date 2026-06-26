import ProductCard from './ProductCard';

/**
 * ProductGrid — responsive 2 / 3 / 4-up grid of ProductCards.
 */
export default function ProductGrid({ products, showCategory = false, cols = 4 }) {
  const colClass =
    cols === 4
      ? 'grid-cols-2 md:grid-cols-3 lg:grid-cols-4'
      : cols === 3
      ? 'grid-cols-2 md:grid-cols-3'
      : 'grid-cols-1 sm:grid-cols-2';

  return (
    <div className={`grid ${colClass} gap-3.5 lg:gap-[22px]`}>
      {products.map((p) => (
        <ProductCard key={p.id} product={p} showCategory={showCategory} />
      ))}
    </div>
  );
}
