import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { ShoppingCart, Zap, Check } from 'lucide-react';
import { formatPrice } from '@/lib/utils.js';
import { useAddToCart } from '@/features/cart/hooks.js';
import { useAuthStore } from '@/features/auth/store.js';
import { ProductMedia } from '../ProductMedia.jsx';

/**
 * Sticky buy bar.
 *
 * MOBILE (< lg): always-visible fixed bottom bar with a grid-cols-2 layout —
 *   Add to Cart (amber) | Buy Now (orange). Matches the mock exactly.
 *
 * DESKTOP (≥ lg): the bar slides in once the #pdp-buybox scrolls out of view,
 *   showing the product thumbnail + name + price + Buy Now + Add to Cart.
 *
 * All logic (IntersectionObserver, auth guard, navigate, mutate) unchanged.
 */
export function StickyBuyBar({ product }) {
  const navigate = useNavigate();
  const reduce = useReducedMotion();
  const addToCart = useAddToCart();
  const user = useAuthStore((s) => s.user);
  const [desktopVisible, setDesktopVisible] = useState(false);
  const [added, setAdded] = useState(false);

  useEffect(() => {
    const anchor = document.getElementById('pdp-buybox');
    if (anchor && 'IntersectionObserver' in window) {
      const io = new IntersectionObserver(
        ([entry]) => {
          setDesktopVisible(!entry.isIntersecting && entry.boundingClientRect.top < 0);
        },
        { threshold: 0 },
      );
      io.observe(anchor);
      return () => io.disconnect();
    }
    let ticking = false;
    const onScroll = () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        setDesktopVisible(window.scrollY > 620);
        ticking = false;
      });
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  const outOfStock = product.stock <= 0;

  function addNext(action) {
    if (!user) {
      navigate(`/login?next=/products/${product.id}`);
      return;
    }
    action();
  }

  function handleAdd() {
    addNext(() =>
      addToCart.mutate(
        { productId: product.id, quantity: 1 },
        { onSuccess: () => setAdded(true) },
      ),
    );
  }

  function handleBuyNow() {
    const target = `/checkout?buyNow=${product.id}&qty=1`;
    if (!user) {
      navigate(`/login?next=${encodeURIComponent(target)}`);
      return;
    }
    navigate(target);
  }

  return (
    <>
      {/* ── MOBILE sticky bar — always visible on small screens ── */}
      <div
        role="region"
        aria-label="Quick buy"
        className="fixed inset-x-0 bottom-0 z-40 grid grid-cols-2 gap-2 border-t border-line-subtle bg-bg-elevated p-2.5 shadow-lg lg:hidden"
      >
        <button
          type="button"
          onClick={handleAdd}
          disabled={outOfStock || addToCart.isPending}
          className="flex h-12 items-center justify-center gap-2 rounded-lg bg-cart text-sm font-bold uppercase tracking-wide text-white active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cart disabled:pointer-events-none disabled:opacity-50"
        >
          {added ? (
            <>
              <Check className="size-[18px]" aria-hidden="true" />
              Added
            </>
          ) : (
            <>
              <ShoppingCart className="size-[18px]" aria-hidden="true" />
              {outOfStock ? 'Out of stock' : 'Add to cart'}
            </>
          )}
        </button>
        <button
          type="button"
          onClick={handleBuyNow}
          disabled={outOfStock}
          className="flex h-12 items-center justify-center gap-2 rounded-lg bg-cta text-sm font-bold uppercase tracking-wide text-white active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cta disabled:pointer-events-none disabled:opacity-50"
        >
          <Zap className="size-[18px] fill-current" aria-hidden="true" />
          Buy now
        </button>
      </div>

      {/* ── DESKTOP slide-in bar — appears once buybox scrolls out of view ── */}
      <AnimatePresence>
        {desktopVisible && (
          <motion.div
            initial={reduce ? false : { y: '100%' }}
            animate={{ y: 0 }}
            exit={reduce ? { opacity: 0 } : { y: '100%' }}
            transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
            role="region"
            aria-label="Quick buy"
            className="fixed inset-x-0 bottom-0 z-40 hidden border-t border-line-subtle bg-bg-elevated shadow-lg lg:block"
          >
            <div className="mx-auto flex max-w-content items-center gap-3 px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:gap-4 sm:px-6">
              {/* Product thumbnail */}
              <div className="size-11 shrink-0 overflow-hidden rounded-lg border border-line-subtle">
                <ProductMedia product={product} />
              </div>

              {/* Name + price */}
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-ink-primary">
                  {product.name}
                </p>
                <p className="nums text-sm font-semibold text-ink-primary tabular-nums">
                  {formatPrice(product.price)}
                </p>
              </div>

              {/* Add to Cart — amber */}
              <button
                type="button"
                onClick={handleAdd}
                disabled={outOfStock || addToCart.isPending}
                className="inline-flex h-10 items-center justify-center gap-1.5 rounded-lg bg-cart px-5 text-sm font-bold uppercase tracking-wide text-white transition-[filter] hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cart disabled:pointer-events-none disabled:opacity-40"
              >
                {added ? (
                  <>
                    <Check className="size-4" aria-hidden="true" />
                    Added
                  </>
                ) : (
                  <>
                    <ShoppingCart className="size-4" aria-hidden="true" />
                    Add to Cart
                  </>
                )}
              </button>

              {/* Buy Now — orange */}
              <button
                type="button"
                onClick={handleBuyNow}
                disabled={outOfStock}
                className="inline-flex h-10 items-center justify-center gap-1.5 rounded-lg bg-cta px-5 text-sm font-bold uppercase tracking-wide text-white transition-[filter] hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cta disabled:pointer-events-none disabled:opacity-40"
              >
                <Zap className="size-4 fill-current" aria-hidden="true" />
                Buy Now
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
