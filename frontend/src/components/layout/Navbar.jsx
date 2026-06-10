import { useEffect, useRef, useState } from 'react';
import { Link, NavLink } from 'react-router-dom';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { ShoppingBag, Menu, X, Sparkles, Search } from 'lucide-react';
import { cn } from '@/lib/utils.js';
import { useAuthStore } from '@/features/auth/store.js';
import { useFooterConfig } from '@/features/footer/hooks.js';
import { FOOTER_DEFAULTS } from '@/features/footer/defaults.js';
import { ThemeToggle } from '@/components/ui/ThemeToggle.jsx';
import AccountMenu from './AccountMenu.jsx';
import { useCart } from '@/features/cart/hooks.js';

const LINKS = [
  { to: '/', label: 'Home', end: true },
  { to: '/products', label: 'Shop' },
];

/** Animated cart badge — only visible when count > 0. */
function CartBadge({ count }) {
  const reduce = useReducedMotion();
  if (!count || count < 1) return null;
  return (
    <AnimatePresence>
      <motion.span
        key={count}
        initial={reduce ? false : { scale: 0.6, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.6, opacity: 0 }}
        transition={{ duration: 0.18, ease: [0.34, 1.56, 0.64, 1] }}
        className="pointer-events-none absolute -right-0.5 -top-0.5 flex h-4 min-w-[1rem] items-center justify-center rounded-full bg-accent px-[3px] text-[9px] font-bold leading-none text-ink-inverse"
        aria-hidden="true"
      >
        {count > 99 ? '99+' : count}
      </motion.span>
    </AnimatePresence>
  );
}

