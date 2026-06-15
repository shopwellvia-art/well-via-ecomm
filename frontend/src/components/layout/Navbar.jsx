import { useState, useEffect, useRef } from 'react';
import { Link, NavLink, useNavigate, useSearchParams } from 'react-router-dom';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import {
  ShoppingCart,
  Menu,
  X,
  Search,
  Heart,
  Store,
  UserRound,
  ChevronDown,
  Star,
} from 'lucide-react';
import { cn } from '@/lib/utils.js';
import { useAuthStore } from '@/features/auth/store.js';
import { useFooterConfig } from '@/features/footer/hooks.js';
import { FOOTER_DEFAULTS } from '@/features/footer/defaults.js';
import { useCategories } from '@/features/categories/hooks.js';
import AccountMenu from './AccountMenu.jsx';
import CategoryNav from './CategoryNav.jsx';
import { useCart } from '@/features/cart/hooks.js';
import { useWishlist } from '@/features/wishlist/hooks.js';

const LINKS = [
  { to: '/', label: 'Home', end: true },
  { to: '/products', label: 'Shop' },
  { to: '/wishlist', label: 'Wishlist' },
  { to: '/orders', label: 'Orders' },
  { to: '/cart', label: 'Cart' },
];

/** Small orange count badge sitting on the cart / wishlist icons. */
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
        className="pointer-events-none absolute -right-2 -top-1.5 flex h-4 min-w-[1rem] items-center justify-center rounded-full bg-cta px-[3px] text-[9px] font-bold leading-none text-white"
        aria-hidden="true"
      >
        {count > 99 ? '99+' : count}
      </motion.span>
    </AnimatePresence>
  );
}

/** White search field for the blue chrome — submits to /products?q=<term>. */
function SearchBox({ className }) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [term, setTerm] = useState(params.get('q') ?? '');

  function onSubmit(e) {
    e.preventDefault();
    const q = term.trim();
    navigate(q ? `/products?q=${encodeURIComponent(q)}` : '/products');
  }

  return (
    <form onSubmit={onSubmit} role="search" className={cn('relative', className)}>
      <label htmlFor="site-search" className="sr-only">
        Search for products, brands and more
      </label>
      <input
        id="site-search"
        type="search"
        value={term}
        onChange={(e) => setTerm(e.target.value)}
        placeholder="Search for products, brands and more"
        className="h-9 w-full rounded-sm border-0 bg-white pl-3 pr-10 text-sm text-ink-primary shadow-sm outline-none placeholder:text-ink-tertiary focus-visible:ring-2 focus-visible:ring-white/70"
      />
      <button
        type="submit"
        aria-label="Search"
        className="absolute right-0 top-0 grid h-9 w-10 place-items-center text-accent"
      >
        <Search className="size-[18px]" aria-hidden="true" />
      </button>
    </form>
  );
}

/** A white header action (Wishlist / Cart) with an optional count badge. */
function HeaderAction({ to, icon: Icon, label, count }) {
  const ariaLabel = count > 0 ? `${label} (${count})` : label;
  return (
    <Link
      to={to}
      aria-label={ariaLabel}
      className="relative flex items-center gap-1.5 rounded-sm px-1.5 py-1.5 text-sm font-medium text-white transition-colors hover:text-white/90 focus-visible:focus-ring"
    >
      <span className="relative">
        <Icon className="size-[21px]" aria-hidden="true" />
        <CountBadge count={count} />
      </span>
      <span className="hidden sm:inline" aria-hidden="true">{label}</span>
    </Link>
  );
}

