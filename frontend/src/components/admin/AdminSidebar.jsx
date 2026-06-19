import { useState } from 'react';
import { Link, NavLink, useLocation } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import {
  LayoutDashboard,
  Package,
  Tags,
  GalleryHorizontal,
  Store,
  LogOut,
  Sparkles,
  X,
  TicketPercent,
  ShieldCheck,
  Users,
  Percent,
  Star,
  Coins,
  History,
  Settings as SettingsIcon,
  ClipboardList,
  Undo2,
  CreditCard,
  LayoutPanelTop,
  ChevronDown,
  LayoutTemplate,
  FileText,
  BarChart3,
  TrendingUp,
  PiggyBank,
  Activity,
  AlertOctagon,
} from 'lucide-react';
import { cn } from '@/lib/utils.js';
import { useAuthStore } from '@/features/auth/store.js';
import { authApi } from '@/features/auth/api.js';
import { ThemeToggle } from '@/components/ui/ThemeToggle.jsx';

// `permission: null` -> always visible to staff. Otherwise the item is hidden
// when the user lacks the permission (admins bypass via hasPermission).
const NAV = [
  { to: '/admin', label: 'Dashboard', icon: LayoutDashboard, end: true, permission: null },
  { to: '/admin/orders', label: 'Orders', icon: ClipboardList, end: false, permission: 'orders.view_all' },
  { to: '/admin/returns', label: 'Returns', icon: Undo2, end: false, permission: 'returns.view_all' },
  { to: '/admin/products', label: 'Products', icon: Package, end: false, permission: 'products.view' },
  { to: '/admin/categories', label: 'Categories', icon: Tags, end: false, permission: 'categories.view' },
  { to: '/admin/coupons', label: 'Coupons', icon: TicketPercent, end: false, permission: 'coupons.view' },
  { to: '/admin/taxes', label: 'Taxes', icon: Percent, end: false, permission: 'taxes.view' },
  { to: '/admin/reviews', label: 'Reviews', icon: Star, end: false, permission: 'reviews.view' },
  { to: '/admin/loyalty', label: 'Loyalty', icon: Coins, end: false, permission: 'loyalty.view' },
  { to: '/admin/users', label: 'Users', icon: Users, end: false, permission: 'users.view' },
  { to: '/admin/roles', label: 'Roles', icon: ShieldCheck, end: false, permission: 'roles.view' },
  { to: '/admin/audit', label: 'Audit log', icon: History, end: false, permission: 'audit.view' },
  { to: '/admin/observability', label: 'Observability', icon: Activity, end: false, permission: 'observability.view' },
  { to: '/admin/payment-methods', label: 'Payment Methods', icon: CreditCard, end: false, permission: 'payments.manage' },
  { to: '/admin/settings', label: 'Settings', icon: SettingsIcon, end: false, permission: 'settings.manage' },
];

// Collapsible group definitions. Children use the same permission model.
const FRONTEND_GROUP = {
  label: 'Frontend',
  icon: LayoutPanelTop,
  children: [
    { to: '/admin/hero', label: 'Hero slides', icon: GalleryHorizontal, end: false, permission: 'hero_slides.manage' },
    { to: '/admin/footer', label: 'Footer', icon: LayoutTemplate, end: false, permission: 'frontend.manage' },
    { to: '/admin/pages', label: 'Company pages', icon: FileText, end: false, permission: 'frontend.manage' },
  ],
};

const ANALYTICS_GROUP = {
  label: 'Analytics',
  icon: BarChart3,
  children: [
    { to: '/admin/analytics/sales', label: 'Sales & Revenue', icon: TrendingUp, end: false, permission: 'dashboard.view' },
    { to: '/admin/analytics/profit', label: 'Profitability', icon: PiggyBank, end: false, permission: 'dashboard.view' },
  ],
};

function useVisibleNav() {
  const user = useAuthStore((s) => s.user);
  const isAdmin = !!user?.is_admin;
  const perms = user?.permissions || [];

  function isVisible(item) {
    // `superadminOnly` items show ONLY for the is_admin tier — never for scoped
    // staff, even those who happen to hold every granular permission.
    if (item.superadminOnly) return isAdmin;
    if (item.permission == null) return true;
    if (isAdmin) return true;
    return perms.includes(item.permission);
  }

  const flatItems = NAV.filter(isVisible);
  const frontendChildren = FRONTEND_GROUP.children.filter(isVisible);
  const analyticsChildren = ANALYTICS_GROUP.children.filter(isVisible);

  return { flatItems, frontendChildren, analyticsChildren, isSuperadmin: isAdmin };
}

