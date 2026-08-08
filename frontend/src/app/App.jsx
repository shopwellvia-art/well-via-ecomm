import { lazy, Suspense } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';

import Layout from '@/components/layout/Layout.jsx';
import AdminLayout from '@/components/admin/AdminLayout.jsx';
import RequireAdmin from './RequireAdmin.jsx';
import RequireAuth from './RequireAuth.jsx';
import RequirePermission from './RequirePermission.jsx';
import RequireSuperadmin from './RequireSuperadmin.jsx';
import ScrollToTop from './ScrollToTop.jsx';
import AuthBootstrap from './AuthBootstrap.jsx';
import SiteMeta from '@/components/storefront/SiteMeta.jsx';
import ConsentGate from '@/components/consent/ConsentGate.jsx';
import { PageFallback } from '@/components/feedback/PageFallback.jsx';
import { useTracking } from '@/features/tracking/index.js';

// Route-based code splitting — each page is its own chunk.
const HomePage = lazy(() => import('@/pages/HomePage.jsx'));
const ProductListPage = lazy(() => import('@/pages/ProductListPage.jsx'));
const CategoriesPage = lazy(() => import('@/pages/CategoriesPage.jsx'));
const ProductDetailPage = lazy(() => import('@/pages/ProductDetailPage.jsx'));
const CartPage = lazy(() => import('@/pages/CartPage.jsx'));
const CheckoutPage = lazy(() => import('@/pages/CheckoutPage.jsx'));
const PaymentMockPage = lazy(() => import('@/pages/PaymentMockPage.jsx'));
const PaymentReturnPage = lazy(() => import('@/pages/PaymentReturnPage.jsx'));
const OrdersPage = lazy(() => import('@/pages/OrdersPage.jsx'));
const OrderDetailPage = lazy(() => import('@/pages/OrderDetailPage.jsx'));
const WishlistPage = lazy(() => import('@/pages/WishlistPage.jsx'));
const RewardsPage = lazy(() => import('@/pages/RewardsPage.jsx'));
const AccountSecurityPage = lazy(() => import('@/pages/AccountSecurityPage.jsx'));
const AddressesPage = lazy(() => import('@/pages/AddressesPage.jsx'));
const LoginPage = lazy(() => import('@/pages/LoginPage.jsx'));
const ForgotPasswordPage = lazy(() => import('@/pages/ForgotPasswordPage.jsx'));
const AuthCallbackPage = lazy(() => import('@/pages/AuthCallbackPage.jsx'));
const NotFoundPage = lazy(() => import('@/pages/NotFoundPage.jsx'));


// Company / content pages (footer "About" links) — admin-editable.
const AboutPage = lazy(() => import('@/pages/AboutPage.jsx'));
const ContactPage = lazy(() => import('@/pages/ContactPage.jsx'));
const CareersPage = lazy(() => import('@/pages/CareersPage.jsx'));
const StoriesPage = lazy(() => import('@/pages/StoriesPage.jsx'));
const PressPage = lazy(() => import('@/pages/PressPage.jsx'));
const CorporatePage = lazy(() => import('@/pages/CorporatePage.jsx'));
const PrivacyPage = lazy(() => import('@/pages/PrivacyPage.jsx'));
const TermsPage = lazy(() => import('@/pages/TermsPage.jsx'));
const RefundPage = lazy(() => import('@/pages/RefundPage.jsx'));
const ShippingPage = lazy(() => import('@/pages/ShippingPage.jsx'));

