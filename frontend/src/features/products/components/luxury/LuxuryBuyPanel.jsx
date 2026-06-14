import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Minus,
  Plus,
  ShoppingCart,
  Zap,
  Check,
  Share2,
  Truck,
  RotateCcw,
  ShieldCheck,
  Lock,
  CreditCard,
} from 'lucide-react';
import { formatPrice, cn } from '@/lib/utils.js';
import { useAddToCart } from '@/features/cart/hooks.js';
import { useAuthStore } from '@/features/auth/store.js';
import { WishlistButton } from '@/features/wishlist/WishlistButton.jsx';
import { StarRating } from '@/features/reviews/StarRating.jsx';
import { Button } from '@/components/ui/Button.jsx';

/**
 * Conversion panel — flat Flipkart/Amazon marketplace style.
 * ADD TO CART = bg-cart (amber). BUY NOW = bg-cta (orange).
 * All logic (navigate, mutate, share, delivery dates, EMI) unchanged.
 */
export function LuxuryBuyPanel({ product }) {
  const navigate = useNavigate();
  const addToCart = useAddToCart();
  const user = useAuthStore((s) => s.user);

  const maxQty = Math.max(1, product.stock);
  const [qty, setQty] = useState(1);
  const [added, setAdded] = useState(false);
  const [shared, setShared] = useState(false);

  const price = Number(product.price) || 0;
  const compareAt = Number(product.compare_at_price) || 0;
  const hasDiscount = compareAt > price;
  const discountPct = hasDiscount ? Math.round((1 - price / compareAt) * 100) : 0;
  const saved = hasDiscount ? compareAt - price : 0;

  const emiPerMonth = price >= 3000 ? Math.round(price / 6) : 0;

  const delivery = useMemo(() => makeDeliveryDates(), []);
  const outOfStock = product.stock <= 0;
  const ratingCount = Number(product.rating_count) || 0;

  function handleAddToCart() {
    if (!user) {
      navigate(`/login?next=/products/${product.id}`);
      return;
    }
    setAdded(false);
    addToCart.mutate(
      { productId: product.id, quantity: qty },
      {
        onSuccess: () => {
          setAdded(true);
          setTimeout(() => setAdded(false), 2000);
        },
      },
    );
  }

  function handleBuyNow() {
    const target = `/checkout?buyNow=${product.id}&qty=${qty}`;
    if (!user) {
      navigate(`/login?next=${encodeURIComponent(target)}`);
      return;
    }
    navigate(target);
  }

  async function handleShare() {
    const url = window.location.href;
    try {
      if (navigator.share) {
        await navigator.share({ title: product.name, url });
      } else {
        await navigator.clipboard.writeText(url);
        setShared(true);
        setTimeout(() => setShared(false), 1800);
      }
    } catch {
      /* user dismissed share sheet */
    }
  }

  return (
    <aside
      aria-label="Purchase options"
      className="overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated shadow-sm lg:sticky lg:top-20"
    >
      {/* Rating link */}
      {ratingCount > 0 && (
        <div className="border-b border-line-subtle px-4 py-3">
          <a
            href="#reviews"
            className="inline-flex items-center gap-2 text-sm text-ink-secondary transition-colors hover:text-ink-primary focus-visible:outline-none focus-visible:underline"
          >
            <StarRating value={Number(product.rating_avg) || 0} size="sm" />
            <span className="font-semibold tabular-nums text-ink-primary">
              {Number(product.rating_avg).toFixed(1)}
            </span>
            <span className="text-ink-tertiary">
              ({ratingCount.toLocaleString()} review{ratingCount === 1 ? '' : 's'})
            </span>
          </a>
        </div>
      )}

      <div className="px-4 py-4 space-y-4">
        {/* Price */}
        <div>
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <span className="nums text-[1.75rem] font-semibold leading-none text-accent tabular-nums">
              {formatPrice(price)}
            </span>
            {hasDiscount && (
              <>
                <span className="nums text-sm text-ink-tertiary line-through tabular-nums">
                  {formatPrice(compareAt)}
                </span>
                <span className="rounded-sm bg-rating/12 px-1.5 py-0.5 text-xs font-bold text-rating">
                  {discountPct}% OFF
                </span>
              </>
            )}
          </div>
          <p className="mt-1 text-xs text-ink-tertiary">
            Inclusive of all taxes.
            {hasDiscount && (
              <span className="ml-1 text-rating">You save {formatPrice(saved)}.</span>
            )}
          </p>
        </div>

        {/* EMI */}
        {emiPerMonth > 0 && (
          <p className="inline-flex items-center gap-1.5 rounded-sm border border-line-subtle bg-bg-sunken px-2.5 py-1.5 text-xs text-ink-secondary">
            <CreditCard className="size-3.5 text-accent" aria-hidden="true" />
            EMI from{' '}
            <strong className="text-ink-primary">{formatPrice(emiPerMonth)}/mo</strong>
            <span className="text-ink-tertiary">· 6 mo (est.)</span>
          </p>
        )}

        {/* Delivery */}
        <div className="rounded-sm bg-bg-sunken px-3 py-2.5 text-xs space-y-1">
          <p className="text-ink-secondary">
            Free delivery by{' '}
            <strong className="font-semibold text-ink-primary">{delivery.free}</strong>
          </p>
          <p className="text-ink-secondary">
            Or fastest by{' '}
            <strong className="font-semibold text-accent">{delivery.fast}</strong>
          </p>
        </div>

        {/* Stock status */}
        <p
          className={cn(
            'text-sm font-semibold',
            outOfStock ? 'text-danger' : product.stock <= 5 ? 'text-warning' : 'text-success',
          )}
        >
          {outOfStock
            ? 'Out of stock'
            : product.stock <= 5
              ? `Hurry — only ${product.stock} left`
              : 'In stock'}
        </p>

        {/* Quantity stepper */}
        <div>
          <label id="lux-qty-label" className="mb-1.5 block text-xs font-medium text-ink-secondary" htmlFor="lux-qty">
            Quantity
          </label>
          <div
            role="group"
            aria-labelledby="lux-qty-label"
            className="inline-flex h-9 items-center rounded-sm border border-line-subtle bg-bg-sunken"
          >
            <button
              type="button"
              aria-label="Decrease quantity"
              disabled={qty <= 1 || outOfStock}
              onClick={() => setQty((q) => Math.max(1, q - 1))}
              className="grid size-9 place-items-center text-ink-secondary transition-colors hover:text-ink-primary focus-visible:focus-ring disabled:opacity-30"
            >
              <Minus className="size-4" />
            </button>
            <output
              id="lux-qty"
              aria-live="polite"
              className="w-9 text-center text-sm font-medium tabular-nums text-ink-primary"
            >
              {qty}
            </output>
            <button
              type="button"
              aria-label="Increase quantity"
              disabled={qty >= maxQty || outOfStock}
              onClick={() => setQty((q) => Math.min(maxQty, q + 1))}
              className="grid size-9 place-items-center text-ink-secondary transition-colors hover:text-ink-primary focus-visible:focus-ring disabled:opacity-30"
            >
              <Plus className="size-4" />
            </button>
          </div>
        </div>

        {/* CTAs */}
        <div className="flex flex-col gap-2.5">
          {/* Add to Cart — amber */}
          <Button
            variant="cart"
            size="lg"
            onClick={handleAddToCart}
            disabled={outOfStock || addToCart.isPending}
            block
          >
            {added ? (
              <>
                <Check className="size-4" aria-hidden="true" />
                Added to Cart
              </>
            ) : (
              <>
                <ShoppingCart className="size-4" aria-hidden="true" />
                Add to Cart
              </>
            )}
          </Button>

          {/* Buy Now — orange */}
          <Button
            variant="cta"
            size="lg"
            onClick={handleBuyNow}
            disabled={outOfStock || addToCart.isPending}
            block
          >
            <Zap className="size-4" aria-hidden="true" />
            Buy Now
          </Button>

          {/* Wishlist + Share row */}
          <div className="grid grid-cols-2 gap-2.5">
            <WishlistButton productId={product.id} variant="inline" />
            <button
              type="button"
              onClick={handleShare}
              className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-sm border border-line-subtle bg-bg-elevated text-sm font-medium text-ink-secondary transition-colors hover:border-line-strong hover:text-ink-primary focus-visible:focus-ring"
            >
              {shared ? (
                <>
                  <Check className="size-4" aria-hidden="true" />
                  Copied
                </>
              ) : (
                <>
                  <Share2 className="size-4" aria-hidden="true" />
                  Share
                </>
              )}
            </button>
          </div>
        </div>

        <p className="flex items-center justify-center gap-1.5 text-xs text-ink-tertiary">
          <Lock className="size-3" aria-hidden="true" />
          Secure, encrypted checkout
        </p>
      </div>

      {/* Assurances strip */}
      <ul className="grid grid-cols-1 gap-2 border-t border-line-subtle px-4 py-3">
        <Assurance icon={Truck} label="Free delivery on every order" />
        <Assurance icon={RotateCcw} label="7-day easy returns" />
        <Assurance icon={ShieldCheck} label="100% authentic · Sold by ShopWell" />
      </ul>

      {addToCart.isError && !addToCart.isPending && (
        <p className="border-t border-line-subtle px-4 pb-3 text-xs text-danger">
          Couldn&apos;t add to cart — try signing in again.
        </p>
      )}
    </aside>
  );
}

function Assurance({ icon: Icon, label }) {
  return (
    <li className="flex items-center gap-2.5 text-xs text-ink-secondary">
      <span className="grid size-6 shrink-0 place-items-center rounded-sm bg-accent/10 text-accent">
        <Icon className="size-3.5" aria-hidden="true" />
      </span>
      {label}
    </li>
  );
}

function makeDeliveryDates() {
  const fmt = (d) =>
    d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  const free = new Date();
  free.setDate(free.getDate() + 5);
  return { free: fmt(free), fast: fmt(tomorrow) };
}
