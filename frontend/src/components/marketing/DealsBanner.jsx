import { Link } from 'react-router-dom';
import { motion, useReducedMotion } from 'framer-motion';
import { ArrowRight, Flame, Clock } from 'lucide-react';
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
export default function DealsBanner() {
  const reduce = useReducedMotion();
  const target = useSaleTarget();
  const { data: productsPage, isLoading } = useProducts({ page: 1, page_size: 6 });
  const addToCart = useAddToCart();
  const products = productsPage?.items ?? [];

  return (
    <section className="mx-auto mt-3 max-w-content px-4 sm:px-6">
      <motion.div
        initial={reduce ? false : { opacity: 0, y: 16 }}
        whileInView={reduce ? undefined : { opacity: 1, y: 0 }}
        viewport={{ once: true, amount: 0.2 }}
        transition={{ duration: 0.4 }}
        className="overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated shadow-sm"
      >
        {/* Blue header bar */}
        <div className="flex flex-col gap-3 bg-accent px-5 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-2">
            <Flame className="size-5 text-white" aria-hidden="true" />
            <h2 className="text-base font-bold text-white">Deals of the Day</h2>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-4">
            <div className="flex items-center gap-2">
              <Clock className="size-4 text-white/80" aria-hidden="true" />
              <span className="text-xs font-medium text-white/80 uppercase tracking-wide">Ends in</span>
              <SaleCountdown target={target} tone="light" />
            </div>
            <Link
              to="/products"
              className="inline-flex items-center gap-1 rounded-xs border border-white/40 px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-white/15 focus-visible:focus-ring"
            >
              View All ›
            </Link>
          </div>
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
              {products.map((p, idx) => (
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
                      {p.image_url ? (
                        <img
                          src={p.image_url}
                          alt={p.name}
                          loading={idx < 3 ? 'eager' : 'lazy'}
                          className="size-full object-contain p-2 transition-transform duration-200 hover:scale-105"
                        />
                      ) : (
                        <span className="text-xs text-ink-tertiary">No image</span>
                      )}
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
                    className={cn(
                      buttonVariants({ variant: 'cart', size: 'sm' }),
                      'mt-2 w-full text-[11px]',
                    )}
                  >
                    Add to Cart
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </motion.div>
    </section>
  );
}