export default function Navbar() {
  const [open, setOpen] = useState(false);
  const drawerRef = useRef(null);
  const hamburgerRef = useRef(null);

  // Lock body scroll while the drawer is open.
  useEffect(() => {
    if (!open) return undefined;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  // Focus trap for the mobile navigation drawer.
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
        } else if (document.activeElement === last) {
          e.preventDefault();
          first?.focus();
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

  const { data: categories = [] } = useCategories();

  const isStaff = !!user?.is_admin || (Array.isArray(user?.roles) && user.roles.length > 0);
  const mobileLinks = isStaff ? [...LINKS, { to: '/admin', label: 'Admin' }] : LINKS;

  return (
    <header className="fixed inset-x-0 top-0 z-50">
      {/* ── Tier 1 — blue bar ─────────────────────────────────────────────── */}
      <div className="bg-accent shadow-sm">
        <nav
          aria-label="Primary navigation"
          className="mx-auto flex h-14 max-w-content items-center gap-2.5 px-3 sm:gap-5 sm:px-6"
        >
          {/* Mobile hamburger */}
          <button
            ref={hamburgerRef}
            type="button"
            aria-label={open ? 'Close menu' : 'Open menu'}
            aria-expanded={open}
            aria-controls="mobile-nav-dialog"
            onClick={() => setOpen((v) => !v)}
            className="grid size-9 shrink-0 place-items-center rounded-sm text-white transition-colors hover:bg-white/10 focus-visible:focus-ring md:hidden"
          >
            <Menu className="size-[22px]" aria-hidden="true" />
          </button>

          {/* Brand / logo */}
          <Link
            to="/"
            className="flex shrink-0 flex-col leading-none rounded-sm focus-visible:focus-ring"
          >
            {brand.logo_url ? (
              <img
                src={brand.logo_url}
                alt={brand.name || 'Store'}
                decoding="async"
                className="h-8 w-auto max-w-[150px] object-contain"
              />
            ) : (
              <>
                <span className="text-lg font-bold italic tracking-tight text-white sm:text-xl">
                  {brand.name || 'ShopWell'}
                </span>
                <span className="hidden items-center gap-1 text-[11px] italic text-white/85 sm:flex">
                  Explore <span className="font-semibold text-[#FFE11B]">Plus</span>
                  <Star className="size-2.5 fill-[#FFE11B] text-[#FFE11B]" aria-hidden="true" />
                </span>
              </>
            )}
          </Link>

          {/* Search */}
          <SearchBox className="min-w-0 flex-1 sm:max-w-[560px]" />

          {/* Login pill / account menu */}
          <AccountMenu onAccent />

          {/* Become a Seller — desktop only */}
          <Link
            to="/contact"
            className="hidden whitespace-nowrap rounded-sm px-2 py-1.5 text-sm font-medium text-white/95 transition-colors hover:text-white lg:inline-block"
          >
            Become a Seller
          </Link>

          {/* Right-side actions */}
          <div className="ml-1 flex items-center gap-1 sm:gap-2">
            <HeaderAction to="/wishlist" icon={Heart} label="Wishlist" count={wishlistCount} />
            <HeaderAction to="/cart" icon={ShoppingCart} label="Cart" count={cartCount} />
          </div>
        </nav>
      </div>

      {/* ── Tier 2 — category nav ─────────────────────────────────────────── */}
      <CategoryNav />

      {/* ── Mobile drawer (left slide-in) ─────────────────────────────────── */}
      <AnimatePresence>
        {open && (
          <div className="fixed inset-0 z-[80] md:hidden">
            {/* Backdrop */}
            <motion.button
              type="button"
              aria-label="Close menu"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              onClick={() => setOpen(false)}
              className="absolute inset-0 cursor-default bg-black/45"
            />

            {/* Panel */}
            <motion.div
              ref={drawerRef}
              id="mobile-nav-dialog"
              role="dialog"
              aria-modal="true"
              aria-labelledby="mobile-nav-title"
              initial={{ x: '-100%' }}
              animate={{ x: 0 }}
              exit={{ x: '-100%' }}
              transition={{ duration: 0.26, ease: [0.22, 1, 0.36, 1] }}
              className="absolute inset-y-0 left-0 flex w-[84%] max-w-xs flex-col bg-bg-elevated shadow-lg"
            >
              {/* Blue drawer header */}
              <div className="flex h-14 items-center justify-between bg-accent px-4">
                <span id="mobile-nav-title" className="text-lg font-bold italic text-white">
                  {brand.name || 'ShopWell'}
                </span>
                <button
                  type="button"
                  aria-label="Close menu"
                  onClick={() => setOpen(false)}
                  className="grid size-9 place-items-center rounded-sm text-white hover:bg-white/10 focus-visible:focus-ring"
                >
                  <X className="size-[22px]" aria-hidden="true" />
                </button>
              </div>

              <div className="flex-1 overflow-y-auto">
                {/* Login / account row */}
                <Link
                  to={user ? '/orders' : '/login'}
                  onClick={() => setOpen(false)}
                  className="flex items-center gap-2.5 border-b border-line-subtle px-4 py-4 text-sm font-semibold text-accent focus-visible:focus-ring"
                >
                  <UserRound className="size-[18px]" aria-hidden="true" />
                  {user ? (user.name || user.email?.split('@')[0] || 'My account') : 'Login / Sign up'}
                </Link>

                {/* Primary nav */}
                <nav className="border-b border-line-subtle py-1">
                  {mobileLinks.map((l) => (
                    <NavLink
                      key={l.to}
                      to={l.to}
                      end={l.end}
                      onClick={() => setOpen(false)}
                      className={({ isActive }) =>
                        cn(
                          'flex items-center gap-2.5 px-4 py-2.5 text-sm font-medium transition-colors focus-visible:focus-ring',
                          isActive
                            ? 'bg-accent/10 text-accent'
                            : 'text-ink-primary hover:bg-fill',
                        )
                      }
                    >
                      {l.label === 'Shop' && <Store className="size-4" aria-hidden="true" />}
                      {l.label}
                    </NavLink>
                  ))}
                </nav>

                {/* Shop by category */}
                {categories.length > 0 && (
                  <>
                    <p className="px-4 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-ink-tertiary">
                      Shop by category
                    </p>
                    <nav className="pb-4">
                      {categories.map((c) => (
                        <Link
                          key={c.id}
                          to={`/products?category=${encodeURIComponent(c.slug)}`}
                          onClick={() => setOpen(false)}
                          className="flex items-center gap-3 px-4 py-2.5 text-sm text-ink-secondary transition-colors hover:bg-fill focus-visible:focus-ring"
                        >
                          {c.name}
                        </Link>
                      ))}
                    </nav>
                  </>
                )}
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </header>
  );
}