export default function Navbar() {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const searchRef = useRef(null);
  const reduce = useReducedMotion();

  const user = useAuthStore((s) => s.user);
  const { data: footer } = useFooterConfig();
  const brand = { ...FOOTER_DEFAULTS.brand, ...footer?.brand };

  // Cart count — derived from React Query cart data; 0 when signed-out or empty.
  const { data: cartData } = useCart();
  const cartCount = cartData?.items?.reduce((n, i) => n + (i.quantity ?? 1), 0) ?? 0;

  const links = user?.is_admin ? [...LINKS, { to: '/admin', label: 'Admin' }] : LINKS;

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 48);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  // Close search on Escape or outside click.
  useEffect(() => {
    if (!searchOpen) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') {
        setSearchOpen(false);
      }
    };
    const onPointer = (e) => {
      if (searchRef.current && !searchRef.current.contains(e.target)) {
        setSearchOpen(false);
      }
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onPointer);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onPointer);
    };
  }, [searchOpen]);

  // Focus search input when it opens.
  useEffect(() => {
    if (searchOpen) {
      const el = searchRef.current?.querySelector('input');
      el?.focus();
    }
  }, [searchOpen]);

  return (
    <header
      className={cn(
        'fixed inset-x-0 top-0 z-50 transition-[background-color,border-color,backdrop-filter,box-shadow] duration-300',
        scrolled
          ? 'glass border-b border-line-subtle shadow-sm'
          : 'border-b border-transparent bg-transparent',
      )}
    >
      <nav className="mx-auto flex h-16 max-w-content items-center gap-4 px-4 sm:px-6">
        {/* ── Brand / logo ─────────────────────────────────────────────── */}
        <Link
          to="/"
          className="flex shrink-0 items-center gap-2.5 rounded-sm text-ink-primary focus-visible:focus-ring"
        >
          {brand.logo_url ? (
            <img
              src={brand.logo_url}
              alt={brand.name || 'Lumen'}
              className="h-8 w-auto max-w-[160px] object-contain"
            />
          ) : (
            <>
              <span className="grid size-8 place-items-center rounded-sm bg-accent text-ink-inverse shadow-glow-sm">
                <Sparkles className="size-4" aria-hidden="true" />
              </span>
              <span className="text-h3 tracking-tight">{brand.name || 'Lumen'}</span>
            </>
          )}
        </Link>

        {/* ── Desktop nav links ────────────────────────────────────────── */}
        <ul className="ml-2 hidden items-center gap-0.5 md:flex">
          {links.map((l) => (
            <li key={l.to}>
              <NavLink
                to={l.to}
                end={l.end}
                className="relative rounded-sm px-3.5 py-2 text-sm font-medium transition-colors duration-150 focus-visible:focus-ring"
              >
                {({ isActive }) => (
                  <>
                    <span className={cn(isActive ? 'text-ink-primary' : 'text-ink-secondary')}>
                      {l.label}
                    </span>
                    {isActive && (
                      <motion.span
                        layoutId="nav-active-pill"
                        className="absolute inset-0 rounded-sm bg-fill"
                        style={{ zIndex: -1 }}
                        transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
                      />
                    )}
                  </>
                )}
              </NavLink>
            </li>
          ))}
        </ul>

        {/* ── Right-side actions ───────────────────────────────────────── */}
        <div className="ml-auto flex items-center gap-1 sm:gap-1.5">
          {/* Search affordance */}
          <div ref={searchRef} className="relative hidden sm:block">
            <button
              type="button"
              aria-label="Search"
              aria-expanded={searchOpen}
              onClick={() => setSearchOpen((v) => !v)}
              className="grid size-10 place-items-center rounded-sm text-ink-secondary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
            >
              <Search className="size-[18px]" aria-hidden="true" />
            </button>

            <AnimatePresence>
              {searchOpen && (
                <motion.div
                  initial={reduce ? { opacity: 0 } : { opacity: 0, y: -4, scale: 0.97 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={reduce ? { opacity: 0 } : { opacity: 0, y: -4, scale: 0.97 }}
                  transition={{ duration: 0.16, ease: [0.22, 1, 0.36, 1] }}
                  className="absolute right-0 top-full mt-1.5 w-72 origin-top-right"
                >
                  <div className="glass rounded-md border border-line-subtle p-2 shadow-md">
                    <label htmlFor="navbar-search" className="sr-only">
                      Search products
                    </label>
                    <div className="flex items-center gap-2 rounded-xs border border-line-subtle bg-bg-sunken px-3 py-2">
                      <Search className="size-4 shrink-0 text-ink-tertiary" aria-hidden="true" />
                      <input
                        id="navbar-search"
                        type="search"
                        placeholder="Search products…"
                        className="w-full bg-transparent text-sm text-ink-primary placeholder:text-ink-tertiary focus:outline-none"
                      />
                    </div>
                    <p className="mt-1.5 px-1 text-xs text-ink-tertiary">
                      Press Enter to search all products.
                    </p>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          {/* Theme toggle — desktop only in bar */}
          <span className="hidden sm:inline-flex">
            <ThemeToggle />
          </span>

          {/* Cart */}
          <Link
            to="/cart"
            aria-label={cartCount > 0 ? `Cart, ${cartCount} item${cartCount !== 1 ? 's' : ''}` : 'Cart'}
            className="relative grid size-10 place-items-center rounded-sm text-ink-secondary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
          >
            <ShoppingBag className="size-[18px]" aria-hidden="true" />
            <CartBadge count={cartCount} />
          </Link>

          {/* Account */}
          <AccountMenu />

          {/* Mobile hamburger */}
          <button
            type="button"
            aria-label={open ? 'Close menu' : 'Open menu'}
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            className="grid size-10 place-items-center rounded-sm text-ink-secondary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring md:hidden"
          >
            <AnimatePresence mode="wait" initial={false}>
              {open ? (
                <motion.span
                  key="close"
                  initial={reduce ? false : { rotate: -45, opacity: 0 }}
                  animate={{ rotate: 0, opacity: 1 }}
                  exit={reduce ? { opacity: 0 } : { rotate: 45, opacity: 0 }}
                  transition={{ duration: 0.14 }}
                >
                  <X className="size-5" />
                </motion.span>
              ) : (
                <motion.span
                  key="menu"
                  initial={reduce ? false : { rotate: 45, opacity: 0 }}
                  animate={{ rotate: 0, opacity: 1 }}
                  exit={reduce ? { opacity: 0 } : { rotate: -45, opacity: 0 }}
                  transition={{ duration: 0.14 }}
                >
                  <Menu className="size-5" />
                </motion.span>
              )}
            </AnimatePresence>
          </button>
        </div>
      </nav>

      {/* ── Mobile drawer ──────────────────────────────────────────────── */}
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
            className="overflow-hidden border-t border-line-subtle glass md:hidden"
          >
            {/* Mobile search */}
            <div className="px-4 pt-4">
              <label htmlFor="mobile-search" className="sr-only">
                Search products
              </label>
              <div className="flex items-center gap-2 rounded-xs border border-line-subtle bg-bg-sunken px-3 py-2.5">
                <Search className="size-4 shrink-0 text-ink-tertiary" aria-hidden="true" />
                <input
                  id="mobile-search"
                  type="search"
                  placeholder="Search products…"
                  className="w-full bg-transparent text-sm text-ink-primary placeholder:text-ink-tertiary focus:outline-none"
                />
              </div>
            </div>

            <ul className="flex flex-col gap-0.5 px-4 py-3">
              {[...links, { to: '/cart', label: 'Cart' }].map((l) => (
                <li key={l.to}>
                  <NavLink
                    to={l.to}
                    end={l.end}
                    onClick={() => setOpen(false)}
                    className={({ isActive }) =>
                      cn(
                        'block rounded-sm px-3 py-2.5 text-base font-medium transition-colors focus-visible:focus-ring',
                        isActive
                          ? 'bg-fill text-ink-primary'
                          : 'text-ink-secondary hover:bg-fill hover:text-ink-primary',
                      )
                    }
                  >
                    {l.label}
                  </NavLink>
                </li>
              ))}

              {!user && (
                <li className="mt-1">
                  <NavLink
                    to="/login"
                    onClick={() => setOpen(false)}
                    className="block rounded-sm px-3 py-2.5 text-base font-medium text-accent transition-colors hover:bg-accent-soft focus-visible:focus-ring"
                  >
                    Sign in
                  </NavLink>
                </li>
              )}

              <li className="mt-2 flex items-center justify-between rounded-sm border border-line-subtle bg-bg-sunken px-3 py-2.5">
                <span className="text-sm font-medium text-ink-secondary">Theme</span>
                <ThemeToggle />
              </li>
            </ul>
          </motion.div>
        )}
      </AnimatePresence>
    </header>
  );
}
