import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { LogOut, LayoutDashboard, UserRound, Package, Heart, Coins, Shield, MapPin, ChevronDown, ShoppingCart } from 'lucide-react';
import { cn } from '@/lib/utils.js';
import { useAuthStore } from '@/features/auth/store.js';
import { authApi } from '@/features/auth/api.js';

/**
 * Navbar account control.
 * - Signed out: a "Sign in" link.
 * - Signed in: an avatar button opening a menu with the email, an admin
 *   shortcut (admins only), and Sign out.
 */
export default function AccountMenu({ onAccent = false }) {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();
  const reduce = useReducedMotion();

  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  // Ref to the trigger button so we can restore focus on close.
  const triggerRef = useRef(null);
  // Ref to the dropdown menu element so we can query menuitems inside it.
  const menuRef = useRef(null);

  // Returns all focusable menuitem elements currently rendered in the menu.
  const getMenuItems = useCallback(() => {
    if (!menuRef.current) return [];
    return Array.from(menuRef.current.querySelectorAll('[role="menuitem"]'));
  }, []);

  // Focus the first menuitem when the menu opens.
  useEffect(() => {
    if (!open) return undefined;
    // Defer one tick so AnimatePresence has mounted the node.
    const id = setTimeout(() => {
      const items = getMenuItems();
      if (items.length > 0) items[0].focus();
    }, 0);
    return () => clearTimeout(id);
  }, [open, getMenuItems]);

  useEffect(() => {
    if (!open) return undefined;
    const onPointer = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') {
        setOpen(false);
        // Restore focus to the trigger button.
        triggerRef.current?.focus();
      }
    };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  // Arrow-key / Home / End / Tab navigation within the menu.
  function handleMenuKeyDown(e) {
    const items = getMenuItems();
    if (items.length === 0) return;
    const focused = document.activeElement;
    const idx = items.indexOf(focused);

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      const next = idx < items.length - 1 ? idx + 1 : 0;
      items[next].focus();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      const prev = idx > 0 ? idx - 1 : items.length - 1;
      items[prev].focus();
    } else if (e.key === 'Home') {
      e.preventDefault();
      items[0].focus();
    } else if (e.key === 'End') {
      e.preventDefault();
      items[items.length - 1].focus();
    } else if (e.key === 'Tab') {
      // Close the menu and let focus leave naturally; return it to the trigger.
      setOpen(false);
      triggerRef.current?.focus();
      e.preventDefault();
    }
  }

  // Signed out: on the blue chrome this is the Flipkart white "Login" pill;
  // elsewhere a dark icon + label consistent with other header actions.
  if (!user) {
    if (onAccent) {
      return (
        <Link
          to="/login"
          className="hidden h-8 items-center rounded-sm bg-white px-6 text-sm font-semibold text-accent shadow-sm transition-colors hover:bg-white/90 focus-visible:focus-ring sm:inline-flex"
        >
          Login
        </Link>
      );
    }
    return (
      <Link
        to="/login"
        className="flex items-center gap-2 rounded-sm px-2 py-1.5 text-sm font-medium text-ink-secondary transition-colors hover:text-accent focus-visible:focus-ring"
      >
        <UserRound className="size-[22px]" aria-hidden="true" />
        <span className="hidden lg:inline">Login</span>
      </Link>
    );
  }

  const initial = (user.email || '?').charAt(0).toUpperCase();
  const displayName = user.name || (user.email ? user.email.split('@')[0] : 'Account');
  // Staff = legacy admin flag OR any assigned role. Mirrors <RequireAdmin>,
  // so anyone who can enter the admin shell also sees the shortcut to it.
  const isStaff = !!user.is_admin || (Array.isArray(user.roles) && user.roles.length > 0);

  async function handleSignOut() {
    setOpen(false);
    triggerRef.current?.focus();
    // Best-effort: tell the server to revoke this refresh-token family. If
    // the request fails (network drop, server down) we still clear local
    // state so the device is signed out either way.
    const rt = useAuthStore.getState().refreshToken;
    if (rt) {
      authApi.logout(rt).catch(() => {});
    }
    logout();
    navigate('/');
  }

  // Shared classes for every menuitem so they can receive programmatic focus
  // without appearing in the natural Tab order (tabIndex={-1}).
  const itemCls =
    'flex items-center gap-2.5 rounded-sm px-3 py-2 text-sm text-ink-secondary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring outline-none';

  return (
    <div ref={ref} className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Account menu"
        className={cn(
          'flex items-center gap-2 rounded-sm px-2 py-1.5 transition-colors focus-visible:focus-ring',
          onAccent ? 'text-white hover:text-white' : 'text-ink-secondary hover:text-accent',
        )}
      >
        <span
          className={cn(
            'grid size-7 shrink-0 place-items-center rounded-full text-xs font-bold',
            onAccent ? 'bg-white text-accent' : 'bg-accent text-white',
          )}
        >
          {initial}
        </span>
        <span
          className={cn(
            'hidden max-w-[8rem] truncate text-sm font-medium lg:inline',
            onAccent ? 'text-white' : 'text-ink-primary',
          )}
        >
          {displayName}
        </span>
        <ChevronDown
          className={cn('hidden size-4 transition-transform sm:block', open && 'rotate-180')}
          aria-hidden="true"
        />
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            ref={menuRef}
            role="menu"
            aria-label="Account"
            onKeyDown={handleMenuKeyDown}
            initial={reduce ? { opacity: 0 } : { opacity: 0, y: -6, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={reduce ? { opacity: 0 } : { opacity: 0, y: -6, scale: 0.97 }}
            transition={{ duration: 0.16, ease: [0.22, 1, 0.36, 1] }}
            className="absolute right-0 mt-2 w-60 origin-top-right rounded-md bg-bg-elevated border border-line-subtle shadow-md p-1.5"
          >
            <div className="border-b border-line-subtle px-3 py-2.5">
              <p className="text-xs text-ink-tertiary">Signed in as</p>
              <p className="truncate text-sm font-medium text-ink-primary">
                {user.email}
              </p>
              {typeof user.points_balance === 'number' && (
                <p className="mt-1 inline-flex items-center gap-1 text-[11px] text-accent">
                  <Coins className="size-3" aria-hidden="true" />
                  {user.points_balance.toLocaleString()} pts
                </p>
              )}
            </div>

            {isStaff && (
              <Link
                to="/admin"
                role="menuitem"
                tabIndex={-1}
                onClick={() => setOpen(false)}
                className={cn('mt-1', itemCls)}
              >
                <LayoutDashboard className="size-4" aria-hidden="true" />
                Admin dashboard
              </Link>
            )}

            <Link
              to="/cart"
              role="menuitem"
              tabIndex={-1}
              onClick={() => setOpen(false)}
              className={itemCls}
            >
              <ShoppingCart className="size-4" aria-hidden="true" />
              My cart
            </Link>

            <Link
              to="/wishlist"
              role="menuitem"
              tabIndex={-1}
              onClick={() => setOpen(false)}
              className={itemCls}
            >
              <Heart className="size-4" aria-hidden="true" />
              My wishlist
            </Link>

            <Link
              to="/rewards"
              role="menuitem"
              tabIndex={-1}
              onClick={() => setOpen(false)}
              className={itemCls}
            >
              <Coins className="size-4" aria-hidden="true" />
              My rewards
            </Link>

            <Link
              to="/account/security"
              role="menuitem"
              tabIndex={-1}
              onClick={() => setOpen(false)}
              className={itemCls}
            >
              <Shield className="size-4" aria-hidden="true" />
              Security & 2FA
            </Link>

            <Link
              to="/account/addresses"
              role="menuitem"
              tabIndex={-1}
              onClick={() => setOpen(false)}
              className={itemCls}
            >
              <MapPin className="size-4" aria-hidden="true" />
              My addresses
            </Link>

            <Link
              to="/orders"
              role="menuitem"
              tabIndex={-1}
              onClick={() => setOpen(false)}
              className={itemCls}
            >
              <Package className="size-4" aria-hidden="true" />
              My orders
            </Link>

            <button
              type="button"
              role="menuitem"
              tabIndex={-1}
              onClick={handleSignOut}
              className="flex w-full items-center gap-2.5 rounded-sm px-3 py-2 text-sm text-ink-secondary transition-colors hover:bg-danger/10 hover:text-danger focus-visible:focus-ring outline-none"
            >
              <LogOut className="size-4" aria-hidden="true" />
              Sign out
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
