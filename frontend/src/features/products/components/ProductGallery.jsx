import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { cn } from '@/lib/utils.js';
import { fadeIn } from '@/lib/motion.js';
import { ProductMedia } from './ProductMedia.jsx';

/**
 * Product detail image gallery — large main image with a smooth cross-fade
 * transition, plus a thumbnail strip below. Falls back to the single
 * ProductMedia (gradient or image_url) when the product has no uploaded images.
 */
export function ProductGallery({ product }) {
  const images = [...(product.images || [])].sort(
    (a, b) => Number(b.is_primary) - Number(a.is_primary) || a.position - b.position,
  );
  const [active, setActive] = useState(0);

  const baseCard =
    'overflow-hidden rounded-lg border border-line-subtle bg-bg-elevated shadow-md';

  if (images.length === 0) {
    return (
      <div className={baseCard}>
        <div className="aspect-[4/5]">
          <ProductMedia product={product} eager />
        </div>
      </div>
    );
  }

  const current = images[Math.min(active, images.length - 1)];

  return (
    <div className="flex flex-col gap-3">
      {/* Main image with cross-fade */}
      <div className={cn(baseCard, 'relative aspect-[4/5]')}>
        <AnimatePresence mode="wait" initial={false}>
          <motion.img
            key={current.id}
            src={current.url}
            alt={product.name}
            fetchpriority="high"
            variants={fadeIn}
            initial="hidden"
            animate="show"
            exit="hidden"
            className="absolute inset-0 size-full object-cover"
          />
        </AnimatePresence>

        {/* Subtle vignette */}
        <div
          className="pointer-events-none absolute inset-0 rounded-lg shadow-[inset_0_0_40px_rgba(0,0,0,0.08)]"
          aria-hidden="true"
        />
      </div>

      {images.length > 1 && (
        <div className="grid grid-cols-5 gap-2" role="group" aria-label="Product image thumbnails">
          {images.map((img, i) => (
            <motion.button
              key={img.id}
              type="button"
              whileTap={{ scale: 0.94 }}
              onClick={() => setActive(i)}
              aria-label={`View image ${i + 1}`}
              aria-current={i === active}
              className={cn(
                'aspect-square overflow-hidden rounded-sm border-2 transition-all duration-150 focus-visible:focus-ring',
                i === active
                  ? 'border-accent shadow-glow-sm'
                  : 'border-line-subtle opacity-65 hover:opacity-100 hover:border-line-strong',
              )}
            >
              <img src={img.url} alt="" loading="lazy" className="size-full object-cover" />
            </motion.button>
          ))}
        </div>
      )}
    </div>
  );
}