const AdminDashboardPage = lazy(() => import('@/pages/admin/AdminDashboardPage.jsx'));
const AdminProductsPage = lazy(() => import('@/pages/admin/AdminProductsPage.jsx'));
const AdminProductFormPage = lazy(() => import('@/pages/admin/AdminProductFormPage.jsx'));
const AdminCategoriesPage = lazy(() => import('@/pages/admin/AdminCategoriesPage.jsx'));
const AdminHeroSlidesPage = lazy(() => import('@/pages/admin/AdminHeroSlidesPage.jsx'));
const AdminCouponsPage = lazy(() => import('@/pages/admin/AdminCouponsPage.jsx'));
const AdminRolesPage = lazy(() => import('@/pages/admin/AdminRolesPage.jsx'));
const AdminTeamPage = lazy(() => import('@/pages/admin/AdminTeamPage.jsx'));
const AdminCustomersPage = lazy(() => import('@/pages/admin/AdminCustomersPage.jsx'));
const AdminCustomerDetailPage = lazy(() =>
  import('@/pages/admin/AdminCustomerDetailPage.jsx'),
);
const AdminTaxesPage = lazy(() => import('@/pages/admin/AdminTaxesPage.jsx'));
const AdminReviewsPage = lazy(() => import('@/pages/admin/AdminReviewsPage.jsx'));
const AdminLoyaltyPage = lazy(() => import('@/pages/admin/AdminLoyaltyPage.jsx'));
const AdminAuditPage = lazy(() => import('@/pages/admin/AdminAuditPage.jsx'));
const AdminSettingsPage = lazy(() => import('@/pages/admin/AdminSettingsPage.jsx'));
const AdminPaymentMethodsPage = lazy(() => import('@/pages/admin/AdminPaymentMethodsPage.jsx'));
const AdminFooterPage = lazy(() => import('@/pages/admin/AdminFooterPage.jsx'));
const AdminStorefrontPage = lazy(() => import('@/pages/admin/AdminStorefrontPage.jsx'));
const AdminPagesPage = lazy(() => import('@/pages/admin/AdminPagesPage.jsx'));
const AdminOrdersPage = lazy(() => import('@/pages/admin/AdminOrdersPage.jsx'));
const AdminOrderDetailPage = lazy(() => import('@/pages/admin/AdminOrderDetailPage.jsx'));
const AdminReturnsPage = lazy(() => import('@/pages/admin/AdminReturnsPage.jsx'));
const AdminSalesAnalyticsPage = lazy(() => import('@/pages/admin/AdminSalesAnalyticsPage.jsx'));
const AdminProfitAnalyticsPage = lazy(() => import('@/pages/admin/AdminProfitAnalyticsPage.jsx'));
// Analytics v2. Two pages serve all 12 modules and 73 views — everything that
// varies between them comes from the generated registry contract.
const AdminAnalyticsPage = lazy(() => import('@/pages/admin/AdminAnalyticsPage.jsx'));
const AdminAnalyticsModulePage = lazy(() => import('@/pages/admin/AdminAnalyticsModulePage.jsx'));
const AdminAnalyticsSettingsPage = lazy(
  () => import('@/pages/admin/AdminAnalyticsSettingsPage.jsx'),
);
const AdminAnalyticsCostRulesPage = lazy(
  () => import('@/pages/admin/AdminAnalyticsCostRulesPage.jsx'),
);
const AdminObservabilityPage = lazy(() => import('@/pages/admin/AdminObservabilityPage.jsx'));
const AdminDangerZonePage = lazy(() => import('@/pages/admin/AdminDangerZonePage.jsx'));

