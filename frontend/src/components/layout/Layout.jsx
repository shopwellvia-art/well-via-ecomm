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
 * - pt-16 clears the fixed Navbar.
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
      <Navbar />
      {/* Clears the fixed header. Mobile is taller — it has a second search row. */}
      <div className="flex-1 pt-[8.5rem] md:pt-16">
        <Outlet />
      </div>
      <Footer />
    </div>
  );
}
