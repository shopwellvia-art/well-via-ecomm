import { Navigate } from 'react-router-dom';
import { useAuthStore } from '@/features/auth/store.js';

/**
 * Guard for superadmin-only routes. "Superadmin" here is the legacy full-access
 * tier (is_admin=True) — narrower than the admin shell, which RequireAdmin
 * admits any staff (is_admin OR any role) into. Scoped RBAC role-holders are
 * bounced back to the dashboard.
 *
 * Always nested INSIDE <RequireAdmin>, so token/staff checks already ran here.
 */
export default function RequireSuperadmin({ children }) {
  const user = useAuthStore((s) => s.user);

  if (!user) return null; // /auth/me bootstrap in flight
  if (!user.is_admin) return <Navigate to="/admin" replace />;
  return children;
}
