import { useState, useEffect, useRef } from 'react';
import { Link, NavLink, useNavigate, useSearchParams } from 'react-router-dom';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { ShoppingCart, Menu, X, Sparkles, Search, Heart, Store } from 'lucide-react';
import { cn } from '@/lib/utils.js';
import { useAuthStore } from '@/features/auth/store.js';
import { useFooterConfig } from '@/features/footer/hooks.js';
import { FOOTER_DEFAULTS } from '@/features/footer/defaults.js';
import AccountMenu from './AccountMenu.jsx';
import { useCart } from '@/features/cart/hooks.js';
import { useWishlist } from '@/features/wishlist/hooks.js';

const LINKS = [
  { to: '/', label: 'Home', end: true },
  { to: '/products', label: 'Shop' },
  { to: '/wishlist', label: 'Wishlist' },
  { to: '/orders', label: 'Orders' },
];

/** Small count badge that sits on the cart / wishlist icons. */
function CountBadge({ count }) {
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
        className="pointer-events-none absolute -right-2 -top-1.5 flex h-4 min-w-[1rem] items-center justify-center rounded-full bg-accent px-[3px] text-[9px] font-bold leading-none text-white"
        aria-hidden="true"
      >
        {count > 99 ? '99+' : count}
      </motion.span>
    </AnimatePresence>
  );
}

/** Search box — submits to /products?q=<term>. Input + attached blue button. */
function SearchBox({ className, autoFocus = false }) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [term, setTerm] = useState(params.get('q') ?? '');

  function onSubmit(e) {
    e.preventDefault();
    const q = term.trim();
    navigate(q ? `/products?q=${encodeURIComponent(q)}` : '/products');
  }

  return (
    <form onSubmit={onSubmit} role="search" className={className}>
      <div className="flex h-10 overflow-hidden rounded-sm border border-line-strong bg-bg-elevated transition-colors focus-within:border-accent">
        <label htmlFor="site-search" className="sr-only">
          Search for products, brands and more
        </label>
        <input
          id="site-search"
          type="search"
          value={term}
          autoFocus={autoFocus}
          onChange={(e) => setTerm(e.target.value)}
          placeholder="Search for products, brands and more"
          className="min-w-0 flex-1 bg-transparent px-4 text-sm text-ink-primary placeholder:text-ink-tertiary focus:outline-none"
        />
        <button
          type="submit"
          aria-label="Search"
          className="grid w-12 shrink-0 place-items-center bg-accent text-white transition-colors hover:bg-accent-hover"
        >
          <Search className="size-[18px]" aria-hidden="true" />
        </button>
      </div>
    </form>
  );
}

/** A right-side header action: icon (with optional badge) above/with a label. */
function HeaderAction({ to, icon: Icon, label, count }) {
  const ariaLabel = count > 0 ? `${label} (${count})` : label;
  return (
    <Link
      to={to}
      aria-label={ariaLabel}
      className="flex min-h-[44px] min-w-[44px] items-center justify-center gap-2 rounded-sm px-2 py-1.5 text-sm font-medium text-ink-secondary transition-colors hover:text-accent focus-visible:focus-ring"
    >
      <span className="relative">
        <Icon className="size-[22px]" aria-hidden="true" />
        <CountBadge count={count} />
      </span>
      <span className="hidden lg:inline" aria-hidden="true">{label}</span>
    </Link>
  );
}

