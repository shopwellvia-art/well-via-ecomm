import { useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { cn } from '@/lib/utils';

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

              {/* Pill-shaped Cards Grid */}
              <div className="grid grid-cols-3 gap-x-4 gap-y-3.5">
                {SHOP_CATEGORIES.map((cat) => {
                  const active = location.pathname === `/products/${cat.slug}`;
                  return (
                    <Link
                      key={cat.slug}
                      to={`/products/${cat.slug}`}
                      onClick={() => setOpen(false)}
                      role="menuitem"
                      className={cn(
                        'flex items-center gap-3 rounded-[24px] border px-4 py-2.5 text-[15px] font-medium text-[#08112C] no-underline transition-all',
                        active
                          ? 'border-[#0a3925] bg-white ring-1 ring-[#0a3925]'
                          : 'border-gray-300 bg-white hover:border-[#0a3925] hover:shadow-sm'
                      )}
                    >
                      <img
                        src={cat.img}
                        alt={cat.label}
                        className="h-9 w-auto object-contain shrink-0"
                      />
                      <span className="truncate">{cat.label}</span>
                      {active && (
                        <span className="ml-auto size-2 shrink-0 rounded-full bg-[#be2254]" aria-hidden="true" />
                      )}
                    </Link>
                  );
                })}
              </div>
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
  return (
    <div className="ml-3 mb-2 mt-1 flex flex-col gap-1 border-l-2 border-wline pl-3">
      <Link
        to="/products"
        onClick={onNavigate}
        className="rounded-xl px-3 py-2 text-[13px] font-medium text-[#08112C] no-underline transition-colors hover:text-[#08112C] hover:bg-[#08112C]/5"
      >
        All Products →
      </Link>
      {SHOP_CATEGORIES.map((cat) => (
        <Link
          key={cat.slug}
          to={`/products/${cat.slug}`}
          onClick={onNavigate}
          className="flex items-center gap-2.5 rounded-xl px-3 py-2 text-[13.5px] text-[#08112C] no-underline transition-colors hover:text-[#08112C] hover:bg-[#08112C]/5"
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