import { Link } from 'react-router-dom';
import { cn, formatPrice } from '@/lib/utils';
import WImage from '@/components/storefront/WImage';
import { HeartIcon, Stars } from '@/components/storefront/Icons';
import { useAddToCart } from '@/features/cart/hooks';
import {
  useIsInWishlist,
  useAddToWishlist,
  useRemoveFromWishlist,
} from '@/features/wishlist/hooks';

/**
 * ProductCard — wellness-styled product card.
 *
 * Props:
 *   product      — real product shape from the API
 *   buttonLabel  — override the CTA text (default "Add to Cart")
 *
 * Wiring:
 *   - Navigates to /products/:id (real route; not slug)
 *   - Image via WImage using product.image_url
 *   - Prices formatted via formatPrice; compare_at_price shown ONLY when > price
 *   - Rating pill shown ONLY when rating_count > 0
 *   - Subtitle from product.brand; omitted if absent
 *   - Tag badge derived ("Sale" when discounted); no reliance on a `tag` field
 *   - Add-to-cart via useAddToCart; disabled when stock <= 0
 *   - Wishlist heart toggle via useIsInWishlist / useAddToWishlist / useRemoveFromWishlist
 */
export default function ProductCard({ product, buttonLabel = 'Add to Cart' }) {
  const addToCart = useAddToCart();
  const inWishlist = useIsInWishlist(product?.id);
  const addWishlist = useAddToWishlist();
  const removeWishlist = useRemoveFromWishlist();

  if (!product) return null;

  const {
    id,
    name,
    brand,
    price,
    compare_at_price,
    image_url,
    stock,
    rating,
    rating_count,
  } = product;

  const isDiscounted =
    compare_at_price != null && Number(compare_at_price) > Number(price);
  const discountPct = isDiscounted
    ? Math.round(
        ((Number(compare_at_price) - Number(price)) /
          Number(compare_at_price)) *
          100
      )
    : 0;
  const outOfStock = stock <= 0;

  const handleWishlist = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (inWishlist) {
      removeWishlist.mutate(id);
    } else {
      addWishlist.mutate(id);
    }
  };

  const handleAddToCart = () => {
    if (outOfStock) return;
    addToCart.mutate({ productId: id, quantity: 1 });
  };

  return (
    <article className="bg-wcard border border-wline rounded-xl2 overflow-hidden flex flex-col transition-transform duration-200 hover:-translate-y-[5px] hover:shadow-[0_24px_50px_-28px_rgba(40,30,10,0.42)] animate-rise">
      {/* Image area */}
      <Link
        to={`/products/${id}`}
        className="relative block"
        style={{ background: 'linear-gradient(160deg,#efe9df,#e4dccd)' }}
      >
        <WImage src={image_url} alt={name} className="w-full h-[200px]" />

        {/* Derived tag badge — "Sale" when discounted, omitted otherwise */}
        {isDiscounted && (
          <span className="absolute top-3 left-3 bg-wpaper/90 text-wgreen text-[9.5px] tracking-[0.14em] uppercase px-[11px] py-[5px] rounded-full border border-wline">
            Sale
          </span>
        )}

        {/* Wishlist heart toggle */}
        <button
          onClick={handleWishlist}
          aria-label={inWishlist ? 'Remove from wishlist' : 'Add to wishlist'}
          className={cn(
            'absolute top-3 right-3 w-8 h-8 rounded-full flex items-center justify-center border border-wline bg-wcard/80 backdrop-blur-sm transition-colors',
            inWishlist
              ? 'text-red-500 border-red-200'
              : 'text-wmuted hover:text-red-400'
          )}
        >
          <HeartIcon size={15} filled={inWishlist} />
        </button>
      </Link>

      {/* Body */}
      <div className="p-[18px] pb-5 flex flex-col flex-1">
        {/* Rating pill — only when rating_count > 0 */}
        {rating_count > 0 && (
          <div className="flex items-center gap-1.5 text-[11.5px] text-wgold mb-[7px]">
            <Stars />
            <span className="text-wmuted">{Number(rating).toFixed(1)}</span>
            <span className="text-wmuted text-[11px]">
              ({Number(rating_count).toLocaleString('en-IN')})
            </span>
          </div>
        )}

        {/* Product name */}
        <Link
          to={`/products/${id}`}
          className="font-wserif font-semibold text-[21px] m-0 mb-[5px] leading-[1.15] no-underline text-wink hover:text-wgreen transition-colors"
        >
          {name}
        </Link>

        {/* Subtitle = brand; omit if absent; flex-1 so price row stays at bottom */}
        {brand ? (
          <p className="text-[12.5px] text-wmuted leading-[1.5] m-0 mb-3.5 font-light flex-1">
            {brand}
          </p>
        ) : (
          <div className="flex-1 min-h-[14px]" />
        )}

        {/* Price row */}
        <div className="flex items-baseline gap-2.5 mb-[13px] flex-wrap">
          <span className="font-wserif text-[20px] text-wink">
            {formatPrice(price)}
          </span>
          {isDiscounted && (
            <>
              <span className="text-[13px] text-wmuted line-through">
                {formatPrice(compare_at_price)}
              </span>
              <span className="text-[12px] text-wgreen font-medium">
                {discountPct}% off
              </span>
            </>
          )}
        </div>

        {/* CTA */}
        {outOfStock ? (
          <button
            disabled
            className="w-full bg-wline/60 text-wmuted border-0 rounded-full py-3 text-[12.5px] tracking-wide cursor-not-allowed"
          >
            Out of Stock
          </button>
        ) : (
          <button
            onClick={handleAddToCart}
            disabled={addToCart.isPending}
            className="w-full bg-wgreen text-white border-0 rounded-full py-3 text-[12.5px] tracking-wide cursor-pointer hover:bg-wgreen-dark disabled:opacity-60 disabled:cursor-wait transition-colors"
          >
            {addToCart.isPending ? 'Adding…' : buttonLabel}
          </button>
        )}
      </div>
    </article>
  );
}
