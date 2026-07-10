import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Minus,
  Plus,
  ShoppingCart,
  Zap,
  Check,
  Share2,
  Copy,
  Star,
  Sparkles,
  Truck,
  RotateCcw,
  ArrowUpRight,
  BadgePercent,
  ShieldCheck,
  FileText,
  ListOrdered,
  MessageCircleQuestion,
} from 'lucide-react';
import { cn, formatPrice } from '@/lib/utils.js';
import { useAddToCart } from '@/features/cart/hooks.js';
import { usePublicSettings } from '@/features/settings/public.js';
import { WishlistButton } from '@/features/wishlist/WishlistButton.jsx';
import { Button } from '@/components/storefront/ui/Button.jsx';
import Accordion from '@/components/storefront/Accordion.jsx';
import PincodeCheck from '@/features/shipping/components/PincodeCheck.jsx';

/**
 * PDP buy rail — the full right column of the buy area, in mockup order:
 * title → flavour chip → rating anchor (#reviews) → price + strikethrough →
 * offer chip → qty stepper → coupon box with Copy → Add to cart →
 * pincode check → free-shipping + refund rows → accordions
 * (Description / How to use / FAQ) → secure-payments note.
 *
 * All buy logic (auth guard, navigate, add-to-cart mutate, buy-now, share)
 * is unchanged. Every content block hides itself when its field is null.
 */

// Mockup purple accents — no Tailwind token exists for the PDP purple.
const PDP_PURPLE = '#7c4a8c';
const PDP_PURPLE_DEEP = '#4a2b5c';
const PDP_LAVENDER = '#ede4f3';

/** Coerce a JSON column that may arrive as an array or a JSON string. */
function toArray(value) {
  if (Array.isArray(value)) return value;
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }
  return [];
}

