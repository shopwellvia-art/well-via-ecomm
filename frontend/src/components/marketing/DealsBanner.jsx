import { useState } from 'react';
import { Link } from 'react-router-dom';
import { motion, useReducedMotion } from 'framer-motion';
import { Flame, Clock, Loader2 } from 'lucide-react';
import { SaleCountdown, useSaleTarget } from './SaleCountdown.jsx';
import { useProducts } from '@/features/products/hooks.js';
import { useAddToCart } from '@/features/cart/hooks.js';
import { formatPrice, cn } from '@/lib/utils.js';
import { buttonVariants } from '@/components/ui/Button.jsx';

/**
 * "Deals of the Day" — blue header bar (bg-accent), live countdown,
 * horizontal product rail with blue prices and blue Add to Cart buttons.
 * Matches ShopFlow h4-deals.png.
 */

/**
 * Image sub-component with onError fallback.
 * When the URL exists but fails to load, hides the img and shows the
 * text placeholder — matching the CategoryCircles pattern.
 */
function DealProductImage({ src, alt, loading: loadingProp }) {
  const [failed, setFailed] = useState(false);

  if (!src || failed) {
    return (
      <span className="flex size-full items-center justify-center text-xs text-ink-tertiary">
        No image
      </span>
    );
  }

  return (
    <img
      src={src}
      alt={alt}
      loading={loadingProp}
      onError={() => setFailed(true)}
      className="size-full object-contain p-2 transition-transform duration-200 hover:scale-105"
    />
  );
}

export default function DealsBanner() {
  const reduce = useReducedMotion();
  const target = useSaleTarget();
  const { data: productsPage, isLoading, isError } = useProducts({ page: 1, page_size: 6 });
  const addToCart = useAddToCart();
  const products = productsPage?.items ?? [];

  // Don't render an empty or broken rail — return null so the section
  // doesn't take up space with a styled-but-empty frame.
  if (isError) return null;
  if (!isLoading && products.length === 0) return null;

  return (
    <section className="mx-auto mt-3 max-w-content px-4 sm:px-6">
      <motion.div
        initial={reduce ? false : { opacity: 0, y: 16 }}
        whileInView={reduce ? undefined : { opacity: 1, y: 0 }}
        viewport={{ once: true, amount: 0.2 }}
        transition={{ duration: 0.4 }}
        className="overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated shadow-sm"
      >
        {/* Orange gradient header bar — matches mock */}
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3 bg-gradient-to-r from-cta to-[#ff8a4d] px-5 py-4">
          <h2 className="flex items-center gap-2 text-lg font-bold text-white">
            <Flame className="size-5 fill-white text-white" aria-hidden="true" />
            Deals of the Day
          </h2>
          <div className="flex items-center gap-2 text-white/90">
            <span className="text-[11px] font-semibold uppercase tracking-wide">Ends in</span>
            <SaleCountdown target={target} tone="light" compact />
          </div>
          <Link
            to="/products"
            className="ml-auto inline-flex h-8 items-center gap-1 rounded-sm bg-white px-4 text-xs font-bold uppercase tracking-wide text-cta transition-colors hover:bg-white/90 focus-visible:focus-ring"
          >
            View all
            <Clock className="size-3" aria-hidden="true" />
          </Link>
        </div>

        {/* Product rail */}
        <div className="overflow-x-auto px-3 pb-4 pt-3 [&::-webkit-scrollbar]:hidden">
          {isLoading ? (
            <div className="flex gap-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <div
                  key={i}
                  className="flex h-[220px] w-36 shrink-0 animate-pulse flex-col gap-2 rounded-xs bg-bg-sunken p-3"
                />
              ))}
            </div>
          ) : (
            <ul className="flex gap-px" role="list">
              {products.map((p, idx) => {
                const isPendingThis =
                  addToCart.isPending && addToCart.variables?.productId === p.id;

                return (
                  <li
                    key={p.id}
                    className={cn(
                      'flex w-40 shrink-0 flex-col items-center border-r border-line-subtle px-3 py-3 last:border-r-0',
                      'transition-colors hover:bg-bg-sunken',
                    )}
                  >
                    <Link
                      to={`/products/${p.id}`}
                      className="flex flex-col items-center gap-2 focus-visible:focus-ring"
                      tabIndex={0}
                    >
                      <div className="relative flex size-28 items-center justify-center overflow-hidden rounded-xs bg-bg-sunken">
                        <DealProductImage
                          src={p.image_url}
                          alt={p.name}
                          loading={idx < 3 ? 'eager' : 'lazy'}
                        />
                        {p.compare_at_price != null && Number(p.compare_at_price) > Number(p.price) && (
                          <span className="absolute left-1 top-1 rounded-xs bg-rating px-1 py-0.5 text-[10px] font-bold text-white">
                            {Math.round(((Number(p.compare_at_price) - Number(p.price)) / Number(p.compare_at_price)) * 100)}% OFF
                          </span>
                        )}
                      </div>

                      <div className="w-full text-center">
                        <p className="line-clamp-2 text-xs font-medium text-ink-primary leading-tight">
                          {p.name}
                        </p>
                        <p className="mt-1 text-sm font-bold text-accent">
                          {formatPrice(p.price)}
                        </p>
                        {p.compare_at_price != null && Number(p.compare_at_price) > Number(p.price) && (
                          <p className="text-[11px] text-ink-tertiary line-through">
                            {formatPrice(p.compare_at_price)}
                          </p>
                        )}
                      </div>
                    </Link>

                    <button
                      type="button"
                      onClick={() => addToCart.mutate({ productId: p.id })}
                      disabled={isPendingThis}
                      aria-label={`Add ${p.name} to cart`}
                      aria-busy={isPendingThis || undefined}
                      className={cn(
                        buttonVariants({ variant: 'cart', size: 'sm' }),
                        'mt-2 w-full text-[11px]',
                      )}
                    >
                      {isPendingThis ? (
                        <>
                          <Loader2 className="size-3 animate-spin" aria-hidden="true" />
                          <span className="opacity-0 select-none" aria-hidden="true">
                            Add to Cart
                          </span>
                        </>
                      ) : (
                        'Add to Cart'
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </motion.div>
    </section>
  );
}
