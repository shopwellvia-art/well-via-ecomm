import { Link } from 'react-router-dom';
import { ShoppingCart, Star } from 'lucide-react';
import { cn, formatPrice, stockLabel } from '@/lib/utils.js';
import { Badge } from '@/components/ui/Badge.jsx';
import { ProductMedia } from './ProductMedia.jsx';
import { WishlistButton } from '@/features/wishlist/WishlistButton.jsx';

/**
 * Flipkart-style product tile: a flat white card with the product image
 * floating on white, a green rating pill, and a price line with the original
 * price struck through plus a green "% off". A quick-add bar fades in on hover.
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
    <div
      className={cn(
        'group flex h-full flex-col overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated',
        'transition-shadow duration-200 hover:shadow-md',
      )}
    >
      <Link
        to={`/products/${product.id}`}
        className="flex flex-1 flex-col rounded-sm focus-visible:focus-ring"
      >
        {/* Media — product floats on white with padding (contain, not cover) */}
        <div className="relative aspect-square bg-white p-3 sm:p-4">
          <ProductMedia
            product={product}
            className={cn('object-contain', outOfStock && 'opacity-50 saturate-[0.4]')}
          />

          {/* Top-left tag — discount % */}
          {onSale && discountPct > 0 && (
            <span className="absolute left-2 top-2 rounded-xs bg-rating px-1.5 py-0.5 text-[11px] font-bold text-white">
              {discountPct}% OFF
            </span>
          )}

          {/* Wishlist heart — top-right. Click is captured so it does not
              trigger the surrounding link's navigation. */}
          <div className="absolute right-2 top-2">
            <WishlistButton productId={product.id} variant="overlay" size="sm" />
          </div>
        </div>

        {/* Info */}
        <div className="flex flex-1 flex-col gap-1.5 border-t border-line-subtle p-3">
          <h3 className="line-clamp-2 min-h-[2.5rem] text-sm text-ink-primary transition-colors group-hover:text-accent">
            {product.name}
          </h3>

          {/* Rating row — green pill + count */}
          {hasRating && (
            <div className="flex items-center gap-1.5">
              <Badge tone="rating" className="px-1.5 py-0.5 text-[11px]">
                {ratingNum.toFixed(1)}
                <Star className="size-3 fill-current" aria-hidden="true" />
              </Badge>
              {Number.isFinite(ratingCount) && ratingCount > 0 && (
                <span className="text-xs font-medium text-ink-tertiary">
                  ({ratingCount.toLocaleString('en-IN')})
                </span>
              )}
            </div>
          )}

          {/* Price row — blue price, struck compare, green % off */}
          <div className="mt-auto flex flex-wrap items-baseline gap-x-2 gap-y-0.5 pt-0.5">
            <span className="text-base font-bold tabular-nums text-accent">
              {formatPrice(product.price)}
            </span>
            {onSale && (
              <>
                <s
                  className="text-xs tabular-nums text-ink-tertiary"
                  aria-label={`Was ${formatPrice(product.compare_at_price)}`}
                >
                  {formatPrice(product.compare_at_price)}
                </s>
                {discountPct > 0 && (
                  <span className="text-xs font-semibold text-rating">{discountPct}% off</span>
                )}
              </>
            )}
          </div>

          {/* Low-stock / out-of-stock hint */}
          {stock.tone !== 'success' && (
            <p
              className={cn(
                'text-xs font-medium',
                stock.tone === 'danger' ? 'text-danger' : 'text-warning',
              )}
            >
              {stock.text}
            </p>
          )}
        </div>
      </Link>

      {/* Always-visible Add to Cart — blue, matches the ShopFlow reference */}
      <div className="p-3 pt-0">
        <button
          type="button"
          onClick={handleQuickAdd}
          disabled={outOfStock}
          aria-label={`Add ${product.name} to cart`}
          className={cn(
            'flex h-9 w-full items-center justify-center gap-2 rounded-sm',
            'bg-accent text-xs font-semibold text-white',
            'transition-colors hover:bg-accent-hover',
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
