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

/** Primary nav links — real app routes. */
const NAV = [
  { to: '/', label: 'Home', end: true },
  { to: '/products', label: 'Shop' },
  { to: '/about', label: 'About' },
  { to: '/contact', label: 'Contact' },
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
 */
export default function Header() {
  const [mobileOpen, setMobileOpen] = useState(false);
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

  // ── Close everything on route change ────────────────────────────────────────
  useEffect(() => {
    setAccountOpen(false);
    setMobileOpen(false);
    setSearchOpen(false);
  }, [location.pathname]);

  // ── Lock body scroll while mobile menu is open ───────────────────────────────
  useEffect(() => {
    document.body.style.overflow = mobileOpen ? 'hidden' : '';
    return () => {
      document.body.style.overflow = '';
    };
  }, [mobileOpen]);

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
    setMobileOpen(false);
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
      <span className="absolute -top-2 -right-2 bg-wgreen text-white text-[9px] min-w-[15px] h-[15px] rounded-full flex items-center justify-center px-0.5 leading-none">
        {count > 99 ? '99+' : count}
      </span>
    );
  }

  return (
    <header className="sticky top-0 z-40 bg-wpaper/95 backdrop-blur-md border-b border-wline">

      {/* ── Desktop ─────────────────────────────────────────────────────────── */}
      <div className="hidden md:grid grid-cols-[1fr_auto_1fr] items-center gap-4 py-3.5 px-5 lg:px-[52px]">
        {/* Left: small horizontal logo */}
        <Logo size="sm" stacked={false} />

        {/* Centre: large stacked logo */}
        <Logo size="lg" stacked />

        {/* Right: nav + icons */}
        <nav
          className="flex items-center justify-end gap-4 lg:gap-7 text-[12.5px] tracking-[0.12em] uppercase"
          aria-label="Main navigation"
        >
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) =>
                cn(
                  'no-underline transition-colors',
                  isActive ? 'text-wgreen font-medium' : 'text-wink hover:text-wgreen',
                )
              }
            >
              {n.label}
            </NavLink>
          ))}

          {/* Divider */}
          <span className="w-px h-[18px] bg-wline shrink-0" aria-hidden="true" />

          {/* Search toggle */}
          <button
            onClick={() => setSearchOpen((v) => !v)}
            className="cursor-pointer bg-transparent border-0 p-0 text-wink hover:text-wgreen transition-colors"
            aria-label={searchOpen ? 'Close search' : 'Open search'}
            aria-expanded={searchOpen}
          >
            {searchOpen ? (
              <CloseIcon size={18} strokeWidth={1.4} />
            ) : (
              <SearchIcon size={18} strokeWidth={1.4} />
            )}
          </button>

          {/* Wishlist */}
          <Link
            to="/wishlist"
            className="relative text-wink hover:text-wgreen transition-colors"
            aria-label={`Wishlist${wishlistCount ? ` (${wishlistCount} items)` : ''}`}
          >
            <HeartIcon size={18} strokeWidth={1.4} />
            <CountBadge count={wishlistCount} />
          </Link>

          {/* Account dropdown */}
          <div ref={accountRef} className="relative">
            {!user ? (
              <Link
                to="/login"
                className="flex items-center gap-1 text-wink hover:text-wgreen transition-colors no-underline"
                aria-label="Sign in"
              >
                <UserIcon size={18} strokeWidth={1.4} />
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
                  <span className="grid size-7 shrink-0 place-items-center rounded-full bg-wgreen text-white text-xs font-bold select-none">
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
            className="relative cursor-pointer bg-transparent border-0 p-0 text-wink hover:text-wgreen transition-colors"
            aria-label={`Open cart${cartCount ? ` (${cartCount} items)` : ''}`}
          >
            <BagIcon size={19} strokeWidth={1.4} />
            <CountBadge count={cartCount} />
          </button>
        </nav>
      </div>

      {/* ── Mobile bar ──────────────────────────────────────────────────────── */}
      <div className="grid md:hidden grid-cols-[1fr_auto_1fr] items-center px-[18px] py-[13px]">
        {/* Hamburger */}
        <button
          onClick={() => setMobileOpen(true)}
          className="bg-transparent border-0 p-0 text-wink justify-self-start cursor-pointer"
          aria-label="Open navigation menu"
          aria-expanded={mobileOpen}
          aria-controls="mobile-menu"
        >
          <MenuIcon size={22} />
        </button>

        {/* Centre: inline logo */}
        <div className="flex items-center gap-1.5">
          <LeafMark size={22} dot={false} />
          <span className="font-display text-[17px] tracking-[0.2em] font-medium text-wgreen pl-[0.2em]">
            WELLVIA
          </span>
        </div>

        {/* Right: wishlist + cart */}
        <div className="flex items-center justify-end gap-4">
          <Link
            to="/wishlist"
            className="relative text-wink"
            aria-label={`Wishlist${wishlistCount ? ` (${wishlistCount})` : ''}`}
          >
            <HeartIcon size={18} strokeWidth={1.4} />
            {wishlistCount > 0 && (
              <span className="absolute -top-2 -right-2 bg-wgreen text-white text-[9px] min-w-[15px] h-[15px] rounded-full flex items-center justify-center px-0.5 leading-none">
                {wishlistCount}
              </span>
            )}
          </Link>
          <button
            onClick={openDrawer}
            className="relative cursor-pointer bg-transparent border-0 p-0 text-wink"
            aria-label={`Open cart${cartCount ? ` (${cartCount})` : ''}`}
          >
            <BagIcon size={19} strokeWidth={1.4} />
            {cartCount > 0 && (
              <span className="absolute -top-2 -right-2 bg-wgreen text-white text-[9px] min-w-[15px] h-[15px] rounded-full flex items-center justify-center px-0.5 leading-none">
                {cartCount}
              </span>
            )}
          </button>
        </div>
      </div>

      {/* ── Inline search bar (desktop + mobile) ────────────────────────────── */}
      {searchOpen && (
        <div className="border-t border-wline px-5 lg:px-[52px] py-3 animate-rise">
          <form
            onSubmit={handleSearch}
            className="flex items-center gap-3 max-w-2xl mx-auto"
            role="search"
          >
            <label htmlFor="header-search" className="sr-only">
              Search products
            </label>
            <div className="relative flex-1">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-wmuted pointer-events-none">
                <SearchIcon size={15} strokeWidth={1.4} />
              </span>
              <input
                id="header-search"
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
              className="bg-wgreen text-white rounded-full px-5 py-2.5 text-[13px] cursor-pointer hover:bg-wgreen-dark transition-colors whitespace-nowrap"
            >
              Search
            </button>
          </form>
        </div>
      )}

      {/* ── Mobile slide-in menu ─────────────────────────────────────────────── */}
      <AnimatePresence>
        {mobileOpen && (
          <>
            {/* Backdrop */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              onClick={() => setMobileOpen(false)}
              className="fixed inset-0 bg-wink/30 z-[80]"
              aria-hidden="true"
            />

            {/* Drawer */}
            <motion.div
              id="mobile-menu"
              initial={reduce ? { opacity: 0 } : { x: -300, opacity: 0 }}
              animate={{ x: 0, opacity: 1 }}
              exit={reduce ? { opacity: 0 } : { x: -300, opacity: 0 }}
              transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
              className="fixed top-0 left-0 h-full w-[300px] max-w-full bg-wcard z-[81] flex flex-col shadow-[4px_0_30px_rgba(30,24,14,0.15)]"
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
                  onClick={() => setMobileOpen(false)}
                  className="bg-transparent border-0 p-0 text-wmuted cursor-pointer hover:text-wink transition-colors"
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
                    className="bg-wgreen text-white rounded-full px-4 py-2 text-[13px] cursor-pointer hover:bg-wgreen-dark transition-colors"
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
                {NAV.map((n) => (
                  <NavLink
                    key={n.to}
                    to={n.to}
                    end={n.end}
                    onClick={() => setMobileOpen(false)}
                    className={({ isActive }) =>
                      cn(
                        'px-3 py-3 rounded-xl text-[14px] no-underline transition-colors',
                        isActive
                          ? 'bg-wgreen text-white'
                          : 'text-wink hover:bg-wline/40',
                      )
                    }
                  >
                    {n.label}
                  </NavLink>
                ))}
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
                          onClick={() => setMobileOpen(false)}
                          className="px-3 py-2.5 rounded-xl text-[13.5px] text-wmuted hover:bg-wline/40 hover:text-wink no-underline transition-colors"
                        >
                          Admin Dashboard
                        </Link>
                      )}
                      {ACCOUNT_LINKS.map((link) => (
                        <Link
                          key={link.to}
                          to={link.to}
                          onClick={() => setMobileOpen(false)}
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
                    onClick={() => setMobileOpen(false)}
                    className="block w-full text-center bg-wgreen text-white rounded-full py-3 text-[14px] font-medium no-underline hover:bg-wgreen-dark transition-colors"
                  >
                    Sign In
                  </Link>
                )}
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </header>
  );
}