function NavItem({ item, onNavigate }) {
  return (
    <NavLink
      to={item.to}
      end={item.end}
      onClick={onNavigate}
      className={({ isActive }) =>
        cn(
          'flex items-center gap-3 rounded-sm px-3 py-2.5 text-sm transition-colors focus-visible:focus-ring',
          isActive
            ? 'bg-accent/12 text-accent'
            : 'text-ink-secondary hover:bg-fill hover:text-ink-primary',
        )
      }
    >
      <item.icon className="size-4" aria-hidden="true" />
      {item.label}
    </NavLink>
  );
}

function CollapsibleGroup({ group, children, onNavigate }) {
  const location = useLocation();

  // Auto-expand when any child route is active.
  const isAnyChildActive = children.some((c) => location.pathname.startsWith(c.to));
  const [open, setOpen] = useState(isAnyChildActive);

  if (children.length === 0) return null;

  const GroupIcon = group.icon;

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          'flex w-full items-center gap-3 rounded-sm px-3 py-2.5 text-sm transition-colors focus-visible:focus-ring',
          isAnyChildActive
            ? 'text-accent'
            : 'text-ink-secondary hover:bg-fill hover:text-ink-primary',
        )}
        aria-expanded={open}
      >
        <GroupIcon className="size-4 shrink-0" aria-hidden="true" />
        <span className="flex-1 text-left">{group.label}</span>
        <ChevronDown
          className={cn(
            'size-3.5 text-ink-tertiary transition-transform duration-200',
            open && 'rotate-180',
          )}
          aria-hidden="true"
        />
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18, ease: 'easeInOut' }}
            className="overflow-hidden"
          >
            <div className="ml-3 mt-0.5 flex flex-col gap-0.5 border-l border-line-subtle pl-3">
              {children.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  onClick={onNavigate}
                  className={({ isActive }) =>
                    cn(
                      'flex items-center gap-3 rounded-sm px-3 py-2 text-sm transition-colors focus-visible:focus-ring',
                      isActive
                        ? 'bg-accent/12 text-accent'
                        : 'text-ink-secondary hover:bg-fill hover:text-ink-primary',
                    )
                  }
                >
                  <item.icon className="size-4" aria-hidden="true" />
                  {item.label}
                </NavLink>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// Keep the old name as a thin wrapper so any external references still compile.
function FrontendGroup({ children, onNavigate }) {
  return <CollapsibleGroup group={FRONTEND_GROUP} children={children} onNavigate={onNavigate} />;
}

function SidebarContent({ onNavigate }) {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const { flatItems, frontendChildren, analyticsChildren, isSuperadmin } = useVisibleNav();

  // Split flat items: place Frontend group where the Hero item used to be
  // (after Categories, before Coupons — index 4 in the original NAV order).
  // We inject the group between Categories and Coupons.
  const beforeGroup = flatItems.filter((item) =>
    ['/admin', '/admin/orders', '/admin/returns', '/admin/products', '/admin/categories'].includes(item.to),
  );
  const afterGroup = flatItems.filter(
    (item) =>
      !['/admin', '/admin/orders', '/admin/returns', '/admin/products', '/admin/categories'].includes(item.to),
  );

  return (
    <div className="flex h-full flex-col gap-1 p-4">
      <Link
        to="/admin"
        onClick={onNavigate}
        className="mb-4 flex items-center gap-2 rounded-sm px-2 py-1 text-ink-primary focus-visible:focus-ring"
      >
        <span className="grid size-8 place-items-center rounded-sm bg-accent text-ink-inverse">
          <Sparkles className="size-4" aria-hidden="true" />
        </span>
        <span className="text-h3">Lumen</span>
        <span className="rounded-full bg-fill-strong px-2 py-0.5 text-xs text-ink-secondary">
          Admin
        </span>
      </Link>

      {/* IMPORTANT: min-h-0 flex-1 overflow-y-auto preserves sidebar scroll */}
      <nav className="-mr-2 flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto pr-2">
        {beforeGroup.map((item) => (
          <NavItem key={item.to} item={item} onNavigate={onNavigate} />
        ))}

        {/* Collapsible Frontend group — sits where Hero slides used to be */}
        <FrontendGroup children={frontendChildren} onNavigate={onNavigate} />

        {afterGroup.map((item) => (
          <NavItem key={item.to} item={item} onNavigate={onNavigate} />
        ))}

        {/* Collapsible Analytics group */}
        <CollapsibleGroup group={ANALYTICS_GROUP} children={analyticsChildren} onNavigate={onNavigate} />

        {/* Danger zone — superadmin (is_admin) only, visually set apart in red. */}
        {isSuperadmin && (
          <div className="mt-2 border-t border-line-subtle pt-2">
            <p className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-widest text-danger/70">
              Danger zone
            </p>
            <NavLink
              to="/admin/danger"
              onClick={onNavigate}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-3 rounded-sm px-3 py-2.5 text-sm transition-colors focus-visible:focus-ring',
                  isActive
                    ? 'bg-danger/12 text-danger'
                    : 'text-danger/80 hover:bg-danger/10 hover:text-danger',
                )
              }
            >
              <AlertOctagon className="size-4" aria-hidden="true" />
              Truncate database
            </NavLink>
          </div>
        )}
      </nav>

      <div className="mt-auto flex shrink-0 flex-col gap-1 border-t border-line-subtle pt-3">
        <Link
          to="/"
          onClick={onNavigate}
          className="flex items-center gap-3 rounded-sm px-3 py-2.5 text-sm text-ink-secondary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
        >
          <Store className="size-4" aria-hidden="true" />
          Back to store
        </Link>

        <div className="flex items-center gap-2 px-3 py-2">
          <div className="grid size-8 shrink-0 place-items-center rounded-full bg-accent/12 text-xs font-semibold text-accent">
            {(user?.email || '?').charAt(0).toUpperCase()}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs text-ink-secondary">{user?.email}</p>
            {(user?.roles?.length || user?.is_admin) ? (
              <p className="truncate text-[10px] text-ink-tertiary">
                {user.is_admin
                  ? 'Administrator'
                  : user.roles.map((r) => r.name).join(', ')}
              </p>
            ) : null}
          </div>
          <ThemeToggle className="size-8" />
        </div>

        <button
          type="button"
          onClick={() => {
            // Tell the server to revoke this refresh-token family. Errors are
            // swallowed — local state must always clear.
            const rt = useAuthStore.getState().refreshToken;
            if (rt) authApi.logout(rt).catch(() => {});
            logout();
          }}
          className="flex items-center gap-3 rounded-sm px-3 py-2.5 text-sm text-ink-secondary transition-colors hover:bg-danger/10 hover:text-danger focus-visible:focus-ring"
        >
          <LogOut className="size-4" aria-hidden="true" />
          Sign out
        </button>
      </div>
    </div>
  );
}

export default function AdminSidebar({ open, onClose }) {
  return (
    <>
      {/* Desktop — persistent */}
      <aside className="fixed inset-y-0 left-0 hidden w-64 border-r border-line-subtle bg-bg-elevated lg:block">
        <SidebarContent />
      </aside>

      {/* Mobile — overlay drawer */}
      <AnimatePresence>
        {open && (
          <>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              onClick={onClose}
              className="fixed inset-0 z-40 bg-black/50 lg:hidden"
            />
            <motion.aside
              initial={{ x: '-100%' }}
              animate={{ x: 0 }}
              exit={{ x: '-100%' }}
              transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
              className="fixed inset-y-0 left-0 z-50 w-64 border-r border-line-subtle bg-bg-elevated lg:hidden"
            >
              <button
                type="button"
                onClick={onClose}
                aria-label="Close menu"
                className="absolute right-3 top-3 grid size-9 place-items-center rounded-sm text-ink-secondary hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
              >
                <X className="size-5" />
              </button>
              <SidebarContent onNavigate={onClose} />
            </motion.aside>
          </>
        )}
      </AnimatePresence>
    </>
  );
}
