import { Routes, Route, useLocation } from 'react-router-dom';
import Header from './components/Header';
import Footer from './components/Footer';
import CartDrawer from './components/CartDrawer';

// Store
import HomePage from './pages/HomePage';
import ProductListPage from './pages/ProductListPage';
import ProductDetailPage from './pages/ProductDetailPage';
import CartPage from './pages/CartPage';
import CheckoutPage from './pages/CheckoutPage';
// Auth
import LoginPage from './pages/LoginPage';
import ForgotPasswordPage from './pages/ForgotPasswordPage';
import AuthCallbackPage from './pages/AuthCallbackPage';
// Account
import AccountSecurityPage from './pages/AccountSecurityPage';
import AddressesPage from './pages/AddressesPage';
import OrdersPage from './pages/OrdersPage';
import OrderDetailPage from './pages/OrderDetailPage';
import WishlistPage from './pages/WishlistPage';
import RewardsPage from './pages/RewardsPage';
// Payments
import PaymentMockPage from './pages/PaymentMockPage';
import PaymentReturnPage from './pages/PaymentReturnPage';
// Company
import AboutPage from './pages/AboutPage';
import CareersPage from './pages/CareersPage';
import CorporatePage from './pages/CorporatePage';
import ContactPage from './pages/ContactPage';
import PressPage from './pages/PressPage';
import StorePage from './pages/StorePage';
import NotFoundPage from './pages/NotFoundPage';

// Auth/payment pages render without the marketing chrome.
const BARE = ['/login', '/forgot-password', '/auth/callback', '/payment', '/payment/return'];

export default function App() {
  const { pathname } = useLocation();
  const bare = BARE.includes(pathname);

  return (
    <div className="min-h-screen bg-page">
      <div className="mx-auto max-w-[1320px] paper min-h-screen shadow-[0_30px_80px_-30px_rgba(40,30,10,0.22)] relative">
        {!bare && <Header />}
        <Routes>
          {/* Store */}
          <Route path="/" element={<HomePage />} />
          <Route path="/shop" element={<ProductListPage />} />
          <Route path="/product/:slug" element={<ProductDetailPage />} />
          <Route path="/cart" element={<CartPage />} />
          <Route path="/checkout" element={<CheckoutPage />} />
          {/* Auth */}
          <Route path="/login" element={<LoginPage />} />
          <Route path="/forgot-password" element={<ForgotPasswordPage />} />
          <Route path="/auth/callback" element={<AuthCallbackPage />} />
          {/* Account */}
          <Route path="/account" element={<AccountSecurityPage />} />
          <Route path="/account/addresses" element={<AddressesPage />} />
          <Route path="/account/orders" element={<OrdersPage />} />
          <Route path="/account/orders/:id" element={<OrderDetailPage />} />
          <Route path="/account/wishlist" element={<WishlistPage />} />
          <Route path="/account/rewards" element={<RewardsPage />} />
          {/* Payments */}
          <Route path="/payment" element={<PaymentMockPage />} />
          <Route path="/payment/return" element={<PaymentReturnPage />} />
          {/* Company */}
          <Route path="/about" element={<AboutPage />} />
          <Route path="/careers" element={<CareersPage />} />
          <Route path="/corporate" element={<CorporatePage />} />
          <Route path="/contact" element={<ContactPage />} />
          <Route path="/press" element={<PressPage />} />
          <Route path="/stores" element={<StorePage />} />
          {/* Misc */}
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
        {!bare && <CartDrawer />}
      </div>
    </div>
  );
}
