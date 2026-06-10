import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { cn } from '@/lib/utils.js';
import { fadeIn } from '@/lib/motion.js';
import { ProductMedia } from './ProductMedia.jsx';

/**
 * Premium product image gallery — vertical thumbnail strip on the left,
 * large animated main image on the right. Thumbnail hover/click updates the
 * main view with a smooth cross-fade. On mobile: thumbs collapse to a
 * horizontal row beneath the main image.
 */
export function GalleryAmz({ product }) {
  const sorted = [...(product.images || [])].sort(
    (a, b) => Number(b.is_primary) - Number(a.is_primary) || a.position - b.position,
  );
  const [active, setActive] = useState(0);

  // No images at all — fall back to the gradient placeholder.
  if (sorted.length === 0) {
    return (
      <div className="flex flex-col gap-3 lg:flex-row">
        <div className="hidden lg:block lg:w-14 lg:shrink-0" />
        <div className="aspect-square w-full overflow-hidden rounded-md border border-line-subtle bg-bg-elevated">
          <ProductMedia product={product} eager />
        </div>
      </div>
    );
  }

  const current = sorted[Math.min(active, sorted.length - 1)];

  return (
    <div className="flex flex-col-reverse gap-3 lg:flex-row">
      {/* Thumbnail strip */}
      <div
        className="flex shrink-0 gap-2 overflow-x-auto lg:w-14 lg:flex-col lg:overflow-visible"
        aria-label="Product images"
      >
        {sorted.map((img, i) => (
          <motion.button
            key={img.id}
            type="button"
            whileTap={{ scale: 0.95 }}
            onMouseEnter={() => setActive(i)}
            onFocus={() => setActive(i)}
            onClick={() => setActive(i)}
            aria-label={`View image ${i + 1}`}
            aria-current={i === active}
            className={cn(
              'size-14 shrink-0 overflow-hidden rounded-sm border-2 transition-all duration-150 focus-visible:focus-ring',
              i === active
                ? 'border-accent shadow-glow-sm'
                : 'border-line-subtle hover:border-line-strong opacity-70 hover:opacity-100',
            )}
          >
            <img src={img.url} alt="" loading="lazy" className="size-full object-cover" />
          </motion.button>
        ))}
      </div>

      {/* Main image — cross-fades between selections */}
      <div className="flex-1">
        <div className="group relative aspect-square w-full overflow-hidden rounded-md border border-line-subtle bg-bg-elevated shadow-md">
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
              className="absolute inset-0 size-full object-cover transition-transform duration-500 group-hover:scale-[1.03]"
            />
          </AnimatePresence>

          {/* Subtle gradient at bottom for depth */}
          <div
            className="pointer-events-none absolute inset-x-0 bottom-0 h-24 bg-gradient-to-t from-bg-base/30 to-transparent"
            aria-hidden="true"
          />
        </div>

        {sorted.length > 1 && (
          <p className="mt-2 text-center text-xs text-ink-tertiary">
            {active + 1} / {sorted.length} — hover a thumbnail to preview
          </p>
        )}
      </div>
    </div>
  );
}
