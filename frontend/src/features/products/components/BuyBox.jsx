import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Minus, Plus, ShoppingCart, Zap, Lock, Truck,
  RotateCcw, ShieldCheck, Check,
} from 'lucide-react';
import { Button } from '@/components/ui/Button.jsx';
import { useAddToCart } from '@/features/cart/hooks.js';
import { useAuthStore } from '@/features/auth/store.js';
import { formatPrice } from '@/lib/utils.js';
import { WishlistButton } from '@/features/wishlist/WishlistButton.jsx';

/**
 * Right-rail buy box — flat Flipkart/Amazon style.
 * ADD TO CART uses bg-cart (amber), BUY NOW uses bg-cta (orange).
 * All logic unchanged; only presentation restyled.
 */
export function BuyBox({ product }) {
  const navigate = useNavigate();
  const addToCart = useAddToCart();
  const user = useAuthStore((s) => s.user);

  const maxQty = Math.max(1, product.stock);
  const [qty, setQty] = useState(1);
  const [added, setAdded] = useState(false);

  const deliveryDates = useMemo(() => makeDeliveryDates(), []);
  const outOfStock = product.stock <= 0;

  function handleAddToCart() {
    if (!user) {
      navigate(`/login?next=/products/${product.id}`);
      return;
    }
    addToCart.mutate(
      { productId: product.id, quantity: qty },
      { onSuccess: () => setAdded(true) },
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

  return (
    <aside
      aria-label="Purchase options"
      className="rounded-sm border border-line-subtle bg-bg-elevated shadow-sm lg:sticky lg:top-20"
    >
      {/* Price block */}
      <div className="border-b border-line-subtle px-4 py-4">
        <div className="flex flex-wrap items-baseline gap-2">
          <p className="nums text-2xl font-semibold text-accent">
            {formatPrice(product.price)}
          </p>
          {product.compare_at_price && Number(product.compare_at_price) > Number(product.price) && (
            <>
              <s
                className="nums text-sm text-ink-tertiary"
                aria-label={`Was ${formatPrice(product.compare_at_price)}`}
              >
                {formatPrice(product.compare_at_price)}
              </s>
              <span className="text-sm font-semibold text-rating">
                {Math.round(
                  (1 - Number(product.price) / Number(product.compare_at_price)) * 100,
                )}
                % off
              </span>
            </>
          )}
        </div>
        <p className="mt-0.5 text-xs text-ink-tertiary">Inclusive of all taxes.</p>
      </div>

      <div className="px-4 py-4 space-y-4">
        {/* Delivery estimate */}
        <div className="rounded-sm bg-bg-sunken px-3 py-2.5 text-xs space-y-1">
          <p className="text-ink-secondary">
            Free delivery{' '}
            <strong className="font-semibold text-ink-primary">{deliveryDates.free}</strong>
          </p>
          <p className="text-ink-secondary">
            Or fastest by{' '}
            <strong className="font-semibold text-accent">{deliveryDates.fast}</strong>
          </p>
        </div>

        {/* Stock status */}
        <p
          className={[
            'text-sm font-semibold',
            outOfStock ? 'text-danger' : product.stock <= 5 ? 'text-warning' : 'text-success',
          ].join(' ')}
        >
          {outOfStock
            ? 'Out of stock'
            : product.stock <= 5
              ? `Only ${product.stock} left in stock — order soon`
              : 'In stock'}
        </p>

        {/* Quantity stepper */}
        <div>
          <label className="mb-1.5 block text-xs font-medium text-ink-secondary" htmlFor="buybox-qty">
            Quantity
          </label>
          <div className="inline-flex h-9 items-center rounded-sm border border-line-subtle bg-bg-sunken">
            <button
              type="button"
              aria-label="Decrease quantity"
              disabled={qty <= 1 || outOfStock}
              onClick={() => setQty((q) => Math.max(1, q - 1))}
              className="grid size-9 place-items-center text-ink-secondary transition-colors hover:text-ink-primary focus-visible:outline-none disabled:opacity-30"
            >
              <Minus className="size-4" />
            </button>
            <span
              id="buybox-qty"
              className="nums w-9 text-center text-sm font-medium text-ink-primary"
            >
              {qty}
            </span>
            <button
              type="button"
              aria-label="Increase quantity"
              disabled={qty >= maxQty || outOfStock}
              onClick={() => setQty((q) => Math.min(maxQty, q + 1))}
              className="grid size-9 place-items-center text-ink-secondary transition-colors hover:text-ink-primary focus-visible:outline-none disabled:opacity-30"
            >
              <Plus className="size-4" />
            </button>
          </div>
        </div>

        {/* CTAs */}
        <div className="flex flex-col gap-3">
          {/* Add to Cart — amber (bg-cart) */}
          <Button
            variant="cart"
            size="lg"
            onClick={handleAddToCart}
            disabled={outOfStock || addToCart.isPending}
            className="w-full"
          >
            {added ? (
              <span className="inline-flex items-center gap-2">
                <Check className="size-4" aria-hidden="true" />
                Added to Cart
              </span>
            ) : (
              <span className="inline-flex items-center gap-2">
                <ShoppingCart className="size-4" aria-hidden="true" />
                Add to Cart
              </span>
            )}
          </Button>

          {/* Buy Now — orange (bg-cta) */}
          <Button
            variant="cta"
            size="lg"
            onClick={handleBuyNow}
            disabled={outOfStock}
            className="w-full"
          >
            <span className="inline-flex items-center gap-2">
              <Zap className="size-4" aria-hidden="true" />
              Buy Now
            </span>
          </Button>

          <WishlistButton productId={product.id} variant="inline" />
        </div>

        <p className="flex items-center justify-center gap-1.5 text-xs text-ink-tertiary">
          <Lock className="size-3" aria-hidden="true" />
          Secure transaction
        </p>
      </div>

      {/* Seller info strip */}
      <ul className="border-t border-line-subtle px-4 py-3 flex flex-col gap-2">
        <InfoRow icon={Truck} label="Ships from" value="ShopWell warehouse" />
        <InfoRow icon={ShieldCheck} label="Sold by" value="ShopWell Retail" />
        <InfoRow icon={RotateCcw} label="Returns" value="7 days from delivery" />
      </ul>

      {addToCart.isError && !addToCart.isPending && (
        <p className="border-t border-line-subtle px-4 pb-3 text-xs text-danger">
          Couldn't add to cart — try signing in again.
        </p>
      )}
    </aside>
  );
}

function InfoRow({ icon: Icon, label, value }) {
  return (
    <li className="flex items-center gap-2 text-xs text-ink-secondary">
      <Icon className="size-3.5 shrink-0 text-ink-tertiary" aria-hidden="true" />
      <span className="text-ink-tertiary">{label}:</span>{' '}
      <span className="font-medium text-ink-primary">{value}</span>
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
