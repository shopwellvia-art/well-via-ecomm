import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { ShoppingCart, Zap, Check } from 'lucide-react';
import { formatPrice } from '@/lib/utils.js';
import { useAddToCart } from '@/features/cart/hooks.js';
import { useAuthStore } from '@/features/auth/store.js';
import { ProductMedia } from '../ProductMedia.jsx';

/**
 * Persistent buy bar — slides in once the buy panel scrolls out of view.
 * Flat Flipkart/Amazon style: white bar, border-top, cart=amber, cta=orange.
 * All logic (IntersectionObserver, auth guard, navigate, mutate) unchanged.
 */
export function StickyBuyBar({ product }) {
  const navigate = useNavigate();
  const reduce = useReducedMotion();
  const addToCart = useAddToCart();
  const user = useAuthStore((s) => s.user);
  const [visible, setVisible] = useState(false);
  const [added, setAdded] = useState(false);

  useEffect(() => {
    const anchor = document.getElementById('pdp-buybox');
    if (anchor && 'IntersectionObserver' in window) {
      const io = new IntersectionObserver(
        ([entry]) => {
          setVisible(!entry.isIntersecting && entry.boundingClientRect.top < 0);
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
        setVisible(window.scrollY > 620);
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
    <AnimatePresence>
      {visible && (
        <motion.div
          initial={reduce ? false : { y: '100%' }}
          animate={{ y: 0 }}
          exit={reduce ? { opacity: 0 } : { y: '100%' }}
          transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
          className="fixed inset-x-0 bottom-0 z-40 border-t border-line-subtle bg-bg-elevated shadow-lg"
        >
          <div className="mx-auto flex max-w-content items-center gap-3 px-4 py-3 sm:gap-4 sm:px-6">
            {/* Product thumbnail */}
            <div className="hidden size-11 shrink-0 overflow-hidden rounded-sm border border-line-subtle sm:block">
              <ProductMedia product={product} />
            </div>

            {/* Name + price */}
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-ink-primary">
                {product.name}
              </p>
              <p className="nums text-sm font-semibold text-accent tabular-nums">
                {formatPrice(product.price)}
              </p>
            </div>

            {/* Buy Now — orange (cta), hidden on smallest mobile */}
            <button
              type="button"
              onClick={handleBuyNow}
              disabled={outOfStock}
              className="hidden h-10 items-center justify-center gap-1.5 rounded-sm bg-cta px-5 text-sm font-semibold uppercase tracking-wide text-white transition-[filter] hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cta disabled:pointer-events-none disabled:opacity-40 sm:inline-flex"
            >
              <Zap className="size-4" aria-hidden="true" />
              Buy Now
            </button>

            {/* Add to Cart — amber (cart) */}
            <button
              type="button"
              onClick={handleAdd}
              disabled={outOfStock || addToCart.isPending}
              className="inline-flex h-10 flex-1 items-center justify-center gap-1.5 rounded-sm bg-accent px-5 text-sm font-semibold text-white transition-[background-color] hover:bg-accent-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:pointer-events-none disabled:opacity-40 sm:flex-none"
            >
              {added ? (
                <>
                  <Check className="size-4" aria-hidden="true" />
                  Added
                </>
              ) : outOfStock ? (
                'Out of stock'
              ) : (
                <>
                  <ShoppingCart className="size-4" aria-hidden="true" />
                  Add to Cart
                </>
              )}
            </button>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
