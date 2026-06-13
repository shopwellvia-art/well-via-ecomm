import { Link } from 'react-router-dom';
import { motion, useReducedMotion } from 'framer-motion';
import { TrendingUp } from 'lucide-react';
import { buttonVariants } from '@/components/ui/Button.jsx';
import { useBestsellers } from '@/features/products/hooks.js';
import { useAddToCart } from '@/features/cart/hooks.js';
import { formatPrice, cn } from '@/lib/utils.js';

/**
 * Bestsellers — white card, section header with "View all ›" link,
 * horizontal product rail. Blue prices, green % OFF badge, blue Add to Cart.
 * Matches ShopFlow h5-bestsellers.png.
 */
export default function BestsellersSection({ limit = 8 }) {
  const reduce = useReducedMotion();
  const { data: products = [], isLoading } = useBestsellers(limit);
  const addToCart = useAddToCart();

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
        {/* Section header */}
        <div className="flex items-center justify-between border-b border-line-subtle px-5 py-3">
          <div className="flex items-center gap-2">
            <TrendingUp className="size-4 text-accent" aria-hidden="true" />
            <h2 className="text-sm font-bold text-ink-primary">Bestsellers</h2>
            <span className="text-xs text-ink-secondary">Loved by customers</span>
          </div>
          <Link
            to="/products?sort=bestsellers"
            className="text-xs font-semibold text-accent transition-colors hover:text-accent-hover focus-visible:focus-ring"
          >
            View all ›
          </Link>
        </div>

        {/* Horizontal product rail */}
        <div className="overflow-x-auto pb-4 pt-3 [&::-webkit-scrollbar]:hidden">
          {isLoading ? (
            <div className="flex gap-px px-3">
              {Array.from({ length: limit }).map((_, i) => (
                <div
                  key={i}
                  className="flex h-[220px] w-40 shrink-0 animate-pulse flex-col gap-2 border-r border-line-subtle px-3 last:border-r-0"
                />
              ))}
            </div>
          ) : (
            <ul className="flex gap-px" role="list">
              {products.map((p, idx) => (
                <li
                  key={p.id}
                  className="flex w-44 shrink-0 flex-col items-center border-r border-line-subtle px-3 py-3 last:border-r-0 transition-colors hover:bg-bg-sunken"
                >
                  <Link
                    to={`/products/${p.id}`}
                    className="flex flex-col items-center gap-2 focus-visible:focus-ring"
                  >
                    <div className="relative flex size-28 items-center justify-center overflow-hidden rounded-xs bg-bg-sunken">
                      {p.image_url ? (
                        <img
                          src={p.image_url}
                          alt={p.name}
                          loading={idx < 4 ? 'eager' : 'lazy'}
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
