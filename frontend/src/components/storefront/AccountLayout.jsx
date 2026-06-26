import { NavLink, useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/features/auth/store';
import { cn } from '@/lib/utils';

/**
 * AccountLayout — sidebar + content shell shared by every account page.
 *
 * Props:
 *   children  — page content
 *   active    — optional key of the current nav item; used to force an active
 *               highlight when the URL alone is ambiguous (e.g. nested child routes).
 *               Keys: 'orders' | 'wishlist' | 'rewards' | 'security' | 'addresses'
 *
 * Wiring:
 *   - User name / email / initial from useAuthStore().user (gracefully handles null)
 *   - Nav links to REAL routes: /orders, /wishlist, /rewards, /account/security, /account/addresses
 *   - Logout via useAuthStore().logout() then navigate to /
 */

const NAV = [
  { key: 'orders',    to: '/orders',            label: 'My Orders' },
  { key: 'wishlist',  to: '/wishlist',           label: 'Wishlist' },
  { key: 'rewards',   to: '/rewards',            label: 'Rewards' },
  { key: 'security',  to: '/account/security',   label: 'Security & 2FA' },
  { key: 'addresses', to: '/account/addresses',  label: 'Addresses' },
];

function UserAvatar({ name }) {
  const initial = name ? name.trim().charAt(0).toUpperCase() : '?';
  return (
    <div
      className="w-12 h-12 rounded-full shrink-0 flex items-center justify-center font-wserif text-[22px] text-white select-none"
      style={{
        background: 'linear-gradient(135deg, #183A2E 0%, #B49A63 100%)',
      }}
      aria-hidden="true"
    >
      {initial}
    </div>
  );
}

export default function AccountLayout({ children, active }) {
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);

  const handleLogout = () => {
    logout();
    navigate('/');
  };

  const displayName = user?.name ?? 'My Account';
  const displayEmail = user?.email ?? '';

  return (
    <main className="px-5 sm:px-10 lg:px-14 py-6 lg:py-11">
      <div className="max-w-[1120px] mx-auto grid md:grid-cols-[260px_1fr] gap-5 lg:gap-9 items-start">
        {/* Sidebar */}
        <aside className="bg-wcard border border-wline rounded-[20px] p-[22px] md:sticky md:top-[88px]">
          {/* User info */}
          <div className="flex items-center gap-3.5 pb-[18px] border-b border-wline mb-4">
            <UserAvatar name={displayName} />
            <div className="min-w-0">
              <div className="font-wserif text-[19px] leading-tight text-wink truncate">
                {displayName}
              </div>
              {displayEmail && (
                <div className="text-[11.5px] text-wmuted truncate">
                  {displayEmail}
                </div>
              )}
            </div>
          </div>

          {/* Nav links */}
          <div className="flex flex-col gap-[3px]">
            {NAV.map((n) => (
              <NavLink
                key={n.key}
                to={n.to}
                end={n.to === '/orders'} // /orders is exact-match only (avoid matching /orders/:id)
                className={({ isActive }) =>
                  cn(
                    'px-3.5 py-[11px] rounded-xl text-[13.5px] cursor-pointer transition-colors no-underline',
                    isActive || active === n.key
                      ? 'bg-wgreen text-white'
                      : 'text-wink hover:bg-wline/40'
                  )
                }
              >
                {n.label}
              </NavLink>
            ))}

            <div className="h-px bg-wline my-2" />

            {/* Logout */}
            <button
              onClick={handleLogout}
              className="text-left px-3.5 py-[11px] rounded-xl text-[13.5px] text-wmuted cursor-pointer bg-transparent border-0 hover:bg-wline/40 transition-colors"
            >
              Sign Out
            </button>
          </div>
        </aside>

        {/* Main content */}
        <section className="min-h-[60vh]">{children}</section>
      </div>
    </main>
  );
}