export function LuxuryBuyPanel({ product }) {
  const navigate = useNavigate();
  const addToCart = useAddToCart();
  const { data: publicCfg } = usePublicSettings();

  const maxQty = Math.max(1, product.stock);
  const [qty, setQty] = useState(1);
  const [added, setAdded] = useState(false);
  const [copied, setCopied] = useState(false);
  const [shared, setShared] = useState(false);

  const price = Number(product.price) || 0;
  const compareAt = Number(product.compare_at_price) || 0;
  const hasDiscount = compareAt > price;
  const ratingAvg = Number(product.rating_avg) || 0;
  const ratingCount = Number(product.rating_count) || 0;
  const outOfStock = product.stock <= 0;

  const description = (product.description || '').trim();
  const usageSteps = toArray(product.usage_steps).filter((s) => s && (s.label || s.text));
  const faqs = toArray(product.faqs).filter((f) => f && (f.q || f.a));
  const freeShipThreshold = Number(publicCfg?.['shipping.free_threshold'] || 0);
  const hasAccordions = Boolean(description) || usageSteps.length > 0 || faqs.length > 0;

  function handleAddToCart() {
    // Guests add to the client-side cart (features/cart/guestStore) — no
    // login gate; the cart merges into the server cart at checkout login.
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
    // Checkout's bag step embeds login for guests — go straight there.
    navigate(`/checkout?buyNow=${product.id}&qty=${qty}`);
  }

  async function handleCopyCoupon() {
    try {
      await navigator.clipboard.writeText(product.coupon_code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      /* clipboard unavailable — leave the code visible for manual copy */
    }
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
    <div className="flex flex-col gap-5">
      {/* Title + flavour chip */}
      <div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h1 className="font-wserif font-medium text-[clamp(26px,3vw,38px)] leading-[1.1] text-wink">
            {product.name}
          </h1>
          {product.flavour && (
            <span
              className="inline-flex items-center rounded-full px-3.5 py-1.5 text-xs font-medium text-white"
              style={{ backgroundColor: PDP_PURPLE }}
            >
              {product.flavour}
            </span>
          )}
        </div>

        {/* Rating anchor — scrolls to the reviews block */}
        {ratingCount > 0 && (
          <a
            href="#reviews"
            className="mt-2.5 inline-flex items-center gap-1.5 text-sm font-medium text-wink underline underline-offset-4 decoration-wmuted/60 transition-colors hover:decoration-wgreen"
            aria-label={`Rated ${ratingAvg.toFixed(1)} out of 5 — read reviews`}
          >
            <Star className="size-4 fill-wgold text-wgold" aria-hidden="true" />
            {ratingAvg.toFixed(1)}/5
          </a>
        )}
      </div>

      {/* Price + offer chip */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="text-[clamp(24px,2.6vw,30px)] font-bold leading-none text-wink">
          {formatPrice(price)}
        </span>
        {hasDiscount && (
          <span
            className="text-base text-wmuted line-through"
            aria-label={`Was ${formatPrice(compareAt)}`}
          >
            {formatPrice(compareAt)}
          </span>
        )}
        {product.offer_text && (
          <span
            className="inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-semibold"
            style={{
              borderColor: PDP_PURPLE,
              color: PDP_PURPLE_DEEP,
              backgroundColor: 'rgba(124, 74, 140, 0.08)',
            }}
          >
            <Sparkles className="size-3.5" aria-hidden="true" />
            {product.offer_text}
          </span>
        )}
      </div>

      {/* Quantity stepper */}
      <div className="flex items-center gap-3">
        <div
          role="group"
          aria-labelledby="lux-qty-label"
          className="inline-flex items-center rounded-lg border border-wline bg-wcard"
        >
          <button
            type="button"
            aria-label="Decrease quantity"
            disabled={qty <= 1 || outOfStock}
            onClick={() => setQty((q) => Math.max(1, q - 1))}
            className="grid size-9 place-items-center text-wmuted transition-colors hover:text-wgreen focus-visible:outline-none disabled:opacity-30"
          >
            <Minus className="size-4" aria-hidden="true" />
          </button>
          <output
            id="lux-qty"
            aria-live="polite"
            className="w-9 text-center text-sm font-semibold nums text-wink"
          >
            {qty}
          </output>
          <button
            type="button"
            aria-label="Increase quantity"
            disabled={qty >= maxQty || outOfStock}
            onClick={() => setQty((q) => Math.min(maxQty, q + 1))}
            className="grid size-9 place-items-center text-wmuted transition-colors hover:text-wgreen focus-visible:outline-none disabled:opacity-30"
          >
            <Plus className="size-4" aria-hidden="true" />
          </button>
        </div>
        <span
          id="lux-qty-label"
          className="text-xs font-semibold uppercase tracking-wide text-wmuted"
        >
          Qty
        </span>
        {outOfStock ? (
          <span className="text-xs font-medium text-red-600">Out of stock</span>
        ) : product.stock <= 5 ? (
          <span className="text-xs font-medium text-wgold">Only {product.stock} left</span>
        ) : null}
      </div>

      {/* Coupon box */}
      {product.coupon_code && (
        <div
          className="flex items-center justify-between gap-3 rounded-xl px-4 py-3"
          style={{ backgroundColor: PDP_LAVENDER }}
        >
          <div className="flex min-w-0 items-center gap-2.5">
            <BadgePercent
              className="size-5 shrink-0"
              style={{ color: PDP_PURPLE }}
              aria-hidden="true"
            />
            <div className="min-w-0">
              {product.coupon_hint && (
                <p
                  className="truncate text-sm font-semibold"
                  style={{ color: PDP_PURPLE_DEEP }}
                >
                  {product.coupon_hint}
                </p>
              )}
              <p className="text-xs" style={{ color: PDP_PURPLE_DEEP }}>
                Use Code :{' '}
                <span className="font-semibold tracking-wide">{product.coupon_code}</span>
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleCopyCoupon}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-dashed bg-white/80 px-3 py-2 text-xs font-semibold transition-colors hover:bg-white focus-visible:outline-none"
            style={{ borderColor: PDP_PURPLE, color: PDP_PURPLE_DEEP }}
            aria-label={`Copy coupon code ${product.coupon_code}`}
          >
            {copied ? (
              <>
                <Check className="size-3.5" aria-hidden="true" />
                Copied!
              </>
            ) : (
              <>
                <Copy className="size-3.5" aria-hidden="true" />
                Copy
              </>
            )}
          </button>
        </div>
      )}

      {/* CTAs — id anchors the StickyBuyBar IntersectionObserver */}
      <div id="pdp-buybox" className="flex flex-col gap-2.5">
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
              Added to cart
            </>
          ) : (
            <>
              <ShoppingCart className="size-4" aria-hidden="true" />
              Add to cart
            </>
          )}
        </Button>

        <Button
          variant="outline"
          size="lg"
          onClick={handleBuyNow}
          disabled={outOfStock || addToCart.isPending}
          block
        >
          <Zap className="size-4" aria-hidden="true" />
          Buy now
        </Button>

        {addToCart.isError && !addToCart.isPending && (
          <p className="text-xs text-red-600">
            Couldn&apos;t add to cart — try signing in again.
          </p>
        )}
      </div>

      {/* Wishlist + Share */}
      <div className="grid grid-cols-2 gap-2.5">
        <WishlistButton productId={product.id} variant="inline" />
        <button
          type="button"
          onClick={handleShare}
          className={cn(
            'inline-flex h-11 w-full items-center justify-center gap-2 rounded-lg border border-wline bg-wcard',
            'text-sm font-medium text-wmuted transition-colors hover:text-wink focus-visible:outline-none',
          )}
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

      {/* Delivery availability — reuses the shipping serviceability widget */}
      <PincodeCheck />

      {/* Free shipping + refund policy rows */}
      <div className="divide-y divide-wline overflow-hidden rounded-xl border border-wline bg-wcard">
        {freeShipThreshold > 0 && (
          <p className="flex items-center gap-2.5 px-4 py-3 text-sm text-wink">
            <Truck className="size-4 shrink-0 text-wgreen" aria-hidden="true" />
            Free Shipping on orders above{' '}
            <span className="font-semibold">
              {formatPrice(freeShipThreshold).replace(/\.00$/, '')}
            </span>
          </p>
        )}
        <Link
          to="/contact"
          className="flex items-center gap-2.5 px-4 py-3 text-sm text-wink transition-colors hover:bg-wpaper/60"
        >
          <RotateCcw className="size-4 shrink-0 text-wgreen" aria-hidden="true" />
          Refund and Replacement Policy
          <ArrowUpRight className="ml-auto size-4 text-wmuted" aria-hidden="true" />
        </Link>
      </div>

      {/* Accordions — Description / How to use / FAQ */}
      {hasAccordions && (
        <div className="flex flex-col gap-2.5">
          {description && (
            <Accordion icon={FileText} title="Description">
              <p className="whitespace-pre-line">{description}</p>
            </Accordion>
          )}

          {usageSteps.length > 0 && (
            <Accordion icon={ListOrdered} title="How to use">
              <ol className="list-decimal space-y-2 pl-5">
                {usageSteps.map((step, i) => (
                  <li key={`${step.label || step.text}-${i}`}>
                    {step.label && (
                      <span className="font-medium text-wink">{step.label}</span>
                    )}
                    {step.label && step.text ? ' — ' : ''}
                    {step.text}
                  </li>
                ))}
              </ol>
            </Accordion>
          )}

          {faqs.length > 0 && (
            <Accordion icon={MessageCircleQuestion} title="FAQ">
              <dl className="space-y-3.5">
                {faqs.map((faq, i) => (
                  <div key={`${faq.q || faq.a}-${i}`}>
                    {faq.q && <dt className="font-medium text-wink">{faq.q}</dt>}
                    {faq.a && <dd className="mt-1">{faq.a}</dd>}
                  </div>
                ))}
              </dl>
            </Accordion>
          )}
        </div>
      )}

      {/* Secure payments note */}
      <p className="flex items-center justify-center gap-1.5 text-sm font-medium text-wink">
        <ShieldCheck className="size-4 text-wgreen" aria-hidden="true" />
        100% Secure Payments
      </p>
    </div>
  );
}
