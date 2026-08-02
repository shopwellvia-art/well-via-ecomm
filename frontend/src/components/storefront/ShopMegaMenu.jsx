import { useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { useShopMenuProducts } from '@/features/products/hooks.js';
import WImage from './WImage.jsx';
import { cn } from '@/lib/utils';

/** Fills the 3-column grid evenly; the menu renders fewer without complaint. */
const MENU_LIMIT = 9;

/**
 * One pill in the menu. Links to /products/:id — the PDP route takes a numeric
 * product id, so this must never be a slug.
 */
function ProductPill({ product, active, onNavigate, variant = 'desktop' }) {
  const desktop = variant === 'desktop';
  return (
    <Link
      to={`/products/${product.id}`}
      onClick={onNavigate}
      role="menuitem"
      aria-current={active ? 'page' : undefined}
      className={cn(
        'no-underline text-[#08112C] transition-all',
        desktop
          ? 'flex items-center gap-3 rounded-[24px] border px-4 py-2.5 text-[15px] font-medium'
          : 'flex items-center gap-2.5 rounded-xl px-3 py-2 text-[13.5px] hover:bg-[#08112C]/5',
        desktop &&
          (active
            ? 'border-[#0a3925] bg-white ring-1 ring-[#0a3925]'
            : 'border-gray-300 bg-white hover:border-[#0a3925] hover:shadow-sm'),
      )}
    >
      <WImage
        src={product.image_url}
        alt={product.name}
        shape="rounded"
        className={desktop ? 'h-9 w-9 shrink-0 object-contain' : 'size-8 shrink-0 object-contain'}
      />
      <span className="truncate">{product.name}</span>
      {desktop && active && (
        <span
          className="ml-auto size-2 shrink-0 rounded-full bg-[#be2254]"
          aria-hidden="true"
        />
      )}
    </Link>
  );
}

/** Placeholder pills so opening the menu doesn't reflow when data lands. */
function PillSkeleton() {
  return (
    <li
      className="flex items-center gap-3 rounded-[24px] border border-gray-200 px-4 py-2.5"
      aria-hidden="true"
    >
      <span className="h-9 w-9 shrink-0 animate-pulse rounded-xl2 bg-gray-100" />
      <span className="h-3 w-28 animate-pulse rounded bg-gray-100" />
    </li>
  );
}

/**
 * Desktop-only "Shop" nav item — opens a mega menu dropdown on click/hover
 * instead of navigating away immediately. "All Products →" inside the
 * panel is the actual link to the full catalog.
 */
export default function ShopMegaMenu({ linkClassName, label = 'Shop' }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const location = useLocation();
  const reduce = useReducedMotion();
  // Only fetched once the menu is opened; see useShopMenuProducts.
  const { data, isLoading, isError } = useShopMenuProducts(MENU_LIMIT, open);
  const products = data?.items ?? [];

  const isShopSection = location.pathname.startsWith('/products');
  const triggerCls = linkClassName ? linkClassName({ isActive: isShopSection }) : '';

  useEffect(() => {
    if (!open) return;
    const onPointer = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);

  return (
    <div
      ref={wrapRef}
      className="relative"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        className={cn(triggerCls, 'bg-transparent border-0 p-0 cursor-pointer font-[inherit]')}
      >
        {label}
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            role="menu"
            aria-label="Shop categories"
            initial={reduce ? { opacity: 0 } : { opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduce ? { opacity: 0 } : { opacity: 0, y: -6 }}
            transition={{ duration: 0.16, ease: [0.22, 1, 0.36, 1] }}
            className="absolute left-1/2 top-full pt-3 -translate-x-1/2 w-[880px] max-w-[95vw] z-[100]"
          >
            <div className="bg-white rounded-2xl border border-gray-200 p-6 shadow-2xl">
              {/* "ALL PRODUCTS ->" header */}
              {/* "ALL PRODUCTS ->" top left action inside ShopMegaMenu */}
<div className="mb-5 flex items-center gap-1.5 pl-1">
  <Link
    to="/products"
    onClick={() => setOpen(false)}
    role="menuitem"
    className="inline-flex items-center gap-1 text-[13px] font-semibold tracking-wider text-[#08112C] uppercase underline underline-offset-4 hover:text-[#08112C] transition-colors"
  >
    ALL PRODUCTS -&gt;
  </Link>
  <span className="size-2 rounded-full bg-[#be2254] inline-block ml-0.5" aria-hidden="true" />
</div>

              {/* Pill-shaped Cards Grid — live catalog, newest first */}
              {isLoading ? (
                <ul className="grid grid-cols-3 gap-x-4 gap-y-3.5 list-none m-0 p-0">
                  {Array.from({ length: 6 }, (_, i) => (
                    <PillSkeleton key={i} />
                  ))}
                </ul>
              ) : products.length > 0 ? (
                <div className="grid grid-cols-3 gap-x-4 gap-y-3.5">
                  {products.map((product) => (
                    <ProductPill
                      key={product.id}
                      product={product}
                      active={location.pathname === `/products/${product.id}`}
                      onNavigate={() => setOpen(false)}
                    />
                  ))}
                </div>
              ) : (
                /* Empty catalog, or the request failed — the "All Products"
                   link above still works, so the header never dead-ends. */
                <p className="m-0 px-1 text-[13.5px] text-wmuted">
                  {isError
                    ? 'Could not load products just now.'
                    : 'The catalog is being stocked. Check back shortly.'}
                </p>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function ChevronIcon({ size = 16, className, ...props }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
      {...props}
    >
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

export function ShopMobileLinks({ onNavigate }) {
  // Rendered only once the mobile "Shop" accordion is expanded, so fetching
  // here is already demand-driven; same query key as the desktop menu, so
  // whichever opens first warms the cache for the other.
  const location = useLocation();
  const { data, isLoading } = useShopMenuProducts(MENU_LIMIT, true);
  const products = data?.items ?? [];

  return (
    <div className="ml-3 mb-2 mt-1 flex flex-col gap-1 border-l-2 border-wline pl-3">
      <Link
        to="/products"
        onClick={onNavigate}
        className="rounded-xl px-3 py-2 text-[13px] font-medium text-[#08112C] no-underline transition-colors hover:text-[#08112C] hover:bg-[#08112C]/5"
      >
        All Products →
      </Link>
      {isLoading && (
        <span className="px-3 py-2 text-[13px] text-wmuted">Loading…</span>
      )}
      {products.map((product) => (
        <ProductPill
          key={product.id}
          product={product}
          active={location.pathname === `/products/${product.id}`}
          onNavigate={onNavigate}
          variant="mobile"
        />
      ))}
    </div>
  );
}