export default function App() {
  // Mounted here — inside the router — so every navigation re-runs the gates
  // rather than trusting the decision made on the first page. The hook is
  // already gated internally on setting-enabled AND consent-granted AND
  // not-an-/admin-route; do not re-check any of that out here, and do not
  // relax it. Its return value is deliberately unused: nothing in the app
  // should branch on whether a customer is being tracked.
  useTracking();

  return (
    <>
      <ScrollToTop />
      <AuthBootstrap />
      <SiteMeta />
      {/* Consent dialog. Outside <Routes> so it survives navigation, inside the
          router so it can see the path (it hides itself on /admin/*). */}
      <ConsentGate />
      <Suspense fallback={<PageFallback />}>
        <Routes>
          {/* Storefront */}
          <Route element={<Layout />}>
            <Route index element={<HomePage />} />
            <Route path="products" element={<ProductListPage />} />
            <Route path="bestsellers" element={<ProductListPage mode="bestsellers" />} />
            <Route path="new-arrivals" element={<ProductListPage mode="new-arrivals" />} />
            <Route path="categories" element={<CategoriesPage />} />
            <Route path="products/:id" element={<ProductDetailPage />} />
            <Route path="cart" element={<CartPage />} />
            <Route path="wishlist" element={<WishlistPage />} />
            {/* Customer-only pages — /checkout stays unguarded (inline LoginPanel) */}
            <Route
              path="rewards"
              element={
                <RequireAuth>
                  <RewardsPage />
                </RequireAuth>
              }
            />
            <Route
              path="account/security"
              element={
                <RequireAuth>
                  <AccountSecurityPage />
                </RequireAuth>
              }
            />
            <Route
              path="account/addresses"
              element={
                <RequireAuth>
                  <AddressesPage />
                </RequireAuth>
              }
            />
            <Route path="checkout" element={<CheckoutPage />} />
            <Route
              path="orders"
              element={
                <RequireAuth>
                  <OrdersPage />
                </RequireAuth>
              }
            />
            <Route
              path="orders/:id"
              element={
                <RequireAuth>
                  <OrderDetailPage />
                </RequireAuth>
              }
            />
            <Route path="payments/mock/:txnId" element={<PaymentMockPage />} />
            <Route path="payments/return" element={<PaymentReturnPage />} />
            <Route path="login" element={<LoginPage />} />
            <Route path="forgot-password" element={<ForgotPasswordPage />} />
            <Route path="auth/callback" element={<AuthCallbackPage />} />
            {/* Company / content pages — match the footer link targets */}
            <Route path="about" element={<AboutPage />} />
            <Route path="contact" element={<ContactPage />} />
            <Route path="careers" element={<CareersPage />} />
            <Route path="stories" element={<StoriesPage />} />
            <Route path="press" element={<PressPage />} />
            <Route path="corporate" element={<CorporatePage />} />
            {/* Legal / customer-care policy pages (admin-editable) */}
            <Route path="privacy" element={<PrivacyPage />} />
            <Route path="terms" element={<TermsPage />} />
            <Route path="refund" element={<RefundPage />} />
            <Route path="shipping" element={<ShippingPage />} />
            {/* Aliases matching existing footer link targets */}
            <Route path="policy/returns" element={<RefundPage />} />
            <Route path="help/returns" element={<RefundPage />} />
            <Route path="help/shipping" element={<ShippingPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>

          {/* Admin — guarded; the server also enforces require_admin */}
          <Route
            element={
              <RequireAdmin>
                <AdminLayout />
              </RequireAdmin>
            }
          >
            <Route path="admin" element={<AdminDashboardPage />} />
            <Route path="admin/products" element={<AdminProductsPage />} />
            <Route path="admin/products/new" element={<AdminProductFormPage />} />
            <Route path="admin/products/:id/edit" element={<AdminProductFormPage />} />
            <Route path="admin/categories" element={<AdminCategoriesPage />} />
            <Route
              path="admin/orders"
              element={
                <RequirePermission permission="orders.view_all">
                  <AdminOrdersPage />
                </RequirePermission>
              }
            />
            <Route
              path="admin/orders/:id"
              element={
                <RequirePermission permission="orders.view_all">
                  <AdminOrderDetailPage />
                </RequirePermission>
              }
            />
            <Route
              path="admin/returns"
              element={
                <RequirePermission permission="returns.view_all">
                  <AdminReturnsPage />
                </RequirePermission>
              }
            />
            <Route path="admin/hero" element={<AdminHeroSlidesPage />} />
            <Route
              path="admin/coupons"
              element={
                <RequirePermission permission="coupons.view">
                  <AdminCouponsPage />
                </RequirePermission>
              }
            />
            <Route
              path="admin/roles"
              element={
                <RequirePermission permission="roles.view">
                  <AdminRolesPage />
                </RequirePermission>
              }
            />
            <Route
              path="admin/team"
              element={
                <RequirePermission permission="users.view">
                  <AdminTeamPage />
                </RequirePermission>
              }
            />
            {/* The staff directory used to live here and mixed shoppers in with
                it. Redirect rather than 404 so existing links and bookmarks
                still land somewhere sensible. */}
            <Route
              path="admin/users"
              element={<Navigate to="/admin/team" replace />}
            />
            <Route
              path="admin/customers"
              element={
                <RequirePermission permission="customers.view">
                  <AdminCustomersPage />
                </RequirePermission>
              }
            />
            <Route
              path="admin/customers/:id"
              element={
                <RequirePermission permission="customers.view">
                  <AdminCustomerDetailPage />
                </RequirePermission>
              }
            />
            <Route
              path="admin/taxes"
              element={
                <RequirePermission permission="taxes.view">
                  <AdminTaxesPage />
                </RequirePermission>
              }
            />
            <Route
              path="admin/reviews"
              element={
                <RequirePermission permission="reviews.view">
                  <AdminReviewsPage />
                </RequirePermission>
              }
            />
            <Route
              path="admin/loyalty"
              element={
                <RequirePermission permission="loyalty.view">
                  <AdminLoyaltyPage />
                </RequirePermission>
              }
            />
            <Route
              path="admin/audit"
              element={
                <RequirePermission permission="audit.view">
                  <AdminAuditPage />
                </RequirePermission>
              }
            />
            <Route
              path="admin/settings"
              element={
                <RequirePermission permission="settings.manage">
                  <AdminSettingsPage />
                </RequirePermission>
              }
            />
            <Route
              path="admin/payment-methods"
              element={
                <RequirePermission permission="payments.manage">
                  <AdminPaymentMethodsPage />
                </RequirePermission>
              }
            />
            <Route
              path="admin/footer"
              element={
                <RequirePermission permission="frontend.manage">
                  <AdminFooterPage />
                </RequirePermission>
              }
            />
            <Route
              path="admin/storefront"
              element={
                <RequirePermission permission="frontend.manage">
                  <AdminStorefrontPage />
                </RequirePermission>
              }
            />
            <Route
              path="admin/pages"
              element={
                <RequirePermission permission="frontend.manage">
                  <AdminPagesPage />
                </RequirePermission>
              }
            />
            <Route
              path="admin/analytics/sales"
              element={
                <RequirePermission permission="dashboard.view">
                  <AdminSalesAnalyticsPage />
                </RequirePermission>
              }
            />
            <Route
              path="admin/analytics/profit"
              element={
                <RequirePermission permission="dashboard.view">
                  <AdminProfitAnalyticsPage />
                </RequirePermission>
              }
            />
            {/* Analytics v2. Declared AFTER the two legacy routes above, but the
                order is not what protects them: React Router 6 ranks static
                segments above dynamic ones, so /analytics/sales keeps hitting
                AdminSalesAnalyticsPage regardless. Neither "sales" nor "profit"
                is a module slug, so there is no collision either way — the
                legacy pages stay reachable until shadow-mode reconciliation
                signs off on retiring them.

                Three routes serve all 73 views. The per-view permission is
                enforced server-side (403 with the view's own permission), so the
                route guard only checks the base grant — duplicating the 73
                per-view checks in the router would be a second source of truth
                that silently drifts from the registry. */}
            <Route
              path="admin/analytics"
              element={
                <RequirePermission permission="analytics.view">
                  <AdminAnalyticsPage />
                </RequirePermission>
              }
            />
            {/* Declared BEFORE the :moduleSlug route on purpose. React Router 6
                ranks static segments above dynamic ones, so "settings" would
                win the match either way — but this is the one route the module
                page would otherwise swallow whole, and leaving that to implicit
                ranking means a future reorder breaks it silently, with a module
                page rendering "settings" as a slug instead of a 404. */}
            <Route
              path="admin/analytics/settings"
              element={
                <RequirePermission permission="analytics.integrations.manage">
                  <AdminAnalyticsSettingsPage />
                </RequirePermission>
              }
            />
            {/* Also declared BEFORE :moduleSlug, and for the same reason as
                "settings" above. Gated on analytics.finance.view: a cost rate is
                the store's margin structure, so seeing it is the same privilege
                as seeing the margin itself. */}
            <Route
              path="admin/analytics/cost-rules"
              element={
                <RequirePermission permission="analytics.finance.view">
                  <AdminAnalyticsCostRulesPage />
                </RequirePermission>
              }
            />
            <Route
              path="admin/analytics/:moduleSlug"
              element={
                <RequirePermission permission="analytics.view">
                  <AdminAnalyticsModulePage />
                </RequirePermission>
              }
            />
            <Route
              path="admin/analytics/:moduleSlug/:viewSlug"
              element={
                <RequirePermission permission="analytics.view">
                  <AdminAnalyticsModulePage />
                </RequirePermission>
              }
            />
            <Route
              path="admin/observability"
              element={
                <RequirePermission permission="observability.view">
                  <AdminObservabilityPage />
                </RequirePermission>
              }
            />
            <Route
              path="admin/danger"
              element={
                <RequireSuperadmin>
                  <AdminDangerZonePage />
                </RequireSuperadmin>
              }
            />
          </Route>
        </Routes>
      </Suspense>
    </>
  );
}
