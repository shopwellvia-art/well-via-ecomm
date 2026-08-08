import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  ArrowLeft,
  ShoppingBag,
  Undo2,
  Star,
  Coins,
  Gift,
  Heart,
  ShoppingCart,
  Mail,
  ShieldCheck,
  KeyRound,
  Loader2,
  Info,
  BarChart3,
  Monitor,
} from 'lucide-react';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Button, buttonVariants } from '@/components/ui/Button.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { toast } from '@/components/ui/Toaster.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn } from '@/lib/utils.js';
import { fadeUp, staggerContainer } from '@/lib/motion.js';
import {
  useCustomer,
  useCustomerActivity,
  useUpdateCustomer,
  useTriggerCustomerPasswordReset,
} from '@/features/customers/hooks.js';
import { useHasPermission } from '@/features/auth/store.js';

const PAGE_SIZE = 25;

/** Tab definitions. `kind` null = the merged feed. */
const TABS = [
  { key: 'all', label: 'All activity', kind: null, icon: BarChart3 },
  { key: 'order', label: 'Orders', kind: 'order', icon: ShoppingBag },
  { key: 'return', label: 'Returns', kind: 'return', icon: Undo2 },
  { key: 'review', label: 'Reviews', kind: 'review', icon: Star },
  { key: 'points', label: 'Loyalty', kind: 'points', icon: Coins },
  { key: 'referral_made', label: 'Referrals', kind: 'referral_made', icon: Gift },
  { key: 'wishlist', label: 'Wishlist', kind: 'wishlist', icon: Heart },
  { key: 'cart', label: 'Cart', kind: 'cart', icon: ShoppingCart },
  { key: 'contact', label: 'Messages', kind: 'contact', icon: Mail },
  { key: 'admin_action', label: 'Admin actions', kind: 'admin_action', icon: ShieldCheck },
];

const KIND_ICON = {
  order: ShoppingBag,
  return: Undo2,
  review: Star,
  points: Coins,
  referral_made: Gift,
  referral_received: Gift,
  wishlist: Heart,
  cart: ShoppingCart,
  contact: Mail,
  admin_action: ShieldCheck,
};

