import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { LogOut, LayoutDashboard, UserRound, Package, Heart, Coins, Shield, MapPin } from 'lucide-react';
import { cn } from '@/lib/utils.js';
import { useAuthStore } from '@/features/auth/store.js';
import { authApi } from '@/features/auth/api.js';

/**
 * Navbar account control.
 * - Signed out: a "Sign in" link.
 * - Signed in: an avatar button opening a menu with the email, an admin
 *   shortcut (admins only), and Sign out.
 */
export default function AccountMenu() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();
  const reduce = useReducedMotion();

  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onPointer = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  if (!user) {
    return (
      <Link
        to="/login"
        className="inline-flex items-center whitespace-nowrap rounded-full border border-line-subtle px-4 py-1.5 text-sm font-medium text-ink-secondary transition-colors hover:border-line-strong hover:text-ink-primary focus-visible:focus-ring"
      >
        Sign in
      </Link>
    );
  }

  const initial = (user.email || '?').charAt(0).toUpperCase();
  // Staff = legacy admin flag OR any assigned role. Mirrors <RequireAdmin>,
  // so anyone who can enter the admin shell also sees the shortcut to it.
  const isStaff = !!user.is_admin || (Array.isArray(user.roles) && user.roles.length > 0);

  async function handleSignOut() {
    setOpen(false);
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

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Account menu"
        className="grid size-10 place-items-center rounded-full focus-visible:focus-ring"
      >
        <span className="grid size-8 place-items-center rounded-full bg-accent/12 text-xs font-semibold text-accent">
          {initial}
        </span>
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            role="menu"
            aria-label="Account"
            initial={reduce ? { opacity: 0 } : { opacity: 0, y: -6, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={reduce ? { opacity: 0 } : { opacity: 0, y: -6, scale: 0.97 }}
            transition={{ duration: 0.16, ease: [0.22, 1, 0.36, 1] }}
            className="absolute right-0 mt-2 w-60 origin-top-right rounded-md glass p-1.5"
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
                onClick={() => setOpen(false)}
                className="mt-1 flex items-center gap-2.5 rounded-sm px-3 py-2 text-sm text-ink-secondary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
              >
                <LayoutDashboard className="size-4" aria-hidden="true" />
                Admin dashboard
              </Link>
            )}

            <Link
              to="/cart"
              role="menuitem"
              onClick={() => setOpen(false)}
              className="flex items-center gap-2.5 rounded-sm px-3 py-2 text-sm text-ink-secondary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
            >
              <UserRound className="size-4" aria-hidden="true" />
              My cart
            </Link>

            <Link
              to="/wishlist"
              role="menuitem"
              onClick={() => setOpen(false)}
              className="flex items-center gap-2.5 rounded-sm px-3 py-2 text-sm text-ink-secondary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
            >
              <Heart className="size-4" aria-hidden="true" />
              My wishlist
            </Link>

            <Link
              to="/rewards"
              role="menuitem"
              onClick={() => setOpen(false)}
              className="flex items-center gap-2.5 rounded-sm px-3 py-2 text-sm text-ink-secondary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
            >
              <Coins className="size-4" aria-hidden="true" />
              My rewards
            </Link>

            <Link
              to="/account/security"
              role="menuitem"
              onClick={() => setOpen(false)}
              className="flex items-center gap-2.5 rounded-sm px-3 py-2 text-sm text-ink-secondary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
            >
              <Shield className="size-4" aria-hidden="true" />
              Security & 2FA
            </Link>

            <Link
              to="/account/addresses"
              role="menuitem"
              onClick={() => setOpen(false)}
              className="flex items-center gap-2.5 rounded-sm px-3 py-2 text-sm text-ink-secondary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
            >
              <MapPin className="size-4" aria-hidden="true" />
              My addresses
            </Link>

            <Link
              to="/orders"
              role="menuitem"
              onClick={() => setOpen(false)}
              className="flex items-center gap-2.5 rounded-sm px-3 py-2 text-sm text-ink-secondary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
            >
              <Package className="size-4" aria-hidden="true" />
              My orders
            </Link>

            <button
              type="button"
              role="menuitem"
              onClick={handleSignOut}
              className="flex w-full items-center gap-2.5 rounded-sm px-3 py-2 text-sm text-ink-secondary transition-colors hover:bg-danger/10 hover:text-danger focus-visible:focus-ring"
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
