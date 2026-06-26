import { useEffect, useState } from 'react';
import { cn } from '@/lib/utils.js';

/**
 * Product image with a graceful gradient fallback. Renders the placeholder
 * both when there's no image_url AND when the image fails to load (404, dead
 * URL) — so a broken image never leaks the browser's broken-glyph + alt text
 * over the card. Always fills a fixed-aspect box so CLS stays 0.
 *
 * Fallback uses warm wellness tones: wpaper / wcanvas / wgold.
 */
export function ProductMedia({ product, className, eager = false }) {
  const initial = (product?.name || '?').trim().charAt(0).toUpperCase();
  const [failed, setFailed] = useState(false);

  // Reset the error state if the image source changes (e.g. list reuse).
  useEffect(() => {
    setFailed(false);
  }, [product?.image_url]);

  const showImage = product?.image_url && !failed;

  if (showImage) {
    return (
      <img
        src={product.image_url}
        alt={product.name}
        loading={eager ? 'eager' : 'lazy'}
        fetchpriority={eager ? 'high' : undefined}
        decoding="async"
        onError={() => setFailed(true)}
        className={cn('size-full object-cover', className)}
      />
    );
  }

  return (
    <div
      aria-hidden="true"
      className={cn(
        'grid size-full place-items-center bg-gradient-to-br from-wgold/20 via-wpaper to-wcanvas',
        className,
      )}
    >
      <span className="text-5xl font-semibold text-wink/30">{initial}</span>
    </div>
  );
}