function formatDate(value) {
  if (!value) return '—';
  return new Date(value).toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

function formatDateTime(value) {
  if (!value) return '—';
  return new Date(value).toLocaleString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function formatMoney(value) {
  if (value == null) return '—';
  return `₹${Number(value).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
}

function Stat({ label, value, hint }) {
  return (
    <div className="rounded-lg border border-line-subtle bg-bg-elevated p-4">
      <p className="text-xs font-medium uppercase tracking-wider text-ink-tertiary">
        {label}
      </p>
      <p className="nums mt-1 text-lg font-semibold text-ink-primary">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-ink-tertiary">{hint}</p>}
    </div>
  );
}

function ActivityRow({ event }) {
  const Icon = KIND_ICON[event.kind] || BarChart3;
  return (
    <motion.li
      variants={fadeUp}
      className="flex gap-3 border-t border-line-subtle px-5 py-3.5 first:border-t-0"
    >
      <div className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-full bg-fill text-ink-secondary">
        <Icon className="size-4" aria-hidden="true" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-sm text-ink-primary">{event.title}</p>
        {event.detail && (
          <p className="mt-0.5 line-clamp-2 text-xs text-ink-secondary">
            {event.detail}
          </p>
        )}
        {/* contact_messages has no user_id — it is matched on the address the
            sender typed, so the UI has to say so rather than implying a link. */}
        {event.meta?.email_matched && (
          <p className="mt-0.5 text-[11px] text-ink-tertiary">
            Matched by email address, not a linked account
          </p>
        )}
      </div>
      <time className="shrink-0 text-xs text-ink-tertiary">
        {formatDateTime(event.occurred_at)}
      </time>
    </motion.li>
  );
}

export default function AdminCustomerDetailPage() {
  const { id } = useParams();
  const customerId = Number(id);
  const [tab, setTab] = useState('all');
  const [page, setPage] = useState(1);

  const canManage = useHasPermission('customers.manage');
  const canSeeAnalytics = useHasPermission('analytics.customers.view');

  const { data: customer, isLoading, isError } = useCustomer(customerId);
  const activeTab = TABS.find((t) => t.key === tab) || TABS[0];
  const { data: activity, isFetching: activityLoading } = useCustomerActivity(
    customerId,
    {
      kinds: activeTab.kind ? [activeTab.kind] : undefined,
      page,
      page_size: PAGE_SIZE,
    },
  );

  const update = useUpdateCustomer();
  const reset = useTriggerCustomerPasswordReset();

  function handleToggleActive() {
    if (!customer) return;
    update.mutate(
      { customerId, data: { is_active: !customer.is_active } },
      {
        onSuccess: () =>
          toast.success(
            customer.is_active
              ? `${customer.email} has been disabled and signed out everywhere.`
              : `${customer.email} can log in again.`,
          ),
        onError: (err) =>
          toast.error(
            err.response?.data?.error?.message || 'Could not update the account.',
          ),
      },
    );
  }

  function handlePasswordReset() {
    reset.mutate(customerId, {
      onSuccess: (res) =>
        toast.success(res?.detail || 'Password reset code sent to the customer.'),
      onError: (err) =>
        toast.error(
          err.response?.data?.error?.message ||
            'Could not send the password reset email.',
        ),
    });
  }

  if (isError) {
    return (
      <AdminPage title="Customer">
        <EmptyState
          icon={ShoppingBag}
          iconTone="danger"
          title="Customer not found"
          description="This account doesn't exist, or it's a staff account — those live under Team."
          action={
            <Link to="/admin/customers" className={buttonVariants({ size: 'sm' })}>
              Back to customers
            </Link>
          }
        />
      </AdminPage>
    );
  }

  if (isLoading || !customer) {
    return (
      <AdminPage title="Customer">
        <Skeleton variant="text" lines={1} className="w-64" />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-20 rounded-lg" />
          ))}
        </div>
      </AdminPage>
    );
  }

  const snap = customer.snapshot;
  const counts = customer.activity_counts || {};
  const total = activity?.total || 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <AdminPage
      title={customer.full_name || customer.email}
      description={
        <>
          {customer.email}
          {customer.phone ? ` · ${customer.phone}` : ''} · joined{' '}
          {formatDate(customer.created_at)}
        </>
      }
      action={
        <div className="flex items-center gap-2">
          <Link
            to="/admin/customers"
            className={buttonVariants({ variant: 'ghost', size: 'sm' })}
          >
            <ArrowLeft className="size-4" /> All customers
          </Link>
          {canManage && (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={handlePasswordReset}
                disabled={reset.isPending}
              >
                {reset.isPending ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <KeyRound className="size-4" />
                )}
                Reset password
              </Button>
              <Button
                variant={customer.is_active ? 'destructive' : 'primary'}
                size="sm"
                loading={update.isPending}
                onClick={handleToggleActive}
              >
                {customer.is_active ? 'Disable account' : 'Re-enable account'}
              </Button>
            </>
          )}
        </div>
      }
    >
      {/* Status strip */}
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={customer.is_active ? 'success' : 'neutral'} dot>
          {customer.is_active ? 'Active' : 'Disabled'}
        </Badge>
        {customer.account_status !== 'active' && (
          <Badge tone="warning">{customer.account_status}</Badge>
        )}
        {snap?.rfm_segment && (
          <Badge tone="info">{snap.rfm_segment.replace(/_/g, ' ')}</Badge>
        )}
        {snap?.churn_risk_band && (
          <Badge tone={snap.churn_risk_band === 'high' ? 'danger' : 'neutral'}>
            churn risk: {snap.churn_risk_band}
          </Badge>
        )}
        {customer.totp_enabled && <Badge tone="accent">2FA on</Badge>}
        <span className="flex items-center gap-1 text-xs text-ink-tertiary">
          <Monitor className="size-3.5" aria-hidden="true" />
          <span className="nums">{customer.active_sessions}</span> active session
          {customer.active_sessions === 1 ? '' : 's'}
        </span>
      </div>

      {/* Headline numbers */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Orders" value={snap?.orders_count ?? 0} />
        <Stat
          label="Last order"
          value={formatDate(snap?.last_order_at)}
          hint={
            snap?.recency_days != null ? `${snap.recency_days} days ago` : undefined
          }
        />
        {customer.money_visible ? (
          <>
            <Stat label="Lifetime spend" value={formatMoney(snap?.gross_ltv)} />
            <Stat label="Average order" value={formatMoney(snap?.aov)} />
          </>
        ) : (
          <>
            <Stat label="Loyalty points" value={customer.points_balance} />
            <Stat
              label="Lifetime points"
              value={customer.lifetime_points}
              hint={customer.vip_tier || undefined}
            />
          </>
        )}
      </div>

      {customer.snapshot_date && (
        <p className="flex items-center gap-1.5 text-xs text-ink-tertiary">
          <Info className="size-3.5" aria-hidden="true" />
          {customer.money_visible ? 'Order and spend figures' : 'Order figures'} are
          as of{' '}
          <span className="font-medium text-ink-secondary">
            {formatDate(customer.snapshot_date)}
          </span>
          . The activity feed below is live.
          {canSeeAnalytics && (
            <Link
              to="/admin/analytics/customers"
              className="ml-1 text-accent underline-offset-2 hover:underline"
            >
              Customer analytics →
            </Link>
          )}
        </p>
      )}

      {/* Activity */}
      <div className="overflow-hidden rounded-xl border border-line-subtle bg-bg-elevated shadow-md">
        <div className="flex flex-wrap gap-1 border-b border-line-subtle bg-bg-sunken/60 px-3 py-2">
          {TABS.map((t) => {
            const count = t.kind ? counts[t.kind] : null;
            if (t.kind && !count) return null;
            return (
              <button
                key={t.key}
                type="button"
                onClick={() => {
                  setTab(t.key);
                  setPage(1);
                }}
                className={cn(
                  'flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors focus-visible:focus-ring',
                  tab === t.key
                    ? 'bg-accent/12 text-accent'
                    : 'text-ink-secondary hover:bg-fill hover:text-ink-primary',
                )}
              >
                <t.icon className="size-3.5" aria-hidden="true" />
                {t.label}
                {count != null && <span className="nums text-xs">({count})</span>}
              </button>
            );
          })}
        </div>

        {activityLoading && !activity ? (
          <div className="divide-y divide-line-subtle">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="flex gap-3 px-5 py-3.5">
                <Skeleton variant="circle" className="size-8 shrink-0" />
                <Skeleton variant="text" lines={1} className="flex-1" />
              </div>
            ))}
          </div>
        ) : (activity?.items || []).length === 0 ? (
          <div className="px-5 py-10">
            <EmptyState
              icon={activeTab.icon}
              title="Nothing here yet"
              description={
                activeTab.kind
                  ? `This customer has no ${activeTab.label.toLowerCase()} recorded.`
                  : 'This customer has no recorded activity yet.'
              }
            />
          </div>
        ) : (
          <motion.ul
            variants={staggerContainer(0.02)}
            initial="hidden"
            animate="show"
          >
            {(activity?.items || []).map((e) => (
              <ActivityRow key={`${e.kind}-${e.ref_id}`} event={e} />
            ))}
          </motion.ul>
        )}

        {totalPages > 1 && (
          <div className="flex items-center justify-between gap-2 border-t border-line-subtle px-5 py-3">
            <p className="text-xs text-ink-tertiary">
              <span className="nums">{total}</span> event{total === 1 ? '' : 's'}
            </p>
            <div className="flex items-center gap-2">
              <span className="text-xs text-ink-tertiary">
                Page <span className="nums">{page}</span> of{' '}
                <span className="nums">{totalPages}</span>
              </span>
              <Button
                variant="ghost"
                size="sm"
                disabled={page === 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                Previous
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={page === totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              >
                Next
              </Button>
            </div>
          </div>
        )}
      </div>
    </AdminPage>
  );
}
