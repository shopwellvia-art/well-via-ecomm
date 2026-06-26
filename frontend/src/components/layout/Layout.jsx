import { useEffect } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import Header from '@/components/storefront/Header';
import Footer from '@/components/storefront/Footer';
import CartDrawer from '@/components/storefront/CartDrawer';

/**
 * Root storefront layout — Wellvia re-skin.
 *
 * Responsibilities:
 * - Wraps everything in `.wellvia-root bg-wcanvas` so the Jost font and
 *   scoped Wellvia tokens apply storefront-wide (admin uses its own layout).
 * - Non-bare routes render inside a centred `.paper max-w-[1320px]` panel
 *   with the sticky Header, CartDrawer (fixed), and Footer.
 * - Bare routes (login / forgot-password / auth callback / payments/…) are
 *   rendered full-screen inside .wellvia-root but WITHOUT chrome.
 * - Scrolls to top on every route change (instant, not smooth, so entrance
 *   animations are not disrupted).
 * - Skip-to-content link for keyboard / AT users.
 */

/** Paths that render without Header / Footer / CartDrawer. */
const BARE_PATHS = ['/login', '/forgot-password', '/auth/callback'];

function isBareRoute(pathname) {
  return BARE_PATHS.includes(pathname) || pathname.startsWith('/payments/');
}

export default function Layout() {
  const { pathname } = useLocation();
  const bare = isBareRoute(pathname);

  // Instant scroll-to-top on every client-side navigation.
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'instant' });
  }, [pathname]);

  return (
    <div className="wellvia-root bg-wcanvas min-h-full">
      {/*
       * Skip link — keyboard users bypass the sticky header.
       * Visible only when focused (sr-only → not-sr-only on focus).
       */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:z-[200] focus:left-2 focus:top-2 focus:rounded-xl focus:bg-wcard focus:px-4 focus:py-2 focus:text-wgreen focus:shadow-md focus:outline-none focus:ring-2 focus:ring-wgreen/40"
      >
        Skip to main content
      </a>

      {bare ? (
        /*
         * Bare routes — full-screen within .wellvia-root.
         * Login, forgot-password, auth callback, payments/* pages
         * render their own full-screen wellness layout internally.
         */
        <main id="main-content" className="min-h-screen">
          <Outlet />
        </main>
      ) : (
        /*
         * Normal storefront routes — centred paper panel.
         * The sticky Header takes its natural height in the flex column;
         * the flex-1 main fills remaining space to push Footer to the bottom.
         */
        <div className="mx-auto max-w-[1320px] paper min-h-screen shadow-[0_30px_80px_-30px_rgba(40,30,10,0.22)] relative flex flex-col">
          <Header />
          {/* CartDrawer is fixed-position; mounting it here keeps it
              inside the .wellvia-root so Wellvia CSS tokens are in scope. */}
          <CartDrawer />
          <main id="main-content" className="flex-1">
            <Outlet />
          </main>
          <Footer />
        </div>
      )}
    </div>
  );
}
