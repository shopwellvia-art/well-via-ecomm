import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  Minus, Plus, ShoppingBag, Zap, Lock, Truck,
  RotateCcw, ShieldCheck, Check,
} from 'lucide-react';
import { Button } from '@/components/ui/Button.jsx';
import { useAddToCart } from '@/features/cart/hooks.js';
import { useAuthStore } from '@/features/auth/store.js';
import { formatPrice } from '@/lib/utils.js';
import { WishlistButton } from '@/features/wishlist/WishlistButton.jsx';
import { attentionPulse, tapPress } from '@/lib/motion.js';

/**
 * Right-rail buy box — the "convert" panel.
 *
 * Two actions stacked:
 *   1. Add to Cart  — adds to the persistent cart, stays on page.
 *   2. Buy Now      — skips the cart and jumps to /checkout.
 *
 * Uses design-system tokens throughout (no hardcoded hex). The warm CTA
 * colours come from warning/accent tokens so both themes stay correct.
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
      className="rounded-lg border border-line-subtle bg-bg-elevated p-5 shadow-md lg:sticky lg:top-24"
    >
      {/* Price block */}
      <div className="flex items-baseline gap-2">
        <p className="nums text-h2 font-semibold text-ink-primary">
          {formatPrice(product.price)}
        </p>
        {product.compare_at_price && Number(product.compare_at_price) > Number(product.price) && (
          <s className="nums text-sm text-ink-tertiary" aria-label={`Was ${formatPrice(product.compare_at_price)}`}>
            {formatPrice(product.compare_at_price)}
          </s>
        )}
      </div>
      <p className="mt-0.5 text-xs text-ink-tertiary">Inclusive of all taxes.</p>

      {/* Delivery estimate */}
      <div className="mt-4 space-y-1.5 rounded-sm bg-bg-sunken px-3 py-2.5 text-sm">
        <p>
          <span className="text-ink-tertiary">Free delivery </span>
          <strong className="text-ink-primary">{deliveryDates.free}</strong>
        </p>
        <p>
          <span className="text-ink-tertiary">Or fastest by </span>
          <strong className="text-accent">{deliveryDates.fast}</strong>
        </p>
      </div>

      {/* Stock status */}
      <p
        className={[
          'mt-4 text-sm font-semibold',
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
      <div className="mt-4">
        <label className="text-xs font-medium text-ink-secondary" htmlFor="buybox-qty">
          Quantity
        </label>
        <div className="mt-1.5 flex h-10 w-fit items-center rounded-sm border border-line-subtle bg-bg-sunken">
          <button
            type="button"
            aria-label="Decrease quantity"
            disabled={qty <= 1 || outOfStock}
            onClick={() => setQty((q) => Math.max(1, q - 1))}
            className="grid size-10 place-items-center text-ink-secondary transition-colors hover:text-ink-primary focus-visible:focus-ring disabled:opacity-30"
          >
            <Minus className="size-4" />
          </button>
          <span
            id="buybox-qty"
            className="nums w-10 text-center text-sm text-ink-primary"
          >
            {qty}
          </span>
          <button
            type="button"
            aria-label="Increase quantity"
            disabled={qty >= maxQty || outOfStock}
            onClick={() => setQty((q) => Math.min(maxQty, q + 1))}
            className="grid size-10 place-items-center text-ink-secondary transition-colors hover:text-ink-primary focus-visible:focus-ring disabled:opacity-30"
          >
            <Plus className="size-4" />
          </button>
        </div>
      </div>

      {/* CTAs */}
      <div className="mt-5 flex flex-col gap-2.5">
        {/* Add to Cart — warning-toned to match conventional ecomm hierarchy */}
        <motion.button
          type="button"
          whileTap={tapPress}
          onClick={handleAddToCart}
          disabled={outOfStock || addToCart.isPending}
          className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-full bg-warning text-sm font-semibold text-bg-base shadow-sm transition-[filter,opacity] hover:brightness-110 focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-40"
        >
          {added ? (
            <motion.span
              animate={attentionPulse}
              className="inline-flex items-center gap-2"
            >
              <Check className="size-4" aria-hidden="true" />
              Added to cart
            </motion.span>
          ) : (
            <>
              <ShoppingBag className="size-4" aria-hidden="true" />
              Add to Cart
            </>
          )}
        </motion.button>

        {/* Buy Now — accent-toned, slightly lower visual weight */}
        <motion.button
          type="button"
          whileTap={tapPress}
          onClick={handleBuyNow}
          disabled={outOfStock}
          className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-full bg-accent text-sm font-semibold text-white shadow-sm transition-[filter,opacity] hover:brightness-110 focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-40"
        >
          <Zap className="size-4" aria-hidden="true" />
          Buy Now
        </motion.button>

        <WishlistButton productId={product.id} variant="inline" />
      </div>

      <p className="mt-3 flex items-center justify-center gap-1.5 text-xs text-ink-tertiary">
        <Lock className="size-3" aria-hidden="true" />
        Secure transaction
      </p>

      {/* Seller info strip */}
      <ul className="mt-5 flex flex-col gap-2 border-t border-line-subtle pt-4 text-xs">
        <InfoRow icon={Truck} label="Ships from" value="ShopWell warehouse" />
        <InfoRow icon={ShieldCheck} label="Sold by" value="ShopWell Retail" />
        <InfoRow icon={RotateCcw} label="Returns" value="7 days from delivery" />
      </ul>

      {addToCart.isError && !addToCart.isPending && (
        <p className="mt-3 text-xs text-danger">
          Couldn't add to cart — try signing in again.
        </p>
      )}
    </aside>
  );
}

function InfoRow({ icon: Icon, label, value }) {
  return (
    <li className="flex items-start gap-2 text-ink-secondary">
      <Icon className="mt-0.5 size-3.5 shrink-0 text-ink-tertiary" aria-hidden="true" />
      <div className="flex-1">
        <span className="text-ink-tertiary">{label}:</span>{' '}
        <span className="text-ink-primary">{value}</span>
      </div>
    </li>
  );
}

/** Free delivery ~5 days out, fast delivery tomorrow. Format like "Sun, 31 May". */
function makeDeliveryDates() {
  const fmt = (d) =>
    d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  const free = new Date();
  free.setDate(free.getDate() + 5);
  return { free: fmt(free), fast: fmt(tomorrow) };
}