export default function Navbar() {
  const [open, setOpen] = useState(false);
  const drawerRef = useRef(null);
  const hamburgerRef = useRef(null);

  // Focus trap for the mobile navigation drawer
  useEffect(() => {
    if (!open) return undefined;

    const drawer = drawerRef.current;
    if (!drawer) return undefined;

    const focusableSelectors = [
      'a[href]',
      'button:not([disabled])',
      'input:not([disabled])',
      '[tabindex]:not([tabindex="-1"])',
    ].join(',');

    const focusableEls = Array.from(drawer.querySelectorAll(focusableSelectors));
    const first = focusableEls[0];
    const last = focusableEls[focusableEls.length - 1];

    // Move focus into the drawer on open
    first?.focus();

    function onKeyDown(e) {
      if (e.key === 'Escape') {
        setOpen(false);
        hamburgerRef.current?.focus();
        return;
      }
      if (e.key === 'Tab') {
        if (focusableEls.length === 0) {
          e.preventDefault();
          return;
        }
        if (e.shiftKey) {
          if (document.activeElement === first) {
            e.preventDefault();
            last?.focus();
          }
        } else {
          if (document.activeElement === last) {
            e.preventDefault();
            first?.focus();
          }
        }
      }
    }

    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [open]);

  const user = useAuthStore((s) => s.user);
  const { data: footer } = useFooterConfig();
  const brand = { ...FOOTER_DEFAULTS.brand, ...footer?.brand };

  const { data: cartData } = useCart();
  const cartCount = cartData?.items?.reduce((n, i) => n + (i.quantity ?? 1), 0) ?? 0;

  const { data: wishlistData } = useWishlist();
  const wishlistCount = wishlistData?.items?.length ?? wishlistData?.length ?? 0;

  const isStaff = !!user?.is_admin || (Array.isArray(user?.roles) && user.roles.length > 0);
  const mobileLinks = isStaff ? [...LINKS, { to: '/admin', label: 'Admin' }] : LINKS;

  return (
    <header className="fixed inset-x-0 top-0 z-50 border-b border-line-subtle bg-bg-elevated shadow-sm">
      <nav aria-label="Primary navigation" className="mx-auto flex h-16 max-w-content items-center gap-3 px-3 sm:gap-6 sm:px-6">
        {/* ── Mobile hamburger ─────────────────────────────────────────── */}
        <button
          ref={hamburgerRef}
          type="button"
          aria-label={open ? 'Close menu' : 'Open menu'}
          aria-expanded={open}
          aria-controls="mobile-nav-dialog"
          onClick={() => setOpen((v) => !v)}
          className="grid size-9 shrink-0 place-items-center rounded-sm text-ink-secondary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring md:hidden"
        >
          {open ? <X className="size-5" /> : <Menu className="size-5" />}
        </button>

        {/* ── Brand / logo ─────────────────────────────────────────────── */}
        <Link to="/" className="flex shrink-0 items-center gap-2 rounded-sm focus-visible:focus-ring">
          {brand.logo_url ? (
            <img
              src={brand.logo_url}
              alt={brand.name || 'Lumen'}
              decoding="async"
              className="h-8 w-auto max-w-[150px] object-contain"
            />
          ) : (
            <>
              <span className="grid size-9 place-items-center rounded-sm bg-accent text-white">
                <Sparkles className="size-5" aria-hidden="true" />
              </span>
              <span className="text-lg font-bold tracking-tight text-ink-primary">
                {brand.name || 'Lumen'}
              </span>
            </>
          )}
        </Link>

        {/* ── Desktop search ───────────────────────────────────────────── */}
        <SearchBox className="hidden min-w-0 max-w-[640px] flex-1 md:block" />

        {/* ── Right-side actions ───────────────────────────────────────── */}
        <div className="ml-auto flex items-center gap-1 sm:gap-3">
          <AccountMenu />
          <HeaderAction to="/wishlist" icon={Heart} label="Wishlist" count={wishlistCount} />
          <HeaderAction to="/cart" icon={ShoppingCart} label="Cart" count={cartCount} />
        </div>
      </nav>

      {/* ── Mobile search row ──────────────────────────────────────────── */}
      <div className="border-t border-line-subtle px-3 py-2.5 md:hidden">
        <SearchBox />
      </div>

      {/* ── Mobile drawer ──────────────────────────────────────────────── */}
      <AnimatePresence>
        {open && (
          <motion.div
            ref={drawerRef}
            id="mobile-nav-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="mobile-nav-title"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
            className="overflow-hidden border-t border-line-subtle bg-bg-elevated md:hidden"
          >
            <h2 id="mobile-nav-title" className="sr-only">
              Mobile navigation
            </h2>
            <ul className="flex flex-col gap-0.5 px-3 py-3">
              {mobileLinks.map((l) => (
                <li key={l.to}>
                  <NavLink
                    to={l.to}
                    end={l.end}
                    onClick={() => setOpen(false)}
                    className={({ isActive }) =>
                      cn(
                        'flex items-center gap-2.5 rounded-sm px-3 py-2.5 text-base font-medium transition-colors focus-visible:focus-ring',
                        isActive
                          ? 'bg-accent/10 text-accent'
                          : 'text-ink-secondary hover:bg-fill hover:text-ink-primary',
                      )
                    }
                  >
                    {l.label === 'Shop' && <Store className="size-4" aria-hidden="true" />}
                    {l.label}
                  </NavLink>
                </li>
              ))}

              {!user && (
                <li className="mt-1">
                  <NavLink
                    to="/login"
                    onClick={() => setOpen(false)}
                    className="block rounded-sm bg-accent px-3 py-2.5 text-center text-base font-semibold text-white focus-visible:focus-ring"
                  >
                    Login
                  </NavLink>
                </li>
              )}
            </ul>
          </motion.div>
        )}
      </AnimatePresence>
    </header>
  );
}
