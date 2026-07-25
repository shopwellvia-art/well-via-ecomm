import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { useAuthStore } from '@/features/auth/store';
import { authApi } from '@/features/auth/api';
import { useCart } from '@/features/cart/hooks';
import { useCartDrawer } from '@/features/cart/drawerStore';
import { useWishlist } from '@/features/wishlist/hooks';
import Logo, { LeafMark } from './Logo';
import {
  SearchIcon,
  BagIcon,
  HeartIcon,
  UserIcon,
  MenuIcon,
  CloseIcon,
} from './Icons';
import ShopMegaMenu, { ShopMobileLinks, ChevronIcon } from './ShopMegaMenu.jsx';

/** Primary nav links — real app routes (mockup order). "Shop" is rendered
 *  via ShopMegaMenu on desktop and as an accordion toggle on mobile. */
const NAV = [
  { to: '/', label: 'Home', end: true },
  { to: '/products', label: 'Shop', megaMenu: true },
  { to: '/categories', label: 'Categories' },
  { to: '/bestsellers', label: 'Best Sellers' },
  { to: '/new-arrivals', label: 'New Arrivals' },
  { to: '/contact', label: 'Contact' },
  { to: '/about', label: 'About' },
];

/** Account dropdown items — signed-in users. */
const ACCOUNT_LINKS = [
  { to: '/orders', label: 'My Orders' },
  { to: '/wishlist', label: 'Wishlist' },
  { to: '/rewards', label: 'Rewards' },
  { to: '/account/security', label: 'Security & 2FA' },
  { to: '/account/addresses', label: 'Addresses' },
];

/**
 * Storefront header — sticky, wellness-styled.
 *
 * Desktop: 3-column grid — Logo (sm horizontal) | Logo (lg stacked) | Nav + icons
 * Mobile:  hamburger | Logo | wishlist + cart
 *
 * Wiring:
 *   - Cart count / openDrawer  → useCart() + useCartDrawer()
 *   - Wishlist count           → useWishlist()
 *   - Account dropdown         → useAuthStore() (full a11y ported from AccountMenu.jsx)
 *   - Search                   → navigate('/products?q=<term>')
 *   - Mobile "Shop" row        → accordion toggle (mobileShopOpen), does not navigate itself
 *
 * All mobile-drawer closing (X button, backdrop click, Esc, link clicks)
 * funnels through the single `closeMobileMenu` callback so `mobileOpen` is
 * only ever set from one place — this avoids the drawer getting "stuck"
 * from two competing state updates racing each other.
 */
