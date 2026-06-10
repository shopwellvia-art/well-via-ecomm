import { motion } from 'framer-motion';
import { listStagger, fadeUp } from '@/lib/motion.js';
import { ProductCard } from './ProductCard.jsx';
import { ProductCardSkeleton } from './ProductCardSkeleton.jsx';

const GRID = 'grid grid-cols-2 gap-4 sm:gap-5 md:grid-cols-3 lg:grid-cols-4';

/** Responsive product grid with staggered reveal and a matching skeleton state. */
export function ProductGrid({ products = [], loading = false, onQuickAdd, skeletonCount = 8 }) {
  if (loading) {
    return (
      <div className={GRID}>
        {Array.from({ length: skeletonCount }).map((_, i) => (
          <ProductCardSkeleton key={i} />
        ))}
      </div>
    );
  }

  return (
    <motion.div
      className={GRID}
      variants={listStagger(0.05)}
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
