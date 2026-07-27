import { useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { cn } from '@/lib/utils';

/**
 * Shop categories shown in the mega menu (desktop dropdown) and the mobile
 * accordion list. Swap `color` for real thumbnail images when available —
 * for now each category gets a small color swatch instead of a photo.
 */
export const SHOP_CATEGORIES = [
  { slug: 'ashwa-ease-gummies', label: 'Ashwa-Ease Gummies', img:'ashwa-gummies.png' },
  { slug: 'beauty-boost-gummies', label: 'Beauty Boost Gummies', img:'beauty-gummies.png' },
  { slug: 'core-omega-gummies', label: 'Core Omega Gummies', img:'omega-gummies.png' },
  { slug: 'sleep-gummies', label: 'Sleep Gummies', img:'sleep-gummies.png' },
  { slug: 'her-wellness-gummies', label: 'Her-Wellness Gummies', img:'her-gummies.png' },
  { slug: 'multivitamin-gummies', label: 'Multivitamin Gummies', img:'multi-gummies.png' },
  { slug: 'immunity-gummies', label: 'Immunity Gummies', img:'immunity-gummies.png' },
  { slug: 'meta-gut-gummies', label: 'Meta-Gut Gummies', img:'gut-gummies.png' },
];

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

  const isShopSection = location.pathname.startsWith('/products');
  const triggerCls = linkClassName ? linkClassName({ isActive: isShopSection }) : '';

  // Click-outside + Escape to close.
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

  // Close on route change (e.g. after clicking a category).
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
            initial={reduce ? { opacity: 0 } : { opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduce ? { opacity: 0 } : { opacity: 0, y: -8 }}
            transition={{ duration: 0.16, ease: [0.22, 1, 0.36, 1] }}
            className="absolute left-1/2 top-full mt-4 w-[560px] -translate-x-1/2 rounded-2xl border border-wline bg-white p-5 shadow-xl z-[60]"
          >
            <Link
              to="/products"
              onClick={() => setOpen(false)}
              role="menuitem"
              className="mb-4 inline-flex items-center gap-1.5 text-[12px] font-semibold uppercase tracking-wide text-wink no-underline hover:text-wgreen"
            >
              All Products
              <span aria-hidden="true">→</span>
              <span className="size-1.5 rounded-full bg-[#c0392b]" aria-hidden="true" />
            </Link>

            <div className="grid grid-cols-3 gap-x-3 gap-y-3">
              {SHOP_CATEGORIES.map((cat) => {
                const active = location.pathname === `/products/${cat.slug}`;
                return (
                  <Link
                    key={cat.slug}
                    to={`/products/${cat.slug}`}
                    onClick={() => setOpen(false)}
                    role="menuitem"
                    className={cn(
                      'flex items-center gap-2.5 rounded-full border px-3 py-2.5 text-[13px] text-wink no-underline transition-colors',
                      active
                        ? 'border-[#c0392b] bg-[#c0392b]/5'
                        : 'border-wline hover:border-wgreen/50 hover:bg-wline/20',
                    )}
                  >
                    <img
  src={cat.img}
  alt={cat.label}
  className="size-10 shrink-0 rounded-md object-contain"
/>
                    <span className="truncate">{cat.label}</span>
                    {active && (
                      <span className="ml-auto size-1.5 shrink-0 rounded-full bg-[#c0392b]" aria-hidden="true" />
                    )}
                  </Link>
                );
              })}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/**
 * Small chevron used as the accordion-toggle indicator on the mobile
 * "Shop" row (rotates via a className the caller passes in).
 */
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

/**
 * Mobile version — a plain vertical column of category links rendered
 * under the "Shop" row in the mobile slide-in menu (shown inside the
 * accordion panel when the Shop row is expanded).
 */
export function ShopMobileLinks({ onNavigate }) {
  return (
    <div className="ml-3 mb-2 mt-1 flex flex-col gap-1 border-l-2 border-wline pl-3">
      <Link
        to="/products"
        onClick={onNavigate}
        className="rounded-xl px-3 py-2 text-[13px] font-medium text-wgreen no-underline transition-colors hover:bg-wline/40"
      >
        All Products →
      </Link>
      {SHOP_CATEGORIES.map((cat) => (
        <Link
          key={cat.slug}
          to={`/products/${cat.slug}`}
          onClick={onNavigate}
          className="flex items-center gap-2.5 rounded-xl px-3 py-2 text-[13.5px] text-wink no-underline transition-colors hover:bg-wline/40"
        >
          <img
  src={cat.img}
  alt={cat.label}
  className="size-8 shrink-0 rounded-md object-contain"
/>
          {cat.label}
        </Link>
      ))}
    </div>
  );
}