export default function Header() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [mobileShopOpen, setMobileShopOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [accountOpen, setAccountOpen] = useState(false);

  // Refs for account menu keyboard accessibility (mirrors AccountMenu.jsx pattern).
  const accountRef = useRef(null);
  const accountTriggerRef = useRef(null);
  const accountMenuRef = useRef(null);

  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();
  const location = useLocation();
  const reduce = useReducedMotion();

  const isShopSection = location.pathname.startsWith('/products');

  // Cart count — sum of quantities across all lines.
  const { data: cartData } = useCart();
  const cartCount =
    cartData?.items?.reduce((sum, i) => sum + (i.quantity || 0), 0) ?? 0;

  // Wishlist badge count.
  const { data: wishlistData } = useWishlist();
  const wishlistCount = wishlistData?.length ?? 0;

  const { openDrawer } = useCartDrawer();

  // Derive isStaff synchronously from user (mirrors AccountMenu.jsx).
  const isStaff = user
    ? !!user.is_admin || (Array.isArray(user.roles) && user.roles.length > 0)
    : false;

  const userInitial = user
    ? (user.name || user.email || '?').charAt(0).toUpperCase()
    : '?';
  const displayName = user
    ? user.name || (user.email ? user.email.split('@')[0] : 'Account')
    : '';

  // Single source of truth for closing the mobile drawer — used by the X
  // button, the backdrop, Esc, every nav link, and the shop sub-links.
  // Nothing else is allowed to call setMobileOpen directly.
  const closeMobileMenu = useCallback(() => {
    setMobileOpen(false);
    setMobileShopOpen(false);
  }, []);

  // ── Close the account dropdown / inline search on route change ───────────────
  // (mobileOpen is intentionally NOT touched here — every link that can close
  // the drawer already calls closeMobileMenu() itself. Also closing it here,
  // right as the route changes, raced with Framer Motion's exit animation and
  // could leave the drawer "stuck" open with the X no longer responding.)
  useEffect(() => {
  closeMobileMenu();
  setAccountOpen(false);
  setSearchOpen(false);
}, [location.pathname, closeMobileMenu]);

  // ── Lock body scroll while mobile menu is open ───────────────────────────────
  useEffect(() => {
    document.body.style.overflow = mobileOpen ? 'hidden' : '';
    return () => {
      document.body.style.overflow = '';
    };
  }, [mobileOpen]);

  // Esc closes the mobile menu too.
  useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (e) => {
      if (e.key === 'Escape') closeMobileMenu();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [mobileOpen, closeMobileMenu]);

  // ── Account dropdown — focus management ──────────────────────────────────────
  const getMenuItems = useCallback(() => {
    if (!accountMenuRef.current) return [];
    return Array.from(
      accountMenuRef.current.querySelectorAll('[role="menuitem"]'),
    );
  }, []);

  // Focus first menuitem on open.
  useEffect(() => {
    if (!accountOpen) return;
    const id = setTimeout(() => {
      const items = getMenuItems();
      if (items.length) items[0].focus();
    }, 0);
    return () => clearTimeout(id);
  }, [accountOpen, getMenuItems]);

  // Esc + click-outside close.
  useEffect(() => {
    if (!accountOpen) return;
    const onPointer = (e) => {
      if (accountRef.current && !accountRef.current.contains(e.target)) {
        setAccountOpen(false);
      }
    };
    const onKey = (e) => {
      if (e.key === 'Escape') {
        setAccountOpen(false);
        accountTriggerRef.current?.focus();
      }
    };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [accountOpen]);

  // Arrow / Home / End / Tab keyboard navigation inside the menu.
  function handleMenuKeyDown(e) {
    const items = getMenuItems();
    if (!items.length) return;
    const focused = document.activeElement;
    const idx = items.indexOf(focused);
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      items[idx < items.length - 1 ? idx + 1 : 0].focus();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      items[idx > 0 ? idx - 1 : items.length - 1].focus();
    } else if (e.key === 'Home') {
      e.preventDefault();
      items[0].focus();
    } else if (e.key === 'End') {
      e.preventDefault();
      items[items.length - 1].focus();
    } else if (e.key === 'Tab') {
      setAccountOpen(false);
      accountTriggerRef.current?.focus();
      e.preventDefault();
    }
  }

  // ── Search submit ─────────────────────────────────────────────────────────────
  function handleSearch(e) {
    e.preventDefault();
    const q = searchQuery.trim();
    if (!q) return;
    navigate(`/products?q=${encodeURIComponent(q)}`);
    setSearchOpen(false);
    setSearchQuery('');
  }

  // ── Sign out ──────────────────────────────────────────────────────────────────
  async function handleSignOut() {
    setAccountOpen(false);
    closeMobileMenu();
    // Best-effort server-side revocation — failure still logs out locally.
    const rt = useAuthStore.getState().refreshToken;
    if (rt) authApi.logout(rt).catch(() => {});
    logout();
    navigate('/');
  }

  // Shared menuitem class for the account dropdown.
  const menuItemCls = cn(
    'flex items-center gap-2.5 rounded-sm px-3 py-2 text-sm text-wmuted no-underline',
    'transition-colors hover:bg-wline/40 hover:text-wink',
    'outline-none focus-visible:ring-2 focus-visible:ring-wgreen/40',
  );

  // ── Badge helper ──────────────────────────────────────────────────────────────
  function CountBadge({ count }) {
    if (!count) return null;
    return (
      <span className="absolute -top-1.5 -right-2 bg-[#c0392b] text-white text-[9px] min-w-[15px] h-[15px] rounded-full flex items-center justify-center px-0.5 leading-none font-bold">
        {count > 99 ? '99+' : count}
      </span>
    );
  }

  return (
    <header className="sticky top-0 z-40 bg-[#08112C]">
      {/* ── Desktop (frontend-3: dark-green band, left logo, serif nav) ───────── */}
      <div className="hidden md:flex items-center gap-4 lg:gap-7 py-2.5 px-5 lg:px-10">
        {/* Left: logo — cream/gold on dark green */}
        <Logo
          size="md"
          stacked={false}
          markColor="#E9DDC0"
          textClassName="text-[#F1EAD8]"
        />

        {/* Nav — EB Garamond, cream links */}
        <nav
          className="flex items-center gap-4 lg:gap-6 font-wserif text-[16px] lg:text-[18px] flex-1"
          aria-label="Main navigation"
        >
          {NAV.map((n) => {
            const linkCls = ({ isActive }) =>
              cn(
                'no-underline transition-colors flex items-center gap-1.5 whitespace-nowrap',
                isActive ? 'text-white' : 'text-[#c9d4cc] hover:text-white',
              );
            if (n.megaMenu) {
              return <ShopMegaMenu key={n.to} linkClassName={linkCls} />;
            }
            return (
              <NavLink key={n.to} to={n.to} end={n.end} className={linkCls}>
                {n.label}
              </NavLink>
            );
          })}
        </nav>

        {/* Persistent search pill (frontend-3) */}
        <form
          onSubmit={handleSearch}
          role="search"
          className="hidden lg:flex items-center bg-white rounded-full pl-4 pr-2 py-1.5 w-[300px] gap-2"
        >
          <label htmlFor="header-search" className="sr-only">
            Search products
          </label>
          <input
            id="header-search"
            type="search"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Looking for immunity, beauty, or better sleep?"
            autoComplete="off"
            className="flex-1 min-w-0 border-0 bg-transparent text-[12.5px] text-wink placeholder:text-[#a9a9a4] outline-none"
          />
          <button
            type="submit"
            aria-label="Search"
            className="text-[#6b6b66] hover:text-[#08112C] transition-colors bg-transparent border-0 p-1 cursor-pointer flex"
          >
            <SearchIcon size={16} strokeWidth={2} />
          </button>
        </form>

        {/* Icon row — cream on dark green */}
        <div className="flex items-center gap-4 lg:gap-5">
          {/* Search toggle — shown only when the pill is hidden (md screens) */}
          <button
            onClick={() => setSearchOpen((v) => !v)}
            className="lg:hidden cursor-pointer bg-transparent border-0 p-0 text-[#efe8d8] hover:text-white transition-colors flex"
            aria-label={searchOpen ? 'Close search' : 'Open search'}
            aria-expanded={searchOpen}
          >
            {searchOpen ? (
              <CloseIcon size={20} strokeWidth={1.6} />
            ) : (
              <SearchIcon size={20} strokeWidth={1.6} />
            )}
          </button>

          {/* Wishlist */}
          <Link
            to="/wishlist"
            className="relative text-[#efe8d8] hover:text-white transition-colors flex"
            aria-label={`Wishlist${wishlistCount ? ` (${wishlistCount} items)` : ''}`}
          >
            <HeartIcon size={22} strokeWidth={1.6} />
            <CountBadge count={wishlistCount} />
          </Link>

          {/* Account dropdown */}
          <div ref={accountRef} className="relative">
            {!user ? (
              <Link
                to="/login"
                className="flex items-center gap-1 text-[#efe8d8] hover:text-white transition-colors no-underline"
                aria-label="Sign in"
              >
                <UserIcon size={22} strokeWidth={1.6} />
              </Link>
            ) : (
              <>
                <button
                  ref={accountTriggerRef}
                  type="button"
                  onClick={() => setAccountOpen((v) => !v)}
                  aria-haspopup="menu"
                  aria-expanded={accountOpen}
                  aria-label="Account menu"
                  className="flex items-center cursor-pointer bg-transparent border-0 p-0"
                >
                  <span className="grid size-7 shrink-0 place-items-center rounded-full bg-[#08112C] text-white text-xs font-bold select-none">
                    {userInitial}
                  </span>
                </button>

                <AnimatePresence>
                  {accountOpen && (
                    <motion.div
                      ref={accountMenuRef}
                      role="menu"
                      aria-label="Account"
                      onKeyDown={handleMenuKeyDown}
                      initial={reduce ? { opacity: 0 } : { opacity: 0, y: -6, scale: 0.97 }}
                      animate={{ opacity: 1, y: 0, scale: 1 }}
                      exit={reduce ? { opacity: 0 } : { opacity: 0, y: -6, scale: 0.97 }}
                      transition={{ duration: 0.16, ease: [0.22, 1, 0.36, 1] }}
                      className="absolute right-0 mt-2 w-56 origin-top-right rounded-xl2 bg-wcard border border-wline shadow-md p-1.5 z-[50]"
                    >
                      {/* User info header */}
                      <div className="border-b border-wline px-3 py-2.5 mb-1">
                        <p className="text-xs text-wmuted">Signed in as</p>
                        <p className="truncate text-sm font-medium text-wink">
                          {user.email}
                        </p>
                        {typeof user.points_balance === 'number' && (
                          <p className="mt-0.5 text-[11px] text-wgold">
                            {user.points_balance.toLocaleString()} pts
                          </p>
                        )}
                      </div>

                      {/* Admin shortcut */}
                      {isStaff && (
                        <Link
                          to="/admin"
                          role="menuitem"
                          tabIndex={-1}
                          onClick={() => setAccountOpen(false)}
                          className={menuItemCls}
                        >
                          Admin Dashboard
                        </Link>
                      )}

                      {/* Account links */}
                      {ACCOUNT_LINKS.map((link) => (
                        <Link
                          key={link.to}
                          to={link.to}
                          role="menuitem"
                          tabIndex={-1}
                          onClick={() => setAccountOpen(false)}
                          className={menuItemCls}
                        >
                          {link.label}
                        </Link>
                      ))}

                      <div className="h-px bg-wline my-1" />

                      {/* Sign out */}
                      <button
                        type="button"
                        role="menuitem"
                        tabIndex={-1}
                        onClick={handleSignOut}
                        className="flex w-full items-center gap-2.5 rounded-sm px-3 py-2 text-sm text-wmuted transition-colors hover:bg-danger/10 hover:text-danger outline-none"
                      >
                        Sign Out
                      </button>
                    </motion.div>
                  )}
                </AnimatePresence>
              </>
            )}
          </div>

          {/* Cart */}
          <button
            onClick={openDrawer}
            className="relative cursor-pointer bg-transparent border-0 p-0 text-[#efe8d8] hover:text-white transition-colors flex"
            aria-label={`Open cart${cartCount ? ` (${cartCount} items)` : ''}`}
          >
            <BagIcon size={22} strokeWidth={1.6} />
            <CountBadge count={cartCount} />
          </button>
        </div>
      </div>

      {/* ── Mobile bar ──────────────────────────────────────────────────────── */}
      <div className="grid md:hidden grid-cols-[1fr_auto_1fr] items-center px-[18px] py-[13px]">
        {/* Hamburger */}
        <button
          type="button"
          onClick={() => {
  setMobileShopOpen(false);
  setMobileOpen(true);
}}
          className="bg-transparent border-0 p-0 text-[#efe8d8] justify-self-start cursor-pointer"
          aria-label="Open navigation menu"
          aria-expanded={mobileOpen}
          aria-controls="mobile-menu"
        >
          <MenuIcon size={24} />
        </button>

        {/* Centre: inline logo */}
        <Link to="/" className="flex items-center gap-1.5 no-underline" aria-label="Wellvia — home">
          <LeafMark size={22} dot={false} color="#E9DDC0" />
          <span className="font-display text-[17px] tracking-[0.2em] font-medium text-[#F1EAD8] pl-[0.2em]">
            WELLVIA
          </span>
        </Link>

        {/* Right: wishlist + cart */}
        <div className="flex items-center justify-end gap-4">
          <Link
            to="/wishlist"
            className="relative text-[#efe8d8]"
            aria-label={`Wishlist${wishlistCount ? ` (${wishlistCount})` : ''}`}
          >
            <HeartIcon size={22} strokeWidth={1.6} />
            {wishlistCount > 0 && (
              <span className="absolute -top-1.5 -right-2 bg-[#c0392b] text-white text-[9px] min-w-[15px] h-[15px] rounded-full flex items-center justify-center px-0.5 leading-none font-bold">
                {wishlistCount}
              </span>
            )}
          </Link>
          <button
            onClick={openDrawer}
            className="relative cursor-pointer bg-transparent border-0 p-0 text-[#efe8d8]"
            aria-label={`Open cart${cartCount ? ` (${cartCount})` : ''}`}
          >
            <BagIcon size={22} strokeWidth={1.6} />
            {cartCount > 0 && (
              <span className="absolute -top-1.5 -right-2 bg-[#c0392b] text-white text-[9px] min-w-[15px] h-[15px] rounded-full flex items-center justify-center px-0.5 leading-none font-bold">
                {cartCount}
              </span>
            )}
          </button>
        </div>
      </div>

      {/* ── Inline search bar (md screens, toggled) ─────────────────────────── */}
      {searchOpen && (
        <div className="lg:hidden border-t border-white/15 px-5 py-3 animate-rise">
          <form
            onSubmit={handleSearch}
            className="flex items-center gap-3 max-w-2xl mx-auto"
            role="search"
          >
            <label htmlFor="header-search-md" className="sr-only">
              Search products
            </label>
            <div className="relative flex-1">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-wmuted pointer-events-none">
                <SearchIcon size={15} strokeWidth={1.4} />
              </span>
              <input
                id="header-search-md"
                type="search"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search wellness products…"
                autoFocus
                autoComplete="off"
                className="w-full rounded-full border border-wline bg-wcard px-4 pl-9 py-2.5 text-[13.5px] text-wink placeholder:text-wmuted"
              />
            </div>
            <button
              type="submit"
              className="bg-[#08112C] text-white rounded-full px-5 py-2.5 text-[13px] cursor-pointer hover:bg-[#08112C] transition-colors whitespace-nowrap"
            >
              Search
            </button>
          </form>
        </div>
      )}

      {/* ── Mobile slide-in menu ─────────────────────────────────────────────── */}
        {mobileOpen && (
          <>
            {/* Backdrop */}
            <motion.div
  key="mobile-menu-backdrop"
                initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              onClick={closeMobileMenu}
              className="fixed inset-0 bg-wink/30 z-[100]"
              aria-hidden="true"
            />

            {/* Drawer */}
            <motion.div
  key="mobile-menu-drawer"
              id="mobile-menu"
              initial={reduce ? { opacity: 0 } : { x: -300, opacity: 0 }}
              animate={{ x: 0, opacity: 1 }}
              exit={reduce ? { opacity: 0 } : { x: -300, opacity: 0 }}
              transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
              className="fixed top-0 left-0 h-full w-[300px] max-w-full bg-wcard z-[101] flex flex-col shadow-[4px_0_30px_rgba(30,24,14,0.15)]"
              role="dialog"
              aria-modal="true"
              aria-label="Navigation menu"
            >
              {/* Menu header */}
              <div className="flex items-center justify-between px-5 py-[18px] border-b border-wline shrink-0">
                <div className="flex items-center gap-1.5">
                  <LeafMark size={20} dot={false} />
                  <span className="font-display text-[15px] tracking-[0.18em] font-medium text-wgreen pl-[0.18em]">
                    WELLVIA
                  </span>
                </div>
                <button
                  type="button"
                  onClick={closeMobileMenu}
                  className="relative z-10 -m-2 cursor-pointer border-0 bg-transparent p-2 text-wmuted transition-colors hover:text-wink"
                  aria-label="Close menu"
                >
                  <CloseIcon size={20} />
                </button>
              </div>

              {/* Search in mobile menu */}
              <div className="px-5 py-4 border-b border-wline shrink-0">
                <form onSubmit={handleSearch} className="flex gap-2" role="search">
                  <label htmlFor="mobile-search" className="sr-only">
                    Search products
                  </label>
                  <input
                    id="mobile-search"
                    type="search"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Search…"
                    autoComplete="off"
                    className="flex-1 rounded-full border border-wline bg-wpaper px-4 py-2 text-[13.5px] text-wink placeholder:text-wmuted"
                  />
                  <button
                    type="submit"
                    className="bg-[#08112C] text-white rounded-full px-4 py-2 text-[13px] cursor-pointer hover:bg-[#08112C] transition-colors"
                  >
                    Go
                  </button>
                </form>
              </div>

              {/* Nav links */}
              <nav
                className="flex flex-col px-5 py-4 gap-1 overflow-y-auto flex-1"
                aria-label="Mobile navigation"
              >
                {NAV.map((n) => {
                  if (n.megaMenu) {
                    // "Shop" row is an accordion trigger on mobile — it toggles
                    // the category list open/closed instead of navigating away.
                    // The "All Products →" link inside the panel is what
                    // actually navigates to /products.
                    return (
                      <div key={n.to}>
                        <button
                          type="button"
                          onClick={() => setMobileShopOpen((v) => !v)}
                          aria-expanded={mobileShopOpen}
                          aria-controls="mobile-shop-panel"
                          className={cn(
                            'flex w-full items-center justify-between px-3 py-3 rounded-xl text-[14px] transition-colors cursor-pointer border-0 bg-transparent font-[inherit] text-left',
                            isShopSection
                              ? 'bg-[#08112C] text-white'
                              : 'text-wink hover:bg-wline/40',
                          )}
                        >
                          <span>{n.label}</span>
                          <ChevronIcon
                            className={cn(
                              'transition-transform duration-200',
                              mobileShopOpen && 'rotate-180',
                            )}
                          />
                        </button>
                        <AnimatePresence initial={false}>
                          {mobileShopOpen && (
                            <motion.div
                              id="mobile-shop-panel"
                              initial={{ height: 0, opacity: 0 }}
                              animate={{ height: 'auto', opacity: 1 }}
                              exit={{ height: 0, opacity: 0 }}
                              transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
                              className="overflow-hidden"
                            >
                              <ShopMobileLinks
  onNavigate={() => {
    setMobileOpen(false);
    setMobileShopOpen(false);
  }}
/>
                            </motion.div>
                          )}
                        </AnimatePresence>
                      </div>
                    );
                  }
                  return (
                    <NavLink
                      key={n.to}
                      to={n.to}
                      end={n.end}
                      onClick={() => {
  setMobileOpen(false);
  setMobileShopOpen(false);
}}
                      className={({ isActive }) =>
                        cn(
                          'block px-3 py-3 rounded-xl text-[14px] no-underline transition-colors',
                          isActive
                            ? 'bg-[#08112C] text-white'
                            : 'text-wink hover:bg-wline/40',
                        )
                      }
                    >
                      {n.label}
                    </NavLink>
                  );
                })}
              </nav>

              {/* Account / sign-in section */}
              <div className="border-t border-wline px-5 py-4 shrink-0">
                {user ? (
                  <div>
                    {/* Avatar + name */}
                    <div className="flex items-center gap-3 mb-3">
                      <span className="grid size-9 shrink-0 place-items-center rounded-full bg-wgreen text-white text-sm font-bold select-none">
                        {userInitial}
                      </span>
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-wink truncate">
                          {displayName}
                        </p>
                        {user.email && (
                          <p className="text-[11px] text-wmuted truncate">
                            {user.email}
                          </p>
                        )}
                      </div>
                    </div>

                    {/* Account links */}
                    <div className="flex flex-col gap-0.5">
                      {isStaff && (
                        <Link
                          to="/admin"
                          onClick={closeMobileMenu}
                          className="px-3 py-2.5 rounded-xl text-[13.5px] text-wmuted hover:bg-wline/40 hover:text-wink no-underline transition-colors"
                        >
                          Admin Dashboard
                        </Link>
                      )}
                      {ACCOUNT_LINKS.map((link) => (
                        <Link
                          key={link.to}
                          to={link.to}
                          onClick={closeMobileMenu}
                          className="px-3 py-2.5 rounded-xl text-[13.5px] text-wmuted hover:bg-wline/40 hover:text-wink no-underline transition-colors"
                        >
                          {link.label}
                        </Link>
                      ))}
                      <button
                        onClick={handleSignOut}
                        className="text-left px-3 py-2.5 rounded-xl text-[13.5px] text-wmuted hover:bg-wline/40 hover:text-danger bg-transparent border-0 cursor-pointer transition-colors"
                      >
                        Sign Out
                      </button>
                    </div>
                  </div>
                ) : (
                  <Link
                    to="/login"
                    onClick={closeMobileMenu}
                    className="block w-full text-center bg-[#08112C] text-white rounded-full py-3 text-[14px] font-medium no-underline hover:bg-[#08112C] transition-colors"
                  >
                    Sign In
                  </Link>
                )}
              </div>
            </motion.div>
          </>
        )}
    </header>
  );
}