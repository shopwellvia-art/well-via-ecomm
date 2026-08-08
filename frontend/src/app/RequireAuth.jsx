import { Navigate, useLocation } from 'react-router-dom';
import { useAuthStore } from '@/features/auth/store.js';

/**
 * Guard for customer-only storefront pages (orders, rewards, account/*).
 * Anyone with a session token is admitted — no staff/permission checks here.
 *
 * Unauthenticated visitors are bounced to /login with the attempted URL in
 * the `next` query param; LoginPage validates it is an internal path and
 * navigates there after a successful sign-in.
 *
 * Note /checkout is deliberately NOT wrapped — the checkout page renders its
 * own inline LoginPanel flow instead of leaving the page.
 */
export default function RequireAuth({ children }) {
  const location = useLocation();
  const token = useAuthStore((s) => s.accessToken);

  if (!token) {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  return children;
}
