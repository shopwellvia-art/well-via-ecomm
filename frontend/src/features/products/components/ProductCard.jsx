import { Link } from 'react-router-dom';
import { ShoppingCart, Star } from 'lucide-react';
import { cn, formatPrice, stockLabel } from '@/lib/utils.js';
import { ProductMedia } from './ProductMedia.jsx';
import { WishlistButton } from '@/features/wishlist/WishlistButton.jsx';

/**
 * Flipkart-style product tile: a soft white card that lifts on hover, with the
 * product image floating on white, a brand eyebrow, a green rating pill, a
 * price line (dark price · struck MRP · green "% off") and an amber
 * "ADD TO CART" bar. A discount ribbon rides the top-left edge.
 */
export function ProductCard({ product, onQuickAdd }) {
  const outOfStock = product.stock <= 0;
  const stock = stockLabel(product.stock);

  // Defensive: server enforces compare_at_price > price, but coerce to numbers
  // so a string payload (Decimal serializes as string) is still compared right.
  const priceNum = Number(product.price);
  const compareNum = Number(product.compare_at_price);
  const onSale =
    product.compare_at_price != null &&
    Number.isFinite(compareNum) &&
    compareNum > priceNum;

  const discountPct =
    onSale && compareNum > 0
      ? Math.round(((compareNum - priceNum) / compareNum) * 100)
      : 0;

  // Aggregate rating — only render the pill when the catalog actually ships one.
  const ratingNum = Number(product.rating);
  const hasRating = Number.isFinite(ratingNum) && ratingNum > 0;
  const ratingCount = Number(product.rating_count);

  function handleQuickAdd(e) {
    e.preventDefault();
    e.stopPropagation();
    if (!outOfStock) onQuickAdd?.(product);
  }

  return (
    <div className="group hover-lift relative flex h-full flex-col overflow-hidden rounded-lg border border-line-subtle bg-bg-elevated">
      {/* Discount ribbon — top-left edge */}
      {onSale && discountPct > 0 && (
        <span className="absolute left-0 top-3 z-10 rounded-r-md bg-rating px-2 py-0.5 text-[11px] font-bold text-white">
          {discountPct}% OFF
        </span>
      )}

      {/* Wishlist heart — top-right. Click is captured so it does not trigger
          the surrounding link navigation. */}
      <div className="absolute right-2.5 top-2.5 z-10">
        <WishlistButton productId={product.id} variant="overlay" size="sm" />
      </div>

      {/* Media — product floats on white (contain, not cover) */}
      <Link
        to={`/products/${product.id}`}
        className="block aspect-square overflow-hidden bg-white p-3 focus-visible:focus-ring sm:p-4"
        tabIndex={-1}
        aria-hidden="true"
      >
        <ProductMedia
          product={product}
          className={cn('object-contain', outOfStock && 'opacity-50 saturate-[0.4]')}
        />
      </Link>

      {/* Info */}
      <div className="flex flex-1 flex-col p-3">
        {product.brand && (
          <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-tertiary">
            {product.brand}
          </p>
        )}

        <Link
          to={`/products/${product.id}`}
          className="mt-0.5 line-clamp-2 min-h-[2.5rem] text-sm text-ink-primary transition-colors hover:text-accent focus-visible:focus-ring"
        >
          {product.name}
        </Link>

        {/* Rating row — green pill + count */}
        {hasRating && (
          <div className="mt-1.5 flex items-center gap-2">
            <span className="rating-pill">
              {ratingNum.toFixed(1)}
              <Star className="size-[9px] fill-current" aria-hidden="true" />
            </span>
            {Number.isFinite(ratingCount) && ratingCount > 0 && (
              <span className="text-xs text-ink-tertiary">
                ({ratingCount.toLocaleString('en-IN')})
              </span>
            )}
          </div>
        )}

        {/* Price row — dark price, struck compare, green % off */}
        <div className="mt-2 flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="text-lg font-bold tabular-nums text-ink-primary">
            {formatPrice(product.price)}
          </span>
          {onSale && (
            <>
              <s
                className="text-sm tabular-nums text-ink-tertiary"
                aria-label={`Was ${formatPrice(product.compare_at_price)}`}
              >
                {formatPrice(product.compare_at_price)}
              </s>
              {discountPct > 0 && (
                <span className="text-sm font-semibold text-rating">{discountPct}% off</span>
              )}
            </>
          )}
        </div>

        {/* Low-stock / out-of-stock hint */}
        {stock.tone !== 'success' && (
          <p
            className={cn(
              'mt-1 text-xs font-medium',
              stock.tone === 'danger' ? 'text-danger' : 'text-warning',
            )}
          >
            {stock.text}
          </p>
        )}

        {/* Add to Cart — amber, matches the Flipkart reference */}
        <button
          type="button"
          onClick={handleQuickAdd}
          disabled={outOfStock}
          aria-label={`Add ${product.name} to cart`}
          className={cn(
            'mt-3 flex h-9 items-center justify-center gap-1.5 rounded-lg',
            'bg-cart text-xs font-semibold uppercase tracking-wide text-white',
            'transition-all hover:bg-cart-hover active:scale-[0.98]',
            'focus-visible:focus-ring',
            'disabled:pointer-events-none disabled:opacity-50',
          )}
        >
          <ShoppingCart className="size-4" aria-hidden="true" />
          {outOfStock ? 'Unavailable' : 'Add to Cart'}
        </button>
      </div>
    </div>
  );
}
