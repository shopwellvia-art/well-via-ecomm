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
import { formatPrice } from '@/lib/utils.js';
import { useAddToCart } from '@/features/cart/hooks.js';
import { useAuthStore } from '@/features/auth/store.js';
import { WishlistButton } from '@/features/wishlist/WishlistButton.jsx';
import { Button } from '@/components/ui/Button.jsx';

/**
 * Conversion panel — flat Flipkart/Amazon marketplace style.
 * Rendered inline inside the right column of the hero card (no wrapper card).
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
  const emiPerMonth = price >= 3000 ? Math.round(price / 6) : 0;

  const delivery = useMemo(() => makeDeliveryDates(), []);
  const outOfStock = product.stock <= 0;

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
    <div className="space-y-5">
      {/* EMI */}
      {emiPerMonth > 0 && (
        <p className="inline-flex items-center gap-1.5 rounded-lg border border-line-subtle bg-bg-sunken px-2.5 py-1.5 text-xs text-ink-secondary">
          <CreditCard className="size-3.5 text-accent" aria-hidden="true" />
          EMI from{' '}
          <strong className="text-ink-primary">{formatPrice(emiPerMonth)}/mo</strong>
          <span className="text-ink-tertiary">· 6 mo (est.)</span>
        </p>
      )}

      {/* Delivery + Quantity stepper row */}
      <div className="flex flex-wrap items-start gap-x-8 gap-y-4">
        {/* Delivery */}
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-ink-tertiary">Delivery</p>
          <p className="mt-1 text-sm text-ink-primary">
            Free delivery by <strong className="font-semibold">{delivery.free}</strong>
          </p>
          <p className="text-sm text-ink-secondary">
            Or fastest by <span className="font-semibold text-accent">{delivery.fast}</span>
          </p>
        </div>

        {/* Quantity stepper */}
        <div>
          <label
            id="lux-qty-label"
            htmlFor="lux-qty"
            className="block text-xs font-semibold uppercase tracking-wide text-ink-tertiary"
          >
            Quantity
          </label>
          <div
            role="group"
            aria-labelledby="lux-qty-label"
            className="mt-1 inline-flex items-center rounded-lg border border-line-strong"
          >
            <button
              type="button"
              aria-label="Decrease quantity"
              disabled={qty <= 1 || outOfStock}
              onClick={() => setQty((q) => Math.max(1, q - 1))}
              className="grid size-9 place-items-center text-ink-secondary transition-colors hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-30"
            >
              <Minus className="size-4" aria-hidden="true" />
            </button>
            <output
              id="lux-qty"
              aria-live="polite"
              className="w-9 text-center text-sm font-semibold nums text-ink-primary"
            >
              {qty}
            </output>
            <button
              type="button"
              aria-label="Increase quantity"
              disabled={qty >= maxQty || outOfStock}
              onClick={() => setQty((q) => Math.min(maxQty, q + 1))}
              className="grid size-9 place-items-center text-ink-secondary transition-colors hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-30"
            >
              <Plus className="size-4" aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>

      {/* 3-up trust row */}
      <div className="grid grid-cols-3 gap-2 rounded-lg bg-bg-sunken p-3 text-center">
        <div className="flex flex-col items-center gap-1.5">
          <span className="text-accent" aria-hidden="true">
            <Truck className="size-5" />
          </span>
          <p className="text-xs font-medium text-ink-primary">Free delivery</p>
        </div>
        <div className="flex flex-col items-center gap-1.5">
          <span className="text-accent" aria-hidden="true">
            <RotateCcw className="size-5" />
          </span>
          <p className="text-xs font-medium text-ink-primary">7-day returns</p>
        </div>
        <div className="flex flex-col items-center gap-1.5">
          <span className="text-accent" aria-hidden="true">
            <ShieldCheck className="size-5" />
          </span>
          <p className="text-xs font-medium text-ink-primary">100% authentic</p>
        </div>
      </div>

      {/* CTAs — Add to Cart (amber) + Buy Now (orange) */}
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
      </div>

      {/* Wishlist + Share row */}
      <div className="grid grid-cols-2 gap-2.5">
        <WishlistButton productId={product.id} variant="inline" />
        <button
          type="button"
          onClick={handleShare}
          className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-lg border border-line-subtle bg-bg-elevated text-sm font-medium text-ink-secondary transition-colors hover:border-line-strong hover:text-ink-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
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

      <p className="flex items-center justify-center gap-1.5 text-xs text-ink-tertiary">
        <Lock className="size-3" aria-hidden="true" />
        Secure, encrypted checkout
      </p>

      {addToCart.isError && !addToCart.isPending && (
        <p className="text-xs text-danger">
          Couldn&apos;t add to cart — try signing in again.
        </p>
      )}
    </div>
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
