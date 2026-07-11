import { useEffect, useRef, useState } from 'react';
import { Link, NavLink } from 'react-router-dom';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { useCategories } from '@/features/categories/hooks.js';
import { cn } from '@/lib/utils.js';

/**
 * "Shop" nav item with a hover/focus mega-menu of category pill links.
 *
 * The trigger itself is a real NavLink to /products, so click/Enter still
 * navigates; the panel is progressive enhancement. Closes on Esc, on route
 * change (parent Header remounts links), and when focus/hover leaves.
 */
/**
 * Mobile drawer variant — collapsible category list under the Shop link.
 * Rendered inside Header's mobile menu; `onNavigate` closes the drawer.
 */
export function ShopMobileLinks({ onNavigate }) {
  const [expanded, setExpanded] = useState(false);
  const { data: categories = [] } = useCategories();
  if (!categories.length) return null;

  return (
    <div className="pl-3">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="w-full text-left bg-transparent border-0 cursor-pointer px-3 py-2 text-[12.5px] uppercase tracking-[0.12em] text-wmuted hover:text-wink transition-colors"
      >
        {expanded ? '− Hide categories' : '+ Browse categories'}
      </button>
      {expanded && (
        <div className="flex flex-col gap-0.5 pb-1">
          {categories.map((c) => (
            <Link
              key={c.id}
              to={`/products?category_ids=${c.id}`}
              onClick={onNavigate}
              className="px-3 py-2 rounded-xl text-[13.5px] text-wmuted hover:bg-wline/40 hover:text-wink no-underline transition-colors truncate"
            >
              {c.name}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

export default function ShopMegaMenu({ linkClassName }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const closeTimer = useRef(null);
  const reduce = useReducedMotion();

  const { data: categories = [] } = useCategories();

  const scheduleClose = () => {
    clearTimeout(closeTimer.current);
    closeTimer.current = setTimeout(() => setOpen(false), 120);
  };
  const cancelClose = () => clearTimeout(closeTimer.current);

  useEffect(() => () => clearTimeout(closeTimer.current), []);

  // Esc + focus-leave close.
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false);
    };
    const onFocus = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('focusin', onFocus);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('focusin', onFocus);
    };
  }, [open]);

  return (
    <div
      ref={rootRef}
      className="relative"
      onMouseEnter={() => {
        cancelClose();
        setOpen(true);
      }}
      onMouseLeave={scheduleClose}
    >
      <NavLink
        to="/products"
        className={linkClassName}
        aria-haspopup="true"
        aria-expanded={open}
        onFocus={() => setOpen(true)}
      >
        Shop
        <svg
          width="11"
          height="11"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.4"
          strokeLinecap="round"
          className={cn('transition-transform', open && 'rotate-180')}
          aria-hidden="true"
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </NavLink>

      <AnimatePresence>
        {open && categories.length > 0 && (
          <motion.div
            initial={reduce ? { opacity: 0 } : { opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduce ? { opacity: 0 } : { opacity: 0, y: -6 }}
            transition={{ duration: 0.16, ease: [0.22, 1, 0.36, 1] }}
            className="absolute left-1/2 -translate-x-1/2 top-full pt-3 z-[60]"
            onMouseEnter={cancelClose}
            onMouseLeave={scheduleClose}
          >
            <div className="w-[560px] max-w-[80vw] rounded-xl2 border border-wline bg-wcard shadow-md p-4 normal-case tracking-normal">
              <p className="px-1 pb-2.5 text-[11px] uppercase tracking-[0.14em] text-wmuted">
                Shop by category →
              </p>
              <div className="grid grid-cols-2 lg:grid-cols-3 gap-2">
                {categories.map((c) => (
                  <Link
                    key={c.id}
                    to={`/products?category_ids=${c.id}`}
                    className={cn(
                      'flex items-center gap-2 rounded-full border border-wline bg-wpaper px-3 py-2',
                      'text-[13px] text-wink no-underline transition-colors',
                      'hover:border-wgreen/50 hover:text-wgreen',
                    )}
                  >
                    {c.image_url ? (
                      <img
                        src={c.image_url}
                        alt=""
                        loading="lazy"
                        className="size-6 shrink-0 rounded-full object-cover"
                      />
                    ) : (
                      <span className="size-6 shrink-0 rounded-full bg-wline/60" aria-hidden="true" />
                    )}
                    <span className="truncate">{c.name}</span>
                  </Link>
                ))}
              </div>
              <div className="mt-3 border-t border-wline pt-2.5 px-1">
                <Link
                  to="/products"
                  className="text-[12.5px] text-wgreen no-underline hover:underline"
                >
                  View all products →
                </Link>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
