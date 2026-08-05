import { useEffect } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import Header from '@/components/storefront/Header';
import Footer from '@/components/storefront/Footer';
import CartDrawer from '@/components/storefront/CartDrawer';
import AddedToCartModal from '@/components/storefront/AddedToCartModal';

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
    <div className="wellvia-root min-h-full">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:z-[200] focus:left-2 focus:top-2 focus:rounded-xl focus:bg-wcard focus:px-4 focus:py-2 focus:text-wgreen focus:shadow-md focus:outline-none focus:ring-2 focus:ring-wgreen/40"
      >
        Skip to main content
      </a>

      {bare ? (
        <main id="main-content" className="min-h-screen">
          <Outlet />
        </main>
      ) : (
        <div className="min-h-screen relative flex flex-col">
          <Header />
          <CartDrawer />
          <AddedToCartModal />
          <main id="main-content" className="flex-1">
            <Outlet />
          </main>
          <Footer />
        </div>
      )}
    </div>
  );
}
