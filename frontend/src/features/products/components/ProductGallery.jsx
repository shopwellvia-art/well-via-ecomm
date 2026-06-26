import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { cn } from '@/lib/utils.js';
import { fadeIn } from '@/lib/motion.js';
import { ProductMedia } from './ProductMedia.jsx';

/**
 * Product detail image gallery — wellness palette. Large main image
 * with cross-fade, thumbnail strip below. Falls back to single ProductMedia
 * when no uploaded images exist.
 */
export function ProductGallery({ product }) {
  const images = [...(product.images || [])].sort(
    (a, b) => Number(b.is_primary) - Number(a.is_primary) || a.position - b.position,
  );
  const [active, setActive] = useState(0);

  if (images.length === 0) {
    return (
      <div className="aspect-square overflow-hidden rounded-xl2 border border-wline bg-wcard">
        <ProductMedia product={product} eager />
      </div>
    );
  }

  const current = images[Math.min(active, images.length - 1)];

  return (
    <div className="flex flex-col gap-2.5">
      {/* Main image */}
      <div className="relative aspect-square overflow-hidden rounded-xl2 border border-wline bg-wcard shadow-sm">
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
      </div>

      {images.length > 1 && (
        <div
          className="grid grid-cols-6 gap-1.5"
          role="group"
          aria-label="Product image thumbnails"
        >
          {images.map((img, i) => (
            <button
              key={img.id}
              type="button"
              onClick={() => setActive(i)}
              aria-label={`View image ${i + 1}`}
              aria-current={i === active}
              className={cn(
                'aspect-square overflow-hidden rounded-sm border-2 transition-all duration-150',
                i === active
                  ? 'border-wgreen'
                  : 'border-wline opacity-60 hover:border-wline hover:opacity-100',
              )}
            >
              <img src={img.url} alt="" loading="lazy" className="size-full object-cover" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
