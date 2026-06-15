import { motion } from 'framer-motion';
import { listStagger, fadeUp } from '@/lib/motion.js';
import { ProductCard } from './ProductCard.jsx';
import { ProductCardSkeleton } from './ProductCardSkeleton.jsx';

// Two density variants. The list page (with its filter sidebar) tops out at 4
// columns; the full-bleed home "Recommended" grid goes to 5.
const GRID_COLS = {
  4: 'grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4 lg:grid-cols-4',
  5: 'grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4 lg:grid-cols-5',
};

/** Responsive product grid with staggered reveal and a matching skeleton state. */
export function ProductGrid({ products = [], loading = false, onQuickAdd, skeletonCount = 8, columns = 4 }) {
  const GRID = GRID_COLS[columns] ?? GRID_COLS[4];
  if (loading) {
    return (
      <div className={GRID}>
        {Array.from({ length: skeletonCount }).map((_, i) => (
          <ProductCardSkeleton key={i} />
        ))}
      </div>
    );
  }

  const stagger = Math.min(0.05, 0.3 / Math.max(products.length, 1));

  return (
    <motion.div
      className={GRID}
      variants={listStagger(stagger)}
      initial="hidden"
      whileInView="show"
      viewport={{ once: true, amount: 0.08 }}
    >
      {products.map((p) => (
        <motion.div key={p.id} variants={fadeUp}>
          <ProductCard product={p} onQuickAdd={onQuickAdd} />
        </motion.div>
      ))}
    </motion.div>
  );
}
