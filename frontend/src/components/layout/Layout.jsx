import { useEffect } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import Navbar from './Navbar.jsx';
import Footer from './Footer.jsx';

/**
 * Root storefront layout.
 *
 * Responsibilities:
 * - Renders the sticky Navbar, main content area, and Footer.
 * - Scrolls the window to the top on every route change (client-side nav
 *   doesn't do this automatically; without it users land mid-page on back nav).
 * - pt-16 (64px) clears the fixed Navbar on desktop (h-16 nav bar only).
 * - pt-[7.75rem] (124px) clears the fixed Navbar on mobile:
 *     h-16 nav bar (64px) + py-2.5 top+bottom (20px) + h-10 search input (40px) = 124px.
 */
export default function Layout() {
  const { pathname } = useLocation();

  useEffect(() => {
    // Instant scroll on route change — no smooth scroll to avoid fighting
    // anchor-link behaviour or the page entrance animation.
    window.scrollTo({ top: 0, behavior: 'instant' });
  }, [pathname]);

  return (
    <div className="flex min-h-full flex-col bg-bg-base">
      {/* Skip link — lets keyboard users jump past the navbar directly to content. */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:z-[200] focus:left-2 focus:top-2 focus:rounded-sm focus:bg-bg-elevated focus:px-4 focus:py-2 focus:text-accent focus:shadow-md"
      >
        Skip to main content
      </a>
      <Navbar />
      {/* Clears the fixed header. Mobile is taller — it has a second search row. */}
      <div id="main-content" className="flex-1 pt-[7.75rem] md:pt-16">
        <Outlet />
      </div>
      <Footer />
    </div>
  );
}
