import { Link, useLocation, useNavigate } from 'react-router-dom';
import { cn, formatPrice } from '@/lib/utils';
import { useAuthStore } from '@/features/auth/store.js';
import WImage from '@/components/storefront/WImage';
import { HeartIcon, Stars } from '@/components/storefront/Icons';
import { useAddToCart } from '@/features/cart/hooks';
import {
  useIsInWishlist,
  useAddToWishlist,
  useRemoveFromWishlist,
} from '@/features/wishlist/hooks';

/** Badge chip styling by kind; free-form strings fall back to 'default'. */
const BADGE_STYLES = {
  bestseller: 'bg-[#8e2f3c] text-white border-transparent',
  new: 'bg-[#7c4a8c] text-white border-transparent',
  sale: 'bg-wpaper/90 text-wgreen border-wline',
  default: 'bg-wpaper/90 text-wgreen border-wline',
};

/**
 * ProductCard — wellness-styled product card.
 *
 * Props:
 *   product      — real product shape from the API
 *   buttonLabel  — override the CTA text (default "Add to Cart")
 *   badge        — listing-context ribbon: 'bestseller' | 'new' | 'sale'.
 *                  Precedence: this prop → product.badge (admin override) →
 *                  derived "Sale" when discounted → none.
 *
 * Wiring:
 *   - Navigates to /products/:id (real route; not slug)
 *   - Image via WImage using product.image_url
 *   - Prices formatted via formatPrice; compare_at_price shown ONLY when > price
 *   - Rating pill shown ONLY when rating_count > 0 (uses rating_avg)
 *   - Subtitle from short_description, else flavour; omitted if absent
 *   - Add-to-cart via useAddToCart; disabled when stock <= 0
 *   - Wishlist heart toggle via useIsInWishlist / useAddToWishlist / useRemoveFromWishlist
 */
export default function ProductCard({ product, buttonLabel = 'Add to Cart', badge }) {
  const addToCart = useAddToCart();
  const inWishlist = useIsInWishlist(product?.id);
  const addWishlist = useAddToWishlist();
  const removeWishlist = useRemoveFromWishlist();
  const isSignedIn = useAuthStore((s) => !!s.accessToken);
  const navigate = useNavigate();
  const location = useLocation();

  if (!product) return null;

  const {
    id,
    name,
    price,
    compare_at_price,
    image_url,
    stock,
    rating_avg,
    rating_count,
    flavour,
    short_description,
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

  // Ribbon: listing context wins, then the admin's free-form product.badge,
  // then the derived Sale chip.
  const badgeText =
    badge != null
      ? badge.charAt(0).toUpperCase() + badge.slice(1)
      : product.badge || (isDiscounted ? 'Sale' : null);
  const badgeStyle =
    BADGE_STYLES[(badge || product.badge || 'sale').toLowerCase()] ||
    BADGE_STYLES.default;

  const subtitle = short_description || null;

  const handleWishlist = (e) => {
    e.preventDefault();
    e.stopPropagation();
    // Wishlist is server-only by design — guests are sent to sign in.
    if (!isSignedIn) {
      navigate(`/login?next=${encodeURIComponent(location.pathname + location.search)}`);
      return;
    }
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
        <WImage
  src={image_url}
  alt={name}
  className="w-full h-[180px] sm:h-[220px] lg:h-[260px] object-contain p-3"
/>

        {badgeText && (
          <span
           className={cn(
  'absolute top-2 left-2 lg:top-3 lg:left-3 text-[6px] lg:text-[9.5px] tracking-[0.08em] uppercase px-2 lg:px-[11px] py-1 lg:py-[5px] rounded-full border',
  badgeStyle,
)}
          >
            {badgeText}
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

      {/* here here */}
      <div className="p-2.5 lg:p-[18px] pb-3 lg:pb-5 flex flex-col flex-1">
        {/* Rating pill — only when rating_count > 0 */}
        {rating_count > 0 && (
  <div className="mb-2 text-[16px] leading-none">
    <Stars count={5} />
  </div>
)}

        {/* Product name */}
        {/* here here */}
        <Link
          to={`/products/${id}`}
          className="font-inter font-bold text-[14px] lg:text-[18px] leading-tight mb-1 lg:mb-[5px] no-underline text-wink hover:text-wgreen transition-colors"
        >
          {name}
        </Link>

        {/* Flavour tag */}
        {/* {flavour && (
          <span className="self-start rounded-full border border-wgold/40 bg-wgold/10 px-2.5 py-0.5 text-[10.5px] tracking-wide text-wgold mb-2">
            {flavour}
          </span>
        )} */}

        {/* Subtitle; flex-1 so price row stays at bottom */}
        {/* {subtitle ? (
          <p className="text-[12.5px] text-wmuted leading-[1.5] m-0 mb-3.5 font-light flex-1 line-clamp-2">
            {subtitle}
          </p>
        ) : (
          <div className="flex-1 min-h-[14px]" />
        )} */}

        {/* Price row */}
        {/* here here */}
        <div className="flex items-baseline gap-2.5 mb-[13px] flex-wrap">
          <span className="font-inter text-[16px] lg:text-[20px] text-wink">
            {formatPrice(price)}
          </span>
          {/* {isDiscounted && (
            <>
              <span className="text-[13px] text-wmuted line-through">
                {formatPrice(compare_at_price)}
              </span>
              <span className="text-[12px] text-wgreen font-medium">
                {discountPct}% off
              </span>
            </>
          )} */}
        </div>

        {/* CTA */}
        {outOfStock ? (
          <button
            disabled
            className="w-full bg-wline/60 text-wmuted border-0 rounded-[6px] py-3 text-[13px] font-semibold tracking-[0.4px] cursor-not-allowed"
          >
            Out of Stock
          </button>
        ) : (
          <button
            onClick={handleAddToCart}
            disabled={addToCart.isPending}
            className="w-full bg-[#08112C] text-white border-0 rounded-md lg:rounded-lg py-2 lg:py-3 text-[12px] lg:text-[13px] font-semibold transition-colors"
          >
            {addToCart.isPending ? 'Adding…' : buttonLabel}
          </button>
        )}
      </div>
    </article>
  );
}
